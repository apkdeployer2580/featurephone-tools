# Answering on the server's behalf: imitate the client's algorithm, not its intent

Once the host decides what a request is worth — how many units fit, how much capacity is left, which
purchase succeeds — it is choosing numbers that a twenty-year-old client will then *execute*. The
client does not validate them. It applies them through whatever procedure its authors wrote, and
where that procedure is lossy it loses **silently**: no error frame, no log line, no exception, just
fewer items than the player paid for. So the obligation is not to model the client's *intent*
("deliver 99 potions") but to reimplement its *algorithm* ("place at most twice, merge-first,
discard the rest"), exactly, including its retry count and what it does with each return value.

This file is that discipline. The engine that hosts these decisions is
[in-process-engine.md](in-process-engine.md); how to *say no* without wedging the client is
[refusals-and-holds.md](refusals-and-holds.md); deriving reply layouts at all is
[reply-re.md](reply-re.md).

**(A)** generic procedure · **(B)** worked example from the reference port, always introduced by the
method that found it · **(C)** do not copy · `⚠` inferred, nothing was run. See
[evidence-standard.md](../../../shared/evidence-standard.md).

## Contents

1. [The discipline in one sentence](#1-the-discipline-in-one-sentence)
2. [★ The worked case: two placements, merge-first, and a discarded return](#2--the-worked-case-two-placements-merge-first-and-a-discarded-return)
3. [Reading a placement routine: retries, return values, scan ORDER](#3-reading-a-placement-routine-retries-return-values-scan-order)
4. [Write it as an executable model that returns what was DELIVERED](#4-write-it-as-an-executable-model-that-returns-what-was-delivered)
5. [Derive every bound by RUNNING the model, over sequences](#5-derive-every-bound-by-running-the-model-over-sequences)
6. [Model the state at the instant the client acts](#6-model-the-state-at-the-instant-the-client-acts)
7. [★ Who owns the effect? An acknowledgement can mean "you grant it yourself"](#7--who-owns-the-effect-an-acknowledgement-can-mean-you-grant-it-yourself)
8. [Before suppressing a client-side duplicate, enumerate what the host hangs off it](#8-before-suppressing-a-client-side-duplicate-enumerate-what-the-host-hangs-off-it)
9. [★ Where the client has NO gate, only the host can refuse](#9--where-the-client-has-no-gate-only-the-host-can-refuse)
10. [Capacity is charged on slot allocation, not on the operation](#10-capacity-is-charged-on-slot-allocation-not-on-the-operation)
11. [★ Read WIDTH and SIGNEDNESS for every quantity the host chooses](#11--read-width-and-signedness-for-every-quantity-the-host-chooses)
12. [The same machine in three more places at once](#12-the-same-machine-in-three-more-places-at-once)
13. [Zero is not "nothing", and the byte you think is a count may be a status](#13-zero-is-not-nothing-and-the-byte-you-think-is-a-count-may-be-a-status)
14. [Echo-as-success: which byte does the client actually test?](#14-echo-as-success-which-byte-does-the-client-actually-test)
15. [Assert the client's constraint, never the bytes you currently emit](#15-assert-the-clients-constraint-never-the-bytes-you-currently-emit)
16. [Display is never enforcement](#16-display-is-never-enforcement)
17. [Symptom table](#17-symptom-table)
18. [Checklist](#18-checklist)

---

## 1. The discipline in one sentence

> **A reasonable model of the client's behaviour authorises operations the client then executes
> lossily, and the loss is silent.**

Confidence `HIGH / CONFIRMED`. The failure has no error path anywhere: the host approves, the client
runs its own code, some units cease to exist, and both sides believe the transaction succeeded. It
reaches the player as "items disappear sometimes" — and only for the players whose inventories are
nearly full, which is the invested ones. **(B)** In the reference port three separate features were
destroying items through the same client routine at the same time (§12), and none of them had ever
logged anything.

The rule that follows: **no host-chosen quantity ships without an executable model of the client's
own apply procedure.** That is an exit criterion, not a nicety — see
[conformance-tests.md](conformance-tests.md) assertion class 4.

## 2. ★ The worked case: two placements, merge-first, and a discarded return

**(B)** Method first: the reference port took the wire opcode that grants an item, found its reply
handler in the category dispatcher ([reply-re.md](reply-re.md) §1), and disassembled the handler *to
its end* rather than to its first interesting call. That produced three facts, in this order:

1. The handler calls the game's grant routine **once**.
2. It then inspects the leftover the routine returned and calls it **exactly one more time**.
3. It **discards that second return value.** There is no loop. Anything still unplaced is gone.

Then the grant routine itself: its cell scanner walks **every merge target in every container**
before it looks at a single free cell (a merge scan followed, textually and in control flow, by a
separate free scan). A cell qualifies as a merge target on two conditions only — its `(category,
index)` match, and its count is at or below `stack cap − 1`. Nothing else is read.

The counterexample that this produces is the whole point, and no reading of the *intent* reaches it:

> **(B)** A bag holding **50 + 50** of a potion and **ten empty cells** destroys a unit of a 99-unit
> delivery. Placement one merges into the first 50 → 99, leftover 50. Placement two merges into the
> second 50 → 99, leftover 1. The ten free cells are never reached. A "total free space" model
> computes 49 + 49 + 10×99 = **1088 units of room** and waves the delivery through.
>
> The true ceiling for that bag is **98** (= 2×99 − 50 − 50).

Two adjacent behaviours from the same reading, both load-bearing:

* **Total failure is not partial failure.** When there is no room at all, the routine sets a
  bag-full flag and branches straight to its epilogue, **skipping the instruction that would load
  the leftover into the return register** — so it returns a sentinel, the single retry hits the same
  wall, and that return is discarded too. The units are simply gone, and by then the host has
  already removed them from its own store.
* **A fresh cell takes the count byte verbatim, with no cap at all.** The 99 cap lives on the
  *merge* path only. That is why the wire count must be clamped before it is ever sent (§11).

## 3. Reading a placement routine: retries, return values, scan ORDER

**(A)** The procedure, for any client, any grant/consume/transfer routine:

1. **Find the handler for the frame that carries the quantity**, not the routine it calls — the
   retry policy lives in the *caller* and is invisible from the routine.
2. **Count the calls.** One, two, a bounded loop, an unbounded loop. That number is specification.
3. **Follow every return value.** Stored, compared, or dropped on the floor. A dropped return is the
   single most reliable predictor of this bug class.
4. **Find the scan ORDER before the scan predicate.** "Which cells qualify" is the question everyone
   asks; "in what order, and does it stop at the first hit" decides whether free space is reachable.
5. **Read the early-exit arms.** An error path that jumps over the return-value setup produces a
   sentinel that looks like a huge or zero leftover, and callers rarely distinguish.
6. **Read the special-cased categories.** **(B)** In the reference port equipment never merges and
   its count is forced to 1 by the routine, so one grant frame delivers exactly one piece however
   large a number the host puts in it.

⚠ Steps 2–4 are static reading. Settle the model dynamically before building on it
([evidence-standard.md](../../../shared/evidence-standard.md) §6): the reference port confirmed it
by running the real engine against constructed inventories and comparing delivered counts.

## 4. Write it as an executable model that returns what was DELIVERED

**(A)** Transcribe the procedure into a pure function in the host's language:

```
fn place(state: &mut Inventory, kind: Kind, n: u32) -> u32   // returns units ACTUALLY delivered
```

Three properties make it useful rather than decorative:

* **It returns what was delivered, not whether it succeeded.** The gap between `n` and the return
  value is exactly the loss you are preventing, so it must be a value, not a flag.
* **It mutates a simulated state**, so several operations can be run in sequence (§5).
* **It lives in a dependency-free module**, `include!`d by a standalone test binary
  ([selftest-convention.md](../../../shared/selftest-convention.md)). This model is the thing a
  refactor is most likely to "simplify"; the tests are what stop that.

**(A)** Comment each branch with the guest site it transcribes — a branch that returns 0 because the
guest jumps over its own return-value setup reads as a bug in your model until the comment explains
it.

## 5. Derive every bound by RUNNING the model, over sequences

**(A)** Do not attempt a closed form. "Merge-first, exactly two placements, remainder discarded" has
no useful algebra, and every closed form anyone writes for it is the "total free space" formula that
started the problem. Instead:

```
fn fits(state, wants: &[(Kind, u32)]) -> bool      // simulate, in order, on a clone
fn room (state, kind) -> u32                       // walk down from the wire cap; first n that fits
```

**(B)** The reference port's `room` walks down from the stack cap and takes the first quantity that
survives the model — at most ~99 iterations of a handful of comparisons, once per operation, never
per frame. The cost is a non-issue; the alternative is a formula that is wrong.

**(A) Simulate sequences, not single operations.** Where a delivery carries several kinds they
compete for the same free cells, and checking each independently says yes once per kind for a bag
with room for one. **(B)** The reference port names that assertion for the property rather than the
code: *a multi-kind delivery shares the free cells*.

**(A) A failed read of the outside world means "no limit known", never "at the limit".** The model
needs a snapshot of client state the host cannot always take (see
[in-process-engine.md](in-process-engine.md) on publishing platform-only facts as data). An absent
snapshot must bound nothing, set no floor, and never refuse something the player already paid for.
Fail-closed is right for *writes into guest memory* and wrong for *limits derived from an optional
observation*; one project-wide rule for both is how this gets inverted.

## 6. Model the state at the instant the client acts

**★ (A)** Your snapshot is not the state the client will apply your answer to. Read the client's
**send** path and find out what it mutates before it builds the frame.

**(B)** In the reference port the deposit path clears the source cell **and saves** before it
assembles the commit frame, so the remainder handed back after a partial deposit lands in a bag one
cell emptier than the snapshot shows. Modelling the raw snapshot answered "49 fit" where the true
answer was **2**. The comment marking the obvious version as wrong sits at the definition site,
which is where the next reader will be about to re-introduce it.

**(A)** Two disciplines fall out:

* Model the post-mutation state explicitly for any frame that arrives *after* the client committed.
* When the snapshot is present but **stale** — it does not contain the cell the client says it is
  sending — model the *more conservative* of the two candidate states. **(B)** The reference port
  models the pre-clear bag there: one free cell short of the truth, because erring toward "transfer
  more" costs the player a choice while erring the other way costs an item.

## 7. ★ Who owns the effect? An acknowledgement can mean "you grant it yourself"

**(A)** In a two-leg request/commit protocol, a success reply is not a receipt. It can be an
**instruction**: the client's success arm sends the commit, and the commit's reply handler performs
the delivery itself out of its own cached row. Acknowledging *and* delivering therefore delivers
twice.

**(A) How to tell which side owns the effect:** read the reply handler to its end and see whether it
calls the game's own grant/apply routine. If it does, the effect is the client's, and your job is to
choose a reply class that does or does not trigger it. Nothing about the frame's name tells you
this.

**(B)** The reference port's chain, recovered by disassembling all three handlers:

| step | what the code does |
|---|---|
| request builder | sends `<purchase request><key><count>` |
| purchase reply handler | gates on `body[0]`; **zero** → open a message box on the frame's own text and **stop**; non-zero → send the commit |
| commit reply handler | same gate; on non-zero, copies the client's **own cached shop row** and calls the grant routine — that is the delivery |

The symptom that exposed it was a player reporting an inert duplicate appearing in their own storage
next to the item the engine had delivered properly.

**★ (A) The reply class is the off switch.** Where the host owns the effect, answer with a
**status-zero-plus-text** frame instead of an ack. **(B)** In the reference port that frame opens the
client's own message box, reading its text from the frame body, and **ends the transaction there** —
the commit leg is never sent, so the client's delivery path is structurally unreachable. The same
lever had already been in use for a *refusal* ("no commit follows a failed purchase"); this is the
same lever used for a *success*.

Three constraints on that frame belong to [refusals-and-holds.md](refusals-and-holds.md) and are
named here only because they bite in this section: the reason text must be **in the same frame** (a
bare status-only ack makes the message box read past the end of it); the frame must be **sent**, as
silence behind a reply-gated wait is an unrecoverable hang; and you must read the **whole** handler
before choosing any reply class, because a message-box category can also tear down the connection.

## 8. Before suppressing a client-side duplicate, enumerate what the host hangs off it

**(A)** Once §7 identifies a client-side copy as inert, the obvious cleanup is to stop it appearing.
Before you do: **enumerate everything the host attaches to that copy.** In a long-lived port,
host-side *substitutes* accumulate — effects built while a real capability was missing — and a
substitute routinely outlives the arrival of the real thing.

> **REFUTED.** The reference port's first cut suppressed two symmetric-looking client copies at once.
> Adversarial review found that one of them **was a feature**: the host granted a reward when the
> player consumed that copy, because at the time the host had no way to deliver the real item, and
> the copy was the only trigger available. A later session had built exactly that ability, so the
> substitute had outlived its reason — but nothing said so, and the two copies looked identical from
> the wire. **Why the wrong reading was attractive:** the substitute's own definition site carried a
> comment explaining that it was a stand-in, and that comment had been true when written; symmetry
> at the protocol layer hides asymmetry at the feature layer.

**★ (A) Order is essential: retire the substitute first, then suppress its carrier.** Reversed, the
feature vanishes with no error and no test failure — the reward code is still there and still
correct, merely unreachable.

## 9. ★ Where the client has NO gate, only the host can refuse

**(A)** Enumerate the client's own validity checks on the **send** path for every operation. Where
there are none, the host is the only party that can refuse — and the refusal has to be decided
**before anything moves**, because a request the host silently drops while the client believes it
succeeded **destroys player property**: the client has already emptied its own slot and shown its
own success message.

**(B)** Method: read the whole send arm from the key handler to the frame builder and list every
branch — the absence is the finding. The reference port's deposit arm checks a worn flag,
tradability, a grade and three category exclusions, and has **no capacity check at all**; the only
code anywhere in the client that reads the storage counters sits in *reply* handlers.

**(A) The complementary gate covers nothing you needed.** A client frequently *does* gate the
reverse operation, but only in the extreme case. **(B)** The reference port's withdraw path calls the
same cell scanner in a free-cells-only mode and skips its capacity test entirely whenever one free
cell exists; when the bag is completely full its predicate agrees with the host's model. The two
gates complement exactly and **neither covers the other's case** — where the client has slack, the
host's limit is the only limit in the system.

**(A) A refusal discovered after the client has committed must hand the item back**, using whichever
reply frame the protocol already has for granting. Answering with a bare state refresh at that point
is item destruction. Mechanics — which frame, the give-back's stateless handler, and the rule that a
pending give-back must be **dropped** when a different record's commit arrives — are
[refusals-and-holds.md](refusals-and-holds.md)'s.

## 10. Capacity is charged on slot allocation, not on the operation

**(A)** Three rules that come as a set:

* **Charge capacity only when a NEW slot is actually needed.** An operation that merges entirely
  into existing stacks consumes no slot and must succeed **even in an already-over-capacity store**.
  Without the exception, the "how much fits" helper proposes a quantity that the commit path then
  refuses, and the feature contradicts itself.
* **Cap what comes IN, never what is DISPLAYED.** Rows an older build overflowed in must stay
  visible and retrievable, or the player can see items they cannot reach. **(B)** The reference
  port's page count deliberately still reports `max(purchased_pages, rows / page_size)`.
* **A runaway guard is not a capacity model.** **(B)** The reference port's only ceiling on stored
  rows was a per-account abuse cap far above any purchasable capacity, so a one-page store accepted a
  seventeenth row and then reported two pages — measured, not assumed: `rows=17 pages=2`. The player
  was handed a page they never bought, and the shop item whose entire purpose is to sell that page
  **sold nothing**. Real capacity was derivable from the purchasable unit (`pages × slots_per_page`);
  the abuse cap survives only as defence in depth around the low-level insert, which other callers
  also use.

⚠ The last one is in [fault-dictionary.md](../../../shared/fault-dictionary.md) as a fault that reads
as a different phase's fault: it presents as a **broken shop item**, and a porter debugging the shop
will never find the cause there.

## 11. ★ Read WIDTH and SIGNEDNESS for every quantity the host chooses

**(A)** [reply-re.md](reply-re.md) tells you to check width and endianness for every field you
encode. **Signedness is the third third of that audit, and it is the one that bites at the top of
the range** — i.e. only for the most invested players, long after the feature shipped clean.

Procedure: for every quantity the host *chooses* (as opposed to echoes), find every instruction that
loads it in the client and classify the load. The tells are `ldrsb`, `ldrsh`, and an
`lsls #24` / `asrs #24` pair (or `#16` for halfwords) immediately after a plain `ldrb`/`ldrh`. Then
**clamp to the client's signed range. Never mask.**

**(B)** Three instances in one title, all read straight off the disassembly:

1. **Two list-header bytes are signed chars.** A legitimate capacity of 128 became −128. The
   parser's own clamp (`if ceiling < cursor: cursor = ceiling`) then wrote −128 into the cursor, and
   both page-turn gates failed simultaneously — the previous-page gate tests `cursor > 0`, the
   next-page gate tests `cursor + 1 >= ceiling`. Reported by a player as *the page keys disappeared*,
   with the correct guess attached: it started when they bought the eighth capacity item.
2. **A stack-count byte is sign-extended by the grant routine**, so any value at or above 128
   **subtracts** from the player's stack instead of adding to it. Everything else in that subsystem
   rests on this invariant, which is why it is enforced on **load** of the persistent store as well
   as on write.
3. **The shipped bug arrived through `& 0xFF`** — precisely the operation that passes the sign bit
   through untouched. The fix was `min(value, 0x7F)`.

Two details worth carrying: the **cost of clamping was one displayed digit** (a denominator reading
127 instead of 128) while storage capacity was unaffected; and a **sibling screen in the same client
was fine** because its equivalent branch compares 32-bit words rather than bytes — which is why "the
other page arrows work" is not evidence against a signedness bug.

## 12. The same machine in three more places at once

**(A)** When you find a lossy client routine, **enumerate every path that reaches it** before you fix
the one that was reported. It is a routine, not an incident.

**(B)** In the reference port, adversarial re-derivation against the real engine found three more
live losses on the same two-placement grant, and measured each:

| site | what was happening | fix |
|---|---|---|
| the remaining shop rows the **client** delivers | the host's quantity spinner allowed 1–99 with no bound at all. Bag with two 98-stacks and five free cells, buy 99 → **2 arrive, 97 vanish.** The client's own gate only runs when the bag is already completely full, so it caught nothing here | bound the quantity at the commit key using the same `room` helper |
| the **remainder of a partial deposit** | it comes back through the same grant frame. Bag `[50 being deposited, 98, 98]` with no free cell, deposit 1 → **2 of the 49 return, 47 vanish** | give the quantity prompt a **floor** = the smallest transfer whose remainder the destination can still take, computed on the post-clear bag (§6) |
| a **pending give-back with no matching commit** | a remainder still armed when a *different* record's commit arrives is paid out against that commit — duplication, the mirror image of the loss | drop the pending remainder; the client never cleared that cell |

**★ (A) Two of those three fixes are quantity *bounds*, and they must be applied to the item that
actually needs them.** In the reference port the two rows the *engine* delivers are refused outright
when the bag cannot hold the lot (a purchase the player cannot receive must not take their money),
while the rows the *client* delivers are bounded at the commit. Bounding both the same way stacks
inert copies in the bag; refusing both the same way blocks legitimate purchases. The predicate that
splits them is a single named function, not an inline condition, precisely so it can be tested.

## 13. Zero is not "nothing", and the byte you think is a count may be a status

> **REFUTED CANDIDATE FIX, preserved.** The reference port considered stopping an unwanted
> client-side delivery by writing **0** into the count field of the row the client copies. Dug to the
> end and dropped. Four facts killed it, in the order they were found:
>
> 1. the grant routine **claims the destination cell before it reads the count** (an occupancy store
>    several instructions ahead of the first count load), so zero produces an occupied cell, not an
>    absent one;
> 2. the grid painter prints that number with a bare integer format and **no zero gate**;
> 3. the merge scanner filters only on "not nearly full", so a zero row is an **eternal merge
>    target**;
> 4. the only count value the client special-cases is **−1**, and it uses it solely to omit an entry
>    from an acquisition list.
>
> **Why it was attractive:** zero is the natural "nothing" sentinel in every language the reader
> knows, and the row *is* a scratch copy, so writing zero feels like clearing a buffer rather than
> like creating an object. **Consequence if carried forward:** a permanently visible row of quantity
> zero that the player can pick up and that absorbs every future merge.

**(A) Generalise:** before choosing any sentinel, read the client's **painter** and its **merge/scan
predicates**, and find the value the client actually special-cases. A sentinel is only a sentinel to
code that tests for it.

**★ (A) Register provenance.** The same investigation corrected a misreading of a nearby load as "an
early return when the count is zero". The base register there holds **the received packet**, not the
row — it is reassigned two instructions later, and the text-box arm reads its message from an offset
into the same structure — so that byte is the **reply status**, which is the premise the shipped fix
stands on. Whenever a load "confirms" your model, re-derive what its base register is holding at that
point ([evidence-standard.md](../../../shared/evidence-standard.md) §3).

## 14. Echo-as-success: which byte does the client actually test?

**(A)** Never echo a request back as a reply — construct it from server state
([server-architecture.md](server-architecture.md)). Where an echo already exists and you cannot
change it, you owe the audit: **identify the exact byte the client tests for success and prove it
cannot take the failure value.**

**(B)** In the reference port the commit reply is a verbatim echo, and the client gates on `body[0]`,
which is the **first byte of the echoed key** — the item index. No catalogue row has index zero. That
coincidence is the *entire* mechanism by which the echo reads as success, and nothing in the code
says so. Anyone adding a row with index zero silently turns every purchase into a failure.

⚠ Both the reference server's comment and the port's comment described that field as an *amount*; the
send-side frame builder disproves it. When two implementations agree about a field's meaning, check
whether they agree because both read the builder or because one copied the other.

## 15. Assert the client's constraint, never the bytes you currently emit

**★ (A)** A regression test that pins the client's *current* byte-level behaviour, without asking
whether the client can survive that byte, is not protection — **it is what ships the bug.**

**(B)** Two instances in the reference port:

* The signedness bug of §11 was guarded by a passing test asserting that eight pages is 128 slots
  and still fits in one byte. It pinned the exact value that broke the client, and the session that
  found the bug had to correct its own green test.
* A later suite's negative-case list for "is this category equipment?" contained category `0x00` —
  the first equipment category. A green 23-assertion module was therefore **proving that helmets are
  not equipment** across two shipped versions. The client's own gates on that value are
  **upper-bound-only** (`cmp #max; bgt reject`, with no instruction testing zero anywhere), which is
  what settled it.

**(A)** The phrasing rule: assert the constraint the **client imposes** ("the header never exceeds
the client's signed maximum"), not the number the encoder happens to emit ("the header is 128"). And
when three components must agree on a set — engine, server, and the in-game path — **assert that they
agree**, because a test written against one of them cannot see the other two drifting.

## 16. Display is never enforcement

**(A)** Where the host renders a list of things the player may act on, hiding or greying an
ineligible row is a **display** decision made for clarity; the apply path re-derives eligibility
independently from the same predicate. **(B)** The reference port's combine picker shows ineligible
items as disabled rows carrying their refusal reason, and its apply function calls the eligibility
predicate again before writing anything.

⚠ The corollary is the more useful half: **a filter applied before the list is built is invisible to
the player as a refusal.** A row that never appears cannot explain itself, so the report arrives as a
question about the game's design rather than as a bug report — which is how the category-zero bug in
§15 was reported, and why it survived so long.

## 17. Symptom table

| symptom | cause |
|---|---|
| items silently disappear during a transfer or a delivery; only for nearly-full inventories | the host authorised a quantity using a "total free space" model instead of the client's placement procedure (§2) |
| the host approves a quantity, then the client refuses that same quantity | the model used the raw snapshot; the client mutates and saves before it sends (§6) |
| a remainder the player chose to keep comes back much smaller than it left | the remainder travels back through the same lossy grant; the prompt needs a floor (§6, §12) |
| an inert duplicate of a purchased item appears in the player's own inventory | you acknowledged a request whose ack means "you grant it yourself" (§7) |
| a feature quietly stops working after you suppress a client-side duplicate | the host hung a substitute effect off that copy; retire the substitute first (§8) |
| the client shows success and empties the slot, but the item never arrives | the host refused by dropping a request the client has no gate for (§9) |
| a shop item that sells capacity appears broken | the only limit was a runaway guard, so the store handed out capacity nobody bought (§10) |
| page keys disappear, or a cursor jumps to an impossible value, at high capacity | the client reads that header byte as signed; you masked instead of clamping (§11) |
| a quantity the host writes SUBTRACTS from the player's stack | the grant routine sign-extends the count; clamp on load as well as on write (§11) |
| a "cleared" row is still visible and absorbs every later merge | zero was chosen as a sentinel the client does not test for (§13) |
| every purchase fails right after a new catalogue row is added | the echo read as success only because no row's index was zero (§14) |
| the whole suite is green and the bug is shipped | the tests pin the bytes you emit rather than the constraint the client imposes (§15) |

## 18. Checklist

Before any host-chosen quantity, capacity or approval ships:

- [ ] The client's placement/grant routine is transcribed as an executable model that returns **units
      delivered**, not a boolean.
- [ ] The model's **call count** and the fate of **every return value** are read from the *caller*,
      not inferred from the routine.
- [ ] The scan **order** is reproduced, not just the scan predicate.
- [ ] Bounds are derived by **running** the model; no closed-form capacity formula anywhere.
- [ ] Multi-item operations are simulated **as a sequence** against mutated state.
- [ ] The state modelled is the state **at the instant the client acts**, including anything the
      client clears or saves before sending.
- [ ] A failed read of client state means **no limit known** — it never refuses and never sets a floor.
- [ ] For every reply class: it is established **which side owns the effect**, by reading the handler
      to its end.
- [ ] Every client-side duplicate you suppress has had its **host-side dependents enumerated first**.
- [ ] Every operation's client-side send path has been read for gates; where there are none, the host
      refuses **before anything moves**, and explicitly.
- [ ] Capacity is charged on **slot allocation**; caps apply to what comes in, never to what is shown.
- [ ] Every host-chosen quantity has had its client read **width, endianness and signedness** audited;
      it is **clamped**, never masked.
- [ ] Every path that reaches the lossy routine has been enumerated, not just the reported one.
- [ ] Sentinels were chosen after reading the client's painter and merge predicates.
- [ ] Assertions state the **client-imposed constraint**; the counterexample of §2 exists as a named
      test.
- [ ] Where several components must agree on a set, a test asserts that they agree.
