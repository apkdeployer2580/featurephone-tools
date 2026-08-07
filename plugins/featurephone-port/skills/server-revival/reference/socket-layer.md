# The socket layer — making the client talk to you

The dead server's address is a constant inside the guest. **You do not patch the guest.** You own
the emulator's socket implementation, so you decode the address the game asked for, log it, throw
it away, and connect where *you* want. Four lines of code, and it is the whole technique.

Everything else in this file is the set of host-side contracts that must be right before the
protocol work in later phases can even be observed. Get one of them wrong and you will spend days
debugging "the protocol" while the bug is in your socket layer.

## Contents

- [Find the connect site](#find-the-connect-site)
- [The slot contract — never trust upstream slot names](#the-slot-contract)
- [The seven host rules](#the-seven-host-rules)
- [Connect budget vs the guest's own patience](#connect-budget-vs-the-guests-own-patience)
- [`poll_readable`: the three verdicts, and the one that freezes the app](#poll_readable-the-three-verdicts)
- [Runtime-configurable destination, from day one](#runtime-configurable-destination-from-day-one)
- [Parsing an endpoint line](#parsing-an-endpoint-line)
- [TLS as a socket-layer wrapper](#tls-as-a-socket-layer-wrapper)
- [Rewriting outgoing frames: identity injection](#rewriting-outgoing-frames-identity-injection)
- [A second, host-owned connection](#a-second-host-owned-connection)
- [Logging discipline](#logging-discipline)
- [The silent baseline](#the-silent-baseline)
- [Failure table](#failure-table)

---

## Find the connect site

**(A)** Three routes, cheapest first:

1. **String scan for dotted quads.** Games of this era ship their own `inet_addr` string parser,
   so the server address survives in the image as ASCII. `strings_dump.py` with a
   `\d+\.\d+\.\d+\.\d+` filter usually finds it in seconds.
2. **Cross-reference the string.** `xref.py pcrel <addr>` / `literal` gives you the code that
   loads it. The nearby function is the connection setup.
3. **Cross-reference the platform's connect slot.** If you already have a slot table, find calls
   through it.

**(B)** In the reference port the connection setup was a completion callback whose two branches
selected between two hardcoded endpoints — `<dotted-quad A>:<port A>` and
`<dotted-quad B>:<port B>` — and whose fourth connect argument was a channel-name string, not an
address. Two useful generalizations from that:

* **There may be more than one hardcoded destination.** Enumerate all branches before you decide
  how many listeners you need. In the reference port RE later showed the client can be pointed at
  a single endpoint for both, so the second listener was deleted as pure attack surface.
* **Some arguments are not what their position suggests.** A "name" argument may be a carrier
  channel selector the host can ignore entirely.

Do not patch the binary to change the address. Redirect in your `connect` implementation and keep
a `REDIRECT_ENABLED` escape hatch so you can run against a real capture setup later.

## The slot contract

**(A)** Feature-phone vendor APIs are dispatched through a **positional table of function
pointers**. The emulator fork you vendored labels those slots by *guess* — upstream authors
inferred names from one or two titles. Your title may use entirely different slots.

**Procedure:** log every slot index the game calls with its arguments, before implementing
anything. Then map indices to behaviour from the argument shapes and the guest's use of the
return value. Only then write the implementations.

**(B)** The reference title's WIPI socket slots, discovered this way:

| slot | operation | notes |
|---|---|---|
| 2 | `socket(af, type) -> fd` | |
| 30 | `connect(fd, addr, port, name, cb, param) -> 0 \| no-data-sentinel` | result arrives via callback, not the return value |
| 31 | `send(fd, buf, len) -> bytes accepted` | |
| 32 | `recv(fd, buf, max) -> n>0 \| sentinel \| 0-or-negative` | |
| 33 | `close(fd)` | |
| 0, 1, 13 | address/name helpers | |

Upstream had named slot 3 "connect" and slot 6 "close". **Both wrong.** Implementing the upstream
names produces a game that appears to do nothing on the network.

**★ The table-length trap.** The dispatch table is indexed by the guest with no bounds check. If
your table has 8 entries and the game calls slot 30, the guest reads past the end of the table,
gets whatever memory follows, and branches to it — typically `PC = 0` and an immediate crash that
looks like a CPU bug. **Grow the table to cover the highest slot you observe and pad it well
beyond** (the reference port needed 34 entries and padded to 64). Pad with a stub that *logs the
slot index and returns a benign value*, so the next unknown slot names itself instead of crashing.

## The seven host rules

**(A)** All seven are generic. Violating any one of them produces a symptom that reads as a
protocol bug.

1. **Nothing may block.** The emulator's interpreter thread runs the guest. If your `connect`,
   your TLS handshake or your `recv` blocks, the whole game freezes. Blocking operations go to a
   helper thread; the emulator polls a result channel.
2. **`recv` must return the platform's no-data sentinel when there is no data.** Not `0`. `0` and
   negative values mean "peer gone" to the guest, and it will tear down a perfectly healthy
   connection. **(B)** the KTF WIPI sentinel is `-19`, recognised by the guest at three sites via
   `cmp r0, #-19`. Find your platform's value by disassembling the guest's own comparisons after
   its `recv` call — do not guess it from a header.
3. **`recv` must clamp to the guest's *real* allocation, not the length it asks for.** Old games
   over-ask. **(B)** the reference title's receive object is a single 1028-byte allocation whose
   wire bytes begin at `obj+0xc`, but its pump calls `recv(fd, obj+12+used, 1024-used)`.
   Satisfying that request in full writes **8 bytes past the object**. This never fired against
   the original server, which never sent enough at once; it is trivially reachable from yours.
   The host keeps a slack constant (`GUEST_RECV_SLACK = 8`) and shortens every read by it.
   **Generic lesson: audit the guest's own buffer arithmetic before you serve it more data than
   the original server ever did.** Also mirror the resulting cap in the server's `MAX_FRAME`.
4. **Implement the read callback.** Platforms of this era register a "socket became readable"
   callback rather than polling. If your emulator stubs that slot, the client **stops polling
   `recv` entirely** and never reads a single one of your replies. In the reference port this was
   the very last blocker before the first successful exchange, and it looked exactly like "the
   server's replies are malformed".
5. **A failed socket counts as readable.** See below — this is the subtle one.
6. **`peek`, do not consume, when answering "is it readable?"** The guest's own `recv` must still
   see the bytes afterwards.
7. **EOF counts as readable.** The guest must be allowed to call `recv`, get its "peer gone"
   value and run its own teardown. Hiding EOF produces a hang.

## Connect budget vs the guest's own patience

**(A)** The guest has its own connect timeout, usually a poll count times a poll interval. Find
it (it is near the connect callback) and set **your** budget *under* it, so host and guest never
disagree about whether a connect is still alive. If your budget is longer, the guest gives up,
tears down its state, and your late "connected" callback arrives at a machine that has moved on.

**(B)** guest patience ≈ 50 ms × 240 ≈ 12 s ⇒ host `CONNECT_BUDGET = 8 s`.

Two traps that only appear once you move from an IP to a hostname:

* **`to_socket_addrs()` has no timeout.** DNS resolution is inside your budget whether you
  planned for it or not. Include it.
* **A per-address timeout multiplies by the number of addresses.** A hostname with four A records
  and a 5 s per-address timeout is a 20 s connect. Share one deadline across all addresses.

## `poll_readable`: the three verdicts

**(A)** The platform's read-waiter typically fires the guest callback for exactly one verdict and
**silently drops the waiter** for anything else. So:

| socket state | you must report |
|---|---|
| data buffered | readable |
| clean EOF | **readable** (let the guest call `recv` and see the peer-gone value) |
| socket error (RST, ECONNRESET, host unreachable) | **readable** — *not* an error code |

Returning `-1` on socket error means the guest's read waiter is dropped with no callback, the
receive pump is gated off **forever**, and the app sits on its waiting-for-server modal until it
is force-stopped.

**★ Why you will not find this in testing.** Over `adb reverse` or on a LAN you only ever see
clean FINs, so verdict 2 covers everything. Carrier NAT resets produce verdict 3. **This is
therefore the first bug every remote player hits and the last one you will reproduce.** Test it
deliberately: connect, then `iptables -j REJECT --reject-with tcp-reset` the server, or kill the
server process with the connection open from a different network.

## Runtime-configurable destination, from day one

**(A)** A compile-time constant destination is what later turns into "it only works on my desk".
Three layers, first hit wins, **re-read on every connect** (not cached at construction):

1. **A file in app-private storage**, written by an in-app dialog → address changes with no
   rebuild, no reinstall, no restart. This is what lets a tester point at a new server.
2. **Build-time environment** (`${ENV_PREFIX}_SERVER_HOST` / `${ENV_PREFIX}_SERVER_PORT` via
   `option_env!`) → a build you hand out already points at the public server.
3. **`127.0.0.1:<port>`** → the `adb reverse tcp:<port> tcp:<port>` development loop.

**★ Never gate the layering on `debug_assertions`.** The reference port did, which pinned every
debug build to loopback and made "an APK someone else can install that reaches a public server"
impossible to produce. The environment layer, not the build profile, decides the destination.

See also `port-shipping/reference/config-baking.md`: `option_env!` resolves at rustc time and a
missing variable yields a *silently wrong* build, so verify the baked string inside the produced
`.so`, not in the build script.

## Parsing an endpoint line

**(A)** The endpoint parser is small, security-relevant, and worth writing dependency-free so a
standalone selftest can `include!` it (see `${CLAUDE_PLUGIN_ROOT}/shared/selftest-convention.md`).

Accept `[scheme://]host[:port]`. Distinguish three shapes **in this order**:

1. `[addr]:port` — bracketed IPv6 with a port.
2. **≥2 colons and no brackets ⇒ a bare IPv6 literal, not `host:port`.** Splitting on the last
   colon turns `::1` into host `:` and port `1`. This is the bug the ordering exists to prevent.
3. Ordinary `host:port`.

Reject port `0`, reject non-hostname characters, reject names over 253 characters. **On a parse
failure, fall through to the next configuration layer rather than failing the connect** — a
typo in a device file must not brick networking until someone can edit the file again.

## TLS as a socket-layer wrapper

**(A)** The original binary knows nothing about TLS, and it does not need to: it calls your
socket slots and never sees a byte you did not hand it. Wrapping is therefore a pure host
concern and costs the guest nothing.

Two structural consequences you cannot design around:

1. **The handshake blocks.** Run it on the connect helper thread, on a still-blocking socket, and
   set the socket non-blocking only afterwards. A lazy handshake on first `send` parks the
   interpreter.
2. **The guest needs `peek`; TLS cannot peek.** A record must be decrypted before you know
   whether it carried application payload. So the TLS stream owns a **plaintext buffer**, and
   "readable" means *that buffer is non-empty*. Put the buffer's drain function in its own
   dependency-free file with its own selftest — it is a three-outcome contract (`bytes handed
   over` / `nothing yet` / `peer gone`) and a bug there costs data, silently.

Trust model, **per endpoint**: a `tls://` scheme in the address selects encryption; the trust
mode is either a bundled root set (no OS trust-store plumbing) or
`Pinned(<32-byte SHA-256 of the server certificate DER>)`. The pin resolves file-then-build-time
env, exactly like the host. **The build-time layer is what makes a domain optional**: a
self-hoster points the app at a bare VPS with a self-signed certificate, bakes the pin into the
build, and hands out an artifact that players configure zero times. Certificate mechanics and the
tradeoff table are in [deploy.md](deploy.md); why pinning is *stricter* than CA validation is in
[threat-model.md](threat-model.md).

Cross-compilation note **(A)**: pick a crypto backend that does not need `cmake` and `nasm`, which
an NDK cross-build does not have. In Rust that means selecting `ring` over the default backend.

## Rewriting outgoing frames: identity injection

**(A)** **The host owns `send`, so the host can rewrite outgoing packets.** This is the escape
hatch for anything the guest gets wrong that you cannot patch cheaply.

**(B) The worked case, and why it generalizes.** The reference title fills its 16-byte identity
field from a platform property that the emulator (and, it turns out, the original handsets in
some configurations) stubs — and then a loop in the client **overwrites the field with a constant
literal anyway**. The shipped binary therefore sends *no per-user identity at all*: every player
would land on one shared server account.

Fix, entirely in the host: `send` matches the two bytes that identify the login frame and
overwrites the identity field in place, leaving every other frame untouched.

```
if data[4] == <login category> && data[5] == <login opcode> {
    data[6..22].copy_from_slice(&device_identity);
}
```

**Generic lesson: when the guest's identity field is a stub, inject identity in the host's send
path.** The server needs no protocol change — it keys on "whatever N bytes arrived". The offsets
and the packet id are **(B)**; the technique is **(A)**.

⚠ Injecting an identity makes that identity a **bearer credential**. Read
[threat-model.md](threat-model.md) before you ship it, and prefer the challenge-response upgrade
described there, which fits inside the same frames.

## A second, host-owned connection

**(A)** The guest's socket belongs to the guest: the game opens it, drives it and tears it down,
and only while the player is inside the network UI. If your host wants to talk to the server at a
moment the game is not online — an automatic backup, a config fetch, a heartbeat while the player
is on a menu — **open your own short-lived connection on your own thread** and speak the same
frames.

Because you have already reproduced the client's frame encoder for the conformance tests, the
frames are byte-identical and **the server needs no special case**. The reference port's
host-side link performs hello → wait ack → login → wait ack → upload → wait ack, then closes; it
is blocking, on its own thread, and never touches the guest's socket state.

This is one of the most valuable patterns in the chapter: once you can synthesize the client's
frames, the host is a first-class protocol participant.

## Logging discipline

**Log the header, never the payload.** The payload carries the account credential and save blobs,
and on Android logcat is world-readable by any app with the right permission on older releases
and by anyone with a cable on all of them. Log `total_len`, category, opcode, direction and a
byte count. Full-payload logging belongs behind an explicitly named debug flag that is off in
release builds, and its output belongs outside the source tree
([black-box-loop.md](black-box-loop.md) covers transcript masking).

## The silent baseline

Before you send a single reply byte, run a server that **accepts the connection, holds it open,
and answers nothing**, and record a transcript for several minutes.

You are establishing four facts:

1. The client reaches you at all (the redirect works).
2. A mute server does **not** cause a teardown — if it does, one of the seven host rules is wrong
   and no protocol work you do will be interpretable.
3. What the client sends **unprompted**: the login sequence and the heartbeat.
4. The **heartbeat period**, measured. That number sets your production idle timeout later.
   **(B)** mean 5.036 s, max 6.798 s over n=1224 frames ⇒ a 45 s idle timeout ≈ 6.6× worst case.

Only after this baseline can any behavioural change be attributed to a reply you added. Skipping
it is the single most common way to burn a week attributing coincidences.

## Failure table

| symptom | cause |
|---|---|
| game crashes with `PC = 0` on first network use | slot table shorter than the highest slot the game calls; grow and pad it |
| game appears to ignore the network entirely | you implemented the slots upstream *named*, not the slots the game *calls* |
| client never reads your replies | read-callback slot stubbed |
| client tears down a healthy connection | `recv` returned `0` instead of the no-data sentinel |
| guest heap corruption after a large reply | `recv` honoured the guest's over-ask; clamp to the real allocation |
| whole app freezes during connect | something blocked on the interpreter thread |
| app frozen forever after a network reset, only off-LAN | socket-error poll verdict dropped the read waiter |
| "cannot connect to server" text on screen | the guest's connect callback got a refused/RST status; accept and hold and it never appears |
| connect works on IP, times out on a hostname | DNS is inside the budget, and per-address timeouts multiply |
| works on your desk, nowhere else | destination is a compile-time constant, or gated on `debug_assertions` |
| TLS build fails in CI cross-compile | crypto backend wants `cmake`/`nasm`; switch backends |
