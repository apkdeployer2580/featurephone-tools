# Touch input, key edges, and the HUD

A feature-phone guest has a keypad. Your player has a slab of glass. This file specifies the
translation: how a touch becomes a keypress, how two fingers and a lifted thumb do not produce
a stuck key, and how the HUD that carries all of it is laid out.

**A third input class exists**, and it does not arrive by this path at all — a physical gamepad or
hardware keyboard, whose analogue axes cannot reach a winit-family host without an owned Java
dispatch route. Everything pad-specific is [gamepad-and-pad-hud.md](gamepad-and-pad-hud.md); what
stays here is the rule that **both entry points share one set of key-edge and modal bookkeeping**,
because two entry points with different modal rules are two confirm behaviours to debug.

**Owned elsewhere:** taps aimed at the *game's own* menus and grids
([touch-driven-guest-ui.md](touch-driven-guest-ui.md)); windows and modals the host draws
([host-windows.md](host-windows.md)); deciding which mode the game is in
([mode-resolution.md](mode-resolution.md)).

Tags: **(A)** generic · **(B)** worked example from the reference port, re-derive it ·
**(C)** do not copy.

## Contents

- [1. The ordered hit-test chain](#1-the-ordered-hit-test-chain)
- [2. Three touch-identity sets](#2-three-touch-identity-sets)
- [3. The key-edge machine](#3-the-key-edge-machine)
- [4. Tap slop: deliberate-tap zones](#4-tap-slop-deliberate-tap-zones)
- [5. The joystick](#5-the-joystick)
- [6. Held-key release discipline](#6-held-key-release-discipline)
- [7. Never decide twice on the two edges of one touch](#7-never-decide-twice-on-the-two-edges-of-one-touch)
- [8. Auto-repeat lives on the worker](#8-auto-repeat-lives-on-the-worker)
- [9. The three layout families](#9-the-three-layout-families)
- [10. Drawing the overlay](#10-drawing-the-overlay)
- [11. What is title-specific in a HUD](#11-what-is-title-specific-in-a-hud)
- [12. Symptom table](#12-symptom-table)
- [13. Checklist](#13-checklist)

---

## 1. The ordered hit-test chain

**(A)** Every `TouchPhase::Started` walks a **single ordered chain** of candidate consumers.
The first one that claims the point wins and the rest never see it. This is not a stylistic
choice: it is the mechanism that decides what "overlapping" means, and the placement resolver
in [layout-editor.md](layout-editor.md) is built directly on top of it.

The order, generically:

```
1  layout editor            -- if active, consumes ALL phases and returns immediately
2  input-source HUD flip    -- SWALLOW: the tap that brought the touch HUD back must not also act
3  full-screen modal state  -- e.g. "a guest map/menu is open": any tap means one key
4  host windows and notices -- host-windows.md; a notice does NOT get to swallow everything
5  settings panel           -- while shown, taps inside it are settings, outside it closes
6  the settings entry point (gear)
7  pull-out keypad toggle
8  host toggle controls     -- one entry per host-owned toggle
9  host action controls     -- one entry per host-owned action (page, quick-save, ...)
10 host data plates         -- e.g. quick-slot plates, whose meaning the WORKER resolves
11 large informational panels, as TAP ZONES (see §4)
12 the game button list     <-- the ONLY path that produces a held key
13 the joystick zone        <-- MODE-GATED: issued only where walking exists (§5)
14 the guest panel router   <-- taps inside the GAME's own menu panel; touch-driven-guest-ui.md
15 a mode-gated policy fallback  <-- "in this mode, a tap that hit nothing sends one key"
```

Five properties of this order, each of which is load-bearing:

- **The editor is first, in every phase.** It must outrank the modal state (the worker can set
  that flag mid-drag, and it would eat the tap) and the settings panel ("tap outside closes"
  would end the editor on the first drag that started outside a control).
- **A swallow arm sits immediately below it.** When one tap changes *which HUD is on screen* —
  bringing the touch controls back after a physical pad took over — that tap must not also be
  delivered to whatever now occupies its pixels
  ([gamepad-and-pad-hud.md](gamepad-and-pad-hud.md#17-three-way-setting-no-timeout-and-the-swallowed-recovery-touch)).
- **Everything host-owned is tested before the game button list.** That is what makes
  "a control overlaps host chrome" a *silent behaviour change* rather than a cosmetic one — the
  chrome wins and the control is dead. §[layout-editor.md](layout-editor.md#3-the-invariant)
  turns that into the resolver's strict-obstacle rule.
- **★ The guest panel router is at the very END, below the joystick.** A tap inside the game's own
  panel is the *weakest* claim on a pixel: the gear, the keypad toggle, the minimap, the quick-slot
  plates and every control the player has dragged there must keep their pixels.
- **★ The last arm is a POLICY, not a control** — see below.

Write the chain as one function returning an enum, not as a nested `if` in the event handler.
You will need to run it from the layout editor's own hit test too.

### ★ The last arm is a policy, and the joystick is not automatically it

> **REFUTED.** *Previously documented (this section, 0.1.0):* "The joystick is last, and it is a
> *zone*, not a control. It has no rectangle to miss." **Refuted by** three shipped changes that
> each put something below it: a guest-panel router, a mode-gated *"a tap that hit nothing sends one
> specific key"* fallback, and the discovery that the zone itself must not exist in every mode.
> **Actual mechanism:** "last" and "catch-all" are two different jobs. The catch-all belongs to a
> **policy** expressed as a fallback — which is why it needs no coordinate arithmetic and follows
> wherever the player has dragged their controls — while the joystick is one mode-gated arm among
> several. The wrong reading is attractive because in the first HUD you write, those two jobs really
> are the same arm. **Consequence if carried forward:** the zone eats taps in modes where nothing is
> drawn, and any router placed below it is unreachable.

Three rules follow, all **(A)**:

1. **Express "everything except the controls" as a fallback, never as a rectangle.** A rectangle has
   to be recomputed every time the player moves a control; a fallback cannot go stale.
2. **A zone is issued only in the mode where its control is drawn.** A zone that outlives what is
   drawn is an **invisible control** that eats taps, and the player has no way to see why.
3. **A detector that identifies a modal OWNS the container.** If your router recognises the game's
   panel, a tap that lands inside it but hits nothing is a **miss**, not a fall-through to the arm
   below — falling through is how a mistap on a panel becomes a keypress in the world behind it.

### ★ Chain ORDER is not the place to fix a control that is hard to hit

> **REFUTED.** *Previously shipped:* a special case that hit-tested a pinned stick *before* the game
> button list, in one mode only, with the defensible argument that it was the only way to walk in
> that mode. **Refuted by** the next session's change to that mode's shape, which deleted the special
> case wholesale — while the chain order itself survived unchanged. **Actual mechanism:** the control
> was hard to hit because the layout was **issuing** the wrong thing, and the chain order was being
> used to compensate for it. **Consequence if carried forward:** a per-mode chain order, which means
> the three ordering arguments above hold in some modes and not others and nobody can reason about
> either.

**(A) The rule: do not special-case the chain order to protect a control whose zone you have shrunk.
Change what the layout issues instead.** The chain order is one of the few things in a HUD that can
stay true for the life of the port; spend it carefully.

### The gate list is a second mechanism, not a consequence of the order

**★ (A)** Adding the panel router (arm 14) opens a hole that chain order alone cannot close. **A host
overlay centred on the window sits exactly where the guest centres its own fixed-size panel** — and
the overlay's inter-key gutters and empty cells *pass* an exact-rectangle hit test, so a thumb that
misses one of your keys falls through to the router and opens a menu row behind it.

So the router needs an explicit **gate list**: one term per overlay drawn in that region, independent
of where that overlay sits in the chain. **(B)** the reference port's router carries nine gate terms,
and the pull-out-keypad term was found by adversarial review rather than by play — then confirmed on
device by the router logging **zero** lines while the keypad was open.
[touch-driven-guest-ui.md](touch-driven-guest-ui.md#16-the-gate-list) owns the list and how to decide
what belongs on it.

### Host notices are ordered by specificity, and do not own the screen

**★ (A)** Arm 4 is not "a modal swallows everything". Three rules, each paid for by a report from
someone holding a phone, all specified in [host-windows.md](host-windows.md):

- a notice must **not** claim the point before the HUD's own confirm/cancel buttons are hit-tested —
  the player's thumb is already resting on exactly those
  ([§2](host-windows.md#2--a-notice-must-not-swallow-the-buttons-the-thumb-is-already-on));
- competing modals are ordered by **specificity**, and the key path and the touch path must use the
  **same** order ([§3](host-windows.md#3-order-competing-modals-by-specificity--in-both-paths));
- a window that cannot be answered draws **no** confirm affordance
  ([§4](host-windows.md#4--a-window-that-cannot-be-answered-draws-no-confirm-band)).

## 2. Three touch-identity sets

Android delivers pointer ids. Track three sets of them; each fixes a real, reported bug.

| set | type | purpose |
|---|---|---|
| `touches` | `HashMap<PointerId, KeyCode>` | id -> the key this finger currently holds |
| `ui_touches` | `HashSet<PointerId>` | ids consumed by host UI; excluded from game input entirely |
| `slide_touches` | `HashSet<PointerId>` | ids that **started on a game control** and may therefore change key by sliding |

`ui_touches` exists because a finger that pressed the gear must not also be a game key, and
must not become one when it drifts two pixels.

**`slide_touches` is the subtle one.** Sliding a thumb from one button to the adjacent one is
a feature — feature-phone players expect it. But if *any* moving pointer can acquire a key,
then a second finger travelling across the screen while the first steers picks up whatever it
passes over. The reference port's bug report was literally *"the minimap keeps getting clicked
by itself"*: a steering thumb crossed the minimap corner, the corner control acquired the
pointer, and the map opened. **(A) Rule: only a touch that STARTED on a control may change
key by sliding.** Everything else is either a fresh `Started` or nothing.

## 3. The key-edge machine

One function owns every keydown and keyup. It is the only place that sends `InputEvent::Key`.

```rust
fn update_touch(&mut self, id: PointerId, new_key: Option<KeyCode>) {
    let old = self.touches.get(&id).copied();
    if old == new_key { return; }

    if let Some(k) = old {
        self.touches.remove(&id);
        // keyup ONLY if no other pointer still holds this key
        if !self.touches.values().any(|&v| v == k) { self.send_key(k, false); }
    }
    if let Some(k) = new_key {
        // keydown ONLY if this is the first pointer on this key
        let first = !self.touches.values().any(|&v| v == k);
        self.touches.insert(id, k);
        if first { self.send_key(k, true); }
    }
}
```

**★ (A) The FIRST test in that function is `old == new_key`, and it is not an optimisation.**
Android emits move events continuously for a finger that is merely **resting**, so a slide-eligible
pointer re-enters this function several times a second still holding the key it already holds. If
"same key as last time" is not an early return, every one of those redeliveries is a fresh press —
and a modal opened *by* a key is then answered by that same key before the player has lifted their
thumb. **(B)** the reference port's report was that a host picker "confirmed itself instantly"; the
fix is the two-line guard above, with the player's own words in the comment.

**(A)** Both guards below are required and they are not symmetric conveniences:

- Without the keyup guard, two fingers on the same key produce a keyup when the *first* lifts,
  while the second is still pressing. The guest stops walking with a finger on the button.
- Without the keydown guard, the second finger produces a redundant keydown. Harmless for a
  latching guest, but it interacts badly with auto-repeat (§8), which will now be armed twice.

`update_touch(id, None)` is the universal release. Use it from `Ended`, `Cancelled`, focus
loss, suspend and editor entry, so there is exactly one code path that can send a keyup.

## 4. Tap slop: deliberate-tap zones

**(A)** Some regions of a HUD are large and informational — a minimap panel, a stats block —
and you want a tap on them to do something (open the full map, open the character sheet)
without a steering thumb crossing them triggering it.

Implement "deliberate tap" rather than "touch inside":

```rust
const TAP_SLOP: f32 = 28.0;   // (B) device-tuned; see below

fn is_tap(down: (f32, f32), up: (f32, f32)) -> bool {
    (up.0 - down.0).abs() <= TAP_SLOP && (up.1 - down.1).abs() <= TAP_SLOP
}
```

The zone fires only if **the down and the up were both inside it** and the movement between
them was under the slop. A finger entering the zone mid-gesture never has a down inside, so it
cannot fire. A finger that starts inside and swipes away exceeds the slop.

**(B)** 28 px was picked on one device at one density. Derive yours the same way the reference
did: swipe across the zone deliberately ten times, tap it deliberately ten times, and take a
threshold with clear separation. Scale it by display density if you support very different
devices. Record the measurement in the constant's comment.

## 5. The joystick

**(A) Before you tune anything, settle how the guest consumes direction keys.** This is a "the
game does X" claim, so it is settled in a probe run and not by reading the input handler
([evidence-standard.md](../../../shared/evidence-standard.md#6-behaviour-is-settled-dynamically-never-by-reading-disassembly)).
Three runs: hold one direction; hold two at once; hold one, add a second, then release the first.

| what the run shows | what to build |
|---|---|
| the guest **latches** one direction on keydown and ignores a second held key | four-way, dominant-axis, exactly one key held — the design below |
| the guest samples a **held-key mask** each frame and moves diagonally on UP+RIGHT | eight-way: hold both axis keys and emit each axis's keyup/keydown **independently**; drop `TURN_RATIO`. Two dead zones still apply, **per axis** |
| the guest integrates an **analog vector** | ignore this section |

A held-key mask is not exotic — it is the shape of J2ME's `Canvas.getKeyStates()`, so a run is
cheaper than an assumption.

**(B) The reference port's guest latched on keydown**, and everything below is that guest's
answer: **four-way, dominant-axis, exactly one key held at a time.** Design around the *guest*,
not around the *input device*.

```
DEAD_ENGAGE = 0.30   // (B) fraction of radius before any direction engages
DEAD_KEEP   = 0.15   // (B) hysteresis: stay engaged until you fall below this
TURN_RATIO  = 1.2    // (B) the new axis must beat the old by this factor to take over
```

**(B)** All three were tuned by feel on one port's devices — derive yours the way §4 derives
`TAP_SLOP`: hold each direction deliberately ten times and rest a thumb near centre ten times, and
put `DEAD_ENGAGE` above the resting cluster with `DEAD_KEEP` below it. **(A)** The *shape* is the
transferable part — two dead zones with `DEAD_ENGAGE > DEAD_KEEP`, plus a turn ratio on a latching
guest — not the values
([gamepad-and-pad-hud.md](gamepad-and-pad-hud.md) §6).

- **Centre is fixed at touch-down**, not trailing the finger. A trailing centre makes it
  impossible to hold a direction while adjusting grip.
- **Two dead zones, not one.** `DEAD_ENGAGE > DEAD_KEEP` stops a thumb resting near the centre
  from chattering between engaged and released.
- **`TURN_RATIO` on the axis switch.** A diagonal drag would otherwise alternate between the
  two axes every frame.

**★ The emission rule (A):** on every direction change send **keyup(old) then keydown(new)**,
in that order, in the same event.

```rust
if new_dir != cur_dir {
    if let Some(k) = cur_dir { send_key(k, false); }   // FIRST
    if let Some(k) = new_dir { send_key(k, true);  }   // SECOND
}
```

Reverse the order and the guest — which latches on keydown — sees the new direction, then a
keyup for a direction it is no longer in, and either ignores the keyup or clears the walk it
just started. The failure looks like "the character stops randomly when I change direction".

### Three facts about the stick that are not about the stick

**★ (A) 1 — The DRAWN ring radius and the DEFLECTION radius are the same number, and must stay
coupled.** The visible ring *is* the stroke: decoupling them makes the thumb stop engaging
mid-ring for no visible reason. The honest consequence is that a bigger stick starts walking
*later*, and the right response is to pin that relation in a selftest rather than to hide it with a
second constant. **(B)** measured across a 50–250 % resize range in the reference port.

**★ (A) 2 — The zone's EXTENT is a policy, not a property of the stick.** A whole-window default
silently makes the stick the intake for **every near-miss of every other control**, so a thumb that
misses a quick-slot plate walks the character instead. **(B)** that default was the real cause of a
long-running "tapping between buttons moves me" report in the reference port — misattributed for
several sessions to a tap-to-move feature that was not even the arm firing.

**⚠ (A) 3 — Deriving the deflection radius from the ZONE's dimensions while meaning the WINDOW's
is a latent unit bug.** It stays correct exactly as long as those two rectangles are equal, and
fails the day a second mode shrinks the zone. Take the radius from whichever rectangle you actually
mean, and name the parameter after it.

For a **physical** stick the quantiser is the same one: sum the hat and the analogue axes *before*
quantising and share this function rather than duplicating it —
[gamepad-and-pad-hud.md](gamepad-and-pad-hud.md#5-axes-hat--stick-summed-latest-sample-triggers-as-synthetic-keycodes).

**Why not eight-way with two keys held — when the guest latches (A).** It is the obvious design
and it is wrong for a latched-direction guest: holding UP+RIGHT makes the guest latch whichever
arrived last and ignore the other, so a diagonal produces the same motion as one cardinal but
with an extra key stuck down. That argument is scoped to **row 1** of the discriminator at the top
of this section. Row 2 inverts it — a held-key-mask guest *needs* both keys down, and four-way
one-key-at-a-time is what makes its diagonals unreachable — and row 3 skips the section outright.
Which row you are in is settled by the probe run, not by reading the input handler.

## 6. Held-key release discipline

**(A)** The worker replays every held key on an auto-repeat cadence (§8). Therefore **any**
transition that changes input routing must release everything first, or the abandoned key
repeats forever:

| event | why |
|---|---|
| suspend | the app is going away with a key down |
| focus loss | notification shade over the game; the app is still resumed |
| entering the layout editor | touches are now edits, not keys |
| leaving immersive mode / layout family change | the control under the finger no longer exists |
| the paused flag going true | the worker's own gate also clears its held set - do both |
| **a mode change WITHIN a family** that removes the zone or control under a held key | leaving the field mid-drag otherwise leaves a direction held with nothing on screen to explain it. **(B)** the reference releases the latched joystick direction whenever the new layout has no zone |
| focus loss, **for the physical pad's own held set** | a different mechanism, needing a second call site: once the Activity loses focus **Java dispatch stops**, so the device's release event never arrives at all. Buttons, the latched stick direction and the synthetic trigger booleans are not reachable from the touch release-all ([gamepad-and-pad-hud.md](gamepad-and-pad-hud.md#22-press-versus-release-and-the-matched-pair-invariant)) |

The section's framing — *any* transition that changes input routing — is right; 0.1.0's
enumeration was simply short. When you add an input source or a mode, add its row here.

One helper: `release_all_held()` iterating `touches` through `update_touch(id, None)`, then
clearing `ui_touches` and `slide_touches`. Give the pad its own `release_gamepad()` beside it and
call both from the same sites.

## 7. Never decide twice on the two edges of one touch

**★ (A)** This is the second of the five contract rules, and it earns its own section because
the bug it prevents is invisible in code review.

A modal confirm button is drawn while a guest popup is open. On DOWN you read guest state to
decide "this tap confirms". On UP you read guest state again — but the confirm you sent on
DOWN has already dismissed the popup, so the UP edge reads a *different* state and decides
something else. The two decisions then alternate on every press, and the guest's key state
inverts on each cycle.

**The fix is to remember the DOWN decision.** Keep a set of pointer ids that were classified
as "confirm" on DOWN; on UP, consult the set and delete the entry. Do not re-derive.

Generalised: *any* affordance whose action mutates the state its own hit test depends on must
be latched at DOWN. That includes toggles that move themselves, controls that appear or
disappear with a mode, and anything gated on a guest flag the action clears.

### ★ A target derived from the LIVE FRAME: latch, re-derive, and compare by MEANING

**(A) "Do not re-derive" is right for a decision read out of guest memory and wrong for a target
derived from the pixels.** A badge the game painted may have been repainted away between the two
edges, and the finger may have slid onto a different one — so the UP edge **must** re-derive. What
it must not do is trust the new answer blindly, or compare the two answers by *value*:

> **REFUTED.** *"Store the panel point on DOWN and require the UP point to equal it."* Shipped in
> the reference port. **Refuted by** the first wobble test: tap slop is around **28 window pixels**,
> which on a typical device is only ~6 *logical* guest pixels, so pixel equality drops most real
> taps. **Actual mechanism:** the two edges are answering "what did the player aim at?", which is a
> question about *meaning*, not coordinates. **Consequence if carried forward:** an intermittently
> unresponsive UI that tests perfectly — because `adb shell input tap` lifts at the *same pixel* and
> can never reproduce it. Test wobble with `input swipe` over a few pixels instead.

**The rule (A): latch the DOWN decision, re-derive on UP, and require `same_target`** — where
"same target" is defined per target kind: a badge must still be *that* badge; a point inside a panel
need only still be *a* point inside the panel. Then **act on the PRESS coordinates**, because that
is what the player aimed at. Log the press coordinates too; the release coordinates in a bug report
send you looking at the wrong pixel.

### The latch bookkeeping is per input ENTRY POINT, not per pointer

**(A)** Every input source that can answer a modal needs the same structure: the touch path keeps
*which pointers were diverted*, the pad path keeps *which keys were diverted*, with identical
semantics. **(B)** the reference port shipped a host picker only the touch path knew about — a
player with a controller could open it and then could not answer it. Two entry points with
different modal rules are two confirm behaviours to debug, and only one of them gets tested.

## 8. Auto-repeat lives on the worker

**(A)** The UI sends edges; the worker synthesises repeats. Roughly every 100 ms **(B)** the
worker re-sends keydown for each key it believes is held — the interval is per-port tuning, the
**ownership rule** underneath it is not
([gamepad-and-pad-hud.md](gamepad-and-pad-hud.md) §23).

Put it on the worker, not the UI, because:

- the guest's notion of "held" must match the worker's tick cadence, not the display's;
- the paused gate can clear the held set in one place;
- the UI thread has no business having a timer.

The consequence is §6: the worker's held set is authoritative, so the UI must always send the
keyup. A key the UI forgets is a key that repeats until the process dies.

### ★ Two classes of key event, and only one of them belongs in the held map

**(A)** 0.1.0 said "the UI sends edges" as though all edges were alike. They are not:

| class | held by | routing |
|---|---|---|
| **finger-held** — a game button under a thumb | a pointer | enters `touches`, obeys §6, is auto-repeated |
| **host-synthesised** — a tap resolved to "press the game's OK", a commit inside a guest-call chain | **nothing** | **bypasses the held map entirely**: emit an immediate down + up |

The held map exists to model *a finger holding a key*. A synthetic press is held by nothing, so
putting it in the map produces a key that repeats forever with nobody pressing anything, and no
release event will ever arrive to clear it. Write the reason at the bypass; it looks like an
inconsistency in review, and it will be "tidied" into the map by someone reasonable.

### Modal diversion: intercept the PRESS only, then remember to divert its release

**★ (A)** When a host modal borrows keys the guest also uses — the direction keys to move a
spinner, confirm to accept it — two rules that are easy to get half-right:

1. **Swallow the press, never the release.** A key that was already down when the modal opened has
   a release in flight; swallowing releases strands it, and the guest walks forever.
2. **If you diverted a press, you must divert its matching release**, tracked in a per-pointer (or
   per-key) "was diverted" set. Otherwise the guest receives a release for a press it never saw,
   which some guests treat as a press.

Apply both at **every** interception site — the reference port has three — and share the set
between them. [gamepad-and-pad-hud.md](gamepad-and-pad-hud.md#21-the-picker-table-diverted--passed-through--swallowed-whole)
carries the same table for the pad path.

## 9. The three layout families

**(A)** Pick a family from `(orientation, immersive?)` and let everything else follow.

| family | game rect | controls |
|---|---|---|
| **landscape immersive** | fills the whole window | translucent overlays **on top of** the game; every rect comes from the placement resolver |
| **portrait** | full width, top | fixed D-pad/joystick + a 3x4 keypad in the strip below |
| **landscape non-immersive** | full height, centred | controls fill the side margins |

**Only the immersive family has placeable controls**, because it is the only one where a
control's position is not already determined by the letterbox geometry. Say so in the UI: grey
the "edit layout" row out elsewhere with a reason, rather than letting the player open an
editor that cannot move anything.

Compute the game rectangle first, then the control rects, then the obstacle list — in that
order, because obstacles are expressed relative to the chrome.

### ★ A second, ORTHOGONAL axis: the input-source HUD mode

**(A)** `(orientation, immersive)` is not the whole answer. When a physical pad is driving, the walk
controls must be **removed — not restyled**: they duplicate a control the player's thumb is already
on, and they cover the game for nothing. Every pad-bound control instead gains a **position badge**.

Three rules, because this is the axis people get wrong:

- **It is not derived from the other two.** Compute it separately and combine.
- **It must not be pure autodetection.** Make it a three-way user setting — `AUTO` / `ON` / `OFF` —
  where `AUTO` means *the last input source used wins*, with **no timeout**. A timeout means the HUD
  changes shape while the player is sitting still, which is indistinguishable from a bug.
- **It has a family-specific exception.** In an **opaque, non-immersive** layout the walk control
  **stays**, because there it sits on a black control column rather than over the game, and removing
  it leaves an empty strip.

The mode itself, the badges, and the "pad seen versus pad driving" distinction are
[gamepad-and-pad-hud.md](gamepad-and-pad-hud.md#16-the-pad-hud-is-a-mode-drop-the-zone-not-the-drawing);
what belongs here is that dropping the walk control also **drops its zone** (§5, rule 2).

## 10. Drawing the overlay

**(A) A softbuffer surface has no alpha channel.** You get `0x00RRGGBB` and nothing composites
for you. Translucency is therefore hand-rolled per-pixel blending against what is already in
the buffer:

```rust
fn blend_px(dst: &mut u32, src: u32, a: u8) {
    let (da, sa) = (255 - a as u32, a as u32);
    let r = ((*dst >> 16 & 0xff) * da + (src >> 16 & 0xff) * sa) / 255;
    // ... g, b
    *dst = r << 16 | g << 8 | b;
}
```

Build three primitives on top of it — soft-edged disc, soft-edged ring, filled rect — and draw
every control from those. Anti-alias by computing coverage from the radial distance rather than
by supersampling; at control sizes it is indistinguishable and vastly cheaper.

Everything here lifts unchanged between titles: `Rect`, padding helpers, the blend primitives and
the ring/disc cluster geometry. **The font does not** — draw with the game's own where the title
ships one ([game-native-ui.md](game-native-ui.md)), and fall back to a hand-rolled bitmap face only
where it does not ([glyphs-and-text.md](glyphs-and-text.md)).

## 11. What is title-specific in a HUD

Keep these in **one module per title**, with the RE provenance in a comment above each
constant. **(B)** examples from the reference port, each introduced by the method that found
it — the values are not yours:

| thing | how it was found |
|---|---|
| slot -> hotkey map for a quick bar | disassembled the field input dispatcher's jump table and read which key index each arm consumed |
| the set of keys the game's own windows need | walked every menu the game opens and recorded which keypresses it responds to on device |
| gauge field offsets (HP, MP, level, EXP, unspent points) | god-object offsets, each with **two independent derivations** (see `guest-re`) |
| an inventory quick bar's backing arrays | found the arrays by RAM-diffing a single bind action, confirmed by enumerating every code site materialising the offset |
| a cap on the host's logical width | the guest's popup painter truncates the popup's interior x to 8 bits (a `strb` on a coordinate), so a logical width above 255 - margin tears the popup; found by rendering wide and disassembling the painter |
| "which key opens the main menu" | device-probed; on that title the soft keys did nothing at all and a different key was the menu |
| a skill cooldown | **host-invented.** The original had no cooldown; the host added one. Label host inventions explicitly - a future reader will otherwise look for the guest code that implements it |
| **a key the game DRAWS itself** | **by SCREENSHOT, not by RE** — see below. The cheapest control in any HUD |

### ★ The cheapest control in a HUD is one the game already drew

**(A)** If the guest paints its own key hints — a corner skip glyph, bracketed key hints around a
list header, an `OK :` label along the bottom — **that lettering is already a button**. Put a hit
target on the glyph and **draw nothing over it**: a translucent disc of your own on top of the
game's own lettering hides the thing the player is aiming at, and adds a second affordance for one
action.

Four mechanics, all **(A)**:

- **Find it by screenshot.** No anchor, no offset, no RE session — which is also why it works on
  screens you have never reverse engineered.
- **Measure in LOGICAL guest pixels**, and check across several logical resolutions; such hints are
  typically constant there and *not* constant in device pixels.
- **Take the target out to the window's real corner** rather than computing the ink's margin
  separately, so "the target contains the ink" is true by construction. Computing the margin
  independently puts one edge a pixel inside the glyph — **(B)** caught by a selftest at one device
  resolution in the reference port.
- **Make it much larger than the ink.** A nine-logical-pixel glyph is about forty device pixels;
  the ink is the label, not the button.

**(B)** The reference port discovered its players had been tapping the game's own lettering all
along, while the port had built a separate translucent disc elsewhere on screen for the same action.
The disc was deleted the following session.

## 12. Symptom table

| Symptom | Cause | Fix |
|---|---|---|
| A control "clicks itself" while the player steers | any moving pointer may acquire a key | `slide_touches`: only a pointer that started on a control may slide (§2) |
| A key sticks down forever after backgrounding | worker auto-repeat with no release on suspend | release-all on suspend, focus loss and editor entry (§6) |
| Character stops when changing direction | keydown(new) sent before keyup(old) | reverse the order (§5) |
| Two fingers on one button, lifting one stops movement | unconditional keyup | keyup only when no other pointer holds the key (§3) |
| Diagonal drag makes the character jitter, on a latching guest | no turn ratio between axes | `TURN_RATIO` (§5, row 1) |
| The character walks only in cardinals though the original moved diagonally | built four-way against a guest that samples a held-key mask each frame | settle the direction model in a probe run first (§5) |
| Thumb resting near centre chatters | one dead zone | two: `DEAD_ENGAGE` > `DEAD_KEEP` (§5) |
| A confirm button alternates between two behaviours | decided on both DOWN and UP | latch the DOWN decision (§7) |
| A big panel fires while the player swipes over it | hit test is "inside", not "tap" | tap-slop zone (§4) |
| Taps land on the game while a settings panel is open | panel is below the game button list in the chain | reorder (§1) |
| The editor ends itself on the first drag | settings "tap outside closes" runs before the editor branch | editor first, in every phase (§1) |
| A button's label is blank | the host font has no glyph for it | [glyphs-and-text.md](glyphs-and-text.md) |
| A host picker or modal confirms itself the instant it opens | a resting finger's redelivered move re-entered the key-edge function as a fresh press | `old == new_key` must be the first test (§3) |
| A synthesised key repeats forever with nobody touching the screen | a host-synthesised press was put in the held map | bypass the map; emit down+up (§8) |
| The guest walks forever after a host modal opened | the modal swallowed a **release** for a key that was already down | intercept presses only; divert the matching release (§8) |
| A tap on a game-drawn key hint does nothing | there is no host target on the game's own lettering | §11 |
| Taps between two controls walk the character | the joystick zone defaults to the whole window | the zone extent is a policy (§5) |
| A tap opens a game menu row through a gap in the host keypad | the panel router has no gate term for that overlay | gate list, not chain order (§1) |
| A UI target works when scripted and fails in the hand | UP compared against DOWN by coordinate; `input tap` cannot reproduce it | `same_target`, and test with `input swipe` (§7) |
| A physical pad's sticks produce nothing at all | the glue's motion filter dropped them before your code ran | [gamepad-and-pad-hud.md](gamepad-and-pad-hud.md#1-the-blocking-fact-axes-cannot-arrive-through-the-windowing-library) |
| Stick movement arrives as touch coordinates | the windowing crate reinterpreted a surviving motion event | as above |
| A pad button is stuck down after backgrounding | Java dispatch stopped; no release event exists | release the pad's own held set on focus loss (§6) |
| A badge names a button that does something else | a second mapping table instead of a reverse lookup of the action table | [gamepad-and-pad-hud.md](gamepad-and-pad-hud.md#11-a-badge-is-a-reverse-lookup-of-the-action-table) |
| A pad button does nothing while a host modal is up | the raw key went to the guest's open window; the pad path has no diversion set | §7, §8 |

## 13. Checklist

- [ ] The hit-test chain is one ordered function: editor first, the guest-panel router and the
      policy fallback last, and no per-mode reordering.
- [ ] Every zone is issued only in the modes where its control is drawn; the panel router has an
      explicit gate term for every overlay drawn in that region.
- [ ] Three touch-identity sets exist and `slide_touches` gates sliding.
- [ ] `update_touch` is the only sender of key edges, with both guards.
- [ ] Direction changes emit keyup(old) before keydown(new).
- [ ] The guest's direction model — latched, held-key mask, or analog vector — was settled in a
      probe run *before* the stick was tuned; two dead zones, a turn ratio if the guest latches;
      centre fixed at touch-down.
- [ ] `release_all_held()` is called on suspend, focus loss, editor entry, family change and any
      mode change that removes a held control; the pad has its own release beside it.
- [ ] `old == new_key` is the first test in the key-edge function.
- [ ] Host-synthesised presses bypass the held map; modal diversion takes presses only and diverts
      the matching releases.
- [ ] Every affordance whose action changes its own hit test latches its DOWN decision.
- [ ] Auto-repeat is on the worker; the UI always sends the keyup.
- [ ] Layout families are chosen from `(orientation, immersive)` and only immersive is placeable;
      the input-source HUD mode is a separate axis and a three-way setting.
- [ ] Every key the game draws itself has a host hit target on the ink, and no host art on top.
- [ ] Every title-specific HUD constant has its RE method in a comment; host inventions are
      labelled as inventions.
