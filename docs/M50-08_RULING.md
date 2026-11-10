# M50-08 — RULING: thinking cannot be bounded separately on this path; ceiling raised to 64,000, single-shot

close header
unit: M50-08
owner: architect (design ruling — read-only, no product edit)
date: 2026-11-10
repo: /opt/data/plugins/hermes_router
head_at_ruling: a1a52d3 (main)
artefact: /opt/data/tmp/r78u/M50-08_RULING.md
research_legs: M50-06_LIVE.md (7 live consults, 47,170 retry) · M50-01_DIAG.md (mechanism)
probe_evidence: M50-08_probe.json · M50-08_probe2.json · M50-08_probe3.json (17 requests, verbatim cells below)
reviewed_by: none dispatched — design ruling, no review leg in this brief
verdict: RULED — Option 1 (bound thinking separately) is NOT AVAILABLE on this path, proven by capability probe. Option 2 (raise the ceiling) SPECIFIED at 64,000, single-shot, with a cap floor gate.
verdict_sha_of_head: a1a52d3

---

## 1. The ruling, in one paragraph

Goran's first option does not exist on the path we call. The nous shim at
`127.0.0.1:8680/v1` has exactly one output allowance — `max_tokens` — and every
thinking/reasoning field it accepts is a sub-budget INSIDE that allowance, never a
separate one. Seventeen requests across every field name the surface accepts produced
zero calls where `completion_tokens > max_tokens`; the one shape that does carry a
separate Anthropic thinking budget (`POST /v1/messages`) is refused by the shim with
404 `path_not_allowed`. So the ruling is **Option 2, specified**: the advisory consult
lane runs ONE call at the largest ceiling the config allows, **64,000**, with the
M50-02 doubling retry suppressed (it would re-burn a ceiling we are already at), and a
**cap floor gate** that refuses to spend on a call whose affordable ceiling sits below
the measured burn point. The ceiling does not eliminate the 43% residual — nothing at
max effort can, because thinking length is unbounded and the ceiling only moves where
the burn stops. It makes every remaining failure *measured* instead of guessed, and it
is **9.5% cheaper per consult** than today's two-rung ladder even when it fails.

---

## 2. Capability probe — the negative finding, with its evidence

Endpoint under test: `http://127.0.0.1:8680/v1/chat/completions`, model
`anthropic/claude-sonnet-5.5` — the literal endpoint and model `anchor_exec` resolves
(`anchor_chain.py:53` `NOUS_BASE`; `config.yaml:302-326` `anchor_chain.primary`).
Scripts: `/opt/data/tmp/r78u/M50-08_probe{,2,3}.py`; raw JSON beside them.

| cell | sent | max_tokens | completion | reasoning | content chars | finish | billed |
|---|---|---|---|---|---|---|---|
| A | `reasoning_effort:"max"` | 800 | 800 | 800 | 0 | length | $0.008162 |
| B | `thinking:{enabled,budget_tokens:6000}` | 800 | 800 | 673 | 305 | length | $0.008162 |
| C | `reasoning:{effort:max,max_tokens:6000}` | 800 | 800 | 800 | 0 | length | $0.008162 |
| D | `reasoning_effort:"low"` | 800 | 671 | 0 | 1763 | stop | $0.006872 |
| E | effort=max **+** thinking{6000} | 800 | 800 | 800 | 0 | length | $0.008162 |
| F | `zzz_bogus_field_xyz:1` | 800 | — | — | — | **HTTP 400** | unknown field |
| G | `POST /v1/messages` | 64 | — | — | — | **HTTP 404** | `path_not_allowed` |
| H | `thinking{6000}` > max_tokens | 4000 | 2282 | 1057 | 2898 | stop | $0.023038 |
| I | `thinking{budget_tokens:100}` | 800 | 23 | 0 | 43 | stop | $0.000250 |
| J1 | effort=max | 24000 | 24000 | 23999 | 0 | length | $0.240240 |
| J2 | effort=max | 24000 | 24000 | 24000 | 0 | length | $0.240268 |
| K | `thinking_budget:8000` (int) | 800 | — | — | — | **HTTP 400** | unknown field |
| L | `reasoning_budget:8000` (int) | 800 | — | — | — | **HTTP 400** | unknown field |
| M | `max_completion_tokens:800` + thinking{6000} | (800) | 800 | 800 | 0 | length | $0.008108 |
| N | `reasoning_effort:"minimal"` | 800 | 800 | 0 | 2413 | length | $0.008108 |
| O | `reasoning_effort:"xhigh"` | 800 | 800 | 799 | 0 | length | $0.008108 |
| P | trivial ask at 64,000 | 64000 | 4 | 0 | 2 | stop | $0.000074 |

