# Changelog

All notable changes to `featurephone-port` are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **Read this before releasing.** `.claude-plugin/plugin.json` pins an explicit `version`, and
> Claude Code resolves a plugin's version from that field first. It caches the installed copy by
> that version string, so **pushing commits alone does not deliver anything to users** — they keep
> the cached copy until the string changes. Every user-visible change therefore needs three things
> in the same commit:
>
> 1. a bumped `version` in `plugins/featurephone-port/.claude-plugin/plugin.json`;
> 2. the identical string in the plugin's entry in the marketplace's `.claude-plugin/marketplace.json`
>    (`plugin.json` silently wins if they disagree, and `claude plugin validate` warns on the mismatch);
> 3. an entry below.
>
> Users pick the change up with `/plugin marketplace update featurephone-tools` followed by
> `/reload-plugins`, or automatically on a later session start. If you are iterating fast on a
> branch and do not want to bump on every commit, remove `version` from `plugin.json` entirely —
> the git commit SHA is then used as the version — and restore it before tagging a release.
>
> Semver for this plugin: **MAJOR** on a removed or renamed skill, a renamed tool, or a
> breaking change to the `target.toml` schema; **MINOR** on a new skill, a new tool, a new
> reference file, or a new tool flag; **PATCH** on corrections, added worked examples, description
> tuning, and documentation fixes. The plugin `name` itself is effectively immutable — users
> reference it in `enabledPlugins` and in every `/plugin install` line.

## [Unreleased]

Nothing yet.

## [0.1.0] - 2026-08-06

Initial public release. Packages a complete methodology for remaking an original feature-phone
game — WIPI (KTF/LGT), SKT/SKVM, or J2ME MIDlet — as a native Android app on a forked
`dlunch/wie` emulator core, generalized away from any single title.

### Added

- **Seven skills**, one router plus six phase skills, each scoped to one contiguous sitting of
  work with one mental model and one toolset:
  - `port-triage` — classifies the dump, picks the platform backend, decides AOT-ARM vs JVM
    bytecode, triages the first symptom, and dispatches. Ships the 20-chapter pipeline with an
    explicit exit criterion per chapter, the platform matrix, the prerequisites table, and the
    scope-and-legality doctrine.
  - `archive-prep` — platform detection from the magic entry, descriptor CRLF repair, AID/jar/native-member
    name reconciliation, stripping the previous owner's private save entries, surgical in-jar
    repacking with member-wise byte-identity proof, unknown record-format brute forcing, and the
    empirically minimal bootable archive.
  - `emulator-core` — the six failures a real commercial title hits on a stock `wie` fork, in the
    order it hits them, each with symptom, mechanism, fix, and whether it is game-specific. Plus
    vendoring discipline, the WIPI-C and Java class extension recipes, the port-verbatim hardening
    batch, the graphics fast paths, and the diagnostic gauges that tell the layers apart.
  - `guest-re` — the four-constant address model, the static cross-reference toolkit, the headless
    probe contract, read-verify-then-write patching, safe host-initiated guest calls, and asset
    decoding.
  - `android-host` — the worker/UI thread contract, the ordered hit-test chain, the placement
    resolver and on-device layout editor, host-side reimplementation of guest widgets with
    differential verification, closed-set CJK glyph generation, JNI/platform glue, and layered
    on-device configuration.
  - `server-revival` — optional. Connect redirect at the socket layer, three-way framing
    triangulation, serializer-anchored request enumeration, reply RE from the client's own
    handlers, the hot-reloadable black-box reply table, the replacement-server architecture, the
    five conformance-test classes, deployment, and a written threat model.
  - `port-shipping` — scaffolding with the full parameter table and its blast radius, the
    toolchain and its three-way native-library naming rule, the build script stages and gates,
    build-time config verification *inside the artifact*, host portability, the device-free test
    gates, and the publish audit.
- **Twenty-three runnable tools** under `tools/`, all Python 3.10+, stdlib except `capstone`
  (`dis.py`, `extract_packets.py`) and `Pillow` (`frames_to_png.py`, `gen_glyphs.py`), which are
  checked for and reported rather than auto-installed. See [tools/README.md](tools/README.md).
- **`target.toml`** — the per-title configuration hinge. The reference implementation's own RE
  scripts each hardcoded a binary path, an image base, a static base and a relocation delta; here
  every static tool reads one config file and `target.py --check` sanity-reports it, including
  corpus-wide inference of the relocation unit.
- **Shared conventions** under `shared/`: the glossary, the evidence standard ((A)/(B)/(C) tags,
  HIGH/MED/LOW × CONFIRMED/INFERRED, two-independent-derivations, preserved refutation blocks),
  the dependency-free selftest convention, the merged cross-phase fault dictionary, and the
  reference-implementation citation policy.
- **Per-skill trigger evaluations** under `evals/`, with should-trigger, should-not-trigger and
  ambiguous prompts, so the seven descriptions can be proven not to steal each other's traffic.
- **MIT `LICENSE`** for the plugin itself.

### Security

- Ships **zero game content**: no archive, no extracted guest binary, no sprites, no string
  tables, no item text, no icons, no listing assets.
- Ships **zero cryptographic keys**. Extracted resource-decryption and save-encryption keys are
  treated as a materially different category from offsets and are omitted entirely — including
  from worked examples and from tool defaults. `bruteforce_records.py` has no key option and its
  documentation states that encrypted members are out of scope.
- Ships **zero secrets**: no server host or IP, no certificate pin, no keystore path or filename,
  no device serial, no email, no absolute personal path, no account identifier. Documentation uses
  `YOUR_SERVER_HOST`, `<64-hex SHA-256 of your server cert DER>`, `$ANDROID_SERIAL`,
  `work/client.bin`, `<project>-release.jks`, `com.example.<name>`.
- `publish_audit.py` sweeps the working tree **and every git ref** for exactly these categories,
  and is run against this repository as a release gate.

### Notes on provenance

Title-specific constants — guest addresses, struct offsets, packet ids, table formats, tuning
numbers — are retained only as **(B)**-tagged worked examples, and every one of them is introduced
by the method that found it. A bare constant with no method is treated as a bug. The reference
title is named exactly once, in `shared/reference-implementation.md`, as attribution.

### Corrections carried from the source analyses

Six claims in the reference implementation's own documentation were measured to be wrong. This
release ships the correction, with the refutation preserved beside it, rather than the error:

1. Bundled private save entries do **not** seed the user database — packaged-database reads
   resolve through the classloader while those entries land in the virtual filesystem. Strip them
   because they are someone's private data, not because they seed anything.
2. The differential-render check is gated on an explicit dump-frame list; the reference invocation
   could not print anything. Both the probe contract and the tool help now state the gate.
3. The first dead-code reachability sweep was wrong because it did not follow `bl` — and Thumb-1
   routinely uses `bl` as a long branch. Only the corrected sweep ships, and its docstring says why.
4. A measured payload length is a **band, not a constant**. The version that asserted a measured
   length fired on the first real save. Never pin a measurement as an invariant.
5. Deployment documentation and code defaults must agree on every destructive flag; the reference
   had a restore-enabled default contradicted by its own service unit. The deployment docs are part
   of the security surface.
6. "The authentication model is structural and cannot be fixed" is wrong. You own both endpoints,
   so a challenge-response fits inside the existing frames with no guest changes. That is presented
   as the reference design; the bearer-token model is labelled the fallback.

[Unreleased]: https://github.com/<owner>/featurephone-tools/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/<owner>/featurephone-tools/releases/tag/v0.1.0
