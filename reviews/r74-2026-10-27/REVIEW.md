# R74 — one banner per turn · verification review

- **Deliverable:** `/opt/data/plugins/hermes_router` @ `be228c8` "R74 (6.13.9): one banner per turn" (tree clean at review start)
- **Spec:** `/opt/data/tmp/r74_banner_aggregation.md`
- **Reviewer:** reviewer — verification leg only. No code, test, or config edits were made; the probe file lives outside the repo's test tree.
- **Verdict: FIX-FIRST** — the four acceptance pins pass and the change set is scoped exactly as required, but the acceptance criterion *"a second turn does not inherit the first turn's counts"* fails in the never-consumed-turn path (F1). One non-blocking edge (F2).

---

## Verdict table

| # | Item | Method | Evidence | Result |
|---|------|--------|----------|--------|
| 1a | 3 fires → one line, x3, summed tok/cost, row range | suite pin + independent re-drive | `x3 | 2 leads · 1 no-lead … tok 6134/280 … row=reflex.1-3`, one segment, one delivered line | PASS |
| 1b | 1 fire → one line, no xN, byte-comparable shape | suite pin + formatter comparison | `… (decision) | every_call | jev-latest @ ? | tok 1330/37 | $0.0000* | initiator=router | row=reflex.1` | PASS |
| 1c | accumulator resets after consume | suite pin + `_SEQ`/slot inspection | turn 2 → `tok 10/2 … row=reflex.4`, no xN | PASS (delivered path) |
| 1d | ownership-claimed turn drops the summary | suite pin + claim seam | parked→claim True→slot `None`→next fire `''` | PASS |
| 2a | `test_r73_p1_receipt.py` edit not a weakening | full file read + diff | 9 receipt assertions retained (lane, trigger, model, tokens, cost, row, sid, banner, parked); only the task-id pin changed | PASS |
| 2b | `test_r10_catalog_resolution.py` pin not diluted | diff + repo-wide grep | one literal, `6.13.8`→`6.13.9`; only 2 version literals in tree | PASS |
| 3 | No lifecycle/kind edits | `git show --stat be228c8` | 5 files: `reflex_lane.py`, `plugin.yaml`, 3 test files | PASS |
| 4 | Two out-of-list failures pre-existing | scratch worktree `fb6276e`, same node ids | identical failures on parent commit | PASS |
| 5 | Invariants (banner outside body, body intact, fail-open) | delivery text + edge source read + probe P7 | `BODY` preserved; exceptions → `''` | PASS |
| 6a | `MAX_BANNER_CHARS` 700 vs spec 400 | source + behaviour | `debug_banner.py:55-61` history 400→480→700, `format_banner:199`, `park_anchor_banner:379` | PASS — deviation correct, spec stale |
| 6b | Reset not at the consume seam | probes P1–P4 | delivered-turn path resets (P4); never-consumed path inherits (P1) | **FAIL vs acceptance** → F1 |
| 7 | Live multi-fire turn | fleet route logs, real session ids only | zero real reflex fires after the 07:58Z deploy | NOT AVAILABLE — stated, not simulated |
| 8 | Full suite | `pytest tests -q -p no:randomly --tb=no` | `6 failed, 2481 passed, 2 skipped, 1 deselected, 1 xfailed, 2 errors in 325.57s` | reported |

---

## 1. The four pins, re-executed

Suite run, from disk: `PYTHONPATH=/opt/data/plugins python -m pytest tests/decision_lane/test_r74_banner_aggregation.py -q` → `4 passed in 0.56s`.

Each pin was then re-driven independently (same modules, separate script) so the evidence is the rendered string, not the test's own verdict:

**(a) 3 fires, one turn**

```
· router · impulse (decision) x3 | 2 leads · 1 no-lead | jev-latest @ ? | tok 6134/280 | $0.0000* | initiator=router | row=reflex.1-3
```

`_ANCHOR_SEGS[sid]` holds exactly 1 segment (constant task id engaged the R9d replace path). Delivery edge `LIFECYCLE.deliver(sid, "BODY", "anchor", "benign")` returned `BODY` plus exactly that one line. Tokens 1000+2000+3134 = 6134, out 100+80+100 = 280 — summed, not averaged. **PASS.**

