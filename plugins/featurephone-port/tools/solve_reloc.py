#!/usr/bin/env python3
"""solve_reloc.py - recover the relocation delta of a position-independent guest image.

THE PROBLEM
    In an RWPI (read-write position independent) ARM image there are no
    absolute data addresses in the instruction stream, and the pointers stored
    IN the image are biased: the word that references a string at file offset F
    holds `F - delta`, not F. Until you know `delta`, every pointer in the
    binary is meaningless and every other tool in this directory lies.

METHOD (A) - solve, then VALIDATE on a corpus
    Solve:
      1. Pick >= 5 strings you can recognise at known file offsets. IP and URL
         literals, printf format strings, error text and database record names
         are the easiest to recognise and the most likely to be referenced.
      2. Build S = the set of every word-aligned 32-bit word in the image.
      3. For each known target t, form D_t = { t - v : v in S }.
      4. Intersect the D_t. Anything that survives explains every known string
         at once.
      5. Rank near-misses by hit count too (`--near-miss`), because one of your
         "known" strings is usually reached through a different mechanism (a
         packed string table, a computed offset) and would otherwise veto the
         right answer.

    Validate (this is the half people skip, and it is the half that matters):
      6. Extract EVERY plausible NUL-terminated ASCII string in the image.
      7. For each candidate delta, count what fraction of that corpus is
         actually referenced by some stored word.
      8. Compare against controls - delta 0 and three arbitrary deltas. A
         correct delta explains a large share of the corpus; a coincidence
         explains about as much as a random number does.
    Five hand-picked strings agreeing is not evidence. "3184 of 4021 strings
    are referenced under this delta, versus 11 under a random one" is.

THE UNIT TRAP - read this before you copy a number
    `delta` and `reloc_unit` are ONE ANSWER IN TWO PARTS, and the two spellings
    below are the SAME relocation:

        reloc_delta = D            reloc_unit = "file_offset"
        reloc_delta = D + base     reloc_unit = "guest_address"

    because a tool computes `fo = word + delta - (image_base if unit ==
    "guest_address" else 0)`. Copy a WHOLE ROW into target.toml. If you take
    the delta from one row and the unit from the other, every pointer resolves
    exactly image_base bytes away from the truth, no tool errors out, and you
    spend an afternoon reading plausible garbage. This tool always prints both
    spellings of every candidate for exactly that reason.

KNOWN LIMITS
    * Word-aligned words only by default. `--unaligned` widens the search and
      is much slower; you should not need it - literal pools are aligned.
    * `--max-abs-delta` bounds the search band around 0, +image_base and
      -image_base. Widening it costs memory, and you almost never need to,
      because searching both spellings already absorbs image_base-sized shifts.
    * `--auto-known` guesses landmark strings for you. It cannot know whether a
      guessed string is referenced at all, so it always reports a RANKING, never
      an exact intersection. Treat its answer as a hypothesis to validate.
    * A title may not bias its pointers at all (delta 0). That is a valid
      answer and the corpus score will say so.

USAGE
    python solve_reloc.py -t target.toml --known 0xFO1,0xFO2,0xFO3,0xFO4,0xFO5 --validate
        (the file offsets of five strings you recognised in strings_dump.py output -
         IPs, URLs, format strings and error text are the easiest to recognise)
    python solve_reloc.py -t target.toml --auto-known 16 --validate
    python solve_reloc.py -t target.toml --validate              # score the configured delta
    python solve_reloc.py --selftest
"""

from __future__ import annotations

import argparse
import array
import json
import os
import random
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

M32 = 0xFFFFFFFF


def die(msg: str, code: int = 2):
    sys.stderr.write("solve_reloc.py: error: %s\n" % msg)
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


def signed(d):
    return d - (1 << 32) if d >= (1 << 31) else d


# --------------------------------------------------------------------------
def word_set(data, unaligned=False):
    if unaligned:
        return {int.from_bytes(data[i:i + 4], "little")
                for i in range(0, len(data) - 3)}
    a = array.array("I")
    if a.itemsize != 4:  # pragma: no cover
        a = array.array("L")
    a.frombytes(bytes(data[: len(data) & ~3]))
    if sys.byteorder == "big":
        a.byteswap()
    return set(a)


def in_band(sd, image_base, max_abs):
    """Keep deltas near 0, near +image_base or near -image_base."""
    if max_abs <= 0:
        return True
    return min(abs(sd), abs(sd - image_base), abs(sd + image_base)) <= max_abs


def deltas_for(S, tval, image_base, max_abs):
    out = set()
    for v in S:
        d = (tval - v) & M32
        if in_band(signed(d), image_base, max_abs):
            out.add(d)
    return out


