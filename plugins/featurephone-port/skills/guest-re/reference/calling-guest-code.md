# Calling guest functions from the host

Reading guest memory is cheap and safe. **Calling guest code is neither.** It runs real game logic on a real ARM context inside a cooperative executor, and every one of the rules below was learned by something breaking.

## Contents

1. [The host↔guest interface surface](#1-the-interface-surface)
2. [Mechanics: what actually happens when you call](#2-mechanics)
3. [Rule 1 — set the Thumb bit](#3-rule-1--set-the-thumb-bit)
4. [Rule 2 — a jump-table arm is not a function](#4-rule-2--a-jump-table-arm-is-not-a-function)
5. [Rule 3 — a chain of calls is a polled state machine](#5-rule-3--a-chain-of-calls-is-a-polled-state-machine)
6. [Rule 4 — attach the JVM thread](#6-rule-4--attach-the-jvm-thread)
7. [Rule 5 — time out every step](#7-rule-5--time-out-every-step)
8. [Rule 6 — gate on the game's own precondition](#8-rule-6--gate-on-the-games-own-precondition)
9. [Rule 7 — clear out-params, own one job at a time](#9-rule-7--clear-out-params-own-one-job-at-a-time)
10. [Accepting residual hazards honestly](#10-accepting-residual-hazards-honestly)
11. [Checklist](#11-checklist)

---

## 1. The interface surface

Four methods added to the emulator trait, with **defaults that fail rather than hang**:

```rust
fn read_memory(&self, address: u32, len: usize) -> Vec<u8>;                  // default: empty
fn write_memory(&mut self, address: u32, data: &[u8]) -> bool;               // default: false
fn call_guest_function(&mut self, address: u32, args: &[u32]);               // return value DISCARDED
fn call_guest_function_out(&mut self, address: u32, args: &[u32],
                           out: Arc<GuestCallResult>);                        // default: out.fail()
```

Two design points worth copying **(A)**:

* **`call_guest_function` discards the return value.** That is fine for fire-and-forget pokes and useless for a chain. `call_guest_function_out` exists because step N+1's arguments are step N's *return*.
* **The trait defaults fail loudly rather than leaving the result pending.** A backend with no ARM core (a stub, a different platform, a test double) must not be able to wedge a host state machine that is waiting for an answer.

Everything else in the host reads guest memory through **one closure shape**, which keeps every parser pure and unit-testable:

```rust
let read = |addr: u32, len: usize| emulator.read_memory(addr, len);
items::parse_something(&read)          // tests feed a HashMap<u32,u8>-backed reader
```

---

## 2. Mechanics

A host-originated call:

1. clones the ARM core handle;
2. spawns a **cooperative task** on the emulator's executor;
3. runs `core.run_function::<u32>(address, &args)` on **its own ARM thread context with a fresh stack** — the exact same mechanism WIPI callbacks use;
4. `run_function` saves and restores CPU context, so the parked main-loop task is undisturbed;
5. publishes the return value into a result cell.

**It executes on the next `tick()`, not inline.** Your call site returns immediately with nothing. This single fact is why every chained interaction has to be a state machine.

---

## 3. Rule 1 — set the Thumb bit

```rust
const SERIALIZE_SAVE: u32 = 0x0019_60bc;
emu.call_guest_function_out(SERIALIZE_SAVE | 1, &[scratch], out);
```

RE notes record **even** addresses, because that is what the disassembler prints. The core branches to whatever you give it, so an even address means ARM mode, which means garbage decode and an immediate fault at an address nowhere near your bug.

OR in `| 1` in one place — inside the call wrapper, not at each call site — and **assert it in a selftest**. It is one line and it removes an entire failure mode.

(If your image is ARM-mode, `isa = "arm"`, this rule inverts: the low bit must be **clear**.)

---

## 4. Rule 2 — a jump-table arm is not a function

**The most expensive mistake available in this area.** The game's own five save call sites may all be unusable while the primitives they are built from are perfectly callable.

Two real counter-examples **(B)**:

* An arm at `0x1243c0` uses its **parent's** `sub sp, #4` frame and exits through the parent's shared epilogue at `0x124246`. Entering it directly pops garbage into `pc`.
* A routine at `0x13ce34` begins `adds r3, r7, #0 ; subs r3, #0xc ; ldr r0, [r3]` — it **reads the caller's stack frame** for its arguments. Called cold, it reads whatever is under your fresh stack.

### The callability check — run it on every target, every time

Disassemble the target's **first** and **last** instructions and require all three:

- [ ] **Its own prologue.** `push {…, lr}` (or an equivalent frame setup). No push of `lr` → it cannot return to you.
- [ ] **Its own epilogue.** `pop {…, pc}` or `bx lr` *within the range you believe is the function*. An exit that branches into a shared tail belongs to the parent.
- [ ] **Its own `sl` setup.** The RWPI prologue (`ldr rX,[pc,#…] ; mov sl,rX ; add sl,pc`). Without it the function inherits `sl` from its caller, and your call has no caller. See [address-model.md](address-model.md) §4.

If a target fails the check, **look one level down**. The arm is usually a thin wrapper around ordinary functions that pass all three tests — call those instead and do the sequencing yourself.

Also worth checking: whether the function is small enough to just **read** rather than call. A "can I do X?" predicate that is nothing but `return (s8)*(GOD+0xa280);` should be a memory read, not a queued call. One read cannot fail, cannot time out, and cannot re-enter.

### Cheapest of all: write the state the handler reads, and synthesise its trigger **(A)**

Before hunting a handler's address at all, ask whether the same effect is available with **no guest address whatsoever**: write the byte the game's own key router reads — a cursor, a highlighted row, a page index — and synthesise the ordinary keypress a player's thumb would produce. The game's dispatcher then does the whole job on its own stack, and your commit path carries **no address that can go stale**.

Reference case **(B)**: build 1's commit called the game's own popup key handler directly. Build 2 needed no guest function at all. Two deterministic probe runs, identical except that one poked the highlighted-row byte ten frames before an ordinary OK, showed the dispatcher reading that byte on key arrival and performing the entire operation itself, cleanup of the previous binding included. A plan document had twice asserted "the one remaining unknown is this handler's address"; both assertions were wrong. `HIGH / CONFIRMED`.

**⚠ But synthesising a keypress is *not* equivalent to calling the handler.** The key is delivered **a tick later**, and the game's own clamps and routers run in the gap. Two consequences, both measured **(B)**:

* **Restore anything you temporarily shrank before the key lands.** One implementation reduced a row-count byte to 4 while the row to be committed could be 5, and the guest's own cursor clamp pulled the row number back down inside that window. The commit path now restores the native count immediately before the key arrives — a hazard the direct-call build structurally does not have.
* **A host guard that duplicates a router check is needed only on the build that bypasses the router.** Carried across to the build that goes *through* the router, the same guard read unrelated live fields and refused every popup of one kind. Diagnostic worth keeping: when two paths share one machine and only one fails, the difference is in the **path-specific gate**, not in the machine.

The touch side of this — one tap focuses, a second tap activates, and never cache the cursor byte you are about to write — is [touch-driven-guest-ui.md](../../android-host/reference/touch-driven-guest-ui.md).

---

## 5. Rule 3 — a chain of calls is a polled state machine

Because results land on a later tick, a four-step chain is four polls, not four lines.

### The three-state result cell

```rust
pub struct GuestCallResult { value: AtomicU32, state: AtomicU8 }   // PENDING | OK | FAILED
```

**Three states, not two.** The docstring rule is the whole reason it exists:

> *Treating a failure as "not yet" would hang the machine forever, and treating "not yet" as a failure would abandon a call that was about to succeed.*

A `bool` cannot express this. Pair `Release` on the writer with `Acquire` on the reader so the value is visible whenever the state is.

### Shape the chain as a pure function

```rust
pub fn advance(&mut self, result: Option<CallOutcome>, scratch: u32) -> Next
```

`Next` is `Call(addr, args, out) | Write(addr, bytes) | Done | Failed(reason)`. The worker polls it once per frame and performs whatever it returns. Because `advance` touches no globals and no emulator, the entire step order, the argument shapes and the timeouts are testable in a dependency-free selftest that `include!`s the real source — see `${CLAUDE_PLUGIN_ROOT}/shared/selftest-convention.md`.

### Worked chain **(B)**

```
1. serializeSave(&scratch)  -> buf          ; read len from scratch
2. validate  buf plausible, len in band     ; refuse, do not clamp
3. writeDbRecord(nameA, buf, len)
4. slotSummary()            -> summary
5. writeDbRecord(nameB, summary, 16)
```

Note step 2. **Validation between steps is a step.** Passing an implausible pointer or a nonsense length into the writer is how you corrupt a save file with the game's own code.

**Never invent the record-naming convention — take it from the game.** It appends a slot suffix to its own name strings, and a host that guesses the format writes into a namespace the game will never read.

> **REFUTED.** *"Read the record-name pointers out of the guest's **own** static slots rather than hardcoding strings."*
>
> **Refuted by:** the second build of the same game, where there are no static slots to read. That build is a linked ELF: its name strings are compiled into `.text` and reached by absolute literals ([address-model.md](address-model.md) §9.1), so the instruction above is not differently-parameterised, it is **unimplementable**. `HIGH / CONFIRMED` — the corrected chain was then proven end to end by the game loading a record the host had written.
>
> **Why the wrong reading was attractive:** on an RWPI build the slot table really is the right source, and reading it really is better than hardcoding a string — the *intent* survives untouched. What does not survive is the unstated assumption that "the guest's own copy of a pointer" is something every build has.
>
> **Consequence if carried forward:** a port to a second build stalls looking for a slot table that does not exist — or, worse, adopts some other plausible static word as "the name pointer" and writes the save into a namespace nothing reads.

**The build-keyed form (A).** Make the *source* of the names an enum with one arm per addressing model, and put the reason on each arm:

```rust
enum SaveNames {
    /// RWPI build: the static slots hold POINTERS to the strings — read them at runtime.
    SlPtrs { game: u32, slot: u32 },
    /// Linked-ELF build: the strings are compiled in — pass their addresses straight through.
    Direct { game: u32, slot: u32 },
}
```

**Key the pointer-plausibility window the same way.** The `plausible_ptr` guard that decides "could this word be a guest pointer at all" is **per build**: one build's image starts near `0x100000`, the other's `.text` starts at `0x1000`. A window written for the first silently refuses every valid pointer on the second, and a refusal reads exactly like "the feature is not available here". Open it at the **lowest mapped guest page of the build that actually booted** — [build-keyed-anchors.md](build-keyed-anchors.md).

---

## 6. Rule 4 — attach the JVM thread

**★ The bug that made the very first host-originated call panic on a device.**

```
PANIC: rustjava jvm.rs:592: called `Option::unwrap()` on a `None` value
```

**Mechanism.** In a hybrid JVM/native runtime, the JVM keys per-thread state by the **executor task id**. `find_calling_class` does `threads.get(&thread_id).unwrap()`. A `System::spawn`ed task has a brand-new id with no entry, so the first call into any path that touches the JVM panics.

The platform's own WIPI callback spawner had *always* wrapped its callbacks in `attach_thread` / `detach_thread`. `call_guest_function` had not — which means **it had never once worked on a device**, and nothing in the headless probe exposed it, because the probe's paths did not reach the JVM.

**Fix.** Publish the JVM handle into an `Arc<Mutex<Option<Jvm>>>` when the emulator starts, and wrap every host-originated call in an RAII guard:

```rust
struct JvmThreadGuard(Option<Jvm>);
// attach on construction; detach in Drop, so error paths and early returns cannot leak an entry.
// None when the JVM does not exist yet (a call issued during boot) — must not panic.
```

**(A) Generalise:** any host→guest call path in a hybrid runtime needs this. **Check it before shipping the first such feature**, and check it *on a device* — this is precisely the class of bug a headless run does not reproduce.

**⚠ "This carrier does not need the guard" is a claim about *paths*, not about a carrier.** The second carrier's runtime crate carried an explicit comment saying the attach was unnecessary **there** ("no ARM-side AOT Java on this path"). That comment was true of most paths and **false of the record-database path** — and the first host-originated save on that build panicked in exactly the same `threads.get(&id).unwrap()`. The guard had to be ported into the vendored crate before an end-to-end save worked. **(B)** `HIGH / CONFIRMED` (panic reproduced, then fixed, then a save the game loaded).

Two rules fall out **(A)**:

* Re-test the claim **per subsystem**, and start with the **record/database path** — that is the one that falsifies it.
* Wrap `call_guest_function` *and* its out-param variant. Wrapping one is the shape of this bug the second time you meet it.

---

## 7. Rule 5 — time out every step

```rust
const STEP_TIMEOUT_POLLS: u32 = 240;   // ~100x the expected latency
```

A call that never lands must not wedge the machine. Reset the timer **per step**, not per job, or a slow-but-progressing chain trips it. Pin the reset with a named regression test — this is exactly the kind of logic that gets refactored into being wrong.

Sentinel bugs in the surrounding timers are their own hazard. One real example **(B)**: an autosave scheduler treated `last == 0` as "now". But `now >= now + interval` is false at *every* poll, so autosave was **silently disabled forever**. The fix is to make `0` mean "never armed" explicitly and have the worker seed it with the session start — with a named test asserting it.

**Any sentinel value in a timer needs a test that shows what it does at the boundary.**

---

## 8. Rule 6 — gate on the game's own precondition

Do not invent your own rule for when an action is allowed. Read the game's.

A real gate **(B)**, with each clause's provenance in its doc comment:

```
god object resolves to a plausible heap pointer          (else the whole read is meaningless)
AND (s8)*(GOD+0xa280) == 0                               (this IS the game's own canSave())
AND the game is in field state                           (the mode the game's own save path requires)
AND slot index <= 15                                     (the record namespace's real bound)
AND both record-name pointers are plausible              (a stale table means a stale build)
```

> **The host refuses in exactly the places the game does, rather than inventing its own rule.**

Every clause you invent instead of reading is a place where your behaviour and the game's diverge, and yours is the one that will be wrong.

**And prove the control is reachable in a state that passes the gate.** A reference SAVE button was placed in a panel that only exists outside field mode, while its gate only passes inside field mode — so it always reported "cannot save here", and the entire chain underneath it was correct. Test the path to the feature, not only the feature.

---

## 9. Rule 7 — clear out-params, own one job at a time

* **Clear the out-param cell before every call.** A stale length from the previous invocation reads exactly like this one's answer, and it will be plausible.
* **One job at a time.** A second serialise started while the first buffer is still unwritten leaks the first buffer and can interleave two records. Refuse to start; do not queue.
* **Validate every pointer the guest hands back** against a window (`0x100000 ..= 0x8000_0000` for "image or heap"), and every length against a band. See [patching.md](patching.md) §6.
* **Keep side jobs off the critical path.** A backup/export that runs after a save must be a *separate* state machine, started only once the save is committed — never another step in the save chain. It must not be able to fail a save. If it goes wrong, the character is still on disk.

---

## 10. Accepting residual hazards honestly

Some hazards cannot be removed, only bounded. Write them down rather than discovering them in production.

**Leaks.** The game's own primitives allocate (serialise twice, summary once, writer `len + 0x10`) and **nothing frees** — the original only saved on demand, so it never mattered. Driving them on a timer changes that. Reference accounting **(B)**: ~2.9 KB per save, so a 5-minute autosave cadence costs ~175 KB over a five-hour session. **Mitigation is cadence, and the arithmetic belongs in the comment next to the interval constant.**

**Allocator assumptions — and the separate question of identity.** Holding a guest pointer across ticks is sound only because the allocator is malloc/free with no compaction. Verify that on your platform before choosing a chained design.

⚠ **That is a statement about the address, not about the object at it.** The *game* may compact its own containers in place — swapping whole record contents between cells — so the address stays valid while the object living there becomes a **different one**. "Can I hold this **address** across ticks?" and "can I hold this **identity** across ticks?" are different questions with different answers, and any deferred call or write that names an object by container coordinate needs an **instance-key re-match at the moment of use**, whatever the allocator does. In the reference port the compactor's wrapper is reached from seventeen call sites — including the item popup's own close path, which is the keystroke that commits the host mutation. `HIGH / CONFIRMED`. The procedure is [live-data-structures.md](live-data-structures.md) §4-§5.

**State the limits of what you drove.** If a network-delivered reward goes through a different code path than the one you patched, or an effect only applies at equip time so expiry cannot revert it, that is user-facing behaviour. Put it in the feature description, not in a future bug report.

---

## 11. Checklist

Before the first host-originated guest call ships:

- [ ] Thumb bit ORed in one place, asserted by a test.
- [ ] You checked whether the feature needs a call at all — a memory read, or writing the state and synthesising the key, beats a call (§4).
- [ ] Every target passed the callability check (own prologue, own epilogue, own `sl` setup).
- [ ] Anything that is just a memory read is a memory read, not a call.
- [ ] Result cell is three-state; `Acquire`/`Release` paired.
- [ ] The chain is a pure `advance()` state machine, selftested via `include!` of the real source.
- [ ] JVM thread attach is RAII, tolerates a missing JVM, wraps **both** call entry points, and **has been verified on a device — on each carrier, on the record path** (§6).
- [ ] Anything named by container coordinate is re-matched by instance key at the moment of use (§10).
- [ ] Record names come from the game, by the route that exists **on this build** (§5).
- [ ] Per-step timeout with a named regression test; every timer sentinel has a boundary test.
- [ ] The gate reads the game's own precondition, clause by clause, with provenance comments.
- [ ] The UI control is reachable in a state that passes the gate.
- [ ] Out-param cleared before use; one job at a time; every returned pointer and length validated.
- [ ] Leak accounting written next to the cadence constant.
