# P23-04 — CLOSE (E2E T4 server-to-server peer; parent reviews this FAIL-BLOCKED record after Radxa substrate death)

**Commit:** (this commit — document-only, artefact + UNIT json; no code change)
**Bind:** v119wt @ e16f52f1f018c5d1760e8908f5edb8fb8d766ca0 (the tree the probes ran against; binary rebuilt from it sha-verified ec5bcda5 = T2's bit-identical)

reviewed_by: parent-of-record (this close-side; child self-review recorded in artefact)
verdict: FAIL-BLOCKED
verdict_sha_of_head: e16f52f1f018c5d1760e8908f5edb8fb8d766ca0

## What happened (one paragraph)

Unit set out to prove a Radxa-hosted peer on OVH /api/v1/servers with an up→down→up cycle. Binary was rebuilt bit-identical to T2's (ec5bcda5), shipped and verified on the Radxa, scratch image probe-t4-arm64:e16f52f built there, relay identity minted from the canonical runtime CA (CN=t4-relay, serial …FE08, exp 2026-11-28T08:45Z). Before launch, the Radxa substrate died: root fs /dev/mmcblk1p3 remounted read-only, all staged files vanished, /usr/bin/uptime returns Input/output error, and docker ps/dmesg/journalctl all time out (multi-probe, 4 instruments). No peer container was ever started; no DNAT was applied; /api/v1/servers never carried a T4 row (full GET quoted 08:53:43Z: only srv-195b743a + pre-existing t2-relay down). A1–A4: not exercised — the verdict table records FAIL-BLOCKED with the captured evidence, no numbers invented.

## What the parent needs to act on

1. RADXA MICROSD FAILURE is fleet-level: the same failure domain carries probe-radxa (prod probe server), probe-relayd, probe-relay443, probe-selfagent, probe-dl, nop-*. Post-rescue, probe-radxa's continuity needs re-verification; NVMe BKP path (backup-radxa, 11-26 patch) is the mitigations lane.
2. Radxa residue declared (inert behind ro fs): image probe-t4-arm64:e16f52f, /tmp staged bytes, t4relay clientAuth cert/key (expire <24h; deleted OVH/CT100-side, quoted; cannot authenticate anywhere as-is).
3. Board-text correction banked in artefact: probe-radxa runs `serve` with no upstream flags — the T4 peer subject when re-dispatched is a HEAD-built `relay` on the Radxa with the 7710 TLS+mTLS candidate (or a temporary 7701 DNAT pair).
4. Re-dispatch recipe (full, in artefact §FINDINGS): relay -upstreams wss://139.99.209.235:7710/ws -upstream-ca -upstream-client-cert/-key, DNAT pair per T1/T2 shape, quoted in/out.

## Verification of this close

- /opt/data/tmp/probe123/P23-04_T4_PEERS.md (10457 B) — artefact with contract lines, verdict table, quotes, per-leg teardown.
- /opt/data/tmp/probe123/P23-04_UNIT.json — unit record, verdict FAIL-BLOCKED, bind sha e16f52f.
- child_close_verify.py --unit P23-04 exit 0 quoted in transcript at close time.
