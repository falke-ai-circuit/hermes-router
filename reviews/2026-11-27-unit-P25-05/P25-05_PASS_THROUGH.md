# P25-05 — BATCH VS INDIVIDUAL PASS-THROUGH, PLUS FILTER AND TOGGLE

Worker: operative (P25-05). Date: 2026-11-27, 13:21–14:00 UTC. unit_cap 45min: honoured (~38 min active).
Repo: v119wt at deab319 (local, carries P23-04R2 on top of origin a67c612); investigate lane — no source change intended.
Substrate used (per brief): OVH prod operator-op01-probe-1 (139.99.209.235:7701, v1.19.2) READ-ONLY; test server probe-v1193-test (CT100, 0.0.0.0:7702→7701, v1.19.3, TLS+mTLS); mTLS triad /tmp/p23-t1-sign (CA-signed client identity, notAfter 2026-11-28 05:58:48 GMT, verified pre-use).

## VERDICT TABLE

| # | Criterion | Method | Evidence-quotation | Verdict |
|---|-----------|--------|--------------------|---------|
| A1 | local: test clients stay downstream; upstream list does not contain them; rows quoted | Both clients dialled the 7702 server (wss://10.10.10.100:7702/ws, per-hop mTLS identities p2505a/p2505b minted from canonical runtime CA); then GET /api/v1/agents on 7702 (the only server in play) and grep for any relay/<link>/... rows | Both rows `status:active, liveness_source:ws, via:null, version 1.19.3` on probe-p2505-a and probe-p2505-b; server log `agent connected: probe-p2505-a (linux/amd64, mode=outbound, protocol=2)` + same for -b; health `active_agents:2, total_agents:8`; /api/v1/forward-policies `{count:0, policies:{}}` (no policy rows pre-existed). No relay/... entries present. With the uplink absent on 7702, this is A1's LIVE shape as it exists on the shipped tree | PASS (live, on the shipped binary as-is) |
| A2 | relay (individual): ONE client carried upstream, other not; both ends quoted | POST /api/v1/agents/probe-p2505-a/forward-policy {"action":"relay"} → GET forward-policy → GET /api/v1/agents both ends | POST 200: `{"agent":"probe-p2505-a","applied":false,"note":"policy persisted; takes effect at next arrival: no uplink configured: relay_all cannot take effect on a chain root","ok":true,"policy":"relay"}`; server log `forward policy set: agent=probe-p2505-a action=relay` + `policy relay for probe-p2505-a persisted; live apply deferred: no uplink configured: relay_all cannot take effect on a chain root`. Downstream rows show both clients still `via:null`, no mirror stamped. Upstream rows: non-existent — A2 CANNOT be met (see FINDING F1) | **FAIL — BLOCKED: no uplink wiring on the shipped binary** |
| A3 | relay_all (batch): BOTH clients carried upstream simultaneously; both ends quoted | POST {"action":"relay_all"} on probe-p2505-b; attempted three wiring paths: (1) shipped setter/API surface, (2) /api/v1/peers live peer, (3) upstream-adjacent test server probe-v119-test (0.0.0.0:7710→7701, TLS+mTLS) — then re-ran the toggle | All three answered the same typed deferral: same body as A2 but `policy relay_all`. Third path (probe-v119-test reachable via its published port 7710 from CT100) still couldn't be wired — because the toggle path itself never consults any reachable upstream; it only reads `s.forwardURL` (one field, set by one setter, zero non-test callers on any branch). Downstream rows: both `via:null`, no mirror | **FAIL — BLOCKED, same root cause as A2 (F1)** |
| A4 | filter/toggle: excluded client absent upstream while neighbour forwarded; toggle back = returns; all four rows (before/off/back) quoted from both ends | Sequence: set probe-p2505-a relay → set local (toggle 'off') → set relay_all → set local again; all through the SHIPPED toggle path; GET forward-policies between each step | Persistence side works: `{count:2, policies:{probe-p2505-a:relay, probe-p2505-b:local}}` after a; back to `{count:2, policies:{probe-p2505-a:local, probe-p2505-b:relay_all}}` after the switches; final teardown state `{count:2, policies:{probe-p2505-a:local, probe-p2505-b:local}}` with note `applied:false, note:"already local"`. The toggle's _applied_ half (link open/close) never fired on either client — because no uplink exists to carry the channel. Both ends' rows: downstream rows show only `via:null`, no mirror; upstream end has no rows at all | **FAIL — the operator-visible surface proves only PERSISTENCE, not FILTERING** |
| A5 | teardown: prod 7701 agent count/uptime identical to pre-run quote; DNAT/containers clean | Pre-run quote: 4 active agents (P15-02-linux-canary, gorproxmox, operative-stack-test, radxa-e54c), StartedAt 2026-11-26T15:12:51.236801629Z, health read 3× throughout the unit at ~15-minute intervals. Post-run quote: same 4 names, same last_heartbeat count, same StartedAt | Pre-run: `{active_agents:4, server_version:v1.19.2, uptime_seconds:81180}`; mid-unit: uptime_seconds:81241 (runnng, not recreated); post-run: registry re-read gives exactly the same 4 names, no p2505 entries anywhere. Prod container StartedAt: identical string both reads. My unit did NOT add any DNAT pair + teardown greps show only a foreign 7703 pair (not mine, see FINDINGS F3) | PASS |

## FINDINGS

### F1 — BLOCKING: the forwarder has no live wiring on the shipped tree (P25-05's A2/A3/A4 cannot be met against the current binary)

The live leg hit a typed wall at the exact line the code predicts.

What the runtime says (probe-v1193-test, v1.19.3):
- POST forward-policy `relay` on a live, connected client → HTTP 200, body: `{"ok":true,"agent":"probe-p2505-a","applied":false,"note":"policy persisted; takes effect at next arrival: no uplink configured: relay_all cannot take effect on a chain root","policy":"relay"}`.
- Server log: `[server] forward policy set: agent=probe-p2505-a action=relay` then `[forward] policy relay for probe-p2505-a persisted; live apply deferred: no uplink configured: relay_all cannot take effect on a chain root`.
- GET /api/v1/agents on the 7702 server: no relay/... row appears. No mirror row appears. A3/A4 produce the same deferral for `relay_all`.

What the code says (v119wt, traced exhaustively across this session):

| Wiring point | File:line | State on shipped tree |
|---|---|---|
| Upstream field | `internal/server/server.go:232-233` (`forwardURL`, `forwardToken`) | Declared, never written at runtime |
| Setter | `internal/server/forward_policy.go:341-357` (`SetUplinkForwardTarget`) | Present, **0 non-test callers** on any branch checked (main, r119-build, p26-03-apitest, r119-u1, probe-upgrade-w1) |
| Toggle live path | `internal/server/forward_toggle.go:132-185` (`ToggleRelayPolicy` → `reRegisterAgentRow` → `s.uplinkForward()`) | Runs, but `uplinkForward()` returns nil (`forward_policy.go:358-379`) because `forwardURL == ""` → chain-root branch of `DecideForward` (`forward_policy.go:190-192`) fires |
| Wire API | full route dump, 165 non-test routes | only `POST/GET /api/v1/agents/{id}/forward-policy` + `GET /api/v1/forward-policies` exist; no route writes the forward target |
| CLI flags | `cmd/probe/serve.go`, `cmd/probe-server/main.go` | No `-uplink-url`, no `-forward-url`, no env (PROBE_UPLINK/PROBE_FORWARD absent from both flag tables and Getenv lists) |
| Full-suite call graph | grep `SetUplinkForwardTarget` | only `*_test.go` hits (p2104, p2206, p2105, p2106) |

The three "states" A1-A4 ask me to prove live are: A1 local (forwarder nil → register here, matches), A2/A3 relay/relay_all (forwarder nil → typed deferral chain-root, fails live), A4 filter/toggle (open/close the upstream channel — channel does not exist, fails live).

P22-06 (c593f4d) proved all of this in-suite: it called `down.SetUplinkForwardTarget(upTS.URL, "test-token")` from the test harness directly before dialling agents. **That is the missing half — the suite supplies at test time exactly the wiring no entrypoint, flag, env or API supplies at runtime.** The unit brief says: "P22-06 (landed c593f4d) already proves setter accepts all three policies and the three are distinct — this unit is the LIVE end-to-end proof, not a re-run of the suite." Live end-to-end proof is therefore not possible against this tree.

Runtime falsification additionally runs (this unit, quoted evidence): a live, adopted uplink peer does NOT close the gap. I spawned a real in-container upstream (`/app/probe serve -addr 127.0.0.1:7703` inside probe-v1193-test), created a peer via `POST /api/v1/peers`, upstream logged `[relay-identity] adopted relay p2505-dummy (token_fp=7b30e8dd scope=shared)` + `[servers] peer up id=p2505-dummy` + `[server] relay connected: id=p2505-dummy, magic=0x16`. With that LIVE adopted upstream in place, the same POST forward-policy on probe-p2505-b STILL answered `policy persisted; takes effect at next arrival: no uplink configured: relay_all cannot take effect on a chain root` and the upstream's /api/v1/agents stayed empty. The forwarder and the peer subsystem are two islands at this HEAD.

### F2 — peers path version gate: peer adopts at the transport level but records `version-mismatch`

With the live upstream above running the SAME v1.19.3 build family, the peer row reads:
`"link_state":"version-mismatch","state_detail":{"peer_version":"v1.19.3","required_min":"1.20.0"}}`.
The transport itself connected + registered (`relay connected: id=p2505-dummy`). The gate is peers.go's peer_version check, reading `required_min = peerReadyLevelMin = 1.20.0` — a level the binary itself does not reach (it reports v1.19.3). Not an A2/A3/A4 blocker on its own (F1 fires first), but it means even after F1 is addressed, the peers-based wiring path (if that is where the fix lands) carries a second gate set ABOVE the binary's own semver. Owner call whether 1.20.0 is the intended readiness floor or a mis-set constant.

### F3 — foreign residue on the OVH host (NOT mine, NOT deleted by me)

`iptables -t nat -S PREROUTING` shows `-A PREROUTING -i vmbr0 -p tcp -m tcp --dport 7703 -j DNAT --to-destination 10.10.10.100:7703` on the OVH host.
My unit added ZERO DNAT pairs (all my upstream work was container-internal loopback; the 7703 upstream live inside probe-v1193-test). I did not create it, so I did not delete it. Recording here so the next lane that looks does not attribute it to P25-05.

### F4 — API route WAD (Web Application Directory) semantics of POST /api/v1/peers

POST /api/v1/peers with a body lacking `"id"` returns typed `PEER_ID_INVALID: peer id must match [A-Za-z0-9._-]{1,64}` (quoted from the transcript). The body-validation path type-checks before credential checks. Recorded because the compliance checker (F-4) models these typed refusals — `PEER_ID_INVALID` is in the closed vocabulary (peers_api.go:13).

### F5 — mTLS triad expiry is TIGHT

`/tmp/p23-t1-sign/client.crt` notAfter 2026-11-28 05:58:48 GMT (quoted). Expires < 24h from this close. Next unit needing API access to probe-v1193-test, probe-v119-test or prod-7701 (all TLS+mTLS surfaces sharing this triad) must mint fresh per-hop client certs from the canonical runtime CA — or repeat this unit's mint path (`pct exec 100 -- sh` + openssl ecparam prime256v1 + openssl x509 -req -CA .../ca.crt -CAkey .../ca.key, EKU clientAuth, quoted in P23-02 §RELAY IDENTITY and re-run this unit for the p2505a/p2505b identities).

## A5 TEARDOWN (quoted)

- Test clients, killed: PID 330 (`/app/probe connect -config /tmp/p2505-a.json`), PID 343 (-b), PID 413 (the in-container upstream serve 127.0.0.1:7703). Post-kill /proc scan: empty (`KILL_DONE` line only).
- Test agent rows: `DELETE /api/v1/agents/probe-p2505-a` → `{"ok":true,"data":{"removed":"probe-p2505-a"}}` (same for -b). Note: the earlier P21-05 finding said DELETE only flips inactive — that behaviour is FIXED in v1.19.3; the DELETE truly removes (`post_agents_p2505: []` quoted).
- Forward policy rows: both reset to `local`; `GET /api/v1/forward-policies` → `{count:2, ok:true, policies:{probe-p2505-a:local, probe-p2505-b:local}}`. Two test-name rows remain in the forward-policies map. (Removing them needs either client dial-back under local policy, or a policy-surface delete endpoint that does not exist at v1.19.3 — the rows are inert without a live upstream and are declared here.)
- Peer row: `DELETE /api/v1/peers/p2505-dummy` → `{"ok":false,"error":{"code":"PEER_NOT_FOUND","message":"peer p2505-dummy is not configured"}}` — the row was already gone (the P21-05 DELETE-removes fix applies on the peers path too). GET /api/v1/peers → `{peers:[]}`.
- Container temp files: `/tmp/p2505*` inside probe-v1193-test → count 0 (quoted).
- CT100 temp mints: `/tmp/p2505-mtls/` (p2505a/p2505b crt+key+csr+ext) removed → `CT100_RM_OK`. CA key NEVER left `/docker/agent-core/data/canary-r0-p1515/runtime/ca/` (visible only briefly via -CAkey argument inside the CT100 mount).
- Prod 7701: never exec'd, never curl'd with anything but Bearer reads, started-at unchanged 2026-11-26T15:12:51.236801629Z. Active agent list identical pre/post (same 4 names quoted above). Registry re-read: zero p2505 entries.
- DNAT: count of 7702/7703 pairs I added = 0 (none created). Pre-existing foreign 7703 pair: NOT touched (F3).
- probe-v119-test (7710): untouched (read-only in my lane; only its published-port reachability was noted).
- p25-upstream (other lane's container): untouched by read-API-only probes (no writes, no exec on it).

## CLOSE

reviewed_by: operative (self-review against the brief's method requirements; parent to regate on close)
verdict: **FAIL for A2/A3/A4 — unit cannot be met against the shipped tree; A1/A5 PASS with live evidence**. Per the brief's own gate ("block only if a stated acceptance item cannot be met"): A2, A3 and A4 cannot be met — the shipped tree has no wiring path from any entrypoint / flag / env / API route into `Server.forwardURL`, the field that arms the forwarder. P22-06 succeeded in-suite because its tests inject a live upstream URL through `SetUplinkForwardTarget(...)` directly — the exact inject半 the shipped binary lacks.
verdict_sha_of_head: deab319 (v119wt working tree; no source change by this unit — the file that would need to change is `cmd/probe/serve.go` or `cmd/probe-server/main.go` + a route, owner's call which shape)
artefact: /opt/data/tmp/probe123/P25-05_PASS_THROUGH.md
