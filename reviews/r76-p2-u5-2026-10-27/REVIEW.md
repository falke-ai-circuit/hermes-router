# REVIEW — R76-P2-U5 (commit c8df8c4, hermes-router 6.13.19)

Reviewer: reviewer profile · 2026-10-27 · repo `/opt/data/plugins/hermes_router`
Target commit: `c8df8c4` "R76-P2-U5 (6.13.19): the frontier empty-consult banner goes through format_banner"
Parent: `c094cba` (6.13.18) · tree at review time: HEAD `9912ffa` (reviews-only commits on top), clean
Method: recipient = the fleet reading a delivered banner. Everything below was driven, not read:
the real `maybe_execute_anchored` empty-consult path was executed against BOTH trees (parent worktree
`/opt/data/tmp/u5-wt/hermes_router` @ c094cba, child = live tree @ c8df8c4) and the text handed to
`park_anchor_banner` was captured and hashed.

## VERDICT: **FIX-FIRST**

The code change is correct, in scope, and byte-identical on the wire. The unit's *claim* about which
bypasses it closes is overstated: two of the four named bypasses are structurally inapplicable to the
shape it added, and one of the two the brief asked to see proven actively (host redaction) is not
closed at all. Nothing here is a runtime regression — the blocking defect is a false provenance claim
in the permanent record plus a same-class site left hand-formatting.

## Verdict table

| # | Check | Result | Evidence class |
|---|-------|--------|----------------|
| 1 | Call site routed through format_banner | PASS | E1 (source + mutation) |
| 2 | Byte-identity of rendered line vs parent | PASS | E2 (both trees driven, sha256 equal) |
| 3 | Bypasses actively closed (≥2, incl. host redaction) | **FAIL** | E1 (renderer driven with adversarial inputs) |
| 4 | Scope = 5 declared files, nothing else | PASS | E1 (`git show --stat`, `--numstat`) |
| 5 | Pins pin real behaviour | PASS | E2 (FROZEN re-derived from parent; mutation-killed) |
| 6 | Version 6.13.19 in plugin.yaml + catalog pin | PASS | E1 |
| 7 | Full suite, no pass-count drop | PASS | E3 (runner executed here) |

## 1. The call site goes through the renderer — PASS

`anchor_exec.py:1683-1687` (empty-consult branch of `maybe_execute_anchored`):

```
1683                _fb = _dbf.format_banner(
1684                    "frontier-anchor", "consult failed | empty response", _fb_model,
1685                    "", 0, 0, 0.0, 0.0, metadata_only=True)
1686                _dbf.park_anchor_banner(
1687                    str(session_id or ""), _fb,
```

No hand-formatted `BANNER_HEAD` assembly remains on that path — repo-wide grep for the old format
string returns only `anchor_exec.py:1684` (the new call) and the pin file; `grep BANNER_HEAD` outside
`debug_banner.py`/tests hits `decision.py:124`, `reflex_lane.py:884` and `gate/orchestration.py` (reads,
not this banner). `metadata_only` has exactly one call site (grep: `anchor_exec.py:1685`), so the
default shape is untouched for the other 12 callers — confirmed live: `format_banner("frontier-anchor",
"midturn", "m1", "openrouter://api.host.example/v1", 10, 5, 0.000001, 0.4, level=1)` still renders
`· router · higher-self (frontier) | midturn | m1 @ api.host.example | tok 10/5 | $0.000001`.

## 2. Byte-identity — PASS (E2, both trees executed)

`probe_u5_byte_identity.py` loads the tree given on argv, asserts `hermes_router.__file__` is under it,
monkeypatches the consult to return an empty response, drives `maybe_execute_anchored`, and captures the
first argument handed to `park_anchor_banner`.

| model | parent c094cba sha256 | child c8df8c4 sha256 | len |
|---|---|---|---|
| `test/model-a` | `464bcba0e5f10bb3…` | `464bcba0e5f10bb3…` | 84 |
| `""` (falls back to `?`) | `7f2ada6b98d639ca…` | `7f2ada6b98d639ca…` | 73 |
| `provider://host.example.com/v1/secret-path?key=abc` | `086b9baf7a3ffa0d…` | `086b9baf7a3ffa0d…` | 122 |

