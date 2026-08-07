#!/usr/bin/env python3
"""dump_table.py - classify a run of words in the RWPI static area (or anywhere).

WHAT IT DOES
    Prints `count` consecutive 32-bit words starting at a file offset and
    classifies each one:

        FN    a Thumb code pointer (low bit set) whose relocated target starts
              with a recognisable function prologue
        FN?   low bit set, target inside the image, prologue NOT recognised -
              this is what a jump-table arm looks like, and a jump-table arm is
              not a callable function
        STR   a data pointer whose relocated target is a plausible string
        STR?  low bit set AND a real prologue at the code target AND real text
              at the data target - both readings survive, so the tool refuses
              to guess. Disassemble the code target to settle it.
        PTR   a data pointer into the image that is not a string
        BSS   a pointer past end-of-file but inside the BSS the loader appends
              (a writable global that is simply zero on disk)
        VAL   a plain value, or zero - a zero word is a slot the guest fills in
              at run time, which is exactly what the god-object slot looks like
              on disk

METHOD (A)
    This is the fastest way to understand an unknown RWPI static region, and it
    is how you find the "god object" slot - the static word that holds the
    pointer to the main game state. Dump the first few hundred words of the
    static area, and the slot you want is the one that is zero on disk (it is
    filled in at run time) and that a runtime dump shows holding a live heap
    pointer. Everything around it will classify as STR or FN, which is your
    confirmation that the static base is right.

    That last point is worth stating on its own: THIS TOOL IS ALSO THE CHECK ON
    YOUR STATIC BASE. If `sl_fo` in target.toml is correct, a dump of
    `sl+0 .. sl+0x400` is a dense run of STR/FN/PTR. If it is noise, the base
    is wrong - go back to `reference/address-model.md` and derive it again.

    A run of >= 3 consecutive FN words is a jump table or a vtable, not three
    scalars; the tool says so, and deadcode_sweep.py uses the same signal.

KNOWN LIMITS
    * Offline only. Slots that the guest fills in at run time (heap pointers,
      handles, caches) read as 0 or as stale build-time junk. Confirm those
      with a runtime dump of the same region.
    * Classification is a heuristic on everything except the Thumb low bit.
      Cross-check anything load-bearing with `xref.py slot` and dis.py.
    * Words below --min-ptr (default 0x1000) are always VAL. Every small
      integer relocates onto SOME file offset and reporting it as a pointer is
      the commonest false lead this kind of tool produces. If your image really
      does store pointers into its first few KB, lower the threshold.
    * A `STR` row proves only that plausible text sits at the relocated target.
      It does not prove the slot is read as a string pointer; `xref.py slot`
      does that.
    * The prologue set is the common ARM/Thumb one (push {..,lr}, push {..},
      sub sp, and the RWPI static-base setup ldr rX,[pc]). Hand-written
      assembly and jump-table arms will not match, which is exactly why a
      jump-table arm shows up as FN? - and why you must never call one as a
      function (see the guest-re skill's calling-guest-code reference).

USAGE
    python dump_table.py -t target.toml sl+0 256
    python dump_table.py -t target.toml 0xSTATIC_BASE_FO 256 --json
    python dump_table.py -t target.toml sl-0xTABLE_OFF 64  # a table below the base
"""

from __future__ import annotations

import argparse
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


def die(msg: str, code: int = 2):
    sys.stderr.write("dump_table.py: error: %s\n" % msg)
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
            "%r is not an integer (write 0x100 or 256)" % s)


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


def reloc_to_fo(tgt, word):
    v = (int(word) + int(tget(tgt, "reloc_delta", 0))) & 0xFFFFFFFF
    if str(tget(tgt, "reloc_unit", "file_offset")) == "guest_address":
        v -= int(tget(tgt, "image_base", 0))
    return v


def read_cstr(data, fo, encoding, maxlen=120, minlen=2):
    if fo is None or fo < 0 or fo >= len(data):
        return None
    end = data.find(b"\x00", fo, min(len(data), fo + maxlen))
    if end < 0 or end - fo < minlen:
        return None
    try:
        s = data[fo:end].decode(encoding)
    except Exception:
        return None
    return None if any(ord(c) < 0x20 and c != "\t" for c in s) else s


def looks_like_text(s):
    """Is a successfully decoded byte run actually TEXT?

    This predicate is the difference between a useful table dump and a
    misleading one. Any three bytes that happen to decode under a permissive
    single-byte codec and happen to be followed by a NUL will 'decode
    successfully' - a Thumb `bx lr; nop` pair does exactly that under cp1252,
    and a table of function pointers then prints as a column of two-letter
    nonsense strings while the real answer (a jump table) goes unreported.

    The rule: short runs must be ASCII-clean, and any run must be mostly
    printable. Multi-byte CJK codecs (euc-kr, sjis, gbk, big5) legitimately
    produce non-ASCII, so those characters count as printable - but a run
    shorter than 4 characters is never accepted as evidence, because at that
    length coincidence is more likely than content.
    """
    if not s:
        return False
    if len(s) < 4:
        return False
    printable = sum(1 for c in s if c == "\t" or 0x20 <= ord(c) < 0x7F or ord(c) >= 0xA1)
    return printable >= max(4, int(0.85 * len(s)))


