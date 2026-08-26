# The evidence standard

Every skill in this plugin writes findings the same way. This document is that format, and the
reasoning behind it.

The short version: **reverse-engineering documentation is wrong on a regular basis, and its value
is determined entirely by how it behaves when it turns out to be wrong.** Notes that record only
conclusions rot silently — the reader cannot tell a device-confirmed fact from a Tuesday-afternoon
guess, so either they trust everything (and ship the guesses) or they trust nothing (and re-derive
the facts). The practices below cost about one extra line per finding each, and they are what make
a document survive being wrong.

## Contents

- [1. Provenance tags: (A) / (B) / (C)](#1-provenance-tags-a--b--c)
- [2. The confidence grid](#2-the-confidence-grid)
- [3. Two independent derivations](#3-two-independent-derivations)
- [4. Preserved refutation blocks](#4-preserved-refutation-blocks)
- [5. A measured constant carries its symptom, its measurement and its date](#5-a-measured-constant-carries-its-symptom-its-measurement-and-its-date)
- [6. Behaviour is settled dynamically, never by reading disassembly](#6-behaviour-is-settled-dynamically-never-by-reading-disassembly)
- [7. What a search result licenses](#7-what-a-search-result-licenses)
- [8. Where evidence lives](#8-where-evidence-lives)
- [9. Checklist](#9-checklist)

Two companion files carry the *procedure* that these evidence rules imply, and this file deliberately
does not repeat them: [investigation-discipline.md](investigation-discipline.md) (how to search, and
how to be wrong cheaply) and
[guarding-destructive-operations.md](guarding-destructive-operations.md) (the shape of a guard on an
operation that can destroy a player's data).

---

## 1. Provenance tags: (A) / (B) / (C)

Every non-obvious statement in this plugin's documentation carries one of three tags. They answer
a single question: **can the reader use this directly, adapt it, or neither?**

| tag | meaning | the reader should |
|---|---|---|
| **(A)** | Generic procedure. True of the platform, the emulator, or the class of problem — not of any one game. | Follow it as written. |
| **(B)** | Worked example from the reference port. A real constant, address, offset, packet id or tuning number, shown to make an (A) procedure concrete. | Read the *method* that produced it and run that method on their own title. The value itself will be different. |
| **(C)** | Do not copy. Host-specific, device-specific, one-session scratch, or an accident of one machine. | Ignore, or replace wholesale. |

### The rule that makes the tags work

> **A (B) constant is always introduced by the method that found it. A bare address with no method
> is a bug in the documentation, not a shortcut for the reader.**

Compare:

- Bad: "the equip-level gate is at guest `0x13d0aa`." Useless to anyone with a different title, and
  actively harmful — it invites a reader to try that address on a binary where it is data.
- Good: "**(A)** Find the refusal message's string id, find the id-consuming accessor by id-set
  density, map the message code to the id through the dispatcher's jump table, find the single call
  site passing that code, and disassemble backwards to the guarding branch. **(B)** In the reference
  port that chain terminated at a `cmp`/`bge` pair whose threshold move sat at guest `0x13d0aa`."

The second form is longer and is the entire point: the address is now a *check* on the reader's own
run of the procedure rather than a substitute for it.

### Tagging your own port's notes

Use the same three tags in your `PORT_NOTES.md`. Once you have a second title, your own (B) values
are somebody else's trap. The tags are also what make a port's notes safely publishable: **(A)**
material is yours to share, **(B)** material is a description of someone else's copyrighted binary
(offsets are fine, content is not — see the plugin README), and **(C)** material should not have
been written down in the first place.

---

## 2. The confidence grid

Two independent axes. Write both. `HIGH / CONFIRMED` and `HIGH / INFERRED` license completely
different actions, and collapsing them to one word is how a plausible reading of a disassembly
becomes a shipped crash.

**Axis 1 — confidence in the claim:**

| level | means |
|---|---|
| **HIGH** | Multiple independent routes agree, or the claim is a direct property of code you can read in full. |
| **MED** | One good route, self-consistent, with a plausible alternative reading that has not been excluded. |
| **LOW** | A hypothesis that fits the evidence you happen to have looked at. |

**Axis 2 — how it was established:**

| level | means |
|---|---|
| **CONFIRMED** | Observed happening. The probe printed it, the device did it, the diff was zero, the test failed before the fix and passes after. |
| **INFERRED** | Derived by reading code, by arithmetic, or by analogy. Nothing was run. |

Every INFERRED claim carries a warning marker (this plugin uses `⚠`) until something runs. Strip
the marker in the same commit that adds the evidence, and say what the evidence was.

### What each cell licenses

| | CONFIRMED | INFERRED |
|---|---|---|
| **HIGH** | Build on it. Ship it. Use it as the anchor for other derivations. | Build a *test* on it. Do not ship a destructive or irreversible action on it. |
| **MED** | Ship it behind a guard that fails safe (a read-verify, a bounds check, a refusal). | Investigate further before writing any code that depends on it. |
| **LOW** | Something reproduced but you do not understand why — the most valuable state to be in, and the one to attack next. | A note to yourself. Never a code constant. |

### Worked example (B)

A protocol-RE track for the reference port labelled every claim in its protocol document this way.
The distinction paid for itself immediately: a whole class of "plausible acknowledgement" replies
was rated HIGH/INFERRED from the client's disassembly, and the device then showed that several of
them dismissed the on-screen modal without advancing the session state machine — after which the
client cleanly closed the socket. Reading the handler was *correct* about what the handler did and
*wrong* about what the session needed. Only the CONFIRMED axis catches that.

---

## 3. Two independent derivations

> **No offset, field or struct layout that you intend to build on ships with one derivation.**

"Independent" means the two routes do not share an assumption. Two disassembly readings of the same
function are one derivation. A reading plus a measurement is two.

Routes that are genuinely independent of each other:

1. **Read the code that constrains it.** A setter's clamp, a loop bound, an allocation size, a
   `memset` extent.
2. **Derive it from the game's own rules.** Arithmetic that has nothing to do with the binary.
3. **Change one thing and diff RAM.** Dump the god object and the static area before and after a
   single in-game action; intersect the changed-offset sets across several trials.
4. **Enumerate every code site that materialises the offset**, and check each is semantically
   consistent with your reading. Cover *both* Thumb idioms for building a constant, or you will
   "prove" a live field unused.
5. **Close a size relation arithmetically.** If a writer allocates `len + 0x10` and the record on
   disk is exactly `payload + 16`, the relation closing is stronger evidence than any amount of
   reading.
6. **Differential verification.** Reimplement host-side and diff against the guest's own output.
7. **Capture a live instance whose values differ.** Two adjacent same-typed fields cannot be told
   apart by reading code that treats them symmetrically — every read, write and copy in the image
   handles both identically, so the disassembly is silent on which is which. One real record in
   which the two happen to hold *different* values decides it in a second.
   **(B)** Two bytes of a 20-byte record were documented as *total sockets* and *filled sockets* the
   wrong way round, through a static reading and a reimplementation, until a record captured from a
   device settled it; the comment recording the swap is still in the reimplementation, which is the
   correct place for it. Swapped adjacent fields produce behaviour that is *almost* right, which is
   the hardest kind of wrong to notice.

### Worked example (B) — the strongest evidence pattern in the reference port

Two adjacent halfword fields in the god object were believed to be *unspent stat points* and
*unspent skill points*.

- **Derivation 1 (read the code):** the two setters clamp at 276 and 44 respectively.
- **Derivation 2 (game rules, no binary involved):** the level cap is 70, so there are 69 level-ups;
  four stat points per level-up gives 69 × 4 = **276**. Skill points are awarded every level up to
  19 and then only on even levels — 18 + 26 = **44**.
- **Derivation 3 (enumerate readers):** every site materialising either offset was the stat window,
  the reset item, or the HUD badge. Nothing inconsistent.

Two numbers arrived at from completely disjoint directions landing on the same pair is as close to
proof as this work gets. Note that derivation 2 is *free* — it required no tooling at all, only the
willingness to ask "what should this number be if my reading is right?"

### Counter-examples — what does not count

- Reading the same function twice, or reading it and then reading its caller.
- A tool agreeing with itself under a different flag.
- Five hand-picked samples agreeing on a relocation delta. That is one derivation with five data
  points, and one of the five is usually referenced through a different mechanism anyway. The second
  derivation is a **corpus-wide validation**: extract every plausible string in the image and count
  what fraction is referenced under each candidate delta. A correct delta explains a large share; a
  coincidence explains almost none.
- "It works." A patch that appears to work confirms that *something* changed, not that your model
  of *why* is right. Ask what your model predicts that a rival model does not, then check that.
- **A green test that agrees with the code.** If the assertion was written from what the code
  currently outputs, the test and the code are one derivation, and the test's greenness is now
  *guarding* whatever the code got wrong. This is not the mirror-drift failure that
  [selftest-convention.md](selftest-convention.md) warns about — a test that `include!`s the real
  source cannot drift, and freezes the wrong premise perfectly.
  **(B)** Three shipped defects in the reference port were each held in place by a passing
  assertion: a server test named for a field "still being one byte" asserted the exact width that
  overflowed the client's *signed* read of it; a negative list asserted that a legitimate item
  category was not equipment, so 23 green assertions proved the bug for two releases and it reached
  the player as a question about the game's design; and a save-policy test asserted the reported
  destructive sequence as the expected safe outcome, under a case comment that mis-named it. All
  three suites were green and believed. The discipline that follows from this — **on a bug report,
  grep the test suite for the reported input before reading any code** — is owned by
  [investigation-discipline.md](investigation-discipline.md) §9.
- **A second person reading the same instruction.** Two competent, independent readings routinely
  reach *opposite* conclusions, and re-reading does not converge them. See below.

### When two readings disagree, compute

> **(A) Do not weigh the readings. Replace reading with something countable — a computed pointer, an
> enumeration of that pointer's readers, a size relation that closes.**

Interpretation has no tie-break. Arithmetic does, and it is usually minutes of work.

**(A)** For a disputed load, compute the literal-pool address the instruction actually reads —
`(pc + 4) & ~3 + imm` for the PC-relative form — read the word that is there, and then enumerate
every site in the whole image that loads that same word. The count is itself informative: a handful
of readers is a call graph.

> **(B)** Two investigations of the reference port read one branch as "free the buffer" and as
> "load the save" respectively — which decided whether the surrounding routine was destructive and
> therefore whether a save guard was even needed. Computing the literal address and reading the word
> ended it: the word was a loader entry with the Thumb bit set, and exactly two sites in the image
> read it. The arithmetic beat both readings.

⚠ A structural companion that makes this the *only* available call graph in some images: where every
cross-module call is `ldr rN, =target|1` followed by a branch through a one-instruction register
veneer, a branch-and-link call graph reports zero callers for real functions. That property is a
per-image fact to establish before trusting any caller count —
[live-data-structures.md](../skills/guest-re/reference/live-data-structures.md) covers it.

---

## 4. Preserved refutation blocks

> **When a claim is disproved, keep the claim and the disproof together. Do not delete the claim.**

Deleting a refuted claim removes the only warning that the claim is attractive. The next reader —
frequently you, four months later — walks the same reasoning, reaches the same wrong conclusion, and
this time ships it.

### Format

```markdown
### ★ CORRECTION (YYYY-MM-DD) — <one-line statement of the truth>

**Previously documented:** <the refuted claim, stated as strongly as it was originally stated>
**Refuted by:** <what was run, on what, and what it produced>
**Actual mechanism:** <why the wrong reading was attractive, and what is really happening>
**Consequence if carried forward:** <what breaks for someone who believes the old claim>
**Limits of the search that produced this:** <what this investigation could not have caught>
```

The third field is not optional. "Why the wrong reading was attractive" is the part that stops the
loop; without it the block reads as a typo correction rather than as a trap marker.

The fifth field is the one most often skipped and it has the best measured payoff of any line in
this document. **(B)** One session in the reference port ran three static searches for a flag, got
zero hits, and wrote down that those searches were literal- and displacement-based and therefore
could not catch an index computed at runtime. The next session started at exactly that sentence,
found the runtime-computed index, and closed a bug that had been answered wrongly three times; the
closing note records the causal link explicitly. See §7.2 and
[investigation-discipline.md](investigation-discipline.md) §2.

### Two renderings of the same block

The heading form above is for an append-only notes file, where corrections accumulate in date order.
The reference documents in this plugin compress the same fields into a **blockquote placed next to
the sentence it corrects**, because there the reader arrives at the wrong belief rather than browsing
a list:

```markdown
> **REFUTED.** *"<the refuted claim, in its own words>"*
>
> **Refuted by:** … **Why it was attractive:** … **Correction:** …
> **Limits of the search that produced this:** … *(where the investigation had a known blind spot)*
```

Both are the same object and neither is optional: the claim must remain visible next to its
disproof. Do not "clean up" a document by deleting the refuted half.

### Corrections carried by this plugin

These are real refutations from the reference port's own documentation. Each is repeated in the
skill that owns it. They are listed here because collectively they are the argument for the whole
practice — every one of them was *written down as fact* by a competent person before it was
disproved, and most were disproved only by running something.

> **The list is a sample, not a census.** An earlier edition of this file said the reference port's
> documentation "was wrong in six identified places", which reads as a closed set that has been
> dealt with. It is not: the port has been corrected many more times since, and several of the later
> corrections are worth more than these because they were *shipped*, damaged a player's data, and
> were caught by measurement. Never write a count. Point at the practice.

1. **Bundled private-save entries in an archive do not seed a new user's database.** The deploy
   documentation said they did. Measurement showed the game writes its own record: packaged-database
   reads resolve through the class loader (jar resources) while the private entries land in the
   virtual filesystem — different namespaces. Strip those entries anyway, because they are somebody
   else's personal data, but do not believe the stated mechanism.
   *Owner: `archive-prep`.*

2. **A dead-code reachability sweep must follow `bl`.** The first sweep did not, on the theory that
   `bl` is a call and calls return. On Thumb-1, `bl` is routinely used as a plain long branch when
   the short form cannot reach — so the sweep marked live code dead. The proven-dead cave started
   several hundred bytes later than the first answer. Writing scratch data into the difference is a
   guaranteed crash.
   *Owner: `guest-re`.*

3. **A measured payload length is a band, not an invariant.** A save-chain assertion pinned the
   payload length observed in an archived save. It fired on the very first real save from a live
   character, because serialised length is content-dependent. Replaced with a plausibility band.
   Generalise: never promote a measured value to an invariant without an argument for why it cannot
   vary.
   *Owner: `guest-re`.*

4. **The differential-render check is gated on an explicit dump-frame list.** The invocation printed
   in the reference port's own top-level documentation could not print anything, because the check
   only runs on frames named by a separate flag. A diagnostic silently producing no output looks
   exactly like a diagnostic reporting no problems.
   *Owner: `guest-re` (probe CLI contract).*

5. **Deployment documentation is part of the security surface.** The reference server's code default
   for a destructive restore path disagreed with both its service unit and its deploy document. An
   operator reading the docs would believe the destructive path was disabled while it was enabled.
   *Owner: `server-revival`.*

6. **"The weak auth model is structural" was wrong.** The claim was that a bearer-token identity
   could not be improved because the original protocol is fixed. But a port owns *both* endpoints —
   the host's send path already rewrites the identity field, and the server already sends an
   acknowledgement before the client sends its identity. A challenge-response fits inside the
   existing frames with no guest changes at all. "The protocol is fixed" is true; "therefore the
   authentication is fixed" does not follow.
   *Owner: `server-revival`.*

7. **A citation is not evidence.** A note claimed a validator accepted an inclusive range and cited
   three guest sites as proof. Re-disassembling the cited instructions showed all three test only an
   *upper* bound. By the time anyone checked, the wrong claim had propagated into a constant, a
   comment, a test's negative list and a shipped release — where it removed an entire legitimate
   item category from a feature, and the player reported it as a question about the game's design.
   Re-read cited instructions before building on the claim they are cited for; this is distinct from
   the rule about re-reading bytes at a patch site, because here nothing was being patched, only
   believed.
   *Owner: `guest-re`.*

8. **A hook that installs cleanly can fire zero times.** A summing hook was placed on a routine that
   computes exactly the wanted value, passed its expected-original-bytes check on install, and then
   fired **zero** times across a whole session — while a sibling hook installed in the same session
   fired 889 times. The operation being hooked simply never went through that routine's caller.
   Verifying the original bytes proves the instruction is at that address and nothing else: verify
   the **call graph** before placing a hook and the **fire count** after.
   *Owner: `guest-re`.*

9. **The cache you rebuilt was not the copy on screen.** A stale on-screen number was diagnosed as a
   derived-cache problem, and the fix rebuilt that cache across the whole container. A later session
   measured the rebuild routine's stores: two halfwords, neither of them the field being drawn. The
   real source was a pair of *byte-for-byte snapshots* of the whole record — a block copy into a
   candidate-list array and a full-record `memcpy` into an equipped-slot array — baked by the equip
   routine's tail, and the character's own bonuses were computed from the snapshot rather than from
   the container. The earlier fix was **structurally void** for that symptom while looking entirely
   correct. Before trusting a rebuild call to fix a display, enumerate that routine's stores.
   *Owner: `guest-re`.*

10. **A plausibility gate inside a resolver deletes features.** The routine that answers "is there a
    live character here?" also required `current <= maximum` on a gauge, as a sanity check. Removing
    a piece of equipment lowers maximum HP below current HP, so ordinary play violated it — and the
    resolver then answered "no character", which silently removed the HUD and the quick bar and made
    a refresh sweep see an empty container. Two separate player reports, one root cause. A gauge that
    reads high is a gauge to **clamp when drawing**, not evidence that the object does not exist.
    *Owner: `android-host`.*

### Refutation is a deliverable, not a failure

Budget at least one refutation per feature, and treat a phase with zero recorded refutations as
under-tested rather than as unusually well executed. Static analysis reliably recovers *shapes*;
only running the thing settles *semantics and error behaviour*.

---

## 5. A measured constant carries its symptom, its measurement and its date

> **Any constant whose value came from an observation is written with a comment giving the symptom
> that produced it, the measurement, and the date.**

```rust
// ★ 2026-07-14: the interpreter thread overflowed its 8 MB stack after 21-35 min of play
// (five SIGSEGVs, all "stack pointer is not in a rw map"). Ruled out: wrapper poll nesting
// (depth is 1 by construction), run_function nesting (tops out at 21, three distinct call
// trees), executor task leak (< 8 tasks). MITIGATION, not a fix: thread stacks are reserved
// address space, so the extra 56 MB costs nothing until touched, and it buys ~8x the observed
// interval.
const EMU_STACK: usize = 64 * 1024 * 1024;
```

Four things that comment does which the number alone cannot:

1. **It stops a cleanup.** A bare `64 * 1024 * 1024` looks like superstition and gets "tidied" back
   to 8 MB by someone reasonable.
2. **It marks itself as a mitigation.** Anyone reading it knows the underlying bug is still open,
   which is information that otherwise exists only in one person's head.
3. **It records the ruled-out hypotheses**, so the next investigator does not re-instrument the
   three layers that were already cleared.
4. **The date lets a reader weigh it against later findings.** Undated notes cannot be ordered, and
   unordered notes cannot be reconciled when they conflict.

Apply the same treatment to timeouts, budgets, caps, cadences, retry counts, buffer sizes and every
"magic" tuning number. If you cannot state the symptom that produced a constant, that is a finding
in itself: the value is arbitrary and should be a named default with a comment saying so.

### Tests carry evidence too

Write the provenance into the assertion's message, not only into a comment:

```python
# ★ DEVICE FINDING (2026-07-23): an empty notice body crashes the client's paged text-box
# builder on a null page list. The notice must be non-empty.
check(len(enc_login_notice(b"")) == 0, "empty login notice is refused by the encoder")
```

When the claim is later refuted you edit the test, the code and the comment in one commit, and the
refutation is preserved by the diff. This is why the conformance-test pattern in `server-revival` is
described as *the evidence, executable*: each assertion restates one RE claim as bytes, so a
disagreement between the document and the code is a test failure rather than an argument.

---

## 6. Behaviour is settled dynamically, never by reading disassembly

> **Static analysis is for finding *where*. It is not for deciding *what*.**

This is the operational form of the CONFIRMED axis, and it is the single most expensive lesson in
the source material. A feasibility study for one feature in the reference port concluded from
disassembly that the change was hard and would require guest patches; **one headless probe run
reversed the conclusion.** The disassembly was not misread — it simply did not contain the answer,
because the answer depended on runtime state.

Practical consequences:

- Any claim of the form "the game does X" gets a probe run attached before it is written down.
- Any claim of the form "the game *cannot* do X" is the most suspicious claim in the document and
  needs a probe run *and* a stated reason why the run is conclusive.
- The probe's automated black-screen detector (a non-black pixel count on a dumped frame) is worth
  more than any amount of eyeballing, and belongs in CI.
- When a host reimplements a guest routine, the emulator runs both and diffs them. Not "looks
  right" — zero mismatched pixels, on at least two different game states.

### An absence in a run is not a negative result

> **(A) Write "not observed in this run; the run did not exercise it" — never "does not happen".
> What you failed to observe is not evidence.**

This is the most common way a *correct* reading becomes a *wrong* document, and it is free to
prevent. **(B)** A reference-port session recorded that neither host nor guest ever stops the music,
on the strength of a headless trace. The device then showed the guest stopping the music on every
single sound effect, because it owns exactly one media clip and recycles it: stop music, play
effect, restart music from bar one. The self-correction names the failure precisely — *"that was not
evidence, it was an observation gap; the two buses simply never overlapped in that trace"* — and the
measured call sequence, with timestamps, then became the test data for the fix.

An observation gap is recorded like any other finding: what was run, what it did **not** cover, and
what would close it. The same habit at the session level is
[device-verification.md](device-verification.md) §5 — "unverified on device" is a shipped state.

### A reading and a measurement can both be right about different questions

> **(A) When static analysis and a live measurement disagree, the first move is not to pick a
> winner. It is to write down the question each of them actually answered — they are usually not the
> same question.**

Framing static versus dynamic purely as *reliability* invites the wrong repair, which is to distrust
the disassembly. The disassembly is normally accurate about what the routine does; it is simply not
about what you asked.

- **(B)** A handler was read correctly and a set of "plausible acknowledgement" replies derived from
  it (§2's worked example). Every reply did what the handler said. The device then showed several of
  them dismissing the on-screen modal without advancing the session state machine, after which the
  client closed the socket cleanly. The reading answered *"what does this handler do?"*; the
  question that mattered was *"what does the session need?"*.
- **(B)** A hot-path attribution was taken from an aggregate counter reading ~26,000 calls per
  second and assigned to the network receive path. The magnitude was right and the attribution was
  wrong — per-category and hot-identifier counters showed the calls were a fill-rectangle primitive.
  An aggregate gives you magnitude, never kind, and a documented attribution derived from a total is
  more likely wrong than right.

---

## 7. What a search result licenses

The confidence grid in §2 asks how strong a claim is. This section asks a narrower question that is
answered wrongly far more often: **given how you looked, what is this result actually evidence
about?** Every rule here is one sentence; the procedure each implies is owned by
[investigation-discipline.md](investigation-discipline.md), and the shape of the guard implied by
§7.5 by [guarding-destructive-operations.md](guarding-destructive-operations.md).

### 7.1 A narrowed search is evidence about the window, not about the binary

> **(A) A search window centred on where you expect the answer can only confirm or mislead. It
> cannot discover. Record the extent of the window next to the result.**

A narrow window never reports failure: it always returns a best-in-window candidate, and that
candidate is indistinguishable from an answer. **(B)** Three consecutive sessions of the reference
port produced three different wrong addresses for one flag byte, each from a ~128-byte watch chosen
because a previous carrier's build had the field nearby. Widening the watch to the entire god object
— 43 KB, diffed twice a second, on the reasoning that *"it is a `memcpy`, there is no reason to
narrow it"* — produced exactly one persistent zero-to-nonzero write at the moment of the in-game
action, and the same run printed zero for all three historical candidates, retiring them in the same
breath.

### 7.2 An empty search is a claim about the search

> **(A) Zero hits is the single most misread result in reverse engineering. Undocumented it reads as
> "the field is dead"; documented with its blind spot it reads as a work item with a starting
> point. Writing down what the search could not have caught is an obligation, not modesty.**

Cost: one sentence. Payoff: see the (B) case in §4's fifth field, where that one sentence is the
whole reason the next session opened in the right place.

### 7.3 Several individually sound measurements that disagree describe a shape

> **(A) When several sessions each measure a different but individually correct-looking value for
> "the same offset", stop adjudicating. The offset is not a constant — it is an array indexed by a
> live value, and every past measurement is one element of it.**

Contradictory-but-sound measurements are data about the structure, not a dispute to be settled by
picking a winner. **(B)** Three historical answers for one flag — one inherited from another
carrier's notes, one measured on a device, one recorded as "refuted" — turned out to be elements 0,
1 and 6 of `base + <index word>`, and the index word was already in the project's anchor table under
a different name because the save chain reads the same word. This shape is invisible to a
literal/displacement cross-reference search, because nothing in the image materialises the final
address; a static sweep therefore reports zero sites and the field looks dead. It also explains
every "it worked for me once" report: the write landed while the index happened to equal the value
baked into the host.

### 7.4 An address is half of a fact; the reader's predicate is the other half

> **(A) How the guest READS a field is part of the field's contract. An anchor for a host write is a
> triple — offset, value, and the predicate the reader applies — and a table that stores only the
> offset hands the next porter the half that does not make the feature work.**

A write at the right address with the wrong-shaped value fails silently and presents *identically*
to a wrong address, so the investigator goes back to hunting the address that was already correct.
**(B)** Three instances in one port: a flag whose reader did `ldrb; lsls #24; asrs #24; cmp #0; bge`
— a bit-7 test, so writing `1` at the proven-correct address did nothing; a stack count read through
the same sign-extension, making 128 negative so the client *subtracted* from the player's stack; and
a capacity byte read with `ldrsb`/`bge`, where a legitimate 128 became −128 and killed two
navigation gates at once. In each case the deciding evidence was the *reader's* instruction
sequence, not the writer's. A corollary worth carrying to every wire format: any field the guest
sign-extends has a real maximum of 127 whatever the protocol permits.

### 7.5 One observation of an event licenses a claim about one path

> **(A) Before you build anything on "this is what the game does when X happens", count how many
> code paths produce X. A guard placed on one of two paths is verified by the reporter's
> reproduction, ships, and is defeated by the sibling path.**

Prefer a signal taken from the event itself over a proxy that merely correlates with it, and check
the proxy's **cardinality** before trusting it — "once per session" versus "once per action" decides
whether it can carry the signal at all, and it is answerable statically from the call sites.
**(B)** A save guard in the reference port pinned the player's chosen slot on the edge where the
network socket opened. The socket opens exactly once per session, at an instant when a menu entry
has just reset the cursor to zero, so the pin could *structurally* only ever be zero. Rebuilt around
the network load — which is observable because it emits a frame — it was then defeated one build
later by the local start-game load, which reads the same records and emits nothing. The version that
held observes the record read itself, which both paths perform.

### 7.6 Inherited output is unverified until you re-derive it

> **(A) Output from an automated investigation whose verification stage did not run is labelled
> unverified in the handoff, and every address in it is re-derived from the binary before it is
> believed.**

Partial automated output looks exactly like finished output: structured, confident, and full of
addresses. **(B)** A reference-port session stopped with one of four investigation branches complete
and no adversarial verification run at all. The handoff said so and named the artefacts; the next
session re-derived every address from the shipping binary rather than inheriting any, and found that
the handoff's reading of one call was exactly inverted. That handoff also generalised its own
lesson: two earlier expensive failures — a hook that never fired, a rolled-back padding hypothesis —
were "both the same mistake: jumping from an inference straight to a symptom with no verification
step". Compare §4 item 7: a citation you inherit is in the same category as a tool's output you
inherit.

---

## 8. Where evidence lives

| artifact | holds | rule |
|---|---|---|
| `target.toml` | The four address-model constants and the encoding. | One file, read by every static tool. Never hardcode these in a script. |
| `PORT_NOTES.md` | Every finding: tag, confidence pair, derivations, date. | Append-only in spirit; refutations are added, claims are not deleted. |
| Code comments | Any constant that came from an observation. | Symptom + measurement + date, at the definition site. |
| Selftests | Encodings, formats, state machines, patch halfwords. | The assertion message names the finding. See [selftest-convention.md](selftest-convention.md). |
| Probe runs | Everything about behaviour. | Runs are reproducible because the clock is virtual — record the exact invocation. |
| Transcripts / frame dumps | Ground truth from the device. | Keep them **outside** the source tree; they contain user data. |
| Handoff notes | What an unfinished or automated investigation produced. | Labelled unverified where the verification stage did not run; addresses re-derived before use (§7.6). |

**One canonical document.** When several documents describe the same subsystem they drift, and a
reader has no way to tell which is current. Nominate one file as canonical per subsystem, state at
the top of it that it corrects the others, and put an appendix in it listing the specific
now-wrong statements elsewhere. The reference port did exactly this, and it is the only reason its
corrections were recoverable at all. In a long append-only log, say which artefact is current — one
live task table at the top, everything below it explicitly labelled finished history, with reading
order stated newest-first ([investigation-discipline.md](investigation-discipline.md) §14).

---

## 9. Checklist

Before a finding is considered recorded:

- [ ] Tagged **(A)**, **(B)** or **(C)**.
- [ ] Every (B) constant is introduced by the method that found it.
- [ ] Confidence written on both axes, e.g. `HIGH / CONFIRMED`.
- [ ] Anything INFERRED carries `⚠` until something runs.
- [ ] Anything you will build on has **two independent derivations**, and the two routes are named.
- [ ] Two readings of one instruction were settled by computation, not by re-reading.
- [ ] The extent of the search is recorded with the result, including what it could not have caught.
- [ ] Contradictory-but-sound measurements were treated as a shape (an array), not adjudicated.
- [ ] Any host write records the reader's predicate, not only the offset.
- [ ] The number of code paths that produce the observed event was counted before anything was built
      on one observation of it.
- [ ] Absences are written as observation gaps, naming what the run did not exercise.
- [ ] Anything inherited — a citation, a handoff, an automated tool's output — was re-derived.
- [ ] Measured constants carry symptom + measurement + date at the definition site.
- [ ] Refuted claims are preserved with their disproof, not deleted.
- [ ] Behavioural claims cite a run, not a reading, and name the question that run answered.
- [ ] Nothing recorded here is a secret, a credential, a key, or copyrighted content.
