# Discovering and naming the request surface

Two mechanical procedures, run in order. Phase 2 finds **every** request the client can send,
without triggering any of them. Phase 3 attaches a human name to each one, also without
triggering them. Together they turn "I wonder what this game does online" into a catalogue you
can implement against.

Both are static. Both produce **omissions rather than fabrications** if you follow the rules
below — which is the property that makes them trustworthy.

## Contents

- [Phase 2 — serializer-anchored enumeration](#phase-2--serializer-anchored-enumeration)
  - [Step 1: identify the serializer primitives](#step-1-identify-the-serializer-primitives)
  - [Step 2: every BL to flush is one request](#step-2-every-bl-to-flush-is-one-request)
  - [Step 3: walk back to the prologue](#step-3-walk-back-to-the-prologue)
  - [Step 4: replay the put_* calls into a template](#step-4-replay-the-put_-calls-into-a-template)
  - [★ Step 5: the branch-reset rule](#-step-5-the-branch-reset-rule)
  - [Step 6: self-validate against ground truth](#step-6-self-validate-against-ground-truth)
  - [Step 7: group by leading constant byte](#step-7-group-by-leading-constant-byte)
  - [Worked output (B)](#worked-output-b)
- [Phase 3 — naming requests from UI strings](#phase-3--naming-requests-from-ui-strings)
  - [The six-hop chain](#the-six-hop-chain)
  - [Confidence: MEDIUM per string, reliable per cluster](#confidence-medium-per-string-reliable-per-cluster)
  - [The string dump is your feature inventory](#the-string-dump-is-your-feature-inventory)
  - [Finding the string-table accessor](#finding-the-string-table-accessor)
- [What to do with the dynamic sites](#what-to-do-with-the-dynamic-sites)
- [Failure table](#failure-table)

---

## Phase 2 — serializer-anchored enumeration

**(A)** The insight: a client of this era does not have one send function per feature. It has
**one buffer and one flush**, and every feature writes into that buffer with the same handful of
primitives. So the flush routine is a **chokepoint** — enumerate its callers and you have
enumerated the protocol's client half, completely, from the binary alone.

Run the packaged tool once you have the addresses:

```
python "${CLAUDE_PLUGIN_ROOT}/tools/extract_packets.py" -t target.toml \
  --flush 0x…  --put-u8 0x…  --put-bytes 0x…  --put-u32le 0x…  [--put-u32be 0x…] \
  --out catalog.txt
```

Everything below is what that tool does, so you can adapt it when your title's serializer has a
different primitive set.

### Step 1: identify the serializer primitives

You need, at minimum: a single-byte writer, a byte-range writer, one or more integer writers, and
the flush. Find them by:

* starting from the frame work in [framing.md](framing.md) — you already found flush there;
* `xref.py calls <flush>` to get the caller set, then looking at what *those* functions call most
  often. The primitives are the small, heavily-called functions with a `(buffer, value)` shape.

**(B)** five primitives: `put_u8`, `put_bytes`, `put_u32_le`, `put_u32_be`, `flush`. The presence
of both integer widths is exactly the endianness warning from [framing.md](framing.md).

Write them into `target.toml`-adjacent notes or pass them as flags. Do not hardcode them in a
script — that is the mistake the reference port's tooling made and it is why every tool here
takes `-t target.toml`.

### Step 2: every BL to flush is one request

Scan the **whole image** for `BL` instructions whose computed target equals the flush address.
Each hit is one request site. Not one request *type* — one *site*; two sites can emit the same
message, and one site can emit different messages on different paths (see step 5).

Do not restrict the scan to functions you think are network-related. The point of the technique
is that it finds the ones you did not think of.

### Step 3: walk back to the prologue

From each flush site, walk **backwards** to the function's entry. Heuristic: the nearest
preceding `push {…, lr}`. It is a heuristic and it can overshoot into the previous function; the
symptom is a template with a suspicious number of leading fields. Sanity-check any template
longer than the frame cap allows.

Then disassemble **forward** from the prologue to the flush.

### Step 4: replay the put_* calls into a template

Walk the instruction stream and interpret each call to a known primitive:

| situation | emit |
|---|---|
| `put_u8` with the argument register set from an immediate on the current path | that constant hex byte |
| `put_u8` with an argument register of unknown provenance | `??` |
| `put_bytes(ptr, n)` with a known `n` | `??` × n |
| `put_bytes` with unknown `n` | `??…` (variable) |
| integer writer with a known immediate | the constant, in the writer's byte order |
| integer writer, unknown | `????????` |

You are building a **template**, not a decompilation. `01 3d ?? ×16 ?? ×20 ???????? 00` is a
perfectly good result: it tells you the message has a 16-byte field and a 20-byte field and a
32-bit field and a terminator, which is everything you need to write a parser.

### ★ Step 5: the branch-reset rule

**This is the rule that makes the output trustworthy.** A linear sweep from prologue to flush
walks straight through `if/else` bodies and **unions mutually exclusive branches**, producing
templates that no message ever matches — and you cannot tell which parts are real.

So: **reset the accumulated sequence at every branch**, and report only the sequence accumulated
in the **final basic block** before the flush, flagged as `[+earlier blocks]` so a reader knows
the message may have a prefix you did not attribute.

The result: **omissions, never fabrications.** A template may be missing a leading field. It will
never contain a field that is not there. That asymmetry is what lets you build on the catalogue
without re-verifying every entry.

The cost is real — you will under-report some messages — and it is the right trade. A missing
field is discovered in ten minutes by the client refusing your reply. A fabricated field sends
you chasing a message that does not exist.

### Step 6: self-validate against ground truth

**Do not use the catalogue until the extractor independently reproduces packets you already
captured.** You have at least two from the silent baseline of Phase 0.

**(B)** two send sites templated to exactly the handshake and heartbeat byte strings captured
from the device. That match is the evidence that the other 25 templates are credible; without it
the whole output is a hypothesis.

If a known packet does *not* reproduce, do not paper over it. Either your primitive set is
incomplete (a writer you have not identified), or the prologue walk-back overshot, or the branch
reset dropped the constant bytes. All three are findable and all three would have poisoned every
other template.

### Step 7: group by leading constant byte

Histogram the templates by their first constant byte. **That grouping is your category
discovery** — you did not have to know the protocol had categories.

Then cross-check the histogram against the **receive** dispatcher's `switch` (see
[reply-re.md](reply-re.md)). When the set of leading bytes on the send side and the set of cases
on the receive side substantially agree, you have established `payload[0] = category`,
`payload[1] = opcode` **from both directions independently** — which is a real derivation, not an
assumption.

### Worked output (B)

27 send sites in the reference title:

| leading byte | count | what the cluster turned out to be |
|---|---|---|
| `0x05` | 15 | online storage and economy |
| `0x01` | 3 | login / handshake |
| `0x00` | 1 | heartbeat |
| `0x04` | 1 | save upload / recovery |
| `0x1f` | 1 | notification |
| dynamic | 6 | leading byte set from a register — see below |

Six dynamic sites out of 27 is a typical ratio. They are not failures; they are the sites where a
single code path serves several opcodes, and they are usually the *most* important ones.

## Phase 3 — naming requests from UI strings

**(A)** Every online operation shows the player a status message — "contacting the server",
"saving", "purchasing" — and that message is set by the **same function** that sends the request,
usually within a few dozen instructions of it. So a localized string is a label you can walk to
its send site.

```
python "${CLAUDE_PLUGIN_ROOT}/tools/name_requests.py" -t target.toml \
  --strings ids.txt --window 0x600
```

### The six-hop chain

```
string file-offset
  → the relocated pointer word value        (file offset − RELOC_DELTA)
  → the .data slot holding that word        (4-aligned search of the image)
  → static-base-relative offset             (slot − SL_FO)
  → literal-pool words equal to that offset (this is how code addresses the slot)
  → nearest flush() site within a window    (== the request that operation sends)
```

Each hop is exact except the last. Hops 1–4 depend entirely on the address model — the relocation
delta and the static base — which `guest-re/reference/address-model.md` teaches you to pin.
**⚠ Get the relocation delta's *unit* right** (file offset vs guest address) or every hop lies
consistently and plausibly.

**(B)** relocation delta `0xeec`, static base file offset `0xb2c40`, window `±0x600`.

### Confidence: MEDIUM per string, reliable per cluster

Be honest in your notes. The window typically catches ~2 flush candidates per string, so an
individual string→request binding is **MEDIUM / INFERRED**, never CONFIRMED.

What *is* reliable is **cluster-level attribution**: all strings about storage land in one address
neighbourhood whose flush sites are all in the same leading-byte category from Phase 2; all
strings about currency land in another. Two independent groupings (address neighbourhood, and
leading-byte category) agreeing is a genuine second derivation.

Label every binding with its confidence and mark it ⚠ until a device confirms it. See
`${CLAUDE_PLUGIN_ROOT}/shared/evidence-standard.md`.

### The string dump is your feature inventory

**★ This is worth as much as the request catalogue.** The set of UI strings that can only appear
while a server is answering tells you **exactly what you must implement** — and, just as usefully,
what you can ignore.

Sort the string dump for messages of these shapes:

* waiting/progress: "waiting for a server response", "saving to the server", "purchasing…"
* server-side capacity errors: "your online storage is full"
* operations with no offline meaning: item transfer, character restore, combining, shop purchase

Each distinct progress message is a feature. The reference port's inventory came out as: account
recovery + networked item storage + an in-game shop + an item-combining operation. Four features,
derived before a single reply had been written, and the final server implemented exactly those.

Conversely, the *absence* of such strings is the cheapest possible answer to "does this title
need a server at all" — which is why `SKILL.md` puts that test at the very top.

### Finding the string-table accessor

**(A)** If the game's strings are behind a `getText(id)` accessor rather than direct pointers, you
need the accessor before the chain above works. Mechanical way to find it:

For every `movs rX, #imm8` where `imm8 < id_count`, followed within ~8 halfwords by a `BL`, tally
the callee. **The real accessor collects a large, dense set of distinct ids; noise collects two
or three.** Rank callees by distinct-id count.

```
python "${CLAUDE_PLUGIN_ROOT}/tools/msgid_scan.py" -t target.toml --id-count N --top 20
```

The same shape works for any `id → resource` accessor: sprite loaders, sound loaders, table
lookups. It is one of the highest-yield generic scans in the whole toolkit.

## What to do with the dynamic sites

A template whose **leading byte** is `??` is a site where one function serves several opcodes.
These are usually the interesting ones (a generic "storage operation" sender, a generic "shop
operation" sender). Three ways to resolve them, cheapest first:

1. **Look at the caller.** The opcode is almost always an argument, and the callers pass
   immediates. `xref.py calls <site> --with-imm8-arg` gives you the constant set directly.
2. **Look at the receive side.** The dispatcher's jump table bounds the opcode space
   ([reply-re.md](reply-re.md)); intersect that with the categories you know.
3. **Trigger it on the device** and read the transcript. Cheapest per-message, most expensive
   per-round-trip. Save it for the ones the first two do not settle.

Record the unresolved ones explicitly as "unknown" in the catalogue. An honest gap is worth more
than a guess that later gets cited as established.

## Failure table

| symptom | cause |
|---|---|
| the extractor invents fields that are never on the wire | branch reset not implemented; you are unioning mutually exclusive paths |
| a known captured packet does not reproduce | missing serializer primitive, prologue walk-back overshoot, or a constant lost to the branch reset — investigate, do not ignore |
| templates far longer than the frame cap | prologue heuristic walked into the previous function |
| every string binds to the wrong function | relocation delta wrong, or its *unit* wrong (file offset vs guest address) |
| the string chain finds nothing at all | strings go through an id accessor; find it with the msgid scan first |
| categories from send and receive sides disagree | one of them is not category/opcode; re-derive before building on it |
| a request site you know exists is missing | it does not call flush — some titles have a second send path (fire-and-forget UDP-like, or a raw write) |
