# Scope and legality — bring your own dump

The posture this methodology assumes, what may never leave your machine, and the line between
publishing *knowledge about* a game and publishing *the game*.

**This is engineering guidance, not legal advice.** Copyright, anti-circumvention and
reverse-engineering law differ by jurisdiction and by what you personally are entitled to do with
a given copy. The rules below are the conservative posture that keeps a project publishable; they
are not a ruling on what is lawful where you live.

Provenance tags: **(A)** generic · **(B)** worked example from the reference port · **(C)** do not
copy.

## Contents

- [1. The default posture](#1-the-default-posture)
- [2. Two supported distribution shapes](#2-two-supported-distribution-shapes)
- [3. Fail loudly when the archive is absent](#3-fail-loudly-when-the-archive-is-absent)
- [4. Never redistribute](#4-never-redistribute)
- [5. Extracted decryption keys are a separate and worse category](#5-extracted-decryption-keys-are-a-separate-and-worse-category)
- [6. Offsets and metadata versus content — where the line is](#6-offsets-and-metadata-versus-content--where-the-line-is)
- [7. The .gitignore doctrine: guard by name AND by path](#7-the-gitignore-doctrine-guard-by-name-and-by-path)
- [8. History is not the working tree](#8-history-is-not-the-working-tree)
- [9. The other secrets a port accumulates](#9-the-other-secrets-a-port-accumulates)
- [10. Other people's data](#10-other-peoples-data)
- [11. Licensing what you do publish](#11-licensing-what-you-do-publish)
- [12. Publish-readiness checklist](#12-publish-readiness-checklist)

---

## 1. The default posture

> **The project ships the emulator and the tooling. It ships no game content. The user supplies
> their own dump.**

That is the only defensible default, and it is also the one that makes the work useful to
strangers — a repository that requires a specific licensed archive is useful to exactly the people
who already have it.

Everything in this plugin is built around it: no tool has a game archive as a default input, the
scaffold generates an empty assets slot, and the publish audit treats a game blob in the tree as a
blocking finding.

State the posture in the **first paragraph** of your README, not in a footnote.

## 2. Two supported distribution shapes

Both have been done; pick one and document it.

**(a) Runtime file picker.** The app ships with no game and the user selects their archive at
launch. This is the cleanest position — the distributed artifact contains nothing licensed at all.
**(B)** A web build of the reference emulator does exactly this with a plain file input; the
Android storage-access-framework equivalent was still an open item, so budget for it rather than
assuming it exists.

**(b) Documented drop-in build step.** The repository documents "place your dump at
`app/src/main/assets/<archive>.zip`", the path is gitignored, and a fresh clone therefore cannot
build a runnable APK. Anyone who builds is someone who already has the archive.

**Bundling the archive into a distributed APK is a private-build option, not the default the
methodology teaches.** If you do it for yourself, understand that the APK now embeds licensed
content *and* your baked configuration, which is why a build output directory is itself a leak
vector (§4).

## 3. Fail loudly when the archive is absent

**(A) The most important hardening a public template can have.** Android's asset directory is
packaged wholesale, including when it is empty — so a missing archive produces a clean build, a
successful install, and a black screen with an error line buried in the log.

Add a build-time check that the expected asset exists and fails the build with the exact path it
wanted. **(B)** The reference project did not have one, and a fresh clone silently produced an
APK with no game.

## 4. Never redistribute

Not in a repository, not in a release artifact, not in an issue attachment, not in a gist.

| category | examples |
|---|---|
| the archive itself | the carrier download package, the inner `.jar`, any repaired or repacked variant of either |
| **derived copies with different names** | `<archive>.zip.pre-rename`, `<archive>.orig`, backups written by your own tools, anything under an `asset_backup/` directory |
| the extracted guest binary | `client.bin*`, `binary.mod`, the relocated image dump |
| decoded assets | sprite atlases, icon atlases, tile sets, palettes, audio, decoded map data |
| dumped text | string tables, item/enemy/dialogue text dumps, an `ids.txt` of the game's own strings |
| title art and anything derived from it | launcher icons drawn from the game's logo, store listing screenshots, promotional graphics |
| **build outputs that embed any of the above** | `*.apk`, `*.aab`, and a `dist/` directory. An APK embeds the archive *and* your baked host and pin, so a string purge cannot succeed while build outputs are tracked. |

**(B) The lesson that generalises:** a project guarded the assets *path* correctly, and leaked
anyway, because one of its own tools wrote a byte-identical copy of the archive **outside** that
directory, under a different suffix. The fix was a second rule matching the archive **by name,
anywhere, with any suffix**. See §7.

## 5. Extracted decryption keys are a separate and worse category

A key that decrypts a copyrighted resource is not an offset. Publishing offsets is publishing an
observation about a program; publishing a key is publishing the means to decrypt a protected work,
which in several jurisdictions is a distinct act with distinct exposure.

Therefore, in anything you publish:

- **No extracted decryption keys, save-encryption keys or content keys.** Not in documentation,
  not in a worked example, not as a tool default, not in a test fixture, not in a comment
  explaining what the tool would do if you had one.
- Tools that parse container formats must have **no key parameter with a default**. Where a
  format's members are encrypted, the published position is: **encrypted members are out of
  scope.** If you hold a key, that is yours to handle privately.
- The same applies to anything that functions as a key: an obfuscation table, a fixed IV plus
  algorithm identification sufficient to reconstruct the key, a "sample" ciphertext-plaintext pair
  chosen so the key falls out.

This is the one rule in this document with no gradations. Everything else is a judgement call
about how much metadata is too much; this is not.

## 6. Offsets and metadata versus content — where the line is

Reverse-engineering notes are the *product* of this methodology and are fine to publish. Content
is not. The distinction that has held up in practice:

**Publishable — a fact you learned about the program:**

- guest addresses of functions and the arguments they take;
- struct field offsets, clamp values, enumerations;
- record and file *format* descriptions — "u8 length, then the name, then a u32 price";
- protocol framing, packet ids, reply layouts;
- a patch: an address, the original halfword, the new halfword, and the liveness argument;
- measured constants and the measurement that produced them.

**Not publishable — the bytes themselves:**

- the *contents* of those records: item names, dialogue, enemy stats as a table you dumped;
- an image produced by running your decoder over the game's assets;
- a text table regenerated from the game's own resources, even if you reformatted it;
- a fixture file for your tests that is a slice of the real archive.

The practical test: **could a person who does not own the game use this to obtain the game's
content?** A format description plus their own copy is fine — they already had the content. A
dumped table is the content.

One corollary for tooling: make data live **outside** the repository by construction. A decoder
that reads its input from an environment variable or an explicit path cannot accidentally have its
input committed; one that reads `./dat/` will.

## 7. The .gitignore doctrine: guard by name AND by path

**(A) Two rules, deliberately overlapping, because each catches what the other misses.**

```gitignore
# 1. the PATH the build reads from — guards the one slot that matters
app/src/main/assets/*.zip

# 2. the NAME, anywhere, with any suffix — guards the copies your own tools write
#    (a repacker's ".orig"/".pre-*" backups are the same licensed bytes in a different
#     directory, and they do not end in .zip)
<archive>.zip*
asset_backup/

# derived licensed artifacts
client.bin
*.apk
*.aab
dist/

# signing material — the second line of defence behind keeping it out of the repo entirely
*.jks
*.keystore
keystore.properties
signing.properties

# live state: account stores hold credentials, transcripts hold everything
*.db
*.db-shm
*.db-wal
*.jsonl
recovery_backup_*.bin

# throwaway TLS material
*.pem
*.key
*.crt
```

Two supporting rules:

- **Default state and log paths must live outside the source tree.** A server whose default
  database path is next to its own source is how a real account store ended up in a repository.
  Default to a sibling `state/` directory, or require the path as an argument.
- **Back the original archive up outside the repository**, before you touch it, and never inside a
  directory the build packages.

## 8. History is not the working tree

**(A) `.gitignore` prevents future additions; it never removes past ones. And a history purge is
undone by the backup branch you kept "just in case".**

**(B)** A verified real case: an account database, its write-ahead log and a character save blob
were removed from the main branch by a history rewrite — and remained reachable from two local
backup branches on a repository whose remote was public. `git push --all`, a mirror clone or a
fork would have republished a live credential in the clear.

So the audit is: **all refs, all reflogs, not just `HEAD`.**

```sh
# what is reachable from anything, not just the current branch
git rev-list --objects --all | git cat-file --batch-check='%(objecttype) %(objectname) %(objectsize) %(rest)' \
  | awk '$1=="blob" && $3 > 100000 {print}' | sort -k3 -n -r | head -50

# after any purge
git branch -a                      # delete or rewrite the backup branches FIRST
git reflog expire --expire=now --all
git gc --prune=now --aggressive
```

Then run the automated sweep: `python "${CLAUDE_PLUGIN_ROOT}/tools/publish_audit.py" --all-refs`.

And if a credential was ever pushed anywhere: **rotate it.** A purge removes the copy you know
about.

## 9. The other secrets a port accumulates

Licensed content is not the only thing that must not be published. Ranked by how bad it is:

| secret | why it matters | placeholder to use in docs |
|---|---|---|
| the release signing key | lose it and every user must uninstall to take an update — which deletes their saves. Leak it and anyone can ship an "update" to your users. | `<project>-release.jks`, kept outside the repo |
| an account/credential store | in a bearer-token model the stored id **is** the password | never published, in any form |
| transcripts and packet logs | credential-and-PII-complete by construction | off by default; path outside the tree |
| your server's address | invites traffic and attack to a machine you personally run | `YOUR_SERVER_HOST` |
| a certificate pin | identifies the server; useless without it but still a fingerprint | `<64-hex SHA-256 of your server cert DER>` |
| device serials, emails, absolute user paths | personal identifiers, and they break everyone else's build | `$ANDROID_SERIAL`, `work/client.bin` |

**(C)** The reference project's own release procedure document contained, in plaintext, a live
server address, a certificate pin, a keystore path, a device serial and an APK certificate
fingerprint. When adapting an internal runbook into public documentation, **rewrite it from the
parameter table**; do not edit the original file, because you will miss one.

## 10. Other people's data

A dump you obtained may contain the previous owner's saved state — on WIPI platforms, entries
under the `P/` prefix. Strip them, always, before doing anything else with the archive.

**Refutation carried forward (A):** the reason usually given for stripping them — that the
emulator seeds them into a new user's database, so everyone would start as someone else's
character — is **wrong**, at least on the KTF path. Measured: packaged-database reads resolve
through the classloader (jar resources), while archive entries land in the virtual filesystem;
they are different namespaces, and the game writes its own record. Strip them anyway, because they
are **somebody's personal data** and pure payload. Carrying a false mechanism into your own docs
means someone later "optimises" the step away.

## 11. Licensing what you do publish

- **Ship a LICENSE.** A repository with no licence is all-rights-reserved by default — nobody may
  legally use what you published. **(B)** The reference project had none; do not replicate that.
- **Ship `THIRD_PARTY_NOTICES.md`** carrying, verbatim: the notices of every vendored emulator
  tree, the licence of any bundled SoundFont, the OFL text of any bundled font, and an explicit
  statement of the **LGPL relink obligation** if you statically link an LGPL synthesiser or codec
  — that obligation attaches to any binary you distribute, independent of your own repository's
  licence.
- **Vendored forks keep their upstream licence and notices.** Record `vendor/UPSTREAM.txt` with
  the repo URL, base commit SHA and date; it is both an attribution artifact and the only way to
  ever rebase.
- Your reverse-engineering notes are your own writing and you may license them as you like — but
  they must contain no content and no keys (§5, §6).

## 12. Publish-readiness checklist

Run before the first push to a public remote, and again before every release.

- [ ] No game archive, jar, native binary, decoded asset, dumped text table or title-derived art
      **in the tree or in any ref**.
- [ ] No build outputs (`*.apk`, `*.aab`, `dist/`) tracked — they embed both content and config.
- [ ] **No extracted decryption or save-encryption key anywhere**, including in prose, examples
      and tool defaults.
- [ ] No keystore, key file, or keystore password in the tree or in history.
- [ ] No real server address, certificate pin, device serial, email or absolute user path in
      documentation, scripts or comments — placeholders only.
- [ ] No account store, WAL, transcript, recovery blob or save file, in the tree **or in any
      branch, tag or reflog**.
- [ ] Default state and log paths are outside the source tree.
- [ ] `.gitignore` guards licensed content **by name and by path**, and the name rule matches
      suffixed copies.
- [ ] Backup branches from any history purge are deleted, reflogs expired, objects pruned; any
      credential that was ever pushed is rotated.
- [ ] The build **fails loudly** when the game archive is missing.
- [ ] `LICENSE` and `THIRD_PARTY_NOTICES.md` exist and cover every vendored and bundled component,
      including any copyleft relink obligation.
- [ ] The README's first paragraph states the bring-your-own-dump posture.
- [ ] If a server is part of the project: the authentication model is stated in one sentence above
      the fold, and the cheat surface is listed explicitly.
- [ ] `python "${CLAUDE_PLUGIN_ROOT}/tools/publish_audit.py" --all-refs` is clean.
