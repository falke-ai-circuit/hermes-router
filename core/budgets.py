# core/budgets.py — central max_tokens profiles (proposal §2.4, P4).
#
# Kills class 3 (token starvation): the per-call `"max_tokens": <literal>`
# entries scattered across decision.py / completion_audit.py become named
# profiles here, with a config overlay (`budgets.<profile>.max_tokens`).
#
# BEHAVIOR-NEUTRAL per Binding 1 rider: defaults are EXACTLY the literals
# being deleted — the consult/verdict 512→4096 raise is OUT of this
# restructure and ships later as a config write, not a code change.
from typing import Any, Dict, Mapping

from .schema import key as _schema_key

# Defaults EXACTLY equal current behavior, ZERO drift.
PROFILES: Dict[str, Dict[str, Any]] = {
    "consult":          {"max_tokens": 512},     # decision.py score() literal (UNCHANGED)
    "verdict":          {"max_tokens": 512},     # decision.py _call_jev/call_backend literal (UNCHANGED)
    "completion_audit": {"max_tokens": 12000},   # completion_audit.py literals (UNCHANGED)
    "render":           {"max_tokens": 2048},
    "probe":            {"max_tokens": 256},
}

# The heuristic threshold is_probe() replaces (anchor_exec.py <500 probe).
PROBE_MAX_TOKENS = 500


def budget(profile: str) -> Mapping[str, Any]:
    """The max_tokens mapping for a named budget profile.

    Config overlay: `budgets.<profile>.max_tokens` overrides the default
    when set (control-writable via /router config). Unknown profile or
    schema-missing overlay falls back to PROFILES — never raises.
    """
    path = "budgets.%s.max_tokens" % str(profile)
    try:
        k = _schema_key(path)
        if k is None:
            return dict(PROFILES.get(profile, {"max_tokens": 512}))
        from . import config_access

        value = config_access.get(path)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return {"max_tokens": value}
        return dict(PROFILES.get(profile, {"max_tokens": k.default}))
    except Exception:  # noqa: BLE001 — budget read must never break a call
        return dict(PROFILES.get(profile, {"max_tokens": 512}))


def is_probe(max_tokens: Any) -> bool:
    """One predicate replacing anchor_exec.py:269-285's <500 heuristic:
    True only for tiny-payload probes (max_tokens < PROBE_MAX_TOKENS)
    where reasoning_effort must NOT be injected (valmet live catch)."""
    return isinstance(max_tokens, int) and not isinstance(max_tokens, bool) \
        and max_tokens < PROBE_MAX_TOKENS
