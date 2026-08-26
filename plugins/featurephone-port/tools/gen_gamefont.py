#!/usr/bin/env python3
"""gen_gamefont.py - bake the GAME'S OWN bitmap font out of the archive, reproducibly.

WHY THIS EXISTS  (A)
    A feature-phone game almost always ships its own bitmap font as ordinary
    resource members.  Draw the host overlay with THAT font and the whole "host
    label is blank" bug class disappears, because the font's coverage is by
    construction the coverage of every string the game itself can display.  It
    is also the right typeface, by definition.

    The hand-rolled ASCII face plus a closed generated set is the FALLBACK.
    This tool COMPLEMENTS gen_glyphs.py and does not replace it: closed-set
    generation is still correct for host-INVENTED words in a script the game's
    own font does not cover, and for the eyeball preview sheet.

    > REFUTED.  "A full Hangul/CJK face is unshippable inside a bitmap overlay:
    > 11,172 syllables."  That is the UNICODE bound.  The game's strings are in
    > a legacy national codepage whose syllable block is far smaller - 2,350 for
    > one Korean standard - so the WHOLE set is finite and the coverage gate for
    > game text stops being necessary.  The wrong bound is attractive because
    > the arithmetic is right; it is applied to the wrong set.  --index derives
    > the real bound from the codec instead of from anybody's memory.

WHAT IT REFUSES TO DO  (A)
    It does not guess the format, and it does not compose syllables.  Both are
    per-title, and both are recovered by reading the guest's own LOADER and its
    own glyph-composition routine - never by pattern-matching a file, and never
    from the script's standard.  You supply the RE-derived parameters; this tool
    makes finding them cheap (--list, --probe), ASSERTS them against the archive,
    and makes the output reproducible (--emit, --check).  A tool that guessed
    here would emit a font that renders MOST characters correctly, which is the
    worst outcome available: it is not a crash, and nobody notices for months.

THE FOUR THINGS TO RECOVER BEFORE YOU CAN EMIT  (A)
    1. WHICH members are the font.  --list enumerates candidates; with
       -t target.toml it also reports which of them the guest image NAMES.  A
       sibling face that no string in the image names is the dead one.
    2. Glyph COUNT and BYTES PER GLYPH per bank, and the cell.  --probe reports
       which (count x bytes-per-glyph) families close exactly; the loader's own
       memcpy sizes pick the right family out of that list.
    3. The SLICE offsets, when one member packs several tables.  Take them from
       the loader's memcpy SOURCE offsets, in FILE order.
    4. The codepage-to-index mapping the composer indexes with (--index).

WORKED EXAMPLE  (B) - shapes only, so the flags are concrete.  Find your own.
    --probe on one port's font directory reported a 7,920-byte member whose only
    plausible family was 360 glyphs x 22 B, and 22 B factors as 2 B/row x 11
    rows, i.e. a cell of at most 16x11 - which the loader then confirmed.  That
    loader's three component memcpys were 160 / 88 / 112 glyphs, and those sum
    to exactly 360: a size relation that closes is a second derivation, free.
    A 199-byte sibling was split by four memcpys whose SOURCE offsets were
    0x00 / 0x5d / 0x89 / 0xb1, giving file-order slice lengths 93 / 44 / 40 / 22.
    > REFUTED.  "Slice a multi-table member in the order of the destination
    > addresses."  Those four memcpys wrote to DESCENDING addresses, so file
    > order and address order disagreed.  Address order is attractive because a
    > dumped symbol list is sorted that way.  Only the SOURCE offsets are file
    > order - and a mis-slice usually surfaces here as an overlap or a gap in
    > the coverage report, which is why --emit computes one per member.

MEASURED FACTS ENCODED AS CHECKS, NOT COMMENTS  (A)
    * Every declared slice is asserted against the archive before anything is
      emitted.  A slice past the end of a member FAILS; an unaccounted tail is
      REPORTED, because a tail is a memcpy you have not found yet.
    * A derived index table must be ASCENDING, or the emitted binary search is
      wrong.  The tool refuses rather than emitting it.
    * --check regenerates in memory and byte-compares the whole output file,
      exiting NON-ZERO when it is stale.  THIS IS WHY THE TOOL EXISTS IN THIS
      FORM: the archive is gitignored, so a checkout can carry new tables with
      an old archive - or an old table with a re-dumped archive - while every
      tool in the pipeline reports success.  Wire --check into the build.

LICENSED CONTENT  (A)
    The emitted file is the game's font bitmaps in another extension, and an
    ignore rule keyed on `.zip`, `.bin` or `work/` misses it entirely.  Publish
    the generator and your format notes; generate the table at build time from
    the user's own archive.  This tool refuses to write into a packaged-assets
    directory and prints the warning on every emit.

EXIT CODES
    0  did the thing it claims
    1  refused (bad spec, missing member, output path inside packaged assets)
    2  I/O error, or a FAILED check (stale output, bad length, bad ordering)
"""

