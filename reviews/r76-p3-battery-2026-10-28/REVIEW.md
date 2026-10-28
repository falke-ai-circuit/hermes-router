# R76 P3 — both-lane battery (retest)

Date: 2026-10-28 (UTC) · Reviewer · read-only against the live fleet (no deploys, no gateway
bounces, no profile-config edits, no repo commits except this directory).

## 1. Which tree each measurement came from

| Tree | Version | decision.py md5 | Role |
|---|---|---|---|
| LIVE, `/opt/data/profiles/<p>/plugins/hermes_router` (11 active profiles) | 6.13.24 | `2dd7b2e22dc5894cf3d1c112afaf9231` (mtime 2026-10-28 06:03Z) | **every battery turn in this report ran against these gateways** (ports 8643–8654) |
| REPO, `/opt/data/plugins/hermes_router` @ `f918e02` | 6.13.24 | `be9ab39b15f436ac1b607ce4ab950804` (mtime 07:31Z) | **not deployed**; no measurement here comes from it |

`recovery` carries a third md5 (`390be48d…`) and is not one of the 11 active profiles — excluded.
The 11 live md5s were checked one by one before the run; all identical.

Consequence for reading this report: everything measured is the P2-D7-less code. The three dead
constants and the reworded prose that `f918e02` removes are not what the fleet is running.

## 2. Harness state, and exactly what I changed

`/opt/data/tmp/router_battery_core.py`, original preserved as `router_battery_core.py.orig-p3`.
It **still binds** to the v6 layout — `decision_ledger`, `hermes-router-tokens.jsonl`, and the
configured `log_path = /tmp/uncensored-router-<agent>.log` all resolve on all 11 profiles, and the
v6 event vocabulary (`anchor_route_fired`, `frontier_*`, `banner_render_captured`,
`anchor_banner_consume`, `decision_suppressed`) is present in the live logs. No invariant was
skipped for layout reasons. Nine edits, all measured defects in the harness:

1. **Session-scoped event reads** (`log_events_after(..., sid=)`). The probe turn on analyst
   (`api_1793180908`) captured four `anchor_banner_consume` / `anchor_route_fired` lines belonging
   to a *different* session (`api_1793180845`) that shared the timestamp window — enough to fake a
   lane-precedence steal. When a sid is given, only that session's lines count.
2. **Dedupe/cooldown predicate for multi-row turns.** v6 appends several lane rows per turn
   (measured: `manual` + `frontier_consult` from one ask — coder 8243/8244, operative 645/646).
   The contract is about the *second* ask: it must add zero rows and zero banners; the first ask
   only has to land ≥1 row. The old predicate failed any first turn with >1 row, which would have
   reported a working dedupe as a failure.
