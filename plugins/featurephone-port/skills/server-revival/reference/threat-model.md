# Threat model

**State your authentication model in one sentence, at the top of the README, before anything
else.** If you cannot write that sentence, you do not have a model — you have a default.

For the shape of server most people build here, the honest sentence is:

> **Bearer token over TLS: anyone who learns your account code is you.**

That sentence is not the end of the story. This file explains why the model ends up that way, why
the claim that it is *unfixable* is **wrong**, and what to build instead.

## Contents

- [★ CORRECTION: "structural, cannot be fixed" is wrong](#-correction-structural-cannot-be-fixed-is-wrong)
- [The reference design: challenge-response inside the existing frames](#the-reference-design-challenge-response-inside-the-existing-frames)
- [The fallback: device-derived bearer id](#the-fallback-device-derived-bearer-id)
- [Prove it: exploit-script-as-test](#prove-it-exploit-script-as-test)
- [Key the store on a hash of the credential](#key-the-store-on-a-hash-of-the-credential)
- [Replay, session binding, rotation](#replay-session-binding-rotation)
- [Transcripts and peer addresses](#transcripts-and-peer-addresses)
- [Never let a live account database live in the source tree](#never-let-a-live-account-database-live-in-the-source-tree)
- [The server can crash any player at will](#the-server-can-crash-any-player-at-will)
- [Denial of service](#denial-of-service)
- [Pin distribution and rotation](#pin-distribution-and-rotation)
- [The account code is a recovery phrase](#the-account-code-is-a-recovery-phrase)
- [Publish-readiness checklist](#publish-readiness-checklist)

---

## ★ CORRECTION: "structural, cannot be fixed" is wrong

The reference port's own documentation framed its authentication model as **structural** — "the
original protocol is fixed, so nothing better is possible". **That framing is wrong, and carrying
it forward would propagate the bug.**

Here is why it is wrong:

1. **You own the client end of the wire.** The emulator's `send` path already rewrites outgoing
   frames — that is how identity gets injected in the first place
   ([socket-layer.md](socket-layer.md)). The `recv` path can equally strip bytes before the guest
   ever sees a frame.
2. **You own the server end of the wire.** You wrote it.
3. **There is already an unused round trip.** The server replies to the handshake *before* the
   client sends its identity. That reply is a body you control, arriving at a moment you control,
   in a frame the guest tolerates.

Three facts, and a challenge-response protocol falls out. **No guest changes. No new frames. No
new opcodes. One HMAC.**

The original protocol being fixed constrains what the *guest* can do. It does not constrain what
the *host* can do, and the host is yours.

## The reference design: challenge-response inside the existing frames

```
client (guest)              host (yours)                     server (yours)
     |                           |                                 |
     |-- handshake ------------->|-- handshake ------------------->|
     |                           |                                 | generate nonce
     |                           |<-- handshake-ack + nonce -------|   (in the ack body,
     |<-- handshake-ack ---------|  strip nonce, remember it       |    which is unused today)
     |     (unmodified bytes)    |                                 |
     |                           |                                 |
     |-- login (stub identity) ->|                                 |
     |                           | overwrite identity field with   |
     |                           | HMAC(device_secret, nonce)[..N] |
     |                           |-- login (derived identity) ---->| verify against the stored
     |                           |                                 | verifier for this account
```

Concretely:

1. **Server → nonce.** Put a fresh random nonce in the body of the handshake acknowledgement. The
   guest never sees it (step 2), so the body's content is unconstrained by the client's parser.
2. **Host strips it.** The host's receive path removes the nonce before handing the frame to the
   guest, restoring the exact byte string the guest expects. The guest's dispatcher sees the reply
   it has always seen.
3. **Host answers it.** In the send path, instead of writing the raw account id into the identity
   field, write `HMAC(device_secret, nonce)` truncated to the field width. The device secret never
   leaves the device.
4. **Server verifies.** Look up the account (by an accompanying account selector, or by trying the
   session's claimed account), recompute the HMAC with the stored key, compare in constant time.

What this buys, for the cost of one HMAC:

| property | bearer token | challenge-response |
|---|---|---|
| replay of a captured login | **works forever** | fails — the nonce is one-shot |
| a leaked server database logs in as everyone | **yes** | no, if you store a verifier |
| a copied device file / backup / screenshot | **is the account** | still is the account (that is a device-security problem, not a protocol one) |
| protocol changes required in the guest | — | **none** |
| new frames on the wire | — | **none** |

Notes on getting it right:

* **The identity field width bounds your MAC truncation.** A 16-byte field gives 128 bits, which
  is plenty. A smaller field is still far better than a static token; do not skip the upgrade
  because the field is 8 bytes.
* **The nonce must be single-use and time-bounded**, tracked per connection, and discarded on
  reconnect.
* **Compare in constant time.**
* **Keep an account selector.** The server needs to know *which* verifier to check. Either send a
  non-secret account selector in the same frame (there is usually a service field with room), or
  bind the selector to the TLS session at connect time.
* **Store a verifier, not the key**, wherever your MAC construction allows it.

Build this if your server is public, or if it holds anything a player would be upset to lose.

## The fallback: device-derived bearer id

If you do not build challenge-response, then this is what you have, and it must be **labelled** in
exactly these words:

> **Fallback model — bearer token over TLS.** A per-device random identifier separates users. It
> does **not** authenticate them. Anyone who learns the identifier is that account, permanently.

Properties of the fallback:

* TLS protects it **on the wire** and nowhere else.
* Per-device ids do keep users apart, which is a real improvement over a client that hardcodes one
  shared identity. That is the *only* thing they do.
* A screenshot of the transfer code, a device backup, a leaked database or a copied file are all
  full account compromise.

The fallback is defensible for a private server hosting your own character. It is not defensible
for a shared public server, and it must not be described with the word "authentication".

## Prove it: exploit-script-as-test

Ship a script that performs the takeover, and make it **exit 0 only on success**. Shape:

1. start the real server on a scratch database;
2. plant a victim account with some contents;
3. connect with a **plain socket** — no phone, no app, no emulator;
4. log in with the victim's identity, list their inventory, take an item, read their balance;
5. exit 0 if that worked.

Roughly twenty lines. Two payoffs: it documents the threat model by executing it (nobody argues
with a script that prints another account's inventory), and after the challenge-response
mitigation lands you invert the expectation and it becomes a **regression check** that no other
path reopened.

Reference it from the README, immediately after the one-sentence auth statement.

## Key the store on a hash of the credential

**★ Never store the credential itself, and never make it a primary key.**

```python
acct_key = hashlib.sha256(credential_bytes).hexdigest()   # this is what goes in the DB
```

The server never needs the raw identity after the lookup, so this costs nothing and changes the
consequences of a database leak completely:

| | keyed on the credential | keyed on `sha256(credential)` |
|---|---|---|
| DB leak (snapshot, backup, support ticket, `git add`) | **every account is compromised** | attacker gets hashes; they are not login tokens |
| operator with shell access can impersonate any user | yes | no |
| functionality lost | — | none |

With challenge-response you store a **verifier** instead, which is strictly better again.

Two companion rules from [server-architecture.md](server-architecture.md):

* **A short or absent identity is refused, never zero-padded.** Padding creates a communal null
  account that every misconfigured client shares — unattributable and undeletable.
* **Player saves are personal data.** Recovery features mean users are trusting you with them.
  Encrypt at rest, or at minimum document your backup handling.

## Replay, session binding, rotation

* **Replay.** If the login frame carries a sequence counter, check whether anything parses it —
  **(B)** in the reference port, nothing did, so a captured login frame replays forever. TLS hides
  it from a passive observer and does nothing against a copied credential. Challenge-response
  fixes this; nothing else does.
* **Session binding.** Bind the authenticated identity to the TLS session, and refuse re-binding
  to a different identity mid-connection ([server-architecture.md](server-architecture.md)).
  Otherwise one socket is an enumeration tool.
* **Duplicate login.** Without a per-account session lock, the same identity from two devices
  gives concurrent sessions on one account, racing writes, and — combined with a bearer model — an
  attacker who can connect *at the same time as the victim*. That makes theft racy rather than
  blocked.
* **Rotation.** In a bearer model there is usually **no way to rotate an identifier while keeping
  the data**: changing the code orphans the account. Design the rotation path (an
  authenticated "re-key this account to this new verifier" operation) at the same time as the
  credential, not after someone's code leaks.

## Transcripts and peer addresses

* **Transcripts are credential-and-PII-complete.** Default OFF; default path **outside** the
  source tree; mask by **field allowlist**, never by op denylist (a denylist fails open the moment
  you add a credential-bearing op); define rotation and retention. Full treatment:
  [black-box-loop.md](black-box-loop.md).
* **★ Never log peer IP addresses into a transcript that could be committed.** They are personal
  data, they are useless for protocol RE, and they are the field most likely to survive a careless
  `git add`. If you need them for abuse handling, log a salted hash with short retention, in a
  separate file, outside the repository.

## Never let a live account database live in the source tree

**★ The single most likely way you leak your players' data is a default path.**

A default like `DB_PATH = <package_dir>/app.db` means a developer who runs the server from a
checkout creates **real user data inside a git repository**. The systemd unit overrides it, so the
production box is fine — the *default* is the trap, and the default is what everyone runs locally.

**Fix:** default to a state directory outside the package (`./state/`, `$XDG_STATE_HOME`,
`/var/lib/<service>`), or require `--db` with no default at all.

Three more rules, learned from a real near-miss:

1. **`.gitignore` prevents future additions. It never removes past ones.** A correct ignore rule
   added today does nothing about the database committed last month.
2. **A history purge is undone by the backup branch you made "just in case".** Deleted commits
   remain reachable from any ref that still points at them — a local branch, a tag, a reflog entry
   — and `git push --all`, a fork, or a full clone bundle republishes them.
3. **Audit all refs, not the working tree.**

```bash
python "${CLAUDE_PLUGIN_ROOT}/tools/publish_audit.py" --repo . --all-refs
```

If a credential *was* exposed: purge history, delete every backup ref, expire reflogs, `git gc
--prune=now`, verify by object listing — **and rotate the affected accounts server-side.** In a
bearer model the identifier is the password, so a purge without a rotation fixes nothing.

Full doctrine: `port-shipping/reference/publish-audit.md`.

## The server can crash any player at will

Your client has, by construction, several server-controlled crash primitives
([reply-re.md](reply-re.md)): an empty body where a page builder needs content; a count beyond an
unclamped array; a short reply that causes an over-read; an over-long text into a fixed buffer.

Your encoders enforce all four in one place, which is correct — **and it means whoever controls
the server can crash or corrupt any player at will**. Consequences to write down:

* A **compromised server is a remote-code-corruption vector against every player**, not merely a
  data-loss event.
* The **reply-table catch-all** hijacks every connected user's replies. Off by default; consider
  removing the catch-all from the production code path entirely and keeping it in a development
  build only.
* Anyone you give shell access to has this capability. Say so before you add a co-admin.

## Denial of service

* **Admission control runs after the TLS handshake.** asyncio completes the handshake before
  invoking the connection callback, so a per-IP session cap only limits *established* sessions. On
  a small VPS an RSA-2048 handshake flood is the cheapest available DoS. Mitigate with an
  accept-level token bucket in front of the TLS wrapper, or terminate TLS behind something that
  has one.
* **Blocking database commits on the event loop** turn one abusive client into a stall for
  everyone. Move commits to an executor or a writer task.
* **Unbounded per-account rows** make an upload loop a write amplifier on a shared database. Cap
  them, and allow *replace* at the cap.

## Pin distribution and rotation

* The pin is baked into an artifact handed out by hand. **There is no revocation and no rotation
  channel.** If the server key leaks, every user must reinstall.
* **Bind the pin to `(host, port)`.** A global pin means a trust decision made for one server is
  silently applied to another, and changing the address in settings produces an unexplained
  connect failure.
* Ship **pin-set** support (accept old and new) *before* you ever need to rotate.

Certificate mechanics and the path tradeoffs are in [deploy.md](deploy.md).

## The account code is a recovery phrase

If your app shows the user a transfer code so they can move their account to a new device, that
code **is the password**. Treat it accordingly:

* warn on display;
* do not auto-copy it into a shared clipboard history;
* discourage screenshots in the dialog text — screenshots and chat pastes are the realistic leak;
* offer rotation once challenge-response exists.

## Publish-readiness checklist

Copy this into the repository and tick it before the first public link.

- [ ] No database, WAL, transcript, save blob or archive anywhere in **history, all branches, all
      reflogs** — not just the working tree. Verified with `publish_audit.py --all-refs`.
- [ ] Default state and log paths are **outside** the source tree.
- [ ] Transcript OFF by default; masking is a **field allowlist**; retention and rotation defined;
      peer addresses not logged.
- [ ] Store keys on a **hash** of the credential (or a verifier), never the credential.
- [ ] Auth model stated in **one sentence at the top of the README**, with the exploit script next
      to it.
- [ ] Cheat surface listed explicitly (which operations the server approves without checking).
- [ ] **Deploy docs and code defaults agree on every destructive flag.**
- [ ] TLS required for any non-loopback address; cleartext fails loudly rather than silently.
- [ ] Rate limiting exists **in front of** the TLS handshake, not only after it.
- [ ] Pin is bound to `(host, port)`; certificate rotation has a written plan.
- [ ] Reply table and any debug flag are OFF by default, and the help text says why.
- [ ] No game content, no extracted decryption keys, no real server address, no keystore, no
      device serial, no personal absolute paths anywhere in the published tree.
