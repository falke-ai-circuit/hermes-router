# R76-P1-U2d — nous banner prices the dispatched model, never the cfg default

**Commit:** `8d4d5fe661033514e610326bb3a6ff68335990d2` (plugin.yaml 6.13.20) · parent `506547a`
**Repo:** `/opt/data/plugins/hermes_router` · HEAD `8d4d5fe`, live tree clean (`git status --porcelain` empty)
**Reviewed:** 2026-10-27 · reviewer lane (verdict only, no implementation)
**Verdict: FIX-FIRST** — the named v3 site is genuinely fixed and proven at the call: an unreported nous
dispatch now prices `''` → `0.0` and renders `$0.0000*`. But the `or model` substitution **still reaches
`estimate_cost`**, on the same nous path, one file over: `decision_midturn.py:1039` bills
`base["model"]` (= `_sent_model or cfg_default`) and `:1491` prices the turn-close rollup the same way.
Driven at the call with an aux client reporting nothing: tokens row
`{'lane':'decision','model':'z-ai/glm-5.3-flash','cost':0.000372,'detail':'decision_midturn'}` and rollup
`· router · impulse (decision) | 1 verdict | tok 2759/102 | $0.000372 | provider=hermes-auxiliary | …`.
By the request's own kill criterion ("if the `or model` substitution still reaches `estimate_cost` anywhere,
the defect class is OPEN"), the class is OPEN.

> Every measurement below was taken in a clean `git worktree` at 8d4d5fe
> (`/opt/data/profiles/reviewer/cache/scratch/u2d_wt`, package import via a symlink parent — the U2/D4
> discipline). Probes and raw logs: `probes/` in this directory.

---

## Item-by-item

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 1a | **v3 path**: which key actually reaches `estimate_cost` | **PASS** | Driven, not read. `_v3_worker` + real `call_backend(backend="nous")` + fake aux transport (`probes/v3_path_three_cases.log`): reported `''` → `estimate_cost keys received: ['', '']`, tokens row `cost 0.0`, banner `· router · impulse (decision) \| pre \| z-ai/glm-5.3-flash @ hermes-auxiliary \| tok 2759/102 \| $0.0000* \| … \| row=1 ·`. `price_used` is `meta["price_model"]` (`decision.py:4082-4084`, `render_payload.py:244-246`); the `or model` cell is never handed to `estimate_cost` on this path. |
| 1b | **Defect CLASS closed everywhere** — no `or model` substitution reaches `estimate_cost` | **FAIL — blocking (D1)** | `decision_midturn.py:951` `base["model"] = str(meta.get("model") or cfg.get("model") or "")` → `:1039` `_ul.estimate_cost(base["model"], _ti, _to)`. Driven with `M._handle_hit(..., cfg backend=nous, MIDTURN_MODE_KEY=on)` and an aux client reporting nothing (`probes/midturn_path.log`): `estimate_cost keys received: ['z-ai/glm-5.3-flash', 'z-ai/glm-5.3-flash']`, row `{'lane':'decision','model':'z-ai/glm-5.3-flash','cost':0.000372,'detail':'decision_midturn'}`, rollup banner `$0.000372`. Nothing was dispatched; the cfg default was billed at its real rate. `decision.py:76` `DEFAULTS["backend"] = "nous"`, and `midturn: 'on'` is set in 9 live profiles. |
| 2a | None reported → `0.0` + honest marker (**price** half) | **PASS** | `keys ['', '']`, `price('') = 0.0`, banner `$0.0000*`, `$0.000372` absent from row and banner. |
| 2b | Priced model reported → its real rate | **PASS** | dispatched `jev-latest` → `keys ['jev-latest','jev-latest']`, row cost `0.000116`, banner `$0.000116`. |
| 2c | Unpriced model reported → `0.0` + marker + the id named | **PASS** | dispatched `zz-no-such-model-9` → `keys ['zz-no-such-model-9','zz-no-such-model-9']`, row cost `0.0`, banner `… \| zz-no-such-model-9 @ hermes-auxiliary \| tok 2759/102 \| $0.0000* \| …`. |
| 2d | **No fabricated id** in the None case | **FAIL — non-blocking finding (D2)** | The money is honest but the name cell is not: banner reads `z-ai/glm-5.3-flash @ hermes-auxiliary` and the ledger row's `model` column is `z-ai/glm-5.3-flash` for a call that dispatched nothing. The fabricated id moved from the price cell to the name cell. The unit declares this as deliberate provenance ("the row/banner model column still names the lane target"); the request names it as a criterion. |
| 3 | The three already-correct sites untouched | **PASS** | `git show 8d4d5fe --numstat` = decision.py 14/2, plugin.yaml 1/1, render_payload.py 12/1, test_r10 1/1, new pin 290/0. `reflex_lane.py` and `semantic_classifier.py` absent; the `decision.score` site (`decision.py:797-802`) is outside both hunks; `tests/routing/test_r75_banner_cost_key.py` → `20 passed`. |
| 4 | SCOPE — `render_payload.py` was not in the brief | **REQUIRED, not a breach** | Mutation (E2): with `_price_key` reverted to `str(model or "")` in the clean worktree, `2 failed, 7 passed` — the banner then prices the cfg default at `$0.000372`. The banner is a second pricing surface that reads `meta` + `model`, so the chosen design (separate price key) cannot land honestly without touching it. Ruled required for the fix as designed. It would have been avoidable by dropping the `or model` fallback in the model cell — see D2. |
| 5 | Pins assert at the CALL, not by grep | **PASS (with a gap)** | `tests/routing/test_r76_p1_u2d_nous_price_fallback.py` → `9 passed in 1.28s`. Behavioural: `_call_backend` / `_drive_worker` drive the real `call_backend` / `_v3_worker` with a fake aux transport and capture the keys `estimate_cost` actually received; `_stub_aux` binds a real OpenAI-shape client so `_original_hermes_aux` is exercised for the three legacy sites. Mutation (E2), both directions: reverting the decision.py price key to `base_row["model"]` → `1 failed, 8 passed` (`test_a_…_writes_a_zero_cost_token_row`); reverting the render_payload key → `2 failed, 7 passed`. **Gap:** no pin drives `decision_midturn`, which is exactly where the defect survives. |
| 6 | Version 6.13.20 in plugin.yaml and the catalog pin | **PASS** | `plugin.yaml:3 version: 6.13.20`; `tests/routing/test_r10_catalog_resolution.py:384 assert manifest["version"] == "6.13.20"`; no stray `6.13.19` outside `reviews/` and `.git/`. |
| 7 | Full suite + runner summary verbatim | **PASS on no-regression; baseline count does not reproduce** | Clean worktree 8d4d5fe, `PYTHONPATH=…:…/u2droot:/opt/hermes python3 -m pytest -q -p no:randomly` → **`7 failed, 2518 passed, 2 skipped, 1 deselected, 1 xfailed, 2 warnings, 2 errors in 357.22s (0:05:57)`** (`probes/full_suite.log`). Recorded baseline is 8 failed / 2 errors; measured 7 / 2. Every red is in the allowed set — p05 ×1, p4 ×2, r33m ×2, rider20 ×2, rider10 errors ×2 — **no red outside the list, therefore no regression**. The one-red difference is `decision_lane/test_r53_midturn_mindset.py::test_m1_envelope_carries_the_current_stance`, which fails standalone at this HEAD (`1 failed, 16 passed`) and passes in-suite: order-dependent flake, the same shape U2/D3 recorded. Not a unit red. |

