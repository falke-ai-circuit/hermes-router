unit: M50-23 · owner: coder=probe · evidence only (no product behaviour change)
artefact: /opt/data/tmp/r78u/M50-23_PROBE.md
head measured: a33ac67d93f7d913e074b07934d9888673a58f61 (git status --porcelain = empty)
reviewed_by: PENDING · verdict: PENDING · verdict_sha_of_head: a33ac67
suite: /opt/hermes/.venv/bin/python3 -m pytest -q tests/routing/test_r78u_m50_02_budget_retry.py tests/higher_self/test_m50_14_retry_descends_effort.py
       34 passed in 2.51s (at a33ac67, tree clean)
driver: /opt/data/tmp/r78u/m50_23_probe.py (outside the repo; imports the production module from /opt/data/plugins)

CODE UNDER TEST (read before driving; line numbers at a33ac67)
  anchor_exec.py:1045  _length_exhausted = (finish_reason == "length")
  anchor_exec.py:1046-1111 retry gate: _budget_retry_max_tokens -> _budget_retry_effort
                  -> _retry_raises_ceiling -> _retry_is_worth_paying -> one retry -> log
  anchor_exec.py:576-604  _budget_retry_max_tokens  max(2*cur, cur+16384, ADVISORY_TOKEN_FLOOR) clamp model_output_limit
  anchor_exec.py:607-638  _budget_retry_effort     max|xhigh|maximum -> "high"
  anchor_exec.py:690-706  _retry_raises_ceiling    raise allowance ONLY when effort does not descend
  anchor_exec.py:641-668  _retry_is_worth_paying   effort changed OR budget can raise
  anchor_exec.py:1262     ADVISORY_TOKEN_FLOOR = 48000
  model_output_limit("anthropic/claude-sonnet-5.5") = 64000 (claude family), "z-ai/glm-5.3" = 32768

