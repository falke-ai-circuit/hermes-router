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
from typing import Any, Dict, Tuple


@dataclass(frozen=True)
class Key:
    path: str                  # dotted: "anchor_chain.reasoning_effort", "debug_banner"
    type: Any                  # bool | int | str | list | dict
    default: Any
    nullable: bool
    control_writable: bool     # cross-checked against config_writer CANONICAL_SECTION rules
    # M23-01: the OLD name(s) this key replaces, comma-separated ("chain,endpoint").
    # ALIASES is derived from this field, so a grouped key states its own migration
    # row and old spellings keep resolving through config_access.get().
    legacy_of: str = ""
    # R34-6: a LEGACY flat leaf that the live nested block supersedes. Load
    # validation skips it when the nested shape is present, so a profile on
    # the nested schema stops emitting bogus config_key_missing rows while
    # real drift in the nested keys still warns. (Distinct from legacy_of,
    # which names what a NEW key replaces.)
    superseded_by: str = ""


def _k(path, type_, default, nullable=False, control_writable=True,
       legacy_of="", superseded_by=""):  # noqa: ANN001
    return Key(path=path, type=type_, default=default,
               nullable=nullable, control_writable=control_writable,
               legacy_of=legacy_of, superseded_by=superseded_by)


# Top-level scalars — defaults EXACTLY equal the literals the readers use.
# (plugin.yaml config_schema + dispatcher_knobs code-read inventory)
# M23-01: this tuple is the PRE-M23 inventory, kept byte-identical (nothing is
# removed in v1). The grouped declaration below ADDS to it; SCHEMA is the union.
_V1_KEYS: Tuple[Key, ...] = (
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
    # bool OR int 0-3: the legacy flag and the current verbosity level are both
    # valid inputs and debug_banner_level() accepts either.
    _k("debug_banner", (bool, int), False),
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
    # --- R22 two-tier detection (features/detection/semantic_judge) ---
    _k("detection", dict, {}),
    _k("detection.enabled", bool, True),
    _k("detection.model", str, "auto"),
    # --- budgets (proposal §2.4 — config overlay for core/budgets.py) ---
    _k("budgets", dict, {}),
    # --- unified lane envelope (features/envelope/unified.py) ---
    # chars_cap: the request envelope budget. drop_order: slot paths emptied
    # (in order) until the envelope fits; every drop is recorded in `trimmed`.
    _k("envelope.chars_cap", int, 8000),
    _k("envelope.drop_order", list, ["slice", "agent_card.history",
                                     "context.session_context"]),
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
    # R34-6: the LIVE nested anchor_chain shape (primary/models/pricing/
    # judge/daily_cap_usd) demonstrably works (sonnet-5.5 consults with
    # correct cost rows) but the validator only knew the legacy flat leaves,
    # so every load emitted ~73-78 bogus config_key_missing rows per profile.
    _k("anchor_chain.primary", dict, {}),
    _k("anchor_chain.models", dict, {}),
    _k("anchor_chain.pricing", dict, {}),
    _k("anchor_chain.judge", dict, {}),
    _k("anchor_chain.daily_cap_usd", float, 0.0),
    # R34-9: consult payload hygiene + empty-response recovery knobs.
    _k("anchor_chain.max_history_turns", int, 12),
    _k("anchor_chain.failover", list, [], nullable=True),
    # Legacy flat leaves (pre-nested profiles). Kept readable via get() but
    # SKIPPED by load validation when the nested block is present.
    _k("anchor_chain.reasoning_effort", str, "", nullable=True,
       superseded_by="anchor_chain"),
    _k("anchor_chain.summary_header", str, "", nullable=True,
       superseded_by="anchor_chain"),
    _k("anchor_chain.threshold", float, 0.0, nullable=True,
       superseded_by="anchor_chain"),
    _k("anchor_chain.enabled", bool, True, superseded_by="anchor_chain"),
    _k("anchor_chain.model", str, "", nullable=True,
       superseded_by="anchor_chain"),
    _k("anchor_chain.endpoint", str, "", nullable=True,
       superseded_by="anchor_chain"),
    _k("anchor_chain.api_key_env", str, "", nullable=True,
       superseded_by="anchor_chain"),
    _k("anchor_chain.on_demand", bool, False),
    _k("anchor_chain.caps", dict, {}),
    _k("decision_head.mode", str, "", nullable=True),
    _k("decision_head.enabled", bool, True),
    _k("decision_head.level", int, 0),
    _k("decision_head.slice", str, "", nullable=True),
    _k("decision_head.confidence_threshold", float, 0.5),
    _k("classification.semantic_gate", bool, False),
    _k("classification.pre_patterns", list, []),

    _k("classification.aux_classify", dict, {}),
    _k("risk.enabled", bool, True),
    _k("risk.endpoint", str, "", nullable=True),
    _k("risk.chain", list, [], nullable=True),
    _k("risk.on_demand", bool, False),
    _k("reflex.pov_mode", str, "auto"),
    _k("frontier.banner_capture_fallback_wait", int, 45),
    _k("frontier.debug_banner", (bool, int), False),
)


