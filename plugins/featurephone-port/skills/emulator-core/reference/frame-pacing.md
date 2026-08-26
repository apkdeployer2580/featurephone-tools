# Frame pacing: how the host silently divides the game's speed

A feature-phone guest does not busy-loop. It arms a timer for `T` milliseconds and yields, so the
host's sleep behaviour *is* the guest's clock. When any host layer rounds that sleep up to a grid of
its own, the whole game runs at a fraction of design speed on every screen at once — no error, no
crash, no log line. Nobody reports that; they report individual screens as laggy, and the porter
spends rounds on rendering and on the network. This file is the pathology, its instrumentation, the
three failures that produce it, and the one case where the slow screen is the game's own decision.
The headless virtual clock and the adaptive multi-tick device loop belong to
[virtual-clock.md](virtual-clock.md); the cost of the guest's own rasterising loops belongs to
[guest-pixel-loops.md](guest-pixel-loops.md).

Provenance tags: **(A)** generic — apply verbatim · **(B)** worked example from the reference port —
copy the method, not the number · **(C)** do not copy. Confidence pairs and the refutation format:
`${CLAUDE_PLUGIN_ROOT}/shared/evidence-standard.md`.

## Contents

1. [The invariant](#1-the-invariant)
2. [Measure first: what the guest asked for beside what the host did](#2-measure-first-what-the-guest-asked-for-beside-what-the-host-did)
3. [Failure A — two independent grids multiply](#3-failure-a--two-independent-grids-multiply)
4. [The fix: a three-state next-wake-up and a pump that sleeps until the earliest due timer](#4-the-fix-a-three-state-next-wake-up-and-a-pump-that-sleeps-until-the-earliest-due-timer)
5. [Why it ships without a behavioural A/B: direction of safety](#5-why-it-ships-without-a-behavioural-ab-direction-of-safety)
6. [Failure B — a poll that yields only on an empty queue never returns](#6-failure-b--a-poll-that-yields-only-on-an-empty-queue-never-returns)
7. [Why the per-poll timer bound is ONE](#7-why-the-per-poll-timer-bound-is-one)
8. [Failure C — an unimplemented timer cancel is a speed multiplier per screen visited](#8-failure-c--an-unimplemented-timer-cancel-is-a-speed-multiplier-per-screen-visited)
9. [Generation-keyed timer shots](#9-generation-keyed-timer-shots)
10. [Clamping the guest's timer is a behaviour change](#10-clamping-the-guests-timer-is-a-behaviour-change)
11. [REFUTED — the frame-timer period was the player's speed setting](#11-refuted--the-frame-timer-period-was-the-players-speed-setting)
12. [Runtime switches and an on-device escape hatch](#12-runtime-switches-and-an-on-device-escape-hatch)
13. [Reading the pacing gauge line](#13-reading-the-pacing-gauge-line)
14. [Before you name a culprit: a total does not name a kind](#14-before-you-name-a-culprit-a-total-does-not-name-a-kind)
15. [What is left after pacing, and is not yours to fix](#15-what-is-left-after-pacing-and-is-not-yours-to-fix)
16. [Symptom table](#16-symptom-table)
17. [Checklist](#17-checklist)

---

## 1. The invariant

> **(A) The frame pace is a ceiling on how often the host loop runs. It is never the length of the
> nap. A host must never sleep past the guest's own next wake-up.**

`HIGH / CONFIRMED.` Two independent host sites violated this in the reference tree simultaneously.
Fixing both, A/B'd on the same phone with the same save **(B)**: title screen **22 → 69 fps**, guest
instructions 14.6 → 56.2 M/s, host loop 61 → 134 iterations/s, wall time inside `tick()` 27 % → 52 %.

Two properties make it the most expensive bug in the class. **It is uniform** — animation, AI cadence,
cooldowns, scrolling and the heartbeat all divide by the same factor, so nothing looks *relatively*
wrong and the game merely feels "a bit slow", which is what an emulator is expected to feel like. And
**it poisons every other measurement in the project**: fps, instructions per second and any "is the
host keeping up" ratio are wrong by that factor until it is fixed. Fix pacing first.

## 2. Measure first: what the guest asked for beside what the host did

**(A)** Print, per interval, the period the guest **asked for** and how long the host **actually
slept**. Three counters and one log line; no profiler substitutes for it.

```
emu frame timing: guest set 51/s timers (min 20 mean 20 max 20); pump napped 47/s for 16.0 ms avg
```

- **requested period, min / mean / max**, at every set-timer entry point. The spread matters as much
  as the mean: `min = mean = max` at one call per frame is a single fixed-period driver; a wide spread
  means several states with different design speeds (§15).
- **nap count and mean nap length**, at every place the host sleeps between guest ticks.
- **share of wall time inside `tick()`** — whether the host is the constraint at all. A slow screen
  with the host thread mostly *idle* is not the host's fault.

Three readings decide the whole investigation: sleep ≈ requested means this layer is done; sleep on a
constant grid regardless of the request means §3; a large requested period with the host idle means
§15 and there is nothing to fix. The full table is §13. Report it as an **interval rate that resets on
read**, never a lifetime average — the title screen (where the loop sleeps through most of every
frame) otherwise dilutes real play into ambiguity. Gauge hygiene, the per-tick hitch report and
per-id attribution: [diagnostics.md](diagnostics.md).

## 3. Failure A — two independent grids multiply

**(B)** The two offending sites in the reference tree, found only once the gauge above existed:

1. the MIDP event pump slept an unconditional flat 16 ms **whenever its queue emptied**;
2. the host worker loop slept to "fill" a 16 ms frame pace.

Neither is obviously wrong alone, and each is what a reasonable person writes. Together, a guest
timer of **20 ms measured 45 ms effective — the entire game at 44 % of design speed, on every
screen** (`HIGH / CONFIRMED`, device). They **multiply rather than add**, because each layer quantises
the output of the other: the pump alone turns a 21 ms wait into 32 ms (two flat naps), and the worker
then rounds *that* up to its own pace boundary.

> ★ **(A) A porter who fixes one rounding and stops still sees most of the loss, concludes the fix did
> not work, and goes back to blaming the interpreter.** Grep for *every* place the host sleeps between
> guest ticks and fix them in one change: the worker/pace loop · the pump's empty-queue nap · any
> `wait_timeout` in the platform glue · a present/vsync wait · a leftover "be nice" yield.

## 4. The fix: a three-state next-wake-up and a pump that sleeps until the earliest due timer

**(A)** The scheduler already knows the answer — it computes it to decide its own early break — and
keeps it private. Publishing it is the whole fix on the host side **(B)**.

```rust
// wie_backend/src/executor.rs
/// Some(0)  a task is runnable right now
/// Some(n)  everything is asleep; the earliest sleeper wakes in n ms
/// None     no tasks at all
pub fn next_wakeup_in(&self, now: Instant) -> Option<u64>
```

**Three states, not two:** "asleep" read as "runnable" is a spin, and "runnable" read as "asleep for
the frame period" is the original over-sleep.

```rust
// host worker loop
let mut nap = pace_remaining;                // the frame pace is the CEILING
match emu.next_wakeup_in() {
    Some(0) => continue,                     // runnable now: tick again (frame budget still applies)
    Some(n) => nap = nap.min(n),             // never sleep past the guest's own timer
    None    => {}                            // nothing scheduled: the ceiling stands
}
thread::sleep(Duration::from_millis(nap.max(1)));   // 1 ms floor: a nap, not a spin
```

In the event pump, sleep until the **earliest not-yet-due queued timer** instead, clamped **(B)**
(`FLAT` = the old unconditional value, 16 in the reference tree):

```rust
let nap = (due.saturating_sub(now)).clamp(1, FLAT);
```

Both bounds are load-bearing **(A)**: the old flat value stays as the **ceiling**, so a queue with no
timers behaves *exactly* as before — that is what keeps the change reviewable — and the **1 ms floor**
stops the fix becoming a spin on an already-due timer.

## 5. Why it ships without a behavioural A/B: direction of safety

**(A)** After the change, **no path can sleep longer than it used to** — a monotonic statement about
the whole behaviour space, and strong enough to ship on: the change cannot introduce a *new* late
wake-up, which is the only failure these sleep sites can cause. It is not a claim of zero behaviour
change (the game now runs at the speed it was written for, which is the point), and it is why this
shipped on unit-level evidence plus the §13 gauge rather than a full behavioural A/B. Reuse the
shape: state the property monotonically, name the failure it can cause, let the gauge confirm the
magnitude afterwards.

⚠ **INFERRED, nothing was run.** Host-side timeouts and poll budgets tuned *before* the pacing fix
were calibrated against the slow clock, so some are now looser than intended. What would settle it:
list every host constant expressed in milliseconds or in polls and re-derive each against the
post-fix gauge.

## 6. Failure B — a poll that yields only on an empty queue never returns

**(A)** A pump whose poll returns only when its event queue is **empty** stops returning on any device
that cannot finish a frame inside the guest's timer period: the frame callback re-arms the timer, and
by the time it returns that timer is **already due**, so the queue never empties. Three consequences,
each of which reads as a different bug (`HIGH / CONFIRMED`):

- **The executor's own inner deadline is bypassed** — it is tested only *between* steps, so a poll
  that never returns never reaches it. A budget that cannot be reached is not a budget.
- **Everything the host does outside `tick()` stops** — input drain, the background/paused check, the
  frame pacing itself. Input latency becomes unbounded.
- **A backgrounded app keeps burning CPU**, because the check that would pause it is in that loop.

It converts "runs slowly" into "stops responding and keeps running in the background", and it only
appears on the low-end device you test last. **Fix: bound the due timer callbacks handled per poll,
and yield after each.**

## 7. Why the per-poll timer bound is ONE

**(B)** Measured on a 2018-era low-end phone at roughly 8 ms per guest frame, against a 24 ms frame
budget:

| bound | mean tick | ticks over budget | guest fps |
|---|---|---|---|
| 4 | 29.9 ms | 895 / 1807 | 13.1 |
| **1** | **24.9 ms** | — | **15.1** |

At 4, one poll chained four whole guest frames while the host's between-tick work waited. On a device
that keeps up the branch runs 0–1 times per call either way, so the bound only matters on the device
that is already struggling.

> ★ **(A) "Hand control back after each frame" is what the bound means. Anything above 1 recreates a
> smaller version of the bug the bound was added to fix.**

## 8. Failure C — an unimplemented timer cancel is a speed multiplier per screen visited

**(A)** If the platform's timer-cancel primitive is a warning-log no-op returning success, and arming
**pushes** a new shot onto the event queue instead of **replacing** the pending one, then every screen
the guest visits leaves a live frame timer behind. The frame callback then runs N times per period
and the game runs N× fast.

`HIGH / CONFIRMED (code reading plus the reported symptom shape).` The player report is **"the game
got faster after visiting one particular screen once"**, and the model makes a falsifiable prediction
that closes it: *a second visit makes it faster again.*

> ★ **(A) This is easy to misattribute**, because it presents as a speed-up tied to one screen and so
> gets blamed on whatever else changed that session — in the reference port, on a frame-timer clamp
> that had already been fixed. **Any WIPI/MIDP port with a stubbed cancel has this defect.** Read your
> cancel primitive and your arm path before theorising: does arming replace, or push?

## 9. Generation-keyed timer shots

**(A)** Implement cancellation by **generation**, not by removing the queued shot.

- each timer object carries a generation counter;
- **arming** bumps it (stale-ing any pending shot) and pushes a shot stamped with the new generation;
- **cancelling** bumps it and pushes nothing;
- the pump **discards** any shot whose stamp is no longer the timer's current generation;
- unkeyed timers keep the old path exactly.

The obvious implementation — search the queue and delete — **cannot be written safely** in a
cooperative pump: the queue cannot be edited while a guest callback runs, that callback is usually the
very code calling cancel, and the pump has already moved not-yet-due timers into a local list.
Generation stamping is the only place the information exists at the right time.

Verification that shipped it **(B)**: 5 unit tests (arm · re-arm replaces · cancel · unknown id is a
no-op · two timers independent) plus a deterministic probe A/B showing **frame bytes identical at
frames 200 and 400**, i.e. the single-timer normal path is provably untouched. Gauges to keep **(A)**:
the unset rate, the stale-drop rate, and — the direct test of the bug — that the **requested-timers
rate returns to its normal-play value** after a round trip through the screen that used to leak one.

## 10. Clamping the guest's timer is a behaviour change

**(A)** Capping the guest's requested frame-timer period at a host ceiling is not a performance fix. A
guest loop that counts **frame ticks** rather than wall time counts them faster the moment you shorten
its period, so a clamp rewrites game timing on every screen you did not test. It is therefore a
**runtime switch with an on-device escape hatch, never a compile-time constant**; **0 means disabled**,
so any target that never sets it stays bit-identical; and it needs a floor — §11 is the refutation
that produced that rule.

★ Related and separate: **never cap the display frame rate of a title whose frame rate *is* its game
speed.** The usual 30 fps cap becomes slow motion. Reduce per-frame host cost instead —
[../../android-host/reference/sustained-load.md](../../android-host/reference/sustained-load.md).

## 11. REFUTED — the frame-timer period was the player's speed setting

### ★ CORRECTION — the in-game speed option *is* the frame-timer period

**Previously documented:** "the in-game speed option only changes how much guest logic advances; the
host has no concept of speed."

**Refuted by:** device measurement across the option's whole range — speed 1 → 100 ms, 6 → 50 ms,
10 → 10 ms, i.e. `ms = 110 − 10 × speed` — with the clamp's own gauge reporting
`capped 100 % to 50 ms` at speed 1. The game ran at exactly double the chosen speed.

**Actual mechanism:** the option writes the frame-timer period directly, so a flat host ceiling
rewrote every slower setting to the cap. The wrong sentence was attractive because it had been written
to explain an *unrelated* lag report and never had evidence behind it — a plausible sentence in a
document, the most durable kind of error.

**Consequence if carried forward:** the clamp is a bug disguised as a feature, invisible to anyone at
the default setting. Only players who deliberately choose a slow speed see it, and they report it as
"the game sped up" — which sounds like a compliment rather than a bug.

**The fix (A):** put a **FLOOR** on the clamp, above the whole band the option can produce, so only
out-of-band values (a modal screen's much longer period, §15) are ever shortened, and add a
regression test that enumerates **every value the option can emit** and asserts none is capped. In
the reference tree the constant is `TIMER_CLAMP_FLOOR_MS`, carrying its symptom, measurement and date
at the definition site **(B)**.

**Generalise (A):** before clamping *any* guest-supplied timing value, enumerate the band the game can
produce by two independent routes — read the writer (the option handler) **and** measure the whole
range on device. A clamp derived from one observed value is one derived from one player's settings.

## 12. Runtime switches and an on-device escape hatch

**(A)** Every pacing knob that changes guest-visible timing gets the same treatment as a
host-reimplementation master switch ([guest-pixel-loops.md](guest-pixel-loops.md)):

- **the value lives in a config file the player can edit on the device**, so the same phone with the
  same save can be A/B'd — a report you cannot reproduce on the reporter's own device is not evidence.
  Keep the filename neutral, e.g. `<app-files>/timer_cap.txt`;
- **absent or 0 = disabled = bit-identical**, which makes the switch safe to enable for one target and
  leave untouched for the others;
- **the gauge prints the value in force and the fraction of requests it capped** — the
  `capped 100 % to 50 ms` line above is what caught the refutation; without the percentage a clamp is
  silent by construction;
- **a user-visible switch, not a silent device tier** — a silent tier means two players are playing
  different games and neither can tell you which.

## 13. Reading the pacing gauge line

| what you see | what it means | next |
|---|---|---|
| nap mean ≈ a constant, independent of the requested period | a flat grid | §3 → §4 |
| nap mean ≈ requested, in-tick share high | the host is the constraint | [guest-pixel-loops.md](guest-pixel-loops.md) |
| nap mean ≈ requested, host mostly idle, requested period large | the game's design speed | §15 |
| requested rate ≫ one per frame, and it grew after visiting a screen | leaked timers | §8 → §9 |
| `min` far below `max` on one screen | several states, different periods; measure per screen class | §2 |
| nap count ≈ 0 and CPU pinned | a poll that never yields, or a lost 1 ms floor | §6, §4 |

Two reporting rules from [diagnostics.md](diagnostics.md): **interval rate with reset on read** (a
lifetime average hides a regression for exactly as long as the session has been running), and a
**per-tick hitch report** for stalls too short for a 30 s window to see.

## 14. Before you name a culprit: a total does not name a kind

The reference port's pacing investigation began with a misattribution that generated a round of
plausible work in the wrong place.

### ★ CORRECTION — the 26,000 calls/s was a fill-rectangle call, not a receive poll

**Previously documented:** "the 26,000/s trap rate is the receive call spinning while waiting for an
acknowledgement that never came."

**Refuted by:** re-measuring **per category and per id** on device. Receive polls: **0/s**. The
26,000/s was the platform's fill-rectangle call.

**Actual mechanism:** the *number* was right and the *attribution* was wrong. On some carrier
frontends the C standard library is dispatched through the **same trap** as the platform API, so a
memory copy and a socket call are indistinguishable in the total. The reading was attractive because
the screen under investigation was a networked one, and a network stall is the obvious story for a
screen that appears to hang.

**Consequence if carried forward:** a wrong attribution generates plausible follow-up work. The task
this one created was "add a receive-poll cap" — not the fix for that symptom — while the real causes
(this file's pacing bug, plus a banded fill path) went unexamined for a round.

> ★ **(A) An aggregate syscall/trap total does not tell you the KIND of call. Naming a culprit from
> the total alone is not evidence.** Get per-category and per-id rates first: two relaxed increments
> at the **single** trap entry point (one per-category array, one per-id array with the id masked into
> range), and that one site covers every dispatch path — init, platform API, standard library, your
> own patch hooks. Implementation and reporting rules: [diagnostics.md](diagnostics.md).

Where the corrected number led, one sentence each: high fill-rect rates on panel and dialog screens
are the framebuffer round-trip, [graphics-perf.md](graphics-perf.md); a field frame issuing a dozen
platform draw calls while executing millions of guest instructions is the guest rasterising in its own
code, [guest-pixel-loops.md](guest-pixel-loops.md).

## 15. What is left after pacing, and is not yours to fix

**(B)** With both grids removed, one class of screen still ran slowly — and correctly. Modal boxes
and list/selection states **requested a 244 ms frame timer** (`min = mean = max`, one call per frame)
against 20–50 ms everywhere else. Measured: **7.0 fps against a design 4.1 fps, host thread 78 %
idle.** The host was already delivering more frames than the game asked for.

**(A)** That is the terminal state of this investigation, and §2's gauge is what recognises it: *the
guest asked for it and the host is idle — there is nothing left at this layer.* Making it faster means
overriding a guest constant: a behaviour change and a product decision, behind §10's runtime switch
with §11's floor, never as a constant and never for a period inside the band the player's own speed
option can produce. Two rounds were spent in the reference port optimising a screen whose frame rate
was the game's own design speed; three counters and one log line end that argument.

## 16. Symptom table

| Symptom | Cause | Fix |
|---|---|---|
| The whole game feels about half speed — every screen, not one — and the host's gauge says it is idle most of the time | Two host layers each round the guest timer to their own grid; the roundings multiply | §3 → §4 |
| Fixing the obvious sleep helped a little, so you concluded that was not the problem | The second grid is still there | §3 |
| On a slow device `tick()` stops returning: input latency unbounded, backgrounded app still burning CPU | The pump yields only on an empty queue and the callback re-arms an already-due timer | §6 → §7 |
| Mean tick well over the frame budget on a low-end device, better when fewer timers are handled per poll | A per-poll bound above 1 chains whole guest frames | §7 |
| The game got faster after visiting one screen once — and faster again the second time | Timer cancel is a no-op stub and arming pushes instead of replacing | §8 → §9 |
| The player's speed setting stopped mattering, or the game runs at exactly double the chosen speed | A host ceiling clamp on the frame-timer period, which *is* the speed option | §11 — add a floor |
| Capping fps to cool the device produced slow motion | On this class of title the frame rate is the game speed | §10 |
| One class of screen runs at a few fps while play is fine, host thread ~78 % idle there | The game armed a much longer timer in that state | §15 — nothing to fix |
| A trap/syscall counter reads tens of thousands per second and the obvious subsystem gets blamed | An aggregate total does not name the kind of call | §14 |

## 17. Checklist

- [ ] The gauge prints the **requested** period (min/mean/max) beside the **actual** nap and the
      in-tick share, as an interval rate that resets on read.
- [ ] Every host site that can sleep while a guest timer is pending is enumerated, not just the
      obvious one.
- [ ] No sleep outlasts the guest's next wake-up: the scheduler exposes a **three-state** next-wake-up
      and the worker naps `min(pace_remaining, next_wakeup)`, with a **1 ms floor** and a ceiling equal
      to its old flat value.
- [ ] The change is stated as a monotonic property ("no path sleeps longer than before") before it
      ships; the gauge confirms the magnitude after.
- [ ] The pump's poll handles at most **one** due timer callback, then yields.
- [ ] Timer cancel is implemented by generation, arming **replaces**, stale shots are dropped, and the
      unset/stale-drop rates are visible; the requested-timers rate returns to its normal-play value
      after a round trip through every screen that arms a timer.
- [ ] Any clamp on a guest timing value is a runtime switch, defaults to disabled, has a floor above
      the whole band the game can request, and reports what fraction it capped — with a regression
      test enumerating every period the player's own settings can emit.
- [ ] No culprit is named from an aggregate trap total — per-category and per-id rates exist.
- [ ] Every performance figure in the notes was taken **after** the pacing fix and says whether it is
      headless or on-device.
