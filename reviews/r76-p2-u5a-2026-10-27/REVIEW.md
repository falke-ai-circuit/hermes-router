# REVIEW — R76-P2-U5a (commit 2c243fc, hermes-router 6.13.22)

Reviewer: reviewer profile · 2026-10-27 · repo `/opt/data/plugins/hermes_router`
Target: `2c243fca83e61e7f61690f8e5cfbd2887d545718` "R76-P2 U5a (6.13.22): correct the U5 claim about what the metadata-only banner enforces"
Parent: `a3a3d41` (reviews-only commit over `d3600b4`, 6.13.21 — code identical to `d3600b4`)
Tree at review: HEAD `2c243fc`, clean.
Recipient: the fleet / the next unit that reads this docstring or the commit record and decides whether the
`metadata_only` shape is a redaction surface. Everything below is DRIVEN: both trees loaded as real packages
(`PYTHONPATH` pinning, `hermes_router.__file__` asserted), the real empty-consult path executed, the renderer
instrumented with a probe object that reports whether its `__str__` is invoked.

## VERDICT: **FIX-FIRST**

The behaviour is untouched and byte-identical, the scope is exact, the version is right, the suite is clean,
and the *enforcement* half of the corrected claim is true (lane validation binds, endpoint redaction does not
reach the rendered line, no unpriced marker). One sentence the commit adds — repeated in four places — is
measurably false about the code: `metadata_only` does **not** "return before the endpoint is ever read". The
endpoint is read (`ep_s = str(endpoint or "?")`, `debug_banner.py:180`) and host-redacted (`:181-183`) *before*
the `metadata_only` branch at `:228`; the redacted host is then discarded because the failure shape renders no
endpoint cell. The same commit's own branch comment states it correctly ("returns BEFORE `ep_s` is used",
`:224`), so the record contradicts itself. For a unit whose entire deliverable is a truthful claim, that is
the defect it exists to remove — one-sentence fix, no bytes move.

## Verdict table

| # | Check | Result | Evidence class |
|---|-------|--------|----------------|
| 1 | Claim vs driven behaviour | **FAIL** (mechanism clause false in 4 places; enforcement half true) | E1 (instrumented renderer) + E2 |
| 2 | Byte identity vs parent `a3a3d41` | PASS (3/3 sha256 equal) | E2 (both trees driven) |
| 3 | No real redaction / no behaviour change introduced | PASS (AST after docstring-strip identical) | E1 |
| 4 | New pin pins true behaviour, mutation-killed | PASS for behaviour; gap: call-site comment unguarded | E2 (4 mutations) |
| 5 | Redaction vacuity still same, now DOCUMENTED | PASS | E1 (quote below) |
| 6 | Scope = 5 declared files; `route_gate` left alone | PASS | E1 |
| 7 | Version 6.13.22 in `plugin.yaml` + catalog pin | PASS | E1 |
| 8 | Full suite, allowed reds only | PASS (7F/2526P/2E; `r53::test_m1` NOT red) | E3 (runner executed here) |

## 1. Claim vs driven behaviour — **FAIL**

Driven measurement (`probe_claim_audit.py`, output `claim_audit.txt`):

```
1a_bytes_secret_ep   '· router · higher-self (frontier) | consult failed | empty response | test/model-a ·'
1b_bytes_empty_ep    '· router · higher-self (frontier) | consult failed | empty response | test/model-a ·'
1c_byte_identical    True
1d_sha_secret        464bcba0e5f10bb3bf57d11cbf0ae1f45377ae38966b2f1a36128d81d68cb129
1e_sha_empty         464bcba0e5f10bb3bf57d11cbf0ae1f45377ae38966b2f1a36128d81d68cb129
5a_endpoint_str_called_on_metadata_only  ['openrouter://api.secret-host.example/v1']
```

So `format_banner(..., ep=<secret URI>, metadata_only=True)` is byte-identical to `ep=""`, and the banner
shows no endpoint cell at all — exactly what the brief asks to see. Statement-by-statement audit of the text
the commit added:

| Added assertion (location) | Measured | Verdict |
|---|---|---|
| "What that buys HERE: lane VALIDATION and the lane label come from format_banner" (`anchor_exec.py:1678-1679`) | label: true; validation: the renderer validates, but this call passes the fixed literal `"frontier-anchor"`, so refusal can never fire at this site | overstated ("buys HERE") — M3, see §4 |
| "It does NOT buy endpoint redaction or the unpriced marker" (`anchor_exec.py:1680-1681`) | no endpoint cell, no cost cell in the rendered line | TRUE |
| "metadata_only returns before the endpoint and usage args are read" (`anchor_exec.py:1681-1682`) | endpoint `__str__` invoked on this path (5a); `ti/to/cost` converted at `:170-172`, before `:228` | **FALSE** |
| "so they are passed empty and inert" (`anchor_exec.py:1682`) | the call passes `""`, `0, 0, 0.0` | TRUE (about the call) |
| "The trigger literal is not an `_END_LABELS` key, so that map is a no-op here too" (`anchor_exec.py:1683-1684`) | literal renders verbatim; `trigger="consult"` → `pre-check` (4a/4b) | TRUE |
| "Byte-identical to the hand-formatted text" (`anchor_exec.py:1685`) | sha256 equal to parent on 3 models | TRUE |
| "lane VALIDATION applies (FORBIDDEN_LANES or an unknown lane returns \"\")" (docstring) | `not-a-lane`/`aux`/`flash` → `""`; `frontier-anchor` renders | TRUE (renderer level) |
| "the lane label comes from lane_label()" (docstring) | `uncensored-render` → `shadow-self (uncensored)`, `decision` → `impulse (decision)` | TRUE |
| "Endpoint REDACTION does NOT apply: metadata_only returns before the endpoint is ever read, so `endpoint` is accepted and ignored" (docstring `debug_banner.py:159-161`) | read at `:180`, host-redacted at `:181-183`, branch at `:228` — the redaction *runs*, the result is discarded | **FALSE mechanism** (output claim true) |
| "passing a secret-bearing URI here leaves the rendered bytes unchanged" (docstring) | 1c | TRUE |
| "tokens_in/tokens_out/est_cost are likewise accepted and discarded, so no unpriced-model marker can appear" | no `$0.0000*`, no `tok` cell | TRUE (observably) |
| "A caller that needs host redaction must use the normal shape" | normal shape: `m1 @ api.secret-host.example | tok 10/5 | $0.000001` | TRUE |
| "It returns BEFORE `ep_s` is used" (branch comment `:224`) | `ep_s` computed but not used in the returned format | TRUE — and this is the honest form of the sentence above |
| "`metadata_only` returns before the endpoint/usage args are read" (test module docstring l.13; pin docstring l.102; commit body) | same measurement as above | **FALSE** |

The false clause appears in four places: `debug_banner.py:159-160`, `anchor_exec.py:1681-1682`, the test
module docstring (line 13), the new pin's docstring (line 102), and the commit message. It is not a
hallucinated detail — it is the *reason* given for a guarantee that does hold, and it is contradicted by
`debug_banner.py:224` in the same commit. Fix wording, never bytes:
"metadata_only returns before `ep_s` is used; the endpoint is read and host-redacted, then discarded because
this shape renders no endpoint cell."

## 2. Byte identity — PASS (E2)

