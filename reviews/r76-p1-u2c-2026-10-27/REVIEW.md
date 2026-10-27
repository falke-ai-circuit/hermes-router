# R76-P1-U2c — impulse banner prices the model that was DISPATCHED

**Commit:** `c094cba5334aba6d80936f4c69cec05e39293c8c` (6.13.18) · parent `bc1a499` (6.13.17)
**Repo:** `/opt/data/plugins/hermes_router` · branch `main` · reviewed 2026-10-27
**Verdict: FIX-FIRST** — inside the commit's own diff the fix is real and proven at the call (the impulse
line now prices the id `_transport` actually put in the request body, and an unpriced id renders the honest
`$0.00000*`). But the path the request names — the **NOUS** banner path — is untouched by this commit and
still substitutes the configured model id when the aux client reports no model, pricing `$0.000372` for a
call that sent none. The predecessor's D1 second half is still open at HEAD.

> **Ambiguity named (no charitable read):** the request calls the target "the NOUS banner path", while the
> line quoted in the commit message (`impulse (decision) | … | jev @ typesafe | …`) is rendered by
> `reflex_lane._park_summary` (`reflex_lane.py:882`, class field `N lead · M hold`, `tok` suffix). The only
> `nous` in the codebase is the decision backend (`decision.py:76-77`, `:3091-3111`, endpoint
> `hermes-auxiliary`). Both readings are measured below; the verdict rests on the name as written.
> If "NOUS" was shorthand for the quoted impulse line, §1a is PASS and only the advisories remain.
>
> **Working-tree warning:** the live checkout was dirty throughout (`anchor_exec.py`, `debug_banner.py`
> modified; untracked `tests/banners/test_r76_p2_u5_failure_banner.py`). Every measurement below was taken
> in a clean `git worktree` at `c094cba` (`/tmp/u2c_wt`, imported as package `hermes_router` via a symlink
> parent so the package import resolves).

---

## Item-by-item

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 1a | **Impulse/reflex banner** (the quoted line) prices the model actually dispatched | **PASS** | Driven, not grepped. `reflex_lane.py:124` `_DISPATCHED = threading.local()`; `:163` `_set_dispatched_model(body["model"])` runs **before** the POST at `:164`; `:967` `price_model = _dispatched_model() or …`; `:878` the model cell reads the same record. Clean-worktree drive of the real `run_on_request`: `body model sent: ['jev-latest']` · `estimate_cost key: ['jev-latest']` · banner `· router · impulse (decision) \| 1 lead \| jev @ typesafe \| 2759/102 tok \| $0.00012 \| row 2`. With the config deliberately changed to `MiniMax-M3` *between* dispatch and receipt, the priced key stays `jev-latest` (same banner) — the cfg re-read no longer reaches the price. Mutation (E2): replacing the record read with `""` (pre-fix behaviour) prices `MiniMax-M3` and renders `$0.00000*`; restoring it prices `jev-latest` and renders `$0.00012`. The record is load-bearing. |
| 1b | **NOUS banner path** prices the model actually dispatched, not the cfg model | **FAIL — blocking (D1)** | `decision.py:3110-3111`: `_sent_model = _sc.dispatched_aux_model()` then `meta = dict(meta, model=_sent_model or model, …)` where `model = cfg.get("model") or DEFAULTS["model"]`. `base_row["model"] = meta["model"]` (`:3908`) → priced at `:4080`. Proven at the call (`decision.call_backend`, `backend="nous"`, fake aux client): `model sent on the wire: [None]` · `meta['model'] (billed id): z-ai/glm-5.3-flash` · `price of meta['model']: 0.000372` · `price of sent id: [0.0]`. Nothing was dispatched, the cfg model was billed. This commit does not touch `decision.py`. |
| 2 | Unresolvable model → `0.0` + honest `$0.00000*`, never a substituted default id | **PASS (impulse) / FAIL (NOUS)** | Impulse: dispatch `custom-no-table-xyz` → priced key `custom-no-table-xyz`, `estimate_cost = 0.0`, banner `custom-no-table- @ typesafe \| 2759/102 tok \| $0.00000*`; log row `price_model=custom-no-table-xyz, price_key=custom-no-table-xyz, price_source='', priced=False`. No stand-in. NOUS: the `or model` fallback above is exactly the substituted default id, with a real rate (`$0.000372`) attached. |
| 3 | The previously-correct sites are unchanged by this commit | **PASS** | `git show --stat c094cba` = 5 files (`core/usage_ledger.py`, `plugin.yaml`, `reflex_lane.py`, `tests/routing/test_r10…`, `tests/routing/test_r75…`). `git diff bc1a499 c094cba -- decision.py semantic_classifier.py decision_midturn.py router.py anchor_exec.py` is **empty**. The shared pricing refactor is arithmetic-identical: old (`bc1a499`) vs new (`c094cba`) `estimate_cost(m, 2759, 102)` match on `jev-latest` 0.000116, `typesafe/jev-latest` 0.000116, `z-ai/glm-5.3-flash` 0.000372, `MiniMax-M3` 0.0, `reflection/beam-1.0-501b-a23b:free` 0.0, `ling` 0.0, `custom-no-table-xyz` 0.0, `""` 0.0. Both aux-tap pins still green (`tests/routing/test_r75_banner_cost_key.py`: 20 passed). |
| 4 | Fail-open: a price-path exception cannot block or alter the banner | **PASS** | Three separate explosions in the clean worktree: `estimate_cost` raises → banner still returned with tokens intact (`2759/102 tok \| $0.00000*`); `describe_price` raises → full price still rendered (`$0.00012`); both raise → banner returned (`$0.00000*`). No exception escaped `_receipt`; `_dispatched_model` has its own guard (`:145-148`). |
| 5 | `plugin.yaml` and `tests/routing/test_r10_catalog_resolution.py` both 6.13.18 | **PASS** | `plugin.yaml:3 version: 6.13.18`; `tests/routing/test_r10_catalog_resolution.py:384 assert manifest["version"] == "6.13.18"`; `pytest -k version` → `1 passed, 23 deselected`. |
| 6 | Full suite, runner summary verbatim | **PASS on no-regression** | Clean worktree at `c094cba`, `PYTHONPATH=/tmp/u2croot python3 -m pytest -q -p no:randomly` → **`6 failed, 2504 passed, 2 skipped, 1 deselected, 1 xfailed, 2 warnings, 2 errors in 622.33s (0:10:22)`**. Standing baseline 6 failed / 2495 passed / 2 errors. Pass count **+9, no drop, no new reds**: `p05_legacy_purge_guard`, `p4_schema_budgets`, `r33m_refusal_semantics` ×2, `rider20_fixes` ×2; errors `rider10_fixes` banner-row ×2 — every one in the allowed set. The commit's own `47 passed on the touched files (r75 22, r10 24, r73 receipt 1)` does **not** reproduce: measured **44** (r75 = 20 collected, r10 = 24; there is no `test_r73*` under `tests/routing/`). See D4. |

