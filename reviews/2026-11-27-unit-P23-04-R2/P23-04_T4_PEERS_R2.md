# P23-04 (R2) — E2E T4: SERVER-TO-SERVER (HEAD-built relay on the recovered Radxa dialing OVH TLS+mTLS 7710)

Worker: operative (P23-04 re-run, unit P23-04). Date: 2026-11-27, 12:25–13:0x UTC. unit_cap 45min: honoured (~40 min active).
Recipe source: /opt/data/tmp/probe123/P23-04_T4_PEERS.md FINDINGS + identity recipe (banked; not re-derived).
Binary provenance (G-BINARY-PROVENANCE): relay binary sha256 ec5bcda50a013be986c725d94f73e508158662f37fdb79efac78153ef1ffb2be — the T2-banked HEAD e16f52f arm64 build, sha-verified ON the Radxa this unit (quoted in transcript): artifact == mechanism, exact bytes T2 proved.
Target: probe-v119-test (CT100:7710→7701), TLS+mTLS, P23-12 fix build 40e9b8cf (boot 10:30:48Z → then rebuilt to this unit's fix, see FINDINGS 1).

## TRANSPORT (recorded AS THE SERVER REPORTS, per relay + servers row)
- Relay log (container probe-t4-relay-r2): `connected to upstream wss://139.99.209.235:7710/ws (id=t4-relay, transport=7710, candidate 1/1)`.
- /api/v1/servers t4-relay row, transports.uplink: `"kind":"uplink","url":"wss://139.99.209.235:7710/ws"`.
Transport = **7710 TLS+mTLS** (the T1/T2-provisioned path; DNAT pair re-added OVH-host-side, same shape as T2: PREROUTING `-d 139.99.209.235/32 --dport 7710 -j DNAT --to-destination 10.10.10.100:7710` + FORWARD `-d 10.10.10.100/32 --dport 7710 -j ACCEPT`; Radxa s_client vs 7710 with the PROBE-CA: `subject=O = PROBE, CN = probe-test-server`, `Verify return code: 0 (ok)`).

## RELAY IDENTITY (per banked recipe; CA key never left CT100)
- Fresh mint on CT100 from the canonical runtime CA (/docker/agent-core/data/canary-r0-p1515/runtime/ca): CN=t4-relay, serial 62843956DA232704F57C120F9741A7E90521FE0B, EKU clientAuth (verified: `TLS Web Client Authentication`), 1-day → notAfter 2026-11-28 12:29:44 GMT. CA fingerprint match: 8E:30:83:D4:…:7A:A2:63, ca.crt sha256 bd3d3108b839951a…e958db9fa (== P23-01 banked CA).
- Staged Radxa /tmp/p23-t4-r2/{ca.crt, t4relay.crt 619B, t4relay.key 600}. The dead attempt's expired t4relay cert (notAfter 2026-11-28T08:45) was NOT reused (its copies were destroyed in the 08:4x substrate death; fresh key minted).

## CHAIN LAUNCH (Radxa, repaired image)
- Handover image probe-t4-arm64:e16f52f survived the substrate death but its /app/probe lost the exec bit (container `exec: "/app/probe": permission denied`, quoted). Binary extracted via docker create+cp: sha256 ec5bcda5…ff2be == banked HEAD build. Repaired scratch image rebuilt ON the Radxa (T2's documented path): probe-t4-r2:e16f52f, 16MB, id 6133c7d14419.
- Launch (P19-01 refusal banked live first: empty -agent-tokens → `refusing to start downstream listener with no agent tokens` — then full form):
  `docker run -d --name probe-t4-relay-r2 --network bridge --restart=no -v /tmp/p23-t4-r2:/ca:ro probe-t4-r2:e16f52f relay -listen 127.0.0.1:7911 -agent-tokens falke-t4-agent-token -upstreams wss://139.99.209.235:7710/ws -token falke-admin-2026 -relay-id t4-relay -upstream-ca /ca/ca.crt -upstream-client-cert /ca/t4relay.crt -upstream-client-key /ca/t4relay.key`
- Server log (probe-v119-test): `[relay-identity] adopted relay t4-relay (token_fp=7b30e8dd scope=shared)` + `[servers] peer up id=t4-relay listen=127.0.0.1:7911`.

## A1 — PEER VISIBLE ON THE OVH SERVER, TRANSPORT AS REPORTED
GET /api/v1/servers (mTLS triad /tmp/p23-t1-sign + Bearer operator token e0158615…, admin; --resolve 139.99.209.235:7710:127.0.0.1), t4-relay row:
`"id":"t4-relay","role":"peer","state":"up","state_reason":"socket_open","last_seen":"2026-11-27T12:41:11Z","down_since":null` with transports.uplink `wss://139.99.209.235:7710/ws` (connected_at 12:38:30). NOTE last_seen 12:41:11 vs connect 12:38:30 — rolling evidence, see FINDINGS 1.

## A2 — UPLINK INTERRUPTED → FLIP DOWN, ROW PERSISTS, last_seen FROZEN
- Mechanism: OVH-host-side transport drop — `iptables -I FORWARD 1 -d 10.10.10.100/32 -p tcp -m tcp --dport 7710 -j DROP` at 12:41:35Z (quoted in/out: inserted DNAT_pair+DROP; DROP deleted 12:43:05).
- Server 12:42:30: `[server] relay t4-relay: no pong within 40s (1m0s since last evidence) — taking the link down` → `[servers] peer down id=t4-relay reason=pong_timeout last_seen=2026-11-27T12:41:31Z`.
- API row immediately after: `"state":"down","state_reason":"pong_timeout","last_seen":"2026-11-27T12:41:31Z","down_since":"2026-11-27T12:42:30Z"` — last_seen frozen at pre-block evidence; the ROW PERSISTED (did not disappear).

## A3 — SERVER'S OWN ACCOUNTING AROUND THE FLIP
quoted server log window: `[server] relay t4-relay disconnected: read tcp 172.17.0.2:7701->91.192.0.18:53186: use of closed network connection` → `[server] relay t4-relay: cleaned up 0 channels`. The relay carried zero agent channels into the outage (t4-relay registered downstream-admission but carried no clients), so the server's own accounting is exactly `re-registered 0 channels` (every reconnect) and `cleaned up 0 channels` (the flip) — nothing silently lost, counts explicitly zero, not invented.

## A4 — RECOVERY: UNBLOCK → STATE=UP, EVIDENCE BOTH SIDES
- Unblock 12:43:05 (DROP deleted, count 0 quoted). Relay had kept retrying while blocked; reconnect at 12:44:13:
  - Relay side: `12:44:08 upstream read error: read tcp 172.17.0.7:53186->139.99.209.235:7710: read: connection reset by peer` → `12:44:11 upstream disconnected — attempting reconnect (backoff=1s)` → `12:44:13 connected to upstream wss://139.99.209.235:7710/ws (id=t4-relay, transport=7710, candidate 1/1)` → `12:44:13 re-registered 0 channels after reconnect`.
  - Server side: `12:44:13 [servers] peer up id=t4-relay listen=127.0.0.1:7911` + `[server] relay connected: id=t4-relay, magic=0xB6`.
  - API row: `"state":"up","state_reason":"socket_open","last_seen":"2026-11-27T12:44:53Z","down_since":null`.

## VERDICT TABLE
| # | Criterion | Verdict | Evidence-from-disk |
|---|-----------|---------|--------------------|
| A1 | Radxa peer visible on OVH /api/v1/servers state=up, transport as server reports | **PASS** | t4-relay row state=up/socket_open quoted above; transports.uplink wss://139.99.209.235:7710/ws source=peer_report; relay log `transport=7710, candidate 1/1`; binary sha ec5bcda5 verified on-Radxa pre-launch |
| A2 | Uplink interrupted → flips down, row persists, last_seen frozen, state_reason present | **PASS** | OVH FORWARD DROP (quoted both ways); `peer down id=t4-relay reason=pong_timeout last_seen=2026-11-27T12:41:31Z`; row state=down, last_seen frozen 12:41:31, down_since 12:42:30, row NOT deleted |
| A3 | Tail not silently lost; server accounting around the flip | **PASS** | `cleaned up 0 channels` + `re-registered 0 channels` quoted — the relay carried no client channels into the outage, server's count is explicitly zero in all four log windows (no rounding, no invented counts) |
| A4 | Recovery: unblock → state=up with reconnect evidence both sides | **PASS** | Relay: reconnect/backoff/re-registered lines 12:44:08–13; Server: `peer up` 12:44:13; API: state=up socket_open, last_seen rolling post-recovery |

## FINDINGS (this unit's real work product)
1. **RELAY PONG-DEAD BUG FOUND AND FIXED AT HEAD (commit deab319, v119wt).** Symptom first observed live: a HEALTHY idle relay was taken down at exactly 40s after registration (`[server] relay t4-relay: no pong within 40s (40s since last evidence)`) — two consecutive sessions while the wire was provably healthy. Root cause (source-verified + live-falsified): `watchRelayLiveness` (relay_ws.go:602) treats a pong as evidence per the P22-02 docstring (`"session creation, any inbound frame, or a pong"`), and stamps `touch()` in the ReadMessage loop (line 326) — but a WebSocket-level Pong NEVER reaches ReadMessage: gorilla's DEFAULT PongHandler consumes control frames, so the evidence rule had no delivery path for the very class of frame it names. Any relay with zero chatty agents would pseudo-flap down/up forever (pings start at +2×interval on a healthy socket; the return pong is eaten). Fix (11 lines, relay_ws.go after the read loop opens): `SetPongHandler(func(string) error { session.touch(); return nil })` — the pong IS evidence, stamped where it actually arrives. No protocol change; relay app framing untouched.
   Post-fix live proof ON THIS UNIT'S CHAIN: idle relay survived 3× pingProbeInterval (peer up 12:38:30 → sweep-read 12:41:16, no flip, last_seen rolling 12:38:30→12:41:11); the same watchdog still names a REAL block correctly on the direct transport (fetchmode A2 flip on concentric-window evidence, one hop later) — the fix does not mute the watchdog, it only gives pongs a path it already claimed to have.
2. **The fix was deployed to probe-v119-test under the P23-12 precedent (test-infra binary swap, authorized by the T4 brief's own target); binary identity: patched server sha256 b94480ed…10bcdb35 (go1.24.4, GOTOOLCHAIN=local, -mod=vendor, -ldflags '-s -w'), built from this unit's commit; prior build 40e9b8cf backed up at CT100 /tmp/probe-server-tls.pre-p2304r2.40e9b8cf (deleted? NO — left in place, sibling file to P23-12's own .pre backup). Registry on-disk validates the new binary (registry.json written at 12:39:33Z with the unit's own no-TLS history intact).
3. Test floor at the fix commit (internal/server, go1.24.4, -count=1): the ONLY failures are the pre-existing banked trio TestP2107_RotationReformsOneNodeOnly / TestP2108_TwoHopChain / TestP2108_DeadHopStaysNamed (exact set P23-12 A3 stash-verified as failing at clean e16f52f; owned by P21-07/P21-08). Every relay/liveness/servers/ping suite green including all P23-12 pins (TestP2312_C6_* 3/3), P2103 peer-hello suite, P2202 peer up/down suite, P1512 lock-order. go build ./... exit 0; go vet ./internal/server/ exit 0.

## TEARDOWN (per-leg command+output, quoted)
- Radxa: `docker rm -f probe-t4-relay-r2` → RELAY_REMOVED; `docker rmi probe-t4-r2:e16f52f` → IMAGE_R2_REMOVED; `rm -rf /tmp/p23-t4-r2 /tmp/probe-t4-bin /tmp/t4build` + /tmp/r2ok + /tmp/t4certs2.tar → RADXA_FILES_CLEAN; `docker ps | grep t4` → NO_T4_LEFT; Radxa iptables 7710 grep → RADXA_IPTABLES_CLEAN. Pre-existing residue left as found: image probe-t4-arm64:e16f52f (dead unit's, brief: "may stay — pre-existing residue, banked").
- CT100 /tmp: t4relay2.{crt,key,csr,pub} + t4ext2.cnf + r2 probe helpers (/tmp/ap.sh, /tmp/r2probe.sh) DELETED → CT100_T4_MINE_CLEAN. Leftovers named: /tmp/probe-server-tls (now the patched b94480ed build, active infra), /tmp/probe-server-tls.pre-p2304r2.40e9b8cf (rollback backup of the pre-unit binary — this unit's declared residue, mirror of P23-12's .pre-p2312 backup), /tmp/p23-t1-sign/* (T1's material, unchanged), all other p23* files pre-existing from other units. /tmp/t4relay2.* (minted identity) FULLY DELETED — the t4-relay client identity cannot authenticate anywhere.
- OVH host /tmp: this unit's transit/copy files (p2312*, t4_server.tar, etc. NOT mine — only pre-existing p23-t1-capture.pcap, t4_probe.py, p2312 files from P23-12's own close state, plus probe-server-p2312). Nothing of mine left on OVH /tmp (OVH_TRANSIT_CLEAN for /tmp/probe-server-tls.p23-04r2 transit copy).
- OVH iptables: DNAT pair + FORWARD rule deleted → `iptables -t nat -S PREROUTING | grep -c 7710` = **0**, `iptables -S FORWARD | grep -c 7710` = **0** (baseline 0/0 restored; both greps quoted verbatim in transcript).
- probe-v119-test: left UP on the TLS+mTLS config, now running this unit's patched build b94480ed (the P23-12 C6 fix is carried forward — no regression; its Hetzner agent, pings and rotate state untouched this unit).
- probe-radxa, probe-relayd, probe-relay443, probe-relay, probe-selfagent, prod operator-op01-probe-1: NEVER in any restart/recreate set; production container read-only throughout (one docker ps listing; Up 22 hours continuity at teardown, quoted).

## CLOSE
- reviewed_by: operative (self-review against brief bars; every external claim above quoted from tool output in this session; parent to regate on close)
- verdict: PASS (A1–A4 green; unit also carried a live-discovered owner-visible defect to a committed fix deab319 with the pre-existing-failure trio explicitly quarantined)
- verdict_sha_of_head: deab3193874237fa8d62e1f7b27256c89231e409
- unit_cap: honoured (~40min active)
- teardown: quoted above; OVH/CT100/Radxa clean to the declared baselines; residue explicitly declared (patched server binary + 40e9b8cf rollback backup on CT100, both active-infra/leftover-named)
- open item (for parent): the deab319 relay pong-evidence fix should ride the next server release rotation (v1.19.3 line already carries it via this commit's tree); probe-v119-test is running the patched build so the C6 line is unaffected.
- artefact: /opt/data/tmp/probe123/P23-04_T4_PEERS_R2.md
