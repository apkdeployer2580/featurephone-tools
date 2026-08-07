# Deploying the replacement server

A hobby game server that other people play on has two properties an internal service does not: the
clients are **shipped binaries you cannot update**, and the operator is usually the only person
who will ever read the runbook. Both of those make the deployment documentation load-bearing.

## Contents

- [★ Rule zero: the deployment docs are part of the security surface](#-rule-zero-the-deployment-docs-are-part-of-the-security-surface)
- [Before you touch a firewall: prove the client is aiming at a public address](#before-you-touch-a-firewall)
- [The two-layer firewall](#the-two-layer-firewall)
- [Certificates: three paths and their real costs](#certificates-three-paths-and-their-real-costs)
- [Generating a self-signed certificate and its pin](#generating-a-self-signed-certificate-and-its-pin)
- [Why pinning is stricter than CA validation](#why-pinning-is-stricter-than-ca-validation)
- [Turning TLS on closes the cleartext path — on purpose](#turning-tls-on-closes-the-cleartext-path)
- [Process hardening](#process-hardening)
- [The installer](#the-installer)
- [Production limits](#production-limits)
- [Operations](#operations)
- [Distribution rules that are really deployment rules](#distribution-rules-that-are-really-deployment-rules)
- [Failure table](#failure-table)

---

## ★ Rule zero: the deployment docs are part of the security surface

**A destructive flag whose default disagrees between the code, the service unit and the
documentation is a real defect, not a documentation nit.** It is exactly as dangerous as a bug,
because the operator's mental model — not the code — is what decides whether the destructive path
gets exercised on real players' data.

**(B) The worked failure.** In the reference port, a **restore** feature (which overwrites a
player's live save with a stored blob) had:

| source | what it said |
|---|---|
| the code | `enable_restore: bool = True` |
| the systemd unit's comment | "It is OFF by default. Do not set it until the trigger is device-confirmed." |
| the deploy document | "restore is OFF by default; enabling it risks data loss" |

An operator reading the deploy docs believed the destructive path was disabled while it was
enabled. (The code's *effective* guard had also moved on to a better-grounded condition — "did
this connection upload a backup?" — and the docs never learned that either.)

**The rules that follow:**

1. **Every destructive flag has exactly one source of truth**, and the docs quote it rather than
   restating it. If you can, make the program **print its effective configuration at startup** and
   make the runbook say "read the log line", not "the default is X".
2. **Default destructive behaviour to OFF**, and require an explicit flag with a name that says
   what it destroys.
3. **Prefer a state-derived guard to a flag.** "Restore is allowed only if this connection
   uploaded a backup" cannot drift out of sync with a document, because there is no default to
   misremember.
4. **Review the deploy docs in the same commit as the flag.** Add it to your publish checklist:
   *deploy docs and code defaults agree on every destructive flag*.
5. Same rule applies to the **transcript** flag, the **reply-table** flag and any **debug** flag —
   each is destructive to privacy rather than to data.

## Before you touch a firewall

**★ Prove the client is even *aiming* at a public address before you debug the network.** A
build-time constant destination fails **identically** to a blocked port: connection times out,
nothing in the server log, nothing in the firewall log.

**(B) The reference port's own misdiagnosis** is the lesson. The symptom was "the server is
blocking my IP". The reality: the phone had only ever connected to **its own loopback, tunnelled
over USB by `adb reverse`**. Pull the cable and there is nothing to connect to. The server was
already bound on all interfaces and no firewall was involved. The actual defects were that the
client's destination was a compile-time constant, and that the release build had no signing
configuration so it could not even be installed.

Order of checks:

```bash
# 1. Is the client aiming anywhere real? Inspect the ARTIFACT, not the build script.
python "${CLAUDE_PLUGIN_ROOT}/tools/verify_artifact_config.py" path/to/libmain.so \
  --require-string "YOUR_SERVER_HOST" --forbid-string "127.0.0.1"

# 2. Is the port reachable from OUTSIDE the box?
openssl s_client -connect YOUR_SERVER_HOST:<port> -brief

# 3. Is it reachable from INSIDE the box? (isolates provider firewall from host firewall)
ss -ltnp | grep <port>
openssl s_client -connect 127.0.0.1:<port> -brief
```

Only when 1 passes and 2 fails while 3 succeeds is this a firewall problem.

## The two-layer firewall

A remote connection has **two independent gates, and each can be closed by a different party**:

| layer | who owns it | how it fails |
|---|---|---|
| **1. Provider security group / VPC firewall** | your cloud console | silent timeout |
| **2. Host firewall inside the VM** (`ufw`, `firewalld`, `nftables`) | you, over SSH | silent timeout |

Opening one and not the other yields a timeout **indistinguishable from "wrong address"**. That
ambiguity is the entire trap: you fix layer 2, nothing changes, you conclude the address is wrong.

```bash
# tester phase: allowlist a single source address first
sudo ufw allow from <TESTER_IP> to any port <port> proto tcp
# fully public, only when you mean it
sudo ufw allow <port>/tcp
```

**Open only the port the client actually uses.** If the guest has two hardcoded destinations and
your host redirect points both at one endpoint
([server-architecture.md](server-architecture.md)), the second port is attack surface serving
nobody.

## Certificates: three paths and their real costs

**An IP address cannot get an ordinary CA certificate.** Public CAs issue for domain names; the
short-lived IP-certificate programs that exist are a separate, far more fragile product. That
single fact drives the whole table.

| path | needs a domain | certificate | client config | you must redistribute the app when |
|---|---|---|---|---|
| **A. IP + self-signed + build-time pin** *(recommended for a hobby server)* | no | self-signed, 10 years | none — baked in | **the server IP changes, or the certificate is ever re-issued** |
| **B. domain + ACME (Let's Encrypt)** | yes | 90 days, auto-renew | none | never, while the name lives |
| **C. IP + short-lived IP certificate** | no | **~6–7 days**; renewal must never stop | none, no pin | the server IP changes |

Choosing:

* **No domain, few players, want it to just work for a decade → A.** One certificate, no renewal
  treadmill, no CA in the trust path.
* **Willing to own a domain name → B.** It is the only path where a server move costs you nothing
  on the client side, and it is what you want if you ever expect to hand the server to someone
  else.
* **C only if you understand that a stalled renewal is an outage within a week.**

**★ The cost of A is rotation, and it is severe.** There is typically no in-app pin entry, so
replacing the certificate means **redistributing the app to everyone**. A re-issued certificate
**bricks every shipped build**. Consequences:

* generate the certificate with a long validity **once**, and back up the key as carefully as the
  app signing key;
* if you ever need rotation, ship **pin-set** support (accept old and new) *before* you rotate,
  not during;
* store the pin as **`(host, port) → pin`**, not as a single global value. A global pin means a
  trust decision made for server A is silently applied to server B, and changing the address in
  settings produces an unexplained connect failure.

## Generating a self-signed certificate and its pin

```bash
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
  -keyout /var/lib/<service>/tls.key -out /var/lib/<service>/tls.crt \
  -subj "/CN=<service>" -addext "subjectAltName=IP:YOUR_SERVER_HOST"

chmod 600 /var/lib/<service>/tls.key
chown <service-user>:<service-user> /var/lib/<service>/tls.key

# the value you bake into the client:
openssl x509 -in /var/lib/<service>/tls.crt -outform DER | sha256sum
# -> <64-hex SHA-256 of your server cert DER>
```

Then bake it: `${ENV_PREFIX}_SERVER_PIN=<64-hex …>` at build time, and **verify it landed in the
artifact** with `verify_artifact_config.py` — `option_env!` resolves at compile time and a missing
variable produces a silently wrong build.

⚠ **Both documented paths must set the same permissions.** The reference port's installer did
`chmod 600` on the key while its manual instructions did only `chown`. Whichever path an operator
follows must end in the same state; a private key readable by the world is not a documentation
inconsistency, it is the whole compromise.

## Why pinning is stricter than CA validation

Say this explicitly in your README, because "self-signed" reads as "insecure" to most people:

> Pinning a self-signed certificate is **stricter** than CA validation, not weaker. With CA
> validation you trust every public CA — any one of which can issue for your name. With a pin you
> trust **exactly one key**.

The verifier compares `SHA-256(end-entity DER)` against the pin and **deliberately skips expiry
and name checks**: the pin *is* the identity. That is also why a ten-year certificate needs no
renewal — nothing in the client is looking at `notAfter`.

**Bake the pin at build time**, not only in a device file. The device path is app-private storage;
an ordinary sideloading user cannot write to it, so a file-only pin is unconfigurable for exactly
the audience you are shipping to.

Surface the transport state in the UI so a user can see it: `LOCAL` / `SECURE` / **`PLAIN!`**. A
loud indicator is the cheapest defence against a misconfiguration nobody notices.

## Turning TLS on closes the cleartext path

**On purpose.** Once the server requires TLS, older builds simply fail to connect. That is
correct: **failing is better than credentials silently leaving in the clear.** Assert it in the
e2e suite (property 5 in [conformance-tests.md](conformance-tests.md)) so a future refactor cannot
quietly re-open a plaintext listener.

Plan the cutover: announce it through the login notice (your only broadcast channel), give a
window, then flip. Do not run both listeners "for compatibility" — that is precisely the
configuration where the credential leak continues.

## Process hardening

A systemd unit, with the two settings that actually matter called out:

```ini
[Service]
Type=simple
User=<service-user>            # dedicated, nologin shell
ExecStart=/usr/bin/python3 /opt/<service>/server.py --db /var/lib/<service>/app.db \
          --tls-cert /var/lib/<service>/tls.crt --tls-key /var/lib/<service>/tls.key
# NOTE: no --transcript flag. It records every user's credential and saves in one file.
# NOTE: no --replies flag. A catch-all entry hijacks every connected user's replies.

Restart=always
RestartSec=2
StartLimitIntervalSec=0        # ★ see below

StateDirectory=<service>
ReadWritePaths=/var/lib/<service>
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
NoNewPrivileges=yes
RestrictAddressFamilies=AF_INET AF_INET6
RestrictNamespaces=yes
LockPersonality=yes
MemoryDenyWriteExecute=yes
```

**★ `StartLimitIntervalSec=0` is the one people miss.** systemd's default rate limit (5 starts in
10 s) means a fast crash loop makes the unit **give up permanently** — the exact opposite of what
you want for a server other people are playing on. Disable the limit and let it keep restarting;
you will see the loop in the journal.

**The two absent flags are documentation.** Write the comment explaining *why* a flag is not
there, or the next operator adds it back.

## The installer

Keep it idempotent, and get these three right:

**★ Copy files explicitly — never `cp -r` the repository.**

```bash
cp "$TMP/repo"/server/*.py "$APP_DIR"/     # explicit. NOT cp -r.
```

A development checkout contains a database, transcripts, `__pycache__`, and quite possibly saved
character data. `cp -r` publishes **someone else's account identifiers and saves** onto a public
server, and you will not notice.

**Generate the certificate and print the pin plus the exact client build command.** The operator
should never have to compute a fingerprint by hand — that is where a wrong pin comes from, and a
wrong pin is indistinguishable from a network failure.

**⚠ `curl … | sudo bash` is not an acceptable published install path.** It fetches a moving branch
with no checksum and no signature. For an open-source release: publish a pinned tag, publish its
`sha256sum`, and have the operator verify before running. If you keep a one-liner for your own
convenience, keep it out of the README.

## Production limits

Repeated from [server-architecture.md](server-architecture.md) because this is the page an
operator reads. **(B)** values; the derivations are what transfer.

| knob | value | derivation |
|---|---|---|
| idle timeout | 45 s | 6.6× measured worst-case heartbeat gap. The server never writes unprompted, so a dropped NAT mapping produces no RST and the fd leaks forever without this. |
| sessions | 64 total / 4 per IP | one socket per install; 4 for carrier NAT |
| frame rate | 20/s over 5 s | legitimate bursts pass, floods do not |
| rows per account | 200 | above any legitimate session |
| frame size | 1016 B | the guest's reassembly buffer |
| transcript | **OFF** | credential + saves + peer address in one file |
| reply table | **OFF** | a catch-all hijacks every user |
| destructive restore | **OFF**, or state-guarded | see rule zero |

## Operations

```bash
journalctl -u <service> -f

sqlite3 /var/lib/<service>/app.db "select count(*) from accounts; select count(*) from items;"

# players' characters — keep the copy OFF this box
sqlite3 /var/lib/<service>/app.db ".backup /var/backups/<service>-$(date +%F).db"
```

Backup handling is a privacy decision, not just an availability one: the database is full of
player saves keyed by a credential hash. Store backups encrypted, off the server, with a
retention you have written down.

## Distribution rules that are really deployment rules

* **Never lose the app signing key.** A different key means Android refuses the overwrite install,
  and the only remedy is uninstall + reinstall — which destroys every user's local account code
  and save. Back it up as carefully as the TLS key, and keep it **outside** the repository.
  Details: `port-shipping/reference/build-script.md`.
* **`versionCode` only ever increases.** You cannot roll back a shipped build.
* **★ The login notice is your only broadcast channel to every installed copy.** There is no push
  channel to a sideloaded app. Use the notice for maintenance windows, TLS cutovers, and "please
  update" messages — and remember that it is server-controlled text rendered by the client, so it
  is subject to the text-capacity bound in [reply-re.md](reply-re.md).

## Failure table

| symptom | cause |
|---|---|
| connect times out from the internet, works on LAN | provider firewall, host firewall, **or** the client is aiming at a compile-time constant |
| server "running" but nobody can play | bind failure was non-fatal and it is listening on the wrong port only |
| new configuration appears to have no effect | a stale listener still holds the port; verify the PID |
| TLS handshake fails on every device | pin mismatch — the certificate was re-issued, or the pin was transcribed by hand |
| some devices connect, others do not | pin baked into some builds and not others; verify inside the `.so` |
| the service stops restarting after a crash | systemd start-rate limit; set `StartLimitIntervalSec=0` |
| a player's save was overwritten | a destructive flag whose default the docs got wrong — see rule zero |
| account data appeared on the public server | `cp -r` of a development checkout |
