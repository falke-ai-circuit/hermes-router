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


def _as_prob(v: Any) -> Optional[float]:
    """'0.62' | '62%' | 0.62 -> 0.62. Anything else -> None (never guess)."""
    try:
        if isinstance(v, bool) or v is None:
            return None
        if isinstance(v, str):
            m = _PCT.match(v)
            if m:
                return max(0.0, min(1.0, float(m.group(1)) / 100.0))
        p = float(v)
    except (TypeError, ValueError):
        return None
    if p < 0.0 or p > 1.0:
        return None
    return p


def _clip(text: Any, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit].rstrip()


def parse(raw: Any, cfg: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Validate a model answer into a renderable reading, or None.

    Drops rather than repairs. Each rule has a measured failure behind it:
      * fewer than two options         -> the 62-97%-of-consulted-rows defect
      * probabilities not summing to 1 -> unnormalized decoration
      * confidence below the floor      -> the noise-band verdict (opt-1 ~0.32)
    """
    c = cfg if isinstance(cfg, dict) and cfg else cfg_for({})
    try:
        if isinstance(raw, str):
            txt = _FENCE.sub("", raw).strip()
            if not txt:
                _note(reason="empty")
                return None
            start, end = txt.find("{"), txt.rfind("}")
            if start < 0 or end <= start:
                _note(reason="no_json")
                return None
            raw = json.loads(txt[start:end + 1])
        if not isinstance(raw, dict):
            _note(reason="not_an_object")
            return None

        conf = raw.get("confidence")
        conf = 0.0 if conf is None else _as_prob(conf)
        if conf is None:
            _note(reason="bad_confidence")
            return None
        if conf < float(c["min_confidence"]):
            _note(reason="low_confidence", confidence=conf)
            return None

        forks = raw.get("forks")
        forks = forks if isinstance(forks, list) else []
        chosen: Optional[Dict[str, Any]] = None
        for f in forks[:int(c["max_forks"])]:
            if not isinstance(f, dict):
                continue
            opts = [o for o in (f.get("options") or []) if isinstance(o, dict)]
            if len(opts) < 2:
                continue
            probs: List[float] = []
            ok = True
            for o in opts[:int(c["max_options"])]:
                p = _as_prob(o.get("p", o.get("probability", o.get("weight"))))
                if p is None:
                    ok = False
                    break
                probs.append(p)
            if not ok or len(probs) < 2:
                continue
            total = sum(probs)
            if abs(total - 1.0) > SUM_TOLERANCE:
                _note(reason="unnormalized", total=round(total, 4))
                continue
            pairs = sorted(zip(opts, probs), key=lambda x: -x[1])
            out_opts: List[Dict[str, Any]] = []
            for i, (o, p) in enumerate(pairs, start=1):
                action = _clip(o.get("action") or o.get("label") or o.get("id"), MAX_ACTION)
                if not action:
                    out_opts = []
                    break
                out_opts.append({"id": "o%d" % i, "p": round(p, 4), "action": action})
            if len(out_opts) < 2:
                continue
            chosen = {"question": _clip(f.get("question") or f.get("at"), MAX_QUESTION),
                      "options": out_opts}
            break

        if chosen is None:
            _note(reason="no_valid_fork", forks_seen=len(forks))
            return None

        mindset: Dict[str, float] = {}
        m = raw.get("mindset")
        if isinstance(m, dict):
            for d in DRIVES:
                v = m.get(d)
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    continue
                mindset[d] = round(max(-1.0, min(1.0, float(v))), 3)

        _note(reason="ok", confidence=round(conf, 3),
              options=len(chosen["options"]), drives=sorted(mindset))
        return {"fork": chosen, "mindset": mindset, "confidence": round(conf, 3)}
    except Exception as exc:  # noqa: BLE001
        _note(reason="parse_error", error=type(exc).__name__)
        return None


def render(parsed: Optional[Dict[str, Any]],
           cfg: Optional[Dict[str, Any]] = None) -> str:
    """The block that rides the request. Empty string when there is nothing to say.

    Four to six short lines: the block must be cheaper to read than to ignore, and it must
    not look like a question the agent owes an answer to.
    """
    if not isinstance(parsed, dict):
        return ""
    c = cfg if isinstance(cfg, dict) and cfg else cfg_for({})
    try:
        fork = parsed.get("fork") or {}
        opts = [o for o in (fork.get("options") or [])[:int(c["max_options"])]]
        if len(opts) < 2:
            return ""
        lines = [HEADER]
        q = _clip(fork.get("question"), MAX_QUESTION)
        if q:
            lines.append("fork: " + q)
        for o in opts:
            lines.append("  %s %4.2f  %s"
                         % (o["id"], float(o["p"]), _clip(o["action"], MAX_ACTION)))
        mind = parsed.get("mindset") or {}
        bits = ["%s %+0.2f" % (d, float(mind[d])) for d in DRIVES
                if isinstance(mind.get(d), float)]
        if bits:
            lines.append("mind: " + " ".join(bits))
        limit = int(c["block_chars"])
        # Trim whole option lines from the tail rather than cutting mid-line: a block that
        # ends in half a filename is worse than one carrying two options.
        while len(lines) > 4 and len("\n".join(lines)) > limit:
            idx = len(lines) - 2 if lines[-1].startswith("mind: ") else len(lines) - 1
            lines.pop(max(idx, 3))
        block = "\n".join(lines)
        return block if len(block) <= limit else ""
    except Exception:  # noqa: BLE001
        return ""


def build_request(window: str, card: str = "") -> str:
    """The extraction prompt: name the choice in front of the agent, and weight it."""
    body = (
        "You are reading a transcript to note the choice currently in front of the agent.\n"
        "Return ONLY JSON, no prose:\n"
        '{"forks":[{"question":"one line",'
        '"options":[{"id":"a","action":"imperative phrase","p":0.0}]}],'
        '"mindset":{"scope":0.0,"rigour":0.0},"confidence":0.0}\n'
        "Rules: the probabilities for one fork must sum to 1.0 within 0.02; two or three "
        "options at most; no option may be named correct, safe or preferred; action is what "
        "the agent would DO, at most 12 words; mindset is the signed adjustment this work "
        "needs (-1..+1) for scope (how much surface to look at) and rigour (how much "
        "checking before acting); confidence is your own 0..1 that the fork is real. If "
        "there is no genuine fork, return "
        '{"forks":[],"mindset":{},"confidence":0.0}.\n'
    )
    if card:
        body += "\nAGENT CARD (whose reflexes these are):\n" + card.strip()[:2000] + "\n"
    return body + "\nTRANSCRIPT WINDOW:\n" + (window or "")[-12000:]


def extract(window: str, call: Callable[[str], Any], card: str = "",
            cfg: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """One model call -> validated reading. Fails open to None; never raises."""
    try:
        if not (window or "").strip():
            _note(reason="no_window")
            return None
        return parse(call(build_request(window, card)), cfg)
    except Exception as exc:  # noqa: BLE001
        _note(reason="call_error", error=type(exc).__name__)
        return None


def summarise(parsed: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Ledger-friendly one-liner: weights as a compact string plus the mindset delta."""
    if not isinstance(parsed, dict):
        return {"emitted": False}
    opts = (parsed.get("fork") or {}).get("options") or []
    return {"emitted": True,
            "top": opts[0]["action"] if opts else "",
            "weights": "/".join("%.2f" % float(o["p"]) for o in opts),
            "margin": (round(float(opts[0]["p"]) - float(opts[1]["p"]), 3)
                       if len(opts) > 1 else 0.0),
            "mindset": dict(parsed.get("mindset") or {}),
            "confidence": parsed.get("confidence")}
