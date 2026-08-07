#!/usr/bin/env python3
"""Serializer-anchored enumeration of every request a guest binary can send.

WHAT IT DOES
    Finds every call site of the protocol serializer's flush/terminator routine
    and reconstructs, for each one, the sequence of put_u8 / put_bytes /
    put_u32le / put_u32be calls that built the frame.  The result is one
    *field template* per send site, grouped by leading constant byte.  That
    grouping is usually your protocol's category discovery.  (A)

THE METHOD IT EMBODIES  (A)
    1. Identify the serializer primitives and the terminator.  Games of this era
       build outgoing frames through a tiny append-only writer:
           put_u8(ctx, v) | put_bytes(ctx, p, n) | put_u32_le/_be(ctx, v)
       and one flush()/send() that writes the length word and resets the cursor.
       You find them from the receive/send pump, or from the one routine that
       writes a length field into the same buffer it just filled.
    2. EVERY `bl flush` site is exactly one request.  Scan the whole image for
       BL instructions whose target equals flush.
    3. Walk back from each flush to the enclosing function prologue
       (`push {..., lr}`), then disassemble forward, replaying the put_* calls
       into a template: a tracked immediate becomes a constant hex byte, an
       unknown register becomes `??`.
    4. ** BRANCH-RESET RULE.  A linear sweep unions mutually exclusive branches
       and invents templates that no message ever matches.  So the accumulated
       sequence is RESET at every branch instruction and (by default) at every
       instruction that is the target of a branch inside the window.  The tool
       therefore reports the final basic block only, flagged `[+earlier blocks]`.
       The output contains OMISSIONS, NEVER FABRICATIONS.  That asymmetry is the
       whole point: an omission costs you a follow-up disassembly, a fabrication
       costs you a week chasing a message the client never sends.
    5. SELF-VALIDATE.  Pass `--expect <site_fo>=<hex>` for packets you already
       captured on the wire.  If the extractor cannot independently reproduce a
       packet you already have, none of the other templates are credible.

WORKED EXAMPLE  (B) -- from one reference port, values are NOT defaults
    Its writer lived at four adjacent addresses with flush a few KB later; 27
    flush sites were found; grouping by leading byte gave 15 sites sharing one
    byte (the economy category), 3 another (login), 1 heartbeat, 1 recovery,
    and 6 dynamic-first-byte sites.  Cross-checking that histogram against the
    receive dispatcher's `switch(cat)` established `payload[0]=category,
    payload[1]=opcode` from both directions.  Two templates reproduced captured
    packets byte for byte, which is what made the other 25 believable.
    Do not copy those addresses.  Find your own with `xref.py` / `dis.py`.  (C)

KNOWN LIMITS
    * Register tracking is intraprocedural, constant-propagation only: MOV/MOVS
      immediates, register copies, small ADD/SUB/LSL, and `ldr rX,[pc,#imm]`
      literal loads.  Anything computed from memory or a parameter is `??`.
    * The prologue heuristic is `push {..., lr}` (and the Thumb-2 wide form).
      A leaf serializer helper that does not push LR will not be found; the site
      is still reported, with `prologue not found`.
    * The put_* argument register defaults to r1 (AAPCS arg #2, i.e. the value
      after the writer context in r0).  If your writer has a different shape,
      set `--val-reg` / `--len-reg`.
    * Thumb only for the template walk.  `--isa arm` is not implemented here;
      AOT-compiled feature-phone images of this era are overwhelmingly Thumb.
    * A BL sitting inside a literal pool decodes as a BL.  Those false positives
      are filtered by exact target match, but a pool word pair that happens to
      encode `bl flush` will show up as a site with an empty template.

REQUIRES
    capstone  ->  python -m pip install capstone
"""

from __future__ import annotations

import os
import sys

