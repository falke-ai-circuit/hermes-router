# Architecture

hermes-router sits **inside** the Hermes Agent turn pipeline and decides,
per turn, whether the main model's ordinary path is augmented by the
registered lanes. It never rewrites the user's message; it envelopes,
substitutes renders, or consults — and always fails open.

## Layer diagram (L0 → L4, one-way, enforced)

Generated on `1cbb739` (R23 leg 1 truth pass; hand-maintained until the
gen script lands). Drift guard: `tests/test_architecture_doc_contract.py`.

```
L0  core/          stdlib only (config_access touches hermes_cli.config)
L1  features/      imports L0 only
L2  passes/        imports L0 + L1
L3  gate/          imports L0..L2
L4  api/           imports anything; nothing imports L4
lanes/             pure data, beside L0, depends on nothing
```

Dependency direction L0 ← L1 ← L2 ← L3 ← L4 ← `__init__` is enforced by the
import-linter layer check in `scripts/ci.sh` (and an AST-walking audit test).
The pre-restructure hub-and-spoke cycle (`__init__` importing everything,
leaves late-binding back through `_plugin()`) is gone: telemetry lives in
L0 and every pass imports it directly.

## Module table per layer

| Layer | Module | Role |
|---|---|---|
| L0 core/ | `config_access.py` | live config reads; the ONLY load_config consumer |
| | `schema.py` | typed config key inventory (SCHEMA — single source of truth) |
| | `budgets.py` | budget profiles + central max_tokens clamp |
| | `telemetry.py` | log_route, isolate(gate, fn), seam_probe |
| | `patchpoints.py` | the one legal monkeypatch surface |
| | `state.py` | pending-render map, counters, sidecars |
| | `canonical.py` | canonical row formatting (artifacts only at edges) |
| | `session_store.py` | message history access |
| | `usage_ledger.py`, `routing_caps.py`, `decisions.py` | spend ledger, caps, decision records |
| L1 features/ | `frames.py` | frame text (byte-identical) |
| | `banners/lifecycle.py` | BannerLifecycle + deliver() chokepoint |
| | `banners/kinds.py` | registered BannerKind data rows |
| | `patterns/engine.py` | pack compiler + matcher |
| | `patterns/packs/*.json` | pattern packs as data |
| | `classifier.py`, `decision*.py`, `semantic_classifier.py`, `intent_classifier.py` | scans and lane brains |
| | `complexity.py`, `flinch_reason.py`, `refusal_doctrine.py`, `risk.py`, `reflex.py`, `bypass_watch.py`, `suggestions.py` | verdict / doctrine surfaces |
| | `persona_card.py`, `method_card.py`, `render_inbox.py`, `render_payload.py`, `provenance_footer.py`, `provider_prices.py` | persona, render, provenance, pricing |
| | `trigger_cascade.py`, `completion_audit.py` (feature legs) | cascade + audit legs |
| L2 passes/ | `dispatcher_pre.py` / `dispatcher_post.py` / `dispatcher_knobs.py` | PRE / POST middleware bodies + knob/config readers (R23; root `dispatcher_*.py` are re-export shims until importers migrate) |
| L3 gate/ | `route_gate.py` | lane routing via registry reads |
| | `router_core.py` | turn orchestration core |
| | `orchestration.py` | the three on_* orchestrator bodies |
| L4 api/ | `router_tools.py`, `commands*`, `config_writer.py` | agent tool, `/router` command surface, atomic config writer |
| data | `lanes/registry.py` | LaneSpec dataclass + register_lane() |
| | `lanes/builtins.py` | the four lane definitions as data |

## Pass pipeline walkthrough (exact seams)

```
user turn
  │
  ├─ SEAM 1: on_llm_request (PRE, before the model runs)      [L2/L3]
  │    1 higher-self rule injection
  │    2 sentinel firewall (routed turns never re-trigger)
  │    3 audit-delivery (stashed POST verdict from last turn)
  │    4 complexity scan → orientation consult (frontier, advisory)
  │    5 uncensored scan → contested content → render staged
  │    6 clarify scan, banners, footers
  ▼
main model runs
  │
  ├─ SEAM 2: on_transform_llm_output (POST, after tools)      [L2/L3]
  │    refusal shape → censorship-flinch verdict → uncensored
  │    render (retry ladder, fail-open deliver)
  │    completion audit gate → sync frontier consult (45s)
  │    → revision pass → deliver
  │    parked banner consumption
  ▼
  ├─ SEAM 3: on_transform_terminal_output                     [L2]
  ▼
final response to user
```

Seam wiring happens in `__init__.py` (plugin build + register 3 seams +
register lanes + export PUBLIC_API). The L0-L3 engine never knows about the
seams; `core/patchpoints.py` is the one legal monkeypatch surface for tests
and the seam probe (`core/telemetry.seam_probe`, P6).

## LaneSpec / BannerKind story

Adding a lane used to touch ~15 files because lane behavior was encoded as
bespoke code per file. Now the behavioral surface is **data**:

- `LaneSpec` (lanes/registry.py) fields map 1:1 to the old touch list:
  `phrases` (route_gate phrase dicts), `pre_patterns` (dispatcher_knobs),
  `marker_strings` (sentinel firewall registry), `banner_kind`
  (debug_banner park semantics), `budget_profile` (audit budgets),
  `config_section`, `commands_switch` (/router map), `provenance_tag`,
  `delivery_edges`, `consult_role`, `valid`. Registration is import-time
  fail-loud (duplicate id = ValueError). The four builtins live in
  `lanes/builtins.py`, transplanted byte-identically from the original
  tables; a parity test holds registry data == original literals.
- `BannerKind` (features/banners/kinds.py) carries the banner semantics that
  were imperative rules inside debug_banner's park/consume path:
  `stack_policy` (replace/stack/once), `ttl_seconds`, `delivery_edges`
  (the legal consume edges), `capture_fallback` (R20 one-shot watcher).
  The lifecycle consults this data instead of hardcoding.

Phrase tables themselves are pack DATA (`features/patterns/packs/
lane-phrases.json`, P7) — `lanes/` imports nothing; consumers unchanged.

## Load-bearing invariants

1. **Fail-open everywhere.** Every consult is budget-bounded; on timeout the
   unaudited/unrouted response delivers as-is.
2. **Routed turns never re-trigger routing.** Sentinel firewall recognizes
   every lane's markers; marked content passes untouched.
3. **Canonical rows never carry lane artifacts.** Banners/envelopes/markers
   append at the delivery edge only.
4. **Every billed frontier call emits a banner.** Spend visibility is
   unconditional.
5. **One consult per turn.** PRE and POST mutually exclusive, keyed on task ID.

## State and logs

- Route decisions: `/tmp/uncensored-router-<profile>.log` (event lines)
- Spend ledger: `<profile>/hermes-router-spend.json`
- Backoff ledger: `<profile>/hermes-router-backoff.json` (anchor failures)
- Config: `hermes_router:` block in the profile `config.yaml`, read live
  per dispatch — knob changes never require a gateway restart; plugin
  `.py` changes do.

Note: this file is static hand-maintained today; a CI-generated variant
(AST-walk → layer table) is sketched in `scripts/gen_architecture.py` scope
and can replace it later without changing consumers.
