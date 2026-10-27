"""Reflex lane — the decision model DETECTS and WEIGHTS, the lane only carries it.

The rewrite this module is:

  OLD  a regex/gate decides "this is a fork" -> a binary option set is minted ->
       Jev returns `opt-1` at ~0.32 -> the note asks the model to answer a question.
  NEW  the candidate universe is assembled WITHOUT judging it (every action the transcript
       makes visible), then ONE call asks the decision model three things:
         noul  "is there a genuine fork here at all?"        <- detection is Jev's judgement
         choice "which of these actions, and how sure"       <- framing is Jev's weights
         score  scope/rigour adjustment for this work        <- mindset, same call

So detection stops being a regex that fails LOUDLY (billing verdicts on non-forks: 62-97% of
consulted rows carried no real option set) and becomes a calibrated yes/no that fails SOFT —
when the model says there is no fork, nothing is emitted and nothing is billed.

Why the weights matter more than the pick: 0.55/0.45 tells the main model "this is a coin
flip, verify before you spend"; 0.90/0.10 tells it "take it and move". A binary `opt-1`
cannot express either, which is why the live lane kept emitting confident noise.

The block is a READING, never an instruction, and it is delivered on the request surface at
the next model call (mid-turn), never persisted into the transcript — a persisted note gets
restated instead of acted on (`advisory_echo_blocked ... reason=no_work_signal`).

Everything fails open. No exception escapes. No block means the turn proceeds unchanged.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from . import reflex_weights as _rw

logger = logging.getLogger("hermes_router.reflex_lane")

HEADER = "[reflex: a reading of the choice in front of you, not a request]"
VALIDATE = "Fork present"
_PENDING_LOCK = threading.Lock()
_PENDING: Dict[str, Dict[str, Any]] = {}
_PENDING_MAX = 64
_SEQ_LOCK = threading.Lock()
_SEQ: Dict[str, int] = {}

# Candidate assembly is deliberately dumb: it collects what the transcript makes visible and
# never decides whether that constitutes a fork. Judgement belongs to the noul question.
_PATH_RE = re.compile(r"(?:/opt|/tmp|/home|/root|/var|/etc|/srv)[\w./\-]{4,80}"
                      r"|(?:^|[\s\"'(])((?:[\w.\-]+/){1,6}[\w.\-]+\.(?:py|js|ts|go|rs|ya?ml|"
                      r"json|md|sh|toml|sql|css|html))")
_CMD_RE = re.compile(r"^\s*(?:\$|#)?\s*((?:sudo\s+)?(?:git|pytest|python3?|npm|go|make|"
                     r"docker|systemctl|curl|bash|ls|cat|grep|rg)\b[^\n]{0,70})", re.M)
_GENERIC = ("stop and report what is known so far",
            "ask the user which way to go",
            "continue with the current approach unchanged")


def enabled(cfg: Any) -> bool:
    """Armed per profile via ``hermes_router.reflex_weights.enabled``. Default OFF."""
    try:
        return bool(_rw.cfg_for(cfg).get("enabled"))
    except Exception:  # noqa: BLE001
        return False


def candidates(tool: str, text: str, cap: int = 4) -> List[str]:
    """Actions the transcript makes visible. No filtering, no ranking, no fork judgement."""
    seen: List[str] = []
    try:
        for m in _PATH_RE.finditer(text or ""):
            p = (m.group(1) or m.group(0)).strip().strip("\"'()")
            if len(p) < 5:
                continue
            for verb in ("open", "fix"):
                cand = "%s %s" % (verb, p)
                if cand not in seen:
                    seen.append(cand)
            if len(seen) >= cap:
                break
        if len(seen) < cap:
            for m in list(_CMD_RE.finditer(text or ""))[:2]:
                cand = "re-run: %s" % m.group(1).strip()[:60]
                if cand not in seen:
                    seen.append(cand)
        for g in _GENERIC:
            if len(seen) >= cap:
                break
            if g not in seen:
                seen.append(g)
    except Exception:  # noqa: BLE001
        pass
    # A fork needs at least two distinguishable actions; one candidate is not a choice.
    return seen[:cap] if len(seen) >= 2 else []


def build_state(ask: str, tool: str, text: str, extra: str = "") -> str:
    """The evidence handed to the decision model: the ask, the last action, its result."""
    parts = []
    if ask:
        parts.append("USER ASK (what this work is for):\n%s" % str(ask)[:1500])
    if extra:
        parts.append("SESSION STATE:\n%s" % str(extra)[:900])
    if tool:
        parts.append("LAST ACTION: %s" % str(tool)[:80])
    if text:
        parts.append("RESULT OF THAT ACTION (truncated):\n%s" % str(text)[:4000])
    return "\n\n".join(parts)


def payload(state: str, cands: List[str]) -> Dict[str, Any]:
    """One request carrying all three questions. Weights, detection and mindset together."""
    return {
        "model": _rw.MODEL_DEFAULT if hasattr(_rw, "MODEL_DEFAULT") else "jev-latest",
        "state": state,
        "questions": {
            "fork": {
                "type": "noul",
                "instructions": ("There is a genuine decision fork here: the next action is "
                                 "not uniquely determined by the evidence above, and at least "
                                 "two of the listed actions are plausible enough that a "
                                 "competent agent could pick either. Choosing to keep going "
                                 "the same way, when that is the only sensible option, is NOT "
                                 "a fork."),
                "criteria": {"true": "two or more actions are genuinely plausible",
                             "false": "one action follows from the evidence, or the choice "
                                      "does not change what happens next"},
            },
            # ONE NOUL PER CANDIDATE, never a single `choice` over the set.
            #
            # A choice distribution is forced to sum to 1, so the candidates compete for a
            # fixed budget of probability: three genuinely viable actions get ~0.33 each and
            # the block reads as "we are unsure" when the truth is "all three are live". It
            # also lets a weak filler candidate steal mass from a strong one. Independent
            # nouls let several candidates all score high (0.85 / 0.78 / 0.71), which is the
            # correct shape for a discovery read and removes the normalization artifact
            # entirely — no renormalizing, no fabricated distribution.
            **{"c%d" % i: {
                "type": "noul",
                "instructions": ("Taking this action next is a sound, viable step given the "
                                 "evidence above: it addresses what the evidence actually "
                                 "shows and does not waste the turn: %s" % c),
            } for i, c in enumerate(cands, start=1)},
            "scope": {
                "type": "score",
                "instructions": "How much surface must be examined to get this right?",
                "criteria": ["a single file, nothing else matters",
                             "a single file plus its immediate caller",
                             "a few files inside one module",
                             "several modules have to be cross-read",
                             "the whole tree has to be mapped before acting"],
            },
            "rigour": {
                "type": "score",
                "instructions": "How much checking must happen before acting on it?",
                "criteria": ["none, it is obviously safe",
                             "a glance at the result",
                             "run the local check once",
                             "verify each step before committing to it",
                             "verify every step against independent evidence"],
            },
        },
    }


def _num(v: Any) -> Optional[float]:
    try:
        if isinstance(v, bool) or v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_answers(data: Any, cands: List[str]) -> Optional[Dict[str, Any]]:
    """answers -> {fork, options[weighted], mindset, confidence}. None when not a fork."""
    try:
        answers = (data or {}).get("answers") or {}
        fork_ans = answers.get("fork") or {}
        p_fork = _num(fork_ans.get("noul"))
        if p_fork is None:
            p_fork = _num(fork_ans.get("confidence"))
        if p_fork is None:
            _rw._note(reason="no_fork_probability")
            return None

        # One independent viability reading per candidate. These are NOT a distribution:
        # they are not normalized, they may all be high, and they may all be low. That is
        # the point — three live options is a different situation from one live option, and
        # a normalized `choice` cannot express the difference.
        weights: List[Dict[str, Any]] = []
        for i, name in enumerate(cands, start=1):
            p = _num((answers.get("c%d" % i) or {}).get("noul"))
            if p is None:
                continue
            weights.append({"action": name, "p": p})
        if len(weights) < 2:
            _rw._note(reason="no_weights", fork_p=round(p_fork, 3))
            return None
        weights.sort(key=lambda o: -o["p"])

        mindset: Dict[str, float] = {}
        for d in _rw.DRIVES:
            s = _num((answers.get(d) or {}).get("score"))
            if s is None:
                continue
            # 5 rubric levels -> the plugin's [-1,+1] dial (2 is neutral).
            mindset[d] = round(max(-1.0, min(1.0, (s - 2.0) / 2.0)), 3)

        viable = [o for o in weights if float(o["p"]) >= 0.5]
        return {"p_fork": round(p_fork, 4), "options": weights,
                "viable": len(viable),
                "mindset": mindset,
                # confidence is now evidence about the fork itself, not a model's self-report
                "confidence": round(min(1.0, len(viable) / 2.0) if viable else 0.0, 3)}
    except Exception as exc:  # noqa: BLE001
        _rw._note(reason="parse_error", error=type(exc).__name__)
        return None


def render(parsed: Dict[str, Any], limit: int = 400) -> str:
    """The block the main model reads. Short, weighted, no imperative."""
    try:
        opts = parsed.get("options") or []
        if len(opts) < 2:
            return ""
        lines = [HEADER]
        for o in opts[:3]:
            # Percentages, not a normalized 0.62/0.28/0.10 split: these are independent
            # viability readings, so 85% / 78% / 71% is a truthful "three live options".
            lines.append("  %d%%  %s" % (round(float(o["p"]) * 100), str(o["action"])[:68]))
        mind = parsed.get("mindset") or {}
        bits = ["%s %+0.2f" % (d, float(mind[d])) for d in _rw.DRIVES
                if isinstance(mind.get(d), float)]
        if bits:
            lines.append("  mind: " + " ".join(bits))
        block = "\n".join(lines)
        return block if len(block) <= limit else ""
    except Exception:  # noqa: BLE001
        return ""


def _cfg(cfg: Any) -> Dict[str, Any]:
    c = _rw.cfg_for(cfg)
    c.setdefault("every_call", True)
    c.setdefault("min_fork_probability", 0.5)
    c.setdefault("min_gap", 0.0)
    return c


def _transport(payload_obj: Dict[str, Any], cfg: Dict[str, Any]) -> Any:
    """Post to the decision substrate. Reuses the lane's own transport so config is one seam."""
    try:
        from . import decision as _dec

        endpoint = str(cfg.get("typesafe_endpoint") or _dec.DEFAULTS["typesafe_endpoint"])
        key_env = str(cfg.get("typesafe_api_key_env") or _dec.DEFAULTS["typesafe_api_key_env"])
        key = os.environ.get(key_env, "")
        if not key:
            _rw._note(reason="no_key", key_env=key_env)
            return None
        body = dict(payload_obj)
        body["model"] = str(cfg.get("jev_native_model") or body.get("model") or "jev-latest")
        return _dec._http_post_json(endpoint, {"Authorization": "Bearer %s" % key}, body,
                                    float(cfg.get("reflex_timeout_seconds") or 8))
    except Exception as exc:  # noqa: BLE001
        _rw._note(reason="transport_error", error=type(exc).__name__)
        return None


