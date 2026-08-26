# Fault dictionary — archive preparation

F1–F10 were each **reproduced** on a real title by deliberately breaking one thing in a known-good archive and running the headless probe; panic texts are quoted from those runs. F11–F13 arrived the other way round — they were **shipped**, in real builds, and are written up here so that nobody has to pay for them twice.

Tags: **(A)** generic · **(B)** worked example from the reference port · **(C)** do not copy.

## Contents

1. [Triage in one question](#1-triage-in-one-question)
2. [The table](#2-the-table)
3. [F1 CRLF in the descriptor, or AID does not match the jar basename](#3-f1-crlf-in-the-descriptor-or-aid-does-not-match-the-jar-basename)
4. [F2 Wrong or misspelled MClass](#4-f2-wrong-or-misspelled-mclass)
5. [F3 No descriptor in the archive](#5-f3-no-descriptor-in-the-archive)
6. [F4 Native member renamed, BSS unparseable](#6-f4-native-member-renamed-bss-unparseable)
7. [F5 Bare jar with no descriptor](#7-f5-bare-jar-with-no-descriptor)
8. [F6 Loads, then a permanently black screen with no error](#8-f6-loads-then-a-permanently-black-screen-with-no-error)
9. [F7 Repack refuses on the second run](#9-f7-repack-refuses-on-the-second-run)
10. [F8 Fatal binary-patch failure after touching the guest image](#10-f8-fatal-binary-patch-failure-after-touching-the-guest-image)
11. [F9 Bundled P/ saves do not seed the database (corrected claim)](#11-f9-bundled-p-saves-do-not-seed-the-database-corrected-claim)
12. [F10 How all of this looks on a phone](#12-f10-how-all-of-this-looks-on-a-phone)
13. [F11 Black screen shortly after the title, allocation failure with a negative size](#13-f11-black-screen-shortly-after-the-title-allocation-failure-with-a-negative-size)
14. [F12 The change you committed is not in the artifact](#14-f12-the-change-you-committed-is-not-in-the-artifact)
15. [F13 One field is blank for some records, different ones each session](#15-f13-one-field-is-blank-for-some-records-different-ones-each-session)
16. [How to reproduce a fault deliberately](#16-how-to-reproduce-a-fault-deliberately)

---

## 1. Triage in one question

**Did the host print `loaded` before it died?**

- **Before `loaded`** — the failure is in synchronous construction: platform detection or descriptor parsing. Clean typed error, no panic. Faults F3, F5(partly).
- **After `loaded`** — the failure is on the async boot task: name resolution, resource lookup, class loading, or the guest itself. Presents as a **panic**, and on a phone as a silent black screen. Faults F1, F2, F4, F5, F6, F8.

Nothing else discriminates as cheaply. A user reporting "it just doesn't work" should be asked for the line immediately before the failure, not for the whole log.

**(A) There is a third band, and it is the one that ships:** the failure arrives **long after `loaded`**, past the title screen, once the guest reads a table that no longer matches what its code expects. Nothing in the boot path can see it — which is why F11 and F12 exist and why they need a byte-comparing gate rather than a smoke test.

## 2. The table

| # | You did this | Fires | Exact text |
|---|---|---|---|
| F1 | descriptor has CRLF, **or** `AID` ≠ jar basename | after `loaded` | panicked at `java_runtime/src/classes/java/io/file.rs:99:50` — called `Result::unwrap()` on an `Err` value: `NotFound` |
| F2 | `MClass:` wrong or misspelled | after `loaded` | panicked at `jvm/src/value.rs:114` — called `Option::unwrap()` on a `None` value |
| F3 | no `__adf__` in the zip | **at load time, clean** | `[probe] not a KTF archive` / from the generic dispatcher, `Unknown archive format` |
| F4 | native member renamed `client.bin<N>` → `client.bin` | after `loaded` | panicked at `ktf_class_loader.rs:94` — `FatalError("Invalid bss_size in filename client.bin: cannot parse integer from empty string")` |
| F5 | KTF **bare jar**, no descriptor anywhere | after `loaded` | `WieError::FatalError("Main class not found")` |
| F6 | forgot the virtual clock (`--ms`) | never — it just runs | `[probe] loaded`, work gauge advances, every dumped frame is 100 % black |
| F7 | re-ran a repack on an already-patched archive, **or** the pristine copy is gone and the tool is reading its own output | at repack time | `changed records {..} != expected {..}`, or a record-count refusal such as `expected N records, got N+1` |
| F8 | repacked or patched the guest image on disk while a hash-keyed patch entry exists | after `loaded` | fatal from `install_binary_patches` — hash matched no entry / entry produced zero patches |
| F9 | (not a fault) bundled `P/` saves | — | no effect on the user database; strip them for privacy, not for behaviour |
| F11 | a generated/patched data member and a guest constant disagree about the table's size | **after** the title screen | an allocation failure with a **negative** size, from inside the guest's own event loop; on a phone, "it starts and then stops on a black screen" |
| F12 | the archive is gitignored, so your asset edit never reached this checkout | never — everything reports success | nothing. The commit exists; the member is pristine. Only a byte-comparing `--check` says so |
| F13 | you deleted a byte the guest's *length* scan terminates on | at draw time, intermittently | nothing in any log. One field draws empty for some records, different ones per session |

## 3. F1 CRLF in the descriptor, or AID does not match the jar basename

**These are the same fault** because CRLF *is* an AID mismatch: the parser slices `line[4..]` from a `\n`-split line with no trim, so `AID:0A1B2C3D\r\n` yields `aid == "0A1B2C3D\r"`, and `format!("{}.jar", aid)` asks the virtual filesystem for `0A1B2C3D\r.jar`.

**(B) Reproduced.** Take a known-good shipped archive, change only the descriptor's LF back to CRLF, change nothing else:

```
[probe] loaded
thread 'main' panicked at .../java_runtime/src/classes/java/io/file.rs:99:50:
called `Result::unwrap()` on an `Err` value: NotFound
```

**(B) Measured on the real dump.** Original descriptor 628 B, `CR=36 LF=36`. Repaired 592 B, `CR=0 LF=36`. And `original.replace(b'\r\n', b'\n') == repaired` → `True`. **"Fixed" meant exactly that and nothing else.** The same held for the second title in the same series: 704 B `CR=36` → 668 B `CR=0`, byte-identical under the same transform. The very first action on that title was the same repair.

**(B) Device confirmation.** The reference port's deploy notes record the same thing measured on hardware: a single stray `\r` in the descriptor and the game does not boot.

**Fix.**

```python
adf = z.read('__adf__')
cr = adf.count(b'\r')
assert cr == 0, "%d CR bytes in the descriptor" % cr
adf = adf.replace(b'\r\n', b'\n').replace(b'\r', b'\n')   # lone CR too, some dumps are ancient
```

Also check the flip side of the same fault: the descriptor may be clean LF and the **jar entry** may simply be named something else (a repacker that "tidied" the name, a dump where the AID and the jar disagree in the original). Compare the two strings byte-for-byte, do not eyeball them — trailing whitespace and case both bite.

**Why the panic is so far from the cause.** The descriptor parse succeeds; the archive loads; the virtual filesystem is populated; `System` is constructed; the JVM comes up; `java.class.path` is set — and only then does `new JarFile("<AID>.jar")` miss. Six stages of apparent success between the mistake and the symptom. That distance is the reason to make step 2 a hard gate rather than something you check when things go wrong.

## 4. F2 Wrong or misspelled MClass

`classLoader.loadClass(mclass.replace('.', "/"))` returns null, which is unwrapped.

```
thread 'main' panicked at .../jvm/src/value.rs:114:
called `Option::unwrap()` on a `None` value
```

**(A)** Distinguishing F2 from F1: F1 fails in `java/io/file.rs` (a *file* is missing), F2 in `jvm/src/value.rs` (a *class* is missing). If you see F2 and you did not touch the descriptor, suspect that the guest image failed to relocate — a mangled native image produces a class registry the loader cannot query, with the same symptom.

**Fix.** Restore the original `MClass` string. If the descriptor is synthesized, the class name has to come from the guest image: dump strings from the native binary and look for a short class-name-shaped literal near the platform's own applet-class strings. **(B)** Both titles examined used the conventional applet class name for the platform.

## 5. F3 No descriptor in the archive

The cleanest failure in the whole pipeline, and the only one that happens before `loaded`:

```
[probe] not a KTF archive
```

or from the generic dispatcher, after all three archive probes miss:

```
Unknown archive format
```

**Fix, in order of preference:** (1) find the descriptor — some dumps store it beside the zip rather than inside it; (2) synthesize one, three lines, see [minimal-archive.md](minimal-archive.md); (3) if you meant LGT or SKT, check you are not looking for the wrong magic name.

**(A) Diagnostic value.** Because this fault is clean and early, it is also the cheapest *positive* test that your archive is being routed to the backend you think it is. If you expect KTF and you get `Unknown archive format`, the magic entry is missing or misnamed — check for a leading directory component (`package/__adf__` does **not** match `__adf__`; the probe is an exact key lookup on the zip's entry names).

## 6. F4 Native member renamed, BSS unparseable

```
thread 'main' panicked at .../ktf_class_loader.rs:94:
FatalError("Invalid bss_size in filename client.bin: cannot parse integer from empty string")
```

**(A) The digits in `client.bin64000` are the BSS size in bytes.** The loader takes the first jar entry starting with `client.bin`, parses the remainder as an integer, and allocates `len + bss_size`. Renaming the member to a "clean" `client.bin` deletes the number and the parse fails on an empty string — hence the very specific error text.

Subtler variants of the same fault, all worth checking:

- The member is at `bin/client.bin64000` instead of the jar root. The scan looks at entry names starting with `client.bin`, so a directory prefix makes it invisible → you get F5 or a null-class F2 instead.
- Two entries start with `client.bin`. The **first** wins, in the jar's own iteration order. Never leave a backup copy inside the jar.
- The digits were "corrected" to a rounder number. The BSS is a real allocation size the guest depends on; a too-small value corrupts guest memory in ways that look like random guest bugs far downstream.

**Fix.** Restore the original member name exactly, including the digits.

## 7. F5 Bare jar with no descriptor

A KTF bare `.jar` is *detected* (the `client.bin*` probe hits) and it loads. Then:

```
WieError::FatalError("Main class not found")
```

**(A) There is no other source for MClass.** For an AOT title there are no `.class` files to enumerate and no manifest attributes. The descriptor is the only carrier of the entry point. This is the fault that forces you to synthesize a descriptor rather than shipping the bare jar.

Note the asymmetry with J2ME: a MIDlet jar carries `MIDlet-1:` in its own manifest, so a bare MIDlet jar is self-describing and this fault does not exist there.

## 8. F6 Loads, then a permanently black screen with no error

Not an archive fault, but it is the most common misdiagnosis *of* an archive fault, so it belongs here.

```
[probe] loaded
[work] f=100 instr=185823685 (1.9 M/frame) ... virt 0.64x
```

...and every dumped frame is entirely black. **(A) The guest idles on a platform timer.** If the host's `now()` is not advanced by the frame loop — because you forgot `--ms`, or because the core still uses a wall-clock `now()` — the guest's timers never fire and it never draws. The archive is fine.

**Fix here:** pass `--ms 20`. **Fix in the core:** the frame-driven virtual clock, which is failure #1 in the `emulator-core` skill. If `--ms` produces a picture and the phone does not, the archive is exonerated and the problem is host-side.

**(B)** A healthy run looks like the gauge above: instruction count climbing, a plausible instructions-per-frame figure, and a virtual-time ratio under 1.0. A gauge that advances with a black screen is F6; a gauge that does not advance at all is a hung guest, which is a `guest-re` problem.

## 9. F7 Repack refuses on the second run

**(A) A good repack tool is deliberately not idempotent.** It verifies that the set of *changed* records exactly equals the set it intended to change. Re-run it on its own output and the records are already changed, so the intended set is empty, so the check fires:

```
changed records set() != expected {17, 18, 24}
```

This is correct behaviour for an in-place transform and you should build it into one. The alternative — a tool that silently no-ops or, worse, applies the transform twice to a length-prefixed field — is how archives get quietly corrupted.

**Fix.** Restore from the backup the tool wrote on its first `--apply` and re-run from the original. That is what the backup rule in [repacking.md §6](repacking.md#6-backups-idempotence-and-the-apply-gate) is for.

### The second cause, which is more dangerous — and the discriminator

**(A)** A transform that always rebuilds from a **pristine** copy *is* idempotent, so on that pipeline the refusal above cannot happen. A refusal there means something else entirely:

```
expected 15 records, got 16 — refusing
```

**The pristine copy has been deleted, so the tool is reading its own previous output as its source.** The message points at the record count — i.e. at the data model — and sends you to debug the parser instead of restoring a file. **(B)** In the reference port this happened when a by-product was cleaned out of the packaged assets directory by deleting it rather than moving it.

> **The discriminator is one question: does a pristine copy still exist?**
> Yes → you have an in-place tool run twice; restore and re-run.
> No → recover the pristine artifact (from your out-of-repo backup, or by re-preparing the original dump) before touching the parser.

Both causes are why the pristine copy is **moved, never deleted** ([repacking.md §6](repacking.md#6-backups-idempotence-and-the-apply-gate)).

## 10. F8 Fatal binary-patch failure after touching the guest image

The core's `install_binary_patches` keys its patch table on the **md5 of the loaded guest image**. Two behaviours **(A)**:

- A **hash-keyed** entry whose patches all fail to apply is **fatal**.
- A **hash-less generic** entry (pattern-matched compiler idioms) only warns.

**(B)** The reference core shipped exactly one entry and it was generic, so a new title needed no table entry at all.

The trap: the moment you add a hash-keyed entry for your title and then repack the jar in a way that rewrites the native member — or patch bytes into the image on disk — the md5 moves and boot dies. **The rule that avoids this entirely is `guest-re`'s: patch guest RAM after boot, never the shipped binary.** Read-verify-then-write at runtime is idempotent, re-assertable, and invisible to the hash.

## 11. F9 Bundled P/ saves do not seed the database (corrected claim)

**This entry exists to kill a wrong claim, because the wrong claim is written down in the reference port's own deploy documentation.**

The claim: the emulator writes packaged `P/` bytes straight into the user database, so every new user would start with the previous owner's character.

**Measured, and false.** Booting an archive with all three `P/` entries still present, against an empty database root:

```
[probe] DB exists name="./OptionSave" app_id="PD00nnnn" -> false
[probe] DB open   name="./OptionSave" ...  -> work/db/PD00nnnn/db/./OptionSave
```

and the record the game then wrote is 32 bytes that **do not match** the bundled `P/` entry of the same name (different md5). The game wrote its own.

**Mechanism.** The packaged-database read path asks the context for a resource size, which is `ClassLoader.getResourceAsStream` → `URLClassLoader.findResource` over `java.class.path` — i.e. **inside `<AID>.jar` only**. `P/` entries are added to the **virtual filesystem**, a different namespace (see [archive-anatomy.md](archive-anatomy.md#7-the-ktf-boot-trace--what-reads-what-in-order)). A bundled save would only seed the database if it were a *jar member*.

**The action was still right; the reason was wrong.** Strip `P/*` because it is **the previous owner's private data** riding in a file you are about to redistribute or install. Keep stripping it even after you have confirmed it cannot seed anything.

Why carrying the wrong reason is harmful: it implies the strip is optional once you control the database path, and it sends anyone debugging "my saves are weird" to inspect archive entries instead of the database directory.

## 12. F10 How all of this looks on a phone

**(A)** On Android none of the panic text above reaches the user. The panic hook logs a single `PANIC:` line to logcat and the surface stays black. So a phone-side report of "black screen" collapses F1, F2, F4, F5, F6 and several `emulator-core` failures into one indistinguishable symptom.

**Therefore: never debug archive faults on a device.** Reproduce in the headless probe, where the text above is printed verbatim, then go back to the phone. This is the practical reason step 5 exists as a gate rather than as a suggestion.

If you must read it from a device, grep logcat for the panic tag your host installs and capture the line *before* it as well — the `loaded` boundary of §1 is what you need.

## 13. F11 Black screen shortly after the title, allocation failure with a negative size

**(A) The most expensive archive fault in this file, because every gate upstream of the phone passes.** The archive is present, correctly named, correctly repacked, signed and installed. The game boots, shows its title, and dies a few hundred frames later.

Cause: **a data member and a guest constant disagree about the size of a table.** The guest's parser walks one record past the end of an array, reads the following bytes as a record length, that length is a signed 16-bit value, and the allocator is called with a negative size:

```
Allocation failure
```

**(B) Reproduced by accident, in ten consecutive shipped builds.** A generated table was left at its pristine record count while the guest-side constants had already been patched to the new one. Nothing noticed: the build script passed, the signature verified, and the **boot smoke test passed too — because the death is after the boot.**

**Fix and gate.** Restore the pair to agreement, then add the gate that would have caught it: a `--check` that rebuilds the derived member in memory from pristine and byte-compares it against the shipping archive, exiting non-zero ([generated-assets.md §3](generated-assets.md#3-the-structural-fix-a-byte-comparing---check-that-exits-nonzero)). Confirm with a **long** probe run rather than a first-frame check: **(B)** the same content died at frame 557 before the fix and survived 5,200 frames with zero tick errors after it.

**(A) Note the shape, because it generalises past archives:** any generated artifact whose *generator* and whose *consumer* are versioned separately needs a byte-comparison gate, not an existence check.

## 14. F12 The change you committed is not in the artifact

**(A)** You edit a table, run the tool, verify it, commit, and a later session finds the member byte-identical to pristine. Nothing failed. There is no error to search for.

Two causes, both silent, and you should check them in this order:

1. **The archive is version-control-ignored, so it did not travel.** A session run on a different checkout leaves only source, documentation and a version bump behind — the asset work stays on the machine that did it. **(B)** The reference port's check gate caught exactly this on its first use: the shipping archive on that machine had never received the previous session's edit.
2. **A second tool rebuilt the archive from pristine afterwards** and discarded your member. See the ordering rule in [generated-assets.md §2](generated-assets.md#2-two-generators-one-archive--the-ordering-rule).

**Fix.** Re-run the pipeline in the declared order on this machine. **Gate:** run every generator's `--check` as the *first command* of any session that touches assets; it is the only thing that distinguishes "this checkout is current" from "this checkout looks current".

## 15. F13 One field is blank for some records, different ones each session

**(A)** A display field is empty for a handful of records; which ones changes between sessions; a different screen shows the same data correctly. It reads as a font, encoding or layout bug, and it is none of those.

Cause: **you deleted a byte that the guest scans for when computing that field's length** — an unbounded, NUL-blind scan. The scan now runs off the allocation into the heap and stops at the first matching byte it finds, so the result depends on allocation history: a length that looks normal, or a negative one that draws nothing.

**(B)** In the reference port an edit removed a separator byte along with the sentence after it and shipped green; the reports arrived two session-blocks later, after unrelated changes moved the heap around.

**Fix.** Restore the byte in the data — not in the code. The unbounded idiom is normally duplicated across every screen that shows the field (**(B)** eight sites, two arms each), so one data byte fixes all of them and a code patch would need every site. Then make the writer enforce it: present, **not at index 0**, on every output record. Full mechanism and method: [record-formats.md §3](record-formats.md#3-the-safety-theorem-for-length-prefixed-records).

## 16. How to reproduce a fault deliberately

**(A)** The whole table above was produced this way, and you should do the same when a new fault appears: start from a **known-good** archive and change exactly one thing.

```python
import zipfile, io

def mutate(src_bytes, member, transform):
    src = zipfile.ZipFile(io.BytesIO(src_bytes))
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            name = info.filename
            if name == member:
                name, data = transform(name, data)
                if data is None:            # returning None deletes the entry
                    continue
            zi = zipfile.ZipInfo(name, date_time=info.date_time)
            zi.compress_type = info.compress_type
            zi.external_attr = info.external_attr
            zi.internal_attr = info.internal_attr
            zi.create_system = info.create_system
            dst.writestr(zi, data)
    return out.getvalue()
```

The six mutations that produced this dictionary:

| scratch archive | mutation | fault |
|---|---|---|
| `minimal.zip` | keep only `__adf__` + `<AID>.jar` | **none** — boots clean, 120 frames |
| `synth.zip` | replace the descriptor with a hand-written 3-line one (37 bytes) | **none** — boots clean |
| `crlf.zip` | descriptor LF → CRLF | F1 |
| `badclass.zip` | `MClass:` misspelled | F2 |
| `noadf.zip` | delete `__adf__` | F3 |
| `nobss.zip` | rename `client.bin<N>` → `client.bin` | F4 |

Keep the mutants. When a later change breaks boot, re-running the known-good and the known-bad archives tells you in thirty seconds whether the regression is in the archive or in the core.
