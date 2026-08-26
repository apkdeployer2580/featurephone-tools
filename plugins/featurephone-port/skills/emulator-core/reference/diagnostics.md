# Diagnostics: gauges, counters, and measuring the right layer

The stack-overflow hunt on the reference port is the best worked example in this whole methodology
of **measuring the wrong layer for a whole round**. Build the gauges before forming the hypothesis.

## Contents

1. [The design rule: log only on a new maximum](#1-the-design-rule-log-only-on-a-new-maximum)
2. [Engine counters: `wie_util::stats`](#2-engine-counters-wie_utilstats)
3. [The three always-on gauges](#3-the-three-always-on-gauges)
4. [Worked example: what each gauge ruled out](#4-worked-example-what-each-gauge-ruled-out)
5. [Interpreter observability and what it costs](#5-interpreter-observability-and-what-it-costs)
6. [Opcode bugs: how they present and how to bisect](#6-opcode-bugs-how-they-present-and-how-to-bisect)
7. [Two fault classes no in-process gauge can see](#7-two-fault-classes-no-in-process-gauge-can-see)
8. [The per-tick hitch report](#8-the-per-tick-hitch-report)
9. [The Thumb decode LUT](#9-the-thumb-decode-lut)
10. [Checklist before you claim a cause](#10-checklist-before-you-claim-a-cause)

---

## 1. The design rule: log only on a new maximum

Every gauge in this file follows the same shape:

```rust
static MAX: AtomicU32 = AtomicU32::new(0);
let cur = measure();
if cur > MAX.fetch_max(cur, Ordering::Relaxed) {
    tracing::info!("[gauge] new max {cur}");
}
```

**A healthy session prints a handful of lines at startup and then goes quiet. A leaking one prints
a rising staircase.** That property is what makes a gauge readable in a multi-hour logcat, and it
is why per-frame logging of a counter is worse than useless — see the log-flood failure in
[hardening-batch.md](hardening-batch.md) §5.

Periodic gauges (every N frames) are the exception, for values that legitimately oscillate. Keep N
large enough that the log write is not on the frame budget.

**Two rules for that exception (A):**

1. **Report an interval rate and reset the counters on read.** A lifetime average mixes the title
   screen — where the loop sleeps through most of every frame — with real play, so a low number is
   ambiguous and a high one is unattributable. Interval rates are comparable across runs and across
   devices; lifetime totals are not.
2. **A 30-second gauge is structurally blind to a 300 ms hitch** — roughly 100:1 dilution — and a
   few-hundred-millisecond stall is exactly what players report. Pair every interval gauge with a
   **per-tick hitch report**: §8.

## 2. Engine counters: `wie_util::stats`

Put the counters in the lowest crate in the graph so every layer can charge to them, and keep it
`no_std`-compatible with relaxed atomics so the cost is a single non-synchronising add.

```rust
// wie_util/src/lib.rs
pub mod stats {
    pub static GUEST_INSTRUCTIONS: AtomicU64 = AtomicU64::new(0);
    pub static SVC_CALLS:          AtomicU64 = AtomicU64::new(0);
    pub static RUN_BATCHES:        AtomicU64 = AtomicU64::new(0);

    pub static ALLOCS:              AtomicU64 = AtomicU64::new(0);
    pub static FREES:               AtomicU64 = AtomicU64::new(0);
    pub static LIST_WALK:           AtomicU64 = AtomicU64::new(0);
    pub static BUCKET_HEADER_BYTES: AtomicU64 = AtomicU64::new(0);

    pub fn heap_snapshot() -> HeapSnapshot { /* one relaxed load each */ }
}
```

**★ Charge instruction counts once per `run` batch, not per instruction.** The interpreter runs a
batch of guest instructions per call; incrementing an atomic inside the instruction loop measures
your instrumentation. Add the retired count once when the batch returns.

**Interpretation (A):**

| Counter | What a bad value looks like | What it means |
|---|---|---|
| `ALLOCS - FREES` (`LIVE`) | climbing monotonically during steady-state play | guest-side leak, or your allocator is leaking on a failure path |
| `LIST_WALK` steps | climbing **superlinearly** with time | free-list degradation — the coalescing bug, [hardening-batch.md](hardening-batch.md) §1 |
| `BUCKET_HEADER_BYTES` | climbing into GB | the bitmap is being scanned, not indexed — §2 there |
| `GUEST_INSTRUCTIONS` per frame | millions | the game rasterises in guest code; your graphics layer is **not** the bottleneck |
| `SVC_CALLS` per frame | thousands | *something* is being hammered — a total **cannot say what**. Add the per-category and per-id counters below before naming anything |

**Run these before anyone touches performance.** On the reference port they are what turned "map
loads feel slow" into "3.98 GB of guest reads across 53 496 allocations, 74 KB per allocation" —
a number that names its own fix.

### An aggregate trap counter establishes magnitude and never kind

> **REFUTED.** *Previously documented (the table row above, in its first form):* "`SVC_CALLS` per
> frame in the thousands means the guest is hammering one platform call — find which one with a
> draw-call trace." **Refuted by** measuring per id. A published finding in the reference port
> attributed **26,000 calls/second** to a socket-receive poll spinning on a missing acknowledgement,
> and that attribution shaped two sessions of planned work. Per-category and per-id counters on the
> device then showed receive polls at **0/s**, and the 26,000/s was a **fill-rectangle** call: *the
> number was right and the attribution was wrong.* **Why the wrong reading was attractive:** the rate
> really was anomalous, a plausible culprit was already under suspicion for unrelated reasons, and a
> total is compatible with every story you might tell about it — it cannot contradict you.
> **Consequence if carried forward:** you plan a fix for a subsystem that is idle. Two aggravating
> details: on some carrier frontends the C standard library dispatches through the **same trap**, so a
> total cannot even separate a memory copy from a socket call; and the recommended follow-up is
> **unavailable where it matters**, because the draw-call trace lives at `debug`, which the quiet
> release build compiles out (§5 and [hardening-batch.md](hardening-batch.md) §5) — so it cannot be
> run on the device the rate was observed on.

**Fix: two relaxed increments at the *single* trap entry point (A).** A per-category array and a
per-id array with the id masked into range. One site covers every dispatch path, so there is no
per-call-site bookkeeping to drift, and the cost is two relaxed adds.

```rust
// at the ONE supervisor-call entry point -- masked, so a bogus id cannot index out of range
stats::SVC_BY_CAT[(cat as usize) & (NCAT - 1)].fetch_add(1, Ordering::Relaxed);
stats::SVC_BY_ID [(id  as usize) & (NIDS - 1)].fetch_add(1, Ordering::Relaxed);
```

Report as **rates per interval** (§1, rule 1) with the hottest ids named:

```
emu svc/s by category: graphics 24102  kernel 812  net 0  media 3   hot ids/s: 0xd3 23940  0x1f 160
```

That line is what turns "thousands of calls" into "one fill-rectangle call, twenty-four thousand times
a second". It is also what made the frame-pacing defect readable at all —
[frame-pacing.md](frame-pacing.md) §14.

## 3. The three always-on gauges

Print each periodically from the headless harness. Keep them on in release; they cost a handful of
relaxed loads.

```
[heap]  f=1200 allocs=53496 frees=51902 LIVE=1594 list_walk_steps=8811234 bucket_header_bytes=3.98 GB
[work]  f=1200 instr=2.94G 2.45 M/frame svc=180422 41.2 M instr/s virt 0.83x
[stack] f=1500 reserved=8.4 MB used=1.9 MB (new max)
```

**`[stack]` deserves special mention — steal it verbatim (A).** In-process counters cannot find a
native stack overflow, because you do not know which function is recursing. Measure the stack
**from outside the code that uses it**:

- **Windows host:** `VirtualQuery` the thread's own stack allocation base and report how much of
  the region is still `MEM_RESERVE`. Windows commits stack pages lazily and never decommits, so
  *initial − current reserved* is an exact high-water mark for the whole run.
- **Android/Linux device:** parse `/proc/self/smaps` and read `Rss:` of the mapping that contains
  the current `sp`.

Both are OS-specific; put them behind one `reserved_bytes() -> Option<u64>` function and ship both
implementations. This is the only gauge in the list that **assumes nothing about which function
repeats**, which is exactly why it is the one that survives contact with a real bug.

## 4. Worked example: what each gauge ruled out

The hunt for a native stack overflow, in order. Read the *shape* of this table, not its numbers.

| Hypothesis | Gauge | Result |
|---|---|---|
| Thread-wrapper poll nesting | `POLL_DEPTH` at the wrapper's `poll` | **Never fires.** There is one wrapper per executor task, so the depth is 1 **by construction**. The earlier round of investigation measured this layer and concluded "no nesting" — which was true and irrelevant. |
| Guest `run_function` nesting | `RUN_DEPTH` + a per-level entry-address trace | Climbs to 21 during warm-up and then stops; the repeats at 19/20/21 are three *different* call trees. Ruled out — but only because the trace recorded the **entry address per level**, so "the same depth" could be distinguished from "the same cycle". |
| Executor task leak | `tasks.len()` high-water | Stays under 8 across 30 000 frames. Ruled out. |
| Native stack actually consumed | stamp + watermark (§3) | Flat headless. Ruled out headless; the device gauge remained the live lead. |

**Three transferable lessons (A):**

1. **A gauge that cannot fire is not evidence.** Before trusting a null result, ask what value the
   gauge *would* show if the hypothesis were true. "Depth is 1 by construction" means the gauge was
   never capable of firing.
2. **Record the identity, not just the magnitude.** `RUN_DEPTH = 21` is ambiguous; `RUN_DEPTH = 21`
   plus the entry address at each level distinguishes a recursion from three unrelated deep calls.
3. **A headless-flat gauge does not clear the device.** Different allocator pressure, different
   thread stack size, different timing. Ship the gauge to the device.

## 5. Interpreter observability and what it costs

Per-instruction tracing must be **compiled out**, not filtered at runtime.

Upstream gated the interpreter's per-instruction `trace!` on
`#[cfg(not(feature = "advanced_disasm"))]` — meaning that in a normal build it was **compiled in
and filtered at runtime**: an atomic max-level load plus a branch **per guest instruction**, with
`pc` and the decoded instruction type pinned live for a format path that never runs. At millions of
guest instructions per rendered frame that lands directly on the frame budget.

**Fix:** a dedicated `insn_trace` cargo feature, forwarded down the crate chain (see
[vendoring.md](vendoring.md) §5), with the trace block behind `#[cfg(feature = "insn_trace")]`.

Two details from the same change:

- One tracer sat on the **predicated-branch** path ("cond not met") and was not covered by the
  disassembly feature gate at all. Grep for every `trace!`/`debug!` in the step loop; there is
  usually one you did not expect.
- The trace block **re-ran the linear decode** — roughly nineteen branches — purely to format its
  line. Reuse the already-decoded instruction type.

Your tool inventory, cheapest first:

| Tool | Cost | Use when |
|---|---|---|
| WIPI draw-call trace (`debug` on the WIPI-C crate) | low | you need the API sequence, not the instructions |
| Sampled guest-PC profiler (PC + frame chain, once per batch) | low | "where is the time going" |
| `--features insn_trace` | very high | a specific short window, with a known frame or trigger |
| `advanced_disasm` (capstone-backed) | high | you need readable mnemonics in the trace |
| `enable_gdbserver` + the debug engine | interactive | you can reproduce on demand and want to step |

**Print every address in the basis your disassembler consumes.** A profiler that prints guest
addresses when your static tools take file offsets makes you do arithmetic hundreds of times.
Print `fo = pc - IMAGE_BASE` alongside, always. ⚠ On a section-mapped build there is no single image
base — take the basis from the section headers instead
([boot-order.md](boot-order.md) §8b).

**Two rules that make the sampled profiler usable (A).** Without them its output is a list of
addresses.

1. **Interpretation: a guest pixel loop appears as a FLAT cluster**, not as a hot line. Thirty to
   seventy adjacent PCs with near-equal shares — the flatness is the signature of an un-unrolled loop,
   and the **cluster sum** is its cost. **(B)** ~35 distinct PCs at ~0.62 % each, spread evenly across
   a 0x86-byte span, summed to **21 %** of all samples and decoded as a per-pixel alpha blend; a
   two-inner-loop cluster on another compile summed to **26 %**. **Exclude the idle PC first** or it
   dominates everything — it was **32–40 %** of all samples in the reference port. And a profiler
   pointing repeatedly at a `bx rN` veneer is pointing at an indirect-call trampoline, not at a
   function.
2. **Scoping: swap the histogram out per tick and merge it back after formatting**, so one tick's
   samples can stand alone in a hitch report (§8) while the interval aggregate stays intact. Two
   lock-and-move pairs per tick, nothing per sample. **Every take must be restored** — an unrestored
   take does not error, it just makes the aggregate read low, forever.

What the census then does with those clusters is
[guest-pixel-loops.md](guest-pixel-loops.md) §2.

## 6. Opcode bugs: how they present and how to bisect

**Expect not to need this.** The full interpreter delta on the reference port was zero opcode
fixes — a KTF/RVCT-compiled commercial ARM binary exercises a well-trodden ARMv4T / Thumb-1 subset.
Budget for it, plan not to spend it.

**The good case.** `Cpu::step` returns false → `WieError::FatalError("Undefined instruction")`.
Clean, named, and it tells you the `pc`. Disassemble there, add the encoding.

**The dangerous case: a silently *wrong* opcode.** It manifests as garbage state, and then as
`InvalidMemoryAccess(addr)` at a `pc` with no relationship to the bug, or as a `run_function` that
never reaches its return sentinel. Bisect:

1. Reproduce under the **headless harness** with the virtual clock, so the run is deterministic and
   the failure lands on the same frame every time.
2. Dump `pc` at the fault, and the register/stack dump the core attaches to fatal errors.
3. Disassemble around that `pc` with your static disassembler (guest address = file offset +
   `IMAGE_BASE`).
4. Re-run with `--features insn_trace` bounded to a window around the fault; compare the executed
   sequence against the disassembly.
5. When you have a suspect encoding, **add it to the assembly fixtures** under
   `arm32_cpu/tests/data/emutest_{arm,thm}*.S` and their assembled `.bin` counterparts. A fixture
   is a permanent regression test; a one-off trace is not.

Also make the fatal-error path itself informative: attach a register + call-stack + stack dump to
every fatal error the core raises, with the image base subtracted so the addresses are pasteable
into the disassembler. That dump is also, incidentally, the fastest way to read the guest's static
base register when pinning the address model (`guest-re`).

## 7. Two fault classes no in-process gauge can see

§4 ends at *"a headless-flat gauge does not clear the device"*. One step further: **two whole fault
classes in this family are invisible to every in-process gauge, on every host**, because the process
does not fail — it is **killed**, or it **stops returning**. There is no counter to read afterwards,
because there is no afterwards.

**(A) Read the device's own records, from outside the process.** Three of them, answering different
questions:

| Record | Answers |
|---|---|
| The native crash-dump (tombstone) directory | *what* faulted — signal, fault address, backtrace |
| The system's crash-log store | the same crashes **with timestamps, surviving reboots** — which dates the last occurrence without waiting for a reproduction |
| The process-exit history | **every** death, including the ones with no crash at all — with a reason code and the process's resident size at death |

The third is the one porters do not know exists, and it is simultaneously the evidence and the
explanation: an emulator is the fattest process on the phone, so it is the low-memory killer's first
pick, and the exit record carries the number that says so.

**(B)** Read together, those three showed the reference port that one crash axis was **provably
closed** — the tombstone timeline stopped at the date the fix was installed — while a second, entirely
different axis was **provably open**, at a moment when the team believed its crash fix had failed.
`HIGH / CONFIRMED`.

How to read each record, symbolicate a stripped release binary and attribute a death to a specific
installed build: [crash-triage.md](../../port-shipping/reference/crash-triage.md). The footprint
defect the exit records point at: [memory-footprint.md](memory-footprint.md).

## 8. The per-tick hitch report

**(A)** An interval gauge answers *"is the machine healthy on average"*. A player reports a **stall**.
So print, for **any single tick that goes over budget**, the **delta of every counter across that
tick** — guest instructions, traps by category, allocations, blits, pixels, the tick's own profiler
take (§5, rule 2) — and rate-limit it to roughly one line per 400 ms, because the log write comes out
of the measured thread's own frame budget.

Its value is that it makes a *comparison* possible in one line. **(B)** After two rounds in which the
aggregate gauge had failed to find a fourth guest pixel loop, the decisive reading was: **"same blits,
same pixels, same traps, 3 allocations, 25× the guest instructions."** Everything except the
interpreter was identical — which is what an unhooked guest loop looks like, and nothing else does.
`HIGH / CONFIRMED`.

Three design rules:

- **Deltas, never totals.** The question is what *this tick* did.
- **Rate-limit, and say in the line that you are doing it.** An unlimited hitch report during a bad
  stretch is itself a stall.
- **Report the budget you compared against**, or the reader cannot tell an over-budget tick from a
  changed budget.

## 9. The Thumb decode LUT

A pure-perf change worth making once, and a template for "precompute a pure function":

Every Thumb decode pattern in the interpreter inspects only `inst & 0xff00`. Decode is therefore a
pure function of `inst >> 8`, with 256 possible outcomes. Precompute all of them in a `const`
block from the existing linear decoder, and **keep the linear decoder as the source of truth**:

```rust
const DECODE_LUT: [ThumbInstType; 256] = { /* const-evaluated from decode_linear */ };

#[test]
fn lut_matches_linear_decode_exhaustive() {
    for inst in 0u32..=0xffff {
        assert_eq!(DECODE_LUT[(inst >> 8) as usize], decode_linear(inst as u16), "{inst:#06x}");
    }
}
```

All 65 536 inputs, exhaustively. That test is what makes the LUT safe to touch later, and it runs
in milliseconds.

## 10. Checklist before you claim a cause

- [ ] The gauge you are relying on **can** fire if the hypothesis is true.
- [ ] The measurement was taken on the layer where the failure occurs, not one layer up.
- [ ] The run is deterministic (virtual clock, scripted input, fixed frame count).
- [ ] You recorded the *identity* of the repeating thing, not only a count.
- [ ] The headless result has been reproduced on device, or you have stated that it has not.
- [ ] Every attribution rests on a **per-kind** measurement. *An attribution derived from a total is
      not an attribution* (§2).
- [ ] Periodic gauges report **interval rates that reset on read**, and a per-tick hitch report exists
      for the stalls an interval hides (§1, §8).
- [ ] If the process **died** rather than failing, you read the device's own exit records (§7) instead
      of looking for a counter that cannot exist.
- [ ] Any mitigation you shipped is labelled **mitigation**, with the observed interval it buys —
      see [hardening-batch.md](hardening-batch.md) §6.
