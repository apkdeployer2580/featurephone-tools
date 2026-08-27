# Graphics: killing the full-framebuffer round-trip

Upstream's WIPI graphics path costs **call count × full screen**, not pixels drawn. Where a title
issues hundreds of draw calls a frame that is the difference between shipping and not — and **where it
does not, this whole file is the wrong layer**, so §1 is a measurement, not an argument.

**Scope.** This file owns the **emulator's own** draw path: the framebuffer round-trip, row bands, the
canvas, and the equivalence tests that make rewriting it safe. The **guest's** pixel loops — a game
that takes the framebuffer pointer once and rasterises in its own ARM code — are
[guest-pixel-loops.md](guest-pixel-loops.md), including the census that finds them, the hook
discipline, and the identity proof. The frame budget both of them have to fit inside is
[frame-pacing.md](frame-pacing.md).

## Contents

1. [Measure first: count the platform draw calls, per screen class](#1-measure-first-count-the-platform-draw-calls-per-screen-class)
2. [Layer 1: row bands](#2-layer-1-row-bands)
3. [Layer 2: read directly into typed pixels](#3-layer-2-read-directly-into-typed-pixels)
4. [Layer 3: fills — write only the rectangle (16 bpp), covering-fill band otherwise](#4-layer-3-fills--write-only-the-rectangle-16-bpp-covering-fill-band-otherwise)
5. [Layer 4: span intersection and raw row copies in the canvas](#5-layer-4-span-intersection-and-raw-row-copies-in-the-canvas)
6. [The one accepted semantic delta](#6-the-one-accepted-semantic-delta)
7. [Equivalence tests — the whole safety argument](#7-equivalence-tests--the-whole-safety-argument)
8. [Host-side blit](#8-host-side-blit)
9. [The offscreen-copy hook](#9-the-offscreen-copy-hook)

---

## 1. Measure first: count the platform draw calls, per screen class

**(A) Before optimising this path, count what goes through it.** Two counters — platform draw calls
per frame, and guest instructions per frame — decide which layer you are allowed to work on, and on a
real title the answer **differs per screen class**:

| Reading | What it means | Where the work is |
|---|---|---|
| Hundreds to thousands of platform draw calls per frame | the game draws through the platform API | this file |
| A handful of draw calls, millions of guest instructions per frame | the game took the framebuffer pointer once and rasterises in its own code | [guest-pixel-loops.md](guest-pixel-loops.md) |

> **REFUTED.** *Previously documented (this file's own opening):* "Upstream's WIPI graphics path costs
> **call count × full screen** … on a title that issues hundreds of draw calls a frame this is the
> difference between shipping and not" — stated as a property of the title. **Refuted by** counting
> the calls: a real **field** frame in the reference port issued **12** platform draw calls and
> **zero** image blits while executing **2.46 M guest instructions**, so the round-trip work barely
> participates in the field at all. **Why the wrong reading was attractive:** the round-trip really is
> enormous *per call*, the arithmetic is compelling, and the fix is real — it simply was not the cost
> on the screen the player was complaining about. **Consequence if carried forward:** you spend a week
> on the framebuffer path and the field still stutters, because the game rasterises in guest code that
> your graphics layer never sees.
>
> **The headline is not wrong; it is screen-specific.** The same title's menu, panel and dialogue
> screens issued **1,650–2,450 fill-rect calls per frame**, and the worst single screen — a network
> modal — cost roughly **112 ms of host work per frame** on a 2018-era low-end phone, most of it band
> round-trips (**(B)** the per-frame host cost from the same census; the device share of the tick is
> an on-device counter,
> [frame-pacing.md §13](frame-pacing.md#13-reading-the-pacing-gauge-line)). That is squarely this file's problem. **Both answers occur in one title**, so count per
> screen class and never generalise from one frame.
>
> **Note which number belongs to this layer: host cost per frame, not frame rate.** The residual few
> fps on those same screens was the guest's own 244 ms frame timer with the host thread 78 % idle —
> removing the round-trip *and* fixing the pacing left that screen's frame rate essentially where it
> was, and the only thing that ever moved it further was overriding the guest's constant. See
> [frame-pacing.md §15](frame-pacing.md#15-what-is-left-after-pacing-and-is-not-yours-to-fix).
>
> **(B)** Both counts above come from one census — a headless probe driven to each screen class with
> the draw-call trace and the work gauge on; the method and the full breakdown are
> [guest-pixel-loops.md §1](guest-pixel-loops.md#1-first-count-the-platform-draw-calls-per-frame--per-screen-class).
> This block keeps them anyway, because the belief it refutes is this file's own opening sentence.

**What the porter sees.** "Maps with a lot to render stutter compared to sparse maps." A
full-screen effect hitches. A panel or dialogue screen is dramatically slower than the field. Frame
time tracks the *number* of draw calls, not the pixel count.

**What upstream actually did on every WIPI draw call:**

1. read the **entire** destination framebuffer out of guest memory (for a 496×240 16 bpp screen,
   ~238 KB);
2. collect it into a second `Vec` (another ~238 KB copy);
3. for blits, do the same for the **whole** source sprite sheet;
4. write the entire framebuffer back on `Drop`.

**A 1×1 pixel write paid roughly 0.7 MB of memcpy plus two large `Vec` allocations.**

On a panel screen at 1,650–2,450 such calls per frame that is the entire frame budget, several times
over. On a field frame at 12 calls it is noise. Same build, same day — which is the whole reason the
count comes first.

## 2. Layer 1: row bands

Give the framebuffer wrapper a set of *band* accessors, so each call site declares only the rows it
touches.

```rust
impl FrameBuffer {
    /// Clamp a requested (y, h) to the framebuffer, returning the actual band.
    fn clamp_rows(&self, y: i32, h: i32) -> Option<(u32 /*y0*/, u32 /*rows*/)>;

    /// Raw guest bytes for a band.
    fn data_rows(&self, ctx: &mut dyn WIPICContext, y: i32, h: i32) -> Result<(u32, Vec<u8>)>;

    /// Typed pixels for a band -- see §3.
    fn pixel_rows<P: Pixel>(&self, ctx: &mut dyn WIPICContext, y: i32, h: i32)
        -> Result<(u32, Vec<P>)>;

    /// A Canvas over a band that translates y and clip internally.
    fn canvas_rows<'a>(&self, ctx: &'a mut dyn WIPICContext, y: i32, h: i32)
        -> Result<FramebufferCanvas<'a>>;

    /// Same, for a source image.
    fn image_rows(&self, ctx: &mut dyn WIPICContext, y: i32, h: i32) -> Result<(u32, Image)>;
}
```

**The design rule that makes this tractable (A):** `FramebufferCanvas` implements `Canvas`
**directly** and translates y and the clip rectangle internally, so **every call site keeps
absolute coordinates**. You are not rewriting the drawing code; you are wrapping it. Without this
the conversion is a hundred coordinate bugs.

Three call-site rules that are not optional:

- **`draw_string` keeps the full canvas.** Glyph extent is not knowable before rendering, so the
  band cannot be computed. Do not guess a band from the font size.
- **`copy_area` must snapshot the source band *before* creating the destination canvas**, or an
  overlapping copy reads pixels this same call already wrote. The original semantics are
  pre-copy; preserve them.
- **Any call reading from a source band must subtract the returned `src_y0` from its source y.**
  `data_rows`/`image_rows` return the clamped origin precisely so the caller can do this; ignoring
  the return value is the single most common porting bug here and it produces a vertical shift that
  only shows near the screen edges.

## 3. Layer 2: read directly into typed pixels

The second full-size copy comes from reading bytes and then collecting them into a `Vec<P>`. Read
the guest bytes *straight into* the typed vector's backing store:

```rust
fn pixel_rows<P: Pod>(&self, ctx: &mut dyn WIPICContext, y: i32, h: i32) -> Result<(u32, Vec<P>)> {
    let (y0, rows) = self.clamp_rows(y, h).ok_or(...)?;
    let count = (rows * self.width) as usize;
    let mut out: Vec<P> = vec![P::zeroed(); count];
    // casting DOWN to u8 can never fail on alignment
    ctx.read_bytes_into(self.addr_of_row(y0), cast_slice_mut(&mut out))?;
    Ok((y0, out))
}
```

**Why not `cast_vec` from a byte vec.** `Vec<u8> → Vec<u16>` requires the allocation to be aligned
to 2, and an allocator handing back alignment-1 memory is legal. Casting a `&mut [P]` **down** to
`&mut [u8]` is always sound; that is the direction to use.

## 4. Layer 3: fills — write only the rectangle (16 bpp), covering-fill band otherwise

**★ SUPERSEDED, and the newer method is the primary path (A).** For a 16 bpp destination, write
**only the rectangle's own pixels**: no band read, no band write, no allocation. Fall back to the
banded canvas below only for other depths.

**Why it supersedes the band.** The banded path costs roughly **1 KB read + 1 KB written + 1 KB
zeroed per call to change ~16 bytes** — and the call sites that dominate are the panel screens issuing
1,650–2,450 fills per frame (§1). The covering-fill trick below removes one of those three kilobytes,
for the subset of fills that span the full width. Writing the rectangle removes all three, for every
fill.

**Why it is safe to claim byte identity, and how to pin it (A):**

- For 16 bpp the colour round trip is the **identity across all 65 536 values** — check it
  exhaustively in a test, it runs in milliseconds. That is what licenses bypassing the canvas rather
  than merely hoping.
- Pin the new path against the old one with a table test that **deliberately reproduces the old path's
  asymmetric clamping** — negative origins, over-long spans, zero width/height, a rectangle wholly
  off-screen on each edge. **(B)** Fourteen cases in the reference port. If the two paths disagree
  anywhere, the *old* behaviour wins: you are optimising, not fixing.
- **Order the call site so the fast path is tried first and falls through**, rather than duplicating
  the clip arithmetic in two places.

**When the older method below is still the right answer:** any destination that is not 16 bpp, and any
fill whose colour handling is not an identity round trip in your build. Keep it; it is the fallback,
not dead code.

### The covering-fill band, retained as the fallback

A fill that spans the **full framebuffer width** repaints every pixel of its band, so reading the band
first is pure waste.

```rust
impl FrameBuffer {
    /// A canvas over a band whose contents are NOT read back from guest memory.
    /// Only legal when the caller will overwrite every pixel of the band.
    fn canvas_rows_uninit<'a>(&self, ctx: &'a mut dyn WIPICContext, y: i32, h: i32)
        -> Result<FramebufferCanvas<'a>>;

    /// i64 arithmetic so (i32::MIN, i32::MAX) cannot wrap into a false positive.
    fn fill_covers_band(&self, x: i32, w: i32) -> bool {
        w > 0 && x <= 0 && (x as i64 + w as i64) >= self.width as i64
    }
}
```

Guarded at the one call site that may use it:

```rust
let mut canvas = if framebuffer.fill_covers_band(x, w) {
    framebuffer.canvas_rows_uninit(context, y, h)?
} else {
    framebuffer.canvas_rows(context, y, h)?
};
```

**Why the guard must be conservative.** The band is written back **whole** on drop. An
over-permissive guard therefore does not produce a subtle artefact — it writes black rows across
the full width. The `i64` widening is not pedantry: a guest passing `x = i32::MIN, w = i32::MAX` is
exactly the input that wraps a naive `x + w` into a false positive.

Two tests pin the guard: the uninit band must match the read band byte-for-byte for covering fills
**including negative origins and over-long spans**, and the predicate must reject partial spans.

## 5. Layer 4: span intersection and raw row copies in the canvas

Inside the shared canvas implementation:

- **One span intersection instead of four per-pixel tests.** The per-pixel bounds check, the clip-x
  check, the clip-y check and the source-bounds check all collapse into a single intersection
  computed once per row, in `i64`.
- **RGB565 → RGB565 becomes a raw row `memcpy`** (`put_row_raw`). RGB565 carries no alpha, so
  blending *is* overwriting; there is nothing to compute.
- **`fill_rect` uses a row-slice `fill`** (`fill_span`). A full-screen fill on a 496×240 display
  drops from ~119 000 `put_pixel` calls to 240 row fills.
- **Alpha fast paths:** `a == 255` skips the background read and the float mix entirely; `a == 0`
  skips the pixel entirely.
- **XOR mode keeps the old per-pixel path.** Do not fast-path a mode whose result depends on the
  destination.

## 6. The one accepted semantic delta

Document deltas; do not hide them.

On an **ARGB** destination, a source pixel with `a == 0` used to rewrite the background RGB with
α = 255 — effectively a bug — and now is left alone. On **RGB565**, which is every path a
16 bpp WIPI title uses, the new code is **bit-identical** to the old.

State the delta in a comment at the fast path, with the reason it is acceptable and the pixel
format for which it is exact. A future reader hitting an ARGB artefact needs to find this in the
code, not in a changelog.

## 7. Equivalence tests — the whole safety argument

Every layer above is a rewrite of code that already worked. The only thing making that safe is a
test that compares old and new output directly.

```bash
cd vendor/wie && cargo test -p wie_backend -p wie_wipi_c
cd vendor/wie && cargo test --test test_helloworld   # boots a real archive and renders
```

What to assert:

- **Band vs full canvas**: the same draw call through `canvas_rows(y, h)` and through the full
  canvas must produce identical framebuffer bytes — run it for bands that clamp at the top, at the
  bottom, and for a zero-height band.
- **Uninit vs read band** for covering fills, including negative x and over-long width.
- **`fill_covers_band` rejects partial spans** — the negative case matters more than the positive.
- **Overlapping `copy_area`** produces pre-copy semantics.
- **End-to-end**: keep an upstream test archive in the tree that boots and renders, so a
  refactor that breaks the pipeline fails a test rather than a play session.

The end-to-end boot test is the cheapest high-value asset in the vendored tree. Keep it green.

## 8. Host-side blit

Not vendored, but the same class of waste and worth fixing in the same sitting: convert the 16 bpp
raw framebuffer **directly** to the host's packed pixel format instead of materialising an
intermediate `Vec<Color>`. At 16 bytes per pixel an intermediate vector for a 496×240 screen is
~1.9 MB of allocation and copy **per frame**.

## 9. The offscreen-copy hook

**(A) generic hook, title-specific use.** Add a note-and-read pair in the backend, written by the
WIPI framebuffer-copy implementation whenever source and destination differ:

```rust
// wie_backend
pub fn note_offscreen_copy(handle: u32, w: u16, h: u16);   // packs (handle<<32)|(w<<16)|h
pub fn last_offscreen_copy() -> Option<u64>;
```

Why it earns its place: a host that wants to find a guest-owned offscreen buffer — the buffer
behind a minimap, a portrait, a pre-rendered layer — cannot otherwise distinguish the *current* one
from stale buffers of previously visited states. The most recent copy is the answer, and the guest
tells you for free every time it blits.

This is also the hook that makes **differential verification** possible: render your host
reimplementation, fetch the guest's own buffer, and diff the pixels. Two rules:

- **Quantise your output through the same pixel-format round-trip** before comparing, or you are
  diffing precision, not correctness.
- **The check is usually gated on an explicit dump-frame list.** *Correction carried from the
  reference port:* its own documented invocation enabled the check with no frame list and therefore
  could print nothing. Say so in `--help` and in the example.

Differential verification against the guest is the strongest verification technique in this
methodology. What it usually verifies is a **host reimplementation of one of the game's own pixel
loops**, and that — the census that finds them, choosing the hook site, the exit-address proof
obligation, and why a matching frame hash can still prove nothing — is
[guest-pixel-loops.md](guest-pixel-loops.md). Two things from there are worth knowing before you plan
any of this work: a title commonly has **three or four distinct per-pixel loops** needing separate
hooks, the rarest of which is only findable by a **differential** profile (one run stopped before the
effect, one containing it); and **generic byte patterns encode a compiler's idioms, not a game's**, so
a pattern-matched hook that paid off on one carrier's compile can measure **exactly zero** on
another's ([boot-order.md](boot-order.md) §8c).
