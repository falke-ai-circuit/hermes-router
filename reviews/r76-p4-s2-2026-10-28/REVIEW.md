# REVIEW — R76-P4-S2 import-guard slice (aa1f846)

Reviewer: reviewer (verification lane). Mode: read-only on the live tree; all mutation ran on
`/opt/data/tmp/s2rev` copies. Live tree `git status --short` = clean before and after.
Artefact produced 2026-10-28.

## Anchor

| Item | Value |
|---|---|
| Sha under test | `aa1f846` — "R76-P4-S2 (guard): five direct-edge contracts, measured exemptions, and a comment that tells the truth" |
| Scope by stat | `.importlinter` only, +81/−9 (matches the brief exactly; no source touched) |
| HEAD at review time | `7388ab2` (two later commits: `26c6f4e` source deletion, `7388ab2` a reviews-only commit) |
| `.importlinter` at HEAD vs aa1f846 | byte-identical (`git diff --stat aa1f846 HEAD -- .importlinter` = empty) — verdict applies to HEAD as well |
| Version | 6.13.23 (pinned in `tests/routing/test_r10_catalog_resolution.py:384`) |
| Tool | `import-linter 2.15` (`/opt/data/.local/bin/lint-imports`) |

RECIPIENT EXPERIENCE: I ran the guard myself (`PYTHONPATH=/opt/data/plugins lint-imports` in the repo
root) — 133 files / 843 dependencies analysed, `Contracts: 7 kept, 0 broken`, exit 0. Green confirmed
first, then everything below is about whether green means anything.

## Verdict: FIX-FIRST

The five contracts are real and every one of them can fail: all five flip to BROKEN under a genuine
upward direct import, and both declared exemptions are live edges (removing either one breaks its
contract). That part of the slice is load-bearing and I could not break it.

What blocks a PASS is that the guard's *stated* universal ("every other direct upward edge is
illegal") is measurably false in two ways, one of which is a config hole rather than a wording
problem, plus a cluster of NOTE measurements that do not reproduce. None of these is exploited by the
tree today — they are guard-coverage and record-accuracy defects, not live violations.

## Check 1 — the guard runs green (E1)

```
Analyzed 133 files, 843 dependencies.
core/ must not import other hermes_router modules (L0) KEPT (8 ignored imports)
core/ must not import hermes_cli (except the config_access adapter) KEPT (1 ignored import)
gate may not import api (direct) KEPT (6 ignored imports)
passes may not import api (direct) KEPT (1 ignored import)
features may not import api/gate/passes (direct) KEPT
lanes may not import api/gate/passes/features (direct) KEPT
core may not import api/gate/passes/features/lanes (direct) KEPT
Contracts: 7 kept, 0 broken.
```

7 contracts = 2 pre-existing S1 contracts + the 5 new S2 direct-edge contracts. The ignored-import
counts (6 / 1) prove both S2 ignore entries match live edges — import-linter raises `MissingImport`
for an unmatched ignore expression, so a dangling exemption would have crashed the run, not passed it.

## Check 2 — liveness mutation, one real violation per contract (E1/E2)

Method: full package copied to `/opt/data/tmp/s2rev/hermes_router` (leaf dir named after the root
package), `PYTHONPATH=/opt/data/tmp/s2rev`, verified bound with
`hermes_router.__file__ == /opt/data/tmp/s2rev/hermes_router/__init__.py` (otherwise the probe can
silently bind the live tree). Control run on the copy: `7 kept, 0 broken`. One genuine upward direct
import injected per contract, then `lint-imports` re-run.

| # | Contract | Injected import | Result | Printed edge |
|---|---|---|---|---|
| M1 | gate may not import api | `gate/orchestration.py` += `from hermes_router.api import routes` | **BROKEN**, exit 1, `6 kept, 1 broken` | `hermes_router.gate.orchestration -> hermes_router.api (l.2445)` |
| M2 | passes may not import api | `passes/dispatcher_post.py` += `from hermes_router.api import router_tools` | **BROKEN**, exit 1, `6 kept, 1 broken` | `hermes_router.passes.dispatcher_post -> hermes_router.api.router_tools` |
| M3 | features may not import api/gate/passes | `features/patterns/__init__.py` += `from hermes_router.gate import registration` | **BROKEN**, exit 1, `6 kept, 1 broken` | `hermes_router.features.patterns -> hermes_router.gate.registration (l.3)` |
| M4 | lanes may not import api/gate/passes/features | `lanes/registry.py` += `from hermes_router.passes import dispatcher_pre` | **BROKEN**, exit 1, `6 kept, 1 broken` | `hermes_router.lanes.registry -> hermes_router.passes.dispatcher_pre (l.75)` |
| M5 | core may not import api/gate/passes/features/lanes | `core/state.py` += `from hermes_router.lanes import registry` | **BROKEN**, exit 1, `6 kept, 1 broken` | `hermes_router.core.state -> hermes_router.lanes.registry (l.541)` |

