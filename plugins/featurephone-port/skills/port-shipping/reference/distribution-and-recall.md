# Distribution posture, handouts and recalls

A port has to decide, once and explicitly, **how it reaches people**: sideloaded APKs handed to a
few players, or a listing on a public store. The decision changes the legal surface and almost
nothing about the engineering discipline — which is the part people get wrong in both directions.

It also has a consequence nobody plans for: sooner or later you will ship a build that destroys
data, and you will need to say so in a way that reaches the next reader.

Tags: **(A)** generic procedure · **(B)** worked example from the reference port, re-derive it ·
**(C)** do not copy. Conventions: `${CLAUDE_PLUGIN_ROOT}/shared/evidence-standard.md`.

## Contents

- [1. Pick a shape and write it down](#1-pick-a-shape-and-write-it-down)
- [2. What the sideload-only decision changes — and what it does not](#2-what-the-sideload-only-decision-changes--and-what-it-does-not)
- [3. Discipline the installer enforces regardless](#3-discipline-the-installer-enforces-regardless)
- [4. The save-preserving update](#4-the-save-preserving-update)
- [5. Verify the installed build from the phone](#5-verify-the-installed-build-from-the-phone)
- [6. The handout directory holds exactly one build](#6-the-handout-directory-holds-exactly-one-build)
- [7. The handout manifest](#7-the-handout-manifest)
- [8. Recalls: where the line lives and what it must name](#8-recalls-where-the-line-lives-and-what-it-must-name)
- [9. The withdrawn ledger only grows](#9-the-withdrawn-ledger-only-grows)
- [10. What a store listing would additionally force](#10-what-a-store-listing-would-additionally-force)
- [11. Checklist](#11-checklist)

---

## 1. Pick a shape and write it down

**(A)** Record the distribution decision in the document every future session reads first, with its
date and its reason. It is load-bearing for the legality chapter, for the build script, and for what
may be committed — and a decision that lives only in someone's memory gets silently reversed.

**(B)** The reference port's line: *no remake is published to any public store; builds are installed
locally or handed directly to a small number of people.*

## 2. What the sideload-only decision changes — and what it does not

> **★ (A) Choosing not to publish changes what may be REFERENCED on your own machine. It changes
> nothing about what may be COMMITTED.**

| | may be referenced locally | may be committed / published |
|---|---|---|
| RE tooling, notes, addresses, offsets, coordinates, formats | yes | **yes** |
| Extracted art, audio, fonts, string tables, data binaries | yes, for your own build | **no** — regardless of which title they came from or how privately you use them |
| Decryption / save-encryption keys | — | **never**, in any form |

**(A)** The practical effect is that a port can reference assets extracted from another title on the
developer's own machine without creating a distribution risk, while the repository stays clean:
**tools, docs and coordinates are committed; the art binaries are not.** That is also what keeps the
work useful to somebody porting a completely different game.

**(A)** A corollary worth stating because it surprises people: a **generated source file** containing
the game's font glyphs, icon atlas or string table is licensed content that happens to be text. It
is not publishable just because it compiles.

Full doctrine: [../../port-triage/reference/scope-and-legality.md](../../port-triage/reference/scope-and-legality.md).

## 3. Discipline the installer enforces regardless

> **(A) "We do not publish to a store" is never a reason to relax build, signing or versioning
> discipline.** Sideloading removes the store's *review*. It removes none of the operating system's
> installer rules.

Three rules the installer enforces on every install, forever:

1. **`versionCode` must increase monotonically.** An equal or lower value is refused.
2. **The signing certificate must stay byte-identical forever.** A different certificate makes the
   update impossible — the only path is uninstall, which deletes the player's saves.
3. **The `applicationId` is the on-device data path.** Changing it orphans every existing install's
   data, silently, and looks like a fresh install to the OS.

## 4. The save-preserving update

**(A)** An update preserves saves when all three hold:

- `versionCode` strictly greater than the installed one;
- the signing certificate identical to the installed one;
- the same `applicationId`.

**(A) The three ways a save dies at install time**, all avoidable and all irreversible:

| what happened | why the save is gone |
|---|---|
| certificate changed, so someone uninstalled first | uninstall deletes app-private storage |
| `applicationId` changed | the new package reads a different data directory; the old one is orphaned |
| a debug-signed build was distributed | it is signed with a per-machine key, so the next release build cannot overwrite it, forcing an uninstall |

> ⛔ **(A) Never distribute a debug build.** Debug artifacts are signed with a key that differs per
> machine and per checkout. Handing one out means the recipient's next update is an uninstall.

## 5. Verify the installed build from the phone

> **(A) Verify the certificate of the build that is actually installed by pulling the installed
> package off the device and running the signature verifier on it — not by trusting your build
> record.**

**(B)** This was done before claiming an update would preserve saves: extract the installed package,
compare its certificate digest against the new APK's. It is the only check that proves an overwrite
install is possible, and it takes about a minute.

```bash
# (A) which build is on the phone, and did the last install replace or reinstall?
adb -s "$ANDROID_SERIAL" shell dumpsys package com.example.<name> \
  | grep -E 'versionCode|firstInstallTime|lastUpdateTime'
```

`firstInstallTime` moving is the tell that somebody uninstalled. Serial discipline and the two
half-days it costs to skip it: `${CLAUDE_PLUGIN_ROOT}/shared/device-verification.md`.

## 6. The handout directory holds exactly one build

> **(A) The distribution directory contains the current build and nothing else.**

**(B)** This rule was written after a build range shipped that died immediately after the title
screen. At the moment of handing a file to somebody, nobody re-reads the changelog to work out which
of four APKs is newest — they take the one whose name looks right.

**(A)** Two supporting habits:

- **Name the artifact with its `versionCode`.** Every build system produces one fixed output name,
  so the file alone cannot say what it is.
- **Keep the handout directory out of version control**, and say so where the build script writes
  to it.

## 7. The handout manifest

**(A)** Whatever you hand over, hand over the four facts that let the recipient check they got what
you think they got — and that let *you* interpret their bug report later:

| fact | why the recipient needs it |
|---|---|
| `versionCode` (and the filename that carries it) | so their report names a binary |
| artifact size and a content digest | so a truncated or wrong download is caught before it is blamed on you |
| signing certificate digest | so they can confirm this update will install over what they have |
| what changed, and **what is unverified** | the second half is the one that gets omitted; see `device-verification.md` §5 |

## 8. Recalls: where the line lives and what it must name

> **★ (A) When a defect can destroy player data, the recall goes into the document every future
> session reads FIRST — as a line naming the affected version range and the failure — not only into
> the release notes of the version that fixed it.**

Release notes are read by whoever is looking for that release. A recall needs to be read by somebody
who is not looking for it at all: the person about to hand out an old file, or the person about to
tell a player to reinstall.

**(A) The line has three parts and fits on one:**

```
⛔ Builds <= vcNNN withdrawn — <mechanism in one clause> can destroy a save slot.
```

**(B)** Two such lines accumulated in the reference port's always-read project document, each for a
different save-destroying defect, each naming a version ceiling and the mechanism in one clause.

## 9. The withdrawn ledger only grows

> **(A) A withdrawn build is not recallable. Somebody already has the file.**

There is no rollback and no revocation. The only remedies are a higher `versionCode` — so the fixed
build can overwrite the bad one — and a **written statement of the affected range**, which is what
lets a recipient decide whether the copy they are holding is safe.

**(A)** Keep the withdrawn list as an append-only ledger with a reason per entry, and distinguish
two states, because they license different actions:

| state | meaning | action |
|---|---|---|
| **recalled** | this build can lose data or cannot run | tell holders to update; never hand it out |
| **do-not-hand-out** | superseded, not dangerous | fine on a device that has it; not given to anyone new |

## 10. What a store listing would additionally force

**(A)** If the decision goes the other way, the added obligations are mostly *publication*
obligations, and they collide directly with §2:

- store listing assets — icons, screenshots and a description, all of which show the game;
- a privacy declaration covering anything the app collects or transmits;
- a content rating, and a developer identity attached to the listing;
- review, which inspects the artifact you upload, including its assets directory.

That last point is the practical one: the artifact contains the game. **(A)** A port that intends to
publish must have an answer for the rights position of everything inside the package *before* it
builds a listing, not after.

## 11. Checklist

- [ ] The distribution decision is written down, dated, with its reason, in the first-read document.
- [ ] `versionCode` increments automatically on every release build; it cannot be forgotten.
- [ ] The release signing key lives outside the repository and is backed up somewhere that survives
      the machine.
- [ ] Release artifacts are named with their `versionCode`.
- [ ] The handout directory contains exactly one build.
- [ ] No debug-signed artifact has ever been handed to anyone.
- [ ] The installed certificate on a real device was compared against the new build's.
- [ ] Every data-destroying defect has a recall line in the first-read document naming the range.
- [ ] The withdrawn ledger distinguishes *recalled* from *do-not-hand-out*.
- [ ] `publish_audit.py` has been run over the working tree **and every git ref** before any push.
      → [publish-audit.md](publish-audit.md)