def solve(S, targets, image_base, max_abs, want_near_miss):
    """Return (exact_sorted, near_miss_rows). Targets are FILE OFFSETS."""
    per = [deltas_for(S, t, image_base, max_abs) for t in targets]
    exact = set(per[0]) if per else set()
    for d in per[1:]:
        exact &= d
    rows = []
    if want_near_miss and per:
        seen, dup = set(), set()
        for d in per:
            dup |= seen & d
            seen |= d
        counts = {}
        for d in per:
            for x in d & dup:
                counts[x] = counts.get(x, 0) + 1
        rows = sorted(counts.items(),
                      key=lambda kv: (-kv[1], abs(signed(kv[0]))))
    return sorted(exact, key=lambda d: abs(signed(d))), rows


ASCIIZ_RE_TMPL = rb"(?<![\x20-\x7e])[\x20-\x7e]{%d,}\x00"


def asciiz_corpus(data, min_len=6):
    rx = re.compile(ASCIIZ_RE_TMPL % int(min_len))
    return [m.start() for m in rx.finditer(data)]


def score(S, corpus, delta):
    d = delta & M32
    hits = 0
    for s in corpus:
        if ((s - d) & M32) in S:
            hits += 1
    return hits


def both_spellings(delta_fo, image_base):
    return {
        "file_offset": delta_fo & M32,
        "guest_address": (delta_fo + image_base) & M32,
    }


def fmt_row(delta_fo, image_base):
    sp = both_spellings(delta_fo, image_base)
    return ("reloc_delta = 0x%x / reloc_unit = \"file_offset\"   ==   "
            "reloc_delta = 0x%x / reloc_unit = \"guest_address\""
            % (sp["file_offset"], sp["guest_address"]))


# --------------------------------------------------------------------------
def pick_auto_known(data, count, min_len=10):
    """Evenly sampled long asciiz strings, as landmark guesses."""
    corpus = [s for s in asciiz_corpus(data, min_len)]
    if not corpus:
        return []
    if len(corpus) <= count:
        return corpus
    step = len(corpus) / float(count)
    return [corpus[int(i * step)] for i in range(count)]


