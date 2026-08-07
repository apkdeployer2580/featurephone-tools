#!/usr/bin/env python3
"""repack_member.py - rewrite exactly one member inside a jar inside a zip, and prove it.

WHAT IT DOES
    Surgical nested rewrite.  You name one member by its full nested path:

        --member "<inner>.jar!dat/<file>"

    and one transform function.  The tool rebuilds every archive on that path entry by
    entry, applies the transform to the leaf, and then PROVES that at every level the
    entry list, the entry order, each entry's compress_type and every other member's
    bytes are unchanged.  Output goes to `<archive>.new` unless you pass `--apply`.

THE METHOD IT EMBODIES  (A)
    Game text and tables usually do not live in the guest binary - they live in data
    files inside the jar.  When the host reassigns the BEHAVIOUR of something, the TEXT
    has to follow, and the only place to change it is the archive you ship.

    Two rules make that safe:

    1. NEVER splice at a byte offset.  Parse the record structure, rebuild it, re-emit.
       A length-prefixed record file with no offset table tolerates a string getting
       longer or shorter, as long as the record COUNT and ORDER are preserved - that is
       precisely why variable-length text edits are legal at all.  A file with a
       fixed-layout header or an offset table does not tolerate it, and the same edit
       silently corrupts everything after the change.

    2. VERIFY BY RE-PARSE, not by eyeball.  Pass `--verify mod.py:fn` and the tool calls
       fn(old_bytes, new_bytes) after the rewrite.  Make that function re-run your parser
       over the new bytes and assert the record count is unchanged and exactly the
       records you intended are different.  A fuzzy parser that silently re-segments the
       file passes an eyeball check and fails this one.

MEASURED FACTS ENCODED AS CHECKS, NOT COMMENTS  (A)
    * compress_type is PER ENTRY and some members are STORED.  It is copied, never chosen.
    * Rewriting a zip changes its SIZE even with identical contents, because deflate
      parameters and extra fields differ.  Size is not a check; member bytes are.
    * Entry order is irrelevant to the emulator but is preserved so diffs stay reviewable.
    * A transform that returns the input unchanged is treated as an ERROR, not a success.
      A silent no-op is the most common way one of these scripts lies to you.
    * THIS TOOL IS NOT IDEMPOTENT.  Running a rename-style transform twice will either
      fail your --verify or produce nonsense.  Restore from the backup first.  `--apply`
      writes a `.orig` backup exactly once and then refuses to run again without --force.

WRITING A TRANSFORM
    A plain Python file, anywhere:

        # mytransform.py
        def transform(data: bytes) -> bytes:
            recs = parse(data)            # your format-aware parser
            recs[0x11].name = "..."       # your edit
            return build(recs)            # re-emit, copying unknown fields verbatim

    Optionally take a second argument and you get context:

        def transform(data: bytes, ctx: dict) -> bytes:   # ctx: member, path, archive
            ...

    And a verifier, in the same or another file:

        def verify(old: bytes, new: bytes) -> None:
            a, b = parse(old), parse(new)
            assert len(a) == len(b), "record count changed"
            changed = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
            assert changed == [0x11], f"changed {changed}, expected [0x11]"

    Copy unknown trailing fields through verbatim.  You do not know what they mean, and
    the game does.

KNOWN LIMITS
    * Encrypted members are out of scope.  This tool does not decrypt anything and ships
      no keys.  If a member is encrypted, decrypt it yourself, outside this tool, and
      hand the plaintext transform in.
    * No signature re-signing.  If the jar has .SF/.RSA files the tool warns; anything
      that verifies them will reject the result.
    * Nesting depth is arbitrary, but every level must be a zip.

EXIT CODES
    0  rewrote the member; every proof passed
    1  refused (no-op transform, missing member, backup already present, ...)
    2  I/O error, or a verification FAILED
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
import zipfile
from typing import Any, Callable, Dict, List, Optional, Tuple

# NOTE: do NOT `import inspect` (or anything that pulls it in) from this directory.
# A sibling tool is named dis.py, and the directory holding the running script is
# sys.path[0], so `dis` resolves to that tool instead of the standard library and
# `inspect` fails at import time with AttributeError: module 'dis' has no attribute
# 'COMPILER_FLAG_NAMES'. Arity is read off __code__ below instead.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import classify_archive as ca  # noqa: E402

posix = ca.posix
human = ca.human

SEP = "!"


class Refuse(Exception):
    pass


class VerifyFailed(Exception):
    pass


# --------------------------------------------------------------------------------------
# loading a user callable
# --------------------------------------------------------------------------------------

def load_callable(spec: str, what: str) -> Callable[..., Any]:
    """`path/to/mod.py:fn` or `dotted.module:fn`."""
    if ":" not in spec:
        raise Refuse(f"--{what} must look like 'mod.py:function' or 'pkg.mod:function', "
                     f"got {spec!r}")
    mod_ref, _, fn_name = spec.rpartition(":")
    if mod_ref.endswith(".py") or os.path.sep in mod_ref or "/" in mod_ref:
        path = os.path.abspath(mod_ref)
        if not os.path.isfile(path):
            raise Refuse(f"--{what}: no such file {posix(path)}")
        mod_spec = importlib.util.spec_from_file_location(
            "_fp_" + os.path.splitext(os.path.basename(path))[0], path)
        if mod_spec is None or mod_spec.loader is None:
            raise Refuse(f"--{what}: cannot import {posix(path)}")
        module = importlib.util.module_from_spec(mod_spec)
        # the transform's own directory on sys.path, so it can import its helpers
        sys.path.insert(0, os.path.dirname(path))
        mod_spec.loader.exec_module(module)
    else:
        try:
            module = importlib.import_module(mod_ref)
        except ImportError as exc:
            raise Refuse(f"--{what}: cannot import module {mod_ref!r}: {exc}")
    fn = getattr(module, fn_name, None)
    if fn is None or not callable(fn):
        have = [n for n in dir(module) if not n.startswith("_") and callable(getattr(module, n))]
        raise Refuse(f"--{what}: {mod_ref} has no callable {fn_name!r}. Found: {have}")
    return fn


def _arity(fn: Callable[..., Any]) -> int:
    """Positional parameter count, without importing `inspect` (see the note on imports)."""
    code = getattr(fn, "__code__", None) or getattr(getattr(fn, "__call__", None), "__code__", None)
    if code is None:
        return 1
    n = code.co_argcount
    if hasattr(fn, "__self__"):  # bound method: drop self
        n -= 1
    return n


def call_transform(fn: Callable[..., Any], data: bytes, ctx: Dict[str, Any]) -> bytes:
    result = fn(data, ctx) if _arity(fn) >= 2 else fn(data)
    if not isinstance(result, (bytes, bytearray)):
        raise Refuse(f"the transform returned {type(result).__name__}, not bytes")
    return bytes(result)


# --------------------------------------------------------------------------------------
# nested rewrite
# --------------------------------------------------------------------------------------

def _write_like(dst: zipfile.ZipFile, info: zipfile.ZipInfo, data: bytes) -> None:
    zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
    zi.compress_type = info.compress_type
    zi.external_attr = info.external_attr
    zi.internal_attr = info.internal_attr
    zi.create_system = info.create_system
    zi.comment = info.comment
    dst.writestr(zi, data)


def rewrite(blob: bytes, path: List[str], apply_leaf: Callable[[bytes], bytes],
            trail: List[Dict[str, Any]], prefix: Optional[List[str]] = None) -> bytes:
    """Rebuild `blob` with path[0] replaced; recurse for deeper paths."""
    prefix = prefix or []
    where = SEP.join(prefix) or "<archive>"
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        raise Refuse(f"{where} is not a zip: {exc}")
    names = zf.namelist()
    target = path[0]
    if target not in names:
        near = [n for n in names if target.lower() in n.lower()][:8]
        raise Refuse(f"member {target!r} is not in {where}."
                     + (f" Did you mean: {near}" if near else
                        f" It has {len(names)} entries; use --list to see them."))

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as dst:
        for info in zf.infolist():
            data = zf.read(info.filename)
            if info.filename == target:
                new = (apply_leaf(data) if len(path) == 1
                       else rewrite(data, path[1:], apply_leaf, trail, prefix + [target]))
                trail.append({
                    "level": len(prefix), "member": SEP.join(prefix + [target]),
                    "old_bytes": len(data), "new_bytes": len(new),
                    "old_md5": hashlib.md5(data).hexdigest(),
                    "new_md5": hashlib.md5(new).hexdigest(),
                    "changed": new != data,
                })
                data = new
            _write_like(dst, info, data)
    return out.getvalue()


def verify_nested(src: bytes, dst: bytes, path: List[str],
                  prefix: Optional[List[str]] = None) -> List[str]:
    """At every level: same names, same order, same compression, same bytes but one."""
    prefix = prefix or []
    proofs: List[str] = []
    sz, dz = zipfile.ZipFile(io.BytesIO(src)), zipfile.ZipFile(io.BytesIO(dst))
    s_infos, d_infos = sz.infolist(), dz.infolist()
    where = SEP.join(prefix) or "<archive>"

    if [i.filename for i in s_infos] != [i.filename for i in d_infos]:
        raise VerifyFailed(f"{where}: entry names or order changed")
    target = path[0]
    identical = 0
    for si, di in zip(s_infos, d_infos):
        if si.compress_type != di.compress_type:
            raise VerifyFailed(f"{where}: member {si.filename!r} compress_type "
                               f"{si.compress_type} -> {di.compress_type}")
        if si.date_time != di.date_time:
            raise VerifyFailed(f"{where}: member {si.filename!r} date_time changed")
        if si.filename == target:
            continue
        if sz.read(si.filename) != dz.read(di.filename):
            raise VerifyFailed(
                f"{where}: member {si.filename!r} changed but only {target!r} was supposed "
                f"to. An archive that still opens can still be a corrupt repack.")
        identical += 1
    proofs.append(f"{where}: {identical}/{len(s_infos)} members byte-identical; order and "
                  f"per-entry compress_type preserved")
    if len(path) > 1:
        proofs += verify_nested(sz.read(target), dz.read(target), path[1:], prefix + [target])
    else:
        old, new = sz.read(target), dz.read(target)
        proofs.append(f"{SEP.join(prefix + [target])}: {human(len(old))} -> "
                      f"{human(len(new))} bytes, "
                      f"md5 {hashlib.md5(old).hexdigest()[:12]} -> "
                      f"{hashlib.md5(new).hexdigest()[:12]}")
    return proofs


# --------------------------------------------------------------------------------------
# listing
# --------------------------------------------------------------------------------------

def list_members(blob: bytes, path: List[str]) -> List[Tuple[str, int, int]]:
    for step in path:
        zf = zipfile.ZipFile(io.BytesIO(blob))
        if step not in zf.namelist():
            raise Refuse(f"member {step!r} not found while descending to list it")
        blob = zf.read(step)
    zf = zipfile.ZipFile(io.BytesIO(blob))
    return [(i.filename, i.file_size, i.compress_type) for i in zf.infolist()]


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="repack_member.py",
        description="Rewrite one member inside a nested archive and prove nothing else moved.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  # what is in there?\n"
            "  python repack_member.py work/<archive>.zip --list\n"
            "  python repack_member.py work/<archive>.zip --list \"<inner>.jar\"\n"
            "\n"
            "  # dry run: writes work/<archive>.zip.new, touches nothing else\n"
            "  python repack_member.py work/<archive>.zip \\\n"
            "      --member \"<inner>.jar!dat/<file>\" \\\n"
            "      --transform tools/rename_items.py:transform \\\n"
            "      --verify    tools/rename_items.py:verify\n"
            "\n"
            "  # commit it (writes a .orig backup once)\n"
            "  python repack_member.py work/<archive>.zip --member \"...\" \\\n"
            "      --transform tools/rename_items.py:transform --apply\n"
            "\n"
            "NOT IDEMPOTENT: to re-run a transform, restore from the .orig backup first.\n"
            "exit codes: 0 ok | 1 refused | 2 I/O error or FAILED verification\n"
        ),
    )
    p.add_argument("archive", help="the outer archive (forward slashes)")
    p.add_argument("--member", metavar="SPEC",
                   help=f"nested member path, levels separated by '{SEP}', "
                        f"e.g. \"<inner>.jar{SEP}dat/<file>\"")
    p.add_argument("--transform", metavar="MOD:FN",
                   help="callable applied to the leaf member: fn(bytes) -> bytes, or "
                        "fn(bytes, ctx) -> bytes")
    p.add_argument("--verify", metavar="MOD:FN",
                   help="callable fn(old_bytes, new_bytes) run after the rewrite. Make it "
                        "RE-PARSE the new bytes; that is the check that catches a parser "
                        "which silently re-segments the file.")
    p.add_argument("--apply", action="store_true",
                   help="overwrite the archive in place (writes a .orig backup once)")
    p.add_argument("--backup-dir", metavar="D",
                   help="where the .orig backup goes (default: beside the archive). Keep it "
                        "OUTSIDE the repository - a backup of licensed content is still "
                        "licensed content.")
    p.add_argument("--out", metavar="PATH",
                   help="explicit output path (default: <archive>.new)")
    p.add_argument("--allow-noop", action="store_true",
                   help="do not fail when the transform changes nothing (rarely what you want)")
    p.add_argument("--force", action="store_true",
                   help="overwrite an existing output, or re-apply over an existing backup")
    p.add_argument("--list", nargs="?", const="", metavar="SPEC",
                   help="list members at the top level, or inside a nested container")
    p.add_argument("--json", action="store_true", help="machine-readable result")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    archive = posix(args.archive)

    try:
        with open(archive, "rb") as fh:
            src_blob = fh.read()
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.list is not None:
        try:
            path = [s for s in args.list.split(SEP) if s]
            rows = list_members(src_blob, path)
        except (Refuse, zipfile.BadZipFile) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps([{"name": n, "size": s, "compress_type": c} for n, s, c in rows],
                             indent=2))
        else:
            here = SEP.join(path) or "<archive>"
            print(f"{here}: {len(rows)} entries")
            for n, s, c in rows:
                kind = {0: "stored", 8: "deflated"}.get(c, f"method{c}")
                print(f"  {s:>10,}  {kind:<8}  {n}")
        return 0

    if not args.member or not args.transform:
        print("error: --member and --transform are both required (or use --list)",
              file=sys.stderr)
        return 1

    path = [s for s in args.member.split(SEP) if s]
    if not path:
        print(f"error: --member is empty. Use e.g. \"<inner>.jar{SEP}dat/<file>\"",
              file=sys.stderr)
        return 1

    try:
        transform = load_callable(args.transform, "transform")
        verifier = load_callable(args.verify, "verify") if args.verify else None
    except Refuse as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 1

    leaf_old: Dict[str, bytes] = {}

    def apply_leaf(data: bytes) -> bytes:
        leaf_old["data"] = data
        return call_transform(transform, data,
                              {"member": path[-1], "path": path, "archive": archive})

    trail: List[Dict[str, Any]] = []
    try:
        dst_blob = rewrite(src_blob, path, apply_leaf, trail)
    except Refuse as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - a user transform can raise anything
        print(f"error: the transform failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    leaf = next((t for t in trail if t["level"] == len(path) - 1), None)  # deepest rewrite
    if leaf and not leaf["changed"] and not args.allow_noop:
        print(f"refusing: the transform returned {path[-1]!r} unchanged "
              f"({human(leaf['old_bytes'])} bytes, md5 {leaf['old_md5']}).\n"
              f"  A silent no-op is the most common way one of these scripts lies to you: "
              f"the archive is rewritten, every check passes, and nothing happened.\n"
              f"  If the member was already patched, restore from your backup first - this "
              f"tool is not idempotent.\n"
              f"  Pass --allow-noop if you really meant it.", file=sys.stderr)
        return 1

    try:
        proofs = verify_nested(src_blob, dst_blob, path)
    except VerifyFailed as exc:
        print(f"VERIFICATION FAILED: {exc}\n  Nothing was written.", file=sys.stderr)
        return 2

    if verifier is not None:
        try:
            # read the leaf back OUT of the rebuilt archive, not from a variable: that is
            # what makes this a verification of the artifact rather than of our intent.
            res = verifier(leaf_old.get("data", b""), _read_nested(dst_blob, path))
        except AssertionError as exc:
            print(f"VERIFICATION FAILED (--verify): {exc}\n  Nothing was written.",
                  file=sys.stderr)
            return 2
        except Exception as exc:  # noqa: BLE001
            print(f"error: --verify raised {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        if isinstance(res, str) and res:
            print(f"VERIFICATION FAILED (--verify): {res}\n  Nothing was written.",
                  file=sys.stderr)
            return 2
        proofs.append(f"--verify {args.verify} passed on the re-parsed member")

    # ---- write ------------------------------------------------------------------------
    if args.apply:
        backup_dir = args.backup_dir or os.path.dirname(os.path.abspath(archive))
        os.makedirs(backup_dir, exist_ok=True)
        backup = posix(os.path.join(backup_dir, os.path.basename(archive) + ".orig"))
        if os.path.exists(backup) and not args.force:
            print(f"refusing: a backup already exists at {backup}, which means --apply has "
                  f"run before.\n  This tool is NOT idempotent: re-running a transform over "
                  f"already-transformed data corrupts it or fails your --verify.\n"
                  f"  Restore first:  cp {backup} {archive}\n"
                  f"  Or pass --force if you know the current state is the original.",
                  file=sys.stderr)
            return 1
        if not os.path.exists(backup):
            shutil.copy2(archive, backup)
            proofs.append(f"backup written: {backup}")
        out_path = archive
    else:
        out_path = posix(args.out or (archive + ".new"))
        if os.path.exists(out_path) and not args.force:
            print(f"error: {out_path} exists. Pass --force or --out.", file=sys.stderr)
            return 2

    tmp = out_path + ".partial"
    with open(tmp, "wb") as fh:
        fh.write(dst_blob)
    shutil.move(tmp, out_path)

    signed = _has_signature(src_blob, path)
    result = {
        "archive": archive, "out": out_path, "member": args.member, "applied": bool(args.apply),
        "trail": trail, "proofs": proofs,
        "src_bytes": len(src_blob), "dst_bytes": len(dst_blob),
        "dst_md5": hashlib.md5(dst_blob).hexdigest(), "jar_signed": signed,
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"archive  {archive}   {human(len(src_blob))} bytes")
        print(f"member   {args.member}")
        print("\nproofs")
        for pf in proofs:
            for i, line in enumerate(ca.wrap(pf, 84)):
                print(f"  {'- ' if i == 0 else '  '}{line}")
        print(f"\nsize {human(len(src_blob))} -> {human(len(dst_blob))} bytes. Size drift is "
              f"expected and proves nothing either way; the member comparison above is the "
              f"proof.")
        if signed:
            print("\nWARNING: this jar carries META-INF signature files. They are now invalid.")
        print(f"\nwrote {out_path}  md5 {result['dst_md5']}")
        if not args.apply:
            print("  (dry run - pass --apply to overwrite the archive itself)")
    return 0


def _read_nested(blob: bytes, path: List[str]) -> bytes:
    for step in path:
        blob = zipfile.ZipFile(io.BytesIO(blob)).read(step)
    return blob


def _has_signature(blob: bytes, path: List[str]) -> bool:
    try:
        inner = _read_nested(blob, path[:-1]) if len(path) > 1 else blob
        names = zipfile.ZipFile(io.BytesIO(inner)).namelist()
    except Exception:  # noqa: BLE001
        return False
    return any(n.startswith("META-INF/") and n.upper().endswith((".SF", ".RSA", ".DSA", ".EC"))
               for n in names)


if __name__ == "__main__":
    sys.exit(main())
