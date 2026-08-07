#!/usr/bin/env python3
"""thumb_enc.py - encode (and decode) the Thumb halfwords used for size-neutral guest patches.

WHAT IT DOES
    Turns one instruction into the exact halfword(s) to write, and tells you
    what the bytes currently there mean. It exists because guest patching is
    done by hand-editing single instructions, and every arithmetic slip in that
    process is silent: a wrong halfword does not fail to assemble, it just
    makes the game behave strangely somewhere else.

        python thumb_enc.py "movs r1,#25"
        python thumb_enc.py "subs r1,r3,#5" --verify-orig 0x1C19
        python thumb_enc.py "b <from_fo> <to_fo>"
        python thumb_enc.py --decode 0xHALFWORD

    (0x1C19 above is `adds r1,r3,#0` - the shape a reduce-a-threshold patch
     replaces. It is a worked example (B) from the reference port, reached by
     the gate-finding recipe in the guest-re skill, not a constant to reuse.)

METHOD (A) - three rules this tool enforces
    1. SIZE-NEUTRAL, SINGLE HALFWORD. Patch by rewriting one instruction in
       place, never by inserting or deleting bytes: everything downstream keeps
       its address and no other analysis is invalidated.

    2. THE ENCODING BOUNDS THE FEATURE. Thumb-1 `subs rD,rN,#imm3` has a THREE
       BIT immediate, so the largest reduction you can express that way is 7.
       Thumb-1 `movs rD,#imm8` has eight, so the largest constant is 255. That
       is not a limitation to work around; it is the specification of what your
       feature can be. Decide the feature after you have read the encoding, and
       state the limit in the user-facing text.
       (Corollary, worked example (B): a reward multiplier implemented by
       editing a `movs rX,#100` DIVISOR is exact only when the multiplier
       divides 100 - x2 becomes #50 and x4 becomes #25, while x3 is impossible
       rather than 3.03. Refuse the inexact case in code, not in prose.)

    3. READ-VERIFY-THEN-WRITE. Never write a patch unconditionally. Read the
       halfword first and write only if it equals the documented original;
       otherwise leave a different build of the game untouched instead of
       corrupting it. That also makes the patch idempotent, so a worker can
       call it every frame. `--verify-orig` is that check performed offline:
       it decodes what you believe is there and compares its shape with the
       replacement.

WHAT THIS TOOL WILL NOT DO
    It never modifies the guest image. Patching a shipped binary is the wrong
    model: the host writes GUEST RAM after boot, so a mis-patch cannot survive
    a restart and the distributed asset stays byte-identical to the original.
    This tool only prints bytes for your host code to write.

KNOWN LIMITS
    * Thumb-1 (ARMv4T/v5T) low-register forms, plus `b.w` and `bl` pairs. No
      Thumb-2 data processing, no IT blocks, no ARM mode.
    * A liveness check is beyond a text encoder. Before rewriting an
      instruction, prove by disassembly that (a) any register whose value you
      change is dead or is re-written before its next use, and (b) the
      condition flags you clobber are re-established before their next use.
      The tool warns where the shapes differ; it cannot prove liveness.
    * `b.w` emits the Thumb-2 T4 encoding. On an ARMv4T/v5T core - which is
      what feature-phone titles are usually built for - that encoding is
      UNDEFINED and will fault. Use `b` (+/-2 KB) when it reaches, and treat
      `bl` as a long branch only where LR is genuinely dead.

USAGE
    python thumb_enc.py "subs r1,r3,#5" --verify-orig 0x1C19
    python thumb_enc.py -t target.toml --at 0xPATCH_SITE_FO "subs r1,r3,#5" --verify-orig 0xORIG
    python thumb_enc.py --decode 0xHW1,0xHW2 --at 0xPATCH_SITE_FO
    python thumb_enc.py --selftest
"""

from __future__ import annotations

import argparse
import json
import os
import re
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


class EncError(Exception):
    pass


def die(msg: str, code: int = 2):
    sys.stderr.write("thumb_enc.py: error: %s\n" % msg)
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


def parse_int(s, what="value"):
    try:
        return int(str(s).strip().replace("_", "").lstrip("#"), 0)
    except ValueError:
        raise EncError("cannot parse %r as an integer (%s)" % (s, what))


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


