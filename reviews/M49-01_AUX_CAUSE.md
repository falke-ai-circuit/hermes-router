# M49-01 — aux_none population: measured cause classification

unit: M49-01 | owner: analyst (measurement, READ-ONLY) | date: 2026-11-10
repos touched: none (product code untouched); artefact only.
reviewed_by: pending (independent reviewer not yet assigned)
verdict: pending
verdict_sha_of_head: 427029a91c518db2b0b7c3001566f6865794e2f2

## 0. Population as measured now (drift vs the recorded 5074)

Command (all 11 live profile route logs + the 12 `/tmp/uncensored-router-*.log` tombstones):

    python3 /opt/data/tmp/r78u/probe1.py
    total aux_none lines (all files, raw): 7163
    distinct (ts,line): 6707
    min 2026-11-02T23:24:08Z max 2026-11-10T09:42:55Z

Cut at the recorded latest timestamp:

    python3 /opt/data/tmp/r78u/probe3.py
    rows <= 11-08T18:24:48Z: 4814  distinct ts+line: 4424
    rows > cut: 2349
    max ts overall: 2026-11-10T09:42:55Z

**Drift: recorded 5074 → measured 4814 at the same cutoff (−260, −5.1%); the live
population is now 7163 raw / 6707 distinct, i.e. +2349 rows since the record.** The
record cannot be reproduced exactly from disk today: route logs carry no `.1`
rotations (`ls -l /opt/data/profiles/*/uncensored-router.log` shows one file per
profile, oldest mtime Nov 10), so a partial truncation cannot be excluded — that is
a stated limit, not a silent gap. All bucket numbers below are over the measured
7163, not the recorded 5074.

Route-log resolution used the convention file, not a guess:

    head -60 /opt/data/profiles/ROUTER_LOG_CONVENTION.md
    Convention: every profile writes `<profile root>/uncensored-router.log`; the /tmp files are tombstones as of the M36-09 repoint (last writes 11:19-11:28Z on 2026-11-08).
    for p in /opt/data/profiles/*/config.yaml; do grep -n 'log_path' $p; done
    analyst:610 ... valmet:630  -> all 11 profiles resolve to /opt/data/profiles/<p>/uncensored-router.log

Per-file rows (`grep -c aux_none ...`): analyst 254, architect 422, coder 535,
conductor 756, evol 108, operative 268, orchestrator 625, researcher 171,
reviewer 1023, shadow 421, valmet 181 (live logs) + tombstones analyst 243,
architect 73, coder 452, operative 278, orchestrator 265, researcher 209,
reviewer 497, valmet 382, conductor/evol/shadow/recovery 0 = 7163.
Excluded files, named: `/tmp/uncensored-router-conductor.log` (last write 2026-10-04),
`/tmp/uncensored-router-evol.log`, `/tmp/uncensored-router-shadow.log`,
`/tmp/uncensored-router-recovery.log` — present but contain 0 aux_none rows;
`/opt/data/profiles/m3629canary/config.yaml:264` points at the coder log, so it is
not a separate population and is not double-counted.

Sampling: **not a sample — full enumeration** of every file that resolves to a
route log. Every row is classified; no estimator is used.

## 1. Per-profile / per-bucket counts (denominator = 7163)

    python3 /opt/data/tmp/r78u/table.py

| profile | rows | breaker_limiter(silent) | dispatch_no_cause | no_field | other_test_2000ms |
|---|---|---|---|---|---|
| analyst | 497 | 345 | 11 | 141 | 0 |
| architect | 495 | 259 | 14 | 221 | 1 |
| coder | 987 | 516 | 15 | 280 | 176 |
| conductor | 756 | 416 | 25 | 269 | 46 |
| evol | 108 | 42 | 0 | 66 | 0 |
| operative | 546 | 328 | 9 | 192 | 17 |
| orchestrator | 890 | 521 | 31 | 281 | 57 |
| researcher | 380 | 182 | 8 | 190 | 0 |
| reviewer | 1520 | 575 | 25 | 401 | 519 |
| shadow | 421 | 207 | 4 | 210 | 0 |
| valmet | 563 | 350 | 18 | 195 | 0 |
| **TOTAL** | **7163** | **3741** | **160** | **2446** | **816** |

Bucket definitions are latency-measured, not assumed. The gate row carries
`latency_ms` written as wall time around `evaluate()` (`domain_gate.py:539-541`,
`res["latency_ms"] = int((time.monotonic() - t0) * 1000)`), so a 1-2 ms row cannot
have dispatched a network aux call:

| bucket | rule | n | share |
|---|---|---|---|
| circuit breaker / limiter (silent) | `latency_ms <= 10` | 3741 | 52.2% |
| not_instrumented (no field) | event shape carries no `latency_ms` (delivery_decision, intent_judge_*, lane_decision_deferred, domain_gate_turn) | 2446 | 34.2% |
| not_instrumented (dispatch, no cause) | `latency_ms > 10`, no joinable failure line | 160 | 2.2% |
| other: test-harness replay | `latency_ms == 2000`, non-`api_` session | 816 | 11.4% |
| 402 / quota | none measured on aux_none rows | **0** | 0 |
| model-not-found | none measured on aux_none rows | **0** | 0 |
| timeout | none measured on aux_none rows | **0** | 0 |
| empty response (finish=length/stop) | none joinable | **0** | 0 |

`latency_ms` histogram over the 4717 rows that have the field
(`python3 /opt/data/tmp/r78u/one.py` family): `<=10ms` 3741, `11-1989ms` 113,
`1990-2100ms` 816, `>2100ms` 26.

Session split (why the 2000 ms block is not a production cause) —
`python3 /opt/data/tmp/r78u/sk.py`:

    inst {'api': 2896, 'test_s': 57, 'dated': 708, 'other': 80} total 3741
    mid  {'api': 92, 'dated': 42} total 134
    2000 {'test_s': 314, 'other': 502} total 816
    long {'api': 21, 'test_s': 2, 'other': 2, 'dated': 1} total 26
    no_lat {'api': 1926, 'test_s': 211, 'other': 63, 'dated': 246} total 2446

Zero `api_` rows in the 2000 ms block: those are replay legs (`s-leg4`, `s1`,
`sess-42`, `m47-14-live-*`), fixed 2000 ms, with no matching failure line.

## 2. What the aux lane actually failed with (cause instrument, fleet-level)

`aux_none` itself is cause-blind — it is set at `domain_gate.py:450` purely because
`aux_raw_call` returned falsy, and that return carries no reason. The only cause
instrument on disk is `hermes_aux_failed` in the per-profile `logs/errors.log*`:

    grep -h 'hermes_aux_failed' /opt/data/profiles/*/logs/errors.log* | sed -E 's/.*reason=([a-z_]+).*/\1/' | sort | uniq -c
    932 empty_content / 851 exception   (all rotations)
    python3 /opt/data/tmp/r78u/table.py -> AUXFAIL_MIX
    {'timeout': 258, 'model_not_found': 578, 'empty_response': 936, 'quota_window': 5, 'other': 7, '429': 3} 1787

