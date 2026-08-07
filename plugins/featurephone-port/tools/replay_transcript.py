#!/usr/bin/env python3
"""Replay a captured protocol transcript through your replacement server's handler.

WHAT IT DOES
    Takes a JSONL transcript recorded by your server (or by a host-side packet
    tap) and drives your own `handle(bytes) -> list[bytes]` protocol handler with
    the EXACT frames a real client sent, asserting that it produces the exact
    replies the real server produced.  Then, with `--emit-test`, it writes that
    replay out as a standalone conformance test you can commit.

WHY THIS EXISTS  (A)
    The oracle is expensive.  The real client is the only authority on your
    protocol, and consulting it costs minutes plus a physical device.  A
    transcript replay is a fast, device-free proxy for "this will not crash the
    guest", so device time is spent on genuinely new questions instead of
    re-confirming what you already learned.

    It is also why the handler's signature matters.  Keep protocol semantics in
    a layer whose entire contract is

        handler.handle(payload: bytes) -> list[bytes]     # payloads, not frames

    with no sockets, no asyncio and no I/O except a store.  That single choice is
    what makes this tool -- and a hundred assertions with no network -- possible.
    A handler that reaches for a socket cannot be replayed.

============================================================================
THE TRANSCRIPT SCHEMA -- this is part of the contract, implement it verbatim
============================================================================
    Append-only JSONL, one JSON object per line, flushed per line so a crashed
    session still leaves a usable file.  Fields:

      t          float   seconds since the session started, from a MONOTONIC
                         clock (time.monotonic()), not wall clock.  Wall clock
                         jumps; a jump in a transcript looks like a protocol
                         stall and you will chase it.
      ev         string  one of:
                           "rx"          a frame arrived from the client
                           "tx"          a frame was sent to the client
                           "connect"     a session opened
                           "disconnect"  a session closed
                           "desync"      the frame decoder lost sync
                           "idle_timeout"
                           "rate_limited"
      sid        int     session id, so interleaved sessions can be separated
      n          int     per-session sequence number of this event
      gap        float   seconds since the PREVIOUS event of the same direction
                         in this session.  Not decoration: the gap column is what
                         later gives you a measured heartbeat interval and hence
                         a defensible idle timeout, instead of a guess.  (A)
      total_len  int     the frame's header length field, as it appeared on the
                         wire (useful for catching your own encoder's mistakes)
      payload    string  lowercase hex of the frame PAYLOAD -- i.e. after the
                         length header, starting at the category/opcode byte.
                         Payload, not frame: the handler layer must never see
                         framing.

    Optional fields this tool tolerates and ignores: "peer", "note", "masked".

    ** Masking.  A transcript records every user's credential, saves and address
    in one file.  Default it OFF, keep its default path OUTSIDE the source tree,
    and mask credential-bearing fields with a field ALLOWLIST (log these bytes)
    rather than an opcode denylist (hide these opcodes) -- a denylist fails open
    the moment you add an opcode.  Masked records should carry "masked": true so
    a replay can skip them instead of failing on redacted bytes.

============================================================================

HOW THE REPLAY PAIRS FRAMES
    For each "rx" record, the handler is called with its payload.  The expected
    replies are the "tx" records that follow it, in order, up to the next "rx"
    of the same session.  A request the real server answered with silence is
    expected to produce [].  This mirrors the request-response pacing a
    feature-phone guest requires: its completed-frame ring is tiny, so answering
    faster than request-response overwrites entries the guest has not consumed.

KNOWN LIMITS
    * Server-initiated pushes (a "tx" with no preceding "rx") cannot be attributed
      to a request; they are reported as UNPAIRED and skipped, not failed.
    * A stateful handler is stateful: replaying session 7 alone will fail if its
      login happened in session 3.  Use --session to replay one session at a time,
      which is what you want anyway.
    * The handler is constructed once per session with no arguments.  If yours
      needs a store or config, pass a factory: --handler mypkg.testing:make_handler
    * A masked transcript cannot be replayed byte-exactly.  Records carrying
      "masked": true are skipped with a count, unless --replay-masked.
"""

from __future__ import annotations

import os
import sys

# This tools directory contains `dis.py`, which shadows the stdlib `dis` that
# `inspect` needs -- and the user handler we are about to import may well pull
# `inspect` in. Pin the real one first. (See extract_packets.py for the full
# explanation.)
_HERE = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")
if "dis" not in sys.modules:
    _saved_path = sys.path[:]
    sys.path[:] = [p for p in sys.path
                   if os.path.abspath(p or ".").replace("\\", "/") != _HERE]
    try:
        import dis as _stdlib_dis  # noqa: F401
    except Exception:  # pragma: no cover
        pass
    finally:
        sys.path[:] = _saved_path

