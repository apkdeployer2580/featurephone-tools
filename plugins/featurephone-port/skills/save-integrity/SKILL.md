---
name: save-integrity
description: Protects the player's saved game in a feature-phone emulator port - the one subject where a wrong guess destroys something nobody can get back. Establishes why a guest can only ever report a slot as empty, so every "my character disappeared" report is a deletion or truncation event and never corruption; catalogues the mechanisms that delete or truncate a record (create-mode open truncating up front, non-atomic replacement, a failed write that becomes a delete, a whole-directory wipe, a destructive server reply, a guard that silently stops saving); specifies the archive-before-overwrite vault with its retention, restore and export paths; and answers which slot a write belongs to when the slot index in the record name is only a UI cursor. Use when a character or save slot vanished, a slot reads as empty, one save overwrote another, saves stopped persisting, a record write needs to be made atomic or guarded, or a save backup, restore, vault or build recall is needed.
license: MIT
---

# Save integrity

Everything that decides whether the player still has their character tomorrow. This skill exists
because the reference port destroyed real players' saves more than once, and because each cause was
in a different layer — the vendored record store, the host's file writes, a guest field that was
misread as an identity, and a server reply. No single phase skill owns them, so they are here.

Provenance tags used here and in every reference file: **(A)** generic procedure · **(B)** worked
example from the reference port, re-derive the number rather than copying it · **(C)** never copy.
`⚠` marks a claim nothing has been run against yet. Conventions:
`${CLAUDE_PLUGIN_ROOT}/shared/evidence-standard.md`.

## 0. The theorem that redirects the whole investigation

> **A guest record store can only express "empty". It cannot express "corrupt".**

A loader that returns zero does so in two cases only — the record is absent or zero-length, or it
failed its own checksum — and the summary builder that feeds the slot list allocates and fills
unconditionally, so it has no way to report a failure at all. Therefore:

**Every "my character is gone" report is a DELETION or TRUNCATION event.** Start in the write and
delete path. Do not debug the serialiser; it was not wrong.
→ [loss-mechanisms.md](reference/loss-mechanisms.md) §1

The second structural fact, immediately: there are usually **two record families per slot** — a
large payload and a tiny summary — and the slot list's occupancy decision reads only the *summary*.
Losing 32 bytes makes an intact character invisible. Vault them, restore them and redirect them as
one unit. → [loss-mechanisms.md](reference/loss-mechanisms.md) §2

## 1. The invariant

> **An occupied character record is only ever replaced by its own character.**

Everything in this skill is an attempt to make that true without also making the game unable to
save. Both failure directions cost a player a character; the second one is less obvious and was
shipped twice.

## 2. The four decisions, in order

Work them in this order. Each one hides the next, and the later ones are meaningless if an earlier
one is unsound.

| # | decision | exit criterion | where |
|---|---|---|---|
| 1 | **Make the write path survive failure.** Defer create-mode truncate to the first successful write; replace every in-place rewrite with temp + fsync + rename; disarm the pending truncate on *every* early return; propagate the store's verdict to the guest. | A forced-failure test — storage error injected mid-write — leaves the previous record byte-identical, and the guest is told the write failed. | [loss-mechanisms.md](reference/loss-mechanisms.md) §3–§6 |
| 2 | **Archive the pre-image.** Hook the record store, copy the bytes that are about to stop existing, name generations so a listing is a timeline, and give the player a restore and an export that need no key. | A deliberate overwrite leaves a recoverable generation; export produces readable files on a *release* build with no debug bridge access to app-private storage. | [save-vault.md](reference/save-vault.md) |
| 3 | **Decide whose save this is.** The slot index in the record name is a UI cursor, not an identity. Pin the slot from an *observed load*, and be sure you have found every load path. | A device trace of the reported sequence shows the pin re-established on every load, with no false positives, and a write to a drifted cursor lands in the pinned slot. | [slot-identity.md](reference/slot-identity.md) |
| 4 | **Make the answer provable.** One decision, one pure function, in one file. Property-test the destructive parts. Emit one verdict line per write. | The decision is a dependency-free function with selftests; callers only gather facts; a save bug is diagnosable from the log alone. | [policy-and-diagnostics.md](reference/policy-and-diagnostics.md) |

## 3. Reference files