WHY THE DRIVER IS THE PRODUCTION FUNCTION
  anchored_call() is the live arm (anchor_exec.py:845). The driver builds the same
  AnchorEndpoint the profile config resolves (nous://anthropic/claude-sonnet-5.5,
  base_url http://127.0.0.1:8680/v1, key env NOUS_API_KEY), passes only messages +
  max_tokens, and lets anchored_call inject reasoning_effort from
  anchor_chain.reasoning_effort (anchor_exec.py:897). Every retry line quoted below was
  emitted by that production code. Nothing in /opt/data/plugins was modified
  (git status --porcelain empty before and after).

===========================================================================
PROBE (a) — FORCED finish=length, pre-fix shape: reasoning_effort=max, small max_tokens
===========================================================================
command:
  cd /opt/data/tmp/r78u && HERMES_HOME=/opt/data/profiles/m3629canary \
    /opt/hermes/.venv/bin/python3 m50_23_probe.py --max-tokens 12000 --tag a_12000

profile: m3629canary (anchor_chain.reasoning_effort = max; log_path -> coder's route log)
input shape: model=anthropic/claude-sonnet-5.5, messages=[system neutral consultant frame,
  user "Design a fault-tolerant ingestion pipeline for 40k events/sec ..."],
  max_tokens=12000, reasoning_effort injected = max
window: 2026-11-15T21:31:11Z .. 2026-11-15T21:33:59Z

driver stdout (verbatim):
  anchor_route_failed reason=empty_response model=anthropic/claude-sonnet-5.5 finish=length tool_calls=False reasoning_chars=0 reasoning_tokens=12000 payload_keys=['max_tokens', 'model', 'reasoning_effort'] max_tokens=12000 n_msgs=2
  {"tag": "a_12000", "config_reasoning_effort": "max", "payload_max_tokens_in": 12000,
   "model_output_limit": 64000, "retry_max_tokens_would_be": 48000,
   "retry_effort_would_be": "high", "started": "2026-11-15T21:31:11Z",
   "content_chars": 12751, "delivered": true, "cost_usd": 0.076666,
   "prompt_tokens": 168, "completion_tokens": 7633,
   "content_head": "# Fault-tolerant ingestion pipeline: 40k events/sec  ## 0. Assumptions and the exactly-once contract ...",
   "secs": 168.4, "ended": "2026-11-15T21:33:59Z"}

raw log lines (unfiltered, with timestamps), /opt/data/profiles/coder/uncensored-router.log:
2026-11-15T21:32:57Z PRE event_detail=anchor_budget_retry model=anthropic/claude-sonnet-5.5 finish=length max_tokens_from=12000 max_tokens_to=12000 effort_from=max effort_to=high at_ceiling=False reasoning_tokens=12000
2026-11-15T21:33:59Z PRE event_detail=anchor_budget_retry_recovered model=anthropic/claude-sonnet-5.5 chars=12751 max_tokens=12000 reasoning_tokens=2772

ASSERTED: anchor_budget_retry -> anchor_budget_retry_recovered AND a delivered body.
  retry line:  21:32:57Z (finish=length, effort=max -> high, allowance held at 12000)
  recovery:    21:33:59Z chars=12751
  delivered:   anchored_call returned 12,751 chars, completion_tokens=7633. (The retry's own
               finish_reason is not exposed by anchored_call's return tuple; content is the
               assertion, and 12,751 chars is content.)
  PROVEN LIVE.

second shape, same probe, max_tokens=1200 (the cap this brief suggests):
command:
  cd /opt/data/tmp/r78u && HERMES_HOME=/opt/data/profiles/m3629canary \
    /opt/hermes/.venv/bin/python3 m50_23_probe.py --max-tokens 1200 --tag a_maxeffort
window: 2026-11-15T21:30:17Z .. 2026-11-15T21:30:57Z
driver stdout: finish=length reasoning_tokens=1200, retry_effort_would_be=high,
  content_chars=0, delivered=false, secs=40.9
raw log lines:
2026-11-15T21:30:31Z PRE event_detail=anchor_budget_retry model=anthropic/claude-sonnet-5.5 finish=length max_tokens_from=1200 max_tokens_to=1200 effort_from=max effort_to=high at_ceiling=False reasoning_tokens=1200
2026-11-15T21:30:46Z PRE event_detail=anchor_budget_retry_empty model=anthropic/claude-sonnet-5.5 max_tokens=1200 reasoning_tokens=1200
2026-11-15T21:30:57Z PRE event_detail=anchor_consult_spend_recorded_empty model=anthropic/claude-sonnet-5.5 est_cost=0.024672 calls=2
  RESULT: descent fires, retry sends max_tokens=1200 (M50-15: do not raise while descending),
  the answer needs ~7.6k-9k completion tokens (measured in the same run at 12000 and 24000),
  so the retry cannot produce a body: _empty, no delivery. Reported, NOT patched (F2 below).

CONTROL — the ceiling-raise arm + the 48,000 floor, live (unconfigured driver run, so no
reasoning_effort was injected: effort_from=-). This is the only run in this unit where the
retry RAISED the allowance, and it is direct evidence the floor is applied on the retry:
command: cd /opt/data/tmp/r78u && HERMES_HOME=/opt/data/profiles/m3629canary \
  python3 m50_23_probe.py --max-tokens 1200 --tag a
window: 2026-11-15T21:27:58Z .. 2026-11-15T21:29:23Z
raw log lines (/opt/data/uncensored-router.log — the driver's own fallback log for that run):
2026-11-15T21:28:13Z PRE event_detail=anchor_budget_retry model=anthropic/claude-sonnet-5.5 finish=length max_tokens_from=1200 max_tokens_to=48000 effort_from=- effort_to=- at_ceiling=False reasoning_tokens=1200
2026-11-15T21:29:23Z PRE event_detail=anchor_budget_retry_recovered model=anthropic/claude-sonnet-5.5 chars=13007 max_tokens=48000 reasoning_tokens=3379
  2*1200=2400 and 1200+16384=17584 are both below the floor, and the line records 48000:
  ADVISORY_TOKEN_FLOOR (M50-10) is applied on the live retry. 13,007-char body delivered.

===========================================================================
PROBE (b) — AT-CEILING variant, effort descent
===========================================================================
Attempt 1 — provider's own table ceiling for sonnet (64000). NOT FORCIBLE.
command: cd /opt/data/tmp/r78u && HERMES_HOME=/opt/data/profiles/m3629canary \
  /opt/hermes/.venv/bin/python3 m50_23_probe.py --max-tokens 64000 --tag b_ceiling --timeout 900
window: 2026-11-15T21:34:20Z .. 2026-11-15T21:39:21Z
stdout: anchor_route_failed reason=exception detail=Error code: 502 - {'error': {'message': 'shim upstream: timed out', 'type': 'shim_error'}}
  content_chars=0, delivered=false, secs=301.1
  BLOCKER: the local shim (127.0.0.1:8680) returns 502 "shim upstream: timed out" at ~301s,
  before a 64,000-token think completes (M50-14 measured 340.9s at 48,000). No finish=length
  is returned, so the retry arm is never entered. ceiling probe cannot run at 64k on this proxy.

Attempt 2 — a model whose table ceiling is reachable inside the shim timeout (z-ai/glm-5.3, 32768). NOT FORCIBLE.
command: cd /opt/data/tmp/r78u && HERMES_HOME=/opt/data/profiles/m3629canary \
  /opt/hermes/.venv/bin/python3 m50_23_probe.py --max-tokens 32768 --model z-ai/glm-5.3 --tag b_glm_ceiling
window: 2026-11-15T21:39:42Z .. 2026-11-15T21:40:05Z
stdout: content_chars=8102, delivered=true, completion_tokens=4813, secs=23.3, no retry line emitted
  BLOCKER: at its own ceiling with reasoning_effort=max, glm-5.3 answers (8,102 chars in 23.3s).
  It does not exhaust the allowance, so finish=length never arrives and at_ceiling is never reached.
  A model that answers at max effort cannot exercise the descent arm.
  (capability pre-check: POST /v1/chat/completions model=z-ai/glm-5.3 max_tokens=32768
   reasoning_effort=max -> HTTP OK, finish=stop, reasoning_tokens=54 — the ceiling is accepted.)

Attempt 3 — at-ceiling reached through the DOCUMENTED deployment knob, not a patch. PROVEN LIVE.
  `advisory_max_tokens_ceiling` is the repo's own config override for endpoints whose real
  ceiling is lower than the family table (anchor_exec.py:1364-1373). Set to 24000 in a
  throwaway probe home (/opt/data/tmp/r78u/probehome/config.yaml), so 24000 IS the model
  ceiling and a 24000 request is at it. No product config, file or code was changed.
command: cd /opt/data/tmp/r78u && HERMES_HOME=/opt/data/tmp/r78u/probehome \
  /opt/hermes/.venv/bin/python3 m50_23_probe.py --max-tokens 24000 --tag b2_ceiling24000 --timeout 600
input shape: model=anthropic/claude-sonnet-5.5, same messages, max_tokens=24000,
  reasoning_effort injected = max, model_output_limit=24000 (== max_tokens, i.e. at ceiling)
window: 2026-11-15T21:40:23Z .. 2026-11-15T21:45:07Z
driver stdout (verbatim):
  anchor_route_failed reason=empty_response model=anthropic/claude-sonnet-5.5 finish=length tool_calls=False reasoning_chars=0 reasoning_tokens=24000 payload_keys=['max_tokens', 'model', 'reasoning_effort'] max_tokens=24000 n_msgs=2
  {"tag": "b2_ceiling24000", "config_reasoning_effort": "max", "payload_max_tokens_in": 24000,
   "model_output_limit": 24000, "retry_max_tokens_would_be": 24000,
   "retry_effort_would_be": "high", "started": "2026-11-15T21:40:23Z",
   "content_chars": 18726, "delivered": true, "cost_usd": 0.013632,
   "prompt_tokens": 168, "completion_tokens": 9032, "secs": 284.5,
   "content_head": "# Fault-tolerant ingestion pipeline: 40k events/sec  ## 0. Requirement interpretation ...",
   "ended": "2026-11-15T21:45:07Z"}

raw log lines (unfiltered, with timestamps), /opt/data/tmp/r78u/m50_23_probe.log:
2026-11-15T21:43:54Z config_key_missing path=paths.log_max_bytes once=True
2026-11-15T21:43:54Z PRE event_detail=anchor_budget_retry model=anthropic/claude-sonnet-5.5 finish=length max_tokens_from=24000 max_tokens_to=24000 effort_from=max effort_to=high at_ceiling=True reasoning_tokens=24000
2026-11-15T21:45:07Z PRE event_detail=anchor_budget_retry_recovered model=anthropic/claude-sonnet-5.5 chars=18726 max_tokens=24000 reasoning_tokens=1682
2026-11-15T21:45:07Z config_key_missing path=providers.nous.base_url once=True
2026-11-15T21:45:07Z config_key_missing path=providers.nous.key_env once=True

ASSERTED: effort_from=max, effort_to=high, at_ceiling=true, and the retry is worth paying.
  effort_from=max effort_to=high at_ceiling=True  -> line 21:43:54Z, verbatim above.
  _retry_is_worth_paying: NOT A LOGGED FIELD. The predicate is computed at
  anchor_exec.py:1064 and only gates the branch; no counter carries its name. The
  equivalent evidence is exact: the same call emitted anchor_budget_retry with
  at_ceiling=True and did NOT emit anchor_budget_retry_skipped reason=ceiling, and the
  descent produced 18,726 chars (21:45:07Z). Claiming a `_retry_is_worth_paying=true`
  log line would be a fabrication; this artefact does not claim one.
  ceiling did not grow (max_tokens_to=24000 == max_tokens_from=24000) and the retry still
  ran and recovered: the M50-14b hole (at-ceiling starved consult gets no retry) is closed
  on live evidence.
  PROVEN LIVE at a 24,000 ceiling.

===========================================================================
CLOSING STATEMENT
===========================================================================
proven live in this unit:
  1. finish=length -> anchor_budget_retry -> anchor_budget_retry_recovered -> body, at the
     pre-fix input shape effort=max / max_tokens=12000 (21:32:57Z -> 21:33:59Z, chars=12751).
  2. ADVISORY_TOKEN_FLOOR=48000 applied on the retry when the ceiling can rise:
     1200 -> 48000 recorded and 13,007 chars recovered (21:28:13Z -> 21:29:23Z).
  3. at-ceiling descent: at_ceiling=True, effort max -> high, ceiling held at 24000, retry
     paid and recovered 18,726 chars (21:43:54Z -> 21:45:07Z).

not proven live (named blockers, not simulation):
  4. the at-ceiling arm at a PROVIDER'S OWN table ceiling (sonnet 64000): shim returns
     502 "shim upstream: timed out" at 301s before 64k thinking completes (21:34:20Z run).
  5. a non-sonnet model at its own ceiling (glm-5.3 @32768, effort=max) answers instead of
     exhausting (8,102 chars / 23.3s), so the arm is never entered.
  Neither blocker falsifies the code; both are properties of the upstream, and attempt 3
  reaches the same branch through the repo's documented ceiling knob.

FINDINGS (reported, nothing patched in this unit)
  F1  no product defect seen on any exercised arm. retry, floor, descent, ceiling gate and
      recovery all fired as their docstrings say.
  F2  minor, real, not patched: at max_tokens=1200 the descent arm holds the allowance at
      1200 (M50-15) while the same ask needs 7.6k-9k completion tokens, so the retry is
      paid and returns _empty (21:30:31Z -> 21:30:46Z). Inside the operating envelope this
      cannot happen (the advisory floor raises the allowance before the call), but a
      starved pre-fix cap below answer size buys a guaranteed-empty retry. Owner decision:
      leave as-is (input outside envelope) or make the descent arm floor the allowance at
      ADVISORY_TOKEN_FLOOR when the current cap is below it.
  F3  the brief asserts a `_retry_is_worth_paying=true` log field. It does not exist in the
      log (anchor_exec.py:1064 computes and branches; nothing logs the predicate). Evidence
      for the decision must be read from at_ceiling + the presence of the retry line and the
      absence of _skipped reason=ceiling.

REPRODUCE
  git -C /opt/data/plugins/hermes_router rev-parse HEAD   # a33ac67
  cd /opt/data/tmp/r78u && HERMES_HOME=/opt/data/profiles/m3629canary \
    /opt/hermes/.venv/bin/python3 m50_23_probe.py --max-tokens 12000 --tag rerun_a
  cd /opt/data/tmp/r78u && HERMES_HOME=/opt/data/tmp/r78u/probehome \
    /opt/hermes/.venv/bin/python3 m50_23_probe.py --max-tokens 24000 --tag rerun_b
