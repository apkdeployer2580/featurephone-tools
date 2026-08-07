---
name: port-triage
description: Orients and routes a feature-phone game port — a WIPI (KTF or LGT), SKT/SKVM, or J2ME MIDlet .jar remade as a native Android app on a forked wie emulator core. Classifies the dump from its magic entry, decides whether the guest is AOT-compiled ARM or JVM bytecode, triages the boot state, lists what the user must supply, and dispatches to the archive-prep, emulator-core, guest-re, android-host, server-revival and port-shipping skills in pipeline order. Use when the user mentions porting, remaking, reviving, emulating or re-hosting a feature-phone, WIPI, KTF, LGT, SKT, SKVM, MIDP or J2ME game; names a .jar, .jad, .kdp, __adf__, app_info, .msd or carrier download package; asks where to start on an emulator-backed remake; asks which phase of a port owns a symptom they are seeing; or asks what has to be installed before starting.
license: MIT
---

# Port triage — classify, then route

**Route, then stop.** This skill decides *what kind of port this is* and *which chapter you are
in*; it owns nothing else. Once §5 names a sibling skill, load it and let it own the procedure — do not begin the phase's work here.

Evidence conventions every skill in this plugin uses — **(A)** generic procedure, **(B)** worked
example from a real port, **(C)** do not copy; confidence labels; preserved refutation blocks:
`${CLAUDE_PLUGIN_ROOT}/shared/evidence-standard.md`.

First time through, read [reference/scope-and-legality.md](reference/scope-and-legality.md)
(bring-your-own-dump posture; what may never be redistributed) and
[reference/prerequisites.md](reference/prerequisites.md) (host toolchain; what is not shipped).

## 1. What do you have?

Run `python "${CLAUDE_PLUGIN_ROOT}/tools/classify_archive.py" <archive> --json`.

| evidence | platform |
|---|---|
| outer zip contains `__adf__` | **KTF** (WIPI) |
| outer zip contains `app_info` | **LGT** (WIPI) |
| outer zip contains any `*.msd` | **SKT** (SKVM) |
| bare `.jar` with a root entry named `client.bin*` | **KTF** |
| bare `.jar` containing `binary.mod` | **LGT** |
| bare `.jar` whose first 8 bytes are `20 00 00 00 00 00 00 00` | **SKT** |
| `.jad` + `.jar`, or `MIDlet-1` in `META-INF/MANIFEST.MF` | **J2ME MIDlet** |
| none of the above | not a supported dump — say so and stop |

Detection is **presence of a magic filename**, nothing more. Loader constants, descriptor keys,
per-platform parser differences and which chapters change:
[reference/platform-matrix.md](reference/platform-matrix.md).

## 2. What kind of guest is it?

Look inside the jar. This single fact decides the shape of the whole project.

- **No `.class` files, one large native member** (`client.bin<N>`, `binary.mod`) →
  **AOT-compiled ARM**. You are emulating a CPU, not bytecode. All of `guest-re` applies: image
  base, BSS size, RWPI static base, relocation delta, Thumb patching, headless probe.
- **Jar full of `.class` files** → **JVM bytecode**. The static ARM toolkit does not apply. Skip
  the disassembly steps inside chapters 7–9 and answer the same questions with JVM-level tooling
  (class dump, constant-pool and bytecode search). Chapters 1–6 and 10–19 are unchanged.

⚠ On KTF the native member's **filename suffix is the BSS size** (`client.bin<N>` → N bytes of
zeroed statics appended after the image). Renaming it to `client.bin` is fatal at load.

## 3. Does it boot?

The objective test is a headless probe run that dumps a frame and reports **non-black > 0** —
never an eyeballed screenshot. Match the first symptom you actually see.

| symptom | chapter | route to |
|---|---|---|
| no headless probe crate exists yet | 3 | `port-shipping` (scaffold), then `emulator-core` |
| clean "not a `<platform>` archive" at load time | 1 | `archive-prep` |
| `loaded` prints, then a `NotFound` panic in the Java runtime's file layer | 2 | `archive-prep` — descriptor CRLF, or AID ≠ jar basename |
| `Main class not found`, or a null unwrap out of `loadClass` | 2 | `archive-prep` — missing or wrong main-class key |
| `Invalid bss_size in filename` | 2 | `archive-prep` — native member was renamed |
| black screen, **no error**, CPU busy | 4 | `emulator-core` — you almost certainly have a wall-clock `now()` |
| exactly one `Unimplemented: <id>: <name>` and the emulator stopped | 5 | `emulator-core` |
| runs, slows down, then `AllocationFailure` after 20–40 min | 6 | `emulator-core` |
| SIGSEGV, "stack pointer is not in a rw map", two addresses alternating in the backtrace | 6 | `emulator-core` |
| stutters in proportion to how much is on screen | 6 | `emulator-core` |
| boots and renders | 7 | `guest-re` |

Merged symptom→cause table for every phase: `${CLAUDE_PLUGIN_ROOT}/shared/fault-dictionary.md`.

## 4. Does it need network?

Decide from evidence, not from the genre. Dump the guest's strings and look for messages that
**can only appear when a server answers** — a "waiting for server" modal, "saving to server",
"purchasing", a warehouse/backup/recovery screen. Corroborate by finding a dotted-quad ASCII
literal and the platform's `connect` call site (titles of this era carry their own `inet_addr`
parser, so the address survives as plain text in the image).

- Found → chapters **15–17** apply → `server-revival`.
- Not found → skip 15–17 entirely. Most titles are offline and the protocol vocabulary is pure
  overhead for them.

## 5. Routing table

| what you are doing | skill |
|---|---|
| preparing, repairing, classifying or repacking the archive | `/featurephone-port:archive-prep` |
| anything inside the vendored emulator core or CPU crate | `/featurephone-port:emulator-core` |
| finding an address, patching Thumb, running or extending the probe | `/featurephone-port:guest-re` |
| touch HUD, layout editor, blit, CJK glyphs, storage, on-device config | `/featurephone-port:android-host` |
| sockets, packets, replacement server, TLS and pinning | `/featurephone-port:server-revival` |
| scaffold, build, sign, version, release, publish audit | `/featurephone-port:port-shipping` |

## 6. Where you actually are

The pipeline is 20 chapters, each with an entry state and an exit criterion you do not advance
without: [reference/pipeline.md](reference/pipeline.md).

**A returning user is almost never at chapter 0.** Real work loops:

- **7 ↔ 9 ↔ 13** — pin the address model, prove offsets, patch and verify on a device. Every new
  guest-side feature re-enters at 9, never at 0.
- **12 ↔ 14** — control placement and differential verification of host reimplementations.
- **2** alone, months later, whenever in-jar text or tables must be rewritten.
- **16 ↔ 17** while a reply table is being learned black-box against a live client.

Ask which exit criteria already hold, then enter at the first one that does not.
