# REVIEW — R76-P2-U5b (commit 50a3311, hermes-router 6.13.23)

Reviewer: reviewer profile · 2026-10-27 · repo `/opt/data/plugins/hermes_router`
Target: `50a3311a54c5ff136e8ee6535a127c669fef0f69` "R76-P2-U5b (6.13.23): the metadata-only claim states what the code does"
Parent: `9874d0e` (reviews-only commit over `2c243fc`; code identical to `2c243fc`)
Tree at review: HEAD `50a3311`, clean.
Closes: F1 of `reviews/r76-p2-u5a-2026-10-27/REVIEW.md`.
Recipient: the next unit (or the user) that reads the `format_banner` docstring, the `anchor_exec` call-site
comment, or the pin module and decides whether the `metadata_only` shape is a redaction surface.

Everything below is DRIVEN. Both trees are real worktrees
(`/opt/data/tmp/u5b/{child,parent}/hermes_router`, HEADs asserted `50a3311` / `9874d0e`), imported as real
packages (`hermes_router.__file__` printed and asserted), with the endpoint and usage arguments replaced by
objects whose `__str__` / `split` / `__int__` / `__float__` record invocation.

## VERDICT: **PASS**

Every clause of the corrected sentence is measurably true of the shipped code: the endpoint is read
(`__str__` invoked), host-redacted (the `://` split is invoked on the returned string), the usage figures are
converted, the `metadata_only` return happens after all of that, and the failure shape renders neither an
endpoint cell nor a usage cell — so a secret-bearing URI is byte-identical to `""`. No copy of the retired
wording survives outside the pin that asserts its absence, all five green/red mutations are caught including
the one that makes the false claim *true* in code, and the rendered failure line is byte-identical to
`9874d0e` on 3/3 models.

One defect found, named here rather than buried: **F1 — the corrected sentence's own line citations are
stale by 4 lines** (`:180-183` / `:170-172` are the PARENT tree's numbers; in 50a3311 those lines are the
unrelated "Redaction surface" comment and `model_s`). Mechanism claims all true, so it is an M3 navigational
defect, not a false mechanism claim: it does not gate this PASS and it changes nothing that ships. Fix it in
the next touch of `debug_banner.py` — see §F1 for the exact replacement.

## Verdict table

| # | Check | Result | Evidence class |
|---|-------|--------|----------------|
| 1 | Claim vs driven behaviour (instrumented endpoint + usage args) | **PASS** — every mechanism clause survives; M3 pointer defect named (F1) | E1 (instrumented renderer) + E2 |
| 2 | No residual copy of the old claim; pin's anti-relapse value | **PASS** (zero survivors outside the asserting pin); pin kills all 5 relapse mutations, blacklist-residual noted | E1 (repo scan) + E2 (mutations) |
| 3 | Byte identity 9874d0e vs 50a3311, real empty-consult path | **PASS** (3/3 sha256 equal) | E2 (both trees driven) |
| 4 | Zero behaviour change; 90-line test change = pins/docstrings, no weakened pin | **PASS** (AST-identical after docstring strip; 0 tests removed; 1 existing assertion strengthened; 2 added) | E1 + E2 |
| 5 | Pin file green, and it fails under mutation | **PASS** (`9 passed`; 5/5 mutations caught) | E2 (mutations executed here) |
| 6 | Version 6.13.23 in `plugin.yaml` + catalog pin | **PASS** | E1 |
| 7 | Full suite, allowed reds only | **PASS** (7F/2528P/2E; `r53::test_m1` GREEN — 7-variant, matches `d3600b4` baseline) | E3 (runner executed here) |

## 1. Claim vs driven behaviour — PASS

Quote of the corrected sentence as it now ships (`debug_banner.py:157-168`):

> Guarantee of this shape, stated exactly: lane VALIDATION applies (FORBIDDEN_LANES or an unknown lane
> returns ""), and the lane label comes from lane_label(). Endpoint REDACTION does NOT reach the rendered
> line — not because the endpoint goes unread: `ep_s` is built and host-redacted (:180-183) and
> tokens_in/tokens_out/est_cost are converted (:170-172) BEFORE this branch, and metadata_only returns
> before `ep_s` and those figures are USED, because the failure shape renders no endpoint cell and no usage
> cell. The redacted host and the figures are therefore discarded unrendered. Passing a secret-bearing URI
> leaves the rendered bytes unchanged, and no unpriced-model marker can appear, for that reason: the values
> are read, then dropped, not skipped. A caller that needs host redaction on the line must use the normal
> shape, not this one.

Call-site form (`anchor_exec.py:1679-1688`):

> It does NOT buy endpoint redaction or the unpriced marker on the line: metadata_only returns before `ep_s`
> and the usage cells are USED, so an endpoint passed here would be read and host-redacted and then
> discarded — this shape renders no endpoint cell — and no usage cell exists for a marker to ride in. This
> call passes "" and 0/0/0.0.

