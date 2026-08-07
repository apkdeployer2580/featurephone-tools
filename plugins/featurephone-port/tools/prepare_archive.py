#!/usr/bin/env python3
"""prepare_archive.py - repair a carrier dump into an archive the emulator will boot.

WHAT IT DOES
    Reads <src>, writes a repaired <dst>, and PROVES that everything it did not intend to
    change came through byte-identical.  Four repairs, in this order:

      1. NORMALISE the app descriptor to LF.  CRLF -> LF, lone CR -> LF, drop a UTF-8 BOM,
         guarantee a trailing newline.  This one repair is, empirically, the difference
         between "black screen" and "boots" for most dumps.
      2. RECONCILE the names: after this tool runs, AID == the jar's basename, exactly.
      3. STRIP the previous owner's private state (`--strip-private`).
      4. SYNTHESISE a descriptor when there is none (`--synthesize-descriptor`), including
         wrapping a bare .jar into a package.

    Then it rebuilds the archive entry by entry, preserving each entry's compress_type,
    date_time and attributes, and preserving the original entry ORDER.

THE METHOD IT EMBODIES  (A)
    The descriptor parser in the wie family splits on b'\\n' and slices each value from a
    FIXED INDEX with no trim.  So a CRLF file gives you AID = "XXXX\\r"; the loader then
    opens "XXXX\\r.jar"; the archive has no such member; you get a NotFound unwrap panic
    on the async boot task - AFTER the emulator has already printed that it loaded.  The
    same panic, from the same lookup, is what an honest AID/jar-name mismatch produces.
    Both are name problems, so this tool's job is to make four names agree:

        AID:<X>  ==  <X>.jar  and  client.bin<digits>  and  MClass:<Y> resolvable

    Two of those names are permanent once you ship:
      * PID is the on-device save namespace (<root>/<PID>/db/<record>).  Change it after
        release and every user's saves become unreachable.
      * AID is only the jar lookup key.  Renaming AID and the jar together is safe.
    That asymmetry is why the default reconciliation adopts the jar's real basename as
    AID (one text edit) rather than renaming the payload.

MEASURED FACTS THIS TOOL ENFORCES AS CHECKS, NOT COMMENTS  (A)
    * Rewriting a zip with Python changes its SIZE even when every member is identical -
      different deflate parameters, no original extra fields.  "Same size" is therefore
      NOT a valid sanity check, and this tool never uses it.  It compares member bytes.
    * Entry ORDER is irrelevant to the emulator (it reads the archive into a map), but it
      is preserved anyway so that diffs stay reviewable.
    * Compression METHOD is per entry.  Some members are STORED.  Re-deflating everything
      would still boot, but it destroys the ability to say "only this member changed".
    * Nothing verifies the jar: a jar with only MANIFEST.MF in META-INF/ has no .SF/.RSA,
      so a repacked member is accepted. Whether the GAME checks its own data is separate.
    * This tool IS idempotent - running it on its own output is a no-op. (repack_member.py
      is not; that is a different tool with a different contract.)

KNOWN LIMITS
    * Structural repair only. It cannot know whether MClass names a resolvable class.
    * AID/PID/MClass must be ASCII: they are used as a filename key and a directory name.
    * It never decrypts, never re-encodes text, never touches the guest binary.
    * SKT (.msd) descriptors are trimmed by their own parser, so the CRLF repair is a
      no-op there; the tool says so instead of pretending it fixed something.

EXIT CODES
    0  wrote <dst> and every verification passed
    1  refused: a repair is needed that you must choose (e.g. ambiguous AID)
    2  I/O error, or - the important one - a verification FAILED after writing
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import shutil
import sys
import zipfile
from typing import Any, Dict, List, Optional, Tuple

# Append, never insert(0): a sibling tool is named dis.py, and putting this directory
# ahead of the standard library makes `import inspect` (which imports `dis`) fail.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import classify_archive as ca  # noqa: E402  (same-directory sibling tool)

posix = ca.posix
human = ca.human


class Refuse(Exception):
    """A decision only the user can make."""


class VerifyFailed(Exception):
    """The output does not match the plan. Never ignore this."""


# --------------------------------------------------------------------------------------
# descriptor surgery, at the byte level
# --------------------------------------------------------------------------------------

def normalise_descriptor(raw: bytes) -> Tuple[bytes, Dict[str, int]]:
    """CRLF -> LF, lone CR -> LF, strip BOM, guarantee a trailing LF."""
    stats = {"crlf": 0, "lone_cr": 0, "bom": 0, "added_final_lf": 0}
    out = raw
    if out.startswith(b"\xef\xbb\xbf"):
        out = out[3:]
        stats["bom"] = 1
    stats["crlf"] = out.count(b"\r\n")
    out = out.replace(b"\r\n", b"\n")
    stats["lone_cr"] = out.count(b"\r")
    out = out.replace(b"\r", b"\n")
    if out and not out.endswith(b"\n"):
        out += b"\n"
        stats["added_final_lf"] = 1
    return out, stats


def set_descriptor_key(raw: bytes, key: str, value: str) -> bytes:
    """Replace (or append) `KEY:value`, preserving every other line verbatim."""
    probe = key.encode("ascii") + b":"
    new_line = probe + value.encode("ascii")
    lines = raw.split(b"\n")
    replaced = False
    for i, line in enumerate(lines):
        if line.startswith(probe):
            lines[i] = new_line
            replaced = True
            break
    if not replaced:
        # append before any trailing empty element produced by the final newline
        insert_at = len(lines) - 1 if lines and lines[-1] == b"" else len(lines)
        lines.insert(insert_at, new_line)
    return b"\n".join(lines)


def loader_view(raw: bytes) -> Dict[str, str]:
    """Exactly what the loader's own parser would extract. No trimming."""
    out: Dict[str, str] = {}
    for line in raw.split(b"\n"):
        for key, idx in ca.DESCRIPTOR_KEYS:
            if line.startswith(key.encode() + b":"):
                out[key] = line[idx:].decode("latin-1")
    return out