# --------------------------------------------------------------------------
# stdlib-shadowing guard. MUST run before any third-party import.
#
# Python puts a script's own directory at the head of sys.path, and this tools
# directory contains `dis.py` (the plugin's disassembler).  That shadows the
# STDLIB `dis` module, which `inspect` imports, which capstone imports -- so a
# plain `import capstone` here dies with a baffling
#     AttributeError: module 'dis' has no attribute 'COMPILER_FLAG_NAMES'
# Loading the real stdlib `dis` first pins it in sys.modules for everyone.
# --------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")
if "dis" not in sys.modules:
    _saved_path = sys.path[:]
    sys.path[:] = [p for p in sys.path
                   if os.path.abspath(p or ".").replace("\\", "/") != _HERE]
    try:
        import dis as _stdlib_dis  # noqa: F401
    except Exception:  # pragma: no cover - stdlib is always importable
        pass
    finally:
        sys.path[:] = _saved_path

import argparse  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402

sys.path.insert(0, _HERE)

try:
    from target import load_target
except ImportError:
    try:
        from target import Target as _Target
    except ImportError as exc:  # pragma: no cover - depends on install layout
        sys.exit(
            "error: cannot import target.py from the tools directory (%s).\n"
            "       extract_packets.py must live next to target.py." % exc
        )

    def load_target(path):
        return _Target.load(path)

try:
    import capstone
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN
    from capstone.arm import ARM_OP_IMM, ARM_OP_MEM, ARM_OP_REG, ARM_REG_PC
except ImportError:
    sys.exit(
        "error: capstone is required for the template walk.\n"
        "       install it with:  python -m pip install capstone"
    )


# --------------------------------------------------------------------------
# target.toml access (tolerant of whatever shape load_target() returns)
# --------------------------------------------------------------------------

_MISSING = object()


def cfg(t, key, default=_MISSING):
    """Read one key out of the loaded target, object- or dict-shaped."""
    if isinstance(t, dict):
        val = t.get(key, _MISSING)
    else:
        val = getattr(t, key, _MISSING)
        if val is _MISSING:
            for holder in ("raw", "data", "values", "table", "cfg"):
                d = getattr(t, holder, None)
                if isinstance(d, dict) and key in d:
                    val = d[key]
                    break
    if val is _MISSING or val is None:
        if default is not _MISSING:
            return default
        sys.exit(
            "error: target.toml provides no usable '%s'.\n"
            "       See the schema in target.py --help." % key
        )
    return val


def resolve_binary(t, toml_path, override=None):
    path = override or cfg(t, "binary_path", None) or cfg(t, "binary")
    path = str(path).replace("\\", "/")
    if not os.path.isabs(path):
        base = os.path.dirname(os.path.abspath(toml_path)).replace("\\", "/")
        cand = base + "/" + path
        if os.path.exists(cand):
            return cand
    if os.path.exists(path):
        return path
    sys.exit(
        "error: guest binary not found: %s\n"
        "       'binary' in target.toml is resolved relative to the .toml file."
        % path
    )


# --------------------------------------------------------------------------
# BL scanning -- no disassembler needed, and it must not need one
# --------------------------------------------------------------------------


def decode_bl(hi: int, lo: int):
    """Decode a Thumb BL/BLX halfword pair.  Returns (offset, is_blx) or None.

    The ARMv7 J1/J2 encoding is a strict superset of the ARMv4T/v5 one: with
    J1=J2=1 (which is what a Thumb-1 assembler always emits) both decodings
    produce the same displacement, so this handles both eras.
    """
    if (hi & 0xF800) != 0xF000:
        return None
    if (lo & 0xF800) == 0xF800:
        is_blx = False
    elif (lo & 0xF800) == 0xE800:
        is_blx = True
    else:
        return None
    s = (hi >> 10) & 1
    imm10 = hi & 0x3FF
    j1 = (lo >> 13) & 1
    j2 = (lo >> 11) & 1
    imm11 = lo & 0x7FF
    i1 = 1 - (j1 ^ s)
    i2 = 1 - (j2 ^ s)
    off = (s << 24) | (i1 << 23) | (i2 << 22) | (imm10 << 12) | (imm11 << 1)
    if s:
        off -= 1 << 25
    return off, is_blx


