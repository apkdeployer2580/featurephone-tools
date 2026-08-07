# Prerequisites — host toolchain and what you must supply

Everything needed before chapter 3, plus the per-OS traps that cost the most time. Paths use
forward slashes on every OS, including Windows.

Provenance tags: **(A)** generic · **(B)** worked example from the reference port · **(C)** do not
copy.

## Contents

- [1. Toolchain table](#1-toolchain-table)
- [2. Version-pinning discipline](#2-version-pinning-discipline)
- [3. Tool discovery — candidate lists, never absolute paths](#3-tool-discovery--candidate-lists-never-absolute-paths)
- [4. Per-OS notes](#4-per-os-notes)
- [5. Python tooling](#5-python-tooling)
- [6. Three name agreements that fail at runtime, not build time](#6-three-name-agreements-that-fail-at-runtime-not-build-time)
- [7. Dead config to delete](#7-dead-config-to-delete)
- [8. Not shipped — you must supply](#8-not-shipped--you-must-supply)
- [9. Verify your toolchain](#9-verify-your-toolchain)
- [10. First-run sequence](#10-first-run-sequence)
- [11. Testing, and why `cargo test` is not the test command](#11-testing-and-why-cargo-test-is-not-the-test-command)

---

## 1. Toolchain table

| Tool | Version | Pinned where | Notes |
|---|---|---|---|
| Android SDK | any recent | `$ANDROID_HOME` | build-tools are discovered by highest version directory, not pinned |
| Android NDK | **one exact revision** | Gradle `ndkVersion`, and the build script hard-fails if that directory is absent | keep the revision string in **one** place and reference it; **(B)** a real project had it in three hand-synced places |
| compileSdk / targetSdk | 35 at time of writing | `app/build.gradle.kts` | the platform must be installed |
| minSdk | 26 | `app/build.gradle.kts` | AAudio needs 26; if you use `rodio`/`cpal` this is a floor, not a preference |
| JDK | 17+ (17 source/target) | `app/build.gradle.kts` | Android Studio's bundled JBR is a real JDK 17+ and is the documented fallback — no second download |
| Gradle | wrapper 8.9 | `gradle/wrapper/gradle-wrapper.properties` | always `./gradlew`, never a system gradle |
| Android Gradle Plugin | 8.7.2 | root `build.gradle.kts` | |
| Rust | stable, edition 2024 | **add a `rust-toolchain.toml`** | **(B)** the reference project pinned this with nothing at all |
| Rust target | `aarch64-linux-android` | — | `rustup target add aarch64-linux-android` |
| cargo-ndk | pin a version | — | `cargo install cargo-ndk`; **(B)** unpinned in the reference project |
| adb | any | `$ANDROID_HOME/platform-tools` | only needed for install and device driving |
| Python | 3.10+ | — | for this plugin's tools and any asset/icon work |
| `capstone` (pip) | any | — | required by `dis.py` and `xref.py`; **checked for and reported, never auto-installed** |
| `Pillow` (pip) | any | — | required by `gen_glyphs.py` and `frames_to_png.py`; same rule |
| `openssl` CLI | any | — | only for the server track's TLS test and cert generation |

Single-ABI `arm64-v8a` is the sane default: it halves build time and APK size, and every phone
since roughly 2017 is arm64. Add more ABIs only when someone asks.

## 2. Version-pinning discipline

- **GameActivity, not NativeActivity.** `cargo-apk` is deprecated and NativeActivity-only;
  GameActivity needs a real Gradle project, which is why the pipeline is cargo-ndk + Gradle.
- **Do not depend on `android-activity` directly.** `winit` pulls it transitively at the version
  it was built against; a direct dependency gives you two copies and a hard link error.
- **The games-activity artifact drags older split Kotlin stdlib artifacts.** Combined with a
  newer appcompat, that is a duplicate-class error at build time. Force one stdlib version with a
  resolution strategy. **(A)** This trap is generic to that dependency, not to any title.
- **Strip the native library in the build script, both profiles.** An unstripped debug `.so`
  produces a ~250 MB APK. Then tell the Android plugin not to strip it again.
- **Release profile:** LTO on, one codegen unit — the whole app is one hot interpreter loop, and
  splitting codegen units costs inlining exactly there. **Do not set `panic = "abort"`**: it
  breaks `cargo test` and the app unwinds across JNI.
- **Compile the log level out, do not filter it at runtime.** Set release max level on *both*
  `log` and `tracing`. **(B)** Measured on a device: ~124 log writes per second from inside a tick
  loop with a 24 ms frame budget. Note that `tracing`'s `#[instrument]` spans default to INFO, so
  "set the level to Info" does not fix it.

## 3. Tool discovery — candidate lists, never absolute paths

**(A) This is the fix for the single most common open-source portability failure.** Every tool
path resolves through an ordered candidate list — environment variable first, then standard
per-user locations — and errors with an actionable message if none exist:

```sh
# shape, not a copy-paste
SDK="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
[ -d "$SDK" ] || { echo "Android SDK not found. Set ANDROID_HOME."; exit 1; }
```

Do the same for `JAVA_HOME` (env → a system JDK → Android Studio's bundled JBR) and for `adb`.
Discover build-tools by highest version directory rather than pinning a version.

**(C) Never** commit an absolute path into one developer's profile as the only candidate. **(B)**
A real build script contained three of them and a second machine could not run it at all.

## 4. Per-OS notes

### Linux, macOS, and Windows-MSVC hosts

Nothing special. The whole host-tooling shim stage below does not exist for you; the environment
setup collapses to putting cargo, the JDK and the SDK on `PATH`.

### Windows-GNU hosts only — the `dlltool` shim

Cross-compiling to Android *from* an `x86_64-pc-windows-gnu` host still builds **host**
build-dependencies that declare imports as `kind = "raw-dylib"`. rustc materialises those by
shelling out to `dlltool`, and it **picks the GNU vs LLVM flag set from the tool's file name** —
so an LLVM `dlltool` renamed to `dlltool.exe` still receives GNU-only flags. One of them
(`--temp-prefix`) makes LLVM's version print usage and exit 1, no import library is written, and
the build dies with a **misleading** error:

```
failed to add native library ...dll_imports.lib: file not found
```

Fix: a tiny shim that filters argv down to the flags LLVM's tool accepts and forwards to the real
one. Two ways to obtain the real tool: rustup's `llvm-dlltool` from the GNU toolchain, or a copy
of the NDK's `llvm-ar` — `llvm-ar` / `lib` / `ranlib` / `dlltool` are one multicall binary that
picks its personality from `argv[0]`, so a renamed copy **is** `llvm-dlltool`. If you add the NDK
bin directory to `PATH` for that reason, append it **last**, so NDK cross-tools cannot shadow
host tools.

### PowerShell traps (any Windows host)

- **`$ErrorActionPreference = 'Stop'` plus `2>&1` on a native command aborts successful builds.**
  cargo-ndk writes progress to stderr; redirecting a native command's stderr into the PowerShell
  stream under `Stop` turns it into a terminating error. **Never redirect; check `$LASTEXITCODE`.**
- **`Set-Content -Encoding utf8` writes a BOM** and re-encodes through the console code page.
  Java's `Properties.load()` decodes as ISO-8859-1. So: keep any `.properties` file **ASCII-only**
  and write it with `[System.IO.File]::WriteAllLines(path, lines, UTF8Encoding($false))`.
- Console output: non-ASCII characters (`…`, box drawing) render as `??` on PowerShell 5.1. Use
  ASCII in build-script output.
- `./gradlew.bat` on Windows vs `./gradlew` elsewhere — trivial, but it is why you want two build
  scripts with **identical stage names and identical gates** rather than one.

## 5. Python tooling

All plugin tools are Python 3.10+, stdlib except `capstone` (disassembly) and `Pillow` (glyph and
frame conversion). Both are **checked for and reported with the install command, never
auto-installed** — a tool that silently pip-installs into whatever interpreter it happens to be
running under is a support burden.

Every static tool reads one config file so nothing per-title is hardcoded:

```toml
# target.toml — everything that changes per title lives here and nowhere else
binary       = "work/client.bin"   # the extracted native guest image
image_base   = 0x100000            # from the loader crate's load(data, BASE, ...) call
bss_size     = 64000               # parsed from the native member's filename
sl_fo        = 0xb2c40             # RWPI static base, as a FILE OFFSET
reloc_delta  = 0xeec               # add to a stored word to recover ...
reloc_unit   = "file_offset"       # ... either "file_offset" or "guest_address"
encoding     = "euc-kr"            # text encoding of the game's strings
isa          = "thumb"             # default decode mode
```

The values above are **(B)** — a worked example from one title, each found by the method in
chapter 7. Copying them into another project produces tools that lie consistently.

## 6. Three name agreements that fail at runtime, not build time

**(A) These produce a perfectly clean build and then break on a device.**

1. **Native library name.** `[lib] name` in `Cargo.toml` ↔ `System.loadLibrary("…")` in the
   activity ↔ `android.app.lib_name` in the manifest. All three must be the same string, and
   GameActivity effectively requires it to be `main`. Disagreement ⇒ `UnsatisfiedLinkError` at
   launch.
2. **The Java namespace must not be renamed** even when the application id is. Rust resolves the
   activity class **by name over JNI**, so moving the package makes JNI-backed features
   (orientation toggle, platform dialogs) stop working silently, at call time, with a clean build.
3. **The launch component.** Once the application id differs from the Java package,
   `am start -n <applicationId>/.MainActivity` no longer resolves. Derive the component from the
   built APK's badging output instead of composing it by hand.

And one that is permanent rather than merely fragile: **the application id cannot change after a
store upload**, and it is also the on-device data path — changing it makes the app a different
app with no access to previous saves.

## 7. Dead config to delete

**(B, do not replicate)** A `.cargo/config.toml` containing absolute NDK linker paths is worse
than useless:

- It is **not what the build uses.** cargo-ndk exports `CARGO_TARGET_*_LINKER` / `CC_*` / `AR_*`,
  and cargo's environment variables take precedence over config files.
- The API level baked into those paths silently disagreed with both `--platform` and `minSdk`, so
  anyone invoking a bare `cargo build --target aarch64-linux-android` linked against the wrong
  API level.

Delete the file and rely on cargo-ndk, or generate it.

## 8. Not shipped — you must supply

| item | where it goes | notes |
|---|---|---|
| the game archive | `app/src/main/assets/<archive>.zip` | gitignored **by name and by path**; a fresh clone must not be able to build a runnable APK |
| a release keystore + 3 secrets | **outside the repository** | created once; losing it means every user must uninstall, which deletes their saves |
| a server host + certificate pin | build-time env or an on-device file | only for online titles |
| a TTF with the glyphs you need | anywhere; referenced by `gen_glyphs.py --font` | only if the host draws CJK text |
| a physical device | — | the probe removes most of the need for one, not all of it. Budget at least one refutation per feature. |

**(A) The build must fail loudly when the archive is absent.** The default behaviour of a Gradle
assets directory is to package whatever is there — including nothing — so the build succeeds and
produces an APK with no game. That fail-fast check is the single most important hardening for a
public template.

## 9. Verify your toolchain

```sh
rustc --version && cargo --version
rustup target list --installed | grep aarch64-linux-android
cargo ndk --version
java -version                      # 17+
ls "$ANDROID_HOME/ndk"             # the pinned revision must be present
adb version
python --version                   # 3.10+
python -c "import capstone, PIL; print('re tooling ok')"
```

A fast Rust-only compile check that does not involve Gradle:

```sh
cargo ndk -t arm64-v8a --platform 26 check
```

Hold the bar at **zero warnings**; on a project this size, warnings stop being read within a week.

## 10. First-run sequence

```sh
# once
./build_android.sh --new-keystore     # after setting the keystore/alias/password variables
# every build
./build_android.sh                    # debug
./build_android.sh --release          # signed, versionCode bumped, config verified in the .so
./build_android.sh --release --install
# the store bundle is a separate invocation, deliberately
./gradlew :app:bundleRelease
```

Keystore creation, once, with the two settings that matter: **PKCS12, not JKS** (JKS is legacy and
tools nag on every use), and a validity far in the future — stores reject keys that expire soon,
and an expired key is a key you can no longer ship updates with.

## 11. Testing, and why `cargo test` is not the test command

`cargo test` on the Android app crate **dies at startup** on a desktop host, because the crate
links the windowing and Android platform graph. **(A) True of any Android/iOS/WASM-target crate.**

The convention that replaces it:

> Pure logic lives in **dependency-free** files, and standalone test files at the repo root
> `include!` **the real source** — never a copy.

```sh
rustc --edition 2021 --test landscape_selftest.rs -o t && ./t
rustc --edition 2021 --test endpoint_selftest.rs  -o t && ./t
```

Rules that make it work: the included files must carry **no imports** and no inner doc comments
(an inner doc cannot appear inside an `include!` — put the module doc on the `mod` item instead);
generated code participates too, so a generated glyph table can be asserted for coverage; and the
same trick gets one source into N link environments, which is how the headless probe verifies the
*shipping* renderer.

**(B) Forbid the hand-kept mirror form.** A project that mirrored constants into its test file
instead of including the source drifted: the mirror said one value while the shipping constant
said another, and the test passed.

Full convention: `${CLAUDE_PLUGIN_ROOT}/shared/selftest-convention.md`.
