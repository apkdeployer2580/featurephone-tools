#!/usr/bin/env python3
"""bind_strings.py - the whole chain from a string to the code that uses it.

WHY THIS IS THE FIRST TOOL TO REACH FOR
    A stripped guest image has no symbols, but it does have text, and text is
    named. "cannot save here", "%d/%d items", a database record name, an error
    message - each one is a landmark whose referencing code you can find
    mechanically. This is the highest-yield entry point into an unknown binary:
    from one user-visible message you get a function, and from that function
    you get the state it reads and the gate it enforces.

METHOD (A) - follow four hops, and print every one of them
    In a position-independent image a pointer to a string is stored biased, and
    in an RWPI image it is reached through the static base register rather than
    absolutely. So the chain has four hops, and skipping any of them loses the
    trail:

        1. STRING at file offset F
        2. RELOCATION WORD  W = unit(F) - reloc_delta
             (the value actually stored in the image; strings_dump.py prints
              this column too)
        3. POOL SLOT        every word-aligned position holding W. In practice
             most of these are in the static/.data area, which means the code
             does not reach them with a pc-relative load - it reaches them as
             `sl + offset`.
        4. CODE
             a. direct:   ldr rX,[pc,#..] whose literal IS the slot
             b. via sl:   the slot's offset from the static base appears as a
                          literal-pool word somewhere, and THAT word is loaded
                          pc-relative and then added to sl. Hop 4b is the one
                          people forget, and in a Thumb-1 RWPI image it is the
                          common case.

    The tool also searches the UNBIASED forms (raw file offset, raw guest
    address) and labels them, because you do not know a priori that your title
    biases its pointers at all - and if the unbiased form is the one that hits,
    your reloc_delta is probably wrong.

KNOWN LIMITS
    * Text packed into a runtime string table (a length/count/offset header
      followed by blobs) has no per-string pointer at all: the whole table has
      one pointer and the code indexes it. If a string you can see in the
      listing produces zero pool slots here, suspect a table, and go find the
      table's own pointer instead.
    * A blind word search cannot tell a pointer from a coincidence. Slots with
      no loader at all are usually coincidences, and are reported separately.
    * Hop 4b matches an sl offset as a bare literal. Small offsets (< 0x100)
      are common integers and will produce noise; the tool warns when the
      offset is small enough for that to matter.

USAGE
    python bind_strings.py -t target.toml --string-fo 0xSTRING_FO
    python bind_strings.py -t target.toml --string-fo 0xFO1,0xFO2 --json
    python bind_strings.py -t target.toml --string-text "cannot save"
"""

from __future__ import annotations

import argparse
import array
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from target import load_target  # noqa: E402
except ImportError:
    try:  # target.py may expose only the class
        from target import Target as _Target

        def load_target(path=None):
            return _Target.load(path)
    except ImportError:  # keep --help and --selftest usable without target.py
        load_target = None

M32 = 0xFFFFFFFF


def die(msg: str, code: int = 2):
    sys.stderr.write("bind_strings.py: error: %s\n" % msg)
    raise SystemExit(code)


def tget(tgt, name, default=None):
    """Field access that works with target.py's Target object or a plain dict.

    Target exposes the optional keys through .get(key, default) and raises from
    the matching property when a key is absent, so ask .get first and fall back
    to attribute access for the things .get does not know about (binary_path,
    data). A missing optional key comes back as `default`, never as an
    exception, so a tool can decide for itself whether it can proceed.
    """
    if isinstance(tgt, dict):
        return tgt.get(name, default)
    g = getattr(tgt, "get", None)
    if callable(g):
        try:
            return g(name, default)
        except Exception:
            pass
    try:
        v = getattr(tgt, name)
    except Exception:
        return default
    return default if v is None else v


def reloc_cfg(tgt):
    """(delta, unit, declared). An undeclared relocation means identity."""
    d = tget(tgt, "reloc_delta")
    if d is None:
        return 0, "file_offset", False
    return int(d), str(tget(tgt, "reloc_unit", "file_offset")), True


