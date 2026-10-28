# R76-P4-S4 — deletion review: the dead fork-era body of reflex_weights

**Commit reviewed:** `26c6f4e9e2d9078825eda382eaf68a41ba638060` (`26c6f4e`)
**Parent:** `aa1f8461e1fc7971974f4ef25e4d683a74779767` (`aa1f846`)
**Repo:** `/opt/data/plugins/hermes_router` (tree clean at review time)
**Reviewer:** reviewer profile (fulfilment verification)
**Date:** 2026-10-28

## Verdict

**PASS**

The deletion is inert on every surface the recipient touches: the block bytes, the request
bytes, the receipt line and all seven telemetry rows are byte-identical at parent and head.
The seven deleted symbols have zero references outside their own definition on both sides
of the commit. The two retained entry points (`_note`, `cfg_for`) behave identically.

## Claim under test

> "Nothing observable changed." (deletion of a dead body — `reflex_weights.py` −199,
> `reflex_lane.py` −1; 200 deletions, no file removed)

Method: driven, not read. Two real git worktrees were checked out
(`pkgP/hermes_router` @ aa1f846, `pkgH/hermes_router` @ 26c6f4e), each imported as the
`hermes_router` package, driven through the same script (`probes/drive.py`) with the same
stubbed model response, and the emitted bytes hashed.

## Findings

| # | Claim | Evidence | Class | Result |
|---|-------|----------|-------|--------|
| 1 | Emitted bytes unchanged — banner block | `fire()` produced a 325-char block; sha256 `7c408150f312…a5a74` identical both trees | E1 | PASS |
| 2 | Emitted bytes unchanged — request payload | `inject_pass()` request JSON sha256 `0abcbae71a6a…091cf` identical; `run_on_request()` request sha256 `1e619f558366…7a5eac` identical | E1 | PASS |
| 3 | Emitted bytes unchanged — receipt + ledger rows | All 7 captured telemetry rows identical, incl. `reflex_receipt_parked` (`row=reflex.2`, `rows=2-2`, `verdict=no_lead`, `est_cost=0.0`, `price_key=jev-latest`), `reflex_weights_ready` (`weights=0.80/0.12/0.05`, `margin=0.68`), `decision_rule_injected` (`chars=1517`), `reflex_weights_delivered` (`chars=325`) | E1 | PASS |
| 4 | Retained API works identically | `cfg_for` over a 6-config matrix: identical dicts both trees; `cfg_for_has_min_gap` false both sides (the key was never a `cfg_for` output). `_note(reason=…)` → `LAST == {"reason": "probe", "top": 0.8}` both trees | E1/E2 | PASS |
| 5 | No import breakage | `python -c "import hermes_router.reflex_weights"` OK both trees; module exposes exactly `_note`, `cfg_for` at head | E1 | PASS |
| 6 | Import-linter contracts intact | `lint-imports` → `Contracts: 7 kept, 0 broken.` (133 files, 843 dependencies; all 7 S2 contracts KEPT) | E3 | PASS |
| 7 | Baseline gate | `python3 scripts/baseline_check.py` exit 0: `baseline_check: OK — failure set equals the pinned baseline` (pinned required=9 optional=1, actual 9) | E3 | PASS |
| 8 | Scope | `git show --name-status`: `M reflex_weights.py`, `M reflex_lane.py` only. No test deletions, no `plugin.yaml`, no file removal. `plugin.yaml` byte-unchanged (needed no version bump) | E1 | PASS |

## Runner summary line (verbatim)

```
7 failed, 2528 passed, 2 skipped, 1 deselected, 1 xfailed, 2 warnings, 2 errors in 313.97s (0:05:13)
```

m1 `tests/decision_lane/test_r53_midturn_mindset.py::test_m1_envelope_carries_the_current_stance`
(the sole `optional` pin) was **GREEN** on this run, not red — the runner printed
`optional member green this run`, and the 9 actual reds are exactly the 9 required pins.

## Reachability census — both sides

AST-based (`probes/census.py`: counts `from-import`, package-attribute access on any
reflex_weights alias, and bare-name use inside the module itself), run over the whole
worktree excluding `__pycache__`/`.git`.

| Symbol | Refs outside its definition @ aa1f846 | @ 26c6f4e |
|--------|--------------------------------------|-----------|
| `parse` | 0 (1 bare-name use, inside module, line 291) | 0 (definition gone) |
| `render` | 0 | 0 |
| `build_request` | 0 (1 bare-name use inside module line 291) | 0 |
| `extract` | 0 | 0 |
| `summarise` | 0 | 0 |
| `_as_prob` | 0 (2 internal uses, lines 158/178) | 0 |
| `_clip` | 0 (4 internal uses, lines 192/199/240/245) | 0 |
| `min_gap` (setdefault) | 1 — `reflex_lane.py:120`, the setdefault itself | 0 |