def fire(session_id: str, tool: str, text: str, cfg: Any, ask: str = "",
         extra: str = "") -> str:
    """Assemble -> ask -> validate -> park. Returns the block ("" when nothing to say)."""
    try:
        c = _cfg(cfg)
        if not c.get("enabled"):
            return ""
        if not session_id:
            return ""
        if len(text or "") < 60:
            _rw._note(reason="result_too_thin")
            return ""
        from . import decision_extract as _dx

        cands = _dx.candidate_set((text or "") + "\n" + (ask or ""),
                                  cap=int(c.get("max_options") or 7))
        if not cands:
            _rw._note(reason="no_candidates")
            return ""
        state = build_state(ask, tool, text, extra)
        data = _transport(_dx.payload(state, cands, str(c.get("jev_native_model") or "jev-latest")), c)
        if not isinstance(data, dict):
            return ""
        parsed = _dx.parse(data, cands)
        if not parsed:
            _rw._note(reason="parse_empty")
            return ""
        ok, why = _dx.gate(parsed, float(c.get("min_top") or 0.45),
                           float(c.get("min_margin") or 0.10))
        if not ok:
            _rw._note(reason=why, top=parsed["options"][0]["p"], margin=parsed["margin"])
            _log(session_id, "reflex_held", reason=why,
                 top=parsed["options"][0]["p"], margin=parsed["margin"], seam="midturn")
            return ""
        _rend = (_dx.render_instinct if str(c.get("register") or "frame") == "instinct"
                 else _dx.render)
        block = _rend(parsed, int(c.get("block_chars") or 560),
                      basis=_dx.basis_line(tool, text))
        if not block:
            return ""
        _park(session_id, block)
        _log(session_id, "reflex_weights_ready", top=parsed["options"][0]["p"],
             weights=_weights_str(parsed), margin=_margin(parsed),
             mindset=parsed.get("mindset"), chars=len(block), candidates=len(cands),
             top_kind=_top_claim(parsed).get("kind"),
             top_target=_top_claim(parsed).get("target"),
             block=json.dumps(block))
        return block
    except Exception:  # noqa: BLE001
        logger.debug("reflex_lane.fire error", exc_info=True)
        return ""


