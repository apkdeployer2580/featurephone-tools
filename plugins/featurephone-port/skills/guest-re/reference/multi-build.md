# One title, N builds — porting between two compiles of the same game

A carrier download package is not the title; it is one **compile** of the title. The same game
routinely exists as two carrier packages, and one carrier package routinely exists as two
revisions. This file is about supporting all of them from a single codebase: what actually
transfers between compiles, what never does, and the derivation routes that convert build 1's
reverse-engineering document into build 2's addresses at the lowest cost. It also names the class
of bug that only multi-build work produces — a value that is *plausible and wrong* rather than
absent — and the two structural shapes (an offset that is really an array, a value that is really a
bit test) that make an address-only anchor useless even when the address is right.

Tags: **(A)** generic · **(B)** worked example from the reference port, re-derive it ·
**(C)** do not copy. `⚠` marks an inferred claim. Conventions:
`${CLAUDE_PLUGIN_ROOT}/shared/evidence-standard.md`.

Not here: the four-constant address model itself ([address-model.md](address-model.md)); the gate
and table **architecture** that carries the per-build values
([build-keyed-anchors.md](build-keyed-anchors.md)); instruction rewriting and scratch space
([patching.md](patching.md)); classifying a dump and the loader differences between carriers
([platform-matrix.md](../../port-triage/reference/platform-matrix.md),
[boot-order.md](../../emulator-core/reference/boot-order.md)).

## Contents

