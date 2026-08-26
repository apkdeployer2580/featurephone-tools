# Filling the screen: bars, cutouts, orientation

Four independent decisions stand between a working emulator and a game that actually covers the
glass: the system bars, the display-cutout policy, the orientation constant you request, and what you
recompute when the window changes size. Each has a different lifetime, each fails in a way that looks
like something else, and one of them — the orientation constant — is a defect that ships as a single
wrong number. Scaling and letterbox arithmetic and the geometry handoff to the guest belong to
[thread-model.md](thread-model.md); the JNI plumbing and lifecycle callbacks these calls ride on
belong to [platform-glue.md](platform-glue.md).

Tags: **(A)** generic · **(B)** worked example from the reference port, re-derive it ·
**(C)** do not copy.

## Contents

- [1. What "fills the screen" actually consists of](#1-what-fills-the-screen-actually-consists-of)
- [2. Hiding the system bars: two implementations](#2-hiding-the-system-bars-two-implementations)
- [3. Re-assert on focus gain — your own dialogs are the trigger](#3-re-assert-on-focus-gain--your-own-dialogs-are-the-trigger)
- [4. Display cutout: opt in once, as a window attribute](#4-display-cutout-opt-in-once-as-a-window-attribute)
- [5. Why this is a cutout question and not a scaling question](#5-why-this-is-a-cutout-question-and-not-a-scaling-question)
- [6. The cost of opting in: a hole inside your layout](#6-the-cost-of-opting-in-a-hole-inside-your-layout)
- [7. Orientation: three constants, and the one that obeys the rotation lock](#7-orientation-three-constants-and-the-one-that-obeys-the-rotation-lock)
- [8. Window size is not constant for the life of the process](#8-window-size-is-not-constant-for-the-life-of-the-process)
- [9. The volume rocker's stream](#9-the-volume-rockers-stream)
- [10. Symptom table](#10-symptom-table)
- [11. Checklist](#11-checklist)

---

## 1. What "fills the screen" actually consists of

**(A)** Four decisions, and the important column is the third. Copying one row's lifecycle handling
to another row is either wasted work or a bug.

| decision | mechanism | lifetime | failure if wrong |
|---|---|---|---|
| system bars hidden | transient UI state on the window | **must be re-asserted** — the system clears it | navigation bar returns permanently after the first dialog |
| cutout region claimed | a **window attribute** | set once; survives rotation and focus | black band along one screen edge |
| orientation | an activity request constant | applied at creation and on preference change | no rotation at 180°, or rotation against the player's lock |
| window-derived geometry | your own layout resolver | recomputed on every resize | controls off-screen, or under the camera hole |

**★ (A) Rows 1 and 2 look like the same subsystem and are not.** They are set a few lines apart, in
the same method, on the same window — and one is a flag the platform revokes while the other is an
attribute that persists. **(B)** The reference port implemented both in one place and recorded the
distinction, because the instinct is to hang the cutout call off the same focus callback as the bar
call. That produces no visible bug, just a pointless call a few hundred times a session — which is
why testing never catches it.

## 2. Hiding the system bars: two implementations

**(A)** Two paths, split by API level, both driven from the UI thread (see
[platform-glue.md](platform-glue.md#1-the-java-side-is-tiny-and-must-stay-tiny) for the thread hop):

- **Modern path** — the window's insets controller: hide the system-bar inset types and set the
  behaviour that lets the player swipe them back **transiently**. Transient is the point: the bars
  must stay reachable, and they must not resize your window when they appear.
- **Legacy path** — the decor view's system-UI visibility flag word, with the *immersive-sticky* flag
  plus hide-navigation, fullscreen, and the stable-layout flags so the layout does not jump when the
  bars come and go.

> **REFUTED.** *"Hiding the bars is a one-shot call at creation."*
> **Refuted by:** the reference port's own implementation, which carries both paths *and* a
> focus-gain re-assertion, with the mechanism and the exact trigger written into the comment: on the
> legacy path the system **clears** the visibility flags whenever the bars are shown again, and the
> app's own platform dialogs are what shows them. `CONFIRMED`.
> **Why the wrong reading is attractive:** the call *works*. The screen is full immediately after
> launch and stays full for as long as you keep testing it — which is until you open a dialog.
> **Consequence if carried forward:** the navigation bar returns permanently mid-session and never
> leaves, presenting as "the bar-hiding code failed" rather than "the bar-hiding code was undone".

**(A)** Do not use "are the bars hidden" as an input to anything. You do not own that state; the
player can summon the bars at any moment and the platform can drop your request. Own the *request*,
re-issue it (§3), and derive nothing from the outcome.

## 3. Re-assert on focus gain — your own dialogs are the trigger

**(A)** Re-issue the hide from the window-focus-gained callback as well as from creation and resume.
Re-assert on **both** paths even though only the legacy one revokes: the call is idempotent and
cheap, and a field report never tells you which path the reporter's phone took.

**(A)** The triggers are the features you built. Anything that puts a platform dialog over your
window shows the bars: a text prompt, an address entry, an account entry, a permission sheet.
**(B)** In the reference port the trigger named in the code comment is exactly its own configuration
dialogs — the ones reached from the settings entry point.

**★ This is why the fault survives testing.** It cannot appear in a launch-and-look pass, because it
requires the player to *use* something first. Add "open a dialog, dismiss it, look at the bottom of
the screen" to the manual pass, or it ships.

## 4. Display cutout: opt in once, as a window attribute

**(A)** With the **default** cutout policy the window stops at the cutout's safe area. The bars can
be perfectly hidden and a band still remains along the edge that carries the camera hole, because the
window never extended there in the first place.

Opt in by setting the window's layout-in-display-cutout mode, guarded by API level:

| API level | policy to request | why |
|---|---|---|
| 30 and above | the **always** policy | the widest available; covers a cutout on any edge |
| 28–29 | the **short-edges** policy | the only opt-in those levels have, and the only levels below 30 that have cutouts at all |
| below 28 | nothing | no cutouts exist; neither does the attribute |

**(A) It is an attribute of the window, not a transient flag.** Set it once, at creation, next to the
bar hiding. It survives rotation, it survives focus loss, and it needs no re-assertion — the exact
opposite of §2. ⛔ Do not add it to the focus callback by analogy with its neighbour.

**(A) Verify it; do not assume it.** Read the window's frame bounds out of the window-manager dump
and compare them against the display size:

```bash
adb -s "$ANDROID_SERIAL" shell wm size                 # the display
adb -s "$ANDROID_SERIAL" shell dumpsys window | less   # find your window's frame/bounds
```

**(B)** In the reference port that comparison returned window bounds exactly equal to the full
display, and the A/B — the same build with the attribute removed — shrank the window by **72
pixels** on a current flagship. `HIGH / CONFIRMED` (device A/B). The number is that phone's hole; the
*method* is what transfers.

## 5. Why this is a cutout question and not a scaling question

**(A)** The chain is short, and it runs the wrong way from where you will look:

```
cutout policy  ->  window size  ->  surface size  ->  your render target  ->  the game's pixels
```

The game surface is sized from the window. So a band along one edge is decided **upstream of every
line of scaling code you have**, and no amount of reading the blit, the render divisor or the
logical-size derivation will find it.

**★ The presenting report is "the game doesn't fill the screen", which reads as a resolution or
letterboxing bug.** **(B)** In the reference port it arrived exactly that way, as a player report,
and was root-caused to the cutout policy; the session's own note is that it presents as a scaling
defect and sends people into the scaling code, where the answer is not.

**(A) First question, one command:** does the window equal the display (§4)? If it does, the band is
yours and lives in the geometry. If it does not, stop reading your renderer.

**⚠ (A) A diagnostic heuristic, INFERRED from the two geometries and not measured:** a band on
**one** edge with the bars hidden is a cutout-policy band, while bands on **two opposite** edges in
proportion to the aspect mismatch are an aspect letterbox — a consequence of the guest's fixed
logical size, owned by
[thread-model.md](thread-model.md#2-deferred-boot-and-the-pre-rotation-size-race). Settle it by
measuring both bands on a dumped frame rather than by eye.

## 6. The cost of opting in: a hole inside your layout

> **REFUTED.** *"Claiming the cutout region is a pure win — the window gets bigger and nothing else
> changes."*
> **Refuted by:** a device measurement one session after the change shipped. Held one way round, the
> camera hole landed on an edge no control claimed; held the other way, it overlapped a host
> control's rectangle **exactly**. Both rectangles were measured and recorded. `CONFIRMED`.
> **Why the wrong reading is attractive:** the change *is* right, and the defect is invisible to
> anyone who always holds their phone the same way — which is everyone, including whoever wrote it.
> **Consequence if carried forward:** a shipped defect affecting the half of your players who hold
> the phone the other way, reported as "a button is under the camera" by people you cannot reproduce.

**(A) The hole is now an obstacle in your layout**, in the same sense as the game's own HUD widgets
and your own menu chrome — a fourth family for the resolver to refuse; see
[layout-editor.md](layout-editor.md#6-the-three-obstacle-families), which owns that machinery.

**(A) The procedure, and it must cover both hold directions:**

1. Log the platform-reported cutout bounding rectangles at window creation and on every
   configuration change, in the same units as your layout.
2. Resolve your layout at that window size and intersect every control rectangle against them.
3. Physically turn the phone 180° and repeat. **The developer's habitual grip is one of the two
   cases, so the untested case is the one that ships.**

**★ (A) §7 and this section pull against each other, and §7 still wins.** Allowing both landscape
directions — which you must — is exactly what doubles the surface this check has to cover. The answer
is to run the check twice, not to pin the rotation.

## 7. Orientation: three constants, and the one that obeys the rotation lock

The request itself is one activity call, reached through the class-loader pattern in
[platform-glue.md](platform-glue.md#3--the-application-not-activity-trap). **The constant you pass is
the entire decision.** For a landscape game (the portrait family mirrors it exactly):

| constant family | rotates 180°? | obeys the system auto-rotate lock? | verdict |
|---|---|---|---|
| plain landscape | **no** — pins one of the two directions | n/a | **the bug** |
| **sensor** landscape | yes | **no** | **use this** for a handheld game |
| **user** landscape | yes | **yes** | looks more correct; reproduces the report |

> **REFUTED.** *"Landscape-locking the activity means requesting the landscape constant."*
> **Refuted by:** a player report that the screen does not rotate when the phone is turned 180°,
> root-caused as a one-line fix and confirmed on device by reading the activity's current orientation
> back from the system. `CONFIRMED`.
> **Why the wrong reading is attractive:** the constant whose *name* matches what you want is the one
> that pins a single direction. Nothing in the name says "one of the two".
> **Consequence if carried forward:** half your players turn the phone the way that puts the hole and
> the charging port where they want them, and the game arrives upside down.

**★ (A) The argument that rules out the *user* variant is not in the API documentation — it is about
your users' settings.** Handheld-game players commonly keep system auto-rotate **off**. The user
variant honours that lock, so a build that ships it changes nothing observable for exactly the
population that filed the report, and you pay a second round of the same report to learn that.

**(A) Verify from the system, not from the source you just edited.** Read the orientation back out of
the activity dump after the call:

```bash
adb -s "$ANDROID_SERIAL" shell dumpsys activity com.example.<name> | grep -i orient
```

This is not paranoia. The call reaches the platform through a class resolved **by name string**, so
renaming the Java package produces a clean build and an orientation request that silently never
happens ([platform-glue.md](platform-glue.md#1-the-java-side-is-tiny-and-must-stay-tiny)).

**(A) The preference is host state and the host owns it.** Persist it yourself, apply it at creation
as well as when the player changes it, and read *it* — never the live system orientation — when
deriving the guest's logical size at boot; that derivation and its race belong to
[thread-model.md](thread-model.md#2-deferred-boot-and-the-pre-rotation-size-race).

**(A) Do not request a free four-way sensor constant** unless the guest can genuinely be reshaped.
The guest framebuffer is allocated once and its pointer is cached, so a rotation that changes the
window's aspect cannot change the guest's — the surplus becomes letterbox. Two landscape directions
keep the aspect identical; landscape-plus-portrait does not.

## 8. Window size is not constant for the life of the process

**(A)** Events that resize your window mid-process: rotation between the two allowed directions, the
bars being summoned back, a platform dialog, multi-window, and any device whose display changes shape
(the lifecycle callback list is in
[platform-glue.md](platform-glue.md#7-the-activity-lifecycle)). Three consequences, with owners:

1. **The guest's logical size does not follow.** It is decided before the first tick and is fixed for
   the life of the process. If window and logical aspect disagree the result is a letterbox, and that
   is correct behaviour rather than a defect to fix here —
   [thread-model.md](thread-model.md#2-deferred-boot-and-the-pre-rotation-size-race).
2. **Everything host-side must be a fraction of the window and recomputed on resize:** the layout
   resolver ([layout-editor.md](layout-editor.md#2-built-ins-are-a-pure-function-of-window-size)),
   every hit test, and any render divisor. **(B)** The reference port's half-resolution mode was
   affordable *because* it verified the placement store was fraction-based first — the divisor is
   applied where the surface is created **or resized**, touch is divided at one entry point, and the
   guest's logical resolution is untouched. That mode is `sustained-load.md`'s subject.
3. **The cutout attribute survives the resize; the bar request does not.** §4 versus §2.

**⚠ INFERRED — a 180° flip may deliver the same dimensions.** Under the sensor constant the two
landscape directions have identical width and height, so a recompute keyed on "did the size change"
can never fire while the cutout has moved to the opposite edge — precisely the §6 case. Key the
cutout-obstacle recompute on the **configuration change**, not on a dimension delta. Settle it by
logging the reported cutout rectangles and the resize callbacks across a physical 180° flip; nothing
in the source material measured this.

**(A) Put both sizes in the report the player can send you.** Window size and logical size, side by
side: that pair says which rotation and which render path the reporter was in, which no screenshot
does. **(B)** The reference port composes that blob from both sides of the platform boundary —
platform-only facts on one side, window and logical sizes and resolved mode flags on the other — with
a copy action, because the reporter's channel is a chat message and not a log file. See
`port-shipping/reference/on-device-diagnostics.md`.

## 9. The volume rocker's stream

**(A) Route the volume rocker to the media stream explicitly**, with the one activity-level call that
does it. It belongs in this file because it is window/activity policy, not mixing.

The failure mode is specific to games: **a game that is often silent has no active media stream, so
the rocker falls through to the ringer.** The player holds volume-up to maximum, watches an unrelated
slider fill, and reports that the game is quiet at full volume. **(B)** The reference port reached
this twice from the same class of report — first as a hypothesis in a total-silence investigation
consistent with "the volume keys do nothing", then fixed with one call in a later session that also
confirmed on device that the audio path had opened on the media stream with media usage.
`HIGH / CONFIRMED`, two independent derivations.

**★ It presents as an audio-level bug and will consume an audio investigation**, so check it before
you open the mixer. Level policy and the surrounding audio failure modes — including the rule that
one transient failure to open the audio device must never permanently disable the pipeline — are
[audio-mixing.md](audio-mixing.md)'s.

## 10. Symptom table

| Symptom | Cause |
|---|---|
| Black band along one screen edge with the bars hidden | default cutout policy; the window stops at the safe area (§4) |
| "The game doesn't fill the screen" and the scaling code looks right | it is a cutout-policy question, upstream of scaling (§5) |
| Bands on two opposite edges, proportional to aspect | aspect letterbox from the fixed guest logical size, not a cutout (§5, thread-model) |
| Navigation bar returns permanently after the player uses any dialog | legacy visibility flags cleared when the bars showed; no focus re-assert (§2, §3) |
| Bars hidden at launch and still hidden after every test you ran | you have not opened a dialog yet (§3) |
| Screen does not rotate when the phone is turned 180° | the plain landscape constant pins one direction (§7) |
| The rotation fix ships and the reporter sees no change | the *user* variant obeys their auto-rotate lock, which is off (§7) |
| Orientation request appears to do nothing at all, on a clean build | the Java class is resolved by name string; package renamed (§7, platform-glue) |
| First frame after a cold start is the wrong orientation | preference applied after creation (§7) |
| A control sits under the camera hole for some players only | cutout claimed; the hole's edge depends on hold direction (§6) |
| Controls off-screen or overlapping after a rotation | layout not recomputed as a fraction of the new window size (§8) |
| Cutout avoidance is stale after a 180° flip | recompute keyed on a dimension delta that did not change (§8, ⚠) |
| Player raises the volume to maximum and the game stays quiet | rocker on the ringer; no active media stream (§9) |

## 11. Checklist

- [ ] Bars hidden on both API paths, and re-asserted on focus gain as well as creation and resume.
- [ ] A manual pass that opens one of your own dialogs, dismisses it, and looks at the bars.
- [ ] Cutout mode set as a window attribute at creation, guarded by API level; **not** in the focus
      callback.
- [ ] Window bounds compared against the display size on a device, and A/B'd with the attribute off.
- [ ] Every control rectangle intersected against the cutout rectangles **in both hold directions**.
- [ ] The orientation constant is the **sensor** variant of the family you want, verified by reading
      it back from the activity dump.
- [ ] The orientation preference is host-owned, persisted, and applied at creation.
- [ ] Layout, hit tests and any render divisor are fractions of the window and recomputed on resize;
      the guest logical size is not.
- [ ] Cutout-obstacle recompute keyed on configuration change, not on a dimension delta.
- [ ] Volume rocker routed to the media stream.
- [ ] Window size and logical size both appear in the copyable on-device report.
