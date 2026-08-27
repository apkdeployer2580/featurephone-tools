# The save vault — archive the pre-image before overwriting it

Every other file in this skill prevents a loss. This one survives the losses you have not found yet.
The vault is an archive-before-overwrite hook in the record store: it copies the bytes that are about
to stop existing, names each copy so a directory listing is a forensic timeline, keeps generations
long enough for a report to arrive, and gives the player a restore that needs no key and no working
game. It is the cheapest thing in this subject and the only thing that turns "my character is gone"
into a support action rather than a lost character.

Tags: **(A)** generic · **(B)** worked example from the reference port, re-derive it ·
**(C)** do not copy. `⚠` marks a claim nothing has been run against yet.

## Contents

- [1. Why nothing else is a backup](#1-why-nothing-else-is-a-backup)
- [2. Where it hooks, and where the path comes from](#2-where-it-hooks-and-where-the-path-comes-from)
- [3. ★ Pre-image, not post-image](#3--pre-image-not-post-image)
- [4. Whole files, ciphertext and all](#4-whole-files-ciphertext-and-all)
- [5. What to vault, and what to exclude](#5-what-to-vault-and-what-to-exclude)
- [6. ★ Generation naming as a forensic instrument](#6--generation-naming-as-a-forensic-instrument)
- [7. Retention: two tiers plus a ceiling](#7-retention-two-tiers-plus-a-ceiling)
- [8. `should_vault`, and the delete exemption](#8-should_vault-and-the-delete-exemption)
- [9. Restore](#9-restore)
- [10. The pin sidecar trick](#10-the-pin-sidecar-trick)
- [11. Export — the only channel off an unrooted release build](#11-export--the-only-channel-off-an-unrooted-release-build)
- [12. Bounded and silent](#12-bounded-and-silent)
- [13. The boot check, and what it must not read](#13-the-boot-check-and-what-it-must-not-read)
- [14. Property tests that make rotation safe to change](#14-property-tests-that-make-rotation-safe-to-change)
- [15. Symptom → cause](#15-symptom--cause)
- [16. Checklist](#16-checklist)

---

## 1. Why nothing else is a backup

**(A)** A feature-phone save is one record per slot, replaced in place. No journal, no `.bak`, no
generation the platform keeps for you — and on Android, app-private storage is in no backup a player
can reach without root. **The first bad write is final, and it is final for whoever hits it before
you find the bug.** **(B)** In the reference port two players lost characters in one week, from two
different mechanisms, and at that point the device held no second copy of a save anywhere; neither
loss could be *confirmed* afterwards, let alone undone.

> **REFUTED.** *"The online backup upload is the backup."*
>
> **Refuted by:** production server logs plus the schema. The table was keyed
> `PRIMARY KEY (account, slot)` — one mutable cell per slot — so a single upload of a damaged
> character overwrote the last good copy, and the client re-uploads on ordinary events (**(B)** on
> every container page turn). The stored fragment was an *old* one carrying equipment records only —
> no story flags, no inventory — so restoring it produced the matching player report: *story,
> equipment and items all reset*.
> **Why it was attractive:** the feature is *called* a backup, the player is told to rely on it, and
> it demonstrably restores something.
> **Consequence if carried forward:** the damage itself destroys the recovery, in one round trip. A
> server-side backup earns the word only with an append-only history, a per-`(account, slot)` cap and
> a promote-by-timestamp operation —
> `${CLAUDE_PLUGIN_ROOT}/skills/server-revival/reference/threat-model.md`.

**(A)** Design target, so you can tell when you are done: *any* single destructive event in the save
path — one you fixed, one you have not found, one the server caused, one the player confirmed on
purpose — leaves a recoverable copy behind and a record of when it happened.

---

## 2. Where it hooks, and where the path comes from

**(A)** Hook the **record store**. It is the only layer every save write, every save delete and
every save read passes through, which is why it carries three responsibilities that look unrelated
and are not:

| responsibility | owner |
|---|---|
| archive the pre-image on `set()` and `delete()` | this file |
| gate the write or delete (whose character is this?) | [slot-identity.md](slot-identity.md) |
| observe that a read of the payload record **is** a character load | [slot-identity.md](slot-identity.md) |

**(A) Enumerate every host path that can remove a record before you claim coverage.** A hook on the
per-record `set`/`delete` pair is not enough. **(B)** In the reference port the carrier runtime mapped
the guest's *delete-record* call onto the host's *delete-**database*** implementation — a recursive
directory removal with no hook in it — so the one irreversible action in the game (the
player-confirmed slot reset) was the one action with no archive behind it. That was found by auditing
all callers of the deletion primitives, **not** by a report. Also audit the create-mode truncate, the
deferred truncate executed at close, and the restore itself (§9); all are catalogued in
[loss-mechanisms.md](loss-mechanisms.md).

**(A) Derive the vault directory from the path of the record you are about to overwrite.** The record
store knows it, at the moment it matters, and it is the only value that cannot be pointing at a stale
root, a different profile or a different user. Never a config value, and never a static that some
earlier write happened to populate — §13 is the bug that costs you.

**(A)** One subdirectory per record name, generations inside it, so retention (§7) is per record — a
chatty record cannot evict a quiet one's generations — and the pin (§10) has an obvious home.

---

## 3. ★ Pre-image, not post-image

**(A)** The hook archives the bytes **on the way out**: what is on disk *before* the write lands, or
before the delete removes it. Not the new bytes, and not a periodic snapshot of the current file.
The reason is not storage cost, it is **judgement**:

> A post-image archive would also have caught every loss the reference port shipped. What it cannot
> do is tell you **which generation is the good one** — because in a destructive event the newest
> write *is* the wipe. A pre-image never has to know.

Two corollaries, both load-bearing **(A)**: the vault **takes no view on whether the write is
legitimate**, so it stays correct under every verdict the policy layer reaches, including the wrong
ones (and that layer has been wrong repeatedly — [slot-identity.md](slot-identity.md) §14); and
because the copy precedes the risky operation, **an aborted write still leaves the archive** rather
than leaving nothing.

---

## 4. Whole files, ciphertext and all

**(A)** The unit of work is the **file**, byte for byte, including whatever encryption the guest
applied. **(B)** In the reference port a record is an encrypted payload plus a writer header, so the
host cannot read a character name or a level out of it. That looks like an obstacle and is the
opposite:

* **Restore is a file copy** — no guest call, no key, no allocation, no deserialiser, no knowledge of
  the save format. **(A)** Anything needing plaintext needs the guest running, which is precisely
  what you cannot rely on during a recovery.
* **It works at the title screen**, which is where the player is standing when they see the empty
  slot they want back.
The price — **(B)**, and only while the payload is encrypted — is that the only readable signal about
*which character* a record holds is its **length**, which is why §6 puts the length in the file name.

---

## 5. What to vault, and what to exclude

**(A) Match record names by PREFIX, and judge the prefix on the name's leaf.** Prefix matching
survives the parts of the name that are not yours to predict — the slot suffix, a build number
embedded in the middle, a carrier variation — while judging on the leaf stops a nested name from
smuggling a match past the filter.

**(A) A slot is usually two records, and they are one unit.** Guests commonly keep a large payload
plus a tiny summary, and the slot list's occupancy decision reads **only the summary** — so losing 32
bytes makes an intact character invisible. Vault, rotate and restore them together, and **never offer
a lone summary for restore**: that produces a menu entry for a character that is not there, which is
the state the player is trying to escape. Mechanism:
[loss-mechanisms.md](loss-mechanisms.md) §2. **(B)** Reference sizes: payload ≈1.4–1.9 KB, summary
32 B on disk = 16 B of content plus a 16 B writer header.

**(A) Exclude the options/settings record**, and anything else whose loss costs a settings screen.
**(B)** That exclusion is deliberate in the reference port: vaulting it would roughly double the
vault's write traffic to protect something the player re-enters in ten seconds. Every added record is
write amplification inside a guest storage call (§12) — the list is a whitelist, never a wildcard.

---

## 6. ★ Generation naming as a forensic instrument

**(A)** Name each archived generation:

```
<zero-padded unix-ms>-<length>.bin        e.g.  01756100480123-1792.bin
```

**Zero-padded, fixed width.** A lexicographic listing is then a chronological listing, for every tool
— `ls`, the platform file picker, your own enumerator — with no parsing and no sort code. A
millisecond stamp is 13 digits today, so **(B)** the reference port pads to 14 and the ordering stays
valid for millennia.

**The length in the name.** Per §4 the length is the *only* readable owner signal **when the record
is encrypted** — if yours are plaintext, put a character identifier in the name too and the rest of
this section is unchanged. Putting the length in the name makes the vault a timeline you can read
**without decrypting a byte or even copying a file**:

> **A jump in length names the moment that slot became a different character.**

**(B) What that bought, in the field:** a single shell listing of an exported vault (§11) located a
save-destroying regression to **six minutes after a specific build was installed**, and showed that
the *direction* of the damage had reversed relative to the previous build — with every record itself
unreadable. Cost: one format string.

**(A) The inverse parser must reject everything that is not a generation**, so junk in the directory
is ignored rather than deleted or misread. Its strictness is reused as a feature in §10. Write it as
a pure function and test the rejections, not only the acceptances:

```rust
fn parse_gen(file_name: &str) -> Option<(u64 /*ms*/, usize /*len*/)>;
// rejects: wrong suffix · non-14-digit stem · missing separator · non-numeric length · the pin file
```

---

## 7. Retention: two tiers plus a ceiling

**(A) Reports arrive a day after the incident** — **(B)** both of the reference port's original loss
reports did — so retention must be designed against that latency, and "keep the newest N", which is
what everybody writes first, cannot survive it. The game writes a save far more often than the player
thinks: a periodic autosave, plus a save on every container deposit and every menu page turn. **(B)**
At a five-minute autosave cadence a flat six-deep window covers **half an hour**. A vault that has
already rotated away the generation the player needs is indistinguishable from no vault.

**(A) So: two tiers plus a hard ceiling.**

| tier | rule | why |
|---|---|---|
| recency | keep the newest **N** unconditionally | undo something that just happened |
| calendar | keep the newest generation of each of the last **D** days | survive a report that arrives late |
| ceiling | never keep more than **M**, whatever the tiers say | the listing runs inside a guest call (§12), and the calendar tier's size depends on the calendar |

**(B)** Reference tiers: N = 6, D = 6, M = 16. **(C)** Do not copy those three numbers without
re-deriving them from *your* write cadence and *your* report latency; each is the answer to a
measurement and belongs at its definition site with the symptom and the date attached.

The restore pin (§10) is held **outside** the tiers — it spends no recency seat and does not seed the
calendar tier — but it **counts toward the ceiling**, so the ceiling stays a true bound.

---

## 8. `should_vault`, and the delete exemption

**(A)** One dependency-free pure function decides whether a generation is taken. Facts in, `bool`
out, no I/O:

```rust
pub fn should_vault(
    old: &[u8],            // the pre-image, already in hand at the hook
    new: Option<&[u8]>,    // None == this is a DELETE
    last_vault_ms: u64,    // 0 == never
    now_ms: u64,
) -> bool
```

1. **Skip an empty pre-image.** Nothing to lose, and a zero-length generation is noise that §6's
   length column can only make confusing.
2. **Skip a byte-identical pre-image.** The game rewrites the same bytes constantly; each of those
   would otherwise spend a retention seat.
3. **Rate-limit overwrites.** A guest in a write loop must not turn a safety feature into a write
   amplifier inside its own storage call.
4. ★ **Exempt a DELETE from all of the above** — the rate limit *and* the identical-content
   shortcut.

> **REFUTED.** *"The rate limit is a uniform optimisation; one interval for all archival events."*
>
> **Refuted by:** the shape of the game's own write traffic. Because a save is rewritten on every
> container deposit and every page turn, a player-initiated reset — or, before it was fixed, a
> failed write that *became* a delete ([loss-mechanisms.md](loss-mechanisms.md) §5) — routinely
> landed inside the window of one of those writes, found the limit armed, and left **nothing**
> behind.
> **Why it was attractive:** the rate limit exists for a genuinely good reason, which is exactly why
> it gets applied uniformly, and why the exemption has to be written down *next to it* rather than
> in a design document.
> **Consequence if carried forward:** the one event that can never be re-taken is the one event you
> declined to archive. **A delete is not one of a series — it is the last thing that ever happens to
> that record.**

**(A)** Pin the exemption with **named** selftests, including one asserting that the
identical-content rule cannot leak into the delete arm. That arm is a one-line `if` which a
reasonable person will "simplify" later.

---

## 9. Restore

### Apply at the next launch, never live

**(A)** The record the player wants replaced may be **open**, with its contents mirrored in a
guest-heap buffer that the next write-through snapshots back to disk — so a live swap is overwritten
seconds later, or half-overwritten. `HIGH / CONFIRMED` from reading the record handle's write-through
path in full: every stream write snapshots the whole guest-side buffer. ⚠ The failure itself was
never observed, because the design avoided it; a live restore of an open record followed by one guest
save would settle it. Boot-time application is race-free and needs no guest cooperation:

1. **The dialog validates the chosen generation against what is on disk, as the request is written.**
   A stale list must not schedule a restore of a rotated-out generation, and you do not want to
   discover that after the app has closed itself.
2. **Vault the current bytes first, unconditionally.** A restore is a destructive write.
3. **Write the request file** naming the record and the generation, then **close the app**.
4. **At boot, before the guest opens any database: delete the request file *first*, then apply it.**
   ⛔ Never the other way round. A restore that crashes half-way must not re-run on every launch —
   **a restore loop is indistinguishable from the bug the feature exists to undo.**
5. **Pin the generation you restored** (§10), and restore payload and summary **together** (§5).

**(A)** A restore that silently does nothing, or half-works, is worse than no restore feature at all:
the player believes it worked and stops looking for their character.

### ★ The restore that ate the backups

> **REFUTED.** *"A restore is a read-only operation on the vault — it copies a generation in, it
> consumes nothing."*
>
> **Refuted by:** a player report, after shipping. One restore consumed **three of six** retention
> seats and, in the worst case, deleted the very generation being restored.
> **Actual mechanism — three separate costs, none obvious:** (a) the mandatory pre-restore capture
> (step 2) spends a seat; (b) the rotation that capture triggers runs over the **same directory**, so
> it can select the generation the restore is about to read; (c) the first ordinary save afterwards
> archives a **byte-identical duplicate** — and §8's identical-content rule does not save you,
> because a boot-time restore happens *before the emulator exists*, so `last_vault_ms` is still zero
> and the rate limit cannot fire either.
> **Consequence if carried forward:** the feature that exists to undo a data loss causes one, on the
> single occasion the player most needs it.

**(A) The pin is the fix, and it has to do all three jobs:** hold the restored generation outside the
retention tiers so rotation cannot select it; count it toward the ceiling so the bound survives; and
compare each new pre-image against the pinned bytes so the duplicate in (c) is declined. **(B)**
Pinned by property tests in the reference port: a 500-iteration save loop never ages the pinned
generation out, and the restore scenario costs **exactly one seat**.

---

## 10. The pin sidecar trick

**(A)** Store the pin as a **sidecar file whose name the §6 generation parser cannot match** — wrong
suffix, wrong stem length, both. That one choice makes it invisible to every enumerator you already
have: `list`, `export` and `rotate` all filter through `parse_gen`, so none of them can show the pin
to the player, copy it out, or delete it — **with no change to any of them**.

**(A)** The alternative — a marker *generation* — has to be special-cased in three places, and each
is a place where the marker can be listed as if it were a save, exported, or rotated away. Three
special cases is three chances to regress a data-safety property.

Worth generalising: **when you need out-of-band metadata in a directory that a strict parser already
guards, pick a name the parser rejects.** The guard becomes the feature.

---

## 11. Export — the only channel off an unrooted release build

**(A)** A release build is not debuggable, so the shell-as-app trick cannot read app-private internal
storage — and the records are encrypted anyway. The **only** way a player on an unrooted phone gets
their save data to you is a **deliberate copy into the app-specific external directory**. Four
constraints, all load-bearing **(A)**:

* **Player-triggered**, from a diagnostics screen. Nothing reaches external storage automatically;
  this is the one stated, bounded exception to that rule
  (`${CLAUDE_PLUGIN_ROOT}/skills/android-host/reference/storage-and-config.md` §9).
* **Ciphertext records only** — no identity, no config, no host state.
* **Design so the FILE NAMES carry the diagnostic value.** **(B)** In the reference port the
  exported files landed owner-only, so a `pull` was refused **while a listing still worked** — and
  that was enough, because §6 puts the timestamp and the length in the name. Assume you will get the
  listing and not the bytes.
* **One listing must answer three questions:** when, which slot, and in which direction the damage
  went.

```bash
adb -s "$ANDROID_SERIAL" shell ls -l /sdcard/Android/data/com.example.<name>/files/save_vault/*
```

⛔ **Never open a support instruction with "uninstall".** Records and vault are both app-private, so
an uninstall destroys the evidence and the recovery path together — and it is the one irreversible
action a helpful player or tester takes unprompted, *after* the loss, when the vault is the only copy
left. Always install over the top. It follows that **a signing-key change is a save-destroying
event**, because it forces an uninstall to update; see
`${CLAUDE_PLUGIN_ROOT}/skills/port-shipping/reference/publish-audit.md`.

---

## 12. Bounded and silent

**(A)** The vault runs on the emulator thread, **inside a guest storage call**. That fixes both its
budget and its error policy.

**Budget:** the pre-image is already in hand at the hook, so at most one small write plus one directory
listing whose length the retention ceiling caps (§7). Nothing recursive, no rescan of the storage
tree. The cost argument: the write it piggybacks on already fsyncs a kilobyte-scale record, which
dominates.

**Error policy: log every failure and swallow it. A vault that cannot write must never be the reason a
save fails.** Inverting this turns the safety feature into the exact failure it was added to prevent —
and it arrives wearing the previous bug's clothes, because in an unfixed core a failed write also
*deletes* the record ([loss-mechanisms.md](loss-mechanisms.md) §5).

**(A)** Log at **warn/error**, never debug: a release build's log filter drops debug, and a save bug is
reported hours later by someone else on a build you cannot attach to. What each line must carry is in
[policy-and-diagnostics.md](policy-and-diagnostics.md) §9.

---

## 13. The boot check, and what it must not read

**(A)** Compute one thing at startup and on the diagnostics screen: **this slot has no record, and
the vault holds generations for it.** That sentence is what turns a loss into a support action, and
it deserves an affordance the player can find.

> **REFUTED.** *"The boot check can use the record path this process already remembers."*
>
> **Refuted by:** code inspection — undertaken only after somebody noticed the notice had **never
> appeared**. The remembered path is a static populated *after* a record write, and the check's only
> caller runs before any record is written. It could not fire, ever, and there was no error to see:
> just an absence.
> **Why it was attractive:** that static genuinely is the most accurate root available *later in the
> session*, and the dependency is invisible at the call site.
> **Consequence if carried forward:** a recovery affordance nobody has seen work, in a feature whose
> whole purpose is to work exactly once, under pressure. And the case that breaks is the case that
> matters: a player opening settings **at the title screen**, before the guest has booted.

**(A)** Route the boot check and the vault listing through the **same candidate-roots helper**,
deriving its roots from the filesystem (§2). Two rules generalise out:

* **A diagnostic that answers "is something missing?" must derive its inputs from the filesystem**,
  never from state this process happens to have populated.
* **A feature never observed firing is untested**, however many tests it has. Force it once, by
  hand, on a device, against a vault you populated deliberately.

---

## 14. Property tests that make rotation safe to change

**(A) Rotation is the only part of an archival layer that can itself destroy data, so test it as
properties over pseudo-random corpora, not as examples.** Example-based tests check the cases you
thought of; the case that eats the last copy is by definition one you did not. The five properties
**(A)**:

1. The **newest** generation is never selected for deletion.
2. Returned indices are **unique, in range**, and leave no more than the ceiling behind.
3. **Convergence** — a second pass over the survivors deletes nothing.
4. A **long write loop** (**(B)** 500 iterations in the reference port) can never exceed the ceiling.
5. **With a pin**, that same loop can never age the pinned generation out.

**(A)** Use a deterministic **hand-written seeded generator** — no external crate — so the suite still
runs under plain `rustc` on any host (`${CLAUDE_PLUGIN_ROOT}/shared/selftest-convention.md`). Sweep
seeds *and* corpus sizes, and include corpora containing zero-length generations, because §8's empty
rule and §6's length column both interact with them.

Alongside the properties, one **named scenario test per real report**: *the restore scenario costs
exactly one seat*, *one generation per day survives a busy session*. A test named after the report is
the only kind still meaningful two years later.

★ **`include!`ing the real source protects against drift, not against a wrong premise.** Two shipped
bugs in the reference port were held green by tests that genuinely exercised the shipping code and
asserted the buggy answer. The rule that follows — when you find a bug, grep the tests for its
*input* — is [policy-and-diagnostics.md](policy-and-diagnostics.md) §7.

---

## 15. Symptom → cause

| symptom | cause | fix |
|---|---|---|
| A slot is gone with no archived copy, though ordinary writes are archived | The guest's "reset" maps to a whole-**database** delete (a recursive directory removal), which the per-record hook never sees | §2 — audit every deletion path; archive the directory first |
| The vault has generations, but not the one from before the incident | Flat newest-N retention, flushed by autosave and container-screen writes before the report arrived | §7 — add the calendar tier |
| A player-confirmed reset left nothing behind | The rate limit or the identical-content shortcut was applied to a delete | §8 — exempt deletes from both |
| After one restore, fewer generations remain and the restored one is gone | The pre-restore capture spends a seat, its rotation can select the generation being read, and the next save archives an identical duplicate no rate limit can suppress | §9 — pin the restored generation outside the tiers |
| A restore appears to work, then the old data returns seconds later | Applied live, over a record mirrored in a guest-heap buffer that the next write-through snapshots back | §9 — apply at next launch |
| Every launch re-runs the same restore | The request file is consumed after the copy instead of before it | §9 step 4 |
| The "missing but recoverable" notice has never once appeared | It reads a path only populated after this process writes a record; its only caller runs first | §13 — candidate-roots scan |
| Saves started failing when the vault shipped | The vault's own I/O error is propagating into the guest's write result | §12 — log and swallow |
| You cannot get a player's save off their phone | Release builds are not debuggable, so app-private internal storage is unreachable | §11 — player-triggered export |
| The exported files cannot be pulled | They are owner-only on this device | §11 — the listing alone answers when, which slot, which direction |

---

## 16. Checklist

- [ ] The hook is in the **record store**, on both `set()` and `delete()`, and archives the **pre-image**.
- [ ] Every host path that can remove a record is enumerated — including the whole-database delete and the create-mode truncate — and none is unhooked.
- [ ] The vault directory is derived from the record's own path; no config value, no remembered static.
- [ ] Records are copied **whole**, ciphertext included; restore needs no guest call and no key.
- [ ] Payload and summary are vaulted, rotated and restored **as one unit**; a lone summary is never offered.
- [ ] The prefix list is a **whitelist** judged on the name's leaf, with the settings record excluded on purpose.
- [ ] Generations are `<zero-padded ms>-<length>.bin`, and the inverse parser **rejects** everything else.
- [ ] Retention is recency + calendar + hard ceiling, each number carrying its symptom, measurement and date.
- [ ] `should_vault` is pure and **exempts a delete** from the rate limit and the identical-content shortcut, with a named test.
- [ ] Restore validates against disk at request time, vaults current bytes first, deletes the request file **before** applying, and pins the result.
- [ ] The pin is a sidecar whose name `parse_gen` rejects, outside the tiers and counted against the ceiling.
- [ ] Export is player-triggered, ciphertext only, and diagnostic **by its file names**. No support text says "uninstall".
- [ ] Every vault failure is logged at warn/error and swallowed; a vault error can never fail a save.
- [ ] Rotation has the five properties under a hand-written seeded generator, plus one named test per real report.
- [ ] The boot notice has been **seen to fire** once, by hand, on a device.