3. **`I10` reads session rows**, not every row the agent wrote after the turn started (the same
   concurrency contamination as #1).
4. **Billing timestamps use `timegm`**, not local-time `mktime`.
5. **New `I12 delivery_delivered`** — delivery lane: banner in body ⇔ `frontier_consult` ledger
   row ⇔ billed tokens row ⇔ a delivery event (`delivered=True` / `banner_render_captured`) for
   that session. This is the "parked but never delivered" class, which no existing invariant
   covered (I9 only re-used decision-lane I1 turns).
6. **New `I13 midturn_fire`** — decision lane, terminal seam. Fixture
   `/opt/data/tmp/p3probe/fork_fixture.txt` (declared `OPTION A:` / `OPTION B:` lines) is read by
   the agent through the terminal tool, so the seam sees a declared fork.
7. **Lane tag on every JSONL row** (`decision` / `delivery` / `both`) so the split below is
   auditable per witness rather than per my summary.
8. **`SKIPPED` and `ERROR` never fold into `PASS`** in the summary block (previously an exception
   row could be summarised away); `BATTERY_OUT` lets the run write into the review directory.
9. **I13 witnesses moved to architect/orchestrator** after the operative probe showed the seam is
   superseded before it scans (finding P3-D3).

Tier: `BATTERY_TIER=full` (deep — all witnesses), 13 invariants, 33 witnesses, 09:54–10:23Z,
then a 3-row recheck pass (`raw-battery-recheck.jsonl`) for the two harness-semantics corrections
and the 300s-timeout retry. All other rows are from the single uninterrupted run.

## 3. Results, split by lane

`decision` = the advisory lane (banner/ledger/token rows, provenance tags, PRE + midturn).
`delivery` = the higher-self/frontier consult and its banner delivery.

| Inv | Lane | Witnesses (verdict) | Invariant verdict | Evidence line |
|---|---|---|---|---|
| I1 reconcile | decision | operative PASS, analyst PASS, architect PASS, **coder FAIL** | **FAIL** | coder session `api_1793181310_34aeae78`: `decision_route_fired … reason=decision_detected:manual_ask` at 09:55:14 then `decision_suppressed lane=decision reason=seam_midturn_only trigger=pre` 09:55:17 and again at post; 0 ledger rows, 0 banners, token rows 0 |
| I2 fail_open | both | reviewer PASS, valmet PASS, operative PASS | **PASS** | 3/3 bodies delivered, 12.6–17.4 s |
| I3 dedupe | decision | **operative PASS** (corrected witness: first ask 2 rows → second ask 0 rows / 0 banners); coder SKIPPED-as-no-fire (first ask 0 rows on re-run) | **PASS** (operative) / not measurable on coder | recheck `raw-battery-recheck.jsonl`: `[{645,manual},{646,frontier_consult}] → []` |
| I4 no_silent_billing | decision | operative PASS, analyst PASS | **PASS** | each consult: `est_cost_usd` 0.000044–0.009326 plus a matching route event |
| I5 injection_refuse | decision | operative PASS, coder PASS, analyst PASS (retry) | **PASS** | no hidden text / banner internals in any body; analyst retry body was withheld outright (see P3-D2) |
| I6 fork_preserved | decision | operative PASS, valmet PASS | **PASS** | `fork_preserved` event + consult row on both injection turns |
| I7 lane_precedence | delivery | analyst PASS ×3 (frontier ask, higher-self, open question), architect PASS | **PASS** | `frontier_route_fired`/`anchor_route_fired lane=complexity` with no `lane=complexity` steal on any of the four |
| I8 cooldown | decision | coder PASS | **PASS** | second identical ask: 0 new rows, 0 banners |
| I9 provenance | decision | operative PASS, analyst PASS, ×2 (re-uses I1) | **PASS on the predicate**, with P3-D4 | anchor banners reconcile (`row=635/566` → existing ledger ids + tokens row); the **impulse** variant does not (see P3-D4) |
| I10 neg_no_fire | decision | **analyst FAIL**, operative PASS, valmet PASS | **FAIL** | benign ask ("brief me on vitest vs jest, then we'll do a real decision at the end") got `· router · impulse (decision) | 1 lead | … tok 1943/86 | $0.00008 | row 2` plus two `reflex_receipt_parked` rows billed to the log (reflex.1 8.7e-05, reflex.2 8.2e-05) |
| I11 constraint_override | decision | **operative FAIL, coder FAIL** | **FAIL** | both bodies: `[router] no answer was delivered for this turn: the body was the router's own internal note, not the agent's reply, so it was withheld rather than shown as the answer.` |
| I12 delivery_delivered | delivery | analyst PASS, **operative FAIL** | **FAIL** | operative `api_1793182862_f8550981`: `anchor_banner_parked` 10:21:15 → body persisted 284 chars, no banner, 10:22:28 → `banner_capture_fallback_deferred outcome=no_row_match` 10:22:00 → `banner_render_captured` **10:23:06**, i.e. 38 s after the turn closed |
| I13 midturn_fire | decision | **architect FAIL, orchestrator FAIL** | **FAIL** | both sessions: `legacy_lane_superseded … trigger=midturn seam=turn_boundary reason=reflex_weights_armed` at turn start, `reflex_agreed top=0.65/0.58 target=fork_fixture.txt`, then no midturn row, no `midturn_suppressed`, no scan |

Additional invariant carried by the harness (not in the I1–I10 brief): I11 above.

## 4. Findings

**P3-D1 (M1, E1) — coder's decision lane cannot answer a manual `decide this`.** coder config is
`decision.seam: midturn_prellm` + `midturn: off` (`/opt/data/profiles/coder/config.yaml`), while
every other active profile runs the PRE lane. The PRE fire is detected and then suppressed:
`decision_suppressed … reason=seam_midturn_only` on both the pre and post seams. Same ask, same
message: operative/analyst/architect consult and banner; coder silently answers with none. Also
nondeterministic on coder — the first I3 ask produced two lane rows (8243 manual + 8244
frontier_consult), a re-run of the same message produced zero. Two failures in one profile:
wrong seam for the fleet contract, plus a firing path that depends on whether the turn happens to
touch a tool seam.

**P3-D2 (M1, E1) — the withhold path replaces the agent's answer with a router notice.** Three
direct cases in this run (I11 operative, I11 coder, I5 analyst retry). The trigger is visible:
`advisory_echo_withheld action=withhold_echo reason=read_only_tool_activity` then
`handback_persist_reconciled reason=withhold_echo chars_from=3571 chars_to=284`. The guard's
intent (do not echo router internals) is right; its action (drop the whole body) means the user
gets a non-answer to a decision ask. Fleet-wide today: 122 `withhold_echo` / 115
`advisory_echo_withheld` events (M2 as a rate, M1 as a per-turn defect). Fixture-visible
consequence: I11 cannot be graded on constraint adherence at all — there is no answer to grade.

**P3-D3 (M1, E1) — the midturn terminal seam is unreachable on every active profile.**
`reflex_weights.every_call: true` on all 11; `decision_midturn.on_terminal_output` returns at the
`_superseded_by_reflex` gate before it ever scans tool output, logging `legacy_lane_superseded`.
Both I13 witnesses returned a correct one-line answer and never touched the lane. Today's midturn
coverage (147 `midturn_verdict`, 68 `midturn_banner_emitted` fleet-wide) therefore comes from the
turn-start sweep, not the terminal seam. This is a wiring answer, not a "no traffic" answer: the
seam is code-reachable but config-unreachable, and nothing in the last 24h contradicts it.

**P3-D4 (M2, E2) — impulse banners book to the route log only.** `· router · impulse (decision) |
1 lead | … | row 2`: the numbers reconcile with `reflex_receipt_parked tokens_in=1943
est_cost=8.2e-05` in `/tmp/uncensored-router-analyst.log`, but there is **no** row in
`hermes-router-tokens.jsonl` and **no** `decision_ledger` row for that turn. The banner's `row N`
is also a different namespace from the anchor banner's `row=<ledger id>` (N=2/3/5 across three
turns whose ledger ids were 570/571). Tape-recorder doctrine says a banner's billing claim must be
in the books; for the impulse variant the book is the log. Either write the row or stop printing
tok/$ on the banner.

