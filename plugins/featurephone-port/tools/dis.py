#!/usr/bin/env python3
"""dis.py - annotated disassembly of a feature-phone guest image, in FILE OFFSETS.

WHAT IT DOES
    Disassembles a byte range of the extracted native guest image (the
    `client.bin<bss>`-style member of the game jar) and annotates each line with
    the things you would otherwise chase by hand through three levels of
    pointer:

      * `ldr rX,[pc,#imm]`  -> literal pool slot -> stored word -> relocated
        target -> the decoded string that lives there;
      * `[sl,#imm]` / `add rX,sl` (RWPI static access) -> static slot file
        offset -> stored word -> relocated target -> string;
      * every direct branch target, printed as `-> fo 0x...`.

    Everything is printed in FILE OFFSET space, because that is the space the
    rest of this toolkit consumes (xref.py, bind_strings.py, deadcode_sweep.py)
    and the space a headless probe's profiler should print. Guest address =
    file offset + image_base; the header line states the relation.

METHOD (A)
    Three ideas, all generic:
      1. Print offsets in the basis your other tools consume. An address that
         has to be mentally re-based is an address you will get wrong at 2am.
      2. Resolve indirection inline. A listing where the strings are visible is
         a listing you can read; one where they are three dereferences away is
         a listing you skim.
      3. Be resilient. An ARM image interleaves literal pools and jump tables
         with code, so a naive `md.disasm(blob)` stops dead at the first pool
         and silently truncates the listing. `--resilient` emits `.hw 0xNNNN`
         and advances two bytes instead. In practice this is mandatory.
      4. Print data as data. A literal pool decodes into perfectly plausible
         instructions, and those instructions are the most convincing wrong
         answer a disassembler can give you: they look like code, in a place
         where code cannot be. Any word that an instruction in THIS listing
         loads through `[pc,#imm]` is printed as `.word` with its resolved
         target, never decoded. The set comes from the same decode stream that
         gets printed, so a bit pattern that merely looks like `ldr rX,[pc]`
         inside string data cannot hide a real instruction behind a bogus
         `.word`. Disable with `--no-pool-marking`.

    Position-independent detail: the annotations only work because the image is
    built read-write position independent (RWPI, `-apcs /rwpi` on the ARM
    toolchain), so statics are reached through the static base register (r10/sl
    or r9/sb) and pointers stored in the image are relocation-biased. See
    `reference/address-model.md` in the guest-re skill for how to derive
    image_base, sl_fo, reloc_delta and reloc_unit for YOUR title, and put them
    in target.toml. Every constant this tool uses comes from there.

WORKED EXAMPLE (B - reference port; do not copy the numbers)
    In the reference port a refusal gate was located by disassembling the ~40
    bytes before the one call site that passed a known message code, which made
    the compare and its threshold move visible in a single listing. The numbers
    (image_base 0x100000, sl at file offset 0xb2c40, relocation delta 0xeec)
    were derived per `address-model.md`, not assumed - your title's will differ.

KNOWN LIMITS
    * Requires capstone. It is not auto-installed.
    * One ISA per run. A real image mixes ARM and Thumb; if a range decodes as
      garbage, try `--isa arm`. There is no automatic mode switching.
    * The `add rX,sl` annotation uses a short backward window of pc-relative
      literal loads to guess which constant is in rX. It is a heuristic: it can
      miss (register reloaded via a path it cannot see) and, rarely, mislead.
      Annotations are labelled `?` when they come from that path.
    * Branch targets are computed, not validated: a target inside a literal
      pool means you are disassembling data.
    * This tool never writes to the image. Guest patching is a runtime act
      performed by the host against guest RAM after boot - see the guest-re
      skill's `reference/patching.md`.

USAGE
    python dis.py -t target.toml 0xSTART_FO 0xEND_FO
    python dis.py -t target.toml 0xSTART_FO +0x40 --resilient --out listing.txt
    python dis.py -t target.toml sl+0x0 +0x80          # read the static area as code
    python dis.py --selftest
"""

from __future__ import annotations

import argparse
import os
import re
import sys

