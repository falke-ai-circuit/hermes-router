# R76-P1-U2 — aux banners price the model that was DISPATCHED

**Commit:** `d380db5` (6.13.16) · parent `39772dd` (6.13.15) · repo `/opt/data/plugins/hermes_router`
**Reviewed:** 2026-10-27 · reviewer lane (verdict only, no implementation)
**Verdict: FIX-FIRST** — the two named aux taps are genuinely fixed and honestly behave as claimed; but the
commit's own headline ("aux banners price the model that was DISPATCHED") is false for a THIRD aux-priced
banner — the decision lane's default `backend: nous` — which still bills a configured model at **$0.000372**
for a call dispatched to `reflection/beam-1.0-501b-a23b:free` (true rate **$0.0**). Same defect class
("fabricated rate over honest zero"), measured live, at `decision.py:3103` → `:3900` → `:4072`.

> Working-tree warning: the shared checkout went **dirty mid-review** (an uncommitted 6.13.17 addendum at
> `decision.py:3103`, `plugin.yaml`, `test_r10`, `test_r75`, mtimes 13:33–13:34Z). Every number below was
> re-taken in a **clean `git worktree` at `d380db5`**; the in-flight patch is not part of this verdict.
> Post-review note (13:58Z): the tree moved on to `bc1a499` (6.13.17, nous branch + pins) and `c094cba`
> (6.13.18, impulse banner) — D1's first half is now committed, but `decision.py:3110-3111` still reads
> `meta = dict(meta, model=_sent_model or model, …)`, so the `0.0` half of D1/§2 and D2 remain open at HEAD.

---

