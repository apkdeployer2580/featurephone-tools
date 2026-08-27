# Making a keypad-driven guest UI touch-driven without patching the guest

The game's menus, grids, confirms and message boxes were built for a d-pad and an OK key; your
player wants to tap them. This file is the translation layer: a tap becomes **"write the game's own
cursor byte" + "synthesise the game's own confirm key"**, geometry comes from the **frame buffer**
rather than from memory, and a gate list keeps a mis-read tap from committing an irreversible
action. No guest code is patched and no menu logic is reimplemented.

Tags: **(A)** generic · **(B)** worked example from the reference port, re-derive it ·
**(C)** do not copy. `⚠` marks a claim nothing has run against yet.

Owned elsewhere: host-drawn controls and the ordered hit-test chain in
[input-and-hud.md](input-and-hud.md); host windows and modals in
[host-windows.md](host-windows.md); how the coarse mode is decided in
[mode-resolution.md](mode-resolution.md); reimplementing a guest widget host-side in
[guest-widgets.md](guest-widgets.md).

## Contents

- [1. First prove the guest cannot see the touch](#1-first-prove-the-guest-cannot-see-the-touch)
- [2. The translation: their cursor byte, their key](#2-the-translation-their-cursor-byte-their-key)
- [3. The two-tap contract](#3-the-two-tap-contract)
- [4. Never cache a cursor; re-read at the write](#4-never-cache-a-cursor-re-read-at-the-write)
- [5. Branch in the guest's own order](#5-branch-in-the-guests-own-order)
- [6. Stage the work by cost and reach](#6-stage-the-work-by-cost-and-reach)
- [7. Stage 1: the keys the game already printed](#7-stage-1-the-keys-the-game-already-printed)
- [8. Finding printed keycaps](#8-finding-printed-keycaps)
- [9. Six rules that keep a pixel detector honest](#9-six-rules-that-keep-a-pixel-detector-honest)
- [10. Occlusion, and the element that is always missing](#10-occlusion-and-the-element-that-is-always-missing)
- [11. Panel geometry is resolution-independent](#11-panel-geometry-is-resolution-independent)
- [12. Variable-length widgets and the derived maximum](#12-variable-length-widgets-and-the-derived-maximum)
- [13. Why geometry comes from pixels, not memory](#13-why-geometry-comes-from-pixels-not-memory)
- [14. The runtime cross-check](#14-the-runtime-cross-check)
- [15. Bounding the write: clamp a scalar, REJECT an index](#15-bounding-the-write-clamp-a-scalar-reject-an-index)
- [16. The gate list](#16-the-gate-list)
- [17. "App state N" means two different things](#17-app-state-n-means-two-different-things)
- [18. Fail closed by evidence](#18-fail-closed-by-evidence)
- [19. Mode precedence: know which way yours errs](#19-mode-precedence-know-which-way-yours-errs)
- [20. Padding is priced by the cost of a false positive](#20-padding-is-priced-by-the-cost-of-a-false-positive)
- [21. Release discipline for a derived target](#21-release-discipline-for-a-derived-target)
- [22. Two threads, two verdict lines](#22-two-threads-two-verdict-lines)
- [23. Closing offsets the probe cannot reach](#23-closing-offsets-the-probe-cannot-reach)
- [24. Screens to refuse, and saying so in code](#24-screens-to-refuse-and-saying-so-in-code)
- [25. Selftesting geometry](#25-selftesting-geometry)
- [26. Anti-pattern gallery](#26-anti-pattern-gallery)
- [27. Symptom table](#27-symptom-table)
- [28. Checklist](#28-checklist)

---

## 1. First prove the guest cannot see the touch

**(A)** Before designing anything, settle whether a coordinate can reach the guest. Two greps, and
they are independent derivations: **(1)** disassemble the guest's single event entry point and list
the event *type ids* it forwards — everything else it drops; **(2)** read the emulator core's event
enum, because with no pointer variant no coordinate can be delivered even if the guest wanted one.

**(B)** In the reference port the entry point accepted exactly two ids — key press and key repeat —
and the core's enum held only redraw, key down, key up, key repeat, timer and notify.
`HIGH / CONFIRMED`, two routes.

This is load-bearing, not trivia. A port that already ships tap-to-move will *believe* the guest
receives coordinates, because tapping the world visibly moves the character, and that belief
survives for sessions. The host translates; the guest only ever sees keys.

## 2. The translation: their cursor byte, their key

**(A)** With coordinates unavailable, a tap is exactly two host actions: **write the game's own
focus/cursor byte for the screen that is showing**, then **synthesise the key the handset would
have pressed**. Everything after that — what the row opens, whether the cell holds anything, which
popup appears — is the game's own code on its own stack, so **the host invents no behaviour and the
feature cannot reach an illegal state on its own.**

**(A) Prove the two halves separately**, in this order, because each fails differently:

- **Write, send zero keys.** **(B)** Poking a six-row menu cursor to `4` moved the highlight with no
  key sent: per-row selection-ink counts on the next painted frame were `[0,0,0,0,119,0]`.
  Reproduced with `3` → a different row.
- **Then one synthetic confirm.** The screen opened. Two values, two screens. `HIGH / CONFIRMED`.

**★ (A) A host-synthesised key is an immediate down+up straight to the guest and must never enter
the held-key / auto-repeat map.** That map is for keys a finger is holding; nothing is holding this
one, so a synthetic press inside it repeats forever with nobody pressing anything. The map itself is
[input-and-hud.md §8](input-and-hud.md#8-auto-repeat-lives-on-the-worker).

## 3. The two-tap contract

**★ (A) One tap is NEVER an activation.**

| tap | writes the cursor byte | sends a key |
|---|---|---|
| 1st — target not focused | yes | **no** |
| 2nd — target already focused | **no** | yes |

**(A) Decide which case you are in by reading the cursor byte back from the guest, never by
remembering what you wrote.** The game moves that byte itself on arrows, on page flips and on its own
screen transitions, so your memory of the last write is a guess and the read is a fact.

> **REFUTED.** *"A tap writes the cursor byte and synthesises confirm in one gesture."* That shipped.
> It is exactly the guest's own "OK pressed twice", so the focus step does not exist: the player
> cannot look at what they picked before acting, and on an item grid the single tap lands straight on
> a destructive action list whose first row uses or drops the item. Rejected on sight — *tapping
> should focus; tapping the focused item again is what opens the detail list, and every screen with a
> focus-then-dropdown structure should behave that way.* `HIGH / CONFIRMED on device`, both ways.

Apply the contract to every focus-then-dropdown screen at once. Players generalise from the first.

## 4. Never cache a cursor; re-read at the write

**(A)** Nothing about the guest's UI cursor state may be cached between the touch and the write.
Re-read screen id, page/tab byte, cursor and row count **inside the worker, on every tap**. A cached
screen id makes the router write the right byte on the wrong screen — and on a shared cursor array
(§15) that is a write into an unrelated record field.

**(B)** The method is a static sweep for writers of those bytes: it found four distinct guest sites
that zero cursor bytes on screen entry or exit, plus a key-callback tail that stamps an "a key
happened" byte for *every* key, synthesised ones included. `HIGH / CONFIRMED`.

**(A)** This is a **seventh** shape of host-to-guest write and it inverts the persistent-poke
discipline in
[guest-widgets.md §5](guest-widgets.md#5-the-write-side-read---compare---write---re-assert):
written once per tap, **never re-asserted** (re-assertion fights the player's own arrow keys), and
with no "documented original" to compare against because the legal value changes every frame. What
replaces the original-bytes check is a bounds check (§15) plus a cross-check (§14).

## 5. Branch in the guest's own order

**(A)** Branch your tap router in the same order the guest's own key callback branches. If the guest
tests an "an overlay owns the keypad" flag *before* it reads any window id, so must you.

**(B)** That callback reads the overlay flag first and, when non-zero, dispatches to an overlay
router without ever reading the window id. Confirmed independently at runtime: the main menu and the
inventory both report the window id meaning *field, no window open* while being real screens.
`HIGH / CONFIRMED`, two routes.

A "which screen is up?" predicate built on the window id alone is therefore blind to whole families
of screens, and the failure reads as *"touch doesn't work on this screen"* rather than as a wrong
gate. Copying the guest's branch order gets it right for free; resolving the coarse mode is
[mode-resolution.md](mode-resolution.md)'s subject.

## 6. Stage the work by cost and reach

**(A)** Do the feasibility study as a separate deliverable answering exactly three questions — *can a
tap be turned into an index, can the host write the highlight, does a synthetic confirm commit* — and
stage the implementation cheapest and widest first.

| stage | what | RE cost | reach |
|---|---|---|---|
| 1 | the keys the game already printed (§7) | none | every screen, including ones never reversed |
| 2 | one measured screen end to end | one cursor byte | one screen |
| 3 | per-screen cursor tables | one byte per screen | the screens you measure |
| 4 | screens sharing one list widget | one measurement | a whole family at once |
| 5 | irreversible screens | — | **decline** (§24) |

**(A) Stage 4 is the leverage.** If the shared widget publishes its own live **row count**, geometry
is *computed*, not tabulated, and one measurement covers every screen using the widget. **(B)** Its
axis byte selected the builder: one value meant a vertical row list where DOWN moves the cursor,
another a two-option confirm where RIGHT moves it and DOWN does nothing — and the same count byte
meant *rows* in the first case and *columns* in the second. Refuse any axis value you have not
watched move. `HIGH / CONFIRMED`.

**(A)** Let later sessions annotate the study with what it failed to predict. **(B)** All of the
reference study's numbers re-measured correctly, and its most valuable section became the header
listing the two things it did *not* predict: that item grids have more than one shape, and that
memory cannot distinguish one grid family from another at all (§13).

## 7. Stage 1: the keys the game already printed

**(A)** Feature-phone games print the keys they want pressed — a header `«1» TITLE «3»`, a page strip
`«7» <name> «9»`, a footer `OK : <verb>`, a centred `OK` in a message box. Hit targets on that
printed text need **zero RE, zero memory writes and zero anchors**, so they work on screens you have
never reverse-engineered and on every screen added later.

**(B)** Shipped as a frame scan for the badge glyph ink; because it needs no anchor it also covered
the shop, warehouse and cash-shop screens, which had no measured cursor at all. Device-verified two
ways: computed badge positions matched a screenshot within 1 px, and the four keys it sends are the
same four an existing on-screen keypad had been sending for weeks — so the stage adds no new key
risk, only new targets. `HIGH / CONFIRMED`.

Do this before any cursor-writing stage. It converts the exact keys players complain about into
touch targets for about a day's work.

## 8. Finding printed keycaps

**(A) Locate printed keycaps by ink colour inside narrow, fixed columns — never by assumed
position.** Some sit at a constant offset on every screen; others move per screen; some screens draw
none, and an assumed position gives you a phantom button there.

**(B)** Measured across five screens and two logical framebuffer sizes: the two tab badges sat at a
constant `dy` everywhere, the page badges' `dy` differed by screen (two distinct values), and two
screens drew no page badge at all. The `x` columns were 11 px wide and invariant, so a downward scan
of just those two columns finds whatever is there.

**★ (A) When two things in the frame share a nearly identical colour, add a positional constraint
rather than loosening the colour match.** **(B)** The header title's ink differed from the badge ink
by 8 in a single channel; restricting the scan to those two 11-px columns removed the ambiguity
outright, and a selftest pins that the title ink is *not* accepted. Colour tolerance is the wrong
dial — widening it claims text, narrowing it misses antialiased edges. Position is exact and free.
**(C)** The ink triplets are one title's palette.

## 9. Six rules that keep a pixel detector honest

**(A)** Every detector fails closed, with the measurement each guard came from in a comment beside it:

| # | guard | the failure it closes |
|---|---|---|
| 1 | reject a frame whose length is not `width × height` | a rotation hands you a buffer of the previous size |
| 2 | require a minimum ink count | a few stray pixels are not a badge |
| 3 | require the band's height in a **measured** range | a too-tall band is some other widget |
| 4 | classify only inside measured bands; **ignore ink between them** | art between bands becomes a button |
| 5 | clip the grown hit target inside its container | a padded target hangs outside the panel |
| 6 | discriminate rivals on a **shape invariant**, and name the rival in the guard | a maintainer loosens the bound to "fix" a miss |

**(B) Three shipped instances of rule 6, each naming what it excludes:** a spinner triangle accepted
only at run length 5..8, because a selected confirm button's background shares its colour and makes a
52 px run; a message-box `OK` accepted only when its white text is ≤ 40 px wide and centred, because
a two-button dialog uses the same grey band with a caption spanning 115 px across both buttons; a
selected confirm button matched on row **span and fill ratio** rather than longest run, because the
label drawn on top fragments the box (longest run 16 px of a 40 px button).

A detector that answers when it should not manufactures a button in the middle of the game's screen,
and the key it sends is destructive on some screen you did not think about.

## 10. Occlusion, and the element that is always missing

**(A) Game art occludes the game's own chrome. Match a box on its un-occluded edge and require only
existence plus alignment from the others.** **(B)** The cell-cursor box's top edge is a full 15 px
run while its bottom shortens to 10 px under an item-count label, so the detector takes the width
from the top and pairs the bottom on its left endpoint only. Demanding four full edges fails exactly
on the cells that contain something.

**★ (A) When scanning a repeated pattern for its pitch, the selected element is the one that will be
MISSING** — its frame is under the highlight. Take the pitch as the **minimum gap** between runs and
require every other gap to be a multiple of it (within 1 px); never require uniformity. **(B)** With
the cursor on cell 3 the frame-run starts were `[22, 51, 80, 138]`; the 58 gap is exactly two
pitches, and a uniformity check rejected that window outright — i.e. it fails precisely on frames
where a cursor exists, which is every frame you care about.

**(A) Separate row bands by at least one cell height**, or a cell's own bottom border counts as a new
row. **(B)** Naive "next row with runs" detection reported a two-row grid as 3 rows at vertical pitch
16; requiring `≥ (cell pitch − 2)` fixed it. A phantom row shifts every index below it.

## 11. Panel geometry is resolution-independent

**(A)** A guest panel is usually a **fixed logical size, centred**, because the guest re-anchors its
chrome to the framebuffer each time. Derive the origin with two divisions from the *logical* size —
never from window pixels — and prove independence at three or more logical sizes.

**(B)** Measured at four logical sizes across two aspect families: the panel was `176×160` with
origin `((lw−176)/2, (lh−160)/2)` in all four, and the background ran uniformly across the full width
at the boundary rows, proving it was not clipped.

**★ The strong form of the proof (A):** compare per-column ink **counts**, not positions. **(B)** The
per-column badge ink counts were byte-identical across two logical sizes (`93/92/89/84`) — that is
"resolution independent" meaning *literally the same picture at the same panel coordinates*, rather
than approximately portable. Hardcoded window pixels break on every device and every rotation.

## 12. Variable-length widgets and the derived maximum

**(A)** For a widget whose length varies with its item count, fit the centring line through two or
more measured counts, confirm with a third, then **derive the maximum count from "does the block
still fit inside the container"** and refuse anything above it. **(B)** Three device samples at 2 and
3 rows sat exactly on `y0 = (SPAN − rows × pitch) / 2` with identical `x` and pitch.

> **REFUTED.** *"A maximum row count of 16 in the geometry table is a harmless upper bound."* It was
> an unmeasured number. At 9 rows the derived grid claims the **entire container height**, so any tap
> anywhere in the panel resolves to a row — and the game **pre-focuses row 0** when it opens that
> window, so a tap on the window frame synthesises confirm on a purchase dialog. Caught in
> adversarial review; the derived maximum plus an in-container assertion closes it twice over.
> `HIGH / INFERRED ⚠` — never observed on device; opening that window with more items than were
> measured would settle it.

**(A) An unmeasured maximum in a geometry table is an "any tap is a row" bug**, invisible until
someone opens a window bigger than the one you measured.

## 13. Why geometry comes from pixels, not memory

**★ (A) For input geometry and for transient widget selection, the game's pixels are the only
trustworthy source; memory is for cross-checking (§14).** This inverts the correct instinct for
*data* widgets — a minimap, a gauge — which
[guest-widgets.md §3](guest-widgets.md#3-do-not-screen-scrape-reimplement-for-data-widgets) covers. Treating pixels as
a last resort here costs builds: three consecutive memory-based guesses each cost one.

Three independent proofs, all `HIGH / CONFIRMED` on device:

1. **A selection can exist nowhere in memory.** **(B)** A module-drawn yes/no confirm moved its highlight on
   a RIGHT press with **zero bytes changing** anywhere in a 43 KB whole-record watcher, while about
   1,040 pixels moved. The selection lives in module-local state unreachable from memory.
2. **One cursor byte, two grid shapes.** **(B)** The standard bag drew 8 cells × 2 rows at pitch 19;
   a combine window drew 5 × 1 at pitch 29 — with **identical 17 px cells**, so the highlight alone
   cannot disambiguate. Applying bag geometry to the combine window read the 5th cell as index 14, an
   out-of-range cursor, after which the game drew no selection box and rendered another item's
   detail. The player's report: *"the middle panel also comes out looking like a bug"*. The
   fixed-geometry helper was deleted.
3. **The byte that looks like a paint flag is not one.** **(B)** It reads 0 on one screen and 12 on
   another, so memory cannot answer *"is this a row list or an item grid?"* at all.

**(A) If a widget's selection state exists nowhere in memory, the two rectangles in the frame ARE the
state:** tapping the selected one is confirm, tapping the other is the arrow key that walks over to
it, and you touch no guest memory at all. **(B)** The detector returns `(selected, other)` and the UI
maps them to confirm / LEFT-or-RIGHT by relative `x`. An apparently impossible screen becomes the
simplest one in the system: no writes, no anchors, two keys.

**(A) On a shared list widget, assume every byte is stale except one you have personally watched get
CLEARED.** **(B)** Entering a grid inside the same window dropped one byte `8 → 0` together with the
widget's rectangle descriptors, and leaving restored it — the only demonstrably fresh one. The cursor
and row count were observed holding the *previous* list's values on the next screen. Every stale byte
you trust becomes *"any tap resolved to row 0, which is the focused row, so the host fired confirm"*.

## 14. The runtime cross-check

**★ (A) Make the pixel-derived index and the live cursor byte check each other at runtime, and do
nothing when they disagree.** Differential verification is not only a test-time exit criterion; at
runtime it converts *"I read the grid wrong"* from an out-of-range write into a no-op. It is the only
rail available when **neither side is authoritative**: memory cannot say which window is drawn (§13),
pixels cannot say which byte to write. Agreement between two independent readings is the permission
to write. **(B)** Verified across six device frames spanning four windows — the derived index
equalled the live byte every time (`0, 0, 1, 1, 3, 11`). `HIGH / CONFIRMED`.

**(A)** The two halves live on different threads by construction — the frame buffer on the UI thread,
guest memory on the worker — so **pass the pixel-derived evidence with the raw point in one message**:

```rust
// UI -> worker, one variant, dumb by design (thread-model.md §3)
MenuHit { x: i32, y: i32, grid: Option<(Grid, u8 /* drawn cursor index */)> }
```

Never let the UI decide meaning; never let the worker guess geometry. Putting the whole decision on
one side is blind to either the screen or the pixels, and the bug reports become unfalsifiable from
that side's logs.

## 15. Bounding the write: clamp a scalar, REJECT an index

**(A) The guest's range clamp often lives ONLY inside its key-handling arms.** The commit/confirm arm
reads the byte and uses it with no bounds check — and that is the arm you are about to drive.

**(B)** Disassembly found clamps at two key-arm sites and none in the confirm arm, which is a bare
load / increment / store. Measured: writing `6` into a `0..5` cursor and synthesising confirm
dispatched entry 7 of a 20-way function-pointer table and opened a screen the menu itself cannot
reach. **No crash** — a reachable-but-undefined screen, exactly the class of bug that survives
testing. `HIGH / CONFIRMED`, static + runtime. So **clamp your own writes twice**: in the pure
geometry function, which must be *incapable* of answering outside the grid, and again at the site
that turns an index into a write.

**★ (A) When the cursor is an ARRAY indexed by another live byte — a page, a tab, a mode — the answer
is REJECT, not clamp.** An out-of-range index does not write a bad value to the right field; it
writes a **good value to the wrong field of the same record**. Clamping an array index looks like the
same defensive move and is the opposite of safe.

**(B)** Device-measured: the per-page cell cursor is one byte per page and pages 0/1/2 moved three
consecutive addresses; beyond the game's own page count the array runs into an unrelated cursor
triple belonging to another screen. The shipped code refuses when `page >= the game's own page
count`. This is the difference between *"the tap does nothing"* and *"the tap corrupts a neighbouring
field"*.

## 16. The gate list

**(A)** Chain order and the gate list are **two mechanisms and both are required**. Order decides who
claims a rectangle ([input-and-hud.md §1](input-and-hud.md#1-the-ordered-hit-test-chain)); gates
decide whether the panel router may act at all. Put the panel router at the very **end** of the chain
so every host-owned control keeps its pixels.

| gate | what happens without it |
|---|---|
| the guest's own action popup is open | the tap moves the cell cursor, then hands the popup confirm — and the popup's first row is the destructive action |
| a host keypad / settings / picker / editor / quantity overlay is open | the hole the feature itself creates, below |
| the coarse mode is not the one you measured | the write lands on a screen whose cursor you never established |
| no measured cursor exists for **this build** of the guest | a multi-build port writes another revision's offset ([build-keyed-anchors.md](../../guest-re/reference/build-keyed-anchors.md)) |
| the record is not live (§17) | the god object holds no character, so every derived field is garbage |
| a detector has claimed the container | a miss inside a claimed modal falls through to the next detector |
| the window is not on your **allow-list** for a shared widget | a stale row count makes the widget always look "in use", so the router writes list bytes into a text box or a map window |
| a "tap anywhere advances" affordance is gated on a *held* scene flag | below |

**★ (A) A detector that identifies a modal OWNS its container.** A tap inside the container that
misses the detector's targets is a **miss**, not a fall-through. **(B)** On a quantity screen the
shared widget still held the previous list's row count, so a fall-through resolved any tap as
"row 0 = the cursor" and fired that screen's destructive confirm.

**★ (A) A host overlay centred on the window sits exactly where the guest centres its own fixed-size
panel (§11), and the overlay's inter-key gutters and empty cells pass an exact-rectangle hit test** —
so a thumb that misses a key falls through the *whole* chain into the panel router. **(B)** Found in
adversarial review, fixed with an explicit "keypad not open" term, then device-verified: keypad open,
taps on gutters and on the one empty cell produced zero log lines and no menu change. The hole was
**new**, created by adding the feature, because the arm it replaced was field-only. Every overlay
drawn in that region needs its own term.

**★ (A) Gate a "tap anywhere advances" affordance on the dialogue window actually being on screen,
not on a held scene flag.** **(B)** The hold exists to absorb the blip where the dialogue window's id
momentarily drops to the no-window value; holding through a **real other** window id gave every
window opened from a dialogue a ~0.5 s head start in which tap-anywhere answered confirm — and it
could complete a purchase (*"it's not a cutscene, but in the shop window tapping anywhere seems to
act like the OK button"*). Fixed by cancelling the hold when the window id becomes **any other real
window**. ⛔ Do not widen the cancel to "the field looks free": that is the blip you are absorbing,
and that variant made menu arrows flash mid-scene. `CONFIRMED both ways`.

## 17. "App state N" means two different things

**★ (A) An app-state word can mean two unrelated screen families at once. Discriminate on the
LIVENESS OF A RECORD, not on the state word.**

**(B)** One state value was both the title/slot shell **and** the online screens. A touch gate using
it as "out of world" refused two whole screens outright, at a cost of one build and a player report.
The discriminator that works is free: the god-object resolver refuses to resolve until the record
holds a real character, so *"state N **and** the record resolves"* means "someone is playing, on an
online screen".

**★ (A) The contradiction is a free audit.** Grep your codebase for every gate using that state
constant and check they agree about what it means. **(B)** The reference port already contained the
other half of the answer — a disconnect modal was gated on the same state *precisely because* the
online screens run in it. Two gates depending on one constant meaning opposite things: one is wrong,
and finding out which is a five-minute grep. State words are the most-copied predicate in a port and
the least examined.

## 18. Fail closed by evidence

**(A) Prefer a gate whose failure mode is "no evidence" over one whose failure mode is "a wrong
claim".** Where a state-word gate might let a stale value through, arrange for the **pixels** to
refuse next: a screen that draws no cell frames and no cursor box yields no grid, so the worker has
nothing to act on.

**(B)** This is the recorded reason a removed shell-state term was *not* re-added "just for the slot
screen": a stale level can pass the liveness gate there, and the detectors then return nothing
because that screen draws none of the chrome they look for. `HIGH / CONFIRMED by construction`.

The property that matters is that the second layer keys on **positive evidence the screen must have
drawn**, which makes the gate list robust to the case you cannot enumerate: an unexpected screen.

## 19. Mode precedence: know which way yours errs

**(A)** Every mode resolver has a precedence order, and that order is a bug for one gate and a
guarantee for another. Write down which way yours errs, then place new gates on the side the known
blip cannot reach.

**(B)** In the reference port's mode match *field* outranks *cutscene*, so a dialogue page-turn blip
reads as Field and never as Menu. An earlier feature gated on Field was broken by exactly that blip;
the menu tap router, gated on Menu, is **structurally immune** to it. `HIGH / CONFIRMED`.

**(A)** Keep the now-unread held-scene flag with an explicit dead-code allowance and a comment saying
that any future *"is a scene playing?"* gate must read the **held flag**, not the mode — deleting the
deliberately dead field deletes the distinction. Precedence itself belongs to
[mode-resolution.md](mode-resolution.md).

## 20. Padding is priced by the cost of a false positive

**★ (A)** Not by thumb size. Pad generously where nothing else is nearby; pad a yes/no confirm by one
or two pixels, because tapping the question text must never become *yes*.

**(B)** Player report: *"in the shop and smith windows it looks like tapping anywhere acts like the
OK button"*. One of the two causes was over-generous uniform padding on the confirm dialog. It was
tightened to 2 px / 1 px there while the footer prompt kept 4 px on all sides, with a ⛔ comment at
both sites saying why they differ. `HIGH / CONFIRMED on device`. **(C)** The pixel counts are that
title's art at that panel size.

Uniform padding is the default and it is wrong: the slack that makes a footer comfortable makes a
purchase confirmation dangerous.

## 21. Release discipline for a derived target

**★ (A) Latch the DOWN decision, RE-DERIVE on UP, compare the two by MEANING, and act on the PRESS
coordinates.** A badge must still be *that* badge; a panel point need only still be a panel point.
The press point is what the player aimed at. This amends
[input-and-hud.md §7](input-and-hud.md#7-never-decide-twice-on-the-two-edges-of-one-touch) for
targets derived from the live frame: the badge may have been repainted away between the edges, so
"do not re-derive" cannot hold — but the *decision* still must not be re-taken from scratch.

> **REFUTED.** *"Store the panel point on DOWN and require the UP point to equal it."* Shipped in the
> first implementation. Tap slop is tens of window pixels — **(B)** ~28 window px is ~6 logical px on
> one device — so ordinary taps would have been silently dropped. **`adb shell input tap` lifts at
> the same pixel, so a device test can NEVER reproduce this.** Caught by reasoning, then verified
> with `input swipe` over a 12×9 px wobble: the action fired and the log printed the *press*
> coordinates. `HIGH / CONFIRMED`. Two rules follow — compare by meaning, and **test wobble with a
> small swipe, never with a tap**.

**(A)** Two more rules for host modals that borrow the guest's keys, both stuck-key bugs from
opposite directions: intercept the **press only** (swallowing releases strands a key that was already
down when the modal opened), and if you diverted a press you must divert its matching release — one
choke point plus a per-pointer "was diverted" set handles every caller. And swallow confirm only
where **the guest is waiting on you**; where a guest button underneath must still receive it, let the
key through and latch whatever host state you need **on that same key event**. **(B)** A polling
approach had no guaranteed window: key-to-grant completed inside a single worker iteration.

## 22. Two threads, two verdict lines

**★ (A) A decision split across two threads needs ONE verdict log line per side, per interaction.**
The UI names which detector claimed the point — or which gate refused it, printing **every gate's
live value**. The worker names the byte it wrote, or why the point was neither a row nor a cell.

**(B)** *"Why is touch dead on this screen?"* was answered by guesswork three times running — a stale
widget byte, a paint-looking flag that reads 0 on one screen and 12 on another, a grid whose shape was
not the one assumed — each round costing a build and a device session. After the UI printed
`panel (dx,dy) -> <detector>` and `REFUSED <gate>=<value> …`, the next screen was diagnosed and fixed
in **one build**. `HIGH / CONFIRMED`.

**(A) A worker-side probe cannot substitute:** it cannot see the detectors at all, because that half
of the decision does not exist on its thread. A diagnostic on the wrong side of a thread split is
silent about exactly the half that failed and reads as *"no problem found"*.

**(A) Log inside the guest's panel only.** Return early and silently outside it, or every stray world
touch floods the log and buries the lines that matter. The diagnostic is usable *because* it is rare.

## 23. Closing offsets the probe cannot reach

**(A) The standard way to answer "which byte holds the focus on this screen" is a whole-record diff
watcher plus a SINGLE arrow press** — one press, one diff, one answer, repeated per screen. **(B)**
Seven screens' focus bytes were established this way in one device session, including the discoveries
that one screen's cursor is an array indexed by page (§15) and another's is three separate bytes
(§24). It is also the only method that *shows* you when a screen's cursor is not a single byte; static
search for "the code that moves a cursor" finds the mover, not the per-screen owner.

**(A) Ship a read-only candidate dumper** printing every candidate cursor byte side by side, so a
human can drive the game into a state a headless probe structurally cannot reach and you read off
which byte tracks the highlight. **(B)** A probe driving a fresh character reached only the field menu
and the inventory in four attempts — shop, smith, warehouse and cash-shop screens need a save in a
town, a live server, or spendable stat points. All were closed by the dumper plus a person holding the
phone. Have it document itself out of existence: *delete me once these screens are measured*.
**(C)** The dumper is scratch.

**⛔ (A) Do not copy an offset into a shared anchor table "for symmetry" when a module already owns
it.** **(B)** A first implementation duplicated four offsets into the per-build anchor table; review
reverted it to exactly one new address, with a comment naming the watcher functions that read the
originals. `HIGH / INFERRED ⚠` — the failure mode is specifically test-invisible: once one copy is
re-measured, the watcher and the writer read different bytes and both halves keep passing their own
tests.

## 24. Screens to refuse, and saying so in code

**(A) Record which screens you deliberately refused and why**, at the match arm that returns "none"
and again in the anchor table's doc comment. An unexplained refusal reads as an oversight and invites
someone to "finish" it. Three recurring reasons **(B)**, each a real refusal in the reference port:

1. **A cursor that is several bytes and SKIPS unusable slots.** An index write lands where the game
   would never have stopped — replay arrow keys instead of writing an index.
2. **A list that only exists when the player has something to spend.** Unmeasurable without such a
   save, so there is nothing to gate on.
3. **Irreversible choice lists.** A wrong row accepts a quest you meant to decline or takes an
   unrecoverable story branch. **The gain is one button of convenience and the cost is asymmetric**,
   so leave it on the keypad until the row count is certain.

**(A)** Item 3 is the one place in this feature where *"nothing happens"* is not the worst outcome, so
the fail-closed argument must be made explicitly rather than assumed. The same reasoning retired an
existing translucent skip affordance whose effect on choice lists had never been established: **a
button of unknown effect is worse than no button.**

## 25. Selftesting geometry

**★ (A) An `include!`-based suite can be self-consistently green even though it tests the real
shipping source, if every test DERIVES its expectations from the constants under test.** Pin the raw
measurements as **literals** in one dedicated test and validate the pin by mutation. **(B)** A
reviewer mis-typed one measured grid origin (`22 → 30`): all 27 existing tests stayed green while taps
mapped eight cells off, because every one computed its expectation from that same constant. Two
literal-pinning tests were added and the mutation re-run — two went red. `CONFIRMED by mutation`. This
is a *different* failure from the forbidden mirror form in
[selftest-convention.md](../../../shared/selftest-convention.md): the module here *is* the shipping
source, and the suite is still asserting a tautology.

**(A) Compilation of the selftest is itself a regression gate** for a dependency-free `include!`d
module — run it immediately before and after any line-range edit. **(B)** Deleting three superseded
functions by range also removed three unrelated detectors and a shared struct that sat between them;
the standalone selftest caught it as an unresolved-name error.

**★ (A) Validate detectors against REAL dumped frames as well as synthetic ones: synthetic frames
prove the arithmetic, only real frames prove the thresholds** — every minimum ink, band height, run
length and fill ratio is a claim about real art. **(B)** A throwaway runner that `include!`s the real
detector module and eats raw framebuffer dumps was fed 20 probe frames plus device captures; the
per-screen target counts matched exactly (one list screen 0, an inventory 4, two screens 4 with the
page band clipped at the panel edge, two screens 2), and a second logical size was identical.

## 26. Anti-pattern gallery

**⛔ (A) A "short press that did nothing else" fallback inside a catch-all input zone converts every
near-miss on every small control into a false action.**

**(B)** A tap-to-move feature lived in two places: an explicit world-tap arm, and the *release* half
of a touch the joystick had already swallowed — "never latched a direction and lasted under 350 ms".
In the default touch layout the whole screen is a stick zone, so every thumb that slightly missed a
small quick-slot plate satisfied the second condition and walked the character. Two independent player
reports: *"tapping between the buttons moves me there and gets in the way of control"*.

**★ The feature was deleted and the on/off toggle the player asked for was declined.** A structural
misfire cannot be fixed by a setting — it only moves the blame. The culprit was also not the arm
anyone would suspect, which is the argument for tap-slop discipline everywhere else.

**★ (A) When you delete a feature, delete only the feature code. Keep the measurements, the
calibration tool and the write-up — measurement is the expensive part.** **(B)** With tap-to-move
removed the port retained the camera globals, the tile size, the render-translation formula, the axis
mapping, the probe's calibration flag and a pointer to the commit carrying the planner; shared helpers
with four other callers were identified and kept. A revival then costs a merge, not a re-derivation.

## 27. Symptom table

| Symptom | Cause | Fix |
|---|---|---|
| Tapping an item opens its action list instead of selecting it | one gesture writes the byte **and** sends confirm | split into focus / activate; decide by reading the byte back (§3) |
| A tap focuses the wrong item; no selection box, another item's detail shown | one screen's grid geometry applied to another | derive `x0`/pitch/cols/rows from drawn cell frames every tap (§10, §13) |
| Tapping anywhere in a shop or crafting panel acts like confirm | a held scene flag survived into the window; **or** a stale row count resolves everything to row 0; **or** an unmeasured maximum claims the whole panel | cancel the hold on any other real window (§16); allow-list watched windows (§16); derive the maximum (§12) |
| With the host keypad open, a missed key opens a game menu row | the overlay's gutters pass an exact-rectangle test and fall through | one "overlay not open" gate term per overlay (§16) |
| A second tap uses or drops the focused item | the guest's action popup is open and still reads as "overlay + item page" | hard refusal on the popup-open byte (§16) |
| Touch works everywhere except one screen family; the log says the state gate refused it | the app-state word means two things | discriminate on record liveness; audit other users of that constant (§17) |
| An unreachable screen opens, usually without crashing | the guest clamps only in its key arms, not its confirm arm | clamp in the geometry function **and** at the write site (§15) |
| A tap on a later page corrupts an unrelated field | the cursor is an array indexed by a live byte | **REJECT** out-of-range, never clamp (§15) |
| Menu taps work under `adb input tap` but most real taps do nothing | press/release compared by coordinate | compare by meaning, act on the press point, test with `input swipe` (§21) |
| A key repeats forever after a synthesised confirm | the synthetic press entered the held-key map | emit host keys as an immediate down+up outside that map (§2) |
| Opening a host dialog leaves a direction key stuck down | the dialog swallowed the release too | intercept the press only; divert only releases whose press you diverted (§21) |
| A host dialog over a guest confirm can never commit | the dialog swallows confirm unconditionally | swallow only where the guest waits on **you**; else pass through and latch on that key (§21) |
| A detector claims a target where nothing was drawn, or garbage after rotation | missing fail-closed guards | the six guards (§9) |
| A detector fires on another widget of the same colour | matching on colour alone | shape invariant + name the excluded rival (§9); positional constraint (§8) |
| A geometry suite is fully green while taps land wrong | every test derives its expectation from the constant under test | one literal-pinning test, validated by mutation (§25) |
| Deleting superseded functions silently removes unrelated code | a dependency-free module has no internal boundaries | selftest compilation before and after every range edit (§25) |
| "Why doesn't touch work here?" costs a build per guess | only one side of the thread split logs | one verdict line per side, gate values included (§22) |
| Tapping between two small controls walks the character | a "short press" fallback inside the catch-all zone | remove it; keep the measurements (§26) |

## 28. Checklist

- [ ] The guest's event entry point and the core's event enum both confirm **keys only** (§1).
- [ ] A tap is: write the game's cursor byte, synthesise the game's key — nothing reimplemented (§2).
- [ ] Synthetic keys are an immediate down+up and never enter the held-key map (§2).
- [ ] One tap focuses and sends no key; the second activates and writes no byte; the case is decided
      by **reading the byte back** (§3).
- [ ] Screen id, page, cursor and row count are re-read inside the worker on every tap (§4).
- [ ] The router branches in the guest key callback's own order (§5).
- [ ] The printed-keycap stage shipped before any cursor-writing stage (§7), found by ink colour in
      fixed narrow columns with a positional constraint rather than a loosened colour match (§8).
- [ ] Every detector carries all six fail-closed guards, each naming the rival it excludes (§9).
- [ ] Pitch is the minimum gap with multiples allowed; bands are separated by a cell height; boxes
      are matched on their un-occluded edge (§10).
- [ ] The panel origin is two divisions from the **logical** size, proved at three or more sizes (§11).
- [ ] Every variable-length widget's maximum count is **derived** from "does it still fit" (§12).
- [ ] Input geometry comes from pixels; memory only cross-checks (§13), and the worker refuses unless
      the derived index equals the live cursor byte (§14).
- [ ] Scalars are clamped twice; array indices are **rejected** (§15).
- [ ] Every gate in §16 exists, including one term per host overlay drawn over the panel, and a
      detector that claims a container returns a **miss** rather than a fall-through.
- [ ] No gate treats an app-state word as one thing without auditing the other users of it (§17), and
      gates are layered so the second requires positive pixel evidence (§18).
- [ ] The mode precedence direction is written down and new gates sit on the blip-free side (§19).
- [ ] Padding is set per site by the cost of a false positive, with the reason in a comment (§20).
- [ ] UP re-derives, compares by meaning, acts on the press point; wobble is tested with a swipe (§21).
- [ ] One verdict line per thread, logged only inside the guest's panel (§22).
- [ ] Offsets a module owns are not duplicated into the shared anchor table (§23).
- [ ] Refused screens carry their reason at the refusing arm (§24).
- [ ] One test pins the raw measurements as literals, validated by mutation; detectors were run
      against real dumped frames, not only synthetic ones (§25).
