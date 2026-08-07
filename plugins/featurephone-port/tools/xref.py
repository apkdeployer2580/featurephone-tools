#!/usr/bin/env python3
"""xref.py - "who touches X?" for a feature-phone guest image. One tool, six questions.

WHAT IT DOES
    Answers the six cross-reference questions that actually come up while
    reverse-engineering an AOT-compiled ARM/Thumb guest image, by brute-force
    decoding of the whole image. No CFG, no symbols, ~1 second per query.

      calls   <addr>   who calls this function?              (Thumb BL/BLX pairs)
      literal <value>  who loads this VALUE?                 (pc-relative literal)
      pcrel   <addr>.. who loads from this POOL SLOT?        (a different question)
      offset  <imm>    who materialises this struct offset?  (all four idioms)
      fnptr   <addr>   where is this function STORED?        (all eight encodings)
      slot    <sl_off> who computes sl + this offset?        (RWPI static access)

METHOD (A)
    Each subcommand embodies one generic technique:

    * calls - decode every halfword pair matching the Thumb BL/BLX prefix
      (0xF000 first half; 0xF800 = BL, 0xE800 = BLX), sign-extend the 23-bit
      offset, `& ~3` for BLX. `--with-imm8-arg` then walks BACKWARD up to
      `--arg-span` halfwords for `movs rN,#imm8`, aborting at any branch - that
      recovers the constant id/enum passed to constant-argument APIs, which is
      how you find the ONE call site that shows a particular message.

    * literal vs pcrel - two different questions that look the same. `literal`
      asks "which code loads a word equal to V"; `pcrel` asks "which code loads
      from pool slot at file offset F". You need the second when you have found
      the slot but not the value, e.g. after bind_strings.py.

    * offset - a struct offset HAS NO SINGLE ENCODING. This tool enumerates
      four idioms; searching only one is how you "prove" a live field is
      unused:
        IMM-LOAD  ldr/str/ldrh/strh/ldrb/strb rD,[rB,#imm]  (offset folded into
                  the load - only possible while it fits the 5-bit field)
        CONST     movs rX,#imm8 [; lsls rX,#sh]             (register-offset
                  path; up to --sched halfwords of instruction scheduling may
                  sit between the two, so do not require adjacency)
        LITERAL   ldr rX,[pc,#..] whose pool word == the offset
        ADD-IMM   adds/subs rX,#imm8 / adds rD,rN,#imm3      (base fixups)

    * fnptr - you do NOT know a priori whether a vtable or jump table stores
      file offsets, guest addresses or relocation-biased words, nor whether the
      Thumb bit is set. Search all eight forms and let the hit tell you the
      convention.

    * slot - in an RWPI image there are no absolute data addresses in the
      instruction stream; statics are `sl + constant`. So the thing to search
      for is the OFFSET as a literal-pool word, not an address. This subcommand
      searches the offset and the two absolute forms, then chains each hit to
      the instructions that load it.

KNOWN LIMITS
    * A blind halfword scan cannot distinguish code from data: some hits will
      be literal pools that happen to look like a BL pair. Confirm any hit that
      matters by disassembling around it with dis.py --resilient.
    * Thumb only for instruction idioms (this is what AOT feature-phone images
      are compiled to); `pcrel` additionally scans the two ARM literal-load
      encodings.
    * Alignment: the scanner considers every 2-byte position, so a pair
      straddling a real instruction boundary can produce a phantom call. Rank
      by plausibility, do not treat the count as ground truth.
    * Everything is a file offset. Guest address = file offset + image_base.
      Write `g:0x23d0aa` to pass a guest address instead.

USAGE
    python xref.py -t target.toml calls 0xCALLEE_FO --with-imm8-arg
    python xref.py -t target.toml literal 0xVALUE
    python xref.py -t target.toml pcrel 0xPOOL_FO 0xPOOL_FO2
    python xref.py -t target.toml offset 0xFIELD_OFF --json
    python xref.py -t target.toml fnptr 0xFUNCTION_FO
    python xref.py -t target.toml slot sl+0xSLOT_OFF
    python xref.py --selftest
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


# --------------------------------------------------------------------------
def die(msg: str, code: int = 2):
    sys.stderr.write("xref.py: error: %s\n" % msg)
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
        die("%s: cannot parse %r as an integer (use 0x1234 or 4660)" % (what, s))


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


def parse_sl_off(tgt, s, what="sl offset"):
    """`0x444`, `sl+0x444`, `sl-0xb430`, `-0x400`. Returns a SIGNED offset.

    Write the `sl-0x...` form rather than a bare leading minus: argparse reads
    a leading `-` as an option, and negative static offsets are common (jump
    tables are often placed below the static base).
    """
    t = str(s).strip().replace("_", "").lower()
    if t.startswith("sl+"):
        return parse_int(t[3:], what)
    if t.startswith("sl-"):
        return -parse_int(t[3:], what)
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
    if a.itemsize != 2:  # pragma: no cover
        die("this platform has no 2-byte array type")
    a.frombytes(bytes(data[: len(data) & ~1]))
    if sys.byteorder == "big":
        a.byteswap()
    return a


def words(data):
    a = array.array("I")
    if a.itemsize != 4:  # pragma: no cover
        a = array.array("L")
        if a.itemsize != 4:
            die("this platform has no 4-byte array type")
    a.frombytes(bytes(data[: len(data) & ~3]))
    if sys.byteorder == "big":
        a.byteswap()
    return a


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


# --------------------------------------------------------------------------
# instruction-level scanners (Thumb-1 unless stated)
# --------------------------------------------------------------------------
def is_branchish(h):
    """Anything that ends a straight-line peephole window."""
    if 0xD000 <= h <= 0xDFFF:      # b<cond> / svc / udf
        return True
    if 0xE000 <= h <= 0xE7FF:      # b.n
        return True
    if (h & 0xF800) == 0xF000:     # first half of BL/BLX/b.w
        return True
    if (h & 0xFF80) == 0x4700:     # bx / blx reg
        return True
    if 0xBD00 <= h <= 0xBDFF:      # pop {..,pc}
        return True
    return False


def scan_bl(hw):
    """Yield (site_fo, target_fo, kind) for every Thumb BL/BLX halfword pair."""
    n = len(hw) - 1
    for i in range(n):
        h1 = hw[i]
        if (h1 & 0xF800) != 0xF000:
            continue
        h2 = hw[i + 1]
        top = h2 & 0xF800
        if top == 0xF800:
            kind = "bl"
        elif top == 0xE800:
            kind = "blx"
        else:
            continue
        off = ((h1 & 0x7FF) << 12) | ((h2 & 0x7FF) << 1)
        if off & 0x400000:
            off -= 0x800000
        tgt = (i * 2) + 4 + off
        if kind == "blx":
            tgt &= ~3
        yield i * 2, tgt, kind


def imm8_before(hw, site_hw_index, reg, span):
    """`movs rREG,#imm8` within `span` halfwords before the site, or None."""
    want = 0x2000 | (reg << 8)
    lo = max(-1, site_hw_index - 1 - span)
    for j in range(site_hw_index - 1, lo, -1):
        h = hw[j]
        if (h & 0xFF00) == want:
            return h & 0xFF
        if is_branchish(h):
            return None
    return None


def thumb_pcrel(hw, dlen):
    """Yield (site_fo, rd, literal_fo) for `ldr rD,[pc,#imm8*4]` (0x4800..0x4FFF)."""
    for i, h in enumerate(hw):
        if 0x4800 <= h <= 0x4FFF:
            fo = i * 2
            lit = ((fo + 4) & ~3) + (h & 0xFF) * 4
            if lit + 4 <= dlen:
                yield fo, (h >> 8) & 7, lit


def arm_pcrel(wds, dlen):
    """Yield (site_fo, rt, literal_fo) for ARM `ldr rT,[pc,#+/-imm12]`."""
    for i, w in enumerate(wds):
        top = w & 0x0FFF0000
        if top == 0x051F0000:
            sign = -1
        elif top == 0x059F0000:
            sign = 1
        else:
            continue
        fo = i * 4
        lit = fo + 8 + sign * (w & 0xFFF)
        if 0 <= lit and lit + 4 <= dlen:
            yield fo, (w >> 12) & 0xF, lit


IMM_LOADS = (
    # (base_opcode, mask_hi, scale, name)
    (0x6800, 0xF800, 4, "ldr"),
    (0x6000, 0xF800, 4, "str"),
    (0x8800, 0xF800, 2, "ldrh"),
    (0x8000, 0xF800, 2, "strh"),
    (0x7800, 0xF800, 1, "ldrb"),
    (0x7000, 0xF800, 1, "strb"),
)


def scan_imm_load(hw, target):
    """Struct offset folded straight into a Thumb immediate-offset load/store."""
    for base, mask, scale, name in IMM_LOADS:
        if target % scale:
            continue
        imm5 = target // scale
        if not (0 <= imm5 <= 31):
            continue
        for i, h in enumerate(hw):
            if (h & mask) == base and ((h >> 6) & 31) == imm5:
                yield i * 2, "%s r%d,[r%d,#0x%x]" % (name, h & 7, (h >> 3) & 7, target)


def scan_const_build(hw, target, sched):
    """`movs rX,#imm8` [+ `lsls rX,rX,#sh`] materialising `target` in a register.

    The shift may be several instructions after the move: the compiler
    schedules other work between them. Requiring adjacency is the single most
    common way to miss real sites.
    """
    if 0 <= target <= 0xFF:
        want = 0x2000 | (target & 0xFF)
        for i, h in enumerate(hw):
            if (h & 0xF8FF) == want:
                yield i * 2, "movs r%d,#0x%x" % ((h >> 8) & 7, target), None
    for i, h in enumerate(hw):
        if (h & 0xF800) != 0x2000:
            continue
        imm8 = h & 0xFF
        rd = (h >> 8) & 7
        if imm8 == 0:
            continue
        sh = None
        for s in range(1, 25):
            if (imm8 << s) == target:
                sh = s
                break
        if sh is None:
            continue
        for j in range(i + 1, min(i + 1 + sched, len(hw))):
            h2 = hw[j]
            if (h2 & 0xF800) == 0x0000 and ((h2 >> 6) & 31) == sh and \
                    ((h2 >> 3) & 7) == rd:
                yield (i * 2,
                       "movs r%d,#0x%x ; lsls r%d,r%d,#%d"
                       % (rd, imm8, h2 & 7, rd, sh), j * 2)
                break
            if is_branchish(h2):
                break


def scan_add_imm(hw, target):
    """`adds/subs rX,#imm8` and `adds/subs rD,rN,#imm3` carrying the offset."""
    if target <= 0xFF:
        for i, h in enumerate(hw):
            if (h & 0xF800) == 0x3000 and (h & 0xFF) == target:
                yield i * 2, "adds r%d,#0x%x" % ((h >> 8) & 7, target)
            elif (h & 0xF800) == 0x3800 and (h & 0xFF) == target:
                yield i * 2, "subs r%d,#0x%x" % ((h >> 8) & 7, target)
    if target <= 7:
        for i, h in enumerate(hw):
            if (h & 0xFE00) == 0x1C00 and ((h >> 6) & 7) == target:
                yield i * 2, "adds r%d,r%d,#%d" % (h & 7, (h >> 3) & 7, target)
            elif (h & 0xFE00) == 0x1E00 and ((h >> 6) & 7) == target:
                yield i * 2, "subs r%d,r%d,#%d" % (h & 7, (h >> 3) & 7, target)


def word_occurrences(wds, value):
    """Word-aligned file offsets whose stored word equals `value`."""
    v = value & 0xFFFFFFFF
    out = []
    start = 0
    n = len(wds)
    while start < n:
        try:
            i = wds.index(v, start)
        except TypeError:  # array.index(x, start) needs Python 3.10+
            return [k * 4 for k, w in enumerate(wds) if w == v]
        except ValueError:
            break
        out.append(i * 4)
        start = i + 1
    return out


# --------------------------------------------------------------------------
# subcommands
# --------------------------------------------------------------------------
class Env:
    def __init__(self, tgt, data):
        self.t = tgt
        self.data = data
        self.hw = halfwords(data)
        self.wd = words(data)
        self.enc = str(tget(tgt, "encoding", "ascii"))
        self.base = int(tget(tgt, "image_base", 0))
        sl = tget(tgt, "sl_fo")
        self.sl = None if sl is None else int(sl)
        self.delta, self.unit, self.reloc_declared = reloc_cfg(tgt)

    def g(self, fo):
        return fo + self.base

    def need_sl(self):
        if self.sl is None:
            die("this subcommand needs `sl_fo` in target.toml (the RWPI static "
                "base as a FILE OFFSET) and it is not declared.")
        return self.sl

    def sl_note(self, fo):
        if self.sl is None:
            return None
        d = fo - self.sl
        if -0x8000 <= d <= 0x40000:
            return "sl%s0x%x" % ("+" if d >= 0 else "-", abs(d))
        return None


def cmd_calls(env, a):
    want = parse_fo(env.t, a.addr, "callee")
    rows = []
    for site, tgt, kind in scan_bl(env.hw):
        if tgt != want and tgt != (want & ~1):
            continue
        row = {"site_fo": site, "site_guest": env.g(site), "kind": kind,
               "target_fo": tgt}
        if a.with_imm8_arg:
            row["arg"] = imm8_before(env.hw, site // 2, a.arg_reg, a.arg_span)
        rows.append(row)
    if a.json:
        return dump_json({"query": "calls", "target_fo": want, "hits": rows})
    print("; callers of fo 0x%06x (guest 0x%08x)" % (want, env.g(want)))
    for r in rows[: a.limit or None]:
        extra = ""
        if a.with_imm8_arg:
            extra = ("   arg r%d=#0x%02x" % (a.arg_reg, r["arg"])
                     if r.get("arg") is not None else
                     "   arg r%d=? (no movs within %d halfwords, or a branch "
                     "intervened)" % (a.arg_reg, a.arg_span))
        print("%08x  %-4s -> 0x%06x%s" % (r["site_fo"], r["kind"], r["target_fo"], extra))
    print("; %d call site(s)" % len(rows))
    if a.with_imm8_arg:
        args = sorted({r["arg"] for r in rows if r.get("arg") is not None})
        print("; distinct recovered r%d constants: %s"
              % (a.arg_reg, ", ".join("0x%02x" % x for x in args) or "(none)"))
    return 0


def cmd_literal(env, a):
    value = parse_int(a.value, "value") & 0xFFFFFFFF
    slots = set(word_occurrences(env.wd, value))
    rows = []
    for site, rd, lit in thumb_pcrel(env.hw, len(env.data)):
        if lit in slots:
            rows.append({"site_fo": site, "rd": rd, "slot_fo": lit, "isa": "thumb"})
    for site, rt, lit in arm_pcrel(env.wd, len(env.data)):
        if lit in slots and int.from_bytes(env.data[lit:lit + 4], "little") == value:
            rows.append({"site_fo": site, "rd": rt, "slot_fo": lit, "isa": "arm"})
    if a.json:
        return dump_json({"query": "literal", "value": value,
                          "pool_slots": sorted(slots), "hits": rows})
    print("; code loading the value 0x%08x" % value)
    for r in rows:
        print("%08x  ldr r%d,[pc] -> slot 0x%06x  (%s)"
              % (r["site_fo"], r["rd"], r["slot_fo"], r["isa"]))
    print("; %d loader site(s); %d word-aligned occurrence(s) of the value"
          % (len(rows), len(slots)))
    unref = sorted(slots - {r["slot_fo"] for r in rows})
    if unref:
        print("; %d occurrence(s) with no pc-relative loader (data tables, or "
              "reached as sl+off - try `xref.py slot`):" % len(unref))
        for s in unref[: (a.limit or 20)]:
            note = env.sl_note(s)
            print(";   0x%06x%s" % (s, "  (%s)" % note if note else ""))
    return 0


def cmd_pcrel(env, a):
    wanted = {parse_fo(env.t, x, "slot") for x in a.addrs}
    rows = []
    for site, rd, lit in thumb_pcrel(env.hw, len(env.data)):
        if lit in wanted:
            rows.append({"site_fo": site, "rd": rd, "slot_fo": lit, "isa": "thumb",
                         "word": int.from_bytes(env.data[lit:lit + 4], "little")})
    for site, rt, lit in arm_pcrel(env.wd, len(env.data)):
        if lit in wanted:
            rows.append({"site_fo": site, "rd": rt, "slot_fo": lit, "isa": "arm",
                         "word": int.from_bytes(env.data[lit:lit + 4], "little")})
    if a.json:
        return dump_json({"query": "pcrel", "slots": sorted(wanted), "hits": rows})
    print("; code loading FROM these pool slots")
    for r in sorted(rows, key=lambda x: (x["slot_fo"], x["site_fo"])):
        s = read_cstr(env.data, reloc_to_fo(env.t, r["word"]), env.enc)
        print('%08x  ldr r%d,[pc] -> slot 0x%06x = 0x%08x  (%s)%s'
              % (r["site_fo"], r["rd"], r["slot_fo"], r["word"], r["isa"],
                 '  "%s"' % s if s else ""))
    print("; %d site(s) over %d slot(s)" % (len(rows), len(wanted)))
    return 0


def cmd_offset(env, a):
    target = parse_int(a.imm, "offset")
    rows = []
    for fo, text in scan_imm_load(env.hw, target):
        rows.append({"idiom": "IMM-LOAD", "site_fo": fo, "text": text})
    for fo, text, second in scan_const_build(env.hw, target, a.sched):
        rows.append({"idiom": "CONST", "site_fo": fo, "text": text,
                     "second_fo": second})
    slots = set(word_occurrences(env.wd, target))
    for site, rd, lit in thumb_pcrel(env.hw, len(env.data)):
        if lit in slots:
            rows.append({"idiom": "LITERAL", "site_fo": site,
                         "text": "ldr r%d,[pc] -> slot 0x%06x = 0x%x" % (rd, lit, target)})
    for fo, text in scan_add_imm(env.hw, target):
        rows.append({"idiom": "ADD-IMM", "site_fo": fo, "text": text})
    rows.sort(key=lambda r: (r["site_fo"], r["idiom"]))
    if a.limit:
        rows = rows[: a.limit]
    if a.json:
        return dump_json({"query": "offset", "offset": target, "hits": rows})
    print("; sites materialising the struct offset 0x%x (%d) - four idioms"
          % (target, target))
    for r in rows:
        print("%-9s %08x  %s" % (r["idiom"], r["site_fo"], r["text"]))
    by = {}
    for r in rows:
        by[r["idiom"]] = by.get(r["idiom"], 0) + 1
    print("; %d site(s): %s" % (len(rows),
                                ", ".join("%s=%d" % kv for kv in sorted(by.items()))
                                or "none"))
    if not rows:
        print("; NOTE: zero hits does NOT prove the field is unused. Check that "
              "the offset is right, widen --sched, and remember a field can be "
              "reached as base+register computed elsewhere.")
    return 0


def fnptr_forms(env, fo):
    """The eight ways a code pointer to `fo` can plausibly be stored."""
    base = env.base
    delta, unit = env.delta, env.unit
    unit_val = fo if unit == "file_offset" else fo + base
    forms = [
        ("file_offset", fo),
        ("guest_address", fo + base),
        ("reloc_biased", unit_val - delta),
        ("reloc_biased_other_unit",
         (fo + base if unit == "file_offset" else fo) - delta),
    ]
    out = []
    seen = set()
    for name, v in forms:
        for tb in (0, 1):
            w = (v | tb) & 0xFFFFFFFF
            if w in seen:
                continue
            seen.add(w)
            out.append(("%s%s" % (name, "|thumb" if tb else ""), w))
    return out


def cmd_fnptr(env, a):
    fo = parse_fo(env.t, a.addr, "function")
    rows = []
    for name, w in fnptr_forms(env, fo):
        for at in word_occurrences(env.wd, w):
            rows.append({"at_fo": at, "encoding": name, "word": w,
                         "sl": env.sl_note(at)})
    rows.sort(key=lambda r: r["at_fo"])
    if a.json:
        return dump_json({"query": "fnptr", "target_fo": fo,
                          "forms": [{"encoding": n, "word": w}
                                    for n, w in fnptr_forms(env, fo)],
                          "hits": rows})
    print("; stored pointers to fo 0x%06x (guest 0x%08x)" % (fo, env.g(fo)))
    print("; searched forms: %s"
          % ", ".join("%s=0x%08x" % (n, w) for n, w in fnptr_forms(env, fo)))
    for r in rows[: a.limit or None]:
        print("%08x  0x%08x  %-24s%s"
              % (r["at_fo"], r["word"], r["encoding"],
                 "  (%s)" % r["sl"] if r["sl"] else ""))
    print("; %d stored reference(s)" % len(rows))
    if rows:
        encs = sorted({r["encoding"] for r in rows})
        print("; the convention this image uses appears to be: %s" % ", ".join(encs))
        runs = consecutive_runs([r["at_fo"] for r in rows])
        for s, n in runs:
            if n >= 3:
                print("; 0x%06x starts a run of %d consecutive pointer words - "
                      "that is a jump table or vtable, not a scalar." % (s, n))
    return 0


def consecutive_runs(offsets):
    offs = sorted(set(offsets))
    runs = []
    i = 0
    while i < len(offs):
        j = i
        while j + 1 < len(offs) and offs[j + 1] == offs[j] + 4:
            j += 1
        runs.append((offs[i], j - i + 1))
        i = j + 1
    return runs


def cmd_slot(env, a):
    off = parse_sl_off(env.t, a.sl_off)
    base = env.base
    delta, unit = env.delta, env.unit
    abs_fo = env.need_sl() + off
    abs_unit = abs_fo if unit == "file_offset" else abs_fo + base
    forms = [
        ("bare_offset", off & 0xFFFFFFFF),
        ("abs_file_offset", abs_fo & 0xFFFFFFFF),
        ("abs_guest_address", (abs_fo + base) & 0xFFFFFFFF),
        ("abs_reloc_biased", (abs_unit - delta) & 0xFFFFFFFF),
    ]
    slot_word = int.from_bytes(env.data[abs_fo:abs_fo + 4], "little") \
        if 0 <= abs_fo and abs_fo + 4 <= len(env.data) else None
    rows = []
    pcrel_index = {}
    for site, rd, lit in thumb_pcrel(env.hw, len(env.data)):
        pcrel_index.setdefault(lit, []).append((site, rd))
    for name, w in forms:
        for at in word_occurrences(env.wd, w):
            rows.append({"pool_fo": at, "encoding": name, "word": w,
                         "loaders": [{"site_fo": s, "rd": r}
                                     for s, r in pcrel_index.get(at, [])]})
    rows.sort(key=lambda r: r["pool_fo"])
    if a.json:
        return dump_json({"query": "slot", "sl_off": off, "slot_fo": abs_fo,
                          "slot_word": slot_word, "hits": rows})
    print("; static slot sl%s0x%x = file offset 0x%06x (guest 0x%08x)"
          % ("+" if off >= 0 else "-", abs(off), abs_fo, abs_fo + base))
    if slot_word is not None:
        tfo = reloc_to_fo(env.t, slot_word)
        s = read_cstr(env.data, tfo, env.enc)
        print("; the slot currently holds 0x%08x -> fo 0x%06x%s"
              % (slot_word, tfo, '  "%s"' % s if s else ""))
    print("; searched forms: %s" % ", ".join("%s=0x%08x" % f for f in forms))
    for r in rows:
        print("%08x  0x%08x  %s" % (r["pool_fo"], r["word"], r["encoding"]))
        for ld in r["loaders"]:
            print("            <- %08x  ldr r%d,[pc]" % (ld["site_fo"], ld["rd"]))
    print("; %d pool word(s), %d loader instruction(s)"
          % (len(rows), sum(len(r["loaders"]) for r in rows)))
    return 0


def dump_json(obj):
    print(json.dumps(obj, indent=2, sort_keys=True))
    return 0


# --------------------------------------------------------------------------
def selftest():
    fails = []

    def check(name, cond):
        if not cond:
            fails.append(name)

    img = bytearray(0x400)

    def put16(fo, h):
        img[fo:fo + 2] = int(h).to_bytes(2, "little")

    def put32(fo, w):
        img[fo:fo + 4] = (int(w) & 0xFFFFFFFF).to_bytes(4, "little")

    def bl(frm, to):
        off = to - (frm + 4)
        u = off & 0x7FFFFF
        put16(frm, 0xF000 | ((u >> 12) & 0x7FF))
        put16(frm + 2, 0xF800 | ((u >> 1) & 0x7FF))

    put16(0x1C, 0x2067)          # movs r0,#0x67
    bl(0x20, 0x100)              # forward BL
    bl(0x100, 0x20)              # backward BL
    put16(0x40, 0x4B04)          # ldr r3,[pc,#0x10]  -> lit 0x54
    put32(0x54, 0xDEADBEEF)
    put16(0x60, 0x231A)          # movs r3,#0x1a
    put16(0x62, 0x00DB)          # lsls r3,r3,#3      -> 0xd0
    put16(0x70, 0x6988)          # ldr r0,[r1,#0x18]
    put16(0x74, 0x3018)          # adds r0,#0x18

    class T(dict):
        pass

    t = T(image_base=0x100000, reloc_delta=0x800, reloc_unit="file_offset",
          sl_fo=0x200, encoding="ascii", isa="thumb")
    env = Env(t, bytes(img))

    calls = list(scan_bl(env.hw))
    check("bl forward", (0x20, 0x100, "bl") in calls)
    check("bl backward", (0x100, 0x20, "bl") in calls)
    check("imm8 arg", imm8_before(env.hw, 0x20 // 2, 0, 10) == 0x67)
    check("imm8 arg aborts at branch", imm8_before(env.hw, 0x100 // 2, 0, 10) is None)

    pc = {(s, lit) for s, _rd, lit in thumb_pcrel(env.hw, len(img))}
    check("pcrel slot", (0x40, 0x54) in pc)
    check("literal value",
          0x54 in set(word_occurrences(env.wd, 0xDEADBEEF)))

    const = list(scan_const_build(env.hw, 0xD0, 6))
    check("const build movs+lsls", any(fo == 0x60 for fo, _t, _s in const))
    iml = list(scan_imm_load(env.hw, 0x18))
    check("imm-offset load", any(fo == 0x70 for fo, _t in iml))
    addi = list(scan_add_imm(env.hw, 0x18))
    check("adds imm8", any(fo == 0x74 for fo, _t in addi))

    # fnptr: all eight forms, and the reloc-biased|thumb one must be present
    forms = dict((n, w) for n, w in fnptr_forms(env, 0x100))
    check("fnptr file_offset", forms.get("file_offset") == 0x100)
    check("fnptr guest|thumb", forms.get("guest_address|thumb") == 0x100101)
    check("fnptr biased|thumb",
      forms.get("reloc_biased|thumb") == (((0x100 - 0x800) & 0xFFFFFFFF) | 1))

    t2 = T(image_base=0x100000, reloc_delta=0x800, reloc_unit="guest_address",
           sl_fo=0x200, encoding="ascii", isa="thumb")
    env2 = Env(t2, bytes(img))
    f2 = dict(fnptr_forms(env2, 0x100))
    check("fnptr biased honours unit",
          f2.get("reloc_biased") == (0x100 + 0x100000 - 0x800))

    check("runs", consecutive_runs([0x10, 0x14, 0x18, 0x30]) == [(0x10, 3), (0x30, 1)])
    check("reloc unit fo", reloc_to_fo(t, 0xAB800) == 0xAC000)
    check("reloc unit guest", reloc_to_fo(t2, 0x1AB800) == 0xAC000)

    if fails:
        for f in fails:
            print("FAIL %s" % f)
        print("selftest: %d failure(s)" % len(fails))
        return 1
    print("selftest: ok (17 checks)")
    return 0


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        prog="xref.py",
        description="Cross-reference a guest image: who calls / loads / stores / "
                    "materialises X. All offsets are FILE OFFSETS; write "
                    "g:0xADDR to pass a guest address.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Every subcommand accepts --json and --limit. Confirm any hit "
               "that matters by disassembling around it with dis.py --resilient.",
    )
    ap.add_argument("-t", "--target", help="path to target.toml")
    ap.add_argument("--binary", help="override target.toml `binary`")
    ap.add_argument("--selftest", action="store_true",
                    help="run the built-in decoder checks and exit")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--json", action="store_true", help="machine-readable output")
        p.add_argument("--limit", type=int, default=0, help="cap printed rows")
        return p

    p = common(sub.add_parser("calls", help="who calls this function (BL/BLX)"))
    p.add_argument("addr", help="callee file offset (or g:0xGUEST)")
    p.add_argument("--with-imm8-arg", action="store_true",
                   help="also recover `movs rN,#imm8` set just before the call")
    p.add_argument("--arg-reg", type=int, default=0,
                   help="register the constant argument lands in (default r0)")
    p.add_argument("--arg-span", type=int, default=10,
                   help="halfwords to walk back (default 10 = 20 bytes)")
    p.set_defaults(fn=cmd_calls)

    p = common(sub.add_parser("literal", help="who loads this VALUE"))
    p.add_argument("value", help="32-bit value")
    p.set_defaults(fn=cmd_literal)

    p = common(sub.add_parser("pcrel", help="who loads from these POOL SLOTS"))
    p.add_argument("addrs", nargs="+", help="literal-pool file offsets")
    p.set_defaults(fn=cmd_pcrel)

    p = common(sub.add_parser("offset", help="who materialises this struct offset"))
    p.add_argument("imm", help="struct offset, e.g. 0xe4")
    p.add_argument("--sched", type=int, default=6,
                   help="halfwords of instruction scheduling allowed between "
                        "movs and lsls (default 6)")
    p.set_defaults(fn=cmd_offset)

    p = common(sub.add_parser("fnptr", help="where is this function STORED"))
    p.add_argument("addr", help="function file offset (or g:0xGUEST)")
    p.set_defaults(fn=cmd_fnptr)

    p = common(sub.add_parser("slot", help="who computes sl + this offset"))
    p.add_argument("sl_off", help="static offset: 0x444, sl+0x444 or sl-0xb430")
    p.set_defaults(fn=cmd_slot)

    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.cmd:
        ap.print_help()
        return 2
    if not a.target:
        ap.error("-t/--target is required (or use --selftest)")
    if load_target is None:
        die("target.py must sit next to this tool (it loads target.toml)")
    tgt = load_target(a.target)
    data = read_image(tgt, a.target, a.binary)
    return a.fn(Env(tgt, data), a)


if __name__ == "__main__":
    raise SystemExit(main())
