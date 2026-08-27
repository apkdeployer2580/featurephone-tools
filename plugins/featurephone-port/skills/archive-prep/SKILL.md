---
name: archive-prep
description: Prepares a carrier download package or a bare .jar so a wie-family emulator will boot it — detects the platform from the magic entry (__adf__ for KTF, app_info for LGT, *.msd for SKT, a MIDlet manifest for J2ME), repairs CRLF line endings in the app descriptor, reconciles the AID against the jar basename and the client.bin<bss> native member, strips the previous owner's private save entries, and repacks in-jar data files without disturbing any other member. Use when a game archive will not load or boots to a black screen; when the loader reports "Unknown archive format" or "not a KTF archive"; on a NotFound panic in java/io/file.rs, on "Main class not found" or "Invalid bss_size in filename"; when asked to classify, prepare, repair, unpack, repack, strip or verify a game .zip/.jar/.jad/.kdp; when in-jar item text, tables or data members must be rewritten; or when a generated archive member may be stale — an edit missing from this checkout, or a game dying after the title screen.
license: MIT
---

# Archive prep

Everything between "the user has a file" and "the emulator loads it". No emulator source changes — if the archive loads and the screen is black, that is `emulator-core`, not this skill.

Work on a copy under `work/`. Never commit, publish or paste the dump, the jar, the extracted native image, or any member of either. The tooling ships; the bytes are the user's.

## Step 1 — Classify

Run `python "${CLAUDE_PLUGIN_ROOT}/tools/classify_archive.py" work/<archive>.zip --json`.

Detection is **presence of a magic filename**, nothing else:

| found | platform |
|---|---|
| outer zip has `__adf__` | KTF (WIPI) |
| outer zip has `app_info` | LGT (WIPI) |
| outer zip has any `*.msd` | SKT (SKVM) |
| bare jar with an entry named `client.bin*` | KTF |
| bare jar containing `binary.mod` | LGT |
| bare jar starting `20 00 00 00 00 00 00 00` | SKT |
| `.jad` + `.jar`, or `MIDlet-1` in the manifest | J2ME MIDlet (fallback — no probe) |

Then decide what the guest **is**: a jar with **no `.class` files and one large `client.bin<N>`** is AOT-compiled ARM. You are emulating a CPU, not bytecode, and that decides the whole project. Detail, entry inventory and the boot trace: [reference/archive-anatomy.md](reference/archive-anatomy.md).

## Step 2 — Repair the descriptor

Do this **before** anything else. Read the descriptor (`__adf__` / `app_info`) and count CR bytes.

```python
adf = z.read('__adf__')
adf.count(b'\r')          # must be 0
adf = adf.replace(b'\r\n', b'\n')
```

The KTF/LGT descriptor parser splits on `\n` and slices from a fixed index **with no trim**, so one `\r` makes the AID `"XXXXXXXX\r"` and the jar lookup misses. Symptom is a panic *after* the emulator reports it loaded. SKT's `.msd` parser trims and is immune. This one repair was the entire difference between the unbootable and bootable archives in the reference port, twice, on two different titles.

## Step 3 — Reconcile the four names

| name | must equal | consequence if wrong |
|---|---|---|
| `AID:<X>` in the descriptor | the outer-zip entry `<X>.jar`, exactly | `NotFound` panic on the virtual FS |
| `client.bin<digits>` at the jar root | digits parse as the BSS size (**KTF**; LGT's `binary.mod` is a linked ELF and carries its BSS in the section table) | `Invalid bss_size in filename` |
| `MClass:<Y>` | a class the guest image can resolve | `Option::unwrap()` on `None`, or `Main class not found` |
| `PID:<P>` | anything — but choose once | it is the on-device save directory, forever |

Missing descriptor? Synthesize one: three lines is proven sufficient. See [reference/minimal-archive.md](reference/minimal-archive.md).

## Step 4 — Strip the previous owner's private entries

Remove every `P/*` entry and anything else shaped like a save record. Reason: it is **someone's personal data** riding in the dump.

It is *not* because bundled saves seed a new user's database — that claim is in the reference port's own deploy notes and it is **false**, measured. Packaged-database reads resolve through the classloader (jar resources); `P/` entries land in the virtual filesystem, a different namespace. The game writes its own fresh record. Correcting the reason matters because the false version implies you can skip stripping when you control the DB path. You cannot.

Run `python "${CLAUDE_PLUGIN_ROOT}/tools/prepare_archive.py" work/<src>.zip work/<archive>.zip --strip-private` — it does steps 2–4 together and proves every surviving member byte-identical.

## Step 5 — Verify boot headless, before any phone

```
cargo build --release -p wie_probe
./target/release/wie_probe --zip work/<archive>.zip --w 240 --h 320 --frames 120 --ms 20 --out work/probe
```

`--ms` is **mandatory**: the guest idles on a platform timer, so with no virtual clock the screen stays black and you will misdiagnose a working archive.

Success = `[probe] loaded`, then a periodic work gauge, then a dumped frame with non-black pixels. Anything else: match the exact text in [reference/fault-dictionary.md](reference/fault-dictionary.md) — the single most useful discriminator is whether the failure fires **before or after** `loaded`.

Add `--db work/db` to exercise the save path against a real on-disk database.

★ Booting proves the archive **loads**; it says nothing about whether its members are **current**. A generated member that disagrees with a guest constant boots fine and kills the game a few hundred frames after the title (F11) — that needs the `--check` gate above and a long soak run, not a smoke test.

## Optional — repack in-jar data

Only when text or tables that are **not** in the guest binary must change. Two rules: rebuild entry-by-entry preserving `compress_type`/`date_time`/attrs, and verify by re-parse plus member-wise byte compare — never by size.

★ **The first command of any session that touches assets is every generator's `--check`**, not an edit. The archive is gitignored, so it does not travel with a commit, and a second tool that rebuilds from pristine silently discards the first one's members. Both failures are invisible: every tool reports success. `--check` rebuilds in memory and byte-compares against the archive that ships, exiting non-zero. Ownership, tool order and the canary member: [reference/generated-assets.md](reference/generated-assets.md).

```
python "${CLAUDE_PLUGIN_ROOT}/tools/repack_member.py" work/<archive>.zip \
  --member "<AID>.jar!dat/<table>" --transform mytransform.py:fn --backup-dir work/backup
```

Mechanics and pitfalls: [reference/repacking.md](reference/repacking.md). Deciding whether a data file is safely editable at all: [reference/record-formats.md](reference/record-formats.md). Encrypted members are out of scope.

## Reference index

| file | read it when |
|---|---|
| [reference/archive-anatomy.md](reference/archive-anatomy.md) | classifying, or you need to know what reads what at boot |
| [reference/fault-dictionary.md](reference/fault-dictionary.md) | anything failed — match the exact panic text |
| [reference/repacking.md](reference/repacking.md) | rewriting any member of the zip or the jar |
| [reference/generated-assets.md](reference/generated-assets.md) | more than one tool writes the archive; a member may be stale; before shipping |
| [reference/record-formats.md](reference/record-formats.md) | deciding if a data file tolerates a length change |
| [reference/minimal-archive.md](reference/minimal-archive.md) | descriptor missing, or bisecting a broken archive |

Provenance tags used throughout: **(A)** generic procedure · **(B)** worked example from the reference port · **(C)** do not copy.