def sx(v, bits):
    m = 1 << (bits - 1)
    return (v ^ m) - m


# --------------------------------------------------------------------------
def reg(tok):
    t = tok.strip().lower()
    m = re.fullmatch(r"r(\d+)", t)
    if m:
        n = int(m.group(1))
    elif t in ("sl",):
        n = 10
    elif t in ("sb",):
        n = 9
    elif t == "fp":
        n = 11
    elif t == "ip":
        n = 12
    elif t == "sp":
        n = 13
    elif t == "lr":
        n = 14
    elif t == "pc":
        n = 15
    else:
        raise EncError("not a register: %r" % tok)
    if not (0 <= n <= 15):
        raise EncError("register out of range: %r" % tok)
    return n


def low(tok):
    n = reg(tok)
    if n > 7:
        raise EncError("Thumb-1 data processing takes LOW registers r0-r7 only; "
                       "got %s" % tok)
    return n


def bound(name, value, lo, hi, extra=""):
    if not (lo <= value <= hi):
        raise EncError("%s = %d is outside the encodable range %d..%d.%s"
                       % (name, value, lo, hi, (" " + extra) if extra else ""))


CONDS = {"eq": 0, "ne": 1, "cs": 2, "hs": 2, "cc": 3, "lo": 3, "mi": 4, "pl": 5,
         "vs": 6, "vc": 7, "hi": 8, "ls": 9, "ge": 10, "lt": 11, "gt": 12, "le": 13}

SHIFTS = {"lsls": 0x0000, "lsrs": 0x0800, "asrs": 0x1000}