def _weights_str(parsed: Dict[str, Any]) -> str:
    return "/".join("%.2f" % float(o["p"]) for o in (parsed.get("options") or [])[:3])


def _margin(parsed: Dict[str, Any]) -> float:
    opts = parsed.get("options") or []
    if len(opts) < 2:
        return 0.0
    return round(float(opts[0]["p"]) - float(opts[1]["p"]), 3)


def _park(session_id: str, block: str) -> None:
    with _PENDING_LOCK:
        _PENDING[session_id] = {"block": block, "ts": time.time()}
        while len(_PENDING) > _PENDING_MAX:
            _PENDING.pop(next(iter(_PENDING)))


def take(session_id: str) -> str:
    """One-shot consume at the next model call. Empty when nothing is pending."""
    with _PENDING_LOCK:
        item = _PENDING.pop(session_id or "", None)
    if not item:
        return ""
    if time.time() - float(item.get("ts") or 0) > 600:
        return ""  # stale: a reading from ten minutes ago is worse than none
    return str(item.get("block") or "")


def next_rule_id(session_id: str) -> str:
    with _SEQ_LOCK:
        n = _SEQ.get(session_id, 0) + 1
        _SEQ[session_id] = n
        while len(_SEQ) > _PENDING_MAX:
            _SEQ.pop(next(iter(_SEQ)))
    return "reflex.%d" % n


