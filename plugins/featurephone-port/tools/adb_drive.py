#!/usr/bin/env python3
"""Drive an installed port on a device from a script, and capture what happened.

WHAT IT DOES
    One reproducible device run:

        adb logcat -c            clear, so the capture starts empty
        adb shell am force-stop  a cold start, so state is not carried over
        launch                   monkey (default) or an explicit component
        <scripted actions>       taps, swipes, keys, text, waits, screencaps
        adb logcat -d            dump everything the run produced
        adb exec-out screencap   a final screenshot

    The point is not automation for its own sake.  It is that a device finding is
    only evidence if somebody else can reproduce it, and "I tapped around for a
    bit" is not reproducible.  A run that produced a refutation should be
    committed as a script next to the claim it refuted.

** COORDINATES ARE DATA, NOT CODE  (A)
    Tap coordinates live in a plain text file, never inside this tool and never
    inside a shell script that gets copied between machines.  They depend on:
      * the device's physical resolution AND its display density
      * the current orientation
      * your port's own layout arithmetic -- and if your HUD is user-placeable,
        on that user's saved layout
    A coordinate file is therefore valid for ONE device in ONE orientation with
    ONE layout.  Name it accordingly (`taps-1080x2340-landscape.txt`), derive the
    numbers from your own layout code rather than by eyeballing a screenshot, and
    confirm them once with `--screencap` before trusting a whole run.

SCRIPT FILE GRAMMAR
    One command per line.  `#` starts a comment.  Blank lines are ignored.

        tap X Y                  a touch down+up at absolute screen pixels
        swipe X1 Y1 X2 Y2 [MS]   a drag (default 300 ms)
        key KEYCODE_BACK         any Android keycode name or number
        text hello world         type into the focused field (spaces preserved)
        sleep 1.5                seconds; also spelled `wait`
        screencap name.png       pull a screenshot now, into --out-dir
        log MESSAGE              write a marker into logcat, so the capture has
                                 an unambiguous boundary between steps
        shell <anything>         raw `adb shell` passthrough (use sparingly)

    Example:
        # taps-1080x2340-landscape.txt  -- device-specific, see --help
        log step1-open-menu
        tap 96 1180
        sleep 1.0
        screencap 01-menu.png
        log step2-network
        tap 540 900
        sleep 2.5

KNOWN LIMITS
    * `adb shell input tap` goes through the input subsystem, not your app, so it
      cannot distinguish a control from the game surface underneath it.  If a tap
      lands one pixel outside a control it silently becomes a game keypress and
      the run looks like a protocol bug.
    * There is no synchronisation with the app.  `sleep` is the only barrier.
      Prefer generous sleeps and a `log` marker before each step over tight
      timing; a flaky drive script is worse than no drive script.
    * Text entry needs a focused text field and escapes spaces as %s, which is
      what `input text` expects; punctuation beyond ASCII is unreliable.
    * `--logcat-grep` filters the dump by substring, not by tag priority.

REQUIRES
    adb on PATH, in $ANDROID_HOME/platform-tools, or via --adb / $ADB.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import time


def find_adb(explicit=None):
    """Candidate list, not an absolute path -- the #1 open-source portability bug
    in build tooling is a hardcoded path into one developer's profile."""
    exe = "adb.exe" if os.name == "nt" else "adb"
    candidates = []
    if explicit:
        candidates.append(explicit)
    if os.environ.get("ADB"):
        candidates.append(os.environ["ADB"])
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        root = os.environ.get(var)
        if root:
            candidates.append(os.path.join(root, "platform-tools", exe))
    candidates.append(exe)  # PATH
    for cand in candidates:
        cand = str(cand).replace("\\", "/")
        if os.path.sep in cand or "/" in cand:
            if os.path.exists(cand):
                return cand
        else:
            from shutil import which
            found = which(cand)
            if found:
                return found.replace("\\", "/")
    sys.exit(
        "error: adb not found.\n"
        "       Tried --adb, $ADB, $ANDROID_HOME/platform-tools, and PATH.\n"
        "       Install platform-tools or pass --adb /path/to/adb."
    )


