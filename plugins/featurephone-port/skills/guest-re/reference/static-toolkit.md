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

Decode **every** Thumb `BL`/`BLX` halfword-pair in the whole image, sign-extend the 23-bit offset, mask `~3` for `BLX`. No CFG, no symbols, no heuristics — about a second over a 1.5 MB image, and complete.

**⚠ On Thumb-1, `bl` is not always a call.** The compiler emits `bl` as a **long branch** when `b.n`'s ±2 KB range cannot reach the target — typically to an outlined `else` arm. So:

* a "caller" list may contain sites that are really fall-through control flow;
* and, far more dangerously, a reachability analysis that treats `bl` as a call it need not follow will mark **live code dead**. See [patching.md](patching.md) §5.

Tell the two apart by disassembling the target's first instructions: a real function starts with `push {…, lr}` and sets up its own `sl`; a long-branch target does neither and often reads the caller's frame.

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