## Item-by-item

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 1 | Both aux sites price the model the call was ACTUALLY dispatched to, from the client that dispatched it | **PASS** | `decision.py:797-802` `_aux_model = _sc.dispatched_aux_model()` → `estimate_cost(_aux_model,…) if _aux_model else 0.0`. `semantic_classifier.py:496-501` identical. Value source: `semantic_classifier.py:386-393` — `_set_dispatched_aux_model(model)` immediately after `client, model = get_text_auxiliary_client("router")`; that `model` is the same string passed as `kwargs["model"]` to the wire (`:399-405`), and `/opt/hermes/agent/auxiliary_client.py:6485` returns `Tuple[Optional[OpenAI], Optional[str]] = (client, resolved_model)`. No lane label, no resolver call on the price path. |
| 2 | Unresolvable model → 0.0 + honest `$0.00000*`, never `jev-latest` or any other id | **PASS for the two aux taps** | Proven by running, not reading: clean d380db5 worktree, `sc.aux_raw_call` with a client reporting no model → price key `''`, `estimate_cost` never called, ledger cost `0.0`; `rl._park_summary(…, cost=0.0, {'jev_native_model':'zz-unknown-9'})` → `"… \| $0.00000* \| row 2"`. Retired resolver raises: `aux_price_model() -> RuntimeError`. Pins `test_missing_dispatched_model_{prices_zero_and_never_a_stand_in,on_the_decision_path_prices_zero}` green. |
| 2b | **Defect CLASS closed** (fabricated rate over honest zero) | **FAIL — blocking (D1)** | Clean d380db5: `decision._v3_worker` with `backend: nous` + aux client reporting `reflection/beam-1.0-501b-a23b:free` wrote `tokens row {'lane':'decision','model':'z-ai/glm-5.3-flash','price_key_used':0.000372,…, 'detail':'decision_v3'}`. `estimate_cost('reflection/beam…:free') = 0.0`. Chain: `decision.py:3103` `meta = dict(meta, model=model, endpoint="hermes-auxiliary", …)` where `model = str(cfg.get("model") or DEFAULTS["model"])` (`:3084`) → `:3900` `base_row["model"]` → `:4072` `estimate_cost(base_row["model"], …)`. `backend: nous` is `decision.DEFAULTS["backend"]`. |
| 3 | Fail-open preserved — an exception in the price path never blocks or alters the banner | **PASS** | Both taps price inside `try/except` (`decision.py:789-812`, `semantic_classifier.py:487-503`); `dispatched_aux_model()` itself catches and returns `''`; `estimate_cost` returns 0.0 on unknown. Pin `test_a_pricing_explosion_cannot_break_the_aux_lane` (both `estimate_cost` and `record_tokens` raise) still returns `"benign"`. |
| 4 | Reflex site from bebfdf7 untouched by this commit | **PASS** | `git show --stat d380db5` = 5 files (`decision.py`, `plugin.yaml`, `semantic_classifier.py`, `tests/routing/test_r10_catalog_resolution.py`, `tests/routing/test_r75_banner_cost_key.py`). `reflex_lane.py` absent; `git diff 39772dd d380db5 -- reflex_lane.py` empty. Pin `test_reflex_still_prices_its_own_dispatched_model` green. |
| 5 | Pins: R75 banner-cost-key + R10 catalog resolution | **PASS (with one weak member)** | Clean d380db5: `44 passed in 4.79s` (12 pins + 24 R10 + neighbours). Behavioural, not grep: `_stub_aux` monkeypatches `agent.auxiliary_client.get_text_auxiliary_client` and drives the REAL `_hermes_aux_call` through a fake OpenAI-shape client that records `kwargs["model"]`; assertions are `seen == [DISPATCHED]` with `DISPATCHED="MiniMax-M3"` while `resolve_aux_model`/`agent_model` are pinned to `DECOY="z-ai/glm-5.3-flash"` — a stand-in cannot pass by coincidence. Mutation check (E2, my own): replacing the tap with `estimate_cost("z-ai/glm-5.3-flash", …)` kills 5 pins (`aux_raw_call`, `decision_score`, `real_rate`, both missing-id). **Weak members, named:** `test_no_estimate_cost_call_anywhere_prices_the_lane_label` and the caller sweep inside `test_the_retired_default_model_helper_is_not_called_from_production` are source greps — anti-regression guards only, carrying no behavioural proof. |
| 6 | Full suite + runner summary verbatim | **PASS on no-regression; the unit's own number is wrong** | Clean d380db5, `python3 -m pytest -q -p no:randomly` → `6 failed, 2496 passed, 2 skipped, 1 deselected, 1 xfailed, 2 warnings, 2 errors in 771.74s (0:12:51)`. Standing baseline: 6 failed / 2495 passed / 2 errors. **Pass count +1, no drop, no new reds** — failures are exactly `p05 legacy purge`, `p4 schema budgets`, `r33m` ×2, `rider20` ×2, errors `rider10` banner-row ×2. r53-midturn / r72k passed in-suite. The commit's "7 failed, 2495 passed" does not reproduce (see D3). |
| 7 | Version 6.13.16 in `plugin.yaml` and the R10 pin | **PASS** | Clean d380db5: `plugin.yaml:3 version: 6.13.16`; `tests/routing/test_r10_catalog_resolution.py:384 assert manifest["version"] == "6.13.16"`; `test_version_bumped` green. (In the dirty live tree both read 6.13.17 — the in-flight addendum, not this commit.) |

---

## Findings

### D1 — BLOCKING: the third aux-priced banner still fabricates a rate
`decision.call_backend(..., backend="nous")` dispatches through `semantic_classifier._hermes_aux_call`, so the
aux client picks the model, **but** records `meta["model"] = cfg model or DEFAULTS["model"]`
(`decision.py:3084` → `:3103`). `base_row` copies it (`:3900`) and the v3 tokens row prices it (`:4072`).