Rendered line, identical in both: `· router · higher-self (frontier) | consult failed | empty response
| test/model-a ·`. This also independently confirms the pin's `FROZEN` constant was captured from the
real parent path, not invented (see §5). The structural reason it *can* be identical:
`format_banner`'s new return is `"%s %s | %s | %s %s" % (BANNER_HEAD, lane_label("frontier-anchor"),
trig_s, model_s, BANNER_TAIL)`, and `lane_label("frontier-anchor")` → `higher-self (frontier)`,
`trig_s` is unmapped for this literal, `model_s` is `_fb_model` (already `[:80]`) re-sliced `[:120]`.

## 3. Bypasses "closed" — **FAIL** (this is the blocking finding)

Measured by driving the renderer with adversarial inputs (`probe_u5_bypass.py`, output in
`bypass_probe.txt`):

| bypass | claimed | actual |
|---|---|---|
| `VALID_LANES`/`FORBIDDEN_LANES` | closed | **closed in the renderer, unreachable at the call**: `not-a-lane` → `''`, `aux` → `''`, `flash` → `''`, `''` → `''`. But the call site passes the hard-coded literal `"frontier-anchor"`, so no input can ever be refused here. Defense-in-depth, not an active guard. |
| `_END_LABELS` pre/post mapping | closed | **applies but is a no-op at the call**: `trigger="consult"` → `pre-check`, `completion_audit` → `post-check`, but the call passes the fixed literal `"consult failed | empty response"`, which is not a key in the map. |
| `"://"` host redaction | closed | **NOT CLOSED — vacuous.** `metadata_only` returns before `ep_s` is ever read; the call passes `""`. `format_banner(..., ep="openrouter://api.secret-host.example/v1/very-secret?key=abc", metadata_only=True)` is **byte-identical** to `ep=""` (`url_ep_equals_empty: True`). A URL-shaped *model* renders verbatim in BOTH trees: `... | provider://host.example.com/v1/secret-path?key=abc ·`. So this banner carries no redaction at all — same as the parent, which is correct for byte-identity but contradicts the unit claim. |
| unpriced-model marker (`$0.0000*`) | closed | **NOT CLOSED — structurally absent.** The early return precedes the usage/cost cell; `tokens_in/out/est_cost` are accepted and discarded (`ti=120, to=40, cost=0.0` still renders no cost cell). |
| lane label from `lane_label()` (bonus) | closed | **closed, actively**: `uncensored-render`/`shadow` → `shadow-self (uncensored)`, `decision` → `impulse (decision)`. |

So the honest count is: lane whitelist + label mapping genuinely closed (both at renderer level); trigger
mapping is present-but-dead; redaction and unpriced marker are unreachable by construction. The brief
asked for two *active* closures with redaction as the worked example; that example fails.

