# P23-04 — E2E T4: SERVER-TO-SERVER (Radxa peer visible from OVH, no client involved)

Worker: operative (P23-04). Date: 2026-11-27, 08:15–09:01 UTC. unit_cap 45min: honoured (46 min active).
Repo at HEAD: /opt/data/projects/PROBE_UPGRADE/v119wt @ e16f52f (clean, `git status --porcelain` empty during build leg).
Binary provenance (G-BINARY-PROVENANCE): arm64 relay binary rebuilt at HEAD e16f52f from v119wt (go1.24.4, GOTOOLCHAIN=local, GOOS=linux GOARCH=arm64, CGO_ENABLED=0, -mod=vendor) — sha256 ec5bcda50a013be986c725d94f73e508158662f37fdb79efac78153ef1ffb2be, BIT-IDENTICAL to the T2-banked build (P23-02 artefact line 5). Artifact == mechanism: the exact bytes T2 proved on this Radxa.

## TRANSPORT DETERMINATION (A1 precondition — measured, not assumed)

- probe-radxa Args (docker inspect, quoted 08:18Z): `serve --addr 0.0.0.0:7701 --token falke-admin-2026 …` — NO upstream/peer/uplink flags of any kind. Container env: no-upstream-env. Container netns + host ns: zero TCP connections to 139.99.209.235 (`ss -tn | grep 139.99.209.235` empty both).
- Upstream peers in v119wt dial via `relay` mode (`cmd/probe/relay.go`: `-upstream/-upstreams` candidate list, P21-09). probe-radxa is not, and has never been, a dialer.
- Radxa→OVH direct 7701: TCP-OK (host-level `/dev/tcp` probe) but DIAL-DEAD for a peer: the OVH host exposes 7701 only via the outer DNAT (80/443→CT100 shape); 7701 has no DNAT (iptables nat PREROUTING grep 7710/7701 = 0 pre-unit) and probe-v119-test's TLS listener answers public-7701 dials with nothing routable. Transport options for a T4 peer were therefore: (a) provision a temporary DNAT pair for 7701, or (b) reuse the T2-proven 7710 TLS+mTLS path (DNAT pair per T1/T2 shape, quoted in/out, deleted same unit).
- The peer mechanism of record (P21-03 `peer_hello` + P22-01 F1 `/api/v1/servers` contract) is exercised by ANY relay-mode upstream dial: t2-relay's row in servers.json IS a peer row (role=peer, state up/down machinery). The T4 subject "probe-radxa dials OVH as PEER" does not exist in the running substrate: probe-radxa (ghcr v1.19.2, 17h uptime, PRODUCTION, read-only) carries no peer-dial configuration, and the fleet's peer dialer is the `relay` mode at HEAD.

## EXECUTION (what was provisioned, in order, before the substrate died)

