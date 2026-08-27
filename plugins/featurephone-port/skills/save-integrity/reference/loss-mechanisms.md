# Every way a port deletes a save

Ten mechanisms by which an emulator port destroys a player's character, catalogued **A..J** with
symptom → mechanism → fix → how to test. Two structural theorems come first, because they redirect the
whole investigation before you look at any mechanism: a guest can only report *empty*, and there are
usually *two* record families per slot. Read §1 and §2 first — most of the time wasted on this subject
goes into debugging a serialiser that was never wrong.

**Tags.** **(A)** generic procedure · **(B)** worked example from the reference port, always introduced
by the method that found it · **(C)** do not copy · `⚠` inferred, nothing was run · `> **REFUTED.**` a
belief that was written down as fact, shipped, and disproved. Confidence on two axes
(`HIGH / CONFIRMED`) per [evidence-standard.md](../../../shared/evidence-standard.md).

## Contents

1. ["Empty" vs "corrupt" — what the guest can express](#1-empty-vs-corrupt--what-the-guest-can-express)
2. [The two record families, and which one decides visibility](#2-the-two-record-families-and-which-one-decides-visibility)
3. [Mechanism A — create-mode open truncates up front](#3-mechanism-a--create-mode-open-truncates-up-front)
4. [Mechanism B — non-atomic replacement](#4-mechanism-b--non-atomic-replacement)
5. [Mechanism C — ★ a failed write becomes a delete](#5-mechanism-c---a-failed-write-becomes-a-delete)
6. [Mechanism D — the discarded store verdict](#6-mechanism-d--the-discarded-store-verdict)
7. [Mechanism E — a whole-directory delete bypasses the guard](#7-mechanism-e--a-whole-directory-delete-bypasses-the-guard)
8. [Mechanism F — the server overwriting a live character](#8-mechanism-f--the-server-overwriting-a-live-character)
9. [Mechanism G — the write lands in the wrong slot](#9-mechanism-g--the-write-lands-in-the-wrong-slot)
10. [Mechanism H — the guard itself](#10-mechanism-h--the-guard-itself)
11. [Mechanism I — broken quit → force-swipe → the open windows](#11-mechanism-i--broken-quit--force-swipe--the-open-windows)
12. [Mechanism J — the recovery feature eating its own backups](#12-mechanism-j--the-recovery-feature-eating-its-own-backups)
13. [Refuting the plausible narrative](#13-refuting-the-plausible-narrative)
14. [Symptom → mechanism](#14-symptom--mechanism)
15. [Checklist: the adversarial storage pass](#15-checklist-the-adversarial-storage-pass)

---

## 1. "Empty" vs "corrupt" — what the guest can express

> **A record-store guest can only say "empty". It has no way to say "corrupt". Every "my character
> disappeared" report is therefore a DELETION or TRUNCATION event, and the whole investigation belongs
> in the write and delete paths.**

`HIGH / CONFIRMED` — two independent routes: the loader's own code, and two field reports whose slots
read as empty and *stayed* empty across a relaunch.

**Establish it on your own title (A).** Enumerate the save loader's zero-returning paths — **(B)** in
the reference port there were exactly two, `seek(handle, 0, END) <= 0` (absent or zero bytes) and a
checksum mismatch. Check whether the summary builder that feeds the slot list can express failure at
all — **(B)** it was `calloc` plus an unconditional fill: no error return, no "absent" encoding, no
third state. Then cross-check the reports: a corrupt record would *load* and misbehave.

> **REFUTED.** *"The save got corrupted — look for a serialisation, encoding or length bug."*
> **Refuted by:** the loader's two zero-returning cases and the summary builder's unconditional fill,
> then by relaunches that kept showing an empty slot.
> **Actual mechanism:** the record file was deleted, or left zero-length, by the write/delete path
> (§3–§7). The bytes were never wrong.
> **Why the wrong reading is attractive:** "corrupt" is the only failure most developers have a model
> for, and the serialiser is the code you can actually read.
> **Consequence if carried forward:** the entire class of fix — atomic writes, deferred truncate,
> guarded deletes, a pre-image vault — is invisible from the corruption hypothesis.

**Corollary (A): fork your symptom row in two.** *"Reads as empty from the first boot, on a save that
never existed"* is a read-path bug — a size-query mode returning 0, a name-keyed delete that is a
no-op upstream ([wipi-c-recipes.md](../../emulator-core/reference/wipi-c-recipes.md) §7). *"A slot that
used to work now reads as empty"* is the deletion class, and **nothing in the read path can cause it**.
One row for both is how a deletion bug gets triaged as a compatibility bug.

---

## 2. The two record families, and which one decides visibility

A guest commonly keeps **two** record families per slot: a large payload and a tiny summary — and the
slot list's occupancy decision frequently reads **only the summary**, so losing 32 bytes makes an
intact character invisible and the report is indistinguishable from a total loss.

**Method (A).** List every record name the guest opens across one save and one slot-list draw, look for
a second much smaller family, then find the code that sets the per-slot occupancy flag and check which
family it reads. **(B)** In the reference port the app-state transition that draws the slot list scanned
only `<Summary>_<slot>`; the payload was never consulted. Payload ~1.4–1.9 KB, summary **32 B** on disk
(16 B content + a 16 B writer header). `HIGH / CONFIRMED`.

* **Vault them together, restore them together, redirect them together.** One unit, one decision. A
  redirect carries the summary for free *if* you redirect the landing point rather than the guest's aim
  (§10), because both records come from the same writer at the same aim.
* ⛔ **Never offer a lone summary for restore** — it produces a menu entry for a character that is not
  there, the exact state the player is trying to escape. ⛔ Never protect only the payload either.

---

## 3. Mechanism A — create-mode open truncates up front

**Symptom.** A character vanishes after a process kill, most often a force-swipe (§11). Never
reproducible on demand.

**Mechanism.** The guest's save is `open(create) → N stream writes → close`. If create-mode open deletes
the record **up front**, the character does not exist on disk for the entire width of that sequence, and
any kill, crash, storage error or host refusal inside it is permanent. Everything else in this file sits
downstream: nothing can protect a record the storage layer already removed on the guest's behalf.
`HIGH / CONFIRMED` (open-path inspection plus two reports of losses after a force-swipe).

**Fix (A): defer.** A per-handle `pending_truncate` set at open, **cleared on the first successful
write**, executed at close only if no write ever landed. The record now stops existing at the same
instant the replacement lands, not one open earlier.

**Two seeding rules that come with it (A).** A create-mode open must **not** truncate a database whose
contents came from a packaged read-only archive member — capture that distinction *before* consuming the
packaged data and gate both the immediate seed and the deferred flag on it, or you wipe shipped seed data
and it presents as a content bug. And non-create modes must seed the per-handle buffer from the existing
record, so seek-and-overlay writes preserve unrelated bytes: multi-slot saves at fixed byte offsets are
exactly that shape, and wrong seeding silently zeroes the other slots.

| test case, at the store, no emulator | required outcome |
|---|---|
| create → one successful write → close | new record present; old bytes in the vault |
| create → a *failing* write → close | previous record **byte-identical** (§5) |
| create → no write at all → close | removed *through the same policy gate and vault as any other delete* (§7, §9) |

**Log it.** Create-mode opens and executed truncates at **warn**, with the open *mode* in the line
([policy-and-diagnostics.md](policy-and-diagnostics.md) §9) — about one line per record written, and the
only thing that will let you confirm or exclude this mechanism from a stranger's log.

---

## 4. Mechanism B — non-atomic replacement

**Symptom and mechanism.** Rare, unreproducible losses, because the failure needs a kill or an I/O error
inside a sub-millisecond window. `fs::write` — any `open(O_TRUNC) + write` — leaves the file **zero
bytes** for the width of one write syscall, and a zero-byte record is exactly what the guest reads back
as "no character" (§1).

> **REFUTED.** *"Use plain synchronous file writes. The property you need is: when the guest's write
> call returns, the bytes are on disk."* — this plugin's own advice, in
> [storage-and-config.md](../../android-host/reference/storage-and-config.md) §2.
> **Refuted by:** two characters lost in one week on a build whose writes were fully synchronous.
> **Actual mechanism:** synchronicity is about *when* bytes land; atomicity is about whether a reader
> can ever see a partial state. The record is destroyed by the truncate that *precedes* the write.
> **Why the wrong reading is attractive:** it is the correct fix for the previous bug (buffered writes
> lost to a task kill), and it is stated as an absolute.
> **Consequence if carried forward:** an unreproducible trickle of destroyed characters, invisible in
> testing and routine in the field.

**Fix (A).** Temp file in the same directory → `sync_all` → `rename` → best-effort directory fsync.
Rename is atomic within a directory, so the old record stays fully readable until the new one is complete,
and the two syncs order the bytes before the directory entry that names them. **A directory-fsync failure
is not a failed save** — the record is already whole, so report success; reporting failure there re-enters
§5 and turns a durability nit into a deletion. Write the invariant at the definition site with its symptom
and date: *refusing to save is recoverable, half-saving is not* — and its second half, **a failed write
must leave the previous record byte-intact.**

---

## 5. Mechanism C — ★ a failed write becomes a delete

The highest-value entry in this file, and an *inversion*: the layer that promised "a failed save leaves
the previous record intact" is the layer deleting it.

**Symptom.** The loss rate goes **up** right after you add a storage-layer guard, an error return or a
capacity check. Or the game reports a successful save and the character is gone.

**Mechanism.** Compose §3's deferred truncate with the guest writer's shape. The normal shape is
`stream_write(...)` followed by an **unconditional `close`**, with the return value **never loaded**. So
if the truncate is cleared only on the success path, **every failed write becomes a delete**: nothing
wrote, close fires the truncate, the previous character is gone. `HIGH / CONFIRMED` — the guest writer
read against the host flag's lifecycle, then pinned by two regression tests. It is also the most
attractive bug here to reintroduce, because the code reads as *cleanup* — "we opened for create and
nothing was written, so remove the stale record" is a sentence a reviewer will approve, and the stale
record is a live player's character.

**Fix (A).** One `cancel_truncate` closure routed through **every** early return in the write function,
not just the store-failed path: argument-arithmetic guards, capacity failures, an unknown or
already-closed handle, a **host policy refusal** (§10 — the newest and least obvious one), and any error
from the layer below. Count the `return`s and count the disarm sites; make the two numbers match in
review, not in your head.

**Ordering constraint with §6.** Returning the failure code to the guest is only safe **once every
failure path also disarms the truncate**. Do them in that order, in one commit, with the tests — the
reverse order converts a silent lie into a deletion.

**Test.** Two regressions named after the behaviour, not the mechanism: a forced store failure leaves the
previous record **byte-identical**; and a **deliberate** truncate-and-close still deletes — otherwise you
have disarmed the mechanism the guest legitimately uses to clear a slot, and "reset save" silently stops
working.

---

## 6. Mechanism D — the discarded store verdict

**Symptom and mechanism.** The game says "saved" and the character is not there, so the player trusts
the confirmation and stops keeping their own copies. The host's stream-write discarded the store's
success flag, so a full disk, an I/O error or a policy refusal is reported to the guest as a success and
the only trace is a report hours later.

**Fix (A).** Return the failure — guests typically pass the writer's return value all the way up, so a
real failure code is something the game can act on instead of a silent deletion. **(B)** In the reference
port returning `-1` from the host's stream-write reached the guest's own save-record writer.

`⚠` **Whether the game acts on it is a per-title question.** The reference guest's *writer* received the
code and its *caller* ignored it (§5). Settle it by injecting a store error on a device and watching the
screen, not by reading the caller. A verdict the guest ignores is still worth returning — it is what
makes the log line (§3) and the tests meaningful — but do not build a recovery flow on it.

---

## 7. Mechanism E — a whole-directory delete bypasses the guard

**Symptom and mechanism.** A player-initiated "reset this slot" wiped a character with **no archived
copy**, even though every ordinary write in the port is archived. The carrier runtime may map the guest's
delete-*record* call onto the host's delete-*database* — a recursive directory removal — which bypasses a
vault hooked on the per-record set/delete calls. The one irreversible action in the game becomes the one
action with no archive behind it. `HIGH / CONFIRMED`, and note **how it was found: by auditing all
callers, not from a report.** Nobody had hit it yet.

**Fix (A).** Audit paths, not reports: enumerate every host path that can remove a record — per-record
delete, database delete, a close that truncates, a recursive directory removal, and any "reset" or
"clear data" affordance in *your own* UI — and archive every record in the directory before a recursive
delete resolves.

**Two exemptions a delete must have (A).** The vault's rate limit and its identical-content shortcut are
both **skipped** for a delete. A delete is not one of a series; it is the last thing that ever happens to
that record, so a copy declined there can never be taken again. The window is real: a game that rewrites
its save on every container deposit and page turn has the rate limit armed when the player's reset
arrives. Detail in [save-vault.md](save-vault.md) §8.

> **REFUTED.** *"The vault hooks set() and delete(), so every deletion is archived."*
> **Refuted by:** a caller audit that found the guest's delete-record SVC resolving to a `remove_dir_all`
> with no vault hook.
> **Actual mechanism:** two granularities (record vs database), two host functions, one hook.
> **Why the wrong reading is attractive:** the hook *is* on the API the guest appears to call.
> **Consequence if carried forward:** a vault covering 95 % of deletion paths, missing precisely the
> generation the player needs — the one the player confirmed on purpose.

---

## 8. Mechanism F — the server overwriting a live character

**Symptom.** A live character reset to an old, *partial* version immediately after entering an online
screen — the player reports it as *story, equipment and items all reset*.

**Mechanism: one request opcode, two senders.** **(B)** In the reference protocol a login sub-request and
a destructive character-restore request were **byte-identical on the wire** — found in production server
logs, where the same seven bytes appear annotated once as a restore and once as a login sub-request. The
client's restore handler has no occupancy check, so the reply is deserialised over the loaded character;
the backup fragment is header + equipment records + tail, with no story flags and no inventory, and it
is whatever was last uploaded — so story and inventory vanish outright while equipment reverts to a
stale snapshot. **(A)** What identifies the mechanism is the *timing* (immediately on entering an
online screen, with no save action by the player), not the shape of what survived. `HIGH / CONFIRMED`.

**Fix (A) — on the client, not the server.** No server can distinguish the two senders; they are the same
bytes. Only the host owns the record store and can answer *"does this slot already hold a character?"*. In
the host's send path: ask **one** question about **the one slot the frame names**, on the caller's thread
(it runs inside a guest send, so one filesystem look is the whole budget); **rewrite** the frame into the
sibling sub-request of the same fork — ⛔ do not drop it, dropping leaves the client waiting on a reply
forever and only that sibling's reply clears the wait state; and **answer conservatively**, treating
anything unreadable or out of range as **occupied**. A false "occupied" costs one recovery that does not
run; a false "empty" costs a character.

**Two adjacent rules, owned by [threat-model.md](../../server-revival/reference/threat-model.md).** A
backup table keyed `PRIMARY KEY (account, slot)` has no history, so one upload of a damaged character
destroys the last good copy — and the damage itself is what uploads; add bounded append-only history,
promote-by-timestamp, and no row for an identical replay. And **offline mode is a save-safety feature, not
a convenience**: an in-app engine beside the save files consults occupancy directly and never sends a
character. **(B)** The reference port made offline the default the same day it established the server had
been overwriting live characters, and stated the cost honestly — a player with items on the live server
cannot see them until they switch modes.

---

## 9. Mechanism G — the write lands in the wrong slot

**Symptom.** One slot's character has been replaced by another slot's. In a vault listing it shows as a
*length jump* in that slot's generation timeline ([save-vault.md](save-vault.md) §6).

**Mechanism.** The slot number in a save record's name is frequently a **slot-list cursor**, not the
identity of the loaded character.

**Method (A)** — two static questions. Find the writer that appends `_<slot>`, find where it reads that
index, and **enumerate every writer of that word**; then separately ask **whether anything unloads the
character**, by counting the callers of the character-reset routine.

**(B)** In the reference port the writer built its name with `sprintf("%s_%d")` from one word in the god
object. Every writer of that word was menu/shell code — the title and online menu rows, both slot screens'
up/down/cancel arms, one modal handler — and the character-reset routine had **exactly one caller**, a
network reply. So nothing unloads on navigation, and *merely moving a highlight in a slot list re-aims
every subsequent save at another character's row*, after which the game's container screens save
constantly. `HIGH / CONFIRMED` (exhaustive writer enumeration; reproduced from a report and from traces).

> **REFUTED.** *"The `_<slot>` suffix in the record name identifies the character the game loaded."*
> **Refuted by:** enumerating every writer of the index word — all UI code — and finding that the save
> writer takes no slot argument at all.
> **Actual mechanism:** the field is a UI cursor the shell moves for its own reasons; the writer reads it
> because it is the only slot number in scope.
> **Why the wrong reading is attractive:** the name is `<something>_<slot>`, and the game saves the right
> character most of the time — because most of the time the cursor happens to agree.
> **Consequence if carried forward:** the whole "my other save turned into this save" class, arriving
> hours later, with no error on screen and nothing in the log.

**⛔ The fix cannot live in the guest.** Patching the cursor breaks the slot screens that own that word.
Redirect the *disk landing point* instead (§10), gated on a pin whose only legitimate source is an
**observed character load** — and there is usually more than one load path. This is the hardest problem in
the subject and it has its own file, **[slot-identity.md](slot-identity.md)**: five successive attempts
shipped and were refuted on devices, and its §14 records each one with the report that killed it. **Read
that before adding a clause to a policy that currently works.**

---

## 10. Mechanism H — the guard itself

A guard that can silently **refuse** a write is a data-loss mechanism in its own right, and it presents as
an unrelated *gameplay* bug.

**Symptom pair, exact (B).** A withdrawn item disappeared after a restart; a deposited item duplicated.
Two reports, **one** bug: the host-owned container file shrank on disk while the receiving inventory was
never persisted, and the container grew while the inventory rolled back. The guard refuses, the guest
ignores the failure code (§5, §6), and the rest of the session commits anyway — so guest state diverges
from host-owned files that already landed, with no save error on screen because nothing in the game knows
a save was refused. `HIGH / CONFIRMED` — shipping a reject-only policy produced both reports.

> **REFUTED.** *"Fail closed: if the host cannot prove which character a write belongs to, refuse it.
> Refusing protects the innocent slot."*
> **Refuted by:** the two reports above, on the shipped reject-only build, plus "the container's autosave
> stopped working" — one bug, three symptoms, none of them shaped like a save error.
> **Actual mechanism:** refusing protects the innocent slot and destroys the player's session.
> **Why the wrong reading is attractive:** "fail closed" is the right instinct for a destructive
> operation, and it is what every review will ask for.
> **Consequence if carried forward:** a reproduced data-loss bug traded for a silent session-destroying
> one, which you will then debug as an inventory bug.

**Fix (A): a three-valued verdict.**

| verdict | when | why it is safe |
|---|---|---|
| **Allow** | the write targets the pin's own slot | safe *only* because the pin is exact — [slot-identity.md](slot-identity.md) §4 |
| **Redirect(slot)** | a foreign target whose owner is proven | the serialiser has no source but the loaded character, so bytes aimed at a drifted slot are **by construction** the pinned character's save |
| **Refuse** | you cannot prove it | last resort, not the default |

**Redirect the disk landing point, never the guest's cursor.** Renaming the target directory for one write
is invisible to the guest, returns success so its deferred truncate resolves normally (§3), and leaves the
original record untouched; a rename you cannot construct falls back to **refusal**, never to the drifted
aim. The summary record (§2) follows its payload automatically. **Delete gets its own verdict function with
no Redirect arm** — deleting the pinned character because the guest aimed elsewhere is the worst outcome
available.

> **REFUTED.** *"After a slot was destroyed, add one more condition: require the observed identity to be
> non-zero before redirecting."*
> **Refuted by:** a reverted attempt that reproduced the item-evaporation regression above and protected
> nothing.
> **Actual mechanism:** the "identity" field was a **class** name written only by world init, so it
> legitimately reads zero on both sides; `0 == 0` must count as a match. Safety came from making the pin
> exact on every observed load, not from tightening the comparison.
> **Why the wrong reading is attractive:** it is the natural second-order fix, it looks like defence in
> depth, and it is one line.
> **Consequence if carried forward:** a real, reproduced data-loss bug traded for an imaginary one, and the
> game stops saving in exactly the flow the player uses.

One more arming failure belongs here and is owned by
[policy-and-diagnostics.md](policy-and-diagnostics.md) §1: a guard armed only "while in play", where the
app-state the port called *shell* also covered the online screens, was **never armed at all** on the route
players used, so the policy returned Allow unconditionally. The rule was right; its load-bearing condition
sat in a third file no test could reach.

---

## 11. Mechanism I — broken quit → force-swipe → the open windows

**Symptom and mechanism.** "The exit button does nothing, so I swipe the app away." A force-swipe kills the
process at an arbitrary instant — precisely the instant §3's and §4's windows are open. **(B)** Both of the
reference port's original loss reports involved force-swipes, and its platform exit hook turned out to be a
one-line log-and-return. A broken in-game quit is a **save** bug, not a cosmetic one; that reframing is the
finding.

**Fix (A).** Wire quit to a real teardown **and** a bounded final save on the way out. **(B)** The reference
request routes through the worker, which attempts a final save with a ~1.5 s budget before the UI tears the
activity down — bounded, because a quit that hangs is a quit players force-swipe.

**The carrier-selector trap.** ⛔ Do not assume the carrier's documented exit call is how the game asks to
quit. **(B)** In one runtime the quit arrived as a constant **selector on an unrelated kernel SVC** that the
fork had stubbed to return zero, while the documented exit handler was correctly wired and never called
once. **Isolation technique (A):** drive the in-game quit **once** with everything traced, then find the stub
that fires exactly then and **never** during boot, title, load, play or save — a whole-session trace is what
makes "exactly once, and only there" provable. **(C)** The selector value itself is title- and
runtime-specific; do not carry it.

---

## 12. Mechanism J — the recovery feature eating its own backups

The feature that exists to undo a data loss is fully capable of causing one. **(B)** A player reported that a
single restore consumed three of six retention seats and, in the worst case, ate the very generation being
restored.

**Three costs nobody budgets.** (1) The pre-restore capture — which you **must** take, unconditionally,
before overwriting the current bytes — spends a retention seat. (2) The rotation that capture triggers runs
over the **same directory** and can delete the generation being restored. (3) The first save afterwards
vaults a **byte-identical duplicate**, because the rate limit that would normally suppress it never fires: a
boot-time restore happens *before the emulator exists*, so the "last vault write" timestamp is still zero.

**Fix (A).** Pin the restored generation. Hold the pin **outside** the retention tiers — no newest-N seat, no
day-tier seed — but count it toward the ceiling, and decline a pre-image that byte-matches the pin. Implement
it as a **sidecar file whose name the generation parser cannot match**, so `list`, `export` and `rotate` all
ignore it with no change to any of them. Property-test it: a 500-iteration save loop must never age the
pinned generation out.

**Three adjacent ways the same feature loses data (A):**

* **Applying a restore live** is undone seconds later, or half-applied — the record may be open with its
  contents mirrored in a guest-heap buffer that the next write-through snapshots back to disk. Apply at the
  **next launch**, before the guest opens any database, and delete the request file **first** so a crashing
  restore cannot re-run every launch; a restore loop is indistinguishable from the bug it exists to undo.
* **Flat retention.** Reports arrive about a **day** late, and at a 5-minute autosave cadence a flat six-deep
  window covers half an hour. Two tiers plus a ceiling — **(B)** newest 6, one per day for 6 days, ceiling 16.
* **A recovery notice that has never fired.** **(B)** A boot-time "your save is missing but recoverable"
  check read a path that only this process's *own* record writes populate, and its only caller was the boot
  check, which runs first. No error, just an absence. Give it the same fallback directory scan the listing
  uses — that is exactly the case that matters, a player opening diagnostics at the title screen before the
  guest has booted.

[save-vault.md](save-vault.md) owns the design. These entries are catalogued here because each is a way to
**lose data**, not merely a way for a feature to underdeliver.

---

## 13. Refuting the plausible narrative

Loss reports arrive with a confident theory attached, frequently one a previous helper supplied and the
reporter now believes. Following one costs a session. Three from the reference port, with the one-step
refutation of each. **(A)** The refutation *shapes* are reusable; the theories are the ones players bring.

> **REFUTED.** *"The save grows with playtime until it overflows the record."* Refuted by one measurement:
> the record is fixed-shape serialisation, not an append log — measure two saves hours apart. **Inverse
> trap:** a serialised length *is* content-dependent, so never pin one as an invariant either; use a
> plausibility band ([patching.md](../../guest-re/reference/patching.md) §6).

> **REFUTED.** *"Leaving to the main menu rewrites the save, and that is when it broke."* Refuted by
> enumerating the slot list's calls — it is a read-only path. **But note what is true nearby, and worse:**
> it moves the cursor (§9). The reporter named the right screen for the wrong reason.

> **REFUTED.** *"A host-side feature's timer values got written into the record."* Refuted structurally:
> host state lives in host files and never enters the guest record. If your port breaks that separation,
> fix *that* first and the theory becomes true.

**What in the report is actually useful: the location.**

> **A loss becomes VISIBLE at a read site and is CAUSED at a write site.**

The reporter's valuable observation is *where the symptom surfaced* — a shop screen, a main menu, a slot
list. Those are read sites. Convert it into a direction: take the read site, find what wrote that record
last, and work backwards along the write path. Do not debug the read site. This one distinction turned the
reference port's least useful report into its most useful one.

**One meta-refutation, because it will happen to you.** A **green** selftest can be defending the bug you are
hunting: `include!`ing the real source protects against *drift*, not against a *wrong premise*, and a test
whose comment asserts an **interpretation** of its input ("this sequence is not a load") pins the
interpretation — the thing under dispute. **(B)** Two shipped bugs were held green this way, both with the
test's own comment naming the input incorrectly. When you find a bug, **grep the tests for its input** and
re-file it as a named regression test whose name states the reported sequence
([policy-and-diagnostics.md](policy-and-diagnostics.md) §7,
[selftest-convention.md](../../../shared/selftest-convention.md)).

---

## 14. Symptom → mechanism

| symptom, as the player reports it | mechanism | first thing to do |
|---|---|---|
| "Empty slot, and after a restart it is completely gone" | §1: deletion or truncation. Candidates in order §3, §5, §4 | Stop reading the serialiser; check the create-mode open and the truncate's lifecycle |
| A working slot reads as empty, only after a force-swipe | §11 into §3/§4 | Fix quit; defer the truncate; make replacement atomic |
| **Every** slot reads as empty from the first boot | not this file — read-path bug | [wipi-c-recipes.md](../../emulator-core/reference/wipi-c-recipes.md) §7 |
| An intact character is invisible in the slot list | §2 — the 32-byte summary is gone | Restore the pair as one unit |
| One slot's character has been replaced by another's | §9, or a pin armed from something other than an observed load | [slot-identity.md](slot-identity.md); look for the length jump in the vault listing |
| Log: `opened <create mode> and closed with NO write — record 1 deleted` | §3 + §5; on a re-aimed cursor it removes *another* character | Disarm the truncate on every failure path; gate deletes with the same pin policy |
| Log: `record 1 … was NOT stored`, and the game said "saved" | §6 | Return the verdict — after §5's disarming lands |
| Saves stop persisting after an online screen; withdrawn item vanishes, deposited item duplicates | §10 — a guard is silently refusing | Grep the log for the REFUSED verdict line during that flow; check the guard's arming condition |
| A live character reset to an old partial version on entering an online screen (story, equipment and items all reset) | §8 — dual-use opcode | Guard on the client; rewrite the frame, never drop it |
| "Reset this slot" wiped a character with nothing archived | §7 — whole-directory delete | Audit every deletion path; archive before the recursive delete |
| A restore appears to work, then the old data is back | §12 — applied live over an open record | Apply at next launch; consume the request first |
| After one restore, fewer generations remain and the restored one is gone | §12 — three unbudgeted costs | Pin the restored generation outside the tiers |
| The "missing but recoverable" notice has never appeared | §12 — reads a path only this process's writes populate | Give it the listing's fallback directory scan |
| "Saving, please wait" looks broken — its button does nothing | not a loss, but you will get this report: an input-swallowing modal drawn with an answerable OK band | Draw a variant with no button; test the more specific modal *before* the save lock in the key path |
| A fix passed its tests, shipped, and destroyed a save — again | the guard trusts one scalar, so every added clause is downstream of a wrong input | [slot-identity.md](slot-identity.md) §14 — provenance, not clauses |

Cross-referenced into [fault-dictionary.md](../../../shared/fault-dictionary.md) §2.10.

---

## 15. Checklist: the adversarial storage pass

Run this as an **attack**, not a suite you pass once. "Saves survive a task kill" is compatible with every
loss in this file — the reference port passed it on all five of the builds it later recalled.

- [ ] A **forced** write failure, injected at the store, leaves the previous record **byte-identical**. §5
- [ ] Every early return in the write function disarms the pending truncate — enumerated from the source and **counted**. §5
- [ ] A deliberate truncate-and-close still deletes, so "reset save" was not disarmed by the fix. §5
- [ ] Create-mode open + close with no write: removal goes through the same policy gate and vault as any other delete, and one aimed at a slot that is not the pin's is **refused**. §3 §9
- [ ] A create-mode open on a database seeded from packaged archive data does **not** truncate it. §3
- [ ] Every replacement is temp + `sync_all` + rename; a directory-fsync failure reports **success**. §4
- [ ] Every host path that can remove a record is enumerated and archives first — including the whole-directory delete — and a delete is exempt from the rate limit and the identical-content shortcut. §7
- [ ] The write's verdict reaches the guest, shipped in the same commit as the truncate disarming. §5 §6
- [ ] Payload and summary are vaulted, restored and redirected as **one unit**; a lone summary is never offered. §2
- [ ] The policy prefers **Redirect** to Refuse, redirect changes only the disk landing point, and delete has its own verdict function with **no Redirect arm**. §10
- [ ] A restore applies at the next launch, costs exactly **one** retention seat, and cannot age out the generation it restored. §12
- [ ] The destructive server opcode is guarded on the client, **rewritten** rather than dropped, answering "occupied" for anything unreadable or out of range. §8
- [ ] In-game quit really exits **and** attempts a bounded final save. §11
- [ ] Create-mode opens, executed truncates and refused/failed writes log at **warn/error** with the open **mode** in the line, plus one verdict line per character-record write carrying every fact the verdict used. §3, [policy-and-diagnostics.md](policy-and-diagnostics.md) §9
- [ ] A **device** walk of the exact sequences from the reports, with those verdict lines in the log. A temp-dir test and a headless probe are prerequisites, not substitutes — this is the one subject where the device tier cannot be skipped.
- [ ] Nobody in your support flow ever says **"uninstall"**. Save records and the vault both live in app-private storage, so an uninstall destroys the evidence and the recovery path together — and a signing-key change forces one, which makes a key change a save-destroying event. [distribution-and-recall.md](../../port-shipping/reference/distribution-and-recall.md)
