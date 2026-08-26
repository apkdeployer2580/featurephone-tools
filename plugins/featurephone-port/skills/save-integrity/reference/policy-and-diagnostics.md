# Policy as pure functions, and the one log line

The rest of this skill is mechanism: how a record gets deleted, how a pre-image gets archived, how a
write finds the wrong slot. This file is about making the answer **provable instead of hopeful** —
where the decision is allowed to live, how to write down the arm that can destroy data, how to test
the parts that can destroy data, and what the log must say so the *next* save bug costs fifteen
minutes instead of a release.

Tags: **(A)** generic procedure · **(B)** worked example from the reference port, always introduced by
the method that produced it · **(C)** do not copy. `⚠` marks anything inferred, nothing run.

## Contents

1. [★★★ The accident: the rule was right, the condition was in a third file](#1--the-accident-the-rule-was-right-the-condition-was-in-a-third-file)
2. [One decision, one pure function](#2-one-decision-one-pure-function)
3. [Make the dangerous arm visible](#3-make-the-dangerous-arm-visible)
4. [Tombstones: deleting the wrong mechanism is not enough](#4-tombstones-deleting-the-wrong-mechanism-is-not-enough)
5. [Property tests for the parts that can destroy data](#5-property-tests-for-the-parts-that-can-destroy-data)
6. [Regression tests are named after the report](#6-regression-tests-are-named-after-the-report)
7. [★ `include!` protects against drift, not against a wrong premise](#7--include-protects-against-drift-not-against-a-wrong-premise)
8. [One end-to-end test, on a real record store, in a single test fn](#8-one-end-to-end-test-on-a-real-record-store-in-a-single-test-fn)
9. [Diagnostics: warn, the mode, and one verdict line](#9-diagnostics-warn-the-mode-and-one-verdict-line)
10. [What the diagnostics bought](#10-what-the-diagnostics-bought)
11. [The test tiers, and why the device tier cannot be skipped here](#11-the-test-tiers-and-why-the-device-tier-cannot-be-skipped-here)
12. [Recall discipline](#12-recall-discipline)
13. [Symptom → what to read](#13-symptom--what-to-read)
14. [Checklist](#14-checklist)

---

## 1. ★★★ The accident: the rule was right, the condition was in a third file

The reference port destroyed a real player's save slot while shipping a policy whose **rules were
correct**: an occupied character record may only be replaced by its own character. What destroyed the
data was one load-bearing condition — the latch deciding when the host may adopt a slot as "the live
character's slot" was gated on *"not currently in the shell"*, and that gate lived in a **third
module**, the one classifying which screen the guest was on. Neither the policy nor the storage hook
could see it, so no test could reach it.

> **REFUTED.** *"The policy is two clear rules and they are right, so the policy is safe."* Refuted on
> a device: the app-state value used for "shell" also covers the game's **online screens** — a fact
> the same repository had already established, months earlier, for an unrelated feature. On the route
> players actually used the latch **never armed**, the pin was never set, and the policy fell through
> to `Allow` unconditionally. **Why it was attractive:** the condition is obviously true ("do not
> adopt a slot from a menu cursor") and it belongs naturally beside the code that knows about
> screens. **Cost:** one shipped build, one destroyed character, one session spent proving the rules
> were fine.

The generalisation, and the reason this file exists **(A)**:

> **For a decision you cannot afford to get wrong twice, testability is not a quality nicety. The
> untestable file is where the fatal condition will hide, and "it is obviously true" is how it gets
> permission to live there.**

Consolidating into two dependency-free pure functions took that module's standalone selftest count
from **15 to 39** over the following sessions **(B)** — all under plain `rustc` on any host, which is
why they got written at all.

---

## 2. One decision, one pure function

**(A)** Two files, hard line between them. The policy file is dependency-free — no IO, no globals, no
clock — and holds one pure function per decision. Facts in as arguments, answer out as a value.

```rust
// save_policy.rs — dependency-free. include!d by save_policy_selftest.rs. NO IMPORTS HERE.
pub enum Verdict { Allow, Redirect(Slot), Refuse }

pub fn write_verdict(
    pin:        Option<Pin>,   // pinned slot + whatever identity was readable at load time
    target:     Slot,          // the slot the guest's record name names
    target_len: Option<u64>,   // on disk NOW: None = absent, Some(0) = zero-length, Some(n) = occupied
    redirect:   Option<Slot>,  // the redirect helper's answer, from the worker's observation
) -> Verdict;

// No `redirect` argument: a delete has no Redirect arm (§3).
pub fn delete_verdict(pin: Option<Pin>, target: Slot, target_len: Option<u64>) -> Verdict;
```

The IO half gathers facts, asks, logs, acts. It decides nothing.

```rust
pub fn write_check(name: &str, bytes: &[u8]) -> Check {          // vault.rs — the store hook
    // ⛔ DO NOT RE-IMPLEMENT ANY PART OF THE POLICY HERE, AND DO NOT ADD A CONDITION TO THIS
    //    FUNCTION. YYYY-MM-DD: exactly one condition ("not in the shell") lived in a third
    //    module for one release. It was wrong, no test could reach it, a player's slot died.
    let (pin, obs) = (pin_snapshot(), worker_observation());
    let v = save_policy::write_verdict(pin, slot_of(name)?, len_on_disk(name), redirect(pin, obs));
    log_verdict(name, &v, pin, obs);                             // §9 — one line, always
    v.into()
}
```

Every branch is then reachable from a standalone selftest, because no host, emulator or phone appears
in the decision; the **fact list is the argument list**, so a new condition cannot be smuggled in
without changing a signature and every call site; and the verdict is a value, so the caller logs it
*before* acting on it — which is what made the next bug cheap (§10).

⛔ **Delete gets its own function, not a flag on the write function.** A boolean parameter invites a
shared body, and the shared body is where a `Redirect` leaks into the delete path.

---

## 3. Make the dangerous arm visible

Enumerating the verdicts forces the dangerous arm into the open. Exactly one arm destroys data on
purpose: `target == pin.slot => Allow`. Write down why it is safe, in the source, above the match
**(A)**:

```rust
// WHY `target == pin.slot => Allow` IS SAFE: the serialiser has no source of bytes other than
//   the loaded character, so bytes aimed at the pin's own slot ARE the pinned character's save,
//   by construction. THEREFORE this arm is exactly as safe as the pin and no safer, and the pin
//   has ONE legal source: an OBSERVED CHARACTER LOAD. Five other sources were tried; each
//   destroyed a slot in the field (tombstones below; slot-identity.md §14).
// ⛔ ADDING A SIXTH PIN SOURCE IS NOT ADDING A HEURISTIC. It grants an authority to overwrite
//   the row it guessed — hours later, no error on screen, on the save nobody was looking at.
```

Two corollaries, only obvious once the arm is written down:

* **`delete_verdict` has no `Redirect`.** Redirecting a delete means deleting the pinned character
  because the guest aimed elsewhere — the worst outcome available. Allow-or-refuse only, with a
  selftest asserting exhaustively that it never yields a `Redirect`.
* **Do not "harden" this arm with an identity comparison.** The reference port's identity field was a
  *class* name: it legitimately reads zero on both sides, so `0 == 0` must count as a match —
  [slot-identity.md](slot-identity.md) §9.

> **REFUTED.** *"Fail closed: refuse the write when ownership is not proven."* Refuted by two player
> reports that were one bug — a withdrawn item disappeared, a deposited item duplicated — because the
> guest ignores the write's failure code while host-owned files have already committed. **Why it was
> attractive:** refusal is the correct instinct and it does protect the innocent slot; it also
> destroys the player's session, silently. Prefer `Redirect` where the owner is provable, keep
> `Refuse` for what you cannot prove ([loss-mechanisms.md](loss-mechanisms.md) §10).

---

## 4. Tombstones: deleting the wrong mechanism is not enough

**(A)** Removing a wrong mechanism removes the only warning that the mechanism is attractive. Leave a
tombstone at the site, with the deleted symbol names in it, so a `grep` for the old name lands on the
reason instead of on nothing.

```rust
// ⛔ TOMBSTONE — REMOVED: execute_pin_slot / note_execute_pin / PENDING_EXECUTE   (build N)
// WHAT IT DID: pinned the slot whose row the player executed on the online slot list.
// WHY IT LOOKED RIGHT: executing a row is the player saying "this character, please".
// WHY IT IS WRONG: the session's socket opens ONCE and entering the menu zeroes the cursor
//   first, so this edge could structurally only ever name slot 0.
// WHAT IT COST: one shipped build; the wrong slot overwritten in the field.
// ⛔ DO NOT REINTRODUCE. The pin's only legal source is an observed load.
```

The reference port's set **(B)** — five attempts at one question, each of which passed its tests and
shipped a build that destroyed a save. Narrative in [slot-identity.md](slot-identity.md) §14; what
belongs here is the shape of the record.

| attempt | trusted | cost |
|---|---|---|
| 1 | refuse any write to a foreign slot | saving stopped on the route players used; items evaporated and duplicated |
| 2 | latch the pin only while in play | "in play" excluded the online screens, so it never armed (§1) |
| 3 | pin the executed row, latched once | named one slot only; a second execute left the pin on an unloaded character |
| 4 | one load path, from the load arm's disassembly | there were two; the silent one destroyed a slot |
| 5 | **any observed character load, re-pinned every time** | held |

**Why the tombstones earn their length (A):** when a guard trusts a single scalar completely, the
invariant to state and test is **the scalar's provenance**, not the guard's clauses. Attempts 1–4 each
added a clause downstream of a wrong input, and the tombstones are the only artefact that stops
attempt six from being attempt three again.

---

## 5. Property tests for the parts that can destroy data

Rotation — choosing which archived generations to delete — is the one part of the vault that can itself
destroy data, so test it as **properties over pseudo-random corpora**, not as examples. Policy in
[save-vault.md](save-vault.md) §7; these five properties are what make it safe to *change* **(A)**:

1. **The newest generation is never deleted** — any corpus, any tier setting, any seed.
2. **Returned indices are unique, in range, and leave no more than the ceiling behind.**
3. **Convergence** — a second pass over the survivors deletes nothing; a non-convergent rotation
   erodes the archive one boot at a time.
4. **A long write loop cannot exceed the ceiling** — the reference test drives **500** save-then-
   rotate iterations **(B)**.
5. **With a pinned generation, the loop can never age the pin out.** The restore feature depends on
   this and nothing else enforces it.

Write the generator yourself, so the test still compiles under plain `rustc` with no dependency graph
([selftest-convention.md](../../../shared/selftest-convention.md)):

```rust
fn next(s: &mut u64) -> u64 { *s = s.wrapping_mul(6364136223846793005).wrapping_add(1); *s >> 33 }

#[test] fn rotation_never_deletes_the_newest_generation() {
    for seed in 0..256u64 { for n in 0..64usize {
        let c = gen_corpus(seed, n);        // MUST include zero-length generations
        assert!(!to_delete(&c, None).contains(&newest(&c)), "seed={seed} n={n}");
    }}
}
```

Sweep corpus sizes below **and far above** the ceiling, and put the seed and size in every assertion
message — a property failure you cannot replay is worthless. Two named example tests sit alongside,
encoding scenarios rather than invariants **(B)**:
`one_generation_per_day_survives_a_busy_session` (reports arrive a day late) and
`the_restore_scenario_costs_exactly_one_seat` (the recovery feature must not eat the archive).

---

## 6. Regression tests are named after the report

**(A)** A test named after a mechanism (`redirect_requires_fresh_observation`) dies with the mechanism.
A test named after a report outlives every rewrite, because the *sequence a player performed* is still
something your code must survive three mechanisms later. So: the name states the reported sequence in
the player's terms (which screen, which action, what was destroyed); the comment carries symptom, date
and build ([evidence-standard.md](../../../shared/evidence-standard.md) §5); and the test stays after
the fix lands — each attempt's report test being green under attempt five is the only evidence that
five is not a regression of two.

```rust
// ★ REPORT (YYYY-MM-DD, build N): entering the LOCAL "game start" slot list and loading another
// character caused the next save to land on the previously-pinned slot, destroying it. Attempt 4
// pinned only on the ONLINE load path; this sequence emits nothing on the wire.
#[test] fn local_game_start_load_repins_before_the_next_write() { … }
```

---

## 7. ★ `include!` protects against drift, not against a wrong premise

The rule that a selftest must `include!` the real source and never a copy exists to stop **drift** — a
mirror saying `160` where the shipping constant says `211`. It works, and it does nothing about the
sharper failure: **a test that genuinely exercises the shipping code and asserts the wrong answer.**
Two shipped bugs in the reference port were held green that way **(B)**, and in both the test's own
comment named its input incorrectly:

* A negative-list assertion contained a value that was in fact **valid**. A whole equipment category
  silently vanished from a feature, and the test defended its absence.
* An assertion read *"already pinned — the walk is not a load"*, where that exact input **was the
  reported destructive sequence**. Renaming it to describe the report made the failure visible.

> **REFUTED.** *"The selftests are green and they `include!` the real source, so this area is
> covered."* Refuted twice, above. `include!` guarantees the test constrains the shipping code; it
> guarantees nothing about whether the assertion's *interpretation of its input* is correct — and that
> interpretation is exactly what is in dispute when you have a bug. **Why it is attractive:** the test
> is real, it is green, and green reads as coverage.

**(A) The grep to run the moment you find a bug, before you fix it:**

```bash
rg -n --glob '*_selftest.rs' --glob '*/tests/*' -e '<the input that reproduced it>'
# then the assertions that pin an INTERPRETATION rather than a computation — the ones that can
# be green and wrong. Read every hit against the report you are holding.
rg -n --glob '*_selftest.rs' -e 'fn .*(_not_|_never_|_is_not|_refus|_invalid|_ignor)'
```

Every hit is either a test about to fail (good) or a test asserting the buggy answer, which is
**re-filed as a named regression test** per §6 in the same commit as the fix. Do not edit the assertion
and keep the name — the name is the artefact that stops the loop.

---

## 8. One end-to-end test, on a real record store, in a single test fn

The pure functions cover the decision; they cannot cover the wiring — that the hook is installed on
both `set` and `delete`, that a redirect really renames the landing directory, that the summary record
follows its payload, that a refusal cancels the deferred truncate. That needs a temp directory, the
real store on it, the real hook installed **(A)**. **Walk every clause of the redirect** in sequence,
asserting the on-disk outcome each time: matching identity redirects; a different observed identity
refuses; a suspended observation refuses; a mismatched cursor refuses; a stale observation refuses.
**Assert bytes on disk**, never a return value alone — the failure mode under test is a file.

⛔ **All of it in a single test function.** The pin, the worker's observation and the suspend flag are
**process-global statics** — not an accident, they model one running game — so two test functions on
different threads of the same binary interleave and produce a flake that reads as a policy bug.

**(C)** In the reference port this test only runs where the application crate links on the development
host, which is not every host. Let CI run whichever tier the runner supports; do not "solve" the split
by moving the e2e assertions into the pure tier, where they cannot see a filesystem.

---

## 9. Diagnostics: warn, the mode, and one verdict line

Save bugs are reported hours later, by somebody else, on a build you cannot attach to. The log line you
did not add is the reproduction you will not get. **(A)** Three rules, all learned expensively:

1. **Log save-path events at `warn`/`error`, never `debug`** — a release build's filter drops debug, so
   the reference port could neither confirm *nor exclude* its own truncate bug from a player's logs.
   Cost is about two lines per save; create-mode opens are rare, one per record actually written.
2. **Log the open MODE, not just the record name** — otherwise a read and a save produce *identical*
   name-only lines, which is the same as having no log.
3. **Print "known"/"unread", not the values** — the verdict depends on whether an identity was
   readable, not on what it said, and identity material does not belong in a log a player may send.

The storage layer's lines, one per mechanism in [loss-mechanisms.md](loss-mechanisms.md):

```
warn  store: open <Payload>_2 mode=CREATE — truncate of record 1 DEFERRED to first write
error store: record 1 of <Payload>_2 was NOT stored (<io error>) — deferred truncate CANCELLED
warn  store: <Payload>_2 closed with NO write — executing deferred truncate, record 1 deleted
```

One verdict line per character-record write, carrying **every fact the verdict used**, so the decision
can be re-derived from the log instead of guessed at:

```
info  save verdict: target=<Payload>_2 len=1808 pin=slot1/id:known obs=cursor2/id:known age=84ms   -> REDIRECT slot1
warn  save verdict: target=<Payload>_2 len=1792 pin=slot1/id:known obs=cursor0/id:known age=3140ms -> REFUSE (observation stale)
```

Required fields: **target name · length on disk · the pin and whether its identity is readable · the
observed cursor and whether *its* identity is readable · the observation's age · the verdict and its
reason.** `REFUSE` and `REDIRECT` go out at `warn` so they survive a filter; `ALLOW` may be `info`.
⛔ **These lines are not debug scaffolding — do not delete them when the bug closes.**

---

## 10. What the diagnostics bought

**(B)** The verdict line was added by an attempt that *failed* — attempt four, which shipped and
destroyed a save anyway. It then located the **next** save-destroying bug in about **fifteen minutes
from a device log**, with cause, timestamp and damage all visible in three lines: a read of the payload
record that no pin line followed (the silent second load path), the next write's verdict line still
naming the previous slot, and the victim record's length changing. The vault's generation names are the
same instrument one layer down — a directory listing alone placed a save-destroying regression **six
minutes after a specific build was installed** ([save-vault.md](save-vault.md) §6).

The rule **(A)**: when a subsystem's decisions depend on facts the log hides, the diagnostic is not a
debugging aid, it is part of the feature. Design the line before the fix.

---

## 11. The test tiers, and why the device tier cannot be skipped here

| tier | covers | cost |
|---|---|---|
| **Pure selftests** (`rustc --test`, no deps) | both verdict functions, every branch; rotation properties; the save-lock's release conditions; the load predicate's recogniser | seconds, any host |
| **Real-store e2e** (one test fn, temp dir) | the wiring: hook on set *and* delete, rename lands, summary follows payload, refusal cancels the truncate | seconds, hosts where the app crate links **(C)** |
| **Device walk** | all of the above plus the guest's own navigation, which no host test generates | a build, a phone, a session |

**The device tier cannot be skipped for this subject**, and every reason was paid for **(A)**:

* **All five attempts passed their tests and shipped.** A green suite is evidence about the model, not
  about the game.
* **The destructive windows only open under a real kill** — a truncate armed across an
  open→write→close sequence, or a non-atomic replacement, needs the process to die inside a
  sub-millisecond window: routine in the field, absent from any host test.
* **A load predicate's false-positive rate is only measurable on a full session trace.** The reference
  validation swept one whole device session: every create-mode open had **no** preceding read of the
  record it was about to replace, and **all eleven** reads of the payload record at id 1 were followed
  by a load (five by one path, six by a state transition into the world) — **zero** false positives
  **(B)**. That measurement is what licenses using reads as load signals at all.

> **REFUTED.** *"The load path was derived from the load arm's disassembly and it has tests, so the pin
> is correct."* Refuted by a device trace: a second load path existed that emits nothing on the wire —
> it reads the record, stores the buffer in a global, and lets world init deserialise it. The build
> shipped and destroyed a slot. **Why it was attractive:** the disassembly was *correct* about the arm
> it described; it simply was not the only arm. Static analysis finds *where*; only running the thing
> settles *what* ([evidence-standard.md](../../../shared/evidence-standard.md) §6).

Device procedure here: walk **each reported sequence** end to end, then read the verdict lines and the
vault listing rather than the screen — [device-verification.md](../../../shared/device-verification.md).

---

## 12. Recall discipline

**(A)** A build that can destroy save data is not "known-buggy", it is **withdrawn**. Write that where
the next session and the next player will both hit it:

* **Name the version RANGE, not the fixed build** — "every build up to and including `vcN` is
  withdrawn". A player is not on your newest build.
* **Name the SEQUENCE that destroys data**, in one clause. A player who knows *"loading another
  character from the local slot list can overwrite a save"* can keep playing and avoid it; one told
  only "there was a save bug" either stops or gambles.
* **Keep old recall notices when a new one lands.** The reference port carried two overlapping ranges
  from two different destructive mechanisms at once **(B)**; collapsing them to the newest would have
  told players on older builds they were safe from a bug that still reached them.
* **Recall notices live with the distribution record**, beside the build's artefacts —
  [distribution-and-recall.md](../../port-shipping/reference/distribution-and-recall.md).

⛔⛔ **Never tell anyone to uninstall.** The save records and the vault both live in app-private storage,
so an uninstall destroys **the evidence and the last copy of the character in one action** — and it is
the one irreversible thing a helpful tester or player does unprompted, *after* the loss report, when
the vault is all that is left. Always update in place (`install -r`). **A signing-key change is
therefore a save-destroying event**, because it forces an uninstall to update: put it in the release
checklist, not in the build configuration's footnotes.

---

## 13. Symptom → what to read

| symptom | read first | likely cause |
|---|---|---|
| a character is gone, slot reads empty | create-mode opens, executed truncates, `NOT stored` | a deletion, never corruption — [loss-mechanisms.md](loss-mechanisms.md) |
| one slot now holds another slot's character | the session's verdict lines; the vault listing for a length jump | pin wrong, or no pin at all — [slot-identity.md](slot-identity.md) |
| saves stop persisting after an online/container screen | grep the log for `REFUSE` during that flow | a guard refusing silently, or its arming condition excludes that screen (§1) |
| a fix passed its tests, shipped, destroyed a save again | the tombstones, then the pin's provenance | a clause added downstream of a wrong input (§4) |
| a green test defends the bug you just found | the test's comment, against the report | wrong premise, not drift (§7) |
| nothing in the log either way | the log level; whether the open mode is printed | debug-level save logging (§9) |
| the restore feature ate the backups | the vault listing before and after a restore | rotation seats and the pin — [save-vault.md](save-vault.md) §9 |
| a "saving, please wait" panel looks broken | whether it draws an OK band at all | an unanswerable modal must draw no button — [slot-identity.md](slot-identity.md) §13 |

---

## 14. Checklist

- [ ] Exactly **one pure function per decision** (write, delete), dependency-free, facts as arguments.
- [ ] **No condition anywhere else** — the IO hook gathers, asks, logs, acts, and says so in a `⛔` comment naming the accident.
- [ ] `delete_verdict` has **no `Redirect` arm**, asserted exhaustively by a selftest.
- [ ] The `target == pin` arm carries a written argument for **why it is safe**, plus the `⛔` about a sixth pin source.
- [ ] Every removed mechanism has a **tombstone**: symbols, what it did, why it looked right, why it is wrong, what it cost.
- [ ] Rotation is **property-tested** (newest survives · unique/in-range/bounded · convergence · long-loop ceiling · pin never ages out) with a seeded dependency-free generator, over corpora including zero-length generations.
- [ ] Every report has a **regression test named after the reported sequence**, with report and build in a comment above it.
- [ ] On finding a bug, the test corpus was **grepped for its input** and for interpretation-pinning assertions; any green-and-wrong test was re-filed.
- [ ] **One e2e test** on a real temp record store, single test fn, with a comment explaining that the pin is process-global.
- [ ] Save logging is at **warn/error**, prints the **open mode**, and emits **one verdict line per character-record write** with every fact used, as known/unread.
- [ ] The diagnostic lines are marked **not deletable**.
- [ ] Reported sequences walked **on a device**, and the load predicate checked for false positives against a **full session trace**.
- [ ] Any build that can destroy data is **recalled by version range and by sequence**, older recalls kept, and **nothing anywhere tells a player to uninstall**.
