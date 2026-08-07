#!/usr/bin/env python3
"""Generate a closed-set CJK glyph table for a host font that only has ASCII.

THE PROBLEM  (A)
    A native emulator host draws its own chrome -- button labels, toasts, HUD
    text -- with a hand-rolled bitmap font, because the host has no text stack of
    its own and pulling one in for six words is absurd.  That font is ASCII.
    Draw a Korean, Japanese or Chinese label through it and you do not get tofu
    boxes; you get NOTHING.  The glyph lookup misses, the renderer advances the
    cursor and moves on, and the label is an invisible hole on the screen.  It
    looks like a layout bug, it gets reported as a layout bug, and it wastes an
    afternoon.

THE SOLUTION  (A)
    The host draws a CLOSED, KNOWN set of strings.  You do not need a font; you
    need the syllables in that set.  A full Hangul face is 11,172 syllables; a
    real port shipped fifteen.  So:

        word list  ->  derived character set  ->  1-bit cells at target size
                   ->  a const table + a covers() predicate
                   ->  a test that asserts covers() over every string the host
                       can actually produce

    Deriving the character set FROM the word list is the load-bearing part: a
    new word cannot forget to bring its glyphs.  Asserting coverage in a test is
    the other half, because the failure mode is invisible.

** RENDER DIRECTLY AT THE TARGET SIZE.  NEVER OVERSAMPLE AND DOWNSCALE.  (A)
    The instinct is to render at 4x and box-filter down for "quality".  It is
    exactly wrong for a 12-pixel cell.  A hinted screen face puts stems on the
    pixel grid deliberately; resampling averages that placement into grey, and
    thresholding grey gives you mush.  Dense CJK glyphs lose whole strokes.
    This tool always calls the rasteriser at the target point size and only ever
    translates the result -- it never scales it.
    Corollary from the same measurement: there is a floor.  Below roughly 12 px
    a dense syllable has more strokes than rows and no font choice saves it.
    If --size 10 produces mush, the answer is a bigger cell, not a better font.

OUTPUT
    A dependency-free Rust source file: no `use`, no `//!` inner doc comments,
    so it can be `include!`d by a standalone `rustc --test` selftest as well as
    compiled into the app.  It exposes

        pub const CELL_W / CELL_H / WORDS / GLYPHS
        pub fn glyph(c: char) -> Option<&'static [uN; CELL_H]>
        pub fn covers(s: &str) -> bool

    Row bit order: MSB is the LEFTMOST pixel.  Row storage width is chosen from
    the cell width (<=8 -> u8, <=16 -> u16, <=32 -> u32).

KNOWN LIMITS
    * Monochrome 1-bit only.  No anti-aliasing: the host's blitter is a bitmask.
    * Fixed cell, no per-glyph advance/kerning.  CJK is nearly monospaced at this
      size so it does not matter; Latin in the same table would look wrong, which
      is why --skip-ascii exists.
    * Vertical alignment is by ink bounding box, not by the font's baseline, so
      mixing this table with an ASCII font means bottom-aligning the two runs in
      the renderer (their cell heights differ).
    * .ttc collections need --font-index.

REQUIRES
    Pillow  ->  python -m pip install Pillow
"""

from __future__ import annotations

import os
import sys

# This tools directory contains `dis.py`, which shadows the stdlib `dis` that
# `inspect` needs. Pin the real one before any third-party import. (See the same
# guard in extract_packets.py for the full explanation.)
_HERE = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")
if "dis" not in sys.modules:
    _saved_path = sys.path[:]
    sys.path[:] = [p for p in sys.path
                   if os.path.abspath(p or ".").replace("\\", "/") != _HERE]
    try:
        import dis as _stdlib_dis  # noqa: F401
    except Exception:  # pragma: no cover
        pass
    finally:
        sys.path[:] = _saved_path

import argparse  # noqa: E402

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit(
        "error: Pillow is required to rasterise glyphs.\n"
        "       install it with:  python -m pip install Pillow"
    )


