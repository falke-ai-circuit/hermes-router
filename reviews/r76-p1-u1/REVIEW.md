# R76-P1-U1 — banner prices the MODEL ID (6.13.11, bebfdf7) — verification review

- **Commit under review:** `bebfdf7` "R75 (6.13.11): banner prices the MODEL ID, not the lane's display label" (parent `7615ac3`)
- **Reviewer:** reviewer — verification only. No code, no fixes, no test edits to the repo's suite; probes under `reviews/r76-p1-u1/`.
- **Concurrency note (material to every number below):** HEAD moved during this review — `f4fae76` (6.13.12) landed at 12:18Z while my first full-suite run was in flight, flipping `plugin.yaml` and the version pin to 6.13.12 mid-run. **All counts below come from isolated git worktrees pinned at `bebfdf7` and `7615ac3`**, never from the moving main tree.
- **Verdict: FIX-FIRST** — the reported defect (reflex banner pricing a label) is fixed and proven; but the two aux sites now price a model that was not dispatched, and in the repo/test layout that key degenerates to `jev-latest`, producing a *fabricated* rate where the old behaviour was an honest zero (F1). The ledger's `model` column ruling (F2) is answered below.

---

## Verdict table

| # | Item | Method | Evidence | Result |
|---|------|--------|----------|--------|
| 1 | All three sites pass a model id, not a label | independent probe capturing the first arg of `usage_ledger.estimate_cost` at each path (`reviews/r76-p1-u1/probe_sites.py`) | `[('jev-latest', 2759, 102), ('MiniMax-M3', 2759, 102), ('MiniMax-M3', 2759, 102)]`; worktree layout: `[('jev-latest',…), ('jev-latest',…), ('jev-latest',…)]` | **PASS (literal)** — no label is ever the key. Adequacy of the aux keys fails → **F1** |
| 2 | Fail-open preserved (unknown → 0.0, `$0.00000*`, no invented rate) | code read + direct `estimate_cost` + `_park_summary` | `estimate_cost('zz-no-such-model-9',2759,102)=0.0`; rendered `· router · impulse (decision) \| 1 lead \| zz-no-such-model @ typesafe \| 2759/102 tok \| $0.00000* \| row 2`; priced model → `$0.00012` with **no** asterisk | PASS |
| 3 | No `estimate_cost("hermes-auxiliary")` left in source | repo-wide regex + manual read of every occurrence | source: none. Remaining label uses are ledger display/model args and comments (list below) | PASS |
| 4 | Version bumped in both places | file read + targeted run | `plugin.yaml:3 version: 6.13.11`; `tests/routing/test_r10_catalog_resolution.py:384 assert manifest["version"] == "6.13.11"`; the two "unfinished" files → **27 passed in 3.06s** | PASS |
| 5 | No regression; coder's counts and "7th failure pre-existing" claim | full suite in pinned worktrees, base vs head, failure lists diffed by node id | head `bebfdf7`: **6 failed, 2495 passed, 2 skipped, 1 deselected, 1 xfailed, 2 errors**; base `7615ac3`: **6 failed, 2484 passed, 2 skipped, 1 deselected, 1 xfailed, 3 errors**; same 6 failures on both; **+11 passed = exactly the 11 new pins** | PASS on substance; **coder's numbers wrong** (see §5) |
| 6 | Ruling: ledger `model` column still records `hermes-auxiliary` | source read of both taps + consumer audit of the ledger | `record_tokens("aux", "hermes-auxiliary", …)` / `record_tokens("decision", "hermes-auxiliary", "", …)`; no consumer reads the ledger `model` for a banner or for routing | **Not acceptable as a permanent state** — see F2 |

---

## 1. The three sites — what is actually passed

Independent capture (monkeypatched `estimate_cost`, no reliance on the shipped pins):

```
main checkout (/opt/data/plugins/hermes_router, root config resolves):
  [('jev-latest', 2759, 102), ('MiniMax-M3', 2759, 102), ('MiniMax-M3', 2759, 102)]
pinned worktree at bebfdf7 (no profile/plugins layout -> agent_model() == ''):
  [('jev-latest', 2759, 102), ('jev-latest', 2759, 102), ('jev-latest', 2759, 102)]
```

- **reflex_lane._receipt** → `cfg["jev_native_model"] or "jev-latest"`. This **is** the model the consult is dispatched to (`_transport` sets `body["model"]` from the same key). Correct.
- **decision.score / semantic_classifier.aux_raw_call** → `aux_price_model()` → plugin `aux_endpoint.model` (unset in this fleet) → `config_access.agent_model()` → `"jev-latest"`.

What those calls actually dispatch to is Hermes' auxiliary client for task `router`:

```
semantic_classifier._hermes_aux_call -> get_text_auxiliary_client("router")
/opt/data/profiles/reviewer/config.yaml:
  auxiliary:
    router:
      provider: custom:nousinf
      model: reflection/beam-1.0-501b-a23b:free
  model:
    default: deepseek/deepseek-v4.1-flash-1017
```

So the price key is **never** the model the aux call used. It is a third value: the agent's own chat model (or the root-config default `MiniMax-M3` when the plugin sits outside a profile, or `jev-latest` when no config resolves at all).

`estimate_cost` for these ids in this environment:

```
reflection/beam-1.0-501b-a23b:free -> 0.0
deepseek/deepseek-v4.1-flash-1017  -> 0.0
MiniMax-M3                         -> 0.0
jev-latest                         -> 0.000116
z-ai/glm-5.3-flash                 -> 0.000372
```

Consequence: in a deployed per-profile layout both aux sites still record `0.0` (the key is unpriced) — same visible outcome as before the commit; in the repo/test layout (and any layout where `agent_model()` returns empty) they record **1.16e-4 per 2759/102 tokens at the JEV rate for a `:free` beam call** — a price for a model that was not used. `$0.00000*` → a fabricated number is a regression in honesty on exactly the surface this commit exists to make truthful.

The pins cannot see this: `test_the_three_known_sites_pass_a_model_not_the_label` monkeypatches `agent_model -> ""` and then asserts `seen.count("jev-latest") >= 2` — i.e. it **encodes the fallback as expected behaviour**. Nothing asserts `key == dispatched aux model`.

**F1 (blocking).** Both aux taps must price the model returned by the same resolver that dispatches (`get_text_auxiliary_client("router")` already returns `(client, model)`), and the pin must assert that equality, not "jev-latest was used".

---

## 2. Fail-open

Verified directly, not only via the pin:

```
priced:        · router · impulse (decision) | 1 lead | jev @ typesafe | 2759/102 tok | $0.00012 | row 1
unknown model: · router · impulse (decision) | 1 lead | zz-no-such-model @ typesafe | 2759/102 tok | $0.00000* | row 2
estimate_cost('zz-no-such-model-9', 2759, 102) -> 0.0
estimate_cost('', 2759, 102) -> 0.0
```

The asterisk is drawn for the unpriced cell and no rate is invented inside `estimate_cost`. PASS — with the caveat that "no fabricated rate" is a property of `estimate_cost`, not of the call sites: F1 makes the caller fabricate by proxy.

---

## 3. Remaining `hermes-auxiliary` occurrences

`estimate_cost("hermes-auxiliary"` appears **nowhere** in source. What remains is display/model bookkeeping and prose:

- `decision.py:784` comment; `decision.py:787` `record_tokens("decision", "hermes-auxiliary", "", …)` (ledger model column); `decision.py:3090` `meta(..., endpoint="hermes-auxiliary")` (banner endpoint cell).
- `semantic_classifier.py:145` docstring; `:466` comment; `:469` `record_tokens("aux", "hermes-auxiliary", …)` (ledger model column).
- `decision_midturn.py:1585` comment; `reflex_lane.py:994` comment.
- **Test files (documenting the proof, as allowed):** `tests/routing/test_r75_banner_cost_key.py` lines 8, 9, 13 (docstring), 74 (the guard regex), 80 (failure text), 127 and 202 (negative assertions), 174 (`estimate_cost("hermes-auxiliary", 2759, 102) == 0.0` proof); `tests/decision_lane/test_d1_impulse_lane.py:185` (`endpoint="hermes-auxiliary"` fixture).

PASS.

---

## 4. Version + the two test files the coder left unfinished

```
plugin.yaml:3                                  version: 6.13.11
tests/routing/test_r10_catalog_resolution.py:384   assert manifest["version"] == "6.13.11"
```

Run at `bebfdf7` in the pinned worktree:

```
pytest tests/decision_lane/test_r73_p1_receipt.py tests/routing/test_r10_catalog_resolution.py -p no:randomly
27 passed in 3.06s
```

PASS. (In the moved main tree this same file now asserts 6.13.12 and passes; the 6.13.12 flip mid-run is what manufactured the phantom "7th failure" below.)

---

## 5. Suite and the "7th failure pre-existing" claim

Both runs in isolated worktrees, `-q -p no:randomly --tb=no`:

| tree | result |
|---|---|
| `bebfdf7` (head) | **6 failed, 2495 passed, 2 skipped, 1 deselected, 1 xfailed, 2 errors** in 364.55s |
| `7615ac3` (parent) | **6 failed, 2484 passed, 2 skipped, 1 deselected, 1 xfailed, 3 errors** in 443.06s |