Test-module form (`tests/banners/test_r76_p2_u5_failure_banner.py:12-19`):

> metadata_only returns before `ep_s` and the usage cells are USED — the endpoint is read and host-redacted,
> and the token/cost args are converted, BEFORE that branch, then discarded, because this shape renders no
> endpoint cell and no usage cell. […] the values are read and dropped, not skipped.

Measurement (`probe_claim_audit_u5b.py` on the child worktree, `claim_audit_child.txt`):

| Clause | Measured on 50a3311 | Verdict |
|---|---|---|
| "`ep_s` is built" / "the endpoint goes unread" is explicitly denied | `str(endpoint)` invoked: `1a_endpoint_str_invoked: true`, events `["str", "split"]` | TRUE |
| "and host-redacted" | the `://` split runs on the value returned by `__str__`: `1b_redaction_split_invoked: true` | TRUE |
| "tokens_in/tokens_out/est_cost are converted" | `2a_usage_args_read_on_metadata_only: ["est_cost","tokens_in","tokens_out"]` (instrumented subclasses; control read `[]` before the call) | TRUE |
| "BEFORE this branch" | source order: conversions `:174-176`, `ep_s` build `:184-187`, branch `:232` | TRUE |
| "returns before `ep_s` and those figures are USED" | the metadata return at `:233-234` formats only `BANNER_HEAD, _lane_label, trig_s, model_s, BANNER_TAIL` | TRUE |
| "no endpoint cell and no usage cell" | `1i_line_has_endpoint_cell: false`, `1j_line_has_usage_cell: false` | TRUE |
| "Passing a secret-bearing URI leaves the rendered bytes unchanged" | `1f_byte_identical: true`; both `464bcba0…cb129` | TRUE |
| "no unpriced-model marker can appear" | `2b_unpriced_marker_present: false` with `tokens_in=120, tokens_out=40` | TRUE |
| lane validation / label from `lane_label()` | `not-a-lane -> ""`, `aux -> ""`, `uncensored-render -> shadow-self (uncensored)` | TRUE |
| "A caller that needs host redaction must use the normal shape" | normal shape: `… jev-latest @ api.secret-host.example | tok 10/5 | $0.000001` | TRUE |

The rendered line, measured: `· router · higher-self (frontier) | consult failed | empty response | test/model-a ·`
— identical with and without a secret-bearing endpoint object.

### F1 (M3, non-blocking) — the corrected sentence's line citations are stale

The same probe audits the pointers against the file that ships them (`5_pointers`):

| In-sentence citation | Child 50a3311 actually contains | Parent 9874d0e contains |
|---|---|---|
| `ep_s` "(:180-183)" | `ep_s` at **184-187**; :180-183 = the pre-existing "Redaction surface:" comment + `model_s` | :180 `ep_s = str(endpoint or "?")`, :181 `if "://" in ep_s:`, :183 slice — resolves |
| conversions "(:170-172)" | `ti/to/cost` at **174-176**; :170 = `try:`, :172 = the lane check | :170-172 resolves exactly |

The commit body repeats the same parent-tree numbers (`:180`, `:181-183`, `:170-172`, `:228`, `:224`); the
branch is at `:232` and the honest branch comment at `:228` in the shipped tree. The docstring grew by 4
lines above the code it cites and the numbers were not carried forward. Ruling: the *substance* survives
measurement — every mechanism clause is true — and a wrong navigation address is M3 by materiality (it
changes no claim about behaviour and no byte). Exact fix for the next touch:

    (:180-183) -> (:184-187)      (:170-172) -> (:174-176)

and the same for any prose that keeps `:228`/`:224`.

## 2. Residual copies of the old claim — PASS

Whitespace-flattened scan over every `.py/.yaml/.yml/.md/.txt/.json/.toml` in the tree (excluding `reviews/`
and `.git`) for: `before the endpoint is ever read`, `before the endpoint and usage args are read`,
`before the endpoint/usage args are read`, `before the endpoint is read`, `endpoint is read`,
`usage args are read`, `never read`, `goes unread`, `before the endpoint`, `returns before`.

Every hit, classified:

