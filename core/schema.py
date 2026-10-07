# core/schema.py — typed config key inventory (proposal §2.3, P4).
#
# SCHEMA is seeded from two sources:
#   1. plugin.yaml config_schema (the top-level scalars + section dict keys);
#   2. the code-read key inventory of dispatcher_knobs + the residual readers
#      (the finer internal knobs: pov_mode, jev_native_model, stage1,
#      rescan_dedupe, consult_cooldown_turns, thread_digest_*, banner_*,
#      aux_consult_min_interval_sec, routing_daily_cap_usd, ...).
#
# config_access.get(path) validates against SCHEMA: unknown path or missing
# value never silently returns None — the typed default comes back and a
# telemetry row is emitted. sub_block() keeps its shape for existing callers.
from dataclasses import dataclass
from typing import Any, Tuple


@dataclass(frozen=True)
class Key:
    path: str                  # dotted: "anchor_chain.reasoning_effort", "debug_banner"
    type: Any                  # bool | int | str | list | dict
    default: Any
    nullable: bool
    control_writable: bool     # cross-checked against config_writer CANONICAL_SECTION rules


def _k(path, type_, default, nullable=False, control_writable=True):  # noqa: ANN001
    return Key(path=path, type=type_, default=default,
               nullable=nullable, control_writable=control_writable)


