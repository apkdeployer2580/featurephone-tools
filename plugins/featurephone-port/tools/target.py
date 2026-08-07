#!/usr/bin/env python3
"""target.py - the per-title configuration hinge for every static RE tool in this plugin.

WHAT IT DOES
    Loads and validates a `target.toml`, and turns it into a `Target` object that knows
    how to convert between the three coordinate systems a position-independent guest
    image forces on you:

        file offset      an index into the extracted native image on your disk
        guest address    what the emulator's loader mapped that byte to
        stored word      what a pointer looks like *inside* the image, which on a
                         relocatable build is neither of the above

    Every other tool takes `-t target.toml` and imports this module.  Nothing else in
    the plugin hardcodes an address, a base, or a delta.

THE METHOD IT EMBODIES
    A native feature-phone guest is usually built read-write position independent
    (the ARM toolchain's `-apcs /rwpi` model, or an equivalent).  Three consequences:

      1. The loader picks the load address, so `guest = file_offset + image_base` and
         `image_base` is a property of the emulator, not of the binary.  Read it out of
         your loader crate's `load(data, BASE, ...)` call.  (A)
      2. Static data is addressed as `[sl, #imm]` (sl = r10; some builds use sb = r9).
         There are no absolute data addresses in the instruction stream, so you need the
         static base `sl` before a disassembly listing means anything.  (A)
      3. Pointers stored in the image are *relocation biased*: the stored word is the
         real location minus some delta, and the guest fixes them up at startup.  You
         must recover the delta AND the unit it produces.  (A)

    Getting the unit wrong is the failure this module exists to prevent.  A delta that
    produces file offsets and a delta that produces guest addresses differ by exactly
    `image_base`, and both "work" on a handful of hand-picked strings.  Set
    `reloc_unit` wrong and every tool downstream reports confident nonsense.
    `Target.infer_reloc_unit()` settles it against the whole image; the CLI runs it.

SCHEMA (target.toml is FLAT - no [sections])

    binary       = "work/client.bin"   # required. extracted native guest image.
                                       #   path is resolved RELATIVE TO target.toml.
                                       #   classify_archive.py --extract-native writes it.
    image_base   = 0x100000            # required. THE LOADER'S constant, not the title's:
                                       #   the BASE in your loader's load(data, BASE, ...).
                                       #   0x100000 for KTF; other backends differ.
    bss_size     = 0                   # optional, default 0. zero-filled bytes appended
                                       #   AFTER the image. For a KTF native member it is
                                       #   the integer suffix of the filename; run
                                       #   classify_archive.py and it prints the number.
    sl_fo        = 0x0                 # optional. RWPI static base, AS A FILE OFFSET.
                                       #   Found by register dump / entry-thunk prologue /
                                       #   dense-static-dump cross-check - see _GUIDANCE.
    reloc_delta  = 0x0                 # optional. Add to a stored word to recover ...
                                       #   Found by set-intersection over known string
                                       #   offsets, then validated corpus-wide.
    reloc_unit   = "file_offset"       # ... "file_offset" or "guest_address".
                                       #   REQUIRED whenever reloc_delta is present.
                                       #   Settled by `target.py <t.toml> --check`.
    encoding     = "euc-kr"            # optional, default "euc-kr". Text encoding of the
                                       #   game's strings: euc-kr, shift_jis, gbk, big5,
                                       #   cp1252, utf-8, ...
    isa          = "thumb"             # optional, default "thumb". "thumb" or "arm".
    god_object   = 0x0                 # optional. GUEST ADDRESS of the static slot that
                                       #   holds the main state pointer. Found by dumping
                                       #   sl..sl+0x800 and taking the slot that always
                                       #   holds a live heap pointer.

    Every value except image_base is per-title and must be MEASURED, never copied from
    anyone's example.  Each one has a derivation recorded in `_GUIDANCE` below, which is
    what the error messages quote back at you when a key is missing.  If you find
    concrete numbers for these keys in someone else's write-up, they describe that
    person's binary and are worthless - and worse than worthless if they happen to look
    plausible against yours.

PUBLIC API (other tools depend on this - it is stable)

    Target.add_argument(parser)            adds `-t/--target`
    Target.from_args(args)                 -> Target
    Target.load(path=None)                 -> Target  (path=None -> $FEATUREPHONE_TARGET
                                                       then ./target.toml)

    t.image_base  t.bss_size  t.encoding  t.isa                 always present
    t.sl_fo  t.reloc_delta  t.reloc_unit  t.god_object          raise TargetError if unset
    t.has("sl_fo")                         -> bool
    t.get("sl_fo", default=None)           -> value or default for an UNSET key. A key that
                                           is not in the schema at all still raises, so a
                                           typo in a tool fails loudly instead of silently
                                           taking the default forever.
    t.require("sl_fo", "reloc_delta")      raises TargetError naming the missing key and
                                           how to find it

    t.data                                 -> bytes of the binary (lazily read, cached)
    t.file_size / t.image_size             image_size = file_size + bss_size
    t.sl_guest                             image_base + sl_fo

    t.fo_to_guest(fo) / t.guest_to_fo(a)   pure arithmetic, no bounds check
    t.in_file_fo(fo) / t.in_image_fo(fo)   bounds predicates (image includes BSS)
    t.in_file_guest(a) / t.in_image_guest(a)

    t.read_u8/u16/u32(fo)  t.read_bytes(fo, n)   raise on out of range, and say whether
                                                 the offset fell in BSS
    t.read_u32_guest(addr)
    t.cstring_at(fo, encoding=None, max_len=512) -> str | None

    t.deref_reloc(word)                    stored word -> FILE OFFSET, or None
    t.deref_reloc_guest(word)              stored word -> GUEST ADDRESS, or None
    t.deref_reloc_string(word)             stored word -> decoded string, or None
    t.reloc_word_for_fo(fo)                inverse: the word you would search for
    t.reloc_word_for_guest(addr)

    t.sl_slot_fo(imm)                      file offset of the static slot at [sl,#imm]
    t.read_sl_slot(imm)                    the word in that slot
    t.classify_word(word)                  -> dict, for static-area dumpers

    t.infer_reloc_unit()                   -> dict, corpus-wide evidence for the unit
    t.sanity_report()                      -> list[Check]
    t.to_dict()                            -> JSON-safe summary

KNOWN LIMITS
    * Only the flat scalar schema above is understood.  A `[section]` is an error.
    * Python 3.11+ parses with `tomllib`; on 3.10 a small built-in parser handles this
      flat subset.  No third-party TOML dependency, ever.
    * The reloc model assumed here is "one global delta".  A guest with per-section
      relocation, or with a real dynamic relocation table, needs more than this file
      can express - `infer_reloc_unit()` will show a poor referenced-fraction and that
      is your signal to stop trusting a single delta.
    * `god_object` is a guest address by definition here.  A value below `image_base`
      is reported as a probable file offset mistake.
    * This module never writes anything and never guesses a value it was not given.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple

__all__ = ["Target", "TargetError", "Check", "posix", "parse_int"]

DEFAULT_TARGET_ENV = "FEATUREPHONE_TARGET"
DEFAULT_TARGET_NAME = "target.toml"

RELOC_UNITS = ("file_offset", "guest_address")
ISAS = ("thumb", "arm")


class TargetError(Exception):
    """A target.toml is missing, malformed, or is being used beyond what it declares."""


# --------------------------------------------------------------------------------------
# small shared helpers other tools may import
# --------------------------------------------------------------------------------------

def posix(path: str) -> str:
    """Display form of a path: forward slashes on every OS."""
    return str(path).replace("\\", "/")


def parse_int(text: str, what: str = "value") -> int:
    """Accept 0x.., 0o.., 0b.., decimal, with underscores. Raises TargetError."""
    s = str(text).strip().replace("_", "")
    neg = s.startswith("-")
    if neg:
        s = s[1:]
    try:
        if s.lower().startswith("0x"):
            v = int(s, 16)
        elif s.lower().startswith("0o"):
            v = int(s, 8)
        elif s.lower().startswith("0b"):
            v = int(s, 2)
        else:
            v = int(s, 10)
    except ValueError:
        raise TargetError(f"{what}: {text!r} is not an integer (use 0x1f400 or 128000)")
    return -v if neg else v


# --------------------------------------------------------------------------------------
# TOML loading: tomllib on 3.11+, a flat-subset parser on 3.10
# --------------------------------------------------------------------------------------

def _load_toml_bytes(raw: bytes, path: str) -> Dict[str, Any]:
    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:
        return _parse_flat_toml(raw, path)
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise TargetError(f"{posix(path)}: not valid UTF-8 ({exc})")
    except Exception as exc:  # tomllib.TOMLDecodeError
        raise TargetError(f"{posix(path)}: TOML parse error: {exc}")


_KV_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_\-]*)\s*=\s*(.+?)\s*$")


def _parse_flat_toml(raw: bytes, path: str) -> Dict[str, Any]:
    """Parse the flat scalar subset this plugin's target.toml uses.

    Deliberately small.  Supports: comments, bare keys, quoted strings (basic and
    literal), booleans, integers in dec/hex/oct/bin with underscores, and floats.
    Rejects tables and arrays with a message that says so, rather than silently
    dropping them - a silently dropped key is exactly the class of bug this whole
    module exists to prevent.
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TargetError(f"{posix(path)}: not valid UTF-8 ({exc})")

    out: Dict[str, Any] = {}
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("["):
            raise TargetError(
                f"{posix(path)}:{lineno}: target.toml is flat - [sections] are not part "
                f"of the schema (got {stripped!r})"
            )
        m = _KV_RE.match(line)
        if not m:
            raise TargetError(f"{posix(path)}:{lineno}: expected `key = value`, got {line.rstrip()!r}")
        key, rhs = m.group(1), m.group(2)
        value = _parse_flat_value(rhs, path, lineno)
        if key in out:
            raise TargetError(f"{posix(path)}:{lineno}: duplicate key {key!r}")
        out[key] = value
    return out


