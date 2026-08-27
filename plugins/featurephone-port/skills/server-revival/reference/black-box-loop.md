# The black-box loop: hot-reloaded replies, transcripts, and the refutation log

**The design constraint of this whole phase is iteration cost.** Rebuild → install → launch →
navigate several menus to reach the online screen is minutes per experiment. Static RE gives you a
hypothesis in seconds; testing it must not cost five minutes.

Therefore: push everything changeable to the **server**, and make the server changeable **while
the client stays connected**.

## Contents

- [The reply table](#the-reply-table)
- [Two discipline rules](#two-discipline-rules)
- [Ship it OFF by default](#ship-it-off-by-default)
- [Transcripts](#transcripts)
- [What the transcript is *for*: deriving constants](#what-the-transcript-is-for-deriving-constants)
- [Transcript safety — masking, paths, retention](#transcript-safety)
- [Scripted device runs](#scripted-device-runs)
- [★ The refutation log](#-the-refutation-log)
- [Worked refutations (B)](#worked-refutations-b)
- [★ Preserve refuted CANDIDATE fixes, not only refuted claims](#-preserve-refuted-candidate-fixes-not-only-refuted-claims)
- [When a diagnosis is refuted, label what survives](#when-a-diagnosis-is-refuted-label-what-survives)
- [Refutation without a device](#refutation-without-a-device)
- [Confidence discipline](#confidence-discipline)
- [Operational traps](#operational-traps)

---

## The reply table

**(A)** A file that maps **request bytes → reply bytes**, consulted before the protocol handler
runs.

```jsonc
{
  "// comment": "keys prefixed with // are ignored, so you can annotate experiments",
  "0101 02":     "0101",
  "053f00000000000000000000": ["053f01000000..." ],
  "*":           "1f00..."
}
```

Contract:

| property | value | why |
|---|---|---|
| key | the **request payload**, lowercase hex, whitespace stripped | payload, not frame — the framing is not what you are experimenting on |
| value | one reply hex string, **or a list** of them | some experiments need two frames; see the pacing warning in [reply-re.md](reply-re.md) |
| `"*"` | catch-all, matched last | the fastest way to answer "does *any* reply move this state?" |
| `//`-prefixed keys | ignored | keep the experiment log in the file itself |
| reload | **`_load()` runs on every lookup**, mtime-gated | this is the whole point: edit the file mid-session, the next request uses it |
| bad hex | logged and skipped, **never fatal** | a typo at 2 a.m. must not kill the session you spent five minutes reaching |

That mtime-gated reload converts protocol learning from an "APK rebuild loop" into an "edit one
line and press the button again" loop. It is the single largest productivity change in the
chapter.

**Override precedence:** a table hit **wins**, per request. Otherwise the protocol handler drives.
Not "table replaces server" and not "server replaces table" — per-request override, so you can
tune exactly one opcode on a live device while every other feature keeps working normally.

## Two discipline rules

**1. Record the silent baseline first.** Start with an **empty** table: socket alive, server mute.
Watch for several minutes and record what the client does unprompted. Only against that baseline
can a later behavioural change be *attributed* to a reply you added.

Without a baseline you will attribute to your reply: the client's own retry timer, its idle
teardown, a heartbeat-driven state change, and a UI transition that would have happened anyway.
All four look exactly like "my reply worked" or "my reply broke it".

**2. Change one thing per run.** The table makes it cheap to change five bytes at once, which
makes it cheap to learn nothing. One field per run, recorded in a `//` comment above the key.

## Ship it OFF by default

The reply table must be behind a flag that defaults to **off**, and the help text must say why:

> `--replies FILE` — request→reply override table for protocol experiments. **Off by default.**
> A `"*"` entry hijacks **every** connected user's replies, and any entry can crash a client (see
> the crash primitives in reply-re.md). Never enable on a server other people play on.

For a public deployment, consider removing the `"*"` catch-all from the production code path
entirely, keeping it only in a development build. See [threat-model.md](threat-model.md).

## Transcripts

**(A)** Append-only JSONL, one record per frame, **flushed per line** so a crash does not eat the
evidence you crashed on.

```json
{"t": 12.481, "ev": "rx", "sid": 3, "n": 7, "gap": 5.031, "total_len": 7, "payload": "010102"}
```

| field | meaning |
|---|---|
| `t` | seconds since server start — monotonic, comparable across sessions |
| `ev` | `rx` · `tx` · `connect` · `disconnect` · `desync` · `idle_timeout` · `rate_limited` |
| `sid` | session id, so interleaved clients are separable |
| `n` | frame ordinal within the session |
| `gap` | **seconds since the previous frame on this session** |
| `total_len` | as decoded from the header, so a desync is visible |
| `payload` | hex, **subject to masking — see below** |

The non-frame events matter as much as the frames: a `disconnect` with no preceding `tx` is the
"client tore down a healthy connection" symptom; an `idle_timeout` tells you your timeout is
shorter than the client's silence; a `desync` is a framing bug caught with its evidence attached.

## What the transcript is *for*: deriving constants

**★ The `gap` field is not decoration.** It is how you replace guessed constants with measured
ones.

**(B)** heartbeat gaps over n=1224 frames: **mean 5.036 s, max 6.798 s** ⇒ production idle timeout
**45 s ≈ 6.6× worst case**. That number is defensible; "60 seconds seems fine" is not.

Constants you should derive from transcripts rather than invent:

| constant | derive from |
|---|---|
| idle timeout | max observed heartbeat gap × a safety factor |
| frames/second rate limit | the largest legitimate burst (opening a list screen) plus headroom |
| sessions per IP | the number of sockets one install opens — usually **one** — plus room for carrier NAT |
| per-account row caps | what a legitimate session actually writes |
| reply pacing assumptions | whether two `tx` records ever landed in one client read |

## Transcript safety

**A transcript is credential-and-PII-complete by construction.** It contains every login frame,
every save blob, and (if you log it) every peer address. Treat it as the most dangerous file the
project produces.

1. **Default OFF.** It is a debugging tool, not telemetry.
2. **Default path OUTSIDE the source tree.** A default of `<package_dir>/transcript.jsonl` is
   exactly the mechanism by which real user data ends up committed. Default to a state directory
   outside the repo, or require the path explicitly.
3. **★ Mask by field allowlist, not by op denylist.** The reference port masked payloads for a
   *list of two known credential-bearing opcodes*. That fails **open**: the next
   credential-bearing op you add is logged raw, silently, by default. Invert it — decide per
   message *which fields are loggable* and log nothing else.
4. **Never write peer IP addresses into a transcript you might commit.** They are personal data,
   they are useless for protocol RE, and they are the field most likely to survive a careless
   `git add`. If you need them for abuse investigation, log a salted hash with a short retention.
5. **Rotation and retention.** ~1.5 MB/day/client with no rotation is a disk-full outage waiting
   for a long-running server.
6. `.gitignore` the pattern **and** keep the directory out of the tree — see
   `port-shipping/reference/publish-audit.md`. `.gitignore` prevents future additions, never past
   ones.

## Scripted device runs

**(A)** A device experiment you cannot repeat is an anecdote. Script it:

```
python "${CLAUDE_PLUGIN_ROOT}/tools/adb_drive.py" --serial $ANDROID_SERIAL \
  --script taps.txt --logcat-grep NETTRACE --screencap out.png
```

Sequence: clear logcat → force-stop the app → launch it → replay a fixed tap script with sleeps →
screencap → grep the host's own network trace tag out of logcat.

Two rules learned the hard way:

* **Coordinates are data, not code.** They depend on the device resolution *and* on your own
  layout arithmetic. Derive them from your layout code and confirm them once against a screencap;
  keep them in a file the script takes as an argument.
* **Correlate by the host's log tag, not by wall clock.** Put a single distinctive tag on every
  network log line in the host so one `grep` reconstructs the session, and log the **header only**
  (see [socket-layer.md](socket-layer.md)) — logcat is not a private channel.

## ★ The refutation log

**The single most valuable artifact of this whole chapter.** Static RE gets the *shapes*; only the
device settles *semantics and error behaviour*. **Budget at least one refutation per feature** —
if a feature went from static claim to working with zero corrections, you probably have not tested
its error paths.

Format, per entry: **the static claim, the device reality, the date, and where the correction now
lives.** Keep the refuted claim **visible**. Deleting it means the next person re-derives it, and
re-derives the same wrong conclusion, because the disassembly still reads that way.

```markdown
### ★ CORRECTION 2026-07-23 — "an empty notice body is the minimal clean reply"
CLAIMED (static, MED/INFERRED): body may be empty; the field is optional.
DEVICE:  crash, `Invalid memory access; address: 0`, at the paged text-box builder.
WHY:     the builder walks a page list that an empty body leaves null.
NOW:     the encoder substitutes a single space; conformance assertion `enc_notice non-empty`.
```

## Worked refutations (B)

Nine real ones from the reference port. They are here as **shapes of error**, not as facts about
your title — read them for the *kind* of mistake each represents. One of them is a refutation that
was itself later refuted, which is the most useful row in the table.

| static claim | device reality | the lesson |
|---|---|---|
| "the minimal clean reply is an empty notice body" | **crash at address 0** — the page builder needs non-empty text | "smallest legal frame" ≠ "smallest frame this handler tolerates" |
| "restore sends a 20-byte compact seed" | **the full record, verbatim** — the "seed" was an artifact of a disassembly that stopped at the first sub-loop | read the *whole* loop nest before declaring a size |
| "this routine opens a secondary socket" | it is a **paged text-box builder**; no secondary socket exists | a function near network code is not network code |
| "the backup ack is a toast" | the toast made the client **cleanly close the socket**; the correct ack is a commit-ack, after which the client sends its next request and advances | dismissing a modal ≠ advancing a state machine |
| "this item category contains 16 entries" | those bytes were **ciphertext noise** in an encrypted resource member; decrypted, the category is empty | never parse an encrypted member as if it were plaintext |
| "the login sequencer force-sends a restore for slot 0" | ~~never observed on a real client~~ — **it does; the login's sub-request fork is chosen by a session-state byte, and one of its three branches is the restore opcode** (see the correction below) | absence from a short capture is not evidence a path does not run |
| "a 16-entry array means expansion is impossible" | 16 is **one page**; capacity is a byte the server writes | a client array bound is not a feature bound |
| "padding a short list reply blanks the client's stale rows" | storage filled with **one item the player never owned** — an all-zero row is a valid item identifier | "the handler memsets that array" proves the array is dirty, **not** that stale rows are drawn |
| "writing zero into a row's count makes the row not exist" | a **visible, pickable, eternally mergeable ghost row** — dug to the end and dropped | check the client's *painter* and its *merge scanner* before choosing any sentinel |

> **REFUTED — a refutation that was itself refuted.** *Previously recorded here as a device
> finding:* the login sequencer's forced save-restore for the first slot is a code path that exists
> and never runs; it was **never observed on a real client**, so the working discriminator for a
> genuine restore is *"did **this connection** upload a backup?"*.
> **Refuted by:** production server logs. The login **does** send that frame, and it is **byte
> identical** to a user-initiated restore — the same seven bytes, on the same connection, annotated
> in the log once as each. Live characters were overwritten as a result, because the client's restore
> handler has no occupancy check.
> **Why the wrong reading was attractive:** it was an honest device observation. The capture was
> short, and the login picks between three different sub-requests from a session-state byte — the
> captures simply took a different branch, and the branch that emits this one names whatever slot
> the client is about to use (in the production log, **not** the first). "I watched and it did not
> happen" is exactly what a conditional path looks like from outside its condition.
> **Actual mechanism and correction:** the discriminator is not "did this connection upload a
> backup?" but **has the login finalised yet?** — and even that is not sufficient on its own, because
> **no server can tell the two senders apart**. The destructive request must additionally be
> neutralised in the host's own **send** path, by substituting a benign sibling request when the
> host's record store says the slot is occupied. (The *shape* recommended in
> [deploy.md](deploy.md) — prefer a state-derived guard to a flag — is still right in general. This
> particular state-derived guard is not, because the state it reads is on the wrong side of the
> wire.) See [reply-re.md](reply-re.md)'s save-restore row,
> [refusals-and-holds.md](refusals-and-holds.md) §9 and §11, and
> `${CLAUDE_PLUGIN_ROOT}/skills/save-integrity/reference/loss-mechanisms.md` §8.
> **Consequence if carried forward:** a server built on that discriminator answers the login's copy
> and overwrites a live character, and the report arrives as *"story, equipment and items all
> reset"* — with nothing wrong-looking in the log.

**★ The transferable rule: `⚠ never observed` is a note about your capture, not about the client.**
Write it as *"not observed in n sessions, none of which took the login fork that emits it"* — name
the branch you failed to exercise, not a property of your account — and it stays honest.
`${CLAUDE_PLUGIN_ROOT}/shared/investigation-discipline.md` §7 is the general form.

## ★ Preserve refuted CANDIDATE fixes, not only refuted claims

**(A)** The refutation log usually collects wrong *claims*. It should also collect **fixes you
designed, investigated to the end, and did not ship** — with the facts that killed them. A dropped
candidate is invisible in the diff, so without a note the next person (usually you) re-invents it,
re-investigates it, and pays for it twice. Record: the candidate, the four-or-so facts that killed
it, and **what shipped instead**.

**(B) The worked one.** To stop a client-side duplicate delivery, the candidate was to write **zero**
into the delivered row's count field so the row would "not exist". Four facts killed it, all read out
of the client:

1. the grant routine **claims the destination slot before it reads the count**, so the row is
   allocated whatever the count says;
2. the grid painter prints the number with a **bare integer format and no zero gate**, so a zero row
   is drawn like any other;
3. the merge scanner filters only on *"not nearly full"*, so a zero row becomes an **eternal merge
   target**;
4. the only value the client actually special-cases is **minus one**, and only to omit an entry from
   one acquisition list.

Net effect of the candidate: a permanent, visible, pickable ghost row. **What shipped instead** was
to stop the client's delivery leg from being sent at all
([client-fidelity.md](client-fidelity.md) §7).

**★ And record the reading error that made it look right.** The same investigation had misread a
nearby load as "an early return when the count is zero". The base register there is the **received
packet**, so that byte is the reply **status**, not a count — and that correction is the premise the
shipped fix stands on. Register provenance is worth stating explicitly whenever an offset is quoted:
*offset into what?*

## When a diagnosis is refuted, label what survives

**(A)** When the cause turns out to be somewhere else, you are left holding changes made under the
old theory. Do not silently keep them and do not reflexively revert them. **Classify each one in the
same note as the refutation**: *kept as hygiene* · *reverted* · *superseded by the real fix*.
Unlabelled survivors become folklore — the next person cites the leftover as the cure and the real
cause stays unfound.

**(B)** The reference port raised a server idle budget to fix "the app hangs after returning from
the background". Production logs then showed the connections being reset by the **platform** within
seconds, far inside the raised budget: the change could not have been the fix. It was **kept and
explicitly relabelled** *"correct hygiene, not the fix for this bug"* — and **paired with
keep-alive**, because a raised idle budget without keep-alive just leaks file descriptors for longer.
The real cause and its repair are in [socket-layer.md](socket-layer.md).

## Refutation without a device

**(A)** A device is the strongest oracle, not the only one. Two techniques refute claims with nothing
plugged in:

* **Adversarial re-derivation.** Take a shipped claim and re-derive it from the artifact *as if
  trying to break it* — read the client's actual routine rather than your model of it, and look for
  the input that separates the two. The most expensive class of bug in an in-process engine (the host
  authorising a quantity the client then destroys) was found this way, from disassembly plus a
  re-derivation against the engine already in production.
* **Settle a disagreement arithmetically rather than by vote.** Where two readings of the same
  instruction disagree — common when a call's target arrives through a literal pool and a register
  veneer, so no branch-and-link call graph exists at all — compute the target address by hand. The
  arithmetic is the tiebreaker, and it is cheap. Full treatment:
  `${CLAUDE_PLUGIN_ROOT}/shared/investigation-discipline.md` §8.

Both belong in the refutation log with the same format as a device finding; only the **CONFIRMED /
INFERRED** axis changes.

## Confidence discipline

Copy this from the reference port; it is the practice that makes documentation survive being
wrong. Every protocol claim carries:

* **HIGH / MED / LOW** — how strong the derivation is;
* **CONFIRMED / INFERRED** — whether a device has run it;
* a **⚠** that stays until it is device-confirmed;
* corrections written as `★ CORRECTION` blocks that **keep the refuted claim visible**.

Full convention: `${CLAUDE_PLUGIN_ROOT}/shared/evidence-standard.md`.

⚠ And keep the *counts* honest: the reference port's own docs cited a test count three milestones
out of date (93 when the suite had 134). If a document states a number that a command prints, make
the command the source of truth and re-run it before you cite it.

## Operational traps

* **A stale listener makes your new configuration look like it is not taking effect.** Killing the
  server in a way that leaves the port bound means your next start fails to bind — and if the bind
  failure is non-fatal (it must not be; see [server-architecture.md](server-architecture.md)), you
  get a process that looks healthy and serves nothing. Always verify the PID you are talking to.
* **The device caches nothing, but your own host config layer does not either** — the destination
  is re-read on every connect ([socket-layer.md](socket-layer.md)). If a config change appears not
  to apply, suspect the stale listener before the config.
* **One experiment per connection where you can.** Some client state only resets on reconnect, and
  a table edit mid-session can leave the client in a state no real sequence produces.