def synthesize_descriptor(aid: str, pid: str, mclass: str) -> bytes:
    """The empirically minimal descriptor: three lines, LF, nothing else."""
    return f"PID:{pid}\nAID:{aid}\nMClass:{mclass}\n".encode("ascii")


def _ascii_or_refuse(value: str, key: str) -> str:
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        raise Refuse(f"--set-{key.lower()} must be ASCII: {key} is used as a filename key "
                     f"(AID) or an on-disk directory name (PID), got {value!r}")
    if value != value.strip() or not value:
        raise Refuse(f"--set-{key.lower()}: {value!r} has leading/trailing whitespace or is "
                     f"empty. The loader never trims - the whitespace would end up in the "
                     f"filename it looks up.")
    if key == "AID" and any(ch in value for ch in '/\\:*?"<>|'):
        raise Refuse(f"--set-aid: {value!r} contains a character that cannot appear in an "
                     f"archive member name (the loader opens '<AID>.jar').")
    return value


# --------------------------------------------------------------------------------------
# the plan
# --------------------------------------------------------------------------------------

class Plan:
    def __init__(self) -> None:
        self.wrap_bare_jar = False           # src is a jar; build a package around it
        self.descriptor_entry: Optional[str] = None
        self.descriptor_new: Optional[bytes] = None
        self.descriptor_is_new_entry = False
        self.descriptor_stats: Dict[str, int] = {}
        self.rename: Dict[str, str] = {}     # old entry name -> new entry name
        self.drop: List[str] = []
        self.add: Dict[str, bytes] = {}
        self.notes: List[str] = []
        self.aid: Optional[str] = None

    @property
    def changes_anything(self) -> bool:
        return bool(self.descriptor_new is not None or self.rename or self.drop
                    or self.add or self.wrap_bare_jar)


