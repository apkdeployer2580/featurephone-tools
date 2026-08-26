# In-jar record formats

How to decide whether a data member inside the jar can be safely rewritten, how to recover its layout without documentation, and how to prove your parser is right before you write anything back.

Tags: **(A)** generic · **(B)** worked example from the reference port · **(C)** do not copy.

## Contents

1. [The question this file answers](#1-the-question-this-file-answers)
2. [The two layout families](#2-the-two-layout-families)
3. [The safety theorem for length-prefixed records](#3-the-safety-theorem-for-length-prefixed-records)
4. [Recovering an unknown layout](#4-recovering-an-unknown-layout)
5. [The consumed-equals-filesize rule](#5-the-consumed-equals-filesize-rule)
6. [Writing records back](#6-writing-records-back)
7. [Text encoding and in-string markers](#7-text-encoding-and-in-string-markers)
8. [Encrypted members](#8-encrypted-members)
9. [Sprites and other non-record members](#9-sprites-and-other-non-record-members)
10. [Checklist](#10-checklist)

---

## 1. The question this file answers

Before you edit any member of the jar: **is this file's layout tolerant of a length change, or not?** Get this wrong and the archive still boots, the file still parses, and the game shows garbage for the next fifty records. There is no crash to tell you.

The answer is decided mostly by one property — whether anything in the file, or in the guest, addresses records by **byte offset** — and partly by a second one that is invisible in the file and lives only in the guest's parser: whether some field's *length* is computed by scanning for a byte your edit is about to remove (§3).

## 2. The two layout families

**(A) Family 1 — length-prefixed, sequentially scanned, no offset table.**

```
[header]
record 0: [len:u8][bytes...][fixed fields][len:u8][bytes...][trailer]
record 1: ...
```

The parser walks forward, consuming each field using the length that immediately precedes it. Nothing stores "record 7 begins at 0x412". **Length changes are safe.**

**(A) Family 2 — offset-table or fixed-stride.**

```
[count:u16][off0:u32][off1:u32]...[blob]
```
or
```
[count:u16][record 0: exactly 24 bytes][record 1: exactly 24 bytes]...
```

Here a record's position is data, either explicitly (offset table) or implicitly (stride × index). **Length changes are illegal** unless you rewrite the table too — and if the *guest* computes positions with a hardcoded stride, even rewriting the table is not enough.

**How to tell them apart in 60 seconds:**

- Does the first 32–64 bytes look like a run of monotonically increasing 16/32-bit values that land inside the file? → offset table, Family 2.
- Divide the file size (minus a small header) by plausible record counts. Does an integer stride fall out? → probably Family 2.
- Do you see `[small byte][that many bytes of plausible text][small byte][that many bytes]…` marching cleanly to EOF? → Family 1.

Confirm with §5, never with a glance.

## 3. The safety theorem for length-prefixed records

**(A) The load-bearing insight**, and it is worth stating precisely because it is what makes variable-length text edits legal at all:

> Every string is LENGTH-PREFIXED and there is no offset table in the file, so changing a string's length is safe **as long as the record COUNT and ORDER are preserved**.

**Five** conditions, all necessary. The first three are the classic statement of the theorem; the fourth used to be stated wrongly in this file and the fifth was missing entirely.

1. **Length-prefixed** — the reader derives each field's extent from the file, not from a constant.
2. **No offset table** — nothing else in the file encodes a position.
3. **Count and order preserved** — because the *guest* almost certainly addresses records **by index**, and index means "the n-th record encountered while scanning". Insert or delete a record and every index above it shifts, silently.

Condition 3 is the one people break. Renaming a record: safe. Adding a record: not safe, even though the file parses — but it *can* be made safe, and the way to do it is below.

**(A) A fourth condition: the value is bounded by the READER'S LOAD INSTRUCTION, not by the prefix's storage width.** A one-byte prefix does not mean "up to 255". It means "whatever the guest's load of that byte can represent", and the two answers differ by a factor of two. Find the instruction that reads the prefix in the guest's own parser, and assert *that* ceiling in your writer with the instruction named in the failure message.

> **REFUTED.** *Previously documented in this file:* "A fourth, implicit condition: the prefix width bounds the value. A `u8` length prefix means no string may exceed 255 bytes after encoding. Assert it in the writer — a 256-byte string wraps to 0 and reformats the entire rest of the file."
> *Refuted by:* disassembling the reference title's own item-table loader instead of reasoning from the file format. **(B)** Both length loads in it — one for the name, one for the effect text — are **signed** byte loads, so the real ceiling is **0x7f**. At 0x80 the length does not wrap to zero; it reads back **negative** and is handed to the allocator like that.
> *Why the wrong reading was attractive:* the storage width is visible in a hex dump and the load width is not, so "`u8` ⇒ 255" is the answer you reach without opening a disassembler — and it *looks* conservative, which is exactly what stops anyone re-checking it.
> *Consequence if carried forward:* your writer accepts a 0x90-byte name, every offline test passes, the file re-parses cleanly, and the game dies at an allocation failure with a negative size, arbitrarily far from the table you edited. **(B)** In the reference port the writer's verify pass now fails with the reason spelled out — *"the loader reads the length with a signed byte load, so 0x7f is the ceiling"* — because the number alone would have been tidied back to 255.

**(A) A fifth condition: the framing the parser depends on is preserved.** This is the one that no round-trip test you can write will ever catch, so it gets its own subsection.

### The framing invariant: the byte the parser scans for

**(A) A guest renderer can depend on a property of the data that no code enforces and that the publisher's shipped records merely happen to satisfy.** The commonest shape is a field whose *length* the guest computes by **scanning for an in-band delimiter with no bound and no NUL test**. Delete the delimiter and the scan runs off the allocation into the heap and stops at the first matching byte it finds there — so the outcome depends on **allocation history**: intermittent, different records each session, and latent across releases.

**(B) The worked case, and the method that produced it.** The method is: take the field the report complains about, find the code that computes its *length* (not the code that draws it), and read that loop's exit test. In the reference port the loop's only exit test was the delimiter byte; the count was then truncated to a **signed 16-bit** value and the drawer returns immediately on a non-positive count. That gives a two-branch outcome table which explains the whole symptom:

| first delimiter found | count | what the player sees |
|---|---|---|
| inside the string (correct data) | small positive | the description, correctly |
| somewhere in the heap, within ~32 KB | positive | a description that looks perfectly normal (the drawer stops at the string's own NUL) |
| more than 0x8000 bytes out | negative after truncation | **an empty field**, with no error anywhere |

The report that arrives is *"some items show a blank description, different ones each session, but the detail popup is fine"* — which reads as a font or layout bug, and is unfalsifiable by inspecting your tool's output. The edit that caused it had shipped green two session-blocks earlier.

**(A) How to know an invariant exists at all: check the property across ALL pristine records, as a set.** If 100 % of the shipped records satisfy it, treat it as an invariant the renderer is entitled to assume, and make your writer enforce it for records you add. **(B)** All 15 stock records of the affected table carried exactly one delimiter, verified against two independent pristine copies (the pristine backup, and the same table from a different carrier's archive) — two derivations, not one reading.

**(A) Safety is per code path and per record category, not per file.** The same visual field, on the same screen, can be drawn by different code with different bounding rules depending on the record's category: in the reference title one category's description went through the unbounded delimiter scan while another's went through a plain `strlen` path on the same screen and the same common tail. That is *why* the delimiter-free records had always been safe — and generalising from them ("these other records don't have it and they're fine") is the exact reasoning that removed the delimiter in the first place.

**(A) Fix at the DATA layer when the code layer is duplicated.** The unbounded idiom is normally copy-pasted across every screen that shows the field. **(B)** Here it existed at eight sites with two arms each: one preserved byte covers all sixteen, a code patch would have needed sixteen hand-assembled sites. Choosing the layer with the smaller blast radius is the whole decision, and the cheaper layer is the one people are more afraid of.

**(A) The tool's duty, in two halves:**

- **Constructive:** an `ensure_<invariant>()` helper that appends the delimiter to any record that lacks one. It is a no-op on today's table and a guard for tomorrow's edit.
- **Assertive:** check it **per output record**, in the writer's verify pass, and reject the *degenerate position* too — a delimiter at index 0 yields length 0, which draws nothing just as reliably as a negative length does. **(B)** The reference writer checks three things on every row: delimiter present, not at index 0, prefix within the signed-load ceiling of the fourth condition above.

**(A) The general rule:** before removing **any** byte from a data member, enumerate every consumer that *scans* for it — not the ones that print it.

### Adding a record is a two-part change, and the halves travel by different routes

**(A)** Condition 3 says adding a record is unsafe. It is *legal* only if the guest constants that bound the table change in the same change — typically a per-category row count, the table's hardcoded allocation size, and any teardown or free bound. No reader range-checks the index, so serving a new index without those constants is a wild pointer on the first list draw.

**(A) The trap is not the arithmetic, it is the logistics:** the data lives in a gitignored archive and the constants live in a committed patch table, so **the two halves travel by different routes** and desynchronise across machines and checkouts. The mismatch presents as an emulator crash long after boot, which looks nothing like an asset problem. **(B)** In the reference port that desync shipped in ten consecutive releases. The gate that closes it is a byte-comparing staleness check — [generated-assets.md §9](generated-assets.md#9-the-asset--guest-constant-pair-two-halves-two-routes).

## 4. Recovering an unknown layout

**(A) Parametrise, then brute-force.** Assume the shape

```
[header: H bytes]
repeated:
   [len:u8][name: len bytes]
   [fixed fields: F bytes]           # e.g. a u32 numeric field
   [len2:u8][text: len2 bytes]
   [trailer: T bytes]                # unknown fields you will not touch
```

and search over `H`, `F` and `T`. **(B)** The reference port's parser tried `T in range(4, 12)` and accepted the first `T` for which the parse consumed the file **exactly** and produced a plausible record count. It found `T = 6` for one table family and `T = 7` for another — *in the same game, in the same directory*. The trailer size is per-file, not per-title. Auto-detect it for every file; never carry it over.

Run: `python "${CLAUDE_PLUGIN_ROOT}/tools/bruteforce_records.py" work/<table> --prefix u8len --field price:u32le --trailer 4..12 --encoding euc-kr --min-records 8`

**(B) A worked layout, tagged as an example, not a spec:**

```
u16 header
per record:  namelen:u8, name[namelen], value:u32 LE, textlen:u8, text[textlen], trailer[T]
```

The way that layout was *found* is the transferable part: dump the file as hex, notice that byte 2 is a small number followed by exactly that many EUC-KR bytes, notice the same pattern repeating, and let the brute-forcer settle the two unknowns (`H`, `T`). Do not start from someone else's `(H, F, T)`.

**Cross-check against the guest.** Once you have a candidate layout, find the guest routine that reads the file and confirm the field widths in the disassembly (`guest-re`'s static toolkit: who loads this table, who indexes it). Two independent derivations of a layout is the same evidence standard as two independent derivations of a struct offset. A layout that parses cleanly *and* matches the reader's load widths is settled; one that only parses is a hypothesis.

## 5. The consumed-equals-filesize rule

**(A) The single most valuable assertion in all asset work:**

```python
assert cursor == len(data), f"parser consumed {cursor} of {len(data)} bytes"
```

A parser that stops early has found *a* self-consistent segmentation, not *the* segmentation. A parser that runs past the end has the field widths wrong. Neither one throws on its own. Without this assertion you will ship plausible garbage, and the failure will surface as an in-game text corruption you attribute to encoding.

Corollaries:

- **Also assert the record count is plausible** — a layout that consumes the file with 3 records when you expected ~200 is wrong.
- **Also assert every decoded string round-trips** through the encoding. A misparse frequently yields text that decodes with replacement characters; treat a single replacement character as a parse failure, not a data quirk.
- **Re-parse after writing** and compare record-for-record (V1/V2 in [repacking.md](repacking.md#4-verification-is-the-deliverable)). Reader and writer disagreeing is the most common way a repack goes wrong.

## 6. Writing records back

**(A) Rebuild the file; never splice bytes at an offset.** Even in Family 1, a splice that changes a length must also rewrite the prefix, and doing that by hand is how off-by-ones enter.

Three writer rules, all of which the reference implementation follows and all of which earn their place:

**Copy unknown fields verbatim by slicing the source.** The fixed numeric field and the trailer are re-emitted from the original bytes, not re-encoded from a parsed value:

```python
value_bytes = src[rec.off + 1 + len(rec.name) : rec.off + 1 + len(rec.name) + 4]
out += value_bytes            # not struct.pack("<I", rec.value)
out += rec.trailer            # unchanged
```

You do not know what is in the trailer. Round-tripping the bytes means an unknown field with unknown semantics survives your edit intact. Re-encoding a value you *think* you parsed is how you discover the field was big-endian.

**Refuse to touch a record that does not look like the one you meant.** **(B)** The reference transform refuses to rewrite a record whose name does not begin with the expected in-band byte (§7). A guard like this converts "the table shifted by one and I renamed the wrong thing" from a silent corruption into an exception.

**Bound every length.** Refuse a name that exceeds the ceiling the *reader's load instruction* imposes, after encoding (§3) — not the prefix's storage width.

### Assert that every intended edit LANDED

**(A) The guard above has a mirror image, and you need both.** Refusing the wrong record stops a corruption; nothing yet stops a **silent no-op**. An override table keyed by index alone rewrites whatever row happens to sit there when it is pointed at another carrier's table or a later revision of the same one — and a key that matches nothing produces a clean `VERIFY OK` over completely unchanged content.

Two rules, cheap to implement together:

- **Key every override by index AND an expected identity field**, and die if the identity does not match. "Record 17" is not an identity; "record 17, whose name is X" is.
- **Have `verify()` walk every override key and assert the output actually carries the new value.** **(B)** The reference tool's loop for this is commented with the failure it prevents — the same silent-revert class as the multi-tool accidents in [generated-assets.md](generated-assets.md), *one layer down*: there the tool never ran, here the tool ran and changed nothing.

A transform that cannot fail is a transform whose report means nothing.

## 7. Text encoding and in-string markers

**(A)** Encode with the same codec the runtime uses. **(B)** For the reference family that is **EUC-KR**, and it is hard-wired as `file.encoding` in the vendored core — so the archive's encoding and the runtime's encoding are the same knob, and changing one without the other produces mojibake in-game while every offline test passes.

Two classes of in-string convention to look for before you edit text **(B)**, both discovered by staring at decoded strings — and then, crucially, by finding the code that *consumes* them:

- **A punctuation byte at the front of a string** is usually an **in-band text-colour escape**, not a record-class flag. **(A)** Expect a short alphabet of such bytes held in rodata with a parallel RGB table beside it, and expect exactly **two** readers image-wide, both in the font layer: a zero-width predicate used by width measurement and line wrapping, and one that sets the pen colour and advances the index *without drawing*. Escapes of this kind are valid at **any** position — the game embeds them mid-string itself. Preserve the byte, and it still makes the §6 guard; just do not read meaning into which records carry it.
- **An in-band control character** used as the game's line break, or as the delimiter a length scan terminates on. **(A)** Treat it as a **load-bearing invariant, not a convention** — deleting it is the failure documented in [§3's framing subsection](#3-the-safety-theorem-for-length-prefixed-records), which is intermittent and allocation-dependent. Your replacement text must use it too, and must respect whatever width the UI has. There is no escape mechanism; the byte is the control.

> **REFUTED.** *Previously documented in this file:* "A leading marker byte that flags a record class — the reference table used one specific ASCII punctuation byte as the first byte of a name to mark a category of item."
> *Refuted by:* the data, and then the code. **(B)** The same marker appears on a record that never had the property it was supposed to mark, and a *different* marker on one that did; statically, the byte belongs to a 7-byte punctuation alphabet in rodata with an RGB table immediately before it, and **no site anywhere in `.text` compares against the individual bytes**. It is a colour escape.
> *Why the wrong reading was attractive:* on the records anyone looks at first, the correlation is perfect — the marked records really were all one category, because that category's names were all drawn in one colour. A private-server notation convention then re-encoded the guess as a fact, and the guess was never taken to a consumer.
> *Consequence if carried forward:* you filter or classify records by a byte that means "draw the rest of this in colour N", and your host UI prints a stray punctuation character in the middle of any string that colours itself mid-line. Rendering both behaviours host-side belongs to [game-native-ui.md](../../android-host/reference/game-native-ui.md).

**(A) The transferable rule:** an in-string marker's meaning comes from **the code that consumes it**, never from correlating the records that carry it. There are usually only one or two consumers, and finding them is an afternoon.

**(A)** Before writing any replacement text, render it: the guest's font is fixed-width per script and the widget has a hard width. A string that fits in your editor may be clipped or may overflow a fixed-size destination buffer in the guest. If the destination is a fixed array, an over-long string is a memory-safety bug in the *guest*, triggered by *your* data.

## 8. Encrypted members

**(A)** Some data members are encrypted. You will notice because the brute-forcer finds no layout that satisfies §5 and the bytes have uniform high entropy while their neighbours in the same directory parse cleanly.

**Encrypted members are out of scope for this skill and for its tooling.** No tool here takes a `--key`, has a key default, or documents one. Extracting and publishing a key that decrypts a copyrighted work is a materially different act from publishing an offset or a file layout, and a public methodology must not carry one. If you hold a key for a title you own, that is yours to handle privately.

Practical consequence: **plan around the encrypted member.** **(B)** In the reference port exactly one table in an otherwise-plaintext directory was encrypted; the port simply never edited it and reassigned behaviour host-side for the affected records instead. That is usually available — the host can override behaviour, and only *text* is trapped inside the archive.

## 9. Sprites and other non-record members

**(A)** The same discipline, three extra traps:

- **Padded stride vs visible width.** Image rows are frequently padded to a byte or word boundary. A decoder that assumes `stride == width` produces a sheared image that still "looks like" the sprite for narrow frames and falls apart for wide ones. Derive stride from `total_bytes / height` and compare it to width.
- **Palette lives in a sibling file.** Indexed images reference a palette member by naming convention, not by embedded reference. Decode both or you get a monochrome mess.
- **Do not repack sprites to change art.** Almost every visual change a port wants (HUD, overlays, a reimplemented widget) is better done host-side, where it is reversible and does not touch licensed bytes. Decode sprites to *understand* the guest's own rendering — for example to verify a host reimplementation against the guest's framebuffer — not to ship modified art.

### The highest-value reason to decode is the one that needs no repack at all

**(A) Decode the game's UI chrome sheet to MEASURE the window style your host panels must obey, and decode its icon atlas to REUSE the game's own icons in host UI.** Both are pure read paths, both leave the archive untouched, and together they are what makes a host window indistinguishable from one of the game's.

Two things make this cheap, and both are easy to miss:

- **The chrome usually lives in the same member family as the icons**, so the decoder you already wrote reads it with no changes. **(B)** In the reference port the chrome sheets were decoded by an icon decoder written many sessions earlier — the capability cost zero new reverse engineering, and the only reason it was late is that nobody inventoried the decoders they already had against a visual question.
- **You can prove a style is global instead of arguing about taste.** Compare the same corner across two frames of very different sizes in one sheet: byte-identity of the corner is the proof. **(B)** Two frames sized 19×25 and 85×26 had byte-identical top-left corners — a 2-pixel 45° cut, a 1-px dark outline, a light bevel with a darker shadow-side variant — which settled that the game never draws a square corner anywhere. If a third frame's corner differs, the style is not global and you have learned that instead.

Drawing with what you measured — window kinds, nesting, scaling, and the game's own bitmap font — belongs to [host-windows.md](../../android-host/reference/host-windows.md) and [game-native-ui.md](../../android-host/reference/game-native-ui.md). The rule *here* is only that this is a **read** of the archive, never a rewrite of it.

**(A) Decoded assets are still licensed content.** A decoded sprite atlas or a dumped string table is as unpublishable as the archive it came from. Guard them by name in `.gitignore`, keep them under `work/`, and keep `work/` outside anything the build packages.

**(A) And "licensed content" survives a change of file extension.** A generated *source file* carrying the game's font bitmaps, or a packed atlas blob committed for a compile-time include, is the same bytes laundered through a different suffix — invisible to an ignore rule written for archives and `work/`. The resolution, stated once and owned by [generated-assets.md §12](generated-assets.md#12-every-generator-gets-a---check): **an artifact derived from the archive is a build product — gitignored, regenerated at build time, with a `--check`. Only a generator whose output derives from your own source may commit its output.** The publishable artifacts are the generator and the format notes.

## 10. Checklist

- [ ] Family identified (length-prefixed vs offset-table/fixed-stride) and confirmed, not guessed.
- [ ] Layout found by brute-force with `consumed == filesize` and a plausible record count.
- [ ] Layout cross-checked against the guest's reader, or explicitly marked as a single-derivation hypothesis.
- [ ] Record count and order preserved by the edit — or, if a record was added, the guest constants that bound the table changed **in the same change**.
- [ ] Every string within the ceiling **the reader's load instruction** imposes, after encoding — the disassembled load, not the prefix's storage width.
- [ ] Every framing invariant the parser scans for is present in every output record, **not at index 0**, and was established by checking all pristine records as a set.
- [ ] Every consumer that *scans* for a byte you removed has been enumerated.
- [ ] Unknown fields copied verbatim from the source slice.
- [ ] A guard that refuses to edit a record that does not match its expected identity — **and** a verify pass asserting every intended edit actually landed.
- [ ] Correct codec, matching the runtime's `file.encoding`.
- [ ] In-band markers preserved; their *meaning* taken from the code that consumes them, not from which records carry them; replacement text checked against the widget width.
- [ ] Encrypted members untouched.
- [ ] Re-parse after write; changed-index set matches exactly.
- [ ] Anything you generated *from* the archive is a gitignored build product with a `--check`, not a committed blob.