def scan_bl_sites(data: bytes):
    """file offset of the BL -> resolved target file offset, for the whole image."""
    sites = {}
    n = len(data) - 3
    for fo in range(0, n, 2):
        hi = data[fo] | (data[fo + 1] << 8)
        if (hi & 0xF800) != 0xF000:
            continue
        lo = data[fo + 2] | (data[fo + 3] << 8)
        dec = decode_bl(hi, lo)
        if dec is None:
            continue
        off, is_blx = dec
        tgt = fo + 4 + off
        if is_blx:
            tgt &= ~3
        if 0 <= tgt < len(data):
            sites[fo] = tgt
    return sites


# --------------------------------------------------------------------------
# instruction classification
# --------------------------------------------------------------------------

_COND = {
    "eq", "ne", "cs", "hs", "cc", "lo", "mi", "pl",
    "vs", "vc", "hi", "ls", "ge", "lt", "gt", "le", "al",
}


def base_mnemonic(mn: str) -> str:
    mn = mn.lower()
    for suffix in (".w", ".n"):
        if mn.endswith(suffix):
            mn = mn[: -len(suffix)]
    return mn


def is_call(mn: str) -> bool:
    return mn in ("bl", "blx")


def is_branch(ins, mn: str) -> bool:
    """True for anything that ends a basic block other than a call."""
    if mn in ("b", "bx", "cbz", "cbnz", "tbb", "tbh"):
        return True
    if mn.startswith("b") and len(mn) == 3 and mn[1:] in _COND:
        return True
    if mn.startswith("it"):
        return True  # the next 1-4 instructions become conditional
    if mn in ("pop", "ldm", "ldmia", "ldmfd") and "pc" in ins.op_str.lower():
        return True
    if mn.startswith("mov") and re.match(r"^\s*pc\b", ins.op_str.lower()):
        return True
    return False


def branch_target(ins, mn: str):
    """Immediate branch target address, or None."""
    if not (mn in ("b", "cbz", "cbnz") or (mn.startswith("b") and len(mn) == 3 and mn[1:] in _COND)):
        return None
    for op in ins.operands:
        if op.type == ARM_OP_IMM:
            return op.imm
    return None


# --------------------------------------------------------------------------
# tiny constant-propagation engine
# --------------------------------------------------------------------------

_CALLER_SAVED = ("r0", "r1", "r2", "r3", "r12", "ip", "lr")


class Regs(dict):
    def clobber(self, names):
        for n in names:
            self.pop(n, None)


def written_regs(ins, cs_handle):
    try:
        _read, write = ins.regs_access()
        return [ins.reg_name(r).lower() for r in write]
    except Exception:
        # Conservative fallback: assume the first register operand is written
        # unless the mnemonic is obviously non-writing.
        mn = base_mnemonic(ins.mnemonic)
        if mn in ("cmp", "cmn", "tst", "teq", "str", "strb", "strh", "push", "b", "bl", "blx", "bx"):
            return []
        ops = ins.operands
        if ops and ops[0].type == ARM_OP_REG:
            return [ins.reg_name(ops[0].reg).lower()]
        return []


def step_regs(regs: Regs, ins, cs_handle, read_word):
    """Apply one instruction to the tracked register state."""
    mn = base_mnemonic(ins.mnemonic)
    ops = ins.operands

    if is_call(mn):
        regs.clobber(_CALLER_SAVED)
        return

    dst = None
    val = None

    def rname(op):
        return ins.reg_name(op.reg).lower()

    def known(op):
        return regs.get(rname(op))

    if mn in ("mov", "movs", "movw") and len(ops) == 2 and ops[0].type == ARM_OP_REG:
        if ops[1].type == ARM_OP_IMM:
            dst, val = rname(ops[0]), ops[1].imm & 0xFFFFFFFF
        elif ops[1].type == ARM_OP_REG:
            src = known(ops[1])
            if src is not None:
                dst, val = rname(ops[0]), src
    elif mn in ("add", "adds", "sub", "subs") and ops and ops[0].type == ARM_OP_REG:
        sign = -1 if mn.startswith("sub") else 1
        if len(ops) == 3 and ops[1].type == ARM_OP_REG and ops[2].type == ARM_OP_IMM:
            base = known(ops[1])
            if base is not None:
                dst, val = rname(ops[0]), (base + sign * ops[2].imm) & 0xFFFFFFFF
        elif len(ops) == 2 and ops[1].type == ARM_OP_IMM:
            base = known(ops[0])
            if base is not None:
                dst, val = rname(ops[0]), (base + sign * ops[1].imm) & 0xFFFFFFFF
    elif mn in ("lsl", "lsls") and len(ops) == 3 and ops[2].type == ARM_OP_IMM:
        base = known(ops[1])
        if base is not None:
            dst, val = rname(ops[0]), (base << ops[2].imm) & 0xFFFFFFFF
    elif mn == "ldr" and len(ops) == 2 and ops[1].type == ARM_OP_MEM:
        mem = ops[1].mem
        if mem.base == ARM_REG_PC and mem.index == 0:
            lit_addr = ((ins.address + 4) & ~3) + mem.disp
            word = read_word(lit_addr)
            if word is not None:
                dst, val = rname(ops[0]), word

    regs.clobber(written_regs(ins, cs_handle))
    if dst is not None and val is not None:
        regs[dst] = val