def _log(session_id: str, event: str, **kw: Any) -> None:
    """Decision-lane telemetry, independent of the legacy module it used to borrow."""
    try:
        from .decision_log import log_event

        log_event(session_id, event, **kw)
    except Exception:  # noqa: BLE001
        try:
            logger.info("reflex_lane %s %s", event, json.dumps(kw, default=str)[:300])
        except Exception:  # noqa: BLE001
            pass


def clear(session_id: str = "") -> None:
    """Test/reset helper: drop pending state for one session or all."""
    with _PENDING_LOCK:
        if session_id:
            _PENDING.pop(session_id, None)
        else:
            _PENDING.clear()
    with _SEQ_LOCK:
        if session_id:
            _SEQ.pop(session_id, None)
        else:
            _SEQ.clear()
    # the live-reading registry is delivery state like any other: leaving it behind made one
    # test's contract injection appear in the next one's
    with _LAST_BLOCK_LOCK:
        if session_id:
            _DELIVERED_AT.pop(session_id, None)
        else:
            _DELIVERED_AT.clear()
            _LAST_BLOCK.clear()
    # the contract is latched once per context by frames; a full clear must release it too,
    # or the next caller silently gets a reading with no instruction for it
    if not session_id:
        try:
            from . import frames as _fr
            _fr.reset_rule_latch()
        except Exception:  # noqa: BLE001
            pass


# ── wiring helpers ──────────────────────────────────────────────────────────────────────
_THROTTLE_LOCK = threading.Lock()
_LAST_FIRE: Dict[str, float] = {}
MIN_INTERVAL_S = 12.0
MAX_PER_SESSION = 40


def _load_cfg() -> Dict[str, Any]:
    """The profile's live config, through the sanctioned seam only."""
    try:
        from .core import config_access as _ca

        full = _ca.load_full_config()
        if isinstance(full, dict):
            return full
    except Exception:  # noqa: BLE001
        pass
    return {}


def fire_from_hook(session_id: str, tool: str, text: str) -> str:
    """Called on the midturn seam (post_tool_call). Throttled, self-configuring."""
    try:
        cfg = _load_cfg()
        if not enabled(cfg):
            return ""
        key = str(session_id or "")
        if not key:
            return ""
        now = time.time()
        with _THROTTLE_LOCK:
            last = _LAST_FIRE.get(key, 0.0)
            if now - last < MIN_INTERVAL_S:
                return ""
            _LAST_FIRE[key] = now
            while len(_LAST_FIRE) > _PENDING_MAX * 2:
                _LAST_FIRE.pop(next(iter(_LAST_FIRE)))
        ask = ""
        try:
            from .core import state as _st

            ask = str(_st.get_last_seen(key) or "")
        except Exception:  # noqa: BLE001
            pass
        return fire(key, tool, text, cfg, ask=ask)
    except Exception:  # noqa: BLE001
        logger.debug("reflex_lane.fire_from_hook error", exc_info=True)
        return ""


