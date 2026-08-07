---
name: guest-re
description: Reverse-engineers a feature-phone game's guest ARM binary and settles every behavioural claim in a headless probe run rather than from disassembly. Pins IMAGE_BASE, the BSS size, the RWPI static base sl and the relocation delta; finds strings, message ids, refusal gates, reward formulas, struct offsets and asset formats; then patches guest RAM and calls guest routines safely from the host. Covers read-verify-then-write patching, Thumb re-encoding, proving a code range dead before using it as scratch, three-state async guest calls, and differential verification of host reimplementations. Use when the user asks to find an address, offset, function, string or table inside client.bin or a guest image; to disassemble or cross-reference the guest; to patch a Thumb instruction or immediate; to call a guest function from the host; to run, script or extend the headless probe; to decode a sprite, item or text table; or to prove a guest offset really is the field they think it is.
license: MIT
---

# Guest binary RE and host-to-guest control

Owns: the guest image, the address model, the probe CLI contract, patching, and calling guest code.
Not here: emulator internals (`emulator-core`), Android UI (`android-host`), wire protocol (`server-revival`), archive repacking (`archive-prep`).

Applies to **AOT-compiled ARM guests** (a jar with no `.class` files and one large native member). For a bytecode guest, the static ARM toolkit below does not apply — only the probe and evidence sections do.

## The two rules

**Rule 1 — Settle behaviour dynamically. Disassembly answers *where*, a probe run answers *what*.**
Never write "the game does X" on the strength of a listing. Run it, dump a frame, diff the RAM, read the gauge. In the reference port a static-disassembly verdict of "impossible without guest patches" was **reversed by a single headless run** with a different framebuffer size. Cost of a probe run: about a minute, no phone.

**Rule 2 — Prove every offset twice, before anything is built on it.**
An offset is *confirmed* only when two **independent** derivations agree — e.g. a clamp value read out of the setter, and the same number derived from the game's own rules; or a differential RAM diff plus a code site that materialises the constant. One derivation is a hypothesis. Label it as such using `${CLAUDE_PLUGIN_ROOT}/shared/evidence-standard.md`.

## Setup — one config file, zero hardcoded paths

Every static tool reads the same file. This is the only thing that changes per title.

```toml
# target.toml
binary      = "work/client.bin"   # the extracted native guest image
image_base  = 0x100000            # from the loader crate's load(data, BASE, ...) call
bss_size    = 64000               # parsed from the native member's filename
sl_fo       = 0xb2c40             # RWPI static base, as a FILE OFFSET
reloc_delta = 0xeec               # add to a stored word to recover ...
reloc_unit  = "file_offset"       # ... "file_offset" or "guest_address"  <- wrong = every tool lies
encoding    = "euc-kr"            # text encoding of the game's strings
isa         = "thumb"             # default decode mode
god_object  = 0x1b3084            # optional: static slot holding the main state pointer
```

Values shown are the reference port's **(B)**. Derive your own — see below.

## Phase 1 — Pin the address model

1. `image_base` from the platform loader's `load(data, BASE, len + bss)` call.
2. `bss_size` from the native member's **filename** (`client.bin<N>`). Guest statics extend past EOF; an address beyond `len(file)` is not automatically invalid.
3. `sl_fo` by **two of three** routes: a register dump from a provoked fault, the entry thunk's `ldr/mov sl/add sl,pc` prologue, or a density scan with `dump_table.py`.
4. `reloc_delta` — run `solve_reloc.py` to intersect candidates over >=5 known string offsets, then `--validate` corpus-wide. **Determine the delta's UNIT before adopting it.**
5. Verify statics are readable offline at all: compare a runtime read at `sl+imm` against file bytes at `sl_fo+imm`.

Exit: a `target.toml` whose delta was validated corpus-wide, not on five hand-picked strings, and a `dis.py` listing where strings appear inline.
→ [reference/address-model.md](reference/address-model.md)

## Phase 2 — Build the string index

6. Run `strings_dump.py` over rodata for a full offset-to-text listing in the game's own encoding.
7. Decode the game's runtime text table if it has one; commit the id-to-text listing.
8. Run `msgid_scan.py` — the text accessor is the callee with the largest *distinct* id set.
9. Run `bind_strings.py` on 5-10 user-visible messages. Those are your first real code locations.

Exit: a committed offset-to-text listing, the id-consuming accessor identified, and a list of features that only exist with a live server (hand those to `server-revival`).
→ [reference/static-toolkit.md](reference/static-toolkit.md)

## Phase 3 — Map live state