But that instrument does **not** join to the aux_none rows. Test:

    python3 /opt/data/tmp/r78u/join.py
    api/dispatched rows tested: 160 joined: 0 {'not_instrumented': 160}

160 rows that provably dispatched an aux call have no `hermes_aux_failed` line within
`[-600s, +60s]` in the same profile. Join rate 0/160 = the two streams cannot be
paired per row (different writers/process lifetimes), so a per-row 402 / 404 /
timeout / empty split **cannot be produced from this substrate** — UNPROVEN, and it
is reported as such rather than estimated.

## 3. The two recorded historical causes: LIVE or dead

| cause | first/last measured evidence | verdict |
|---|---|---|
| `anchor_route_failed` with 402 | present on 2026-10-29 (`2026-10-29 10:07:50 ... Error code: 402`), last occurrence **2026-11-09 01:18:12**; 20 such lines, dated 10-29 ×1, 10-31 ×1, 11-03 ×6, 11-05 ×3, 11-06 ×5, 11-08 ×1, 11-09 ×3 | **LIVE** (last 2026-11-09T01:18:12Z) |
| `hermes_aux_failed` | 13 rows on 2026-10-06; last fleet occurrence **2026-11-10 09:55:18** (`reason=empty_content`) | **LIVE** (last 2026-11-10T09:55:18Z) |

    grep -h '402' .../errors.log* | grep 'anchor_route_failed' | grep -o '^[0-9-]* [0-9:]*\|Error code: 402'
    2026-10-29 10:07:50  Error code: 402   ...   2026-11-09 01:18:12  Error code: 402
    grep -h '^2026-10-06' .../errors.log* | grep -c 'hermes_aux_failed'   -> 13
    grep -h 'hermes_aux_failed' .../errors.log* | sort | tail -1
    2026-11-10 09:55:18,635 ERROR ... hermes_aux_failed reason=empty_content

Neither is dead, and neither is the dominant aux_none cause: 402 appears only on the
*anchor* lane (`anchor_route_failed`), not on the rows under study (0/7163).

