# Patching the guest — host writes RAM after boot, never the shipped binary

## The rule that frames everything

> **You do not modify the game binary. The host writes guest RAM after boot, verifies before every write, and re-asserts forever.**

Consequences, all of them good:

* The archive stays byte-identical to the dump the user supplied. Nothing you ship contains modified game code.
* A different build of the game is left **untouched** instead of corrupted, because every write is conditional on reading the documented original bytes first.
* Features are switchable at runtime with no rebuild, and a player who never enables one runs a **bit-identical** guest.
* There is no patch-order problem, no "did new-game reset it" problem, and no migration when the player loads a save.
* **The emulator's own optimisation hooks keep working.** Host reimplementations of the guest's pixel loops are installed from a table **keyed by the guest image's hash** (§3c). Edit one byte of the image and every one of those hooks silently fails to match — no error, no log line, just a large share of the instruction budget handed back to the interpreter. In the reference port that was **a third to two thirds** of the guest-instruction budget on the hot paths **(B)**. A load-time RAM patch does not move the hash.

The only shipped-asset modification that is ever acceptable is **text** — a data member you repack, which is `archive-prep`'s job, not this skill's. Even there, prefer a runtime patch when the change is conditional: §9.

### Extending a guest data table is an asset row **plus three code constants** **(A)**

Adding a row to a table the game loads from its archive — an item, a shop line, a skill — is not an asset edit. It is an asset edit **and every hardcoded bound in the code**, because no reader bounds-checks the index:

| what | where it lives | what happens if you forget it |
|---|---|---|
| the row itself | the archive member | the constants promise a row the file does not contain, and the loader reads past the last record |
| the **row count** | a per-category constant in code | the new row is never read, or the loader walks one row past the end |
| the **allocation size** | a hardcoded expression such as `count << 1` | the extra row is written past the end of the allocation |
| the **teardown bound** | a compare against the stock maximum | the last row is never freed, or the free walks past the end |

Reference shape **(B)**: adding a 16th row to a 15-row category needed the asset edit plus **three** load-time constant patches, verified by dumping the live table in a headless run — the new row present, the row after it all zeros. **Ship those constants as load-time RAM patches, never as edits to the image**, for the hash reason above.

**The symptom when either half ships without the other (B):** the game boots, shows its title screen, and then dies inside the allocator with a nonsense size, because a parser that ran off the end of the table read a length field as a negative number. Nothing in that failure points at an asset.

## Contents

