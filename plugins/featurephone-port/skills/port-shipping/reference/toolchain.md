# Host toolchain

Provenance: **(A)** generic · **(B)** worked example from one reference port · **(C)** never copy.

## Contents

1. [Prerequisites](#1-prerequisites)
2. [What the user must supply — not in the repo](#2-what-the-user-must-supply--not-in-the-repo)
3. [Why cargo-ndk + Gradle, and not cargo-apk](#3-why-cargo-ndk--gradle-and-not-cargo-apk)
4. [GameActivity, not NativeActivity](#4-gameactivity-not-nativeactivity)
5. [The three-way native library name agreement](#5-the-three-way-native-library-name-agreement)
6. [Do not depend on android-activity directly](#6-do-not-depend-on-android-activity-directly)
7. [Cargo profiles](#7-cargo-profiles)
8. [Log levels are compiled out, not filtered](#8-log-levels-are-compiled-out-not-filtered)
9. [ABI and API level](#9-abi-and-api-level)
10. [The Kotlin stdlib duplicate-class trap](#10-the-kotlin-stdlib-duplicate-class-trap)
11. [Candidate-list tool discovery](#11-candidate-list-tool-discovery)
12. [Where every version is pinned](#12-where-every-version-is-pinned)

---

## 1. Prerequisites

| Tool | Version | Pinned where | Notes |
|---|---|---|---|
| Android SDK | any recent | `$ANDROID_HOME`, else the per-user default location | build-tools is **discovered** (highest version directory), not pinned |
| Android NDK | one exact revision | `ndkVersion` in `app/build.gradle.kts` **and** the build script | Gradle pins the revision; the build script must hard-fail if `$ANDROID_HOME/ndk/<rev>` is absent |
| compileSdk / targetSdk | 35 **(B)** | `app/build.gradle.kts` | that platform must be installed |
| minSdk | 26 **(A)** | `app/build.gradle.kts` | AAudio (rodio → cpal) needs API 26; this is the floor for audio, not a style choice |
| JDK | 17+ | `compileOptions` / `kotlinOptions` in `app/build.gradle.kts` | Android Studio's bundled JBR is a real JDK 17+ and is the documented fallback — no second download |
| Gradle | via the committed wrapper | `gradle/wrapper/gradle-wrapper.properties` | always `./gradlew`, never a system gradle |
| Android Gradle Plugin | matched to the wrapper | root `build.gradle.kts` | AGP and wrapper versions are coupled |
| Rust | stable, edition pinned | `rust-toolchain.toml` | the reference pinned **nothing** — add the file |
| Rust target | `aarch64-linux-android` | — | `rustup target add aarch64-linux-android` |
| cargo-ndk | pin it | `rust-toolchain.toml` cannot pin it; record it in the README and CI | the reference left it unpinned |
| adb | any | `$ANDROID_HOME/platform-tools` | only needed for `--install` |
| Python | 3.10+ | — | only for archive/icon/RE tooling |

A reference port's concrete pin set **(B)**: NDK `25.1.8937393`, compileSdk/targetSdk 35,
minSdk 26, AGP 8.7.2, Gradle wrapper 8.9, JDK 17 source/target (21 used to run Gradle),
`androidx.games:games-activity:4.4.0`, `android-activity` 0.6.1 (transitive), single ABI
`arm64-v8a`. Copy the *shape*; choose your own currency.

⚠ The NDK revision appears in **three hand-synced places** in the reference: the Gradle
`ndkVersion`, the build script's existence check, and `.cargo/config.toml`. Delete the third
(see [scaffolding.md](scaffolding.md) §9.4) and you are down to two — still two. A scaffold
should derive the build script's check from the Gradle value, or fail with a message naming both
files.

## 2. What the user must supply — not in the repo

* **The game archive** → `android_app/app/src/main/assets/<archive>.zip`, gitignored. A fresh
  clone therefore **cannot build a runnable APK**, and that is the correct default.
* **A release keystore and its three secrets** — created by the build script's
  `--new-keystore` stage, stored outside the repository.
* **Optionally** a private-server host and certificate pin, if the title has online features.

## 3. Why cargo-ndk + Gradle, and not cargo-apk

`cargo-apk` is deprecated and NativeActivity-only. GameActivity needs a real Gradle project:
Java sources, `androidx` dependencies, resources, a manifest AGP processes. Any Rust-cored
Android port with a Java activity lands on cargo-ndk + Gradle. **(A)**

The division of labour:

```
cargo ndk -t arm64-v8a --platform 26 -o android_app/app/src/main/jniLibs build [--release]
        -> android_app/app/src/main/jniLibs/arm64-v8a/libmain.so
llvm-strip --strip-all libmain.so
./gradlew --no-daemon :app:assemble{Debug,Release}
        -> packages jniLibs/**.so + src/main/assets/** + res + the Java activity
```

Gradle never invokes cargo. Keeping them separate is what makes the artifact gate in stage 7
possible ([config-baking.md](config-baking.md)): there is a moment where the `.so` exists and the
APK does not.

## 4. GameActivity, not NativeActivity

Enable it through winit's feature, not by depending on the activity crate:

```toml
winit = { version = "…", features = ["android-game-activity"] }
```

and on the Gradle side:

```kotlin
implementation("androidx.games:games-activity:4.4.0")
```

**Version skew here is a hard link error, not a warning.** `android-activity` bundles the C++
glue for one specific GameActivity release; the Gradle dependency must be that same release.
When you bump either, bump both and rebuild from clean. **(A)**

## 5. The three-way native library name agreement

```toml
# Cargo.toml
[lib]
name       = "main"
crate-type = ["cdylib"]
```

```java
// MainActivity.java
static { System.loadLibrary("main"); }
```

```xml
<!-- AndroidManifest.xml -->
<meta-data android:name="android.app.lib_name" android:value="main" />
```

All three must say `main`. The failure is `UnsatisfiedLinkError` **at launch** — nothing in the
build complains. Assert it in the scaffold and, if you can, in CI. **(A)**

Side effect worth knowing: because the crate is literally called `main`, the module path in
`log`/`tracing` filters is `main`. A runtime filter of `warn,main=info` is what enables your own
logs. Rename the crate and your logs vanish while everything still "works".

## 6. Do not depend on android-activity directly

winit pulls `android-activity` transitively at the exact version it was built against. Adding a
direct dependency gives you **two copies** in the graph, two sets of JNI glue, and behaviour that
depends on link order. Let winit own it. **(A)**

## 7. Cargo profiles

```toml
[profile.dev]
opt-level = 3          # a debug build must still emulate a CPU in real time

[profile.release]
lto            = true
codegen-units  = 1
# NOT panic = "abort"
```

* `opt-level = 3` on **dev** as well: the whole app is an instruction-decode/dispatch hot loop
  (order of a few million guest instructions per rendered frame). An unoptimised debug build is
  not merely slow, it is unusable and you cannot test anything on it. **(A)**
* `codegen-units = 1` + `lto` because the hot loop is one function-cluster; splitting codegen
  units costs inlining exactly there.
* **Explicitly not `panic = "abort"`.** It breaks `cargo test`, and this app unwinds across JNI
  boundaries. Recorded as a deliberate decision, not an oversight. **(A)**

## 8. Log levels are compiled out, not filtered

```toml
log     = { version = "…", features = ["release_max_level_info"] }
tracing = { version = "…", features = ["release_max_level_info"] }
```

Both, not one. Measured on the reference **(B)**: about 124 `__android_log_write` calls per
second issued from inside the tick loop, on the thread with a ~24 ms frame budget. Two traps:

1. `tracing`'s `#[instrument]` spans default to **INFO**, not DEBUG. "Just set the runtime level
   to Info" does not remove them — only the compile-time max level does.
2. A runtime filter still pays the formatting and the FFI call for anything above the compiled
   ceiling. Compile it out.

## 9. ABI and API level

Single ABI `arm64-v8a`: halves build time and APK size, and every Android phone since roughly
2017 is arm64. Add `armeabi-v7a` only if you have an actual 32-bit target device. **(A)**

Keep `--platform` (cargo-ndk) equal to `minSdk` (Gradle). A mismatch links against a different
API level than the app declares, and the symptom appears only on the oldest device you test on —
which is usually none of them.

## 10. The Kotlin stdlib duplicate-class trap

`games-activity:4.4.0` drags `kotlin-stdlib-jdk7` / `-jdk8` at an older version while
`appcompat` brings a newer main `kotlin-stdlib`; the split artifacts and the merged artifact then
collide:

```
Duplicate class kotlin.jvm.internal.… found in modules kotlin-stdlib-… and kotlin-stdlib-jdk8-…
```

Fix in the app module:

```kotlin
configurations.all {
    resolutionStrategy {
        force("org.jetbrains.kotlin:kotlin-stdlib-jdk7:<version matching your main stdlib>")
        force("org.jetbrains.kotlin:kotlin-stdlib-jdk8:<same>")
    }
}
```

This trap is generic to `games-activity:4.4.0` + a modern `appcompat`, not to any one game. **(A)**

## 11. Candidate-list tool discovery

**The single fix for the number-one open-source portability failure.** Every external tool is a
*candidate list*, never an absolute path, and a miss throws with the environment variable to set:

```powershell
$Sdk = @($env:ANDROID_HOME, "$env:LOCALAPPDATA/Android/Sdk") |
       Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $Sdk) { throw 'Android SDK not found. Set $env:ANDROID_HOME.' }
```

```bash
for c in "$ANDROID_HOME" "$HOME/Android/Sdk" "$HOME/Library/Android/sdk"; do
  [ -n "$c" ] && [ -d "$c" ] && SDK="$c" && break
done
[ -n "$SDK" ] || { echo "Android SDK not found. Set ANDROID_HOME." >&2; exit 1; }
```

Same shape for:

| tool | candidate order |
|---|---|
| SDK | `$ANDROID_HOME` → per-user default for the OS |
| JDK | `$JAVA_HOME` → a system JDK → **Android Studio's bundled JBR** (present wherever Studio is) |
| adb | `${ENV_PREFIX}_ADB` → `$SDK/platform-tools/adb` |
| build-tools | **highest version directory** under `$SDK/build-tools` — used for both `apksigner` and `aapt2` |

The reference port started with three absolute paths into one user profile, and **a second
machine could not run the build at all**. If you are adapting an existing script, converting each
absolute path into a candidate list — keeping the original path *first* so the original machine
behaves identically — is a mechanical, zero-risk change. **(A)**

⚠ Never leave a personal absolute path in a candidate list you publish. Publish
`$ANDROID_HOME`, the OS defaults, and nothing else.

## 12. Where every version is pinned

| thing | file |
|---|---|
| NDK revision | `app/build.gradle.kts` → `ndkVersion` (+ the build script's existence check) |
| compileSdk / minSdk / targetSdk | `app/build.gradle.kts` |
| AGP | root `build.gradle.kts` |
| Gradle | `gradle/wrapper/gradle-wrapper.properties` |
| JDK source/target | `app/build.gradle.kts` → `compileOptions` |
| GameActivity | `app/build.gradle.kts` dependency |
| Kotlin stdlib force | `app/build.gradle.kts` → `resolutionStrategy` |
| Rust toolchain + edition | `rust-toolchain.toml` |
| cargo-ndk | README + CI (nothing in-tree can pin it) |
| ABI + `--platform` | the build script's cargo-ndk invocation |

Keep this table in the project README too. Every one of these is a thing a new contributor will
have wrong on their first attempt, and every one produces a different confusing error.
