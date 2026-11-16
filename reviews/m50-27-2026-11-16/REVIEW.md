VERDICT FIX-FIRST /opt/data/tmp/r78u/M50-27_CLOSEOUT.md d9cc1a73d9b9bebfa0cc24a294a412c6e346692c 50 passed, 12 passed, 32 passed
# M50-27 — independent closeout of the three unaudited fixes

unit: M50-27
role: reviewer (author of none of these units)
repo: /opt/data/plugins/hermes_router  branch main
head_at_grade: d9cc1a73d9b9bebfa0cc24a294a412c6e346692c  git status --porcelain: 0 lines
reviewed_by: reviewer
verdict: FIX-FIRST
verdict_sha_of_head: d9cc1a73d9b9bebfa0cc24a294a412c6e346692c
suite: targeted 12 passed (3 new files), combined 50 passed, pairing 32 passed
open item: F3 absent; live gateway acceptance + live POST pairing not driven

## 1. THE THREE FIXES — graded on my own runs

### 383b5c3 M50-24 — PASS on everything drivable in-process
Probe: /opt/data/profiles/reviewer/cache/scratch/m5027_probe_24.py (real
route_gate.claim_pass -> real router_core.stage_model_swap -> real consult_budget;
no assertion borrowed from the coder's test). Output:

  scope s:m5027-independent|i:1|t:1
  A1 route=True staged=True task=77d1de8f8c0b206deaf63411
  A1 consumed=True
  A2 route=False reason=turn_claim_exists staged=False task=None
  A counts: t1=1 t2=None
  B3 route=False reason=turn_claim_exists
  C4 route=True staged=True same_scope=True
  events: executed=2 staged_true=2 standdown=0 budget_refused=0

Read: the declared claim stages (staged=True), the consult seat is CONSUMED once,
the task's PRE count is exactly 1, the second provider call does not re-stage, the
next-turn distinct ask stages in the SAME held scope (`|i:1|t:1`), and
declared_claim_standdown_unexecuted is 0 in the whole drive. The measured defect
shape (276/314 staged=False, 198 staging_no_record) does not reproduce.

Correction to the brief's expected shape: two DISTINCT asks in one held scope do
not both stage at the gate — the second returns turn_claim_exists (the one-consult-
per-turn contract), not a staged consult. The per-task budget slot exists
(budget.check allows task-bbb) but route_gate never reaches it. Not a regression
against the fix's claims; recorded so "each get a PRE slot" is not banked as a
delivered second consult.

POST pairing: the pair store is intact and reads back (own drive:
`no_pre -> None`, `stored: True`, `pre_pair -> True`; 32 pairing tests pass). The
live starvation (509 paired=False reason=no_pre) cannot be re-run without a
gateway bounce; the causal precondition (a PRE that stages) now holds, but no live
paired row was produced. See residual R1.

### 4ca97a4 M50-25 — F2 PASS, F3 FAIL (not a logged field)
Probe: /opt/data/profiles/reviewer/cache/scratch/m5027_probe_25.py, real
anchored_call at max_tokens=1200 with finish=length. Output:

  anchor_route_failed reason=empty_response model=claude-sonnet-5.5 finish=length ... max_tokens=1200
  delivered_content='RECOVERED ADVISORY'
  calls=2
    call0 max_tokens=1200 effort=max
    call1 max_tokens=48000 effort=high
  floor=48000
  PRE event_detail=anchor_budget_retry model=claude-sonnet-5.5 finish=length max_tokens_from=1200 max_tokens_to=48000 effort_from=max effort_to=high at_ceiling=False reasoning_tokens=0
  PRE event_detail=anchor_budget_retry_recovered model=claude-sonnet-5.5 chars=18 max_tokens=48000 reasoning_tokens=0
  worth_paying token in log: False

F2: the 1,200-cap descent ends in a DELIVERED BODY, not an empty retry. PASS.
F3: `_retry_is_worth_paying` is still not a logged field. `grep -rn
"worth_paying\|retry_worth\|worth=" --include=*.py` returns only the def (:641)
and the call site (:1125); the `anchor_budget_retry` row carries model, finish,
max_tokens_from/to, effort_from/to, at_ceiling, reasoning_tokens — no
worth/worth_paying decision and none of its inputs. The last commit touching that
symbol is 33f49bf (M50-14b). FAIL.

### d9cc1a7 M50-26 — PASS, re-driven
See §2. Render-seam re-drive, exact specimen and the whole in-window population.

## 2. DOUBLE-HEAD SHAPE — re-driven, heads per body before/after

Specimen drive (scratch/m5027_drive_heads.py): the two lane lines extracted from
the actual analyst record (api_1794774302_b2dd3a43, ts=1794774435.514,
render_chars=4031) registered into a fresh session and composed.

  BEFORE 4ca97a4: composed heads: 2  composed lines: 2  delivered heads: 2
  AFTER  d9cc1a7: composed heads: 1  composed lines: 2  delivered heads: 1

Window re-drive (scratch/m5027_heads_window.py): the 5 sessions named in
M50-22 §3, whole UTC day 2026-11-14, delivered-body stages only:

  bodies: 29
  BEFORE dist: {1: 25, 2: 4}
  AFTER  dist: {1: 29}

Doubled before, by body: coder idx=2694 (2→1), conductor idx=3294 (2→1),
reviewer_a idx=1802 (2→1), reviewer_b idx=1858 (2→1). All other 25 bodies stayed
at 1; no body gained a head. This reproduces M50-22's "4 of 29" independently and
means the fix is measured on the same bodies, not asserted from a diff.

## 3. M50-22's OPEN VERDICT — what banks, what does not

BANKABLE (my own run or a log line I re-read):
- The double-head defect is real: 4/29 bodies in-window carried TWO canonical
  heads; the analyst specimen carries exactly 2 (re-derived, §2 above).
- 383b5c3 removes the staging_no_record shape in-process: staged=True, one PRE
  per task, exactly-once, standdown 0 (§1).
- 4ca97a4 makes the 1,200 descent deliver a body at the 48,000 floor (§1).
- The earlier retry/floor facts quoted from disk, re-read by me:
  2026-11-15T21:30:31Z max_tokens_from=1200 max_tokens_to=1200 effort max->high
  (the old defect), 21:32:57Z 12000->12000 + 21:33:59Z recovered chars=12751,
  21:28:13Z 1200->48000 floor, 21:45:07Z recovered chars=18726 at 24000.

UNPROVEN / NOT BANKABLE:
- Before/after banner delta 99.2% -> 100%: NOT COMPARABLE. 123 ambient-day
  bodies vs 17 driven bodies; my §2 population is a third (29 session-matched
  bodies, 4 doubled). No 11-14 driven-turn ledger exists, so no like-for-like
  denominator. No improvement percentage is banked.
- Sonnet 64k table ceiling (shim 502 at 301s) and glm-5.3 32k (answers at max,
  finish=stop): NOT FORCIBLE. Not re-tested here; remain non-claims.
- "The after leg holds at the current head" (11 profiles, 0 empty_response,
  11/11 delivered): M50-20/22's run at an older head, not re-driven at d9cc1a7.
- 16-of-17 / 1-of-17 delivered-body split: measurement at be55019, not re-run.

## 4. RESIDUAL — UNPROVEN, not assumed

R1 M50-24 live acceptance: no gateway bounce was run (a worker may not bounce the
   fleet and a self-bounce ends this turn). Needed: one restart of this profile or
   the orchestrator, a driven declared ask, then
   `request_routing_executed staged=True` and `higher_self_pair stage=post
   paired=True` for the same task id, and 0 standdown rows in that turn.
R2 M50-24 live POST pairing: same run as R1; the in-process store read proves the
   mechanism, not the restored fleet pairing.
R3 F3: `_retry_is_worth_paying` logged field with inputs — ABSENT (verified
   negative). Needed: pass worth + (cur_mt, new_mt, cur_effort, new_effort) into
   `_budget_retry_log` at anchor_exec.py:1125-1134 and pin it.
R4 M50-26 S2 (lifecycle capture-fallback suppress) is declared unexercised by the
   author and remains so; needs a fake-clock test.
R5 The double-head re-drive is at the render seam on stored lane payloads, not a
   fresh delivered gateway turn. A new driven turn at d9cc1a7 would close it.
R6 64k / glm-32k ceilings: unproven and non-forcible; no claim either way.

## 5. Suite (my runs at d9cc1a7)
  tests/routing/test_m50_24_declared_claim_staging_handoff.py
  tests/routing/test_r78u_m50_25_descent_floor.py
  tests/banners/test_m50_26_one_head_per_body.py
    -> 12 passed in 2.48s
  + tests/routing/test_r78u_m50_02_budget_retry.py
  + tests/higher_self/test_m50_14_retry_descends_effort.py
  + tests/routing/test_r78u_m50_04_failover_bound.py
    -> 50 passed in 3.73s
  tests/higher_self/test_r34_19_pairing.py
  tests/higher_self/test_r34_19h_post_when_it_makes_sense.py
    -> 32 passed in 2.45s

## 6. FIX LIST (blocking before PASS)
1. Log `_retry_is_worth_paying` and its four inputs on the `anchor_budget_retry`
   row (anchor_exec.py:1125-1134), with a pinned assertion on the field.
2. Re-run M50-24 acceptance live per R1 and record staged=True + paired=True for
   the same task id, or record it as an accepted residual in the close header.
Everything else in §1-§3 reproduces.
