# The virtual clock, the executor model, and device pacing

The first thing a new headless runner needs is a **frame-driven virtual clock**. Without it a
timer-driven guest boots cleanly, reports no error, and renders black frames forever.

**Scope.** This file owns the **headless** clock — the executor and timer model, the virtual clock
itself, determinism, and the harness traps around them. On a device the clock is real and the problem
inverts into **pacing**, where the expensive failure is a host layer rounding the guest's timer period
up to a grid of its own. §5 gives the device loop's shape and hands the pathology, its instrumentation
and its three failure modes to [frame-pacing.md](frame-pacing.md).

## Contents

1. [The executor and timer model](#1-the-executor-and-timer-model)
2. [Why a wall clock produces a permanently black screen](#2-why-a-wall-clock-produces-a-permanently-black-screen)
3. [The fix: a frame-driven clock in `Platform::now()`](#3-the-fix-a-frame-driven-clock-in-platformnow)
4. [Determinism, and the two ratios worth printing](#4-determinism-and-the-two-ratios-worth-printing)
5. [Device pacing is a separate problem](#5-device-pacing-is-a-separate-problem)
6. [The executor task gauge](#6-the-executor-task-gauge)
7. [Harness traps that cost hours](#7-harness-traps-that-cost-hours)

---

## 1. The executor and timer model

Upstream logic, unmodified — you need to understand it, not change it.

```
System::tick
  └─ Executor::tick(|| platform.now())
        ├─ budget loop: keep polling ready tasks until now() exceeds start + 8 ms
        └─ EARLY BREAK: if every task is sleeping and the earliest wakeup is in the
           future, return immediately
```

- Tasks sleep via `Executor::sleep`, which stores an **absolute `Instant`** computed from
  `platform.now()`.
- A WIPI `MC_knlSetTimer` call becomes an `Event::timer(due, …)` on the event queue.
- Nothing in the executor consults a wall clock of its own. **`Platform::now()` is the only clock
  in the system.** That is the whole leverage point.

## 2. Why a wall clock produces a permanently black screen

A WIPI or MIDP game does not busy-loop. It arms a timer — 50 ms is a very common period — and
yields. Now consider a headless harness that calls `tick()` in a tight loop:

- the loop runs thousands of iterations per wall-second;
- each iteration advances `now()` by microseconds;
- the "all tasks sleeping, earliest wakeup in the future" branch is taken every single time;
- **zero timers ever expire**, so the guest never runs its next frame, so nothing is ever drawn.

**Exactly what the porter sees:** loads without error, host CPU pinned, every dumped frame entirely
black — and no amount of extra frames helps, because more frames means less emulated time per
frame, not more.

This is the single most common first trap on a new port. It is also *not* a bug in the emulator or
the game; it is a missing contract in your harness.

## 3. The fix: a frame-driven clock in `Platform::now()`

Advance an atomic millisecond counter once per frame and serve `now()` from it.

```rust
struct ProbePlatform {
    clock: Arc<AtomicU64>,
    // ...
}

impl Platform for ProbePlatform {
    fn now(&self) -> Instant {
        // The guest arms WIPI timers and then idles, so a wall-clock now() would never
        // fire them in a headless run that ticks as fast as it can.
        Instant::from_epoch_millis(self.clock.load(Ordering::SeqCst))
    }
    // ...
}
```

```rust
// main loop
let clock = Arc::new(AtomicU64::new(1_000)); // seed non-zero; 0 is a sentinel in a lot of code
for frame in 0..=frames {
    clock.fetch_add(ms_per_frame, Ordering::SeqCst);
    // drain redraw requests, fire scheduled key events, then:
    emu.tick()?;
}
```

**Make the per-frame millisecond count a required CLI flag, not a default.** A harness that
defaults it invites exactly the failure it exists to prevent, and a default that silently differs
from the documented invocation makes every measurement incomparable. `20 ms` (≈ 50 fps of guest
time) is the sane starting value for a title built around 50 ms timers; derive yours from the
period the guest actually passes to `MC_knlSetTimer`, which the trace stub will show you.

**But that period is a band, not a number (A).** Read it as a distribution before you pick a value:

- It may be a **player-settable option**. **(B)** In the reference port the frame timer measured
  `ms = 110 − 10 × speed` across a 1..10 in-game speed setting — 100 ms down to 10 ms, a factor of
  ten, every value of it equally "the period the guest passes to the timer call".
  `HIGH / CONFIRMED`, by device measurement across the option's whole range.
- Individual guest **states** request values far outside the play band. **(B)** Modal and list screens
  in the same title armed **244 ms**, against 20–50 ms in play. That is the game's own decision, not a
  defect — [frame-pacing.md](frame-pacing.md) §15.

Two consequences. **Pick one value for the harness, and record that the choice fixes the emulated
game speed for the whole measurement campaign** — runs taken at different `--ms` are not comparable
and nothing in the output says so. And **never derive a host-side ceiling clamp from the value you
picked without also giving it a floor**: a clamp fitted to the fast end of the band turns the slow
states into a stall.

**Seed the clock away from zero.** Sentinel-zero timestamps are endemic in this kind of code
("`last == 0` means never armed"), and a clock that starts at 0 turns a first-frame comparison into
a silent no-op.

## 4. Determinism, and the two ratios worth printing

Two benefits fall out of the frame-driven clock, both worth engineering for:

1. **Runs become bit-reproducible.** Emulated time is a pure function of the frame counter, so a
   scripted input at frame 260 lands at the same game state every run. Every diagnostic below,
   every differential render check, and every regression bisect depends on this.
2. **Emulated time is decoupled from wall time**, so you can print the ratio and see whether the
   host is keeping up:

```
[work] f=1200 instr=2.94G  2.45 M/frame  svc=180422  41.2 M instr/s  virt 0.83x
```

`virt 0.83x` means the host is running the guest at 83 % of real time. Under `1.0x` on a desktop
means your device build has no chance. This is also how you decide *which layer* is slow: guest
instructions per frame in the millions means the game rasterises in guest code and your graphics
layer is not the bottleneck.

**Automate the black-screen check.** When dumping a frame, print
`(N px, opaque X, non-black Y)` and assert `non-black > 0` in CI. Do not eyeball PNGs — a black
frame and a frame that is 99 % black look identical in a contact sheet.

## 5. Device pacing is a separate problem

The virtual clock belongs to the headless harness. On a real device `now()` is a wall clock and the
question inverts: how many `tick()` calls per displayed frame?

The shape that works **(A)**:

```rust
// budget/threshold values are per title — measure yours (B: one port used 24 / 6 / 16)
const FRAME_BUDGET_MS: u64 = ...;   // hard wall-clock cap on one frame's ticking
const TICK_FULL_MS:    u64 = ...;   // a tick taking longer than this means the game is work-bound
const FRAME_PACE_MS:   u64 = ...;   // a CEILING on how often the loop runs — never a target frame
                                    // rate; derive it from the fastest period the guest's own timer
                                    // can request (frame-pacing.md §4, §10), not from a display rate

let frame_start = Instant::now();
loop {
    let t = Instant::now();
    emu.tick()?;
    let took = t.elapsed();
    // keep ticking only while the game is work-bound...
    if took.as_millis() < TICK_FULL_MS as u128 { break; }
    // ...but never past the hard wall-clock cap
    if frame_start.elapsed().as_millis() >= FRAME_BUDGET_MS as u128 { break; }
}
```

Two rules **(A)**:

- **The wall-clock cap is not optional.** Without it, a guest that is genuinely work-bound holds the
  loop forever and the UI thread starves.
- **Do not copy the three numbers.** They are tuned to one game's tick shape **(C)**. Derive yours
  from the work gauge: pick `TICK_FULL_MS` just under the observed per-tick cost of a busy frame,
  and `FRAME_BUDGET_MS` at the frame time you are willing to ship.

**Two more rules, and the second is the one that stops you wasting a week (A):**

- **Never sleep past the guest's next wake-up.** The shape above says when to stop *ticking*; it says
  nothing about **sleeping**, and the sleep side is where the whole pacing bug lives. A host that
  sleeps a fixed pace, or an event pump that sleeps a flat interval whenever its queue empties,
  silently divides the game's speed. Sleep until the earliest due timer, keep the old flat value only
  as a **ceiling**, and give it a ~1 ms floor.
- **Measure host idle before optimising anything further** — *once the grids below are gone.* A slow
  screen with an idle host thread is either this file's sleep bug or the guest asking for it, and the
  requested-period gauge is what tells them apart. **(B)** After the pacing fixes, one class of screen
  in the reference port was still slow with the host thread idle, and the remainder was the guest's own
  244 ms timer — a guest-patch policy decision, not an emulator defect.
  [frame-pacing.md](frame-pacing.md) §15 owns that measurement.

**The pathology this shape does not prevent has its own file.** Two independent host loops, each
rounding the guest's period up to its own ~16 ms grid, **multiply**. The diagnostic tell is
counter-intuitive and is why it survives for months: **the whole game is slow while every report names
one screen.** **(B)** An unconditional sleep in the event pump plus a "nap to fill the frame pace" in
the worker turned a 20 ms guest timer into a measured **45 ms** — the entire game at **44 % of
designed speed** — and removing both took the title screen from **22 to 69 fps** and guest throughput
from 14.6 to 56.2 M instructions/s. `HIGH / CONFIRMED`. The invariant, the two gauges that detect it,
the direction-of-safety argument that makes the fix shippable without a behavioural A/B, and the two
other pacing failures are [frame-pacing.md](frame-pacing.md).

## 6. The executor task gauge

The only change worth making inside the executor itself is a **diagnostic**: tasks are removed only
on `Poll::Ready`, so a spawned task that never completes lives forever.

```rust
// wie_backend/src/executor.rs, inside tick
#[cfg(debug_assertions)]
{
    static MAX: AtomicUsize = AtomicUsize::new(0);
    let n = tasks.len();
    if n > MAX.fetch_max(n, Ordering::Relaxed) {
        tracing::info!("[exec] task high-water {n}");
    }
}
```

Report **only on a new maximum** — see [diagnostics.md](diagnostics.md) §1. On the reference port
the maximum stayed below 8 across 30 000 headless frames, which ruled out a task leak as the cause
of the native stack overflow. Keep the gauge anyway; it is three lines and it converts a whole
hypothesis into a one-line answer.

## 7. Harness traps that cost hours

Each of these was paid for on the reference port. They are properties of hand-rolled CLI parsers,
which is what every probe harness has.

- **Flag/value pairs.** A parser written as `while i + 1 < argv.len() { … i += 2 }` requires every
  flag to take a value. A boolean written bare consumes the *next flag* as its value and
  desynchronises everything after it. Either accept bare booleans explicitly or require `--flag 1`
  and document it.
- **Unknown flags are silently ignored.** A `_ => {}` arm turns a typo into a fully default run that
  looks plausible and proves nothing. Make an unrecognised flag a hard error.
- **Inconsistent boolean spellings.** One flag accepting `1|true` while another accepts only `"1"`
  is a trap you will fall into yourself six weeks later. Parse booleans in one function.
- **⚠ Diagnostics gated on an explicit frame list.** *Correction to the reference port's own docs:*
  its canonical differential-render invocation passed the check flag with **no dump-frame list**,
  and the check was gated on `dumps.contains(&frame)` — so the documented command could not print
  anything. If a diagnostic is frame-gated, say so in `--help` **and** in the example invocation.