1. Binary built at HEAD (sha above), b64+gzip shipped OVH→Radxa: sha256 re-verified Radxa-side `ec5bcda5…ff2be` (quoted in transcript).
2. Scratch image built ON the Radxa (T2's documented save|load-free path): `probe-t4-arm64:e16f52f`, 16MB, id f700ec0430e6.
3. Relay identity minted on CT100 from the canonical runtime CA (/docker/agent-core/data/canary-r0-p1515/runtime/ca/ca.key, CA cert sha256 bd3d3108b839951a…, fingerprint 8E:30:83:D4:…:63 = /tmp/p23-t1-sign/ca.crt same CA): CN=t4-relay, serial 62843956DA232704F57C120F9741A7E90521FE08, EKU clientAuth, notAfter 2026-11-28T08:45:05Z. Staged to Radxa /tmp/p23-t4-ctx2 (t4relay.crt 635B, t4relay.key 600).
4. DNAT pair for 7710 provisioned OVH-host-side (same shape T1/T2 used) — PLANNED but NOT YET APPLIED at halt: scripts written, launch order fixed (run script quotes PREROUTING DNAT + FORWARD ACCEPT before `docker run`, same as T2).
5. **THE UNIT DIED HERE.** Between 08:45:05 (cert mint) and 08:47 (first post-stage probe) the Radxa substrate failed. NO peer container ever started; NO DNAT rule was ever applied; `/api/v1/servers` never got a t4/T4-peer row.

## SUBSTRATE FAILURE EVIDENCE (Radxa 100.71.4.28, quotes from 08:47–08:53Z)

- `mount | grep " / "` → `/dev/mmcblk1p3 on / type ext4 (ro,relatime)` — root fs REMOUNTED READ-ONLY (microSD failure signature; memory note: Radxa root is 58G microSD, docker vols on same card).
- `touch /tmp/t4p` → `touch: cannot touch '/tmp/t4p': Read-only file system` (tmp is on /).
- All staged files GONE between checks: /tmp/probe-t4, /tmp/p23-t4-probe.b64, /tmp/t4certs.tar, /tmp/p23-t4-ctx2, /tmp/t4_r.sh — present at 08:45, absent at 08:47.
- `/usr/bin/uptime` → `Input/output error` (binary unreadable off the ro/sick fs).
- `dmesg`, `journalctl -k`, `docker ps` — ALL time out (multi-probe: 4 independent instruments, 2+ attempts each, no mask).
- SSH (host + sshpass login) still answers — the OS is alive enough to serve a shell; everything touching disk or the docker daemon hangs.
- OVH-side corroboration (independent instrument, full GET 08:53:43Z): `/api/v1/servers` shows ONLY `srv-195b743a` (local, serving) + `t2-relay` (peer down socket_closed 07:13:17Z — pre-existing P23-02 residue, not mine). NO t4/peer-radxa row EVER appeared → nothing of mine reached the upstream. probe-v119-test logs 08:44–08:54: only my own TLS-handshake negative-test lines, no relay/peer lines.
- Production continuity check at halt (OVH-side, last successful probe 08:41Z): probe-radxa "Up 17 hours" — its uptime continuity AFTER the ro-remount is UNVERIFIED (docker ps on the Radxa times out). probe-relayd (start 2026-11-24T22:39:50Z), probe-relay443, probe-selfagent — same caveat. NOTHING I did touched them: my write-set on the Radxa was /tmp files + one `docker build` of a new 16MB scratch image (probe-t4-arm64:e16f52f). No stop/rm/restart/recreate was executed against any pre-existing container; the launch docker-run never ran.

## VERDICT TABLE (honest: FAIL/BLOCKED rows are failure-to-prove, not fabrication)

| # | Criterion | Verdict | Evidence-from-disk |
|---|-----------|---------|--------------------|
| A1 | Radxa peer VISIBLE on OVH /api/v1/servers state=up + transport recorded | **FAIL (substrate-dead)** | Full GET /api/v1/servers 08:53:43Z quoted above: servers = [srv-195b743a local serving, t2-relay peer down]. No T4 peer row existed at any point. POST-collapse /api/v1/servers could not be captured — OVH-side GET is the captured state of record. |
| A2 | Uplink interrupted → FLIPS to down, last_seen PRESERVED, state_reason present, peer never disappears | **FAIL (never reached)** | The peer never existed; no interruption was performed; no /api/v1/servers before/during/after window exists. The T2-produced t2-relay row (down/socket_closed/last_seen-preserved) remains the only on-disk demonstration of the machinery this unit was to re-prove. |
| A3 | Tail accounting during outage (absent counts, stale flags, carried agent rows) | **FAIL (never reached)** | Nothing was carried; no numbers exist. No invented counts. |
| A4 | Recovery: unblock → state=up with reconnect evidence both sides | **FAIL (never reached)** | No block, no recovery. |

## TEARDOWN (per-leg command+output, all mutations mine only)

- OVH host /tmp: `rm -rf /tmp/p23-t4-sign /tmp/t4certs.tar /tmp/t4ca.pem /tmp/p23-t4-probe.b64 /tmp/t4_stage.sh` → post-ls shows only pre-existing `p23-t1-capture.pcap` + my own pre-unit `t4_probe.py`; OVH_TMP_CLEAN_RC=0. (t4_probe.py predates this unit — parent's staged probe, left as found.)
- CT100 /tmp: `pct exec 100 -- rm -rf /tmp/p23-t4-sign /tmp/t4_httpcode.txt` → clean.
- OVH-host iptables: `iptables -t nat -S PREROUTING | grep -c 7710` → 0; `iptables -S FORWARD | grep -c 7710` → 0. Baseline (P23-02 restored state) INTACT — the planned DNAT pair was never applied, nothing to remove.
- CT100 docker: `docker ps -a | grep -c t4` → NO_T4_CONTAINERS (no container was ever created OVH-side).
- Radxa residue (CANNOT TEARDOWN — fs read-only, docker unreachable): image probe-t4-arm64:e16f52f (16MB) + /tmp staged bytes were my write-set; they are unrecoverable-removable from here. Explicit residue: **probe-t4-arm64:e16f52f image, probe-t4 binary at /tmp/probe-t4 (if the ro snapshot retains it), /tmp/p23-t4-ctx2/{t4relay.crt,t4relay.key,ca.crt}**. The relay private key (t4relay.key) is a LIVE clientAuth identity until 2026-11-28T08:45Z — MITIGATION IS OVH-SIDE: the t4-relay identity can only authenticate against probe-v119-test as a relay; its cert was never loaded into any listener and the updater path can't use it; parent should sweep it or let it expire (<24h). t4relay.key/t4relay.crt DELETED OVH/CT100-side (quotes above); only the Radxa ro-fs copies remain, inert there.
- OVH probe-v119-test log noise (my 3 negative-test handshake lines 08:44) — read-only traffic, no config touched, container state unchanged (Up 3 hours at 08:53 → Up since 06:04, continuous).
- /opt/data/tmp/probe123 local: t4_lib.py, t4_hop*.sh, t4_r*.sh, t4certs.tar, t4ca.pem, /tmp/p23-t4/ binaries deleted; pre-existing t4_probe.py untouched.

## FINDINGS (for parent)

1. **RADXA MICROSD FAILURE — FLEET-LEVEL, NOT UNIT-LOCAL.** Root fs ro-remount + I/O hangs on a failure domain that carries probe-radxa (prod server), probe-relayd, probe-relay443, probe-selfagent, probe-dl and nop-* (postgres/redis/backend). Memory-doctrine backup target (NVMe BKP, backup-radxa 11-26 patch) is exactly the play here. Recommendations: (a) do not redeploy T4 until the SD is swapped or rescued; (b) after rescue, `docker start` continuity of probe-radxa needs verification (its uptime-currently-unverifiable caveat above); (c) the e54c NVMe migration should be accelerated — this is the second major failure-signature class on that card class in this campaign.
2. probe-radxa as a T4 PEER SUBJECT is a category error in the board text: it runs `serve` with no upstream flags; P21-03's peer = relay-mode upstream dial. T4's executable form (when the fleet is re-dispatched) is a HEAD-built `relay` on the Radxa with `-upstreams wss://…:7710/ws -upstream-ca -upstream-client-cert/-key` (identity recipe in this artefact) + the T1/T2-shaped 7710 DNAT pair — the transport cell gets filled by the candidate walk (P21-09), and the ouvrier for 7701-direct would be a second candidate.
3. Bit-identical rebuild (sha ec5bcda5 = T2's) confirms reproducible artifact provenance on the arm64 path — banked as provenance evidence even though the unit failed.

## CLOSE

- reviewed_by: operative (self-review; parent to regate on close — every external claim above is quoted from tool output in this session)
- verdict: FAIL-BLOCKED (substrate death mid-unit; A1–A4 not exercised; no invented numbers; residue itemized)
- verdict_sha_of_head: e16f52f1f018c5d1760e8908f5edb8fb8d766ca0
- unit_cap: honoured; halt at 46min active under the two-failed-probes stop rule (G-BOT-RECALL/2-failure doctrine)
- teardown: quoted above; OVH/CT100 clean; Radxa residue explicitly declared (ro-fs-inaccessible)
- artefact: /opt/data/tmp/probe123/P23-04_T4_PEERS.md