`probe_u5_byte_identity.py` (the predecessor's, re-run here) drives `maybe_execute_anchored` with
`anchored_call` returning an empty consult and captures the argument handed to `park_anchor_banner`.

| model | parent `a3a3d41` sha256 | child `2c243fc` sha256 | len |
|---|---|---|---|
| `test/model-a` | `464bcba0e5f10bb3bf57d11cbf0ae1f45377ae38966b2f1a36128d81d68cb129` | same | 84 |
| `""` → `?` | `7f2ada6b98d639ca74e8388fc147a7fe04d4fc249d500918152b90b6280bb292` | same | 73 |
| URL-shaped model | `086b9baf7a3ffa0d038a2bdf25ec64569f14f9f5fa4458aa37ad764a22f6284e` | same | 122 |

Rendered line on both: `· router · higher-self (frontier) | consult failed | empty response | test/model-a ·`.
The commit body's three abbreviated hashes (`464bcba0..`, `7f2ada6b..`, `086b9baf..`) match the measured
full digests. No byte moved.

## 3. No real redaction introduced — PASS

Mechanical proof that the unit is comment/docstring-only: both production modules are AST-identical to the
parent once docstrings are stripped.

```
debug_banner.py AST-identical-after-docstring-strip: True
anchor_exec.py  AST-identical-after-docstring-strip: True
```

`git show --numstat`: `anchor_exec.py 7/4`, `debug_banner.py 16/3` — every added/removed line in those two
files is a comment or docstring line. No behaviour was changed to "fix" the vacuity, so the byte-identity
requirement is not contradicted anywhere.

## 4. Pin — pins true behaviour, but does not guard the place the old claim lived

`tests/banners/test_r76_p2_u5_failure_banner.py` — 7 passed (`7 passed in 0.57s`). The new pin
`test_metadata_only_enforces_lane_validation_but_not_endpoint_redaction` asserts `_render(lane="not-a-lane") == ""`
(behaviour), `with_secret == _render()` (behaviour), and a docstring substring.

Mutations (worktree `/opt/data/tmp/u5a-mut/hermes_router` @ 2c243fc, `mutation_results.txt`):

| mutation | result |
|---|---|
| M1 docstring claims redaction **applies** | `1 failed, 6 passed` — caught |
| M1b docstring sentence removed | `1 failed, 6 passed` — caught |
| M2 boundary broken (`@ ep_s` added to the failure shape) | `3 failed, 4 passed` — caught (byte pin + real-path pin + new pin) |
| M3 old overstated claim re-added as a comment **at the call site** (`anchor_exec.py`) | `7 passed` — **NOT caught** |
| M4 pin's sentence kept, contradicting sentence appended after it | `7 passed` — **NOT caught** |

So the pin distinguishes the honest from the dishonest docstring at the one guarded sentence, and it kills
behaviour drift — requirement met. Two gaps, neither blocking: the claim's original home (`anchor_exec.py`
comment) is unguarded, and the doc check is a substring, so a contradicting sentence can sit next to the
pinned one. A `!= "metadata_only" and "redaction"` style source tripwire over `anchor_exec.py`, or an
assert `"before the endpoint is ever read" not in src`, would close both cheaply.

## 5. Sophistication check — PASS: vacuous in the same way, now documented

Redaction is still structurally absent on this shape (endpoint read, host-extracted, discarded; the failure
shape renders no endpoint cell) — identical to the parent (`url_ep_equals_empty: True` on both). What changed
is that it is no longer advertised. The line that carries it:

> "Endpoint REDACTION does NOT apply: metadata_only returns before the endpoint is ever read, so `endpoint` is
> accepted and ignored — passing a secret-bearing URI here leaves the rendered bytes unchanged."
> — `debug_banner.py:159-162`

The guarantee stated is the true one; only its causal clause is wrong (§1). Also documented, correctly:
"the trigger literal is not an `_END_LABELS` key" and "tokens_in/tokens_out/est_cost ... accepted and
discarded, so no unpriced-model marker can appear".

## 6. Scope — PASS

`git show --stat 2c243fc`: `anchor_exec.py` (7/4), `debug_banner.py` (16/3), `plugin.yaml` (1/1),
`tests/banners/test_r76_p2_u5_failure_banner.py` (+25), `tests/routing/test_r10_catalog_resolution.py` (1/1).
Exactly the five declared files; no deletions, no reformat, no unrelated edits.

