#!/usr/bin/env python3
"""strings_dump.py - offset <-> text listing for a guest image, in the game's own encoding.

WHAT IT DOES
    Sweeps a byte range of the guest image for runs of text in a legacy
    multi-byte encoding and prints, for every run:

        file_offset   guest_address   reloc_word   "the text"

    The `reloc_word` column is the value you search for NEXT: in a
    position-independent image, a pointer to a string is stored biased, so the
    word that references file offset F is `F - reloc_delta` (or
    `F + image_base - reloc_delta`, depending on `reloc_unit`). Printing it here
    saves the arithmetic step that people get wrong, and feeds directly into
    `xref.py literal <reloc_word>` and `bind_strings.py`.

METHOD (A)
    Do not use `strings(1)`. A legacy Korean/Japanese/Chinese game binary is
    mostly double-byte text that `strings` either skips or mangles. Instead,
    match a byte-class regex built for the target encoding, then decode the run
    with the real codec and drop anything the codec rejects:

        euc-kr   (?:[\\xa1-\\xfe][\\xa1-\\xfe]|[\\x20-\\x7e])+
        sjis     lead [\\x81-\\x9f\\xe0-\\xef] + trail, halfwidth kana, ascii
        gbk      lead [\\x81-\\xfe] + trail [\\x40-\\x7e\\x80-\\xfe], ascii
        big5     lead [\\xa1-\\xf9] + trail [\\x40-\\x7e\\xa1-\\xfe], ascii
        cp1252   [\\x20-\\x7e\\xa0-\\xff]+
        utf8     a real UTF-8 run matcher
        ascii    [\\x20-\\x7e]+

    Building the string index is the porter's first hour, and it is the single
    highest-yield thing you can do to an unknown binary: error text, IP/URL
    literals, format strings, database record names and UI messages are all
    named landmarks in an image that otherwise has no symbols. `--grep` turns
    the listing into "where is this exact phrase", whose answer is the input to
    the string -> code chain.

    `--asciiz-only` restricts output to NUL-terminated runs that start after a
    non-printable byte. That is the corpus definition solve_reloc.py uses when
    it validates a relocation delta over the whole image, so use the same flag
    when you want the two tools to agree.

KNOWN LIMITS
    * Byte-class matching over-accepts: binary data that happens to fall in the
      lead/trail bands decodes to plausible junk. Raise --min-len, and treat a
      run with no NUL terminator as suspect.
    * Text stored in a packed runtime string table (a length/count/offset
      header followed by blobs) will NOT appear as separate runs. Decode that
      table with its own parser; see the guest-re skill's asset-decoding
      reference.
    * Encrypted resources are out of scope. If a member of the archive is
      high-entropy, this tool will find nothing there and that is the correct
      answer - do not go looking for a key to put in a tool.

USAGE
    python strings_dump.py -t target.toml --range 0xRODATA_START:0xRODATA_END
    python strings_dump.py -t target.toml --grep "NETWORK" --min-len 3
    python strings_dump.py -t target.toml --asciiz-only --out strings.txt
    python strings_dump.py --selftest
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


def die(msg: str, code: int = 2):
    sys.stderr.write("strings_dump.py: error: %s\n" % msg)
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


# --------------------------------------------------------------------------
ENC_ALIAS = {
    "euc-kr": "euc_kr", "euckr": "euc_kr", "euc_kr": "euc_kr", "cp949": "euc_kr",
    "sjis": "sjis", "shift-jis": "sjis", "shift_jis": "sjis", "cp932": "sjis",
    "gbk": "gbk", "cp936": "gbk",
    "big5": "big5", "cp950": "big5",
    "cp1252": "cp1252", "latin1": "cp1252", "windows-1252": "cp1252",
    "utf8": "utf8", "utf-8": "utf8",
    "ascii": "ascii", "us-ascii": "ascii",
}

CODEC = {
    "euc_kr": "cp949",      # a superset of euc-kr; decodes everything euc-kr does
    "sjis": "cp932",
    "gbk": "gbk",
    "big5": "big5",
    "cp1252": "cp1252",
    "utf8": "utf-8",
    "ascii": "ascii",
}

PATTERN = {
    "euc_kr": rb"(?:[\xa1-\xfe][\xa1-\xfe]|[\x20-\x7e])+",
    "sjis": rb"(?:[\x81-\x9f\xe0-\xef][\x40-\x7e\x80-\xfc]|[\xa1-\xdf]|[\x20-\x7e])+",
    "gbk": rb"(?:[\x81-\xfe][\x40-\x7e\x80-\xfe]|[\x20-\x7e])+",
    "big5": rb"(?:[\xa1-\xf9][\x40-\x7e\xa1-\xfe]|[\x20-\x7e])+",
    "cp1252": rb"[\x20-\x7e\xa0-\xff]+",
    "utf8": (rb"(?:[\x20-\x7e]|[\xc2-\xdf][\x80-\xbf]|\xe0[\xa0-\xbf][\x80-\xbf]"
             rb"|[\xe1-\xec][\x80-\xbf]{2}|\xed[\x80-\x9f][\x80-\xbf]"
             rb"|[\xee-\xef][\x80-\xbf]{2})+"),
    "ascii": rb"[\x20-\x7e]+",
}


def norm_encoding(name):
    key = str(name or "ascii").strip().lower()
    if key not in ENC_ALIAS:
        die("unknown encoding %r. Known: %s"
            % (name, ", ".join(sorted(set(ENC_ALIAS)))))
    return ENC_ALIAS[key]


def reloc_word_for(tgt, fo):
    """The word a pointer to `fo` is stored as, honouring reloc_unit."""
    delta, unit, _declared = reloc_cfg(tgt)
    v = fo + (int(tget(tgt, "image_base", 0)) if unit == "guest_address" else 0)
    return (v - delta) & 0xFFFFFFFF


def find_runs(data, enc, lo, hi, min_len, asciiz_only):
    """Yield (fo, text, terminated) for every decodable run in [lo, hi)."""
    rx = re.compile(PATTERN[enc])
    codec = CODEC[enc]
    for m in rx.finditer(data, lo, hi):
        start, end = m.start(), m.end()
        terminated = end < len(data) and data[end] == 0
        if asciiz_only:
            if not terminated:
                continue
            if start > 0 and 0x20 <= data[start - 1] <= 0x7E:
                continue
        raw = m.group(0)
        try:
            text = raw.decode(codec)
        except Exception:
            continue
        if len(text) < min_len:
            continue
        yield start, text, terminated


def esc(s):
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "\\t")


def selftest():
    fails = []

    def check(name, cond):
        if not cond:
            fails.append(name)

    check("alias euc-kr", norm_encoding("euc-kr") == "euc_kr")
    check("alias SJIS", norm_encoding("SJIS") == "sjis")

    blob = bytearray(0x100)
    blob[0x10:0x1B] = b"NETWORK_UP\x00"
    blob[0x30:0x3C] = b"%d/%d items\x00"
    hangul = "\uac00\ub098\ub2e4".encode("cp949")   # 3 double-byte syllables
    blob[0x50 : 0x50 + len(hangul)] = hangul
    blob[0x50 + len(hangul)] = 0
    data = bytes(blob)

    runs = list(find_runs(data, "euc_kr", 0, len(data), 3, False))
    got = {fo: t for fo, t, _ in runs}
    check("ascii run", got.get(0x10) == "NETWORK_UP")
    check("format run", got.get(0x30) == "%d/%d items")
    check("double-byte run", got.get(0x50) == "\uac00\ub098\ub2e4")
    check("terminated flag", all(term for _fo, _t, term in runs))
    # min-len counts DECODED CHARACTERS, so a 6-byte 3-syllable run fails at 4
    check("min-len counts characters",
          not any(fo == 0x50 for fo, _t, _x in
                  find_runs(data, "euc_kr", 0, len(data), 4, False)))

    check("min-len filters",
          not any(fo == 0x10 for fo, _t, _x in
                  find_runs(data, "euc_kr", 0, len(data), 40, False)))
    check("range filters",
          [fo for fo, _t, _x in find_runs(data, "euc_kr", 0x30, 0x40, 4, False)] == [0x30])

    # asciiz-only rejects a run that starts mid-string
    check("asciiz start rule",
          any(fo == 0x10 for fo, _t, _x in find_runs(data, "euc_kr", 0, len(data), 4, True)))
    check("asciiz mid rejected",
          not any(fo == 0x12 for fo, _t, _x in find_runs(data, "euc_kr", 0x12, len(data), 4, True)))

    class T(dict):
        pass

    t_fo = T(image_base=0x100000, reloc_delta=0x800, reloc_unit="file_offset")
    t_g = T(image_base=0x100000, reloc_delta=0x800, reloc_unit="guest_address")
    check("reloc word fo", reloc_word_for(t_fo, 0xAC000) == 0xAB800)
    check("reloc word guest", reloc_word_for(t_g, 0xAC000) == 0x1AB800)

    utf = "caf\u00e9 \u00fcber".encode("utf-8")
    d2 = b"\x00" * 4 + utf + b"\x00"
    check("utf8 run",
          [t for _fo, t, _x in find_runs(d2, "utf8", 0, len(d2), 4, False)]
          == ["caf\u00e9 \u00fcber"])

    if fails:
        for f in fails:
            print("FAIL %s" % f)
        print("selftest: %d failure(s)" % len(fails))
        return 1
    print("selftest: ok (13 checks)")
    return 0


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        prog="strings_dump.py",
        description="Offset <-> text listing of a guest image in the game's own "
                    "encoding, with the relocation word you would search for next.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Ranges accept 0x1234, g:0xGUEST and sl+0xOFF on both sides of "
               "the colon, e.g. --range 0xa9000:0xb0000 or --range sl+0:sl+0x800.",
    )
    ap.add_argument("-t", "--target", help="path to target.toml")
    ap.add_argument("--range", dest="rng",
                    help="A:B file-offset range (default: the whole image)")
    ap.add_argument("--encoding",
                    help="override target.toml `encoding` "
                         "(euc-kr|sjis|gbk|big5|cp1252|utf8|ascii)")
    ap.add_argument("--min-len", type=int, default=4,
                    help="minimum DECODED CHARACTER count, not bytes: one "
                         "double-byte syllable counts as one (default 4, so a "
                         "3-syllable Korean word is dropped - lower it to 2 "
                         "when hunting short UI labels)")
    ap.add_argument("--grep",
                    help="only print runs containing this substring "
                         "(case-insensitive)")
    ap.add_argument("--asciiz-only", action="store_true",
                    help="only NUL-terminated runs that start after a "
                         "non-printable byte (the corpus solve_reloc.py uses)")
    ap.add_argument("--out", help="write the listing to this file (utf-8)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--binary", help="override target.toml `binary`")
    ap.add_argument("--selftest", action="store_true",
                    help="run the built-in encoding checks and exit")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()
    if not a.target:
        ap.error("-t/--target is required (or use --selftest)")
    if load_target is None:
        die("target.py must sit next to this tool (it loads target.toml)")

    tgt = load_target(a.target)
    data = read_image(tgt, a.target, a.binary)
    enc = norm_encoding(a.encoding or tget(tgt, "encoding", "ascii"))
    base = int(tget(tgt, "image_base", 0))

    lo, hi = 0, len(data)
    if a.rng:
        if ":" not in a.rng:
            die("--range must be A:B")
        ls, hs = a.rng.split(":", 1)
        lo = parse_fo(tgt, ls, "range start") if ls.strip() else 0
        hi = parse_fo(tgt, hs, "range end") if hs.strip() else len(data)
    lo = max(0, lo)
    hi = min(len(data), hi)
    if hi <= lo:
        die("empty range 0x%x:0x%x" % (lo, hi))

    needle = a.grep.lower() if a.grep else None
    rows = []
    for fo, text, term in find_runs(data, enc, lo, hi, a.min_len, a.asciiz_only):
        if needle and needle not in text.lower():
            continue
        rows.append({"fo": fo, "guest": fo + base, "reloc_word": reloc_word_for(tgt, fo),
                     "terminated": term, "text": text})

    if a.json:
        out = json.dumps({"encoding": enc, "range": [lo, hi], "count": len(rows),
                          "strings": rows}, indent=2, ensure_ascii=False)
        if a.out:
            with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(out + "\n")
            sys.stderr.write("strings_dump.py: wrote %s\n" % a.out.replace("\\", "/"))
        else:
            print(out)
        return 0 if rows else 1

    fh = open(a.out, "w", encoding="utf-8", newline="\n") if a.out else None
    emit = (lambda s: fh.write(s + "\n")) if fh else print
    try:
        emit("; encoding=%s  range=0x%06x:0x%06x  min-len=%d%s"
             % (enc, lo, hi, a.min_len, "  asciiz-only" if a.asciiz_only else ""))
        _d, _u, _declared = reloc_cfg(tgt)
        emit("; reloc_word = the value a pointer to this string is STORED as "
             "(word + 0x%x = %s)" % (_d, _u))
        if not _declared:
            emit("; !! target.toml declares no reloc_delta, so the reloc_word "
                 "column is just the file offset. Feed these offsets to "
                 "solve_reloc.py --known to find the real delta.")
        emit("; fo        guest     reloc_word  z  text")
        for r in rows:
            emit('%08x  %08x  %08x  %s  "%s"'
                 % (r["fo"], r["guest"], r["reloc_word"],
                    "z" if r["terminated"] else "-", esc(r["text"])))
        emit("; %d string(s)" % len(rows))
    finally:
        if fh:
            fh.close()
            sys.stderr.write("strings_dump.py: wrote %s (%d strings)\n"
                             % (a.out.replace("\\", "/"), len(rows)))
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
