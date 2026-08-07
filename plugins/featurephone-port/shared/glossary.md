# Glossary

Vocabulary used by every skill in this plugin. Written for someone who has never opened a
feature-phone binary. One line per term; where a term has a trap attached, the trap is the
second sentence.

Provenance tags used throughout the plugin — **(A)** generic procedure, **(B)** worked example
from the reference port, **(C)** do not copy — are defined in
[evidence-standard.md](evidence-standard.md).

## Contents

- [Platforms and carriers](#platforms-and-carriers)
- [The archive and its descriptor](#the-archive-and-its-descriptor)
- [The guest binary and its addressing model](#the-guest-binary-and-its-addressing-model)
- [Emulator internals](#emulator-internals)
- [Method vocabulary](#method-vocabulary)
- [Two words that mean the opposite of what you expect](#two-words-that-mean-the-opposite-of-what-you-expect)

---

## Platforms and carriers

**WIPI** — *Wireless Internet Platform for Interoperability*, the middleware standard mandated on
Korean feature phones from the mid-2000s. It defines both a C API (see **WIPI-C**) and a Java API,
so a WIPI title may be native ARM code, Java bytecode, or Java that was compiled to ARM at
packaging time. "It is WIPI" therefore does not yet tell you what you are emulating.

**KTF** — Korea Telecom Freetel, one of the three Korean carriers, and the WIPI flavour the
reference port targets. Detected by an `__adf__` entry in the outer zip, or by a jar whose root
holds a member named `client.bin<digits>`.

**LGT** — LG Telecom, another Korean carrier's WIPI flavour. Detected by an `app_info` entry in the
outer zip, or by a `binary.mod` member inside a bare jar. Its descriptor is parsed by the same
three-key line scanner as KTF's, so it inherits KTF's CRLF fault.

**SKT** — SK Telecom, the third Korean carrier. Detected by any `*.msd` entry in the archive, or by
a bare jar whose first eight bytes are the SKVM magic. Its descriptor parser trims whitespace, so
the CRLF fault does **not** apply to it — a useful reminder that "the platform" is really "the
loader's parsing habits".

**SKVM** — the virtual machine SKT phones ran. In this plugin "SKT" names the archive format and
"SKVM" names the runtime; they travel together.

**MIDP / J2ME** — *Java 2 Micro Edition* plus the *Mobile Information Device Profile*: the
international (non-Korean) feature-phone Java stack. A J2ME guest is `.class` bytecode, never
AOT-compiled ARM, so the static ARM toolkit in `guest-re` does not apply to it.

**MIDlet** — a J2ME application class extending `javax.microedition.midlet.MIDlet`, declared by a
`MIDlet-1:` attribute in the jar manifest or in the JAD. Presence of that attribute is the J2ME
tell; absence of it in a jar full of game data is the tell that you have a carrier-specific title.

---

## The archive and its descriptor

**JAD / JAR** — JAD is the plain-text *Java Application Descriptor*, the J2ME analogue of an ADF;
JAR is the zip that holds the application. A J2ME dump is normally the pair, and the loader is
selected by which one you hand it.

**ADF** — *Application Descriptor File*: the `__adf__` member of a KTF archive. Plain text,
`Key:Value`, one per line, in the game's own encoding. The emulator reads exactly three keys —
AID, PID, MClass — and ignores everything else the carrier put there (icons, catalog blobs,
display size, vendor).

**AID** — *Application ID*. It is not an opaque token: it **is the jar's basename**. The loader
composes `<AID>.jar` and opens that name, by exact match, in the virtual filesystem. If the
descriptor says one thing and the zip entry says another, boot dies with a file-not-found panic
*after* the emulator has already reported that it loaded.

**PID** — *Provider/Product ID*. It becomes the **save namespace**: records are written under
`<root>/<PID>/db/<name>/<record-id>`. Choose it once. Changing it after release makes every
player's saves unreachable while leaving the app perfectly functional.

**MClass** — the descriptor's main-class name. The loader converts `.` to `/` and asks the class
loader for it. Nothing else in the archive carries this information, which is why a bare jar with
no descriptor loads and then fails with a main-class error.

**`client.bin<N>`** — the KTF convention for the native guest image inside the jar: the numeric
suffix is the **BSS size in bytes**. Renaming the member to something tidier strips a load-bearing
value and the loader fails while trying to parse an empty integer.

---

## The guest binary and its addressing model

**AOT-compiled guest** — a jar containing **no `.class` files** and one large native member. The
Java was compiled ahead of time to ARM machine code at packaging time. This single observation
decides the whole project: you are emulating a CPU, not a bytecode VM, and every reverse-
engineering technique in `guest-re` becomes relevant.

**ARM / Thumb** — the two instruction encodings of 32-bit ARM. ARM instructions are 4 bytes; Thumb
instructions are mostly 2 bytes and are what feature-phone compilers emit for size. The **low bit
of any branch target or function pointer selects the mode**: 1 = Thumb, 0 = ARM. This is why every
callable guest address in host code is OR'd with 1, and why a function-pointer table search must
try both the even and the odd form of each candidate.

**IMAGE_BASE** — the guest address at which the loader maps the native image. It is a property of
*the loader*, not of the binary: read it out of the emulator's platform crate, at the
`load(data, BASE, …)` call. Once you have it, `guest address = file offset + IMAGE_BASE`, and that
identity is the backbone of every static tool.

**BSS** — zero-initialised writable statics that exist at runtime but occupy no bytes in the file.
The loader appends BSS after the image, so the guest's valid address range is
`[IMAGE_BASE, IMAGE_BASE + file_len + bss_len)`. **An address past the end of the file is not
automatically invalid** — that mistake makes a live global look like garbage.

**RWPI** — *Read-Write Position Independent*, the ARM compiler model these binaries are built with.
Static and global data is addressed as `[sl, #imm]` instead of by absolute address, so there are
**no absolute data addresses in the instruction stream at all**. Every "find who touches this
global" question becomes "find who computes this `sl` offset".

**sl (static base)** — register r10 (spelled `sl`; some builds use r9, spelled `sb`), holding the
base of the RWPI static area. Two consequences worth memorising: (1) each function normally sets
`sl` up itself from a PC-relative literal, which is exactly what makes it safe for a host to call a
guest function cold; (2) if the static area lies inside the loaded file, a static slot is readable
*offline* at file offset `sl_fo + imm`, which is what lets a disassembler annotate strings inline.

**relocation delta** — pointers stored in the image's data section are stored biased: the word
holds `target − delta`, and the loader (or the guest's own self-relocation entry) fixes them up at
load time. You recover the target by adding the delta back. **Determine the delta's unit before you
adopt it** — in some images `word + delta` yields a *file offset* and you must still add
IMAGE_BASE; in others it yields a guest address directly. Getting the unit wrong makes every tool
lie consistently, which is the hardest kind of wrong to notice.

**framebuffer** — the block of guest memory the game rasterises pixels into, described by a small
struct (width, height, bytes-per-line, bits-per-pixel, buffer pointer). Feature-phone titles are
usually 16 bpp RGB565. The internal consistency relation `bytes_per_line == width * 2` is what
makes a shape-based search for framebuffers in an unknown heap near-zero-false-positive.

---

## Emulator internals

**WIPI-C** — the C half of the WIPI API. The guest reaches it by reading a **function pointer out
of a table the emulator allocated in guest memory** and branching to it; the emulator publishes
each entry as a small stub that traps back to Rust. Two consequences: slot *position* is the ABI
(insert a field in the middle and every later call goes to the wrong function), and a table shorter
than the highest slot the guest indexes is read past its allocation, yielding a branch to address
zero rather than a clean error.

**JVM support layer** — for a KTF title the emulator runs a Java runtime *alongside* the ARM core:
emulator-provided WIPI/MIDP classes registered from Rust, plus the game's own classes resolved
lazily out of the native image by a class loader that calls into the guest. Knowing which of those
two paths a missing name is on decides whether you see a hard error or a silently swallowed
`ClassNotFoundException`.

**the god object** — the single large heap struct that a feature-phone RPG hangs essentially all
live state off: character stats, inventory, current map, UI flags. The host finds the one static
slot that always holds a live heap pointer, dereferences it once, and reads everything else at
fixed offsets from there. Every title has one; it has a different name in every title, so these
docs call it "the god object" and its static slot "the god-object slot".

**virtual clock** — a host clock advanced by a fixed number of milliseconds **per frame** instead of
reading wall time. Mandatory for headless runs: the guest does not busy-loop, it arms a timer and
yields, so a harness that ticks as fast as it can with a real clock never expires a single timer and
renders a permanently black screen. It also makes runs bit-reproducible.

**the probe** — the headless runner: a full `Platform` implementation with no window, no audio, a
virtual clock, a memory or file-backed record store, scripted key input, frame dumps and always-on
heap/work/stack gauges. It is how you test without a phone, how you measure, and — because it can
`include!` the port's own shipping source — how you verify a host reimplementation against the
guest itself.

**trace stub** — a fatal "unimplemented API" stub replaced by a **logging no-op that returns a
plausible value**, so the guest keeps walking and the whole call sequence lands in the log with its
arguments. It converts "the game stops at the first missing function" into "the game tells you its
entire API contract in one run".

---

## Method vocabulary

**differential verification** — running your host reimplementation *and* the guest's own routine in
the same process and diffing the outputs byte-for-byte or pixel-for-pixel. A mismatch is then a bug
in your reimplementation and not a matter of opinion. The rule that gives it teeth: the check must
exercise the **shipping** source (via `include!`), because a check that exercises a copy proves
nothing about what ships. See [selftest-convention.md](selftest-convention.md).

**read-verify-then-write patch** — a host-side guest-memory patch that reads the current bytes
first, writes only if they equal the documented original, and returns "nothing to do" once applied.
Idempotent, safe against a different build of the game, and safe to re-assert every frame.

**two independent derivations** — the plugin's standard for "this offset is confirmed": two routes
to the same constant that do not share an assumption. Defined, with worked examples and
counter-examples, in [evidence-standard.md](evidence-standard.md).

**refutation block** — a disproved claim kept in the documentation *next to its disproof*, rather
than deleted. Deleting a refuted claim guarantees someone re-derives it and re-ships the bug.

**closed-set glyph generation** — the technique for CJK text in a host overlay whose font is ASCII:
enumerate the finite set of strings the host itself draws, derive exactly the syllables those need,
generate a constant bitmap table for them, and assert coverage in a test. A full CJK font is
unshippable; fifteen glyphs are free.

---

## Two words that mean the opposite of what you expect

**"loaded"** — in the boot log, this means the emulator constructed successfully. It says nothing
about whether the game will run: the archive faults that kill a boot fire on the async boot task
*after* that line prints. Never read `loaded` as success.

**"the fixed archive"** — in this methodology, "fixed" does not mean patched, modded or improved.
It means the descriptor's line endings were normalised and the entry names were made to agree.
See `archive-prep`.
