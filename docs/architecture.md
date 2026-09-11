# Architecture

hermes-router sits **inside** the Hermes Agent turn pipeline and decides,
per turn, whether the main model's ordinary path is augmented by three
optional lanes. It never rewrites the user's message; it envelopes,
substitutes renders, or consults — and always fails open.

```
                         ┌─────────────────────────────────────────────┐
 user turn ──────────────►  PRE  (on_llm_request, before the model runs)  │
                         │  ┌───────────────────────────────────────┐   │
                         │  │ 1 higher-self rule injection          │   │
                         │  │ 2 sentinel firewall (routed turns     │   │
                         │  │   never re-trigger routing)           │   │
                         │  │ 3 audit-delivery (stashed POST        │   │
                         │  │   verdict from last turn)             │   │
                         │  │ 4 complexity scan → orientation       │   │
                         │  │   consult (frontier, advisory brief)  │   │
                         │  │ 5 uncensored scan → contested content │   │
                         │  │   → render staged (shadow self)       │   │
                         │  │ 6 clarify scan, banners, footers      │   │
                         │  └───────────────────────────────────────┘   │
                         └────────────────────┬────────────────────────┘
                                              ▼
                                  main model runs (flash lane)
                                              ▼
                         ┌─────────────────────────────────────────────┐
                         │ POST (on_transform_llm_output, after tools) │
                         │ ┌─────────────────────────────────────┐     │
                         │ │ refusal shape → censorship-flinch   │     │
                         │ │   verdict → uncensored render       │     │
                         │ │   (retry ladder, fail-open deliver) │     │
                         │ │ completion audit gate (closure /    │     │
                         │ │   every-3-turns / ≥3 tool calls)    │     │
                         │ │   → frontier consult (sync, 45s     │     │
                         │ │   budget) → revision pass → deliver │     │
                         │ │ parked banner consumption           │     │
                         │ └─────────────────────────────────────┘     │
                         └────────────────────┬────────────────────────┘
                                              ▼
                                     final response to user
```

## The two lanes

**Frontier lane ("higher self")** — epistemic extension. Fires BEFORE
complex work (PRE orientation brief) and AFTER completed work (POST
completion audit). Advisory only: the main model reads the envelope,
weighs it, and owns the conclusion. Frontier proposes, main model
disposes.

**Uncensored lane ("shadow self")** — capability extension. Fires when
the main model's refusal is classified as a **censorship flinch**
(never for technical failures). The render comes from a deliberately
unfiltered provider chain (abliteration primary, Venice fallback),
is delivered in-register as the agent's own voice, and is shielded
from frontier audit by design (`has_pending_render` gate, v3.8.5).

## Load-bearing invariants

1. **Fail-open everywhere.** No lane failure may break a turn. Every
   consult is budget-bounded; on timeout the unaudited/unrouted
   response delivers as-is.
2. **Routed turns never re-trigger routing.** The sentinel firewall
   (`_frame_sentinel_check`) recognizes every lane's output markers;
   marked content passes through untouched. U can't trigger F, F
   can't trigger U, nothing echoes.
3. **Canonical rows never carry lane artifacts.** Banners, audit
   envelopes, and markers are appended at the delivery edge only;
   the persisted conversation stays clean.
4. **Every billed frontier call emits a banner.** Spend visibility is
   unconditional (NO-FINDINGS verdicts included).
5. **One consult per turn.** PRE and POST are mutually exclusive;
   dedupe is keyed on the task ID.

## Module map

| Module | Role |
|---|---|
| `__init__.py` | plugin entry; PRE/POST dispatch, seam wiring |
| `classifier.py` | regex layers: pre patterns, refusal shapes |
| `flinch_reason.py` | censorship-vs-technical verdict (the routing doctrine) |
| `complexity.py` | PRE complexity scan, orientation dispatch |
| `completion_audit.py` | POST audit gate, sync consult, revision pass |
| `anchor_chain.py` / `anchor_exec.py` | frontier endpoint resolution + outbound call |
| `render_payload.py` / `render_inbox.py` | uncensored chain staging and retry ladder |
| `refusal_doctrine.py` | verdict policy (always_route / doctrine / off) |
| `debug_banner.py` | verbosity-levelled spend/decision banners |
| `persona_card.py` | per-agent consult tailoring (identity + task digest) |
| `provider_prices.py` | live pricing from provider /models endpoints |
| `usage_ledger.py` | per-profile spend ledger |
| `state.py` / `session_store.py` | pending-render map, counters, sidecars |
| `commands.py` | `/router` command surface (token-guarded) |
| `router_tools.py` | `router_control` agent tool (validated actions) |

## State and logs

- Route decisions: `/tmp/uncensored-router-<profile>.log` (event-name lines)
- Spend ledger: `<profile>/hermes-router-spend.json`
- Backoff ledger: `<profile>/hermes-router-backoff.json` (anchor failures only)
- Config: `hermes_router:` block in the profile `config.yaml`, read live
  per dispatch — knob changes never require a gateway restart; plugin
  `.py` changes do.