---

## Findings

### D1 — BLOCKING: the same `or model` substitution still bills on the midturn nous path
`call_backend(backend="nous")` now carries `meta["price_model"]`. Two consumers were taught to read it
(`decision.py` v3 tokens row, `render_payload.render_decision_banner`). Two were not:

```python
# decision_midturn.py:928
content, meta, reason = _dec.call_backend(envelope, cfg)      # meta has price_model
# decision_midturn.py:951
base["model"] = str(meta.get("model") or cfg.get("model") or "")   # provenance == _sent_model or cfg
# decision_midturn.py:1039
_ul.estimate_cost(base["model"], _ti, _to)
# decision_midturn.py:1491 (turn-close rollup record)
model = str(meta.get("model") or cfg.get("model") or "")
cost  = usage_ledger.estimate_cost(model, ti, to)
```

Measured, `medium` mode `on`, `backend=nous`, cfg `model="z-ai/glm-5.3-flash"`, aux client reporting nothing:

```
estimate_cost keys received : ['z-ai/glm-5.3-flash', 'z-ai/glm-5.3-flash']
tokens rows                 : [{'lane': 'decision', 'model': 'z-ai/glm-5.3-flash',
                                'cost': 0.000372, 'detail': 'decision_midturn'}]
TURN-CLOSE ROLLUP           : · router · impulse (decision) | 1 verdict | tok 2759/102 |
                              $0.000372 | provider=hermes-auxiliary | initiator=agent | row=1 ·
```

Reach: `decision.py:76` makes `nous` the DEFAULTS backend and `midturn: 'on'` is set in analyst, architect,
conductor, evol, operative, orchestrator, researcher, reviewer, shadow configs. Live profiles currently pin
`backend: jev_native`, so this is not billing today — but it is the plugin's default backend and the
unit's own headline ("nous banner prices the dispatched model, never the cfg default") is false while this
line exists.

