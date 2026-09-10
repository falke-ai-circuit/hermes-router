# hermes-router

Generic **two-lane Hermes router** plugin (any profile, any agent). Lane 1 is the proven
uncensored render lane (v2 behavior, byte-identical mechanics). Lane 2 is the v3
complexity/struggle lane: when a task needs stronger reasoning — or the main model is
visibly struggling — ONE per-call anchored call goes to a configured frontier model, and
its answer enters the main model's context as a provenance-stamped advisory envelope.
Fail-open everywhere: every failure degrades to normal agent pass-through. The plugin
never crashes a turn.

**Two-lane doctrine (v3.8.0):** the frontier lane is the *observer*; the uncensored lane
is *capability*. Frontier calls never filter and never rewrite — they observe, orient,
and audit (as advisory "higher-self" messages); capability work (including uncensored
rendering) stays with the agent's own configured chain.

```
                       ┌──────────────────────────────────────────────────┐
   user turn ────────► │  PRE llm_request (SINGLE dispatcher pass)        │
                       │  1. inline overrides: "skip anchor" > "anchor this"│
                       │  2. struggle? (a)N≥3 same-failure (b)tool-loop    │
                       │     (c)user struggle phrasing  → OWNERSHIP       │
                       │  3. complexity classify (L0-L3, 2-stage) → PLAN  │
                       │  4. contested-class match → LANE 1 render        │
                       │  5. else → FLASH_DIRECT (pass-through)           │
                       └───────────────┬──────────────────────────────────┘
                                       │
                 ┌─────────────────────┴─────────────────────┐
                 ▼                                           ▼
        LANE 1 — uncensored                          LANE 2 — complexity/struggle
        (existing, unchanged)                        (v3, per-call)
        ─────────────────────                        ─────────────────────────
        substance-frame render via                   llm_execution middleware:
        ordered chain (primary→fallback),            per-call client → anchor
        persona from profile DNA,                    endpoint (openrouter:// or
        POST refusal swap + FIX1                     custom scheme), cap guard,
        history reconcile                            spend ledger
                                                     frontier answer enters main
                                                     model context as advisory
                                                     (kind/producer/route_id/
                                                      task_id/limitations)
```

## Lane 1 — uncensored render (v2 semantics preserved)

- **PRE** (`llm_request` middleware): contested-class regex → substance-frame render
  (ask + render composite, invisible seam). H1 sentinel prevents render-of-render.
- **POST** (`transform_llm_output` hook): refusal-shaped replies are swapped for chain
  renders in the agent's voice; unconditional on fallback recovery; loop guard keyed on
  message hash stays as-is.
- **Model chain**: ordered `chain:` (primary → fallback), first success wins.
- Persona card: derived from `HERMES_HOME/{IDENTITY,SOUL}.md` at call time,
  secret-scrubbed, mtime-cached; modes `voice_stems` (default) / `voice_only` / `full` / `none`.
- No code-side content filtering: boundaries live in the render substrate, not routing
  code.

## Lane 2 — complexity / struggle (v3)

**Higher-self frontier consults (v3.8.0)** — both lanes deliver frontier input as a
*message from your higher self*: the part of the agent that observes it while it acts.

- **PRE orientation** — before a complex task, the frontier sends an orientation message:
  how the agent's higher self would optimally do the coming job (role, voice, closed
  lines, pitfalls). The agent owns the conclusion and never disowns the turn.
- **POST completion audit** — every `post_audit_min_turns` substantive turns, the
  frontier gives its own post-intuition take on the resolution: requested-vs-delivered
  sense check, unexplored angles, what is genuinely good, what could be better. Never an
  audit *of* the main model, never first-person self-review, no user names in frames.
- **Sync with fail-open (v3.8.0)**: the POST audit runs synchronously within
  `audit_sync_seconds` (default 45) so the verdict is visible immediately. An unaudited
  turn ALWAYS delivers — a frontier failure or timeout never blocks the answer. A sync
  audit that merely times out downgrades to async and delivers next turn with a parked
  banner (slow ≠ failed). `audit_topology: sync|async` selects the default topology.