def encode(expr):
    """expr -> (halfwords, warnings). Raises EncError with a precise reason."""
    toks = [t for t in re.split(r"[\s,]+", expr.strip()) if t]
    if not toks:
        raise EncError("empty expression")
    mn = toks[0].lower()
    a = toks[1:]
    warn = []

    def imm(tok):
        return parse_int(tok, "immediate")

    if mn == "nop":
        return [0x46C0], ["Thumb-1 nop is `mov r8,r8` (0x46c0); it is not a "
                          "no-write instruction on every core, but it changes "
                          "no architectural state."]
    if mn == "bx" and len(a) == 1:
        return [0x4700 | (reg(a[0]) << 3)], []
    if mn == "pop" and "".join(a).lower() in ("{pc}",):
        return [0xBD00], []

    if mn in ("movs", "mov") and len(a) == 2 and a[1].startswith("#"):
        rd, v = low(a[0]), imm(a[1])
        bound("imm8", v, 0, 255,
              "imm8 is EIGHT bits - a larger constant needs a literal pool, "
              "which is not size-neutral.")
        return [0x2000 | (rd << 8) | v], []

    if mn == "cmp" and len(a) == 2 and a[1].startswith("#"):
        rd, v = low(a[0]), imm(a[1])
        bound("imm8", v, 0, 255)
        return [0x2800 | (rd << 8) | v], []

    if mn in ("adds", "subs") and len(a) == 2 and a[1].startswith("#"):
        rd, v = low(a[0]), imm(a[1])
        bound("imm8", v, 0, 255)
        base = 0x3000 if mn == "adds" else 0x3800
        return [base | (rd << 8) | v], ["writes the flags"]

    if mn in ("adds", "subs") and len(a) == 3 and a[2].startswith("#"):
        rd, rn, v = low(a[0]), low(a[1]), imm(a[2])
        bound("imm3", v, 0, 7,
              "imm3 is THREE bits. If your feature needs more than 7, this "
              "encoding cannot express it - change the feature or patch a "
              "different instruction.")
        base = 0x1C00 if mn == "adds" else 0x1E00
        return [base | (v << 6) | (rn << 3) | rd], ["writes the flags"]

    if mn in ("adds", "subs") and len(a) == 3:
        rd, rn, rm = low(a[0]), low(a[1]), low(a[2])
        base = 0x1800 if mn == "adds" else 0x1A00
        return [base | (rm << 6) | (rn << 3) | rd], ["writes the flags"]

    if mn in SHIFTS and len(a) == 3 and a[2].startswith("#"):
        rd, rm, v = low(a[0]), low(a[1]), imm(a[2])
        bound("imm5", v, 0, 31)
        if mn == "lsls" and v == 0:
            warn.append("`lsls rD,rM,#0` encodes as `movs rD,rM`")
        return [SHIFTS[mn] | (v << 6) | (rm << 3) | rd], ["writes the flags"]

    if mn in ("movs",) and len(a) == 2:
        rd, rm = low(a[0]), low(a[1])
        return [(rm << 3) | rd], ["writes the flags (this is lsls rD,rM,#0)"]

    if mn == "mov" and len(a) == 2:
        rd, rm = reg(a[0]), reg(a[1])
        return [0x4600 | ((rd >> 3) << 7) | (rm << 3) | (rd & 7)], []

    if mn in ("b", "b.n", "b.w", "bl", "blx") or (
            mn.startswith("b") and mn[1:] in CONDS):
        if len(a) != 2:
            raise EncError("%s takes two addresses: %s <from_fo> <to_fo>" % (mn, mn))
        frm, to = parse_int(a[0], "from"), parse_int(a[1], "to")
        if frm & 1 or to & 1:
            raise EncError("Thumb branch endpoints must be even; strip the "
                           "Thumb bit before encoding")
        off = to - (frm + 4)
        if off % 2:
            raise EncError("branch offset %d is odd" % off)

        if mn[1:] in CONDS:
            cond = CONDS[mn[1:]]
            bound("conditional branch offset", off, -256, 254,
                  "b<cond> reaches +/-254 bytes only.")
            return [0xD000 | (cond << 8) | ((off >> 1) & 0xFF)], []

        if mn in ("b", "b.n"):
            bound("branch offset", off, -2048, 2046,
                  "b.n reaches +/-2 KB. Beyond that you need bl (which "
                  "CLOBBERS LR) or a Thumb-2 b.w (which an ARMv4T/v5T core "
                  "does not implement).")
            return [0xE000 | ((off >> 1) & 0x7FF)], []

        if mn == "bl" or mn == "blx":
            bound("bl offset", off, -0x400000, 0x3FFFFE)
            u = off & 0x7FFFFF
            h1 = 0xF000 | ((u >> 12) & 0x7FF)
            h2 = (0xF800 if mn == "bl" else 0xE800) | ((u >> 1) & 0x7FF)
            return [h1, h2], [
                "!! bl CLOBBERS LR. Using it as a long branch is only safe "
                "where LR is dead (a tail position, or a leaf that has "
                "already popped). The compiler does exactly this, which is "
                "why a reachability sweep must follow bl targets."]

        # b.w
        bound("b.w offset", off, -0x1000000, 0xFFFFFE)
        u = off & 0x1FFFFFF
        s = (u >> 24) & 1
        i1 = (u >> 23) & 1
        i2 = (u >> 22) & 1
        j1 = ((~i1) & 1) ^ s
        j2 = ((~i2) & 1) ^ s
        h1 = 0xF000 | (s << 10) | ((u >> 12) & 0x3FF)
        h2 = 0x9000 | (j1 << 13) | (j2 << 11) | ((u >> 1) & 0x7FF)
        return [h1, h2], [
            "!! b.w is the Thumb-2 T4 encoding and requires ARMv6T2 or later. "
            "A feature-phone title is usually built for ARMv4T/v5T, where "
            "this encoding is UNDEFINED and will fault. Prefer `b` if it "
            "reaches."]

    raise EncError("unsupported instruction %r. Supported: movs/cmp/adds/subs "
                   "(imm8), adds/subs (imm3 and register), lsls/lsrs/asrs, "
                   "movs/mov (register), nop, bx, pop {pc}, b, b<cond>, b.w, "
                   "bl, blx" % expr)


BRANCH_MNEMONICS = ("b", "b.n", "b.w", "bl", "blx")


def implicit_site(expr):
    """For `b <from> <to>`, the patch site is the `from` operand.

    Without this the round-trip line reads a bare `b` with no target, which is
    the one thing you actually want to check about a branch you just encoded.
    """
    toks = [t for t in re.split(r"[\s,]+", str(expr).strip()) if t]
    if len(toks) != 3:
        return None
    mn = toks[0].lower()
    if mn not in BRANCH_MNEMONICS and not (mn.startswith("b") and mn[1:] in CONDS):
        return None
    try:
        return parse_int(toks[1], "from")
    except EncError:
        return None