**Smallest correct fix:** the price key must be derived once and consumed everywhere — e.g.
`usage_ledger.price_key(meta, fallback_model)` used at `decision_midturn.py:1039`, `:1491`,
`decision.py:4092` and `render_payload.py:249`, so a new pricing site cannot re-invent the fallback. Add one
pin driving `_handle_hit(backend="nous")` with an aux client reporting nothing and asserting the
`decision_midturn` row cost is `0.0` and the rollup carries the marker.

### D2 — the fabricated id moved from the price cell to the name cell
For an unreported dispatch the banner renders `z-ai/glm-5.3-flash @ hermes-auxiliary | tok 2759/102 |
$0.0000*`. The money is now honest; the model cell still names a model that never served the call, and the
ledger row's `model` column agrees with it (`{'model': 'z-ai/glm-5.3-flash', 'cost': 0.0}`). The predecessor
verdicts asked for `meta["model"] = dispatched` (`''` when none) with the label kept in a separate field
(`endpoint` already reads `hermes-auxiliary`). Keeping the name cell as-is is a defensible provenance
choice *only* if the reader is told — today nothing in the banner distinguishes "this is the lane's target"
from "this is what served the call". Non-blocking, but it is the request's "no fabricated id" clause.

### D3 — marker precision differs from the claim text
Rendered/asserted marker is `$0.0000*` (4 dp, `debug_banner` format); the request and the commit message both
quote `$0.00000*` (5 dp). Cosmetic; noting it so the series' quotation stays exact.

---

## Hostile attacks (Nemesis gate)

1. **`_price_key` recomputes from the cfg elsewhere** → killed for the two edited sites by mutation
   (1 and 2 pins die respectively) and by driving with cfg `z-ai/glm-5.3-flash` while the transport reported
   `jev-latest`/`zz-no-such-model-9` (`keys` never contained the decoy).
2. **A second pricing surface still consumes the substituted cell** → **succeeded (D1)**:
   `decision_midturn.py:1039` and `:1491` bill `$0.000372` for a dispatch that reported nothing.
3. **Pins are decoration / grep-only** → killed: all 9 execute the real entry points; two independent
   mutations kill 1 and 2 pins. Weak member: `test_a_…_prices_zero_never_the_configured_default` only asserts
   `"" in keys` and `DECOY not in keys` — it never proves the *row* key, which is why one mutation kills only
   one pin.
4. **Unpriced id silently substituted with a rate** → no on the v3 path (`zz-no-such-model-9` → `0.0`, id
   named); yes on the midturn path (D1).
5. **Fail-open regression in the new `try/except`** → cleared: `test_pricing_explosion_cannot_break_the_nous_banner`
   green, and the banner still carries `tok 2759/102`; `except` returns `0.0`, it does not suppress.
6. **Scope creep in `render_payload.py`** → required, proven load-bearing by mutation (D4/N4 above), not a breach.
7. **Version/pin drift** → cleared: only two `6.13.20` occurrences in the tree, no stale `6.13.19` pin.
8. **Suite reported green that is not / baseline over-claimed** → measured 7 failed / 2 errors, all reds in the
   allowed set; the recorded 8-red baseline is one red too high at this HEAD (order-dependent r53).

## Recipient seat

The reader is the operator doing cost attribution. On the v3 pre/post banner they now get what the request
asks for: `$0.0000*` when the transport named nothing, the real rate when it named a priced model, the id
named when it named an unpriced one. On the midturn rollup — the banner an operator sees at turn close, in
every profile with `midturn: 'on'` — they still get `$0.000372` for a call that dispatched nothing. The
receipt is honest on one surface and fabricated on the other, which is worse than uniformly silent: it is
indistinguishable from a correct receipt.

## Drift check

- **Request-drift:** the request's kill criterion is class-level ("anywhere"); the unit scoped the fix to two
  consumers of the new key and left two others reading the old cell. The wider check is in scope.
- **Implementation-drift:** the price key exists in one place (`meta["price_model"]`) but is consumed by two
  ad-hoc `if … in meta` expressions rather than one helper — that duplication is how the midturn site was
  missed.
- **Verification-drift:** the pins verify the design as written but never enumerate the pricing sites; the
  suite's red count was reported against a baseline that no longer holds at this HEAD.

---

**FIX-FIRST.** Route every nous pricing site through one price-key helper (mid: `decision_midturn.py:1039`
and `:1491`), add the missing midturn pin, and decide D2 explicitly (either accept "model cell = lane target"
in writing or drop the `or model` fallback). The v3 banner fix itself is correct and must not be reworked.