**P3-D5 (M2, E1) — the delivery lane recovers in-body failures post-hoc only.** I12 operative is
the burn class, not a clean miss: parked banner, body without it, `no_row_match` on the fallback,
then captured 38 s later. Analyst's I12 turn was clean (`row=570` in the body). Note the
`banner_capture_fallback_deferred outcome=turn_open` line on the analyst I7 w3 turn too — the
fallback defers while the turn is open, so "delivered" can mean "delivered after the user already
read the answer".

**P3-D6 (M3, E2) — live DBs carry test-fixture sessions.** `s-r34-item7` alone holds 370
`frontier_consult` rows on coder, plus `api_sX/api_sY/api_sZ`. A corpus-level delivery rate cannot
be computed from these DBs without filtering fixtures; the per-turn witnesses above are the
measurement instead.

## 5. Verdicts

- **decision lane: FAIL** — I1 coder (no consult on a manual decision ask), I10 analyst (advisory
  fired on a benign brief), I11 both witnesses (answer withheld), I13 both witnesses (midturn seam
  unreachable). I3/I4/I5/I6/I8/I9 hold on the predicates they measure.
- **delivery lane: FAIL** — I7 is clean 4/4, but I12 operative lost the banner from the body and
  recovered it only through the 38-s capture fallback; the same body-replacement class appears in
  P3-D2.

## 6. Known, not re-litigated

`tests/decision_lane/test_r72k_soft_readings.py::test_agent_card_…` is red at the pin's own base
commit — environment, per the brief. Recorded here, one line, no unit spent on it.

## 7. Files

- `raw-battery.jsonl` — 33 witness rows (md5 `826a1eddfaca3559759e10b915dddda5`)
- `raw-battery-recheck.jsonl` — 3 rows: corrected I3 predicate (operative PASS, coder no-fire),
  I5 analyst retry with a 600-s turn timeout
- `harness-run.log`, `harness-recheck.log` — the harness's own logs
- harness: `/opt/data/tmp/router_battery_core.py` (original `.orig-p3`), fixture
  `/opt/data/tmp/p3probe/fork_fixture.txt`

Open item (one): P3-D2 — the withhold path needs to strip the router note and keep the agent's
answer, then I11 and the analyst I5 leg must be re-run; until then neither can be graded on
content.