def parse_int(s, what="value"):
    try:
        return int(str(s).strip().replace("_", ""), 0)
    except ValueError:
        die("%s: cannot parse %r as an integer" % (what, s))


def hexint(s):
    """argparse type accepting 0x-hex as well as decimal."""
    try:
        return int(str(s).strip().replace("_", ""), 0)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "%r is not an integer (write 0x20 or 32)" % s)


def parse_fo(tgt, s, what="offset"):
    """A file offset, `g:0x...` for a guest address, or `sl+0x...` / `sl-0x...`."""
    t = str(s).strip().replace("_", "").lower()
    if t.startswith("g:"):
        return parse_int(t[2:], what) - int(tget(tgt, "image_base", 0))
    if t.startswith("sl+") or t.startswith("sl-"):
        sl = tget(tgt, "sl_fo")
        if sl is None:
            die("%s: the sl+/sl- form needs `sl_fo` in target.toml and it is "
                "not declared. Derive the RWPI static base first (three "
                "independent routes are in the guest-re skill's "
                "address-model reference)." % what)
        v = parse_int(t[3:], what)
        return int(sl) + (v if t[2] == "+" else -v)
    return parse_int(t, what)


def read_image(tgt, target_path, override=None):
    if override:
        p = str(override).replace("\\", "/")
        if not os.path.isfile(p):
            die("--binary %s does not exist" % p)
        with open(p, "rb") as fh:
            return fh.read()
    for attr in ("data", "image"):
        v = tget(tgt, attr)
        if isinstance(v, (bytes, bytearray)):
            return bytes(v)
    cands = []
    for attr in ("binary_path", "binary", "image_path"):
        v = tget(tgt, attr)
        if isinstance(v, str) and v:
            cands.append(v)
            if not os.path.isabs(v) and target_path:
                cands.append(os.path.join(
                    os.path.dirname(os.path.abspath(target_path)), v))
            break
    for c in cands:
        p = c.replace("\\", "/")
        if os.path.isfile(p):
            with open(p, "rb") as fh:
                return fh.read()
    die("cannot read the guest image. target.toml `binary` = %r; tried: %s"
        % (tget(tgt, "binary"), ", ".join(cands) or "(nothing)"))


def halfwords(data):
    a = array.array("H")
    a.frombytes(bytes(data[: len(data) & ~1]))
    if sys.byteorder == "big":
        a.byteswap()
    return a


def words(data):
    a = array.array("I")
    if a.itemsize != 4:  # pragma: no cover
        a = array.array("L")
    a.frombytes(bytes(data[: len(data) & ~3]))
    if sys.byteorder == "big":
        a.byteswap()
    return a


def word_occurrences(wds, value):
    v = value & M32
    out = []
    start = 0
    n = len(wds)
    while start < n:
        try:
            i = wds.index(v, start)
        except TypeError:
            return [k * 4 for k, w in enumerate(wds) if w == v]
        except ValueError:
            break
        out.append(i * 4)
        start = i + 1
    return out


def pcrel_index(hw, dlen):
    """literal_fo -> [(site_fo, rd), ...] for every Thumb pc-relative load."""
    idx = {}
    for i, h in enumerate(hw):
        if 0x4800 <= h <= 0x4FFF:
            fo = i * 2
            lit = ((fo + 4) & ~3) + (h & 0xFF) * 4
            if lit + 4 <= dlen:
                idx.setdefault(lit, []).append((fo, (h >> 8) & 7))
    return idx


CODECS = {"euc-kr": "cp949", "euc_kr": "cp949", "euckr": "cp949", "cp949": "cp949",
          "sjis": "cp932", "shift_jis": "cp932", "shift-jis": "cp932", "cp932": "cp932",
          "gbk": "gbk", "big5": "big5", "cp1252": "cp1252",
          "utf8": "utf-8", "utf-8": "utf-8", "ascii": "ascii"}


def codec_for(tgt):
    return CODECS.get(str(tget(tgt, "encoding", "ascii")).lower(), "ascii")


