# The headless probe — the contract your port must implement

The probe is a **second host** for the same emulator core: no window, no audio, a virtual clock, and a `main()` you can script. It is the difference between "one minute per experiment, on your desk" and "five minutes per experiment, on a phone, with a cable".

It cannot ship as a plugin script. It must link **the same patched core the app ships**, and it must `include!` the port's own renderer sources to do differential verification against them. The crate therefore lives in the template repo. What follows is the **contract**: implement all of it, or the recipes in [recipes.md](recipes.md) will not work.

## Contents

1. [Why a virtual clock is mandatory](#1-the-virtual-clock--the-single-highest-value-requirement)
2. [The flag set](#2-the-flag-set)
3. [Parser traps that cost hours](#3-parser-traps)
4. [The tick loop](#4-the-tick-loop)
5. [Always-on gauges](#5-always-on-gauges)
6. [Frame dumps](#6-frame-dumps)
7. [The file-backed record store](#7-the-file-backed-record-store)
8. [Diagnostic modes](#8-diagnostic-modes)
9. [Build modes and the tracing trap](#9-build-modes)
10. [Canonical invocations and CI assertions](#10-canonical-invocations)

---

## 1. The virtual clock — the single highest-value requirement

**Requirement: `Platform::now()` must return a clock advanced by a fixed amount once per frame, not wall time.**

```rust
struct ProbePlatform { clock: Arc<AtomicU64>, /* … */ }

impl Platform for ProbePlatform {
    fn now(&self) -> Instant { Instant::from_epoch_millis(self.clock.load(Ordering::SeqCst)) }
}

// once per loop iteration, before anything else:
clock.fetch_add(ms_per_frame, Ordering::SeqCst);
```

**The mechanism.** A WIPI/MIDP game does not busy-loop. It arms a timer (50 ms is typical) and yields. The executor's tick breaks early when every task is asleep and the earliest wakeup is in the future. A headless loop runs thousands of iterations per wall-second, so with a real clock `now()` advances by microseconds per tick and **zero timers ever expire**.

**The symptom.** The game boots. There is no error. The log looks healthy. **Every dumped frame is entirely black, forever.** This is the first trap every port hits, and nothing in the output points at the clock.

**Three payoffs beyond "it works at all":**

1. **Determinism.** Time is a pure function of the frame counter, so `--keys 260:OK` lands at the same game state on every run, on every machine. Bisecting a key script is only possible because of this.
2. **Decoupling.** Emulated time no longer depends on how fast your laptop is. Print the ratio (`virt 3.41x`) every 100 frames so you can see whether the host is keeping up.
3. **Fast-forward.** `--ms 40` runs guest time at double speed to reach a late game state sooner, at the cost of coarser timer resolution.

**Default:** `--ms 20` ≈ 50 fps of guest time. Sane for a 50 ms-timer title. Make the flag **mandatory in the documented invocation** and mention the black-frame symptom in `--help`.

Device-side pacing is a different problem with a different answer (adaptive multi-tick against a wall-clock budget) — that belongs to `android-host`, not here.

---

## 2. The flag set

| flag | default | effect |
|---|---|---|
| `--zip <path>` | *(required)* | the game archive. Validate with the backend's `loadable_archive` check and **fail loudly** — a silent return after "not a supported archive" wastes a session |
| `--out <dir>` | `work/probe` | where frame dumps land |
| `--w <n>` / `--h <n>` | platform default | **the guest framebuffer size**, not a window size. This is the knob that tells you whether the title is resolution-adaptive — try the device's logical size before assuming it is not |
| `--frames <n>` | 400 | loop bound |
| `--ms <n>` | 20 | **virtual ms added to the clock per frame** (§1) |
| `--db <dir>` | none (in-memory) | switch to a file-backed record store whose layout matches the device's (§7) |
| `--dump f1,f2,…` | empty | frames at which to write a frame dump — **and the gate for every other diagnostic** (§3) |
| `--keys f:KEY[:hold],…` | empty | scripted input; `hold` defaults to ~6 frames |
| `--find-fb 1` | false | shape-search for framebuffer-like objects (§8.1) |
| `--map-check 1` | false | differential render vs the guest's own buffer (§8.2) |
| `--profile 1` | false | sampled guest-PC profile (§8.3) |
| `--char-dump <dir>` | none | dump the god object and the whole static area at dump frames (§8.4) |
| `--trace-state 1` | false | print map signature / window state / last offscreen blit |

**Key names to accept:** `UP DOWN LEFT RIGHT OK LSK RSK CLEAR|CLR NUM0..NUM9|0..9 HASH|# STAR|*`. Accept both the long and short spellings; you will type these hundreds of times.

---

## 3. Parser traps

These are cheap to avoid at write time and expensive to diagnose later. **(A)**

* **Strict `--flag value` pairs.** A loop shaped `while i + 1 < argv.len() { … i += 2 }` means a boolean written bare (`--map-check`) swallows the *next flag* as its value and desynchronises everything after it. Either accept bare booleans or reject them with an error.
* **Never `_ => {}` on an unknown flag.** A silently ignored misspelling gives you a default-configured run that looks entirely plausible, and you will believe its output. Print `unknown flag: --map-chek` and exit non-zero.
* **Be consistent about boolean spellings.** If `--find-fb` accepts `1|true` but `--trace-state` accepts only `"1"`, someone will lose an afternoon. Parse them all through one function.
* **⚠ Diagnostics gated on `--dump` must say so — in `--help` and in the docs.**
  This is a real, published error worth carrying: the reference project's own documented invocation passed `--map-check 1` with **no `--dump`**, and the check is gated `if map_check && dumps.contains(&frame)`. That command **cannot print anything**. The invocation was copied into a `CLAUDE.md` and reused for months.
  The general lesson **(A)**: gate expensive diagnostics on an explicit frame list — that is correct design, because they cost real time — but then make the gate visible. `--help` text: *"requires `--dump`; only runs at dump frames."* And if `--map-check` is passed with an empty dump list, **warn on stderr**.

---

## 4. The tick loop

Per frame, in this order:

1. **Advance the virtual clock.**
2. Drain any `request_redraw` from the platform into a redraw event.
3. Fire scheduled keydowns; synthesise key-repeat every ~6 frames while a key is held; fire due keyups.
4. `emulator.tick()`. On error: **print the error and break** — do not keep ticking a dead emulator; the subsequent output is noise that looks like data.
5. Periodic gauges (§5).
6. Diagnostics, gated on the dump list (§8).
7. Frame dump, if this frame is in the dump list (§6).

**Dump frames must force a paint.** Send a redraw event, tick once more, *then* read the framebuffer. Otherwise you capture whatever was last painted, which on an idle frame is the previous scene.

---

## 5. Always-on gauges

Cheap, printed on a fixed cadence, and they are how you tell the layers apart before you start guessing. **(A)**

**`[heap]` every 100 frames** — `allocs / frees / LIVE / list_walk_steps / bucket_header_bytes`, from counters inside the guest allocator.

* `LIVE = allocs - frees` climbing monotonically during steady play **is** the guest-side leak. Do not go looking for a memory bug elsewhere until this is flat.
* `list_walk_steps` climbing superlinearly = allocator free-list degradation (see `emulator-core`).
* `bucket_header_bytes` climbing into gigabytes = the small-object allocator is rescanning a bitmap per call.

**`[work]` every 100 frames** — retired guest instructions, M instructions per frame, SVC count, M instructions/s wall, and the **virtual-time ratio**.

This is how you answer "is the lag the interpreter or my graphics layer" without guessing. Reference measurement **(B)**: ~2.5 M guest instructions per rendered frame, because that title rasterises the world in guest code. If your title measures 100 k/frame and still stutters, the interpreter is not your problem.

**`[stack]` every 500 frames** — native stack high-water mark.

Worth stealing verbatim as an *idea* **(A)**: query the OS for the thread's own stack allocation and report how much of it is still un-committed reserve. The OS commits stack pages lazily and never decommits, so `initial − current` is an exact high-water mark for the entire run. On Windows that is `VirtualQuery` against the region still in `MEM_RESERVE`; on Linux/Android it is the `Rss:` of the `/proc/self/smaps` mapping containing `sp`. Both are OS-specific **(C)** — put them behind one `reserved_bytes() -> Option<usize>`.

Why this gauge and not an in-process counter: in the reference hunt, *every* in-process hypothesis measured the wrong layer (a thread-wrapper nesting counter that is 1 by construction; a `run_function` nesting depth that plateaus; an executor task count under 8). **Measure the thing that actually overflows, from outside the code that uses it.**

**Design rule for all gauges: log only on a new maximum.** A healthy session prints a handful of lines at startup; a leaking one prints a rising staircase you can read at a glance.

---

## 6. Frame dumps

Write raw RGBA and **encode the geometry in the filename**:

```
frame_00400_240x320.rgba
```

The converter then needs no arguments at all:

```
python "${CLAUDE_PLUGIN_ROOT}/tools/frames_to_png.py" work/probe/run1 --montage work/probe/run1.png
```

**(A) Encode the geometry in the filename** — every alternative (a sidecar JSON, a `--size` flag, a hardcoded default) eventually disagrees with the bytes.

**Print a summary line per dump**: total pixels, opaque pixels, **non-black pixels**.

`non-black` is your automated black-screen detector. Assert `non_black > 0` in CI instead of eyeballing PNGs — it catches the virtual-clock regression, an archive that loads but never paints, and a graphics change that blanks a band, all with one integer.

A montage contact sheet of 10-20 frames is the fastest way to find the frame at which the title screen becomes playable.

---

## 7. The file-backed record store

`--db <dir>` must switch the record store from in-memory to files, with the layout:

```
<dir>/<app_id>/db/<record_name>/<record_id>
```

**This layout must be byte-identical to the device host's storage layout.** That single agreement buys you the most valuable capability the probe has:

> Pull a real save off a phone, drop it into `--db`, and start the headless run **deep in the game** instead of replaying from the title screen.

It also runs backwards: a save produced headlessly can be pushed to the device.

Log the store's own decisions: `[probe] DB open name=… app_id=… -> <path>`. That log line is **step 1 of [recipes.md](recipes.md) Recipe A** — it tells you the game's record *names* for free, and the files on disk tell you their *sizes*, before you have disassembled anything.

---

## 8. Diagnostic modes

### 8.1 `--find-fb` — locating an object by *shape* **(A)**

Three-stage generic search for an object whose struct layout you know but whose address you do not:

1. **Field scan.** Walk the god object in u32 steps; keep words that land in the heap window; treat each as the struct you are hunting; accept only if the fields satisfy an **invariant relation** between themselves. For a framebuffer: `8 <= w,h <= 512 && bpl == w*2 && bpp == 16`. The internal relation `bpl == w*2` is what makes this near-zero-false-positive — an arbitrary quadruple of words almost never satisfies it.
2. **Heap sweep.** Scan a window of the guest heap in 64 KB blocks for the same shape. This finds instances nothing points to from the anchor you know.
3. **Back-reference scan.** For each candidate, scan the static area and the god object for words equal to its address, and report each hit in **both** addressing bases (`OBJ+0x…` *and* `sl+0x…`).

**The recipe generalises to any struct with a self-consistent header**: bitmaps, sound buffers, tile maps, inventory records, save headers. *Shape-match on an invariant relation → find who points at it → express the answer in both bases.*

The heap window is title- and core-specific **(C)** — derive yours from the allocator's base and size constants.

### 8.2 `--map-check` — differential verification against the guest itself **(A)**

The best idea in this toolchain. When you reimplement a guest routine host-side (a minimap, a stat panel, a damage formula), make the emulator run **both** and diff the results.

Requirements:

* The probe **`include!`s the shipping renderer source**, not a copy. *A check that exercises a copy proves nothing about what ships.* See `${CLAUDE_PLUGIN_ROOT}/shared/selftest-convention.md`.
* The core exposes a hook that records the guest's own offscreen output — e.g. a `note_offscreen_copy` / `last_offscreen_copy` pair written by the framebuffer-copy WIPI call, packing `(handle, w, h)`. Without it you cannot tell the guest's current buffer from a stale one belonging to a map visited earlier.
* **Quantise your output through the guest's own colour space before comparing.** If the guest is RGB565, round-trip your RGB through RGB565 so the comparison is like-for-like; otherwise you will chase a 3-LSB "mismatch" that is your own precision.
* **Report, do not stamp.** If your renderer draws an extra marker the guest does not (a player dot, a highlight), report its position separately rather than compositing it, or every diff shows your own marker as an error.
* Print `mismatch N/total (x.xxx%)` plus the first ~8 differing pixels with coordinates and both colours. The coordinates are the debugging information; the percentage is the gate.

Reference result **(B)**: 0/5040 and 0/1224 pixels on two different maps — including a frame where the game's own map window had never been opened, which proves the host renderer is reading live data rather than a stale repaint.

**A mismatch is a bug in the reimplementation, not a matter of opinion.**

### 8.3 `--profile` — sampled guest profiler **(A)**

Have the interpreter sample the guest PC plus the frame-pointer chain once per instruction batch (once per 1000 instructions is plenty). Accumulate two histograms:

* **leaf PC** — where time is actually spent;
* **(pc, lr) caller pairs** — who is spending it.

Print the top ~30 and ~15. **Print each address as `pc - IMAGE_BASE`**, so every line pastes straight into `dis.py`. Any address a tool prints should be in the basis your disassembler consumes. **(A)**

### 8.4 `--char-dump` — the differential-RAM technique **(A)**

At each dump frame write **two** blobs:

* `obj_%05d.bin` — the god object (a few tens of KB);
* `glob_%05d.bin` — the whole static/global area.

Dump both, always. A "current map id" is far more likely to be a global than a field of the player struct, and you do not know which you are hunting until you find it.

**The recipe to find an unknown scalar field:**

1. Dump at frame N.
2. Perform **exactly one** in-game change via `--keys` (spend one stat point, pick up one item, walk through one door).
3. Dump at frame M.
4. `cmp` the blobs; collect the changed offsets.
5. **Repeat with different starting states** and intersect the changed-offset sets. One trial gives you dozens of candidates (timers, animation counters, RNG state); three trials that change the same thing give you one or two.
6. Confirm with `xref.py offset <imm>`: some code site must materialise that offset. If nothing does, you found a field the code reaches by pointer arithmetic and you need a different anchor.
7. Satisfy Rule 2 — the diff is derivation #1; the clamp value, the game-rule arithmetic, or the set of semantically consistent readers is derivation #2. See [recipes.md](recipes.md) Recipe D.

---

## 9. Build modes

```
cargo build --release -p <probe>                          # quiet: log/trace macros compiled OUT
cargo build --release -p <probe> --no-default-features    # enables the WIPI draw-call trace
cargo build --release -p <probe> --no-default-features --features insn_trace
```

The default feature should set `release_max_level_info` on **both** the `log` and `tracing` facades across the whole dependency graph. Two reasons:

1. The probe then measures the same code the app runs, so its instructions/second is comparable to the device's.
2. A per-instruction `trace!` that is merely *filtered at runtime* still costs an atomic level load and a branch per guest instruction, plus keeping values alive for a never-taken format path. At millions of instructions per frame that lands directly on the frame budget.

**⚠ Trap:** the same switch silently disables the draw-call trace. A `RUST_LOG=…=debug` run against a default-features build produces an empty log and looks like "the game made no draw calls". Document the `--no-default-features` requirement next to the trace invocation.

**Second trap:** the ARM interpreter may log through `log` while everything else uses `tracing`. Suppressing one facade is not suppressing both — depend on the other facade explicitly, even if unused, so its max level can be set too.

---

## 10. Canonical invocations

```bash
# boot to gameplay with a real device save, dump the last frame
<probe> --zip work/<archive>.zip --w 240 --h 320 \
        --frames 700 --ms 20 --db work/probe/db --out work/probe/x \
        --dump 700 --keys 260:OK,300:OK,360:OK
python "${CLAUDE_PLUGIN_ROOT}/tools/frames_to_png.py" work/probe/x --montage work/probe/x.png

# differential-verify a host reimplementation  (NOTE: --dump is REQUIRED, see §3)
<probe> --zip work/<archive>.zip --w 240 --h 320 --frames 900 --ms 20 \
        --map-check 1 --dump 880,900

# find an unknown scalar field
<probe> … --char-dump work/probe/ram --dump 400,700 --keys 500:NUM1

# WIPI draw-call trace (needs the --no-default-features build)
RUST_LOG=<wipi_crate>=debug <probe> … 2> work/probe/trace.log
```

**Finding your own title-navigation key script (A):** dump a frame every ~40 frames until you can see the title screen in the montage, then bisect: insert one `OK` and re-dump, moving its frame number until the screen advances. Three or four `OK`s usually reaches gameplay. The resulting script is **(C)** — pure title content — but the bisect method is not.

## CI gates worth wiring up

Cheap, device-free, and each one catches a whole class of regression:

| assertion | catches |
|---|---|
| the probe exits 0 over N frames | fatal unimplemented stubs, tick errors |
| a dumped frame reports `non_black > 0` | the virtual-clock regression, a blank archive, a graphics blanking bug |
| `--map-check` reports 0 mismatches | a host reimplementation drifting from the guest |
| `LIVE` is flat across the last 10 k frames | a guest-side allocation leak |
| a long run (30 k frames) completes | allocator exhaustion after tens of minutes |

Combine with the dependency-free selftests described in `${CLAUDE_PLUGIN_ROOT}/shared/selftest-convention.md`: everything decidable without an emulator belongs there, and the probe covers what genuinely needs a running guest. The phone is then only needed for things that genuinely need a phone.
