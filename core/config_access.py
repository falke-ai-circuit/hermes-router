# config_access.py — SINGLE config accessor for hermes_router (v3.8 step 2).
#
# Replaces the 6+ independent config readers that had drifted:
#   __init__._cfg (no co-located fallback, no cache)
#   debug_banner._banner_section (fallback + cache, but a dead code path bug:
#       the `global`/cache-update block was unreachable)
#   provenance_footer._config_section (fallback + .update() cache that MERGES
#       stale keys across profiles)
#   decision_head._decision_head_cfg (load_config only — blind to profile
#       co-located config; the 2026-09-07 L0-pinning bug class)
#   anchor_chain (load_config only for section + providers)
#   persona_card (load_config only, no fallback)
#
# Canonical read order (2026-09-09 consolidation):
#   1. hermes_cli.config.load_config() → section "hermes_router"
#   2. profile-co-located <plugin_root>/../config.yaml (same keys) — rescues
#      profile gateways whose process-level load_config resolves the global
#      home (the 2026-09-07 live-caught failure)
#   3. last-good in-process cache (thread executors may lose the profile
#      context mid-turn)
#
# Legacy-section note (P0.5, 2026-10-07): the legacy config section and the
# R10-6 key-merge fallback are DELETED. The legacy sections were purged from
# every profile config fleet-wide (parity-proven merge into the canonical
# section; pre-purge backups at /opt/data/tmp/backup_*_config_pre_purge.yaml),
# so the fallback had no reachable input. The canonical-first read order and
# the 3-tier resolution + cache semantics are unchanged.
#
# Caching: mtime-keyed on the co-located yaml (edits land on next read with
# no bounce). load_config() is called fresh each time (Hermes core already
# mtime-caches internally).
#
# Never raises. Returns {} on total miss — every caller already handles {}.
from typing import Any, Dict, Optional

_SECTION_KEYS = ("hermes_router",)

_cache: Dict[str, Any] = {"mtime": None, "path": None, "section": None}


def _coLocatedPath() -> Optional[str]:
    """config.yaml co-located with this plugin instance, or None. Never raises."""
    try:
        import os

        here = os.path.dirname(os.path.abspath(__file__))
        # <profile>/plugins/hermes_router/config_access.py → <profile>/config.yaml
        profile_root = os.path.dirname(os.path.dirname(here))
        return os.path.join(profile_root, "config.yaml")
    except Exception:  # noqa: BLE001
        return None


