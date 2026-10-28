"""Weighted reflex — the decision substrate EXTRACTS forks and weights them.

Why this exists
---------------
Detector-first fork finding was the component the measurements convicted: only 14% of fork
rows carried a real option set, 69% of coder ``pre_fork`` rows were frontier accounting
written into the decision ledger, and prose forks were structurally unresolvable
(unique-target rate 3/385 conductor, 15/282 coder, 1/26 reviewer). A regex that fails to
name a fork fails LOUDLY — it bills a verdict on something that was not a fork.

Extraction inverts that. A model reading the transcript is asked which choice is actually in
front of the agent, and it fails SOFT: a fork it misses is simply absent. It also returns a
distribution instead of a label, because the MARGIN is the information that makes a decision
cheap — 0.55/0.45 means "coin flip, verify first", 0.90/0.10 means "take it and move". A
binary ``opt-1`` cannot say either, which is why the lane kept emitting confident noise.

The block is a *reading*, never an instruction: appended to the request payload only, never
persisted as a message. A persisted note gets restated instead of acted on — the echo guard
already proved that (``advisory_echo_blocked ... reason=no_work_signal``).

Everything here is pure and total: no network, no config writes, no exception escapes.
The caller supplies the model call; this module guarantees the contract.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional

HEADER = "[reflex: read-only reading of the fork in front of you]"
MAX_FORKS = 1
MAX_OPTIONS = 3
MAX_QUESTION = 90
MAX_ACTION = 90
MAX_BLOCK_CHARS = 400
MIN_CONFIDENCE = 0.5
SUM_TOLERANCE = 0.02
DRIVES = ("scope", "rigour")

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.M)
_PCT = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*%\s*$")

# Diagnostics for the last parse/render: what happened and why. Read by the fire path so a
# silent drop is never mistaken for "there was no fork".
LAST: Dict[str, Any] = {}


def _note(**kw: Any) -> None:
    """Observability that cannot itself raise."""
    try:
        LAST.clear()
        LAST.update(kw)
    except Exception:  # noqa: BLE001
        pass


def cfg_for(cfg: Any) -> Dict[str, Any]:
    """Read the ``reflex_weights`` block. Absent block == disabled.

    Default OFF is deliberate: this arms per profile after measurement, never fleet-wide by
    a plugin upgrade.
    """
    out: Dict[str, Any] = {"enabled": False, "max_forks": MAX_FORKS,
                           "max_options": MAX_OPTIONS,
                           "min_confidence": MIN_CONFIDENCE,
                           "block_chars": MAX_BLOCK_CHARS}
    try:
        root = cfg if isinstance(cfg, dict) else {}
        for key in ("hermes_router", "uncensored_router"):
            sec = root.get(key)
            if not isinstance(sec, dict):
                continue
            block = sec.get("reflex_weights")
            if not isinstance(block, dict):
                continue
            if block.get("enabled"):
                out["enabled"] = True
            for k in ("max_forks", "max_options", "block_chars"):
                v = block.get(k)
                if isinstance(v, int) and not isinstance(v, bool) and v > 0:
                    out[k] = v
            mc = block.get("min_confidence")
            if isinstance(mc, (int, float)) and not isinstance(mc, bool):
                out["min_confidence"] = max(0.0, min(1.0, float(mc)))
            # R72: this whitelist silently dropped every key it did not name, so a profile
            # that configured min_top / min_margin / every_call / timeout was quietly run
            # on the module defaults. Config that does not arrive is worse than no config,
            # because the operator believes a knob is set.
            for k, lo, hi in (("min_top", 0.0, 1.0), ("min_margin", 0.0, 1.0)):
                v = block.get(k)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    out[k] = max(lo, min(hi, float(v)))
            for k in ("every_call", "agree_silent", "integrate"):
                v = block.get(k)
                if isinstance(v, bool):
                    out[k] = v
            ts = block.get("reflex_timeout_seconds")
            if isinstance(ts, (int, float)) and not isinstance(ts, bool) and ts > 0:
                out["reflex_timeout_seconds"] = float(ts)
            rg = block.get("register")
            if isinstance(rg, str) and rg.lower() in ("frame", "instinct"):
                out["register"] = rg.lower()
            sh = block.get("shape")
            if isinstance(sh, str) and sh.lower() in ("user", "system", "evidence"):
                out["shape"] = sh.lower()
            break
    except Exception:  # noqa: BLE001
        pass
    return out