# --------------------------------------------------------------------------
def decode(h, fo=None, h2=None):
    """Best-effort disassembly of one Thumb halfword (for verification)."""
    def b(off):
        return "" if fo is None else "  ; -> fo 0x%x" % (fo + 4 + off)

    if (h & 0xF800) == 0x2000:
        return "movs r%d,#0x%x (%d)" % ((h >> 8) & 7, h & 0xFF, h & 0xFF)
    if (h & 0xF800) == 0x2800:
        return "cmp r%d,#0x%x" % ((h >> 8) & 7, h & 0xFF)
    if (h & 0xF800) == 0x3000:
        return "adds r%d,#0x%x" % ((h >> 8) & 7, h & 0xFF)
    if (h & 0xF800) == 0x3800:
        return "subs r%d,#0x%x" % ((h >> 8) & 7, h & 0xFF)
    if (h & 0xFE00) == 0x1C00:
        return "adds r%d,r%d,#%d" % (h & 7, (h >> 3) & 7, (h >> 6) & 7)
    if (h & 0xFE00) == 0x1E00:
        return "subs r%d,r%d,#%d" % (h & 7, (h >> 3) & 7, (h >> 6) & 7)
    if (h & 0xFE00) == 0x1800:
        return "adds r%d,r%d,r%d" % (h & 7, (h >> 3) & 7, (h >> 6) & 7)
    if (h & 0xFE00) == 0x1A00:
        return "subs r%d,r%d,r%d" % (h & 7, (h >> 3) & 7, (h >> 6) & 7)
    if (h & 0xF800) == 0x0000:
        sh = (h >> 6) & 31
        if sh == 0:
            return "movs r%d,r%d" % (h & 7, (h >> 3) & 7)
        return "lsls r%d,r%d,#%d" % (h & 7, (h >> 3) & 7, sh)
    if (h & 0xF800) == 0x0800:
        return "lsrs r%d,r%d,#%d" % (h & 7, (h >> 3) & 7, ((h >> 6) & 31) or 32)
    if (h & 0xF800) == 0x1000:
        return "asrs r%d,r%d,#%d" % (h & 7, (h >> 3) & 7, ((h >> 6) & 31) or 32)
    if (h & 0xF800) == 0x6800:
        return "ldr r%d,[r%d,#0x%x]" % (h & 7, (h >> 3) & 7, ((h >> 6) & 31) * 4)
    if (h & 0xF800) == 0x6000:
        return "str r%d,[r%d,#0x%x]" % (h & 7, (h >> 3) & 7, ((h >> 6) & 31) * 4)
    if (h & 0xF800) == 0x7800:
        return "ldrb r%d,[r%d,#0x%x]" % (h & 7, (h >> 3) & 7, (h >> 6) & 31)
    if (h & 0xF800) == 0x7000:
        return "strb r%d,[r%d,#0x%x]" % (h & 7, (h >> 3) & 7, (h >> 6) & 31)
    if (h & 0xF800) == 0x8800:
        return "ldrh r%d,[r%d,#0x%x]" % (h & 7, (h >> 3) & 7, ((h >> 6) & 31) * 2)
    if (h & 0xF800) == 0x8000:
        return "strh r%d,[r%d,#0x%x]" % (h & 7, (h >> 3) & 7, ((h >> 6) & 31) * 2)
    if (h & 0xF800) == 0x9800:
        return "ldr r%d,[sp,#0x%x]" % ((h >> 8) & 7, (h & 0xFF) * 4)
    if (h & 0xF800) == 0x9000:
        return "str r%d,[sp,#0x%x]" % ((h >> 8) & 7, (h & 0xFF) * 4)
    if (h & 0xF800) == 0x4800:
        return "ldr r%d,[pc,#0x%x]" % ((h >> 8) & 7, (h & 0xFF) * 4)
    if (h & 0xFF87) == 0x4700:
        return "bx r%d" % ((h >> 3) & 15)
    if (h & 0xFF87) == 0x4780:
        return "blx r%d" % ((h >> 3) & 15)
    if (h & 0xFC00) == 0x4400:
        rd = ((h >> 4) & 8) | (h & 7)
        return "add r%d,r%d" % (rd, (h >> 3) & 15)
    if (h & 0xFC00) == 0x4600:
        rd = ((h >> 4) & 8) | (h & 7)
        return "mov r%d,r%d" % (rd, (h >> 3) & 15)
    if (h & 0xFF00) == 0xB500:
        return "push {..,lr}"
    if (h & 0xFF00) == 0xB400:
        return "push {..}"
    if (h & 0xFF00) == 0xBD00:
        return "pop {..,pc}"
    if (h & 0xFF00) == 0xBC00:
        return "pop {..}"
    if (h & 0xFF80) == 0xB080:
        return "sub sp,#0x%x" % ((h & 0x7F) * 4)
    if (h & 0xFF80) == 0xB000:
        return "add sp,#0x%x" % ((h & 0x7F) * 4)
    if 0xE000 <= h <= 0xE7FF:
        return "b" + b(sx(h & 0x7FF, 11) * 2)
    if 0xD000 <= h <= 0xDCFF:
        name = [k for k, v in CONDS.items() if v == ((h >> 8) & 15)]
        return "b%s" % (name[0] if name else "?") + b(sx(h & 0xFF, 8) * 2)
    if (h & 0xF800) == 0xF000 and h2 is not None:
        if (h2 & 0xF800) == 0xF800:
            off = sx(((h & 0x7FF) << 12) | ((h2 & 0x7FF) << 1), 23)
            return "bl" + b(off)
        if (h2 & 0xF800) == 0xE800:
            off = sx(((h & 0x7FF) << 12) | ((h2 & 0x7FF) << 1), 23)
            return "blx" + b(off)
    return ".hw 0x%04x (not decoded by this tool)" % h


