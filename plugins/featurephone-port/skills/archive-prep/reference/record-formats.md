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

The answer is decided by one property — whether anything in the file, or in the guest, addresses records by **byte offset**.

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

Three conditions, all necessary:

1. **Length-prefixed** — the reader derives each field's extent from the file, not from a constant.
2. **No offset table** — nothing else in the file encodes a position.
3. **Count and order preserved** — because the *guest* almost certainly addresses records **by index**, and index means "the n-th record encountered while scanning". Insert or delete a record and every index above it shifts, silently.

Condition 3 is the one people break. Renaming a record: safe. Adding a record: not safe, even though the file parses.

**(A) A fourth, implicit condition: the prefix width bounds the value.** A `u8` length prefix means no string may exceed 255 bytes after encoding. Assert it in the writer — a 256-byte string wraps to 0 and reformats the entire rest of the file into something that still parses.

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

**Refuse to touch a record that does not look like the one you meant.** **(B)** The reference transform refuses to rename a record whose first name byte is not the expected marker (§7). A guard like this converts "the table shifted by one and I renamed the wrong thing" from a silent corruption into an exception.

**Bound every length.** Refuse a name that exceeds the prefix width after encoding (§3).

## 7. Text encoding and in-string markers

**(A)** Encode with the same codec the runtime uses. **(B)** For the reference family that is **EUC-KR**, and it is hard-wired as `file.encoding` in the vendored core — so the archive's encoding and the runtime's encoding are the same knob, and changing one without the other produces mojibake in-game while every offline test passes.

Two classes of in-string convention to look for before you edit text **(B)**, both discovered by staring at decoded strings:

- **A leading marker byte** that flags a record class (the reference table used one specific ASCII punctuation byte as the first byte of a name to mark a category of item). It is *part of the name string*, counted in the length prefix. Preserve it, and use it as the guard in §6.
- **An in-band control character** used as the game's line break inside display text (the reference table used a single ASCII punctuation byte). Your replacement text must use it too, and must respect whatever width the UI has. There is no escape mechanism; the byte is the control.

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

**(A) Decoded assets are still licensed content.** A decoded sprite atlas or a dumped string table is as unpublishable as the archive it came from. Guard them by name in `.gitignore`, keep them under `work/`, and keep `work/` outside anything the build packages.

## 10. Checklist

- [ ] Family identified (length-prefixed vs offset-table/fixed-stride) and confirmed, not guessed.
- [ ] Layout found by brute-force with `consumed == filesize` and a plausible record count.
- [ ] Layout cross-checked against the guest's reader, or explicitly marked as a single-derivation hypothesis.
- [ ] Record count and order preserved by the edit.
- [ ] Every string within the prefix's bound after encoding.
- [ ] Unknown fields copied verbatim from the source slice.
- [ ] A guard that refuses to edit a record that does not match its expected marker.
- [ ] Correct codec, matching the runtime's `file.encoding`.
- [ ] In-band markers and line-break bytes preserved; replacement text checked against the widget width.
- [ ] Encrypted members untouched.
- [ ] Re-parse after write; changed-index set matches exactly.
