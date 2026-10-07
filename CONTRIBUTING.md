# Contributing to hermes-router

## Environment setup

- Python 3.11+ (stdlib-first: the L0 core layer depends on nothing but
  stdlib; the plugin must stay importable under the gateway's
  `hermes_plugins.*` alias).
- `pip install pytest ruff` (+ `import-linter` for the layer check).
- Dev copy: clone the repo (canonical = the plugin's own git repo); tests
  run from the repo root with `pytest tests -q`.

## Test doctrine

- **Two gates:** the mock suite (`pytest tests -q`, hermetic, fully mocked —
  no test may make a real provider call, no keys in env) and the behavioral
  battery (runs orchestrator-side against a live canary profile; see
  `benchmarks/METHODOLOGY.md` for the oracle pitfalls before trusting a
  result).
- **Cheap models in test legs:** any test leg that exercises a consult path
  must stub the model or use a cheap fixture-tier endpoint. Nothing in the
  suite bills a frontier/render provider.
- Live smoke is exactly ONE cheap call (`test_router_live.py -m live`,
  max_tokens 16, key-gated skip) — never part of the default run.
- Suite must be 100% green before any commit; pre-existing reds are not a
  thing.

## Add-a-lane recipe

1. Define the `LaneSpec` data row in `lanes/builtins.py` (see
   `docs/lanes.md` for the field table): `id`, `phrases` (or a new pack
   entry in `features/patterns/packs/lane-phrases.json`), `pre_patterns`
   if the lane fires mechanically, `marker_strings` (REQUIRED — sentinel
   firewall; routed turns never re-trigger routing), plus any of
   `banner_kind` / `budget_profile` / `config_section` /
   `commands_switch` / `provenance_tag` / `delivery_edges` /
   `consult_role` the lane needs.
2. Register it (import-time fail-loud; keep VALID_ROUTE_LANES order).
3. Fixtures: add phrase/variant rows to the lane-phrases pack.
4. Witness: every lane output must be recognizable by its
   `marker_strings` and stay out of canonical rows (artifacts only at
   the delivery edge).
5. Tests: a parity test if transplanted from an original table
   (byte-identical literals), plus behavioral tests in
   `tests/<functional-group>/` (see `docs/testing-history.md` for the
   grouping).

## Add-a-pattern-row recipe

1. Edit the pack data (`features/patterns/packs/*.json`) — patterns are
   data, not code (P7 engine). Fixture corpus rows go in
   `features/patterns/corpus/*.json`.
2. Keep literal parity with any consumer that still asserts the original
   table (the registry parity tests).
3. Add/extend a test in `tests/classification/` proving the row fires on
   its family and stays inert on meta-discussion (audit mentions, quotes).

## Patchpoint rules

- `core/patchpoints.py` is the ONE legal monkeypatch surface. Tests and the
  seam probe go through it; ad-hoc monkeypatching elsewhere in the package
  is forbidden. Tests use `core.telemetry.isolate(gate, fn)` boundaries.
- Never reintroduce a deferred `from hermes_router import ...` late-binding
  in engine code — import `core.telemetry` directly (the cycle is dead).

## Swallow-audit contract

`scripts/swallow_audit.py` compares current-source swallow sites against
`tests/swallow_baseline.json` (recorded at P6). The audit FAILS when any
non-registered swallow appears or a baseline row disappears without a
recorded justification. Any new `except: pass`-shaped handler must either
telemetry-log or be added to the baseline with a reason. CI runs it in
regression-only mode (no baseline updates).

## CI gates (`scripts/ci.sh`)

ruff → pytest (mock suite, hermetic) → swallow_audit (regression-only) →
import-linter layer check → docs-drift check (the module/layer tables in
docs/architecture.md must not reference missing files) → battery pointer
(the behavioral battery runs orchestrator-side, not in CI).

## PR checklist

- [ ] Suite green (`scripts/ci.sh` or `pytest tests -q`)
- [ ] CHANGELOG.md entry for the next version
- [ ] New knobs documented in `docs/operator-guide.md` + present in
      `core/schema.py` SCHEMA
- [ ] New lane outputs registered in the sentinel firewall
- [ ] No new top-level imports of `hermes_router` inside gateway code paths
      (use relative imports)
