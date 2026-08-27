---
name: port-shipping
description: Scaffolds, builds, signs, versions, ships and publish-audits a native Android port of a feature-phone game on a forked wie emulator core. Covers project generation with every parameter substituted, cargo-ndk plus Gradle under GameActivity's three-way native-library naming rule, signing with the keystore kept outside the repo, versionCode discipline, verifying build-time-baked config inside the produced .so instead of trusting the build script, Linux/macOS/Windows parity, and a publish audit of the working tree and every git ref. Use when asked to scaffold or start a port project; to build, strip, sign, version, install, release or distribute the APK or AAB; on UnsatisfiedLinkError, duplicate kotlin-stdlib classes, an app-release-unsigned.apk, "App not installed", or a release that silently talks to 127.0.0.1; when preparing a store listing or sideload handout; or when asking what may safely be committed, open-sourced or published.
license: MIT
---

# port-shipping

Owns project generation, the build pipeline, release, distribution, and the publish audit.
Does **not** own emulator internals, guest RE, the Android UI layer, or protocol work.

Provenance tags, used here and in every reference file:
**(A)** generic procedure · **(B)** worked example measured on one reference port — the method
transfers, the number does not · **(C)** never copy.

## 0. Route

| you are doing | go to |
|---|---|
| starting a new project | §1 + [scaffolding.md](reference/scaffolding.md) |
| installing or pinning host tools | [toolchain.md](reference/toolchain.md) |
| running or editing the build | §2 + [build-script.md](reference/build-script.md) |
| any constant baked at compile time (server host, pin, feature flag) | [config-baking.md](reference/config-baking.md) |
| what a release build actually resolved at runtime, or reading logs and gauges off a phone you cannot `run-as` | [on-device-diagnostics.md](reference/on-device-diagnostics.md) |
| making the build run on Linux/macOS, or off the author's machine | [host-portability.md](reference/host-portability.md) |
| tests, device-free gates, CI | [testing-and-ci.md](reference/testing-and-ci.md) |
| release, signing, versionCode, AAB, handing out a build | §3 + [build-script.md](reference/build-script.md) |
| sideload-only distribution, telling players which build to run, pulling a bad build back | [distribution-and-recall.md](reference/distribution-and-recall.md) |
| "can I publish / open-source / commit this?" | §4 + [publish-audit.md](reference/publish-audit.md) |
| a build or install error | [troubleshooting.md](reference/troubleshooting.md) |
| the app closes with no error, an ANR, a native tombstone, or a crash only some phones show | [crash-triage.md](reference/crash-triage.md) |

## 1. Scaffold

Run to generate a project:

```
python "${CLAUDE_PLUGIN_ROOT}/tools/scaffold.py" --dest ./<name> --name <name> \
  --display "<Display Name>" --app-id com.example.<name> --env-prefix <NAME> \
  --gradle-prefix <name> --archive-name <archive>.zip --platform ktf \
  --template-repo <your-template-url> --template-ref <tag> \n  [--screen 240x320] [--no-server] [--dry-run]
```

State these three rules to the user **before** running it — each one fails at runtime, not at
build time, so a hand-edit that looks correct is not (A):

1. **Do not rename the Java namespace.** Rust resolves the activity class **by name over JNI**.
   Moving it gives a perfectly clean build and silently dead dialogs, orientation and vibration
   at call time.
2. **`applicationId` is permanent** once a build is uploaded to a store, and it *is* the
   on-device data path. Changing it later is a different app with no access to existing saves.
3. **The native library must be named `main` in three places at once** — Cargo `[lib] name`,
   `System.loadLibrary("main")`, and the manifest's `android.app.lib_name`. Disagreement is an
   `UnsatisfiedLinkError` at launch, not a compile error.
4. **You supply the template.** No template repository is published with this plugin, and
   `scaffold.py` refuses to run without one. [scaffolding.md](reference/scaffolding.md) §3 and
   §7–§10, plus `scaffold.py`'s own token table and `scaffold.toml` reader, **are** its
   specification. Build and tag it once — every later title reuses that tag, and that reuse is what
   makes the second and third port cheap.

Parameter table, per-parameter blast radius, and the five gaps a template must close:
[scaffolding.md](reference/scaffolding.md).

