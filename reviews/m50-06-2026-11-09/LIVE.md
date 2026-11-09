# M50-06 — LIVE acceptance for M50 (canary: architect, gateway 8650)

close header
unit: M50-06
owner: operative (rollout + measurement)
date: 2026-11-09
repo: /opt/data/plugins/hermes_router
head: c5e09ec568023ef37a16fcf392677fba414a8c2f (main)
canary: architect (profile tree /opt/data/profiles/architect/plugins/hermes_router)
reviewed_by: none dispatched (self-measured unit; no review leg in this brief)
verdict: M50 CLOSABLE on the retry mechanism (executed live, 4/7 recovered); residual failure reported, not fixed
verdict_sha_of_head: c5e09ec568023ef37a16fcf392677fba414a8c2f

## 1. Deployed bytes vs HEAD

Before this unit: every profile plugin tree was at c0ff779 (files mtime 2026-11-09 08:56:41),
i.e. BEFORE M50-02 (bb2cc0f 17:24:06) — the retry code was not on disk anywhere.

| module | deployed md5 (pre) | HEAD md5 | verdict |
|---|---|---|---|
| anchor_exec.py | 1da5b6f1e85d53775a7ea4d8d3fedcec | 52c3ea9d707becf7c5af85e2367f996e | MISMATCH — stale (c0ff779) |
| gate/orchestration.py | 2f3cd434de9214e7a23e196c44d978a2 | aeda93141e9f3f78c5844ab741660c88 | MISMATCH — stale |
| passes/dispatcher_pre.py | bf1dccb4190f95ade7432449a2ee58bd | 7f356298c8a97df48b8b9c423d5cc069 | MISMATCH — stale |

