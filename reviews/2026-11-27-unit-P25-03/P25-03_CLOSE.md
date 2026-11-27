# P25-03 — CLOSE (OVH side-by-side: v1.19.3 on CT100:7702 beside prod v1.19.2:7701; parent re-gate)

**Commit:** ddb772a0c4260a73bb222ee0f7731eb74dc7a97f
**Bind:** hermes_router reviews record chain (14eed1b close pointer + artefact commit ebb2483 per header rebind ddb772a) — the unit is an ops/verif unit with no v119wt source change; the image it deployed IS the product artefact (v1.19.3, rev a67c612d51d78dcd2adf433cb42bf1bea96bd3a4).

reviewed_by: parent-of-record (child self-review in artefact; parent re-verified the load-bearing claims live this turn)
verdict: PASS
verdict_note: child verdict PASS maps 1:1 onto the close vocabulary; no translation needed. All four acceptance rows evidenced-from-disk.
verdict_sha_of_head: ddb772a0c4260a73bb222ee0f7731eb74dc7a97f

## Parent re-verification (fresh commands this close turn, 12:0xZ)

1. Prod untouched: `docker inspect operator-op01-probe-1` → StartedAt **2026-11-26T15:12:51.236801629Z** — byte-identical to the child's before/after quotes; health GET 7701 → `{"active_agents":4,"server_version":"v1.19.2",...,"status":"ok"}`. The 20h-old production container never restarted. A2 PARENT-CONFIRMED.
2. New container live: `docker inspect probe-v1193-test` → running, StartedAt 2026-11-27T11:46:54.775751867Z, port 0.0.0.0:7702→7701. Image labels VER=1.19.3 REV=a67c612d51d7 (the P23-12-fix sha). A1 PARENT-CONFIRMED (mTLS health GET on 7702 → `server_version":"v1.19.3"`, quoted in transcript).
3. Separation: runtime-1193 vs prod runtime bind mounts distinct (artefact Mounts quotes + this turn's ports/labels re-read); the child's independent-drift evidence (registry heartbeats diverging after the clone point) is decisive and побorbit corroborated by the two health outputs showing different agent populations (4 vs 0). A3 PARENT-CONFIRMED.
4. Teardown: `iptables -t nat -S PREROUTING | grep -c 7702` → **0** (parent re-ran; temp DNAT gone). Client-side dirs deleted per quotes; probe-v1193-test LEFT UP as intended residue with a named owner (P25-04's OVH host). A4 PARENT-CONFIRMED.
5. Orthogonal corroboration properly labelled: the child's early-optional e2e dial landed on the DEPLOYED v1.19.3 (agent connected with E2E on, zero "expected relay registration" lines) — corroborates P23-12 on the actually-deployed digest, supplement to (not substitute for) P23-12's own close.
6. Extra flag-parity break caught and typed by the child (--allow-disabled-token-ttl now mandatory with --token-ttl 0 at this head; surfaced as DEVIATIONS 1 with a clean recreate) — a real delta of the 1.19.3 release line, recorded here so P25-04's brief carries it.

## Record

- Record chain: 14eed1b (close pointer) -> ddb772a (header sha-of-head rebind), committed into the fleet's close-chain repo reviews dir; artefact /opt/data/tmp/probe125/P25-03_OVH_SBS.md sha256 60d5f5e243f5 = watch-ledger art. No product commit — the deployed image (v1.19.3, CI run 45990578298 digest) is the artefact.
- Suite: n/a (ops/verif unit; the parent's fresh checks quoted above).

## Chain state after this close

- probe-v1193-test (v1.19.3, 7702, runtime-1193): UP, intended residue, owner P25-04 OVH leg — CARRY INTO P25-04's brief verbatim.
- Still fenced behind the Radxa e54c recovery (banners 13502/13504): P23-03/05/06/08, six T7 relay cells, P25-02 Radxa half, P25-04's Radxa leg.
- Next legal driver after this close: P25-08's OVH-side cells if split explicitly (the Radxa cells stay DEFERRED) — arm only with that split stated on the unit text; otherwise hold.
