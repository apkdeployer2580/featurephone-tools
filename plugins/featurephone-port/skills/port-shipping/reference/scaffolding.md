# Scaffolding a port project

Provenance: **(A)** generic · **(B)** worked example from one reference port · **(C)** never copy.

## Contents

1. [Why scaffolding is scripted, not manual](#1-why-scaffolding-is-scripted-not-manual)
2. [The generator contract](#2-the-generator-contract)
3. [The parameter table](#3-the-parameter-table)
4. [Blast radius, per parameter](#4-blast-radius-per-parameter)
5. [The three parameters that fail at runtime, not build time](#5-the-three-parameters-that-fail-at-runtime-not-build-time)
6. [applicationId is permanent](#6-applicationid-is-permanent)
7. [What the generator must verify after substituting](#7-what-the-generator-must-verify-after-substituting)
8. [Project layout the template produces](#8-project-layout-the-template-produces)
9. [Five gaps a template must close](#9-five-gaps-a-template-must-close)
10. [Vendoring the emulator fork](#10-vendoring-the-emulator-fork)
11. [What is *not* scaffold](#11-what-is-not-scaffold)
12. [PORT_NOTES.md](#12-port_notesmd)

---

## 1. Why scaffolding is scripted, not manual

A port is a Cargo workspace + an Android Gradle project + a vendored emulator core + a headless
probe crate. Renaming it for a new title is not a find-and-replace: the identifiers are spread
across PowerShell, Bash, Kotlin DSL, Rust, a Java source file, an Android manifest, `.gitignore`
and standalone test files, and **three of the agreements fail silently at runtime**.

Measured blast radius on the reference port **(B)** — excluding `vendor/`, `target/`,
`node_modules/`, `build/`, `.git/`:

| where | occurrences of the project token |
|---|---|
| the build script | 61 |
| `app/build.gradle.kts` | 25 |
| the Rust config module | 10 |
| the Rust entry module | 5 |
| the probe crate, the Java activity, `Cargo.toml` | 2 each |
| 15 further files | 1 each |

97 files contained the token in total, of which the actionable non-Markdown set is ~20.
A human doing this by hand gets it 95% right and then spends an afternoon on an
`UnsatisfiedLinkError` or a dialog that does nothing. **(A)**

## 2. The generator contract

**(A) No template repository ships with this plugin, and the generator refuses to run without
one** — it exits with `Nothing was written` rather than producing a half-project, which is the
right behaviour. §3 and §7–§10 below *are* the template's specification, together with
`scaffold.py`'s token table and its `scaffold.toml` reader. Build one once from that spec, tag
it, and point `--template-repo`/`--template-ref` (or `--offline-template`) at it. **The tag is
what makes the second and third port cheap** — an untagged branch generates a project you
cannot reproduce.

```
python "${CLAUDE_PLUGIN_ROOT}/tools/scaffold.py" \
  --dest ./mygame --name mygame --display "My Game" \
  --app-id com.example.mygame --env-prefix MYGAME --gradle-prefix mygame \
  --archive-name mygame.zip --platform ktf \n  --template-repo <your-template-url> --template-ref <your-tag>
```

| flag | purpose |
|---|---|
| `--dest` | directory to create; must not already exist (`--force` allows an *empty* one) |
| `--name` / `--display` | lowercase project token / human-readable label |
| `--app-id` | Android `applicationId` — **permanent after a store upload**, §6 |
| `--env-prefix` / `--gradle-prefix` | the UPPERCASE env-var prefix and its lowercase gradle-property twin, §4 |
| `--archive-name` | the filename of the dump *you* will supply; six sites depend on it, §4 |
| `--platform` | `ktf` \| `lgt` \| `skt` \| `j2me` — which emulator backend the host constructs |
| `--screen WxH` | guest screen size (default `240x320`) |
| `--template-repo` / `--template-ref` | where the template comes from and **which tag**. A branch works and is not reproducible. |
| `--offline-template DIR` | use a local checkout instead of fetching — the air-gapped and the "I forked the template" path |
| `--no-server` | omit the replacement-server component. Most titles are offline; take this by default. |
| `--extra-substitution OLD=NEW` | one more **word-boundary** replacement, repeatable. Never a blind string replace. |
| `--dry-run` | print the plan and the parameter table, write nothing. Do this first, always. |

Steps, in order:

1. Fetch the template repo at the **pinned tag** (or use `--offline-template`), copy into
   `--dest`, remove `.git`. Never track a moving branch: a scaffold that changes under you is
   indistinguishable from a bug in your port.
2. Substitute the parameter table with **word-boundary** replacements. Never a blind
   `str.replace` — the project token appears inside longer identifiers, and a blind pass will
   corrupt them.
3. **Verify** (§7). Fail loudly rather than emitting a project that builds and misbehaves.
4. Emit `PORT_NOTES.md` seeded with the chapter checklist and empty slots for the address-model
   constants (§12).

**Nothing lands in `--dest` unless every verification in §7 passes.** A half-substituted project
is worse than no project: it builds, and you cannot tell by looking which half was renamed.

## 3. The parameter table

| parameter | example | appears in | rename-safe? |
|---|---|---|---|
| project name | `mygame` | workspace member names, `settings.gradle.kts` root project name | yes |
| display name | `My Game` | `res/values/strings.xml`, launcher label | yes |
| `applicationId` | `com.example.mygame` | `app/build.gradle.kts`, one place | **permanent after a store upload**; also the on-device data path |
| **Java namespace** | `net.wie.emulator` | gradle `namespace`, the `java/net/wie/emulator/` source dir, **and Rust JNI class lookups** | **DO NOT RENAME** — §5 |
| **native lib name** | `main` | Cargo `[lib] name`, `System.loadLibrary`, manifest `android.app.lib_name` | **must stay `main`** — §5 |
| env prefix | `MYGAME_` | build script, `build.gradle.kts`, the Rust config module, selftests | yes — the single highest-leverage rename |
| gradle property prefix | `mygame.` | build script, `build.gradle.kts` | yes |
| archive constant | `mygame.zip` | one Rust const, the assets path, `.gitignore` ×2, the probe default, any repack tool | yes — but 6 sites |
| guest screen size | e.g. `240x320` | two Rust consts | yes |
| launcher icon | — | mipmaps + the icon generator | yes; template ships neutral placeholders |
| platform backend | `ktf` \| `lgt` \| `skt` | which emulator crate the host constructs | yes |

Everything else — server code, RE tooling, distribution notes, per-title Markdown — is **content,
not scaffold**. The generator creates empty equivalents; it does not rename someone else's. §11.

## 4. Blast radius, per parameter

**Env prefix (highest leverage).** In the reference port the prefix appeared as ~10 distinct
identifiers **(B)**: `_SERVER_HOST` (15 hits), `_SERVER_PIN` (9), `_SERVER_PORT` (6),
`_KEYSTORE` (6), `_KEYSTORE_PASSWORD` (7), `_KEY_ALIAS` (8), `_KEY_PASSWORD` (7), `_ADB` (2),
plus two feature toggles. All of them live in exactly **three code files plus one test file**:
the build script, the Gradle module, and the Rust config module. Renaming the prefix is one
mechanical pass with a high payoff, which is why the generator templates it rather than asking
the user to keep the reference's prefix.

**Gradle property prefix.** Mirrors the env prefix (`mygame.keystore`, `mygame.keystorePassword`,
`mygame.keyAlias`, `mygame.keyPassword`, `mygame.serverHost`, `mygame.serverPort`,
`mygame.serverPin`). 25 hits in the Gradle module, 12 in the build script **(B)**. These names
are read from the *user's* `$HOME/.gradle/gradle.properties`, so two ports on one machine that
share a prefix will silently share a keystore.

**Archive constant.** Six sites, and each one fails differently:

```
wie_android/src/lib.rs      const BUNDLED_GAME_ASSET   -> black screen, logged error, no crash
app/src/main/assets/…       the actual file            -> same
.gitignore  (path rule)     assets/*.zip               -> licensed bytes get committed
.gitignore  (name rule)     <archive>.zip*             -> a stray copy elsewhere gets committed
wie_probe   default         --zip default              -> probe silently runs the wrong build
repack tool default         nested-archive target      -> patches nothing, reports success
```

## 5. The three parameters that fail at runtime, not build time

These are the reason scaffolding is scripted. All three produce a **clean build**.

### 5.1 The Java namespace — DO NOT RENAME

The Rust side looks the activity class up **by name over JNI**, because on Android
`ndk_context` hands you the *Application* context, not the Activity — so the code goes through
`getClassLoader().loadClass("<namespace>.MainActivity")` with the class name as a **string
literal**. Move the Java package and:

* Gradle is happy (namespace and directory agree).
* Rust compiles (it is a string).
* The app launches.
* The orientation toggle, the server/account dialogs and vibration **do nothing**, forever, with
  no error visible without logcat.

So the template's namespace is deliberately decoupled from `applicationId` and is **not** a
scaffold parameter. If someone insists on changing it, it must be changed in three places
simultaneously — gradle `namespace`, the source directory, and every JNI class-name string —
and the change must be validated by actually invoking each JNI path on a device. **(A)**

### 5.2 The native library name

GameActivity does `System.loadLibrary("main")` from the Java activity, the manifest declares
`android.app.lib_name = main`, and Cargo must produce `libmain.so`:

```toml
[lib]
name       = "main"
crate-type = ["cdylib"]
```

Disagreement → `UnsatisfiedLinkError` at launch. A second, subtler consequence: the crate is
named `main`, so the **runtime log filter's module path is `main`** (`warn,main=info`), not your
project name. Rename the crate and every log line disappears.

### 5.3 The three-way agreement is not checkable by the compiler

`scaffold.py` therefore asserts it after substitution (§7). Do not remove that check when
adapting the template.

## 6. applicationId is permanent

Two separate irreversibilities:

1. **Store identity.** Once a build with `com.example.mygame` is uploaded, that id is taken
   forever, by you, on that account. There is no rename.
2. **Data path.** It *is* `/data/data/<applicationId>/`. Changing it makes a different app with
   no access to previous saves, settings, layout files or the account identifier — from the
   user's point of view the game is wiped.

Also: `applicationId` and the Java package are **not** the same thing (§5.1). Anything that
launches the app must derive the component from `aapt2 dump badging`, never by concatenating
`applicationId` + `.MainActivity` — that string stops resolving the moment they diverge. **(A)**

## 7. What the generator must verify after substituting

Fail the scaffold, do not warn, on any of:

* any `__TEMPLATE_*__` token survives anywhere in `--dest`;
* the three native-lib names disagree (Cargo `[lib] name`, `loadLibrary` argument, manifest
  `android.app.lib_name`);
* the Java namespace differs from the gradle `namespace`, or the source directory does not match
  either;
* the archive constant in Rust, the `.gitignore` name rule and the probe default disagree;
* `applicationId` is not a valid Android package id, or still contains `example` **and** the user
  passed a non-example display name (warn loudly — an accidental `com.example.*` upload is
  permanent);
* the env prefix is not `[A-Z][A-Z0-9_]*`, or the gradle prefix is not its lowercase form.

Then **print the parameter table it applied**. The user must be able to see, at scaffold time,
what every downstream error message will be talking about.

## 8. Project layout the template produces

```
<dest>/
├── Cargo.toml                     workspace root; [patch] redirects the emulator fork
├── rust-toolchain.toml            pins the stable toolchain and the edition
├── rustfmt.toml                   same settings as the vendored core, so fork diffs stay small
├── .editorconfig
├── build_android.ps1              identical stage names + gates …
├── build_android.sh               … as this one
├── .github/workflows/android.yml  cargo-ndk build + standalone selftests
├── LICENSE  THIRD_PARTY_NOTICES.md  README.md  PORT_NOTES.md
├── android_app/
│   ├── build.gradle.kts  settings.gradle.kts  gradle/wrapper/…  gradlew  gradlew.bat
│   ├── version.properties         versionCode / versionName, ASCII-only
│   └── app/
│       ├── build.gradle.kts
│       └── src/main/
│           ├── AndroidManifest.xml
│           ├── java/net/wie/emulator/MainActivity.java
│           ├── assets/            <-- the game archive goes here; gitignored; NOT shipped
│           └── res/
├── wie_android/                   the host crate  (cdylib "main")
│   └── src/…                      dependency-free modules that selftests include!
├── wie_probe/                     headless runner; same [patch]ed core as the app
├── vendor/
│   ├── UPSTREAM.txt               repo URL + base commit SHA + date
│   ├── wie/                       the emulator fork
│   └── arm32_cpu/                 the CPU fork
├── tools/                         project-local scripts (target.toml lives here)
└── *_selftest.rs                  standalone rustc --test harnesses at the repo root
```

`app/src/main/jniLibs/` is **generated by cargo-ndk and never committed**.

## 9. Five gaps a template must close

These are defects measured in the reference port; a template that reproduces them is a template
that reproduces its bugs. **(A)**

1. **`vendor/UPSTREAM.txt`.** The reference vendored its emulator fork by *copying* a cargo git
   checkout: no `.git`, no submodule, no recorded base commit, and `Cargo.lock` therefore has no
   git source line for it at all. Consequence: the fork cannot be diffed against upstream and
   cannot take upstream fixes. Record repo URL + base SHA + date, and refresh it on every
   re-vendor. §10.
2. **Fail fast when the game archive is absent.** The reference build *succeeds* with no archive
   in `assets/` and produces an APK that installs and shows a black screen. For a public
   template this is the single most important hardening: a Gradle task that fails with
   "supply your own dump at `app/src/main/assets/<archive>.zip`".
3. **`rust-toolchain.toml`.** Nothing pinned the toolchain; the crate uses a recent edition and
   will simply fail to parse on an older stable, with a confusing error.
4. **Delete `.cargo/config.toml`.** In the reference it hardcoded absolute Windows linker paths
   into one user profile, *and it was dead config*: cargo-ndk exports
   `CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER` / `CC_*` / `AR_*`, and cargo environment
   variables take precedence over config files. Worse, its API level (24) disagreed with both
   `--platform 26` and `minSdk 26`, so anyone running a bare `cargo build --target
   aarch64-linux-android` got an API-24 link against an API-26 app. Rely on cargo-ndk.
5. **A `.sh` beside the `.ps1`, plus CI.** Identical stage names, identical gates
   ([host-portability.md](host-portability.md)), and a GitHub Actions workflow that actually
   builds for Android. The reference inherited a CI that ran `cargo test --all` over a workspace
   containing the Android crate — i.e. it tried to host-build the one crate that cannot be
   host-built ([testing-and-ci.md](testing-and-ci.md)).

## 10. Vendoring the emulator fork

Keep the `[patch]` architecture — it is the right call **(A)**. Every consuming crate declares
the **upstream git dependency**, and the workspace root redirects the whole graph:

```toml
[patch."https://github.com/<upstream>/arm32_cpu.git"]
arm32_cpu = { path = "vendor/arm32_cpu" }

[patch."https://github.com/<upstream>/wie.git"]
wie_backend = { path = "vendor/wie/wie_backend" }
wie_ktf     = { path = "vendor/wie/wie_ktf" }
# … one line per vendored crate …
```

Why `[patch]` and not direct path dependencies: the CPU crate is a **transitive** dependency of
the emulator's ARM core. Patching at the workspace root applies your fork to the *entire*
dependency graph without editing the emulator's own manifests — which keeps your diff against
upstream small enough to rebase.

Mirror the CPU patch **inside** the vendored emulator workspace too, so that tests run from
inside `vendor/wie` exercise the same interpreter the APK ships, and so features your fork adds
to the CPU crate resolve at all.

Replace copy-vendoring with either (i) a git submodule pinned to a fork commit, or
(ii) `vendor/UPSTREAM.txt`. Without one of the two, "track the fork" is impossible.

## 11. What is *not* scaffold

The generator creates **empty equivalents**, never renamed copies, of:

* the replacement-server directory (skipped entirely with `--no-server`),
* the RE tooling directory (the plugin's `${CLAUDE_PLUGIN_ROOT}/tools/` are the generic ones;
  per-title decoders live in the user's repo),
* the distribution directory,
* every per-title Markdown note.

**Never scaffold a game archive, extracted binary, dumped string table, sprite atlas, icon
derived from original art, or store-listing asset.** The template ships zero bytes of any game
and states the bring-your-own-dump posture in the first paragraph of its README. This is not
only the legal position, it is what makes the template useful to a stranger.

## 12. PORT_NOTES.md

The generator seeds one file in the new project, pre-filled with:

* the chapter checklist with exit criteria (from the router skill's pipeline),
* four empty slots for the address-model constants — image base, BSS size, static base,
  relocation delta — plus the relocation **unit**, which is the one that silently corrupts every
  downstream tool if guessed,
* an empty "constants and their provenance" section.

That last section encodes the reference port's single most transferable convention **(A)**:
**next to every non-obvious constant, record the symptom, the date and the measurement that
produced it.** A value with no recorded provenance is a value the next person will "clean up".
