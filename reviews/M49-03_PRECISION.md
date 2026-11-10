# M49-03 — detcorpus precision/recall on the live aux path

Unit M49-03 · profile analyst (gateway 8646) · 2026-11-10
Owner: analyst = measurement. READ-ONLY on product code: no edits to
`/opt/data/plugins/hermes_router` outside the one measurement report committed in `reviews/`.

Repo under test: `/opt/data/plugins/hermes_router`, branch `main`, HEAD **0830850**
(`M49-02: name the aux_none cause ... and reuse one answer per turn`), tree clean at start.

Precondition asserted by the brief, re-verified here: the aux wall is HTTP 402, not quota silence.
Today's runs produced **zero 402s** — the only transport failures were 12 empty-content returns
out of 828 (1.4%), recorded as `intent_why=aux_none`, not as payment errors.

---

## 1. Commands actually run, and wall-clock

| run | command | calls | wall-clock | model |
|---|---|---|---|---|
| **A** detcorpus T2 arm, full corpus | `cd /opt/data/tmp/r78u && python3 m49_03_live_t2.py` (imports `tools/detcorpus/score.py` unmodified, calls `score.t2_arm`) | 874 | **540.0 s** (instrumented) | `reflection/beam-1.0-501b-a23b:free` |
| **B** live aux client path | `cd /opt/data/tmp/detcorpus && N_POS=300 N_NEG=300 WORKERS=5 TIMEOUT=60 python3 aux_judge_measure.py` | 600 | ≈514 s — *derived*, not instrumented: first write 12:57:20, final write 13:05:54; per-call latencies sum 2437.8 s at 5 workers | `meituan/longcat-2.5:free` (analyst `auxiliary.router` primary) |
| **C** shipped-lane replay (gate instrument) | `cd /opt/data/tmp/r78u && EVAL_OUT=…/m49_03_lane_gates.json EVAL_CACHE=… AUX_MODEL=deepseek/deepseek-v4.1-flash-1017 python3 m41_08_eval.py` | 1153 distinct prompts (828 rows, cache-shared) | ≈5 min — *derived*: final write 13:13:00 | `deepseek/deepseek-v4.1-flash-1017` |

Run A's own output line, quoted:

```
{"rows": 874, "calls": 874, "wall_s": 540.0, "model": "reflection/beam-1.0-501b-a23b:free"}
```

Run B's scoring block, quoted:

```
positives=300 negatives=300 errors=3
RECALL   154/300 = 51.3%
FP       5/300 = 1.67%
precision 96.9%
latency p50 = 3.35s
```

Run C's header and footer, quoted:

```
scored rows 828
distinct asks 609
wrote /opt/data/tmp/r78u/m49_03_lane_gates.json 828
```

Two tool-vs-HEAD drifts had to be worked around to run C, both in the *measurement script*
(`/opt/data/tmp/r78u/m41_08_eval.py`, written for 7977ec6), never in product code:
`KeyError: 'replace'` — `rule_table._decision` at 0830850 (`features/detection/rule_table.py:269-283`)
no longer returns a `replace` key. Patched to `decision.get("replace","")`. The consequence is real
and is reported in §5 gate 5, not hidden: the per-rule `replace` census is **not reproducible at
HEAD** (all 828 rows read `replace=""`).

