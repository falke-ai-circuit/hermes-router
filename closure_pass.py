"""Closure pass: the agent's own second look, informed by the higher self.

The frontier reviews finished work and produces a note (POVs, gates, mindset
delta). This module hands that note BACK TO THE AGENT'S OWN MODEL and asks for
its closing message. Authorship never leaves the agent: the frontier supplies
context, the agent writes the text.

Why a second call rather than a rewrite by the frontier: `fold_revision` asked
the CONSULT to return a corrected body, which makes frontier the author. That
is override, not complement, and it is now disabled structurally.

Fail-open everywhere. The caller always still has the first answer, and anything
short, empty, or malformed here returns None so the original ships.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

#: The second answer must keep at least this share of the first answer's
#: length. A model asked to "revise" will sometimes summarise instead of
#: correcting; a collapse in length is the cheapest reliable tell, and a
#: summarised turn is worse than an unrevised one.
MIN_LEN_RATIO = 0.5

_INSTRUCTION = (
    "You just finished this work and were about to send the answer below.\n\n"
    "A higher-self review of that work is attached as context: it names your "
    "mindstate on two dials (scope, rigour) measured against what this ask "
    "required, plus several vantages on the work.\n\n"
    "Return your CLOSING MESSAGE now.\n"
    "- Revise only if the review names something real. If it does not, return "
    "your answer unchanged, verbatim.\n"
    "- The writing must be yours, in your voice. The review is context, not "
    "text to reproduce.\n"
    "- Never mention, quote, or reference the review or that one exists.\n"
    "- Return the message only, with no preamble."
)


def _config() -> Dict[str, Any]:
    try:
        from hermes_cli.config import load_config  # type: ignore

        cfg = load_config()
        if isinstance(cfg, dict):
            return cfg
    except Exception:  # noqa: BLE001 — fall through to the profile file
        pass
    try:
        import yaml  # type: ignore

        home = (os.environ.get("HERMES_HOME")
                or os.environ.get("HERMES_PROFILE_HOME") or "")
        path = os.path.join(home, "config.yaml") if home else ""
        if path and os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                got = yaml.safe_load(fh)
            if isinstance(got, dict):
                return got
    except Exception:  # noqa: BLE001
        pass
    return {}


def _target() -> Optional[Dict[str, str]]:
    """(base_url, api_key, model) for the agent's own model, or None."""
    cfg = _config()
    model_cfg = cfg.get("model")
    model = ""
    provider = ""
    if isinstance(model_cfg, dict):
        model = str(model_cfg.get("default") or "").strip()
        provider = str(model_cfg.get("provider") or "").strip()
    elif isinstance(model_cfg, str):
        model = model_cfg.strip()
    if not model or not provider:
        return None
    provs = cfg.get("providers")
    if not isinstance(provs, dict):
        return None
    entry = provs.get(provider)
    if not isinstance(entry, dict):
        return None
    base = str(entry.get("base_url") or "").rstrip("/")
    key_env = str(entry.get("key_env") or "")
    key = os.environ.get(key_env, "") if key_env else ""
    if not key:
        try:  # profile dotenv, without importing the platform's loader
            home = (os.environ.get("HERMES_HOME")
                    or os.environ.get("HERMES_PROFILE_HOME") or "")
            for cand in (os.path.join(home, ".env"),
                         os.path.join(home, "..", ".env")):
                if not os.path.isfile(cand):
                    continue
                with open(cand, "r", encoding="utf-8") as fh:
                    for line in fh:
                        if line.strip().startswith(key_env + "="):
                            key = line.split("=", 1)[1].strip().strip('"\'')
                            break
                if key:
                    break
        except Exception:  # noqa: BLE001
            pass
    if not base or not key:
        return None
    # a custom:<name> provider passes the bare model name through
    name = model.split("/", 1)[1] if "/" in model else model
    return {"base": base, "key": key, "model": name}


def revise(ask: str, answer: str, note: str,
           budget_s: float = 25.0) -> Optional[str]:
    """The agent's closing message, revised under the higher-self note.

    Returns None on ANY doubt — no target, no note, network/parse failure, an
    empty or collapsed body. The caller then delivers the original.
    """
    try:
        if not str(answer or "").strip() or not str(note or "").strip():
            return None
        tgt = _target()
        if not tgt:
            return None
        msgs = [
            {"role": "system", "content": _INSTRUCTION},
            {"role": "user", "content": str(ask or "(ask not captured)")[:4000]},
            {"role": "assistant", "content": str(answer)[:12000]},
            {"role": "assistant", "content": str(note)[:4000]},
            {"role": "user", "content": "Return your closing message."},
        ]
        body = json.dumps({
            "model": tgt["model"],
            "messages": msgs,
            "temperature": 0.3,
            "max_tokens": min(4000, max(256, len(answer) // 3)),
        }).encode("utf-8")
        req = urllib.request.Request(
            tgt["base"] + "/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + tgt["key"]})
        with urllib.request.urlopen(req, timeout=float(budget_s)) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if not isinstance(choices, list) or not choices:
            return None
        msg = (choices[0] or {}).get("message") or {}
        out = str(msg.get("content") or "").strip()
        if not out:
            return None
        if len(out) < int(len(str(answer)) * MIN_LEN_RATIO):
            logger.warning(
                "closure_pass: collapsed body rejected (%d < %d)",
                len(out), int(len(str(answer)) * MIN_LEN_RATIO))
            return None
        return out
    except Exception as exc:  # noqa: BLE001 — never break delivery
        logger.warning("closure_pass skipped: %s: %s", type(exc).__name__, exc)
        return None