| Hit | Text | Ruling |
|---|---|---|
| `debug_banner.py:160` | "not because the endpoint **goes unread**" | negation — the corrected form |
| `debug_banner.py:162`, `:228` | "metadata_only **returns before** `ep_s` and those figures are USED" / "It returns BEFORE ep_s is used" | corrected form |
| `anchor_exec.py:1682` | "metadata_only returns before `ep_s` and the usage cells are USED" | corrected form |
| `tests/banners/test_r76_p2_u5_failure_banner.py:13` | "returns before `ep_s` and the usage cells are USED — the endpoint is read…" | corrected form |
| `tests/banners/test_r76_p2_u5_failure_banner.py:124` | "The claim says the endpoint is READ and host-redacted" | corrected form |
| `tests/banners/test_r76_p2_u5_failure_banner.py:153-156` | the four retired phrases, built for the tripwire | test that ASSERTS absence — allowed |
| `CHANGELOG.md:1323`, `:2149`; `core/usage_ledger.py:6`; `docs/r73-dead-code.md:22,183,384`; `passes/dispatcher_pre.py:183`; `tests/conftest.py:184`; `tests/decision_lane/test_d3_delivery_midturn_key.py:8`; `tests/routing/test_r40_consequence_gate.py:5`; `work_signal.py:8` | unrelated subjects (delivery edges, `min_fork_probability`, usage bodies, `clear_simple`, work-free turns) | not this claim |

