# Build and release troubleshooting

Provenance: **(A)** generic · **(B)** worked example from one reference port · **(C)** never copy.

Symptom → cause → fix. Every entry here was observed on a real port; the ones that cost the most
time are the ones where **the build succeeds**.

## Contents

1. [Symptom table](#1-symptom-table)
2. [The clean-build class: it succeeded and it is wrong](#2-the-clean-build-class-it-succeeded-and-it-is-wrong)
3. [Compile and link failures](#3-compile-and-link-failures)
4. [Install and launch failures](#4-install-and-launch-failures)
5. [Script-level failures](#5-script-level-failures)
6. [Diagnostic one-liners](#6-diagnostic-one-liners)

---

## 1. Symptom table

| symptom | cause | fix |
|---|---|---|
| `failed to add native library …dll_imports.lib: file not found` | windows-gnu host; rustc handed llvm-dlltool the **GNU** flag set (`--temp-prefix`) because it picks the flag set from the tool's filename | build and use the argv-filtering shim — [host-portability.md](host-portability.md) §3 |
| Build succeeds; the app talks to `127.0.0.1`; online features dead for everyone but you | a compile-time `option_env!` resolved with the variable unset, **or** a stale cargo fingerprint | the release hard-error plus the post-build ASCII scan of the `.so`; `cargo clean -p <host crate>` — [config-baking.md](config-baking.md) |
| APK will not install on another phone; the file is `app-release-unsigned.apk` | no complete signing config resolved | set the four keystore values in env or `$HOME/.gradle/gradle.properties`. **The filename is the diagnostic** — [build-script.md](build-script.md) §8 |
| `INSTALL_FAILED_VERSION_DOWNGRADE` / "App not installed" on update | `versionCode` not greater than the installed one, **or** the certificate changed | bump (automatic on `--release`); never re-key — a new key means uninstall + reinstall and every save is gone |
| `INSTALL_FAILED_UPDATE_INCOMPATIBLE` | signed with a different key than the installed build (very often: a debug build over a release install) | uninstall first, or sign with the same keystore |
| `UnsatisfiedLinkError` at launch | Cargo `[lib] name`, `System.loadLibrary` and manifest `android.app.lib_name` disagree | all three must be `main` — [scaffolding.md](scaffolding.md) §5.2 |
| `Duplicate class kotlin.jvm.internal.…` | `games-activity` pulls older `kotlin-stdlib-jdk7/8` split artifacts; `appcompat` brings a newer main stdlib | `resolutionStrategy.force(...)` on both split artifacts — [toolchain.md](toolchain.md) §10 |
| Orientation toggle, dialogs or vibration silently dead; build perfectly clean | the Java namespace was moved; Rust resolves the class **by name over JNI** at call time | put the class back, or update every JNI class-name string — [scaffolding.md](scaffolding.md) §5.1 |
| Debug APK is ~266 MB; `adb install` times out | the strip stage was skipped | `llvm-strip --strip-all` before Gradle, **on debug too**, and `keepDebugSymbols += "**/*.so"` so AGP does not redo it |
| `am start -n <applicationId>/.MainActivity` → class not found | `applicationId` and the Java package legitimately differ | derive the component from `aapt2 dump badging`, never by concatenation — [build-script.md](build-script.md) §10 |
| App installs, launches, shows a **black screen**, no crash | the game archive is missing from `assets/`, or the archive constant does not match its filename | check both; add the fail-fast Gradle asset check — [scaffolding.md](scaffolding.md) §9.2 |
| PowerShell aborts a step that actually **succeeded** | `$ErrorActionPreference='Stop'` plus `2>&1` on a native tool that logs progress to stderr (cargo-ndk does) | never redirect; check `$LASTEXITCODE` — [host-portability.md](host-portability.md) §4 |
| `version.properties` comments corrupted; Gradle reads the wrong version | `Set-Content -Encoding utf8` writes a BOM and re-encodes through the console codepage; Java `Properties.load()` decodes ISO-8859-1 | `[System.IO.File]::WriteAllLines` + `UTF8Encoding($false)`; keep the file ASCII-only |
| Stage banners render as `??` on Windows | non-ASCII characters in the script | keep both build scripts ASCII |
| `./gradlew: bad interpreter: /bin/sh^M` | the Unix wrapper got CRLF line endings | `.gitattributes` with `gradlew text eol=lf`; re-checkout |
| Headless probe shows a black screen | `--ms` omitted — the guest idles on a platform timer and there is no virtual clock | always pass `--ms`; make the flag required |
| Probe's differential render check prints nothing | the check is gated on an explicit dump-frame list | pass the frame list, or remove the gate — [testing-and-ci.md](testing-and-ci.md) §6 |
| `cargo test -p <host crate>` dies instantly | the linked winit/platform-activity dependency graph | use the root `*_selftest.rs` + `rustc --test` harness — [testing-and-ci.md](testing-and-ci.md) §2 |
| Bare `cargo build --target aarch64-linux-android` links against the wrong API level | a checked-in `.cargo/config.toml` naming an old platform linker | delete the file; go through cargo-ndk — [host-portability.md](host-portability.md) §9 |
| CI cannot find the NDK | the revision is pinned in Gradle but not installed on the runner | `sdkmanager --install "ndk;<pinned revision>"`; keep the revision in one place |
| Gradle picks a different JDK than the script | `JAVA_HOME` differs between the shell and the Gradle daemon | run with `--no-daemon`, and set `org.gradle.java.home` or export `JAVA_HOME` in the script |
| Two projects on one machine accidentally share a keystore | they share a `${GRADLE_PREFIX}` in `$HOME/.gradle/gradle.properties` | one prefix per project — [scaffolding.md](scaffolding.md) §4 |
| Release build reaches the server from your phone but from nobody else's | your device has an on-device config override shadowing the baked value | delete the on-device file and retest, then check the `.so` — [config-baking.md](config-baking.md) §9 |

## 2. The clean-build class: it succeeded and it is wrong

Six failures in this project produce **zero build output**. They are the reason for the gates.

| what is wrong | when you find out | the gate that catches it |
|---|---|---|
| baked config resolved to the fallback | when a user reports the feature is dead | artifact scan (stage 7) |
| no signing config | when someone else tries to install | output filename + `apksigner verify` (stages 8–9) |
| the three native-lib names disagree | on first launch | scaffold assertion |
| the Java namespace was moved | when a user taps a dialog that does nothing | none — do not move it |
| the game archive is absent | on first launch, black screen | fail-fast Gradle asset check |
| `versionCode` not bumped | while handing out the build | automatic bump on `--release` |

If you are adding a new build-time behaviour, ask which of these categories it falls into and
which gate would catch it. If the answer is "none", write the gate before writing the feature.

## 3. Compile and link failures

**`error: linker \`aarch64-linux-android26-clang\` not found`** — the NDK revision Gradle pins is
not installed, or `ANDROID_NDK_HOME` points elsewhere. Check
`$ANDROID_HOME/ndk/<revision>/toolchains/llvm/prebuilt/<host-triple>/bin/`.

**`error: failed to run custom build command for <crate>`** on a windows-gnu host — almost always
§1's dlltool issue reported one layer up. Look for `dll_imports.lib` in the full output.

**Rust edition parse errors on an otherwise valid file** — a toolchain older than the edition the
crate declares. Add `rust-toolchain.toml`.

**Link error mentioning GameActivity symbols** — version skew between the `android-activity`
version winit pulls and the `androidx.games:games-activity` version Gradle resolves. They must
match; rebuild from clean after changing either — [toolchain.md](toolchain.md) §4.

**Two copies of `android-activity` in `cargo tree`** — you added a direct dependency on it.
Remove it and let winit own the version.

## 4. Install and launch failures

Order of checks, cheapest first:

```bash
# 1. is it signed, and with which certificate?
"$BUILD_TOOLS/apksigner" verify --print-certs app-release.apk

# 2. what does the APK actually declare?
"$BUILD_TOOLS/aapt2" dump badging app-release.apk | head -20

# 3. is the native library present, and stripped?
unzip -l app-release.apk | grep 'lib/'

# 4. is the game archive present?
unzip -l app-release.apk | grep 'assets/'

# 5. what does the device say?
adb -s "$ANDROID_SERIAL" logcat -c
adb -s "$ANDROID_SERIAL" shell am start -n "$COMPONENT"
adb -s "$ANDROID_SERIAL" logcat | grep -E 'AndroidRuntime|UnsatisfiedLink|main:'
```

Step 5's log filter is `main:` because the crate is named `main`
([toolchain.md](toolchain.md) §5). If you see nothing at all from your own code, the runtime log
filter or the compile-time max level is excluding it — check both.

## 5. Script-level failures

**A stage silently skipped.** Every stage prints `[n/10] <name>`. If a number is missing from the
log, a conditional swallowed it. The two that get skipped most often are `verify-artifact` (when
the config resolution produced nothing to require) and `strip` (when the tool was not found).
Both should be errors, not skips.

**The build works for you and not for a colleague.** Almost always a tool discovered from an
absolute path rather than a candidate list, or a value read from your environment that is not in
theirs. Run the script with the relevant environment variables unset and see whether it fails
with a useful message — [toolchain.md](toolchain.md) §11.

**The `.sh` and the `.ps1` disagree.** Run the parity diff in
[host-portability.md](host-portability.md) §10. A gate that exists in only one script is worse
than no gate, because a contributor on the other OS will believe it ran.

## 6. Diagnostic one-liners

```bash
# what did we actually bake into the shipped .so?
strings -a android_app/app/src/main/jniLibs/arm64-v8a/libmain.so | grep -E 'tls://|^[0-9a-f]{64}$'

# is the .so stripped?
file android_app/app/src/main/jniLibs/arm64-v8a/libmain.so     # expect "stripped"

# what versionCode did Gradle actually use?
"$BUILD_TOOLS/aapt2" dump badging app-release.apk | grep versionCode

# which build-tools directory got picked?
ls -1 "$ANDROID_HOME/build-tools" | sort -V | tail -1

# which JDK is Gradle using?
cd android_app && ./gradlew --no-daemon -q javaToolchains | head -20

# did cargo actually rebuild, or hit the cache?
touch wie_android/src/lib.rs && cargo ndk -t arm64-v8a --platform 26 build -v 2>&1 | head -5
```
