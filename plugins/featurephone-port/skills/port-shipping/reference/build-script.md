# The build script: stages, flags, signing, distribution

Provenance: **(A)** generic · **(B)** worked example from one reference port · **(C)** never copy.

## Contents

1. [The pipeline in one picture](#1-the-pipeline-in-one-picture)
2. [The stages](#2-the-stages) — [2.1 Stage 0, the two input gates](#21-stage-0--the-two-input-gates-and-why-neither-is-optional) · [2.2 The headless soak](#22-the-gate-that-is-not-a-stage-the-headless-soak)
3. [Flags](#3-flags)
4. [versionCode discipline](#4-versioncode-discipline)
5. [version.properties encoding](#5-versionproperties-encoding)
6. [Keystore creation](#6-keystore-creation)
7. [Signing resolution order](#7-signing-resolution-order)
8. [The output filename is the diagnostic](#8-the-output-filename-is-the-diagnostic)
9. [apksigner verification: the fingerprint is the app's identity](#9-apksigner-verification-the-fingerprint-is-the-apps-identity)
10. [Install and launch](#10-install-and-launch)
11. [The AAB is the exception, not the terminal state](#11-the-aab-is-the-exception-not-the-terminal-state)
12. [Shipping a sideload build](#12-shipping-a-sideload-build)
13. [Two update rules that are really distribution rules](#13-two-update-rules-that-are-really-distribution-rules)
14. [Warts to avoid reproducing](#14-warts-to-avoid-reproducing)

---

## 1. The pipeline in one picture

```
   ★ GATE 0 (before anything expensive): the game archive EXISTS  +  every generator's
              --check is clean  →  hard failure, not a warning
   ▼
wie_android/src/*.rs
   │  cargo ndk -t arm64-v8a --platform 26 -o <jniLibs> build [--release]
   ▼
android_app/app/src/main/jniLibs/arm64-v8a/libmain.so        (cdylib named "main")
   │  llvm-strip --strip-all
   │  ★ GATE: scan the .so for every string that had to be baked in
   ▼
./gradlew --no-daemon :app:assemble{Debug,Release}
   │  packages jniLibs/**.so + src/main/assets/<archive>.zip + res + MainActivity.java
   ▼
app/build/outputs/apk/{debug,release}/app-{debug,release[-unsigned]}.apk
   │  ★ GATE: apksigner verify --print-certs
   ▼  [--install]  adb install -r  →  aapt2 dump badging  →  am start -n <pkg>/<activity>
```

The archive is injected as a **plain Gradle asset** — no task, no copy step, no `-P` property.
AGP packages `src/main/assets/**` wholesale. The Rust side never touches a filesystem path for
it; it reads it through the platform asset manager, which is the only zero-permission way to
ship guest content. A missing asset is a logged error and a black screen, **not** a crash — see
[scaffolding.md](scaffolding.md) §9.2 for the fail-fast Gradle check that a template must add.

**(A) "Packages wholesale" has an unstated and expensive consequence: NOTHING ELSE may live in
that directory.** Not a backup, not a pristine `.orig`, not a dry-run `.new`. **(B)** It shipped
twice on the reference — one tool's backup left beside the archive added **1.76 MB** to a release,
and later two by-products added **3.53 MB** of licensed content to a build that was handed to
people. Every gate passed both times; the only tell was artifact size. Backups belong outside every
packaged directory, at a path computed by one shared function
([archive-prep/reference/generated-assets.md §7](../../archive-prep/reference/generated-assets.md#7-where-by-products-live-and-why-pristine-is-moved-rather-than-deleted)).

## 2. The stages

Both host scripts implement the same names in the same order. **(A)** Stage 0 is the newest and the
cheapest: it is the only stage that can fail in under a second, and it guards the two input classes
no later stage can see.

| # | stage | detail |
|---|---|---|
| 0 | `check-inputs` | ★ gate — the game archive exists **and** every generator's `--check` is clean. Hard failure. §2.1 |
| 1 | `discover-tools` | candidate lists for SDK / NDK / JDK / adb / build-tools; hard-fail naming the env var. [toolchain.md](toolchain.md) §11 |
| 2 | `host-shim` | windows-gnu only; no-op elsewhere. [host-portability.md](host-portability.md) |
| 3 | `bump-version` | release only, unless `--no-bump`. §4 |
| 4 | `resolve-config` | env → user `gradle.properties`, then **re-export into the process environment**. Release with no destination = hard error. [config-baking.md](config-baking.md) |
| 5 | `cargo-ndk` | one ABI; `--platform` equal to `minSdk`; check the exit code explicitly, never redirect its stderr |
| 6 | `strip` | `llvm-strip --strip-all`, **both profiles**; tell AGP not to strip again |
| 7 | `verify-artifact` | ★ gate — [config-baking.md](config-baking.md) |
| 8 | `gradle` | `--no-daemon`, `:app:assembleDebug` or `:app:assembleRelease` |
| 9 | `verify-signature` | ★ gate — §8, §9 |
| 10 | `install` | optional; §10 |

**Stage 6 is mandatory on debug too.** An unstripped debug `libmain.so` produced a ~266 MB APK
on the reference **(B)** — big enough that `adb install` times out and everyone assumes the build
is broken. Then tell AGP to leave it alone, or it strips a second time and you pay the wall clock
twice:

```kotlin
packaging { jniLibs { keepDebugSymbols += "**/*.so" } }
```

### 2.1 Stage 0 — the two input gates, and why neither is optional

**(A) Gate 0a — the archive EXISTS.** Put it in the host build script, **before** the native build,
and make it a **hard failure with the exact path it wanted**. The archive is the one input the
repository cannot carry itself, so a fresh clone has no assets directory at all.

**(B) It is a gate because it already shipped without one.** A build produced on a second machine had
no archive; the packager took the empty directory, the build reported success, the signature
verifier passed, the install succeeded — and the app aborted on its first log line. It was handed to
a user in that state. Every stage upstream of the phone passes, which is why a warning is not
sufficient: warnings scroll past in a build that ends with `BUILD SUCCESSFUL`.

**(A) Gate 0b — presence is not correctness.** A generated or patched member that is **present but
stale** is a worse failure than a missing one, because it survives every gate in this pipeline *and*
a boot smoke test: the game starts, shows its title, and dies a few hundred frames later. So stage 0
also runs **every generator's `--check`**: rebuild the derived artifact in memory from the pristine
source, byte-compare it against the file that will ship, exit non-zero on mismatch, and print the
regeneration command.

**(B)** A build **range** — ten consecutive releases — shipped in exactly that state, with a data
member left pristine while the guest patches assumed the newer layout; the guest parser overran an
array and asked the allocator for a negative size. The symptom read as "the game starts and stops on
a black screen", which does not look like an asset problem. The gate was written afterwards and
caught a **second, independent** instance of the same class, on a different machine, on its first
run. Mechanism and tooling:
[archive-prep/reference/generated-assets.md](../../archive-prep/reference/generated-assets.md);
the fault as a symptom:
[archive-prep/reference/fault-dictionary.md F11](../../archive-prep/reference/fault-dictionary.md#13-f11-black-screen-shortly-after-the-title-allocation-failure-with-a-negative-size).

⚠ **(A) CI has no archive**, so gate 0a must be *skippable by an explicit flag* in CI — and a build
with that flag set must never produce a distributable artifact. Gate 0b's checks split: the ones
whose inputs are all in-repo run in CI unchanged; the ones that need the archive are **skipped, not
faked** ([testing-and-ci.md](testing-and-ci.md) §9).

### 2.2 The gate that is not a stage: the headless soak

**(A)** Stage 0 catches staleness you can compute. A **long headless run** catches everything else
that only appears after the title screen, and it is the one pre-release gate this pipeline cannot
express as a build step:

> **A few thousand frames of scripted input with zero fatal tick errors, zero allocation failures
> and zero panics** — not one non-black frame.

**(B)** On the reference port the difference between the two is stark: the stale-member build died at
frame **557** and its fix ran **5,200** frames clean. A first-frame smoke test reports both as
healthy. Run it before every handout; details in [testing-and-ci.md](testing-and-ci.md) §5.

## 3. Flags

| flag | effect |
|---|---|
| *(none)* | debug: no `--release`, `:app:assembleDebug`. Auto-signed with AGP's **per-machine throwaway debug key** — installs on your own phone, useless for distribution and it will not upgrade a release install. |
| `--release` | LTO build, versionCode bump, `:app:assembleRelease`, real signing, `apksigner verify`. **Hard-errors if no baked destination is configured.** |
| `--install` | `adb install -r` → HOME keycode → `aapt2 dump badging` → `am start` |
| `--new-keystore` | creates the release keystore **and returns**. Never implicit in a build. Refuses to overwrite an existing one. |
| `--no-bump` | `--release` without incrementing versionCode: re-sign or retry the same code |
| `--local-server` | explicitly build against the loopback dev loop; the only way to make a `--release` with no public destination. Nulls out an already-configured host and says so. |

PowerShell equivalents are the same names in PascalCase (`-Release`, `-Install`,
`-NewKeystore`, `-NoBump`, `-LocalServer`).

## 4. versionCode discipline

Bump **only** on `--release && !--no-bump`, so debug builds and IDE syncs leave it alone.

```
android_app/version.properties     versionCode=<int>
                                   versionName=<string>
```

* The script regex-matches `^\s*versionCode\s*=`, adds 1, rewrites the file.
* Gradle reads it back through `java.util.Properties`, with a fallback of `1` / `"0.1.0"` so
  that **a fresh clone with no `version.properties` still builds**.
* `versionName` is never rewritten by anything. Edit it by hand.

**(A) The lesson, and it is worth stating in the file itself:** a hand-edited monotonic constant
is a trap you hit at the worst possible moment — while handing someone a build that "won't
install". Android refuses an install whose versionCode is ≤ the installed one, and a store
refuses one already uploaded. Automate it or you will forget it exactly once, publicly.

## 5. version.properties encoding

Two constraints that meet in one file:

1. Java's `Properties.load()` decodes as **ISO-8859-1**. So declare the file **ASCII-only** and
   put no non-ASCII comments in it.
2. On Windows PowerShell 5.1, `Set-Content -Encoding utf8` writes a **BOM** and re-encodes
   through the console codepage. The BOM alone makes the first key unreadable to
   `Properties.load()`. Write it with an explicit no-BOM encoder:

```powershell
[System.IO.File]::WriteAllLines($path, $lines, (New-Object System.Text.UTF8Encoding($false)))
```

On Linux/macOS a plain `printf` / `sed -i` is already correct.

## 6. Keystore creation

```
keytool -genkeypair -v -keystore "$HOME/.android/keystores/<project>-release.jks" \
  -storetype PKCS12 -storepass "$KS_PASS" -alias "$KS_ALIAS" -keypass "$KEY_PASS" \
  -keyalg RSA -keysize 4096 -validity 10000 \
  -dname "CN=<Project> Port, OU=side project, O=self, C=<XX>"
```

Every choice here is load-bearing **(A)**:

* **Outside the repository.** No clone, no zip, no CI artifact can carry it away. This is the
  first line of defence; the `.gitignore` rule for `*.jks` is the second.
* **PKCS12, not JKS.** JKS is legacy, `keytool` nags on every use, and `apksigner` reads PKCS12
  natively.
* **`-validity 10000`** (~27 years). State the reason **without** reference to any store policy,
  because most ports of commercial titles never go near one (§11): an expired key **cannot sign**,
  and re-keying orphans every existing install — the only path back is uninstall-and-reinstall,
  which destroys every player's save. Long validity is therefore required no matter how you
  distribute. (A store adds its own expiry floor on top; that is a second, weaker reason.)
* **Refuse to overwrite.** Overwriting orphans every existing install — an existing user cannot
  update, only uninstall (losing their save) and reinstall.
* **Require the three secrets to be pre-set** in the environment and throw with the exact
  commands to set them, rather than prompting. Prompting cannot run in CI and encourages people
  to paste passwords into shell history.

The DN is cosmetic but it is **permanently visible** in `apksigner verify --print-certs` output
for every user who checks. Do not put a real name, address or email in it.

## 7. Signing resolution order

Identical in the script and in Gradle; first *complete* set wins:

1. **Environment:** `${ENV_PREFIX}_KEYSTORE`, `${ENV_PREFIX}_KEYSTORE_PASSWORD`,
   `${ENV_PREFIX}_KEY_ALIAS`, `${ENV_PREFIX}_KEY_PASSWORD`
2. **User Gradle properties** in `$HOME/.gradle/gradle.properties`:
   `${GRADLE_PREFIX}.keystore`, `.keystorePassword`, `.keyAlias`, `.keyPassword`

```kotlin
val canSign = ksFile != null && ksFile.exists() &&
              ksPass != null && ksAlias != null && ksKeyPass != null
```

The `signingConfigs { create("release") { … } }` block is **conditionally created**, with:

```kotlin
enableV1Signing = true   // what API 26 verifies
enableV2Signing = true   // everything since, and what a store expects
```

With no config resolved the release build **still succeeds, unsigned**. So emit a
`logger.warn` at configuration time for any task whose name contains "Release", and include the
diagnostic case that costs the most time: *the path is set but wrong*
(`configured keystore not found: <path>`). A silently missing variable and a typo in a path
produce the same unsigned APK; only the warning tells them apart. **(A)**

Both credential sources live **outside the repository**, and the same
`$HOME/.gradle/gradle.properties` also holds the baked-config values
([config-baking.md](config-baking.md)). ⚠ Two projects that share a `${GRADLE_PREFIX}` share a
keystore — see [scaffolding.md](scaffolding.md) §4.

## 8. The output filename is the diagnostic

AGP writes:

```
app-release.apk             <- signed
app-release-unsigned.apk    <- no signing config resolved
```

**(A) and quietly brilliant: the filename is the answer to "can anyone else install this?"**
Have the script take whichever file exists, and re-warn if the name matches `*unsigned*`. Do not
rename the artifact before that check; you would be deleting the only evidence.

**(A) And do not rename it *after* the check either — copy instead.** The later install stage
locates the artifact by the packager's own filename, so renaming in place turns a perfectly good
build into `no APK found` at install time. **(B)** That failure is recorded as a standing warning in
the reference port's handout directory. The rule that avoids both problems: **the packager's output
is never moved or renamed; the handout artifact is a COPY** into the distribution directory under
the handout naming convention (§12).

## 9. apksigner verification: the fingerprint is the app's identity

```
"$BUILD_TOOLS/apksigner" verify --print-certs "$APK" |
    grep -E 'SHA-256 digest|DOES NOT VERIFY|Signer #1 certificate DN'
```

The certificate SHA-256 digest **is** the app's identity to Android. It must be byte-identical on
every build you ever hand out; if it changes, existing installs cannot be updated at all.

Publish that digest next to any sideload build, with a *"do not install this file if the
fingerprint differs"* instruction. That single line turns an unverifiable APK download into
something a user can check. **(A)** Do **not** publish anyone else's fingerprint, and do not copy
one from an example — it is per-keystore.

## 10. Install and launch

```
adb -s "$ANDROID_SERIAL" install -r "$APK"
adb -s "$ANDROID_SERIAL" shell input keyevent KEYCODE_HOME
COMPONENT=$("$BUILD_TOOLS/aapt2" dump badging "$APK" | …)   # parse package + launchable-activity
adb -s "$ANDROID_SERIAL" shell am start -n "$COMPONENT"
```

⚠ **Derive the component from `aapt2 dump badging`, never by concatenating `applicationId` +
`.MainActivity`.** The moment `applicationId` and the Java namespace diverge — which is the
*intended* configuration ([scaffolding.md](scaffolding.md) §5.1) — the concatenated string stops
resolving and you get "class not found" from `am start` on a perfectly good APK. **(A)**

`$ANDROID_SERIAL` comes from the environment. **Never hardcode a device serial in a published
script.** The reference did, and that serial is one of the values that had to be scrubbed before
publication — see [publish-audit.md](publish-audit.md).

## 11. The AAB is the exception, not the terminal state

**(A) For a port of a commercial title the public-store path is normally ruled out, not merely
rare.** Decide it once, explicitly, and write it down: the documented default shape of this pipeline
is a **signed APK handed to specific people**. **(B)** The reference port made "no public store,
ever — sideload only" a standing decision, and kept every other piece of this file intact, because
none of it was store-driven in the first place.

What the store path *additionally* forces you to publish is the part people forget when they treat
it as a continuation of the build: **listing imagery** — which for a port is derived from the
original's art and is exactly the category the publish audit refuses — plus a store identity and a
content rating. That is a separate decision from the build. The posture, the handout manifest and
the recall ledger live in [distribution-and-recall.md](distribution-and-recall.md).

If you do go that way, `:app:bundleRelease` is deliberately **not** in the build script:

```
cd android_app && ./gradlew :app:bundleRelease
```

Keeping it manual means the frequent path (`--release` → APK, sideload, test) stays fast, and the
rare path is an explicit act. If you automate it, keep it behind its own flag and run the same
gates — an AAB is signed and versioned exactly like the APK, and store-side signing will re-sign it
with the upload key, so **the upload key is a second key you must also never lose**.

⚠ **Sideload-only removes the store's review; it removes none of the installer's rules.**
versionCode must still increase, the certificate must still be byte-identical forever, and the
applicationId is still the on-device data path. "There is no store to reject it" is never a reason
to relax §4, §6 or §9.

## 12. Shipping a sideload build

A `dist/README.md` next to a handout build should carry — and this convention transfers verbatim
**(A)**:

* filename convention `<project>-v<versionName>-vc<versionCode>.apk`;
* a table of `applicationId`, `minSdk`, ABI;
* **the certificate fingerprint, with "do not install if this differs"**;
* which server/config was baked in (the *fact*, not the secret — say "points at the public
  server" or "loopback only");
* **the withdrawn-build ledger** — not a note. It only grows, and it must separate two different
  statements, because they ask the reader for different things:
  * **RECALLED** — this build can destroy player data. Name the version ceiling and the mechanism in
    one clause ("*vc ≤ N: opening another slot from the title screen overwrites the loaded
    character*"). **(B)** Two separate save-destroying defects each produced such a line in the
    reference port.
  * **DO NOT HAND OUT** — a mid-session intermediate with a known bug. Annoying, not destructive.
* the exact command to reproduce the build;
* the honest closing statement: *"signing credentials live outside this repository, so this
  repository alone cannot produce an APK with the same fingerprint."*

⚠ **Do not commit the APK itself.** See [publish-audit.md](publish-audit.md) §2 — a built APK
embeds the game archive *and* every baked constant, and a tracked file is never re-evaluated
against `.gitignore`.

★ **(A) A recall line has to be written where the next person actually looks.** Its audience is not
the player who already has the file; it is **whoever next decides which artifact to hand someone** —
which is you, four months later, or a collaborator. So the same one-line recall goes into the
document every session reads first, as well as into the handout README. A recall that lives only
beside the artifact is a recall nobody reads.

★ **(A) Keep exactly ONE build in the handout directory: the current one.** At the moment of handing
something over, nobody re-reads a changelog to work out which file is newest — and a recalled build
surviving beside the current one is precisely how it gets distributed. The ledger records the dead
ones; the directory holds one file. Full posture, manifest and ledger:
[distribution-and-recall.md](distribution-and-recall.md).

## 13. Two update rules that are really distribution rules

1. **Never lose the signing key.** A different key means Android refuses the overwrite install;
   the only fix is uninstall + reinstall, which destroys every user's local save and account
   identifier.
2. **`versionCode` only ever increases.** There is no rollback. If you ship a bad build, the
   remedy is a higher versionCode, not a lower one.

If the title has an online component, remember that its login/notice message is your **only
broadcast channel to every installed copy** — it is the update-announcement mechanism.

## 14. Warts to avoid reproducing

Observed in the reference **(C)** — do not carry them into a template:

* **A duplicated print block.** The config summary was printed twice, the second copy
  re-deriving its values from the environment. Harmless but dead weight, and the two copies
  drifted (one emitted a non-ASCII character that PowerShell 5.1 renders as `??`). Emit one.
* **Hardcoded device serial and hardcoded adb path.** Env var + candidate list. And an install
  step that picks "the attached device" silently is how a build gets verified on the wrong phone —
  [device-verification.md](../../../shared/device-verification.md).
* **Tool by-products written into the packaged assets directory.** A backup or a dry-run `.new`
  beside the archive ships inside the artifact (§1). Every tool's output path comes from one shared
  function, and that function never points inside a packaged directory.
* **A `.cargo/config.toml` full of absolute linker paths** that cargo-ndk's environment variables
  override anyway, with an API level disagreeing with both `--platform` and `minSdk`. Delete it.
* **The NDK revision written out in three files.** Two is the practical minimum; make the third
  derive or check.