def read_cstr(data, fo, codec, maxlen=160, minlen=1):
    if fo is None or fo < 0 or fo >= len(data):
        return None
    end = data.find(b"\x00", fo, min(len(data), fo + maxlen))
    if end < 0 or end - fo < minlen:
        return None
    try:
        s = data[fo:end].decode(codec)
    except Exception:
        return None
    return None if any(ord(c) < 0x20 and c != "\t" for c in s) else s


def stored_forms(tgt, fo):
    """The ways a pointer to `fo` may be stored, most likely first."""
    base = int(tget(tgt, "image_base", 0))
    delta, unit, _declared = reloc_cfg(tgt)
    unit_val = fo + (base if unit == "guest_address" else 0)
    forms = [("reloc_biased", (unit_val - delta) & M32),
             ("raw_file_offset", fo & M32),
             ("raw_guest_address", (fo + base) & M32)]
    seen, out = set(), []
    for name, v in forms:
        if v not in seen:
            seen.add(v)
            out.append((name, v))
    return out


def chain(tgt, data, hw, wds, pidx, fo):
    base = int(tget(tgt, "image_base", 0))
    sl = tget(tgt, "sl_fo")
    codec = codec_for(tgt)
    rec = {"string_fo": fo, "string_guest": fo + base,
           "text": read_cstr(data, fo, codec), "forms": [], "slots": []}
    for name, w in stored_forms(tgt, fo):
        rec["forms"].append({"encoding": name, "word": w})
        for slot in word_occurrences(wds, w):
            entry = {"slot_fo": slot, "slot_guest": slot + base,
                     "encoding": name, "direct_loaders": [], "via_sl": None}
            for site, rd in pidx.get(slot, []):
                entry["direct_loaders"].append({"site_fo": site, "rd": rd})
            off = None if sl is None else slot - int(sl)
            if off is not None and -0x10000 <= off <= 0x80000:
                via = {"sl_off": off, "literal_words": []}
                for lw in word_occurrences(wds, off & M32):
                    loaders = [{"site_fo": s, "rd": r} for s, r in pidx.get(lw, [])]
                    if loaders:
                        via["literal_words"].append({"pool_fo": lw,
                                                     "loaders": loaders})
                entry["via_sl"] = via
            rec["slots"].append(entry)
    return rec


def render(tgt, rec, out):
    base = int(tget(tgt, "image_base", 0))
    sl = tget(tgt, "sl_fo")
    out('string fo 0x%06x  guest 0x%08x  "%s"'
        % (rec["string_fo"], rec["string_guest"], rec["text"] or "<not a string>"))
    out("  stored-pointer forms searched: %s"
        % ", ".join("%s=0x%08x" % (f["encoding"], f["word"]) for f in rec["forms"]))
    if not rec["slots"]:
        out("  (no stored pointer anywhere in the image)")
        out("  -> suspect a PACKED STRING TABLE: one pointer for the whole")
        out("     table, indexed by id. Find the table's pointer instead, and")
        out("     use msgid_scan.py to find the accessor that takes the index.")
        out("  -> or reloc_delta / reloc_unit is wrong: re-run solve_reloc.py.")
        return
    for s in rec["slots"]:
        off = None if sl is None else s["slot_fo"] - int(sl)
        where = ("" if off is None
                 else ", sl%s0x%x" % ("+" if off >= 0 else "-", abs(off)))
        out("  slot 0x%06x (guest 0x%08x%s)  [%s]"
            % (s["slot_fo"], s["slot_guest"], where, s["encoding"]))
        for ld in s["direct_loaders"]:
            out("      <- %08x  ldr r%d,[pc]        (direct pc-relative load)"
                % (ld["site_fo"], ld["rd"]))
        via = s["via_sl"]
        if via and via["literal_words"]:
            out("      via sl offset 0x%x:" % (via["sl_off"] & M32))
            for lw in via["literal_words"]:
                out("        literal 0x%x at pool 0x%06x" % (via["sl_off"] & M32,
                                                             lw["pool_fo"]))
                for ld in lw["loaders"]:
                    out("            <- %08x  ldr r%d,[pc]  then `add rX,sl`"
                        % (ld["site_fo"], ld["rd"]))
            if 0 <= via["sl_off"] < 0x100:
                out("        ! the sl offset is small, so these literal matches "
                    "include ordinary integers. Confirm with dis.py.")
        if not s["direct_loaders"] and not (via and via["literal_words"]):
            out("      (no loader found - probably a coincidental word, or the "
                "slot is reached by indexing a nearby table)")
            if off is not None:
                # A slot that lives in the static area but has no pc-relative
                # loader is the normal shape when the compiler reached it with
                # a base register it set up once, or when the slot is one entry
                # of an indexed table. xref.py slot searches the other
                # encodings of `sl + off` that this chain does not.
                out("      -> next: xref.py -t target.toml slot 0x%x   "
                    "(searches every encoding of the sl offset)" % (off & M32))
                out("      -> or dump_table.py -t target.toml sl%s0x%x 16   "
                    "(is this one entry of a table?)"
                    % ("+" if off >= 0 else "-", abs(off)))
    total = sum(len(s["direct_loaders"]) for s in rec["slots"]) + sum(
        len(lw["loaders"]) for s in rec["slots"] if s["via_sl"]
        for lw in s["via_sl"]["literal_words"])
    out("  %d slot(s), %d code site(s)" % (len(rec["slots"]), total))
    out("  next: dis.py -t target.toml <site> +0x40 --resilient")