class Adb:
    def __init__(self, exe, serial, dry_run=False, verbose=False):
        self.exe = exe
        self.serial = serial
        self.dry_run = dry_run
        self.verbose = verbose

    def _argv(self, args):
        argv = [self.exe]
        if self.serial:
            argv += ["-s", self.serial]
        return argv + list(args)

    def run(self, args, capture=True, check=False, binary=False):
        argv = self._argv(args)
        if self.verbose or self.dry_run:
            print("$ " + " ".join(shlex.quote(a) for a in argv), file=sys.stderr)
        if self.dry_run:
            return 0, b"" if binary else ""
        proc = subprocess.run(
            argv,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
        out = proc.stdout or b""
        if not binary:
            out = out.decode("utf-8", errors="replace")
        if check and proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
            sys.exit("error: adb %s failed (%d): %s"
                     % (" ".join(args[:2]), proc.returncode, err or "(no stderr)"))
        return proc.returncode, out

    def shell(self, cmdline, check=False):
        return self.run(["shell"] + cmdline, check=check)


def parse_script(path):
    steps = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.rstrip("\r\n")
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split(None, 1)
            verb = parts[0].lower()
            rest = parts[1] if len(parts) > 1 else ""
            steps.append((lineno, verb, rest, stripped))
    return steps


def validate(steps):
    errors = []
    for lineno, verb, rest, raw in steps:
        toks = rest.split()
        if verb == "tap":
            if len(toks) != 2 or not all(t.lstrip("-").isdigit() for t in toks):
                errors.append("line %d: tap wants two integer coordinates: %r" % (lineno, raw))
        elif verb == "swipe":
            if len(toks) not in (4, 5) or not all(t.lstrip("-").isdigit() for t in toks):
                errors.append("line %d: swipe wants X1 Y1 X2 Y2 [MS]: %r" % (lineno, raw))
        elif verb in ("sleep", "wait"):
            try:
                float(rest)
            except ValueError:
                errors.append("line %d: %s wants a number of seconds: %r" % (lineno, verb, raw))
        elif verb in ("key", "text", "log", "shell", "screencap"):
            if not rest:
                errors.append("line %d: %s needs an argument: %r" % (lineno, verb, raw))
        else:
            errors.append("line %d: unknown command %r (see --help for the grammar)"
                          % (lineno, verb))
    return errors


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="adb_drive.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Cold-start an installed port on a device, replay a scripted "
                    "sequence of taps, and capture logcat and screenshots.",
        epilog=(
            "!! TAP COORDINATES ARE PER-DEVICE AND PER-LAYOUT.\n"
            "   They depend on the device resolution, the orientation, and your port's\n"
            "   own layout arithmetic -- including, if your HUD is user-placeable, on\n"
            "   the saved layout of whoever is holding the phone. A script that works\n"
            "   on one phone will silently tap the game surface on another. Derive the\n"
            "   numbers from your layout code, name the file after the device and\n"
            "   orientation, and confirm with --screencap before trusting a whole run.\n"
            "\n"
            "   Set the device with --serial or $ANDROID_SERIAL; never hardcode a serial."
        ),
    )
    ap.add_argument("--serial", default=os.environ.get("ANDROID_SERIAL"),
                    help="device serial (default: $ANDROID_SERIAL; omit if only one device)")
    ap.add_argument("--adb", help="path to adb (default: $ADB, $ANDROID_HOME, PATH)")
    ap.add_argument("--package", required=True, metavar="APP_ID",
                    help="applicationId of the installed port, e.g. com.example.mygame")
    ap.add_argument("--activity", metavar="COMPONENT",
                    help="explicit component to start, e.g. com.example.mygame/.MainActivity. "
                         "Default: launch via monkey, which does not care that applicationId "
                         "and the Java package differ.")
    ap.add_argument("--script", required=True, help="tap/action script (see --help for grammar)")
    ap.add_argument("--out-dir", default="out/drive",
                    help="where screenshots and the logcat dump are written (default out/drive)")
    ap.add_argument("--logcat-grep", metavar="TAG",
                    help="only keep dumped logcat lines containing this substring "
                         "(your host's trace tag)")
    ap.add_argument("--logcat-out", help="write the (filtered) logcat dump here "
                                         "(default <out-dir>/logcat.txt)")
    ap.add_argument("--screencap", metavar="OUT.PNG",
                    help="take a final screenshot into this path")
    ap.add_argument("--boot-wait", type=float, default=3.0,
                    help="seconds to wait after launch before the first action (default 3)")
    ap.add_argument("--no-force-stop", action="store_true",
                    help="do not force-stop first (keeps whatever state the app was in)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print every adb command that would run, and run none of them")
    ap.add_argument("--verbose", action="store_true", help="echo each adb invocation")
    args = ap.parse_args(argv)

    if not os.path.exists(args.script):
        sys.exit("error: no such script: %s" % args.script)
    steps = parse_script(args.script)
    if not steps:
        sys.exit("error: %s contained no commands." % args.script)
    errors = validate(steps)
    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        sys.exit("error: %d problem(s) in the script; nothing was run." % len(errors))

    adb = Adb(find_adb(args.adb), args.serial, args.dry_run, args.verbose)

    if not args.dry_run:
        rc, out = adb.run(["get-state"])
        if rc != 0 or "device" not in out:
            sys.exit(
                "error: no device in state 'device'%s.\n"
                "       `adb devices` to check; pass --serial when more than one is attached."
                % (" for serial %s" % args.serial if args.serial else "")
            )

    out_dir = args.out_dir.replace("\\", "/")
    os.makedirs(out_dir, exist_ok=True)

    print("== clearing logcat ==")
    adb.run(["logcat", "-c"])

    if not args.no_force_stop:
        print("== force-stop %s ==" % args.package)
        adb.shell(["am", "force-stop", args.package])

    print("== launch ==")
    if args.activity:
        rc, out = adb.shell(["am", "start", "-n", args.activity])
        if not args.dry_run and ("Error" in out or rc != 0):
            sys.exit(
                "error: am start failed:\n%s\n"
                "       If the class is 'not found', remember that applicationId and the\n"
                "       Java package are allowed to differ. Either omit --activity (monkey\n"
                "       resolves the launcher intent) or read the real component out of\n"
                "       `aapt2 dump badging <apk>`." % out.strip()
            )
    else:
        adb.shell(["monkey", "-p", args.package, "-c",
                   "android.intent.category.LAUNCHER", "1"])

    if args.boot_wait > 0 and not args.dry_run:
        time.sleep(args.boot_wait)

    print("== %d step(s) ==" % len(steps))
    shots = []
    for lineno, verb, rest, raw in steps:
        toks = rest.split()
        if verb == "tap":
            print("  [%d] tap %s" % (lineno, rest))
            adb.shell(["input", "tap", toks[0], toks[1]])
        elif verb == "swipe":
            ms = toks[4] if len(toks) == 5 else "300"
            print("  [%d] swipe %s (%s ms)" % (lineno, " ".join(toks[:4]), ms))
            adb.shell(["input", "swipe", toks[0], toks[1], toks[2], toks[3], ms])
        elif verb == "key":
            print("  [%d] key %s" % (lineno, rest))
            adb.shell(["input", "keyevent", rest])
        elif verb == "text":
            print("  [%d] text %r" % (lineno, rest))
            adb.shell(["input", "text", rest.replace(" ", "%s")])
        elif verb in ("sleep", "wait"):
            secs = float(rest)
            print("  [%d] sleep %.2fs" % (lineno, secs))
            if not args.dry_run:
                time.sleep(secs)
        elif verb == "log":
            print("  [%d] marker %s" % (lineno, rest))
            adb.shell(["log", "-t", "ADBDRIVE", rest])
        elif verb == "screencap":
            name = rest.strip().replace("\\", "/")
            path = name if os.path.isabs(name) else out_dir + "/" + name
            print("  [%d] screencap -> %s" % (lineno, path))
            rc, png = adb.run(["exec-out", "screencap", "-p"], binary=True)
            if not args.dry_run and png:
                d = os.path.dirname(path)
                if d:
                    os.makedirs(d, exist_ok=True)
                with open(path, "wb") as fh:
                    fh.write(png)
                shots.append(path)
        elif verb == "shell":
            print("  [%d] shell %s" % (lineno, rest))
            adb.shell(shlex.split(rest))

    if args.screencap:
        path = args.screencap.replace("\\", "/")
        print("== final screencap -> %s ==" % path)
        rc, png = adb.run(["exec-out", "screencap", "-p"], binary=True)
        if not args.dry_run and png:
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(png)
            shots.append(path)

    print("== logcat dump ==")
    rc, log = adb.run(["logcat", "-d", "-v", "time"])
    kept = log
    if args.logcat_grep:
        kept = "\n".join(l for l in log.splitlines() if args.logcat_grep in l)
    log_path = (args.logcat_out or (out_dir + "/logcat.txt")).replace("\\", "/")
    if not args.dry_run:
        d = os.path.dirname(log_path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(log_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(kept)
    line_count = len(kept.splitlines())
    print("  %d line(s)%s -> %s"
          % (line_count, " matching %r" % args.logcat_grep if args.logcat_grep else "", log_path))
    if args.logcat_grep and line_count == 0 and not args.dry_run:
        print("  !! nothing matched. Either the tag is wrong, or the log level was compiled")
        print("     out of a release build -- release profiles commonly strip below INFO.")

    if shots:
        print("screenshots:")
        for s in shots:
            print("  %s" % s)

    print()
    print("Reminder: this run is evidence only if it is reproducible. Commit the script")
    print("next to the claim it settled, with the device model and orientation in its name.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
