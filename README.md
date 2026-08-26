# featurephone-tools

A Claude Code **plugin marketplace** for remaking original feature-phone games as native apps.

It currently hosts one plugin, **`featurephone-port`**, which packages a complete, field-tested
methodology for taking an original WIPI (KTF/LGT), SKT/SKVM or J2ME MIDlet `.jar` — the kind of
archive a Korean or Japanese feature phone downloaded over the air in the 2000s — and turning it
into a native **64-bit** Android app running on a forked
[`dlunch/wie`](https://github.com/dlunch/wie) emulator core, with touch controls, a layout editor,
gamepad support, host-side UI drawn with the game's own assets, save-data protection, and
optionally a replacement for a game server that no longer exists — or an in-process engine that
retires it entirely.

**Eight skills**, ~95 reference documents and 24 runnable tools, all generalized away from any
single title. See the plugin's [CHANGELOG](plugins/featurephone-port/CHANGELOG.md).

## What this ships, and what it does not

**Ships:** the procedure, the failure dictionary, the reverse-engineering toolchain, the build and
signing pipeline, and a publish audit — all generalized away from any single title.

**Does not ship:** any game, any game asset, any extracted decryption key, any server address, and
any signing material. You supply your own legally obtained dump. That is the only defensible
posture for a project like this, and it is also what makes the tooling useful to someone porting a
completely different title.

## Install

```
/plugin marketplace add apkdeployer2580/featurephone-tools
/plugin install featurephone-port@featurephone-tools
```

Non-interactive equivalents:

```bash
claude plugin marketplace add apkdeployer2580/featurephone-tools
claude plugin install featurephone-port@featurephone-tools
```

Then start with `/featurephone-port:port-triage` — it classifies your dump and routes you to the
right chapter.

### The eight skills

| skill | owns |
|---|---|
| `port-triage` | Classify the dump, pick the backend, decide AOT-ARM vs bytecode, route. Start here. |
| `archive-prep` | Everything between "you have a file" and "the emulator loads it", plus generated-asset gates. |
| `emulator-core` | The failures a real commercial title hits on a stock fork, in the order it hits them; frame pacing, guest pixel loops, audio, memory. |
| `guest-re` | The guest binary: the address model, static toolkit, patching, calling guest code, live data structures, and supporting more than one build. |
| `android-host` | The Android layer: threads, touch, HUD and layout editor, gamepad, translating the guest's own keypad UI to touch, host UI drawn with the game's assets, audio mixing. |
| `save-integrity` | Not destroying the player's save. Loss mechanisms, the pre-image vault, slot identity, policy as pure functions. |
| `server-revival` | Optional. Put a dead online title back on the wire — or retire the server with an in-process engine. |
| `port-shipping` | Scaffold, build, sign, version, distribute, crash-triage, and the publish audit. |

## Repository layout

```
featurephone-tools/
├── .claude-plugin/marketplace.json     the catalog
├── LICENSE                             MIT
└── plugins/
    └── featurephone-port/              the plugin (8 skills, shared docs, tools, evals)
```

## Local development

```bash
claude plugin validate .                            # the marketplace manifest
claude plugin validate ./plugins/featurephone-port  # the plugin + every skill's frontmatter
claude --plugin-dir ./plugins/featurephone-port     # load it without installing
```

After editing a skill in a loaded session, run `/reload-plugins`.

## Versioning

`plugins/featurephone-port/.claude-plugin/plugin.json` pins an explicit `version`. Claude Code
caches installed plugins **by version string**, so pushing new commits alone will not reach
existing users — bump the version (and the matching `version` in `marketplace.json`) for every
release, and record it in the plugin's `CHANGELOG.md`.

## Legal

This is tooling for people who own the original software. It contains no game code, no game data,
and no keys. Game titles, logos and trademarks belong to their respective rights holders, who are
not affiliated with this project. Reverse engineering for interoperability is treated differently
across jurisdictions; you are responsible for your own compliance.

## License

MIT — see [LICENSE](LICENSE).
