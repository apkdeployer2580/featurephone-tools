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

**(A) This file describes ONE tool writing the archive.** The moment a second generator writes it — a price table, a generated catalogue, an extracted font — the failure modes change completely, **both tools report success in every one of them**, and the answer is member ownership plus a byte-comparing `--check`: [generated-assets.md](generated-assets.md). Run that check gate as the **first command of any session that touches assets**.

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

**V5 — Read the member back out of the PACKAGED artifact.** Not out of your tool's output. The packager, a build cache and possibly a different checkout sit between the file you wrote and the file the player runs, and each of them is a place where your edit can fail to arrive. **(B)** One release check opened the built package, confirmed its asset directory held exactly one file, and read the 727-byte member back out of it — closing two independent risks (a stray by-product shipping alongside the archive, and a stale member) in one command.

**(A) Choose the comparison base deliberately, because V3/V4 answer different questions depending on it:**

| base you diff against | question it answers |
|---|---|
| the file this run started from | "did *this* run behave?" — necessary, and much weaker than it looks |
| **the last SHIPPED archive** | "did every **other** producer's members survive?" — this is the release check |

**(B)** The reference port adopted the second as the standing rule after an entire session's asset work was found reverted: diffing the jar members against the previous shipped build is how 41 preserved price rows were confirmed intact after an unrelated tool had rebuilt the archive.

