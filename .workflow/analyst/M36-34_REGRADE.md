# M36-34 REGRADE — architect at c9099b0: 7P/6F (was 9P/3F); reviewer row owed (probe timeout)

Unit M36-34 · owner analyst=regrade · read-only measurement, no product-code writes.
Repo `/opt/data/plugins/hermes_router` @ `c9099b0` (`git rev-parse HEAD` = c9099b07487032400f7e8050b27b29d525bcfcc4),
only ` M scripts/install.sh` dirty (untouched, out of scope).

## Substrate (what code each gateway actually ran)

Kernel evidence that the architect row measures c9099b0 and not an older tree:

- repo commit: `c9099b0 2026-11-09 13:37:48 +0000 M36-33 F3b: the fallback decides only on a cue; no cue stays abstain`
- deployed copy `/opt/data/profiles/architect/plugins/hermes_router`:
  `sha256(features/detection/domain_gate.py)` = `abf74213cbfb9603f30d957fbb8dc19d1ab872cbdc44b7c3702f2a4de5276069`
  == repo same-path sha (byte-identical); `core/delivery_decision.py` `0ded96c1…` == repo (byte-identical).
- copy mtime `2026-11-09 13:37:59`; gateway pid 171729 `lstart Mon Nov 9 13:38:02 2026`
  → process started **after** the c9099b0 content landed. Measured substrate = c9099b0.

Reviewer substrate is **not** c9099b0, recorded as found:
`sha256(/opt/data/profiles/reviewer/plugins/hermes_router/features/detection/domain_gate.py)` =
`2c7ac5455e4f5c6f9129cc03a34b5df20d34abb0a8d6c7686c7263af1f45d4a3` ≠ repo `abf74213…`;
`core/delivery_decision.py` `7f46ae74…` ≠ repo `0ded96c1…`; copy mtime `2026-11-09 08:56:41`,
gateway pid 118333 `lstart Mon Nov 9 09:23:28 2026` → pre-M36-33 (pre-H1 floor, pre-F3/F3b).

## R1 — ARCHITECT at c9099b0

Command (one invocation, foreground-bound to the gateway):
`python3 benchmarks/battery_run.py --profile architect --out /opt/data/tmp/r78u/M36-34/architect.json --timeout 2400`
Observed: `started_at=2026-11-09T14:15:15Z`, `head_sha=c9099b0`, `battery_exit=0`, `log_freshness=fresh`,
`BATTERY DONE: 15 PASS / 6 FAIL` at turn level; `TOTALS pass=7 fail=6 inconclusive=0 overall=FAIL`.

| scenario | verdict | cause class |
|---|---|---|
| N1_benign | PASS | — (decisions.jsonl:566 lane=main rule_6 delivered=1588) |
| N2_confident_essay | PASS | — (:570 lane=lane) |
| F1_complexity_pre | FAIL | instrumentation gap — `absent_class=not_instrumented_path_not_taken` (anchor_route_fired absent, `pre_no_impact`/`complexity_skip` in window) |
| F2_closure_post | PASS | — (:579 rule_6 delivered=3130) |
| F3_cooldown | PASS | — (:586 rule_6 delivered=4165) |
| U1_render_continuity | FAIL | product-side abstention, **not** F3b-specific — window carries `lane_decision_deferred`/`gate_abstain`/`main_body_kept`; the reviewer row shows the same window on pre-F3 code (see R2), so the domain gate abstained on the fiction ask before F3b existed. Class: instrumentation gap on an abstain path (route arm not reached) |
| U2_double_render | PASS | — (:592 rule_4 delivered=4181) |
| X1_mixed | FAIL | harness bound + instrumentation — `render_not_delivered reason=render_settled_after_host_hook_kill severity=error turn_failed=1 elapsed_s=60.919 render_chars=7200`; the router emitted `rule_route`/`replacement_chain`, never the token `route_fired` the scenario asserts; `absent_class=null` (classifier found no stand-down row) |
| N3_marker_probe | PASS | — (:602 lane=none) |
| D1_on_demand_higher_pre | FAIL | harness bound — `HTTP error: TimeoutError('timed out')` at the battery's 240 s turn timeout; the turn completed server-side: `2026-11-09T14:42:19Z POST event_detail=main_body_kept reason=main_complied body_chars=1447 session_id=api_1794234819_16f59432` |
| D2_on_demand_shadow | PASS | — (:611 rule_6 delivered=1139) |
| D3_cap_denial_banner | FAIL | genuine regression (inverse) — plain ask anchored: `2026-11-09T14:38:06Z PRE event_detail=anchor_route_fired lane=complexity mode=consult model_target=anthropic/claude-sonnet-5.5 reason=risk_r2 content_chars=39 session_id=api_1794235075_f871395f` (second emit 14:41:24Z, anchor_chars=3895) |
| D4_double_declare | FAIL | genuine regression — delivered body is the withhold banner, not an answer: `state.db messages id=16872 role=assistant len=166 '[hermes-router] This turn was withheld: internal routing metadata reached the delivery boundary and could not be removed'` |

