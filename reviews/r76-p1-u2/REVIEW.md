# REVIEW — R76-P1-U2 (d380db5, 6.13.16) "aux banners price the model that was DISPATCHED"

**Verdict: FIX-FIRST**

Parent reviewed against: `39772dd` (6.13.15). Both trees exported with `git archive`
(parent and child) into `/opt/data/profiles/reviewer/cache/scratch/{parent,child}/hermes_router`;
the repository working tree was not modified.

RECIPIENT EXPERIENCE (G-USE-DONT-READ): ran the real `decision.call_backend()` on the
`nous` path with a stubbed aux client reporting a dispatched id of `MiniMax-M3`, then
rendered the delivered banner and read the cost cell (probe:
`scratch/probe/probe_nous.py`). Also ran the full suite in both trees and the mutation
trees M1 / whole-parent-swap.

---

## 1. The two claimed taps now price the dispatched id — CORRECT

`semantic_classifier.py:496` (`aux_raw_call`) and `decision.py:797` (`score`) both read
`dispatched_aux_model()` and call `estimate_cost` only when it is non-empty:

    _aux_model = dispatched_aux_model()
    (usage_ledger.estimate_cost(_aux_model, _it, _ot) if _aux_model else 0.0)

`_hermes_aux_call` records the model `get_text_auxiliary_client("router")` returned for
that call (`semantic_classifier.py:386` clears at entry, `:393` sets after resolution,
`:422` clears on exception), so a failed/retried call cannot inherit the previous id.
`aux_price_model()` now raises (`semantic_classifier.py:142-155`) and no production caller
survives (pinned by the repo-wide scan test). E1/E2.

## 2. BLOCKING — the same defect class survives on the default decision backend, and the banner still prints a fabricated rate

The commit title asserts aux banners price the model that was DISPATCHED. For the `nous`
backend they do not. That branch puts no model in the request and lets
`get_text_auxiliary_client("router")` pick one, yet records the CONFIG model:

- `decision.py:3023` — `backend = str(cfg.get("backend") or "nous")` (nous is the code default)
- `decision.py:3103` — `meta = dict(meta, model=model, endpoint="hermes-auxiliary", ...)`
  where `model = str(cfg.get("model") or DEFAULTS["model"])`
- `decision.py:3900` `base_row["model"]` inherits it; `decision.py:4072` prices the
  tokens-LEDGER row with it; `decision.py:4101` → `render_payload.py:240` prices the
  parked BANNER with it.
- Same inheritance on the midturn seam: `decision_midturn.py:951`, priced again at
  `:1039` and at `:1493` for the turn-close rollup.

Probe output (E1, child tree at d380db5):

    dispatched_aux_model(): 'MiniMax-M3'      estimate_cost('MiniMax-M3') = 0.0
    meta.model:             z-ai/glm-5.3-flash estimate_cost(cfg model)  = 0.000372
    BANNER: · router · impulse (decision) | pre | z-ai/glm-5.3-flash @ hermes-auxiliary
            | tok 2759/102 | $0.000372 | initiator=user | ledger-row MISSING ·

So the call served by an unpriced model renders a precise `$0.000372` — the exact "zero
removed by fabricating a plausible rate" failure the unit was sent to kill, and it lands
in the money ledger, not only the display. Materiality tier: M1 on correctness of the cost
surface; M2 on live exposure — all 12 fleet profiles currently set
`backend: jev_native` (grep of `/opt/data/profiles/*/config.yaml`), so this branch is
reachable only via cfg omission or explicit `nous`, but it is the code's own default.

Note: the live working tree already carries an UNCOMMITTED 6.13.17 addendum on this exact
site (`decision.py:3100-3113`). It fixes the common case but keeps `_sent_model or model`,
i.e. when the aux client reports no model it prices the configured id again — the honest
zero is still not honoured there.

## 3. Unpriced models still render the honest asterisk on the fixed paths — CORRECT

`debug_banner.py:188-192`: `cost == 0.0` with real tokens renders `$0.0000*` (verified by
direct call: `... | tok 2759/102 | $0.0000* | 1s`), and `estimate_cost` returns 0.0 for
unknown ids (`core/usage_ledger.py:158-193`). The fix does not add any fallback rate; the
taps simply stop calling `estimate_cost` without an id. E1/E2. (The unpriced case is still
unreachable on the nous banner path — that is finding 2, not a renderer defect.)

