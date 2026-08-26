# Device verification

The largest single time loss in a port is usually not technical. It is a disagreement about **which
binary is running**, and it is invisible from the inside: every log line you read is real, the code
you are looking at is correct, and the report you cannot reproduce is honest. Two separate half-days
went that way on the reference port, one build apart.

This file is the discipline that prevents it, plus the rules for what a device — and only a device —
can settle.

Tags: **(A)** generic procedure · **(B)** worked example from the reference port, re-derive it ·
**(C)** do not copy. Conventions: [evidence-standard.md](evidence-standard.md).

## Contents

- [1. The multi-device rule](#1-the-multi-device-rule)
- [2. The first action on any bug report](#2-the-first-action-on-any-bug-report)
- [3. Install in place; never uninstall](#3-install-in-place-never-uninstall)
- [4. The device table, and its expiry notice](#4-the-device-table-and-its-expiry-notice)
- [5. "Unverified on device" is a shipped state](#5-unverified-on-device-is-a-shipped-state)
- [6. Verification tiers: push every question to the cheapest one](#6-verification-tiers-push-every-question-to-the-cheapest-one)
- [7. Making a device session cheap](#7-making-a-device-session-cheap)
- [8. Perceptual findings need an axis, not a constant](#8-perceptual-findings-need-an-axis-not-a-constant)
- [9. Checklist before writing "verified on device"](#9-checklist-before-writing-verified-on-device)

---

## 1. The multi-device rule

A port normally acquires at least two phones: a **low-end baseline** that decides whether the thing
is shippable, and a **current device** that is what the person giving you feedback actually plays
on. That is a good setup and it has one sharp edge.

> **(A) Only the currently attached device appears in the device list. Therefore every device
> command names a serial explicitly.**

Not "usually both are plugged in". In practice exactly one is, and which one is a property of the
desk, not of the work. A build script that installs to "the device" installs to whichever cable was
in, and then records success.

```bash
# (A) Wrong: silently installs to whatever is attached.
adb install -r build/app-release.apk

# (A) Right: the serial is part of the command, and the command fails loudly if it is absent.
adb -s "$ANDROID_SERIAL" install -r build/app-release.apk
```

Make the build script refuse to install without an explicit target, or make it install to **all**
attached devices and print each one. A script that picks one silently is the mechanism of the loss
below.

### The failure, from inside

**(B)** A new build was installed on the low-end baseline and the session notes recorded "device
verification complete". The person playing was on the other phone, several builds behind. Reports
that the new feature "does not work" kept arriving; each one was investigated against source that
had the feature. Half a day, twice.

> **REFUTED.** *"I installed the build and checked it on device, so 'verified on device' is true."*
>
> **Refuted by** the reporter's phone still running an older `versionCode`. The verification was
> real and it was performed on a device nobody was playing.
>
> **Why it was attractive:** every observation in the session was correct. Nothing looked wrong,
> because nothing *was* wrong except the unstated assumption about which binary was under test.
>
> **Correction:** "verified on device" is only meaningful with the serial and the `versionCode` it
> was verified at, both written down.

## 2. The first action on any bug report

> **(A) Before the log, before the diff, before reading any code: read the installed build's
> version off the reporting device.**

```bash
adb -s "$ANDROID_SERIAL" shell dumpsys package com.example.<name> \
  | grep -E 'versionCode|versionName|lastUpdateTime'
```

If it does not match the build that contains the fix, the report is about a different program and
the investigation has not started yet.

**What a version mismatch looks like** — learn the shape, because it mimics a real bug well:

| you observe | and conclude | but actually |
|---|---|---|
| the reported behaviour is impossible given the source | the reporter is describing it wrong | they are describing an older binary correctly |
| a fix "did not take" although its test passes | the fix is incomplete or wrong | the fix is not on that phone |
| logs from the device look normal | the feature never ran | the feature is not in that build to run |

**(A)** Where a person cannot run a shell command, put the version somewhere they can read and
photograph — a diagnostics panel is the usual answer, and it should print the build identity next to
everything else it reports.

## 3. Install in place; never uninstall

> ⛔ **(A) Never resolve a device problem by uninstalling.** On Android, uninstall deletes
> app-private storage: the player's saves, any local backup store, and the evidence you were about
> to read — in one action.

Two consequences worth stating separately:

- An update that will not install because the signing certificate changed is a **release-process
  bug**, not a device problem, and "just uninstall first" is a data-loss instruction. Fix the
  signing. See the `port-shipping` skill.
- A `versionCode` that is not strictly greater than the installed one also refuses to install, and
  it is equally not a reason to uninstall.

Verify an update was an *update* rather than a reinstall by checking that the first-install
timestamp did not move:

```bash
adb -s "$ANDROID_SERIAL" shell dumpsys package com.example.<name> \
  | grep -E 'firstInstallTime|lastUpdateTime'
```

## 4. The device table, and its expiry notice

Keep one row per device in your port notes:

| serial | model / role | installed build |
|---|---|---|
| `$SERIAL_A` | low-end baseline — decides shippability | `vcNN` |
| `$SERIAL_B` | current device — what the reporter plays on | `vcNN` |

**(A) Then write, in the table, that the table is stale, and put the query that refreshes it next to
it.** A build-number column is out of date the next time anyone builds, and a reader who trusts it
makes exactly the §1 mistake. The row is there to record *roles* and *serials*; the build number is
a hint with a stated expiry.

**(C)** Never publish real serial numbers, and never commit them. Use environment variables.

## 5. "Unverified on device" is a shipped state

Ports run ahead of their verification by many builds. A document that records only successes reads
as "all verified", which is how an unverified claim becomes load-bearing.

> **(A) Every session's verification section ends with an explicit list of what it did NOT do.**

**(B)** On the reference port this took the form of a final row headed *"what could not be done"* —
device reproduction blocked because no device was attached, a re-check still pending, a listening
test pending on a particular phone. It is also how the next session gets its first task for free.

Three rules that make it work:

1. **One open verification item per shipped build**, closed only by a human playing it. Not by a
   passing test, and not by the author's own smoke run.
2. **Partial automated output is labelled unverified in the handoff, and re-derived before it is
   believed.** **(B)** One session stopped with one of four investigation branches finished and no
   adversarial verification stage run at all; because the handoff said so, the next session
   re-derived every address from the shipping binary instead of inheriting them. Partial output
   looks like finished output — it is structured, confident, and cites addresses.
3. **Hiding the gap is worse than a long pending list.** A long list is a work queue. A silent gap
   is a false claim that someone will build on.

## 6. Verification tiers: push every question to the cheapest one

| tier | cost | settles |
|---|---|---|
| **Selftest** (dependency-free, no emulator) | milliseconds | Pure logic: geometry, policy verdicts, parsers, formulas, rotation. → [selftest-convention.md](selftest-convention.md) |
| **Headless probe** (no phone, deterministic) | ~a minute | Anything the guest does: frame pixels, RAM diffs, guest call behaviour, A/B of a patch. Same input, same frame hashes. |
| **Device** | a session, and someone's attention | Everything below. |

**(A) Device-only questions** — do not attempt these anywhere else:

- Real input latency, touch ergonomics, and whether a control is reachable by a thumb.
- Platform lifecycle: background, lock, focus loss, low-memory kill, and what the OS does to
  sockets while the app is cached.
- Anything perceptual: audio balance, readability at arm's length, whether an effect is audible.
- Thermal behaviour and sustained frame rate.
- Whether the *reporter's* actual sequence reproduces.

**(A) And one gate that looks device-shaped but is not:** a boot smoke test only covers the boot.
**(B)** A data-table member with one fewer record than a guest constant expected made the guest's
parser run past an array end, produce a negative allocation size and die — *after* the title screen.
"Reaches the title screen" and "survives five thousand frames with zero tick errors" are different
claims, and only the second catches archive/constant skew. Make the long headless run the gate.

## 7. Making a device session cheap

A device session is the most expensive tier, so spend it on questions only it can answer, and make
each answer arrive in one line.

- **(A) One log line per decision, emitted by the layer that owns the decision.** Not a dump. A
  line that names the inputs, the verdict, and the branch taken. If the UI layer makes a decision
  the worker cannot see, the worker's diagnostics can never explain it — the line has to come from
  the UI layer.
- **(A) Keep the last bug's diagnostic lines.** They are already tuned to the subsystem and they
  answer the next report. Deleting diagnostics after a fix is how the next session starts from
  zero. **(B)** Three retained log lines located a save bug in fifteen minutes.
- **(A) One grep-able filter per subsystem**, written into the session record next to the finding,
  so the next person reproduces the *observation*, not just the bug.
- **(A) Timestamped, length-bearing filenames date an incident without decrypting anything.**
  **(B)** A backup store named `<unix_ms>-<length>.bin` let a session identify the exact minute a
  slot became a different character — from a directory listing, on an unrooted phone, with the
  contents encrypted and unreadable.

## 8. Perceptual findings need an axis, not a constant

> **(A) A global tuning constant chosen by ear on one device earns the opposite complaint from the
> next device. Both reports are correct. Expose the axis as a setting.**

**(B)** Two opposite reports about one audio trim were each right on their own hardware. The note
that settled it reads: *a third global number will just earn the same report from a third device.*

The rule that makes adding the axis safe:

> **(A) The new setting's default must be bit-identical to the previously shipped value.** Then the
> change is a zero-regression change: a player who never opens the setting hears exactly what they
> heard before, and nothing already tuned needs re-verifying.

## 9. Checklist before writing "verified on device"

- [ ] The serial is named in the sentence, not implied.
- [ ] The `versionCode` under test was read off that device, not off the build script.
- [ ] The build was installed with an explicit `-s <serial>`.
- [ ] The install was an update — the first-install timestamp did not move.
- [ ] Each reported symptom was checked **independently**; one fix was not assumed to close a
      cluster. **(B)** A speed complaint had a real measured cause that was fixed and verified, and
      the symptom persisted with a completely unrelated second cause.
- [ ] Anything that could not be checked is written down as a pending item, per build.
- [ ] No serial, account identifier or personal path reached the notes that will be published.