def inject_pass(request: Any, session_id: str = "") -> bool:
    """Deliver the parked reading at the NEXT model call. Never persists, never raises."""
    try:
        if not isinstance(request, dict) or not session_id:
            return False
        block = take(session_id)
        if not block:
            return False
        from . import frames as _fr

        msgs = request.get("messages")
        if not isinstance(msgs, list):
            return False
        # the contract goes in BEFORE the reading: the model must know what the marked
        # block is by the time it reaches it, not after
        _DELIVERED_AT[session_id] = time.time()
        _ensure_rule(request, session_id, _cfg(None))
        ok = _fr.append_platform_rule(msgs, next_rule_id(session_id), block, role="user")
        if ok:
            _log(session_id, "reflex_weights_delivered", chars=len(block))
        return bool(ok)
    except Exception:  # noqa: BLE001
        logger.debug("reflex_lane.inject_pass error", exc_info=True)
        return False


# ── every-call mode ─────────────────────────────────────────────────────────────────────
# Goran 2026-10-26: the reflex can run on EVERY model call, not only mid-turn. Jev is cheap
# enough that the economics invert — a $0.0002 reading per call is cheaper than the main
# model wandering for a thousand tokens while it re-interprets its own transcript. This is
# also what keeps the MAIN model's context clean: the reading replaces deliberation, it does
# not add to it.

_LAST_BLOCK_LOCK = threading.Lock()

# A reading is live in a context once any seam has delivered one. The integration contract
# has to follow the READING, not the call that happens to deliver it: a block parked by the
# midturn seam is delivered by inject_pass, which never reached the contract injection that
# lives on the request-surface path, so the model got a marked block and no instruction for
# it. Tracked here so either path can ensure the rule is present.
_DELIVERED_AT: Dict[str, float] = {}
LIVE_READING_TTL = 1800.0


def _ensure_rule(request: Any, sid: str, c: Dict[str, Any]) -> None:
    """Put the standing rule in the request while a reading is live in this context."""
    if not c.get("integrate", True):
        return
    ts = _DELIVERED_AT.get(sid)
    if not ts or (time.time() - ts) > LIVE_READING_TTL:
        return
    try:
        from . import frames as _fr
        ok = _fr.inject_platform_rule(request, _fr.DECISION_RULE_ID, _fr.DECISION_RULE,
                                      session_id=sid, role="system")
        # the contract is never persisted, so this log line is the only proof it was in the
        # request the model saw
        _log(sid, "decision_rule_injected", rule=_fr.DECISION_RULE_ID,
             chars=len(_fr.DECISION_RULE), appended=bool(ok), seam="every_call")
    except Exception:  # noqa: BLE001 - the contract must never break delivery
        logger.debug("decision rule injection failed", exc_info=True)
_LAST_BLOCK: Dict[str, Tuple[str, float]] = {}
DEDUPE_S = 45.0

# One frame in flight per session, kept so the next request can be asked what the main model
# actually DID with it. Without this the lane can only report that it spoke, never that it was
# heard — which is the difference between a delivery log and a measurement.
_LAST_FRAME_LOCK = threading.Lock()
_LAST_FRAME: Dict[str, Dict[str, Any]] = {}
_PATH_ARG = re.compile(r"(?:/opt|/tmp|/home|/root|/srv|/var|/etc)[\w./\-]{3,90}"
                       r"|[\w.\-]+/[\w.\-]+\.(?:py|js|ts|tsx|go|rs|ya?ml|json|md|sh|toml|sql|cfg|ini)")
_TOOL_CLASS = (("edit", ("edit", "write", "patch", "apply_patch", "create")),
               ("read", ("read_file", "read", "view", "cat", "open")),
               ("run", ("terminal", "exec", "bash", "run", "pytest", "test")),
               ("search", ("search", "grep", "find", "glob", "rg")))


def _tool_class(tool: str) -> str:
    t = (tool or "").lower()
    for cls, names in _TOOL_CLASS:
        if any(n in t for n in names):
            return cls
    return "other"