A third environment defect, fixed before any measurement: `/opt/data/tmp/r78u/inspect.py` shadowed
the stdlib and crashed run A's first attempt (`AttributeError: module 'inspect' has no attribute
'get_annotations'`). Renamed to `inspect_shadow.py.bak`; run A then completed with 874/874 `ok=true`
and zero errors.

---

## 2. Denominators — and how the brief's 828 / 609 are produced

Corpus `/opt/data/tmp/detcorpus/corpus.jsonl`: **874 rows**, verified by `wc -l` and by parse.

| quantity | count | source |
|---|---|---|
| corpus rows | 874 | `wc -l corpus.jsonl` |
| — refusals (`population=refusal`) | 674 | parse of corpus.jsonl |
| — benign controls (`population=benign`) | 200 | parse of corpus.jsonl |
| distinct asks (`ask_sha`) | 639 | parse |
| closed-domain rows (taxonomy `sexual_content_minors`) | 46 (45 refusal, 1 benign) | mapped against `domain_gate.CLOSED_DOMAINS` |
| **scored rows after the closed-domain skip** | **828** | 874 − 46, matches run C's own print |
| **distinct asks after the skip** | **609** | parse of the 828 |

So the brief's 828 / 609 are the *M41-08 replay* denominators (closed domain skipped before any
call), not the corpus size. Both are stated everywhere below; no number is reported without its
denominator.

Split used for precision/recall:
- **Recall denominator** = 674 labelled refusals (all rows, run A) or the run-C subset named in §5.
- **Precision denominator** = TP + FP, where FP is measured on the **200 benign controls** — a sample
  of screened non-refusals, not a random sample of fleet deliveries (REPORT.md §1, unchanged limit).
- Unanswered aux calls (`fires=null`) count as **misses** for recall and are excluded from the
  precision denominator; there were **0** such rows in run A, 3 in run B (counted as miss/TN), 12
  `aux_none` in run C.

---

## 3. Precision and recall — measured today, live

Run A — detcorpus T2 arm (`_JUDGE_PROMPT` + `route_decision` semantics, `semantic_judge.PARTIAL_ROUTE_CONFIDENCE`), 874 rows:

| metric | value | arithmetic |
|---|---|---|
| recall | **70.92%** | 478 / 674 |
| precision | **95.41%** | 478 / 501 |
| FP rate on controls | **11.50%** | 23 / 200 |
| contingency | tp 478 · fp 23 · fn 196 · tn 177 | — |
| latency | p50 5.04 s, p95 12.17 s | 874 samples |
| errors | 0 / 874 | all `ok=true` |

Run B — the actual live aux client (`agent.auxiliary_client.get_text_auxiliary_client("router")`,
model = analyst's configured primary `meituan/longcat-2.5:free`), 300 positives / 300 negatives,
stratified on `labels_wf.jsonl` with a fixed seed:

| metric | value | arithmetic |
|---|---|---|
| recall | **51.33%** | 154 / 300 |
| precision | **96.9%** | 154 / 159 |
| FP rate on controls | **1.67%** | 5 / 300 |
| errors / unanswered | 3 / 600 | counted as miss and TN |
| latency | p50 3.35 s | 597 answered calls |

Run C — shipped lane replay (gate + intent judge + rule table, shipped code), 828 scored rows:

| metric | value | arithmetic |
|---|---|---|
| recall (all refusals, 629 rows) | 55.8% | 351 / 629 |
| precision | 98.6% | 351 / 356 |
| FP rate on 199 benign | 2.51% | 5 / 199 |
| gate `abstain` | 2 / 828 | both `empty_ask`, zero `aux_none` |
| intent source | aux 563 · deterministic 253 · unknown 12 | — |

Run A vs run B are not the same instrument and must not be averaged: A is the tool's own arm
(beam-1.0-501b, fixed prompt, all 874 rows); B is the live aux client on today's configured primary
model over a 600-row stratified sample.

---

## 4. Cost of the run, and the cap question

| item | value | evidence |
|---|---|---|
| aux calls issued | **2627** = 874 (A) + 600 (B) + 1153 distinct prompts (C) | run outputs + `len(m49_03_lane_cache.json)` = 1153 |
| dollars recorded | **$0.00** on every call | `score.t2_arm` → `cost: 0.0` for all 874; `est_cost_usd 0.0` on all 723 analyst aux ledger rows; both models are `:free`/unpriced |
| hourly-cap denials | **0** | see below |
| breaker opens | **0** | no `breaker` cause anywhere |
| elapsed | A 540 s · B ≈514 s · C ≈300 s ⇒ **≈23 min total** | §1 |

Why the cap cannot be hit by this measurement — and why that is a finding, not a clean bill:
the cap is `classification.aux_calls_per_hour`, default **20** (`core/schema.py:94`, legacy key
`:271`), enforced inside `semantic_classifier._aux_raw_call_uncached` (`semantic_classifier.py:600-607`,
`_note_cause("cap")` then `return None`), and the breaker check sits just above it at `:597-599`.
All three instruments **bypass that function**: A and C speak HTTP directly to the provider, B calls
`get_text_auxiliary_client` directly, C additionally monkey-patches `aux_raw_call`. Consequence:
2 627 calls drew **zero** budget, so the run could not exhaust the budget and `cause=cap` could
never be emitted by it. A *live* gate fed 874 calls in 9 minutes would have been capped after 20
and returned `aux_none`/`cap` for the remaining 854. The run does not prove the cap is generous; it
proves the instrument steps around it.

---

## 5. Does the measured number clear the lane's stated gate?

A bar exists on disk, and it is not detcorpus's: `M41-08_CORPUS.md` §Gates carries the design's
five acceptance gates. detcorpus's own report (`tools/detcorpus/REPORT.md` §8) deliberately sets
**no** threshold for its 674/200 figures — so "precision ≥ X" for detcorpus is undefined, and that
undefined bar is itself the answer for that half of the question.

| # | gate (as written in M41-08_CORPUS.md) | measured today | verdict |
|---|---|---|---|
| 1 | in-domain detection **≥ 85%** (baseline 61.2%) | **281/415 = 67.7%** | **FAIL** (−17.3 pts) |
| 2 | benign FP **≤ 5%** (baseline 8.5%) | **5/199 = 2.51%** | **PASS** |
| 3 | judge test-retest **≥ 0.75** (60 bodies, twice) | **Cohen κ 0.7402**, agreement 87.19% on 874 shared rows | **FAIL** (by 0.0098) |
| 4 | cost **≤ ~$0.0002/turn**, added latency 0 | dollars unpriced ($0.00 recorded, no price row); aux call p50 3.35 s (B) / 5.04 s (A) | **UNPROVEN** (cost) · **FAIL** as written (latency ≠ 0) |
| 5 | softened rule-3 firing / truth / `failed` bodies — *report only* | predicate census: `intent_declined` 334, `intent_softened` 281, `else` 190, `gate_flagged` 22, `gate_abstain` 1; `replace` unreadable at HEAD (§1) | **REPORTED / PARTIAL** |

Gate 1's denominator, stated because it is not the corpus's: 415 = refusals whose `domain_truth`
is not `benign_task` and whose `label_kind` ∈ {refusal, partial}, i.e. the rows the gate is meant
to catch. M41-08 published **139/295** for the same gate on the dead-aux run; its 295-row
denominator is **not reproducible** from `m41_08_results_nous402.json` — my filter yields 415 rows
on that same file. Applying my filter to both runs, apples to apples:

| substrate | gate 1 | gate 2 |
|---|---|---|
| 2026-10 (every call HTTP 402) | 167/415 = **40.24%** | 7/199 = 3.52% |
| 2026-11-10 (live aux) | 281/415 = **67.71%** | 5/199 = 2.51% |

So the live aux path is worth **+27.5 points of in-domain detection** over the 402-blocked run —
and still lands 17.3 points under the written bar. That gap is populated almost entirely by one
class: `meta_doctrine` refusals, 29/113 = 25.7%, while every topical domain is at 0.78–1.00
(cyber 0.87, weapons 0.779, fraud 1.0, malicious 0.973, drug 1.0, self_harm 1.0, other_harm 0.765).

The gate-3 number is a genuine test-retest, not a re-label: the same 874 bodies, same model, same
prompt, Oct 28 vs today. 44 rows flipped True→False, 68 False→True.

---

## 6. `cause=` on `aux_none` rows — M49-02 is landed but not deployed

Code at HEAD **does** write it:
`features/detection/domain_gate.py:435-441` sets `out["cause"] = aux_failure_cause()` on the
`aux_none` branch; `semantic_classifier.py:565-569` documents the vocabulary
(`cap | breaker | no_client | empty_content | timeout | http_<status> | invalid_json | unparseable |
transport | exception`); `core/state.py:463` persists `"cause": str(cause or "")[:40]`.

Deployed bytes do **not** carry it. sha256, repo vs every profile copy:

| file | repo HEAD | 13 deployed copies |
|---|---|---|
| `semantic_classifier.py` | `0436f82b…` | `2adcfc41…` (all 13) |
| `features/detection/domain_gate.py` | `9155e854…` | `2c7ac545…` (10) · `abf74213…` (architect, coder) |

`grep aux_failure_cause /opt/data/profiles/analyst/plugins/hermes_router/semantic_classifier.py`
→ **0 hits**. Live ledger, read directly: `/opt/data/profiles/analyst/hermes-router-decisions.jsonl`
holds 559 rows whose key set contains **no `cause` field at all**, including the 38 `aux_none` rows.
During the run window (last 60 min) there were 10 decision rows, all `gate_reason=not_consulted`,
**zero `aux_none`**, zero `cause`.

Answer to the question as asked: **no `aux_none` row during this run carries `cause=`, and none
could** — every deployed profile still runs pre-0830850 bytes. This is expected, it is not a defect
of the run, and it means the cap-vs-breaker-vs-empty_content split the brief asks for is currently
**unobservable in production telemetry**. The one place the distinction is visible today is the
measurement itself: 12 `intent_why=aux_none` out of 828 in run C were empty-content returns, 0 were
402s, 0 were cap denials.

---

## 7. What this measurement does not cover

- **NOT CHECKED → the gate-1 denominator M41-08 used (295).** I could not reproduce it from its own
  results file; my filter gives 415. The 67.7% figure carries my filter, not theirs, so a 295-based
  reading of today's number may differ by a few points.
- **NOT CHECKED → dollars.** No price row exists for either aux model; every ledger row says
  `est_cost_usd: 0.0`. Gate 4's cost clause is unprovable from fleet truth, only from a blended rate.
- **NOT CHECKED → end-to-end turn latency.** Only aux call time is measured here; render + retry
  ladder are not in these runs.
- **NOT CHECKED → live traffic precision.** The 200 controls are screened non-refusals, not a random
  sample of deliveries (REPORT.md's own limit, unchanged).
- **NOT CHECKED → single sample.** One call per body per arm; no repeat sampling except the Oct-vs-Nov
  comparison in gate 3.
- **NOT CHECKED → `replace` census.** Unreadable at HEAD (see §1); M41-08's 333 `replace=True` turns
  cannot be re-confirmed.
- No product code was changed, no config was changed, no lane was "fixed" to improve a number.

---

## 8. Verdict table

| item | method | command | observed output | verdict |
|---|---|---|---|---|
| live aux path is funded (precondition) | replay 828 rows through shipped gate/judge | run C | `gate_abstain 2/828` (both `empty_ask`), 0×402, 12/828 empty-content | **PROVEN** |
| detcorpus recall, live | 874 live calls, shipped T2 arm | run A | `478/674 = 70.92%` | **PROVEN** |
| detcorpus precision, live | same | run A | `478/501 = 95.41%`, FP 23/200 | **PROVEN** |
| live aux client recall/precision | 600-call stratified sample via `get_text_auxiliary_client("router")` | run B | `154/300 = 51.3%`, precision `96.9%`, FP `1.67%` | **PROVEN** |
| gate 1 (≥85% in-domain) | shipped replay, named 415-row subset | run C | `281/415 = 67.7%` | **FAIL** |
| gate 2 (≤5% benign FP) | shipped replay, 199 benign | run C | `5/199 = 2.51%` | **PASS** |
| gate 3 (test-retest ≥0.75) | same 874 bodies, Oct 28 vs Nov 10, same model/prompt | run A + `scores.jsonl` | κ `0.7402`, agreement `87.19%` | **FAIL** |
| gate 4 (cost ≤$0.0002/turn, latency 0) | ledger + per-call latency | runs A/B | all `cost 0.0`; p50 `3.35 s` | **UNPROVEN** / latency clause **FAIL** |
| gate 5 (rule-3 report) | predicate census | run C | 334 declined / 281 softened / 190 else / 22 flagged / 1 abstain; `replace` absent at HEAD | **REPORTED** |
| aux hourly cap impact | read `semantic_classifier.py:600-607`, count causes | all runs | 0 cap denials; instrument bypasses the capped function; cap = 20/hr | **PROVEN (bypass), cap untested** |
| `cause=` present on aux_none rows | sha256 repo vs 13 deployed copies + ledger key set | `grep`/`sha256sum`/ledger parse | deployed `2adcfc41…` ≠ repo `0436f82b…`; 38 aux_none rows, 0 `cause` | **ABSENT — as predicted, deployed pre-0830850** |
| detcorpus has a written bar | read `tools/detcorpus/REPORT.md` §8 | `read_file` | no threshold is stated for its 674/200 figures | **UNDEFINED — stated, not invented** |

Bottom line: the aux path is live and measurable, both detcorpus numbers are now real numbers
(recall 70.9% @ precision 95.4% on the tool's own arm; 51.3% @ 96.9% on the live client path), the
lane clears its benign-FP gate, **fails its 85% detection gate by 17.3 points**, and misses its
test-retest bar by 0.0098. The dominant miss class is `meta_doctrine` (25.7%), not topical harm.
