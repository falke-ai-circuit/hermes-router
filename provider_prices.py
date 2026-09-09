"""Provider-sourced pricing (Goran 09-09 ruling: cost comes FROM THE
PROVIDER, never a static estimate table).

Fetches per-model pricing from each uncensored-chain provider's /v1/models
endpoint (both abliteration.ai and venice.ai publish pricing per model there),
caches on disk under the profile hermes home, refreshes at most once per
PRICING_TTL_S. Fail-open: fetch errors leave the cache as-is; unknown model
returns None so the ledger records tokens but no fabricated price.

Pricing shapes normalized to {"input_per_1m": float, "output_per_1m": float}:
  - venice.ai:    model.model_spec.pricing.{input,output}.usd  (per 1M)
  - abliteration: model.pricing.{prompt,completion}           (per token)
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any, Dict, Optional

PRICING_TTL_S = 12 * 3600
_CACHE_FILENAME = "hermes-router-provider-prices.json"
_FETCH_TIMEOUT_S = 15


def _cache_path() -> str:
    try:
        home = os.environ.get("HERMES_HOME") or os.path.expanduser("~")
        return os.path.join(str(home), _CACHE_FILENAME)
    except Exception:  # noqa: BLE001
        return ""


def _load_cache() -> Dict[str, Any]:
    p = _cache_path()
    if not p or not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return {}
        fetched = float(data.get("fetched_at") or 0.0)
        if time.time() - fetched > PRICING_TTL_S:
            return {}
        prices = data.get("prices")
        return prices if isinstance(prices, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _write_cache(prices: Dict[str, Any]) -> None:
    p = _cache_path()
    if not p:
        return
    try:
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"fetched_at": time.time(), "prices": prices}, fh)
        os.replace(tmp, p)
        try:
            os.chmod(p, 0o600)
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass


def _chain_models_urls() -> Dict[str, str]:
    """model name -> /models base url, from hermes_router.chain entries."""
    out: Dict[str, str] = {}
    try:
        try:
            from .config_access import router_section

            chain = router_section().get("chain")
        except Exception:  # noqa: BLE001
            chain = None
        if not isinstance(chain, list):
            return out
        for e in chain:
            if not isinstance(e, dict):
                continue
            model = str(e.get("model") or "")
            chat_url = str(e.get("url") or "")
            if model and chat_url:
                out[model] = chat_url.rsplit("/chat/completions", 1)[0] + "/models"
    except Exception:  # noqa: BLE001
        pass
    return out


def _fetch(url: str, key_file: str, key_env: str) -> Optional[Dict[str, Any]]:
    """GET the /models endpoint. curl only (urllib 403s in some sandboxes)."""
    try:
        api_key = ""
        if key_env:
            api_key = os.environ.get(key_env, "")
        if not api_key and key_file:
            kf = os.path.expanduser(key_file)
            if os.path.exists(kf):
                with open(kf, "r", encoding="utf-8") as fh:
                    api_key = fh.read().strip()
        cmd = ["curl", "-s", "-m", str(_FETCH_TIMEOUT_S),
               "-H", "Authorization: Bearer %s" % api_key, url]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=_FETCH_TIMEOUT_S + 5)
        if r.returncode != 0 or not r.stdout:
            return None
        return json.loads(r.stdout)
    except Exception:  # noqa: BLE001
        return None


def _normalize(model: str, data: Dict[str, Any]) -> Optional[Dict[str, float]]:
    try:
        for m in data.get("data", []):
            if not isinstance(m, dict) or str(m.get("id")) != model:
                continue
            # venice shape
            spec = m.get("model_spec") or {}
            vp = spec.get("pricing") if isinstance(spec, dict) else None
            if isinstance(vp, dict):
                pin = vp.get("input") or {}
                pout = vp.get("output") or {}
                if isinstance(pin, dict) and isinstance(pout, dict):
                    i = pin.get("usd")
                    o = pout.get("usd")
                    if isinstance(i, (int, float)) and isinstance(o, (int, float)):
                        return {"input_per_1m": float(i), "output_per_1m": float(o)}
            # abliteration shape (per-token USD)
            ap = m.get("pricing")
            if isinstance(ap, dict):
                ip = ap.get("prompt")
                cp = ap.get("completion")
                if isinstance(ip, (int, float, str)) and isinstance(cp, (int, float, str)):
                    return {"input_per_1m": float(ip) * 1_000_000.0,
                            "output_per_1m": float(cp) * 1_000_000.0}
    except Exception:  # noqa: BLE001
        pass
    return None


def provider_price_for(model: str) -> Optional[Dict[str, float]]:
    """Public: prices for a chain model, fetched from its provider (cached).
    None when unknown/fetch-failed — caller records no price."""
    try:
        cached = _load_cache()
        if model in cached:
            p = cached[model]
            if isinstance(p, dict):
                return p
        urls = _chain_models_urls()
        if model not in urls:
            return None
        # find matching chain entry key info
        try:
            from .config_access import router_section

            chain = router_section().get("chain") or []
        except Exception:  # noqa: BLE001
            chain = []
        kf = ke = ""
        for e in chain:
            if isinstance(e, dict) and str(e.get("model")) == model:
                kf = str(e.get("key_file") or "")
                ke = str(e.get("key_env") or "")
                break
        data = _fetch(urls[model], kf, ke)
        if not isinstance(data, dict):
            return None
        norm = _normalize(model, data)
        if norm:
            fresh = {k: v for k, v in _load_cache().items() if isinstance(v, dict)}
            fresh[model] = norm
            _write_cache(fresh)
        return norm
    except Exception:  # noqa: BLE001
        return None