Dynamic forms checked and empty on both trees: `from .reflex_weights import X`,
`import reflex_weights as`, `getattr(module, "name")`, `importlib`, `globals()`,
`__all__` entries (module defines none), string-keyed dispatch tables (no such table
references any of the eight names). The only `getattr` hits in the module are
`getattr(node, ...)`-style AST/attribute helpers elsewhere, none naming a deleted symbol.

### Per-profile copies (`/opt/data/profiles/*/plugins/hermes_router/`)

12 profile dirs, **11** carry `reflex_weights.py` + `reflex_lane.py` (recovery carries
neither module — nothing there to break). All 11 copies:

* `reflex_weights.py` sha256 `c1621fa75c30…799c` — **byte-identical to parent `aa1f846`**,
  i.e. they still contain all seven defs and the `min_gap` setdefault;
* `reflex_lane.py` sha256 `eff90d23ff93…fc8cb3` — identical across all 11, and **behind the
  repo**: it differs from the parent by the R76-P1-U2 `_set_dispatched_model` /
  `_dispatched_model` block (lines 124-147). It calls `_rw` only via `_rw._note` and
  `_rw.cfg_for`.

Every copy is internally self-consistent, so no copy can call a name its own
`reflex_weights.py` no longer defines. The copies are stale copies of a *pre-deletion*
tree, which is the safe direction for this change: they simply do not have the cleanup yet.

## `reflex_lane.py` single-line deletion — judged

```diff
 def _cfg(cfg):
     c = _rw.cfg_for(cfg)
     c.setdefault("every_call", True)
-    c.setdefault("min_gap", 0.0)
     return c
```

It removed a key that was written into the lane's local config dict and read by nothing:
post-deletion `grep -rn "\bmin_gap\b" --include=*.py` over the tree returns 0 hits, and the
only pre-deletion hit was this line. Observed effect: `_cfg(...)` dicts differ by exactly
that one key (`lane_cfg_has_min_gap` true→false), and nothing downstream reads it — the
driven lane output, receipt row and request bytes are unchanged. Provably inert: the key
never reached a consumer, so its absence cannot change a branch, a price, or a string.
It is a visibility improvement, not a behaviour change (the knob previously *looked*
configurable; `docs/r76-p4-refactor-plan.md` §3b says the same).

## Nemesis gate — hostile attacks, all addressed

1. **String-keyed / dynamic reach.** A deletion in a dynamic language fails through
   `getattr(mod, "render")` or a dispatch table, not through imports. Searched both trees
   for `getattr(`, `importlib`, `globals()`, `__all__`, and any string literal matching the
   eight names inside executable code: no hit outside doc prose. *Addressed — no dynamic
   reach exists.*
2. **A stale per-profile copy calling a deleted name.** 11 copies audited by sha and by
   `_rw.` attribute census: all carry the *full* parent module, all lanes call only
   `_note`/`cfg_for`. *Addressed — breakage is impossible in the copies as they stand.*
3. **A silent behaviour change hidden in the diff.** Worked around by driving instead of
   reading: identical sha256 on the decided block, the post-inject request JSON, the
   every-call request JSON, and all 7 telemetry rows. *Addressed.*
4. **A test or fixture that asserted on the deleted API.** Suite ran: 9 reds, exactly the 9
   required pins, no new node id appeared; the green/red partition is unchanged.
   *Addressed.*

Residual, non-blocking: the per-profile copies remain at a pre-deletion tree (also missing
R76-P1-U2). That is a deployment-lag observation, not a defect of this commit — the copies
are consistent and will pick the deletion up on their next deploy. No action required for
S4.

## What was driven, what was compared

Driven (E1, in-process, both trees, stubbed decision backend so no network):

* `reflex_weights.cfg_for` over 6 config shapes;
* `reflex_weights._note` → `LAST`;
* `reflex_lane._cfg` over the same 6 shapes;
* `reflex_lane.fire("probe-s1", "terminal", <traceback body>, ON, ask=…)` → parked block;
* `reflex_lane.inject_pass(req, "probe-s1")` twice (second must be False) → request JSON;
* `reflex_lane.run_on_request(<4-message request>, "probe-s2", ON)` → request JSON;
* telemetry seam (`core.telemetry.log_route`) captured rather than written, so the ledger
  rows the lane hands over are compared verbatim.

Compared: sha256 of the block, of both request JSONs, and deep equality of all 7 telemetry
rows; `hasattr` census of the nine names; `cfg_for`/`_note` outputs.

Not driven: the live `jev-router` HTTP path (no key, and the deletion is upstream of it —
`_transport` was stubbed identically on both sides, so it cannot mask a change in the
deleted code). The full suite was run at head only; the failure-set equality to the pin
plus the zero-reference census covers the parent side.

## Artifacts

* `probes/drive.py`, `probes/drive_parent.json`, `probes/drive_head.json`
* `probes/census.py`, `probes/census_parent.json`, `probes/census_head.json`
* `logs/junit_head.xml` (full-suite run), `logs/baseline_check-head.log`,
  `logs/git-show-stat.txt`
