# hermes-router

Generic **two-lane Hermes router** plugin (any profile, any agent). Lane 1 is the proven
uncensored render lane (v2 behavior, byte-identical mechanics). Lane 2 is the v3
complexity/struggle lane: when a task needs stronger reasoning — or the flash model is
visibly struggling — ONE per-call anchored call goes to a configured frontier model, and
its answer enters the flash model's context as a provenance-stamped advisory envelope.
Fail-open everywhere: every failure degrades to normal agent pass-through. The plugin
never crashes a turn.

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
                                                     frontier answer enters flash
                                                     context as advisory envelope
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
- **Persona card**: derived from `HERMES_HOME/{IDENTITY,SOUL}.md` at call time,
  secret-scrubbed, mtime-cached; modes `voice_stems` (default) / `voice_only` / `full` / `none`.
- The 2026-09-04 Goran-direct reversal removed the csam content gate entirely: no
  code-side filtering — boundaries live in the render substrate, not routing code.

## Lane 2 — complexity / struggle (v3)

**4-mode controller** (task-scoped, never start-anchor/end-judge):

| Mode | Fires when | Effect |
|---|---|---|
| `flash_direct` | default | pass-through, no extra calls |
| `plan` | complexity classifier fires | one anchored frontier call; flash executes with the plan as advisory data |
| `consult` | explicit `anchor this` or gray-zone stage-2 "complex" | one bounded frontier consult; flash keeps ownership |
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
| 3 | aggressive-auto | + debug chains, cross-file, multi-part (default in config examples) |

**Struggle detection** (router-owned — flash cannot self-report being lost):
(a) N≥3 refusals/failures on the same task hash, (b) ≥5 provider calls in one turn with
no new tool-result content (hash dedup), (c) explicit user struggle phrasing ("still
broken", "not working", third correction). Trigger → next provider call escalates to
`ownership`.

**Inline overrides** (checked in PRE before classification, trusted origin, standalone
line only): `anchor this` → force a CONSULT route; `skip anchor` → force pass-through.

**Anchored execution** — the frontier answer never rewrites the user message. It enters
the flash request as a provenance-stamped advisory envelope:
`{kind: frontier_plan|consultation, producer, route_id, task_id, answer, evidence_refs,
limitations}` — flash evaluates it and writes its own turn. Per-call only; the agent's
provider configuration is never touched.

## Anchor chain config (LANE 2)

```yaml
hermes_router:
  enabled: true                       # lane 1 master
  complexity:
    enabled: true
    level: 3                          # 0-3, see table above
  anchor_chain:
    primary: openrouter://anthropic/claude-fable-5.1
    judge: openrouter://openai/o4-mini     # verification/consult tier
    overflow: pass_through                  # fail/over-cap → flash + route_skipped log
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

Dev canonical: `/opt/data/plugins/hermes_router` (its own git repo). Each profile owns
an independent REAL copy at `/opt/data/profiles/<agent>/plugins/hermes_router` —
no symlinks (Goran 2026-09-02). Propagate with
`/opt/data/uncensored-router-update-all.sh` (`--dry-run` first; deploys under the NEW
name, seeds from and then removes the legacy `uncensored_router` dir, prints a
per-profile md5 table). Bounce gateways after a copy: `/command/s6-svc -r
/run/service/gateway-<agent>`.

## Config backward compatibility

Existing `uncensored_router:` profile-config sections keep working untouched — the
config readers check `hermes_router` first and fall back to `uncensored_router`
(all 11 deployed profiles migrate transparently). `router_control` writes go to the
canonical `hermes_router:` section and dual-write the legacy section so fallback
readers stay coherent. Canonical going forward: `hermes_router`.

## Testing

- Mock suite: 240 tests, fully offline (`pytest` — the live marker is deselected).
- Live smoke: exactly ONE cheap call (`pytest tests/test_live_smoke.py -m live`,
  openrouter openai/o4-mini, max_tokens 64, prompt "Reply with the single word: OK";
  skipped when `OPENROUTER_API_KEY` is absent). No frontier/fable/astra calls in tests.

## Changelog

### 3.0.0 — 2026-09-05
- **Two-lane generic router.** Lane 1 (uncensored render) keeps v2 mechanics byte-identical.
  Lane 2 (complexity/struggle): 2-stage detection, 4-mode controller, router-owned struggle
  escalation, per-call anchored execution with provenance envelopes, anchor-chain config,
  daily cap guard, router_status/router_control tools with atomic config-writer.
- **csam content gate removed** (2026-09-04, Goran-direct reversal): uncensored should not
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