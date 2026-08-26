---
name: android-host
description: Builds the native Android host layer around a wie-family feature-phone emulator core - GameActivity and winit wiring, a worker thread owning guest memory that talks to the UI thread through one enum per direction, RGB565 blit and letterboxing, touch-to-keypress routing through an ordered hit-test chain, a placeable touch HUD with an on-device layout editor, making the game's own keypad menus tappable by writing its cursor byte, gamepad support with position badges, host windows drawn in the game's own font and chrome, audio mixing and ducking, bars, cutout and orientation, sustained load on old hardware, and layered configuration. Use when working on the Android app layer of an emulator port - screen scaling, black bars, touch controls, a joystick or D-pad, HUD placement, a control that fires by itself or sticks held down, tapping the game's own menus, a gamepad whose sticks do nothing, CJK text drawing blank, unbalanced audio, no rotation at 180 degrees, or a hot phone.
license: MIT
---

# Android host layer

Everything above the emulator's `Emulator` trait and below Gradle. Assumes the core already
boots headless (`emulator-core`) and that guest addresses come from `guest-re`.

Provenance tags: **(A)** generic procedure · **(B)** worked example from the reference port,
re-derive the number rather than copying it · **(C)** never copy. Conventions:
`${CLAUDE_PLUGIN_ROOT}/shared/evidence-standard.md`.

## The three-layer stack (A)

```
Android GameActivity subclass (Java, ~250 lines: loadLibrary, static dialog entry points,
                               gamepad dispatch overrides, platform declarations)
        v  android_main()
winit UI thread   -- ApplicationHandler: softbuffer surface, blit, touch routing, overlay draw
        ^v
        EventLoopProxy<UserEvent>   worker -> UI, ONE enum
        mpsc::Sender<InputEvent>    UI -> worker, ONE enum
        Arc<AtomicBool> paused      UI -> worker, background/lock gate
        Arc<AtomicU32>  geometry    UI -> worker, packed layout numbers the worker must poke
        ^v
worker thread, large explicit stack -- tick loop + Platform/Screen impl
        v  wie_backend::Emulator  (KTF / LGT / SKT / J2ME backend)
        v  ARM interpreter
```

The crate is a `cdylib` whose `[lib] name` must equal the string passed to
`System.loadLibrary` and to the manifest's `android.app.lib_name`. Three-way agreement that
fails at *runtime* only.

## The contract - six rules that shape every file

1. **Two threads, two enums, no shared mutable state.** Worker owns guest memory and the
   emulator; UI owns the window, layout and drawing.
2. **Anything that needs a guest-memory read to decide is decided on the worker.** The UI's
   copy of guest state is up to ~80 ms old; the UI reports *that a tap happened*, never
   *what it means*.
3. **Never decide twice on the two edges of one touch.** Remember the DOWN decision and
   replay it on UP - the press itself changes the state the UP edge would read.