def storage_for(width):
    if width <= 8:
        return "u8", 8
    if width <= 16:
        return "u16", 16
    if width <= 32:
        return "u32", 32
    sys.exit("error: cell width %d exceeds 32; a wider row type is not supported "
             "(and a 33-pixel bitmap font is not what you want)." % width)


def render_cell(font, ch, cell_w, cell_h, threshold, valign, dx, dy):
    """Rasterise ONE character at the font's own size and place it in the cell.

    The scratch canvas is bigger than the cell so that ink which overflows can be
    measured and reported.  Nothing is ever resampled -- only translated.
    """
    pad_x, pad_y = cell_w * 2, cell_h * 2
    scratch = Image.new("L", (cell_w + pad_x * 2, cell_h + pad_y * 2), 0)
    draw = ImageDraw.Draw(scratch)
    draw.text((pad_x, pad_y), ch, font=font, fill=255)

    bbox = scratch.getbbox()
    if bbox is None:
        return None, (0, 0)

    ink_w = bbox[2] - bbox[0]
    ink_h = bbox[3] - bbox[1]

    ox = bbox[0] - (cell_w - ink_w) // 2 - dx
    if valign == "top":
        oy = bbox[1] - dy
    elif valign == "bottom":
        oy = bbox[3] - cell_h - dy
    else:  # center
        oy = bbox[1] - (cell_h - ink_h) // 2 - dy

    rows = []
    px = scratch.load()
    for y in range(cell_h):
        bits = 0
        sy = oy + y
        for x in range(cell_w):
            sx = ox + x
            if 0 <= sx < scratch.width and 0 <= sy < scratch.height and px[sx, sy] >= threshold:
                bits |= 1 << x
        rows.append(bits)
    return rows, (ink_w, ink_h)


def notdef_signature(font, cell_w, cell_h, threshold, valign, dx, dy):
    """Work out what THIS font draws for a character it does not have.

    A rasteriser does not report a missing glyph: it draws the font's .notdef,
    which is usually a hollow box (tofu) and occasionally nothing at all.  A
    tofu has ink, so an 'is the cell blank' check passes it happily and you ship
    a table of identical boxes.  Render three codepoints no real font covers and
    take the bitmap at least two of them agree on; anything matching it later is
    a MISSING glyph, not a glyph.
    """
    from collections import Counter
    sigs = []
    for probe in ("\ue05f", "\U0010fffd", "\ue7f3"):
        try:
            rows, _ink = render_cell(font, probe, cell_w, cell_h, threshold, valign, dx, dy)
        except Exception:
            continue
        if rows and any(rows):
            sigs.append(tuple(rows))
    if not sigs:
        return None
    sig, count = Counter(sigs).most_common(1)[0]
    return sig if count >= 2 else None


def pack_rows(rows, cell_w, bits):
    """Convert per-row 'bit x set' masks (LSB = x0) into MSB-is-leftmost words."""
    out = []
    for bitmask in rows:
        word = 0
        for x in range(cell_w):
            if bitmask & (1 << x):
                word |= 1 << (bits - 1 - x)
        out.append(word)
    return out


def rust_char_literal(ch):
    code = ord(ch)
    if ch == "\\":
        return "'\\\\'"
    if ch == "'":
        return "'\\''"
    if 0x20 <= code < 0x7F:
        return "'%s'" % ch
    return "'\\u{%x}'" % code