def _parse_flat_value(rhs: str, path: str, lineno: int) -> Any:
    # strip a trailing comment that is outside any quotes
    quote = None
    end = len(rhs)
    i = 0
    while i < len(rhs):
        c = rhs[i]
        if quote:
            if c == "\\" and quote == '"':
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "#":
            end = i
            break
        i += 1
    rhs = rhs[:end].strip()
    if not rhs:
        raise TargetError(f"{posix(path)}:{lineno}: empty value")

    if rhs[0] == '"' and rhs.endswith('"') and len(rhs) >= 2:
        return _unescape_basic(rhs[1:-1], path, lineno)
    if rhs[0] == "'" and rhs.endswith("'") and len(rhs) >= 2:
        return rhs[1:-1]
    if rhs in ("true", "false"):
        return rhs == "true"
    if rhs.startswith("[") or rhs.startswith("{"):
        raise TargetError(
            f"{posix(path)}:{lineno}: arrays and inline tables are not part of the "
            f"target.toml schema"
        )
    cleaned = rhs.replace("_", "")
    try:
        if cleaned.lower().startswith(("0x", "-0x", "0o", "-0o", "0b", "-0b")):
            return parse_int(rhs, f"{posix(path)}:{lineno}")
        if re.fullmatch(r"[+-]?\d+", cleaned):
            return int(cleaned, 10)
        if re.fullmatch(r"[+-]?(\d+\.\d*|\.\d+)([eE][+-]?\d+)?", cleaned):
            return float(cleaned)
    except TargetError:
        raise
    raise TargetError(
        f"{posix(path)}:{lineno}: cannot parse value {rhs!r}. Strings must be quoted."
    )


