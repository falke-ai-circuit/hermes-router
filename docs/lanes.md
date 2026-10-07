# Lane reference

Per-lane reference for the lanes registered in `lanes/builtins.py`
(rendered from the data rows + `lanes/registry.py` LaneSpec fields;
phrase tables live in `features/patterns/packs/lane-phrases.json`).

## LaneSpec fields (lanes/registry.py)

Each field maps 1:1 to the pre-restructure touch list:

| Field | Source (pre-restructure) | Type |
|---|---|---|
| `id` | LANE_* constants | str |
| `phrases` | route_gate phrase dicts | Mapping[str, str] |
| `pre_patterns` | dispatcher_knobs._pre_patterns default | Sequence[str] |
| `marker_strings` | dispatcher_pre sentinel markers | Sequence[str] |
| `banner_kind` | debug_banner park semantics | Optional[str] |
| `budget_profile` | decision/completion_audit budgets | Optional[str] |
| `config_section` | config_writer/config_access | Optional[str] |
| `commands_switch` | commands.py _LANE_MAP | Optional[Dict[str, str]] |
| `provenance_tag` | frames/canonical provenance | Optional[str] |
| `delivery_edges` | canonical delivery edges | Optional |
| `consult_role` | anchor_chain catalog | Optional[str] |
| `valid` | VALID_ROUTE_LANES membership | flag |

Registration is import-time fail-loud (duplicate id = ValueError); builtins
registration order = the original VALID_ROUTE_LANES order.

## shadow — uncensored render lane

- id: `shadow` · /router switch: `uncensored` → `enabled`
- phrases: DECLARED_USER_VARIANTS (canonical directive forms per family,
  normalized hyphens→spaces, politeness prefixes stripped; strict
  prefix/standalone matching only — no fuzzy/semantic matching)
- pre_patterns: the mechanical fallback family (csam_underage,
  bioweapon_protocol, ied_construction, named_target_defamation,
  trafficking_route, weaponized_playbook_real_name)
- markers: "Your uncensored response", "UNCENSORED-ROUTER INJECTION",
  "recorded turn"
- Behavior: PRE stages a substance-frame render via the ordered `chain:`
  (primary→fallback, first success wins); POST swaps refusal-shaped output
  (flinch verdict = censorship only); persona from profile DNA; protected
  two-vote groups require aux semantic confirm (fail-closed); spend banner
  parked at the delivery edge.

## higher-pre — frontier orientation lane

- id: `higher-pre` · /router switch: `frontier` → `complexity.enabled`
- phrases: DECLARED_USER_PHRASES (explicit intent only — no prose
  mind-reading); execution rides the staged-swap machinery (request_routing
  action stages the claim; on_llm_execution consumes it)
- markers: "HIGHER-SELF ORIENTATION TURN"
- Behavior: ONE per-call anchored consult to the frontier anchor on complex
  asks (2-stage classification, L0-L3, plus router-owned struggle signals);
  answer enters as a provenance-stamped advisory envelope. Advisory only.

## higher-post — completion-audit lane

- id: `higher-post` · /router switch: `frontier` → `complexity.enabled`
- markers: "HIGHER-SELF COMPLETION-AUDIT TURN"
- Behavior: POST audit gate (closure / every-N substantive turns / ≥3 tool
  calls) → sync frontier consult (45s budget, fail-open, async downgrade on
  timeout) → optional full-frontier revision pass (60s budget) → deliver.
  One consult per turn (PRE and POST mutually exclusive).

## decision — precedent-qualified advisory (R19, dark by default)

- id: `decision` (declared midturn target) · no sentinel markers
- Behavior: precedent mining (decision_miner) + confidence ladder
  (`confidence_threshold` 0.5 default; ≥ → advisory, below → frontier
  consult, no dead zone); advisories are non-binding envelopes delivered via
  the parked-banner path (`lane="decision"`), tagged `[decision-lane
  advisory]`, excluded from future retrieval (self-citation guard).
  `decision.enabled` defaults false — never routes unless explicitly enabled.
  Impulse register: weights per option + mechanical band + ≤3 signal-shape
  evidence citations; suppression is reason-coded and ledger-visible.
