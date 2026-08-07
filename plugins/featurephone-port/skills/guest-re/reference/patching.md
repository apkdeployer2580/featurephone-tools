# Patching the guest — host writes RAM after boot, never the shipped binary

## The rule that frames everything

> **You do not modify the game binary. The host writes guest RAM after boot, verifies before every write, and re-asserts forever.**

Consequences, all of them good:

* The archive stays byte-identical to the dump the user supplied. Nothing you ship contains modified game code.
* A different build of the game is left **untouched** instead of corrupted, because every write is conditional on reading the documented original bytes first.
* Features are switchable at runtime with no rebuild, and a player who never enables one runs a **bit-identical** guest.
* There is no patch-order problem, no "did new-game reset it" problem, and no migration when the player loads a save.

The only shipped-asset modification that is ever acceptable is **text** — a data member you repack, which is `archive-prep`'s job, not this skill's.

## Contents

1. [Before you write any code](#1-before-you-write-any-code)
2. [The helper contract](#2-the-helper-contract)
3. [The two patch shapes](#3-the-two-patch-shapes)
4. [Hazards of block-skipping](#4-hazards-of-block-skipping)
5. [Dead guest code as scratch space](#5-dead-guest-code-as-scratch-space)
6. [A measured value is a band, not an invariant](#6-a-measured-value-is-a-band-not-an-invariant)
7. [Re-assert cadence and idempotence](#7-re-assert-cadence-and-idempotence)
8. [Testing patches without a phone](#8-testing-patches-without-a-phone)
9. [Checklist](#9-checklist)

---

## 1. Before you write any code

**Re-read the original bytes at every patch site, from the binary, immediately before implementing.**

```bash
python "${CLAUDE_PLUGIN_ROOT}/tools/dis.py" -t target.toml 0x13d0a0 0x13d0c0
```

Confirm the halfword you are about to depend on is actually there. An RE note — including one you wrote last week — is a single source. Notes drift, sites get transcribed with a digit swapped, and a patch written against a wrong original silently never fires (best case) or scribbles on live code (worst case).

Make this **step 2 of your own implementation checklist**, permanently, in the findings document.

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
| **size-neutral, single halfword** | nothing downstream moves; no address in any note becomes stale |
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

**Accept your own previous encoding as well as the pristine one.** If the re-assert pass only accepts the pristine bytes, it can run exactly once and then never adjusts the value again. The verify becomes:

```rust
if cur != ORIG && (cur & OPCODE_MASK) != OUR_OPCODE { return None; }   // unknown build, leave it
```

---

## 3. The two patch shapes

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

### 3b. Block skip — a branch over a range of instructions

```
0x149f7a   9b40  ldr r3,[sp,#0x100]  ->  e1d3  b #0x14a324
```

Thumb-1 unconditional branch T2: `imm11 = (target - (pc_of_branch + 4)) / 2`, encoded `0xE000 | (imm11 & 0x7FF)`.

```
(0x14a324 - (0x149f7a + 4)) / 2 = 467 = 0x1D3   ->  0xE1D3
```

Put that arithmetic in a comment at the site. It is the one thing nobody can re-derive from the bytes alone without going back to the map.

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

### Preconditions on your platform

* **The allocator must not move objects.** Holding a guest pointer across ticks is only sound because these cores use a malloc/free allocator with no compacting GC. With a moving collector, only the single-thunk design is safe. **Check before you choose the design.**
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

## 9. Checklist

Before shipping a guest patch:

- [ ] Original bytes re-read from the binary today, with `dis.py`, not from a note.
- [ ] Helper is `fn(read) -> Option<(addr, bytes)>`, verifies the original, returns `None` when already applied.
- [ ] Encoding computed in code, bounded by the encoding's real limit, and refused rather than rounded when unrepresentable.
- [ ] Liveness and flags argument written down at the site.
- [ ] Searched for the **other copies** of the block; patched all or none.
- [ ] For a block skip: every store inside the range enumerated; everything the block maintained is now supplied by the host.
- [ ] For scratch memory: `deadcode_sweep.py` run **with `bl` following**, range guarded by `const _: () = assert!(…)`, scratch cleared before use.
- [ ] No measured length or size asserted as a constant — bands with reasons at both ends.
- [ ] Selftests pin the encoder over the whole legal range **and** the refusals.
- [ ] Verified headlessly, then on a device.
- [ ] Recorded in the findings document: address, original bytes, new bytes, liveness argument, encoding bound, **and what the patch does not cover**.
