# Audio in the core: buses, one volume law, and the guest's single sound slot

Sound is not one subsystem, it is two — a sequenced/MIDI bus and a PCM bus — and a feature-phone
title hides both behind one file extension, one set of API calls and one handle type. This file owns
the core side: how to tell the buses apart, the single volume law they must both obey, and the
arbitration the host needs because these guests typically own **exactly one sound slot**, so "play an
effect" means *stop the music, play the effect, restart the music from bar one*.

Tags: **(A)** generic · **(B)** worked example from the reference port, re-derive it ·
**(C)** do not copy. `⚠` marks anything INFERRED — nothing was run.

## Contents

- [0. Scope, and the boundary with the host sink](#0-scope-and-the-boundary-with-the-host-sink)
- [1. The container: magic + big-endian TLV chunks](#1-the-container-magic--big-endian-tlv-chunks)
- [2. ★ Classify the bus at PARSE time and cache the verdict](#2--classify-the-bus-at-parse-time-and-cache-the-verdict)
- [3. Refutation preserved: negative claims read off a trace](#3-refutation-preserved-negative-claims-read-off-a-trace)
- [4. One volume law, defined once](#4-one-volume-law-defined-once)
- [5. ⛔ Never scale note velocity](#5--never-scale-note-velocity)
- [6. Capability probe: let the sink declare which layer applied the level](#6-capability-probe-let-the-sink-declare-which-layer-applied-the-level)
- [7. Which identifier is stable?](#7-which-identifier-is-stable)
- [8. Handle lifetime](#8-handle-lifetime)
- [9. Parse once, cache by content — and the cache is your identity oracle](#9-parse-once-cache-by-content--and-the-cache-is-your-identity-oracle)
- [10. ★ The guest owns ONE sound slot](#10--the-guest-owns-one-sound-slot)
- [11. Why neither simplification works](#11-why-neither-simplification-works)
- [12. The deferred stop: the four-way decision table](#12-the-deferred-stop-the-four-way-decision-table)
- [13. Deriving the grace and rejoin constants](#13-deriving-the-grace-and-rejoin-constants)
- [14. The gate: four operations, four invariants](#14-the-gate-four-operations-four-invariants)
- [15. Effect duration must include the PCM tail](#15-effect-duration-must-include-the-pcm-tail)
- [16. Sequencer versus wall clock](#16-sequencer-versus-wall-clock)
- [17. "Allocate an additional player" is usually a stub](#17-allocate-an-additional-player-is-usually-a-stub)
- [18. One subsystem below every front-end](#18-one-subsystem-below-every-front-end)
- [19. Symptom table and verification](#19-symptom-table-and-verification)
- [20. Checklist](#20-checklist)

---

## 0. Scope, and the boundary with the host sink

**(A)** Three layers, guest to speaker:

| layer | contents | owner |
|---|---|---|
| front-end | the platform's media API — clip create / put-data / set-volume / play / stop / free | per-carrier front-end crate |
| **shared audio subsystem** | parse, parse cache, handle bookkeeping, the sequencer timeline, the music channel and its deferred-stop gate, **the volume law** | **this file** |
| host sink | mixer bus, master limiter, ducking, the effect-level setting, opening the output device | [audio-mixing.md](../../android-host/reference/audio-mixing.md) |

**The dividing rule (A):** a property of the **file** or of the **guest's calling pattern** belongs in
the shared subsystem, where every front-end and every host gets it for free; a property of the
**device** belongs in the sink. All of it is **upstream-defect work, not title-specific work** — parse
caching, handle lifetime, a clock rebase and a deferred-stop machine are wrong in the core for every
title, exactly like the allocator rows in [vendoring.md](vendoring.md) §7. Budget it that way.

## 1. The container: magic + big-endian TLV chunks

**(A)** The usual shape: a magic word, a big-endian total length, then a run of **4-byte-tagged,
big-endian-length TLV chunks**. A sequenced-score chunk and a PCM-audio chunk are two different tags
at the same level of the same container — which is why the extension cannot tell you which you have.
**Method (A):** write a ~30-line chunk-tag dumper and run it over the **whole** sound directory,
printing filename → tag histogram. Do not sample; the split is the point.

**(B)** In the reference port the two tags are 4-byte ASCII differing **only in their first byte**,
and the dump split the sound directory cleanly into **19 sequenced** and **14 PCM** files — all with
an *identical filename suffix*. Second, independent derivation: a probe trace showed the guest
issuing the **same** create / put-data / set-volume / play sequence for both kinds, differing only in
the repeat flag. `HIGH / CONFIRMED`. The parser must emit three things per file, not one: an **event
list** with times, a **bus verdict** (§2), and a **duration that includes the PCM tail** (§15).

## 2. ★ Classify the bus at PARSE time and cache the verdict

**(A)** Which bus a sound belongs to is a property of the **file**. Settle it once, at parse, and
store the verdict on the parsed object; do not derive it per playback, and **never let a handle, a
clip pointer or a file name carry it** — all three are recycled out from under you (§7). A predicate
that generalises: *"does this file contain any event that is not a wave one-shot or an end marker?"*
→ music. It survives a file carrying both a wave chunk and a trivial score, which a tag-presence test
does not.

**Failure mode if you skip it.** An effect pushes *its* level onto the music bus: with music at step
5 and effects at step 2, every effect ducks the music to 40 % — presenting as a mixing bug, so it
gets fixed in the wrong file. **Cheap verification (A):** log a line whenever a bus level is applied,
then play a scene with effects in it. **(B)** On device the music-bus line printed **once**, at the
game's default setting, and was **not re-printed across 18 subsequent effects** — that is what
"effects never reach the music bus" looks like in a log. `HIGH / CONFIRMED`.

## 3. Refutation preserved: negative claims read off a trace

> **REFUTED.** *Previously documented*, in the reference port's own hand-off document and stated as
> settled fact: **"this title's audio is 100 % MIDI — the PCM play call is never made, sound effects
> are unused, and the PCM path is dead code kept for a sequel."**
> **Refuted by:** a player report, then a chunk dump — **all 14 of the title's sound effects are
> PCM** (§1).
> **Why it was attractive:** it came from a *real device trace* with thousands of sequencer note-ons
> and literally zero PCM-play calls; the trace was captured in a stretch of play where no effect
> fired.
> **Consequence if carried forward:** the effect bus went un-levelled and un-measured for weeks, and
> the diagnosis of the loudest player complaint was invalid the whole time.

> **REFUTED.** The *same* error one session later, one level finer: **"nothing stops the music."**
> **Refuted by:** a media-call trace of a single attack (§10) — the guest stops the music on **every
> effect**.
> **Why it was attractive:** in the probe session behind that note, music and effects never
> overlapped, so a stop was never observed adjacent to a play.
> **Consequence if carried forward:** every diagnosis of "the music cuts out" hunts for a level or
> masking problem, which is a dead end.

**(A) The rule, in bold because it cost weeks twice: never write "this title does not do X" on the
strength of a trace.** Write **"not observed in \<named scenario\>"**, name the scenario, then
*design* the scenario that would trigger X before concluding. Absence in a trace is an observation
gap — see [evidence-standard.md](../../../shared/evidence-standard.md) §6.

## 4. One volume law, defined once

**(A)** Both buses answer to **one function**, in the core, shared by every sink, applied as an
**output gain in linear amplitude**. Anything else means the same in-game setting denotes two
different loudnesses, and no single number anywhere can then fix the imbalance. **Curve it:** with a
linear map almost the whole audible range sits in the top step, while squaring the normalised level
keeps 0 silent and full unity and spreads the middle. **(B)** The guest sends `level * 20` in
`0..=100`; the law clamps at 100 and a unit test exercises all **six** reachable values. Measured dB,
steps 1..5 — **linear** −14 / −8 / −4.4 / −1.9 / 0; **squared** −28 / −16 / −8.9 / −3.9 / 0. The
exponent is a *taper choice*, so it ships with a comment stating the dB table it produces, or the
next person re-tunes it blind.

> **REFUTED.** *Previously documented and shipped*, as the port's volume law: **"music = MIDI note
> velocity × volume/100; effects = linear sample scale."**
> **Refuted by:** offline 20-second renders per bus at the game's **default** setting (both in-game
> sliders on 3, the guest sending the same number to both) — music **−42 to −48 dBFS RMS**, effects
> **−8 to −24 dBFS RMS**: a **27–39 dB gap from one setting**.
> **Why it was attractive:** velocity is the only level knob a naive MIDI path exposes, and each half
> of the law is locally reasonable.
> **Consequence if carried forward:** three separate player complaints with one cause, and every
> per-bus fudge factor moves the problem to a different slider position.

**After one shared law (B):** the gap measured **4.6 dB** and — the actual deliverable — was
**constant across all five slider steps**. ★ Constancy is what you are buying: once both buses obey
one law, "music 3 / effects 3" means one thing and any residual imbalance is a *single* number to
tune, which belongs in a setting rather than a constant
([audio-mixing.md](../../android-host/reference/audio-mixing.md)). **Method (A):** render each bus
offline at the **same** in-game setting, tabulate peak and RMS per step, print the gap — before and
after every change to the law.

## 5. ⛔ Never scale note velocity

**(A)** Velocity is an input to *synthesis*, not a gain. Scaling it runs the level through the
instrument bank's default **concave velocity-to-attenuation modulator** (so the dB drop is roughly
**double** the linear equivalent), **selects sample layers**, and **moves filter cutoff** — a lower
setting becomes **duller as well as quieter**, on a curve unrelated to the PCM bus. **(B)** At step
3: −4.4 dB on the PCM bus against about −8.9 dB *plus* a separate ~14 dB synth output-gain deficit on
the sequenced bus.

**(A) General form: apply the level to the OUTPUT, never inside the synthesis domain.** RMS
measurement alone will not reveal half of this defect, because the timbre change is not in the level
— which is why the prohibition ships as a comment at the definition site, not as a test.

## 6. Capability probe: let the sink declare which layer applied the level

**(A)** When a level can legitimately be applied in two places, do not choose at build time — **ask
the sink and branch on its answer.** A defaulted trait method whose *return value* selects the code
path is cheaper and safer than a feature flag, and it generalises to every host capability the core
cannot see. **(B)** `fn set_music_volume(&self, v: u8) -> bool { false }`: the Android sink returns
`true` because it owns a real music bus and applies the shared law to rendered output, while a sink
that hands events to an **external** system synth (desktop MIDI-out, WebMIDI) keeps the default and
therefore keeps the legacy path with **zero** behaviour change. Without the probe you get a
build-time `cfg`, or the level applied **twice** — squared twice is inaudible at mid settings.

## 7. Which identifier is stable?

**(A)** Whenever you cache state across two API calls, ask *which identifier in this pair survives
the calls between them.* It is frequently not the handle the API hands you: the **data-upload call
mints a fresh handle**, so a handle-keyed volume cache lands the level on the *previous* clip. Key on
the stable **guest clip pointer** and re-bind to the playing handle inside `play` — worked example in
[wipi-c-recipes.md](wipi-c-recipes.md) §8, not restated here. Two prohibitions that fix does not
cover:

- ⛔ **Never read the handle back out of the guest's own clip struct.** The create call hands that
  struct back **uninitialised**; closing whatever integer was sitting in guest memory can silence an
  unrelated clip.
- ⛔ **The pointer is not a permanent identity either.** **(B)** The device trace re-created a clip
  at the *same address* for a completely different sound **within 36 ms**. Pointer-keyed bookkeeping
  must be **updated**, not appended to — and "is this the same sound?" must be answered by content
  identity (§9), never by an address.

## 8. Handle lifetime

**(A)** Nothing in these APIs tells you a handle has died; the only signal is the guest's
**clip-free** call, keyed by pointer.

- **Release the bookkeeping on free.** **(B)** Before that fix every upload minted a handle nothing
  ever removed, and **each handle held its own copy of the sound file** — an unbounded leak on a
  platform that kills backgrounded apps by memory first. `HIGH / CONFIRMED`.
- ⛔ **Do not stop an in-flight playback on free**, and leave a previous handle for the same pointer
  alone. Rapid **overlapping effects are the game's own behaviour**, not a defect; chopping them
  looks like a mixing bug and sends the next investigator to the sink.

Both wrong fixes here are attractive (trust the struct field; stop playback on free) and both are
direct consequences of single-slot juggling (§10) — the same pointer is recycled in milliseconds.

## 9. Parse once, cache by content — and the cache is your identity oracle

**(A)** Parse each sound file **once, at load**, into a shared immutable object — never inside the
`play` call, which runs on the emulator thread inside a frame, where a full chunk walk plus event
vector build and sort is a frame hitch on every sound. **Key the cache by `(length, 64-bit hash)` and
confirm the hit by comparing the actual bytes**: trusting a hash alone trades a *visible* performance
bug for an *invisible* correctness one, and "the wrong sound plays" is the class of bug that gets
blamed on the emulator for a year. **Bound the entry count**, so a guest that synthesises clips
instead of loading them stops being cached rather than growing forever.

★ **The cache you built for performance is your identity oracle.** Two byte-identical files share one
parsed object, so **pointer equality on that object is exact content identity** — precisely the
same/different-song test §12 needs, and the guest's own identifiers (handle, address, name) are all
recycled or absent. **(B)** `Arc::ptr_eq` on the parsed score. **And exonerate the parser with
numbers before optimising it (A):** **(B)** benchmarked over all 33 sound files it cost
**0.016–0.049 ms per effect** and **0.7 ms** for a music file played once — suspected, measured,
cleared, so nobody re-suspects it. The real per-sound tax was elsewhere (§19).

## 10. ★ The guest owns ONE sound slot

**(A)** On a handset with a single sound slot, "play an effect" is compiled into the game as:
**stop the music → reuse the same clip object for the effect → afterwards re-issue the music from the
beginning.** The platform's "allocate an additional player" call is a stub (§17) and the original
handset had one player, so the guest was *written* to time-share.

**Method (A).** Log every media-table call with its arguments and a monotonic timestamp, then play one
attack — the `trace_stub` discipline of [wipi-c-recipes.md](wipi-c-recipes.md) §2 applied to a table
that is already implemented. **(B)** What one attack produced:

```
stop(handle=1)                      ; the music
free
create   -> <clip addr>             ; the SAME address the music had just used
put-data -> handle=2
play(repeat=0)                      ; the effect
      ... 626 ms later ...
create   -> <the same addr>         ; now a music file
put-data -> handle=4
set-volume
play(repeat=1)                      ; the song again, from bar one
```

★ **The giveaway in a player report is "and it starts over from the beginning."** No level, masking or
limiter problem can restart a song, so that phrase closes off a whole family of diagnoses at zero
cost. Note also the reuse of the same clip **address** within milliseconds — that is why the
bookkeeping in §7–§8 is keyed by pointer *and* updated in place. **The conclusion (A): a host with a
real mixer must OWN the music channel and refuse to reproduce this faithfully.** §11–§14 are how.

## 11. Why neither simplification works

> **REFUTED.** *Previously shipped:* **"obey the guest — it asked us to stop the music, so stop the
> music."** Literal fidelity.
> **Refuted by:** play. The music restarted from the top **on every skill cast**.
> **Why it was attractive:** it is the correct emulation of a single-slot handset, and it is what the
> trace says to do.
> **Consequence if carried forward:** the single most-reported audio complaint in the port.

**(A) The opposite simplification fails too: the guest also stops the music *deliberately*** — for
menus, cutscenes and dialogue — and leaves it stopped. **(B)** A device trace of an in-game menu open
produced **stop + free, then 4.5 seconds with no sound call at all** until the menu closed, so "ignore
all stops" plays music over a silence the designer intended. ⛔ Do not simplify to ignoring stops:
that case is **invisible in a combat test**, which is why the prohibition belongs in a comment at the
site and not only in a document. **(A) Therefore: DEFER the stop and interpret it from what arrives
inside the window.** The stop request carries no intent. The window does.

## 12. The deferred stop: the four-way decision table

| what arrives inside the window | what the stop meant | action |
|---|---|---|
| **an EFFECT starts** | the guest is borrowing its one slot | keep the music open, **extend** past the end of that effect, let them overlap |
| **the SAME music restarts** | the guest is rebuilding what is already playing | **cancel the stop AND ignore the restart** — the song never breaks |
| **a DIFFERENT music starts** | the stop was real; the track changed | kill, and start the new song |
| **nothing** | the stop was real (menu, cutscene, dialogue) | honour it at the deadline |

Row 2's same/different test is **content identity from the parse cache** (§9) — not handles, not
addresses, not names. Row 1 lets two sounds overlap, which the original handset could not do; that is
not a fidelity violation, because the alternative is the refuted literal behaviour of §11, and the
host-side argument that ducking is *closer* to the original design (the guest itself silences the
music completely to play an effect) is developed in
[audio-mixing.md](../../android-host/reference/audio-mixing.md).

## 13. Deriving the grace and rejoin constants

**(A)** Both are perceptual trade-offs with a wrong answer on **each** side. **Grace** too short → the
music dies before the effect is recognised as an effect; too long → a genuine silence starts audibly
late. **Rejoin slack** too short → the song does not survive the gap between the effect ending and the
guest re-issuing the music. **Measure your own guest** with the timestamped media log of §10:
(i) stop → effect play, (ii) effect end → music re-issue.

**(B)** Measured **37 ms** and **626 / 800 ms**; shipped **grace 250 ms, rejoin 700 ms**. **(C) Do not
copy 250/700** — on a slower guest you get the short-side failure, silently. **(A) The transferable
artefact is the pair of measurements plus a regression test that drives the state machine with those
exact timestamps**, so the constant and its justification cannot drift apart. Write symptom +
measurement + date at the definition site
([evidence-standard.md](../../../shared/evidence-standard.md) §5).

## 14. The gate: four operations, four invariants

**(A)** Express the deferral as **atomics on a shared gate object** the playback task polls between
events — not as a lock on the render path. Four operations: **arm(deadline)**, **cancel**,
**extend(deadline)**, **hard kill**. Four invariants, each a real audible bug, each cheap to state,
each **impossible to rediscover from the symptom**, and each pinned by a dedicated test:

| invariant | the bug when it is violated |
|---|---|
| `extend` must never **invent** a deadline where no stop is pending | "an effect alone stops the music" |
| `extend` must never pull a deadline **inward** | "the music dies early when two effects overlap" |
| a hard kill **outranks** a later cancel | "the old song plays under the new one" |
| the playback loop **re-reads the clock after every sleep** | a deadline that came due while sleeping is missed |

## 15. Effect duration must include the PCM tail

**(A)** An event time is when a sound **starts**; the audible length of a one-shot is *event time +
sample length*, and guests routinely issue a stop long before the sound has finished — **(B)** one was
observed calling stop **84 ms** after starting an effect that was still clearly audible. Compute at
parse time: `duration = max over events of ( event_time + frames / channels / rate )` for a wave event
and `event_time` otherwise, with **divide-by-zero guards on channel count and sample rate**;
**(B)** pinned by three unit tests including a zero-rate case.

**Failure mode.** Taking duration as the *last event time* collapses the hold-open window to ~0 for
exactly the sounds it exists to cover, so the music dies mid-effect — which looks like the grace
constant being too small and **cannot be fixed by enlarging it**.

## 16. Sequencer versus wall clock

**(A)** A playback loop that sleeps only when the next event is in the future will, after the host
stops ticking the emulator (screen off, background), dispatch its **entire backlog inside one poll** —
on the emulator thread, in a single frame. Symptom: a burst of noise plus a frame spike on every
resume, which nobody attributes to the sequencer. **Rebase:** when the deficit passes a threshold,
advance the start time by **exactly the deficit**, so the current event is due now and every later
event keeps its original spacing. That is what a real sequencer does with a stalled clock; skipping
ahead silently and replaying the backlog are both audible.

**Deriving the threshold (A).** It separates *ordinary* jitter between playback polls from *the host
stopped ticking*, so measure the poll-to-poll interval with the app in the foreground under load — a
hitching frame is the worst ordinary case — and set it just above that. Both sides are wrong out loud:
below ordinary jitter it rebases on every hitch and the song's spacing drifts; above a real stall it
never fires and the resume burst of §19's symptom table comes back. **(B)** The reference port shipped
**250 ms**. **(C) Do not copy it** — it is a property of your host's poll cadence, and it is **not**
the same 250 as §13's grace, which is a different quantity that happens to share a value.

⛔ **Compute elapsed time with saturating subtraction.** A wall clock can step *backwards* (an NTP
correction), and a raw instant difference underflows into an astronomically large "elapsed" that
dispatches the whole song at once. Related: [virtual-clock.md](virtual-clock.md) for the clock the
core hands out, and [thread-model.md](../../android-host/reference/thread-model.md) for the paused
flag, whose consumers include the audio render source.

## 17. "Allocate an additional player" is usually a stub

**(A)** Upstream typically leaves the media call that allocates an *extra* player or channel as a
stub, and that stub is load-bearing in one direction: while it reports no additional player, the guest
stays on the single-slot path the original handset exercised — the path §10–§14 model.

⚠ **INFERRED**: implementing it — reporting a real second player — puts the guest on a branch never
exercised on the original handset, after which the single-slot machinery no longer matches what the
guest does. Nothing was run. **What would settle it:** find the guest's branch on that return value,
flip the stub's return in a probe run, diff the media-call trace. The discipline for stub return
values is [wipi-c-recipes.md](wipi-c-recipes.md) §9.

## 18. One subsystem below every front-end

**(A)** Put parse, cache, handle bookkeeping, the gate and the volume law in the **shared backend
crate**, below the per-platform front-end, leaving the front-end as argument marshalling only. Each of
§2–§16 then lands once for every carrier front-end you support instead of once per crate. **(B)** In
the reference port the WIPI-C media table is the front-end that was traced and fixed; ⚠ the MIDP
player front-end enters the same subsystem by construction, but nothing was run through it. **What
would settle it:** point a J2ME title at the same core and re-run the §19 tests.

## 19. Symptom table and verification

Host-side rows (silence on one device, quiet at maximum, effects erased on loud songs, a duck that
never releases) live in [audio-mixing.md](../../android-host/reference/audio-mixing.md).

| Symptom | Cause | Fix |
|---|---|---|
| In-game volume control does nothing | clip-volume stub; the handle changes between set-volume and play | key by clip pointer, re-bind in `play` (§7) |
| Music and effects wildly different at one setting; every balance attempt moves the problem to another slider step | two laws — linear samples vs note velocity | one law, output gain, curved (§4, §5) |
| The lowest setting is still loud; steps are not audibly different | linear level map | square the normalised level (§4) |
| Lower settings sound **duller**, not just quieter | the level was applied inside synthesis | never scale velocity (§5) |
| Inaudible at mid settings | the level was applied twice | capability probe returning `bool` (§6) |
| The music cuts out when an effect plays **and starts over from the beginning** | the guest's single sound slot, reproduced faithfully | host-owned music channel + deferred stop (§10–§12) |
| Music plays over a menu or cutscene that should be silent | stops were *ignored* instead of deferred | the "nothing arrived" branch must honour the stop (§11, §12) |
| The music dies mid-effect | duration taken as the last event time | add the sample length (§15) |
| The music dies early when two effects overlap, or an effect alone stops it | `extend` pulled the deadline inward / invented one | gate invariants (§14) |
| The wrong sound plays, rarely | the parse cache trusted a hash without byte confirmation | confirm hits by bytes (§9) |
| Memory grows steadily, the OS kills the app first, handle count never stops rising | every upload mints a handle nothing releases, each holding its own copy of the file | pointer-keyed bookkeeping released on the guest's free call (§8) |
| An **unrelated** clip goes silent | the handle was read back out of the guest's clip struct | never trust that field — it is uninitialised (§7) |
| Burst of noise plus a frame spike on every resume | the sequencer's backlog dispatched in one poll | rebase past a deficit threshold; saturating subtraction (§16) |
| A frame hitch every time a sound plays | almost never the decoder — measured 0.016–0.049 ms per effect (§9) | measure the whole per-sound chain first: synchronous platform calls and the archive resource path — [frame-pacing.md](frame-pacing.md) |
| Your own notes say "the title has no PCM" or "nothing stops the music" | a negative claim written off a trace | §3 |

**Verification.** `cargo test` inside the vendored core should cover, at minimum: the bus verdict per
file (§2); the volume law over every reachable level, with its dB table (§4); duration including a
zero-rate case (§15); the four gate invariants (§14); and the **deferred-stop state machine driven by
your own measured timestamps** (§13). Outside the test suite: offline renders per bus at matched
settings, tabulated peak and RMS, with the gap stated and **constant** across steps (§4).

Two things the toolchain cannot do. **Nothing in it can hear** — a human listening on a real device is
part of the exit criteria, and the reference port needed that "yes" at the end of every audio round.
And **one law living in two crates cannot be `include!`d both ways**: where you must mirror it into a
standalone test, *pin* the mirror to the real source text instead of accepting an unverified copy —
see [selftest-convention.md](../../../shared/selftest-convention.md).

## 20. Checklist

- [ ] Chunk-tag dump run over the **whole** sound directory; per-bus counts recorded.
- [ ] Bus verdict computed at parse time and stored on the parsed object; nothing derives it from a handle, a pointer or a name.
- [ ] One volume law: one function, output gain, curved — and its comment states the dB table it produces.
- [ ] No velocity scaling on any sink that owns a bus, and the prohibition is written at the site.
- [ ] The sink **declares** whether it applied the level; external-synth sinks keep the old path unchanged.
- [ ] Volume and handle bookkeeping keyed by **clip pointer**, released on the guest's free call, never read back out of the guest struct; playback not stopped on free.
- [ ] Parse once at load; cache keyed by `(length, hash)`, **confirmed by bytes**, entry count bounded.
- [ ] Duration includes the PCM tail; both divides guarded; a zero-rate test exists.
- [ ] The music channel is host-owned, stops are **deferred**, and all four branches of §12 are implemented.
- [ ] Grace and rejoin measured **on your guest**, with a test driving the state machine from those timestamps.
- [ ] Gate invariants tested: no invented deadline, no inward move, kill outranks cancel, clock re-read after every sleep.
- [ ] The sequencer rebases past a deficit threshold and uses saturating subtraction.
- [ ] No negative claim anywhere in your notes that came from a trace (§3).
- [ ] A human has listened, on a real device, at more than one in-game setting.