def build_plan(src: str, args: argparse.Namespace) -> Tuple[Plan, Optional[zipfile.ZipFile],
                                                            bytes]:
    with open(src, "rb") as fh:
        blob = fh.read()
    if blob[:2] != b"PK":
        raise Refuse(f"{posix(src)} is not a zip/jar (no PK header). This tool repairs "
                     f"archives; a raw payload has nothing to repair.")
    zf = zipfile.ZipFile(io.BytesIO(blob))
    names = zf.namelist()
    plan = Plan()

    magic = next((m for m in (ca.DESCRIPTOR_ENTRIES["ktf"], ca.DESCRIPTOR_ENTRIES["lgt"])
                  if m in names), None)
    msd = [n for n in names if n.lower().endswith(".msd")]
    jars = [n for n in names if n.lower().endswith(".jar")]
    is_bare_jar = magic is None and not msd and any(
        n.startswith(ca.CLIENT_BIN_PREFIX) or n == "binary.mod" for n in names)

    if msd and not magic:
        plan.notes.append(
            "SKT (.msd): its parser trims values, so the CRLF repair does not apply. "
            "Nothing to normalise.")

    # ---- bare jar: wrap it into a package --------------------------------------------
    if is_bare_jar:
        if not args.synthesize_descriptor:
            raise Refuse(
                f"{posix(src)} is a bare jar, not a package: there is no descriptor to "
                f"repair, and a bare jar has no MClass, so it loads and then dies with "
                f'FatalError("Main class not found").\n'
                f"  Wrap it: --synthesize-descriptor --set-aid <X> --set-pid <Y> "
                f"--set-mclass <Z>")
        plan.wrap_bare_jar = True

    # ---- descriptor ------------------------------------------------------------------
    if args.synthesize_descriptor and magic is None:
        missing = [f"--set-{k}" for k, v in
                   (("aid", args.set_aid), ("pid", args.set_pid), ("mclass", args.set_mclass))
                   if not v]
        if missing:
            raise Refuse(
                "--synthesize-descriptor needs " + ", ".join(missing) + ".\n"
                "  AID    is the jar lookup key; the loader opens '<AID>.jar'.\n"
                "  PID    is the on-device save namespace and is PERMANENT after release.\n"
                "  MClass is the main class name and has no other source - it cannot be "
                "recovered from the jar. Guess wrong and loadClass returns null, which "
                "surfaces as an Option::unwrap() on None panic in the JVM value code, not "
                "as a helpful message.")
        aid = _ascii_or_refuse(args.set_aid, "AID")
        pid = _ascii_or_refuse(args.set_pid, "PID")
        mcl = _ascii_or_refuse(args.set_mclass, "MClass")
        plan.descriptor_entry = ca.DESCRIPTOR_ENTRIES["ktf"]
        plan.descriptor_new = synthesize_descriptor(aid, pid, mcl)
        plan.descriptor_is_new_entry = True
        plan.aid = aid
        if not plan.wrap_bare_jar:
            # The entry does not exist in the source, so the rebuild loop will never see
            # it. It has to be added explicitly or the repair silently does nothing.
            plan.add[plan.descriptor_entry] = plan.descriptor_new
        plan.notes.append(
            f"synthesised a 3-line '{plan.descriptor_entry}' (PID/AID/MClass). That is "
            f"empirically sufficient to boot - the icon and catalog entries a real package "
            f"carries are read by nobody.")
    elif magic is not None:
        raw = zf.read(magic)
        new, stats = normalise_descriptor(raw)
        plan.descriptor_entry = magic
        plan.descriptor_stats = stats
        for key, val in (("AID", args.set_aid), ("PID", args.set_pid),
                         ("MClass", args.set_mclass)):
            if val:
                new = set_descriptor_key(new, key, _ascii_or_refuse(val, key))
        plan.descriptor_new = new if new != raw else None
        if stats["crlf"] or stats["lone_cr"]:
            plan.notes.append(
                f"descriptor line endings: {stats['crlf']} CRLF + {stats['lone_cr']} lone CR "
                f"-> LF. This is THE repair; on KTF/LGT a single stray CR is enough to make "
                f"the archive unbootable.")
        if stats["bom"]:
            plan.notes.append("dropped a UTF-8 BOM: it hides whichever descriptor key is first.")
    elif not is_bare_jar and not msd:
        raise Refuse(
            f"{posix(src)} has no '__adf__', no 'app_info' and no '*.msd', so no backend "
            f"will claim it.\n  If the payload is intact, synthesise a descriptor: "
            f"--synthesize-descriptor --set-aid <X> --set-pid <Y> --set-mclass <Z>")

    # ---- name reconciliation ---------------------------------------------------------
    if plan.descriptor_entry in (ca.DESCRIPTOR_ENTRIES["ktf"], ca.DESCRIPTOR_ENTRIES["lgt"]) \
            and not plan.wrap_bare_jar:
        current = plan.descriptor_new if plan.descriptor_new is not None else zf.read(magic)
        aid = loader_view(current).get("AID")
        if args.set_aid:
            aid = args.set_aid
        if aid is None:
            if len(jars) == 1:
                aid = os.path.splitext(jars[0])[0]
                plan.descriptor_new = set_descriptor_key(current, "AID", _ascii_or_refuse(aid, "AID"))
                plan.notes.append(f"no AID line; adopted the only jar's basename: {aid!r}")
            else:
                raise Refuse("the descriptor has no AID and the archive does not contain "
                             "exactly one .jar, so the jar name cannot be inferred. "
                             "Pass --set-aid.")
        plan.aid = aid
        want = f"{aid}.jar"
        if want not in names:
            if args.set_aid and len(jars) == 1:
                plan.rename[jars[0]] = want
                plan.notes.append(
                    f"--set-aid forced AID={aid!r}, so the jar entry {jars[0]!r} is renamed to "
                    f"{want!r}. Content is untouched; only the member name changes.")
            elif len(jars) == 1:
                real = os.path.splitext(jars[0])[0]
                plan.descriptor_new = set_descriptor_key(
                    plan.descriptor_new if plan.descriptor_new is not None else zf.read(magic),
                    "AID", _ascii_or_refuse(real, "AID"))
                plan.aid = real
                plan.notes.append(
                    f"AID {aid!r} did not match the jar {jars[0]!r}; adopted the jar's real "
                    f"basename {real!r} instead. One text edit beats renaming the payload, "
                    f"and AID is only a lookup key.")
            else:
                raise Refuse(
                    f"AID={aid!r} means the loader opens {want!r}, which is not in the "
                    f"archive, and there are {len(jars)} .jar entries ({jars}) so the right "
                    f"one is ambiguous. Pass --set-aid <basename-of-the-real-jar>.")

    # ---- private entries -------------------------------------------------------------
    private = [n for n in names if n.startswith("P/")]
    savelike = [n for n in names if n not in private and "/" not in n and not n.endswith("/")
                and any(tok in n.lower() for tok in ("save", "record"))]
    victims = private + savelike
    if victims:
        if args.strip_private:
            plan.drop.extend(victims)
            plan.notes.append(
                f"stripping {len(victims)} private entries. Reason: they are the previous "
                f"owner's personal data. They do NOT seed a new user's save database - "
                f"packaged-database reads resolve through the classloader (jar resources) "
                f"while 'P/' entries land in the virtual filesystem, a different namespace. "
                f"That was measured, against a documented claim to the contrary.")
        else:
            plan.notes.append(
                f"WARNING: {len(victims)} entries look like somebody's saved state "
                f"({victims[:4]}...). They are being kept. Pass --strip-private before you "
                f"distribute this to anyone.")
    return plan, zf, blob


