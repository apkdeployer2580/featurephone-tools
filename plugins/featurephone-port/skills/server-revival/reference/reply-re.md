# Reverse-engineering the replies

Requests you can catalogue statically ([request-discovery.md](request-discovery.md)). Replies you
cannot: nothing in the binary *is* a reply. What exists is the code that **consumes** one, and
that code is a complete specification of the layout — including several bounds the client fails
to enforce on itself, which therefore become **your** obligations.

This is where a mistake stops being a bug in your server and becomes a crash on a player's phone.

## Contents

- [Enumerate the dispatcher first](#enumerate-the-dispatcher-first)
- [Reply parsers are fixed-offset inline reads](#reply-parsers-are-fixed-offset-inline-reads)
- [★ Width, endianness and signedness](#-width-endianness-and-signedness)
- [★ Find the blocking-state variable and the ONE reply that clears it](#-find-the-blocking-state-variable-and-the-one-reply-that-clears-it)
- [Toast vs finalize: dismissing a modal is not advancing a state machine](#toast-vs-finalize)
- [★ A reply's semantics are the whole handler](#-a-replys-semantics-are-the-whole-handler)
- [★ Unclamped client arrays: the bound is a server obligation](#-unclamped-client-arrays-the-bound-is-a-server-obligation)
- [★ Never pad a short list](#-never-pad-a-short-list)
- [★ Unbounded text destinations and their real capacity](#-unbounded-text-destinations-and-their-real-capacity)
- [Empty bodies are not safe](#empty-bodies-are-not-safe)
- [Which byte does the client actually test?](#which-byte-does-the-client-actually-test)
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
   back to [framing.md](framing.md)'s endianness warning before you assume the order. **Then read
   the *signedness* of the load**, which is a third and separate question — see the next section.
   `ldrb` and `ldrsb` occupy the same row of your field table and mean opposite things at 128.
3. Loops with an incrementing index are your repeated records; the increment is the **record
   stride**, and the loop bound is where the next two sections start.
4. `memcpy`-shaped calls with a constant length are fixed-size blobs. Write down the length.

Write each field into the encoder as you read it, and write the corresponding byte-layout
assertion into the conformance suite at the same time
([conformance-tests.md](conformance-tests.md), assertion class 1). **Each such assertion is one RE
claim written as bytes**, so when the doc and the code later disagree, the test says which.

## ★ Width, endianness and signedness

**(A) Width and endianness are two thirds of the audit.** The third is **signedness**, and it bites
only on the quantities *the server chooses* — the numbers the original operator's server happened
never to send large. A capacity, a page count, a stack size, a currency byte: if the client reads it
as a signed char and you emit 200, the client sees −56.

The disassembly tells, in Thumb-1:

| you see | it means |
|---|---|
| `ldrb` / `ldrh` | unsigned; your full range is available |
| `ldrsb` / `ldrsh` | **signed**; your usable range is 0..127 (or 0..32767) |
| `lsls rX, rX, #24` then `asrs rX, rX, #24` | **signed**, assembled by hand — the idiom used when the value is already in a register. Easy to miss, because neither instruction mentions a load. |

**The rule: clamp to the client's signed range. Never mask.** `value & 0xFF` is *precisely* the
operation that keeps the sign bit and passes it through, so it reads like a bounds fix and is not
one.

**(B) How this was found, and what it cost.** Three sites in one reference title, all located by
re-reading each list handler for the *flavour* of the load rather than its offset — the offsets had
been right for months:

* **Two bytes of a list header** are read with `ldrsb`. A legitimate capacity of **128** arrived as
  **−128**, and *both* page-turn keys stopped responding at once, because both gates compare against
  that byte; the client's own cursor clamp then wrote −128 into the cursor. The bug shipped through
  an `& 0xFF` in the encoder and came back as a player report — *"the page keys disappeared"* —
  which reads like a UI bug and is a wire bug.
* **A stack-count byte** is sign-extended by the client's grant routine, so any value at or above
  128 **subtracts** from the player's stack instead of adding to it. Same class, worse outcome.

Confidence `HIGH / CONFIRMED`: the parser clamp and both page gates read off the disassembly, plus
the field report and its disappearance once the encoder clamped instead of masking.

The matching test discipline — assert the *client-imposed constraint*, never the byte your encoder
currently emits — is [conformance-tests.md](conformance-tests.md) class 1; the full treatment of
host-chosen quantities is [client-fidelity.md](client-fidelity.md) §11.

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

## ★ A reply's semantics are the whole handler

**(A) A reply class is not defined by what the player sees it do.** "Toast", "message box",
"commit-ack" name only the *visible* half. Before you adopt a reply class anywhere, read its handler
**to the end** and list every side effect: does it tear the connection down, does it call the game's
own grant routine, does it overwrite another modal, does it advance a state word?

**(B)** Two findings from the reference port, both expensive:

* The **failure-plus-text** reply — the obvious way to say no — has a category handler that calls the
  client's **network teardown** routine *before* it shows its box. That is correct for the sender
  who is finished with the connection and fatal for the sender who is still logging in. Symptom:
  everything succeeds, the screen opens, and then every later request waits forever. Handling —
  discriminate by session state and keep the two arms separate — is
  [refusals-and-holds.md](refusals-and-holds.md) §9–§10.
* A **synthetic reply** injected to release a stuck screen opens a *different* modal, and that modal
  overwrites the one whose OK handler would have let the player out.

### The stuck-screen escape, and how to find it

This is the discovery route for the modal that [refusals-and-holds.md](refusals-and-holds.md) §12
tells you to drive. It is reply-RE work even though no reply is involved, because it uses this
file's dispatcher-enumeration technique on a different dispatcher.

**(A)**

1. Find the **one word** the online module switches on for both painting and key handling. In a
   client of this era the online screens are a small state machine over a single static.
2. **Enumerate every site that loads it** — exhaustively, covering both Thumb idioms for
   materialising an address, or you will "prove" that the arm you need does not exist.
3. Read each arm's **key handler** for one that performs **both** the network teardown and the
   screen transition. That arm is your escape hatch: the game already ships the behaviour you were
   trying to synthesize on the wire.
4. Find the game's own **show-modal** routine, read which words it writes and in which order, and
   replay exactly those writes.

Two implementation rules, both device-established. **Write the state word LAST** — before it the
dispatch tables still point at the previous modal, after it every field the painter reads must
already be in place. And **gate the whole thing on the screen family that owns those dispatch
tables**, or you plant a box nobody paints whose later OK drags the player out of ordinary gameplay.

**Generic lesson: drive UI the game already has for this situation instead of constructing UI on the
wire.** Once the game's own modal works, **delete** the synthetic-frame attempt rather than keeping
it as a fallback — the refutation is in [refusals-and-holds.md](refusals-and-holds.md) §12.

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

## ★ Never pad a short list

The bound is a ceiling. It is **not** a target: send exactly the rows you have. Topping a list up to
the array size is a bug that reaches the player as content they never owned.

> **REFUTED.** *Previously believed, and shipped:* a short list reply should be **padded** with
> zeroed records up to the client's array size, because the reply handler `memset`s that array and
> therefore stale rows from the previous page must be getting drawn.
> **Refuted by:** the next device session — storage rendered as page after page of one identical item
> the player had never owned. Rolled back the same day; verified that the stored data was untouched
> (the padding lived only in the reply encoder), and pinned with a regression assertion that a short
> page is **short on the wire**.
> **Why it was attractive:** the `memset` is real, and "the array is dirty" is a correct reading of
> it. The leap is an invisible step — from *the array is dirty* to *stale rows are drawn*.
> **Actual mechanism:** the client draws exactly `N` cells and resolves each row from its **own item
> database** by the row's identifiers. An all-zero pad row is therefore not a blank; it is the item
> whose identifiers are all zero, which is a real item.
> **Consequence if carried forward:** every short page fills the rest of the screen with a valid,
> pickable item, on every player's device, indefinitely.

**(A) The reusable lesson is the step, not the row.** "The handler `memset`s that array" is evidence
the array is **dirty**. Whether stale rows are **drawn** is a *separate* question, answered by the
**draw loop's bound**, and it has to be checked separately. Skipping that check is how a plausible
hypothesis jumps straight from an instruction to a symptom.

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

## Which byte does the client actually test?

**(A)** Many revived servers answer a commit by **echoing the request back**. It usually works, and
almost nobody who ships it can say *why* — which means nobody can say when it will stop working.

Find out mechanically: in the reply handler, locate the comparison that selects the success arm and
identify **which byte of your frame it reads**. Then prove that byte cannot take the failure value
for any input you will ever send.

**(B)** In the reference port the success gate is the **first byte of the echoed key**, and that byte
is the **item index**. The echo reads as success purely because no catalogue row has index zero. Two
consequences fell out of writing that down:

* anyone who adds a catalogue row with index `0` silently turns **every** purchase into a failure;
* the field had been documented as an *"amount"* in **both** the server and the port, which the
  send-side builder disproves. A comment that survived two implementations is not evidence.

**Rule (A): when you echo, name the exact byte the client tests and prove it cannot be the failure
value — and prefer constructing the reply from server state, which contains no such coincidence**
([server-architecture.md](server-architecture.md), "the economy is client-trusted").

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
5. **When you refuse a request, refuse *loudly*, with the client's own error widget** — a
   failure-status reply carrying its reason text **in the same frame**. See the correction below,
   and [refusals-and-holds.md](refusals-and-holds.md) for the full treatment.

> **REFUTED.** *Previously documented here as rule 5, and in
> [server-architecture.md](server-architecture.md) as "refusal is silence — return `[]`":* when you
> refuse a request, prefer **silence** over a malformed reply, because the guest treats "no reply" as
> an incomplete request and eventually times out cleanly. Silence is the safe failure mode.
> **Refuted by:** device sessions plus the client's own wait-state disassembly. **Every screen in the
> client's online module is released by a reply frame and by nothing else, and there is no timeout
> behind any of them.** Silence is not a clean timeout; it is an unrecoverable hang whose only exit
> is killing the app.
> **Why it was attractive:** it is true of the *transport* — an incomplete request really does leave
> the frame decoder in a clean state — and every crash in the early refutation log genuinely did come
> from a malformed reply. The inference "therefore say nothing" quietly assumes a timeout nobody ever
> looked for.
> **Correction:** silence is safe **only** where you have *proven* the client has a deadline behind
> the wait it is showing. Otherwise reply with a **failure status plus non-empty reason text in one
> frame**. And note the sting in the tail: a **bare status-only failure ack is itself a crash
> primitive**, because the client's failure arm builds a message box from text it reads out of that
> same body and runs off the end of the frame. Use silence only for messages the client demonstrably
> ignores — its heartbeat.
> **Consequence if carried forward:** the first refusal a real player triggers wedges the game. In
> the reference port bare status-only refusals sat in the shipped server for months, undetected only
> because the condition that produced them was never reached in production.

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
| save restore | `04 3e <slot>` | `04 3e <slot><blob verbatim>` | **★ dual-use and destructive — see below** |
| currency | `05 14` | `05 14 <u32 LE>` | |
| list (open) | `05 03` | `05 03 <cap u8><u16 N><N×21B>` | **N capped at 16 by the encoder** |
| list (refresh) | `05 3d <slot>` | `05 3d <used><cap><u16 N><rows>` | short reply here causes an over-read |
| deposit / withdraw / discard | `05 41` / `05 04` / `05 05` | `05 41 <status>` / `05 04 <u16><u8><20B>` / `05 05 <status>` | |
| paginated catalogue | `05 3f <12B, cursor at body[4:8]>` | `05 3f <u32 A><u32 B><u16 N><rows>` | the client's cursor machine is simulated in assertion class 4 |
| purchase → commit | `05 42 …` then `05 40 …` | `05 42 01` then **echo of the request** | an echo is a reflection primitive; see [threat-model.md](threat-model.md) |
| combine | `05 06 <u64><u64>` | `05 06 00 00 <text>00` | text bound 251 bytes, multibyte-safe |
| toast / message box | — | `1f 00 …` (transient) / `64 <op><len><text>` (persistent) | **toast ≠ ack** |

**★ The save-restore row earns three lines of its own.** A server-side feature flag
([deploy.md](deploy.md)) is the right hygiene for destructive flags in general and is **not
sufficient mitigation here**, and the reference port learned that from a production log:

1. **The request is byte-identical to a routine login sub-request.** The same seven bytes arrive on
   the same connection during an ordinary login and when a player asks for a restore, annotated in
   the log once as each. **No server can tell them apart**, so no server-side guard — flag, default,
   or state-derived condition — can be correct.
2. **The client applies the reply with no occupancy check.** It deserialises the blob straight into
   the live character structure and re-serialises the slot, so a stale or partial blob is an
   immediate, permanent character reset. The reported symptom was *"story, equipment and items all
   reset"*.
3. **So stop sending it, and neutralise the OUTGOING frame in the host.** When the host's own record
   store says the slot is occupied, **substitute** a benign request from the same fork — never *drop*
   it, because dropping leaves the client's wait unresolved forever. Mechanics:
   [refusals-and-holds.md](refusals-and-holds.md) §11. The guard's design rules (ask one question
   about exactly the slot the frame names; anything unreadable or out of range counts as *occupied*)
   and the host-side pre-image vault that replaces the feature are in
   [save-integrity](../../save-integrity/reference/loss-mechanisms.md) §8.

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
| the client hangs forever after you declined to answer | silence is not a refusal for a reply-gated UI; refuse with failure-status **plus text** |
| the client crashes inside its own error box on a refusal | status-only failure ack; its message box reads text past the end of your frame |
| page-turn keys vanish, or a cursor jumps to an impossible value | a header byte is read `ldrsb` and your unsigned value went negative — clamp, do not mask |
| a count you sent **subtracts** from the player's stack | same class: the client's grant routine sign-extends the count byte |
| storage renders as pages of one item the player never owned | you padded a short list; an all-zero row is a valid item identifier, not a blank |
| every purchase starts failing after a catalogue edit | the echo's success gate is one byte of the echoed key, and the new row set it to the failure value |
| a character resets after a "backup" operation | a restore-class reply was applied over the live character; neutralise it in the host's send path |
| the screen will not change however you reply | no reply can move it; drive the game's own modal instead |
