# Guarding destructive operations

A port owns things its user cannot recreate: save records, host-owned store files, the generated
archive input that version control ignores, server-side backup rows, the working tree's history. Each
is reached through a primitive that *writes over* or *removes* bytes, and each will eventually be
reached by a path you were not thinking about when you wrote the guard.

This is the general doctrine for that class of guard, in any domain. The save-data case is worked end
to end in its own skill — [save-integrity](../skills/save-integrity/SKILL.md) — and this file does not
repeat its mechanisms; it cites it as the case study that produced most of the rules below.

Tags: **(A)** generic procedure · **(B)** worked example from the reference port, re-derive it ·
**(C)** do not copy. Conventions: [evidence-standard.md](evidence-standard.md).

## Contents

- [1. One decision, one pure function, one file](#1-one-decision-one-pure-function-one-file)
- [2. Refusing is not safe by default](#2-refusing-is-not-safe-by-default)
- [3. Fail-closed or fail-open is decided per entry point](#3-fail-closed-or-fail-open-is-decided-per-entry-point)
- [4. Enumerate every path that reaches the primitive](#4-enumerate-every-path-that-reaches-the-primitive)
- [5. Take the signal from the observed event, never a proxy](#5-take-the-signal-from-the-observed-event-never-a-proxy)
- [6. Count the paths that produce the event](#6-count-the-paths-that-produce-the-event)
- [7. Identity predicates that do not identify](#7-identity-predicates-that-do-not-identify)
- [8. Release-side primitives are never neutral stubs](#8-release-side-primitives-are-never-neutral-stubs)
- [9. Archive before overwrite](#9-archive-before-overwrite)
- [10. Make the dangerous arm visible, and leave tombstones](#10-make-the-dangerous-arm-visible-and-leave-tombstones)
- [11. Order dependence between two guards](#11-order-dependence-between-two-guards)
- [12. Instrument so that "nothing was written" is loud](#12-instrument-so-that-nothing-was-written-is-loud)
- [13. Symptom table](#13-symptom-table)
- [14. Checklist](#14-checklist)

---

## 1. One decision, one pure function, one file

> **(A) A decision that can destroy something unrecoverable is a single pure function in a single
> dependency-free file. Callers may only gather facts and ask it.**

Not elegance — this is about where the fatal condition is allowed to live.

**(B) The incident that produced the rule.** A storage-layer policy was *correct as designed* and had
provably never fired on the path where the damage happened, because one load-bearing condition — a
boolean meaning "not in the shell" — sat in a caller in a third file. No test could reach it, and it
happened to exclude an entire application state, so the policy returned "allow" unconditionally on the
route players actually used. Consolidating into two pure functions took that module's standalone
assertion count from **15 to 39**, all runnable on any host with a bare compiler.

> **(A) Coverage is not a property of the policy; it is a property of where the policy's conditions
> live.** A condition in a caller is invisible to the policy's tests *and* to its reader, and the
> failure is not a wrong rule — it is an unreachable one.

```
verdict(facts) -> Allow | Refuse | Redirect(destination)
```

* Facts in, verdict out: no I/O, no globals, no platform types, so a standalone selftest walks every
  branch anywhere — see [selftest-convention.md](selftest-convention.md).
* The caller only collects facts honestly and carries the verdict out.
* **(A) Each destructive verb gets its own function**, never a boolean on a shared one: a shared body
  is where a `Redirect` leaks into a delete path.

⛔ **Do not scatter the verdict again**, and say so in the module with the incident attached.

---

## 2. Refusing is not safe by default

> **(A) A guard that only refuses converts a data-loss bug into a silent no-write bug — and the
> application will not retry.**

Refusal feels conservative and is the version a reviewer approves. When the caller ignores the failure
code while the rest of the session has already committed elsewhere, refusal is destruction by another
mechanism, attributed to whatever feature was running.

> **REFUTED.** *"Fail closed — refuse the operation whenever ownership is not proven."*
> **Refuted by:** two separately-reported player bugs that were one root — "this store stopped
> autosaving" and "items vanish when withdrawn" — after a reject-only guard shipped. One screen that
> reset a cursor the writer reads disabled every save for the rest of the session; the host-owned file
> shrank on disk while the other half of the transaction lived only in RAM.
> **Why it was attractive:** refusing does protect the innocent destination. It also destroys the
> caller's session, silently, and the two costs are never weighed against each other.
> **Consequence if carried forward:** any storage guard with no redirect path ships this regression,
> and it arrives as an unrelated gameplay report.
> Mechanism: [loss-mechanisms.md](../skills/save-integrity/reference/loss-mechanisms.md) §10.

**(A) Where the correct destination is derivable, redirect the write to it and keep `Refuse` for what
you cannot prove.** A redirect is defensible only behind a gate set where **every clause fails closed
toward refusal**, one of them **freshness** — a stalled observer means a stale fact, and a stale fact is
the one thing a redirect must never act on. **(B)** The reference clause set: a pin exists; observed
identity equals the pinned one; observed target equals the write's target; the observation is younger
than **3 s** against a ~100 ms poll.

**(A) Outside storage the same doctrine reads: rewrite, do not drop.** **(B)** One request opcode had
two senders — a login sub-request and a destructive restore — byte-identical on the wire, so no server
could separate them. The fix runs on the client, which owns the record store and can answer *is this
slot occupied?*, and **rewrites the outbound frame into the sibling sub-request of the same fork**
rather than dropping it, because dropping leaves the client waiting for a reply forever. Dropping is
refusal; rewriting is redirect.

**(A) The tell for a refusing guard:** two unrelated-sounding reports arriving together, one a
disappearance and one a duplication — one transaction whose halves committed to different places.

---

## 3. Fail-closed or fail-open is decided per entry point

Neither is a house style. **(A) Decide per entry by which failure is worse, and write the answer with
its reason at the entry.** Both directions are correct in one codebase:

* **Fail closed where a false "safe" costs something unrecoverable. (B)** A client-side guard on a
  destructive restore asks one question about exactly the slot the frame names, on the caller's thread,
  and treats **anything unreadable or out of range as OCCUPIED**: a false "occupied" costs one recovery
  that does not run, a false "empty" costs a character.
* **Fail open where a false "unsafe" refuses a legitimate action. (B)** A failed read of an inventory
  snapshot means *no limit known*, never *at the limit* — at three call sites (capacity check,
  largest-safe-quantity, prompt floor), each restating it in its comment: an unknown snapshot bounds
  nothing, sets no floor, and can never refuse something the player already paid for.

> **(A) An entry with no stated failure direction has one anyway — whichever the last edit produced.**
> The reason belongs in the comment, or the next reader "makes it consistent" with the other entry.

---

## 4. Enumerate every path that reaches the primitive

> **(A) Before shipping the guard, enumerate every host path that reaches the destructive primitive —
> including the ones that only delete, the ones that truncate on open, and the failure-cleanup ones.**

Porters guard the write because the write is what they are thinking about. The other paths usually
belong to the emulator core or to a build tool, so they are not where the porter is looking. Three
recurring shapes:

* **A deferred truncate.** An open-for-create that defers its truncate until the first write turns
  *opening a record and never writing it* into a delete. **(B)** The mirror image shipped first: a
  failed write left the deferred truncate armed, so **a failed write became a delete**.
* **A whole-container delete.** A per-record guard is bypassed when the platform maps "reset" onto
  removing the directory.
* **A tool that rebuilds from pristine.** **(B)** Two archive tools operated on one artefact with
  different strategies — one rebuilt from the pristine source (discarding the other's edits), the other
  edited in place (leaving the first's member pristine when run alone). Either alone lost work; running
  one alone shipped ten consecutive dead builds. Resolved by a documented fixed order plus a check tool
  that byte-compares **only the member it solely owns**, with a comment naming the jointly-owned member
  as deliberately out of scope — a check that fails spuriously gets disabled, and that loses the gate.

**(A) The path table is a session artefact**, in the notes and not only in the code:

| path | reaches | guarded by | test |
|---|---|---|---|
| ordinary write | record set | `write_verdict` | selftest + end-to-end |
| deferred truncate on open | record set | cleared on **every** failure path | selftest |
| explicit delete | record delete | `delete_verdict` | selftest |
| container reset | whole directory | pre-image capture of every member first | end-to-end |
| tool rebuild from pristine | generated build input | documented order + `--check` | CI gate |

---

## 5. Take the signal from the observed event, never a proxy

> **(A) A state signal must come from the observed event itself. Before trusting a proxy, ask its
> cardinality: once per session, or once per action?**

That is answerable **statically**, from the call sites of whatever opens the resource, and it decides
whether the proxy can carry the signal at all.

**(B)** A guard pinned the user's chosen record on the edge where the network socket opened. The socket
opens **once per session**, and at that instant a menu entry had just reset the cursor to zero — so the
pin could structurally only ever hold zero, and every later choice left it frozen. The verdict's
`pin == target => Allow` arm then approved the destroying write **with no log line at all**: from the
verdict's point of view nothing unusual had happened. The replacement reads the value out of the bytes
of the event the guest itself emits, and re-stamps it on every occurrence.

> **(A) A proxy gets chosen because it is easy to observe from the host** — a statement about your
> convenience, not about the signal.

---

## 6. Count the paths that produce the event

> **(A) Having found the path that produces the event you watch, ask how many paths produce it. A guard
> on one of two paths destroys data on the next build.**

**(B) The most expensive shape in the reference port's delta:** a guard was rebuilt around a load that
is observable because it emits a network frame; the local start path loads the same records and emits
nothing, so it was invisible to the host and the same user's data was destroyed again one build later.

**(A) Enumerate the readers of the primitive, not the senders of the frame you happened to notice**,
then choose a signal every path must pass through — there, the record read itself.

**(A) Validate the new signal against an existing device trace and state the counts.** **(B)** 11 record
reads in the captured trace, all 11 genuine loads, zero false positives, and writes provably never read
(the create mode skips the seed read). A signal validated only against the reporter's reproduction is a
signal validated against one path.

---

## 7. Identity predicates that do not identify

> **(A) A field that is not per-instance cannot be a safety predicate. Verify what writes it and what
> indexes it before making it one.**

**(B)** A field long used as "the character's name" had one writer, called from one place (world
initialisation), indexed by a class byte into a class-name table. It therefore could not distinguish two
instances of the same class, and read **zero** in any session reaching the network screens without world
initialisation. Both properties then appeared in the destruction log: the stale pin was "held" because
the identity had not changed between two characters of the same class.

> **REFUTED.** *"Adding an identity comparison to the guard makes it strictly safer."*
> **Refuted by:** a same-session attempt and revert — the identity field reads zero in exactly the
> sessions where the destruction happens, so the tightening bought no safety and re-enabled the earlier
> silent-no-write bug. `0 == 0` counting as a match was then recorded as a deliberate decision.
> **Why it was attractive:** a second condition on a guard reads as monotonically safer.
> **Consequence if carried forward:** the extra clause changes only the rate of false refusals — the
> other failure you have already paid for.

**(A) Where no per-instance identity exists, safety comes from the provenance of the scalar the guard
trusts**, not from more clauses around it: state and test "this value may only be written by an observed
*X*". Worked out in [slot-identity.md](../skills/save-integrity/reference/slot-identity.md) §9, §14.

---

## 8. Release-side primitives are never neutral stubs

> **REFUTED.** *"For any unimplemented platform call, ship a logging no-op returning a plausible value
> so the guest keeps walking."*
> **Refuted by:** a report that the game "speeds up after connecting to the network". The timer-**cancel**
> call was a logging no-op while timer-**set** pushed a new event instead of replacing the keyed one, so
> every visit to a screen that arms a timer left one behind and the frame callback ran once per live
> timer — permanently N× speed after N visits.
> **Why it was attractive:** the rule is correct, and load-bearing, for query-side and set-side calls.
> **Actual mechanism:** a release-side call has no plausible return value, because the problem is the
> omitted side effect. Zero is not neutral; it is a leak.
> **Consequence if carried forward:** the symptom appears arbitrarily far from the stub in time and in
> subsystem. Second instance in the same core: a name-keyed record delete was also an upstream no-op, so
> "reset save" left the record and it reloaded on the next boot.

> **(A) For every `set` / `alloc` / `open` / `start` you implement, audit its `unset` / `free` /
> `close` / `stop` before shipping, and treat a release-side no-op as a known leak with a named symptom
> rather than as a placeholder.**

**(B) The verification that closes it** is a conservation check, not a screenshot: the per-second timer
fire count is identical before and after a round trip through the screen that arms the timer.

---

## 9. Archive before overwrite

> **(A) Capture the bytes about to be lost, at the one layer every write and every delete passes
> through, before the operation runs.** A guard prevents the losses you predicted; the pre-image archive
> covers the ones you did not.

The pattern generalises across record stores, generated build inputs and server rows:

* **Pre-image, not post-image** — the interesting bytes are the ones being replaced.
* **(A) Name each generation `<zero-padded timestamp>-<length>`.** Zero padding makes a lexicographic
  listing chronological for free, and the length turns the directory into a forensic timeline readable
  **without decrypting or copying anything**. **(B)** A listing alone dated a regression to six minutes
  after a specific build was installed, and showed the damage direction had reversed since the previous
  build.
* **★ (A) A DELETE is exempt from every optimisation the archival layer applies to overwrites** — the
  rate limit and the identical-content shortcut both. A delete is not one of a series; it is the last
  thing that will ever happen to that record, so a copy declined there can never be taken again. The
  rate limit exists for a good reason (a caller in a write loop must not turn the archive into a write
  amplifier), which is why it gets applied uniformly and why **the exemption is written next to it**.
* **(A) Rotation is the only part of an archival layer that can itself destroy data** — test it as
  properties over pseudo-random corpora, not examples: the newest generation is never deleted, a second
  pass over the survivors deletes nothing, a long write loop cannot exceed the ceiling.
* **(A) A restore archives the current bytes first and pins what it restored**, or the feature that
  undoes a data loss causes one.
* **(A) Provide an export path the user can reach on a release build**, designed so the **file names
  alone** carry the diagnostic value — the bytes may be unpullable where a listing still works.
* **(A) Where the archive lands is never a hand step.** **(B)** A tool dropped its backup inside the
  directory the build packages wholesale, mitigated by "move it by hand after every run"; the once it
  was forgotten, a shipped build carried ~1.76 MB of dead weight. Route the path through the one
  function that already owns backup-path knowledge — a hand step has a failure rate and fails silently.
* **(A) Server side the rule has a schema shape.** **(B)** A backup table keyed
  `PRIMARY KEY (account, slot)` has no history, so one upload of a damaged record destroys the last good
  one. Add an append-only history with a per-key cap and promote-by-timestamp, and make an identical
  replay create no row.

Mechanism, retention tiers and the restore protocol:
[save-vault.md](../skills/save-integrity/reference/save-vault.md).

---

## 10. Make the dangerous arm visible, and leave tombstones

Enumerating the verdicts forces the dangerous arm into the open: **(A)** exactly one arm destroys data
*on purpose*. Above the match, write why that arm is safe, what it is exactly as safe as, and the
prohibition that follows. **(B)** In the reference port that arm is exactly as safe as one scalar whose
only legal source is an observed load, and the comment says so — followed by
`⛔ ADDING A SIXTH SOURCE IS NOT ADDING A HEURISTIC`.

> **(A) Deleting a wrong mechanism is not enough** — removing it removes the only warning that it was
> attractive. Leave a tombstone at the site carrying the removed **symbol names**, so a `grep` for the
> old name lands on the reason rather than on nothing: what it did · why it looked right · why it is
> wrong · **what it cost** (builds, and what was destroyed in the field) · ⛔ do not reintroduce.

**(A) Why tombstones earn their length:** when a guard trusts a single scalar, each failed attempt adds
a clause *downstream of a wrong input*, and every attempt looks like progress. Tombstones are the only
artefact that stops attempt six from being attempt three again. **(B)** Five attempts at one question,
each passing its tests and shipping a build that destroyed data; the table of what each trusted and what
it cost is in
[policy-and-diagnostics.md](../skills/save-integrity/reference/policy-and-diagnostics.md) §4.

**(A) A substitute needs the same treatment.** Anything built because a capability was missing carries
its **expiry condition at the definition site**, with a date — the session that finally implements the
capability will be working on something else and will not think to look. **(B)** A host-side stand-in
outlived its own recorded reason by many sessions and was diagnosed instantly only because the comment
said why it existed.

---

## 11. Order dependence between two guards

> **(A) Two changes that are each correct in the final state can each be incorrect in the intermediate
> states. Nothing in a diff, a test run or a build shows this — only writing the dependency next to both
> changes does.**

**(B)** Suppressing a duplicate item the client delivered was correct for one of two item types and
wrong for the other, because for the second type that "duplicate" *was* the feature: a host-side reward
was triggered by using it. The first version suppressed both symmetrically and **silently deleted the
item's entire function**. Only after the host reward was retired — its own stated reason having expired
several sessions earlier — did the symmetric suppression become correct. The write-up states the
ordering as the essential fact: *retire the reward first, suppress the copy second; the reverse makes
the feature disappear quietly.*

**(A)** Adversarial verification caught it — the change was reviewed by asking *for which inputs is this
wrong?* rather than by re-reading it. Ordering hazards are also a reason not to squash a destructive
change and its prerequisite into one commit: a bisect cannot distinguish the two intermediate states.

---

## 12. Instrument so that "nothing was written" is loud

A guard's normal success is silence, and its worst failure is also silence. Separate them.

* **(A) One line per decision, from the layer that owns the decision**, naming the verdict and — on
  refusal — the gate that refused and its inputs. A worker's diagnostics can never explain a decision a
  UI thread made.
* **(A) Log destructive-path events at warn/error, and log the open MODE, not just the record name.** A
  release build's filter drops debug, and a read and a write produce identical name-only lines — so when
  a loss is reported hours later there is nothing to confirm *or rule out*.
* **(A) Print `known` / `unread` rather than the values** where the facts are identity material: the
  verdict's inputs are what you need, their content is not.
* **(A) Keep the diagnostic lines after the bug closes.** They look like debris and they are the next
  report's answer. **(B)** Lines added by one failed attempt located the *next* destructive bug from a
  device log in about fifteen minutes, with cause, timestamp and damage all visible.
* **(A) A gate that compares the wrong artefact is a silent gate.**

> **REFUTED.** *"The tool's own `--verify` mode proves the shipping input is current."*
> **Refuted by:** running the current tool against the pristine source reports success while the file
> that actually ships still holds the old content. Only a byte-comparison against the shipping file
> answers it — and when that gate was finally added it fired on its author's very next session,
> revealing that machine's shipping input was several sessions behind the repository.
> **Why it was attractive:** the tool knows the format, so its own verifier feels authoritative.
> **Consequence if carried forward:** edits revert silently; ten consecutive builds shipped dead.

Related: a diagnostic that silently produces no output looks exactly like a diagnostic reporting no
problems — [evidence-standard.md](evidence-standard.md) §4, correction 4.

---

## 13. Symptom table

| symptom | cause | first move |
|---|---|---|
| Writes stop, or a result vanishes on restart, soon after a guard was added | The guard refuses; the caller never retries and its other half committed (§2) | Redirect to a provable destination; keep refuse as fallback |
| Two unrelated reports, one a disappearance and one a duplication | One transaction whose halves committed to different places (§2) | Look for a refusing guard, not for two bugs |
| The guard is correct and provably never fired on the damaging path | A load-bearing condition lives in a caller, in a third file (§1) | Move the verdict into one pure function; count reachable branches |
| Guarded, verified against the reporter's repro, shipped — destroyed again next build | Two paths produce the event; you guarded one (§6) | Enumerate readers of the primitive; pick a signal all paths pass |
| A pin or latch is frozen on one value and silently approves everything | The signal came from a proxy edge with once-per-session cardinality (§5) | Ask the cardinality; re-stamp from the observed event |
| The safety check "matches" for exactly the users who report the problem | The identity field is a type name, or reads zero in that session (§7) | Verify writer and index; locate safety in the scalar's provenance |
| A failed operation turns into a deletion | A deferred truncate cleared only on success (§4) | Clear it on **every** failure path; add the row to the path table |
| Something runs faster or heavier with each visit to one screen | A release-side no-op paired with an implemented acquire (§8) | Audit `unset`/`free`/`close`/`stop`; verify by conservation count |
| A previous session's archive edit is missing from the shipped build | Two tools rebuild the same artefact and overwrite each other (§4) | Documented order + `--check` on the solely-owned member |
| A corruption is real but undatable because the payload is encrypted | Archived generations carry no identifying metadata (§9) | `<timestamp>-<length>` names; the listing is the evidence |
| The recovery feature consumed the copies it existed to restore | Restore ran through the ordinary rotation (§9) | Archive current bytes first, pin the restored generation |
| An obviously-correct change silently removed a feature | Order dependence with a prerequisite that had not landed (§11) | Write the dependency next to both changes |

---

## 14. Checklist

- [ ] The verdict is **one pure function per verb** in one dependency-free file; no condition of the
      decision lives in a caller, and a standalone selftest walks every branch, refusals included.
- [ ] `Refuse` is not the only outcome: where the destination is provable the verdict **redirects**, and
      every redirect clause fails closed toward refusal — including a freshness clause.
- [ ] Each entry point states **fail-closed or fail-open, and why**, decided by which failure is worse.
- [ ] The **path table** exists: writes, deletes, truncate-on-open, failure cleanup, container removal,
      and any tool that rebuilds the artefact — each row naming its guard and its test.
- [ ] The signal comes from the observed event; any proxy's **cardinality** was checked, the paths
      producing that event were **counted** from the primitive's readers, and the signal was validated
      against a **captured device trace** with true/false-positive counts.
- [ ] No safety predicate rests on a field whose writer and index were not verified.
- [ ] For every acquire primitive implemented, its release primitive was audited; no release-side
      logging no-op is left unlabelled.
- [ ] A **pre-image archive** sits at the layer every write and delete passes, generations are named
      `<timestamp>-<length>`, and the **delete path is exempt** from the rate limit and the
      identical-content shortcut.
- [ ] Rotation is property-tested; restore archives-then-pins; an export path a user can reach on a
      release build carries its diagnostic value in the **file names**.
- [ ] The dangerous arm is annotated with why it is safe and what it is exactly as safe as.
- [ ] Every removed mechanism left a **tombstone** (symbols, why it looked right, why it is wrong, what
      it cost, ⛔ do not reintroduce), and every substitute carries its **expiry condition** with a date.
- [ ] Ordering hazards between this change and its prerequisite are written next to **both** changes.
- [ ] One log line per decision, at warn/error, from the owning layer, naming the gate that refused; the
      open mode is logged; identity material prints as `known`/`unread`.
- [ ] Any staleness gate byte-compares **the artefact that actually ships**, not a rebuild of the source.