---

## Findings

### D1 — BLOCKING: the NOUS path still fabricates a rate for an unresolvable dispatch
`call_backend(..., backend="nous")` lets the aux client pick the model, and `_sc.dispatched_aux_model()`
honestly returns `''` when the client reports none. The code then substitutes the **configured** id:

```python
# decision.py:3110-3111
_sent_model = _sc.dispatched_aux_model()
meta = dict(meta, model=_sent_model or model, endpoint="hermes-auxiliary", …)
#            ^^^^^^^^^^^^^^^^^^ model = cfg.get("model") or DEFAULTS["model"]
```

Measured on a clean `c094cba` worktree, `backend="nous"`, cfg `model="z-ai/glm-5.3-flash"`:

```
model sent on the wire   : [None]
meta['model'] (billed id): z-ai/glm-5.3-flash
price of meta['model']   : 0.000372
price of sent id         : [0.0]
```

The tokens row prices `base_row["model"]` at `:4080`, so an unknowable call is billed a precise, wrong
number — the exact defect class this series exists to remove, on the plugin's **default** backend, and the
one thing item 2 forbids. `c094cba` does not touch `decision.py`, so this commit cannot close it.

**Shortest correct fix:** in the nous branch set the price key from the dispatcher with **no configured
fallback** — `meta["model"] = _sc.dispatched_aux_model()` and price `0.0` when it is `''` (the endpoint
label already lives in `meta["endpoint"]`). Pin one case: `call_backend` with a client reporting no model
must yield `estimate_cost` never called with the cfg id and a `0.0` row — the same shape the two aux taps
already have.

### D2 — advisory: the reflex fallback's stated premise is false for accumulated turns
`reflex_lane.py:967` and `:878` keep `or str(cfg.get("jev_native_model") or "jev-latest")`, justified in the
comment as "usage is 0 there, so the cost is moot". Receipts with no dispatch on the thread are not always
0-token: `inject_pass` receipt (`:400`, `seam="midturn"`) folds into the **same** turn accumulator. Measured
(P3): after an `every_call` fire priced `jev-latest | 2759/102 tok | $0.00012`, a following no-dispatch
midturn receipt re-rendered

```
· router · impulse (decision) x2 | 1 lead · 1 hold | MiniMax-M3 @ typesafe | 2759/102 tok | $0.00012 | row 2-3
```