**(b) 1 fire, one turn**

```
· router · impulse (decision) | every_call | jev-latest @ ? | tok 1330/37 | $0.0000* | initiator=router | row=reflex.1
```

No `x1` marker, no `leads` clause. Field order and text identical to the parent commit's formatter output for the same inputs; the only delta is the duplicate `row=` suffix treatment — the parent emitted `… | row=reflex.N` with `task_id="reflex|N"`, the commit emits the same text with `task_id="reflex"`. Byte-comparable as specified. **PASS.**

**(c) reset after consume**

Turn 1: three fires → `… x3 | 3 leads · 0 no-lead | … | tok 6000/150 | … | row=reflex.1-3`; `consume_parked_banner` returns truthy and empties the slot. Turn 2, one fire → `· router · impulse (decision) | every_call | jev-latest @ ? | tok 10/2 | $0.0000* | initiator=router | row=reflex.4` — no inherited counts, no `x`. **PASS.** (Caveat: the pin exercises the consume function directly, not a real delivery edge; see F1 for the path it does not cover.)

**(d) ownership-claimed turn**

3 fires → slot populated; `claim_banner_ownership(sid, edge="uncensored-render")` → `True` and slot `None`; the following fire in the same turn returns `''` and re-parks nothing. No double banner. **PASS.**

---

## 2. The two test edits

**`tests/decision_lane/test_r73_p1_receipt.py`** — the changed line is the last assertion only:

```python
-    assert task_id.startswith("reflex|")
+    assert task_id == "reflex"  # R74: constant id -> R9d replace, one banner per turn
```

The test still asserts the six receipt fields (`impulse (decision)`, trigger, model, `tok 1330/37`, `$`, `row=reflex.`), plus the shared banner family (`banner.startswith(db.BANNER_HEAD)`) and the no-stub-on-refusal contract (`format_banner → ""` ⇒ `_receipt` returns `""` and parks nothing). The new assertion is the mechanism pin for this fix (constant id is what makes replace fire), not a tautology over whatever the code emits — but note it is a *mechanism* assertion, not a *receipt* invariant; the receipt invariants are unchanged and intact. **PASS.**

**`tests/routing/test_r10_catalog_resolution.py`** — one literal, `6.13.8` → `6.13.9`, inside `test_version_bumped()` only; the manifest load and assertion shape are untouched. Repo-wide, the version literal now exists in exactly two places (`plugin.yaml:3`, this test:384), consistent. **PASS.**

---

## 3. Scope

```
 plugin.yaml                                        |   2 +-
 reflex_lane.py                                     | 232 ++++++++++++++++++++-
 tests/decision_lane/test_r73_p1_receipt.py         |   2 +-
 tests/decision_lane/test_r74_banner_aggregation.py | 124 ++++++++++++++
 tests/routing/test_r10_catalog_resolution.py       |   2 +-
 5 files changed, 350 insertions(+), 12 deletions(-)
```

No touch to `features/banners/lifecycle.py` or `features/banners/kinds.py`. **PASS.**

---

## 4. Independent baseline check of the two out-of-list failures

Scratch worktree at `fb6276e` (`/opt/data/profiles/reviewer/cache/scratch/r74base/hermes_router`), same node ids:

```
FAILED tests/classification/test_p05_legacy_purge_guard.py::test_legacy_mention_allowlist_only_shrinks
FAILED tests/config/test_p4_schema_budgets.py::test_c_every_get_path_in_schema
2 failed in 0.44s
```