def _unescape_basic(s: str, path: str, lineno: int) -> str:
    out: List[str] = []
    i = 0
    simple = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "b": "\b", "f": "\f"}
    while i < len(s):
        c = s[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        if i + 1 >= len(s):
            raise TargetError(f"{posix(path)}:{lineno}: trailing backslash in string")
        e = s[i + 1]
        if e in simple:
            out.append(simple[e])
            i += 2
        elif e in ("u", "U"):
            n = 4 if e == "u" else 8
            hexs = s[i + 2:i + 2 + n]
            if len(hexs) != n:
                raise TargetError(f"{posix(path)}:{lineno}: bad \\{e} escape")
            out.append(chr(int(hexs, 16)))
            i += 2 + n
        else:
            raise TargetError(f"{posix(path)}:{lineno}: unknown escape \\{e}")
    return "".join(out)


# --------------------------------------------------------------------------------------
# schema
# --------------------------------------------------------------------------------------

_REQUIRED = ("binary", "image_base")

_DEFAULTS: Dict[str, Any] = {
    "bss_size": 0,
    "encoding": "euc-kr",
    "isa": "thumb",
}

_OPTIONAL_NO_DEFAULT = ("sl_fo", "reloc_delta", "reloc_unit", "god_object")

_KNOWN_KEYS = set(_REQUIRED) | set(_DEFAULTS) | set(_OPTIONAL_NO_DEFAULT)

# How to obtain each key, quoted back at the user when it is missing or wrong.
_GUIDANCE: Dict[str, str] = {
    "binary":
        "the extracted native guest image, e.g. binary = \"work/client.bin\". "
        "Get it with: classify_archive.py <archive> --extract-native work/client.bin",
    "image_base":
        "the load address, e.g. image_base = 0x100000. It is a property of the LOADER, "
        "not of the binary: open your platform's loader crate and read the BASE argument "
        "of its load(data, BASE, ...) call.",
    "bss_size":
        "zero-filled bytes appended after the image. For KTF it is the integer suffix of "
        "the native member's filename (client.bin<N>); classify_archive.py prints it. "
        "It matters because the guest's writable statics can live PAST the end of the "
        "file, so an address beyond EOF is not automatically invalid.",
    "sl_fo":
        "the RWPI static base as a FILE OFFSET (sl_guest = image_base + sl_fo). Three "
        "independent routes, use at least two: (1) boot headless, provoke a fault, read "
        "SL/SB out of the register dump; (2) disassemble from image_base+1 and follow the "
        "`ldr rX,[pc,#..]; mov sl,rX; add sl,pc` prologue; (3) dump 0x400 words at a "
        "candidate base and check they decode densely as strings and function pointers.",
    "reloc_delta":
        "add this to a stored pointer word to recover a real location. Solve it: build the "
        "set S of every 4-byte word in the image, take >=5 strings whose file offsets you "
        "know, and intersect {t - v : v in S} across them. Then VALIDATE corpus-wide - "
        "count what fraction of all asciiz candidates in the image are referenced under "
        "the candidate. Never adopt a delta from five hand-picked samples.",
    "reloc_unit":
        "\"file_offset\" or \"guest_address\" - which one `word + reloc_delta` produces. "
        "The two differ by exactly image_base and both look right on a few samples. "
        "Run: target.py <target.toml> --check   to settle it against the whole image.",
    "encoding":
        "the text encoding of the game's own strings, e.g. \"euc-kr\", \"shift_jis\", "
        "\"gbk\", \"big5\", \"cp1252\", \"utf-8\".",
    "isa":
        "\"thumb\" or \"arm\" - the default decode mode for disassembly.",
    "god_object":
        "optional. The GUEST ADDRESS of the static slot holding the main state pointer. "
        "Find it by dumping sl..sl+0x800 and picking the slot that always holds a live "
        "heap pointer.",
}


class Check:
    """One sanity-check result from Target.sanity_report()."""

    __slots__ = ("level", "code", "message")

    def __init__(self, level: str, code: str, message: str):
        self.level = level  # "ok" | "warn" | "error"
        self.code = code
        self.message = message

    def to_dict(self) -> Dict[str, str]:
        return {"level": self.level, "code": self.code, "message": self.message}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Check({self.level}, {self.code}, {self.message!r})"


class Target:
    """A loaded, validated target.toml plus the address arithmetic it implies."""

    # ---------------------------------------------------------------- construction

    def __init__(self, path: str, raw: Dict[str, Any], *, require_binary: bool = True):
        self.path = posix(os.path.abspath(path))
        self.root = posix(os.path.dirname(self.path))
        self._raw = dict(raw)
        self._data: Optional[bytes] = None
        self.defaulted: List[str] = []
        self.unknown_keys: List[str] = sorted(k for k in raw if k not in _KNOWN_KEYS)
        self._validate(require_binary=require_binary)

    @classmethod
    def load(cls, path: Optional[str] = None, *, require_binary: bool = True) -> "Target":
        p = path or os.environ.get(DEFAULT_TARGET_ENV) or DEFAULT_TARGET_NAME
        if not os.path.isfile(p):
            hint = ""
            if path is None:
                hint = (
                    f"\n  Looked for: {posix(os.path.abspath(p))}"
                    f"\n  Pass -t <path>, or set {DEFAULT_TARGET_ENV}."
                )
            raise TargetError(f"target.toml not found: {posix(p)}{hint}")
        with open(p, "rb") as fh:
            raw = fh.read()
        return cls(p, _load_toml_bytes(raw, p), require_binary=require_binary)

    @staticmethod
    def add_argument(parser: argparse.ArgumentParser, *, required: bool = False) -> None:
        """Add the standard `-t/--target` flag. Every tool in this plugin uses this."""
        parser.add_argument(
            "-t", "--target",
            metavar="target.toml",
            required=required,
            help=(f"per-title config (default: ${DEFAULT_TARGET_ENV} then ./{DEFAULT_TARGET_NAME}); "
                  f"see target.py --schema"),
        )

    @classmethod
    def from_args(cls, args: argparse.Namespace, *, require_binary: bool = True) -> "Target":
        return cls.load(getattr(args, "target", None), require_binary=require_binary)

    # ---------------------------------------------------------------- validation

    def _validate(self, *, require_binary: bool) -> None:
        raw = self._raw
        for key in _REQUIRED:
            if key not in raw:
                raise TargetError(
                    f"{self.path}: missing required key '{key}'.\n  {key} is {_GUIDANCE[key]}"
                )

        # binary
        b = raw["binary"]
        if not isinstance(b, str):
            raise TargetError(f"{self.path}: 'binary' must be a quoted path string")
        self.binary = posix(b)
        self.binary_path = self.binary if os.path.isabs(self.binary) else posix(
            os.path.normpath(os.path.join(self.root, self.binary))
        )
        if require_binary and not os.path.isfile(self.binary_path):
            raise TargetError(
                f"{self.path}: binary not found: {self.binary_path}\n"
                f"  'binary' is resolved RELATIVE TO the directory holding target.toml.\n"
                f"  {_GUIDANCE['binary']}"
            )

        # ints
        self.image_base = self._int_key("image_base", minimum=0)
        self.bss_size = self._int_key("bss_size", minimum=0, default=_DEFAULTS["bss_size"])
        self._sl_fo = self._int_key("sl_fo", minimum=0, default=None)
        self._reloc_delta = self._int_key("reloc_delta", default=None)
        self._god_object = self._int_key("god_object", minimum=0, default=None)

        # reloc_unit: required whenever a delta is declared, meaningless otherwise
        unit = raw.get("reloc_unit")
        if unit is not None and not isinstance(unit, str):
            raise TargetError(f"{self.path}: 'reloc_unit' must be a quoted string")
        if unit is not None and unit not in RELOC_UNITS:
            raise TargetError(
                f"{self.path}: reloc_unit = {unit!r} is not one of {RELOC_UNITS}.\n"
                f"  reloc_unit is {_GUIDANCE['reloc_unit']}"
            )
        if self._reloc_delta is not None and unit is None:
            raise TargetError(
                f"{self.path}: 'reloc_delta' is set but 'reloc_unit' is missing.\n"
                f"  A delta without its unit is unusable: the two candidate units differ by "
                f"exactly image_base (0x{self.image_base:x}) and BOTH decode a few "
                f"hand-picked strings correctly.\n"
                f"  reloc_unit is {_GUIDANCE['reloc_unit']}"
            )
        self._reloc_unit = unit

        # strings
        enc = raw.get("encoding", _DEFAULTS["encoding"])
        if not isinstance(enc, str):
            raise TargetError(f"{self.path}: 'encoding' must be a quoted string")
        if "encoding" not in raw:
            self.defaulted.append("encoding")
        try:
            "probe".encode(enc)
        except LookupError:
            raise TargetError(
                f"{self.path}: encoding = {enc!r} is not a codec Python knows.\n"
                f"  encoding is {_GUIDANCE['encoding']}"
            )
        self.encoding = enc

        isa = raw.get("isa", _DEFAULTS["isa"])
        if not isinstance(isa, str) or isa.lower() not in ISAS:
            raise TargetError(
                f"{self.path}: isa = {isa!r} is not one of {ISAS}.\n  isa is {_GUIDANCE['isa']}"
            )
        if "isa" not in raw:
            self.defaulted.append("isa")
        self.isa = isa.lower()

        if "bss_size" not in raw:
            self.defaulted.append("bss_size")

    def _int_key(self, key: str, *, minimum: Optional[int] = None,
                 default: Optional[int] = 0) -> Optional[int]:
        if key not in self._raw:
            return default
        v = self._raw[key]
        if isinstance(v, bool) or not isinstance(v, (int, str)):
            raise TargetError(
                f"{self.path}: '{key}' must be an integer (0x... or decimal), got {v!r}.\n"
                f"  {key} is {_GUIDANCE.get(key, '')}"
            )
        if isinstance(v, str):
            v = parse_int(v, f"{self.path}: {key}")
        if minimum is not None and v < minimum:
            raise TargetError(f"{self.path}: '{key}' must be >= {minimum}, got {v}")
        return v

    # ---------------------------------------------------------------- optional keys

    def has(self, key: str) -> bool:
        return self.get(key) is not None

    def get(self, key: str, default: Any = None) -> Any:
        mapping = {
            "binary": getattr(self, "binary", None),
            "image_base": self.image_base,
            "bss_size": self.bss_size,
            "sl_fo": self._sl_fo,
            "reloc_delta": self._reloc_delta,
            "reloc_unit": self._reloc_unit,
            "encoding": self.encoding,
            "isa": self.isa,
            "god_object": self._god_object,
        }
        if key not in mapping:
            raise TargetError(f"unknown target key {key!r}; known keys: {sorted(_KNOWN_KEYS)}")
        v = mapping[key]
        return default if v is None else v

    def require(self, *keys: str) -> None:
        """Assert that optional keys this tool needs are actually present."""
        missing = [k for k in keys if not self.has(k)]
        if not missing:
            return
        lines = [f"{self.path} does not declare: {', '.join(missing)}",
                 "This tool cannot run without them."]
        for k in missing:
            lines.append(f"  {k} = ...   # {_GUIDANCE.get(k, 'see target.py --schema')}")
        raise TargetError("\n".join(lines))

    @property
    def sl_fo(self) -> int:
        self.require("sl_fo")
        return int(self._sl_fo)

    @property
    def reloc_delta(self) -> int:
        self.require("reloc_delta")
        return int(self._reloc_delta)

    @property
    def reloc_unit(self) -> str:
        self.require("reloc_delta")
        return str(self._reloc_unit)

    @property
    def god_object(self) -> int:
        self.require("god_object")
        return int(self._god_object)

    @property
    def sl_guest(self) -> int:
        return self.image_base + self.sl_fo

    # ---------------------------------------------------------------- image bytes

    @property
    def data(self) -> bytes:
        if self._data is None:
            try:
                with open(self.binary_path, "rb") as fh:
                    self._data = fh.read()
            except OSError as exc:
                raise TargetError(f"cannot read binary {self.binary_path}: {exc}")
            if not self._data:
                raise TargetError(f"binary is empty: {self.binary_path}")
        return self._data

    @property
    def file_size(self) -> int:
        return len(self.data)

    @property
    def image_size(self) -> int:
        """Bytes the loader maps: the file plus the BSS the loader appends."""
        return len(self.data) + self.bss_size

    # ---------------------------------------------------------------- conversions

    def fo_to_guest(self, fo: int) -> int:
        """file offset -> guest address. Pure arithmetic; no bounds check."""
        return fo + self.image_base

    def guest_to_fo(self, addr: int) -> int:
        """guest address -> file offset. Pure arithmetic; may return a negative."""
        return addr - self.image_base

    def in_file_fo(self, fo: int) -> bool:
        return 0 <= fo < self.file_size

    def in_image_fo(self, fo: int) -> bool:
        """True inside file OR inside the appended BSS."""
        return 0 <= fo < self.image_size

    def in_file_guest(self, addr: int) -> bool:
        return self.in_file_fo(self.guest_to_fo(addr))

    def in_image_guest(self, addr: int) -> bool:
        return self.in_image_fo(self.guest_to_fo(addr))

    # ---------------------------------------------------------------- raw reads

    def _bounds_error(self, fo: int, n: int) -> TargetError:
        if self.file_size <= fo < self.image_size:
            return TargetError(
                f"file offset 0x{fo:x} is past EOF (0x{self.file_size:x}) but inside the "
                f"{self.bss_size} bytes of BSS the loader appends. BSS is zero-filled at "
                f"load time and is NOT in the file, so it cannot be read offline - read it "
                f"from a live emulator instead."
            )
        return TargetError(
            f"file offset 0x{fo:x}..0x{fo + n:x} is outside the image "
            f"(file 0x0..0x{self.file_size:x}, +BSS to 0x{self.image_size:x})"
        )

    def read_bytes(self, fo: int, n: int) -> bytes:
        if fo < 0 or n < 0 or fo + n > self.file_size:
            raise self._bounds_error(fo, n)
        return self.data[fo:fo + n]

    def read_u8(self, fo: int) -> int:
        return self.read_bytes(fo, 1)[0]

    def read_u16(self, fo: int, big: bool = False) -> int:
        return int.from_bytes(self.read_bytes(fo, 2), "big" if big else "little")

    def read_u32(self, fo: int, big: bool = False) -> int:
        return int.from_bytes(self.read_bytes(fo, 4), "big" if big else "little")

    def read_u32_guest(self, addr: int) -> int:
        return self.read_u32(self.guest_to_fo(addr))

    def words(self, start_fo: int = 0, end_fo: Optional[int] = None,
              step: int = 4) -> Iterable[Tuple[int, int]]:
        """Yield (file_offset, u32 LE) over an aligned range. Used by scanners."""
        end = self.file_size if end_fo is None else min(end_fo, self.file_size)
        d = self.data
        fo = start_fo
        while fo + 4 <= end:
            yield fo, int.from_bytes(d[fo:fo + 4], "little")
            fo += step

    def decode(self, raw: bytes, encoding: Optional[str] = None) -> str:
        return raw.decode(encoding or self.encoding, errors="replace")

    def cstring_at(self, fo: int, encoding: Optional[str] = None,
                   max_len: int = 512) -> Optional[str]:
        """NUL-terminated string at a file offset, decoded. None if not plausible."""
        if not self.in_file_fo(fo):
            return None
        end = self.data.find(b"\x00", fo, min(fo + max_len, self.file_size))
        if end < 0:
            return None
        raw = self.data[fo:end]
        if not raw:
            return ""
        return self.decode(raw, encoding)

    # ---------------------------------------------------------------- relocation

    def deref_reloc(self, word: int) -> Optional[int]:
        """A stored pointer word -> the FILE OFFSET it names, honouring reloc_unit.

        Returns None when the word does not land inside the image - which is the normal
        answer for most words, since a static area is mostly not pointers.
        """
        self.require("reloc_delta")
        if word == 0:
            return None
        v = (word + int(self._reloc_delta)) & 0xFFFFFFFF
        fo = v if self._reloc_unit == "file_offset" else v - self.image_base
        return fo if self.in_file_fo(fo) else None

    def deref_reloc_guest(self, word: int) -> Optional[int]:
        fo = self.deref_reloc(word)
        return None if fo is None else self.fo_to_guest(fo)

    def deref_reloc_string(self, word: int, encoding: Optional[str] = None,
                           max_len: int = 512) -> Optional[str]:
        fo = self.deref_reloc(word)
        return None if fo is None else self.cstring_at(fo, encoding, max_len)

    def reloc_word_for_fo(self, fo: int) -> int:
        """Inverse of deref_reloc: the word you would grep the image for."""
        self.require("reloc_delta")
        v = fo if self._reloc_unit == "file_offset" else self.fo_to_guest(fo)
        return (v - int(self._reloc_delta)) & 0xFFFFFFFF

    def reloc_word_for_guest(self, addr: int) -> int:
        return self.reloc_word_for_fo(self.guest_to_fo(addr))

    # ---------------------------------------------------------------- static area

    def sl_slot_fo(self, imm: int) -> int:
        """File offset of the static slot referenced as [sl, #imm]."""
        return self.sl_fo + imm

    def read_sl_slot(self, imm: int) -> int:
        return self.read_u32(self.sl_slot_fo(imm))

    def classify_word(self, word: int) -> Dict[str, Any]:
        """Classify one static-area word. For static-area dumpers.

        A set low bit on a relocatable pointer means Thumb, so a word with bit0 set is a
        candidate function pointer; a word without it is a candidate data pointer.  Both
        are only candidates - the string decode is the evidence.
        """
        out: Dict[str, Any] = {"word": word, "kind": "plain", "fo": None,
                               "guest": None, "text": None}
        if word == 0 or not self.has("reloc_delta"):
            return out
        if word & 1:
            fo = self.deref_reloc(word & ~1)
            if fo is not None:
                out.update(kind="thumb_fnptr", fo=fo, guest=self.fo_to_guest(fo))
                return out
        fo = self.deref_reloc(word)
        if fo is not None:
            text = self.cstring_at(fo)
            out.update(kind="data_ptr", fo=fo, guest=self.fo_to_guest(fo), text=text)
            if text and _looks_like_text(text):
                out["kind"] = "string_ptr"
        return out

    # ---------------------------------------------------------------- diagnostics

    def infer_reloc_unit(self, min_len: int = 6, sample_cap: int = 20000) -> Dict[str, Any]:
        """Settle reloc_unit against the whole image instead of a handful of strings.

        Method (A): harvest every plausible asciiz start in the image, then for each
        candidate unit count how many of those strings are actually referenced by some
        word under `reloc_delta`.  The correct unit explains a large share of the corpus;
        the wrong one explains close to none, because the two candidates differ by
        image_base and image_base-sized words are rare.
        """
        self.require("reloc_delta")
        delta = int(self._reloc_delta)
        data = self.data
        pat = re.compile(rb"(?<=\x00)[\x20-\x7e]{%d,}\x00" % min_len)
        starts = [m.start() for m in pat.finditer(data)]
        if len(starts) > sample_cap:
            stride = len(starts) // sample_cap + 1
            starts = starts[::stride]
        wordset = set()
        for _, w in self.words():
            wordset.add(w)
        res: Dict[str, Any] = {"delta": delta, "corpus": len(starts), "units": {}}
        for unit in RELOC_UNITS:
            hits = 0
            for t in starts:
                v = t if unit == "file_offset" else t + self.image_base
                if ((v - delta) & 0xFFFFFFFF) in wordset:
                    hits += 1
            frac = (hits / len(starts)) if starts else 0.0
            res["units"][unit] = {"referenced": hits, "fraction": round(frac, 4)}
        best = max(RELOC_UNITS, key=lambda u: res["units"][u]["referenced"])
        res["best"] = best
        res["configured"] = self._reloc_unit
        res["agrees"] = (best == self._reloc_unit)
        return res

    def sanity_report(self) -> List[Check]:
        """Everything cheap that can be checked without a running emulator."""
        checks: List[Check] = []
        add = lambda lvl, code, msg: checks.append(Check(lvl, code, msg))  # noqa: E731

        if self.unknown_keys:
            add("warn", "unknown_keys",
                f"keys not in the schema, probably typos: {', '.join(self.unknown_keys)}. "
                f"Known keys: {', '.join(sorted(_KNOWN_KEYS))}")
        if self.defaulted:
            add("warn", "defaulted",
                f"not declared, using defaults: "
                f"{', '.join(f'{k}={_DEFAULTS[k]!r}' for k in self.defaulted)}")

        try:
            size = self.file_size
        except TargetError as exc:
            add("error", "binary_unreadable", str(exc))
            return checks
        add("ok", "binary",
            f"{self.binary_path}: {size} bytes (0x{size:x}); image with BSS = "
            f"0x{self.image_size:x}")

        if self.bss_size == 0:
            add("warn", "no_bss",
                "bss_size = 0. For a KTF-style native member the BSS is the integer suffix "
                "of the filename (client.bin<N>); if you did not set it, addresses past EOF "
                "will be misreported as invalid.")

        if self.has("sl_fo"):
            if self._sl_fo >= size:
                add("warn", "sl_outside_file",
                    f"sl_fo 0x{self._sl_fo:x} is past EOF 0x{size:x}. The static area then "
                    f"lives in BSS and CANNOT be read offline - [sl,#imm] annotation and "
                    f"static-area dumps will not work; read those from a live emulator.")
            else:
                add("ok", "sl",
                    f"sl_fo 0x{self._sl_fo:x} -> sl_guest 0x{self.sl_guest:x} "
                    f"({size - self._sl_fo} bytes of file after it)")
        else:
            add("warn", "no_sl", "sl_fo not set: no [sl,#imm] resolution. " + _GUIDANCE["sl_fo"])

        if self.has("reloc_delta"):
            try:
                inf = self.infer_reloc_unit()
            except TargetError as exc:  # pragma: no cover - defensive
                add("error", "reloc_infer_failed", str(exc))
                return checks
            fo_f = inf["units"]["file_offset"]["fraction"]
            ga_f = inf["units"]["guest_address"]["fraction"]
            detail = (f"corpus {inf['corpus']} asciiz candidates; referenced under "
                      f"file_offset {fo_f:.1%}, under guest_address {ga_f:.1%}")
            if inf["corpus"] < 50:
                add("warn", "reloc_thin_corpus",
                    f"only {inf['corpus']} asciiz candidates found - too few to judge the "
                    f"unit. Widen the search or verify by hand. {detail}")
            elif not inf["agrees"]:
                add("error", "reloc_unit_wrong",
                    f"reloc_unit = {self._reloc_unit!r} but the image says "
                    f"{inf['best']!r}. {detail}. Fix reloc_unit before trusting ANY tool "
                    f"in this plugin - every pointer they print is off by image_base.")
            elif max(fo_f, ga_f) < 0.05:
                add("warn", "reloc_delta_weak",
                    f"the configured delta explains almost nothing. Either reloc_delta is "
                    f"wrong, or this image does not use a single global bias. {detail}")
            else:
                add("ok", "reloc",
                    f"reloc_delta 0x{int(self._reloc_delta):x} unit {self._reloc_unit}: {detail}")
        else:
            add("warn", "no_reloc",
                "reloc_delta not set: stored pointers cannot be resolved. "
                + _GUIDANCE["reloc_delta"])

        if self.has("god_object"):
            g = int(self._god_object)
            if g < self.image_base:
                add("warn", "god_object_looks_like_fo",
                    f"god_object 0x{g:x} is below image_base 0x{self.image_base:x}. This key "
                    f"is a GUEST ADDRESS; you may have written a file offset "
                    f"(guest would be 0x{self.fo_to_guest(g):x}).")
            elif not self.in_image_guest(g):
                add("warn", "god_object_outside",
                    f"god_object 0x{g:x} is outside the mapped image "
                    f"0x{self.image_base:x}..0x{self.image_base + self.image_size:x}")
            else:
                fo = self.guest_to_fo(g)
                extra = ""
                if self.has("sl_fo"):
                    extra = f" = sl + 0x{fo - int(self._sl_fo):x}"
                add("ok", "god_object", f"god_object 0x{g:x} -> fo 0x{fo:x}{extra}")

        return checks

    # ---------------------------------------------------------------- output

    def to_dict(self, *, with_binary: bool = True) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "target": self.path,
            "binary": self.binary,
            "binary_path": self.binary_path,
            "image_base": self.image_base,
            "bss_size": self.bss_size,
            "sl_fo": self._sl_fo,
            "reloc_delta": self._reloc_delta,
            "reloc_unit": self._reloc_unit,
            "encoding": self.encoding,
            "isa": self.isa,
            "god_object": self._god_object,
            "defaulted": list(self.defaulted),
            "unknown_keys": list(self.unknown_keys),
        }
        if with_binary:
            try:
                d["file_size"] = self.file_size
                d["image_size"] = self.image_size
                d["sl_guest"] = self.sl_guest if self.has("sl_fo") else None
            except TargetError:
                d["file_size"] = None
                d["image_size"] = None
                d["sl_guest"] = None
        return d

    def describe(self) -> str:
        hx = lambda v: "-" if v is None else f"0x{v:x}"  # noqa: E731
        rows = [
            ("target.toml", self.path),
            ("binary", f"{self.binary_path}"),
            ("image_base", hx(self.image_base)),
            ("bss_size", f"{self.bss_size} (0x{self.bss_size:x})"),
            ("sl_fo", f"{hx(self._sl_fo)}"
                      + (f"  -> sl_guest {hx(self.sl_guest)}" if self.has("sl_fo") else "")),
            ("reloc_delta", hx(self._reloc_delta)),
            ("reloc_unit", self._reloc_unit or "-"),
            ("encoding", self.encoding),
            ("isa", self.isa),
            ("god_object", hx(self._god_object)),
        ]
        w = max(len(k) for k, _ in rows)
        return "\n".join(f"  {k.ljust(w)}  {v}" for k, v in rows)


