# Reverse-engineering the replies

Requests you can catalogue statically ([request-discovery.md](request-discovery.md)). Replies you
cannot: nothing in the binary *is* a reply. What exists is the code that **consumes** one, and
that code is a complete specification of the layout — including several bounds the client fails
to enforce on itself, which therefore become **your** obligations.

This is where a mistake stops being a bug in your server and becomes a crash on a player's phone.

## Contents

- [Enumerate the dispatcher first](#enumerate-the-dispatcher-first)
- [Reply parsers are fixed-offset inline reads](#reply-parsers-are-fixed-offset-inline-reads)
- [★ Find the blocking-state variable and the ONE reply that clears it](#-find-the-blocking-state-variable-and-the-one-reply-that-clears-it)
- [Toast vs finalize: dismissing a modal is not advancing a state machine](#toast-vs-finalize)
- [★ Unclamped client arrays: the bound is a server obligation](#-unclamped-client-arrays-the-bound-is-a-server-obligation)
- [★ Unbounded text destinations and their real capacity](#-unbounded-text-destinations-and-their-real-capacity)
- [Empty bodies are not safe](#empty-bodies-are-not-safe)
- [Reply pacing](#reply-pacing)
- [Encoder design rules](#encoder-design-rules)
- [Worked reply map (B)](#worked-reply-map-b)
- [Failure table](#failure-table)

---

## Enumerate the dispatcher first

**(A)** Find the function the receive pump calls with a completed frame. It will be a `switch` on
the category byte. **Enumerate every case.** Anything outside that set is unreachable: sending
category `0x77` when the switch has no `0x77` case is not "an unimplemented feature", it is a
frame the client discards, and no amount of body tweaking will change that.

Then, per category, find how the opcode is dispatched. Common shapes:

* a nested `switch` — read the cases off the disassembly;
* **a jump table indexed by `opcode - K`, with a bound check**. Read both `K` and the bound. That
  gives you the *exact* legal opcode range for the category, which is far better information than
  a list of the opcodes you happen to have seen. **(B)** one category dispatches through a jump
  table indexed `opcode - 3` with a bound of `0x3f`.

`xref.py fnptr` / `dump_table.py` classify a jump table's entries so you can label each arm's
handler function before reading any of them.

**Cross-check against Phase 2's leading-byte histogram.** Two independent derivations of the
category set is the standard from `${CLAUDE_PLUGIN_ROOT}/shared/evidence-standard.md`.

## Reply parsers are fixed-offset inline reads

**(A)** Clients of this era do not have cursor-based parsers. A handler reads
`payload[2]`, `payload[3]`, `payload[4..8]` directly, with the offsets baked into the
instructions. That is a gift: **the layout is literally visible in the disassembly**, one
`ldrb rX, [rY, #imm]` at a time.

Reading a handler:

1. Note the base register — it will be the payload pointer, usually offset by 2 to skip the
   category/opcode.
2. Every `ldrb #imm` is a `u8` field at `body[imm]`. Every `ldrh`/`ldr` is 16/32 bits — and go
   back to [framing.md](framing.md)'s endianness warning before you assume the order.
3. Loops with an incrementing index are your repeated records; the increment is the **record
   stride**, and the loop bound is where the next two sections start.
4. `memcpy`-shaped calls with a constant length are fixed-size blobs. Write down the length.

Write each field into the encoder as you read it, and write the corresponding byte-layout
assertion into the conformance suite at the same time
([conformance-tests.md](conformance-tests.md), assertion class 1). **Each such assertion is one RE
claim written as bytes**, so when the doc and the code later disagree, the test says which.

## ★ Find the blocking-state variable and the ONE reply that clears it

**This is the single highest-value find of the whole phase, and you should go after it before any
feature work.**

When the client sends a request it sets a state variable and shows a modal. Until that variable is
cleared, **the client is unobservable**: every reply you try lands on a UI that cannot react, so
every experiment gets misattributed and you conclude that half your correct replies are wrong.

**How to find it (A):**

1. Take the waiting-for-server string from the feature inventory.
2. Chain it to the code that displays it ([request-discovery.md](request-discovery.md)'s hop
   chain, or `xref.py pcrel`).
3. Find the *variable* whose value gates that display — it will be a field of the network object
   or a static, compared against a small constant.
4. `xref.py offset <imm>` for **writes** to that field. Enumerate every writer.
5. Exactly one (or very few) of those writers is reachable from a **reply handler**. That is your
   clearing reply.

**(B)** the wait flag is a field at network-object + `0x9c`, value `3` while the modal is up, and
**only one category/opcode pair** calls the session-begin function that clears it.

Write it at the top of your protocol notes. Everything else depends on it.

## Toast vs finalize

**★ A reply that dismisses the modal is not necessarily a reply that advances the state machine.**

**(B)** In the reference title, several plausible acknowledgements — a notification-category
message, a commit acknowledgement — *do* dismiss the modal. The player sees the operation
"succeed". But a **second** state field (network object + `0x38`) is left untouched, the session
never finalizes, and the client then **cleanly closes the socket**. Symptom on the server side: a
successful-looking exchange followed by a graceful FIN, every time, with nothing in the log that
looks wrong.

So when you are hunting the clearing reply, look for **two** state fields, not one:

* the one that controls the modal (what the player sees), and
* the one that means "the session is established" (what the client's next action depends on).

A reply that moves only the first is a **toast**. Useful, but not an ack.

Diagnostic: if your client keeps closing the connection right after a successful operation, you
are sending a toast where a finalize is required. That is symptom row 4 in the SKILL.md table.

## ★ Unclamped client arrays: the bound is a server obligation

**(A)** Find every fixed-size array the client fills from your reply, and check whether the fill
loop clamps its count.

Procedure: in each list-handling reply handler, find the loop's destination base and stride, then
find the *allocation* of that destination. If the loop bound comes from the wire and there is no
`cmp` against a capacity, **you have found a remote memory-corruption primitive that you control**.

**(B)** one list handler stores into a fixed 16-entry array of 20-byte records plus a parallel
16-byte flag array, **with no clamp**. `N > 16` writes straight past both, into the next fields of
the enclosing object. The original server never sent more than 16, so it never fired.

**The rule: when the client does not clamp, the bound is a SERVER obligation, and it is enforced
in the encoder, exactly once, not at call sites.**

```python
WH_PAGE = 16          # (B) derived from the client's array, not chosen

def _rows(records):
    if len(records) > WH_PAGE:
        records = records[:WH_PAGE]      # or raise, if silent truncation would confuse
    ...
```

Then assert it:

```python
big = enc_list([rec] * 30)
assert int.from_bytes(big[3:5], "little") == 16   # encoder caps N
```

This is assertion class 3 in [conformance-tests.md](conformance-tests.md), and it is the class
that keeps a future refactor from crashing a player's phone.

**★ Corollary — a client array bound is not a feature bound.** The reference port's own notes once
read "the 16-slot array means storage expansion is impossible". Wrong: 16 is **one page**. The
actual capacity is a byte *the server writes* into the list header, and the client pages through.
Before you conclude a fixed array limits the game, check whether the header carries a total that
the client is willing to believe.

## ★ Unbounded text destinations and their real capacity

**(A)** Any reply carrying text ends up in a `sprintf`/`strcpy`/`memcpy` destination. Find the
destination's **real** capacity — not the field's nominal size:

* the allocation size, minus the offset the text sits at;
* **minus anything else inside the same allocation that is cleared or written as a unit**. A block
  `memset` over `obj[0 .. 0x100]` tells you the usable region ends there even if the object is
  bigger.

**(B)** an operation-result text is copied into `obj+0x10` inside a block that is memset as a unit
⇒ usable 252 bytes ⇒ the encoder enforces a 251-byte maximum, leaving room for the terminator.

**★ Multibyte truncation is part of the bound.** For a legacy multi-byte encoding (EUC-KR, Shift
JIS, GBK — see `guest-re/reference/asset-decoding.md`), truncating at a byte count can end the
string **on the first byte of a two-byte character**. The client's renderer then reads the
terminator as the second byte and either draws garbage or walks off the end. Your truncation must
be encoding-aware:

```python
def _truncate_mb(text_bytes, limit, encoding):
    if len(text_bytes) <= limit:
        return text_bytes
    cut = text_bytes[:limit]
    while cut:
        try:
            cut.decode(encoding)
            return cut
        except UnicodeDecodeError:
            cut = cut[:-1]          # never end on half a character
    return b""
```

## Empty bodies are not safe

**(B) Refuted claim, carried as a correction:** the reference port's protocol notes stated that
the minimum clean reply for the login notice was an **empty** body. On the device that reply
**crashed the client** — the paged text-box builder walks a page list that an empty body leaves
null, and the guest faults with `Invalid memory access; address: 0`.

**(A) Generalize it: "the smallest legal frame" and "the smallest frame this handler tolerates"
are different numbers.** A handler that builds UI from your text needs non-empty text. Derive the
minimum from the *handler*, and put it in the encoder:

```python
if not body_text:
    body_text = " ".encode(ENCODING)     # never emit an empty notice body
```

Symptom mapping: **a crash at `address: 0` immediately after your reply is almost always an empty
body where the handler requires content.**

## Reply pacing

Recap of [framing.md](framing.md), because it is a reply-side constraint: the guest's
completed-frame queue is a tiny fixed ring (**(B)** four slots), and the pump may allocate a fresh
receive buffer after dispatching one frame — discarding bytes of a second frame that arrived in
the same read.

**Answer at request-response pace.** Where a handler must emit two frames back to back, you are
relying on them landing in separate TCP segments. It works in practice; it is fragile; pin the
reply-list length with a conformance assertion and put a comment next to the handler saying why.

## Encoder design rules

1. **One pure function per reply**, `enc_<name>(args) -> bytes`, no I/O, no state. Purity is what
   makes assertion class 1 possible.
2. **All client-derived bounds live in the encoder.** List caps, text caps, minimum bodies. Never
   at call sites; call sites get forgotten during refactors.
3. **Raise, do not truncate, when a bound is a correctness bound** (frame cap). **Truncate, and
   say so, when it is a display bound** (text length, page size).
4. **Return `bytes`, let the transport frame it.** The encoder must not know the header format.
5. **When you refuse a request, prefer silence over a malformed reply.** The guest treats "no
   reply" as an incomplete request and eventually times out cleanly; every crash in the refutation
   log came from a *malformed* reply. Silence is the safe failure mode.

## Worked reply map (B)

Shown to make the shapes concrete. **Every id here is title-specific**; the value is in the
*pattern* of each row — what kind of thing a reply is.

| feature | C→S | S→C | note |
|---|---|---|---|
| handshake | `01 01 02` | `01 01` | the only exchange before identity |
| login | `01 3d <16B id><20B svc><u32 seq>00` | `01 3d` | the `seq` field is parsed by nobody — see [threat-model.md](threat-model.md) |
| **finalize (clears WAIT)** | any of three sub-requests | `01 3e 00 00 <text>00` | **all three finalize identically**; body must be non-empty |
| heartbeat | `00 0a` every ~5 s | *(silence)* | its gaps set your idle timeout |
| save upload | `04 3d <slot><blob>` | `04 3d <slot><status><resv><text>00` | commit-ack class, **not** toast class |
| save restore | `04 3e <slot>` | `04 3e <slot><blob verbatim>` | destructive — see [deploy.md](deploy.md) on flag defaults |
| currency | `05 14` | `05 14 <u32 LE>` | |
| list (open) | `05 03` | `05 03 <cap u8><u16 N><N×21B>` | **N capped at 16 by the encoder** |
| list (refresh) | `05 3d <slot>` | `05 3d <used><cap><u16 N><rows>` | short reply here causes an over-read |
| deposit / withdraw / discard | `05 41` / `05 04` / `05 05` | `05 41 <status>` / `05 04 <u16><u8><20B>` / `05 05 <status>` | |
| paginated catalogue | `05 3f <12B, cursor at body[4:8]>` | `05 3f <u32 A><u32 B><u16 N><rows>` | the client's cursor machine is simulated in assertion class 4 |
| purchase → commit | `05 42 …` then `05 40 …` | `05 42 01` then **echo of the request** | an echo is a reflection primitive; see [threat-model.md](threat-model.md) |
| combine | `05 06 <u64><u64>` | `05 06 00 00 <text>00` | text bound 251 bytes, multibyte-safe |
| toast / message box | — | `1f 00 …` (transient) / `64 <op><len><text>` (persistent) | **toast ≠ ack** |

Two structural observations that *do* generalize:

* **The item database is not in the code binary.** Names, effects and prices live in archive
  resources; the wire carries only `(category, index)` and the client resolves the rest itself.
  So a "shop catalogue" is a list of small records you *synthesize* — **you do not invent items,
  you re-list the ones the game already ships**. (Encrypted resource members are out of scope for
  this plugin; if a member is encrypted, that is yours to handle privately.)
* **Server-side features can exist that the original protocol never named.** Capacity in the
  reference title is a byte the *server* writes into the list header, so "buy more storage" is
  implementable with no client change at all. Look for every field of your replies that the client
  merely *believes*; each one is a feature you can add.

## Failure table

| symptom | cause |
|---|---|
| permanent waiting-for-server modal | you replied, but not with the one op that clears the wait flag |
| modal clears, then the client closes the socket | toast-class reply where a finalize was required; a second state field is untouched |
| crash at `address: 0` right after your reply | empty body where a text/page builder requires content |
| corruption, then a crash minutes later | `N` exceeded a client array that has no clamp |
| garbled or truncated text, sometimes a crash | text exceeded the destination's real capacity, or was cut mid multi-byte character |
| client reads fields you never sent | reply shorter than the handler's fixed-offset reads; pad to the handler's expectation |
| your reply is discarded with no visible effect | category not in the dispatcher's switch, or opcode outside the jump table's bound |
| two replies sent together, only the first acted on | reply pacing; the completed-frame ring is tiny |