DEST_RE = re.compile(r"^(?:movs|mov|adds|subs|lsls|lsrs|asrs|ldr|ldrb|ldrh)\s+r(\d+)")
FLAGS_RE = re.compile(r"^(movs|adds|subs|cmp|lsls|lsrs|asrs)\b")


def shape_notes(orig_text, new_text):
    notes = []
    mo, mn_ = DEST_RE.match(orig_text), DEST_RE.match(new_text)
    if mo and mn_ and mo.group(1) != mn_.group(1):
        notes.append("!! the replacement writes r%s but the original wrote r%s. "
                     "Prove the original destination is dead, or you will break "
                     "the very next instruction."
                     % (mn_.group(1), mo.group(1)))
    fo_, fn_ = bool(FLAGS_RE.match(orig_text)), bool(FLAGS_RE.match(new_text))
    if fn_ and not fo_:
        notes.append("!! the replacement writes the condition flags and the "
                     "original did not. Check that nothing between here and "
                     "the next branch depends on the old flags.")
    if fo_ and not fn_:
        notes.append("!! the original wrote the condition flags and the "
                     "replacement does not. A following conditional branch may "
                     "now read stale flags.")
    return notes


# --------------------------------------------------------------------------
def selftest():
    fails = []

    def check(name, cond):
        if not cond:
            fails.append(name)

    def enc(e):
        return encode(e)[0]

    # the canonical shapes
    check("movs r1,#25", enc("movs r1,#25") == [0x2119])
    check("movs r1,#100", enc("movs r1,#100") == [0x2164])
    check("movs r1,#50", enc("movs r1,#50") == [0x2132])
    check("adds r1,r3,#0", enc("adds r1,r3,#0") == [0x1C19])
    check("subs r1,r3,#5", enc("subs r1,r3,#5") == [0x1F59])
    check("lsls r3,r3,#3", enc("lsls r3,r3,#3") == [0x00DB])
    check("ldr-style decode", decode(0x6988) == "ldr r0,[r1,#0x18]")

    # b.n T2: the block-skip shape
    check("b reaches", enc("b 0x149f7a 0x14a324") == [0xE1D3])
    check("b backwards", enc("b 0x100 0x0f8") == [0xE7FA])

    # round-trip every legal movs and subs-imm3
    for rd in range(8):
        for v in range(256):
            h = enc("movs r%d,#%d" % (rd, v))[0]
            check("movs roundtrip", decode(h) == "movs r%d,#0x%x (%d)" % (rd, v, v))
    for rd in range(8):
        for rn in range(8):
            for v in range(8):
                h = enc("subs r%d,r%d,#%d" % (rd, rn, v))[0]
                check("subs roundtrip",
                      decode(h) == "subs r%d,r%d,#%d" % (rd, rn, v))

    # bounds are refused, not silently truncated
    def refused(e):
        try:
            encode(e)
            return False
        except EncError:
            return True

    check("imm3 bound", refused("subs r1,r3,#8"))
    check("imm8 bound", refused("movs r1,#256"))
    check("high register refused", refused("movs r9,#1"))
    check("b range", refused("b 0x1000 0x9000"))
    check("odd endpoint refused", refused("b 0x1001 0x1010"))

    # bl round-trips both directions and clamps at +/-4 MB
    for frm, to in ((0x20, 0x100), (0x100, 0x20), (0x1000, 0x1000 + 0x3FFFFE),
                    (0x400000, 0x000004)):
        h1, h2 = enc("bl 0x%x 0x%x" % (frm, to))
        off = sx(((h1 & 0x7FF) << 12) | ((h2 & 0x7FF) << 1), 23)
        check("bl roundtrip", frm + 4 + off == to)
    check("bl range", refused("bl 0x0 0x800000"))
    check("bl warns about lr", any("LR" in w for w in encode("bl 0x20 0x100")[1]))

    # b.w round-trips and warns about the architecture
    for frm, to in ((0x1000, 0x900000), (0x900000, 0x1000), (0x20, 0x24)):
        h1, h2 = enc("b.w 0x%x 0x%x" % (frm, to))
        s = (h1 >> 10) & 1
        j1 = (h2 >> 13) & 1
        j2 = (h2 >> 11) & 1
        i1 = (~(j1 ^ s)) & 1
        i2 = (~(j2 ^ s)) & 1
        off = sx((s << 24) | (i1 << 23) | (i2 << 22) |
                 ((h1 & 0x3FF) << 12) | ((h2 & 0x7FF) << 1), 25)
        check("b.w roundtrip", frm + 4 + off == to)
    check("b.w warns about armv6t2",
          any("ARMv6T2" in w for w in encode("b.w 0x20 0x24")[1]))

    # conditional branches
    check("beq", enc("beq 0x100 0x110") == [0xD006])
    check("bne range", refused("bne 0x100 0x400"))

    # the implicit patch site lets a branch round-trip visibly: encode from
    # -> to, then decode the halfword back at the same site and land on `to`
    for frm, to in ((0x149F7A, 0x14A324), (0x100, 0x0F8), (0x200, 0x202)):
        h = enc("b 0x%x 0x%x" % (frm, to))[0]
        check("branch site is inferred from the expression",
              implicit_site("b 0x%x 0x%x" % (frm, to)) == frm)
        check("branch decodes back to its target",
              decode(h, frm).endswith("fo 0x%x" % to))
    check("a non-branch has no implicit site",
          implicit_site("movs r1,#25") is None)
    check("a two-token branch has no implicit site",
          implicit_site("b 0x100") is None)

    # shape notes catch a changed destination register
    n = shape_notes(decode(0x1C19), decode(0x1F59))
    check("same dest -> no dest warning", not any("writes r" in x for x in n))
    n2 = shape_notes(decode(0x1C19), decode(enc("subs r2,r3,#5")[0]))
    check("changed dest -> warning", any("writes r2" in x for x in n2))

    if fails:
        for f in sorted(set(fails)):
            print("FAIL %s" % f)
        print("selftest: %d failure(s)" % len(set(fails)))
        return 1
    print("selftest: ok (encoding round-trips over the full legal ranges)")
    return 0


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        prog="thumb_enc.py",
        description="Encode a single Thumb instruction to the halfword(s) a "
                    "host-side guest patch should write, with the bounds "
                    "warning that tells you what the encoding lets your "
                    "feature be. Also decodes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="This tool never modifies the guest image: the host writes guest "
               "RAM after boot, and the shipped asset stays byte-identical.",
    )
    ap.add_argument("expr", nargs="?",
                    help='instruction, e.g. "subs r1,r3,#5" or "b 0x149f7a 0x14a324"')
    ap.add_argument("--verify-orig",
                    help="the halfword you believe is currently at the patch "
                         "site; it is decoded and compared in shape")
    ap.add_argument("--decode", help="comma-separated halfwords to decode, then exit")
    ap.add_argument("-t", "--target", help="path to target.toml (needed for --at)")
    ap.add_argument("--at", help="patch site FILE OFFSET; with -t, the current "
                                 "bytes are read from the image and checked")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--binary", help="override target.toml `binary`")
    ap.add_argument("--selftest", action="store_true",
                    help="run the encoding round-trip checks and exit")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()

    if a.decode:
        hws = []
        for part in a.decode.replace(" ", "").split(","):
            if part:
                try:
                    hws.append(parse_int(part, "halfword") & 0xFFFF)
                except EncError as e:
                    die(str(e))
        site0 = None
        if a.at:
            try:
                site0 = parse_int(a.at, "--at")
            except EncError as e:
                die(str(e))
        for i, h in enumerate(hws):
            nxt = hws[i + 1] if i + 1 < len(hws) else None
            # --at makes a decoded branch resolve its target instead of
            # printing a bare `b`
            print("0x%04x  %s"
                  % (h, decode(h, None if site0 is None else site0 + 2 * i, nxt)))
        return 0

    if not a.expr:
        ap.error('give an instruction, e.g. "movs r1,#25", or use --decode/--selftest')

    try:
        hws, warns = encode(a.expr)
    except EncError as e:
        die(str(e))

    site = implicit_site(a.expr)
    cur = None
    if a.at:
        try:
            site = parse_int(a.at, "--at")
        except EncError as e:
            die(str(e))
    base = 0
    if a.target:
        if load_target is None:
            die("target.py must sit next to this tool (it loads target.toml)")
        tgt = load_target(a.target)
        base = int(tget(tgt, "image_base", 0))
        if site is not None:
            data = read_image(tgt, a.target, a.binary)
            if site + 2 * len(hws) > len(data):
                die("--at 0x%x is outside the image" % site)
            cur = [int.from_bytes(data[site + 2 * i: site + 2 * i + 2], "little")
                   for i in range(len(hws))]

    orig_hw = None
    if a.verify_orig:
        try:
            orig_hw = parse_int(a.verify_orig, "--verify-orig") & 0xFFFF
        except EncError as e:
            die(str(e))

    notes = list(warns)
    rc = 0
    if cur is not None and orig_hw is not None and cur[0] != orig_hw:
        notes.append("!! MISMATCH: the image holds 0x%04x at fo 0x%x, but "
                     "--verify-orig says 0x%04x. Your note is stale, or this is "
                     "a different build. Do not patch."
                     % (cur[0], site, orig_hw))
        rc = 1
    if orig_hw is not None:
        notes.extend(shape_notes(decode(orig_hw), decode(hws[0])))
    if len(hws) == 1 and orig_hw is not None and hws[0] == orig_hw:
        notes.append("the replacement equals the original - this patch is a "
                     "no-op, which is what a correctly written idempotent "
                     "helper should report as 'already applied'.")

    payload = {
        "expr": a.expr,
        "halfwords": ["0x%04x" % h for h in hws],
        "bytes_le": " ".join("%02x" % b for h in hws
                             for b in h.to_bytes(2, "little")),
        "size": 2 * len(hws),
        "decoded_back": [decode(h, site, hws[i + 1] if i + 1 < len(hws) else None)
                         for i, h in enumerate(hws)],
        "site_fo": site,
        "site_guest": (site + base) if site is not None and base else None,
        "current_in_image": (["0x%04x" % h for h in cur] if cur else None),
        "verify_orig": ("0x%04x" % orig_hw) if orig_hw is not None else None,
        "verify_orig_decoded": decode(orig_hw) if orig_hw is not None else None,
        "notes": notes,
    }
    if a.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return rc

    print("expr      %s" % a.expr)
    print("encoding  %s   (%d bytes, little-endian: %s)"
          % (" ".join("0x%04x" % h for h in hws), 2 * len(hws), payload["bytes_le"]))
    for d in payload["decoded_back"]:
        print("decodes   %s" % d)
    if orig_hw is not None:
        print("original  0x%04x = %s" % (orig_hw, decode(orig_hw)))
    if cur is not None:
        print("in image  %s at fo 0x%x%s"
              % (" ".join("0x%04x" % h for h in cur), site,
                 "  (guest 0x%08x)" % (site + base) if base else ""))
    for n in notes:
        print("          %s" % n)
    print()
    print("read-verify-then-write: have the host read the halfword first and "
          "write only if it equals the original, so a different build is left")
    print("untouched and the helper is idempotent. Compute this encoding in "
          "your code and pin it with a test - do not paste the hex.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
