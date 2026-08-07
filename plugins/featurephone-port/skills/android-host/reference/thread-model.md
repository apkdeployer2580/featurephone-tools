# Thread model, boot, and the frame path

The host is two threads and two enums. Everything else in this skill is a consequence of
that shape. This file specifies the split, the boot sequence, the tick loop, how a frame
gets from guest memory to the screen, and how the host reads and calls into the guest.

Tags: **(A)** generic · **(B)** worked example from the reference port, re-derive it ·
**(C)** do not copy.

## Contents

- [1. Why a dedicated worker thread](#1-why-a-dedicated-worker-thread)
- [2. Deferred boot and the pre-rotation size race](#2-deferred-boot-and-the-pre-rotation-size-race)
- [3. The two enums and the design rule behind them](#3-the-two-enums-and-the-design-rule-behind-them)
- [4. The two atomics](#4-the-two-atomics)
- [5. The tick loop](#5-the-tick-loop)
- [6. Frame transport: guest RGB565 to host ARGB](#6-frame-transport-guest-rgb565-to-host-argb)
- [7. The blit](#7-the-blit)
- [8. The read closure](#8-the-read-closure)
- [9. Host-initiated guest calls](#9-host-initiated-guest-calls)
- [10. The native-stack gauge](#10-the-native-stack-gauge)
- [11. Log volume is a frame-budget problem](#11-log-volume-is-a-frame-budget-problem)
- [12. Checklist](#12-checklist)

---

## 1. Why a dedicated worker thread

**(A)** Do not run the emulator on the thread the platform hands you.

`android_main` runs on the game-activity thread. That thread has a modest, platform-chosen
stack, and an ARM-interpreter emulator wants two things it cannot supply:

- **A large by-value construction at startup.** The emulated memory object is constructed on
  the stack before being moved into place; in the reference core that is half a megabyte in
  one frame **(B)**.
- **Deep interpreter recursion.** Guest callbacks re-enter `run_function`, which saves and
  restores CPU context on the native stack. Measured nesting in the reference port topped out
  at 21 levels during warm-up **(B)** — bounded, but 21 frames of a context-saving function
  is not a small number.

So: spawn one named thread with an explicit, generous stack, move the emulator into it, and
never touch the emulator from anywhere else.

```rust
std::thread::Builder::new()
    .name("emu".into())
    .stack_size(EMU_STACK)
    .spawn(move || emulator_main(...))
```

**(B)** The reference port ships `EMU_STACK = 64 MB`, and its comment says plainly that this
is a *mitigation for an unfound stack-growth bug*, not a fix — the interpreter thread
overflowed an 8 MB stack after 21-35 minutes of play, five times, all with
`stack pointer is not in a rw map`. Thread stacks are reserved address space, so the extra
reservation costs nothing until touched, and it bought roughly eight times the observed
interval. Write that reasoning into the constant's comment; a bare `64 * 1024 * 1024` gets
"tidied" back down by the next reasonable person. See
`${CLAUDE_PLUGIN_ROOT}/shared/evidence-standard.md` §5.

Name the thread. Every native crash report on Android names the faulting thread, and
`emu` versus `main` is the difference between five minutes and an afternoon.

## 2. Deferred boot and the pre-rotation size race

**(A) Do not start the emulator in `android_main`.** Build a `PendingBoot` value — archive
bytes, storage paths, configuration — and hold it until the first `resumed` event delivers a
real window.

The reason is not tidiness. **The guest framebuffer is allocated exactly once and the guest
caches its pointer**, so the logical guest resolution must be decided before the first tick
and can never change afterwards. Deriving it from a window that does not exist yet means
either a hardcoded resolution (and letterboxing on every device) or a re-allocation the guest
will not notice.

**⚠ The race (A, CONFIRMED on device).** The *first* `resumed` can report the **pre-rotation**
window size. An app that is landscape-locked will be handed portrait dimensions once, then
corrected. Code of the form

```rust
let landscape = win_w > win_h;   // WRONG - decides from a size that is about to change
```

boots the guest at the wrong aspect and the mistake is unrecoverable for the process
lifetime.

**The fix:** derive the logical size from `(long_side, short_side)` — a rotation-invariant
pair — combined with the **persisted orientation preference**, which is host state you own
and which cannot lie. Never from the sign of `win_w - win_h`.

```rust
let (long, short) = if win_w >= win_h { (win_w, win_h) } else { (win_h, win_w) };
let (logical_w, logical_h) = if cfg.landscape { (long, short) } else { (short, long) };
```

Related lifecycle rules, all **(A)**:

- On `suspended`, release every held key (§ [input-and-hud.md](input-and-hud.md)) and set the
  paused flag *before* the platform can freeze the surface.
- On focus loss, release held keys too. Focus loss is not suspension; a notification shade
  pulled over the game leaves the app resumed and the last-touched key held forever.
- Reacquire the softbuffer surface on every `resumed`; do not cache it across suspension.

## 3. The two enums and the design rule behind them

Exactly one enum per direction. Adding a second channel in either direction is how ordering
bugs get in.

```rust
enum UserEvent {          // worker -> UI, via EventLoopProxy
    Paint(FrameBuf),      //   a completed ARGB frame (pooled, see §6)
    Hud { .. },           //   everything the overlay needs to draw, as plain values
    WidgetFrame(..),      //   a host-rendered guest widget, as pixels
    Popup(..),            //   a modal the worker decided to show
    Toast(&'static str),  //   transient text, ASCII only (see glyphs-and-text.md)
}

enum InputEvent {         // UI -> worker, via mpsc::Sender
    Key { code, down },
    PopupConfirm, PopupCancel,
    <one variant per UI affordance whose meaning the worker must resolve>
}
```

> **★ The design rule (A).** *Anything that needs a guest-memory read to decide must be
> decided on the worker. The UI only reports the tap.*

The UI's copy of guest state is as old as the last `Hud` publish — up to ~80 ms **(B: at a
5-frame publish cadence and a 16 ms pace)**. A stale read is harmless when it only chooses a
colour. It is a bug when it chooses **which guest function runs**: the player taps a slot, the
UI's 80 ms-old snapshot says the slot holds item A, the worker invokes "use item A", and the
guest is now holding item B.

So the `InputEvent` variants are deliberately *dumb*: `SlotTapped(3)`, not `UseItem(A)`. The
worker re-reads the slot at the moment it acts.

Corollaries:

- `Hud` carries **plain owned values**, never guest pointers. A pointer that crosses to the UI
  thread is a pointer that will be dereferenced after the guest freed it.
- The UI never calls `read_memory`. It has no handle to the emulator; enforce that by
  construction, not by convention.
- Every `UserEvent` payload must be cheap to construct at its cadence. `Hud` is built every
  few frames; if building it costs a dozen guest reads, publish it less often, do not make the
  reads lazy.

## 4. The two atomics

Two things do not fit the enums, and both are `Arc<Atomic*>` shared directly.

**`paused: Arc<AtomicBool>`** — set by the UI on suspend/lock, read at the top of the worker
loop. When set, the worker drains and **discards** pending input, clears its held-key set,
sleeps ~50 ms and continues. This is what stops audio and battery burn when the phone is in a
pocket. Discarding rather than buffering is deliberate: a queue of taps replayed on resume is
always wrong.

**A packed geometry word: `Arc<AtomicU32>`** — the one place the UI's layout arithmetic
reaches the worker. **(A) The pattern is: the UI owns layout, the worker owns guest memory,
and they cross on a single atomic carrying packed integers.** The worker unpacks it on its
poke pass and writes the values into the guest.

```
[31:24] widget shift   [23:16] band height   [15:8] band x   [7:0] y origin
```

**(B)** Those four fields are what one particular game's HUD needed. The generic part is the
packing: four `u8`s in one `u32` means the worker can never observe a half-updated geometry,
with no lock and no channel. If you need more than four bytes, use a second atomic rather than
a mutex — a torn read between two atomics costs one frame of a misplaced widget; a mutex on
the tick path costs the frame budget.

## 5. The tick loop

**(A)** One iteration per displayed frame. The order matters; the cadences are yours to tune.

| step | what | note |
|---|---|---|
| 0 | **paused gate** | drain-and-discard input, clear held keys, sleep ~50 ms, `continue` |
| 1 | **redraw request** | `should_redraw.swap(false)` -> feed the emulator a redraw event |
| 2 | **drain input** | `input_rx.try_iter()` -> apply; this is where the worker resolves tap meanings |
| 3 | **key auto-repeat** | re-send keydown for every held key every ~100 ms **(B)** |
| 4 | **adaptive multi-tick** | see below |
| 4b | **host cadence work** | pokes, save/backup polling, HUD publish, widget render, gauges |
| 5 | **pace** | sleep so the whole iteration lands on the frame period |

**Adaptive multi-tick (A) — the correct shape for a cooperative-interpreter emulator.**

```rust
let deadline = Instant::now() + FRAME_BUDGET;
loop {
    let t0 = Instant::now();
    emulator.tick()?;                       // one cooperative scheduler pass
    let took = t0.elapsed();
    if took < TICK_FULL || Instant::now() >= deadline { break; }
}
```

A WIPI/MIDP guest **idles on a timer**: it arms a callback and yields. So a *fixed* tick count
either starves the guest on a busy map (it had work queued and you gave it one pass) or burns
CPU on a quiet one (it had nothing to do and you called it eight times). "Keep ticking while
the last tick was expensive, stop at a hard wall-clock deadline" adapts to both, and the
wall-clock cap is what keeps input latency bounded when the guest is genuinely saturated.

**(B)** The reference port uses `TICK_FULL_MS = 6`, `FRAME_BUDGET_MS = 24`, `FRAME_PACE = 16`.
Those came from measuring one title's tick shape; a title that rasterises its world in guest
code — the reference measured about **2.5 M guest instructions per rendered frame** on a
headless run — has a very different profile from a sprite-blitting one. Measure yours with the
probe's work gauge before picking numbers.

**Cadences (B, all tunable):** guest pokes re-asserted every 30 frames; save-chain polling
every frame (it is a state machine, it must not miss a completion); HUD publish every 5
frames; modal-popup scan **every frame** (a modal the host misses for 80 ms is a modal the
player taps through); host-rendered widget every 15 frames; native-stack gauge every 1800.

If `emulator.tick()` returns `Err`, log it with the error's `Display` and **stop the loop**.
A tick error is `Unimplemented`, `InvalidMemoryAccess`, `AllocationFailure` or `FatalError` —
all of them mean the guest's state is no longer trustworthy. Continuing produces a second,
misleading error at an unrelated address. That single logged line is the whole entry point
into the `emulator-core` skill.

## 6. Frame transport: guest RGB565 to host ARGB

The `Screen::paint` implementation runs on the worker. Do three things and nothing else.

**(1) Convert in one pass, with lookup tables.** Guest framebuffers are RGB565. The host
surface wants `0xAARRGGBB`. Do not materialise an intermediate `Vec<Color>` (16 bytes per
pixel); expand the 5- and 6-bit channels with two small `const` tables:

```rust
const EXPAND5: [u32; 32] = /* generated: (v << 3) | (v >> 2) */;
const EXPAND6: [u32; 64] = /* generated: (v << 2) | (v >> 4) */;
```

**⚠ Generate them to match the core's own `to_color` bit-for-bit.** If the host rounds
`v * 255 / 31` and the core shifts, host screenshots and probe frame dumps differ by one LSB
per channel and every pixel-diff you ever run reports noise. That is a real cost: differential
verification (see [guest-widgets.md](guest-widgets.md)) depends on the two being comparable.

**(2) Recycle the buffer.** At 25+ fps a few hundred kilobytes per frame of alloc/free is
pure waste and shows up as jitter, not as throughput. Use a **two-deep process-global pool**:
the worker takes a buffer, fills it, sends it in `UserEvent::Paint`; the UI blits from it and
**hands it back**. Two is the right depth — one in flight, one being filled. Cap it: if the UI
is behind and both are out, allocate a third and drop it on return rather than blocking the
worker.

**(3) Send, do not draw.** The worker must never touch the window. `proxy.send_event(...)`.

## 7. The blit

Nearest-neighbour scaling from the guest framebuffer to the window, on the UI thread. Naive
code does two integer divides per destination pixel; at a modern phone resolution that is
about **2.5 million divides per frame (B)** and it is the single most expensive thing the UI
thread does. Three optimisations, all **(A)**, all worth having from day one:

**Column LUT.** `sx_lut[dx] = dx * guest_w / logical_w`, computed once and cached, keyed on
`(guest_w, logical_w)`. Invalidate on resize and on a zoom change. This removes the horizontal
divide entirely.

**Vertical run copying.** When scaling up, consecutive destination rows map to the *same*
source row roughly `(scale - 1) / scale` of the time — about 90% at typical phone scales. So
gather a row once, then `copy_within` it for every following destination row that resolves to
the same source row. Ten percent of rows do real work.

```rust
let mut prev_sy = usize::MAX;
let mut prev_off = 0usize;
for dy in y0..y1 {
    let sy = dy * guest_h / logical_h;
    let off = dy * pitch + x0;
    if sy == prev_sy {
        dst.copy_within(prev_off..prev_off + run_len, off);   // no gather, no LUT walk
    } else {
        for dx in 0..run_len { dst[off + dx] = src[sy * guest_pitch + sx_lut[dx]]; }
        prev_sy = sy; prev_off = off;
    }
}
```

**Partial clear.** Do not `fill(0)` the whole surface every frame. The game rectangle is
completely overwritten; only the letterbox bars and margins need clearing, and only when the
layout changed. Clearing a 1080x2340 surface you are about to overwrite is a wasted 10 MB of
writes per frame.

Trap: `copy_within` needs the source and destination ranges in the same slice and takes
`Range` + destination index in that order. Getting it backwards produces a screen that looks
like a smear and reads like a scaling bug.

## 8. The read closure

**(A) Every host-side read of guest memory goes through one closure shape.**

```rust
let read = |addr: u32, len: usize| emulator.read_memory(addr, len);
let hp = model::hp(&read);
```

This one line is the reason the whole guest-memory model is testable. Every parsing function
takes `&impl Fn(u32, usize) -> Vec<u8>` and is therefore **pure**: the app passes the
emulator, the selftest passes a map-backed reader.

```rust
#[test]
fn resolves_the_god_object_through_two_hops() {
    let mut m = HashMap::new();
    m.insert(SLOT, 0x4010_0000u32);       // static slot -> heap
    // ...
    let read = |a: u32, n: usize| synth(&m, a, n);
    assert_eq!(model::god_object(&read), Some(0x4010_0000));
}
```

Consequences to enforce:

- The parsing modules stay **dependency-free** so a standalone test can `include!` them —
  `${CLAUDE_PLUGIN_ROOT}/shared/selftest-convention.md`.
- `read_memory` returns an owned `Vec`. A read of a bad address must return short or empty,
  never panic; every parser therefore checks length before indexing and returns `Option`.
- **Validate every pointer hop.** A stale handle must yield `None`, not a parse of garbage.
  See [guest-widgets.md](guest-widgets.md#pointer-hop-validation).

`read_memory` / `write_memory` / `call_guest_function` / `call_guest_function_out` are
**additions to the vendored core**, with defaults that return empty / `false` / fail-the-result
so a backend without an ARM core degrades instead of hanging. If they are not on your
`Emulator` trait yet, that is an `emulator-core` task.

## 9. Host-initiated guest calls

**(A) A guest call is not a function call.** `call_guest_function` clones the ARM core, spawns
a cooperative task and runs the target **on its own ARM thread context with a fresh stack** —
the same mechanism WIPI callbacks use. It executes on the *next* `tick()`, not inline. The
host therefore polls a result cell, and a chain of dependent calls is a **state machine
advanced once per frame**, not a sequence.

Five rules, each learned from a real failure:

1. **★ The JVM thread must be attached.** The very first host-originated guest call in the
   reference port **panicked on device** with `Option::unwrap()` on a `None` value inside the
   JVM. The JVM keys per-thread state by *executor task id*, and a freshly spawned task has an
   id the JVM has never seen. Platform callbacks were always wrapped in attach/detach;
   host-originated calls were not — so the feature had never once worked on hardware. The fix
   is an RAII guard around every host-originated call, attaching on entry and detaching in
   `Drop` so error paths cannot leak an entry. Details and the core-side patch:
   `emulator-core`. **Check this before you ship your first host-initiated call.**
2. **Three-state results, never two.** `pending` / `ok` / `failed`. Treating a failure as "not
   yet" hangs the machine forever; treating "not yet" as a failure abandons a call that was
   about to succeed.
3. **Timeout every step**, generously — the reference used ~240 polls per step, about 100x the
   expected latency — and reset the timer per step. A call that never lands must not wedge the
   host.
4. **Gate on the game's own precondition, not one you invented.** If the guest exposes a
   "can I do this?" byte, read it. The host should refuse in exactly the places the game
   refuses; any other rule diverges from the guest the first time you are wrong about state.
5. **One job at a time.** Two overlapping chains that both allocate in the guest will leak the
   first buffer and can interleave two records.

**⚠ Prove the control is reachable in a state that passes the gate.** In the reference port a
"save now" button was placed in a panel whose entry point only exists outside field mode,
while the save gate only passes *inside* field mode. The button was correct, the gate was
correct, and it printed "cannot save here" every single time. When a feature has a state gate,
test the reachability of the *control*, not only the correctness of the gate.

## 10. The native-stack gauge

**(A) Measure the thing that overflows, from outside the code that uses it.**

The reference port's stack-overflow hunt instrumented three plausible layers first — interpreter
wrapper poll nesting (constant 1 by construction), `run_function` nesting (peaks at 21 and
stops), executor task count (never above 8) — and all three were the wrong layer. The gauge
that could not be wrong asks the kernel:

```rust
// parse /proc/self/smaps, find the mapping containing the current sp, report its Rss:
fn stack_rss_kb() -> Option<u64>;
```

Committed stack pages are never decommitted, so `Rss` of the stack mapping is an exact
high-water mark for the run. It assumes nothing about which function repeats. Sample it on a
slow cadence (every ~1800 frames) and **log only on a new maximum** — a healthy session then
prints a handful of lines and a leaking one prints a rising staircase.

The equivalent on a Windows dev host is `VirtualQuery` on the thread's own stack base,
reporting how much is still `MEM_RESERVE`. Put both behind one `reserved_bytes()` function;
the *idea* is portable, the implementations are not **(C)**.

## 11. Log volume is a frame-budget problem

**(A)** Every `__android_log_write` from inside the tick loop is a syscall inside your frame
budget. The reference port measured **124 log records per second** from a per-frame path, and
separately found a platform timer callback annotated at INFO firing ~46 times a second. Both
were real, measurable frame cost.

Two defences:

```rust
// 1. a restrictive default filter, with your own crate raised
android_logger::init_once(Config::default().with_filter(
    FilterBuilder::new().parse("warn,<your-lib-name>=info").build()));
```

```toml
# 2. compile the noisy levels out of the whole graph in release
log     = { version = "*", features = ["release_max_level_info"] }
tracing = { version = "*", features = ["release_max_level_info", "log"] }
```

`tracing`'s `log` feature matters: parts of a wie-family core log through `log` and parts
through `tracing`, and switching only one leaves half the flood in place. Audit every
`#[tracing::instrument]` and `info!` on a per-frame path before shipping.

## 12. Checklist

- [ ] Emulator runs on its own named thread with an explicit stack; the constant carries its
      symptom, measurement and date.
- [ ] Boot is deferred to the first `resumed`; the logical size comes from
      `(long, short)` + the persisted orientation, never from `win_w > win_h`.
- [ ] Exactly one enum per direction; no third channel; no guest pointer crosses a thread.
- [ ] Every decision needing a guest read happens on the worker.
- [ ] Tick loop has a paused gate, adaptive multi-tick with a hard wall-clock cap, and a pace.
- [ ] A tick error stops the loop and is logged once.
- [ ] RGB565 expansion tables match the core's conversion bit-for-bit.
- [ ] Frame buffers are pooled and handed back.
- [ ] Blit has a column LUT, vertical run copying and a partial clear.
- [ ] All guest reads go through one closure; parsers are pure and dependency-free.
- [ ] Host-initiated guest calls attach the JVM thread, poll a three-state cell and time out.
- [ ] Native-stack gauge present, logging only on a new maximum.
- [ ] Release log filter set and `release_max_level_*` enabled on both logging facades.