- [0. The rule, stated sharply](#0-the-rule-stated-sharply)
- [1. Three levels of build identity — hash the native member first](#1-three-levels-of-build-identity--hash-the-native-member-first)
- [2. The experiment that settles "do addresses port" in an hour](#2-the-experiment-that-settles-do-addresses-port-in-an-hour)
- [3. Data layout: what ported, four counterexamples, the standing rule](#3-data-layout-what-ported-four-counterexamples-the-standing-rule)
- [4. A carrier-runtime property masquerading as a game property](#4-a-carrier-runtime-property-masquerading-as-a-game-property)
- [5. When the addressing model itself differs](#5-when-the-addressing-model-itself-differs)
- [6. Re-deriving an address in build 2 — seven routes, ranked](#6-re-deriving-an-address-in-build-2--seven-routes-ranked)
- [7. Cross-validation: arithmetic agreement beats pattern matching](#7-cross-validation-arithmetic-agreement-beats-pattern-matching)
- [8. Two shapes that defeat address-only thinking](#8-two-shapes-that-defeat-address-only-thinking)
- [9. The verification ladder for a second build](#9-the-verification-ladder-for-a-second-build)
- [10. Refutation log](#10-refutation-log)
- [11. Operational consequences](#11-operational-consequences)
- [12. Symptom table](#12-symptom-table)
- [13. Checklist](#13-checklist)

---

## 0. The rule, stated sharply

**(A) `HIGH / CONFIRMED`.** Three statements, in the order you need them:

1. **Semantics port.** The two builds are the same source through different compilers. Argument
   order, format codes, field meanings, protocol shapes, pixel arithmetic — all identical.
2. **Code addresses do not port. Not one of them**, and no amount of fuzzy matching recovers them
   (§2).
3. **Data layout mostly ports, and the exceptions are the entire problem** (§3).

The operational consequence is the posture: **build 1's RE document is a map of what to look for,
never a source of addresses.** A porter who assumes code addresses transfer scribbles into
unrelated code on build 2; a porter who assumes *nothing* transfers re-derives semantics they
already own.

### The backlog ordering rule that falls out of it

Classify every item in the port backlog before doing any of them **(A)**:

| tier | item needs | cost on build 2 |
|---|---|---|
| **a** | data offsets only | nearly free once §3 and §4 are settled |
| **b** | a code **site** to patch | one hunt from §6 per site |
| **c** | a callable guest **routine** | the most expensive, and the last to unblock |

Do them in that order, because each tier builds the tooling the next tier needs. **(B)** In the
reference port the item predicted cheapest — a host re-render of the minimap — needed *not a single
guest code address*, only the container flag from §4 and struct offsets already owned; it came out
pixel-identical on two maps (0/3500 and 0/1368 mismatched pixels) with a 0/5040 regression check on
build 1. The items that stayed blocked longest were exactly the tier-c ones.

⛔ Do not order the backlog from build 1's feature list. That list encodes what the *features* are,
not what each one *costs* on a rebuilt image — and it does not encode the dependency graph either
(§6.3 note on trigger anchors).

---

## 1. Three levels of build identity — hash the native member first

Triage normally asks one question ("which platform?"). A multi-build title needs three **(A)**:

1. **Which platform / carrier flavour** — answered by the archive's magic member. Owned by
   [platform-matrix.md](../../port-triage/reference/platform-matrix.md).
2. **Which carrier build of this same title** — because two carrier builds are different compiles
   with zero transferable code addresses (§2).
3. **Which revision within that carrier** — because one carrier can ship two revisions of the same
   package whose functions are uniformly relocated.

**The operational rule: hash the native member as the first command on any new dump, and look the
hash up.** Everything downstream that is keyed on the image — performance hooks, patch sites,
anchors — is keyed on that hash or should be.

### A minor revision is an address-reconfirmation job, not a re-RE job

**(B)** Between two revisions of one carrier package in the reference port, `.text` grew `0x144`
bytes and the re-hosted blitter moved **+0x74** with its prologue intact. The porting session
re-verified each clause of the hook's contract against the new binary instruction by instruction —
early-outs, sprite-descriptor loads, palette arithmetic, row-bytes rounding, argument slot — and
recorded that trace in the patch table's comment. Budget: hours, not a session. **(A)** The
method is: recognise the function by its byte shape, compute the displacement from one anchor
instruction, then confirm *clause by clause* rather than assuming a uniform shift held.

### The tell that a hash-keyed table missed

A hash-keyed hook table **falls back silently**: the game boots and runs, just at interpreter
speed. Judge installation from the boot line that names the hook, never from a throughput gauge —
a text-only screen legitimately reports zero hits. **(A)**

### ⚠ Key the anchor table at the granularity you verified at

**(B, ⚠ INFERRED)** The reference port keys its *hook* table on the image hash (correct: one entry
per revision) but its *anchors* table on the carrier only — while every address in that arm's doc
comments is annotated with one revision's version string. On the other revision the sole remaining
protection is the read-verify guard, which the repo itself calls probabilistic. **If you already
hash the image to choose hooks, hash it to choose anchors too** — the information is free at that
point. Granularity is [build-keyed-anchors.md](build-keyed-anchors.md) §5's subject.

### The version-numbering trap

**(B)** The descriptor's version field and the in-game splash version are **different numbering
schemes and they do not agree**: descriptor `01.00.07` ships a splash reading `VER 1.0.4`, while
descriptor `01.00.04` shows `VER 1.0.1`. A comment in the reference repo's own patch table had the
two schemes crossed and asserted that the wrong archive was the shipped one.

> **REFUTED.** *"The package version identifies the build."* Refuted by finding two numbering
> schemes in one project whose orderings are not aligned, after a patch-table comment had already
> shipped a wrong claim about which archive was live. It is attractive because both numbers look
> like versions of the same thing and one of them is printed on screen where you can see it.
> **Identify a build by the native member's hash, and say in the note that you did.**

---

## 2. The experiment that settles "do addresses port" in an hour

Do this once, at the start, and never argue about it again **(A)**:

1. Take **N samples** of raw bytes at fixed intervals across build 1's game-logic region, and
   search each as an **exact byte string** in build 2 — first at 16 bytes, then at 8.
2. Then try **fuzzy**: wildcard every `bl` target and every PC-relative literal load, search the
   residue.
3. Separately, take **one** function you fully understand in build 1, locate its counterpart in
   build 2 by any means, and diff them instruction by instruction.

**(B)** In the reference port: 152 samples at 4 KB intervals → **0 hits at 16 bytes**, 2 hits at 8
bytes (noise). Fuzzy matching with `bl` targets and literal loads wildcarded was a **total
failure**. Even the save-record base-name strings differ in text between the two builds. And yet
the instruction-level diff of one blitter showed **identical semantics** — 13 arguments in the same
order, the same format codes, the same pixel arithmetic — with only the container dereference
differing (§4).

> **REFUTED.** *"The two builds are the same code, so a fuzzy signature search will relocate the
> functions I already know."* Refuted by the corpus search above: zero 16-byte matches over 152
> samples, and fuzzy matching no better. It is attractive because the diff of any single function
> *does* show a near-identical algorithm — the semantics survive, the register allocation,
> scheduling, literal pools and call idioms do not. **Consequence if carried forward:** you patch
> at an address that is data or unrelated code on build 2, and the read-verify guard (probabilistic
> by construction) may not stop you.

---

## 3. Data layout: what ported, four counterexamples, the standing rule

**(A) `HIGH / CONFIRMED`.** Struct offsets mostly survive a carrier recompile. The exceptions
cluster, and they do not share a single delta — so you cannot "fix up" a shifted region with one
constant.

**(B)** In the reference port's main record, dozens of offsets ported unchanged and **four** did
not:

| field | build 1 | build 2 | delta | how it presented |
|---|---|---|---|---|
| coarse state byte | `+0x7c` | `+0x78` | −4 (one word earlier) | a mode gate reading the wrong state |
| a HUD field | `+0xa384` | `+0xa370` | −0x14 | wrong number drawn |
| the save-slot word | `+0xa3f8` | `+0xa410` | +0x18 | writes landing on the wrong slot |
| a popup pre-emptor byte | `+0xa220` | — | shifted neighbourhood | read a *different live field*, refused a whole feature (§10 R8) |

Everything the host read **below ~`0xa000`** read back correct against on-screen numbers: the
level/exp/hp/sp block, the inventory grid handle, the window id, and the popup cluster.

**The standing rule (A):**

- **Never introduce an offset on a new build without a live read.** The failure mode is not
  absence — it is a *plausible wrong value*, which ships and comes back months later as a gameplay
  complaint.
- **Treat any offset in the record's high region as guilty until measured.** That is where the
  reference port's exceptions clustered, and it is where a compiler's layout freedom is largest.
- Track **"active but unverified on this build"** as a state distinct from "gated off", and record
  each unverified item's *failure direction*. Both practices, and the periodic device audit line
  that closes them, belong to [build-keyed-anchors.md](build-keyed-anchors.md).

> **REFUTED.** *"The data layout is identical between the builds, so the offsets can be copied
> wholesale."* Refuted by four device-visible bugs, each traced to one offset, with deltas of −4,
> −0x14 and +0x18 — i.e. not a uniform shift that a single correction could absorb. It is
> attractive because the *first twenty offsets you check are all correct*, which is exactly the
> evidence pattern that produces overconfidence.

---

## 4. A carrier-runtime property masquerading as a game property

**(A) `HIGH / CONFIRMED`.** A dereference convention that appears throughout a game's RE notes may
belong to the **emulator's carrier runtime crate**, not to the game. When it does, a single
build-keyed flag resolves *every container in the game at once* — and the wrong setting fails
silently.

**(B)** The `*(*(X)) + 8` double dereference documented everywhere in build 1's notes exists
because that carrier crate's `WIPICContext::data_ptr` is `*(handle) + 8`. The other carrier crate's
`data_ptr` is **the identity function**, so the same game's allocations are direct pointers there.
One flag — `container_data_off() -> Option<u32>`, `Some(8)` / `None` — simultaneously fixed the
inventory grid, the area object, the item database, the map arrays, the offscreen framebuffers, the
popup label array and the sprite descriptor.

### How to test for it on a new pair **(A)**

1. **Read the two runtime crates' `data_ptr` (or equivalent allocation-handle accessor).** This is
   a five-minute read and it is authoritative — the convention is one function.
2. **Resolve one container both ways at runtime** and compare the decoded records against what is
   on screen. Only one convention produces the right item names/counts.
3. **Route every container through one helper** (`deref_container()`), then grep for surviving
   unconditional double-dereferences. In the reference port three already-shipping functions were
   still hardcoding the wrong hop.
4. **Add a selftest that runs both conventions against both data shapes**, because the wrong-but-
   plausible direction is the one that ships.

**Acceptance evidence (B):** after the change, a host re-render of the minimap was pixel-identical
on two maps (0/3500 and 0/1368 mismatched pixels), with a 0/5040 regression check on build 1.

> **REFUTED.** *"The indirect-handle convention is a property of the platform or of the game, and
> if your first pointer hop lands 8 bytes short of something that looks almost right, you have
> found it."* Refuted by reading both carrier runtime crates: one `data_ptr` is `*(handle)+8`, the
> other is `Ok(memory.0)`. The "8 bytes short" advice describes only the **benign** direction.
> The dangerous direction is the other one: **double-dereferencing a direct pointer reads the
> container's first word as an address**, and if that word happens to land inside the heap window
> it passes every plausibility guard and yields *wrong item records* rather than `None`. It is
> attractive because build 1's notes state the convention hundreds of times, so it reads as a fact
> about the game.

**Generalise it (A):** when *many apparently unrelated features* are simultaneously wrong on a new
build, stop porting features and go looking for a convention that lives in the runtime rather than
in the game. That is the shape whose fix is one line.

---

## 5. When the addressing model itself differs

**(A) `HIGH / CONFIRMED`.** The second build may not use the same addressing model at all. In the
reference port, build 1 is an RWPI image that self-rebases at entry (image base, BSS size from the
member's filename, an `sl` static base, a relocation delta), and build 2's native member is a
**fully linked 32-bit ARM ELF with live section headers** — the loader maps each section with
`sh_addr != 0` at its own vaddr, entry is `e_entry`, and there is no self-rebase.

What **stops applying**: the four constants, the `[sl,#imm]` annotation, the static-slot dump, the
relocation solver. `guest_address == sh_addr` is the whole model. The details belong to
[address-model.md](address-model.md) and
[boot-order.md](../../emulator-core/reference/boot-order.md).

What **becomes easier**, and this is why it matters here: on an absolute-addressing image, code
that touches a static **embeds the static's address verbatim in a literal pool**, so "who touches
this field?" becomes a plain word search (§6.3). The porting order inverts — pin the data model
first and every later code hunt is cheap.

Two consequences worth carrying forward:

- **(B)** The reference port's section table put `.text` at file offset `0x34` / vaddr `0x1000`,
  `.data` at `0x1400000` and `.bss` at `0x1500000`. A tool that hardcoded only the `.text` mapping
  could not see `.data` at all — and `.data` turned out to hold a 20-way overlay dispatch table
  indexed by a record byte, i.e. a first-class answer to "who draws this screen". **An ELF-aware
  tool must read the section headers, not one section's mapping.** **(A)**
- Because sections are mapped **page-rounded**, the bytes between the declared `.bss` end and the
  end of that page are mapped memory belonging to no section — better host scratch than
  proven-dead code, with a cheaper proof. That technique is [patching.md](patching.md)'s §5.

---

## 6. Re-deriving an address in build 2 — seven routes, ranked

Ranking below is by **measured cost in the reference port** where a comparison exists, and by
judgement where it does not — the two are marked. All seven are **(A)** procedures; every constant
inside them is **(B)**.

| # | route | best for | cost |
|---|---|---|---|
| 6.3 | literal-address search on an absolute-addressing image | "who reads/writes this field" | lowest, once the data model is pinned |
| 6.2 | string → literal pool → loader instruction | a named subsystem (save, shop, message) | **measured faster than value search** |
| 6.4 | arithmetic signature (constant multiset in a window) | a known formula | one pass, self-validating |
| 6.5 | base-accessor call-site sweep | bulk re-confirmation, and negative results | one tool run, hundreds of sites |
| 6.1 | value clustering on a number that is on screen | the anchor itself, when you have nothing | needs two runs and careful filtering |
| 6.6 | single-word isolation from two deterministic runs | an array whose contents you cannot predict | two probe runs |
| 6.7 | differential profiling | a pixel loop / a hot path | two probe runs, one long |

### 6.1 The number on screen (value clustering)

When you have no anchor at all, search live memory for a **number the player can see** — level,
current HP, gold — and intersect the candidate sets across states in which that number changed.
This is how the reference port first located the main record base on build 2 **(B)**; §6.4 later
re-derived the same base independently, which is what promoted it to two derivations.

Two filters make it usable **(A)**:

- **Compare frame hashes before diffing memory.** If the two runs produced identical frames, the
  input you varied hit a clamp or a wall and the experiment failed — an empty diff then means
  nothing about the field.
- **Most heap diff hits are framebuffer or tile pixels**, recognisable by 16-bit repeating values.
  On an absolute-addressing build, prefer diffing `.data`/`.bss` over the heap: that build keeps
  statically what the other build allocates.

### 6.2 String → literal pool → loader instruction

The highest-yield route for **code**, and it needs no emulator. The refinement that makes it work
is the **selection rule**: choose a string whose *reference count* is small — a format string, a
record base name — not a string that is merely distinctive.

**(B)** The record-name format string had exactly **three** referencing sites in the whole image.
Disassembling those three yielded the record writer (which prepends a 16-byte header), its wrapper
(which builds `"%s_%d"` from a slot word), and the reader. The session recorded it as *"static XREF
gave two functions straight away — faster than value searching"*, which is the one measured
head-to-head in this table.

**(B)** Second application, same route: the save routine is the site that loads **both** record-name
strings. Five candidates loaded both; the one that called the writer **twice** was it — and its
literal pool then named the entire contract in one place (serialiser, slot summary, writer, both
name strings), each as an `ldr rN,=addr|1` entry.

⚠ **On this build family, module-crossing calls are `ldr rN,=addr|1` + a `bx rN` veneer, so a
`bl` call graph does not find call sites.** Compute the literal's address as `(pc & ~3) + 4 + imm`
and search for the *value*. Two investigations in the reference port read the same `bl` as two
different functions; the literal arithmetic settled it.

### 6.3 Literal-address search on an absolute-addressing image

Once the record's base is known, a throwaway scanner (~150 lines; see
[static-toolkit.md §12](static-toolkit.md#12-the-throwaway-scanner-pattern)) answers three query
shapes over the image **(A)**:

- **(a)** every 4-byte aligned word whose value falls in a given address range, **plus** the
  `ldr rX,[pc,#imm]` instructions that load it — resolved as `(pc & ~3) + 4 + imm*4`;
- **(b)** the same, restricted to one struct field;
- **(c)** `movs/cmp/adds/subs rX,#imm8` scans for a known layout constant.

**(B)** This located a HUD renderer's site through a panel-width immediate, and — decisively —
proved that two suspicious counters were **HUD-only**, because an address-range query returned
only readers inside the block the host was already skipping. It was the common entry point for
every remaining item on that build: the save chain, the quick-bind work and the reward-multiplier
work all started here.

**The dependency-graph payoff (A).** Run query (a) over the *trigger* surface before porting
features one by one. **(B)** Four items the port's coverage matrix listed as separately blocked —
two reward multipliers, an equip-restriction relief and a full-restore — all depended on **one**
address: the popup row-label array, the only thing that can tell the host a particular in-game
action is about to run. One audit line printing `handles=None` explained all four, and locating
that single word made two of them immediately functional. Feature order written from build 1's
feature list will not surface this.

### 6.4 Arithmetic signature: the constant multiset in a window

If you know the **formula**, you know the constants the code must materialise. Require them to
**co-occur inside a small window**, and add a shape filter **(A)**.

**(B)** For a kill-reward formula: `#0xd`, `#0x82`, `#0xa`, `#0x64` within one `0x38`-byte window
with at least two `muls` → **exactly one site image-wide**, whose ~20 instructions corresponded
one-to-one with build 1's. For a HUD panel origin: `subs rX,#0xaa` gave 14 hits, and only one was
followed by `asrs #1; adds #2` — that one is the site.

### 6.5 The base-accessor call-site sweep

Find the game's **own** base accessor, learn the constant offset between it and your anchor, and
every literal offset in the image becomes directly translatable **(A)**.

**(B)** A two-instruction thunk (`ldr r0,[pc,#0]; bx lr`) returned `anchor + 0x28` and was called
from **647 sites** reached as `ldr rN,[pc]=thunk|1; bl <bx rN veneer>`. So *game-offset k* ==
*host-offset k + 0x28*. That single relation independently re-confirmed **twelve** offsets the host
was already using (level, exp, max-hp, hp, sp, two badge counters, the popup cluster, the grid
handle, the window id), and revealed that the six writers of a popup tag byte store exactly the
values `100..105` — matching a separately measured `0x64`.

**The negative result is worth as much as the positive one (A).** Local abstract interpretation
over those 647 sites found the only `strb #1` in a `0x100`-byte range, and showed that a
strongly-suspected flag byte was read or written by **no code at all**. A byte no code reads cannot
be a gate — that retires a candidate which looked confirmed on offset, value and timing
simultaneously.

### 6.6 Single-word isolation from two deterministic runs

When the contents are **unpredictable** (heap pointers, handles), search for a **structure**, not a
value **(A)**: *"N consecutive words that are all plausible heap pointers while the UI is open, and
all zero when it is closed"* is a lifecycle-shaped predicate specific enough to be unique, because
the game's teardown nulls every handle.

**(B)** Two deterministic probe runs (one dumping with a popup open, one after closing it) over a
**19,030-word** statics region produced exactly **one** candidate run, of length 8. Dereferencing
it produced the two label strings visible on screen, at a hop count that followed from §4's
container convention rather than being a new discovery. The same dump pair validated eight
neighbouring cluster offsets for free — which is the argument for **always dumping the whole
record** rather than a window (§8.1).

Two traps, both **(A)**: an empty diff usually means the runs never diverged (check frame hashes
first, §6.1), and heap hits are mostly pixels.

### 6.7 Differential profiling for a pixel loop

One profile buries a rare effect. Take **two runs — one stopped before the event, one containing
it — and pick the sample cluster whose count doubled** **(A)**.

**(B)** A field profile put ~35 distinct PCs, each ~0.62 % of samples, **evenly** across a
`0x86`-byte span — 21 % of all samples in one loop, later decoded as a per-pixel alpha blend
costing ~44 guest instructions per pixel. A rare combat effect's loop was isolated by comparing a
2,950-frame run (before the fight) with a 4,200-frame one (containing it).

Three readings that generalise **(A)**:

- **An evenly distributed cluster is a tight loop, not a hot function** — that is the signal to
  disassemble a *range* rather than look for a symbol.
- **A profiler pointing repeatedly at a `bx rN` veneer table is pointing at an indirect-call
  trampoline**, not at a function.
- One title can have **three distinct per-pixel loops** needing three separate hooks (opaque sprite
  blit; translucent rectangle fill; translucent sprite blit, which reads the destination and runs a
  two-stage 32-stride LUT at roughly 4× the per-pixel cost of the opaque one). The re-hosting
  itself is [guest-pixel-loops.md](../../emulator-core/reference/guest-pixel-loops.md).

---

## 7. Cross-validation: arithmetic agreement beats pattern matching

**(A)** Pattern matching gives a *candidate*. Agreement between two facts that were derived
independently gives a *proof*, and on a rebuilt image it is the cheapest way to satisfy the
two-derivations rule without a device.

**(B)** The reward function found in §6.4 had exactly one reference in the image (a function-pointer
literal). Its caller did `r0 = accessor(); r0 += 0x8c; f(r0, enemy)` — and `+0x8c` lands on a
sub-struct documented from build 1 — while the function body's `[r4,#0x30]` lands on the documented
reward offset. **If the site were wrong, that agreement could not occur.** As a bonus, the same
chain re-derived the record base independently of the value search that first found it (§6.1),
which is what turned one derivation into two.

Ask, every time: *what does my model predict that a rival model does not?* Then check that. "It
works" confirms that something changed, not that your model of why is right.

---

## 8. Two shapes that defeat address-only thinking

### 8.1 The offset that is really an ARRAY

**(A) `HIGH / CONFIRMED`.** An offset that resists every search may be **element 0 of an array
indexed by a runtime word** — a save slot, a page index, a tab. The tell is diagnostic: **several
sessions each measure a different, correct-looking value**, and each one is refuted by the next.

**(B)** A flag byte defied three candidate offsets across multiple sessions. The resolution: the
game's own store computes `base + *(base + slot_word) + K` — one byte per save slot. Build 1's
documented offset was **element 0**, one device measurement was **element 1**, and a candidate
previously written up as "refuted" was **element 6**. The host had been writing a fixed element
while the player's character was in another slot, so the feature had **never once executed** in the
shipped build.

The methodology recorded alongside it **(A)**:

1. **Do not narrow the watch window.** A window centred on the other build's offset can only
   confirm or mislead — it cannot discover. Reading tens of KB twice a second is a `memcpy`; there
   is no reason to narrow.
2. **Auto-mute any byte that changes on N consecutive samples**, or counters bury the signal.
3. **Make the game write the field itself** (buy or use the in-game item that sets it) — far
   cheaper than finding the writer.
4. **"It changed at the right moment" is not evidence.** Block writes move whole neighbourhoods.
   The answer requires the host writing that value to reproduce the **effect**.
5. When you find the store, **copy its address arithmetic verbatim, including the index load**, and
   **refuse** rather than falling back to element 0 when the index cannot be read. Log all elements
   so the array is visible.

> **REFUTED.** *"Candidate `+0x2dd` is refuted; the offset is a constant and we simply have not
> found it."* Refuted by reading the game's own store, which indexes by a slot word — after which
> build 1's documented offset, the device measurement and the "refuted" candidate were all the same
> array at indices 0, 1 and 6. It is attractive because each individual measurement *was correct*,
> and because mutual contradiction between correct measurements reads as measurement error rather
> than as a missing index. **Cost: three sessions and one shipped-but-dead feature.**

**Standing question to apply for free: is this offset actually element 0 of something?**

### 8.2 The value that is really a BIT TEST — read the reader

**(A) `HIGH / CONFIRMED`.** The value an anchor must carry is decided by **how the game tests it**.
A byte tested as signed-negative can never be satisfied by `1`, however right the address is.

**(B)** The reader recomputed the same address and did `ldrb; lsls #24; asrs #24; cmp #0; bge skip`
— a bit-7 test. That is why the game's own store builds `0x81` as `movs r2,#0x7f; rsbs r2,r2,#0`.
And the reader **clears** the byte on the branch it takes, so the feature is one-shot per event:
the host must **re-assert on a timer**, not write once.

**Make the anchor a triple, not an address: `(offset, index-source, value)`.** **(A)** An
address-only anchor invites writing a plausible boolean into a bitfield, shipping a silent no-op,
and hiding the re-assert requirement entirely.

**Corollary — a null immediate scan proves nothing.** **(A)** A search for "any `movs rX,#0x81`
followed by a `strb`" returned **0 sites image-wide** and was used as grounds to doubt the anchor;
the compiler had synthesised the constant. Constants get built (negate, shift pair, add pair,
reciprocal multiply) and addresses get computed from indices, so **write the search's blind spot
down next to the null result** — and do not flip a shipping table to `None` on an inference.
**(B)** The reference port did exactly that: it recorded that its three failed searches "would
MISS a runtime-computed index", and the next session's answer was a runtime-computed index. Writing
down the limits is what made the next person dig there first.

### 8.3 Which operand you rewrite is a correctness decision

**(A)** When a patch from build 1 is re-derived on build 2, the site you land on may offer two
operands, and **choosing the wrong one silently changes a field's signedness**.

**(B)** The obvious port of an equip-restriction relief was to rewrite the *requirement* load,
which meant turning `ldrsb` into `ldrb`. That flips any item whose requirement byte is `>= 0x80`
from allowed to blocked — and `-1` meaning "no requirement" is exactly what a C `signed char` field
invites. The shipped fix rewrites the **other** operand (bias the player level instead) and proves
equivalence from that field's **provable range**: the game caps level at 70, so `ldrh` and `ldrsh`
agree for every value it can hold.

Two rules **(A)**:

- **Prove equivalence from a range, do not argue the hazard away.** If you cannot state the field's
  range, you have not finished.
- **The same feature can need a different patch *shape* per build** (one halfword register-move
  rewrite versus a two-halfword load rewrite). Carry `Option<(addr, Vec<u8>)>` in the patch
  plumbing from the start, not one halfword. Encoding and re-encoding are
  [patching.md](patching.md)'s subject.

---

## 9. The verification ladder for a second build

Climb it in order; each rung is cheap and rules out a different failure **(A)**.

1. **Static read.** Disassemble the candidate site and record the bytes you expect to displace.
2. **Live poke.** Run the headless probe with a poke flag that logs the bytes it displaced. **The
   pass condition is that the displaced bytes equal what the static read predicted.** **(B)** In
   the reference port every code site shipped only after such a run — ten sites in one widget, all
   ten logging `orig -> new (ok)`. This matters because static reads and live memory can differ
   (load-time patches, a wrong section mapping), and the in-app read-verify guard fails *silently*
   by design: it returns `None`.
3. **Frame-hash A/B.** For anything that touches pixels, run the deterministic probe with the host
   reimplementation off and on and require **byte-identical frame hashes** at every dumped frame.
   **(B)** Three hooks: 6/6, 5/5 and 7/7 dumped frames identical, with guest instruction counts
   down 34 %, 64 % and 24 %.
   ⚠ The differential check is gated on an explicit dump-frame list — an invocation without it
   prints nothing, which looks exactly like "no problem found".
4. **End-to-end through the game's own loader**, where one exists. **(B)** For the ported save
   chain: the host drove the real step machine to write a record, then the **same database
   directory was booted again** and play resumed at frame ~1,400, where creating a character from
   scratch takes ~7,800. The frame-count argument is the trick worth copying — **pick an observable
   that can only be produced by the path you claim.**
5. **Device**, for the states a script cannot reach. **(B)** A driving script of "press OK every
   120 frames from frame 260" reached the field at ~frame 7,800, but a battle, a levelled-up
   character, an active buff, an equipped secondary weapon and a registered quick slot were **not**
   reachable from a scripted run and became the device checklist. **Write both halves down** — the
   script is a project asset, and its reachability boundary decides which items can close without a
   phone. The audit line that closes the rest is
   [build-keyed-anchors.md](build-keyed-anchors.md) §12.

**A cheap cross-build signal worth knowing (B):** across two revisions of one carrier the probe's
blitter **call count was identical** (226,008 / 226,008) and the frame hashes matched except on the
title screen, which differs only in its version string. Identical counts from a deterministic
runner are positive evidence that a minor revision really is minor, *before* you spend a session
re-deriving.

---

## 10. Refutation log

Eleven things the reference port believed, wrote down, and in several cases shipped.

1. Fuzzy signature matching relocates known functions between builds — **§2**.
2. The data layout is identical, so offsets can be copied wholesale — **§3**.
3. The `*(handle)+8` convention is a property of the game or the platform — **§4**.
4. The offset is a constant; the other candidates are refuted — **§8.1**.
5. The package version identifies the build — **§1**.
6. A null immediate scan shows the constant is not stored — **§8.2**.

> **7. REFUTED.** *"The popup tag constant differs per build, so gate it per build."* Refuted by
> re-measuring with the item class that actually has the registration row: the value matched build
> 1's exactly, including the row count. The first measurement had used a two-row popup for a
> different item class. **Why it was attractive:** on a multi-build port, *"it differs per build"*
> is the cheapest available explanation and therefore the one that gets written down. Each such
> claim adds a permanent branch to the code and a permanent wrong sentence to the notes.
> **The rule: do not attribute an observed difference to the builds until you have reproduced the
> same SITUATION on both.** Half of this port's early "build differences" were different
> situations.

> **8. REFUTED.** *"A host-side guard that duplicates one of the game's own checks is portable."*
> Refuted by an asymmetric device symptom: on build 2 that guard refused **every** consumable
> popup while skill popups worked. Mechanism: the guard existed only because build 1's commit path
> **bypasses the game's key router** (a direct call into the key handler), so the host had to
> re-implement the router's refusal; build 2 commits by synthesising an ordinary keypress, which
> rides the router — so the game applies its own check and the host's copy contributes only a read
> of a guessed address that now names a different live field. **The fix made the guard's presence a
> COUPLING to the commit shape**, pinned in a selftest. **The diagnostic move to keep:** when two
> paths share one machine and only one fails, the difference is in the path-specific gate, not the
> machine.

> **9. REFUTED.** *"The one thing remaining for this feature is that guest function's address"* —
> asserted twice in the plan document. Refuted by two deterministic probe runs, identical except
> that one poked the highlighted-row byte before an ordinary OK key: the game's own dispatcher read
> that byte on key arrival and performed the **entire** operation itself. **Build 2 needed FEWER
> guest addresses than build 1, not more.** **Why it was attractive:** build 1 solved it by calling
> the handler, so the handler looked like the mechanism. **The generalisation:** before hunting a
> handler's address, ask whether *writing the state the handler reads and synthesising its trigger*
> achieves the same thing — and note that the result is strictly more robust, because no guest
> address is involved and nothing there can go stale.
>
> Its sequel, also refuted: *"synthesising a key is equivalent to calling the handler."* It is not
> — the key is delivered a tick later, so the game's own clamps run in the gap. The rewrite had
> shrunk a row-count byte to 4 while the row to commit could be 5, and the guest's cursor clamp
> pulled it down inside that window. **Restore any state you temporarily shrank before the key
> lands.**

> **10. REFUTED.** *"A live-record liveness predicate can require `hp <= hp_max`."* Refuted on
> device: unequipping gear drops max-hp below current hp, the host concluded "no character", and
> the HUD and quick slots vanished. **Why it was attractive:** it is a true invariant *most of the
> time*, and liveness predicates are needed precisely because a **static (`.bss`) anchor cannot be
> null-checked** the way build 1's pointer-global anchor can. **A gauge reading high is a reason to
> clamp at draw time, not evidence that the record is dead.** The deeper lesson: when the anchor
> KIND changes between builds, every gate written against the pointer build's implicit null check
> silently inverts — one title-screen feature on that build had **never drawn once**, because its
> gate called the liveness-checking resolver and the shell screens are exactly where no character
> exists. Separate *"give me the base address"* from *"is there a live record"*.

> **11. REFUTED.** *"The generic compiler-library hook patterns will pay off once they install."*
> Background: the patch installer `return`ed after the first matching hash-keyed entry, so six
> generic hooks had **never installed on any build** — and the boot log said `Installed 1 hooks`,
> which reads as a success line. After the merged installer fixed that, the measured gain on build
> 2 was **exactly zero**: identical instruction, SVC and batch counts. **Why:** generic byte
> patterns encode a **compiler's** idioms, not a game's — those were written against build 1's
> frame-relative down-counters, and build 2 is stack-spill-heavy with `bx rN` call veneers. The
> refactor was kept (structurally correct, zero regression) but the *expected payoff must be
> re-derived per compile.* Installer mechanics belong to
> [boot-order.md](../../emulator-core/reference/boot-order.md).

Two more that recur and have no other home here:

- **Site counts are not portable between builds.** One build had **12** sites for a widget's
  coordinates, the other **10**, because two of that build's draws are shared by both paths. After
  finding one site, enumerate the rest before writing a line, patch the whole widget or none of it,
  and assert the count and the constant multiset **per build** in a selftest.
- **"This carrier needs no JVM attach" is a claim about PATHS, not about a carrier.** The second
  carrier crate carried an explicit comment saying the guard was unnecessary; it was true for most
  paths and false for the record-database path, and the first host-originated save on that build
  panicked. A headless probe does not reproduce it. Re-test per subsystem.

---

## 11. Operational consequences

### Switching which build ships can be zero lines of code

**(B) `HIGH / CONFIRMED`.** With archive detection, per-build tables and hash-keyed hooks already
in place, replacing the bundled archive with the other carrier's package changed **nothing else**:
detection picked the other emulator, the hash-keyed hook installed, build 1's features gated
themselves off, and the boot log carried one warning line. Reverting is copying the old archive
back. This is the acceptance test for the whole architecture.

### The other build's saves become invisible, not deleted

**(A)** The save namespace comes from the descriptor's namespace id and the record names come from
the guest's **own** name strings — and both differ between carrier builds of the same title. So the
two builds' save sets **coexist in one install**; the other set reappears when the archive is
swapped back. Log the resolved record name and namespace at open time so the support report you
*will* receive after a build swap is one grep. Two adjacent warnings: never change the
save-namespace id after release, and **a reinstall must preserve the app's private storage** — a
signing-key mismatch forces an uninstall that destroys saves, layouts and settings.

### Which build is actually shipping is often not in version control

**(A) `HIGH / CONFIRMED`.** Generated or patched game assets are typically gitignored, so a session
on another checkout can commit source, docs and a version bump while **the asset it baked never
travels**. `git log` cannot describe the build you are shipping.

**Give the asset generator a `--check` mode** that rebuilds the artifact in memory from the
pristine original and byte-compares the shipped one, exiting non-zero on mismatch — and make it the
**first command of any session that touches assets**. **(B)** The first time it ran it exited
non-zero reporting *"shipped 729 B, tables produce 727 B"*, proving that machine's archive had
never received the previous session's asset work. Without the gate that session would have shipped
an APK missing the previous session's change, with the symptom *"I definitely fixed it and the
device shows the old text"* — a failure this repo had already hit three times. The generator
pipeline is [generated-assets.md](../../archive-prep/reference/generated-assets.md).

### Re-check both sides of every handshake

**(B)** An infinite loading screen on build 2 was chased through the emulator (fatal on unmapped
platform slots) and the host socket layer (clean) before landing on the **replacement server**,
which accepted only build 1's **carrier discriminant byte** in the connection hello and answered
nothing at all — and since the hello is the only unsolicited frame, the client waited forever.
**(A)** The transferable part is the triage signature, which had been written down in advance: *if
the server rejects the frame, the transport log shows send/recv working normally* — "the transport
looks perfect" is positive evidence that the fault is above it. Fix with a **set** of accepted
carrier bytes plus two tests (accept the new one, reject a bogus one); copy the discriminant-set
pattern, not the byte values. Framing is
[framing.md](../../server-revival/reference/framing.md)'s subject.

---

## 12. Symptom table

| Symptom | Cause | Fix |
|---|---|---|
| A feature that works on build A does nothing on build B; no error, logs look normal | one blanket "are the RE'd addresses valid" flag, false on B, asked at the feature's entry | split the gate per item — [build-keyed-anchors.md](build-keyed-anchors.md) |
| A host feature shows plausible-but-wrong values (counts, icons, quantities) instead of failing | the container dereference convention is the **carrier runtime's**, and it was hardcoded | §4 — one build-keyed hop count, one `deref_container()`, both-conventions selftest |
| An offset believed portable reads a live but unrelated field, and only one path misbehaves | most offsets survive a recompile, not all; the high region is where they move | §3 — live read before introducing any offset; audit line with reference fields beside suspects |
| A host overlay on build B has literally never drawn, and its code is present and un-gated | anchor KIND changed (pointer global → `.bss` static); the liveness gate inverted | §10 R10 — separate "base address" from "is a record live" |
| A guest write is at the right offset with the right value and has no effect | the offset is element 0 of an array, **or** the reader tests the byte signed | §8.1, §8.2 — copy the store's arithmetic; make the anchor an (offset, index-source, value) triple |
| A static search for the constant the game must store returns zero sites image-wide | the compiler synthesised the constant, or the address is computed from an index | §8.2 — treat a null scan as "my search cannot see this" and record its blind spot |
| Two deterministic runs meant to isolate one word produce an empty diff | the runs never diverged — the varied input hit a clamp | §6.1 — compare frame hashes before diffing memory |
| A patch is byte-verified on build B and the widget only half moves | the compiler outlined the block; site counts differ per build (12 vs 10) | §10 — enumerate all sites first; assert count + constant multiset per build |
| Performance hooks missing on a dump that is "the same carrier build" | it is a different **revision**; a hash-keyed table falls back silently | §1 — hash the member first; judge from the hook's boot line, not a throughput gauge |
| Player reports "my saves are gone" right after a build swap | namespace id and record names both differ per build; both sets coexist | §11 — restore the previous archive to see them; never change the namespace id |
| "I fixed it, rebuilt, and the device shows the old behaviour" | the build-identity artifact is gitignored and never travelled | §11 — `--check` rebuild-and-compare as the session's first command |
| The emulator exits the instant the game touches a whole subsystem on build B | that carrier crate names a few slots and makes the rest fatal | [wipi-c-recipes.md](../../emulator-core/reference/wipi-c-recipes.md) |

---

## 13. Checklist

Before any address derived on a second build is built on:

- [ ] The native member was **hashed** and the hash recorded; the note identifies the build by that
      hash, not by a version string.
- [ ] The addressing model was determined for **this** member (§5), not assumed from build 1.
- [ ] The corpus experiment of §2 was run once, so nobody re-argues whether addresses port.
- [ ] The container/handle convention was read out of **the carrier runtime crate**, and every
      container goes through one helper (§4).
- [ ] Every offset introduced on this build has a **live read**, and high-region offsets are
      treated as guilty until measured (§3).
- [ ] Each new address has **two independent derivations**, and the two routes are named (§6, §7).
- [ ] For any flag-like anchor: *is this element 0 of an array?* and *how does the reader test the
      value?* were both asked and answered (§8.1, §8.2).
- [ ] The anchor is recorded as **(offset, index-source, value)**, and the re-assert requirement is
      stated if the reader clears the byte.
- [ ] A patch's rewritten operand was chosen with a **signedness and range argument** (§8.3).
- [ ] The site was confirmed by a **live poke whose displaced bytes matched the static read**, and
      pixel work by a **frame-hash A/B with an explicit dump-frame list** (§9).
- [ ] Every search that came back empty has its **blind spot written down beside it**.
- [ ] Any claim of the form "this differs per build" was checked by reproducing the **same
      situation** on both builds (§10 R7).
- [ ] The states the probe **cannot** reach are listed, and they are the device checklist.
