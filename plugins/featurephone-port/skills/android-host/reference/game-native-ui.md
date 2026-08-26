# Drawing host UI with the game's own assets

The game inside your emulator already ships a bitmap font, a UI window sprite sheet and an icon
atlas. Draw the host overlay with those and host windows stop looking like a mod. This file is the
extraction-and-drawing method: locating the font members, recovering their format from the loader
rather than from a published standard, composing a syllabic script the way the guest composes it,
measuring the window chrome, and reusing the icon atlas.

> **★ This file SUPERSEDES the hand-rolled-ASCII-font-plus-closed-set-CJK approach for any title
> that ships its own font.** [glyphs-and-text.md](glyphs-and-text.md) stays correct for three
> narrower jobs and only those: a title that ships no font at all, host-**invented** words in a
> script the game's font does not cover, and the eyeball preview/regression sheet. Read §0 before
> writing a single `const` row of a bitmap font.

Tags: **(A)** generic · **(B)** worked example from the reference port, re-derive it ·
**(C)** do not copy. `⚠` marks anything inferred.

## Contents

- [0. The decision gate](#0-the-decision-gate)
- [1. Find the font members](#1-find-the-font-members)
- [2. Recover the format from the LOADER, not the standard](#2-recover-the-format-from-the-loader-not-the-standard)
- [3. Recover composition from the guest's own renderer](#3-recover-composition-from-the-guests-own-renderer)
- [4. Bake it as generated source, with `--check`](#4-bake-it-as-generated-source-with---check)
- [5. Two faces, one cell: measure each face's ink extents](#5-two-faces-one-cell-measure-each-faces-ink-extents)
- [6. Integer scale for chrome, FRACTIONAL for a replica](#6-integer-scale-for-chrome-fractional-for-a-replica)
- [7. Strings the host read out of guest memory](#7-strings-the-host-read-out-of-guest-memory)
- [8. What the coverage test becomes](#8-what-the-coverage-test-becomes)
- [9. Measure the chrome from the game's own UI sheet](#9-measure-the-chrome-from-the-games-own-ui-sheet)
- [10. Prove the style is global: byte-identical corners](#10-prove-the-style-is-global-byte-identical-corners)
- [11. Implement the shape once, and nest the cut](#11-implement-the-shape-once-and-nest-the-cut)
- [12. Window KIND decides the body](#12-window-kind-decides-the-body)
- [13. Reuse the icon atlas: one frame rule, on the worker](#13-reuse-the-icon-atlas-one-frame-rule-on-the-worker)
- [14. Verify an art mapping with a contact sheet](#14-verify-an-art-mapping-with-a-contact-sheet)
- [15. Scaling a tiny 1-bit-alpha icon](#15-scaling-a-tiny-1-bit-alpha-icon)
- [16. Reserve the icon and caret columns](#16-reserve-the-icon-and-caret-columns)
- [17. Verification when the module cannot be `include!`d](#17-verification-when-the-module-cannot-be-included)
- [18. Publication: generated font source is licensed content](#18-publication-generated-font-source-is-licensed-content)
- [19. Symptom table](#19-symptom-table)
- [20. Checklist](#20-checklist)

---

## 0. The decision gate

**(A) Before authoring any host text layer, inventory the archive for three member families.**

| Look for | If present, it gives you | Here |
|---|---|---|
| a **font** family (component banks, a codepage conversion table, Latin faces) | the host's text layer, covering by construction every string the game can display | §1–§8 |
| a **UI chrome** sprite sheet | the measured window style every host panel must obey | §9–§12 |
| an **icon** atlas | the game's own item/skill art in host lists, at zero RE cost | §13–§16 |

**(A)** The font question is load-bearing, because a hand-rolled ASCII font forces every label into
its character set and the failure mode is *invisibility*: the drawing routine finds no glyph, skips
the character, and a label in the player's language renders as blank space. Teams stop noticing the
constraint and start treating it as a house style. **(B)** The reference port shipped an English
overlay for the entire life of its 5x7 ASCII font, and its own note on why is one sentence — *"the
reason the overlay has been in English until now was the font."* Adopting the game's font (a
component bank, a conversion table, variant tables and a Latin face, all already in the archive)
localised the whole settings panel in one session.

**(A)** So: grep for font members (§1); if they exist they are your text layer and you stop
hand-rolling; if they do not, [glyphs-and-text.md](glyphs-and-text.md) is the right answer rather
than a compromise. Either way you still need a coverage test (§8), and text **input** still goes to a
platform dialog ([glyphs-and-text.md §6](glyphs-and-text.md#6-text-input-goes-to-a-platform-dialog)).

## 1. Find the font members

**(A)** Dump every plausible string out of the guest image and filter for resource-path shapes: the
paths the code names are the resources the game loads
([static-toolkit.md §10](../../guest-re/reference/static-toolkit.md#10-reading-text-out-of-the-image)
for the extraction, §9 of the same file for going from a string to its consumer). Two independent
derivations — **the string table names it**, and **the loader copies it**, the load routine's
`memcpy` destinations being static addresses whose readers you can then find (§2).

> **★ (A) The sibling that no string names is dead.** Font directories routinely carry more faces
> than the game uses — a leftover from another screen size or an earlier build. Do not port it, and
> do not average two candidate faces because you cannot tell which is live.

**(B)** The reference port's font directory held six members: a component bank, a codepage conversion
table, a packed member carrying a remap plus three variant tables, and several candidate Latin faces
— one of which no string in the image named, and which was excluded on that basis.

## 2. Recover the format from the LOADER, not the standard

The published standard for a script tells you what the script *is*, not how this file is laid out,
and a file that "obviously" matches the standard is the trap.

**(A) Procedure.** Find the load routine for the member and read its copies in order, recording three
things per copy: **source offset in the file**, **size**, **destination address**. Offset plus size is
the slice; the destination tells you which table it becomes, because you can find that address's
readers in the renderer.

> **★★ (A) FILE order can differ from ADDRESS order.** Slicing a multi-table member in
> destination-address order yields a font that renders *most* characters correctly. This is the
> highest-risk step in adopting a game's font, and it does not crash.

**(B)** Four copies out of one packed member had source offsets `0x00 / 0x5d / 0x89 / 0xb1` while
their destinations ran in **descending** address order — file order `[remap][variant A][variant
B][variant C]`, sizes 93 / 44 / 40 / 22 as the deltas, addresses the other way round. The method that
produced those numbers is "read the loader's copies"; that is the part to re-run.

**(A) Pin every slice to the copy that produced it in the assertion message**, not in a comment, so a
later reader can re-verify it from one offset in the disassembly:

```rust
assert_eq!(remap.len(), 93, "loader memcpy at member offset 0x00 copies 93 bytes");
```

**(A) Second derivation:** the renderer's per-variant **strides** pin the bank sizes independently of
the loader, and a disagreement means you mis-sliced. **(B)** Cross-checking both gave ~360 component
glyphs × 22 bytes in a **16 × 11** cell, split **160 / 88 / 112** across the three component classes.

## 3. Recover composition from the guest's own renderer

**(A)** For a syllabic script, read the guest's composition routine instruction by instruction. The
shape is nearly always this:

```
codepage index -> conversion table -> packed component code -> split into component indices
               -> remap each index through the component remap table
               -> COPY the first component's bitmap, then OR each remaining component in,
                  each component's VARIANT chosen by a table indexed on its NEIGHBOURS
```

> **★★ (A) The variant tables are the part a naive implementation omits, and omitting them still
> produces recognisable script.** Only the syllables needing a squeezed or narrowed form come out
> wrong, so "does it look like the script?" cannot detect it and it surfaces months later on one rare
> word.

**(A) Two shape assertions a human can check**, and they are the whole composition test: a syllable
with **no final component** must leave the cell's bottom row clear, and adding a final component must
change rows in the bottom band **and** change the variant chosen for the components above it. If only
the bottom band changes, your variant table is not wired in. **(B)** The routine that yielded this
pipeline in the reference port is ~220 bytes of guest code.

## 4. Bake it as generated source, with `--check`

**(A) Do not read the font resource at runtime.** The overlay draws before the guest has opened
anything, so a runtime read needs a missing-resource *and* a short-resource path on every draw, and
the app acquires a state in which it ships without its own font. Emit **generated source** — the raw
banks, the codepage-to-index table, an index function — which lands in `.rodata`, is kept by the
linker, and cannot be forgotten at packaging time.

**(A) Two gates, both mandatory.** ① **Assert each member's exact byte length before emitting**, with
a message that says what to do: `"expected <N> bytes; the asset changed — re-read the format notes"`.
Without it a changed asset silently shifts every glyph. ② **A `--check` mode** that regenerates in
memory and byte-compares the whole output, exiting non-zero when stale — the archive is usually
gitignored, so the baked table and the archive beside it drift with nothing to say so. Member
ownership and tool order (a co-owned member legitimately differs) are in
[repacking.md](../../archive-prep/reference/repacking.md).

**(B)** The reference port's extracted font is ~14 KB of `.rodata` and its generated
codepage-to-index table ~68 KB. Both come from
`python "${CLAUDE_PLUGIN_ROOT}/tools/gen_gamefont.py"`: `--list` enumerates candidate members and
cross-checks them against the string table, `--probe` reports the divisor families that close so you
can match them to the loader's copies, `--check` is the staleness gate, `--preview` writes the contact
sheet. Glyph counts, bytes per glyph, cell size and variant-table offsets are **inputs you supply
from §2** — the tool does not guess a format.

## 5. Two faces, one cell: measure each face's ink extents

> **REFUTED.** *Previously documented (glyphs-and-text.md §4):* "Bottom-align, do not top-align."
> **Refuted by** the game's own font, where both faces share **one** cell height so "bottom-align" is
> not expressible, and by two independent player reports of the same artefact on two widgets: *"the
> number is not in the middle of the box, it pokes out of the top."* **Actual mechanism:** the faces
> ink *different rows* of the shared cell — the Latin face reserves its lower rows for descenders, so
> centring a digit on the **cell** height pins it to the top of its box. **Carried forward:** every
> number the host centres is visibly off and reads as a rendering fault.

**(A) Measure, then align on the measurement.** Compute each face's minimum and maximum inked row
over its whole range from the generated data, and name two constants: a **cap height** (the rows a
digit or capital actually inks) for centring a number in a box, and a **drop** for putting the Latin
baseline on the syllabic baseline in a mixed line. The drop is safe **only for strings with no
descenders** — a dropped descender is pushed past the last row and clipped, so write that limitation
at the constant. **(B)** In the reference port's font the syllabic face inks all 11 rows while the
Latin face inks roughly the top 7 and reserves the rest, which is why one constant is not enough.

## 6. Integer scale for chrome, FRACTIONAL for a replica

> **REFUTED.** *Previously documented (glyphs-and-text.md §4):* "Scale: draw at integer multiples
> only." **Refuted by** side-by-side comparison with the game's own window — the replica's text sat
> one whole size step smaller. **Actual mechanism:** the guest reaches the screen through a
> **nearest-neighbour non-integer** upscale, so one source pixel lands as `floor(s)` or `ceil(s)`
> device pixels depending on position, and an integer-only path can only round the scale down.
> **Carried forward:** a host "replica" is identifiably not one, and the defect is invisible until the
> two windows are on screen together.

**(A) The rule has two branches and you must know which you are in.** For **host-invented chrome**
(settings, layout editor, diagnostics) use integer scale — a 5x7 cell at 2.5x is a blurry 5x7 cell.
For a **replica of one of the game's own windows**, match the guest's upscale: draw each glyph pixel
as `[floor(px*s), floor((px+1)*s)) × [floor(py*s), floor((py+1)*s))`, because rounding each edge
independently is exactly what nearest-neighbour does. **(B)** A logical 496x240 framebuffer upscaled
into 1080 device rows is ×4.5 on the reference port's targets: the fractional path exists because 4
and 5 are both wrong. Express the replica's geometry in the guest's **logical** pixels and scale once
at the end — [host-windows.md](host-windows.md) owns that sizing discipline.

## 7. Strings the host read out of guest memory

The moment the host displays a name it read from a live guest record, it needs three things authored
labels never needed.

**(A) 1 — A codepage decoder built as the exact inverse of the index the glyph composer uses.** Not a
general codec: the inverse, so that what the font can compose is exactly what the decoder can
produce. Round-trip it over real names out of the game's own tables.

**(A) 2 — An undecodable byte becomes a VISIBLE sentinel, never a dropped byte.** *A name with a
visible gap gets reported; a name that is quietly shorter does not.* Test three cases explicitly
because they arrive from different directions: a **truncated trail byte** at a buffer's end, an
**unmappable lead/trail pair**, and a syllable from the codepage's **extension rows** that the font
has no glyph for. **(B)** All three print a sentinel in the reference port, each with its own case.

**(A) 3 — The in-band colour-escape alphabet.** A game's text layer often carries one: a short run of
punctuation bytes that change the pen colour mid-string. Find it by locating that run in rodata with
its **parallel RGB table** beside it, then finding every site that reads the run — usually **exactly
two**, both in the font layer: a **zero-width predicate** used by width and line-wrap, and one that
**sets the pen colour and advances the index without drawing**. Implement both: implement neither and
the escape prints as a stray punctuation glyph, implement only the colour and every measured width is
off by one cell. **(B)** The reference port's alphabet is 7 bytes with the RGB table immediately
before it and exactly two readers image-wide, and no site in `.text` compares against the individual
bytes; the escapes are valid at **any** position and the game embeds them mid-string. **(C)** That
host strips only a **leading** escape and is knowingly incomplete for mid-string ones — state such a
limitation in your notes rather than discovering it from a screenshot.

> **REFUTED.** *Previously documented (record-formats.md §7, and the reference port's own catalogue
> for a full session):* a leading punctuation byte on records in the game's name table is a **record-
> class flag** ("this item is purchasable"). **Refuted by** a full re-decode of the table: the
> identical marker appears on records that were never purchasable, and a *different* marker on one the
> player remembered buying. **Actual mechanism:** it is the text **colour** from the alphabet above,
> valid anywhere in the string — the class correlation held only on the sample that motivated it.
> **Carried forward:** a wrong catalogue and a pipeline treating a colour byte as a semantic flag.
> **The rule:** derive an in-string marker's meaning from the code that **consumes** it, never from
> correlation over the records that carry it.

Preserving these bytes when you *write* into the game's tables belongs to
[record-formats.md](../../archive-prep/reference/record-formats.md), not here.

## 8. What the coverage test becomes

> **REFUTED.** *Previously documented (glyphs-and-text.md §2):* "A full Korean font is about 11,172
> syllables; a CJK font is far worse. Neither is shippable inside a bitmap-font overlay" — therefore
> gate coverage on a hand-maintained word list forever. **Refuted by** the bound that actually
> applies: game strings are in a **legacy national codepage** whose syllable block is far smaller
> than Unicode's (the Korean national standard carries **2,350** precomposed syllables against
> Unicode's 11,172). **Actual mechanism:** 11,172 is the Unicode bound; a bitmap font for the whole
> *codepage* block is finite and cheap. **Carried forward:** a permanent coverage gate and a partial
> font, for a problem that can be closed structurally.

**(A) Adopting the game's font MOVES the gate; it does not delete it.** The runtime codepage is
usually a **superset** of the standard the font has glyphs for — extension-row syllables have no
glyph — and the layout routine skips them silently, exactly like the old missing-glyph mode. When the
ASCII face is itself a game resource, **ASCII is not free either**: **(B)** the reference port's Latin
face is 95 glyphs starting at space, and anything outside that range returns an all-zero cell. So
keep one vocabulary test over the **enumerated** phrases the overlay can draw, and make a silent skip
fail several ways at once:

```rust
let cells = gamefont::layout(phrase);
assert_eq!(cells.len(), phrase.chars().count(), "a character was skipped in {phrase:?}");
assert!(cells.iter().all(|c| c.iter().any(|&row| row != 0)), "blank cell in {phrase:?}");
assert_eq!(gamefont::width(phrase), cells.len() as i32 * gamefont::ADVANCE);
```

**(B)** The reference port additionally asserts **2,350 / 2,350** of the standard's syllables compose
non-blank ink with none spilling past the advance. That whole-block assertion is what makes "the font
covers anything the game can emit" a fact rather than a hope.

## 9. Measure the chrome from the game's own UI sheet

> **★ (A) Before writing new RE for a visual question, inventory the decoders you already have
> against it.** A game's UI window chrome is normally a sprite sheet in the **same member family** as
> its icon sheets, so the decoder you wrote for the icon atlas already reads it.

**(B)** In the reference port the chrome sheet is ~2 KB and decoded with **zero** changes by a sprite
reader written many sessions earlier for the item atlas; the session note records the discovery as
*"the decoder has been in the repo since session 44 — we were only using it for icons."* Three chrome
sheets in that archive all decode the same way, and the whole "host windows look like the game's"
capability cost no new reverse engineering. The decoder itself — stride versus width, palettes, origin
corner — is
[asset-decoding.md §3](../../guest-re/reference/asset-decoding.md#3-sprite-sheets-stride-is-not-width).
This is a pure read path: nothing is repacked and no art is shipped.

## 10. Prove the style is global: byte-identical corners

**(A) Procedure.** Pick **two frames of very different sizes** from the same chrome sheet, extract the
same corner from each as a byte block, and compare. **Byte-identical** ⇒ the corner is a constant of
the game's style, not per-frame art: measure it once, implement it once. **Different** ⇒ the style is
not global, and you have learned that instead of assuming it — which is what makes the claim
falsifiable.

**(B)** Two frames sized 19x25 and 85x26 had byte-identical top-left corners, and the measured shape
was a **2-pixel 45° cut**, then a **1-px dark outline**, a **light bevel** (with a darker shadow-side
variant) and a **dark interior**. The measured conclusion — *the game never draws a square corner
anywhere* — was also the player's actual complaint about the host's windows (*"the corners are too
pointy"*).

## 11. Implement the shape once, and nest the cut

**(A)** One primitive, and every host panel, row, button, arrow and badge goes through it:

```rust
fn fill_chamfered(buf: &mut Frame, r: Rect, cut: i32, colour: u32);
fn deflate(r: Rect, by: i32) -> Rect;
```

**(A) Nest the corner parameter, not just the rectangle.** Each inset ring uses one less pixel of cut
— `cut`, `cut - 1`, `cut - 2` — or the bevel cuts straight **across** the corner instead of following
it round.

> **⛔ (A) Never "simplify" an inner chamfered fill back to a rectangular fill.** The square corner
> shows through the chamfered one in front of it. This is precisely the line a later cleanup pass
> deletes, so write the prohibition at the call site.

**(B)** Three nested chamfered fills at `cut`, `cut-1`, `cut-2` reproduce the sheet's own corner
exactly. Separately, a mock-up (§17) caught a button face indistinguishable from the panel body at a
1-px border; the fix was 2 px.

## 12. Window KIND decides the body

> **REFUTED.** *Previously believed:* "every colour in this window was sampled from the game,
> therefore the window is styled correctly." **Refuted by** a player rejecting exactly such a window —
> *"make it more like the in-game menu windows"* — and by the fix being a palette already approved for
> a different host panel. **Actual mechanism:** a game has several window **kinds** and the colours
> came from the wrong one. **Carried forward:** you defend the window with "I sampled it" while it
> keeps reading as invented.

**(A) Enumerate the kinds first, sample each kind separately, and choose by kind.** Keep every palette
in the source, each labelled with the window it belongs to and carrying the prohibition on swapping
them, or the next editor unifies them for tidiness. **(B)** Two kinds in the reference port's family:
a **list / settings** window is a parchment body with a checker texture and a dark title band; a
**two-line question / message** box is dusty rose with a near-black plate behind the number.
Behaviour for these windows — modal precedence, which affordances they draw, sizing — is
[host-windows.md](host-windows.md).

## 13. Reuse the icon atlas: one frame rule, on the worker

**(A)** The decoded atlas is directly reusable, and the frame-index rule must be **one public function
living where guest memory is readable** — the worker. The UI thread has no guest memory
([thread-model.md](thread-model.md)), so it *cannot* compute the general case, and one function is
what stops two host surfaces from disagreeing about what an item looks like.

**(A)** The rule looks trivial and is not. Expect three tiers: a class where the frame is an
**identity** on the record's category id; at least one category that reads a **per-record icon byte
out of the item database** in the guest's heap; and at least one **hardcoded exception** index.
**(B)** Adding an icon column to a host picker in the reference port needed **zero** new RE — atlas,
aspect-preserving alpha blit and frame rule were already in the tree for another surface, behind one
worker-side entry point.

**(A)** Icon sheets are normally **number-free**: hotkey digits and stack counts come from a separate
numeric sheet composited at draw time. Decode the sheet; do not crop the game's composited HUD cell,
which bakes in a number that will be wrong in your context
([asset-decoding.md §3](../../guest-re/reference/asset-decoding.md#3-sprite-sheets-stride-is-not-width)).

## 14. Verify an art mapping with a contact sheet

> **★ (A) No assertion substitutes for eyeball verification of an art mapping.** A mapping that is off
> by one still draws icons, and every icon is wrong. Nothing in the code can tell you that a helmet
> picture is in the helmet slot.

**(A) Procedure.** Lay the decoded atlas out as a contact sheet in frame order, print the record
table's categories in table order beside it, and read the two against each other. **(B)** The
reference port read the first thirteen atlas frames off a contact sheet and matched them, in order,
against the item database's first thirteen equipment categories; that pass also cross-confirmed an
independent finding about **which category id is the first equipment slot** — a free second
derivation, which is the sort of thing a contact sheet hands you and an assertion never will.

## 15. Scaling a tiny 1-bit-alpha icon

> **REFUTED.** *Previously shipped:* nearest-neighbour upscaling of the guest's icons into host
> buttons. **Refuted by** a player report about blocky icons. **Actual mechanism:** at a non-integer
> factor nearest maps source texels to uneven blocks (some 4 device pixels wide, some 5), which reads
> as mush rather than as pixel art. **Carried forward:** it looks like a bug in the blend code, so the
> investigation starts in the wrong place.

**(A)** Three rules, and both wrong answers look like blend bugs: **bilinear, not nearest**;
**normalise the weights across OPAQUE neighbours only** (naive bilinear averaging across the
transparent border gives the silhouette a dark halo, while opaque-only normalisation keeps a 1-bit
silhouette clean); and **fit to the smaller side** so square source art never stretches. **(B)**
Feature-phone icons are ~15 px and host targets ~85 px in the reference port — every port meets some
version of this factor.

## 16. Reserve the icon and caret columns

**(A)** Reserve the icon column — and the selection caret's column — on **every** row, including rows
that have neither. The icon rect is a square inset present on every row, so the column lines up
whatever the text does; a frame the atlas lacks draws as an **empty square** with the text start
unchanged; and a focus caret is drawn into whitespace the icon column **already reserves**, never
pushing the icon or the text. Why: a list whose rows jitter as the selection moves reads as broken,
and the cause is **invisible in a static screenshot** — which is the only artefact a bug report
contains. What focus indication should *look* like at arm's length is
[host-windows.md](host-windows.md).

## 17. Verification when the module cannot be `include!`d

Overlay drawing code legitimately references half the application crate, so the dependency-free
standalone pattern in [selftest-convention.md](../../../shared/selftest-convention.md) does not apply
to it. That file forbids mirror modules as *tests*; here is the missing half — two honest options:

1. **An `#[ignore]`d in-crate test that writes an image file** (a PPM is enough) for side-by-side
   comparison against a device screenshot. It runs on demand, needs no assertions, and produces an
   artefact a human can judge.
2. **A throwaway mirror renderer** carrying only the chrome-painting code, when the host toolchain
   cannot link the crate's test target at all. It must carry an explicit *"no correctness claim rests
   on this"* label and be **excluded from the test tally**.

**(A) Do the visual pass; it is not optional here.** **(B)** A full-resolution mock-up from exactly
such a throwaway renderer caught a real defect — a button face indistinguishable from the panel body
at a 1-px border — that no assertion would have found.

## 18. Publication: generated font source is licensed content

> **★★ (A) A generated SOURCE file carrying the game's font bitmaps, or a packed atlas blob committed
> as application source, is the same licensed content as the archive it came from — laundered through
> a different file extension.** An ignore rule keyed on archive extensions, directory names or the
> word "atlas" misses both, and so does a human reviewer scanning for them.

**(A)** The publishable artefacts are **the generator and the format notes**. Generate the table at
build time from the user's own archive, and gitignore generated tables by **content origin** rather
than by extension or directory. Add the row to your publish audit explicitly — *any generated source
or embedded blob whose bytes came from the archive is content*
([publish-audit.md](../../port-shipping/reference/publish-audit.md)).

**(C)** The reference port itself tracks its extracted font tables as a committed source file and its
decoded icon atlas as a committed blob inside the application crate, while that project's stated
policy is that extracted art stays out of version control and its ignore rules cover the archive and
its pristine backups by path and by name. Do not copy the arrangement; copy the lesson, which is that
this leak is invisible to everyone who is looking for a `.zip`.

## 19. Symptom table

| Symptom | Cause | Fix |
|---|---|---|
| Host UI is entirely in English in a non-English game and nobody can say why | the overlay draws with a hand-rolled ASCII font, so every label was authored inside its set | §0 |
| Extracted font renders recognisable script, but a subset of characters is subtly wrong | sliced in destination-address order, **or** the variant tables were omitted | §2, §3 |
| A number sits hard against the top of its box | centred on the font **cell** height; the Latin face reserves its lower rows for descenders | §5 |
| A replica of a game window has text one visible size step small | integer-only glyph scaling against a non-integer guest upscale | §6 |
| A guest-read name shows a stray punctuation character, and centred strings are off by a cell | unhandled in-band colour escape | §7 |
| A guest-read name is quietly shorter than on the game's own screen | the decoder drops unmappable bytes instead of printing a sentinel | §7 |
| A newly written host phrase draws with a hole in it | a codepage extension-row syllable the font has no glyph for | §8 |
| Host windows look invented; a player says the corners are too pointy | the chrome was designed instead of measured | §9, §10 |
| A square corner shows through a chamfered one, or the bevel cuts across the corner | an inner ring drawn as a plain rectangle, or all rings using the same cut | §11 |
| A window looks wrong although every colour was sampled from the game | sampled from the wrong window **kind** | §12 |
| Two host surfaces draw different icons for the same item | the frame rule was duplicated, or reimplemented UI-side | §13 |
| Every icon in a list is the wrong item, but the list is correct | frame-to-record mapping off by one, or an assumed identity | §14 |
| A small icon in a large button looks blocky, or has a dark halo | nearest-neighbour at a non-integer factor; bilinear across the transparent border | §15 |
| A host icon or HUD cell has a number burnt into it | art cropped from the composited HUD instead of decoded from the icon sheet | §13 |
| Rows shift horizontally as the selection moves | icon or caret column not reserved on rows that lack one | §16 |
| A shipped build behaves as if the font never changed | baked table stale against the archive; no `--check` | §4 |
| Nobody can verify the overlay because no standalone test can include it | overlay code references half the crate | §17 |

## 20. Checklist

- [ ] The archive was inventoried for font, chrome and icon members **before** any host font was authored.
- [ ] Font members were identified by the string table; the sibling no string names was excluded.
- [ ] Every slice comes from a loader copy, and each length assertion **names that copy's offset**.
- [ ] File order versus destination-address order was checked explicitly, not assumed.
- [ ] Bank sizes have a second derivation from the renderer's strides.
- [ ] Composition reproduces the guest's routine, variant tables included, with both shape assertions.
- [ ] The font is generated **source**, with per-member exact-length assertions and a `--check` mode.
- [ ] Cap height and baseline drop are measured constants; the drop's descender limit is written down.
- [ ] Host-invented chrome scales by integers; a replica uses the fractional path.
- [ ] Guest-read strings use the composer's inverse decoder, print a sentinel, and treat escapes as zero-width.
- [ ] A vocabulary test asserts cell count, non-blank cells and measured width over **enumerated** phrases.
- [ ] The chrome corner was measured from two differently sized frames and byte-compared.
- [ ] One chamfer primitive; the cut decrements per inset ring; no inner rectangle shortcut.
- [ ] Window kinds are enumerated and each palette is labelled with its kind.
- [ ] One worker-side frame rule; the mapping was read off a contact sheet.
- [ ] Icons upscale bilinearly with opaque-only weights, fitted to the smaller side.
- [ ] Icon and caret columns are reserved on every row.
- [ ] The overlay has a labelled visual verification artefact, excluded from the test tally.
- [ ] No generated table of the game's font bytes and no packed atlas blob is tracked in the repository.
