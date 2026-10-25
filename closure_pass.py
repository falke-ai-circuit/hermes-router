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
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

#: The second answer must keep at least this share of the first answer's
#: length. A model asked to "revise" will sometimes summarise instead of
#: correcting; a collapse in length is the cheapest reliable tell, and a
#: summarised turn is worse than an unrevised one.
MIN_LEN_RATIO = 0.5

#: Why the last revise() gave up. Every early return sets this, so a skip is
#: never a bare None in the log -- the whole point is that "closure_pass_none"
#: must say WHY.
last_reason = ""

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
    """Whole config through the plugin's OWN reader.

    core.config_access is the plugin's single sanctioned config seam: it uses
    hermes_cli.config when importable and falls back to the resolved path when
    it is not. Reaching for hermes_cli directly here would make this module
    depend on a host install rather than on the plugin's portability layer.
    Never raises.
    """
    # ORDER MATTERS: the plugin's own location is authoritative for WHICH
    # PROFILE this is. load_config() resolves a profile from ambient state, and
    # with none set it happily returns another profile's config -- which would
    # make this pass speak as the wrong agent. Ask the file first, then the
    # host API.
    import os

    candidates = []
    try:
        from .core import config_access

        located = config_access._coLocatedPath()
        if located:
            candidates.append(located)
    except Exception:  # noqa: BLE001
        pass
    for env in ("HERMES_HOME", "HERMES_PROFILE_HOME"):
        home = os.environ.get(env)
        if home:
            candidates.append(os.path.join(home, "config.yaml"))
            candidates.append(os.path.join(os.path.dirname(home),
                                           "config.yaml"))
    for path in candidates:
        try:
            if path and os.path.isfile(path):
                import yaml  # type: ignore

                with open(path, "r", encoding="utf-8") as fh:
                    got = yaml.safe_load(fh)
                if isinstance(got, dict):
                    return got
        except Exception:  # noqa: BLE001
            continue
    try:
        from .core import config_access

        cfg = config_access.load_full_config()
        if isinstance(cfg, dict):
            return cfg
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
    # provider blocks spell this either way; reading only one of them makes
    # the pass silently skip on every install that uses the other.
    key_env = str(entry.get("key_env") or entry.get("api_key_env") or "")
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
    # pass the model id EXACTLY as configured: custom providers expect the
    # full slug ("deepseek/deepseek-v4.1-flash-1017"); stripping the prefix
    # gets a 404 from the endpoint.
    return {"base": base, "key": key, "model": model}


def _call(tgt: Dict[str, str], msgs: list, max_tokens: int,
          budget_s: float) -> Optional[str]:
    """One completion, in whichever dialect this endpoint actually speaks.

    The nous endpoint serves Anthropic-style /v1/messages and 404s on
    /v1/chat/completions, while other Hermes installs point at an
    OpenAI-compatible base. Try OpenAI shape first, fall back to Anthropic --
    the plugin must not assume one provider's dialect.
    """
    headers = {"Content-Type": "application/json",
               "Authorization": "Bearer " + tgt["key"]}
    openai_body = json.dumps({"model": tgt["model"], "messages": msgs,
                              "temperature": 0.3,
                              "max_tokens": max_tokens}).encode("utf-8")
    try:
        req = urllib.request.Request(tgt["base"] + "/chat/completions",
                                     data=openai_body, headers=headers)
        with urllib.request.urlopen(req, timeout=budget_s) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
        return _text_openai(payload)
    except urllib.error.HTTPError as exc:
        if exc.code not in (404, 405):
            raise
    # Anthropic dialect: system is a top-level field, not a message role.
    sys_txt = "\n\n".join(m["content"] for m in msgs
                            if m.get("role") == "system")
    turns = [m for m in msgs if m.get("role") != "system"]
    anth_body = json.dumps({"model": tgt["model"], "system": sys_txt,
                            "messages": turns,
                            "temperature": 0.3,
                            "max_tokens": max_tokens}).encode("utf-8")
    req = urllib.request.Request(tgt["base"] + "/messages", data=anth_body,
                                 headers=headers)
    with urllib.request.urlopen(req, timeout=budget_s) as resp:
        payload = json.loads(resp.read().decode("utf-8", "replace"))
    return _text_anthropic(payload)


def _text_openai(payload: Any) -> Optional[str]:
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices:
        return None
    msg = (choices[0] or {}).get("message") or {}
    return str(msg.get("content") or "")


def _text_anthropic(payload: Any) -> Optional[str]:
    blocks = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(blocks, list):
        return None
    # text blocks only: 'thinking' blocks are reasoning, not the message.
    parts = [str(b.get("text") or "") for b in blocks
             if isinstance(b, dict) and b.get("type") == "text"]
    return "".join(parts)


def revise(ask: str, answer: str, note: str,
           budget_s: float = 25.0) -> Optional[str]:
    """The agent's closing message, revised under the higher-self note.

    Returns None on ANY doubt — no target, no note, network/parse failure, an
    empty or collapsed body. The caller then delivers the original.
    """
    try:
        global last_reason
        last_reason = ""
        if not str(answer or "").strip():
            last_reason = "empty_answer"
            return None
        if not str(note or "").strip():
            last_reason = "empty_note"
            return None
        tgt = _target()
        if not tgt:
            last_reason = "no_target"
            return None
        msgs = [
            {"role": "system", "content": _INSTRUCTION},
            {"role": "user", "content": str(ask or "(ask not captured)")[:4000]},
            {"role": "assistant", "content": str(answer)[:12000]},
            {"role": "assistant", "content": str(note)[:4000]},
            {"role": "user", "content": "Return your closing message."},
        ]
        # A thinking model spends output tokens on a reasoning block BEFORE
        # emitting text. A small budget yields a valid response with a
        # 'thinking' block and NO text block -- which reads as an empty
        # completion and silently skips the closure. Budget for both.
        mt = min(8000, max(1500, len(answer) // 2 + 400))
        out = _call(tgt, msgs, mt, float(budget_s))
        if out is None:
            last_reason = last_reason or "empty_completion"
            return None
        out = out.strip()
        if not out:
            last_reason = "empty_completion"
            return None
        if len(out) < int(len(str(answer)) * MIN_LEN_RATIO):
            last_reason = "collapsed"
            logger.warning(
                "closure_pass: collapsed body rejected (%d < %d)",
                len(out), int(len(str(answer)) * MIN_LEN_RATIO))
            return None
        return out
    except Exception as exc:  # noqa: BLE001 — never break delivery
        last_reason = "error:%s" % type(exc).__name__
        logger.warning("closure_pass skipped: %s: %s", type(exc).__name__, exc)
        return None
