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
#   persona_card (load_config only, no legacy fallback)
#
# Canonical read order (2026-09-09 consolidation):
#   1. hermes_cli.config.load_config() → section "hermes_router"
#      (legacy "uncensored_router" honored when canonical absent)
#   2. profile-co-located <plugin_root>/../config.yaml (same keys) — rescues
#      profile gateways whose process-level load_config resolves the global
#      home (the 2026-09-07 live-caught failure)
#   3. last-good in-process cache (thread executors may lose the profile
#      context mid-turn)
#
# Caching: mtime-keyed on the co-located yaml (edits land on next read with
# no bounce). load_config() is called fresh each time (Hermes core already
# mtime-caches internally).
#
# Never raises. Returns {} on total miss — every caller already handles {}.
from typing import Any, Dict, Optional

_SECTION_KEYS = ("hermes_router", "uncensored_router")

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
            if isinstance(sec, dict) and sec:
                section = sec
                break
        _cache["path"] = path
        _cache["mtime"] = st.st_mtime
        _cache["section"] = dict(section)
        return dict(section)
    except Exception:  # noqa: BLE001
        return {}


def router_section() -> Dict[str, Any]:
    """The plugin's config section (hermes_router / legacy uncensored_router).
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


def reset_cache() -> None:
    """Test hook — clear the co-located yaml cache."""
    _cache["mtime"] = None
    _cache["path"] = None
    _cache["section"] = None
