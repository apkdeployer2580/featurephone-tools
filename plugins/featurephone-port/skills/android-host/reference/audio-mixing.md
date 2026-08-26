# Audio on the host: two buses, one mixer, and who gives way

Everything between the core's audio subsystem and the platform's output device: mixer topology, the
master limiter, ducking the music while an effect sounds, and the level setting a player can reach.
The topology lifts unchanged to any port. **Every number here is a measurement of one title's assets
on one class of handset** — copying the constants unmeasured reproduces the exact imbalance the
reference port shipped and had reported to it three different ways.

Tags: **(A)** generic · **(B)** worked example from the reference port, re-derive it ·
**(C)** do not copy. `⚠` marks anything inferred.

## Contents

- [0. Where this lives, and its other half in the core](#0-where-this-lives-and-its-other-half-in-the-core)
- [1. The signal chain](#1-the-signal-chain)
- [2. Why there is an intermediate bus at all](#2-why-there-is-an-intermediate-bus-at-all)
- [3. The last stage must be an infinite source](#3-the-last-stage-must-be-an-infinite-source)
- [4. The synth's default output gain is a desktop safety margin](#4-the-synths-default-output-gain-is-a-desktop-safety-margin)
- [5. Ceilings you may lower; settings you may not](#5-ceilings-you-may-lower-settings-you-may-not)
- [6. The synth burns CPU in silence](#6-the-synth-burns-cpu-in-silence)
- [7. PCM one-shots are concurrent mixer inputs](#7-pcm-one-shots-are-concurrent-mixer-inputs)
- [8. ★ A static limiter on the SUM starves whichever bus arrives last](#8--a-static-limiter-on-the-sum-starves-whichever-bus-arrives-last)
- [9. Ducking: decide who gives way](#9-ducking-decide-who-gives-way)
- [10. Duck state across two audio threads](#10-duck-state-across-two-audio-threads)
- [11. The per-device level row](#11-the-per-device-level-row)
- [12. The ceiling when the source material is brickwalled](#12-the-ceiling-when-the-source-material-is-brickwalled)
- [13. The guest's slider and the host's trim are different knobs](#13-the-guests-slider-and-the-hosts-trim-are-different-knobs)
- [14. Pin the volume rocker to the media stream](#14-pin-the-volume-rocker-to-the-media-stream)
- [15. Opening the output device: retry forever](#15-opening-the-output-device-retry-forever)
- [16. Audio must self-report](#16-audio-must-self-report)
- [17. The background gate: freeze, do not stop](#17-the-background-gate-freeze-do-not-stop)
- [18. Sound counters belong on the hitch line](#18-sound-counters-belong-on-the-hitch-line)
- [19. ★ A hitch when a sound plays is usually not in the audio code](#19--a-hitch-when-a-sound-plays-is-usually-not-in-the-audio-code)
- [20. Verifying audio without ears](#20-verifying-audio-without-ears)
- [21. Symptom table](#21-symptom-table)
- [22. Checklist](#22-checklist)

---

## 0. Where this lives, and its other half in the core

**(A)** Audio splits at the sink trait, and the two halves fail differently. Read
[../../emulator-core/reference/audio-core.md](../../emulator-core/reference/audio-core.md) first —
it owns the sound container and **which bus a file belongs to** (classified from chunk tags at parse
time, because both kinds ship under one extension and one set of API calls); **the one volume law**
shared by both buses, with the prohibition on applying a level inside the synthesis domain and the
capability probe that lets a sink declare it took the level; clip identity, the parse cache and
handle lifetime; the guest's **single sound slot** and the deferred-stop machine; and the sequencer's
wall-clock catch-up rebase. Below is host-side only.

**(A) One boundary rule.** A negative claim about your title's audio ("it has no PCM effects",
"nothing stops the music") written from a device trace is an observation gap, not evidence — the
reference port shipped both of those and both were false. Write "not observed in *this named
scenario*" ([evidence-standard.md](../../../shared/evidence-standard.md#6-behaviour-is-settled-dynamically-never-by-reading-disassembly)).

## 1. The signal chain

```
core: note events  --channel-->  synth source (infinite, render-thread-owned) --.
                                                                                >-- bus (mixer)
core: parsed PCM clip -----------> one concurrent mixer input per playback -----'      |
                                                          master limiter (infinite) <--'
                                                                    |
                                                                    v  platform output device
```

**(A)** Rules that travel with it (ref: `<app-crate>/src/audio_sink.rs`):

- **The synth is handed to the render thread once and driven by messages.** No mutex on the render
  path; an underrun is instant and audible.
- **A level is not an event.** Levels — music gain, effect trim, duck counter — are plain atomics read
  once per render chunk: only the newest value matters, and a channel would make you reason about
  queue depth. Note-ons *are* events and do go through the channel.
- **PCM clips are mixer inputs, not a queue** (§7). **The last stage is infinite** (§3).

## 2. Why there is an intermediate bus at all

**(A)** The platform output **hard-clamps** float PCM, and a clamp is not a mix: inside every clipped
sample the quiet signal riding on the loud one is *deleted*, not attenuated. Add the synth and each
one-shot straight to the device mixer and a full-scale effect erases the music for its whole
duration — with no stage anywhere that can see the sum and act on it.

Sum into **your own** bus, limit that sum, hand the limiter to the device. That one indirection is
what makes §8's diagnosis possible and §9's fix implementable, for one buffer copy per chunk.

## 3. The last stage must be an infinite source

**(A)** A mixer **drops any input that reports end-of-stream**, so the stage you hand to the output
device must be *infinite* — yielding silence when its own input is exhausted. Otherwise one exhausted
moment detaches the whole chain and the session is silent with no way back.

Both exhaustion paths are startup-order or failure-path events, so no test catches them: the
instrument bank fails to load and the bus momentarily holds no inputs, or the synth source is
rendered before the first note arrives. **PCM must keep working when the synth fails**, which only
holds if the master stage survives an empty bus (§16 reports that state explicitly). Give both the
synth source and the limiter no total duration, and have the limiter substitute zero when the bus
yields nothing (ref: `<app-crate>/src/audio_sink.rs`, `MasterLimiter`).

## 4. The synth's default output gain is a desktop safety margin

**(A)** A general-purpose software synth's default output gain exists to keep arbitrary
hundred-voice music from clipping. It is not a mix decision about *your* content, and it survives
review because it looks deliberate.

**(A) Method.** Render every music file offline at unity, print peak and RMS per file, set the bus
gain from that table. Let the one file that exceeds unity be the limiter's problem; do not lower the
gain again for it.

**(B)** The reference port's bundled synth inherited a default of `0.2` from its ancestor project.
That table gave peaks of **0.45 / 0.49 / 0.66 / 1.18** across four songs — the default was discarding
roughly **14 dB** the mix never needed to give up, which is most of why players ran the phone at
maximum. Changed to `1.0` (ref: `<app-crate>/src/audio_mix.rs`, `SYNTH_GAIN`, whose comment carries
that table). First thing to measure for "too quiet even at maximum"; §14 is the second.

## 5. Ceilings you may lower; settings you may not

**(A)** Split the synth's cost knobs in two:

| kind | example | rule |
|---|---|---|
| **worst-case ceiling** | polyphony cap | lower by default; at your content's density it changes nothing |
| **audible setting** | reverb, chorus, interpolation order | opt-in switch, defaulting to previous behaviour |

A lazily grown voice pool makes a polyphony cap a **ceiling**: below it, output is bit-identical.
**(B)** 256 → 64 gave identical output at the title's measured density (~12 note-ons/second over 16
channels), removed the pathological case, and quartered the pool's memory bound; synthesised at 200
notes/second the cost was **8.9 % of a core at 256 versus 2.5 % at 64**
(ref: `<app-crate>/src/audio_sink.rs`, `POLYPHONY`).

⛔ **Never auto-detect a device tier for the audible settings** — the rule is *the phone that already
works must sound the same*. Higher-order interpolation is charged **per active voice** (about half the
synth's DSP time, by its own documentation), so it scales with exactly the moments a slow device is
already losing.

## 6. The synth burns CPU in silence

**(A)** Reverb and chorus are typically computed unconditionally rather than gated on active voice
count, so the synth costs a permanent slice of a core **on a static screen**. Put it on the
sustained-load list before decomposing a heat report into interpreter and pixel pipeline
([sustained-load.md](sustained-load.md)). **(B)** Per-thread measurement on device put the audio
callback thread at **13.3 % of a core at the title screen** on a 2021-class phone, and higher on a
2018-class one — so a thermal mode should **imply** the reduced synth preset of §5, while the
standalone switch keeps working alone.

## 7. PCM one-shots are concurrent mixer inputs

**(A)** Add each playback as **its own concurrent mixer input**. A sequential player queue backlogs
rapid effects, so they arrive late and keep draining after the action that caused them ended — a
symptom that reads as a performance problem and invites frame-budget work, when it is one line of
topology. **Overlapping effects are the game's own behaviour, not a defect to serialise away**; the
corollary — freeing a clip must not stop an in-flight playback — lives in
[audio-core.md](../../emulator-core/reference/audio-core.md).

## 8. ★ A static limiter on the SUM starves whichever bus arrives last

**(A)** A limiter or waveshaper on the **sum** has a property nobody chose: **whatever is added last
gives way.** Once the bed signal alone is past the knee, a transient mixed on top is not mixed in —
it is compressed away. And the player who reports it *arrived* at that operating point deliberately:
a quiet handset makes them raise every slider.

**(A) Method** — arithmetic on measured peaks, no listening required. Take the §4 peak table, put both
in-game sliders at maximum, compute the delivered level of one effect over each song's peak.

**(B)** The reference port's table, both sliders at maximum:

| music peak | effect delivered | note |
|---|---|---|
| 0.42 | **±0.0 dB** | default settings — the limiter never engages |
| 0.66 | **−5.9 dB** | |
| 1.18 | **−20.2 dB** | the loudest song in the title — effectively erased |

The effect bus was not quiet, it was being **erased** — a song-and-settings-shaped mechanism wearing
a device-shaped symptom. No global trim can fix it, and **two reports that contradict each other,
"effects too loud" from one handset and "too quiet" from another, are the signature of this fault
rather than of a level.**

> **REFUTED.**
> *Previously documented:* the sum limiter's compression of effects was written up correctly one
> session earlier and dismissed in the same paragraph as "probably not the cause of this report".
> *Refuted by:* recomputing the delivered effect level per song from the §4 peak table, at the slider
> positions the reporter actually used.
> *Actual mechanism:* the fault only bites above the knee — absent at default settings, total at
> maximum. A developer testing at default settings watches a limiter that never engages and concludes
> it is not involved.
> *Consequence if carried forward:* the next round is spent tuning an effect-bus level, which cannot
> work, because the trim is applied *before* the stage deleting the signal.

## 9. Ducking: decide who gives way

**(A)** The fix is not a level. **Duck the music bus while an effect sounds:** attack short enough to
be in place before the effect's own transient, release long enough that a run of effects does not
pump. Keep the sum limiter — it becomes a rare-peak safety net instead of an always-engaged stage.

**(B)** Duck gain `0.5` (−6 dB), attack **12 ms**, release **140 ms** (ref:
`<app-crate>/src/audio_mix.rs`). The §8 arithmetic re-run after the change: at music peak 0.42 the
effect stays at **±0.0 dB — bit-unchanged**; at 0.66, −5.9 → **−0.1 dB**; at 1.18, −20.2 →
**−3.8 dB**.

Three arguments to carry across, all **(A)**:

1. ⛔ **Do not go deeper.** 0.4 and 0.3 were computed: they buy the effect a further 2.3 dB and
   3.6 dB, and at that depth *the music dip becomes the thing the player notices*. A duck you can
   hear as a duck is a worse bug than the one you fixed.
2. **The assurance argument is what makes it shippable:** a player at default settings hears
   bit-identical output, because the limiter was idle there. Without that property you cannot ship a
   mix change to a device someone already tuned by ear.
3. **Ducking is closer to the original design than not ducking** — the guest itself silences the music
   completely in order to play an effect (the single-slot behaviour the core deliberately refuses to
   reproduce). You are implementing a gentler version of the designer's intent.

## 10. Duck state across two audio threads

**(A)** The duck is written by the clip-submitting thread and consumed by the render thread. **One
atomic counter is the whole shared state**, and four of its properties are each a real bug.

- **Count FRAMES, not milliseconds.** The render thread has no clock and legitimately *stops
  consuming* (§17) while a wall clock does not, so a millisecond deadline drifts against a stalled
  synth and parks the music at the ducked gain. The submitting side converts the clip's own length —
  whose sample rate is usually **not** the bus rate — into **bus** frames.
- **Combine with `fetch_max`, never a store.** Overlapping effects must **extend** a duck and must
  never shorten it. A store is the "music dies early when two effects overlap" bug.
- **The render thread subtracts one chunk per cycle** (`fetch_update`), clamping the per-chunk amount
  so it cannot go negative.
- **Guard the degenerate rate.** Envelope arithmetic is a dependency-free pure function; a rate of 0
  or 1 must return the whole span (the envelope jumps) rather than dividing. A NaN or infinity in the
  audio callback is a **permanent full-scale fault**, not a glitch.

⛔ **Run the release on frames where the owed duck is zero** — that *is* the release. Stepping the
envelope only on frames that still owe a duck parks the music at the ducked gain after the last
effect: "the music has been quiet ever since I used a skill".

⛔ **Clamp at both ends:** the envelope must neither undershoot the target nor drift *above* unity and
become an amplifier. Both directions are cheap to assert over a large iteration count (§20).

⛔ **Reset on background entry** — envelope to unity, counter to zero, before returning the silent
chunk (§17). The general rule, which [thread-model.md](thread-model.md#4-the-two-atomics) also
carries: **any time-based state consumed by a thread that can stop consuming needs an explicit reset
at the stop boundary.**

## 11. The per-device level row

**(A) One global trim constant cannot serve two handsets** — the same dB of headroom is not the same
experience on two devices — **so the number the ear picks must be a setting the ear can change.**

**(B)** The structural proof that no third global number would have helped: with one square volume law
serving both buses and only the effect bus additionally eating the trim, that trim is a **fixed offset
at every slider step** (−8.0 dB in the reference port). There is no slider position at which one
number is right for both reporters.

> **REFUTED.**
> *Previously documented:* the effect bus needed a global trim, and −8.0 dB was the right value —
> derived honestly from a real report that effects were too loud, and verified by ear on that device.
> *Refuted by:* a second handset, on which the same build's effects were inaudible; then §8, which
> showed part of that report was not a level at all.
> *Actual mechanism:* "tuned by ear on one device" produces a number correct for one device, and a
> fixed dB offset cannot express a per-device difference.
> *Consequence if carried forward:* you iterate the constant a third time, move the complaint to a
> third phone, and regress the already-tuned device on every iteration.

**(A) The shape that is safe to ship:**

- A few steps at fixed dB spacing — **(B)** five steps 3 dB apart, *centred on the previously shipped
  value*.
- **The default is bit-identical to the shipped constant.** That is the whole trick: a player who never
  opens the row hears exactly what shipped, so the tuned device cannot regress and needs no
  re-verification.
- **One line on disk** ([storage-and-config.md](storage-and-config.md)), **read at the point of use in
  the audio path**, so it applies to the *next* sound with no restart.
- **An out-of-range stored value falls back to the default** — never to silence, never to full scale.
- Selftests pin: default equals the shipped constant, the dB spacing, the ceiling (§12), the
  out-of-range fallback, and that cycling the row reaches every step.

## 12. The ceiling when the source material is brickwalled

**(A)** Do not offer unity as the top step of an effect-bus control before counting how many of your
effect assets are already brickwalled. **Method:** decode every effect sample, print its peak.

**(B)** **4 of the title's 14 effect samples decode to a literal 0 dBFS peak**, the loudest at
−3.9 dBFS RMS. At unity those hand the limiter a full-scale source and the music underneath starts
disappearing — §8's symptom, straight back. The top step was set to `0.8` (about −1.9 dB), with a
selftest asserting both that the top step is below unity and that a 0 dBFS sample at that step stays
near the knee (ref: `<app-crate>/src/audio_mix.rs`). **A level control whose top step re-creates the
bug it was added to fix is worse than no control.** Transferable: the measurement and the assertion,
not `0.8`.

## 13. The guest's slider and the host's trim are different knobs

**(A)** The guest's in-game settings mean *"relative level, as the designer exposed it"*. A host bus
trim means *"this device's headroom"*. **Never fake one with the other.** The guest's numbers flow
through unchanged into the core's shared law; rewriting the level the guest asked for makes the game's
own settings screen disagree with what the player hears and leaves any later real fix with two
overlapping corrections to unpick. It also cannot express a fixed offset (§11).

## 14. Pin the volume rocker to the media stream

**(A)** In the activity's `onCreate`: `setVolumeControlStream(AudioManager.STREAM_MUSIC)`. Without it
the volume keys control whichever stream the system considers active — for a game that is often
silent, the **ringer** — so the player presses volume-up, watches a ring-volume slider hit maximum,
and reports the game as quiet at full volume. This is one of the very few legitimate additions to a
Java shim that [platform-glue.md](platform-glue.md#1-the-java-side-is-tiny-and-must-stay-tiny)
otherwise tells you not to grow: a platform *declaration* with no native equivalent, same category as
cutout handling and system-bar hiding, not logic.

**(A)** It also disambiguates two reports with identical wording: with **no** active media stream —
what a failed audio open (§15) leaves you — the keys do not grab media volume at all, so "the volume
control is unresponsive" is a symptom of a failed bring-up, not of a missing call.

## 15. Opening the output device: retry forever

**(A)** **Retry the open forever.** One failed open — a cold-boot race, another app briefly holding the
device, a Bluetooth route switching — otherwise becomes a **whole session of silence**, and the failure
is total, silent and unreproducible on your own hardware. "Give up permanently on an init failure" is
the default shape of every audio bring-up. Shipped shape: fast retries first for transient races, then
a slow patient loop, **draining both event queues on every attempt** so a long retry does not
accumulate a backlog that all fires at once when the device opens (ref:
`<app-crate>/src/audio_sink.rs`). The retry is also what makes headphone and Bluetooth route changes
survivable.

**(B)** The report behind it: a remote tester's handset, well above the app's minimum API level (chosen
to match the audio backend's own floor), was **completely silent** and unreproducible on any device the
developer owned. A compatibility audit ruled out API level and ABI and left "this device's audio init
failed" as the only hypothesis — with **no way to confirm it**, correctly recorded as the real defect.
Hence §16.

## 16. Audio must self-report

**(A)** Audio is the subsystem whose failure produces no visible artefact, so it is the one that most
needs to describe itself. Write **one human-readable status string** at every bring-up stage and
surface it on the device; the people who hit audio failures cannot attach a debugger. **(B)** The four
states, in shape (ref: `<app-crate>/src/audio_sink.rs`): `opening…` ·
`output open FAILED xN: <error>` · `output open, but synth load FAILED: <error> (music off, effects
on)` · `OK (output open, synth loaded)`. That turns every "no sound" report into a **three-way
classification**, which is the entire value:

| status | meaning | next step |
|---|---|---|
| output open failed | the device never opened | chase the error code; the §15 retry is already running |
| synth load failed | music off, effects on (§3 keeps PCM alive) | instrument bank missing or unreadable |
| OK, and still silent | the chain is fine | the game's own sound setting, or system media volume (§14) |

Put it in an on-device diagnostics dialog with a copy button
([on-device-diagnostics.md](../../port-shipping/reference/on-device-diagnostics.md)). **Redact by
default:** a diagnostics panel gets pasted into chat logs, so it carries subsystem status, never
endpoints, identifiers or save contents.

## 17. The background gate: freeze, do not stop

**(A)** The render source reads the shared `paused` flag **once per render chunk** and, when set,
returns a **silent chunk without advancing the synth and without draining queued events**, leaving the
output device open.

> **REFUTED.**
> *Previously documented:* "Stop everything on the paused gate. Audio continuing in a pocket is the
> most-reported complaint a port can have." — this plugin's own
> [platform-glue.md](platform-glue.md) §6.
> *Refuted by:* §15. Stopping the sink requires a **re-open** on resume, and re-opening is precisely
> the operation that fails on the devices that had trouble opening in the first place; it also loses
> the song position and strands every envelope.
> *Actual mechanism:* the goal — no sound in a pocket — is met by rendering silence. Nothing
> accumulates while paused because the emulator worker is paused in lock-step, so held notes freeze
> and resume seamlessly.
> *Consequence if carried forward:* the music restarts from the beginning on every unlock, and on some
> handsets the audio never comes back at all.

Two consequences elsewhere in the host: **the `paused` flag has a third consumer**, handed to the audio
thread at construction ([thread-model.md](thread-model.md#4-the-two-atomics)) — the worker pausing
stops *new* sound events, this branch stops the *current* sound; and **this branch is where time-based
envelope state is reset** (§10), precisely because the render thread is the thing that stops consuming
it.

## 18. Sound counters belong on the hitch line

**(A)** Whatever line your host prints when a tick overruns its budget, add **plays and parses since
the previous sample**. Audio-caused hitches are otherwise attributed to the interpreter, which is where
a port already spends its optimisation budget. **(B)** The reference port's hitch report carries guest
instructions, supervisor calls, blit counts and refusal reasons, allocations **and** sound plays/parses
for the overrunning tick, with a reading guide in its own comment — *"instructions small with a large
parse count = the sound path"* (ref: `<app-crate>/src/emulator.rs`, `report_hitch`). One counter pair on
an existing diagnostic line ends a whole class of misattribution, and this port needed it twice (§19).

## 19. ★ A hitch when a sound plays is usually not in the audio code

**(A)** **Measure the whole per-sound chain before touching the audio code.** Two independent
investigations in the reference port both landed outside it.

> **REFUTED.**
> *Previously documented:* the container parser was the obvious suspect — it walks chunks, builds an
> event vector and sorts it, inside the guest's play call, on the emulator thread, inside a frame.
> *Refuted by:* a benchmark over all 33 of the title's sound files: **0.016–0.049 ms per effect**,
> 0.7 ms for a music file played once. Cleared with numbers, and the numbers were written down so
> nobody re-suspects it.
> *Actual mechanism:* the tax was elsewhere, twice. (1) A synchronous platform service call (vibration)
> issued **inline on the emulator thread** —
> [platform-glue.md](platform-glue.md#5-haptics-on-a-dedicated-always-attached-thread). (2) The **archive
> resource path**: the guest re-reads each sound from the archive, each resource-stream call internally
> rebuilt the whole zip object **three times**, and the guest makes two such calls per sound — **six full
> archive copies plus central-directory parses per sound**, **3.9 ms** on a desktop x86 host inside a
> 24 ms budget, paid twice in a frame that plays two sounds.
> *Consequence if carried forward:* you optimise a decoder that costs 0.03 ms and the hitch stays.

**(A)** The fix for (2) is a **process-wide immutable name → bytes cache** with a size ceiling, so the
worst case degrades to previous behaviour rather than to unbounded memory. **Key it by (application id,
resource name)** — without the application id, two emulator instances in one process read each other's
resources.

## 20. Verifying audio without ears

**(A)** Nothing in the toolchain can hear. Four things automate; one does not.

1. **Offline renders and a peak/RMS table, per bus, at the same in-game setting** — this produces §4, §8,
   §9 and §12 and is the exit criterion that transfers. **The deliverable is a constancy, not a number:**
   state the gap between the buses and check it is the **same at every slider step**. **(B)** Before one
   shared volume law the reference port measured the buses **27–39 dB** apart at one setting; after,
   **4.6 dB** and constant. Once constant, any residual imbalance is a single number you can tune (§11);
   while it varies, every fix moves the problem to a different slider position.
2. **Pure-arithmetic selftests over the mixing module.** Keep envelope and level maths dependency-free so
   a standalone test can `include!` the real source: clamping in both directions over a large iteration
   count, the release-at-zero path, recovery to unity, default-equals-shipped, step spacing, the ceiling,
   out-of-range fallback, and *output unchanged at default settings* — §9's assurance argument, asserted.
3. **A pinned mirror where `include!` is impossible** — the standard case being one law split across core
   and host, the core's half living in a vendored `no_std` crate beside the trait it serves. Do not accept
   an unverified mirror: have the test **read the real source file**, split on the function signature,
   whitespace-normalise the body and assert equality against a literal, with a failure message naming what
   to update. Drift becomes a build failure instead of a test that stays green while the shipping constant
   moves ([selftest-convention.md](../../../shared/selftest-convention.md)).
4. **There is no automated black-screen equivalent for audio** — §16's status string *is* the automated
   check for "is the chain alive".

⚠ **Then a human listens, on a real device.** Every round of the reference port's audio work ended on a
listening confirmation, and its last outstanding audio item is a listening test on a third handset — so
treat "a person said yes" as a required exit criterion for the audio step of your build order.

## 21. Symptom table

| Symptom | Cause | § |
|---|---|---|
| One tester's device has no sound at all; unreproducible on yours | a single failed output-device open treated as permanent, with no self-report | §15, §16 |
| "Quiet even at maximum volume", and the volume keys seem to do nothing | synth default output gain; and/or the rocker not pinned to the media stream | §4, §14 |
| Effects inaudible only during certain songs, or only with every slider raised | a static limiter on the sum starves the later-added bus | §8 |
| Two handsets report the opposite — "too loud" and "too quiet" | the same fault; it is not a level | §8, §11 |
| Changing the trim fixes one phone and regresses the other | a global constant chosen by ear; make it a setting whose default is the constant | §11 |
| Effects get louder but the music starts disappearing | the control's top step is unity over brickwalled source material | §12 |
| The music dip is audible as a dip | duck depth too deep; −6 dB is about the limit | §9 |
| The music dies early when two effects overlap | duck state combined with a store instead of `fetch_max` | §10 |
| The music stays quiet after the last effect | the release only runs on frames that owe a duck | §10 |
| The music stays quiet after returning from background | duck state in milliseconds, or not reset in the paused branch | §10, §17 |
| A permanent full-scale fault | a divide by a zero sample rate put NaN/inf in the callback | §10 |
| Audio worked, then stopped forever, with no error | a finite master stage was dropped when its input exhausted | §3 |
| Music silent but effects fine | instrument bank failed to load; read the status string | §3, §16 |
| A full-scale effect erases the music while it plays | sources added to the device mixer with no intermediate bus | §2 |
| Rapid effects arrive late and keep draining | one-shots appended to a sequential queue | §7 |
| Music restarts from the beginning on unlock, or the device fails to re-open | the paused gate stops the sink instead of freezing it | §17 |
| Frame hitch every time a sound plays | the resource path or a synchronous platform call, not the decoder | §19 |
| Device runs hot on a static screen | the synth computes reverb/chorus unconditionally | §6 |
| The in-game slider and what the player hears stop corresponding | the host faked a trim by rewriting the guest's level | §13 |
| Music cuts out and **starts over from the beginning** when an effect plays | the guest's single sound slot — core-side | [audio-core.md](../../emulator-core/reference/audio-core.md) |
| Music plays over a menu that should be silent | guest stops deferred *and then ignored* — core-side | [audio-core.md](../../emulator-core/reference/audio-core.md) |
| Burst of noise plus a frame spike on every resume | sequencer backlog on a wall clock — core-side | [audio-core.md](../../emulator-core/reference/audio-core.md) |

## 22. Checklist

- [ ] Sources sum into your own bus; a limiter sees the sum before the platform output; the last stage is
      **infinite** and PCM survives a failed instrument-bank load.
- [ ] Each PCM one-shot is its own concurrent mixer input; levels are atomics, events are messages.
- [ ] Every music file rendered offline at unity and every effect sample's peak counted; the bus gain
      comes **from that table**, and the level control's top step leaves the limiter headroom.
- [ ] The delivered effect level computed over each song's peak at maximum sliders — if it falls on the
      loud songs, you have the §8 fault.
- [ ] Music ducks while an effect sounds, no deeper than about −6 dB, and a default-settings mix is
      **bit-unchanged**.
- [ ] Duck state is one atomic counted in **bus frames**, combined with `fetch_max`, released every frame
      including zero, clamped both ends, degenerate rate guarded, reset on background.
- [ ] The effect/music balance is a **setting** whose default is bit-identical to the shipped constant,
      with an out-of-range fallback to that default and no restart needed.
- [ ] The guest's own sliders pass through unchanged; no host trim is faked by rewriting them.
- [ ] `setVolumeControlStream(STREAM_MUSIC)` in `onCreate`; the output open retries forever, draining
      queues per attempt; a status string is written at every stage and shown on the device, redacted.
- [ ] The paused branch **freezes**: silent chunk, synth not advanced, queues not drained, device left
      open, envelopes reset.
- [ ] Sound plays and parses appear on the tick-overrun line, and the whole per-sound chain was measured
      before any audio code was touched.
- [ ] Mixing maths is dependency-free and `include!`d by a selftest; any unavoidable mirror is **pinned to
      the real source text**. A person has listened, at default settings and at maximum.