def run(tgt, data, a):
    image_base = int(tget(tgt, "image_base", 0))
    cfg_delta, cfg_unit, cfg_declared = reloc_cfg(tgt)
    cfg_delta_fo = cfg_delta - (image_base if cfg_unit == "guest_address" else 0)

    out = {"image_len": len(data), "image_base": image_base,
           "configured": {"reloc_delta": cfg_delta, "reloc_unit": cfg_unit,
                          "declared": cfg_declared,
                          "delta_in_file_offset_space": cfg_delta_fo & M32}}
    lines = []

    def say(s=""):
        lines.append(s)

    S = word_set(data, a.unaligned)
    say("; image 0x%x bytes, %d distinct %sword values"
        % (len(data), len(S), "" if not a.unaligned else "unaligned "))

    targets = []
    auto = False
    if a.known:
        for part in str(a.known).replace(" ", "").split(","):
            if part:
                targets.append(parse_int(part, "known offset"))
    elif a.auto_known:
        targets = pick_auto_known(data, a.auto_known)
        auto = True
        say("; --auto-known picked %d landmark strings; the result is a "
            "RANKING, not a proof" % len(targets))

    for t in targets:
        if not (0 <= t < len(data)):
            die("known offset 0x%x is outside the image (0..0x%x)" % (t, len(data)))
    out["known"] = targets

    exact, near = [], []
    if targets:
        if len(targets) < 5 and not auto:
            say("; WARNING: only %d known string(s). Use at least 5, or the "
                "intersection will admit coincidences." % len(targets))
        exact, near = solve(S, targets, image_base, a.max_abs_delta,
                            a.near_miss or auto)
        say("")
        say("=== candidate deltas that explain ALL %d known strings ===" % len(targets))
        if exact:
            for d in exact[: a.top]:
                say("  %s" % fmt_row(d, image_base))
        else:
            say("  (none) - either a known offset is wrong, or one of your "
                "strings is not referenced by a stored pointer at all.")
            say("  Re-run with --near-miss.")
        out["exact"] = [{"delta_file_offset": d,
                         "spellings": both_spellings(d, image_base)} for d in exact]
        if near:
            say("")
            say("=== near misses (delta -> how many of the %d knowns it explains) ==="
                % len(targets))
            for d, n in near[: a.top]:
                say("  %2d/%d  %s" % (n, len(targets), fmt_row(d, image_base)))
            out["near_miss"] = [{"delta_file_offset": d, "hits": n,
                                 "of": len(targets)} for d, n in near[: a.top]]
    elif not a.validate:
        die("give --known 0x..,0x.. (>= 5 offsets), or --auto-known N, "
            "or --validate to score the delta already in target.toml")

    if a.validate:
        corpus = asciiz_corpus(data, a.corpus_min)
        say("")
        say("=== corpus validation: %d NUL-terminated ASCII strings of >= %d chars ==="
            % (len(corpus), a.corpus_min))
        if not corpus:
            say("  (no corpus) - this image may store all its text double-byte; "
                "lower --corpus-min or validate against a hand-built list.")
        cands = []
        for d in exact[: a.top]:
            cands.append((d, "solved"))
        for d, _n in near[: a.top]:
            if all(d != c for c, _l in cands):
                cands.append((d, "near-miss"))
        if all(cfg_delta_fo & M32 != c for c, _l in cands):
            cands.append((cfg_delta_fo & M32, "configured"))
        cands.append((0, "control: no bias"))
        rnd = random.Random(20240101)
        for _ in range(3):
            cands.append((rnd.randrange(0x1000, 0x40000), "control: random"))

        say("  %-12s  %-18s  %s" % ("hits/total", "fraction", "delta (file-offset space) / label"))
        rows = []
        for d, label in cands:
            h = score(S, corpus, d)
            frac = (100.0 * h / len(corpus)) if corpus else 0.0
            rows.append({"delta_file_offset": d & M32, "label": label,
                         "hits": h, "total": len(corpus), "percent": round(frac, 2),
                         "spellings": both_spellings(d, image_base)})
            say("  %-12s  %-18s  0x%08x  %s"
                % ("%d/%d" % (h, len(corpus)), "%.2f%%" % frac, d & M32, label))
        out["validation"] = rows
        best = max((r for r in rows if not r["label"].startswith("control")),
                   key=lambda r: r["hits"], default=None)
        ctrl = max((r["hits"] for r in rows if r["label"].startswith("control")),
                   default=0)
        if best and corpus:
            say("")
            if best["hits"] > max(4 * ctrl, 0.10 * len(corpus)):
                say("VERDICT: adopt this pair. Copy ONE WHOLE ROW into target.toml:")
                say("  reloc_delta = 0x%x" % best["spellings"]["file_offset"])
                say('  reloc_unit  = "file_offset"')
                say("  (identical alternative: reloc_delta = 0x%x with "
                    'reloc_unit = "guest_address")'
                    % best["spellings"]["guest_address"])
                out["verdict"] = {"adopt": True,
                                  "reloc_delta": best["spellings"]["file_offset"],
                                  "reloc_unit": "file_offset"}
            else:
                say("VERDICT: NOT established. The best candidate (%d/%d) is not "
                    "clearly above the control floor (%d). Add more known "
                    "strings, or this image may not bias its pointers the way "
                    "this model assumes." % (best["hits"], len(corpus), ctrl))
                out["verdict"] = {"adopt": False}

    say("")
    say("!! reloc_delta and reloc_unit are ONE ANSWER IN TWO PARTS. The two")
    say("!! spellings printed on each row are the same relocation. Mixing the")
    say("!! delta from one spelling with the unit from the other shifts every")
    say("!! resolved pointer by image_base (0x%x) and NOTHING WILL ERROR." % image_base)
    return out, lines