`test_c_every_get_path_in_schema` fails with the identical assertion on both commits: `config paths not in SCHEMA: ['closure_pass.py: closure_pass']`. `test_p05…` fails with the same allowlist assertion. Both are pre-existing at the parent commit; the commit did not break them. **PASS (E2 — re-derived, not the coder's claim).**

---

## 5. Invariants

- **Banner outside the body:** delivery returned `BODY` + the single banner fragment; the body prefix is untouched (`delivered.startswith("BODY")`). The append happens only at the delivery edges (`gate/orchestration.py:718`, and the canonical commit precedes the append), and `format_banner` output is one line, so no multi-line content can leak into the body. **PASS.**
- **Fail-open:** probe P7 monkeypatches `_park_summary` and `_acc_update` to raise; `_receipt` returns `''` in both cases without raising, so the delivery caller takes the no-banner path. Source agrees: the whole receipt body is inside `try/except → return ""`. **PASS.**
- **Cap:** on long triggers (P5, 200-char seam) and two distinct long seams (P6), the parked segment stays under 700 (`len=133`, `len=234`), the trigger list is truncated *before* the numbers, and `x2`/`tok`/`row=reflex.1-2` survive. **PASS.**

---

## 6. Rulings on the coder's declared deviations

**6a. `MAX_BANNER_CHARS` is 700, not the spec's 400 — the deviation is correct.**
`debug_banner.py:55-61` documents the deliberate history (400 → 480 → 700), with the reason: the impulse frame with weights + evidence + band + ADVICE-ONLY caveat runs ~500-560 chars and the cap exists so the provenance line is never truncated off. `format_banner` (line 199) and `park_anchor_banner` (line 379) both clip at the live constant. Hard-coding 400 would have truncated exactly the frames the constant was raised to protect, and would have made the summary's own numbers the first thing cut. **Ruling: accepted; the spec text (400) is stale and should be corrected at its source.**

**6b. Reset keyed on the parked slot being empty at the next fire, not at the consume seam — accepted as an implementation choice, but it does not fully secure the acceptance criterion.**
Probes (`reviews/r74-2026-10-27/probe_reset_seam.py`):

- **P4 (stale reset cannot clear a live turn's counts): PASS.** Turn 1 delivered via consume; turn 2's two fires → `x2 | 2 leads · 0 no-lead | tok 18/2 | row=reflex.2-3`. The reset is per-session, applied only at that session's next fire; there is no timer, no sweep, and no cross-session touch.
- **P3 (another session's consume does not clear live counts): PASS.**
- **P1 (second turn must not inherit the first turn's counts): FAIL.** Turn 1 fires twice and its parked banner is *never consumed* (a turn that ends without a delivery edge — the repo itself documents this live class: `gate/orchestration.py:686-694` "the banner parked past its own turn", analyst T2e). Turn 2 fires once and the delivered summary is:

```
· router · impulse (decision) x3 | 3 leads · 0 no-lead | jev-latest @ ? | tok 2007/203 | $0.0000* | initiator=router | row=reflex.1-3
```

  Turn 2's line claims 3 fires and 2007/203 tokens when it fired once, and the row range spans two turns. The precondition is exactly the one this design cannot see: `_slot_live()` stays true because turn 1's text is still in the slot, so the accumulator is never rolled.
- **P2 (turn ledger absent, `turn_no == 0`): FAIL, low reachability.** Fire → consume → fire again re-parks a second banner in the same turn (`row=reflex.2`), because the "same turn, already delivered" branch requires a non-zero turn number.

Important: the spec's own §3.4 design (clear at consume) has the *same* hole — if no consume happens, no reset happens. So F1 is a **spec-level gap surfaced by the implementation's probe-based reset**, not a coder deviation from the letter of §3.4. It is nonetheless a breach of the acceptance wording, and the cheapest correct fix is to roll on turn identity (`acc["turn_no"] != turn`) as well as on slot emptiness.

---

## 7. Real-world evidence: no live multi-fire turn exists yet

Deploy landed 07:58:46Z (`plugin.yaml` 6.13.9, file mtime 07:58). Fleet route logs were searched for real sessions:

- `grep -h reflex_receipt_parked /opt/data/profiles/*/uncensored-router.log | grep fires=` returns 21 lines, **all** with `session_id=r74-probe`, `ev-a`, `ev-b`, `ev-c`, `ev-d` — these are my own probe runs (see disclosure below), not fleet traffic.
- Real-session reflex activity stops at 07:49:57Z (`reflex_weights_delivered … session_id=20260813_160517_d56f96a7`), i.e. before the deploy; the last real session showed 5 fires in one conductor turn but on 6.13.8.
- After 07:58Z, zero non-probe reflex events of any kind in any profile's route log.

**There is no delivered 6.13.9 turn with 2+ reflex fires to inspect yet.** The live acceptance line cannot be produced before deploy; I am not substituting a simulated turn for it.

**Disclosure (M3):** my probes emit real `reflex_receipt_parked` route events, and those 21 lines are now in `/opt/data/profiles/reviewer/uncensored-router.log` under session ids `r74-probe`, `ev-a`…`ev-d`. They are review artefacts, not traffic. I left them in place rather than editing a tape-recorder log; any later audit should filter those ids out.

---

## 8. Full suite

Two runs from the same tree, `PYTHONPATH=/opt/data/plugins`:

- Run 1: `6 failed, 2481 passed, 2 skipped, 1 deselected, 1 xfailed, 2 warnings, 3 errors in 347.35s`
- Run 2 (`--tb=no`, log `/tmp/r74_full_be228c8.log`): `6 failed, 2481 passed, 2 skipped, 1 deselected, 1 xfailed, 2 warnings, 2 errors in 325.57s`

Failures: `test_p05_legacy_purge_guard::test_legacy_mention_allowlist_only_shrinks`, `test_p4_schema_budgets::test_c_every_get_path_in_schema`, `test_r33m_refusal_semantics` (2), `test_rider20_fixes::test_d3_hook_budget_clamps_sync_and_revision_sum`, `test_rider20_fixes::test_d3_a_large_hook_budget_still_hosts_the_fold`.
Errors: `test_rider10_fixes::test_r10_2_audit_banner_carries_row_ref_after_persist`, `test_r10_2_meta_carries_row_for_downgraded_async_banner` — both are the conftest egress-tripwire at teardown (`socket.connect` to `ollama.com` / `inference-api.nousresearch.com`), reproduced identically on `fb6276e` at the parent commit.

The third error in run 1 (`test_r19_decision_lane_v3::test_midturn_declared_claim_runs_lane`) did not reproduce in run 2 and passes in isolation; the `test_r19_decision_lane_v3.py` file is environment/order-flaky on both commits (parent: `52 passed, 1 error` once, `1 failed, 51 passed` on a re-run with a different node failing). Baseline arithmetic is consistent with the spec's stated baseline: 2481 passed − 4 new R74 pins = 2477 ≈ the stated ~2477, with the same 6 failures / 2 errors.

**Coder's reported counts are not exactly reproducible:** they claimed `2480 passed / 7 failed / 2 errors`; both of my runs give `2481 passed / 6 failed / 2 errors`. The delta is one flaky r19 test, not an R74 defect, but the close report should carry the reproducible numbers.

---

## Findings

**F1 (blocking, M1 — acceptance criterion breached): counts and row range cross turns when a turn's parked banner is never consumed.**
Evidence: probe P1 → turn 2 delivers `x3 … tok 2007/203 | row=reflex.1-3` for a single fire. Precondition is a real live class (the repo's own comment at `gate/orchestration.py:686-694` describes banners parked past their turn). Minimal fix direction (for the coder, not applied here): roll the accumulator when `acc["turn_no"] != current turn` in addition to the slot-emptiness probe; the turn number is already computed and available. Note the spec's §3.4 design has the same gap — return it to the spec owner as well.

**F2 (non-blocking, M2 — edge): with no turn ledger (`turn_no == 0`) a fire after the delivery edge re-parks inside the same turn** (probe P2 → `row=reflex.2` after consume). The PRE seam calls `note_turn_open` for user-message turns, so reachability is limited to surfaces without a turn record; the same turn-identity roll would cover it only if the turn number is known.

**N1 (process, M3):** the live proof in §7 cannot exist until the deploy sees a real multi-fire turn; the close report must not claim it.

---

## What was verified vs what remains

Verified from disk and re-execution (E1/E2): the four pins, the two test edits, the change scope, the pre-existing status of both out-of-list failures, the cap behaviour, the fail-open paths, the reset behaviour in the delivered-turn path, the full suite counts.

Not verified (and not claimed): a live 6.13.9 multi-fire delivered turn — no such turn exists yet.

Refs: probe file + suite log paths above; suite `tests/decision_lane/test_r74_banner_aggregation.py` (4 passed).