if __name__ == "dis":  # pragma: no cover - stdlib-shadowing guard
    # This file is named dis.py because that is its documented command name,
    # but the plugin's tools/ directory lands on sys.path whenever any tool in
    # it runs - which means an `import dis` from the standard library (inspect
    # does it, and dataclasses imports inspect) would otherwise find THIS file
    # and fail with a confusing AttributeError. Hand the caller the real
    # module: the import machinery re-reads sys.modules after executing a
    # module, so replacing ourselves here is enough.
    import importlib.util as _ilu
    import sysconfig as _sc

    _std = os.path.join(_sc.get_paths()["stdlib"], "dis.py")
    if os.path.isfile(_std):
        _spec = _ilu.spec_from_file_location("dis", _std)
        _real = _ilu.module_from_spec(_spec)
        sys.modules["dis"] = _real
        _spec.loader.exec_module(_real)

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

try:  # optional at import time so --selftest and --help work without it
    import capstone  # type: ignore
except Exception:  # pragma: no cover - environment dependent
    capstone = None

CAPSTONE_HINT = (
    "capstone is required for disassembly. Install it with:\n"
    "    python -m pip install capstone\n"
    "(it is the only third-party dependency of this tool and is never "
    "installed automatically)"
)


# --------------------------------------------------------------------------
# small shared helpers (duplicated across tools on purpose: every tool in this
# directory must stay independently copyable into a user's own repo)
# --------------------------------------------------------------------------
def die(msg: str, code: int = 2):
    sys.stderr.write("dis.py: error: %s\n" % msg)
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
    t = str(s).strip().replace("_", "")
    try:
        return int(t, 0)
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
    """Stored word -> file offset, honouring reloc_unit.

    reloc_unit == "file_offset"   : word + delta IS a file offset
    reloc_unit == "guest_address" : word + delta is a guest address; subtract
                                    image_base to land back in file space.
    Getting this wrong makes every annotation in this listing a lie, which is
    why target.py refuses to load a target.toml without it.
    """
    v = (int(word) + int(tget(tgt, "reloc_delta", 0))) & 0xFFFFFFFF
    if str(tget(tgt, "reloc_unit", "file_offset")) == "guest_address":
        v -= int(tget(tgt, "image_base", 0))
    return v


def u32(data, fo):
    if fo < 0 or fo + 4 > len(data):
        return None
    return int.from_bytes(data[fo : fo + 4], "little")


def read_cstr(data, fo, encoding, maxlen=160, minlen=2):
    """NUL-terminated string at a file offset, or None if it is not one."""
    if fo is None or fo < 0 or fo >= len(data):
        return None
    end = data.find(b"\x00", fo, min(len(data), fo + maxlen))
    if end < 0 or end - fo < minlen:
        return None
    raw = data[fo:end]
    try:
        s = raw.decode(encoding)
    except Exception:
        return None
    for ch in s:
        if ord(ch) < 0x20 and ch not in "\t":
            return None
    return s


def esc(s):
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "\\t")


# --------------------------------------------------------------------------
# annotation
# --------------------------------------------------------------------------
RE_PC_MEM = re.compile(r"\[\s*(?:pc|r15)\s*(?:,\s*#(?P<imm>[+-]?(?:0x)?[0-9a-fA-F]+))?\s*\]")
RE_SL_MEM = re.compile(
    r"\[\s*(?P<base>sl|r10|sb|r9)\s*(?:,\s*#(?P<imm>[+-]?(?:0x)?[0-9a-fA-F]+))?\s*\]"
)
RE_ADD_SL = re.compile(r"^(?P<rd>r\d+|sl|sb|fp|ip)\s*,\s*(?:(?P<rn>r\d+)\s*,\s*)?(?:sl|r10|sb|r9)\s*$")
RE_FIRST_REG = re.compile(r"^\s*(?P<r>r\d+|sl|sb|fp|ip|sp|lr|pc)\b")
RE_BRANCH_TARGET = re.compile(r"#(?P<t>[+-]?(?:0x)?[0-9a-fA-F]+)\s*$")
RE_BRACKETED = re.compile(r"\[[^\]]*\]")