| file | owns |
|---|---|
| [loss-mechanisms.md](reference/loss-mechanisms.md) | The ten mechanisms A..J, symptom → mechanism → fix → how to test, and the three theories a reporter will bring with the one-step refutation of each. |
| [save-vault.md](reference/save-vault.md) | Archive-before-overwrite: where it hooks, pre-image not post-image, generation naming as forensics, retention, `should_vault` and the delete exemption, restore, and export. |
| [slot-identity.md](reference/slot-identity.md) | Which slot a write belongs to: the cursor proof, the pin and its provenance, load signals and non-signals, why identity fields lie, Allow/Redirect/Refuse, and **the five attempts with the report that refuted each**. |
| [policy-and-diagnostics.md](reference/policy-and-diagnostics.md) | Policy as pure functions, tombstones, property and regression tests, why `include!` does not protect against a wrong premise, the log line, and recall discipline. |

## 4. The five refutations this skill exists to preserve

Read these before you design anything here. Every one was written down as fact, shipped to players,
and disproved.

1. **"Refusing is safe — the worst case is that a save does not happen."** The guest ignores the
   write's failure code, so the game reports success while nothing persists, and host-owned state
   diverges from the guest's. Prefer **Redirect** wherever ownership is provable.
   → [slot-identity.md](reference/slot-identity.md) §14
2. **"Latch the pin only while the game is in play."** The gate excluded the route players actually
   use, because one app-state value covered both the title shell and the online screens. The policy
   degraded to unconditional allow and a slot was destroyed.
   → [slot-identity.md](reference/slot-identity.md) §14
3. **"The player executing a row is an unambiguous pin trigger."** The edge it hung on fires once
   per session, so it could structurally only ever name the first slot.
   → [slot-identity.md](reference/slot-identity.md) §14
4. **"We found the load path."** There was a second one. Blocking only the first and shipping it
   destroyed the other slot. Only the record store sees them all.
   → [slot-identity.md](reference/slot-identity.md) §6
5. **"The server copy is a backup."** It is one mutable cell per account and slot, with no history;
   the first bad write is final. → [save-vault.md](reference/save-vault.md) §1

## 5. Do not

- ⛔ **Never rate-limit or skip a DELETE in the vault.** Skipping a redundant overwrite is an
  optimisation; skipping a delete is the loss you built the vault for.
- ⛔ **Never patch the guest's slot cursor.** It drives the slot-list UI. Change the *destination
  the bytes land in*, not the number the game is holding.
- ⛔ **Never add a pin source that is not an observed load.** Four of the five wrong answers were
  new pin sources that seemed like better evidence than the last one.
- ⛔ **Never let a guard silently stop saving.** A guard that only refuses converts one bug into a
  worse one; instrument it so "nothing was written this session" is loud.
- ⛔ **Never tell a player to uninstall to fix this.** On Android that deletes app-private storage —
  the character, the vault and the evidence, in one action.
- ⛔ **Never tighten policy on an identity you cannot trust.** See
  [slot-identity.md](reference/slot-identity.md) §9: a field that looks like a character name can
  be a *class* name, and a session that never loaded a character reads zero.

## 6. Cross-skill handoffs

| you need | skill |
|---|---|
| record-store open modes, the vendored storage API, database create/delete semantics | `emulator-core` → `reference/wipi-c-recipes.md` |
| where app-private files live, config layering, the host's file-writing layer | `android-host` → `reference/storage-and-config.md` |
| which guest word holds the slot index, who writes it, calling the guest's own save routines | `guest-re` → `reference/calling-guest-code.md`, `reference/recipes.md` |
| a server reply that overwrites a live character; a dual-use opcode; restore-flag defaults | `server-revival` → `reference/threat-model.md`, `reference/deploy.md` |
| recalling a build that can destroy data; why an update that will not install forces an uninstall | `port-shipping` |

## 7. Test tiers

Three tiers, and for this subject the third cannot be skipped.

1. **Pure functions** — the verdict functions and the vault's rotation, dependency-free, run with a
   standalone test binary on any host. → `${CLAUDE_PLUGIN_ROOT}/shared/selftest-convention.md`
2. **A real temporary record store** — one end-to-end test, in a single test function, because the
   pin is process-global.
3. **A device walk of the exact sequence in the report.** Every wrong answer in §4 passed tiers 1
   and 2. → [policy-and-diagnostics.md](reference/policy-and-diagnostics.md) §11
