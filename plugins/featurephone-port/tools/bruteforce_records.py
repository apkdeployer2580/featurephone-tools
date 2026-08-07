#!/usr/bin/env python3
"""bruteforce_records.py - find the record layout of an unknown length-prefixed data file.

WHAT IT DOES
    You have a data file out of a game jar and no format documentation.  You suspect it
    is a table of records with length-prefixed strings.  You describe the record's field
    sequence, give ranges for the two things you cannot guess - how many header bytes
    come first and how many trailing bytes each record carries - and this tool tries
    every combination and keeps only the ones that consume the file EXACTLY.

THE METHOD IT EMBODIES  (A)
    ** `consumed == filesize` is the whole point.

    A parser that stops early still prints plausible-looking text.  That is exactly how
    a wrong format survives for years: someone reads the first ten records, they look
    right, and nobody notices the stream went out of phase at record eleven.  Requiring
    the layout to land precisely on EOF is a single assertion that kills that entire
    class of mistake, and it costs nothing.  Never write a decoder for one of these
    files without it.

    The second load-bearing idea: in a length-prefixed record file with NO OFFSET TABLE,
    a string may get longer or shorter without breaking anything downstream, as long as
    the record COUNT and ORDER are preserved.  That is why editing text in these files
    is legal at all.  Confirm the absence of an offset table before you rely on it - a
    plausible-looking header that happens to contain rising 16-bit values probably IS an
    offset table, and then the rule does not hold.

    Corroboration you get for free: if the header contains an integer equal to the number
    of records you parsed, that is an independent confirmation of the whole layout, from
    a different part of the file. The tool checks for it automatically.

WHAT IT WILL NOT DO
    * It does not decrypt.  If a member is encrypted, this tool says so and stops.  It
      ships no keys, has no --key option, and there is no default.  Handle encrypted
      members privately, outside this tool - a key that decrypts copyrighted resources is
      a different category of thing from a file format.
    * It does not guess your field sequence unless you ask for --auto, and --auto only
      tries a handful of common shapes.  A real format may need a field type this tool
      does not have; then write the parser by hand and keep the EOF assertion.
    * A layout that consumes the file exactly is strong evidence, not proof. Two layouts
      can both fit. When that happens the tool reports the ambiguity instead of picking.

FIELD TYPES
    str        a length-prefixed string; the prefix width comes from --prefix
    u8 i8 u16le u16be u32le u32be i32le
    bytes:N    N raw bytes
    pad:N      N bytes, not reported

EXIT CODES
    0  the layout is determined - one candidate, or one the header corroborates, or
       several that are the same layout differing only in fixed-field byte order
    1  nothing fits, or genuinely different layouts both fit (the output says how to
       narrow it)
    2  I/O error, or the file looks encrypted/compressed
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

FIXED_TYPES: Dict[str, Tuple[int, str, bool]] = {
    # name: (width, byteorder, signed)
    "u8": (1, "little", False),
    "i8": (1, "little", True),
    "u16le": (2, "little", False),
    "u16be": (2, "big", False),
    "u32le": (4, "little", False),
    "u32be": (4, "big", False),
    "i32le": (4, "little", True),
    "i32be": (4, "big", True),
}

PREFIX_TYPES: Dict[str, Tuple[int, str]] = {
    "u8": (1, "little"),
    "u8len": (1, "little"),     # accepted spelling
    "u16le": (2, "little"),
    "u16be": (2, "big"),
}

AUTO_TEMPLATES: Sequence[Sequence[str]] = (
    ("name:str",),
    ("name:str", "value:u32le"),
    ("name:str", "value:u16le"),
    ("name:str", "text:str"),
    ("name:str", "value:u32le", "text:str"),
    ("name:str", "value:u32be", "text:str"),
    ("id:u16le", "name:str"),
)


class ParseError(Exception):
    pass


def posix(p: str) -> str:
    return str(p).replace("\\", "/")


# --------------------------------------------------------------------------------------
# field spec
# --------------------------------------------------------------------------------------

class Field:
    __slots__ = ("name", "kind", "width", "order", "signed")

    def __init__(self, name: str, kind: str, width: int = 0, order: str = "little",
                 signed: bool = False):
        self.name, self.kind, self.width = name, kind, width
        self.order, self.signed = order, signed

    def __repr__(self) -> str:
        return f"{self.name}:{self.kind}" + (f":{self.width}" if self.kind in
                                             ("bytes", "pad") else "")


def parse_field_spec(spec: str) -> Field:
    name, _, rest = spec.partition(":")
    if not name or not rest:
        raise ValueError(f"--field {spec!r}: expected NAME:TYPE, e.g. name:str or price:u32le")
    kind, _, arg = rest.partition(":")
    kind = kind.lower()
    if kind == "str":
        return Field(name, "str")
    if kind in ("bytes", "pad"):
        if not arg.isdigit() or int(arg) <= 0:
            raise ValueError(f"--field {spec!r}: {kind} needs a positive length, e.g. "
                             f"{name}:{kind}:4")
        return Field(name, kind, int(arg))
    if kind in FIXED_TYPES:
        w, order, signed = FIXED_TYPES[kind]
        return Field(name, kind, w, order, signed)
    raise ValueError(f"--field {spec!r}: unknown type {kind!r}. Known: str, "
                     f"{', '.join(sorted(FIXED_TYPES))}, bytes:N, pad:N")


def parse_range(text: str, what: str) -> List[int]:
    """'4..12' or '6' or '0..8'. Inclusive."""
    t = text.strip()
    if ".." in t:
        a, _, b = t.partition("..")
        try:
            lo, hi = int(a, 0), int(b, 0)
        except ValueError:
            raise ValueError(f"{what}: expected A..B, got {text!r}")
    else:
        try:
            lo = hi = int(t, 0)
        except ValueError:
            raise ValueError(f"{what}: expected N or A..B, got {text!r}")
    if lo < 0 or hi < lo:
        raise ValueError(f"{what}: bad range {text!r}")
    if hi - lo > 256:
        raise ValueError(f"{what}: range {text!r} is too wide to be meaningful")
    return list(range(lo, hi + 1))


# --------------------------------------------------------------------------------------
# the parser under test
# --------------------------------------------------------------------------------------

MAX_RECORDS = 1_000_000


def parse_records(data: bytes, header: int, fields: Sequence[Field], trailer: int,
                  prefix: Tuple[int, str]) -> Tuple[List[Dict[str, Any]], int]:
    """Parse until EOF. Raises ParseError the moment the layout stops making sense."""
    pw, porder = prefix
    n = len(data)
    off = header
    records: List[Dict[str, Any]] = []
    while off < n:
        start = off
        rec: Dict[str, Any] = {}
        for f in fields:
            if f.kind == "str":
                if off + pw > n:
                    raise ParseError(f"truncated string prefix at 0x{off:x}")
                ln = int.from_bytes(data[off:off + pw], porder)
                off += pw
                if off + ln > n:
                    raise ParseError(f"string of {ln} at 0x{off:x} runs past EOF")
                rec[f.name] = data[off:off + ln]
                off += ln
            elif f.kind in ("bytes", "pad"):
                if off + f.width > n:
                    raise ParseError(f"truncated {f.kind} at 0x{off:x}")
                if f.kind == "bytes":
                    rec[f.name] = data[off:off + f.width]
                off += f.width
            else:
                if off + f.width > n:
                    raise ParseError(f"truncated {f.kind} at 0x{off:x}")
                rec[f.name] = int.from_bytes(data[off:off + f.width], f.order,
                                             signed=f.signed)
                off += f.width
        if off + trailer > n:
            raise ParseError(f"truncated trailer at 0x{off:x}")
        rec["_trailer"] = data[off:off + trailer]
        off += trailer
        if off == start:
            raise ParseError("zero-length record: this layout can never terminate")
        rec["_offset"] = start
        records.append(rec)
        if len(records) > MAX_RECORDS:
            raise ParseError("absurd record count; layout is wrong")
    return records, off


# --------------------------------------------------------------------------------------
# plausibility
# --------------------------------------------------------------------------------------

def valid_text(raw: bytes, encoding: str, max_len: int = 128) -> bool:
    """A sanity predicate for a decoded string field. Deliberately conservative."""
    if not raw or len(raw) > max_len:
        return False
    if any(b < 0x20 and b not in (0x09,) for b in raw):
        return False
    try:
        s = raw.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        return False
    return "�" not in s


def entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    n = len(data)
    return -sum((c / n) * math.log2(c / n) for c in counts if c)


def header_corroborates(data: bytes, header: int, count: int) -> Optional[str]:
    """Does an integer in the header equal the record count? Independent evidence."""
    for width, order, label in ((2, "little", "u16le"), (2, "big", "u16be"),
                                (4, "little", "u32le"), (4, "big", "u32be")):
        for pos in range(0, max(header - width + 1, 0)):
            if int.from_bytes(data[pos:pos + width], order) == count:
                return f"header {label} at +0x{pos:x} == the record count ({count})"
    return None


# --------------------------------------------------------------------------------------
# the search
# --------------------------------------------------------------------------------------

class Candidate:
    def __init__(self, header: int, trailer: int, fields: Sequence[Field],
                 records: List[Dict[str, Any]], consumed: int, encoding: str,
                 data: bytes):
        self.header, self.trailer = header, trailer
        self.fields = list(fields)
        self.records = records
        self.consumed = consumed
        self.count = len(records)
        str_fields = [f.name for f in fields if f.kind == "str"]
        total = ok = 0
        for r in records:
            for name in str_fields:
                total += 1
                if valid_text(r[name], encoding):
                    ok += 1
        self.text_fields = len(str_fields)
        # No string fields => no plausibility evidence at all. Score 0, not 1: a
        # fixed-width layout with no strings consumes almost any file of the right
        # length, and must never out-rank a layout that actually decodes as text.
        self.text_score = (ok / total) if total else 0.0
        self.corroboration = header_corroborates(data, header, self.count)

    @property
    def key(self) -> Tuple[float, int, int]:
        # best text plausibility first; then more records; then a smaller trailer, because
        # a larger trailer can always absorb a real field you failed to model.
        return (round(self.text_score, 3), self.count, -self.trailer)

    def signature(self) -> str:
        return (f"header={self.header} trailer={self.trailer} "
                f"fields=[{', '.join(repr(f) for f in self.fields)}]")

    def to_dict(self) -> Dict[str, Any]:
        return {"header": self.header, "trailer": self.trailer,
                "fields": [repr(f) for f in self.fields], "records": self.count,
                "consumed": self.consumed, "text_score": round(self.text_score, 4),
                "corroboration": self.corroboration}


def search(data: bytes, templates: Sequence[Sequence[Field]], headers: Sequence[int],
           trailers: Sequence[int], prefix: Tuple[int, str], encoding: str,
           min_records: int) -> Tuple[List[Candidate], int]:
    accepted: List[Candidate] = []
    tried = 0
    for fields in templates:
        for header in headers:
            for trailer in trailers:
                tried += 1
                try:
                    records, consumed = parse_records(data, header, fields, trailer, prefix)
                except ParseError:
                    continue
                # THE assertion. A layout that ends early is wrong even if the text reads
                # perfectly, and a layout that overruns has already raised.
                if consumed != len(data):
                    continue
                if len(records) < min_records:
                    continue
                accepted.append(Candidate(header, trailer, fields, records, consumed,
                                          encoding, data))
    accepted.sort(key=lambda c: c.key, reverse=True)
    # A record count that also appears as an integer in the header is evidence from a
    # DIFFERENT part of the file. When exactly one candidate has it, it outranks every
    # scoring heuristic - so float it to the front.
    corroborated = [c for c in accepted if c.corroboration]
    if len(corroborated) == 1:
        accepted.remove(corroborated[0])
        accepted.insert(0, corroborated[0])
    return accepted, tried


def shape_key(c: Candidate) -> Tuple[Any, ...]:
    """The layout, ignoring the byte order of fixed-width fields.

    Two candidates that differ only in u32le vs u32be occupy identical bytes. The search
    cannot distinguish them and never will: endianness is a property of how the GUEST
    reads the field, not of the file's shape. Treat them as one answer.
    """
    def norm(f: Field) -> str:
        if f.kind == "str":
            return "str"
        if f.kind in ("bytes", "pad"):
            return f"{f.kind}:{f.width}"
        return f"fixed{f.width}"
    return (c.header, c.trailer, tuple(norm(f) for f in c.fields))


def resolve(accepted: Sequence[Candidate]) -> Tuple[str, Optional[Candidate]]:
    """unique | corroborated | endian_only | ambiguous | none, plus the winner."""
    if not accepted:
        return "none", None
    if len(accepted) == 1:
        return "unique", accepted[0]
    corroborated = [c for c in accepted if c.corroboration]
    if len(corroborated) == 1:
        return "corroborated", corroborated[0]
    if corroborated and len({shape_key(c) for c in corroborated}) == 1:
        return "endian_only", corroborated[0]
    if len({shape_key(c) for c in accepted}) == 1:
        return "endian_only", accepted[0]
    return "ambiguous", accepted[0]


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bruteforce_records.py",
        description="Find the record layout that consumes an unknown data file EXACTLY.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  # you have a guess at the fields, not at the framing\n"
            "  python bruteforce_records.py work/dat/<file> \\\n"
            "      --prefix u8len --field name:str --field price:u32le --field effect:str \\\n"
            "      --trailer 4..12 --header 0..4 --encoding euc-kr --show 3\n"
            "\n"
            "  # you have no guess at all\n"
            "  python bruteforce_records.py work/dat/<file> --auto --show 3\n"
            "\n"
            "  # machine-readable, for a script that then writes the real decoder\n"
            "  python bruteforce_records.py work/dat/<file> --auto --json\n"
            "\n"
            "Encrypted members are OUT OF SCOPE. This tool ships no keys and has no --key.\n"
            "exit codes: 0 exactly one layout fits | 1 none or several | 2 I/O or encrypted\n"
        ),
    )
    p.add_argument("file", help="the data file to analyse (forward slashes)")
    p.add_argument("--field", action="append", metavar="NAME:TYPE", default=[],
                   help="one record field, in order. Repeatable. Types: str, u8, i8, "
                        "u16le, u16be, u32le, u32be, i32le, i32be, bytes:N, pad:N")
    p.add_argument("--prefix", default="u8len", choices=sorted(PREFIX_TYPES),
                   help="width/endianness of the length prefix on 'str' fields "
                        "(default: u8len)")
    p.add_argument("--trailer", default="0..12", metavar="A..B",
                   help="range of per-record trailing bytes to try (default: 0..12)")
    p.add_argument("--header", default="0..4", metavar="A..B",
                   help="range of file-header bytes to skip (default: 0..4)")
    p.add_argument("--encoding", default="euc-kr", metavar="ENC",
                   help="encoding used to judge string plausibility and to print records "
                        "(default: euc-kr). Affects the SCORE and the display, never the "
                        "layout search.")
    p.add_argument("--min-records", type=int, default=8, metavar="N",
                   help="reject layouts yielding fewer than N records (default: 8)")
    p.add_argument("--auto", action="store_true",
                   help="try a handful of common field templates instead of --field")
    p.add_argument("--show", type=int, default=3, metavar="N",
                   help="print the first N decoded records of the winner (default: 3, "
                        "0 to print none)")
    p.add_argument("--all", action="store_true",
                   help="list every layout that fits, not just the top few")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    path = posix(args.file)
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not data:
        print(f"error: {path} is empty", file=sys.stderr)
        return 2

    try:
        headers = parse_range(args.header, "--header")
        trailers = parse_range(args.trailer, "--trailer")
        if args.auto:
            templates = [[parse_field_spec(s) for s in tpl] for tpl in AUTO_TEMPLATES]
        else:
            if not args.field:
                print("error: give --field NAME:TYPE (repeatable), or --auto to try common "
                      "shapes.\n  A reasonable first guess for a name/price/description "
                      "table:\n"
                      "    --field name:str --field value:u32le --field text:str",
                      file=sys.stderr)
                return 2
            templates = [[parse_field_spec(s) for s in args.field]]
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    prefix = PREFIX_TYPES[args.prefix]
    ent = entropy(data)
    accepted, tried = search(data, templates, headers, trailers, prefix, args.encoding,
                             args.min_records)

    result: Dict[str, Any] = {
        "file": path, "bytes": len(data), "entropy_bits_per_byte": round(ent, 3),
        "combinations_tried": tried, "accepted": [c.to_dict() for c in accepted],
    }

    if not accepted and ent > 7.5:
        result["verdict"] = "looks-encrypted-or-compressed"
        msg = (f"{path}: {len(data)} bytes at {ent:.2f} bits/byte of entropy, and no record "
               f"layout consumes it.\n"
               f"  That entropy is what encrypted or already-compressed data looks like; "
               f"plain text tables sit far lower.\n"
               f"  Encrypted members are out of scope here. This tool ships no keys, has no "
               f"--key option, and will not acquire one. If you hold a key, decrypt outside "
               f"this tool and re-run on the plaintext.")
        if args.json:
            result["message"] = msg
            print(json.dumps(result, indent=2))
        else:
            print(msg, file=sys.stderr)
        return 2

    verdict, winner = resolve(accepted)
    ok = verdict in ("unique", "corroborated", "endian_only")

    if args.json:
        result["verdict"] = verdict
        if winner is not None:
            result["winner"] = winner.to_dict()
            if args.show:
                result["sample"] = _sample(winner, args.show, args.encoding)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if ok else 1

    print(f"file      {path}   {len(data):,} bytes   entropy {ent:.2f} bits/byte")
    print(f"search    {tried} combinations "
          f"(header {args.header}, trailer {args.trailer}, "
          f"{len(templates)} field template{'s' if len(templates) != 1 else ''}, "
          f"prefix {args.prefix})")
    print(f"accepted  {len(accepted)} layout(s) that consume the file EXACTLY "
          f"(>= {args.min_records} records)")

    if not accepted:
        print("\nNothing fits. In rough order of likelihood:")
        print("  * a field type you have not modelled (an extra u8 flag, a second string,")
        print("    a fixed-width name instead of a length-prefixed one)")
        print("  * the length prefix is not " + args.prefix + " - try --prefix u16le")
        print("  * a file-level footer, not a per-record trailer - widen --header will not")
        print("    help; a footer needs a hand-written parser")
        print("  * there IS an offset table at the front, so records are not contiguous")
        print("  * it is not a record file at all (sprite/audio/script data)")
        print(f"\nWiden the search first:  --header 0..8 --trailer 0..20 --min-records 2")
        return 1

    show = accepted if args.all else accepted[:5]
    print()
    for i, c in enumerate(show):
        mark = "->" if i == 0 else "  "
        print(f"{mark} {c.signature()}")
        judged = (f"text plausibility {c.text_score:.0%} over {c.text_fields} string "
                  f"field(s)" if c.text_fields else
                  "no string fields, so there is NO plausibility evidence - a fixed-width "
                  "layout consumes almost any file of the right length")
        print(f"     {c.count} records, consumed {c.consumed}/{len(data)} bytes, {judged}")
        if c.corroboration:
            print(f"     CORROBORATION: {c.corroboration} - independent evidence from a "
                  f"different part of the file")
    if len(accepted) > len(show):
        print(f"   ... and {len(accepted) - len(show)} more (--all to list)")

    win = winner
    if args.show and win is not None and win.records:
        print(f"\nfirst {min(args.show, win.count)} records under the winning layout "
              f"(decoded as {args.encoding}):")
        for line in _sample(win, args.show, args.encoding):
            print(f"  {line}")

    if verdict == "ambiguous":
        print(f"\nAMBIGUOUS: {len(accepted)} layouts consume the file exactly, and none is "
              f"singled out by the header. Consuming the file is necessary, not sufficient.")
        print("  Narrow it by: pinning --trailer to one value once you know what the bytes")
        print("  mean; adding the field you suspect is hiding inside the trailer; or")
        print("  checking a SECOND file of the same family - the real layout fits both.")
        return 1

    if verdict == "corroborated":
        print(f"\nRESOLVED: {len(accepted)} layouts consume the file, but exactly one is "
              f"corroborated by the header, so that is the answer. The others fit by "
              f"coincidence - a length-prefixed stream can re-synchronise on a different "
              f"phase and still land on EOF.")
    elif verdict == "endian_only":
        print(f"\nRESOLVED (up to byte order): {len(accepted)} candidates survived, but they "
              f"are all the SAME layout - header {win.header}, trailer {win.trailer}, same "
              f"field widths - differing only in the byte order of fixed-width fields.")
        print("  No amount of layout searching can settle endianness: it is a property of "
              "how the guest READS the field, not of the file's shape. Settle it by which "
              "order gives sane values, or by finding the guest code that loads it.")
    else:
        print("\nOne layout fits.")
    print("Now write the real decoder, and keep the assertion:")
    print(f"  assert consumed == len(data)   # {len(data)} here")
    print("  A decoder without it will one day read plausible garbage and you will believe it.")
    return 0


def _sample(c: Candidate, n: int, encoding: str) -> List[str]:
    out: List[str] = []
    for r in c.records[:n]:
        parts = [f"@0x{r['_offset']:x}"]
        for f in c.fields:
            if f.kind == "pad":
                continue
            v = r[f.name]
            if isinstance(v, bytes):
                try:
                    s = v.decode(encoding)
                    parts.append(f"{f.name}={s!r}")
                except (UnicodeDecodeError, LookupError):
                    parts.append(f"{f.name}=<{v.hex()}>")
            else:
                parts.append(f"{f.name}={v}")
        if r["_trailer"]:
            parts.append(f"trailer={r['_trailer'].hex()}")
        out.append("  ".join(parts))
    return out


if __name__ == "__main__":
    sys.exit(main())