from __future__ import annotations

import os
import sys

# This directory contains dis.py, which shadows the stdlib `dis` that `inspect`
# needs, and Pillow pulls `inspect` in. Pin the real one before any third-party
# import. (Same guard as gen_glyphs.py, which explains it in full.)
_HERE = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")
if "dis" not in sys.modules:
    _saved = sys.path[:]
    sys.path[:] = [p for p in sys.path
                   if os.path.abspath(p or ".").replace("\\", "/") != _HERE]
    try:
        import dis as _stdlib_dis  # noqa: F401
    except Exception:  # pragma: no cover
        pass
    finally:
        sys.path[:] = _saved

import argparse  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import zipfile  # noqa: E402

SEP = "!"
HINTS = ("font", "fnt", "glyph", "glyf", "hangul", "jamo", "kanji", "kana",
         "gothic", "mincho", "ascii", "eng", "kor", "jpn", "chs")
# A generated table of the game's font must not be packaged as an app asset: it is
# already inside the archive you ship, and a second copy there is licensed content
# in a second place. Emit into source and let the build read it.
ASSET_DIRS = {"assets", "asset", "raw", "res"}
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
BANK_KEYS = (("name", "member", "glyphs", "bpg", "cell"), ("at", "first"))
TABLE_KEYS = (("name", "member", "at", "len"), ())
INDEX_KEYS = (("name", "encoding", "lead", "trail"), ("count",))
HEAD = (
    "// GENERATED by gen_gamefont.py -- do not edit by hand.",
    "//",
    "// LICENSED CONTENT: these bytes are the game's own font. Do not commit this file to a",
    "// public repository. Publish the generator and your format notes instead, and generate",
    "// this table at build time from the user's own archive.",
    "//",
    "// Every length below was ASSERTED against the archive at generation time; a mismatch",
    "// means the archive changed -- re-read your format notes rather than shipping a shifted",
    "// font. No `use` and no `//!` doc comments: this file is `include!`d by a standalone",
    "// `rustc --test` selftest as well as compiled into the app.",
)


class Refuse(Exception):
    pass


class Failed(Exception):
    pass


def posix(p):
    return str(p).replace("\\", "/")


def pint(text, what):
    try:
        return int(str(text), 0)
    except ValueError:
        raise Refuse("%s: %r is not an integer (decimal or 0x...)" % (what, text))


def kv(spec, flag, keys):
    """`a=1,b=2` -> dict, with required and optional keys enforced by name."""
    required, optional = keys
    out = {}
    for part in [p.strip() for p in spec.split(",") if p.strip()]:
        if "=" not in part:
            raise Refuse("--%s: %r is not key=value; expected %s"
                         % (flag, part, ",".join(k + "=..." for k in required)))
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    missing = [k for k in required if k not in out]
    if missing:
        raise Refuse("--%s %r is missing %s" % (flag, spec, ", ".join(missing)))
    unknown = [k for k in out if k not in required and k not in optional]
    if unknown:
        raise Refuse("--%s: unknown key(s) %s. Known: %s"
                     % (flag, ", ".join(unknown), ", ".join(required + optional)))
    if not IDENT.match(out["name"]):
        raise Refuse("--%s: name %r is not an identifier" % (flag, out["name"]))
    return out


