# Reading, reimplementing and poking the guest

The host wants to draw things the game already knows — a minimap, gauges, an inventory bar —
and to change things the game already does. This file specifies both directions across the
`Emulator` boundary: how to read guest state safely, why you **reimplement** a guest widget
rather than screen-scraping it, how to prove your reimplementation is right, and the six
classes of host-to-guest write with the discipline each one needs.

Tags: **(A)** generic · **(B)** worked example from the reference port, re-derive it ·
**(C)** do not copy.

**Owned elsewhere:** making the game's *own* menus tappable — cursor bytes, synthesised confirms,
geometry derived from the frame buffer — is
[touch-driven-guest-ui.md](touch-driven-guest-ui.md), and §3 and §7 below hand off to it at the two
points where this file's rules invert.

## Contents

- [1. The read side](#1-the-read-side)
- [2. Pointer-hop validation](#2-pointer-hop-validation)
- [3. Do not screen-scrape. Reimplement. (For DATA widgets.)](#3-do-not-screen-scrape-reimplement-for-data-widgets)
- [4. Differential verification](#4-differential-verification)
- [5. The write side: read - compare - write - re-assert](#5-the-write-side-read---compare---write---re-assert)
- [6. Poke the guest's widget out of the way](#6-poke-the-guests-widget-out-of-the-way)
- [7. The eight classes of host-to-guest write](#7-the-eight-classes-of-host-to-guest-write)
- [8. Six rules for code patches](#8-six-rules-for-code-patches)
- [9. Host-owned state](#9-host-owned-state)
- [10. Driving the game's own routines](#10-driving-the-games-own-routines)
- [11. Symptom table](#11-symptom-table)
- [12. Checklist](#12-checklist)

---

## 1. The read side

Everything goes through the one closure shape
([thread-model.md](thread-model.md#8-the-read-closure)):

```rust
let read = |addr: u32, len: usize| emulator.read_memory(addr, len);
let bar  = model::quick_bar(&read);
```

**(A) Structure the guest-memory model as one module of pure functions**, each taking
`&impl Fn(u32, usize) -> Vec<u8>` and returning `Option<T>`. One `const` per offset, with the
RE provenance in a comment **above** it — the method that found it and the second, independent
derivation that confirmed it (`${CLAUDE_PLUGIN_ROOT}/shared/evidence-standard.md` §3).

```rust
// Unspent stat points. u16 LE.
// Derivation 1: the setter clamps at 276.
// Derivation 2 (game rules, no binary): level cap 70 -> 69 level-ups x 4 = 276.
// Derivation 3: every site materialising this offset is the stat window, the reset item,
// or the HUD badge - all semantically consistent.  HIGH / CONFIRMED 2026-07-30.
const STAT_POINTS: u32 = 0x18a;   // (B) - your title's offset will differ
```

This module is the one file a new port **rewrites from scratch**. Its *shape* is the reusable
part: pure functions, a map-backed test reader, one documented const per offset.

## 2. Pointer-hop validation

**(A) Validate every hop. A stale handle must yield `None`, not a render of garbage.**

Guest state is reached by chasing pointers: a static slot holds the god object, the god object
holds a map handle, the handle's payload starts some fixed number of bytes in. Every one of
those words can be stale — between maps, during a load, on the title screen, and for the whole
of a new-game sequence.

```rust
const HEAP_LO: u32 = 0x4000_0000;   // (B) read these OUT OF YOUR CORE, not out of this doc
const HEAP_HI: u32 = 0x5000_0000;

fn hop(read: &impl Fn(u32, usize) -> Vec<u8>, at: u32) -> Option<u32> {
    let p = rd_u32(read, at)?;
    (HEAP_LO..HEAP_HI).contains(&p).then_some(p)
}
```

Two more guards that are equally load-bearing:

- **Sanity-ceiling any derived size before allocating.** Width and height read out of a guest
  struct become a `vec![0; w * h]`. A stale struct yields absurd values and the host OOMs on
  the player's phone. **(B)** the reference capped map dimensions at 200 tiles a side, chosen
  from the largest real map plus a wide margin.
- **Check internal consistency.** A struct that stores both a stride and a width lets you
  assert `stride == width * bytes_per_pixel`. That single relation makes a shape-based search
  near-zero-false-positive, and it makes a stale read detectable.

Every one of these returns `None`, and `None` means **draw nothing this frame**, never
"draw the last good value forever" and never "draw zeros".

## 3. Do not screen-scrape. Reimplement. (For DATA widgets.)

**Scope, added in 0.2.0:** this section is about widgets whose content is *data the game holds* —
a minimap, gauges, an inventory bar. It is right for those and **wrong for input geometry and for
transient selection state**, which is the subject of
[touch-driven-guest-ui.md](touch-driven-guest-ui.md#13-why-geometry-comes-from-pixels-not-memory).
Read the split at the end of this section before applying the rule to a screen you want to make
tappable.

The history here is the lesson, and it is worth its full length because the wrong approach is
the obvious one.

**v1 — mirror the guest's own offscreen buffer (abandoned).** The game paints its minimap into
an offscreen framebuffer when the player opens the map window. To show it in the HUD
continuously, the host had to: open the map window behind a frozen screen so the guest would
repaint, stash the player's real input and replay it afterwards, close the window, debounce a
map-change signature so it did not repaint every frame, and handle every state in which the
window cannot be opened. About 250 lines of state machine. **Every single player-reported bug
in that feature came out of it** — the screen flickering, input eaten, the map showing the
previous area, the game stealing a keypress.

**v2 — reimplement (shipped).** Statically disassemble the guest's own paint routine. It turned
out to read from arrays that are **live at all times**, because they are the field's own
collision and event data — *"if they were ever stale the player would walk through walls"*.
Reimplement the identical pixel algorithm host-side. No window, no synthetic input, no freeze,
no state machine. The module is ~200 lines and has had no bug reports.

**(A) The general rule:**

> **If the data behind a guest widget is live in memory, reimplement the widget. Only scrape
> when the pixels are the only source of truth — and then question whether they really are.**

The test for "is it live" is not a guess. Find the arrays the guest's paint routine reads,
then ask *what else reads them*. If gameplay depends on them every frame, they are live by
construction and no repaint is needed.

Practical shape of a reimplementation:

- Put it in a **dependency-free module** so both the app and the headless probe can `include!`
  it (§4). This is not optional; it is what makes §4 possible.
- Reproduce the guest's algorithm exactly, including its rounding and its supersampling, not
  "something that looks the same".
- **Report** overlays such as a player marker as coordinates for the caller to draw, rather
  than stamping them into the buffer. Stamped decoration makes the pixel diff in §4
  meaningless.

### ★ Where the rule inverts: geometry and selection are pixel-first

The rule above says *"only scrape when the pixels are the only source of truth — and then question
whether they really are."* **(A) For two categories, the pixels really are the only source, the
questioning costs builds, and the correct pattern is the inverse of this section's.**

**1. Transient widget selection.** A module-drawn confirm box moves its highlight when the player
presses a direction — and a whole-record memory watcher sees **zero bytes change anywhere**. The
selection lives in module-local state the host cannot reach; the two rectangles in the frame *are*
the state. **(B)** measured in the reference port: ~1,040 pixels moved, not one record byte.

**2. Which layout a shared widget is drawing.** Two screens can share one cursor byte and draw
completely different grids — **(B)** 8×2 at pitch 19 versus 5×1 at pitch 29, with identical 17 px
cells — and the byte that looked like a paint flag read `0` on one screen and `12` on the other.
Memory cannot answer *"is this a row list or an item grid?"* at all. Three consecutive
memory-based fixes each cost a build and each was reported back as *"the wrong item focuses"*.

**The pattern that works, and it is the inverse of §3's:** derive the geometry **from the frame
buffer on the UI thread**, then cross-check the pixel-derived index against the live cursor byte
**on the worker**, and do nothing on a mismatch. Neither half is trusted alone.
[touch-driven-guest-ui.md](touch-driven-guest-ui.md#14-the-runtime-cross-check) specifies it.

A corollary worth its own line: **the keycaps and prompts the game draws itself are pixel-only
targets that need no anchor at all** — which is why they work on screens you have never reverse
engineered. See [touch-driven-guest-ui.md](touch-driven-guest-ui.md#7-stage-1-the-keys-the-game-already-printed).

## 4. Differential verification

> **★ (A) When you reimplement a guest routine, make the emulator run both and diff the
> output. A mismatch is a bug in the reimplementation, not a matter of opinion.**

The headless probe (`guest-re`) `include!`s the **shipping** renderer source — *a check that
exercises a copy proves nothing about what ships* — renders host-side from guest memory, then
fetches the guest's own output buffer and compares pixel by pixel.

Four mechanics that make the comparison honest:

1. **Quantise your output through the same colour round-trip the guest's buffer went through.**
   If the guest's buffer is RGB565 and yours is 24-bit RGB, convert yours down and back before
   comparing, or you will chase phantom one-LSB differences forever.
2. **Diff without decoration.** No player marker, no highlight, no border — those are the
   caller's job (§3).
3. **Print the first N differing pixels with coordinates and both colours**, not just a count.
   A count tells you it is broken; the coordinates tell you it is the last column, which tells
   you it is a rounding rule.
4. **Require zero, on at least two different game states.** "0.3% mismatch" is a bug you have
   not localised.

**★ CORRECTION carried from the reference port (A).** The differential check is **gated on an
explicit dump-frame list**. The invocation printed in that project's own top-level
documentation passed the check flag but no frame list, so it *could not print anything*. A
diagnostic that silently produces no output is indistinguishable from a diagnostic reporting no
problems. Two rules follow: gate diagnostics on an explicit frame list *and say so in the usage
text*, and treat "the check printed nothing" as a failure until you have seen it print a
non-trivial line.

**(B)** The reference recorded `0/5040` and `0/1224` mismatched pixels on two different maps —
and, importantly, at a frame where the guest's map window had **never been opened**, which is
the whole point: the host renderer works from live data, not from a repaint.

Make this a project exit criterion, not a one-off: any host reimplementation of a guest widget
ships only after a zero-mismatch run on two states.

## 5. The write side: read - compare - write - re-assert

> **★ (A) The discipline for every host poke that establishes PERSISTENT guest state:**
> **read the current bytes -> compare against the documented original -> write only on a
> mismatch -> re-assert on a slow cadence, forever.**

**The scope word is load-bearing.** 0.1.0 called this "the universal discipline for every host
poke" and it is not: a **transient UI cursor** write inverts three of the four properties below —
it is written once, must never be re-asserted, and has no documented original to compare against.
That is class 7 in §7, and its rules are stated there.

```rust
/// Returns the write to perform, or None if the guest already holds the desired value.
fn poke(read: &impl Fn(u32, usize) -> Vec<u8>, want: u8) -> Option<(u32, [u8; 1])> {
    let cur = rd_u8(read, ADDR)?;
    (cur != want).then_some((ADDR, [want]))
}
```

Four properties fall out of this shape, and together they delete an entire category of code:

- **Idempotent.** The worker can call it every frame, or every 30, and nothing depends on how
  often.
- **No ordering logic at all.** New game, continue, death, map change, the guest reinitialising
  its own state — all of it is handled by "re-assert forever". You never write a hook, never
  detect a transition, never sequence anything.
- **Self-cleaning expiry.** To turn a feature off, ask the helper for the **original** bytes
  back; it returns `None` when they are already there. So a build that carries the feature is
  **byte-identical in the guest** until a player actually enables it.
- **Safe on the wrong build.** A different version of the game does not hold the documented
  original, so nothing is written. See §8 rule 1.

Re-assert on a slow cadence — **(B)** every 30 frames in the reference port. Every frame is
wasted reads; too slow and the player sees the guest's own value flicker back after a state
change.

## 6. Poke the guest's widget out of the way

**(A)** When the host's HUD wants space the game's own HUD occupies, there are two options and
the non-obvious one is better.

- Move **your** chrome out of the game's way. Costs nothing, and looks worse forever.
- Move **the game's** widget. Costs an RE session, and then your layout is free.

Prefer the second, and derive the shift **from your own chrome's geometry** rather than
hardcoding it — then the game's widget follows automatically when you restyle. This is what the
packed geometry atomic in
[thread-model.md](thread-model.md#4-the-two-atomics) is for: the UI computes the offset from
its own layout, the worker writes it into the guest.

It also removes obstacles from the placement resolver's list
([layout-editor.md](layout-editor.md#6-the-three-obstacle-families)), which is a second,
larger win: fewer obstacles means more legal seats for the player's controls.

## 7. The eight classes of host-to-guest write

**(A)** Every poke the reference port shipped falls into one of eight shapes. Knowing the
taxonomy tells you what to verify. **(B)** the example sites are that title's; the *method*
column is yours. Classes 7 and 8 were added in 0.2.0 — 0.1.0 said "every poke falls into one of
six shapes" and the two it was missing are the two whose verification rules are *not* §5's.

| # | class | what it is | how you find the site | what can go wrong |
|---|---|---|---|---|
| 1 | **data byte force** | write a flag/level byte in the god object | enumerate every writer of the field and prove the set is closed (init, the item that sets it, the new-game reset) and that it is **not serialised** into the save; **and** establish the legal range, and whether the field is a scalar or an array element (see below) | if it *is* serialised, you have permanently altered the player's save; an out-of-range value opens a state the game cannot reach by itself |
| 2 | **relocated global write** | write a word in the RWPI static area | find the slot, then prove **all** referencing sites are read-only | a writer elsewhere overwrites you at an unpredictable time |
| 3 | **runtime record field poke** | change a field of a struct the guest built at load time (e.g. a UI frame's height) | trace the loader that fills the struct; identify the field by its use, not its position | ⚠ adjacent fields lie. In the reference, the field two bytes below the frame height was the **4bpp source row stride** — poking it corrupts the sprite |
| 4 | **immediate-operand rewrite** | change an `imm8` inside a Thumb ALU instruction | `xref` for every site materialising the constant; **both** Thumb idioms | see rule 2 below - the widget usually has several sites, and the block may exist twice |
| 5 | **branch-over-a-block** | replace one instruction with a branch past a range | disassemble the range; audit its stores | anything the block maintained is now frozen (see rule 6) |
| 6 | **arithmetic-instruction rewrite** | re-encode e.g. `adds rD, rN, #0` as `subs rD, rN, #k`, or change a divisor | find the formula, pick a constant you can change **size-neutrally** | flag liveness, and the immediate's bit width bounds the feature |
| 7 | **transient UI cursor write** | move the game's own menu/grid cursor so a tap lands where the player pointed | the byte the game's own arrow-key arm writes; see [touch-driven-guest-ui.md](touch-driven-guest-ui.md#2-the-translation-their-cursor-byte-their-key) | it has **no** documented original and no stable value; §5's discipline actively breaks it |
| 8 | **intercepting a guest write to STORAGE** | allow, refuse or redirect the guest's own record write | the record store you ported ([storage-and-config.md](storage-and-config.md#1-the-two-storage-backends)) | the guest usually ignores the failure code, so a refusal is silent to the game *and* to the player |

Classes 4-6 are code patches and are covered by `guest-re`'s patching reference for the
encoding side. The rules below are the host-side half.

### Class 1, the two obligations 0.1.0 left out

**(A) (a) Clamp your own writes — the guest's clamp may not be where you think.** A range check
frequently lives *only* inside the key-handler arms, while the commit/OK arm is a bare
load/increment/store with no bounds check at all. So a host write of an out-of-range value opens a
state the game cannot reach by itself, and it will not crash to tell you. **(B)** writing `6` into
a `0..=5` menu cursor and synthesising OK dispatched entry 7 of a 20-way function-pointer table and
opened a screen unreachable from the menu. Clamp in the pure geometry function **and again at the
write site** — two clamps, because the first is a UI-thread computation and the second is the last
thing before the poke.

**(A) (b) When the field is an ARRAY element, clamping is the wrong defence — REJECT.** If the
index comes from another live byte (a page number, a tab), an out-of-range index does not write an
illegal value: it writes a perfectly legal value **into a different field of the same record**.
Clamping the *value* does nothing about that. **(B)** in the reference port the per-page cell-cursor
array runs into another screen's three-byte cursor immediately past the game's own page count.
Full statement of the split: [touch-driven-guest-ui.md](touch-driven-guest-ui.md#15-bounding-the-write-clamp-a-scalar-reject-an-index).

### Class 7: a transient cursor inverts §5

**★ (A)** Four rules, each the opposite of the corresponding §5 property:

| §5 says | class 7 needs |
|---|---|
| re-assert forever | **write exactly once per tap.** Re-asserting fights the player's own arrow keys and the game's own screen transitions |
| compare against the documented original | **there is no original** — the legal value changes every frame. What replaces it is a **bounds check** plus a **cross-check against independent evidence** (the pixel-derived index must agree with the live byte) |
| a cached read is fine at this cadence | **re-read before every decision.** The game clears these bytes on its own transitions, and its key callback stamps one for every key — including the ones you synthesise |
| idempotent, so cadence does not matter | **the write is an event.** Order matters, and the write and the synthesised confirm are one transaction |

Everything about performing that transaction — the two-tap contract, the gate list, failing closed
on a bad read — is [touch-driven-guest-ui.md](touch-driven-guest-ui.md).

### Class 8: intercepting the guest's own save

**(A)** Four obligations, none of which resemble classes 1-6, because the thing you are modifying is
the *player's data* rather than the game's behaviour:

- **A refusal is silent.** The guest typically discards the store's failure code, so the game shows
  no error and the player learns nothing — until they notice their character stopped being saved.
- **A refusal diverges state.** Host-owned files that already committed now disagree with the
  guest's world, and that surfaces as items lost or duplicated rather than as a save error. Prefer
  **redirect** to refusal wherever you can prove where the write belongs.
- **A redirect changes the disk landing point ONLY** — never the guest's own index word, which the
  screen that owns it will keep using.
- **The whole authority rests on one scalar** (which slot the live character occupies), whose
  provenance must be an **observed load** and nothing else.

This is `save-integrity`'s subject in full:
[slot-identity.md](../../save-integrity/reference/slot-identity.md) and
[policy-and-diagnostics.md](../../save-integrity/reference/policy-and-diagnostics.md).

## 8. Six rules for code patches

**1. Verify the original bytes before writing.** The helper reads the site and returns `None`
unless it matches the documented original. A different build of the game is then left
completely alone rather than corrupted. This is what makes shipping a patch to strangers
defensible.

**2. Patch the whole widget or none of it.** A widget's geometry is usually built from several
immediates. **(B)** patching one of three x-immediates in a status band left the icons at the
far left and the frame where it was. Worse: **the block may exist twice.** A selector on a
state byte ran either an inline path A or, via a `bl` used **as a long branch**, a
byte-identical outlined path B with its own immediates — twelve sites in total for one widget.
Patching path A alone moved the widget for one case and not the other. Always `xref` the
constant image-wide, and always check whether the enclosing block has an outlined twin.

**3. Prefer size-neutral single-halfword rewrites.** Thumb-1 `adds`/`subs` with an immediate
are both 16 bits, so a signed shift re-encodes without changing instruction length and no
address downstream moves. Check **flag liveness** at every site: the rewritten instruction sets
flags, so verify another flag-setting instruction runs before any conditional that depends on
them.

**4. The encoding bounds the feature — enforce the bound in code.** Thumb-1 `imm3` is three
bits, so "reduce a requirement by N" tops out at 7. An immediate binding at `255 - margin`
bounds a pixel shift. A divisor rewrite must **refuse** multipliers that do not divide the
original exactly, rather than silently rounding — the reference's helper returns `None` for a
x3 multiplier over a divisor of 100 rather than shipping x3.03. Pin all of this with a selftest
over the whole legal range, including that out-of-range values are refused.

**5. Accept your own previous encoding as well as the pristine one.** Otherwise the re-assert
pass in §5 can only ever run once: the second pass sees your own bytes, fails the
original-bytes check, and gives up. Match either the pristine halfword or the opcode-family
mask of your own encoding.

```rust
if cur != ORIG && (cur & FAMILY_MASK) != FAMILY_BITS { return None; }  // not ours, leave it
```

**6. A branch-over patch freezes everything the block maintained.** Audit the stores inside the
skipped range: if any of them is game state rather than presentation, do not skip. **(B)** the
only non-stack stores in one skipped HUD range were blink counters in the static area — safe to
freeze, *but* the host could then no longer read those counters, because they had stopped
toggling. The host must switch to its own clock for anything the frozen code used to drive.

## 9. Host-owned state

Some features the host adds have no representation in the guest at all. Two rules, both
learned from real breakage:

**★ Store expiries as absolute wall-clock stamps, never as tick counters.** Android kills a
backgrounded app constantly. A counter decremented per frame stops when the process dies and
resumes where it left off, so a "30 minute" effect lasts as long as the player keeps the app
foregrounded — which is not what anyone means. Persist absolute Unix milliseconds, compare
against `now`, and accept that a clock change can expire something early. That is the correct
trade.

**★ Store identities with a validity stamp, not raw guest bytes.** A host feature that
remembers "the player had X bound to slot 2" must store *what X is* plus *the condition under
which that binding is still meaningful*, and drop the entry when the condition no longer holds.
Storing the raw guest bytes of the slot and writing them back later re-creates an item the
player no longer owns, or a stale index into a re-ordered inventory.

And where the game has its own routine for making the change, **restore through the game's own
routine** rather than writing the bytes. It updates whatever else it updates.

## 10. Driving the game's own routines

For anything the game already implements — saving, binding an item, loading a record — call the
game's code rather than reimplementing it.

The strongest case is persistence. Save records are commonly **encrypted and checksummed**.
Reimplementing that cipher host-side risks a one-byte-wrong corrupted save, which is the single
worst bug a port can ship. Driving the game's own serialise/write primitives is exactly as
correct as the game is. *(Extracted decryption keys are out of scope for this plugin; if a
title's records are encrypted, drive its primitives.)*

Two structural facts to plan around:

- **The game's own call sites may not be callable.** A jump-table arm is not a function: it
  shares its parent's stack frame and exits through the parent's epilogue, so entering it
  directly pops garbage into `pc`. What *is* callable is the ordinary functions it is built
  from — verify each candidate has its own prologue/epilogue and sets up its own static base.
  Full procedure in `guest-re`.
- **A chain is a polled state machine.** Step N+1's arguments come from step N's *return*, and
  guest calls land on the next tick, so the chain is a `advance(result) -> Next` function polled
  once per frame. Keep it pure and dependency-free so a selftest can drive it through every
  path, including every timeout.

Validation discipline for such a chain, all **(A)**:

- Plausibility-window every pointer the guest hands back.
- **A measured length is a BAND, not a constant.** The reference asserted the payload length it
  had observed in an archived save; the assertion fired on the very first real save, because
  serialised length is content-dependent. Use a wide `MIN..MAX` band.
- Timeout every step, and reset the timer per step.
- Read record names out of the guest's own tables rather than hardcoding strings.
- Gate on the game's own precondition byte, not on a rule you invented.
- **Know what leaks.** The game's primitives allocate, and if the original only ran them on
  demand, nothing frees. **(B)** roughly 2.9 KB per save in the reference port — which is why
  its autosave interval is *minutes*, not seconds. Measure yours and pick the cadence from the
  measurement.

## 11. Symptom table

| Symptom | Cause |
|---|---|
| Host widget renders garbage on the title screen or between maps | a pointer hop not validated against the heap window (§2) |
| Host OOMs on some maps | a derived size used without a sanity ceiling (§2) |
| Reimplemented widget differs from the guest's by a thin edge | rounding or supersampling rule not reproduced exactly (§3) |
| Pixel diff reports noise everywhere | comparing 24-bit against RGB565 without the round-trip, or decoration stamped into the buffer (§4) |
| Pixel diff prints nothing at all | the check is gated on a dump-frame list you did not pass (§4) |
| A poke works once and then stops after a map change | not re-asserted (§5) |
| A poke works on the first run and never again | the helper does not accept its own previous encoding (§8 rule 5) |
| Half a widget moves | not all immediate sites patched, or an outlined twin block missed (§8 rule 2) |
| A patched build corrupts a different version of the game | no read-verify before write (§8 rule 1) |
| A sprite tears after poking a struct field | poked the stride, not the height (§7 class 3) |
| A HUD indicator sticks on or off after a block-skip patch | the skipped block drove it; host must use its own clock (§8 rule 6) |
| A timed host effect never expires, or lasts far too long | expiry stored as a tick counter (§9) |
| A restored binding refers to something the player no longer has | raw guest bytes stored instead of an identity + validity stamp (§9) |
| A length assertion fires on the first real use | a measured value pinned as an invariant (§10) |
| A tap focuses the wrong cell in one window and the right one in another | grid geometry taken from memory; two screens share a cursor byte and draw different pitches (§3) |
| A menu cursor the host writes keeps snapping back, or fights the player's arrow keys | a class-7 transient cursor re-asserted like persistent state (§5, §7) |
| A host-written cursor opens a screen the menu cannot reach | the guest's clamp lives only in its key arms; the host did not clamp its own write (§7 class 1) |
| A host-written cursor corrupts an unrelated field | an out-of-range **array index** clamped instead of rejected (§7 class 1) |
| Saving silently stops for a whole session; items vanish or duplicate | a class-8 storage refusal the guest never reported (§7 class 8) |

## 12. Checklist

- [ ] All guest reads go through one closure; every parser is pure and returns `Option`.
- [ ] Every offset const carries its RE method and a second independent derivation.
- [ ] Every pointer hop is window-validated; every derived size has a sanity ceiling.
- [ ] Any host reimplementation of a guest widget lives in a dependency-free module the probe
      `include!`s, and has a **zero-mismatch** differential run on two game states.
- [ ] Every **persistent-state** poke is read-compare-write and re-asserted on a slow cadence; every
      **transient cursor** poke is written once, re-read before each decision, bounds-checked and
      cross-checked (§7 classes 1 and 7).
- [ ] Any interception of the guest's own record writes follows `save-integrity`, and prefers
      redirect to a silent refusal (§7 class 8).
- [ ] Every code patch verifies the original bytes, covers the whole widget, checks for an
      outlined twin, is size-neutral, bounds itself by its encoding, and accepts its own prior
      encoding.
- [ ] Host-owned expiries are absolute wall-clock; host-owned bindings are identities with a
      validity stamp.
- [ ] Guest-call chains are polled state machines with per-step timeouts and band validation.
