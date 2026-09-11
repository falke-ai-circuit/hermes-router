# Operator Guide

hermes-router augments a Hermes agent with two optional lanes — a
**frontier consult lane** ("higher self", advisory) and an **uncensored
render lane** ("shadow self", capability) — plus a routing brain that
decides per turn whether either lane should fire. This guide covers
install, verify, diagnose, and upgrade. Architecture details:
[architecture.md](architecture.md).

## Install

1. Copy (or clone) this repo into the target profile's plugin directory:
   `<profile>/plugins/hermes_router/`.
2. Ensure the profile `config.yaml` has a `hermes_router:` block (a
   missing block = safe defaults, router ON for the frontier lane).
3. Provide provider credentials as environment variables or in the
   profile `.env`: `NOUS_API_KEY` for the frontier anchor chain, plus
   whichever uncensored-chain keys you configure.
4. Restart the gateway for the profile:
   `/command/s6-svc -r /run/service/gateway-<profile>`.
5. Verify: from the agent's Telegram surface, send `/router` — it
   replies with live status (lanes, level, endpoints). Slash commands
   work on the chat surface only, not the API surface.

Minimal config (frontier lane only):

```yaml
hermes_router:
  anchor_chain:
    primary: nous://z-ai/glm-5.3
  debug_banner: 1        # 0 off · 1 resting · 2 verbose · 3 debug
```

The uncensored lane is opt-in: it fires only when its endpoints and keys
are configured AND the profile's doctrine authorizes it.

## What "working" looks like

- Frontier consults append a one-line banner to the delivered turn
  (`router · frontier …` with model, tokens, cost). Every billed call
  shows one — if a consult ran, you will see its banner.
- Uncensored renders are delivered in the agent's own voice with no
  visible marker in the body; provenance lives in the route log, not
  the message.
- Route decisions log to `/tmp/uncensored-router-<profile>.log`, one
  event per line:
  - `anchor_route_fired lane=… reason=…` (PRE consult dispatched)
  - `completion_audit_gate ok=True closure=True` (POST audit dispatched)
  - `route_fired_no_stash pattern_groups=…` (uncensored render delivered)
  - `audit_gate_skip reason=…` (gate considered and skipped — normal)

## What "failed open" looks like

Fail-open is silent by design — the turn always delivers. Signs to check:

- **No banner after a consult-shaped turn:** grep the route log for
  `dispatch_error` / `anchor_route_failed`. Ledger
  (`<profile>/hermes-router-spend.json`) shows whether a call was billed.
- **`anchor_route_failed reason=key_unavailable`:** credential missing
  from env AND profile dotenv. Fix the key; no restart needed for
  config reads.
- **`render_refusal` flag / empty render:** the uncensored provider
  declined; the retry ladder escalates then fails open. Check provider
  status; the backoff ledger (`hermes-router-backoff.json`) appears
  only after repeated anchor failures.
- **Slow turns (~45s):** sync completion audit in progress. Tune
  `complexity.audit_sync_seconds` (0 disables sync, falls back to the
  next-turn async path).

## Knobs that matter

| Knob (config.yaml) | Default | Meaning |
|---|---|---|
| `debug_banner` | 1 | banner verbosity 0–3 |
| `complexity.level` | 0/2 | 0 = manual `anchor this` only, 2 = auto |
| `complexity.audit_mode` | complex | POST audit: off / complex / always |
| `complexity.audit_sync_seconds` | 45 | sync consult budget; 0 = async |
| `pre_cooldown_seconds` | 600 | min seconds between PRE consults |
| `post_audit_min_turns` | 3 | POST cadence (also fires on closure / ≥3 tools) |

Config edits are read live per dispatch. Plugin `.py` changes require a
gateway restart.

## Upgrade

1. `git pull` (or copy) the new tree into each profile's plugin dir.
2. Restart each gateway.
3. State sidecars are forward-compatible JSON keyed by profile; old
   ledgers load, unknown fields are ignored. No migration step has been
   required through v3.9.0 — if a future version needs one, its
   CHANGELOG entry will say so explicitly.

## Manual lane controls (per turn, chat surface)

- `anchor this` — force a frontier consult on this turn.
- `skip anchor` — bypass all routing for this turn.

## Known limitations

- Sentinel markers are string-matched; injected text can forge them to
  suppress routing (see SECURITY.md).
- No concurrency stress coverage yet: parallel sessions on one profile
  are serialized by the gateway, but sidecar locking is untested under
  synthetic parallelism.
- The battery (`benchmarks/`) is a live canary harness, not part of CI.
