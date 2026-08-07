# Host portability: making the build run on a machine that is not yours

Provenance: **(A)** generic · **(B)** worked example from one reference port · **(C)** never copy.

> **Rule:** `build_android.ps1` and `build_android.sh` implement the **same stage names** and the
> **same gates**. Only stage 2 (`host-shim`) and the invocation of the Gradle wrapper differ by
> host. Anything else that differs is a bug in one of the two scripts. **(A)**

## Contents

1. [What is actually host-specific](#1-what-is-actually-host-specific)
2. [The shared stage contract](#2-the-shared-stage-contract)
3. [windows-gnu only: the dlltool shim](#3-windows-gnu-only-the-dlltool-shim)
4. [PowerShell trap: native stderr under ErrorActionPreference Stop](#4-powershell-trap-native-stderr-under-erroractionpreference-stop)
5. [PowerShell trap: text encoding](#5-powershell-trap-text-encoding)
6. [PowerShell trap: console output](#6-powershell-trap-console-output)
7. [Wrapper, paths and separators](#7-wrapper-paths-and-separators)
8. [Per-OS tool locations](#8-per-os-tool-locations)
9. [Delete .cargo/config.toml](#9-delete-cargoconfigtoml)
10. [A parity test for the two scripts](#10-a-parity-test-for-the-two-scripts)

---

## 1. What is actually host-specific

The pipeline's *structure* — discover → shim → bump → resolve → cargo-ndk → strip → verify
artifact → gradle → verify signature → install — is 100% portable. Exactly four things are not,
and three of them are PowerShell-only:

| # | host-specific thing | applies to | Linux/macOS/windows-msvc equivalent |
|---|---|---|---|
| a | the `dlltool` shim | **`x86_64-pc-windows-gnu` hosts only** | nothing — the whole stage collapses to a PATH export |
| b | native stderr under `$ErrorActionPreference = 'Stop'` | PowerShell | n/a; use `set -euo pipefail` and check exit codes |
| c | `Set-Content -Encoding utf8` writes a BOM | PowerShell 5.1 | n/a; `printf`/`sed` are already byte-exact |
| d | `gradlew.bat` vs `./gradlew` | Windows | `./gradlew` (committed, executable bit set) |

Cross-compiling to Android is otherwise identical on all three OSes. **Do not let a Windows-only
script be the only way to build a public project.**

## 2. The shared stage contract

Both scripts must print the same stage banners, in the same order, so that a log from either host
is readable by anyone:

```
[1/10] discover-tools     SDK=… NDK=… JDK=… build-tools=…
[2/10] host-shim          (skipped: not a windows-gnu host)
[3/10] bump-version       versionCode 14 -> 15
[4/10] resolve-config     host=<set|LOCAL> port=… pin=<set|none>   source=env|gradle.properties
[5/10] cargo-ndk          arm64-v8a --platform 26 [--release]
[6/10] strip              libmain.so  128.4 MB -> 9.1 MB
[7/10] verify-artifact    OK: host string present; pin present
[8/10] gradle             :app:assembleRelease
[9/10] verify-signature   SHA-256 digest: <fingerprint>   (signed)
[10/10] install           <component> launched on $ANDROID_SERIAL
```

Every stage either succeeds or **exits non-zero**; no stage warns and continues except where this
document says so explicitly (unsigned output, missing transport prefix).

Gates that must exist identically in both: stage 4's "release with no destination" hard error,
stage 7's artifact scan, stage 9's signature check. If one script has a gate the other lacks, the
project has two different definitions of "a good build".

## 3. windows-gnu only: the dlltool shim

**Skip this entire section unless the host triple is `x86_64-pc-windows-gnu`.** Linux, macOS and
`x86_64-pc-windows-msvc` never hit it.

### The mechanism

Cross-compiling *to* Android from a windows-gnu host still builds **host build-dependencies** —
build scripts, proc macros and their transitive crates. Some of those declare Windows imports as
`kind = "raw-dylib"`. rustc materialises a raw-dylib import library by shelling out to
`dlltool`, and it **chooses the GNU flag set or the LLVM flag set from the tool's file name**.

So the standard workaround of copying `llvm-dlltool.exe` to `dlltool.exe` does not work: the
renamed tool still receives the GNU-only set,

```
dlltool -d <def> -D <dll> -l <lib> -m i386:x86-64 -f --64 \
        --no-leading-underscore --temp-prefix <name>
```

`--temp-prefix` is fatal — llvm-dlltool prints its usage and exits 1, no import library is
written, and the build dies with a **completely misleading** error:

```
failed to add native library …dll_imports.lib: file not found
```

### The fix

A ~60-line Rust shim compiled at build time. It filters argv down to the flags llvm-dlltool
accepts — `-D -d -f -k -l -m -S` — and forwards to the real tool named by an environment
variable:

```
tools/dlltool_shim.rs   ->  compiled by the build script into a dlltool.exe placed FIRST on PATH
                            forwards to $WIE_REAL_DLLTOOL
```

The real tool is either rustup's `llvm-dlltool.exe` from the gnu toolchain, or — the neat
fallback — **a copy of the NDK's `llvm-ar.exe`**. llvm-ar, llvm-lib, llvm-ranlib and llvm-dlltool
are **one multicall binary that picks its personality from `argv[0]`**, so a copy named
`llvm-dlltool.exe` *is* llvm-dlltool.

### PATH ordering, and why it matters

Append the NDK's `bin` directory **last**:

```
PATH = <shim dir>;<cargo bin>;<JAVA_HOME>/bin;…;<NDK toolchain bin>
```

It is on PATH only so that multicall binary can find its own DLLs. Putting it **first** lets the
NDK's cross-compilation tools shadow host tools with the same names, which produces a new and
much less obvious class of failure.

### On every other host

The whole block collapses to:

```bash
export PATH="$HOME/.cargo/bin:$JAVA_HOME/bin:$PATH"
```

Print `[2/10] host-shim (skipped: not a windows-gnu host)` so the stage numbering stays aligned
between logs.

## 4. PowerShell trap: native stderr under ErrorActionPreference Stop

`cargo-ndk` writes progress (`Building arm64-v8a`) to **stderr**. Under
`$ErrorActionPreference = 'Stop'`, redirecting a native command's stderr into the PowerShell
stream with `2>&1` turns each stderr line into an `ErrorRecord` — which is a **terminating
error** — and aborts a build that was succeeding.

```powershell
# WRONG - aborts a successful build
& cargo ndk -t arm64-v8a --platform 26 build 2>&1 | Tee-Object build.log

# RIGHT - never redirect; check the exit code explicitly
& cargo ndk -t arm64-v8a --platform 26 -o $JniLibs build @relArgs
if ($LASTEXITCODE -ne 0) { throw "cargo ndk failed ($LASTEXITCODE)" }
```

This applies to every native tool in the pipeline: cargo, cargo-ndk, llvm-strip, gradlew,
apksigner, aapt2, adb, keytool. Same rule each time: **do not redirect, check `$LASTEXITCODE`.**

Bash equivalent:

```bash
set -euo pipefail
cargo ndk -t arm64-v8a --platform 26 -o "$JNILIBS" build ${REL:+--release}
```

⚠ With `pipefail`, remember that `cmd | grep -q` returning 1 will also abort — wrap the
grep-based gates in an explicit `if`.

## 5. PowerShell trap: text encoding

`Set-Content -Encoding utf8` on PowerShell 5.1 writes a **BOM** and re-encodes through the
console codepage. Both halves cause damage to a Java properties file:

* the BOM lands in front of the first key, so `Properties.load()` cannot read it;
* non-ASCII comments are mangled by the codepage round-trip.

```powershell
[System.IO.File]::WriteAllLines($path, $lines, (New-Object System.Text.UTF8Encoding($false)))
```

And keep `version.properties` **ASCII-only** regardless, because Java's `Properties.load()`
decodes ISO-8859-1. The two constraints together mean: no BOM, no non-ASCII, ever, in that file.

## 6. PowerShell trap: console output

PowerShell 5.1 renders many non-ASCII characters as `??` in a default console. Use ASCII `...`
rather than `…`, ASCII arrows rather than `→`, and no box-drawing characters in stage banners.
The Bash script can use whatever the terminal supports, but **keeping both scripts ASCII is the
cheaper rule** — it also makes the two files diffable against each other (§10).

## 7. Wrapper, paths and separators

* Windows: `gradlew.bat`. Unix: `./gradlew`. The Unix wrapper is committed and needs its
  executable bit; a `core.autocrlf` mishap that gives it CRLF line endings produces
  `bad interpreter: /bin/sh^M`.
* **Write every path in documentation and in configuration with forward slashes.** Windows
  accepts them everywhere that matters (PowerShell, cargo, Gradle, Java), and it removes the
  escaping problem entirely.
* Never put a drive letter or a user profile directory in a committed file. Candidate lists and
  environment variables only — [toolchain.md](toolchain.md) §11.

## 8. Per-OS tool locations

| tool | Linux | macOS | Windows |
|---|---|---|---|
| SDK default | `$HOME/Android/Sdk` | `$HOME/Library/Android/sdk` | `%LOCALAPPDATA%/Android/Sdk` |
| NDK | `$SDK/ndk/<rev>` | same | same |
| `llvm-strip` | `$SDK/ndk/<rev>/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-strip` | `…/darwin-x86_64/bin/llvm-strip` | `…/windows-x86_64/bin/llvm-strip.exe` |
| `apksigner` | `$SDK/build-tools/<highest>/apksigner` | same | `apksigner.bat` |
| `aapt2` | `$SDK/build-tools/<highest>/aapt2` | same | `aapt2.exe` |
| Studio JBR | `<studio>/jbr` | `<studio>.app/Contents/jbr/Contents/Home` | `<studio>/jbr` |

Resolve the prebuilt-triple directory from the OS rather than hardcoding it; that one substring
is the only difference between the three `llvm-strip` paths.

## 9. Delete .cargo/config.toml

A `[target.aarch64-linux-android] linker = "…"` entry with an absolute path is:

* **not portable** — it names one user's profile directory;
* **not used** — cargo-ndk exports `CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER`, `CC_*` and
  `AR_*`, and **cargo environment variables take precedence over config files**;
* **actively harmful** — in the reference its API level (24) disagreed with both `--platform 26`
  and `minSdk 26`, so anyone running a bare `cargo build --target aarch64-linux-android` without
  cargo-ndk silently linked against a different API level than the app declares. **(B)**

Delete it and go through cargo-ndk for everything. If you need a bare-cargo path (for
`cargo check` in an editor), give it the same `--platform` via cargo-ndk's `check` subcommand
rather than a checked-in linker path.

## 10. A parity test for the two scripts

Cheap and worth having in CI:

```bash
diff <(grep -oE '^\[[0-9]+/[0-9]+\] [a-z-]+' build_android.sh  | sort -u) \
     <(grep -oE '^\[[0-9]+/[0-9]+\] [a-z-]+' build_android.ps1 | sort -u)
```

Extend it to the flag names (`--release`/`-Release`, `--install`/`-Install`, …) and to the gate
messages. Two scripts that drift are worse than one script that only runs on one OS, because a
contributor on the other OS will believe the gates ran.

The CI job that actually exercises the Bash script is described in
[testing-and-ci.md](testing-and-ci.md) — a Linux runner builds with cargo-ndk without ever
touching §3.
