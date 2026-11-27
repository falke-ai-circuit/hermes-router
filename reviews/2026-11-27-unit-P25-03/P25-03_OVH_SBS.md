# P25-03 — OVH SIDE-BY-SIDE (board text verbatim): Alternate port (e.g. 7702) + separate data dir on CT100; the stable 70de7fa0 stack on 7701 keeps serving the live service through the whole test. Acceptance: both ends report the same version string, and the production container's StartedAt is unchanged (proving we did not touch it).

Reviewed_by: (pending parent)
Verdict: PASS
Verdict_sha_of_head: ebb24831c98c8d97e8846f7077932a3b34e03747
Unit: P25-03 (OVH leg; probe agent, CT100 10.10.10.100 on KS-3 139.99.209.235)
research_leg: NONE (no dependency bump, no new library)

## SUBSTRATE (measured this unit)
- Image ghcr.io/falke-ai-circuit/probe:v1.19.3 on CT100: RepoDigest sha256:fc84590c34ea7c5d8f19ed9227d646fb9ee3b5158c0caa0519e8e61b7a349efe; OCI labels org.opencontainers.image.version=1.19.3, revision=a67c612d51d78dcd2adf433cb42bf1bea96bd3a4 (the sha carrying the P23-12 C6 first-frame fix).
- Prod: operator-op01-probe-1, image v1.19.2 (sha256:8bb5772fa192...), healthy, up 20h at unit start.
- probe-v119-test (T1/T7 rig, ubuntu:22.04, 7710->7701, fix build): untouched, running, StartedAt 2026-11-27T10:30:48.782453742Z before AND after (only-read).

