# The placeable HUD and its on-device layout editor

Letting the player drag and resize the touch controls is the single highest-value feature a
feature-phone port can add, and it is where the subtle bugs live. This file specifies the data
model, the placement resolver and its invariant, and the editor's gesture handling — including
one winit-Android quirk that silently breaks pinch on every app that hits it.

The placement module is the **cleanest verbatim lift** in a wie-family host: only the control
identifier enum and the per-mode id lists are game-shaped.

Tags: **(A)** generic · **(B)** worked example from the reference port, re-derive it ·
**(C)** do not copy.

## Contents

- [1. The data model](#1-the-data-model)
- [2. Built-ins are a pure function of window size](#2-built-ins-are-a-pure-function-of-window-size)
- [3. The invariant](#3-the-invariant)
- [4. The fixpoint resolver](#4-the-fixpoint-resolver)
- [5. `accepts`: loose against controls, strict against obstacles](#5-accepts-loose-against-controls-strict-against-obstacles)
- [6. The three obstacle families](#6-the-three-obstacle-families)
- [7. The editor's predicate must BE the resolver](#7-the-editors-predicate-must-be-the-resolver)
- [8. Mirroring for left-handed play](#8-mirroring-for-left-handed-play)
- [9. Persistence](#9-persistence)
- [10. Editor gestures](#10-editor-gestures)
- [11. ⚠ The winit-Android `Moved` quirk](#11--the-winit-android-moved-quirk)
- [12. Symptom table](#12-symptom-table)
- [13. Checklist](#13-checklist)

---

## 1. The data model

### Control identity is a seat, not a key code

```rust
enum CtrlId { Ok, Action(u8), Dir, Menu(u8), Plate(u8), MinimapToggle, QuickSave, ... }
```

**(A) Identify a control by its seat in the layout, never by the key it sends.** Three reasons,
all of which bite immediately:

- **Some controls have no key code at all.** A quick-slot plate resolves its key at press time
  from whatever the slot currently holds — the worker decides, not the UI
  ([thread-model.md](thread-model.md#3-the-two-enums-and-the-design-rule-behind-them)).
- **The same key exists in two seats.** A numeric key can appear both as a menu button and on
  a pull-out keypad. Keyed by code, they are the same control, and moving one moves the other.
- **Two seats can be ALTERNATIVES for one job**, chosen by a player setting — a stick and a
  d-pad, a large action disc and a compact one. They send the same keys and only one is on screen.

Give each id a **stable short on-disk key** and a `parse()` that round-trips it. The on-disk
key is a compatibility surface: renaming a variant must not silently drop a player's layout.
Pin the round-trip in a selftest over every variant.

### Alternatives are a FILTER over one id list, never a second id list

**★ (A)** When a setting swaps one control for another, both ids stay in the same list and the
same table; visibility is a predicate:

```rust
fn shown(mode: Mode, id: CtrlId, settings: &Settings) -> bool
```

Three properties follow, and each is the reason not to maintain two id lists:

- **The hidden alternative keeps its table slot and its disk line**, so flipping the setting back
  restores the arrangement the player made for *that* control rather than resetting it.
- **The resolver keeps indexing its fixed-size arrays by position in the full list** (§1, §4), so
  nothing about priority order or array sizing changes when the setting does.
- **The editor's hit test must apply the same filter as the renderer.** Otherwise a tap aimed at
  the visible control selects the hidden one sitting under it, and the player drags something they
  cannot see. **(B)** the reference port shipped exactly that blind-tap selection and closed it by
  routing the editor's hit test through the same `shown` predicate — one predicate, two callers.

### Placement is a fraction and a percent, never pixels

```rust
struct Place {
    cx: f32,   // centre x as a fraction of window width   0.0 ..= 1.0
    cy: f32,   // centre y as a fraction of window height
    pct: u32,  // size as a percent of the BUILT-IN size for this control, e.g. 130
}
```

**(A)** A stored layout has to survive three things that pixels cannot:

1. **Rotation.** The window's dimensions swap.
2. **A different device.** The player restores a backup on a new phone.
3. **The zoom / scale setting.** Which changes the built-in sizes underneath the placement.

And it must never be expressed in **guest** pixels: the guest framebuffer size is frozen at
boot ([thread-model.md](thread-model.md#2-deferred-boot-and-the-pre-rotation-size-race)), so
guest px are a coordinate system that stops tracking the window the moment the player rotates.

**⚠ `pct` is carried, never re-derived.** Converting px -> pct -> px floors twice. If a drag
recomputes `pct` from the control's current pixel rect on every move event, the control loses
about one percent per event and a control the player merely *moved* visibly withers. Move
changes `cx`/`cy` only; resize changes `pct` only.

### The table

```rust
struct Table { field: [Option<Place>; N_FIELD], menu: [Option<Place>; N_MENU] }
```

Two fixed-size arrays, indexed by position in `FIELD_IDS` / `MENU_IDS`. Fixed-size arrays
rather than a map because the resolver iterates in **priority order** and priority is exactly
"position in the id list" (§4).

If you have a third UI mode whose control set is a subset of an existing one, **let it share
that array** rather than adding a third. An array nothing ever writes is a permanent source of
"why is this empty" confusion. **(B)** the reference port's cutscene mode shares the menu
array for exactly this reason.

## 2. Built-ins are a pure function of window size

> **★ (A) The built-in geometry function takes the window size and nothing else. No player
> setting is an input to it. Player sizing is applied *on top*, as `pct`.**

This is the central design decision of the whole module, and it was forced by a measurement.

**★ CORRECTION / worked failure (B).** An earlier design fed a global "control size" slider
into the built-in formulas. The result was **non-monotonic**: the primary action button
measured 98 px at 100% and **94 px at 120%**. The mechanism: the ring of secondary controls
derives its radius from the primary disc's size, and the per-slot size derives from the ring
circumference divided by the slot count — so growing the primary by 40% shrank every ring
slot from 98 px to 52 px, and the resolver then had to re-place things, moving the primary
too. A slider that makes buttons smaller when you turn it up is not a tuning problem; it is a
sign that sizing is entangled with layout.

Consequences to enforce:

- Built-ins are mutually **disjoint by construction**. Assert it in a selftest across a spread
  of window sizes and aspect ratios, including extreme ones.
- Built-ins are the **fallback** the resolver starts from, so they must always be valid.
- A "reset" is `Table::default()` — no stored placements — and is therefore guaranteed sane.
- Everything the player can change (`cx`, `cy`, `pct`) is a *layer* over that function.

## 3. The invariant

> **A resolved layout contains no control that overlaps an obstacle, and every honoured
> placement is one the player actually asked for.**

The first clause is a correctness property, not a cosmetic one, and the reason is in
[input-and-hud.md](input-and-hud.md#1-the-ordered-hit-test-chain): **all host chrome is
hit-tested before the game button list.** A control sitting under host chrome is not "partly
covered" — it is *dead*, and the tap silently does the chrome's job instead. The player
experiences it as "this button stopped working".

## 4. The fixpoint resolver

```rust
fn resolve_with(win: Size, stored: &Table, obstacles: &[Rect]) -> HashMap<CtrlId, Rect> {
    let mut out = builtins(win);                  // always mutually disjoint
    for _pass in 0..ids.len() {                   // bounded; see the termination argument
        let mut moved = false;
        for id in ids {                           // PRIORITY ORDER
            let Some(p) = stored.get(id) else { continue };
            let cand = rect_from(p, win, builtin_size(id, win));
            if out[id] == cand { continue; }
            if accepts(&cand, id, &out, obstacles) {
                out.insert(id, cand);
                moved = true;
            }
        }
        if !moved { break; }
    }
    out
}
```

**★ (A) It must run to a fixpoint, not once.** A single pass produces **ghost seats**: control
A is stored at position P; control B, earlier in the list, currently sits at P *only because
it has not been moved to its own stored position yet*. B blocks A, then B moves away, and A is
left at its built-in for the rest of the session. The player drags a control, it snaps back,
they drag it again, it stays — because the second attempt ran against a settled table.

**Termination.** Each pass either moves at least one control to its stored placement or stops.
A control moves to its stored placement at most once (afterwards `out[id] == cand` and it is
skipped). So there are at most `ids.len()` productive passes, and the loop is bounded by that.
Write this argument in a comment; the next reader will otherwise "optimise" the loop away.

**Priority order matters and is a design decision.** Earlier ids win ties. Put the controls the
player is most likely to care about first — the primary action, the direction control — and
put decorative or rarely-moved ones last.

## 5. `accepts`: loose against controls, strict against obstacles

```rust
fn accepts(cand: &Rect, id: CtrlId, placed: &Map<CtrlId, Rect>, obstacles: &[Rect]) -> bool {
    if !cand.inside(window) { return false; }

    // control vs control: LOOSE - test a SHRUNK candidate, so neighbours may kiss
    let tol   = cand.min_side() / 5;
    let shrunk = cand.shrunk(tol);
    for (oid, r) in placed { if *oid != id && shrunk.intersects(r) { return false; } }

    // control vs obstacle: STRICT - test a GROWN candidate
    let grown = cand.grown(pad_of(id));
    for o in obstacles { if grown.intersects(o) { return false; } }

    true
}
```

**★ (A) The asymmetry is deliberate and it is the most important thing in this file.**

| pair | test | why |
|---|---|---|
| control vs **control** | candidate **shrunk** by `min_side / 5` | Overlapping two of your own controls is a **cosmetic choice the player is allowed to make**. Players explicitly ask to pack controls tighter than a strict test allows. The worst case is a slightly ambiguous edge, and the hit-test chain resolves it deterministically anyway. |
| control vs **obstacle** | candidate **grown** by a per-control pad | Overlapping an obstacle means the control **silently does something else**, because obstacles are hit-tested first (§3). There is no version of this the player wanted. |

Note that `shrunk` uses the *candidate's* size, so a large control gets a proportionally larger
tolerance. A fixed pixel tolerance behaves very differently for a 40 px toggle and a 160 px
action disc.

## 6. The three obstacle families

**(A)** Obstacles are rectangles the resolver refuses to let a control touch. Enumerate them
from three distinct sources — missing any one of them produces a different, confusing bug.

**1. The guest's own HUD widgets.** Things the *game* draws: its status band, its target box,
its message area. You do not control these and they are painted after your blit and before your
overlay, so a control under one is invisible.
*Locate them by RE, and prefer to* **poke the guest's widget out of the way** *rather than
routing around it* — see
[guest-widgets.md](guest-widgets.md#6-poke-the-guests-widget-out-of-the-way).

**2. Your own menu chrome.** The settings entry point, the keypad toggle, the pull-out keypad
panel, your gauge block. These are hit-tested before the button list (or drawn after the
controls), so a control underneath is dead or hidden.

**3. The layout editor's own chrome.** The most forgotten family, and the one with the worst
failure mode: a control dragged fully under the editor's `RESET` button **can never be picked
up again**, and the tap intended to grab it wipes the mode's entire layout instead. Add the
editor's own buttons to the obstacle list *while the editor is open*.

Express obstacles as a function of the same window size the built-ins use, so they move
together.

## 7. The editor's predicate must BE the resolver

> **★ (A) The editor's "is this placement legal?" check is not a second function that looks
> like `accepts`. It RUNS THE REAL RESOLVER on a table containing the candidate and asks
> whether that control got the rectangle it asked for.**

```rust
fn would_accept_place(win, stored: &Table, id: CtrlId, p: Place, obstacles) -> bool {
    let mut probe = stored.clone();
    probe.set(id, p);
    let after = resolve_with(win, &probe, obstacles);

    // clause 1: did the edited control actually get what it asked for?
    if after[&id] != rect_from(&p, win, builtin_size(id, win)) { return false; }

    // clause 2: did the edit cost any OTHER control a placement it previously had?
    let before = resolve_with(win, stored, obstacles);
    for other in ids.iter().filter(|o| **o != id) {
        if stored.get(*other).is_some()
            && before[other] == stored_rect(other) && after[other] != stored_rect(other) {
            return false;
        }
    }
    true
}
```

**Why clause 1.** The reference port originally had two similar-looking functions. They
diverged. The editor accepted placements the resolver silently dropped, so the control snapped
back under the player's finger and a dead line accumulated in the layout file. A predicate that
*is* the resolver cannot diverge from it.

**Why clause 2** (added after clause 1 shipped). The resolver is **priority-ordered**, so an
earlier control growing into a later control's stored rect makes the later one fall back to its
built-in — which can be on the other side of the screen. The player resizes one button and an
unrelated button leaps away. **No edit may cost another control its honoured placement.**

Cost: `would_accept_place` runs two full resolves per candidate. At control counts of a few
dozen and human gesture rates this is free. Do not cache it.

## 8. Mirroring for left-handed play

One tap producing a mirrored layout is a small feature with a nasty implementation.

**★ (A) Two phases. Do not validate in flight.**

```
phase 1: mirror EVERY stored placement unvalidated   (cx -> 1.0 - cx, pct unchanged)
phase 2: for each control the resolver now refuses, search for the nearest legal seat
```

Validating as you mirror fails because **controls not yet mirrored still occupy their old
seats** and block the ones being moved. The intermediate table is temporarily invalid by
design; that is fine, because nothing renders from it.

The phase-2 search: try **upwards first, then sideways** — vertical space is usually free in a
landscape HUD while horizontal space is contested. **(B)** the reference searched 55 candidate
offsets in that order. Each candidate is tested with the full predicate from §7 including
clause 2, so a repair may not cost a higher-priority control its placement.

Mirroring the *built-ins* is not enough. A player who has customised nothing has an empty table
and mirroring is a no-op — which is correct, because the built-ins for a mirrored layout are a
different built-in function, or nothing at all. Decide which, and say so in the UI.

## 9. Persistence

One line per control, human-readable, in app-private storage:

```
# custom control layout - mode id cx cy pct
field ok    0.9120 0.8083 130
field dir   0.1400 0.7600 100
menu  k0    0.4000 0.6000 100
```

**(A) Parse defensively, per line.** A malformed line is **dropped**; every other line still
loads; the count of dropped lines is logged. The alternative — failing the whole file — throws
away a player's entire layout because one float has a stray character.

**Seed the process-global table in `App::new`, before the first layout pass.** If you load it
lazily, the default arrangement is drawn until something triggers a relayout, and the player
sees their customisation "reset itself" on every launch.

**★ Write on every finger-up, not on a DONE button.** Android kills backgrounded apps without
warning; a layout that only persists on explicit confirmation is a layout the player loses.
Which then forces the corresponding design for cancellation:

> **`CANCEL` restores an entry snapshot. It does not mean "we did not save".**

Take a full `Table` clone when the editor opens; `CANCEL` writes the snapshot back and
re-persists. This is more code than "don't save" and it is the only version that is correct
when the process dies mid-edit.

## 10. Editor gestures

**The editor carries its own mode**, never the app's live mode. The worker rewrites the app's
mode from guest state at any moment, and — critically — the settings entry point may not exist
in one of the modes at all, so **that mode's control set can only ever be arranged in
simulation**. That is what the editor's mode-switch buttons are for. Do not try to arrange a
mode by entering it.

**★ Selection is sticky. A touch that misses every control does NOT deselect.** Two fingers
never arrive in one event: the second finger is its own `Started`, and it is almost always on
empty screen. "Miss = deselect" therefore makes **pinch unreachable by construction** — the
finger you added to resize with is the finger that cleared the selection.

**Drag is delta-based**, `Drag { from: Point, centre0: Place }`, applying
`centre = centre0 + (now - from)`. Not grab-offset: with sticky selection a drag legitimately
starts *off* the control, and a grab-offset model teleports the control under the fingertip.

**Pinch pairs the just-landed finger with the DRAGGING finger** — or with the nearest one if
nothing is dragging. Do **not** pick the two lowest pointer ids: Android hands out the lowest
*free* id, so "lowest two" selects the **oldest** contacts, which is typically a thumb resting
on the bezel.

**Suppress every overlay layer that annotates LIVE input while the editor is open.** Position
badges for a physical pad ([gamepad-and-pad-hud.md](gamepad-and-pad-hud.md#14-one-trailing-pass-only-where-the-control-was-drawn)),
pressed-state highlights, held-direction indicators: the editor is about arranging *seats*, and an
annotation that describes what the control does right now is noise on top of the thing being
dragged — and it moves under the finger for reasons the player cannot connect to their gesture.
One gate at the badge pass (`&& editor.is_none()`), not a special case per badge family.

Editor chrome buttons, at minimum: mode switches, `FLIP` (mirror), `RESET`, `CANCEL`, `SAVE`.
Label them in ASCII — see [glyphs-and-text.md](glyphs-and-text.md); a Korean label on a 5x7
ASCII font renders as **blank space**, not as tofu, so it looks like a missing button.

## 11. ⚠ The winit-Android `Moved` quirk

> **`winit`'s Android backend emits `TouchPhase::Moved` for EVERY pointer in the `MotionEvent`,
> not only for the pointer that actually moved.**

Android's `ACTION_MOVE` carries the full pointer set, and the backend fans it out. So a
one-finger drag while a second finger rests still delivers two `Moved` events per frame, one of
which reports an unchanged position.

**What it breaks:** any gesture that *accumulates deltas*. A pinch implemented as
"`pct += k * (dist - last_dist)` on each `Moved`" applies the same delta twice per frame and
the control grows at double rate — or four times, with three fingers down.

**The fix: make the gesture absolute from an anchor captured at gesture start.**

```rust
struct Pinch { pct0: u32, dist0: f32 }          // captured on the second Started
// on any Moved involving either pinch pointer:
let d = distance(p_a, p_b);
place.pct = (pinch.pct0 as f32 * (d / pinch.dist0)).clamp(PCT_MIN, PCT_MAX) as u32;
```

Now a duplicated event is **idempotent**: it recomputes the same absolute value. Note this also
makes the pinch correct if events are dropped or coalesced, which is a second reason to prefer
it even on platforms without the quirk.

Drag can stay delta-based because its delta is `now - from` with `from` fixed at the gesture
start — also absolute in the same sense. The rule generalises: **anchor-relative, not
increment-accumulating.**

## 12. Symptom table

| Symptom | Cause | Section |
|---|---|---|
| A control snaps back under the finger; a dead line appears in the layout file | editor predicate is a lookalike, not the resolver | §7 |
| Moving one control makes an unrelated one leap across the screen | priority-ordered resolve with no clause 2 | §7 |
| A control refuses a position that is visibly empty | single-pass resolve leaving a ghost seat | §4 |
| A button stops responding after the player moves it | it overlaps host chrome; obstacle test too loose | §5, §6 |
| A control cannot be picked up in the editor and tapping it resets the layout | editor chrome missing from the obstacle list | §6 |
| Pinch resizes at double speed | `Moved` fan-out plus delta accumulation | §11 |
| Adding a second finger clears the selection | "miss = deselect" | §10 |
| A pinch resizes the wrong control | paired the two lowest pointer ids | §10 |
| A control shrinks slightly every time it is dragged | `pct` re-derived from a pixel rect | §1 |
| Tapping the visible control in the editor selects a different one | the editor's hit test does not apply the renderer's `shown` filter | §1 |
| Flipping a control-style setting back loses that control's arrangement | alternatives modelled as two id lists instead of one filtered list | §1 |
| Badges or press highlights move around while dragging in the editor | live-input annotation not suppressed for the editor | §10 |
| Making controls bigger makes some of them smaller | player sizing fed into built-in geometry | §2 |
| The player's layout is gone after the app was killed | saved only on DONE | §9 |
| Layout resets itself on launch, then appears after a rotation | table seeded after the first layout pass | §9 |
| Mirroring drops half the controls to their defaults | validated in flight instead of two-phase | §8 |
| An editor button's label is blank | non-ASCII label on the bitmap font | §10 |

## 13. Checklist

- [ ] Controls identified by seat, with stable on-disk keys and a round-trip selftest.
- [ ] Alternative controls are one id list plus a `shown` filter, applied by the renderer **and**
      by the editor's hit test.
- [ ] The editor suppresses every live-input annotation layer.
- [ ] `Place` is fraction + percent-of-built-in; `pct` carried, never re-derived from pixels.
- [ ] `builtins(win)` takes only the window size; a selftest asserts they are disjoint across a
      spread of sizes and aspects.
- [ ] The resolver runs to a fixpoint with a written termination argument.
- [ ] Control-vs-control is loose (shrunk candidate); control-vs-obstacle is strict (grown).
- [ ] All three obstacle families are populated, including the editor's own chrome.
- [ ] `would_accept_place` calls the real resolver and enforces both clauses.
- [ ] Mirror is two-phase and repairs never demote a higher-priority control.
- [ ] Layout persists on every finger-up; `CANCEL` restores an entry snapshot.
- [ ] Every gesture is anchor-relative, not increment-accumulating.
- [ ] Selection is sticky.
