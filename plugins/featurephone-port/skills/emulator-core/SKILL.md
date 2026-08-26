---
name: emulator-core
description: Brings a commercial feature-phone title up on a forked dlunch/wie emulator core and fixes the defects it hits, in the order it hits them - a black screen from a wall-clock host now(), fatal unimplemented WIPI-C stubs, a JVM thread-attach panic on the first host-to-guest call, full-framebuffer round-trips per draw call, a list allocator that never coalesces, an allocation failure reported by allocating, host sleep grids that run the game at a fraction of speed, an eager guest-heap commit that gets the app killed, and two sound buses with two volume laws. Also covers vendoring the fork with cargo [patch], adding WIPI-C functions and Java classes, re-hosting the guest pixel loops, and the gauges that tell the layers apart. Use when an emulator boots to black, logs Unimplemented, panics in threads.get().unwrap(), stutters, dies with AllocationFailure, SIGSEGVs on a stack not in a rw map, is slow on every screen at once, restarts after switching away, or has silent or unbalanced audio.
license: MIT
---

# Emulator core

Scope: everything inside `vendor/wie` and `vendor/arm32_cpu`. Stops at the `Emulator` trait
boundary — the host app is `android-host`, the guest binary is `guest-re`, the archive is
`archive-prep`, and not destroying the player's character is `save-integrity`.

Provenance tags, used here and in every reference file:
**(A)** generic — apply verbatim · **(B)** worked example from the reference port — copy the
method, not the number · **(C)** do not copy.

## 0. Before touching anything

Vendor the fork as a plain directory redirected by cargo `[patch]` at the workspace root, repeat
the transitive patch inside the vendored workspace, and write `vendor/UPSTREAM.txt`
(repo + base SHA + date) before the first edit. Without it the fork cannot be diffed or rebased,
and `git diff HEAD -- vendor/` will understate your delta.
→ [reference/vendoring.md](reference/vendoring.md)

## 1. The six failures, in the order they appear

Each one hides the next. Fix in order; do not skip ahead because a later symptom looks worse.

| # | First symptom the porter sees | Cause | Where |
|---|---|---|---|
| 1 | Boots, no error, **every frame entirely black**, host CPU busy | Host `Platform::now()` is wall-clock; the guest sleeps on WIPI timers that never expire | [virtual-clock.md](reference/virtual-clock.md) |
| 2 | Frozen/black screen + **exactly one** logcat line `Unimplemented: <table>: <name>`, emulator stopped | Fatal WIPI-C stub | [wipi-c-recipes.md](reference/wipi-c-recipes.md) |
| 3 | `Option::unwrap()` on a `None` in the JVM, on the **first** host-initiated guest call | Spawned executor task is not an attached JVM thread | [hardening-batch.md](reference/hardening-batch.md) §4 |
| 4 | Stutter proportional to draw-call count; full-screen effects hitch | Every draw call round-trips the whole framebuffer through guest memory | [graphics-perf.md](reference/graphics-perf.md) |
| 5 | Runs fine, gets slower, then `AllocationFailure` after 20–40 min with free memory left | Guest list allocator never coalesces | [hardening-batch.md](reference/hardening-batch.md) §1 |
| 6 | `SIGSEGV SEGV_ACCERR`, "stack pointer is not in a rw map", two addresses alternating for all 511 frames | Reporting an allocation failure allocates: `Jvm::exception` ↔ guest allocator recursion | [hardening-batch.md](reference/hardening-batch.md) §3 |

**Only #2 is game-specific, and only its values are.** #1, #3, #4, #5, #6 are defects in the
emulator's own code paths — port those fixes verbatim to any title. The ordered narrative with
per-stage exit criteria and the full symptom→cause→file triage table is in
[boot-order.md](reference/boot-order.md).

**Row 4 carries a discriminator; apply it before doing any of that work.** Count platform draw calls
per frame, **per screen class**. Hundreds to thousands of calls → the round-trip is your cost, and it
is [graphics-perf.md](reference/graphics-perf.md). A handful of calls with **millions** of guest
instructions per frame → the game rasterises in its own code and the cost is
[guest-pixel-loops.md](reference/guest-pixel-loops.md). **Both answers occur in one title**, on
different screens, so measure per screen rather than generalising from one frame.

⚠ Two carriers, two boot chains: the KTF path parses the BSS size out of the native member's
filename and the guest self-rebases; the other AOT carrier loads **by ELF section** and never rebases.
[boot-order.md](reference/boot-order.md) §8b.

## 2. After it boots: the failures that are not in the boot sequence

None of these stops the emulator, which is why they survive for months and get reported as something
else. Each has its own reference.

| What you see | Cause | Where |
|---|---|---|
| **Every** screen is slow, but each report names one screen; the host thread is mostly idle | Two host layers each round the guest's timer period up to their own ~16 ms grid, and the two **multiply** | [frame-pacing.md](reference/frame-pacing.md) |
| Millions of guest instructions per frame with only a handful of platform draw calls | The game took the framebuffer pointer once and rasterises in its **own** ARM code | [guest-pixel-loops.md](reference/guest-pixel-loops.md) |
| "It restarts when I switch away and come back" — no crash dialog, no tombstone | The mapper commits the whole guest heap before the first frame, making the process the low-memory killer's first pick | [memory-footprint.md](reference/memory-footprint.md) |
| Music restarts from the beginning on every effect; music and effects wildly imbalanced at one setting; a hitch on every sound | The guest owns **one** sound slot, and the two buses need **one** volume law applied as an output gain | [audio-core.md](reference/audio-core.md) |
| Frame budget blown, log flooded with one repeating record | `#[tracing::instrument]` at INFO on a per-frame path | [hardening-batch.md](reference/hardening-batch.md) §5 |
| Steady-state stutter; the bucket-header byte counter climbing into GB | The per-slot bitmap is read whole on every alloc and free | [hardening-batch.md](reference/hardening-batch.md) §2 |
| A save slot that **used to work** now reads as empty | Not a read-path bug — a deletion. A guest cannot report "failed", only "empty" | [wipi-c-recipes.md](reference/wipi-c-recipes.md) §7a, [save-integrity](../save-integrity/SKILL.md) |

