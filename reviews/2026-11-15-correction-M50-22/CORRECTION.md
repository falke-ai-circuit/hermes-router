# M50-22 — correction of the M50-20 measurement

unit: M50-22 · owner: analyst (measurement; evidence only, no product code)
head measured: be55019 (a33ac67 + M50-23 probe commit), git status --porcelain = empty
measured at: 2026-11-15T22:0x–22:2xZ
reviewed_by: PENDING · verdict: PENDING · verdict_sha_of_head: be55019
suite: /opt/hermes/.venv/bin/python3 -m pytest -q tests/routing/test_r78u_m50_02_budget_retry.py
       tests/higher_self/test_m50_14_retry_descends_effort.py tests/routing/test_r78u_m50_04_failover_bound.py
       tests/higher_self/test_banner_template_compliance.py -> 43 passed in 4.47s (run by me at be55019)
scripts (read-only over logs): /opt/data/tmp/r78u/m50_22_before.py, m50_22_heads.py, m50_22_heads2.py,
       m50_22_banner_before.py, m50_22_dup.py

This artefact re-derives every number it prints. M50-21's numbers were reproduced or contradicted
from the logs, never copied.

## 1. DEFECT 1 — BEFORE COUNT (M50-20 said 6 lines / 3 sessions)

Window bounds: 2026-11-14T00:00:00Z .. 2026-11-15T00:00:00Z, upper bound EXCLUSIVE (whole UTC day
11-14, every profile). Command, verbatim as run:

  cd /opt/data/profiles && for p in analyst architect coder conductor evol m3629canary operative \
    orchestrator researcher reviewer shadow valmet; do
    awk '$1>="2026-11-14T00:00:00Z" && $1<"2026-11-15T00:00:00Z" && /empty_response/ {c++} END{print c+0}' \
      $p/uncensored-router.log; done

plus the same awk restricted to session ids:
  awk '... /empty_response/ {match($0,/session_id=[^ ]+/); print substr($0,RSTART,RLENGTH)}' <p>/uncensored-router.log | sort -u | wc -l

| profile | empty_response lines 11-14 | sessions | session ids |
|---|---|---|---|
| analyst | 0 | 0 | — |
| architect | 1 | 1 | api_1794646732_5ebd1953 |
| coder | 1 | 1 | api_1794684231_56ee65ad |
| conductor | 3 | 1 | 20260813_160517_d56f96a7 |
| evol | 0 | 0 | — |
| m3629canary | 0 | 0 | no uncensored-router.log on disk (renders file only, 1 line, Nov 9) |
| operative | 0 | 0 | — |
| orchestrator | 0 | 0 | — |
| researcher | 0 | 0 | — |
| reviewer | 3 | 2 | api_1794679847_19825b68 (x2), api_1794696047_345169ae |
| shadow | 0 | 0 | — |
| valmet | 0 | 0 | — |

TOTAL = 8 lines / 5 sessions across 4 of 12 profiles. All 8 lines are event_detail=completion_audit_skipped
reason=anchored_call_empty_response fail_reason=empty_response (no other empty_response shape occurs that day).

Verdict on M50-20's "6 lines / 3 sessions": FALSE. Its own rows (1/1 + 1/1 + 3/1 + 3/2) already sum to
8 lines / 5 sessions, so the aggregate contradicted its own table. The three brief-named profiles alone are
5 lines / 3 sessions. M50-21's 8/5 is reproduced independently.

## 2. DEFECT 2 — HEADS PER BODY

Counting rule, applied identically to every record:
  head_re = · router · (impulse \(decision\)|higher-self \(frontier\)|shadow-self \(uncensored\))
  body   = one render record with stage in {BENIGN_BANNER, PARKED_CAPTURE_FALLBACK}
PRE-stage records are excluded: their render is the injected "----- BEGIN ROUTER READING" preamble
(measured: 597-802 chars, 0 heads), i.e. the reading text, not a delivered body. Counting them as bodies
is what produced M50-20's record-vs-turn confusion.
After window: 2026-11-15T20:24:57Z .. 2026-11-15T20:31:30Z, session ids taken from m50_20_live.json.

