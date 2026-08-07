# featurephone-port

A Claude Code plugin that packages a complete, field-tested methodology for remaking an original
feature-phone game — a WIPI (KTF or LGT), SKT/SKVM, or J2ME MIDlet `.jar` or carrier download
package — as a native Android app running on a forked [`dlunch/wie`](https://github.com/dlunch/wie)
emulator core. It covers the whole distance: classifying and repairing the archive, bringing the
emulator core up on a real commercial title, reverse-engineering the guest ARM binary with a
headless probe, building the Android host layer with a placeable touch HUD, optionally reviving a
game server that no longer exists, and finally signing, shipping and publish-auditing the result.
Seven skills, twenty chapters with explicit exit criteria, twenty-three runnable tools, and a
fault dictionary that maps a symptom to the phase that owns it.

## Posture: bring your own dump

**This plugin ships zero game content, zero cryptographic keys, and zero server addresses.**

- No game archive, no extracted guest binary, no sprites, no string tables, no item names.
- No extracted decryption or save-encryption keys — not in a worked example, not as a tool
  default, not anywhere. Encrypted archive members are explicitly out of scope.
- No server host, no IP address, no certificate pin, no keystore, no signing material, no
  device serials, no personal paths.

What it ships is the *procedure*, the *failure dictionary*, and the *tooling*. **You supply your
own legally obtained dump of a game you own.** Title-specific constants that appear anywhere in
the documentation are tagged **(B)** — a worked example from one reference port — and are always
introduced by the *method* that found them, so you can rerun the method on your own title instead
of copying a number that will be wrong for you.

Provenance tags used across every file in this plugin:

| tag | meaning |
|---|---|
| **(A)** | generic procedure — applies to any title |
| **(B)** | worked example from one reference port — instructive, not copyable |
| **(C)** | do not copy — shown only so you recognise the shape |

## Install

In a Claude Code session:

```
/plugin marketplace add <owner>/featurephone-tools
/plugin install featurephone-port@featurephone-tools
```

Non-interactive equivalents:

```bash
claude plugin marketplace add <owner>/featurephone-tools
claude plugin install featurephone-port@featurephone-tools
```

To try it without installing, from a clone of the marketplace repo:

```bash
claude --plugin-dir ./plugins/featurephone-port
```

Then start with **`/featurephone-port:port-triage`**. It classifies your dump, picks the platform
backend, and routes you to the chapter you are actually in — including when you are resuming a
half-finished port months later.

## The seven skills

| slash command | what it owns | when it fires |
|---|---|---|
| `/featurephone-port:port-triage` | The router. Classifies the dump, picks the backend, decides AOT-ARM vs JVM bytecode, triages the first symptom, and dispatches. Owns nothing else. | "I want to port an old phone game", "where do I start", "which part of this pipeline owns this symptom", resuming a half-finished port |
| `/featurephone-port:archive-prep` | Everything between "the user has a file" and "the emulator loads it": platform detection, descriptor repair, name reconciliation, stripping the previous owner's saves, surgical in-jar repacking | archive will not load, "Unknown archive format", "not a KTF archive", a `NotFound` panic in the virtual filesystem, "Main class not found", repacking in-jar text or tables |
| `/featurephone-port:emulator-core` | Changes inside the vendored emulator (`vendor/wie`, `vendor/arm32_cpu`). Stops at the `Emulator` trait boundary. The six failures a real title hits, in order. | black screen with no error, `Unimplemented` with a table id, a JVM thread-attach panic, stutter on busy maps, `AllocationFailure` after tens of minutes, "stack pointer is not in a rw map" |
| `/featurephone-port:guest-re` | The guest binary and the host↔guest contract: the address model, static cross-referencing, the headless probe, patching guest RAM, calling guest functions | "where is the save function", "find this offset", disassembly, cross-references, Thumb patching, probe runs, differential verification |
| `/featurephone-port:android-host` | The native Android layer above the `Emulator` trait and below Gradle: threading, blit, touch, HUD, layout editor, host-side widgets, CJK glyphs, storage, config | letterboxing, a control that fires by itself, a virtual joystick, HUD placement, Korean text rendering blank, JNI dialogs, orientation, vibration, saves on device |
| `/featurephone-port:server-revival` | Everything on the wire, both endpoints. **Optional** — offline titles skip it entirely. | a shut-down official server, a private/replacement server, packet RE, socket slots, a client stuck on a waiting-for-server modal, a reply table, TLS and certificate pinning |
| `/featurephone-port:port-shipping` | Project generation, the build pipeline, signing, distribution, and the publish audit | scaffolding a new port, `UnsatisfiedLinkError`, duplicate-class errors, an unsigned APK, versionCode discipline, a Play listing, "what may I safely commit or open-source" |

Level-1 cost when nothing triggers is seven descriptions. Only the skill you land in loads its
body; its reference files cost nothing until read.

## The pipeline — 20 chapters

Each chapter has an exit criterion. Do not advance without it. Full list with exit criteria:
`skills/port-triage/reference/pipeline.md`.

**Phase 0 — orient**

0. Decide and scope — `port-triage`

**Phase 1 — archive**

1. Classify the dump — `archive-prep`
2. Repair and prepare the archive — `archive-prep`

**Phase 2 — core bring-up**

3. Scaffold the project, vendor the core — `port-shipping` → `emulator-core`
4. First headless boot with a virtual clock — `emulator-core`
5. Walk the guest forward past the unimplemented stubs — `emulator-core`
6. Apply the core hardening batch — `emulator-core`

**Phase 3 — guest RE**

7. Pin the address model — `guest-re`
8. Build the string index and feature inventory — `guest-re`
9. Map live state and prove offsets twice — `guest-re`

**Phase 4 — get onto a device**

10. First device build and run — `port-shipping`
11. Input, screen and storage on device — `android-host`
12. Touch HUD and layout editor — `android-host`

**Phase 5 — host↔guest integration**

13. Host↔guest reads, patches and calls — `guest-re` + `android-host`
14. Reimplement guest widgets and verify differentially — `android-host`

**Phase 6 — the wire (optional; offline titles skip 15–17)**

15. Socket layer and connect redirect — `server-revival`
16. Protocol reverse engineering — `server-revival`
17. Replacement server, conformance tests, deploy — `server-revival`

**Phase 7 — ship**

18. Release build, signing, distribution — `port-shipping`
19. Publish audit — `port-shipping`

**Re-entry.** Real work loops 7 ⇄ 9 ⇄ 13 (RE ⇄ patch ⇄ verify) and 12 ⇄ 14 (UI ⇄ verification).
Coming back after a break does not mean restarting at chapter 0 — ask `port-triage` where you are.

## Tools

All under `${CLAUDE_PLUGIN_ROOT}/tools/`, Python 3.10+, stdlib only except where noted. Full CLI
signatures, dependencies, the `target.toml` schema and the shared conventions:
[tools/README.md](tools/README.md).

| tool | purpose | used by |
|---|---|---|
| `target.py` | Loads and sanity-checks `target.toml`, the per-title address model every static tool reads; explains any file offset, guest address, stored pointer word or static slot | all static tools |
| `classify_archive.py` | Detects the platform backend from the magic entry and reports every name-agreement problem that would stop the archive booting | `port-triage`, `archive-prep` |
| `prepare_archive.py` | Repairs a dump into a bootable archive (LF descriptor, reconciled names, private saves stripped) and proves every other member came through byte-identical | `archive-prep` |
| `repack_member.py` | Rewrites exactly one member inside a jar inside a zip, preserving compression and metadata, and proves nothing else moved | `archive-prep` |
| `bruteforce_records.py` | Finds the record layout that consumes an unknown length-prefixed data file *exactly* | `archive-prep`, `guest-re` |
| `dis.py` | Annotated Thumb/ARM disassembly printed in file offsets, with literal-pool, static-slot and branch targets resolved inline | `guest-re` |
| `xref.py` | Six cross-reference questions in one tool: who calls / loads a value / loads a pool slot / materialises a struct offset / stores this function pointer / computes this static slot | `guest-re`, `server-revival` |
| `strings_dump.py` | Offset ↔ text listing in the game's own legacy encoding, with the relocation word to search for next | `guest-re`, `server-revival` |
| `solve_reloc.py` | Recovers the relocation delta by intersection over known strings, then validates it corpus-wide against random controls | `guest-re` |
| `dump_table.py` | Classifies a run of 32-bit words as function pointer / string pointer / plain value — the fastest read of an unknown static area | `guest-re` |
| `msgid_scan.py` | Ranks callees by distinct-small-constant density to find the id-consuming accessor in a stripped image | `guest-re` |
| `bind_strings.py` | Chains a string through its relocation word and pool slot to the instructions that reference it | `guest-re`, `server-revival` |
| `deadcode_sweep.py` | Reachability sweep reporting byte ranges neither executed nor read — follows `bl`, honours literal pools, cross-checks stored code pointers and jump tables | `guest-re` |
| `thumb_enc.py` | Encodes (and decodes) the Thumb halfwords a host-side patch writes, with the immediate-width bound that tells you what your feature can actually be | `guest-re` |
| `frames_to_png.py` | Converts a headless probe's raw framebuffer dumps to PNG, with geometry parsed from the filename | `guest-re` |
| `gen_glyphs.py` | Generates a closed-set CJK bitmap glyph table for a host renderer whose built-in font is ASCII only | `android-host` |
| `adb_drive.py` | One reproducible device run: cold start, scripted taps, logcat capture, screenshot | `android-host`, `server-revival` |
| `extract_packets.py` | Enumerates every request the guest can send, by anchoring on the send-side serializer's flush routine | `server-revival` |
| `name_requests.py` | Names requests by chaining UI strings to their nearest send site — without triggering them | `server-revival` |
| `replay_transcript.py` | Replays a captured JSONL transcript through your `handle(bytes) -> list[bytes]` handler and emits a conformance test | `server-revival` |
| `scaffold.py` | Generates a new port project from a pinned tag of the template repo, substitutes every parameter, then verifies no template token survived and the three native-library names agree | `port-shipping` |
| `verify_artifact_config.py` | Scans the produced `.so` for the configuration strings the build was supposed to bake in — because a missing environment variable produces a silent wrong build, not an error | `port-shipping` |
| `publish_audit.py` | Sweeps the working tree **and every git ref** for secrets, personal identifiers, other people's data and licensed content, before you push | `port-shipping` |

## Prerequisites

You need an Android SDK and NDK, a JDK, Gradle/AGP, a Rust toolchain with `cargo-ndk` and the
`aarch64-linux-android` target, and Python 3.10+. Two tools have third-party dependencies that are
checked for and reported, never auto-installed: `capstone` (`dis.py`, `extract_packets.py`) and
`Pillow` (`frames_to_png.py`, `gen_glyphs.py`).

The pinned-version table, where each version is pinned, and the full **"not in the repo — you must
supply this"** list live in `skills/port-triage/reference/prerequisites.md`. Read
`skills/port-triage/reference/scope-and-legality.md` before your first port.

## 한국어 빠른 시작

이 플러그인은 **방법론과 도구만** 제공합니다. 게임 파일, 복호화 키, 서버 주소, 서명 키는
**하나도 들어 있지 않습니다.** 본인이 정당하게 소유한 게임의 덤프(`.jar` / `.kdp` / 통신사
다운로드 패키지)를 직접 준비해야 합니다.

설치:

```
/plugin marketplace add <owner>/featurephone-tools
/plugin install featurephone-port@featurephone-tools
```

그다음 **`/featurephone-port:port-triage`** 를 실행하세요. 덤프의 플랫폼(WIPI KTF/LGT, SKT/SKVM,
J2ME)을 판별하고, 지금 어느 단계에 있는지 진단해서 알맞은 스킬로 안내합니다. 처음이라면
`skills/port-triage/reference/scope-and-legality.md` 를 먼저 읽으세요 — 무엇을 절대 재배포하면
안 되는지가 거기에 있습니다. 문서에 나오는 주소·오프셋 같은 상수는 전부 **(B)** 태그가 붙은
*예시*이며, 그것을 찾아낸 *방법*과 함께 제시됩니다. 값을 베끼지 말고 방법을 본인 타이틀에
다시 적용하세요.

## Legal

This plugin is tooling for people who own the original software. It does not contain, embed,
bundle, link to, or distribute any game, game asset, ROM, archive, decryption key, or server
address, and it will not help you obtain one. Every workflow in it starts from a dump you supply
yourself.

Reverse engineering for interoperability and personal preservation is treated differently across
jurisdictions, and nothing here is legal advice. Redistributing a copyrighted game — or a key that
decrypts one — is a different act from documenting an offset, and this plugin draws that line
deliberately: see `skills/port-triage/reference/scope-and-legality.md`.

All trademarks, game titles, and carrier platform names are the property of their respective
owners and are referenced only descriptively, to identify formats and platforms. `dlunch/wie` and
every other third-party project named here belongs to its own authors under its own licence.

## Contributing

Issues and pull requests are welcome at `https://github.com/<owner>/featurephone-tools`.

Before opening a PR:

1. Run `python plugins/featurephone-port/tools/publish_audit.py --repo . --all-refs`. It must be
   clean. A contribution that adds a real host, a pin, a serial, an email, an absolute personal
   path, or any game bytes will be rejected on sight.
2. Keep the provenance discipline. A bare constant with no method that found it is a bug — tag it
   **(A)**, **(B)** or **(C)** and say how it was derived.
3. Validate before pushing:
   ```bash
   claude plugin validate ./plugins/featurephone-port
   claude plugin validate .
   ```
4. If you changed a skill's `description`, re-run its evaluations — the seven descriptions are the
   only thing that makes seven sibling skills route correctly. See [evals/README.md](evals/README.md).
5. Bump `version` in `.claude-plugin/plugin.json` (and the matching marketplace entry). See
   [CHANGELOG.md](CHANGELOG.md) for why pushing commits alone is not enough.

Corrections are especially welcome. Several passages here exist specifically to carry a
*correction* to something the reference implementation's own documentation got wrong; if you find
another, the fix is to record the refutation next to the claim, not to quietly delete it.

## License

MIT. See [LICENSE](LICENSE).