**The load-bearing count: 0 of 17.** No request produced `completion_tokens >
max_tokens`. Not one field created an allowance outside the answer budget.

Four sub-findings that close the question for good:

1. **Unknown fields are rejected, not swallowed.** Cell F/K/L return
   `HTTP 400 invalid request body: json: unknown field "..."`. So `thinking` being
   accepted (B, H, I, M) is proof it is *recognised*, and its effect is still bounded
   by `max_tokens` (B and M both stop dead at 800 with 800 completion).
2. **`thinking.budget_tokens` is not honoured as Anthropic honours it.** On the real
   Messages API, `budget_tokens:6000` with `max_tokens:4000` is a 400 (budget must be
   below max_tokens) and `budget_tokens:100` is a 400 (minimum 1024). Here both return
   200 (cells H, I). The field is carried but not given thinking semantics.
3. **The separate-budget endpoint is unreachable.** `POST /v1/messages` →
   `404 {"type":"path_not_allowed","message":"Path /v1/messages not handled by shim"}`.
   The shim's allowlist is `/chat/completions`, `/completions`, `/embeddings`, `/models`
   (`hermes_cli/proxy/adapters/nous_portal.py:35-42`), and its handler forwards the body
   verbatim with no field translation (`hermes_cli/proxy/server.py:110-170`). There is no
   layer between us and the provider that could add a separate budget, and the provider's
   OpenAI-compatible surface does not have one.
4. **`reasoning.max_tokens` is inert.** Cell C sends `{"effort":"max","max_tokens":6000}`
   and still burns exactly 800 — the inner `max_tokens` is ignored while the inner
   `effort` is honoured.

This is the negative finding the brief asked for, and it should stop anyone re-opening
the question: **the cure cannot be a request-shape change on this path.** It is
`max_tokens` or nothing.

---

## 3. What the ceiling has to clear (and why 64,000 is the honest maximum)

Measured burn points, all at `reasoning_effort:max`:

| observation | ceiling | result | source |
|---|---|---|---|
| live, first rung | 23,585 | **7/7 burned**, finish=length, content empty | M50-06 §2 |
| probe J1 | 24,000 | burned, reasoning 23,999 | this unit |
| probe J2 | 24,000 | burned, reasoning 24,000 | this unit |
| live, retry rung | 47,170 | **3/7 still burned**, 4/7 recovered | M50-06 §2 |
| probe P | 64,000 | accepted by the API (trivial ask answered in 4 tokens) | this unit |
| config bound | 64,000 | `chain.max_tokens` `{"min":8000,"max":64000}` | `api/commands_config.py:186` |

The number M50-01 called the "thinking peak" (17,510) was a **success-case** number,
not a peak — cells J1/J2 burned the entire 24,000 with nothing to show. The true peak
is unbounded and unmeasurable from outside: once a call burns, the reasoning stops at
whatever ceiling we sent, so we never see how far past it wanted to go.

Arithmetic:

- The last observed failure point is 47,170. A 1.5× margin over it is 70,755 —
  **above the configurable maximum**. The ceiling cannot clear the tail with 1.5×.
- The maximum the config accepts is 64,000: **1.357× over the last failure point**,
  2.667× over the 24,000 probe burns.
- 64,000 is therefore not a "clear with margin" figure. It is the **largest number the
  system permits**, and the ruling says so plainly rather than dressing it up: raising
  the ceiling to its maximum buys the band `47,170 → 64,000` and nothing beyond it.
  Anything still thinking past 64,000 will still return empty, and §6 makes that
  failure visible with the number attached.

---

## 4. Spend delta at 64,000 — measured pricing

Pricing, verbatim from `config.yaml:317-320`: `output_per_1m: 10.0`,
`input_per_1m: 2.0`. Billing rule established by the probe: on a burn you pay the
**full ceiling** (J1: 24,000 tokens → $0.24024); on a success you pay what was emitted.

Per-consult output cost at a ceiling C: `C × 10.00 / 1e6`.

