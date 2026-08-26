# Retiring the server: the in-process protocol engine

The replacement server in [server-architecture.md](server-architecture.md) is how you *learn* a dead
protocol on real hardware. It is rarely what you should still be running in three years. This file
is the other answer: keep every online feature and delete the network — give the emulator's socket
layer a third connection state in which `connect` constructs an engine, `send` feeds it request
bytes and `recv` drains its replies, so the guest's socket API is untouched and the client cannot
tell. What follows is everything that is *not* the protocol itself: the wiring, the purity boundary,
the mode switch and the cost of its default, the local store, the guest-side patches an offline
session needs to stop lying to the player, and the testing that becomes possible once no socket is
involved. Choosing *what number* to put in a reply is [client-fidelity.md](client-fidelity.md);
saying no, holding a reply, and retiring a feature are
[refusals-and-holds.md](refusals-and-holds.md).

Tags: **(A)** generic procedure · **(B)** worked example from the reference port, always introduced
by the method that found it · **(C)** do not copy. `⚠` marks an inferred claim with nothing run
behind it. See [evidence-standard.md](../../../shared/evidence-standard.md).

## Contents

- [1. Why retiring the wire is the terminal answer](#1-why-retiring-the-wire-is-the-terminal-answer)
- [2. The fork after Phase 5: who will run this forever?](#2-the-fork-after-phase-5-who-will-run-this-forever)
- [3. Wiring: a third connection state](#3-wiring-a-third-connection-state)
- [4. The testability hinge, in the host's language](#4-the-testability-hinge-in-the-hosts-language)
- [5. The Env pattern: platform-only facts arrive as data](#5-the-env-pattern-platform-only-facts-arrive-as-data)
- [6. The mode switch, its default, and the cost of the default](#6-the-mode-switch-its-default-and-the-cost-of-the-default)
- [7. One store per process, session state per socket](#7-one-store-per-process-session-state-per-socket)
- [8. The store file: tolerant text that degrades](#8-the-store-file-tolerant-text-that-degrades)
- [9. Normalisation runs on LOAD](#9-normalisation-runs-on-load)
- [10. Persist only on a real change](#10-persist-only-on-a-real-change)
- [11. In-process is not free](#11-in-process-is-not-free)
- [12. Offline-only guest patches](#12-offline-only-guest-patches)
- [13. The notice you cannot delete, and the host pressing OK](#13-the-notice-you-cannot-delete-and-the-host-pressing-ok)
- [14. Co-location as a safety property](#14-co-location-as-a-safety-property)
- [15. A one-shot command file for a release build](#15-a-one-shot-command-file-for-a-release-build)
- [16. Re-derive the failure modes](#16-re-derive-the-failure-modes)
- [17. Porting the server: carry the comments](#17-porting-the-server-carry-the-comments)
- [18. Generated shared tables with a `--check` gate](#18-generated-shared-tables-with-a---check-gate)
- [19. Symptom table](#19-symptom-table)
- [20. Checklist](#20-checklist)

---

## 1. Why retiring the wire is the terminal answer

**(A)** Three reasons, all of them measured in the reference port rather than argued:

1. **The platform destroys backgrounded sockets.** Once a modern mobile OS caches the process it
   resets the app's connections, reported as a connection-abort error, and no combination of
   socket-layer host rules prevents it. The only real cure is a persistent-notification foreground
   service, which many projects — including the reference port — decline. A socket-based online
   feature therefore has a permanent, unfixable failure mode; an in-process engine has none of it.
   Detection and resume are [socket-layer.md](socket-layer.md)'s.
2. **The online UI is the only route to several game features.** Storage, the shop, item combining
   and paging live behind the network screens even in a game nobody plays with anybody. Losing the
   server loses features that have nothing to do with other players.
3. **Every byte a server can send is a byte that can damage the save.** In the reference port a
   production log showed the server overwriting live characters through a restore reply the client
   applies with no occupancy check (see [refusals-and-holds.md](refusals-and-holds.md) and
   [save-vault.md](../../save-integrity/reference/save-vault.md)). No remote party, no class.

**(A)** Two independent derivations that the client genuinely cannot tell the difference — reproduce
both. **Structural:** the guest's socket slot table is unchanged, so every existing host path (the
poll-rate throttle, the unanswered-request watchdog, the resume probe) sees a file descriptor that
never fails. **Behavioural:** a byte-at-a-time equivalence test proves the engine answers
identically however the stream is chunked (§4) — the one property a real TCP stack was providing.

> **REFUTED.** *Previously documented* (in this plugin's own request-discovery chapter): "operations
> with no offline meaning: item transfer, character restore, combining, shop purchase."
> *Refuted by:* the reference port shipping three of the four entirely in-process with no network at
> all, and retiring the fourth on purpose. *Why it was attractive:* those four features live behind
> online menus and speak to a server, so they read as inherently peer-to-peer. *Actual mechanism:*
> the only operations that need a network are the ones that need a **peer** — another live player or
> an authoritative economy. Storage, a shop and a combining bench are single-player features with a
> socket in front of them. *Consequence if carried forward:* a porter is told that exactly the work
> which turns out to be the answer is impossible.

## 2. The fork after Phase 5: who will run this forever?

**(A)** After reply RE is done you have a working replacement server and a decision to make. Ask the
deployment question, not the technical one:

| the title is | ship | because |
|---|---|---|
| genuinely multiplayer — players see each other's state | a hosted server | the state must live somewhere both clients can reach |
| single-player with online-gated features | an **in-process engine** | nothing on the wire needs a peer; hosting is a liability you carry forever |
| either, *while you are still doing RE* | the server, first | it is the only way to iterate against real hardware, with a reply table and transcripts ([black-box-loop.md](black-box-loop.md)) |

**(A)** In practice you build both, and the order is not waste: **the server is the instrument, the
engine is the product.** Phases 0–5 (socket layer, framing, request discovery, reply RE, the
conformance suite) are spent identically either way. What changes at the fork is only what comes
afterwards: TLS, certificate pinning, an account store, deployment and the whole threat-model
section become optional, and in their place you owe the seven things §3–§18 are about — a third
connection state, an environment struct, a mode switch with a stated cost, a local store, a
poll-rate gate, offline-only guest patches, and a generated shared table. The triage-level version
of this fork is [pipeline.md](../../port-triage/reference/pipeline.md).

## 3. Wiring: a third connection state

**(A)** The engine goes *inside* the socket implementation you already own, as one more variant of
the connection state — not as a layer above it and not as a loopback server:

```
enum Conn { Connecting{..}, Socket(TcpStream, RxBuf), Local(Session) }   // Local: no fd at all

connect(dest)   -> if mode is local { Conn::Local(Session::new(shared_store())) }
send(bytes)     -> session.rx.feed(bytes);
                   for req in session.rx.frames() {
                       for r in ENGINE.handle(req, &env) { session.outbox.push(frame(r)) }
                   }
                   // returns the full length, always: a local write cannot short-write
recv(buf)       -> drain session.outbox
poll_readable() -> outbox non-empty                 // and see §11
close()         -> drop the session; the STORE survives (§7)
```

Four rules **(A)**:

- **Do not touch the guest's slot table.** The redirect and the slot contract in
  [socket-layer.md](socket-layer.md) keep working unchanged; the engine is a destination, not an API.
- **Keep the frame decoder on the local path.** The guest still chunks its writes however it likes,
  so the same incremental decoder from [framing.md](framing.md) sits in front of the engine — which
  is also what makes the byte-at-a-time test meaningful rather than tautological.
- **Never fail.** The socket layer's error handling exists for a network; a local arm that invents
  errors reactivates code paths you no longer have any way to exercise.
- **Reply synchronously inside `send`.** Everything in §11 depends on it.

## 4. The testability hinge, in the host's language

**(A)** The engine's entry point is the server handler's signature, transliterated:

```rust
/// (request bytes, environment) -> reply payloads.
/// No sockets, no threads, no clock, no guest memory. The store is the only I/O.
pub fn handle(&mut self, req: &[u8], env: &Env) -> Vec<Vec<u8>>
```

Holding that signature is what makes the port cheap: a handler in the server's language and one in
the host's are interchangeable, so a black-box finding, a conformance assertion and a shipped engine
arm are all the same shape (the hinge section of
[server-architecture.md](server-architecture.md)). It also makes the engine **the one part of a port
that can be tested exhaustively without a phone** — everything else (pixels, input, audio, timing)
needs hardware, while a pure protocol engine needs a list of byte strings. Two whole-stream tests
hang directly off the signature and are worth writing on day one:

1. **Chunking equivalence.** Feed the same request sequence one byte at a time and assert the reply
   stream is identical to the single-write case.
2. **Open-sequence replay.** Replay the full connect-to-first-screen exchange captured against the
   real server and assert byte equality.

**(B)** In the reference port the engine and its store are one dependency-free module, and a
standalone selftest binary `include!`s that real source — never a copy — and pins **79 assertions**
across login, notice, storage transfer, the shop, purchase commit, currency, pagination and item
combining, with no device and no network. The harness, and the rule against hand-written mirrors,
are in [selftest-convention.md](../../../shared/selftest-convention.md).

## 5. The Env pattern: platform-only facts arrive as data

**(A)** A pure engine will need facts it cannot compute. Do not reach for them — **publish them into
an explicit environment struct, documenting beside each field why it cannot be read inside.** The
list stays short precisely because each entry has to justify itself in writing.

**(B)** The reference port's `Env` carries exactly three:

| fact | why it cannot live inside the engine |
|---|---|
| which save slots hold a character | the engine must not open save records; that is the vault's business |
| a quantity the player chose in a host dialog | it arrives from the UI thread, asynchronously, long after the request |
| a snapshot of the client's inventory | **only the emulator thread may read guest memory** — a worker publishes the snapshot and the engine receives it as data |

**★ (A) A failed read means "no limit known", never "at the limit".** A limit derived from an
optional observation must vanish when the observation is missing: an absent snapshot bounds nothing,
sets no floor, and can never refuse something the player has already paid for. Where the snapshot is
present but *stale*, model the more conservative of the two candidate states — erring toward "move
more" costs the player a choice, erring the other way costs them an item.

This is the exact inverse of the rule for guest-memory **writes**, which must fail closed. Applying
one project-wide "fail safe" policy is how a project gets this backwards; the two cases have
opposite safe directions, and the comment at each call site should say which one it is.

## 6. The mode switch, its default, and the cost of the default

**(A)** Ship the network source as a three-position user setting, in the same settings screen as
everything else the player can change:

| position | behaviour |
|---|---|
| **local** | the engine, always; no socket is ever opened |
| **server** | the real endpoint, always |
| **auto** | try the server, fall back to the engine on failure |

**(B)** The reference port defaults to **local**, by explicit product decision.

**★ (A) State the cost of your default in the same place as the setting.** The cost of defaulting to
local is precise and must be written where the player will read it: *an account with items on a live
server cannot see them in local mode — they are not lost, but the row must be switched to reach
them.* Undocumented, the default looks exactly like data loss, and it looks that way to the players
who used the server most.

**(A) Why the fallback position exists at all.** The client has no retry and no timeout behind its
waiting modal, so "server unreachable" does not degrade — the feature is simply gone, including for
a player who never had a server. `auto` converts an outage into a mode change. The same fact is why
the guest patches in §12 apply **only in local-only mode**: under `auto` the game's own operator-era
warnings are still honest. Where the switch lives and how it persists is
[storage-and-config.md](../../android-host/reference/storage-and-config.md).

## 7. One store per process, session state per socket

**★ (A) The guest may open more than one socket.** If each connection constructs its own copy of the
persistent state, the last writer silently discards the other's work — and the bug appears only
under the interleaving the guest happens to produce, as "an item was deposited, and then it was not".

**(B)** How it was found: a device trace of the reference client showed two sockets opened back to
back, the second closed immediately. The split it settled on is the general one:

| scope | holds |
|---|---|
| **one per process** (shared, behind a lock) | the persisted rows — the store |
| **one per connection** | handshake progress, the pending-echo memo, the partial-frame reassembly buffer |

The per-connection list is short and every entry on it is genuinely per connection. Everything else
is shared, and a second connection must observe the first one's writes.

## 8. The store file: tolerant text that degrades

**(A)** Line-oriented plain text, one record per line, written **temp file + rename** so an
interrupted write can never leave a truncated store. Three properties, each worth one assertion:

- **Round trip.** Encode → decode → encode is byte-identical.
- **Degrade, never refuse.** A corrupt line is dropped and the store still opens. A store that
  refuses to open leaves the player with no way back into the feature at all, which is strictly
  worse than missing rows.
- **Preserve lines you do not understand.** A line written by a newer build must survive a
  downgrade. Without this, installing an older build is destructive.

**(A)** Text is chosen for a support reason, not an aesthetic one: a player who loses items can be
asked to paste the file, whereas a binary format turns one bad byte into a permanently inaccessible
feature. Clamp on load as well as on write (§9), and range-check every wire-sourced value at the
store boundary exactly as the server does — a field's wire *type* is not its *domain*
([server-architecture.md](server-architecture.md)).

## 9. Normalisation runs on LOAD

**★ (A) A migration that runs only on write does nothing for any existing install.** The new rule
applies solely to data created from now on, while every player who already has the problem keeps it
— and that population is exactly the one that reported it. The rule looks implemented, its tests
pass, and nothing changes for anybody.

**(A)** Fold on load, with three properties:

1. **Idempotent** — loading twice changes nothing, because this will now run on every launch forever.
2. **Clamp damaged values first**, whatever their origin, before any merging logic sees them.
3. **Stable ordering** — let the first row of a kind keep its key and its position, so the player's
   view barely moves and the fold does not read as a shuffle.

**(B)** How the reference port measured it: a tester's real device store, obtained through the
command file of §15, was loaded by the new fold — **198 rows collapsed to 5**. That tester had been
unable to clear those rows by hand because the in-game UI discards one cell at a time.

## 10. Persist only on a real change

**(A)** Write the store only when the encoded payload actually differs from what is already on disk.

**(B)** The reason is a client behaviour you can only find by watching the wire: the reference client
replays a save upload on **every page turn** and blocks on the ack. Persisting on every request
therefore puts a file write and a rename directly onto page-turn latency, which the player feels as
a stutter on the one screen they page through most.

**(A)** Note what kind of bug this was. The original server already had the dirty-check fast path;
the reimplementation dropped it. A fast path does not survive a port unless its *reason* is written
down next to it — which is §17.

## 11. In-process is not free

> **REFUTED.** *Previously believed:* an in-process transport costs nothing, so the socket path's
> throttles and fast paths are unnecessary on it. *Refuted by:* two device-reported slowdown
> regressions from the first cut. *Why it was attractive:* the local arm has no syscall, no
> handshake and no round trip, so "free" is nearly true **per call** — it is the call **rate** that
> is not free. *Actual mechanism:* the guest's wait loop polls receive tens of thousands of times
> per second; the socket path absorbed that with a timestamp gate and the local path skipped it,
> taking the connection-map lock on every poll. *Consequence:* page turns and storage screens
> stutter on the build that was supposed to be faster than the network.

**(A)** Route the local transport through the **same** receive-poll rate gate as the real socket.
This is safe only because the engine produces its reply synchronously inside `send` (§3): a reply
that exists is already queued before the guest polls, so gating the poll delays nothing. If you ever
make the engine asynchronous, that reasoning expires with it.

**(A)** And keep disk I/O off any path the player feels — §10, which is the second half of the same
regression.

## 12. Offline-only guest patches

**(A)** Retiring the wire does not retire the guest's own operator-era behaviour. Carrier-billing
warnings, "connect to the network?" etiquette prompts and idle-disconnect timers are the *game's*
constants and they survive the switch untouched. The idle timer alone will disconnect an offline
player after a few minutes for no reason at all.

**(B)** The reference port applies three such patches — a connection notice, a purchase-confirmation
sentence, and an idle-disconnect constant — each located the ordinary way (find the string,
cross-reference its loader, patch at the site) and re-verified idempotently every couple of seconds
because the guest can rebuild the region. The patching mechanics themselves are
[patching.md](../../guest-re/reference/patching.md). Apply them **only in local-only mode** (§6).

**★ (A) A sentence assembled by a formatted print cannot be fixed by editing one string.** If the
text the player objects to is built from a format string plus arguments, no single literal in the
image contains it. Repoint the **format pointer** at a spare tail literal holding the whole
replacement; the surplus arguments are then simply ignored.

> **REFUTED.** *Previously believed:* the patch was installed — the "is it already patched?" guard
> said so on every pass. *Refuted by:* the string on screen never changing, with no error anywhere.
> *Why it was attractive:* a prefix comparison is the obvious cheap idempotence test, and it is
> correct whenever the two strings differ early. *Actual mechanism:* the original and the
> replacement began with the same four bytes, so the guard matched the **original** and concluded
> the work was already done; the write never happened. *Consequence:* a patch that looks installed,
> reports success and has never once run. **Compare the whole replacement, never a prefix.**

## 13. The notice you cannot delete, and the host pressing OK

**(A)** Some frame in the login sequence exists purely to release the client's wait. When you have
nothing to put in it, the instinct is to stop sending it. Do not.

> **REFUTED.** *Previously believed:* there is nothing to announce in an offline session, so the
> announcement frame can be dropped. *Refuted by:* the client never leaving its wait. *Why it was
> attractive:* the frame is cosmetic — its only visible effect is a text box. *Actual mechanism:* it
> is the **sole finaliser of the login**; nothing else clears the wait flag (see
> [reply-re.md](reply-re.md), *find the blocking-state variable and the ONE reply that clears it*).
> And an **empty body crashes** the client's paged text builder, so "send it with no text" is not an
> escape either. *Consequence:* a login that completes on the wire and hangs on screen.

**(A)** The shape that works: send the frame with non-empty text, then have the **host** synthesise
the confirm keypress a few hundred milliseconds later, so the player never sees a box they have no
reason to dismiss. Arm the auto-dismiss when the notice is sent and disarm it on any real inbound
frame — **but not on the heartbeat.** The heartbeat fires on a fixed period regardless of what is on
screen, so letting it disarm makes the feature work or not depending on where in the heartbeat cycle
the notice happened to land: an intermittent bug with a periodic cause, which is the most expensive
kind to chase.

## 14. Co-location as a safety property

**★ (A) An engine that lives beside the client's private state can enforce invariants the protocol
cannot express.** This is the argument for making it the default, and it is stronger than the
availability argument.

**(B)** The worked case: the reference client's destructive restore reply is applied straight into
the live character with no occupancy check, and its *request* is byte-identical to a routine login
sub-request. A remote server therefore **structurally cannot** tell a user-initiated restore from a
login step — no amount of server-side care fixes that. An engine running next to the save records
simply never sends a restore for a slot that holds a character, because it can look. The refusal
itself, and the complementary rewrite of the outgoing frame that protects the player from *any*
server, are [refusals-and-holds.md](refusals-and-holds.md).

Generalise **(A)**: enumerate the replies whose damage depends on state the wire does not carry.
Each one is an invariant only a co-located engine can hold.

## 15. A one-shot command file for a release build

**(A)** A signed release build has no debug access to app-private storage, and in-game UI typically
edits one record at a time — so a store-format change has no route to verification on the build
players actually run. The temptation is to hand the tester a debug build, which then behaves
differently from the shipped one.

**(A)** The escape hatch: read a plain-text command file from shared storage at launch, apply it,
**then delete it**. Deleting is what stops it running again on the next launch.

**(B)** The reference port's version accepts a small vocabulary — remove every row of a given kind,
or remove everything — and was the only way its load-time fold (§9) could be exercised against a
real store. Two constraints **(A)**: scope the tool to the **engine's own store and never to save
records**, and log what it did, because a silent one-shot tool is indistinguishable from one that
did not run.

## 16. Re-derive the failure modes

**★ (A) Moving from sockets to an in-process engine changes which failure modes exist.** Inheriting
the socket-era list produces both false alarms and missed windows.

> **REFUTED.** *Previously documented:* a known narrow window for losing a held remainder was
> written up as "if the socket dies mid-transfer". *Refuted by:* adversarial re-derivation against
> the engine as it actually is. *Why it was attractive:* the sentence was true of the same feature
> one implementation earlier, and it was carried forward with the feature. *Actual mechanism:* an
> in-process socket cannot be killed by the platform — the trigger is the **process** dying, and the
> window needs three conditions at once (local mode, a partial transfer, and death within tens of
> milliseconds of the confirmation). *Consequence:* a porter guards the wrong event, and the real
> window stays open.

**(A)** The honest form is to bound the hole and leave it, with the candidate fixes and your
preference order written down beside it. A precisely bounded known hole is more useful than a vague
one, and a far better artefact than a fix nobody was able to evaluate.

## 17. Porting the server: carry the comments

**★ (A) The reimplementation is where a project pays a second time for everything its device
sessions bought.** Every device-learned constraint that is not restated as an assertion in the new
implementation's own suite is silently lost — the code compiles, the tests pass, and a fact nobody
remembers is gone.

**(B)** The reference port's engine module opens with the five constraints that were most expensive
to learn, each one a sentence a fresh implementation would not have thought of:

1. never pad a short list reply;
2. both list-header bytes are read as **signed**;
3. the pagination cursor must be stateless;
4. an **empty** notice body crashes the client's text builder;
5. a failure acknowledgement must carry its reason text **in the same frame**.

Its selftest header states the rule that keeps them: *every assertion below that mentions a session
number is a device-measured constraint a fresh implementation would otherwise lose.* Items 1, 2 and
5 are owned by [reply-re.md](reply-re.md) and [refusals-and-holds.md](refusals-and-holds.md) — carry
them across, do not re-derive them. **(B)** The instance of the loss actually happening is §10's
dirty check: present in the server, absent from the first engine, re-found as a visible stutter.

**(A)** Procedure: port the comments *first*, as failing assertions, then make them pass. Carrying a
suite across a language boundary is [conformance-tests.md](conformance-tests.md).

## 18. Generated shared tables with a `--check` gate

**(A)** Any table two implementations must agree on — a catalogue, an id map, a price list — is
**generated from a single authority**, in whichever language holds it, and never hand-copied. The
generator gets a `--check` mode that regenerates in memory and compares against the committed copy,
failing loudly when the two have diverged.

**(B)** In the reference port the engine's shop catalogue is emitted from the server's own objects,
and the generator's docstring says why: the table is not a constant but a *computed product* of
several tier tables, content flags and a delist set, each carrying a paragraph of device-measured
reasoning that would not survive being retyped. Two implementation notes **(A)**: run `--check` in
CI and as the first command of any session that touches the source data, and emit the generated file
with outer-style comments only if a selftest `include!`s it.

**(A)** The failure mode of a hand-copied table is worth naming because it does not look like
staleness: a player buys something the authority delisted, or an identifier resolves to a different
item on one side than on the other. Nothing errors.

## 19. Symptom table

| symptom | cause | fix |
|---|---|---|
| Page turns and storage screens stutter after moving the protocol in-process, where the real server was smooth | the local path skipped the receive-poll rate gate, and the store was written on every page turn | §11 and §10 — same gate as the socket path; persist only when the payload changed |
| An item deposited into local storage is intermittently just not there | two sockets opened in succession, each with its own copy of the store; last writer wins | §7 — one store per process, session state per socket |
| A migration or clamp you added and tested has no effect for any existing player | it runs only on write | §9 — fold on load, idempotent, clamp first |
| The offline build still warns about call charges and still disconnects after a few minutes | those are the guest's own operator-era constants, not yours | §12 — patch them, in local-only mode |
| A guest patch reports success on every pass and never changes anything | the idempotence guard compared a prefix and matched the original | §12 — compare the whole replacement |
| A sentence you patched is still wrong, and no single string in the image contains it | it is assembled by a formatted print | §12 — repoint the format pointer at a spare tail literal |
| The login completes but the client never leaves its wait, after you removed a notice you had nothing to put in | that frame is the sole finaliser of the login, and an empty body crashes the text builder | §13 — send it with text, dismiss it from the host, never disarm on the heartbeat |
| A store or command file cannot be inspected on a signed release build, so a format change cannot be verified | release builds have no debug access to app-private storage | §15 — one-shot command file, deleted after it applies |
| Players buy a catalogue row the authority delisted, or an id resolves differently on the two sides | a hand-copied shared table drifted | §18 — generate it; gate staleness with `--check` |
| The engine refuses something the player has already paid for, but only sometimes | a failed read of an outside fact was treated as "at the limit" | §5 — unknown means no limit known |

## 20. Checklist

Before the engine becomes the default network source:

- [ ] `handle(request_bytes, env) -> Vec<reply_payloads>`; the store is the only I/O.
- [ ] Every fact the engine cannot compute is a documented `Env` field, with the reason beside it.
- [ ] Every optional observation fails **open**: absent means no limit known, never at the limit.
- [ ] The local arm is a connection state in the socket layer; slot table untouched, decoder in front.
- [ ] The local transport goes through the same receive-poll rate gate as the socket path.
- [ ] The store is written only when the payload changed, and written temp + rename.
- [ ] One store per process; handshake, pending-echo memo and reassembly buffer are per connection.
- [ ] The store parses tolerantly, preserves unknown lines, and degrades rather than refusing to open.
- [ ] Normalisation runs on load: idempotent, clamps damaged values first, ordering stable.
- [ ] The mode switch has three positions, a stated default, and that default's cost written beside it.
- [ ] Guest patches are local-only, re-verified idempotently, guarded on the whole replacement.
- [ ] The login-finalising frame is still sent with a non-empty body, dismissed by the host, and the
      heartbeat does not disarm the dismissal.
- [ ] Every shared table is generated, with a `--check` mode wired into CI.
- [ ] Every device-learned constraint from the server exists as an assertion in the engine's suite,
      including a chunking-equivalence test and a full open-sequence replay.
- [ ] The failure-mode list has been re-derived for a process without sockets, not inherited.
