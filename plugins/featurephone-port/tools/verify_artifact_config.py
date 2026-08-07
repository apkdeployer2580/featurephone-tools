#!/usr/bin/env python3
"""Verify build-time configuration INSIDE the produced artifact, not in the build script.

THE FAILURE THIS DEFENDS AGAINST  (A)
    Rust's `option_env!` -- and every equivalent in every toolchain -- resolves at
    COMPILE time.  If the environment variable is not exported when the compiler
    runs, the macro yields None, the fallback arm compiles in, and the build
    SUCCEEDS.  You get a perfectly signed, perfectly installable artifact that
    silently points at the developer loopback address.

    It is worse than it sounds, for two reasons:
      * It is invisible on the developer's own phone, which still has a
        device-side override file from an earlier session, and total for
        everybody else.
      * It survives a `git status` clean tree, a passing test suite, and a
        successful release build.  Nothing in the build reports it.

    Three defences, in increasing order of how much you can trust them:
      1. Resolve the setting from persistent config (a properties file), not only
         from the shell, so forgetting to export is not the normal case.
      2. Make a release build with no destination a HARD ERROR, with the exact
         fix in the message, overridable only by an explicit "local build" flag.
      3. ** THIS TOOL: scan the produced binary for the string you intended to
         bake in.  It is the one check that cannot be fooled -- not by a stale
         cargo fingerprint, not by a variable exported into the wrong process,
         not by a typo in a variable name nothing validates.

    Generalised rule, and it is worth naming in your own docs:
        VERIFY BUILD-TIME CONFIGURATION IN THE ARTIFACT, NOT IN THE BUILD SCRIPT.
    Run this BEFORE the packaging step, so it fires on the compiler output rather
    than after you have signed something.

** THE TRAP IN --forbid-string, READ BEFORE USING IT
    Your fallback/dev-default string is compiled in ALWAYS -- it is the other arm
    of the same match.  So "does the binary mention 127.0.0.1" proves NOTHING and
    a naive `--forbid-string 127.0.0.1` will fail every correct build you ever
    make.  Only the PRESENCE of the wanted host is evidence.  Use --forbid-string
    for things that must never appear at all: a staging hostname, an internal
    domain, a debug token, a colleague's machine name.

WHAT IT SCANS FOR
    Each required/forbidden needle is searched for as
      * ASCII / UTF-8 bytes
      * UTF-16LE bytes (resource strings, some JNI paths)
    Matching is exact and case-sensitive by default (--ignore-case to relax).
    The tool never parses ELF/Mach-O/PE structure: it is a byte scan, on purpose,
    so it works on a .so, a .a, an .exe, a .dylib or a raw blob without caring.

KNOWN LIMITS
    * A string built at runtime by concatenation is not in the binary and cannot
      be verified this way. Bake whole strings.
    * A compressed or encrypted section hides its strings.  If your linker packs
      rodata, this tool reports absence for a present string.  Check once, by
      hand, that a known-present string is found before trusting the gate.
    * An APK is a zip: extract lib/<abi>/lib*.so first and scan that. Scanning the
      .apk directly finds nothing because the .so inside it is deflated.
    * Presence is not correctness: a stale host string from a previous build can
      still be present if the linker kept dead rodata. Combine with a clean build
      when the result surprises you.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys


def needle_variants(text, ignore_case):
    """(label, bytes) pairs to search for."""
    out = [("ascii", text.encode("utf-8", errors="replace")),
           ("utf16le", text.encode("utf-16-le", errors="replace"))]
    if ignore_case:
        low = text.lower()
        up = text.upper()
        for variant in (low, up):
            if variant != text:
                out.append(("ascii", variant.encode("utf-8", errors="replace")))
                out.append(("utf16le", variant.encode("utf-16-le", errors="replace")))
    return out


def find_all(data, needle, limit=8):
    hits = []
    start = 0
    while len(hits) < limit:
        idx = data.find(needle, start)
        if idx < 0:
            break
        hits.append(idx)
        start = idx + 1
    return hits


def scan(data, text, ignore_case):
    found = []
    for encoding, raw in needle_variants(text, ignore_case):
        if not raw:
            continue
        for off in find_all(data, raw):
            found.append({"encoding": encoding, "offset": off})
    # de-duplicate identical (encoding, offset)
    seen = set()
    uniq = []
    for f in found:
        key = (f["encoding"], f["offset"])
        if key not in seen:
            seen.add(key)
            uniq.append(f)
    uniq.sort(key=lambda f: f["offset"])
    return uniq


_ASCII_RUN = re.compile(rb"[\x20-\x7e]{4,}")


def list_strings(data, min_len, contains=None, limit=400):
    out = []
    for m in _ASCII_RUN.finditer(data):
        s = m.group(0)
        if len(s) < min_len:
            continue
        text = s.decode("ascii")
        if contains and contains not in text:
            continue
        out.append((m.start(), text))
        if len(out) >= limit:
            break
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="verify_artifact_config.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Scan a produced shared object / executable for the configuration "
                    "strings the build was supposed to bake into it.",
        epilog=(
            "Typical release gate, run BEFORE packaging:\n"
            "  python verify_artifact_config.py build/libmain.so \\\n"
            "      --require-string \"tls://YOUR_SERVER_HOST\" \\\n"
            "      --require-string \"<64-hex SHA-256 of your server cert DER>\"\n"
            "\n"
            "Do NOT --forbid-string your loopback default: the fallback arm of the\n"
            "configuration match is compiled in unconditionally, so it is present in\n"
            "every correct build. Only the presence of the WANTED value is evidence."
        ),
    )
    ap.add_argument("artifact", help="path to the .so / .exe / .dylib / .a / blob to scan")
    ap.add_argument("--require-string", action="append", default=[], metavar="TEXT",
                    help="fail unless this string is present. Repeatable.")
    ap.add_argument("--forbid-string", action="append", default=[], metavar="TEXT",
                    help="fail if this string is present. Repeatable. Read the --help "
                         "warning about fallback defaults first.")
    ap.add_argument("--require-file", metavar="PATH",
                    help="file with one required string per line ('#' comments allowed)")
    ap.add_argument("--ignore-case", action="store_true",
                    help="also try lower/upper-case variants of each needle")
    ap.add_argument("--list-strings", action="store_true",
                    help="print the ASCII strings found in the artifact (diagnostic)")
    ap.add_argument("--grep", metavar="SUBSTR",
                    help="with --list-strings, only print strings containing this")
    ap.add_argument("--min-len", type=int, default=6,
                    help="minimum length for --list-strings (default 6)")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args(argv)

    path = args.artifact.replace("\\", "/")
    if not os.path.exists(path):
        sys.exit("error: no such file: %s" % path)
    if os.path.isdir(path):
        sys.exit("error: %s is a directory. Point at the built binary itself." % path)

    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        data = fh.read()

    if path.lower().endswith((".apk", ".aab", ".zip", ".jar")) and data[:2] == b"PK":
        print(
            "warning: %s is a zip container. The native library inside it is DEFLATED,\n"
            "         so a byte scan of the container finds nothing even when the string\n"
            "         is present. Extract lib/<abi>/lib*.so and scan that instead."
            % os.path.basename(path),
            file=sys.stderr,
        )

    required = list(args.require_string)
    if args.require_file:
        with open(args.require_file, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    required.append(line)

    if not required and not args.forbid_string and not args.list_strings:
        sys.exit("error: nothing to check. Give --require-string / --forbid-string, "
                 "or --list-strings to explore.")

    results = {"artifact": path, "size": size, "required": [], "forbidden": []}
    failures = []

    for text in required:
        hits = scan(data, text, args.ignore_case)
        ok = bool(hits)
        results["required"].append({
            "string": text, "present": ok,
            "hits": [{"encoding": h["encoding"], "offset": "0x%x" % h["offset"]} for h in hits[:8]],
        })
        if not ok:
            failures.append(("MISSING", text))

    for text in args.forbid_string:
        hits = scan(data, text, args.ignore_case)
        bad = bool(hits)
        results["forbidden"].append({
            "string": text, "present": bad,
            "hits": [{"encoding": h["encoding"], "offset": "0x%x" % h["offset"]} for h in hits[:8]],
        })
        if bad:
            failures.append(("PRESENT", text))

    if args.list_strings:
        results["strings"] = [
            {"offset": "0x%x" % off, "text": text}
            for off, text in list_strings(data, args.min_len, args.grep)
        ]

    if args.json:
        results["ok"] = not failures
        results["failures"] = [{"kind": k, "string": s} for k, s in failures]
        print(json.dumps(results, indent=2))
    else:
        print("artifact : %s  (%d bytes)" % (path, size))
        print()
        for entry in results["required"]:
            mark = "OK  " if entry["present"] else "FAIL"
            where = ""
            if entry["hits"]:
                where = "  [%s @ %s]" % (entry["hits"][0]["encoding"], entry["hits"][0]["offset"])
                if len(entry["hits"]) > 1:
                    where += " +%d more" % (len(entry["hits"]) - 1)
            print("%s required  %r%s" % (mark, entry["string"], where))
        for entry in results["forbidden"]:
            mark = "FAIL" if entry["present"] else "OK  "
            where = ""
            if entry["hits"]:
                where = "  [%s @ %s]" % (entry["hits"][0]["encoding"], entry["hits"][0]["offset"])
            print("%s forbidden %r%s" % (mark, entry["string"], where))

        if args.list_strings:
            print()
            print("-- ASCII strings (min %d%s) --"
                  % (args.min_len, ", containing %r" % args.grep if args.grep else ""))
            for item in results.get("strings", []):
                print("  %-10s %s" % (item["offset"], item["text"]))

        print()
        if failures:
            print("FAILED: %d check(s)." % len(failures))
            for kind, text in failures:
                if kind == "MISSING":
                    print("  MISSING %r" % text)
            if any(k == "MISSING" for k, _ in failures):
                print()
                print("  A required string is absent from the artifact. In order of likelihood:")
                print("   1. the build-time variable was not exported into the COMPILER's")
                print("      process (exporting it into the shell that calls the build script")
                print("      is not the same thing if the script re-execs);")
                print("   2. a stale incremental build reused an object compiled without it --")
                print("      force a rebuild of the crate that reads the variable;")
                print("   3. the variable name in the build script and the name the source")
                print("      reads differ by a character, and nothing validates that pairing;")
                print("   4. the value is assembled at runtime instead of baked -- in which")
                print("      case this gate cannot verify it, and you should bake it whole.")
            for kind, text in failures:
                if kind == "PRESENT":
                    print("  PRESENT (forbidden) %r" % text)
        else:
            print("All checks passed.")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
