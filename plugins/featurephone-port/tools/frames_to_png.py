#!/usr/bin/env python3
"""Turn a headless probe's raw framebuffer dumps into PNGs you can actually look at.

WHAT IT DOES
    Converts every raw frame dump in a directory to a PNG, and optionally builds
    one montage image of the whole run.  Geometry and pixel format come from the
    DUMP FILENAME, so the normal invocation takes no arguments beyond the
    directory:

        python frames_to_png.py out/frames --montage out/run.png

THE METHOD IT EMBODIES  (A)
    ** Put the geometry in the filename.  A raw framebuffer dump is a bag of bytes
    with no self-description; if the runner does not encode width, height and
    pixel format into the name, then six weeks later you have a directory of
    .bin files and no way to know whether that 238080-byte blob was 496x240
    RGB565 or 240x248 ARGB.  Making the runner write

        frame_000900_496x240_rgb565.bin

    costs one format string and removes an entire class of "the converter needs
    arguments the user does not have" failure.  This tool is the payoff.

    ** The first question of chapter 4 is "is the screen black?", not "is the
    screen right".  A black frame is the signature of a missing virtual clock:
    a WIPI/MIDP guest idles on a platform timer, so a runner that does not
    advance a virtual millisecond counter renders nothing forever and looks
    exactly like a broken loader.  This tool therefore always reports a
    non-black pixel count, and `--require-nonblack` turns that into a CI gate.

FILENAME GRAMMAR ACCEPTED
    Anything containing WxH, e.g.
        frame_000900_496x240.bin
        f900-496x240-rgb565.raw
        dump.0900.w496.h240.argb.bin        (w<N> ... h<N> also works)
    Pixel format, if present anywhere in the name (case-insensitive):
        rgb565 / 565            -> 2 bytes per pixel, little-endian RGB565
        rgb555 / 555            -> 2 bytes per pixel, little-endian XRGB1555
        argb / xrgb / 8888 / 32 -> 4 bytes per pixel, 0xAARRGGBB little-endian
        rgba                    -> 4 bytes per pixel, R,G,B,A byte order
        bgra                    -> 4 bytes per pixel, B,G,R,A byte order
        rgb888 / rgb24          -> 3 bytes per pixel, R,G,B byte order
    If the format is absent it is INFERRED from filesize / (w*h): 2 -> RGB565,
    3 -> RGB888, 4 -> ARGB.  Override everything with --format / --geometry.

KNOWN LIMITS
    * A padded stride (row pitch > width * bpp) is detected only when the file
      size divides evenly by height; the residue is reported as a stride guess
      and applied.  If your runner pads rows to a power of two, pass --stride.
    * There is no attempt to guess the palette of an indexed framebuffer.  If
      your guest renders 8bpp indexed, dump it already expanded.
    * Alpha is ignored (feature-phone framebuffers are opaque); ARGB's high byte
      is dropped rather than composited.

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
import glob as globmod  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402

try:
    from PIL import Image
except ImportError:
    sys.exit(
        "error: Pillow is required to write PNGs.\n"
        "       install it with:  python -m pip install Pillow"
    )


# Default patterns. `*.rgba` first because that is what a probe that dumps
# 32-bit frames writes, and the whole design goal of this tool is that the user
# passes only a directory. Anything not listed here still works via --glob.
_DEFAULT_GLOB = "*.rgba,*.rgb,*.bin,*.raw,*.fb,*.dump,*.565,*.argb"

_WH_RE = re.compile(r"(?:^|[^0-9a-zA-Z])(\d{2,5})\s*[xX]\s*(\d{2,5})(?:[^0-9]|$)")
_W_RE = re.compile(r"(?:^|[^a-zA-Z0-9])w(\d{2,5})(?:[^0-9]|$)", re.I)
_H_RE = re.compile(r"(?:^|[^a-zA-Z0-9])h(\d{2,5})(?:[^0-9]|$)", re.I)
_IDX_RE = re.compile(r"(?:frame|f|dump)[^0-9]{0,2}(\d{2,8})", re.I)

_FORMATS = {
    "rgb565": 2, "565": 2,
    "rgb555": 2, "555": 2,
    "argb": 4, "xrgb": 4, "8888": 4, "argb32": 4,
    "rgba": 4, "bgra": 4,
    "rgb888": 3, "rgb24": 3,
}

_FORMAT_ALIASES = [
    ("rgb565", "rgb565"), ("rgb555", "rgb555"), ("argb32", "argb"), ("argb", "argb"),
    ("xrgb", "argb"), ("rgba", "rgba"), ("bgra", "bgra"), ("rgb888", "rgb888"),
    ("rgb24", "rgb888"), ("8888", "argb"), ("565", "rgb565"), ("555", "rgb555"),
]


def parse_name(name):
    """-> (w, h, fmt|None, index|None); w/h may be None."""
    base = os.path.basename(name)
    stem = os.path.splitext(base)[0]
    w = h = None
    m = _WH_RE.search(stem)
    if m:
        w, h = int(m.group(1)), int(m.group(2))
    else:
        mw, mh = _W_RE.search(stem), _H_RE.search(stem)
        if mw and mh:
            w, h = int(mw.group(1)), int(mh.group(1))
    low = stem.lower()
    fmt = None
    for token, canonical in _FORMAT_ALIASES:
        if token in low:
            fmt = canonical
            break
    idx = None
    mi = _IDX_RE.search(stem)
    if mi:
        idx = int(mi.group(1))
    return w, h, fmt, idx


def decode(data, w, h, fmt, stride):
    """-> (PIL.Image RGB, nonblack_pixel_count)."""
    bpp = _FORMATS[fmt]
    row_bytes = stride if stride else w * bpp
    out = bytearray(w * h * 3)
    nonblack = 0
    o = 0
    for y in range(h):
        base = y * row_bytes
        if fmt in ("rgb565", "rgb555"):
            for x in range(w):
                i = base + x * 2
                v = data[i] | (data[i + 1] << 8)
                if fmt == "rgb565":
                    r = (v >> 11) & 0x1F
                    g = (v >> 5) & 0x3F
                    b = v & 0x1F
                    r = (r << 3) | (r >> 2)
                    g = (g << 2) | (g >> 4)
                    b = (b << 3) | (b >> 2)
                else:
                    r = (v >> 10) & 0x1F
                    g = (v >> 5) & 0x1F
                    b = v & 0x1F
                    r = (r << 3) | (r >> 2)
                    g = (g << 3) | (g >> 2)
                    b = (b << 3) | (b >> 2)
                out[o] = r; out[o + 1] = g; out[o + 2] = b
                if r or g or b:
                    nonblack += 1
                o += 3
        elif fmt == "rgb888":
            for x in range(w):
                i = base + x * 3
                r, g, b = data[i], data[i + 1], data[i + 2]
                out[o] = r; out[o + 1] = g; out[o + 2] = b
                if r or g or b:
                    nonblack += 1
                o += 3
        else:
            for x in range(w):
                i = base + x * 4
                if fmt == "argb":       # 0xAARRGGBB stored little-endian
                    b, g, r = data[i], data[i + 1], data[i + 2]
                elif fmt == "rgba":
                    r, g, b = data[i], data[i + 1], data[i + 2]
                else:                    # bgra
                    b, g, r = data[i], data[i + 1], data[i + 2]
                out[o] = r; out[o + 1] = g; out[o + 2] = b
                if r or g or b:
                    nonblack += 1
                o += 3
    return Image.frombytes("RGB", (w, h), bytes(out)), nonblack


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="frames_to_png.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Convert a headless probe's raw framebuffer dumps to PNG. "
                    "Geometry is parsed from the dump filename, so normally you pass "
                    "only the directory.",
        epilog="A run of entirely black frames almost always means the runner has no "
               "virtual clock: the guest idles on a platform timer and never repaints. "
               "Advance a virtual millisecond counter per frame before blaming the loader.",
    )
    ap.add_argument("directory", help="directory containing the raw dumps")
    ap.add_argument("--glob", default=_DEFAULT_GLOB,
                    help="comma-separated filename patterns (default: %s)" % _DEFAULT_GLOB)
    ap.add_argument("--out-dir", help="where to write PNGs (default: alongside the dumps)")
    ap.add_argument("--montage", metavar="OUT.PNG", help="also write one contact sheet of the run")
    ap.add_argument("--cols", type=int, default=0, help="montage columns (default: near-square)")
    ap.add_argument("--pad", type=int, default=4, help="montage cell padding in pixels (default 4)")
    ap.add_argument("--scale", type=int, default=1, help="integer upscale for the PNGs (nearest)")
    ap.add_argument("--geometry", metavar="WxH",
                    help="override the geometry for files whose name does not carry it")
    ap.add_argument("--format", dest="fmt", choices=sorted(set(v for _, v in _FORMAT_ALIASES)),
                    help="override the pixel format")
    ap.add_argument("--stride", type=int, default=0,
                    help="row pitch in bytes, if rows are padded (default: w*bpp)")
    ap.add_argument("--require-nonblack", action="store_true",
                    help="exit non-zero unless at least one frame has a non-black pixel "
                         "(the chapter-4 gate)")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args(argv)

    directory = args.directory.replace("\\", "/")
    if not os.path.isdir(directory):
        sys.exit("error: not a directory: %s" % directory)

    files = []
    for pat in args.glob.split(","):
        files.extend(globmod.glob(directory + "/" + pat.strip()))
    files = sorted(set(f.replace("\\", "/") for f in files))
    if not files:
        sys.exit("error: no dumps matched %s in %s" % (args.glob, directory))

    override_w = override_h = None
    if args.geometry:
        m = re.match(r"^\s*(\d+)\s*[xX]\s*(\d+)\s*$", args.geometry)
        if not m:
            sys.exit("error: --geometry wants WxH, e.g. --geometry 496x240")
        override_w, override_h = int(m.group(1)), int(m.group(2))

    out_dir = (args.out_dir or directory).replace("\\", "/")
    os.makedirs(out_dir, exist_ok=True)

    entries = []
    images = []
    any_nonblack = False

    for path in files:
        size = os.path.getsize(path)
        w, h, fmt, idx = parse_name(path)
        if override_w:
            w, h = override_w, override_h
        if args.fmt:
            fmt = args.fmt
        note = ""

        if not w or not h:
            entries.append({"file": path, "ok": False,
                            "why": "no WxH in the filename and no --geometry given"})
            continue

        stride = args.stride
        if fmt is None:
            if stride:
                bpp_guess = None
            else:
                if size % (w * h) == 0:
                    bpp_guess = size // (w * h)
                else:
                    bpp_guess = None
                if bpp_guess == 2:
                    fmt = "rgb565"
                elif bpp_guess == 3:
                    fmt = "rgb888"
                elif bpp_guess == 4:
                    fmt = "argb"
            if fmt is None:
                entries.append({"file": path, "ok": False,
                                "why": "cannot infer a pixel format: %d bytes for %dx%d "
                                       "is %.3f bytes/pixel. Pass --format (and --stride "
                                       "if rows are padded)." % (size, w, h, size / float(w * h))})
                continue
            note = "format inferred from size"

        bpp = _FORMATS[fmt]
        if not stride:
            need = w * h * bpp
            if size > need and size % h == 0:
                cand = size // h
                if cand > w * bpp:
                    stride = cand
                    note = (note + "; " if note else "") + "padded stride %d (visible %d)" % (
                        stride, w * bpp)
            elif size < need:
                entries.append({"file": path, "ok": False,
                                "why": "short file: %d bytes, %dx%d %s needs %d"
                                       % (size, w, h, fmt, need)})
                continue

        with open(path, "rb") as fh:
            data = fh.read()

        try:
            img, nonblack = decode(data, w, h, fmt, stride)
        except IndexError:
            entries.append({"file": path, "ok": False,
                            "why": "ran off the end of the file -- geometry or stride is wrong"})
            continue

        if args.scale > 1:
            img = img.resize((w * args.scale, h * args.scale), Image.NEAREST)

        png = out_dir + "/" + os.path.splitext(os.path.basename(path))[0] + ".png"
        img.save(png)
        if nonblack:
            any_nonblack = True
        images.append((idx if idx is not None else len(images), img, os.path.basename(png)))
        entries.append({
            "file": path, "png": png, "ok": True, "w": w, "h": h, "format": fmt,
            "stride": stride or w * bpp, "index": idx,
            "nonblack": nonblack, "pixels": w * h,
            "nonblack_pct": round(100.0 * nonblack / (w * h), 3),
            "note": note,
        })

    montage_path = None
    if args.montage and images:
        images.sort(key=lambda t: t[0])
        cw = max(im.width for _, im, _ in images)
        ch = max(im.height for _, im, _ in images)
        n = len(images)
        cols = args.cols if args.cols > 0 else max(1, int(n ** 0.5 + 0.999))
        rows = (n + cols - 1) // cols
        pad = args.pad
        sheet = Image.new("RGB",
                          (cols * cw + (cols + 1) * pad, rows * ch + (rows + 1) * pad),
                          (24, 24, 28))
        for i, (_idx, im, _name) in enumerate(images):
            r, c = divmod(i, cols)
            sheet.paste(im, (pad + c * (cw + pad), pad + r * (ch + pad)))
        montage_path = args.montage.replace("\\", "/")
        d = os.path.dirname(montage_path)
        if d:
            os.makedirs(d, exist_ok=True)
        sheet.save(montage_path)

    ok = [e for e in entries if e.get("ok")]
    bad = [e for e in entries if not e.get("ok")]

    if args.json:
        print(json.dumps({
            "directory": directory,
            "converted": len(ok),
            "failed": len(bad),
            "any_nonblack": any_nonblack,
            "montage": montage_path,
            "frames": entries,
        }, indent=2))
    else:
        for e in entries:
            if e.get("ok"):
                print("%-44s %4dx%-4d %-7s  nonblack %7d / %-7d (%5.1f%%)%s"
                      % (os.path.basename(e["file"]), e["w"], e["h"], e["format"],
                         e["nonblack"], e["pixels"], e["nonblack_pct"],
                         "   [%s]" % e["note"] if e["note"] else ""))
            else:
                print("%-44s SKIPPED: %s" % (os.path.basename(e["file"]), e["why"]))
        print()
        print("%d converted, %d skipped -> %s" % (len(ok), len(bad), out_dir))
        if montage_path:
            print("montage -> %s" % montage_path)
        if ok and not any_nonblack:
            print()
            print("!! EVERY frame is entirely black.")
            print("   The usual cause is a missing virtual clock in the headless runner:")
            print("   the guest idles on a platform timer, so with a wall-clock now() that")
            print("   never advances between ticks it renders nothing and the loader looks")
            print("   broken.  Advance a virtual millisecond counter per frame and re-run.")

    if bad and not ok:
        return 1
    if args.require_nonblack and not any_nonblack:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
