# The frame layer

A framing is three facts: where a message begins, how long it is, and what the length counts.
Getting the third one wrong produces a decoder that works on small messages and desyncs on the
first large one, days later, in a way that looks like a server bug.

**Rule: do not accept a framing until two independent derivations agree. Get three.**

## Contents

- [The three derivations, and what each one alone cannot tell you](#the-three-derivations)
- [Worked example (B)](#worked-example-b)
- [Three more things to extract in this phase](#three-more-things-to-extract-in-this-phase)
- [Write an incremental decoder that mirrors the guest](#write-an-incremental-decoder-that-mirrors-the-guest)
- [Desync policy](#desync-policy)
- [Coalescing: two frames, one read](#coalescing-two-frames-one-read)
- [The completed-frame ring, and why you answer at request-response pace](#the-completed-frame-ring)
- [Encoder obligations](#encoder-obligations)
- [Failure table](#failure-table)

---

## The three derivations

**(A)**

| derivation | what you get | what it cannot tell you |
|---|---|---|
| **Receive pump disassembly** — the loop that accumulates bytes, reads a length, decides a message is complete, and dispatches | the header's position and width, the completeness test, the minimum-length guard | whether the length is header-inclusive, if the pump's arithmetic is ambiguous |
| **Captured client bytes** — from the silent baseline of Phase 0 | ground truth for the messages the client actually sends at startup | nothing about messages you have not triggered; nothing about *your* reply direction |
| **The send-side serializer** — the `flush()` routine that writes the length word and resets the cursor | ★ whether the length is total or body-only, and where the header lives relative to the body | nothing about the receive direction, if the two differ (check!) |

The third is the decisive one and is the one people skip. **(B)** the reference title's flush
writes `*(u32*)(txbuf + 0xc) = len` and then resets `len = 4` after every send. Two deductions
fall out immediately: the header lives *inside the same buffer as the body* (so it is contiguous
on the wire), and the counter is reset to 4, i.e. to the header size — therefore the length word
is **total, header-inclusive**. Neither of the other two derivations settles that.

**Method to find the serializer:** look for a small function that is `BL`-ed from dozens of sites
and whose callees are byte/word writers into a shared buffer. Its callers are your request sites,
which is exactly what Phase 2 needs — so this work is not spent twice. See
[request-discovery.md](request-discovery.md).

## Worked example (B)

```
[u32 LE total_len (header-inclusive)] [u8 category] [u8 opcode] [body …]
```

* smallest legal frame the dispatcher will act on: **6 bytes** (`total_len = 6`, category, opcode,
  empty body) — because its guard is `total_len > 5`
* hard cap: **1016 bytes**, derived from the guest's reassembly buffer, enforced by the encoder,
  which *refuses* to build a larger frame rather than truncating
* observed client frames from the silent baseline: `07 00 00 00 | 01 01 02` and
  `06 00 00 00 | 00 0a`

Note the second one is the heartbeat and the first is the handshake — you get both for free from
a mute server, and they are your first two self-validation targets in Phase 2.

**These numbers are (B).** Your title's header may be big-endian, may be 2 bytes, may count only
the body, may carry a magic or a checksum. The *procedure* is what transfers.

## Three more things to extract in this phase

All three bite later, and all three are cheap now.

### 1. The reassembly buffer size is your hard frame cap

Find the allocation the receive pump fills. Its usable size — **after** subtracting any object
header the wire bytes sit behind — is the largest frame the client can survive. Put that number
in your encoder as a constant and make the encoder **raise** on overflow. Do not let call sites
decide; they will forget.

Cross-check it against the `recv` clamp from [socket-layer.md](socket-layer.md): the guest may
*ask* for more than its own buffer holds. The two constants are related and you should derive
both from the same allocation, once.

### 2. Both LE and BE integer writers may exist in the same image

**(B)** the reference title has a little-endian u32 writer at one address and a big-endian one at
another. The frame header uses LE. **Assume nothing about payload fields** — every multi-byte
field is a separate question, answered by which writer the send site calls and by which read
idiom the reply handler uses. A payload field whose endianness you guessed will produce values
that look almost right (small numbers work, large ones do not).

Record the two writer addresses; `extract_packets.py` needs both to template correctly.

### 3. The dispatcher's minimum-length guard

**(B)** `cmp #5; bgt` ⇒ any frame with `total_len ≤ 5` is **silently dropped**. Not rejected, not
logged — dropped. If your first reply is short you will conclude the client ignores you.

Find the guard, write the number down, and add a conformance assertion that every encoder's
output exceeds it.

## Write an incremental decoder that mirrors the guest

**(A)** Do not write a decoder that reads a length and then blocks for that many bytes. Write the
same accumulate-then-dispatch state machine the guest uses, so **your message boundaries are the
game's message boundaries**:

```python
class FrameDecoder:
    def __init__(self, max_frame):
        self.buf = bytearray()
        self.max_frame = max_frame

    def feed(self, chunk) -> list[bytes]:
        """Returns zero or more complete payloads. Never raises on partial input."""
        self.buf += chunk
        out = []
        while len(self.buf) >= HEADER_LEN:
            total = int.from_bytes(self.buf[:HEADER_LEN], "little")
            if total < MIN_TOTAL or total > self.max_frame:
                raise Desync(total)              # see below — do not try to resync
            if len(self.buf) < total:
                break                            # incomplete; wait for more
            out.append(bytes(self.buf[HEADER_LEN:total]))
            del self.buf[:total]
        return out
```

Two properties this shape gives you for free:

* **It is a pure function of the byte stream**, so a captured transcript replays through it
  exactly. That is assertion class 2 in [conformance-tests.md](conformance-tests.md).
* **It handles the coalescing case correctly by construction** (below), which a
  read-length-then-block decoder does not.

Keep the codec **deliberately dumb**: it frames and unframes, it refuses oversized frames, and it
knows nothing about payloads. Every temptation to peek at the category from inside the decoder is
a layering violation that will make the protocol layer untestable.

## Desync policy

**(A)** When the length word is implausible — below the minimum, above the cap, or absurd — you
are not at a frame boundary. **Do not attempt to resync by scanning forward.** With a bare length
prefix and no magic there is nothing to scan *for*, and a resync heuristic will happily lock onto
a plausible-looking length inside a payload and stay wrong forever.

Instead: record a `desync` event in the transcript with the offending bytes, and **close the
connection**. The client reconnects; you get a clean stream and a logged incident. Silence about
a desync is the worst option — that is how you end up with a bug report of "it randomly stops
working" and no evidence.

## Coalescing: two frames, one read

**(A)** TCP does not preserve write boundaries and TLS does not preserve record boundaries as
message boundaries either. Two frames written by one `sendall` will frequently arrive in one
`read()` and, over TLS, inside **one record**.

Your decoder must produce two payloads from that single feed. Assert it:

```python
d = FrameDecoder(MAX_FRAME)
assert d.feed(frame_a + frame_b) == [payload_a, payload_b]
assert d.feed(frame_a[:3]) == []                       # partial header
assert d.feed(frame_a[3:] + frame_b[:2]) == [payload_a]  # split anywhere
```

The real client does coalesce. The e2e TLS test in [conformance-tests.md](conformance-tests.md)
exists largely to prove your server handles it under a real TLS stack, where a unit test with a
byte string cannot.

## The completed-frame ring

**(A)** Framing is not only about parsing. The **guest** has a queue of completed frames waiting
to be consumed by game code, and on these platforms it is tiny and fixed-size. Find it — it is
adjacent to the receive pump, typically a small ring of pointers in static memory with a peek and
a pop primitive.

**(B)** four slots, in a native ring in static memory, with `rx_peek` and `rx_pop` helpers.
Pushing more than four completed frames without consumption **overwrites entries**.

**★ Worse: the pump may allocate a fresh receive buffer after dispatching one frame**, discarding
any bytes of a *second* frame that arrived in the same read. The reference title does exactly
this; it is a live latent bug in the original client.

**⇒ Answer at request-response pace.** One request, one reply, then wait for the next request.
Where a handler genuinely must emit two frames, know that you are relying on them landing in
separate TCP segments — which works in practice today and is **fragile**. If you can restructure
so the client's next request pulls the second frame, do that instead. If you cannot, document the
dependency next to the handler, and add a conformance test that pins the reply-list length.

## Encoder obligations

By the end of this phase, the encoder — one function, one place — must:

1. Refuse to build a frame larger than the reassembly cap (raise, never truncate).
2. Refuse to build a frame at or below the dispatcher's minimum-length guard.
3. Write the header with the derived width and endianness, counting whatever the flush routine
   proved it counts.
4. Take `(category, opcode, body)` and nothing else. Bounds that belong to *specific* replies —
   list caps, text caps — belong in the per-message encoders described in
   [reply-re.md](reply-re.md), not here.

## Failure table

| symptom | cause |
|---|---|
| small replies work, larger ones corrupt or crash the guest | frame exceeded the reassembly cap; enforce it in the encoder |
| client silently ignores your reply | frame at or below the dispatcher's minimum-length guard |
| decoder works against your own server, desyncs against the real client | length is header-inclusive and you assumed body-only (or the reverse) |
| numbers in a payload are right when small, wrong when large | you guessed a field's endianness; the image has both writers |
| two replies sent together, only the first is acted on | the guest's completed-frame ring or its buffer-reallocation bug; answer at request-response pace |
| intermittent garbage after a burst | you resynced on a bad length instead of closing the connection |
| unit tests pass, TLS e2e fails | coalesced frames in one record; the decoder must be incremental |