Turn-level rows quoted above come from `/opt/data/tmp/r78u/M36-34/architect.json` and
`/opt/data/profiles/architect/uncensored-router.log` / `hermes-router-decisions.jsonl`.

## Delta vs M36-33 (61c4a40 → c9099b0)

Scenario totals moved 9P/3F/1INC → 7P/6F/0INC. Not the same three fails:
F1 and U1 persist; D3 moves from HTTP timeout (unmeasured) to a **measured forbidden fire**; D1 and D4 are new;
X1 is new; D2 recovered.

- **F1** — unchanged instrumentation gap (no code path reachable from this wire).
- **U1** — not changed by F3b: the reviewer row (pre-F3 substrate) shows the identical `gate_abstain` window, so
  the fiction ask abstained before F3b existed. Corrected classification: abstain-path instrumentation gap, not a
  F3b regression. This is the one claim the prior unit made that the reviewer row disproves.
- **D3** — measured, not inferred: `anchor_route_fired … reason=risk_r2 content_chars=39`.
- **D4** — measured: withheld banner delivered (`len=166`), i.e. the delivery boundary still sees internal
  routing metadata on the declared-consult path.
- **X1** — host hook killed the render at 60.9 s (`turn_failed=1`); also an event-token gap (`rule_route` vs
  `route_fired`). Not evidence of a router flip either way.

## R2 — REVIEWER

**Partial row, then timeout.** First invocation aborted before the first turn —
`INCONCLUSIVE: gateway probe failed on http://127.0.0.1:8654/api/sessions (TimeoutError('timed out'))`
(`M36-34/reviewer.log`, run start `2026-11-09T14:49:18Z`). Re-probe at 15:02Z shows the gateway is alive and fast:
`GET_reviewer 200 4.912461s`, `POST /api/sessions 201 0.984271s` — so the abort was a >10 s probe-window miss under
load 6.65, not a dead lane. Cause class: **harness bound** (fixed 10 s probe timeout), aggravated by host load.
Retry at `2026-11-09T14:50:44Z` **produced a partial row** (780 s cap, `battery_exit=124`, `error=TIMEOUT after 780s`,
`overall=INCONCLUSIVE`): **6 PASS / 3 FAIL / 1 INCONCLUSIVE of 10 graded** of 13 scenarios
(`/opt/data/tmp/r78u/M36-34/reviewer.json`).

| reviewer scenario | verdict | note |
|---|---|---|
| N1_benign | PASS | decisions.jsonl:13547 rule_6 delivered=1399 |
| N2_confident_essay | PASS | :13551 rule_6 delivered=4865 |
| F1_complexity_pre | FAIL | `absent_class=not_instrumented_path_not_taken` (same class as architect) |
| F2_closure_post | PASS | :13559 rule_6 delivered=13130 |
| F3_cooldown | PASS | :13563 rule_6 delivered=5151 |
| U1_render_continuity | FAIL | `route_fired` absent, window = `not_a_denial`/`lane_decision_deferred`/`gate_abstain`/`main_body_kept` |
| U2_double_render | PASS | :13569 rule_4 delivered=4315 |
| X1_mixed | FAIL | `route_fired` absent, `absent_class=null`, T1 72 s |
| N3_marker_probe | PASS | :13577 lane=none |
| D1_on_demand_higher_pre | INCONCLUSIVE | `NO_TURNS_RECORDED` — 780 s cap reached before the turn was issued |
| D2, D3, D4 | not reached | harness cap, not graded |

Causes: F1/U1 = instrumentation gap; X1 = unclassified absence (classifier found no stand-down row) plus 72 s T1;
D1 = harness bound (timeout). None of the three reviewer fails is a measured product regression, and all three are
the same two shapes that appear on architect.