Command: /opt/hermes/.venv/bin/python3 /opt/data/tmp/r78u/m50_22_heads2.py (record filter: session_id ==
driven id, ts in window, stage in the two above; heads = len(head_re.findall(render))).

| profile | driven session id | bodies | heads per body |
|---|---|---|---|
| analyst | api_1794774302_b2dd3a43 | 1 | [2] |
| architect | api_1794774302_8b0f3077 | 2 | [1, 1] |
| coder | api_1794774302_c0de81b6 | 1 | [1] |
| conductor | api_1794774302_1c68e0bc | 1 | [1] |
| evol | api_1794774302_2ef7abf3 | 2 | [1, 1] |
| operative | api_1794774302_5074b4d9 | 2 | [1, 1] |
| orchestrator | api_1794774302_0c467940 | 1 | [1] |
| researcher | api_1794774302_445d25aa | 2 | [1, 1] |
| reviewer | api_1794774302_83801a94 | 2 | [1, 1] |
| shadow | api_1794774302_95bd8f89 | 1 | [1] |
| valmet | api_1794774302_49b09719 | 2 | [1, 1] |

Distribution over 17 delivered bodies: 0 heads = 0, exactly 1 head = 16, 2 heads = 1.
Body with >1 head, named: analyst session api_1794774302_b2dd3a43, record 2026-11-15T20:27:15Z,
stage=BENIGN_BANNER, render_chars=4031, two canonical heads, verbatim:

  · router · impulse (decision) | 1 verdict+1 hold | ×2 | row=1032 ·/jev @ typesafe | tok 1647/74 | $0.0002 | provider=typesafe | initiator=agent
  · router · higher-self (frontier) | pre-check | anthropic/claude-sonnet-5.5 @ 127.0.0.1:8680 | tok 1605/1712 | $0.0203

Two different lanes, two models, two spend figures in one delivered body. Against the one-banner rule
(unit_banner_unify_spec.md: "Exactly ONE decision banner per delivered turn ... Two banners where one
would do is the defect") and r79_one_banner_per_turn.py.spec:65 (body.count(HEAD) == 1), that is a
violation of the head count, not a rounding.

Verdict on M50-20's "1 banner per driven turn": FALSE as worded. Two independent failures:
 (a) analyst delivered 2 heads in 1 body (1 of 17 bodies);
 (b) six profiles (architect, evol, operative, researcher, reviewer, valmet) delivered 2 bodies in one
     turn, so heads per TURN is 2 for them and 1 for the rest — the correct invariant is per BODY, and
     only analyst breaks it.
Corrected claim: 16 of 17 delivered bodies carry exactly one canonical head; 1 of 17 carries two.

## 3. DEFECT 3 — LIKE-FOR-LIKE BANNER DELTA

Same counting rule both columns (head_re above, delivered-body stages only). Denominators differ and are
printed in the table:

| column | window | population (denominator) | bodies | 0 heads | 1 head | 2+ heads | ≥1 head |
|---|---|---|---|---|---|---|---|
| before | 2026-11-14T00:00:00Z..2026-11-15T00:00:00Z | delivered bodies in the day, 12 profiles (ambient traffic) | 123 | 1 | 100 | 22 | 99.2% |
| after | 2026-11-15T20:24:57Z..20:31:30Z | delivered bodies in the 11 driven sessions | 17 | 0 | 16 | 1 | 100% |

Before-side per profile (bodies / 0 heads / 1 head / 2+ heads), command m50_22_scopeA.py:
analyst 3/0/1/2, architect 16/0/16/0, coder 22/1/17/4, conductor 14/0/13/1, evol 1/0/0/1,
operative 8/0/5/3, orchestrator 46/0/38/8, researcher 0, reviewer 9/0/7/2, shadow 0, valmet 4/0/3/1,
m3629canary 0.

VERDICT: NOT COMPARABLE. Reason, measured: the before column's denominator is ambient day traffic; the
after column's is driven sessions. No 11-14 driver ledger exists on disk (checked the /opt/data/tmp/r78u
artefact set: m5010.json / m5011.json are 11-14 single-model probes, there is no 12-profile 11-14 sweep
file with per-turn session ids), so the before side cannot be restated on the after side's footing. The
99.2% -> 100% number is therefore NOT banked as an improvement — and it is not the improvement the brief
wanted either: per delivered body the before side ALREADY carried a head on 100 of 101 banner-bearing
bodies, so the "before: banner ZERO on the rest" baseline was never a per-body banner count. It was a
per-failing-turn observation (section below), which is the different quantity.