def _remember(sid: str, parsed: Dict[str, Any], block: str, n_msgs: int) -> None:
    top = (parsed.get("options") or [{}])[0]
    with _LAST_FRAME_LOCK:
        _LAST_FRAME[sid] = {"ts": time.time(), "n_msgs": n_msgs,
                            "top_kind": str(_top_claim({"options": [top]}).get("kind") or ""),
                            "top_target": str(_top_claim({"options": [top]}).get("target") or ""),
                            "top_label": str(top.get("label") or ""),
                            "top_p": float(top.get("p") or 0.0),
                            "mind": dict(parsed.get("mindset") or {}),
                            "options": "|".join("%.2f" % float(o.get("p") or 0)
                                                for o in (parsed.get("options") or [])[:4]),
                            "block": block}
        while len(_LAST_FRAME) > _PENDING_MAX * 2:
            _LAST_FRAME.pop(next(iter(_LAST_FRAME)))


def _top_claim(parsed: Any) -> Dict[str, Any]:
    """The reading's claim about an OBJECT. An abstain option is not a claim.

    The candidate set deliberately offers "take a route none of these name" whenever the
    window is wide (measured recall collapses there), so the model can decline a wrong menu.
    If that option wins, the lane has said "not these" - grading it against a path would score
    a non-claim as a miss, and treating it as a target would make the block point nowhere.
    """
    opts = (parsed or {}).get("options") or []
    for o in opts:
        if str(o.get("kind") or "") not in ("none", ""):
            return o
    return opts[0] if opts else {}


def settle(session_id: str, request: Any) -> Optional[Dict[str, Any]]:
    """Grade the previous frame against what the main model actually did next.

    exact = the tool touched the object the top pick named; class = same kind of move;
    answered = the model replied instead of acting; none = it went elsewhere.
    Absence of any follow-up is reported as unscoreable, never as a miss.
    """
    try:
        with _LAST_FRAME_LOCK:
            fr = _LAST_FRAME.pop(session_id, None)
        if not fr:
            return None
        msgs = (request or {}).get("messages") or []
        if not isinstance(msgs, list):
            return None
        if len(msgs) <= fr["n_msgs"]:
            logger.debug("reflex_lane settle: no follow-up yet (%d <= %d)", len(msgs),
                         fr["n_msgs"])
            # nothing has happened since the frame was written; that is not a miss and not a
            # result — report it as unmeasured and do not log an outcome for it.
            return {"grade": "unscoreable", "tool": "", "paths": [],
                    "top_kind": fr["top_kind"], "top_target": fr["top_target"],
                    "top_p": fr["top_p"], "since_s": round(time.time() - fr["ts"], 1)}
        acted: Optional[Dict[str, str]] = None
        replied = False
        for m in msgs[fr["n_msgs"]:]:
            if not isinstance(m, dict) or m.get("role") != "assistant":
                continue
            for c in (m.get("tool_calls") or []):
                fn = (c.get("function") or {}) if isinstance(c, dict) else {}
                acted = {"tool": str(fn.get("name") or ""),
                         "args": str(fn.get("arguments") or "")[:600]}
                break
            if acted:
                break
            replied = True
        if not acted:
            grade = "answered" if replied else "unscoreable"
            out = {"grade": grade, "tool": "", "paths": [], "top_kind": fr["top_kind"],
                   "top_target": fr["top_target"], "top_p": fr["top_p"],
                   "since_s": round(time.time() - fr["ts"], 1)}
            if grade != "unscoreable":
                _log(session_id, "reflex_outcome", **out)
            return out
        paths = list(dict.fromkeys(_PATH_ARG.findall(acted["args"])))
        want = (fr["top_target"] or "").rstrip("/")
        if want and not os.path.splitext(want)[1]:
            # a directory target names a place, not a file: acting anywhere inside it counts
            exact = any(p.rstrip("/") == want or p.startswith(want + "/") for p in paths)
        else:
            base = os.path.basename(want) if want else ""
            exact = bool(base) and any(os.path.basename(p.rstrip("/")) == base for p in paths)
        same = _tool_class(acted["tool"]) == fr["top_kind"]
        out = {"grade": "exact" if exact else ("class" if same else "none"),
               "tool": acted["tool"], "paths": paths[:3], "top_kind": fr["top_kind"],
               "top_target": fr["top_target"], "top_p": fr["top_p"],
               "since_s": round(time.time() - fr["ts"], 1)}
        _log(session_id, "reflex_outcome", **out)
        return out
    except Exception:  # noqa: BLE001
        return None