Substrate caveat, load-bearing: the deployed reviewer plugin is pre-M36-33 (sha evidence above), so this row grades
code **older** than c9099b0 even though `head_sha` in the JSON says c9099b0 (that field reads the repo, not the
deployed copy — a measurement-validity gap in `battery_run.head_sha()` worth a unit of its own).

## R3 — FAIL causes, rolled up

| class | scenarios |
|---|---|
| instrumentation gap (`not_instrumented_path_not_taken`, no reachable emit arm / token mismatch) | F1, X1 (token), U1 (partly) |
| harness bound (client timeout / host-hook kill / probe window) | D1 architect (240 s turn timeout, turn finished at 14:42:19Z), D1 reviewer (unreached at 780 s cap), X1 (host hook @60.9 s; reviewer X1 72 s T1), reviewer first probe (10 s) |
| genuine regression, row quoted | D3 (`anchor_route_fired … content_chars=39`), D4 (`withheld … len=166`) |

## R4 — Fleet verdict (real rows only)

- ARCHITECT @ c9099b0 (measured on the deployed copy that contains F3b) — **FAIL**, 7P/6F/0INC of 13.
- REVIEWER — **INCONCLUSIVE**, 6P/3F/1INC of 10 graded, cut at the 780 s harness cap; substrate is pre-M36-33, so
  the row is a comparison point, not a c9099b0 grade.
- FLEET — **not green**: one measured FAIL row. F3/F3b did not clear the architect lane. What it changed is the
  failure mode: 9P/3F/1INC at 61c4a40 → 7P/6F/0INC at c9099b0, with D3 now measured (forbidden fire on a 39-char
  plain ask) and D4 measured (withheld body), while D1/D4/X1 losses on architect are harness (timeouts, host-hook
  kill) and only D3/D4 are product rows. No evidence F3b caused any of the six.

## R5 — Load, no gate

- architect run start `2026-11-09T14:15:09Z` loadavg `5.15 5.85 6.04 2/4730`.
- reviewer run start `2026-11-09T14:49:18Z` loadavg `6.65 7.24 6.84 3/4740`; retry `reviewer_load_at_retry.txt`.
- No load gate applied anywhere: both runs were started at whatever load the host had, with a fixed
  `--timeout` (2400 s architect, 780 s reviewer retry).

## R6 — One battery at a time

`ps -eo pid,args | grep battery_run` before each launch: empty (only the grep itself).
- architect pid 175250: started 14:15:05Z, `battery_exit=0` at ~14:48Z (completed, not killed).
- reviewer invocation #1 pid 178287: 14:49:18Z → aborted at probe, no turns.
- reviewer retry pid 178488: 14:50:44Z → cut at the 780 s cap, `battery_exit=124`.
Every pair of invocations was separated by a full process exit, so no two batteries ever overlapped, on any
gateway; and at no point were two batteries run against one profile.

## NOT CHECKED

- Reviewer at c9099b0 — would settle whether the F3b fallback behaves the same on a consult-heavy profile; the deployed
  reviewer copy is pre-M36-33, so this unit cannot reach it without a deploy + restart (out of the read-only mandate).
- Whether D4's withheld body is a marker-strip bug in `core/delivery_decision.py` or a declaration-contract
  interaction — would settle the cause class of D4; this unit recorded the delivered body only.

## Close-check state (child_close_verify)

`python3 /opt/data/profiles/orchestrator/scripts/child_close_verify.py --unit M36-34 --artefact /opt/data/tmp/r78u/M36-34_REGRADE.md`
at 15:04Z reported NOT CLOSABLE on two lines: `UNCOMMITTED in /opt/data/plugins/hermes_router:  M scripts/install.sh`
and `no commit in the last 15 mentioning M36-34`.

Fixed: the unit commit `eb35df5` (artefact synced to `.workflow/analyst/M36-34_REGRADE.md`, explicit path only,
no product file touched). Repo HEAD was `c9099b0` during both battery runs; the measurement sha is in both JSONs.

NOT fixed, deliberately: ` M scripts/install.sh` is foreign pre-existing dirt (already parked as
`M36-33/install.sh.foreign.bak`, `M36-33/install_park.txt`) and this unit's mandate says "Leave scripts/install.sh
alone". Staging/committing or `git checkout --`-ing another unit's dirty file on a shared tree is the collision
path M36-33 already recorded. Action needed from the unit that owns install.sh: commit it or restore it.
