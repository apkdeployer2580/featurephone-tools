# The 20-chapter pipeline

Every chapter states an **entry state** (what must already be true), an **exit criterion** (a
thing you can observe, not a feeling), and the **owning skill**. Do not advance without the exit
criterion. Where a chapter is commonly failed, the failure is written out so you can recognise it
before you spend a day on it.

Provenance tags: **(A)** generic procedure · **(B)** worked example from the reference port ·
**(C)** do not copy. See `${CLAUDE_PLUGIN_ROOT}/shared/evidence-standard.md`.

## Contents

- [At a glance](#at-a-glance)
- [Phase I — get a file the emulator will load (0–2)](#phase-i--get-a-file-the-emulator-will-load-02)
- [Phase II — get it running headless (3–6)](#phase-ii--get-it-running-headless-36)
- [Phase III — understand the guest (7–9)](#phase-iii--understand-the-guest-79)
- [Phase IV — make it a phone game (10–12)](#phase-iv--make-it-a-phone-game-1012)
- [Phase V — change the guest, prove it (13–14)](#phase-v--change-the-guest-prove-it-1314)
- [Phase VI — the network chapters, optional (15–17)](#phase-vi--the-network-chapters-optional-1517)
- [Phase VII — ship (18–19)](#phase-vii--ship-1819)
- [Re-entry points](#re-entry-points)
- [What changes for a JVM-bytecode guest](#what-changes-for-a-jvm-bytecode-guest)
- [What changes for an offline title](#what-changes-for-an-offline-title)
- [The five gates you must not skip](#the-five-gates-you-must-not-skip)

---

## At a glance

| # | Chapter | Owner | Exit criterion, in one line |
|---|---|---|---|
| 0 | Decide and scope | `port-triage` | Platform named, dump in hand, non-redistributable list written down |
| 1 | Classify the dump | `archive-prep` | Classifier prints platform + descriptor + jar name + native member with a BSS value |
| 2 | Repair and prepare the archive | `archive-prep` | Zero CR bytes, names agree, private saves stripped, survivors byte-identical |
| 3 | Scaffold and vendor the core | `port-shipping` → `emulator-core` | Host crate and probe crate both `cargo check`; upstream base commit recorded |
| 4 | First headless boot, virtual clock | `emulator-core` | A dumped frame reports **non-black > 0** |
| 5 | Walk the guest forward | `emulator-core` | No fatal unimplemented stub across N minutes of scripted input |
| 6 | Core hardening batch | `emulator-core` | 30k frames, no allocation failure, heap LIVE flat |
| 7 | Pin the address model | `guest-re` | Four constants in one `target.toml`, delta validated corpus-wide |
| 8 | String index and feature inventory | `guest-re` | Committed offset↔text listing; the online-only feature list |
| 9 | Map live state, prove offsets | `guest-re` | Every offset you will build on has **two independent derivations** |
| 10 | First device build and run | `port-shipping` | A debug APK installs and renders on a phone |
| 11 | Input, screen, storage on device | `android-host` | Playable with defaults; saves survive a kill; DB layout matches the probe |
| 12 | Touch HUD and layout editor | `android-host` | Controls placeable and persisted; resolver invariant holds |
| 13 | Host↔guest reads, patches, calls | `guest-re` + `android-host` | An idempotent patch; one guest call chain completes on device |
| 14 | Reimplement widgets, verify | `android-host` | Probe reports **0 mismatched pixels** on ≥2 game states |
| 15 | Socket layer and connect redirect | `server-revival` | Client connects to you; a mute server causes no teardown |
| 16 | Protocol RE | `server-revival` | Framing derived three ways; ≥1 device refutation recorded |
| 17 | Replacement server, tests, deploy | `server-revival` | Conformance suite passes; service survives restart and network reset |
| 18 | Release build, signing, distribution | `port-shipping` | Signed APK, incrementing versionCode, config verified **inside the .so** |
| 19 | Publish audit | `port-shipping` | Audit clean across **all refs**; licences present; no content, key or credential |

---

## Phase I — get a file the emulator will load (0–2)

### Chapter 0 — Decide and scope

- **Owner:** `port-triage`
- **Entry:** the user wants a native app and has, or is about to have, a dump they are entitled
  to possess.
- **Do:** classify the platform; decide AOT-ARM vs JVM bytecode; decide whether the network
  chapters apply; write down, in the repo, the list of things this project will never
  redistribute.
- **Exit:** you can name the platform, the dump is on disk, and the non-redistributable list
  exists as a file.
- **Trap (A):** skipping the legality note now means discovering at chapter 19 that licensed
  bytes are in git history. That is a history rewrite across all refs, not a `git rm`.

### Chapter 1 — Classify the dump

- **Owner:** `archive-prep`
- **Entry:** a file, of unknown shape.
- **Do:** `python "${CLAUDE_PLUGIN_ROOT}/tools/classify_archive.py" <archive> --json`.
- **Exit:** it prints a platform, the descriptor keys, the inner jar name, and the native member
  name with a parsed BSS value (AOT platforms), or a MIDlet manifest (J2ME).
- **Fails like:** the emulator prints a clean `not a <platform> archive` at load time — a load
  failure, not a panic. Clean message ⇒ you are in chapter 1, not chapter 4.

### Chapter 2 — Repair and prepare the archive

- **Owner:** `archive-prep`
- **Entry:** platform known.
- **Exit, all four:**
  1. the app descriptor contains **zero CR bytes**;
  2. the AID equals the inner jar's basename exactly;
  3. the native member's name parses to a BSS integer;
  4. no private save entries remain (`P/…` and anything shaped like a save record), and every
     surviving member is **byte-identical** to the source.
- **Fails like (B, reproduced):** `loaded` prints and *then* the Java runtime panics with
  `NotFound` — the descriptor had CRLF, so the AID carried a trailing `\r` and the jar lookup
  missed by one byte. Also: `Main class not found`; a null unwrap out of `loadClass` (wrong
  main-class key); `Invalid bss_size in filename` (native member renamed).
- **Trap (A):** repacking with a different zip writer legitimately changes the archive's size.
  **Size is not a validity check** — compare member by member.
- **Refutation carried (A):** bundled private save entries do **not** seed a new user's database.
  Packaged-database reads resolve through the classloader (jar resources) while those entries
  land in the virtual filesystem — two different namespaces. Strip them because they are somebody
  else's personal data, not because they seed.

---

## Phase II — get it running headless (3–6)

### Chapter 3 — Scaffold the project, vendor the core

- **Owner:** `port-shipping` (scaffold) → `emulator-core` (vendoring)
- **Entry:** a prepared archive.
- **Do:** generate the project from the template with every parameter substituted; vendor the
  emulator fork as a plain directory redirected by `[patch]` at the workspace root.
- **Exit:** `cargo check` passes for the host crate **and** the headless probe crate, and
  `vendor/UPSTREAM.txt` records repo URL + base commit SHA + date.
- **Trap (A):** without the recorded base commit the fork can never be diffed against upstream or
  rebased onto a newer one. Copy-vendoring with no provenance is a one-way door.
- **Trap (A):** the `[patch]` must be repeated **inside** the vendored workspace too, or tests run
  from within it exercise a different interpreter than the app ships.

### Chapter 4 — First headless boot with a virtual clock

- **Owner:** `emulator-core`
- **Entry:** probe crate builds; archive prepared.
- **Exit:** the probe prints `loaded`, then a periodic work gauge, and a dumped frame reports
  **non-black > 0**.
- **Fails like (A):** every frame is black forever with no error at all. The guest arms a
  platform timer and idles; a wall-clock `now()` in a loop that ticks thousands of times per
  wall-second never lets a timer expire. Drive `now()` from the frame counter instead.
- **Side benefit (A):** a frame-driven clock makes runs bit-reproducible, so a scripted key
  sequence lands at the same game state every time.
- **(A) Automate the check:** assert `non-black > 0` in CI. Never eyeball a PNG for this.

### Chapter 5 — Walk the guest forward

- **Owner:** `emulator-core`
- **Entry:** something renders.
- **Do:** script input past the title screen; every fatal unimplemented stub you hit becomes a
  **logging no-op that returns a plausible value**, so the guest keeps walking and the whole call
  sequence lands in the log at once.
- **Exit:** no fatal unimplemented stub across the first N minutes of scripted input, and every
  "unknown interface" warning is understood rather than ignored.
- **Fails like:** frozen screen plus exactly one log line naming a table id and slot. That single
  line *is* the workflow — it tells you which function to implement.
- **Trap (A):** an interface table shorter than the slot the guest indexes reads past the
  allocation as zero and is **branched to**. That surfaces as a fault at address 0, not as a
  clean error. Pad the table.

### Chapter 6 — Apply the core hardening batch

- **Owner:** `emulator-core`
- **Entry:** the game plays headless for minutes.
- **Exit:** 30k headless frames with no allocation failure; the heap gauge's LIVE count flat in
  steady state; the vendored crates' own tests pass.
- **Fails like (A), all four are emulator defects rather than title bugs:** progressive slowdown
  then `AllocationFailure` with free memory plentiful (a guest list allocator that never
  coalesces); steady-state stutter with a header-bytes counter climbing into GB (a bitmap
  allocator scanned instead of indexed); SIGSEGV with "stack pointer is not in a rw map" and two
  addresses alternating for hundreds of frames (reporting an allocation failure by allocating);
  stutter proportional to how much is on screen (full-framebuffer round-trip per draw call).

---

## Phase III — understand the guest (7–9)

### Chapter 7 — Pin the address model

- **Owner:** `guest-re`
- **Entry:** the guest boots and plays headless.
- **Exit:** four constants written into one `target.toml` — image base, BSS size, static base,
  relocation delta — with the delta **validated corpus-wide**, not on five hand-picked strings.
- **How each is found (A):** image base from the loader crate's `load(data, BASE, …)` call; BSS
  from the native member's filename; the static base from a register dump, cross-checked against
  the entry thunk and against a static-area dump that decodes cleanly; the delta by intersecting
  `{target − word}` sets across ≥5 known string offsets, then scoring what fraction of *all*
  string candidates the delta explains.
- **⚠ Trap (A):** determine the delta's **unit** before adopting it. `word + delta` may land in
  `[0, filesize)` (a file offset) or in `[image_base, image_base + filesize)` (a guest address).
  Get it wrong and every downstream tool lies consistently.

### Chapter 8 — Build the string index and feature inventory

- **Owner:** `guest-re`
- **Exit:** a committed offset↔text listing in the title's own encoding; the id-consuming text
  accessor identified; and a written list of features that only exist when a server answers.
- **How (A):** dump strings by byte-class regex for the encoding; find the accessor by ranking
  callees on **distinct-id density** (the real one collects a large dense id set, accidental
  neighbours collect two or three); chain a user-visible string to its code sites.
- **Why it matters:** the string dump *is* the feature inventory, and it is what decides §4 of the
  router — whether chapters 15–17 apply at all.

### Chapter 9 — Map live state and prove offsets

- **Owner:** `guest-re`
- **Exit:** the god-object pointer resolved, and **every offset you intend to build on has two
  independent derivations**.
- **What counts as a derivation (A):** a clamp read out of the setter; the same number derived
  from the game's own rules; a differential RAM dump before/after one in-game change; an
  enumeration of every code site that materialises the offset, each semantically consistent.
  Two agreeing derivations ≈ proof. One is a hypothesis.
- **Trap (A):** a struct offset has **no single encoding**. Enumerate the compiler's idioms
  (small-immediate-then-shift with scheduling in between, and PC-relative literal loads) or you
  will "prove" a live field unused.

---

## Phase IV — make it a phone game (10–12)

### Chapter 10 — First device build and run

- **Owner:** `port-shipping`
- **Exit:** a debug APK installs and shows the game rendering on a real phone.
- **Fails like:** `UnsatisfiedLinkError` at launch (the three native-library names disagree); a
  duplicate-class error from transitive Kotlin stdlib versions; a ~250 MB APK (the strip stage
  was skipped); a black screen with no crash (the archive asset is missing — the build succeeds
  without it unless you add a fail-fast check).

### Chapter 11 — Input, screen and storage on device

- **Owner:** `android-host`
- **Exit:** you can play with the default control set; saves survive an app kill; and the on-disk
  database layout is identical to the probe's, so a device save drops straight into headless runs.
- **Trap (A):** the first `resumed` can report the pre-rotation window size. Derive the logical
  size from the (long, short) sides plus the persisted orientation preference, never from
  `width > height`.
- **Trap (A):** storage writes must be synchronous — "on disk by the time the call returns" is
  what makes save-then-kill safe.

### Chapter 12 — Touch HUD and layout editor

- **Owner:** `android-host`
- **Exit:** controls are placeable, persisted, and the resolver's invariant holds — no control
  silently overlaps an obstacle, because obstacles are hit-tested before the button list, so an
  overlap means the control quietly does something else.
- **Trap (A):** store position as a **window fraction** and size as a **percent of a built-in
  size that is a pure function of window size**. Never pixels, never guest pixels — a layout must
  survive rotation, another device and a zoom change.
- **Trap (A):** the editor's acceptance predicate must **be** the resolver, not a lookalike, or
  it accepts placements the resolver silently drops.

---

## Phase V — change the guest, prove it (13–14)

### Chapter 13 — Host↔guest reads, patches and calls

- **Owner:** `guest-re` (what and where) + `android-host` (when and how it is wired)
- **Exit, both:** a read-verify-then-write patch that is idempotent across New Game / Continue /
  death / map change; **and** one host-initiated guest call chain that completes on a device
  without a JVM panic.
- **Trap (A):** the first host-originated guest call panics inside the JVM if the spawned task is
  not attached as a JVM thread. Wrap every host→guest call in an RAII attach/detach guard before
  shipping any such feature.
- **Trap (A):** a jump-table arm is **not** a function. It borrows the parent's stack frame and
  exits through the parent's epilogue; entering it directly pops garbage into the program counter.
  Require the target to have its own prologue/epilogue and its own static-base setup.
- **Trap (A):** never pin a measured payload length as an invariant. Content-dependent lengths
  fire the assertion on the first real use. Assert a band.

### Chapter 14 — Reimplement guest widgets, differentially verify

- **Owner:** `android-host`
- **Exit:** the headless probe reports **0 mismatched pixels** between the *shipping* host
  renderer and the guest's own buffer, on at least two different game states.
- **How (A):** put the renderer in a dependency-free file that both the app and the probe
  `include!`, so the diff exercises the code that ships. A check that exercises a copy proves
  nothing. Quantise your output through the same pixel format before comparing, so the comparison
  is like-for-like.
- **Why reimplement instead of screen-scraping (A):** driving the guest's own window open and
  shut behind a frozen screen needs input stashing, replay and change debouncing — and that state
  machine is where the user-visible bugs come from.

---

## Phase VI — the network chapters, optional (15–17)

Skip all three if §4 of the router found no server-only strings.

### Chapter 15 — Socket layer and connect redirect

- **Owner:** `server-revival`
- **Exit:** the client connects to *your* address, and a **mute** server does not cause a
  teardown. Record that silent baseline before you reply to anything — otherwise you cannot
  attribute any later behaviour change to the reply you added.
- **Trap (A):** never patch the guest's address. You own the emulator's socket layer; redirect
  `connect` there and make the destination runtime-configurable from day one.
- **Trap (A):** three host bugs that impersonate protocol bugs — a stubbed read-callback (the
  client never reads your replies); `recv` returning `0` instead of the platform's no-data
  sentinel (the guest tears down a healthy connection); `recv` clamped to what the guest *asked*
  for rather than what it actually allocated (a heap overrun the original server never triggered).

### Chapter 16 — Protocol RE

- **Owner:** `server-revival`
- **Exit, all four:** framing derived **three independent ways** (receive pump, captured bytes,
  send-side serializer); a request catalogue that reproduces at least two captured packets; the
  one reply that clears the client's blocking state identified; and **≥1 device refutation
  recorded**, with the refuted claim left visible.
- **Trap (A):** enumerate requests by anchoring on the serializer — every branch to the flush
  primitive is one request — and **reset the accumulated template at every branch**, so you emit
  omissions rather than fabrications.
- **Trap (A):** any fixed-size array the client fills from your reply without clamping makes its
  bound a **server obligation**. Enforce it once, in the encoder.

### Chapter 17 — Replacement server, tests, deploy

- **Owner:** `server-revival`
- **Exit:** the conformance suite passes; an exploit-as-test documents the threat model by
  executing it; and the service survives a process restart and a network reset.
- **Trap (A):** the deployment docs are part of the security surface. A destructive flag whose
  documented default disagrees with the code's default is a data-loss incident waiting for an
  operator to read the wrong file.

---

## Phase VII — ship (18–19)

### Chapter 18 — Release build, signing, distribution

- **Owner:** `port-shipping`
- **Exit:** a signed APK whose certificate fingerprint you have published next to it, whose
  versionCode increments automatically, and whose build-time-baked configuration is **verified
  inside the produced `.so`** rather than trusted from the build script.
- **Trap (A):** `option_env!`-style constants resolve at compile time. A shell that forgets to
  export the variable produces no error — it produces a shipped build silently pointing at
  localhost. Scan the artifact for the wanted string; scanning for the *fallback* string proves
  nothing, because the fallback is always present in the binary.
- **Trap (A):** the output filename is the answer to "can anyone install this?" — an unsigned
  release build succeeds and is named accordingly. Do not ignore it.

### Chapter 19 — Publish audit

- **Owner:** `port-shipping`
- **Exit:** `publish_audit.py` clean across **all refs**, a `LICENSE` and `THIRD_PARTY_NOTICES`
  exist, and no game content, extracted key or credential is in the tree **or in history**.
- **Trap (A):** `.gitignore` prevents future additions, never past ones — and a history purge is
  undone by the backup branch you kept "just in case". Audit every ref and every reflog.

---

## Re-entry points

The pipeline is a first pass, not a life cycle. Once a port is alive, work loops:

| loop | why it loops |
|---|---|
| **7 ↔ 9 ↔ 13** | Each new guest-side feature needs a new offset (9), sometimes a new constant in the address model (7), then a patch or call proved on device (13). Re-enter at 9. |
| **12 ↔ 14** | Every new host-drawn widget is placed (12) and then differentially verified against the guest (14). |
| **2** | Repacking in-jar text or tables, months later, in isolation. Nothing else re-enters with it. |
| **16 ↔ 17** | Black-box protocol learning: edit a reply, watch the live client, encode the finding as a conformance assertion. |
| **5 → 6** | A newly reached area exercises an API slot nobody hit before. |

Before answering "where do I start", ask which exit criteria already hold and enter at the first
one that does not.

## What changes for a JVM-bytecode guest

| chapter | change |
|---|---|
| 4–6 | Unchanged. The clock, the stubs, the allocator and the graphics path are the emulator's, not the guest's. |
| 7 | Mostly void. There is no image base, no BSS-in-a-filename, no RWPI static base and no relocation delta. Record the class path and the entry MIDlet instead. |
| 8 | Same questions, different tool: string constants live in the constant pool. |
| 9 | Field identity is a name, not an offset — much easier. Keep the two-derivation rule for *semantics*. |
| 13 | Patching is bytecode or method interception, not Thumb halfwords. The idempotence and read-verify rules still apply. |
| 14–19 | Unchanged. |

## What changes for an offline title

Chapters 15–17 vanish. So do: the socket layer, TLS and pinning, the account store, the transfer
code, and the entire threat-model section of the publish audit. Chapter 18's config-baking check
still applies to whatever else you bake in.

## The five gates you must not skip

1. **Chapter 4** — a frame that reports non-black. Everything after it is guesswork otherwise.
2. **Chapter 7** — a corpus-validated relocation delta. Five agreeing hand-picked strings is a
   coincidence generator.
3. **Chapter 9** — two independent derivations per offset, before any code depends on it.
4. **Chapter 14** — zero mismatched pixels, from a diff of the code that actually ships.
5. **Chapter 19** — the audit across all refs, before the first push to a public remote.
