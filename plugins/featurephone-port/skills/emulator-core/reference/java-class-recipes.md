# Adding KTF/WIPI Java classes, methods and fields

Two resolution paths exist for a Java class name, and a porter must know **which one** a missing
name is on before doing anything. The failure modes are completely different.

## Contents

1. [The two resolution paths](#1-the-two-resolution-paths)
2. [How a missing class presents on each path](#2-how-a-missing-class-presents-on-each-path)
3. [Recipe: add an emulator-provided class](#3-recipe-add-an-emulator-provided-class)
4. [Recipe: add a method to an existing class](#4-recipe-add-a-method-to-an-existing-class)
5. [Recipe: add a field — and the only reliable GC root](#5-recipe-add-a-field--and-the-only-reliable-gc-root)
6. [Initialisation ordering](#6-initialisation-ordering)
7. [Stub honestly](#7-stub-honestly)

---

## 1. The two resolution paths

**Path 1 — emulator-provided WIPI/MIDP classes.** Rust class prototypes registered up front, before
the guest runs:

```rust
// wie_ktf/src/runtime/java/jvm_support.rs
let protos = [
    wie_wipi_java::get_protos().into(),
    wie_midp::get_protos().into(),
];
```

These are the platform classes: handset services, display, graphics, media, networking. If the
guest asks for one you have not registered, **you** are missing it.

**Path 2 — the game's own classes**, compiled into the native member and resolved *lazily* by the
KTF class loader (`classes/net/wie/ktf_class_loader.rs::findClass`). It heap-allocates a C string
for the class name, calls the guest's own `fnGetClass` with it, and registers the returned raw
`JavaClassDefinition`. If the guest does not know the name, it returns `null`.

**Deciding which path you are on:** if the name is a platform namespace
(`org/kwis/msp/...`, `javax/microedition/...`, `java/lang/...`) it is path 1 and it is yours to add.
If it is the game's own namespace, it is path 2 and a failure means either the class genuinely is
not in the image or your loader is not reaching it — do not "add" it.

## 2. How a missing class presents on each path

| Path | Presentation | Why it is easy to miss |
|---|---|---|
| 1 (emulator-provided) | JVM `ClassNotFoundException` | It is a **Java** exception, so the guest can *catch* it. The game then takes a fallback branch and you see a silent behaviour bug — a feature quietly not working — rather than a crash. |
| 2 (guest's own) | `findClass` returns null, then a `NoClassDefFoundError` or a guest-side null deref | Usually means the loader is looking at the wrong image, or the native member's BSS size is wrong so the class table is truncated. |

**(A)** Because path-1 misses can be swallowed, **log every `ClassNotFoundException` at warn level
in the class loader, unconditionally**, even though the guest may legitimately catch some of them.
A noisy log you can filter beats a silent branch you cannot see.

## 3. Recipe: add an emulator-provided class

A minimal real example — a handset service the guest calls and whose effect the host does not need
to reproduce:

```rust
// wie_wipi_java/src/classes/org/kwis/msp/handset/backlight.rs
pub struct BackLight;

impl BackLight {
    pub fn as_proto() -> WieJavaClassProto {
        WieJavaClassProto {
            name: "org/kwis/msp/handset/BackLight",
            parent_class: Some("java/lang/Object"),
            interfaces: vec![],
            methods: vec![JavaMethodProto::new(
                "alwaysOn",
                "()V",
                Self::always_on,
                MethodAccessFlags::STATIC,
            )],
            fields: vec![],
            access_flags: Default::default(),
        }
    }

    async fn always_on(_: &Jvm, _: &mut WieJvmContext) -> JvmResult<()> {
        tracing::warn!("stub org.kwis.msp.handset.BackLight::alwaysOn()");
        Ok(())
    }
}
```

Then register it:

```rust
// wie_wipi_java/src/lib.rs
pub fn get_protos() -> [WieJavaClassProto; 36] {   // <-- BUMP THE ARRAY LENGTH
    [
        // ...
        classes::org::kwis::msp::handset::BackLight::as_proto(),
    ]
}
```

⚠ **The array length is a hard-coded constant.** Forgetting to bump it is a compile error, which is
the good case; the bad case is a stray edit that bumps it without adding an entry.

Checklist for the proto:

- **The descriptor string is the ABI.** `"()V"`, `"(II)Z"`, `"(Ljava/lang/String;)V"`. A
  descriptor that does not match the guest's call site fails at *resolution*, and the message names
  the method but not the mismatch.
- **`MethodAccessFlags::STATIC` matters** — a non-static method receives the instance as its first
  Java argument, so getting this wrong shifts every parameter by one.
- **`parent_class: Some("java/lang/Object")`** unless the guest genuinely subclasses something.
- Constructors are `"<init>"` with `"(...)V"`; static initialisers are `"<clinit>"`.

## 4. Recipe: add a method to an existing class

Append a `JavaMethodProto` to the class's `methods` vec and write the `async fn`. Nothing is
positional here — resolution is by `(name, descriptor)` — so unlike the WIPI-C tables, ordering is
free.

Signature shape:

```rust
async fn set_something(jvm: &Jvm, _ctx: &mut WieJvmContext, this: ClassInstanceRef<Self>, v: i32)
    -> JvmResult<()>
{
    jvm.put_field(&this, "something", "I", v).await?;
    Ok(())
}
```

## 5. Recipe: add a field — and the only reliable GC root

**The problem this solves (A):** you hold a guest object from Rust — a pre-allocated exception, a
cached buffer, a singleton — as a raw `u32` guest pointer. The collector roots **class static
fields, thread frames, and interned strings**. A `u32` in a Rust `static` is none of those. The next
collection frees your object and you are handing out a dangling guest pointer, with a failure that
appears arbitrarily later and nowhere near the cause.

**The fix:** park it in a static field of a class you registered.

```rust
// classes/net/wie/ktf_class_loader.rs — add to the proto's `fields`
fields: vec![
    JavaFieldProto::new(oom::ROOT_FIELD, oom::WIE_ERROR_DESCRIPTOR, FieldAccessFlags::STATIC),
],
```

```rust
// write it once, while the heap is healthy
jvm.put_static_field(oom::ROOT_CLASS, oom::ROOT_FIELD, oom::WIE_ERROR_DESCRIPTOR, instance)
   .await?;
```

**A static field of a registered class is the only reliable GC root available to a Rust-held guest
object.** Note that some MIDP paths call the collector *directly* rather than only on allocation
pressure, so "the heap is not under pressure" is not protection.

Pick the host class deliberately: it must be a class that is registered before your object is
created and never unloaded. The class loader's own class is a good default because it exists for
the whole session by construction.

## 6. Initialisation ordering

**(A)** Anything you pre-allocate to survive a resource exhaustion must be allocated **before the
largest allocation burst the emulator makes**. On the KTF path that burst is the class loader's
constructor, which reads the whole jar and loads the multi-megabyte native member.

```
oom::init(&jvm).await?;                 // <-- pre-allocate here, heap is healthy
KtfClassLoader::<init>(...).await?;     // <-- the burst
```

Get this backwards and the safety net is allocated *after* the window it exists to cover. Write the
ordering constraint as a comment at both sites, not just one — the constraint is invisible from
either site alone.

## 7. Stub honestly

For a class whose behaviour the host does not need to reproduce, a warn-logging no-op is the right
implementation and should say so:

```rust
tracing::warn!("stub org.kwis.msp.handset.BackLight::alwaysOn()");
```

**(A)** Use `warn!`, not `info!` or `debug!`:

- `warn!` survives release log filtering, so a stub reached in a shipped build is still visible.
- It reads as "this returned a made-up answer" in a log someone else is reading.
- But **audit every stub on a per-frame path before shipping** — see
  [hardening-batch.md](hardening-batch.md) §5. A `warn!` in a routine the guest calls forty times a
  second is a frame-budget bug, not a diagnostic. Log it once with a latch:

```rust
static ONCE: AtomicBool = AtomicBool::new(false);
if !ONCE.swap(true, Ordering::Relaxed) {
    tracing::warn!("stub …");
}
```
