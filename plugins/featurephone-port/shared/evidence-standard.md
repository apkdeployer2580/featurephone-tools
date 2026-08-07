# The evidence standard

Every skill in this plugin writes findings the same way. This document is that format, and the
reasoning behind it.

The short version: **reverse-engineering documentation is wrong on a regular basis, and its value
is determined entirely by how it behaves when it turns out to be wrong.** Notes that record only
conclusions rot silently — the reader cannot tell a device-confirmed fact from a Tuesday-afternoon
guess, so either they trust everything (and ship the guesses) or they trust nothing (and re-derive
the facts). The four practices below cost about one extra line per finding and make the document
survive being wrong.

## Contents

- [1. Provenance tags: (A) / (B) / (C)](#1-provenance-tags-a--b--c)
- [2. The confidence grid](#2-the-confidence-grid)
- [3. Two independent derivations](#3-two-independent-derivations)
- [4. Preserved refutation blocks](#4-preserved-refutation-blocks)
- [5. A measured constant carries its symptom, its measurement and its date](#5-a-measured-constant-carries-its-symptom-its-measurement-and-its-date)
- [6. Behaviour is settled dynamically, never by reading disassembly](#6-behaviour-is-settled-dynamically-never-by-reading-disassembly)
- [7. Where evidence lives](#7-where-evidence-lives)
- [8. Checklist](#8-checklist)

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
```

The third field is not optional. "Why the wrong reading was attractive" is the part that stops the
loop; without it the block reads as a typo correction rather than as a trap marker.

### Six corrections carried by this plugin

These are real refutations from the reference port's own documentation. Each is repeated in the
skill that owns it. They are listed here because collectively they are the argument for the whole
practice — every one of them was *written down as fact* by a competent person before it was
disproved, and four of the six were disproved only by running something.

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

---

## 7. Where evidence lives

| artifact | holds | rule |
|---|---|---|
| `target.toml` | The four address-model constants and the encoding. | One file, read by every static tool. Never hardcode these in a script. |
| `PORT_NOTES.md` | Every finding: tag, confidence pair, derivations, date. | Append-only in spirit; refutations are added, claims are not deleted. |
| Code comments | Any constant that came from an observation. | Symptom + measurement + date, at the definition site. |
| Selftests | Encodings, formats, state machines, patch halfwords. | The assertion message names the finding. See [selftest-convention.md](selftest-convention.md). |
| Probe runs | Everything about behaviour. | Runs are reproducible because the clock is virtual — record the exact invocation. |
| Transcripts / frame dumps | Ground truth from the device. | Keep them **outside** the source tree; they contain user data. |

**One canonical document.** When several documents describe the same subsystem they drift, and a
reader has no way to tell which is current. Nominate one file as canonical per subsystem, state at
the top of it that it corrects the others, and put an appendix in it listing the specific
now-wrong statements elsewhere. The reference port did exactly this and it is why its six
corrections were recoverable at all.

---

## 8. Checklist

Before a finding is considered recorded:

- [ ] Tagged **(A)**, **(B)** or **(C)**.
- [ ] Every (B) constant is introduced by the method that found it.
- [ ] Confidence written on both axes, e.g. `HIGH / CONFIRMED`.
- [ ] Anything INFERRED carries `⚠` until something runs.
- [ ] Anything you will build on has **two independent derivations**, and the two routes are named.
- [ ] Measured constants carry symptom + measurement + date at the definition site.
- [ ] Refuted claims are preserved with their disproof, not deleted.
- [ ] Behavioural claims cite a run, not a reading.
- [ ] Nothing recorded here is a secret, a credential, a key, or copyrighted content.
