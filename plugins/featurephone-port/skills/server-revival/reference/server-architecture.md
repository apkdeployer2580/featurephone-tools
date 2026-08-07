# The replacement server

Four layers, one file each, and the separation **is** the design. The whole point is that the
layer carrying the protocol semantics has no sockets in it, so it can be tested exhaustively
without a device, without a network and without an event loop.

## Contents

- [Layering](#layering)
- [★ The testability hinge: `handle(bytes) -> list[bytes]`](#-the-testability-hinge)
- [Transport and framing](#transport-and-framing)
- [Admission control, rate limiting, idle timeout](#admission-control-rate-limiting-idle-timeout)
- [Session and identity](#session-and-identity)
- [Persistence](#persistence)
- [Range-check wire bytes AT the store boundary](#range-check-wire-bytes-at-the-store-boundary)
- [Limits, all derived from measurement](#limits-all-derived-from-measurement)
- [Features the original protocol never named](#features-the-original-protocol-never-named)
- [Known-weak spots to fix before scale](#known-weak-spots-to-fix-before-scale)
- [The economy is client-trusted — say so above the fold](#the-economy-is-client-trusted)

---

## Layering

| layer | responsibility | contract |
|---|---|---|
| **frame codec** | frames and unframes; refuses oversized frames; **knows nothing about payloads** | `Frame.encode(payload) -> bytes`, `FrameDecoder.feed(chunk) -> list[payload]` |
| **transport + policy** | asyncio TCP, optional TLS, admission control, idle timeout, rate limit, transcript, reply-table override, one handler per session | owns every socket in the process |
| **protocol semantics** | pure `enc_*` encoders + a stateful handler | `handle(payload) -> list[payload]` |
| **persistence** | account-keyed SQLite (WAL) | plain functions, range-checked at the boundary |

Keeping the codec dumb is not fastidiousness. The moment the codec peeks at a category to decide
something, the protocol layer can no longer be tested with byte strings, and the whole conformance
suite in [conformance-tests.md](conformance-tests.md) becomes impossible.

## ★ The testability hinge

```python
def handle(self, payload: bytes) -> list[bytes]:
    """(session state, request bytes) -> reply payloads. No sockets. No asyncio.
       No I/O except the store."""
```

Everything good downstream follows from this one signature:

* **A captured transcript converts into a test mechanically** — the JSONL `rx` records are the
  inputs, the `tx` records are the expected outputs. `replay_transcript.py` does exactly this.
* **134 assertions run in about a second with no device, no network and no event loop** (the
  reference port's measured figure).
* **A reply-table experiment and a handler implementation are interchangeable**, because both
  produce `list[bytes]` for a request — so a black-box finding is promoted to code without
  restructuring anything.
* **Refusal is expressible.** Returning `[]` means "say nothing", which is the safe failure mode
  (see [reply-re.md](reply-re.md)).

Resist adding `writer` or `loop` parameters. If a handler needs to do something asynchronous,
return a reply and queue the work outside.

## Transport and framing

```python
async def _session(reader, writer):
    dec = FrameDecoder(MAX_FRAME)
    handler = ProtocolHandler(store, session_state)
    while True:
        chunk = await asyncio.wait_for(reader.read(4096), timeout=IDLE_TIMEOUT)
        if not chunk:
            break
        for payload in dec.feed(chunk):
            for reply in (table_override(payload) or handler.handle(payload)):
                writer.write(Frame.encode(reply))
            await writer.drain()
```

Three decisions worth copying:

**One listener.** The reference client had two hardcoded destinations, and the naive reading is
"open two ports". RE showed the host redirect can point both at one endpoint, so the second
listener was **removed** — it was pure attack surface serving nobody. Enumerate the guest's
destinations; then serve the minimum number of ports that actually receive traffic.

**★ Bind failure is fatal, not `continue`.** A loop that tries several binds and continues past
failures can leave you with a "half server" listening only on the port nobody uses: healthy
process, healthy logs, no players. Fail the process, loudly, on any bind error.

**TLS is a wrapper, not a redesign.** `ssl.SSLContext(PROTOCOL_TLS_SERVER)`, minimum TLS 1.2, no
client certificates, cert and key from flags. Zero dependencies beyond the standard library.
Certificate strategy is in [deploy.md](deploy.md).

## Admission control, rate limiting, idle timeout

| control | why it exists |
|---|---|
| **idle timeout** | the server never writes unprompted, so a dropped NAT mapping produces **no RST** and the fd leaks forever. Derive the value from measured heartbeat gaps. |
| **total session cap** | bounds memory and fd usage on a small VPS |
| **per-IP session cap** | the game opens *one* socket per install; several from one address is abnormal. Do not set it to 1 — carrier NAT puts many real players behind one address. |
| **frame rate limit over a window** | legitimate bursts (opening a list screen is a handful of frames) pass; floods do not |

⚠ **Admission control runs after the TLS handshake.** asyncio completes the handshake before it
invokes your connection callback, so a per-IP session cap cannot protect you from a
handshake-CPU flood — the cheapest DoS available against a small box. If that matters, put an
accept-level token bucket in front of the TLS wrapper, or terminate TLS behind something that has
one. See [threat-model.md](threat-model.md).

## Session and identity

**Identity is whatever the login packet carried.** The server keys on those bytes; it does not and
cannot verify them. Read [threat-model.md](threat-model.md) **before** you ship this — the model
is "bearer token over TLS", and there is a challenge-response upgrade that fits inside the
existing frames.

Four rules that are correct regardless of which model you choose:

1. **Gate every account-scoped operation on "has this session logged in?"** — one
   `_require_account()` at the top of each handler.
2. **Refusal is silence.** Return `[]`. The guest treats no-reply as an incomplete request and
   times out cleanly; every crash in the refutation log came from a malformed reply.
3. **One account per connection.** Re-sending the *same* identity is a legitimate retry and must
   succeed. Re-binding the session to a *different* identity is refused — otherwise one socket is
   an enumeration tool.
4. **★ A short or absent identity is refused, never zero-padded.** Padding creates a communal
   "null account" that every misconfigured client shares, and its contents are then unattributable
   and undeletable. Raise at the store boundary rather than defaulting.

## Persistence

SQLite, one file, `journal_mode=WAL`, `synchronous=NORMAL`. Sizing rationale belongs in the
module docstring so the next person does not re-litigate it: **(B)** ~5–20 KB per user, 50k users
≈ 0.5–1 GB — SQLite is correct; a networked database is only warranted once you shard.

Schema shape **(A)**, with the [threat-model.md](threat-model.md) correction already applied —
**the key is a hash of the credential, never the credential**:

```sql
-- acct = sha256(credential_bytes) hex. The raw credential is never stored.
accounts (acct TEXT PRIMARY KEY, created INTEGER)
blobs    (acct TEXT, slot INTEGER, blob BLOB, updated INTEGER, PRIMARY KEY(acct, slot))
items    (acct TEXT, item_key BLOB, record BLOB, updated INTEGER, PRIMARY KEY(acct, item_key))
balances (acct TEXT PRIMARY KEY, currency_a INTEGER, currency_b INTEGER)
capacity (acct TEXT PRIMARY KEY, pages INTEGER)
```

Why the hash costs nothing: the server never needs the raw identity **after** the lookup. With it,
a leaked database file stops being a mass credential leak — which matters because the realistic
leak paths are a VPS snapshot, a support ticket, a nightly backup left in a world-readable
directory, and a `git add` (see [threat-model.md](threat-model.md)).

⚠ **Player saves are personal data.** The recovery feature means users are trusting you with them.
At-rest encryption, or at minimum a documented backup-handling policy, is warranted before you
invite strangers.

## Range-check wire bytes AT the store boundary

**★ (A) A wire field's *type* is not its *domain*.** A slot index is a `u8` on the wire, so 256
values are expressible; if the game has three slots, **only three are legal**, and the store — not
the handler — is where that is enforced.

```python
def put_blob(self, acct, slot, blob):
    if not (0 <= slot < N_SLOTS):     # a u8 on the wire; 0..2 in the game
        return False
    ...
```

Without it, one connection allocates 256 rows per account by looping a byte. Every wire-sourced
value that becomes part of a primary key or a row count gets this treatment.

Companion control: a **per-account row cap**, with *replace* always allowed because replacing does
not grow the table. Without the exception you break legitimate use at the cap; without the cap an
upload loop is an unbounded write amplifier on a database every player shares.

## Limits, all derived from measurement

**(B)**, and the *derivations* are what transfer:

| knob | value | derivation |
|---|---|---|
| idle timeout | 45 s | 6.6× measured worst-case heartbeat gap |
| sessions | 64 total / 4 per IP | one socket per install; 4 for carrier NAT |
| frame rate | 20/s over a 5 s window | largest legitimate burst plus headroom |
| rows per account | 200 | above any legitimate session's writes |
| frame size | 1016 B | **the guest's reassembly buffer**, not a round number |
| transcript | **OFF** | it records every user's credential, saves and address in one file |

Never pick these from taste. Each one should trace to a transcript
([black-box-loop.md](black-box-loop.md)) or to a guest allocation ([framing.md](framing.md)).

## Features the original protocol never named

**(A)** Look for every value in your replies that the client merely *believes*. Each is a feature
you can add with no client change:

* a capacity byte the client trusts ⇒ sellable storage expansion;
* a status/flag byte with an unused value ⇒ a new result state;
* a text field ⇒ a message-of-the-day, patch notes, or an outage notice — **and note that a login
  notice is your only broadcast channel to every installed copy**, since you cannot push an update
  to a sideloaded build.

And the counterpart constraint: **you do not invent items.** The item database lives in the game's
own archive resources; the wire carries only identifiers and the client resolves names and effects
itself. A "shop" is a list of records that re-lists things the game already ships.

## Known-weak spots to fix before scale

Carried honestly from the reference implementation, because a reader will otherwise copy them:

| issue | consequence | fix |
|---|---|---|
| **synchronous `commit()` inside the asyncio handler** | one client doing writes at the rate limit stalls every other player | `run_in_executor`, or a single writer task with a queue |
| **admission control after the TLS handshake** | handshake-CPU flood is unmitigated | accept-level token bucket in front of TLS |
| **no duplicate-login guard** | the same identity from two devices gives concurrent sessions on one account and racing writes | a per-account session lock; also mitigates theft (see [threat-model.md](threat-model.md)) |
| **primary key taken from a client-chosen id** | two identical items collapse into one row; a chosen colliding key is a **deletion** primitive | server-generated keys with real entropy, or `(acct, client_key, seq)` |

## The economy is client-trusted

State this **above the fold** in the server's README, not in a footnote.

**(B)** as shipped, the reference server: always approves purchases and deducts nothing; **echoes
the commit request back verbatim** (a reflection primitive and an item-duplication path); stores
deposited item records **unvalidated**, so any item can be forged; and grants bonuses keyed off
client-chosen bytes.

For a private server hosting your own character, that is a deliberate and reasonable choice. On a
**shared** server it means one player mints items into everyone's economy. If your server is
public:

1. Validate deposited records against the game's own item tables (which you can read from the
   archive) before storing them.
2. Make purchases actually debit, server-side, before approving.
3. Never echo a request as a reply. Construct the reply from server state.
4. Then say, in the README, which of these you did and which you did not.
