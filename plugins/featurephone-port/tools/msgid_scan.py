#!/usr/bin/env python3
"""msgid_scan.py - find the id-consuming accessor without knowing its name.

THE PROBLEM
    An AOT-compiled game binary has no symbols. You know the game has something
    like `getText(id)` or `showMessage(code)` or `playSound(id)` - a function
    called from all over with a small constant - but you have no idea where it
    is, and you cannot grep for a name that does not exist.

METHOD (A) - inverse index by constant-id density
    Turn the question inside out. Instead of "what does function F take", ask
    "which function is called with the widest variety of small constants":

      1. Scan every `movs rX,#imm8` in the image whose immediate is below
         --id-count (the number of ids the game actually has - get it from the
         decoded string table, or guess generously).
      2. Find the next `bl` within --window halfwords.
      3. Tally callee -> set of distinct ids.
      4. Rank by DISTINCT id count, not by call count.

    Distinctness is the whole trick. A hot low-level helper is called ten
    thousand times but with two or three different constants. The real text
    API is called a few hundred times with a few hundred DIFFERENT ids, because
    every id in the table is used somewhere. The gap between rank 1 and rank 5
    is usually an order of magnitude, and the `cover%` column - what fraction
    of the whole id space the callee has been seen with - makes it obvious.

    Then `--sites-for <id>` inverts it again: given the refusal message whose
    id you care about, print the handful of call sites that pass that exact id.
    In a real image that is frequently ONE site, and disassembling backwards
    from it lands you on the branch that guards the behaviour you want to
    change.

KNOWN LIMITS
    * Thumb-1 `movs rX,#imm8` only. An id >= 256, or one loaded from a literal
      pool, or one computed, is invisible to this scan. If your id space is
      larger than 255, this finds the low ids only - which is normally enough
      to identify the function.
    * The scan does not prove the constant reaches the call in that register;
      it is a proximity heuristic. `--stop-at-branch` tightens it (a branch
      between the move and the call means the value may not survive) at the
      cost of losing real sites. Confirm with dis.py before acting.
    * A dispatcher may map a message CODE to a text ID through a jump table, in
      which case you will find two functions: the one taking codes and the one
      taking ids. Both are useful; do not assume the first hit is the text API.
    * Blind halfword scanning cannot tell code from data, so a literal pool can
      contribute phantom sites. They do not survive the distinctness ranking.

USAGE
    python msgid_scan.py -t target.toml --id-count 200
    python msgid_scan.py -t target.toml --id-count 200 --top 10 --json
    python msgid_scan.py -t target.toml --id-count 0x100 --sites-for 0xID
    python msgid_scan.py --selftest
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


def die(msg: str, code: int = 2):
    sys.stderr.write("msgid_scan.py: error: %s\n" % msg)
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
        return int(str(s).strip().replace("_", ""), 0)
    except ValueError:
        die("%s: cannot parse %r as an integer" % (what, s))


def hexint(s):
    """argparse type accepting 0x..., 0b..., 0o... and decimal.

    Message ids are quoted in hex everywhere in this toolkit (--sites-for 0x87),
    so an --id-count that only accepted decimal was a papercut that rejected the
    obvious `--id-count 0x100`.
    """
    try:
        return int(str(s).strip().replace("_", ""), 0)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "%r is not an integer (write 0x100 or 256)" % s)


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


def is_branchish(h):
    if 0xD000 <= h <= 0xDFFF:
        return True
    if 0xE000 <= h <= 0xE7FF:
        return True
    if (h & 0xFF80) == 0x4700:
        return True
    if 0xBD00 <= h <= 0xBDFF:
        return True
    return False


def decode_bl(hw, j):
    """(target_fo, kind) if halfwords j,j+1 are a BL/BLX pair, else None."""
    if j + 1 >= len(hw):
        return None
    h1, h2 = hw[j], hw[j + 1]
    if (h1 & 0xF800) != 0xF000:
        return None
    top = h2 & 0xF800
    if top == 0xF800:
        kind = "bl"
    elif top == 0xE800:
        kind = "blx"
    else:
        return None
    off = ((h1 & 0x7FF) << 12) | ((h2 & 0x7FF) << 1)
    if off & 0x400000:
        off -= 0x800000
    tgt = j * 2 + 4 + off
    if kind == "blx":
        tgt &= ~3
    return tgt, kind


def scan(hw, dlen, id_count, window, reg, stop_at_branch):
    """Return {callee_fo: {"ids": {...}, "sites": [(site_fo, id, rd), ...]}}."""
    acc = {}
    n = len(hw)
    for i in range(n):
        h = hw[i]
        if (h & 0xF800) != 0x2000:
            continue
        imm = h & 0xFF
        rd = (h >> 8) & 7
        if imm >= id_count:
            continue
        if reg is not None and rd != reg:
            continue
        for j in range(i + 1, min(i + 1 + window, n - 1)):
            got = decode_bl(hw, j)
            if got is not None:
                tgt = got[0]
                if 0 <= tgt < dlen:
                    e = acc.setdefault(tgt, {"ids": set(), "sites": []})
                    e["ids"].add(imm)
                    e["sites"].append((i * 2, imm, rd, j * 2))
                break
            if stop_at_branch and is_branchish(hw[j]):
                break
    return acc


def rank(acc, id_count, min_distinct):
    rows = []
    for callee, e in acc.items():
        d = len(e["ids"])
        if d < min_distinct:
            continue
        rows.append({"callee_fo": callee, "distinct_ids": d,
                     "sites": len(e["sites"]),
                     "cover_pct": round(100.0 * d / max(1, id_count), 2),
                     "id_min": min(e["ids"]), "id_max": max(e["ids"])})
    rows.sort(key=lambda r: (-r["distinct_ids"], -r["sites"], r["callee_fo"]))
    return rows


def selftest():
    fails = []

    def check(name, cond):
        if not cond:
            fails.append(name)

    img = bytearray(0x1000)

    def put16(fo, h):
        img[fo:fo + 2] = int(h).to_bytes(2, "little")

    def bl(frm, to):
        off = to - (frm + 4)
        u = off & 0x7FFFFF
        put16(frm, 0xF000 | ((u >> 12) & 0x7FF))
        put16(frm + 2, 0xF800 | ((u >> 1) & 0x7FF))

    TEXT_API = 0x700
    HELPER = 0x780
    # the text API: 40 call sites, 40 distinct ids
    fo = 0x100
    for i in range(40):
        put16(fo, 0x2000 | i)          # movs r0,#i
        bl(fo + 2, TEXT_API)
        fo += 8
    # a hot helper: 60 call sites, 2 distinct ids
    for i in range(60):
        put16(fo, 0x2000 | (i % 2))
        bl(fo + 2, HELPER)
        fo += 8

    hw = halfwords(bytes(img))
    acc = scan(hw, len(img), 200, 8, None, False)
    rows = rank(acc, 200, 1)
    check("text api ranks first", rows and rows[0]["callee_fo"] == TEXT_API)
    check("text api distinct", rows[0]["distinct_ids"] == 40)
    helper = [r for r in rows if r["callee_fo"] == HELPER]
    check("helper found", helper and helper[0]["distinct_ids"] == 2)
    check("helper has more sites than the winner",
          helper[0]["sites"] > rows[0]["sites"])
    check("distinctness beats call count",
          rows[0]["distinct_ids"] > helper[0]["distinct_ids"])

    sites = [s for s in acc[TEXT_API]["sites"] if s[1] == 7]
    check("sites-for finds one site", len(sites) == 1)
    check("site offset is the movs", sites[0][0] == 0x100 + 7 * 8)

    check("bl decode", decode_bl(hw, (0x102) // 2)[0] == TEXT_API)
    check("reg filter", not scan(hw, len(img), 200, 8, 3, False))
    check("id-count filter",
          max(len(e["ids"]) for e in
              scan(hw, len(img), 10, 8, None, False).values()) == 10)

    check("--id-count accepts hex", hexint("0x100") == 256)
    check("--id-count accepts decimal", hexint("256") == 256)

    if fails:
        for f in fails:
            print("FAIL %s" % f)
        print("selftest: %d failure(s)" % len(fails))
        return 1
    print("selftest: ok (12 checks)")
    return 0


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        prog="msgid_scan.py",
        description="Rank callees by how many DISTINCT small constants they are "
                    "called with, to identify an id-consuming accessor "
                    "(getText / showMessage / playSound) in a stripped image.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Rank by distinct ids, never by call count: the hottest callee "
               "is a helper, the widest one is the API.",
    )
    ap.add_argument("-t", "--target", help="path to target.toml")
    ap.add_argument("--id-count", type=hexint,
                    help="size of the id space, decimal or 0x-hex "
                         "(ids >= this are ignored)")
    ap.add_argument("--top", type=hexint, default=20, help="rows to print (default 20)")
    ap.add_argument("--sites-for", help="print the call sites passing this id")
    ap.add_argument("--window", type=hexint, default=8,
                    help="halfwords to look ahead for the bl (default 8)")
    ap.add_argument("--reg", type=hexint,
                    help="only count constants moved into this register "
                         "(default: any low register)")
    ap.add_argument("--stop-at-branch", action="store_true",
                    help="abandon a candidate if a branch sits between the "
                         "movs and the bl (fewer false attributions, some "
                         "real sites lost)")
    ap.add_argument("--min-distinct", type=hexint, default=2,
                    help="drop callees with fewer distinct ids (default 2)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--binary", help="override target.toml `binary`")
    ap.add_argument("--selftest", action="store_true",
                    help="run the built-in ranking checks and exit")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()
    if not a.target:
        ap.error("-t/--target is required (or use --selftest)")
    if a.id_count is None:
        ap.error("--id-count is required (the number of ids the game has; "
                 "guess generously if you do not know)")
    if load_target is None:
        die("target.py must sit next to this tool (it loads target.toml)")

    tgt = load_target(a.target)
    data = read_image(tgt, a.target, a.binary)
    base = int(tget(tgt, "image_base", 0))
    hw = halfwords(data)
    acc = scan(hw, len(data), a.id_count, a.window, a.reg, a.stop_at_branch)
    rows = rank(acc, a.id_count, a.min_distinct)

    if a.sites_for is not None:
        wanted = parse_int(a.sites_for, "--sites-for")
        sites = []
        for callee, e in acc.items():
            for site_fo, imm, rd, bl_fo in e["sites"]:
                if imm == wanted:
                    sites.append({"movs_fo": site_fo, "bl_fo": bl_fo,
                                  "callee_fo": callee, "reg": rd})
        sites.sort(key=lambda s: s["movs_fo"])
        if a.json:
            print(json.dumps({"id": wanted, "sites": sites}, indent=2, sort_keys=True))
            return 0 if sites else 1
        print("; call sites passing the constant 0x%02x (%d)" % (wanted, wanted))
        for s in sites:
            print("%08x  movs r%d,#0x%02x   ->  bl %08x  callee fo 0x%06x "
                  "(guest 0x%08x)"
                  % (s["movs_fo"], s["reg"], wanted, s["bl_fo"], s["callee_fo"],
                     s["callee_fo"] + base))
        print("; %d site(s). If this is 1, disassemble backwards from it with "
              "dis.py --resilient: the branch that guards it is usually within "
              "20 bytes." % len(sites))
        return 0 if sites else 1

    if a.json:
        print(json.dumps({"id_count": a.id_count, "ranking": rows[: a.top]},
                         indent=2, sort_keys=True))
        return 0
    print("; callees ranked by DISTINCT constant ids (id space 0..0x%x)"
          % (a.id_count - 1))
    print("; rank  callee_fo  guest       distinct  cover%  sites  id range")
    for i, r in enumerate(rows[: a.top], 1):
        print("  %3d   0x%06x   0x%08x  %8d  %5.1f  %5d  0x%02x..0x%02x"
              % (i, r["callee_fo"], r["callee_fo"] + base, r["distinct_ids"],
                 r["cover_pct"], r["sites"], r["id_min"], r["id_max"]))
    if len(rows) >= 2:
        gap = rows[0]["distinct_ids"] / max(1.0, float(rows[1]["distinct_ids"]))
        print("; rank1/rank2 distinctness ratio = %.1fx" % gap)
        if gap < 1.5:
            print("; WARNING: no clear winner. Raise --id-count if the id space "
                  "is bigger than you assumed, widen --window, or the API may "
                  "take its id in a register this scan does not see.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
