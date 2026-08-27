# The hardening batch — port these verbatim

**Ten** defects that are in **the emulator, not in any game**. A new title hits every one of them;
the only title-specific part is how long it takes. Apply them as a batch, early, before you spend a
week bisecting a symptom that is already documented here.

Five are worked in full below. The other five are the same kind of defect with the same kind of
device-measured before/after, and are only elsewhere because another file owns the mechanism in
depth — they are §7, and they are part of the batch, not related reading. **Two of them appear only
on a device**, which is a property none of the five below have.

**The axis boundary for this file.** §1–§3 own the **guest allocator**: monotonic block growth,
bucket-header scanning, and the allocation-failure recursion. **Process footprint and eager
address-space commit are not here** — those live in the mapper below the allocator and belong to
[memory-footprint.md](memory-footprint.md). The two are confusable in one direction only: a heap that
*fails to allocate* is §1; a process that *dies with plenty of guest heap free* is footprint.

## Contents

1. [List allocator: lazy coalescing](#1-list-allocator-lazy-coalescing)
2. [Bucket allocator: free chain + indexed in-use map](#2-bucket-allocator-free-chain--indexed-in-use-map)
3. [`oom.rs`: reporting an allocation failure must not allocate](#3-oomrs-reporting-an-allocation-failure-must-not-allocate)
4. [`JvmThreadGuard` and the host→guest call API](#4-jvmthreadguard-and-the-hostguest-call-api)
5. [Timer callback log level](#5-timer-callback-log-level)
6. [Mitigations that are not fixes](#6-mitigations-that-are-not-fixes)
7. [Five more of exactly the same character, owned by other files](#7-five-more-of-exactly-the-same-character-owned-by-other-files)

---

## 1. List allocator: lazy coalescing

**Layout (upstream, unchanged).** One 256 MB guest heap at a fixed base, split in half: a
**list allocator** for allocations above a threshold (512 bytes on the reference core) and a
**bucket allocator** below it.

**Symptom.** The game runs fine, gets **progressively slower**, and then one large allocation fails
while total free memory is plentiful. `AllocationFailure` → Java exception → tick error → the
emulator stops. On the reference port this landed at 21–35 minutes of play, reproducibly.

**Mechanism.** Upstream `free` only cleared the in-use bit, and `alloc` only ever **split** blocks.
A block could therefore never grow back, so the block count rose **monotonically**. Two
consequences, and you see them in that order:

1. every `find_address` walk got longer → the slowdown;
2. the largest contiguous free block shrank toward zero → the failure.

**Fix: coalesce lazily, inside the walk.**

```rust
impl ListAllocator {
    // note: now takes &mut ArmCore, because coalescing WRITES the merged header back
    fn find_address(core: &mut ArmCore, base: u32, base_size: u32, req: u32) -> Result<u32> {
        let mut cursor = base;
        while cursor < base + base_size {
            let Some(size) = Self::checked_block_size(core, cursor)? else {
                // corrupt header: STOP THE WALK. Do not raise FatalError. See below.
                break;
            };
            if !Self::in_use(core, cursor)? {
                let size = Self::coalesce(core, cursor, base + base_size)?; // merges forward
                if size >= req { return Ok(cursor); }
            }
            cursor += size;
        }
        Err(WieError::AllocationFailure)
    }
}
```

**Why not coalesce in `free`.** The chain is singly linked **forwards** — a header records only its
own size — so `free` cannot reach the block *preceding* the one being released without adding a
footer or a back-pointer. Either of those moves the canary offset and every size computation in the
allocator. The walk cursor, by contrast, is already standing on the predecessor. Coalescing in the
walk is not a shortcut; it is the only place the information exists.

**`coalesce` must write the merged header back.** This is load-bearing, not a cache. `alloc`
re-reads the header to compute the trailing free remainder to split off; returning a merged size
without storing it means the split runs against the stale size and **leaks everything the merge
absorbed**.

**The canary is safe.** A guard word (`0xDEADBEEF` at `block + size - 4`) is only read by `free`,
only for an in-use block, and a block only becomes in-use through `alloc`, which writes a fresh
one. A merged block swallowing the stale canaries of the blocks it ate is therefore harmless.

**★ The containment rule, worth copying verbatim (A).** Make the size read fallible and let a
corrupt header **stop the walk** rather than raise a fatal error:

```rust
/// Returns None on a header that cannot be true (zero, unaligned, or running past
/// the region end). The caller must STOP, not fail.
///
/// Rationale: upstream's unchecked read simply pushed the cursor past base+base_size
/// and ended the loop, so a corrupt block cost exactly one AllocationFailure.
/// Promoting that to FatalError would make THIS fix -- whose entire purpose is to
/// stop dying at 21-35 minutes -- kill the emulator EARLIER on heaps the old code
/// survived.
fn checked_block_size(core: &ArmCore, block: u32) -> Result<Option<u32>> { ... }
```

Generalise it: **a hardening change must never upgrade a survivable error into a fatal one.**
Measure the blast radius of your new strictness against the bug you are fixing.

**Regression tests to write** (inside the vendored crate):

- interleaved free/refill churn, then a whole-region allocation must succeed and return the first
  block address — proves coalescing actually reclaims;
- three adjacent free blocks must fuse into one.

## 2. Bucket allocator: free chain + indexed in-use map

**Symptom.** Not a crash — a **frame-budget** symptom. Loads and transitions stutter; a profile
shows time inside the allocator; the `bucket_header_bytes` counter climbs into the gigabytes.

**Mechanism.** The one-bit-per-slot bitmap made both `alloc` and `free` read the **entire** class
header out of guest memory on every call — 128 KB for the smallest size class. Measured on the
reference port: **3.98 GB of guest-memory reads across 53 496 allocations ≈ 74 KB per allocation**.
`alloc` additionally scanned linearly from index 0, so it got slower as a class filled.

**Fix.** An 8-byte header per class holding a LIFO free chain and a bump index:

```rust
#[repr(C)]
struct BucketHeader {
    free_head: u32,        // index of the first free slot, or SENTINEL
    next_never_used: u32,  // bump index
}
// a freed slot stores the next free index in its own first word
```

`init` then writes **8 bytes per class** instead of hundreds of kilobytes of `0xff`.

**Three non-obvious constraints, all generalisable (A):**

1. **The bitmap must stay.** A bare free chain cannot survive a double free: pushing an
   already-free slot links it to itself, and every later `alloc` of that class returns the *same*
   address forever, silently aliasing live objects. The old bitmap was idempotent under double
   free. So keep the map, but make it **indexed** — one byte read, one byte written:

```rust
fn in_use(core: &ArmCore, class: usize, index: u32) -> Result<bool>;
fn set_in_use(core: &mut ArmCore, class: usize, index: u32, v: bool) -> Result<()>;
```

2. **`header_length` must not change.** Every slot address is
   `region_offset(i) + header_length(i) + index * slot_size`. Shrinking the header moves **every
   guest pointer in the heap**. So put the two new words at the *start of the old bitmap area* and
   give up the last slots of each class:

```rust
const fn usable_slots(i: usize) -> u32 {
    BUCKETS[i].1 - 8 * size_of::<BucketHeader>() as u32
}
// const assertion block: every class still has a positive slot count and the
// header words do not overlap slot 0
const _: () = { /* assert!(...) for each class */ };
```

3. **Do not pre-link the chain at init.** Writing a next-pointer into every slot is megabytes of
   writes touching every page of a 120 MB region before the first frame is drawn. The bump index
   exists precisely so init stays O(number of classes).

**Failure handling (A) — the asymmetry is the point:**

- A free-chain head that is out of range, or points at a slot the bitmap says is live → **log and
  drop the whole chain**, fall through to the bump index. *A leak is survivable; aliasing is not.*
- A `free` whose address is not a slot boundary for the class → **leak it loudly**. The usual cause
  is a `free` whose `size` argument selects a different class than the matching `alloc` did.

**Regression tests to write:** free chain is LIFO and never aliases the bump index; a double free
cannot alias (mark it `#[cfg(not(debug_assertions))]` — the double free trips a `debug_assert` by
design); class exhaustion returns exactly the last usable slot address.

## 3. `oom.rs`: reporting an allocation failure must not allocate

**Symptom, exact.** `Fatal signal 11 (SIGSEGV), code 2 (SEGV_ACCERR)`,
`Cause: stack pointer is not in a rw map; likely due to stack overflow`, on the emulator thread,
with **two addresses alternating for all 511 frames** of the backtrace and the ASCII bytes of
`"Allocation failure"` still in argument registers.

**Mechanism.** `Jvm::exception` builds its message as a real `java.lang.String` — which means a
`char[]` allocation on the **guest** heap. When the guest heap is what ran out, that allocation
fails, and the array-instantiation path reports *that* failure by calling `Jvm::exception` again.
Roughly 1 KiB of native stack per cycle, until the guard page.

**Why the obvious fix does not work.** A plain re-entrancy guard forces the *inner* call to return
`Err`, and `Jvm::exception` `.unwrap()`s it — you trade a SIGSEGV for a panic. **The outer call is
the one that must never enter `Jvm::exception` at all.**

**Fix.** One exception instance, pre-allocated at startup while the heap is healthy, handed out
instead of building a new one. All the throw sites in the crate throw the same emulator-owned
exception class anyway, so a guest `catch` still matches; only the message text is lost.

```rust
// three latches, all module-private
static OOM_EXCEPTION: AtomicU32 = AtomicU32::new(0);   // guest ptr; 0 = unavailable
static HEAP_DEAD:     AtomicBool = AtomicBool::new(false); // sticky
static IN_EXCEPTION:  AtomicBool = AtomicBool::new(false); // re-entrancy, held ACROSS the await

/// Pure decision function -- unit-testable with no JVM.
pub const fn use_singleton(ptr: u32, allocation_failure: bool, heap_dead: bool, reentered: bool)
    -> bool
{
    ptr != 0 && (allocation_failure || heap_dead || reentered)
}

/// Message is a CLOSURE, not a String: on the exhausted path even `format!` is a host
/// allocation whose product is thrown away.
pub async fn java_error<F: FnOnce() -> String>(jvm: &Jvm, msg: F, cause: &WieError)
    -> JavaError { ... }

/// Short-circuits the normal error renderer, which builds a stack trace using a
/// StringWriter + PrintWriter + StringBuffer -- three more guest allocations, each
/// unwrapped.
pub fn to_wie_err(e: JavaError) -> WieError { ... }
```

**Mechanics that are easy to get wrong:**

- **Hold the re-entrancy latch across the `await`.** The recursion goes *through* the await point;
  a guard scoped to the synchronous prologue catches nothing.
- **GC-root the singleton.** A raw `u32` in a Rust static is not a root. Park it in a static field
  of a registered class — see [java-class-recipes.md](java-class-recipes.md) §5. Without the root,
  the next collection frees it and you hand out a dangling guest pointer.
- **Initialise before the biggest allocation burst** — the class loader's constructor, which reads
  the whole jar and the native member. That burst is exactly the window the net must already cover.
- **Convert every throw site**, including the `.unwrap()`s. On the reference port that was six
  throw sites plus **two `.unwrap()`s** in the JVM implementation layer — panics on precisely the
  path the OOM handling exists to survive. Grep the crate for `.unwrap()` on anything returning a
  Java error before declaring this done.

**Will a new title hit this?** Yes, unconditionally, the moment it exhausts the guest heap. Port it
verbatim.

## 4. `JvmThreadGuard` and the host→guest call API

**Symptom.** The first host-initiated guest call — a UI button wired to one of the game's own
routines — panics inside the JVM with `called Option::unwrap() on a None value`.

**Mechanism.** The JVM keys per-thread state by the **executor task id**, and
`find_calling_class` does `threads.get(&thread_id).unwrap()`. `System::spawn` mints a fresh task
id. The WIPI-C context's own `spawn` has always wrapped its callback in
`attach_thread`/`detach_thread`; the newly added host-call path did not. On the reference port this
meant the feature **had never once worked on a device**, while passing every host-side test.

**Fix — RAII, so error paths cannot leak a thread entry:**

```rust
struct JvmThreadGuard<'a> { jvm: Option<&'a Jvm> }

impl<'a> JvmThreadGuard<'a> {
    /// `None` when the JVM does not exist yet (a call issued during boot) -- so the
    /// guard is always constructible and the detach is always paired.
    fn attach(jvm: Option<&'a Jvm>) -> Self {
        if let Some(j) = jvm { j.attach_thread(); }
        Self { jvm }
    }
}

impl Drop for JvmThreadGuard<'_> {
    fn drop(&mut self) { if let Some(j) = self.jvm { j.detach_thread(); } }
}
```

The JVM is published into an `Arc<Mutex<Option<Jvm>>>` by the emulator's `start`, so the guard can
be constructed from any host thread.

**The supporting API (A).** Extend the `Emulator` trait with **defaults that fail rather than
hang**:

```rust
fn read_memory(&self, address: u32, len: usize) -> Vec<u8> { Vec::new() }
fn write_memory(&mut self, address: u32, data: &[u8]) -> bool { false }
fn call_guest_function(&mut self, address: u32, args: &[u32]) { }        // return DISCARDED
fn call_guest_function_out(&mut self, address: u32, args: &[u32], out: Arc<GuestCallResult>) {
    out.fail();   // <-- default FAILS; a backend with no ARM core must not wedge a caller
}
```

**`GuestCallResult` — a lock-free cell with exactly three states.**

```rust
const PENDING: u8 = 0;
const OK:      u8 = 1;
const FAILED:  u8 = 2;

/// Three states, not two. Treating a failure as "not yet" hangs the host state machine
/// forever; treating "not yet" as a failure abandons a call that was about to succeed.
pub struct GuestCallResult { value: AtomicU32, state: AtomicU8 }
```

Store the value with `Release` **before** publishing the state; load the state with `Acquire`
**before** reading the value. Document the pairing at the definition — it is the whole correctness
argument and it is invisible at the call sites.

This is generic infrastructure. Everything a host chains on top of it (multi-step save sequences,
patch-and-call features) is title-specific and belongs to `guest-re`; the mechanism here is not.

**Will a new title hit this?** Yes, the moment you add your first host-originated guest call. Add
the guard *before* shipping that feature.

## 5. Timer callback log level

**Symptom.** Frame budget blown; the log flooded with one repeating record; the effect is worse on
device than on the host.

**Mechanism.** `#[tracing::instrument(name = "timer")]` on the WIPI timer callback defaults to
**INFO**. A WIPI title idles on timers, so it fires tens of times per second, and each record is a
platform log write **inside the frame budget**. Measured at ~46/s on one title and 124/s elsewhere.

**Fix.**

```rust
#[tracing::instrument(name = "timer", level = "debug", skip_all)]
```

**Generalise it (A).** Before shipping, audit every `#[tracing::instrument]`, `info!` and `warn!`
on a per-frame path — WIPI timer callbacks, draw calls, input polling, the tick loop itself. On
Android in particular each record is a syscall. Two additional levers:

- compile the low levels out entirely in release with
  `log/release_max_level_info` + `tracing/release_max_level_info` on your shipping profile;
- **but know that this also silences the diagnostic traces you rely on** — the WIPI draw-call trace
  and the instruction trace both live at `debug`/`trace`. Give the headless probe a
  `--no-default-features` build mode so a trace run is one flag away, and note in its help text
  that the default quiet build cannot produce a trace no matter what the log filter says.
- note that the ARM interpreter may log through a *different* facade (`log`) than the rest of the
  tree (`tracing`); switching one does not reach the other.

## 6. Mitigations that are not fixes

Label these as mitigations in the code, or someone will later read them as solutions.

- **Raising the emulator thread's stack size.** Thread stacks are *reserved address space*, so a
  large reservation costs nothing until touched — raising it multiplies your time-to-failure
  cheaply. It does not fix a recursion. The reference port raised it by 8× and said so in the
  comment. The number is **(C)**; the technique is **(A)**.
- **Increasing a timeout because a call sometimes lands late.** Find out why it is late.
- **Widening a plausibility gate to stop a false positive.** Widen it only with a written
  justification of the new bound; see the covering-fill guard in
  [graphics-perf.md](graphics-perf.md) §4 for the shape of a bound that is argued rather than
  tuned.

---

## 7. Five more of exactly the same character, owned by other files

Each row is an **emulator defect a new title hits unconditionally**, with a device-measured
before/after. Work them in the same pass as §1–§6.

| Symptom the porter or the player reports | The defect | Owner |
|---|---|---|
| "It restarts if I switch away and come back." No crash dialog, no tombstone | The memory mapper **commits every page of a range up front**, so creating the guest heap commits and zeroes all of it before the first frame — and the fattest process is the low-memory killer's first pick. **(B)** Lazy commit measured total resident **−47 %**, native heap **−66 %**, with the game touching about 7 MB of a 256 MB heap | [memory-footprint.md](memory-footprint.md) §3–§7 |
| Input lags without bound; the game keeps running in the player's pocket | The event-queue poll yields only when its queue is **empty**. On a device slower than the guest's timer period, a re-armed overdue timer keeps it non-empty and the poll never returns — so the worker loop's input drain and background-pause check are unreachable. Fix: cap consecutive timer callbacks per poll, then yield; on a device that keeps up the branch never fires, so it is free | [frame-pacing.md](frame-pacing.md) §6–§7 |
| A frame hitch on **every** sound effect | The resource fetch **re-reads and re-parses the whole archive per call** — **(B)** six full archive copies per effect, ≈3.9 ms on a *desktop* host, spent inside the frame budget on the emulator thread | [audio-core.md](audio-core.md) §9 |
| Memory grows with every sound played, and the parse cost repeats | The asset is parsed on **every** use, and the handle-minting call's destructor is unimplemented, so each call leaks one parsed copy. This is the release-side stub rule again — [wipi-c-recipes.md](wipi-c-recipes.md) §2 | [audio-core.md](audio-core.md) §8–§9 |
| "The game speeds up", after visiting a screen | The timer **cancel** primitive is a logging no-op while arming **pushes** a new shot instead of replacing the keyed one, so a visit leaves a live timer behind — ⚠ the per-visit accumulation is inferred from those two primitives, not observed | [frame-pacing.md](frame-pacing.md) §8–§9 |

**(A) The first two are invisible headless.** One needs the platform's memory manager, the other needs
a host slower than the guest's own timer period. **A porter who validates only headless ships both**,
and both present to the player as something else entirely — a crash and a laggy control respectively.
Put them on the device pass:
[device-verification.md](../../../shared/device-verification.md).
