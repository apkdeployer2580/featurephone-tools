# Fault dictionary

The plugin's index of pain. Every failure the reference port hit, merged across all phases, keyed
by the text a user would actually search for.

**How to use it.** If you have a literal message, start at [§1](#1-verbatim-messages). If you have a
behaviour ("it stutters", "it works on my desk only"), go to the matching section in
[§2](#2-behavioural-symptoms). Each row names the skill that owns the fix — invoke that skill and
it will route you to the reference file with the mechanism and the repair.

Owning skills: `archive-prep` · `emulator-core` · `guest-re` · `android-host` · `server-revival` ·
`port-shipping` · `port-triage`.

Messages are quoted the way the reference implementation emits them. Your fork's wording may differ;
match on the distinctive fragment, not the whole line.

## Contents

- [1. Verbatim messages](#1-verbatim-messages)
- [2. Behavioural symptoms](#2-behavioural-symptoms)
  - [2.1 Archive and first boot](#21-archive-and-first-boot)
  - [2.2 Rendering and frame budget](#22-rendering-and-frame-budget)
  - [2.3 Memory, allocator and long sessions](#23-memory-allocator-and-long-sessions)
  - [2.4 Host to guest: reads, patches and calls](#24-host-to-guest-reads-patches-and-calls)
  - [2.5 Input, HUD and on-device UI](#25-input-hud-and-on-device-ui)
  - [2.6 Text and glyphs](#26-text-and-glyphs)
  - [2.7 Network and replacement server](#27-network-and-replacement-server)
  - [2.8 Build, sign, install](#28-build-sign-install)
  - [2.9 Publish and secrets](#29-publish-and-secrets)
- [3. Faults that look like a different phase's fault](#3-faults-that-look-like-a-different-phases-fault)

---

## 1. Verbatim messages

| message | what it actually means | skill |
|---|---|---|
| `AllocationFailure` after 20–40 minutes of play, with plenty of free heap | The guest heap's list allocator never coalesces adjacent free blocks, so the block count rises monotonically and the largest contiguous free block shrinks toward zero. The progressive slowdown before it is the same cause — every free-list walk gets longer. Fix: coalesce lazily **in the walk**, not in `free` (the chain is singly linked forward, so `free` cannot reach its predecessor). | `emulator-core` |
| `"App not installed"` / the update is refused on a phone that already has the app | Either `versionCode` is not strictly greater than the installed one, or the signing certificate changed. The second is unrecoverable without an uninstall, which deletes the player's saves. | `port-shipping` |
| Output is `app-release-unsigned.apk` instead of `app-release.apk` | No complete signing configuration resolved, so the build succeeded and produced something nobody else can install. The filename *is* the diagnostic — treat any `*unsigned*` output as a build failure. | `port-shipping` |
| `called 'Option::unwrap()' on a 'None' value` in the JVM value layer, during boot | `loadClass` returned null: the descriptor's main-class name is wrong, misspelled, or not a class the guest's own class resolver knows. | `archive-prep` |
| `called 'Result::unwrap()' on an 'Err' value: NotFound` in the Java `io/file` layer, **printed after `loaded`** | The loader tried to open `<AID>.jar` and it is not in the virtual filesystem. Two causes, both in the archive: the descriptor has CR bytes (`AID:XXXX\r` composes `XXXX\r.jar`), or the AID simply does not match the jar's basename. | `archive-prep` |
| `Invalid bss_size in filename client.bin: cannot parse integer from empty string` | Somebody renamed the native member and stripped its numeric suffix. That suffix is the BSS size; it exists nowhere else. | `archive-prep` |
| `client.bin* not found in jar` | No jar member matches the platform's native-image name prefix. Either the wrong platform was detected, or this is a bytecode title (check for `.class` files). | `archive-prep` / `port-triage` |
| `Duplicate class kotlin.jvm.internal.…` at the Gradle stage | Version skew between the game-activity artifact's Kotlin split stdlib and the one your support library pulls. Force a single stdlib version in a resolution strategy. | `port-shipping` |
| `entry <hash>: hash matched but produced 0 patches/hooks` | The emulator's md5-keyed binary-patch table has an entry for this guest image but none of its patterns matched — the table is stale relative to the binary. Fires the moment you repack a guest binary and its md5 moves. A hash-keyed entry that yields nothing is fatal; a generic hash-less entry only warns. | `emulator-core` |
| `failed to add native library …dll_imports.lib: file not found` | Cross-compiling from a `windows-gnu` host. rustc chooses the GNU vs LLVM `dlltool` flag set from the tool's **file name**, so a renamed `llvm-dlltool` gets a GNU-only flag it rejects, writes no import library, and the error names the missing file rather than the cause. Needs an argv-filtering shim. Does not exist on Linux, macOS or `windows-msvc`. | `port-shipping` |
| `Fatal signal 11 (SIGSEGV), code 2 (SEGV_ACCERR)` · `Cause: stack pointer is not in a rw map; likely due to stack overflow` · **two addresses alternating for the whole backtrace** · the ASCII text `"Allocation failure"` still in registers | Mutual recursion between the JVM's exception constructor and the guest allocator. Building an exception message allocates a guest `char[]`; when the guest heap is what failed, that allocation fails, and the failure is reported by constructing another exception. Fix: pre-allocate one error instance at startup while the heap is healthy, root it in a static field of a registered class, and hand it out. A plain re-entrancy guard does **not** work — it converts the SIGSEGV into a panic, because the *outer* call is the one that must not enter the exception path. | `emulator-core` |
| `Init failed with code <n>` / `wipi init failed` at boot | Wrong BSS size, or the guest asked for an init interface the emulator does not know. Check the boot log for an "unknown interface" warning first. | `emulator-core` |
| `Invalid memory access; address:0` **on the client, immediately after your server reply** | Your reply body was empty where the client's text/page builder requires non-empty content — it walks a null page list. "The minimum clean reply is an empty one" is a very attractive and very wrong inference from the disassembly. | `server-revival` |
| `InvalidMemoryAccess(0)` / a branch to address 0 **right after a WIPI call** | The guest indexed a slot past the end of the function-pointer table you allocated, read whatever follows the allocation (zero), and branched to it. Pad the table **and** the corresponding struct; upstream slot counts are guesses, not a contract. | `emulator-core` |
| `FatalError("Main class not found")` after a clean load | A bare jar with no descriptor. The main-class name has no other source in the archive; synthesize a descriptor. | `archive-prep` |
| `[probe] not a KTF archive` / `Unknown archive format` — clean, at load time, no panic | No recognised magic entry in the outer zip. Re-classify: this may be a different carrier, a bare jar, a `.jad`+`.jar` pair, or not a game dump at all. | `port-triage` |
| `PANIC: … jvm.rs … called 'Option::unwrap()' on a 'None' value` on the **first host-initiated guest call** | The JVM keys per-thread state by executor task id, and the task you spawned to run the guest function has a brand-new id with no thread entry. The emulator's own callback path has always wrapped its spawns in attach/detach; the host-call path did not — which is why this feature "had never once worked on a device". Fix: an RAII attach/detach guard around every host-originated call, tolerant of the JVM not existing yet. | `emulator-core` / `guest-re` |
| `FatalError("Undefined instruction")` | A genuinely missing opcode in the interpreter. Rare — a commercial ARM feature-phone binary exercises a well-trodden subset. Add a hand-written assembly fixture with the suspect encoding. | `emulator-core` |
| `Unimplemented: <id>: <name>` — exactly one line, then a frozen or black screen | A fatal WIPI-C stub. The line names the table id and the slot. Convert it to a logging no-op returning a plausible value (a "trace stub") and re-run: the game will keep walking and dump its whole API sequence for you. | `emulator-core` |
| `UnsatisfiedLinkError` at launch | The three native-library names disagree: the Cargo `[lib] name`, the `System.loadLibrary` argument, and the manifest's `android.app.lib_name`. This is a runtime error, not a build error. | `port-shipping` |
| `Unknown SVC handler category: <n>` | An SVC category the guest uses is registered by no handler. | `emulator-core` |
| `Unknown <name>` warning from the interface getter, then a fault far away later | The guest asked for an interface the loader does not know and got a null pointer back. It dies much later, at a site with no relationship to the cause. **Check this warning first on any new title.** | `emulator-core` |

---

## 2. Behavioural symptoms

### 2.1 Archive and first boot

| symptom | what it actually means | skill |
|---|---|---|
| The emulator prints `loaded` and then panics | `loaded` means "constructed", not "booted". Every archive fault fires afterwards, on the async boot task. On a phone this surfaces as a panic line in the log and a black screen, not as a load error. | `archive-prep` |
| A boot that worked yesterday fails after you edited the descriptor in an editor | The editor rewrote the file with CRLF line endings. The KTF and LGT descriptor parsers slice from a fixed index with no trim; the SKT parser trims, so the same edit is harmless there. Normalising CR to nothing is the entire content of the "repair" step. | `archive-prep` |
| A repack tool refuses with "changed records … != expected" | Repacking is not idempotent. You are running it against an already-patched archive. Restore the pristine copy from your backup directory first. | `archive-prep` |
| The repacked archive is a different size from the original | Expected and harmless — a different zip implementation uses different deflate parameters and writes no extra fields. **Size is not a validity check.** Verify by re-parsing the modified member and byte-comparing every other member. | `archive-prep` |
| A fresh clone builds an APK with no game in it | The archive is gitignored (correctly), the assets directory is simply empty, and the packaging step succeeds anyway. Add a fail-fast build check for the asset's presence — the single most important hardening for a public template. | `port-shipping` |
| Boot works but saves land in an unexpected directory, or an old backup will not load | The save namespace is the descriptor's PID. Choose it once; changing it after release orphans every player's saves while leaving the app apparently healthy. | `archive-prep` |

### 2.2 Rendering and frame budget

| symptom | what it actually means | skill |
|---|---|---|
| **Black screen, no error, CPU busy, every dumped frame 100% black** | The host's `now()` is wall-clock and the harness ticks as fast as it can. The guest does not busy-loop — it arms a timer and yields — so no timer ever expires. Give the platform a virtual clock advanced by a fixed number of milliseconds per frame. This is the first trap every headless run hits, and the reason the millisecond flag is mandatory rather than optional. | `emulator-core` |
| Maps with a lot on screen stutter; sparse maps are fine; a full-screen effect stutters | Draw cost scales with call count × whole screen, not with pixels drawn. Every draw call reads the entire destination framebuffer out of guest memory, copies it again into a typed vector, and writes the whole thing back on drop — so a one-pixel operation pays hundreds of kilobytes of memcpy. Fix with row bands, a direct typed read, and a skip-the-read path for fills that cover their band. | `emulator-core` |
| Black horizontal bands, or stale content, after a fill | The "skip the read-back" path was used for a fill that does not actually cover the whole band. The band is written back whole on drop, so an over-permissive guard writes black rows. The guard must be evaluated in 64-bit arithmetic or extreme coordinates wrap into a false positive. | `emulator-core` |
| Frame budget blown and the log is flooded during idle | A tracing instrumentation attribute on a per-frame path. Instrumentation spans default to INFO, not DEBUG, so "set the level to info" does not fix it. A WIPI title idles on timers, so its timer callback is the worst possible place for a log record. Audit every per-frame log site before shipping. | `emulator-core` |
| Alpha-zero source pixels stopped repainting the background after a graphics optimisation | An accepted, documented semantic change on ARGB destinations — the previous behaviour was itself a bug. Bit-identical on RGB565, which is every path a feature-phone title uses. Pin it with an equivalence test rather than reverting. | `emulator-core` |
| The device is much slower than the headless run at the same frame count | Compare the work gauge (guest instructions per frame) first. If it matches, the cost is in your host layer — blitting, scaling, or per-frame allocation — not in the interpreter. A nearest-neighbour blit with a cached column lookup table and vertical run copying removes millions of integer divides per frame. | `android-host` |

### 2.3 Memory, allocator and long sessions

| symptom | what it actually means | skill |
|---|---|---|
| Steady-state stutter, and the allocator's header-bytes counter climbs into gigabytes | The bucket allocator's one-bit-per-slot map is being read in full on **every** alloc and free. Replace the bitmap scan with an 8-byte header holding a LIFO free-chain head and a bump index — but **keep an indexed in-use map**, because a bare free chain cannot survive a double free (pushing an already-free slot links it to itself and every later allocation of that class returns the same address forever). | `emulator-core` |
| Two live objects at the same guest address | Exactly the aliasing failure above. Leaking is survivable; aliasing is not. On a suspect free-chain head, log and drop the chain, then fall through to the bump index. | `emulator-core` |
| Allocated blocks leak "loudly" at free | A free whose size argument selects a different size class than the allocation did, so the address is not a slot boundary of the class being freed. Leak it and log it rather than corrupting the class. | `emulator-core` |
| The heap gauge's live count climbs monotonically during steady play | A guest-side leak. Note that some leaks are the *game's*: driving the game's own save primitives from the host allocates on each call and the game frees nothing, because the original only saved on demand. Mitigate with cadence, and say so in a comment. | `guest-re` |
| The interpreter thread overflows its native stack after tens of minutes | Instrument the thing that actually overflows, from outside the code that uses it — ask the kernel how much of the stack mapping has ever been resident. In-process nesting counters measured three plausible layers and cleared all three. Enlarging the thread's reserved stack is a mitigation, not a fix, and must be labelled as one: reserved address space costs nothing until touched. | `emulator-core` |

### 2.4 Host to guest: reads, patches and calls

| symptom | what it actually means | skill |
|---|---|---|
| A host-initiated guest call panics inside the JVM the first time it runs | See the PANIC row in §1 — the spawned task is not an attached JVM thread. | `emulator-core` |
| A guest call enters and immediately pops garbage into the program counter | The target is **not a function**. Jump-table arms share the parent's stack frame and exit through the parent's epilogue; some "functions" begin by reading the caller's frame. Verify before calling: the target must have its own push/pop prologue and epilogue and set up its own static base. | `guest-re` |
| A host state machine waits forever for a guest call that already failed | A two-state result cell conflates "not yet" with "failed". Use three states — pending / ok / failed — and give every step a timeout at roughly 100× the expected latency, reset per step. A backend with no CPU core must **fail** the result rather than leaving it pending. | `guest-re` |
| A patch works once, then stops after a new game, a load or a death | The patch is not being re-asserted. Read, compare, write only on mismatch, and re-run every frame. That single discipline makes every poke idempotent across all game-state transitions with no ordering logic, and makes expiry self-cleaning. | `guest-re` / `android-host` |
| The re-assert pass only works the first time | The helper compares against the pristine bytes only. It must also accept **its own previous encoding** as an already-applied state. | `guest-re` |
| A patched widget tears apart — half of it moves, half does not | Either you patched one of several immediates the widget uses, or the whole block **exists twice**: a compiler outlines an else-arm and reaches it with `bl` used as a long branch. Patch every site or none. | `guest-re` |
| Writing to "proven dead" guest code crashes the game | The reachability sweep did not follow `bl`. On Thumb-1, `bl` is routinely a long branch, not a call. A correct sweep follows `bl` targets, honours literal pools, and cross-checks direct branches, function-pointer words and jump-table bases. Guard the final address with a compile-time assertion so it cannot drift out of the proven range in a passing build. | `guest-re` |
| An assertion on a payload length fires on the very first real use | You pinned a measured value as an invariant. Serialised lengths are content-dependent. Assert a plausibility band, not a constant. | `guest-re` |
| A host-drawn indicator freezes on or off after a block-skip patch | The skipped block was maintaining that state (a blink counter, a frame index). Anything the skipped code updated is now frozen; the host must use its own clock instead of reading the guest's flag. | `guest-re` |
| A timed host feature silently never fires | A sentinel bug. If "never armed" is encoded as `0` and the check is `now >= last + interval`, then treating `0` as "now" makes the comparison false at **every** poll. Encode "never armed" as a distinct hard case and seed it at session start. | `android-host` |
| A feature's button always reports that it cannot be used here | The control is only reachable in a state the gate refuses. Prove the control is reachable in a state that *passes* the gate — a state machine can be correct and still unreachable. | `android-host` |
| A host feature reads a plausible number out of a stale pointer | Validate every pointer hop against the heap window and sanity-ceiling any size derived from guest memory *before* allocating on it. A stale handle must yield "no data", never a render of garbage. | `android-host` |
| The host reimplementation of a guest widget is subtly wrong and nobody notices | Diff it. Run both in the same process and compare pixel for pixel, with your output quantised through the same colour round-trip so the comparison is like for like. Zero mismatched pixels on two different game states, or it is not verified. And the check must exercise the shipping source — see [selftest-convention.md](selftest-convention.md). | `android-host` / `guest-re` |
| A diagnostic that should be reporting prints nothing | Many diagnostics are gated on an explicit frame list. A gate that silently produces no output is indistinguishable from a clean result. State the gate in the tool's own help text. | `guest-re` |

### 2.5 Input, HUD and on-device UI

| symptom | what it actually means | skill |
|---|---|---|
| "A control keeps getting pressed by itself" while the player is steering | A second finger slid over a control and acquired its key. Only touches that **started** on a control may change key by sliding; track that eligibility in its own identity set. | `android-host` |
| A key repeats forever after the app is backgrounded, rotated, or the layout editor is opened | Held keys were not released when input routing changed. The worker replays held keys on a repeat timer, so a key still down at the moment of the change auto-repeats indefinitely. Release on suspend, on focus loss, and on any mode change. | `android-host` |
| Pinch-to-resize changes the size two or four times too fast | The windowing backend on Android emits a move event for **every** pointer in the motion event, not only the one that moved, so a delta-accumulating gesture double-applies. Compute size absolutely from an anchor captured at gesture start. | `android-host` |
| A control snaps back under the finger when dropped, and the layout file gains a line that does nothing | The editor's acceptance predicate is a lookalike of the layout resolver instead of *being* it. Run the real resolver on a table containing the candidate and ask whether that control got the rectangle it asked for. | `android-host` |
| A control sits somewhere legal-looking but pressing it does something else | It overlaps an obstacle that is hit-tested **before** the control list. Control-versus-control overlap is a cosmetic choice and can be loose; control-versus-obstacle must be strict, precisely because the consequence is silent misrouting rather than an ugly layout. | `android-host` |
| Growing one control shrinks all the others | Player sizing was fed back into the built-in geometry formulas, which derive several sizes from one anchor. Built-ins must be a pure function of window size with no player input anywhere in them; layer the player's scaling on top. | `android-host` |
| A control that has moved still blocks its old position | The resolver ran a single pass. Run it to a fixpoint — one move per control, monotone, so it terminates in at most one pass per control. | `android-host` |
| Editing one control makes a different control leap across the screen | The resolver is priority-ordered, so an earlier control growing into a later one's rectangle displaces it. The editor must also refuse an edit that costs another control its honoured placement. | `android-host` |
| Mirroring the layout for left-handed play produces a mess | Validating each control as you mirror it fails, because controls not yet mirrored still block from their old seats. Mirror everything unvalidated first, then repair only what the resolver refuses. | `android-host` |
| A dragged control slowly shrinks | The size is being re-derived from a pixel rectangle on every event, and the round-trip floors twice. Carry the size as a percentage throughout. | `android-host` |
| Two fingers never both register; the second one deselects the first | Selection was deselecting on a miss. Two fingers never arrive in the same event — the second is its own start event, almost always on empty screen — so "miss = deselect" makes multi-touch unreachable by construction. | `android-host` |
| The guest's key state inverts on every other press | A decision was taken on both edges of the same touch, and the press itself changes the state being read. Remember the decision taken on the DOWN edge and reuse it on UP. | `android-host` |
| The layout is built from the wrong screen size at first launch after a rotation | The first resume can report the pre-rotation window size. Derive the logical size from the long and short sides plus the persisted orientation preference, never from a width-versus-height comparison. | `android-host` |
| Walking direction sticks or the character stutters when changing direction on a virtual stick | The guest latches a walk direction on key-down. Emit **key-up of the old direction before key-down of the new**, one key at a time, four-way with hysteresis. Eight-way two-key emission is wrong for a latched-direction guest. | `android-host` |
| A brief frame hitch every time the phone vibrates | Attaching a thread to the VM, looking up the system service and making a synchronous binder call, all inside a 16 ms frame. Move haptics to a dedicated, permanently attached thread. | `android-host` |
| An orientation toggle or a dialog silently does nothing, with a completely clean build | Native code looks the class up **by name** over JNI, and the runtime context handed to native code is the Application, not the Activity — a native thread's class lookup cannot see application classes. Go through the context's class loader. This also means renaming the Java namespace fails silently at call time rather than at build time. | `android-host` / `port-shipping` |
| A save survives a normal exit but not a task kill | The storage layer is asynchronous. Use synchronous file writes so a write is on disk by the time the call returns; Android kills backgrounded apps without warning. For the same reason, persist layout edits on every finger-up rather than on a "done" button, and implement "cancel" as a restore of an entry snapshot. | `android-host` |

### 2.6 Text and glyphs

| symptom | what it actually means | skill |
|---|---|---|
| A CJK label renders as **blank space** — not as tofu boxes | The host overlay font is an ASCII bitmap font and it silently skips characters it does not have. A hole is much harder to notice than a box. Generate a closed set of glyphs for exactly the strings the host draws, and assert coverage in a test so a new string cannot forget a glyph. | `android-host` |
| Generated CJK glyphs look like grey mush at small sizes | They were rendered large and downscaled. A hinted screen face puts strokes on the pixel grid deliberately, and resampling averages that away. Render directly at the target size, and accept that below a certain size some faces simply lose strokes. | `android-host` |
| A hint string renders with a word missing | A punctuation character is absent from the ASCII font. Enumerate the font's real character set and keep host strings inside it, or add the glyph. | `android-host` |
| Text input is needed and there is no keyboard | Correct — an overlay font has no text-input machinery. Route text entry to platform dialogs over JNI. | `android-host` |

### 2.7 Network and replacement server

| symptom | what it actually means | skill |
|---|---|---|
| The client sits on a "waiting for the server" state forever, even though it is receiving your replies | You replied, but not with the **one** operation that clears the blocking-state variable. Find that variable and the single reply that clears it before doing anything else; until then every experiment is misattributed. | `server-revival` |
| The client dismisses the modal but then cleanly closes the socket after a *successful* operation | You used a notification-class reply that dismisses the dialog without advancing the session state machine. "Dismissed the dialog" and "advanced the state" are different things and several plausible acknowledgements do only the first. | `server-revival` |
| The client never reads anything you send | The read-callback registration slot is stubbed in the emulator, so the guest stops polling for received data entirely. | `emulator-core` / `server-revival` |
| The client tears down a healthy connection | `recv` returned `0`, or a wrong negative, instead of the platform's specific **no-data sentinel**. Conflating "nothing yet" with "peer gone" is fatal. | `server-revival` |
| Guest heap corruption after a large reply | The frame exceeded the guest's reassembly buffer, or you satisfied a `recv` request in full — old games over-ask. Derive the frame cap from the reassembly buffer and enforce it **in the encoder**, and clamp `recv` to the guest's *real* allocation, not the size it asked for. | `server-revival` |
| Corruption or a crash after a list-shaped reply | Your element count exceeded a fixed-size array the client fills without clamping. Where the client does not clamp, **the bound is a server obligation** — enforce it once, in the encoder. | `server-revival` |
| The client crashes formatting a string from your reply | An unbounded string copy into a fixed destination. Find the real capacity of each such destination and enforce it in the encoder, including a multibyte-safe truncation that cannot end on half a character. | `server-revival` |
| Everything works over a USB-forwarded port and over the LAN, then the app freezes forever on the public internet | A socket **error** was reported as not-readable. The read waiter is then dropped silently and the receive pump is gated off for good. LAN and port-forwarding only ever produce clean connection closes, so this is invisible in development and is the first thing every remote player hits. A failed socket must count as readable. | `android-host` / `server-revival` |
| Connect starts timing out after switching from a bare IP to a domain name | Name resolution has no timeout of its own and a per-address timeout multiplies by the number of addresses, so the guest's own patience expires first. Budget the whole operation — resolution included — under the guest's timeout. | `android-host` |
| The client shows a connection-refused error | Only a refused/reset status produces that message. Accept and hold the connection and it never appears — a mute server is fine, a closed port is not. | `server-revival` |
| Replies work individually but a burst of them is dropped or corrupts state | The guest's completed-frame ring is tiny, and its pump allocates a fresh receive buffer after dispatching one frame — so bytes of a second frame that arrived in the same read are discarded. Answer at request-response pace. | `server-revival` |
| Every player shares one account | The guest's identity field is a hardcoded constant: the original client filled it from a device property whose implementation is a stub, then overwrote it with a literal. Because the host owns the send path, inject a real per-device identity into the outgoing frame. The server needs no protocol change — it keys on whatever bytes arrive. | `android-host` / `server-revival` |
| "It only works on my desk" | Before touching any firewall, prove the client is even *aiming* at a public address: a compile-time destination constant fails identically to a blocked port. Then remember there are **two** independent gates — the cloud provider's security group and the host firewall inside the machine — and opening one produces a silent timeout indistinguishable from the wrong address. | `port-shipping` / `server-revival` |
| The server looks healthy but nobody can connect | A bind failure on one listener was treated as non-fatal and the process kept running on the other. Bind failure must be fatal. | `server-revival` |
| A configuration change appears not to take effect | A stale listener still holds the port and your new process's bind failed. Kill it properly; on some hosts the naive kill leaves the port bound and the next bind fails with an error you never see. | `server-revival` |
| A protocol experiment cannot be attributed to anything | You did not record the silent baseline. Start with an empty reply table — socket alive, server mute — so a later behavioural change can be attributed to the reply you added. | `server-revival` |
| Everyone's replies suddenly become wrong | A catch-all entry in the hot-reload override table hijacks every user, not just yours. Ship the override table off by default and say why in the help text. | `server-revival` |

### 2.8 Build, sign, install

| symptom | what it actually means | skill |
|---|---|---|
| The build succeeds and the APK points at localhost, with online features dead for everyone but you | A build-time-baked constant resolved to nothing. `option_env!` is evaluated at compile time and a missing variable produces a *silent wrong build*, not an error. Three defences: resolve from persistent configuration rather than only the shell; hard-error on a release build with no destination; and scan the produced shared object for the expected string. The last one is the only check that cannot be fooled — note that the fallback string is always present, so "does it mention localhost" proves nothing; only presence of the **wanted** value does. | `port-shipping` |
| A rebuild "did nothing" and the old configuration is still baked in | A stale cargo fingerprint. This is exactly what the artifact scan catches; without it, the build is perfectly successful and wrong. | `port-shipping` |
| The debug APK is hundreds of megabytes | The strip stage was skipped. Strip in the build script for both profiles and tell the packaging step not to strip again. | `port-shipping` |
| Launching by an explicit component name fails with "class not found" | The application id and the Java package are deliberately different. Derive the launchable component from the built package's own metadata instead of composing it by hand. | `port-shipping` |
| PowerShell aborts a build step that actually succeeded | A stop-on-error preference combined with redirecting a native command's stderr. Many tools write ordinary progress to stderr. Do not redirect; check the exit code explicitly. | `port-shipping` |
| A properties file's comments are corrupted, or the build reads the wrong version | The file was rewritten with a BOM or through the console code page, and the consuming loader decodes it as Latin-1. Write it with an explicit no-BOM UTF-8 writer and keep the file ASCII-only. | `port-shipping` |
| `cargo test -p <app-crate>` dies immediately | The application crate's platform link graph. Expected. Use the standalone selftest harness — see [selftest-convention.md](selftest-convention.md). | `port-shipping` |
| A bare `cargo build --target aarch64-linux-android` links against a different API level than the app declares | A stale linker configuration file with absolute paths and its own API level. The wrapper tool's environment variables win when you go through it, so the file is dead config *until* somebody bypasses the wrapper. Delete it. | `port-shipping` |
| The fork cannot be diffed against upstream or rebased | The vendored core was copied rather than tracked, and no base commit was recorded. Record repository URL, base commit and date in a file next to the vendored tree, refreshed on every re-vendor. | `emulator-core` / `port-shipping` |
| Inherited CI fails on a workspace-wide test run | The inherited workflow tries to host-build the application crate. Replace it with a job that runs the selftests, the vendored core's own tests, and a cross-compile build. | `port-shipping` |

### 2.9 Publish and secrets

| symptom | what it actually means | skill |
|---|---|---|
| A secret is gone from the working tree but still in the repository | Ignore rules prevent future additions and never remove past ones. Audit **all refs**, not the working tree — and remember that a history purge is undone by the backup branch you made "just in case". | `port-shipping` |
| Licensed content leaks despite an ignore rule for its directory | A tool wrote a byte-identical copy under a different name in a different directory, and the rule only guarded one path. Guard by **name** as well as by path. | `port-shipping` / `archive-prep` |
| A distributed build embeds content you did not intend to publish | A packaged application contains the game archive *and* the baked configuration. A source-tree purge cannot succeed while built artifacts are tracked. | `port-shipping` |
| Real user data ends up in the source tree | Default state and log paths point at the package directory. Default them outside the source tree, or require the path to be given explicitly. | `server-revival` |
| An operator believes a destructive feature is off while it is on | Code default and deployment documentation disagree. Deployment documentation is part of the security surface; a mismatch on any destructive flag is a defect. | `server-revival` |

---

## 3. Faults that look like a different phase's fault

The most expensive failures in this methodology are the ones whose symptom points at the wrong
layer. Keep this short list in mind when a fix is not working.

| looks like | actually is |
|---|---|
| A black screen "in the game" | Almost never the game. In order of likelihood: no virtual clock (headless), a fatal unimplemented stub, an archive fault that panicked on the async boot task, or a missing bundled asset. |
| A protocol bug | Frequently a host socket-layer bug: a stubbed read callback, a wrong no-data sentinel, an unclamped `recv`, or an error status reported as not-readable. Rule those out before touching the frame format. |
| A game-logic bug in your host reimplementation | Frequently a stale pointer that was never validated, or a struct offset with only one derivation. |
| An emulator opcode bug | Almost never. Expect not to need opcode work on a commercial ARM feature-phone binary; spend the budget on the platform API surface instead. A *silently* wrong opcode presents as garbage state and a fault at an unrelated address, so if you must chase one, use instruction tracing or the debug server rather than reasoning. |
| A firewall problem | Frequently a compile-time destination constant. Prove the client is aiming at a public address first. |
| A test that is passing | If it is a hand-kept mirror of the shipping source rather than an `include!` of it, it is passing about a copy. |
