---
name: server-revival
description: Revives or retires the dead server of an online feature-phone game (WIPI/KTF, LGT, SKT/SKVM, J2ME MIDlet) without patching the guest - redirects connect at the emulator socket layer, derives the framing, enumerates the requests statically and reverse-engineers the replies from the client's handlers, then either stands up a replacement server (conformance suite, TLS, threat model) or retires the wire for an in-process engine needing no network. Also: refusing without hanging a reply-gated UI, and copying the client's lossy apply procedure so a chosen quantity never destroys items. Use when the user mentions a private or replacement game server, a shut-down official server, an offline or in-process network mode, online storage, backup, recovery or shop features, packet reverse engineering, socket slots or connect redirect, a client stuck on a waiting-for-server modal, frozen after resume, or closing the socket after a success, items that silently disappear, or certificate pinning.
license: MIT
---

# Server revival — putting a dead online title back on the wire, or taking it off

**This chapter is OPTIONAL.** Most feature-phone titles are entirely offline and skip it. You
need it only if the guest opens a socket. Cheap test before committing: dump the guest's
strings and look for messages that can only appear while a server is answering — a
waiting-for-server modal, "saving to the server", a purchase or refresh progress line. No such
strings, no server work; go back to `port-triage`.

Conventions: `${CLAUDE_PLUGIN_ROOT}/shared/evidence-standard.md`.
Tags: **(A)** generic procedure · **(B)** worked example from the reference port · **(C)** do not copy.
Reference-port files are cited as `(ref: server/protocol.py)` — read the mechanism, ignore the path.

