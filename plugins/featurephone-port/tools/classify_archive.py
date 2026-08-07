#!/usr/bin/env python3
"""classify_archive.py - diagnose a carrier download package before you try to boot it.

WHAT IT DOES
    Takes a carrier download package (.zip / .kdp / whatever the phone stored), a bare
    .jar, or a .jad, and tells you:

      * which emulator backend will claim it (KTF / LGT / SKT / J2ME) and WHY,
      * what the app descriptor says (AID / PID / MClass and every other key),
      * how many CR bytes are in that descriptor - the single most common reason a dump
        does not boot,
      * the jar it points at, whether that jar actually exists under that exact name,
      * the native guest member and the BSS size parsed out of its filename,
      * whether the guest is AOT-compiled ARM or JVM bytecode - which decides the entire
        rest of the project,
      * every name-agreement problem it can see, each with the exact failure you will get
        if you ignore it.

    This is the FIRST tool to run.  It changes nothing.  Its output is a diagnosis.

THE METHOD IT EMBODIES  (A)
    Platform detection in the wie family is not heuristic and not content-based: it is
    the presence of one magic filename in the outer archive.  The dispatcher tries the
    backends in a fixed order and takes the first that claims the file:

        outer zip contains "__adf__"                    -> KTF   (WIPI)
        outer zip contains "app_info"                   -> LGT   (WIPI)
        outer zip contains any "*.msd"                  -> SKT   (SKVM)
        bare .jar with an entry starting "client.bin"   -> KTF
        bare .jar containing "binary.mod"               -> LGT
        bare .jar whose first 8 bytes are 20 00 * 7     -> SKT
        .jad, or anything else                          -> J2ME MIDlet (last-resort)

    So the whole classification is a namelist lookup.  Everything else this tool prints
    is a consistency check on the four names the KTF boot path must agree on:

        AID:<X>            in the descriptor
        <X>.jar            an entry in the outer zip, matched EXACTLY
        client.bin<digits> at the jar root, digits = BSS size
        MClass:<Y>         a class the guest image's own class lookup can resolve

    The descriptor parser in the emulator splits on '\\n' and slices from a fixed index
    with NO trimming.  A CRLF descriptor therefore yields AID = "XXXX\\r", the loader
    asks for "XXXX\\r.jar", and the archive has no such member.  That is why CR count is
    the headline number here.  (SKT's .msd parser trims, so SKT is immune.)

KNOWN LIMITS
    * Only structural checks.  It cannot tell you whether MClass names a class the guest
      can actually resolve - nothing but running it can.
    * The AID == jar-basename rule is CONFIRMED for KTF.  For LGT the same descriptor
      keys are parsed but the jar-naming rule is reported as INFERRED, not asserted.
    * It reads the whole archive into memory. Fine for phone-era payloads (a few MB).
    * It never decrypts anything.  An encrypted member is reported as such and skipped.

EXIT CODES
    0  classified, no blocking issue found
    1  classified, but at least one blocking issue - it will not boot as-is
    2  could not read or could not classify the input
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import zipfile
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

CLIENT_BIN_PREFIX = "client.bin"
SKT_JAR_MAGIC = b"\x20\x00\x00\x00\x00\x00\x00\x00"
DESCRIPTOR_ENTRIES = {"ktf": "__adf__", "lgt": "app_info"}

# Descriptor keys the loader actually consumes, with the exact slice index it uses.
# The index matters: the parser does line[len(key):] with no trim.
DESCRIPTOR_KEYS = (("AID", 4), ("PID", 4), ("MClass", 7))

# Platform display names.
PLATFORMS = {
    "ktf": "KTF (WIPI)",
    "lgt": "LGT (WIPI)",
    "skt": "SKT (SKVM)",
    "j2me": "J2ME / MIDP MIDlet",
}

# Loader constants that are a property of the emulator, not of the title.  Only KTF is
# pinned here because that is the one this plugin's address model is written against;
# always verify against your own loader crate's load(data, BASE, ...) call.
IMAGE_BASE_BY_PLATFORM = {"ktf": 0x100000}

BLOCKING, WARNING, INFO = "blocking", "warning", "info"


class Issue:
    __slots__ = ("severity", "code", "message", "symptom", "fix")

    def __init__(self, severity: str, code: str, message: str,
                 symptom: str = "", fix: str = ""):
        self.severity = severity
        self.code = code
        self.message = message
        self.symptom = symptom
        self.fix = fix

    def to_dict(self) -> Dict[str, str]:
        return {"severity": self.severity, "code": self.code, "message": self.message,
                "symptom": self.symptom, "fix": self.fix}


def posix(p: str) -> str:
    return str(p).replace("\\", "/")


def human(n: int) -> str:
    return f"{n:,}"


def _decode_descriptor(raw: bytes, encodings: List[str]) -> Tuple[str, str]:
    """Return (text, encoding_used). Falls back to latin-1, which never fails."""
    for enc in encodings:
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin-1"), "latin-1 (fallback; bytes preserved, text may be wrong)"


def parse_descriptor(raw: bytes, encodings: List[str]) -> Dict[str, Any]:
    """Mirror the loader's parser exactly, then add everything a human wants to see.

    The loader: for each line split on b'\\n', if it starts with b"AID:" take line[4:]
    verbatim.  No trim, no strip.  We reproduce that (`*_raw`) AND report the trimmed
    value, so a whitespace problem is visible as a difference between the two.
    """
    info: Dict[str, Any] = {
        "bytes": len(raw),
        "cr": raw.count(b"\r"),
        "lf": raw.count(b"\n"),
        "bom": raw.startswith(b"\xef\xbb\xbf"),
        "keys": {},
        "loader_values": {},
        "raw_repr": {},
    }
    text, enc = _decode_descriptor(raw, encodings)
    info["encoding_used"] = enc

    for line in raw.split(b"\n"):
        for key, idx in DESCRIPTOR_KEYS:
            probe = key.encode() + b":"
            if line.startswith(probe):
                value_raw = line[idx:]
                decoded, _ = _decode_descriptor(value_raw, encodings)
                info["loader_values"][key] = decoded
                info["raw_repr"][key] = repr(value_raw)

    # every key:value line, for the human
    for line in text.split("\n"):
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k = k.strip("﻿")
        if k and not k.startswith("#"):
            info["keys"].setdefault(k, v.rstrip("\r"))
    return info


def _zip_from_bytes(blob: bytes, what: str) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"{what} is not a readable zip archive: {exc}")


def _compress_summary(zf: zipfile.ZipFile) -> Dict[str, int]:
    names = {zipfile.ZIP_STORED: "stored", zipfile.ZIP_DEFLATED: "deflated",
             zipfile.ZIP_BZIP2: "bzip2", zipfile.ZIP_LZMA: "lzma"}
    c = Counter(names.get(i.compress_type, f"method{i.compress_type}") for i in zf.infolist())
    return dict(c)


def _top_dirs(names: List[str]) -> Dict[str, int]:
    c: Counter = Counter()
    for n in names:
        if n.endswith("/"):
            continue
        c[n.split("/")[0] + "/" if "/" in n else "(root)"] += 1
    return dict(c.most_common())


def parse_bss_size(member_name: str) -> Tuple[Optional[int], Optional[str]]:
    """`client.bin<N>` -> N.  Returns (value, error_text_the_emulator_would_print)."""
    suffix = member_name[len(CLIENT_BIN_PREFIX):]
    try:
        return int(suffix), None
    except ValueError:
        reason = ("cannot parse integer from empty string" if suffix == ""
                  else f"invalid digit in {suffix!r}")
        return None, (f'FatalError("Invalid bss_size in filename {member_name}: {reason}")')


# --------------------------------------------------------------------------------------
# the classifier
# --------------------------------------------------------------------------------------

class Classification:
    def __init__(self, path: str):
        self.path = posix(path)
        self.issues: List[Issue] = []
        self.data: Dict[str, Any] = {"archive": self.path}

    def add(self, severity: str, code: str, message: str, symptom: str = "", fix: str = "") -> None:
        self.issues.append(Issue(severity, code, message, symptom, fix))

    @property
    def blocking(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == BLOCKING]

    def to_dict(self) -> Dict[str, Any]:
        d = dict(self.data)
        d["issues"] = [i.to_dict() for i in self.issues]
        d["verdict"] = "blocked" if self.blocking else "ok"
        return d


def classify(path: str, encodings: List[str], want_list: bool = False) -> Classification:
    c = Classification(path)
    try:
        with open(path, "rb") as fh:
            blob = fh.read()
    except OSError as exc:
        raise ValueError(f"cannot read {posix(path)}: {exc}")
    c.data["bytes"] = len(blob)
    c.data["md5"] = hashlib.md5(blob).hexdigest()

    ext = os.path.splitext(path)[1].lower()

    # ---- .jad: a text descriptor that names a jar sitting beside it -------------------
    if ext == ".jad" or (not blob[:2] == b"PK" and b"MIDlet-1:" in blob[:8192]):
        return _classify_jad(c, blob, encodings)

    if blob[:2] != b"PK":
        if blob[:8] == SKT_JAR_MAGIC:
            c.data["platform"] = "skt"
            c.data["detected_by"] = "first 8 bytes are 20 00 00 00 00 00 00 00 (SKT bare payload)"
            c.data["container"] = "raw"
            c.add(WARNING, "skt_raw_payload",
                  "This is an SKT payload, not a zip. The SKT backend takes it directly; "
                  "there is no descriptor to repair.")
            return c
        raise ValueError(
            f"{posix(path)} is neither a zip (no PK header), a .jad, nor a recognised SKT "
            f"payload. First 8 bytes: {blob[:8].hex(' ')}"
        )

    zf = _zip_from_bytes(blob, posix(path))
    names = zf.namelist()
    c.data["container"] = "zip"
    c.data["entries"] = len(names)
    c.data["compression"] = _compress_summary(zf)
    if want_list:
        c.data["entry_list"] = [
            {"name": i.filename, "size": i.file_size, "compress_type": i.compress_type}
            for i in zf.infolist()
        ]

    if "stored" in c.data["compression"] and len(c.data["compression"]) > 1:
        c.add(INFO, "mixed_compression",
              f"mixed compression methods present ({c.data['compression']}). Any repack must "
              f"copy each entry's compress_type; do not re-deflate everything.")

    # ---- detection, in the dispatcher's own order -------------------------------------
    platform, detected_by = _detect_zip(zf, names, blob, c)
    c.data["platform"] = platform
    c.data["platform_name"] = PLATFORMS.get(platform, platform)
    c.data["detected_by"] = detected_by

    if platform is None:
        c.add(BLOCKING, "unknown_format",
              "No backend claims this archive: no '__adf__', no 'app_info', no '*.msd'.",
              symptom='the loader reports "Unknown archive format" (clean, at load time)',
              fix="If you have a descriptor from elsewhere, add it; otherwise this is not a "
                  "supported dump. A 3-line descriptor can be synthesised - see "
                  "prepare_archive.py --synthesize-descriptor.")
        return c

    if c.data.get("bare_jar"):
        c.data["container"] = "jar"
        _analyse_bare_jar(c, zf, names, platform)
    elif platform in ("ktf", "lgt"):
        _analyse_wipi(c, zf, names, platform, encodings)
    elif platform == "skt":
        _analyse_skt(c, zf, names, encodings)
    else:
        _analyse_j2me_jar(c, zf, names)

    _check_private_entries(c, names, zf)
    return c


def _detect_zip(zf: zipfile.ZipFile, names: List[str], blob: bytes,
                c: Classification) -> Tuple[Optional[str], str]:
    if DESCRIPTOR_ENTRIES["ktf"] in names:
        if DESCRIPTOR_ENTRIES["lgt"] in names:
            c.add(WARNING, "ambiguous_descriptors",
                  "both '__adf__' and 'app_info' are present. The dispatcher tries KTF "
                  "first, so KTF wins - but check which descriptor is the real one.")
        return "ktf", "outer zip entry '__adf__'"
    if DESCRIPTOR_ENTRIES["lgt"] in names:
        return "lgt", "outer zip entry 'app_info'"
    msd = [n for n in names if n.lower().endswith(".msd")]
    if msd:
        return "skt", f"outer zip entry '{msd[0]}' (*.msd)"

    # nothing claimed it: is the descriptor merely misplaced?
    for magic in ("__adf__", "app_info"):
        buried = [n for n in names if n.rsplit("/", 1)[-1] == magic and n != magic]
        if buried:
            c.add(BLOCKING, "descriptor_not_at_root",
                  f"found '{buried[0]}' but the loader looks up the EXACT key '{magic}' at "
                  f"the archive root. A nested descriptor is invisible to it.",
                  symptom='"Unknown archive format"',
                  fix=f"re-root the archive so '{magic}' is a top-level entry")

    # the input may itself be a bare jar (the .jar dispatch path, not the .zip one)
    jarish = [n for n in names if n.startswith(CLIENT_BIN_PREFIX)]
    if jarish:
        c.add(BLOCKING, "bare_jar_no_descriptor",
              f"this is a bare KTF jar ('{jarish[0]}' at the root), not a package. It will "
              f"LOAD - the jar probe succeeds - and then die, because MClass has no other "
              f"source than a descriptor.",
              symptom='FatalError("Main class not found")',
              fix="prepare_archive.py <src> <dst> --synthesize-descriptor --set-aid <X> "
                  "--set-pid <Y> --set-mclass <Z>   (3 lines is enough)")
        c.data["bare_jar"] = True
        return "ktf", (f"no descriptor, but entry '{jarish[0]}' starts with "
                       f"'{CLIENT_BIN_PREFIX}' - BARE KTF JAR")
    if "binary.mod" in names:
        c.add(BLOCKING, "bare_jar_no_descriptor",
              "this is a bare LGT jar ('binary.mod' inside), not a package. No descriptor "
              "means no MClass.",
              fix="prepare_archive.py --synthesize-descriptor")
        c.data["bare_jar"] = True
        return "lgt", "no descriptor, but the jar contains 'binary.mod' - bare LGT jar"
    if blob[:8] == SKT_JAR_MAGIC:
        return "skt", "first 8 bytes are the SKT magic"

    # a package whose descriptor is simply gone: look INSIDE the payload jar and say what
    # it is, so the user knows which descriptor to synthesise
    jars = [n for n in names if n.lower().endswith(".jar")]
    if len(jars) == 1:
        try:
            jz = _zip_from_bytes(zf.read(jars[0]), jars[0])
            jn = jz.namelist()
        except ValueError:
            jn = []
        if any(n.startswith(CLIENT_BIN_PREFIX) for n in jn):
            c.add(BLOCKING, "descriptor_missing",
                  f"no '__adf__' at the root, but '{jars[0]}' contains a KTF native member. "
                  f"This is a KTF package that lost its descriptor. Nothing will claim the "
                  f"zip until you put one back.",
                  symptom='"Unknown archive format" - clean, at load time',
                  fix=f"prepare_archive.py <src> <dst> --synthesize-descriptor "
                      f"--set-aid {os.path.splitext(jars[0])[0]} --set-pid <Y> --set-mclass <Z>")
        elif "binary.mod" in jn:
            c.add(BLOCKING, "descriptor_missing",
                  f"no 'app_info' at the root, but '{jars[0]}' contains 'binary.mod' - an LGT "
                  f"package that lost its descriptor.",
                  fix="prepare_archive.py --synthesize-descriptor")

    if any(n.endswith(".class") for n in names) or "META-INF/MANIFEST.MF" in names:
        return "j2me", "no platform magic; falls through to the J2ME MIDlet backend"
    return None, "nothing matched"


def _classify_jad(c: Classification, blob: bytes, encodings: List[str]) -> Classification:
    text, enc = _decode_descriptor(blob, encodings + ["utf-8"])
    c.data["container"] = "jad"
    c.data["platform"] = "j2me"
    c.data["platform_name"] = PLATFORMS["j2me"]
    c.data["detected_by"] = ".jad descriptor"
    keys: Dict[str, str] = {}
    for line in text.replace("\r\n", "\n").split("\n"):
        if ":" in line:
            k, _, v = line.partition(":")
            keys[k.strip()] = v.strip()
    c.data["descriptor"] = {"bytes": len(blob), "cr": blob.count(b"\r"),
                            "lf": blob.count(b"\n"), "encoding_used": enc, "keys": keys}
    jar = keys.get("MIDlet-Jar-URL")
    c.data["jar"] = {"declared": jar}
    if not jar:
        c.add(BLOCKING, "jad_no_jar_url",
              "the .jad has no MIDlet-Jar-URL, so nothing tells the loader which jar to open.")
    else:
        side = posix(os.path.join(os.path.dirname(c.path), os.path.basename(jar)))
        c.data["jar"]["expected_beside_jad"] = side
        if not os.path.isfile(side):
            c.add(BLOCKING, "jad_jar_missing",
                  f"MIDlet-Jar-URL names '{jar}' but there is no such file beside the .jad "
                  f"({side}). A .jad is useless without its .jar.")
        else:
            _analyse_jad_sidecar(c, side, keys)
    if not keys.get("MIDlet-1"):
        c.add(WARNING, "jad_no_midlet1",
              "no MIDlet-1 attribute: the loader has no main class to start.")
    c.add(INFO, "j2me_path",
          "J2ME is JVM bytecode, not an AOT-compiled native image. The static ARM toolkit "
          "in this plugin does not apply; use JVM-level tooling instead.")
    return c


def _analyse_jad_sidecar(c: Classification, side: str, keys: Dict[str, str]) -> None:
    """A .jad describes a jar it does not contain. Open the jar and diagnose it too.

    Without this the .jad path reports a descriptor and nothing else, which is the one case
    where the tool would answer "fine" about a payload it never looked at.
    """
    try:
        blob = open(side, "rb").read()
    except OSError as exc:
        c.add(BLOCKING, "jad_jar_unreadable", f"cannot read {side}: {exc}")
        return
    try:
        jz = _zip_from_bytes(blob, side)
    except ValueError as exc:
        c.add(BLOCKING, "jad_jar_unreadable", str(exc))
        return
    jnames = jz.namelist()
    jar_info = c.data["jar"]
    jar_info.update({
        "entry": posix(os.path.basename(side)),
        "bytes": len(blob),
        "md5": hashlib.md5(blob).hexdigest(),
        "members": len(jnames),
        "compression": _compress_summary(jz),
        "top_dirs": _top_dirs(jnames),
        "class_files": sum(1 for n in jnames if n.endswith(".class")),
    })

    declared = keys.get("MIDlet-Jar-Size", "").strip()
    if declared.isdigit() and int(declared) != len(blob):
        c.add(BLOCKING, "jad_jar_size_mismatch",
              f"MIDlet-Jar-Size says {int(declared)} but {posix(os.path.basename(side))} is "
              f"{len(blob)} bytes. MIDP loaders are entitled to reject the pair on this "
              f"alone, and a mismatch usually means the jar was repacked without updating "
              f"the descriptor.",
              fix="rewrite MIDlet-Jar-Size to the real size after every repack")

    natives = [n for n in jnames if n.startswith(CLIENT_BIN_PREFIX)]
    if not natives and "binary.mod" in jnames:
        natives = ["binary.mod"]
    if natives:
        data = jz.read(natives[0])
        c.data["native_member"] = {"name": natives[0], "bytes": len(data),
                                   "md5": hashlib.md5(data).hexdigest(),
                                   "bss_size": parse_bss_size(natives[0])[0]}
        c.add(WARNING, "platform_payload_mismatch",
              f"a .jad normally accompanies a MIDlet, but this jar carries {natives[0]!r} - "
              f"a WIPI native-image name. The dispatcher picks the backend from the "
              f"descriptor, so a .jad will route this to the MIDP backend and the native "
              f"image will never run. Work out which backend the title really needs.")

    for attr in ("MIDlet-1", "MIDlet-Jar-URL"):
        jad_v = keys.get(attr)
        mf_v = _manifest_attr(jz, jnames, attr)
        if jad_v and mf_v and jad_v.strip() != mf_v.strip():
            c.add(WARNING, "jad_manifest_disagree",
                  f"{attr} differs between the .jad ({jad_v!r}) and the jar manifest "
                  f"({mf_v!r}). MIDP says the .jad wins for most attributes, but loaders "
                  f"disagree in practice; make them identical and remove the question.")

    _guest_kind(c, jar_info, jnames, jz)


def _manifest_attr(jz: zipfile.ZipFile, jnames: List[str], attr: str) -> Optional[str]:
    mf = "META-INF/MANIFEST.MF"
    if mf not in jnames:
        return None
    text, _ = _decode_descriptor(jz.read(mf), ["utf-8", "latin-1"])
    for line in text.replace("\r\n", "\n").split("\n"):
        k, _, v = line.partition(":")
        if k.strip().lower() == attr.lower():
            return v.strip()
    return None


def _analyse_wipi(c: Classification, zf: zipfile.ZipFile, names: List[str],
                  platform: str, encodings: List[str]) -> None:
    magic = DESCRIPTOR_ENTRIES[platform]
    desc: Dict[str, Any] = {}
    if magic in names:
        raw = zf.read(magic)
        desc = parse_descriptor(raw, encodings)
        desc["entry"] = magic
    c.data["descriptor"] = desc

    # ---- CR bytes: the headline ------------------------------------------------------
    if desc:
        if desc["cr"]:
            c.add(BLOCKING, "descriptor_crlf",
                  f"'{magic}' contains {desc['cr']} CR bytes ({desc['lf']} LF). The parser "
                  f"splits on '\\n' and slices from a fixed index WITHOUT trimming, so every "
                  f"value keeps a trailing '\\r'. AID becomes \"...\\r\" and the loader then "
                  f"asks for \"...\\r.jar\", which is not in the archive.",
                  symptom="prints 'loaded' first, THEN panics on the async boot task: "
                          "java_runtime/src/classes/java/io/file.rs - "
                          "called `Result::unwrap()` on an `Err` value: NotFound. "
                          "On a device this is a PANIC log line and a black screen, not a "
                          "load error.",
                  fix="prepare_archive.py <src> <dst>   (CRLF -> LF is the whole repair)")
        else:
            c.add(INFO, "descriptor_lf_clean", f"'{magic}' has 0 CR bytes - line endings are fine.")
        if desc.get("bom"):
            c.add(BLOCKING, "descriptor_bom",
                  "the descriptor starts with a UTF-8 BOM. The parser matches b'AID:' at the "
                  "start of a line; a BOM on the first line hides whichever key is first.",
                  fix="strip the BOM (prepare_archive.py does)")

    aid = desc.get("loader_values", {}).get("AID")
    pid = desc.get("loader_values", {}).get("PID")
    mclass = desc.get("loader_values", {}).get("MClass")

    for key, val in (("AID", aid), ("PID", pid), ("MClass", mclass)):
        if val is None:
            sev = BLOCKING if key in ("AID", "MClass") else WARNING
            extra = {
                "AID": ("AID is the jar lookup key: the loader opens '<AID>.jar' from the "
                        "archive."),
                "MClass": ("MClass is the main class name and has NO other source - it "
                           "cannot be recovered from the jar."),
                "PID": ("PID is the on-device save namespace (saves land under "
                        "<root>/<PID>/db/...). Choose it once; changing it after release "
                        "makes every user's saves unreachable."),
            }[key]
            c.add(sev, f"descriptor_no_{key.lower()}",
                  f"'{magic}' has no {key}: line. {extra}",
                  symptom=("FatalError(\"Main class not found\")" if key == "MClass" else ""),
                  fix=f"prepare_archive.py --set-{key.lower()} <value>")
        elif val != val.strip():
            # A trailing CR is already reported once, as descriptor_crlf. Reporting it again
            # per key turns one root cause into four blockers and buries the real list.
            crlf_only = val.endswith("\r") and val.rstrip("\r") == val.strip()
            if crlf_only and desc.get("cr"):
                c.add(INFO, f"descriptor_{key.lower()}_cr",
                      f"consequence of descriptor_crlf: the loader reads {key} as "
                      f"{desc['raw_repr'][key]}.")
            else:
                c.add(BLOCKING, f"descriptor_{key.lower()}_whitespace",
                      f"{key} = {desc['raw_repr'][key]} - the value the loader sees carries "
                      f"whitespace, because it slices from a fixed index and never trims. "
                      f"A value that merely LOOKS right in a text editor still fails.",
                      fix="prepare_archive.py normalises this")

    # ---- the jar ---------------------------------------------------------------------
    jars = [n for n in names if n.lower().endswith(".jar")]
    jar_info: Dict[str, Any] = {"jars_in_archive": jars}
    expected = f"{aid}.jar" if aid else None
    jar_info["expected"] = expected
    chosen = None
    if expected and expected in names:
        chosen = expected
        jar_info["match"] = "exact"
    elif len(jars) == 1:
        chosen = jars[0]
        jar_info["match"] = "fallback: the only .jar in the archive"
    jar_info["entry"] = chosen

    if expected and expected not in names:
        rule = ("CONFIRMED for KTF" if platform == "ktf"
                else "INFERRED for LGT - the same descriptor keys are parsed, but confirm the "
                     "jar-naming rule against your loader")
        c.add(BLOCKING, "aid_jar_mismatch",
              f"the descriptor says AID={aid!r}, so the loader will open {expected!r}, but the "
              f"archive contains {jars if jars else 'no .jar at all'}. ({rule})",
              symptom="'loaded', then a NotFound unwrap panic in java/io/file.rs - identical "
                      "to the CRLF failure, because it is the same missing-member lookup",
              fix="prepare_archive.py reconciles this by adopting the real jar basename as "
                  "AID (one text edit, no payload rename); or force it with --set-aid")
    if len(jars) > 1:
        c.add(WARNING, "multiple_jars",
              f"{len(jars)} .jar entries: {jars}. Only '<AID>.jar' is ever opened; the rest "
              f"are dead payload you can strip.")
    if not jars:
        c.add(BLOCKING, "no_jar", "the archive contains no .jar entry at all.")
        c.data["jar"] = jar_info
        return

    # ---- inside the jar --------------------------------------------------------------
    try:
        jz = _zip_from_bytes(zf.read(chosen), chosen)
    except ValueError as exc:
        c.add(BLOCKING, "jar_unreadable", str(exc))
        c.data["jar"] = jar_info
        return

    jnames = jz.namelist()
    jar_info.update({
        "bytes": zf.getinfo(chosen).file_size,
        "members": len(jnames),
        "compression": _compress_summary(jz),
        "top_dirs": _top_dirs(jnames),
        "class_files": sum(1 for n in jnames if n.endswith(".class")),
    })
    c.data["jar"] = jar_info

    # native member: the loader takes the FIRST entry whose name starts with client.bin
    natives = [n for n in jnames if n.startswith(CLIENT_BIN_PREFIX)]
    buried = [n for n in jnames
              if n.rsplit("/", 1)[-1].startswith(CLIENT_BIN_PREFIX) and n not in natives]
    lgt_native = "binary.mod" if "binary.mod" in jnames else None

    if platform == "ktf":
        if not natives:
            if buried:
                c.add(BLOCKING, "native_not_at_root",
                      f"found {buried[0]!r}, but the probe is name.starts_with('client.bin') "
                      f"on the FULL entry name, so a nested native member never matches.",
                      fix="move it to the jar root")
            else:
                c.add(BLOCKING, "no_native_member",
                      f"no jar entry starts with '{CLIENT_BIN_PREFIX}'. Either this is not a "
                      f"KTF AOT title, or the member was renamed.")
        else:
            if len(natives) > 1:
                c.add(WARNING, "multiple_native_members",
                      f"{len(natives)} entries start with '{CLIENT_BIN_PREFIX}': {natives}. "
                      f"The loader silently takes the first in jar order ({natives[0]!r}).")
            name = natives[0]
            data = jz.read(name)
            bss, err = parse_bss_size(name)
            nat = {"name": name, "bytes": len(data), "md5": hashlib.md5(data).hexdigest(),
                   "bss_size": bss}
            c.data["native_member"] = nat
            if bss is None:
                c.add(BLOCKING, "bss_unparseable",
                      f"the BSS size is the integer suffix of the native member's filename, "
                      f"and {name!r} has no usable suffix. This is what happens when someone "
                      f"'tidies up' the name.",
                      symptom=err or "",
                      fix="restore the original filename; the digits are load-bearing")
            else:
                c.add(INFO, "bss",
                      f"BSS size {bss} (0x{bss:x}) parsed from {name!r}: the loader maps "
                      f"len(file)+{bss} bytes, so guest addresses PAST the end of the file "
                      f"are still valid - they are zero-filled statics.")
            c.add(INFO, "native_md5",
                  f"native member md5 {nat['md5']}. Emulator cores that carry md5-keyed "
                  f"binary patches match on exactly this value; if you ever repack the guest "
                  f"binary, a hash-keyed patch entry stops matching and that is fatal.")
    elif platform == "lgt":
        if lgt_native:
            data = jz.read(lgt_native)
            c.data["native_member"] = {"name": lgt_native, "bytes": len(data),
                                       "md5": hashlib.md5(data).hexdigest(), "bss_size": None}
            c.add(INFO, "lgt_native",
                  "LGT's native member is 'binary.mod'. Its BSS is NOT encoded in the "
                  "filename the way KTF's is - find it in your loader.")
        elif natives:
            data = jz.read(natives[0])
            c.data["native_member"] = {"name": natives[0], "bytes": len(data),
                                       "md5": hashlib.md5(data).hexdigest(),
                                       "bss_size": parse_bss_size(natives[0])[0]}
            c.add(WARNING, "platform_payload_mismatch",
                  f"the descriptor entry is 'app_info' (LGT) but the jar carries "
                  f"{natives[0]!r}, which is the KTF convention, and there is no "
                  f"'binary.mod'. Either the descriptor was renamed by hand or this dump was "
                  f"assembled from two sources. Decide which backend you actually want: the "
                  f"dispatcher picks by descriptor NAME, so renaming 'app_info' to '__adf__' "
                  f"switches backends.")
        else:
            c.add(WARNING, "lgt_no_binary_mod",
                  "no 'binary.mod' in the jar; if this title is bytecode, the ARM toolkit "
                  "does not apply.")

    _guest_kind(c, jar_info, jnames, jz)


def _guest_kind(c: Classification, jar_info: Dict[str, Any], jnames: List[str],
                jz: zipfile.ZipFile) -> None:
    n_class = jar_info["class_files"]
    has_native = bool(c.data.get("native_member"))
    if has_native and n_class == 0:
        c.data["guest_kind"] = "aot-native"
        c.add(INFO, "guest_aot",
              "no .class files and one large native member: the Java was AOT-compiled to a "
              "native image. You are emulating a CPU, not bytecode. This single fact decides "
              "the whole rest of the project - the guest-re static toolkit applies in full.")
    elif n_class and not has_native:
        c.data["guest_kind"] = "jvm-bytecode"
        c.add(INFO, "guest_bytecode",
              f"{n_class} .class files and no native member: this is JVM bytecode. The ARM "
              f"disassembly toolkit does not apply; use JVM-level tooling.")
    elif n_class and has_native:
        c.data["guest_kind"] = "mixed"
        c.add(WARNING, "guest_mixed",
              f"{n_class} .class files AND a native member. Unusual - work out which one the "
              f"loader actually executes before investing in either toolchain.")
    else:
        c.data["guest_kind"] = "unknown"
        # Do not dead-end. Name the largest root-level members: on an unfamiliar platform the
        # native image is still the biggest non-resource blob, it just has a name this tool
        # does not know. The user can recognise it even when the classifier cannot.
        roots = [(n, jz.getinfo(n).file_size) for n in jnames
                 if "/" not in n.rstrip("/") and not n.endswith("/")]
        roots.sort(key=lambda t: -t[1])
        top = ", ".join(f"{n!r} ({human(s)} B)" for n, s in roots[:5]) or "none"
        c.add(BLOCKING, "guest_unknown",
              f"neither .class files nor a native member under a name this tool knows "
              f"('{CLIENT_BIN_PREFIX}*' for KTF, 'binary.mod' for LGT). Largest root-level "
              f"members, biggest first: {top}. If one of those is the executable image, the "
              f"dump uses a naming convention outside this tool's table - read your loader's "
              f"own probe to find out which name it looks for.",
              fix="confirm the name in the loader crate, then point target.toml's `binary` at "
                  "the member you extract")

    mf = "META-INF/MANIFEST.MF"
    if mf in jnames:
        text, _ = _decode_descriptor(jz.read(mf), ["utf-8", "latin-1"])
        attrs = {k.strip(): v.strip() for k, _, v in
                 (l.partition(":") for l in text.replace("\r\n", "\n").split("\n")) if k.strip()}
        midlet = {k: v for k, v in attrs.items() if k.lower().startswith(("midlet", "microedition"))}
        c.data["manifest"] = {"present": True, "attributes": len(attrs),
                              "midlet_attributes": midlet}
        if midlet:
            c.add(INFO, "manifest_midlet",
                  f"the manifest carries MIDlet attributes {sorted(midlet)} - evidence for a "
                  f"MIDlet, even if a platform magic also matched.")
        else:
            c.add(INFO, "manifest_no_midlet",
                  "the manifest has no MIDlet attributes. That absence is the tell that this "
                  "is not a MIDlet.")
    else:
        c.data["manifest"] = {"present": False}

    signed = [n for n in jnames if n.startswith("META-INF/")
              and n.upper().endswith((".SF", ".RSA", ".DSA", ".EC"))]
    c.data["jar_signed"] = bool(signed)
    if signed:
        c.add(WARNING, "jar_signed",
              f"the jar carries signature files {signed}. Repacking will invalidate them. "
              f"Check whether anything actually verifies them before you edit a member.")
    else:
        c.add(INFO, "jar_unsigned",
              "no .SF/.RSA in META-INF: nothing verifies jar contents, so a repacked member "
              "is accepted. (Whether the GAME hashes its own data files is a separate "
              "question this tool cannot answer - test it.)")


def _analyse_skt(c: Classification, zf: zipfile.ZipFile, names: List[str],
                 encodings: List[str]) -> None:
    msd = [n for n in names if n.lower().endswith(".msd")][0]
    raw = zf.read(msd)
    text, enc = _decode_descriptor(raw, encodings)
    keys: Dict[str, str] = {}
    for line in text.replace("\r\n", "\n").split("\n"):
        if ":" in line:
            k, _, v = line.partition(":")
            keys[k.strip()] = v.strip()
    c.data["descriptor"] = {"entry": msd, "bytes": len(raw), "cr": raw.count(b"\r"),
                            "lf": raw.count(b"\n"), "encoding_used": enc, "keys": keys}
    c.add(INFO, "skt_trims",
          "the .msd parser trims its values, so CR bytes here are harmless - the CRLF "
          "failure that kills KTF and LGT does not apply to SKT.")
    if "MIDlet-1" not in keys:
        c.add(WARNING, "skt_no_midlet1", "no 'MIDlet-1:' line in the .msd.")
    jars = [n for n in names if n.lower().endswith(".jar")]
    jar_info: Dict[str, Any] = {"jars_in_archive": jars, "entry": jars[0] if jars else None}
    c.data["jar"] = jar_info
    if not jars:
        c.add(BLOCKING, "no_jar", "the .msd is present but there is no .jar to run.")
        return
    try:
        jz = _zip_from_bytes(zf.read(jars[0]), jars[0])
    except ValueError as exc:
        c.add(BLOCKING, "jar_unreadable", str(exc))
        return
    jnames = jz.namelist()
    jar_info.update({"bytes": zf.getinfo(jars[0]).file_size, "members": len(jnames),
                     "compression": _compress_summary(jz), "top_dirs": _top_dirs(jnames),
                     "class_files": sum(1 for n in jnames if n.endswith(".class"))})
    natives = [n for n in jnames if n.startswith(CLIENT_BIN_PREFIX)]
    if natives:
        data = jz.read(natives[0])
        c.data["native_member"] = {"name": natives[0], "bytes": len(data),
                                   "md5": hashlib.md5(data).hexdigest(),
                                   "bss_size": parse_bss_size(natives[0])[0]}
        c.add(WARNING, "platform_payload_mismatch",
              f"the descriptor is a '.msd' (SKT) but the jar carries {natives[0]!r}, the KTF "
              f"convention. The dispatcher picks by descriptor name, so check which backend "
              f"this dump is really for.")
    _guest_kind(c, jar_info, jnames, jz)


def _analyse_bare_jar(c: Classification, zf: zipfile.ZipFile, names: List[str],
                      platform: str) -> None:
    """The input IS the jar. There is no outer package and no descriptor to reconcile."""
    jar_info: Dict[str, Any] = {
        "entry": "(the file itself)", "members": len(names),
        "compression": _compress_summary(zf), "top_dirs": _top_dirs(names),
        "class_files": sum(1 for n in names if n.endswith(".class")),
    }
    c.data["jar"] = jar_info
    natives = [n for n in names if n.startswith(CLIENT_BIN_PREFIX)]
    if platform == "ktf" and natives:
        name = natives[0]
        data = zf.read(name)
        bss, err = parse_bss_size(name)
        c.data["native_member"] = {"name": name, "bytes": len(data),
                                   "md5": hashlib.md5(data).hexdigest(), "bss_size": bss}
        if bss is None:
            c.add(BLOCKING, "bss_unparseable",
                  f"the BSS size is the integer suffix of the native member's filename, and "
                  f"{name!r} has no usable suffix.", symptom=err or "",
                  fix="restore the original filename; the digits are load-bearing")
        else:
            c.add(INFO, "bss", f"BSS size {bss} (0x{bss:x}) parsed from {name!r}.")
    elif platform == "lgt" and "binary.mod" in names:
        data = zf.read("binary.mod")
        c.data["native_member"] = {"name": "binary.mod", "bytes": len(data),
                                   "md5": hashlib.md5(data).hexdigest(), "bss_size": None}
    _guest_kind(c, jar_info, names, zf)


def _analyse_j2me_jar(c: Classification, zf: zipfile.ZipFile, names: List[str]) -> None:
    jar_info = {"entry": "(the archive itself)", "members": len(names),
                "class_files": sum(1 for n in names if n.endswith(".class")),
                "top_dirs": _top_dirs(names), "compression": _compress_summary(zf)}
    c.data["jar"] = jar_info
    _guest_kind(c, jar_info, names, zf)


def _check_private_entries(c: Classification, names: List[str], zf: zipfile.ZipFile) -> None:
    """Entries that are the previous owner's saved state."""
    private = [n for n in names if n.startswith("P/")]
    savelike = [n for n in names
                if n not in private and not n.endswith("/")
                and any(tok in n.lower() for tok in ("save", "record"))
                and "/" not in n]
    c.data["private_entries"] = private
    c.data["savelike_entries"] = savelike
    if private or savelike:
        total = sum(zf.getinfo(n).file_size for n in private + savelike)
        c.add(WARNING, "private_entries",
              f"{len(private) + len(savelike)} entries look like somebody's saved state "
              f"({human(total)} bytes): {(private + savelike)[:6]}"
              f"{' ...' if len(private + savelike) > 6 else ''}. "
              f"'P/' is the private-data prefix; the loader strips the prefix and adds these "
              f"to the virtual filesystem. Strip them before distributing - they are personal "
              f"data. NOTE: they do NOT seed a new user's save database. Packaged-database "
              f"reads resolve through the classloader (jar resources), while 'P/' entries land "
              f"in the virtual filesystem, which is a different namespace - measured, against "
              f"a documented claim to the contrary.",
              fix="prepare_archive.py <src> <dst> --strip-private")


