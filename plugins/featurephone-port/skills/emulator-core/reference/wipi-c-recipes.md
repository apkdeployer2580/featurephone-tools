# WIPI-C: discovering, stubbing and implementing methods

The WIPI C API is the surface where a real title diverges from the emulator's test corpus. This is
the only one of the six boot failures whose *content* is title-specific — the method is not.

## Contents

1. [How a missing method presents](#1-how-a-missing-method-presents)
2. [`trace_stub` — the highest-value technique in this skill](#2-trace_stub--the-highest-value-technique-in-this-skill)
3. [Recipe: add a WIPI-C function (KTF)](#3-recipe-add-a-wipi-c-function-ktf)
   - [3a. Range-dispatch a whole block when the runtime crate names only a few slots](#3a-range-dispatch-a-whole-block-when-the-runtime-crate-names-only-a-few-slots)
4. [Table padding, or the guest branches to address 0](#4-table-padding-or-the-guest-branches-to-address-0)
5. [Argument mapping and the `MethodBody`-not-closure constraint](#5-argument-mapping-and-the-methodbody-not-closure-constraint)
6. [Worked example: the network table](#6-worked-example-the-network-table)
7. [Worked example: the record database](#7-worked-example-the-record-database)
   - [7a. The same interface can destroy saves, and that outranks every read-path bug](#7a-the-same-interface-can-destroy-saves-and-that-outranks-every-read-path-bug)
8. [Worked example: audio clip volume, and "the handle changes under you"](#8-worked-example-audio-clip-volume-and-the-handle-changes-under-you)
9. [Return-value honesty](#9-return-value-honesty)

---

## 1. How a missing method presents

The KTF method table's stub generator returns
`Err(WieError::Unimplemented("<table_id>: <name>"))` for any slot with no Rust body. That error
propagates out of `System::tick`; the host loop logs it and stops the emulator.

So the presentation is: **frozen or black screen, plus exactly one log line naming the table and
the slot.** Nothing else. That single line is the entire porting workflow for this stage — grep for
`Unimplemented` and you have your next task.

Three failure shapes that look similar but are not:

- **Unknown interface at boot.** The loader's `get_interface(name)` knows a small fixed set of
  names and returns **0 with a warning** for anything else. The guest then dereferences a null
  pointer and dies far from the cause. Grep the boot log for that warning before anything else.
- **Unknown SVC category.** `Unknown SVC handler category: N` — the guest issued a supervisor call
  in a category no handler claims. Different layer, different fix.
- **A whole functional block that is fatal by construction.** This shape appears the moment you boot
  a **second carrier's** runtime crate. That crate may name only a handful of slots in an entire
  functional block and route every other id through a `TryFrom<u16>` whose `_ =>` arm returns
  `FatalError`. There is then **no `Unimplemented` line, no null branch and nowhere to hang a trace
  stub** — the emulator exits at the first call into that subsystem, i.e. the instant the game opens
  a socket. The report you receive is *"the network doesn't work on the new build"*; what happened is
  that the emulator stopped. Fix: [§3a](#3a-range-dispatch-a-whole-block-when-the-runtime-crate-names-only-a-few-slots).

## 2. `trace_stub` — the highest-value technique in this skill

**(A)** Do not implement the missing method first. Replace the fatal stub with a **logging no-op
that returns a chosen plausible value**, so the guest keeps walking forward and the *whole*
subsequent call sequence lands in the log — including the calls you would never have known to
expect.

```rust
/// A logging no-op WIPI-C method. Logs the raw argument words plus a hex+ASCII peek
/// of any argument that plausibly points at the image or the heap, then returns
/// `ret` so the guest keeps executing.
pub struct TraceStub {
    pub name: &'static str,
    pub ret: i32,
}

#[async_trait::async_trait(?Send)]
impl MethodBody<WieError, dyn WIPICContext> for TraceStub {
    async fn call(&self, context: &mut dyn WIPICContext, args: Box<[WIPICWord]>) -> Result<WIPICWord> {
        // the KTF dispatcher always hands over a fixed word count; extra words are garbage,
        // but printing them is how you discover the real arity
        let mut s = String::new();
        for (i, a) in args.iter().enumerate() {
            s.push_str(&format!(" a{i}={a:#x}{}", peek(context, *a)));
        }
        tracing::info!("WIPI {}({} ) -> {}", self.name, s, self.ret);
        Ok(self.ret as WIPICWord)
    }
}

/// Only dereference an argument that could be a pointer. Gate on the two known
/// address bands so a small integer argument cannot fault the emulator.
fn peek(context: &mut dyn WIPICContext, v: WIPICWord) -> String {
    const IMAGE: Range<u32> = 0x0010_0000..0x0040_0000;      // (B) your loader's base + size
    const HEAP:  Range<u32> = 0x4000_0000..0x4080_0000;      // (B) your heap's base + a sane window
    if !IMAGE.contains(&v) && !HEAP.contains(&v) { return String::new(); }
    let Ok(b) = context.read_bytes(v, 16) else { return String::new() };
    format!(" [{}|{}]", hex(&b), ascii(&b))
}
```

**Rules for the peek helper (A):**

- **Both bands are yours, and both are per build.** The image band is the window your loader passes
  to `core.load()` ([address-model.md](../../guest-re/reference/address-model.md) §2, and §9 for why
  a section-mapped build's `.text` can start three orders of magnitude lower — a band written for the
  other build refuses every valid pointer, and a refusal reads exactly like "the feature is not
  available here"). The heap band is the `map()` call that creates the guest heap;
  [memory-footprint.md](memory-footprint.md) §3 is the method that finds it.
- **Plausibility-gate every dereference.** An ungated peek turns a WIPI call taking an integer into
  an `InvalidMemoryAccess` and you have replaced one fatal error with another.
- Print **both** hex and ASCII. Half of what these APIs take is a C string, and the ASCII column is
  what lets you recognise it instantly.
- Print the raw words too, not just the decoded peek — the argument you did not decode is the one
  that turns out to matter.

**Then, and only then**, confirm each discovered call against the guest disassembly (`guest-re`) so
you know the *semantics*, not just the sequence. Keep the trace stub in place for the slots you
still do not understand; a running game producing a log is worth more than a correct
implementation of one slot.

### The rule inverts for release-side calls

**(A)** `trace_stub` is right for **query** and **set-side** calls, where *some* return value keeps
the guest walking. It is dangerous for **release-side** calls — cancel, free, close, stop, unset —
because there is no plausible return value to choose: the thing that matters is the **omitted side
effect**. A no-op release paired with an implemented acquire **accumulates**, and the symptom then
appears nowhere near the stub.

> **REFUTED.** *Previously documented (this file §2 and §7, and the router's discovery loop):* "Do
> not implement the missing method first. Replace the fatal stub with a logging no-op that returns a
> chosen plausible value" — stated without exception, with the one release-side instance in this file
> ("name-keyed record delete was a no-op upstream") filed as a quirk of that interface rather than as
> the rule it is. **Refuted by** a shipped speed bug: the platform's timer-**cancel** call was a
> logging no-op while the timer-**set** call *pushed* a new event instead of replacing the keyed one,
> so a visit to a screen that arms its own timer left a live timer behind. (⚠ That the callback
> therefore ran once per live timer, accumulating per visit, is derived from the two primitives and
> was never observed —
> [guarding-destructive-operations.md](../../../shared/guarding-destructive-operations.md) §8.) The player's report was *"the game speeds up after connecting to
> the network"*, and it was first misattributed to an unrelated clamp change. **Why the wrong reading
> was attractive:** a no-op looks *neutral* — it returns success, nothing faults, and the call appears
> in the trace — so it reads as "handled" in exactly the log you are using to decide what is handled.
> **Consequence if carried forward:** an unbounded leak whose symptom (game speed, memory, socket
> count, audio slots) has no textual link to the stub, and which grows with the player's **route
> through the game** rather than with time — so it does not reproduce on your test path.

**(A) The audit, and its verification.** For every `set` / `alloc` / `open` / `start` you implement,
find its `unset` / `free` / `close` / `stop` in the same table and implement it in the same sitting.
Where you cannot, record it as a **known leak with a named symptom**, never as a neutral placeholder.
The verification is a **round trip**: enter the screen that acquires, leave it, come back, and confirm
the resource count — **live** timer objects, live handles, open sockets, live clips — is identical
to before. Doing that once per subsystem is cheaper than one misattributed speed bug. The specific
timer case, including why arming must *replace* a keyed shot rather than push one, is
[frame-pacing.md](frame-pacing.md) §8–§9.

## 3. Recipe: add a WIPI-C function (KTF)

Three or four places must agree, because the guest reads a function pointer **by struct offset** out
of a table you allocate. Get one wrong and you get an unmapped-PC fault, not a compile error.

1. **The struct field** — `wipi_types::ktf::wipic::WIPIC<Category>Interface`. **Field order is the
   ABI.** Never insert in the middle; append, or you move every later slot.

2. **The id enum** — `wie_ktf/src/runtime/svc_ids.rs`
   (`WIPICKernelMethodId`, `WIPICGraphicsMethodId`, `WIPICDatabaseMethodId`, `WIPICTableId`),
   including its `TryFrom<u16>` arm.

3. **Two registration sites** in `wie_ktf/src/runtime/wipi_c/method_table.rs`:

```rust
// (a) allocate the SVC stub that is written into the guest's table
fn get_graphics_interface(core: &mut ArmCore) -> Result<WIPICGraphicsInterface> {
    Ok(WIPICGraphicsInterface {
        // ... existing fields, in ABI order ...
        foo: core.make_svc_stub(
            SVC_CATEGORY_WIPIC,
            WIPICTableId::Graphics.function_id(WIPICGraphicsMethodId::Foo),
        )?,
    })
}

// (b) dispatch to the Rust body
fn get_method_body(table_id: WIPICTableId, function_id: u16) -> Option<Box<dyn MethodBody<..>>> {
    match (table_id, function_id.try_into().ok()?) {
        // ...
        (WIPICTableId::Graphics, WIPICGraphicsMethodId::Foo) => Some(graphics::foo.into_body()),
    }
}
```

   Tables that are **positional `Vec`s** (util, misc, uic, media, net, and the numbered generic
   interfaces) need only the vec entry — but the *position* is the ABI. Pin the positional prefix
   with an assertion next to the table:

```rust
debug_assert_eq!(table.len(), EXPECTED_PREFIX_LEN);
```

4. **The body**, in `wie_wipi_c/src/api/<category>.rs`:

```rust
pub async fn fill_rect(
    context: &mut dyn WIPICContext,
    dst_fb: WIPICIndirectPtr,
    p_gctx: WIPICWord,
    x: i32, y: i32, w: i32, h: i32,
) -> Result<()> {
    let framebuffer = FrameBuffer(read_generic(context, context.data_ptr(dst_fb)?)?);
    let gctx: WIPICGraphicsContext = read_generic(context, p_gctx)?;
    let mut canvas = if framebuffer.fill_covers_band(x, w) {
        framebuffer.canvas_rows_uninit(context, y, h)?   // skip the read-back entirely
    } else {
        framebuffer.canvas_rows(context, y, h)?
    };
    // ... draw ...
    Ok(())
}
```

Return `Result<i32>` / `Result<u32>` / `Result<()>`; `.into_body()` (the `MethodImpl` trait) does
the wrapping.

### 3a. Range-dispatch a whole block when the runtime crate names only a few slots

**(A)** When a second carrier's runtime names three slots out of a block of thirty-odd and sends every
other id to a fatal `_ =>` arm (§1, third shape), **the bodies are not what is missing.** The WIPI-C
bodies live in the shared crate and are carrier-agnostic; what is missing is a **slot-number
mapping**. Three rules:

1. **Dispatch the block by range *before* the id→enum conversion.** Any id that reaches `try_from` is
   fatal by construction, so a `match` arm added *after* it can never run — the fix looks applied and
   changes nothing. The shape is
   `if (BASE..BASE + SLOTS).contains(&id) { return block_body(id - BASE); }`, placed above the
   conversion.
2. **Reuse the other carrier's table at the same positional index.** Position is the ABI (§3), and
   both runtimes front the same shared bodies.
3. **Prove the base; do not assume it.** Take the slots the crate *does* name, subtract the base you
   propose, and check each lands on the semantically matching call in the other carrier's table. Two
   adjacent agreements prove nothing — you need a **non-adjacent** third point to rule out an
   accidental fit.

**(B)** In the reference port the second carrier named three network slots. They resolved to
`base + 0`, `base + 1` and `base + 6`, and index 6 in the first carrier's table was exactly the
matching call — the non-adjacent point — so `NET_SVC_BASE = 0x258` with 34 slots was not a fit to two
data points. `HIGH / CONFIRMED`: before the change the emulator exited the instant the game opened a
socket; after it, the device log carries the entire connect sequence. Carrier-to-carrier work in
general is [multi-build.md](../../guest-re/reference/multi-build.md); which loader each carrier uses
is [platform-matrix.md](../../port-triage/reference/platform-matrix.md).

## 4. Table padding, or the guest branches to address 0

**This is the trap that produces the least legible failure in the whole skill.**

The guest reads the function pointer *straight out of the table you allocated in guest memory*, by
offset. If the guest indexes a slot past the end of your table, it reads whatever bytes follow your
allocation — typically zero — and **branches to it**. You get an unmapped-PC fault at an address
with no relationship to the call, not a clean `Unimplemented`.

Upstream ships tables sized to what upstream's own test corpus exercises. A real title indexes
further. **Pad every positional table well past the highest slot you have seen**, and pad the
`WIPIC*Interface` struct in `wipi_types` to match:

```rust
/// Upstream shipped 30 entries. A real title indexed slot 30. The guest reads the
/// pointer by offset, so a missing tail entry reads as 0 and is BRANCHED TO.
/// Pad generously; an unused padded slot costs one word and yields a clean
/// Unimplemented instead of a fault at an unmapped PC.
const NET_TABLE_SLOTS: usize = 64;
```

Fill the padding with `gen_stub`-style entries (fatal, named) rather than nulls — a fatal error that
names the slot is a bug report; a null is a crash.

## 5. Argument mapping and the `MethodBody`-not-closure constraint

- Arguments map positionally: r0..r3, then the stack. The KTF dispatcher always hands over a fixed
  word count (nine on the reference core), so `args.len()` tells you nothing about the real arity —
  the trace log does.
- **If your body needs `&mut dyn WIPICContext` across an `await`, you must write a `MethodBody`
  impl by hand.** The closure form does not compile: the returned future would have to hold the
  trait-object reference, and `dyn Trait` is invariant, so the borrow cannot be expressed. This is
  why `trace_stub` above is a struct with an `impl`, not a closure — it is not a style choice.

## 6. Worked example: the network table

Everything in this section is **(B)** — the *method* transfers, the slot numbers and values do not.

Shipping `trace_stub` on every unimplemented net slot produced, in the log, the title's actual
network bring-up sequence:

```
fd   = net[2](2, 1)                        ; two args: address family, socket type
addr = inet_addr("<server host>")          ; parsed by an in-game routine, not by WIPI
port = util[1] htons(<port>)
rc   = net[30](fd, addr, (s16)port, "<channel name>", ptr, fd)
; rc < 0 && rc != <would-block sentinel> -> error path
```

Slots eventually implemented: **2** (socket), **13** (set read callback), **30** (named connect),
**31** (send), **32** (recv), **33** (close). Two structural findings that **do** generalise:

- **Upstream's slot names for the slots nobody exercised are guesses, not a contract.** This title
  did not use the slot upstream calls "connect" to connect, and its teardown was slot 33 then slot
  1, not upstream's slot 6. *Trace the real contract; do not read the names and assume.*
- **The table had to be padded** (§4). Upstream shipped 30 entries; the guest indexed slot 30.

**Both findings are stronger per carrier than they look here.** The *same game* on a second carrier
runtime used a **different slot in the same block for the same operation** — one build connects
through the block's named-connect slot, the other through slot 3 with a different argument shape —
and it also called two slots in an entirely different id group. The rule that follows is blunt:
**a per-carrier port re-traces the whole call sequence for every subsystem it touches.** None of the
sequence is a property of the game.

**(A) The cheapest way to enumerate the platform surface is the guest's own import table, not a
device log.** On a linked, section-mapped image the platform imports are a table of fixed-size call
stubs in a data section; dumping it lists **every** platform slot the guest can reach — statically,
with no device round trip and no need to reach the game state that exercises it. **(B)** In the
reference port each stub was 16 bytes
(`str lr,[sp,#-4]!` / `bl <resolver>` / `.word <table>` / `.word <index>`), the dump gave all 64
imports at once, and two unknown ids in a 900-range group decoded to `htons` and `inet_addr`. It was
cross-checked, not guessed: one entry decoded to an id the device log had already shown being used to
connect. `HIGH / CONFIRMED`. Your static tools can only see that section if they read the section
headers instead of hardcoding one section's file-offset-to-vaddr mapping —
[static-toolkit.md](../../guest-re/reference/static-toolkit.md).

Server-side work — framing, request discovery, replies — belongs to the `server-revival` skill.
This skill owns only the socket surface.

## 7. Worked example: the record database

**Symptom (device-confirmed).** Every save slot read as empty; valid saves were rejected.

**Method (A).** Disassemble the guest's slot-reading routine and find *what it gates on*. Here it
called `select(handle, 0, mode=2)` and branched on the return being **greater than zero**
(a `cmp`/`bgt` pair at guest `0x94dda` **(B)**) to decide whether the slot held data. Upstream's
implementation returned 0 for that mode, so the answer was always "empty".

**Fix.** On `mode == 2`, return `handle.buffer_len`.

Two more from the same interface, both **(A)** as method:

- A neighbouring unknown slot's return value was a **dead store** in the guest — written at one
  address and overwritten a few instructions later, before any read (`0x94dfc` overwritten at
  `0x94e1e` **(B)**). Proving the store dead is what licensed returning a benign value (the record
  length) instead of guessing at semantics.
- Two further unknown slots were converted from fatal `Unimplemented` to **logging no-ops returning
  0**. Label this honestly in the code: *"we never observed the return being consumed"* is not the
  same claim as *"the contract is 0"*. Anyone who later sees odd behaviour needs to know which one
  you meant.
- **Name-keyed record delete was a no-op upstream**, so "reset save" left the old record in place
  and the supposedly-empty slot reloaded it on the next boot. Check delete, not just read/write. This
  is one instance of the release-side rule in §2, not a quirk of this interface.

### 7a. The same interface can destroy saves, and that outranks every read-path bug

Everything above **hides** a save. The three defects below **delete** one, they were all in the same
upstream file as the size-query bug, and on a real port they are what you fix first.

1. **Create-mode open must not truncate up front.** Upstream deleted the record the moment the guest
   opened the database in create mode — so the character did not exist on disk for the entire width of
   the guest's `open` → `write` → `close` sequence, a window that a kill, a crash or a background
   death lands inside. **Defer the truncate to the first successful write**, carried as a per-handle
   flag, and execute it at `close` only if no write ever landed.
2. **Every failure path must disarm the pending truncate.** With the flag cleared only on success, and
   a guest writer that closes unconditionally without reading the return value, **every failed write
   became a delete.** Each early return in the write function has to cancel it explicitly; "the
   function returns an error" is not enough, because nobody reads the error.
3. **Propagate the store's verdict.** Upstream discarded the store's success flag, so a failed save
   was reported to the guest as a success — the one state in which the player is guaranteed not to
   retry.

Add two rules alongside (1): a create-mode open must not truncate a database **seeded from a packaged
read-only archive member**; and every create-mode open and every executed truncate is logged at
**warn** with the mode in the line, because these are precisely the events you will want *dated* when
a report arrives weeks later.

**And the framing at the top of §7 is wrong in one important way (A).** A guest can only report
"empty" for a record that is **missing, zero-length, or checksum-failed** — a slot-summary builder has
no way to express *failure*. So **"reads as empty" is a deletion report, not a read-path report**,
unless the slot was empty on the very first boot. Split the symptom:

| What the player says | Class | Where |
|---|---|---|
| Every slot reads as empty from boot 1, for a save that never existed | read path — the size-query mode above | §7 |
| A slot that **used to work** now reads as empty | **deletion** — a create-mode truncate, a truncate left armed by a failed write, or a non-atomic replacement | [save-integrity](../../save-integrity/SKILL.md) |

The host-side record store, the pre-image vault, the write/delete policy gate and the load-observation
diagnostics are the [save-integrity](../../save-integrity/SKILL.md) skill's subject. This file owns
only the WIPI-C surface they sit under — and that surface is where three of the four irreversible
mechanisms live, so do not treat it as merely an API-completeness chore.

## 8. Worked example: audio clip volume, and "the handle changes under you"

**Symptom.** The in-game volume control does nothing.

The stub was a no-op — but the naive fix (cache volume keyed by clip handle in set-volume, apply it
in play) still fails, and fails *subtly*: the volume lands on the **previous** clip.

**Mechanism (A, fully generic).** The data-upload call mints a **new handle** between set-volume
and play. A handle-keyed cache therefore keys on a handle that no longer identifies the clip.

**Fix (A).** Key the cache on the **stable guest clip pointer**, and re-bind it to the playing
handle inside `play`. Whenever you cache state across two WIPI calls, ask *which identifier in this
pair is stable across the calls between them* — the answer is frequently not the handle the API
hands you.

**That fix makes the control *do* something; it does not make it correct.** Three further things
decide whether one chosen level means the same thing on both sound buses, and all three are
core-side:

1. **One volume law, defined once and shared by every bus**, applied as an **output gain** and
   **curved** (square a normalised level) — a linear map spends nearly its whole perceptual range in
   the top step.
2. **Never apply the level inside the synthesis domain.** Scaling note velocity on a sequenced bus
   runs the level through the instrument bank's concave velocity-to-attenuation modulator (roughly
   double the dB drop) and additionally selects sample layers and moves filter cutoff — so lower
   settings become *duller* as well as quieter, on a curve unrelated to the PCM bus.
3. **Let the sink declare which layer applied the level** — a defaulted trait method returning
   `bool` — so a sink that hands events to an external synth keeps its old path and nothing applies
   the level twice.

**(B)** The reference port's first shipped volume fix did exactly the wrong thing here (linear on the
PCM bus, velocity on the sequenced one) and the two buses measured **27–39 dB apart at one setting**.
The law, the prohibition and the capability probe are [audio-core.md](audio-core.md) §4–§6; the
player-facing level UI and the mixing above it are
[audio-mixing.md](../../android-host/reference/audio-mixing.md).

## 9. Return-value honesty

`trace_stub` return values are **(C)** — they are tuned to keep one specific guest walking forward.
Do not copy them; derive yours from what the guest does with the value.

The reference port shipped one deliberate lie worth understanding as a *pattern*: its connect
completion callback reported **success** where the historical platform returned an error, purely
because with an error the guest never advanced past network bring-up and no further network slot
was ever exercised — "which is exactly what hid the call sequence until now". That is a legitimate
discovery tactic. It is not a legitimate shipping state. When you replace a lie with the truth,
grep for every other stub value you chose during discovery and re-derive it.