⚠ **Scope of the mechanics.** Phases 1–4 assume an **AOT-compiled ARM guest**: they read a serialiser
chokepoint, a `BL` caller set and fixed-offset loads out of a disassembly, and the packaged tools
(`extract_packets.py`, `name_requests.py`) decode Thumb only. **Phases 0 and 5–7 are host-side and
transfer unchanged.** For a **JVM-bytecode guest** — a J2ME MIDlet, or an SKT/SKVM title, which is
also bytecode ([platform-matrix.md §10](../port-triage/reference/platform-matrix.md#10-what-differs-per-platform))
— the same five questions apply (framing, request surface, reply layout, the blocking-state variable,
the unclamped array) but the tooling is a class dumper and a bytecode decompiler: the chokepoint is
the method that writes and flushes the stream behind `Connector.open`, callers are enumerated through
constant-pool methodrefs rather than by scanning `BL` targets, Phase 3's string chain collapses to a
constant-pool reference search (no relocation delta, no static base, no literal pool), and
width/endianness/signedness come from the stream API (`readByte` vs `readUnsignedByte`, big-endian by
specification) rather than from a load instruction's flavour. The signedness *obligation* is
unchanged: clamp to the client's signed range, never mask. **Nobody has run this path** — see
[reference-implementation.md](../../shared/reference-implementation.md).

## Three outcomes, and the rule that picks one

A title that opens a socket ends in one of three places. Decide which one you are heading for
**before Phase 6**, because the back half of the chapter is a different job in each.

| outcome | you build | you own afterwards, forever |
|---|---|---|
| **no socket** | nothing in this chapter | — |
| **socket + a hosted replacement server** | Phases 0–6 | a host, TLS, a certificate, an account store, a threat model, an operator |
| **socket + an in-process engine** | Phases 0–5, then **Phase 7** | nothing that can lapse — the engine ships inside the app |

> **The decision rule: if the feature behind the socket is genuinely between *players*, you need a
> server. If it is between the player and the game, retire the wire.**

Storage, a shop, item combining, paging and backup are usually the second kind even in a title whose
own UI calls them "online". Three measured reasons to prefer retirement where you can (all three in
[in-process-engine.md](reference/in-process-engine.md) §1): the platform destroys backgrounded
sockets and the only cure is a foreground service many projects decline; the online UI is often the
**only** route to several single-player features; and **every byte a server can send is a byte that
can damage the player's save** ([threat-model.md](reference/threat-model.md)).

**In practice you build both, in this order.** The replacement server is the **instrument** — it is
the only way to learn a dead protocol on real hardware, because it is the only thing you can change
while the client stays connected (Phase 5). The engine is the **product**. Jumping to Phase 7 without
Phases 0–5 is not a shortcut, it is guessing; the `handle(request) -> replies` signature you build in
Phase 6 is what you carry across.

## Two invariants for the whole chapter

1. **Never patch the game archive.** The dead server's address stays in the binary. You own the
   emulator's socket layer, so you rewrite the destination at the host boundary. That is what
   makes this technique work for any title whose net calls pass through the emulator API.
2. **Static RE gives shapes; only the client gives semantics.** Every reply layout derived from
   disassembly is a hypothesis until a device runs it. Budget **at least one refutation per
   feature**, and keep the refuted claim visible next to its correction.
   **Corollary: budget one preserved refuted *candidate fix* per feature too** — a repair you
   designed, investigated to the end and dropped is invisible in the diff, so without a note it gets
   re-invented and re-investigated. And a refutation is not final: one of this chapter's own worked
   refutations was later refuted by a production log
   ([black-box-loop.md](reference/black-box-loop.md)).

## Phase 0 — Redirect connect, then record the silent baseline

Goal: the client connects to *you*, and a server that says nothing does not cause a teardown.

1. Find the connect site and its dotted-quad argument in the guest. Games of this era carry
   their own `inet_addr`, so the address survives as an ASCII literal.
2. Implement the platform's socket slots for real. **Do not trust upstream slot names** —
   emulator forks label vendor API slots by guess. Trace what the game actually calls.
3. Get the host rules right: nothing blocks · `recv` returns the platform's *no-data sentinel*,
   never `0` · `recv` clamps to the guest's **real allocation**, not the size it asks for · a
   **failed** socket counts as readable · the read callback is implemented, not stubbed.
4. Make the destination runtime-configurable on day one: device file → build-time env →
   loopback, re-read on every connect, never gated on `debug_assertions`.
5. **Then run a server that accepts, holds the connection and replies to nothing**, and record a
   transcript. This mute run is your baseline. Without it no later behavioural change can be
   attributed to a reply you added, and you will misread every experiment that follows.

Exit: the client stays connected to a mute server for minutes, heartbeats appear in the
transcript, no teardown. → [socket-layer.md](reference/socket-layer.md)

## Phase 1 — Derive the frame layer three independent ways

Do not accept a framing until **two independent derivations agree; get three**: the receive
pump's disassembly, bytes captured from the live client, and the **send-side serializer** (only
the flush routine tells you whether the length word is header-inclusive). Extract three more
things here, because each bites later: the reassembly buffer size (that is your hard frame cap,
enforced in the encoder), whether the image has both LE and BE integer writers (then assume
nothing about payload endianness), and the dispatcher's minimum-length guard. Write the decoder
as an **incremental accumulate-then-dispatch mirror of the guest's own logic** so your message
boundaries match the game's exactly.

Exit: a codec that round-trips every captured byte string and refuses oversized frames.
→ [framing.md](reference/framing.md)

## Phase 2 — Enumerate the whole request surface statically

The highest-leverage mechanical trick in the chapter. Anchor on the serializer: identify
`put_u8` / `put_bytes` / `put_u32*` / `flush`, then **every `BL flush` site is exactly one
request**. Walk back to the prologue, replay the `put_*` calls into a template, and **reset the
template at every branch** — a linear sweep unions mutually exclusive branches and invents
messages no client ever sends. Self-validate: the extractor must independently reproduce packets
you already captured. Group templates by leading constant byte; that histogram *is* your category
discovery, and cross-checking it against the receive dispatcher's `switch` establishes the
category/opcode split from both directions.

Run: `python "${CLAUDE_PLUGIN_ROOT}/tools/extract_packets.py" -t target.toml --flush 0x… …`

Exit: a catalogue that reproduces ≥2 captured packets, plus a category histogram.
→ [request-discovery.md](reference/request-discovery.md)

## Phase 3 — Name the requests without triggering them

Every online operation shows a localized status message from the *same function* that sends the
request. Chain: string offset → relocated pointer word → the static slot holding it →
static-base-relative offset → literal-pool words equal to that offset → nearest `flush` within a
window. Per-string confidence is honestly MEDIUM; **cluster-level attribution is reliable**. The
same string dump is your **feature inventory** — the server-only messages tell you exactly what
you must implement.

Run: `python "${CLAUDE_PLUGIN_ROOT}/tools/name_requests.py" -t target.toml --strings ids.txt`

Exit: every catalogued send site has a feature label or an explicit "unknown".
→ [request-discovery.md](reference/request-discovery.md)

## Phase 4 — Reverse the replies from the client's own handlers

Requests you catalogue statically; replies you derive from what the client *does* with them.
Enumerate the dispatcher's `switch(category)` — anything outside it is unreachable. Reply parsers
of this era are fixed-offset inline reads, so the layout comes straight off the disassembly. Then
find the three things that decide whether your server is usable at all:

* **The blocking-state variable and the ONE reply that clears it.** Everything else is
  unobservable until you can clear it, and you will misattribute every experiment.
* **Every fixed-size array the client fills from your reply, and whether it clamps.** If it does
  not clamp, **the bound is a server obligation** — enforce it in the encoder, once.
* **Every unbounded `sprintf`/`%s` destination and its real capacity.** Those are heap smashes on
  a player's phone.

A fourth thing, added after the chapter shipped and now an exit criterion of its own: **audit every
quantity *you* choose for the width, endianness AND signedness of the client's read** — an unsigned
capacity of 128 read back with `ldrsb` is −128, and it disabled both page-turn keys of a real
player's screen. Clamp to the client's signed range; never mask.

Exit: you can clear the wait state; every list/text encoder enforces a client-derived bound; and **no
host-chosen quantity ships without an executable model of the client's own apply procedure**
([client-fidelity.md](reference/client-fidelity.md)).
→ [reply-re.md](reference/reply-re.md)

## Phase 5 — The black-box loop and the refutation log

The design constraint is iteration cost: rebuild → install → navigate menus is minutes. Push
everything changeable to the server, and make the server changeable **while the client stays
connected** — a request-hex → reply-hex override table reloaded on every lookup, outranking the
state machine per request, shipped **OFF by default** because a catch-all entry hijacks every
user's replies. Log append-only JSONL with a per-frame `gap`; those gaps are what later produce
your idle-timeout and rate-limit constants. Script the device run so it is reproducible, and
write down every static claim the device refutes.

Exit: ≥1 recorded refutation, and timing constants derived from measurement, not guessed. Any
candidate fix you investigated and dropped is written down too, with the facts that killed it.
→ [black-box-loop.md](reference/black-box-loop.md)

## Phase 6 — The replacement server, its tests, its semantics

Four layers, one file each: frame codec · transport and policy · protocol semantics ·
persistence. The hinge is `handle(request_bytes) -> list[reply_bytes]` — no sockets, no event
loop, no I/O except the store. That signature is what makes a device-free conformance suite
possible, it converts a captured transcript into a test mechanically, **and it is the artifact you
port if you go on to Phase 7.**

* Architecture, store schema, wire-byte range checks, measured limits → [server-architecture.md](reference/server-architecture.md)
* The assertion classes, TLS e2e, exploit-as-test, chunking equivalence → [conformance-tests.md](reference/conformance-tests.md)
* **Choosing the numbers: imitate the client's algorithm, not its intent** → [client-fidelity.md](reference/client-fidelity.md)
* **Saying no, holding a reply, retiring a feature** → [refusals-and-holds.md](reference/refusals-and-holds.md)

**Deployment and the threat model are only mandatory for the hosted outcome.** If you are heading for
Phase 7, skip the last two links below on the first pass — an engine has no host, no certificate, no
account store and no remote party. It still has [threat-model.md](reference/threat-model.md)'s
save-authority section, which applies to any server you *ever* connect to.

* Firewall layers, certificate and pinning tradeoffs, systemd, installer → [deploy.md](reference/deploy.md)
* Auth model, the challenge-response upgrade, the replies that can overwrite a character, publish checklist → [threat-model.md](reference/threat-model.md)

Exit: conformance suite green; the service survives a restart and a network reset; the auth model
is stated in one sentence at the top of the README; the publish audit is clean.

## Phase 7 — Retire the wire

**The terminal answer for a single-player title.** Give the socket layer a third connection state:
`connect` constructs the engine, `send` feeds it request bytes, `recv` drains its replies. The
guest's socket API is untouched and the client cannot tell — the rest of the socket layer simply sees
an fd that never fails. Everything Phase 6 built survives: the same `handle()` shape, the same
assertions, the same encoders, in whatever language the host is written in.

What is genuinely new here, and none of it is obvious from Phase 6: a **three-position mode switch**
(local / server / server-first-with-fallback) whose default's cost is stated in the same place as the
setting · a **local store** with a tolerant parser and normalisation that runs on **load**, not only
on write · **offline-only guest patches** for the operator-era warnings and the idle-disconnect timer
the guest still believes in · **one store per process, session state per socket** · and re-deriving
the failure modes, because an in-process socket cannot be killed by the platform, so the trigger is
the process.

* Wiring, the mode switch, the local store, guest patches, porting the comments → [in-process-engine.md](reference/in-process-engine.md)
* The lifecycle facts the engine changes and the ones it does not → [socket-layer.md](reference/socket-layer.md)

Exit: every online feature works with the network permanently off; the engine's suite restates every
device-learned constraint the server's suite held; and the same requests delivered one byte at a time
produce identical replies.

## Symptom → phase

| symptom on device | phase |
|---|---|
| "cannot connect to server" text | 0 — only a refused/RST status produces it; accept and hold the connection |
| client never reads anything you send | 0 — the read-callback slot is stubbed in the emulator |
| client tears down a healthy connection | 0 — `recv` returned `0` instead of the no-data sentinel |
| app frozen forever after a network reset, only off-LAN | 0 — a socket-error poll verdict dropped the read waiter silently |
| guest heap corruption on large replies | 1 — frame exceeded the reassembly buffer, and the guest over-asks |
| permanent waiting-for-server modal | 4 — you replied, but not with the one op that clears the wait flag |
| crash at `address: 0` right after your reply | 4 — empty body where a text/page builder requires content |
| corruption or crash after a list reply | 4 — your `N` exceeded a client array that has no clamp |
| client cleanly closes the socket after a *success* | 4 — a toast-class reply dismissed the modal without advancing state |
| replies work singly, break when batched | 4 — the completed-frame ring is tiny; answer at request-response pace |
| works on LAN, times out from the internet | 6 — two firewall layers, or the client is not even aiming at a public address |
| items silently disappear on a transfer or a delivery, only for near-full inventories | 4/6 — you sized the operation with a "total free space" model; copy the client's own placement procedure |
| the client hangs forever on a wait after you declined to answer | 6 — silence is not a refusal for a reply-gated UI; reply failure-plus-text |
| the client crashes inside its own error box on a refusal | 4 — status-only failure ack; its message box reads past the end of your frame |
| frozen on a network screen after a background/resume, no network fault, no LAN repro | 0 — the platform destroyed the socket; probe on the first call after resume |
| the screen will not change however you reply, or whether you close the socket | 4 — only the client's own modal moves the client's screen |
| page-turn keys vanish, or a cursor jumps, once a player's capacity grows | 4 — the header byte is read signed; clamp, do not mask |
| storage renders as pages of one item the player never owned | 4 — a short list reply was padded; an all-zero row is a real item |
| a character resets after a "backup" or "recovery" operation | 4/6 — a restore-class reply, whose request has a second, innocent sender |
| everything succeeds, the screen opens, then every later request waits forever | 6 — one request, two senders: the reply correct for one tore the socket down for the other |
| page turns stutter after you move the protocol in-process | 7 — the local arm skipped the receive-poll rate gate, or the store is written on every turn |