def _window_from_request(request: Any, limit: int = 6) -> Tuple[str, str]:
    """(user ask, recent tool/assistant text) from the live request payload."""
    ask, chunks = "", []
    try:
        msgs = (request or {}).get("messages") or []
        if not isinstance(msgs, list):
            return "", ""
        for m in reversed(msgs):
            if not isinstance(m, dict):
                continue
            role = str(m.get("role") or "")
            content = m.get("content")
            if isinstance(content, list):
                content = " ".join(str(c.get("text") or "") if isinstance(c, dict) else str(c)
                                   for c in content)
            text = str(content or "")
            if role == "user" and not ask:
                ask = text[:1500]
                continue
            if role in ("tool", "function") or (role == "assistant" and m.get("tool_calls")):
                # The ARGUMENTS matter as much as the output: a tool result names the file in
                # a traceback, but the thing the agent is actually working on is in its own
                # call. Leaving them out meant extraction mostly never saw the object of the
                # next move - measured: the real next move was in the candidate set only 28-34%
                # of the time, and a third of those turns produced no candidates at all.
                args_txt = ""
                tcs = m.get("tool_calls") if role == "assistant" else None
                if isinstance(tcs, list):
                    bits = []
                    for t in tcs[:3]:
                        fn = (t or {}).get("function") or (t or {})
                        nm = fn.get("name") or ""
                        a = fn.get("arguments")
                        if isinstance(a, (dict, list)):
                            a = json.dumps(a)
                        bits.append("%s %s" % (nm, str(a or "")[:600]))
                    args_txt = "\n".join(bits)
                joined = (text.strip() + ("\n" + args_txt if args_txt else "")).strip()
                if joined:
                    chunks.append(joined[:2500])
            if len(chunks) >= limit:
                break
        chunks.reverse()
    except Exception:  # noqa: BLE001
        pass
    return ask, "\n---\n".join(chunks)


_ARG_PATH = re.compile(r"((?:/opt|/tmp|/home|/root|/srv)[\w./\-]{3,120}"
                       r"|[\w.\-]+/[\w.\-]+\.(?:py|js|ts|go|rs|ya?ml|json|md|sh))")


def _last_move_targets(msgs: Any) -> set:
    """Targets the main model itself named in its most recent tool call.

    This is how the lane avoids fighting the model's own reflexes instead of merely
    disagreeing with them: if the last thing it decided to touch is the thing the reading
    would tell it to touch, the reading has nothing to add.
    """
    try:
        for m in reversed(list(msgs or [])):
            if not isinstance(m, dict) or m.get("role") != "assistant":
                continue
            calls = m.get("tool_calls")
            if not calls:
                continue
            out = set()
            for c in calls or []:
                fn = (c.get("function") or {}) if isinstance(c, dict) else {}
                for p in _ARG_PATH.findall(str(fn.get("arguments") or "")):
                    out.add(os.path.basename(p.rstrip("/")))
            return out
    except Exception:  # noqa: BLE001
        pass
    return set()


def _inject_into_evidence(msgs: Any, block: str) -> bool:
    """Append the reading to the last tool result - the evidence channel.

    The reading then arrives with the data it was derived from, in the channel the model
    already treats as ground truth, rather than as a separate message it must reconcile
    with its own plan. Content shape differs by carrier: str for text tool output, list
    for multimodal parts.
    """
    try:
        if not isinstance(msgs, list):
            return False
        for m in reversed(msgs):
            if not isinstance(m, dict) or m.get("role") != "tool":
                continue
            content = m.get("content")
            if isinstance(content, str):
                m["content"] = content.rstrip() + "\n\n" + block
                return True
            if isinstance(content, list):
                content.append({"type": "text", "text": block})
                return True
            return False
    except Exception:  # noqa: BLE001
        return False
    return False


