# P23-07 — CLOSE (E2E T7 encryption falsification, OVH/T1-scope; parent-authored after child hit the iteration cap post-delivery)

**Commit:** 7b6afcae4766 (hermes_router reviews pointer — the unit's no-code-change commit; NOT a v119wt product sha. Full: git rev-parse in that repo)
**Bind:** v119wt @ e16f52f1f018c5d1760e8908f5edb8fb8d766ca0 (the tree the probes ran against; falsification is live-infrastructure, no source change — reviews-pointer commit is the unit record, NOT repo product code)

reviewed_by: parent-of-record (child self-review recorded in artefact; this close = parent re-gate + the commit the cap swallowed)
verdict: FIX-FIRST
verdict_note: mechanical close-gate translation of the child's own verdict shape `PASS on C3/C4/C7, FAIL on C6` — C6's typed rotation-instability finding is a real defect surface (the next dispatch's deliverable), so the unit closes on a FIX-FIRST following the P18-03 falsifier-gate-flip pattern; per-check results stand verbatim in the body/artefact.
verdict_sha_of_head: e16f52f1f018c5d1760e8908f5edb8fb8d766ca0

## Child evidence the parent verified before writing this close

- Iteration-cap shape (documented variant): forced no-tools summary at row 63544 states delivery COMPLETE + `child_close_verify.py` NOT CLOSABLE on the missing commit only — the documented "forced-summary = fix the writes, never re-brief" case.
- Artefact on disk sha256 03243f2591d008ec (== watch-ledger art) — content read in full by the parent: C3/C4/C7 PASS with quoted server-log refusals + grep-count evidence; C6 FAIL typed (21/22 dials 1006 `expected relay registration on channel 0, got channel <random>`, 1 registration closed in 4s) — honest census, no invented numbers.
- `child_close_verify.py --unit P23-07 --artefact ... --repo v119wt` re-run by parent this turn: reproduces the single NOT-CLOSABLE item (no commit mentioning P23-07 in last 15) — matches the child's final row exactly. Nothing else missing.

## What this close adds (the swallowed writes)

1. Artefact pointer committed under hermes_router `reviews/2026-11-27-unit-P23-07/` (the P23-0x close-chain pattern the child itself documented as its repo convention in P23-02).
2. This CLOSE document + the registry/ledger close rows.
3. Booking note: the "no commit in v119wt" verifier leg is SATISFIED by a no-code-change unit's pointer commit, not by inventing product-code provenance — the artefact names zero source deltas and the re-run at e16f52f confirms the tree untouched.

## Re-dispatch payload (the finding is the deliverable)

- C6 defect: agent hello first-frame negotiation at HEAD e16f52f unstable — 101 `expected relay registration on channel 0, got channel <random>` server-side vs 1 `agent connected` (09:40:13, closed 4s) in the 09:25-09:45 window; 21/22 dials 1006-unexpected-EOF. Rotation endpoint `/api/v1/credentials/agents/{id}/rotate` exists, enforces live transport, typed AGENT_NOT_CONNECTED on dead id — correct; the bringup it guards against is what never holds.
- Next unit shape: falsify the handshake instability itself (file:line ownership of the `expected relay registration` path at e16f52f; repro outside the live server if needed), then carry the rotate roundtrip as its acceptance — the P18-03 gate-flip contract applies (C6's pending flip rides the fix's re-run).
- Radxa fence unchanged: six relay-host cells stay DEFERRED behind the owner recovery decision (banner 13502).

## Residue / teardown inherits the child's quotes verbatim (artefact TEARDOWN section): iptables 0/0 restored, /tmp/p23-t7 + Hetzner keys deleted, no server restart, production untouched. /tmp/p23-t1-sign stands per P23-01 (certs expire 2026-11-28).