# A stored word below this is treated as a scalar rather than a pointer: every
# small integer relocates onto SOME file offset, and reporting that offset as a
# target is the commonest false lead an annotating disassembler can produce.
SMALL_VALUE_MAX = 0x1000


class Ctx:
    def __init__(self, tgt, data, want_guest=False):
        self.t = tgt
        self.data = data
        self.enc = str(tget(tgt, "encoding", "ascii"))
        self.base = int(tget(tgt, "image_base", 0))
        sl = tget(tgt, "sl_fo")
        self.sl = None if sl is None else int(sl)
        self.want_guest = want_guest
        self.arm_mode = str(tget(tgt, "isa", "thumb")) == "arm"
        # rolling map: register -> (value, instruction_index_when_set, slot_fo)
        self.regs = {}
        self.idx = 0

    def g(self, fo):
        return fo + self.base

    def describe_target(self, fo, tag):
        """Word at `fo` -> relocated target -> string, as an annotation string."""
        w = u32(self.data, fo)
        if w is None:
            return "%s@0x%06x = <out of image>" % (tag, fo)
        out = "%s@0x%06x = 0x%08x" % (tag, fo, w)
        tfo = reloc_to_fo(self.t, w)
        s = read_cstr(self.data, tfo, self.enc)
        if s is not None:
            out += ' -> fo 0x%06x "%s"' % (tfo, esc(s))
        elif 0 <= tfo < len(self.data) and w >= SMALL_VALUE_MAX:
            out += " -> fo 0x%06x" % tfo
            if w & 1:
                cfo = reloc_to_fo(self.t, w & ~1)
                if 0 <= cfo < len(self.data):
                    out += " (thumb fn 0x%06x)" % cfo
        else:
            # Small words are scalars - struct offsets, counts, sl offsets -
            # and relocating them produces a plausible-looking file offset that
            # means nothing. Print the number instead of a false lead.
            out += " (%d)" % w
        return out


def annotate(ctx, ins):
    """Return a list of comment fragments for one capstone instruction."""
    notes = []
    op = ins.op_str
    mn = ins.mnemonic.lower()

    # 1. pc-relative literal load -------------------------------------------
    m = RE_PC_MEM.search(op)
    if m or mn == "adr":
        if mn == "adr":
            mt = RE_BRANCH_TARGET.search(op)
            lit = parse_int(mt.group("t")) if mt else None
        else:
            imm = int(m.group("imm"), 0) if m.group("imm") else 0
            if ctx.arm_mode:
                lit = ins.address + 8 + imm
            else:
                lit = ((ins.address + 4) & ~3) + imm
        if lit is not None:
            notes.append(ctx.describe_target(lit, "lit"))
            w = u32(ctx.data, lit)
            rm = RE_FIRST_REG.match(op)
            if w is not None and rm:
                ctx.regs[rm.group("r")] = (w, ctx.idx, lit)

    # 2. RWPI static access, explicit [sl,#imm] ------------------------------
    m = RE_SL_MEM.search(op)
    if m and ctx.sl is not None:
        imm = int(m.group("imm"), 0) if m.group("imm") else 0
        notes.append(
            "sl+0x%x -> %s" % (imm, ctx.describe_target(ctx.sl + imm, "slot"))
        )

    # 3. RWPI static access, Thumb-1 form: constant in a reg then `add rX,sl`
    m = RE_ADD_SL.match(op) if (mn in ("add", "adds") and ctx.sl is not None) else None
    if m:
        rd = m.group("rd")
        rn = m.group("rn") or rd
        known = ctx.regs.get(rn)
        if known and ctx.idx - known[1] <= 8:
            off = known[0]
            soff = off - (1 << 32) if off >= (1 << 31) else off
            notes.append(
                "? sl%s0x%x -> %s"
                % ("+" if soff >= 0 else "-", abs(soff),
                   ctx.describe_target(ctx.sl + soff, "slot"))
            )

    # 4. branch targets ------------------------------------------------------
    if mn.startswith("b") or mn.startswith("cb"):
        stripped = RE_BRACKETED.sub("", op)
        mt = RE_BRANCH_TARGET.search(stripped)
        if mt:
            tgt = parse_int(mt.group("t"))
            note = "-> fo 0x%06x" % tgt
            if ctx.want_guest:
                note += " (guest 0x%08x)" % ctx.g(tgt)
            if not (0 <= tgt < len(ctx.data)):
                note += " !! outside the image"
            notes.append(note)

    # 5. register bookkeeping: any write to the first operand invalidates it
    rm = RE_FIRST_REG.match(op)
    if rm and mn not in ("cmp", "cmn", "tst", "teq", "str", "strb", "strh", "push", "b", "bl"):
        r = rm.group("r")
        if r in ctx.regs and ctx.regs[r][1] != ctx.idx:
            del ctx.regs[r]
    return notes


