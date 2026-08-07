# Build-time configuration: verify it in the artifact, not in the build script

Provenance: **(A)** generic · **(B)** worked example from one reference port · **(C)** never copy.

> **The rule, stated once:** any constant resolved at compile time must be **verified in the
> produced binary**, not in the script that produced it. A compile-time environment lookup that
> resolves to nothing does not fail — it succeeds, with the fallback, silently. **(A)**

## Contents

1. [The mechanism](#1-the-mechanism)
2. [The failure, exactly as it happened](#2-the-failure-exactly-as-it-happened)
3. [Why the obvious checks do not work](#3-why-the-obvious-checks-do-not-work)
4. [Defence 1 — resolve from persistent config, not only the shell](#4-defence-1--resolve-from-persistent-config-not-only-the-shell)
5. [Defence 2 — a release with no destination is a hard error](#5-defence-2--a-release-with-no-destination-is-a-hard-error)
6. [Defence 3 — scan the produced .so](#6-defence-3--scan-the-produced-so)
7. [Choosing the strings you require](#7-choosing-the-strings-you-require)
8. [Where the gate belongs in the pipeline](#8-where-the-gate-belongs-in-the-pipeline)
9. [The runtime layering this pairs with](#9-the-runtime-layering-this-pairs-with)
10. [Generalising beyond a server address](#10-generalising-beyond-a-server-address)
11. [What must never be baked](#11-what-must-never-be-baked)

---

## 1. The mechanism

The host crate reads configuration at **rustc** time:

```rust
// wie_android/src/config.rs   (shape, not a copy)
const fn build_time_host() -> Option<&'static str> { option_env!("MYGAME_SERVER_HOST") }
const fn build_time_port() -> Option<&'static str> { option_env!("MYGAME_SERVER_PORT") }
const fn build_time_pin()  -> Option<&'static str> { option_env!("MYGAME_SERVER_PIN")  }

fn build_time_endpoint() -> Endpoint {
    match (build_time_host(), build_time_port()) {
        (Some(h), Some(p)) => Endpoint::parse(h, p),
        (Some(h), None)    => Endpoint::parse(h, DEFAULT_PORT),
        _                  => Endpoint::loopback(),   // <-- the trap lives here
    }
}
```

`option_env!` is evaluated by the compiler, from the **rustc process's** environment. Not the
shell you typed in, not the Gradle daemon's, not the CI job's — the environment of the exact
process cargo spawned. Three consequences:

* A variable exported in a *parent* shell that cargo did not inherit is invisible.
* A variable set after cargo's fingerprint was already computed produces a **cache hit**: the
  crate is not recompiled and the old constant survives.
* A typo in the variable's name is not an error at any level. It is the `_ =>` arm.

## 2. The failure, exactly as it happened

**(B)** A release APK was built with the public server host exported. It worked. Later the same
build was reproduced in a shell where the variable had not been exported. Everything succeeded:
cargo, strip, Gradle, signing, install. The APK pointed at loopback.

It was **invisible on the developer's own device**, because that phone still had an on-device
override file from an earlier session, so the app connected fine. It was **total for everyone
else**: every online feature dead, no error message, no crash, no log a user could read.

That is the shape of the bug class: *the person who can detect it is the one person for whom it
does not reproduce.*

## 3. Why the obvious checks do not work

| check | why it fails |
|---|---|
| "did the script set the variable?" | it did; cargo did not necessarily see it |
| "did cargo exit 0?" | it always does |
| "does the binary mention `127.0.0.1`?" | **the loopback fallback string is always present** — it is the `_ =>` arm. Its presence proves nothing |
| "did I do a clean build?" | you did last time; the fingerprint is per-crate and per-env-var, and `option_env!` participation in fingerprints is not something to rely on |
| "the app connects on my phone" | on-device overrides shadow the baked value; see §9 |

The only check that cannot be fooled is: **is the string I wanted actually inside the binary?**

## 4. Defence 1 — resolve from persistent config, not only the shell

Do not make the build depend on a variable someone has to remember to export. Resolve each
setting from a chain, then **re-export the result into the process environment** before invoking
cargo, because that is the environment `option_env!` will read.

```
resolve(NAME):
    1. environment  ${ENV_PREFIX}_SERVER_HOST / _PORT / _PIN
    2. $HOME/.gradle/gradle.properties  ->  ${GRADLE_PREFIX}.serverHost / .serverPort / .serverPin
    3. a documented default (port only)
    then: export the resolved values into the environment for the cargo invocation
```

Use the same file the signing credentials live in
([build-script.md](build-script.md) §7). One place, outside the repository, holding everything
per-machine. A new machine is then one file to populate, not a shell ritual to remember.

## 5. Defence 2 — a release with no destination is a hard error

```
if release and not local_server and not resolved_host:
    fail with a multi-line message that gives the exact fix:
      - the env var to export, with an example value
      - the gradle.properties key, with an example value
      - the flag that means "yes, I really want loopback"  (--local-server)
```

Two properties matter. It must be a **hard failure**, not a warning — warnings scroll past. And
it must be **overridable by an explicit flag**, because building a loopback release for the adb
dev loop is legitimate, and a gate with no escape hatch gets commented out.

Warn separately if the host has no transport prefix (e.g. no `tls://`): *"the account identifier
will travel in CLEARTEXT."* A silent downgrade to plaintext is the same bug class as a silent
downgrade to loopback.

## 6. Defence 3 — scan the produced .so

★ **The one check that cannot be fooled.** Run it after strip, before Gradle.

```
python "${CLAUDE_PLUGIN_ROOT}/tools/verify_artifact_config.py" \
  android_app/app/src/main/jniLibs/arm64-v8a/libmain.so \
  --require-string "tls://YOUR_SERVER_HOST" \
  --require-string "<64-hex SHA-256 of your server cert DER>"
```

Inline equivalents, if you would rather not shell out:

```powershell
$bytes = [System.IO.File]::ReadAllBytes($So)
$ascii = [System.Text.Encoding]::ASCII.GetString($bytes)
if (-not $ascii.Contains($BuildHost)) {
    throw "libmain.so does NOT contain '$BuildHost'. Stale cargo build? Try: cargo clean -p wie_android"
}
if ($BuildPin -and -not $ascii.Contains($BuildPin)) {
    throw "libmain.so does NOT contain the certificate pin - this build would refuse every connection."
}
```

```bash
grep -qF -- "$BUILD_HOST" "$SO" || {
  echo "libmain.so does NOT contain '$BUILD_HOST'. Stale cargo build? Try: cargo clean -p wie_android" >&2
  exit 1; }
```

Notes that make it work in practice:

* Read the file as **bytes and decode as ASCII/latin-1**, do not use a text reader with an
  encoding that can throw on arbitrary bytes.
* Search for the **string you wanted**, never for the fallback (§3).
* The error message must name the most likely cause. In practice it is a stale cargo
  fingerprint, so put `cargo clean -p <crate>` in the message itself.
* Failing here catches, in one check: a variable exported into the wrong process, a typo in a
  name nothing validates, a stale build cache, and a `--release` run in a shell that never had
  the config.
* Strip first, then scan. Stripping removes symbols, not `.rodata` string literals — but running
  the gate on the *shipped* bytes means the gate tests what ships.

## 7. Choosing the strings you require

| requirement | good choice | bad choice |
|---|---|---|
| server destination | the full host string including scheme and any prefix | the bare hostname (may appear in an unrelated literal) |
| certificate pin | the exact 64-hex string | a prefix |
| feature flag on | a distinctive literal that only that code path contains | a boolean (booleans leave no string) |
| build channel | a literal like `channel:beta` | a version number that also appears elsewhere |

**A boolean baked with `option_env!` leaves no trace in the binary and cannot be verified this
way.** If a compile-time flag matters, make it bake a *string* — a marker literal in the enabled
branch — so the artifact can be interrogated. **(A)**

`--forbid-string` is also useful: assert that a development-only marker is *absent* from a
release artifact.

## 8. Where the gate belongs in the pipeline

Stage 7 of ten, i.e. after `strip` and **before** `gradle`:

* running it before Gradle saves the slowest step when it is going to be wrong anyway;
* the `.so` at that moment is exactly what will be packaged;
* it runs on debug builds too, wherever a debug build has required configuration.

If you also want an end-to-end check, unzip the finished APK and scan
`lib/arm64-v8a/libmain.so` from inside it. That is strictly stronger — it proves packaging did
not swap the file — but it is slower, so make it the release-only variant.

## 9. The runtime layering this pairs with

The baked value is one layer of three. Get the ordering right or the artifact gate will be
correct while the app still misbehaves.

```
1. on-device file in app-private storage     -> changes with no rebuild, no reinstall, no restart
2. build-time option_env! value              -> what the artifact gate verifies
3. a documented development default          -> loopback + the adb reverse dev loop
```

Three rules learned the hard way **(A)**:

* **Re-read at the point of use** (per connect), not once at construction. Otherwise the layer-1
  file needs an app restart and stops being useful.
* **Never gate the layering on `debug_assertions`.** The reference did, which pinned every debug
  build to loopback and made "an APK someone else can install that reaches a real server"
  impossible to produce. Debug/release is not the same axis as local/remote.
* **Layer 1 shadows the gate.** Your own device having an override file is exactly why §2 was
  invisible. When diagnosing "it works for me", check for the on-device file first.

Bake the pin at build time rather than relying on the on-device file: app-private storage is not
writable by an ordinary sideloading user, so a pin that only exists as a device file is a pin
only the developer has.

## 10. Generalising beyond a server address

Anything resolved at compile time deserves the same three defences:

* a build channel or flavour marker,
* an update/notice URL,
* a feature kill-switch,
* an analytics or crash-reporting endpoint,
* a certificate pin, a public key, an allowed-origin list,
* the archive filename constant, if you ever move it from a `const` to an env lookup.

The generic statement to put in your own build script's header comment:

> Every value this script bakes into the binary is verified **in the binary** before packaging.
> A build-time environment lookup that resolves to nothing does not fail; it silently produces a
> different program.

## 11. What must never be baked

* **Signing credentials.** They belong to the keystore resolution path, never to a compiled
  constant.
* **Any private key, of any kind.** A key in `.rodata` is a published key.
* **Extracted decryption or save-encryption keys for the original game.** Out of scope for this
  methodology entirely — not as a constant, not as a tool default, not as an example.
* **A real account identifier, device serial or personal path.** These end up in the artifact and
  then in every copy of it.

Everything you bake is readable with `strings`. Treat the set of baked constants as a public
document, because it is one.
