# Tests, device-free gates, and CI

Provenance: **(A)** generic · **(B)** worked example from one reference port · **(C)** never copy.

## Contents

1. [`cargo test` is not the test command](#1-cargo-test-is-not-the-test-command)
2. [The standalone selftest harness](#2-the-standalone-selftest-harness)
3. [The dependency-free rule](#3-the-dependency-free-rule)
4. [Fast compile check](#4-fast-compile-check)
5. [The headless probe as a device-free gate](#5-the-headless-probe-as-a-device-free-gate)
6. [Differential verification](#6-differential-verification)
7. [Formatting and lints](#7-formatting-and-lints)
8. [The CI the reference did not have](#8-the-ci-the-reference-did-not-have)
9. [A workflow that works](#9-a-workflow-that-works)
10. [Gate summary](#10-gate-summary)

---

## 1. `cargo test` is not the test command

The shippable crate is an Android `cdylib` that links winit and the platform activity glue. On a
desktop host, `cargo test -p <host crate>` **dies on startup** — the link graph pulls in the
Android platform crates, and the test binary never reaches a test. This is not a misconfiguration
to fix; it is what an Android-only crate is.

The instinctive workarounds both fail:

| workaround | why it fails |
|---|---|
| `#[cfg(test)]` modules in the crate | still linked into the same test binary |
| feature-gating the Android deps out | you then test a program that is not the program you ship |
| an integration-test crate | same dependency graph, same death |
| running tests on device | far too slow for the inner loop, and needs a device |

## 2. The standalone selftest harness

**The convention that replaces it (A), and it is strong enough to be worth adopting on day one:**

> Pure logic lives in **dependency-free source files**. Standalone test files at the repository
> root **`include!` the real source** — not a copy of it — and are compiled by `rustc --test`
> with no Cargo, no dependency graph, and no platform link.

```rust
// landscape_selftest.rs  (repo root)
include!("wie_android/src/placement.rs");

#[test]
fn control_never_overlaps_an_obstacle() { /* … */ }
```

```powershell
rustc --edition 2021 --test landscape_selftest.rs -o t.exe; ./t.exe
```

```bash
rustc --edition 2021 --test landscape_selftest.rs -o t.out && ./t.out
```

Properties that make this worth the oddity:

* It tests **the shipping source**, byte for byte. A mirror copy would drift within a week; the
  `include!` cannot.
* It runs in under a second, with no toolchain beyond `rustc`.
* It runs in CI on any OS with no Android SDK at all.
* It scales: the reference had 9 such files and ~150 assertions covering layout, endpoint
  parsing, buffered TLS reads, item tables, save-chain state and page persistence **(B)**.

Write a tiny runner script (`run_selftests.sh` / `.ps1`) that loops over `*_selftest.rs`, and
**gitignore the produced binaries** — they are ~7 MB each:

```gitignore
/t?.exe
/t?.out
/tt_*.exe
```

## 3. The dependency-free rule

The `include!`d files — the layout resolver, endpoint parser, buffered reader, save state
machine, glyph table, minimap renderer, and anything else you want covered — **must never gain a
dependency**. Not `log`, not `serde`, not a helper from elsewhere in the crate.

Enforce it, because it will be violated by accident:

* put a one-line header comment in each such file saying so;
* list them in `CLAUDE.md`/`AGENTS.md`;
* the selftest run **is** the enforcement — a new `use` of an external crate breaks
  `rustc --test` immediately, with a clear error. That is the point: the harness fails loudly the
  moment the invariant is broken, which is far better than a lint.

Where a dependency-free module genuinely needs a platform call, invert it: take a small trait or
a closure parameter, and let the caller supply the platform half.

## 4. Fast compile check

```
cargo ndk -t arm64-v8a --platform 26 check
```

Run it from the host crate directory. It type-checks the real target without linking or building
an APK; it is the inner loop while editing Rust.

Set the bar at **zero warnings** and hold it. On a project that vendors a forked emulator, a
warning you have decided to tolerate is a warning you will stop reading, and the interesting ones
(unused result, unreachable pattern, dead code that used to be a feature) are exactly the ones
that matter in this codebase.

## 5. The headless probe as a device-free gate

The probe crate is a workspace member with the **same `[patch]`ed emulator core the APK ships**,
so what it exercises is what runs on the phone.

```
cargo build --release -p wie_probe
./target/release/wie_probe --zip work/<archive>.zip --w 496 --h 240 \
  --db work/probe/db --frames 900 --ms 20 --keys 260:OK,300:OK,360:OK --map-check 1
```

⚠ **`--ms` is mandatory.** The guest idles on a platform timer; with no virtual clock advancing,
the emulator runs and the screen stays **black**, and you will spend an hour looking for a
rendering bug that does not exist. Make the flag required, not defaulted, so the mistake is
impossible. **(A)**

Feature flags worth mirroring **(B)**:

| feature | purpose |
|---|---|
| `quiet_release` (default) | mirrors the APK's compile-time log filtering, so instructions/second measured headlessly is comparable to the device |
| `insn_trace` | per-instruction trace; needs `--no-default-features` to see draw-call traces |

What the probe can gate, with no device:

* the archive still loads (`loaded` printed, backend detected);
* a dumped frame reports **non-black pixel count > 0** — the single best smoke test;
* no allocation failure across tens of thousands of frames;
* the live-heap gauge is flat in steady state (a leak shows as a rising floor);
* no fatal unimplemented platform call along a scripted input path;
* the on-disk database layout matches what the device writes.

Put "build the probe and run N frames" in CI. It is the cheapest possible regression test for a
change to the vendored core.

## 6. Differential verification

When the host reimplements something the guest also draws (a minimap, a status widget), keep the
renderer in a **dependency-free file that both the app and the probe `include!`**. The probe can
then compare the *shipping* renderer's output against the guest's own framebuffer and report
mismatched pixels.

⚠ **Correction to carry, not the error:** in the reference, the differential check was **gated on
an explicit dump-frame list** — the invocation printed in its own documentation could not
produce any comparison output at all, and the feature looked broken. If you build this, either
remove the gate or make `--help` and the docs state it. A verification tool that silently
verifies nothing is worse than no tool. **(A)**

## 7. Formatting and lints

* One `rustfmt.toml` at the repository root, with **the same settings as the vendored emulator's
  own** (e.g. a wide `max_width`). This is deliberate: it keeps diffs against the fork minimal, so
  a re-vendor is reviewable.
* `cargo fmt` and `cargo clippy --workspace` before every commit — the vendored core's own
  contributing rules require it, and your fork inherits them.
* `.editorconfig`: LF, final newline, trim trailing whitespace, UTF-8.

## 8. The CI the reference did not have

**(B)** The reference inherited its workflows from the upstream emulator project. `rust.yml` ran
`cargo fmt --check`, `cargo clippy --all` and **`cargo test --all`** on Ubuntu over a workspace
whose members included the Android host crate — i.e. it tried to host-build the one crate that
§1 says cannot be host-built. There was **no CI for the Android build, the APK, or the
selftests**. Web-target workflows and `package.json` were inherited too and are irrelevant to a
native port; a scaffold should drop them.

So: **do not inherit the upstream project's CI. Write one for what you actually ship.**

## 9. A workflow that works

Everything below runs on a plain Linux runner. Nothing in it needs the windows-gnu shim
([host-portability.md](host-portability.md) §3), and nothing needs a device.

```yaml
name: android
on: [push, pull_request]

jobs:
  selftests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: standalone selftests
        run: |
          for f in *_selftest.rs; do
            rustc --edition 2021 --test "$f" -o "/tmp/$(basename "$f" .rs)"
            "/tmp/$(basename "$f" .rs)"
          done

  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: cargo fmt --all -- --check
      - run: rustup target add aarch64-linux-android
      - run: cargo install cargo-ndk --version <pin>
      - run: cargo ndk -t arm64-v8a --platform 26 clippy -- -D warnings

  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-java@v4
        with: { distribution: temurin, java-version: '17' }
      - uses: android-actions/setup-android@v3
      - run: sdkmanager --install "ndk;<pinned revision>"
      - run: rustup target add aarch64-linux-android
      - run: cargo install cargo-ndk --version <pin>
      - name: build_android.sh (debug, loopback config)
        run: ./build_android.sh --local-server
      # NOTE: no game archive in CI -> the fail-fast asset check must be skippable,
      # e.g. ALLOW_MISSING_ARCHIVE=1, and CI must NOT produce a distributable APK.

  probe:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: cargo build --release -p wie_probe
      # A boot-smoke run needs an archive, which CI does not have. Keep this job to
      # "the probe compiles against the vendored core"; run the boot smoke locally.
```

Three CI-specific facts to design around:

1. **CI has no game archive** and must never be given one. So CI can build, lint and run the
   selftests, but it cannot run a boot smoke test, and it must not upload an APK artifact.
2. **CI has no keystore.** A release job would produce `app-release-unsigned.apk`
   ([build-script.md](build-script.md) §8). Either skip signing in CI entirely, or inject the
   keystore from encrypted secrets and accept that your signing key now lives in a CI provider.
   The safer default for a hobby port is: **release builds are local only.**
3. **CI cannot run the artifact-config gate meaningfully**, because there is no real destination
   to bake. Run it with a synthetic value so the *gate itself* is tested
   (`--require-string tls://ci.invalid`), which catches a broken gate before it matters.

## 10. Gate summary

| gate | where | blocks |
|---|---|---|
| standalone selftests pass | CI + pre-commit | logic regressions in the dependency-free modules |
| `cargo fmt --check`, clippy `-D warnings` | CI | style drift, and the warnings that are real |
| `cargo ndk … check` clean | inner loop | target-specific compile errors |
| probe: `loaded` + non-black pixels | local, per core change | the emulator no longer boots the archive |
| probe: N frames, no allocation failure, flat live heap | local, before release | slow-burn core regressions |
| artifact config scan | build stage 7 | a silently misconfigured release |
| `apksigner verify` | build stage 9 | an unsigned or wrongly-keyed handout |
| `publish_audit.py --all-refs` | before any push to a public remote | secrets and licensed content |