_TEXTY = re.compile("^[\x20-\x7e\u00a0-\uffff]+$")


def _looks_like_text(s: str) -> bool:
    if len(s) < 3 or "�" in s:
        return False
    return bool(_TEXTY.match(s))


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

SCHEMA_TEMPLATE = '''# target.toml - everything that changes per title lives here and nowhere else.
# Values below are EXAMPLES of shape, not of content. Find your own; see the comments.

binary       = "work/client.bin"   # required. relative to THIS file.
image_base   = 0x100000            # required. from your loader's load(data, BASE, ...)
bss_size     = 0                   # integer suffix of the native member's filename
# sl_fo      = 0x0                 # RWPI static base AS A FILE OFFSET
# reloc_delta = 0x0                # add to a stored word to recover ...
# reloc_unit = "file_offset"       # ... "file_offset" or "guest_address"  (required with delta)
encoding     = "euc-kr"            # euc-kr | shift_jis | gbk | big5 | cp1252 | utf-8
isa          = "thumb"             # thumb | arm
# god_object = 0x0                 # GUEST ADDRESS of the main-state pointer slot
'''


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="target.py",
        description="Load, validate and sanity-check a target.toml; print the address model.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python target.py --schema > target.toml\n"
            "  python target.py target.toml\n"
            "  python target.py target.toml --check\n"
            "  python target.py target.toml --json\n"
            "  python target.py target.toml --fo 0xab1fc      # offset -> address -> string\n"
            "  python target.py target.toml --guest 0x1ab1fc\n"
            "  python target.py target.toml --word 0x000aa310 # stored pointer -> what it names\n"
        ),
    )
    p.add_argument("target", nargs="?", help="path to target.toml")
    p.add_argument("--schema", action="store_true",
                   help="print a commented starter target.toml and exit")
    p.add_argument("--check", action="store_true",
                   help="run the sanity report, including corpus-wide reloc_unit inference")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--fo", metavar="N", help="explain a file offset")
    p.add_argument("--guest", metavar="N", help="explain a guest address")
    p.add_argument("--word", metavar="N", help="explain a stored pointer word")
    p.add_argument("--sl-slot", metavar="IMM", help="explain the static slot at [sl,#IMM]")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.schema:
        sys.stdout.write(SCHEMA_TEMPLATE)
        return 0

    try:
        # --check is the "tell me everything that is wrong" mode, so a missing binary must
        # become one finding among many instead of aborting before the other checks run.
        t = Target.load(args.target, require_binary=not args.check)
    except TargetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    out: Dict[str, Any] = t.to_dict()

    explain: Dict[str, Any] = {}
    try:
        if args.fo is not None:
            fo = parse_int(args.fo, "--fo")
            explain["fo"] = {
                "file_offset": fo, "guest": t.fo_to_guest(fo),
                "in_file": t.in_file_fo(fo), "in_image": t.in_image_fo(fo),
                "string": t.cstring_at(fo),
                "reloc_word": t.reloc_word_for_fo(fo) if t.has("reloc_delta") else None,
            }
        if args.guest is not None:
            a = parse_int(args.guest, "--guest")
            fo = t.guest_to_fo(a)
            explain["guest"] = {
                "guest": a, "file_offset": fo,
                "in_file": t.in_file_fo(fo), "in_image": t.in_image_fo(fo),
                "string": t.cstring_at(fo),
            }
        if args.word is not None:
            w = parse_int(args.word, "--word")
            explain["word"] = t.classify_word(w)
        if args.sl_slot is not None:
            imm = parse_int(args.sl_slot, "--sl-slot")
            slot_fo = t.sl_slot_fo(imm)
            w = t.read_u32(slot_fo)
            explain["sl_slot"] = {
                "imm": imm, "slot_fo": slot_fo, "slot_guest": t.fo_to_guest(slot_fo),
                "word": w, "resolved": t.classify_word(w),
            }
    except TargetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if explain:
        out["explain"] = explain

    checks: List[Check] = []
    if args.check:
        checks = t.sanity_report()
        out["checks"] = [c.to_dict() for c in checks]

    if args.json:
        print(json.dumps(out, indent=2, sort_keys=False))
    else:
        print("address model")
        print(t.describe())
        if explain:
            print("\nexplain")
            print(json.dumps(explain, indent=2, default=str))
        if args.check:
            print("\nchecks")
            for c in checks:
                mark = {"ok": "ok", "warn": "warn", "error": "ERROR"}.get(c.level, c.level)
                print(f"  [{mark:5}] {c.code}: {c.message}")

    if any(c.level == "error" for c in checks):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
