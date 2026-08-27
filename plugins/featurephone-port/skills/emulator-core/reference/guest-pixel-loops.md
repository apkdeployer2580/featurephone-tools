# The guest's own pixel loops: census, host handoff, and the identity proof

A commercial feature-phone title frequently does not draw sprites through the platform API at all.
It asks for the screen framebuffer pointer once and then rasterises everything — sprites, panels,
dialogue bars, fades, hit flashes — with its own ARM code, one pixel at a time. Where that is true,
rendering cost is **interpreter** cost, your graphics layer barely participates, and the only large
lever is to find each of the game's pixel loops and hand it to a host implementation behind a trap
hook. This file is the census method, the loop classes to expect, the order to port them in, the
proof obligation that makes a handoff defensible, and the installer discipline that decides whether
your hooks ever run. The emulator's *own* draw-call path — framebuffer round-trips, row bands — is
[graphics-perf.md](graphics-perf.md). The frame budget these pixels have to fit inside is
[frame-pacing.md](frame-pacing.md). The trap hook as a member of the patch taxonomy is
[patching.md](../../guest-re/reference/patching.md) §3c.

**(A)** generic procedure · **(B)** worked example from the reference port, always introduced by the
method that found it · **(C)** do not copy · `⚠` inferred, nothing was run. See
[evidence-standard.md](../../../shared/evidence-standard.md).

## Contents