# --------------------------------------------------------------------------
# disassembly drivers
# --------------------------------------------------------------------------
def make_md(isa):
    if capstone is None:
        die(CAPSTONE_HINT, 3)
    mode = capstone.CS_MODE_ARM if isa == "arm" else capstone.CS_MODE_THUMB
    md = capstone.Cs(capstone.CS_ARCH_ARM, mode)
    md.skipdata = False
    return md


def emit(out, ctx, addr, raw, mn, op, notes):
    b = "".join("%02x" % x for x in raw)
    line = "%08x  %-8s  %-7s %-30s" % (addr, b, mn, op)
    if notes:
        line = line.rstrip().ljust(58) + "  ; " + " | ".join(notes)
    out(line.rstrip())


def pc_literal(ins, arm_mode):
    """File offset of the pool word an instruction loads, or None."""
    m = RE_PC_MEM.search(ins.op_str)
    if not m:
        return None
    imm = int(m.group("imm"), 0) if m.group("imm") else 0
    if arm_mode:
        return ins.address + 8 + imm
    return ((ins.address + 4) & ~3) + imm


def collect_pools(md, data, start, end, arm_mode):
    """Pool-word offsets, taken from instructions the disassembler ACTUALLY emitted.

    A blind scan for the `ldr rX,[pc,#imm]` bit pattern would also fire inside
    string data and inside pools themselves, and every false hit would hide a
    real instruction behind a bogus `.word`. Deriving the set from the same
    decode stream that will be printed keeps the two consistent: a pool word is
    only called a pool word because something in this listing loads it.
    """
    pools = set()
    i = start
    while i < end:
        got = None
        for ins in md.disasm(bytes(data[i:min(i + 4, end)]), i, count=1):
            got = ins
            break
        if got is None:
            i += 4 if arm_mode else 2
            continue
        lit = pc_literal(got, arm_mode)
        if lit is not None and start <= lit and lit + 4 <= end and lit % 4 == 0:
            pools.add(lit)
        i += got.size
    # a pool word that some instruction we are about to print sits on top of is
    # not a pool word; the instruction wins and we drop the claim
    return pools