# Top-level scalars — defaults EXACTLY equal the literals the readers use.
# (plugin.yaml config_schema + dispatcher_knobs code-read inventory)
SCHEMA: Tuple[Key, ...] = (
    # --- plugin.yaml config_schema scalars ---
    _k("enabled", bool, True),
    _k("refusal_doctrine", str, "always_route"),
    _k("dry_run", bool, False),
    _k("pending_routes_ttl_seconds", int, 300),
    _k("render_max_chars", int, 0),
    _k("log_path", str, "/tmp/hermes-router.log"),
    _k("log_routes", bool, True),
    _k("log_max_bytes", int, 10485760),
    _k("substance_frame", str, ""),
    _k("render_method_spec", str, ""),
    _k("debug_banner", bool, False),
    # --- chain (LANE-1 chain spec, list of endpoint dicts) ---
    _k("chain", list, [], nullable=True),
    # --- dispatcher_knobs / code-read finer knobs (top level) ---
    _k("persona_card_chars", int, 2500),
    _k("on_demand_routing", bool, False),
    _k("on_demand_aux_classify", bool, False),
    _k("orientation_ask_cap", int, 4000),
    _k("routing_daily_cap_usd", float, 0.0, nullable=True),
    _k("thread_digest_chars", int, 3000),
    _k("thread_digest_asks", int, 6),
    _k("flinch_reason_gate", bool, True),
    _k("post_banner_cap_per_run", int, 3),
    _k("banner_capture_fallback_wait", int, 45),
    _k("pov_mode", str, "auto"),
    _k("jev_native_model", str, "", nullable=True),
    _k("stage1", bool, True),
    _k("rescan_dedupe", bool, True),
    _k("embedding_endpoint", str, "", nullable=True),
    _k("weights_path", str, "", nullable=True),
    _k("decision_mode", str, "", nullable=True),
    # --- decision-lane knobs (decision.py reads, top level) ---
    _k("jev_model", str, ""),
    _k("openrouter_endpoint", str, ""),
    _k("api_key_env", str, ""),
    _k("typesafe_endpoint", str, "", nullable=True),
    _k("typesafe_api_key_env", str, "", nullable=True),
    _k("backend", str, "nous"),
    _k("backend_timeout_seconds", float, 15.0),
    _k("score_timeout_seconds", float, 45.0),
    _k("confidence_threshold", float, 0.5),
    _k("breaker_fails", int, 3),
    _k("breaker_cooldown_s", int, 600),
    _k("calls_per_hour", int, 20),
    _k("misfire_confidence", float, 0.3),
    _k("max_frame_chars", int, 8000),
    _k("max_snippet_chars", int, 400),
    _k("max_precedent_age_days", int, 30),
    _k("provenance_window_chars", int, 3000),
    _k("enum_min_chars", int, 40),
    _k("enum_min_items", int, 3),
    _k("evol_tail_lines", int, 30),
    _k("extract_cap", int, 8000),
    _k("bounded_replay", bool, False),
    # --- sub-blocks (dict sections; read via sub_block()/get() walking) ---
    _k("classification", dict, {}),
    _k("complexity", dict, {}),
    _k("anchor_chain", dict, {}),
    _k("decision_head", dict, {}),
    _k("decision", dict, {}),
    _k("reflex", dict, {}),
    _k("risk", dict, {}),
    _k("frontier", dict, {}),
    _k("uncensored_router", dict, {}, nullable=True),
    # --- budgets (proposal §2.4 — config overlay for core/budgets.py) ---
    _k("budgets", dict, {}),
    _k("budgets.consult.max_tokens", int, 512),
    _k("budgets.verdict.max_tokens", int, 512),
    _k("budgets.completion_audit.max_tokens", int, 12000),
    _k("budgets.render.max_tokens", int, 2048),
    _k("budgets.probe.max_tokens", int, 256),
    # --- finer leaf knobs inside the sub-blocks (code-read) ---
    _k("complexity.pov_mode", str, "auto"),
    _k("complexity.stage1", bool, True),
    _k("complexity.consult_cooldown_turns", int, 5),
    _k("complexity.aux_consult_min_interval_sec", int, 300),
    _k("frontier.pov_mode", str, "auto"),
    _k("frontier.consult_cooldown_turns", int, 5),
    _k("frontier.aux_consult_min_interval_sec", int, 300),
    _k("frontier.ttl_s", int, 0, nullable=True),
    _k("frontier.base_s", int, 0, nullable=True),
    _k("frontier.max_s", int, 0, nullable=True),
    _k("decision.miner_max_records", int, 200),
    _k("decision.miner_scan_days", int, 14),
    _k("decision.post_audit", bool, True),
    _k("decision.mode", str, "", nullable=True),
    _k("decision.frame_context_chars", int, 0, nullable=True),
    _k("decision.confidence_threshold", float, 0.5),
    _k("decision.model", str, "", nullable=True),
    _k("decision.endpoint", str, "", nullable=True),
    _k("decision.api_key_env", str, "", nullable=True),
    _k("decision.pre", dict, {}),
    _k("decision.post", dict, {}),
    _k("anchor_chain.reasoning_effort", str, "", nullable=True),
    _k("anchor_chain.summary_header", str, "", nullable=True),
    _k("anchor_chain.threshold", float, 0.0, nullable=True),
    _k("anchor_chain.enabled", bool, True),
    _k("anchor_chain.model", str, "", nullable=True),
    _k("anchor_chain.endpoint", str, "", nullable=True),
    _k("anchor_chain.api_key_env", str, "", nullable=True),
    _k("anchor_chain.on_demand", bool, False),
    _k("anchor_chain.caps", dict, {}),
    _k("decision_head.mode", str, "", nullable=True),
    _k("decision_head.enabled", bool, True),
    _k("decision_head.level", int, 0),
    _k("decision_head.slice", str, "", nullable=True),
    _k("decision_head.confidence_threshold", float, 0.5),
    _k("classification.semantic_gate", bool, False),
    _k("classification.pre_patterns", list, []),
    _k("classification.two_vote_confirm", str, "on"),
    _k("classification.two_vote_groups", dict, {}, nullable=True),
    _k("classification.aux_classify", dict, {}),
    _k("risk.enabled", bool, True),
    _k("risk.endpoint", str, "", nullable=True),
    _k("risk.chain", list, [], nullable=True),
    _k("risk.on_demand", bool, False),
    _k("reflex.pov_mode", str, "auto"),
    _k("frontier.banner_capture_fallback_wait", int, 45),
    _k("frontier.debug_banner", bool, False),
)

_BY_PATH = {k.path: k for k in SCHEMA}


def key(path: str) -> Any:
    """The SCHEMA Key for a dotted path, or None if unknown."""
    return _BY_PATH.get(str(path))