# ---------------------------------------------------------------------------
# M23-01 — GROUPED SCHEMA DECLARATION (spec: M20_CONFIG_CONSULT §5 + App. A)
# ---------------------------------------------------------------------------
# Ten groups. Every key is typed and defaulted here; `legacy_of` names the old
# spelling(s) the key replaces, so the migration table lives in code and both
# spellings resolve (config_access.get() reads the grouped path first, then the
# deployed old name). Defaults EQUAL the current code literals — no default is
# flipped in M23 (the corrected decision.* defaults are Phase 3, behind
# HERMES_ROUTER_LEGACY_DEFAULTS). Nothing is removed in v1.
GROUPS: Tuple[str, ...] = (
    "operator",   # day-to-day switches
    "render",     # LANE 1 — uncensored render chain
    "classify",   # PRE/POST detection + classification
    "anchor",     # LANE 2 — complexity/struggle consult
    "decision",   # decision lane (fork/consult/frame)
    "reflex",     # reflex/instinct lane
    "persona",    # persona card + integration frames
    "budgets",    # per-lane token/char envelopes
    "paths",      # CODE-OWNED: state dir + route log
    "pricing",    # $/1M model pricing table
)

_GROUPED_KEYS: Tuple[Key, ...] = (
    # --- operator -----------------------------------------------------------
    _k("operator.enabled", bool, True, legacy_of="enabled"),
    _k("operator.dry_run", bool, False, legacy_of="dry_run"),
    _k("operator.pending_routes_ttl_seconds", int, 300,
       legacy_of="pending_routes_ttl_seconds"),
    # env-only today (HERMES_ROUTER_ENABLE_SLASH_COMMAND); declared so the
    # operator surface and the env override name the same setting.
    _k("operator.slash_command", bool, True),

    # --- render (LANE 1) ----------------------------------------------------
    _k("render.chain", list, [], nullable=True, legacy_of="chain,endpoint"),
    _k("render.max_chars", int, 0, legacy_of="render_max_chars"),
    _k("render.method_spec", str, "", legacy_of="render_method_spec"),
    _k("render.refusal_doctrine", str, "always_route",
       legacy_of="refusal_doctrine"),
    _k("render.substance_frame", str, "", legacy_of="substance_frame"),
    # App. A undeclared key: opt-IN, default FALSE (provenance_footer.py:105).
    _k("render.provenance_footer", bool, False, legacy_of="provenance_footer"),
    # env var name or file:<path>; replaces the key_file/key_env duality.
    _k("render.key_ref", str, ""),

    # --- classify -----------------------------------------------------------
    # the block container itself: sub_block("classify") is a literal read
    _k("classify", dict, {}),
    _k("classify.pre_enabled", bool, True,
       legacy_of="classification.pre_classify"),
    _k("classify.post_enabled", bool, True,
       legacy_of="classification.post_classify"),
    _k("classify.mode", str, "route", legacy_of="classification.mode"),
    _k("classify.match_threshold", float, 1.0,
       legacy_of="classification.match_threshold"),
    _k("classify.pre_patterns", list, [], legacy_of="classification.pre_patterns"),
    _k("classify.post_patterns", list, [],
       legacy_of="classification.post_patterns"),
    _k("classify.semantic_gate", bool, False,
       legacy_of="classification.semantic_gate"),
    _k("classify.aux", dict, {}),
    _k("classify.aux.enabled", bool, True,
       legacy_of="classification.aux_classify"),
    _k("classify.aux.provider", str, "nous"),
    _k("classify.aux.endpoint", str, "",
       legacy_of="classification.aux_endpoint"),
    _k("classify.aux.model", str, "", legacy_of="classification.aux_model"),
    _k("classify.aux.key_ref", str, ""),
    # replaces the silent HERMES_AUX_TIMEOUT_SECONDS = 45 literal
    _k("classify.aux.timeout_s", int, 45),
    _k("classify.aux.calls_per_hour", int, 20,
       legacy_of="classification.aux_calls_per_hour"),
    _k("classify.aux.breaker_failures", int, 3,
       legacy_of="classification.aux_breaker_failures"),
    _k("classify.aux.breaker_cooldown_s", int, 600,
       legacy_of="classification.aux_breaker_cooldown_seconds"),
    # M41-07b RETIREMENT: the seven retired PRE-probe `classify.*` keys are gone
    # with the module (spec M41_BUILD_SPEC.md:313). No profile config carries
    # any of them — verified by scanning all 11 /opt/data/profiles/*/config.yaml
    # for the probe key prefix, zero hits — so no deprecation window is owed:
    # an old config carrying a retired key is what load validation should flag,
    # and no shipped config does.

    # --- anchor (LANE 2) ----------------------------------------------------
    _k("anchor.enabled", bool, True, legacy_of="anchor_chain.enabled"),
    _k("anchor.primary", str, "", legacy_of="anchor_chain.primary"),
    _k("anchor.judge", str, "", legacy_of="anchor_chain.judge"),
    _k("anchor.failover", list, [], nullable=True,
       legacy_of="anchor_chain.failover"),
    # read today but undeclared (anchor_chain.py); default = live fallback.
    _k("anchor.overflow", str, "pass_through", legacy_of="anchor_chain.overflow"),
    _k("anchor.daily_cap_usd", float, 2.0, legacy_of="anchor_chain.daily_cap_usd"),
    _k("anchor.reasoning_effort", str, "", nullable=True,
       legacy_of="anchor_chain.reasoning_effort"),
    _k("anchor.max_history_turns", int, 12,
       legacy_of="anchor_chain.max_history_turns"),
    _k("anchor.complexity_level", int, 2, legacy_of="complexity.level"),
    _k("anchor.models", dict, {}, legacy_of="anchor_chain.models"),
    _k("anchor.pricing", dict, {}, legacy_of="anchor_chain.pricing"),

    # --- decision (the live block on all 11 profiles is already this shape) --
    _k("decision.enabled", bool, False, legacy_of="decision_head.enabled"),
    _k("decision.seam", str, "midturn_prellm"),
    _k("decision.backend", str, "jev_native"),

    # --- reflex -------------------------------------------------------------
    _k("reflex.enabled", bool, False),
    _k("reflex.timeout_s", int, 8),
    _k("reflex.min_top", float, 0.45),
    _k("reflex.min_margin", float, 0.10),
    _k("reflex.weights", dict, {}, legacy_of="reflex_weights"),

    # --- persona ------------------------------------------------------------
    _k("persona.mode", str, "voice_stems", legacy_of="persona_mode"),
    _k("persona.card_chars", int, 2500, legacy_of="persona_card_chars"),
    _k("persona.shadow_integration_frame", str, "impulse",
       legacy_of="shadow_integration_frame"),
    _k("persona.higher_self_integration_frame", str, "internal-observer",
       legacy_of="higher_self_integration_frame"),

    # --- budgets (max_tokens leaves already declared above) -----------------
    _k("budgets.readout", dict, {}, legacy_of="budget_readout"),

    # --- paths (CODE-OWNED: never router_control-writable) ------------------
    _k("paths.state_dir", str, ""),
    _k("paths.log_path", str, "", control_writable=False, legacy_of="log_path"),
    _k("paths.log_max_bytes", int, 10485760, control_writable=False,
       legacy_of="log_max_bytes"),
    _k("paths.log_routes", bool, True, control_writable=False,
       legacy_of="log_routes"),

    # --- pricing (model-keyed: pricing.<model>.{input_per_1m,output_per_1m}) --
    _k("pricing", dict, {}),

    # --- providers: base URLs, URL tails and KEY-ENV NAMES read as config, the
    # code literal only the DEFAULT (M24-01 base_url, M24-02 chat_path/
    # models_path, M24-03 key_env). A key_env value is an ENV VAR NAME, never a
    # credential — the accessor returns a name, the caller reads the env.
    _k("providers", dict, {}),
    _k("providers.nous.base_url", str, "", legacy_of="base_url"),
    _k("providers.nous.chat_path", str, "/chat/completions"),
    _k("providers.nous.models_path", str, "/models"),
    _k("providers.nous.key_env", str, "NOUS_API_KEY"),
    _k("providers.nous.hosts", list, []),
    _k("providers.openrouter.base_url", str, ""),
    _k("providers.openrouter.chat_path", str, "/chat/completions"),
    _k("providers.openrouter.key_env", str, "OPENROUTER_API_KEY"),
    _k("providers.openrouter.hosts", list, []),
    _k("providers.typesafe.key_env", str, "TYPESAFE_API_KEY"),
    _k("providers.typesafe.hosts", list, []),
    _k("providers.venice.key_env", str, "VENICE_API_KEY"),
    _k("providers.venice.hosts", list, []),
    _k("providers.minimax.key_env", str, "MINIMAX_API_KEY"),
    # M24-04: HOST TABLES — the hostnames that RECOGNISE a provider in text or in
    # an endpoint URL. Declared as an empty list so the example can carry an
    # empty default: unset means "use the code default host list", and the
    # accessor (config_access.provider_hosts) is what turns it into hostnames.
    _k("providers.abliteration.hosts", list, []),

    # --- detection + closure runtime switches (M25-02 GATE 2) ----------------
    # Both were READ and UNDECLARED (test_c's pinned red named exactly these
    # two). They are real reads, so the schema declares them: a declared key
    # gets a typed default, a load-time validation row and example coverage —
    # an undeclared one gets none of that. Types/defaults equal the in-code
    # literals the readers fall back to.
    # DEPRECATED (M41-05, spec §F.1): `unknown_route` was the retired body
    # gate's mode knob. Its reader is deleted and the key has zero hits across
    # /opt/data/profiles/*/config.yaml, so nothing reads it. The schema entry is
    # kept for ONE release so an old config carrying the key still validates
    # instead of failing loudly at load; the next release removes it.
    _k("unknown_route", (str, bool), "route"),
    _k("closure_pass", dict, {}),
    _k("closure_pass.enabled", bool, True),
)