Measured on a clean d380db5 worktree with a fake aux client reporting
`reflection/beam-1.0-501b-a23b:free` (conductor's real `auxiliary.router` model):

```
model the client actually sent : ['reflection/beam-1.0-501b-a23b:free']
dispatched_aux_model()         : 'reflection/beam-1.0-501b-a23b:free'
plugin cfg/default model label : z-ai/glm-5.3-flash
v3 ledger row model column     : z-ai/glm-5.3-flash
tokens row : {'lane': 'decision', 'model': 'z-ai/glm-5.3-flash',
              'price_key_used': 0.000372, 'detail': 'decision_v3'}
estimate_cost('reflection/beam-1.0-501b-a23b:free', 2759, 102) = 0.0
```

Same defect class the earlier review killed `bebfdf7` for, on the plugin's **default** backend, in a banner
whose endpoint cell literally reads `@ hermes-auxiliary`. Under the held doctrine (fabricated rate over honest
zero is worse than no number) this is blocking, and it makes the commit's title claim false.

**Shortest correct fix direction:** in the nous branch set the price key from the dispatcher, with **no
configured-model fallback** — `meta["model"] = _sc.dispatched_aux_model()` (`''` when the client reports none),
price `0.0` on `''` exactly as the two taps now do, and keep any display label in a separate field
(`meta["endpoint"]`/`backend` already carries it). Add one pin driving `call_backend(backend="nous")` with a
client reporting a non-config id and asserting equality of the priced key, plus one asserting `0.0` when the
client reports none. *(The uncommitted 6.13.17 patch in the tree does the first half but keeps `_sent_model or
model` — that fallback still fabricates whenever the client reports no id, so it must be dropped, not
inherited.)*

### D2 — advisory: "unresolvable" is only honest if the fallback is removed everywhere
`_sent_model or model` (in-flight patch) and any future `or <configured id>` re-opens the same hole from the
other side. The invariant to pin: **price key == the id the dispatching client reported, else 0.0** — full stop.

### D3 — advisory: the unit's suite arithmetic still does not reproduce
Commit message claims `7 failed, 2495 passed … 832.92s`. Clean d380db5 gives `6 failed, 2496 passed … 771.74s`.
`tests/decision_lane/test_r53_midturn_mindset.py::test_m1_envelope_carries_the_current_stance` **does** fail
standalone at both `39772dd` and `d380db5` (`1 failed, 16 passed` each — verified in separate worktrees), but it
**passes inside the full suite** at both trees, so it cannot be counted as a suite red. No regression either
way; the report is simply not reproducible, for the second leg running.

### D4 — advisory: review hygiene of a live checkout
The worktree moved under the review (dirty at 13:33–13:34Z, `plugin.yaml` 6.13.16→6.13.17). A run started
before that edit already false-failed `test_version_bumped` (collected pin 6.13.16 vs on-disk 6.13.17). Verdicts
on a shared repo must be taken from `git worktree add <tmp> <sha>`, never from the live checkout.

---

## Hostile attacks (Nemesis gate)

1. **Price key is a label or default** → killed for the two taps (equality pins + mutation, 5 pins die).
2. **Thread-local inherits the previous call's model** → cleared at `_hermes_aux_call` entry *and* on exception
   (`:386`, `:422`); every retry re-enters the function; no second dispatch seam exists in `aux_raw_call`.
3. **Missing id silently priced** → `estimate_cost` is not even called without an id (pins assert `seen == []`).
4. **Aux client's returned model ≠ wire model** → same string is used for `kwargs["model"]`; when it is empty no
   model kwarg is sent, so the provider default applies and the honest `''` is recorded (conservative, not false).
5. **Fail-open regression** → both `estimate_cost` and `record_tokens` raising still yields `"benign"`.
6. **Reflex site moved** → `reflex_lane.py` untouched; pin green.
7. **Third site** → **not addressed by this commit (D1).** Found by driving `_v3_worker`, not by reading.
8. **Pins are decoration** → mutation run kills 5/12; the remaining two are named source greps.

## Recipient seat

The reader of the banner is the operator doing cost attribution. `$0.00000*` tells them "unpriced, tokens were
real". `$0.000372` for a `:free` aux model tells them a number that is arithmetically precise and factually
wrong — the exact failure this series exists to remove. D1 is user-visible in the plugin's default config.

## Drift check

- Request-drift: unit scoped as "the two aux sites"; the held defect class is wider, and the commit's own title
  asserts the wider claim → the wider check is in scope, not scope creep.
- Implementation-drift: `aux_price_model()` retired to a raise — no surviving caller (grep-verified) — consistent.
- Verification-drift: unit reported 7/2495 from a tree that yields 6/2496 (D3); corrected here by re-running in
  an isolated worktree.

---

**FIX-FIRST.** D1 must be fixed (dispatched id or `0.0`, no configured-model fallback) and pinned at
`call_backend(backend="nous")`; then re-run the same clean-worktree suite before resubmitting.
