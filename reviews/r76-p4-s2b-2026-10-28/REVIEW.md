# R76-P4-S2b — re-review of 96c9c0c (repair of FIX-FIRST on aa1f846)

Reviewer: reviewer (read-only). Date: 2026-10-28. HEAD: 96c9c0c, tree clean (`git status --porcelain` = only untracked `reviews/`).
Artefact under review: `.importlinter` (+70/-7) and NEW `tests/test_import_guard_liveness.py` (182 lines).

## Request frame

The unit is the guard config, not the product. aa1f846's FIX-FIRST named four defects; this review answers
exactly one question: **did those four land, and did they land correctly** (a corrected figure that is wrong,
or a liveness test that asserts nothing, is a regression in its own right).

Recipient of this deliverable: the next agent who edits `.importlinter` or adds a module under
`features/`/`lanes/` and trusts the guard to shout. Recipient definition of satisfied: every contract is
falsifiable, exemptions are pinned to the six live binds, the hub blind spot is closed, and every number in
the NOTE can be re-measured.

## Scope confirmation (from the diffstat, not the message)

```
.importlinter                       |  70 ++++++++++++++--
tests/test_import_guard_liveness.py | 182 ++++++++++++++++++++++++++++++++
2 files changed, 245 insertions(+), 7 deletions(-)
```
No source module touched — confirmed.

## Use-it evidence

Baseline run of the deliverable (own tree, own command):
`PYTHONPATH=/opt/data/plugins lint-imports` → `Analyzed 135 files, 850 dependencies`, **8 contracts, 0 broken**,
`gate may not import api (direct) KEPT (6 ignored imports)`, `features/lanes may not import the root package
hermes_router (direct) KEPT (5 ignored imports)`.
Liveness battery run: `pytest tests/test_import_guard_liveness.py` → `1 passed in 2.31s`.

## Fix-by-fix verification

### 1. Wildcard gone → six exact pairs, and a seventh bind breaks the contract — LANDED

- `.importlinter:157-162`: six explicit `gate.registration -> api.{commands, commands_config, commands_diag,
  commands_runtime, config_writer, router_tools}` entries; no `api.*` anywhere in the ignore block.
- Live run reports **6 ignored imports** on that contract (not "6 ignored + 1 unmatched", and no
  `No matches for ignored import` exit).
- Hostile probe (scratch copy, `api/newthing.py` created + `from ..api import newthing` appended to
  `gate/registration.py`, real `lint-imports`):
  `gate may not import api (direct) BROKEN (6 ignored imports)` / `Contracts: 7 kept, 1 broken`, exit 1.
  Under the old wildcard this seventh bind was swallowed. E1.
- Six pairs match the source: `gate/registration.py:32` binds exactly those six names.

### 2. Sixth root-package contract + hub-spelling probes — LANDED

`[importlinter:contract:features-lanes-no-root]` (`.importlinter:223+`) with `as_packages = False`,
enumerated sources, `forbidden_modules = hermes_router`, 5 facility ignores.

Re-ran my original check-3 probes on a scratch copy:

| probe | result |
|---|---|
| `from ... import router_tools` → `features/banners/lifecycle.py` | **BROKEN** (hub contract), 7 kept / 1 broken, exit 1 |
| `from .. import router_tools` → `lanes/registry.py` | **BROKEN** (hub contract), 7 kept / 1 broken, exit 1 |

Both were KEPT on aa1f846; both now flip. On the spelling: the brief writes `from ...` for `lanes/registry.py`,
but three dots there is a beyond-top-level import (no graph edge) — I re-ran it as spelled and it stays
`8 kept, 0 broken`, which is why the test's one-deep spelling (`from ..`) is the correct probe. Not a defect;
the file documents this at `tests/test_import_guard_liveness.py:38-42`. E1.

### 3. NOTE figures re-measured — LANDED, both replacements are correct

Method: rebuilt the layers ladder (api | gate | passes | features | lanes | core) on a scratch copy of HEAD and
counted `is not allowed to import` pair headers and `- ` route lines from real `lint-imports` runs.

