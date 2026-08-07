# Decoding the game's assets

The other half of a remake. Your host overlay needs item names, icons, text and tables that live inside the archive in undocumented formats. A decoder that "looks about right" is the most expensive artefact in this whole methodology, because it produces **plausible garbage** that nobody questions for months.

## Contents

1. [The one non-negotiable rule](#1-the-one-non-negotiable-rule)
2. [A case study in plausible garbage](#2-a-case-study-in-plausible-garbage)
3. [Sprite sheets: stride is not width](#3-sprite-sheets-stride-is-not-width)
4. [Record databases: parametrise and brute-force](#4-record-databases-parametrise-and-brute-force)
5. [Text tables and encodings](#5-text-tables-and-encodings)
6. [Encrypted members are out of scope](#6-encrypted-members-are-out-of-scope)
7. [Generating host assets from a closed set](#7-generating-host-assets-from-a-closed-set)
8. [Rewriting a member in place](#8-rewriting-a-member-in-place)
9. [Checklist](#9-checklist)

---

## 1. The one non-negotiable rule

> **Assert that your decoder consumed exactly the file, and assert the format's own arithmetic at every record.**

```python
assert off == len(data), f"decoder consumed {off} of {len(data)} bytes"
assert frame_len == 9 + palcnt * 2 + pixel_bytes, f"frame {i} arithmetic does not close"
```

Two assertions, and they catch essentially every wrong-format hypothesis:

* **`consumed == filesize`** catches a stream read at the wrong offset, a missing header field, an extra trailer, and an off-by-one in a length prefix. A decoder that stops early or runs past the end is wrong *even if every record it produced looked fine*.
* **Per-record arithmetic that closes** (`declared_length == computed_length_from_the_fields`) catches a misidentified field, a wrong element size, and an endianness error. Anything that does not close is a hypothesis, not a format.

A decoder without these two assertions **will** silently produce output that renders as noise, and the noise will be blamed on alignment, on scaling, on the palette — on anything except the parser. That is not hypothetical; see below.

---

## 2. A case study in plausible garbage

A pre-existing sprite decoder for one real title treated the halfword `0xF81F` as a **frame marker**.

It is not a marker. It is `palette[0]` — the transparency sentinel — and it is **counted by the palette count field**. So the decoder:

* skipped a palette entry it should have kept, making every pixel index resolve to `palette[k+1]`;
* consequently read the stream **2 bytes late** from that point onward.

The output was recognisably sprite-shaped and wrong. For a long time the result was dismissed as "an alignment issue". Nobody re-derived the format, because the images looked like they were nearly working.

**Three lessons (A):**

1. `0xF81F` is magenta in RGB565 and is a *conventional transparency colour*, not a structural token. A value that is meaningful as data will be misread as structure by anyone in a hurry.
2. **"Nearly working" output is the failure mode to fear.** Total garbage gets fixed in an hour.
3. **`assert off == len(data)` would have caught it on the first run.** The stream would have ended 2 bytes short per frame and the assertion would have fired immediately.

---

## 3. Sprite sheets: stride is not width

Two dimensions, and they are routinely different:

| name | meaning |
|---|---|
| `w` — **stride** | bytes/pixels per row *as stored*, padded to a byte or word boundary |
| `cw` — **visible width** | the pixels the game actually draws |

At 4 bits per pixel a 7-pixel-wide sprite occupies 4 bytes per row, i.e. a stride of 8 pixels. Decode with `cw` and every row shifts by one pixel; decode and *display* with `w` and you get a column of junk on the right. Keep both fields, name them differently, and use each where it belongs.

Other things to pin before believing a sprite decoder:

* **Which corner is the origin**, and whether rows are stored bottom-up.
* **Whether the palette is per-frame or per-sheet.** Per-frame palettes are common in feature-phone formats because they let each sprite carry only the colours it uses.
* **Whether index 0 is transparency by convention or by a flag.**
* **What the frame count is, and whether it is stated or implied by EOF.** If implied, your `consumed == filesize` assertion is the only thing that verifies it.

**Cross-validate against the game's own draw call.** Recipe E in [recipes.md](recipes.md) is the strongest form: find the draw site, read the frame index and the width/height it passes, and confirm your decoder produces exactly those dimensions for exactly that frame. Two independent derivations, per Rule 2.

---

## 4. Record databases: parametrise and brute-force

Item, monster and skill tables are usually length-prefixed record streams with a small header and a fixed trailer per record. You will not guess the trailer size, and you do not have to.

**The technique (A):** parametrise `(header_len, prefix_kind, trailer_len)`, then **search the parameter space and keep the configuration that (a) yields the most records and (b) consumes the file exactly**.

```bash
python "${CLAUDE_PLUGIN_ROOT}/tools/bruteforce_records.py" work/dat/items.bin \
  --prefix u8len --field price:u32le --trailer 4..12 --encoding euc-kr --min-records 8
```

Make it robust with a **sanity predicate** on the decoded fields — for a name field: non-empty, no control characters, decodes cleanly in the declared encoding, length within a plausible range. A configuration that produces 400 records of which 380 fail the predicate is not the right configuration even if it consumed the file.

Two further findings that generalise **(B, method A)**:

* **One file can carry two on-disk layouts.** A format that grew across game versions may switch shape partway, or use a different layout for a different category. If your best configuration explains 80% of the file and then desynchronises, look for a second header rather than forcing one parse.
* **Leading marker bytes often encode a category** — rarity, tier, shop availability. They look like padding until you tabulate them against records whose in-game properties you know.

---

## 5. Text tables and encodings

### The packed table

A very common shape **(B)**:

```
u16 total_len
u16 count
u16 offsets[count]      // relative to the start of the blob
<encoded text>
```

Decode it once, commit an `id <TAB> text` listing, and keep the regenerator script in your repo so the table survives an asset change. That listing is what turns [recipes.md](recipes.md) Recipe B from archaeology into a lookup.

### Encodings

Feature-phone titles predate UTF-8 in practice. Determine the encoding **before** writing any decoder, and put it in `target.toml`:

| region | likely | lead-byte class for run detection |
|---|---|---|
| Korea | EUC-KR (CP949) | `[\xa1-\xfe][\xa1-\xfe]` |
| Japan | Shift-JIS | `[\x81-\x9f\xe0-\xef][\x40-\xfc]` |
| China | GBK | `[\x81-\xfe][\x40-\xfe]` |
| Taiwan | Big5 | `[\xa1-\xf9][\x40-\xfe]` |

Two failure modes to expect:

* **A wrong encoding makes a title look like it has no strings.** ASCII `strings` over a CJK image returns fragments and punctuation, and it is easy to conclude the text lives elsewhere.
* **A right encoding with a wrong run detector splits every string.** Multi-byte trail bytes overlap the ASCII range, so a naive `[\x20-\x7e]+` scan cuts strings at every trail byte that happens to be printable.

### Round-trip before you rewrite

If you intend to *edit* text (`archive-prep`'s repack path), first prove you can **decode and re-encode a member byte-identically**. A round-trip that is not byte-identical means your understanding of the format is incomplete, and any edit you make on top of it will corrupt something you have not noticed.

Variable-length text edits are legal when the format has **no offset table**; when it has one, you must rewrite the offsets too, and your round-trip test is what proves you did it right.

---

## 6. Encrypted members are out of scope

Some members are encrypted. You will recognise them immediately: uniformly high entropy, no recognisable header, no strings, and a `consumed == filesize` decoder that can never be written.

**This plugin does not ship, document, or default any extracted decryption key**, and no worked example here contains one. Publishing a key that decrypts a copyrighted work is a materially different act from publishing a struct offset. If you hold a key for your own dump, that is yours to handle privately; the tooling here takes no `--key` argument.

**Route around it instead.** Everything the host needs from an encrypted record can usually be obtained by driving the game's own read path — see [recipes.md](recipes.md) Recipe A and [calling-guest-code.md](calling-guest-code.md). The guest already knows how to decrypt its own data; make it do the work and read the plaintext out of its heap.

---

## 7. Generating host assets from a closed set

**The problem.** Your overlay's built-in font is 5x7 ASCII: uppercase Latin, digits, and a handful of punctuation. A CJK label renders as **blank space** — not tofu, not a box, *nothing*. A missing glyph is an invisible hole, so it does not look like a bug; it looks like a layout mistake.

**The insight (A).** The host draws a **closed, known set** of strings — your own labels, toasts and status lines. A full Hangul font is ~11,172 syllables and unshippable. The set you actually need is often under twenty glyphs.

**The generator:**

```bash
python "${CLAUDE_PLUGIN_ROOT}/tools/gen_glyphs.py" \
  --words "<word1>,<word2>,<word3>" --font <system.ttf> --size 12 \
  --threshold 110 --out src/glyphs.rs --preview
```

1. Declare the **words**, not the glyphs. Derive the syllable set from the word list, so adding a word cannot forget a glyph.
2. **Render directly at the target size. Do not oversample and downscale.** A hinted screen face deliberately puts strokes on the pixel grid; resampling averages that into grey which thresholds to mush. Below ~12 px most faces lose strokes entirely — test and pick the smallest size that survives.
3. Emit **Rust source**, not a runtime asset: a `const GLYPHS: [(char, [u16; N]); M]` table plus a `covers(&str) -> bool` predicate.
4. Print ASCII art of every glyph for eyeball review before you commit it.
5. **Assert coverage in a test** over every string your UI can produce. Because the renderer silently skips unknown glyphs, only a test can turn "invisible hole" into "build failure".

The generated file participates in the dependency-free selftest harness — it is `include!`d, not linked, so mark selftest-only helpers `#[allow(dead_code)]`. Convention: `${CLAUDE_PLUGIN_ROOT}/shared/selftest-convention.md`.

**Boundary:** this solves text *output*. Text **input** (hostnames, transfer codes) goes to the platform's own dialogs over JNI — an overlay font has no input machinery and building one is not worth it.

---

## 8. Rewriting a member in place

When you must change a data member (item text, a table) inside the shipped archive, the mechanics belong to `archive-prep`. Two properties matter enough to state here because a decoder author is usually the one doing it:

* **Rebuild entry by entry, preserving each surviving member's original compression type and attributes**, then **re-read and re-parse the result** and byte-compare every member you did not intend to change. A reference repack of a nested archive verified 1274 inner members and 6 outer entries byte-identical except the one file.
* **Size is not a valid check.** Two archives of the same size can differ in every member. Compare member bytes.

---

## 9. Checklist

Before you trust a decoder:

- [ ] `assert consumed == len(data)` at EOF.
- [ ] Per-record arithmetic asserted (`declared == computed`), not just used.
- [ ] Stride and visible width are separate named fields.
- [ ] Encoding declared in `target.toml`, and the run detector matches that encoding's lead/trail classes.
- [ ] A sanity predicate rejects implausible decoded fields, and you counted the rejects.
- [ ] Round-trip byte-identical, if you intend to write the format back.
- [ ] Cross-validated against the game's own use of the asset (a draw call's dimensions, a record count the game displays) — Rule 2.
- [ ] No key, no `--key` flag, no encrypted member in scope.
- [ ] Generated host assets have a coverage test, because a missing glyph is invisible.