Why this is blocking rather than cosmetic: the banner is a provenance artifact and the commit message is
the permanent record of what it guarantees. A future reader (or the next unit that "reuses the
metadata_only shape for a different failure path") will assume redaction is enforced here and will pass
an endpoint expecting it to be redacted. The fix is a documentation correction, not a byte change —
changing bytes to make redaction "real" would violate the harder requirement in §2.

## 4. Scope — PASS

`git show --stat c8df8c4`: `anchor_exec.py` (+7/−3), `debug_banner.py` (+18/−2), `plugin.yaml` (1/1),
`tests/banners/test_r76_p2_u5_failure_banner.py` (new, 102), `tests/routing/test_r10_catalog_resolution.py`
(1/1 — version pin). Exactly the declared five, nothing else. No unrelated reformat, no test deletions.

Same-class site `route_gate.py:1620-1624` still hand-formats its own frontier banner
(`"· router · higher-self (frontier) | consult FAILED (routing) — ask not routed ·"`) and bypasses the
renderer. Correctly *not* touched here (out of unit scope) and honestly declared in the commit body —
but it means the family is not closed, and the string is a different message, so this unit cannot be
read as covering it.

## 5. Pins — PASS (real behaviour, mutation-killed)

`tests/banners/test_r76_p2_u5_failure_banner.py`, 6 tests, all green at c8df8c4 (`6 passed in 0.76s`).

Is `FROZEN` a real pre-change capture or the code's own output? Independently re-derived: the parent
worktree produces the exact same sha256 for the same input, and the parent has no `metadata_only`
keyword at all — so the constant was captured from the hand-formatted path, not read back out of the
new formatter.

Mutation checks (in throwaway worktree `/opt/data/tmp/u5-wt-c/hermes_router` @ c8df8c4):
- one extra space in the `metadata_only` return format → `test_the_renderer_reproduces_the_frozen_string_exactly`
  and `test_the_failure_path_parks_the_frozen_string` both FAIL (`2 failed, 4 passed`). Real assertion.
- call site reverted to the old hand-formatted string (byte output identical) →
  `test_no_hand_formatted_banner_survives_in_anchor_exec` FAILS (`1 failed, 5 passed`). The routing
  claim is pinned by the source tripwire, not by the byte test — which is the right split, but it does
  mean the anti-revert guarantee is a string grep, not a behavioural assertion.
- `test_the_normal_shape_is_untouched_by_the_new_keyword` is weak (`BANNER_TAIL not in ... rsplit("| tok")`
  is near-tautological); harmless, not load-bearing.

## 6. Version — PASS

`plugin.yaml:3` → `version: 6.13.19`; `tests/routing/test_r10_catalog_resolution.py:384` →
`assert manifest["version"] == "6.13.19"`. Only two `6.13.19` occurrences in the tree (plus reviews) —
no leftover `6.13.18` pin.

## 7. Full suite — PASS, runner summary VERBATIM

Command: `/opt/hermes/.venv/bin/python3 -m pytest -q` from the live tree (log:
`full_suite_c8df8c4.log`).

```
6 failed, 2510 passed, 2 skipped, 1 deselected, 1 xfailed, 2 warnings, 2 errors in 345.80s (0:05:45)
```

Failures = the allowed red set, unchanged: `test_p05_legacy_purge_guard::test_legacy_mention_allowlist_only_shrinks`,
`test_p4_schema_budgets::test_c_every_get_path_in_schema`, `test_r33m_refusal_semantics::test_valmet_f2_channel_distrust_has_no_semantics`,
`test_r33m_refusal_semantics::test_valmet_fragments_have_no_semantics`,
`test_rider20_fixes::test_d3_hook_budget_clamps_sync_and_revision_sum`,
`test_rider20_fixes::test_d3_a_large_hook_budget_still_hosts_the_fold`; errors = the two
`test_rider10_fixes` cases as at baseline.

No drop: standing baseline 6F/2495P, parent c094cba measured 6F/2504P, this commit 6F/2510P — exactly
+6, the six new pins. Note: the commit message records "7 failed, 2509 passed"; the rerun gives 6F/2510P.
Neither number shows a regression, but the commit's own recorded suite line does not reproduce and should
not be trusted as a frozen figure.

## Findings

- **F1 (blocking, M1)** — Unit claim overstates closure: `"://"` host redaction and the unpriced-model
  marker are not closed on the `metadata_only` shape (endpoint and usage args are discarded before use;
  a URL-shaped model renders verbatim in parent and child). Evidence: `bypass_probe.txt`,
  `byte_identity_probe.txt`.
- **F2 (follow-up, M2)** — `route_gate.py:1620-1624` hand-formats a same-class frontier banner beside the
  renderer. Declared in the commit body, not fixed; needs its own unit.
- **F3 (M3)** — Commit-message suite numbers (7F/2509P) do not reproduce (6F/2510P).
- **F4 (M3)** — Anti-revert guarantee rests on a source-string grep pin; weak default-shape pin.

## Minimal fix list

1. Correct the unit record: list the bypasses that actually bind here (`VALID_LANES` whitelist,
   `lane_label()` mapping) and state plainly that host redaction and the unpriced marker are
   structurally inapplicable because the shape renders no endpoint and no usage cell. Drop them from
   the "closed" list. Do not alter the rendered bytes.
2. Extend the `metadata_only` docstring with one line: endpoint/usage arguments are accepted for signature
   symmetry and are **ignored** — this shape enforces no redaction, so a caller that needs redaction must
   not rely on it.
3. File `route_gate.py:1620` as its own unit (same-class bypass, different message).
4. No change required to `anchor_exec.py` / `debug_banner.py` logic; re-verify only the amended claim.

## Evidence artifacts
- `probe_u5_byte_identity.py` — drives the real empty-consult path in a given tree
- `byte_identity_probe.txt` — parent vs child, three models, sha256 comparison
- `probe_u5_bypass.py` / `bypass_probe.txt` — renderer driven with invalid/forbidden lanes, mapped
  triggers, URL-shaped endpoint and model, unpriced tokens, default shape
- `full_suite_c8df8c4.log` — full pytest run