# --------------------------------------------------------------------------
def selftest():
    fails = []

    def check(name, cond):
        if not cond:
            fails.append(name)

    BASE = 0x100000
    DELTA = 0x7A4
    img = bytearray(0x2000)
    strings = {}
    fo = 0x800
    for i, s in enumerate([b"NETWORK", b"BILLSOCK", b"%d.%d.%d.%d",
                           b"readSocket_CB_SET_ERROR", b"MSG_disconnect",
                           b"/comm/ui_bm", b"SaveRecord_%d"]):
        img[fo] = 0
        img[fo + 1 : fo + 1 + len(s)] = s
        img[fo + 1 + len(s)] = 0
        strings[i] = fo + 1
        fo += len(s) + 8
    # a literal pool of biased pointers, word aligned
    pool = 0x100
    for i in sorted(strings):
        img[pool:pool + 4] = ((strings[i] - DELTA) & M32).to_bytes(4, "little")
        pool += 4
    data = bytes(img)

    S = word_set(data)
    known = [strings[i] for i in sorted(strings)][:5]
    exact, near = solve(S, known, BASE, 0x40000, True)
    check("exact recovers delta", DELTA in exact)
    check("exact is unique-ish", len(exact) <= 3)

    corpus = asciiz_corpus(data, 6)
    check("corpus found the strings", len(corpus) >= 6)
    good = score(S, corpus, DELTA)
    bad = score(S, corpus, 0x12345)
    check("corpus score separates", good >= 6 and bad == 0)

    # the two spellings are the same relocation
    sp = both_spellings(DELTA, BASE)
    check("spelling equivalence", sp["guest_address"] == DELTA + BASE)

    def resolve(word, delta, unit):
        return ((word + delta) & M32) - (BASE if unit == "guest_address" else 0)

    w = (strings[0] - DELTA) & M32
    check("file_offset spelling resolves",
          resolve(w, sp["file_offset"], "file_offset") == strings[0])
    check("guest_address spelling resolves",
          resolve(w, sp["guest_address"], "guest_address") == strings[0])
    check("mixing halves is off by image_base",
          resolve(w, sp["file_offset"], "guest_address") == strings[0] - BASE)

    # a guest-address-biased image: words hold (guest - DELTA)
    img2 = bytearray(img)
    pool = 0x100
    for i in sorted(strings):
        img2[pool:pool + 4] = ((strings[i] + BASE - DELTA) & M32).to_bytes(4, "little")
        pool += 4
    S2 = word_set(bytes(img2))
    exact2, _ = solve(S2, known, BASE, 0x40000, False)
    # the SAME image is described by delta DELTA-BASE in file-offset space and
    # by delta DELTA in guest-address space; both spellings must be produced
    check("guest-biased image solves in file-offset space",
          ((DELTA - BASE) & M32) in exact2)
    check("guest-biased image reports the small guest spelling",
          both_spellings(((DELTA - BASE) & M32), BASE)["guest_address"] == DELTA)

    # near-miss survives one bogus known
    bogus = known[:4] + [0x7F0]
    exact3, near3 = solve(S, bogus, BASE, 0x40000, True)
    check("bogus known kills the exact set", DELTA not in exact3)
    check("near-miss still ranks the truth first",
          near3 and near3[0][0] == DELTA and near3[0][1] == 4)

    check("band filter", in_band(0x7A4, BASE, 0x40000))
    check("band filter rejects", not in_band(0x900000, BASE, 0x40000))
    check("band filter accepts near -image_base",
          in_band(-(BASE + 0x100), BASE, 0x40000))

    if fails:
        for f in fails:
            print("FAIL %s" % f)
        print("selftest: %d failure(s)" % len(fails))
        return 1
    print("selftest: ok (15 checks)")
    return 0


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        prog="solve_reloc.py",
        description="Recover the relocation delta of a position-independent "
                    "guest image by intersection over known strings, then "
                    "validate it corpus-wide against controls.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="reloc_delta and reloc_unit are one answer in two parts - copy a "
               "whole row into target.toml, never half of one.",
    )
    ap.add_argument("-t", "--target", help="path to target.toml")
    ap.add_argument("--known",
                    help="comma-separated FILE OFFSETS of strings you can "
                         "recognise (>= 5 recommended)")
    ap.add_argument("--auto-known", type=int, metavar="N",
                    help="guess N landmark strings instead of naming them; "
                         "always reports a ranking, never a proof")
    ap.add_argument("--validate", action="store_true",
                    help="score candidates over every asciiz string in the "
                         "image, against a no-bias and three random controls")
    ap.add_argument("--near-miss", action="store_true",
                    help="also rank deltas that explain only some of the knowns")
    ap.add_argument("--top", type=int, default=10, help="rows to print (default 10)")
    ap.add_argument("--corpus-min", type=int, default=6,
                    help="minimum asciiz length for the validation corpus "
                         "(default 6)")
    ap.add_argument("--max-abs-delta", type=lambda s: parse_int(s, "--max-abs-delta"),
                    default=0x40000,
                    help="search band around 0, +image_base and -image_base "
                         "(default 0x40000; 0 = unlimited, costs memory)")
    ap.add_argument("--unaligned", action="store_true",
                    help="consider words at every byte offset (slow; literal "
                         "pools are aligned, so you should not need this)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--out", help="write the report to this file (utf-8)")
    ap.add_argument("--binary", help="override target.toml `binary`")
    ap.add_argument("--selftest", action="store_true",
                    help="run the built-in solver checks and exit")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()
    if not a.target:
        ap.error("-t/--target is required (or use --selftest)")
    if load_target is None:
        die("target.py must sit next to this tool (it loads target.toml)")

    tgt = load_target(a.target)
    data = read_image(tgt, a.target, a.binary)
    obj, lines = run(tgt, data, a)

    text = ("\n".join(lines) if not a.json
            else json.dumps(obj, indent=2, sort_keys=True))
    if a.out:
        with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text + "\n")
        sys.stderr.write("solve_reloc.py: wrote %s\n" % a.out.replace("\\", "/"))
    else:
        print(text)
    v = obj.get("verdict")
    if v is not None and not v.get("adopt"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
