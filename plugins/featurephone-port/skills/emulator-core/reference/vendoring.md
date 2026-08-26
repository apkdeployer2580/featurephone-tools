# Vendoring the emulator fork

Every fix in this skill is a change to code you do not control and would never get merged
upstream. The vendoring architecture is what makes those changes shippable the same day.

## Contents

1. [Vendor as a plain directory + cargo `[patch]`](#1-vendor-as-a-plain-directory--cargo-patch)
2. [Repeat the transitive patch inside the vendored workspace](#2-repeat-the-transitive-patch-inside-the-vendored-workspace)
3. [`vendor/UPSTREAM.txt` — the file the reference port did not have](#3-vendorupstreamtxt--the-file-the-reference-port-did-not-have)
4. [Diffing your fork against its base](#4-diffing-your-fork-against-its-base)
5. [Feature pass-through: forwarding a feature to a transitive crate](#5-feature-pass-through-forwarding-a-feature-to-a-transitive-crate)
6. [Pin the toolchain](#6-pin-the-toolchain)
7. [What the fork delta looked like, categorised](#7-what-the-fork-delta-looked-like-categorised)
8. [Working rules inside vendored code](#8-working-rules-inside-vendored-code)
9. [Before you blame a dependency, check whether you have already forked it](#9-before-you-blame-a-dependency-check-whether-you-have-already-forked-it)

---

## 1. Vendor as a plain directory + cargo `[patch]`

**(A)** Do not use a git submodule of your own fork, and do not try to land the fixes upstream
first. Copy the two trees into `vendor/` and redirect the *entire* dependency graph from the
workspace root:

```toml
# Cargo.toml (workspace root)

[patch."https://github.com/dlunch/arm32_cpu.git"]
arm32_cpu = { path = "vendor/arm32_cpu" }

[patch."https://github.com/dlunch/wie.git"]
wie_backend   = { path = "vendor/wie/wie_backend" }
wie_core_arm  = { path = "vendor/wie/wie_core_arm" }
wie_ktf       = { path = "vendor/wie/wie_ktf" }
wie_midp      = { path = "vendor/wie/wie_midp" }
wie_util      = { path = "vendor/wie/wie_util" }
wie_wipi_c    = { path = "vendor/wie/wie_wipi_c" }
wie_wipi_java = { path = "vendor/wie/wie_wipi_java" }
# ... one line per crate you consume, directly or transitively
```

Consuming crates still declare the **upstream git dependency**:

```toml
# your_host_crate/Cargo.toml
wie_backend = { git = "https://github.com/dlunch/wie.git" }
wie_ktf     = { git = "https://github.com/dlunch/wie.git" }
```

**Why `[patch]` and not direct path dependencies.** `wie_core_arm` depends on `arm32_cpu`
*transitively*. A path dependency in your own manifest cannot reach it; you would have to edit the
wie crates' manifests too, which makes them un-rebasable. One `[patch]` section at the workspace
root applies the interpreter fork to every crate in the graph — including crates you never name.

**Consequence to expect:** `Cargo.lock` will contain **no** `git+https://github.com/dlunch/wie.git`
source line at all. The wie crates appear as plain path deps with no `source` key. Only the
*transitive* git dependencies (the JVM implementation, the audio format crates, the WIPI type
crate) keep pinned revs. That is normal and is exactly why §3 is mandatory.

## 2. Repeat the transitive patch inside the vendored workspace

`vendor/wie/Cargo.toml` is its own workspace. Add the arm32_cpu patch there as well:

```toml
# vendor/wie/Cargo.toml
[patch."https://github.com/dlunch/arm32_cpu.git"]
arm32_cpu = { path = "../arm32_cpu" }
```

Two reasons, both load-bearing:

1. `cd vendor/wie && cargo test` then exercises **the same interpreter the shipped binary runs**.
   Without it, the vendored workspace's tests silently run against pristine upstream, and a test
   that passes there proves nothing about your build.
2. Any feature you add to `arm32_cpu` (see §5) does not exist upstream, so feature resolution
   inside `vendor/wie` fails outright without the patch.

## 3. `vendor/UPSTREAM.txt` — the file the reference port did not have

**This is the single most important correction to the reference implementation.** Its vendored
trees were *copied out of the cargo git checkout cache* (the giveaway is a `.cargo-ok` marker file
in the tree root), with no `.git` directory and no recorded base commit. The result:

- there is no way to `git diff` the fork against upstream;
- there is no way to rebase onto a newer upstream;
- a large first batch of fixes was committed *inside* the import commit, so
  **`git diff HEAD -- vendor/` understates the true fork delta** — it shows only the deltas made
  after the import, not the fork itself.

Write this before your first edit and refresh it on every re-vendor:

```
# vendor/UPSTREAM.txt
wie        https://github.com/dlunch/wie.git         <base-commit-sha>  <YYYY-MM-DD>
arm32_cpu  https://github.com/dlunch/arm32_cpu.git   <base-commit-sha>  <YYYY-MM-DD>
```

A git submodule pinned to your own fork commit is the other acceptable answer. What is not
acceptable is a copied tree with no provenance.

## 4. Diffing your fork against its base

If you inherited a provenance-less vendored tree, the base is usually still on disk in the cargo
checkout cache. Identify the right rev by file-count: the correct base differs in tens of files,
the wrong one in hundreds.

```bash
UP="$CARGO_HOME/git/checkouts/wie-<hash>/<short-sha>"
UPA="$CARGO_HOME/git/checkouts/arm32_cpu-<hash>/<short-sha>"

# which rev is the real base?
diff -rq "$UP" vendor/wie -x target -x .git -x Cargo.lock -x .cargo-ok | wc -l

# per-file diff
diff -u --strip-trailing-cr "$UP/<path>" "vendor/wie/<path>"
```

⚠ `--strip-trailing-cr` is **required** when the working tree was checked out on Windows, or every
line reads as changed and the diff is useless.

## 5. Feature pass-through: forwarding a feature to a transitive crate

You will want a compile-time switch in the interpreter (per-instruction tracing) that nothing in
your tree depends on directly. Cargo features do not travel by themselves — build an explicit
chain, one hop per manifest:

```toml
# vendor/arm32_cpu/Cargo.toml
[features]
insn_trace = []

# vendor/wie/wie_core_arm/Cargo.toml
[features]
insn_trace = ["arm32_cpu/insn_trace"]

# vendor/wie/wie_ktf/Cargo.toml
[features]
insn_trace = ["wie_core_arm/insn_trace"]

# your probe crate's Cargo.toml
[features]
insn_trace = ["wie_ktf/insn_trace"]
```

Without the chain the feature exists and is unreachable — `cargo build --features insn_trace` from
your workspace root will not enable it, and it fails quietly with no error.

## 6. Pin the toolchain

The reference port pinned nothing: no `rust-toolchain.toml`, edition 2024 declared in the manifest
with no minimum compiler recorded. Add one at the workspace root so the vendored tree, the host
crate and CI agree:

```toml
# rust-toolchain.toml
[toolchain]
channel = "1.<NN>.0"
targets = ["aarch64-linux-android"]
components = ["rustfmt", "clippy"]
```

Also delete any inherited `.cargo/config.toml` carrying absolute NDK linker paths. Those paths are
machine-specific, they are dead configuration (the cargo-ndk environment variables win), and their
embedded API level typically disagrees with both the build flag and `minSdk`.

## 7. What the fork delta looked like, categorised

Use this as a budget estimate, not a checklist — sizes are from one port **(B)**, the categories
are what any port hits **(A)**.

| Area | ~Δ lines | What | Tag |
|---|---|---|---|
| `wie_core_arm/src/allocator/list.rs` | +225 | lazy coalescing in the walk, corrupt-header containment | **A** |
| `wie_core_arm/src/allocator/bucket.rs` | +340 | free chain + bump index + indexed in-use map | **A** |
| `wie_ktf/.../jvm_support/oom.rs` | +223 new | pre-allocated OOM exception | **A** |
| `wie_ktf/.../jvm_support/{class_definition,array_class_definition,jvm_implementation,method}.rs` | +74 | route throw sites and two `unwrap()`s through the OOM path | **A** |
| `wie_ktf/src/emulator.rs` | +99 | host memory access, host→guest calls, **JVM thread attach** | **A** |
| `wie_backend/src/guest_call.rs` | +67 new | lock-free three-state result cell | A |
| `wie_backend/src/lib.rs` | +81 | `Emulator` host-introspection methods, offscreen-copy hook | A |
| `wie_backend/src/platform.rs` | +67 | `Network` trait, defaulted to `None` | A |
| `wie_wipi_c/src/api/net.rs` + KTF method table | +90 | `trace_stub`, table padding, real slot implementations | **B** (method A) |
| `wie_wipi_c/src/api/database.rs` | +400 | record-size query mode, name-keyed delete, unknown-slot no-ops — **plus the destructive half**: a deferred create-mode truncate held as a per-handle flag, truncate cancellation on every failure and early-return path, the store's write verdict propagated to the guest instead of discarded, and warn-level logging of create-mode opens and executed truncates | **A** for the destructive half (upstream defect), **B** for the slot values |
| `wie_wipi_c/src/api/graphics{,/framebuffer}.rs` | +440 | row-band framebuffer access | **A** |
| `wie_backend/src/canvas.rs` | +318 | span intersection, raw row copy, alpha fast paths | **A** |
| `wie_wipi_c/src/api/media.rs`, `wie_backend/src/system/audio.rs`, `wie_backend/src/audio_sink.rs` | +900 | per-clip volume keyed by clip pointer; **and the rest of audio**: a parse cache with handle lifetime management (upstream leaked one file copy per data-upload call), a wall-clock catch-up rebase in the sequencer, one shared volume law plus a capability-probing sink method, parse-time bus classification, and the deferred-stop state machine with a host-owned music channel | **A** — mostly upstream defect, like the allocator rows |
| `wie_util/src/lib.rs` | +67 | `stats` module (no_std atomics) | A |
| `wie_core_arm/src/engine/arm32_cpu.rs` | +174 | batch-wide memory adapter, retired-instruction count | **A** (perf) |
| `wie_core_arm/src/{core,thread_wrapper}.rs`, `wie_backend/src/executor.rs` | +102 | nesting, task-count and native-stack gauges | A (diagnostic) |
| `arm32_cpu/src/{arm,thumb}.rs` | +33 | Thumb decode LUT, `insn_trace` gating | **A** (perf) |
| `wie_wipi_c/src/api/kernel.rs` | +3 | timer callback `tracing` level demoted to debug | **A** |

**Zero opcode fixes.** See [diagnostics.md](diagnostics.md) §6.

**Two ways this table has already misled a reader, both worth stating out loud (A).**

- **Audio is not a title-specific afterthought.** An earlier version of this table carried audio as a
  single +110 line for one clip-volume quirk. The real delta in those files is several times that and
  is **the same kind of upstream-defect work as the allocator and framebuffer rows above it** — a
  per-call archive re-read, a leaked parse per handle, a sequencer on a wall clock, two buses with two
  different volume laws. Budget it accordingly. [audio-core.md](audio-core.md).
- **This table is the *fork*, not the delta.** The host-side half of some subsystems is bigger and
  more safety-critical than the vendored half. The clearest case is saving: against ~400 vendored
  lines in `database.rs` sits a host-side record store **and** a pre-image vault — atomic write,
  retention, restore-at-boot, export, the write/delete policy gate, and the load observation that
  feeds it — roughly **2,100 lines** in the reference port, and every one of them is the difference
  between a player keeping their character and not. That work is the
  [save-integrity](../../save-integrity/SKILL.md) skill.

## 8. Working rules inside vendored code

- **Keep upstream's formatting.** Run the vendored workspace's own `cargo fmt` with its own
  `rustfmt.toml`, not your host crate's. A whole-file reformat destroys your ability to diff
  against the base.
- **Comment every divergence with the symptom that caused it.** A doc-comment naming the failure
  ("upstream `free` only cleared the in-use bit, so blocks could never grow back") is the only
  thing that survives to the next re-vendor.
- **Add a regression test inside the vendored crate**, not in your host crate. The test travels
  with the fix when someone rebases.
- **Never make a containment change fatal.** See the corrupt-header rule in
  [hardening-batch.md](hardening-batch.md) §1 — a hardening change that upgrades a survivable error
  to `FatalError` can make the emulator die *earlier* than the bug it was meant to fix.

---

## 9. Before you blame a dependency, check whether you have already forked it

**(A) A vendored fork is invisible to a normal read of the dependency.** `[patch]` redirects the
build; it does not redirect a *reader*. Someone debugging a hot path opens the published source, or
the upstream release notes, and reasons carefully about code their binary does not contain. This is a
**diagnostic** rule, not a setup rule, and it belongs in the same file as the setup because that is
where a reader will look for it.

Before suspecting any dependency's hot path:

1. read the workspace root's `[patch]` / `[patch.crates-io]` sections — that is precisely the list of
   crates whose published source is **not** what you are running;
2. read the **vendored** source of the function you suspect;
3. only then form a hypothesis.

**(B)** The reference port lost half a day reading an upstream windowing/surface crate's release and
came close to hand-reimplementing two per-frame costs — a full-window zeroed allocation on every
buffer acquisition (~10 MB at phone resolution) and a per-byte channel swizzle (~10 M single-byte
stores per frame) — **both of which its own fork had already fixed**. Upstream even carried its own
TODO on the second. `HIGH / CONFIRMED`; the note that recorded it was written as an instruction to the
next session, which is the right instinct.

**(A) The cheap prevention: put the *why* in the manifest, where a reader of the dependency sees it.**
One comment per patched crate, naming the symptom that forced the fork:

```toml
[patch.crates-io]
# forked: this backend allocated and zeroed a whole window buffer per acquisition and swizzled
# channels one byte at a time -- ~10 MB of allocation and ~10 M single-byte stores per frame at
# phone resolution. Base commit in vendor/UPSTREAM.txt -- see 3 above.
<crate> = { path = "vendor/<crate>" }
```

Same principle as §8's "comment every divergence with the symptom that caused it", moved one level
up: the divergence comment helps whoever opens the fork; the manifest comment helps whoever never
thinks to.