Rollout performed (owner lane = rollout + measurement, no product edit):
`bash scripts/install.sh --hermes-home /opt/data/profiles/architect` → installed 356 files.
Re-hash after install: all three modules MATCH HEAD (verified above, same run).
Gateway: old pid 171729 (started 12:30:02) loaded the stale tree; bounced with
`/command/s6-svc -r /run/service/gateway-architect` (not this session's gateway) → pid 200569,
`/health` 200, boot at 18:36:51. Bytes and process now both at HEAD.

## 2. Real turns driven through the live path

Driver: POST /api/sessions + /api/sessions/{sid}/chat on 127.0.0.1:8650 (architect's own
gateway, own config, own anchor chain). 11 turns attempted (10 driven + 1 calibration).

Anchor consults fired: 7 of 11 turns (4 turns did not reach the consult gate).

Per consult (router log anchor_route_fired / route_skipped + agent.log anchor_exec lines):

| # | session | fired reason | retry | outcome | body chars |
|---|---|---|---|---|---|
| 1 | api_1794249442_f5ceb0ff | risk_r2 | 23585->47170 | retry_empty -> failover_skipped -> route_skipped | 0 |
| 2 | api_1794249537_892d941c | risk_r2 | 23585->47170 | retry_recovered, delivered | 6277 |
| 3 | api_1794249576_554278ad | complexity_orientation | 23585->47170 | retry_empty -> failover_skipped -> route_skipped | 0 |
| 4 | api_1794249660_7bbb8596 | risk_r2 | 23585->47170 | retry_recovered, delivered | 5605 |
| 5 | api_1794249876_130ff848 | risk_r2 | 23585->47170 | retry_recovered, delivered | 5344 |
| 6 | api_1794249960_eaded9a4 | risk_r2 | 23585->47170 | retry_empty -> failover_skipped -> route_skipped | 0 |
| 7 | api_1794249963_203fbc37 | risk_r2 | 23585->47170 | retry_recovered, delivered | 5662 |

## 3. Counts from the profile's own logs

Two lanes (this is a finding, not a convenience): the retry strings are emitted by the
module logger (`hermes_plugins.hermes_router.anchor_exec`) into `logs/agent.log`, NOT into
the configured `hermes_router.log_path`
(`/opt/data/profiles/architect/uncensored-router.log` = route events only).
Counting `anchor_budget_retry*` in log_path returns 0 even when the retry ran — the same
file-lane mismatch that produced the reviewer's "0 across 74 profile logs".

Window after restart (2026-11-09 18:36:00 → 18:56, agent.log):

| counter | before 18:36 (today, same file) | after |
|---|---|---|
| anchor_budget_retry (paid) | 0 | 7 |
| anchor_budget_retry_recovered | 0 | 4 |
| anchor_budget_retry_empty | 0 | 3 |
| anchor_budget_retry_skipped (ceiling) | 0 | 0 |
| anchor_consult_failover_skipped | 0 | 3 |
| anchor_consult_failover (walk) | 0 | 0 |
| anchor_consult_minimal_retry | 28 (cumulative, older) | 0 |
| anchor_route_failed reason=empty_response | 27 | 7 |

Rotated logs agent.log.1/.2/.3 (2026-10-07 → 2026-11-07): 0 anchor_budget_retry lines,
29 empty_response — the retry had never executed before this unit.

## 4. Rate and mechanism

Mechanism (named, from the live lines): reasoning_effort=max is charged to the same
max_tokens allowance. First call returns finish=length, reasoning exhausted the budget,
content empty (`reasoning_chars=0`, max_tokens=23585, n_msgs=2). M50-02 raises the cap to
47170 and keeps the SAME reasoning setting (keep_reasoning=True) — 4 of 7 then returned a
body (6785 / 6336 / 6073 / 6262 chars raw; 6277 / 5605 / 5344 / 5662 after framing).
On the 3 that stayed empty, M50-04 suppressed the failover walk (`reason=budget_retry`),
so the turn had no advisory and failed visibly (`route_skipped reason=anchored_call_failed`).

- non-empty advisory bodies / consults fired = 4/7 = 57%
- consults that took the retry path = 7/7 = 100%
- non-empty advisory bodies / turns attempted = 4/11 = 36%

## 5. Verdict table

| item | method | command | observed output | verdict |
|---|---|---|---|---|
| deployed bytes == HEAD | md5 compare, 3 modules | `md5sum <deployed> vs git show HEAD:<path> | md5sum` | pre: 3/3 MISMATCH; post-install: 3/3 MATCH | PASS after rollout |
| process carries HEAD bytes | pid vs install time + restart | `stat -c %y`, `s6-svc -r` | pid 171729 stale tree → pid 200569 after restart, /health 200 | PASS |
| retry executes live | count retry lines | `grep -cE 'anchor_budget_retry model=' logs/agent.log` | 7 in window (0 before) | PASS |
| retry recovers content | count recovered + delivered chars | `grep anchor_budget_retry_recovered`; router `anchor_chars` | 4 recovered, delivered 6277/5605/5344/5662 | PASS |
| failover bound holds | count failover lines | `grep anchor_consult_failover` | walk 0, skipped 3 (reason=budget_retry) | PASS |
| lane fully cured | residual empty rate | `grep anchor_route_failed reason=empty_response` | 3/7 consults still empty at 47170, skipped | NOT CLOSED (reported) |
| configured log_path carries retry counters | count in log_path | `grep -c anchor_budget_retry uncensored-router.log` | 0 (strings live in agent.log) | FAIL (instrumentation lane) |
| credit / cap / reasoning changes | scope check | none performed | untouched | COMPLIANT |

## 6. Open items (Goran's decisions, not taken)

1. 3/7 (43%) consults still return empty at 47170 max_tokens with reasoning_effort=max.
   M50-01's root cause is reduced, not eliminated. Options sit in Goran's lane: lower
   effective reasoning for the anchor lane, raise the ceiling, or accept the skip.
2. With the failover walk suppressed after a paid retry (M50-04), those 3 turns deliver no
   advisory at all. Intentional spend bound; its cost is the residual empty rate above.
3. Retry counters are not visible in `hermes_router.log_path`; any future audit must read
   `logs/agent.log` (or the module logger's sink) to see anchor_budget_retry*.

sha at measurement: c5e09ec568023ef37a16fcf392677fba414a8c2f