# --------------------------------------------------------------------------
# template model
# --------------------------------------------------------------------------


class Field:
    __slots__ = ("kind", "value", "count")

    def __init__(self, kind, value=None, count=None):
        self.kind = kind          # 'u8' | 'u32le' | 'u32be' | 'bytes'
        self.value = value        # int or None
        self.count = count        # for 'bytes': int or None

    def pattern(self):
        """Byte-level pattern: list of int|None, or None if the length is unknown."""
        if self.kind == "u8":
            return [self.value if self.value is None else self.value & 0xFF]
        if self.kind == "u32le":
            if self.value is None:
                return [None] * 4
            v = self.value & 0xFFFFFFFF
            return [v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF, (v >> 24) & 0xFF]
        if self.kind == "u32be":
            if self.value is None:
                return [None] * 4
            v = self.value & 0xFFFFFFFF
            return [(v >> 24) & 0xFF, (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF]
        if self.kind == "bytes":
            if self.count is None:
                return None
            return [None] * self.count
        return None

    def render(self):
        if self.kind == "u8":
            return "??" if self.value is None else "%02x" % (self.value & 0xFF)
        if self.kind in ("u32le", "u32be"):
            pat = self.pattern()
            return " ".join("??" if b is None else "%02x" % b for b in pat)
        if self.kind == "bytes":
            return "[%sB]" % ("?" if self.count is None else self.count)
        return "?"


class Template:
    def __init__(self, site_fo, func_fo):
        self.site_fo = site_fo
        self.func_fo = func_fo
        self.fields = []
        self.earlier_blocks = False
        self.note = ""

    def pattern(self):
        out = []
        for f in self.fields:
            p = f.pattern()
            if p is None:
                return None
            out.extend(p)
        return out

    def render(self):
        if not self.fields:
            return "(empty)"
        return " ".join(f.render() for f in self.fields)

    def lead(self):
        pat = self.pattern()
        if pat and pat[0] is not None:
            return pat[0]
        if self.fields:
            first = self.fields[0].pattern()
            if first and first[0] is not None:
                return first[0]
        return None


def match_pattern(pattern, payload: bytes) -> bool:
    if pattern is None:
        return False
    if len(pattern) != len(payload):
        return False
    for want, got in zip(pattern, payload):
        if want is not None and want != got:
            return False
    return True


# --------------------------------------------------------------------------
# the walk
# --------------------------------------------------------------------------


def find_prologue_candidates(data: bytes, site_fo: int, max_back: int, limit: int = 8):
    """Nearest-first `push {..., lr}` file offsets preceding a call site."""
    out = []
    lo = max(0, site_fo - max_back)
    fo = site_fo & ~1
    while fo >= lo and len(out) < limit:
        hw = data[fo] | (data[fo + 1] << 8)
        if (hw & 0xFF00) == 0xB500:          # PUSH {reglist, LR}
            out.append(fo)
        elif hw == 0xE92D and fo + 3 < len(data):
            nxt = data[fo + 2] | (data[fo + 3] << 8)
            if nxt & 0x4000:                  # PUSH.W with LR
                out.append(fo)
        fo -= 2
    return out


def walk(md, data, image_base, func_fo, site_fo, prims, val_reg, len_reg, strict_blocks):
    """Disassemble func_fo..site_fo and build the template.  None if we never
    land exactly on the flush site (wrong prologue guess)."""
    start_addr = func_fo + image_base
    end_fo = site_fo + 4
    blob = data[func_fo:end_fo]

    def read_word(addr):
        fo = addr - image_base
        if 0 <= fo <= len(data) - 4:
            return int.from_bytes(data[fo:fo + 4], "little")
        return None

    insns = list(md.disasm(blob, start_addr))
    if not insns:
        return None
    if not any(i.address - image_base == site_fo for i in insns):
        return None

    labels = set()
    if strict_blocks:
        for ins in insns:
            mn = base_mnemonic(ins.mnemonic)
            tgt = branch_target(ins, mn)
            if tgt is not None:
                labels.add(tgt)

    tmpl = Template(site_fo, func_fo)
    regs = Regs()
    for ins in insns:
        fo = ins.address - image_base
        if fo == site_fo:
            break
        mn = base_mnemonic(ins.mnemonic)

        if strict_blocks and ins.address in labels and tmpl.fields:
            tmpl.fields = []
            tmpl.earlier_blocks = True
            regs = Regs()

        if is_call(mn):
            tgt = None
            for op in ins.operands:
                if op.type == ARM_OP_IMM:
                    tgt = op.imm - image_base
                    break
            kind = prims.get(tgt)
            if kind == "u8":
                tmpl.fields.append(Field("u8", regs.get(val_reg)))
            elif kind == "u32le":
                tmpl.fields.append(Field("u32le", regs.get(val_reg)))
            elif kind == "u32be":
                tmpl.fields.append(Field("u32be", regs.get(val_reg)))
            elif kind == "bytes":
                tmpl.fields.append(Field("bytes", count=regs.get(len_reg)))
            step_regs(regs, ins, md, read_word)
            continue

        if is_branch(ins, mn):
            if tmpl.fields:
                tmpl.fields = []
                tmpl.earlier_blocks = True
            regs = Regs()
            continue

        step_regs(regs, ins, md, read_word)

    return tmpl


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def parse_addr(text: str) -> int:
    text = text.strip()
    return int(text, 16) if text.lower().startswith("0x") else int(text, 0)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="extract_packets.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Enumerate every request a guest can send, by anchoring on the "
                    "send-side serializer's flush routine.",
        epilog=(
            "Addresses are FILE OFFSETS by default (guest address = file offset + "
            "image_base).  Pass --guest-addrs if you prefer to type guest addresses.\n\n"
            "The output contains omissions, never fabrications: the template is reset "
            "at every branch, so a request whose bytes are written across a conditional "
            "shows only its final basic block, flagged [+earlier blocks].  Follow those "
            "up with dis.py; do not assume the missing bytes away."
        ),
    )
    ap.add_argument("-t", "--target", required=True, help="path to target.toml")
    ap.add_argument("--binary", help="override target.toml's 'binary'")
    ap.add_argument("--flush", required=True,
                    help="address of the flush/terminator routine (one call = one request)")
    ap.add_argument("--put-u8", help="address of the 1-byte append primitive")
    ap.add_argument("--put-bytes", help="address of the block append primitive")
    ap.add_argument("--put-u32le", help="address of the little-endian u32 append primitive")
    ap.add_argument("--put-u32be", help="address of the big-endian u32 append primitive")
    ap.add_argument("--guest-addrs", action="store_true",
                    help="interpret every address argument as a guest address, not a file offset")
    ap.add_argument("--val-reg", default="r1",
                    help="register holding the value argument of put_u8/put_u32* (default r1)")
    ap.add_argument("--len-reg", default="r2",
                    help="register holding the length argument of put_bytes (default r2)")
    ap.add_argument("--max-back", default="0x600",
                    help="how far to search backwards for a prologue (default 0x600 bytes)")
    ap.add_argument("--loose-blocks", action="store_true",
                    help="reset only at branch INSTRUCTIONS, not at branch TARGETS. "
                         "Produces longer templates and a small fabrication risk.")
    ap.add_argument("--expect", action="append", default=[], metavar="SITE=HEX",
                    help="self-validation: assert the template at file offset SITE reproduces "
                         "the captured payload HEX. Repeatable. Exits non-zero on mismatch.")
    ap.add_argument("--out", help="write the catalog to this file as well as stdout")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args(argv)

    tgt = load_target(args.target)
    binpath = resolve_binary(tgt, args.target, args.binary)
    image_base = int(cfg(tgt, "image_base"))
    isa = str(cfg(tgt, "isa", "thumb")).lower()
    if isa != "thumb":
        sys.exit("error: extract_packets.py implements the Thumb template walk only "
                 "(target.toml says isa = %r)." % isa)

    with open(binpath, "rb") as fh:
        data = fh.read()

    def norm(addr_text):
        if addr_text is None:
            return None
        a = parse_addr(addr_text)
        if args.guest_addrs:
            a -= image_base
        if not (0 <= a < len(data)):
            sys.exit("error: address 0x%x is outside %s (%d bytes). "
                     "Did you mean --guest-addrs?" % (a, binpath, len(data)))
        return a

    flush_fo = norm(args.flush)
    prims = {}
    for flag, kind in (("put_u8", "u8"), ("put_bytes", "bytes"),
                       ("put_u32le", "u32le"), ("put_u32be", "u32be")):
        val = getattr(args, flag)
        if val is not None:
            prims[norm(val)] = kind
    if not prims:
        sys.exit("error: give at least one of --put-u8 / --put-bytes / --put-u32le / --put-u32be.\n"
                 "       Without a serializer primitive every template would be empty.")
    if flush_fo in prims:
        sys.exit("error: --flush and a put_* primitive are the same address.")

    max_back = parse_addr(args.max_back)
    strict = not args.loose_blocks

    bl = scan_bl_sites(data)
    sites = sorted(fo for fo, tgt_fo in bl.items() if tgt_fo == flush_fo)
    if not sites:
        sys.exit(
            "error: no BL targets 0x%x.\n"
            "       Either the address is not the flush routine, or it is reached "
            "indirectly (a function-pointer table).  Confirm with xref.py calls 0x%x, "
            "and check whether the address needs the Thumb bit cleared."
            % (flush_fo, flush_fo)
        )

    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_LITTLE_ENDIAN)
    md.detail = True
    md.skipdata = True

    templates = []
    for site in sites:
        cands = find_prologue_candidates(data, site, max_back)
        tmpl = None
        for func_fo in cands:
            tmpl = walk(md, data, image_base, func_fo, site, prims,
                        args.val_reg.lower(), args.len_reg.lower(), strict)
            if tmpl is not None:
                break
        if tmpl is None:
            tmpl = Template(site, None)
            tmpl.note = ("prologue not found within 0x%x bytes; no template. "
                         "Disassemble by hand: dis.py 0x%x 0x%x"
                         % (max_back, max(0, site - 0x80), site + 8))
        templates.append(tmpl)

    # ---- grouping -------------------------------------------------------
    groups = {}
    for tmpl in templates:
        groups.setdefault(tmpl.lead(), []).append(tmpl)

    # ---- self validation ------------------------------------------------
    expectations = []
    for spec in args.expect:
        if "=" not in spec:
            sys.exit("error: --expect wants SITE=HEX, e.g. --expect 0x56a34=010102")
        site_text, hex_text = spec.split("=", 1)
        site = parse_addr(site_text)
        if args.guest_addrs:
            site -= image_base
        payload = bytes.fromhex(re.sub(r"[\s_]", "", hex_text))
        expectations.append((site, payload))

    validation = []
    for site, payload in expectations:
        hit = None
        for tmpl in templates:
            if tmpl.site_fo == site or tmpl.func_fo == site:
                hit = tmpl
                break
        if hit is None:
            validation.append((site, payload, False, "no send site at that address"))
            continue
        ok = match_pattern(hit.pattern(), payload)
        why = "" if ok else "template %s does not match %s" % (
            hit.render(), payload.hex(" "))
        validation.append((site, payload, ok, why))

    # ---- output ---------------------------------------------------------
    if args.json:
        doc = {
            "binary": binpath,
            "image_base": image_base,
            "flush_fo": flush_fo,
            "primitives": {("0x%x" % k): v for k, v in sorted(prims.items())},
            "strict_blocks": strict,
            "site_count": len(templates),
            "sites": [
                {
                    "site_fo": "0x%x" % t.site_fo,
                    "site_guest": "0x%x" % (t.site_fo + image_base),
                    "func_fo": None if t.func_fo is None else "0x%x" % t.func_fo,
                    "template": t.render(),
                    "lead_byte": None if t.lead() is None else "0x%02x" % t.lead(),
                    "earlier_blocks": t.earlier_blocks,
                    "fields": [
                        {"kind": f.kind,
                         "value": None if f.value is None else "0x%x" % f.value,
                         "count": f.count}
                        for f in t.fields
                    ],
                    "note": t.note,
                }
                for t in templates
            ],
            "validation": [
                {"site": "0x%x" % s, "payload": p.hex(), "ok": ok, "why": why}
                for s, p, ok, why in validation
            ],
        }
        text = json.dumps(doc, indent=2)
    else:
        lines = []
        lines.append("# request catalog -- serializer-anchored")
        lines.append("# binary      : %s" % binpath)
        lines.append("# image_base  : 0x%x   (guest = file offset + image_base)" % image_base)
        lines.append("# flush       : fo 0x%x  guest 0x%x" % (flush_fo, flush_fo + image_base))
        for addr, kind in sorted(prims.items()):
            lines.append("# %-11s: fo 0x%x" % (kind, addr))
        lines.append("# block policy: %s" % ("strict (reset at branch targets too)"
                                             if strict else "loose (reset at branch instructions only)"))
        lines.append("# send sites  : %d" % len(templates))
        lines.append("#")
        lines.append("# '??' = a register the constant-propagator could not resolve.")
        lines.append("# '[nB]' = a block append of n bytes.  '[?B]' = unknown length.")
        lines.append("# '[+earlier blocks]' = fields were dropped at a branch; the real frame")
        lines.append("#   is LONGER than shown.  Omission, not fabrication.")
        lines.append("")

        for lead in sorted(groups, key=lambda k: (k is None, k)):
            label = "dynamic first byte" if lead is None else "leading byte 0x%02x" % lead
            bucket = groups[lead]
            lines.append("=" * 72)
            lines.append("%s  (%d site%s)" % (label, len(bucket), "" if len(bucket) == 1 else "s"))
            lines.append("=" * 72)
            for t in sorted(bucket, key=lambda x: x.site_fo):
                fn = "?" if t.func_fo is None else "0x%x" % t.func_fo
                lines.append("  site fo 0x%-8x  guest 0x%-8x  fn %s"
                             % (t.site_fo, t.site_fo + image_base, fn))
                lines.append("      %s%s" % (t.render(),
                                             "   [+earlier blocks]" if t.earlier_blocks else ""))
                if t.note:
                    lines.append("      ! %s" % t.note)
            lines.append("")

        if validation:
            lines.append("=" * 72)
            lines.append("self-validation against captured packets")
            lines.append("=" * 72)
            for site, payload, ok, why in validation:
                lines.append("  %s  0x%-8x  %s%s" % ("PASS" if ok else "FAIL", site,
                                                     payload.hex(" "),
                                                     "" if ok else "   <- " + why))
            lines.append("")
            if all(ok for _, _, ok, _ in validation):
                lines.append("  All captured packets reproduced.  The remaining templates are credible.")
            else:
                lines.append("  A captured packet was NOT reproduced.  Do not trust the other")
                lines.append("  templates until this is explained: wrong primitive addresses, wrong")
                lines.append("  --val-reg, or the frame is built somewhere other than this function.")
        text = "\n".join(lines)

    print(text)
    if args.out:
        out = args.out.replace("\\", "/")
        d = os.path.dirname(out)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print("\nwrote %s" % out, file=sys.stderr)

    if validation and not all(ok for _, _, ok, _ in validation):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
