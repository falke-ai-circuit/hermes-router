# M51-10 — deploy afdd9b1 (seam deadline fix) to the 11 profile plugin trees

close header

unit: M51-10
owner: operative (deploy)
date: 2026-11-16
repo: /opt/data/plugins/hermes_router
head: afdd9b140ea33ba87b349bd33c26388a13f3b430 (main)
artefact: /opt/data/tmp/r78u/M51-10_DEPLOY.md
research_leg: NONE
policy_change: none — no model, routing, reasoning or refusal key touched
reviewed_by: none dispatched (mechanical deploy unit, no review leg in the brief)
verdict: WAIVED — no reviewer round has run; bytes deployed 11/11, zero gateways restarted (see §3)
verdict_sha_of_head: afdd9b140ea33ba87b349bd33c26388a13f3b430
suite: not run by this unit (research_leg NONE, no code change in repo; A4 smoke read explicitly out of scope)

## 1. Bytes deployed — A1 PASS 11/11

Source: worktree of `/opt/data/plugins/hermes_router` at HEAD afdd9b1, verified before install:

    core/turn_provenance.py   md5 38436a694fd4347857f28857690e3afe
    completion_audit.py       md5 f35a15eab7c8ae8d6bdfe15a2ecc8b3c

Install: `install -m 644 <repo file> <profile tree file>` per profile, `__pycache__` purged after
install so stale bytecode cannot win. Every target tree's two files were copied aside first to
`/opt/data/tmp/r78u/backup-20261116T130900Z/<profile>/`. Re-running is a no-op (same bytes, same mode).

Pre-state measured on all 11 trees (not just the 6 in the brief): `core/turn_provenance.py` md5 was
`6e9e546cb429c6e656de40445298f8a1` and `completion_audit.py` md5 was `899330ccff6991612fae1dcebd779c8b`
everywhere — so the pre-fix bytes were fleet-wide, as the brief asserted.

| profile | md5 before | md5 after | completion_audit after | mtime (UTC) | gateway pid | process start (UTC) | start>mtime | restarted |
|---|---|---|---|---|---|---|---|---|
| analyst | 6e9e546cb429c6e656de40445298f8a1 | 38436a694fd4347857f28857690e3afe | f35a15eab7c8ae8d6bdfe15a2ecc8b3c | 2026-11-16 13:09:00.669724943 +0000 | 542765 | Mon Nov 16 11:37:28 2026 | no | no |
| architect | 6e9e546cb429c6e656de40445298f8a1 | 38436a694fd4347857f28857690e3afe | f35a15eab7c8ae8d6bdfe15a2ecc8b3c | 2026-11-16 13:09:01.175743077 +0000 | 542857 | Mon Nov 16 11:37:31 2026 | no | no |
| coder | 6e9e546cb429c6e656de40445298f8a1 | 38436a694fd4347857f28857690e3afe | f35a15eab7c8ae8d6bdfe15a2ecc8b3c | 2026-11-16 13:09:01.795765296 +0000 | 542753 | Mon Nov 16 11:37:27 2026 | no | no |
| conductor | 6e9e546cb429c6e656de40445298f8a1 | 38436a694fd4347857f28857690e3afe | f35a15eab7c8ae8d6bdfe15a2ecc8b3c | 2026-11-16 13:09:02.729798769 +0000 | 543410 | Mon Nov 16 11:39:50 2026 | no | no |
| evol | 6e9e546cb429c6e656de40445298f8a1 | 38436a694fd4347857f28857690e3afe | f35a15eab7c8ae8d6bdfe15a2ecc8b3c | 2026-11-16 13:09:02.781800632 +0000 | 542769 | Mon Nov 16 11:37:28 2026 | no | no |
| operative | 6e9e546cb429c6e656de40445298f8a1 | 38436a694fd4347857f28857690e3afe | f35a15eab7c8ae8d6bdfe15a2ecc8b3c | 2026-11-16 13:09:03.425823712 +0000 | 542853 | Mon Nov 16 11:37:31 2026 | no | no |
| orchestrator | 6e9e546cb429c6e656de40445298f8a1 | 38436a694fd4347857f28857690e3afe | f35a15eab7c8ae8d6bdfe15a2ecc8b3c | 2026-11-16 13:09:04.007844569 +0000 | 542761 | Mon Nov 16 11:37:27 2026 | no | no |
| researcher | 6e9e546cb429c6e656de40445298f8a1 | 38436a694fd4347857f28857690e3afe | f35a15eab7c8ae8d6bdfe15a2ecc8b3c | 2026-11-16 13:09:04.400858653 +0000 | 542871 | Mon Nov 16 11:37:31 2026 | no | no |
| reviewer | 6e9e546cb429c6e656de40445298f8a1 | 38436a694fd4347857f28857690e3afe | f35a15eab7c8ae8d6bdfe15a2ecc8b3c | 2026-11-16 13:09:04.678868616 +0000 | 542757 | Mon Nov 16 11:37:27 2026 | no | no |
| shadow | 6e9e546cb429c6e656de40445298f8a1 | 38436a694fd4347857f28857690e3afe | f35a15eab7c8ae8d6bdfe15a2ecc8b3c | 2026-11-16 13:09:05.038881518 +0000 | 542793 | Mon Nov 16 11:37:28 2026 | no | no |
| valmet | 6e9e546cb429c6e656de40445298f8a1 | 38436a694fd4347857f28857690e3afe | f35a15eab7c8ae8d6bdfe15a2ecc8b3c | 2026-11-16 13:09:05.568900512 +0000 | 542885 | Mon Nov 16 11:37:31 2026 | no | no |