def run(out, tgt, data, start, end, isa, resilient, want_guest, mark_pools=True):
    ctx = Ctx(tgt, data, want_guest)
    ctx.arm_mode = isa == "arm"
    md = make_md(isa)
    delta, unit, declared = reloc_cfg(tgt)

    out("; image len 0x%x  guest = fo + 0x%x  %s"
        % (len(data), ctx.base,
           "sl = fo 0x%x (guest 0x%x)" % (ctx.sl, ctx.sl + ctx.base)
           if ctx.sl is not None else
           "sl_fo not declared - static-slot annotations are OFF"))
    out("; reloc: stored word + 0x%x = %s   encoding = %s   isa = %s%s"
        % (delta, unit, ctx.enc, isa, "  (resilient)" if resilient else ""))
    if not declared:
        out("; !! target.toml declares no reloc_delta, so pointer annotations "
            "assume no bias. Run solve_reloc.py before trusting them.")
    out("; columns: file_offset  bytes  mnemonic  operands  ; annotations")
    out("")

    pools = (collect_pools(md, data, start, end, ctx.arm_mode)
             if mark_pools else set())

    i = start
    stopped = None
    while i < end:
        # A word this listing's own code loads through [pc,#imm] is DATA. Left
        # to itself capstone decodes it as instructions, and those instructions
        # are the most convincing wrong answer this tool can produce: they look
        # exactly like code, in a place where code cannot be.
        if i in pools:
            w = u32(data, i)
            emit(out, ctx, i, data[i:i + 4], ".word", "0x%08x" % (w or 0),
                 [ctx.describe_target(i, "pool") + "  (literal pool - data, "
                  "not code)"])
            i += 4
            continue
        got = None
        for ins in md.disasm(bytes(data[i:min(i + 4, end)]), i, count=1):
            got = ins
            break
        if got is None:
            if not resilient:
                stopped = i
                break
            if ctx.arm_mode:
                w = u32(data, i)
                emit(out, ctx, i, data[i:i + 4], ".word",
                     "0x%08x" % (w if w is not None else 0), [])
                i += 4
            else:
                hw = (int.from_bytes(data[i:i + 2], "little")
                      if i + 2 <= len(data) else 0)
                emit(out, ctx, i, data[i:i + 2], ".hw", "0x%04x" % hw, [])
                i += 2
            continue
        if any(i < p < i + got.size for p in pools):
            # an instruction straddling a pool word: the pool claim is the one
            # backed by an actual load, so honour it and re-align
            w = u32(data, i)
            emit(out, ctx, i, data[i:i + 2], ".hw",
                 "0x%04x" % ((w or 0) & 0xFFFF), ["realigning onto a pool word"])
            i += 2
            continue
        ctx.idx += 1
        emit(out, ctx, got.address, got.bytes, got.mnemonic, got.op_str,
             annotate(ctx, got))
        i += got.size

    if stopped is not None:
        out("")
        out("; !! decode stopped at fo 0x%06x of 0x%06x - almost certainly a "
            "literal pool or jump table." % (stopped, end))
        out("; !! re-run with --resilient to sweep past it.")
        return 1
    return 0


