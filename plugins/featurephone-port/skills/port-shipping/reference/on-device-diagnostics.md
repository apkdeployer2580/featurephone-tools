# On-device diagnostics

On a release build, **nothing outside the app can find out what the app decided.** The debug bridge
cannot read app-private storage, because the run-as helper refuses a non-debuggable package. So the
question "which configuration did it actually resolve, and from where?" has exactly one possible
answerer: the app.

This file specifies that reporting surface — what it must say, who it is for, what must never appear
on it, and the two escape hatches (a writable directory to configure from, and an export route to
get data back out) that make a shipping build diagnosable at all.

Tags: **(A)** generic procedure · **(B)** worked example from the reference port, re-derive it ·
**(C)** do not copy. Conventions: `${CLAUDE_PLUGIN_ROOT}/shared/evidence-standard.md`.

## Contents

- [1. Why the app is the only channel](#1-why-the-app-is-the-only-channel)
- [2. The effective-configuration line](#2-the-effective-configuration-line)
- [3. Two audiences: the screen and the log](#3-two-audiences-the-screen-and-the-log)
- [4. What never goes on the screen](#4-what-never-goes-on-the-screen)
- [5. Composing the report across the platform boundary](#5-composing-the-report-across-the-platform-boundary)
- [6. Per-stage status strings, so a report triages itself](#6-per-stage-status-strings-so-a-report-triages-itself)
- [7. The writable escape hatch](#7-the-writable-escape-hatch)
- [8. The export route, and evidence in a filename](#8-the-export-route-and-evidence-in-a-filename)
- [9. Notices the player cannot act on](#9-notices-the-player-cannot-act-on)
- [10. Never gate UI on a value you have not measured in the field](#10-never-gate-ui-on-a-value-you-have-not-measured-in-the-field)
- [11. Symptom table](#11-symptom-table)
- [12. Checklist](#12-checklist)

---

## 1. Why the app is the only channel

**(A)** On a non-debuggable package:

- app-private internal storage is unreadable over the shell;
- so is any configuration file the app wrote there;
- so the layering you designed — device file over build-time default over fallback — is invisible
  from outside, and a **stale override file silently shadows the value baked into the build**.

**(B)** That exact failure reduced a whole session to guessing: the value in the build was correct,
the app behaved as though it were not, and nothing on screen said why. The fix was a reporting
function whose rationale is written on it.

> **(A) A release build must be able to state its own effective configuration on screen.**

## 2. The effective-configuration line

**(A)** For each value that can come from more than one place, report three things:

```
<what>: <effective value>   [source: <where it came from>]   (overrode: <what it displaced>)
```

The third field is the one people leave out and the one that solves the bug. "The endpoint is X" is
not actionable; "the endpoint is X, from a device file, overriding the build-time value" names the
file to delete.

**(A)** Report the source even when nothing was overridden — `[source: built in]` is a positive
statement that no device file was found, which is exactly what you need to know.

## 3. Two audiences: the screen and the log

**(A)** They are different audiences with different needs, and conflating them is how operator
configuration ends up distributed to every player.

| | on-screen panel | boot log |
|---|---|---|
| reader | the player, who will screenshot or copy it | you, with a cable |
| contains | build identity, device facts, mode flags, per-stage status, sizes | everything on the panel **plus** resolved paths, which layer won, endpoints, and anything operational |
| lifetime | one tap away, always | until the log buffer rolls |

**(A) Keep the computation shared.** The panel and the log render from the same assembled report;
the panel just omits fields. Two independent report builders drift, and the one nobody reads drifts
first.

## 4. What never goes on the screen

> **(A) An on-device diagnostics screen has an audience, and operator configuration is not it.**

A server host, a port, a transport scheme or a certificate pin printed on that screen is
**distributed to every installed copy**, on a screen any player can open, screenshot and paste — and
it helps that reader with nothing.

> **REFUTED.** *"The endpoint belongs on the diagnostics panel; that is exactly the sort of thing a
> diagnostics panel is for."*
>
> **Refuted by** the observation that it shipped that way for six days and was then removed on the
> project owner's instruction — *is there any need to give the user that information?* Nobody
> reading the panel could act on it.
>
> **Why it was attractive:** the panel exists precisely because a stale override is invisible from
> outside, and the endpoint is the canonical example of a value that gets overridden.
>
> **Correction:** keep the computation, emit it to the **boot log** instead. Diagnosing a stale
> override is work for whoever has the cable. The panel keeps the fields the player can act on or
> report.

**(A)** Same rule for: keystore identity, account identifiers, absolute paths outside the app, and
anything that would be a finding in a publish audit —
[publish-audit.md](publish-audit.md).

## 5. Composing the report across the platform boundary

**(A)** The facts live on both sides of the JNI boundary and neither side has all of them:

| platform side only | native side only |
|---|---|
| app version and `versionCode`, OS version, device model | pipeline status, window and logical sizes, resolved mode flags, gauge levels |

**(A)** Assemble the native half, pass it up, let the platform dialog **prepend** the facts only it
knows, and give the dialog a **copy-to-clipboard** action.

> **(A) The reporter's channel is a chat message, not a log file.** A panel that cannot be copied
> gets retyped, badly, or photographed at an angle.

**(B)** Built exactly this way in response to a field report that could not be diagnosed over the
debug bridge at all.

## 6. Per-stage status strings, so a report triages itself

**(A)** Have each stage of a subsystem's bring-up write **one human-readable line** into a cell the
panel reads. Then a field report arrives pre-triaged into named classes instead of as "it does not
work".

**(B)** For an audio pipeline, four distinct strings covered every case, and each licensed exactly
one conclusion:

| status | conclusion |
|---|---|
| acquisition failed, with a count and the error | the platform refused; the retry count says whether it is still trying |
| acquisition succeeded, downstream component failed | name what is degraded **and what still works** — this is the difference between "silent" and "no music but effects play" |
| everything succeeded | the fault is elsewhere; stop investigating this subsystem |

**(A)** Include the retry count in the string. "Failed" and "failed 340 times and still trying" are
different bugs.

**(A)** And the rule that makes the retry count meaningful:

> **One transient failure to acquire a platform resource must never become a permanent session-level
> disable.** Retry with a two-stage cadence — a few fast attempts for races, then a slow patient
> loop — and **drain the pending work queue on every attempt**, so a long retry does not accumulate
> a backlog that fires all at once on success.

**(B)** Root-caused from a field report of total silence on a distributed build: the audio thread
abandoned the pipeline after a single failed device open. A cold-boot race or another app briefly
holding the device was enough to silence the game for the whole session.

## 7. The writable escape hatch

**(A)** You need somewhere an ordinary developer can drop a configuration file **on a shipping
build**. There is exactly one such place: the app-specific directory on shared external storage. The
shell is exempt from scoped storage there, while app-private internal storage is unreachable.

Two rules:

1. **Search the external location BEFORE the internal one**, so a dropped file wins.
2. **Log which path won.** Otherwise you have rebuilt the invisible-override problem in a new place.

**(B)** Applied to seven runtime switches, each with the same two-element search order and a log line
naming the winning path.

**(A)** Re-read the value at the point of use rather than caching it at boot where the switch is
something you want to flip during a session, and never gate any of this on a debug-build flag — the
entire point is that it works on the build people actually have.

## 8. The export route, and evidence in a filename

**(A)** The same externally-writable directory is the **only route back out**: a player-triggered
action that copies app-private data into it is the only way to get a save, a backup or a log off a
shipping build without root.

**(A)** Two properties to design for:

- **Exported files may still be owner-only.** In that case the *contents* are unreadable over the
  shell but the **names are not** — and that is often enough.
- **Therefore put evidence in the filename.** A name encoding a millisecond timestamp and a byte
  length dates an incident without decrypting anything.

**(B)** A save vault's filenames were `<unix_ms>-<length>.bin`. Because the listing is chronological
and carries lengths, a *length jump* identified the exact minute a save slot became a different
character — read from a directory listing, on an unrooted phone, with every file encrypted.
See `${CLAUDE_PLUGIN_ROOT}/skills/save-integrity/reference/save-vault.md`.

## 9. Notices the player cannot act on

> **(A) Do not show a recurring notice for a state the player cannot act on. Keep the computation,
> emit it to the log, and leave the action at the one entry point that is always present.**

**(B)** A boot-time toast announcing that a recovery was available shipped for a week and was removed
on the project owner's instruction — *there is no need for this.* The underlying check was kept and
now reports to the log; the restore action stayed where it already was, in the diagnostics panel.

**(A)** The reason is not tidiness: a notice that reappears every launch until somebody intervenes
trains the player to dismiss notices, including the next one that matters.

## 10. Never gate UI on a value you have not measured in the field

> **★ (A) Hiding a signal because a value you have never measured says to hide it is a defect
> generator. Publish the value as a periodic diagnostic first; wait until field data shows the value
> you predicted; only then let the interface act on it. If the value never appears, delete the
> feature.**

**(B)** A feature hid two UI elements based on a guest byte believed to mean "saving is forbidden
here". Reports of the elements vanishing were first written off as a sampling glitch. They were not:
the byte meant something else entirely. The reverse-engineering round that settled it enumerated
every write to that byte across the whole guest image — all five were in a shared list widget's
constructor and its cursor movement.

**(A)** Two transferable pieces:

- **A player's account of *when* a symptom appears and disappears is evidence, not colour.** *"I
  opened the menu, closed it, and it came back"* named the mechanism directly, because opening that
  screen was the only code path in the image that wrote the byte.
- **Enumerate every writer of a byte before you trust your reading of it.**
  → `${CLAUDE_PLUGIN_ROOT}/skills/guest-re/reference/static-toolkit.md`

## 11. Symptom table

| symptom | likely cause | where |
|---|---|---|
| the app ignores a setting you baked into the build | a stale device override file is shadowing it, invisibly | §1, §2, §7 |
| a field report cannot be diagnosed and the device is not yours | no copyable on-screen report | §3, §5 |
| "the game is completely silent" on one person's phone only | a single failed resource acquisition became a permanent disable | §6 |
| the volume keys appear to do nothing | the app has no active media stream, so the rocker lands on the ringer — route it explicitly to media | §6 |
| a UI element vanishes for some players | UI gated on an unmeasured guest value | §10 |
| a player cannot get their save off the phone | no export route; app-private storage is unreachable on release | §8 |
| your diagnostics panel shows a server address | §4 — remove it, keep it in the log | §4 |

## 12. Checklist

- [ ] The panel reports build identity, and it is the **first** thing on it.
- [ ] Every layered value reports its **source**, and what it overrode.
- [ ] The panel is copyable in one tap.
- [ ] The panel contains no endpoint, pin, key, path or account identifier — those go to the log.
- [ ] Each bring-up stage writes a status string, including a retry count where it retries.
- [ ] No transient platform failure permanently disables a subsystem for the session.
- [ ] The external config location is searched first and the winning path is logged.
- [ ] An export route exists and was tested **on a release build**, not a debug one.
- [ ] Exported filenames carry timestamp and length.
- [ ] No recurring notice exists for a state the player cannot act on.
- [ ] No UI is gated on a guest value that has not been observed in the field.
