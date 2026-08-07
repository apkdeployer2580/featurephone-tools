---
name: server-revival
description: Revives the dead server of an online feature-phone game (WIPI/KTF, LGT, SKT/SKVM, J2ME MIDlet) without patching the guest — redirects connect at the emulator socket layer, derives the frame format three independent ways, enumerates every request from the send-side serializer, names requests by chaining UI strings to their send sites, reverse-engineers reply layouts from the client's own handlers, then stands up a replacement server with a byte-level conformance suite, a hot-reloadable reply table for black-box probing, TLS with certificate pinning, and a written threat model. Use when the user mentions a private or replacement game server, a shut-down official server, online storage, backup, recovery or in-game shop features, packet or protocol reverse engineering, socket slots or connect redirect, a client stuck on a waiting-for-server modal, a client that closes the socket after a successful reply, a reply table or replies.json, packet transcripts, or certificate pinning for a game client.
license: MIT
---

# Server revival — putting a dead online title back on the wire

**This chapter is OPTIONAL.** Most feature-phone titles are entirely offline and skip it. You
need it only if the guest opens a socket. Cheap test before committing: dump the guest's
strings and look for messages that can only appear while a server is answering — a
waiting-for-server modal, "saving to the server", a purchase or refresh progress line. No such
strings, no server work; go back to `port-triage`.

Conventions: `${CLAUDE_PLUGIN_ROOT}/shared/evidence-standard.md`.
Tags: **(A)** generic procedure · **(B)** worked example from the reference port · **(C)** do not copy.
Reference-port files are cited as `(ref: server/protocol.py)` — read the mechanism, ignore the path.

## Two invariants for the whole chapter

1. **Never patch the game archive.** The dead server's address stays in the binary. You own the
   emulator's socket layer, so you rewrite the destination at the host boundary. That is what
   makes this technique work for any title whose net calls pass through the emulator API.
2. **Static RE gives shapes; only the client gives semantics.** Every reply layout derived from
   disassembly is a hypothesis until a device runs it. Budget **at least one refutation per
   feature**, and keep the refuted claim visible next to its correction.

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

Exit: you can clear the wait state, and every list/text encoder enforces a client-derived bound.
→ [reply-re.md](reference/reply-re.md)

## Phase 5 — The black-box loop and the refutation log

The design constraint is iteration cost: rebuild → install → navigate menus is minutes. Push
everything changeable to the server, and make the server changeable **while the client stays
connected** — a request-hex → reply-hex override table reloaded on every lookup, outranking the
state machine per request, shipped **OFF by default** because a catch-all entry hijacks every
user's replies. Log append-only JSONL with a per-frame `gap`; those gaps are what later produce
your idle-timeout and rate-limit constants. Script the device run so it is reproducible, and
write down every static claim the device refutes.

Exit: ≥1 recorded refutation, and timing constants derived from measurement, not guessed.
→ [black-box-loop.md](reference/black-box-loop.md)

## Phase 6 — The replacement server, its tests, its deployment, its threat model

Four layers, one file each: frame codec · transport and policy · protocol semantics ·
persistence. The hinge is `handle(request_bytes) -> list[reply_bytes]` — no sockets, no event
loop, no I/O except the store. That signature is what makes a device-free conformance suite
possible, and it converts a captured transcript into a test mechanically.

* Architecture, store schema, wire-byte range checks, measured limits → [server-architecture.md](reference/server-architecture.md)
* The five assertion classes, TLS e2e, exploit-as-test → [conformance-tests.md](reference/conformance-tests.md)
* Firewall layers, certificate and pinning tradeoffs, systemd, installer → [deploy.md](reference/deploy.md)
* Auth model, the challenge-response upgrade, publish checklist → [threat-model.md](reference/threat-model.md)

Exit: conformance suite green; the service survives a restart and a network reset; the auth model
is stated in one sentence at the top of the README; the publish audit is clean.

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