**(A) Then the empirical check:** boot the repacked archive in the headless probe and load a save. A repacked archive that boots is the only evidence you will get that the guest does not verify its own resources (see §9). ⚠ Booting is **not** evidence that the member is *current* — an archive whose data and whose guest constants disagree boots perfectly and dies after the title screen. That class needs the content gate in [generated-assets.md §3](generated-assets.md#3-the-structural-fix-a-byte-comparing---check-that-exits-nonzero) and a long soak run ([testing-and-ci.md](../../port-shipping/reference/testing-and-ci.md) §5).

## 5. Pitfalls, each with its mitigation

| pitfall | reality | mitigation |
|---|---|---|
| entry **order** | irrelevant to the emulator — the archive is read into a sorted map | preserve it anyway so diffs stay reviewable |
| **compression method** | mostly deflate, but STORED members exist in the wild | `zi.compress_type = info.compress_type`, per entry |
| **byte alignment** | none required; the jar is read through the zip reader, never mmapped | nothing to do |
| **CRC / signature** | the zip CRC is recomputed by the writer. There is **no jar signature**: `META-INF/` holds only `MANIFEST.MF`, no `.SF`, no `.RSA` | nothing to do |
| **guest self-integrity check** | **unproven** either way. The reference port flagged this honestly and never resolved it | boot the repacked archive and load a save; treat "boots into a corruption screen" as the tell |
| **size drift** | rebuilding with a different zip implementation changes deflate parameters and drops extra fields. **(B)** measured: a rebuilt jar shrank 1,899,048 → 1,877,743 B with **no content change at all** | **"size unchanged" is not a valid sanity check.** Use member-wise byte compare (V3/V4) |
| **re-running the tool** | a transform that edits the shipped file in place is not idempotent and dies on V2; one that rebuilds from pristine is idempotent by construction | rebuild from pristine (§6). If a pristine-sourced tool refuses on a record count, the pristine copy has been **deleted** |
| **a second tool writes the archive** | both tools print a clean report while silently reverting each other's members | declare the order and the ownership; gate with a byte-comparing `--check` — [generated-assets.md](generated-assets.md) |
| **the archive is gitignored** | asset work does not travel with a commit, so a second checkout ships an input that lags its own source | the check gate is the first command of every asset session (§6) |
| **encrypted members** | some data members are encrypted with a per-title cipher; a plaintext parser will produce plausible garbage | out of scope. Touch only members you can parse and re-parse. See [record-formats.md](record-formats.md#8-encrypted-members) |
| **timestamps** | dropping `date_time` makes every entry look changed to downstream tooling | copy it |
| **the guest image** | rewriting the native member changes its md5 and can make a hash-keyed patch table fatal | never repack the native member |

## 6. Backups, idempotence and the apply gate

**(A)** Four rules, all learned the same way:

1. **`--apply` is opt-in.** Without it, write `<archive>.zip.new` **through the same path function as the backups** (rule 3 — a dry-run output beside the archive ships inside the artifact exactly like a backup does) and print the verification report. Default-destructive tooling on someone's only copy of a licensed dump is indefensible.
2. **Always rebuild from the PRISTINE copy, never from the shipped file.** The first `--apply` takes that pristine copy — and only ever from an artifact that really is pristine, because backing up an already-patched archive poisons the fallback source forever. Refuse to overwrite an existing backup.
3. **Backups live OUTSIDE every directory the build packages, at a path computed by one shared function** that every tool touching the archive imports. Not "next to the target", and not "move it by hand after each run".
4. **Backups are the same licensed bytes.** They must be gitignored **by name** as well as by path — a backup with a suffix that is not `.zip` defeats a path-only ignore rule. The reference port needed a dedicated follow-up commit for exactly this leak.

> **REFUTED.** *Previously documented in this section:* "The first `--apply` drops a backup **next to** (not over) the target … naming it `<archive>.zip.orig` or putting it in `--backup-dir work/backup` are **both fine**."
> *Refuted by:* two measurements on the built release package. "Next to the target" is inside the directory the mobile packager consumes wholesale, so the backup shipped **inside the artifact** — 1.76 MB of licensed bytes once, and later 3.53 MB from two by-products. Both measurements: [generated-assets.md §7](generated-assets.md#7-where-by-products-live-and-why-pristine-is-moved-rather-than-deleted).
> *Why the wrong reading was attractive:* the sentence reads as being about *naming*, and naming genuinely does not matter — the **directory** does, and the two options differ in exactly that. Every downstream gate (build, signature, boot smoke) passes either way; the only tell is artifact size.
> *Consequence if carried forward:* every release ships a duplicate of the licensed archive the project was careful to gitignore, and hands it to other people.

**(A) Rebuilding from pristine makes the transform idempotent by construction**, which supersedes what this section used to recommend:

> **REFUTED.** *Previously documented in this section:* "Embrace non-idempotence. A repack tool that can be safely re-run is a repack tool that cannot tell you whether it already ran. Let V2 fail loudly on the second invocation."
> *Refuted by:* a pipeline with two generators, in which the question that matters is not "did *I* already run?" but "**did the other tool's work survive, and does the shipped archive still reflect the tables in this repository?**" Non-idempotence answers neither. The failure that actually ships is staleness, and only a byte-compare detects it.
> *Why the wrong reading was attractive:* failing loudly *is* a signal, and in a single-tool pipeline with no pristine copy it is the only one available — which is exactly when the old rule is still the right answer.
> *Consequence if carried forward:* a manual "restore from backup first" step in front of every run, and total silence about every member this tool does not own. The full argument, and the measured double-run idempotence, are in [generated-assets.md §6](generated-assets.md#6-always-rebuild-from-pristine).

**(A) Move the pristine copy, never delete it.** It is the pipeline's only source, and its absence does not announce itself as "the backup is missing" — it surfaces as an unrelated-looking record-count refusal from the transform, because the tool is now reading its own previous output as its input. See F7 in [fault-dictionary.md](fault-dictionary.md#9-f7-repack-refuses-on-the-second-run).

**(A) Add a `--check` mode, and make it the first command of any session that touches assets.** It rebuilds the members this tool **solely owns**, in memory, byte-compares them against the archive that actually ships, exits non-zero on a mismatch and names the regeneration command. A tool's own `--verify` is not a substitute: run the current tool against the pristine source and it reports success while the shipping file still holds last month's content. Ownership, co-owned members, tool order and the canary member: [generated-assets.md](generated-assets.md).

⚠ **(A) The archive is gitignored, so archive work does not travel with a commit.** A project with more than one checkout will eventually ship an input that lags its own source — commit present, result absent. That is why the gate is a command with an exit code and not a convention.

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

- [ ] Every generator's `--check` was run **first**, and was clean.
- [ ] Working on a copy; the original dump is untouched and backed up outside the repo.
- [ ] The transform sources **pristine**, not the shipped archive; the pristine copy still exists (moved, never deleted).
- [ ] Transform is format-aware, not a byte-offset splice.
- [ ] V1 record count matches.
- [ ] V2 changed-index set matches **exactly**.
- [ ] V3 all other jar members byte-identical.
- [ ] V4 all other outer entries byte-identical.
- [ ] V3/V4 were run against the **last shipped archive**, not only against this run's input — that is what proves the other generator's members survived.
- [ ] V5 the member reads back correctly out of the **packaged artifact**, and that artifact's asset directory contains nothing else.
- [ ] Native member untouched (name and bytes).
- [ ] Descriptor still LF-only and AID still matches the jar basename.
- [ ] Archive boots headless and, if the change touches saved data, loads a save. (Booting proves nothing about staleness.)
- [ ] Backup path is gitignored by name **and** by path, is produced by the one shared path function, and is not inside any directory the build packages.