4. **Read - compare - write - re-assert forever** for every host poke that establishes
   *persistent* guest state. Makes pokes idempotent across new game / continue / death / map
   change with no ordering logic. A **transient UI cursor** write inverts this rule - written once,
   never re-asserted, bounds-checked instead of compared
   ([guest-widgets.md](reference/guest-widgets.md#7-the-eight-classes-of-host-to-guest-write)).
5. **Nothing blocks inside a guest call.** DNS, TLS handshakes, binder calls and long guest
   calls all go to threads and are polled.
6. **A storage-layer refusal is a data-loss mechanism, not a safety net.** The guest usually
   ignores the write's failure code while the rest of the session commits, so a guard that refuses
   a save is silent to the game *and* to the player. **Prefer redirect to refusal**, and never let
   a guard be the reason a player quietly stops being saved
   (`${CLAUDE_PLUGIN_ROOT}/skills/save-integrity/SKILL.md`).

## Build order

| # | Do | Done when |
|---|---|---|
| 1 | Java shim + `android_main` + worker thread + deferred boot | game renders on a device |
| 2 | `Platform`/`Screen` impl, frame pool, blit, letterbox | full-rate scaled output, no tearing |
| 3 | Storage + config layering | saves survive a process kill; every record write is temp+fsync+rename; DB layout matches the probe's |
| 4 | Touch -> keypress, joystick, held-key discipline | playable with the default control set |
| 5 | Overlay drawing + the three layout families | HUD readable in both orientations |
| 6 | Placement table + resolver + on-device layout editor | controls placeable, persisted, no control overlaps an obstacle |
| 7 | Guest reads: gauges, inventory, host-side widget reimplementation | probe reports 0 mismatched pixels vs the guest's own buffer, on 2+ game states |
| 8 | Guest writes + host-initiated guest calls | one call chain completes on-device with no JVM panic |
| 9 | Text, dialogs, vibration, audio, orientation | the host draws in the **game's own** typeface and its windows use chrome measured from the game's own UI sheets; audio has its own exit criteria (below); no frame hitch on haptics; the game rotates both ways |

Steps 6 and 7 loop with `guest-re`. Do not start 7 before the read closure exists (see
[thread-model.md](reference/thread-model.md#8-the-read-closure)).

**Step 9's audio exit criteria, because 0.1.0 gave audio none at all** — and they are unusual in
being partly unautomatable:

- each bus rendered **offline at the same in-game setting** and tabulated as peak and RMS, with the
  gap between buses stated and **constant across every slider step**;
- **a real person has listened on a real device.** Nothing in the toolchain can hear; every round of
  audio work in the reference port ended with a human "yes";
- **audio init failure self-reports on device** (a status line in a diagnostics dialog) — a silent
  handset is otherwise unreachable;
- **the effect/music balance is a SETTING**, with its default bit-identical to the shipped constant,
  because one global number provably cannot serve two handsets.

## Configure, don't rewrite

These lift almost unchanged from any prior wie-family host. Rewriting them is the single
most common waste of a port's time.

| Module | Change only |
|---|---|
| Platform/Screen impl, RGB565->ARGB, frame pool | nothing |
| placement table, fixpoint resolver, editor predicate | the control-id enum and the per-mode id lists |
| layered config, endpoint + pin resolution, account id | the env-var prefix `${ENV_PREFIX}_` |
| endpoint parser, TLS wrapper, plaintext-buffer handoff | nothing |
| WIPI filesystem + record store over app-private storage | **the save-policy hooks** - the vault, the write/delete gate and the load observation belong *in* the store (`save-integrity`) |
| audio sink **topology** (mixer bus -> master limiter -> device; the synth as an infinite source owned by the render thread; MIDI over a channel; PCM one-shots as concurrent inputs) | **the gain constants** - re-measure peak and RMS per bus from your own assets, and expose the effect trim as a setting |
| vibration, orientation, text-prompt dialogs (all JNI) | the Java class name |
| host UI icons as coverage masks | your own mask blob |
| guest icon-atlas parser | the magic bytes and the frame decoder |
| overlay drawing primitives, alpha blending, `Rect`, ring/disc geometry | nothing |
| the **font** | extracted from the target's own font members; a hand-rolled ASCII bitmap face is the fallback |
| blit + column LUT, touch-identity sets, tap-slop zones, joystick driver, editor gestures | nothing |
| tick-loop skeleton, stack gauge, poll cadences | the poke list |
| GameActivity subclass | the package name |

**Rewrite from scratch, per title:** the guest-memory model (god-object resolution, gauges,
inventory, the poke list), any host reimplementation of a guest widget, the generated font table,
and any host-invented feature.

> **REFUTED.** *Previously documented (this table, 0.1.0):* "audio sink (PCM + in-process SF3 MIDI
> synth) | change only: nothing (supply your own SoundFont)" — i.e. the sink lifts whole and the
> per-port audio work is picking a SoundFont. **Refuted by** three separate player reports on one
> title (music too loud against effects, effects inaudible at full volume, the game quiet while the
> volume keys moved a *ring* slider), all traced to constants that had been carried rather than
> measured. **Actual mechanism:** the topology is generic; the **numbers** — synth output gain,
> effect-bus trim, limiter knee, duck depth — are measurements of one title's assets on one class of
> handset. The wrong reading is attractive because the module compiles and makes sound. **Consequence
> if carried forward:** you reproduce the exact imbalance the reference port shipped. **The per-port
> work is:** classify the buses from the container's chunk tags, render each music file at unity and
> tabulate peaks, count how many effect samples are brickwalled at full scale, and set the constants
> from that table — [audio-mixing.md](reference/audio-mixing.md),
> [audio-core.md](../emulator-core/reference/audio-core.md).

**And the same warning one level up (§"the generated glyph table"):** 0.1.0 told you to hand-roll an
ASCII font and generate a closed CJK set. Where the title ships its own font — the common case —
that is now the fallback, not the norm; see [game-native-ui.md](reference/game-native-ui.md).

## Reference

* [thread-model.md](reference/thread-model.md) - worker/UI split, the two enums, deferred
  boot and the pre-rotation size race, the tick loop, frame transport, blit, the read
  closure, host-initiated guest calls and the JVM attach trap.
* [input-and-hud.md](reference/input-and-hud.md) - the ordered hit-test chain and why its last
  arm is a policy, three touch-identity sets, the key-edge machine, tap slop, the
  latched-direction joystick, held-key release discipline, the layout families and the
  input-source axis.
* [layout-editor.md](reference/layout-editor.md) - fraction+percent placement, built-ins as a
  pure function of window size, the fixpoint resolver, loose vs strict overlap, the three
  obstacle families, mirroring, and the winit-Android `Moved`-for-every-pointer landmine.
* [guest-widgets.md](reference/guest-widgets.md) - reimplement instead of screen-scraping (and
  the two categories where that inverts), pointer-hop validation, differential verification
  against the guest's own buffer, the **eight** classes of host->guest write and their rules,
  host-owned state.
* [glyphs-and-text.md](reference/glyphs-and-text.md) - **the fallback font path**: the ASCII
  face and what it costs, closed-set CJK generation for host-invented words, render at target
  size, coverage assertions, platform dialogs for text input.
* [platform-glue.md](reference/platform-glue.md) - the Java shim and the closed list of what
  must live in it, the Application-not-Activity problem, the Java->Rust `native` contract, the
  haptics thread, in-process MIDI, orientation, lifecycle, log filtering.
* [storage-and-config.md](reference/storage-and-config.md) - synchronous **and atomic** filesystem
  and record store, id sanitising, probe-compatible DB layout, three-layer config and its two
  layer-1 locations, endpoint and trust resolution, account identity and its transfer code.
* [touch-driven-guest-ui.md](reference/touch-driven-guest-ui.md) - making the game's own keypad
  menus, grids and confirms tappable: its cursor byte plus its confirm key, geometry from the frame
  buffer, the runtime cross-check, clamp-a-scalar / reject-an-index, and the gate list.
* [gamepad-and-pad-hud.md](reference/gamepad-and-pad-hud.md) - the owned Java dispatch route the
  axes require, one mapping table for two routes, position badges as a reverse lookup, the pad HUD
  mode, and modal capture.
* [mode-resolution.md](reference/mode-resolution.md) - deriving what the game is showing, the
  hysteresis it needs, and the one place where reusing that answer is a correctness bug.
* [game-native-ui.md](reference/game-native-ui.md) - drawing the host UI with the game's own font,
  window chrome and icon atlas. **Supersedes the hand-rolled font for any title that ships one.**
* [host-windows.md](reference/host-windows.md) - which inputs a host window must accept, modal
  precedence in both paths, sizing, and focus that reads at arm's length.
* [audio-mixing.md](reference/audio-mixing.md) - mixer topology, the master limiter, ducking, the
  per-device level row, freezing on the background gate, and verifying audio without ears.
* [fullscreen-and-orientation.md](reference/fullscreen-and-orientation.md) - system bars, the
  display cutout, **which** orientation constant, and what to recompute on resize.
* [sustained-load.md](reference/sustained-load.md) - what to remove when the *device* rather than
  the game is the constraint: thermals, the identical-frame skip, the opt-in switches.

Tools: `python "${CLAUDE_PLUGIN_ROOT}/tools/gen_gamefont.py" --help` extracts and bakes the
**game's own** font; `python "${CLAUDE_PLUGIN_ROOT}/tools/gen_glyphs.py" --help` generates a
closed-set glyph table for the fallback path.
Save policy is its own skill: `${CLAUDE_PLUGIN_ROOT}/skills/save-integrity/SKILL.md`.
Testing: `${CLAUDE_PLUGIN_ROOT}/shared/selftest-convention.md` - `cargo test` cannot run in
this crate; pure logic goes in dependency-free modules that standalone tests `include!`.