# --------------------------------------------------------------------------------------
# apply
# --------------------------------------------------------------------------------------

def rebuild(zf: zipfile.ZipFile, plan: Plan) -> bytes:
    """Entry-by-entry rebuild. Order, compress_type, dates and attrs all preserved."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as dst:
        # new entries go first: a descriptor at the top of the listing reads naturally,
        # and entry order is free as far as the emulator is concerned.
        for name, data in plan.add.items():
            zi = zipfile.ZipInfo(name, date_time=_now_dos())
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            dst.writestr(zi, data)
        for info in zf.infolist():
            if info.filename in plan.drop:
                continue
            data = zf.read(info.filename)
            name = plan.rename.get(info.filename, info.filename)
            if info.filename == plan.descriptor_entry and plan.descriptor_new is not None:
                data = plan.descriptor_new
            _write_like(dst, info, name, data)
    return out.getvalue()


def wrap_jar(jar_bytes: bytes, plan: Plan) -> bytes:
    """Build a minimal package around a bare jar: descriptor + '<AID>.jar'."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as dst:
        for name, data in ((plan.descriptor_entry, plan.descriptor_new),
                           (f"{plan.aid}.jar", jar_bytes)):
            zi = zipfile.ZipInfo(name, date_time=_now_dos())
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            dst.writestr(zi, data)
    return out.getvalue()


