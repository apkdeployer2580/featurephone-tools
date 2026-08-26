# Platform matrix — KTF, LGT, SKT, J2ME

What the emulator actually tests to pick a backend, what each platform's descriptor holds, what
the loader does with it, and which chapters of the pipeline change per platform.

Provenance tags: **(A)** generic · **(B)** worked example from the reference port · **(C)** do not
copy.

## Contents

- [1. The detection dispatcher — source-of-truth shape](#1-the-detection-dispatcher--source-of-truth-shape)
- [2. The four probes, exactly](#2-the-four-probes-exactly)
- [3. Descriptor keys per platform](#3-descriptor-keys-per-platform)
- [4. The CRLF trap, and why it is platform-specific](#4-the-crlf-trap-and-why-it-is-platform-specific)
- [5. The three-name agreement rule (AOT platforms)](#5-the-three-name-agreement-rule-aot-platforms)
- [6. Loader constants — where each one comes from](#6-loader-constants--where-each-one-comes-from)
- [7. A second AOT loader shape: the linked-ELF guest](#7-a-second-aot-loader-shape-the-linked-elf-guest)
- [8. Minimal bootable archive](#8-minimal-bootable-archive)
- [9. Fault dictionary, keyed by platform](#9-fault-dictionary-keyed-by-platform)
- [10. What differs per platform](#10-what-differs-per-platform)
- [11. Which chapters change per platform](#11-which-chapters-change-per-platform)
- [12. What is invariant across all four](#12-what-is-invariant-across-all-four)
- [13. Things to verify in *your* fork before trusting this page](#13-things-to-verify-in-your-fork-before-trusting-this-page)

---

## 1. The detection dispatcher — source-of-truth shape

Every wie-family host repeats the same dispatcher, and **the order is load-bearing**: KTF, then
LGT, then SKT, then J2ME as an unprobed last resort. Read your fork's copy rather than trusting
this page; there are typically three copies (CLI, Android host, web host) and they must agree.

```rust
// (A) The shape. Extension decides the family; a magic entry decides the backend.
let emulator: Box<dyn Emulator> = if filename.ends_with("zip") {
    let files = extract_zip(&buf)?;                       // -> BTreeMap<String, Vec<u8>>
    if      KtfEmulator::loadable_archive(&files) { KtfEmulator::from_archive(files, …)? }
    else if LgtEmulator::loadable_archive(&files) { LgtEmulator::from_archive(files, …)? }
    else if SktEmulator::loadable_archive(&files) { SktEmulator::from_archive(files, …)? }
    else { bail!("Unknown archive format") }
} else if filename.ends_with("jad") {
    J2MEEmulator::from_jad_jar(platform, jad, jar_filename, jar)?
} else if filename.ends_with("jar") {
    if      KtfEmulator::loadable_jar(&buf) { … }
    else if LgtEmulator::loadable_jar(&buf) { … }
    else if SktEmulator::loadable_jar(&buf) { … }
    else { J2MEEmulator::from_jar(platform, &filename, buf)? }   // fallback, no probe
};
```

Two consequences worth stating:

- **Entry order inside the zip is irrelevant.** The archive is read into a map, so a repack may
  reorder members freely. Entry *names* are not free.
- **An unknown zip is fatal, by design.** There is no "try J2ME anyway" path for zips. If your
  host's dispatcher leaves `from_jar` unreferenced (some do, to keep an import meaningful),
  **J2ME is not actually wired up in that host** — check before promising a MIDlet will run.

## 2. The four probes, exactly

| platform | archive probe (zip) | bare-jar probe |
|---|---|---|
| **KTF** (WIPI, AOT ARM) | `files.contains_key("__adf__")` | first jar entry whose name `starts_with("client.bin")` exists |
| **LGT** (WIPI) | `files.contains_key("app_info")` | jar contains an entry named `binary.mod` |
| **SKT** (SKVM) | any entry name ends with `.msd` | jar's first 8 bytes are `20 00 00 00 00 00 00 00` |
| **J2ME / MIDP** | — (jad/jar only) | none — last-resort fallback |

That is the entire detection mechanism: **presence of a magic filename**. No header parsing, no
heuristics. `classify_archive.py` implements the same table and additionally reports the
descriptor keys, the CR count, the native member's parsed BSS value, and any name disagreements.

### Classification has three levels, not one

**(A) "Which platform" is only the first question, and the two below it change the shape of the
project just as much:**

| level | question | why it matters |
|---|---|---|
| 1 | **which platform?** | picks the backend — the table above |
| 2 | **which CARRIER BUILD of this same title?** | two carrier packages of one game are **different compiles**. Data layout largely transfers; **code addresses transfer at zero** |
| 3 | **which REVISION within that carrier?** | one carrier routinely shipped two revisions. **(B)** In the reference pair the `.text` lengths differ by 0x144 bytes and functions are relocated by **+0x74** — enough that a hash-keyed hook table matches neither |

**(A) The operational rule for any new dump: hash the native member FIRST, and look the hash up.**
Not because the hash is interesting, but because everything downstream is keyed on it — a hash-keyed
patch or hook table **silently falls back to its generic entries** when the hash is unknown, and the
game then runs correctly *without its hooks*. The tell is a **boot log line** naming how many entries
installed, not a performance gauge.

⚠ **(A) The in-game splash version is NOT the package version.** They are two independent numbering
schemes and they disagree. **(B)** In the reference pair, one build's descriptor says `01.00.07`
while its splash screen says `VER 1.0.4`; the other pair is `01.00.04` / `VER 1.0.1`.

> **REFUTED.** *Previously believed, and written into a comment in the reference port's own patch
> table:* that the build whose splash reads like the higher version was the one being shipped.
> *Refuted by:* hashing the native member that the shipping archive actually contains.
> *Why the wrong reading was attractive:* both numbers are four digits, both look like versions,
> and the one you see while playing is the splash. Nothing in either artefact says which scheme it
> belongs to.
> *Consequence if carried forward:* every address in the notes is attributed to the wrong revision,
> and the hooks you "verified" were never installed on the build you shipped.

Deriving a second build's values from the first, and the table architecture that carries both:
[guest-re/reference/multi-build.md](../../guest-re/reference/multi-build.md) and
[guest-re/reference/build-keyed-anchors.md](../../guest-re/reference/build-keyed-anchors.md).

## 3. Descriptor keys per platform

### KTF — `__adf__`, plain text

Parsed by a **three-key line scanner** that splits on `\n` and slices from a fixed index with
**no trim**:

```
PID:<save-namespace id>        <- becomes the on-device save directory. Permanent after release.
AID:<application id>           <- MUST equal the inner jar's basename
Name:<display name>            <- ignored by the emulator
Ver:<version>                  <- ignored
Vdr:<vendor>                   <- ignored
MClass:<main class>            <- loaded at start; obtainable from nowhere else
DisplaySize:<W>*<H>            <- informational; the host picks the real framebuffer size
```

Only `PID`, `AID` and `MClass` are read. Everything else — display name, vendor, version, the
icon filenames — is decoration. **(B)** In one measured dump the icon filename prefix inside the
descriptor did *not* match the AID, which is harmless but proves you cannot derive AID from
neighbouring fields; read it, do not infer it.

**Other entries in a KTF archive are read by nobody:** a carrier catalog blob and two or three
menu icon files typically ride along. Deleting them changes nothing.

### LGT — `app_info`, plain text

Same three-key scanner, same `AID` / `PID` / `MClass` semantics, same no-trim slicing. The native
member is `binary.mod` rather than `client.bin<N>`, so **the BSS size is not in the filename** —
and it does not need to be, because that member is a **linked ELF** and the loader reads its section
table. That replaces the whole four-constant model; see §7.

### SKT — `*.msd`, plain text

Parsed as a `MIDlet-1:` line plus a generic `key: value` property scan, and **it calls `.trim()`
on values**. That single difference is why the CRLF fault of §4 hits KTF and LGT but not SKT.

### J2ME / MIDP — `.jad` file and/or `META-INF/MANIFEST.MF`

Standard MIDP attributes: `MIDlet-Name`, `MIDlet-Vendor`, `MIDlet-Version`, `MIDlet-Jar-URL`,
`MIDlet-Jar-Size`, and one or more `MIDlet-<n>: <label>,<icon>,<class>`. The third field of
`MIDlet-1` is the entry class — the analogue of `MClass`.

**The manifest is also the AOT-vs-bytecode tell.** A manifest carrying only `Manifest-Version`
and `Created-By`, with no MIDlet attributes, is the signature of a compiled WIPI title rather
than a MIDlet.

## 4. The CRLF trap, and why it is platform-specific

**(A) The single most transferable archive finding: for KTF and LGT, "repairing" a dump usually
means exactly one thing — converting the descriptor's CRLF line endings to LF.**

Mechanism: the scanner splits on `b'\n'` and takes `&line[4..]` (or `&line[7..]`) with no trim.
With CRLF the AID becomes `"<id>\r"`, the derived jar name becomes `"<id>\r.jar"`, and the
virtual filesystem has no such entry.

Symptom **(B, reproduced)**: the emulator prints `loaded` **first**, then panics later, on the
async boot task, in the Java runtime's file layer with `NotFound`. On a phone that surfaces as a
panic line in the log and a black screen — not as a load error. The `loaded`-then-panic ordering
is what distinguishes chapter 2 from chapter 4.

Fix: `descriptor.replace(b"\r\n", b"\n")`. Verify with a CR count of zero. **(B)** Two different
dumps from the same publisher both needed precisely this and nothing else.

**SKT is immune** because its property parser trims. **J2ME manifests** are line-oriented with
their own continuation rules; use a real manifest parser rather than a line scanner.

## 5. The three-name agreement rule (AOT platforms)

```
AID:<X>              in the descriptor
<X>.jar              an entry in the outer zip          <- must match exactly, byte for byte
client.bin<digits>   at the jar root (KTF)              <- digits are the BSS size, load-bearing
MClass:<Y>           a class the AOT image can resolve
```

- **AID is only the jar lookup key.** Rename the AID and the jar together and it still boots.
- **PID is the save namespace.** The on-device database path is `<root>/<pid>/db/<name>/<record>`.
  Change PID after release and every user's saves become unreachable. Choose once.
- **The BSS size is in the filename.** `client.bin64000` means 64,000 bytes of zeroed statics are
  appended after the loaded image. Two consequences: renaming the member to `client.bin` is fatal
  at load, and **a guest address past the end of the file is not automatically invalid** — the
  writable statics live there.
- **`MClass` has no other source.** A bare KTF jar with no descriptor loads and then dies with
  `Main class not found`. If the descriptor is missing, synthesise one — **(B)** a hand-written
  three-line descriptor (`PID:` / `AID:` / `MClass:`) booted a real title identically.

## 6. Loader constants — where each one comes from

For AOT platforms the KTF boot chain is the reference shape; read your fork's equivalent.

```
from_archive:
  descriptor -> { aid, pid, mclass }
  jar name    = "<aid>.jar"
  every archive entry is added to the virtual filesystem, with the private-data prefix stripped
  System::new(platform, pid, aid, …)                 <- PID becomes the save namespace
jvm init:
  java.class.path = "<runtime jars>:<aid>.jar"
  file.encoding   = "<hard-wired text encoding>"     <- see the warning below
  new JarFile("<aid>.jar")                           <- resolves by EXACT name
  scan entries for the first name starting with "client.bin"
class loader:
  bss_size = parse_bss_size("client.bin<N>")         <- from the FILENAME
  core.load(data, IMAGE_BASE, data.len() + bss_size) <- IMAGE_BASE is the loader's constant
  install_binary_patches(core, data, …)              <- md5-keyed table, see below
  run_function(IMAGE_BASE + 1, [bss_size])           <- +1 is the Thumb bit; the guest SELF-RELOCATES
start:
  classLoader.loadClass(mclass.replace('.', "/"))
  <platform main>.main([mclass])
```

The four constants a static toolchain needs, and their sources **(A)**:

| constant | source |
|---|---|
| `image_base` | **flat image:** the loader crate's `load(data, BASE, …)` call — a property of the *loader*, not of the binary. **Linked ELF:** there is none; every section carries its own `sh_addr` (§7). |
| `bss_size` | **flat image:** the native member's filename suffix (KTF). **Linked ELF:** the `.bss` **section header** (§7). |
| `sl` (RWPI static base) | a register dump at any fault, cross-checked against the entry thunk's `ldr / mov sl / add sl,pc` prologue and against a static-area dump that decodes as strings and function pointers. Use ≥2 routes. |
| `reloc_delta` | solved by intersecting `{known_string_offset − stored_word}` across ≥5 targets, then validated corpus-wide. **Determine whether it yields a file offset or a guest address.** |

⚠ **The text encoding is hard-wired in the JVM-support crate** (`file.encoding`). A title from a
different locale needs that changed, and every string tool in the pipeline needs the matching
byte class. This is a one-line fork edit that is easy to overlook and produces mojibake
everywhere downstream.

⚠ **`install_binary_patches` is keyed on the guest binary's md5.** A hash-keyed entry that
matches but produces zero patches is **fatal**; a hash-less generic entry only warns. A new title
usually needs no entry — but if you add a hash-keyed one, it dies loudly the moment you repack
the native member and the hash moves.

## 7. A second AOT loader shape: the linked-ELF guest

**(A)** §6 describes a **flat image**: one blob, loaded at a constant the loader chooses, with a BSS
appended and the guest self-relocating on entry. That is not the only AOT shape in this family, and
the other one is easy to miss because the container and the descriptor look identical.

**(A) Check the native member's first bytes before you go looking for an image base.** If it is a
**linked executable** (ELF here — `ET_EXEC`, 32-bit ARM), then:

| flat image (§6) | linked ELF |
|---|---|
| one `image_base` constant, from the loader | **no image base** — the loader maps every section with a non-zero `sh_addr` at its own address, using `sh_size` |
| BSS size from the filename | **BSS from the `.bss` section header** |
| entry = `image_base + 1` (Thumb bit) | entry = the header's **`e_entry`** |
| guest **self-relocates** on entry | **no rebase step at all** — the addresses in the image are the addresses at runtime |
| `reloc_delta` solves stored-word → address | **no delta**; a stored word *is* an address |

**(A) The practical consequence for your notes:** the four-constant address model of chapter 7 is
replaced by "read the section table", and it is *more* reliable, not less — the sections are
self-describing, so `.text`, `.data` and `.bss` bounds come from the file rather than from a
derivation you have to validate corpus-wide. Read them with any ELF-aware tool; a static toolkit
that assumes a linear `.text`-only mapping will silently mis-address everything outside `.text`.

**(A) And it hands you something the flat shape does not: provable scratch space.** Loaders map
page-rounded, so the gap between the declared end of `.bss` and the end of its last page is **mapped
memory that belongs to no section** — nothing in the guest can be using it, because nothing in the
guest knows it exists. **(B)** In the reference title the declared `.bss` end and the page end left a
usable tail that was adopted as the host's out-parameter scratch after **three independent proofs**
that no guest code addressed it. Ownership of that argument, and what it is used for, belongs to
[guest-re/reference/calling-guest-code.md](../../guest-re/reference/calling-guest-code.md); the
liveness argument you must construct first belongs to
[guest-re/reference/patching.md](../../guest-re/reference/patching.md).

⚠ **Verify all of this in your own fork** (§13): read the loader's initialisation path and confirm
whether it walks section headers or takes a constant. Both shapes exist behind the same descriptor
format, and nothing in the archive announces which one you have.

## 8. Minimal bootable archive

**(B, empirically proven for KTF):** `__adf__` + `<AID>.jar`. Nothing else. The carrier catalog
blob and the icon entries are read by nobody — a grep of the whole emulator tree for their names
returns zero hits. A three-line synthesised descriptor works. Booting the two-entry archive
produced a normal `loaded` line and a normal work gauge.

Use this as a bisection tool: if the full dump fails and the two-entry archive boots, the fault
is in something you removed.

## 9. Fault dictionary, keyed by platform

| you did this | symptom | platform |
|---|---|---|
| descriptor has CRLF, **or** AID ≠ jar basename | `loaded`, then a `NotFound` panic in the Java runtime's file layer | KTF, LGT |
| `MClass` wrong or misspelled | a null unwrap out of `loadClass` | KTF, LGT |
| no descriptor entry in the zip | clean `not a <platform> archive` at load time | all zip platforms |
| native member renamed to drop the digits | `Invalid bss_size in filename …: cannot parse integer from empty string` | KTF |
| bare KTF jar with no descriptor | loads, then `Main class not found` | KTF |
| native member missing entirely | `client.bin* not found in jar` | KTF |
| repacked the native member with a hash-keyed patch entry present | `entry X: hash matched but produced 0 patches/hooks` (fatal) | all AOT |
| the guest asks for an interface name the loader does not know | a warning plus a **null pointer**, and a crash much later, far from the cause. Grep the log for unknown-interface warnings on every new title. | all AOT |
| carried code addresses over from the other **carrier build** of the same title | plausible wrong values, or a crash at a plainly valid-looking address. Nothing reports a mismatch | all AOT |
| the dump is a different **revision** of the carrier build your hook table is keyed to | the game runs **correctly and without its hooks** — a silent fallback to generic entries. The tell is the installer's own boot line, e.g. `Installed 1 hooks` where you expected more | all AOT |
| assumed a flat image on a **linked-ELF** guest | every address outside `.text` is wrong, consistently (§7) | AOT, ELF-shaped |

## 10. What differs per platform

| | KTF | LGT | SKT | J2ME |
|---|---|---|---|---|
| magic entry | `__adf__` | `app_info` | `*.msd` | `.jad` / manifest |
| guest form | AOT ARM | AOT ARM | SKVM | JVM bytecode |
| native member | `client.bin<N>` | `binary.mod` | — | — |
| image shape | flat blob | **linked ELF** (§7) | n/a | n/a |
| BSS source | filename suffix | **`.bss` section header** | n/a | n/a |
| entry point | `image_base + 1` (Thumb bit) | **`e_entry`** | n/a | n/a |
| guest self-rebase on entry | yes | **no** — addresses are already final | n/a | n/a |
| descriptor parser | 3-key, **no trim** | 3-key, **no trim** | property scan, **trims** | manifest attributes |
| entry point key | `MClass` | `MClass` | `MIDlet-1` field 3 | `MIDlet-1` field 3 |
| save namespace | `PID` | `PID` | suite identity | suite identity |
| CRLF hazard | **yes** | **yes** | no | parser-dependent |
| needs `guest-re`'s ARM toolkit | yes | yes | partly | **no** |

## 11. Which chapters change per platform

| chapter | KTF | LGT | SKT | J2ME |
|---|---|---|---|---|
| 1 Classify | same tool, different magic entry | same | same | jad/jar pair |
| 1b Which build/revision | hash the native member; two carrier builds share no code addresses | same | same | same question, class files |
| 2 Repair | CRLF + three-name rule | CRLF + name rule, BSS differs | no CRLF step | manifest sanity only |
| 3–6 Core | identical — these are emulator defects, not title bugs | identical | identical | identical |
| 7 Address model | full four constants | **section table instead** — no image base, no delta, no self-rebase (§7) | verify against your fork's loader | **skip** — no image base, no delta |
| 8 Strings | image-wide byte-class scan | same | same | constant-pool scan |
| 9 Offsets | struct offsets, two derivations | same | same | field names; keep the rule for semantics |
| 10–12 Device | identical | identical | identical | identical |
| 13 Patch/call | Thumb halfword rewrites | same | verify the ISA | bytecode/interception |
| 14 Verify | identical | identical | identical | identical |
| 15–17 Network | socket slots of the WIPI C API | same | SKVM equivalent | MIDP `Connector` / `SocketConnection` |
| 18–19 Ship | identical | identical | identical | identical |

## 12. What is invariant across all four

Safe to bake into tooling **(A)**:

- Detection is by magic filename; the dispatcher order is KTF → LGT → SKT → J2ME.
- Zip **entry order and compression method are free**; entry **names** are not.
- There is no signature or CRC gate on the inner jar in these formats — a repacked jar that boots
  is itself the proof. (Whether the *game* hashes its own resources is a separate question and is
  usually unproven; treat it as unknown until measured.)
- Whatever the platform, the emulator loads the archive into memory; nothing is mmapped, so byte
  alignment inside the zip is irrelevant.
- Private-data entries in the archive (the `P/` prefix on WIPI platforms) belong to whoever
  dumped it. Strip them.
- The build must **fail loudly** when the archive asset is absent, or you ship an app with no
  game and a perfectly clean build log. Presence is not correctness: a member that is present and
  **stale** needs a byte-comparing check as well
  ([archive-prep/reference/generated-assets.md](../../archive-prep/reference/generated-assets.md)).
- **Identify the build before you trust any address.** Hash the native member; a hash-keyed hook
  table falls back silently, and the fallback runs the game correctly with none of your work in it.

## 13. Things to verify in *your* fork before trusting this page

Forks drift, and this page is a description of one lineage. Check these six before relying on
them, and record what you find next to the constant:

1. The dispatcher's probe list and its **order** — in every host that has a copy.
2. Whether `J2MEEmulator::from_jar` is actually reachable from your host, or only imported.
3. The loader's `IMAGE_BASE` literal and the exact `load(...)` arithmetic — **or**, for a
   linked-ELF guest, the loop that walks section headers and maps each `sh_addr` / `sh_size` (§7).
4. Where the BSS size comes from on non-KTF AOT platforms. **(B)** On the reference LGT lineage the
   answer is the `.bss` section header, and the four-constant model does not apply at all — but that
   is one fork's loader, so read yours.
5. The hard-wired `file.encoding` and whether it matches your title's locale.
6. Whether the hook/patch installer is keyed on a hash of the native member, and **what it logs when
   the hash is unknown**. A silent generic fallback is the failure this page most wants you to avoid.