# --------------------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------------------

def render_text(c: Classification) -> str:
    d = c.data
    out: List[str] = []
    w = out.append

    w(f"archive   {d['archive']}")
    w(f"          {human(d.get('bytes', 0))} bytes   md5 {d.get('md5', '?')}")
    cont = d.get("container", "?")
    if "entries" in d:
        cont += f", {d['entries']} entries, {d.get('compression')}"
    w(f"container {cont}")
    plat = d.get("platform")
    w(f"platform  {PLATFORMS.get(plat, plat or 'UNRECOGNISED')}")
    w(f"          detected by: {d.get('detected_by', '-')}")

    desc = d.get("descriptor")
    if desc:
        w("")
        w(f"descriptor  {desc.get('entry', '(jad)')}   {human(desc.get('bytes', 0))} bytes"
          f"   CR={desc.get('cr')} LF={desc.get('lf')}   decoded as {desc.get('encoding_used')}")
        lv = desc.get("loader_values", {})
        for key, _ in DESCRIPTOR_KEYS:
            if key in lv:
                w(f"   {key:<8} {lv[key]!r}      (as the loader sees it: {desc['raw_repr'][key]})")
        extra = {k: v for k, v in desc.get("keys", {}).items()
                 if k not in {kk for kk, _ in DESCRIPTOR_KEYS}}
        if extra:
            w(f"   other keys: {', '.join(f'{k}={v!r}' for k, v in list(extra.items())[:10])}")

    jar = d.get("jar")
    if jar:
        w("")
        name = jar.get("entry") or jar.get("declared") or "(none)"
        w(f"jar         {name}"
          + (f"   expected '{jar['expected']}'" if jar.get("expected") else "")
          + (f"   [{jar['match']}]" if jar.get("match") else ""))
        if jar.get("expected_beside_jad"):
            found = "found" if jar.get("members") is not None else "NOT FOUND"
            w(f"            named by MIDlet-Jar-URL, resolved beside the .jad: {found}")
        if "members" in jar:
            size = f"{human(jar['bytes'])} bytes, " if jar.get("bytes") else ""
            w(f"            {size}{jar['members']} members, {jar.get('compression')}")
            w(f"            .class files: {jar.get('class_files')}   guest kind: "
              f"{d.get('guest_kind')}")
            tops = jar.get("top_dirs", {})
            if tops:
                w("            top dirs: "
                  + "  ".join(f"{k} {v}" for k, v in list(tops.items())[:12]))

    nat = d.get("native_member")
    if nat:
        w("")
        w(f"native      {nat['name']}   {human(nat['bytes'])} bytes   md5 {nat['md5']}")
        w(f"            bss_size = {nat['bss_size']}")

    if d.get("private_entries") or d.get("savelike_entries"):
        w("")
        w(f"private     {d.get('private_entries', []) + d.get('savelike_entries', [])}")

    order = {BLOCKING: 0, WARNING: 1, INFO: 2}
    issues = sorted(c.issues, key=lambda i: order.get(i.severity, 9))
    w("")
    w(f"issues      {sum(1 for i in issues if i.severity == BLOCKING)} blocking, "
      f"{sum(1 for i in issues if i.severity == WARNING)} warning, "
      f"{sum(1 for i in issues if i.severity == INFO)} info")
    for i in issues:
        tag = {BLOCKING: "BLOCKING", WARNING: "warning ", INFO: "info    "}[i.severity]
        w("")
        w(f"  [{tag}] {i.code}")
        for line in wrap(i.message, 88):
            w(f"      {line}")
        if i.symptom:
            for n, line in enumerate(wrap(i.symptom, 84)):
                w(f"      {'symptom: ' if n == 0 else '         '}{line}")
        if i.fix:
            for n, line in enumerate(wrap(i.fix, 88)):
                w(f"      {'fix:     ' if n == 0 else '         '}{line}")

    w("")
    if c.blocking:
        w("verdict     WILL NOT BOOT AS-IS - fix the blocking issues above.")
    else:
        w("verdict     structurally sound. Nothing here guarantees it runs; boot it headless next.")
    return "\n".join(out)