5/5 flip. Each mutation produced exactly ONE broken contract (the other six stayed KEPT with their
ignored-import counts intact) — the contracts are individually load-bearing, not one entangled blob.
Raw output: `evidence/battery.json`, driver `evidence/mutate.py`.

Exemption liveness (deleting each ignore entry must break its contract — this is what distinguishes a
live exemption from decoration):

| # | Ignore entry removed | Result |
|---|---|---|
| X1 | `hermes_router.gate.registration -> hermes_router.api.*` | **BROKEN** — prints all six live edges at `gate/registration.py:32` (`api.commands`, `commands_config`, `commands_diag`, `commands_runtime`, `config_writer`, `router_tools`) |
| X2 | `hermes_router.passes.dispatcher_pre -> hermes_router.api.router_tools` | **BROKEN** — prints `hermes_router.passes.dispatcher_pre -> hermes_router.api.router_tools (l.38)` |

## Check 3 — is the declared set really the only direct upward edges? (E2)

Independent AST census over every module in the package (all `Import`/`ImportFrom` nodes, module-level
and function-local, relative imports resolved), ladder `api > gate > passes > features > lanes > core`:

```
total hermes_router-internal import statements: 557
upward LAYER edges: 2
  hermes_router.gate.registration:32 [local] gate -> api
      (from ..api import commands, commands_config, commands_diag, commands_runtime, config_writer, router_tools)
  hermes_router.passes.dispatcher_pre:38 [module] passes -> api
      (from ..api import router_tools)
```

Same census on the `03b0a36` tree: identical two edges. features / lanes / core directly upward: 0.
**The "exactly seven direct upward edges" claim is correct** (one statement binding six api modules at
`gate/registration.py:32` = 6 edges, plus one at `passes/dispatcher_pre.py:38` = 7 edges total).

The census also shows why the count is fragile, and this is Finding F2: 90 further import statements
from layered modules target the **root package** (`from .. import X`, `from ... import X`) and are
invisible to all five contracts, because the contracts name layer packages (`hermes_router.api`, …)
rather than the root package. `from .. import router_tools` is a direct import of `api.router_tools`
at runtime but is recorded by grimp against `hermes_router`. Proven live:

| Probe | Result |
|---|---|
| `features/banners/lifecycle.py` += `from ... import router_tools` | **KEPT** — features contract blind to it |
| `lanes/registry.py` += `from ... import router_tools` | **KEPT** — lanes contract blind to it |
| `passes/dispatcher_pre.py` += `from ..api import commands` | **BROKEN** — direct spelling is caught, hub spelling is not |

`core` is protected here only indirectly: `core-purity` forbids `hermes_router` itself
(`.importlinter:36`), with two root-package ignores at `.importlinter:113-114`.

## Check 4 — NOTE block audit against reality (E2)

Reproduced on `git archive` trees of `e846ae9` (layers baseline) and `03b0a36` (direct-edge baseline).

| NOTE claim (line) | Measured | Verdict |
|---|---|---|
| `type = layers` has no `allow_indirect_imports` field (l.7-8) | `grep allow_indirect importlinter/contracts/layers.py` → no match; import-linter 2.15 | **CONFIRMED** |
| layers on this tree = 11 illegal pairs / 45 routes (l.9) | 11 pair headers / 45 routes | **CONFIRMED exactly** |
| hub imports api.* at l.44-49 + gate.registration at l.54 (l.10-11) | `__init__.py` l.44-49 = the six `from .api import …`; l.54 = `from .gate import registration` | **CONFIRMED** |
| both true upward edges exempted → still 11 pairs / 38 routes (l.13-15) | 11 / 38 | **CONFIRMED exactly** |
| hub neutralised (58 hub-involved edges) → 7 illegal pairs / 19 module pairs (l.15-16) | 58 hub-involved edges ✓, but after removing them: **4 pairs / 5 routes / 6 distinct module pairs** | **NOT REPRODUCED** |
| 92 modules / 320 edges at 03b0a36 (l.21) | grimp: 92 modules, 320 direct edges | **CONFIRMED exactly** |
| 7 direct upward edges (l.22-23) | 7 (6 + 1), see Check 3 | **CONFIRMED exactly** |
| 29 source modules import the hub → 32 distinct entry edges (l.11-12) | 29 importer modules ✓ (matches), 29 hub-out edges — **not 32** | **PARTIAL** |
| 238 sites in 37 files (l.12) | AST: 329 root-targeting import statements in 42 files | **NOT REPRODUCED** |
| 31 of 43 route objects hub-mediated (l.13) | baseline is 45 routes, not 43; removing all 58 hub edges removes **40 of 45** routes | **NOT REPRODUCED** |
| without `allow_indirect_imports` 4 of the 5 fail on the untouched tree (l.19-20) | 4 BROKEN (gate, passes, features, core), lanes KEPT | **CONFIRMED exactly** |

