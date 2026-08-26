# The reference implementation

Everything in this plugin was derived from one real, working port. This file says what that port
is, how it is cited, and what this plugin deliberately does not ship.

## Contents

- [What it is](#what-it-is)
- [How far it has run](#how-far-it-has-run)
- [How it is cited](#how-it-is-cited)
- [What this plugin deliberately does not ship](#what-this-plugin-deliberately-does-not-ship)
- [Bring your own dump](#bring-your-own-dump)
- [What the reference port proves, and what it does not](#what-the-reference-port-proves-and-what-it-does-not)

## What it is

A **Korean WIPI/KTF role-playing game for feature phones, remade as a native Android
application.** The original is a carrier download package from the mid-2000s: an outer zip holding
a plain-text application descriptor and a jar, and inside the jar a single large ahead-of-time
compiled ARM image plus a few hundred data and sprite members. There are no `.class` files — the
Java was compiled to machine code at packaging time — so the port emulates an ARM CPU and the WIPI
platform API rather than a bytecode VM.

The port consists of a fork of an open-source feature-phone emulator core, a native Android host
layer built around it (touch HUD, on-device layout editor, host-side reimplementations of a few
guest widgets, app-private storage, configuration), a headless runner used for measurement and
verification without a phone, a static reverse-engineering toolkit over the guest binary, and a
replacement server for the title's long-dead online features.

The title exists as **more than one carrier package**: separate builds, compiled separately, sharing
the game's data layout but not one code address. That fact is not a footnote about this particular
game — it is common for the platform, it is the subject of an entire reference file
([multi-build.md](../skills/guest-re/reference/multi-build.md)), and it changed which package the
reference port ships. See [How far it has run](#how-far-it-has-run).

It is a private hobby project. The game is a commercial title that somebody else owns. This plugin
names neither the game nor the people involved, and the methodology does not depend on either.

## How far it has run

The port did not stop when this plugin's first release was cut. It has since run roughly
**forty-five further working sessions**, and essentially everything added in 0.2.0 comes from them:
save integrity, translating touch onto a keypad-driven guest UI, host windows drawn in the game's own
idiom, the audio mixer, physical gamepad support, frame pacing and guest pixel loops, an in-process
replacement server, generated build inputs, and the sideload-and-recall distribution posture.

Two properties of that stretch decide how much weight to put on what it produced.

**Most of it happened on a second carrier build.** Partway through, the port switched its shipping
target to the other carrier's package of the same game. Every guest *code* address had to be
re-derived from scratch — the transfer rate was zero — while the *data* layout came across almost
intact, with a handful of moved fields that had to be found individually. Several of this release's
strongest generic findings are artefacts of doing that work twice: that the container-dereference
convention belongs to the **carrier runtime** rather than to the game, so one flag resolves grids,
arrays, the item database and the framebuffer together; that binary-patch entries must be keyed by
image hash because one carrier's package shipped in two revisions; and that a runtime which
under-registers a block of platform API slots should be given range dispatch rather than another
enumerated slot. See [multi-build.md](../skills/guest-re/reference/multi-build.md) and
[build-keyed-anchors.md](../skills/guest-re/reference/build-keyed-anchors.md).

**A large part of it shipped ahead of its verification**, sometimes by many builds. That is not a
defect in the record; it is a state the record has to be able to express. Sessions in the reference
port end with an explicit list of what was **not** done — device re-check pending, a listening test
pending on a particular handset, a reproduction blocked — and the matching task row is created in the
same commit. Treat every claim in this plugin that is not marked as device-confirmed accordingly:
[device-verification.md](device-verification.md) §5 is the convention, and it is why `⚠` exists.

## How it is cited

Every reference document in this plugin states the **mechanism first** and the reference port's
file path second, in parentheses:

> The editor's acceptance predicate must *be* the resolver, not a lookalike: run the real resolver
> on a table containing the candidate and ask whether that control got the rectangle it asked for
> (ref: `<app-crate>/src/placement.rs`).

A reader working in a differently-shaped repository takes the sentence and ignores the
parenthetical. A reader who scaffolded from the template repository finds the file exactly where
the parenthetical says. Neither reader is required to obtain the reference port, and no document in
this plugin instructs anyone to fetch it.

Constants from the reference port appear as **(B)**-tagged worked examples, always introduced by
the method that produced them. A bare address with no method is treated as a documentation bug.
See [evidence-standard.md](evidence-standard.md) for the tagging rules.

## What this plugin deliberately does not ship

**No game content, of any title.** No archive, no jar, no extracted native image, no decoded sprite
atlas, no dumped string table, no item text, no icon derived from the game's art, and no packaged
build containing any of the above. Not one byte. Reverse-engineering notes — addresses, offsets,
struct layouts, packet shapes, formats — are engineering description and are included; the bytes
they describe are not.

**No extracted cryptographic keys.** Some titles encrypt individual resource members or their save
records. Publishing a key that decrypts a copyrighted work is a materially different act from
publishing an offset, so no such key appears here in any form — not in a worked example, not as a
tool default, not in a comment. **Encrypted members are out of scope for this plugin's tooling.**
If you hold a key for a title you own, handling it is your problem to handle privately.

**No secrets from the reference deployment.** No server address, no certificate fingerprint or pin,
no keystore path or filename, no device serial, no account identifier, no email address, no
absolute personal path, and no repository remote. Every such value appears as a placeholder:
`YOUR_SERVER_HOST`, `<64-hex SHA-256 of your server cert DER>`, `<project>-release.jks`,
`$ANDROID_SERIAL`, `com.example.<name>`, `work/client.bin`.

**No commercial identity.** The game's name, its Korean name, its publisher, its package
identifier and its store listing do not appear anywhere in this plugin, including in trigger words
and filenames. Where the reference port used a title-derived prefix for environment variables or
Gradle properties, the documentation writes `${ENV_PREFIX}_` and `${GRADLE_PREFIX}.`; where it named
a specific archive, the documentation writes `<archive>.zip`.

## Bring your own dump

The posture the reference port arrived at, and the only defensible one for public tooling:

> **Ship the emulator and the tooling. Ship no game content. Require the user to supply their own
> dump.**

Concretely, that means the template repository this plugin scaffolds from builds an application
with no game inside it, the archive slot is ignored by version control **by name and by path**, and
the build **fails fast** when the slot is empty rather than quietly producing an installable app
with nothing to run. The reference port's build did not have that check; the omission is recorded
in `port-shipping` as a gap the template closes.

Two supported shapes for getting a dump into a build, both demonstrated by the reference port:
a documented "drop your archive at the asset path" step, or a runtime file picker so the shipped
application contains no game at all. Bundling is a private-build option, not the default this
plugin teaches.

Nothing in this plugin helps you obtain a dump, and nothing here should be read as advice that
obtaining or redistributing one is lawful where you are. That is your decision and your
jurisdiction's.

## What the reference port proves, and what it does not

Honest scope, because it changes how much weight a reader should put on a given claim:

- **Thoroughly exercised:** the WIPI archive and boot path on **two carrier variants of one title**;
  the emulator core's six-failure sequence; the address model for an ahead-of-time-compiled ARM
  guest; host-to-guest reads, patches and calls; re-deriving a complete anchor set against a second
  build; the Android host layer, including touch translation, a physical gamepad and host-drawn
  windows; audio on both a synthesised and a sampled bus; frame pacing and host reimplementation of
  guest pixel loops; protocol reverse engineering, a replacement server and an in-process offline
  engine; save integrity under a guest that rewrites its own records; the build, signing and
  sideload pipeline.
- **Exercised once, on one title:** the asset and record formats, the specific guest widgets
  reimplemented host-side, the online feature set, and the in-game systems whose formulas were
  decoded. These are the areas where the (B) examples are most likely to mislead if copied rather
  than re-derived.
- **Not exercised at all:** the SKT/SKVM and J2ME paths beyond their detection probes and loader
  constants; non-Korean text encodings; titles that ship Java bytecode instead of a native image;
  and any title that verifies the integrity of its own archive. Where a document rests on one of
  these, it says so.

> **REFUTED.** *"Not exercised at all: the LGT, SKT/SKVM and J2ME paths beyond their detection
> probes and loader constants."* — this file, in the 0.1.0 release.
>
> **Refuted by:** the reference port's subsequent work. The second WIPI carrier variant became the
> **primary shipping target** and the subject of roughly forty-five sessions, including a complete
> re-derivation of the guest code addresses, per-revision hash-keyed binary patches, range dispatch
> for a block of platform API slots the runtime under-registered, and the whole save and
> serialisation chain ported across.
>
> **Why it was attractive:** the sentence was *true when written* — at 0.1.0 that carrier really was
> just a detection probe and a loader constant — and nothing in the file made it expire. A scope
> statement is exactly the kind of sentence that goes stale silently, because it describes the
> author's situation rather than the subject.
>
> **Correction:** the honest scope for the carrier axis is now "two WIPI carrier variants of one
> title". SKT/SKVM and J2ME remain unexercised. Re-read this section against the release it ships
> in, and date it if it disagrees.

- **Corrected on a regular basis, and that is the point:** the reference port's own documentation
  has been proved wrong repeatedly — a hot-path attribution reversed by per-category counters, a
  padding hypothesis rolled back, an idle-timeout diagnosis refuted by server logs, a "this title is
  all synthesised music, sound effects unused" claim shown simply false, a previous session's
  "fixed" corrected to "half", an inherited reading of one call shown exactly inverted by
  literal-pool arithmetic, and a save guard shipped, defeated and re-derived across four consecutive
  builds. **Do not look for a count here.** A number in a document like this is stale the moment it
  is written, exactly the way a build-number table is; what matters is that each correction is
  preserved beside the claim it refutes. The pattern and a sample of the corrections are in
  [evidence-standard.md](evidence-standard.md) §4, and each is repeated in the skill that owns it.
  The plugin ships the corrections; it does not ship the errors.