10. Resolve the god object: dump `sl .. sl+0x800` and pick the slot that always holds a live heap pointer.
11. Find scalar fields by **differential RAM**: dump, make exactly one in-game change via scripted keys, dump, intersect the changed-offset sets across trials.
12. Find structs whose address you do not know by **shape**: match an invariant relation between fields, then find who points at the hit.
13. Confirm every candidate with `xref.py offset <imm>` (it covers **both** Thumb idioms) and satisfy Rule 2.
14. Anything you reimplement host-side gets **differentially verified**: make the emulator render both and diff the pixels. A mismatch is a bug, not an opinion.

Exit: god object resolved; every offset you intend to build on has two independent derivations.
→ [reference/probe-cli.md](reference/probe-cli.md) · [reference/recipes.md](reference/recipes.md)

## Phase 4 — Drive the guest

15. Locate the routine with the recipes: save serialiser, refusal gate, reward formula, UI art descriptor.
16. Run the **callability check** on every call target — a jump-table arm is not a function.
17. Re-read the original bytes at every patch site with `dis.py` immediately before implementing. Never implement from a single-source RE note.
18. Encode patches as read-verify-then-write helpers returning `Option<(addr, bytes)>`; compute the encoding in code; pin it with a selftest.
19. Need scratch or a thunk? Run `deadcode_sweep.py` (it **follows `bl`**) and guard the address with `const _: () = assert!(...)`.
20. Verify in the probe first, then on a device. Re-run the standalone selftests.

Exit: an idempotent patch that survives new-game/continue/death, and one host-initiated guest call chain completing on a device.
→ [reference/patching.md](reference/patching.md) · [reference/calling-guest-code.md](reference/calling-guest-code.md)

## Tools

All under `${CLAUDE_PLUGIN_ROOT}/tools/`, all take `-t target.toml`, all print **file offsets** so their output pastes straight back into the disassembler.

| run | question it answers |
|---|---|
| `dis.py <start_fo> <end_fo> [--resilient]` | annotated listing; resolves `[pc,#imm]` and `[sl,#imm]` to strings inline |
| `xref.py calls <addr> [--with-imm8-arg]` | who calls X, and with what constant argument |
| `xref.py offset <imm>` | who materialises struct offset K (both Thumb idioms) |
| `xref.py literal <v>` / `pcrel <addr>` / `slot <sl_off>` / `fnptr <addr>` | who loads this value / pool slot / static slot / function pointer (all six encodings) |
| `strings_dump.py [--encoding …]` | offset-to-text listing |
| `solve_reloc.py --known … --validate` | the relocation delta, then its corpus-wide score |
| `dump_table.py <sl_fo> <count>` | classify each static word: fn-ptr / data-ptr-to-string / plain |
| `msgid_scan.py --id-count N` | which function is the text accessor |
| `bind_strings.py --string-fo …` | string to the instructions that reference it |
| `deadcode_sweep.py` | proven-unreachable ranges (follows `bl`, honours pools and jump tables) |
| `thumb_enc.py 'subs r1,r3,#5' --verify-orig 0x1C19` | the halfword, plus bounds and liveness warnings |
| `frames_to_png.py <dir> [--montage out.png]` | probe frame dumps to PNG; geometry from the filename |
| `bruteforce_records.py` | length-prefixed record layout that consumes the file exactly |

The headless probe itself is a Rust crate in the template repo, not a plugin script — it must link the same patched core the app ships. Its contract is specified in [reference/probe-cli.md](reference/probe-cli.md).

## References

* [address-model.md](reference/address-model.md) — the four constants, by multiple independent routes, with validation
* [probe-cli.md](reference/probe-cli.md) — the headless-runner contract, gauges, parser traps
* [static-toolkit.md](reference/static-toolkit.md) — one section per question you can ask a binary
* [recipes.md](reference/recipes.md) — save serialiser · refusal gate · reward formula · scalar offset proof · UI art
* [patching.md](reference/patching.md) — read-verify-then-write, block skips, dead-code scratch
* [calling-guest-code.md](reference/calling-guest-code.md) — callability, three-state results, JVM attach, timeouts
* [asset-decoding.md](reference/asset-decoding.md) — assert `consumed == filesize`, stride vs visible width, brute force

Shared: `${CLAUDE_PLUGIN_ROOT}/shared/evidence-standard.md` (tags and confidence) · `${CLAUDE_PLUGIN_ROOT}/shared/selftest-convention.md` (dependency-free `include!` selftests) · `${CLAUDE_PLUGIN_ROOT}/shared/glossary.md` (RWPI, sl, ADF, BSS).

Provenance tags used throughout: **(A)** generic procedure · **(B)** worked example from one real port, re-derive yours · **(C)** never copy.
