# Five recipes that find the routines you actually want

Each recipe is a **procedure (A)** illustrated with constants from one real port **(B)**. Re-derive every number; the point is the sequence of questions, not the answers.

Every recipe assumes the address model is pinned ([address-model.md](address-model.md)) and the string index exists ([static-toolkit.md](static-toolkit.md) §9-10).

## Contents

* [Recipe A — find the save serialiser](#recipe-a--find-the-save-serialiser)
* [Recipe B — find any "you can't do X" gate](#recipe-b--find-any-you-cant-do-x-gate)
* [Recipe C — turn a formula into a multiplier](#recipe-c--turn-a-formula-into-a-multiplier)
* [Recipe D — prove a scalar field offset is right](#recipe-d--prove-a-scalar-field-offset-is-right)
* [Recipe E — recover the original art for a UI element](#recipe-e--recover-the-original-art-for-a-ui-element)
* [Choosing between them](#choosing-between-them)

---

## Recipe A — find the save serialiser

**Goal:** drive the game's *own* save path from the host (autosave, a SAVE button, a backup export) instead of writing save files yourself.

**Why the game's own primitives and not your own writer:** save records are usually checksummed, often encrypted, and always exactly as long as the build says they are. Reimplementing the cipher risks one-byte-wrong corrupted saves, which players discover the hard way. Encrypted formats are also where extracted keys would live, and those are out of scope for this plugin entirely. Drive the guest.

### Steps

**1. Start at the persistence layer, not at the code.**
Boot with `--db work/probe/db` and read the probe's own log:

```
[probe] DB open name=<RecordName> app_id=<AID> -> work/probe/db/<AID>/db/<RecordName>/0
```

You now have the **record names** for free, and the files on disk give you the **record sizes**. No disassembly yet.

Reference measurement **(B)**: two record families, `…GameSave_0/1` at **1408 B** and `…SlotSave_0/1` at **32 B**.

**2. Find those name strings in the image**, then the static slot that points at each one.

```bash
strings_dump.py -t target.toml | grep GameSave
xref.py -t target.toml slot <sl_off>          # or dump_table.py around the hit
```

Reference **(B)**: `*(sl+0x138)` and `*(sl+0x13c)` hold the two record-name strings. The `./` prefix on the names is a hint that a filesystem-shaped API is underneath.

**3. Find who loads those slots.** That is the record writer.

Reference **(B)**: `writeDbRecord(name, ptr, len)` at `0x1951b0`. It appends `"_%d"` from a slot index held in the god object, then calls a helper that does `alloc(len + 0x10)` and encrypts.

**4. Ask who owns that slot index. This is the most damaging question in the whole area, and the recipe used to stop one step short of it.**

The writer takes no slot argument — it reads a word out of the god object. So enumerate **every writer of that word** (`xref.py offset <imm>`, both idioms) and read each one.

Reference finding **(B)**: every writer was in **menu and shell screens** — the title screen, the two slot-list screens' up/down handlers, one modal. None of them was the load path or the save path, and **the game never unloads the character**. The word is therefore a **slot-list cursor**, not "the slot this character came from": walking a slot list re-aims every subsequent save at another character's record, and the game's own online/container screens then save constantly. `HIGH / CONFIRMED`.

**The test (A):** if every writer of the slot word is UI code and none of them is the load or the save path, **you have this bug**. The fix belongs in the **host's storage layer**, not in the guest — do not "correct" the guest's cursor, because the slot screens read it and would break. Redirect or refuse at the point where a record reaches disk.

That subject is large enough to be its own skill: [save-integrity](../../save-integrity/SKILL.md), and for this question specifically [slot-identity.md](../../save-integrity/reference/slot-identity.md).

**5. Cross-check the sizes arithmetically. This is the strongest single piece of evidence in the recipe.**

`alloc(len + 0x10)` predicts `on_disk = payload + 16`.

```
1408 - 1392 = 16   ✓
  32 -   16 = 16   ✓
```

**A size relation that closes is worth more than any amount of disassembly reading.** It confirms the writer, the header size and both payload sizes at once, and it is impossible to fool yourself with.

**6. Confirm the argument order by consensus of independent call sites.**
Two unrelated callers producing the same `(name, ptr, len)` shape is a consensus. Better still if one of them is a call whose length you can predict — a 16-byte summary written to the 32-byte record checks out against step 4.

**7. The buffer's producer is the call immediately before the writer.**

Reference **(B)**: `serializeSave(u32* outLen) -> buf` at `0x1960bc`. Read the out-param convention off the *caller's* prologue:

```
sub sp, #8
movs r3, #0
str  r3, [sp]
mov  r0, sp        ; r0 = &outLen
```

That tells you the host must supply a writable 4-byte address — see [patching.md](patching.md) §5 for where to get one without allocating.

**8. Find the precondition from the refusal message** (Recipe B). Games refuse to save in towns, in combat, during cutscenes.

Reference **(B)**: `canSave()` at `0x192bd0` turns out to be nothing but a load of `(s8)*(GOD+0xa280)`. **Read the byte directly rather than calling the function** — one guest read instead of a queued call, and it cannot fail.

**9. Expect encryption, and route around it.** If the records are high-entropy, you cannot parse or synthesise them offline. Everything the host wants from a save (a backup blob, a summary, a slot listing) must come from calling the game's own read path, not from parsing the file.

Two consequences that are easy to miss, and both are good news **(A)**:

* **Ciphertext is an *advantage* for a backup vault.** If you cannot read a record you also do not need to: a **byte copy** of the record taken immediately before it is overwritten needs no key, no guest call and no game state, so it works at the title screen and during boot, which is exactly where the guest-call route does not. See [save-vault.md](../../save-integrity/reference/save-vault.md).
* **The record's LENGTH is then the only readable signal about what it holds.** Name each vault copy `<timestamp>-<length>.bin`, and a jump in length tells you the moment a slot started holding a *different* character. It is a weak signal; on an opaque record it is the only one you have.

### Refutations to carry

* A note claiming a function "calculates the save size" may actually be the **serialiser with an out-param**. Read the caller's prologue before believing a one-word summary of a function's job.
* A record length measured once is **not** the record length. See [patching.md](patching.md) §6.

---

## Recipe B — find any "you can't do X" gate

**Goal:** locate the branch that refuses an action, so you can relax it, report it in the HUD, or gate a host feature on the same condition the game uses.

This is the most mechanical recipe here. Every step narrows by at least 10x.

### Steps

**1. Decode the game's runtime text table** ([static-toolkit.md](static-toolkit.md) §10) and commit an `ids.txt`.

**2. Get the id of the refusal text.** Search `ids.txt` for the message you saw on screen. Two reference examples **(B)**: id `0x87` for an equip refusal, id `0xa7` for a save-location refusal.

**3. Identify the id-consuming function** with `msgid_scan.py` — rank callees by distinct-id count.

Reference **(B)**: `getText(obj, id)` at `0x199764`, and a separate `showMessage(code)` at `0x13d5d0`.

**4. Map the message *code* to the text *id*.** These are usually different numbering spaces, joined by a jump table inside the dispatcher.

Reference **(B)**: `showMessage` code `0x67` dispatches to arm `0x13da18`, which loads id `0x87`. The jump-table base sat at a negative `sl` offset — do not assume tables live at positive offsets from the static base.

**5. Find the call sites passing that code:**

```bash
xref.py -t target.toml calls 0x13d5d0 --with-imm8-arg
```

then filter on the recovered `movs r0, #0x67`. Reference **(B)**: **exactly one** site in the entire image. That is the normal outcome — a refusal message is emitted from one place.

**6. Disassemble backwards from that call to the branch that guards it.**

Reference **(B)**:

```
13d0a4  ldrb r3, [r3, #2]     ; required level, from the item record
13d0aa  adds r1, r3, #0       ; r1 = required
13d0b6  cmp  r2, r3
13d0b8  bge  pass             ; player level >= required -> allowed
```

**7. Choose *what* to patch by the semantics you want** — this is the step people get wrong.

| you patch | you get |
|---|---|
| the branch (`bge` → `b`) | "ignore the check entirely" |
| the **move of the threshold** | "reduce the requirement by N", everything downstream unchanged |
| the load of the player's level | "pretend the player is stronger", affects every later use of that register |

Reference choice **(B)**: rewrite `adds r1, r3, #0` (`0x1C19`) to `subs r1, r3, #5` (`0x1F59`). The comparison, the message, the UI colouring and the tooltip all keep working; the condition simply becomes `playerLevel >= requiredLevel - 5`.

**8. Prove register liveness before rewriting an instruction.** For the example above: `r3` is overwritten by the very next instruction, `r1` is dead after the compare, and `subs` clobbers flags but the following `cmp` re-sets them. Write that argument down next to the patch — it is the thing a future reader cannot reconstruct.

**9. Bound the patch by its encoding.** Thumb-1 `subs rD, rN, #imm3` has a **3-bit** immediate, so the maximum reduction is 7. The encoding, not the design, sets the feature's limit. Say so in the code: name the constant `RELIEF_LEVELS_MAX = 7` and comment *why*.

**10. Check uniqueness, then state the limits honestly.** Because that level check exists only at equip time, a timed version of the effect cannot un-equip anything when it expires. That is a real behaviour, and it belongs in the user-facing description rather than in a bug report three weeks later.

### The other use of this recipe: transcribing a gate you are *not* patching **(A)**

Most of the time you are not relaxing the gate at all — you are **reimplementing** it, because a host list has to grey out the rows the game refuses, or a replacement server has to reject what the game rejects. Then step 7's table does not apply and one rule replaces it:

> **Transcribe the comparison exactly, including the half that is absent.** Game validators are routinely one-sided (`cmp #N / bgt`, with no test for zero), and "tidying" that into a range silently removes a whole category — usually the zeroth, which is a real and common item.

In the reference port that cost two releases, and the bug was **kept green by its own selftest**, whose negative list had been copied from the wrong implementation rather than derived from the gate. The full treatment — the refutation, the three-implementations rule, and where the rejection has to happen so the player can see it — is [live-data-structures.md](live-data-structures.md) §14.

---

## Recipe C — turn a formula into a multiplier

**Goal:** a "2x experience" or "double drop rate" feature implemented as a one-halfword guest patch rather than host-side polling.

### Steps

**1. Find the accumulator field.** Either read it off the game's own HUD (the number is on screen; find what changes when it changes) or use differential RAM ([probe-cli.md](probe-cli.md) §8.4).

Reference **(B)**: experience is a `u32` at `GOD+0xe4`.

**2. Find its writers** — `xref.py offset 0xe4`, then disassemble each hit. You are looking for the reward function, not the display code.

Reference **(B)**: `addExpFromKill` at `0x14650c`, one caller at `0x120682`, immediately followed by `checkLevelUp` at `0x146ca8`.

**3. Read the arithmetic and look for a constant you can change *size-neutrally*.**

```
gain = (enemyLv*13 + 130) * enemy[0xb4] * (clamp(lvDiff, -9, 9)*2 + 10) / 100
```

**A divisor beats a multiplier**: `movs r1, #100` is one 16-bit instruction with an 8-bit immediate, so `#50` and `#25` re-encode in place with no size change and no downstream address movement. Inserting a multiply would not.

Reference site **(B)**: `0x14654e`, `movs r1, #100` = `0x2164` → `0x2132` (#50, x2) or `0x2119` (#25, x4).

**4. Identify compiler runtime helpers by their epilogues** so you can read the arithmetic at all — see [static-toolkit.md](static-toolkit.md) §11. If the divide is a reciprocal multiply instead of a call, this whole approach changes: you would have to rewrite a 32-bit magic constant and a shift, which is no longer size-neutral in a useful way.

**5. Refuse inexact multipliers in code, not in prose.**

```rust
fn exp_divisor_halfword(mult: u32) -> Option<u16> {
    if mult == 0 || 100 % mult != 0 { return None; }   // x3 is impossible, not 3.03
    Some(encode_movs_imm8(R1, (100 / mult) as u8))
}
```

x2 and x4 are exact; x3 would silently be x3.0303…. Making it `None` means the feature list cannot quietly contain a lie.

**6. Document what the patch does *not* cover.** In the reference formula a second, flat `+= enemy[0xb4]` happens **after** the divide, so "x2" is really `2*gain + flat`. And rewards delivered over the network go through a different code path entirely and must be handled server-side. Both facts belong in the feature's documentation.

**7. Prefer the patch over host-side polling, and say why.** Polling the accumulator and adding to it cannot distinguish a hunting gain from a save-load or a network gain, needs a separate level-up call, and races the guest's own writes. One halfword in the middle of the formula has none of those problems.

---

## Recipe D — prove a scalar field offset is right

**The strongest evidence pattern in this methodology. Make it your standard for "confirmed".**

**Two independent derivations landing on the same constant is proof. One is a hypothesis.**

### Worked example **(B)**

Two adjacent `u16` fields, candidates from a RAM diff: `GOD+0x18a` (remaining stat points) and `GOD+0x18c` (remaining skill points).

**Derivation 1 — read the clamp out of the setter.**
Disassemble the functions that write those offsets. They clamp at **276** and **44** respectively.

**Derivation 2 — derive the same numbers from the game's own rules, independently.**

```
stat points:  level cap 70  ->  69 level-ups x 4 per level      = 276  ✓
skill points: 1 per level to 19, then only on even levels
              (the setter skips when  level > 19 && (level & 1))
              18 + 26                                            =  44  ✓
```

Neither derivation used the other. They agree exactly. Done.

**Derivation 3 (completeness, not proof) — enumerate every reader.**
`xref.py offset 0x18a 0x18c` and check each site is semantically consistent: the stat window, the reset item, the HUD badge. A reader you cannot explain means the field is doing something else too.

### Other pairs of independent derivations that work

| derivation 1 | derivation 2 |
|---|---|
| differential RAM diff isolates the offset | a code site materialises the same offset |
| a clamp constant in the setter | the same number from the game's published rules |
| a struct size from an `alloc(N)` | the same size from the field offsets you have mapped |
| an on-disk record length | `payload + header` from the writer's arithmetic |
| a host reimplementation's output | the guest's own buffer, diffed pixel-for-pixel |
| a field's value read live | the same value shown on screen at the same moment |

### What does **not** count as a second derivation

* Reading the same disassembly twice.
* A different tool answering the same question the same way.
* "It looks right on screen" without a recorded measurement.
* Another port's notes for a different title.

---

## Recipe E — recover the original art for a UI element

**Goal:** your host overlay needs to draw something that matches the game's own look — a frame, a badge, an icon — and you want the game's actual pixels rather than an invention.

### Steps

**1. Trace the descriptor-array constructor.** Find the routine that loads the UI sprite sheet: it will load an asset path string from a static slot, allocate `frameCount << 4` (a 16-byte descriptor per frame is common), and store the result into a field of the god object.

Reference **(B)**: constructor at `0x193d66`, path string from `*(sl+0xc9c)`, stores `*(alloc) + 8` into `GOD+0xa384`.

Note the `+ 8` — that is the platform's indirect-handle convention ([address-model.md](address-model.md) §6) and it recurs everywhere.

**2. Find the draw site and read the index.** `base + 0x60` with a 16-byte descriptor stride means **frame 6**.

**3. Decode the asset offline** with your own asserting decoder ([asset-decoding.md](asset-decoding.md)) and check that frame 6's dimensions match what the draw site actually draws (two 7x7 blits, say).

That dimension agreement is derivation #2. You now have the original art with **zero invention** — you did not eyeball a screenshot, you did not guess a palette, and if the asset ever changes your decoder will tell you.

**4. Prefer masks to images for host chrome.** If you are re-drawing the element yourself rather than blitting it, store it as an **alpha coverage mask** rather than RGB: one asset then serves normal, pressed and dimmed states, and it composites correctly against whatever your overlay's colour scheme is.

---

## Choosing between them

| you want | recipe |
|---|---|
| autosave, a save button, a backup export | A |
| relax a restriction, or gate a host feature on the game's own condition | B |
| a multiplier, a rate change, a tuning knob | C |
| any field offset at all, before building on it | D — always |
| host UI that matches the game's look | E |
| a host or a replacement server that must refuse exactly what the game refuses | B, then transcribe rather than patch |
| a save that your own storage layer must not destroy | A, then [save-integrity](../../save-integrity/SKILL.md) |
| an unknown scalar you cannot see in code | [probe-cli.md](probe-cli.md) §8.4, then D |
| an object whose address you do not know but whose shape you do | [probe-cli.md](probe-cli.md) §8.1 |

Whatever you find, record it with its provenance and confidence — `${CLAUDE_PLUGIN_ROOT}/shared/evidence-standard.md`. Then re-read the original bytes at every site immediately before implementing anything ([patching.md](patching.md) §1). **Never implement from a single-source RE note, including your own.**
