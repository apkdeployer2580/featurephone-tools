# Boot order: the six failures and the full triage table

Booting a real commercial WIPI title on a stock `dlunch/wie` build fails in a **predictable
order**. Each failure hides the next, so the sequence below is also the work plan. Only failure #2
is title-specific, and even then only its *values* are — the discovery method is mechanical.

## Contents

1. [The sequence, with exit criteria](#1-the-sequence-with-exit-criteria)
2. [#1 Black screen — wall-clock `now()`](#2-1-black-screen--wall-clock-now)
3. [#2 Fatal WIPI-C stub](#3-2-fatal-wipi-c-stub)
4. [#3 JVM thread-attach panic](#4-3-jvm-thread-attach-panic)
5. [#4 Full-framebuffer round-trip per draw call](#5-4-full-framebuffer-round-trip-per-draw-call)
6. [#5 List allocator never coalesces](#6-5-list-allocator-never-coalesces)
7. [#6 Reporting an allocation failure allocates](#7-6-reporting-an-allocation-failure-allocates)
8. [Boot mechanics you must understand to read a boot failure](#8-boot-mechanics-you-must-understand-to-read-a-boot-failure)
9. [`WieError` variants and where each surfaces](#9-wieerror-variants-and-where-each-surfaces)
10. [Full triage table: symptom → cause → file](#10-full-triage-table-symptom--cause--file)

---

## 1. The sequence, with exit criteria

| Stage | Do | Exit criterion |
|---|---|---|
| 0 | Vendor the fork, write `vendor/UPSTREAM.txt` | `cargo check` passes for host crate and headless probe |
| 1 | Give the host a frame-driven virtual clock | A dumped frame reports **non-black > 0** |
| 2 | Walk the guest forward past every fatal stub | No `Unimplemented` for N minutes of scripted input; every "unknown interface" warning understood |
| 3 | Attach the JVM thread on host→guest calls | One host-initiated guest call chain completes on a device with no panic |
| 4 | Row-band the framebuffer path | Vendored graphics equivalence tests pass; frame time no longer scales with draw-call count |
| 5 | Coalesce in the list allocator | 30k headless frames, no `AllocationFailure`, `LIVE` flat in steady state |
| 6 | Pre-allocate the OOM exception | Deliberate heap exhaustion produces a clean Java exception, not a SIGSEGV |

Do not advance without the exit criterion. Stage 5 in particular is invisible until you run long
enough — a 400-frame probe run will never show it.

---

## 2. #1 Black screen — wall-clock `now()`

**Symptom.** The emulator loads, prints no error, the host CPU is busy, and every dumped frame is
entirely black — forever, not for a while.

**Mechanism.** `System::tick` calls `Executor::tick(|| platform.now())`. That runs a
`now() + 8 ms` budget loop and **breaks early when all tasks are sleeping and the earliest wakeup
is in the future**. A WIPI/MIDP game does not busy-loop: it arms a timer
(`MC_knlSetTimer` → an `Event::timer(due, …)` on the event queue) and yields. If your host's
`now()` is a wall clock and your loop ticks as fast as it can, `now` advances by microseconds per
tick and **zero timers ever expire**.

**Fix.** Frame-driven virtual clock in the `Platform` implementation. Full code and the device-side
pacing counterpart: [virtual-clock.md](virtual-clock.md).

**Different title?** Yes, unconditionally, for any timer-driven guest. This is a property of the
host harness, not of the game.

---

## 3. #2 Fatal WIPI-C stub

**Symptom.** Frozen or black screen plus **exactly one** log line naming a table id and a slot:

```
Unimplemented: <table_id>: <method_name>
```

and then nothing. The emulator has stopped: the error propagates out of `System::tick`, and the
host loop logs `emulator tick error` and returns.

**Mechanism.** The KTF method table's `gen_stub` returns
`Err(WieError::Unimplemented(format!("{}: {}", id, name)))` for every slot with no Rust body. That
single log line is the entire bug report — it names the table and the slot.

**Fix.** Convert the stub to a **logging no-op returning a plausible value** (`trace_stub`), read
the whole subsequent call sequence out of the log, then implement for real.
[wipi-c-recipes.md](wipi-c-recipes.md).

**Different title?** Yes — but *which* slots is title-specific. Budget for this stage; it is the
only one of the six where the answer is not already written down.

---

## 4. #3 JVM thread-attach panic

**Symptom.** The first time the host calls a guest function — a HUD button wired to one of the
game's own routines — the app panics inside the JVM:

```
PANIC: <jvm crate> jvm.rs:<line>: called `Option::unwrap()` on a `None` value
```

**Mechanism.** The JVM identifies a "thread" by the **executor task id**, and
`Jvm::find_calling_class` does `threads.get(&thread_id).unwrap()`. `System::spawn` mints a *fresh*
task id. The WIPI-C context's own `spawn` has always wrapped its callback in
`attach_thread`/`detach_thread`; the newly added `call_guest_function` did not — which is why the
feature "had never once worked on a device" despite passing every host-side test.

**Fix.** RAII `JvmThreadGuard`, attach on entry and detach in `Drop`.
[hardening-batch.md](hardening-batch.md) §4.

**Different title?** Yes, for any hybrid JVM/native runtime, the moment you add your first
host-originated guest call. Add the guard *before* shipping that feature, not after.

---

## 5. #4 Full-framebuffer round-trip per draw call

**Symptom.** "Maps with a lot to render stutter compared to sparse maps"; a full-screen effect
hitches. Cost tracks **call count**, not pixels drawn.

**Mechanism.** Every WIPI draw call read the *entire* destination framebuffer out of guest memory,
copied it into a second `Vec`, did the same for the whole source sheet on blits, and wrote the
entire framebuffer back on `Drop`. A 1×1 pixel write paid roughly 0.7 MB of memcpy plus two large
`Vec` allocations.

**Fix.** Row bands + direct typed reads + a read-skipping path for covering fills.
[graphics-perf.md](graphics-perf.md).

**Different title?** Yes. This is upstream's design, not a game behaviour. Any title that issues
more than a handful of draw calls per frame pays it.

---

## 6. #5 List allocator never coalesces

**Symptom.** The game runs fine, gets **progressively slower**, and then a large allocation fails
while free memory is plentiful. On the reference port: 21–35 minutes of play, every time.
`AllocationFailure` → Java exception → tick error → emulator stops.

**Mechanism.** Upstream `free` only cleared the in-use bit; `alloc` only ever *split* blocks. A
block could never grow back, so the block count rose **monotonically**. Every `find_address` walk
got longer (the slowdown) and the largest contiguous free block shrank toward zero (the failure).

**Fix.** Coalesce **lazily, inside the walk** — not in `free`, which cannot reach the predecessor
block. [hardening-batch.md](hardening-batch.md) §1.

**Different title?** Yes, unconditionally, for any title that churns the guest heap for long
enough. The time-to-failure is the only title-specific part.

---

## 7. #6 Reporting an allocation failure allocates

**Symptom, exact:**

```
Fatal signal 11 (SIGSEGV), code 2 (SEGV_ACCERR)
Cause: stack pointer is not in a rw map; likely due to stack overflow
```

on the emulator thread, with a backtrace in which **two addresses alternate for all 511 frames**,
and the ASCII bytes of `"Allocation failure"` still sitting in argument registers.

**Mechanism.** `Jvm::exception` builds its message as a real `java.lang.String`, which means a
`char[]` allocation on the **guest** heap. When the guest heap is what ran out, that allocation
fails, and the array-instantiation path reports the failure by calling `Jvm::exception` again.
About 1 KiB of native stack per cycle.

**Why the obvious fix fails.** A plain re-entrancy guard makes the *inner* call return `Err` — and
`Jvm::exception` `.unwrap()`s it. You trade a SIGSEGV for a panic. **The outer call is the one that
must never enter `Jvm::exception`.**

**Fix.** One exception instance pre-allocated at startup while the heap is healthy, GC-rooted in a
static field, handed out instead. [hardening-batch.md](hardening-batch.md) §3.

**Different title?** Yes, unconditionally, the moment it exhausts the guest heap. This is a defect
in the emulator's *error path*. Port it verbatim.

---

## 8. Boot mechanics you must understand to read a boot failure

The KTF native load path, in order:

1. `parse_bss_size(filename)` — **the BSS size is encoded in the jar entry's filename**
   (`client.bin<N>` → N bytes of BSS appended after the image). A porter who overlooks this
   concludes that guest addresses past EOF are invalid; they are not.
2. `core.load(data, IMAGE_BASE, data.len() + bss_size)`.
3. `install_binary_patches` — a hash-keyed table. A stale table logs
   `entry X: hash matched but produced 0 patches/hooks`.
4. Register the SVC handlers, one per category.
5. `run_function(IMAGE_BASE + 1, &[bss_size])` — the `+1` is the Thumb bit; the guest
   **self-rebases** at that entry point.
6. `fn_init`.

`get_interface(name)` knows only a small fixed set of interface names and returns **0 with a
warning** for anything else. A game asking for a third interface gets a null pointer and dies far
from the cause. **On a new title, grep the boot log for that warning first.** Likewise
`Unknown SVC handler category: N` means the guest issued an SVC in a category no handler claims.

---

## 9. `WieError` variants and where each surfaces

| Variant | Typical origin | What it looks like |
|---|---|---|
| `InvalidMemoryAccess(u32)` | guest read/write outside a mapped region | address in the message; often *downstream* of the real bug |
| `AllocationFailure` | guest heap exhausted | failure #5 or a genuine leak |
| `JavaException(u32)` | guest-visible Java throw | may be caught by guest code and look like a behaviour bug |
| `JavaExceptionUnwind { .. }` | unwind out of a native frame | |
| `Unimplemented(String)` | a stubbed WIPI-C slot | failure #2; the string names table + slot |
| `FatalError(String)` | interpreter-level, e.g. `"Undefined instruction"` | clean and named; see [diagnostics.md](diagnostics.md) §6 |

Any of these propagating out of `System::tick` stops the emulator. Decide deliberately, per
variant, whether your host loop stops or logs-and-continues; stopping is the right default while
porting, because a continue turns one legible line into a flood.

---

## 10. Full triage table: symptom → cause → file

| Symptom on a new title | Likely cause | Where to fix |
|---|---|---|
| Black screen, no error, host CPU busy | wall-clock `now()`; guest sleeps on WIPI timers forever | [virtual-clock.md](virtual-clock.md) |
| Black screen, one `Unimplemented: <id>: <name>`, emulator stopped | fatal WIPI-C stub | [wipi-c-recipes.md](wipi-c-recipes.md) |
| `Init failed with code …` / `wipi init failed` at boot | wrong BSS size, or an init interface the loader does not know | §8 above — check `parse_bss_size` and the `Unknown <name>` interface warning |
| `client.bin* not found in jar` | native member not named as the loader expects | archive layer — see `archive-prep` |
| `entry X: hash matched but produced 0 patches/hooks` | binary-patch table stale versus this build | the loader's patch table |
| Branch to address 0 / `InvalidMemoryAccess(0)` right after a WIPI call | interface table shorter than the slot the guest indexes; the guest read past your allocation and branched to whatever followed | **pad the table** — [wipi-c-recipes.md](wipi-c-recipes.md) §4 |
| `Unknown SVC handler category: N` | SVC category claimed by no handler | the core's SVC dispatch; category list in the KTF runtime |
| Panic `threads.get(&id).unwrap()` on a host-initiated guest call | spawned task is not an attached JVM thread | [hardening-batch.md](hardening-batch.md) §4 |
| Runs fine, slows down, then `AllocationFailure` after 20–40 min | list allocator never coalesces | [hardening-batch.md](hardening-batch.md) §1 |
| Steady-state stutter, `bucket_header_bytes` counter climbing into GB | bucket bitmap scanned instead of indexed | [hardening-batch.md](hardening-batch.md) §2 |
| SIGSEGV `SEGV_ACCERR`, "stack pointer is not in a rw map", two alternating addresses, `"Allocation failure"` in registers | `Jvm::exception` ↔ allocator mutual recursion | [hardening-batch.md](hardening-batch.md) §3 |
| Stutter proportional to sprite count or on full-screen effects | full-framebuffer round-trip per draw call | [graphics-perf.md](graphics-perf.md) |
| Wrong colours or black bands after a fill | read-skipping canvas used where the fill does not cover the band | [graphics-perf.md](graphics-perf.md) §4 |
| Frame budget blown, log flooded with one repeating record | `tracing::instrument` at INFO on a per-frame path | [hardening-batch.md](hardening-batch.md) §5 |
| Saves always read as an empty slot | record-size query mode returns 0 for this platform build | [wipi-c-recipes.md](wipi-c-recipes.md) §7 |
| "Reset save" leaves the old save in place | name-keyed record delete is a no-op upstream | [wipi-c-recipes.md](wipi-c-recipes.md) §7 |
| In-game volume control does nothing | clip volume stub; the clip **handle changes** between set-volume and play | [wipi-c-recipes.md](wipi-c-recipes.md) §8 |
| A guest class resolves to `ClassNotFoundException` and the game silently misbehaves | missing emulator-provided Java class | [java-class-recipes.md](java-class-recipes.md) |
| `FatalError("Undefined instruction")` | genuinely missing opcode | [diagnostics.md](diagnostics.md) §6 |
| Garbage behaviour, faults at an unrelated `pc` | silently wrong opcode semantics | [diagnostics.md](diagnostics.md) §6 |
| Frame rate fine on the host, terrible on device | see the device pacing section of [virtual-clock.md](virtual-clock.md) §5 |
