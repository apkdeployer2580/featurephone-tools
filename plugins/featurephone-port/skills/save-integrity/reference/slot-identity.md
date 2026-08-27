# Which slot does this write belong to?

A save write reaches the host's record store as `(name, record id, bytes)`. The name carries a slot
suffix, the bytes are opaque to the host **(B)** — ciphertext in the reference port; §1 says how to
settle that on your own title — and **nothing in the request names a character.** Deciding whether
those bytes belong in that slot is the hardest question in this subject: it cannot be answered from
the request, it has to be answered inside a guest storage call on the emulator thread, and both
wrong answers cost a player a character. The reference port answered it five times and shipped four
wrong answers to real people. §14 preserves all five, in order, with the field report that refuted
each — **read §14 before you add a clause to a policy that has already failed once.**

Tags: **(A)** generic · **(B)** worked example from the reference port, re-derive it ·
**(C)** do not copy.

## Contents

- [1. The question, and why it is the hardest thing in the subject](#1-the-question-and-why-it-is-the-hardest-thing-in-the-subject)
- [2. ★★★ The slot index in the record name is a UI cursor](#2--the-slot-index-in-the-record-name-is-a-ui-cursor)
- [3. What the guest does next](#3-what-the-guest-does-next)
- [4. The pin: one scalar, and its provenance is the invariant](#4-the-pin-one-scalar-and-its-provenance-is-the-invariant)
- [5. Load signals that work](#5-load-signals-that-work)
- [6. ★ There is more than one load path](#6--there-is-more-than-one-load-path)
- [7. Validating the predicate against a device trace](#7-validating-the-predicate-against-a-device-trace)
- [8. Non-signals, each with what it cost](#8-non-signals-each-with-what-it-cost)
- [9. Identity fields lie](#9-identity-fields-lie)
- [10. The three verdicts, and why delete has no Redirect](#10-the-three-verdicts-and-why-delete-has-no-redirect)
- [11. Redirect: what licenses it, and four fail-closed clauses](#11-redirect-what-licenses-it-and-four-fail-closed-clauses)
- [12. The same-tick race and the synchronous suspend](#12-the-same-tick-race-and-the-synchronous-suspend)
- [13. Freezing the UI while a save settles](#13-freezing-the-ui-while-a-save-settles)
- [14. The five attempts, in order](#14-the-five-attempts-in-order)
- [15. Symptom → cause table](#15-symptom--cause-table)
- [16. Checklist](#16-checklist)

---

## 1. The question, and why it is the hardest thing in the subject

Three properties, together, make this different from every other guard in a port.

**The request under-determines the answer (A).** The slot suffix comes from guest state, and §2 is
about the fact that that state is frequently a *menu highlight*. What the *payload* tells you is a
property of your title rather than of the platform, so settle that before you design anything
downstream **(A)**: dump one record straight after a save and look at it — uniform high entropy, no
header, no strings means encrypted ([recipes.md](../../guest-re/reference/recipes.md) Recipe A
step 9); readable field values mean plaintext.

* **(B)** In the reference port the payload is encrypted, so the host cannot read a name out of it —
  see [save-vault.md](save-vault.md) for why that is an advantage everywhere else and a handicap
  only here. A record's one readable property is then its **length on disk**, which says whether a
  record exists and, across generations, whether it changed *character* — never whose it is.
* ⚠ **INFERRED.** If your records are **plaintext**, you hold a signal the reference port did not: the
  incumbent record's identity bytes and the incoming buffer's can be compared inside the same
  storage call. Verify that field the way §9 verifies the reference port's — enumerate its writers,
  because a name-shaped field in a record can be a *class* name too — and treat it as
  **corroboration of the pin, not a verdict on its own**: a deliberate "overwrite this slot"
  produces the same mismatch as a drifted cursor. ⚠ INFERRED — no port here has run it; what would
  settle it is one port instrumenting that comparison across a session of real loads and saves and
  reporting its false positives the way §7 does. What it buys outright, if it holds, is a
  **per-instance** identity, closing §9's "two characters of the same class are indistinguishable"
  hole, and a [save-vault.md](save-vault.md) §6 generation name that carries *who* as well as
  *how long*.

**Both wrong answers destroy something (A).** Writing to the wrong slot replaces a character the
player was not looking at: no error on screen, discovered days later. Refusing is *not* the safe
default — the guest ignores the write's failure code, so a refusal is invisible to the game while
host-owned state that already committed carries on. The reference port's reject-only build produced
two reports that read as gameplay bugs — a withdrawn item disappeared (the host-owned container file
shrank, the receiving inventory was never persisted) and a deposited item duplicated (the container
grew, the inventory rolled back) — which were one storage-layer refusal **(B)**. Mechanism in
[loss-mechanisms.md](loss-mechanisms.md) §10; the rule here is that "fail closed" is the right instinct
and the wrong implementation.

**The decision repeats dozens of times per session (A).** The game saves on deposits, withdrawals,
purchases, page turns and network acknowledgements (§3), so a policy that is right 95 % of the time
mis-aims several writes per visit.

The decision also runs **inside** a guest storage call — bounded: one small read, at most one small
write, no network — and it must be *observable*, because a log line written at the moment of the
decision is the only way you will ever diagnose a wrong one
([policy-and-diagnostics.md](policy-and-diagnostics.md) §9).

---

## 2. ★★★ The slot index in the record name is a UI cursor

> **The `_<n>` suffix on a save record is frequently a slot-LIST CURSOR, not the identity of the
> loaded character.** `HIGH / CONFIRMED`

### The proof procedure (A)

Four steps, in this order. Each one is cheap; the conclusion is only sound with all four.

1. **Find where the writer gets the suffix.** Locate the record-writer routine
   ([recipes.md](../../guest-re/reference/recipes.md) Recipe A finds it from the serialiser) and read
   its prologue. **(B)** In the reference port it took **no slot argument at all**: it read one word
   out of the god object and built the name with a `sprintf("%s_%d")`-shaped call. A writer with no
   slot argument is the entire finding — the slot is ambient state.

2. **Enumerate every writer of that word.** Static enumeration over the whole image, covering *both*
   Thumb idioms for materialising the offset — see
   [static-toolkit.md](../../guest-re/reference/static-toolkit.md); missing one lets you "prove" a
   live field unused. **(B)** The writers were: the title and online menu rows (which set it to
   **0**), the up/down/cancel arms of **both** slot-list screens, and one modal handler. All inside
   the shell UI. **Not one of them in the load path, the save path or the world.**

3. **Check whether anything ever unloads the character.** Find the character-reset routine and
   enumerate *its* callers. **(B)** Exactly one caller, a network reply — so navigation never
   unloads, and the live character stays live while the cursor moves under it.

4. **Close it dynamically.** Replay the player's reported sequence with the record store logging the
   name it is about to write ([policy-and-diagnostics.md](policy-and-diagnostics.md) §9). Steps 1–3
   are one derivation; this is the second, and
   [evidence-standard.md](../../../shared/evidence-standard.md) §3 requires both. The reference
   port's traces showed the suffix following the highlight.

### What follows

Step 2 plus step 3 give you the mechanism in one sentence: **moving a highlight in a slot list
re-aims every subsequent save in the session at another character's record.** No corruption, no
serialiser bug, no timing window — the game writes a correct save into the wrong file.

**The fix cannot live in the guest (A).** The obvious repair is to poke the cursor word back to the
right value; that word is *owned* by the slot-list UI, and rewriting it breaks the screen the player
is standing in. Everything below is therefore a **storage-layer** policy: the host changes where the
bytes land, never what the guest believes (§11).

---

## 3. What the guest does next

Three behaviours turn §2 from a curiosity into a constant hazard. Look for all three in your title.

**Menu entry zeroes the cursor (A).** The title and online menu rows wrote **0** into that word on
entry **(B)**, so every character who does not live in slot 0 is mis-aimed *from the moment they open
the online menu*, before touching a list. That is why the bug presents as "saves stopped working
after I visited the online screen" rather than as anything save-shaped.

**The cursor moves on navigation, not on confirmation.** The writers found in step 2 are the up/down
arms, so the word has already changed by the time the player presses anything, and a refusal path
("that slot is empty") has no compensating write. ⚠ INFERRED from the writer enumeration; settle it
on your title by logging the word from the worker while walking the list and backing out.

**The screens most exposed to the bug save constantly.** The reference online container screen
rewrote both save records on every deposit, withdrawal, purchase and page turn, plus once per backup
acknowledgement — that handler calls the game's own save routine, so saves happen the player never
asked for **(B)**. The exposure is the whole visit, not a moment.

**Two record families, one unit (A).** Guests commonly keep a large payload record and a tiny summary
record per slot, and the slot list's occupancy decision reads **only the summary** — a lost summary
makes an intact character invisible. Reference sizes **(B)**: payload ~1.4–1.9 KB, summary 32 B on
disk. Both come from the same writer and carry the same drifted suffix, so every mechanism below
treats them as one unit: vault together, redirect together, restore together.

---

## 4. The pin: one scalar, and its provenance is the invariant

The host keeps one small piece of state — **the pin**: which slot the live character occupies, plus
whatever identity it could read at the time.

```rust
struct Pin { slot: u8, identity: u32 }   // identity: a hash, 0 = "could not read"
```

The policy's fast path is `target == pin.slot ⇒ Allow`. Which means:

> **★★★ A pin written by a guess is not a heuristic. It is an authority to overwrite the slot it
> guessed.** `HIGH / CONFIRMED across four shipped builds`

So the invariant you state, test and defend is **not** a property of the guard's clauses but of the
scalar's *source*:

> **The pin may only be written by an OBSERVED CHARACTER LOAD. Nothing else may write it, ever.**

Write that at the definition site, with the dangerous arm next to it — "`target == pin ⇒ Allow` is
safe *only* because the pin is exact" — because that sentence is what stops the next contributor
adding a sixth source ([policy-and-diagnostics.md](policy-and-diagnostics.md) §3). Every rejected
source gets a tombstone comment naming it and what it cost (§8).

The pin is process-global by nature, with one testing consequence: the end-to-end test over a real
temporary record store is **one** sequenced test function, not several
([selftest-convention.md](../../../shared/selftest-convention.md)).

---

## 5. Load signals that work

Two shapes; take both where both exist. 5a is available only to a title whose load arm talks to a
server, and 5b is the one that has to work either way — but "the store sees every read" is not the
same as "every read is a load", so it is still counted against the load paths (§6) and validated for
false positives (§7, §8) **(A)**. Each funnels into one function — `note_slot_loaded(slot, how)` —
so there is exactly one writer of the pin in the host **(A)**.

### 5a. An outbound frame the load arm itself builds

Some load arms announce themselves on the network. **(B)** In the reference port the online slot
list's confirm arm read the record, uploaded it to the server as a backup, *and* called the
deserialiser in the same breath — so that upload frame is a load announcement, and **the slot rides
in the frame's own bytes.** That is the property that matters: a byte in the frame is independent of
the cursor word and is rebuilt on every execute, so the pin refreshes on every load.

Recognise it with a pure function over the frame in the host's send path, unit-tested against a
captured frame: match the opcode, read the slot out of the payload. ⚠ Do **not** add an equality
check on total frame length unless you measured that length **on the wire** — a length taken from the
guest's blob builder is not the framed length, and a recogniser gated on the wrong one never fires at
all. A dead recogniser and a working one look identical from outside.

### 5b. A read of the payload record

The record store sees every read. Treat **a read of the payload record, at the record id your writer
actually uses, whose length on disk falls inside a plausibility band** as a load of that slot
**(A)**. Both halves are measured, and neither is copied: the id comes out of the §7 instrumentation
run — the same log that records every open with its mode also names the id — never out of the
disassembly ([evidence-standard.md](../../../shared/evidence-standard.md) §6). **(B)** In the
reference port it was id 1 (§7). Key the predicate on the wrong id and it never fires — and a dead
recogniser looks exactly like a working one (§5a). And a band, not an equality: a serialised save length is
content-dependent, and a measured value is never an invariant
([patching.md](../../guest-re/reference/patching.md) §6).

This is the signal that covers the load paths which announce nothing (§6), and the reason the policy
belongs in the record store: **it is the only layer that sees all of them.** The two signals cannot
disagree — both write the same pin through the same function, and the later observation wins because
it is the later load — but see §11 for the unmeasured window between this read and the deserialise.

---

## 6. ★ There is more than one load path

> **★ Blocking one load path and shipping it re-breaks the data in a new place.**
> `HIGH / CONFIRMED by device trace`

### Count the load paths before you choose a signal (A)

Find the deserialiser (Recipe A), then **count the sites that reference it by literal-pool
reference, not by call graph.** In these images inter-module calls are commonly a register-indirect
trampoline — a literal load of `addr | 1` followed by a branch through a thunk — so a `bl` graph
shows nothing, and two competent readers can disagree about what a given `bl` even calls. Compute
the literal's address from the load instruction and look at who reads it. **(B)** That gave exactly
**two** references in the whole image: the online slot list's confirm arm, and world initialisation.

Those two are not symmetrical, and that asymmetry is the trap:

| load path | what it does | what the host sees |
|---|---|---|
| online slot list | reads the record → uploads it → deserialises, all in one arm | an outbound frame with the slot in its bytes (§5a) |
| local "game start" slot list | reads the record → stores the buffer in a global → sets the app state; **world init** deserialises later | **nothing on the wire** — only a record read (§5b) |

### The refutation

> **REFUTED.** *"The load signal is the outbound frame the load arm builds. It is derived from the
> load arm's own disassembly, the recogniser is pure and unit-tested, and the slot rides in the
> bytes so it cannot drift."*
>
> **Refuted by** a full device trace of a player's reported sequence. The local slot list sends
> nothing: that load was invisible to the host, the pin stayed on the previously loaded character, and
> the next save was redirected onto the other slot and destroyed it. The trace showed the whole causal
> chain in three log lines.
>
> **Why it was attractive:** the reading of the load arm was *correct*, the tests were real, and the
> signal genuinely cannot drift. The claim was not wrong about the path it described — it was wrong
> about being the only path. Static analysis recovers shapes, not enumerations you did not ask for.
>
> **Carried forward:** a build that destroys saves, shipped with confidence, recalled.

So ask one question *before* choosing a signal: **"how many ways can this game load a character?"**
The reference answer was two, and asking versus not asking was one release versus three.

---

## 7. Validating the predicate against a device trace

A load predicate with **false positives** moves the pin onto a character that is not loaded — which
is exactly the authority-to-overwrite failure of §4. So validate it the expensive way, once (A):

1. Instrument the record store to log every open (**with its mode**), every read of the payload
   record and every write verdict. Warn level, not debug — a release log filter drops debug
   ([policy-and-diagnostics.md](policy-and-diagnostics.md) §9).
2. Play one full session on a device, deliberately walking the slot list, *both* load paths, the
   online screens and a save.
3. Check **every** firing of the predicate against what the session actually did.

**(B)** In the reference session: every create-mode open had **no preceding read** of the record it
was about to replace, and **all eleven** reads of the payload record at id 1 were followed by a load —
five via the online path, six via a state transition into the world. **Zero false positives.**

### What licenses using a read as a load signal (A)

> **A write never reads.** A create-mode open skips the record seed, so no write is preceded by a
> read of the record it replaces.

**Check this on your own core before relying on it.** It is a property of *your* record store, not of
the platform, and it is in tension with a rule you also need: non-create opens should seed the handle
from the existing record so seek-and-overlay writes preserve unrelated bytes
([wipi-c-recipes.md](../../emulator-core/reference/wipi-c-recipes.md)). If your create-mode open also
seeds, reads *do* precede writes and this signal is poisoned. Restrict the load signal to the read
shape a write can never perform, and pin that with a test named after the property.

"I read the disassembly" is not sufficient evidence for a predicate this dangerous
([evidence-standard.md](../../../shared/evidence-standard.md) §6). The trace is.

---

## 8. Non-signals, each with what it cost

Every row here was believed by a competent person, and four of them reached players.

| candidate source | why it looks right | why it is wrong | cost |
|---|---|---|---|
| **the slot-list cursor** | it is literally the number in the record name | it *is* the bug (§2); and menu entry zeroes it (§3) | the original loss class |
| **a session-scoped socket's first-open edge** | "the player just went online, so they just chose a slot" | the socket opens **once per session**, and that moment is immediately after the menu row zeroed the cursor — so the edge can *structurally* only ever name the first slot | a shipped build whose pin was always slot 0 |
| **"the player executed a row", latched once** | the execute is a real user intent | a *second* execute leaves the pin naming a character that is no longer loaded | a shipped build that mis-aimed after the second load |
| **an identity becoming readable later** | the identity changed, so surely a character was loaded | that is the *same* character becoming readable, not a load — the field is written late (§9) | a shipped build whose pin walked one slot sideways |
| **the summary-record scan the slot list performs** | the slot list reads a record per slot; reads mean loads (§5b) | the scan touches **every** slot on **every** entry to the list, so the pin walks the whole list each time it is drawn | refuted from the hook itself |

Notes on the last two, because they are the subtle ones.

**An identity appearing is not a load (A).** The identity field is populated by world initialisation
(§9), so it can go from unreadable to readable with *no load happening* — for instance when a session
that went straight to the online screens finally enters the world. A pin source watching for identity
changes fires there and re-aims at whatever the cursor currently says.

**The summary scan is refutable before you ship it (A).** The app-state transition that draws the slot
list scans every slot's summary record to set its occupancy flag. Instrument the pin, open the slot
list, watch it walk — the record store's own hook makes it visible in one run. Generalise the check:
**for every candidate pin source, ask what the slot-list draw does to it**, because the slot list is
the one screen guaranteed to be involved in every report.

⛔ **Do not add a pin source that is not an observed load.** If you are tempted, ship a log line
instead and read what the device says. Then leave a tombstone comment naming the source you rejected
and what the trace showed ([policy-and-diagnostics.md](policy-and-diagnostics.md) §4).

---

## 9. Identity fields lie

A field that looks like the character's name may be the character's **class** name.

### Verify it (A)

Enumerate the writers. Find each writer's callers. Find where its index comes from. Then check
whether the save deserialiser touches the field at all.

**(B)** In the reference port: exactly one writer, exactly one caller (**world initialisation**), the
index a class byte into a class data member, and **the deserialiser never touches the field.** Two
consequences fall straight out:

1. **Two characters of the same class are indistinguishable.** Established when a device log showed
   the pin being *held* across a genuine load "because the identity did not change" — the reporter's
   two characters shared a class.
2. **A session that never entered the world reads zero.** Title → online → container is a real route,
   and on it the field is 0 all session.

### Therefore `0 == 0` must count as a match

> **REFUTED.** *"Tighten the redirect: require a non-zero identity on both sides before landing a
> write in another slot's record."*
>
> **Refuted by** reverting it. The tightened build reproduced the earlier item-evaporation
> regression (§1) and protected nothing, because the field is a class name written only by world
> initialisation — so the pairing it refused is the pairing the player's actual flow produces.
>
> **Why it was attractive:** it is the natural second-order fix after a slot is destroyed. Add
> another condition; conditions are safety. And the field contains a plausible-looking string, which
> reads as identity.
>
> **Carried forward:** a real, reproduced data-loss bug traded for an imaginary one.

The rule (A): **tighten on the pin's provenance, not on identity** — which is why §11's clause 1
asks only that a pin *exist*. Safety comes from the pin being
exact on every observed load (§5–§7), not from clauses downstream of a scalar you do not trust. An
identity check built on a misread field is security theatre that reads as safety and fails silently
in the most common case — and it is
[evidence-standard.md](../../../shared/evidence-standard.md) §3 in miniature: "the field contains a
plausible-looking string" is not a derivation.

---

## 10. The three verdicts, and why delete has no Redirect

One pure function per decision, taking facts as arguments, with the caller only gathering facts and
carrying the verdict out (A). Enumerating the verdicts is what makes the dangerous arm *visible*.

```rust
enum Verdict { Allow, Redirect(u8), Refuse }

// target_len: length on disk, None = no record there.  redirect: §11's answer, already computed.
fn write_verdict (pin: Option<Pin>, target: u8, target_len: Option<u64>, redirect: Option<u8>) -> Verdict;
fn delete_verdict(pin: Option<Pin>, target: u8, target_len: Option<u64>) -> Verdict;
```

* **Allow** — the target is the pin's own slot. Safe **only** because the pin is exact.
* **Redirect(slot)** — a foreign target whose real owner is *proven* by §11's clauses.
* **Refuse** — everything you cannot prove. Refusal is not the default; it is the residue.
* **No pin at all** — allow only an empty record (a new character's first save); refuse anything
  occupied.

**Occupied versus empty foreign records is a policy choice you must make explicitly.** The reference
port refuses **both**, having no permission to trade a destroyed character for a duplicated one
**(B)**. Decide, write the sentence down, and pin it with a test — whichever way you choose, the
other choice's failure mode will be reported to you eventually.

### Delete has no Redirect arm (A)

Redirecting a delete deletes the character you were protecting. Permit a delete when it targets the
pin's slot or when the record is already empty; refuse otherwise — and have a selftest assert
exhaustively that the delete function **never** returns a `Redirect`.

* **A whole-database delete is a different host call and must not be routed through this verdict.**
  In the reference port the guest's "reset this slot" mapped to a recursive directory removal that
  bypassed the per-record gate entirely **(B)**. Gate that path by *archiving first*, not by refusing
  — a player-confirmed reset is allowed. See [loss-mechanisms.md](loss-mechanisms.md) §7 and
  [save-vault.md](save-vault.md) §8.
* **The refusal must be visible.** A refused write returns a failure the guest ignores, so the only
  trace is your log line ([policy-and-diagnostics.md](policy-and-diagnostics.md) §9) and the only
  symptom is a gameplay bug (§1).

---

## 11. Redirect: what licenses it, and four fail-closed clauses

A redirect is licensed by **two** things, and only one of them is in this section.

The structural half gets you halfway (A): the serialiser is fed from whatever character is **live**,
so bytes aimed at a drifted slot are the *live* character's save. ⚠ In the reference port that was a
rationale, never a derivation — the serialiser was located by
[recipes.md](../../guest-re/reference/recipes.md) Recipe A, which finds *where* it is and not what it
reads, and nobody ever enumerated its loads. Settle it on your own title by disassembling the
serialiser and listing every source it loads from: a staged load buffer parked in a global, or a
restore payload held between arrival and the swap, would break it outright. ⚠ A second window has the
same shape and is likewise unmeasured: if your load signal is the record *read* (§5b), the pin names
the incoming slot from the moment of the read, while the live character is still the outgoing one —
**(B)** in the reference port the read and the world initialisation that deserialises are separated
by a state transition — and §10's `target == pin ⇒ Allow` would pass a write
of the outgoing character into the incoming slot. A store log that timestamps the read, the swap and
every verdict (§7, and [policy-and-diagnostics.md](policy-and-diagnostics.md) §9) would settle
whether any write lands there.

The other half is not structural at all. Getting from *the live character* to **the pinned
character** is §4's invariant — the pin is exact because an observed load is its only writer — and
§6's — you have counted *every* load path. **The four clauses below do not establish that and cannot;
they filter an already-exact pin.** Landing a write in the pinned slot is a correction of an address
only while the pin is exact; while it is not, it is the most confident data-loss mechanism in the
port. **So: ship the redirect once §4 and §6 hold, and refuse until they do.** Refusing on principle
is not the safe default — it is §1's and §14's attempt 1, the answer that stopped a container session
from saving at all.

> **REFUTED.** *"The redirect is sound as long as its four clauses hold: a pin exists, the
> observation is fresh, the identities match, and the observed cursor equals the write's own
> target."*
>
> **Refuted by** the device trace of the build that shipped with exactly those four clauses: all four
> held, and the redirect still landed one character's save on another character's slot. The pin was
> stale because a second load path announces nothing (§6), and the identity clause could not tell the
> two characters apart because that field is a class name and the player's two characters shared a
> class (§9). The build was recalled.
>
> **Why it was attractive:** every clause is individually correct, individually fails closed, and
> individually has a test. A set of sound clauses reads as a proof.
>
> **Limits of the search that produced this:** the clauses were tested against a single-character
> account, which cannot exercise either failure — a second load path needs a second route into the
> world, and a class-name collision needs two characters of one class.
>
> **Carried forward:** §14's meta-lesson — when a guard trusts one scalar completely, the thing to
> state and test is the scalar's *provenance*, not the guard's clauses.

Each clause fails **closed** — a failure falls back to §10's Allow/Refuse decision, never to the
drifted aim — and none of them is a substitute for §4 and §6:

1. **A pin exists.** Nothing is required of its *identity* here — clause 3 compares it, and
   requiring a *readable* one is the tightening §9 preserves as refuted. (§4 defines identity
   `0` as "could not read", so a readability clause here would make clause 3's `0 == 0`
   unreachable and refuse every write on the title → online → container route — the exact
   session shape a redirect exists to fix.)
2. **The observation is fresh.** The worker's `(cursor, identity)` observation has a maximum age.
   **(B)** 3 s against a ~100 ms poll. A stalled worker means a stale identity, and a stale identity
   is the one thing a redirect must never act on.
3. **The live identity equals the pinned identity** (with `0 == 0` a match — §9).
4. **The observed cursor equals the write's own target.** If they disagree, the write is not the one
   you are explaining.

Then:

**Rename the target. Never patch the cursor (A).** Change only the disk landing point: write into the
sibling record directory named by the pin. That is invisible to the guest, returns success — so its
deferred truncate resolves normally instead of executing at close
([loss-mechanisms.md](loss-mechanisms.md) §5) — and leaves the record the guest named byte-untouched. A
rename that cannot be constructed falls back to **refusal**.

**The summary follows its payload.** Both record families come from the same writer with the same
suffix, so both redirect together (§3). Redirecting the payload alone leaves a character whose slot
list says the row is empty.

**Publish the observation from everywhere, including the shell.** The pair a redirect exists to fix
is exactly "drifted cursor, unchanged identity", and that pair only occurs on shell screens — so the
worker publishes its observation there too, even though the *pin* is never latched from the shell
**(B)**. Two mechanisms of opposite polarity: keep them in different functions and say so in the
comments.

Walk every clause in the end-to-end test: a different observed identity refuses, a suspended
observation refuses, a mismatched cursor refuses.

---

## 12. The same-tick race and the synchronous suspend

If a server reply can replace the live character **within one guest tick**, a poll-based observation
structurally cannot win: the character is swapped before the next poll, and a redirect acting on the
pre-swap identity writes the *restored* character over the pinned one (A).

The fix is not a shorter poll but a **synchronous suspend in the send path** — at the moment such a
request is allowed onto the wire, drop the observation while keeping the pin. Redirect is off until
the next in-play poll rebuilds it, and §11's clause 2 does the rest.

**(B)** In the reference port that request is the server-side restore; see
[threat-model.md](../../server-revival/reference/threat-model.md) for why it is indistinguishable on
the wire from a benign login sub-request and why the guard lives on the client.

⚠ The race exists only in network mode, so **it will not appear in offline testing** — and an
offline-by-default posture is itself a save-safety measure, not a convenience
([loss-mechanisms.md](loss-mechanisms.md) §8).

---

## 13. Freezing the UI while a save settles

The screen that can re-aim a save is the slot list. While a save may still be settling, freeze it
(A). One pure function of three timestamps, with a standalone test, and **three** release conditions
rather than one:

| condition | **(B)** | why |
|---|---|---|
| quiet-for-Q after the last write that started inside the lock | 400 ms | a single fixed delay releases before the last write |
| a shorter empty timeout if no write ever arrives | 800 ms | not every visit saves; do not punish the player |
| a hard cap | 2500 ms | a stuck writer must not soft-lock the screen |

Two device findings that cost a build each:

* **A modal that swallows all input must not draw an answerable affordance.** The reference wait
  panel drew the same OK band as the game's normal message boxes, and players reported the game as
  broken because pressing it did nothing. The fix is a panel variant whose OK band has **zero
  height** — nothing to press. See
  [game-native-ui.md](../../android-host/reference/game-native-ui.md).
* **Modal precedence in the key path.** A more specific modal must be tested **before** the save
  lock, or it becomes uncloseable — the lock swallows the key that would have dismissed it.

---

## 14. The five attempts, in order

**Read this section before adding a clause to a failing policy.** Every attempt below was
implemented by someone who had read the previous attempt's notes, every one had tests, and every one
shipped.

| # | the answer | refuted by | what it cost |
|---|---|---|---|
| 1 | **Refuse** any write to a record that is not the pinned slot's | two player reports that read as gameplay bugs: a withdrawn item vanished, a deposited item duplicated | all saving stopped for the session; state diverged from host-owned files |
| 2 | Latch the pin **only while in play** | a slot destroyed in the field; the log showed the latch had never armed | the arming condition excluded the route players actually use (below) |
| 3 | Pin on **"the player executed a row"**, latched once | device traces: the pin could only ever name the first slot | a second load left the pin naming an unloaded character |
| 4 | Pin on the **one load path** you found — the outbound frame | a full device trace of the reported sequence (§6) | the second load path was invisible; the next save destroyed the other slot |
| 5 | Pin on **any observed load**, at the record store (§5–§7) | — | held |

### Attempt 1 → the redirect

> **REFUTED.** *"A storage-layer guard should refuse writes it cannot prove. Refusing protects the
> innocent slot; the worst case is that a save does not happen."*
>
> **Refuted by** the field: the guest ignores the write's failure code, so the game reported success
> while nothing persisted, and host-owned files that had already committed diverged from the guest's
> rolled-back state. Two reports, one bug.
>
> **Why it was attractive:** it is the textbook fail-closed design, and in almost any other
> subsystem it would be right.
>
> **Correction:** prefer **Redirect** wherever ownership can be proven (§11); keep refusal for what
> you cannot prove (§10).

### Attempt 2 → policy as pure functions

> **REFUTED.** *"Latch the pin only while the game is in play — outside the world we cannot trust
> anything, so do not latch there."*
>
> **Refuted by** a destroyed slot and a log showing the latch had never armed once. The gate was "not
> in the shell app-state", and that app-state turned out to *also* cover the online screens — a fact
> the same repository had already established for an unrelated feature. The policy silently degraded
> to `Allow` unconditionally.
>
> **Why it was attractive:** the rule was *correct*. What failed was that its load-bearing condition
> lived in a third file no test could reach, where "it is obviously true" stood in for a test.
>
> **Correction:** one dependency-free pure function per decision, all facts as arguments
> ([policy-and-diagnostics.md](policy-and-diagnostics.md) §1–§2); consolidating took that module's
> standalone selftest count from 15 to 39 **(B)**, runnable with plain `rustc` on any host. See also
> [mode-resolution.md](../../android-host/reference/mode-resolution.md) — "outside the world" is not a
> state word, and treating one as if it were is its own recurring fault.

### Attempt 3 → the pin needs a per-load source

> **REFUTED.** *"Pin the slot when the player executes a row — that is an unambiguous user intent,
> and the session's socket opening is a reliable edge to hang it on."*
>
> **Refuted by** device traces: the socket opens **once per session**, and that instant is right after
> the menu row zeroed the cursor, so the edge could structurally only ever name the first slot. A
> second execute left the pin naming a character that was no longer loaded.
>
> **Why it was attractive:** it uses a real user action instead of a heuristic, and a once-per-session
> edge looks like a clean, unambiguous trigger.
>
> **Correction:** re-establish the pin **on every load**, from something that carries the slot itself
> (§5).

### Attempt 4 → §6

Refutation block in [§6](#6--there-is-more-than-one-load-path).

### The meta-lesson (A)

> **When a guard trusts a single scalar completely, the invariant you must state and test is the
> scalar's PROVENANCE, not the guard's clauses.** `HIGH / CONFIRMED over five shipped builds`

Attempts 1–4 each added a clause. Every clause was downstream of a wrong input, so every attempt
shipped a build that destroyed a save. The version that held changed the *rule about the input*.

Two operational duties that go with it:

* **Recall the destructive range by version, in the notes, permanently.** Say which builds can
  destroy data and why — see
  [distribution-and-recall.md](../../port-shipping/reference/distribution-and-recall.md).
* ⛔ **Never tell anyone to uninstall.** Save records and the vault both live in app-private
  storage, so an uninstall destroys the evidence *and* the recovery path, and it happens after the
  loss report when the vault is the only copy left. Update in place; treat a signing-key change as a
  save-destroying event, because it forces an uninstall to update.

---

## 15. Symptom → cause table

| symptom | first cause to check |
|---|---|
| one slot's character has been replaced by another slot's | the record name's slot suffix is a UI cursor (§2); or a pin armed from something other than an observed load (§4) |
| saves stop persisting after visiting an online/container screen | menu entry zeroed the cursor (§3), and the policy is refusing rather than redirecting (§1, §10) |
| a withdrawn item vanishes; a deposited item duplicates | a silent storage-layer refusal — the guest ignored the failure code while host-owned files committed (§1) |
| a save-integrity fix passed its tests, shipped, and destroyed a save again | you are debugging the clauses; debug the pin's provenance (§14) |
| the pin is correct in play but wrong after a load from the local menu | a second load path that announces nothing (§6) |
| the pin walks across slots whenever the slot list is drawn | the summary-record scan is being read as a load (§8) |
| the guard held the pin across a genuine load "because the identity did not change" | the identity field is a class name, and the two characters share a class (§9) |
| a live character is replaced right after an online screen (story, equipment and items all reset) | a server reply, not a slot bug — [threat-model.md](../../server-revival/reference/threat-model.md), and §12 |
| the "saving, please wait" panel looks broken | it swallows all input but drew an answerable OK band (§13) |
| a green selftest is defending the bug you just found | the test asserted an interpretation of its input; re-file it as a named regression test ([policy-and-diagnostics.md](policy-and-diagnostics.md) §7) |

---

## 16. Checklist

Before you ship any policy that decides where a save lands:

- [ ] The writer's slot suffix traced to its source; **every** writer of that word enumerated; the
      character-unload routine's callers enumerated. Two derivations, one of them a device run.
- [ ] **Every** load path counted, by literal-pool reference rather than call graph — you can say how
      many there are and what each puts on the wire.
- [ ] The load predicate validated against a full device trace for **false positives**, and "a write
      never reads" checked against *your* record store's seeding rules.
- [ ] The pin has exactly one writer in the host; its provenance rule and why `target == pin ⇒ Allow`
      is safe are both written at the definition site.
- [ ] Every rejected pin source carries a tombstone comment naming it and what it cost.
- [ ] Policy is one dependency-free pure function per decision; no condition anywhere else.
- [ ] `delete_verdict` has no `Redirect` arm, and a selftest asserts that exhaustively.
- [ ] Redirect: **§4 and §6 are done first** — a redirect on an inexact pin is worse than a refusal —
      then four clauses, each failing closed; freshness bounded against the poll interval; the
      summary redirects with its payload; an unconstructable rename refuses.
- [ ] The redirect suspends **synchronously** when a character-replacing request goes on the wire.
- [ ] Identity is never used to *tighten* (`0 == 0` is a match), and its real meaning was established
      by enumerating its writers.
- [ ] The slot list freezes while a save may be settling, with three release conditions, and the wait
      panel draws no answerable affordance.
- [ ] One verdict log line per character-record write, at warn level, carrying every fact the verdict
      used ([policy-and-diagnostics.md](policy-and-diagnostics.md) §9).
- [ ] A device walk of the reported sequences, with those verdict lines read afterwards — not "it
      works".
- [ ] A pre-image vault exists, so the next wrong answer is recoverable
      ([save-vault.md](save-vault.md)).