def _write_like(dst: zipfile.ZipFile, info: zipfile.ZipInfo, name: str, data: bytes) -> None:
    zi = zipfile.ZipInfo(name, date_time=info.date_time)
    zi.compress_type = info.compress_type      # some members are STORED - keep them STORED
    zi.external_attr = info.external_attr
    zi.internal_attr = info.internal_attr
    zi.create_system = info.create_system
    zi.comment = info.comment
    dst.writestr(zi, data)


def _now_dos() -> Tuple[int, int, int, int, int, int]:
    import time
    t = time.localtime()
    return (max(t.tm_year, 1980), t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec)


# --------------------------------------------------------------------------------------
# verify - the part that makes this tool trustworthy
# --------------------------------------------------------------------------------------

def verify(src_blob: bytes, dst_blob: bytes, plan: Plan) -> List[str]:
    """Prove the output differs from the input in exactly the intended ways."""
    proofs: List[str] = []
    sz = zipfile.ZipFile(io.BytesIO(src_blob))
    dz = zipfile.ZipFile(io.BytesIO(dst_blob))
    s_infos = {i.filename: i for i in sz.infolist()}
    d_infos = {i.filename: i for i in dz.infolist()}

    if plan.wrap_bare_jar:
        jar_name = f"{plan.aid}.jar"
        if jar_name not in d_infos:
            raise VerifyFailed(f"wrapped package has no {jar_name!r}")
        if dz.read(jar_name) != src_blob:
            raise VerifyFailed("the wrapped jar is not byte-identical to the source file")
        proofs.append(f"wrapped jar {jar_name!r} is byte-identical to the source "
                      f"({human(len(src_blob))} bytes)")
    else:
        # 1. entry set
        expected = [plan.rename.get(n, n) for n in s_infos if n not in plan.drop]
        expected += list(plan.add)
        if sorted(expected) != sorted(d_infos):
            missing = sorted(set(expected) - set(d_infos))
            extra = sorted(set(d_infos) - set(expected))
            raise VerifyFailed(f"entry set differs. missing={missing} unexpected={extra}")
        proofs.append(f"entry set matches the plan ({len(d_infos)} entries)")

        # 2. order preserved
        s_order = [plan.rename.get(i.filename, i.filename) for i in sz.infolist()
                   if i.filename not in plan.drop]
        d_order = [i.filename for i in dz.infolist() if i.filename not in plan.add]
        if s_order != d_order:
            raise VerifyFailed(f"entry ORDER changed:\n  src {s_order}\n  dst {d_order}")
        proofs.append("entry order preserved")

        # 3. member-wise bytes and compression, for everything not intentionally changed
        changed_ok = {plan.descriptor_entry} if plan.descriptor_new is not None else set()
        identical = 0
        for old_name, info in s_infos.items():
            if old_name in plan.drop:
                continue
            new_name = plan.rename.get(old_name, old_name)
            if old_name in changed_ok:
                continue
            if sz.read(old_name) != dz.read(new_name):
                raise VerifyFailed(
                    f"member {old_name!r} changed but was not supposed to. This is the check "
                    f"that matters: a repack that alters an unrelated member is a corrupt "
                    f"repack, even though the archive still opens.")
            if d_infos[new_name].compress_type != info.compress_type:
                raise VerifyFailed(
                    f"member {new_name!r}: compress_type {info.compress_type} -> "
                    f"{d_infos[new_name].compress_type}. Per-entry compression must be "
                    f"copied, not chosen.")
            if d_infos[new_name].date_time != info.date_time:
                raise VerifyFailed(f"member {new_name!r}: date_time changed")
            identical += 1
        proofs.append(f"{identical} members byte-identical, with compress_type and date_time "
                      f"preserved")
        proofs.append(f"size {human(len(src_blob))} -> {human(len(dst_blob))} bytes "
                      f"(size drift is EXPECTED and is not evidence of anything: Python's "
                      f"deflate parameters differ from the original packer's)")

    # 4. the repair actually took
    desc_entry = plan.descriptor_entry
    if desc_entry and desc_entry in d_infos:
        raw = dz.read(desc_entry)
        n_cr = raw.count(b"\r")
        if n_cr:
            raise VerifyFailed(f"{desc_entry!r} still contains {n_cr} CR bytes")
        lv = loader_view(raw)
        for key, val in lv.items():
            if val != val.strip():
                raise VerifyFailed(f"{desc_entry!r}: the loader would read {key}={val!r}, "
                                   f"which still carries whitespace")
        proofs.append(f"{desc_entry!r}: 0 CR bytes; loader reads "
                      + ", ".join(f"{k}={v!r}" for k, v in sorted(lv.items())))
        aid = lv.get("AID")
        if aid is not None:
            want = f"{aid}.jar"
            if want not in d_infos:
                raise VerifyFailed(f"AID={aid!r} but {want!r} is not in the output")
            proofs.append(f"AID {aid!r} == jar basename ({want!r} present)")

    # 5. nothing private survived a strip
    if plan.drop:
        leftover = [n for n in d_infos if n.startswith("P/")]
        if leftover:
            raise VerifyFailed(f"private entries survived: {leftover}")
        proofs.append(f"{len(plan.drop)} private entries removed, none left under 'P/'")
    return proofs


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="prepare_archive.py",
        description="Repair a carrier dump into a bootable archive, and prove that nothing "
                    "else changed.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  # the common case: normalise line endings, reconcile names, drop saved state\n"
            "  python prepare_archive.py dumps/<archive>.zip work/<archive>.zip "
            "--strip-private\n"
            "\n"
            "  # see what it would do, touch nothing\n"
            "  python prepare_archive.py dumps/<archive>.zip work/out.zip --dry-run\n"
            "\n"
            "  # a bare .jar with no descriptor: wrap it into a package\n"
            "  python prepare_archive.py game.jar work/game.zip --synthesize-descriptor \\\n"
            "      --set-aid <X> --set-pid <Y> --set-mclass <Z>\n"
            "\n"
            "PID is permanent: it is the on-device save directory. Choose it once.\n"
            "exit codes: 0 ok | 1 needs a decision from you | 2 I/O error or FAILED "
            "verification\n"
        ),
    )
    p.add_argument("src", help="the dump to repair (forward slashes)")
    p.add_argument("dst", help="where to write the repaired archive")
    p.add_argument("--strip-private", action="store_true",
                   help="remove 'P/*' and save-looking entries (somebody's personal data)")
    p.add_argument("--synthesize-descriptor", action="store_true",
                   help="write a 3-line descriptor when there is none; also wraps a bare .jar "
                        "into a package. Requires --set-aid/--set-pid/--set-mclass.")
    p.add_argument("--set-aid", metavar="X", help="force AID (the loader opens '<AID>.jar')")
    p.add_argument("--set-pid", metavar="Y",
                   help="force PID (the on-device save namespace - PERMANENT after release)")
    p.add_argument("--set-mclass", metavar="Z", help="force MClass (the main class name)")
    p.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    p.add_argument("--force", action="store_true", help="allow overwriting an existing <dst>")
    p.add_argument("--encoding", action="append", metavar="ENC",
                   help="descriptor encoding to try when re-classifying (repeatable)")
    p.add_argument("--json", action="store_true", help="machine-readable result")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    encodings = args.encoding or ["euc-kr", "shift_jis", "utf-8"]
    src, dst = posix(args.src), posix(args.dst)

    if os.path.abspath(src) == os.path.abspath(dst):
        print("error: <src> and <dst> are the same file. This tool never edits in place.",
              file=sys.stderr)
        return 2
    if os.path.exists(dst) and not (args.force or args.dry_run):
        print(f"error: {dst} exists. Pass --force to overwrite.", file=sys.stderr)
        return 2

    try:
        plan, zf, src_blob = build_plan(src, args)
    except Refuse as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 1
    except (OSError, zipfile.BadZipFile) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    report: Dict[str, Any] = {
        "src": src, "dst": dst, "dry_run": bool(args.dry_run),
        "descriptor_entry": plan.descriptor_entry,
        "descriptor_stats": plan.descriptor_stats,
        "descriptor_rewritten": plan.descriptor_new is not None,
        "renames": plan.rename, "dropped": plan.drop, "aid": plan.aid,
        "wrap_bare_jar": plan.wrap_bare_jar, "notes": plan.notes,
    }

    if not args.json:
        print(f"src  {src}   {human(len(src_blob))} bytes  md5 "
              f"{hashlib.md5(src_blob).hexdigest()}")
        print(f"dst  {dst}")
        print("\nplan")
        if not plan.changes_anything:
            print("  (nothing to do - this archive is already normalised)")
        if plan.descriptor_new is not None:
            verb = "create " if plan.descriptor_is_new_entry else "rewrite"
            stats = f"  {plan.descriptor_stats}" if plan.descriptor_stats else ""
            print(f"  {verb} descriptor {plan.descriptor_entry!r}{stats}")
        for old, new in plan.rename.items():
            print(f"  rename {old!r} -> {new!r}")
        for n in plan.drop:
            print(f"  drop   {n!r}")
        if plan.wrap_bare_jar:
            print(f"  wrap the bare jar into a package as '{plan.aid}.jar'")
        for note in plan.notes:
            for i, line in enumerate(ca.wrap(note, 84)):
                print(f"  {'note: ' if i == 0 else '      '}{line}")

    if args.dry_run:
        if args.json:
            print(_json(report))
        else:
            print("\ndry run: nothing written.")
        return 0

    try:
        dst_blob = wrap_jar(src_blob, plan) if plan.wrap_bare_jar else rebuild(zf, plan)
    except Exception as exc:  # noqa: BLE001
        print(f"error: rebuild failed: {exc}", file=sys.stderr)
        return 2

    try:
        proofs = verify(src_blob, dst_blob, plan)
    except VerifyFailed as exc:
        print(f"\nVERIFICATION FAILED: {exc}\n  Nothing was written.", file=sys.stderr)
        return 2

    parent = os.path.dirname(os.path.abspath(dst))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = dst + ".partial"
    with open(tmp, "wb") as fh:
        fh.write(dst_blob)
    shutil.move(tmp, dst)

    report["proofs"] = proofs
    report["dst_bytes"] = len(dst_blob)
    report["dst_md5"] = hashlib.md5(dst_blob).hexdigest()

    # final gate: re-run the classifier on what we actually wrote
    blocking: List[str] = []
    try:
        c = ca.classify(dst, encodings)
        blocking = [i.code for i in c.blocking]
        report["classify_verdict"] = "blocked" if blocking else "ok"
        report["classify_blocking"] = blocking
    except ValueError as exc:
        report["classify_verdict"] = f"error: {exc}"
        blocking = ["classify_failed"]

    if args.json:
        print(_json(report))
    else:
        print("\nproofs")
        for pf in proofs:
            for i, line in enumerate(ca.wrap(pf, 84)):
                print(f"  {'- ' if i == 0 else '  '}{line}")
        print(f"\nwrote {dst}  {human(len(dst_blob))} bytes  md5 {report['dst_md5']}")
        if blocking:
            print(f"\nWARNING: the repaired archive still has blocking issues: {blocking}")
            print(f"  Run: python classify_archive.py {dst}")
        else:
            print("re-classified: no blocking issues. Boot it headless next.")
    return 1 if blocking else 0


def _json(obj: Dict[str, Any]) -> str:
    import json
    return json.dumps(obj, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    sys.exit(main())