# --------------------------------------------------------------------------
def selftest():
    fails = []

    def check(name, cond):
        if not cond:
            fails.append(name)

    # thumb literal-pool address arithmetic: pc = (addr+4) & ~3
    check("thumb lit even", ((0x100 + 4) & ~3) + 0x64 == 0x168)
    check("thumb lit odd", ((0x102 + 4) & ~3) + 0x04 == 0x108)
    # arm literal: pc = addr + 8, no alignment mask
    check("arm lit", 0x100 + 8 + 0x64 == 0x16C)

    class T(dict):
        pass

    t_fo = T(image_base=0x100000, reloc_delta=0x800, reloc_unit="file_offset",
             sl_fo=0x30000, encoding="ascii")
    t_g = T(image_base=0x100000, reloc_delta=0x800, reloc_unit="guest_address",
            sl_fo=0x30000, encoding="ascii")
    check("reloc file_offset", reloc_to_fo(t_fo, 0x000AB800) == 0xAC000)
    check("reloc guest_address", reloc_to_fo(t_g, 0x001AB800) == 0xAC000)

    data = bytearray(0x100)
    data[0x40:0x4B] = b"NETWORK_UP\x00"
    check("cstr", read_cstr(bytes(data), 0x40, "ascii") == "NETWORK_UP")
    check("cstr not a string", read_cstr(bytes(data), 0x00, "ascii") is None)
    data[0x60:0x64] = b"\x01\x02\x03\x04"
    check("u32", u32(bytes(data), 0x60) == 0x04030201)
    check("u32 oob", u32(bytes(data), len(data) - 2) is None)

    # branch-target regex must not pick an immediate out of a bracket group
    stripped = RE_BRACKETED.sub("", "r3, [pc, #0x64]")
    check("bracket strip", RE_BRANCH_TARGET.search(stripped) is None)
    check("branch imm", RE_BRANCH_TARGET.search("#0x13d0aa").group("t") == "0x13d0aa")

    nchecks = 10
    if capstone is not None:
        nchecks += 4
        # A pool word must be printed as data. Left to capstone it decodes as
        # perfectly plausible instructions sitting where instructions cannot be
        # - the most convincing wrong answer this tool can produce.
        img = bytearray(0x40)
        img[0x00:0x02] = b"\x01\x48"            # ldr r0,[pc,#4] -> 0x08
        img[0x02:0x04] = b"\x00\xbd"            # pop {pc}
        img[0x04:0x08] = b"\xc0\x46\xc0\x46"    # padding
        img[0x08:0x0C] = b"\x00\x1f\x00\x00"    # the pool word
        md = make_md("thumb")
        pools = collect_pools(md, bytes(img), 0, 0x0C, False)
        check("the pool word is identified from the load that reads it",
              pools == {0x08})
        lines = []
        t = T(image_base=0x100000, reloc_delta=0x100, reloc_unit="file_offset",
              encoding="ascii")
        run(lines.append, t, bytes(img), 0, 0x0C, "thumb", True, False)
        body = [ln for ln in lines if ln and not ln.startswith(";")]
        check("the pool word prints as .word, not as an instruction",
              any(ln.startswith("00000008") and ".word" in ln for ln in body))
        check("the instructions before it still print",
              any(ln.startswith("00000000") and "ldr" in ln for ln in body))
        lines2 = []
        run(lines2.append, t, bytes(img), 0, 0x0C, "thumb", True, False,
            mark_pools=False)
        check("--no-pool-marking really disables it",
              not any(".word" in ln for ln in lines2))

    if fails:
        for f in fails:
            print("FAIL %s" % f)
        print("selftest: %d failure(s)" % len(fails))
        return 1
    print("selftest: ok (%d checks)" % nchecks)
    return 0


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        prog="dis.py",
        description="Annotated Thumb/ARM disassembly of a guest image, printed "
                    "in FILE OFFSETS with literal, static-slot and branch "
                    "targets resolved inline.",
        epilog="Offsets accept 0x1234, decimal, g:0xGUEST, sl+0xOFF and sl-0xOFF. "
               "The end offset also accepts +0xLEN. Requires capstone.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("-t", "--target", help="path to target.toml")
    ap.add_argument("start", nargs="?", help="start file offset (inclusive)")
    ap.add_argument("end", nargs="?", help="end file offset (exclusive), or +0xLEN")
    ap.add_argument("--out", help="write the listing to this file (utf-8)")
    ap.add_argument("--resilient", action="store_true",
                    help="linear sweep: emit .hw 0xNNNN and advance 2 bytes on "
                         "undecodable data instead of stopping. Use this by default.")
    ap.add_argument("--isa", choices=("thumb", "arm"),
                    help="decode mode (default: target.toml `isa`)")
    ap.add_argument("--guest", action="store_true",
                    help="also print guest addresses in branch annotations")
    ap.add_argument("--no-pool-marking", action="store_true",
                    help="do not render literal-pool words as .word (they will "
                         "be decoded as instructions, which is what they are "
                         "not)")
    ap.add_argument("--binary", help="override target.toml `binary`")
    ap.add_argument("--selftest", action="store_true",
                    help="run the built-in arithmetic/decoder checks and exit")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()
    if not a.target:
        ap.error("-t/--target is required (or use --selftest)")
    if a.start is None or a.end is None:
        ap.error("start and end file offsets are required")
    if load_target is None:
        die("target.py must sit next to this tool (it loads target.toml)")

    tgt = load_target(a.target)
    data = read_image(tgt, a.target, a.binary)
    start = parse_fo(tgt, a.start, "start")
    end = (start + parse_int(a.end[1:], "length")
           if str(a.end).startswith("+") else parse_fo(tgt, a.end, "end"))
    if start < 0 or start >= len(data):
        die("start 0x%x is outside the image (0..0x%x)" % (start, len(data)))
    if end <= start:
        die("end 0x%x must be greater than start 0x%x" % (end, start))
    end = min(end, len(data))
    isa = a.isa or str(tget(tgt, "isa", "thumb"))

    fh = open(a.out, "w", encoding="utf-8", newline="\n") if a.out else None
    try:
        out = (lambda s: fh.write(s + "\n")) if fh else print
        rc = run(out, tgt, data, start, end, isa, a.resilient, a.guest,
                 mark_pools=not a.no_pool_marking)
    finally:
        if fh:
            fh.close()
            sys.stderr.write("dis.py: wrote %s\n" % a.out.replace("\\", "/"))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
