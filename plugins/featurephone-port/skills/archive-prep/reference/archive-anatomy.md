# Archive anatomy

What a carrier download package contains, which four names are load-bearing, what reads what during boot, and what the emulator never touches at all.

Tags: **(A)** generic · **(B)** worked example from the reference port · **(C)** do not copy.

## Contents

1. [The artifact chain](#1-the-artifact-chain)
2. [Outer archive inventory](#2-outer-archive-inventory)
3. [The app descriptor](#3-the-app-descriptor)
4. [Inner jar inventory — and the one fact that decides the project](#4-inner-jar-inventory--and-the-one-fact-that-decides-the-project)
5. [Platform detection, exactly](#5-platform-detection-exactly)
6. [The four load-bearing names](#6-the-four-load-bearing-names)
7. [The KTF boot trace — what reads what, in order](#7-the-ktf-boot-trace--what-reads-what-in-order)
8. [What the emulator never reads](#8-what-the-emulator-never-reads)
9. [Where the archive lives in the app](#9-where-the-archive-lives-in-the-app)
10. [What changes for another title, what never does](#10-what-changes-for-another-title-what-never-does)
11. [Licensing posture](#11-licensing-posture)

---

## 1. The artifact chain

**(A)** Every port walks the same chain. Name each stage as a distinct file and keep them all; you will bisect across them.

```
carrier download package  (.zip / .kdp / whatever the phone stored)
  │   contains: a JAR + a text app-descriptor + carrier icons (+ sometimes the previous owner's saves)
  ├─ REPAIR   normalize the descriptor's line endings to LF            ← §3, the "fixed" step
  ├─ RECONCILE  AID == jar basename == the name the loader will open   ← §6
  ├─ STRIP    remove P/* and anything shaped like a save record
  ├─ REPACK   (optional) rewrite in-jar data members                   ← repacking.md
  └─ INSTALL  app/src/main/assets/<archive>.zip, referenced by one Rust const
```

**(B)** Measured on the reference port, one title, four stages. Sizes only — the identifying details are omitted deliberately:

| stage | outer zip | note |
|---|---|---|
| raw carrier dump | 1,804,117 B | descriptor is CRLF, three `P/` save entries present |
| repaired ("fixed") | 1,804,103 B | −14 B — that is 36 CR bytes removed and deflate noise. Nothing else changed |
| saves stripped | 1,802,819 B | three `P/` entries gone |
| one data member rewritten → shipped | 1,800,713 B | inner jar rebuilt; 1273 of 1274 members byte-identical |

The chronology of the reference port's own `work/` directory is a readable bisection log and worth imitating: raw dump → "saves moved to the root" → "saves removed" (both still broken) → "CRLF→LF" (**first bootable**) → final. Four archives, one variable changed at a time. Do that.

**(A) There is no build step that produces the asset.** In the reference port no script copies it, the build script never mentions it, and it is gitignored. A fresh clone therefore builds an APK containing **no game**, silently and successfully. That is the correct licensing property and a real hardening gap: add a build-time existence check (see the `port-shipping` skill) so "I built it and the screen is black" cannot mean "there is no game in the APK".

## 2. Outer archive inventory

**(B)** A KTF outer zip, six entries after stripping (nine before):

```
<AID>.jar        1,877,743 B  deflated   ← the app. Name MUST be "<AID>.jar"
__adf__                592 B  deflated   ← app descriptor, plain text, EUC-KR, LF
__class__              212 B  deflated   ← carrier catalog blob — UNUSED by the emulator
big.icon             9,426 B             ← carrier menu icons — UNUSED
middle.icon          1,324 B             ← UNUSED
small.icon             678 B             ← UNUSED
(stripped) P/<Title>GameSave_0   1472 B  ← previous owner's character
(stripped) P/<Title>SlotSave_0     32 B
(stripped) P/<Title>OptionSave     32 B
```

`P/` is the KTF private-data prefix. On load the emulator adds every archive entry to a virtual filesystem with `P/` trimmed off the front — so `P/Foo` becomes the virtual file `Foo`. That is *not* the database namespace (§7); see the correction in [fault-dictionary.md](fault-dictionary.md#11-f9-bundled-p-saves-do-not-seed-the-database-corrected-claim).

**(A)** Entry **order** is irrelevant to the emulator — the zip is read into a sorted map. Entry **names** are not: three of them are load-bearing (§6). Compression method is free per entry.

## 3. The app descriptor

**(B)** `__adf__`, verbatim shape (values genericized):

```
PID:PD00nnnn
AID:0A1B2C3D
Name:<title, EUC-KR>
Ver:01.00.03
Vdr:<vendor, EUC-KR>
MClass:Clet
...
DisplaySize:240*320
```

**(A) The parser reads exactly three keys.** `AID:`, `PID:`, `MClass:` (`ref: wie_ktf/src/adf.rs`, and the same three-key line scanner again in `wie_lgt/src/emulator.rs`). Everything else in the file — `Name`, `Ver`, `Vdr`, `DisplaySize`, icon names — is carrier metadata that no code path reads. `DisplaySize` is useful to *you* as the guest's native resolution, but the emulator does not consume it; the host passes width and height in explicitly.

The parse is:

```rust
for line in lines {                    // split on b'\n'
    if      line.starts_with(b"AID:")    { aid    = from_utf8_lossy(&line[4..]) }
    else if line.starts_with(b"PID:")    { pid    = from_utf8_lossy(&line[4..]) }
    else if line.starts_with(b"MClass:") { mclass = from_utf8_lossy(&line[7..]) }
}
```

**Fixed index, no trim.** That is the whole CRLF bug: with `\r\n`, `aid == "0A1B2C3D\r"`, `format!("{}.jar", aid)` produces a name that is not in the archive, and the failure surfaces much later as a filesystem `NotFound`.

Per-platform descriptor differences **(A)**:

| platform | descriptor entry | parser | trims? | CRLF-safe? |
|---|---|---|---|---|
| KTF | `__adf__` | three-key line scan | no | **no** |
| LGT | `app_info` | same three-key line scan | no | **no** |
| SKT | `*.msd` | `MIDlet-1:` line + generic `key: value` scan, `.trim()` | yes | yes |
| J2ME | `.jad` / `META-INF/MANIFEST.MF` | MIDP manifest rules | yes | yes |

Encoding: the KTF runtime hard-wires `file.encoding = EUC-KR` (`ref: wie_jvm_support/src/lib.rs`). A non-Korean title needs that changed — it is a one-line constant in the vendored core, and it affects both descriptor text and every string the guest decodes.

## 4. Inner jar inventory — and the one fact that decides the project

**(B)** 1274 members, **all deflated**:

| top dir | n | contents |
|---|---|---|
| `(root)` | 1 | `client.bin64000` — the AOT-compiled ARM image, 735,760 B |
| `META-INF/` | 2 | `MANIFEST.MF`, 71 B: `Manifest-Version` + `Created-By: 1.3.1_14 (Sun Microsystems Inc.)`. **No MIDlet attributes** |
| `dat/` | 252 | game tables — item DBs, enemy/drop/character tables, in-game text |
| ~13 further top-level dirs | ~990 | one per asset class — maps, events, per-actor sprite sets, UI, audio, fonts, logos. Sprites, scripts, palettes, tile maps and MIDI/SMAF audio, distinguished by a two-or-three-letter filename suffix rather than by extension |

**(A) There are no `.class` files.** All Java was AOT-compiled into the single native image. Two consequences, both large:

1. You are emulating an **ARM CPU**, not a JVM. Bytecode tooling does not apply. The `guest-re` skill's static toolkit does.
2. The `META-INF/MANIFEST.MF` without MIDlet attributes is the positive tell that this is **not** a MIDlet even though it is a `.jar`. Do not let the `.jar` extension route you to J2ME.

**(A)** The directory layout above is a *publisher house style*, not a WIPI standard. Expect a completely different tree for another title, and expect suffix conventions (`_bm`, `_scn`, `_pa`, `_mp`, `_mf`) to differ too. Nothing in the loader cares.

**(A)** `META-INF/` contains only `MANIFEST.MF` — no `.SF`, no `.RSA`. **There is no jar signature to invalidate.** Repacking is mechanically safe at the container level; whether the *guest* hashes its own resources is a separate question (§10).

## 5. Platform detection, exactly

**(A)** One dispatcher, replicated verbatim in the CLI, the Android host and the web host (`ref: wie_cli/src/main.rs`, `wie_android/src/emulator.rs`, `wie_web/src/rust/lib.rs`). Keep all copies in the same order; a divergence here produces "works in the probe, not on the phone".

```rust
if filename.ends_with("zip") {
    let files = extract_zip(&buf)?;
    if      KtfEmulator::loadable_archive(&files) { KtfEmulator::from_archive(..) }
    else if LgtEmulator::loadable_archive(&files) { LgtEmulator::from_archive(..) }
    else if SktEmulator::loadable_archive(&files) { SktEmulator::from_archive(..) }
    else { bail!("Unknown archive format") }
} else if filename.ends_with("jad") {
    J2MEEmulator::from_jad_jar(..)
} else if filename.ends_with("jar") {
    if      KtfEmulator::loadable_jar(&buf) { .. }
    else if LgtEmulator::loadable_jar(&buf) { .. }
    else if SktEmulator::loadable_jar(&buf) { .. }
    else { J2MEEmulator::from_jar(..) }          // last-resort fallback, no probe
}
```

The four probes, quoted:

| platform | archive probe | bare-jar probe | source |
|---|---|---|---|
| KTF | `files.contains_key("__adf__")` | first jar entry whose name `starts_with("client.bin")` | `wie_ktf/src/emulator.rs`, `wie_ktf/src/adf.rs` |
| LGT | `files.contains_key("app_info")` | jar contains `binary.mod` | `wie_lgt/src/emulator.rs` |
| SKT | `files.iter().any(|x| x.0.ends_with(".msd"))` | jar starts with `\x20\x00\x00\x00\x00\x00\x00\x00` | `wie_skt/src/emulator.rs` |
| J2ME | — | none; the fallback | `wie_j2me/src/emulator.rs` |

**Presence of a magic filename is the whole of detection.** Nothing sniffs content. Which means: if you synthesize an archive, adding the right magic name is sufficient to route it, and adding the *wrong* one silently routes it to a backend that will fail with a confusing message.

## 6. The four load-bearing names

**(A)** These are the only names that matter. Get all four consistent and a KTF archive boots.

### AID — the jar lookup key

`jar_filename = format!("{}.jar", adf.aid)`. The runtime then sets `java.class.path = "rt.rustjar:net.wie.rustjar:<AID>.jar"` and does `new JarFile("<AID>.jar")` against the virtual filesystem, **by exact name**. Rename the AID and the jar entry together and it still boots; rename either alone and you get `NotFound`. The AID is otherwise inert — it carries no semantics.

**(B) You cannot derive the AID from anything else in the descriptor.** In the reference port's second title, the icon filenames embedded in the descriptor use a *different* 8-hex prefix from the AID. Cosmetic (the emulator ignores icons) but it disproves the tempting shortcut of reading the AID off an icon name.

### PID — the save namespace, permanent

`System::new(platform, pid, aid, ..)`. The on-disk database path is `<root>/<pid>/db/<name>/<record>`.

**(B)** A probe run against a fresh root created `work/db/PD00nnnn/db/<SaveName>/1`.

**Change the PID after release and every existing user's saves become unreachable.** There is no migration path in the loader. Choose it once, at step 3, and write it into the port's notes. If you are synthesizing a descriptor from nothing, this is the single decision you cannot revisit.

Note the DB name can contain path-ish characters — the reference port observed a record named `./OptionSave`, with the leading `./` intact. Your filesystem layer must sanitise ids and reject traversal without *changing* the effective path, or the probe's DB and the device's DB will diverge.

### MClass — the entry point, unobtainable elsewhere

`classLoader.loadClass(mclass.replace('.', "/"))`, then `org/kwis/msp/lcdui/Main.main([mclass])`. **(B)** Both reference-port titles used `Clet`. There is no way to recover the MClass from the jar for an AOT title — there are no `.class` files to enumerate — so a bare jar with no descriptor cannot boot: `FatalError("Main class not found")`. If you must guess, guess the platform's conventional applet class name first, then look for the class-name string inside the native image with the `guest-re` string tools.

### `client.bin<N>` — the BSS size, in the filename

The class loader finds the first jar entry starting with `client.bin`, then parses the **remaining characters as an integer** and uses it as the BSS size:

```
bss_size = parse_bss_size("client.bin64000") = 64000
core.load(data, IMAGE_BASE = 0x100000, len + bss_size)
run_function(IMAGE_BASE + 1, [bss_size])     // guest self-relocates; RWPI
```

**(A)** Renaming the native member to a tidy `client.bin` is a natural instinct and it is fatal — see [fault-dictionary.md](fault-dictionary.md#6-f4-native-member-renamed-bss-unparseable). The digits are data, not decoration. `IMAGE_BASE + 1` sets the Thumb bit; that is the guest's self-relocation entry, and after it runs the image is position-adjusted in place.

## 7. The KTF boot trace — what reads what, in order

**(A)** Memorise this ordering; it is what lets you convert a symptom into a stage.

```
KtfEmulator::from_archive                      wie_ktf/src/emulator.rs
  files["__adf__"] -> KtfAdf { aid, pid, mclass }        ← §3. Missing => "not a KTF archive"
  jar_filename = "<AID>.jar"
  load():
    for (path, data) in files:
        filesystem.add_virtual(path.trim_start_matches("P/"), data)   ← "P/" stripped here
    System::new(platform, pid, aid, ..)                  ← PID becomes the save namespace
KtfJvmSupport::init                            wie_ktf/src/runtime/java/jvm_support.rs
    java.class.path = "rt.rustjar:net.wie.rustjar:<AID>.jar"
    file.encoding   = "EUC-KR"
    new JarFile("<AID>.jar")                             ← EXACT NAME. Mismatch => NotFound panic
    scan jar entries for the first starting with "client.bin"
KtfClassLoader::<init>                         .../classes/net/wie/ktf_class_loader.rs
    getResourceAsStream("client.bin<N>") -> load_native(..)
load_native                                    wie_ktf/src/runtime/init.rs
    bss_size = parse_bss_size("client.bin<N>")           ← unparseable => FatalError
    core.load(data, IMAGE_BASE = 0x100000, len + bss_size)
    install_binary_patches(core, data, [(0x100000, len)]) ← md5-keyed, see §10
    run_function(IMAGE_BASE + 1, [bss_size])             ← RWPI self-relocation
KtfEmulator::start                             wie_ktf/src/emulator.rs
    classLoader.loadClass(MClass.replace('.', "/"))      ← wrong MClass => unwrap on None
    org/kwis/msp/lcdui/Main.main([mclass])
```

**The `loaded` boundary.** Construction (`from_archive`) is synchronous and its failures are clean, typed errors printed at load time. Everything from `load()` onward runs on the async boot task, after the host has already reported that the emulator loaded. So: *clean error before `loaded`* = descriptor/detection; *panic after `loaded`* = names, resources, or the guest itself. This is the fastest triage question available and [fault-dictionary.md](fault-dictionary.md) is sorted by it.

**Two namespaces, do not conflate them.**

- *Virtual filesystem* — every archive entry, `P/` trimmed. Backs generic file I/O.
- *Classloader resources* — members of `<AID>.jar` only, resolved over `java.class.path` by `URLClassLoader.findResource`. Backs `getResourceAsStream`, and therefore backs the packaged-database read path.

A `P/` entry is in the first and not the second. That is exactly why bundled saves do not seed the database.

## 8. What the emulator never reads

**(B)** Verified by grepping the entire emulator tree, the host and the probe for `__class__` and `.icon`: **zero hits**.

- `__class__` — carrier catalog blob.
- `big.icon` / `middle.icon` / `small.icon` — carrier menu artwork.
- Every descriptor key except `AID`, `PID`, `MClass`.

Deleting all of them still boots (see [minimal-archive.md](minimal-archive.md)). Keep them anyway in your working copy unless you are bisecting — they cost nothing and they are evidence about the original package. Do **not** ship them anywhere public: the icons are the publisher's artwork.

## 9. Where the archive lives in the app

**(A)** One constant, one read, no build step:

```
android_app/app/src/main/assets/<archive>.zip
  └ Gradle packages assets/ wholesale — no `androidResources { noCompress }` needed.
    (B) measured in a release APK: the zip is stored with method=deflate and shrinks 118 B. Harmless.
  └ Rust reads it at boot through AAssetManager into a Vec<u8>, whole file in RAM
      const BUNDLED_GAME_ASSET = "<archive>.zip";     ← wie_android/src/lib.rs
      read_asset(&app, BUNDLED_GAME_ASSET)
      asset.read_to_end(..)
  └ extract_zip(bytes) -> BTreeMap<String, Vec<u8>>  -> KtfEmulator::loadable_archive(..)
```

Because the whole archive is held in RAM, archive size is an Android memory cost, not just a download cost. **(B)** ~1.8 MB was a non-issue; a 50 MB dump would not be.

**(A)** Bundling is a *private-build* option. The default shape a public port should teach is bring-your-own-dump: either a runtime file picker (the reference port's web build does exactly this with a plain `<input type=file>` handing bytes to the emulator constructor; the Android SAF equivalent was still an open TODO) or a documented "drop your dump at `app/src/main/assets/<archive>.zip`" step with a **fail-fast build check**.

## 10. What changes for another title, what never does

**Per-title (all found mechanically, all in step 1–3):**

- AID, PID, MClass, jar basename, the `client.bin<N>` BSS value.
- Resource tree inside the jar and every filename convention in it.
- Data-record layouts, marker bytes, trailer sizes — see [record-formats.md](record-formats.md).
- Text encoding, if the title is not Korean (hard-wired EUC-KR in the vendored core).
- Whether repacking is needed at all.

**Never (safe to rely on):**

- Detection by magic filename: `__adf__` / `app_info` / `*.msd` / MIDlet manifest.
- `IMAGE_BASE = 0x100000`; BSS from the native member's filename; self-relocation at `IMAGE_BASE + 1` with the Thumb bit set.
- AID = jar basename. PID = save namespace. MClass required and unobtainable from anywhere else.
- CRLF in the descriptor = unbootable, for KTF and LGT; harmless for SKT and J2ME.
- Zip entry order and per-entry compression method are free; entry *names* are not.
- No signature or CRC gate on the jar.

**One trap that couples §9 to repacking.** `install_binary_patches` matches the guest image's **md5** against a table shipped with the emulator core. A hash-keyed entry that resolves to zero patches is **fatal**; a hash-less generic entry only warns. **(B)** The reference core shipped exactly one entry, generic. A new title needs no entry at all — but the moment you add a hash-keyed one and then repack or patch the guest binary on disk, the md5 moves and the loader dies loudly. Patch guest memory at runtime instead (that is the `guest-re` rule), and this never bites.

**Still unproven, flagged honestly (B):** whether the guest image verifies a hash of its own jar resources. The reference port never proved it either way; the empirical answer was "a repacked archive boots and loads a save", which is evidence, not proof. If a repacked archive boots into a corruption screen rather than crashing, suspect this first.

## 11. Licensing posture

**(A)** Three overlapping guards, all needed:

1. **Ignore by path** — `app/src/main/assets/*.zip`.
2. **Ignore by name, anywhere, with any suffix** — `<archive>.zip*`. Path rules leak: a repack tool that writes `<archive>.zip.pre-rename` into a backup directory produces the same licensed bytes, outside the guarded path, not ending in `.zip`. The reference port needed a follow-up commit for exactly this.
3. **Ignore the backup directory** wholesale, and keep it **outside** any directory the build packages wholesale — `assets/` is packaged whole, so a backup left there ships inside the APK.

Derived artifacts are equally licensed: the extracted native image, decoded sprite atlases, dumped string tables, any `dat/*` blob. Reverse-engineering *notes* — addresses, offsets, formats — are not content and are fine to publish. **Extracted decryption keys are neither**: publishing a key that decrypts a copyrighted work is a materially different act from publishing an offset. Out of scope here, in tools, in examples, and in defaults.