Zero surviving instances outside the asserting pin. **Does the pin prevent a relapse?** Yes for the wordings
that existed: `test_no_site_still_claims_the_endpoint_goes_unread` flattens whitespace before matching, so
both originally-wrapped sites are covered (M3, M4, M5 below all caught), and it also inspects the runtime
`__doc__` of the pin. Residual limit, named: it is a **blacklist** — a different phrasing ("the endpoint
stays untouched", "neither the endpoint nor the figures is read") would pass it. That is acceptable because
the behavioural pin in item 5 covers the *code* side: if the code is changed to make the false claim true,
the pin goes red regardless of wording.

## 3. Byte identity 9874d0e vs 50a3311 — PASS

`probe_u5_byte_identity.py` (the predecessor's probe, re-run here on both worktrees; `realpath_{parent,child}.jsonl`)
drives `maybe_execute_anchored` with `anchored_call` returning an empty consult and captures the argument
handed to `park_anchor_banner`:

| model | parent 9874d0e sha256 | child 50a3311 sha256 | len |
|---|---|---|---|
| `test/model-a` | `464bcba0e5f10bb3bf57d11cbf0ae1f45377ae38966b2f1a36128d81d68cb129` | same | 84 |
| `""` → `?` | `7f2ada6b98d639ca74e8388fc147a7fe04d4fc249d500918152b90b6280bb292` | same | 73 |
| `provider://host.example.com/v1/secret-path?key=abc` | `086b9baf7a3ffa0d038a2bdf25ec64569f14f9f5fa4458aa37ad764a22f6284e` | same | 122 |

3/3 identical, and the commit body's abbreviated hashes match the measured full digests. Renderer-level
identity for the `metadata_only` shape: secret URI == `""` == `464bcba0…` on both trees.

## 4. Zero behaviour change; the 90-line test change is pins + docstrings — PASS

`git show --numstat 50a3311`:

```
8	4	anchor_exec.py
10	6	debug_banner.py
1	1	plugin.yaml
78	12	tests/banners/test_r76_p2_u5_failure_banner.py
1	1	tests/routing/test_r10_catalog_resolution.py
```

Production modules are AST-identical to `9874d0e` once docstrings are stripped (assertions printed by the
reviewer's run):

```
debug_banner.py AST-identical-after-docstring-strip: True
anchor_exec.py  AST-identical-after-docstring-strip: True
```

Test file, compared by AST function-by-function (asserts extracted with `ast.unparse`):

| function | change |
|---|---|
| `test_the_renderer_reproduces_the_frozen_string_exactly` | unchanged |
| `test_the_failure_path_parks_the_frozen_string` | unchanged |
| `test_an_invalid_lane_is_refused_where_the_hand_string_rendered` | unchanged |
| `test_the_label_comes_from_the_renderer_not_a_literal` | unchanged |
| `test_no_hand_formatted_banner_survives_in_anchor_exec` | unchanged |
| `test_the_normal_shape_is_untouched_by_the_new_keyword` | unchanged |
| `test_metadata_only_enforces_lane_validation_but_not_endpoint_redaction` | same 2 behavioural asserts (`invalid lane == ""`, `with_secret == _render()`); its ONE docstring-substring assert replaced by TWO on a whitespace-normalised `__doc__` — 3 asserts → 4, **strengthened, not weakened** |
| `test_the_documented_mechanism_matches_observable_behaviour` | **new** (4 asserts) |
| `test_no_site_still_claims_the_endpoint_goes_unread` | **new** (2 asserts) |

Zero tests deleted, zero behavioural asserts removed or loosened. The 78/12 line delta is docstrings (module
+ pin), the two new pins, and the `_FALSE_CLAIMS`/`_flat` helpers.

## 5. Pins: green, and mutation-killed — PASS

`pytest -q tests/banners/test_r76_p2_u5_failure_banner.py -p no:randomly` → **`9 passed in 0.73s`**.

`mutate.py` (`mutation_results.txt`) — each mutation copies the reviewed tree, applies ONE edit, and runs the
pin file from inside the copy:

| # | Mutation | Result |
|---|---|---|
| M1 | restore "Endpoint REDACTION does NOT apply: metadata_only returns before the endpoint is ever read" in the docstring | `2 failed, 7 passed` — caught by `test_metadata_only_enforces_lane_validation_but_not_endpoint_redaction` + `test_no_site_still_claims_the_endpoint_goes_unread` |
| **M2** | make the false claim **true in code**: move the `ep_s` build/redaction block below the `metadata_only` return | `1 failed, 8 passed` — caught by `test_the_documented_mechanism_matches_observable_behaviour` (`assert calls`) |
| M3 | reinstate the retired wording in the `anchor_exec.py` call-site comment | `1 failed, 8 passed` — caught by the tripwire |
| M4 | reinstate it wrapped across lines inside the test module's own docstring | `1 failed, 8 passed` — caught by the tripwire (flattening works) |
| M5 | reinstate `before the endpoint/usage args are read` in `anchor_exec.py` with a mid-sentence wrap | `1 failed, 8 passed` — caught by the tripwire |

M2 is the decisive one: the U5a-era pin could not distinguish the two readings; this one fails the moment the
code stops reading the endpoint. A pin that passes against both readings is not pinning — this one does not.

## 6. Version — PASS

`plugin.yaml:3 version: 6.13.23`; `tests/routing/test_r10_catalog_resolution.py:384 assert manifest["version"] == "6.13.23"`;
that test run standalone here: `1 passed in 0.37s`.

## 7. Full suite — PASS (7-variant, `r53::test_m1` GREEN)

Runner invoked here in the live tree:
`/opt/hermes/.venv/bin/python3 -m pytest -q` (`full_suite_50a3311.log`). Summary line VERBATIM:

```
7 failed, 2528 passed, 2 skipped, 1 deselected, 1 xfailed, 2 warnings, 2 errors in 329.02s (0:05:29)
```

Reds, mapped to the allowed baseline:

| Red | Allowed? |
|---|---|
| `tests/classification/test_p05_legacy_purge_guard.py::test_legacy_mention_allowlist_only_shrinks` | p05 legacy purge ✓ |
| `tests/config/test_p4_schema_budgets.py::test_b_no_max_tokens_literals_outside_budgets`, `::test_c_every_get_path_in_schema` | p4 schema budgets ×2 ✓ |
| `tests/routing/test_r33m_refusal_semantics.py::test_valmet_f2_channel_distrust_has_no_semantics`, `::test_valmet_fragments_have_no_semantics` | r33m ×2 ✓ |
| `tests/routing/test_rider20_fixes.py::test_d3_hook_budget_clamps_sync_and_revision_sum`, `::test_d3_a_large_hook_budget_still_hosts_the_fold` | rider20 d3 ×2 ✓ |
| ERROR `tests/routing/test_rider10_fixes.py::test_r10_2_audit_banner_carries_row_ref_after_persist`, `::test_r10_2_meta_carries_row_for_downgraded_async_banner` | rider10 banner row ×2 (both are teardown errors in the autouse `_zero_network_guard` fixture) ✓ |

Variant: **7 reds**, i.e. the `d3600b4` baseline shape — `decision_lane/test_r53_midturn_mindset.py::test_m1_envelope_carries_the_current_stance`
was **NOT red** in this in-suite run (the commit reported the 8-variant where it was red; that is the known
order-dependent row, both shapes acceptable). Passed count is `2528` vs the `2526` baseline — exactly the two
new pins, no other movement. No red outside the allowed list; no regression.

## Residuals (named, not blocking)

- **F1 (M3)** — stale line citations inside the corrected docstring (`:180-183`, `:170-172`; shipped tree is
  `:184-187`, `:174-176`). Exact replacement in §F1. Fix with the next edit of `debug_banner.py`; a repair
  cycle that only moves comments does not need its own unit.
- **R2 (M3)** — the anti-relapse tripwire is a phrase blacklist; a synonym relapse passes it (the behavioural
  pin catches the code side, not the prose side).
- The unit's own scope refusals (F3 "What that buys HERE" overstatement; `route_gate.py`'s hand-formatted
  banner) remain open by declaration and are not counted against this commit.

## Artefacts in this directory

- `probe_claim_audit_u5b.py` — instrumented claim audit (endpoint `__str__`/`split`, usage-arg `__int__`/`__float__`, pointer resolution)
- `claim_audit_child.txt`, `claim_audit_parent.txt` — driven output on `50a3311` and `9874d0e`
- `realpath_parent.jsonl`, `realpath_child.jsonl` — real empty-consult path, 3 models per tree, with sha256
- `mutate.py`, `mutation_results.txt` — 5 mutations, per-mutation caught-by
- `full_suite_50a3311.log` — full pytest run