- **Revision pass**: when the audit flags a real problem, the delivered turn is
  re-run/revised on the FULL frontier model (full reasoning budget, 60s
  `audit_revision_seconds`, clamp 0–180) — never a downgraded model.
- Payloads are **agent-tailored**: PRE and POST messages carry the profile's own persona
  card (runtime-derived from `HERMES_HOME/IDENTITY.md` + `SOUL.md`) — the frontier orients
  *this* agent, not a generic specialist. Universal: any Hermes setup's agents get their
  own cards automatically, zero config.

**Consult cadence gates (v3.7.0+)** — frontier consults are rate-shaped, all knobs
live-read from config and fail-open:

| Knob | Default | Effect |
|---|---|---|
| `verify_class_exempt` | on | short imperative confirm/status asks ("can you confirm it's working?") skip PRE orientation entirely; explicit `anchor this` still overrides |
| `pre_cooldown_seconds` | 600 | minimum seconds between billed PRE consults per session (same task_id exempt, override beats it) |
| `post_audit_min_turns` | 3 | POST completion audit fires every N substantive turns, not every turn (≥3-tool-call turns audit immediately) |
| `audit_topology` | sync | POST audit topology: `sync` (45s budget, fail-open, async downgrade on timeout) or `async` (always next-turn) |
| `audit_sync_seconds` | 45 | max seconds a synchronous POST audit may hold the turn before downgrading to async |
| `audit_revision_seconds` | 60 | budget for the full-frontier revision pass (clamp 0–180; 0 disables revision) |
| `persona_card_chars` | 2500 | enriched persona card budget (0 = legacy compact card) |
| `orientation_ask_cap` | 4000 | max chars of the ask included in the orientation payload |
| `bounded_replay.last_n_turns` | 24 | conversation replay depth for frontier consults |
| `debug_banner` | 0 | route banner verbosity on delivered output: 0 off / 1 one-liner / 2 +context / 3 maximum (diagnostic only, never enters canonical/history/model context) |
| `refusal_doctrine` | always_route | v3.8.1: `always_route` (default — all refusals route to the uncensored chain, doctrine machinery inert) / `doctrine` (honor the agent's closed lines from her SOUL/IDENTITY doctrine card — backed refusals pass through unrouted) / `off` (same as always_route) |

**Zero-config frontier lane (v3.7.1+)** — if `anchor_chain.primary` is configured (you
inserted a frontier API) but NO `complexity` block exists, the complexity lane
auto-defaults to level 2 (conservative-auto). Explicit config always wins:
`complexity.enabled: false` keeps it off; an explicit level is honored as-is. Out-of-box
contract: insert your frontier (and optionally uncensored-chain) API config → both lanes
work; knobs only if you want non-defaults.

**4-mode controller** (task-scoped, never start-anchor/end-judge):

| Mode | Fires when | Effect |
|---|---|---|
| `direct` | default | pass-through, no extra calls |
| `plan` | complexity classifier fires | one anchored frontier call; the main model executes with the plan as advisory data |
| `consult` | explicit `anchor this` or gray-zone stage-2 "complex" | one bounded frontier consult; the main model keeps ownership |
| `ownership` | struggle signals fire | escalation to the anchor for the task segment |

**Detection** — 2-stage. Stage 1 is free/local regex over immutable ingress text
(planning/architecture, debug why-chains, cross-file analysis, multi-part asks).
Stage 2 (semantic aux, reusing the existing stage-2 endpoint + breaker + cap) runs ONLY
on stage-1 borderline texts — never on clear matches. Intensity per profile:

| Level | Name | Behavior |
|---|---|---|
| 0 | off | lane disabled |
| 1 | manual-only | route only on inline `anchor this` |
| 2 | conservative-auto | planning/architecture signals |
| 3 | aggressive-auto | + debug chains, cross-file, multi-part |

**Struggle detection** (router-owned — the main model cannot self-report being lost):
(a) N≥3 refusals/failures on the same task hash, (b) ≥5 provider calls in one turn with
no new tool-result content (hash dedup), (c) explicit user struggle phrasing ("still
broken", "not working", third correction). Trigger → next provider call escalates to
`ownership`.

**Inline overrides** (checked in PRE before classification, trusted origin, standalone
line only): `anchor this` → force a CONSULT route; `skip anchor` → force pass-through.

**Anchored execution** — the frontier answer never rewrites the user message. It enters
the main model's request as a provenance-stamped advisory envelope:
`{kind: frontier_plan|consultation, producer, route_id, task_id, answer, evidence_refs,
limitations}` — the main model evaluates it and writes its own turn. Per-call only; the
agent's provider configuration is never touched.

## Anchor chain config (LANE 2)

```yaml
hermes_router:
  enabled: true                       # lane 1 master
  complexity:
    enabled: true
    level: 3                          # 0-3, see table above; omit the block → auto level 2
    pre_mode: route                   # PRE orientation consults on complex-shaped asks
    audit_mode: complex               # POST completion audits
    audit_topology: sync              # sync (45s fail-open budget) | async (next-turn)
    audit_sync_seconds: 45
    audit_revision_seconds: 60
    pre_cooldown_seconds: 600
    post_audit_min_turns: 3
  anchor_chain:
    primary: openrouter://anthropic/claude-fable-5.1
    judge: openrouter://openai/o4-mini     # verification/consult tier
    overflow: pass_through                  # fail/over-cap → main model + route_skipped log
    daily_cap_usd: 2.0                      # non-tunable floor; raise only via router_control
    pricing:                                # per-model $/1M tokens (cost guard)
      openai/o4-mini: {input_per_1m: 1.15, output_per_1m: 4.60}
```

URL schemes: `openrouter://<model>` → `https://openrouter.ai/api/v1` with
`OPENROUTER_API_KEY`. Any other `<scheme>://<model>` resolves through your existing
`providers.custom` blocks in config.yaml. Unresolvable scheme → fail-open pass-through.

**Daily cap guard**: every anchored call's cost is estimated from the pricing table
(unknown models get a conservative default price so they still count), persisted
date-keyed under the profile home (`hermes-router-spend.json`). At/over cap → the
anchored call is skipped (overflow), `cap_blocked` logged, spend visible in
`router_status`. The cap is raise-only via `router_control`; lowering it is rejected.

## Decision heads (optional)

`decision_head.backend` selects how complexity is scored:

- `heuristic` (**default**) — stage-1 regex + stage-2 aux tie-break. What v3.0.0 ships.
- `routellm_mf` — trained matrix-factorization decision head vendored from
  [lm-sys/RouteLLM](https://github.com/lm-sys/RouteLLM) (Apache-2.0), weights
  `routellm/mf_gpt4_augmented` (public safetensors). Requires `torch` +
  `safetensors` importable AND cached weights at `decision_head.weights_path`
  (default `~/.hermes/.cache/routellm/mf_gpt4_augmented`); turns are embedded via the
  aux endpoint's `/embeddings` route or `decision_head.embedding_endpoint`. If anything
  is missing → silent fallback to heuristic + one-time `decision_head_fallback` log.
  No hard deps; nothing downloads at runtime.

## Tools

**`router_status`** — read-only: lane states, anchor chain (masked), today's
anchored/skipped/blocked counts, spend vs cap, last `route_skipped` reason,
decision-head status.

```
router_status()
→ {"lanes": {...}, "anchor_chain": {...masked...}, "daily_cap_usd": 2.0,
   "spend_today_usd": 0.0001, "counts_today_process": {"anchored": 1, ...},
   "decision_head": {"active_backend": "heuristic", ...}}
```

**`router_control`** — single validated-action control surface:

| Action | Args | Notes |
|---|---|---|
| `enable_lane` / `disable_lane` | `lane=uncensored\|complexity` | per-profile |
| `set_level` | `level=0..3` | complexity intensity |
| `set_endpoint` | `role=primary\|judge`, `model=<scheme>://<model>` | URI must resolve; no raw URLs |
| `set_cap` | `cap=<usd>` | raise-only, floor enforced |
| `reload` | — | dirty-flag; config is re-read per call, NO gateway bounce |
| `ping` | — | ONE live call on the judge tier, max_tokens 16 |
| `set_decision_head` | `backend=heuristic\|routellm_mf` | validated enum |

All edits go through an atomic config-writer (temp file → validate → `os.replace`).
Route logging (`log_path`/`log_routes`/`log_max_bytes`) and the loop guard are
code-owned — control actions can never introduce or alter them.

## Route log events

Every decision logs one line to `log_path` (0600, content-free):
`anchor_route_fired` (lane/mode/model_target/reason/override_used) ·
`route_skipped` (anchored call failed) · `cap_blocked` (spend, cap) ·
`escalation_fired` semantics carried by `mode=ownership` · legacy lane-1 events
(`route_fired`, `render_refusal_retry`, `loop_guard_skipped`, ...) unchanged.

## Deployment layout

Dev canonical: the plugin's own git repo. Each Hermes profile owns an independent REAL
copy of the plugin directory under its plugins path — no symlinks (a per-profile copy
lets each profile pin its own version). After copying a new version to a profile,
restart that profile's gateway so the plugin re-registers. Install via `hermes plugins
install` or a straight directory copy — see INSTALL.md.

## Config backward compatibility

Existing `uncensored_router:` profile-config sections keep working untouched — the
config readers check `hermes_router` first and fall back to `uncensored_router`.
`router_control` writes go to the canonical `hermes_router:` section and dual-write the
legacy section so fallback readers stay coherent. Canonical going forward: `hermes_router`.

## Testing

- Mock suite: 500+ tests, fully offline (`pytest` — the live marker is deselected).
- Live smoke: exactly ONE cheap call (`pytest tests/test_live_smoke.py -m live`,
  max_tokens 16, prompt "Reply with the single word: OK";
  skipped when `OPENROUTER_API_KEY` is absent). No frontier/uncensored-chain calls in tests.

## Changelog

### 3.8.0 — 2026-09-10
- **Higher-self doctrine:** PRE orientation + POST audit envelopes framed as messages
  from the agent's higher self (frontier = observer, uncensored = capability); POST is
  post-intuition on the resolution — requested-vs-delivered, unexplored angles, genuinely
  good, could-be-better — never an audit of the main model.
- **Unified audit gate:** POST audit covers benign + refusal-FP passthrough delivery
  paths; gateway-safe imports (no hard top-level `from hermes_router import` in gateway
  paths — plugins load under a `hermes_plugins.*` alias).
- **Sync POST audit:** `audit_topology` sync|async (default sync, 45s budget), fail-open
  (unaudited always delivers), timeout downgrades to async with next-turn delivery.
- **Full-frontier revision pass** (60s budget, full reasoning budget — empty verdicts
  were token starvation, not slowness).
- **Zero-config defaults** (3.7.1): `anchor_chain.primary` set + no complexity block →
  auto level 2; `/router` chat command ships ON.
- See CHANGELOG.md for the full entry.

### 3.0.0 — 2026-09-05
- **Two-lane generic router.** Lane 1 (uncensored render) keeps v2 mechanics byte-identical.
  Lane 2 (complexity/struggle): 2-stage detection, 4-mode controller, router-owned struggle
  escalation, per-call anchored execution with provenance envelopes, anchor-chain config,
  daily cap guard, router_status/router_control tools with atomic config-writer.
- **csam content gate removed** (2026-09-04): uncensored should not
  filter anything when asked — no code-side content gate remains; boundaries live in the
  render substrate. (The gate had been live-unverified since 2026-09-01 anyway.)
- **Post-mortem one-liner:** the 2026-09-01 "hard gate" referenced `session_id` three lines
  before its binding → UnboundLocalError swallowed by the outer handler → PRE silently
  passed through every time (never actually gated).
- Optional RouteLLM mf decision head (config-gated, default heuristic) — credit
  lm-sys/RouteLLM, Apache-2.0.
- Rename uncensored-router → hermes-router; package `hermes_router`; config section
  `hermes_router:` canonical with `uncensored_router:` fallback.
- Live smoke: ONE openrouter o4-mini call, skip-by-default, key-gated.

### 2.x — see CHANGELOG.md (v2.0.0 → v2.4.0: chain fallback, persona modes, digest
frame-strip, composite frame, render-shape guard, thread-digest refusal exclusion,
persona none mode, orchestrator lane override).

## License

Internal — falke-ai-circuit. RouteLLM decision-head code is vendored under Apache-2.0
(copyright lm-sys contributors); see `decision_head.py` header.