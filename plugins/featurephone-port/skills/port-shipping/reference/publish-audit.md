# Publish audit: what may safely be committed, open-sourced or published

Provenance: **(A)** generic · **(B)** worked example from one reference port · **(C)** never copy.

> This chapter exists because a real port's release documentation — the file that was itself
> destined for a public marketplace — contained, in plaintext, a live server address, a
> certificate pin, a keystore path, a device serial and an APK certificate fingerprint. Nothing
> in the build pipeline noticed, because none of it is a build problem. **(B)**

## Contents

1. [Posture: what a port may publish](#1-posture-what-a-port-may-publish)
2. [Run the audit](#2-run-the-audit)
3. [The .gitignore doctrine](#3-the-gitignore-doctrine)
4. [A tracked file is never re-evaluated against .gitignore](#4-a-tracked-file-is-never-re-evaluated-against-gitignore)
5. [Derived artifacts carry what you purged](#5-derived-artifacts-carry-what-you-purged)
6. [State, logs and databases live outside the source tree](#6-state-logs-and-databases-live-outside-the-source-tree)
7. [History is not the working tree](#7-history-is-not-the-working-tree)
8. [What a history rewrite cannot reach](#8-what-a-history-rewrite-cannot-reach)
9. [The four categories of finding](#9-the-four-categories-of-finding)
10. [Sweep classes](#10-sweep-classes)
11. [False-positive discipline](#11-false-positive-discipline)
12. [Method traps that produce wrong audits](#12-method-traps-that-produce-wrong-audits)
13. [Remediation order](#13-remediation-order)
14. [LICENSE and THIRD_PARTY_NOTICES](#14-license-and-third_party_notices)
15. [The checklist](#15-the-checklist)

---

## 1. Posture: what a port may publish

There is exactly one defensible shape, and it is also the one that makes the project useful to
strangers:

> **Publish the emulator and the tooling. Ship no game content. Require the user to supply their
> own dump at runtime.**

| category | publishable? |
|---|---|
| your host code, build scripts, server code, RE tooling | yes, with a LICENSE |
| the vendored emulator fork | yes — its licence permits it; carry the notice |
| derived protocol metadata (offsets, byte templates, catalogues) | yes — wiki-analogous |
| **the game archive, the extracted native binary, in-jar data** | **no** |
| **decoded sprite atlases, dumped string tables, title art, derived icons** | **no** |
| **any built APK/AAB** (it embeds all of the above) | **no** |
| **extracted decryption or save-encryption keys** | **no, and not even as an example** |
| store-listing assets built from original art | **no** |

Choosing a licence for your own code does not and cannot cure the game content, because you have
nothing to sublicense. There is no arrangement that makes shipping the archive lawful; there is
no need to, because the user has their own.

**Extracted keys are a separate and worse category than offsets.** An offset is a fact about a
program. A key that decrypts a copyrighted work is a circumvention tool. Keep keys out of the
tree, out of history, out of examples, and out of any tool's defaults — a tool that takes
`--key` must have no default and must document that encrypted members are out of scope.

## 2. Run the audit

```
python "${CLAUDE_PLUGIN_ROOT}/tools/publish_audit.py" --repo .              # fast, working tree
python "${CLAUDE_PLUGIN_ROOT}/tools/publish_audit.py" --repo . --all-refs   # the one that counts
```

Run it:

* before the **first** push to any remote,
* after **every** history rewrite (to prove the rewrite worked),
* before publishing any documentation, skill or plugin derived from the repository,
* before handing out any build.

`--all-refs` is not optional. §7 explains why: the worst finding is usually not on `main`.

| flag | use |
|---|---|
| `--all-refs` | scan every blob in every ref and report **which refs each hit is reachable from**. Slower, and the only mode that tells the truth about a clone. |
| `--fail-on {info,low,medium,high,critical}` | exit non-zero at or above this severity (default `medium`). This is what makes it a CI gate rather than a report. |
| `--allow-ip` / `--allow-hex` / `--allow-email` | one value you have decided is fine — e.g. your **own published** certificate fingerprint. Repeatable. |
| `--allowlist FILE` | the same, in a file (default `.publishauditignore`, `#` comments). Commit it. |
| `--show-secrets` | print matches unmasked. **Never in a CI log** — the log becomes the leak. |
| `--json` | machine-readable, for wiring into a pre-push hook |
| `--blob-threshold` / `--max-blob-bytes` | tune what counts as "possible game content" and how large a blob is still scanned |

The allowlist is not a way to silence findings; it is where §11's false-positive discipline is
recorded. Every entry should carry a `#` comment saying **why** it is not exposure. An allowlist
with no comments becomes the place findings go to die.

## 3. The .gitignore doctrine

**Guard by NAME and by PATH. Both. Always. (A)**

```gitignore
# licensed game content -- the slot the build reads from (PATH rule)
android_app/app/src/main/assets/*.zip
# ...and any copy of it anywhere, under any suffix (NAME rule)
<archive>.zip*
asset_backup/

# signing material -- second line of defence; the first is keeping it outside the repo
*.jks
*.keystore
*.p12
*.pfx
keystore.properties
signing.properties
*.pem
*.key
*.crt
*.cer

# live state -- an account store IS a credential store
server/*.db
server/*.db-shm
server/*.db-wal
*.jsonl
recovery_backup_*.bin

# build outputs
*.apk
*.aab
/android_app/.gradle/
/android_app/build/
/android_app/app/build/
/android_app/app/.cxx/
/android_app/app/src/main/jniLibs/
android_app/local.properties
*.log
*.err

# test harness binaries (~7 MB each)
/t?.exe
/t?.out
/tt_*.exe
```

**Why two rules for the same content**, and this is the part people get wrong: the *path* rule
guards the one slot the build reads from, which is the only place the archive is *supposed* to
be. The *name* rule catches every other copy — a `.orig` backup a repack tool wrote, a copy
dropped in a scratch directory, a variant with a suffix that no longer ends in `.zip`. A path
rule alone is a rule about where you *intended* to put the file. **(B)** In the reference, the
path rule existed first and was insufficient for exactly that reason; the comment recording why
is still next to it.

Two structural gaps worth closing preemptively **(B)**:

* No generic `*.key` / `*.pem` rule existed — one careless test-cert generation and it is
  committed.
* The assets directory was guarded only by `*.zip`, so a non-zip asset dropped there (a `.jar`,
  a raw `.bin`) would be committed. Prefer a whitelist:
  `assets/*` ignored, with `!assets/.gitkeep`.

## 4. A tracked file is never re-evaluated against .gitignore

This is the single most important mechanical fact in this chapter, and the one that defeated an
otherwise excellent `.gitignore` in the reference **(B)**.

```
git add dist/build.apk       # before the *.apk rule existed
# ...later...
echo '*.apk' >> .gitignore   # does NOTHING to dist/build.apk
```

`.gitignore` is consulted for **untracked** paths only. A file committed once stays committed
through every subsequent rule change, every cleanup pass and every audit that only reads
`.gitignore`. In the reference this is precisely why a distributed APK survived a purge that
caught everything else — and that APK is what kept the archive and the baked secrets public.

**Therefore: never audit `.gitignore`. Audit `git ls-files`.**

```bash
git ls-files                                  # what is actually tracked, right now
git ls-files --others --exclude-standard      # what a careless `git add -A` would add next
git ls-files | grep -Ei '\.(apk|aab|jks|keystore|db|jsonl|pem|key|crt|p12)$'
git rm --cached <path>                        # untrack without deleting locally
```

If `git ls-files --others --exclude-standard` returns a short, all-clean list, your ignore
coverage is good *going forward*. That says nothing about the past.

## 5. Derived artifacts carry what you purged

A built APK contains:

* `assets/<archive>.zip` — the licensed game content, byte-identical;
* `lib/arm64-v8a/libmain.so` — with every baked constant in `.rodata`: server host, certificate
  pin, any endpoint or flag you compiled in ([config-baking.md](config-baking.md));
* `META-INF/CERT.RSA` — the signing **certificate** (public only; no private key, but the DN is
  visible, so do not put a real name in it).

**Consequence (A): a string purge cannot succeed while any built artifact is tracked.** You can
scrub every `.md`, `.ps1` and `.rs` mention of a host and a pin, and the values are still served
from inside the APK. Purge the artifacts first, then the strings.

The same logic applies to: an AAB, a `.so` committed for convenience, a screenshot of a terminal,
a log file, a `.jsonl` transcript, a database, a crash dump, and any archive of the repository.

Verify by *opening* the artifact, not by trusting the filename:

```bash
unzip -l dist/build.apk | grep -E 'assets/|lib/'
unzip -p dist/build.apk lib/arm64-v8a/libmain.so | grep -aoE '[0-9a-f]{64}'
```

## 6. State, logs and databases live outside the source tree

Default them **outside the repository**, not inside-and-ignored. **(A)**

| artifact | why it is sensitive | where it belongs |
|---|---|---|
| account/save database (+ `-wal`, `-shm`) | holds per-device account identifiers, which **are** the credential in a bearer-token model; holds other people's saves | a service data directory, e.g. `/var/lib/<project>/` |
| protocol transcripts (`*.jsonl`) | request/reply bytes including identifiers | outside the tree; mask identifiers when writing them |
| recovery/backup blobs | serialized player state | outside the tree |
| build and run logs | absolute paths, hostnames, serials | outside the tree |
| test TLS key/cert | throwaway, but indistinguishable from a real one to a scanner | a temp dir, generated per run |

Two supporting rules:

* **The SQLite sidecars are separate files.** A rule for `*.db` that omits `-wal` and `-shm` is
  a rule that leaks. In the reference, the main database contained only a synthetic identifier
  while **the WAL contained a real one** — the sidecar was the actual leak. **(B)**
* **Mask identifiers at the point of writing.** The server should truncate or hash account ids
  in its own transcripts, so a leaked transcript is not a leaked credential.
* **Deploy scripts must copy explicitly, never recursively.** `cp server/*.py "$APP_DIR"/`, not
  `cp -r server/ "$APP_DIR"/` — a recursive copy of a development directory publishes the
  development database onto the public server. **(A)**

## 7. History is not the working tree

```bash
git log --all --oneline | wc -l
git branch -a
git rev-list --objects --all | wc -l
```

**Audit every ref.** A finding on a local-only branch is invisible to every working-tree scan and
to every `main`-only scan, and it is one `git push --all` or `git push --mirror` away from being
public.

**★ A purge is undone by the backup branch you kept. (A)** The reference's history had been
rewritten to strip the account database and recovery blobs from `main` — and the pre-purge
commits were **deliberately preserved on two local branches** named as backups. `main` was clean;
`origin/main` was clean; the blobs were two commits away from publication at all times. **(B)**

Confirm reachability rather than assuming it:

```bash
git branch --contains <commit>                        # which refs reach it
git merge-base --is-ancestor <commit> origin/main     # exit 0 = it IS public
git log --all --pretty=format: --diff-filter=A --name-only | sort -u   # every path ever ADDED
git log -S'<a confirmed secret string>' --all --oneline                # where a string entered
```

That `--diff-filter=A` inventory is the pass that finds **deleted-but-in-history** files — the
ones no working-tree scan can see. In the reference it is what surfaced a withdrawn APK, the
database, its two sidecars and the recovery blobs, none of which appear in the working tree at
all. **(B)**

If you keep a pre-purge backup — and it is reasonable to want one — keep it as a **bundle file
outside the repository**, never as a branch:

```bash
git bundle create ../pre-purge-backup.bundle --all      # then delete the branches
git branch -D backup-pre-purge leak-backup
git reflog expire --expire=now --all && git gc --prune=now --aggressive
```

## 8. What a history rewrite cannot reach

Force-pushing a rewritten history does **not** remove:

* objects served by the forge's cached-commit URLs (a commit SHA you know can still be fetched);
* objects reachable from pull-request refs (`refs/pull/*`) on the forge;
* **forks** — a fork is a separate repository and keeps everything;
* anyone's local clone, any CI cache, any mirror, any archive service.

**Therefore, if the repository was ever public with the secret in it, the safest remediation is
delete-and-recreate**, plus revoking the credential itself. A rewrite is the right move for a
repository that has never been pushed; it is a partial measure for one that has. Rotate the
secret regardless — that is the only step that is actually within your control.

## 9. The four categories of finding

| category | examples | required action |
|---|---|---|
| **live credentials** | server address in use, certificate pin, account identifier, API token, private key | **revoke/rotate first**, then scrub. Scrubbing a live credential is not remediation. |
| **licensed content** | game archive, extracted binary, sprite atlases, string dumps, title art, derived icons, store assets, any built APK | untrack + purge from history; the project ships none of it |
| **extracted cryptographic keys** | resource-decryption key, save-encryption key | remove entirely; out of scope for published material |
| **personal identifiers** | absolute user paths, email addresses, device serials, real names, remote URLs, cloud region/instance names | replace with placeholders: `work/client.bin`, `$ANDROID_SERIAL`, `$ANDROID_HOME`, `YOUR_SERVER_HOST` |

An **info/low** tier is worth keeping too: values that match a sensitive pattern but are not
exposure — a decommissioned address, your own APK's public certificate fingerprint. Report them
*with the reason they are not exposure*, so the next pass does not re-litigate them.

## 10. Sweep classes

What `publish_audit.py` runs over the working tree and, with `--all-refs`, over every blob in
every ref:

* IPv4 literals, excluding loopback, RFC1918 and RFC5737 documentation ranges;
* `BEGIN PRIVATE KEY` / `BEGIN RSA PRIVATE KEY` / `BEGIN CERTIFICATE` / `ssh-rsa` / `ssh-ed25519`
  / PuTTY key headers;
* assignment forms for `password|passwd|pwd|secret|token|api_key|credential`;
* `keystore|\.jks|storePassword|keyPassword|keyAlias|storeFile`;
* bare 64-hex strings (certificate pins, SHA-256 digests) and 32-hex strings (device-derived
  account identifiers);
* absolute user paths — `C:/Users/<name>/…`, `/home/<name>/…`, `/Users/<name>/…`, any drive
  letter;
* email addresses;
* adb device-serial shapes and `ANDROID_SERIAL` assignments;
* known token shapes: AWS `AKIA…`, GitHub `gh[pousr]_…`, JWT `eyJ…`, Slack `xox…`, `sk-…`;
* a **tracked-file extension sweep**: `pem key crt cer p12 pfx jks keystore ppk apk aab db
  db-wal db-shm jsonl`;
* game-content shapes: any archive in the assets path, any large binary blob with no generator
  script.

The extension sweep and the `--diff-filter=A` inventory are the two passes that find what
grepping for strings cannot.

## 11. False-positive discipline

Write down what you dismissed and why. An audit that re-flags the same 40 benign hits every time
is an audit people stop reading. **(A)**

Legitimately dismissible:

* `127.0.0.1`, `0.0.0.0`, `::1` — loopback defaults, dev-loop addresses, bind-all arguments;
* RFC1918 (`10.*`, `192.168.*`) and RFC5737 (`192.0.2.*`, `198.51.100.*`, `203.0.113.*`) in
  tests and documentation;
* RFC2606 names (`*.example.com`, `*.invalid`, `*.test`) in test vectors;
* CI secret *references* (`${{ secrets.X }}`) — correct usage, not a leak;
* explicit placeholders (`<store password>`, `<YOUR_SERVER_IP>`);
* lockfile integrity hashes and sponsor URLs;
* the vendored third-party tree (exclude from noise sweeps, **include** in the private-key and
  extension sweeps — a key committed under `vendor/` is still a committed key);
* a synthetic test account identifier, if you can prove it is synthetic.

Not dismissible without opening the file: anything binary, anything in history, and anything
whose provenance you inferred from its filename.

## 12. Method traps that produce wrong audits

Each of these produced a materially wrong answer in a real audit. **(B)**

1. **Auditing only `main`.** The worst finding lived on two local branches. Enumerate refs first
   (`git branch -a`, `git log --all`), then decide scope.
2. **Batching `git check-ignore --stdin` through a text-mode pipe.** On Windows, Python's text
   mode translated `\n` to `\r\n` and git received corrupted paths, producing a spectacular false
   result of "1126 exposed files". The tell was that it flagged a `.log` file that `*.log`
   obviously covers. **Use `git ls-files --others --exclude-standard` as the authority**, and
   distrust any helper whose output contradicts an obviously-correct rule.
3. **Trusting filenames over contents.** The database's *filename* suggested a leak; only reading
   it established that the main file held a synthetic identifier and the **WAL** held a real one.
   Extract the blob from history and read it, read-only, with identifiers truncated.
4. **Trusting a summary table over the section it summarises.** One document's status table said
   the leak vector was "closed"; the same document's body said it was pending a decision. The
   body was right. Summaries go stale first.
5. **Assuming a `.gitignore` rule protected a path.** Verify with
   `git ls-files --error-unmatch <path>` and `git log --all --oneline -- <path>`.
6. **Inferring the provenance of binary assets from their generator's docstring alone.** That
   tells you what the generator was *for*. If it says its source is the original title's art, the
   output is derived work — treat it as licensed content and remove it.

## 13. Remediation order

Do these in order. Reordering wastes work: scrubbing strings before removing artifacts, or
rewriting history before deciding the content question, means doing the rewrite twice.

1. **Revoke or rotate every live credential.** The only step with immediate blast radius. An
   account identifier that is a bearer token must be invalidated server-side.
2. **Settle the licensed-content question.** It decides whether the repository can be published
   at all, and therefore what the rewrite has to remove.
3. **Untrack everything that must go** (`git rm --cached`), and add the ignore rules — both the
   name rule and the path rule.
4. **One combined history rewrite** covering artifacts, databases and sidecars, licensed blobs,
   and an author mailmap if the email must go. One pass, not five.
5. **Delete the backup branches**, then `git reflog expire --expire=now --all` and
   `git gc --prune=now`. Keep the pre-purge state as an out-of-tree bundle if you need it.
6. **Delete and recreate the remote** if it was ever public (§8).
7. **Scrub the plaintext strings** in the working tree — addresses, pins, paths, serials, emails
   — replacing them with placeholders.
8. **Add `LICENSE`, `THIRD_PARTY_NOTICES.md`, and a README** that describes the project, the
   fork, and the bring-your-own-dump requirement (§14).
9. **Re-run the audit against the rewritten history** and only then publish.
10. **Audit any derived documentation separately.** A skill, a plugin, a blog post or a slide deck
    generated from the repository is a *new* publication surface and inherits none of the
    repository's protections.

## 14. LICENSE and THIRD_PARTY_NOTICES

**No LICENSE file means all-rights-reserved.** A public repository with no licence grant leaves
adopters with no right to use, fork or redistribute it — which is the opposite of what publishing
it was for. The reference had none. **(B)** Decide before publishing, not after.

Pick a permissive licence compatible with the vendored trees (MIT or Apache-2.0 if the fork is
MIT), and ship `THIRD_PARTY_NOTICES.md` carrying:

| component | obligation |
|---|---|
| vendored emulator core (MIT) | the copyright + permission notice travels with all copies. Satisfied by keeping its `LICENSE` file intact in `vendor/`. |
| vendored CPU core (MIT) | same |
| bundled font | e.g. SIL OFL 1.1 — **the licence text must accompany the font** |
| bundled SoundFont | its licence document **must accompany redistribution** |
| **statically linked LGPL-2.1 synthesiser** | **★ the relink obligation** — see below |

**★ The LGPL-2.1 relink obligation.** If an LGPL-2.1 library (a common case: a software MIDI
synthesiser, linked statically into `libmain.so`, often with a SoundFont embedded via
`include_bytes!`) is **statically** linked into a binary you distribute, you must supply
relinkable object files or an equivalent mechanism so that a recipient can substitute a modified
version of that library and produce a working binary.

Four things about it that catch people out:

* **It attaches to the binary, not to the repository.** Your own MIT licence does not discharge
  it. Distributing the APK is the triggering act.
* **It applies to store distribution too**, not just to a zip on a web page.
* **It survives being a "personal project"** — the obligation is on distribution, and a public
  APK is distribution.
* **Dynamic linking would avoid it**, which on Android means a second `.so`. If you do not want
  the relink obligation, that is the fix; if you accept it, publish the object files (or the
  build inputs sufficient to relink) alongside each release and say so in
  `THIRD_PARTY_NOTICES.md`.

Also rewrite the README. Inheriting the upstream emulator's README means your project is
described by someone else's six-line summary, does not say that `vendor/` is a **modified** fork,
and does not state the bring-your-own-dump posture. MIT does not require change-marking, but with
a heavily modified fork, recording the upstream commit and summarising the changes is what stops
people reporting your bugs upstream.

## 15. The checklist

Nothing here is optional before a first public push.

**Content**

- [ ] no game archive, in the tree or in any ref
- [ ] no extracted guest binary, sprite atlas, string dump, decoded data table
- [ ] no title art, derived icon, mipmap, splash or store-listing asset
- [ ] no built `.apk` / `.aab` / `.so`, tracked or in history (they embed all of the above)
- [ ] no extracted decryption or save-encryption key, including in examples and tool defaults

**Credentials**

- [ ] no keystore, `.p12`, `.pem`, `.key`, `.crt`, `keystore.properties`, `signing.properties`
- [ ] no keystore password, alias or path, in scripts, docs, CI or comments
- [ ] no server address in use; no certificate pin; no API token
- [ ] no account identifier, no transcript, no database, no `-wal`/`-shm` sidecar
- [ ] every live credential found has been **rotated**, not just scrubbed

**Personal identifiers**

- [ ] no absolute user path (`C:/Users/…`, `/home/…`, `/Users/…`, any drive letter)
- [ ] no email address, real name, or personal remote URL
- [ ] no device serial; every adb call uses `$ANDROID_SERIAL`
- [ ] no cloud provider region, instance name or host id

**Repository hygiene**

- [ ] `git ls-files` reviewed in full, not just `.gitignore`
- [ ] `git ls-files --others --exclude-standard` is short and all-clean
- [ ] ignore rules exist **by name and by path** for licensed content
- [ ] state, logs, databases and transcripts default outside the tree
- [ ] `publish_audit.py --all-refs` clean
- [ ] every allowlisted value in `.publishauditignore` carries a comment saying why it is safe
- [ ] no backup branch retains pre-purge blobs; reflog expired; gc run
- [ ] if the remote was ever public: deleted and recreated, credentials rotated

**Licensing and documentation**

- [ ] `LICENSE` exists
- [ ] `THIRD_PARTY_NOTICES.md` exists and covers every bundled font, sound bank and library
- [ ] the LGPL relink obligation is discharged or the library is dynamically linked
- [ ] the vendored fork's own licence files are intact, with the upstream commit recorded
- [ ] the README states the project, the fork, and **bring your own dump** in its first paragraph

**Derived publications**

- [ ] every doc, skill, plugin or post generated from this repository has been audited
      **separately** — it inherits none of the repository's protections