def looks_like_prologue(data, fo):
    """True if the halfword at `fo` starts a plausible Thumb function."""
    if fo < 0 or fo + 2 > len(data):
        return False
    h = int.from_bytes(data[fo:fo + 2], "little")
    if 0xB400 <= h <= 0xB5FF:      # push {regs} / push {regs, lr}
        return True
    if 0xB080 <= h <= 0xB0FF:      # sub sp, #imm
        return True
    if 0x4800 <= h <= 0x4FFF:      # ldr rX,[pc,#..]  (RWPI static-base setup)
        return True
    if (h & 0xFF00) == 0x4600:     # mov rX, rY (frame shuffle)
        return True
    return False


# A stored word below this is read as a scalar, not as a pointer. Every small
# integer relocates onto SOME file offset, and reporting that offset is the
# commonest false lead a table dumper can produce: a slot holding the count 42
# is not a pointer to file offset 0x12a. Override with --min-ptr if your image
# genuinely stores pointers into its first few KB (unusual: that region is the
# entry thunk).
SMALL_VALUE_MAX = 0x1000


def classify(tgt, data, w, bss_end, min_ptr=SMALL_VALUE_MAX):
    """Classify one stored word. Returns (kind, target_fo, text).

    ORDER MATTERS, and the order here is the corrected one:

      1. The low bit is the ARM architecture telling you this word is a Thumb
         code pointer. It is a hardware fact, not a heuristic, so it is tested
         FIRST. An earlier version of this tool tried the string reading first
         on the theory that "a decodable string beats an opcode match" - and
         then printed every entry of a function-pointer table as a two-letter
         nonsense string, because `bx lr; nop` decodes cleanly under a
         permissive single-byte codec and is NUL-terminated by the next
         function's `push`. See looks_like_text().
      2. A word that survives looks_like_text() at its relocated target is a
         string pointer.
      3. A word below `min_ptr` is a scalar, whatever it relocates onto.

    STR? / FN? mark the genuinely ambiguous rows rather than guessing: STR?
    means the low bit is set AND the code target has a real prologue AND the
    data target is real text; FN? means the low bit is set but the target does
    not start with a prologue, which is what a jump-table ARM looks like - and
    a jump-table arm must never be called as a function.
    """
    enc = str(tget(tgt, "encoding", "ascii"))
    n = len(data)
    if w == 0:
        return "VAL", None, None                     # a null / not-yet-filled slot
    if w < min_ptr:
        # A scalar: a count, a flag, a size. Reporting the file offset it
        # happens to relocate onto is the classic false lead. The one escape
        # hatch is a small ODD word whose code target has a real prologue -
        # two independent signals agreeing is worth more than the threshold.
        if w & 1:
            c = reloc_to_fo(tgt, w & ~1)
            if 0 <= c < n and looks_like_prologue(data, c):
                return "FN?", c, None
        return "VAL", None, None
    tfo = reloc_to_fo(tgt, w)
    s = read_cstr(data, tfo, enc, minlen=3) if 0 <= tfo < n else None
    if s is not None and not looks_like_text(s):
        s = None
    cfo = reloc_to_fo(tgt, w & ~1) if (w & 1) else None
    code_ok = cfo is not None and 0 <= cfo < n
    if code_ok:
        prologue = looks_like_prologue(data, cfo)
        if s is not None and prologue:
            return "STR?", tfo, s
        return ("FN" if prologue else "FN?"), cfo, None
    if s is not None:
        return "STR", tfo, s
    if 0 <= tfo < n:
        return "PTR", tfo, None
    if n <= tfo < bss_end:
        return "BSS", tfo, None
    return "VAL", None, None


def run(tgt, data, start, count, min_ptr=SMALL_VALUE_MAX):
    base = int(tget(tgt, "image_base", 0))
    sl = tget(tgt, "sl_fo")
    bss_end = len(data) + int(tget(tgt, "bss_size", 0) or 0)
    rows = []
    for i in range(count):
        fo = start + 4 * i
        if fo + 4 > len(data):
            break
        w = int.from_bytes(data[fo:fo + 4], "little")
        kind, tfo, s = classify(tgt, data, w, bss_end, min_ptr)
        rows.append({"fo": fo, "guest": fo + base,
                     "sl_off": None if sl is None else fo - int(sl),
                     "word": w, "kind": kind, "target_fo": tfo, "text": s})
    return rows