`route_gate.py:1648-1650` still hand-formats its own frontier banner
(`"· router · higher-self (frontier) | consult FAILED (routing) — ask not routed ·"`) and is **unchanged**
in this commit. Correctly left for its own unit: it is a different message on a different code path, and
folding it in would have widened a claim-correction unit into a behaviour change. Note: the commit cites
`route_gate.py:1648`, which is the correct current line; the predecessor's `1620-1624` was stale.

## 7. Version — PASS

`plugin.yaml:3` → `version: 6.13.22`; `tests/routing/test_r10_catalog_resolution.py:384` →
`assert manifest["version"] == "6.13.22"`. No remaining `6.13.21` outside `reviews/`.

## 8. Full suite — PASS, runner summary VERBATIM

`/opt/hermes/.venv/bin/python3 -m pytest -q` in the live tree (`full_suite_2c243fc.log`):

```
7 failed, 2526 passed, 2 skipped, 1 deselected, 1 xfailed, 2 warnings, 2 errors in 314.20s (0:05:14)
```

Reds, all inside the allowed set: `test_p05_legacy_purge_guard::test_legacy_mention_allowlist_only_shrinks`,
`test_p4_schema_budgets::test_b_no_max_tokens_literals_outside_budgets`,
`test_p4_schema_budgets::test_c_every_get_path_in_schema`,
`test_r33m_refusal_semantics::test_valmet_f2_channel_distrust_has_no_semantics`,
`test_r33m_refusal_semantics::test_valmet_fragments_have_no_semantics`,
`test_rider20_fixes::test_d3_hook_budget_clamps_sync_and_revision_sum`,
`test_rider20_fixes::test_d3_a_large_hook_budget_still_hosts_the_fold`; errors = the two
`test_rider10_fixes` banner-row cases.

Variant: the in-suite one — `r53::test_m1` was **not** red. Against the d3600b4 baseline
(7F/2525P/2E) this is 7F/**2526**P/2E: exactly +1 pass, the new pin. No regression.

## Findings

- **F1 (blocking, M1)** — The claim repair still contains a false mechanism sentence, in four places plus the
  commit body: "metadata_only returns before the endpoint is ever read" / "before the endpoint and usage args
  are read". Measured: endpoint `__str__` invoked on the metadata_only path; `ep_s` read at `debug_banner.py:180`
  and host-redacted at `:181-183`, both before the branch at `:228`; `ti/to/cost` converted at `:170-172`. The
  same commit states it correctly at `:224` ("before `ep_s` is used"). Evidence: `claim_audit.txt` 5a/6_*.
- **F2 (M2)** — The pin does not guard the call-site comment (`anchor_exec.py`), the original home of the false
  claim: M3 mutation passes 7/7. Doc assertion is substring-only (M4 passes).
- **F3 (M3)** — `anchor_exec.py:1678` "What that buys HERE: lane VALIDATION" reads as an active guard; the call
  passes a fixed literal, so nothing can be refused there. Label half is true.

## Minimal fix list

1. Restate the mechanism in the docstring, the `anchor_exec.py` comment, the test-module docstring, the pin
   docstring and the commit record: "the endpoint is read and host-redacted, then discarded — this shape
   renders no endpoint cell, so no redaction reaches the reader." Do not touch rendered bytes (§2 stands).
2. Optional, cheap: add a source tripwire over `anchor_exec.py` rejecting "endpoint ... read" /
   "host redaction" phrasing, and `assert "before the endpoint is ever read" not in src`, closing the M3/M4 gaps.
3. No change to `anchor_exec.py` / `debug_banner.py` logic; no change to `route_gate.py` (own unit).

## Evidence artifacts

- `probe_claim_audit.py` / `claim_audit.txt` — every added assertion driven; endpoint-read instrumentation
- `probe_u5_byte_identity.py` (copied from the U5 review) + `parent_a3a3d41.json`, `child_2c243fc.json`
- `probe_u5_bypass.py` + `bypass_child_2c243fc.txt` — renderer driven with adversarial lanes/endpoints
- `mutate.py` / `mutation_results.txt` — four mutations against the new pin
- `full_suite_2c243fc.log` — full pytest run