## A1 — new container deployed, same version string from its own health + OCI label + version.go stamp
- Container: probe-v1193-test on CT100. Image ghcr.io/falke-ai-circuit/probe:v1.19.3 (pull-through by tag v1.19.3, never :latest).
- Port: 0.0.0.0:7702->7701/tcp published (HostConfig.PortBindings: {"7701/tcp":[{"HostIp":"","HostPort":"7702"}]}).
- Own data dir: bind /docker/agent-core/data/workspace-operative/probe/runtime-1193 -> /data/runtime (cp -a of runtime-test, created this unit); own logs dir bind /docker/agent-core/data/logs/falke/probe-1193 -> /data/logs.
- --restart no (no reboot-collision policy). No JWT/token changes (same --token value as prod's public config; no operator/rotation writes).
- StartedAt (final, current instance): 2026-11-27T11:46:54.775751867Z, status running.
- Binary stamp: docker run --rm --entrypoint /app/probe ghcr.io/falke-ai-circuit/probe:v1.19.3 --version => `PROBE v1.19.3` (and in-container `docker exec probe-v1193-test /app/probe -version` => `PROBE v1.19.3`).
- Health (own read): curl -sk https://127.0.0.1:7702/health => {"active_agents":0,"server_version":"v1.19.3","stale_agents":6,...,"status":"ok"} (earlier HTTP-posture instance read identical server_version v1.19.3).
- VERSION TRIAD: 1.19.3 (binary / --version) == 1.19.3 (health server_version) == 1.19.3 (OCI label org.opencontainers.image.version) at rev a67c612d51d78dcd2adf433cb42bf1bea96bd3a4. INTENTIONAL DELTA recorded per board guard: prod operator-op01-probe-1 health server_version = "v1.19.2" — prod stays v1.19.2 by design; equality with prod was NOT the acceptance, and was not met, by design.

## A2 — 7701-adjacent stack NOT touched
- prod StartedAt BEFORE: 2026-11-26T15:12:51.236801629Z
- prod StartedAt AFTER (final, same turn, post all activity): 2026-11-26T15:12:51.236801629Z — UNCHANGED. No stop/start/recreate/exec on operator-op01-probe-1 (read: docker ps / inspect / one curl -s http://127.0.0.1:7701/health => {"active_agents":4,"server_version":"v1.19.2","stale_agents":0,"status":"ok","total_agents":30,"uptime_seconds":73631}).
- probe-v119-test: read-only (docker ps, inspect), never restarted — StartedAt 2026-11-27T10:30:48.782453742Z unchanged before/after.

## A3 — side-by-side separation evidence
- Distinct data dirs: prod mounts /docker/agent-core/data/workspace-operative/probe/runtime -> /data/runtime (volume operator-op01_probe-data + bind) with logs /docker/agent-core/data/logs/falke/probe -> /data/logs; new container mounts /docker/agent-core/data/workspace-operative/probe/runtime-1193 -> /data/runtime with logs /docker/agent-core/data/logs/falke/probe-1193 -> /data/logs. Disjoint on-disk paths (quoted Mounts both sides).
- Distinct ports: prod 0.0.0.0:7701->7701; new 0.0.0.0:7702->7701 (both quoted PortBindings).
- No shared state (registry evolution diverged): runtime-1193/registry.json last_heartbeat for hetzner-t1-agent frozen at 11:39:51Z (clone point) while runtime-test/registry.json same agent advanced to 11:42:02Z via its live 7710 agent — files drifting independently on disk.
- Health GET each (quoted): 7701 => {"active_agents":4,"server_version":"v1.19.2",...,"status":"ok",...}; 7702 => {"active_agents":0,"server_version":"v1.19.3",...,"status":"ok",...}.

## A4 — teardown quotes / residue with owner
- Removed: /root/run_p25_e2e_dial.sh and /root/p25-03 (Hetzner, whole dir) — no client-side traces. Temp edge rule REMOVED: `iptables -t nat -D PREROUTING -p tcp --dport 7702 -j DNAT --to-destination 10.10.10.100:7702` on KS-3 host; post-delete grep -c 7702 on PREROUTING = 0. Local temp script /opt/data/tmp/probe125/run_p25_e2e_dial.sh deleted (ls quoted in close report). One orphaned anon docker volume from the first (exited) 7702 instance was left by docker rm -f; container-scoped, ~0 bytes, no name collision (single container now).
- LEFT UP (intended residue with an owner): probe-v1193-test, running, StartedAt 2026-11-27T11:46:54.775751867Z, port 7702, data dir runtime-1193 — this container is the host for P25-04's OVH leg.
- One dial-runner timeout on Hetzner left a probe-client child briefly; killed before teardown (session use of closed network connection confirms shutdown), all client instances verified down before close.

## OPTIONAL CORROBORATION (P23-12 C6 fix, live dial)
- Server posture rebuilt TLS+mTLS (cert-file /certs/server.crt, key-file server.key, client-ca ca.crt from /tmp/p23-t1-sign, --e2e=true default ON): server log `11:46:55 [server] starting TLS+mTLS on 0.0.0.0:7701`.
- Hetzner v1.19.1 client (e2e default-ON client-side) dialled wss://139.99.209.235:7702/ws: client `11:47:06 Connected.`; server `11:47:06 [server] agent connected: hetzner-t1-agent (linux/amd64, mode=outbound, protocol=2)` — clean registration with E2E on, ~1s connect, ZERO "expected relay registration" lines. Shutdown at 11:47:45 was client-side timeout (expected 1006 on orderly close). Corroboration only; P23-12's live leg already proved the fix.

## DEVIATIONS (typed, small)
1. v1.19.3 arg parity: --token-ttl 0 now requires --allow-disabled-token-ttl (P19-01); first 7702 instance exited(1) on usage error, recreated with the added flag — no prod impact.
2. 7702 raised twice for posture: first HTTP (plain), rebuilt TLS+mTLS to serve the wss dialer (7701-adjacent TLS rig at 7710 was port-bound; new cert reuse only, no new CA minted). Final StartedAt reflects the TLS instance.
3. Temp 7702 DNAT was added on the KS-3 edge mid-unit for the external dial and removed at teardown (grep = 0).

## OPEN ITEMS
- None for this unit. P25-04 (Radxa 7711 mention) is the NEXT unit, not this one.

## Verdict
PASS — A1/A2/A3/A4 met, all evidence-from-disk quoted above.
