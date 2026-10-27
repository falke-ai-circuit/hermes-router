# R76-P4 — red-test baseline triage

**Unit:** R76-P4-S1-guard-baseline-truth · **Repo:** `/opt/data/plugins/hermes_router`
**Measured at:** HEAD `756e8ed`, 2026-10-27, `python3 -m pytest tests -q -p no:cacheprovider`
**Runner summary (verbatim, this machine):**

```
8 failed, 2527 passed, 2 skipped, 1 deselected, 1 xfailed, 2 warnings, 2 errors in 636.88s (0:10:36)
```

10 red node ids (8 failures + 2 teardown errors). The set is pinned in
`tests/baseline_failures.txt` and enforced by `scripts/baseline_check.py`.

## Why this file exists

The plan (§C clause 3) makes a slice GREEN on **failure-set equality against a
pinned baseline**, not on "0 failed", because the suite is red at HEAD for
reasons that predate the refactor. Pinning that fact without naming each red
test would institutionalise it — a pinned baseline nobody has read is folklore
with a checksum. So every line in the pin has a row here.

## Classes

| class | meaning | what it costs |
|---|---|---|
| **env** | the test is right and the code is unproven: the red comes from machine/tree/live state (network egress, review artifacts on disk, live session state), not from a plugin defect | gates the suite, not the release — but it must never be counted as evidence of correctness |
| **real** | the test is right and the code is wrong; a genuine defect a later unit must fix | a slice cannot be GREEN over an undiagnosed real failure |
| **accepted-debt** | the test pins a contract a later unit deliberately changed; the pin is stale, not the code | needs an explicit re-pin decision (and a note saying who decided), never a silent deletion |

## Rows