— the model cell names a model that was never dispatched while the tokens and money come from the
`jev-latest` call. That is precisely the "name and money agree by construction" property the commit claims.
It is **latent today**, not live: `reflex_weights.cfg_for` (`reflex_weights.py:57`) does not whitelist
`jev_native_model`, so the fallback always resolves to `jev-latest`, which coincides with every reflex
dispatch. It becomes live the moment that key is whitelisted or a caller passes a payload model. Either
record the model per session/turn instead of per thread, or drop the fallback and let the cell be omitted.

### D3 — advisory: "cleared on any failed … dispatch" is not what the code does
`_transport` clears the record on entry and on exception (`:150`, `:166`) but not when `_http_post_json`
returns `None` (HTTP failure without raising). Measured: record pre-seeded `PREVIOUS-CONSULT`, POST returns
`None` → `_dispatched_model() == 'jev-latest'` (the attempted id survives). The pin
`test_a_failed_dispatch_claims_no_model` covers only the missing-key branch. Low risk (a failed dispatch
returns `None`, so no receipt follows in that call), but the commit message overstates the guarantee.

### D4 — advisory: the commit's own pin arithmetic does not reproduce (third leg running)
Claimed `47 passed on the touched files (r75 22, r10 24, r73 receipt 1)`. Measured on the clean worktree:
r75 collects **20**, r10 collects 24, and `tests/routing/` contains no `test_r73*` → **44 passed** for
r75+r10. No regression implied; the report simply is not reproducible, as in `d380db5` (D3 of the U2
review) and `bc1a499`.

### D5 — observation: the lane is armed in this review environment
During this audit the reflex/impulse lane appended readings to nine of my tool results, including a
decision-lane reading self-labelled `PROVENANCE-LOGGED`. All were advice-shaped (`no clear lead`,
`run the check`, `stop and report`) and carried no token or cost claim, so they neither corroborate nor
contradict the pricing fix; I treated them as data, not instructions. Worth knowing that an auditor of this
lane is being read by it.

---

## Hostile attacks (Nemesis gate)

1. **Price key is the cfg value re-read at receipt** → killed for the impulse path: config mutated to
   `MiniMax-M3` between dispatch and receipt, priced key stayed `jev-latest`.
2. **The record is decorative** → mutation kills it: with the record read replaced by `""` the banner prices
   `MiniMax-M3` at `$0.00000*`; restored, it prices `jev-latest` at `$0.00012`.
3. **Unpriced model silently substituted** → impulse: no — priced key is the dispatched id,
   `describe_price` reports `priced=False`, banner names the id with `$0.00000*`. NOUS: **yes (D1)**.
4. **Cross-thread / midturn receipt reads no dispatch record** → record falls back to cfg; latent because
   the fallback resolves to the same `jev-latest` that `cfg_for` permits (D2). Verified with a real
   thread-crossing probe and an accumulated-turn probe.
5. **Stale record after a failed dispatch** → **yes**, HTTP-`None` path keeps the attempted id (D3).
6. **Pricing explosion blocks the banner** → killed: three exception shapes still return a banner with
   tokens intact.
7. **Shared pricing refactor changes a previously-correct site** → killed: `decision.py` /
   `semantic_classifier.py` byte-identical between `bc1a499..c094cba`; old-vs-new `estimate_cost` identical
   across 8 ids including the alias and the empty string.
8. **Suite reported green that is not** → measured: 6 failed / 2504 passed / 2 errors, all reds in the
   allowed set; the commit's own 47 does not reproduce (D4).

## Recipient seat

The reader is the operator doing cost attribution. For the impulse line the money cell is now derived from
the same id as the name cell and survives a config change — that is the requested behaviour and it holds.
For the NOUS banner the reader still sees a precise number for a call whose target was never reported:
`z-ai/glm-5.3-flash | $0.000372` when nothing was sent. `$0.00000*` would tell them "unpriced, tokens were
real"; the current cell tells them a fact about a model that did not serve the call.

## Drift check

- **Request-drift:** the request asks about the NOUS path; the commit is scoped to reflex_lane. Both were
  measured. The commit's own claim is true; the request's claim is not yet true at HEAD. This is not scope
  creep — the request named the path.
- **Implementation-drift:** the dispatch record is written exactly where the body model is set, and read by
  both name and money cells — coherent. The only incoherence is the retained `or cfg` fallback (D2).
- **Verification-drift:** unit reports 47 pins; 44 reproduce. Suite is otherwise honest (pass count +9,
  reds exactly the allowed set).

---

**FIX-FIRST.** Drop the configured-model fallback in the nous branch (`decision.py:3111`) and price `0.0`
when `dispatched_aux_model()` is empty, with a pin driving `call_backend(backend="nous")` for the missing-id
case. The impulse/reflex site (`reflex_lane.py`) is correctly fixed and needs no further work beyond the
D2 fallback cleanup; then re-run the clean-worktree suite and correct the pin arithmetic in the report.
