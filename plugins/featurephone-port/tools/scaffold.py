#!/usr/bin/env python3
"""Generate a new port project from a pinned tag of the template repository.

WHY THIS IS A TOOL AND NOT A PARAGRAPH OF INSTRUCTIONS  (A)
    "Copy the template and rename things" is measurably not a task humans get
    right.  In the reference port, the blast radius of the renames was:

      * the environment-variable prefix alone: ~10 distinct identifiers,
        61 matches in the build script, 25 in the Gradle build file, 10 in the
        Rust configuration module;
      * the game-archive filename: 6 places across Rust, .gitignore (twice), a
        Python tool and the headless probe;
      * THREE separate three-way agreements that fail at RUNTIME rather than at
        build time -- most importantly the native library name, which must be
        identical in the Cargo `[lib] name`, in `System.loadLibrary(...)` and in
        the manifest's `android.app.lib_name`, or you get an UnsatisfiedLinkError
        at launch and a completely clean build;
      * ONE parameter that must NOT be renamed at all -- the Java namespace,
        because the Rust side resolves the activity class BY NAME over JNI.
        Move it and the build stays green, the app starts, and the orientation
        toggle plus every platform dialog silently stop working.

    A human doing this by hand gets it 95% right and then loses an afternoon.
    So: script it, and then VERIFY the result rather than assuming it.

WHAT IT DOES
    1. Fetches the template repository at a PINNED TAG (or uses --offline-template),
       and removes its .git so your project starts its own history.
    2. Substitutes the parameter table into file contents AND into file and
       directory names.  Token substitution is exact; any --extra-substitution you
       add is applied with WORD BOUNDARIES, never as a blind string replace.
    3. Optionally strips the optional components you did not ask for (--no-server).
    4. VERIFIES, and refuses to produce a project if any check fails:
         * no `__TEMPLATE_*__` token survived anywhere
         * the three native-library names agree
         * the Java namespace is byte-identical to the template's
    5. Writes PORT_NOTES.md (the chapter checklist plus empty slots for the four
       address-model constants), a starter target.toml, and
       TEMPLATE_PROVENANCE.txt recording repo + ref + commit + date.

    Everything happens in a temporary directory.  If any step fails, --dest is
    never created or touched.  There is no half-scaffolded state.

ON PINNING
    --template-ref should be a TAG.  A branch name is a moving target: two people
    running the same command a month apart get different projects, and neither
    can reproduce the other's bug.  The tool warns loudly if the ref is not a tag.

WHAT IT DOES NOT DO
    It does not ship, fetch, or generate any game content.  A freshly scaffolded
    project cannot build a runnable app until you supply your own archive; the
    template is expected to fail the build loudly when the archive slot is empty,
    which is the correct default for anything published.
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import shutil
import subprocess
import sys
import tempfile

# The Java namespace is deliberately NOT a parameter. See the module docstring.
TOKEN_PREFIX = "__TEMPLATE_"

PLATFORMS = ("ktf", "lgt", "skt", "j2me")

CHAPTERS = [
    ("0", "Decide and scope",
     "You can name the platform, you have the dump, and you have written down what you will NOT redistribute."),
    ("1", "Classify the dump",
     "classify_archive.py prints a platform, a descriptor, a jar name and a native member with a BSS value."),
    ("2", "Repair and prepare the archive",
     "Descriptor has zero CR bytes; the application id matches the jar basename; the native member name parses "
     "to a BSS size; no previous owner's private save entries; every surviving member byte-identical to the source."),
    ("3", "Scaffold the project, vendor the core",
     "cargo check passes for the host crate and the headless probe crate; vendor/UPSTREAM.txt records the base commit."),
    ("4", "First headless boot with a virtual clock",
     "The probe prints 'loaded', then a periodic work gauge, and a dumped frame reports non-black > 0."),
    ("5", "Walk the guest forward",
     "No fatal unimplemented platform stub for the first N minutes of scripted input; every unknown-interface "
     "warning is understood."),
    ("6", "Apply the core hardening batch",
     "30k headless frames with no allocation failure; the heap gauge's live count is flat in steady state."),
    ("7", "Pin the address model",
     "Four constants written into target.toml: image base, BSS size, static base, relocation delta -- the delta "
     "validated corpus-wide, not on five hand-picked strings."),
    ("8", "Build the string index and feature inventory",
     "A committed offset/text listing; the id-consuming accessor identified; a list of features that only exist "
     "with a live server."),
    ("9", "Map live state and prove offsets",
     "The main state pointer resolved; every offset you intend to build on has TWO independent derivations."),
    ("10", "First device build and run",
     "A debug package installs and shows the game rendering on a phone."),
    ("11", "Input, screen, storage on device",
     "You can play with the default control set; saves survive a kill; the database layout matches the probe's."),
    ("12", "Touch HUD and layout editor",
     "Controls are placeable, persisted, and the resolver's invariant holds (no control silently overlaps an obstacle)."),
    ("13", "Host-guest reads, patches, calls",
     "A read-verify-then-write patch is idempotent across new-game/continue/death; one host-initiated guest call "
     "chain completes on a device without a JVM panic."),
    ("14", "Reimplement guest widgets, differentially verify",
     "The probe reports 0 mismatched pixels between the shipping host renderer and the guest's own buffer, on at "
     "least two different game states."),
    ("15", "Socket layer and connect redirect",
     "The client connects to your address, and a mute server does not cause a teardown.  [online titles only]"),
    ("16", "Protocol RE",
     "Framing derived three ways; a request catalogue that reproduces at least two captured packets; the one reply "
     "that clears the blocking state identified; at least one device refutation recorded.  [online titles only]"),
    ("17", "Replacement server, tests, deploy",
     "The conformance suite passes; the exploit-as-test documents the threat model; the service survives a restart "
     "and a network reset.  [online titles only]"),
    ("18", "Release build, signing, distribution",
     "A signed package whose certificate fingerprint you have published, whose versionCode increments, and whose "
     "baked configuration is VERIFIED INSIDE the produced binary."),
    ("19", "Publish audit",
     "publish_audit.py is clean across ALL refs, a LICENSE and THIRD_PARTY_NOTICES exist, and no game content, key "
     "or credential is in the tree or in history."),
]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def die(msg, code=1):
    sys.exit("error: " + msg)


def run(argv, cwd=None, check=True, quiet=False):
    if not quiet:
        print("$ " + " ".join(argv), file=sys.stderr)
    proc = subprocess.run(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out = (proc.stdout or b"").decode("utf-8", errors="replace")
    err = (proc.stderr or b"").decode("utf-8", errors="replace")
    if check and proc.returncode != 0:
        die("command failed (%d): %s\n%s" % (proc.returncode, " ".join(argv), err.strip() or out.strip()))
    return proc.returncode, out, err


def is_text(path, sniff=8192):
    try:
        with open(path, "rb") as fh:
            chunk = fh.read(sniff)
    except OSError:
        return False
    if b"\x00" in chunk:
        return False
    return True


def walk_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git",)]
        for name in filenames:
            yield os.path.join(dirpath, name).replace("\\", "/")


def read_text(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def write_text(path, text):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


# --------------------------------------------------------------------------
# template acquisition
# --------------------------------------------------------------------------


def fetch_template(repo, ref, workdir):
    if shutil.which("git") is None:
        die("git is not on PATH; cannot fetch the template. Use --offline-template DIR instead.")
    dest = workdir + "/template"
    rc, out, err = run(["git", "clone", "--depth", "1", "--branch", ref, repo, dest], check=False)
    if rc != 0:
        die(
            "could not clone the template repository.\n"
            "       repo : %s\n"
            "       ref  : %s\n"
            "       git  : %s\n"
            "\n"
            "       Nothing was written; --dest was not created.\n"
            "       Check the URL and that the tag exists (`git ls-remote --tags <repo>`),\n"
            "       or work offline with --offline-template /path/to/checkout."
            % (repo, ref, (err or out).strip().splitlines()[-1] if (err or out).strip() else "(no output)")
        )
    _, sha, _ = run(["git", "-C", dest, "rev-parse", "HEAD"], quiet=True)
    _, tags, _ = run(["git", "-C", dest, "tag", "--points-at", "HEAD"], check=False, quiet=True)
    tag_list = [t.strip() for t in tags.splitlines() if t.strip()]
    if ref not in tag_list:
        print(
            "warning: --template-ref %r is not a tag on the fetched commit (%s).\n"
            "         A branch is a moving target: the same command run a month from now\n"
            "         produces a different project, and neither run can reproduce the other's\n"
            "         bug. Pin a tag." % (ref, sha.strip()[:12]),
            file=sys.stderr,
        )
    return dest, sha.strip()


def copy_offline_template(src, workdir):
    src = src.replace("\\", "/")
    if not os.path.isdir(src):
        die("--offline-template %s is not a directory." % src)
    dest = workdir + "/template"
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".git", "target", "build", ".gradle"))
    sha = "(offline copy of %s)" % src
    if shutil.which("git") and os.path.isdir(src + "/.git"):
        rc, out, _ = run(["git", "-C", src, "rev-parse", "HEAD"], check=False, quiet=True)
        if rc == 0:
            sha = out.strip() + " (offline copy)"
    return dest, sha


# --------------------------------------------------------------------------
# substitution
# --------------------------------------------------------------------------


def build_token_table(args):
    stem = args.archive_name
    for ext in (".zip", ".jar", ".kdp"):
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break
    return {
        "__TEMPLATE_NAME__": args.name,
        "__TEMPLATE_DISPLAY_NAME__": args.display,
        "__TEMPLATE_APP_ID__": args.app_id,
        "__TEMPLATE_APP_ID_PATH__": args.app_id.replace(".", "/"),
        "__TEMPLATE_ENV_PREFIX__": args.env_prefix,
        "__TEMPLATE_GRADLE_PREFIX__": args.gradle_prefix,
        "__TEMPLATE_ARCHIVE_NAME__": args.archive_name,
        "__TEMPLATE_ARCHIVE_STEM__": stem,
        "__TEMPLATE_PLATFORM__": args.platform,
        "__TEMPLATE_SCREEN_W__": str(args.screen_w),
        "__TEMPLATE_SCREEN_H__": str(args.screen_h),
    }


def substitute_text(text, tokens, extras):
    # Longest token first so that a shorter token is never a prefix of a longer one.
    for token in sorted(tokens, key=len, reverse=True):
        text = text.replace(token, tokens[token])
    for old, new in extras:
        text = re.sub(r"\b%s\b" % re.escape(old), new, text)
    return text


def substitute_name(name, tokens):
    for token in sorted(tokens, key=len, reverse=True):
        name = name.replace(token, tokens[token])
    return name


def apply_substitutions(root, tokens, extras):
    changed_files = 0
    # contents first
    for path in list(walk_files(root)):
        if not is_text(path):
            continue
        original = read_text(path)
        updated = substitute_text(original, tokens, extras)
        if updated != original:
            write_text(path, updated)
            changed_files += 1
    # then names, deepest first so a renamed parent does not invalidate a child path
    renamed = 0
    for dirpath, dirnames, filenames in list(os.walk(root, topdown=False)):
        for name in filenames:
            new = substitute_name(name, tokens)
            if new != name:
                os.replace(os.path.join(dirpath, name), os.path.join(dirpath, new))
                renamed += 1
        base = os.path.basename(dirpath)
        new_base = substitute_name(base, tokens)
        if new_base != base and dirpath != root:
            os.replace(dirpath, os.path.join(os.path.dirname(dirpath), new_base))
            renamed += 1
    return changed_files, renamed


# --------------------------------------------------------------------------
# optional components
# --------------------------------------------------------------------------


def read_optional_manifest(root):
    """Very small reader for the template's optional-component declaration.

    scaffold.toml at the template root, e.g.

        [optional.server]
        paths = ["server", "docs/server.md"]
    """
    path = root + "/scaffold.toml"
    if not os.path.exists(path):
        return {}
    groups = {}
    current = None
    for raw in read_text(path).splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^\[optional\.([A-Za-z0-9_-]+)\]$", line)
        if m:
            current = m.group(1)
            groups[current] = []
            continue
        if current and line.startswith("paths"):
            inner = line.split("=", 1)[1].strip()
            groups[current] = re.findall(r'"([^"]+)"', inner)
    return groups


def strip_component(root, name, manifest):
    paths = manifest.get(name)
    if paths is None:
        paths = [name]
        print("warning: the template declares no [optional.%s] in scaffold.toml; "
              "falling back to removing '%s/'." % (name, name), file=sys.stderr)
    removed = []
    for rel in paths:
        target = os.path.join(root, rel.replace("\\", "/"))
        if os.path.isdir(target):
            shutil.rmtree(target)
            removed.append(rel + "/")
        elif os.path.exists(target):
            os.remove(target)
            removed.append(rel)
    return removed


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------


def find_residual_tokens(root):
    hits = []
    for path in walk_files(root):
        if not is_text(path):
            continue
        text = read_text(path)
        for m in re.finditer(re.escape(TOKEN_PREFIX) + r"[A-Z0-9_]*__", text):
            line = text.count("\n", 0, m.start()) + 1
            hits.append((os.path.relpath(path, root).replace("\\", "/"), line, m.group(0)))
        base = os.path.basename(path)
        if TOKEN_PREFIX in base:
            hits.append((os.path.relpath(path, root).replace("\\", "/"), 0, base))
    return hits


def collect_lib_names(root):
    """-> dict with the three places the native library name must agree."""
    found = {"cargo": [], "loadLibrary": [], "manifest": []}
    for path in walk_files(root):
        rel = os.path.relpath(path, root).replace("\\", "/")
        base = os.path.basename(path).lower()
        if base == "cargo.toml" and is_text(path):
            text = read_text(path)
            if "cdylib" in text:
                m = re.search(r"\[lib\][^\[]*?name\s*=\s*\"([^\"]+)\"", text, re.S)
                if m:
                    found["cargo"].append((rel, m.group(1)))
        elif base.endswith((".java", ".kt")) and is_text(path):
            for m in re.finditer(r"loadLibrary\s*\(\s*\"([^\"]+)\"\s*\)", read_text(path)):
                found["loadLibrary"].append((rel, m.group(1)))
        elif base == "androidmanifest.xml" and is_text(path):
            text = read_text(path)
            m = re.search(
                r"android:name\s*=\s*\"android\.app\.lib_name\"[^>]*android:value\s*=\s*\"([^\"]+)\"",
                text, re.S)
            if not m:
                m = re.search(
                    r"android:value\s*=\s*\"([^\"]+)\"[^>]*android:name\s*=\s*\"android\.app\.lib_name\"",
                    text, re.S)
            if m:
                found["manifest"].append((rel, m.group(1)))
    return found


def find_namespace(root):
    """-> (namespace, file) from the Gradle build, or (None, None)."""
    for path in walk_files(root):
        base = os.path.basename(path)
        if base in ("build.gradle.kts", "build.gradle") and is_text(path):
            text = read_text(path)
            m = re.search(r"\bnamespace\s*=?\s*[\"']([A-Za-z0-9_.]+)[\"']", text)
            if m:
                return m.group(1), os.path.relpath(path, root).replace("\\", "/")
    return None, None


def verify(root, template_namespace):
    problems = []
    notes = []

    residual = find_residual_tokens(root)
    if residual:
        for rel, line, tok in residual[:20]:
            problems.append("residual template token %s at %s:%s" % (tok, rel, line or "(filename)"))
        if len(residual) > 20:
            problems.append("... and %d more residual tokens" % (len(residual) - 20))

    libs = collect_lib_names(root)
    names = set()
    for kind in ("cargo", "loadLibrary", "manifest"):
        for _rel, value in libs[kind]:
            names.add(value)
    if not names:
        notes.append("could not locate any native-library name declaration to cross-check "
                     "(no cdylib Cargo.toml, no loadLibrary call, no android.app.lib_name)")
    elif len(names) > 1:
        detail = "; ".join(
            "%s: %s" % (kind, ", ".join("%s=%r" % (rel, val) for rel, val in libs[kind]) or "(none)")
            for kind in ("cargo", "loadLibrary", "manifest"))
        problems.append(
            "the native library name DISAGREES between its three declaration sites (%s). "
            "This does not fail the build -- it fails at launch with UnsatisfiedLinkError." % detail)
    else:
        for kind in ("cargo", "loadLibrary", "manifest"):
            if not libs[kind]:
                notes.append("no %s declaration of the native library name was found; the other "
                             "sites agree on %r" % (kind, next(iter(names))))

    ns, ns_file = find_namespace(root)
    if template_namespace and ns and ns != template_namespace:
        problems.append(
            "the Java namespace changed: template had %r, generated project has %r (%s). "
            "The namespace must NOT be renamed: the Rust side resolves the activity class by "
            "name over JNI, so a rename builds cleanly, launches, and silently breaks every "
            "JNI call at runtime." % (template_namespace, ns, ns_file))
    elif ns:
        expected_dir = ns.replace(".", "/")
        if not any(("/" + expected_dir + "/") in p for p in walk_files(root)):
            problems.append(
                "namespace is %r but no source directory .../%s/ exists. The Gradle namespace, "
                "the source directory and the Rust JNI lookups must all agree."
                % (ns, expected_dir))
        slash = ns.replace(".", "/")
        rust_hits = [p for p in walk_files(root)
                     if p.endswith(".rs") and is_text(p) and (slash in read_text(p) or ns in read_text(p))]
        if not rust_hits:
            notes.append("no Rust source mentions the namespace %r; if your host resolves an "
                         "activity class over JNI, check that lookup by hand." % ns)

    return problems, notes


# --------------------------------------------------------------------------
# generated files
# --------------------------------------------------------------------------


def port_notes(args, provenance):
    L = []
    L.append("# %s -- port notes" % args.display)
    L.append("")
    L.append("Generated by scaffold.py on %s." % datetime.date.today().isoformat())
    L.append("")
    L.append("This file is the project's memory. Keep it honest: record the SYMPTOM, the DATE")
    L.append("and the MEASUREMENT next to every constant you write down, so that the next")
    L.append("person (including you) cannot 'clean up' a value they do not understand.")
    L.append("")
    L.append("Evidence tags used throughout this project:")
    L.append("")
    L.append("| tag | meaning |")
    L.append("|---|---|")
    L.append("| (A) | generic procedure -- true for any title on this platform |")
    L.append("| (B) | worked example -- true for THIS title, method generalises |")
    L.append("| (C) | do not copy -- machine-, device- or account-specific |")
    L.append("")
    L.append("Confidence on every behavioural claim: HIGH / MED / LOW  x  CONFIRMED / INFERRED.")
    L.append("A claim is CONFIRMED only when something outside the disassembly agreed with it.")
    L.append("When a claim is refuted, write the correction BELOW the original and keep the")
    L.append("original visible. A deleted wrong claim gets rediscovered; a struck-through one")
    L.append("does not.")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## Project parameters")
    L.append("")
    L.append("| parameter | value |")
    L.append("|---|---|")
    L.append("| project name | `%s` |" % args.name)
    L.append("| display name | %s |" % args.display)
    L.append("| application id | `%s` -- PERMANENT after a store upload; it is also the "
             "on-device data path |" % args.app_id)
    L.append("| env var prefix | `%s_` |" % args.env_prefix)
    L.append("| gradle prop prefix | `%s.` |" % args.gradle_prefix)
    L.append("| archive filename | `%s` (you supply this; it is git-ignored) |" % args.archive_name)
    L.append("| platform backend | `%s` |" % args.platform)
    L.append("| guest screen | %dx%d |" % (args.screen_w, args.screen_h))
    L.append("")
    L.append("---")
    L.append("")
    L.append("## The address model -- chapter 7 fills these in")
    L.append("")
    L.append("Nothing downstream is trustworthy until all four are pinned and the fourth is")
    L.append("validated corpus-wide. Copy them into `target.toml` when you have them.")
    L.append("")
    L.append("| constant | value | how it was determined | confidence |")
    L.append("|---|---|---|---|")
    L.append("| `image_base` | `TODO` | the base passed to the loader's `load(data, BASE, ...)` "
             "call in the platform backend | |")
    L.append("| `bss_size` | `TODO` | parsed out of the native member's own filename in the "
             "archive | |")
    L.append("| `sl_fo` (RWPI static base, as a FILE OFFSET) | `TODO` | needs THREE independent "
             "routes to agree | |")
    L.append("| `reloc_delta` | `TODO` | solve from known string references, then validate over "
             "the whole corpus, not five hand-picked strings | |")
    L.append("| `reloc_unit` | `TODO` (`file_offset` or `guest_address`) | ** determine the UNIT "
             "before adopting the delta -- get this wrong and every static tool lies ** | |")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## Chapter checklist")
    L.append("")
    L.append("Do not advance without the exit criterion. Real work loops 7 <-> 9 <-> 13 (RE,")
    L.append("patch, verify) and 12 <-> 14 (UI, verification); returning to an earlier chapter")
    L.append("is normal and is not a restart.")
    L.append("")
    for num, title, exit_crit in CHAPTERS:
        if args.no_server and num in ("15", "16", "17"):
            L.append("- [ ] ~~**%s. %s**~~ -- skipped (--no-server: offline title)" % (num, title))
            continue
        L.append("- [ ] **%s. %s**" % (num, title))
        L.append("      _exit:_ %s" % exit_crit)
    L.append("")
    L.append("---")
    L.append("")
    L.append("## Refutation log")
    L.append("")
    L.append("Budget at least one refutation per feature. Static RE gets the SHAPES; only a run")
    L.append("on the real thing settles semantics and error behaviour. Every row here is worth")
    L.append("more than a page of disassembly notes.")
    L.append("")
    L.append("| date | claim (from static RE) | reality (measured) | where |")
    L.append("|---|---|---|---|")
    L.append("| | | | |")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## What must never be committed")
    L.append("")
    L.append("- the game archive, the extracted guest binary, and anything decoded from them")
    L.append("- any signing keystore or its passwords (keep the keystore OUTSIDE this repo)")
    L.append("- account stores, write-ahead logs, transcripts, save blobs -- these are other")
    L.append("  people's data, and in a bearer-token model the id IS the password")
    L.append("- extracted decryption keys. A key that decrypts a copyrighted work is a")
    L.append("  materially different thing from an offset. Out of scope for this project.")
    L.append("")
    L.append("Run `publish_audit.py --all-refs` before the first push and before every release.")
    L.append("A .gitignore prevents future additions and never past ones, and a history purge")
    L.append("is undone by the backup branch you kept 'just in case'.")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## Template provenance")
    L.append("")
    L.append("```")
    L.append(provenance)
    L.append("```")
    L.append("")
    return "\n".join(L)


def starter_target_toml(args):
    return "\n".join([
        "# target.toml -- everything that changes per title lives here and nowhere else.",
        "# Every static RE tool in the featurephone-port plugin takes -t target.toml.",
        "#",
        "# Filled in during chapter 7. Until then the tools will refuse to run, which is",
        "# correct: a static tool with a guessed address model produces confident nonsense.",
        "",
        '# The extracted native guest image. Relative paths resolve against THIS file.',
        'binary       = "work/guest.bin"',
        "",
        "# From the platform backend's load(data, BASE, ...) call.",
        "image_base   = 0x0            # TODO chapter 7",
        "",
        "# Parsed out of the native member's own filename inside the archive.",
        "bss_size     = 0              # TODO chapter 7",
        "",
        "# RWPI static base, as a FILE OFFSET. Needs three independent derivations.",
        "sl_fo        = 0x0            # TODO chapter 7",
        "",
        "# Add to a stored word to recover ... (see reloc_unit)",
        "reloc_delta  = 0x0            # TODO chapter 7",
        "",
        '# ... "file_offset" or "guest_address". DETERMINE THE UNIT BEFORE ADOPTING THE',
        "# DELTA. Get this wrong and every tool lies to you consistently enough to believe.",
        'reloc_unit   = "file_offset"  # TODO chapter 7',
        "",
        "# Text encoding of the game's strings.",
        'encoding     = "utf-8"        # TODO chapter 8',
        "",
        "# Default decode mode for the disassembler.",
        'isa          = "thumb"',
        "",
        "# Optional: the static slot holding the pointer to the main game state object.",
        "# god_object = 0x0            # TODO chapter 9",
        "",
    ])


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def validate_args(args):
    if not re.match(r"^[a-z][a-z0-9_-]*$", args.name):
        die("--name %r must be lowercase letters, digits, '_' and '-', starting with a letter."
            % args.name)
    if not re.match(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$", args.app_id):
        die("--app-id %r is not a valid Android applicationId: it needs at least two "
            "dot-separated segments, each starting with a letter." % args.app_id)
    args.env_prefix = args.env_prefix.rstrip("_")
    if not re.match(r"^[A-Z][A-Z0-9_]*$", args.env_prefix):
        die("--env-prefix %r must be UPPERCASE letters, digits and '_' (no trailing '_' needed)."
            % args.env_prefix)
    args.gradle_prefix = args.gradle_prefix.rstrip(".")
    if not re.match(r"^[a-z][A-Za-z0-9_]*$", args.gradle_prefix):
        die("--gradle-prefix %r must start with a lowercase letter and contain only letters, "
            "digits and '_' (no trailing '.' needed)." % args.gradle_prefix)
    if not re.match(r"^[A-Za-z0-9._-]+\.(zip|jar|kdp)$", args.archive_name):
        die("--archive-name %r should be a plain filename ending in .zip, .jar or .kdp."
            % args.archive_name)
    if args.platform not in PLATFORMS:
        die("--platform %r must be one of: %s" % (args.platform, ", ".join(PLATFORMS)))
    if args.screen_w <= 0 or args.screen_h <= 0:
        die("--screen wants positive dimensions, e.g. --screen 240x320")


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="scaffold.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Generate a new feature-phone port project from a pinned tag of the "
                    "template repository, substituting every parameter and then verifying "
                    "the result.",
        epilog=(
            "Example:\n"
            "  python scaffold.py --dest ./mygame --name mygame --display \"My Game\" \\\n"
            "      --app-id com.example.mygame --env-prefix MYGAME --gradle-prefix mygame \\\n"
            "      --archive-name mygame.zip --platform ktf --template-ref v0.1.0\n"
            "\n"
            "Two parameters deserve a second look before you commit to them:\n"
            "  --app-id   is PERMANENT once the app has been uploaded to a store, and it is\n"
            "             also the on-device data directory: changing it later makes a\n"
            "             different app with no access to anyone's saves.\n"
            "  the Java namespace is NOT a parameter and must not be renamed. The Rust side\n"
            "             resolves the activity class by name over JNI; renaming it produces\n"
            "             a clean build, a launching app, and dead dialogs.\n"
            "\n"
            "Nothing is written to --dest unless every verification passes."
        ),
    )
    ap.add_argument("--dest", required=True, help="directory to create (must not already exist)")
    ap.add_argument("--name", required=True, help="project name, lowercase, e.g. mygame")
    ap.add_argument("--display", required=True, help='human-readable name, e.g. "My Game"')
    ap.add_argument("--app-id", required=True, help="Android applicationId, e.g. com.example.mygame")
    ap.add_argument("--env-prefix", required=True,
                    help="UPPERCASE prefix for build-time environment variables, e.g. MYGAME")
    ap.add_argument("--gradle-prefix", required=True,
                    help="lowercase prefix for gradle.properties keys, e.g. mygame")
    ap.add_argument("--archive-name", required=True,
                    help="filename of the game archive you will supply, e.g. mygame.zip")
    ap.add_argument("--platform", required=True, choices=PLATFORMS,
                    help="emulator backend for this dump")
    ap.add_argument("--screen", default="240x320", metavar="WxH",
                    help="guest screen size (default 240x320)")
    ap.add_argument("--template-repo", default=os.environ.get("FEATUREPHONE_TEMPLATE_REPO"),
                    help="template repository URL (default: $FEATUREPHONE_TEMPLATE_REPO)")
    ap.add_argument("--template-ref", default="",
                    help="TAG of the template to fetch. A branch works but is not reproducible.")
    ap.add_argument("--offline-template", metavar="DIR",
                    help="use a local checkout instead of fetching")
    ap.add_argument("--no-server", action="store_true",
                    help="omit the replacement-server component (most titles are offline)")
    ap.add_argument("--extra-substitution", action="append", default=[], metavar="OLD=NEW",
                    help="additional WORD-BOUNDARY replacement applied to every text file. "
                         "Repeatable. Never a blind string replace.")
    ap.add_argument("--force", action="store_true",
                    help="allow --dest to exist if it is empty")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and the parameter table, write nothing")
    args = ap.parse_args(argv)

    m = re.match(r"^\s*(\d+)\s*[xX]\s*(\d+)\s*$", args.screen)
    if not m:
        die("--screen wants WxH, e.g. --screen 240x320")
    args.screen_w, args.screen_h = int(m.group(1)), int(m.group(2))
    validate_args(args)

    extras = []
    for spec in args.extra_substitution:
        if "=" not in spec:
            die("--extra-substitution wants OLD=NEW, got %r" % spec)
        old, new = spec.split("=", 1)
        if not old:
            die("--extra-substitution has an empty OLD in %r" % spec)
        extras.append((old, new))

    dest = args.dest.replace("\\", "/").rstrip("/")
    if os.path.exists(dest):
        if not (args.force and os.path.isdir(dest) and not os.listdir(dest)):
            die("--dest %s already exists. Choose a new directory, or pass --force if it is "
                "empty." % dest)

    if not args.offline_template:
        if not args.template_repo:
            die(
                "no template source.\n"
                "       Give --template-repo <url> (or set $FEATUREPHONE_TEMPLATE_REPO), or\n"
                "       work from a local checkout with --offline-template DIR.\n"
                "       Nothing was written."
            )
        if not args.template_ref:
            die(
                "--template-ref is required when fetching.\n"
                "       Pin a TAG so the scaffold is reproducible, e.g. --template-ref v0.1.0.\n"
                "       `git ls-remote --tags %s` lists what is available." % args.template_repo
            )

    tokens = build_token_table(args)

    print("parameter table")
    print("-" * 60)
    for k in sorted(tokens):
        print("  %-28s %s" % (k, tokens[k]))
    for old, new in extras:
        print("  %-28s %s  (word-boundary)" % ("\\b" + old + "\\b", new))
    print("  %-28s %s" % ("Java namespace", "UNCHANGED (not a parameter)"))
    print("-" * 60)

    if args.dry_run:
        print()
        print("dry run: would %s and write %s" % (
            ("copy %s" % args.offline_template) if args.offline_template
            else ("clone %s at %s" % (args.template_repo, args.template_ref)),
            dest))
        if args.no_server:
            print("dry run: would strip the optional 'server' component")
        return 0

    workdir = tempfile.mkdtemp(prefix="fp-scaffold-")
    try:
        if args.offline_template:
            tmpl, sha = copy_offline_template(args.offline_template, workdir)
            source_desc = "offline: %s" % args.offline_template.replace("\\", "/")
        else:
            tmpl, sha = fetch_template(args.template_repo, args.template_ref, workdir)
            source_desc = "%s @ %s" % (args.template_repo, args.template_ref)

        template_namespace, ns_file = find_namespace(tmpl)
        manifest = read_optional_manifest(tmpl)

        git_dir = tmpl + "/.git"
        if os.path.isdir(git_dir):
            shutil.rmtree(git_dir, ignore_errors=True)
        for stray in (".github/workflows/release-template.yml",):
            p = tmpl + "/" + stray
            if os.path.exists(p):
                os.remove(p)

        removed = []
        if args.no_server:
            removed = strip_component(tmpl, "server", manifest)

        changed, renamed = apply_substitutions(tmpl, tokens, extras)

        problems, notes = verify(tmpl, template_namespace)
        if problems:
            print()
            print("VERIFICATION FAILED -- %s was not created." % dest)
            for p in problems:
                print("  * %s" % p)
            print()
            print("  This is the scaffold refusing to hand you a project that builds cleanly")
            print("  and fails at runtime. Fix the template (or the parameters) and re-run.")
            return 2
        for n in notes:
            print("note: %s" % n, file=sys.stderr)

        provenance = "\n".join([
            "template  : %s" % source_desc,
            "commit    : %s" % sha,
            "scaffolded: %s" % datetime.datetime.now().isoformat(timespec="seconds"),
            "tool      : scaffold.py (featurephone-port)",
            "",
            "Refresh this file whenever you re-take the template, the same way",
            "vendor/UPSTREAM.txt records the emulator fork's base commit. Without a",
            "recorded base you cannot diff your changes against upstream or rebase onto",
            "a newer one, and 'track the fork' becomes impossible.",
        ])
        write_text(tmpl + "/TEMPLATE_PROVENANCE.txt", provenance + "\n")
        write_text(tmpl + "/PORT_NOTES.md", port_notes(args, provenance))
        if not os.path.exists(tmpl + "/target.toml"):
            write_text(tmpl + "/target.toml", starter_target_toml(args))

        parent = os.path.dirname(os.path.abspath(dest))
        if parent:
            os.makedirs(parent, exist_ok=True)
        if os.path.isdir(dest):
            os.rmdir(dest)
        shutil.move(tmpl, dest)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    print("scaffolded %s" % dest)
    print("  %d file(s) rewritten, %d path(s) renamed" % (changed, renamed))
    if removed:
        print("  stripped optional component 'server': %s" % ", ".join(removed))
    print("  wrote PORT_NOTES.md, target.toml, TEMPLATE_PROVENANCE.txt")
    print()
    print("Next:")
    print("  1. Put your own game archive where the build expects it. Nothing in this")
    print("     project ships game content, and the build should fail loudly until you do.")
    print("  2. Chapter 3: vendor the emulator core and record its base commit.")
    print("  3. Chapter 4: first headless boot -- remember the virtual clock, or the screen")
    print("     stays black and the loader gets blamed.")
    print("  4. Work through PORT_NOTES.md. Do not advance past an exit criterion.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
