# The build-keyed anchor table

One codebase, N builds of the same title. This file owns the **architecture** that makes that safe:
the three gate axes, the shape of a table entry, the tests that keep the table honest, and the two
instruments — a generated boot banner and a periodic on-device audit line — that tell you what is
actually live on the build in the player's hand. *Deriving* a value for a second build is
[multi-build.md](multi-build.md); calling the code an entry points at is
[calling-guest-code.md](calling-guest-code.md); rewriting bytes at a site is
[patching.md](patching.md). The host feature flags that consume these gates belong to the
[android-host](../../android-host/SKILL.md) skill.

**(A)** generic procedure · **(B)** worked example from the reference port — copy the *method*, never
the number · **(C)** do not copy · `⚠` inferred, nothing was run. See
[evidence-standard.md](../../../shared/evidence-standard.md).

## Contents

- [1. The problem a single boolean cannot express](#1-the-problem-a-single-boolean-cannot-express)
- [2. The three axes](#2-the-three-axes)
- [3. Never flip the master predicate — unlock per item](#3-never-flip-the-master-predicate--unlock-per-item)
- [4. Anchors are shapes, not addresses](#4-anchors-are-shapes-not-addresses)
- [5. Key the table at the granularity you verified](#5-key-the-table-at-the-granularity-you-verified)
- [6. The "build not known yet" answer](#6-the-build-not-known-yet-answer)
- [7. One dependency-free table, `include!`d by the app and the probe](#7-one-dependency-free-table-included-by-the-app-and-the-probe)
- [8. Discipline tests: pin relationships, refusals and couplings](#8-discipline-tests-pin-relationships-refusals-and-couplings)
- [9. Never mirror a build-keyed table](#9-never-mirror-a-build-keyed-table)
- [10. Generate the boot banner from the tables](#10-generate-the-boot-banner-from-the-tables)
- [11. The coverage matrix: "gated off" is not "active but unverified"](#11-the-coverage-matrix-gated-off-is-not-active-but-unverified)
- [12. Closing unverified offsets: the on-device audit line](#12-closing-unverified-offsets-the-on-device-audit-line)
- [13. When the second build overtakes the first](#13-when-the-second-build-overtakes-the-first)
- [14. Symptom table](#14-symptom-table)
- [15. Checklist](#15-checklist)

---

## 1. The problem a single boolean cannot express

The obvious design is one predicate: *are my reverse-engineered guest addresses valid on the build
that booted?* Every feature asks it, and a foreign build answers `false`.

It cannot express the state you are in for most of a port. **(B)** The reference port reached, within
one session of starting the second build, "we know exactly where the character record is, and we do
not yet know one code address" — which the flag renders as either *nothing works* or *every
not-yet-found address is unlocked at once*.

The `false` side is not benign either: a blanket early-return at a feature's entry swallows a fully
ported data half and prints nothing. **(B)** A quick-slot page button on the unported build flipped
the host's own store, its page index, its save file and its UI, and wrote nothing to the guest. The
player reported "the button does nothing"; no log line was wrong.

> **REFUTED.** *Previously documented:* one `guest_addresses_valid()` predicate, asked by reads,
> writes and calls alike, is enough to keep a second build safe.
> *Refuted by:* splitting it into three data accessors plus a residual code predicate opened the
> port's entire read-only feature set in one session, with the residual predicate still `false`.
> *Why the wrong belief was attractive:* it is genuinely true that a foreign build invalidates
> addresses, and one flag makes that fact impossible to forget. What it hides is that *reads,
> writes and calls have different blast radii*, and that address validity is per-address.
> *Consequence if carried forward:* every feature is either off or dangerous, so the only way to
> make progress looks like flipping the flag — which is §3's catastrophe.

---

## 2. The three axes

**(A)** Gate on three separate axes, and keep them separate in the source.

| axis | the question it answers | a wrong answer produces | default when the build is unknown |
|---|---|---|---|
| **1 — DATA anchors**, per build | where is the record / the statics / how many pointer hops to a container | **plausible wrong values**, not absence | a shape whose failure is a refusal (§6) |
| **2 — per-FEATURE code sites**, per build *and* per feature | may I patch, write or call for *this one feature* on *this* build | the feature is off, including its UI | `None` |
| **3 — the MASTER predicate** | is everything *not yet moved out into its own entry* trustworthy | the residual features stay off | `false` |

```rust
pub enum GuestBuild { A, B, C, Unknown }

// Axis 1 — data. Open on every build where it is known.
pub fn char_anchor(b: GuestBuild)        -> Option<CharAnchor>;
pub fn container_data_off(b: GuestBuild) -> Option<u32>;   // Some(8) | None — see below

// Axis 2 — one function per feature. Each entry is a claim with its own evidence.
pub fn hud_bottom_patch(b: GuestBuild)   -> Option<BlockSkip>;
pub fn equip_relief_site(b: GuestBuild)  -> Option<EquipReliefSite>;
pub fn save_chain(b: GuestBuild)         -> Option<SaveChain>;

// Axis 3 — the residue, and nothing else.
pub fn guest_code_addresses_valid(b: GuestBuild) -> bool;
```

**(B)** In the reference port, 14 named code sites started life behind the master flag; by the end
roughly a dozen per-feature tables had replaced it and **the master flag was still `false` on the
second build** — which is the correct end state, not a failure to finish.

One axis-1 entry is worth calling out because it is the cheapest line in the architecture:
**(B)** `container_data_off()` returns `Some(8)` or `None`, and it simultaneously resolved the
inventory grid, the area object, the item database, the map arrays, the offscreen framebuffers, the
popup label array and the sprite descriptor — because the double-dereference documented all over the
first build's notes was a property of the *carrier runtime's* container accessor, not of the game.
The derivation and the hazard (the wrong convention succeeds *silently*) are
[multi-build.md](multi-build.md)'s §4.

---

## 3. Never flip the master predicate — unlock per item

> **⛔ Do not set the master predicate to `true` for a new build. Ever. Move items out of it one at
> a time.**

Flipping it does not "enable the port"; it releases every address you have not found yet into a
binary where those addresses are something else. It converts *a feature is missing* — an absence a
player can report — into *the guest is corrupted*, which they cannot.

**(A) The per-item unlock checklist.** Each row is one commit's worth of work for one entry.

| # | step | done when |
|---|---|---|
| 1 | **Locate** the address on *this* build by a named method | the method and its output are in the notes, not just the number |
| 2 | **Two independent derivations** | both routes named and genuinely disjoint ([evidence-standard.md §3](../../../shared/evidence-standard.md#3-two-independent-derivations)) |
| 3 | **Read the reader**, not only the writer | you know the value, the index source and the re-assert requirement (§4) |
| 4 | **Live-verify** against running guest memory | a poke run logged the displaced bytes and they equal the static read |
| 5 | **Add the entry**, `Some(..)` for this build only | every other arm still returns `None` |
| 6 | **Pin it in a selftest** | the test fails if the address is moved to another build's arm (§8) |
| 7 | **Banner + matrix** in the same commit | the generated boot line prints it (§10) and the row changes symbol (§11) |

**`None` means the feature is off, including its UI.** A missing anchor must degrade to *absence*,
never to a half-drawn control that writes garbage. That is the whole reason `Option` is the return
type rather than a sentinel address.

**(A) The exception, and it is a real one: gate the WRITE, never an already-shipped control.**

> **REFUTED.** *Previously done:* a session hid a quick-save control on the unported build, on the
> reasoning that a button which does nothing is worse than no button.
> *Refuted by:* the project owner reversed it the same day — *removing an already-shipped feature
> because a carrier port is unfinished is the wrong trade.*
> *Why it was attractive:* it is the same instinct as `None`-means-absent, applied one level too
> high. *Actual rule:* absence applies to a feature that never shipped on that build. For one that
> did, keep the control and spend the effort on **telling the two refusals apart** — the game's own
> "you may not save here" byte versus "this build's save chain was reverse-engineered from a
> different compile". **(B)** Without the second reason the only symptom was a queued-retry-expired
> log line that reads exactly like the first.

---

## 4. Anchors are shapes, not addresses

**(A)** An address-only anchor invites you to write a plausible boolean into a bitfield and ship a
silent no-op. Give each anchor the shape the *game* uses.

**An anchor for a tested byte is an `(offset, index-source, value)` triple.**

* **The value is decided by the reader.** **(B)** One flag byte's reader recomputed the address and
  did `ldrb; lsls #24; asrs #24; cmp #0; bge skip` — a bit-7 test — which is why the game's own store
  materialises `0x81` as `movs r2,#0x7f; rsbs r2,r2,#0`. Writing `1` there is right on address and
  dead on effect. The same disassembly showed the reader **clears** the byte on the branch it takes:
  the feature is one-shot per event, so the host must **re-assert on a timer** — a requirement an
  address-only anchor hides entirely.
* **The index source is part of the anchor.** **(B)** That byte defeated three candidate offsets over
  multiple sessions because it is not a constant offset: the game's own store computes
  `base + *(base + slot_word) + K`, one element per save slot. The first build's documented offset
  was element 0, one device measurement was element 1, and a candidate previously written up as
  *refuted* was element 6. Copy the store's arithmetic verbatim **including the index load**, log the
  whole array rather than one element, and **refuse** when the index is unreadable or out of range —
  never fall back to element 0. Standing question for any offset that resists search: *is this
  element 0 of something?*

**The anchor KIND can differ between builds — that is an enum, not two addresses.**

**(B)** One build allocates the record and keeps a pointer in a global; the other compiles it into
`.bss` with no pointer anywhere. A pointer anchor can be null-checked; a static one cannot, so
liveness must be judged by *contents* — and every gate written against the pointer build's implicit
null-check then inverts silently. **(B)** A title-screen letterbox feature had never drawn once on
the static build, because its gate called the liveness-checking resolver and the shell screens are
precisely where no character exists. The fix is two accessors: a **raw base with no liveness test**
for gates that only read a state word, and the checked resolver for field reads.

> **REFUTED.** *Previously shipped:* the liveness predicate `level in 1..=70 && hp <= hp_max &&
> sp <= 1000`.
> *Refuted by:* unequipping gear drops max-hp below current hp on a live character, so the host
> concluded "no character" mid-play and the HUD and quick slots vanished; a downstream sweep that
> asks the same resolver for equipment cells then returned zero cells and silently did nothing.
> *Why it was attractive:* every clause is a true invariant of a *saved* record, and the predicate
> reads like a checksum. *Actual mechanism:* it is not an invariant of a *live* one.
> *Consequence:* a gauge reading over its maximum is a reason to clamp **at draw time**, never
> evidence that the record is dead.

**Other entries that must be enums rather than addresses:**

| entry | build A arm | build B arm | why one address cannot express it |
|---|---|---|---|
| record-name strings | pointers held in the static-base table, read at runtime | the strings are compiled in; pass their addresses through | there is no slot to read on a linked image ([address-model.md](address-model.md)) |
| a patch site | one halfword register-move rewrite | a two-halfword load rewrite | carry `Option<(addr, Vec<u8>)>` from the start, not one halfword ([patching.md](patching.md)) |
| a commit path | call the game's own key handler | **no address at all** — write the row byte and synthesise an ordinary keypress | see below |
| pointer plausibility window | opens at the first build's image base | opens at this build's lowest mapped page | a fixed window rejects valid pointers on one of them |

**(B) A feature can need FEWER guest addresses on the second build.** Two deterministic probe runs,
identical except that one poked the highlighted-row byte ten frames before an ordinary OK, showed the
game's own dispatcher performing the entire bind itself — kind, reference, cleanup of the previous
binding, teardown. The plan document had twice asserted "the one thing remaining is this guest
function's address"; both assertions were wrong, and the resulting arm carries **no address that can
go stale**. Before hunting a handler address, ask whether writing the state that handler reads plus
synthesising its trigger achieves the same thing.

**(A)** That arm has its own hazard, and it belongs in the entry's doc comment: a synthesised key
arrives **a tick later**, so the game's clamps and routers run in the gap. **(B)** The commit path
shrinks a row-count byte, and the guest's cursor clamp can pull the target row down inside that
window — so it restores the native count immediately before the key lands, a hazard the direct-call
build structurally does not have.

> **Rule.** `Option` expresses *known / not known*. An **enum** expresses *known, and differently
> shaped*. The moment you find yourself writing `if build == A { .. } else { .. }` at a call site,
> that branch belongs in the table as an enum arm.

---

## 5. Key the table at the granularity you verified

**(A)** Key on the **hash of the native member you actually verified against** — not on the carrier,
and not on any version string. Carrier is usually one level too coarse: a single carrier can ship two
revisions of one package whose code is relocated, and a carrier-keyed table then applies revision A's
addresses to revision B in silence.

**(B)** Between two revisions of one carrier in the reference port, `.text` grew `0x144` bytes and a
hooked function moved `+0x74` with its prologue intact — found by re-recognising the function's bytes
and re-verifying each clause of the hook's contract instruction by instruction (early-outs, the
descriptor loads, the palette arithmetic, the row-bytes rounding, the argument slot). A minor revision
is an **address-reconfirmation job, hours not sessions** — but it is not a no-op.

> **⚠ INFERRED — a latent trap in the reference design itself.** That port keys its *performance-hook*
> table on the member's hash (correctly: one entry per revision) while keying its *anchors* table on
> carrier alone — even though every address in that arm's doc comments is annotated with one specific
> revision. The only remaining protection on the other revision is the per-patch read-verify guard,
> which the repo's own source describes as **probabilistic**. Nothing was run on the second revision
> with the anchors table; what would settle it is booting that revision and reading §12's audit line.
> **If you already hash the image to choose hooks, hash it to choose anchors too — the information is
> free at that point.**

Two corollaries:

* **(A) Identify a build by its hash in prose, too** — never by a version string. The two
  numbering schemes that do not agree, and the shipped wrong claim they produced, are
  [multi-build.md §1](multi-build.md#1-three-levels-of-build-identity--hash-the-native-member-first).
* **A hash-keyed table must not short-circuit the generic entries.** **(B)** An installer that
  `return`ed after the first hash match meant six generic library hooks had never installed on any
  build, for years, while the boot log printed a cheerful `Installed 1 hooks`. That is
  [emulator-core](../../emulator-core/reference/boot-order.md)'s to fix; what belongs here is the
  lesson that **a count in a log line is not an inventory** — §10.

---

## 6. The "build not known yet" answer

**(A)** `Unknown` is not hypothetical. Boot lasts long enough to draw frames and run worker polls, so
every accessor is called at least once before detection completes. Define the answer **per entry**,
decided by which failure is worse, and write the reason at the accessor.

**(B)** The reference table's three rationales, all stated at the code:

| entry | default before detection | stated reason |
|---|---|---|
| container hop count | the first build's shape | every caller validates its hops anyway — an unresolvable chain yields `None`, never a bad read |
| a UI capability flag | `false` | otherwise a bar can flash for one frame during boot |
| every code site and callable | `None` | there is no such thing as a safe guess about a code address |

> **Guest CODE entries default closed. Guest DATA entries default to a shape whose failure is a
> refusal.** "Unknown build" silently mapped to one build's answers is how a second-build session
> gets first-build behaviour for its first second.

---

## 7. One dependency-free table, `include!`d by the app and the probe

**(A)** The headless probe and the shipping app must read the **same** table file — not a copy.

**(B)** Before that was true, the probe carried a hand-copied set of the first build's anchors, so
on a second-build boot four of its diagnostic modes — map check, record dump, framebuffer find, state
trace — all read unrelated memory. That is the exact tool you reach for *because* a second build is
misbehaving, and its wrongness is unfalsifiable from inside the app.

Constraints that keep the sharing possible **(A)**:

* **No `use`, no dependencies.** The file must compile standing alone.
* **No `//!` inner doc comments** — it is `include!`d into the middle of another file, where an inner
  attribute is a syntax error. Use `//` comments for the module-level prose.
* The app `include!`s it into its build-state module and adds exactly one thing on top: the atomic
  holding *which build booted*. The probe includes it as a module of the same name.
* A standalone `rustc --test` file `include!`s the same source and pins it — see
  [selftest-convention.md](../../../shared/selftest-convention.md).

**(B) One deliberate non-share.** The "is this record alive" plausibility test stays *out* of the
shared file. A diagnostic wants the raw base **precisely when the record reads dead** — that is the
case it was launched to investigate. Sharing the addresses is the point; sharing the judgement is not.

---

## 8. Discipline tests: pin relationships, refusals and couplings

**(A)** A table of constants cannot be unit-tested — the test would restate the constant. What *can*
be tested are the **invariants around** it, and those catch the regression that looks plausible.

**(B)** The reference port's anchors selftest is ~29 tests over the `include!`d table:

| invariant | what it catches |
|---|---|
| **Range** — every code site of build B lands inside build B's `.text` | a build-A address pasted into a build-B arm |
| **Recomputation** — a branch immediate recomputed from scratch lands on *that* build's epilogue | a wrong branch target, which the read-verify guard cannot see |
| **Refusal** — `assert_ne!(site, <the superseded address>)` | an edit that "restores" the address an older document named, which looks like a fix and *is* the regression |
| **Shared base** — a modal's five field offsets are all consistent with the character anchor | one of five updated and four left behind |
| **Encoding** — the pristine halfword and the stated signed displacement encode each other | a hand-typed patch byte |
| **Uniqueness** — no address appears twice in a poke list | a duplicate means a site is missing from the list |
| **Cross-build equality** — the two builds' constant multisets for one copied routine are equal | it is the same C source; inequality means one list is incomplete |
| **Scratch bound** — the chosen scratch address stays beyond the maximum literal reach in the image | a scratch write landing on live data |
| **Coupling** — only a build whose commit *bypasses* the game's key router may carry pre-emptor offsets | the bug below |

**(B) The coupling test, and the bug that bought it.** A host-side guard reading two "pre-emptor"
bytes existed only because the first build's commit path called the game's key handler directly,
bypassing the router that performs that check. The second build commits by synthesising an ordinary
keypress — which *rides* the router, so the game applies its own check and the host's copy adds
nothing except a read of guessed offsets that name different live fields on that build. Symptom: the
host refused **every** consumable popup while skill popups, which do not pass through that check,
worked fine. **The asymmetry between two item classes through one machine was the diagnosis** — look
for the gate only one class passes through. The test was written by the session whose bug it would
have prevented, which is the normal and correct order.

> **Build tables are edited by people reading old plans.** The only defence against "restore the
> address the earlier document named" is a test that refuses it *and says why* in the assertion
> message. A discipline test is also the cheapest durable home for a refutation.

---

## 9. Never mirror a build-keyed table

The general rule — a hand-written mirror of shipping logic drifts, and its greenness then means
nothing — is [selftest-convention.md §6](../../../shared/selftest-convention.md#6-the-forbidden-mirror-form).
Two additions belong to build tables specifically.

> **REFUTED.** *Previously believed:* a hand-written mirror is a cheap way to get a table under test
> when the real source cannot be `include!`d.
> *Refuted by:* a mirror's **negative** assertion list encoded the shipping code's bug — a wrong
> lower bound excluded an entire item category from a feature, and the mirror asserted that
> exclusion as correct. The suite was **holding the bug green**, and the bug surfaced instead as a
> user question ("is this category just not supported?").
> *Why it was attractive:* a mirror is fast to write and its tests genuinely fail when you break the
> arithmetic. *Consequence:* mirrors are for pure arithmetic only. **A mirror must never restate a
> build-keyed table or a bound** — the reference port deliberately declined to mirror its save-slot
> indexing for exactly this reason and let the `include!`-based anchors selftest own it.

**(A)** And: **delete a mirror in the same commit as the function it mirrors.** A mirror of a deleted
function stays green forever. The reference port hit that trap for real.

---

## 10. Generate the boot banner from the tables

**(A)** The line your boot log prints about which features are live must be **formatted from the
table accessors**, never hand-maintained. On a multi-build port that line is the primary instrument:
it is how a device session, a bug report and a second contributor decide what is supposed to work.

> **REFUTED — twice, in one port.**
> *Previously documented:* a hand-written capability line is fine because you update it when you
> change a gate.
> *Refuted by:* (1) the banner still read "HUD skip, EXP … inactive" **three sessions after** both
> had shipped measured second-build addresses — so the one line a device session trusts was lying
> about two working features; (2) a line reading "still gated: auto-loot" stood for **six weeks**
> while that feature was in fact dead on the shipped build for an entirely different reason — and
> the replacement server **delisted the equivalent purchasable item** on the strength of that claim,
> so players got neither the item nor the effect.
> *Why it was attractive:* it is one line, and it is right on the day you write it.
> *Consequence if carried forward:* a hand-maintained banner is a second source of truth that only
> ever drifts one way — toward understating what works and overstating what is blocked, because
> nobody edits a line to say *more* is broken.

**(B)** Shape of the generated line — every field read out of the table, and an array-indexed anchor
printed **as an array** so the index bug of §4 is visible on sight:

```
build=B  hud_bottom=0x…  exp_divisor=0x…  band_x=3 sites  relief=LoadRewrite(0x…)
         autoloot=[0x…+slot] slots 0..7   popup labels NOT LOCATED — no cash buff can start
```

"NOT LOCATED" is doing real work: it names the *consequence* (`no cash buff can start`), which is
what makes the line actionable to somebody who does not know the table.

---

## 11. The coverage matrix: "gated off" is not "active but unverified"

**(A)** Publish one matrix of every host feature × every build, with a legend that distinguishes four
states — and treat it as the anti-omission artifact for the port.

| symbol | state | failure mode |
|---|---|---|
| ✅ | verified on this build | — |
| 🔶 | **active but unverified on this build** | **wrong values**, not absence — the dangerous state |
| ⛔ | gated off (`None`) | absence, by design |
| — | irrelevant on this carrier | — |

**The dangerous state is not "off", it is "on and unmeasured"**, and nothing in a test suite or a log
distinguishes it from a working feature. **(B)** The reference matrix listed 23 features and singled
out five 🔶 rows as a silent-bug watch list, each with its **failure direction** written down: two
fail safe (one fail-open on a save-permission byte; one affects only a cooldown display), while three
sit behind weak plausibility guards (a kind enumeration, a badge cap, an icon-frame ceiling) so a
coincidentally-plausible byte renders a wrong value. Four of the five were later closed by
measurement, and one of them — a pre-emptor offset — failed in exactly the predicted way (§8).

**(A) Write down the reason for *not* acting**, or someone will "fix" the row: *changing the value or
disabling the feature without verification are both unfounded changes — the first creates a new
silent bug, the second is a regression.*

**(A) The matrix's row order is not the dependency graph.** It is usually written from the first
build's feature list, which does not encode what gates what. **(B)** Four items the matrix listed as
separately blocked — two reward multipliers, an equip-restriction relief, a full restore — all
depended on **one** address: the popup row-label array, the only thing that can tell the host that a
particular in-game action is about to run, i.e. the sole entry point to the whole host-buff
subsystem. The audit line of §12 printed `handles=None`, and that one word explained all four.
**Before porting features one at a time, look for the single anchor that gates many of them.**

---

## 12. Closing unverified offsets: the on-device audit line

**(A)** Some offsets cannot be verified headlessly, because the probe cannot reach the state that
displays them. **(B)** Levelling up, equipping a secondary weapon, registering a quick slot, opening
an item popup and entering a no-save zone were all unreachable from a scripted headless run — and
take a human two minutes. Move the audit into the shipping app as a **periodic, bounded, read-only
log line**, roughly every five seconds.

```
offset audit (build B)  REF lvl=12 exp=3480 hp=88/120 sp=40 win=3 state=0 sel=1
                        SUS quickbar[0x…]=2 badge=… secondary=… nosave=… cooldown=…
```

**(A) Print known-good reference fields NEXT TO the suspects.** That layout is the entire diagnostic
value: *if the reference block matches the numbers on screen and only a suspect disagrees, that one
offset shifted; if the reference block is wrong too, the base is wrong.* Without the reference block
the line is suggestive; with it, it is decisive.

Four rules for the line **(A)**:

* Read through the **shared** table (§7), and expose the same audit behind a probe flag, so the two
  tools agree by construction.
* Every out-of-map read yields `None`, never a panic. A diagnostic that can crash the app will be
  deleted by the next person.
* Bounded and read-only. It runs on players' devices.
* It is **removable** once the matrix has no 🔶 rows — say so at the code, or it becomes permanent.

Handling of the log itself — redaction, pulling it off a device, what a release build may print — is
[on-device-diagnostics.md](../../port-shipping/reference/on-device-diagnostics.md).

---

## 13. When the second build overtakes the first

**(A)** Design for the day the build you ported *from* is the incomplete one. It arrives sooner than
you expect, and no privileged-reference-build design survives it.

**(B)** In the reference port's shipped table, several entries are `Some(..)` for the second build and
`None` for the original: a level-up routine, a warehouse-grade gate, a refine-odds capability, the
menu-cursor set. A master "is this the reference build?" predicate would have to be **true for the
build that now lacks those addresses**. Any design where one build is privileged — a boolean, a
`#[cfg]`, an `if !A { return }` — becomes unrepresentable within a few months of real work, and
migrating away from it means rewriting every gate at once.

**(A) Represent "boots, but nothing is measured" explicitly.** **(B)** A third carrier in the same
table boots with every arm `None`/`false` and one boot warning line. That is a *known to be unknown*
state, and having a name for it on day one is what stops the first person to add a third build from
reaching for the master flag.

**(A)** Finally, keep negative results in the table's comments. **(B)** A session ran three static
searches for a flag byte, got zero hits in all three, and deliberately did **not** change the shipping
entry to `None` — writing instead that the searches were literal- and displacement-based and *would
miss a runtime-computed index*, and that turning a shipped write off is a behaviour change wanting
device evidence rather than a second inference. The next session's answer was exactly a
runtime-computed index (§4). **Writing down a search's blind spot is what makes the next person dig
there first.**

---

## 14. Symptom table

| symptom | cause | where to look |
|---|---|---|
| A feature works on build A, does nothing on build B, and no log line is wrong | one blanket predicate early-returns at the feature's entry; the data half is fine and swallowed | §1, §3 |
| Plausible-but-wrong values on the second build (counts, icons, quantities) | a container hop count hardcoded from the other carrier's runtime; the wrong convention passes every plausibility guard | §2, [multi-build.md](multi-build.md) |
| A guest write is at the right offset with the right value and has no effect | the reader tests the byte **signed**, or the "offset" is element 0 of an array indexed by a runtime word | §4 |
| A host overlay has literally never drawn once on one build, and the drawing code is present and un-gated | a liveness gate that meant "pointer is non-null" on build A and means "record contents look sane" on build B | §4 |
| A host guard blocks a whole feature on one build, asymmetrically — one item class works, its sibling never does | a host copy of a check the game's own router already does; needed only on the build that bypasses the router | §8 |
| Performance hooks missing on a dump that is "the same carrier build" | it is a different **revision**; a hash-keyed table falls back to generic entries silently | §5 |
| The boot log says a feature is inactive that has worked for weeks (or vice versa) | a hand-written capability line | §10 |
| An offset believed portable reads a live but unrelated field, and only one path misbehaves | an unverified high-region offset; 🔶 not ⛔ | §11, §12 |

---

## 15. Checklist

Before a second build ships from one codebase:

- [ ] Gates are on **three axes**; no feature asks "is this the build I reverse-engineered".
- [ ] The master predicate is still `false` on the unported build, and nothing flipped it.
- [ ] Every unlocked item went through the seven-step per-item checklist (§3), including a live poke.
- [ ] Anchors carry **shape**: value + index source + re-assert requirement; enums where the builds differ structurally.
- [ ] The table is keyed on the **hash of the member you verified**, and the key granularity is stated.
- [ ] Every accessor has a defined, documented `Unknown` answer; code defaults closed.
- [ ] One dependency-free table file, `include!`d by the app **and** the probe; no copy anywhere.
- [ ] Discipline tests pin ranges, recomputations, refusals of superseded addresses, and couplings.
- [ ] No mirror restates the table or any bound; deleted functions took their mirrors with them.
- [ ] The boot banner is **generated** from the tables and names the consequence of each `None`.
- [ ] The coverage matrix distinguishes 🔶 from ⛔ and records each 🔶 row's failure direction.
- [ ] The device audit line prints reference fields beside suspects, is read-only, and cannot panic.
- [ ] A build that boots with nothing measured is representable, and one is in the table.
- [ ] Every failed search left its blind spot written next to the entry it failed to change.
