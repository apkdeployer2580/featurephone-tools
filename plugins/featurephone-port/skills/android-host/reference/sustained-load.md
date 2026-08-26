# Sustained load: when the device is the constraint

Sustained load is not throughput. A port can hold full speed and still make a
several-generations-old phone uncomfortably hot in ten minutes, and that decides shippability
independently of frame rate. This file is what to remove when the *device*, not the game, is the
constraint: the axis decomposition, the one mitigation that is always on because it is provably
output-identical, the two that are opt-in because they change what the player sees and hears, what
actually breaks on an old phone, what the minimum-API decision costs, and the three ways a device
measurement lies to you. The guest's own frame budget is
[frame-pacing.md](../../emulator-core/reference/frame-pacing.md); hosting a guest pixel loop is
[guest-pixel-loops.md](../../emulator-core/reference/guest-pixel-loops.md); process memory and the
low-memory killer are [memory-footprint.md](../../emulator-core/reference/memory-footprint.md).

Tags: **(A)** generic · **(B)** worked example from the reference port, re-derive it ·
**(C)** do not copy. `⚠` marks anything inferred. Conventions:
`${CLAUDE_PLUGIN_ROOT}/shared/evidence-standard.md`.

## Contents

1. [Sustained load is its own defect class](#1-sustained-load-is-its-own-defect-class)
2. [Decompose into three axes](#2-decompose-into-three-axes)
3. [What you must not touch: the frame rate is the game speed](#3-what-you-must-not-touch-the-frame-rate-is-the-game-speed)
4. [Always on because provably identical: the identical-frame skip](#4-always-on-because-provably-identical-the-identical-frame-skip)
5. [Opt-in: half-resolution render with hardware upscale](#5-opt-in-half-resolution-render-with-hardware-upscale)
6. [The cheap synthesiser, on the same switch](#6-the-cheap-synthesiser-on-the-same-switch)
7. [A user-visible switch, never a silent device tier](#7-a-user-visible-switch-never-a-silent-device-tier)
8. [Observability: the paint gauge and the zero-skip signal](#8-observability-the-paint-gauge-and-the-zero-skip-signal)
9. [What actually breaks on an old phone](#9-what-actually-breaks-on-an-old-phone)
10. [Synchronous platform calls: a hitch, not a load](#10-synchronous-platform-calls-a-hitch-not-a-load)
11. [The minimum-API question, and what lowering it costs](#11-the-minimum-api-question-and-what-lowering-it-costs)
12. [The dev device is not the fleet](#12-the-dev-device-is-not-the-fleet)
13. [Measuring honestly on a device](#13-measuring-honestly-on-a-device)
14. [Read your own vendored fork first](#14-read-your-own-vendored-fork-first)
15. [Symptom table](#15-symptom-table)
16. [Checklist](#16-checklist)

---

## 1. Sustained load is its own defect class

**(A)** Throughput asks "does a frame finish in time". Sustained load asks "what does this cost per
hour". Different symptoms, different measurements, different fixes — and it appears first on the
*oldest* device you support, usually only there. That makes it the one performance class where a
single report from one player is the whole signal; you cannot see it on a current phone at all.

**(B)** In the reference port a 2018-class mid-range phone became uncomfortably hot after roughly ten
minutes of play, with no complaint from a current flagship on the same build and the same save.

**(A)** The framing that produces good fixes: **remove work that is invisible, not work that is the
game.** Every mitigation below is judged by whether a player could tell it happened.

## 2. Decompose into three axes

**(A)** Split host cost into the three things that can burn a core for an hour, and measure them
separately before touching anything:

| axis | what it is | measure it by |
|---|---|---|
| **interpreter** | guest instructions retired per frame | instructions/frame, inst/s |
| **host pixel pipeline** | clear, convert, transfer, blit, overlay, present | frames sent/s, host work per frame |
| **audio synthesis** | the sequencer and its effects, running even in silence | thread CPU share while nothing is audible |

**(B)** The reference port's three readings from one session: the synth measured **13.3 % of a core
while rendering silence**; the pixel pipeline was running a full convert/blit/present at **69 fps on
a static title screen**; the interpreter was deliberately left alone, because on this class of title
the interpreter *is* the game.

**(A)** Two properties of the split matter more than the numbers. **The two reducible axes are
reducible because they produce nothing** — a repaint identical to the last one, and reverb applied to
silence, have no observer. And **measure per screen class, not once**: **(B)** the reference port
counted 12 platform draw calls per frame in the field against 1,650–2,450 small fill-rect calls per
frame on panel screens, the latter at 5–7 fps on the low-end phone. One measurement taken on the
wrong screen sends the next three sessions to the wrong layer —
[graphics-perf.md](../../emulator-core/reference/graphics-perf.md).

## 3. What you must not touch: the frame rate is the game speed

**(A)** The first idea anyone has about a hot phone is a frame-rate cap. On this family of guests it
is not a performance setting but a behaviour change: the guest advances its logic once per frame
timer, so a 30 fps ceiling is slow motion. **Never cap fps. Reduce per-frame host cost and leave the
guest's timing alone.** Clamping the guest's *requested* period from the host is the same hazard: it
needs a floor and a runtime escape hatch, never a constant — mechanics in
[frame-pacing.md](../../emulator-core/reference/frame-pacing.md).

> **REFUTED.** *Previously documented:* "the in-game speed option only changes how much guest logic
> advances; the host has no concept of game speed, so a host-side ceiling on the frame timer is
> purely an optimisation."
> *Refuted by:* device measurement across the option's whole range. The option **was** the frame
> timer period — **(B)** `ms = 110 − 10 × speed` (speed 1 → 100 ms, 6 → 50 ms, 10 → 10 ms) — with the
> gauge reporting `capped 100 % to 50 ms` at speed 1. A flat ceiling rewrote every slow setting to
> the cap and the player reported the game had sped up, exactly double, at the slowest setting.
> *Why the wrong reading was attractive:* the sentence had been written to explain an unrelated lag
> report and never had evidence behind it, and at the default setting the clamp is invisible.
> *Consequence if carried forward:* a clamp shipped as a constant silently rewrites game timing on
> every screen you did not test, and you have no way to ask a player to prove it.

**(A)** The trap in the other direction: a very low frame rate on one screen class may be the game's
design speed and not your problem. **(B)** Modal and network screens in the reference port requested
a 244 ms frame timer (min = mean = max, one per frame) against 20–50 ms everywhere else; the measured
7.0 fps there was against a design 4.1, with the host thread 78 % idle. Print the period the *guest
asked for* before optimising the screen that renders it.

## 4. Always on because provably identical: the identical-frame skip

**(A)** These guests repaint on their own timer whether or not anything changed, so a static screen —
title, an open menu, a shop — runs the whole host pipeline dozens of times a second for no observable
result. Hash the **raw guest framebuffer bytes** and drop the frame *before* the pixel-format
conversion, so one test elides convert, transfer, blit and present together. This is the only
mitigation here that ships **on**, because it cannot regress: the worst case of a hash collision is
one stale frame, until the next real change.

**(B)** The reference implementation is a 64-bit FNV-style hash over 8-byte gulps of the raw guest
pixels — about 30 000 iterations for a 496×240 16 bpp frame — pinned by three unit tests: identity,
single-byte sensitivity, and length sensitivity including the sub-8-byte tail.

```rust
// present path, BEFORE any pixel-format conversion
let h = fnv1a64_gulps8(guest_fb_bytes);
stats::GUEST_FRAMES.fetch_add(1, Relaxed);   // ★ count the frame BEFORE the skip
if h == last_hash && fmt == last_fmt {
    stats::FRAMES_SKIPPED.fetch_add(1, Relaxed);
    return;                                   // no convert, transfer, blit or present
}
last_hash = h;
last_fmt  = fmt;
```

**(A)** Three clauses keep it honest; dropping any one turns a free win into a silent defect.

1. **Increment the guest frame counter before the skip.** Every throughput gauge is denominated in
   frames; count after the skip and every number you collect afterwards is quietly wrong.
2. **Make it observable** — frames sent/s *and* skipped/s (§8).
3. **A path that does not feed the hash must not consult it.** If some pixel formats or code paths
   bypass the hashed conversion, they bypass the skip too, or they compare against a stale hash.

## 5. Opt-in: half-resolution render with hardware upscale

**(A)** If your surface API lets you set buffer geometry independently of window size, a buffer
*smaller* than the window is upscaled by the compositor in hardware. Rendering at half linear size
therefore divides every pixel-proportional host cost — clear, game blit, overlay draw, present
swizzle — by **four**, at no CPU cost. It is the largest single lever on the host pixel axis.
**(B)** Confirmed on Android through the native-window buffer-geometry call. ⚠ On an API that ties
buffer size to window size the lever does not exist; settle it by setting a smaller geometry once and
checking the surface still fills the window instead of occupying a corner.

**(A) The precondition decides the whole thing, so check it first.** This is a three-line change *if
and only if* layout and hit-testing are stored as **fractions of the window**:

1. divide the window size once, where the surface is created or resized, so everything downstream is
   already working in render space;
2. divide incoming touch coordinates once, at the single input entry point;
3. leave the guest's own logical resolution untouched.

In a codebase that stores control positions in pixels the same change is a rewrite of the layout
system. **(B)** The reference port verified its placement store was fraction-based *before* starting,
and the edit landed in exactly those three places. If yours stores pixels, that is the finding — an
argument for fractional placement ([input-and-hud.md](input-and-hud.md)), not against the mode.

**(A)** It defaults **off**: the quality change is visible, which by §7 makes it opt-in.

## 6. The cheap synthesiser, on the same switch

**(A)** The synth axis reduces the same way, but every reduction is audible, so it is opt-in by
construction. Wire it to the **same** switch as the half-resolution render: a player whose device
cannot cope needs both, and two settings means three configurations you never tested. Reductions
worth having, cheapest first: drop effects (reverb, chorus) that run while nothing is audible, reduce
the mixing rate, reduce polyphony — which of those your core exposes belongs to
[audio-mixing.md](audio-mixing.md) and [audio-core.md](../../emulator-core/reference/audio-core.md).
A restart notice is legitimate here: rebuilding an audio pipeline live is more failure surface than
the mitigation is worth.

## 7. A user-visible switch, never a silent device tier

> **The standing rule: nothing regresses on a device that already works.** **(A)**

**(A)** The split is mechanical, which is the point — it removes the judgement call.
Provably output-identical (the frame skip) ⇒ **on by default**, because nobody can tell and there is
nothing to opt into. Changes what the player sees or hears (half-res, cheap synth) ⇒ **off behind a
switch**, because it is a regression for every device that did not need it.

**(A)** Do not tier by guesswork. A silent tier means a player on the boundary runs a different
program from the one you tested, with no way for either of you to say which — and it removes the only
mechanism that makes an on-device A/B possible: asking one player to flip one switch and report.

**(B)** The reference port applied this rule twice to opposite conclusions. Once it defaulted a
half-resolution mode and a reduced-synthesis preset **off** with a restart notice. Earlier, the same
rule *stopped* a speculative device-tiering scheme that would have lowered reverb and chorus defaults
on older hardware, because no measurement yet showed those devices could not carry them: "turn it on
when measurement shows the device cannot cope; do not split tiers on a guess."

## 8. Observability: the paint gauge and the zero-skip signal

**(A)** Every work-saving change needs a gauge reporting the **saved quantity as a level or a rate**,
so a later regression appears as a number climbing back toward the old value rather than as a vague
slowdown from a player. A one-off measurement in a document does not do this. For the pixel axis that
is one periodic line: frames sent/s, frames skipped/s, and the render divisor in force.

- **Zero skips on a static screen ⇒ the gate is dead.** Either the hash is taken after something that
  always differs, or an unhashed path is doing the presenting.
- A skip rate collapsing during play is normal; one collapsing on the title screen is the regression.

**(A)** Report gauges as an **interval rate with a reset**, never a lifetime average. An average
mixes the title screen — where the loop sleeps through most of every frame — with real play, so a low
number could mean "too slow" or "nothing to draw", and a regression stays invisible for as long as
the session has been running: exactly the session length in which players notice regressions. Gauge
design and the aggregate-versus-hitch distinction:
[diagnostics.md](../../emulator-core/reference/diagnostics.md).

**(A)** On a release build a periodic log line may be all you have, because the debug bridge cannot
read app-private storage on a non-debuggable package — so put the render divisor and the resolved
switch states in the on-device report as well ([storage-and-config.md](storage-and-config.md),
[on-device-diagnostics.md](../../port-shipping/reference/on-device-diagnostics.md)).

## 9. What actually breaks on an old phone

**(A)** These are device-class defects, not title defects: a new title hits them all, and none
reproduces on a phone that keeps up. That last property is why they ship.

**The event poll that never returns.** A poll that yields only when its queue is *empty* never
returns on a device slower than the guest's timer period, because a timer callback re-arms a timer
already overdue by the time it returns. Everything the worker loop does *between* polls — draining
input, checking the paused flag, frame pacing — becomes unreachable, so input latency is unbounded
and the guest keeps running while the app is backgrounded. It presents as "the controls stop
responding" *plus* "the battery drains in my pocket", two reports nobody connects. Fix: cap
consecutive timer callbacks per poll and yield. **(B)** Tuned on device from 4 to 1 — average tick
29.9 ms with 895 of 1807 ticks over budget at 4, versus 24.9 ms at 1 — because each chained callback
was a whole guest frame. On a device that keeps up the branch never fires, so it is free.

**A transient resource failure that becomes permanent.** One failed attempt to open a platform
resource must never disable it for the session. Retry forever on a two-stage cadence — a few fast
attempts for races, then a patient slow loop — and **drain the pending queues on every attempt**, or
success after a long retry replays a session's worth of stale events at once. **(B)** Five one-second
attempts, then an indefinite ten-second loop, draining both queues per attempt.

> **REFUTED.** *Previously shipped:* an audio pipeline that abandoned itself after a single failed
> device open, reasoning that a device which refuses to open has no audio.
> *Refuted by:* a field report of **total silence from a distributed build**, on the device the player
> actually used, while every device the developer owned was fine.
> *Actual mechanism:* resource acquisition on a phone is racy — a cold-boot race, another app briefly
> holding the device, a Bluetooth route switch — and those races are device-specific and transient.
> The give-up path converted a one-second race into a permanent failure.
> *Consequence if carried forward:* a class of "no sound at all" reports you can never reproduce,
> arriving only from devices you do not own.

**The volume rocker on the wrong stream.** A game that is often silent has no active media stream, so
the rocker lands on the ringer: the player raises the volume to maximum, watches an unrelated slider
fill, and reports the game is quiet at full volume. Route the rocker explicitly to the media stream —
one platform call, and the report is literally about the part that is broken.

**Getting killed rather than crashing.** An emulator that pre-commits a large guest address space is
structurally the fattest process on the phone and so the low-memory killer's first choice. It presents
as "the app closes and comes back at the title", with no tombstone and nothing in your own log —
mechanism in [memory-footprint.md](../../emulator-core/reference/memory-footprint.md), telling a kill
from a crash in [crash-triage.md](../../port-shipping/reference/crash-triage.md).

## 10. Synchronous platform calls: a hitch, not a load

**(A)** A synchronous platform call on the emulator thread — attach the thread, allocate a string,
look up a system service, make a cross-process call — can blow the frame budget on its own, and it is
worst exactly when the game is busiest. Move it to a dedicated thread that **attaches once**, caches
the service handle globally, is reached by a **non-blocking try-send on a bounded channel**, and
**coalesces bursts** into one action. Let the queue *drop*: one missing buzz is invisible, a stalled
game is not; and coalescing stops three rapid effects queuing three long actions that outlive the
encounter.

**(B)** In the reference port this was traced to a guest special-attack branch calling a platform
wrapper gated on the game's own option — and note the negative result that preceded it: an asset
re-parse on the same code path was benchmarked against all 33 real sound files at 0.016–0.049 ms each
and cleared, **for that stage**. Two rounds later the re-parse *did* matter, at the archive read
around it. A bare "the parse is not the problem" would have blocked the later fix; record what a
negative result clears.

## 11. The minimum-API question, and what lowering it costs

**(A)** **An old low-end phone is not a 32-bit phone.** Several-generations-old flagships still report
the 64-bit ABI first, so the right response to "support older devices" is usually to change nothing
about the ABI. A second ABI doubles build time and artifact size — and if your emulator reserves a
large thread stack as a mitigation for a stack-growth bug, a 32-bit build turns that free reservation
into a real address-space hazard. **(B)** An audit across three devices spanning eight years and three
Android major versions: all three reported the 64-bit ABI first, and the oldest ran an API level far
above the declared minimum. The 32-bit build was refused explicitly on stack-reservation grounds.

**(A)** Run the audit as a table — minimum, target and compile API levels, ABI list, native toolchain
platform level, every version-guarded platform call — and accept **"nothing to lower" as a legitimate
outcome**. It redirects the investigation from the version axis to the individual device, and it stops
you paying the real cost of a lower minimum (on Android, typically an audio-backend rewrite) to serve
devices that could not carry the interpreter anyway. When an audit finds no violation, the defect is
usually **the absence of any way to confirm one** — build that next.

## 12. The dev device is not the fleet

**(A)** Three rules, each of which has cost somebody a round:

1. **A host-side gauge does not clear the device.** State per finding which measurements were
   headless and which were on device. **(B)** One reference round shipped with "device measurement:
   zero, all of it headless — the cable came out mid-measurement"; the device gauges added for that
   reason later resolved two separate misattributions. A separate feature passed every host test
   having **never once run on a device**.
2. **Attribute every report to an installed build before comparing it to anything.** Read the version
   off the phone that produced the report; do not trust your own build record.
3. **The device that receives your builds and the device that plays them may be different phones.**

**(B)** The shipped case: a distributed build was **completely silent on the player's device** while
working on every device the developer owned (§9). The version axis was audited and cleared; the real
defects were a give-up path in resource acquisition and a misrouted volume rocker, neither of which
any developer device could produce. What closed the report class was not a fix but an **on-device
status line** — each pipeline stage writing one human-readable string the diagnostics screen shows,
so the next report triages itself. Handout and recall discipline:
[distribution-and-recall.md](../../port-shipping/reference/distribution-and-recall.md); the
verification procedure: `${CLAUDE_PLUGIN_ROOT}/shared/device-verification.md`.

## 13. Measuring honestly on a device

**(A)** Three traps invalidate a whole performance round, and each is one command to check.

**(a) You were measuring a debug artifact.** Setting only the optimisation level on the debug profile
makes a build *usable*, not *measurable*: debug assertions and overflow checks stay on, and an ARM
interpreter pays an overflow check on every arithmetic operation. Gate every performance claim on a
release artifact and confirm it by reading the installed package's own flags, not by remembering
which profile you used.

> **REFUTED.** *Previously concluded:* "this phone is too slow for the interpreter" — with a round of
> work planned around it.
> *Refuted by:* a device A/B on one variable. The same scene on the same phone moved the emulator
> thread from **89.6 % to 64.5 % of a core** purely by switching from the debug artifact to the
> release one; the debug artifact was identified after the fact by its package flags.
> *Why the wrong reading was attractive:* the numbers were plausible and self-consistent, and the
> profile had been made "fast enough to play", which reads as "fast enough to measure".
> *Consequence if carried forward:* a quarter to a third of interpreter cost attributed to the
> device — larger than any optimisation you would then attempt.

**(b) The device's own frequency policy is capping the big cores.** A vendor performance or
game-management policy can pin them to the little cores' maximum with no battery saver active and no
thermal event, and application code cannot lift it. Compare each CPU policy's *administrative*
maximum against its *hardware* maximum before concluding anything. **(B)** Measured: the big
cluster's policy maximum equalled the little cluster's hardware maximum, against a hardware maximum
51 % higher, low-power flag off, battery at 34.7 °C. Uncorrected, an administratively throttled
device reads as an architecturally too-slow one and the port gets redesigned around a constraint that
does not exist.

**(c) Single measurements are worthless.** **(C)** One reference *host machine* produced intermittent
25 %-faster outliers — a property of that machine, not of anything portable. Interleave runs, take the
median, and record which build and clock state produced each figure: the reference port's build-profile
note reads "106.5 vs 102.2 M inst/s, median of three interleaved runs", which is what makes a 4 %
claim readable at all.

## 14. Read your own vendored fork first

**(A)** If your tree vendors a fork of a windowing, surface or audio crate through cargo
`[patch.crates-io]`, **read the vendored source before suspecting any dependency's hot path.** A
patched dependency and its upstream are different programs, and upstream is the one your search
engine, your documentation habit and your editor's "go to definition" all hand you.

> **REFUTED.** *Previously believed, mid-session:* the presentation layer had two per-frame costs that
> made it unusable and had to be replaced wholesale — a full-window zeroed allocation on every buffer
> acquisition (**(B)** about 10 MB per frame at phone resolution) and a per-byte channel swizzle
> (**(B)** roughly 10 M single-byte stores per frame). Both were read straight out of the dependency's
> published source; upstream even carried its own TODO on the second.
> *Refuted by:* opening the repository's own `[patch.crates-io]` table. The vendored fork had already
> fixed both, so the planned work would have produced a functional duplicate of code the project
> already owned. *Cost:* about half a day.
> *Consequence if carried forward:* a second implementation of something you own, and a diagnosis
> that measures code you are not running.

**(A)** Two habits stop the recurrence: make "check the patch table" step one of any render-path
diagnosis, and keep the **why** of each fork in a comment *in the patch table*, where a reader of the
manifest sees it. Setup-side vendoring rules:
[vendoring.md](../../emulator-core/reference/vendoring.md).

## 15. Symptom table

| symptom | cause | first move |
|---|---|---|
| An older phone gets hot after ~10 min; the flagship never complains | Sustained load, not throughput: the pixel pipeline repaints identical frames, the synth runs effects while silent | Decompose into three axes (§2), ship the frame skip (§4) |
| A static screen burns as much host CPU as gameplay | The guest repaints on its timer regardless, and every repaint rode the full pipeline | Hash raw guest bytes before conversion; skip identical frames (§4) |
| Capping fps to reduce heat makes the game run in slow motion | On this class of title the frame rate **is** the game speed, often the player's own setting | Never cap fps; reduce per-frame host cost (§3) |
| Zero skipped frames on a static screen | The skip gate is dead, or an unhashed path is presenting | Hash raw guest bytes before conversion (§4, §8) |
| Half-resolution mode leaves touch controls misaligned | Touch divided in more than one place, or layout stored in pixels | Divide at exactly one input entry point; verify fractional placement (§5) |
| Controls stop responding on a slow phone, battery drains in the pocket | The event poll only yields when its queue is empty, so an overdue re-armed timer never lets the worker loop run | Cap consecutive timer callbacks per poll and yield (§9) |
| A player reports no sound at all; your devices are fine | A single transient device-open failure disabled the pipeline for the session; with no media stream the rocker moves the ringer | Retry forever with queue draining; route the rocker to media (§9) |
| Performance numbers do not reproduce; one run is 25 % faster | Debug artifact, a vendor frequency cap, or host outliers | Package flags, policy max vs hardware max, median of interleaved runs (§13) |
| Half a day optimising a dependency that turns out already fixed | The tree vendors a fork through `[patch.crates-io]`, invisible to a normal read | Read the patch table first (§14) |

## 16. Checklist

- [ ] Sustained load is tracked as its own class, separate from frame rate.
- [ ] Host cost decomposed into interpreter / pixel pipeline / synth, measured separately and **per
      screen class**.
- [ ] No frame-rate cap; any clamp on the guest's timer has a floor and a runtime escape hatch.
- [ ] Identical-frame skip on by default: hashes **raw** guest bytes **before** conversion, counts the
      frame **before** the skip, and no unhashed path consults the hash.
- [ ] Half-resolution render only after confirming fractional layout; touch divided at exactly one
      entry point; the guest's logical resolution untouched.
- [ ] Output-changing mitigations default **off**, on one user-visible switch, audio preset included.
      No silent device tiers.
- [ ] Frames sent/s, skipped/s and the render divisor reported as interval rates with a reset, and
      present in the on-device report.
- [ ] No give-up path on platform resource acquisition; retries drain their queues; the volume rocker
      is routed to the media stream.
- [ ] Every performance figure records build profile, device and clock state; medians, not samples.
- [ ] Every finding says whether it was measured headless or on device.
- [ ] The `[patch.crates-io]` table was read before any dependency was blamed.
