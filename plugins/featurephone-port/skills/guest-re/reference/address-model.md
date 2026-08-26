# The address model — deriving it for a binary nobody has looked at

Four constants turn a bag of bytes into a navigable image. Every static tool in this plugin is wrong until all four are right, and three of the four are silently wrong in ways that still produce plausible output. Derive each by **at least two independent routes** and record which routes agreed.

## Contents

1. [What the four constants are, and the order to pin them](#1-the-four-constants)
2. [IMAGE_BASE — where the loader puts the image](#2-image_base)
3. [BSS_SIZE — the writable statics that are not in the file](#3-bss_size)
4. [SL_FO — the RWPI static base](#4-sl_fo--the-rwpi-static-base)
5. [RELOC_DELTA — and the unit trap](#5-reloc_delta--and-the-unit-trap)
6. [The god-object slot](#6-the-god-object-slot)
7. [Validation: when is the model pinned?](#7-validation--when-is-the-model-pinned)
8. [Failure modes and what each looks like](#8-failure-modes)
9. [When none of this applies — and the second address model](#9-when-none-of-this-applies--and-the-second-address-model)

---

## 1. The four constants

```toml
image_base  = 0x100000        # guest_address = file_offset + image_base
bss_size    = 64000           # zero-filled writable statics appended after the image
sl_fo       = 0xb2c40         # RWPI static base, expressed as a FILE OFFSET
reloc_delta = 0xeec           # stored_word + reloc_delta  ->  a file offset OR a guest address
reloc_unit  = "file_offset"   # which one. Get this wrong and every tool lies quietly.
```

The values above are **(B)** — one real KTF title. `image_base` is a property of the *loader*, so it is shared by every KTF title; the other three are properties of *this* binary and must be re-derived.

**⚠ First check that your image *has* these constants.** All four describe a **flat, self-rebasing** image: one blob mapped at one base, statics reached through `sl`, stored pointers carrying a link-time bias. A second, equally common shape — a **fully linked ELF with live section headers** — has **none of the four**, and the model collapses to one line. The check takes ten seconds: does the native member begin with `\x7fELF`, and does a section table print? If so, go to [§9.1](#91-the-second-address-model-a-section-mapped-elf) now rather than spending a day fitting a relocation delta to an image that has none.

Pin them in this order. Each one is used to validate the next:

```
image_base ──> lets you disassemble at all
     │
     └─> bss_size ──> tells you which "out of range" addresses are actually fine
            │
            └─> sl_fo ──> lets you read statics offline
                   │
                   └─> reloc_delta ──> lets stored pointers resolve to strings
                          │
                          └─> god_object ──> lets you reach live state
```

---

## 2. IMAGE_BASE

**Definition.** `guest_address = file_offset + IMAGE_BASE`, for the loaded native image.

This is **a property of the emulator's loader, not of the binary**. Do not derive it — read it.

### Route 1 (authoritative) — the loader crate

Grep the platform backend for the call that maps the image:

```
core.load(data, IMAGE_BASE, data.len() + bss_size as usize)?;
```

`IMAGE_BASE` is a `pub const` a few files up. For the KTF backend it is `0x100000` **(B)**. Other backends (LGT, SKT/SKVM) use their own; a pure-Java MIDlet has no such constant at all.

### Route 2 — the entry call

The same crate runs the guest's entry point right after loading:

```
core.run_function(IMAGE_BASE + 1, &[bss_size]);   // +1 is the Thumb bit
```

Two facts fall out of this line and both matter later:

* **The entry point is file offset 0.** Whatever the image's first instruction is, it lives at offset 0 and is Thumb.
* **The guest self-rebases at that entry point.** It is handed `bss_size` and expected to fix its own pointers up. The relocation delta in §5 exists because of what that code does.

### Route 3 (validation) — PC-relative consistency

Disassemble a function you can already see and pick any `ldr rX, [pc, #imm]`. The literal pool word it loads should, under your candidate model, resolve to something meaningful (a string, a plausible pointer, a small constant). If your `IMAGE_BASE` is off, PC-relative *loads* still work — they are self-relative — but every **absolute** interpretation fails. This is why `IMAGE_BASE` errors hide: a disassembler with the wrong base still produces a perfectly readable listing and only the annotations are nonsense.

**Convention to adopt everywhere: tools print file offsets, not guest addresses.** File offsets are what your disassembler, your hex editor and your `grep` all consume. Any address a tool prints — including a runtime profiler's sampled PC — should be printed as `pc - IMAGE_BASE` so it pastes straight back in. **(A)**

---

## 3. BSS_SIZE

**Definition.** Zero-filled writable static storage the loader appends *after* the file image, so the mapped region is `len(file) + bss_size`.

### Route 1 — the native member's filename (KTF)

The archive's native member is named `client.bin<N>` and **`<N>` is the BSS size in bytes**: `client.bin64000` → 64000 **(B)**. The loader parses it out of the jar entry name. There is no header field; rename the member and the game gets the wrong amount of writable memory.

Consequences you must internalise:

* **An address past EOF is not automatically invalid.** Valid guest addresses run `[IMAGE_BASE, IMAGE_BASE + len(file) + bss_size)`. A pointer into that tail is a pointer into zero-initialised statics, and your offline tools cannot read it because the bytes are not in the file.
* **A struct that lives in BSS has no initial value to inspect statically.** You will only ever see it in a runtime dump. Plan for it: `--char-dump`-style RAM captures (see [probe-cli.md](probe-cli.md)) are the *only* way to look at that region.

### Route 2 (validation) — the boot log

A wrong BSS size usually presents as a boot failure with a code, not as corruption: the guest's self-rebase writes past the end of its own arena, or its init routine refuses. If the emulator prints an init-failure code, suspect the BSS size and the descriptor before suspecting the game.

### Other platforms

If the backend derives BSS from a header field or from a descriptor key instead of the filename, read it out of the loader the same way. Record *where it came from* in `target.toml`'s comment — the next person will otherwise assume "filename" universally.

---

## 4. SL_FO — the RWPI static base

### What RWPI means, and why it shapes every tool

**Precondition: that your image is RWPI at all.** Everything in this section applies to an image built with the ARM toolchain's **Read-Write Position Independent** model. A section-mapped ELF build addresses its statics with **absolute literals** and has no `sl` — if the entry point has no `sl` prologue and no route in this section produces a candidate, stop looking and read [§9.1](#91-the-second-address-model-a-section-mapped-elf) before you spend an afternoon on a density scan.

The practical consequences of RWPI:

1. **Static and global data is addressed as `[sl, #imm]`**, where `sl` is r10 — some builds use `sb`, r9. There are **no absolute data addresses in the instruction stream**. Your disassembler must recognise all four spellings (`sl`, `r10`, `sb`, `r9`) or you will conclude the binary has no globals.
2. **Every function sets `sl` up itself**, typically:
   ```
   ldr rX, [pc, #…]     ; a link-time constant
   mov sl, rX
   add sl, pc           ; make it position-independent
   ```
   This is what makes the host able to call guest functions cold, without arranging registers — see [calling-guest-code.md](calling-guest-code.md). **Verify it before calling anything.**
3. **Pointers stored in the static area are usually reloc-biased** — see §5.

### The three routes to `sl` — use at least two

**Route 1 — run it and read the register (fastest, authoritative).**
Boot under the headless probe, provoke any fatal error (or add a temporary print), and read `SL`/`SB` out of the register dump the core attaches to fatal errors. If the core does not dump registers, add it: a `dump_reg_stack(image_base)` that prints regs + call stack + a stack window pays for itself within the hour. **(A)**

**Route 2 — disassemble the entry thunk.**
Disassemble forward from file offset 0 (remember: Thumb) and follow the `ldr rX,[pc,#…]; mov sl,rX; add sl,pc` prologue. `sl = literal + pc_at_the_add`, where `pc` in Thumb is `(address of the add) + 4`, word-aligned. This route needs no emulator at all.

**Route 3 — density scan (also the cross-check).**
For a candidate `sl_fo`, run:

```
python "${CLAUDE_PLUGIN_ROOT}/tools/dump_table.py" -t target.toml <sl_fo> 256
```

It classifies each word: odd word → Thumb function pointer at `(w & ~1) + delta`; even word → try as a data pointer → try to decode a string. A **correct** base produces a dense run of words that decode as strings and function pointers. A wrong base produces noise. This is a strong signal because RWPI static areas are almost entirely pointer tables and initialised globals.

### `sl_guest` vs `sl_fo`

Store the **file offset** in `target.toml`, and compute `sl_guest = image_base + sl_fo` where needed. Mixing the two is the single most common arithmetic error in this work: `0x1b2c40` and `0xb2c40` differ by exactly `IMAGE_BASE` and both look equally plausible in a note.

### ⚠ Verify that the statics are readable offline *at all*

In the reference image the static area lives **inside the loaded file** (`sl_guest = IMAGE_BASE + sl_fo`), so a static slot can be read offline at `sl_fo + imm`. That is what makes `[sl,#imm]` annotation and `dump_table.py` possible **(B)**.

RWPI does not guarantee this. A build can copy its RW statics into a freshly allocated block at startup, in which case `sl` at runtime points into the heap and offline reads give you only the *initial* image values, not live ones.

**The test, and you must run it:** read `N` bytes at runtime from `sl_guest + imm` via the probe, and compare against file bytes at `sl_fo + imm` **immediately after boot, before the game mutates anything**.

* Identical → statics are in-place; offline static reads are valid and `dump_table.py` works.
* Different → statics were relocated. Set `sl_fo` to the *initial-image* copy for offline work if one exists, and treat every live static read as runtime-only. Say so loudly in `target.toml`'s comment.

---

## 5. RELOC_DELTA — and the unit trap

**Definition.** Pointers stored in the image do not hold usable addresses. They hold a biased word `W`, and `W + RELOC_DELTA` recovers the real location. In the reference image `RELOC_DELTA = 0xeec` and the result is a **file offset** **(B)**.

### Why a delta exists at all

The guest self-rebases at entry (§2 Route 2). Whatever base the linker assumed is not where the loader put the image, so stored pointers carry the link-time bias. A delta usually has a **physical explanation** worth hunting for, because finding it converts a fitted number into a prediction you can test:

* the file carries a header/prelude of exactly `RELOC_DELTA` bytes before the linked image proper (so `file_offset = link_address + delta`);
* or the linker's base was a small non-zero constant;
* or the delta is `0` and pointers are plain file offsets;
* or the delta is **negative** and pointers hold guest addresses that must be reduced.

If you can explain the number, you can predict where the real entry code begins and check it.

### Derivation — solve by intersection

```
python "${CLAUDE_PLUGIN_ROOT}/tools/solve_reloc.py" -t target.toml \
  --known 0x0ab1c0,0x0ab1fc,0x0ab20c,0x0ab218,0x0ab228,0x0a9d50 --near-miss
```

The algorithm **(A)**:

1. Identify **>= 5** strings you can recognise, at known file offsets. Easiest targets: IPv4 literals, URLs, `printf` format strings, error and log text, protocol channel names. Get their offsets from `strings_dump.py`.
2. Build `S` = every 4-byte little-endian word in the image (overlapping, not just aligned — but score aligned hits higher).
3. For each target offset `t`, form the candidate set `{ t - v : v ∈ S }`.
4. **Intersect** across all targets. Sort survivors by `|delta|`; the true delta is usually small.
5. Also print the **5-of-6 ranking**. One of your "known" strings is usually referenced through a different mechanism (an index into a table, a computed address, or it is simply never referenced), and a strict intersection then returns nothing.

### Validation — score it corpus-wide, never on the hand-picked five

```
python "${CLAUDE_PLUGIN_ROOT}/tools/solve_reloc.py" -t target.toml --validate
```

1. Extract **all** plausible asciiz starts in the image — regex `(?<=\x00)[\x20-\x7e]{6,}\x00` is a good first cut.
2. For each candidate delta, count how many of those strings are actually referenced by some stored word under that delta.
3. A correct delta explains a large fraction of the corpus. A coincidence explains approximately none.

**Never adopt a delta from five hand-picked samples.** Five samples is how you *generate* candidates; the corpus score is how you *accept* one. **(A)**

### ⚠ The unit trap — determine what the delta produces BEFORE adopting it

`W + delta` may be a **file offset** or a **guest address**. Both are plausible; both produce a self-consistent-looking tool chain; only one is right, and the wrong one shifts every resolved string by exactly `IMAGE_BASE`.

**The test:**

```
candidate = W + delta
if 0 <= candidate < len(file):                       -> unit is "file_offset"
elif IMAGE_BASE <= candidate < IMAGE_BASE + len + bss -> unit is "guest_address"
else                                                  -> the delta is wrong
```

Run it over the whole corpus, not one word. If most words land in `[0, len)` the unit is `file_offset`; if most land in `[IMAGE_BASE, …)` the unit is `guest_address`. A delta whose hits straddle both bands is not a delta — it is noise that happened to intersect.

Record the answer in `target.toml` as `reloc_unit`. Every tool branches on it. Getting it wrong does **not** produce an error; it produces a listing full of strings that are subtly the wrong strings.

### Sanity relations to keep in your head

```
guest_address = file_offset + IMAGE_BASE
file_offset   = stored_word + RELOC_DELTA          (when reloc_unit = "file_offset")
sl_guest      = IMAGE_BASE + SL_FO
static slot   = file bytes at SL_FO + imm          (only if the offline-readability test passed)
thumb target  = (stored_word & ~1) + RELOC_DELTA   (function pointers carry the Thumb bit)
```

---

## 6. The god-object slot

Most feature-phone RPGs keep one large heap-allocated struct holding the player, the current map, the inventory and the UI state. Finding it early makes every later field hunt an offset from a single anchor.

**Procedure (A):**

1. `dump_table.py -t target.toml <sl_fo> 512` — dump the first ~2 KB of statics.
2. Look for slots that hold a value inside the guest **heap window** (for the reference core, `0x4000_0000 .. 0x5000_0000`) rather than inside the image.
3. Re-dump at several very different game states (title screen, in a map, in a menu). The god-object slot holds a live heap pointer in **all** of them; scratch slots do not.
4. Cross-check with `xref.py slot <sl_off>` — the god object is read from *everywhere*; a one-off buffer pointer is read from two places.

Reference example **(B)**: `CHAR = *(sl + 0x444)` i.e. `*(0x1b3084)`, with a hardcoded fallback pointer used when the slot fails the heap-window test. The fallback matters in practice: during boot and during certain transitions the slot is transiently garbage, and a host that renders from a garbage pointer paints noise instead of returning `None`.

**Validate every pointer hop, every time.** A stale handle must produce `None`, never a render of whatever was there. **(A)**

### The indirect-handle convention — a property of the *carrier runtime*, not of the game **(B, and derive yours)**

In one reference platform, an allocation handed to the guest is an indirect handle: the **payload starts 8 bytes after the handle word**. So `object = *(handle) + 8` appears everywhere — in the god-object resolution, in the save primitives, in the offscreen-buffer walk. As a **discovery heuristic** this still stands, and it is still the fastest one: if your first pointer hop lands 8 bytes short of something that looks almost right, you have found your platform's version of it.

> **REFUTED.** *"This is a property of the platform — find the convention once, then apply it wherever the game hands you a container."*
>
> **Refuted by:** reading the two carrier runtime crates that host the **same game**. The convention is neither the game's nor "the platform's" loosely — it is the body of **one function in the carrier runtime**, the accessor that turns a container handle into a data pointer. One carrier's is `*(handle) + 8`; the other's is the **identity function**, so the same game's containers are plain direct pointers there. `HIGH / CONFIRMED` — two runtime crates read, and the corrected host renderer then diffed pixel-identical against the guest's own buffer.
>
> **Why the wrong reading was attractive:** the convention appears at every container in the image, which makes it look like a property of the world rather than of one accessor — and it is invisible in the guest disassembly, because the guest just calls the platform.
>
> **Consequence if carried forward:** the wrong convention **does not fail loudly**. Double-dereferencing a direct pointer reads the container's first word *as an address*; when that word happens to land inside the heap window it passes every plausibility check you wrote and yields **wrong records** instead of `None`. Three already-shipping functions in the reference port were wrong in exactly this way at the same time. The "8 bytes short of something that looks almost right" tell only describes the benign direction.

**What to do instead (A).** Because it is one function, **one build-keyed flag resolves every container in the game simultaneously** — inventory grid, area objects, the item database, map arrays, offscreen framebuffers, popup label arrays, sprite descriptors:

```rust
/// None = the handle IS the data pointer; Some(n) = payload starts n bytes past *(handle).
fn container_data_off() -> Option<u32>;
```

Derive it by **reading the carrier runtime's accessor**, not by inferring it from one hop that looked plausible, and pin **both** conventions in a selftest so a build that switches cannot pass silently. Where that flag lives is [build-keyed-anchors.md](build-keyed-anchors.md); the general lesson — that an apparent game property is frequently a carrier-runtime property — is [multi-build.md](multi-build.md) §4.

---

## 7. Validation — when is the model pinned?

Do not proceed to Phase 2 until all of these hold:

- [ ] The image's **shape** was established first — flat and self-rebasing, or section-mapped ELF (§1, §9.1). On a section-mapped image the rest of this checklist does not apply.
- [ ] `IMAGE_BASE` was read from the loader source, not guessed.
- [ ] `bss_size` parsed from the documented source (filename or header) and the game boots.
- [ ] `sl_fo` agreed by **two** of {register dump, entry thunk, density scan}.
- [ ] The offline-readability test in §4 was run and its answer recorded.
- [ ] `reloc_delta` came from an intersection over >= 5 strings **and** scored well corpus-wide.
- [ ] `reloc_unit` determined by the band test in §5, not assumed.
- [ ] A `dis.py` listing over a known-good range shows **strings inline in the annotations**. This is the integration test for the whole model: it exercises `IMAGE_BASE` (addresses), `SL_FO` (`[sl,#imm]` resolution), `RELOC_DELTA` and `reloc_unit` (pointer → string) simultaneously.
- [ ] Every constant in `target.toml` carries a comment naming the route that produced it.

---

## 8. Failure modes

| Symptom | Likely constant | Why it looks plausible |
|---|---|---|
| Listing decodes fine, annotations resolve to garbage strings | `RELOC_DELTA` or `reloc_unit` | PC-relative decoding is self-relative and unaffected |
| Strings resolve but are consistently the *wrong* strings, all shifted the same way | `reloc_unit` (off by `IMAGE_BASE`) | Every lookup still lands inside the file |
| `[sl,#imm]` annotations are noise but the rest is clean | `SL_FO`, or statics were relocated at runtime (§4) | The disassembly itself never touches `sl` |
| An address you are sure is valid reads as out of range | `bss_size` not accounted for | The address is past EOF but inside the mapped region |
| `dump_table.py` output has no dense pointer run anywhere near your base | `SL_FO` wrong | A wrong base still prints 256 rows of *something* |
| `solve_reloc.py` returns dozens of candidates | too few known strings, or one of them is not referenced | The intersection is under-constrained; add targets and use `--near-miss` |
| Profiler PCs disassemble to mid-instruction garbage | printing guest addresses where file offsets were expected, or ARM-vs-Thumb confusion | Both bases produce *a* decode |
| Everything is right but one region decodes as nonsense | it is a literal pool or a jump table, not code | Use `dis.py --resilient`, which emits `.hw 0xNNNN` and advances 2 |
| No `sl` candidate survives any of the three routes, and the entry point never rebases | the image is not RWPI — it is section-mapped (§9.1) | Every route "fails quietly": there is no error, just no dense pointer run anywhere |
| A pointer-plausibility guard that worked on build 1 rejects **every** pointer on build 2 | the window's lower bound is build 1's image base | Both builds' addresses are small round numbers; `0x1000` and `0x100000` look equally like "the start of the image" |
| A `.data`-resident table looks observable only at runtime | your tool maps `.text` only (§9.1) | A tool that hardcodes one section's mapping answers "nothing there" instead of "I cannot see there" |

---

## 9. When none of this applies — and the second address model

* **The jar is full of `.class` files.** There is no ARM image, no `sl`, no relocation delta. Skip this entire document; use JVM-level tooling and keep only the probe, differential-verification and evidence practices.
* **The native member is compressed or encrypted.** Extract and decompress first (`archive-prep`), then start here on the plaintext image. If a member is encrypted, that is out of scope for this plugin: no extracted decryption keys are shipped, documented or defaulted anywhere in it.
* **The image is ARM-mode, not Thumb.** Set `isa = "arm"` in `target.toml`. The `+1` Thumb bit on entry disappears, function pointers lose their low bit, and the "both Thumb idioms" note in [static-toolkit.md](static-toolkit.md) is replaced by ARM's rotated-immediate encoding. Everything structural above is unchanged.
* **The native member is a fully linked ELF with live section headers.** Then all four constants above **cease to exist** — not "take different values". This is common enough on a second carrier that it deserves its own model, below.

### 9.1 The second address model: a section-mapped ELF

**(A)** A second AOT-ARM carrier's native member can be an ordinary linked 32-bit ARM ELF whose section table survived. Its loader does not map one blob at a base; it walks the section headers, maps every section with a non-zero `sh_addr` at that address for `sh_size`, and enters at `e_entry`:

```rust
for shdr in elf.section_headers() {
    if shdr.sh_addr != 0 { core.load(bytes_for(shdr), shdr.sh_addr, shdr.sh_size)?; }
}
core.run_function(elf.ehdr.e_entry, &[])?;      // no rebase argument, and no rebase step
```

The boot chain around it is [boot-order.md](../../emulator-core/reference/boot-order.md) §8b; carrying a document written against one model over to the other is [multi-build.md](multi-build.md) §5. What follows is only the addressing.

| the flat, self-rebasing model | the section-mapped model |
|---|---|
| `image_base` — one base added to every file offset | **gone.** Each section has its own `(sh_offset, sh_addr, sh_size)`; `guest_address = sh_addr + (file_offset - sh_offset)` **within that section** |
| `bss_size` — parsed out of the member's filename | **gone.** `.bss` is a section header with a vaddr and a size and no file bytes |
| `sl_fo` — an RWPI static base in r10 | **gone.** Statics are absolute literals; there is no `sl` and no static-slot table |
| `reloc_delta` / `reloc_unit` — a bias on stored pointers | **gone.** The image is linked: a stored pointer *is* the address |
| entry at file offset 0, guest self-rebases | entry at `e_entry`, nothing rebases |

**Which tools survive.** `dump_table.py`, `xref.py slot`, `solve_reloc.py` and every `[sl,#imm]` annotation are **inapplicable**, not merely differently parameterised. Do not try to fit a delta to an image that has none — you will find one that scores badly and believe the corpus is unusual.

**What replaces them is better.** Because every static reference is an absolute literal, **"who touches this global?" becomes a plain literal search** over `.text` *and* `.data` — the hard question (unknown code addresses) turns into the easy one (known data addresses). See [static-toolkit.md](static-toolkit.md) §8. The porting order inverts with it: pin the data model first, and every later code hunt is cheap.

**Two traps that follow from the model, both real (B):**

* **A tool that maps only `.text`** — the natural shortcut, since `.text` is where you disassemble — is structurally blind to `.data`, and `.data` is where dispatch tables, per-category constant tables and string-pointer arrays live. Read the section table and keep the right pair per section. A null result from a `.text`-only tool about a `.data` address is a **false negative**, not evidence.
* **Every "is this a guest pointer?" window is per build.** One build's image starts near `0x100000`; a section-mapped build's `.text` can start at `0x1000`. A plausibility guard written for the first silently refuses every valid pointer on the second — and a refusal reads exactly like "the feature is not available here". Open the window at the **lowest mapped page of the build that actually booted**, and key it like every other per-build value ([build-keyed-anchors.md](build-keyed-anchors.md)).

Everything else in this plugin is unchanged by the switch: the probe, the evidence standard, differential verification, patching and the recipes all apply as written. Only the arithmetic that turns a file offset into a guest address differs — and on this model there barely is any.
