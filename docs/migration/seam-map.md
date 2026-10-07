# hermes-router Seam Map (P0 artifact)

Repo HEAD at P0 authoring: `590bda3` (v4.23.0, FIX-FIRST rider 22). Branch: `restructure/A`.

This is the seam inventory every later phase (P0.5..P4 in the current dispatch block; P1..P9 in
proposal §3) is audited against. Each later phase record states which seams it touches and proves
untouched seams untouched (grep + no-diff assertion for their anchor files). Source evidence:
`/opt/data/tmp/router_restructure_evidence.md` (researcher, live-verified @ 590bda3),
proposal `/opt/data/tmp/router_restructure_proposal.md` §P0.

---

## S-1 — Dual-block config order (behavior)

Anchor: `config_access.py` lines 15/30/103 (verified live @ 590bda3).

- `_SECTION_KEYS = ("hermes_router", "uncensored_router")` (line 30) — canonical section FIRST,
  legacy section ONLY when canonical absent. This ORDER IS BEHAVIOR.
- Line 84 region: merged multi-block semantics — a profile whose `hermes_router` block is non-empty
  (e.g. only `decision`) plus a legacy `uncensored_router` block merges via `_merge_legacy`
  (lines 71, 132): legacy keys fill gaps, canonical keys WIN on conflict. This merge (not either-or)
  was the 2026-09-09 fix for the silent-drop of orphan keys.
- Line 103: `section()` docstring spells both section names.
- 3-tier resolution (load_config → co-located `<plugin_root>/../config.yaml` → last-good mtime-keyed
  cache) and its cache semantics are behavior; preserved verbatim through moves.

### PURGE STATE UPDATE (2026-10-07, supersedes the "fallback moves as-is forever" note in proposal §P0)

The legacy `uncensored_router` sections have been DELETED from ALL profile configs
(per-profile `grep -c uncensored_router` = 0 across analyst, architect, coder, conductor, evol,
operative, orchestrator, researcher, reviewer, shadow, valmet; recovery is remote — its copy is
covered by S-3's deploy-wave rule and re-verified at deploy). Canonical `hermes_router` sections are
merged + parity-proven; pre-purge backups exist at:

- `/opt/data/tmp/backup_coder_config_pre_purge.yaml`
- `/opt/data/tmp/backup_conductor_config_pre_purge.yaml`

Consequence: `_merge_legacy` in `config_access.py` is now DEAD CODE fleet-wide (no caller input can
reach a legacy block). Phase P0.5 of the current dispatch block deletes `_merge_legacy` and the
legacy branch while keeping the 3-tier resolution + cache semantics untouched. The remaining
`uncensored_router` string usage in the package after P0.5: docstring/historical references and the
guard test itself; S-1's surviving invariant is the CANONICAL-first read order, which no phase may
reorder.

## S-2 — evol files-only / no-bounce constraint

The evol profile's plugin copy is updated by FILE COPY ONLY; no gateway bounce on evol (its gateway
serves the mutation lane's substrate tooling). Recorded as a deploy constraint: deploys to the evol
slot are copy-only and sequenced at clean boundaries like every other profile. NO phase deploys —
deploy/bounce is the orchestrator's at block gates (dispatch binding 3).

## S-3 — recovery remote profile

The recovery profile (VPS-side remote Hermes) carries its own plugin copy; it is updated by copy in
the SAME deploy wave as the 12 local slots. It is Goran's recovery path — a restructure that leaves
recovery on stale code is a failed rollback story. Noted so no phase "forgets" it as the
non-obvious 13th tree (12 local + 1 remote).

## S-4 — 12 per-profile physical plugin copies, no symlinks

Verified live: analyst, architect, coder, conductor, evol, operative, orchestrator, recovery(local
slot), researcher, reviewer, shadow, valmet — 12 independent trees, physical copies, zero symlinks.
Deploys are copies; there is no shared mutable tree to lean on. Any phase touching package layout
must ship with a copy/deploy-script check that all slots receive the IDENTICAL tree (hash-check
across slots). Orchestrator-run at block gates, never mid-phase.

## S-5 — plugin.yaml hook surface

Hooks registered in plugin.yaml: `llm_request` (middleware → `on_llm_request`, `__init__.py:346`),
`llm_execution` (middleware → `on_llm_execution`, `:1583`), `transform_llm_output`
(hook → `on_transform_llm_output`, `:853`), `transform_tool_result` (hook → decision_midturn, R19.2).
`middleware_schema`, plugin identity/timestamps are FROZEN — no phase edits plugin.yaml's hook
registration mechanics; a layout change inside the package must not change how the host binds hooks.
No plugin.yaml change in any phase.

## S-6 — Refusal-vocab 5-file duplication list (verified live @ 590bda3)

The refusal vocabulary/doctrine surface lives across exactly five files and must move TOGETHER
(same phase) so the duplication cannot be half-moved:

| File | LOC | Refusal-relevant anchors |
|---|---|---|
| `classifier.py` | 558 | scan_pre/scan_post pattern tables incl. refusal-shaped vocab |
| `refusal_doctrine.py` | 224 | DNA-grounded doctrine, `verdict_for_refusal` verdict prompt text |
| `flinch_reason.py` | 79 | flinch reason strings / gate |
| `persona_card.py` | 410 | card build sites w/ refusal-relevant context |
| `benchmarks/behavioral_battery.py` | 259 | battery refusal cases |

Later phases prove the vocab intact post-move by asserting these anchors are byte-identical
(no-diff on the anchor regions).

---

## Per-phase seam accounting (appended as phases land)

| Phase | Seams touched | Seams proven untouched |
|---|---|---|
| P0 | — (docs-only) | all |
| P0.5 | S-1 (legacy branch deleted; canonical-first order + 3-tier + cache semantics unchanged; guard test added) | S-2..S-6 |
