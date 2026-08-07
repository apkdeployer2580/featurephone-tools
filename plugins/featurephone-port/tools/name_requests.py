#!/usr/bin/env python3
"""Name a protocol's requests from UI strings, without triggering them.

WHAT IT DOES
    Chains a localized UI status string to the nearest send site, so that a
    request catalog produced by extract_packets.py stops being a list of hex
    templates at unnamed addresses and becomes a list of named operations.

THE METHOD IT EMBODIES  (A)
    Every online operation in a game of this era shows a status message from the
    *same function* that sends the request ("contacting server", "saving", ...).
    That co-location is the exploit.  The chain, one link at a time:

        [1] string file offset                     (from strings_dump.py)
        [2] -> the relocated pointer WORD that would be stored for it
               word = fo - reloc_delta          when reloc_unit = "file_offset"
               word = (fo + image_base) - reloc_delta
                                                when reloc_unit = "guest_address"
        [3] -> the .data slot that holds that word    (4-aligned search of the image)
        [4] -> that slot's static-base-relative offset:  slot_fo - sl_fo
               (RWPI code addresses statics as [sl, #off] or via a literal `off`)
        [5] -> every literal-pool word equal to that offset
               (that IS how the code names the string)
        [6] -> the nearest `bl flush` site within +/- --window of the literal
               (that IS the request the operation sends)

    Nothing is executed.  Nothing is triggered on a device.  You get names for
    requests you have never made.

CONFIDENCE -- READ THIS  (A)
    Every row this tool prints is labelled MEDIUM, always, by construction:
    the window typically catches more than one flush candidate, and a literal
    pool word can be shared.  Two honest ways to use it:
      * CLUSTER-LEVEL attribution is reliable.  If four warehouse strings all
        land in one 4 KB region of the image, that region is the warehouse code,
        even if you cannot say which of its three flush sites is which.
        The tool prints the cluster for exactly this reason.
      * SITE-LEVEL attribution is a hypothesis.  Confirm it by making the
        operation happen once on a device with a transcript running, or by
        reading the function with dis.py.
    Do not promote a row to HIGH because it "looks right".  Promote it when a
    transcript or a disassembly says so, and write the date next to it.

WORKED EXAMPLE  (B) -- from one reference port, values are NOT defaults
    A window of +/-0x600 halfwords around each literal caught roughly two flush
    candidates per string.  Cluster attribution came out clean: one 4 KB region
    held every warehouse operation, another every currency operation, a third
    the login sequence.  Site attribution needed the device.  Your delta, your
    static base and your window will all be different -- pin them in target.toml
    with solve_reloc.py first.  (C) do not copy the numbers.

A COROLLARY WORTH MORE THAN THE TOOL  (A)
    The string dump is also your FEATURE INVENTORY.  The set of UI strings that
    can only appear while a server is alive tells you exactly what a replacement
    server must implement.  Grep the dump for waiting/saving/purchasing/
    connecting phrasing in the game's language before you write a line of server
    code, and turn that list into the chapter plan.

KNOWN LIMITS
    * If reloc_unit in target.toml is wrong, EVERY row is wrong and nothing
      warns you except this tool's cross-check against target.deref_reloc().
      Heed that warning; it is the single highest-cost misconfiguration.
    * Strings referenced only through a computed pointer (concatenation, format
      building, a table index) have no literal-pool word and produce no rows.
    * A string used by several functions produces several slots and several
      literal sites.  All are printed; none is preferred.
    * Requires the flush routine address.  Find it the way extract_packets.py
      documents, then reuse it here.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from target import load_target
except ImportError:
    try:
        from target import Target as _Target
    except ImportError as exc:  # pragma: no cover
        sys.exit(
            "error: cannot import target.py from the tools directory (%s).\n"
            "       name_requests.py must live next to target.py." % exc
        )

    def load_target(path):
        return _Target.load(path)


_MISSING = object()


def cfg(t, key, default=_MISSING):
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
        sys.exit("error: target.toml provides no usable '%s'." % key)
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
    sys.exit("error: guest binary not found: %s" % path)


# --------------------------------------------------------------------------
# BL scan (kept local so each tool stands alone)
# --------------------------------------------------------------------------


def decode_bl(hi: int, lo: int):
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


def scan_flush_sites(data: bytes, flush_fo: int):
    sites = []
    for fo in range(0, len(data) - 3, 2):
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
        if tgt == flush_fo:
            sites.append(fo)
    return sites


def find_words(data: bytes, value: int):
    """4-aligned file offsets whose little-endian u32 equals value."""
    want = (value & 0xFFFFFFFF).to_bytes(4, "little")
    out = []
    start = 0
    while True:
        idx = data.find(want, start)
        if idx < 0:
            break
        if idx % 4 == 0:
            out.append(idx)
        start = idx + 1
    return out


# --------------------------------------------------------------------------
# string list input
# --------------------------------------------------------------------------


_OFFSET_RE = re.compile(r"^\s*(0[xX][0-9a-fA-F]+|[0-9a-fA-F]{4,8})\b[\s:,|\t-]*(.*)$")


def read_string_list(path, encoding_hint):
    """Accept the offset<->text listings strings_dump.py produces, plus anything
    close to them: '0x1234  text', '0x1234: text', '1234\ttext', '# comment'."""
    rows = []
    with open(path, "r", encoding=encoding_hint, errors="replace") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.rstrip("\r\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            m = _OFFSET_RE.match(line)
            if not m:
                continue
            text = m.group(1)
            try:
                fo = int(text, 16) if text.lower().startswith("0x") else int(text, 16)
            except ValueError:
                continue
            rows.append((fo, m.group(2).strip(), lineno))
    return rows


# --------------------------------------------------------------------------


def parse_addr(text):
    text = str(text).strip()
    return int(text, 16) if text.lower().startswith("0x") else int(text, 0)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="name_requests.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Chain UI strings to their nearest send site to NAME requests "
                    "without triggering them.",
        epilog=(
            "Every row is MEDIUM confidence by construction.  Cluster-level "
            "attribution (which 4 KB region of the image owns a feature) is reliable; "
            "site-level attribution is a hypothesis until a transcript or a "
            "disassembly confirms it.  Record the date of that confirmation next to "
            "the claim."
        ),
    )
    ap.add_argument("-t", "--target", required=True, help="path to target.toml")
    ap.add_argument("--binary", help="override target.toml's 'binary'")
    ap.add_argument("--strings", help="offset<->text listing, e.g. the output of strings_dump.py")
    ap.add_argument("--string-fo", action="append", default=[], metavar="FO[=LABEL]",
                    help="a single string file offset, optionally labelled. Repeatable.")
    ap.add_argument("--flush", required=True,
                    help="file offset of the serializer flush routine (same value you gave "
                         "extract_packets.py)")
    ap.add_argument("--window", default="0x600",
                    help="how far from a literal-pool word to look for a flush site, in bytes "
                         "each way (default 0x600)")
    ap.add_argument("--guest-addrs", action="store_true",
                    help="interpret address arguments as guest addresses, not file offsets")
    ap.add_argument("--max-slots", type=int, default=8,
                    help="stop after this many .data slots per string (default 8)")
    ap.add_argument("--max-literals", type=int, default=16,
                    help="stop after this many literal-pool sites per slot (default 16)")
    ap.add_argument("--cluster-bits", type=int, default=12,
                    help="cluster granularity in bits; 12 = 4 KB regions (default 12)")
    ap.add_argument("--out", help="also write the report here")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args(argv)

    tgt = load_target(args.target)
    binpath = resolve_binary(tgt, args.target, args.binary)
    image_base = int(cfg(tgt, "image_base"))
    sl_fo = int(cfg(tgt, "sl_fo"))
    reloc_delta = int(cfg(tgt, "reloc_delta"))
    reloc_unit = str(cfg(tgt, "reloc_unit")).strip().lower()
    encoding = str(cfg(tgt, "encoding", "utf-8"))

    if reloc_unit not in ("file_offset", "guest_address"):
        sys.exit(
            "error: reloc_unit = %r in target.toml.\n"
            "       It must be exactly \"file_offset\" or \"guest_address\".\n"
            "       Get this wrong and every row this tool prints is wrong."
            % reloc_unit
        )

    with open(binpath, "rb") as fh:
        data = fh.read()

    _word_for = getattr(tgt, "reloc_word_for_fo", None)

    def stored_word_for_fo(fo):
        # Prefer target.py's own arithmetic when it exposes it, so that the two
        # can never drift apart; fall back to the documented formula otherwise.
        if callable(_word_for):
            try:
                return int(_word_for(fo)) & 0xFFFFFFFF
            except Exception:
                pass
        if reloc_unit == "file_offset":
            return (fo - reloc_delta) & 0xFFFFFFFF
        return (fo + image_base - reloc_delta) & 0xFFFFFFFF

    # Cross-check against target.py's own deref, if it exposes one.  A mismatch
    # here is the single highest-cost misconfiguration in the whole toolchain.
    deref = getattr(tgt, "deref_reloc", None)
    if callable(deref):
        probe_fo = min(len(data) - 1, max(0x1000, sl_fo // 2))
        try:
            back = deref(stored_word_for_fo(probe_fo))
            if back is not None and int(back) != probe_fo:
                print(
                    "warning: target.deref_reloc() disagrees with reloc_unit=%s\n"
                    "         probe fo 0x%x -> word 0x%x -> deref 0x%x\n"
                    "         One of reloc_delta / reloc_unit is wrong. Re-run solve_reloc.py\n"
                    "         with --validate before trusting anything below."
                    % (reloc_unit, probe_fo, stored_word_for_fo(probe_fo), int(back)),
                    file=sys.stderr,
                )
        except Exception:
            pass

    flush_fo = parse_addr(args.flush)
    if args.guest_addrs:
        flush_fo -= image_base
    if not (0 <= flush_fo < len(data)):
        sys.exit("error: --flush 0x%x is outside the image (%d bytes). Did you mean "
                 "--guest-addrs?" % (flush_fo, len(data)))

    window = parse_addr(args.window)

    targets = []
    if args.strings:
        targets.extend((fo, label or "") for fo, label, _ln in read_string_list(args.strings, encoding))
    for spec in args.string_fo:
        if "=" in spec:
            fo_text, label = spec.split("=", 1)
        else:
            fo_text, label = spec, ""
        fo = parse_addr(fo_text)
        if args.guest_addrs:
            fo -= image_base
        targets.append((fo, label))
    if not targets:
        sys.exit("error: give --strings FILE and/or one or more --string-fo.")

    sites = scan_flush_sites(data, flush_fo)
    if not sites:
        sys.exit(
            "error: no BL targets 0x%x, so no send sites exist to name.\n"
            "       Confirm the flush address with xref.py calls 0x%x." % (flush_fo, flush_fo)
        )
    sites.sort()

    def nearest_sites(pos):
        out = []
        for s in sites:
            d = s - pos
            if -window <= d <= window:
                out.append((abs(d), s))
        out.sort()
        return out

    results = []
    for fo, label in targets:
        row = {
            "string_fo": fo,
            "string_guest": fo + image_base,
            "label": label,
            "stored_word": stored_word_for_fo(fo),
            "slots": [],
            "confidence": "MEDIUM",
        }
        word = row["stored_word"]
        slots = find_words(data, word)[: args.max_slots]
        if not slots:
            row["note"] = ("no .data slot holds this string's relocated word. "
                           "The string is probably reached through a computed pointer, "
                           "or reloc_delta/reloc_unit is wrong.")
        for slot_fo in slots:
            sl_off = slot_fo - sl_fo
            lits = find_words(data, sl_off & 0xFFFFFFFF)[: args.max_literals]
            slot_entry = {
                "slot_fo": slot_fo,
                "sl_off": sl_off,
                "literals": [],
            }
            for lit_fo in lits:
                cands = nearest_sites(lit_fo)
                slot_entry["literals"].append({
                    "literal_fo": lit_fo,
                    "candidates": [
                        {"site_fo": s,
                         "site_guest": s + image_base,
                         "distance": d,
                         "cluster": s >> args.cluster_bits << args.cluster_bits}
                        for d, s in cands
                    ],
                })
            slot_entry["literals"] = [x for x in slot_entry["literals"] if x["candidates"]] \
                or slot_entry["literals"]
            row["slots"].append(slot_entry)
        results.append(row)

    # cluster tally across everything, because cluster attribution is the reliable part
    clusters = {}
    for row in results:
        for slot in row["slots"]:
            for lit in slot["literals"]:
                for c in lit["candidates"]:
                    clusters.setdefault(c["cluster"], set()).add(row["label"] or ("0x%x" % row["string_fo"]))

    if args.json:
        doc = {
            "binary": binpath,
            "image_base": "0x%x" % image_base,
            "sl_fo": "0x%x" % sl_fo,
            "reloc_delta": "0x%x" % reloc_delta,
            "reloc_unit": reloc_unit,
            "flush_fo": "0x%x" % flush_fo,
            "window": "0x%x" % window,
            "flush_site_count": len(sites),
            "confidence": "MEDIUM (every row; see --help)",
            "rows": [
                {
                    "string_fo": "0x%x" % r["string_fo"],
                    "string_guest": "0x%x" % r["string_guest"],
                    "label": r["label"],
                    "stored_word": "0x%x" % r["stored_word"],
                    "note": r.get("note", ""),
                    "slots": [
                        {
                            "slot_fo": "0x%x" % s["slot_fo"],
                            "sl_off": "0x%x" % (s["sl_off"] & 0xFFFFFFFF),
                            "literals": [
                                {
                                    "literal_fo": "0x%x" % l["literal_fo"],
                                    "candidates": [
                                        {"site_fo": "0x%x" % c["site_fo"],
                                         "site_guest": "0x%x" % c["site_guest"],
                                         "distance": c["distance"],
                                         "cluster": "0x%x" % c["cluster"]}
                                        for c in l["candidates"]
                                    ],
                                }
                                for l in s["literals"]
                            ],
                        }
                        for s in r["slots"]
                    ],
                }
                for r in results
            ],
            "clusters": {("0x%x" % k): sorted(v) for k, v in sorted(clusters.items())},
        }
        text = json.dumps(doc, indent=2, ensure_ascii=False)
    else:
        L = []
        L.append("# UI string -> send site   (CONFIDENCE: MEDIUM on every row)")
        L.append("# binary      : %s" % binpath)
        L.append("# sl_fo       : 0x%x     reloc_delta 0x%x  (%s)" % (sl_fo, reloc_delta, reloc_unit))
        L.append("# flush       : fo 0x%x  -> %d send sites in the image" % (flush_fo, len(sites)))
        L.append("# window      : +/- 0x%x bytes around each literal-pool word" % window)
        L.append("#")
        L.append("# chain: string fo -> stored word -> .data slot -> sl offset -> literal -> flush")
        L.append("")
        for r in results:
            head = "0x%-8x %s" % (r["string_fo"], r["label"])
            L.append("-" * 72)
            L.append(head.rstrip())
            L.append("  stored word 0x%08x" % r["stored_word"])
            if r.get("note"):
                L.append("  ! %s" % r["note"])
            for s in r["slots"]:
                L.append("  slot fo 0x%-8x  sl+0x%x" % (s["slot_fo"], s["sl_off"] & 0xFFFFFFFF))
                for l in s["literals"]:
                    if not l["candidates"]:
                        L.append("    literal fo 0x%-8x  (no flush site within the window)"
                                 % l["literal_fo"])
                        continue
                    L.append("    literal fo 0x%-8x" % l["literal_fo"])
                    for c in l["candidates"]:
                        d = c["site_fo"] - l["literal_fo"]
                        L.append("      -> send site fo 0x%-8x  guest 0x%-8x  d=%s0x%-5x  cluster 0x%x  [MEDIUM]"
                                 % (c["site_fo"], c["site_guest"],
                                    "-" if d < 0 else "+", abs(d), c["cluster"]))
            L.append("")
        if clusters:
            L.append("=" * 72)
            L.append("cluster tally -- THIS is the part you can rely on")
            L.append("=" * 72)
            for c in sorted(clusters):
                L.append("  cluster 0x%-8x : %s" % (c, ", ".join(sorted(clusters[c]))))
            L.append("")
            L.append("  A cluster that collects several strings from one feature IS that")
            L.append("  feature's code.  Which flush site inside it belongs to which string")
            L.append("  is still a hypothesis -- settle it with a device transcript.")
        text = "\n".join(L)

    print(text)
    if args.out:
        out = args.out.replace("\\", "/")
        d = os.path.dirname(out)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print("\nwrote %s" % out, file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