| case | tokens billed | cost |
|---|---|---|
| healthy consult (baseline the brief measured) | 1,312–1,833 | $0.015–0.025 |
| today, first rung burns (happens on 100% of consults) | 23,585 | $0.23585 |
| today, failed retry burns | 47,170 | $0.47170 |
| **today, full failure** (both rungs) | 70,755 | **$0.70755** |
| **new, full failure** (single call at 64,000) | 64,000 | **$0.64000** |
| new, success (30,000 thinking + ~700 out) | ~30,700 | ~$0.30700 |

**The delta is negative on the failure path: −$0.06755 per failing consult (−9.5%)**,
because the ladder pays for two rungs while the single call pays for one that is
higher. On successes the ceiling costs nothing extra — a consult that would have
answered at 40,000 emits 40,000 whether the ceiling says 47,170 or 64,000. The ceiling
is a limit, not a purchase.

Against the baseline: a working consult at max effort costs **12–20× the "healthy"
$0.015–0.025** the brief cites. That gap is the thinking, and it is not something the
ceiling changes.

---

## 5. Daily cost implication at the observed consult rate, against the $2.00 cap

Consult rate, counted from `anchor_route_fired` in
`/opt/data/profiles/architect/uncensored-router.log` (the events lane — note M50-06's
finding that retry strings live in `logs/agent.log` instead):

| day | consults |
|---|---|
| 2026-11-08 | 14 |
| 2026-11-09 | 39 (the M50-06 acceptance burst) |
| 2026-11-10 (to 14:41) | 3 |

Steady state is 3–14/day; 14 is the realistic high bound.

| scenario | daily spend | vs $2.00 cap |
|---|---|---|
| 3/day, all answer at 64k | 3 × $0.31 = $0.93 | 46% |
| 3/day, 43% residual (measured) | 1.7 × $0.31 + 1.3 × $0.64 = **$1.36** | 68% |
| 14/day, all answer | 14 × $0.31 = $4.34 | **217%** |
| 14/day, 43% residual | 8 × $0.31 + 6 × $0.64 = **$6.32** | **316%** |

Two facts that matter more than the projection:

1. **The cap is already breached at the CURRENT ceiling.** The spend ledger
   (`/opt/data/profiles/architect/hermes-router-spend.json`) records
   `2026-11-09: $2.368508` against `daily_cap_usd: 2.0` — 118.4% of cap, at 47,170.
   Raising the ceiling does not create the breach; it exposes that the cap was never
   sized for a lane whose consult costs $0.31–0.64.
2. **The cap guard will block the lane at the third consult.** `cap_check`
   (`anchor_exec.py:569-577`) prices the NEXT call from `estimate_tokens_from_payload`,
   which returns `out = max_tokens` (`anchor_exec.py:261-283`, verified). At 64,000 the
   pre-call estimate is $0.64+, so the guard blocks once spend passes $1.36 — consult 3
   of a 14/day lane. Without a compensating mechanism, raising the ceiling converts an
   `empty_response` failure into a `cap_blocked` failure: same lost advisory, different
   label. §6 S3 is that compensating mechanism, and it is why the spec has three parts
   rather than one.

Cap re-basing is Goran's call and is **not** taken here. What this ruling does is make
the arithmetic visible: at max effort and 14 consults/day, no $2.00 cap can hold this
lane, at any ceiling that works.

---

## 6. SPEC — what the coder implements

Scope: `anchor_exec.py` (+ its tests). No config write, no model change, no rollout in
this unit. Each item is independently testable; S1–S3 are the cure, S4–S6 are the
guarantees that keep it honest.

**S1 — knob.** Add `anchor_chain.max_tokens_consult` (int). `api/commands_config.py`
lane A, `{"type":"int","min":8000,"max":64000}`, surfaced by `/router config`.
**Default is the value the lane uses today** (read from `_budgets.budget("consult")`),
so a profile that does not set the knob behaves exactly as before — backward-compatible
by construction, not by promise.

**S2 — ceiling resolution (per call, before the cap check).**
```
ceiling = min(max_tokens_consult, max_tokens_for_headroom(ep, est_in, headroom_usd))
```
`max_tokens_for_headroom` already exists (`anchor_chain.py:549`) and already prices
against the cap; reuse it, do not write a second estimator. `headroom_usd =
daily_cap_usd − today_spend()`. Honour `MIN_VIABLE_CONSULT_TOKENS = 48000` (S3).

