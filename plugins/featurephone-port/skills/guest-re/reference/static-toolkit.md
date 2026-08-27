# The static toolkit — one section per question you can ask a binary

Static analysis answers **where**. It never answers **what** — that is Rule 1. Use these to narrow a search from megabytes to a handful of call sites, then settle the behaviour in a probe run.

Everything here runs offline in seconds against `work/client.bin`, with no emulator. Every tool reads `target.toml` ([address-model.md](address-model.md)) and prints **file offsets**.

## Contents

1. [Disassembly: the resilient sweep](#1-disassembly)
2. [Who calls X?](#2-who-calls-x)
3. [Who calls X with constant N?](#3-who-calls-x-with-constant-n)
4. [Which function consumes ids?](#4-which-function-consumes-ids)
5. [Who materialises struct offset K?](#5-who-materialises-struct-offset-k)
6. [Who loads this value / this pool slot?](#6-who-loads-this-value--this-pool-slot)
7. [Where is this function stored in a table?](#7-where-is-this-function-stored-in-a-table)
8. [What does this static slot hold?](#8-what-does-this-static-slot-hold)
9. [From a string to the code that prints it](#9-from-a-string-to-the-code-that-prints-it)
10. [Reading text out of the image](#10-reading-text-out-of-the-image)
11. [Compiler runtime helpers have no symbols](#11-compiler-runtime-helpers)
12. [The throwaway-scanner pattern](#12-the-throwaway-scanner-pattern)
13. [Null results: what each query cannot see](#13-null-results-what-each-query-cannot-see)

---

## 1. Disassembly

```bash
python "${CLAUDE_PLUGIN_ROOT}/tools/dis.py" -t target.toml 0x13d000 0x13d100 --resilient
```

Three properties that make a listing usable, all of which you must insist on **(A)**:

**It prints file offsets, not guest addresses.** File offsets are what your hex editor, your `grep`, your `--char-dump` diff and your next `dis.py` invocation all consume. Printing guest addresses forces a mental subtraction on every line, and mental subtractions are how `IMAGE_BASE` errors survive for weeks.

**It resolves pointer chains inline.** Two idioms carry essentially all the interesting data in an RWPI image:

```
ldr rX, [pc, #imm]   ->  literal address -> word -> +RELOC_DELTA -> decode as text
ldr rX, [sl, #imm]   ->  SL_FO + imm     -> word -> +RELOC_DELTA -> decode as text
```

Annotating these turns "chase three levels of pointer by hand for every line" into "read the strings in the listing". This single feature is the difference between a usable disassembler and capstone.

**`--resilient` is mandatory in practice.** ARM images interleave literal pools and jump tables with code. A naive `md.disasm(blob)` stops dead at the first pool and you silently lose the rest of the function. The resilient mode does a linear sweep with `count=1`; on an undecodable halfword it emits `.hw 0xNNNN` and advances 2. Branch targets are decoded to `; -> fo 0x…` so you can follow control flow without arithmetic.

**Resuming a sweep**: infer the previous instruction's width from its halfword prefix — `0xf000 / 0xf800 / 0xe800` masks indicate a 32-bit Thumb-2 encoding (4 bytes), everything else is 2. Getting this wrong shifts the entire rest of the listing by one halfword and produces confident nonsense.

---

## 2. Who calls X?

```bash
python "${CLAUDE_PLUGIN_ROOT}/tools/xref.py" -t target.toml calls 0x199764
```

Decode **every** Thumb `BL`/`BLX` halfword-pair in the whole image, sign-extend the 23-bit offset, mask `~3` for `BLX`. No CFG, no symbols, no heuristics — about a second over a 1.5 MB image, and **complete for direct calls**. Whether your image contains any direct cross-module calls *at all* is the first thing to establish, not something to assume. **(A)**

**⚠ On Thumb-1, `bl` is not always a call.** The compiler emits `bl` as a **long branch** when `b.n`'s ±2 KB range cannot reach the target — typically to an outlined `else` arm. So:

* a "caller" list may contain sites that are really fall-through control flow;
* and, far more dangerously, a reachability analysis that treats `bl` as a call it need not follow will mark **live code dead**. See [patching.md](patching.md) §5.

Tell the two apart by disassembling the target's first instructions: a real function starts with `push {…, lr}` and sets up its own `sl`; a long-branch target does neither and often reads the caller's frame.

### The false-NEGATIVE direction is the expensive one

> **REFUTED.** *"A whole-image `BL`/`BLX` decode answers 'who calls X?' completely. The only caveat is the false-positive direction, where a `bl` is really a long branch."*
>
> **Refuted by:** an image in which **every** cross-module call is indirect — `ldr rN, =target|1` followed by a `bl` into a shared, one-instruction `bx rN` veneer. A BL scan over that image reports **zero** callers for every real function, and thousands of callers for the veneer. In the reference port two independent investigations read the **same** `bl` as two different callees — "a call to `free`" and "a call to the save deserialiser" — and no amount of further reading separated them. Computing that instruction's literal-pool address and reading the word settled it in one line; a search for the resulting word then found exactly **two** sites in the entire image that reference that function, which is why a BL graph had never shown its callers. `HIGH / CONFIRMED`.
>
> **Why the wrong reading was attractive:** the scan really is complete *as a decode*. Every `BL` in the image is found and every offset is correct. The unstated assumption is that a call's target is encoded **in the call**. On an indirect image it is encoded in a literal pool several instructions earlier, and the branch itself is honest about nothing.
>
> **Consequence if carried forward:** "nothing calls this function" is one of the most load-bearing negative results in this work — it is what licenses treating a routine as dead, as un-hooked, or as safe to drive. On an indirect image that negative result is manufactured by the tool, and it is *always* wrong.

### Detect it once per image, then stop wondering **(A)**

Two cheap tells; either is sufficient.

1. **A leaf with an absurd call count.** Rank every callee by BL count. If one or two tiny targets absorb thousands of sites and each disassembles to a single `bx rN` (plus at most a `push`/`pop` pair), the image routes calls indirectly.
2. **A function you *know* runs has no callers.** Pick a routine you reached from a string binding (§9) — something the game demonstrably executes — and ask who calls it. An empty answer for live code is the tell.

Write the answer into your findings document next to the address model. It decides which query you run for the rest of the port.

### Resolving a call on an indirect image **(A)**

The callee is the word loaded by the nearby `ldr rN, [pc, #imm8]`. Compute the pool slot from the **loading** instruction, never from the branch:

```
pool_addr = ((addr_of_ldr + 4) & ~3) + imm8 * 4     # Thumb pc = insn + 4, then word-aligned
callee    = word_at(pool_addr) & ~1                 # the stored word carries the Thumb bit
```

and the caller query inverts into a **literal search**, which this toolkit already has:

```bash
xref.py -t target.toml literal 0x0004ce65    # who loads this biased function word (callee|1)
xref.py -t target.toml fnptr   0x0004ce64    # …in all six encodings, if you are unsure of the bias (§7)
```

Two consequences worth saying out loud:

* **Arithmetic is the arbiter, not judgement.** When two readings of the same instruction disagree, do not weigh the readings — compute the pointer. "Two people read the disassembly and agreed" is **one** derivation, not two ([evidence-standard.md](../../../shared/evidence-standard.md) §3).
* **An indirect image is, in one respect, easier.** The callee becomes a searchable *datum* rather than a relative offset, so "who calls X" and "who stores X in a table" are the same query — and on an absolute-addressing build the same trick answers "who touches this global" (§8).

---

## 3. Who calls X with constant N?

```bash
python "${CLAUDE_PLUGIN_ROOT}/tools/xref.py" -t target.toml calls 0x13d5d0 --with-imm8-arg
```

Same image-wide `BL` scan, plus a peephole: walk back up to ~20 bytes from each call looking for `movs r0, #imm8` (`0x20xx`), aborting at any intervening branch. Recovers the ids and enum values passed to constant-argument APIs.

This is how you go from "1400 call sites of the message function" to "**exactly one** site passes code 0x67". That reduction is the whole game — see [recipes.md](recipes.md) Recipe B.

Extend the peephole for your title's calling shape: `movs r1,#imm8`, `movs rX,#imm8; lsls rX,#sh` for larger constants, and `ldr rX,[pc,#…]` for constants over 255.

---

## 4. Which function consumes ids?

You want the text accessor (`getText(obj, id)`) or the message dispatcher (`showMessage(code)`), and you do not know its address.

```bash
python "${CLAUDE_PLUGIN_ROOT}/tools/msgid_scan.py" -t target.toml --id-count 196 --top 20
```

**The inverse index (A):** for every `movs rX, #imm8` where `imm8 < N_IDS`, find the next `bl` within ~8 halfwords, and tally `callee -> set(ids)`. Rank callees by the number of **distinct** ids they receive.

Why it works: the real text API collects a large, dense set of ids from all over the image. Accidental neighbours collect one or two. The ranking is usually unambiguous by an order of magnitude.

Then invert it again: `--sites-for 0x87` prints every site passing a specific id, which is your entry point into the code path that produces that message.

---

## 5. Who materialises struct offset K?

```bash
python "${CLAUDE_PLUGIN_ROOT}/tools/xref.py" -t target.toml offset 0x18a
```

**A struct offset has no single encoding.** Enumerate the idioms or you will "prove" a live field is unused. The two Thumb-1 forms:

```
movs rX, #imm8 ; lsls rX, #sh        where (imm8 << sh) == target
                                     — allow up to ~6 halfwords of instruction
                                       scheduling between the two
ldr  rX, [pc, #…]                    with a literal pool word == target
```

Also present in real images and worth adding for your title: `adds rX, #imm8` accumulating in two steps, `add rX, sp, #imm` for stack-relative structures, and offsets folded into `ldr rD, [rB, #imm5*4]` when small enough to be an addressing-mode immediate — the last of which is invisible to a constant scan entirely, so **a null result never proves a field is unread**.

### The third enumeration: block copies name no offset at all **(A)**

Two idiom scans are still not a complete answer, because **a field is also read and written by code that never mentions its offset**:

* a `memcpy`-shaped call that copies a whole record;
* an `ldm`/`stm` run that copies a block of fields in one go;
* a struct assignment the compiler turned into either of those.

In the reference port a store-site enumeration built on `strh` missed a site that wrote the two fields it was tracking through an `ldm`/`stm` block copy. Several sessions later, the numbers one screen displayed turned out to come from **two whole-record copies** — a 20-byte block copy into a candidate array, and a full-record `memcpy` into an equipped-slot array — both of which place the fields at the **same relative offsets under a different base**. Neither an offset scan nor a store scan can see either one, and a cache fix built on the assumption that they did not exist was structurally void. `HIGH / CONFIRMED`.

So the complete enumeration is three questions, not two:

| question | query |
|---|---|
| who names the offset? | `xref.py offset <imm>`, both Thumb idioms |
| who copies the whole record? | find the record stride as an immediate (`movs`/`lsls`, an `alloc(N)`, a `memcpy` length) and disassemble its uses |
| who holds a **second copy** of the record? | search for the same *relative* offsets under a **different base** — that is the signature of a snapshot array |

Finding every copy of a field is a procedure in its own right: [live-data-structures.md](live-data-structures.md) §7.

Practical use: after a differential-RAM diff hands you a candidate offset ([probe-cli.md](probe-cli.md) §8.4), this tool tells you whether any code actually names it, and gives you the readers and writers to inspect.

---

## 6. Who loads this value / this pool slot?

Two genuinely different questions; keep them apart.

```bash
xref.py -t target.toml literal 0x0000dead    # who loads this VALUE
xref.py -t target.toml pcrel   0x000ab1fc    # who loads from THIS pool slot
```

* **`literal`** finds `ldr Rd, [PC, #imm8*4]` whose literal *word* equals a given value. Use it when you know the constant (a magic number, a size, a biased pointer).
* **`pcrel`** finds PC-relative loads whose *target address* is in a set — both Thumb (`0x4800..0x4fff`) and ARM (`0x051f`/`0x059f` mask families). Use it when you know the slot but not what it holds, or when the same value appears in many pools and you care about one.

```bash
xref.py -t target.toml slot 0x138            # who computes or loads sl + 0x138
```

* **`slot`** searches literal-pool words equal to an **sl offset**, not an address. Static accesses go through `sl`, so this is the "who touches this global" query.

### ⚠ An immediate scan for a *value* proves nothing either

The caveat in §5 belongs here too, and its absence cost a real session. **(B)** Three separate image-wide searches for "a `movs rX, #0x81` feeding a `strb`" returned **zero** sites, and that null result was used as grounds to doubt an anchor that was in fact correct. The compiler had *synthesised* the constant:

```
movs r2, #0x7f
rsbs r2, r2, #0      ; r2 = -0x7f, i.e. the byte 0x81
```

**(A) Enumerate the synthesis idioms, or state the blind spot next to the null result.** Constants are routinely built rather than loaded: by negation (`rsbs`), by a shift pair (`movs` + `lsls`), by an add pair, by masking a larger literal, and — for divisors — by a reciprocal multiply with no immediate present anywhere (§11). A value scan is a way to *find* sites; it is never a way to prove there are none.

---

## 7. Where is this function stored in a table?

```bash
python "${CLAUDE_PLUGIN_ROOT}/tools/xref.py" -t target.toml fnptr 0x1960bc
```

**Search six encodings at once (A):**

```
fo            fo|1
guest         guest|1
fo - delta    (fo - delta)|1
```

You do **not** know a priori whether a vtable holds file offsets, guest addresses or reloc-biased words, nor whether the Thumb bit is set. Searching all six and letting the hit tell you the convention is faster and more reliable than reasoning about it — and the convention can differ between two tables in the same image (a compiler-emitted jump table and a hand-written dispatch table have no reason to agree).

A hit also tells you the table's **base and stride**, which usually identifies the dispatcher and hence the enum you are inside.

---

## 8. What does this static slot hold?

```bash
python "${CLAUDE_PLUGIN_ROOT}/tools/dump_table.py" -t target.toml 0xb2c40 256
```

Per-word classification of an RWPI static region:

* `w & 1` → treat as a Thumb function pointer at `(w & ~1) + delta`, print the target;
* else → try as a data pointer → resolve through the delta → decode as text;
* else → print the raw word.

Output shape: `addr (sl+0x…): value  -> …`.

**The fastest way to understand an unknown static region**, and the cross-check for `SL_FO` — a correct base yields a dense run of resolvable words; a wrong one yields noise. It is also how you find the god-object slot (§[address-model.md](address-model.md) §6) and how you find the slots holding the game's own record-name strings, which is step 2 of Recipe A.

### The other address model: `.data` is a section, and it is in the file **(A)**

Everything above assumes an RWPI image whose statics are reached through `sl`. A second, equally common shape is a **fully linked ELF with live section headers** ([address-model.md](address-model.md) §9). There is no `sl` and no static-slot table at all: statics are addressed by **absolute literals**. Two consequences, both large.

**1. Read the section headers; do not hardcode one mapping.** Each section carries its own `(sh_offset, sh_addr, sh_size)`. A tool that linearly maps `.text` — file offset X to vaddr Y, running to EOF — is **structurally blind to `.data`**, and answers "nothing here" to every query about it. In the reference port that blindness lasted many sessions; the moment `.data`'s own pair was read out of the section table, a 20-way overlay dispatch table indexed by a record byte could be dumped **statically**, and its entry 17 identified as the screen a live bug was about. Earlier sessions had only ever observed that table at runtime. The same region on that build also held 16-byte platform import stubs, so dumping it enumerated every platform API the guest uses. `HIGH / CONFIRMED`.

**2. "Who touches this field?" collapses into a literal search.** Because a static's address is embedded verbatim in a literal pool, the hard question (unknown code addresses) becomes the easy one (known data addresses):

```
every 4-byte-aligned word whose value falls in [field, field + size)
   -> the ldr rX,[pc,#imm] instructions that load it, resolved as ((pc & ~3) + 4 + imm*4)
   -> the functions containing them
```

That query shape — plus an `imm8` scan for known layout constants — was the entry point for most of the remaining work on the reference port's second build. It also answers the negative direction honestly: a range query that returns only readers *inside* a block you already patch is real evidence that nothing else reads it.

**⚠ A null xref against a `.data` address proves nothing if your tool maps only `.text`.** Check which sections your tool knows about before believing any negative result about data.

---

## 9. From a string to the code that prints it

```bash
python "${CLAUDE_PLUGIN_ROOT}/tools/bind_strings.py" -t target.toml --string-fo 0x0ab1c0,0x0a9d50
```

**The highest-yield entry point into an unknown binary.** The full chain:

```
string file offset
   -> reloc word              (fo - RELOC_DELTA)
   -> word-aligned occurrences of that word in the image   (these are literal-pool slots)
   -> the ldr [pc, #…] instructions that reference those slots
   -> the functions containing them
```

Pick 5-10 messages a *player* sees — a refusal message, a confirmation prompt, a network status line, a menu heading — and run this. You now have five real code locations with known semantics, in a binary you have never read, in about a minute.

Everything else in this document is a way of expanding outward from those five points.

---

## 10. Reading text out of the image

```bash
python "${CLAUDE_PLUGIN_ROOT}/tools/strings_dump.py" -t target.toml --range 0xa0000:0xb0000 --encoding euc-kr --out work/strings.txt
```

* Multi-byte CJK encodings need an encoding-aware run detector, not ASCII `strings`. For EUC-KR the run class is `(?:[\xa1-\xfe][\xa1-\xfe]|[\x20-\x7e])+`. **Swap the byte class per title**: Shift-JIS, GBK, Big5, CP1252 all have their own lead/trail ranges. Getting this wrong makes a Korean title look like it has no strings at all.
* Emit `file_offset <TAB> text` and commit the result. It is the index every later query starts from.
* Also print, for each hit, the **reloc word** (`fo - delta`) you would search for next. Saving that subtraction removes the most common transcription error in this workflow.

### The game's *runtime* text table is a separate thing

Rodata strings are what the code passes to `printf`-alikes. Most titles also carry a packed text table as an archive member, indexed by id. A common shape **(B)**:

```
u16 total_len
u16 count
u16 offsets[count]      // relative to the start of the blob
<encoded text blobs>
```

Decode it, commit an `ids.txt` of `id <TAB> text`, and you can then map any message id you recover from code straight to the text a player sees. That mapping is what makes Recipe B mechanical. Ship the regenerator script in your repo so the table can be re-derived after an asset change.

---

## 11. Compiler runtime helpers

ARM feature-phone binaries call `__aeabi_*`-style helpers that carry no symbols. You will hit them constantly while reading arithmetic, and misreading one as game logic wastes an hour.

**Identify them by their epilogues (A).** Worked examples from one image **(B)**:

| helper | tell |
|---|---|
| signed/unsigned **MOD** | epilogue `eors r1, r3 ; subs r0, r1, r3` |
| signed/unsigned **DIV** | epilogue `adds r0, r3, #0` |

The technique generalises: these routines are short, called from hundreds of sites (`xref.py calls` gives a huge count), take exactly two arguments, touch no globals, and never call anything. Find the top-N most-called leaf functions in the image and label them once; every later listing becomes readable.

Divide-by-constant is often *not* a call at all — the compiler emits a reciprocal multiply. If you are hunting a divisor and find no `movs rX, #100`, look for a magic multiplier and a shift instead; that changes the patching strategy completely ([patching.md](patching.md) §3).

---

## 12. The throwaway-scanner pattern

Real sessions produce one-shot scripts with a labelled dict at the top:

```python
TARGETS = {
    "refusal_msg":   0x0ab1c0,
    "confirm_msg":   0x0ab1fc,
    "net_status":    0x0a9d50,
}
```

**The pattern is worth keeping; the contents never are.** **(C)** Those dicts are a session's scratch memory. Do not commit them into shared tooling, do not let them become defaults, and do not copy another port's. Keep them next to the notes for the question they answered, and put anything durable into `target.toml` or into your findings document with its provenance.

---

## 13. Null results: what each query cannot see

Every query in this document is a **finder**. None of them is a prover, and this table is the difference. **(A)**

| query | the null result you will be tempted to believe | what is hiding there | do this instead |
|---|---|---|---|
| `calls <addr>` (§2) | "nothing calls it" | every call is `ldr rN,=addr\|1` + `bl` into a `bx rN` veneer | literal-search the biased word — §2 |
| `offset <imm>` (§5) | "the field is unread" | the offset is an addressing-mode immediate, or the record is block-copied | stride / `memcpy` scan plus a snapshot-array search — §5 |
| `literal <value>` (§6) | "that constant appears nowhere" | the constant is synthesised (`rsbs`, a shift pair, a reciprocal multiply) | enumerate the synthesis idioms — §6, §11 |
| any query about a static (§8) | "no code touches this global" | your tool maps `.text` only and the datum is in `.data` | read the section headers — §8 |
| `fnptr <addr>` (§7) | "it is in no table" | the table stores a different one of the six encodings | search all six at once — §7 |
| `dis.py` over a range | "this region is not code" | it is a literal pool or a jump table interleaved with code | `--resilient` — §1 |
| `deadcode_sweep.py` | "this range is unreachable" | the sweep did not follow `bl`, or the range is merely one your own patch branches over | [patching.md](patching.md) §5 |

**The discipline:** when a query returns nothing, write down *which* blind spot you have excluded before you record the negative. An undefended negative result is worth less than no result at all, because it stops the search.

---

## Recording what you find

Every constant you accept goes into your findings document with, at minimum:

```
CHAR+0x18a   u16   remaining stat points
  derivation 1: setter clamps at 276          (dis.py 0x…)
  derivation 2: level cap 70 -> 69 * 4 = 276  (game rules)
  readers: stat window, reset item, HUD badge (xref.py offset 0x18a)
  confidence: HIGH / CONFIRMED       date: YYYY-MM-DD
```

Format and confidence vocabulary: `${CLAUDE_PLUGIN_ROOT}/shared/evidence-standard.md`. A bare address with no method attached is a bug in your notes, and six months later it is indistinguishable from a guess.
