#!/usr/bin/env python3
"""Publish-readiness audit: sweep the working tree AND every git ref before you push.

WHY THIS IS THE MOST IMPORTANT TOOL IN THE SET
    A port accumulates, quietly and without anyone deciding to:
      * signing keys, and the passwords that open them
      * a server address and a certificate pin
      * an account store -- and in a bearer-token auth model the account id IS
        the password, so a leaked database is a leaked credential for every user
      * transcripts of other people's sessions
      * the licensed game archive, the extracted guest image, and everything
        decoded from them
      * one developer's absolute paths, device serial and email

    And then two facts collide:

      ** .gitignore prevents FUTURE additions and never past ones. A file that was
        committed once stays in history forever, and a tracked file is NEVER
        re-evaluated against .gitignore -- adding a rule that matches it changes
        nothing at all.

      ** HISTORY IS NOT THE WORKING TREE. `git log` on the branch you are looking
        at is not the set of objects a clone receives. A file deleted at the tip,
        or living only on a branch you forgot, ships with every clone, every
        fork, and every `git push --all`.

      ** And the purge you finally run is undone by the backup branch you made
        "just in case".

    So this tool reports, for every hit, WHICH REFS IT IS REACHABLE FROM, checks
    whether anything sensitive is tracked despite matching .gitignore, and looks
    for the backup refs that would resurrect a purge.

WHAT IT LOOKS FOR
    private keys and keystores | 64-hex certificate pins | PUBLIC IPv4 literals
    (loopback / RFC1918 / RFC5737 doc / link-local / CGNAT / multicast /
    reserved are excluded) | email addresses | absolute per-user paths |
    Android device serials | cloud-token shapes (AWS, GitHub, Slack, Google,
    Stripe, npm, JWT) | high-risk blob classes (*.db and its WAL/SHM siblings,
    *.jsonl, *.apk / *.aab, *.jks / *.keystore / *.p12, and large zip/jar/bin
    blobs that are probably game content).

WHAT IT CANNOT DO
    * It cannot tell a real secret from a fixture. Triage the output; use
      --allow-* and an .publishauditignore for the ones you have decided about,
      and write down WHY next to each allowance.
    * It cannot see into a compressed or encrypted blob. A secret inside a zip
      is reported only by the zip's own path/size class.
    * It does not rewrite anything. Purging history is destructive and belongs
      in your hands, not in a tool that runs in CI.
    * A clean run is evidence, not proof. The last check is always a human
      reading `git log --all --stat` once.

IF SOMETHING IS FOUND IN HISTORY
    The order that actually works:
      1. ROTATE FIRST. Assume the secret is public from the moment it was pushed.
         Re-key the signing keystore only if it was never used to ship (you can
         never re-key a published one -- a different key means Android refuses the
         update and every user must uninstall). Rotate server certs, tokens and
         any account credential immediately.
      2. Delete every backup branch and every refs/original/* left by an earlier
         rewrite, THEN rewrite history, THEN expire reflogs and gc --prune=now.
         In that order, or the objects survive.
      3. Re-run this tool with --all-refs and confirm zero.
      4. If the repository was ever public or ever pushed to a fork, treat the
         secret as burned regardless of what the history now looks like.

EXIT CODE
    Non-zero when any finding is at or above --fail-on (default: medium), so it
    can gate CI.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

SEVERITIES = ["info", "low", "medium", "high", "critical"]


def sev_rank(name):
    return SEVERITIES.index(name)


# --------------------------------------------------------------------------
# content detectors
# --------------------------------------------------------------------------

_HEX64 = re.compile(rb"(?<![0-9A-Za-z])([0-9a-fA-F]{64})(?![0-9A-Za-z])")
_IPV4 = re.compile(rb"(?<![0-9.])((?:\d{1,3})\.(?:\d{1,3})\.(?:\d{1,3})\.(?:\d{1,3}))(?![0-9.])")
_EMAIL = re.compile(rb"(?<![A-Za-z0-9._%+-])([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
# The terminator class excludes the backtick and the asterisk as well as quotes
# and brackets: neither can appear in a real path, but both routinely wrap the
# path in Markdown, and swallowing one turns a recognisable placeholder into an
# unrecognisable one.
_USERPATH = re.compile(
    rb"(?:[A-Za-z]:[\\/]Users[\\/][^\\/\s\"'`*<>|;:,)\]]+"
    rb"|[A-Za-z]:\\\\Users\\\\[^\\\s\"'`*<>|;:,)\]]+"
    rb"|/home/[A-Za-z0-9._-]+(?=/)"
    rb"|/Users/[A-Za-z0-9._-]+(?=/))"
)
# NOTE: the PEM banners below are assembled from fragments on purpose. Spelled
# out in full they would match THIS FILE, and an audit tool that fails on its own
# source cannot be used as a CI gate on the repository that contains it.
_BEGIN = rb"-----BE" rb"GIN "
_PRIVKEY = re.compile(
    _BEGIN + rb"(?:RSA |DSA |EC |OPENSSH |PGP |ENCRYPTED )?PRIV" rb"ATE KEY(?: BLOCK)?-----")

# A path whose user component is obviously a placeholder is documentation telling
# people NOT to do this, not a leak. Matching it trains users to ignore the tool.
_PLACEHOLDER_USER = re.compile(
    rb"^(?:<[^>]*>|\$\{[^}]*\}|%[A-Za-z_]+%|\.\.\.|\xe2\x80\xa6|\*+|"
    rb"(?i:you|user|username|name|me|yourname|youruser|developer|dev))$")
_SERIAL_ASSIGN = re.compile(
    rb"(?i)(?:ANDROID_SERIAL|DEVICE_SERIAL|[A-Z0-9_]*_ADB|adb[_.]?serial|serial)"
    rb"\s*[:=]\s*[\"']?([A-Za-z0-9]{8,24})[\"']?")
_ADB_S = re.compile(rb"adb(?:\.exe)?\s+(?:-\S+\s+)*-s\s+([A-Za-z0-9]{6,24})")

_TOKENS = [
    ("aws-access-key", "critical", re.compile(rb"\b((?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16})\b")),
    ("github-token", "critical", re.compile(rb"\b(gh[pousr]_[A-Za-z0-9]{30,})\b")),
    ("github-pat", "critical", re.compile(rb"\b(github_pat_[A-Za-z0-9_]{50,})\b")),
    ("slack-token", "critical", re.compile(rb"\b(xox[abprs]-[A-Za-z0-9-]{10,})\b")),
    ("google-api-key", "critical", re.compile(rb"\b(AIza[0-9A-Za-z_\-]{35})\b")),
    ("stripe-secret", "critical", re.compile(rb"\b(sk_live_[0-9a-zA-Z]{16,})\b")),
    ("npm-token", "critical", re.compile(rb"\b(npm_[A-Za-z0-9]{36})\b")),
    ("jwt", "high", re.compile(rb"\b(eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{8,})\b")),
    # fragmented for the same reason as _PRIVKEY above -- see the note there
    ("ssh-private-key-file", "critical", re.compile(_BEGIN + rb"OPENSSH PRIV" rb"ATE KEY-----")),
    ("pkcs12-password-prop", "high",
     re.compile(rb"(?i)\b(?:storePassword|keyPassword|keystorePassword|storePass|keyPass)\s*[:=]\s*"
                rb"[\"']?([^\s\"'#]{4,})")),
]

# paths whose mere presence is a finding
_BLOB_CLASSES = [
    ("keystore", "critical", (".jks", ".keystore", ".p12", ".pfx", ".bks")),
    ("private-key-file", "critical", (".key", "_rsa", "_ed25519", "_ecdsa")),
    ("account-store", "critical", (".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3")),
    ("session-transcript", "high", (".jsonl",)),
    ("built-package", "high", (".apk", ".aab", ".ipa")),
    ("possible-game-content", "high", (".zip", ".jar", ".kdp", ".jad", ".msd")),
]

_BINARY_EXT = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".ttf", ".otf", ".ttc",
    ".so", ".a", ".o", ".dll", ".dylib", ".exe", ".bin", ".dat", ".class", ".dex",
    ".pdf", ".mp3", ".ogg", ".wav", ".sf2", ".sf3", ".mid",
)

_HEX_NOISE_PATHS = (
    "cargo.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "go.sum", "gradle/verification-metadata.xml", "flake.lock", "composer.lock",
    "gemfile.lock", "pubspec.lock",
)

_VERSIONISH = re.compile(rb"(?i)version|gradle|agp|ndk|sdk|toolchain|semver|plugin|wrapper|dist")


def is_public_ipv4(text):
    try:
        parts = [int(p) for p in text.split(".")]
    except ValueError:
        return False
    if len(parts) != 4 or any(p > 255 for p in parts):
        return False
    a, b = parts[0], parts[1]
    if a == 0 or a == 127 or a >= 240:
        return False                              # this-network, loopback, reserved/broadcast
    if a == 10:
        return False                              # RFC1918
    if a == 172 and 16 <= b <= 31:
        return False                              # RFC1918
    if a == 192 and b == 168:
        return False                              # RFC1918
    if a == 192 and b == 0 and parts[2] in (0, 2):
        return False                              # RFC5737 doc / IETF protocol assignments
    if a == 198 and parts[1] == 51 and parts[2] == 100:
        return False                              # RFC5737 doc
    if a == 203 and b == 0 and parts[2] == 113:
        return False                              # RFC5737 doc
    if a == 169 and b == 254:
        return False                              # link-local
    if a == 100 and 64 <= b <= 127:
        return False                              # RFC6598 CGNAT
    if a == 198 and b in (18, 19):
        return False                              # benchmarking
    if 224 <= a <= 239:
        return False                              # multicast
    return True


def looks_like_binary(path_lower, blob):
    if path_lower.endswith(_BINARY_EXT):
        return True
    return b"\x00" in blob[:8192]


def mask(text):
    if len(text) <= 8:
        return text[0] + "*" * (len(text) - 1) if text else ""
    return text[:4] + "..." + text[-4:]


# --------------------------------------------------------------------------
# scanning
# --------------------------------------------------------------------------


class Finding:
    __slots__ = ("severity", "kind", "path", "line", "detail", "where", "refs", "advice")

    def __init__(self, severity, kind, path, line, detail, where, advice=""):
        self.severity = severity
        self.kind = kind
        self.path = path
        self.line = line
        self.detail = detail
        self.where = where
        self.refs = []
        self.advice = advice

    def key(self):
        return (self.kind, self.path, self.line, self.detail)

    def as_dict(self):
        return {
            "severity": self.severity, "kind": self.kind, "path": self.path,
            "line": self.line, "detail": self.detail, "where": self.where,
            "refs": sorted(self.refs), "advice": self.advice,
        }


def classify_path(path, size, opts):
    out = []
    low = path.lower()
    base = os.path.basename(low)
    for kind, severity, exts in _BLOB_CLASSES:
        if base.endswith(exts) or any(base.endswith(e) for e in exts):
            if kind == "possible-game-content":
                if size is not None and size < opts.blob_threshold:
                    continue
                if base.endswith(".zip") and ("gradle" in low or "wrapper" in low):
                    continue
            if kind == "private-key-file" and base.endswith(".key") and "tls" not in low and "test" in low:
                severity = "high"
            out.append((severity, kind,
                        "%s (%s)" % (base, "%d bytes" % size if size is not None else "size unknown")))
    if size is not None and size >= opts.blob_threshold and not out:
        if base.endswith((".bin", ".dat", ".img", ".rom", ".pak", ".res")):
            out.append(("high", "large-opaque-blob",
                        "%s (%d bytes) -- extracted guest images and decoded assets land here"
                        % (base, size)))
    return out


def scan_blob(path, blob, opts):
    """Content detectors. `path` is only used for suppression heuristics."""
    findings = []
    low = path.lower()
    base = os.path.basename(low)

    if looks_like_binary(low, blob):
        return findings
    if len(blob) > opts.max_blob_bytes:
        return findings

    def lineno(pos):
        return blob.count(b"\n", 0, pos) + 1

    for m in _PRIVKEY.finditer(blob):
        findings.append(Finding(
            "critical", "private-key-block", path, lineno(m.start()),
            m.group(0).decode("ascii", "replace"), "",
            "Rotate the key. A private key in a repository is compromised the moment it is pushed."))

    for name, severity, rx in _TOKENS:
        for m in rx.finditer(blob):
            val = (m.group(1) if m.lastindex else m.group(0)).decode("ascii", "replace")
            findings.append(Finding(
                severity, name, path, lineno(m.start()),
                val if opts.show_secrets else mask(val), "",
                "Revoke and reissue. Assume it is already scraped."))

    if base not in _HEX_NOISE_PATHS and not any(low.endswith(p) for p in _HEX_NOISE_PATHS):
        for m in _HEX64.finditer(blob):
            val = m.group(1).decode("ascii")
            if val.lower() in opts.allow_hex:
                continue
            line_start = blob.rfind(b"\n", 0, m.start()) + 1
            line_end = blob.find(b"\n", m.end())
            line = blob[line_start: line_end if line_end >= 0 else len(blob)]
            if b"sha256" in line.lower() and (b"checksum" in line.lower() or b"integrity" in line.lower()):
                continue
            findings.append(Finding(
                "medium", "sha256-hex-64", path, lineno(m.start()),
                val if opts.show_secrets else mask(val), "",
                "64 hex could be a certificate pin, a signing fingerprint or an innocent "
                "checksum. A pin is not secret but it does identify your server; decide "
                "deliberately, then allow it with --allow-hex."))

    for m in _IPV4.finditer(blob):
        val = m.group(1).decode("ascii")
        if not is_public_ipv4(val):
            continue
        if val in opts.allow_ip:
            continue
        line_start = blob.rfind(b"\n", 0, m.start()) + 1
        line_end = blob.find(b"\n", m.end())
        line = blob[line_start: line_end if line_end >= 0 else len(blob)]
        versionish = bool(_VERSIONISH.search(line))
        findings.append(Finding(
            "low" if versionish else "high", "public-ipv4", path, lineno(m.start()),
            val + ("  (line looks version-ish; probably a false positive)" if versionish else ""),
            "",
            "A hardcoded public address pins your users to one machine and tells the world "
            "where it is. Make the destination configurable, and replace the literal in "
            "documentation with a placeholder such as YOUR_SERVER_HOST."))

    for m in _EMAIL.finditer(blob):
        val = m.group(1).decode("ascii", "replace")
        low_val = val.lower()
        if low_val in opts.allow_email:
            continue
        if any(low_val.endswith(sfx) for sfx in
               ("@example.com", "@example.org", "@example.net", ".invalid", "@localhost")):
            continue
        if low_val.startswith("noreply@") or low_val.startswith("no-reply@"):
            continue
        findings.append(Finding(
            "medium", "email-address", path, lineno(m.start()),
            val if opts.show_secrets else mask(val), "",
            "Personal identifier. Keep it if it is a deliberate authorship credit; "
            "otherwise remove it and allow the ones you keep with --allow-email."))

    for m in _USERPATH.finditer(blob):
        raw = m.group(0)
        # `C:/Users/<name>` in a "do not do this" table is documentation, not a
        # leak. Flagging it teaches people to ignore the tool, which is worse
        # than the false negative of skipping a genuinely placeholder-named user.
        user_part = re.split(rb"[\\/]", raw)[-1]
        if _PLACEHOLDER_USER.match(user_part):
            continue
        val = raw.decode("utf-8", "replace")
        findings.append(Finding(
            "medium", "absolute-user-path", path, lineno(m.start()), val, "",
            "An absolute path into one developer's profile is the single most common reason "
            "a second machine cannot build a project. Replace it with a candidate list, an "
            "environment variable, or a repo-relative path with forward slashes."))

    for rx, kind in ((_SERIAL_ASSIGN, "device-serial-assignment"), (_ADB_S, "adb-serial-flag")):
        for m in rx.finditer(blob):
            val = m.group(1).decode("ascii", "replace")
            if val.lower() in ("true", "false", "none", "null", "default"):
                continue
            findings.append(Finding(
                "medium", kind, path, lineno(m.start()),
                val if opts.show_secrets else mask(val), "",
                "A hardcoded device serial makes the script work on exactly one desk. "
                "Read it from $ANDROID_SERIAL instead."))

    return findings


# --------------------------------------------------------------------------
# git plumbing
# --------------------------------------------------------------------------


def git(repo, args, binary=False, check=False, stdin=None):
    proc = subprocess.run(
        ["git", "-C", repo] + args,
        input=stdin,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    out = proc.stdout or b""
    if not binary:
        out = out.decode("utf-8", errors="replace")
    if check and proc.returncode != 0:
        sys.exit("error: git %s failed: %s"
                 % (" ".join(args), (proc.stderr or b"").decode("utf-8", "replace").strip()))
    return proc.returncode, out


def is_git_repo(repo):
    rc, out = git(repo, ["rev-parse", "--is-inside-work-tree"])
    return rc == 0 and out.strip() == "true"


def list_refs(repo):
    rc, out = git(repo, ["for-each-ref",
                         "--format=%(objectname)%09%(refname)",
                         "refs/heads", "refs/remotes", "refs/tags", "refs/stash",
                         "refs/original", "refs/notes"])
    refs = []
    for line in out.splitlines():
        if "\t" in line:
            sha, name = line.split("\t", 1)
            refs.append((sha.strip(), name.strip()))
    rc, head = git(repo, ["rev-parse", "HEAD"])
    if rc == 0 and head.strip():
        refs.append((head.strip(), "HEAD"))
    return refs


def object_sizes(repo):
    rc, out = git(repo, ["cat-file", "--batch-check", "--batch-all-objects"])
    sizes = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] == "blob":
            try:
                sizes[parts[0]] = int(parts[2])
            except ValueError:
                pass
    return sizes


def blobs_for_ref(repo, ref_sha):
    rc, out = git(repo, ["rev-list", "--objects", ref_sha])
    if rc != 0:
        return {}
    result = {}
    for line in out.splitlines():
        if " " in line:
            sha, path = line.split(" ", 1)
            result[sha] = path
    return result


def cat_file_batch(repo, shas):
    """sha -> bytes, in one git process."""
    if not shas:
        return {}
    proc = subprocess.Popen(["git", "-C", repo, "cat-file", "--batch"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)
    out, _ = proc.communicate(("\n".join(shas) + "\n").encode("ascii"))
    result = {}
    pos = 0
    n = len(out)
    while pos < n:
        nl = out.find(b"\n", pos)
        if nl < 0:
            break
        header = out[pos:nl].decode("ascii", "replace").split()
        pos = nl + 1
        if len(header) < 3:
            continue
        sha, typ, size_s = header[0], header[1], header[2]
        try:
            size = int(size_s)
        except ValueError:
            continue
        body = out[pos:pos + size]
        pos += size + 1
        if typ == "blob":
            result[sha] = body
    return result


def tracked_but_ignored(repo):
    rc, files = git(repo, ["ls-files"])
    paths = [p for p in files.splitlines() if p.strip()]
    if not paths:
        return []
    proc = subprocess.run(
        ["git", "-C", repo, "check-ignore", "--no-index", "-v", "--stdin"],
        input="\n".join(paths).encode("utf-8"),
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    out = (proc.stdout or b"").decode("utf-8", "replace")
    rows = []
    for line in out.splitlines():
        if "\t" in line:
            source, path = line.rsplit("\t", 1)
            rows.append((source.strip(), path.strip()))
    return rows


_BACKUP_NAME = re.compile(
    r"(?i)(backup|bak\b|pre-purge|prepurge|before|old\b|leak|orig|original|keep|"
    r"snapshot|archive|wip-save|tmp\b|temp\b)")


def suspicious_refs(repo):
    out = []
    for sha, name in list_refs(repo):
        if name == "HEAD":
            continue
        if name.startswith("refs/original/"):
            out.append(("critical", name, sha,
                        "left over from a previous history rewrite; it holds the ORIGINAL, "
                        "unpurged objects and will resurrect them"))
        elif _BACKUP_NAME.search(name.rsplit("/", 1)[-1]):
            out.append(("high", name, sha,
                        "name looks like a safety copy; a history purge is undone by exactly "
                        "this kind of ref"))
        elif name == "refs/stash":
            out.append(("high", name, sha,
                        "the stash holds committed objects that no branch shows and that "
                        "`git log` will not display"))
    rc, out_txt = git(repo, ["reflog", "--all"])
    if rc == 0 and out_txt.strip():
        out.append(("medium", "(reflogs)", "",
                    "%d reflog entr%s exist. Reflogs keep purged objects alive (90 days by "
                    "default): after a rewrite you must `git reflog expire --expire=now --all` "
                    "and `git gc --prune=now` or nothing was actually deleted."
                    % (len(out_txt.strip().splitlines()),
                       "y" if len(out_txt.strip().splitlines()) == 1 else "ies")))
    return out


# --------------------------------------------------------------------------


def load_allowlist(path):
    entries = set()
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.split("#", 1)[0].strip()
                if line:
                    entries.add(line)
    return entries


DEFAULT_EXCLUDES = (
    "node_modules/",
    "vendor/",
    "third_party/",
    "target/",
    "build/",
    ".gradle/",
    ".venv/",
    "__pycache__/",
    "dist/node_modules/",
)


def _excluded(rel, prefixes):
    """True when `rel` (forward-slash, repo-relative) sits under any prefix.

    Matches on a path component boundary so `vendor/` never swallows
    `vendored_notes.md`.
    """
    for p in prefixes:
        p = p.replace("\\", "/").lstrip("./")
        if not p:
            continue
        if not p.endswith("/"):
            p += "/"
        if rel == p[:-1] or rel.startswith(p):
            return True
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="publish_audit.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Sweep the working tree and every git ref for secrets, personal "
                    "identifiers, other people's data and licensed content, before a repo "
                    "is published.",
        epilog=(
            "Run it twice: once now, and once with --all-refs before the first push.\n"
            "\n"
            "Remember what a clean working tree does NOT prove:\n"
            "  * a tracked file is never re-evaluated against .gitignore, so adding a rule\n"
            "    that matches it changes nothing;\n"
            "  * a file deleted at the tip still ships with every clone;\n"
            "  * a purge is undone by the backup branch you kept just in case;\n"
            "  * reflogs keep purged objects alive for 90 days unless you expire them.\n"
            "\n"
            "And if something WAS pushed: rotate the credential first, purge second. The\n"
            "purge is housekeeping; the rotation is the fix."
        ),
    )
    ap.add_argument("--repo", default=".", help="repository or directory to audit (default .)")
    ap.add_argument("--all-refs", action="store_true",
                    help="also scan every git ref, and report which refs each hit is reachable "
                         "from. Slower, and the only mode that tells the truth about a clone.")
    ap.add_argument("--fail-on", choices=SEVERITIES, default="medium",
                    help="exit non-zero when a finding at or above this severity exists "
                         "(default medium)")
    ap.add_argument("--blob-threshold", type=int, default=262144,
                    help="bytes above which an archive-like file is treated as possible game "
                         "content (default 262144)")
    ap.add_argument("--max-blob-bytes", type=int, default=2 * 1024 * 1024,
                    help="skip content scanning for blobs larger than this (default 2 MiB)")
    ap.add_argument("--max-refs", type=int, default=200,
                    help="stop after this many refs (default 200)")
    ap.add_argument("--allow-ip", action="append", default=[],
                    help="a public IPv4 you have decided is fine. Repeatable.")
    ap.add_argument("--allow-hex", action="append", default=[],
                    help="a 64-hex value you have decided is fine (e.g. your published cert "
                         "pin). Repeatable.")
    ap.add_argument("--allow-email", action="append", default=[],
                    help="an email address you have decided is fine. Repeatable.")
    ap.add_argument("--exclude", action="append", default=[],
                    help="path prefix to skip, relative to the repo root and using forward "
                         "slashes (e.g. vendor/). Repeatable. Adds to the defaults below.")
    ap.add_argument("--no-default-excludes", action="store_true",
                    help="also scan vendored and generated trees (" +
                         ", ".join(DEFAULT_EXCLUDES) + "). They are skipped by default "
                         "because third-party code is not your secret surface, and scanning "
                         "it buries your own findings under tens of thousands of hits.")
    ap.add_argument("--allowlist", default=".publishauditignore",
                    help="file of allowed literal values, one per line, '#' comments "
                         "(default .publishauditignore)")
    ap.add_argument("--show-secrets", action="store_true",
                    help="print matched values in full instead of masking them. Do not do this "
                         "in CI logs.")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args(argv)

    repo = args.repo.replace("\\", "/").rstrip("/") or "."
    if not os.path.isdir(repo):
        sys.exit("error: not a directory: %s" % repo)

    allowed = load_allowlist(os.path.join(repo, args.allowlist))
    args.allow_ip = set(args.allow_ip) | {a for a in allowed if re.match(r"^\d+\.\d+\.\d+\.\d+$", a)}
    args.allow_hex = {h.lower() for h in args.allow_hex} | {
        a.lower() for a in allowed if re.match(r"^[0-9a-fA-F]{64}$", a)}
    args.allow_email = {e.lower() for e in args.allow_email} | {
        a.lower() for a in allowed if "@" in a}

    findings = []
    have_git = os.path.isdir(os.path.join(repo, ".git")) or is_git_repo(repo)

    # ---- working tree -----------------------------------------------------
    shipped = []
    ignored = []
    if have_git:
        _rc, out = git(repo, ["ls-files", "--cached", "--others", "--exclude-standard"])
        shipped = [p for p in out.splitlines() if p.strip()]
        _rc, out = git(repo, ["ls-files", "--others", "--ignored", "--exclude-standard"])
        ignored = [p for p in out.splitlines() if p.strip()]
    else:
        for dirpath, dirnames, filenames in os.walk(repo):
            dirnames[:] = [d for d in dirnames
                           if d not in (".git", "node_modules", "target", "build", ".gradle")]
            for name in filenames:
                full = os.path.join(dirpath, name)
                shipped.append(os.path.relpath(full, repo).replace("\\", "/"))

    skip_prefixes = list(args.exclude)
    if not args.no_default_excludes:
        skip_prefixes += list(DEFAULT_EXCLUDES)
    if skip_prefixes:
        _before = len(shipped) + len(ignored)
        shipped = [p for p in shipped if not _excluded(p, skip_prefixes)]
        ignored = [p for p in ignored if not _excluded(p, skip_prefixes)]
        skipped_count = _before - len(shipped) - len(ignored)
    else:
        skipped_count = 0

    def scan_worktree(paths, where, base_note):
        for rel in paths:
            full = os.path.join(repo, rel)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            for severity, kind, detail in classify_path(rel, size, args):
                f = Finding(severity, kind, rel, 0, detail, where, base_note)
                findings.append(f)
            if size > args.max_blob_bytes:
                continue
            try:
                with open(full, "rb") as fh:
                    blob = fh.read()
            except OSError:
                continue
            for f in scan_blob(rel, blob, args):
                f.where = where
                if base_note and not f.advice:
                    f.advice = base_note
                findings.append(f)

    scan_worktree(shipped, "worktree", "")
    ignored_findings_start = len(findings)
    scan_worktree(ignored, "worktree-ignored", "")
    for f in findings[ignored_findings_start:]:
        # present locally but not shipped in a clone; still worth knowing about
        f.severity = SEVERITIES[max(0, sev_rank(f.severity) - 2)]
        f.advice = (f.advice + " " if f.advice else "") + \
            "(git-ignored: not in a clone today, but one `git add -f` away)"

    # ---- tracked despite .gitignore --------------------------------------
    if have_git:
        for source, path in tracked_but_ignored(repo):
            findings.append(Finding(
                "high", "tracked-despite-gitignore", path, 0,
                "matched by %s" % source, "index",
                "A TRACKED file is never re-evaluated against .gitignore. The rule that "
                "matches it does nothing at all: the file is still committed, still in "
                "history, and still in every clone. `git rm --cached` it, then decide "
                "whether history needs rewriting too."))

    # ---- refs -------------------------------------------------------------
    if have_git:
        for severity, name, sha, why in suspicious_refs(repo):
            findings.append(Finding(
                severity, "dangerous-ref", name, 0,
                (sha[:12] + " -- " if sha else "") + why, "refs",
                "Delete it BEFORE any history rewrite, not after. Order matters: refs, then "
                "rewrite, then `git reflog expire --expire=now --all`, then "
                "`git gc --prune=now`."))

    if args.all_refs:
        if not have_git:
            print("warning: --all-refs given but %s is not a git repository; only the "
                  "directory tree was scanned." % repo, file=sys.stderr)
        else:
            sizes = object_sizes(repo)
            refs = list_refs(repo)[: args.max_refs]
            print("scanning %d ref(s) ..." % len(refs), file=sys.stderr)
            blob_paths = {}          # sha -> set(path)
            blob_refs = {}           # sha -> set(refname)
            tip_cache = {}
            for sha, name in refs:
                if sha in tip_cache:
                    objs = tip_cache[sha]
                else:
                    objs = blobs_for_ref(repo, sha)
                    tip_cache[sha] = objs
                for osha, path in objs.items():
                    if osha not in sizes:
                        continue
                    blob_paths.setdefault(osha, set()).add(path)
                    blob_refs.setdefault(osha, set()).add(name)

            # path/size class first (cheap, works for huge blobs too)
            for osha, paths in blob_paths.items():
                size = sizes.get(osha)
                for path in paths:
                    for severity, kind, detail in classify_path(path, size, args):
                        f = Finding(severity, kind, path, 0, detail, "git")
                        f.refs = sorted(blob_refs.get(osha, ()))
                        f.advice = ("Present in history. Deleting it at the tip does not "
                                    "remove it from a clone.")
                        findings.append(f)

            scan_shas = [s for s in blob_paths
                         if sizes.get(s, 1 << 62) <= args.max_blob_bytes]
            CHUNK = 400
            for i in range(0, len(scan_shas), CHUNK):
                chunk = scan_shas[i:i + CHUNK]
                for osha, blob in cat_file_batch(repo, chunk).items():
                    path = sorted(blob_paths.get(osha, {"?"}))[0]
                    for f in scan_blob(path, blob, args):
                        f.where = "git"
                        f.refs = sorted(blob_refs.get(osha, ()))
                        if not f.advice:
                            f.advice = "Present in history."
                        findings.append(f)

    # ---- de-duplicate -----------------------------------------------------
    merged = {}
    for f in findings:
        k = f.key()
        if k in merged:
            prev = merged[k]
            prev.refs = sorted(set(prev.refs) | set(f.refs))
            if sev_rank(f.severity) > sev_rank(prev.severity):
                prev.severity = f.severity
            if f.where and f.where not in prev.where:
                prev.where = (prev.where + "+" + f.where).strip("+")
        else:
            merged[k] = f
    findings = sorted(merged.values(),
                      key=lambda f: (-sev_rank(f.severity), f.kind, f.path, f.line))

    threshold = sev_rank(args.fail_on)
    gating = [f for f in findings if sev_rank(f.severity) >= threshold]

    counts = {s: 0 for s in SEVERITIES}
    for f in findings:
        counts[f.severity] += 1

    if args.json:
        print(json.dumps({
            "repo": repo,
            "git": have_git,
            "all_refs": bool(args.all_refs),
            "counts": counts,
            "skipped_paths": skipped_count,
            "excluded_prefixes": skip_prefixes,
            "fail_on": args.fail_on,
            "gating": len(gating),
            "findings": [f.as_dict() for f in findings],
        }, indent=2))
    else:
        print("publish audit: %s%s" % (repo, "" if have_git else "   (not a git repository)"))
        if have_git and not args.all_refs:
            print("mode: WORKING TREE ONLY. Pass --all-refs before publishing -- a clean tree")
            print("      says nothing about what a clone receives.")
        print()
        if not findings:
            print("No findings.")
        else:
            current = None
            for f in findings:
                if f.severity != current:
                    current = f.severity
                    print("== %s ==" % current.upper())
                loc = f.path + (":%d" % f.line if f.line else "")
                print("  [%s] %s" % (f.kind, loc))
                if f.detail:
                    print("        %s" % f.detail)
                if f.refs:
                    shown = f.refs[:6]
                    more = "" if len(f.refs) <= 6 else "  (+%d more)" % (len(f.refs) - 6)
                    print("        reachable from: %s%s" % (", ".join(shown), more))
                elif f.where == "git":
                    print("        reachable from: (unattributed)")
                if f.advice:
                    print("        -> %s" % f.advice)
            print()
        print("counts: " + "  ".join("%s=%d" % (s, counts[s]) for s in reversed(SEVERITIES)))
        if skipped_count:
            print("skipped %d path(s) under: %s" % (skipped_count, "  ".join(skip_prefixes)))
            print("        (pass --no-default-excludes to scan them too)")
        print()
        print("Checklist this tool cannot automate:")
        print("  [ ] LICENSE and THIRD_PARTY_NOTICES exist, and the notices cover every")
        print("      vendored tree, bundled font and bundled audio bank -- including any")
        print("      copyleft relink obligation, which attaches to the distributed binary")
        print("      regardless of your own repository's licence.")
        print("  [ ] No game content of any kind: not the archive, not the extracted guest")
        print("      image, not decoded sprites, not dumped text tables, not the icon derived")
        print("      from the title art. Bring-your-own-dump is the only defensible posture.")
        print("  [ ] No extracted decryption key, anywhere, in any form. A key that decrypts")
        print("      a copyrighted work is a materially different act from publishing an")
        print("      offset. Not in examples, not as a tool default, not in a comment.")
        print("  [ ] Default state and log paths point OUTSIDE the source tree, so that")
        print("      running the server from a checkout cannot create user data inside a")
        print("      git repository. That is how account stores get committed.")
        print("  [ ] Deployment docs and code defaults agree on every destructive flag.")
        print("      The docs are part of the security surface: an operator who reads")
        print("      'this is OFF by default' and finds it ON has been misled by you.")
        print("  [ ] The auth model is stated in one sentence at the top of the README,")
        print("      next to the script that demonstrates it.")
        if gating:
            print()
            print("FAILED: %d finding(s) at or above '%s'." % (len(gating), args.fail_on))

    return 1 if gating else 0


if __name__ == "__main__":
    raise SystemExit(main())
