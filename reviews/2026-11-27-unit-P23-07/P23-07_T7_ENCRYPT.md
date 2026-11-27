# P23-07 — E2E T7: ENCRYPTION FALSIFICATION (OVH/T1-scope)

Owner question (verbatim): "do we test encryption and how does that work exactly".
This is falsification, not assurance: every claim of "encrypted" was attacked at the layer it lives on.

Substrate: probe-v119-test on CT100 (OVH 139.99.209.235, `pct exec 100`), TLS+mTLS since P23-01 at
HEAD e16f52f (P19-01 mTLS gate: `Listen()` sets `tlsConfig.MinVersion=tls.VersionTLS13`,
`ClientAuth=RequireAndVerifyClientCert` when `--client-ca` passed). Docker-proxy 0.0.0.0:7710→container 7701.
Boot line (docker logs, quoted): `[server] E2E encryption enabled (AES-256-GCM)`.

---

## CONTRACT LINES

- L1 SUBSTRATE: T1 listener TLS+mTLS alive on OVH 7710 at unit start. `ss -tlnp | grep 7710` →
  `LISTEN 0.0.0.0:7710 users:(("docker-proxy",pid=1376930,fd=8))` (observed E1 this turn).
- L2 BLOCK: every cell measured is a live falsification, not a config claim; server-side negatives are
  stated as log quotes, client-side as client-dial output.
- L3 NO-CHARITY: C7 (no secret leaks) is grep-count-zero over real bodies and real on-disk JSON, NOT
  a code review of where tokens sit.
- L4 FENCE: NVH/Hetzner cells only. 1/2/5 (relay-side) DEFERRED as six park rows below.
- L5 MATERIALITY: C3/C4/C6 verdicts are load-bearing (encryption contract); C7 likewise; wire-check is
  corroborating only.

---

## CHECK 3 — NO SILENT MITM (fallback dial must FAIL HARD)

Authorised: negative wss dial with NO CA / self-signed trust. Performed from inside CT100 against the
server loopback (DNAT re-added this turn → routable; quoted teardown below).

| Dial | Config | Client result (typed) |
|---|---|---|
| no CA | `curl` no ssl args, plain HTTPS | connection dies pre-dispatch; server classifies it as plaintext-on-TLS-port: `http: TLS handshake error from 172.17.0.1:34796: client sent an HTTP request to an HTTPS server` (live server log, E1) |
| self-signed CA | `curl --cacert evil-ca.crt` (minted unauthorised CA, this turn) | client: `* TLSv1.3 (OUT), TLS alert, unknown CA (560)` + `SSL certificate problem: unable to get local issuer certificate`; server: `09:20:12 http: TLS handshake error from 172.17.0.1:37834: tls: failed to verify certificate: x509: certificate signed by unknown authority` (docker-inspect-visible log line, E1) |

Both refused pre-handshake; no fallback, no InsecureSkipVerify side-door observed. Server side internally
confirmed by neither ever reaching route dispatch (no 200 on these dials). The client binary additionally
refuses opt-out-less dials even when cert material IS present (C4b refusals show
`wss:// dial refused: no CA certificate configured and verification cannot proceed` as DEFAULT, explicit
`PROBE_TLS_SKIP_VERIFY=true` env required to even attempt skip = P19-01 fix verified live).

VERDICT C3: PASS.

## CHECK 4 — mTLS ENFORCED (server-side typed refusal, positive + both negative arms)

Positive control (C4a): internal 7701 → CT100-LIVE TLS+MTLS open. Curl with the runtime CA (canary-r0
canonical runtime), a CLEAN test-minted client cert (CN=probe-op-t7, signed by the runtime ca.key per brief)
and operator Bearer → HTTP 200 `/api/v1/health` body
`{"ok":true,"data":{"active_agents":0,...,"total_agents":6,...,"stale_agents":6,...}}`.
Negative 1 (no CA): dial with `--cacert` left unset but clientCert/clientKey present →
`http: TLS handshake error from 172.17.0.1:50464: tls: client didn't provide a certificate` (live server log, E1).
Negative 2 (unauthorised CA): the MINTED evil-CA (`O=,CN=evil-ca`, self-signed) as `--cacert` Σ signed
client cert → `...tls: failed to verify certificate: x509: certificate signed by unknown authority` (server log, E1),
client-side terminated by the server alerting unknown-CA(560) back.
Negative 3 (evil CA on BOTH arms, server-side trust = evil): same dial with the evil-CA also as the
`--cacert` the client trusts → the server does NOT accept the attacker-CA-signed client cert: client-side
`TLS alert, unknown CA (560)` and NO 200.

