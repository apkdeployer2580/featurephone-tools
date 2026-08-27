# Refusing, holding, and retiring: what to say when you cannot just say yes

Most of a replacement server's handler code says yes. This file is the other three answers — **no**,
**not yet, how many?**, and **never again**. All three are dangerous in the same way: the client's
online UI is released by a reply frame and by nothing else, several of its operations empty the
player's slot on the success arm, and one key on one screen can be two features. A refusal written
the obvious way hangs the game, destroys an item, or kills the half the player still wanted.

**(A)** generic procedure · **(B)** worked example from the reference port, always introduced by the
method that found it · **(C)** do not copy. `⚠` marks anything INFERRED. Convention:
`${CLAUDE_PLUGIN_ROOT}/shared/evidence-standard.md`.

## Contents

- [1. Three shapes of no, and only one of them works](#1-three-shapes-of-no-and-only-one-of-them-works)
- [2. Prove the wait has a deadline before you rely on silence](#2-prove-the-wait-has-a-deadline-before-you-rely-on-silence)
- [3. The refusal carries its reason in the same frame](#3-the-refusal-carries-its-reason-in-the-same-frame)
- [4. Refusing after the client has already committed](#4-refusing-after-the-client-has-already-committed)
- [5. Holding a reply to ask the player a question](#5-holding-a-reply-to-ask-the-player-a-question)
- [6. Every hold is answered](#6-every-hold-is-answered)
- [7. The prompt's bounds: ceiling, floor, and when to open it](#7-the-prompts-bounds-ceiling-floor-and-when-to-open-it)
- [8. Retiring a feature you cannot honour](#8-retiring-a-feature-you-cannot-honour)
- [9. One request, two senders](#9-one-request-two-senders)
- [10. A reply's semantics are the whole handler](#10-a-replys-semantics-are-the-whole-handler)
- [11. Refusing on the way out: rewriting the outgoing frame](#11-refusing-on-the-way-out-rewriting-the-outgoing-frame)
- [12. The lifecycle facts that shape all of this](#12-the-lifecycle-facts-that-shape-all-of-this)
- [Failure table](#failure-table)
- [Checklist](#checklist)

---

## 1. Three shapes of no, and only one of them works

**(A)** A host has exactly three ways to decline a request. Two of them are traps.

| shape | what the client does | verdict |
|---|---|---|
| drop the frame | sits on its reply-gated modal forever | **never**, unless §2's proof holds |
| reply success, discard the payload | shows *its own* success message and clears the source slot | **destroys property** |
| reply failure **with reason text** | opens its own error widget, leaves the slot alone | the only real refusal |

The middle row is the one porters write by accident, because "accept and ignore" feels like the
conservative choice. It is the opposite: the client's success arm is what empties the slot, so a
silently dropped payload is an item deletion with a congratulatory message on top.

**(A) Where the client has no gate of its own, the host is the only party that can refuse.** Read
the *send*-side arm of every operation and list the conditions it checks before transmitting. If
capacity, ownership or economy limits are not in that list, the client will send anything and
believe whatever you answer.

**(B)** How that was established in the reference port: full disassembly of the deposit send arm.
It checks a worn flag, tradability, an item grade and three category exclusions — and has **no
capacity check at all**; the only code that reads the storage counters is the *reply* handlers. The
same title's reverse operation does have a client-side gate, but it runs only when the inventory has
zero free cells, so the two gates complement each other exactly and neither covers the other's case.
That asymmetry — a check that only fires in the extreme case — is a general shape; see
[client-fidelity.md](client-fidelity.md).

**(A) Decide the refusal before anything moves.** A refusal you discover after you have already
mutated the store is §4's problem, and §4 is expensive.

## 2. Prove the wait has a deadline before you rely on silence

> **REFUTED.** *Previously documented, in this chapter:* [reply-re.md](reply-re.md) encoder rule 5 —
> "When you refuse a request, prefer silence over a malformed reply. The guest treats *no reply* as
> an incomplete request and eventually times out cleanly … Silence is the safe failure mode", and
> [server-architecture.md](server-architecture.md) handler rule 2, "Refusal is silence. Return `[]`."
> **Refuted by:** device sessions plus the client's failure-arm disassembly. Every screen in the
> online module is released by a reply frame and by nothing else, and there is no timeout behind any
> of them. Silence is an unrecoverable hang, not a clean timeout.
> **Why it was attractive:** it is true that every crash in the early refutation log came from a
> *malformed* reply, so silence really did eliminate a whole class of failure — and on an ordinary
> request/response protocol a client timeout is the normal recovery path. Both halves of that
> reasoning are sound; the missing step is checking that this particular client has the timer.
> **Consequence if carried forward:** the player's only escape from the wait is to kill the app, and
> the bug reproduces on no LAN test because the refusal condition is rare.

**(A)** Three routes, cheapest first:

1. **The silent baseline you already ran.** Accept the connection, answer nothing, watch for several
   minutes ([black-box-loop.md](black-box-loop.md)). A wait that never clears has no deadline. This
   is the CONFIRMED route and it costs nothing you were not doing anyway.
2. **Read the wait's own exit.** Find the screen's key/paint dispatch and look for a timer armed on
   entry. A modal whose only exit is a reply handler has no deadline by construction.
3. **Do not mistake a session timer for a request timer.** A guest of this era usually carries an
   operator-era idle-disconnect constant, and finding it feels like finding the answer. **(B)** The
   reference port found exactly such a constant, patched it out for offline play, and still had no
   deadline behind any individual wait. Only the per-wait timer licenses silence.

Silence stays legitimate for one thing: **messages the client demonstrably ignores** — in the
reference port, the heartbeat. That the server never answers it is itself load-bearing; see §12 and
[socket-layer.md](socket-layer.md) on why a host watchdog keys on an unanswered *request* and never
on received silence.

## 3. The refusal carries its reason in the same frame

**(A)** The refusal frame must carry **both** the failure status and the reason text, in one frame.
A bare status-only failure ack is a **crash primitive**: the client's failure arm opens a message box
that reads its text out of *this frame's* body, so a body that stops after the status byte is a read
past the end.

**(A)** Two corollaries, same root cause — the client's text widgets do not defend themselves:

* **Never send an empty body either.** A paged text builder walking a page list that an empty body
  leaves null crashes at address zero; one of the chapter's oldest device refutations
  ([black-box-loop.md](black-box-loop.md)). Substitute a single space rather than emitting nothing.
* **The condition that produces a refusal is usually rare, so the bug is latent.** **(B)** The
  reference port's replacement server shipped bare status-only refusals for months with no report,
  undetected only because the situation was never reached in production.

**(A)** Write it into the suite as a *constraint*, not as a byte: every refusal this encoder can
emit has a non-empty text field. Pinning the bytes an encoder currently emits is how a suite keeps a
real bug green — [conformance-tests.md](conformance-tests.md).

## 4. Refusing after the client has already committed

**(A)** In a two-leg protocol the commit frame can arrive *after* the client has cleared its source
slot and saved. At that point a refusal is not enough and a state refresh is item destruction: **you
must hand the item back**, using whichever reply frame the protocol already has for granting.

**(A) How to pick the give-back frame.** Read every grant-capable reply handler to its end and score
it on three questions: does it need prior request state (is it *stateless*, so you can send it
unsolicited)? does it open a modal the player must dismiss? does it save afterwards? Choose the one
that grants and saves **silently**.

**(B)** The reference port's answer was the withdraw-reply frame — device-measured as a stateless
handler that grants and saves without opening a message box — sent unsolicited as a give-back.

**(A) ⛔ Drop a pending give-back the moment a *different* record's commit arrives.** Otherwise it is
paid out against the wrong commit and duplicates units. This was discovered in the same session that
built the give-back, which is the usual pattern: an unsolicited frame needs an ownership rule the
protocol never had to express.

**(A)** The amount handed back travels through the client's own placement procedure, so it is subject
to the same silent loss as any delivery. That is why §7 has a *floor* and not only a ceiling, and why
the quantity comes from running a model of the client's algorithm
([client-fidelity.md](client-fidelity.md)) rather than from arithmetic.

## 5. Holding a reply to ask the player a question

**(A)** When the wire carries no field for something you must decide — most often a quantity — you do
not have to guess. **Hold the reply, ask the player, then answer.** The client is already blocked on
a reply-gated wait with no deadline (§2), so from its side a late answer is indistinguishable from a
slow disk.

**(A) Establish first that the number genuinely cannot come from the wire.** Read the send-side arm
of *both* directions and list the fields each frame actually carries; do not infer it from the
feature's UI.

**(B)** In the reference port one direction sends the whole stack and clears the cell outright — a
single-unit primitive exists elsewhere in the game, but that path does not use it — and the other
sends only a key. So the count can come from nowhere but the host, and the only moment it can be
asked for is during the block.

**(A)** Four preconditions, all of which must hold first: §2's proof that the wait has no deadline;
a route for the answer to reach the engine as **data** (it arrives in the explicit environment struct
alongside the other facts the engine cannot compute — the Env pattern in
[in-process-engine.md](in-process-engine.md)); a resume tick that fires while the client is blocked
(§6, rule 3); and a definition of what the answer is if it never comes (§6, rule 1).

## 6. Every hold is answered

> **The absolute rule.** A hold that is never resolved is a permanently wedged game, which is
> strictly worse than the imprecision the question was added to fix.

**(A)** Five mechanics, all learned by hitting them:

1. **Put a deadline on the hold, and make its expiry reply with the pre-feature default** — whatever
   the feature did before the prompt existed (in the reference port, the whole stack). Expiry is an
   answer, not an abandonment. **(C)** The exact duration is a UI-pacing choice: long enough that a
   player reading the prompt is not cut off, short enough that a forgotten dialog recovers.
2. **Cancel is an answer too.** Resolve the hold with a §3 refusal rather than dropping it.
   Cancel is *cheap* only in a direction where the client has not moved yet — the client mutates in
   its own reply handler, on the success arm only. In a direction where the client cleared its slot
   before sending (§4), cancel is not free: it is a give-back.
3. **Drive the resume from the guest's receive/readability poll, not from inbound frames.** A
   blocked client sends nothing but a heartbeat every few seconds, so a request-driven pump never
   wakes and the answer sits in memory while the game hangs.
4. **Clear any stale answer when a new question is asked.** Otherwise a number chosen for a
   different item resolves the next question instantly, and the player never sees the prompt.
5. **Exactly one question outstanding.** Rules 3 and 4 both assume it.

**(A)** Each of these is a one-line assertion in the suite, and they are the assertions most worth
carrying across a reimplementation — a constraint that lives only in a comment is lost the moment
the protocol is rewritten in another language ([conformance-tests.md](conformance-tests.md)).

## 7. The prompt's bounds: ceiling, floor, and when to open it

**(A)** A prompt offering numbers that cannot work is worse than no prompt, because the player
believes the transfer they chose happened.

* **Ceiling = what actually fits at the destination**, not what the player is carrying. **(B)** The
  reference port's original ceiling was the carried amount, on an all-or-nothing operation: the
  prompt let the player choose a number the commit path then refused.
* **Floor = the smallest transfer whose remainder the destination can still take.** A floor is
  required whenever the un-chosen remainder travels back through the same lossy client path (§4).
  **(B)** Measured: keeping 49 of a stack of 50 destroyed 47 of them, because the remainder went
  back through a grant routine that makes exactly two placements and discards the second return.
* **Open the prompt whenever the player *holds* more than one**, not only when more than one fits.
  Otherwise a forced partial transfer happens silently.

**★ (A) Compute the bounds against the state the client will be in when it acts, not against your
snapshot.** A client that mutates and saves *before* it builds the commit frame leaves your snapshot
one cell stale, in a load-bearing way. **(B)** Modelling the raw snapshot answered "a remainder of 49
fits" where the truth was **2**. The reference port models the post-clear state explicitly and its
own comment marks the obvious version as wrong.

**(A) A failed read of the outside world means "no limit known", never "at the limit".** An unknown
inventory snapshot bounds nothing, sets no floor, and may never refuse something already paid for.
Where the snapshot is present but possibly stale, model the more conservative of the two candidate
states: erring toward "transfer more" costs a choice, erring the other way costs an item.
Fail-closed is right for *writes* into guest memory and wrong for *limits* derived from an optional
observation — a distinction easy to get backwards by applying one project-wide rule.

Deriving the numbers themselves is [client-fidelity.md](client-fidelity.md)'s subject: an executable
model of the client's own placement procedure, run over a whole *sequence* of operations.

## 8. Retiring a feature you cannot honour

**(A) Define the retirement by the request the client sends, not by the player's apparent intent.**
Procedure:

1. Enumerate what each half of the control sends, from the send-side arms.
2. If the half you must retire **sends a frame** and the half you keep is **purely local**, the
   refusal lives where the frame arrives — and the surviving half is never touched by your code at
   all. That is the whole trick.
3. **⛔ Never swallow the key.** A key is both halves; swallowing kills the one the player wanted.
4. Refuse **unconditionally**, with a §3 failure-plus-text frame, even in the cases where you could
   technically honour it. Pin that with a test.

**(B)** In the reference port, one key on one screen is two features chosen by slot occupancy: a
destructive local reset on an occupied slot, a server-side character restore on an empty one. The
restore was retired — it overwrites live characters and is byte-identical to a login sub-request
(§9, §11). The engine answers that request with a permanent "this feature is unavailable" and never
sends a character, **even when a backup exists**; a selftest asserts that exception. Local deletion,
which the player still needs, never reaches the engine and kept working with no code at all. The
replacement capability is a host-side pre-image vault of the save records —
`${CLAUDE_PLUGIN_ROOT}/skills/save-integrity/reference/save-vault.md`.

> **REFUTED.** *Previously believed:* the host can tell the two halves apart by reading guest memory
> — the screen visibly knows which slots are occupied, so an occupancy array must exist and can be
> read at the moment the key is pressed.
> **Refuted by:** an occupancy array *was* found, derived from the online module's singleton, and it
> reported **all slots occupied for a screen showing a single character**; the neighbouring candidate
> window was all zeroes. A session was spent on it.
> **Why it was attractive:** the array decoded plausibly and was self-consistent on the screen it was
> derived from. The failure is that the derivation came from a *different* screen family with a
> different population, so the reading was structurally wrong rather than off by an offset — which is
> invisible if you only test where you derived it.
> **Consequence if carried forward:** the retired half fires anyway, or the surviving local half is
> refused. The reference port's notes now say, in as many words, do not look for that anchor again.

**(A)** Generalise the discriminator: *"which requests does each half send?"* is cheaper, more
stable and more testable than *"what does memory say the screen is showing?"*. Reach for guest state
only when neither half touches the wire.

## 9. One request, two senders

**(A)** The same request bytes can have two senders, and a reply that is correct for one can be fatal
for the other. **Discriminate by session state — has the login finalised? — never by opcode alone.**

**(B)** In the reference port a login sub-request and a user-initiated restore are byte-identical
seven-byte frames, confirmed in production logs as the same bytes arriving on the same connection.
Compounding it: the refusal frame's own message-box category calls the **network teardown routine
before it shows its box** (§10), so sending §8's refusal as part of the login killed the socket on the
way in. The client then cleared its wait on the following notice, walked into the online menu with a
dead socket, and every later request waited forever. The engine's comment ends with an explicit
⛔ do not merge these two arms back together.

**(A)** This is the failure that looks least like its cause: everything succeeds, the screen opens,
and only then does nothing ever answer again. When a hang appears one screen *after* the frame you
changed, suspect a reply whose handler had a side effect (§10) sent to the wrong sender.

## 10. A reply's semantics are the whole handler

**(A) Read the client's handler to its end before you choose a reply class.** A reply's meaning is
everything its arm does, not the text it displays. Three questions to answer for each candidate:

1. Does it **tear down the connection**?
2. Does it **open a modal that overwrites another** — including one you are relying on as an escape?
3. Does it call the game's **own grant/apply routine**, i.e. does replying success mean *you deliver
   it yourself*? (That question belongs to [client-fidelity.md](client-fidelity.md); acking *and*
   delivering means delivering twice.)

**(B)** Two instances in one title, both found by reading past the string load to the end of the arm:
the text-box category of §9, whose handler tears the socket down first; and an earlier synthetic
"goodbye" frame removed as **actively harmful**, because its handler opened a different modal that
overwrote the screen's only escape hatch (§12). Choosing replies by "which one shows my message"
produces a client that displays exactly the right words while losing its connection, or its only way
out of the screen.

## 11. Refusing on the way out: rewriting the outgoing frame

**(A)** Because the host owns `send`, it can refuse a request **before any server sees it** — and
this is the only place a port can enforce an invariant the protocol cannot express. Two design rules,
both learned by getting them wrong:

* **Substitute, never drop.** A dropped request leaves the client's wait unresolved forever (§2).
  Replace it with a different, benign request the client is already prepared to have answered.
* **Match the whole frame, including its self-declared length.** A buffer that is not exactly its
  declared length is something your rewriter does not understand, and guessing is how a rewrite
  corrupts a request. Treat an out-of-range target the same as a protected one, so the login still
  completes.

**(B)** The reference port rewrites the destructive restore request into a benign one whenever its
own record store says the target slot is occupied — so the destructive reply cannot be provoked by
**any** server, including one the porter does not control. Co-locating the engine with the client's
private state is what makes this possible at all, and it is the argument for the in-process engine
being *safer* rather than merely more available ([in-process-engine.md](in-process-engine.md)).

> **REFUTED.** *Previously documented,* [reply-re.md](reply-re.md)'s worked reply map: the
> destructive restore row's mitigation is a **server-side feature flag**.
> **Refuted by:** production logs and a player report — story, equipment and items all reset. The
> client applies that reply straight into its live character structure with **no occupancy check**,
> and the request is byte-identical to a routine login step, so no server can tell a real restore
> from a login sub-request no matter how its flags are set.
> **Why it was attractive:** flag-gating a destructive path is genuinely correct practice, and it
> does protect against the *server operator* enabling something careless. It just does not address a
> reply the client will apply the instant any peer sends it.
> **Consequence if carried forward:** a permanent, unrecoverable character reset for any player
> whose slot **already holds a live character** — the population the guard exists to protect —
> triggered by merely entering an online screen, because the login's own sub-request names that
> slot and the server answers it with whatever stale backup it stored. A slot that is genuinely
> empty is the one case that must pass through: that is the real restore. And the flag cannot
> help, because it has to be *on* for the recovery half the player still wants; the reference
> port shipped it default-off, found that this broke the legitimate restore, turned it on — and
> only then discovered that with it on no server can tell the two senders apart.

## 12. The lifecycle facts that shape all of this

Two client-visible facts sit underneath every rule above. Mechanics live elsewhere; the consequences
are what matter here.

**(A) A modern mobile OS destroys a backgrounded app's sockets**, reported as a connection-abort
error, and no host-side socket rule prevents it. The only real cure is a persistent-notification
foreground service, which many projects will decline. **(B)** The reference port measured detection at
**39 ms** after resume with a two-stage defence: record the transition on the way *down* (not on
resume, or you also suspect sockets opened after it), then probe each live socket on the first
network call after resume and *ask* it rather than assuming — a short background often survives.
Mechanics and the watchdog that backstops it: [socket-layer.md](socket-layer.md).

**The consequence here:** a player can arrive at a screen whose wait will never be answered by
anyone, so a hold's deadline (§6) is sometimes the only thing left running. And raising a *server*
idle budget does not address this — the platform resets the connection within seconds, far inside any
sane budget.

> **REFUTED.** *Previously believed:* a client stuck on its loading modal can be released either by
> closing the socket under it or by feeding it a synthetic reply.
> **Refuted by:** device runs establishing three constraints. The guest ran its **complete** network
> teardown and still displayed the loading modal; which reply opens a message box is **screen
> dependent**, so the same frame works in one online screen and not in another; and closing that box
> does not leave the screen either.
> **Why it was attractive:** the teardown really did run, so it looks as though the client has already
> accepted the disconnect and needs only a nudge — and a synthetic frame *does* open a box on some
> screens, which reads as "it works" until you try the screen that matters.
> **Consequence if carried forward:** the synthetic-frame attempt is not merely useless, it is
> harmful — its handler opens a different modal that overwrites the one escape hatch (§10). Delete it
> rather than keeping it as a fallback.

**(A) Only the client's own modal changes the client's screen.** Find the modal id whose key handler
already performs **both** the teardown and the screen transition, and write exactly the words the
game's own show-modal routine writes. Two implementation rules: write the **state word last** (before
it, the dispatch table still points at the old modal; after it, every field the painter reads is in
place), and **gate it on the screen family that owns those dispatch tables**, or you plant a box
nobody paints whose later OK drags the player out of gameplay. The discovery route for that modal id
is in [reply-re.md](reply-re.md). The generic move: **drive UI the game already has for this
situation rather than constructing UI on the wire.**

## Failure table

| symptom | cause |
|---|---|
| client hangs forever on a waiting box after you declined to answer | silence is not a refusal for a reply-gated UI; there is no timeout behind the wait (§2) |
| client shows its own success message and empties the slot, item never arrives | you replied success and discarded the payload; the client's send arm has no gate of its own (§1) |
| client crashes in its message box on a refusal | status-only failure ack; the box reads its text from that frame's body and runs off the end (§3) |
| refusal works in testing, crashes in the field | the refusal condition is rare — the bug was latent for months in the reference port (§3) |
| a refused transfer eats the item | the commit arrived after the client cleared its slot; a state refresh is destruction, hand it back (§4) |
| a give-back pays out twice, or against the wrong item | a stale pending give-back was not dropped when a different record's commit arrived (§4) |
| the quantity dialog is answered and nothing happens | the resume is driven by inbound frames; a blocked client sends only heartbeats (§6) |
| the dialog resolves instantly with a number the player chose for something else | a stale answer was not cleared when the new question was asked (§6) |
| the game wedges when the player ignores the dialog | no deadline on the hold (§6) |
| the player chooses a quantity the commit path then refuses | the ceiling is what they carry, not what fits (§7) |
| a remainder the player chose to keep comes back much smaller | no floor; the remainder travelled back through the client's lossy placement (§7, §4) |
| the model says a remainder fits and the client disagrees | bounds computed against the pre-send snapshot; the client mutates and saves before sending (§7) |
| a retired feature still fires, or retiring it killed the half the player wanted | the refusal was placed by inferring intent from guest state instead of by the request (§8) |
| everything succeeds, the screen opens, then every later request waits forever | one request, two senders: a reply correct for one tore the socket down for the other (§9, §10) |
| the right words appear and the connection dies | the reply's category handler does more than show a box (§10) |
| a destructive reply still reaches the player despite a server flag | the request is indistinguishable from a login step; refuse in the host's send path instead (§11) |
| frozen on a network screen after resume, no network fault, no LAN repro | the platform destroyed the socket while backgrounded (§12) |
| the screen will not clear however you reply | only the client's own modal changes the screen (§12) |

## Checklist

- [ ] Every refusal path emits a **failure status plus non-empty reason text in one frame**, and a
      test asserts that constraint rather than the current bytes.
- [ ] Silence is used only for messages the client demonstrably ignores, and the proof that the wait
      has no deadline is written down with what was run.
- [ ] No handler replies success and discards a payload. Refusals are decided before anything moves.
- [ ] Every operation whose commit can arrive post-mutation has a give-back frame, chosen by reading
      candidate handlers to their end, plus a rule dropping a stale pending give-back.
- [ ] Every hold has a deadline whose expiry replies with the pre-feature default; cancel resolves it;
      the resume runs off the receive poll; a new question clears the old answer.
- [ ] Prompt bounds have a floor as well as a ceiling, are computed against the post-clear state, and
      come from running the client model — with "unknown snapshot" meaning *no limit*, never *at the
      limit*.
- [ ] Retirements are anchored on the request the client sends; no key is swallowed; the refuted
      guest-state discriminator is recorded so nobody hunts it again.
- [ ] Every reply class in use has been read to the end of its handler for teardown, modal-overwrite
      and self-grant side effects.
- [ ] Dangerous outgoing requests are **substituted**, never dropped, and matched on the whole frame
      including its declared length.
- [ ] The stuck-screen escape is the game's own modal, state word written last, gated on the owning
      screen family — and any synthetic-frame attempt has been deleted.