def _read_yaml(path: str) -> Dict[str, Any]:
    """Parse a yaml config file, return the router section or {}. Never raises.
    mtime-keyed cache: edits land on the next read with no bounce."""
    try:
        import os
        import yaml

        st = os.stat(path)
        if (_cache["path"] == path and _cache["mtime"] == st.st_mtime
                and isinstance(_cache["section"], dict) and _cache["section"]):
            return dict(_cache["section"])
        with open(path, "r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        section: Dict[str, Any] = {}
        for key in _SECTION_KEYS:
            sec = cfg.get(key)
            if isinstance(sec, dict) and sec and not section:
                section = sec
        _cache["path"] = path
        _cache["mtime"] = st.st_mtime
        _cache["section"] = dict(section)
        return dict(section)
    except Exception:  # noqa: BLE001
        return {}



def router_section() -> Dict[str, Any]:
    """The plugin's config section ("hermes_router").
    Single canonical reader for the whole plugin. Never raises."""
    # 1. process-level config (Hermes core, mtime-cached internally)
    section: Optional[Dict[str, Any]] = None
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        if isinstance(cfg, dict):
            for key in _SECTION_KEYS:
                sec = cfg.get(key)
                if isinstance(sec, dict) and sec:
                    section = sec
                    break
    except Exception:  # noqa: BLE001
        section = None
    if isinstance(section, dict) and section:
        _cache["section"] = dict(section)
        _cache["path"] = None  # process-level read wins; drop stale yaml key
        return dict(section)
    # 2. profile-co-located yaml (mtime-keyed cache; fresh read on edit)
    p = _coLocatedPath()
    if p:
        sec2 = _read_yaml(p)
        if sec2:
            return sec2
    # 3. last-good cache (thread executors, transient read failures)
    if isinstance(_cache["section"], dict) and _cache["section"]:
        return dict(_cache["section"])
    return {}


def sub_block(name: str) -> Dict[str, Any]:
    """A named sub-block of the router section (complexity, decision_head,
    anchor_chain, ...). {} on miss. Never raises."""
    try:
        block = router_section().get(name)
        return dict(block) if isinstance(block, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def agent_model() -> str:
    """R19.17 ADDENDUM: the AGENT's current model, resolved at call time
    from the profile's top-level Hermes config (model.default / model) —
    the same model the agent itself runs on. NEVER a cached constant: each
    call re-reads the config (mtime-cheap) so future model changes
    propagate automatically with no stale-literal rot (the exact bug this
    kills: 12 configs carried dead meituan/longcat-2.0:free for weeks).
    Never raises; "" on any miss (callers fail-open)."""
    try:
        import os
        import yaml

        path = _coLocatedPath()
        if not path or not os.path.exists(path):
            return ""
        with open(path, "r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        m = cfg.get("model")
        if isinstance(m, dict):
            return str(m.get("default") or m.get("model") or "").strip()
        if isinstance(m, str):
            return m.strip()
        return ""
    except Exception:  # noqa: BLE001 — fail-open empty
        return ""


def sub_block_alias(*names: str) -> Dict[str, Any]:
    """R19.13 reflex modularization: FIRST non-empty named sub-block wins —
    used so hermes_router.reflex may alias the legacy decision block
    (reflex first, decision fallback; no config migration required of
    existing profiles). {} on miss. Never raises."""
    try:
        for name in names:
            block = sub_block(name)
            if isinstance(block, dict) and block:
                return block
        return {}
    except Exception:  # noqa: BLE001
        return {}


def providers_custom() -> Dict[str, Any]:
    """Top-level providers.custom block (NOT under hermes_router — Hermes
    core providers, read from process config only; no co-located fallback
    because provider credentials are fleet-level). Never raises."""
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        providers = (cfg or {}).get("providers") if isinstance(cfg, dict) else None
        custom = (providers or {}).get("custom") if isinstance(providers, dict) else None
        return custom if isinstance(custom, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


# -----------------------------------------------------------------------
# Typed get() + load-time validation (P4, proposal §2.3)
# -----------------------------------------------------------------------

_MISSING = object()


def _walk(section: Dict[str, Any], path: str) -> Any:
    """Walk a dotted path through the router section. Returns _MISSING when
    any segment is absent/non-dict (leaf values may legitimately be None)."""
    parts = str(path).split(".")
    node: Any = section
    for part in parts:
        if not isinstance(node, dict):
            return _MISSING
        node = node.get(part, _MISSING)
        if node is _MISSING:
            return _MISSING
    return node


def _type_ok(value: Any, expected: Any) -> bool:
    if expected is bool:
        return isinstance(value, bool)
    if expected is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if expected is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, expected)


def get(path: str, default: Any = None) -> Any:
    """Typed config read — THE read primitive (proposal §2.3).

    Validates `path` against core/schema.SCHEMA. On missing key: emits a
    `config_key_missing` telemetry row and returns the SCHEMA typed default
    (never silently None). On type mismatch: emits a `config_key_type_mismatch`
    row and returns the typed default. On explicit None with nullable=True:
    returns None (no row). Never raises.
    """
    try:
        from . import telemetry

        k = telemetry_key(path)
        if k is None:
            telemetry.log_route("config_key_unknown", path=path)
            return default
        value = _walk(router_section(), path)
        if value is _MISSING:
            telemetry.log_route("config_key_missing", path=path)
            return default if default is not None else k.default
        if value is None and k.nullable:
            return None
        if not _type_ok(value, k.type):
            telemetry.log_route("config_key_type_mismatch", path=path,
                                type=_type_name(value), expected=k.type.__name__)
            return default if default is not None else k.default
        return value
    except Exception:  # noqa: BLE001 — config read must never raise
        return default


def telemetry_key(path: str) -> Any:
    """SCHEMA key lookup — split out so callers/tests can probe the
    inventory without triggering a read. Never raises."""
    try:
        from . import schema

        return schema.key(path)
    except Exception:  # noqa: BLE001
        return None


def _type_name(value: Any) -> str:
    try:
        return type(value).__name__
    except Exception:  # noqa: BLE001
        return "?"


def validate_config() -> int:
    """LOAD-TIME VALIDATION pass (proposal §2.3 best-practice adjustment):
    validate the whole resolved section once at plugin load — one telemetry
    row per schema violation (missing key / type mismatch), so a drifted
    config surfaces as a log row at boot instead of a silent None mid-turn.
    Returns the violation count. Never raises."""
    violations = 0
    try:
        from . import schema
        from . import telemetry

        section = router_section()
        if not isinstance(section, dict):
            return 0
        for k in schema.SCHEMA:
            try:
                value = _walk(section, k.path)
                if value is _MISSING:
                    # A wholly-absent parent block is a profile that simply
                    # hasn't tuned that section — NOT a violation (every
                    # missing key still gets its typed default via get()).
                    # A PARTIAL block (parent present, leaf missing) IS a
                    # violation — the operator set the block and dropped a
                    # key it expects.
                    parent = _walk(section, k.path.rsplit(".", 1)[0]) \
                        if "." in k.path else _MISSING
                    if "." not in k.path or parent is not _MISSING:
                        telemetry.log_route("config_key_missing", path=k.path,
                                            at="load_validation")
                        violations += 1
                    continue
                if value is None and k.nullable:
                    continue
                if not _type_ok(value, k.type):
                    telemetry.log_route("config_key_type_mismatch", path=k.path,
                                        type=_type_name(value),
                                        expected=k.type.__name__,
                                        at="load_validation")
                    violations += 1
            except Exception:  # noqa: BLE001 — per-key isolation
                violations += 1
        return violations
    except Exception:  # noqa: BLE001
        return violations


def reset_cache() -> None:
    """Test hook — clear the co-located yaml cache."""
    _cache["mtime"] = None
    _cache["path"] = None
    _cache["section"] = None