def read_member(blob, spec):
    """Read one member through nested containers: '<inner>.jar!res/font/<member>'."""
    for step in [s for s in spec.split(SEP) if s]:
        try:
            zf = zipfile.ZipFile(io.BytesIO(blob))
        except zipfile.BadZipFile as exc:
            raise Refuse("while descending to %r: not a zip (%s)" % (spec, exc))
        if step not in zf.namelist():
            near = [n for n in zf.namelist() if step.lower() in n.lower()][:6]
            raise Refuse("member %r not found in %r.%s"
                         % (step, spec, (" Near: %s" % near) if near else " Use --list."))
        blob = zf.read(step)
    return blob


def walk(blob, prefix="", depth=0):
    """Every leaf member, descending into nested containers, as (spec, size)."""
    rows = []
    zf = zipfile.ZipFile(io.BytesIO(blob))
    for info in zf.infolist():
        if info.is_dir():
            continue
        spec = prefix + info.filename
        if depth < 3 and info.filename.lower().endswith((".jar", ".zip", ".kdp")):
            try:
                rows += walk(zf.read(info.filename), spec + SEP, depth + 1)
                continue
            except zipfile.BadZipFile:
                pass
        rows.append((spec, info.file_size))
    return rows


def packings(bpg):
    """(bytes_per_row, rows, max_width) cells that pack into bpg exactly."""
    return [(bpg // r, r, bpg // r * 8)
            for r in range(5, 33) if not bpg % r and 1 <= bpg // r <= 4]


def plausible(cells):
    """A glyph cell is a few bytes per row and roughly a line tall. Ranking only."""
    return any(bpr <= 2 and 6 <= rows <= 24 for bpr, rows, _w in cells)


def families(size, min_glyphs=16, max_bpg=512):
    """(glyphs, bytes_per_glyph, cells) that consume `size` EXACTLY, plausible first.

    The ordering is a convenience, NOT evidence: several families always close on
    any real member. The loader's own memcpy sizes are what pick one.
    """
    out = [(size // b, b, packings(b)) for b in range(1, min(size, max_bpg) + 1)
           if not size % b and min_glyphs <= size // b <= 65536]
    out.sort(key=lambda f: (not plausible(f[2]), f[1]))
    return out


def coverage(spans, size):
    """spans: [(start, end, name)] -> (gaps, overlaps). A mis-slice shows up here."""
    gaps, overlaps, cur = [], [], 0
    for start, end, name in sorted(spans):
        if start > cur:
            gaps.append((cur, start))
        elif start < cur:
            overlaps.append(name)
        cur = max(cur, end)
    if cur < size:
        gaps.append((cur, size))
    return gaps, overlaps


def index_pairs(encoding, lead, trail, declared=None):
    """Derive the codepage-to-index table FROM THE CODEC.  (A)

    The index of a character is its position in this table and the entry is its
    (lead, trail) codepage pair - the arithmetic the guest itself does. Deriving
    it from the codec is what stops this table and the host's own decoder from
    drifting apart; that decoder must be the exact INVERSE of this table, or a
    name the host read out of a live guest record draws the wrong glyphs.
    """
    lo_l, hi_l = lead
    lo_t, hi_t = trail
    pairs = []
    for hi in range(lo_l, hi_l + 1):
        for lo in range(lo_t, hi_t + 1):
            try:
                ch = bytes((hi, lo)).decode(encoding)
            except LookupError:
                raise Refuse("--index: python has no codec named %r" % encoding)
            except UnicodeDecodeError:
                continue
            if len(ch) == 1:
                pairs.append((hi << 8) | lo)
    if pairs != sorted(pairs) or len(set(pairs)) != len(pairs):
        raise Failed("--index produced a non-ascending table; the emitted binary search "
                     "would be wrong")
    if declared is not None and declared != len(pairs):
        raise Failed("--index: you declared count=%d but the %s codec yields %d pairs over "
                     "that range. The codec is the authority - fix the range or the count. "
                     "(This is the check that retires a remembered syllable count.)"
                     % (declared, encoding, len(pairs)))
    return pairs


def parse_range(text, what):
    a, _, b = text.partition("..")
    if not b:
        raise Refuse("%s: expected LO..HI, got %r" % (what, text))
    return pint(a, what), pint(b, what)


def rust_bytes(name, data):
    out = ["pub static %s: [u8; %d] = [" % (name, len(data))]
    for i in range(0, len(data), 16):
        out.append("    " + " ".join("0x%02x," % b for b in data[i:i + 16]))
    return out + ["];", ""]


def rust_u16(name, values):
    out = ["pub static %s: [u16; %d] = [" % (name, len(values))]
    for i in range(0, len(values), 12):
        out.append("    " + " ".join("0x%04x," % v for v in values[i:i + 12]))
    return out + ["];", ""]


def build(blob, banks, tables, indexes, invocation):
    """Assert every declared parameter against the archive, then render the source."""
    spans, sizes, notes, body = {}, {}, [], []
    for spec in banks + tables:
        if spec["member"] not in sizes:
            sizes[spec["member"]] = len(read_member(blob, spec["member"]))
            spans[spec["member"]] = []

    for b in banks:
        at = pint(b.get("at", 0), "bank at")
        glyphs, bpg = pint(b["glyphs"], "glyphs"), pint(b["bpg"], "bpg")
        cw, _, chh = b["cell"].partition("x")
        cw, ch = pint(cw, "cell width"), pint(chh or "0", "cell height")
        if not ch or bpg % ch or (bpg // ch) * 8 < cw:
            raise Failed("bank %s: a %dx%d cell does not pack into %d bytes/glyph. Take bpg "
                         "from the loader's memcpy, not from the cell you expect."
                         % (b["name"], cw, ch, bpg))
        end = at + glyphs * bpg
        if end > sizes[b["member"]]:
            raise Failed("bank %s: %d glyphs x %d B from 0x%x needs %d bytes but the member "
                         "holds %d. At least one of the three came from a guess."
                         % (b["name"], glyphs, bpg, at, end, sizes[b["member"]]))
        spans[b["member"]].append((at, end, b["name"]))
        up = b["name"].upper()
        body += ["// bank %s: %d glyphs x %d B, cell %dx%d, from %s+0x%x"
                 % (b["name"], glyphs, bpg, cw, ch, b["member"].split(SEP)[-1], at),
                 "pub const %s_GLYPHS: usize = %d;" % (up, glyphs),
                 "pub const %s_BPG: usize = %d;" % (up, bpg),
                 "pub const %s_CELL_W: usize = %d;" % (up, cw),
                 "pub const %s_CELL_H: usize = %d;" % (up, ch),
                 "pub const %s_FIRST: usize = %d;" % (up, pint(b.get("first", 0), "first")),
                 ""]
        body += rust_bytes(up, read_member(blob, b["member"])[at:end])
        body += ["pub fn %s_glyph(i: usize) -> Option<&'static [u8]> {" % b["name"],
                 "    if i >= %s_GLYPHS { return None; }" % up,
                 "    Some(&%s[i * %s_BPG..(i + 1) * %s_BPG])" % (up, up, up),
                 "}", ""]

    for t in tables:
        at, ln = pint(t["at"], "table at"), pint(t["len"], "table len")
        if at + ln > sizes[t["member"]]:
            raise Failed("table %s: 0x%x+%d runs past the member (%d bytes). Slice offsets "
                         "are the loader's memcpy SOURCE offsets, in FILE order."
                         % (t["name"], at, ln, sizes[t["member"]]))
        spans[t["member"]].append((at, at + ln, t["name"]))
        body += ["// table %s: %d B at %s+0x%x (loader memcpy source offset)"
                 % (t["name"], ln, t["member"].split(SEP)[-1], at)]
        body += rust_bytes(t["name"].upper(), read_member(blob, t["member"])[at:at + ln])

    for member, size in sorted(sizes.items()):
        gaps, overlaps = coverage(spans[member], size)
        if overlaps:
            raise Failed("%s: slice(s) %s overlap. Two tables cannot own the same bytes - the "
                         "usual cause is slicing in DESTINATION-ADDRESS order instead of file "
                         "order." % (member, ", ".join(overlaps)))
        for lo, hi in gaps:
            notes.append("%s: %d bytes unaccounted at 0x%x..0x%x -- a tail is a memcpy you "
                         "have not found yet" % (member, hi - lo, lo, hi))

    for ix in indexes:
        pairs = index_pairs(ix["encoding"],
                            parse_range(ix["lead"], "--index lead"),
                            parse_range(ix["trail"], "--index trail"),
                            pint(ix["count"], "count") if "count" in ix else None)
        up = ix["name"].upper()
        body += ["// index %s: %d codepage pairs derived from the %s codec, ascending."
                 % (ix["name"], len(pairs), ix["encoding"]),
                 "// The host's own decoder must be the exact INVERSE of this table.",
                 "pub const %s_COUNT: usize = %d;" % (up, len(pairs)), ""]
        body += rust_u16(up + "_PAIRS", pairs)
        body += ["pub fn %s_index(pair: u16) -> Option<usize> {" % ix["name"],
                 "    let (mut lo, mut hi) = (0usize, %s_PAIRS.len());" % up,
                 "    while lo < hi {",
                 "        let mid = (lo + hi) / 2;",
                 "        let v = %s_PAIRS[mid];" % up,
                 "        if v == pair { return Some(mid); }",
                 "        if v < pair { lo = mid + 1; } else { hi = mid; }",
                 "    }",
                 "    None",
                 "}", ""]

    return "\n".join(list(HEAD) + ["//", "// regenerate with:", "//   " + invocation, ""]
                     + body) + "\n", notes


def check_out_path(path):
    if os.path.isabs(path):
        raise Refuse("--out must be repo-relative: an absolute path bakes one machine's "
                     "layout into a generated file.")
    hit = [p for p in posix(path).lower().split("/")[:-1] if p in ASSET_DIRS]
    if hit:
        raise Refuse("--out is inside a packaged-assets directory (%r). The font is already in "
                     "the archive you ship, so a second copy there is licensed content in a "
                     "second place. Emit into your source tree instead." % hit[0])


def preview(banks, blob, out_png, want, first, count, zoom):
    try:
        from PIL import Image
    except ImportError:
        raise Refuse("--preview needs Pillow, which is not installed.\n"
                     "  python -m pip install Pillow\n"
                     "  (no tool in this plugin installs anything for you)")
    b = next((x for x in banks if x["name"] == want), banks[0] if banks else None)
    if b is None:
        raise Refuse("--preview needs at least one --bank")
    at, bpg = pint(b.get("at", 0), "at"), pint(b["bpg"], "bpg")
    cw, _, chh = b["cell"].partition("x")
    cw, ch = pint(cw, "cell width"), pint(chh or "0", "cell height")
    row_b = bpg // max(ch, 1)
    data = read_member(blob, b["member"])
    n = max(0, min(count, pint(b["glyphs"], "glyphs") - first))
    cols = max(1, int(n ** 0.5 + 0.999))
    gap, rows_n = 3, (n + cols - 1) // max(cols, 1)
    img = Image.new("RGB", (cols * (cw * zoom + gap) + gap, rows_n * (ch * zoom + gap) + gap),
                    (30, 30, 34))
    px = img.load()
    for i in range(n):
        cell = data[at + (first + i) * bpg:at + (first + i + 1) * bpg]
        r, c = divmod(i, cols)
        ox, oy = gap + c * (cw * zoom + gap), gap + r * (ch * zoom + gap)
        for y in range(ch):
            for x in range(cw):
                k = y * row_b + x // 8
                on = cell[k] & (0x80 >> (x % 8)) if k < len(cell) else 0
                col = (235, 235, 235) if on else (58, 58, 66)
                for zy in range(zoom):
                    for zx in range(zoom):
                        px[ox + x * zoom + zx, oy + y * zoom + zy] = col
    d = os.path.dirname(out_png)
    if d:
        os.makedirs(d, exist_ok=True)
    img.save(out_png)
    return b["name"], n


def selftest():
    """Arithmetic only: no archive, no Pillow, no network."""
    ok = 0

    def check(cond, msg):
        nonlocal ok
        if not cond:
            raise Failed("selftest: " + msg)
        ok += 1

    check((360, 22) in {(g, b) for g, b, _c in families(7920)},
          "7920 must factor as 360 glyphs x 22 B (the --probe worked example)")
    check((2, 11, 16) in packings(22), "22 B must pack as 2 B/row x 11 rows, width <= 16")
    check(160 + 88 + 112 == 360, "the component counts must sum to the bank's glyph count")

    offs = [0x00, 0x5d, 0x89, 0xb1]
    spans = [(o, (offs + [199])[i + 1], "t%d" % i) for i, o in enumerate(offs)]
    check([e - s for s, e, _ in spans] == [93, 44, 40, 22],
          "file-order memcpy offsets must give slice lengths 93/44/40/22")
    check(coverage(spans, 199) == ([], []), "file-order slices must cover the member exactly")
    check(coverage([(0xb1, 199, "a"), (0x89, 0xb1 + 40, "b")], 199)[1],
          "an address-order mis-slice must be reported as an overlap")

    pairs = index_pairs("euc-kr", (0xb0, 0xc8), (0xa1, 0xfe))
    check(len(pairs) == 2350,
          "the codec must yield 2350 syllables over that block, not Unicode's 11172")
    check(pairs == sorted(pairs), "the derived index table must be ascending")
    check("0xb0a1," in "\n".join(rust_u16("T", pairs[:1])), "u16 emission must be hex, ascending")

    try:
        kv("name=a,member=m,glyphs=1,bpg=1,cell=8x8,bogus=1", "bank", BANK_KEYS)
        check(False, "an unknown spec key must be refused")
    except Refuse:
        ok += 1
    for path, must_refuse in (("src/gen/font.rs", False), ("app/src/main/assets/font.rs", True)):
        try:
            check_out_path(path)
            check(not must_refuse, "%s must be refused" % path)
        except Refuse:
            check(must_refuse, "%s must be allowed" % path)
    print("selftest: %d checks passed" % ok)
    return 0


def _parser():
    p = argparse.ArgumentParser(
        prog="gen_gamefont.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Bake the game's own bitmap font out of the archive into generated source, "
                    "reproducibly. You supply the RE-derived format; this asserts it.",
        epilog=(
            "examples:\n"
            "  # 1. which members are the font? (-t also reports which ones the image NAMES)\n"
            "  python gen_gamefont.py work/<archive>.zip --list -t target.toml\n"
            "\n"
            "  # 2. which layouts close exactly? match these against the loader's memcpys\n"
            "  python gen_gamefont.py work/<archive>.zip --probe \\\n"
            "      --member \"<jar>!res/font/<member>\"      # <jar> = the inner container\n"
            "\n"
            "  # 3. emit, asserting every declared length against the archive\n"
            "  python gen_gamefont.py work/<archive>.zip --emit \\\n"
            "      --bank  \"name=syl,member=<jar>!res/font/<m>,glyphs=360,bpg=22,cell=16x11\" \\\n"
            "      --table \"name=remap,member=<jar>!res/font/<m2>,at=0x00,len=0x5d\" \\\n"
            "      --index \"name=kor,encoding=euc-kr,lead=0xb0..0xc8,trail=0xa1..0xfe\" \\\n"
            "      --out src/gamefont_data.rs\n"
            "\n"
            "  # 4. the staleness gate: the SAME flags with --check. Wire this into the build.\n"
            "  python gen_gamefont.py work/<archive>.zip --check --bank \"...\" \\\n"
            "      --out src/gamefont_data.rs\n"
            "\n"
            "  # 5. the eyeball pass (needs Pillow). Composition is NOT performed: for a\n"
            "  #    composed script this sheet shows COMPONENT cells - which is exactly what\n"
            "  #    catches a mis-sliced bank, because no assertion can.\n"
            "  python gen_gamefont.py work/<archive>.zip --preview font.png --bank \"...\"\n"
            "\n"
            "exit codes: 0 ok | 1 refused | 2 I/O error or FAILED check\n"),
    )
    p.add_argument("archive", nargs="?", help="the outer archive (forward slashes)")
    p.add_argument("--list", action="store_true",
                   help="enumerate candidate font members; with -t, cross-check each against "
                        "the guest image's own strings (a face nothing names is the dead one)")
    p.add_argument("--probe", action="store_true",
                   help="report each member's size and the (glyphs x bytes-per-glyph) families "
                        "that close exactly, for matching against the loader's memcpy sizes")
    p.add_argument("--emit", action="store_true", help="write the generated source to --out")
    p.add_argument("--check", action="store_true",
                   help="regenerate in memory and byte-compare against --out; exits non-zero "
                        "when stale. The build gate this tool exists for.")
    p.add_argument("--preview", metavar="OUT.PNG", help="contact sheet of one bank's cells")
    p.add_argument("--member", action="append", default=[], metavar="SPEC",
                   help="restrict --list/--probe to this member ('<inner>.jar!path'); repeatable")
    p.add_argument("--bank", action="append", default=[], metavar="K=V,...",
                   help="glyph bank: name=,member=,glyphs=,bpg=,cell=WxH[,at=][,first=]")
    p.add_argument("--table", action="append", default=[], metavar="K=V,...",
                   help="raw slice (remap/variant table): name=,member=,at=,len= -- offsets in "
                        "FILE order, taken from the loader's memcpy SOURCE offsets")
    p.add_argument("--index", action="append", default=[], metavar="K=V,...",
                   help="codepage index: name=,encoding=,lead=LO..HI,trail=LO..HI[,count=]")
    p.add_argument("--out", metavar="PATH", help="generated source file (repo-relative)")
    p.add_argument("--preview-bank", metavar="NAME", help="which --bank to draw (default: first)")
    p.add_argument("--preview-from", type=int, default=0, metavar="N", help="first glyph index")
    p.add_argument("--preview-count", type=int, default=256, metavar="N", help="glyphs to draw")
    p.add_argument("--zoom", type=int, default=5, help="preview magnification (default 5)")
    p.add_argument("--json", action="store_true", help="machine-readable result")
    p.add_argument("--selftest", action="store_true", help="run built-in arithmetic checks")
    try:
        sys.path.append(_HERE)
        import target as _t
        _t.Target.add_argument(p)
    except Exception:  # noqa: BLE001 - -t is a convenience for --list only
        p.add_argument("-t", "--target", metavar="target.toml", help="per-title config")
    return p


def _report(rows, args):
    if args.json:
        print(json.dumps(rows, indent=2))
        return
    print("%d candidate member(s)%s"
          % (len(rows), "" if args.member else "  (name hints: %s)" % ", ".join(HINTS)))
    for row in rows:
        tag = ("" if "named_by_image" not in row else
               "  [named by the image]" if row["named_by_image"] else
               "  [NOT named by the image -- likely the dead face; do not bake it]")
        print("  %10d  %s%s" % (row["size"], row["member"], tag))
        fams = row.get("families")
        if fams is None:
            continue
        shown = [f for f in fams if f["plausible"]][:24]
        for f in shown:
            print("      %6d glyphs x %3d B   cells: %s"
                  % (f["glyphs"], f["bpg"], ", ".join(f["cells"])))
        rest = len(fams) - len(shown)
        if not shown:
            print("      no family closes with a plausible glyph cell. This member is more "
                  "likely\n      several PACKED TABLES than a glyph bank -- slice it with "
                  "--table, taking the\n      offsets from the loader's memcpy source offsets, "
                  "in FILE order.")
        elif rest:
            print("      (+%d further famil%s close with an implausible cell -- 32 px wide or "
                  "40 rows\n      tall is not what you are looking for; --json lists them all)"
                  % (rest, "y" if rest == 1 else "ies"))
    if args.probe:
        print("\nSeveral families always close, so nothing here is evidence on its own: the\n"
              "loader's own memcpy SIZES are what pick the right one out of this list.")
    elif any("named_by_image" in r for r in rows):
        print("\nThe cross-check is a substring search over the whole image, so record what it\n"
              "could NOT catch: a short basename can match by accident, and a member the game\n"
              "builds a path for at runtime is named nowhere. Confirm a hit lands inside a\n"
              "string before you retire a face on the strength of it.")


def main(argv=None):
    args = _parser().parse_args(argv)
    if args.selftest:
        return selftest()
    modes = [m for m in ("list", "probe", "emit", "check") if getattr(args, m)]
    modes += ["preview"] if args.preview else []
    try:
        if len(modes) != 1:
            raise Refuse("give exactly one of --list, --probe, --emit, --check, --preview")
        if not args.archive:
            raise Refuse("an archive is required (see the examples in --help)")
        with open(posix(args.archive), "rb") as fh:
            blob = fh.read()
        banks = [kv(s, "bank", BANK_KEYS) for s in args.bank]
        tables = [kv(s, "table", TABLE_KEYS) for s in args.table]
        indexes = [kv(s, "index", INDEX_KEYS) for s in args.index]

        if args.list or args.probe:
            found = [(s, n) for s, n in walk(blob)
                     if (s in args.member if args.member
                         else any(h in s.lower() for h in HINTS))]
            image = None
            if args.list and getattr(args, "target", None):
                import target as _t
                image = _t.Target.load(args.target).data
            rows = []
            for spec, size in sorted(found):
                row = {"member": spec, "size": size}
                if image is not None:
                    leaf = spec.split(SEP)[-1]
                    row["named_by_image"] = any(image.find(c.encode("ascii", "ignore")) >= 0
                                                for c in (leaf, leaf.split("/")[-1]))
                if args.probe:
                    fam = families(size)
                    row["families"] = [{"glyphs": g, "bpg": b, "plausible": plausible(c),
                                        "cells": ["%dx%d" % (w, r) for _bw, r, w in c]}
                                       for g, b, c in fam]
                rows.append(row)
            _report(rows, args)
            return 0

        if args.preview:
            name, n = preview(banks, blob, posix(args.preview), args.preview_bank,
                              args.preview_from, args.preview_count, args.zoom)
            print("wrote %s: %d cells of bank %s" % (posix(args.preview), n, name))
            print("LOOK AT IT before shipping. A mis-sliced bank draws recognisable-but-wrong "
                  "shapes, and no assertion catches that.")
            return 0

        if not args.out:
            raise Refuse("--out is required for --emit and --check")
        check_out_path(args.out)
        if not (banks or tables or indexes):
            raise Refuse("nothing to emit: give at least one --bank, --table or --index")
        # Always record the --emit form, whichever mode is running: --check compares the
        # whole file, so a recorded "--check" would report every fresh table as stale.
        inv = "python gen_gamefont.py %s --emit %s --out %s" % (
            os.path.basename(posix(args.archive)),
            " ".join("--%s %r" % (k, v)
                     for k, lst in (("bank", args.bank), ("table", args.table),
                                    ("index", args.index)) for v in lst),
            posix(args.out))
        text, notes = build(blob, banks, tables, indexes, inv)
        for note in notes:
            print("note: %s" % note, file=sys.stderr)

        if args.check:
            try:
                with open(posix(args.out), "r", encoding="utf-8") as fh:
                    have = fh.read()
            except OSError as exc:
                raise Failed("cannot read %s to compare against: %s" % (posix(args.out), exc))
            if have != text:
                a, b = have.splitlines(), text.splitlines()
                first = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y),
                             min(len(a), len(b)))
                raise Failed("%s is STALE (first difference at line %d; %d lines on disk vs %d "
                             "regenerated).\n  The archive and this generated table disagree. "
                             "Re-run --emit, and work out WHY they drifted: a re-dumped archive "
                             "and a hand-edited table look identical from here."
                             % (posix(args.out), first + 1, len(a), len(b)))
            print("%s is up to date (%d bytes)" % (posix(args.out), len(text.encode("utf-8"))))
            return 0

        d = os.path.dirname(posix(args.out))
        if d:
            os.makedirs(d, exist_ok=True)
        with open(posix(args.out), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        result = {"out": posix(args.out), "bytes": len(text.encode("utf-8")),
                  "banks": [b["name"] for b in banks], "tables": [t["name"] for t in tables],
                  "indexes": [i["name"] for i in indexes], "notes": notes}
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print("wrote %s  (%d bytes)" % (result["out"], result["bytes"]))
            print("LICENSED CONTENT: this file is the game's font in another extension. Do not\n"
                  "  commit it. Gitignore generated tables BY CONTENT, not by name or directory;\n"
                  "  publish the generator and your format notes instead, and wire --check into\n"
                  "  the build so the baked copy cannot drift from the archive.")
        return 0
    except Refuse as exc:
        print("refusing: %s" % exc, file=sys.stderr)
        return 1
    except Failed as exc:
        print("FAILED: %s" % exc, file=sys.stderr)
        return 2
    except zipfile.BadZipFile as exc:
        print("error: %s is not a readable archive: %s" % (posix(args.archive), exc),
              file=sys.stderr)
        return 2
    except OSError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