def rust_string_literal(s):
    out = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        else:
            out.append(ch)
    return '"%s"' % "".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="gen_glyphs.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Generate a closed-set CJK bitmap glyph table (Rust) for a host "
                    "renderer whose built-in font is ASCII only.",
        epilog="Regenerate this file whenever you add a word the host draws, and keep "
               "the emitted covers() assertion in a test. A missing glyph does not "
               "render tofu -- it renders nothing, and nothing is invisible.",
    )
    ap.add_argument("--words", default="",
                    help="comma-separated list of the exact words the host draws. Non-ASCII "
                         "is expected here; quote the argument. The character set is derived "
                         "from this list, so a new word cannot forget its glyphs.")
    ap.add_argument("--words-file",
                    help="file with one word per line (UTF-8); merged with --words")
    ap.add_argument("--font", required=True, help="path to a .ttf / .otf / .ttc")
    ap.add_argument("--font-index", type=int, default=0, help="face index inside a .ttc")
    ap.add_argument("--size", type=int, default=12,
                    help="point size AND cell size in pixels (default 12). Rendering happens "
                         "at exactly this size; nothing is ever downscaled.")
    ap.add_argument("--cell-w", type=int, help="cell width if not square (default: --size)")
    ap.add_argument("--cell-h", type=int, help="cell height if not square (default: --size)")
    ap.add_argument("--threshold", type=int, default=110,
                    help="grey level (0-255) at or above which a pixel is set (default 110)")
    ap.add_argument("--valign", choices=("center", "top", "bottom"), default="center",
                    help="vertical placement of the ink inside the cell (default center)")
    ap.add_argument("--dx", type=int, default=0, help="extra horizontal nudge, pixels")
    ap.add_argument("--dy", type=int, default=0, help="extra vertical nudge, pixels")
    ap.add_argument("--skip", default="", help="characters to exclude from the table")
    ap.add_argument("--skip-ascii", action="store_true",
                    help="exclude ASCII -- normally correct, because the host's own 5x7 font "
                         "already draws it and mixing metrics looks wrong")
    ap.add_argument("--module-doc", default="",
                    help="one-line comment to put at the top of the generated file")
    ap.add_argument("--out", required=True, help="Rust file to write, e.g. src/glyphs.rs")
    ap.add_argument("--preview", nargs="?", const="", metavar="OUT.PNG",
                    help="also write a magnified contact sheet of the rendered cells; "
                         "with no value, writes <out>.preview.png")
    ap.add_argument("--allow-clipped", action="store_true",
                    help="do not fail when a glyph's ink is wider or taller than the cell")
    args = ap.parse_args(argv)

    words = [w for w in (x.strip() for x in args.words.split(",")) if w]
    if args.words_file:
        with open(args.words_file, "r", encoding="utf-8") as fh:
            words.extend(w for w in (line.strip() for line in fh) if w and not w.startswith("#"))
    if not words:
        sys.exit("error: give --words and/or --words-file. The character set is DERIVED from "
                 "the word list on purpose, so that a new word cannot forget its glyphs.")

    skip = set(args.skip)
    chars = set()
    for w in words:
        for ch in w:
            if ch in skip:
                continue
            if args.skip_ascii and ord(ch) < 0x80:
                continue
            if ch.isspace():
                continue
            chars.add(ch)
    if not chars:
        sys.exit("error: the word list produced no characters to render (everything skipped).")

    cell_w = args.cell_w or args.size
    cell_h = args.cell_h or args.size
    rust_ty, bits = storage_for(cell_w)

    font_path = args.font.replace("\\", "/")
    if not os.path.exists(font_path):
        sys.exit("error: font not found: %s" % font_path)
    try:
        font = ImageFont.truetype(font_path, args.size, index=args.font_index)
    except Exception as exc:
        sys.exit("error: could not open %s at size %d index %d: %s"
                 % (font_path, args.size, args.font_index, exc))

    notdef = notdef_signature(font, cell_w, cell_h, args.threshold,
                              args.valign, args.dx, args.dy)

    ordered = sorted(chars, key=ord)
    table = []
    empty = []
    tofu = []
    clipped = []
    for ch in ordered:
        rows, ink = render_cell(font, ch, cell_w, cell_h, args.threshold,
                                args.valign, args.dx, args.dy)
        if rows is None or not any(rows):
            empty.append(ch)
            continue
        if notdef is not None and tuple(rows) == notdef:
            tofu.append(ch)
            continue
        if ink[0] > cell_w or ink[1] > cell_h:
            clipped.append((ch, ink))
        table.append((ch, pack_rows(rows, cell_w, bits)))

    # ---- coverage assertion: the whole point of the tool ------------------
    if empty or tofu:
        parts = []
        if empty:
            parts.append("%d rendered COMPLETELY BLANK: %s"
                         % (len(empty), ", ".join("U+%04X" % ord(c) for c in empty)))
        if tofu:
            parts.append("%d rendered as this font's .notdef box (tofu): %s"
                         % (len(tofu), ", ".join("U+%04X" % ord(c) for c in tofu)))
        sys.exit(
            "error: the font does not cover %d requested character(s).\n"
            "       %s\n"
            "\n"
            "       This is the failure this tool exists to prevent. A rasteriser does\n"
            "       not report a missing glyph: it silently draws .notdef, or nothing.\n"
            "       A blank cell is worse than tofu -- the host renderer draws nothing,\n"
            "       advances the cursor, and the label becomes an INVISIBLE HOLE that\n"
            "       gets reported as a layout bug.\n"
            "\n"
            "       Causes, in order of likelihood:\n"
            "         1. the font has no glyph for that codepoint. Pick a face that\n"
            "            actually covers the script; a Latin UI font will not, and it\n"
            "            will not tell you so.\n"
            "         2. a .ttc collection: the right face may be another --font-index\n"
            "         3. --size is below the floor for these strokes; try 12 or 14\n"
            "         4. --threshold is too high for a light-weight face; try 90\n"
            % (len(empty) + len(tofu), "\n       ".join(parts))
        )

    if clipped and not args.allow_clipped:
        detail = ", ".join("U+%04X %s (ink %dx%d)" % (ord(c), c, w, h) for c, (w, h) in clipped)
        sys.exit(
            "error: %d glyph(s) do not fit the %dx%d cell and would be cut off: %s\n"
            "       Increase --cell-w/--cell-h, or pass --allow-clipped if the crop is\n"
            "       cosmetic and you have looked at --preview to confirm it."
            % (len(clipped), cell_w, cell_h, detail)
        )

    # ---- emit -------------------------------------------------------------
    hexw = bits // 4
    lines = []
    lines.append("// GENERATED by gen_glyphs.py -- do not edit by hand.")
    lines.append("// Regenerate after changing WORDS:")
    lines.append("//   python \"${CLAUDE_PLUGIN_ROOT}/tools/gen_glyphs.py\" \\")
    lines.append("//     --words %s \\" % ",".join(words))
    # Echo the output path only when it is repo-relative. An absolute path here
    # would bake one developer's home directory into a committed source file --
    # exactly the finding publish_audit.py reports as `absolute-user-path`.
    _out_echo = args.out.replace("\\", "/")
    if os.path.isabs(_out_echo):
        _out_echo = "src/" + os.path.basename(_out_echo)
    lines.append("//     --font <your-font> --size %d --threshold %d --out %s"
                 % (args.size, args.threshold, _out_echo))
    lines.append("//")
    if args.module_doc:
        lines.append("// %s" % args.module_doc)
        lines.append("//")
    lines.append("// Closed-set glyph table. The host's built-in font is ASCII only; a")
    lines.append("// character missing from this table renders as NOTHING, not as tofu, so")
    lines.append("// covers() must be asserted in a test over every string the host can draw.")
    lines.append("//")
    lines.append("// Row bit order: MSB is the LEFTMOST pixel. Cell is %dx%d." % (cell_w, cell_h))
    lines.append("// NOTE: no `use` and no `//!` doc comments -- this file is `include!`d by a")
    lines.append("// standalone `rustc --test` selftest as well as compiled into the app.")
    lines.append("")
    lines.append("#[allow(dead_code)]")
    lines.append("pub const CELL_W: usize = %d;" % cell_w)
    lines.append("#[allow(dead_code)]")
    lines.append("pub const CELL_H: usize = %d;" % cell_h)
    lines.append("")
    lines.append("// The exact word list this table was generated from. Assert coverage over")
    lines.append("// this in a test, and over every runtime-composed string as well.")
    lines.append("#[allow(dead_code)]")
    lines.append("pub const WORDS: [&str; %d] = [%s];"
                 % (len(words), ", ".join(rust_string_literal(w) for w in words)))
    lines.append("")
    lines.append("#[allow(dead_code)]")
    # `static`, deliberately, not `const`: glyph() hands out &'static references
    # into this table, and a reference into a `const` is a reference into a
    # freshly materialised temporary, which does not live long enough.
    lines.append("pub static GLYPHS: [(char, [%s; %d]); %d] = [" % (rust_ty, cell_h, len(table)))
    for ch, rows in table:
        row_txt = ", ".join(("0x%0" + str(hexw) + "x") % r for r in rows)
        lines.append("    (%s, [%s])," % (rust_char_literal(ch), row_txt))
    lines.append("];")
    lines.append("")
    lines.append("#[allow(dead_code)]")
    lines.append("pub fn glyph(c: char) -> Option<&'static [%s; CELL_H]> {" % rust_ty)
    lines.append("    let mut i = 0;")
    lines.append("    while i < GLYPHS.len() {")
    lines.append("        if GLYPHS[i].0 == c {")
    lines.append("            return Some(&GLYPHS[i].1);")
    lines.append("        }")
    lines.append("        i += 1;")
    lines.append("    }")
    lines.append("    None")
    lines.append("}")
    lines.append("")
    table_has_ascii = any(ord(ch) < 0x80 for ch, _rows in table)
    lines.append("/// True when every character of `s` can actually be drawn.")
    if table_has_ascii:
        lines.append("/// This table contains ASCII glyphs of its own, so every character must be")
        lines.append("/// present -- nothing is delegated to the host's built-in font.")
        body = "    s.chars().all(|c| glyph(c).is_some())"
    else:
        lines.append("/// ASCII is delegated to the host's own built-in font (this table was")
        lines.append("/// generated with --skip-ascii, or contains no ASCII), so only non-ASCII")
        lines.append("/// characters are looked up here.")
        body = "    s.chars().all(|c| c.is_ascii() || glyph(c).is_some())"
    lines.append("#[allow(dead_code)]")
    lines.append("pub fn covers(s: &str) -> bool {")
    lines.append(body)
    lines.append("}")
    lines.append("")

    out_path = args.out.replace("\\", "/")
    d = os.path.dirname(out_path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))

    # ---- preview ----------------------------------------------------------
    preview_path = None
    if args.preview is not None:
        preview_path = args.preview or (os.path.splitext(out_path)[0] + ".preview.png")
        preview_path = preview_path.replace("\\", "/")
        zoom = 6
        cols = max(1, int(len(table) ** 0.5 + 0.999))
        rows_n = (len(table) + cols - 1) // cols
        gap = 3
        sheet = Image.new("RGB",
                          (cols * (cell_w * zoom + gap) + gap,
                           rows_n * (cell_h * zoom + gap) + gap),
                          (30, 30, 34))
        px = sheet.load()
        for i, (_ch, rows) in enumerate(table):
            r, c = divmod(i, cols)
            ox = gap + c * (cell_w * zoom + gap)
            oy = gap + r * (cell_h * zoom + gap)
            for y in range(cell_h):
                word = rows[y]
                for x in range(cell_w):
                    on = word & (1 << (bits - 1 - x))
                    col = (235, 235, 235) if on else (58, 58, 66)
                    for zy in range(zoom):
                        for zx in range(zoom):
                            px[ox + x * zoom + zx, oy + y * zoom + zy] = col
        d = os.path.dirname(preview_path)
        if d:
            os.makedirs(d, exist_ok=True)
        sheet.save(preview_path)

    print("words   : %d  (%s)" % (len(words), ", ".join(words)))
    print("glyphs  : %d  cell %dx%d  storage [%s; %d]" % (len(table), cell_w, cell_h, rust_ty, cell_h))
    print("font    : %s  size %d  index %d  threshold %d"
          % (font_path, args.size, args.font_index, args.threshold))
    print("wrote   : %s" % out_path)
    if preview_path:
        print("preview : %s   <- LOOK AT THIS before shipping; mush at this size is silent"
              % preview_path)
    print()
    print("Now add a test that asserts coverage over every string the host can draw,")
    print("not only over WORDS -- runtime-composed strings are where the hole appears.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