import argparse  # noqa: E402
import importlib  # noqa: E402
import json  # noqa: E402


# --------------------------------------------------------------------------
# transcript loading
# --------------------------------------------------------------------------

_VALID_EV = {"rx", "tx", "connect", "disconnect", "desync", "idle_timeout", "rate_limited"}


class SchemaError(Exception):
    pass


def load_transcript(path, strict_schema=True):
    records = []
    problems = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line or line.startswith("//") or line.startswith("#"):
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                problems.append("line %d: not JSON (%s)" % (lineno, exc))
                continue
            if not isinstance(rec, dict):
                problems.append("line %d: not a JSON object" % lineno)
                continue
            ev = rec.get("ev")
            if ev not in _VALID_EV:
                problems.append("line %d: unknown ev %r (schema: %s)"
                                % (lineno, ev, ", ".join(sorted(_VALID_EV))))
                if strict_schema:
                    continue
            if ev in ("rx", "tx"):
                pay = rec.get("payload")
                if not isinstance(pay, str):
                    problems.append("line %d: %s record has no hex 'payload' string" % (lineno, ev))
                    continue
                try:
                    rec["_bytes"] = bytes.fromhex(pay.replace(" ", ""))
                except ValueError:
                    problems.append("line %d: 'payload' is not hex" % lineno)
                    continue
            rec["_lineno"] = lineno
            records.append(rec)
    return records, problems


def split_sessions(records):
    sessions = {}
    for rec in records:
        sid = rec.get("sid", 0)
        sessions.setdefault(sid, []).append(rec)
    return sessions


def pair_exchanges(session_records):
    """[(rx_record, [tx_record, ...]), ...] plus a list of unpaired tx records."""
    exchanges = []
    unpaired = []
    current = None
    for rec in session_records:
        ev = rec.get("ev")
        if ev == "rx":
            current = (rec, [])
            exchanges.append(current)
        elif ev == "tx":
            if current is None:
                unpaired.append(rec)
            else:
                current[1].append(rec)
        elif ev in ("disconnect", "idle_timeout"):
            current = None
    return exchanges, unpaired


# --------------------------------------------------------------------------
# handler loading
# --------------------------------------------------------------------------


def load_handler_factory(spec, extra_paths):
    for p in extra_paths:
        p = os.path.abspath(p).replace("\\", "/")
        if p not in sys.path:
            sys.path.insert(0, p)
    if ":" not in spec:
        raise SystemExit(
            "error: --handler wants module:attr, e.g.  --handler mypkg.proto:ProtocolHandler\n"
            "       got %r" % spec
        )
    modname, attr = spec.rsplit(":", 1)
    try:
        mod = importlib.import_module(modname)
    except ImportError as exc:
        raise SystemExit(
            "error: cannot import %r (%s).\n"
            "       Add the directory that contains it with --sys-path DIR." % (modname, exc)
        )
    try:
        obj = getattr(mod, attr)
    except AttributeError:
        raise SystemExit("error: %s has no attribute %r" % (modname, attr))
    if not callable(obj):
        raise SystemExit("error: %s:%s is not callable (expected a class or a factory)" % (modname, attr))
    return obj


def construct(factory):
    try:
        inst = factory()
    except TypeError as exc:
        raise SystemExit(
            "error: could not construct the handler with no arguments (%s).\n"
            "       Point --handler at a zero-argument factory instead, e.g.\n"
            "         def make_handler():\n"
            "             return ProtocolHandler(store=Store(':memory:'))\n"
            "       and pass --handler mypkg.testing:make_handler" % exc
        )
    if not hasattr(inst, "handle"):
        raise SystemExit(
            "error: the handler has no .handle() method.\n"
            "       The contract this tool replays is:  handle(payload: bytes) -> list[bytes]"
        )
    return inst


def normalise_replies(value, where):
    if value is None:
        return []
    if isinstance(value, (bytes, bytearray)):
        raise SystemExit(
            "error: %s returned bytes, not a list of bytes.\n"
            "       handle() must return a LIST so that 'no reply' is [] and a "
            "multi-frame answer is expressible." % where
        )
    if not isinstance(value, (list, tuple)):
        raise SystemExit("error: %s returned %s; expected list[bytes]" % (where, type(value).__name__))
    out = []
    for item in value:
        if not isinstance(item, (bytes, bytearray)):
            raise SystemExit("error: %s returned a list containing %s; expected bytes"
                             % (where, type(item).__name__))
        out.append(bytes(item))
    return out


# --------------------------------------------------------------------------
# emitted test
# --------------------------------------------------------------------------