Raw outputs: `evidence/layers_e846ae9.txt` (11/45), `evidence/both_exempt.txt` (11/38),
`evidence/hub_neutralised.txt`, `evidence/layers_variants.py`, `evidence/ast_census_HEAD.txt`,
`evidence/ast_census_03b0a36.txt`.

The operative conclusion the numbers support still holds and is now stronger than stated: a transitive
ladder is not reachable by config (still 4 broken pairs after all 58 hub edges are removed) and not
reachable by hub surgery alone. The decision to ship `forbidden` + `allow_indirect_imports` is
correct — but four of the twelve measured figures in the NOTE cannot be reproduced.

## Findings

**F1 — BLOCKING (config): the gate exemption is subtree-wide, so the gate contract has no teeth at
its own exemption site.** `.importlinter:148` exempts `hermes_router.gate.registration -> hermes_router.api.*`
— a wildcard over every present and future api submodule. Proof: add a new `api/newthing.py` and
`from ..api import newthing` in `gate/registration.py`; the guard prints
`gate may not import api (direct) KEPT (7 ignored imports)` — the seventh edge was absorbed silently.
The declared intent (`.importlinter:137`, "the composition root's 6 handler binds") is a fixed set of
six; the config enforces nothing after the sixth. Compare `passes`, which is pair-exact
(`.importlinter:157`) and does break on a new edge at the same module (Check 3, third row).

**F2 — BLOCKING (coverage + claim): upward imports spelled through the hub are invisible to every
contract, so `.importlinter:139` "Every other direct upward edge is illegal" is false.** features and
lanes can each import `api.router_tools` via `from ... import router_tools` and both contracts stay
KEPT (Check 3). Not exploited by the tree today (the 7 live feature root-imports are all facility
modules — `debug_banner`, `render_inbox`, `dispatcher_knobs`, `advisory_consumption` — none in a
layer), so this is a latent hole rather than a live violation.

**F3 — BLOCKING (record accuracy): four NOTE measurements do not reproduce** — `31 of 43 route
objects` (45 routes, 40 hub-mediated), `7 illegal pairs / 19 module pairs` (4 / 5 / 6), `32 distinct
entry edges` (29), `238 sites in 37 files` (329 / 42). The NOTE tells the next unit "re-measure, do
not re-derive"; re-measuring contradicts it, which is the one thing a provenance comment cannot do.
See Check 4 for the exact rows.

**F4 — non-blocking:** M1's injected `from hermes_router.api import routes` (`api.routes` is not a
real submodule) is still caught, because grimp records the unresolved name against `hermes_router.api`
itself and the wildcard does not match the package root. Worth knowing: the wildcard's blind spot is
bounded to real submodules — which is exactly the expanding set F1 is about.

## Numbered fix list

1. `.importlinter:147-148` — replace the wildcard with the six explicit pairs:
   `hermes_router.gate.registration -> hermes_router.api.commands` (and `commands_config`,
   `commands_diag`, `commands_runtime`, `config_writer`, `router_tools`). Re-run X1 to confirm the
   contract still reports 6 ignored imports and still passes.
2. Add a sixth contract forbidding `hermes_router` (the root package) from `hermes_router.features`
   and `hermes_router.lanes`, with `ignore_imports` naming the known facility modules
   (`debug_banner`, `render_inbox`, `dispatcher_knobs`, `advisory_consumption`, …) — the same shape
   `core-purity` already uses for core (`.importlinter:36`, `:113-114`). Then re-run Check 3's two
   hub-spelling probes: they must go BROKEN.
3. `.importlinter:12-13` and `:15-16` — correct or delete the four figures in F3; keep the ones that
   reproduce (11/45, 11/38, 92/320, 7 direct edges, 58 hub edges, 4-of-5) since they carry the
   argument on their own.
4. Optional, cheap: pin the liveness battery (one injected edge per contract, expect exit 1 and the
   contract name BROKEN) as a test under `tests/`, so the guard's teeth are re-proven by CI instead of
   by the next reviewer.

## What this review did NOT do

No source edits, no commit, no branch. The live tree was never mutated; all five liveness mutations,
both exemption-removal probes and the two hub-escape probes ran on `/opt/data/tmp/s2rev` copies
(`hermes_router.__file__` asserted per copy). This artifact is written, not committed, per the brief.
