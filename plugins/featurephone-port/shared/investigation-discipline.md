# Investigation discipline: how to search, and how to be wrong cheaply

The expensive failures in a long port are **search failures, not analysis failures**. Nobody loses a
week misreading an instruction. They lose it by watching a window that could only ever return a
plausible answer, by reading a zero-hit search as a negative result, by guarding one of two paths, or
by trusting a green test written from the code's own output.

Each rule below is one sentence you can recall while you are inside the mistake, followed by the case
that paid for it.

Tags: **(A)** generic procedure · **(B)** worked example from the reference port, re-derive it ·
**(C)** do not copy. Conventions: [evidence-standard.md](evidence-standard.md). Device rules:
[device-verification.md](device-verification.md). The shape of a guard itself:
[guarding-destructive-operations.md](guarding-destructive-operations.md).

## Contents

- [1. A narrow watch window cannot discover](#1-a-narrow-watch-window-cannot-discover)
- [2. Write down the limits of a search](#2-write-down-the-limits-of-a-search)
- [3. The offset is not a constant, it is an array](#3-the-offset-is-not-a-constant-it-is-an-array)
- [4. How the value is READ is part of the field's contract](#4-how-the-value-is-read-is-part-of-the-fields-contract)
- [5. Count the paths that produce the event before you guard one](#5-count-the-paths-that-produce-the-event-before-you-guard-one)
- [6. Attribution versus magnitude](#6-attribution-versus-magnitude)
- [7. Absence in a trace is an observation gap](#7-absence-in-a-trace-is-an-observation-gap)
- [8. Settling a disagreement between two readings](#8-settling-a-disagreement-between-two-readings)
- [9. On a bug report, grep the test suite before you read code](#9-on-a-bug-report-grep-the-test-suite-before-you-read-code)
- [10. An invisible refusal arrives as a design question](#10-an-invisible-refusal-arrives-as-a-design-question)
- [11. A proven constraint is reusable in the opposite direction](#11-a-proven-constraint-is-reusable-in-the-opposite-direction)
- [12. Order dependence between fixes](#12-order-dependence-between-fixes)
- [13. Verify each symptom independently](#13-verify-each-symptom-independently)
- [14. The handoff artefact](#14-the-handoff-artefact)
- [15. Symptom table](#15-symptom-table)
- [16. Checklist](#16-checklist)

---

## 1. A narrow watch window cannot discover

> **(A) A search window centred on where you expect the answer can only confirm or mislead. It
> cannot discover.**

The failure is self-reinforcing, which is why it survives: a narrow window always yields a
*best-in-window* candidate, so it never reports failure. You get a hit, you write against it, nothing
happens, and you go back to hunting the address — inside the same window.

**(B) Three consecutive wrong answers for one flag byte.** All three came out of a ~128-byte window,
chosen because a second carrier build of the same title put the field there. Each candidate matched
on offset, on value and on timing. Each host write did nothing. The third was written down as
"refuted", which was also wrong — see [§3](#3-the-offset-is-not-a-constant-it-is-an-array).

**(A) The procedure that broke the streak:**

1. **Widen to everything.** The whole god object and the whole static area, sampled twice a second.
   **(B)** 43 KB, twice a second, with the stated justification: *that is a memcpy; there is no
   reason to narrow it.* The wide watch costs nothing. The narrow one cost three sessions.
2. **Auto-silence the counters.** A byte that differs on two consecutive samples is a clock, a frame
   counter or an animation phase. Silence it automatically or it buries the signal.
3. **Make the game write the byte.** Do not hunt the writer. Perform the in-game action and look for
   the *single persistent* `0 → non-zero` transition at that instant.
4. **Retire the old candidates in the same run.** The run that finds the answer should also print the
   historical ones. **(B)** All three printed 0 throughout.
5. **Confirm with a host write, not with the correlation.**

**(A) "It changed at that instant" is not evidence.** Block writes move neighbours wholesale; a
`memcpy` of one record moves dozens of bytes at the moment of your action and every one correlates
perfectly. Correlation identifies a *region*. Confirmation is: the host writes the value, the game
behaves.

**(B)** The second derivation arrived independently — a later session disassembled the game's own
accessor, and the arithmetic lay outside the original window's assumptions entirely.

---

## 2. Write down the limits of a search

> **(A) "This search cannot catch X" is a documentation obligation, not a modesty note. The next
> session opens at exactly that gap.**

**Zero hits is the most misread result in this work.** Undocumented it reads as *the field is dead*.
Documented with its blind spot it reads as *a work item with a starting point*. One sentence.

**(B) The causal chain.** One session ran three static searches, got nothing, and recorded that they
were literal and displacement analyses and therefore **could not catch a runtime-computed index**.
The next session started there, found the runtime-computed index, and closed a bug that had been
answered wrongly three times — recording the causal link explicitly: writing the limit down is what
made the next person dig there.

### 2.1 Downgrade the claim, not the behaviour (A)

That same session refused to null the anchor — *grounds for suspicion, not proof of absence, and
turning it off is a behaviour change that needs device evidence.* It changed what the code **claims**:

```
- feature forced ON
+ writing <addr> <- <value>  (anchor UNCONFIRMED — no reader found)
```

A log line costs nothing and cannot regress. Disabling a feature on a static hunch is an untested
behaviour change made for documentation reasons. **(B)** The next session found the anchor; the
feature had never been broken by the doubt.

### 2.2 A fifth field for the refutation block (A)

Add **"Limits of the search that produced this"** to the four fields in
[evidence-standard.md §4](evidence-standard.md#4-preserved-refutation-blocks). It is what turns a
preserved refutation into a starting point rather than a warning sign.

---

## 3. The offset is not a constant, it is an array

> **(A) When several sessions each measure a different but individually correct-looking value for
> "the same offset", it is an array indexed by a live value elsewhere, and every past measurement is
> one element of it.**

**The tell** is exactly that: a history of plausible, mutually inconsistent answers, each of which
worked for whoever measured it.

**Why a static sweep reports zero sites.** Nothing materialises the final address. The game loads an
index word, adds it to a sub-base and stores at a fixed displacement, so literal and displacement
xref searches are structurally blind to it — and the porter concludes the field is dead. It also
explains the *"it worked for me once"* report: the value was written while the index happened to hold
the constant baked into the host.

> **REFUTED.** *Previously documented:* the flag lives at a fixed offset in the character struct —
> three different values were each recorded as that offset (one inherited from another carrier's
> notes, one measured on device, one marked "refuted").
> *Refuted by:* a whole-struct watch, plus disassembling the game's own address arithmetic.
> *Actual mechanism:* the address is `base + <index word> + <fixed displacement>`, and **(B)** the
> three historical answers were elements **0, 1 and 6**. The index word was already in the anchor
> table under a different name, because the save chain reads the same word.
> *Why the wrong reading was attractive:* all three were real, correct observations — of different
> elements.
> *Consequence if carried forward:* the host writes element 0 for everyone, so the feature works only
> for players whose index is 0 and fails with no symptom for the rest.

**Reject an out-of-range index; never fall back to element 0.** A wrong index writes into an
arbitrary part of the record and, again, fails without a symptom. Structure work of this kind:
[live-data-structures.md](../skills/guest-re/reference/live-data-structures.md).

---

## 4. How the value is READ is part of the field's contract

> **(A) Disassemble the READER, not the writer. An anchor is a triple — offset, value, and the
> reader's predicate — not an offset.**

A host write at the right address with the wrong-shaped value fails **silently and identically to a
wrong address**, so the porter goes back to hunting the address. That is the whole trap.

**(B) Three instances in one port, all from the same instruction shape:**

| what was read | reader's instructions | consequence |
|---|---|---|
| a feature flag | `ldrb; lsls #24; asrs #24; cmp #0; bge skip` | a **bit-7 test** — writing `1` at the proven-correct address does nothing |
| a stack count | sign-extended via `lsls/asrs #24` | 128 or more is negative, and the client **subtracts** from the player's stack |
| a capacity byte | `ldrsb` then `bge` | a legitimate 128 became −128 and killed **both** page-navigation gates |

The cap follows: **any field the guest sign-extends has a real maximum of 127**, whatever your wire
format or store permits — a constraint on host, offline engine and server at once.

**(B)** After the bit-7 finding the anchor entry changed from a bare offset to a struct carrying base
offset, index offset and value; the notes state that keeping only the offset would have produced
*another silent failure*. ⚠ **INFERRED half:** the predicate lives in a comment rather than being
encoded, so a wrong-shaped write is still constructible. Encoding it on the anchor (a predicate the
writer must satisfy) would settle it; nothing has been run. Anchor tables:
[build-keyed-anchors.md](../skills/guest-re/reference/build-keyed-anchors.md).

---

## 5. Count the paths that produce the event before you guard one

> **(A) When you find *the* path that produces an event, ask how many paths produce it. A guard on
> one of two paths destroys data on the next build.**

**(B)** A save-slot guard was rebuilt around the network path's load, which is observable because it
emits a frame. The local start-game path loads the same records and emits nothing, so it was
invisible to the host — and the same user's save was destroyed again one build later.

**(A) How to enumerate: list the readers of the primitive, not the senders of the frame you happened
to notice.** The fix observed the record read itself, which both paths perform, and was validated
against the *existing* device trace with the counts stated: **(B)** 11 record-1 reads, all 11 loads,
zero false positives, and writes provably never read — the create mode skips the record-1 seed.

Before trusting any proxy signal, ask its **cardinality**: once per session, or once per action? That
question and the guard's own shape belong to
[guarding-destructive-operations.md](guarding-destructive-operations.md).

---

## 6. Attribution versus magnitude

> **(A) An aggregate counter gives you magnitude, never kind. An attribution derived from a total is
> not an attribution.**

On a carrier runtime where even the C standard library goes through the same supervisor-call path, a
total cannot separate a `memcpy` from a socket syscall.

> **REFUTED.** *Previously documented:* the hot path is a socket receive poll running ~26,000 times
> per second.
> *Refuted by:* per-category counters plus a hot-identifier breakdown, re-measured on device.
> *Actual mechanism:* receive polls ran at **zero** per second; the 26,000 were a fill-rectangle call.
> *Why the wrong reading was attractive:* the magnitude was real, and the port was investigating the
> network at that moment.
> *Consequence if carried forward:* **(B)** two sessions of work had already been planned against the
> wrong subsystem.

The sentence to use when correcting a note like this is **"the number was right, the attribution was
wrong"** — it separates the two claims so the measurement survives.

**(A) Instrument for kind:** calls/s **by category**, the hot identifiers within the category, and a
frame gauge printing the guest's **requested** timer period next to the **achieved** one. See
[diagnostics.md](../skills/emulator-core/reference/diagnostics.md) and
[frame-pacing.md](../skills/emulator-core/reference/frame-pacing.md), which owns the counter-intuitive
tell of multiplied pacing grids: the whole game is slow while the report names one screen.

### 6.1 Measure host idle before optimising further (A)

> **When the host is mostly idle, the remaining slowness is the guest's own design, and the question
> stops being an emulator question.**

**(B)** After the pacing fix one game state still ran at 4–7 fps. The guest itself arms a **244 ms**
timer in that state (20 ms everywhere else) and the host was **idle 78 %** of the time. The session
recorded that the emulator has nothing left to do, that raising it means patching a guest constant,
that patching it changes game behaviour and is therefore the user's decision — and stopped.

Without the idle number this is the next optimisation target and absorbs a session. With it, the
finding is a one-line policy question and *"this is the game's speed"* is defensible to the person
reporting the lag.

---

## 7. Absence in a trace is an observation gap

> **(A) "We never saw X" is not a negative result. Write it as an observation gap, or it will be read
> later as "X does not happen".**

> **REFUTED.** *Previously documented:* neither the host nor the guest ever stops the music.
> *Refuted by:* a device trace of the real call sequence.
> *Actual mechanism:* the game owns exactly one media clip and recycles it — it stops the music every
> single time it plays a sound effect, then restarts the music from the beginning.
> *Why the wrong reading was attractive:* the claim rested on a headless trace in which the two audio
> buses never overlapped. The self-correction names it: *that was not evidence, it was an observation
> gap.*
> *Consequence if carried forward:* every downstream mixing decision is built on a false model of who
> owns the channel.

Prevention, in the note itself: write **"not observed in this trace; the trace did not exercise it"**.
The measured call sequence that refuted it then became the test data for the fix.

---

## 8. Settling a disagreement between two readings

> **(A) Two competent readings of the same instruction do not resolve by rereading. Settle it with
> something countable.**

Countable things that end the argument in minutes: a **computed literal-pool address**
(`(pc + 4) & ~3 + imm`), an **enumeration of that literal's readers** across the image, a **size
relation that closes**.

> **REFUTED.** *Previously documented:* an inherited investigation read one branch as "free the
> buffer", which made the surrounding code harmless.
> *Refuted by:* computing the literal the load actually reads, then enumerating every reader of that
> literal image-wide — there were exactly two.
> *Actual mechanism:* the call was the save **deserialiser**, so the surrounding code was a
> destructive load.
> *Why the wrong reading was attractive:* both readings were of the same instruction and each was
> internally consistent.
> *Consequence if carried forward:* the guard is placed on the wrong side of a load that overwrites
> the player's character.

**(B) Structural companion:** where every cross-module call is `ldr rN, =addr|1` followed by a branch
through a register veneer, a branch-and-link call graph finds **nothing at all** — literal-pool
arithmetic is the only call graph available. Tooling:
[static-toolkit.md](../skills/guest-re/reference/static-toolkit.md).

**(A) Corollary for inherited work.** Output from an automated investigation whose verification stage
never ran must be **labelled unverified in the handoff**, with its artefacts named, and every address
re-derived from the shipping binary before one is believed. Partial automated output looks finished:
structured, confident, citing addresses. **(B)** The port did label one such handoff; the next session
inherited no addresses, re-derived them, and found that reading exactly inverted.

---

## 9. On a bug report, grep the test suite before you read code

> **(A) Grep the suite for the reported input first. If a green test contains it, the test is the
> bug.**

This inverts the normal assumption, which is why it works. When the suite is green and the user is
right, the suite is the fastest place to find the wrong premise — and the one place nobody looks,
because it is "covered".

**(B) Three shipped instances, in two languages, two of them not mirrors:**

- a server test named for a capacity "still fitting in one byte" asserted **the exact value that
  overflowed the client's signed read**; the write-up names that test as the reason the bug shipped;
- a negative list in a host selftest asserted that a legitimate equipment category is not
  equipment — **23 green assertions continuously proving the bug** across five shipped builds, until
  it reached the user as a question about the game's design;
- a save-policy selftest asserted the reported **destructive** sequence as the expected safe outcome,
  under a case comment that mis-named it.

> **REFUTED.** *Previously documented:* a green test is untrustworthy only when it is a hand-kept
> mirror of the shipping source.
> *Refuted by:* the second and third instances above, which `include!` the shipping source.
> *Actual mechanism:* `include!` removes **drift** and freezes **wrong premises** — two independent
> failures. An assertion written from the code's current output pins the bug and ships it.
> *Consequence if carried forward:* the suite is trusted precisely where it guards a defect.

⚠ **The procedure itself is INFERRED** — derived from three confirmed instances where the reported
input was already in a passing assertion, but never actually run *first*. What would settle it: apply
it to the next report and record whether it was the shortest path. The assertion rules it implies
(state the rule not the output; derive negative and refusal lists from the guest's own gate and cite
the derivation; name cases after properties, never inputs) are owned by
[selftest-convention.md](selftest-convention.md).

**(A) Related: one rule expressed in three subsystems is three chances to disagree.** A port grows a
host-side filter, an in-app offline engine and a replacement server, and each new one is written from
the notes rather than from the others. **(B)** A category bound was correct in the engine and the
server from day one and narrowed in the newest host filter, so the same operation worked over the
network path and was impossible in-game for five consecutive builds — presenting as a *mode* bug.
Derive the three from one predicate, or test them against each other.

---

## 10. An invisible refusal arrives as a design question

> **(A) A refusal that happens before the candidate list is built is invisible. Render refusals as
> disabled rows carrying their reason.**

This is not cosmetic. An invisible refusal is **misclassified by the reporter**: it reaches you as a
question about the game's rules, and it gets *answered* rather than fixed.

**(B)** A picker module documented exactly this principle in a code comment — *"my sword is not in the
list" is indistinguishable from broken, whereas "this item has no sockets" explains itself* — and then
a category filter one layer up removed candidates **before** the list was constructed, so the
affected items produced not even a greyed row. The report arrived as the exact failure the comment
predicted.

> **(A) The audit follows: wherever a module states a principle, check every filter upstream of that
> module's list builder. The layer that documents a rule is not the layer that breaks it.**

---

## 11. A proven constraint is reusable in the opposite direction

> **(A) A limitation recorded for one feature is frequently a guarantee for another. Re-read the
> constraint list when designing something unrelated.**

**(B)** *"This reply only opens a message box on one of the two online screens"* was recorded as a hard
limitation, and it killed an approach that needed a notice on any screen. Two sessions later the same
fact was a **guarantee** for a different feature — purchases only ever happen on that one screen — and
the same reply shape was used deliberately. The neighbouring constraint, *"dismissing the box does not
change the screen"*, was fatal in the first context and exactly right in the second.

Device-proven constraints are the most expensive facts in the notes and they get filed as dead ends.
Keep them as a **list per subsystem**, not as prose inside the session that produced them.

**(A) The same instinct one level up:** before building host machinery for a state the original had to
handle — disconnected, error, confirm, out of space — look for the game's own widget for that state;
its key handler frequently already performs the transition you cannot trigger from outside. That
method is owned by [guest-widgets.md](../skills/android-host/reference/guest-widgets.md).

---

## 12. Order dependence between fixes

> **(A) Two changes that are each correct in the final state can be incorrect in either intermediate
> state. Nothing in a diff, a test run or a build shows this.**

**(B)** Suppressing a duplicate item the client delivered was correct for one of two item types and
wrong for the other — because for the second type that "duplicate" *was* the feature: a host-side
reward was triggered by using it. The first version suppressed both symmetrically and **silently
deleted the item's entire function**; adversarial verification caught it before it shipped. The
symmetric suppression became correct only once the host reward had been retired. The write-up states
the ordering as the essential fact: *retire the reward first, suppress the copy second; the reverse
makes the feature disappear quietly.*

Write the dependency **next to both changes**. It is the only place it can live.

### 12.1 A substitute must carry its own expiry condition (A)

Every port accumulates stand-ins for things it cannot yet do. **Nothing retires them automatically.**

**(B)** A host-side reward pool existed only because the inventory record schema was not yet
understood well enough to grant the real item — and the code comment said so, with a date. A later
session implemented that capability; the substitute survived unnoticed for many sessions until a user
asked *"why is this here?"*, and the dated comment made the diagnosis instant.

Record the expiry condition at the definition site, in the form of the [measured-constant
comment](evidence-standard.md#5-a-measured-constant-carries-its-symptom-its-measurement-and-its-date).
The session that satisfies the condition will be working on something else and will not think to look.

---

## 13. Verify each symptom independently

> **(A) One fix rarely closes a cluster. And when a previous session's "fixed" turns out to be half,
> correct that session's text in place rather than editing it away.**

**(B)** A speed complaint had a real, measured cause — a host clamp overriding the game's own timer
setting — which was fixed and verified. The symptom persisted, with a completely unrelated second
cause (a release-side platform stub leaking timers; see
[wipi-c-recipes.md](../skills/emulator-core/reference/wipi-c-recipes.md)).

How the correction was written, and this is the transferable part:

- it says plainly *the previous section was premature; correcting it here*, **in the earlier section's
  own text**;
- it **keeps the earlier measurement**, which was valid;
- it marks the constant that fix introduced as **not to be reverted**;
- it adds a **falsifiable prediction** for the remaining half — *connect twice and it should get twice
  as fast; that confirms accumulation.* The prediction held and named the real cause.

> **(A) Editing the wrong claim away is how the half that was right gets reverted six sessions later
> by someone who cannot see why it is there.**

---

## 14. The handoff artefact

A long port log is read as a to-do list unless it says otherwise, and then sessions re-attempt
finished work or follow a superseded instruction. **(B)** One convention held across a 12,000-line log
and roughly seventy sessions:

1. **Exactly one artefact is current: a single TODO table at the very top**, declared as the only
   current content.
2. A header sentence that makes *"let's do session 39"* a meaningless request — everything below the
   table is a **record of a completed session**.
3. An explicit **newest-first reading order**, naming which section supersedes or corrects which.
4. **Stable item ids, never reused.** When two parallel branches collided it was resolved by
   renumbering, *with a note saying what was renumbered and why*.
5. Closed items keep a one-line entry naming the session that closed them — **and a note when a later
   session invalidated that closing text.**
6. Every session record ends with a verification table whose **last row is what was not done**; see
   [device-verification.md §5](device-verification.md#5-unverified-on-device-is-a-shipped-state).

Point 3 is what makes a preserved refutation *findable* rather than merely present. Template shape:
[scaffolding.md](../skills/port-shipping/reference/scaffolding.md).

### 14.1 When you record a fact, record what the fact invalidates (A)

This is the failure mode of an **accurate** notes file. **(B)** The port had written down that one
application-state value covers both the pre-game shell and the online screens — correct, dated, and
the stated reason an existing feature was gated on it. What it had not written was the consequence:
*therefore that value cannot mean "outside the game world"*. A later session used it that way and
disabled touch input on every online screen.

One line — **"therefore do not use X to mean Y"** — is the part a future reader is searching for, and
the only part that stops the trap.

### 14.2 Keep the diagnostic lines (A)

They look like debris once the bug is closed; in this class of project the same subsystem produces the
next three bugs. **(B)** Log lines added to a save-policy path in one session gave the next session's
cause, timestamp and damage in fifteen minutes with no inference at all. Scope and ownership:
[device-verification.md §7](device-verification.md#7-making-a-device-session-cheap).

---

## 15. Symptom table

| symptom | cause | first move |
|---|---|---|
| A candidate matches on offset, value and timing, and the host write does nothing | A narrow watch window, or a value whose shape fails the reader's predicate | Widen to the whole struct and static area ([§1](#1-a-narrow-watch-window-cannot-discover)); disassemble the reader ([§4](#4-how-the-value-is-read-is-part-of-the-fields-contract)) |
| Several sessions each measured a different, individually plausible offset | It is an array indexed by a live word; each answer is one element | Look for `load index → add to sub-base → store at fixed displacement` ([§3](#3-the-offset-is-not-a-constant-it-is-an-array)) |
| A static search returns zero hits and you are about to call the field dead | Literal/displacement analysis cannot see a runtime-computed address | Record the blind spot; downgrade the log line, not the behaviour ([§2](#2-write-down-the-limits-of-a-search)) |
| You guarded the path, verified it against the reporter's repro, shipped, and the data died again | Two paths produce the event; the second emits nothing observable | Enumerate readers of the primitive, not senders of the frame ([§5](#5-count-the-paths-that-produce-the-event-before-you-guard-one)) |
| Your plan for the next session rests on which platform call is hot | An aggregate counter gives magnitude, not kind | Add per-category and hot-id counters before attributing ([§6](#6-attribution-versus-magnitude)) |
| A screen is still slow after the pacing work | It may be the guest's own timer | Measure host idle first ([§6.1](#61-measure-host-idle-before-optimising-further-a)) |
| A note says "this never happens" and the device does it constantly | An absence in a trace was recorded as a negative result | Re-word as an observation gap; re-run exercising that path ([§7](#7-absence-in-a-trace-is-an-observation-gap)) |
| Two investigations read the same instruction as two different functions | Neither reading is a derivation | Compute the literal-pool address; enumerate its readers ([§8](#8-settling-a-disagreement-between-two-readings)) |
| Green suite, correct-looking code, and the user is right | An assertion states the current output rather than the rule | Grep the suite for the reported input before reading code ([§9](#9-on-a-bug-report-grep-the-test-suite-before-you-read-code)) |
| A bug report arrives phrased as a question about the game's design | A refusal happened before the list was built, so it is invisible | Disabled rows with reasons; audit filters upstream ([§10](#10-an-invisible-refusal-arrives-as-a-design-question)) |
| An obviously correct change silently removes a feature | Order dependence on a prerequisite that has not landed | Write the dependency next to both changes ([§12](#12-order-dependence-between-fixes)) |
| A previous session says "fixed and verified" and the report keeps coming | One fix was assumed to close a cluster | Verify each symptom independently; correct the earlier text in place ([§13](#13-verify-each-symptom-independently)) |
| Nobody can tell which part of the project log is current | The log has no lifecycle | One TODO table on top, everything else labelled finished history ([§14](#14-the-handoff-artefact)) |

---

## 16. Checklist

- [ ] The watch covered the **whole** struct and static area, counters were auto-silenced, and the
      candidate was confirmed by a **host write** rather than by correlation.
- [ ] The run that found the answer also **printed and retired** the historical candidates.
- [ ] Every zero-hit search is recorded **with its blind spot**, in the same note as the result.
- [ ] No feature was disabled on absence of evidence; the **claim** was downgraded instead.
- [ ] A "constant" offset with conflicting historical measurements was checked for being an **array**,
      and out-of-range indices are rejected rather than falling back to element 0.
- [ ] The **reader's** instruction sequence was disassembled; the anchor records offset, value **and**
      predicate; every sign-extended field is capped at **127** in host, engine and server alike.
- [ ] The number of paths producing the guarded event was **counted**, and the signal is one every
      path must pass through — validated against an existing trace, with counts stated.
- [ ] No attribution rests on a total; per-category and hot-id counters exist first.
- [ ] Host idle was measured before any further optimisation was proposed.
- [ ] Nothing you failed to observe is written as something that does not happen.
- [ ] Any disagreement between two readings was settled by something **countable**, and inherited
      output from an unverified investigation is labelled and re-derived.
- [ ] On a bug report: the suite was grepped for the reported input **before** any code was read.
- [ ] Refusals are visible as disabled rows with reasons, and every filter upstream of the list
      builder was audited.
- [ ] The same game rule in host, offline engine and server means the same set.
- [ ] Constraints proven on device live in a re-readable list, not buried in a session record.
- [ ] Order dependencies are written next to **both** changes, and every substitute carries its expiry
      condition at its definition site.
- [ ] Each reported symptom was verified independently, and any premature "fixed" was corrected
      **in place**.
- [ ] The log has one current TODO table, a newest-first reading order, stable ids, and a
      "what was not done" row per session.
- [ ] Every recorded fact that closes a door also records **what it invalidates**.