_TEST_HEADER = '''#!/usr/bin/env python3
"""Protocol conformance test -- GENERATED by replay_transcript.py.

Source transcript : {src}
Handler           : {handler}
Exchanges         : {count}

This is a skeleton, not a finished suite.  It covers exactly ONE of the five
assertion classes a protocol conformance suite needs:

  1. encoder byte-layout      -- each assertion restates one RE claim as bytes
  2. transcript replay        -- THIS FILE
  3. guest-safety invariants  -- the bounds the CLIENT fails to enforce, which
                                are therefore YOUR obligation (unclamped arrays,
                                unbounded string destinations, empty bodies a
                                text builder will null-deref on)
  4. client-model simulation  -- unit-test a model of the guest's own cursor /
                                pagination machine, not just your encoder
  5. abuse-case regression    -- one named test per hole you closed, with a
                                comment saying what was possible before

Write the other four by hand.  Add a comment beside each assertion naming the
workflow id or the DEVICE DATE that established the claim; when a claim is
refuted you then edit test, code and comment together, and the refutation stays
visible instead of being quietly deleted.

Run:  python {basename}
"""

import sys

{syspath}
from {module} import {attr}

fails = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


def hx(s):
    return bytes.fromhex(s)
'''

_TEST_FOOTER = '''

def main():
{body}
    print("ALL PASS" if not fails else "%d FAIL: %s" % (len(fails), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def emit_test(path, src, handler_spec, sessions_used, exchanges_by_session, extra_paths):
    module, attr = handler_spec.rsplit(":", 1)
    syspath = "\n".join(
        'sys.path.insert(0, %r)' % p.replace("\\", "/") for p in extra_paths
    )
    total = sum(len(v) for v in exchanges_by_session.values())
    body = []
    for sid in sessions_used:
        body.append("    # ---- session %s ----" % sid)
        body.append("    h = %s()" % attr)
        for rx, txs in exchanges_by_session[sid]:
            req = rx["_bytes"].hex()
            reps = ", ".join('hx("%s")' % t["_bytes"].hex() for t in txs)
            desc = "%s -> %s" % (
                " ".join("%02x" % b for b in rx["_bytes"][:2]) or "(empty)",
                (" ".join("%02x" % b for b in txs[0]["_bytes"][:2]) if txs else "(silence)"),
            )
            body.append('    check(h.handle(hx("%s")) == [%s],' % (req, reps))
            body.append('          "t=%.3f  %s")' % (rx.get("t", 0.0), desc))
        body.append("")
    text = (
        _TEST_HEADER.format(
            src=src.replace("\\", "/"),
            handler=handler_spec,
            count=total,
            basename=os.path.basename(path),
            syspath=syspath,
            module=module,
            attr=attr,
        )
        + _TEST_FOOTER.format(body="\n".join(body) if body else "    pass")
    )
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


# --------------------------------------------------------------------------


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="replay_transcript.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Replay a captured JSONL protocol transcript through a "
                    "handle(bytes) -> list[bytes] handler and emit a conformance test.",
        epilog="The JSONL schema this tool consumes is documented in the module "
               "docstring and is part of the methodology's contract: implement it "
               "in your server's transcript writer verbatim.",
    )
    ap.add_argument("transcript", help="path to the .jsonl transcript")
    ap.add_argument("--handler", metavar="MODULE:ATTR",
                    help="handler class or zero-argument factory, e.g. mypkg.proto:ProtocolHandler. "
                         "Required to replay; optional with --stats alone, which only reads the "
                         "transcript.")
    ap.add_argument("--sys-path", action="append", default=[], metavar="DIR",
                    help="directory to prepend to sys.path before importing. Repeatable.")
    ap.add_argument("--session", action="append", default=[],
                    help="replay only this session id (sid). Repeatable. Default: all.")
    ap.add_argument("--limit", type=int, default=0, help="stop after N exchanges (0 = all)")
    ap.add_argument("--replay-masked", action="store_true",
                    help="replay records marked \"masked\": true (they will usually fail)")
    ap.add_argument("--emit-test", metavar="OUT.PY",
                    help="write a standalone conformance-test skeleton for this transcript")
    ap.add_argument("--no-strict", action="store_true",
                    help="report mismatches but exit 0")
    ap.add_argument("--stats", action="store_true",
                    help="print inter-frame gap statistics -- this is how you derive a "
                         "defensible idle timeout instead of guessing one")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args(argv)

    if args.handler is None and not args.stats:
        ap.error("--handler is required unless you are only asking for --stats.")
    if args.handler is None and args.emit_test:
        ap.error("--emit-test names the handler in the generated file, so --handler is required.")

    if not os.path.exists(args.transcript):
        sys.exit("error: no such transcript: %s" % args.transcript)

    records, problems = load_transcript(args.transcript)
    if problems and not args.json:
        for p in problems[:20]:
            print("schema: %s" % p, file=sys.stderr)
        if len(problems) > 20:
            print("schema: ... and %d more" % (len(problems) - 20), file=sys.stderr)
    if not records:
        sys.exit("error: transcript contained no usable records. See the schema in --help.")

    sessions = split_sessions(records)
    wanted = set(args.session)
    sids = [s for s in sessions if not wanted or str(s) in wanted]
    if not sids:
        sys.exit("error: no sessions matched --session %s (present: %s)"
                 % (", ".join(args.session), ", ".join(str(s) for s in sorted(sessions, key=str))))
    sids.sort(key=str)

    factory = None if args.handler is None else load_handler_factory(args.handler, args.sys_path)

    results = []
    exchanges_by_session = {}
    total = 0
    skipped_masked = 0
    unpaired_total = 0

    for sid in (sids if factory is not None else []):
        recs = sessions[sid]
        exchanges, unpaired = pair_exchanges(recs)
        unpaired_total += len(unpaired)
        exchanges_by_session[sid] = exchanges
        inst = construct(factory)
        for rx, txs in exchanges:
            if args.limit and total >= args.limit:
                break
            if rx.get("masked") and not args.replay_masked:
                skipped_masked += 1
                continue
            total += 1
            expected = [t["_bytes"] for t in txs]
            try:
                got = normalise_replies(inst.handle(rx["_bytes"]), "handle()")
                err = None
            except SystemExit:
                raise
            except Exception as exc:  # a handler that throws is a failure, not a crash
                got = None
                err = "%s: %s" % (type(exc).__name__, exc)
            ok = err is None and got == expected
            results.append({
                "sid": sid,
                "line": rx["_lineno"],
                "t": rx.get("t"),
                "request": rx["_bytes"].hex(),
                "expected": [e.hex() for e in expected],
                "got": None if got is None else [g.hex() for g in got],
                "ok": ok,
                "error": err,
            })

    failed = [r for r in results if not r["ok"]]

    # ---- gap statistics ---------------------------------------------------
    stats = {}
    if args.stats:
        gaps = [float(r["gap"]) for r in records
                if r.get("ev") == "rx" and isinstance(r.get("gap"), (int, float))]
        if gaps:
            gaps_sorted = sorted(gaps)
            n = len(gaps_sorted)
            stats = {
                "n": n,
                "mean": sum(gaps_sorted) / n,
                "p50": gaps_sorted[n // 2],
                "p95": gaps_sorted[min(n - 1, int(n * 0.95))],
                "max": gaps_sorted[-1],
            }

    if args.json:
        print(json.dumps({
            "transcript": args.transcript.replace("\\", "/"),
            "handler": args.handler,
            "sessions": [str(s) for s in sids],
            "exchanges": total,
            "passed": total - len(failed),
            "failed": len(failed),
            "skipped_masked": skipped_masked,
            "unpaired_tx": unpaired_total,
            "schema_problems": problems,
            "gap_stats": stats,
            "results": results,
        }, indent=2))
    else:
        for r in results:
            if r["ok"]:
                print("PASS  sid=%s line=%d  %s -> %d reply(ies)"
                      % (r["sid"], r["line"], r["request"][:8] or "(empty)", len(r["expected"])))
            else:
                print("FAIL  sid=%s line=%d" % (r["sid"], r["line"]))
                print("        request  %s" % r["request"])
                print("        expected %s" % (r["expected"] or "[]"))
                if r["error"]:
                    print("        handler raised %s" % r["error"])
                else:
                    print("        got      %s" % (r["got"] or "[]"))
        print()
        if factory is None:
            print("statistics only -- no --handler given, so nothing was replayed.")
        else:
            print("%d exchange(s): %d pass, %d fail%s%s"
                  % (total, total - len(failed), len(failed),
                     ", %d masked skipped" % skipped_masked if skipped_masked else "",
                     ", %d unpaired tx" % unpaired_total if unpaired_total else ""))
        if stats:
            print("inter-request gap over n=%d: mean %.3fs  p50 %.3fs  p95 %.3fs  max %.3fs"
                  % (stats["n"], stats["mean"], stats["p50"], stats["p95"], stats["max"]))
            print("  -> an idle timeout of ~%.0fs is ~%.1fx the measured worst case."
                  % (max(10.0, stats["max"] * 6.6), 6.6))
            print("  -> derive limits from measured client behaviour, never from a round number.")
        if unpaired_total:
            print("note: %d tx record(s) had no preceding rx in their session. Server-initiated"
                  % unpaired_total)
            print("      pushes cannot be attributed to a request and were not replayed.")

    if args.emit_test:
        emit_test(args.emit_test.replace("\\", "/"), args.transcript, args.handler,
                  sids, exchanges_by_session, args.sys_path)
        print("wrote %s" % args.emit_test.replace("\\", "/"), file=sys.stderr)

    if failed and not args.no_strict:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
