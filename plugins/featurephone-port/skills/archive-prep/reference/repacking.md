# Repacking

How to rewrite one member of a two-level archive and **prove** nothing else moved. Applies to stripping entries, repairing the descriptor, and rewriting in-jar data.

Tags: **(A)** generic · **(B)** worked example from the reference port · **(C)** do not copy.

## Contents

1. [When to repack at all](#1-when-to-repack-at-all)
2. [The rebuild primitive](#2-the-rebuild-primitive)
3. [Nested surgery — two levels in one pass](#3-nested-surgery--two-levels-in-one-pass)
4. [Verification is the deliverable](#4-verification-is-the-deliverable)
5. [Pitfalls, each with its mitigation](#5-pitfalls-each-with-its-mitigation)
6. [Backups, idempotence and the apply gate](#6-backups-idempotence-and-the-apply-gate)
7. [Stripping entries](#7-stripping-entries)
8. [Repairing the descriptor as a repack](#8-repairing-the-descriptor-as-a-repack)
9. [What repacking can never fix](#9-what-repacking-can-never-fix)
10. [Checklist](#10-checklist)

---

## 1. When to repack at all

**(A)** Repack only for things that are genuinely not reachable any other way:

| goal | repack? | better route |
|---|---|---|
| descriptor repair (CRLF, AID, MClass) | **yes** | none — it is a member of the outer zip |
| strip `P/*` private entries | **yes** | none |
| change guest *behaviour* | **no** | patch guest RAM at runtime (`guest-re`) |
| change guest *code* on disk | **no** | see [fault-dictionary.md](fault-dictionary.md#10-f8-fatal-binary-patch-failure-after-touching-the-guest-image) — it moves the image md5 |
| change **text or tables the guest reads from the jar** | **yes** | none. This is the real use case |

**(B) The motivating case.** The reference port reassigned the behaviour of some dead in-game item indices host-side. The item *names and effect text* for those indices are not in the guest binary at all — they live in a data table inside the jar. Behaviour could be changed at runtime; text could only be changed in the archive. That asymmetry is what forces repacking into the pipeline.

If you find yourself repacking to change something the host could decide, stop: the host is where per-user, reversible, updatable decisions belong. The archive is where the *publisher's static data* lives.

## 2. The rebuild primitive

**(A)** The whole of repacking is one function. Read every entry, transform the one you care about, write every entry back with its original metadata.

```python
import io, zipfile

def rebuild(zbytes: bytes, member: str, transform) -> bytes:
    src = zipfile.ZipFile(io.BytesIO(zbytes))
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as dst:
        for info in src.infolist():                  # ORIGINAL ORDER preserved
            data = src.read(info.filename)
            if info.filename == member:
                data = transform(data)
            zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            zi.compress_type = info.compress_type    # some members may be STORED
            zi.external_attr = info.external_attr
            zi.internal_attr = info.internal_attr
            zi.create_system = info.create_system
            dst.writestr(zi, data)
    return out.getvalue()
```

Four fields are copied for four different reasons:

- **`compress_type` per entry.** Copying it per entry rather than setting one global method is the difference between "works on my archive" and "works". **(B)** The reference title happened to be 100 % deflate at both levels (measured: `Counter({8: 1274})`), but the tool copies per entry anyway because carriers did ship STORED members.
- **`date_time`.** Not functional. Copied so that a diff of the rebuilt archive shows only the member you changed, which is what makes verification legible.
- **`external_attr` / `create_system`.** Unix permission bits and the originating platform. Nothing in the emulator reads them; dropping them makes the archive look machine-generated to any other tool the user runs.
- **`internal_attr`.** The text/binary flag. Same reasoning.

**Iteration order matters only to humans** — the emulator reads the zip into a sorted map — but preserving it costs nothing and makes reviewable diffs possible.

## 3. Nested surgery — two levels in one pass

**(A)** To rewrite `outer.zip → <AID>.jar → dat/<table>` you apply `rebuild` twice, inner inside outer, via a closure. Never extract to a temp directory and re-zip: that loses per-entry metadata for every member of the jar, not just the one you changed.

```python
def patch_jar(jar_bytes: bytes) -> bytes:
    return rebuild(jar_bytes, "dat/<table>", transform_records)

new_outer = rebuild(outer_bytes, "<AID>.jar", patch_jar)
```

The jar is rebuilt **while being written back into** the outer zip, in one pass, in memory. The outer zip's own entry metadata is preserved by the outer `rebuild`; the jar's by the inner one.

Run it: `python "${CLAUDE_PLUGIN_ROOT}/tools/repack_member.py" work/<archive>.zip --member "<AID>.jar!dat/<table>" --transform mymod.py:fn --backup-dir work/backup`

## 4. Verification is the deliverable

**(A)** The transform is the easy part. The verification is what makes the operation safe, and it should be the majority of your script. Four checks, all of them cheap:

**V1 — Re-parse and require the same record count.** Read the patched member back with the *same parser* you used to read the original. If the record count changed, your writer and your reader disagree about the format and you have already corrupted the file.

**V2 — Require the set of changed record indices to equal exactly what you intended.** Not "at least"; exactly. A fuzzy parser that silently re-segments the file after a length change passes V1 and fails V2. This is the check that catches the dangerous class of bug.

```python
changed = {i for i, (a, b) in enumerate(zip(old_records, new_records)) if a != b}
assert changed == set(EXPECTED_INDICES), f"changed {changed} != expected {set(EXPECTED_INDICES)}"
```

**V3 — Every other member of the inner jar byte-identical.** Not "same size" — byte-identical, member by member, by name.

**(B)** The reference port's shipped jar versus its pre-patch jar: **1274 of 1274 members present, exactly one differs** (the one data table, 1288 → 1271 B). Descriptor, catalog blob and all three icons byte-identical.

**V4 — Every other entry of the outer zip byte-identical.** Same check one level up.

**(A) Then the empirical check:** boot the repacked archive in the headless probe and load a save. A repacked archive that boots is the only evidence you will get that the guest does not verify its own resources (see §9).

## 5. Pitfalls, each with its mitigation

| pitfall | reality | mitigation |
|---|---|---|
| entry **order** | irrelevant to the emulator — the archive is read into a sorted map | preserve it anyway so diffs stay reviewable |
| **compression method** | mostly deflate, but STORED members exist in the wild | `zi.compress_type = info.compress_type`, per entry |
| **byte alignment** | none required; the jar is read through the zip reader, never mmapped | nothing to do |
| **CRC / signature** | the zip CRC is recomputed by the writer. There is **no jar signature**: `META-INF/` holds only `MANIFEST.MF`, no `.SF`, no `.RSA` | nothing to do |
| **guest self-integrity check** | **unproven** either way. The reference port flagged this honestly and never resolved it | boot the repacked archive and load a save; treat "boots into a corruption screen" as the tell |
| **size drift** | rebuilding with a different zip implementation changes deflate parameters and drops extra fields. **(B)** measured: a rebuilt jar shrank 1,899,048 → 1,877,743 B with **no content change at all** | **"size unchanged" is not a valid sanity check.** Use member-wise byte compare (V3/V4) |
| **not idempotent** | re-running dies on V2 | restore from backup first; see §6 |
| **encrypted members** | some data members are encrypted with a per-title cipher; a plaintext parser will produce plausible garbage | out of scope. Touch only members you can parse and re-parse. See [record-formats.md](record-formats.md#8-encrypted-members) |
| **timestamps** | dropping `date_time` makes every entry look changed to downstream tooling | copy it |
| **the guest image** | rewriting the native member changes its md5 and can make a hash-keyed patch table fatal | never repack the native member |

## 6. Backups, idempotence and the apply gate

**(A)** Three rules, all learned the same way:

1. **`--apply` is opt-in.** Without it, write `<archive>.zip.new` and print the verification report. Default-destructive tooling on someone's only copy of a licensed dump is indefensible.
2. **The first `--apply` drops a backup** next to (not over) the target, and refuses to overwrite an existing backup. Naming it `<archive>.zip.orig` or putting it in `--backup-dir work/backup` are both fine.
3. **Backups are the same licensed bytes.** They must be gitignored **by name** as well as by path — a backup written outside `assets/` with a suffix that is not `.zip` defeats a path-only ignore rule. The reference port needed a dedicated follow-up commit for exactly this leak. Also keep backups out of any directory the build packages wholesale.

**(A) Embrace non-idempotence.** A repack tool that can be safely re-run is a repack tool that cannot tell you whether it already ran. Let V2 fail loudly on the second invocation and make the error message name the backup to restore from.

## 7. Stripping entries

**(A)** Stripping is `rebuild` with a filter instead of a transform:

```python
DROP = lambda name: name.startswith("P/") or SAVE_RE.match(name)
```

What to drop:

- **`P/*`** — the KTF private-data prefix. Always.
- Anything else shaped like a save record: names containing `Save`, `Option`, `Slot`, `Profile`, or a bare numeric record name at the archive root.
- Nothing else. Do **not** strip the icons or the catalog blob in the archive you actually ship — they are unread but they are also evidence about the original package, and removing them gains nothing. (Strip them in a *bisection* copy; see [minimal-archive.md](minimal-archive.md).)

Verify with the same V3/V4 byte-identity pass over the survivors, plus an explicit assertion that the dropped set equals what you intended.

**Why:** the entries are the previous owner's private data. **Not** because they seed the database — that claim is false and [fault-dictionary.md](fault-dictionary.md#11-f9-bundled-p-saves-do-not-seed-the-database-corrected-claim) has the measurement.

## 8. Repairing the descriptor as a repack

The CRLF repair is the smallest possible `rebuild`:

```python
fixed = rebuild(raw, "__adf__", lambda d: d.replace(b'\r\n', b'\n'))
```

**(B)** Applied to a real dump this changed the outer zip by 14 bytes total and nothing else — descriptor 628 → 592 B, 36 CR bytes gone, every other entry byte-identical. Verify with `adf.count(b'\r') == 0` and by re-deriving the AID from the patched descriptor and asserting `f"{aid}.jar" in namelist()`.

Do the descriptor repair, the name reconciliation and the `P/` strip in **one** rebuild pass (`prepare_archive.py` does), not three — each pass re-encodes every member and each one is another chance to drop metadata.

## 9. What repacking can never fix

- **A wrong `MClass`** — repacking rewrites bytes, it cannot invent an entry point.
- **A missing native member** — nothing to rebuild.
- **Guest behaviour** — that is a runtime patch.
- **The unknown.** If the guest *does* verify its own resources, repacking is detectable and there is no container-level workaround; you would have to find and neutralise the check in the guest. Nobody has had to, yet, on this family of titles — but "boots and loads a save" is the extent of the evidence.

## 10. Checklist

Before `--apply`:

- [ ] Working on a copy; the original dump is untouched and backed up outside the repo.
- [ ] Transform is format-aware, not a byte-offset splice.
- [ ] V1 record count matches.
- [ ] V2 changed-index set matches **exactly**.
- [ ] V3 all other jar members byte-identical.
- [ ] V4 all other outer entries byte-identical.
- [ ] Native member untouched (name and bytes).
- [ ] Descriptor still LF-only and AID still matches the jar basename.
- [ ] Archive boots headless and, if the change touches saved data, loads a save.
- [ ] Backup path is gitignored by name **and** by path, and is not inside a directory the build packages.
