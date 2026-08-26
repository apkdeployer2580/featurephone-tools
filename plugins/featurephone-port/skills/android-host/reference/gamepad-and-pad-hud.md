# Physical gamepads: the two input routes, position badges, and the pad HUD

A physical controller is a third input class, and on Android it does not arrive by the same path as
touch — the analogue axes cannot arrive that way **at all**. This file specifies the owned path
(Java dispatch → JNI → the event-loop proxy), the mapping table that both routes share, the badge
system that tells the player which button does what without lying to two thirds of them, and the
HUD mode and modal-capture discipline that a second input entry point drags in behind it.

Tags: **(A)** generic · **(B)** worked example from the reference port, re-derive it ·
**(C)** do not copy. Conventions: `${CLAUDE_PLUGIN_ROOT}/shared/evidence-standard.md`.

Not here: touch controls and the hit-test chain ([input-and-hud.md](input-and-hud.md)), control
placement and the editor ([layout-editor.md](layout-editor.md)), and which mode the game is in —
that is [mode-resolution.md](mode-resolution.md), which this file consumes and does not duplicate.

## Contents

- [1. The blocking fact: axes cannot arrive through the windowing library](#1-the-blocking-fact-axes-cannot-arrive-through-the-windowing-library)
- [2. The owned path: dispatch override → JNI → proxy](#2-the-owned-path-dispatch-override--jni--proxy)
- [3. Source masks, and what must not be claimed](#3-source-masks-and-what-must-not-be-claimed)
- [4. Two routes, two audiences, one mapping table](#4-two-routes-two-audiences-one-mapping-table)
- [5. Axes: hat + stick summed, latest sample, triggers as synthetic keycodes](#5-axes-hat--stick-summed-latest-sample-triggers-as-synthetic-keycodes)
- [6. Trigger hysteresis is the joystick's dead zones again](#6-trigger-hysteresis-is-the-joysticks-dead-zones-again)
- [7. Mapping is data: the override file, defensive parsing, explicit NONE](#7-mapping-is-data-the-override-file-defensive-parsing-explicit-none)
- [8. Badges: Android reports POSITION, not the printed letter](#8-badges-android-reports-position-not-the-printed-letter)
- [9. REFUTED: "draw the set, light the member" needs a visible set](#9-refuted-draw-the-set-light-the-member-needs-a-visible-set)
- [10. Vendor-neutral names, and how they reconcile with "no letters"](#10-vendor-neutral-names-and-how-they-reconcile-with-no-letters)
- [11. A badge is a REVERSE LOOKUP of the action table](#11-a-badge-is-a-reverse-lookup-of-the-action-table)
- [12. One store for the overrides](#12-one-store-for-the-overrides)
- [13. Badge geometry: rim, floor, cap, clamp — and which diagonal is yours](#13-badge-geometry-rim-floor-cap-clamp--and-which-diagonal-is-yours)
- [14. One trailing pass, only where the control was drawn](#14-one-trailing-pass-only-where-the-control-was-drawn)
- [15. The legend must name unreachable actions](#15-the-legend-must-name-unreachable-actions)
- [16. The pad HUD is a MODE: drop the zone, not the drawing](#16-the-pad-hud-is-a-mode-drop-the-zone-not-the-drawing)
- [17. Three-way setting, no timeout, and the swallowed recovery touch](#17-three-way-setting-no-timeout-and-the-swallowed-recovery-touch)
- [18. "Pad seen" versus "pad driving"](#18-pad-seen-versus-pad-driving)
- [19. Do not remap per UI mode](#19-do-not-remap-per-ui-mode)
- [20. Modal capture: a second entry point reuses the first one's bookkeeping](#20-modal-capture-a-second-entry-point-reuses-the-first-ones-bookkeeping)
- [21. The picker table: diverted / passed through / swallowed whole](#21-the-picker-table-diverted--passed-through--swallowed-whole)
- [22. Press versus release, and the matched-pair invariant](#22-press-versus-release-and-the-matched-pair-invariant)
- [23. Auto-repeat: drop the system's, keep the worker's](#23-auto-repeat-drop-the-systems-keep-the-workers)
- [24. Release discipline: dispatch stops on focus loss](#24-release-discipline-dispatch-stops-on-focus-loss)
- [25. Building this without a pad](#25-building-this-without-a-pad)
- [26. Recorded limits](#26-recorded-limits)
- [27. Symptom table](#27-symptom-table)
- [28. Checklist](#28-checklist)

---

## 1. The blocking fact: axes cannot arrive through the windowing library

**(A) `HIGH / CONFIRMED`.** A game-activity host receives gamepad **buttons** through the normal
windowing path and can **never** receive joystick **axes** that way. Two independent barriers, each
fatal on its own:

1. The bundled native-app-glue keeps a **default motion-event filter that discards every
   `MotionEvent` whose source is not the touchscreen.** The C glue does expose a setter for that
   filter, but its argument is the `android_app*` the activity crate owns privately, and there is no
   safe way to obtain it — so the filter is not replaceable from a Rust host.
2. Even for a survivor, the windowing crate treats every motion *move* action as a **touch**. The
   stick's `-1.0 ..= 1.0` axis values arrive as touch **coordinates**.

The reference port established this by reading the four layers end to end — Java activity → the
bundled glue's input buffer → `android-activity` 0.6.1 → `winit` 0.30.13 — **before** writing a line
of the module, and the owned path (§2) then worked first try on a device.

**Check it yourself before designing anything (A):** log every event your handler receives with a pad
connected and the stick deflected. Buttons appear; axes do not. If you have "fixed" that by forcing
motion events through, the symptom is a character that teleports — barrier 2, not a mapping bug.
**Consequence:** analogue input must be captured in Java and pushed into native code. Settle this
first; it determines the whole architecture.

## 2. The owned path: dispatch override → JNI → proxy

**(A)** The path has one shape, and each hop exists for a reason:

```
Activity.dispatchKeyEvent / dispatchGenericMotionEvent   <- TOP of the dispatch chain
   |  filter by input source (§3); non-matching -> super, untouched
   v
static native padKey(int code, boolean down, boolean repeat)
static native padAxes(float x, float y, float lt, float rt)
   |  runs on the JAVA MAIN THREAD. Touches no app state. Allocates nothing interesting.
   v
event-loop proxy  ->  UserEvent  ->  UI thread, which owns layout, modal state, held sets
```

- **Override at the top of the dispatch chain**, before any view sees the event — a view-level
  listener competes with focus.
- **The natives may only relay.** They run on the Java main thread; mapping there is a data race
  against the UI thread that owns layout, popup state and the held-key sets. The relay is also where
  events arriving before the event loop exists are dropped harmlessly rather than panicking.

JNI mechanics that are not pad-specific — symbol-name resolution, `extern "system"`, the `jclass` a
`static native` hands you, and gating the module to the target OS so a host build still compiles —
belong to [platform-glue.md](platform-glue.md). This is the mandatory **fourth** job of the Java
shim: input the platform glue refuses to forward.

## 3. Source masks, and what must not be claimed

**(A)** Match input sources with the full-mask idiom, never a non-zero test:

```java
static boolean isSource(int source, int mask) { return (source & mask) == mask; }   // RIGHT
// (source & mask) != 0   <- WRONG: source classes share bits
```

Source classes share the **button class bit** in particular, so `!= 0` also matches hardware
keyboards and drags every keystroke into the pad table. The symptom — a keyboard that types quick-slot
digits — reads as a mapping bug and is a masking bug.

**(A) Non-matching events fall through to `super`, untouched.** That keeps touch, volume and the
system BACK gesture behaving exactly as before, and it is the only thing that makes a **pad's** BACK
button mappable: some pads send BACK from their left middle button, and the phone's own back key is
the *same keycode*. The two are distinguishable **only by source** — map the pad's, never the keycode.

**(A) What you must not claim:** sources you are not handling. Claim too much and a Bluetooth
keyboard or a D-pad-only TV remote stops working — invisibly, on a desk with neither attached; claim
too little and the pad is unreachable. Draw the line deliberately and write down which sources you
took (§4).

## 4. Two routes, two audiences, one mapping table

**(A)** Take **all** pad input — buttons included — through the Java route, even though buttons would
arrive on their own. One owner means one modal rule, one auto-repeat rule and one stuck-key ledger;
two entry points for one physical button means two places that must agree about diversion, which is
exactly the bug in §20. Return `true` from the Java handler for gamepad-source key events so they do
not also reach the windowing crate; keep that route as belt-and-braces only.

**(A)** Keep the windowing crate's **keyboard** route alive for a *different* audience: real hardware
keyboards, and D-pad-only devices such as TV remotes whose source the Java filter deliberately does
not claim. Resolve identified keys through a keyboard table and **unidentified Android keycodes
through the same gamepad table** the JNI route uses.

> **Two routes, two audiences, ONE mapping table.** A second table is a second truth (§11, §12).

## 5. Axes: hat + stick summed, latest sample, triggers as synthetic keycodes

**(A) A pad's D-pad usually arrives as HAT AXES inside the motion event**, not as D-pad key events.
Sum hat and stick **before** quantising:

```
(x, y) = (stick_x + hat_x, stick_y + hat_y)   -> the shared quantiser
```

The hat is digital ±1, so it dominates whenever it is pressed, and nobody deflects both deliberately.
One addition collapses it into the stick's existing state machine; treating it as a separate source
means two latched directions and two release paths. **Skip the historical samples** batched inside
the motion event, too — a guest that latches one of four directions can only use the latest position.

**(A) The physical stick goes through the SAME quantiser as the on-screen virtual stick** — same
engage dead zone, same keep dead zone, same turn ratio, same keyup-old-then-keydown-new emission
([input-and-hud.md](input-and-hud.md#5-the-joystick)). One quantiser, two producers; a second one is
a second set of dead-zone constants to tune, a second place to get the guest's latch semantics wrong,
and two devices that feel different for no nameable reason. Pin it: the reference port's pad tests
assert the pad behaves identically to the touch stick across engage, hold-through-a-dip, shallow
diagonal, decisive turn, release and digital-hat cases.

**(A) ⚠ Many Bluetooth pads never send trigger key events at all** — the shoulder triggers exist
**only** as analogue axes, and *which* axis pair carries them depends on the pad (dedicated trigger
axes on some, the brake/accelerator axes on others). Read **both** pairs, take the larger, threshold
in the host (§6), then feed the result through the **same mapping table as a synthetic keycode** for
that trigger. Routing the synthetic edge through the table — rather than straight to an action — is
what keeps a player's remap file covering analogue and digital triggers with one line. `⚠` because
the reference port had no pad on the build machine: the arithmetic is pinned by a truth-table
selftest, the per-vendor axis families are device-pending (§25).

## 6. Trigger hysteresis is the joystick's dead zones again

**(A)** An analogue-to-digital threshold needs **two** thresholds: engage above a high value, release
below a lower one. One threshold makes a trigger resting on it chatter, and the guest sees a
machine-gun keypress.

**(B)** The reference port engages at **50 %** of full deflection and releases at **25 %**, pinned by
a five-assertion selftest that walks a value up through the engage point, dips it *between* the two,
and asserts it stays released until it crosses the high threshold again. The numbers are one port's
choice; **the shape is the transferable part**, and it is the same shape as the virtual stick's
`DEAD_ENGAGE > DEAD_KEEP` pair. Anyone who writes one threshold here will write one on the next axis
too — so state the rule, not the value.

## 7. Mapping is data: the override file, defensive parsing, explicit NONE

**(A)** The map from pad control to guest action must be **data**, not code: one text file of
`<android keycode>=<TOKEN>` lines, consulted **before** the built-in defaults, with tokens for every
guest key, every host-only action, and an explicit **`NONE`** that swallows a button.

```
# <prefix>_gamepad.txt  — one line per override, '#' comments, whitespace tolerated
96=OK          # bottom face button -> guest confirm
102=NONE       # this pad fires L1 on its own; disable it
```

Three requirements, each earned:

- **`Option<Action>`, not `Action`.** "Explicitly unmapped" cannot be expressed by the defaults table
  and must be representable, or a player cannot disable a button their pad fires accidentally. Lookup
  is *override present and Some* → that action, *present and None* → swallow, *absent* → default.
- **Parse defensively, per line.** A typo is ignored; nothing panics; a bad line does not discard the
  good ones. **Do not reuse the emulator backend's own key parser** if it panics on unknown tokens —
  the reference port deliberately kept a separate, total parser for exactly that reason.

Pin it with a selftest covering an override winning over a default, `NONE` unmapping, whitespace,
comment lines, and several kinds of garbage. Config-file layering and reload semantics:
[storage-and-config.md](storage-and-config.md).

## 8. Badges: Android reports POSITION, not the printed letter

**(A) `HIGH / CONFIRMED`.** Android reports face buttons **by position**. The kernel emits
south/east/north/west and the platform key layout maps those onto the `BUTTON_A/B/Y/X` keycodes — so
"the keycode named A" means **the bottom face button**, which is printed **A** on one vendor's pad,
**B** on another's (its letters are mirrored), and a **letterless symbol** on a third's. The keycode
constants' *names* are the misleading part.

> A HUD that prints a letter is therefore **wrong on two of the three common pad families**. Draw
> the **position**: a diamond of four dots with the bound one lit.

Guard it with a selftest that asserts each face keycode maps to a *position* and fails if a later
maintainer "corrects" it back to a letter. This is the single design fact that makes a pad HUD
portable; without it, every badge is a per-vendor quirk table that can never be complete.

**(A) A second, independent reason to prefer position glyphs: they need no font.** A badge is a dozen
pixels of radius — **(B)** ~24 px diameter on the smallest control in the reference HUD — and a 5x7
bitmap glyph inside it is unreadable. Ports that already fought the bitmap-font problem for CJK
([glyphs-and-text.md](glyphs-and-text.md)) will reach for text by habit; the size argument kills it
before the portability argument is needed.

## 9. REFUTED: "draw the set, light the member" needs a visible set

> **REFUTED.** *Previously shipped, as a uniform visual language across five badge families:* "draw
> the whole set and light the bound member" — a diamond of four dots for the face buttons, and by
> the same logic an abstract **2x2 bar grid** with one bar lit for the shoulders, triggers and stick
> clicks.
>
> **Refuted by:** a player on a shipped build, one session after the badge system was designed. The
> report was literally *"I don't know what the yellow one of the four bars means."*
>
> **Actual mechanism:** the visual language is correct **only where the player can SEE the physical
> arrangement it depicts.** Four dots in a diamond are 1:1 with what is under the thumb. Shoulders,
> triggers and stick clicks are **out of sight during play**, so the bar grid depicted nothing the
> player could look at and compare. The wrong reading was attractive because the face-button case is
> genuinely excellent and consistency is a virtue — so the design was applied to all five families
> at once, on the strength of one.
>
> **Fix:** spell the **name** for controls that are out of sight (§10). Keep the position glyph only
> where the arrangement is visible.
>
> **Consequence if carried forward:** any port copying the diamond copies the bars too, and ships
> badges that are *worse than text* on the controls the player is least sure about.

**(A) The rule this establishes:** *"positions, not letters" is a rule about **ambiguity**, not a rule
about pictures.* Where a name cannot be wrong, a name is better. And when a name will not fit, raise
the badge's minimum radius — **do not shrink the font** (§13, §17).

## 10. Vendor-neutral names, and how they reconcile with "no letters"

**(A)** Vendor-neutral names are safe exactly where **no pad prints anything that contradicts them**.
`L1 L2 R1 R2 L3 R3` is a different word from one vendor's LB/LT or another's L/ZL, but it means *the
same control in the same place*, so it cannot be wrong the way a printed letter can.

The reconciliation, which must travel with both rules or a later maintainer "restores consistency"
and regresses one of them:

| | face buttons | shoulders · triggers · stick clicks |
|---|---|---|
| visible during play | yes — under the thumb | no |
| letters printed on the hardware | **contradictory across vendors** | none |
| badge | position glyph (diamond, bound dot lit) | vendor-neutral **name** |

**(B)** Corroborating evidence that those names read as unambiguous: the reference port's diagnostics
legend had already been describing them in prose ("left upper shoulder (L1/LB)") before badges
existed, and nobody had ever queried it.

## 11. A badge is a REVERSE LOOKUP of the action table

**(A)** Derive every badge by **reverse lookup from the action table** — scan the code list and take
the first code whose *resolved* action equals the one being badged, using the **same override-aware
resolver the input path uses**. Never build a second table of "which button to draw".

```rust
fn pos_for_action(want: Action) -> Option<Pos> {
    ALL_CODES.iter().copied()
        .find(|&c| resolve(c) == Some(want))     // resolve() == the input path's resolver
        .map(pos_of)
}
```

A badge is a promise to the player about what a button does. A second table makes that promise
falsifiable by a config file the drawing code never reads, and the failure is silent and confidently
wrong. **(B)** In the reference port two later remaps — a shoulder/trigger swap, and a re-seating of
three menu keys — needed **no HUD change at all**. That property is the claim.

**(A) Scan the code list in PHYSICAL order**, not numeric: face buttons, shoulders, middles, sticks,
hat. An action is often reachable from two codes (confirm on the bottom face button *and* the hat's
centre press), and physical order makes the badge name the control the player thinks of. Numeric
order is the default anyone reaches for and it is wrong on exactly the most-used button — the bug
reads as "the badge on OK shows a d-pad".

## 12. One store for the overrides

**(A)** There must be exactly **one** store for the mapping overrides. If your drawing path is free
functions with no application handle — the usual shape for an overlay renderer — that store has to be
**process-global and write-once** (a `OnceLock` set at boot), not a field on the app struct. A field
plus a copy handed to the draw call is precisely the two-sources-of-truth that §11 exists to prevent,
and the natural refactor ("pass the map to the draw call") spreads it to every call site and
eventually to a stale copy.

**(B)** The reference port *deleted* the app-struct field when badges were added, with the reason
recorded: *one store, so what a button does and what its badge says can never disagree.* Two
pre-existing globals in the same crate — the touch-stick mode and the immersive flag — had already
been made global for the same reason, which is the tell that this is a recurring shape.

## 13. Badge geometry: rim, floor, cap, clamp — and which diagonal is yours

**(A)** Five clauses. Every one of them is a bug somebody will ship:

| clause | the bug it prevents |
|---|---|
| place the badge **on the control's rim**, not beside it | a floating badge reads as a separate control |
| size it as a **fraction of the control's inscribed radius** | badges that do not track a resized control |
| give it a **pixel floor** | the glyph mushes on the smallest control |
| **cap** it well below the control's own radius | a small control that looks like a badge |
| clamp inside the window with **`max` before `min`** | negative centres on a window narrower than the badge |

**(B)** The reference HUD's values, from the method that produced them: radius **0.36** of the
inscribed radius, centre at **0.74** of it (on the rim), a **12 px** floor *derived from the smallest
real control* (~35 px radius menu keys on a 1080-tall window), and a **55 %** cap. Nine selftests pin
the **properties, not the numbers**: on the rim; never bigger than its control; scales with its
control; clamped at all four corners; `None` for a zero-sized control or window; finite for a window
smaller than the badge itself.

**(A) Which diagonal the badge sits on is a property of your layout, not a convention.** Pick the
direction empty for *every* control in *every* cluster and verify it against the real arrangement. A
badge landing on the game is fine — the whole overlay does; a badge landing on the **next control's
badge** is not. **(B)** Down-left was the only free direction in the reference HUD because its skill
plates arc up-and-left from the action disc while its menu keys run down the left edge. Copy
"lower-left" into a differently-arranged HUD and the collision appears on the densest cluster only —
i.e. after shipping.

## 14. One trailing pass, only where the control was drawn

**(A) Draw badges in ONE pass after the entire HUD is painted**, not inside each control's draw call.
Some controls paint their face twice (a generic round face plus a specialised icon pass), and a badge
drawn with the face ends up *underneath* the second layer — visible on most controls and invisible on
the one or two that matter most, which reads as a random rendering bug. Run the pass **before** any
modal dim, so the modal covers badges like everything else.

**(A) Badge a control only where that control was actually DRAWN this frame.** Host-owned controls
with no guest key are badged **by action**, each inside the same conditional that painted it — **(B)**
in the reference port the page flip, the quick-save, and the map toggle *only while the toggle rather
than the expanded panel is up*. A badge for an absent control tells the player a button does
something; they press it, nothing happens, and that is indistinguishable from a broken mapping.

**(A) Badges are DRAW-ONLY — not one pixel of hit testing changes.** Say so explicitly: a badge that
overhangs its control does **not** extend it, and growing the hit rect to match is the first thing a
maintainer will "fix" when a player reports a near-miss — silently stealing pixels from the neighbour.

**(A) Suppress the badge pass while the layout editor is open.** Generalises: any overlay layer that
annotates **live input** must be gated off in the editor, or the editor's own labels and the
annotation fight for the same pixels — see [layout-editor.md](layout-editor.md).

## 15. The legend must name unreachable actions

**(A)** A diagnostics legend built from the same reverse lookup (§11) must print a row for an action
a remap left **unreachable** — `(none) = quick-save` — rather than omitting it. An omitted row reads
as *"this feature does not exist"*, and a player who unmapped a button by accident cannot debug an
absence. The legend describes the **configured** pad, not the defaults, for the same single-source
reason as the badges. On-device diagnostics generally: `${CLAUDE_PLUGIN_ROOT}/shared/device-verification.md`.

## 16. The pad HUD is a MODE: drop the zone, not the drawing

**(A)** A pad changes the **whole HUD**, not just its labels. With a pad driving, the on-screen walk
controls are a second copy of a control the player's thumb is already on — so **remove** them.

> **⛔ Drop the ZONE, not merely the drawing.** An undrawn hit zone is an **invisible control**, and
> this one would swallow the very tap that switches back to the touch HUD (§17). Gate drawing and
> hit-testing with the **same** predicate — the stick-zone predicate is one expression, and it must
> return `None` in pad mode.

The reference port learned the drawing-versus-zone rule three separate times — a menu-mode stick
placeholder, a second touch mode, and then this — which is why it is a rule and not a note. The
related failure ("taps do nothing where nothing is drawn") and the whole-window-zone trap are
[input-and-hud.md](input-and-hud.md#1-the-ordered-hit-test-chain)'s.

**(A) Family-specific exception.** In an **opaque, non-immersive** layout family the walk control
**stays** in pad mode: there it sits on a black control column rather than over the game, so removing
it frees no view and leaves an empty strip. The rule is about **occlusion**, so it applies only where
controls occlude — a HUD-mode rule applied to every family breaks the one nobody tests. Two
*alternative* walk controls chosen by a user setting are a different problem: model them as a filter
over one id set ([layout-editor.md](layout-editor.md)).

## 17. Three-way setting, no timeout, and the swallowed recovery touch

**(A)** Make the pad HUD a **three-way user setting: AUTO / ON / OFF**, not autodetection alone.

- **AUTO = the last input source wins, with NO timeout.** Every switch is then something the player
  *just did*, so nothing changes by itself. A timeout makes the controls vanish while the player is
  reading a dialogue box.
- **ON and OFF exist because** "a pad is connected", "a pad is being used" and "a pad is lying on the
  table" are three different intents and only the player knows which.

**(B)** Pin it with a five-property selftest: token round-trip, garbage falls back to AUTO, the
three-state cycle closes, and the full `active(pad_last)` truth table.

**(A) The touch that brings the touch HUD back must be SWALLOWED.** The controls it would be
hit-tested against **did not exist when the finger came down**, so dispatching it opens a map or picks
a menu row "by itself". Mark that pointer in the same consumed-by-the-UI identity set every other
UI-owned touch uses, so its **release** is dropped too; put the arm immediately after the
layout-editor branch of the chain, and have the mode-flip function **return a boolean** so the caller
can decide to swallow.

> **(A) General rule:** the input that **changes the control set** must not also be dispatched
> against the new set. Same family as "never decide twice on the two edges of one touch", arriving
> from a different direction.

**(B) Adding a settings row shrinks every other row.** The reference port went from five rows to six
when the pad-HUD row landed and the row height fell from **82 px to 68 px** at 1080. State the pitch
consequence when you add a row and check the smallest label still reads — **grow the container, never
shrink the glyph.**

## 18. "Pad seen" versus "pad driving"

**(A)** Two booleans, and they answer different questions:

| flag | set by | gates |
|---|---|---|
| `pad_seen` | any pad event this session | whether the **settings row appears** at all |
| `pad_last` | the last input source used | whether the pad HUD is **active** in AUTO |

Never show a touch-only player a row that can do nothing for them — and once the preference is
**non-default, keep the row visible** so whoever set it can undo it. Two booleans is the whole cost of
shipping neither a noise row nor a trap row.

**(A) Record "which source was used last" from the INPUT path only, never from the drawing path.**
Reading it while drawing makes the HUD depend on what was last drawn — a feedback loop that gets
diagnosed as a relayout bug. Only the two input entry points may call the recorder.

**(A) An idle stick's noise is not input.** Count "the pad was used" only from a sample that actually
**changes the latched direction** — put the call inside the `new != current` branch of the axis
handler. Axis events arrive continuously from a pad that is merely connected, so any "last input
wins" scheme that treats every sample as input is **permanently stuck on the pad**, and the player can
never get the touch controls back.

## 19. Do not remap per UI mode

**(A) Considered and rejected.** A feature-phone guest reuses the **same digit keys with
window-dependent meaning** — the field dispatcher reads them as quick slots, an open window reads the
same digits as page controls. A pad that sends *different keys per mode* stacks a second indirection
on top of the game's own, and the remap file (§7) would have to know about modes too.

**Let the BADGE carry the mode-specific meaning instead** — it sits on whatever control occupies that
seat now, and it is already derived by reverse lookup (§11), so it costs nothing. "Shoulders should be
previous/next page in menus" is an appealing improvement that makes the mapping unpredictable, and the
reason it is wrong is a property of the **guest**, so expect it to hold for most feature-phone titles.
Which mode the game is in: [mode-resolution.md](mode-resolution.md).

## 20. Modal capture: a second entry point reuses the first one's bookkeeping

**(A)** A second input entry point must **reuse the first one's modal bookkeeping, not re-derive it.**
The pad's held map is `key -> was_diverted`, which is *exactly* the touch path's set of pointer ids
whose DOWN was diverted — same semantics, same release rule. Mirror the touch key-edge function
branch for branch, and state the mirror as a requirement so a reviewer notices a missing branch.

**(A) A raw key leaking into the guest while a host modal is up is a CORRECTNESS bug, not polish.**
The key does whatever the game's currently-open window does with it: navigating a list, closing the
popup under the host's own highlight, or confirming a purchase. The user-visible symptom is usually
*"nothing happened"*, which sends the investigation to the wrong module.

> **REFUTED.** *Previously shipped:* a host picker (choose a quick-bar slot to bind) whose behaviour
> the **touch** path understood completely — including its second stage — was assumed to be device-
> agnostic because the modal state lived in one place.
>
> **Refuted by:** a player report on a shipped build, one session after pad support landed:
> *"pressing the pad button does nothing."*
>
> **Actual mechanism:** the **pad path knew about only the first of the picker's two stages**, so the
> pad's buttons sent their **raw guest digits** into the game's open window underneath, which did
> something unrelated. The wrong reading was attractive because the modal *state* was shared — but
> the *capture rules* were not, and only the touch entry point had them.
>
> **Fix:** a reverse lookup from the button's guest key back to the picker slot it corresponds to,
> plus the same divert bookkeeping the finger uses — pinned by a round-trip selftest asserting the
> lookup is the exact inverse of the forward slot→key table, and that non-picker keys do not bind.
>
> **Consequence if carried forward:** the **worst symptom class there is** — nothing happens and
> nothing is logged — and an investigation that starts in the mapping table.

**(A) The general rule:** every host modal must **enumerate its behaviour for every input device**,
not just the one it was written with. §21 is that enumeration.

## 21. The picker table: diverted / passed through / swallowed whole

**(A)** Be explicit **per input**, not per device. Write it as a table; the missing device or the
missing key then becomes obvious at review.

| input, while the host picker is open | treatment | why |
|---|---|---|
| the inputs the picker is **asking about** (the slot buttons) | act on the picker, **diverted** | the guest must never also see the digit |
| **cancel** | acts on the picker **and passes through** | the game's own popup underneath has to close too |
| **everything else, confirm included** | **swallowed whole** | a second confirm can execute a *different* row after a cursor move |
| the host's own quick-save | swallowed | explicitly excluded while a picker is up |
| **(B)** one named exception: page flip during registration | passes | a player asked for it by name |

**(A)** Both defaults are wrong: "swallow everything" breaks cancel, "pass everything" breaks the
picker — only the enumeration is correct. **Grant at most one exception, and only because a player
asked for it by name**; an exception granted on a maintainer's judgement is how the table stops being
a table.

## 22. Press versus release, and the matched-pair invariant

**(A) A modal must intercept only the PRESS.** A release falls through to the normal release path,
which sends a key-up only for a key it actually pressed. Swallowing releases inside the modal
**strands** a key that was already down when the modal opened — the guest walks forever in whatever
direction the player was holding. Write it as a prohibition above the branch, because "while the
modal is up, swallow the key" is the natural one-line implementation.

**(A) The dual rule: if you DIVERTED a press, you must also drop its release.**

> **The single invariant behind both halves: the guest sees exactly one key-up per key-down it saw.**

That is why a **per-holder boolean** (`diverted`, beside the held key or pointer id) is required and a
bare "a modal is up" flag is **not** — the flag cannot say whether *this* press was diverted. **(B)**
The reference port carries the same bookkeeping in three places (the touch popup-confirm set, the
pad's held map, the picker-bind path), each recording the diversion at press time and consulting it at
release. State the invariant rather than either rule, or a reader copies whichever half they saw first
and gets the other one backwards. One trap that belongs to the touch path but bites any modal **opened
by a key**: "same key as last time is not a press" must be the **first** test in the key-edge
function, above the modal test —
[input-and-hud.md](input-and-hud.md#7-never-decide-twice-on-the-two-edges-of-one-touch).

## 23. Auto-repeat: drop the system's, keep the worker's

**(A)** Forward the system's **repeat flag** from Java and then **return early on it**, and on a
key-down for a key already held. The worker replays held keys on its own cadence
([input-and-hud.md](input-and-hud.md#8-auto-repeat-lives-on-the-worker)), so a system repeat is a
*double* repeat and a second key-down makes the guest **re-latch mid-hold**.

**(B)** The reference port's worker replays at **100 ms**; the number is per-port tuning, the
**ownership rule is not**. Both repeaters are invisible individually — together they produce a guest
that walks at double speed, and the symptom gets blamed on emulator timing.

## 24. Release discipline: dispatch stops on focus loss

**(A)** Release the pad's held set on **focus loss** and **suspend**, for a mechanism the touch path
does not have: **once the Activity loses focus, the Java dispatch stops — so the release event for a
held button never arrives from the device at all.** There is no lost finger to notice; there is simply
no further event. The pad also needs its **own** release-all, because the touch one reaches none of
the held-button map (`key -> was_diverted`), the **latched stick direction**, or the **synthetic
analogue-trigger booleans** (§5), which are host state rather than device state.

Call it from the same teardown sites as the touch cleanup, and add a pad row to the lifecycle table in
[platform-glue.md](platform-glue.md#7-the-activity-lifecycle) with the mechanism spelled out. Copying
the touch discipline verbatim leaves a pad-held direction repeating forever after a phone call.

## 25. Building this without a pad

**(A) Pad support can be built and shipped with no pad on the development machine** — source-read the
input chain (§1), mirror the existing touch discipline (§20–§24), and pin every pure rule with
selftests (`${CLAUDE_PLUGIN_ROOT}/shared/selftest-convention.md`). **Then say so**, and mark every
itemised behaviour device-pending.

**The plumbing is derivable from source; the ergonomics are not.** **(B)** In the reference port both
things that came back wrong came back from **players** on shipped builds, and neither was in the JNI
path: an unreadable badge family (§9), and a host picker the pad could not answer (§20). So when
feedback arrives, look **first** at the badge vocabulary and the modal-capture table — not at the
dispatch override. Device-verification discipline for a blind build:
`${CLAUDE_PLUGIN_ROOT}/shared/device-verification.md`.

## 26. Recorded limits

**(A)** Record the pad capabilities you deliberately did **not** implement, each with the one change
that would lift it. An unrecorded omission is indistinguishable from a bug and gets re-investigated.

| not implemented | reason | what would lift it |
|---|---|---|
| right-stick axes | a game with no camera has nothing to map them to | a target for the axis exists — add the axis then, not before |
| rumble | no guest signal drives it | a guest event worth feeling |
| the host-invented quick-slot **cooldown ring** stays touch-only | pad quick slots are **raw guest keys**, not the host's own cast event | route the pad's press through the host cast event *deliberately* — ⛔ not merely to animate a ring |

The third row is the useful one: recording the lift path is also what prevents the **wrong** fix.

## 27. Symptom table

| Symptom | Cause | Fix |
|---|---|---|
| Buttons work, the stick produces no events at all | the glue's default motion filter discards non-touchscreen motion | Java `dispatchGenericMotionEvent` + JNI (§1, §2) |
| The character teleports when the stick moves | axes forced through the windowing crate arrive as touch coordinates | do not route axes there at all (§1) |
| A hardware keyboard types quick-slot digits, or the phone's back key dies | `(source & mask) != 0`; source classes share the button bit | full-mask idiom, fall through to `super` (§3) |
| Two shoulder buttons do nothing on a Bluetooth pad | that pad's triggers exist only as analogue axes | read both axis families, take the max, threshold with hysteresis, synthesise the keycode (§5, §6) |
| The trigger fires like a machine gun near its threshold | one threshold | engage high, release low (§6) |
| A pad-held direction repeats forever after a phone call | dispatch stopped; no release will ever arrive | a separate pad release-all on focus loss and suspend (§24) |
| The guest walks at double speed with a pad | system auto-repeat forwarded on top of the worker's replay | drop the repeat flag and already-held key-downs (§23) |
| A badge names a button that does something else | a second "which button to draw" table | reverse lookup through the input path's resolver; one store (§11, §12) |
| The button hint is right on one brand of pad and wrong on others | a printed letter was drawn | draw the position (§8) |
| Players cannot tell what a shoulder badge means | an abstract set glyph for a control they cannot see | spell the vendor-neutral name (§9, §10) |
| A badge is under an icon on some controls, over it on others | badges drawn inside each control's draw call | one trailing pass before the modal dim (§14) |
| A badge promises a control that is not on screen | host-only actions badged unconditionally | badge by action, inside the conditional that drew it (§14) |
| The first touch after using a pad opens the map "by itself" | the HUD-flip touch was also dispatched, against a new control set | swallow it via the UI-consumed identity set (§17) |
| Stuck in pad mode; no touch brings the controls back | every axis sample counted as input, or the source is noted from the draw path | count only direction-changing samples; record from the input path (§18) |
| Taps do nothing where the walk control used to be | only the drawing was gated for pad mode; the zone still issued | drop the zone with the same predicate (§16) |
| A pad button does nothing while a host picker is open, nothing logged | the pad path knew only the picker's first stage; raw key went to the guest window | enumerate per input; reverse-lookup the button to the picker item (§20, §21) |
| A key already down when a modal opened stays down forever | the modal swallowed releases too | intercept the press only (§22) |
| The guest gets a key-up for a direction it never started | a diverted press was followed by a forwarded release | per-holder `diverted` boolean; matched pairs (§22) |

## 28. Checklist

- [ ] Axes come in through a Java dispatch override and JNI; nothing tries to fix the glue's filter.
- [ ] The natives only relay through the event-loop proxy; events before the loop exists are dropped.
- [ ] Source tests use `(source & mask) == mask`; non-matching events reach `super` untouched.
- [ ] The pad route owns buttons too; the windowing route is kept for keyboards and D-pad remotes.
- [ ] Hat and stick are summed before quantising, and the quantiser is **shared** with the touch stick.
- [ ] Triggers read both axis families, threshold with two values, and enter as synthetic keycodes.
- [ ] Mapping is a text file parsed defensively, with an explicit `NONE`, over built-in defaults.
- [ ] No badge prints a face-button letter; names are used only where nothing contradicts them.
- [ ] Every badge is a reverse lookup, scanned in physical order, from one write-once store.
- [ ] Badge geometry: on the rim, floored, capped, clamped `max` before `min`; diagonal verified
      against your own clusters.
- [ ] One trailing badge pass; only where the control was drawn; draw-only; off in the editor.
- [ ] The legend prints `(none) = <action>` for anything a remap left unreachable.
- [ ] Pad mode removes the walk **zone**, not just its paint — except in an opaque layout family.
- [ ] The HUD setting is AUTO/ON/OFF with no timeout; the recovery touch is swallowed.
- [ ] `pad_seen` gates the settings row; `pad_last` is written only from the input path, and only by
      a direction-changing sample.
- [ ] No per-mode remapping; the badge carries the mode-specific meaning.
- [ ] Every host modal has a written per-input table, and the pad reuses the touch path's divert
      bookkeeping.
- [ ] Modals intercept the press only; every diverted press drops its release; exactly one key-up per
      key-down the guest saw.
- [ ] The pad has its own release-all covering buttons, latched direction and trigger booleans.
- [ ] If it was built without a pad, that is written down and every itemised behaviour is marked
      device-pending.
- [ ] The limits you did not implement are recorded with their lift paths.
