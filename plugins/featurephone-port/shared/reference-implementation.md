# The reference implementation

Everything in this plugin was derived from one real, working port. This file says what that port
is, how it is cited, and what this plugin deliberately does not ship.

## Contents

- [What it is](#what-it-is)
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

It is a private hobby project. The game is a commercial title that somebody else owns. This plugin
names neither the game nor the people involved, and the methodology does not depend on either.

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

- **Thoroughly exercised:** the KTF/WIPI archive and boot path; the emulator core's six-failure
  sequence; the address model for an ahead-of-time-compiled ARM guest; host-to-guest reads, patches
  and calls; the Android host layer; protocol reverse engineering and a replacement server; the
  build, signing and distribution pipeline.
- **Exercised once, on one title:** the asset and record formats, the specific guest widgets
  reimplemented host-side, and the online feature set. These are the areas where the (B) examples
  are most likely to mislead if copied rather than re-derived.
- **Not exercised at all:** the LGT, SKT/SKVM and J2ME paths beyond their detection probes and
  loader constants; non-Korean text encodings; titles that ship Java bytecode instead of a native
  image; and any title that verifies the integrity of its own archive. Where a document rests on
  one of these, it says so.
- **Corrected, not carried forward:** the reference port's own documentation was wrong in six
  identified places. Those are preserved as refutation blocks in
  [evidence-standard.md](evidence-standard.md) §4 and repeated in the skills that own them. The
  plugin ships the corrections; it does not ship the errors.
