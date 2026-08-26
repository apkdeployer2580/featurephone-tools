# Generated assets: several tools, one archive

The shipped archive is a **build product with more than one producer**. This file is about what
happens when two or more generators write it — who owns which member, in what order they run, and
the one gate that makes the answer checkable instead of remembered. Read it before you add a second
tool to the pipeline, and run its check gate as the first command of any session that touches
assets. The single-tool mechanics (the rebuild primitive, per-member byte identity) live in
[repacking.md](repacking.md); the on-disk record layouts live in
[record-formats.md](record-formats.md).

Tags: **(A)** generic · **(B)** worked example from the reference port · **(C)** do not copy.

## Contents

1. [The question this file answers: who owns which member](#1-the-question-this-file-answers-who-owns-which-member)
2. [Two generators, one archive — the ordering rule](#2-two-generators-one-archive--the-ordering-rule)
3. [The structural fix: a byte-comparing `--check` that exits nonzero](#3-the-structural-fix-a-byte-comparing---check-that-exits-nonzero)
4. [Member ownership: compare only what one tool solely owns](#4-member-ownership-compare-only-what-one-tool-solely-owns)
5. [The canary member](#5-the-canary-member)
6. [Always rebuild from pristine](#6-always-rebuild-from-pristine)
7. [Where by-products live, and why pristine is moved rather than deleted](#7-where-by-products-live-and-why-pristine-is-moved-rather-than-deleted)
8. [Gitignored artifacts do not travel with commits](#8-gitignored-artifacts-do-not-travel-with-commits)
9. [The asset ↔ guest-constant pair: two halves, two routes](#9-the-asset--guest-constant-pair-two-halves-two-routes)
10. [The framing invariant: preserve what the parser scans for](#10-the-framing-invariant-preserve-what-the-parser-scans-for)
11. [Verification: the last shipped build, then the packaged artifact](#11-verification-the-last-shipped-build-then-the-packaged-artifact)
12. [Every generator gets a `--check`](#12-every-generator-gets-a---check)
13. [Generate from the other implementation's own objects](#13-generate-from-the-other-implementations-own-objects)
14. [Generated code a selftest `include!`s](#14-generated-code-a-selftest-includes)
15. [Checklist](#15-checklist)

---

## 1. The question this file answers: who owns which member

**(A)** With one tool, "did the edit land?" is answered by the tool's own verification report. With
two, it is not answerable from any single report, because **both tools print a clean report in every
failure mode described below.** The pipeline needs one invariant instead:

> **For every member of the shipped archive, exactly one tool is its producer, that ownership is
> written down in the tool, and one command re-derives the whole archive and byte-compares.**

Everything else in this file follows from that sentence. The failures it prevents:

| symptom | cause | § |
|---|---|---|
| A tool reports VERIFY OK, all members byte-identical, and the shipped archive still carries the **old** text — or another member has reverted to its pristine value | two generators, wrong order; the rebuild-from-pristine tool discarded the in-place tool's work, or never ran | [2](#2-two-generators-one-archive--the-ordering-rule) |
| A change you committed and documented is **not in the built artifact**, and every tool printed OK | the archive is gitignored, so the bytes never left the machine that baked them | [8](#8-gitignored-artifacts-do-not-travel-with-commits) |
| Title screen appears, then a **black screen**; the log shows an allocation failure with a **negative size** in the argument register, at a platform call unrelated to any asset | a generated data table and a guest constant disagree about the row count | [9](#9-the-asset--guest-constant-pair-two-halves-two-routes) |
| The release package grew by roughly the size of the game archive, with no content change | a backup or dry-run output sits inside the directory the packager consumes wholesale | [7](#7-where-by-products-live-and-why-pristine-is-moved-rather-than-deleted) |
| The transform dies with `expected N records, got N+1 — refusing` on a run that should have worked | the pristine copy was deleted, so the tool is reading its own previous output as its source | [7](#7-where-by-products-live-and-why-pristine-is-moved-rather-than-deleted) |
| An in-game one-line description is blank for **some** items, a different subset every session, while the detail popup is fine | a text edit removed a byte the renderer scans for, unbounded | [10](#10-the-framing-invariant-preserve-what-the-parser-scans-for) |

## 2. Two generators, one archive — the ordering rule

**(B) The shape, from the reference port.** Two tools wrote one archive with opposite strategies:

| tool | strategy | writes |
|---|---|---|
| `patch_tables.py` | **rebuilds the whole archive from a pristine copy** | a name/description table, `dat/<table-A>` |
| `patch_prices.py` | **edits the shipped archive in place** | 41 price fields in `dat/<table-B>` |

**(A) The ordering rule:** *rebuild-from-pristine first, in-place second, always run as a pair.*
The rebuild tool starts from pristine bytes, so it discards everything any other tool has done; the
in-place tool only touches its own fields, so anything the rebuild tool would have written stays at
its pristine value if the rebuild tool never runs.

**(B)** Both directions shipped:

- *In-place run last, alone.* A session committed a text edit; the next session found the target
  member **byte-identical to pristine**. The commit existed, the result did not — only the price tool
  had been re-run afterwards, and it rebuilds nothing.
- *Rebuild omitted.* A data table stayed pristine while the guest constants had moved on, and that
  combination shipped in **ten consecutive releases** (see §9 for what the players saw).

**(A) The trap is that both orders "succeed".** Each tool re-parses its own member, finds exactly the
records it meant to change, and prints a clean report. The porter has no signal at all. Fixing the
order is the minimum; it is not the fix, because the order is a thing a human has to remember. §3 is
the fix.

## 3. The structural fix: a byte-comparing `--check` that exits nonzero

**(A)** Give the rebuild tool a second mode that takes no arguments and changes nothing:

1. Rebuild the archive from the pristine copy **in memory**, applying today's tables.
2. **Byte-compare** the members this tool owns (§4) against the archive that is actually shipped.
3. Print which member differs and how, name the command that fixes it, and **exit nonzero**.

Then make it **the first command of any session that touches assets**, and a build stage before
anything is compiled (see [build-script.md](../../port-shipping/reference/build-script.md)).

**(B)** Run live against a current tree, it prints the member's pristine-to-built transition and the
two identity sweeps, then `CHECK OK`, exit 0:

```
res/dat/<table-A>: 892 -> 727 bytes
jar: 1290 members, all others byte-identical
zip: 7 entries, all others byte-identical
CHECK OK
```

Against a stale tree it prints the disagreement and exits 1:

```
STALE: res/dat/<table-A>: shipped 729 B, tables produce 727 B
```

**(B)** That gate caught a real staleness accident **on its first ever invocation** — the session's
own asset bake had never reached that checkout — and caught a second one several sessions later. It
is the only check that answers *both* "is my tool current with the repository?" and "is this
machine's archive current with the repository?", which are indistinguishable from either tool's own
output.

**(A) Why it has to be a command and not a paragraph.** The ordering rule of §2 existed as a
documented two-line procedure for months and was violated at least four times. A second, independent
demonstration from the same port: two consecutive session logs recorded a workspace linter run with
zero errors, and the next session discovered **the linter was not installed on that machine** — the
earlier record had been carried in from a different environment.

> **REFUTED.**
> *Previously documented:* "the ordering rule is in the project instructions, so the pipeline is
> safe."
> *Refuted by:* expressing the rule as `--check` with an exit code, which reported a violation the
> first time it was run.
> *Actual mechanism:* a rule in a document is executed by a person who has already decided what they
> are doing. It gets **reported as followed** rather than run — and every one of this window's worst
> accidents happened while every tool printed OK.
> *Consequence if carried forward:* you have a ritual, not a gate, and no way to tell the two apart
> from any output you can see.

## 4. Member ownership: compare only what one tool solely owns

**(A)** A byte-comparing gate may only compare members the checking tool **solely owns**. A member
another tool rewrites afterwards is *legitimately* different from what this tool's builder produces;
comparing it gives a permanently red gate, and a permanently red gate gets switched off — which is
worse than not having one.

**(A) Derive ownership from each tool's declared plan**, not from what a run happens to touch. Every
generator declares the members it writes; a member appearing in exactly one plan is solely owned; a
member appearing in two is co-owned and is excluded, **with the reason recorded at the exclusion**.

**(B)** The reference gate declares a single-element tuple of checked members and carries an inline
prohibition next to it explaining that the second patched member is co-owned with the price tool —
which rewrites 41 price fields after this tool has run — and therefore cannot be compared. The first
member is absent from the price tool's declared plan, which is exactly what makes it solely owned.

**(A)** A co-owned member is not left unprotected: it is covered by the last-shipped-build diff of
§11 and, if it matters enough, by the second tool growing its own `--check` (§12).

## 5. The canary member

**(A)** Pick one member whose framing decides the question *"is the shipped archive pristine, or has
something already been applied to it?"* — ideally the member a past framing accident damaged. Gate
the pristine-source fallback on that member parsing **byte-exact** (`consumed == filesize`; see
[record-formats.md §5](record-formats.md#5-the-consumed-equals-filesize-rule)).

**(A) What the canary buys:** without one, *a damaged archive* and *a merely out-of-date archive*
look identical, and the porter has to decide by hand which recovery to run — on a file that is the
only copy of licensed content. With one, a single `--apply` **both repairs an earlier framing
accident and applies the current tables**, because a canary that fails to parse forces the rebuild
back to pristine bytes.

**(B)** The reference tool names the member in one constant, chooses the member its old broken record
model had damaged, and the session that introduced it recorded that one run did exactly that: repair
plus apply, in a single pass.

## 6. Always rebuild from pristine

**(A)** An archive transform must rebuild from a **pristine backup**, never from the archive it is
about to replace. Rebuilding from an already-patched file is idempotent only by accident: it stays
idempotent exactly as long as no table edits a field it also matches on. The moment one does — a
rename table keyed on the name it rewrites — the second run rewrites something else.

Fall back to the shipped archive **only** when no pristine copy exists at all, and only through the
canary gate of §5.

> **REFUTED.** *Previously documented in this plugin*
> ([repacking.md §6](repacking.md#6-backups-idempotence-and-the-apply-gate)): "Embrace
> non-idempotence. A repack tool that can be safely re-run is a repack tool that cannot tell you
> whether it already ran. Let the changed-index check fail loudly on the second invocation."
> *Refuted by:* a multi-tool pipeline, where the question that matters is not "did *I* already run?"
> but "**did the other tool's work survive?**" — which non-idempotence cannot answer at all.
> *Actual mechanism:* the advice was attractive because failing loudly *is* a signal, and in a
> single-tool pipeline it is the only one available. But it puts a manual recovery step ("restore
> from backup first") in front of every single run, and it is silent about every member this tool
> does not own.
> *Consequence if carried forward:* the porter restores-and-reruns by hand forever, and still ships
> the §2 failure.

**(A) The replacement:** a pristine source makes the operation **idempotent by construction**, and
the question "is the shipped archive current?" moves to the `--check` gate, where it can be a CI job.
**(B)** Measured on the reference pipeline, the apply pair run twice: 1,764,374 → 1,764,381 →
1,764,374 B — the end state equals the start state. Size is not the check (see
[repacking.md §5](repacking.md#5-pitfalls-each-with-its-mitigation)); the member-wise compare is.

**(A)** And the writer must **refuse** input that does not parse byte-exact, with a refusal message
that names the pristine fallback. A tolerant tool propagates damage: **(B)** a wrong record model —
a per-record length prefix read as a file header — once shipped a table whose prefixes went stale
from the first renamed record onward, so 18 of 27 records parsed and the remaining 9 were simply
unreachable in-game. The file still parsed. The archive still booted.

## 7. Where by-products live, and why pristine is moved rather than deleted

**(A)** The mobile packager takes the app's assets directory **wholesale**. Therefore: **nothing but
the archive may live there.** Not a backup, not a pristine `.orig`, not a dry-run `.new`, not a
pre-edit copy under some other suffix.

**(B)** It shipped twice, both measured on the release package:

| occurrence | before | after removal | shipped |
|---|---|---|---|
| one backup left beside the archive | 29,503,153 B | 27,739,719 B | **1.76 MB** of licensed bytes |
| two by-products, later | 31,669,905 B | 28,140,816 B | **3.53 MB** of licensed bytes |

The build script, the signer and the boot smoke test all pass in both cases. Nothing in the pipeline
notices, and the artifact is the thing handed to other people.

> **REFUTED.** *Previously documented in this plugin*
> ([repacking.md §6](repacking.md#6-backups-idempotence-and-the-apply-gate)): "Naming it
> `<archive>.zip.orig` or putting it in `--backup-dir work/backup` are both fine."
> *Refuted by:* the two package measurements above.
> *Actual mechanism:* "next to the target" is inside the directory the packager consumes wholesale.
> The advice reads as being about *naming*, and the naming genuinely does not matter — the
> *directory* does, and the two options differ in exactly that.
> *Consequence if carried forward:* every release ships a duplicate of the licensed archive the
> project was careful to gitignore.

**(A) Three rules that make it stick:**

1. **One function knows the paths.** Every tool that touches the archive imports the *same* function
   for "where is pristine, where do by-products go". **(B)** The reference port shipped the
   alternative for exactly one session — a written instruction to move the backup by hand after each
   run — and the very next session's first apply violated it. The fix made the second tool import the
   first tool's path function, so, in the note's own words, *there is nothing left to forget.*
2. **A backup may only ever be taken from a pristine artifact.** **(B)** The reference apply path
   guards the copy with "no pristine copy exists **and** this one parses byte-exact", commented:
   backing up an already-patched one would poison the fallback. **(A)** A backup taken at the wrong
   moment silently *becomes* the new pristine — after which every rebuild starts from patched bytes
   and the §3 gate compares patched against patched and goes **green while the tables are no longer
   being applied**.
3. **Move the pristine copy out; never delete it.** It is the pipeline's only source. **(B)** Its
   absence surfaces as `expected 15 records, got 16 — refusing`, because the tool then reads its own
   previous output — which already carries the appended row — as its source. **(A)** That message
   points at the record count, i.e. at the data model, and sends the porter to debug the parser
   instead of restoring a file. This is the second cause behind
   [fault-dictionary.md F7](fault-dictionary.md#9-f7-repack-refuses-on-the-second-run), and the
   discriminator is one question — *does a pristine copy still exist?*

**(A)** Gitignore by-products **by name as well as by path**, and keep them out of every directory
any build step packages.

## 8. Gitignored artifacts do not travel with commits

**(A)** A generated archive is correctly gitignored — it is licensed content. The consequence is that
it **does not travel with the commit that describes it**. A session baked in one checkout commits
source, tables, documentation and a version bump; the bytes stay on that one machine. Another
checkout is then simultaneously "up to date" and wrong.

**(B)** Confirmed twice in the reference port. Once, a session found its target member byte-identical
to pristine although the commit existed — the tools had only ever been run in a remote tree. Later,
the §3 gate reported the shipped archive at 729 B where the tables produce 727 B; the previous
session's asset work had never reached that checkout, corroborated by the newest local build
predating three version bumps.

> **(A) The rule that falls out:** a sentence in a document saying *restored* / *applied* / *shipped*
> is a claim about **some** machine, not about yours. Only a gate that reads the actual bytes is
> evidence about yours.

**(A)** Two practical consequences. First, an archive-consuming `--check` cannot run in a clean CI
container that has no archive — such a job must be **skipped explicitly and loudly**, never faked
green. Second, generators whose inputs are all in-repo (a protocol table, a config) *can* run their
checks in CI, so prefer that shape where you have the choice (§12, §13).

## 9. The asset ↔ guest-constant pair: two halves, two routes

**(A)** Appending a record to an in-jar table is legal only if the guest constants bounding that
table change **in the same change**. Typically three: the row count the loader reads, the table's
hardcoded allocation size, and any teardown/free bound. **(A)** And the two halves travel by
**different routes** — the data in a gitignored archive (§8), the constants in a committed load-time
patch table (they are load-time patches, not edits to the guest image, because the hook table is
keyed by that image's hash; see
[build-keyed-anchors.md](../../guest-re/reference/build-keyed-anchors.md)). So they desynchronise
across machines, per machine, silently.

**(B) The method that establishes the three constants:** disassemble the loader and read what bounds
it. In the reference port the loader bounded itself with a hardcoded per-category count table read
through a **signed byte load**, the allocation was a hardcoded `alloc(N << 1)` sized to exactly
15 × stride, and **no reader range-checks the index** — so serving a 16th index without those three
bytes is a wild pointer on the first list draw.

**(B) What the desync looks like when it ships.** A pristine 15-row table against 16-row guest
constants shipped in **ten consecutive releases**. The device log:

```
emulator tick error: FatalError("… Allocation failure at <platform>/EventQueue.getNextEvent([I)V  R0: 0xffffffe4")
```

`R0` = **−28**: a negative allocation size. The parser had walked past the end of the array and a
length field read back negative. The headless probe reproduced it deterministically — died at
**frame 557** — and confirmed the fix at **5,200 frames with zero tick errors**.

**(A) Why this is the most expensive shape in the whole pipeline:** it is an **asset bug wearing an
emulator bug's symptom.** The player reports "the game starts and then stops at a black screen".
Nothing upstream catches it — not the build script, not signature verification, not a boot smoke test
— because **the death is after the title screen**. Presence checks and signature checks answer "is a
file there, and is it ours"; they never answer "do these two numbers agree".

> **(A)** Therefore the gate must be **data-level**: dump the table with your own decoder, assert the
> record count against the guest constants, and run §3's byte-compare. And the diagnostic belongs in
> your fault dictionary, or the next person debugs the interpreter. The generic allocation-failure
> entry in [shared/fault-dictionary.md](../../../shared/fault-dictionary.md) describes a *different*
> fault — fragmentation after tens of minutes of play. This one arrives **seconds after the title**,
> and the argument register is **negative**. Those two facts are the discriminator.

## 10. The framing invariant: preserve what the parser scans for

**(A)** When you edit game text, you must preserve the **framing** the game's parser depends on — and
that framing is frequently not enforced by any code. It holds only because the publisher's data
happens to satisfy it in every record.

**(B) The worked case.** A focused-cell one-line description measured its own length by **scanning
the effect string for an in-band separator byte, with no NUL test and no bound**. All 15 stock rows
shipped with exactly one separator. A text tool deleted an advisory sentence *and its separator*; the
scan then ran off the malloc'd buffer into the heap and stopped at the first matching byte, so the
computed length came back positive (looks perfectly normal) or negative (draws nothing) — **depending
on allocation history**. The bug appeared only two sessions later, when the heap layout changed, and
the report was *"some items show a blank description, a different subset each session, but the detail
popup is fine."* That reads as a font or layout bug, and it is unfalsifiable by inspecting the tool's
output.

**(A) The three rules this yields:**

1. **Before removing any byte from a data member, find every consumer that SCANS for it** — not just
   the ones that read a length. A pattern holding in 100 % of shipped records is evidence of an
   invariant, not of redundancy.
2. **Expect the unbounded idiom to be duplicated.** **(B)** The same scan sat at **eight distinct
   screen handlers with two per-category arms each**; patching it in code means hand-assembling
   sixteen sites, so **the one data byte is the cheap fix and the code patch is not.** This reverses
   the instinct: the tidy-looking change (drop the now-dangling separator) is the dangerous one. A
   bare trailing separator is invisible in-game.
3. **Enforce the invariant constructively and assert it per row.** **(B)** The reference tool ships an
   `ensure_*` helper documented as a no-op for today's table, existing so a future edit producing a
   single-segment string cannot silently reintroduce the bug — plus three assertions on **every**
   output row: the separator exists, it is **not at index 0** (which would give length 0), and the
   field length stays under the reader's ceiling.

**(A) That ceiling is a property of the reader, not of the format.** Where the guest loader reads a
length prefix with a **signed** byte load, the bound is `0x7f`, not the prefix's storage width: a
`0x80`+ length becomes a negative allocation argument at load time. **(B)** Both length loads in the
reference item-DB loader were signed-byte loads, and the writer asserts that ceiling as a hard
failure with the reason inline. See
[record-formats.md §3](record-formats.md#3-the-safety-theorem-for-length-prefixed-records).

**(A) The mirror of the refusal guard.** [record-formats.md §6](record-formats.md#6-writing-records-back)
already says to refuse a record that does not look like the one you meant. Add its other half:
**assert that every intended edit actually landed.** Key each override by index **and** an expected
identity field, die if the identity mismatches, and have `verify` walk every override key asserting
the output row really starts with the new value. **(B)** The reference tool's comment names the
failure that check prevents as the §2 failure mode one layer down — a key matching nothing is a
**silent no-op that produces a clean VERIFY OK over unchanged content**.

## 11. Verification: the last shipped build, then the packaged artifact

**(A)** [repacking.md §4](repacking.md#4-verification-is-the-deliverable) gives the four member-wise
identity checks. Two additions matter once the pipeline has more than one producer.

**The comparison base for a release is the LAST SHIPPED BUILD**, not the file you started this run
from. Diffing against the pre-run file proves only that *this run* behaved. Diffing against the last
shipped archive is what proves **the other generator's members survived** — precisely the thing that
keeps getting lost. Assert that **only the intended members changed**.

**(B)** Measured results in that form, from two sessions:

- *1,297 members* (outer zip 7 + jar 1,290), **exactly one changed**: 729 B → 727 B, both hashes
  recorded.
- *jar 1,274 members*, **only one data member changed**, and the **41 price rows preserved** — the
  check that proves the in-place tool's work survived the rebuild.

**A fifth check: read the member back out of the PACKAGED artifact.** The tool's report proves what
the tool did, not what got packaged. Between them sit the packager, the build cache, and possibly a
different checkout (§8). **(B)** One session opened the built package, confirmed the assets directory
held **exactly one file**, and read the member back at **727 B with the new sentence present and the
old one absent** — closing that session's staleness risk and its by-product risk at once.

## 12. Every generator gets a `--check`

**(A)** Make it a project rule: **no generator without a `--check` mode.** Then the audit is
mechanical — list every generated artifact; any without a check is an accident waiting. It is also
the cheapest CI job you will ever write.

**(A) The shape, three lines:** regenerate **in memory**, compare against the on-disk artifact, and on
mismatch print *which* artifact, *how* it differs, and **the command that regenerates it**, then exit
nonzero. The message must name the regeneration command, because the porter who trips the gate is
usually not the porter who wrote the generator.

**(B)** Three reference generators share that shape: two compare generated source text
(`STALE: <path> does not match the catalog` / `… does not match the asset`, followed by the
regenerate command), and the archive one compares member bytes and prints both sizes
(`<table-A>: shipped 729 B, tables produce 727 B`). The archive gate was negative-tested by running
it against a pre-patch copy of the archive: exit 1.

**(C) Do not copy the counter-example.** The same port committed an **83 KB binary sprite atlas**,
decoded from the game's own art, into the crate source tree for a compile-time include — while its
generator hardcoded absolute input and output paths on a drive the project's own notes record as no
longer existing, and had **no `--check`**. That artifact is simultaneously a licensed-content leak
into a repository that carefully gitignores the archive, and un-auditable: there is no way to answer
*"does this blob match the archive we ship?"* This is where two otherwise-good rules collide —
"generated code participates in the selftests"
([selftest-convention.md](../../../shared/selftest-convention.md)) and "decoded assets are licensed
content, keep them under `work/`"
([record-formats.md §9](record-formats.md#9-sprites-and-other-non-record-members)). Resolve it this
way:

> **(A)** A generated artifact derived from **the archive** is a **build product**: gitignored,
> produced at build time, with a `--check`. Only a generator whose output derives from **your own
> source** may commit its output.

## 13. Generate from the other implementation's own objects

**(A)** When a generated table exists because **two implementations must agree** — a host-side offline
engine and a server, say — do not hand-copy it. Generate it by instantiating the other implementation
and calling the method that builds what it actually serves. A hand-copied table creates a second
source of truth that rots the first time a row is added, and the failure mode is a player buying
something one side has delisted, or an id the two sides resolve to different items.

**(B)** The reference catalogue table is emitted by instantiating the server's own protocol handler
and calling its serve-the-catalogue method. The generated file's header records **which method**
produced it, the five source tables it concatenates, the shipped flag values, the row layout,
`DO NOT EDIT BY HAND`, and a pointer to the canonical prose document.

**(A)** Record that provenance in the artifact's own header, not only in the generator: the person who
opens the generated file next is looking for permission to edit it, and the header is where they
look. See [in-process-engine.md](../../server-revival/reference/in-process-engine.md) for the engine
side of this arrangement.

## 14. Generated code a selftest `include!`s

**(A)** Generated source that is `include!`d into another file must use plain line comments, **never
inner doc comments** (`//!` in Rust) — an included file is spliced into the middle of its host, where
an inner doc comment is not legal. This constrains the **code generator**, so it has to be known when
the generator is written; discovering it later is a confusing build error at a site that looks fine.
The same constraint binds any hand-written module a second crate `include!`s.

**(A)** Two further constraints on generated code that tests consume, both owned by
[selftest-convention.md](../../../shared/selftest-convention.md) and repeated here only as pointers:
compile the standalone test at the **same language edition as the crate that ships the source**, and
never redirect the compiler's stderr in a test or build command — on one common Windows shell that
turns warnings into a terminating error and makes a passing test read as a build failure.

**(A)** Finally, remember what `include!` does *not* buy you. It protects against **drift** from the
source and does nothing about a **wrong premise**: a negative-case list can assert that a legitimate
input is refused, and thereby guard a real bug green. That failure and its fix belong to
[selftest-convention.md](../../../shared/selftest-convention.md); they matter here because the input
in question is very often **a category or row id that came out of a generated table**.

## 15. Checklist

**The first command of any session that touches assets:**

- [ ] Run the rebuild tool's `--check`. Exit 0, or stop and fix the tree before anything else.

**Before `--apply`:**

- [ ] A pristine copy exists, **outside** every directory the build packages, at the path the shared
      path function returns.
- [ ] Every generator's declared plan is written down, and the check gate compares only solely-owned
      members, with a reason recorded next to each exclusion.
- [ ] The transform reads from pristine, refuses input that does not parse byte-exact, and its refusal
      message names the pristine fallback.
- [ ] Every override is keyed by index **and** an expected identity field.
- [ ] Any framing byte the guest scans for is preserved constructively, and asserted on every output
      row — including "not at index 0" and the reader's signed-load ceiling.

**Run the tools as a pair: rebuild-from-pristine first, in-place second.**

**After `--apply`, four proofs:**

- [ ] **P1** Every intended edit landed: `verify` walked every override key.
- [ ] **P2** Diff every member against the **last shipped build**; only the intended members changed;
      both hashes recorded.
- [ ] **P3** If a record was added or removed, the guest constants bounding that table changed in the
      same change — and they are committed, whereas the archive is not.
- [ ] **P4** Open the **packaged artifact**: the assets directory contains exactly the archive, and
      the member read back out of it is the new one.

**Standing:**

- [ ] Every generator has a `--check` that exits nonzero and names its regenerate command.
- [ ] `check-generated` runs as a build stage before anything is compiled, and any check needing the
      archive is skipped **loudly** where the archive is absent.
- [ ] No generated artifact derived from the archive is committed.
- [ ] By-products are gitignored by name **and** by path.
