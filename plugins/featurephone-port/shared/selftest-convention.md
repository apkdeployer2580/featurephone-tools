# The selftest convention

`cargo test` cannot run the tests for an Android application crate on the host most porters develop
on. This document explains why, gives the harness that replaces it, states the two rules that make
the replacement trustworthy rather than decorative — and then the two ways a suite built correctly
to those rules is still green while the shipping code is wrong.

The convention shapes the whole codebase, not just its tests: **everything decidable without an
emulator lives in dependency-free modules**, so the phone is only needed for the things that
genuinely require a phone.

Tags: **(A)** generic procedure · **(B)** worked example from the reference port, re-derive it ·
**(C)** do not copy. Conventions: [evidence-standard.md](evidence-standard.md).

## Contents

- [1. Why `cargo test` dies — and the host where it does not](#1-why-cargo-test-dies--and-the-host-where-it-does-not)
- [2. The harness](#2-the-harness)
- [3. The two hard rules](#3-the-two-hard-rules)
- [4. Supporting rules](#4-supporting-rules)
- [5. Adding a new selftest](#5-adding-a-new-selftest)
- [6. The forbidden mirror form](#6-the-forbidden-mirror-form)
- [7. Green and wrong: what an include! selftest does not protect against](#7-green-and-wrong-what-an-include-selftest-does-not-protect-against)
- [8. What belongs in a selftest, and what does not](#8-what-belongs-in-a-selftest-and-what-does-not)
- [9. Running them all: the runner, the count, and CI](#9-running-them-all-the-runner-the-count-and-ci)
- [10. Symptom table](#10-symptom-table)
- [11. Checklist](#11-checklist)

---

## 1. Why `cargo test` dies — and the host where it does not

`cargo test -p <app-crate>` builds the crate **for the host triple** and links a test harness
against it. For an Android application crate that link graph is the problem:

- the crate is a `cdylib` named to satisfy `System.loadLibrary`, not a normal `lib`;
- it depends on a windowing library configured for the Android game-activity backend;
- that pulls in the Android activity glue, which expects a JVM, a `JNIEnv`, an
  `AAssetManager`, an activity lifecycle and an `android_main` entry point supplied *by the
  platform*;
- several of those crates provide host-triple builds that compile but whose runtime
  initialisation immediately dereferences a global the platform was supposed to have filled in.

The failure mode is host-dependent and most of the variants are useless: on some hosts the link
fails, on others the test binary builds and then dies during static initialisation before a single
test runs, and on others it runs but every test touching the platform layer aborts the process. The
reference port's Windows host is in the second category — `cargo test -p <app-crate>` dies at
startup **(B)**.

**Generalise it as:** *an application crate for a target platform cannot host unit tests on a
development host that cannot satisfy its link graph, because that graph assumes a runtime which only
exists on the target.* This is equally true for iOS, for wasm application crates, and for any
`cdylib` whose entry point is called by a foreign runtime, and it is not fixable by feature flags —
the dependency is real and the app genuinely needs it.

Two things that do **not** solve it:

- **`#[cfg(test)]` guards.** They remove your test code, not the dependency graph.
- **A `dev-dependencies` split.** The offending crates are normal dependencies of the shipping
  binary.

### 1.1 One host usually can, and that tier is worth keeping

> **REFUTED.** *"An application crate cannot host unit tests on the development host. It is not a
> quirk of one machine."*
>
> **Refuted by** running the same command on Linux: the reference workspace's
> `cargo test -p <app-crate>` completes with **128 passing tests (B)** once one system package is
> installed for the audio backend (`libasound2-dev`). Nothing about the crate changed.
>
> **Why it was attractive:** the *generalisation* is sound and the observed failure was total, on
> the one host the work happened to be done on. "My host cannot" was written down as "no host can",
> which is the ordinary shape of an overstatement — the same sentence with the scope deleted.
>
> **Correction:** which failure mode you get is a property of the host. Some hosts satisfy the link
> graph, and a porter who reads this file as an absolute gives up an entire tier of coverage.

That tier is not redundant with the standalone harness. It holds tests the dependency-free form
**structurally cannot express (B)**: an end-to-end save-redirect test that drives the real on-disk
record store through the storage hook, and an `#[ignore]`d native-window preview harness that
renders host UI to image files for eyeballing. Neither can live in a module with no imports.

So the correct framing is two tiers, not one command:

| tier | runs where | holds |
|---|---|---|
| **standalone selftests** | every host, plain `rustc` | the portable inner loop — all pure logic |
| **workspace `cargo test`** | the host(s) whose link graph is satisfiable, and CI | end-to-end tests against real IO, `#[ignore]`d preview harnesses |

**(A) The rule that keeps the second tier alive: every module that pulls a target-only crate is
`#[cfg(target_os = "android")]`-gated.** JNI bindings, the activity glue, the platform audio sink.
Otherwise the host build stops compiling — silently, and only for whoever next tries it. **(B)** Two
JNI modules shipped in the reference port without that gate and broke the Linux path outright for
several sessions; nobody noticed, because the only people who run it are on the other OS. A CI step
that merely *builds* the workspace for the host triple catches this in one command.

---

## 2. The harness

Extract the pure logic into modules with **zero imports**, put a standalone test file at the
repository root, and have that file `include!` the real source. Then compile it with plain `rustc`
— no Cargo, no dependency graph, no platform:

```bash
# (A) The edition comes from the shipping crate — see 2.1. Read it once, use it everywhere.
ED=$(sed -n 's/^ *edition *= *"\(.*\)"/\1/p' Cargo.toml)     # workspace root, or the crate's own
rustc --edition "$ED" --test endpoint_selftest.rs -o tt_endpoint && ./tt_endpoint
rustc --edition "$ED" --test save_selftest.rs     -o tt_save     && ./tt_save
```

```powershell
# (A) No 2>&1 anywhere in this block — see 2.2.
$ed = (Select-String -Path Cargo.toml -Pattern '^\s*edition\s*=\s*"(.*)"').Matches[0].Groups[1].Value
rustc --edition $ed --test endpoint_selftest.rs -o tt_endpoint.exe
if ($LASTEXITCODE -ne 0) { throw "selftest build failed" }
.\tt_endpoint.exe
```

A selftest file looks like this:

```rust
//! Selftest for src/endpoint.rs.
//!
//! This include!s the REAL source, not a copy — the parsing rules under test are exactly the
//! ones the app ships. The edition must match the crate's (see 2.1).
//! Run:  rustc --edition <crate edition> --test endpoint_selftest.rs -o tt && ./tt

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

### 2.1 The edition is the shipping crate's, not the example's

> **REFUTED.** *"`rustc --edition 2021 --test <name>_selftest.rs` is the command."* Earlier
> revisions of this file printed `--edition 2021` in every example, in §2 and again in §5.
>
> **Refuted by** running it: the reference workspace declares `edition = "2024"`, and compiling one
> of its selftests under the documented 2021 fails outright —
> `error: let chains are only allowed in Rust 2024 or later`, at three sites **in the shipping
> source** **(B)**.
>
> **Why it was attractive:** the flag looks like part of the invocation rather than part of the
> *subject*. It reads as "how to run a test", and 2021 was true of the crate on the day the example
> was written.
>
> **Correction:** the standalone test compiles the shipping source, so it must compile it under the
> **same language rules the real build uses**. Derive the edition from the crate's `Cargo.toml`,
> following `edition.workspace = true` to the workspace root; never hardcode it in a document.

The benign direction of getting this wrong is a test that stops compiling and someone notices. The
malign direction is a test that **passes under older rules while the shipped build uses newer
semantics**. And a hardcoded example produces a third state, which is the one the reference port
actually ended up in: the edition became per-file folklore recorded in each selftest's doc comment —
**16 files saying 2021 and two saying 2024 (C)**, with nothing deriving either from the crate. The
runner in [§9](#9-running-them-all-the-runner-the-count-and-ci) reads it per file for that reason.

### 2.2 Never redirect the compiler's stderr

**(A)** In PowerShell 5.1, `rustc … 2>&1` wraps each native stderr line in an `ErrorRecord`
(`NativeCommandError`) and sets the success variable to false **even on exit code 0** — so compiler
*warnings* become a terminating error and a perfectly healthy selftest reads as a build failure.
`cargo … 2>&1` has the identical trap. Check `$LASTEXITCODE` explicitly and leave stderr alone.

This earns its own subsection because the commands above get pasted into scripts, and adding `2>&1`
to capture output is the obvious next thing a porter does. **(B)** The reference port hit it and
carries it as a standing prohibition; the same instruction appears in the build script's compile
stage one skill over — but not where it actually bites, which is here.

---

## 3. The two hard rules

### Rule 1 — `include!` the real source. Never a copy.

The whole point is that the addresses, encodings, offsets, step orders and thresholds under test are
*the ones the application issues*. A test over a copy tests the copy. It will pass while the
shipping code is broken, and it will do so most reliably exactly when the two have drifted — which
is to say, exactly when you need the test.

Write the reason in the file header, every time. It is the sentence that stops a future
contributor "simplifying" the `include!` into a `mod`.

> **Rule 1 buys freedom from drift and nothing else.** It does not make a green suite mean the code
> is right. The two failures it leaves wide open are
> [§7](#7-green-and-wrong-what-an-include-selftest-does-not-protect-against), and both have shipped
> bugs.

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
  — one doc comment per included module, stating its no-dependency contract. **The same constraint
  binds any code generator that emits an included file**: it must emit plain `//` comments, which is
  a fact you need when the generator is written rather than when the build error appears.
- **Guest memory is read through a closure**, `impl Fn(u32, usize) -> Vec<u8>`, not through an
  emulator handle. That is what makes every parsing function pure and unit-testable: the test feeds
  a map-backed reader and the app feeds the real emulator.
- **Selftest-only helpers need `#[allow(dead_code)]`**, because the crate build cannot see the use —
  the only caller is a test file that `include!`s rather than links.
- **Generated code participates.** A code generator that emits Rust source (rather than a runtime
  asset) lets the selftest `include!` the generated table too. That is how a missing CJK glyph —
  which would otherwise render as an invisible hole rather than as tofu — becomes a test failure.
  Generator hygiene and `--check` gates:
  [generated-assets.md](../skills/archive-prep/reference/generated-assets.md).
- **Compilation of the selftest is itself a regression gate.** A dependency-free module accumulates
  many small pure functions with no structural separation, and line-range deletion is the normal way
  it gets maintained — so **run the selftest before and after any range edit**. **(B)** Deleting
  three superseded functions by line range in the reference port also removed three unrelated
  detectors and a shared struct that happened to sit between them; the only thing that caught it was
  the standalone test failing to compile on an unresolved name.

---

## 4. Supporting rules

**Design for it up front.** When you are about to write logic, ask whether it needs the platform. If
it does not, it goes in a dependency-free module. The list of things that turn out not to need the
platform is longer than it first looks: endpoint and address parsing, protocol framing, buffer
handoff contracts, layout resolvers, patch-halfword encoders, state machines, timer sentinels,
record parsers, glyph coverage, minimap rasterisation.

**★ A decision you cannot afford to get wrong twice lives in ONE pure function, in one
dependency-free file, and every caller may only gather facts and ask it.** This is the strongest
structural rule here and it was learned the expensive way. **(B)** The reference port destroyed a
player's save while shipping a policy whose *rules were correct*: the load-bearing condition — a
boolean meaning "not currently in the shell" — lived in a **third module**, the one classifying which
screen the guest was on. No test in the suite could reach it, so nobody could see that it excluded an
entire code path in which the guard had literally never fired. **Coverage is not a property of the
policy; it is a property of where the policy's conditions live.** A condition sitting in a caller is
invisible to the policy's tests *and* to the policy's reader, and the resulting bug is not a wrong
rule but an unreachable one. Worked case:
[policy-and-diagnostics.md](../skills/save-integrity/reference/policy-and-diagnostics.md) §1-§2.

**Property tests for anything that can destroy data.** Example-based tests over a retention policy, a
rotation, a compaction or a truncation check the cases you thought of; the case that eats the last
copy is by definition one you did not. Write the generator yourself — a four-line seeded LCG — so the
test still compiles under plain `rustc` with no dependency graph, sweep seeds *and* corpus sizes
(including degenerate corpora), and put the seed and the size in every assertion message, because a
property failure you cannot replay is worthless.

```rust
fn next(s: &mut u64) -> u64 { *s = s.wrapping_mul(6364136223846793005).wrapping_add(1); *s >> 33 }
```

**(B)** The reference port's archival rotation is pinned by five properties — the newest generation is
never deleted; returned indices are unique, in range and under the ceiling; a second pass deletes
nothing (convergence); a 500-iteration write loop cannot exceed the ceiling; and a pinned generation
can never be aged out. Those are the tests that made it safe to *change* the policy afterwards:
[policy-and-diagnostics.md](../skills/save-integrity/reference/policy-and-diagnostics.md) §5.

**Pin encodings in code, not in prose.** A patch that rewrites a Thumb immediate should compute the
halfword in a function, and the selftest should assert the function's output over the whole legal
range — including that out-of-range values are *refused* rather than silently truncated. "The
encoding bounds the feature" is a design constraint you want a test to enforce. Pin the raw
*measurements* the same way, as literals, and validate the pin by mutation —
[§7.2](#72-the-suite-that-only-agrees-with-itself).

**Test the sentinel cases.** Two real bugs from the source material, both caught only after they
shipped, both trivially testable:

- a timer whose "last fired" sentinel of `0` was treated as "now", making `now >= now + interval`
  false at every poll and silently disabling the feature forever;
- a length assertion pinned to a measured value, which fired on the first real input because the
  quantity is content-dependent. The fix is a band, and the test asserts the band's edges.

**One assertion, one finding — and name the test after the finding.** Put the date and symptom in a
comment above it. A failing test should tell the reader which claim just became false.

**A regression test is named after the REPORT, not after the mechanism.** A test called
`redirect_requires_fresh_observation` dies with the mechanism it names; a test called
`local_start_load_repins_before_the_next_write` outlives every rewrite, because the *sequence a
player performed* is still something your code must survive three mechanisms later. Keep the report
test after the fix lands: an old report test staying green under attempt five is the only evidence
that five is not a regression of two. The name is also the review surface —
[§7.1](#71-a-suite-can-pin-a-wrong-premise-and-hold-a-bug-green).

---

## 5. Adding a new selftest

1. **Isolate the logic.** Move it into `<app-crate>/src/<name>.rs` with no imports. If it currently
   takes an emulator handle, change the signature to take a read closure.
2. **Document the module on its `mod` item** in `lib.rs`, and add the no-imports warning comment
   inside the file itself.
3. **Create `<name>_selftest.rs` at the repository root**, with the header from [§2](#2-the-harness)
   and `include!("<app-crate>/src/<name>.rs");`.
4. **Write tests that restate findings.** Each assertion should correspond to something you
   discovered, with its provenance in a comment. Every assertion that transcribes a *game rule*
   cites the disassembly site it transcribes, and every **refusal** carries a reason
   ([§7.1](#71-a-suite-can-pin-a-wrong-premise-and-hold-a-bug-green)). See
   [evidence-standard.md](evidence-standard.md).
5. **Run it with the crate's edition**:
   `rustc --edition "$ED" --test <name>_selftest.rs -o tt && ./tt`.
6. **Mutate one constant and confirm a test goes red.** If nothing fails, the suite is deriving its
   expectations from the very thing it is supposed to be checking
   ([§7.2](#72-the-suite-that-only-agrees-with-itself)). Revert the mutation in the same minute.
7. **Make sure the runner finds it** — which means the runner **globs** rather than reading a list
   ([§9](#9-running-them-all-the-runner-the-count-and-ci)) — and add the produced binary to
   `.gitignore`.
8. **Never let the module gain a dependency.** If you find yourself needing one, the right move is
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

Three further failure modes, all of them observed **(B)**:

- **A mirror of a deleted function stays green forever.** When a feature was removed from the
  reference app, its nine mirror tests had to be deleted **by hand**; had nobody remembered, the
  suite would have gone on asserting the behaviour of code that no longer existed — recorded at the
  time as *"we hit that trap for real this time"*. So: **delete the mirror in the same commit as the
  function.**
- **A mirror must never restate a build-keyed table or a bound.** Those are the values deciding
  whether a write lands on the right structure, and a stale copy of one is a crash on a build you did
  not test. The reference repo deliberately did *not* mirror its save-slot indexing for exactly this
  reason, and let the `include!`-based anchors selftest own it —
  [build-keyed-anchors.md](../skills/guest-re/reference/build-keyed-anchors.md).
- **A mirror's *negative* assertions can encode the shipping code's bug** and then defend it. That is
  a general failure rather than a mirror-only one; it is
  [§7.1](#71-a-suite-can-pin-a-wrong-premise-and-hold-a-bug-green).

### 6.1 The third form: pin the mirror to the source text

Sometimes the real definition genuinely cannot be `include!`d — it lives in a vendored `no_std`
crate next to the trait it serves, or it is bound up with a type the standalone build has no way to
see. This file used to offer one answer for that case: treat it as untested, with a TODO. **That is
still the right answer when the real source cannot be read at test time at all** (a definition behind
a proc macro, a value that only exists after codegen). It is no longer the first answer, because
there is a middle option that costs about eight lines.

**(A) Pin the mirror to the real source *text*.** The test reads the real file at test time, splits
on the function's signature, whitespace-normalises the body, and asserts equality against a literal —
with a failure message that names what to update.

```rust
// (A) The mirror above is a copy of <vendored-core>/src/<path>.rs::<fn>. This test fails the
// build when that source moves, instead of letting the copy drift silently.
#[test]
fn law_matches_the_vendored_core() {
    let src = std::fs::read_to_string("<vendored-core>/src/<path>.rs").expect("run from repo root");
    let body: String = src.split("fn <fn>(").nth(1).expect("signature moved — update the mirror")
        .split("\n}").next().unwrap().chars().filter(|c| !c.is_whitespace()).collect();
    assert_eq!(body, "<the normalised body, as a literal>",
               "the vendored law changed — update the mirror in this file and re-measure");
}
```

Drift then fails the build instead of passing silently, and the mirror's assertions become honest.

**(B)** The reference port needs this because one volume law lives in the vendored core beside the
trait it serves, while the host-side mixing module is `include!`d normally; the pinning test's own
comment names the earlier silent-drift incident it exists to prevent. Any port that splits one law
across core and host lands in exactly this position —
[audio-mixing.md](../skills/android-host/reference/audio-mixing.md),
[audio-core.md](../skills/emulator-core/reference/audio-core.md).

Note the assertion message doing two jobs: it says the mirror is stale *and* what to do about it.
Note also `run from repo root` — a test that reads a relative source path constrains the runner's
working directory, which is one of the things [§9](#9-running-them-all-the-runner-the-count-and-ci)
has to get right.

### 6.2 When even that is impossible: two honest options

Some modules can be neither included nor usefully text-pinned — host overlay drawing typically
references half the crate, and its output is a picture rather than a value. There are exactly two
honest options, and naming them beats pretending the module is covered:

1. **An `#[ignore]`d in-crate test that writes an image file** for side-by-side comparison against a
   device screenshot. It lives in the workspace tier
   ([§1.1](#11-one-host-usually-can-and-that-tier-is-worth-keeping)), runs only when asked, and its
   output is inspected by a human.
2. **A throwaway mirror renderer used for shape judgement only**, carrying an explicit *"no
   correctness claim rests on this"* label and excluded from the test tally.

**Visual verification is not optional here.** **(B)** A one-off full-resolution mock-up from a
throwaway renderer caught a real defect in the reference port — a button face indistinguishable from
the panel body at a 1 px border — that no assertion would ever have found. Both artefacts were
labelled as mirrors in the session notes at the moment they were produced, which is the only thing
stopping a later reader from citing a mock-up as evidence.

### 6.3 "Convert any instance you find" is aspirational — so record what is not covered

In practice mirrors are **partially** updated: each session patches the part it touched and leaves
the rest drifted, which is worse than an obviously stale mirror because the fresh parts make the file
look maintained. **(B)** The reference port carries one mirror whose enum is missing four whole
control types — so for those controls its 67 tests protect nothing — documented and still unfixed.

The honest mitigation, until conversion happens:

- state in the mirror's header **exactly which parts of the module it does not cover**, as a list a
  reader can check against the real source;
- mark it as an untested module with a TODO, not as a tested one;
- **do not count its assertions in the project's test tally**
  ([§9](#9-running-them-all-the-runner-the-count-and-ci)).

---

## 7. Green and wrong: what an include! selftest does not protect against

Rules 1 and 2 buy one thing: the test constrains **the shipping code**. They buy nothing about
whether what the shipping code does is right, and nothing about whether the suite is checking
anything at all. Both failures below have shipped bugs in the reference port, both were green
throughout, and both are invisible to a reader whose model is "this area is covered".

### 7.1 A suite can pin a wrong premise and hold a bug green

> **REFUTED.** *"The selftests are green and they `include!` the real source, so this area is
> covered."*
>
> **Refuted by** three shipped defects that a *passing* assertion was actively defending **(B)**:
>
> * **A negative-case list that contained a legitimate value.** A host constant set an equipment
>   category's lower bound one too high, so an entire item category silently vanished from a feature
>   — and the selftest's negative list had that same category as its **first entry**. Twenty-three
>   green tests were continuously proving *"this legitimate category is not equipment"*. Re-reading
>   the guest showed both of its gates are **upper bounds only** (`cmp #0xc` / `bgt`, with no
>   instruction anywhere testing for zero) and that the recompute routine indexes its table with no
>   `-1`.
> * **An expected-`None` assertion over a pair that should have matched.** A save-slot test asserted
>   that a sequence produced "no change", under a comment reading *"already pinned — the walk is not
>   a load"*. That exact input **was the reported destructive sequence**. The test was the
>   specification, and the specification was the bug.
> * **A named conformance test asserting a byte width that was wrong.** The reported input was
>   sitting in a passing assertion the entire time; finding it eventually formed part of the
>   diagnosis, and doing it first would have been the shortest path.
>
> **Why it was attractive:** the test is *real*. It exercises the shipping code, it cannot drift, it
> is green, and green reads as coverage. `include!` fixes one of the two failure modes, and the
> strength of the argument for it is what makes the other one easy to miss.
>
> **Correction:** a test tells you the code does what you meant. It does not tell you whether what
> you meant is right — and the assertion's *interpretation of its input* is precisely what is in
> dispute when you are holding a bug report.

**(A) The rules that follow.**

1. **On a bug report, grep the suite for the reported input BEFORE reading any code.** If a green
   test contains it, **the test is the bug**, and you have found the wrong premise in one command.
   This inverts the normal assumption, which is why nobody does it: the suite is the one place
   nobody looks, because it is "covered".
   ```bash
   rg -n --glob '*_selftest.rs' --glob '*/tests/*' -e '<the value that reproduced it>'
   rg -n --glob '*_selftest.rs' -e 'fn .*(_not_|_never_|_is_not|_refus|_invalid|_ignor)'
   ```
   The fuller procedure, with the save case worked end to end, is
   [policy-and-diagnostics.md](../skills/save-integrity/reference/policy-and-diagnostics.md) §7.
2. **A test asserting a REFUSAL needs a stated reason per refused input**, in the test, citing the
   evidence — the disassembly site of the guest's own gate, or the measurement. An unexplained entry
   in a negative list is unreviewable, and a wrong one is invisible.
3. **Derive rejection lists from the game's gate, never from your implementation.** Copying your own
   constant into the test is how a premise gets pinned; reading the gate is how it gets checked.
4. **Prefer asserting the SHAPE of the gate to enumerating values.** "This gate is an upper bound
   only, with no lower bound" is a claim about the guest that a reviewer can verify and that stays
   true as the data grows. A list of accepted and rejected numbers is a claim about nothing.
5. **Pair every refusal test with a positive test that asserts an effect is actually EMITTED**, not
   merely permitted. A feature can pass every "does not do the wrong thing" test while doing nothing
   at all.
6. **Negative assertions deserve more scrutiny than positive ones.** A positive assertion is checked
   against reality every time a player uses the feature; a negative one is never exercised by
   anybody, so a wrong one can live indefinitely.
7. **The test's name and its case comment are part of the claim.** A reviewer skimming a file reads
   names, not tuples. A case named after its *input* is unreviewable; a case named after the
   *property* is self-checking. Renaming is the cheapest defect-prevention step available in a test
   file and the only one that works on a skimming reader — the second defect above was fixed by
   renaming the case to state the property in full, and that is what made the failure visible.
8. **When you find a wrong assertion, re-file it as a named regression test in the same commit as
   the fix** ([§4](#4-supporting-rules)). Do not edit the expectation and keep the old name; the
   name is the artefact that stops the loop.
9. **When one predicate is spelled in three places — host, offline engine, server — they must denote
   the same set, and the one written last is the one that narrowed it.** **(B)** In the first defect
   above, the engine and the server both tested a single upper bound from the start and were always
   right; the host spelling, added two sessions later, introduced a lower bound and was the only one
   that lost the category. The oldest implementation is usually the one derived from the guest; a new
   one is usually derived from a comment. When they disagree, suspect the newest.

### 7.2 The suite that only agrees with itself

The second failure needs no wrong belief at all. If **every** test computes its expectation *from the
constants under test*, the suite asserts a tautology: it proves the code is self-consistent, which it
is, whatever the constants happen to say.

**(B)** A reviewer on the reference port demonstrated this by mutation. Mis-typing one measured grid
origin — `22` to `30` — left **all 27 existing tests green** while mapping taps eight cells off,
because every one of them derived its expected cell from that same constant. Two literal-pinning
tests were added and the same mutation was re-run: two tests went red.

**(A) The remedy, in two parts:**

- **One dedicated test per measured table restates the RAW measurements as literals** — the probe's
  numbers, written out, not recomputed. Geometry origins and pitches, palette values, protocol
  offsets, timing constants. Name it after what it pins (`the_constants_are_the_probe_numbers`) and
  put the measurement's date and source in its comment.
- **Validate the pin by mutation.** Change one constant, run the suite, confirm something goes red,
  revert. A pin you have never mutated is a pin you are guessing about. That is step 6 of
  [§5](#5-adding-a-new-selftest), for this reason.

**(A) And for anything that classifies pixels, synthetic frames are not enough.** Synthetic frames
prove the *arithmetic*; only real frames prove the *thresholds* — every minimum ink count, band
height, run length and fill ratio in a detector is a claim about real art. Build a throwaway runner
that `include!`s the real detector module and eats raw framebuffer dumps, then feed it probe frames
and device captures. **(B)** Twenty probe frames plus device captures were run through a detector
suite that was entirely green on synthetic input; the per-screen target counts matched the expected
sets exactly, including a second logical size producing an identical result — which is what
"resolution independent" has to mean before you write it down.

---

## 8. What belongs in a selftest, and what does not

Four tiers, in ascending order of cost. Push every question down to the cheapest tier that can
answer it.

| tier | cost | answers |
|---|---|---|
| **standalone selftest** | ~1 s, no device, no emulator | Encodings, parsers, formats, geometry, resolvers, state machines, patch halfwords, coverage predicates, bounds and refusals. |
| **workspace `cargo test`** (one host, plus CI — [§1.1](#11-one-host-usually-can-and-that-tier-is-worth-keeping)) | seconds, but only where the link graph resolves | End-to-end paths through real IO — a write policy exercised against a real on-disk record store — and `#[ignore]`d harnesses that emit images for a human. |
| **headless probe** | ~1 min, no device | Does it boot; is the frame non-black; does the guest routine behave as claimed; does my host reimplementation match the guest's own output byte-for-byte; are the heap gauges flat. |
| **device** | minutes, plus a physical phone | Anything involving real input latency, the platform's JNI behaviour, the real network, orientation and lifecycle, perceptual judgements, and every claim about *error* behaviour under conditions a LAN cannot reproduce. |

The tier boundaries are also a debugging strategy: a bug that reproduces in tier 1 is a bug you can
fix in a loop measured in seconds. Most of the effort in structuring the code this way is repaid the
first time a device-only bug turns out to be reproducible in a selftest after a small refactor. What
only the last tier can settle: [device-verification.md](device-verification.md).

The vendored emulator core is a separate case: it *is* an ordinary Rust workspace and its own
`cargo test` works on every host. Run it. Equivalence tests inside the core — "the optimised path
produces byte-identical output to the reference path" — are the right place for graphics and
allocator changes, and they are the reason those changes can be shipped at all.

---

## 9. Running them all: the runner, the count, and CI

Ship one script that compiles and runs every selftest, prints a per-file pass count, and exits
non-zero on any failure. Give the shell and PowerShell versions identical stage names so the CI
transcript and a developer's local run are comparable line for line.

**★ (A) The runner GLOBS the test files. It never reads a list.** A hand-maintained list *is* the
test registry, and it drifts like any other undated prose.

> **(B) What happens without one.** The reference port has no runner script at all: the file list and
> the per-file pass counts live in the project's instructions document. One whole selftest file —
> **20 assertions** — was missing from that list for several sessions, so nobody ran it and there was
> no red anywhere to say so. The documented total was restated four times, each revision leaving the
> superseded number annotated in place (`451 → 467 → 489 → 500`), which is archaeology rather than an
> invariant. A file that is not in the list is a file nobody runs, and its absence produces no signal
> of any kind.

Three things the script must get right, each of them learned by having it wrong:

1. **Read each file's required edition** rather than hardcoding one
   ([§2.1](#21-the-edition-is-the-shipping-crates-not-the-examples)). Derive it from the crate; fall
   back to a per-file marker only where a file genuinely needs a different one, and print which was
   used.
2. **Run from the repository root.** At least one selftest reads a vendored source path relatively
   ([§6.1](#61-the-third-form-pin-the-mirror-to-the-source-text)), and a runner that `cd`s into a
   test directory breaks it with an error about a missing file rather than about a working directory.
3. **Do not redirect the compiler's stderr**
   ([§2.2](#22-never-redirect-the-compilers-stderr)). Check the exit code.

**Print the total, and treat it as an invariant.** A count in a document is a number somebody typed;
a count printed by the runner is a fact, and a drop in it is a file that stopped being compiled. If
you want the total in the docs as well, have the runner emit the exact line the docs contain, so a
stale count is a diff rather than an argument. Mirrors contribute nothing to the total
([§6.3](#63-convert-any-instance-you-find-is-aspirational--so-record-what-is-not-covered)).

**(A) The rule behind all of this: every gate must be a command that exits non-zero.** A gate that
lives only in a document is a gate that gets *reported as* run. **(B)** Two consecutive session logs
in the reference port recorded a clean lint pass over the workspace; the next session discovered the
linter had never been installed on that machine at all — the earlier record had been carried across
from a different environment.

The CI job is a plain Linux job. It needs `rustc` and nothing else for the selftests, and
`cargo-ndk` plus the Android target for the build. It must **not** try to run `cargo test --all`
over a workspace containing the application crate — that is precisely the command
[§1](#1-why-cargo-test-dies--and-the-host-where-it-does-not) exists to explain, and an inherited
upstream workflow will attempt it.

A useful CI shape:

1. `cargo fmt --check` and `cargo clippy` over the crates that can be host-built.
2. Every `*_selftest.rs`, discovered by glob, compiled and run.
3. The vendored core's own `cargo test`; then, on Linux, the application crate's own workspace tests
   — or at minimum a host-triple **build** of it, which is what catches a lost
   `#[cfg(target_os = "android")]` gate
   ([§1.1](#11-one-host-usually-can-and-that-tier-is-worth-keeping)).
4. `cargo-ndk` build of the application crate for the Android target (compiles and links; does not
   run).
5. Optionally, a headless probe boot with an assertion that a dumped frame has a non-zero non-black
   pixel count — a device-free regression gate against the black-screen class of failure. This one
   requires a game archive, so it only runs where a dump is available; keep it as an opt-in job.

---

## 10. Symptom table

| symptom | what it actually is | § |
|---|---|---|
| `cargo test -p <app-crate>` aborts before any test runs | Expected on that host — the platform link graph. Use the harness; the same command may work on another host. | [1](#1-why-cargo-test-dies--and-the-host-where-it-does-not), [1.1](#11-one-host-usually-can-and-that-tier-is-worth-keeping) |
| The host build of the workspace stopped compiling and nobody knows when | A module pulling a target-only crate lost its `#[cfg(target_os = "android")]` gate. | [1.1](#11-one-host-usually-can-and-that-tier-is-worth-keeping) |
| A selftest fails with "only allowed in Rust \<newer\>" pointing **into shipping source** | The `--edition` flag disagrees with the crate. | [2.1](#21-the-edition-is-the-shipping-crates-not-the-examples) |
| A passing selftest reports as a build failure inside a PowerShell script | `2>&1` turned compiler warnings into a terminating error. | [2.2](#22-never-redirect-the-compilers-stderr) |
| A range delete removed code you did not mean to touch | An `include!`d module has no internal boundaries; compiling the selftest is the gate. | [3](#3-the-two-hard-rules) |
| The policy was correct and the data was destroyed anyway | The load-bearing condition lives in a caller, where no test can reach it. | [4](#4-supporting-rules) |
| A test is green and the shipping constant is something else entirely | The mirror form. | [6](#6-the-forbidden-mirror-form) |
| A mirror is green for code that no longer exists | The function was deleted and the mirror was not. | [6](#6-the-forbidden-mirror-form) |
| A definition genuinely cannot be `include!`d | Pin the mirror to the source text; fall back to a labelled, uncounted mirror. | [6.1](#61-the-third-form-pin-the-mirror-to-the-source-text), [6.2](#62-when-even-that-is-impossible-two-honest-options) |
| The suite is green and a player reports a legal input being refused | A negative list pinned a wrong premise and is defending the bug. | [7.1](#71-a-suite-can-pin-a-wrong-premise-and-hold-a-bug-green) |
| Everything is still green after you mutate a measured constant | Every test derives its expectation from that constant. | [7.2](#72-the-suite-that-only-agrees-with-itself) |
| A pixel detector is green on synthetic frames and misses on the device | The thresholds were never run against real art. | [7.2](#72-the-suite-that-only-agrees-with-itself) |
| A whole test file has not run for weeks and nothing was red | The registry is a hand-maintained list. | [9](#9-running-them-all-the-runner-the-count-and-ci) |
| A documented test total disagrees with reality | The count is prose, not output. | [9](#9-running-them-all-the-runner-the-count-and-ci) |

---

## 11. Checklist

Before you call a module tested:

- [ ] The selftest `include!`s the real source, and the header says why.
- [ ] The included module has **no imports**, and says so at the top.
- [ ] The compile edition comes from the crate, not from an example in a document.
- [ ] No command in the loop redirects the compiler's stderr; exit codes are checked explicitly.
- [ ] Every refusal assertion carries a **reason**, and the rejection list is derived from the
      game's own gate rather than from your implementation.
- [ ] At least one test asserts an effect is **emitted**, not only that wrong things are refused.
- [ ] Every test name states a property or a report — none is named after its input.
- [ ] One test restates the **raw measurements** as literals, and you have mutated a constant and
      watched something go red.
- [ ] Anything that can destroy data has **property tests** over a seeded corpus, with the seed and
      size in the assertion messages.
- [ ] Any decision that can destroy data is **one pure function in one file**; callers only gather
      facts and ask it.
- [ ] Pixel and threshold detectors have been run against **real dumped frames**, not only synthetic
      ones.
- [ ] No mirror restates a bound or a build-keyed table; any mirror that survives is pinned to the
      source text, or is labelled uncovered and excluded from the tally.
- [ ] The runner **globs**; the file is picked up without anybody editing a list.
- [ ] The total is printed by a command, and the CI job builds the workspace for the host triple.