1. [Before you write any code](#1-before-you-write-any-code)
2. [The helper contract](#2-the-helper-contract)
3. [The three patch shapes](#3-the-three-patch-shapes)
4. [Hazards of block-skipping](#4-hazards-of-block-skipping)
5. [Dead guest code as scratch space](#5-dead-guest-code-as-scratch-space)
6. [A measured value is a band, not an invariant](#6-a-measured-value-is-a-band-not-an-invariant)
7. [Re-assert cadence and idempotence](#7-re-assert-cadence-and-idempotence)
8. [Testing patches without a phone](#8-testing-patches-without-a-phone)
9. [Patching what the game says](#9-patching-what-the-game-says)
10. [Checklist](#10-checklist)

---

## 1. Before you write any code

**Re-read the original bytes at every patch site, from the binary, immediately before implementing.**

```bash
python "${CLAUDE_PLUGIN_ROOT}/tools/dis.py" -t target.toml 0x13d0a0 0x13d0c0
```

Confirm the halfword you are about to depend on is actually there. An RE note — including one you wrote last week — is a single source. Notes drift, sites get transcribed with a digit swapped, and a patch written against a wrong original silently never fires (best case) or scribbles on live code (worst case).

Make this **step 2 of your own implementation checklist**, permanently, in the findings document.

### Then verify against RUNNING memory before you ship **(A)**

A static read proves the bytes are in the *file*. It does not prove they are in *memory at the moment your patch fires* — a load-time fixup, another patch, or the guest's own initialisation may have been there first. Verify with a poke tool that **logs the bytes it displaced** ([probe-cli.md](probe-cli.md) §2, `--poke`), and make "the displaced bytes equal what the static read predicted" the pass condition:

```
[poke] frame 300  0x0014a324: orig 9b40 -> e1d3 (ok)
```

Reference practice **(B)**: every code site in that port shipped only after such a run logged `orig -> new (ok)` — ten sites in one widget, all ten logged — **and** the same run's frame hashes matched an unpatched run everywhere the patch was not expected to change pixels. One of those runs also produced the first *live* confirmation of a site an earlier session had only ever read statically.

---

## 2. The helper contract

Every patch is a **pure function** of the current guest bytes that returns what to write, or `None`.

```rust
pub const HUD_BOTTOM_PATCH_AT:   u32 = 0x0014_9f7a;
pub const HUD_BOTTOM_PATCH_ORIG: u16 = 0x9b40;   // ldr r3,[sp,#0x100]
pub const HUD_BOTTOM_PATCH_NEW:  u16 = 0xe1d3;   // b #0x14a324

pub fn hud_bottom_patch(read: &impl Fn(u32, usize) -> Vec<u8>) -> Option<(u32, [u8; 2])> {
    let cur = rd_u16(read, HUD_BOTTOM_PATCH_AT)?;
    (cur == HUD_BOTTOM_PATCH_ORIG)
        .then_some((HUD_BOTTOM_PATCH_AT, HUD_BOTTOM_PATCH_NEW.to_le_bytes()))
}
```

Every property here earns its place **(A)**:

| property | why |
|---|---|
| **read-verify-then-write** | a different build is left alone rather than corrupted |
| **returns `None` once applied** | the worker can call it every frame; idempotent by construction |
| **`Option<(addr, bytes)>`, not a write** | pure, so it unit-tests against a `HashMap<u32, u8>`-backed reader with no emulator |
| **size-neutral** — one halfword, occasionally two, never a length change | nothing downstream moves and no address in any note goes stale. Carry the payload as `Vec<u8>`, not `[u8; 2]`: the same feature can need a different patch shape on another build (§3a) |
| **zero writes when the feature is off** | an unpatched player runs a bit-identical guest, so a bug report can be reproduced without your features |

**Compute the encoding in code; do not hardcode the result.**

```rust
// Thumb-1  subs rD, rN, #imm3   ->  0x1E00 | imm3<<6 | rn<<3 | rd
fn subs_imm3(rd: u8, rn: u8, imm3: u8) -> Option<u16> {
    if imm3 > 7 { return None; }                        // 3 bits. The encoding IS the limit.
    Some(0x1E00 | ((imm3 as u16) << 6) | ((rn as u16) << 3) | rd as u16)
}
```

Then pin it with a selftest over the whole legal range, including the values that must be **refused**. Use the dependency-free `include!`-the-real-source harness in `${CLAUDE_PLUGIN_ROOT}/shared/selftest-convention.md` so the test exercises the shipping constants, not a copy of them.

`${CLAUDE_PLUGIN_ROOT}/tools/thumb_enc.py` computes and cross-checks these at the command line:

```bash
python "${CLAUDE_PLUGIN_ROOT}/tools/thumb_enc.py" 'subs r1,r3,#5' --verify-orig 0x1C19
python "${CLAUDE_PLUGIN_ROOT}/tools/thumb_enc.py" 'movs r1,#25'
python "${CLAUDE_PLUGIN_ROOT}/tools/thumb_enc.py" 'b.w 0x149f7a 0x14a324'
```

**Encoding bounds the feature.** Write the bound into a named constant with the reason attached:

* Thumb-1 `imm3` is 3 bits → a level-relief patch maxes at **7**.
* An `imm8` field that already holds a base offset of 44 → your shift maxes at `255 - 44 = 211`.
* A divisor rewrite must divide the original exactly → x3 of a `/100` is **impossible**, not approximate.

**⚠ The read-verify guard is probabilistic, not airtight — and the repo that ships it should say so.** It is the *second* line of defence, never the first. A foreign build can present the same halfword at the same address by coincidence, and a build that merely relocated the function by a few hundred bytes will present *some* matching halfword sooner or later. **The airtight version is a build key**: resolve the address — and the patch *shape* — from a table keyed on the guest image's hash, and let the byte check catch the residue. See [build-keyed-anchors.md](build-keyed-anchors.md).

**⚠ An idempotence guard must distinguish the two states over the WHOLE replacement.** "Returns `None` once applied" holds only if the comparison can tell the states apart. A guard that checks a prefix which the replacement *shares* with the original always answers "already applied", never writes, and leaves the feature silently dead. That shipped in the reference port — see §9.

**Accept your own previous encoding as well as the pristine one.** If the re-assert pass only accepts the pristine bytes, it can run exactly once and then never adjusts the value again. The verify becomes:

```rust
if cur != ORIG && (cur & OPCODE_MASK) != OUR_OPCODE { return None; }   // unknown build, leave it
```

---

## 3. The three patch shapes

Two of them rewrite the guest's own instructions in place; the third replaces one instruction with a trap and runs the work on the host. All three are applied to loaded memory, never to the shipped image.

### 3a. Value rewrite — preferred, always

Rewrite an **immediate operand** in place. Same instruction length, same instruction count, same flags behaviour if you check.

```
0x13d0aa   1C19  adds r1, r3, #0    ->  1F59  subs r1, r3, #5
0x14654e   2164  movs r1, #100      ->  2132  movs r1, #50     (x2)
                                     ->  2119  movs r1, #25     (x4)
```

Before shipping one, write down three things:

1. **Liveness**: which registers the rewritten instruction defined, and where they die.
2. **Flags**: whether you changed a flag-setting instruction, and whether anything reads the flags before they are re-set.
3. **Range**: the encoding's limit and what your code does at the boundary.

And a fourth, which is a **correctness** decision rather than a convenience one: *which operand of the comparison you rewrite.*

> **REFUTED.** *"A value rewrite is a value rewrite. Take whichever operand of the comparison re-encodes most easily; the semantics are the same either way."*
>
> **Refuted by:** porting a "reduce the requirement by N" patch to a second build of the same game. The obvious port rewrote the **requirement load**, which on that build meant turning an `ldrsb` into an `ldrb` — a **sign-extension change**, not a value change. Every record whose requirement byte is `>= 0x80` flips from allowed to blocked, and `-1` meaning "no requirement" is exactly what a C `signed char` field invites. Both halfwords were read back out of *running* guest memory before the corrected patch shipped. `HIGH / CONFIRMED`.
>
> **Why the wrong reading was attractive:** the two operands sit on the same line of the same comparison, and they produce the same on-screen result in every case you are likely to test. The cases that differ are the ones a hand-built test save does not contain.
>
> **Consequence if carried forward:** a whole class of item silently stops being usable, on one build only, and the symptom is a refusal message the *game itself* prints — so it reads as a game rule rather than as your patch.

**The corrected rule (A): rewrite the operand whose provable range makes the rewrite semantics-preserving, and write the proof down next to it.** The reference fix biased the **player's level** instead: the game caps level at 70, so `ldrh` and `ldrsh` agree for every value that field can hold, and the comparison's meaning is unchanged **(B)**.

Two corollaries for the plumbing **(A)**:

* **The same feature can need a different patch *shape* per build** — one build a single-halfword register-move rewrite, the other a two-halfword load rewrite. Carry `Option<(addr, Vec<u8>)>` from the first day rather than `Option<(addr, [u8; 2])>`; widening it later touches every call site and every test.
* **Changing a load's width or signedness changes the field's *type*, not its value.** Before you do it, state the field's full range and whether the game ever uses the top half. If you cannot state it, choose a different operand.

### 3b. Block skip — a branch over a range of instructions

```
0x149f7a   9b40  ldr r3,[sp,#0x100]  ->  e1d3  b #0x14a324
```

Thumb-1 unconditional branch T2: `imm11 = (target - (pc_of_branch + 4)) / 2`, encoded `0xE000 | (imm11 & 0x7FF)`.

```
(0x14a324 - (0x149f7a + 4)) / 2 = 467 = 0x1D3   ->  0xE1D3
```

Put that arithmetic in a comment at the site. It is the one thing nobody can re-derive from the bytes alone without going back to the map.

### 3c. Trap hook — hand a whole guest routine to the host

The third shape, and the largest single performance lever in a port: replace one instruction with a trap the core dispatches to a **host implementation** of that guest function (or of one of its loops), do the work natively, and return past the displaced instruction.

It is **consistent with the framing rule**, which is worth stating because it looks like an exception: the shipped archive is untouched, the patch is applied to loaded memory at boot from a table, and a build whose bytes do not match is left alone.

What makes it a different discipline from 3a and 3b — each of these is a proof obligation, not a preference:

* choosing between a **function-entry** hook and an **outer-loop** hook. An entry hook runs before the prologue, so the handler sees the **caller's** stack pointer and the arguments are *not* at the offsets a disassembly of the body shows;
* a refusal path that reproduces the displaced instruction **exactly**, so the worst case is "exactly as before";
* **refusing rather than clamping** when the guest routine does no clipping of its own — its *caller* may be doing the clipping, and re-clamping double-clips;
* **all reads before any write**, so a refusal or an unreadable guest table cannot leave a half-drawn rectangle on screen;
* **install-time verification** of the displaced bytes with a refusal to install on mismatch — which is what makes a revision that relocated the function fail loudly instead of corrupting an unrelated instruction;
* an **exit-address proof**: the tail you jump to must be one the function's own do-nothing early exits already reach.

The full contract, the census that tells you which loops are worth hooking, and the installer discipline are [guest-pixel-loops.md](../../emulator-core/reference/guest-pixel-loops.md). The A/B protocol that proves the host version is bit-identical is [probe-cli.md](probe-cli.md) §8.5.

---

## 4. Hazards of block-skipping

Three, all of which have bitten a real port.

**1. Audit every store inside the skipped range.**
Disassemble the whole range and enumerate its stores. Stack stores are fine (the frame dies with the function). `sl`-relative or god-object-relative stores are **not** — you are freezing game state. In the reference case the only non-stack stores were HUD blink counters, which was acceptable *and was checked*, not assumed.

**2. Anything the skipped code maintained is now frozen.**
A blink flag that the skipped block toggled stops toggling. If your host UI reads that flag it will stick on or off forever. **The host must use its own clock instead** — e.g. `elapsed_ms / 250 % 2`. Go looking for these deliberately: for every store you found in step 1, ask "who reads this, and do I read it too?"

**3. ⚠ The block may exist twice.**
Compilers outline `else` arms. A selector on a byte field may run either an inline path A or a byte-identical path B reached by **`bl` used as a long branch** — with its own copy of every immediate. Patch one and the widget tears in half: it moves for one case and not the other.

Reference measurement **(B)**: one HUD element needed **12** immediate rewrites across two copies of the same block, and an adjacent element needed 3. The rule that falls out **(A)**:

> **Patch the whole widget or none of it.** After you find one site, search for the *rest* of them — `xref.py literal`, `xref.py offset`, and a scan for the same immediate near the same code — before writing a line.

---

## 5. Dead guest code as scratch space

### The problem

An out-param convention (`serializeSave(u32* outLen)`) needs a writable 4-byte guest address. Your options:

| option | problem |
|---|---|
| allocate in the guest | needs freeing; needs to survive across ticks; you must know the allocator's behaviour |
| a spare slot in the god object | you cannot prove nothing else uses it |
| the guest's stack | gone by the time the queued call returns |
| **four bytes of proven-dead code** | none of the above — see below |
| **the page-rounding tail past the last section** | only exists on a section-mapped image — but where it exists it is strictly better, and the proof is cheaper (see below) |

### The technique **(A)**

Put **data** — never code — inside a range of the game's own code that is provably never executed and never read.

```rust
pub const SCRATCH: u32 = 0x0014_a400;
pub const DEAD_LO: u32 = 0x0014_a32e;
pub const DEAD_HI: u32 = 0x0014_a6aa;

const _: () = assert!(SCRATCH >= DEAD_LO && SCRATCH + 4 <= DEAD_HI && SCRATCH % 4 == 0);
```

**A compile-time assert, not a test.** Drifting the scratch address out of the proven range means scribbling on live guest code. That must not be something a passing build can do.

**Data, not code.** The alternative design was a ~40-byte hand-assembled Thumb thunk in the same cave, which would have run a whole call chain inside one guest execution and dodged the return-value plumbing entirely. It lost because it requires hand-assembled Thumb that no test can meaningfully check. **Data executes nothing, so the entire risk class disappears.**

### ⛔ The reachability sweep — and how the first one was WRONG

Run:

```bash
python "${CLAUDE_PLUGIN_ROOT}/tools/deadcode_sweep.py" -t target.toml --min-len 64 --report-jumptables
```

A correct sweep must:

* **follow local `bl` targets**;
* honour every literal pool (pool words are data, not code, and must not be decoded as branches);
* cross-check against **all** direct branches;
* cross-check against **all** Thumb function-pointer words in the image (any word `w` with `w & 1` whose target is a plausible function entry);
* cross-check against every **jump-table base** it can find — the reference image had **179** of them.

**The first sweep in the reference port did not follow `bl`, and it was wrong.** It reported a cave starting at `0x149f7c`. That range contained a **live** target: `0x149d6c` reaches `0x14a6aa` via `bl`. The real cave starts at `0x14a32e`.

**Why the mistake is easy to make and easy to repeat:** on Thumb-1 `bl` has a ±4 MB range while `b.n` has ±2 KB, so the compiler routinely emits `bl` **as a long branch** for ordinary intra-function control flow. A sweep that assumes `bl` means "call, and calls always return, so the fall-through is what matters" will happily mark an outlined `else` arm as unreachable.

**Writing four bytes into live guest code is a guaranteed crash, at an unrelated PC, minutes later.** Follow `bl`. Then verify the range independently: run the game headlessly through every state you can reach and confirm the sampled profiler never lands inside it.

### A better scratch on a section-mapped image **(A — supersedes dead code where it applies)**

**Dead code stays the right answer for a flat, self-rebasing image.** There, no mapped region belongs to nothing, so the only writable bytes nobody names are inside code nobody runs.

On a **section-mapped ELF** ([address-model.md](address-model.md) §9.1) there is a strictly better one. The loader maps each section **page-rounded**, so the bytes between the declared end of `.bss` and the end of that page are **mapped memory belonging to no section at all**. Nothing in the image can name them, because nothing in the image describes them.

The proof is three-way and mostly static **(B)**:

1. **Static.** No literal anywhere in `.text` or `.data` points at or past the declared `.bss` end. Compute the largest such literal *plus* the largest addressing-mode displacement that can be applied to it (`imm5 * 4` for a word load) and place the scratch beyond that reach — the reference port placed it `0x530` past the computed maximum.
2. **Mapping.** A probe poke writes the address and reads it back.
3. **Live.** A sentinel written there survived boot, ~7,800 frames of play, and the game performing its own save — byte-identical.

That is cheaper than a reachability sweep, and it does not depend on `bl` following, on jump-table recovery, or on a profiler never landing in a range.

### ⛔ A range your own patch branches over is NOT a proven-unreachable range

The two get confused because both produce a span of instructions that "does not run". They are different claims:

| claim | what it rests on | what it licenses |
|---|---|---|
| "our patch skips `A..B`" | *your* branch — which a setting, a build flag or a bug can remove | **nothing.** Every other path into `A..B` still exists, and returns the moment the feature is switched off |
| "`A..B` is unreachable" | a whole-image sweep that followed `bl`, honoured pools, function-pointer words and jump tables | writing data there |

Writing scratch into a range you merely branch over is a crash the first time a player turns the feature off — or the first time the guest reaches it by the path you did not patch.

### Preconditions on your platform

* **The allocator must not move objects.** Holding a guest pointer across ticks is only sound because these cores use a malloc/free allocator with no compacting GC. With a moving collector, only the single-thunk design is safe. **Check before you choose the design.** ⚠ This is a claim about the *address*, not about the *object at it* — the **game** may compact its own containers in place, so a deferred write that names an object by container coordinate still needs an identity re-match. See [calling-guest-code.md](calling-guest-code.md) §10 and [live-data-structures.md](live-data-structures.md) §5.
* **The scratch must be cleared before each use.** A stale value left from a previous call reads as this call's answer. Clear it in the call wrapper, not at the call site.

---

## 6. A measured value is a band, not an invariant

**⛔ Carry this correction; do not repeat the error.**

An early version of the reference save chain asserted the serialised payload length equalled **1392**, the value measured from an archived save. *The warning fired on the very first real save.* A live mid-game character serialised to **1808**. The length is **content-dependent** — inventory size, quest flags, party members all move it.

The fix is a band with a documented rationale:

```rust
const PAYLOAD_MIN: u32 = 64;      // below this it cannot be a real save
const PAYLOAD_MAX: u32 = 8192;    // above this something has gone wrong; refuse
```

**Generalise it (A):**

> **Never pin a measured value as an invariant.** A number you observed once is a *sample*. Convert it into a **range check with a stated reason at each end**, or into a **relation between two measured quantities** (`file == payload + header`) which is invariant even when both sides move.

Values that look constant and are not: serialised lengths, record counts, frame counts, allocation sizes, sprite dimensions, the number of entries in a table, the time a network round-trip takes. Values that genuinely are constant: instruction encodings, struct offsets within one build, and arithmetic relations.

Corollaries:

* **Validate pointers against a window, not against a value.** `0x100000 ..= 0x8000_0000` for "anywhere in the image or the heap"; the allocator's own base and size for "in the heap".
* **Sanity-ceiling any size you derive from guest memory before allocating on it.** A stale handle yields a garbage width; a `TILES_MAX`-style ceiling turns that into a refusal instead of a multi-gigabyte allocation.
* **Refusal is a valid answer.** A helper that returns `None` for an implausible read is doing its job.

---

## 7. Re-assert cadence and idempotence

Run every patch helper on a fixed cadence from the worker — a `frame % 30` pass is plenty. The discipline is one line:

> **read → compare → write only on mismatch → re-assert forever.**

What that buys **(A)**:

* **Idempotence across New Game / Continue / death / map change, with zero ordering logic.** You never need to know when the guest reinitialised something; the next pass fixes it.
* **Self-cleaning expiry.** To turn a feature off, ask the helper for the *original* bytes back. It returns `None` when they are already there.
* **No startup race.** The pass runs whether or not the guest has finished booting; before the god object resolves, every helper returns `None`.

Two adjacent rules from the same discipline:

* **Store host-side state as absolute wall-clock stamps, never tick counters.** A mobile app is killed constantly; a tick counter restarts at zero and a timed effect becomes permanent. Persist absolute UNIX-ms expiries.
* **Gate on the game's own precondition, not one you invented.** If the game refuses to save in towns, read *its* byte rather than reimplementing "am I in a town". Your rule and its rule will diverge, and yours will be wrong. See [calling-guest-code.md](calling-guest-code.md) §6.

---

## 8. Testing patches without a phone

Three layers, cheapest first:

1. **Dependency-free selftests.** The encoder, the bounds, the refusals, the `None`-when-already-patched behaviour, and the address constants are all pure logic. They belong in a module with **no imports**, tested by a standalone `rustc --test` binary that `include!`s the real source. Full convention: `${CLAUDE_PLUGIN_ROOT}/shared/selftest-convention.md`. Mirror files that restate constants **drift** — one reference mirror said `160` where the real value was `211` — so the `include!` form is mandatory and the copy form is banned.
2. **A `HashMap<u32, u8>`-backed reader.** Because every helper is `fn(&impl Fn(u32, usize) -> Vec<u8>) -> Option<…>`, you can feed it a synthetic memory containing the pristine bytes, the already-patched bytes, and a foreign build's bytes, and assert all three outcomes. No emulator involved.
3. **The headless probe.** Boot, apply, and check the behaviour actually changed — a dumped frame, a differential render, a RAM diff. Only then a device.

**Prove the control is reachable in a state that passes the gate.** A reference SAVE button lived in a panel whose entry point only exists **outside** field mode, while its gate only passes **inside** field mode — so it was always disabled, and the patch itself was fine. When a feature has a state gate, test the *path to it*, not only the gate.

---

## 9. Patching what the game says

Replacing a *sentence* is a distinct and much cheaper patch class than the instruction rewriting above: the target is data, the bound is the game's codepage, and the failure modes are its own. **(A)**

### The sentence may not exist anywhere in the image

A displayed line is frequently **assembled** — `sprintf(dst, "%s %d%s", lit_a, value, lit_b)` — so no single-literal edit produces the sentence you want, and editing `lit_a` alone gives you a mangled hybrid.

**Repoint the format pointer instead.** Every call site reaches the format string through a literal-pool word: write your replacement into spare space and aim that one word at it. With no conversion specifiers left in the new string, the surplus arguments are simply ignored. Reference case **(B)**: two byte-identical call sites read the *same* pool word, so **one** write changed both, and the replacement went into a tail literal with 65 spare bytes.

### The bounds

* **In-place replacement is bounded by the original literal's byte length in the game's codepage** — not in characters, and not in UTF-8. Make it a compile-time assertion: a 34-byte replacement inside a 54-byte original **(B)**, checked by the build, not by a reviewer.
* **Derive the file-offset-to-vaddr delta by cross-checking one string whose guest address you already know**, rather than assuming the text member maps the way the code does.

### Prefer a runtime RAM poke to an archive edit

* it can be **conditional** on a host setting (only in offline mode, only for one language);
* it is **reversible** — ask the helper for the original bytes back (§7);
* it re-asserts idempotently on the normal cadence, so a guest that reloads the member does not undo it;
* and it **does not move the guest image's hash**, which an archive edit does — silently unhooking every hash-keyed optimisation hook (see the framing rule and §3c).

### ⛔ The idempotence guard must compare the WHOLE replacement

**Device-caught, and it shipped.** A guard compared a **4-byte prefix** and matched the *original* string, because replacement and original began with the same word. The helper concluded "already applied", never wrote, and the feature was dead in the field with no log line and no crash.

> Natural-language replacements very often share a prefix with the text they replace — that is what makes them read as the same sentence, rewritten. **Compare the full replacement**, or compare a suffix and write a comment saying why that suffix discriminates.

§2's `cur != ORIG && (cur & OPCODE_MASK) != OUR_OPCODE` guard is the instruction-level form of the same rule: the comparison has to be able to tell the two states apart, and "it looks different" is not the same as "it compares different".

---

## 10. Checklist

Before shipping a guest patch:

- [ ] Original bytes re-read from the binary today, with `dis.py`, not from a note — **and** displaced bytes logged from running memory by a poke run (§1).
- [ ] Helper is `fn(read) -> Option<(addr, bytes)>`, verifies the original, returns `None` when already applied.
- [ ] Encoding computed in code, bounded by the encoding's real limit, and refused rather than rounded when unrepresentable.
- [ ] Liveness and flags argument written down at the site.
- [ ] **Which operand** you rewrote is justified by that operand's provable range — no accidental change of a load's width or signedness (§3a).
- [ ] The site is resolved from a **build-keyed** table; the byte check is the second line of defence, not the first (§2).
- [ ] Searched for the **other copies** of the block; patched all or none.
- [ ] For a block skip: every store inside the range enumerated; everything the block maintained is now supplied by the host.
- [ ] For scratch memory: `deadcode_sweep.py` run **with `bl` following**, range guarded by `const _: () = assert!(…)`, scratch cleared before use — or, on a section-mapped image, the page tail with its three-way proof (§5).
- [ ] No range that your own patch merely branches over is being treated as unreachable (§5).
- [ ] For a text patch: length asserted against the original at compile time, and the idempotence guard compares the **whole** replacement (§9).
- [ ] No measured length or size asserted as a constant — bands with reasons at both ends.
- [ ] Selftests pin the encoder over the whole legal range **and** the refusals.
- [ ] Verified headlessly, then on a device.
- [ ] Recorded in the findings document: address, original bytes, new bytes, liveness argument, encoding bound, **and what the patch does not cover**.