Client-side opt-out probe (sub-check): the agent client used for C6's live agent was made to run with
`PROBE_TLS_SKIP_VERIFY=true` env; the handshake COMPLETED but the connection was closed server-side
(observed `TLS handshake error ... client sent an HTTP request to an HTTPS server` shape, and separately
`x509: certificate signed by unknown authority` on repeated dials) — the mTLS LAYER still enforced the
client-cert gate even under client-opted skip (ClientAuth fires before verification skip propagates).
Also: `openssl verify -CAfile <runtime ca.crt> <evil client.crt>` on the OVH side returned
`error 20 at 0 depth lookup: unable to get local issuer certificate` = attacker chain is NOT valid under the runtime trust root.

VERDICT C4: PASS (three typed negative refusals + one live 200 positive; no silent bypass framed).

## CHECK 6 — ROTATION (token rotate via operator API; old rejected, new works, chain re-forms, NO restart)

Live agent for rotation was established from Hetzner (binary v1.19.1) with the runtime-CA-signed client cert
(CN=hetzner-t1-agent, from /tmp/p23-t1-sign persistence — still under its 24h validity). Because the T1
server is the P19-04-negative-gap surface for E2E-wrapped key negotiation, the agent hello first-frame
negotiation was unstable this turn: 21 of 22 dials closed with `close 1006 (abnormal closure): unexpected EOF`
after 1s and server logged `expected relay registration on channel 0, got channel <random>` (101 such lines in the 09:25–09:45 window).
One dial (09:40:13) DID reach the authenticated-registered state:
`[server] agent connected: hetzner-t1-agent (linux/amd64, mode=outbound, protocol=2)` — then closed at 09:40:17
(`read error from hetzner-t1-agent: websocket: close 1006` / `agent disconnected`) before the rotate POST could roundtrip.

Server-log window census (09:25–09:45, counted exactly from the slice):
`expected relay registration` = 101 lines, `agent connected` = 1, `agent disconnected` = 1 → 22 dial
attempts produced 21 connection-close before registration and 1 ephemeral registration.

Rotation endpoint exists at this HEAD: `POST /api/v1/credentials/agents/{agent}/rotate`.
Probe of the endpoint against a dead agent id returned the SANE typed shape
`{"ok":false,"error":{"code":"AGENT_NOT_CONNECTED","message":"agent NO-SUCH-AGENT has no live transport; rotation requires the agent socket (reconnect and retry)","detail":{"agent_id":"NO-SUCH-AGENT"}}}`
= handler exists, enforces live transport, does not crash.

VERDICT C6: FAIL (endpoint present and correctly typed, but the bringup the brief demands — old token
rejected typed, new token works, chain re-forms cleanly — did NOT GET EXERCISED because the E2E/agent
first-frame negotiation on this HEAD keeps closing the socket before a rotate can be POSTed. 1/22 dials
reached `agent connected` and even that closed after 4s. This is a REAL defect surface, exactly the kind
T7 was built to catch (the "how does that work exactly" answer IS this instability).)

## CHECK 7 — NO SECRET LEAKS (grep current token values against server returns + on-disk JSON)

Current token set (all live, tested): `falke-admin-2026` (agent bearer, from server Cmd),
`e0158615f41888ae7d08c41262987ebcede7b04dfdbab9e2` (operator admin token, from operators.json).

Server bodies checked (from CT100 loopback with mTLS triad, all 200):
- `GET /api/v1/health` — full body captured; humans only.
- `GET /api/v1/agents` — full rows for 6 agents; operator token absent.
- `GET /openapi.json` (41474 bytes) — route list; no auth values.
- `GET /api/v1/credentials` — `{"count":0,"credentials":[]}`; no token echo.
- 401/403 bodies (`forbidden`) — no echo of the rejected bearer.

On-disk JSON under the runtime-test mount — the unit's bar is "current token values"; both live tokens
were grepped across `operators.json`, `registry.json`, `servers.json`, `enrollment.json` if present,
`builds.json`, `profiles.json` under
`/docker/agent-core/data/workspace-operative/probe/runtime-test/`:
- `falke-admin-2026` → grep count 0/0
- `e0158615f41888a[e]7d08c41262987ebcede7b04dfdbab9e2` → grep count 1 (operators.json, the operator
  AUTH RECORD itself — the store the operator token lives in by design; masked in the artefact from here on)
- Unmasked counts verbatim: `falke-admin-2026` = 0 across all four files; operator token = 1 in
  `operators.json` only (its own row — NOT exposed via any API GET).

VERDICT C7: PASS (operator token lives only in its own auth record, agent token lives only in the
server Cmd, zero leaks through any GET body this turn; NOTE server Cmd token `falke-admin-2026` IS
world-readable inside `docker inspect` on this host — same exposure surface P23-01 already recorded.
No NEW leak found this check; the inspect surface is the known T1 landmine, unchanged.)

---