1. [First, count the platform draw calls per frame — per screen class](#1-first-count-the-platform-draw-calls-per-frame--per-screen-class)
2. [The census: sampled guest-PC profiling, and what a pixel loop looks like](#2-the-census-sampled-guest-pc-profiling-and-what-a-pixel-loop-looks-like)
3. [Four classes, sorted by what they READ](#3-four-classes-sorted-by-what-they-read)
4. [Relative per-pixel cost, and the order to port in](#4-relative-per-pixel-cost-and-the-order-to-port-in)
5. [Phase 0: install the hook as a counter with an unconditional fallback](#5-phase-0-install-the-hook-as-a-counter-with-an-unconditional-fallback)
6. [The decision rule: pixels per call must justify the trap round trip](#6-the-decision-rule-pixels-per-call-must-justify-the-trap-round-trip)
7. [Choosing the hook site: function entry vs outer row loop](#7-choosing-the-hook-site-function-entry-vs-outer-row-loop)
8. [The entry-hook trap: the handler sees the CALLER's stack pointer](#8-the-entry-hook-trap-the-handler-sees-the-callers-stack-pointer)
9. [The exit-address proof obligation](#9-the-exit-address-proof-obligation)
10. [Refuse, never clamp](#10-refuse-never-clamp)
11. [Every read before any write](#11-every-read-before-any-write)
12. [Install-time byte verification, and refusing to install](#12-install-time-byte-verification-and-refusing-to-install)
13. [Transcribe the guest's arithmetic in the guest's shape](#13-transcribe-the-guests-arithmetic-in-the-guests-shape)
14. [Bounding a table read you cannot bound statically](#14-bounding-a-table-read-you-cannot-bound-statically)
15. [The A/B identity proof — and why a matching hash can prove nothing](#15-the-ab-identity-proof--and-why-a-matching-hash-can-prove-nothing)
16. [Checking you did not create a trap storm](#16-checking-you-did-not-create-a-trap-storm)
17. [The installer must install every matching class](#17-the-installer-must-install-every-matching-class)
18. [Four things that only work when entries install as one unit](#18-four-things-that-only-work-when-entries-install-as-one-unit)
19. [Zero results: fatal for a keyed entry, a warning for a generic one](#19-zero-results-fatal-for-a-keyed-entry-a-warning-for-a-generic-one)
20. [Gauges: is it installed, is it firing, is it the same kind of work](#20-gauges-is-it-installed-is-it-firing-is-it-the-same-kind-of-work)
21. [What not to spend: a distribution measured on one scene](#21-what-not-to-spend-a-distribution-measured-on-one-scene)
22. [Symptom table](#22-symptom-table)
23. [Checklist](#23-checklist)

---

## 1. First, count the platform draw calls per frame — per screen class

**(A)** One counter decides which layer you optimise, and it costs nothing: instrument the platform
graphics entry points, print **calls per frame broken down by kind** (fill, blit, string) next to
**guest instructions per frame**, and drive the probe to each *screen class* separately — field
play, menu/panel, dialogue, shop or list, battle. Do not take this measurement once.

* Few platform calls **and** millions of guest instructions per frame → the game rasterises itself.
  This file.
* Many platform calls → the round-trip work in [graphics-perf.md](graphics-perf.md) applies.

**(B)** Method: a headless probe driven to a real field map with the draw-call trace and the work
gauge both on. Result: **12 platform draw calls per frame and zero image blits**, against **2.46 M
guest instructions per frame** — about 123 M inst/s to hold real time. The same title's menu and
panel screens were the opposite case: **1,650–2,450 small fill-rect calls per frame**, with the
worst single screen — a network modal — at roughly **112 ms of host work per frame** on a 2018-era
low-end phone.

> **REFUTED.**
> **Previously documented:** "the platform graphics path costs call count × full screen; on a title
> that issues hundreds of draw calls a frame this is the difference between shipping and not" —
> stated as the *first* thing to fix. **Refuted by:** the trace above, on a real field frame.
> **Actual mechanism:** the game fetches the framebuffer pointer once and never calls draw-image, so
> a completed round of round-trip optimisation "barely participates" in normal play. The claim was
> attractive because it is true of the platform layer in isolation *and* of the same title's panel
> screens — every measurement anyone had taken agreed with it.
> **Consequence if carried forward:** a whole round aimed at a layer the game does not use, with the
> real 26 % untouched. Both answers occur in one title; hence "per screen class".

## 2. The census: sampled guest-PC profiling, and what a pixel loop looks like

**(A)** Sample the interpreter's PC at a fixed interval into a histogram keyed by PC, then read it
with four rules:

1. **Exclude the idle PC first**, or it dominates every profile. **(B)** the idle PC alone was
   32–40 % of all samples, and it is not work.
2. **A pixel loop is FLAT.** Thirty to seventy adjacent PCs each holding a near-equal share is the
   signature of an un-unrolled tight loop, and the **sum of the cluster** is its cost. Without this
   rule a profile of an interpreted guest reads as noise — no single PC is ever large — and the
   porter concludes there is no hot spot.
3. **Difference-profile to isolate an event.** Run the deterministic probe to just before the effect
   and again through it; the cluster that appears is the loop. **(B)** the intro battle sat at frames
   ~3000–4200, so a 2950-frame run against a 4200-frame run isolated the flash loop by itself.
4. **Scope one tick when chasing a hitch.** Swap the histogram out per tick and merge it back after
   formatting, or a hot-PC list printed at a hitch carries the previous 30 s of idle samples.
   Mechanics in [diagnostics.md](diagnostics.md).

**(B)** What the clusters looked like, on two different compiles of one game: ~35 PCs spread almost
perfectly evenly over `0x86` bytes summing to **~21 %** of all samples; and, in a different
function, two inner loops summing to **26 %** (17.8 % walking left-to-right, 8.0 % right-to-left).
A cluster that flat is not a hot branch.

## 3. Four classes, sorted by what they READ

**(A)** Expect **several** loops, not one, and classify them by what they read — because that is
what tells you which of your counters can possibly see the next one.

| class | reads | writes | what sees it |
|---|---|---|---|
| (i) opaque palettised sprite blit | sprite indices + palette | dest | blit counters |
| (ii) alpha rectangle fill | dest, one source **colour** | dest | nothing blit-shaped; it draws no sprite |
| (iii) LUT / translucent sprite blit | sprite **and** dest, two LUT stages | dest | blit counters |
| (iv) tint rectangle | **dest only**, no source at all | dest | nothing — a per-pixel colour transform of what is already on screen |

**(B)** All four were found and hosted on one title, and a fifth cluster surfaced after (iv)
shipped. Class (iv) hid longest precisely because it is not a blitter. Its decisive measurement was
a per-tick counter delta — same number of blits, same ~140 K pixels, same ~1,300 traps, three
allocations, no asset parse, and **25× the guest instructions** (0.65 M → 16.05 M). "Traps did not
increase" is what proved it was pure guest compute that never talks to the host.

> **REFUTED.**
> **Previously documented:** the translucent-sprite hook (iii) had closed the reported frame drop.
> **Refuted by:** the player reporting the same hitch after it shipped, then the per-tick hitch
> report on the device. **Actual mechanism:** (iv) was a *separate function* sitting immediately
> after (ii) in the image, reading no source pixels at all. The wrong reading was attractive because
> both effects appear on the same game event and one hook had genuinely removed most of the other's
> cost. **Consequence if carried forward:** "we hosted the blitter, the hitch is still there" with no
> next move, because no counter exists for a loop that draws no sprite. Assume each loop you remove
> uncovers the next.

## 4. Relative per-pixel cost, and the order to port in

**(A)** Port in **measured-share order** (share = cluster sum from §2), not in the order you find
them. Per-pixel cost differs by an order of magnitude between the classes, so an infrequent loop can
outrank a constant one; and a full-screen rectangle beats any number of sprites.

**(B)** Costs from disassembly (instructions per pixel) crossed with profile share, and the win each
handoff produced:

| class | per pixel | share found | measured win |
|---|---|---|---|
| (i) opaque sprite blit | ~11 guest instr | 26 % of all guest instructions | **−36 %** of all guest instructions; 99.99 % of calls hosted |
| (ii) alpha rect fill | ~44 guest instr | ~21 % of samples | **−64 %** guest instructions (−72 % in some spans); 1.0 M → 0.4 M per frame; virtual-clock ratio 1.86× → 7.46× |
| (iii) LUT sprite blit | ~4× class (i) | battle spans only | **−24 %** over the battle span (−29…−40 % per sub-span) |
| (iv) tint rect | one LUT read per channel | one game event | a 25 ms tick had become 124 ms; ~113,000 pixels per call, about one full 496×240 screen |

A single full-screen fade through class (ii) was ~3.4 M guest instructions against ~1.0 M for an
entire ordinary field frame: rank by per-pixel cost × area, not by call count.

**(C)** Those shares belong to that title. What transfers is the heuristic: **loops that read the
destination are the expensive ones**, and a class averaging a screenful of pixels per call is worth
hosting even if it fires rarely.

## 5. Phase 0: install the hook as a counter with an unconditional fallback

**(A)** The first install implements no pixels. It reads the arguments, increments counters and
**declines unconditionally**, so the guest runs exactly as before. Measure, before writing any pixel
code: calls per frame, pixels per call, the distribution of every mode / pixel-format / transform
argument, and how often each gate clause *would* have refused. That answers "is this worth hosting
at all" and "which cases can I skip" for the price of a counter, before the expensive reverse
engineering — and it is the **only honest way to claim a case is unreachable**.

**(B)** Phase 0 on class (i) measured **191 calls/frame at 331 pixels/call**, with the transform
argument distributed **0 = 56.6 %, 1 = 31.3 %, 2 = 5.9 %, 3 = 6.2 %, 4…8 = 0 %**. The four non-zero
cases were implemented; the 0 % cases were deliberately left on the fallback (§10).

## 6. The decision rule: pixels per call must justify the trap round trip

**(A)** A hook costs one trap in and one jump out, per call, so the gate is a *ratio*: your host's
trap round trip over your guest's interpreted per-pixel cost. Measure both, in the same unit, rather
than importing a number: §5's Phase 0 hook declines unconditionally, so the **host-time delta with it
installed and removed** is the round trip; §4's per-pixel figures are in *guest instructions*, so
convert them through your interpreter's measured instructions per second.
Below the gate, either drop the candidate or move the hook site *outward* until one call covers more
pixels (§7). **(B)** The reference port never ran that arithmetic; it carried **~100 pixels per call**
as a judgement recorded at the time, which is why the next line cites the threshold rather than a
derivation. The shape you want is **rare calls each doing a lot of work**;
§16 is the measurement that proves you got it rather than trading interpretation for trap overhead.

**(B)** 331 pixels/call cleared a threshold recorded at the time as "under ~100 pixels per call the
trap round trip would not have been justified".

## 7. Choosing the hook site: function entry vs outer row loop

**(A)** There are two sites and they are not interchangeable.

* **Function entry** — when you can reproduce the *whole* function. The displaced instruction is the
  prologue, so declining means executing that prologue yourself and resuming two bytes on.
* **The outer row loop's first instruction** — when the prologue does clipping, context or
  format-dispatch work you do not want to reimplement. By the loop head everything you need is
  already spilled to the stack frame, so **one hook finishes the whole rectangle**; declining means
  performing the displaced load yourself and resuming two bytes on.

Prefer the loop head whenever the prologue contains behaviour you would otherwise have to guess at:
clip-rectangle construction, negative-origin clamping, a pixel-format branch, an early return.

**(B)** Both shapes shipped on one title: two loops hooked at entry (displacing a register-list
`push`), two at the outer-loop head (displacing a 2-byte stack load), sharing one
resume-past-the-push helper on the refusal path. What one loop head handed the host, read straight
off the frame — the instruction immediately before the hook site had just stored the row count:

| slot | meaning |
|---|---|
| `[sp+0x00]` | row stride in **bytes** |
| `[sp+0x08]` | current row's byte pointer |
| `[sp+0x10]` | rows left |
| `[sp+0x14]` | level / alpha, already zero-extended and range-checked by the guest |
| `[sp+0x18]` | pixels per row, **already clipped** |
| `[sp+0x50]`, `[sp+0x54]`, `[sp+0x58]` | the three channel selectors, already shifted |

## 8. The entry-hook trap: the handler sees the CALLER's stack pointer

**(A)** At a function-**entry** hook the prologue has not run, so SP is still the caller's: stack
arguments sit at `[SP+0]`, `[SP+4]`, … and **not** at the offsets a disassembly of the function body
shows. An argument the body reads at `[SP+0x80+0x18]` is read at `[SP+0x18]` from the hook. Getting
this wrong yields plausible garbage that looks exactly like a reverse-engineering error, and you
will re-derive "correct" offsets twice before suspecting the frame. A loop-head hook does not have
this trap — a second reason to prefer it. **(B)** written at the entry-hooked blitter's argument
reader so it cannot be lost: "the hook replaces the function's FIRST instruction, so the prologue
has not run and SP is still the caller's: the stack arguments start at `SP + 0`, not at the
`SP + 0x80` they sit at inside the function body."

## 9. The exit-address proof obligation

**(A)** A loop-head hook that finishes the rectangle has to jump somewhere, and the cheap answer is
the function's shared tail. That owes a **proof that the tail cannot depend on register or stack
state the loop would have produced.** The proof is: *the same tail is reached by the function's
do-nothing early exits.* Enumerate them and name them in the hook's header; if no do-nothing path
reaches your chosen tail then **you do not have the proof** — hook at entry instead, or write the
loop state back and let the guest's own loop-exit branch run. Write that state back as the guest's
own loop would have left it (rows 0, row pointer advanced) **even when the tail does not read it**:
it is free, and it removes an invisible dependency on compiler spill behaviour.

**(B)** Both loop-head hooks named three reaching do-nothing exits: the level/alpha byte meaning
"nothing to do", a non-positive height, and the 32-bit pixel-format path the hook never sees.

## 10. Refuse, never clamp

**(A)** **The guest function may do no clipping at all — its caller clips.** A host reimplementation
that re-clamps therefore produces *double* clipping, which A/B frame hashing catches only on the
frames where clipping happens: at screen edges, late, in play. So every case outside the hook's gate
**falls back to the guest**, and the fallback reproduces the displaced instruction exactly, so the
worst case is precisely the performance the build had before the hook existed. That is also what
licenses a narrow gate: you may refuse anything you like.

Refuse anything whose semantics you would have to guess:

* **Transform cases you did not analyse.** The transposing transforms — 90° rotations and diagonal
  flips — are legitimate *permanent* residents of the fallback. Where the guest keeps a whole
  separate copy of the loop per transform, hosting one transform is explicitly not hosting the
  others, and implementing an unreachable case only produces untested code.
* **Pixel formats other than the one you read**; null arguments; anything whose source, palette,
  table or destination bytes are not readable.
* **Degenerate or implausible geometry** — negative origins, non-positive strides, a row running
  past the destination stride. **(B)** `w <= 0` on the 4-bit-per-pixel path turns into a *two-pixel
  overrun* the guest really does produce at a clipped left edge; handing it back keeps that bug
  bit-exact. The general rule: a guest bug *inside* your gate must be reproduced (§13), a guest bug
  you do not understand must be refused.

**Count refusals per cause in separate counters** — format / transform / geometry / unreadable /
null. A gate that has silently shifted shows up as one cause climbing.

> **REFUTED.**
> **Previously documented:** the first handoff was planned on the assumption that the guest function
> clipped its own output, so the host would clamp to the destination. **Refuted by:** reading the
> call sites — the caller cuts with the application clip rectangle and the loop does none of it.
> **Actual mechanism:** clipping lives one level up. The assumption was attractive because every
> *platform* draw call clips, so a routine writing raw pixels "obviously" must too.
> **Consequence if carried forward:** double clipping — a subtly wrong image only near edges, on
> exactly the frames a short A/B run does not dump.

## 11. Every read before any write

**(A)** Order **all** reads — arguments, sprite bytes, palette, the whole lookup-table plane —
before the first pixel store. Then a refusal, an unmapped table or a failed read can never leave a
half-drawn rectangle on screen, and the refusal path becomes safe to take *at any time*, including
on a build where a table moved. Without this rule a partial write is indistinguishable from a
rendering bug, and it will be reported as one.

**(B)** Stated as an invariant in all four hook implementations: "reads are all finished before any
write begins"; "the whole 1 KB plane is read BEFORE any pixel is written".

## 12. Install-time byte verification, and refusing to install

**(A)** Every hook pins the halfword it displaces and **verifies it at install time; a mismatch is
fatal, not a warning.** Raw-byte patches carry an `expect` field of the original bytes under the same
rule. The reason is specific to this technique: the decline path *emulates* the displaced
instruction, so if the function moved you emulate a push that was never there and corrupt the guest
stack silently — the worst failure mode available, prevented by one comparison. Where a
build-specific address is fragile, match the hook by byte pattern instead; see
[build-keyed-anchors.md](../../guest-re/reference/build-keyed-anchors.md). **(B)** a second revision
of the same game had the loop relocated by **+0x74** — exactly the case this catches. Guest binaries
come in revisions differing by a few hundred bytes of code; assume yours will.

## 13. Transcribe the guest's arithmetic in the guest's shape

**(A)** Write the pixel math the way the guest writes it, **even where the shape looks wrong**. An
unmasked shift that relies on a 16-bit store to truncate stays unmasked. A green channel folded from
six bits to five stays folded. An algebraically-equal "tidy" yields the same value today and a
divergence later, and it destroys the next reader's ability to tell a port bug from a guest quirk.
Pin the resulting mask with a unit test that round-trips the identity.

**(B)** Two instances, both recorded at the definition: the red accumulator is masked with a
pc-relative literal rather than shifted, and the 16-bit store does the truncation; and the green
field is halved to five bits **in two independent functions** — which is how you know the loss is
the game's design and not the port's. That second function also derived the same level/selector
table layout from different code: an independent cross-check on the first reading.

## 14. Bounding a table read you cannot bound statically

**(A)** For each per-call table index, either prove it in-plane from the field widths or bound it
**dynamically**: scan the plane once per call and refuse if any entry exceeds the index range. At
1 KB per call that is free against the sprite the call is about to draw, and it converts "probably in
range" into a refusal you can count. This is the pattern for any guest loop that indexes tables the
game builds at runtime. **(B)** stage one indexes a 32×32 plane as `s*32 + d` with both operands
5-bit, so it is in-plane by construction; stage two indexes with a **stage-one result byte**, which
carries no such guarantee, so the hook validates the whole plane per call and thereby proves every
stage-two index is at most `31*32+31`. The game *builds* those planes at runtime, which is also why
nothing can be cached across calls.

## 15. The A/B identity proof — and why a matching hash can prove nothing

**(A)** The proof obligation for a hosted loop is a **bit-identical frame-hash A/B from a
deterministic run whose only difference is the hook**.

* **Isolation mechanism 1:** a runtime master switch (an `--hle 0|1` style boolean), so the *same*
  binary and the *same* input run both ways and any pixel difference is unambiguously the host
  implementation's fault.
* **Isolation mechanism 2**, for hooks that cannot be switched at runtime: add and remove the patch
  table entry.
* Quantise your output through the same pixel-format round trip before comparing, or you are diffing
  precision rather than correctness.
* **A matching hash proves nothing unless a fire counter shows the hook actually ran in that run.**
* Run it on at least two game states, and dump frames explicitly — a differential check gated on a
  dump-frame list prints nothing when the list is empty, which looks exactly like success.

**(B)** Results across the four handoffs: **7/7, 5/5 and 7/7 identical dumped frames**. And the
failure mode was hit for real: one A/B ran a title-screen loop the new hook never reached, so its
hashes matched perfectly and proved nothing — firing had to be established afterwards from the
device's own counter. Protocol and flags in
[probe-cli.md](../../guest-re/reference/probe-cli.md).

## 16. Checking you did not create a trap storm

**(A)** Across the same A/B, the **total host-call (trap) count must be essentially unchanged** while
guest instructions fall. A large trap increase means the handoff boundary is inside the loop rather
than around it — you have traded interpretation for round-trip overhead. The counter already exists,
so this check is free.

**(B)** Over an 8,600-frame A/B: traps **5,640,894 → 5,647,559** (+0.1 %) while guest instructions
fell **64 %**, per-frame guest instructions went 1.0 M → 0.4 M, and the virtual-clock ratio went
1.86× → 7.46×.

## 17. The installer must install every matching class

**(A)** A table-driven patch installer that **selects among entry classes** must install **every**
matching class. Returning after the first match is the bug, and it is invisible: the installer still
logs a plausible count.

> **REFUTED.**
> **Previously documented:** the generic, hash-less pattern entries in the patch table are installed
> on every build. They were in the table, they had tests, and the boot log printed a hook count.
> **Refuted by:** reading the installer — a hash match `return`s — plus the log line that had been
> visible all along, `Installed 1 hooks`, for months.
> **Actual mechanism:** a title with its own hash-keyed entry received **none** of the hash-less
> entry's hooks. Six compiler-library hooks had never once been installed on any build. The two kinds
> of entry answer different questions and were never in competition: a hash-keyed entry names
> addresses inside **one build of one game**, while a hash-less entry matches compiler-library **byte
> patterns** belonging to the toolchain. "First match wins" is the right rule for *selecting a build*
> and the wrong rule for *selecting work*.
> **Consequence if carried forward:** an optimisation that was written, tested, documented and never
> ran. **The count an installer logs must be owned by someone.**

**(A) Honest null result — keep it in the record.** After the fix, the measured gain on the shipping
build was **exactly zero**. **(B)** identical instruction, trap and batch totals across the same
8,600-frame run before and after (instructions 8,793,014,905 / traps 5,640,894 / batches
13,533,404), and no crash: the generic patterns had been written for a different compiler's idioms
and matched nothing. Keep the structural fix, record the null, and note where a newly written pattern
would attach. The transferable claim is that **compiler-idiom patterns do not cross toolchains** — a
porter who assumes "library hooks" are free performance will spend a session on them.

## 18. Four things that only work when entries install as one unit

**(A)** The obvious fix — call the single-entry installer twice — **aborts the boot**, because that
function ends by registering the trap dispatcher and the second registration is fatal, before the
first frame. Four things have to happen together, which is why this is not a loop:

1. **Resolve every entry's hooks first**, into one combined list.
2. **Check patch-versus-hook overlap against the combined list.** Given one entry's hooks the check
   can only see that entry's share, so a generic raw-byte patch could silently overwrite a
   title-specific hook.
3. **Register the dispatcher exactly once**, at the end.
4. **Define collision priority.** Order the selection hash-keyed-entry-first and let the **first**
   hook at a given PC win, logging a warning for the loser, so a generic library pattern cannot
   shadow the game-specific hook at the same address.

Then the single-entry function becomes a one-element wrapper over the multi-entry one. Omit any one
of the four and the fix becomes a new, worse bug: a boot abort, a clobbered hook, or a generic
pattern shadowing the specific one.

## 19. Zero results: fatal for a keyed entry, a warning for a generic one

**(A)** An entry keyed to a specific binary that produces **zero** patches and zero hooks is
**fatal** — the table has drifted from the binary. A generic pattern entry producing zero only
**warns**. The asymmetry is the entire safety property: it converts "the optimisation silently
stopped applying after a binary or asset update" — the failure mode that hid the six uninstalled
hooks in §17 — into a boot-time error.

**(A)** Pass the *is-specific* flag from the caller rather than inferring it from the presence of a
hash, so a placeholder all-zero hash in a test fixture cannot trip the fatal path.

## 20. Gauges: is it installed, is it firing, is it the same kind of work

**(A)** Three, all in the periodic device log, all as **interval rates that reset on read** — a
lifetime average mixes the title screen, where the loop mostly sleeps, with real play, so a low
number is ambiguous:

1. **Is it installed** — the hosted/total call ratio. Its collapse to **0** is the *only* signal
   that the binary patches did not install and the device is interpreting everything again. Hooks are
   keyed to binary bytes, so a binary or asset update disables them silently and the only visible
   effect is that the game is slow.
2. **Is it declining** — the per-cause refusal counters from §10, printed beside the ratio.
3. **Unlike work gets its own counter.** A hook that is not the same *kind* of work gets its own
   calls / hosted / pixels triple. Folding a colour-transform hook into the sprite-blit counters
   quietly changes what the headline ratio means — and that ratio is the gauge everything else leans
   on.

**(B)** The shipped line's shape, every 30 s, alongside throughput: `blit <hosted>/<calls>, refused
[format, transform, geometry, unreadable, null], tint <hosted>/<calls> (<pixels> px)`. The reasoning
for keeping the second triple separate is written at its definition: those counters mean "a *sprite*
the host drew instead of interpreting", and that function draws no sprite.

## 21. What not to spend: a distribution measured on one scene

**(A)** A distribution measured on one scene does not transfer to another. **Re-measure before
spending a session on a remaining fallback case** — the counter that would refute the plan already
exists, and a leftover TODO with a percentage attached reads as a queued win.

**(B)** A recorded "next optimisation candidate" — a transposing-transform fallback taking 30 % of
one function's calls *in the scene where it was measured* — became the leading hypothesis for a later
hitch. The tick-level gauge killed it immediately: **~5 refusals/s against ~10,600 calls/s, 0.05 %**.
The note now says "do not go down this road again". The same case had already measured **0 %** in
another scene (§5): a permanent fallback resident either way.

**(A)** The corollary for finding the loop the aggregate gauge cannot see: a 30 s gauge dilutes a
few-hundred-millisecond hitch roughly 100:1, so add a per-tick report that prints the delta of every
counter across any tick over budget, and read the **guest-instruction delta** first — millions means
the interpreter and therefore an unhosted loop; a few thousand alongside a large asset-parse or
allocation delta means host work on the same thread. Owned by [diagnostics.md](diagnostics.md).

## 22. Symptom table

| symptom | cause | where |
|---|---|---|
| Busy maps stutter, the graphics layer looks healthy, optimising it changes almost nothing | The game never calls the platform's draw-image: it took the framebuffer pointer once and rasterises in guest code | §1, §2 |
| A guest pixel loop was hosted and shipped; the reported hitch is still there | There is more than one loop, and the remaining one may read **no source pixels**, so every blit counter reads normal | §3, §4 |
| A specific effect hitches for a few hundred ms while the periodic gauge looks normal | ~100:1 dilution in a 30 s aggregate | §21, [diagnostics.md](diagnostics.md) |
| A hosted loop's output shifts or clips wrongly near screen edges | Double clipping — the guest function does not clip, its caller does, and the host clamped again | §10 |
| A hosted loop leaves a half-drawn or half-tinted rectangle | Writes began before all reads (including a runtime-built table) completed | §11 |
| Arguments read at the hook are garbage that looks like an RE error | A function-entry hook sees the **caller's** SP; stack arguments are not at the body's offsets | §8 |
| The A/B frame hashes are identical but the hosted loop clearly is not helping | The hook never fired in that run | §15 |
| Guest instructions barely fall and the trap total jumps | The handoff boundary is inside the loop, not around it | §6, §16 |
| The boot log says "Installed 1 hooks" and the generic patterns never fire on any build | The installer returned after the first matching entry class | §17, §18 |
| The hosted ratio in the device log is 0 after a binary or asset update | The hooks are keyed to bytes that moved; install refused, or the entry no longer matches | §12, §19, §20 |

## 23. Checklist

Before you claim a guest pixel loop is hosted:

- [ ] Platform draw calls per frame counted **per screen class**, with guest instructions per frame
      beside them, and this file is the branch the numbers chose.
- [ ] The loop was found as a **flat cluster** in a guest-PC profile with the idle PC excluded, and
      its cost is the cluster **sum**.
- [ ] Loops classified by what they read; you have named which of your counters can see each, and you
      expect the next one.
- [ ] Phase 0 shipped first: counter, unconditional fallback, calls/frame, pixels/call, argument
      distribution.
- [ ] Pixels per call justifies the trap round trip, or the hook site moved outward.
- [ ] The hook site is chosen deliberately (entry vs loop head), and at an entry hook the stack
      arguments are read at the **caller's** SP.
- [ ] The exit target is proved by naming the do-nothing early exits that reach it.
- [ ] Everything outside the gate **refuses** and falls back, reproducing the displaced instruction
      exactly; transposing transforms and unread formats are on the fallback on purpose.
- [ ] All reads precede the first write.
- [ ] The displaced bytes are verified at install time and a mismatch is fatal.
- [ ] The pixel arithmetic is the guest's shape, quirks included, with an identity round-trip test.
- [ ] Every runtime table index is proved in-plane statically or validated per call.
- [ ] A/B frame hashes identical on at least two game states, **with a fire counter proving the hook
      ran**, and the trap total essentially unchanged.
- [ ] The installer installs every matching entry class as one unit; a keyed entry yielding zero is
      fatal, a generic one warns.
- [ ] The hosted ratio, per-cause refusals, and any unlike work's own counters ship in the periodic
      device gauge as interval rates.
