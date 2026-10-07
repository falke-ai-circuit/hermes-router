# hermes-router

A Hermes Agent plugin that adds four optional, fail-open lanes to every turn:
an uncensored render lane (shadow), a frontier advisory lane (higher self,
PRE + POST), and a decision lane (R19, dark by default) — plus a `/router`
command surface and validated `router_status`/`router_control` agent tools.
It never rewrites the user's message; it envelopes, substitutes renders, or
consults — and **always fails open**: every failure degrades to normal agent
pass-through. The plugin never crashes a turn.

Reference docs: `docs/architecture.md` (layers, seams, LaneSpec/BannerKind)
· `docs/lanes.md` (per-lane reference) · `docs/operator-guide.md` ·
`docs/known-gaps-2026-09-06.md` · `docs/testing-history.md` ·
config keys: `core/schema.py` (single source of truth).

## Quickstart

1. Install the plugin: `hermes plugins install` or a directory copy into the
   profile's plugins path — see INSTALL.md. Zero config required to install.
2. Restart that profile's gateway; the plugin registers three seams
   (`on_llm_request`, `on_transform_llm_output`, `on_transform_terminal_output`)
   and starts routing with built-in defaults.
3. First route in 60 seconds — send a standalone directive line in a session:

   ```
   ask shadow self to give her read on <topic>
   ```

   The shadow lane fires, stages a substance-frame render through the ordered
   chain, and appends a spend banner at the delivery edge. Watch decisions in
   `/tmp/uncensored-router-<profile>.log`, or set `debug_banner: true` for
   inline verbosity-levelled banners.

Config is read live per dispatch: knob changes never require a gateway
restart (only plugin `.py` changes do). Out-of-box contract: insert your
frontier and uncensored-chain API config → both lanes work; knobs only if
you want non-defaults.

## Worked examples

### 1. Shadow render (uncensored lane)

`ask shadow self to render: <contested ask>` — PRE matches the declared
phrase variant (strict prefix/standalone matching, no prose mind-reading),
stages a render through the ordered `chain:` (primary → fallback, first
success wins), swaps refusal-shaped POST output in the agent's own
persona-derived voice, and parks a spend banner consumed at the delivery
edge. Chain failure → the unaudited response delivers as-is. Content
boundaries live in the render substrate, not routing code.

### 2. Frontier consult (higher-self lane)

Send a complex planning ask (or `anchor this` on a line). The PRE complexity
scan classifies L0-L3 (2-stage: regex + optional aux model on the gray zone),
performs ONE per-call anchored consult to the configured frontier anchor, and
enters the answer as a provenance-stamped advisory envelope
(`{kind, producer, route_id, task_id, answer, evidence_refs, limitations}`).
Frontier proposes; the main model disposes. Model override typed BY THE USER
names the consult model (R7); agent-initiated consults always use
`anchor_chain.primary`. Every billed call emits a banner.

### 3. Higher-self POST audit

After a completed substantive turn (every `post_audit_min_turns`, or ≥3 tool
calls), the POST completion-audit gate runs a sync frontier audit inside a
45s budget, may trigger a full-frontier revision pass (60s budget), and
delivers the parked banner. One consult per turn, ever — PRE and POST are
mutually exclusive. Sync timeout downgrades to async: the unaudited turn
ALWAYS delivers, the verdict lands next turn.

## Configuration tour

All keys are typed, defaulted, and validated in `core/schema.py` (`SCHEMA`);
`config_access.get()` reads against it live per dispatch. Highlights:

| Key (dotted) | Type / default | What it controls |
|---|---|---|
| `enabled` | bool / true | master switch |
| `refusal_doctrine` | str / always_route | verdict policy (always_route / doctrine / off) |
| `dry_run` | bool / false | classify + log, never act |
| `log_path` / `log_routes` / `log_max_bytes` | str, bool, int | route-event log (0600, content-free) |
| `debug_banner` | bool / false | spend/decision banners on delivered output |
| `chain` | list / [] | uncensored render chain (primary → fallback endpoint dicts) |
| `routing_daily_cap_usd` | float / 0.0 | daily routing spend cap |
| `render_max_chars` | int / 0 (off) | render body clamp |
| `flinch_reason_gate` | bool / true | censorship-vs-technical verdict gate |
| `classification.*` | dict | pre_patterns, semantic_gate, two_vote_confirm/groups, aux_classify |
| `complexity.*` / `frontier.*` | dict | pov_mode, stage1, consult_cooldown_turns, aux_consult_min_interval_sec, ttl/base/max |
| `anchor_chain.*` | dict | endpoint/model/key, reasoning_effort, threshold, caps |
| `decision.*` / `decision_head.*` | dict | decision-lane mode/model/miner, backend (heuristic default, optional RouteLLM mf) |
| `risk.*` / `reflex.*` | dict | risk consult chain, reflex pov_mode |
| `budgets.*` | dict | per-consult-type max_tokens overlays (consult/verdict/completion_audit/render/probe) |

Exact type + default for every key: `core/schema.py`. Legacy
`uncensored_router:` config sections keep working (canonical
`hermes_router:` wins; `router_control` dual-writes).

## Lane table

Registered lane data lives in `lanes/builtins.py` (phrase tables from
`features/patterns/packs/lane-phrases.json`); full per-lane reference:
`docs/lanes.md`.

| Lane id | Fires on | Sentinel markers | /router switch |
|---|---|---|---|
| `shadow` | declared uncensored-render variants | "Your uncensored response", "UNCENSORED-ROUTER INJECTION", "recorded turn" | `uncensored` → enabled |
| `higher-pre` | declared higher-self phrases | "HIGHER-SELF ORIENTATION TURN" | `frontier` → complexity.enabled |
| `higher-post` | (POST audit hook) | "HIGHER-SELF COMPLETION-AUDIT TURN" | `frontier` → complexity.enabled |
| `decision` | declared midturn decision target | — | — |

## Troubleshooting

- Turn on `debug_banner` (or run `/router diag`) for per-stage spend/decision
  banners inline; `router_status` shows lanes, masked anchor chain, today's
  counts, spend vs cap.
- No route fired? Check `log_path` event lines (`route_fired`, `route_skipped`,
  `cap_blocked`, `two_vote_*`, `model_override_*` — content-free).
- Spend: `<profile>/hermes-router-spend.json`; anchor backoff:
  `<profile>/hermes-router-backoff.json`.
- Protected-group confirm gate (two-vote aux semantic confirm) is fail-CLOSED
  by design: aux outage → no render. That is deliberate.
- Known open gaps: `docs/known-gaps-2026-09-06.md`.
- Sentinel echo regressions: report with the exact turn text.

## Testing

Mock suite: 1500+ hermetic tests (`pytest tests -q`; the live marker is
deselected). Live smoke: exactly ONE cheap call
(`pytest tests/routing/test_router_live.py -m live`, skipped when
`OPENROUTER_API_KEY` is absent). Development: `CONTRIBUTING.md`; CI gates:
`scripts/ci.sh` (the behavioral battery runs orchestrator-side).

## License

Internal — falke-ai-circuit. RouteLLM decision-head code is vendored under
Apache-2.0 (copyright lm-sys contributors); see `decision_head.py` header.