## 4. The new tests are real pins, but the count claim is wrong and the surviving site is untested

Mutation test (E2). Two mutants built from the child tree:

- **M1** — restore the old shape while keeping the new API: taps price
  `_aux_model or resolve_aux_model(...) or "jev-latest"` / `_aux_model or "jev-latest"`.
  Result: `2 failed, 10 passed` —
  `test_missing_dispatched_model_prices_zero_and_never_a_stand_in` and
  `test_missing_dispatched_model_on_the_decision_path_prices_zero` both fail with
  `AssertionError: ['jev-latest']`. A fabricating implementation cannot pass. Pin is real.
- **Whole-parent-source swap** (parent `semantic_classifier.py` + `decision.py`, child tests):
  12 errors at fixture setup (`_set_dispatched_aux_model` absent). The file cannot pass
  against old code — but note this proves only API-shape coupling, which is why M1 above is
  the load-bearing mutation.

Count claim: the commit says "New pins: 12". The file collects 12 tests total, of which the
parent already had 11; the diff adds 9 test defs and removes 8 → net +1 test (confirmed by
`--collect-only` on both trees and by the suite totals, 2496 vs 2495 passed). No test
exercises `call_backend`/`render_decision_banner` at all (grep for `nous` in `tests/`: zero
hits), which is precisely the hole in finding 2.

## 5. Version bumped in both places — CORRECT

`plugin.yaml:3` → `version: 6.13.16`; `tests/routing/test_r10_catalog_resolution.py:384` →
`assert manifest["version"] == "6.13.16"`; that file passes (24 passed). E2.

## 6. Suite red set is baseline — no new failures

Full suite, `-q -p no:cacheprovider -p no:randomly`, both trees exported from git:

| tree | result |
|---|---|
| d380db5 (child) | **6 failed, 2496 passed, 2 skipped, 1 deselected, 1 xfailed, 2 errors** in 626.74s |
| 39772dd (parent) | **6 failed, 2495 passed, 2 skipped, 1 deselected, 1 xfailed, 2 errors** in 628.64s |

The eight red names are IDENTICAL between trees (set-compared, `NEW IN CHILD: []`):
`test_p05_legacy_purge_guard::test_legacy_mention_allowlist_only_shrinks`,
`test_p4_schema_budgets::test_c_every_get_path_in_schema`,
`test_r33m_refusal_semantics::{test_valmet_f2_channel_distrust_has_no_semantics,
test_valmet_fragments_have_no_semantics}`,
`test_rider20_fixes::{test_d3_hook_budget_clamps_sync_and_revision_sum,
test_d3_a_large_hook_budget_still_hosts_the_fold}`, and the two
`test_rider10_fixes` ERRORs. The reported 7th failure,
`test_r53_midturn_mindset::test_m1_envelope_carries_the_current_stance`, fails identically
in isolation in both trees (1 failed / 16 passed in each) and was green in both full runs —
order-dependent, not a regression. So: same baseline, zero new red; the absolute "7 failed"
count in the commit message is not reproducible in a clean full run (6 failed), which is
consistent with the commit's own flake note but should not be read as a fix effect.

---

## Fix list (numbered, minimal)

1. `decision.py:3103` — after `_sc._hermes_aux_call(...)`, record the id the aux client
   reported (`dispatched_aux_model()`), and carry BOTH a display label and a *price id* in
   meta. Do not let `cfg["model"]` be the price id when the client reported something else.
2. `decision.py:4072` and `decision.py:4101` (plus `decision_midturn.py:1039` and `:1493`) —
   price the price-id; empty → `0.0` and the honest asterisk. Keep the cfg label only as a
   label, never as a rate key.
3. When the aux client reports no model, price `0.0` on this path too — the 6.13.17
   `_sent_model or model` fallback reintroduces the fabricated rate for exactly that case.
4. Add pins for the `call_backend` → `render_decision_banner` path with
   dispatched ≠ cfg (a `nous`-backend test asserting the banner's cost cell and the
   tokens-ledger cost), and confirm they fail against d380db5.
5. Correct the "New pins: 12" claim (net new = 1 test; 12 is the file total).

## Could not verify

Whether any live profile or ledger row actually exercised the `nous` backend and recorded
the fabricated cost — the addendum cites a measured `$0.000372` row, but I found no such
row in the ledgers and all 12 fleet configs set `backend: jev_native`, so live exposure of
finding 2 is unproven (the code path itself is proven by probe).
