---
name: emulator-core
description: Brings a real commercial feature-phone title up on a forked dlunch/wie emulator core and fixes the six failures it hits, in the order it hits them - a black screen caused by a wall-clock host now(), fatal unimplemented WIPI-C stubs, a JVM thread-attach panic on the first host-initiated guest call, full-framebuffer round-trips on every draw call, a guest list allocator that never coalesces, and a native stack overflow caused by reporting an allocation failure with an allocation. Also covers vendoring the fork with cargo [patch], adding WIPI-C functions and KTF Java classes, and the diagnostic gauges that tell the layers apart. Use when an emulator boots to black with no error, logs "Unimplemented", panics in threads.get().unwrap(), stutters on busy maps or full-screen effects, dies with AllocationFailure after twenty to forty minutes, SIGSEGVs with "stack pointer is not in a rw map", hits "Undefined instruction", or when the emulator API surface must be extended.
license: MIT
---

# Emulator core

Scope: everything inside `vendor/wie` and `vendor/arm32_cpu`. Stops at the `Emulator` trait
boundary — the host app is `android-host`, the guest binary is `guest-re`, the archive is
`archive-prep`.

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

Two more that are not in the boot sequence but cost a frame budget each:

- **Log flood.** A `#[tracing::instrument]` at INFO on the WIPI timer callback fires tens of times
  per second inside the frame budget. Set `level = "debug"`. (A) —
  [hardening-batch.md](reference/hardening-batch.md) §5.
- **Bucket allocator header scan.** Not a crash, a frame-budget symptom: the per-slot bitmap was
  read whole on every alloc and free. [hardening-batch.md](reference/hardening-batch.md) §2.

## 2. Extension recipes

- **Add a WIPI-C function** (three sites must agree; tables are positional and **must be padded**,
  or the guest branches to address 0) → [wipi-c-recipes.md](reference/wipi-c-recipes.md)
- **Add a KTF/WIPI Java class, method or field** (two resolution paths; a static field of a
  registered class is the only reliable GC root for a Rust-held guest object) →
  [java-class-recipes.md](reference/java-class-recipes.md)

## 3. Discovery loop for a missing WIPI-C method

1. Read the one `Unimplemented: <table>: <name>` line. That is the whole bug report.
2. Replace the fatal stub with `trace_stub` — a logging no-op returning a chosen plausible value —
   so the guest keeps walking and the **entire** call sequence lands in the log with hex+ASCII
   peeks of pointer arguments.
3. Read the sequence, then confirm each call against the guest disassembly (`guest-re`).
4. Implement for real. Keep the trace stub for the slots you still do not understand.

Do not trust upstream's slot *names* for slots nobody has exercised — several are guesses, not a
contract. Details and the `MethodBody`-not-closure constraint: [wipi-c-recipes.md](reference/wipi-c-recipes.md).

## 4. Diagnostics

Turn the gauges on before forming a hypothesis. The reference port's first stack-overflow hunt
measured the wrong layer for a whole round.

- Heap counters (`allocs / frees / LIVE / list_walk_steps / bucket_header_bytes`), work counters
  (guest instructions per frame, SVC count), native-stack watermark, executor task count.
- Rule: **log only on a new maximum** — a healthy session prints a handful of lines, a leaking one
  prints a rising staircase.
- Interpreter-level tools: `insn_trace` feature, `advanced_disasm` (capstone), `enable_gdbserver`,
  and the hand-written assembly fixtures under `arm32_cpu/tests/data/`.

→ [diagnostics.md](reference/diagnostics.md)

## 5. Verification

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

## 6. Budget

Expect **zero opcode fixes**. The full interpreter delta on the reference port was performance and
observability only; a KTF/RVCT-compiled commercial ARM binary exercises a well-trodden ARMv4T /
Thumb-1 subset. Spend the budget on the WIPI surface instead. If you do hit
`FatalError("Undefined instruction")`, [diagnostics.md](reference/diagnostics.md) §6 has the
bisect procedure.
