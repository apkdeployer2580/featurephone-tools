# Decoding an outcome formula, and printing it honestly

How to take a game system that rolls dice — an upgrade, a refinement, a socket, a drop, a fusion —
and recover its arithmetic well enough that a host overlay can tell the player the truth *before*
they stake an irreplaceable object on it. The method is mechanical and it generalises: find the
**outcome strings**, xref their **loaders**, watch the loaders converge on one function, then read
the arithmetic out of that function term by term. What you ship afterwards is not the formula — it
is one number the player decides on and one sentence that stops them losing the item.

**Boundaries.** Reading and writing the records the formula operates on is
[live-data-structures.md](live-data-structures.md); the string index, the xref queries and the
literal-pool arithmetic are [static-toolkit.md](static-toolkit.md). This file owns what is between
them.

**Tags.** **(A)** generic procedure · **(B)** worked example from the reference port, always
introduced by the method that found it · **(C)** do not copy · `⚠` inferred, nothing was run ·
`> **REFUTED.**` a belief that was written down as fact, shipped, and disproved. Confidence on two
axes (`HIGH / CONFIRMED`) per [evidence-standard.md](../../../shared/evidence-standard.md).

## Contents

0. [When this is worth doing, and what the deliverable is](#0-when-this-is-worth-doing-and-what-the-deliverable-is)
1. [Find the judge: outcome-string convergence](#1-find-the-judge-outcome-string-convergence)
2. [Pin the die first — an inclusive uniform roll makes a threshold a percentage](#2-pin-the-die-first--an-inclusive-uniform-roll-makes-a-threshold-a-percentage)
3. [Read the arithmetic term by term: the recurring shape](#3-read-the-arithmetic-term-by-term-the-recurring-shape)
4. [Read the join point: what is consumed regardless, and what is reset](#4-read-the-join-point-what-is-consumed-regardless-and-what-is-reset)
5. [The number a player decides on: where risk becomes zero](#5-the-number-a-player-decides-on-where-risk-becomes-zero)
6. [Search the game's own function; keep the closed form in the test](#6-search-the-games-own-function-keep-the-closed-form-in-the-test)
7. [Verify by simulating every die face — and what only a device can settle](#7-verify-by-simulating-every-die-face--and-what-only-a-device-can-settle)
8. [Gate the readout on the step byte, not the window id](#8-gate-the-readout-on-the-step-byte-not-the-window-id)
9. [On a filtered screen the cursor indexes the filter's table](#9-on-a-filtered-screen-the-cursor-indexes-the-filters-table)
10. [Print it honestly: impossible is not the same as rounds-to-zero](#10-print-it-honestly-impossible-is-not-the-same-as-rounds-to-zero)
11. [Symptom → cause](#11-symptom--cause)
12. [Checklist](#12-checklist)

---

## 0. When this is worth doing, and what the deliverable is

**(A)** Decode a formula when the game asks the player to bet something irreplaceable on a number it
refuses to show. That justification carries the quality bar with it: odds are the one host output a
player acts on destructively, so spot checks are not an acceptable standard of evidence (§7).

The deliverable is **not** "the formula". It is four artefacts, of which the formula is only the
first: **(1)** the per-outcome odds for the state the player is in now; **(2)** the input at which
risk becomes **zero** — the number they actually decide on (§5); **(3)** one safety sentence naming
the state where a cheap attempt is near-certain loss (§5); **(4)** an executable transcription — a
dependency-free selftest that reimplements the expression and asserts the boundary cases (§6, §7).
Budget accordingly: the arithmetic is small, and the expensive parts are proving you found *every*
branch (§1) and putting the readout on the right step of the flow (§8).

---

## 1. Find the judge: outcome-string convergence

**(A) HIGH / CONFIRMED.** The judging function is reachable in four mechanical steps, and you do not
need to know anything about the game to run them.

1. **Enumerate every result string the system can print** — success, plain failure, and *each
   distinct catastrophic result separately*. This is the step people short-change: collecting only
   "success" and "failure" finds one branch and convinces you it is the judgement.
2. **Locate them with an encoding-aware scan** — ASCII `strings` will not see a CJK title at all
   ([static-toolkit.md](static-toolkit.md) §10, which also emits the reloc word you search next).
3. **Xref the loaders of all of them at once**: a range query over the string block, collecting
   `ldr [pc, …]` sites and their containing functions ([static-toolkit.md](static-toolkit.md) §9).
4. **Watch them converge.** All the result strings landing inside one small function *is itself the
   evidence* that you hold the whole judgement rather than one arm of it.

**(B)** In the reference port four result strings — success, failure, and two distinct catastrophic
outcomes — were found by an encoding-aware scan and their loaders extracted with a range xref; all
four sat inside one function of roughly `0x130` bytes.

**Then get a second, independent route to that function**
([evidence-standard.md](../../../shared/evidence-standard.md) §3). **(B)** The reference port's came
through the UI — the window table's key-handler entry for that screen, resolved through its
trampoline's literal, landed on the same function. A UI route and a string route share no assumption,
so together they are two derivations.

> **⚠ If the caller query returns nothing, suspect the tooling before the binary.** Some images route
> every cross-module call as `ldr rN, =target|1` + `bl` to a one-instruction `bx rN` trampoline, so a
> BL-based call graph reports zero callers for real functions. Switch to a biased-literal search and
> compute each target as `(pc + 4) & ~3 + imm*4` — [static-toolkit.md](static-toolkit.md) §2.

---

## 2. Pin the die first — an inclusive uniform roll makes a threshold a percentage

**(A) HIGH / CONFIRMED.** Resolve the random generator **before** you read a single threshold. Most
of the perceived difficulty in "decode the rates" is fear of misreading the distribution, and that
fear evaporates the moment the die is pinned.

* If the generator is `min + rand() % (max - min + 1)` called with `(1, 100)`, it is **inclusive
  uniform over [1,100]**, so `roll <= T` occurs `clamp(T, 0, 100)` times in 100 — **a threshold *is* a
  percentage**, and the host readout is literally `clamp(threshold, 0, 100)`. No probability theory
  appears anywhere else in this file.
* **Cross-check the generator's identity by literal equality**, not by reading it twice: if an
  unrelated randomised system in the same image loads the same function pointer, you have confirmed
  the same die from a second direction for free. **(B)** The reference port's upgrade rolls and its
  socket rolls resolve to the same generator by literal equality.
* If the die is 0-based (`rand() % N`) the semantics shift by one, and so does `<` versus `<=`.
  **Either off-by-one is a whole percentage point at every level and is invisible in the output**, so
  transcribe the comparison exactly — including the half that is missing, since game validators are
  routinely one-sided ([live-data-structures.md](live-data-structures.md)).

---

## 3. Read the arithmetic term by term: the recurring shape

**(A)** Expect this shape. Knowing it tells you what to look for and, more usefully, what you are
still missing:

```
cost(n)                    = f(n)                      ; level alone; charged up front
success_T(n, B, C)         = g(B) / h(n)  +  k*C       ; integer division; B RAISES it
    roll1 = die()                                       ; success iff roll1 <= success_T
doom_T(n, B, C)            = p(n) - q*B - r*C          ; ONLY after a failure; B LOWERS it
    roll2 = die()                                       ; catastrophe iff roll2 <= doom_T
        roll3 = die()                                   ; an exact even split between two catastrophes
```

Four properties to confirm one at a time, each a question a partial reading never asks:

* **The cost curve depends on the level alone**, not on the consumed input. Cost and odds are
  separate knobs; do not assume the expensive attempt is the likely one.
* **The consumed resource appears in *two* thresholds with opposite signs.** Spending more helps
  *twice* — it raises success and lowers catastrophe. A reader who stops at the success roll never
  sees the second half, and then advertises a risk figure wrong in the direction that scares players
  off a safe action.
* **A consecutive-failure compensation term**, derived from a raw counter through a *step ladder*
  rather than a linear term, and zeroed on success. Find the counter's incrementer on the failure arm
  and the ladder that maps it; they are usually a few instructions apart.
* **The catastrophic branch is reached only after a failure**, and the split inside it is an exact
  coin flip against the midpoint of the die.

**Integer division is part of the formula** — truncation is the behaviour, not an artefact, and a
host reimplementation in floating point is a different formula that agrees on most inputs.

**(B) The reference instance**, read term by term out of the single function §1 converged on, with
`n` = current level (a record byte, cap 10), `B` = units of the consumable spent, `C` = the stepped
compensation:

```
cost      = 300 * (n+1)^2
success_T = 200*B / ((n+4)*(n+1))  +  6*C          (integer division)
doom_T    = 12*(n+1) - 2*B - 3*C                   (only after a failed roll)
            inside doom: roll > 50 -> outcome X, else outcome Y     (exactly 50:50)
C:          a raw counter incremented on each failure;  <= 4 -> C = 1,  >= 5 -> C = 3
            both the counter and C are zeroed on success
```

**(C)** Those coefficients are one title's balance. They are shown to make the template legible;
reproducing them in your port's UI would be a lie about your game.

---

## 4. Read the join point: what is consumed regardless, and what is reset

**(A) HIGH / CONFIRMED.** All the outcomes merge into one epilogue. Read it — a branch-by-branch
reading skips it, and **the join is where the honest, player-facing facts live**. Three answers come
out of it at once:

1. **What is consumed regardless of result.** **(B)** In the reference port the consumable is spent
   on every path, including outright destruction of the item — "is my resource refunded on failure?"
   is a question every player asks, and the join answers it in one instruction.
2. **What state it resets.** **(B)** The epilogue zeroes the resource count *and* writes the flow's
   step byte back to the previous step. That is the mechanism behind §8's wrong-screen bug, found
   here rather than by debugging the overlay.
3. **Whether the failure streak is cleared on success** — a store in the success arm just before the
   join, completing the compensation term of §3.

---

## 5. The number a player decides on: where risk becomes zero

**(A) HIGH / CONFIRMED.** The percentage table is not the useful output. The useful output is **the
smallest input at which the first roll is certain** — and it carries a second meaning for free:

> **Certainty on the first roll means the catastrophe roll is never reached.** So the same input that
> makes the operation *certain* also makes it *safe*, and one number answers both questions.

State that implication explicitly, in your notes and in the UI copy — it is what turns a table nobody
reads into a single number a player acts on. **Derive it from the success expression; do not measure
it by grinding.** **(B)** Solving the reference port's success threshold for `>= 100` gives
`B = ceil((n+4)*(n+1) / 2)`, i.e. **2, 5, 9, 14, 20, 27, 35, 44, 54, 65** over its ten levels — 275
units to take an item from bottom to cap with zero risk at any point. **(C)** The values are that
title's; the derivation is the transferable part.

**The single safety sentence worth surfacing** is the mirror image of the same algebra: **the state
where one unit of resource buys a nearly-zero success chance and every failure is catastrophic.**
Evaluate `doom_T` at `B = 1` at the highest level — where `doom_T >= 100`, catastrophe is not a risk
but the *default* outcome of a failure. **(B)** At the reference port's level 9 with one unit and no
failure streak, `success_T` truncates to **1** (one percent) while `doom_T` evaluates to **118**,
i.e. clamped certainty; the player loses the item outright with probability
`0.99 × 1.00 × 0.50` = **49.5 %**. That one sentence is worth more to a player than the whole table.

---

## 6. Search the game's own function; keep the closed form in the test

**(A) HIGH / CONFIRMED.** Ship the **search**, keep the **algebra in the test**. This inverts most
people's instinct and it is the right way round:

* **Shipping code**: loop the input upward through *your transcription of the game's own threshold
  function* and return the first value that reaches certainty. It asks exactly the right question of
  exactly the function the game uses, and it stays correct when a live term shifts the answer — the
  compensation term `C` moves the safe count, and a hard-coded ceiling formula does not know that.
* **Test**: assert the closed form against the search across the whole domain.

Two implementations of one fact, in opposite places, so a transcription error in the algebra
**cannot reach the screen** — it fails a test instead. **(B)** The reference port's shipping helper
is a search; the `ceil((n+4)(n+1)/2)` closed form of §5 exists only inside the selftest.

Both live in a dependency-free module that a standalone test binary `include!`s rather than copies
([selftest-convention.md](../../../shared/selftest-convention.md)) — and **write the disassembly
site into the assertion message**. A faithful `include!` test cannot drift from the code, but it
*can* freeze the code's wrong premise as a specification; the citation is what lets the next reader
check the premise instead of trusting the green bar.

---

## 7. Verify by simulating every die face — and what only a device can settle

**(A) HIGH / CONFIRMED.** An inclusive uniform die over a small range makes exhaustive verification
affordable, so there is no excuse for spot checks. Enumerate **every die face over every reachable
input** — levels × resource counts × compensation values — and count outcomes rather than sampling
them. **(B)** The reference sweep is 10 levels × 99 resource counts × 3 compensation values, each
case a full enumeration of three chained rolls (10^6 outcomes).

The simulation is a genuine **second derivation** — an exhaustive count against a closed form,
sharing no assumption — and it is the natural home for the property that matters most: **the
advertised safe count never rolls for catastrophe**, asserted at every level and every compensation
value, because that is the claim a player bets an item on.

> **REFUTED.** *"The published odds table is the reference; the implementation should match it."*
> The reference port's hand-derived table and its shipping calculation disagreed by a tenth of a
> percent in exactly one row — **33.9 against 34.0 on an exact 33.95** — and the exhaustive
> simulation is what identified the *table* as the wrong one. **Why the wrong belief is attractive:**
> a hand table is produced by evaluating the formula on paper, and paper truncates where an
> implementation rounds; both look right, and a tenth of a percent is below the threshold at which
> anyone re-checks. **Consequence:** the published table becomes the authority for a bug report filed
> against correct code.

**What only a device (or a headless probe run) can settle** — static reading gives *what the code
computes*, never *that this code runs on the player's path*
([evidence-standard.md](../../../shared/evidence-standard.md) §6):

* **That the judge you decoded is on the path.** A hook can install cleanly, pass its expected-bytes
  check and fire **zero** times because the operation reaches the outcome another way. Log a fire
  count and compare it against a sibling hook in the same session.
* **Which of two adjacent same-typed byte fields is which** — a level and a counter, a total and a
  filled count. Code that treats a pair symmetrically cannot distinguish them however long you read
  it; `⚠` only a live record whose two values *differ* decides it ([probe-cli.md](probe-cli.md) §7).
* **That the compensation counter advances in play and is zeroed on success**, and that your readout
  appears on the right step at the right moment (§8).

---

## 8. Gate the readout on the step byte, not the window id

**(A) HIGH / CONFIRMED.** A multi-step flow keeps **one** window/screen id from the picker to the
result. The step within it is a **separate byte the flow's own epilogue writes** — which is why §4's
reading of the join point pays for itself here.

> **REFUTED.** *"The window id tells you which step of the flow is on screen."* Refuted by the
> epilogue's own stores: on the way out it zeroes the resource count and writes the step byte back to
> the picker value **while the window id is unchanged**. **Why the wrong belief is attractive:** the
> window id is the field a host overlay already has from every other screen, and it is perfectly
> correct for single-step screens — this is the first place it silently is not. **Consequence:** the
> odds panel draws a confident, precise number over the *item picker* — a wrong number attached to
> the wrong object, in front of an irreversible action.

Gate on the step byte, and **pin the two distinct step values in a test** so a later refactor cannot
collapse them.

---

## 9. On a filtered screen the cursor indexes the filter's table

**(A) HIGH / CONFIRMED.** A screen that shows a *filtered subset* of a container does not address the
container. A builder walks every cell, skips anything the category bound rejects, appends a
coordinate pair into a **side table** and increments a **signed live count**; the cursor indexes that
side table.

Three consequences for a host annotation:

* **The container-coordinate convention that works on every other screen produces a plausible number
  for the wrong object here** — the worst failure a host annotation can have, because it is confident
  and wrong rather than absent.
* **The side table keeps stale entries past the live count.** Never-used entries may hold a sentinel
  (**(B)** `-1` in the reference port), but entries left over from an earlier, longer pass look like
  ordinary valid coordinates. Only the count separates them.
* **Not every guest consumer re-validates against the count.** **(B)** The reference port's builder
  emits `{s16 container, s16 slot}` pairs plus a signed count and three consumers do byte-identical
  arithmetic on it, but only the picker re-checks the count — the roll handler does not. **The host
  guard is then the only bounds check on that path**, so write it.

Resolving that coordinate to an item is a separate problem, and a real one — a `(container, slot)`
pair is not an identity. [live-data-structures.md](live-data-structures.md).

---

## 10. Print it honestly: impossible is not the same as rounds-to-zero

**(A) HIGH / CONFIRMED.** Keep **structurally impossible** and **rounds to zero** as different things
*in the type*, not in the rendering. A readout that prints `0 %` where the truth is "unlikely" lies in
the direction that costs the player the item.

* Carry a separate `safe` boolean **earned by the formula** (the threshold provably cannot be
  reached), never inferred from a rounded percentage being zero; render a sub-threshold marker
  (`<1 %`) for a real-but-small chance.
* **Test both halves, and test that the confusing case is *reachable*:** assert that `safe` implies
  impossible, *and* count the inputs where the printed value rounds to zero while the chance is real,
  failing if that count ever becomes zero. **(B)** The reference port's property test does exactly
  this pair — without the second half it would pass vacuously the day a refactor removes the case it
  was written for.
* Print what §4 told you alongside the odds: **the resource is consumed regardless of outcome**. An
  odds panel that omits it is technically accurate and practically misleading.

---

## 11. Symptom → cause

| symptom | cause | first thing to do |
|---|---|---|
| Your odds panel prints over the item picker, or over the result screen | Gated on the window id, which does not change across the flow | §8 — find the step byte in the epilogue's stores (§4) |
| A published table disagrees with the shipping calculation by a tenth of a percent | The table was hand-derived and truncated where the code rounds | §7 — let the exhaustive simulation decide which is right |
| A host annotation names the wrong item, but only on one screen | That screen is filtered; the cursor indexes the filter's side table | §9 — bounds-check against the live count yourself |
| The "safe" count you advertise sometimes destroys the item | The closed form was transcribed by hand into shipping code | §6 — ship the search, keep the algebra in the test |
| Your risk figure scares players off an action that is actually safe | You read the success roll only; the resource also *lowers* the catastrophe threshold | §3 — find the second appearance of the resource term |
| Players say the resource is gone after a failure and your model says otherwise | You read the branches, not the join | §4 — read the epilogue |
| The xref for the judge returns no callers, or two readers disagree about which function a `bl` reaches | Indirect `ldr`+`bl`-to-`bx` trampolines; both readers are inferring the callee from context | Compute `(pc + 4) & ~3 + imm*4`, read the word, then search for it. [static-toolkit.md](static-toolkit.md) §2 |
| A player loses an item after your readout said `0 %` | Rounds-to-zero conflated with impossible | §10 — separate `safe` flag, sub-threshold marker |

---

## 12. Checklist

- [ ] **Every** result string collected — success, plain failure, and each catastrophe separately — and their loaders converge on one function, reached a second time by an independent route (§1).
- [ ] The die is pinned: range, inclusivity, and `<` vs `<=` transcribed exactly, including the missing half (§2).
- [ ] The cost curve, **both** appearances of the consumed resource, the stepped compensation term and the even split inside the catastrophe branch are each accounted for, with integer division reproduced as integer division (§3).
- [ ] The join point read: what is consumed regardless, what is reset, what clears the failure streak (§4).
- [ ] The zero-risk input derived from the success expression, with its "certain implies safe" argument written down (§5).
- [ ] The one safety sentence — cheapest attempt at the worst level — computed and surfaced (§5).
- [ ] Shipping code searches the game's own threshold function; the closed form lives only in the test (§6).
- [ ] Exhaustive simulation over every die face and every reachable input, asserting the advertised safe count never rolls for catastrophe (§7).
- [ ] Assertions cite the disassembly site they transcribe; rejection lists derived from the game's gate, not from your implementation (§6, [selftest-convention.md](../../../shared/selftest-convention.md)).
- [ ] Readout gated on the step byte, with both step values pinned in a test (§8).
- [ ] On a filtered screen, the cursor resolved through the filter's side table and bounds-checked against its live count (§9).
- [ ] `safe` is earned by the formula; small-but-real prints as a sub-threshold marker; the reachability of that case is itself asserted (§10).
- [ ] Every constant above recorded with its method, its confidence pair and its date — [evidence-standard.md](../../../shared/evidence-standard.md).