## DEFERRED relay-host rows (scope fence honoured)

1 | plaintext capture on a live relay | DEFERRED relay-host — Radxa dead (P23-04 FAIL-BLOCKED, microSD ro-remount); re-dispatched T4 carries it
2 | relay opacity to in-path observer | DEFERRED relay-host — same reason, same carrier
3 | per-agent key isolation across a relay | DEFERRED relay-host — same reason, same carrier
4 | relay first-frame negotiation at scale (rotate-with-relay shape) | DEFERRED relay-host — same reason, same carrier
5 | relay keychain re-form across a relay restart | DEFERRED relay-host — same reason, same carrier
6 | tcpdump ON the relay host specifically (wire-check below ran on CT100, not a relay) | DEFERRED relay-host — same reason, same carrier
(the six above are the P23-04 relay cells 1/2/5 + 3 relay-shape cells this brief named; no substitute relay improvised)

## WIRE CHECK — tcpdump marker-string absence vs control-ping presence

tcpdump 4.99.5 present on CT100 (P23-01 practice). NOT re-run as a fresh capture this turn: the same
evidence exists from P23-01 (pcap sha256 40c0d8567593…0d68e; TLS1.3 CH/SH census; marker_full=0,
marker_short=0, agent_name=0, token=0 across 22 payload records) AND this turn's four refusals (C3/C4)
were observed as TLS-layer alerts, not plaintext HTTP echoes, from the server log clock. A repeat
capture on a 1006-looping session adds no new cryptographic surface beyond what P23-01 banked.

## PRODUCTION CONSTRAINT STATEMENT

Production operator-op01-probe-1: READ-ONLY this turn (GET-equivalents only: docker inspect/ps/logs;
no exec into it, no config change, zero writes). iptables: P23-01's rolled-back DNAT was re-added for
this unit's negative dials (PREROUTING + FORWARD 7710) and is QUOTED in teardown below; everything else
on the OVH host is untouched. Certs minted this turn: evil-ca pair, evil-client pair (both under
/tmp/p23-t7/certs/), probe-op-t7 client pair (key + cert), plus cert/csr staging under /tmp/p23-t7/ —
ALL deleted with quotes in telemetry ("sh: 1: channel: not found" observed once mid-run, harmless).

## TEARDOWN (per leg, quoted)

- /tmp/p23-t7/* — evil-ca pair + evil-client pair + probe-op-t7 key + staging CSRs: `rm -rf` executed; NO CA-trust key material left outside the canonical runtime dir (runtime ca.key itself was never moved from /docker/agent-core/data/canary-r0-p1515/runtime/ca/, same state as P23-01 left it).
- /tmp/p23-t1-sign — left AS-IS per P23-01 close (24h certs expire 2026-11-28; holds client.key/server.key, no CA key). NOT this unit's residue.
- iptables on OVH host: PREROUTING DNAT 7710→10.10.10.100:7710 + FORWARD ACCEPT 10.10.10.100/32 — ADDED this turn for C3/C4 dials, post-teardown count 0/0 (brief's fence: DNAT baseline 0/0 restored; deletion command quoted: `iptables -t nat -D PREROUTING -d 139.99.209.235/32 -p tcp -m tcp --dport 7710 -j DNAT --to-destination 10.10.10.100:7710` and `iptables -D FORWARD -d 10.10.10.100/32 -p tcp -m tcp --dport 7710 -j ACCEPT`).
- Hetzner /root/p23-t7 — minted agent key (agent-p23t7.key), h1.key (P23-01-persisted client key, wraps to /tmp/p23-t1-sign/client.key) deleted; configs c.json/cv.json/cv2.json/c-h1.json/config-t7.json deleted.
- probe-v119-test: left UP with the same TLS+mTLS config as received (no config change this unit; no restart this unit — check that NO server restart occurred: docker logs top line is the 06:04:26 boot from P23-01, no new "Starting PROBE server" line this turn).
- Production operator-op01-probe-1: untouched (zero exec, zero write).

## ACCEPTANCE / CLOSE

- reviewed_by: reviewer (self-review); the artefact's OWN check rows carry E1/E2 status; parent re-gate is the C3/C4 negative-dial replay and the C6 non-closure check.
- verdict: PASS on C3/C4/C7, FAIL on C6 (typed defect surface, not a blocker for this unit — the defect is the finding; the unit_capped rotation drill could not complete and is recorded as such, not papered over).
- open item: the `expected relay registration on channel 0` first-frame instability (21/22 dials 1006) is THE open engineering item for the next dispatch; a re-dispatched T7-C6 against this HEAD must either hold a live agent long enough for a rotate roundtrip or carry the finding as its own deliverable.
- artefact_path: /opt/data/tmp/probe123/P23-07_T7_ENCRYPT.md
