# Platform glue: Java shim, JNI, lifecycle, haptics, audio

The parts of the host that are not the emulator and not the HUD: the ~250 lines of Java, the
JNI calls back into the platform, the activity lifecycle, and the two subsystems (haptics and
audio) that have to be built differently on Android than on a desktop. Small surface, high trap
density.

Tags: **(A)** generic · **(B)** worked example from the reference port, re-derive it ·
**(C)** do not copy.

## Contents

- [1. The Java side is tiny, and must stay tiny](#1-the-java-side-is-tiny-and-must-stay-tiny)
- [2. The three-way native library name agreement](#2-the-three-way-native-library-name-agreement)
- [3. ⚠ The Application-not-Activity trap](#3--the-application-not-activity-trap)
- [4. Orientation](#4-orientation)
- [5. Haptics on a dedicated always-attached thread](#5-haptics-on-a-dedicated-always-attached-thread)
- [6. Audio: MIDI must be rendered in-process](#6-audio-midi-must-be-rendered-in-process)
- [7. The activity lifecycle](#7-the-activity-lifecycle)
- [8. Loading the game archive](#8-loading-the-game-archive)
- [9. Logging](#9-logging)
- [10. Symptom table](#10-symptom-table)
- [11. Checklist](#11-checklist)

---

## 1. The Java side is tiny, and must stay tiny

**(A)** The entire Java layer is a `GameActivity` subclass with three jobs:

```java
public class MainActivity extends GameActivity {
    private static MainActivity instance;              // stashed for the static entry points

    static { System.loadLibrary("main"); }             // (2) - the name is a contract

    @Override protected void onCreate(Bundle b) { instance = this; super.onCreate(b); }

    // (3) static entry points callable from native code, each hopping to the UI thread
    public static void promptText(String title, String message, String initial) {
        final MainActivity a = instance;
        if (a == null) return;
        a.runOnUiThread(() -> { /* AlertDialog + EditText; on OK, hand the value back */ });
    }
}
```

Three properties, each load-bearing:

- **`System.loadLibrary` in a static initialiser**, so the library is present before any native
  method resolves.
- **`instance` stashed in `onCreate`.** Native code has no Activity reference (§3); a static
  field is the pragmatic bridge. Clear it in `onDestroy` and null-check every use — a dialog
  requested during teardown must be a no-op, not a crash.
- **`runOnUiThread` around everything that touches a `View` or a `Dialog`.** Native code calls
  these from the worker or from a JNI helper thread, and Android's UI toolkit is single-threaded
  and will throw.

**Do not grow this file.** Every line of logic in Java is a line that cannot be unit-tested by
the selftest harness and that must be kept in sync with Rust by hand. Java's job is
`loadLibrary`, `instance`, and thread hopping.

**★ The Java namespace must not be renamed when scaffolding a new port.** Rust resolves these
classes **by name string** over JNI. Moving the package produces a perfectly clean build and a
silently dead dialog and orientation toggle at runtime. See `port-shipping`.

## 2. The three-way native library name agreement

Three places must contain the same string, and a mismatch fails only at **runtime**:

| where | what |
|---|---|
| the Rust crate's `Cargo.toml` | `[lib] name = "main"`, `crate-type = ["cdylib"]` |
| the Java shim | `System.loadLibrary("main")` |
| `AndroidManifest.xml` | `<meta-data android:name="android.app.lib_name" android:value="main" />` |

**(A)** With `GameActivity`, the value is conventionally `main`, because the activity glue looks
up `android_main` in the library named by the manifest metadata. Keep it. The failure is
`UnsatisfiedLinkError` if `loadLibrary` disagrees, and a blank activity with no useful log if
the manifest disagrees.

Also **(A)**: depend on the windowing crate with its **game-activity** feature, and never depend
on the activity-glue crate directly. Two copies of the glue in one binary produce duplicate
symbols or, worse, two `android_main` registrations and a launch that does nothing.

The only Rust symbol Java knows is `android_main`. Everything else is your own JNI.

## 3. ⚠ The Application-not-Activity trap

> **The native context handle you get from the Android context crate is the **Application**,
> not the Activity. And a `FindClass` from a native thread cannot see your app's classes at
> all.**

Both halves bite in the same five minutes, and the symptoms are unhelpful:

- Calling an `Activity` method on the Application handle throws `NoSuchMethodError` — the class
  really does not have it.
- `env.find_class("com/example/app/MainActivity")` from a thread you attached yourself returns
  `ClassNotFoundException`, even though the class is unquestionably in the APK. The reason is
  that a natively attached thread gets the **system** class loader, which cannot see application
  classes; only threads created by the Java side carry the app class loader.

**The fix for both is one pattern: go through the context's own class loader.**

```rust
let ctx = ndk_context::android_context();
let vm  = unsafe { JavaVM::from_raw(ctx.vm().cast())? };
let mut env = vm.attach_current_thread()?;
let context = unsafe { JObject::from_raw(ctx.context().cast()) };   // the APPLICATION

// ask the context for its class loader, then load your class BY NAME through it
let loader = env.call_method(&context, "getClassLoader", "()Ljava/lang/ClassLoader;", &[])?
                .l()?;
let name   = env.new_string("com.example.app.MainActivity")?;       // DOTS, not slashes
let class  = env.call_method(&loader, "loadClass",
                "(Ljava/lang/String;)Ljava/lang/Class;", &[(&name).into()])?.l()?;

// now call a STATIC method on that class - which is why the Java entry points are static
env.call_static_method(JClass::from(class), "promptText",
    "(Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;)V", &[...])?;
```

Two details that cost time if missed:

- `loadClass` takes a **dotted** binary name (`com.example.app.MainActivity`), while
  `find_class` takes a **slashed** internal name. Mixing them up gives you
  `ClassNotFoundException` for a class that exists.
- **This is the reason the Java entry points are `static`.** You have a `Class`, not an
  instance, so a static method plus an `instance` field on the Java side is the shortest path
  that works. Trying to obtain the Activity object from native code is a much longer road for
  no benefit.

## 4. Orientation

Locking or switching orientation is `Activity.setRequestedOrientation(int)`, reached exactly as
in §3: class loader -> your Activity class -> a static helper that forwards to
`instance.setRequestedOrientation(...)` on the UI thread.

**(A)** Persist the player's orientation preference in your own config and treat it as the
source of truth, because you need it during boot **before any window exists**, to derive the
logical guest size — see
[thread-model.md](thread-model.md#2-deferred-boot-and-the-pre-rotation-size-race). Reading the
current orientation from the system at that moment is exactly the thing that races.

Apply the preference at `onCreate` time as well as when the player changes it, or the first
frame after a cold start is drawn in the wrong orientation and then flips.

## 5. Haptics on a dedicated always-attached thread

**(A) Do not vibrate inline.** A vibration from inside the frame loop costs, in order:
`AttachCurrentThread`, a class-loader lookup, `getSystemService`, a `VibrationEffect`
construction, a **synchronous binder call** into the system vibrator service, and
`DetachCurrentThread`. The reference port measured a visible frame hitch on every critical hit
before this was moved off the frame path.

The shape:

```
worker  --mpsc::Sender<VibeCmd>-->  "vibe" thread
                                     - attaches to the JVM ONCE, stays attached for life
                                     - caches the Vibrator service object as a GlobalRef
                                     - caches the method ids
                                     - blocks on recv(); the binder call happens here
```

Rules:

- **Attach once, never detach.** Attach/detach per call is most of the cost.
- **Cache the service object as a `GlobalRef`**, and cache the method ids. A local ref does not
  survive the call it was created in.
- **Send, never wait.** `try_send` on a bounded channel and drop on full. A dropped buzz is
  invisible; a blocked frame is not.
- **Coalesce.** Ten hits in one frame is one buzz. Drop duplicates within a short window in the
  vibe thread, not in the caller.
- Respect the system haptics setting and offer your own toggle.

The same pattern applies to **any** synchronous platform service you need per-frame-ish: one
long-lived attached thread, cached refs, a channel, and no back-pressure on the caller.

## 6. Audio: MIDI must be rendered in-process

**(A)** A desktop build of a wie-family core typically routes the guest's MIDI to the host's
MIDI sequencer. **There is no such thing on Android.** So the host must contain a synthesiser.

```
guest PCM clip  -> decode -> PCM sink (rodio or equivalent)
guest MIDI clip -> in-process SoundFont synth (e.g. oxisynth + a bundled .sf3) -> same sink
```

Consequences:

- **You must bundle a SoundFont.** Pick one whose licence you can actually satisfy and record
  it in `THIRD_PARTY_NOTICES` — and note that a statically linked LGPL synthesiser attaches a
  relink obligation to any binary you distribute, independent of your own repository's licence.
  This is a `port-shipping` audit item, not an afterthought.
- **Ownership must be lock-free from the audio callback's perspective.** The synth is written
  by the worker (note events) and read by the audio thread (render). Hand the synth to the sink
  once and drive it by messages; do not put a `Mutex` on the render path — an audio underrun is
  audible and instant.
- **Per-clip volume: key it by the stable guest pointer, not by the handle.** A guest can mint
  a *new* handle for a clip between "set volume" and "play", so a handle-keyed cache applies
  the volume to the previous clip and the in-game volume slider appears to do nothing (or to
  work one clip late). That fix is core-side; see `emulator-core`.
- Stop everything on the paused gate. Audio continuing in a pocket is the most-reported
  complaint a port can have.

## 7. The activity lifecycle

| callback | what the host must do |
|---|---|
| `resumed` (first time) | consume the `PendingBoot` and start the emulator; derive the logical size (see [thread-model.md](thread-model.md#2-deferred-boot-and-the-pre-rotation-size-race)) |
| `resumed` (subsequent) | reacquire the softbuffer surface; clear the paused flag |
| `suspended` | set the paused flag, release all held keys, stop audio, **persist anything unpersisted** |
| focus lost | release all held keys (the app is still resumed - a notification shade is enough) |
| window resized | recompute the layout, invalidate the blit column LUT, re-resolve control placements; do **not** change the guest framebuffer size |
| `destroyed` | clear the Java `instance`; do not rely on this running |

**★ Android kills backgrounded apps without warning.** Two design consequences that appear
elsewhere in this skill and are collected here because they are lifecycle facts:

- **Persist on every meaningful edit, not on a confirmation button.** The layout editor writes
  on every finger-up ([layout-editor.md](layout-editor.md#9-persistence)).
- **Storage must be synchronous** so a write is on disk when the call returns
  ([storage-and-config.md](storage-and-config.md)).

The window can be resized without the app being recreated (split screen, foldables, the
navigation bar appearing). Handle it; do not assume one window size per process.

## 8. Loading the game archive

Two supported shapes, and the choice is a distribution decision, not a technical one:

1. **Bundled asset** — read the archive out of the APK's assets via the asset manager at boot.
   Simple, and the app is self-contained. Only legitimate for a private build.
2. **Runtime file picker** — ship no game at all; the player supplies the archive on first run
   and it is copied into app-private storage. This is the only shape that can be published.

Either way, **fail loudly and specifically** when the archive is absent or unloadable. A build
whose asset slot is empty must not produce a running app with a black screen; that is
indistinguishable from the virtual-clock failure in `emulator-core` and will waste a day. Check
at build time where you can (`port-shipping`) and at boot in every case.

## 9. Logging

Set a restrictive default filter and raise only your own crate:

```rust
android_logger::init_once(
    Config::default().with_filter(FilterBuilder::new().parse("warn,<your-lib-name>=info").build())
);
```

**(A)** This is a performance setting, not a tidiness one — see
[thread-model.md](thread-model.md#11-log-volume-is-a-frame-budget-problem). Also compile the
noisy levels out in release with `release_max_level_info` on **both** `log` and `tracing`, and
enable `tracing`'s `log` feature so core events routed through `log` are actually visible when
you do want them.

**Never log user-identifying data.** Logcat is readable by tooling on the device. Log frame
*headers*, sizes and verdicts; never account identifiers, save payloads or transfer codes.

## 10. Symptom table

| Symptom | Cause |
|---|---|
| `UnsatisfiedLinkError` at launch | the three-way library name disagrees (§2) |
| Activity launches, stays blank, no useful log | manifest `android.app.lib_name` wrong, or two copies of the activity glue (§2) |
| `NoSuchMethodError` calling an Activity method | you are holding the Application, not the Activity (§3) |
| `ClassNotFoundException` for a class that is definitely in the APK | `FindClass` on a natively attached thread; use the context's class loader (§3) |
| `ClassNotFoundException` with a slashed name | `loadClass` wants dots (§3) |
| Dialog throws "not on the UI thread" | missing `runOnUiThread` (§1) |
| Dialog request during teardown crashes | `instance` not null-checked (§1) |
| Frame hitch on every hit / every buzz | vibrating inline instead of on the haptics thread (§5) |
| No music, or MIDI silent, while PCM works | expecting a host MIDI device; render in-process (§6) |
| Volume slider does nothing | volume keyed by handle, not by clip pointer (§6) |
| Audio keeps playing with the phone locked | paused gate not stopping the sink (§7) |
| First frame after cold start is the wrong orientation | preference applied after `onCreate` (§4) |
| Guest boots at the wrong aspect on one device | logical size derived from the first reported window size (§7, thread-model) |
| Player's layout lost after backgrounding | persisted on a confirm button, not on edit (§7) |
| Renaming the Java package builds fine and breaks dialogs | Rust resolves the class by name string (§1) |

## 11. Checklist

- [ ] The Java shim is `loadLibrary` + `instance` + static thread-hopping entry points, nothing
      more.
- [ ] The three library-name sites agree; the windowing crate carries the game-activity feature
      and the glue crate is not a direct dependency.
- [ ] Every JNI class lookup goes through the context's class loader with a dotted name.
- [ ] Orientation preference is host-owned and applied at `onCreate`.
- [ ] Haptics run on one always-attached thread with cached global refs and a lossy channel.
- [ ] MIDI is rendered in-process; the SoundFont's licence is recorded.
- [ ] Every lifecycle callback in §7 is handled, including focus loss.
- [ ] A missing or unloadable archive fails loudly at boot, and at build time where possible.
- [ ] Release log filter set; `release_max_level_*` on both facades; nothing identifying is
      logged.