Attempt to build a driven-turn before column, and why it fails (this also corrects M50-21's claim 4):
for each of the 8 empty_response lines on 11-14, nearest delivered body in ±180s in the same session
(m50_22_heads2.py, SCOPE B):
  architect 09:13:19Z -> body 09:13:10Z, 1790 chars, 1 head
  coder     20:06:20Z -> none
  conductor 21:08:50Z -> none
  conductor 21:42:43Z -> body 21:40:36Z, 173 chars, 1 head
  conductor 22:35:28Z -> none
  reviewer  18:25:14Z -> none
  reviewer  18:33:17Z -> none
  reviewer  22:54:07Z -> body 22:53:58Z, 2433 chars, 2 heads
3 of 8 failing turns have any delivered body within ±180s; 5 have none. M50-21 read those 5 as "bodies
that carried no banner" (it counted PRE-stage reading preambles, 597-828 chars, as bodies). Corrected:
on 5 of 8 failing turns nothing was delivered at all, so "banner absent" is not measurable there — there
is no body to carry or omit one. A failing-turn banner denominator therefore does not exist on the before
side either.

Session-matched scope, for completeness (bodies inside the 5 sessions that failed, delivered stages only):
architect api_1794646732_5ebd1953 = 3 bodies (0/3/0); coder api_1794684231_56ee65ad = 5 (0/4/1);
conductor 20260813_160517_d56f96a7 = 14 (0/13/1); reviewer api_1794679847_19825b68 = 5 (0/4/1);
reviewer api_1794696047_345169ae = 2 (0/1/1). 29 bodies, 4 with two heads — the double-head defect is not
new to the after leg, and this is stated because it removes "2 heads is a post-fix regression" as a reading.

## 4. AFTER LEG — numbers M50-20 got right, restated unchanged, with denominators

Denominator for this section: 11 reachable profiles / 11 driven turns; window 2026-11-15T20:24:57Z..20:31:30Z.
Command for the zero: awk '$1>="2026-11-15T20:24:57Z" && $1<="2026-11-15T20:31:30Z" && /empty_response/ {c++} END{print c+0}'
per profile -> 0 on all 11; anchor_budget_retry|anchor_retry -> 0 on all 11. Re-derived by me, unchanged.

Ports (grep -h '^API_SERVER_PORT' <p>/.env, then curl 127.0.0.1:<port>/health): analyst 8646, architect 8650,
coder 8653, conductor 8649, evol 8645, operative 8651, orchestrator 8647, researcher 8648, reviewer 8654,
shadow 8643, valmet 8644 all 200; m3629canary 8669 -> 000 connection refused.
Delivered 11/11; wall clocks unchanged from M50-20: analyst 135.5s, architect 120.0s, coder 71.2s,
conductor 334.8s, evol 159.0s, operative 272.3s, orchestrator 71.6s, researcher 141.3s, reviewer 168.5s,
shadow 150.9s, valmet 188.6s. All 11 delivered a body; body_chars in M50-20 was capture-capped at 4000 and
that cap still applies.
"0 dropped" restated as M50-21 requires: 0 LOST. anchor_banner_promoted reason=promoted_from_stash fired
on 6 of 11 driven sessions in-window (architect, evol, operative, researcher, reviewer, valmet) — the
park->consume step missed on those turns and the promotion rescued the banner. Quoted from the logs:
  2026-11-15T20:26:57Z POST event_detail=anchor_banner_promoted edge=benign reason=promoted_from_stash session_id=api_1794774302_8b0f3077 (architect; same shape on evol/operative/researcher/reviewer/valmet).

## 5. NOT MEASURABLE — listed, not counted as passes

- m3629canary: API_SERVER_PORT 8669 configured in .env, no listener (curl 000 connection refused); no
  uncensored-router.log on disk, so its route log is empty by absence, not by measurement.
- docs: no .env, no API_SERVER_PORT -> no HTTP surface.
- recovery: no .env, no API_SERVER_PORT -> no HTTP surface.
- skill-archive, scripts: not runtime profiles (no .env).
Coverage therefore 11 of 12 port-bearing profiles, 11/14 profile directories.

## 6. RETRY / FLOOR / DESCENT ARM — ANSWERED by M50-23, dropped from the open list

M50-23 (commit be55019) proved the arm live by driving production anchored_call() at a33ac67. Evidence,
re-read from disk by me, not quoted from its summary:
  /opt/data/profiles/coder/uncensored-router.log:19422  2026-11-15T21:32:57Z PRE event_detail=anchor_budget_retry model=anthropic/claude-sonnet-5.5 finish=length max_tokens_from=12000 max_tokens_to=12000 effort_from=max effort_to=high at_ceiling=False reasoning_tokens=12000
  /opt/data/profiles/coder/uncensored-router.log:19423  2026-11-15T21:33:59Z PRE event_detail=anchor_budget_retry_recovered model=anthropic/claude-sonnet-5.5 chars=12751 max_tokens=12000 reasoning_tokens=2772
  /opt/data/uncensored-router.log:9316  2026-11-15T21:28:13Z PRE event_detail=anchor_budget_retry model=anthropic/claude-sonnet-5.5 finish=length max_tokens_from=1200 max_tokens_to=48000  (2*1200 and 1200+16384 are both under the floor; the line records 48000 => ADVISORY_TOKEN_FLOOR applied on the retry)
  /opt/data/tmp/r78u/m50_23_probe.log  2026-11-15T21:43:54Z PRE event_detail=anchor_budget_retry model=anthropic/claude-sonnet-5.5 finish=length max_tokens_from=24000 max_tokens_to=24000 effort_from=max effort_to=high at_ceiling=True reasoning_tokens=24000
  /opt/data/tmp/r78u/m50_23_probe.log  2026-11-15T21:45:07Z PRE event_detail=anchor_budget_retry_recovered model=anthropic/claude-sonnet-5.5 chars=18726 max_tokens=24000 reasoning_tokens=1682
  /opt/data/tmp/r78u/m50_23_probe_b2_ceiling24000.json: content_chars=18726, delivered=true, secs=284.5
Those three lines are the finish=length entry, the floor raise, and the at-ceiling descent with a delivered
body. The arm is exercised; the "retry arm unproven" gap in M50-21 CLAIM 3 is closed. Two upstream ceilings
remain non-forcible and stay non-claims: sonnet's 64k table ceiling (shim 502 at 301s) and glm-5.3's 32k
(it answers at max effort, finish=stop). Provenance note: the at-ceiling line is in the probe's own log
file, not a profile route log — that is where the driver wrote it, and it is why a fleet-wide grep on
profiles/*/uncensored-router.log does not show at_ceiling=True.

## 7. OPEN ITEMS

1. analyst double head (this unit's finding, not fixed here — measurement only): 2 canonical heads in one
   4031-char body, session api_1794774302_b2dd3a43. Needs a product fix under the one-banner rule; owner
   decision required on which emitter appends the second line.
2. Before/after banner improvement claim: cannot be produced until an 11-14 driven-turn sweep exists with
   per-turn session ids and delivered-body capture. NOT COMPARABLE is the standing verdict.
3. M50-20_UNIT.json provenance defect stands (F5 of M50-21): it lists anchor_exec.py + a test file and
   lines_added 136 / lines_removed 23 for a no-code unit, and attributes M50-19's diff to M50-20. Not this
   unit's file to repair; recorded here so it is not silently dropped with the count corrections.

NOT CHECKED: a re-run of the 11-15 driven sweep at be55019 (the after leg is M50-20's run at 3520fb5,
quoted not re-driven) -> leaves "after leg holds at the current head" unsupported by a same-head run.
NOT CHECKED: m3629canary through any HTTP surface -> leaves it a coverage gap, not a pass.