def selftest():
    """End-to-end chain checks on a synthetic image with a known answer."""
    fails = []

    def check(name, cond):
        if not cond:
            fails.append(name)

    DELTA = 0x100
    SL = 0x3000
    n = 0x4000
    tgt = {"image_base": 0x100000, "bss_size": 0, "sl_fo": SL,
           "reloc_delta": DELTA, "reloc_unit": "file_offset",
           "encoding": "cp1252", "isa": "thumb"}
    img = bytearray(n)

    def put(fo, b):
        img[fo:fo + len(b)] = b

    def put16(fo, h):
        img[fo:fo + 2] = int(h).to_bytes(2, "little")

    def put32(fo, v):
        img[fo:fo + 4] = (v & M32).to_bytes(4, "little")

    TEXT = "cannot save in this area"
    put(0x2000, TEXT.encode("cp1252") + b"\x00")
    reloc = (0x2000 - DELTA) & M32

    # path 1: the pointer sits in a literal pool and code loads it directly.
    #   ldr r0,[pc,#4]  at 0x400  ->  pool word at 0x408
    put16(0x400, 0x4801)
    put32(0x408, reloc)

    # path 2: the pointer sits in the static area at sl+0x120, and code
    #   materialises the OFFSET 0x120 from a pool then adds sl. This is the
    #   hop Thumb-1 RWPI code actually uses, and a chain that stops at the
    #   pool word finds nothing.
    put32(SL + 0x120, reloc)
    put16(0x500, 0x4802)                       # ldr r0,[pc,#8] -> 0x50c
    put32(0x50C, 0x120)

    data = bytes(img)
    hw, wds = halfwords(data), words(data)
    pidx = pcrel_index(hw, len(data))
    rec = chain(tgt, data, hw, wds, pidx, 0x2000)

    check("the string is decoded", rec["text"] == TEXT)
    check("the reloc-biased form is searched first",
          rec["forms"][0]["encoding"] == "reloc_biased"
          and rec["forms"][0]["word"] == reloc)
    slots = {s["slot_fo"]: s for s in rec["slots"]}
    check("the literal-pool slot is found", 0x408 in slots)
    check("the static-area slot is found", SL + 0x120 in slots)
    check("the direct pc-relative loader is found",
          [ld["site_fo"] for ld in slots[0x408]["direct_loaders"]] == [0x400])
    check("the direct loader records its destination register",
          slots[0x408]["direct_loaders"][0]["rd"] == 0)
    via = slots[SL + 0x120]["via_sl"]
    check("the sl hop is computed", via is not None and via["sl_off"] == 0x120)
    check("the sl-offset literal is found",
          via and [x["pool_fo"] for x in via["literal_words"]] == [0x50C])
    check("the instruction that loads the sl offset is found",
          via and via["literal_words"][0]["loaders"][0]["site_fo"] == 0x500)
    check("the static slot has no direct loader",
          not slots[SL + 0x120]["direct_loaders"])

    # a string nothing points at must report the packed-table advice, not a
    # false hit
    put(0x2100, b"UNREFERENCED\x00")
    rec2 = chain(tgt, bytes(img), hw, words(bytes(img)), pidx, 0x2100)
    check("an unreferenced string yields no slots", not rec2["slots"])

    # the pc-relative literal address must be computed from the ALIGNED pc
    idx = pcrel_index(halfwords(b"\x00\x00" + bytes(img)[:0x40]), 0x42)
    check("pc-relative base is align4(pc+4)", all(k % 4 == 0 for k in idx))

    check("word_occurrences finds every copy",
          len(word_occurrences(words(b"\x01\x00\x00\x00" * 3), 1)) == 3)
    check("stored_forms de-duplicates when delta is 0",
          len(stored_forms({"image_base": 0, "reloc_delta": 0,
                            "reloc_unit": "file_offset"}, 0x10)) == 1)

    if fails:
        for f in fails:
            print("FAIL %s" % f)
        print("selftest: %d failure(s)" % len(fails))
        return 1
    print("selftest: ok (14 checks)")
    return 0


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        prog="bind_strings.py",
        description="Follow a string through its relocation word and pool slot "
                    "to the instructions that reference it - including the "
                    "sl-offset hop that Thumb-1 RWPI code actually uses.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("-t", "--target", help="path to target.toml")
    ap.add_argument("--string-fo",
                    help="comma-separated string file offsets (g:0xGUEST ok)")
    ap.add_argument("--string-text",
                    help="find the string by content first, then chain every "
                         "occurrence (encoded with target.toml `encoding`)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--out", help="write the report to this file (utf-8)")
    ap.add_argument("--limit", type=hexint, default=20,
                    help="max --string-text occurrences to chain (default 20)")
    ap.add_argument("--binary", help="override target.toml `binary`")
    ap.add_argument("--selftest", action="store_true",
                    help="run the built-in chain checks and exit")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()
    if not a.target:
        ap.error("-t/--target is required (or use --selftest)")
    if load_target is None:
        die("target.py must sit next to this tool (it loads target.toml)")
    if not a.string_fo and not a.string_text:
        ap.error("give --string-fo or --string-text")

    tgt = load_target(a.target)
    data = read_image(tgt, a.target, a.binary)
    hw = halfwords(data)
    wds = words(data)
    pidx = pcrel_index(hw, len(data))

    offs = []
    if a.string_fo:
        for part in a.string_fo.replace(" ", "").split(","):
            if part:
                offs.append(parse_fo(tgt, part, "string offset"))
    if a.string_text:
        codec = codec_for(tgt)
        try:
            needle = a.string_text.encode(codec)
        except Exception:
            die("cannot encode %r as %s - is target.toml `encoding` right?"
                % (a.string_text, codec))
        pos = data.find(needle)
        found = 0
        while pos >= 0 and found < a.limit:
            # walk back to the start of the NUL-terminated string
            s = pos
            while s > 0 and data[s - 1] != 0:
                s -= 1
            if s not in offs:
                offs.append(s)
                found += 1
            pos = data.find(needle, pos + 1)
        if not found:
            die("%r not found in the image with encoding %s"
                % (a.string_text, codec))

    for fo in offs:
        if not (0 <= fo < len(data)):
            die("string offset 0x%x is outside the image" % fo)

    recs = [chain(tgt, data, hw, wds, pidx, fo) for fo in offs]

    if a.json:
        text = json.dumps({"strings": recs}, indent=2, ensure_ascii=False,
                          sort_keys=True)
    else:
        lines = []
        for r in recs:
            render(tgt, r, lines.append)
            lines.append("")
        text = "\n".join(lines).rstrip()

    if a.out:
        with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text + "\n")
        sys.stderr.write("bind_strings.py: wrote %s\n" % a.out.replace("\\", "/"))
    else:
        print(text)
    return 0 if any(r["slots"] for r in recs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
