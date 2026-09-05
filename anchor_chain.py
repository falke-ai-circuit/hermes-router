"""Anchor chain resolution + daily cap guard for the hermes-router complexity
lane (v3.0.0).

ANCHOR CHAIN (config, per-deployment — ZERO endpoint knowledge in code):

  anchor_chain:
    primary: openrouter://anthropic/claude-fable-5.1
    judge: openrouter://openai/o4-mini        # verification/consult tier
    overflow: pass_through                     # fail/over-cap -> flash + route_skipped log

URL scheme resolution:
  openrouter://<model> -> base https://openrouter.ai/api/v1, key from
  OPENROUTER_API_KEY env. Generic scheme passthrough: any <scheme>://<model>
  resolvable via existing custom: providers in config (providers.<scheme>
  block with base_url + key_env). Unresolvable scheme -> fail-open
  pass-through (route_skipped, reason=unresolvable_scheme).

DAILY CAP GUARD (non-tunable floor):
  Cost estimated per anchored call from the config price table
  (anchor_chain.pricing: {<model_id>: {input_per_1m, output_per_1m}}).
  Daily spend persists in state (date-keyed, under profile hermes home).
  At/over cap -> overflow behavior + cap_blocked log. Default cap $2/day,
  config-settable UP only via router_control (never silently).

Fail-open everywhere: resolution/cost/state failures degrade to pass-through
and never raise into middleware paths.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
OPENROUTER_KEY_ENV = "OPENROUTER_API_KEY"
DEFAULT_DAILY_CAP_USD = 2.0

_ROLES = ("primary", "judge")


# ---------------------------------------------------------------------------
# Resolved anchor endpoint
# ---------------------------------------------------------------------------


@dataclass
class AnchorEndpoint:
    """A resolved anchor target: what an llm_execution middleware needs to
    perform the anchored call WITHOUT persisting anything into the agent's
    provider configuration."""

    scheme: str
    model: str
    base_url: str
    api_key_env: str
    role: str  # primary | judge

    def masked(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "scheme": self.scheme,
            "model": self.model,
            "base_url": self.base_url,
            "key_env": self.api_key_env,
        }


@dataclass
class AnchorChainCfg:
    primary: Optional[AnchorEndpoint]
    judge: Optional[AnchorEndpoint]
    overflow: str = "pass_through"
    daily_cap_usd: float = DEFAULT_DAILY_CAP_USD
    pricing: Dict[str, Dict[str, float]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.pricing is None:
            self.pricing = {}

    def endpoint_for(self, role: str) -> Optional[AnchorEndpoint]:
        if role == "judge":
            return self.judge or self.primary
        return self.primary


def _custom_providers() -> Dict[str, Dict[str, Any]]:
    """Read providers: {custom: {<name>: {base_url, key_env?, api_key?}}} from
    config — the existing Hermes custom-provider block. {} on any failure."""
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        providers = (cfg or {}).get("providers") if isinstance(cfg, dict) else None
        custom = (providers or {}).get("custom") if isinstance(providers, dict) else None
        return custom if isinstance(custom, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _resolve_scheme(scheme: str, model: str, role: str) -> Optional[AnchorEndpoint]:
    """Resolve <scheme>://<model> to (base_url, key_env). Built-in schemes:
    openrouter. Generic passthrough: custom providers in config under the
    scheme name. None when unresolvable."""
    try:
        scheme = (scheme or "").strip().lower()
        if scheme == "openrouter":
            return AnchorEndpoint(
                scheme="openrouter", model=model, base_url=OPENROUTER_BASE,
                api_key_env=OPENROUTER_KEY_ENV, role=role,
            )
        custom = _custom_providers().get(scheme)
        if isinstance(custom, dict) and custom.get("base_url"):
            key_env = str(custom.get("key_env") or "").strip()
            return AnchorEndpoint(
                scheme=scheme, model=model,
                base_url=str(custom.get("base_url")).rstrip("/"),
                api_key_env=key_env, role=role,
            )
        return None
    except Exception:  # noqa: BLE001
        return None


def parse_anchor_uri(uri: str, role: str) -> Optional[AnchorEndpoint]:
    """Parse '<scheme>://<model>' into an AnchorEndpoint. None on parse/resolution
    failure (fail-open)."""
    try:
        if not isinstance(uri, str) or "://" not in uri:
            return None
        scheme, _, model = uri.partition("://")
        scheme = scheme.strip()
        model = model.strip()
        if not scheme or not model:
            return None
        return _resolve_scheme(scheme, model, role)
    except Exception:  # noqa: BLE001
        return None


def load_anchor_chain() -> AnchorChainCfg:
    """Read anchor_chain from the plugin config section (hermes_router first,
    legacy uncensored_router fallback — same rule as __init__._cfg). Never
    raises; missing/empty blocks give an empty chain (lane inert)."""
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        section = None
        if isinstance(cfg, dict):
            section = cfg.get("hermes_router")
            if not (isinstance(section, dict) and section):
                section = cfg.get("uncensored_router")
        block = (section or {}).get("anchor_chain") if isinstance(section, dict) else None
        if not isinstance(block, dict):
            return AnchorChainCfg(None, None)
        cap = block.get("daily_cap_usd", DEFAULT_DAILY_CAP_USD)
        try:
            cap = float(cap)
        except (TypeError, ValueError):
            cap = DEFAULT_DAILY_CAP_USD
        pricing = block.get("pricing")
        return AnchorChainCfg(
            primary=parse_anchor_uri(str(block.get("primary") or ""), "primary"),
            judge=parse_anchor_uri(str(block.get("judge") or ""), "judge"),
            overflow=str(block.get("overflow") or "pass_through").strip().lower(),
            daily_cap_usd=max(0.0, cap),
            pricing=pricing if isinstance(pricing, dict) else {},
        )
    except Exception:  # noqa: BLE001
        return AnchorChainCfg(None, None)


# ---------------------------------------------------------------------------
# Daily cap ledger (date-keyed persistence under profile hermes home)
# ---------------------------------------------------------------------------

_LEDGER_LOCK = threading.Lock()
_LEDGER_FILENAME = "hermes-router-spend.json"
# Test seam: tests point this at a tmp file; default resolves per profile home.
_LEDGER_PATH_OVERRIDE: Optional[str] = None


def _ledger_path() -> str:
    if _LEDGER_PATH_OVERRIDE:
        return _LEDGER_PATH_OVERRIDE
    try:
        import hermes_constants

        home = str(hermes_constants.get_hermes_home())
    except Exception:  # noqa: BLE001
        home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    return os.path.join(home, _LEDGER_FILENAME)


def _today_utc() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_ledger(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — corrupt/missing ledger = zero spend
        return {}


def _write_ledger(path: str, data: Dict[str, Any]) -> None:
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception as exc:  # noqa: BLE001 — persistence must never break routing
        logger.debug("spend ledger write failed: %s", exc)


def today_spend() -> float:
    """Persisted spend for today (UTC date key). Never raises."""
    try:
        with _LEDGER_LOCK:
            data = _load_ledger(_ledger_path())
            rec = data.get(_today_utc())
            if isinstance(rec, dict):
                return float(rec.get("spend_usd", 0.0) or 0.0)
        return 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def record_spend(amount_usd: float) -> float:
    """Add cost to today's ledger entry, return the new daily total.
    Never raises."""
    try:
        amt = max(0.0, float(amount_usd or 0.0))
        if amt <= 0.0:
            return today_spend()
        with _LEDGER_LOCK:
            path = _ledger_path()
            data = _load_ledger(path)
            key = _today_utc()
            rec = data.get(key)
            new = (float(rec.get("spend_usd", 0.0) or 0.0) if isinstance(rec, dict) else 0.0) + amt
            data[key] = {"spend_usd": round(new, 6), "updated_at": time.time()}
            # Prune stale days (keep 7) so the file stays tiny.
            try:
                keys = sorted(k for k in data if isinstance(k, str) and len(k) == 10 and k[4] == "-")
                for k in keys[:-7]:
                    data.pop(k, None)
            except Exception:  # noqa: BLE001
                pass
            _write_ledger(path, data)
            return new
    except Exception:  # noqa: BLE001
        return today_spend()


def estimate_call_cost(endpoint: AnchorEndpoint, est_input_tokens: int,
                       est_output_tokens: int, pricing: Dict[str, Dict[str, float]]) -> float:
    """Estimate one anchored call's cost from the config price table.
    Pricing shape: {<model_id>: {input_per_1m: x, output_per_1m: y}}.
    Unknown model -> conservative default (0.5 + 1.5 USD per 1M) so an
    unpriced anchor still gets capped, never silently free. Never raises."""
    try:
        prices = pricing.get(endpoint.model) if isinstance(pricing, dict) else None
        if not isinstance(prices, dict):
            prices = {"input_per_1m": 0.5, "output_per_1m": 1.5}
        inp = float(prices.get("input_per_1m", 0.5) or 0.0)
        out = float(prices.get("output_per_1m", 1.5) or 0.0)
        cost = (est_input_tokens / 1_000_000.0) * inp
        cost += (est_output_tokens / 1_000_000.0) * out
        return round(max(0.0, cost), 6)
    except Exception:  # noqa: BLE001
        return 0.0


def cap_check(chain: AnchorChainCfg, next_call_cost: float) -> Tuple[bool, float, float]:
    """Return (allowed, spend_now, projected). allowed=False when today's spend
    plus the next call cost would exceed the daily cap. Never raises."""
    try:
        spend = today_spend()
        projected = spend + max(0.0, float(next_call_cost or 0.0))
        return (projected <= chain.daily_cap_usd, spend, projected)
    except Exception:  # noqa: BLE001
        return True, 0.0, 0.0


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _test_reset(ledger_path: Optional[str] = None) -> None:
    """Tests-only: point the ledger at a fresh file (or reset the override)."""
    global _LEDGER_PATH_OVERRIDE
    _LEDGER_PATH_OVERRIDE = ledger_path