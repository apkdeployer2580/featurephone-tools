#!/usr/bin/env python3
"""deadcode_sweep.py - find byte ranges of the guest image that are neither executed nor read.

WHY YOU WANT THIS
    Sometimes the host needs a few bytes of writable guest memory that survive
    across ticks and that nothing else touches - an out-parameter scratch word
    for a guest function you are calling, for instance. A guest heap allocation
    would need freeing and could be reused; a static slot belongs to the game.
    A proven-dead range inside the game's own code is stable, is never read,
    and costs nothing.

    "Proven-dead" is the load-bearing word. Writing four bytes into code that
    turns out to be live is a guaranteed crash, and it will not reproduce until
    the one game state that reaches it.

METHOD (A) - reachability walk + three independent cross-checks
    1. WALK. A linear-recursive Thumb walk from the entry point and from every
       plausible code pointer. Straight-line decoding, stopping at
       unconditional flow changes (b, pop {..,pc}, bx, mov pc, add pc, udf) and
       queueing every branch and call target.

    2. ** FOLLOW `bl`. ** This is the correction that matters most. On Thumb-1
       the compiler routinely emits `bl` as a LONG BRANCH when `b.n` cannot
       reach - it is not necessarily a call. The first version of this sweep in
       the reference port did not follow `bl` targets, and it therefore
       reported a live function as dead: a single `bl` was the only edge into
       it. The reported cave started hundreds of bytes too early. Only the
       corrected sweep ships here. A sweep that assumes `bl` == call WILL mark
       live code dead.

    3. HONOUR LITERAL POOLS. When the walk decodes `ldr rX,[pc,#imm]` it marks
       the pool word as DATA, so it is never reported as dead (it is read) and
       never decoded as code.

    4. CROSS-CHECK, three ways, each able to veto a candidate range:
       a. every direct branch/call target found by a BLIND image-wide BL/BLX
          scan - independent of the walk, so it catches code the walk never
          reached;
       b. every Thumb function-pointer word found in a literal pool, in the
          static area, or in a run of consecutive pointer words (a jump table
          or vtable) - those runs are seeded into the walk as well;
       c. every data pointer stored in those same places that resolves into
          the candidate range - i.e. the range is READ even if not executed.

    5. SPLIT AT VETOES, DO NOT DISCARD. A veto is one address, not a verdict on
       the kilobytes around it. A real cave is normally bounded by live code at
       BOTH ends - that is exactly the shape of the range the reference port
       ended up using - so a hole containing a veto point is cut into
       sub-ranges rather than thrown away. Discarding produces a subtler
       failure than it sounds: the true cave disappears and the tool then
       recommends whatever unrelated stretch of padding is now the longest.
       Each sub-range is labelled with the veto that bounds it, and a range
       that STARTS immediately after a veto is flagged, because whatever begins
       at that address has unknown length and may run forward into it.

KNOWN LIMITS - read before you write a single byte
    * This proves unreachability UNDER THIS MODEL. A pointer computed at run
      time from data the tool cannot see is invisible to it. Treat the output
      as a candidate, take a range with a CLEAN START, use bytes in the MIDDLE
      of it, and confirm on a device before shipping.
    * Only Thumb decoding. An ARM-mode region will be walked as nonsense; the
      cross-checks still protect it, but prefer a range far from any
      ARM/Thumb boundary.
    * The default search window is [0, sl_fo) - the code region below the RWPI
      static base. Override with --code-start/--code-end if your image is laid
      out differently; dump_table.py will tell you where data begins.
    * Once you pick an address, pin it in your source with a COMPILE-TIME
      assertion that it lies inside the proven range, not with a unit test.
      Drifting out of the range means scribbling on live guest code, and that
      must not be something a passing build can do.

USAGE
    python deadcode_sweep.py -t target.toml
    python deadcode_sweep.py -t target.toml --min-len 128 --report-jumptables
    python deadcode_sweep.py -t target.toml --json --out dead.json
    python deadcode_sweep.py --selftest
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
CODE, DATA = 1, 2


def die(msg: str, code: int = 2):
    sys.stderr.write("deadcode_sweep.py: error: %s\n" % msg)
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
            "%r is not an integer (write 0x40 or 64)" % s)


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


def sx(v, bits):
    m = 1 << (bits - 1)
    return (v ^ m) - m


# --------------------------------------------------------------------------
def decode_pair(hw, i):
    """(kind, target_fo, size) for a 32-bit Thumb pair at halfword index i."""
    if i + 1 >= len(hw):
        return None
    h1, h2 = hw[i], hw[i + 1]
    if (h1 & 0xF800) != 0xF000:
        return None
    fo = i * 2
    top = h2 & 0xF800
    if top in (0xF800, 0xE800):
        off = sx(((h1 & 0x7FF) << 12) | ((h2 & 0x7FF) << 1), 23)
        tgt = fo + 4 + off
        if top == 0xE800:
            tgt &= ~3
            return "blx", tgt, 4
        return "bl", tgt, 4
    if (h2 & 0xD000) == 0x9000:      # Thumb-2 b.w; decoded for conservatism
        s = (h1 >> 10) & 1
        j1 = (h2 >> 13) & 1
        j2 = (h2 >> 11) & 1
        i1 = (~(j1 ^ s)) & 1
        i2 = (~(j2 ^ s)) & 1
        off = sx((s << 24) | (i1 << 23) | (i2 << 22) |
                 ((h1 & 0x3FF) << 12) | ((h2 & 0x7FF) << 1), 25)
        return "b.w", fo + 4 + off, 4
    return None


def walk(data, hw, seeds, mark):
    """Mark reachable code. Returns the set of jump-table dispatch sites."""
    n = len(data)
    dispatch = set()
    stack = [s & ~1 for s in seeds]
    while stack:
        fo = stack.pop()
        while True:
            if fo < 0 or fo + 2 > n or fo & 1:
                break
            if mark[fo] & CODE:
                break
            i = fo >> 1
            h = hw[i]
            size, stop = 2, False

            pair = decode_pair(hw, i)
            if pair is not None:
                kind, tgt, size = pair
                if 0 <= tgt < n:
                    stack.append(tgt & ~1)
                # a bl may be a long branch, but continuing past it is the
                # conservative choice: it can only mark MORE bytes live.
                stop = kind == "b.w"
            elif 0xE000 <= h <= 0xE7FF:               # b.n
                tgt = fo + 4 + sx(h & 0x7FF, 11) * 2
                if 0 <= tgt < n:
                    stack.append(tgt)
                stop = True
            elif 0xD000 <= h <= 0xDCFF:               # b<cond>
                tgt = fo + 4 + sx(h & 0xFF, 8) * 2
                if 0 <= tgt < n:
                    stack.append(tgt)
            elif 0xDE00 <= h <= 0xDEFF:               # udf
                stop = True
            elif 0xBD00 <= h <= 0xBDFF:               # pop {..,pc}
                stop = True
            elif (h & 0xFF87) == 0x4700:              # bx rM
                stop = True
            elif (h & 0xFF87) == 0x4687:              # mov pc, rM
                dispatch.add(fo)
                stop = True
            elif (h & 0xFF87) == 0x4487:              # add pc, rM
                dispatch.add(fo)
                stop = True
            elif 0x4800 <= h <= 0x4FFF:               # ldr rX,[pc,#imm]
                lit = ((fo + 4) & ~3) + (h & 0xFF) * 4
                if lit + 4 <= n:
                    for k in range(lit, lit + 4):
                        mark[k] |= DATA

            for k in range(fo, min(fo + size, n)):
                mark[k] |= CODE
            if stop:
                break
            fo += size
    return dispatch


def blind_bl_targets(hw, n):
    """Every BL/BLX/b.w target from a blind image-wide scan (no walk)."""
    out = set()
    for i in range(len(hw) - 1):
        p = decode_pair(hw, i)
        if p is not None and 0 <= p[1] < n:
            out.add(p[1] & ~1)
    return out


def blind_b_targets(hw, n):
    """Every direct b.n / b<cond> target from a blind scan."""
    out = set()
    for i, h in enumerate(hw):
        fo = i * 2
        if 0xE000 <= h <= 0xE7FF:
            t = fo + 4 + sx(h & 0x7FF, 11) * 2
        elif 0xD000 <= h <= 0xDCFF:
            t = fo + 4 + sx(h & 0xFF, 8) * 2
        else:
            continue
        if 0 <= t < n:
            out.add(t)
    return out


def pool_slots(hw, n):
    out = set()
    for i, h in enumerate(hw):
        if 0x4800 <= h <= 0x4FFF:
            fo = i * 2
            lit = ((fo + 4) & ~3) + (h & 0xFF) * 4
            if lit + 4 <= n:
                out.add(lit)
    return out


def pointer_word_positions(wds, n, pools, static_start, jt_min, resolve):
    """Where pointers actually live: literal pools, the static area, and runs.

    Returns (positions, jumptables). A "run" is >= jt_min consecutive
    word-aligned words that all resolve to an in-image Thumb code pointer -
    i.e. a jump table or a vtable.
    """
    positions = set(pools)
    if static_start is not None:
        for fo in range(static_start & ~3, (n & ~3), 4):
            positions.add(fo)
    runs = []
    run_start, run_len = None, 0
    for idx in range(len(wds)):
        w = wds[idx]
        ok = False
        if w & 1:
            t = resolve(w & ~1)
            ok = 0 <= t < n and (t & 1) == 0
        if ok:
            if run_start is None:
                run_start, run_len = idx * 4, 1
            else:
                run_len += 1
        else:
            if run_start is not None and run_len >= jt_min:
                runs.append((run_start, run_len))
                for k in range(run_len):
                    positions.add(run_start + 4 * k)
            run_start, run_len = None, 0
    if run_start is not None and run_len >= jt_min:
        runs.append((run_start, run_len))
        for k in range(run_len):
            positions.add(run_start + 4 * k)
    return positions, runs


def referenced_targets(data, positions, n, resolve):
    """Everything the stored pointers point AT: code targets and data targets."""
    code_t, data_t = set(), set()
    for fo in positions:
        if fo + 4 > n:
            continue
        w = int.from_bytes(data[fo:fo + 4], "little")
        if w & 1:
            t = resolve(w & ~1)
            if 0 <= t < n:
                code_t.add(t)
        t = resolve(w)
        if 0 <= t < n:
            data_t.add(t)
    return code_t, data_t


def find_ranges(mark, lo, hi, min_len, vetoes, detail=None):
    """Unmarked runs in [lo, hi), SPLIT at every veto point, >= min_len long.

    Splitting rather than discarding is the whole point, and an earlier version
    of this tool got it wrong. A veto point is one address that something
    references; it is not a reason to throw away the kilobytes on either side
    of it. The reference port's usable cave was bounded by live code at BOTH
    ends - a discard-on-veto policy reports nothing there, and then recommends
    some unrelated stretch of padding as "the largest range", which is a
    confident wrong answer.

    A sub-range that begins immediately after a veto point is flagged: whatever
    starts at that vetoed address has unknown length, so it may run forward
    into the range. Ranges with `after=None` are the safe ones and the caller
    prefers them when recommending a scratch address.

    `detail`, if given, is a list that receives one dict per reported range
    recording which veto bounded each end.
    """
    pts = {}
    for why, xs in vetoes:
        for x in xs:
            if lo <= x < hi:
                pts.setdefault(x, why)
    ordered = sorted(pts.items())

    out = []
    i = lo
    p = 0
    while p < len(ordered) and ordered[p][0] < i:
        p += 1
    while i < hi:
        if mark[i]:
            i += 1
            continue
        j = i
        while j < hi and not mark[j]:
            j += 1
        # split [i, j) at the veto points inside it
        sub, after = i, None
        while p < len(ordered) and ordered[p][0] < j:
            pos, why = ordered[p]
            if pos >= sub and pos - sub >= min_len:
                out.append((sub, pos))
                if detail is not None:
                    detail.append({"start": sub, "end": pos, "after": after,
                                   "before": why})
            # the vetoed byte itself is excluded, and so is the rest of
            # whatever starts there - which we cannot size, hence the flag
            sub = max(sub, pos + 1)
            after = why
            p += 1
        if j - sub >= min_len:
            out.append((sub, j))
            if detail is not None:
                detail.append({"start": sub, "end": j, "after": after,
                               "before": None})
        i = j
    order = sorted(range(len(out)), key=lambda k: -(out[k][1] - out[k][0]))
    if detail is not None:
        detail[:] = [detail[k] for k in order]
    return [out[k] for k in order]


# --------------------------------------------------------------------------
def sweep(data, resolve, entry, min_len, jt_min, code_start, code_end,
          static_start):
    n = len(data)
    hw = halfwords(data)
    wds = words(data)
    mark = bytearray(n)

    pools = pool_slots(hw, n)
    positions, runs = pointer_word_positions(wds, n, pools, static_start,
                                             jt_min, resolve)
    code_t, data_t = referenced_targets(data, positions, n, resolve)

    seeds = [entry & ~1]
    seeds.extend(t for t in code_t if (t & 1) == 0)
    walk(data, hw, seeds, mark)
    for fo in positions:
        for k in range(fo, min(fo + 4, n)):
            mark[k] |= DATA

    vetoes = [
        ("blind bl/blx target", blind_bl_targets(hw, n)),
        ("blind direct-branch target", blind_b_targets(hw, n)),
        ("stored code pointer", code_t),
        ("stored data pointer", data_t),
    ]
    detail = []
    ranges = find_ranges(mark, code_start, code_end, min_len, vetoes, detail)
    stats = {
        "image_len": n,
        "marked_code": sum(1 for x in mark if x & CODE),
        "marked_data": sum(1 for x in mark if x & DATA),
        "pool_slots": len(pools),
        "pointer_word_positions": len(positions),
        "jump_tables": len(runs),
        "veto_points": sum(len(v[1]) for v in vetoes),
    }
    stats["detail"] = detail
    return ranges, runs, stats


# --------------------------------------------------------------------------
def selftest():
    fails = []

    def check(name, cond):
        if not cond:
            fails.append(name)

    n = 0x400
    img = bytearray(n)

    def put16(fo, h):
        img[fo:fo + 2] = int(h).to_bytes(2, "little")

    def bl(frm, to):
        off = to - (frm + 4)
        u = off & 0x7FFFFF
        put16(frm, 0xF000 | ((u >> 12) & 0x7FF))
        put16(frm + 2, 0xF800 | ((u >> 1) & 0x7FF))

    # entry: one bl into a big function at 0x100, then return.
    bl(0x00, 0x100)
    put16(0x04, 0xBD00)                       # pop {pc}
    # 0x006 .. 0x0ff  -> dead hole #1
    put16(0x100, 0xB500)                      # push {lr}
    for fo in range(0x102, 0x140, 2):
        put16(fo, 0x2000)                     # movs r0,#0
    put16(0x140, 0xBD00)                      # pop {pc}
    # 0x142 .. 0x3ff -> dead hole #2

    def resolve(w):
        return w                              # delta 0, unit file_offset

    data = bytes(img)
    ranges, runs, stats = sweep(data, resolve, 0x1, 64, 3, 0, n, None)

    def covered(x):
        return any(s <= x < e for s, e in ranges)

    check("hole 1 found", covered(0x40))
    check("hole 2 found", covered(0x200))
    # THE REGRESSION: the function at 0x100 is reachable ONLY through a bl.
    check("bl target is live (not reported dead)", not covered(0x110))
    check("bl target start is live", not covered(0x100))
    check("entry is live", not covered(0x00))
    check("min-len respected", all(e - s >= 64 for s, e in ranges))

    # prove the regression detector actually detects: a sweep that does not
    # follow bl must report the function as dead
    hw = halfwords(data)
    mark = bytearray(n)
    stack = [0]
    while stack:  # a deliberately WRONG walk: bl is treated as a plain call
        fo = stack.pop()
        while 0 <= fo + 2 <= n and not (mark[fo] & CODE):
            h = hw[fo >> 1]
            size = 4 if (h & 0xF800) == 0xF000 else 2
            for k in range(fo, min(fo + size, n)):
                mark[k] |= CODE
            if 0xBD00 <= h <= 0xBDFF:
                break
            fo += size
    wrong = find_ranges(mark, 0, n, 64, [])
    check("the no-bl-following sweep really is wrong",
          any(s <= 0x110 < e for s, e in wrong))

    # A literal pool must not be reported dead and must not be decoded as
    # code; and a function reached ONLY through a stored pointer must be live.
    img2 = bytearray(n)
    img2[0x100:0x142] = img[0x100:0x142]              # the same function body

    def put16b(fo, h):
        img2[fo:fo + 2] = int(h).to_bytes(2, "little")

    put16b(0x000, 0x4805)          # ldr r0,[pc,#0x14]  -> literal at 0x018
    put16b(0x002, 0xE001)          # b.n 0x008          (skips the pool)
    put16b(0x008, 0xBD00)          # pop {pc}
    img2[0x018:0x01C] = (0x101).to_bytes(4, "little")  # thumb fn ptr -> 0x100
    ranges2, _r2, _s2 = sweep(bytes(img2), resolve, 0x1, 8, 3, 0, n, None)
    check("literal pool is not reported dead",
          not any(s <= 0x18 < e for s, e in ranges2))
    check("function reached only via a stored pointer is live",
          not any(s <= 0x110 < e for s, e in ranges2))

    check("sx 11-bit", sx(0x7FF, 11) == -1)
    check("sx 8-bit", sx(0x80, 8) == -128)
    check("stats sane", stats["marked_code"] > 0 and stats["image_len"] == n)

    # THE SPLIT REGRESSION. One veto point in the middle of a big hole must
    # split it, not delete it. The earlier discard-on-veto policy returned
    # nothing here - and in a real image that means the tool skips the actual
    # cave and confidently recommends some unrelated stretch of padding.
    m3 = bytearray(0x1000)
    for k in range(0x000, 0x010):
        m3[k] = CODE
    for k in range(0xFF0, 0x1000):
        m3[k] = CODE
    split = find_ranges(m3, 0, 0x1000, 0x40, [("stored data pointer", {0x800})])
    check("a veto SPLITS the hole instead of deleting it", len(split) == 2)
    check("split keeps the bytes before the veto", (0x10, 0x800) in split)
    check("split keeps the bytes after the veto", (0x801, 0xFF0) in split)
    d3 = []
    find_ranges(m3, 0, 0x1000, 0x40, [("stored data pointer", {0x800})], d3)
    byrange = {(x["start"], x["end"]): x for x in d3}
    check("the range before a veto has a clean start",
          byrange[(0x10, 0x800)]["after"] is None)
    check("the range after a veto is flagged",
          byrange[(0x801, 0xFF0)]["after"] == "stored data pointer")
    check("sub-ranges under min-len are dropped",
          not find_ranges(m3, 0, 0x1000, 0x900,
                          [("stored data pointer", {0x800})]))

    if fails:
        for f in fails:
            print("FAIL %s" % f)
        print("selftest: %d failure(s)" % len(fails))
        return 1
    print("selftest: ok (18 checks)")
    return 0


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        prog="deadcode_sweep.py",
        description="Reachability sweep that reports byte ranges neither "
                    "executed nor read. Follows bl (Thumb-1 uses it as a long "
                    "branch), honours literal pools, and cross-checks blind "
                    "branch scans, stored code pointers and jump tables.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Output is a CANDIDATE, not a certificate. Use the middle of the "
               "recommended range (the longest one with a clean start), and "
               "guard the address with a compile-time assertion in your source.",
    )
    ap.add_argument("-t", "--target", help="path to target.toml")
    ap.add_argument("--entry", default="0x1",
                    help="entry point as a FILE OFFSET, Thumb bit allowed "
                         "(default 0x1 = offset 0, Thumb)")
    ap.add_argument("--min-len", type=hexint, default=64,
                    help="smallest range to report, bytes (default 64)")
    ap.add_argument("--jt-min", type=hexint, default=3,
                    help="consecutive code-pointer words that count as a jump "
                         "table (default 3)")
    ap.add_argument("--code-start", help="search window start (default 0)")
    ap.add_argument("--code-end",
                    help="search window end (default: sl_fo, the static base; "
                         "pass the image length if your data is elsewhere)")
    ap.add_argument("--static-start",
                    help="start of the static/.data area whose words are "
                         "treated as pointers (default: sl_fo)")
    ap.add_argument("--report-jumptables", action="store_true",
                    help="list the detected pointer runs")
    ap.add_argument("--top", type=hexint, default=25, help="ranges to print (default 25)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--out", help="write the report to this file (utf-8)")
    ap.add_argument("--binary", help="override target.toml `binary`")
    ap.add_argument("--selftest", action="store_true",
                    help="run the built-in reachability checks (including the "
                         "bl-following regression) and exit")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()
    if not a.target:
        ap.error("-t/--target is required (or use --selftest)")
    if load_target is None:
        die("target.py must sit next to this tool (it loads target.toml)")

    tgt = load_target(a.target)
    data = read_image(tgt, a.target, a.binary)
    base = int(tget(tgt, "image_base", 0))
    delta, unit, reloc_declared = reloc_cfg(tgt)
    if not reloc_declared:
        sys.stderr.write(
            "deadcode_sweep.py: warning: target.toml declares no reloc_delta, "
            "so stored pointers are read unbiased. The sweep can only be "
            "trusted once solve_reloc.py has settled the relocation.\n")
    _sl = tget(tgt, "sl_fo")
    sl = 0 if _sl is None else int(_sl)

    def resolve(w):
        v = (int(w) + delta) & M32
        return v - (base if unit == "guest_address" else 0)

    n = len(data)
    static_start = (parse_int(a.static_start, "--static-start")
                    if a.static_start else (sl if 0 < sl < n else None))
    code_start = parse_int(a.code_start, "--code-start") if a.code_start else 0
    if a.code_end:
        code_end = parse_int(a.code_end, "--code-end")
    else:
        code_end = sl if 0 < sl < n else n
    code_start = max(0, code_start)
    code_end = min(n, code_end)
    if code_end <= code_start:
        die("empty search window 0x%x:0x%x" % (code_start, code_end))

    ranges, runs, stats = sweep(data, resolve, parse_int(a.entry, "--entry"),
                                a.min_len, a.jt_min, code_start, code_end,
                                static_start)

    detail = stats.pop("detail", [{} for _ in ranges])
    # A range that starts immediately after a veto point is riskier than one
    # that starts at a hole boundary: something references that vetoed address
    # and we cannot size what starts there.
    clean = [k for k in range(len(ranges)) if not detail[k].get("after")]
    pick = clean[0] if clean else (0 if ranges else None)

    if a.json:
        text = json.dumps({
            "window": [code_start, code_end],
            "stats": stats,
            "ranges": [{"fo_start": s, "fo_end": e, "len": e - s,
                        "guest_start": s + base, "guest_end": e + base,
                        "after_veto": detail[k].get("after"),
                        "before_veto": detail[k].get("before"),
                        "recommended": k == pick}
                       for k, (s, e) in enumerate(ranges[: a.top])],
            "jump_tables": ([{"fo": s, "entries": ln} for s, ln in runs]
                            if a.report_jumptables else None),
        }, indent=2, sort_keys=True)
    else:
        out = []
        out.append("; window fo 0x%06x..0x%06x of 0x%06x  (guest 0x%08x..0x%08x)"
                   % (code_start, code_end, n, code_start + base, code_end + base))
        out.append("; %d bytes marked code, %d marked data, %d pointer words, "
                   "%d jump tables, %d veto points"
                   % (stats["marked_code"], stats["marked_data"],
                      stats["pointer_word_positions"], stats["jump_tables"],
                      stats["veto_points"]))
        out.append("; fo range               len     guest range              bounded by")
        for k, (s, e) in enumerate(ranges[: a.top]):
            aft = detail[k].get("after")
            bef = detail[k].get("before")
            note = "clean start" if not aft else "STARTS AFTER a %s" % aft
            if bef:
                note += "; ends at a %s" % bef
            out.append("  0x%06x..0x%06x   %6d   0x%08x..0x%08x   %s"
                       % (s, e, e - s, s + base, e + base, note))
        out.append("; %d range(s) of >= %d bytes survived all four checks "
                   "(%d with a clean start)"
                   % (len(ranges), a.min_len, len(clean)))
        out.append("; ranges are SPLIT at veto points, not discarded: a cave is "
                   "normally bounded by live code at both ends.")
        if a.report_jumptables:
            out.append("; jump tables / vtables (runs of >= %d code-pointer words):"
                       % a.jt_min)
            for s, ln in runs[:200]:
                out.append("  0x%06x  %d entries" % (s, ln))
            if len(runs) > 200:
                out.append("  ... and %d more" % (len(runs) - 200))
        if ranges:
            s, e = ranges[pick]
            mid = (s + (e - s) // 2) & ~3
            out.append("")
            out.append("; recommended range: fo 0x%06x..0x%06x "
                       "(guest 0x%08x..0x%08x)" % (s, e, s + base, e + base))
            if not clean:
                out.append("; !! EVERY surviving range starts right after a "
                           "veto point. Something references that address and "
                           "its length is unknown, so this range may not be "
                           "dead at all. Disassemble forward from the veto "
                           "with dis.py --resilient before you write here.")
            elif pick != 0:
                out.append("; (not the longest: the longest range starts right "
                           "after a veto point, so this shorter one with a "
                           "clean start is the safer candidate)")
            out.append("; a scratch word in the middle would be guest 0x%08x."
                       % (mid + base))
            out.append("; pin it in your source with a compile-time assertion, "
                       "e.g. in Rust:")
            out.append(";   const DEAD_LO: u32 = 0x%08x;" % (s + base))
            out.append(";   const DEAD_HI: u32 = 0x%08x;" % (e + base))
            out.append(";   const SCRATCH: u32 = 0x%08x;" % (mid + base))
            out.append(";   const _: () = assert!(SCRATCH >= DEAD_LO && "
                       "SCRATCH + 4 <= DEAD_HI && SCRATCH % 4 == 0);")
            out.append("; then CONFIRM ON A DEVICE. This is a candidate, not a "
                       "certificate.")
        else:
            out.append("; no range survived. Lower --min-len, or accept that "
                       "this image has no cave and allocate instead.")
        text = "\n".join(out)

    if a.out:
        with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text + "\n")
        sys.stderr.write("deadcode_sweep.py: wrote %s\n" % a.out.replace("\\", "/"))
    else:
        print(text)
    return 0 if ranges else 1


if __name__ == "__main__":
    raise SystemExit(main())
