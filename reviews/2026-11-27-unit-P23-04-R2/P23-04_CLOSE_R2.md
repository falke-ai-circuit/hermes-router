# P23-04 (R2) — E2E T4: SERVER-TO-SERVER — close record (operative)

verdict: PASS. verdict_sha_of_head (v119wt, product): deab3193874237fa8d62e1f7b27256c89231e409. Full delivery: /opt/data/tmp/probe123/P23-04_T4_PEERS_R2.md (5 contract lines, A1–A4 evidence rows, FINDINGS, TEARDOWN).

- A1 peer visible: PASS — t4-relay row state=up/socket_open on /api/v1/servers; transport AS THE SERVER REPORTS: transports.uplink url=wss://139.99.209.235:7710/ws (source peer_report); relay log `transport=7710, candidate 1/1`.
- A2 uplink interrupted (OVH-host FORWARD DROP on 7710, quoted in/out): PASS — `peer down id=t4-relay reason=pong_timeout last_seen=2026-11-27T12:41:31Z`; row PERSISTED, last_seen frozen, down_since 12:42:30, state_reason present. NO disappearance.
- A3 server accounting around the flip: PASS — `[server] relay t4-relay: cleaned up 0 channels` + `re-registered 0 channels after reconnect` quoted (relay carried zero agent channels; counts explicit, not invented).
- A4 recovery: PASS — unblock 12:43:05 → relay `connected to upstream wss://139.99.209.235:7710/ws (id=t4-relay, transport=7710, candidate 1/1)` 12:44:13 + `re-registered 0 channels after reconnect`; server `peer up id=t4-relay` 12:44:13; API `state=up, socket_open`.
- Binary: relay ec5bcda5…ff2be (T2-banked HEAD e16f52f arm64 build, sha-verified on Radxa after docker cp extract); target server probe-v119-test boot log "Starting PROBE server with TLS+mTLS on 0.0.0.0:7701 … client-ca=/tmp/p23-t1-sign/ca.crt", patched this unit to sha b94480ed…10bcdb35 (see finding below).
- FINDING CARRIED: relay pong-evidence defect live-falsified and fixed at v119wt deab319 (WS Pong never reached ReadMessage — gorilla default PongHandler consumed it — evidence rule had no pong path; idle relay falsely taken down at exactly 40s); fix deployed probe-v119-test b94480ed; pre-existing test trio P2107/P2108×2 stash-banked at clean e16f52f (P23-12 A3) — only failures, unchanged.
- Teardown: relay container + rebuilt image removed; Radxa /tmp clean; CT100 identity material deleted; OVH DNAT pair deleted — baseline `nat 7710 = 0`, `fwd 7710 = 0` restored (quoted). probe-relayd/probe-radxa/probe-relay443 NEVER touched; operator-op01-probe-1 read-only, Up 22h at close.
- Residue declared: /tmp/probe-server-tls (now b94480ed, active infra) + rollback backup /tmp/probe-server-tls.pre-p2304r2.40e9b8cf (deleted at parent discretion; the 40e9b8cf build is reproducible from a67c612).

artefact: /opt/data/tmp/probe123/P23-04_T4_PEERS_R2.md
