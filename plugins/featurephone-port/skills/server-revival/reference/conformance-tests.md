# The conformance suite

**The oracle is expensive.** The real client is the only authority on whether a reply is correct,
and consulting it costs minutes plus a physical device. The conformance suite is a **fast proxy
for "this will not crash the guest"**, so that device time is spent on genuinely new questions
instead of re-checking things you already knew.

It is also where the protocol RE is *written down in an executable form*. Every byte-layout
assertion is one RE claim; when a document and the code disagree, the suite says which is wrong.

## Contents

- [Shape: no framework, two files](#shape-no-framework-two-files)
- [The five assertion classes](#the-five-assertion-classes)
  - [1. Encoder byte-layout](#1-encoder-byte-layout)
  - [2. State-machine transcript replay](#2-state-machine-transcript-replay)
  - [3. Guest-safety invariants](#3-guest-safety-invariants)
  - [4. Client-model simulation](#4-client-model-simulation)
  - [5. Abuse-case regression](#5-abuse-case-regression)
- [The end-to-end TLS test](#the-end-to-end-tls-test)
- [Exploit-script-as-test](#exploit-script-as-test)
- [Mechanizing class 2 from a transcript](#mechanizing-class-2-from-a-transcript)
- [Stream-chunking equivalence](#stream-chunking-equivalence)
- [★ Carrying the suite across a reimplementation](#-carrying-the-suite-across-a-reimplementation)
- [Cross-language generated tables](#cross-language-generated-tables)
- [Honest defects of this pattern](#honest-defects-of-this-pattern)
- [What to run, and when](#what-to-run-and-when)

---

## Shape: no framework, two files

```python
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)

# ... assertions ...

print("ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
return 1 if fails else 0
```

Why deliberately no test framework:

* **It runs anywhere**, including on the deploy box, with nothing installed. Standard library
  plus `tempfile` for a scratch database.
* **The pass/fail line is greppable** from CI or a shell one-liner.
* **The suite doubles as documentation.** Reading it top to bottom is the fastest way to learn the
  protocol, because each assertion is a byte string next to a sentence.

Two files: the unit suite (no network at all) and an end-to-end TLS suite (needs `openssl` on
`PATH`; probe the common install locations explicitly rather than failing with "not found").

⚠ `check()` on a **raised exception** aborts the whole run instead of failing one assertion. Wrap
individually risky blocks, or accept it knowingly.

## The five assertion classes

This is the recipe. Every protocol you revive gets all five.

### 1. Encoder byte-layout

**Each assertion is one RE claim written as bytes.**

```python
check(P.enc_currency(0x11223344) == b"\x05\x14\x44\x33\x22\x11", "enc_currency u32 LE")
check(lst[:5]     == b"\x05\x03\x10\x02\x00", "enc_list header: op, capacity, u16 count")
check(refresh[:6] == b"\x05\x3d\x01\x10\x01\x00", "enc_list_refresh header")
```

Rules:

* Assert **the exact bytes**, not a decoded structure. The decoded structure is your belief; the
  bytes are the claim.
* Assert the **header slice** even for long replies, so a length or an endianness regression is
  caught without pinning the whole payload.
* Put the RE provenance in the message or a comment: the workflow id, or the device date that
  established it. When it is later refuted, the comment is where the correction goes.
* **★ For a *bound*, assert the client's constraint — never the bytes you currently emit.** The
  rules above are about *layout*. A bound is a different kind of claim and needs the opposite
  treatment; see the correction immediately below.

> **REFUTED.** *Previously implied by the rules above, and shipped:* "assert the exact bytes"
> applies to every assertion in class 1, bounds included — so a capacity limit is pinned by asserting
> the exact byte the encoder produces at the limit.
> **Refuted by:** a shipped signedness bug. A green test named for a capacity *"still fitting in one
> byte"* asserted precisely the value that **overflowed the client's signed read**, and the
> post-mortem names that test as the reason the bug shipped: it converted the encoder's current
> behaviour into the specification, so the encoder and its test were wrong together and agreed.
> **Why it was attractive:** for layout the rule is exactly right, and byte assertions are the whole
> reason this suite works. The failure is applying a *description* rule to a *constraint*.
> **Correction:** phrase a bound as the property the client imposes — `enc_list(...)` caps `N` at the
> client's array size; every host-chosen quantity round-trips through the client's own signed read
> unchanged — so that a wrong constant **fails** rather than being enshrined. Detail:
> [client-fidelity.md](client-fidelity.md) §15, and the signedness section of
> [reply-re.md](reply-re.md).

### 2. State-machine transcript replay

**Replay the exact frames the device sent; assert the exact reply list.**

```python
check(h.handle(bytes([0x01, 0x01, 0x02])) == [b"\x01\x01"], "handshake -> ack")
check(h.handle(acct_frame)                == [b"\x01\x3d"], "login -> ack")
check(h.handle(bytes([0x01, 0x3E]))       == [NOTICE],      "sub-request -> notice")
check(h.state.wait_cleared,                                 "WAIT cleared")
```

This class only exists because `handle()` is `bytes -> list[bytes]`
([server-architecture.md](server-architecture.md)). Assert the **whole list**, including its
length — that is what pins reply pacing ([reply-re.md](reply-re.md)).

Assert **state** as well as bytes. `wait_cleared` is the property that actually matters to a
player; the byte string is only how it is achieved.

### 3. Guest-safety invariants

**The bounds the *client* fails to enforce.** These are the assertions that keep a refactor from
crashing a player's phone, and they should be the ones you are most reluctant to delete.

```python
big = P.enc_list([rec] * 30)
check(int.from_bytes(big[3:5], "little") == 16, "enc_list caps N at the client's array size")

check(len(P.enc_result_text("x" * 4000)) - HDR <= TEXT_MAX, "text capped to the destination")
check(P.enc_result_text(long_mb_text).decode(ENC, "strict"), "truncation never splits a character")
check(P.enc_notice(b"") != EMPTY_BODY, "notice body is never empty")
```

One assertion per crash primitive found in [reply-re.md](reply-re.md). Name the client behaviour
in the message so the reader knows it is not a style preference.

### 4. Client-model simulation

**Unit-test a model of the guest, not just your encoder.** This is the class people skip. It catches
the windowing and pagination bugs you would otherwise find on a device — and, far more importantly,
it is the only thing standing between a host-chosen quantity and **silent destruction of a player's
property**. Read it in that order of priority: pagination is the tutorial case, the destructive case
is why the class exists.

Reimplement the client's cursor machine as you RE'd it, drive the **real** handler through it, and
assert properties rather than values:

```python
# model of the client's paginator, as read from its handler
anchor = min(A, N)
while anchor + 1 < B:
    page = drive(handler, cursor=anchor)
    seen |= set(page.items)
    check(page.count <= PAGE_MAX, "every page respects the client's array bound")
    anchor = advance(anchor, page)
    steps += 1
    check(steps < HARD_LIMIT, "the scroll terminates")
check(seen == set(catalog), "every catalogue entry is reachable")
```

Three properties worth asserting for any paginated reply: **reachability** (nothing is
unreachable), **bound** (no page exceeds the client's array), **termination** (the scroll ends).
That is how you validate windowing arithmetic against a client you cannot run.

#### ★ The destructive case: model the client's *apply* procedure

**(A)** Wherever the client applies a host-chosen quantity through a **lossy procedure of its own** —
a grant routine, a placement scan, an inventory merge — the model must reproduce that procedure
*exactly*, including **how many times the client retries** and **whether it discards the retry's
return value**. The assertion to write is not about layout at all:

> **the host never authorises a quantity the client would partially destroy.**

Three properties to add on top of the paginated three:

| property | assert |
|---|---|
| **loss-freedom** | what the model actually *delivers* equals what the host authorised — not "it did not raise" |
| **sequence-correctness** | several operations in a row are simulated against the **mutated** state, not each against the original snapshot |
| **exhaustive sweep** | every point of the legal input space is checked, not a hand-picked sample. These input spaces are small; there is no excuse for sampling |

**(B)** In the reference port this class caught what a "total free space" model authorises: the
client's grant routine makes exactly **two** placements and **discards the second return value**, and
its scanner exhausts every mergeable partial stack across all containers before it looks at a free
cell. So an inventory holding two half-full stacks destroys units of a large delivery **even with ten
empty cells**. Assertions named for the counterexample — *two partial stacks beat ten free cells*, *a
multi-kind delivery shares the free cells*, *a withdraw into a full bag is refused, not swallowed*,
*the deposit floor protects the remainder* — pin it. Three separate features in that title were
destroying items through this same routine at the same time, and none of them logged anything.

The client-side derivation is [client-fidelity.md](client-fidelity.md); this class is where it is
kept honest.

### 5. Abuse-case regression

**One named test per hole you closed**, and the comment states *what was possible before*.

```python
# Block 3 — a session that never logged in must get nothing back.
#   Previously: state.authed was set but never read, so every account-scoped
#   op answered before login.
for req in ACCOUNT_SCOPED_REQUESTS:
    check(fresh_handler().handle(req) == [], f"no-login refusal: {req[:2].hex()}")
```

The reference suite has nine such blocks: no-login refusal, session re-binding refusal, store
refusing an empty identity, account isolation, wire-byte range checks, per-account row cap,
transcript masking, admission control, and the default-listener count. Each maps to a finding in
[threat-model.md](threat-model.md).

## The end-to-end TLS test

Covers exactly what unit tests cannot. Five properties, and all five have caught real bugs:

1. **A trusting client completes the full protocol over TLS.** Sanity.
2. **★ Two frames written in one `sendall`, coalesced into one TLS record, are split into two
   replies.** The real client does this. A unit test with a byte string cannot exercise a real TLS
   stack's record boundaries.
3. **Heartbeats interleaved with requests cause no desync.**
4. **An untrusted client is refused** — i.e. your test client is not silently accepting any
   certificate, which would make property 1 meaningless.
5. **★ A plaintext client gets no protocol reply from the TLS port.** Enabling TLS must genuinely
   *close* the cleartext path, so a stale build cannot keep leaking credentials in the clear.

**The test doubles as the operator tutorial.** Its `make_cert()` helper generates the throwaway
self-signed certificate and prints `SHA-256(DER)` with the line "this is the value you put in your
pin configuration" — so the pin-provisioning procedure in [deploy.md](deploy.md) is *executed* by
the test suite rather than described in prose that can drift.

## Exploit-script-as-test

**A separate exploit script is a test too**, and it is the most honest documentation a threat
model can have.

Shape: start a real server on a scratch database, plant a victim account, then connect with a
plain socket — no phone, no app, no emulator — and perform the takeover using only the credential.
**Exit 0 only if the takeover succeeds.**

Two payoffs:

* **It documents the threat model by executing it.** Nobody argues with a script that prints
  another account's inventory.
* **After a mitigation lands it becomes a regression check**: invert the expectation, and it now
  proves that no *other* path reopened the hole.

Keep it in the repository, keep it pointed at a scratch database only, and reference it from the
README next to the one-sentence auth statement ([threat-model.md](threat-model.md)).

## Mechanizing class 2 from a transcript

```
python "${CLAUDE_PLUGIN_ROOT}/tools/replay_transcript.py" transcript.jsonl \
  --handler mypkg.proto:ProtocolHandler --emit-test out.py
```

Replays the `rx` frames of a captured session through your handler and emits a class-2 test
skeleton with the observed replies as expectations. Review it before committing — the observed
replies are *your server's* past behaviour, which is only the right expectation for the parts a
device has confirmed. Mark the rest ⚠.

## Stream-chunking equivalence

**(A)** One assertion, worth writing on the first day and never touching again:

> **the same requests, delivered one byte at a time, produce identical replies.**

Feed a captured session through the decoder in chunks of 1, then in chunks of 7, then all at once,
and compare the reply lists. It costs a loop and it covers the entire class of accumulate-and-
dispatch bugs that otherwise appear only on a congested mobile link: a length word read across a
chunk boundary, a partial frame dropped when a fresh buffer is allocated, a handler that reads past
the bytes it has.

It is also the property that lets you **retire the wire**. An in-process engine and a socket both
present the client with a byte stream, so an engine that passes chunking equivalence answers
identically however the transport happens to slice it — which is one of the two independent
derivations that the client cannot tell the difference ([in-process-engine.md](in-process-engine.md)).

## ★ Carrying the suite across a reimplementation

**(A) The suite is the only thing that survives a rewrite.** When the protocol layer is reimplemented
— a different language, a different process, an in-process engine — **port the comments, not only the
code.** Every device-learned constraint that is not restated as an assertion in the new
implementation's own suite is **silently lost**, and it is lost in the most expensive way: the
knowledge cost device sessions to acquire and its absence is invisible until a player finds it again.

Concretely, do these three things in the same commit as the port:

1. **Enumerate the constraints** in the new module's header, in prose, before writing a line of it.
   **(B)** the reference port's engine header lists five: never pad a list reply · both list-header
   bytes are read signed · the cursor must be stateless · an empty notice body crashes the client · a
   failure ack must carry its text in the same frame.
2. **Restate each one as an assertion** in the new suite, with the *original* finding's date and
   symptom in the message. A test whose message says only `enc_notice non-empty` has lost half of
   what it knows.
3. **Say so at the top of the new suite**, e.g. *"every assertion below that names a session is a
   device-measured constraint a fresh implementation would otherwise lose"* — so nobody deletes one
   as redundant.

⚠ Expect to lose something anyway. The reference port dropped a dirty-check fast path during exactly
this move and only noticed when page turns started stuttering. A diff of the two suites' assertion
*messages* is a cheap check that nothing walked off.

## Cross-language generated tables

**(A)** When two implementations must agree on a table — a catalogue, an id map, a tier list —
**generate it from the single authority** rather than hand-copying, and ship a **`--check` mode that
regenerates in memory and compares**, wired into CI. A hand-copied table's failure mode is not a
crash: it is a player buying something the authority delisted, or an identifier that resolves to a
different thing on the other side. Neither looks like a stale table.

Two details that make the pattern work:

* **Generate from the authority's own objects**, not from its source text. **(B)** the reference
  catalogue is not a constant at all — it is a computed product of several tier tables, content flags
  and a delist set, each carrying a paragraph of device-measured reasoning. Nothing that reads like a
  table can be copied faithfully by hand.
* **Emit the generated file so your selftest can include it** (outer-style comments only, if your
  language's inclusion mechanism is fussy), so the generated copy is under test rather than beside
  it. See `${CLAUDE_PLUGIN_ROOT}/shared/selftest-convention.md`.

## Honest defects of this pattern

Carried so a reader does not inherit them silently:

* **Shared mutable state.** A single-`main()` suite with one store and one handler means blocks
  must claim **disjoint identity ranges**, enforced by hand-written comments, and **ordering is
  load-bearing**. It works up to a few hundred assertions. At the next size increase, split into
  functions with a fresh store each — do it before the first mysterious ordering failure, not
  after.
* **An exception aborts the run**, rather than failing one assertion.
* **No end-to-end frame-size assertion.** The frame encoder enforces the cap, but unless a
  round-trip block exercises every reply *through* the encoder, an over-long reply is only caught
  at runtime. Add one loop that encodes every `enc_*` output at its maximum arguments and asserts
  the resulting frame fits.
* **Test counts drift into prose.** The reference port's documents cited a count three milestones
  stale. Make the command the source of truth; do not hand-copy the number into docs.

## What to run, and when

| when | run |
|---|---|
| every edit to the protocol layer | the unit suite (≈1 s) |
| before any device session | the unit suite — device time is the scarce resource |
| before enabling or changing TLS | the e2e suite |
| after any change to identity, sessions or the store | the unit suite **and** the exploit script |
| in CI | both, plus a lint that fails on a committed database, transcript or archive blob |
| in CI, if a table is generated | the generator's `--check` mode — a stale copy must fail the build, not ship |
| after reimplementing the protocol layer anywhere | the **new** suite, and a diff of assertion messages against the old one |