def selftest():
    """Classification checks, including the regression this tool once failed."""
    fails = []

    def check(name, cond):
        if not cond:
            fails.append(name)

    DELTA = 0x100
    n = 0x4000
    tgt = {"image_base": 0x100000, "bss_size": 0x400, "sl_fo": 0x3000,
           "reloc_delta": DELTA, "reloc_unit": "file_offset",
           "encoding": "cp1252", "isa": "thumb"}
    img = bytearray(n)

    def put(fo, b):
        img[fo:fo + len(b)] = b

    def word(fo):
        return (fo - DELTA) & 0xFFFFFFFF

    # a real function: push {lr} ... pop {pc}
    put(0x1140, b"\x00\xb5\x00\x20\x00\xbd")
    # a jump-table arm: no prologue, and the reference port's counter-example
    # (it reads the PARENT's frame), so it must never classify as callable FN
    put(0x1160, b"\x3b\x1c\x18\x68")           # adds r3,r7,#0 ; ldr r0,[r3]
    # a leaf that is only `bx lr; nop` - the exact byte pattern that used to be
    # printed as a two-letter string under a permissive single-byte codec
    put(0x1180, b"\x70\x47\xc0\x46\x00\xb5")
    put(0x2000, b"CONNECTING TO SERVER\x00")
    put(0x2040, b"Hi\x00")                     # too short to be evidence

    bss_end = n + 0x400

    def cls(w):
        return classify(tgt, bytes(img), w, bss_end)

    check("real function -> FN", cls(word(0x1140) | 1)[0] == "FN")
    check("FN target is the even address", cls(word(0x1140) | 1)[1] == 0x1140)
    check("jump-table arm -> FN? (not callable)", cls(word(0x1160) | 1)[0] == "FN?")
    check("REGRESSION: `bx lr; nop` code pointer is FN?, never a string",
          cls(word(0x1180) | 1)[0] == "FN?")
    check("string pointer -> STR", cls(word(0x2000))[0] == "STR")
    check("string text is decoded",
          cls(word(0x2000))[2] == "CONNECTING TO SERVER")
    check("a 2-char run is not evidence of a string",
          cls(word(0x2040))[0] != "STR")
    check("zero -> VAL", cls(0)[0] == "VAL")
    check("small scalar is VAL, not a pointer to fo 0x12a", cls(0x2A)[0] == "VAL")
    check("--min-ptr can be lowered",
          classify(tgt, bytes(img), 0x2A, bss_end, min_ptr=4)[0] != "VAL")
    check("pointer past EOF but inside BSS -> BSS",
          cls(word(n + 0x10))[0] == "BSS")
    check("looks_like_text rejects short nonsense", not looks_like_text("G\xc0F"))
    check("looks_like_text accepts a path", looks_like_text("/comm/ui_bm"))
    check("looks_like_text accepts CJK characters",
          looks_like_text("저장하기"))

    # a run of >= 3 code pointers is a table, and run() must see all three
    for k, t in enumerate((0x1140, 0x1180, 0x1140)):
        img[0x2200 + 4 * k:0x2204 + 4 * k] = (word(t) | 1).to_bytes(4, "little")
    rows = run(tgt, bytes(img), 0x2200, 3)
    check("consecutive code pointers all classify as FN/FN?",
          all(r["kind"].startswith("FN") for r in rows))
    check("hexint accepts hex", hexint("0x10") == 16)

    if fails:
        for f in fails:
            print("FAIL %s" % f)
        print("selftest: %d failure(s)" % len(fails))
        return 1
    print("selftest: ok (16 checks)")
    return 0


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        prog="dump_table.py",
        description="Classify a run of 32-bit words: Thumb function pointer / "
                    "data pointer to a string / plain value. The fastest way to "
                    "read an unknown RWPI static area, and the check on whether "
                    "your static base is right.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="The start accepts 0x1234, sl+0x444, sl-0xb430 and g:0xGUEST.",
    )
    ap.add_argument("-t", "--target", help="path to target.toml")
    ap.add_argument("start", nargs="?",
                    help="first word, as a file offset or sl+/-offset")
    ap.add_argument("count", nargs="?", type=hexint,
                    help="how many 32-bit words to dump")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--out", help="write the dump to this file (utf-8)")
    ap.add_argument("--only", help="comma-separated kinds to print "
                                   "(FN,FN?,STR,STR?,PTR,BSS,VAL)")
    ap.add_argument("--min-ptr", type=hexint, default=SMALL_VALUE_MAX,
                    help="stored words below this are scalars, never pointers "
                         "(default 0x1000)")
    ap.add_argument("--binary", help="override target.toml `binary`")
    ap.add_argument("--selftest", action="store_true",
                    help="run the built-in classification checks and exit")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()
    missing = [n for n, v in (("-t/--target", a.target), ("start", a.start),
                              ("count", a.count)) if v is None]
    if missing:
        ap.error("the following arguments are required: %s "
                 "(or use --selftest)" % ", ".join(missing))
    if load_target is None:
        die("target.py must sit next to this tool (it loads target.toml)")
    tgt = load_target(a.target)
    data = read_image(tgt, a.target, a.binary)
    start = parse_fo(tgt, a.start, "start")
    if start < 0 or start >= len(data):
        die("start 0x%x is outside the image (0..0x%x)" % (start, len(data)))
    if a.count <= 0:
        die("count must be positive")
    if start % 4:
        sys.stderr.write("dump_table.py: warning: start 0x%x is not word "
                         "aligned; static slots normally are\n" % start)

    rows = run(tgt, data, start, a.count, a.min_ptr)
    keep = None
    if a.only:
        keep = {x.strip().upper() for x in a.only.split(",") if x.strip()}

    if a.json:
        text = json.dumps(
            {"start_fo": start, "count": len(rows),
             "rows": [r for r in rows if not keep or r["kind"] in keep]},
            indent=2, ensure_ascii=False, sort_keys=True)
    else:
        out = []
        sl = tget(tgt, "sl_fo")
        base = int(tget(tgt, "image_base", 0))
        delta, unit, declared = reloc_cfg(tgt)
        out.append("; %d words from fo 0x%06x%s, guest 0x%08x"
                   % (len(rows), start,
                      "" if sl is None else
                      " (sl%s0x%x)" % ("+" if start - int(sl) >= 0 else "-",
                                       abs(start - int(sl))),
                      start + base))
        out.append("; reloc: stored word + 0x%x = %s" % (delta, unit))
        if not declared:
            out.append("; !! target.toml declares no reloc_delta, so every "
                       "pointer here is being read unbiased. Run "
                       "solve_reloc.py first or this classification is noise.")
        out.append("; fo        sl+off     word        kind  target")
        for r in rows:
            if keep and r["kind"] not in keep:
                continue
            off = r["sl_off"]
            tail = ""
            if r["kind"].startswith("STR"):
                tail = '-> fo 0x%06x "%s"' % (r["target_fo"], r["text"])
                if r["kind"] == "STR?":
                    tail += "  (low bit set: could also be a code pointer)"
            elif r["kind"] in ("FN", "FN?"):
                tail = "-> code fo 0x%06x (guest 0x%08x, call as 0x%08x)" % (
                    r["target_fo"], r["target_fo"] + base,
                    (r["target_fo"] + base) | 1)
                if r["kind"] == "FN?":
                    tail += "  no prologue - may be a jump-table arm, not callable"
            elif r["kind"] == "PTR":
                tail = "-> fo 0x%06x" % r["target_fo"]
            elif r["kind"] == "BSS":
                tail = "-> fo 0x%06x (past EOF, inside the appended BSS)" % r["target_fo"]
            else:
                tail = "%d" % r["word"]
            slcol = ("-" if off is None
                     else "sl%s0x%x" % ("+" if off >= 0 else "-", abs(off)))
            out.append("%08x  %-10s %08x    %-5s %s"
                       % (r["fo"], slcol, r["word"], r["kind"], tail))
        # consecutive-FN runs are tables, not scalars
        i = 0
        notes = []
        while i < len(rows):
            if rows[i]["kind"].startswith("FN"):
                j = i
                while j + 1 < len(rows) and rows[j + 1]["kind"].startswith("FN"):
                    j += 1
                if j - i + 1 >= 3:
                    notes.append("; fo 0x%06x: a run of %d consecutive code "
                                 "pointers - jump table or vtable, not scalars"
                                 % (rows[i]["fo"], j - i + 1))
                i = j + 1
            else:
                i += 1
        out.extend(notes)
        kinds = {}
        for r in rows:
            kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        out.append("; %s" % ", ".join("%s=%d" % kv for kv in sorted(kinds.items())))
        if kinds.get("VAL", 0) > 0.9 * max(1, len(rows)):
            out.append("; WARNING: almost everything classified VAL. Either this "
                       "is not the static area, or sl_fo / reloc_delta in "
                       "target.toml is wrong (re-derive both with "
                       "solve_reloc.py before continuing), or this image is "
                       "small enough that real pointers fall below --min-ptr "
                       "0x%x - try lowering it." % a.min_ptr)
        text = "\n".join(out)

    if a.out:
        with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text + "\n")
        sys.stderr.write("dump_table.py: wrote %s\n" % a.out.replace("\\", "/"))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
