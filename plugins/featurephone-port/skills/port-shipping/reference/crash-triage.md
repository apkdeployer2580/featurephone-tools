# Crash triage on a distributed build

"The app just closes" is the most common field report a port receives, and it is **at least two
unrelated faults**. One of them leaves a native tombstone; the other leaves nothing but an exit
record. The player's description is identical either way, and closing one axis does not close the
other.

This file is the triage: which device records answer which question, how to read a tombstone before
you can symbolicate it, how to symbolicate a stripped release binary with no symbol file, and how to
recognise the one design decision that puts an emulator first in line for the low-memory killer.

Tags: **(A)** generic procedure · **(B)** worked example from the reference port, re-derive it ·
**(C)** do not copy. `⚠` marks anything inferred. Conventions:
`${CLAUDE_PLUGIN_ROOT}/shared/evidence-standard.md`.

## Contents

- [1. "The app just closes" is at least two faults](#1-the-app-just-closes-is-at-least-two-faults)
- [2. The three device records, and what each one answers](#2-the-three-device-records-and-what-each-one-answers)
- [3. Read a tombstone's shape before its contents](#3-read-a-tombstones-shape-before-its-contents)
- [4. Symbolicating a stripped release binary](#4-symbolicating-a-stripped-release-binary)
- [5. Attributing a death to a specific installed build](#5-attributing-a-death-to-a-specific-installed-build)
- [6. The OS-kill axis](#6-the-os-kill-axis)
- [7. Eager address-space commit is the fault that creates it](#7-eager-address-space-commit-is-the-fault-that-creates-it)
- [8. Every saving needs a level gauge](#8-every-saving-needs-a-level-gauge)
- [9. Measure on a release artifact, and check the device's own frequency policy](#9-measure-on-a-release-artifact-and-check-the-devices-own-frequency-policy)
- [10. One axis closed is not the bug closed](#10-one-axis-closed-is-not-the-bug-closed)
- [11. Checklist before claiming a cause](#11-checklist-before-claiming-a-cause)

---

## 1. "The app just closes" is at least two faults

| | crashed | killed |
|---|---|---|
| what happened | the process took a fatal signal | the OS chose to end the process |
| what it leaves | a **native tombstone** — signal, cause line, backtrace, registers | **nothing but an exit record**, carrying a reason code |
| typical cause in a port | interpreter fault, allocator recursion, stack overflow | resident memory, while the phone needed pages |
| how the player describes it | "it just closes" | "it just closes" |

**(A)** Investigate both axes on every report until one is positively excluded. **(B)** On the
reference port, eight native crashes were found and fixed — and the reports continued, because the
surviving fault was on the other axis entirely and had left no tombstone to find.

## 2. The three device records, and what each one answers

**(A)** Read all three in one session and cross-tabulate them. They answer different questions and
none of them is a superset of another.

| record | answers | note |
|---|---|---|
| **Tombstone directory** | *What* faulted: signal, cause line, backtrace, register state | Deep detail, but a small ring — old entries roll off |
| **System crash-log store** | *When* it last happened | **Survives reboots**, so it dates the most recent occurrence without waiting for a reproduction |
| **Process-exit history** | Deaths with **no crash at all** | The only source that sees an OS kill. Carries the reason code **and the process's resident size at the moment it died** |

**(B)** In one session the tombstone directory and the crash-log store held the same eight native
crashes, all ending on the same date — meaning the native fault was already fixed — while the exit
history showed a *later* death with a low-memory reason. The third record is the one that contained
the live bug.

## 3. Read a tombstone's shape before its contents

**(A)** Three shape facts settle most of a triage before any source is opened:

1. **A backtrace that alternates between exactly two addresses for its whole length is a two-node
   mutual recursion**, not deep nesting. Do not look for a deep call chain; look for two functions
   that call each other.
2. **The reported frame count is the unwinder's own cap**, not the real depth. A backtrace that
   stops at a round number stopped because the unwinder stopped.
3. **The arguments are frequently still in registers, and frequently name the failure in plain
   ASCII.** Dump the register block and look for readable text before doing anything else.

**(B)** An alternating address pair, plus the failure's own message text sitting in a register, plus
an identical register state across five tombstones, identified a `Jvm::exception` ↔ guest-allocator
cycle before a line of source was read: reporting an allocation failure required allocating.
The mechanism and its fix live in
[../../emulator-core/reference/hardening-batch.md](../../emulator-core/reference/hardening-batch.md).

## 4. Symbolicating a stripped release binary

A distributed build is stripped, and you probably kept no symbol file for it. You can still resolve
its frames **against a current build**, with no symbol file at all.

**(A) The procedure:**

1. Take the stripped shared object out of the distributed package itself — it is in there.
2. **Recover function boundaries from the unwind tables**, which survive stripping. This gives you
   the start and length of every function in the old binary without any names.
3. For each old function, **match its bytes against the current build's functions with branch
   targets and page-relative address immediates masked out.** Those are the only fields that move
   when code is relocated; everything else is stable across a rebuild that did not touch the
   function.
4. Map the tombstone's addresses through the resulting correspondence to today's names.

**(B)** Run over five tombstones from a distributed build, this resolved every frame, and produced
identical results across all five — which is itself the check that the matching is sound.

> **(A) Why this is worth the effort:** the alternative is asking the player to reproduce a crash on
> a debug build, which changes the timing, the memory profile and often the bug.

## 5. Attributing a death to a specific installed build

**(A)** A tombstone is evidence about *a* binary. Before you attribute it, establish *which*:

- read the installed `versionCode` off the reporting device;
- compare the timestamp in the crash-log store against that build's install time;
- confirm the shared object you symbolicated against came from that same package.

Full discipline, including the two half-days it costs to skip this:
`${CLAUDE_PLUGIN_ROOT}/shared/device-verification.md`.

## 6. The OS-kill axis

**(A)** When the exit history shows a low-memory reason, the number sitting next to it — the
process's **resident size at the moment it died** — is not merely a symptom. It is the explanation
for why the OS chose *your* process.

> **★ (A) An emulator that reserves a large guest address space is structurally the fattest process
> on the phone, and therefore first in line for the low-memory killer on every device, no matter how
> well it runs.**

**(B)** One device's exit history showed a low-memory kill alongside ten other exits whose recorded
resident sizes were around half a gigabyte; a live memory dump taken at the title screen attributed
almost all of it to the guest address space.

This is why a port can be "stable" in every technical sense — no faults, no leaks, a flat allocator
gauge over thirty thousand headless frames — and still close on people. Nothing is wrong. The
process is simply the cheapest thing on the phone to kill.

## 7. Eager address-space commit is the fault that creates it

> **★ (A) Commit guest pages on first WRITE only.**

A read of a mapped-but-never-written page must return zero **without committing**. This is not an
approximation: an eagerly mapped page was zero-filled anyway, so the bytes handed back are
identical. The only thing that changes is whether the phone is holding them.

**(B)** Replacing an eager mapper with a lazy page table plus a per-page mapped bitmap, measured A/B
on the same phone and the same screen: total resident size **−47 %**, native heap **−66 %**.

**(A) One constraint comes with it, and it will bite immediately if you miss it:**

> Once commit is lazy, *"is there a page object here"* is no longer the answer to *"is this address
> mapped"*. Keep the permission map as a **separate bitmap**, or a freshly mapped heap reports as
> unmapped until something writes to it, and the core's own guest-pointer validation starts
> rejecting perfectly valid pointers.

Keep the page table dependency-free so a standalone selftest can `include!` it —
`${CLAUDE_PLUGIN_ROOT}/shared/selftest-convention.md`. Process footprint as an ongoing concern:
[../../emulator-core/reference/memory-footprint.md](../../emulator-core/reference/memory-footprint.md).

## 8. Every saving needs a level gauge

> **(A) A memory- or work-saving change ships with a gauge that reports the saved quantity as a
> LEVEL, so a later regression appears as a number climbing back toward the old value instead of as
> a vague slowdown.**

**(B)** The lazy-commit change shipped with a committed-guest-pages gauge attached to the periodic
throughput log, and the interpretation written next to it: *if this climbs back toward the
precommitted size, something is touching pages it does not need.*

Without the gauge, the regression is a report six months later that the app "closes a lot again",
and the entire triage above has to be run from the start.

## 9. Measure on a release artifact, and check the device's own frequency policy

Two ways to spend a session measuring something that is not real:

**(A) A debug artifact is usable but not measurable.** Setting the optimisation level on the debug
profile makes a debug build fast enough to play, but overflow checks and debug assertions stay on —
and in a CPU interpreter those land on *every arithmetic operation*. **(B)** The same scene on the
same phone moved the emulator thread from **89.6 %** to **64.5 %** of a core purely by switching to
the release artifact. Gate every performance claim on a release build, and confirm it by reading the
built package's own flags rather than remembering which profile you used.

**(A) Check whether the device is capping its own cores** before comparing throughput between
phones. A vendor performance or game-management policy can pin the big cores to the little cores'
maximum with no battery saver active and no thermal event, and no code can lift it. **(B)** Measured:
the big-core policy maximum equalled the little-core maximum, while the hardware maximum was 51 %
higher — battery at 34.7 °C, low-power flag off.

## 10. One axis closed is not the bug closed

**(A)** Two rules, both learned the expensive way:

- **Verify each reported symptom independently.** One fix rarely closes a cluster. **(B)** A speed
  complaint had a real, measured cause that was fixed and confirmed on device; the symptom persisted
  with a completely unrelated second cause. When a previous session's "fixed" turns out to be half,
  correct that text **in place** rather than deleting it — the half that was right is what stops the
  next session reverting the fix.
- **Distinguish mitigation from fix, and say which you shipped.** **(B)** Enlarging a thread stack
  reservation made a stack-overflow crash rarer and was recorded as a mitigation; the fix was
  removing the recursion. A mitigation recorded as a fix is a bug that returns under a new report.

**(A)** Also note that "support older devices" is usually not the answer. A several-generations-old
flagship still reports the 64-bit ABI first, and adding a 32-bit ABI turns a large thread-stack
reservation from free into an address-space risk. **(B)** An audit across three devices spanning
eight years and three Android major versions found **nothing to lower** — and that was still the
right outcome, because it redirected the investigation from the version axis to the individual
device, where the actual defect was that nothing in the build could confirm a compatibility problem
either way. See [on-device-diagnostics.md](on-device-diagnostics.md).

## 11. Checklist before claiming a cause

- [ ] Both axes checked: a tombstone was looked for **and** the exit history was read.
- [ ] The `versionCode` of the build that died was read off the reporting device.
- [ ] The tombstone's shape was read (alternating pair? unwinder cap? ASCII in registers?) before
      its contents.
- [ ] Frames were symbolicated against the binary from **that package**, not a later build, or the
      byte-matching procedure in §4 was used and cross-checked across more than one tombstone.
- [ ] If the death was an OS kill: the resident size at exit is written down, and the guest address
      space is accounted for in it.
- [ ] Any performance number quoted came from a **release** artifact.
- [ ] Every change that saves memory or work shipped with a level gauge.
- [ ] The write-up says explicitly whether this was a fix or a mitigation.
