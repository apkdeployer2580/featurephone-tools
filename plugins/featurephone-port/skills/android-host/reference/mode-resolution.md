# Mode resolution: deriving what the game is showing, and the hysteresis it needs

Before the host can decide which controls exist, it has to answer *what is the game showing right
now?* That answer is one small value, computed by the worker from guest reads and consumed by the
layout. This file specifies how to derive it, how to keep it stable across the guest's own
transient states, and the one place where reusing it is a correctness bug rather than a cosmetic
one. Input routing and the hit-test chain are [input-and-hud.md](input-and-hud.md); the physical
input-source axis is [gamepad-and-pad-hud.md](gamepad-and-pad-hud.md); the publish machinery
itself is [thread-model.md](thread-model.md).

Tags: **(A)** generic · **(B)** worked example from the reference port, re-derive it ·
**(C)** do not copy. `⚠` marks anything inferred.

## Contents

- [1. The mode is published by the worker, consumed by the layout](#1-the-mode-is-published-by-the-worker-consumed-by-the-layout)
- [2. One window id can be two features](#2-one-window-id-can-be-two-features)
- [3. Finding the splitting flag: a diff-only whole-record watcher](#3-finding-the-splitting-flag-a-diff-only-whole-record-watcher)
- [4. A gate must be a flag, not an index](#4-a-gate-must-be-a-flag-not-an-index)
- [5. Consecutive polls, not a deadline](#5-consecutive-polls-not-a-deadline)
- [6. REFUTED: "if the world looks free, cancel the hold early"](#6-refuted-if-the-world-looks-free-cancel-the-hold-early)
- [7. The one legitimate cancel: a DIFFERENT window opened](#7-the-one-legitimate-cancel-a-different-window-opened)
- [8. Two flags, opposite hysteresis polarity](#8-two-flags-opposite-hysteresis-polarity)
- [9. ★ The order that is right for controls is wrong for "are we in a scene"](#9--the-order-that-is-right-for-controls-is-wrong-for-are-we-in-a-scene)
- [10. Keep the held flag with a dead-code marker](#10-keep-the-held-flag-with-a-dead-code-marker)
- [11. Do not add a mode variant for a fact one draw block reads](#11-do-not-add-a-mode-variant-for-a-fact-one-draw-block-reads)
- [12. The worker re-checking the same predicate is not a second opinion](#12-the-worker-re-checking-the-same-predicate-is-not-a-second-opinion)
- [13. The input-source axis lives elsewhere](#13-the-input-source-axis-lives-elsewhere)
- [14. Symptom table](#14-symptom-table)
- [15. Checklist](#15-checklist)

---

## 1. The mode is published by the worker, consumed by the layout

**(A)** The mode is a small enum, computed on the worker from guest-memory predicates and shipped
to the UI inside the periodic HUD publish. The UI never reads guest memory to obtain it — see
[thread-model.md](thread-model.md#3-the-two-enums-and-the-design-rule-behind-them).

Three properties, each load-bearing:

- **It is derived, never assigned.** No host action sets the mode. Every change is a consequence
  of a guest read. A host that can *set* a mode will eventually disagree with the guest, and the
  disagreement is silent — the controls are wrong and nothing logs.
- **It is allowed to be stale.** It arrives on a publish cadence, so it is always a few frames
  old — acceptable because a mode chooses *pixels and rectangles*, and never acceptable for
  anything choosing **which guest function runs** (same section of `thread-model.md`).
- **One enum, matched in several places.** The mode is matched by the placement table, the quick
  bar, the HUD block and the layout editor. That match surface is the whole cost of adding a
  variant — see §11.

**(B)** The reference port resolves three variants — in-world / game-window / scene — from **two**
booleans, and publishes the mode block **every fifth frame**. Its scene variant is deliberately
*not* a third layout: it borrows the game-window variant's seats, so a control the player dragged
somewhere stays where they put it.

**(A)** The method that produces the booleans matters more than the booleans. Each is a predicate
over god-object bytes, and a **conjunction is more robust than a single byte**: the reference's
in-world predicate is a three-tuple match — *no window open* **and** *app state is the field*
**and** *the quick-select overlay is closed* — so a stale value in any one of the three cannot
alone claim the field. Its scene predicate is *the window-id byte equals the script window's id*,
where the id labels came from walking every screen the game opens with that byte logged. Neither
required disassembly.

## 2. One window id can be two features

**(A) The guest's window id identifies a *screen*, not a *feature*.** Story narration and
interactive NPC dialogue are routinely the same window: same id, same painter, same key handler,
completely different input policy. Do not build a UI policy on the id alone.

The failure is not cosmetic. **(B)** The reference removed a control set "for cutscenes" and
thereby removed the direction arrows from quest dialogue as well, so the player could not answer a
choice — reported as a bug, and correctly.

**★ (A) Do not trust a comment that already hints at both.** The port's own accessor was
documented as "story scene *or* NPC dialogue" before the regression. The hint was there and was
read as a description instead of as a warning. **When a comment names two features for one id,
that is a work item, not a note.**

**⚠ Static RE is often blocked here.** In the reference the window object arrives as a pointer
argument with no literal to cross-reference, so there was nothing to search for. The measurement in
§3 is minutes of work and does not care — background:
[live-data-structures.md](../../guest-re/reference/live-data-structures.md).

## 3. Finding the splitting flag: a diff-only whole-record watcher

**(A) The procedure**, in six steps. It needs no disassembly and no new tooling beyond a reader
you already have.

1. **Watch the whole record**, not a neighbourhood. Snapshot the entire god object.
2. **Sample a couple of times a second and report only bytes that CHANGED** since the previous
   sample. Diff-only is what makes the output readable at all.
3. **Suppress counters.** A byte that changes on N consecutive samples is a counter, not a flag;
   mute it from then on and keep only the count. A real flag transitions once and never reaches
   the limit.
4. **Cap the number of changes reported per sample.** One event — a save load, a screen rebuild —
   rewrites thousands of bytes, and a single log line carrying them all is worse than useless.
5. **Walk three transitions:** feature A opens · the distinguishing element appears · it closes.
6. **Read the neighbours that moved in step.** A lone byte is a candidate; a byte that moves
   together with a plausibly-shaped structure is a finding.

**(B)** The reference port's watcher covered the **whole ~43 KB character record**, sampled
**twice a second**, muted any byte that changed on **3 consecutive samples**, and printed at most
**48 changes per sample**. It was already in the crate for an unrelated hunt, and it produced the
answer in about **30 seconds of runtime**. Those four numbers are the tuning; the four ideas are
the method.

The three transitions, as the diff reported them **(B)**:

| transition | what changed |
|---|---|
| dialogue opens, text only | window-id byte `3 -> 4` **and the candidate flag `1 -> 0`** |
| the choice box appears | candidate flag `0 -> 1`, and a 4-row x 4-byte pointer-shaped table filled in the same sample |
| the choice box closes | window id `4 -> 3`, and that row table zeroed |

**★ (A) The middle column of row one is the row that licenses the read.** Entering the window
*clears* the flag. That is what makes it safe to read while the window is open: it cannot return a
stale `1` left by a previous dialogue. Look for that fact explicitly. Without it you have a
candidate, not a gate — and no amount of further sampling will upgrade it.

> **REFUTED.** *Previously believed:* the flag can be found by watching a narrow window around
> where a sibling build of the same game keeps its equivalent byte.
> *Refuted by:* the first device trial of this watcher, aimed at a ~0x80-byte neighbourhood. Exactly
> one byte in it moved, to exactly the expected value, at exactly the right moment — and writing it
> from the host did nothing. It was not the flag.
> *Why the wrong reading was attractive:* a narrow window produces a short, clean report, which
> reads as precision. *Actual mechanism:* **a window chosen around the answer you expect can only
> confirm or mislead; it cannot find.** Reading tens of kilobytes twice a second is a `memcpy`, so
> there is no reason to narrow it.

**⚠ Record the risk class with the flag.** A flag found this way is measured on **one** build. In
the reference it is read for a *drawing* decision, so a wrong value on a sibling build costs a
stray control, not corruption — say that, because it is the difference between "ship it" and "gate
it per build" ([multi-build.md](../../guest-re/reference/multi-build.md)).

## 4. A gate must be a flag, not an index

**(A)** The trace that finds the flag almost always surfaces a neighbouring **cursor or index**
that moves far more visibly — and it is the wrong byte.

An index legitimately reaches its sentinel. A choice list's cursor is `0` when the player selects
the first row, so a gate of the form *"cursor is non-zero, therefore a choice is open"* makes the
controls **vanish at the moment the player uses them**. Presence is a flag; position is an index;
never derive one from the other. **If the only candidate you have is an index, you have not found
the gate yet.**

**(B)** Both bytes were visible in the same device trace in the reference port, several hundred
bytes apart in the same record, with the cursor's response to up and down recorded alongside.
**(A)** Keep that recording: a trace taken to identify a gate is simultaneously an end-to-end test
that your own synthesised direction keys reach the guest.

## 5. Consecutive polls, not a deadline

**(A) Guest UI states are not stable across the guest's own internal transitions.** Turning a page
of dialogue drops the window id for a poll or two and puts it straight back. A poll that lands in
that gap resolves a *different* mode, and the entire control set swaps on screen for a few frames.
The player sees a flash of the wrong buttons, repeatedly, at every page turn.

**(A) Discriminate by CONSECUTIVE POLLS, never by elapsed time.** A blip and a real exit both look
like an *absence* at the sampling instant; what separates them is **length in samples**. A deadline
long enough to cover a slow blip is also long enough to hold the wrong controls after a real exit,
and a short one lets the blip through. There is no correct millisecond value.

**(A) Write it as a pure function of a caller-held counter.** The bug it exists for is a
*sequence*, not a value, and a sequence is only checkable if the rule is a function:

```rust
/// `gone` counts consecutive polls that saw the state absent; owned by the caller.
fn hold(gone: &mut u32, present: bool, cancel: bool, exit_polls: u32) -> bool {
    if present      { *gone = 0;          return true;  }   // seen -> reset
    if cancel       { *gone = exit_polls; return false; }   // §7, the one legitimate cancel
    *gone = gone.saturating_add(1);
    *gone < exit_polls
}
```

A selftest can then replay the exact sequence that broke — present, absent, present, absent,
absent, absent — which is impossible to express against a clock.

**(B)** The reference uses **3** polls for the scene hold and **2** for the choice hold, and
recorded the cost of erring in the safe direction as roughly **100-150 ms** and **50-70 ms** of
extra hold at its publish cadence. The counts are per-title tuning; the shape is not. Note also
that the scene hold's cost is usually *zero*, for the reason in §9: leaving a scene lands
in-world, and in-world outranks scene in the mode resolution, so the correct control set appears
immediately anyway.

## 6. REFUTED: "if the world looks free, cancel the hold early"

> **REFUTED.** *Previously shipped:* a wall-clock hold with a fast-cancel clause — *"if the
> in-world predicate is true, throw the hold away immediately"* — added for responsiveness, so a
> real scene exit would not cost even one extra poll.
> *Refuted by:* a player report of the menu control set flashing between dialogue pages, on a
> shipped build, then traced in the worker's own poll.
> *Actual mechanism:* the in-world predicate requires **no window open** — which is exactly what
> the page-turn blip looks like. So the early cancel fired **precisely when the hold was needed**,
> and the following poll, with the window already reopening, was neither in-world nor scene: the
> fall-through case, i.e. the game-window control set. That is the set the player saw flash.
> *Consequence if carried forward:* a hold with this clause is **worse than no hysteresis at all** —
> it has all the code and none of the effect, and the flash it fails to suppress is now attributed
> to the hold rather than to the cancel.

**(A) The general shape:** a hysteresis cancel predicated on *"the underlying state looks normal
again"* is unsound whenever **the blip is itself a normal-looking state**. Before adding any early
cancel, evaluate its predicate *on the blip*. If it is true there, the cancel is the bug.

## 7. The one legitimate cancel: a DIFFERENT window opened

**(A)** Hysteresis that cannot be cancelled at all is as wrong as hysteresis cancelled by the
wrong predicate. The correct cancel is the *opposite* fact from the one in §6: not *"nothing is
up"* — that is the blip the hold exists to absorb — but **"a different, real thing is up"**. When
the window id becomes a genuinely different window, the scene is over as a matter of fact: the
game has put another screen on top.

> **REFUTED.** *Previously shipped:* a consecutive-poll hold with **no** cancel, on the reasoning
> that §6 had proved cancels dangerous.
> *Refuted by:* a player report on that build — a tap anywhere inside a shop or upgrade window
> behaved like a confirm.
> *Actual mechanism:* the scene mode carried a *tap-anywhere-confirms* fallback policy (the right
> policy for advancing dialogue). With no cancel, **every window opened from a dialogue inherited
> the scene's input policy for the length of the hold** — about half a second in which a tap
> anywhere could confirm a purchase.
> *Consequence if carried forward:* a destructive action reachable by a stray tap, in a window the
> host believes it is not governing.

Two implementation details, both **(A)**:

- **Saturate the counter, do not decrement it.** Set it straight to the limit so the hold ends on
  that same poll. A cancel that merely nudges the counter still grants a partial head start.
- **The predicate must exclude two values:** the scene window's own id, and the "no window"
  value. Everything else is a real other window.

The distinguishing fact costs nothing — it comes out of the same byte you already read for the
mode. There is no excuse for a hold whose only exit is its counter.

## 8. Two flags, opposite hysteresis polarity

**(A) Do not copy one hysteresis helper for both flags.** The polarity follows from **which error
is expensive**, and for two flags in the same subsystem it can point in opposite directions.

| flag | default | counts consecutive polls that saw it... | the expensive error |
|---|---|---|---|
| *a scene is still running* | keep holding | **GONE** | dropping the hold mid-scene — the control set flashes at every page turn |
| *a choice list is up* | do not show | **PRESENT** | showing a choice UI with nothing behind it — arrows blink into existence spuriously |

**(A)** The reason the choice flag needs the *other* polarity: it is written around the window's
own open and close, so a single sample can catch it set with no choice box behind it (this is the
same transition §3 identified as *entering the window clears it*). Requiring agreement is safe
because **the true state is long-lived** — a real choice holds the flag until the player answers,
so N agreeing samples cannot hide one.

That last argument is the test to apply before choosing the present-counting polarity: *is the
true state long-lived relative to my poll rate?* If yes, confirmation is free. If no, you will
suppress the real state and the flag needs the other polarity, or a different flag.

## 9. ★ The order that is right for controls is wrong for "are we in a scene"

This is the single most transferable trap in this file.

**(A)** The resolution `(in_world, scene_open)` with **in-world winning** is *correct* for the
control set. A real scene exit must show the field HUD immediately, without waiting out the hold —
that is the property that makes the hold's cost approximately zero (§5).

```rust
let mode = match (field, scene) {
    (true, _) => Mode::Field,      // in-world OUTRANKS scene, deliberately
    (_, true) => Mode::Cutscene,
    _         => Mode::Menu,
};
```

**(A) And therefore the resolved mode reads "in-world" during the page-turn blip.** It has to.
Which means:

> **Any gate meaning "not during a scene, blips included" must read the worker's HELD flag, never
> the resolved mode. The same value must not serve both questions.**

> **REFUTED.** *Previously shipped:* a walk gate of the form *"only move the character if the
> resolved mode is in-world"* — the obvious reading, and the mode is the value every other consumer
> uses.
> *Refuted by:* a device session. A tap intended to advance dialogue fell through to the walk
> handler and moved the character mid-scene.
> *Why the wrong reading was attractive:* the enum is called **mode**, and "are we in a scene" sounds
> like a question about modes. *Actual mechanism:* two different questions collapsed onto one value —
> *which controls should exist* (where in-world must win) and *what is happening* (where it must
> not).
> *Consequence if carried forward:* the disagreement appears for one poll out of hundreds, i.e. in
> play and not in testing, and it is unattributable from the player's side.

**(A) Generalise it as a review question.** For every consumer of the mode, ask: does this want
*which controls exist*, or *what is happening*? Publish both values, give them different names,
and put the reason on the field itself — see §10.

## 10. Keep the held flag with a dead-code marker

**(A)** When the last consumer of the held flag is deleted along with its feature, **keep the
flag.** Mark it dead-code explicitly and write the reason beside it, naming the failure it
prevents.

Two things this is *not*:

- Not a fake reader. A consumer that pretends to use the value is a lie the compiler cannot catch,
  and the next reader deletes it anyway — with the comment.
- Not a tidy-up candidate. Dead-code warnings are normally resolved by deletion; here **the field
  is the institutional memory**, and deleting it re-arms a trap that cost a device session to
  find.

**(B)** The reference kept exactly this after §9's gate was removed with its feature; the comment
states that nothing reads the flag today, that the next such gate must not re-learn the lesson at a
player's expense, and which work session paid for it.

**(A) While deleting a feature, publish a "what stays and why" list** beside the list of what went.
In the reference it named a state predicate four other callers use, a data-structure reader shared
with an offline tool, a window-pixel-to-logical-pixel conversion the next feature inherited, and a
calibration flag — **a measurement tool is not a feature**. Deleting code need not delete the
reverse engineering.

## 11. Do not add a mode variant for a fact one draw block reads

**(A)** A new enum variant is the natural modelling choice and it is the expensive one, because
**the mode's match surface is its cost**: the placement table, the quick bar, the HUD block and
the layout editor all match on it, and every match has to be audited. A boolean published
alongside the mode costs one extra parameter on the layout function.

```rust
// mode chooses the control SET; `choice` adds one block to it
pub fn layout(w: u32, h: u32, mode: Mode, keypad_open: bool, choice: bool) -> Layout;
```

**★ (A) But a flag that changes which CONTROLS exist needs a RELAYOUT, not a redraw.** This is the
subtle half. A redraw paints the old layout: the control is drawn and unhittable, or hittable and
invisible, until something unrelated forces a rebuild. Fold the flag into the same branch that
already handles a mode change:

```rust
if self.mode != mode || choice_changed {
    self.mode = mode;
    self.relayout();          // NOT request_redraw(): the button set changed
    // ...and the transition housekeeping the mode change implies
}
```

Hold the distinction explicitly: **pixels -> redraw; control set -> relayout.** It is invisible
until a player presses something that is not there.

**(A)** That branch is also the right place for the rest of the transition's housekeeping — close
transient host affordances (a pull-out keypad is opened for one key and must not survive a mode
change), and release any held key whose control the new layout does not contain
([input-and-hud.md §6](input-and-hud.md#6-held-key-release-discipline)).

★ Related but distinct: two controls that are **alternatives** for one job under a user setting
are a *filter over one id set*, not a mode variant and not two sets —
[layout-editor.md](layout-editor.md) owns that rule.

## 12. The worker re-checking the same predicate is not a second opinion

**(A)** *"The worker validates it anyway"* is the standard reassurance in a two-thread host, and
it is **worthless whenever both sides evaluate the same predicate.** They are true together, in
the same blip.

> **REFUTED.** *Previously believed:* §9's walk bug could not happen, because the worker
> re-validates the in-world state before acting on the tap.
> *Refuted by:* the same device session. The re-check used **the same in-world predicate** the UI
> gate did, so it was true in the same blip and rescued nothing.
> *Actual mechanism:* re-evaluating a fact is not corroborating it. Two evaluations of one
> predicate are one derivation — the same rule as
> `${CLAUDE_PLUGIN_ROOT}/shared/evidence-standard.md` §3, applied to runtime state.
> *Consequence if carried forward:* the re-check is load-bearing in review and inert in production.

**(A) A defence must be a different FACT.** Three that qualify:

- **the held flag** — a different fact, because it carries history the instantaneous predicate does not;
- **a guest byte the action itself clears**, so a repeat cannot pass;
- **the game's own refusal gate** — read the byte the guest reads
  ([thread-model.md §9](thread-model.md#9-host-initiated-guest-calls), rule 4).

Ask of every "we re-check on the worker": *which fact does each side use?* If the two answers have
the same name, delete the re-check or replace it with one of the above. Keeping it is worse than
having none, because it stops the next reviewer from looking.

## 13. The input-source axis lives elsewhere

There is a second mode axis, orthogonal to this one: **which input device is driving**. It is not
a guest read — it is a user setting plus a note taken on the input path — and it *removes* controls
rather than restyling them ([gamepad-and-pad-hud.md](gamepad-and-pad-hud.md)). The layout-family
axis `(orientation, immersive?)` is a third ([input-and-hud.md §9](input-and-hud.md#9-the-three-layout-families)).
Keep all three separate: guest state, input source, window geometry.

## 14. Symptom table

| Symptom | Cause | Fix |
|---|---|---|
| A control set flashes for a frame or two between pages of dialogue | the transient was detected with a deadline, or with a hold carrying an "if the world looks free, cancel early" clause — and the in-world predicate is TRUE during the blip | hold until N **consecutive** polls disagree, as a pure function of a counter (§5, §6) |
| A "tap anywhere confirms" policy fires inside a shop or upgrade window opened from a dialogue, and can confirm a purchase | the scene hold has no cancel for "another window opened", so every window inherits the scene's input policy for the hold's length | add that one cancel and only that one; saturate the counter so the hold ends on the same poll (§7) |
| A gate meaning "not during a cutscene" fires during a cutscene, for one poll, and the worker's re-validation does not catch it | the gate read the **resolved mode**, whose order makes in-world outrank scene — right for the control set, wrong for "what is happening"; the worker used the same predicate | read the worker's **held** flag; a defence must be a different fact (§9, §12) |
| A control set removed for cutscenes is also missing from interactive dialogue, so a choice cannot be answered | the policy was built on the window id, and one id serves both features | find the splitting flag with a diff-only whole-record watcher across three transitions (§2, §3) |
| The controls vanish the instant the player selects the first row of a list | a cursor **index** was used as a presence gate, and it legitimately reaches its sentinel | gate on a flag, never an index (§4) |
| A newly added boolean changes which controls exist, but the control is unhittable until something else forces a rebuild | the flag triggered a redraw instead of a relayout | fold it into the mode-change branch and call relayout (§11) |
| Choice arrows blink into existence when no choice is on screen | one hysteresis helper was copied for both flags; the choice flag needs the opposite polarity | count consecutive polls that saw it **present** for that flag (§8) |

## 15. Checklist

- [ ] The mode is computed on the worker from guest predicates and published; the UI never derives it, and each predicate is a conjunction where one is available.
- [ ] No feature policy is built on a window id alone; any id documented as two features has been split.
- [ ] The splitting flag was found with a **whole-record** diff watcher, three transitions, counter suppression and a report cap.
- [ ] The flag's "cleared on window entry" transition (or an equivalent) is recorded — it is what licenses reading it.
- [ ] Every presence gate is a flag; no index is used as presence.
- [ ] Transient states are held by **consecutive polls**, in a pure function of a caller-held counter, with a selftest replaying the breaking sequence.
- [ ] No early-cancel clause whose predicate is true during the blip.
- [ ] Exactly one cancel: a different, real window opened — and it saturates the counter.
- [ ] Hysteresis polarity is chosen per flag, from which error is expensive.
- [ ] Every consumer of the mode was asked whether it wants "which controls" or "what is happening"; the second class reads the held flag.
- [ ] The held flag survives the deletion of its last consumer, with a dead-code marker and the reason.
- [ ] No new mode variant for a fact one draw block reads; the flag relayouts, not redraws.
- [ ] No worker re-check that evaluates the same predicate as the UI gate it is supposed to defend.
