# Mutating the guest's live data — records, identity, caches and copies

A host feature that adds something the original never had — a socketing verb, an upgrade screen, a stash, an item editor — almost always reduces to writing a few bytes into a record the game is holding in RAM right now. This file is about doing that without corrupting the save, without mutating the wrong object, and without shipping a change the player cannot see.

Owns: record-layout discovery, instance identity, derived caches and snapshot copies, choosing which of the game's own rebuild routines to call, transcribing the game's validators, and write-plan discipline.
Not here: probability and reward **formula** decoding ([odds-and-formulas.md](odds-and-formulas.md)); instruction encoding and RAM patching ([patching.md](patching.md)); the mechanics of issuing a host-originated call ([calling-guest-code.md](calling-guest-code.md)); the static queries themselves ([static-toolkit.md](static-toolkit.md)).

**(A)** generic procedure · **(B)** worked example from the reference port, always introduced by the method that found it — your value will differ · **(C)** do not copy. `⚠` marks anything inferred. Conventions: `${CLAUDE_PLUGIN_ROOT}/shared/evidence-standard.md`.

## Contents

- [0. The question that comes before the design](#0-the-question-that-comes-before-the-design)
- [1. Record-layout discovery — the six-site rule, and the offset that is a field and not a base](#1-record-layout-discovery--the-six-site-rule-and-the-offset-that-is-a-field-and-not-a-base)
- [2. The save serialiser is the durability boundary](#2-the-save-serialiser-is-the-durability-boundary)
- [3. This can delete an entire planned network feature](#3-this-can-delete-an-entire-planned-network-feature)
- [4. Identity: a (container, slot) pair is not an identity](#4-identity-a-container-slot-pair-is-not-an-identity)
- [5. Compaction — the address survives, the object does not](#5-compaction--the-address-survives-the-object-does-not)
- [6. Derived caches: one producer, and never a host reimplementation](#6-derived-caches-one-producer-and-never-a-host-reimplementation)
- [7. There is more than one copy — finding every copy of a field](#7-there-is-more-than-one-copy--finding-every-copy-of-a-field)
- [8. Live read vs cache read, decided at the draw site](#8-live-read-vs-cache-read-decided-at-the-draw-site)
- [9. Choosing which rebuild to call, and which to refuse](#9-choosing-which-rebuild-to-call-and-which-to-refuse)
- [10. When the second cache is un-callable, restrict the feature](#10-when-the-second-cache-is-un-callable-restrict-the-feature)
- [11. Asynchronous calls: sweep the container, do not chase the cell](#11-asynchronous-calls-sweep-the-container-do-not-chase-the-cell)
- [12. Re-trigger on the screen edge; an empty sweep is a failed read](#12-re-trigger-on-the-screen-edge-an-empty-sweep-is-a-failed-read)
- [13. Do not put plausibility gates in a resolver](#13-do-not-put-plausibility-gates-in-a-resolver)
- [14. Gate polarity: transcribe the comparison, including its missing half](#14-gate-polarity-transcribe-the-comparison-including-its-missing-half)
- [15. Write-plan discipline](#15-write-plan-discipline)
- [16. Borrow the verb, own the object](#16-borrow-the-verb-own-the-object)
- [17. Symptom table](#17-symptom-table)
- [18. Checklist](#18-checklist)

---

## 0. The question that comes before the design

**(A) Before you design a protocol, a save-format extension or a new screen for a feature, dump the container record around the object the feature mutates and ask: does the record already carry the state?** An inventory or equipment record usually carries its entire subsystem — not a pointer to it, not a server-side mirror of it, the state itself — in which case the whole feature is a handful of record-byte writes and no transport at all.

The dump is cheap: find the container, find the record stride, and print the stride's worth of bytes around a cell whose contents you can change from inside the game. Change one thing, dump again, diff. See [probe-cli.md](probe-cli.md) §8 for the differential-RAM mechanics.

**(B)** In the reference port, one session mapped a `0x3c`-byte inventory record from six independent guest sites and found total-socket count, socketed-item id, filled-socket count, refinement level and the worn flag all present. A feature that had been written off as server-only shipped as three byte writes and was device-verified. `HIGH / CONFIRMED`.

---

## 1. Record-layout discovery — the six-site rule, and the offset that is a field and not a base

**(A) One site tells you a record exists; six tell you what it is.** Look for these roles, because each touches a different subset of the record and the union is the layout:

| # | role | what it proves |
|---|---|---|
| 1 | the grant path that creates an object from an external source (wire, drop, purchase) | which fields are *inputs* |
| 2 | the serialiser that packs a record for the wire | which fields the original service cared about |
| 3 | the **save serialiser** | which fields are durable (§2) |
| 4 | the **save loader** | the mirror of 3, plus what is rebuilt rather than restored |
| 5 | the routine that derives stats from a record | which fields are *read* by the game's own maths |
| 6 | the generator that rolls a new instance | field ranges and defaults, for free |

Where two of the six disagree about a field, your reading of one of them is wrong — that disagreement is the most useful signal in the exercise. Confirm each field with two independent derivations before you build on it ([recipes.md](recipes.md) Recipe D).

### Two adjacent same-typed fields cannot be settled by reading

> **REFUTED.** *Previously documented:* in a 20-byte record, the total-socket count and the filled-socket count were assigned to each other's offsets, and the assignment was cited from code.
> *Refuted by:* a real record read from a live device whose two values differed.
> *Why the wrong reading was attractive:* every site that touches the pair treats it **symmetrically** — both are unsigned bytes, both are bounded by the same small number, both are copied together — so both readings disassemble perfectly.
> *Consequence if carried forward:* behaviour that is almost right, which is the hardest kind of wrong to notice.

**(A) Rule:** two adjacent same-typed fields are settled only by observing a record whose two values differ. Plan a probe run or a device read that produces one, rather than reading harder.

### ★ The offset that is a field, not a base

**(A)** A constant lifted from a listing is an offset **from whatever base that instruction used**. If the site you copied from addressed, say, the first record's *name* field, the constant already contains the array's base offset.
Adding a record-relative field offset on top of an address you computed as `container + array_off + tab*tab_stride + slot*rec_stride` then adds the array base **twice** and writes into a neighbouring item's record — a mutation that looks exactly like the compaction bug in §4 and is not.

> **Rule: re-base every field constant to the record itself (`0x00 .. rec_stride-1`), and keep exactly one function in the host that turns `(tab, slot)` into a record address.** Never mix re-based constants with absolute-from-container constants in one module, and do not import the absolute forms from a sibling module that predates the re-basing.

**(B)** The reference port's socketing module carries that rule as a header comment because the trap was live: the constant that read like the record array's base was the offset of a *field* of the first record, and the module's field constants were re-based to `0x000..0x035` inside the `0x3c`-byte record so the double-add became inexpressible. `HIGH / CONFIRMED`.

---

## 2. The save serialiser is the durability boundary

**(A) The save serialiser's per-record conditional defines the host's durable-write set.** The fields it copies are the ones a host mutation survives on; everything else in the record is a cache that the loader rebuilds. Read the per-record loop, note the unconditional prefix and the conditional tail, then read the loader's mirror — including the recompute it calls after restoring.

**(B)** In the reference port the serialiser wrote `[occupied, cat, idx, key×8]` for every cell and, only under `cmp #0xc / bgt`, six more bytes: refinement level, total sockets, socketed id, filled count, worn flag and worn slot. The loader restores those six and then calls exactly one recompute to rebuild two derived halfwords (§6). `HIGH / CONFIRMED`.

**Pin the boundary in a test.** Make the host's mutation produce a *write plan* — a list of `(address, bytes)` — from a pure function, then assert over an exhaustive corpus of inputs that every emitted address falls inside the durable bytes.
**(B)** The reference port's socketing selftest does exactly that, with a named case asserting that no legal input can produce an out-of-range record. Convention: `${CLAUDE_PLUGIN_ROOT}/shared/selftest-convention.md`.

**Why this deserves a test and not a comment:** writing a cache field *looks like it works* and is erased at the next load, while writing a carried field persists through ordinary saves with no new format and no migration. The two are indistinguishable inside a single play session.

---

## 3. This can delete an entire planned network feature

> **REFUTED.** *Previously documented:* "this is a server operation; the client only has the picker UI; implementing it requires the protocol, so it stays unimplemented." Two separate sessions recorded that, and it blocked the feature for dozens of sessions.
> *Refuted by:* mapping the record from the six sites in §1 — every field the feature needed was already in the record the game saves. The feature then shipped with no protocol work at all.
> *Why the wrong reading was attractive:* it is what the game's **architecture** says, and the architecture was accurately observed. The picker really did talk to a server; the original service really did perform the operation. Reading the architecture is not reading the data.
> *Consequence if carried forward:* you implement a protocol, a server and a round trip for something that is three byte writes — or, more likely, you never ship the feature.

**(A)** Generalise it into the feasibility question you ask before any host feature that enriches a game object: **enumerate the representations of the object and count the instances of the field you need.** Typical representations: the in-memory instance, the wire record, a snapshot array (§7), and the save payload. It is answerable statically in an afternoon — before any hooks exist to be debugged.

---

## 4. Identity: a (container, slot) pair is not an identity

**(A) Games compact their containers in place, so a cell coordinate names a location and not an object.** The robust identity is the record's own **instance key**: the field the game's own protocol or save keys on.

Procedure:

1. **Find the key.** Two independent tells: what the wire serialiser copies into the head of the record, and what the save serialiser writes **unconditionally** for every cell (§2). If those two agree, you have the key and its second derivation at the same time.
2. **Capture it on both ends** of the mutation — source and target — at selection time.
3. **At commit, re-read both cells and re-match both keys.** On any mismatch, write nothing.

**(B)** In the reference port the key is a `u64` at a fixed record offset; the deposit path copies it into `wire[0..8]` and the save's unconditional prefix carries the same eight bytes. `HIGH / CONFIRMED`.

**★ A zero key is "no identity" and must match nothing, including another zero.** An unoccupied or half-initialised record reads as zeros, and an empty cell is the most likely thing your re-read finds after a compaction — so the fail-open case is also the likely case. Put it in the comparison itself (`key != 0 && key == other`) and pin it with a test named after the claim.

**★ Re-reading "is the resource still there" is not a fix.** It passes. After a compaction another record of the same kind is frequently sitting in that cell, which is precisely the case that produces "the player picked one consumable and a different one was spent".

**★ On a screen showing a filtered subset, the cursor does not index the container.** A builder walks the container, skips whatever the filter rejects, and appends entries into its own side table alongside a live count; the cursor indexes **that**.
**(B)** In the reference port the side table held `{s16 tab, s16 slot}` pairs with unused entries as `-1`, three consumers did byte-identical arithmetic on it, and only one of them re-validated against the count — so stale entries past the count stayed reachable and the host guard was the only one on that path. `HIGH / CONFIRMED`. Reusing the container coordinate convention that works on every other screen produces a plausible number for the wrong object, which is the worst failure a host annotation can have.

---

## 5. Compaction — the address survives, the object does not

> **Correction to [calling-guest-code.md](calling-guest-code.md) §10 and [patching.md](patching.md) §5.** Those sections say a guest pointer may be held across ticks because the allocator is malloc/free with no compaction. True of the **heap**, and misleading as a general licence: the **game** compacts its own containers, swapping whole record contents between cells, so the address stays valid while the object at it is replaced. *"Can I hold this address across ticks"* and *"can I hold this identity across ticks"* are different questions with different answers.

**(A)** Find the compactor early, before you design any deferred write. It is the routine that bubbles empty cells to the end by swapping record-stride blocks. Count its call sites, and specifically **check whether the item popup's own close path reaches it** — that is the keystroke between the player choosing a source and the player choosing a target.

**(B)** In the reference port the compactor's wrapper had **seventeen** call sites, including the item popup's close path. That is why §4 re-matches an identity instead of re-reading a cell. `HIGH / CONFIRMED`.
If your image routes cross-module calls indirectly, a `bl`-based caller query will report none of those sites — see [static-toolkit.md](static-toolkit.md) §2 and §6, and resolve each call by computing its literal-pool address `(pc + 4) & ~3 + imm*4` rather than by inferring the callee from context.

---

## 6. Derived caches: one producer, and never a host reimplementation

**(A)** Some record fields are **derived caches**: computed from other fields of the same record by exactly one routine. Recognition signature, in order of usefulness:

* very few sites write the field, and **none of them runs spontaneously** — each is a grant, a generation, or the recompute itself;
* one of those sites is called by the **save loader, over every record, on every load**;
* host writes of the raw bytes alone produce **no visible effect** until that routine runs.

**Rule: trigger the game's own rebuild; do not reimplement the arithmetic host-side.** Recomputing host-side means re-deriving the per-category item database and whatever power table the formula consults and hoping they never diverge — while the guest re-runs *its* version on every load, so a divergence becomes permanent at the next load.
The game's own routine cannot diverge from itself, and because it is a pure function of the record's own bytes it is **idempotent**: running it over every record is exactly what the loader already does.

**(B)** In the reference port only three sites in the whole image ever wrote the two derived halfwords (wire grant, the recompute, drop generation), and the loader's only call of the recompute is over every record. `HIGH / CONFIRMED`.

---

## 7. There is more than one copy — finding every copy of a field

> **REFUTED.** *Previously documented:* "the on-screen number is stale because the derived cache is stale; rebuilding that cache over the whole container fixes it." Shipped.
> *Refuted by:* enumerating the recompute routine's **stores** — they are two halfwords, and the numbers the player was complaining about are two *other* bytes. The stale values came from two whole-record **snapshot copies** baked by the equip routine's tail: a 20-byte block copy into a candidate-list array and a full `0x3c`-byte `memcpy` into an equipped-slot array, both placing the fields at the same relative offsets under a different base.
> *Why the wrong reading was attractive:* the derived-cache diagnosis was **correct about a real cache** and the fix was correctly implemented — it was simply void for that symptom. Worse, the character's own bonuses were computed from the snapshot array, so the *copy*, not the record, was the authority.
> *Consequence if carried forward:* a whole release cycle spent rebuilding the wrong cache while the report stays open.

**(A) Finding every copy of a field takes three enumerations, not one.**

1. **Writers of the offset.** Cover both Thumb idioms for materialising a constant ([static-toolkit.md](static-toolkit.md) §5).
2. **Readers of the offset.** For an offset above 255 the compiler builds it as `imm8 << shift`, so scanning for the imm8 with the shift folded in enumerates every site that touches the field in one command — and **the hit count is a completeness check on your understanding**.
   **(B)** In the reference port that scan returned all 22 sites touching the two derived halfwords, which is how the live readers on the equipment screen were found. `HIGH / CONFIRMED`.
3. **Whole-record block copies.** `memcpy`-shaped calls and `ldm`/`stm` runs name **no offset at all** and are invisible to both scans above. The searchable signature of a snapshot array is **the same relative field offsets under a different base**. **(B)** A store-site enumeration built on `strh` in the reference port missed a site that wrote the same two fields through a block copy; a reviewer caught it. `HIGH / CONFIRMED`.

**(A) Before trusting a rebuild call to fix a display, enumerate that routine's stores.** If the field the screen shows is not among them, the call cannot possibly fix the symptom however correct the call is. That five-minute check discriminates "my fix is racing" from "my fix is addressing a different field", and those two have completely different repairs.

**(A) The player's description of *when* the number corrects itself names the routine that rebuilds it.** "The list shows the old value, but it becomes correct the moment I equip the item" is a pointer to the equip routine's tail; read the tail and the missing call is in it. This needs no instrumentation on the device at all.

### Count the representations before designing

> **REFUTED.** *Previously documented:* "there are unread spare bytes in the wire record, so extra per-object data can be smuggled through the round trip." The bytes really were unread.
> *Refuted by:* reading which side builds the record on each leg — the deposit leg had the client **construct a fresh record from its own inventory instance**, which carries only one instance of the field, so the smuggled list came back cleared every time. A back-fill added to compensate manufactured plausible-looking wrong data. The attempt was rolled back after shipping two bugs.
> *Why the wrong reading was attractive:* "there is room in the record" and "the record round-trips" feel like the same statement.
> *Consequence if carried forward:* a feature that silently loses data, then a fake-history generator added to hide it.

**(A)** For each leg of a round trip, establish whether the record is **copied** or **rebuilt**. If any leg rebuilds, a smuggled field cannot survive. And when every representation carries only **one** instance of a field, a feature that needs N of them is **structurally impossible** — no amount of hooking will deliver it.

---

## 8. Live read vs cache read, decided at the draw site

**(A) The drawing code tells you which copy the player is actually looking at.** A **live** read re-materialises the container pointer and the full index arithmetic at the draw site; a **cache** read indexes a *different* base with a small stride. Two numbers on the same screen can therefore have different refresh rules, and deciding "the screen is stale" without asking which base the painter uses sends you to the wrong fix.

**(B)** In the reference port the socket-pip painter recomputed `container + tab*tab_stride + slot*rec_stride + field` at the draw site, while the candidate list on the same screen read `otherBase + row*20 + field`. One number updated after a host mutation and the other did not — the observation that unpicked §7. `HIGH / CONFIRMED`.

---

## 9. Choosing which rebuild to call, and which to refuse

**(A) Prefer the leaf that only rebuilds.** Refuse three shapes, in this order of danger:

| refuse | because |
|---|---|
| a wrapper that **compacts** the container as well as rebuilding | it moves records and invalidates the key match you just made (§4), turning a careful mutation into a write on the wrong record |
| any routine that **zeroes a state block on entry** | called with guessed arguments it destroys live state before it can fail |
| any routine whose **argument count you cannot prove** | an argument you did not supply is whatever happened to be in the register |

**How to prove the argument count (A):** read the *single call site's* setup. If the caller sets up no argument registers before the call, the routine takes none — a stronger statement than reading the callee's prologue, because a callee may legitimately clobber argument registers it never uses.

**(B)** In the reference port the snapshot rebuilder was adopted after proving from its one call site that it takes **no arguments**; its wrapper was refused because it compacts first; and the character-stat rebuilder was refused permanently because it takes **six** arguments (two on the stack, unexplained, two of them a "preview this item instead" pair) and zeroes the derived stat block on entry.
`HIGH / CONFIRMED`. The callability check for any target — a jump-table arm is not a function — is [calling-guest-code.md](calling-guest-code.md) §4.

---

## 10. When the second cache is un-callable, restrict the feature

**(A) If a host mutation leaves a second cache stale and that cache's rebuild is un-callable, do not paper over it with a caveat message. Restrict the feature to the states in which the player's own next action performs the rebuild.** That converts a permanent correctness hole into a precondition the player can satisfy, and removes the need for a message admitting the feature is unreliable.

**(B)** The reference port's socketing feature refuses **worn** items, so the sequence is unequip → mutate → equip and the player's own equip action *is* the character-stat recompute. That equipping runs the recompute was **proven, not assumed**: the equip routine's literal and the recompute's literal sit eight bytes apart in one literal pool, so the item-action dispatcher loads both.
`HIGH / CONFIRMED`. The first shipped version instead displayed a "stats will catch up later" toast, and the honest reading of that toast was that the feature sometimes appeared to do nothing.

---

## 11. Asynchronous calls: sweep the container, do not chase the cell

**(A) Host-originated guest calls execute on a later tick** ([calling-guest-code.md](calling-guest-code.md) §5), so a `(container, slot)` captured before the call can be moved by a compaction in between.
The robust answer is not to track what moved where: **run the idempotent rebuild over the whole container.** It is a pure function of each record's own bytes, it is exactly what the save loader does on every load, and it is simpler *and* stronger than chasing a cell.

**(B)** The reference port first shipped a single targeted post-mutation call, documented as "a one-tick window". The next session found that the popup close — the very keystroke that commits the mutation — is inside that window, because closing the popup runs the compactor. The replacement sweeps every equipment record after a delay. `HIGH / CONFIRMED`.

**★ Latch the player's selection when you intercept the confirm key, *before* handing it to the guest.** The event queue means the guest processes that key on a later tick, by which point the popup has been closed and freed.
Reading the popup after the key has gone to the guest is a race you lose most of the time, and the resulting bug presents as "the feature works intermittently". **(B)** The reference port uses the same latch for its socketing commit and for a quantity commit. `HIGH / CONFIRMED`.

---

## 12. Re-trigger on the screen edge; an empty sweep is a failed read

**(A) Give an idempotent refresh two triggers: a post-mutation delay *and* the edge of entering the screen that reads the value.** A timer has an expiry — if anything goes wrong inside its window the screen shows old numbers for the rest of the session.
A screen-entry edge has no expiry, costs one pass per visit, and fires exactly where the evidence of failure would appear anyway. Read the game's own overlay/screen dispatch index to know which screen is up.

**(A) A sweep that finds zero objects is evidence that the *read* failed, not that the player owns none — retry rather than consuming the retry budget.** An empty result is the most common consequence of a resolver refusal (§13), so treating it as a valid answer converts a transient read failure into a permanent feature failure.
Note the vicious pairing: the one action that needs the sweep is often the action that produces the transition the resolver refuses during.

**(B)** In the reference port the timer-only version gave the refresh a 750 ms window; both of its passes were then burned on an empty container because the resolver was refusing mid-transition. The shipped version keeps the pass and retries eight times at 250 ms, and adds the screen-entry edge. `HIGH / CONFIRMED`.

---

## 13. Do not put plausibility gates in a resolver

> **REFUTED.** *Previously documented:* the routine that answers "is there a live character here?" required `current <= maximum` on a gauge, as a sanity check that the pointer really is a character record.
> *Refuted by:* two separate player reports, then the code — equipment carries maximum HP, so **unequipping a piece of gear drops the maximum below the current value** and the resolver reported "no object". The HUD and the quickbar vanished, and the refresh sweep of §12 saw an empty container.
> *Why the wrong reading was attractive:* the relation looks like a structural invariant, and using it as a discriminator is cheap and appears to cost nothing.
> *Consequence if carried forward:* every host feature gated on resolving live state disappears at once, invisibly, and the symptom is attributed to the features rather than to the resolver.

**(A) Rules:**

* A resolver answers **existence**, so it may only test what a not-yet-created record cannot satisfy — an in-range level, in-range maxima, an in-range secondary gauge. **(B)** The reference port kept exactly those three clauses, which is all the gate was ever for. `HIGH / CONFIRMED`.
* **A gauge that reads high is a gauge to clamp when *drawing*, not evidence that the object does not exist.**
* A resolver refusal is **invisible and total**. Anything that can refuse should log the reason once per transition, or you will debug the dependents instead of the gate.

---

## 14. Gate polarity: transcribe the comparison, including its missing half

**(A) The game's own validators are frequently ONE-SIDED — an upper bound with no lower bound. Transcribe the comparison exactly, including the half that is absent.** Adding the "obviously implied" other half silently removes a whole category from your feature, and **the zero element of an enum is the one a tidy range most often excludes** — usually a real, common item.

> **REFUTED.** *Previously documented:* "the equipment category range is `1..=0x0c`", cited with three guest addresses as evidence.
> *Refuted by:* re-disassembling all three. The target validator sign-extends the category and does `cmp #0xc / bgt` with **no test for zero**; the save serialiser does the same; and the recompute indexes its per-category table **directly, with no `cat - 1`**. The zeroth category is the first equipment file in the item database, and it was excluded from an in-game feature for two releases.
> *Why the wrong reading was attractive:* a category enum "obviously" starts at 1, the wrong claim had a citation attached, and by the time anyone checked it had propagated into a constant, a comment, a test's negative list and a shipped release.
> *Consequence if carried forward:* the report arrives as a **question about game design** — "can this item type not be upgraded?" — and gets answered "correct, by design".

**(A) A citation is not evidence.** When a comment or note cites addresses as proof of a claim, re-disassemble the cited instructions before building on it. This is distinct from the "re-read the bytes at every patch site" rule in [patching.md](patching.md) §1: there, something is being patched; here, something is merely being believed, which is why nobody checks.

**(A) One rule, three implementations, one set.** The host UI filter, the offline/replacement engine and the tests must denote the same set — check all three whenever you touch one. Divergence shows up only in the newest implementation, and its signature is *"it works one way and not the other on the same data"*, which sends the porter to look at the transport instead of at the predicate.
**(B)** In the reference port the offline engine and the replacement server had always tested the single upper bound and were correct; only the newest in-game path carried the narrowed range. `HIGH / CONFIRMED`.

**(A) A filter that runs *before* the list is built is a silent refusal.** Put every rejection inside the row builder, so it produces a visible, self-explaining **disabled row**. Otherwise "my item is missing" is indistinguishable from "the feature is broken", and neither is distinguishable from a game rule.
**(B)** The reference port's module documented "list ineligible items with the reason" as its design principle — and the category rejection happened upstream of the row builder, so the excluded category produced no greyed row at all. A design principle is worth only as much as its pre-filters obey it.

**(A) Derive a test's rejection list from the game's gate, not from your implementation.** An `include!`-the-real-source selftest cannot drift from the code — and can therefore **freeze the code's wrong premise as a specification**. **(B)** A green 23-test suite in the reference port carried the zeroth category in its "not equipment" list and actively proved the bug for two releases; a second suite asserted a reported failure sequence as correct behaviour under a comment that misnamed it.
Both were `include!`-based, i.e. immune to the drift failure the convention already warns about. `HIGH / CONFIRMED`. Remedy: **every assertion that transcribes a game rule cites the disassembly site it transcribes**, and negative lists come from the gate. See `${CLAUDE_PLUGIN_ROOT}/shared/selftest-convention.md`.

---

## 15. Write-plan discipline

**(A) Emit a write plan, never side effects.** A pure function takes the two cells and returns a list of `(address, bytes)`; a refusal returns an **empty** plan. Then a test can assert that an ineligible pair produces no partial write at all, and §2's corpus test can assert that every emitted address is durable.

**(A) Spend the consumable LAST, and abort on the first failed write.** Host writes into a live save have no transaction; ordering is the only atomicity available, and choosing the direction of the residual error is free. Ordered this way a partially applied mutation can only ever leave the player with **more** than they should have, never with a spent resource and nothing to show for it.

**(A) A container cell holds a stack, not an item.** To use one unit, decrement the count and leave the cell; free the cell only on the last unit, by clearing the **occupancy byte that every guest reader gates on**. Never compact the container yourself — that moves other items under the player's cursor.
**(B)** In the reference port the painter, the free-slot/merge scan and the save serialiser all test the occupancy byte first, which is why the stale category/index left behind in a cleared cell is unreachable; the earlier network path got this wrong and a player reported depositing ten units, using one, and losing all ten. `HIGH / CONFIRMED`.

**(A) Refuse a self-contradictory record rather than repairing it, and bound the values the game does not bound.** The reason is sharper than "a bad byte causes a glitch": when the **load path re-derives** from a record field through an unchecked table index, an out-of-range value you wrote is reproduced on **every launch** — a permanent, deterministic corruption rather than a one-off.
**(B)** In the reference port nothing cross-checks filled-count against total-sockets, and the bonus formula multiplies the stored id by a row stride into a table with no range check; the surviving case survives only because a neighbouring counter happens to be zero. The host therefore caps the id itself and refuses three specific inconsistent shapes, with an exhaustive test proving the refusal set matches the consistency predicate exactly. `HIGH / CONFIRMED`.

**(A) A mutation made from a menu is not saved where it happens.** The game's own save gate typically requires the field/world state, so arm the same deferred-save retry a consumable item already uses and let the worker fire it on the first poll that passes that gate.
Otherwise the symptom — "it worked, then it was gone" — reads as a failed write rather than as an unsaved one. Gating on the game's own precondition: [calling-guest-code.md](calling-guest-code.md) §8.

---

## 16. Borrow the verb, own the object

**(A) Take one row of the game's own popup as your entry point and draw the rest yourself.** A row added past the range a popup tag's dispatcher handles runs **zero guest code**, because the dispatcher falls through to an unconditional close-and-free. Three things to read before relying on that:

1. the popup constructor — how many label buffers are allocated, and is it unconditional?
2. the tag's row dispatcher — which row indices have arms, and what does fall-through do?
3. the cursor clamp — does it follow the row-count byte, so that raising the count makes the row reachable?

**(B)** In the reference port the plain-item popup allocates **eight** fixed-size label buffers on every open regardless of the live row count, dispatches rows 0 and 1 for that tag, and falls through to close-and-free for anything else, with the cursor clamp following the count byte. Writing a label into an unused buffer and raising the count therefore yields a pure host verb with no leak. `HIGH / CONFIRMED`.

**The alternative was measured and rejected.** Re-pointing the game's own picker at a different container needed row forgery, a label-table patch, a confirm-arm patch and a key inversion — and still would not have updated the container.
**This inverts the usual instinct: do not try to make the game's UI address your data; take one row of the game's UI and own everything behind it.** Drawing the host's side so that it matches the game's chrome is [game-native-ui.md](../../android-host/reference/game-native-ui.md); making the game's own widgets respond to touch is [touch-driven-guest-ui.md](../../android-host/reference/touch-driven-guest-ui.md).

---

## 17. Symptom table

| symptom | cause | fix |
|---|---|---|
| A host write has no visible effect, but the number becomes correct the moment the player equips the item, changes screen, or reloads | The field shown is a derived cache or a snapshot, rebuilt by exactly one routine that those paths run | Call the game's own rebuild (§6). First enumerate its stores — if the shown field is not among them you are rebuilding the wrong cache (§7) |
| Still stale after correctly rebuilding over every record | There is more than one copy; a whole-record snapshot is baked at a state change | Enumerate writers, readers **and** block copies, looking for the same relative offsets under a different base (§7) |
| A mutation occasionally lands on the wrong item | The container was compacted between selection and commit | Re-match the instance key on both ends at commit; a zero key matches nothing (§4, §5) |
| An asynchronous rebuild refreshes the wrong object | `(container, slot)` was captured before a compaction | Sweep the whole container instead of chasing the cell (§11) |
| A whole category is missing from a host list, with no greyed row either | An upper-bound-only game gate reimplemented as a range, **plus** a pre-filter upstream of the row builder | Transcribe the comparison exactly; move the refusal into the row builder (§14) |
| The report arrives as a question about game design | A silent host-side pre-filter | Never drop a candidate silently — list it disabled with its reason (§14) |
| A green selftest suite asserts the bug you are fixing | The negative list was copied from the implementation instead of derived from the gate | Cite the disassembly site in every assertion that transcribes a rule (§14) |
| HUD/quickbar vanish when the player unequips gear | A `current <= maximum` plausibility gate inside the state resolver | Remove the relation gate; clamp gauges when drawing (§13) |
| A refresh sweep runs, reports zero objects, fixes nothing | The resolver refused mid-transition and the sweep spent its budget on an empty read | Treat an empty sweep as a failed read; retry, and re-trigger on screen entry (§12) |
| Using one unit of a stack consumes the whole stack | The code operated on the cell rather than on one unit | Decrement; clear the occupancy byte only on the last unit (§15) |
| A value the host wrote causes garbage on **every** launch | The load path re-derives from it through an unchecked table index | Bound host-written ids yourself; refuse self-contradictory records (§15) |
| A host feature's change works, then is gone after the app is killed | The mutation was made from a menu and the game's save gate requires the world state | Arm the deferred-save retry (§15) |
| A field smuggled through spare protocol bytes comes back cleared | One leg **rebuilds** the record from a local instance rather than copying it | Establish copy-vs-rebuild for each leg; never add a back-fill (§7) |
| Two adjacent same-typed byte fields behave as if swapped | Code that treats them symmetrically cannot distinguish them | Settle it with a live record whose two values differ (§1) |
| A write lands in the neighbouring record | A field constant that already contained the array's base offset was added to a computed record address | Re-base field constants to the record; one function converts `(tab, slot)` to an address (§1) |

---

## 18. Checklist

Before shipping any host mutation of a live guest record:

- [ ] The record was mapped from **six independent sites**, and every field you write has two independent derivations.
- [ ] Field constants are **re-based to the record**, and exactly one function converts `(tab, slot)` into an address.
- [ ] Every byte you write is inside the **save serialiser's durable set**, asserted by a corpus test.
- [ ] The mutation names objects by **instance key**, re-matched on both ends at commit; a zero key matches nothing.
- [ ] You know where the **compactor** is, and whether the commit keystroke reaches it.
- [ ] Every **derived cache** and every **snapshot copy** of the fields you touched has been enumerated by all three scans, and the rebuild you call actually stores the field the screen shows.
- [ ] The rebuild you call is a **leaf**: it does not compact, does not zero a block on entry, and its argument count is proven from its call site.
- [ ] The refresh has **two triggers** (post-mutation delay and screen-entry edge), and an empty sweep retries rather than counting as an answer.
- [ ] No **plausibility relation** was added to a state resolver; gauges are clamped when drawn.
- [ ] Every game gate you reimplemented was **re-disassembled**, transcribed with its missing half, and the host, the offline engine and the tests denote the same set.
- [ ] Rejections are visible **disabled rows**, not silent pre-filters.
- [ ] The write plan is pure, empty on refusal, spends the consumable last, and the change is **saved** through the game's own gate.
