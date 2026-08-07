# The selftest convention

`cargo test` cannot run the tests for an Android application crate. This document explains why,
gives the harness that replaces it, and states the two rules that make the replacement trustworthy
rather than decorative.

The convention shapes the whole codebase, not just its tests: **everything decidable without an
emulator lives in dependency-free modules**, so the phone is only needed for the things that
genuinely require a phone.

## Contents

- [1. Why `cargo test` dies](#1-why-cargo-test-dies)
- [2. The harness](#2-the-harness)
- [3. The two hard rules](#3-the-two-hard-rules)
- [4. Supporting rules](#4-supporting-rules)
- [5. Adding a new selftest](#5-adding-a-new-selftest)
- [6. The forbidden mirror form](#6-the-forbidden-mirror-form)
- [7. What belongs in a selftest, and what does not](#7-what-belongs-in-a-selftest-and-what-does-not)
- [8. Running them all, and CI](#8-running-them-all-and-ci)

---

## 1. Why `cargo test` dies

`cargo test -p <app-crate>` builds the crate **for the host triple** and links a test harness
against it. For an Android application crate that link graph is the problem:

- the crate is a `cdylib` named to satisfy `System.loadLibrary`, not a normal `lib`;
- it depends on a windowing library configured for the Android game-activity backend;
- that pulls in the Android activity glue, which expects a JVM, a `JNIEnv`, an
  `AAssetManager`, an activity lifecycle and an `android_main` entry point supplied *by the
  platform*;
- several of those crates provide host-triple builds that compile but whose runtime
  initialisation immediately dereferences a global the platform was supposed to have filled in.

The failure mode is host-dependent and none of the variants are useful: on some hosts the link
fails, on others the test binary builds and then dies during static initialisation before a single
test runs, and on others it runs but every test touching the platform layer aborts the process. The
reference port's host is in the second category — `cargo test -p <app-crate>` dies at startup.

**Generalise it as:** *an application crate for a target platform cannot host unit tests on the
development host, because its link graph assumes a runtime that only exists on the target.* This is
equally true for iOS, for wasm application crates, and for any `cdylib` whose entry point is called
by a foreign runtime. It is not a quirk of one machine, and it is not fixable by feature flags —
the dependency is real, the app genuinely needs it, and it genuinely cannot be satisfied on the
host.

Two things that do **not** solve it:

- **`#[cfg(test)]` guards.** They remove your test code, not the dependency graph.
- **A `dev-dependencies` split.** The offending crates are normal dependencies of the shipping
  binary.

---

## 2. The harness

Extract the pure logic into modules with **zero imports**, put a standalone test file at the
repository root, and have that file `include!` the real source. Then compile it with plain `rustc`
— no Cargo, no dependency graph, no platform:

```powershell
rustc --edition 2021 --test endpoint_selftest.rs -o tt_endpoint.exe; ./tt_endpoint.exe
rustc --edition 2021 --test save_selftest.rs     -o tt_save.exe;     ./tt_save.exe
rustc --edition 2021 --test layout_selftest.rs   -o tt_layout.exe;   ./tt_layout.exe
```

```bash
rustc --edition 2021 --test endpoint_selftest.rs -o tt_endpoint && ./tt_endpoint
```

A selftest file looks like this:

```rust
//! Selftest for src/endpoint.rs.
//!
//! This include!s the REAL source, not a copy — the parsing rules under test are exactly the
//! ones the app ships. Run:  rustc --edition 2021 --test endpoint_selftest.rs -o tt && ./tt

include!("<app-crate>/src/endpoint.rs");

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ipv6_literal_is_not_split_on_its_last_colon() {
        // ★ 2026-07-19: "::1" was parsed as host ":" port 1 by a naive rsplit(':').
        assert_eq!(parse("::1"), Some(Endpoint::new("::1", DEFAULT_PORT)));
    }
}
```

Add the test binaries to `.gitignore` — they are several megabytes each.

The same `include!` trick generalises past tests. A headless probe crate can `include!` the app's
renderer module, so the emulator diffs the **shipping** renderer against the guest's own output.
`include!` is the cheapest way to get one source file into N different link environments.

---

## 3. The two hard rules

### Rule 1 — `include!` the real source. Never a copy.

The whole point is that the addresses, encodings, offsets, step orders and thresholds under test are
*the ones the application issues*. A test over a copy tests the copy. It will pass while the
shipping code is broken, and it will do so most reliably exactly when the two have drifted — which
is to say, exactly when you need the test.

Write the reason in the file header, every time. It is the sentence that stops a future
contributor "simplifying" the `include!` into a `mod`.

### Rule 2 — an `include!`d module must stay dependency-free.

No `use` of any external crate. No `use` of another module in the app crate. Standard library only,
and prefer `core`-shaped code where it is natural.

Put the prohibition in the file itself, at the top:

```rust
// This file is include!d by <name>_selftest.rs, which compiles with plain rustc and has no
// dependency graph at all. DO NOT ADD IMPORTS HERE.
```

Consequences you will feel, and the workarounds:

- **A module cannot carry `//!` inner doc comments**, because an `include!`d file is spliced into
  the middle of another file. Put the module documentation on the `mod x;` item in `lib.rs` instead
  — one doc comment per included module, stating its no-dependency contract.
- **Guest memory is read through a closure**, `impl Fn(u32, usize) -> Vec<u8>`, not through an
  emulator handle. That is what makes every parsing function pure and unit-testable: the test feeds
  a map-backed reader and the app feeds the real emulator.
- **Selftest-only helpers need `#[allow(dead_code)]`**, because the crate build cannot see the use —
  the only caller is a test file that `include!`s rather than links.
- **Generated code participates.** A code generator that emits Rust source (rather than a runtime
  asset) lets the selftest `include!` the generated table too. That is how a missing CJK glyph —
  which would otherwise render as an invisible hole rather than as tofu — becomes a test failure.

---

## 4. Supporting rules

**Design for it up front.** When you are about to write logic, ask whether it needs the platform. If
it does not, it goes in a dependency-free module. The list of things that turn out not to need the
platform is longer than it first looks: endpoint and address parsing, protocol framing, buffer
handoff contracts, layout resolvers, patch-halfword encoders, state machines, timer sentinels,
record parsers, glyph coverage, minimap rasterisation.

**Pin encodings in code, not in prose.** A patch that rewrites a Thumb immediate should compute the
halfword in a function, and the selftest should assert the function's output over the whole legal
range — including that out-of-range values are *refused* rather than silently truncated. "The
encoding bounds the feature" is a design constraint you want a test to enforce.

**Test the sentinel cases.** Two real bugs from the source material, both caught only after they
shipped, both trivially testable:

- a timer whose "last fired" sentinel of `0` was treated as "now", making `now >= now + interval`
  false at every poll and silently disabling the feature forever;
- a length assertion pinned to a measured value, which fired on the first real input because the
  quantity is content-dependent. The fix is a band, and the test asserts the band's edges.

**One assertion, one finding.** Name the test after the finding, and put the date and symptom in a
comment above it. A failing test should tell the reader which claim just became false.

---

## 5. Adding a new selftest

1. **Isolate the logic.** Move it into `<app-crate>/src/<name>.rs` with no imports. If it currently
   takes an emulator handle, change the signature to take a read closure.
2. **Document the module on its `mod` item** in `lib.rs`, and add the no-imports warning comment
   inside the file itself.
3. **Create `<name>_selftest.rs` at the repository root**, with the header from §2 and
   `include!("<app-crate>/src/<name>.rs");`.
4. **Write tests that restate findings.** Each assertion should correspond to something you
   discovered, with its provenance in a comment. See [evidence-standard.md](evidence-standard.md).
5. **Run it**: `rustc --edition 2021 --test <name>_selftest.rs -o tt && ./tt`.
6. **Register it** in the project's test-runner script and in CI, and add the produced binary to
   `.gitignore`.
7. **Never let the module gain a dependency.** If you find yourself needing one, the right move is
   almost always to hoist the dependency-needing part into the caller and keep the pure part pure.

---

## 6. The forbidden mirror form

There is an older, weaker variant of this pattern: a standalone test file that **re-declares** the
constants and functions it wants to test, with a comment saying `// Mirrors src/items.rs`.

**Do not use it, and convert any instance you find.**

It fails in the one way that matters. In the reference port, a mirror declared a clamp constant as
`160` while the shipping value was `211`. The selftest passed. It had been passing for weeks. It was
asserting a property of a number that no longer existed anywhere except in the test file.

The failure is not "the mirror got out of date" — that is the symptom. The failure is that **a
mirror inverts the test's meaning**: instead of the test constraining the code, the code and the
test become two independent copies that agree only by luck, and green tests become evidence of
nothing. Worse, the greenness is actively misleading, because the reader's model is "this is
covered".

If a mirror is unavoidable for some reason — usually because the real module has a dependency you
have not yet hoisted out — treat it as an untested module with a TODO, not as a tested one. Do not
count its assertions in the project's test tally.

---

## 7. What belongs in a selftest, and what does not

Three tiers, in ascending order of cost. Push every question down to the cheapest tier that can
answer it.

| tier | cost | answers |
|---|---|---|
| **standalone selftest** | ~1 s, no device, no emulator | Encodings, parsers, formats, geometry, resolvers, state machines, patch halfwords, coverage predicates, bounds and refusals. |
| **headless probe** | ~1 min, no device | Does it boot; is the frame non-black; does the guest routine behave as claimed; does my host reimplementation match the guest's own output byte-for-byte; are the heap gauges flat. |
| **device** | minutes, plus a physical phone | Anything involving real input latency, the platform's JNI behaviour, the real network, orientation and lifecycle, and every claim about *error* behaviour under conditions a LAN cannot reproduce. |

The tier boundaries are also a debugging strategy: a bug that reproduces in tier 1 is a bug you can
fix in a loop measured in seconds. Most of the effort in structuring the code this way is repaid the
first time a device-only bug turns out to be reproducible in a selftest after a small refactor.

The vendored emulator core is a separate case: it *is* an ordinary Rust workspace and its own
`cargo test` works. Run it. Equivalence tests inside the core — "the optimised path produces
byte-identical output to the reference path" — are the right place for graphics and allocator
changes, and they are the reason those changes can be shipped at all.

---

## 8. Running them all, and CI

Ship one script that compiles and runs every selftest, prints a per-file pass count, and exits
non-zero on any failure. Give the shell and PowerShell versions identical stage names so the CI
transcript and a developer's local run are comparable line for line.

The CI job is a plain Linux job. It needs `rustc` and nothing else for the selftests, and
`cargo-ndk` plus the Android target for the build. It must **not** try to run `cargo test --all`
over a workspace containing the application crate — that is precisely the command this document
exists to explain, and an inherited upstream workflow will attempt it.

A useful CI shape:

1. `cargo fmt --check` and `cargo clippy` over the crates that can be host-built.
2. Every `*_selftest.rs`, compiled and run.
3. The vendored core's own `cargo test`.
4. `cargo-ndk` build of the application crate for the Android target (compiles and links; does not
   run).
5. Optionally, a headless probe boot with an assertion that a dumped frame has a non-zero non-black
   pixel count — a device-free regression gate against the black-screen class of failure. This one
   requires a game archive, so it only runs where a dump is available; keep it as an opt-in job.