| # | node id | class | evidence (verbatim from the run) | mechanism | disposition |
|---|---|---|---|---|---|
| 1 | `tests/classification/test_p05_legacy_purge_guard.py::test_legacy_mention_allowlist_only_shrinks` | **real** | `AssertionError: new legacy mentions appeared: ['decision_midturn.py', 'reflex_weights.py']` | The guard pins an allowlist of package files still allowed to mention the legacy section string `uncensored_router`, and states it **may only shrink**. Two files now mention it and are not in `ALLOWED_FILES`: `decision_midturn.py:408,420` and `reflex_weights.py:69` (both dual-block config readers). The guard is doing its job — drift is real, not an env accident. | Later unit: migrate or route those two reads through the accessor, or extend the allowlist as a **declared** decision (the guard's own comment forbids the quiet version of this). |
| 2 | `tests/config/test_p4_schema_budgets.py::test_b_no_max_tokens_literals_outside_budgets` | **env** | `AssertionError: bare max_tokens literals outside core/budgets.py: ['api/router_tools.py:434', 'reviews/r76-p2-u5a-2026-1…` | The test asserts the hit list is exactly `["api/router_tools.py:434"]` (a documented carve-out). `_py_files()` walks the whole repo root, which includes `reviews/` — so review **artifacts** (`reviews/r76-p2-u5a-2026-10-27/probe_u5_byte_identity.py:45` and its `r76-p2-u5` twin) count as plugin code. No plugin module drifted. | Test-side: `_py_files()` should skip `reviews/` (capture artifacts are not shipped code). Not a source change → not S1. |
| 3 | `tests/config/test_p4_schema_budgets.py::test_c_every_get_path_in_schema` | **real** | `AssertionError: config paths not in SCHEMA: ['closure_pass.py: closure_pass']` | `closure_pass.py:221` calls `config_access.sub_block("closure_pass")`, and `closure_pass` is absent from `core/schema.py` `SCHEMA`. A live config read is unregistered, so it cannot be validated or documented from the schema — exactly what this guard exists to catch. | Later unit: register the path in `core/schema.py` (a schema addition, not a behaviour change *if* the default matches the current fail-open). |
| 4 | `tests/decision_lane/test_r53_midturn_mindset.py::test_m1_envelope_carries_the_current_stance` | **env** (declared **optional** in the pin) | `assert None is not None` at `:104` — `env["mindset"]["scope"]` is `None` | `test_m1` requests only `monkeypatch`, **not** the module's `_env` fixture (`:38-62`), and `_env` is what patches `anchor_exec._mindset_history`. So the stance is read from *live session state*: `decision._current_mindset` → `anchor_exec._mindset_history()` → `mindset.read_trace_vectors(...)`, and with no history for `s-r53-mindset` it correctly returns `{"scope": None, "rigour": None}`. The assertion asserts the opposite of what a hermetic environment can produce. | Test-side: add `_env` to the test signature (one word). Until then the pin is `optional`. |
| 5 | `tests/routing/test_r33m_refusal_semantics.py::test_valmet_f2_channel_distrust_has_no_semantics` | **real** | `assert True is False ... has_refusal_semantics("STOPPING AND FLAGGING SOMETHIN…")` | The R33m ruling binds refusal detection to the **ask's object**, never to channel/agent state. `VALMET_F2` is a status report, and the classifier now says it carries refusal semantics. The string carries the same construction as row 6 (`I'm not going to pretend …`), which suggests one construction is over-broad. | Later unit: narrow the decline construction so it requires an object-bound decline. Touching the classifier is a behaviour change → its own unit, not a guard slice. |
| 6 | `tests/routing/test_r33m_refusal_semantics.py::test_valmet_fragments_have_no_semantics` | **real** | `assert True is False ... has_refusal_semantics("I'm not going to pretend the G…")` | Same root cause as row 5: `"I'm not going to pretend the Gateway dispatch worked."` is a first-person account of what the assistant did not do, not a refusal of the ask. | Same unit as row 5. |
| 7 | `tests/routing/test_rider10_fixes.py::test_r10_2_audit_banner_carries_row_ref_after_persist` | **env** | `failed on teardown with "AssertionError: egress attempts recorded: [('socket', (('ollama.com', 443), 25.0, None))]"` | **Teardown-only**: every assertion in the test body passed. The autouse `conftest._zero_network_guard` records the socket and fails the test at `yield`. `_consult_meta` resolves its endpoint through the live provider config, so the container's configured provider host is dialled even though `anchored_call` is mocked; conftest patches `provider_prices.provider_catalog_entries`, not the model-resolution seam this path uses. | Test-side: mock the endpoint-resolution seam for this path. The same two ids error in **every** earlier review log at this repo (`r76-p1-u2e`, `r76-p2-u5a/u5b`, `r74`) — pre-existing and independent of S1. |
| 8 | `tests/routing/test_rider10_fixes.py::test_r10_2_meta_carries_row_for_downgraded_async_banner` | **env** | same teardown assertion, `('ollama.com', 443)` | Same mechanism as row 7 (same module, same autouse guard, same resolution seam). | Same fix as row 7. |
| 9 | `tests/routing/test_rider20_fixes.py::test_d3_hook_budget_clamps_sync_and_revision_sum` | **accepted-debt** | `AssertionError: the consult must be asked for both halves` / `assert False is True` | The test pins the R37-1 contract that the audit consult is asked with `fold_revision=True`. `completion_audit.py:1300-1310` now passes `fold_revision=False **BY DESIGN**`, with the recorded reason inline (the folded revision was the only path by which the frontier could author the delivered body; it never fired in 141 consults). The code changed deliberately; the pin was not updated. | Explicit re-pin: invert the assertion **with** the decision recorded, or restore the fold. Either way it is a product decision, not a refactor — P4 does not touch it. |
| 10 | `tests/routing/test_rider20_fixes.py::test_d3_a_large_hook_budget_still_hosts_the_fold` | **accepted-debt** | `assert False is True` (`recorded["fold"] is True`) | Same decision as row 9, exercised through the large-budget branch. | Same as row 9. |

## The `optional` class — what it is and what it is not

`test_m1` (row 4) is the only entry pinned as `optional`. Stated exactly:

- The brief's premise for this row was that it is **order-dependent** (fails
  standalone, passes in-suite, count 7 or 8). **That did not reproduce here.**
  Measured twice: standalone module run `1 failed, 16 passed in 39.45s`, and the
  full suite `8 failed … 2 errors` — red in both. What the row actually depends
  on is **state**, not order: the test never applies the fixture that patches
  `anchor_exec._mindset_history`, so its result is a function of the live session
  history DB. On a machine where that state exists it can go green; here it is
  red. Order is one way state can differ between invocations, not the cause.
- It is therefore pinned as `optional` — the honest encoding of "this test's
  colour is not a fact about the refactor" — rather than silently dropped from
  the pin (which would make a green run a lie) or pinned as `required` (which
  would fail the suite on any machine where it flips green).
- `scripts/baseline_check.py` accepts either colour for an `optional` entry and
  prints which variant it saw. It does **not** silently tolerate a superset: an
  unexpected red fails the check, and a required red that goes green fails the
  check (baseline drift must be re-pinned deliberately, in a commit that says so).

## Consequences for the slice gate

A later slice may claim GREEN only when `scripts/baseline_check.py` exits 0 —
i.e. its full-suite failure set equals the set above, with `optional` free to
move. The honest summary of the current gate is: **"equal to a baseline that is
9 red plus 1 state-dependent"**, never "green suite".
