# Footprint: the low-memory kill that looks exactly like a crash

An emulator that reserves a large guest address space is structurally the fattest process on the
phone, so it is first in line when the platform needs memory back. The kill leaves no tombstone and
no error line, and the player reports it in the same words as a crash — *"I switch to another app
and come back and the game restarts from the title."* This file owns **process footprint and
OS-initiated kills**: how to tell a kill from a fault, how to size the guest heap against what the
guest actually touches, and what a long session does to the number. The guest allocator's own
defects (monotonic block growth, allocation-failure recursion) are
[hardening-batch.md](hardening-batch.md) §1–§3; crash triage as a *workflow* is
[../../port-shipping/reference/crash-triage.md](../../port-shipping/reference/crash-triage.md).

Tags: **(A)** generic procedure · **(B)** worked example from the reference port, always introduced
by the method that found it · **(C)** do not copy · `⚠` inferred, nothing was run.

## Contents

1. [Two exit reasons, one player report](#1-two-exit-reasons-one-player-report)
2. [Reading them apart: tombstones vs the exit-info list](#2-reading-them-apart-tombstones-vs-the-exit-info-list)
3. [The eager `map()` that commits the heap before the first frame](#3-the-eager-map-that-commits-the-heap-before-the-first-frame)
4. [Lazy commit: permission on map, allocation on first write](#4-lazy-commit-permission-on-map-allocation-on-first-write)
5. [Why a read must NOT commit](#5-why-a-read-must-not-commit)
6. ["Is it mapped" needs its own bitmap](#6-is-it-mapped-needs-its-own-bitmap)
7. [Keeping the page index bounds-check-free](#7-keeping-the-page-index-bounds-check-free)
8. [Publish committed pages as a LEVEL — and long sessions](#8-publish-committed-pages-as-a-level--and-long-sessions)
9. [Check the layer below your careful layer](#9-check-the-layer-below-your-careful-layer)
10. [Vendored dependencies: a read-only deep copy](#10-vendored-dependencies-a-read-only-deep-copy)
11. [Proving such a change invisible: count the uses](#11-proving-such-a-change-invisible-count-the-uses)
12. [Symptom table](#12-symptom-table)
13. [Checklist](#13-checklist)

---

## 1. Two exit reasons, one player report

**(A)** *"The app just closes"* is at least two unrelated faults needing different evidence: a
process that **crashed** leaves a native tombstone; a process the OS **killed** leaves nothing but
an exit record. Closing one axis does not close the other. The tell that you are chasing a closed
axis: **the tombstone timeline stops at the date your fix was installed while the reports keep
arriving.**

**(B)** *Method: read all three device records in one session and cross-tabulate them against
install dates.* That produced eight native crashes all ending on one date (the allocation-failure
recursion of [hardening-batch.md](hardening-batch.md) §3, already fixed), the platform's crash-log
store confirming the last of the eight *predated* the fix's install, and the process-exit history
holding eleven further deaths of the **post-fix** install, none of them crashes. One axis provably
closed, the other provably open, in one sitting. `HIGH / CONFIRMED` — three sources agreeing.

## 2. Reading them apart: tombstones vs the exit-info list

**(A)** The three on-device sources answer three different questions. Ask the right one.

| source | answers | notes |
|---|---|---|
| tombstone directory | crash **detail** — signal, cause line, backtrace, registers | nothing at all for a kill |
| system crash-log store | the same crashes **with timestamps**, surviving reboots | dates the last occurrence, so you need no reproduction |
| process-exit history | **every** death, including the ones with no crash | carries a reason code *and* the resident size at death |

The third is the only source with any evidence about a kill; without it an investigation concludes
"no crash, so no bug".

**(B)** *Method: dump the exit history for the app's uid and read the reason codes as a list, not
one at a time.* Invocation:
[../../port-shipping/reference/crash-triage.md](../../port-shipping/reference/crash-triage.md) §2.
The reference port's list was:

```
#0        reason=3  (LOW_MEMORY)               rss=104 MB
#1..#10   reason=10 (user requested)           rss=520-551 MB
```

★ **(A) The RSS on the kill record is a trap.** It is the size at the moment of death, *after* the
platform has trimmed the process, and it can be the smallest number in the list. The evidence for
*why you were chosen* is the **neighbouring ordinary exits**, whose recorded sizes are your normal
footprint. Half a gigabyte on every voluntary exit is the finding; the 104 MB on the kill is not.

**(A)** No in-process gauge can see any of this: the process does not fail, it is killed, so every
memory diagnostic you own runs inside the thing that stops existing. Footprint is diagnosed from
outside the process and defended with a gauge you read afterwards (§8).

## 3. The eager `map()` that commits the heap before the first frame

**(A)** In this family of cores the guest heap is created with a single `map(base, size)` call from
the allocator's `init`. If `map` **allocates and zero-fills every page of the range**, the whole
reservation is resident before the first frame, whether or not the guest ever touches it. Nothing
asked for those pages: the reservation is a build-time constant chosen to be generous, not a
measurement.

**(B)** *Method: root-cause downwards from the exit record — dump the OS memory report for your own
process while it sits on the title screen, attribute the bulk to a region (native heap, graphics,
code), then grep the core for the call that creates that region.* That chain was three steps and
terminated at one line: `map(HEAP_BASE, 0x1000_0000)` — 256 MB, 4096 × 64 KB pages — whose loop
boxed and zeroed every page. The title-screen dump read **native heap RSS 387,540 KB, process RSS
545,876 KB**. `HIGH / CONFIRMED`, two independent derivations: the exit records and the live dump
agree on the same magnitude.

**(A) Do not fix this by shrinking the reservation.** A guest that allocates past the reservation
fails, and you cannot know the ceiling in advance. Make the commit lazy so the reservation is free,
then *measure* what the guest touches (§8) and keep the generous number.

## 4. Lazy commit: permission on map, allocation on first write

**(A)** The shape, in three rules:

- `map` records **permission only** — zero allocations.
- A page is boxed on its **first write**.
- A read of a mapped page that was never written answers **zero without allocating** (§5).

```rust
/// `Ok(None)` = mapped, never written, all zeroes. `Err` = never given to the guest.
pub fn page_for_read(&self, i: usize) -> CoreResult<Option<&[u8; PAGE_SIZE]>, ()> {
    match self.pages[i].as_deref() {
        Some(page) => Ok(Some(page)),
        None if self.is_page_mapped(i) => Ok(None),
        None => Err(()),
    }
}
pub fn page_for_write(&mut self, i: usize) -> CoreResult<&mut [u8; PAGE_SIZE], ()> {
    if self.pages[i].is_none() {
        if !self.is_page_mapped(i) { return Err(()); }
        self.pages[i] = Some(Box::new([0; PAGE_SIZE]));
        self.committed += 1;                       // the gauge of §8
    }
    Ok(self.pages[i].as_deref_mut().unwrap())
}
```

**(B)** Device A/B, same phone, same screen, only this change:

| | before | after | delta |
|---|---|---|---|
| native heap RSS | 387,540 KB | 133,356 KB | **−66 %** |
| process RSS | 545,876 KB | 290,140 KB | **−47 %** |
| total PSS | 440,427 KB | 183,194 KB | **−58 %** |

**(C)** The absolute figures are one handset, one build, one screen. Do not carry them as targets;
the *ratio* and the attribution argument below are the transferable parts.

**(A) The attribution test.** The saving (≈248 MB) matches the size of the thing you stopped doing
(256 MB) almost exactly — *that* is what proves the reduction was the heap and not something else
that moved in the same build. A footprint win whose magnitude does not match its supposed cause is
unattributed; find the rest of it before you write the number down.

**(A)** Keep the page table **dependency-free** — no logging crate, no core error type — and report
faults as `Err(faulting_address)`, letting the caller map that to the core's error. That is what
lets the module be `include!`d by a standalone test binary on a host where the core's own
`cargo test` cannot build; see [../../../shared/selftest-convention.md](../../../shared/selftest-convention.md).

## 5. Why a read must NOT commit

**(A)** A read of a mapped-but-never-written page returns zeros **without** committing. This is not
an approximation: an eagerly mapped page was zero-filled, so the bytes are *identical*. And it is
load-bearing, not tidiness — **a guest-side collector or allocator that walks the free heap would
otherwise re-materialise everything the laziness saved**, so a committing read decays the fix back
to eager at the first collection. **(B)** On the reference port that walker is the KTF-family
collector, which scans the heap's free region; the rule is recorded at the definition site for
exactly that reason.

**(A)** Three details a straightforward implementation gets wrong, each worth a test:

- A range read must **write** the zeros into the caller's buffer, not skip it — the destination is
  the caller's memory and may hold anything. Test with a buffer pre-filled with `0xff`.
- A range spanning a **committed page and an uncommitted one** must join them. "If the page is
  missing return zeros" passes the easy case and corrupts this one.
- An **unmapped** access must still fault, must report the address where it actually went wrong
  (not the range start), and must commit nothing on the way out.

## 6. "Is it mapped" needs its own bitmap

**(A)** Once commit is lazy, *the presence of a page allocation stops being the answer to "is this
address mapped".* Conflate them and a freshly mapped heap reports unmapped until something writes
to it, at which point the core's own guest-pointer validity checks begin **rejecting perfectly good
pointers**. This is the bug that follows the lazy-commit change by about ten minutes, and it
presents as unrelated invalid-memory faults on addresses that are unquestionably mapped. Keep a
separate bitmap, one bit per page, as the **only** thing the mapped query consults — **(B)** for a
32-bit guest at 64 KB pages, 65,536 bits = 8 KB.

Tests that pin it: a mapped never-written range answers mapped; one byte before the start and one
page past the end answer unmapped; a sub-page `map` rounds out to the whole page (small mappings
such as a patch table's scratch page rely on that); and `map` sets **only** the requested bits — a
single stray bit converts a guest bug from a fault into silent corruption.

## 7. Keeping the page index bounds-check-free

**(A)** Hold the page array as a **fixed-size array behind a `Box`**, not a boxed slice. The length
stays a compile-time constant, so an index derived from a 32-bit address is provably in range and
the bounds check still optimises away on the interpreter's every load and store. A `Box<[_]>` keeps
a runtime length and puts a real check on the hottest path in the program. *A footprint fix that
costs a bounds check per guest memory access has paid for itself in RSS and lost it in throughput.*

**(A)** Build it through a `Vec`, not `Box::new(array::from_fn(..))`: the latter materialises the
whole table as a **stack temporary**, which a non-optimised build does not elide. **(B)** 512 KB of
temporary on the reference core — it overflowed a 2 MB test thread outright. And
`Option<Box<[u8; PAGE_SIZE]>>` is niche-packed to a nullable pointer, so the per-access cost of the
whole scheme is one load and one null test.

## 8. Publish committed pages as a LEVEL — and long sessions

**(A)** Every memory- or work-saving change ships with a gauge reporting the saved quantity as a
**level**, so a later regression is a number climbing back toward the old value rather than a vague
slowdown. Publish it with a setter, republished once per instruction batch, because the page table is
deliberately dependency-free and cannot reach the stats module (§4), and put the interpretation next
to the definition rather than in a document. It must appear in the **periodic device log**: the
platform's own memory tooling is not available to the app about itself, so on a release build you
have no other channel. Gauge conventions are in [diagnostics.md](diagnostics.md) and
[../../port-shipping/reference/on-device-diagnostics.md](../../port-shipping/reference/on-device-diagnostics.md).

**(B)** On the reference port it rides the end of the throughput line as `guest mem 7 MB`, and it
settled the sizing question outright: **4096 pages committed to serve about 112.** The comment at
the definition says what a reader should do about it — *if it climbs back toward 256, the saving has
been undone somewhere.*

**Long sessions: the number that gets you killed is a peak, not an average. (A)** The kill happens
while you are backgrounded, which is *after* a session, at your high-water mark. Two consequences:

- ⚠ In this design a committed page is never handed back, so the level is monotonically
  non-decreasing within a run and its plateau — not the title-screen figure — is your real
  footprint. The reference port's tables are all title-screen measurements and the never-freed
  property is readable in the code, but the session curve was never logged. *What would settle it:
  log the level every 30 s across an hour of real play and read the shape.*
- Anything per-call that never frees adds to that peak directly: a handle-minting platform API with
  an unimplemented destructor leaks one asset copy per call forever, and an uncached asset re-parse
  grows without bound. Cap every cache so that **overflow stops caching rather than growing** — then
  the worst case is exactly the old behaviour. **(B)** A 12 MB cap over a 6.1 MB decompressed asset
  set never engaged. Those two mechanisms are owned by [audio-core.md](audio-core.md) §8–§9; put
  their counters in the same periodic line as the level, because a handle count climbing without
  bound is the only warning you get.

## 9. Check the layer below your careful layer

**(A)** When a careful optimisation is not visible in the numbers, **the layer below it may be
defeating it — and the comments will not tell you, because each layer is locally right.** Make this
an explicit audit step rather than a reaction.

> **REFUTED.** *"Both guest allocators avoid touching pages they have not handed out, so the guest
> heap's resident cost tracks actual use."*
> **Refuted by:** the title-screen memory dump of §3 — full reservation resident, on every device,
> on every build, from boot.
> **Actual mechanism:** the belief was *true of both allocators*. The bucket allocator's bump index
> exists precisely so slots never handed out are never written, and its comment says so ("pre-linking
> would touch every page of a 120 MB region"). The mapper one layer beneath them had already
> committed and zeroed all 256 MB before the first frame, so the entire effort bought nothing.
> **Consequence if carried forward:** you read the comment instead of measuring, and a whole class of
> footprint work looks done while the process stays the kill list's first entry.

Those two allocator designs are [hardening-batch.md](hardening-batch.md) §1–§2. The point here is
only that a locally correct optimisation can be spent from underneath.

## 10. Vendored dependencies: a read-only deep copy

**(A)** A dependency's memory habits decide whether the process survives backgrounding on the class
of device where your port is marginal. Audit the resident cost of **every vendored library that
loads a large read-only table**, and treat a per-reference deep copy of such a table as a footprint
bug in your own app.

**(B)** *Method: compare the number of records the library builds against the number the file
format describes, then multiply by the record size.* The reference port's synth deep-copied a whole
instrument for every preset zone naming one: a general-purpose sound bank with 324 instruments and
2,900 preset zones produced **24,155** zone records instead of the 2,482 the file describes (9.7×),
at ~2 KB each (a fixed generator array dominates) ≈ **47 MiB of pure duplication**. The decoded
sample data was *already* shared behind a reference count — the duplication was not the samples,
which is why a naive "audio uses a lot of memory" reading would have optimised the wrong thing. Fix:
share each instrument behind a reference count with a per-source cache. Device A/B, same build, only
this change, on the oldest supported handset (2018-era, 3.5 GB): native heap PSS 271,612 → 218,892 KB
(**−52,720 KB**); total PSS 379,339 → 326,085 KB (**−14 %**). That device was running with 116 MB
free and 921 MB of swap in use, and the low-memory killer was the only surviving exit reason on the
project — so this was not decoration.

**(A)** Fork hygiene: mark every changed hunk with a searchable token, and keep the *why* of each
fork in the patch table's comment where a reader of the manifest sees it — see
[vendoring.md](vendoring.md). A vendored fork is invisible to a normal read of the dependency, in
both directions: you will also waste a day re-solving a bottleneck the fork already fixed.

## 11. Proving such a change invisible: count the uses

**(A)** "Read-only after load" is the whole argument that sharing behind a reference count changes
nothing: if nobody mutates the structure, the parsed data, the rendered output and the behaviour
cannot differ. Establish it by **counting the field's uses across the entire crate** and naming them
in the commit message — that count is what makes the change reviewable by someone who does not know
the library. **(B)** The shared instrument field had exactly two use sites, both reads: voice start,
and a global-zone test during import. Two lines of evidence for a 47 MiB change.

**(A)** The equivalent argument for lazy commit is **byte-identity of the read path**, pinned by an
executable suite rather than by inspection: mapping the whole heap allocates nothing; a write commits
exactly the pages touched (including both halves of a straddling write); a read of an uncommitted
page is zero and commits nothing; a read spanning both kinds joins them; an unmapped access still
faults with the right address. **(B)** Eleven such tests, run against the real module via `include!`
rather than a copy of it.

**(A)** What counting uses does **not** prove: that a *later* change will keep the field read-only.
Prefer a shape that enforces it — handing out an immutable shared handle does this for free — over a
comment asking future readers to be careful.

## 12. Symptom table

| symptom | cause | section |
|---|---|---|
| App restarts from the title after switching away and back; no tombstone, no error line | OS low-memory kill; read the exit-info reason code | §1–§2 |
| Kill record shows a small RSS while ordinary exits show half a gigabyte | Normal — the kill record is post-trim; the neighbours are the evidence | §2 |
| Process RSS is roughly the guest heap reservation from the first frame onward | `map` allocates and zeroes the whole range eagerly | §3–§4 |
| Freshly mapped guest memory is rejected as an invalid pointer, right after making commit lazy | Mapped-ness answered from the presence of a page allocation | §6 |
| Footprint fell but throughput fell too | A boxed slice put a runtime bounds check on every guest load and store | §7 |
| The committed-pages gauge climbs back toward the reservation | Some later change re-introduced an eager commit | §8 |
| Process RSS climbs tens of MB and the culprit is a vendored asset or audio library | Per-reference deep copy of a read-only table | §10 |
| Handle or parse counters climb without bound over a session | A leaked handle lifecycle or an uncached re-parse — a peak problem, not a rate problem | §8, [audio-core.md](audio-core.md) §8–§9 |
| Runs fine, gets progressively slower, then one large allocation fails with plenty free | The guest allocator, not footprint | [hardening-batch.md](hardening-batch.md) §1 |
| Crash with an alternating two-address backtrace on allocation failure | Allocation reporting that allocates | [hardening-batch.md](hardening-batch.md) §3 |

## 13. Checklist

- [ ] Exit-info reason codes read **separately** from the tombstones; each axis marked open or
      closed with a date, and the resident sizes of the *ordinary* exits recorded too.
- [ ] `map` allocates nothing; a page is committed on its first write.
- [ ] A read of a mapped-but-unwritten page returns zeros and commits nothing — tested with a
      destination buffer pre-filled with non-zero bytes, and with a range spanning both kinds.
- [ ] An unmapped access still faults, reports the faulting address, and commits nothing.
- [ ] Mapped-ness comes from its own bitmap; `map` sets only the requested bits and rounds sub-page
      requests out.
- [ ] The page array is a fixed-size array behind a `Box`, built through a `Vec`.
- [ ] Committed pages published as a level, per batch, in the periodic device log, with the
      regression threshold written at the definition.
- [ ] The measured saving matches the size of the thing you stopped doing; if not, the rest is
      unaccounted for.
- [ ] The level has been read at the **end** of a long session, not only at the title screen.
- [ ] Every cache has a cap whose overflow stops caching instead of growing.
- [ ] Every vendored library that loads a large read-only table checked for per-reference
      duplication, and any sharing change names its read sites.