def run_on_request(request: Any, session_id: str = "", cfg: Any = None) -> bool:
    """Every-call mode: read the live request, ask, inject. Returns True when injected."""
    try:
        c = _cfg(cfg if cfg is not None else _load_cfg())
        if not c.get("enabled") or not c.get("every_call", True):
            return False
        if not isinstance(request, dict):
            return False
        sid = str(session_id or "req")
        settle(sid, request)          # grade the last frame against what actually happened
        _ensure_rule(request, sid, c)  # a reading delivered by another seam still needs it
        ask, window = _window_from_request(request)
        if len(window) < 60:
            _rw._note(reason="no_work_in_request")
            return False
        from . import decision_extract as _dx

        cands = _dx.candidate_set(window + "\n" + ask, cap=int(c.get("max_options") or 7))
        if not cands:
            _rw._note(reason="no_candidates")
            return False
        # One reading per distinct situation: the same block twice in a row is noise, and a
        # retried request must not pay twice.
        sig = "%s|%s" % (window[-400:], "|".join(str(c.get("label") or c) for c in cands))
        now = time.time()
        with _LAST_BLOCK_LOCK:
            prev = _LAST_BLOCK.get(sid)
            if prev and prev[0] == sig and now - prev[1] < DEDUPE_S:
                _rw._note(reason="duplicate_situation")
                return False
        data = _transport(_dx.payload(build_state(ask, "", window), cands,
                                      str(c.get("jev_native_model") or "jev-latest")), c)
        if not isinstance(data, dict):
            return False
        parsed = _dx.parse(data, cands)
        if not parsed:
            return False
        ok, why = _dx.gate(parsed, float(c.get("min_top") or 0.45),
                           float(c.get("min_margin") or 0.10))
        if not ok:
            _rw._note(reason=why, top=parsed["options"][0]["p"], margin=parsed["margin"])
            _log(sid, "reflex_held", reason=why, top=parsed["options"][0]["p"],
                 margin=parsed["margin"], seam="every_call")
            return False
        msgs_now = request.get("messages") or []
        n_before = len(msgs_now) if isinstance(msgs_now, list) else 0

        # Agreement rule (R72). A reflex that only ever contradicts trains the model to
        # discount it. When the model's own last move already targets what the reading
        # would name, the reading adds nothing - so it says nothing, and its silences
        # stop being indistinguishable from its disagreements.
        if c.get("agree_silent", True):
            top_target = str(_top_claim(parsed).get("target") or "")
            want = os.path.basename(top_target.rstrip("/")) if top_target else ""
            if want and want in _last_move_targets(msgs_now):
                _rw._note(reason="agreed_with_model")
                _log(sid, "reflex_agreed", top=parsed["options"][0]["p"],
                     target=want, seam="every_call")
                return False

        # register: `frame` is the dashboard (auditable tables); `instinct` is prose.
        # Which one the model actually acts on is an A/B question, not a taste question.
        _rend = (_dx.render_instinct if str(c.get("register") or "frame") == "instinct"
                 else _dx.render)
        block = _rend(parsed, int(c.get("block_chars") or 560),
                      basis=_dx.basis_line("", window))
        if not block:
            return False
        from . import frames as _fr

        # Integration contract (R72). The reading is a reflex, not an advisory, and the
        # model has to be told which - the frontier and uncensored lanes both carry a
        # standing rule for exactly this reason. Delivered at most once per context,
        # marked by rule id, before the reading itself.
        _DELIVERED_AT[sid] = time.time()
        _ensure_rule(request, sid, c)

        # Delivery shape (R72). `user` = the old interlocutor append; `evidence` = ride the
        # last tool result; `system` = standing instruction. Which one the model actually
        # acts on is a question for the A/B, not for intuition.
        shape = str(c.get("shape") or "user").lower()
        ok = False
        if shape == "evidence":
            ok = _inject_into_evidence(msgs_now, block)
        if not ok and shape == "system":
            ok = _fr.append_platform_rule(msgs_now, next_rule_id(sid), block, role="system")
        if not ok:
            ok = _fr.append_platform_rule(msgs_now, next_rule_id(sid), block, role="user")
        if ok:
            with _LAST_BLOCK_LOCK:
                _LAST_BLOCK[sid] = (sig, now)
                while len(_LAST_BLOCK) > _PENDING_MAX * 2:
                    _LAST_BLOCK.pop(next(iter(_LAST_BLOCK)))
            _remember(sid, parsed, block, n_before)
            _DELIVERED_AT[sid] = time.time()
            _log(sid, "reflex_weights_delivered", chars=len(block),
                 weights=_weights_str(parsed), margin=_margin(parsed),
                 mindset=parsed.get("mindset"), seam="every_call",
                 top=parsed["options"][0]["p"],
                 top_kind=parsed["options"][0].get("kind"),
                 top_target=parsed["options"][0].get("target"),
                 shape=shape, register=str(c.get("register") or "frame"),
                 opts="|".join("%.2f" % float(o["p"]) for o in parsed["options"][:4]),
                 block=json.dumps(block))
        return bool(ok)
    except Exception:  # noqa: BLE001
        logger.debug("reflex_lane.run_on_request error", exc_info=True)
        return False
