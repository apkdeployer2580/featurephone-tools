---
name: android-host
description: Builds the native Android host layer around a wie-family feature-phone emulator core - GameActivity and winit wiring, a worker thread that owns guest memory and talks to the UI thread through one enum per direction, RGB565 blit with a column LUT and vertical run copying, touch-to-keypress routing through an ordered hit-test chain, a placeable touch HUD with an on-device layout editor and a fixpoint placement resolver, host-side reimplementation of guest widgets such as a minimap, closed-set CJK glyph generation for an ASCII bitmap font, JNI glue and layered on-device configuration. Use when working on the Android app layer of an emulator port - screen scaling, letterboxing or black bars, touch controls, a virtual joystick or D-pad, HUD placement or a layout editor, overlays, a control that fires by itself or sticks held down, pinch-resize double-applying, Korean or CJK text drawing as blank, JNI dialogs, orientation, vibration, audio, or app-private storage for saves.
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
Android GameActivity subclass (Java, ~250 lines: loadLibrary + static dialog entry points)
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

## The contract - five rules that shape every file

1. **Two threads, two enums, no shared mutable state.** Worker owns guest memory and the
   emulator; UI owns the window, layout and drawing.
2. **Anything that needs a guest-memory read to decide is decided on the worker.** The UI's
   copy of guest state is up to ~80 ms old; the UI reports *that a tap happened*, never
   *what it means*.
3. **Never decide twice on the two edges of one touch.** Remember the DOWN decision and
   replay it on UP - the press itself changes the state the UP edge would read.
4. **Read - compare - write - re-assert forever** for every host poke into the guest. Makes
   pokes idempotent across new game / continue / death / map change with no ordering logic.
5. **Nothing blocks inside a guest call.** DNS, TLS handshakes, binder calls and long guest
   calls all go to threads and are polled.

## Build order

| # | Do | Done when |
|---|---|---|
| 1 | Java shim + `android_main` + worker thread + deferred boot | game renders on a device |
| 2 | `Platform`/`Screen` impl, frame pool, blit, letterbox | full-rate scaled output, no tearing |
| 3 | Storage + config layering | saves survive a process kill; DB layout matches the probe's |
| 4 | Touch -> keypress, joystick, held-key discipline | playable with the default control set |
| 5 | Overlay drawing + the three layout families | HUD readable in both orientations |
| 6 | Placement table + resolver + on-device layout editor | controls placeable, persisted, no control overlaps an obstacle |
| 7 | Guest reads: gauges, inventory, host-side widget reimplementation | probe reports 0 mismatched pixels vs the guest's own buffer, on 2+ game states |
| 8 | Guest writes + host-initiated guest calls | one call chain completes on-device with no JVM panic |
| 9 | Glyphs, dialogs, vibration, audio, orientation | no blank labels, no frame hitch on haptics |

Steps 6 and 7 loop with `guest-re`. Do not start 7 before the read closure exists (see
[thread-model.md](reference/thread-model.md#the-read-closure)).

## Configure, don't rewrite

These lift almost unchanged from any prior wie-family host. Rewriting them is the single
most common waste of a port's time.

| Module | Change only |
|---|---|
| Platform/Screen impl, RGB565->ARGB, frame pool | nothing |
| placement table, fixpoint resolver, editor predicate | the control-id enum and the per-mode id lists |
| layered config, endpoint + pin resolution, account id | the env-var prefix `${ENV_PREFIX}_` |
| endpoint parser, TLS wrapper, plaintext-buffer handoff | nothing |
| WIPI filesystem + record store over app-private storage | nothing |
| audio sink (PCM + in-process SF3 MIDI synth) | nothing (supply your own SoundFont) |
| vibration, orientation, text-prompt dialogs (all JNI) | the Java class name |
| host UI icons as coverage masks | your own mask blob |
| guest icon-atlas parser | the magic bytes and the frame decoder |
| overlay drawing primitives, 5x7 ASCII font, alpha blending, `Rect`, ring/disc geometry | nothing |
| blit + column LUT, touch-identity sets, tap-slop zones, joystick driver, editor gestures | nothing |
| tick-loop skeleton, stack gauge, poll cadences | the poke list |
| GameActivity subclass | the package name |

**Rewrite from scratch, per title:** the guest-memory model (god-object resolution, gauges,
inventory, the poke list), any host reimplementation of a guest widget, the generated glyph
table, and any host-invented feature.

## Reference

* [thread-model.md](reference/thread-model.md) - worker/UI split, the two enums, deferred
  boot and the pre-rotation size race, the tick loop, frame transport, blit, the read
  closure, host-initiated guest calls and the JVM attach trap.
* [input-and-hud.md](reference/input-and-hud.md) - the ordered hit-test chain, three
  touch-identity sets, the key-edge machine, tap slop, the latched-direction joystick,
  held-key release discipline, the three layout families.
* [layout-editor.md](reference/layout-editor.md) - fraction+percent placement, built-ins as a
  pure function of window size, the fixpoint resolver, loose vs strict overlap, the three
  obstacle families, mirroring, and the winit-Android `Moved`-for-every-pointer landmine.
* [guest-widgets.md](reference/guest-widgets.md) - reimplement instead of screen-scraping,
  pointer-hop validation, differential verification against the guest's own buffer, the six
  classes of host->guest write and their rules, host-owned state.
* [glyphs-and-text.md](reference/glyphs-and-text.md) - the ASCII font and what it costs,
  closed-set CJK glyph generation, render at target size, coverage assertions, platform
  dialogs for text input.
* [platform-glue.md](reference/platform-glue.md) - the Java shim, the Application-not-Activity
  problem, haptics thread, in-process MIDI, orientation, lifecycle, log filtering.
* [storage-and-config.md](reference/storage-and-config.md) - synchronous filesystem and record
  store, id sanitising, probe-compatible DB layout, three-layer config, endpoint and trust
  resolution, account identity and its transfer code.

Tools: run `python "${CLAUDE_PLUGIN_ROOT}/tools/gen_glyphs.py" --help` to generate a
closed-set glyph table.
Testing: `${CLAUDE_PLUGIN_ROOT}/shared/selftest-convention.md` - `cargo test` cannot run in
this crate; pure logic goes in dependency-free modules that standalone tests `include!`.