A1: `core/turn_provenance.py` = `38436a694fd4347857f28857690e3afe` on 11/11 deployed profiles. PASS.
`completion_audit.py` matched the repo's `f35a15eab7c8ae8d6bdfe15a2ecc8b3c` on 11/11 too.

## 2. Skipped trees (deliberate)

- `recovery` — no gateway service (`/run/service/gateway-recovery` absent). Not deployed.
- `m3629canary` — s6 slot down (known pre-existing ownership condition). Not deployed.

Neither serves a live gateway, matching the brief's instruction.

## 3. Gateways restarted — NONE

A2 is vacuous here: zero gateways were restarted, so no process start time can be checked against
file mtime. Every `start>mtime` cell above reads `no` for that reason, not because a restart was
missed. Restarts were withheld deliberately:

- This unit runs inside the `operative` gateway's own turn. `s6-svc -r /run/service/gateway-operative`
  would kill the turn that is performing the deploy, so own-slot bounce was not attempted.
- Every other profile is a PEER gateway. Per the FLEET RESTART PROTOCOL in this unit's brief, a peer's
  gateway is bounced only with conductor's authorisation; none was held, and the brief instructs a
  worker to stop and hand back rather than assemble a fleet-wide restart.
- `fleet_preflight.py` was therefore not needed and no gateway was touched: 11/11 stay at their
  pre-deploy pids (start times 11:37-11:39 today, all BEFORE the 13:09 mtimes).

Consequence, stated plainly: the fix is on disk on all 11 trees but is NOT loaded in any running
process. Live behaviour is unchanged until each gateway restarts.

## 4. Repo state — A3 PASS

`git status --porcelain` in `/opt/data/plugins/hermes_router` is empty (deploy wrote only to profile
trees, never to the source). HEAD at deploy time: afdd9b140ea33ba87b349bd33c26388a13f3b430.
This artefact is additionally committed as `docs/M51-10_DEPLOY.md`; the code tree is untouched by it.

## 5. Not claimed

A4: no live smoke read was taken and none is claimed. No `consult_gate`/seam row observed on any
profile after this deploy. The after-histogram belongs to M51-05's soak.

## 6. Open items

1. **Bounces unowned.** 11 gateways must each be restarted (one at a time, own-slot or conductor-
   authorised) before the afdd9b1 bytes take effect. Until then M51-05's 48h soak on valmet would
   still measure pre-fix behaviour — valmet needs its gateway bounced first.
2. **M51-05 unblock condition:** valmet gateway restart, then confirm its process start > 13:09:05
   mtime and that `core/turn_provenance.py` still hashes `38436a694fd4347857f28857690e3afe` after boot.
3. Rollback: reinstall the two files from `/opt/data/tmp/r78u/backup-20261116T130900Z/<profile>/`
   (pre-fix md5s `6e9e546c…` / `899330cc…`) and restart the same way.
