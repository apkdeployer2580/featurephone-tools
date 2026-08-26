# Glyphs, labels and text input — the hand-rolled font path

The host overlay has to draw text. This file is the **fallback** path: a small hand-rolled ASCII
bitmap font, a closed-set generator that extends it to a script it cannot cover, and where text
*input* has to go instead. Every consequence of a hand-rolled font shows up as a *silent* bug — a
label that is simply not there — so most of the file is about making the silence impossible.

> **★ Read §1 before writing a single `const` row.** If the title ships its own font as archive
> members — the common case in this family — you should be drawing with **that**, and
> [game-native-ui.md](game-native-ui.md) supersedes this file for everything except three narrow
> jobs named in §1 and §2.

Tags: **(A)** generic · **(B)** worked example from the reference port, re-derive it ·
**(C)** do not copy.

## Contents

- [1. First decide WHICH font: the game's, or a hand-rolled one](#1-first-decide-which-font-the-games-or-a-hand-rolled-one)
- [2. Closed-set glyph generation — a fallback, and when it is still right](#2-closed-set-glyph-generation--a-fallback-and-when-it-is-still-right)
- [3. Render directly at target size](#3-render-directly-at-target-size)
- [4. Drawing mixed ASCII and wide glyphs](#4-drawing-mixed-ascii-and-wide-glyphs)
- [5. Coverage must be asserted, not hoped for](#5-coverage-must-be-asserted-not-hoped-for)
- [6. Text INPUT goes to a platform dialog](#6-text-input-goes-to-a-platform-dialog)
- [7. Running the generator](#7-running-the-generator)
- [8. Symptom table](#8-symptom-table)
- [9. Checklist](#9-checklist)

---

## 1. First decide WHICH font: the game's, or a hand-rolled one

> **★ (A) The decision gate, in this order:**
> **(1) Look for the game's own font members in the archive. If they are there, draw with them.**
> **(2) Only if there are none, hand-roll an ASCII face and extend it with a closed set (§2).**

The reason is not aesthetics. The game's font is the right typeface **by definition**, and it
covers **by construction** every string the game itself can display — which deletes the entire
failure mode this file spends nine sections defending against, and makes the abbreviation
discipline below unnecessary.

**(B)** The reference port ran an English-only overlay for its whole life *because of the font*,
in its own words, and localised the entire settings UI in **one session** the moment it adopted the
game's face — a jamo bank plus its conversion and variant tables plus an ASCII face, all already
inside the archive it was shipping. Extraction, composition, baking and verification are
[game-native-ui.md](game-native-ui.md).

**So this section's rules are the FALLBACK branch**, and they stay exactly right there — a title
that ships no font, and a host-invented word in a script the game's own face does not cover.

### In the fallback branch: the host font is ASCII, so plan for it

A 5x7 bitmap font is about 250 lines of `const` data and draws at any scale with no
dependencies, no font file, no shaping and no layout engine. For a HUD it is the right choice.
Its cost is its character set.

**(B)** The reference port's set, device-confirmed: **uppercase Latin, digits, and
`* # - + / . %`**. That is it. No lowercase. No `=`. No punctuation beyond that list.

**★ The failure mode is invisibility, not tofu.** The drawing routine looks the character up,
finds nothing, and **skips it** — advancing zero pixels or one space. So:

- A Korean button label renders as **blank space**. It does not render a box, a question mark
  or a replacement glyph. It looks like the button has no label, or like a rendering bug in
  your blend code, and you will look in the wrong place.
- An on-screen hint written as `DRAG = MOVE` renders as `DRAG  MOVE`, because `=` is not in
  the set. Nobody notices for weeks.

Four rules that follow, all **(A)**:

1. **Every label, hint and toast is ASCII from the set, decided at authoring time.** Where the
   player asked for a word in their own language and the font cannot draw it, pick a short
   Latin abbreviation and move on: `CLR` for a cancel button, `GAME` / `MENU` / `FLIP` /
   `RESET` / `CANCEL` / `SAVE` for editor chrome.
2. **Make the type system help.** Give toasts the type `&'static str` so every one of them is a
   literal in your source that you can grep, rather than a runtime-formatted string that can
   acquire a non-ASCII character from data.
3. **Assert the set in a test.** One selftest that walks every literal your UI can draw and
   asserts the font covers it. Cheap, and it catches the `=` class of bug on the commit that
   introduces it.
4. **Extend the set before you abbreviate everything.** Adding lowercase or `=` to a 5x7 font is
   a handful of `const` rows. Do it once rather than working around it forever.

**⚠ And notice what rule 1 costs you.** "Pick a short Latin abbreviation and move on" is a
reasonable answer to a font limitation and a **bad** answer to a player who does not read Latin.
It is the rule that produced an all-English UI in the reference port for dozens of sessions. If
you find yourself abbreviating the third label this week, you are in the wrong branch of §1.

## 2. Closed-set glyph generation — a fallback, and when it is still right

Real CJK text is sometimes not optional — a status line naming a timed effect, a unit of time,
a word the player will look for.

> **SUPERSEDED, and by a bound rather than by a technique.** 0.1.0 argued from **11,172** Unicode
> Hangul syllables that a complete font "is not shippable inside a bitmap-font overlay", and made a
> hand-maintained word list a permanent fixture. That is the wrong bound: game strings are in a
> **legacy national codepage**, whose precomposed syllable block is far smaller — **2,350** for the
> Korean national standard — so shipping the *whole* standard set is finite and cheap and removes
> the missing-glyph mode **structurally**. The full correction, with the coverage gate that
> survives it, is [game-native-ui.md §8](game-native-ui.md#8-what-the-coverage-test-becomes).
> **(B)** the reference port's complete generated table is ~68 KB of `.rodata`.

**(A) So closed-set generation survives for exactly two jobs**, and it is still the right tool for
both:

1. **Host-INVENTED words** in a script the game's own font does not cover — words that, by
   definition, cannot come from the game's own vocabulary.
2. **The eyeball preview sheet** (§3), which is a regression instrument regardless of where the
   glyphs came from.

Everything below is written for those two jobs.

> **★ (A) The host draws a CLOSED, KNOWN set of words. Only the syllables in that set need
> glyphs. Generate them, and generate them from the word list so a new word cannot forget one.**

The pipeline, and every stage of it matters:

```
WORDS = ["<word 1>", "<word 2>", ...]          # the exact strings your host draws
   |
   |  derive the syllable/character SET from the word list  <-- not a hand-maintained list
   v
render each character from a system TTF at the target cell size, threshold to 1 bit
   |
   v
emit RUST SOURCE:  pub static GLYPHS: [(char, [u16; CELL_H]); N]   MSB = leftmost pixel
   +               pub fn glyph(c: char) -> Option<&'static [u16; CELL_H]>
   +               pub fn covers(s: &str) -> bool
   +               pub const CELL_W / CELL_H / WORDS
   |
   v
a selftest include!s the generated file and asserts covers() over every string the host
can produce
```

**(B)** The reference port ships **15** glyphs, derived from six words. Fifteen entries of
twelve `u16` rows is under a kilobyte of `const` data.

Why emit **Rust source** rather than a runtime asset:

- the selftest can `include!` it, so coverage is checked at test time with no file IO
  (`${CLAUDE_PLUGIN_ROOT}/shared/selftest-convention.md`);
- there is no asset to forget to package, and no failure mode where the app ships without its
  font;
- the linker drops nothing and the data is in `.rodata`, not on the heap.

Cell format: one word per row, MSB = leftmost pixel, with the word width chosen from the cell
width (`<=8 -> u8`, `<=16 -> u16`, `<=32 -> u32`), so a 12x12 cell is `[u16; 12]`. Store the
character as a `char` key and linear-scan — at these sizes it does not matter.

## 3. Render directly at target size

> **★ (A) Rasterise the font at the size you will draw, and threshold. Do NOT render large and
> downscale.**

This is counter-intuitive and it is the finding most likely to be re-litigated by the next
person.

A hinted screen typeface **deliberately snaps stems onto the pixel grid** at small sizes; that
is what hinting is for. Rendering at 4x and box-filtering down averages a crisp on-grid stem
with its neighbours, producing mid-grey, and any threshold you pick then either drops the stem
or fattens it into its neighbour. Complex syllables turn to mush.

Practical procedure:

1. Pick candidate faces from what the platform actually has.
2. Render your **worst** character — the densest, most-stroked one in your word list — at
   10, 11, 12, 13 px.
3. Render a preview sheet of each and **look at it with your eyes**.
4. Take the **smallest size at which the worst character is still readable**.

**(B)** In the reference port every candidate face lost strokes at 10 px; a standard Korean
screen face at **12 px with a threshold of 110/255** was the smallest that survived. Those are
that font, that character set and that threshold — re-run the procedure, do not copy the
numbers.

Always look at the preview before committing. A generator that silently produces empty cells
for characters the face does not contain is otherwise indistinguishable from one that worked —
and the resulting hole in the UI is invisible for exactly the reason in §1.

## 4. Drawing mixed ASCII and wide glyphs

The two cells differ in width **and** in height — 5x7 versus 12x12 **(B)**. Three helpers:

```rust
fn draw_mixed_text(buf, x, y, s: &str, colour);   // interleaves both cell kinds
fn draw_wide(buf, x, y, ch: char, colour);        // one generated glyph
fn mixed_text_w(s: &str) -> i32;                  // measured width, for centring
```

**Bottom-align, do not top-align** — *while the two faces have different cell heights.* Aligning
tops makes the Latin text float above the baseline of the wide glyphs and it reads as a rendering
fault; bottom-aligning puts both on a common baseline.

> **REFUTED.** *Previously documented (this section, 0.1.0):* "Bottom-align, do not top-align",
> stated as the general rule for mixing faces. **Refuted by** the game's own font, where both faces
> share **one** cell height — so "bottom-align" is not even expressible — and by two independent
> player reports of the same artefact on two different widgets: *"the number is not in the middle of
> the box, it pokes out of the top."* **Actual mechanism:** the faces ink **different rows of the
> same cell**; the Latin face reserves its lower rows for descenders, so centring a digit on the
> *cell* height pins it to the top of its box. **Consequence if carried forward:** every number the
> host centres is visibly off, and reads as a rendering fault rather than as a metrics bug.

**(A) The transferable rule: measure each face's ink extents inside the cell, and align on the
measurement.** You need two named constants, not one — a **cap height** for centring a digit or a
capital in a box, and a **drop** for putting the Latin baseline on the syllabic baseline in a mixed
line (usable only for strings with no descenders, since a dropped descender is clipped). Derived
from the generated data, not by eye:
[game-native-ui.md §5](game-native-ui.md#5-two-faces-one-cell-measure-each-faces-ink-extents).

`mixed_text_w` must be the **only** width computation. Centring code that assumes
`len(s) * 6` is correct until the first wide character, and then every centred string in the
app is off by a variable amount.

**Scale: integer multiples — for chrome the host invented.** A 5x7 cell at 2.5x is a blurry 5x7
cell, so settings, the layout editor and diagnostics all draw at whole multiples.

> **REFUTED.** *Previously documented (this section, 0.1.0):* "Scale: draw at integer multiples
> only", with no exception. **Refuted by** putting a host **replica** of one of the game's own
> windows side by side with the real one: the replica's text sat one whole size step smaller.
> **Actual mechanism:** the guest reaches the screen through a **nearest-neighbour non-integer**
> upscale — **(B)** a logical 496x240 framebuffer into 1080 device rows is x4.5 — so one source
> pixel lands as `floor(s)` or `ceil(s)` device pixels *depending on its position*, and an
> integer-only path can only round the scale down. **Consequence if carried forward:** anything you
> call a replica is identifiably not one, and the defect is invisible until both windows are on
> screen together.

**(A) The rule has two branches and you must know which you are in:** integer scale for
host-invented chrome, **fractional** for a replica of the game's own window — rounding each glyph
pixel's edges independently, which is what nearest-neighbour does. The drawing rule and the sizing
discipline that goes with it are
[game-native-ui.md §6](game-native-ui.md#6-integer-scale-for-chrome-fractional-for-a-replica) and
[host-windows.md](host-windows.md).

## 5. Coverage must be asserted, not hoped for

Because a missing glyph renders as **nothing**, coverage cannot be checked by looking at the
screen — the hole is where the character would have been, and you do not know it was supposed
to be there.

The generator emits the predicate; the selftest uses it:

```rust
pub fn covers(s: &str) -> bool {
    s.chars().all(|c| c.is_ascii() || GLYPHS.iter().any(|(g, _)| *g == c))
}
```

**⚠ That predicate treats ASCII as free, and it is only free while the ASCII face is YOURS.** When
the Latin face is itself a game resource it has a real range — **(B)** 95 glyphs starting at space
in the reference port — and anything outside it returns an all-zero cell that draws blank in
exactly the way a missing wide glyph does. **(A) Check both faces against their real ranges**, and
make the test assert more than acceptance: that the **cell count equals the character count**, that
**no cell is blank**, and that the **measured width equals the expected count x advance**, so a
silent skip fails several ways at once
([game-native-ui.md §8](game-native-ui.md#8-what-the-coverage-test-becomes)).

```rust
#[test]
fn every_status_line_is_drawable() {
    for line in every_possible_status_line() {          // enumerate, do not sample
        assert!(glyphs::covers(&line), "no glyph for a character in {line:?}");
    }
}
```

**Enumerate the strings, do not sample them.** If a module produces status lines from a
template plus a small set of names and a small set of units, the test should build the full
cross product. That is what makes "a new word cannot forget a glyph" true rather than
aspirational.

`covers` will be dead code from the crate's point of view — its only caller is a test file that
`include!`s rather than links — so it needs `#[allow(dead_code)]`.

### Strings you read out of GUEST MEMORY are a different problem

Everything above is about strings **you** authored, which is why a vocabulary test can enumerate
them. The moment the host draws an item or skill name read from a live guest record, three more
things are required — a codepage decoder built as the exact inverse of the composer's index, a
**visible sentinel** for undecodable bytes (*a name with a visible gap gets reported; a name that is
quietly shorter does not*), and handling for the game's in-band colour-escape alphabet, whose bytes
are zero-width in width and wrap calculations. All three are
[game-native-ui.md §7](game-native-ui.md#7-strings-the-host-read-out-of-guest-memory).

## 6. Text INPUT goes to a platform dialog

**(A) The overlay font has no text-input machinery, and building one is not a good use of a
port's time.** For a server hostname, an account transfer code, a slot name — anything the
player types — hand off to the operating system's own dialog over JNI.

You get, for free: an IME for every language, clipboard paste, cursor placement, selection,
autocorrect suppression, accessibility, and a keyboard the player already knows.

```
Rust worker/UI  --JNI-->  static Java method on your Activity subclass
                          -> runOnUiThread { AlertDialog with an EditText }
                          -> on OK, call back into Rust (or stash and let Rust poll)
```

Details of the JNI plumbing, including the trap that the native context handle is the
**Application** and not the Activity, are in
[platform-glue.md](platform-glue.md#3--the-application-not-activity-trap).

Design consequences:

- The dialog is **modal and asynchronous**; the emulator must keep running or be explicitly
  paused. Never block a worker tick waiting for it.
- The value comes back as a Java `String`; validate it in Rust (see the endpoint parser in
  [storage-and-config.md](storage-and-config.md)) and show a toast on rejection rather than
  silently keeping the old value.
- Put the explanatory text in the dialog, where the OS renders it with a real font — this is
  the one place in the app where you can write a full sentence in the player's language.

## 7. Running the generator

This is the **closed-set** generator, for §2's two remaining jobs. Extracting the *game's* font is
a different tool with different inputs — `python "${CLAUDE_PLUGIN_ROOT}/tools/gen_gamefont.py"`,
documented in [game-native-ui.md §4](game-native-ui.md#4-bake-it-as-generated-source-with---check).

```bash
python "${CLAUDE_PLUGIN_ROOT}/tools/gen_glyphs.py" \
    --words "<word1>,<word2>,<word3>" \
    --font  "/path/to/font.ttf" \
    --size 12 --threshold 110 \
    --out   "<app-crate>/src/glyphs.rs" \
    --preview
```

`--preview` writes a contact sheet of every generated cell next to the output file (pass a path
to place it elsewhere). **Open it before committing** — mush at these sizes is silent. Other
useful flags: `--words-file` for a long list, `--font-index` for a face inside a `.ttc`,
`--cell-w` / `--cell-h` for non-square cells, `--skip-ascii`, and `--valign`. Run
`python "${CLAUDE_PLUGIN_ROOT}/tools/gen_glyphs.py" --help` for the full set.

Then:

1. Add `mod glyphs;` with the module documentation on the `mod` item (an `include!`d file
   cannot carry `//!` inner docs).
2. Add the no-imports warning comment inside the generated file's header template.
3. Add a `<name>_selftest.rs` at the repository root that `include!`s both the generated file
   and the module producing the strings, and asserts §5.
4. Re-run the generator whenever the word list changes; the syllable set is derived, so this is
   the only step needed.

## 8. Symptom table

| Symptom | Cause | Fix |
|---|---|---|
| A button label is blank | non-ASCII, or a character outside the 5x7 set | §1 - abbreviate, extend the set, or move to the game's own font |
| The whole overlay is in English while the game is not | you are in §1's fallback branch by accident | §1 - look for the game's font members first |
| A digit centred in a box sits too high | centred on the cell height, not on the cap height | §4 |
| A replica of a game window has visibly smaller text than the original | integer-only scale against a non-integer guest upscale | §4 |
| An ASCII character draws blank when the Latin face is a game resource | outside that face's real range; the coverage predicate assumed ASCII was free | §5 |
| A name read from guest memory is quietly a character short | an undecodable byte was dropped instead of drawn as a sentinel | §5, game-native-ui §7 |
| A hint shows a double space where a symbol should be | e.g. `=` is not in the set | §1 |
| A generated wide glyph is a solid block or empty | threshold too low / too high, or the face lacks the character | §3 - preview and re-pick |
| Wide glyphs look like mush at small sizes | rendered large and downscaled | §3 - render at target size |
| Latin text floats above the wide glyphs | top-aligned cells of different heights | §4 - bottom-align |
| Centred strings are off by a few pixels once a wide glyph appears | width computed as `len * 6` | §4 - use `mixed_text_w` |
| A newly added word draws only half its characters | glyph table not regenerated; no coverage test | §5 |
| The coverage test does not compile after adding the module | `covers` is dead code to the crate | `#[allow(dead_code)]` (§5) |
| The app hangs when the hostname dialog opens | a worker tick is blocking on the dialog result | §6 |

## 9. Checklist

- [ ] The archive was searched for the game's own font members **before** a bitmap font was
      hand-rolled, and the answer is written down.
- [ ] The font's exact character set is written down next to the font data.
- [ ] Every UI literal is ASCII within that set, and a selftest asserts it.
- [ ] Toasts are `&'static str`.
- [ ] Any CJK the host draws comes from the game's own font, or — for host-invented words only —
      from a generated closed-set table derived from a word list.
- [ ] Glyphs were rendered directly at target size and eyeballed via `--preview`.
- [ ] The generator emits Rust source, not a runtime asset.
- [ ] A selftest asserts `covers()` over the **enumerated** set of strings the host can produce.
- [ ] Mixed text is aligned on **measured** ink extents (cap height, drop) and measured with one
      width function; integer scale for host chrome, fractional for a replica.
- [ ] Names read out of guest memory decode through the composer's inverse, print a visible
      sentinel, and honour the game's in-band escapes.
- [ ] All text input goes to a platform dialog; nothing blocks a tick on it.
