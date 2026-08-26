# Host windows and modals

Windows the **host** draws over the guest — notices, pickers, quantity spinners, wait boxes,
confirmations — and the four things that decide whether a player experiences one as part of the game
or as a fault: which inputs it accepts, which competing modal outranks it, how big it is, and whether
you can tell what is selected. What such a window should *look* like — the chrome measured from the
game's own UI sheets, its font, its icons, its palette per window kind — belongs to
[game-native-ui.md](game-native-ui.md); the touch chain and key-edge machinery it plugs into belong
to [input-and-hud.md](input-and-hud.md). Every rule below was paid for by a report from someone
holding a phone, and most of them are invisible to a desk test.

Tags: **(A)** generic · **(B)** worked example from the reference port, re-derive it ·
**(C)** do not copy. `⚠` marks anything inferred.

## Contents

- [1. What a host window owes the player](#1-what-a-host-window-owes-the-player)
- [2. ★ A notice must not swallow the buttons the thumb is already on](#2--a-notice-must-not-swallow-the-buttons-the-thumb-is-already-on)
- [3. Order competing modals by specificity — in both paths](#3-order-competing-modals-by-specificity--in-both-paths)
- [4. ★ A window that cannot be answered draws no confirm band](#4--a-window-that-cannot-be-answered-draws-no-confirm-band)
- [5. The window must not be answerable by the input that opened it](#5-the-window-must-not-be-answerable-by-the-input-that-opened-it)
- [6. Sizing: percentage-with-clamp, and which term is binding](#6-sizing-percentage-with-clamp-and-which-term-is-binding)
- [7. ★ Row height sets the font scale, so a symmetric shrink undoes a readability fix](#7--row-height-sets-the-font-scale-so-a-symmetric-shrink-undoes-a-readability-fix)
- [8. Geometry in the guest's logical pixels, scaled once](#8-geometry-in-the-guests-logical-pixels-scaled-once)
- [9. Focus indication that reads at arm's length](#9-focus-indication-that-reads-at-arms-length)
- [10. Symptom table](#10-symptom-table)
- [11. Checklist](#11-checklist)

---

## 1. What a host window owes the player

> **★ (A) An affordance is a promise.** Anything you draw that looks pressable must be pressable,
> and anything the player can *already* press must keep working while your window is up.

Four obligations, each with its own failure mode and its own section:

| the window owes | broken by | section |
|---|---|---|
| accepting every input the player will plausibly use to answer it | an early `return` in the touch arm | §2 |
| being dismissible at all | a more general modal tested first | §3 |
| **not** advertising an answer it will ignore | one painter for answerable and unanswerable boxes | §4 |
| not answering itself with the press that opened it | no de-duplication above the modal arm | §5 |
| being legible, and showing what is selected | a percentage that never binds; one-cue focus | §6-§9 |

**(A) A refusal must be visible and self-explaining, which means it is rendered as a *disabled row
with its reason*, not as an absent row.** Put every rejection **inside** the row builder. A filter
that runs upstream of the builder makes the candidate simply missing, and then the report arrives as
a question about the game's rules rather than as a bug.

**(B)** CONFIRMED, and it is a refutation of the reference port's own written principle: a picker
module carried a comment saying exactly this — *"my sword is not in the list" is indistinguishable
from broken, but "this item has no sockets" explains itself* — while the category filter one layer
above it removed a whole equipment class **before** the list was constructed. The affected items
produced not even a greyed row, and the report came in as *"is this item type just not supported?"*
**The audit that follows:** wherever a module states this principle, check every filter upstream of
its list builder.

## 2. ★ A notice must not swallow the buttons the thumb is already on

> **(A) In a modal's touch arm, accept the modal's own band OR the HUD's confirm OR the HUD's
> cancel — and for an informational box treat cancel as a second confirm. Do not return from the
> handler before the HUD button list has been hit-tested.**

The mechanism is the ordered, first-claim-wins hit-test chain in
[input-and-hud.md §1](input-and-hud.md#1-the-ordered-hit-test-chain). A modal sits near the top of
it, so an early return there means the entries *below* it never run — including the game button
list, which is where the player's thumb already is. One extra hit test against that list also covers
the pull-out keypad, so the fix is a single arm, not three.

> **REFUTED.** *Previously documented* (implicitly, by the shipped code): "the modal owns the screen
> while it is up; taps outside its own band are not for it." **Refuted by** a device report on a
> shipped build: *"the OK button and the CLR button do not work on this confirm popup; both should
> act as OK."* **Actual mechanism:** the **key** path had always accepted both keys — only the touch
> arm returned early, so the two paths had silently diverged (§3). **Why the wrong reading was
> attractive:** the modal draws its own affordance, so accepting taps there feels complete, and
> every desk test answers the box with a keyboard or by tapping the band you just drew.
> **Consequence if carried forward:** the modal is unanswerable by the first input a real player
> reaches for, and the report will name the HUD buttons rather than the modal.

## 3. Order competing modals by specificity — in both paths

**(A)** When two host modals can be live at once, order them by **specificity**: the most specific
*answerable* window first, the general swallow-everything states last. And **keep the key path's
order and the touch path's order identical** — write the order once, as a function returning which
modal owns the event, and consult it from both.

**(B)** CONFIRMED, twice and independently. A save-in-progress lock that swallows all input was
tested **before** a specific refusal notice, so the notice could never be answered; the same
conclusion arrived from the save track as *"a more specific modal must be tested before the save
lock in the key path, or it becomes uncloseable"*. The fix reordered both paths, and both call sites
carry a comment saying the two orders must stay in step. The lock itself — why a save may need the UI
frozen, and its release conditions — is [save-integrity](../../save-integrity/SKILL.md)'s subject,
not this file's.

**(A)** Two input paths with divergent modal precedence is the worst shape of bug available here: it
reproduces on one path only, so it survives half of your testing. Prefer one ordered list over two
`if` cascades even when the second path has one extra case.

## 4. ★ A window that cannot be answered draws no confirm band

> **(A) Make the confirm band's height a parameter, and let zero mean "there is no band" — the
> painter early-returns when the height is zero.** One number then controls the geometry *and* the
> paint, so a wait box cannot drift back into drawing a button.

```
native_panel(rows, ok_h)   // ok_h > 0  -> answerable: band drawn, band hit-tested
native_wait (rows)         // ok_h == 0 -> painter returns before the band; nothing to press
```

> **REFUTED.** *Previously documented:* "a wait box is just a message box whose input we ignore."
> **Refuted by** a device report: a *"saving, please wait"* box that correctly swallowed **all**
> input was drawn by the same painter as answerable message boxes, so it carried an OK band that did
> nothing. **Actual mechanism:** input handling and painting were two independent decisions about
> the same window, and only one of them was changed. **Consequence if carried forward:** a correct
> busy state is reported as a bug — and reported against the *input* subsystem, which is where the
> next person will look.

**⚠ (A) The inverse is also a bug** — reasoned, not reported: a window that *can* be answered but draws no affordance is
unreachable for a touch player even though the key path works. Both directions are one rule — the
affordance and the handler are a single decision.

## 5. The window must not be answerable by the input that opened it

**(A)** Two independent halves. Both produce "the window flashed past" reports.

**(a) De-duplicate before dispatching.** The "is this the same key/target as last time?" test must be
the **first** test in the key-edge function, above the modal arm — see the two guards in
[input-and-hud.md §3](input-and-hud.md#3-the-key-edge-machine). Android delivers move events
continuously for a finger that is merely **resting**, so a finger that opened your window re-enters
the input path still holding the key that opened it. **(B)** Device-caught, with the report quoted in
the code: *"the quantity window flashed and jumped straight to maximum without me pressing OK
again"* — the modal test had been placed above the de-duplication test. A second input entry point
(a pad) must reuse the first one's bookkeeping rather than re-deriving it; see
[gamepad-and-pad-hud.md](gamepad-and-pad-hud.md).

**(b) Open with nothing selected.** If a picker opens with row 0 pre-focused, the confirm that
dismissed the *previous* window is one press away from committing. The contract: the first tap or
direction key sets **focus**; confirm with no focus is **no action**. **(B)** The reference port's
combine picker was changed to open focus-less after a report that using the item completed the
operation immediately. The guest's own windows have the same shape and it is worth knowing about: one
of them pre-focuses row 0 on open, so a tap that resolves to any row fires a purchase confirm
([touch-driven-guest-ui.md §12](touch-driven-guest-ui.md#12-variable-length-widgets-and-the-derived-maximum)).

**(A)** Whatever the window decides, latch the decision at DOWN and consult the latch at UP: an
affordance whose action mutates the state its own hit test reads must never be re-derived on the
second edge
([input-and-hud.md §7](input-and-hud.md#7-never-decide-twice-on-the-two-edges-of-one-touch)).

## 6. Sizing: percentage-with-clamp, and which term is binding

**(A) The procedure, and it is arithmetic rather than judgement:**

1. Express the window as `pct% of the window axis, clamped to [min, max]`.
2. **Tabulate the result for every resolution you actually ship to** — the devices you own, plus the
   extremes of your supported set.
3. Read off which term won in each row. **If the clamp wins everywhere, the percentage is dead code
   and you have shipped a fixed-size window** — the one thing percentage sizing was meant to prevent.
4. Only then change a number, and re-run the table.

**(B)** CONFIRMED by arithmetic over four real resolutions. A window specified as
`46% clamp(320,760)` x `78% clamp(200,520)` asked for **1076x842** on a 2340x1080 window and was
given **760x520** — about a third of the width. From that height, row height came out at **66**,
which the text fitter turned into **font scale 2**: 22-px glyphs on a 1080-px screen. The player's
report was verbatim *"the text is too small to read."*

> **REFUTED.** *Previously documented:* "the window is sized as a percentage of the screen, so it is
> resolution-independent." **Refuted by** computing both terms at each shipped resolution.
> **Actual mechanism:** the clamps were chosen on a small development window, where they were
> generous; on every phone the app actually ran on they were the binding term on both axes.
> **Consequence if carried forward:** you tune the percentage repeatedly and nothing changes, and
> the readability complaint gets misfiled as a font problem.

**(A) Two coordinate policies, and they are not interchangeable.** A host-invented window (settings,
diagnostics, a picker with no counterpart in the game) is a **fraction of the window**, as above. A
window that must line up with something the guest drew is measured in the guest's **logical** pixels
and scaled once (§8). Choose per window, and say which in a comment.

## 7. ★ Row height sets the font scale, so a symmetric shrink undoes a readability fix

**(A)** The coupling is: window height → row height → integer glyph scale → readability. Height is
therefore not a cosmetic axis, and **a request to "make the window smaller" must move only the axis
that was actually complained about.** Before shrinking anything, invert the chain: the minimum row
height for the scale you need, then the minimum window height for that row count.

**(B)** Method: read the text fitter's own budget expression and the font's cell height. In the
reference port the fitter was handed **44 % of the row height** as its budget against an **11-px**
syllabic cell, so **scale 5 needs row height ≥ 125**, i.e. a panel ≥ ~946 px tall. When the next
report was *"the enlarged window covers the touch controls"*, only the **width** clamp moved (to
`56% clamp(320,1180)`) and the height was deliberately held at `88% clamp(200,1000)` — shrinking
symmetrically would have taken the font straight back to the size of the previous complaint.

**(A)** Both clamps carry a comment naming the report that raised them and a prohibition on tidying
them back down. A bare pair of magic numbers looks like superstition and gets "cleaned up" by someone
reasonable — see
[evidence-standard.md §5](../../../shared/evidence-standard.md#5-a-measured-constant-carries-its-symptom-its-measurement-and-its-date).

## 8. Geometry in the guest's logical pixels, scaled once

**(A)** For anything that must coincide with the guest's own output — a replica window, a hit target
over a glyph the game drew, a highlight over a guest row — do the arithmetic in the guest's
**logical** pixels and convert **once**, at the boundary. Never store window pixels: they are wrong
on the next device and wrong after a rotation, and the miss falls through to whatever policy sits
underneath.

**(A) Prove resolution independence before you believe a measurement:** take it again at three or
more logical sizes, across two aspect families if you have them. Expect the numbers to be
**identical**, not close.

**(B)** A tap target over a glyph the game draws in its own corner measured **32x9 logical px with
2 px right and bottom margins** at all four logical resolutions probed, and the derived window rect
then matched on device. Those numbers are one title's glyph; **the four-resolution check is the
method.** Note what the scale factor buys: 9 logical px is about 40 device px — comfortably under a
thumb.

**(A) Anchor to the real edge; do not recompute the margin.** Extending the target to the window's
actual corner makes "the target contains the ink" true by construction. Computing the margin
symmetrically on both sides put one target's left edge a pixel *inside* the ink, and a selftest at a
real device resolution caught the off-by-one.

**(B)** The upscale is usually **fractional** — a 496x240 logical framebuffer into 1080 device rows
is x4.5 — which is why a replica's text needs a fractional glyph path rather than an integer one
([game-native-ui.md §6](game-native-ui.md#6-integer-scale-for-host-chrome-fractional-scale-for-a-replica)).

**(A)** Divide touch coordinates at exactly **one** entry point. That single property is also the
precondition for rendering into a smaller buffer later, which is the largest host-side saving
available on a weak device ([sustained-load.md](sustained-load.md)).

## 9. Focus indication that reads at arm's length

**(A)** Focus has to be readable on a phone at arm's length, among rows that are otherwise nearly
identical. **One cue is not enough**, and a cue that reads on a desktop mock-up at 100 % is not
necessarily a cue that reads on glass in a lit room. Use three independent ones: **face colour**, a
**ring**, and a **caret**.

**(B)** The reference port shipped "one step brighter face + a fixed 2-px ring" and the player asked
for it to be redone: among five similar plates, neither cue read. The three-layer replacement, each
layer with its own reason:

| layer | rule | (B) as shipped |
|---|---|---|
| face | raise the selected face until it reads as "on" **before** ring and caret are counted | a new selected-face colour in the row's own hue family |
| ring | thickness **derived from the row height**, clamped at both ends | `(row_h / 16).clamp(2, 5)` — 5 px on a large phone, 3 px on a small one |
| caret | a filled `▶` in the column the icon layout **already** reserves | measured: caret at x 6..18, ring 5, icon at 20 |

**(A)** A fixed ring thickness is the trap: it is chosen on one screen and becomes a hairline as the
window grows, and §6-§7 make windows grow. Derive it, clamp it, and ⚠ pick the divisor so the thin
end is still ~3 px on your smallest target — the reference divisor was fitted to two handsets, so it
is a starting point rather than a measurement of your set.

**(A) Centre the caret *inside* the ring, not over it** — `left = panel.x + ring`, then centred in
the width that remains. Drawn at the ring's position in the ring's own colour it reads as part of the
border rather than as an arrow.

**(A) A disabled row still gets the caret at full contrast.** "Which row am I on" is the most urgent
question exactly on the row that is going to refuse you (§1).

Two rules that live next door: the icon and caret columns are reserved on **every** row so nothing
jitters as the selection moves
([game-native-ui.md §16](game-native-ui.md#16-reserve-the-icon-and-caret-columns-on-every-row)), and
the body colour comes from the game's window **kind**
([game-native-ui.md §12](game-native-ui.md#12-window-kind-decides-the-body)).

**(A) Verify this class of change with your eyes, and label the artefact.** A full-resolution mock-up
from a throwaway renderer caught a button face indistinguishable from the panel body at a 1-px border
(fixed to 2 px); no assertion would have found it. The two honest options for a module that no
dependency-free test can include are in
[game-native-ui.md §17](game-native-ui.md#17-verification-when-the-module-cannot-be-included).

## 10. Symptom table

| Symptom | Cause | Section |
|---|---|---|
| A confirm popup ignores the HUD's OK and CANCEL; only tapping the popup works | the modal's touch arm returns before the HUD button list is hit-tested | §2 |
| A modal misbehaves on touch but is fine from a keyboard or a pad | the key path and the touch path have different modal orders | §2, §3 |
| A notice cannot be dismissed at all | a general swallow-everything state is tested before the specific answerable window | §3 |
| A "please wait" box has an OK button and pressing it does nothing | one painter for answerable and unanswerable windows | §4 |
| A window is answerable by key but not by touch | an answerable window drawn with no band | §4 |
| A window opens and immediately commits, at its maximum or on its first row | the press that opened it was re-delivered, or row 0 was pre-focused | §5 |
| The window is the same size on every phone and its text is tiny | the clamp, not the percentage, binds at every shipped resolution | §6 |
| Making a window smaller silently re-breaks a readability fix | height was shrunk too; row height sets the font scale | §7 |
| A host window lines up with the game's on your phone and misses on the next | geometry stored in window pixels instead of guest logical pixels | §8 |
| The leftmost column of a small tap target is dead | margin recomputed symmetrically instead of anchoring to the real edge | §8 |
| Replica text is one visible size step smaller than the game's | integer-only glyph scale against a fractional guest upscale | §8, [game-native-ui.md §6](game-native-ui.md#6-integer-scale-for-host-chrome-fractional-scale-for-a-replica) |
| Players cannot tell which row is selected | one cue, and a ring that became a hairline as the window grew | §9 |
| Rows shift sideways as the selection moves | icon/caret column not reserved on every row | [game-native-ui.md §16](game-native-ui.md#16-reserve-the-icon-and-caret-columns-on-every-row) |
| "Is this item type just not supported?" arrives as a design question | a refusal upstream of the row builder — an absent row, not a disabled one | §1 |

## 11. Checklist

- [ ] Every modal arm hit-tests the modal band **and** the HUD button list; cancel dismisses an
      informational box.
- [ ] Modal precedence is one ordered list, consulted by every input path, with a comment at each
      call site saying the orders must stay in step.
- [ ] The confirm band's height is a parameter; zero means absent and the painter early-returns.
- [ ] No window draws an affordance whose handler is absent, and none is answerable only by key.
- [ ] The de-duplication test is the first test in the key-edge function, above the modal arm.
- [ ] Pickers open with **nothing** focused; confirm with no focus is a no-op.
- [ ] Each window's size is tabulated at every shipped resolution and the binding term is recorded.
- [ ] The minimum row height for the required font scale is written down, and the height clamp cites
      it.
- [ ] Every clamp carries the report that set it and a prohibition on tidying it down.
- [ ] Replica geometry is in guest logical pixels, converted at one boundary, verified at three or
      more logical sizes.
- [ ] Touch coordinates are divided at exactly one entry point.
- [ ] Focus uses three cues; the ring thickness is derived from the row height and clamped.
- [ ] Disabled rows are present, labelled with their reason, and carry the caret.
- [ ] The window has been looked at, on a device or in a labelled full-resolution mock-up, at arm's
      length.