**S3 — cap floor gate (the failure path, designed first).** If `ceiling <
MIN_VIABLE_CONSULT_TOKENS`, **do not make the call.**
```
anchor_consult_skipped reason=cap_floor ceiling=<n> headroom_usd=<x> spend_today=<y>
```
Spend is 0, the turn proceeds without the advisory, the banner shows the skip. Rationale:
below 48,000 the call is a measured burn (43% of consults still burned at 47,170), so
paying for it is buying a known-empty answer. 48,000 is deliberately at the measured
failure point, not below it — it is a floor, not a guess. Knob:
`anchor_chain.min_viable_consult_tokens`, default 48000, same lane.

**S4 — single-shot at the ceiling.** When `ceiling >= max_tokens_consult`, suppress the
M50-02 doubling retry: a retry that sends the same ceiling to a prompt whose failure is
deterministic per prompt (M50-01's finding) buys the same empty answer twice.
```
anchor_budget_retry_suppressed reason=at_ceiling max_tokens=<n>
```
When headroom forces `ceiling < max_tokens_consult`, keep the existing retry **only if**
`ceiling * 2 <= max_tokens_consult`; otherwise suppress. M50-04's failover-skip after a
paid retry stays exactly as it is.

**S5 — payload: what happens to the existing keys.** This is the answer to the brief's
question about `extra_body` / `reasoning_effort`:

- `reasoning_effort` — **unchanged.** Still injected from
  `anchor_chain.reasoning_effort` ("max") at `anchor_exec.py:747-751`, still stripped by
  the minimal-retry arm. Not touched, not lowered (Goran 2026-11-10).
- `extra_body` — **unchanged.** No new keys, no translation.
- `thinking` / `thinking_budget` / `reasoning_budget` — **must never be sent.** The first
  is accepted and quietly bounded by `max_tokens` (cells B/H/M); the other two are 400s.
  A future reader will see `thinking.budget_tokens` in a provider doc and add it; cells
  H and I prove the API accepts an impossible config (`budget_tokens` above `max_tokens`,
  and below Anthropic's 1024 minimum) without complaint, which is a trap, not a feature.
  Enforce with a test: the built payload has no `thinking` key and
  `payload["max_tokens"] == resolved_ceiling`.

**S6 — observability (this is why the next unit will not have to guess).** On every
anchored call, log one line:
```
anchor_consult_billed model=<m> max_tokens=<n> completion_tokens=<n> reasoning_tokens=<n> finish=<r> cost_usd=<x>
```
`usage.completion_tokens_details.reasoning_tokens` was present in **all 17** probe
responses and in the live rows; today nobody records it, which is exactly why M50-01
believed the peak was 17,510 while J1/J2 burned 24,000 twice. Cost of adding it: zero.

**S7 — tests (each maps to one item).**
1. payload shape: no `thinking` key; `max_tokens == ceiling` (S5).
2. ceiling resolution: headroom-limited and knob-limited cases (S2).
3. floor gate: skip logged, **zero spend recorded**, no HTTP call attempted (S3) — assert
   the client is never invoked.
4. retry suppression at ceiling; retry preserved when headroom halves it (S4).
5. `reasoning_tokens` lands in the log line from a canned usage block (S6).
6. backward compatibility: unset knob reproduces today's `_budgets.budget("consult")`
   value exactly (S1).
Existing suite must stay green; the unit reports the before/after red-ID delta.

---

## 7. Verifier appendix — re-runnable, no trust required

```
python3 /opt/data/tmp/r78u/M50-08_probe.py    # cells A-F: field semantics
python3 /opt/data/tmp/r78u/M50-08_probe2.py   # cells G-J: messages path, thinking>max, 24k burns
python3 /opt/data/tmp/r78u/M50-08_probe3.py   # cells K-P: field inventory, 64k acceptance
grep -n "NOUS_BASE\|_LEDGER_FILENAME" /opt/data/plugins/hermes_router/anchor_chain.py
sed -n '261,283p' /opt/data/plugins/hermes_router/anchor_exec.py   # est_out = max_tokens
sed -n '569,577p' /opt/data/plugins/hermes_router/anchor_chain.py  # cap_check
sed -n '184,188p' /opt/data/plugins/hermes_router/api/commands_config.py  # 8000..64000
grep -n "output_per_1m\|daily_cap_usd" /opt/data/profiles/architect/config.yaml
cat /opt/data/profiles/architect/hermes-router-spend.json   # 11-09 = 2.368508
grep -c anchor_route_fired /opt/data/profiles/architect/uncensored-router.log
sed -n '35,42p' /opt/hermes/hermes_cli/proxy/adapters/nous_portal.py  # path allowlist
```

Probe cost of record: **$0.567714** (sum of the `cost` fields across the 13 billed cells
of the 17; F/K/L were 400s and G a 404 — no charge).

---

## 8. Failure paths (every component, named)

| component | fails how | designed response |
|---|---|---|
| ceiling resolution | headroom < floor | S3 skip, spend 0, banner visible — never a silent empty |
| the call at 64,000 | still burns (finish=length, empty) | existing `route_skipped reason=empty_response`, **plus** S6 records the floor reached — next unit sizes the tail from data |
| retry ladder | suppressed at ceiling | `anchor_budget_retry_suppressed`, explicit, not an invisible no-op |
| cap guard | estimate = full ceiling (pessimistic) | S2 resolves the ceiling from real headroom, so the guard blocks nothing it should have allowed |
| provider/transport | down, 5xx, timeout | unchanged fail-open pass-through; zero advisory, turn continues |
| ledger write | disk/JSON error | `record_spend` already never raises (`anchor_chain.py:503-527`); the guard stays fail-open |

---

## 9. Future projections

**1 month.** The lane's true thinking distribution is known for the first time
(S6), and the ceiling question stops being a debate: the p50/p90 of `reasoning_tokens`
either fits under 64,000 or the lane's real answer is a different model tier, not a
bigger ceiling. The $2.00 cap is either re-based on that distribution or the lane's
consult rate drops.

**6 months.** The provider surface moves. `reasoning_tokens` in the usage block is
already the rail: if a separate thinking budget appears (a `/v1/messages` forward, a
`reasoning.max_tokens` that is honoured, a native thinking-token field), the change is
one resolver behind S2, not a rewrite — the spec already isolates "how big is the
allowance" in a single function. The `thinking`-must-never-be-sent test is the guard
against someone bolting on the field that does not work.

**1 year.** The consult lane becomes budget-aware by design: it asks only when the
headroom supports the measured p90, and it says so when it does not. The failure mode
that cost this fleet a year of "the advisory is empty" is replaced by a lane that
degrades on purpose, in the open, with a number on the banner.

---

## 10. Decision log

| id | decision | rationale |
|---|---|---|
| D1 | Option 1 (bound thinking separately) **rejected** | probe-proven impossible: 0/17 requests exceeded `max_tokens`; `/v1/messages` 404; `thinking` accepted but bounded by `max_tokens`; `thinking_budget`/`reasoning_budget` 400 |
| D2 | Option 2 (raise ceiling) **chosen**, at 64,000 | the configurable maximum (`api/commands_config.py:186`), accepted live (cell P); 1.357× over the last failure point |
| D3 | Thinking stays at `reasoning_effort:max` | Goran 2026-11-10 verbatim ruling; the probe's own cells (D/N) show what lowering effort would do and it is not proposed |
| D4 | Single-shot, retry suppressed at ceiling | M50-02's doubling is a no-op once the ceiling is the maximum; a deterministic failure retried at the same ceiling is the same empty answer, paid twice |
| D5 | Cap floor gate added | without it the guard blocks at consult 3 (est_out = max_tokens) and the failure only changes its label |
| D6 | New knob defaults to today's value | backward-compatible by construction; no profile changes silently on install |
| D7 | No config write, no model change, no rollout | unit scope; the canary flip is the next authorised step |

---

## 11. Open items (not this unit's lane)

1. **Canary knob flip** — set `anchor_chain.max_tokens_consult: 64000` on architect only,
   then re-run M50-06's live acceptance to measure the `47,170 → 64,000` band. That band
   is the one number this ruling could not measure from outside.
2. **Cap re-basing (Goran).** $2.00/day is already breached on record ($2.368508 on
   2026-11-09) and cannot hold a 14-consult/day lane at $0.31–0.64 per consult.
3. **A foreign uncommitted file sits in the repo**: `features/detection/domain_gate.py`
   (M49-07's in-flight work, mtime 2026-11-10 14:30:47, no commit anywhere — `git log
   --all --grep=M49-07` is empty). It is not this unit's to commit or stash; it is named
   here because the close verifier will see it. M49-07's owner commits it.
