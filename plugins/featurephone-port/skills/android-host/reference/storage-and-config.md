# Storage, configuration and identity

Where the guest's files and records live, how the host's own settings are layered, and how an
account identity is created, protected and — critically — recoverable when the player changes
phones. Almost all of this lifts verbatim between ports; the parts that do not are named.

Tags: **(A)** generic · **(B)** worked example from the reference port, re-derive it ·
**(C)** do not copy.

**Owned elsewhere:** everything about *not destroying* what is stored here — atomic replacement in
depth, the archive-before-overwrite vault, which slot a write belongs to, and the policy function
that answers it — is the `save-integrity` skill
([SKILL.md](../../save-integrity/SKILL.md)). This file says where the bytes live and which layer
must carry the hooks; it does not restate the mechanisms.

## Contents

- [1. The two storage backends](#1-the-two-storage-backends)
- [2. Synchronous on purpose — and atomic, which is a different property](#2-synchronous-on-purpose--and-atomic-which-is-a-different-property)
- [3. Sanitise every id; reject traversal](#3-sanitise-every-id-reject-traversal)
- [4. Keep the layout probe-compatible](#4-keep-the-layout-probe-compatible)
- [5. Three-layer configuration](#5-three-layer-configuration)
- [6. The endpoint parser](#6-the-endpoint-parser)
- [7. Trust: web PKI or a pin](#7-trust-web-pki-or-a-pin)
- [8. Account identity and the transfer code](#8-account-identity-and-the-transfer-code)
- [9. What lives where](#9-what-lives-where)
- [10. Symptom table](#10-symptom-table)
- [11. Checklist](#11-checklist)

---

## 1. The two storage backends

A WIPI-family guest expects two persistence services, and both are near-verbatim ports of the
desktop implementations with one substitution: the platform data directory becomes
`AndroidApp::internal_data_path()`.

| service | guest sees | host implements |
|---|---|---|
| **filesystem** | open / read / write / seek / delete on paths the guest chooses | files under `<internal>/<app_id>/fs/...` |
| **record store** ("database") | named record sets addressed by integer record id | one file per record: `<internal>/<app_id>/db/<name>/<record_id>` |

Nothing else about them is Android-specific. Do not reimplement; port.

Two behaviours the guest depends on that are easy to get wrong:

- **A record-set "size" query is a distinct mode from a read**, and some guests gate
  "does this save slot exist?" on its return value. Returning 0 makes every slot read as empty
  and valid saves get rejected. That is a core-side fix (`emulator-core`), but the host's
  record layout is what it queries.
- **Name-keyed delete must actually delete.** A guest's "reset save" path may delete by name
  rather than by id; if that is a no-op the slot reloads the old save and the player cannot
  start over.

### ★ "Do not reimplement; port" is right about the mechanics and wrong about the policy

**(A)** The record store is the **only** layer that every save write, every save delete and every
save *read* passes through. That makes it the mandatory — and the only correct — home for three
things a neutral persistence layer would not have, and the reference port's save losses all lived
in the ported implementation rather than in the code around it:

| hook | on | why it can only go here |
|---|---|---|
| **archive-before-overwrite** | `set()` and `delete()` | it is the last moment the outgoing bytes exist |
| **a write/delete policy gate** | `set()` and `delete()` | the slot index inside the record *name* is a guest UI cursor, so a write can be aimed at another character's record |
| **a load observation** | `get()` | reading the payload record **is** a character load, and it is the one signal that covers every load path the guest has |

Each of those is `save-integrity`'s to specify, and this file does not restate them:
[save-vault.md](../../save-integrity/reference/save-vault.md) §2 for the hook point,
[slot-identity.md](../../save-integrity/reference/slot-identity.md) for the cursor-versus-identity
problem, [policy-and-diagnostics.md](../../save-integrity/reference/policy-and-diagnostics.md) for
keeping the decision one pure function. What this file owns is that the hooks belong **in the
ported store**, not in a wrapper somebody can forget to route through.

## 2. Synchronous on purpose — and atomic, which is a different property

**(A) Use plain synchronous `std::fs`. Do not buffer, do not batch, do not defer.**

The property you need is: **when the guest's write call returns, the bytes are on disk.**
Android terminates backgrounded processes without notice and without running any flush you
planned to do later. A save that is "queued" is a save the player loses, and the player's
mental model — "the game said it saved" — is not negotiable.

The cost is a handful of small synchronous writes at moments the player already expects to be
brief. That is the correct trade for a feature-phone-sized guest, where a save record is
kilobytes.

> **REFUTED.** *Previously documented (this section, 0.1.0):* synchronous `std::fs` is the whole
> requirement, and the only save-loss symptom worth a row in §10 is "buffered or deferred writes".
> **Refuted by** two players losing characters in one week on a build with no buffering anywhere:
> both slots read as empty and stayed empty across relaunches. **Actual mechanism:** `fs::write` is
> `open(O_TRUNC)` + `write`, so the record file is **zero bytes for the width of one syscall** — and
> a zero-byte record is exactly what a guest reads back as *"no character"*
> ([loss-mechanisms.md](../../save-integrity/reference/loss-mechanisms.md) §1). The wrong reading is
> attractive because *synchronous* and *durable* feel like one property; they are not, and the
> window is far too small to appear in your own testing. **Consequence if carried forward:** a
> process death, a full disk or an I/O error inside that window destroys the very record the guest
> was updating, and the player is told nothing.

**★ (A) Synchronous AND atomic.** Every record write is: temp file **in the same directory** →
write → `sync_all` → `rename` over the target → a best-effort directory fsync **whose failure must
not be reported as a failed save**. Same-directory is what makes the last step a rename rather than
a copy, and the rename is the only step that has to be atomic.

> **State the invariant where the code is: refusing to save is recoverable, half-saving is not.**
> Its second half is that a failed write leaves the previous record byte-intact — so a write
> failure must never fall through into a delete.

Corollary for host-owned state: the layout file, the config file and any host feature state are
written the same way, at the moment of the edit
([layout-editor.md](layout-editor.md#9-persistence)).

## 3. Sanitise every id; reject traversal

Both backends take strings **chosen by the guest**, and a guest is not a trusted input source —
neither maliciously nor accidentally. A record-set name containing `../` escapes app-private
storage.

```rust
fn sanitise(component: &str) -> Option<String> {
    if component.is_empty() || component.len() > MAX { return None; }
    if component == "." || component == ".." { return None; }
    if component.contains(['/', '\\', '\0']) { return None; }
    // map every remaining character outside [A-Za-z0-9._-] to a safe escape
    Some(escape(component))
}
```

Apply it to the application id, to record-set names and to file path components — **each
component separately**, then join. Sanitising the joined path is where the bugs are.

Reject rather than silently mangle where you can: a name that fails sanitisation should surface
as the platform's "cannot open" error to the guest, which every guest already handles.

## 4. Keep the layout probe-compatible

**★ (A) The on-device record layout and the headless probe's `--db` layout must be
byte-identical.**

```
<data-root>/<app_id>/db/<record-set-name>/<record-id>
```

That single agreement means:

- a save pulled off a device drops straight into a probe run, so you can debug a player's
  actual game state with no phone attached;
- the probe can be started **deep in the game** instead of replaying from the title screen
  every run, which is the difference between a one-minute and a ten-minute iteration;
- a bug reproduced in the probe is reproducible in CI.

This is the cheapest interoperability decision in the whole port and it is trivially lost by
"improving" the on-device layout later. Pin it with a comment in both implementations naming
the other.

## 5. Three-layer configuration

**(A) Every externally-variable setting resolves through three layers, first hit wins:**

| layer | mechanism | who it serves |
|---|---|---|
| 1 | a file, written by the in-app dialog — searched in **two** locations, see below | **the player**, and **you on a shipping build** — changes with no rebuild, no reinstall, no restart |
| 2 | build-time environment, read with `option_env!("${ENV_PREFIX}_...")` | **the distributor** — a tester build already points at the right place |
| 3 | a hardcoded development default | **you** — e.g. `127.0.0.1:<port>` with `adb reverse` |

```rust
pub fn server_endpoint() -> Endpoint {
    read_config_file("server.txt").and_then(|s| endpoint::parse(&s))
        .or_else(|| option_env!(concat!(env!("ENV_PREFIX"), "_SERVER")).and_then(endpoint::parse))
        .unwrap_or(Endpoint::new("127.0.0.1", DEV_PORT))
}
```

**★ (A) Layer 1 has TWO locations, and the app-specific directory on shared external storage is
searched FIRST.**

```
1a  <external-app-specific>/<file>     <- writable by the device shell on a RELEASE build
1b  <internal>/<file>                  <- written by the in-app dialog
2   build-time environment
3   development default
```

The reason is a property of shipping builds, not a convenience. **A release package is not
debuggable, so the debug shell cannot read or write app-private internal storage at all** — while
the shell *is* exempt from scoped storage for the app-specific external directory. That directory
is therefore the only writable configuration location a release build has, and it is what makes a
**same-phone, same-save A/B** possible: the alternative, installing the previous artifact, is a
downgrade, which requires an uninstall, which deletes the player's save. **Log which path won**,
next to the endpoint report — a switch that silently did not apply is indistinguishable from a
feature that does not work.

This does not weaken §9's rule. What moves outward is **switches and diagnostic exports**; saves,
the account identity and anything credential-shaped stay internal-only.

Four more rules, each of which was a bug first:

**★ Do not gate the layering on `debug_assertions`.** An earlier version returned the dev
default in debug builds unconditionally. That pinned every debug build to loopback and made
"an APK someone else can install that reaches a real server" impossible to produce without a
release build. The layers are about *who configured what*, not about optimisation level.

**★ Re-read at the point of use, do not cache at construction.** Resolve the endpoint inside
`connect`, not once at startup. This is what makes layer 1 take effect without a restart, which
is the entire reason layer 1 exists.

**★ `option_env!` resolves at compile time and a missing variable produces a silent, wrong
build.** The build script must fail if the variable is required and absent, and — because build
scripts lie — the produced binary should be scanned for the expected string before it ships.
That gate belongs to `port-shipping`.

**Validate layer 1 on write, not on read.** The dialog rejects a malformed value while the
player is looking at it. A malformed value that reaches storage should fall through to layer 2
rather than failing the connect, so a bad edit cannot brick the app.

## 6. The endpoint parser

Small, dependency-free, and worth getting exactly right because it is the thing between a
player's typed string and a connect. Accepts `[scheme://]host[:port]`.

**★ Distinguish three shapes, in this order.** Order matters:

1. **`[addr]:port`** — bracketed. Unambiguous; parse the brackets first.
2. **Two or more colons, no brackets** -> an **IPv6 literal**, *not* `host:port`. Splitting
   `::1` on its last colon yields host `:` and port `1`. Every naive parser has this bug.
3. **Exactly one colon** -> ordinary `host:port`.

Reject: port 0, ports over 65535, empty host, hosts over 253 characters, and any character
outside the hostname set (plus `:` and `.` for literals). On rejection, **fall through to the
next configuration layer** rather than failing the operation — a typo in a config file should
degrade to the build-time default, not to a dead app.

Put it in a dependency-free module with a standalone selftest
(`${CLAUDE_PLUGIN_ROOT}/shared/selftest-convention.md`); the IPv6 case gets its own named test
with the date and symptom in a comment.

## 7. Trust: web PKI or a pin

If the port talks to a replacement server (see `server-revival` for the wire itself), the
transport choice is configuration and belongs here.

`tls://` in the address selects encryption. Trust resolves two ways:

| mode | when | operational cost |
|---|---|---|
| **Web PKI** with a bundled root set | you have a domain name and a CA-issued certificate | ACME renewals; no app changes |
| **Pinned certificate** — the SHA-256 of the server certificate's DER | you have a bare host and a self-signed certificate | none, until the certificate is replaced, which then requires a new build |

**(A)** The pin resolves through the same three layers as the endpoint, and **the build-time
layer is what makes a domain optional**: a self-hoster points the app at their own host, bakes
`${ENV_PREFIX}_SERVER_PIN` into the build, and hands out an APK that players configure not at
all. Write the pin in documentation as
`<64-hex SHA-256 of your server cert DER>` — never a real one.

Note that a pin is **stricter** than CA validation, not a weaker substitute: it authenticates
one specific key rather than anything a CA will sign.

Two build-level details that will otherwise cost an afternoon:

- **Choose the TLS crypto provider that cross-compiles.** The default provider in some stacks
  needs `cmake` and an assembler that the Android cross-build does not have; the pure-Rust
  alternative builds cleanly. Pick it explicitly in `Cargo.toml` rather than discovering it in
  a CI log.
- **The handshake blocks.** Run it on the connect helper thread while the socket is still
  blocking, and only set the socket non-blocking afterwards. Doing it lazily on the first read
  parks the interpreter.

## 8. Account identity and the transfer code

If the port has a server, it needs an identity for each installation.

**Generate it locally, from the platform's randomness** — read from `/dev/urandom` — and store
it in app-private storage. Do not derive it from a device identifier: those are either
unavailable without a permission, or shared between apps, or reset by the user, and all three
outcomes are worse than a random value you control.

**★ Say plainly what it is: a BEARER credential.** If the server keys a player's data on
whatever identifier arrives, then *knowing the identifier is being the account*. Per-device
random identifiers separate users; they do **not** authenticate them. This is a real threat
model with a real upgrade path — you own both endpoints, so a challenge-response fits inside
the existing frames — and it is `server-revival`'s to specify. What this layer owes is honesty
in the UI and TLS on the wire so the credential is not readable in transit.

**★ The transfer code is not optional.** The identifier lives only in app-private storage, so
an uninstall, a factory reset or a new phone makes the player's server-side data unreachable
**forever**. Therefore:

- the player can **read the code out** of the app (a dialog they can copy from);
- the player can **type it back in** on another installation;
- the dialog says, in plain language in the player's own language, that it is a password and
  that anyone holding it holds the account;
- entering a code **replaces** the local identity, and the app says so before doing it.

The reading and typing both go through platform dialogs
([glyphs-and-text.md](glyphs-and-text.md#6-text-input-goes-to-a-platform-dialog)), because the
overlay font cannot do text entry and this is a string the player must transcribe accurately.

## 9. What lives where

| path | contents | notes |
|---|---|---|
| `<internal>/<app_id>/fs/...` | the guest's own files | sanitised per component |
| `<internal>/<app_id>/db/<name>/<id>` | the guest's records | **layout matches the probe's** (§4) |
| `<internal>/layout.txt` | host control placements | one line per control, per-line defensive parse |
| `<internal>/settings.txt` | host settings | key=value, unknown keys ignored |
| `<internal>/server.txt` | endpoint override (layer 1) | written by the dialog |
| `<internal>/identity` | the account identifier | never logged, never in a crash report |
| `<internal>/<app_id>/vault/<name>/` | archived pre-images of records about to be overwritten or deleted | ciphertext as the guest wrote it; naming is the diagnostic ([save-vault.md](../../save-integrity/reference/save-vault.md) §6) |
| `<internal>/<restore-request>` | a pending restore, applied at next boot | a request, not the restore itself — the record may be open |
| `<external-app-specific>/<switch files>` | developer switches on a shipping build (§5 layer 1a) | never a credential, never a save |
| `<external-app-specific>/<vault export>/` | a **player-initiated** copy of the vault | see the exception below |
| build-time env `${ENV_PREFIX}_*` | endpoint, pin, anything a distributor bakes in | verified inside the built artifact, not trusted from the build script |

Everything that matters is under app-private internal storage. A save on shared external storage is
readable by any app with the legacy permission, and an identity there is a credential in a public
place.

**★ The rule with its one bounded exception (A): nothing goes to external storage
*automatically*.** A release build is not debuggable, so on an unrooted phone there is otherwise
**no channel at all** for getting a player's save data to you. So allow exactly one deliberate
export, and constrain it:

- **initiated by the player**, from a diagnostics screen, never on a timer and never at boot;
- **ciphertext records only** — no identity, no config, nothing the player would not knowingly hand
  over;
- **designed so the FILE NAMES carry the diagnostic value**, because on some devices the exported
  files are owner-only: a listing succeeds while a pull is refused, and a generation timeline made
  of `<timestamp>-<length>` names is enough to locate when a slot changed hands.

The retention, naming and restore semantics are [save-vault.md](../../save-integrity/reference/save-vault.md)
§6, §7 and §11; this file owns only where the two directories sit.

**Nothing in this table is ever logged.** Log verdicts and sizes, not values
([platform-glue.md](platform-glue.md#9-logging)).

## 10. Symptom table

| Symptom | Cause |
|---|---|
| Saves lost when the app is killed shortly after saving | buffered or deferred writes (§2) |
| A slot reads as empty after a crash, a full disk or a storage error | non-atomic replacement: `fs::write` truncates before it writes (§2) |
| One character's save overwrote another's, or saving silently stopped for a session | no policy gate in the record store; the slot in the record name is a UI cursor (§1, `save-integrity`) |
| A player on an unrooted phone cannot send you their save | no player-initiated export; internal storage is unreachable on a release build (§9) |
| A developer switch cannot be flipped on a shipping build | layer 1 searched only internal storage (§5) |
| A record-set name with a slash writes outside app storage | id not sanitised per component (§3) |
| A device save will not load in the headless probe | the two DB layouts have diverged (§4) |
| Changing the server address requires a reinstall | endpoint cached at construction instead of re-read at connect (§5) |
| Debug builds can only reach loopback | layering gated on `debug_assertions` (§5) |
| A build silently points at the wrong server | `option_env!` absent at compile time; no artifact verification (§5) |
| `::1` becomes host `:` port 1 | last-colon split before the IPv6 shape check (§6) |
| A typo in the config file makes the app unable to connect at all | parse failure not falling through to the next layer (§5, §6) |
| TLS build fails in the Android cross-compile with a missing assembler | default crypto provider needs a native toolchain (§7) |
| The interpreter freezes for seconds when connecting | TLS handshake performed lazily on the emulator's thread (§7) |
| A player loses everything after reinstalling | no transfer code, or nobody told them what it was for (§8) |
| A player's account is taken over from a 20-line script | bearer-token identity over plaintext (§7, §8) |

## 11. Checklist

- [ ] Filesystem and record store are ports of the desktop implementations, synchronous, over
      app-private internal storage.
- [ ] Every record write is temp + `sync_all` + rename in the same directory; a failed write
      leaves the previous record byte-intact and never becomes a delete.
- [ ] The ported record store carries the vault hook, the write/delete gate and the load
      observation (`save-integrity`), rather than a wrapper around it.
- [ ] Config layer 1 searches the app-specific external directory before internal storage, and
      logs which path won.
- [ ] The vault export is player-initiated, ciphertext-only, and its file names are the diagnostic.
- [ ] Every guest-supplied id is sanitised per path component and traversal is rejected.
- [ ] The record layout is byte-identical to the headless probe's, with a comment in both files
      naming the other.
- [ ] Config resolves file -> build-time env -> dev default, re-read at point of use, not gated
      on `debug_assertions`.
- [ ] The endpoint parser handles brackets, IPv6 literals and `host:port` in that order, and
      falls through on rejection.
- [ ] Trust mode is per-endpoint; the pin resolves through the same layers; the crypto provider
      is chosen for the cross-build; the handshake happens on the connect thread.
- [ ] The account identifier is locally generated, never logged, and described honestly in the
      UI as a password.
- [ ] A transfer code can be read out and typed back in, through platform dialogs.
- [ ] Nothing is written to shared or external storage.
