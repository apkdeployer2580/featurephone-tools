# `featurephone-port` tools

Twenty-three command-line tools. Every one is a standalone Python 3.10+ script with no import of
any other tool except `target.py`, so a single file can be copied into a port's own repo if you
outgrow the plugin.

Invoke them as `python "${CLAUDE_PLUGIN_ROOT}/tools/<name>.py" …`. `${CLAUDE_PLUGIN_ROOT}` is the
plugin's install directory; it changes when the plugin updates, so never write state there.

## Contents

- [Shared conventions](#shared-conventions)
- [Dependencies](#dependencies)
- [`target.toml` — the per-title configuration hinge](#targettoml--the-per-title-configuration-hinge)
- [Address vocabulary](#address-vocabulary)
- [Archive tools](#archive-tools)
- [Static RE tools](#static-re-tools)
- [Probe and device tools](#probe-and-device-tools)
- [Protocol tools](#protocol-tools)
- [Build and publish tools](#build-and-publish-tools)
- [What is deliberately not a tool](#what-is-deliberately-not-a-tool)

## Shared conventions

These hold for every tool in this directory. They are contracts, not style preferences — the
skills' instructions rely on them.

**Forward slashes in every path, on every OS.** `work/client.bin`, never a backslash. Windows
Python accepts forward slashes everywhere; mixing separators breaks copy-paste between a skill's
instructions and a shell, and breaks the tools' own path joining in quoted arguments.

**`--apply` gates every destructive write.** Tools that modify a file default to writing a `.new`
sibling or printing a plan. `repack_member.py` needs `--apply` before it touches the original;
`prepare_archive.py` writes to an explicit `<dst>` and needs `--force` to overwrite;
`scaffold.py` refuses a non-empty `--dest` without `--force`. Where a tool overwrites in place it
also writes a `.orig` backup first, and refuses to re-apply over an existing backup without
`--force` — so a second run cannot destroy the pristine copy.

**`--dry-run` prints the plan and writes nothing** on every tool that writes.

**`--json` emits machine-readable output** on every tool that has structured results. The human
format is for reading; the JSON is the stable interface. Do not parse the human format.

**Exit non-zero on failure.** Every tool exits 0 only when it did the thing it claims. A
verification tool that finds a problem exits non-zero so it can be used as a build gate directly:
`verify_artifact_config.py` and `publish_audit.py` are designed to be the last line of a release
script. `replay_transcript.py` exits non-zero on any mismatch unless you pass `--no-strict`.
`extract_packets.py --expect SITE=HEX` exits non-zero when a reconstructed template fails to
reproduce a captured packet.

**No key defaults, ever.** No tool has a `--key` option, a built-in key, an embedded key table, or
a place to put one. Encrypted archive members are out of scope for this plugin. If you have a key,
handling it is yours to do privately.

**No baked-in title.** No tool has a default binary path, image base, static base, relocation
delta, packet id, or address. Everything per-title comes from `target.toml` or from an explicit
flag. A tool that lies because it kept someone else's constant is worse than a tool that refuses.

**Third-party dependencies are checked for and reported, never installed.** A tool that needs
`capstone` or `Pillow` prints the exact `pip install` line and exits non-zero. No tool ever shells
out to a package manager.

**`--selftest`** runs a tool's built-in arithmetic/decoder round-trip checks and exits. Present on
`dis.py`, `xref.py`, `thumb_enc.py`, `solve_reloc.py`, `strings_dump.py`, `msgid_scan.py`,
`deadcode_sweep.py`. Run it once after any local edit to a tool; it needs no target and no binary.

**Addresses are FILE OFFSETS by default.** Write `g:0x1234ab` to pass a guest address instead. The
tools print file offsets because that is what you paste back into the next tool and into a hex
editor. See [Address vocabulary](#address-vocabulary).

## Dependencies

| package | needed by | install |
|---|---|---|
| `capstone` | `dis.py`, `extract_packets.py` | `pip install capstone` |
| `Pillow` | `frames_to_png.py`, `gen_glyphs.py` | `pip install Pillow` |

Everything else is Python 3.10+ standard library. `target.py` uses `tomllib`, which is stdlib from
3.11; on 3.10 install `tomli` or upgrade. `publish_audit.py` shells out to `git` for `--all-refs`
and degrades to a working-tree-only scan when there is no repository.

## `target.toml` — the per-title configuration hinge

One file per title. Every static tool takes `-t target.toml` and reads its address model from
there, so the same tool works on any title and no tool carries a constant that belongs to a
different game. Generate a starter with `python target.py --schema > target.toml`, or let
`classify_archive.py --emit-target target.toml` pre-fill what it can infer from the archive.

```toml
# target.toml - everything that changes per title lives here and nowhere else.
# Values below are EXAMPLES of shape, not of content. Find your own.

binary       = "work/client.bin"
# REQUIRED. The extracted native guest image - the large binary member inside the
# game jar (KTF names it client.bin<bss>, LGT binary.mod, and so on). Path is
# resolved relative to THIS file, so a target.toml can be committed next to the
# work directory and still work from any cwd. Forward slashes.

image_base   = 0x100000
# REQUIRED. The address the emulator loads the image at. Do not guess it: read it
# out of your loader crate's load(data, BASE, ...) call. Guest address =
# file offset + image_base. Getting this wrong shifts every address you derive by
# a constant, which is the most convincing kind of wrong.

bss_size     = 0
# The zero-initialised region appended after the image. For KTF this is the integer
# SUFFIX OF THE NATIVE MEMBER'S FILENAME - the loader parses it out of the name,
# which is why renaming that member breaks the archive. classify_archive.py reports
# it. Used to bound "is this address inside the image, in bss, or on the heap".

# sl_fo      = 0x0
# The RWPI static base, as a FILE OFFSET. Position-independent ARM keeps the static
# data base in r9/sl, and Thumb-1 code addresses statics as [sl,#imm]. Derive it by
# three independent routes and require agreement before you write it down; a wrong
# sl makes dump_table.py print plausible garbage. Optional until you need statics.

# reloc_delta = 0x0
# Add this to a pointer word STORED IN THE IMAGE to recover what it points at. An
# RWPI image's stored pointers are biased by the difference between link-time and
# load-time layout. Recover it with solve_reloc.py --known ... --validate, never by
# eyeballing five hand-picked strings. Optional until you follow a stored pointer.

# reloc_unit = "file_offset"
# REQUIRED WHENEVER reloc_delta IS SET. Whether the recovered value is a FILE OFFSET
# or a GUEST ADDRESS. This is the single highest-consequence field in the file: get
# the unit wrong and every tool that follows a pointer lies to you consistently, by
# exactly image_base. target.py --check infers it corpus-wide and tells you when the
# declared unit disagrees with the evidence.

encoding     = "euc-kr"
# Text encoding of the game's own strings. euc-kr | shift_jis | gbk | big5 |
# cp1252 | utf-8. Legacy Korean titles are usually euc-kr, Japanese usually
# shift_jis - but verify by decoding, do not assume by region. strings_dump.py
# --encoding overrides this for one run while you are deciding.

isa          = "thumb"
# Default decode mode: thumb | arm. AOT-compiled feature-phone images are
# overwhelmingly Thumb-1, and the tools' idiom recognition assumes Thumb unless
# told otherwise. dis.py --isa arm overrides per invocation.

# god_object = 0x0
# Optional. The GUEST ADDRESS of the static slot holding the pointer to the main
# game-state object. Once you have proven it, the tools can annotate loads through
# it. Leave it out until you have two independent derivations of it.
```

Verify a file before you build on it:

```bash
python target.py target.toml --check          # sanity report + corpus-wide reloc_unit inference
python target.py target.toml --fo 0x14654e    # explain a file offset
python target.py target.toml --guest 0x24654e # explain a guest address
python target.py target.toml --word 0xab1fc   # explain a stored pointer word
python target.py target.toml --sl-slot 0x444  # explain the static slot at [sl,#0x444]
```

## Address vocabulary

Three coordinate systems, and every tool states which one it is printing.

| term | meaning |
|---|---|
| **file offset** | Byte index into `binary`. The default input and output format of every tool. What you paste into a hex editor. |
| **guest address** | `file offset + image_base`. What the CPU sees, what a disassembler's branch target shows, what a debugger breakpoint takes. Pass one by writing `g:0x…`; print them with `dis.py --guest`. |
| **stored word** | A 32-bit pointer as it literally appears inside the image, biased by `reloc_delta`. Never usable directly — add the delta first, then interpret in `reloc_unit`'s coordinate system. |

## Archive tools

### `classify_archive.py`

```
classify_archive.py [-h] [--json] [--list] [--encoding ENC]
                    [--extract-native PATH] [--emit-target PATH]
                    archive
```

Detects the platform backend of a dump (`.zip` / `.kdp` / `.jar` / `.jad`) from its magic entry
and reports every name-agreement problem that will stop it booting: descriptor keys, AID versus
jar basename, the native member's name and the BSS value parsed out of it, and the CR count in the
descriptor. `--extract-native PATH` writes the guest image out for the RE tools;
`--emit-target PATH` writes a starter `target.toml` pre-filled with what it could infer. Read-only;
never modifies the input. Deps: none.

### `prepare_archive.py`

```
prepare_archive.py [-h] [--strip-private] [--synthesize-descriptor]
                   [--set-aid X] [--set-pid Y] [--set-mclass Z]
                   [--dry-run] [--force] [--encoding ENC] [--json]
                   src dst
```

Repairs a dump into a bootable archive and **proves** that everything it did not intend to change
came through byte-identical, member by member. Normalises the descriptor to LF, reconciles AID /
jar basename / native member name, strips `P/*` and save-looking entries (someone else's private
data), and can synthesize a minimal descriptor or wrap a bare `.jar` when there is none. Writes
`<dst>`; needs `--force` to overwrite an existing one. Deps: none.

### `repack_member.py`

```
repack_member.py [-h] [--member SPEC] [--transform MOD:FN] [--verify MOD:FN]
                 [--apply] [--backup-dir D] [--out PATH] [--allow-noop]
                 [--force] [--list [SPEC]] [--json]
                 archive
```

Surgical nested rewrite of exactly one member. `--member "<inner>.jar!dat/<file>"` names a path
through nested containers with `!`; `--transform mymod:fn` supplies `bytes -> bytes`. Rebuilds
every archive on that path entry by entry, preserving each member's compression type, timestamp
and attributes, then verifies every other member is byte-identical. Writes a `.new` sibling unless
`--apply`, which backs the original up first. `--list [SPEC]` enumerates members at any level.
Deps: none.

### `bruteforce_records.py`

```
bruteforce_records.py [-h] [--field NAME:TYPE] [--prefix {u8,u8len,u16le,u16be}]
                      [--trailer A..B] [--header A..B] [--encoding ENC]
                      [--min-records N] [--auto] [--show N] [--all] [--json]
                      file
```

Finds the record layout that consumes an unknown length-prefixed data file **exactly**. You
describe the field sequence (`--field name:str --field price:u32le`, repeatable; types `str`,
`u8`, `i8`, `u16le/be`, `u32le/be`, `i32le/be`, `bytes:N`) and give ranges for the unknown header
and trailer sizes; it reports the layouts where consumed bytes equal the file size. `--auto` tries
common templates when you have no hypothesis yet. The exact-consumption assertion is the whole
point: a layout that decodes "most" of a file is a layout that is wrong. No key handling —
encrypted members are out of scope. Deps: none.

## Static RE tools

All take `-t target.toml`, accept file offsets by default and guest addresses as `g:0x…`, and
support `--json`.

### `dis.py`

```
dis.py [-h] [-t TARGET] [--out OUT] [--resilient] [--isa {thumb,arm}]
       [--guest] [--binary BINARY] [--selftest]
       [start] [end]
```

Annotated disassembly printed in **file offsets**, with `[pc,#imm]` literal loads, `[sl,#imm]`
static slots and branch targets resolved inline — the three lookups you would otherwise chase by
hand. `end` may be written `+0xLEN`. **Use `--resilient` by default**: it emits `.hw 0xNNNN` and
advances two bytes on undecodable data instead of stopping, which matters because AOT images
interleave literal pools with code. Deps: `capstone`.

### `xref.py`

```
xref.py [-h] [-t TARGET] [--binary BINARY] [--selftest]
        {calls,literal,pcrel,offset,fnptr,slot} ...

  calls   <addr> [--with-imm8-arg]   who calls this function (BL/BLX)
  literal <value>                    who loads this 32-bit VALUE
  pcrel   <addrs...>                 who loads from these literal-pool SLOTS
  offset  <imm>                      who materialises this struct offset (both Thumb idioms)
  fnptr   <addr>                     where is this function STORED (all encodings)
  slot    <sl_off>                   who computes sl + this offset (0x444, sl+0x444, sl-0xb430)
```

Brute-force decode of the whole image, roughly a second, no CFG and no symbols. Six subcommands
for the six questions that actually come up. `calls --with-imm8-arg` additionally recovers the
`movs rN,#imm8` set just before each call, which is how you find "who calls X with constant N".
`offset` checks **both** Thumb idioms for materialising a struct offset — checking only one is a
classic source of a false negative that costs an afternoon. Deps: none.

### `strings_dump.py`

```
strings_dump.py [-h] [-t TARGET] [--range A:B] [--encoding ENCODING]
                [--min-len MIN_LEN] [--grep GREP] [--asciiz-only]
                [--out OUT] [--json] [--binary BINARY] [--selftest]
```

Offset ↔ text listing in the game's own legacy encoding. Prints file offset, guest address, the
**relocation word** you would search for next, and the text. `--min-len` counts decoded
*characters*, not bytes. The reloc-word column is what makes this the first tool in most chains:
it hands you the exact value to feed `xref.py literal`. Deps: none.

### `solve_reloc.py`

```
solve_reloc.py [-h] [-t TARGET] [--known KNOWN] [--auto-known N] [--validate]
               [--near-miss] [--top TOP] [--corpus-min CORPUS_MIN]
               [--max-abs-delta MAX_ABS_DELTA] [--unaligned] [--json]
               [--out OUT] [--binary BINARY] [--selftest]
```

Recovers the relocation delta by intersecting candidates over strings you can recognise
(`--known 0x…,0x…,…`, five or more recommended), then **validates corpus-wide** against a
no-bias control and three random controls, scoring by referenced fraction. Solve-then-validate is
not optional: a delta that explains five hand-picked strings and nothing else is a coincidence,
and it will be wrong in a way that stays plausible for a long time. `--auto-known N` guesses
landmarks and always reports a ranking, never a proof. Deps: none.

### `dump_table.py`

```
dump_table.py [-h] -t TARGET [--json] [--out OUT] [--only ONLY]
              [--binary BINARY]
              start count
```

Prints `count` consecutive 32-bit words from `start` (a file offset, or `sl+0x…` / `sl-0x…`) and
classifies each: Thumb function pointer, data pointer that relocates to a decodable string, or
plain value. The fastest way to read an unknown RWPI static area — and, run over a region you
already understand, the check on whether your `sl_fo` is right at all. Deps: none.

### `msgid_scan.py`

```
msgid_scan.py [-h] [-t TARGET] [--id-count ID_COUNT] [--top TOP]
              [--sites-for SITES_FOR] [--window WINDOW] [--reg REG]
              [--stop-at-branch] [--min-distinct MIN_DISTINCT] [--json]
              [--binary BINARY] [--selftest]
```

Finds the id-consuming accessor — `getText(id)`, `showMessage(code)`, `playSound(id)` — in a
stripped image, by ranking callees on how many **distinct** small constants they are called with.
`--sites-for 0x87` then prints every call site passing that id, which is how a message id becomes
a list of code locations. Deps: none.

### `bind_strings.py`

```
bind_strings.py [-h] -t TARGET [--string-fo STRING_FO] [--string-text STRING_TEXT]
                [--json] [--out OUT] [--limit LIMIT] [--binary BINARY]
```

The whole chain from a string to the code that uses it: string → relocation word → `.data` pool
slot → the `sl`-offset hop that Thumb-1 RWPI code actually uses → the referencing instructions.
`--string-text` finds the string by content first. In a symbol-less image, text is the only thing
that is already named, so this is usually the first tool of a session. Deps: none.

### `deadcode_sweep.py`

```
deadcode_sweep.py [-h] [-t TARGET] [--entry ENTRY] [--min-len MIN_LEN]
                  [--jt-min JT_MIN] [--code-start CODE_START] [--code-end CODE_END]
                  [--static-start STATIC_START] [--report-jumptables] [--top TOP]
                  [--json] [--out OUT] [--binary BINARY] [--selftest]
```

Reachability sweep reporting byte ranges that are neither executed nor read — the prerequisite for
using a range of the guest image as host-owned scratch memory. **Follows `bl`**, because Thumb-1
routinely uses `bl` as a long branch; honours literal pools; and cross-checks blind branch scans,
stored code pointers and jump-table bases. The first version of this sweep in the reference
implementation did not follow `bl` and was therefore wrong; only the corrected one ships. Treat
its output as a candidate list to be confirmed, and pin any range you adopt with a compile-time
assertion in your own source. Deps: none.

### `thumb_enc.py`

```
thumb_enc.py [-h] [--verify-orig VERIFY_ORIG] [--decode DECODE] [-t TARGET]
             [--at AT] [--json] [--binary BINARY] [--selftest]
             [expr]
```

Encodes one Thumb instruction to the exact halfword(s) a host-side patch should write — e.g.
`"subs r1,r3,#5"`, `"movs r1,#25"`, `"b <from> <to>"` — and decodes what is currently there.
`--verify-orig` compares against the halfword you believe is at the site; with `-t` and `--at`,
it reads the current bytes out of the image and checks them for you. It also prints the
**immediate width bound**, which is load-bearing: a 3-bit `imm3` or an 8-bit `imm8` is what
actually determines the range your feature can have, and discovering that after you designed the
feature is a rewrite. Deps: none.

## Probe and device tools

### `frames_to_png.py`

```
frames_to_png.py [-h] [--glob GLOB] [--out-dir OUT_DIR] [--montage OUT.PNG]
                 [--cols COLS] [--pad PAD] [--scale SCALE] [--geometry WxH]
                 [--format {argb,bgra,rgb555,rgb565,rgb888,rgba}] [--stride STRIDE]
                 [--require-nonblack] [--json]
                 directory
```

Converts a headless probe's raw framebuffer dumps to PNG, and optionally builds one contact sheet
of the whole run. Geometry and pixel format are parsed from the **dump filename**, so the normal
invocation is just the directory — which is the reason the probe should put geometry in the
filename in the first place. `--require-nonblack` makes it a gate: exits non-zero if every frame
is black, which is exactly the chapter-4 exit criterion. Deps: `Pillow`.

### `gen_glyphs.py`

```
gen_glyphs.py [-h] [--words WORDS] [--words-file WORDS_FILE] --font FONT
              [--font-index FONT_INDEX] [--size SIZE] [--cell-w CELL_W]
              [--cell-h CELL_H] [--threshold THRESHOLD] [--valign {center,top,bottom}]
              [--dx DX] [--dy DY] [--skip SKIP] [--skip-ascii]
              [--module-doc MODULE_DOC] --out OUT [--preview [OUT.PNG]]
              [--allow-clipped]
```

Generates a closed-set CJK bitmap glyph table as a Rust source file, for a host renderer whose
built-in font is ASCII only. The character set is derived from the exact words the host draws
(`--words`, quoted), which keeps the table small enough to be a `const`. **Renders directly at the
target size** — oversampling and downscaling produces mush at 12px. Emits a `covers()` predicate
so a missing glyph is a test failure rather than a hole on screen, and fails on clipped ink unless
you pass `--allow-clipped`. Deps: `Pillow`.

### `adb_drive.py`

```
adb_drive.py [-h] [--serial SERIAL] [--adb ADB] --package APP_ID
             [--activity COMPONENT] --script SCRIPT [--out-dir OUT_DIR]
             [--logcat-grep TAG] [--logcat-out LOGCAT_OUT] [--screencap OUT.PNG]
             [--boot-wait BOOT_WAIT] [--no-force-stop] [--dry-run] [--verbose]
```

One reproducible device run: clear logcat, force-stop for a cold start, launch, replay a scripted
sequence of taps from `--script`, capture filtered logcat and a screenshot. `--serial` defaults to
`$ANDROID_SERIAL`; `--adb` to `$ADB`, then `$ANDROID_HOME`, then `PATH`. Tap coordinates are
**data, not code**, because they depend on the device resolution *and* on your port's own layout
arithmetic — the same script on a different phone is meaningless, and `--help` says so. Deps: none
(needs `adb` on the host).

## Protocol tools

Only needed for online titles. Offline ports never touch these.

### `extract_packets.py`

```
extract_packets.py [-h] -t TARGET [--binary BINARY] --flush FLUSH
                   [--put-u8 PUT_U8] [--put-bytes PUT_BYTES] [--put-u32le PUT_U32LE]
                   [--put-u32be PUT_U32BE] [--guest-addrs] [--val-reg VAL_REG]
                   [--len-reg LEN_REG] [--max-back MAX_BACK] [--loose-blocks]
                   [--expect SITE=HEX] [--out OUT] [--json]
```

Enumerates every request the guest can send, statically, by anchoring on the send-side
serializer's flush routine: every `bl` to flush is one request; walk back to the prologue
reconstructing the append calls. **Resets the template at every branch target**, which yields
omissions rather than fabrications — a partial template you know is partial beats a complete one
you cannot trust. `--loose-blocks` relaxes that at a stated fabrication risk. `--expect SITE=HEX`
self-validates a reconstructed template against a captured packet and exits non-zero on
disagreement. Groups results by leading constant byte, which is how request *categories* fall out.
Deps: `capstone`.

### `name_requests.py`

```
name_requests.py [-h] -t TARGET [--binary BINARY] [--strings STRINGS]
                 [--string-fo FO[=LABEL]] --flush FLUSH [--window WINDOW]
                 [--guest-addrs] [--max-slots MAX_SLOTS] [--max-literals MAX_LITERALS]
                 [--cluster-bits CLUSTER_BITS] [--out OUT] [--json]
```

Turns a catalogue of hex templates at unnamed addresses into a list of named operations, by
chaining a localized UI status string to its nearest send site — **without triggering the
request**, which matters when the server is dead and the feature is one-shot. `--strings` takes
the output of `strings_dump.py`. Every result carries an explicit MEDIUM confidence label:
proximity is evidence, not proof, and the tool refuses to pretend otherwise. Deps: none.

### `replay_transcript.py`

```
replay_transcript.py [-h] --handler MODULE:ATTR [--sys-path DIR] [--session SESSION]
                     [--limit LIMIT] [--replay-masked] [--emit-test OUT.PY]
                     [--no-strict] [--stats] [--json]
                     transcript
```

Replays a captured JSONL transcript through your replacement server's
`handle(bytes) -> list[bytes]` handler, asserting it produces the same replies the real server
did, and `--emit-test` writes a standalone conformance-test skeleton from that transcript. Exits
non-zero on any mismatch unless `--no-strict`. `--stats` prints inter-frame gap statistics, which
is how you derive a defensible idle timeout from measured client behaviour instead of guessing
one. `--handler` is why the architecture insists on a pure `handle(bytes) -> list[bytes]` seam.
Deps: none.

## Build and publish tools

### `scaffold.py`

```
scaffold.py [-h] --dest DEST --name NAME --display DISPLAY --app-id APP_ID
            --env-prefix ENV_PREFIX --gradle-prefix GRADLE_PREFIX
            --archive-name ARCHIVE_NAME --platform {ktf,lgt,skt,j2me}
            [--screen WxH] [--template-repo TEMPLATE_REPO] [--template-ref TEMPLATE_REF]
            [--offline-template DIR] [--no-server] [--extra-substitution OLD=NEW]
            [--force] [--dry-run]
```

Generates a new port project from a **pinned tag** of the template repository, substitutes every
parameter with word-boundary replacements (never a blind string replace), then **verifies**: no
`__TEMPLATE_*__` token survived, the three native-library names agree, and the Java namespace was
not changed. It prints the parameter table it applied and seeds a notes file with the chapter
checklist and empty slots for the four address-model constants.

This is a tool rather than a paragraph of instructions because the rename blast radius is large
and several of its failures are *runtime* failures with a perfectly clean build — a mismatched
native-library name, or a moved Java namespace that Rust resolves by name over JNI. A human gets
this 95% right and then debugs an `UnsatisfiedLinkError` for an afternoon. `--offline-template`
uses a local checkout; `--template-ref` should be a tag, since a branch is not reproducible. Deps:
none (needs `git` unless `--offline-template`).

### `verify_artifact_config.py`

```
verify_artifact_config.py [-h] [--require-string TEXT] [--forbid-string TEXT]
                          [--require-file PATH] [--ignore-case] [--list-strings]
                          [--grep SUBSTR] [--min-len MIN_LEN] [--json]
                          artifact
```

Scans a produced `.so` / `.exe` / `.dylib` / `.a` for the configuration strings the build was
supposed to bake into it, and exits non-zero when one is missing or a forbidden one is present.

The failure it defends against is specific and silent: build-time configuration macros resolve at
compile time, so if the environment variable is not exported when the compiler runs, you get a
fallback value and **no error** — a release build that quietly talks to a development default.
Checking the build script does not catch this. Checking the artifact does. Generalised rule:
verify build-time configuration in the artifact, never in the script that was supposed to set it.
Deps: none.

### `publish_audit.py`

```
publish_audit.py [-h] [--repo REPO] [--all-refs]
                 [--fail-on {info,low,medium,high,critical}]
                 [--blob-threshold BLOB_THRESHOLD] [--max-blob-bytes MAX_BLOB_BYTES]
                 [--max-refs MAX_REFS] [--allow-ip ALLOW_IP] [--allow-hex ALLOW_HEX]
                 [--allow-email ALLOW_EMAIL] [--allowlist ALLOWLIST]
                 [--show-secrets] [--json]
```

Sweeps the working tree — and with `--all-refs`, **every git ref** — for private keys, keystores,
64-hex certificate pins, public IPv4 literals, email addresses, absolute user paths, device
serials, account stores and transcripts, packaged APKs, game-archive blobs, and known cloud-token
shapes. Reports which refs each hit is reachable from.

`--all-refs` is the only mode that tells the truth about what a clone of your repository would
contain: deleting a file from the working tree does not delete it from history, and a port
accumulates exactly these categories quietly, without anyone deciding to. Run it before the first
push and again before every release. `--show-secrets` prints matched values in full instead of
masking them — never in CI logs. Deps: none (needs `git` for `--all-refs`).

## What is deliberately not a tool

Four things in this methodology cannot be generalized into a script, and the skills say so
explicitly rather than shipping something that pretends:

| item | why not | what ships instead |
|---|---|---|
| The headless probe itself | It is a Rust crate that must link the *same patched core* your app ships and `include!` your port's own renderer source for differential verification. A Python script can do neither. | `guest-re/reference/probe-cli.md` specifies the full CLI contract; the template repo ships the crate. |
| The native-stack watermark gauge | Measuring reserved stack is an OS-specific syscall — a different one per host. | The idea ("measure the thing that overflows, from outside the code that uses it") is documented; the template ships both implementations behind one function. |
| Sprite and asset decoders | The format is per-title, sometimes per-title-series. | `guest-re/reference/asset-decoding.md` teaches the method — assert `consumed == filesize`, distinguish padded stride from visible width, brute-force the trailer — and `bruteforce_records.py` handles the tractable case. Decoders live in your repo. |
| Anything involving an extracted decryption key | Publishing a key that decrypts a copyrighted work is a materially different act from publishing an offset. | Nothing. Encrypted members are out of scope; no tool has a key option or a key default. |