| claim in NOTE | measured now (96c9c0c) | verdict |
|---|---|---|
| `type = layers` → 11 illegal pairs / 45 routes | 11 / 45 | reproduces |
| both true upward edges exempted → 11 / 38 | 11 / 38 | reproduces |
| hub neutralised → **4 illegal pairs / 5 routes** (replacement for "7 pairs / 19 module pairs") | **4 / 5** | correct |
| hub-involved edges = 58 | 58 (29 in + 29 out) | reproduces |
| **29 distinct entry edges** (replacement for "32") | 29 modules import `hermes_router` root | correct |
| "92 modules / 320 edges" (retained, dated 03b0a36) | 93 modules / **320** edges today | edge count exact; module delta is tree drift since the stated commit — acceptable, the figure is commit-stamped |

The four withdrawn figures ("31 of 43 routes", "7 pairs / 19 module pairs", "32 entry edges", "238 sites in
37 files") are gone from the file; no stale copy of them survives in the NOTE. E2.

### 4. Liveness battery — EXISTS AND BITES

Read in full (182 lines) then exercised it three ways:

- **Control + 8 probes**: one genuine upward direct import per contract, injected into a `shutil.copytree`
  throwaway, `lint-imports` re-run, asserts `returncode == 1` **and** `"<contract name> BROKEN" in output` —
  so a run that fails for an unrelated reason does not satisfy it. The copy's `hermes_router.__file__` is
  asserted to live under the tmp dir (`:152-158`), which is what makes the BROKEN assertions meaningful.
- **Fails loudly on a non-flip** (the "asserts nothing" failure mode): I replaced one probe's injection with
  `import os` in a scratch copy of the test → `AssertionError ... 0 = CompletedProcess(... lint-imports ...).returncode`,
  test FAILED, no skip. Correct.
- **Fails loudly on an unguarded new module**: added `features/patterns/newmod.py` in a scratch copy →
  `AssertionError: hermes_router.features.patterns.newmod missing from the hub contract sources`. The
  enumerated-source completeness check bites. Correct.
- **Skips only when the tool is missing**: single `skipif(LINTER is None, ...)`; no other skip path, no
  try/except around the probes. The suite's zero-network tripwire is bypassed by capturing `_REAL_RUN`
  at import (`:35`), which is legitimate — and it demonstrably worked, the test passed inside the suite.
- Temp-copy only: the live tree's `git status` stayed clean throughout my runs of it.

E1 on all four.

## Residual finding (non-blocking, does not gate this repair)

**F1 — same blind spot survives one layer up: `gate/` and `passes/` can import the root package invisibly.**
`.importlinter:223+` scopes root-package detection to `features.*` and `lanes.*`. Probes on a scratch copy:
`from .. import router_tools` appended to `gate/orchestration.py` → `8 kept, 0 broken`; same into
`passes/dispatcher_post.py` → `8 kept, 0 broken`. At runtime both are upward imports of `api.router_tools`
through the hub. Pre-existing, unchanged by this commit, and outside the four requested fixes — but it is the
identical defect class fix 2 closed, so the guard is "blind spot closed" for two of four upper layers. Next
slice, not this one.

Also noted, cosmetic: `_enumerated_sources()` catches a *missing* module but not a stale entry naming a
deleted module; `lint-imports` would then be the one to complain, and the control run asserts exit 0, so the
hole is covered indirectly.

## On the suite-flake verdict

Accepted, with the recorded logs (E3, not a re-run): the head log's failure set is
`p05_legacy_purge_guard`, `p4_schema_budgets`, `r63_fork_observability`, `r10_catalog_resolution`,
2× `r33m_refusal_semantics`, 2× `rider20_fixes` (+2 `rider10` teardown errors), and the baseline log at
7388ab2 carries the same set minus the two order-dependent ones. Subset of the baseline failure set, no new
failure class, nothing touching the import graph. I do not dispute the flake verdict.

## Verdict

**PASS.** All four fixes from the aa1f846 FIX-FIRST landed and are independently reproduced: wildcard replaced
by six pair-exact exemptions that break on a seventh bind; the root-package contract flips both hub-spelling
probes to BROKEN; every corrected NOTE figure re-measures exactly (29 entry edges, 4 pairs / 5 routes, 11/45,
11/38, 58); and the liveness battery is real — 8 probes, temp-copy binding asserted, loud on a non-flip, loud
on an unguarded new module, skipping only when `lint-imports` is absent. Scope is clean (2 files, no source
modules). F1 above is a pre-existing sibling blind spot for `gate/` and `passes/`, recorded for the next slice.
