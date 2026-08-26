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
- [2. Three supported distribution shapes](#2-three-supported-distribution-shapes)
- [3. Fail loudly when the archive is absent — and again when it is stale](#3-fail-loudly-when-the-archive-is-absent--and-again-when-it-is-stale)
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

## 2. Three supported distribution shapes

All three have been done; pick one and write it down.

**(a) Runtime file picker.** The app ships with no game and the user selects their archive at
launch. This is the cleanest position — the distributed artifact contains nothing licensed at all.
**(B)** A web build of the reference emulator does exactly this with a plain file input; the
Android storage-access-framework equivalent was still an open item, so budget for it rather than
assuming it exists.

**(b) Documented drop-in build step.** The repository documents "place your dump at
`app/src/main/assets/<archive>.zip`", the path is gitignored, and a fresh clone therefore cannot
build a runnable APK. Anyone who builds is someone who already has the archive.

**(c) Sideload-only, to named people.** The build is never published: it is handed to specific
players, or installed by you. **(B)** The reference port made this an explicit standing decision —
no public store, ever — and it is the shape most ports of commercial titles land in.

**Bundling the archive into a distributed APK is a private-build option, not the default the
methodology teaches.** If you do it for yourself, understand that the APK now embeds licensed
content *and* your baked configuration, which is why a build output directory is itself a leak
vector (§4).

### 2a. What the sideload-only decision changes — and what it does not

> **★ (A) Choosing not to publish on any public store changes what may be REFERENCED on your own
> machine. It changes nothing about what may be COMMITTED.**

Tools, notes, addresses, offsets, coordinates and format descriptions are publishable either way
(§6). Extracted art, audio, fonts, string tables and data binaries are not publishable either way —
**regardless of which title they came from, or how privately you use them.** The store decision
removes one risk vector; it does not license redistribution, and **a repository is redistribution.**

**(B)** Applied in practice on the reference port, for a second title in the same repository: the RE
toolkit, the format notes and the container offsets were committed, while every extracted asset
stayed in a scratch directory and a disassembler database was handed over as a file rather than
committed. Two derivations that the rule is real: the written policy, and the observable split in
what the repository actually tracks.

**(A) The failure runs in both directions.** A porter who conflates the two rules either
over-restricts — refuses to reference assets they legitimately hold, and so cannot build anything —
or under-restricts, and commits the extracted art because "we are not selling it".

### 2b. The discipline sideload does *not* relax

Sideloading removes the store's **review**. It removes none of the operating system's **installer**
rules: `versionCode` must still increase, the signing certificate must stay byte-identical forever,
and the applicationId is still the on-device data path. "There is no store to reject it" is never a
reason to skip versionCode automation or keystore care — you meet the installer's refusal instead,
at the moment you are handing someone a build. Posture, handout manifest and recall ledger:
[../../port-shipping/reference/distribution-and-recall.md](../../port-shipping/reference/distribution-and-recall.md).

A store listing, if you ever choose one, additionally forces you to publish **listing imagery** —
which for a port is derived from the original's art, and is exactly what §4 refuses — plus a store
identity and a content rating. That is a separate decision from the build, not a continuation of it.

## 3. Fail loudly when the archive is absent — and again when it is stale

**(A) The most important hardening a public template can have.** Android's asset directory is
packaged wholesale, including when it is empty — so a missing archive produces a clean build, a
successful install, and a black screen with an error line buried in the log.

Add a build-time check that the expected asset exists, **ahead of the expensive native build**, and
make it a **hard failure** naming the exact path it wanted — not a warning, which scrolls past in a
build that ends in success.

**(B) The gate exists because the failure shipped first.** A build produced on a second machine's
clone had no archive at all: the packager took the empty directory, the build reported success, the
signature verifier passed, the install succeeded, and the app aborted on its first log line — and it
was handed to a user in that state. The check was added afterwards, with the incident written into
the comment above it. That is stronger evidence than the earlier form of this paragraph ("the
reference project did not have one") could ever be: every gate upstream of the phone passes, so
nothing in a conventional pipeline notices.

### 3a. Presence is not correctness

**(A) A second, worse failure hides behind the first: an asset that is PRESENT but STALE.** It
survives the existence check, the signature verification and a boot smoke test, because the game
starts, shows its title, and dies afterwards. **(B)** Ten consecutive releases of the reference port
shipped that way.

The gate for it is a byte-comparing content check — rebuild the derived member in memory from the
pristine original and compare, exiting non-zero — plus a long headless soak asserting zero fatal
errors. Both:
[../../archive-prep/reference/generated-assets.md](../../archive-prep/reference/generated-assets.md)
and [../../port-shipping/reference/build-script.md](../../port-shipping/reference/build-script.md) §2.1.

## 4. Never redistribute

Not in a repository, not in a release artifact, not in an issue attachment, not in a gist.

| category | examples |
|---|---|
| the archive itself | the carrier download package, the inner `.jar`, any repaired or repacked variant of either |
| **derived copies with different names** | `<archive>.zip.pre-rename`, `<archive>.orig`, backups written by your own tools, anything under an `asset_backup/` directory |
| the extracted guest binary | `client.bin*`, `binary.mod`, the relocated image dump |
| decoded assets | sprite atlases, icon atlases, tile sets, palettes, audio, decoded map data |
| **generated files that CONTAIN those assets** | a generated **source file** holding the game's font bitmaps; a packed atlas blob committed for a compile-time include. Same bytes, different extension — and invisible to an ignore rule written for archives |
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
- **a generated source file carrying those bytes** — a font table baked into a `.rs`/`.c`/`.py`
  array is licensed content that happens to compile;
- a fixture file for your tests that is a slice of the real archive.

**(A) The rule that resolves this cleanly, because a port generates derived content by design:**

> A generated artifact derived from **the archive** is a **build product** — gitignored, produced at
> build time from the user's own archive, with a `--check` that proves it is current. Only a
> generator whose output derives from **your own source** may commit its output.

**(C) The counter-example, from the reference port itself:** it committed an 83 KB binary sprite
atlas decoded from the game's art into its crate source tree for a compile-time include, and the
generator that produced it hardcoded absolute paths on a drive the project's own notes record as
gone, with no `--check`. That artifact is simultaneously a licensed-content leak into a repository
that carefully gitignores the archive, **and** un-auditable: there is no way to answer *"does this
blob match the archive we ship?"* Mechanism and the `--check` shape:
[../../archive-prep/reference/generated-assets.md](../../archive-prep/reference/generated-assets.md).

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

# generated artifacts that CONTAIN licensed bytes — build products, not sources.
# An ignore rule written for archives does not match a .rs full of font glyphs.
**/<generated_font>_data.rs
**/*_atlas.bin

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

**(A) The app's own diagnostics screen is a disclosure surface too, and it is the one people
forget.** On a release build it is the *only* channel that can report what the app resolved — the
debug bridge cannot read app-private storage — so it accumulates exactly the values in the table
above. It is shown to whoever is holding the phone. Report the **fact** and not the secret: the
configuration *source* ("baked at build time" / "read from a file"), the mode, a pin's
presence-or-absence — never the address, never the pin itself.
[../../port-shipping/reference/on-device-diagnostics.md](../../port-shipping/reference/on-device-diagnostics.md).

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
      **in the tree or in any ref** — including a **generated source file or blob that carries those
      bytes**. Every artifact derived from the archive is a gitignored build product with a
      `--check`.
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
- [ ] The build **fails loudly** when the game archive is missing — and every generator's `--check`
      is clean, because a present-but-stale member passes every other gate here.
- [ ] The on-device diagnostics screen reports configuration **facts**, not the destination, the pin
      or any credential.
- [ ] `LICENSE` and `THIRD_PARTY_NOTICES.md` exist and cover every vendored and bundled component,
      including any copyleft relink obligation.
- [ ] The README's first paragraph states the bring-your-own-dump posture.
- [ ] If a server is part of the project: the authentication model is stated in one sentence above
      the fold, and the cheat surface is listed explicitly.
- [ ] `python "${CLAUDE_PLUGIN_ROOT}/tools/publish_audit.py" --all-refs` is clean.