SCHEMA: Tuple[Key, ...] = _V1_KEYS + _GROUPED_KEYS

_BY_PATH = {k.path: k for k in SCHEMA}

# old name -> grouped path, DERIVED from legacy_of (single source of truth;
# a key that does not record its predecessor cannot silently acquire an alias).
ALIASES: Dict[str, str] = {
    old.strip(): k.path
    for k in SCHEMA
    for old in str(k.legacy_of or "").split(",")
    if old.strip()
}

# Block renames: asking for the grouped block name reads it first and falls back
# to the legacy block name while profiles still carry it.
BLOCK_ALIASES: Dict[str, str] = {"classify": "classification",
                                 "anchor": "anchor_chain"}


def key(path: str) -> Any:
    """The SCHEMA Key for a dotted path, or None if unknown."""
    return _BY_PATH.get(str(path))


def canonical_path(path: str) -> str:
    """The GROUPED path for an old spelling (ALIASES), else `path` unchanged.
    An unknown path is returned as-is — get() then reports it. Never raises."""
    try:
        p = str(path)
        return ALIASES.get(p, p)
    except Exception:  # noqa: BLE001
        return str(path)


def legacy_paths(path: str) -> Tuple[str, ...]:
    """The old name(s) the key at `path` replaces — the read fallback for a
    profile still on the pre-M23 spelling. () for a key with no predecessor."""
    try:
        k = _BY_PATH.get(str(path))
        if k is None:
            return ()
        return tuple(old.strip() for old in str(k.legacy_of or "").split(",")
                     if old.strip())
    except Exception:  # noqa: BLE001
        return ()


def group_of(path: str) -> str:
    """The declared group a path belongs to ("" for ungrouped/legacy keys)."""
    head = str(path).split(".", 1)[0]
    return head if head in GROUPS else ""


def keys_in_group(group: str) -> Tuple[Key, ...]:
    """Every declared LEAF key under one of the ten groups. The block container
    itself (`key("pricing")`) is not a leaf and is not returned. Never raises."""
    g = str(group)
    return tuple(k for k in SCHEMA if k.path.startswith(g + "."))