Same six failures on both, by node id: `test_p05_legacy_purge_guard::test_legacy_mention_allowlist_only_shrinks`, `test_p4_schema_budgets::test_c_every_get_path_in_schema`, `test_r33m_refusal_semantics` (2), `test_rider20_fixes::test_d3_*` (2). Collection: head 2505, base 2494 → **+11 collected, +11 passed = exactly the 11 new pins** in `tests/routing/test_r75_banner_cost_key.py` (11 passed in isolation).

The coder's report — "2494 passed / 7 failed / 2 errors, the 7th failure proven pre-existing on parent 7615ac3" — is **not reproducible**:

- `bebfdf7` yields **6** failures, not 7; the parent yields **6** failures and **3** errors (the extra error is the known egress-flaky `test_r19_decision_lane.py::test_injection_unknown_decision_rejected`).
- The only "7th failure" I ever observed was `test_r10_catalog_resolution.py::test_version_bumped` in my first, main-tree run — and it was caused by the **concurrent 6.13.12 commit** rewriting `plugin.yaml`/the pin mid-run, not by `bebfdf7`. It does not exist at `bebfdf7` (that file passes: 27 passed).

Substance is therefore clean — no new reds — but the close report's counts and its "pre-existing" claim are wrong as written and must not be carried forward as evidence.

---

## 6. Ruling — the ledger's `model` column still records `hermes-auxiliary`

Facts:
- `record_tokens("aux", "hermes-auxiliary", …)` and `record_tokens("decision", "hermes-auxiliary", "", …)` keep the display label in the ledger's `model` field.
- `est_cost_usd` in the same record is now computed from a **different** key (`aux_price_model()`), so the two columns no longer reconcile: recomputing `estimate_cost(rec["model"], it, ot)` from the record gives `0.0` while the record may claim a nonzero cost.
- Consumer audit: `aggregate()` groups by **lane**, not model; `commands_diag` session table reads state rows, not ledger models; `bypass_watch` reads lane/session/ts only. **Nothing renders a banner or routes on the ledger `model` column.** The non-fix is therefore not forced by any display dependency — the banner's `@ hermes-auxiliary` cell comes from `meta["endpoint"]` / the lane, not from this record.

**Ruling: not acceptable as a permanent state; acceptable only as an explicitly tracked follow-up, and it must be fixed together with F1.** A cost-audit surface whose per-call record cannot reproduce its own cost is not auditable; per-model spend attribution is impossible while a lane label sits in the model column. The cheap correct shape is: `model` = the resolved (price-key) model id, the display label moved to `detail` or a new `endpoint`/`price_model` field. Because no consumer keys on it, that change is low-risk — the coder's "deliberate non-fix" is a scoping choice, not a constraint.

---

## Findings and fix list (for the coder; not implemented here)

**F1 (blocking) — aux price key is not the dispatched model.** In the repo/test layout it degenerates to `jev-latest` and bills a `:free` beam call at the JEV rate; in a deployed layout it uses the agent's chat model. Fix: price with the model `get_text_auxiliary_client("router")` returns for the same call; replace the pin that asserts `jev-latest` with a pin asserting key == dispatched model.

**F2 (blocking for the cost-audit claim, same fix surface) — ledger `model` column is a label while `est_cost_usd` is priced from another key.** Fix: record the price-key model in `model`; keep the display name in `detail` (or a schema addition), so each record is self-reconciling. No consumer depends on the current value.

**N1 (report accuracy, non-blocking) — the close report's suite numbers and "7th failure pre-existing" claim do not reproduce.** State the measured pair: 6F/2495P/2E at `bebfdf7`, 6F/2484P/3E at `7615ac3`, +11 passed = the 11 new pins.

**N2 (process, non-blocking) — a commit landed mid-verification.** `f4fae76` (6.13.12) changed `plugin.yaml` and the version pin while a suite run was reading them, which is what produced a phantom failure. Verdicts on push-time commits need a frozen worktree; that is what I used.

---

## What was proven / what was not

Proven from disk and re-execution: the label is gone from every price key (E1/E2); the reflex lane prices the model it dispatches; fail-open still yields `$0.00000*` with no invented rate; version bumped in both places; 27 passed on the two "unfinished" files; 11/11 new pins; the full-suite comparison and the +11-passed attribution; the aux key ≠ dispatched model, with the concrete ids.

Not proven (stated, not inferred): that the coder's 7-failure/parent claim was ever true — I could not reproduce it at either commit; and any live fleet effect, since 6.13.11 is not shown to be loaded (the deployed tree moved to 6.13.12 during this review).