## 2. Build stages

Ten stages, in this order. `build_android.ps1` and `build_android.sh` must implement the **same
stage names and the same gates**; only stages 2 and 10 differ by host
([host-portability.md](reference/host-portability.md)).

| # | stage | what it must do / gate |
|---|---|---|
| 1 | `discover-tools` | SDK, NDK, JDK, adb, build-tools by **candidate list**, never an absolute path. Hard-fail with the env var to set. |
| 2 | `host-shim` | windows-gnu hosts only; no-op on Linux, macOS, windows-msvc. |
| 3 | `bump-version` | release only, and only when `--no-bump` is absent. Never on debug, never on an IDE sync. |
| 4 | `resolve-config` | resolve baked constants from env → user gradle.properties, then **re-export into the process env**. A release with no destination is a hard error. |
| 5 | `cargo-ndk` | one ABI, `--platform` equal to `minSdk`. Check the exit code explicitly; never redirect its stderr. |
| 6 | `strip` | `llvm-strip --strip-all`, **both profiles**. Tell AGP not to strip again. |
| 7 | `verify-artifact` | ★ **gate** — scan the produced `.so` for every string the build was supposed to bake in; fail if any is absent. |
| 8 | `gradle` | `./gradlew --no-daemon :app:assembleDebug` or `:app:assembleRelease`. |
| 9 | `verify-signature` | ★ **gate** — `apksigner verify --print-certs`; the output **filename** already tells you whether it is signed. |
| 10 | `install` | optional: `adb install -r`, then derive the launch component from `aapt2 dump badging`, never from `applicationId`. |

Stage 7, concretely:

```
python "${CLAUDE_PLUGIN_ROOT}/tools/verify_artifact_config.py" \
  android_app/app/src/main/jniLibs/arm64-v8a/libmain.so \
  --require-string "tls://YOUR_SERVER_HOST"
```

Flags, the versionCode rule, keystore creation and the signing resolution order:
[build-script.md](reference/build-script.md).

## 3. Release

```
# once, after setting the three keystore secrets in the environment
./build_android.sh --new-keystore          # or: ./build_android.ps1 -NewKeystore
# every build
./build_android.sh                         # debug
./build_android.sh --release               # signed, versionCode bumped, config verified in the .so
./build_android.sh --release --install
cd android_app && ./gradlew :app:bundleRelease    # AAB for a store — NOT done by the script
```

Non-negotiable at release (A):

* Keystore lives **outside the repository** (`$HOME/.android/keystores/<project>-release.jks`),
  is PKCS12, and has `-validity 10000`. Losing it ends your ability to update the app.
* The certificate SHA-256 digest **is the app's identity**. Publish it next to any sideload
  build with a "do not install if this differs" note.
* `versionCode` only ever increases. Android refuses an install with a lower or equal code and a
  store refuses one already uploaded.
* A signed APK that hands out cleanly is still wrong if stage 7 was skipped — see §4 and
  [config-baking.md](reference/config-baking.md).

## 4. Publish audit

Run before the first push, and again after any history rewrite:

```
python "${CLAUDE_PLUGIN_ROOT}/tools/publish_audit.py" --repo . --all-refs
```

Five facts that decide the outcome — details, remediation order and the checklist in
[publish-audit.md](reference/publish-audit.md):

1. **A tracked file is never re-evaluated against `.gitignore`.** An artifact committed once
   stays committed no matter what rule you add afterwards.
2. **Derived artifacts carry what you purged.** A built APK embeds the game archive *and* every
   baked constant, so a string purge cannot succeed while a built APK is tracked.
3. **History is not the working tree.** Audit **all refs** — the worst finding usually lives on
   a local-only branch, and a "backup-pre-purge" branch undoes the purge on `git push --all`.
4. **State, logs and databases default outside the source tree.** An account store is a
   credential store.
5. **No LICENSE means all-rights-reserved.** Ship `LICENSE` plus `THIRD_PARTY_NOTICES.md`,
   including the LGPL-2.1 relink obligation of any statically linked synthesiser.

Ship **zero bytes** of game content and **zero** extracted decryption or save-encryption keys —
not in the tree, not in history, not as a tool default, not as an example.