def wrap(text: str, width: int) -> List[str]:
    """Naive word wrap. Public: sibling tools format their reports with it."""
    words, lines, cur = text.split(), [], ""
    for word in words:
        if cur and len(cur) + 1 + len(word) > width:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        lines.append(cur)
    return lines or [""]


def emit_target_toml(c: Classification, binary_rel: str) -> str:
    plat = c.data.get("platform")
    nat = c.data.get("native_member") or {}
    base = IMAGE_BASE_BY_PLATFORM.get(plat)
    lines = [
        "# target.toml - generated by classify_archive.py. Fill in the rest as you pin it.",
        f'binary       = "{posix(binary_rel)}"',
    ]
    if base is not None:
        lines.append(f"image_base   = 0x{base:x}"
                     f"   # {plat.upper()} loader constant - verify in your loader crate's "
                     f"load(data, BASE, ...)")
    else:
        lines.append("# image_base = 0x0   # REQUIRED: read it from your loader's "
                     "load(data, BASE, ...) call")
    if nat.get("bss_size") is not None:
        lines.append(f"bss_size     = {nat['bss_size']}"
                     f"   # integer suffix of {nat['name']}")
    else:
        lines.append("# bss_size   = 0     # integer suffix of the native member's filename")
    lines += [
        "# sl_fo      = 0x0                 # RWPI static base AS A FILE OFFSET (register dump,",
        "#                                  #   entry-thunk prologue, or a dense static dump)",
        "# reloc_delta = 0x0                # solve, then validate corpus-wide",
        '# reloc_unit = "file_offset"       # or "guest_address" - REQUIRED with a delta',
        'encoding     = "euc-kr"            # the game\'s own string encoding',
        'isa          = "thumb"',
        "# god_object = 0x0                 # GUEST ADDRESS of the main-state pointer slot",
    ]
    return "\n".join(lines) + "\n"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="classify_archive.py",
        description="Detect the platform of a feature-phone game dump and diagnose every "
                    "name-agreement problem that will stop it booting.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python classify_archive.py dumps/<archive>.zip\n"
            "  python classify_archive.py dumps/<archive>.zip --json\n"
            "  python classify_archive.py dumps/<archive>.zip --extract-native work/client.bin \\\n"
            "                                                 --emit-target target.toml\n"
            "  python classify_archive.py game.jar --list\n"
            "\n"
            "exit codes: 0 clean | 1 blocking issues found | 2 unreadable/unclassifiable\n"
        ),
    )
    p.add_argument("archive", help="the dump: .zip / .kdp / .jar / .jad (forward slashes)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--list", action="store_true", help="include the full outer entry list")
    p.add_argument("--encoding", action="append", metavar="ENC",
                   help="descriptor text encoding to try, repeatable "
                        "(default: euc-kr, shift_jis, utf-8)")
    p.add_argument("--extract-native", metavar="PATH",
                   help="write the native guest member to PATH (this is the 'binary' your "
                        "target.toml points at). Refuses to overwrite.")
    p.add_argument("--emit-target", metavar="PATH",
                   help="write a starter target.toml (use '-' for stdout). Refuses to overwrite.")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    encodings = args.encoding or ["euc-kr", "shift_jis", "utf-8"]

    try:
        c = classify(args.archive, encodings, want_list=args.list)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.extract_native:
        rc = _do_extract_native(args.archive, c, args.extract_native)
        if rc:
            return rc

    if args.emit_target:
        # `binary` is resolved relative to target.toml, not to the cwd - so rewrite the
        # extraction path into that frame or the generated file points at nothing.
        binary_rel = args.extract_native or "work/client.bin"
        if args.emit_target != "-":
            base = os.path.dirname(os.path.abspath(args.emit_target))
            try:
                binary_rel = posix(os.path.relpath(os.path.abspath(binary_rel), base))
            except ValueError:  # different drive on Windows: keep it absolute
                binary_rel = posix(os.path.abspath(binary_rel))
        text = emit_target_toml(c, binary_rel)
        if args.emit_target == "-":
            sys.stdout.write(text)
        elif os.path.exists(args.emit_target):
            print(f"error: {posix(args.emit_target)} exists; refusing to overwrite",
                  file=sys.stderr)
            return 2
        else:
            _mkparents(args.emit_target)
            with open(args.emit_target, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            print(f"wrote {posix(args.emit_target)}", file=sys.stderr)

    if args.json:
        print(json.dumps(c.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(render_text(c))

    if c.data.get("platform") is None:
        return 2
    return 1 if c.blocking else 0


def _mkparents(path: str) -> None:
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)


def _do_extract_native(archive: str, c: Classification, dest: str) -> int:
    nat = c.data.get("native_member")
    if not nat:
        print("error: --extract-native: no native member was identified in this archive",
              file=sys.stderr)
        return 2
    if os.path.exists(dest):
        print(f"error: {posix(dest)} exists; refusing to overwrite", file=sys.stderr)
        return 2
    try:
        with open(archive, "rb") as fh:
            blob = fh.read()
        outer = zipfile.ZipFile(io.BytesIO(blob))
        jar_entry = c.data["jar"]["entry"]
        jz = zipfile.ZipFile(io.BytesIO(outer.read(jar_entry))) if jar_entry else outer
        data = jz.read(nat["name"])
    except Exception as exc:  # noqa: BLE001 - report and stop, never half-write
        print(f"error: --extract-native failed: {exc}", file=sys.stderr)
        return 2
    got = hashlib.md5(data).hexdigest()
    if got != nat["md5"]:
        print(f"error: extracted bytes hash {got}, expected {nat['md5']}", file=sys.stderr)
        return 2
    _mkparents(dest)
    with open(dest, "wb") as fh:
        fh.write(data)
    print(f"wrote {posix(dest)}  {human(len(data))} bytes  md5 {got}", file=sys.stderr)
    print("  This is licensed game content. Keep it out of version control - guard it by "
          "NAME as well as by path.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())


_wrap = wrap  # backwards-compatible alias