**The hardening batch is ten items, not five.** Five are worked in
[hardening-batch.md](reference/hardening-batch.md); the other five are owned by the files above and
listed in its §7. **Two of the ten appear only on a device**, so a porter who validates headless ships
both.

## 3. Extension recipes

- **Add a WIPI-C function** (three sites must agree; tables are positional and **must be padded**,
  or the guest branches to address 0) → [wipi-c-recipes.md](reference/wipi-c-recipes.md)
- **Add a KTF/WIPI Java class, method or field** (two resolution paths; a static field of a
  registered class is the only reliable GC root for a Rust-held guest object) →
  [java-class-recipes.md](reference/java-class-recipes.md)
- **A whole functional block is fatal on a second carrier's runtime** — that crate names a handful of
  slots and its `_ =>` arm returns `FatalError`, so there is no `Unimplemented` line and nowhere to
  hang a trace stub. Range-dispatch the block **before** the id→enum conversion; the bodies are
  carrier-agnostic and only the slot numbering is missing →
  [wipi-c-recipes.md](reference/wipi-c-recipes.md) §3a

## 4. Discovery loop for a missing WIPI-C method

1. Read the one `Unimplemented: <table>: <name>` line. That is the whole bug report.
2. Replace the fatal stub with `trace_stub` — a logging no-op returning a chosen plausible value —
   so the guest keeps walking and the **entire** call sequence lands in the log with hex+ASCII
   peeks of pointer arguments.
3. Read the sequence, then confirm each call against the guest disassembly (`guest-re`).
4. Implement for real. Keep the trace stub for the slots you still do not understand.

Do not trust upstream's slot *names* for slots nobody has exercised — several are guesses, not a
contract. Details and the `MethodBody`-not-closure constraint: [wipi-c-recipes.md](reference/wipi-c-recipes.md).

⛔ **Step 2 inverts for release-side calls** — cancel, free, close, stop, unset. There is no plausible
return value to choose, because what matters is the **omitted side effect**, and a no-op release
paired with an implemented acquire accumulates into a symptom with no textual link to the stub. A
shipped example: a no-op timer *cancel* beside a timer *set* that pushed instead of replacing, so the
whole game ran faster with every screen the player visited. For every `set`/`alloc`/`open`/`start` you
implement, implement its release in the same sitting, or record it as a known leak with a named
symptom. [wipi-c-recipes.md](reference/wipi-c-recipes.md) §2.

## 5. Diagnostics

Turn the gauges on before forming a hypothesis. The reference port's first stack-overflow hunt
measured the wrong layer for a whole round.

- Heap counters (`allocs / frees / LIVE / list_walk_steps / bucket_header_bytes`), work counters
  (guest instructions per frame, SVC count), native-stack watermark, executor task count.
- Rule: **log only on a new maximum** — a healthy session prints a handful of lines, a leaking one
  prints a rising staircase. Periodic gauges report an **interval rate that resets on read**; a
  lifetime average mixes the title screen with real play and cannot be interpreted.
- **Per-category and per-id trap counters, never one aggregate.** A total establishes magnitude and
  never kind — a published "26,000 calls/s on a socket poll" turned out to be a fill-rectangle call,
  and it had already shaped two sessions of work.
- **A per-tick hitch report.** A 30-second gauge is structurally blind to the 300 ms stall the player
  is actually reporting; print every counter's delta across any over-budget tick.
- When the process **died** rather than failed, no in-process counter can help. Read the device's own
  records — tombstones, the crash-log store, and the **process-exit history** with its reason code and
  resident size at death.
- Interpreter-level tools: `insn_trace` feature, `advanced_disasm` (capstone), `enable_gdbserver`,
  and the hand-written assembly fixtures under `arm32_cpu/tests/data/`.

→ [diagnostics.md](reference/diagnostics.md)

## 6. Verification

Run these before declaring any core change done.

```bash
cd vendor/wie && cargo test -p wie_backend -p wie_wipi_c   # graphics/canvas equivalence tests
cd vendor/wie && cargo test -p wie_core_arm                # allocator regression tests
cd vendor/wie && cargo test --test test_helloworld         # end-to-end boot + render
cd vendor/arm32_cpu && cargo test                          # decode LUT vs linear decode, exhaustive
```

Then a headless run of the real title: 30k frames with the heap gauge flat in steady state, and a
dumped frame reporting **non-black > 0**. `non-black` is the automated black-screen detector —
assert it in CI instead of eyeballing PNGs.

## 7. Budget

Expect **zero opcode fixes**. The full interpreter delta on the reference port was performance and
observability only; a KTF/RVCT-compiled commercial ARM binary exercises a well-trodden ARMv4T /
Thumb-1 subset. Spend the budget on the WIPI surface instead. If you do hit
`FatalError("Undefined instruction")`, [diagnostics.md](reference/diagnostics.md) §6 has the
bisect procedure.
