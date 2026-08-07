# Touch input, key edges, and the HUD

A feature-phone guest has a keypad. Your player has a slab of glass. This file specifies the
translation: how a touch becomes a keypress, how two fingers and a lifted thumb do not produce
a stuck key, and how the HUD that carries all of it is laid out.

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
2  full-screen modal state  -- e.g. "a guest map/menu is open": any tap means one key
3  settings panel           -- while shown, taps inside it are settings, outside it closes
4  the settings entry point (gear)
5  pull-out keypad toggle
6  host toggle controls     -- one entry per host-owned toggle
7  host action controls     -- one entry per host-owned action (page, quick-save, ...)
8  host data plates         -- e.g. quick-slot plates, whose meaning the WORKER resolves
9  large informational panels, as TAP ZONES (see §4)
10 the game button list     <-- the ONLY path that produces a held key
11 the joystick zone        <-- catch-all for the remaining area
```

Three properties of this order, each of which is load-bearing:

- **The editor is first, in every phase.** It must outrank the modal state (the worker can set
  that flag mid-drag, and it would eat the tap) and the settings panel ("tap outside closes"
  would end the editor on the first drag that started outside a control).
- **Everything host-owned is tested before the game button list.** That is what makes
  "a control overlaps host chrome" a *silent behaviour change* rather than a cosmetic one — the
  chrome wins and the control is dead. §[layout-editor.md](layout-editor.md#the-invariant)
  turns that into the resolver's strict-obstacle rule.
- **The joystick is last, and it is a *zone*, not a control.** It has no rectangle to miss.

Write the chain as one function returning an enum, not as a nested `if` in the event handler.
You will need to run it from the layout editor's own hit test too.

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

**(A)** Both guards are required and they are not symmetric conveniences:

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

**(A) A feature-phone RPG latches a walk direction on keydown.** It does not integrate an
analog vector. That single fact determines the whole design and it is wrong to design around
the *input device* instead of around the *guest*.

**Four-way, dominant-axis, exactly one key held at a time.**

```
DEAD_ENGAGE = 0.30   // fraction of radius before any direction engages
DEAD_KEEP   = 0.15   // hysteresis: stay engaged until you fall below this
TURN_RATIO  = 1.2    // the new axis must beat the old by this factor to take over
```

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

**Why not eight-way with two keys held (A).** It is the obvious design and it is wrong for a
latched-direction guest: holding UP+RIGHT makes the guest latch whichever arrived last and
ignore the other, so a diagonal produces the same motion as one cardinal but with an extra
key stuck down. If your guest genuinely integrates a vector, ignore this section — but prove
it with a probe run before you do, not by reading the input handler.

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

One helper: `release_all_held()` iterating `touches` through `update_touch(id, None)`, then
clearing `ui_touches` and `slide_touches`.

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

## 8. Auto-repeat lives on the worker

**(A)** The UI sends edges; the worker synthesises repeats. Roughly every 100 ms **(B)** the
worker re-sends keydown for each key it believes is held.

Put it on the worker, not the UI, because:

- the guest's notion of "held" must match the worker's tick cadence, not the display's;
- the paused gate can clear the held set in one place;
- the UI thread has no business having a timer.

The consequence is §6: the worker's held set is authoritative, so the UI must always send the
keyup. A key the UI forgets is a key that repeats until the process dies.

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

Everything here lifts unchanged between titles: `Rect`, padding helpers, the blend primitives,
the ring/disc cluster geometry, the 5x7 ASCII bitmap font
([glyphs-and-text.md](glyphs-and-text.md)).

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

## 12. Symptom table

| Symptom | Cause | Fix |
|---|---|---|
| A control "clicks itself" while the player steers | any moving pointer may acquire a key | `slide_touches`: only a pointer that started on a control may slide (§2) |
| A key sticks down forever after backgrounding | worker auto-repeat with no release on suspend | release-all on suspend, focus loss and editor entry (§6) |
| Character stops when changing direction | keydown(new) sent before keyup(old) | reverse the order (§5) |
| Two fingers on one button, lifting one stops movement | unconditional keyup | keyup only when no other pointer holds the key (§3) |
| Diagonal drag makes the character jitter | no turn ratio between axes | `TURN_RATIO` (§5) |
| Thumb resting near centre chatters | one dead zone | two: `DEAD_ENGAGE` > `DEAD_KEEP` (§5) |
| A confirm button alternates between two behaviours | decided on both DOWN and UP | latch the DOWN decision (§7) |
| A big panel fires while the player swipes over it | hit test is "inside", not "tap" | tap-slop zone (§4) |
| Taps land on the game while a settings panel is open | panel is below the game button list in the chain | reorder (§1) |
| The editor ends itself on the first drag | settings "tap outside closes" runs before the editor branch | editor first, in every phase (§1) |
| A button's label is blank | the ASCII font has no glyph for it | [glyphs-and-text.md](glyphs-and-text.md) |

## 13. Checklist

- [ ] The hit-test chain is one ordered function, editor first, joystick last.
- [ ] Three touch-identity sets exist and `slide_touches` gates sliding.
- [ ] `update_touch` is the only sender of key edges, with both guards.
- [ ] Direction changes emit keyup(old) before keydown(new).
- [ ] Two dead zones and a turn ratio on the joystick; centre fixed at touch-down.
- [ ] `release_all_held()` is called on suspend, focus loss, editor entry and family change.
- [ ] Every affordance whose action changes its own hit test latches its DOWN decision.
- [ ] Auto-repeat is on the worker; the UI always sends the keyup.
- [ ] Layout families are chosen from `(orientation, immersive)` and only immersive is placeable.
- [ ] Every title-specific HUD constant has its RE method in a comment; host inventions are
      labelled as inventions.