## 4. Highest-volume cause and the fix it implies

The single highest-volume cause is **silent non-dispatch: 3741 of 7163 rows (52.2%)
return `aux_none` in ≤10 ms, so `aux_raw_call` returned None without ever making the
call.** The breaker is not the mechanism — `python3 /opt/data/tmp/r78u/brk.py` reports
`instant rows within 600s of a breaker_opened: 0  outside: 3741` against 29 logged
`semantic_aux_breaker_opened` lines, and the instant rows are instant from the first
row of each busy hour, so it is not "cap consumed then silent" either. What remains in
the code path is the rolling-hour call budget (`semantic_classifier.py:61`
`DEFAULT_CALLS_PER_HOUR = 20`, now configured `aux_calls_per_hour: 200` in every
profile's `hermes_router.classification`) returning None with no log line at all: the
aux token ledger pins at exactly the budget value in saturated hours (architect and
coder both hit exactly 200 in 2026-11-09 12; earlier days pin at exactly 20 —
`python3 /opt/data/tmp/r78u/cap.py` → 132 profile-hours sitting on a cap value carrying
2404 instant rows), i.e. the gate calls one aux request per model turn and the budget
must eventually suppress every call in a busy hour. The fix this implies is not a model
or provider change: make the failure surface explicit and stop the silent suppression —
carry a reason on the None return (`cap` / `breaker` / `no_client` / `empty_content` /
HTTP status) into the gate row instead of the opaque `reason=aux_none`, and stop paying
one aux call per seam when one answer per turn would do (gate + intent judge + stage-2
classify all draw on the same budget), which is what makes the cap reachable at all.
Implementation is out of scope for this unit.

## 5. Verdict table

| item | method | command | observed output | verdict |
|---|---|---|---|---|
| population count | full enumeration, 11 route logs + tombstones | `python3 probe1.py` | `total aux_none lines (all files, raw): 7163` / `distinct (ts,line): 6707` | VERIFIED |
| drift vs 5074 | cutoff at recorded latest ts | `python3 probe3.py` | `rows <= 11-08T18:24:48Z: 4814`; `max ts overall: 2026-11-10T09:42:55Z` | VERIFIED (−260 at cutoff, +2349 since) |
| per-profile counts | per-file grep | `grep -c aux_none /opt/data/profiles/*/uncensored-router.log` | reviewer 1023 … evol 108 (table §1) | VERIFIED |
| bucket split | latency rule on 4717 rows with the field | `python3 table.py` | `TOTAL 7163 | 3741 | 160 | 2446 | 816` | VERIFIED |
| breaker as cause | 600 s proximity to logged openings | `python3 brk.py` | `within 600s: 0  outside: 3741` | REFUTED |
| hourly budget as cause | cap-value pinning in token ledger | `python3 cap.py` | `132 profile-hours ... at a cap value`; `their instant aux_none rows: 2404` | SUPPORTED (hypothesis-grade on mechanism, measured on pinning) |
| 402/quota | per-row join to failure log | `python3 join.py` | `tested: 160 joined: 0` | UNPROVEN (0 rows attributable; 402 lives on anchor lane only) |
| model-not-found | same join | `join.py` | 0 joined; fleet `model_not_found: 578` | UNPROVEN per row (fleet count 578 from errors.log) |
| timeout | same join | `join.py` | 0 joined; fleet `timeout: 258` | UNPROVEN per row |
| empty response finish=length vs stop | field absent on aux rows | `grep -h 'hermes_aux_failed' …` | line format has `reason=empty_content` only, no `finish=` | UNPROVEN (finish is never recorded for aux; `finish=` exists only on `anchor_route_failed`) |
| anchor_route_failed 402 live/dead | date histogram | `grep ... \| grep 'anchor_route_failed'` | last `2026-11-09 01:18:12 Error code: 402` | LIVE |
| hermes_aux_failed live/dead | date histogram | `grep ... \| sort \| tail -1` | `2026-11-10 09:55:18 ... reason=empty_content`; 13 rows on 10-06 | LIVE |

NOT CHECKED: rotated route logs (none exist) → whether the recorded 5074 included
lines truncated in place; the −260 drift stays unattributed.
NOT CHECKED: process-side breaker/cap counters (in-memory only, `_CALL_TIMES`,
`_BREAKER_OPENED_AT` are module globals, `semantic_classifier.py:200`) → the
cap-vs-breaker split inside the 3741 silent rows is inferred from ledger pinning, not
from a counter readout.
