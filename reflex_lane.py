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

from . import persona_card as _pc
from . import reflex_weights as _rw

# R73 P2 S1: the card, its cache and its IDENTITY parser live in persona_card now
# (role_card / _identity_slice). The names below stay because this lane's seams — and
# their tests — reach for them, and because _profile_home must remain a module-global
# name here: the wrapper calls it through globals, so patching it patches the resolver
# that actually runs.
_CARD_CACHE: Dict[str, Any] = _pc._card_cache
CARD_CHARS = _pc.CARD_CHARS

logger = logging.getLogger("hermes_router.reflex_lane")

HEADER = "[reflex: a reading of the choice in front of you, not a request]"
VALIDATE = "Fork present"
_PENDING_LOCK = threading.Lock()
_PENDING: Dict[str, Dict[str, Any]] = {}
_PENDING_MAX = 64
_SEQ_LOCK = threading.Lock()
_SEQ: Dict[str, int] = {}

# R74: one banner per turn. Every fire of this lane used to park its OWN segment under a
# unique task id ("reflex|" + row), and the anchor kind's R9d per-task replace could never
# match one — so a two-fire turn delivered two banner lines (fleet-wide: 116 turns at
# row=reflex.2 vs 60 at .1). The accumulator below is the turn's decision content; the
# constant task id "reflex" makes the existing replace path overwrite it, so the surviving
# segment always carries the WHOLE turn and never stacks. Bounded like _ANCHOR_BANNERS:
# max 32 sessions, oldest evicted.
_ACC: Dict[str, Dict[str, Any]] = {}
_ACC_MAX = 32
_ACC_LOCK = threading.Lock()

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


# R73 P2 S1: the PROFILE-dir resolver moved to persona_card.profile_home() so there is one
# derivation. Kept as a module-global name here because the lane's tests patch it and the
# wrapper below resolves it through globals.
_profile_home = _pc.profile_home


def _agent_card(messages: Any = None) -> str:
    """Who the reading is FOR.

    The decision model was judging the next move without knowing which role has to live with
    it. "Read X first" means something different to a coder mid-repair and to a reviewer
    mid-audit, and scope/rigour read differently again. The card comes from the profile's own
    IDENTITY so every Hermes install carries its own; if it cannot be read the envelope simply
    omits it rather than inventing a role.
    """
    # R73 P2 S1: a thin wrapper over the one accessor. role_card(purpose="read") is the shared
    # card with the RENDER MANDATE *section* removed (a prefix split emptied mandate-first
    # cards, telling the decision model to never decline is the one thing a read must be free
    # to do), and it is the profile's IDENTITY prose slice when the shared builder yields no
    # usable identity — so this module no longer carries a second parser, a second cap or a
    # second cache. messages ride through so the card carries "=== CURRENT TASK ===" from the
    # live session, exactly as the other lanes get it.
    return _pc.role_card(purpose="read", messages=messages, home=_profile_home())


def build_state(ask: str, tool: str, text: str, extra: str = "", card: str = "") -> str:
    """The evidence handed to the decision model: who is asking, the ask, the last action."""
    parts = []
    if card:
        parts.append("WHO THIS IS FOR (the role that has to live with this step):\n%s" % card)
    if ask:
        parts.append("USER ASK (what this work is for):\n%s" % str(ask)[:1500])
    if extra:
        parts.append("SESSION STATE:\n%s" % str(extra)[:900])
    if tool:
        parts.append("LAST ACTION: %s" % str(tool)[:80])
    if text:
        parts.append("RESULT OF THAT ACTION (truncated):\n%s" % str(text)[:4000])
    return "\n\n".join(parts)


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

        # R73 P3: the ask is passed SEPARATELY - concatenating it into the window made the
        # ask's own objects score as scenery, and the top move drifted to whatever a listing
        # printed earlier. Window and ask stay distinguishable all the way down.
        cands = _dx.candidate_set((text or ""), cap=int(c.get("max_options") or 7),
                                  ask=(ask or ""))
        if not cands:
            _rw._note(reason="no_candidates")
            return ""
        # Same card as the every-call path and the other two lanes: without it the midturn
        # read scores moves for a role it cannot name.
        _card = _agent_card()
        state = build_state(ask, tool, text, extra, card=_card)
        data = _transport(_dx.payload(state, cands, str(c.get("jev_native_model") or "jev-latest")), c)
        if not isinstance(data, dict):
            return ""
        parsed = _dx.parse(data, cands)
        if not parsed:
            _rw._note(reason="parse_empty")
            return ""
        ok, why = _dx.gate(parsed, float(c.get("min_top") or 0.45),
                           float(c.get("min_margin") or 0.10))
        _soft = ""
        if not ok:
            # Any midturn gets a reading. A flat board downgrades the claim, not the delivery:
            # the dials and the option set are context even when nothing separates.
            if why == "no_option_set" or not (parsed.get("options") or parsed.get("mindset")):
                _rw._note(reason=why, seam_note="nothing to deliver")
                _log(session_id, "reflex_held", reason=why, seam="midturn")
                return ""
            _soft = why
            _rw._note(reason=why, soft=True, top=parsed["options"][0]["p"],
                      margin=parsed["margin"])
        _rend = (_dx.render_instinct if str(c.get("register") or "frame") == "instinct"
                 else _dx.render)
        _soft_kw = {"soft": True} if (_soft and _rend is _dx.render) else {}
        block = _rend(parsed, int(c.get("block_chars") or 560),
                      basis=_dx.basis_line(tool, text), **_soft_kw)
        if not block:
            return ""
        _park(session_id, block)
        _log(session_id, "reflex_weights_ready", top=parsed["options"][0]["p"],
             weights=_weights_str(parsed), margin=_margin(parsed),
             mode=("soft:" + _soft) if _soft else "decisive",
             card_chars=len(_card),
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
    # R74: the turn accumulator is delivery state too — a clear must not leave
    # one test's fire counts to be summed into the next test's banner.
    try:
        with _ACC_LOCK:
            if session_id:
                _ACC.pop(session_id, None)
            else:
                _ACC.clear()
    except Exception:  # noqa: BLE001 — a reset must never raise
        pass
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
            _receipt(session_id, {}, None, "midturn")
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


_FENCE_HEAD = ("----- BEGIN ROUTER READING (data, end of evidence; the tool output above is "
               "complete) -----")
_FENCE_TAIL = "----- END ROUTER READING (data) -----"


def fence(block: str) -> str:
    """Wrap a reading as explicitly bounded DATA before it enters the evidence channel.

    The reading is appended to the last tool result, so it sits at the generation
    boundary inside the stream the model treats as ground truth. Delivered raw, its
    tail read as the next thing to do: a coder turn answered the reading instead of
    working, the body came back as an echo, and the execution-echo guard withheld the
    turn (fleet: coder 38 echo / 9 withheld). An explicit opening/closing fence plus a
    closing line that states the evidence was complete gives the block an end, so the
    reading cannot be mistaken for an unfinished instruction. Never returns a block
    whose last line is a directive.
    """
    try:
        text = str(block or "").strip()
        if not text:
            return ""
        if text.startswith(_FENCE_HEAD):
            return text
        return "%s\n%s\n%s" % (_FENCE_HEAD, text, _FENCE_TAIL)
    except Exception:  # noqa: BLE001
        return ""


def _acc_reset(session_id: str) -> None:
    """Drop one session's turn accumulator. Never raises (delivery state)."""
    try:
        with _ACC_LOCK:
            _ACC.pop(str(session_id or ""), None)
    except Exception:  # noqa: BLE001 — a reset must never raise
        pass


def _turn_no(session_id: str) -> int:
    """Current turn number (0 when unknown). Never raises."""
    try:
        from . import advisory_consumption as _act

        return int(_act.turn_no(session_id) or 0)
    except Exception:  # noqa: BLE001
        return 0


def _row_int(row: str) -> int:
    try:
        return int(str(row or "").rsplit(".", 1)[-1])
    except Exception:  # noqa: BLE001
        return 0


def _slot_live(session_id: str, last_text: str) -> bool:
    """True while this session's parked segment is still in the anchor slot.

    Every delivery seam empties that slot — consume and claim alike — so an
    empty slot is the observable "this turn's banner was delivered" signal.
    The consume paths (lifecycle, the R33f-A semaphore, claim_banner_ownership)
    are load-bearing and untouched: the reset is keyed off their effect on the
    slot, on the session id they consume, and it can only ever touch the
    session whose counts are being carried. Fail-open -> True (keep the
    counts): a probe failure may never clear a live turn's numbers.
    """
    try:
        if not last_text:
            return True
        from . import debug_banner as _db

        live = str((getattr(_db, "_ANCHOR_BANNERS", {}) or {}).get(
            str(session_id or ""), "") or "")
        return last_text[:120] in live
    except Exception:  # noqa: BLE001
        return True


def _verdict_class(parsed: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    """'lead' or 'no_lead' — the class the fire path ALREADY computed.

    A fire is decisive when the lane's own gate passes, soft ("no clear lead")
    when it downgrades the claim: exactly the `mode=decisive` vs `mode=soft:*`
    split written to the route log. No new taxonomy is minted here.
    """
    try:
        from . import decision_extract as _dx

        ok, _why = _dx.gate(parsed, float(cfg.get("min_top") or 0.45),
                            float(cfg.get("min_margin") or 0.10))
        return "lead" if ok else "no_lead"
    except Exception:  # noqa: BLE001
        return "no_lead"


def _acc_update(session_id: str, tokens_in: int, tokens_out: int, cost: float,
                row: str, seam: str, verdict: str) -> Tuple[Dict[str, Any], bool]:
    """Fold one fire into the turn's accumulator. Returns (snapshot, delivered).

    `delivered` is True when this fire arrives AFTER the turn's banner was
    already consumed or claimed: the render edge's claim_banner_ownership pops
    the slot exactly like a consume, and a claimed turn carries its OWN banner,
    so the reflex summary is DROPPED rather than double-bannered (§3.5).
    """
    sid = str(session_id or "")
    n = _row_int(row)
    turn = _turn_no(sid)
    with _ACC_LOCK:
        acc = _ACC.get(sid)
        delivered = False
        if acc is not None:
            acc_turn = int(acc.get("turn_no") or 0)
            # R74-A2 F1: TURN IDENTITY WINS over the slot probe. A turn whose
            # parked banner was never consumed (a real live class — see
            # gate/orchestration.py:_deliver_parked_at_edge) leaves its text in
            # the anchor slot, so slot-emptiness alone cannot see the boundary
            # and the next turn inherited its counts. Roll only when BOTH sides
            # of the turn identity are known: an unknown turn (0) keeps today's
            # slot-based behaviour rather than guessing a reset that could
            # clear a live turn's numbers.
            if turn and acc_turn and acc_turn != turn:
                acc = None
            elif not _slot_live(sid, str(acc.get("last_text") or "")):
                if turn and acc_turn == turn and int(acc.get("fires") or 0) >= 1:
                    # Same turn, slot already emptied -> the banner was delivered
                    # (or claimed) mid-turn. Drop, never re-park into this turn.
                    delivered = True
                    acc = None
                else:
                    acc = None  # previous turn was delivered -> this fire opens a new one
        if acc is None:
            acc = {"fires": 0, "leads": 0, "no_lead": 0, "tok_in": 0, "tok_out": 0,
                   "cost": 0.0, "first_row": n, "last_row": n, "triggers": set(),
                   "last_text": "", "last_seam": "", "turn_no": turn}
            _ACC[sid] = acc
            while len(_ACC) > _ACC_MAX:
                _ACC.pop(next(iter(_ACC)), None)
            if delivered:
                _ACC.pop(sid, None)
                return ({"fires": 0, "leads": 0, "no_lead": 0, "tok_in": 0,
                         "tok_out": 0, "cost": 0.0, "first_row": n, "last_row": n,
                         "triggers": set(), "last_text": "", "last_seam": seam,
                         "turn_no": turn}, True)
        acc["fires"] = int(acc["fires"]) + 1
        acc["leads"] = int(acc["leads"]) + (1 if verdict == "lead" else 0)
        acc["no_lead"] = int(acc["no_lead"]) + (1 if verdict == "no_lead" else 0)
        acc["tok_in"] = int(acc["tok_in"]) + int(tokens_in or 0)
        acc["tok_out"] = int(acc["tok_out"]) + int(tokens_out or 0)
        acc["cost"] = float(acc["cost"]) + float(cost or 0.0)
        acc["last_row"] = n
        acc["last_seam"] = str(seam or "")
        try:
            acc["triggers"].add(str(seam or ""))
        except Exception:  # noqa: BLE001
            pass
        snap = {k: (set(v) if isinstance(v, set) else v) for k, v in acc.items()}
        return snap, delivered


_PROVIDER_HOSTS = (("openrouter.ai", "openrouter"),
                   ("inference-api.nousresearch.com", "nous"),
                   ("api.venice.ai", "venice"),
                   ("api.typesafe.ai", "typesafe"))


def _display_model(model: Any) -> str:
    """Fleet naming for the model cell: bare name, lane prefix/-latest dropped.

    The other lanes print the provider as the HOST cell, so repeating it in the
    model cell ("typesafe/jev-router @ typesafe") is noise; the fleet reads
    "jev @ typesafe". Bounded so the line cannot grow past the size budget.
    """
    try:
        m = str(model or "").strip()
        if "/" in m:
            m = m.rsplit("/", 1)[-1]
        if m.lower().endswith("-latest") and len(m) > len("-latest") + 1:
            m = m[: -len("-latest")]
        return m[:16]
    except Exception:  # noqa: BLE001
        return ""


def _display_host(cfg: Dict[str, Any]) -> str:
    """The RESOLVED provider host the other lanes name — never a literal '?'.

    Same provider map render_payload uses for its banners, applied to the
    decision backend's configured endpoint. Unresolvable -> '' and the cell is
    omitted (a missing host must not print as a question mark).
    """
    try:
        from . import decision as _dec

        ep = str(cfg.get("typesafe_endpoint")
                 or (_dec.DEFAULTS or {}).get("typesafe_endpoint") or "")
    except Exception:  # noqa: BLE001
        ep = ""
    try:
        if "://" in ep:
            ep = ep.split("://", 1)[1]
        host = ep.split("/", 1)[0].split(":", 1)[0].strip()
    except Exception:  # noqa: BLE001
        host = ""
    for real, name in _PROVIDER_HOSTS:
        if real in host:
            return name
    return host[:24]


def _park_summary(session_id: str, acc: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    """Render the turn's ONE banner line and park it under the constant task id.

    R74-A2 (Goran, measured): SIZE IS BOUNDED BY TODAY'S LINE, not by
    MAX_BANNER_CHARS (a global crash-guard). The live single-fire line was 120c;
    this renderer targets <= 120c and is hard-bounded at 140c for any fire count:

        · router · impulse (decision) x3 | 2 lead · 1 hold | jev @ typesafe |
          6134/280 tok | $0.00017 | row 4-6

    Compression (all of it, every fire count): the trigger field (`every_call`)
    is gone — `xN` already implies cadence; verdict classes are singular
    ("2 lead · 1 hold", zero classes omitted); tokens render `6134/280 tok`; cost
    carries 5 decimals; the row renders `row 4-6` with no `reflex.` prefix; and
    the host is the REAL resolved provider, never "?". Tokens and cost are
    SUMMED, never averaged.

    debug_banner.format_banner stays the shared validator and the fail-open gate
    for this family: when it refuses (unknown/forbidden lane, oversized), this
    returns "" and nothing is parked. Never raises.
    """
    try:
        from . import debug_banner as _db

        n = int(acc.get("fires") or 0)
        if n <= 0:
            return ""
        ti = int(acc.get("tok_in") or 0)
        to = int(acc.get("tok_out") or 0)
        cost = float(acc.get("cost") or 0.0)
        model = str(cfg.get("jev_native_model") or "jev-latest")
        first = int(acc.get("first_row") or 0)
        last = int(acc.get("last_row") or first)
        leads = int(acc.get("leads") or 0)
        holds = int(acc.get("no_lead") or 0)

        head = "%s impulse (decision)" % _db.BANNER_HEAD
        if n > 1:
            head += " x%d" % n

        classes = []
        if leads:
            classes.append("%d lead" % leads)
        if holds:
            classes.append("%d hold" % holds)
        field2 = " · ".join(classes) or "%d lead" % 0

        model_s = _display_model(model)
        host_s = _display_host(cfg)
        model_part = ("%s @ %s" % (model_s, host_s)) if (model_s and host_s) else (model_s or host_s)
        if not model_part:
            return ""  # nothing honest to name -> no banner, never a "?"

        if cost > 0.0:
            cost_s = "$%.5f" % cost
        elif ti > 0 or to > 0:
            cost_s = "$0.00000*"   # unpriced provider: zero is not a price
        else:
            cost_s = "$0.00000"

        row_s = ("row %d" % first) if (n == 1 or last <= first) else ("row %d-%d" % (first, last))

        # the shared formatter is the lane validator / fail-open gate here; its
        # own render is discarded in favour of the compressed cell order.
        gate = _db.format_banner(
            lane="reflex", trigger="reading", model=model, endpoint=host_s,
            tokens_in=ti, tokens_out=to, est_cost=cost, latency_s=0,
            session_id=session_id, route_id=("reflex.%d" % first), initiator="router",
        )
        if not gate:
            return ""

        out = " | ".join((head, field2, model_part, "%d/%d tok" % (ti, to), cost_s, row_s))
        cap = int(getattr(_db, "MAX_BANNER_CHARS", 700) or 700)
        if len(out) > min(140, cap):
            # size is a hard requirement, not a target: shorten the two name
            # cells rather than emit an over-budget line or a truncated field.
            short_model = model_s[:8]
            short_host = host_s[:10]
            model_part = ("%s @ %s" % (short_model, short_host)) if (short_model and short_host) \
                else (short_model or short_host)
            out = " | ".join((head, field2, model_part or "-", "%d/%d tok" % (ti, to),
                              cost_s, row_s))
        return out if len(out) <= min(140, cap) else ""
    except Exception:  # noqa: BLE001 — a render must never break delivery
        return ""


def _receipt(session_id: str, parsed: Dict[str, Any], data: Any, seam: str) -> str:
    """Park a delivery receipt for a reading that actually shipped.

    A reading is a billed call that changes what the model does next, and it used to leave no
    trace the user could see: reflex_lane had zero banner references while decision.py had 41,
    so only the older PRE/POST path ever bannered (4/4 readings delivered, 0/4 banners on the
    API surface). This is the SAME mechanism the other lanes use - debug_banner.format_banner
    + park_anchor_banner consumed at the post seam - never a second banner system. It names
    lane, trigger, model, tokens, cost and the reading's row id, and it is only parked when a
    block was really delivered.
    """
    try:
        from . import debug_banner as _db

        usage = (data or {}).get("usage") or {} if isinstance(data, dict) else {}
        ti = usage.get("prompt_tokens")
        if ti is None:
            ti = usage.get("input_tokens") or 0
        to = usage.get("completion_tokens")
        if to is None:
            to = usage.get("output_tokens") or 0
        cfg = _cfg(None)
        # R75: price the MODEL ID, never the lane's display label. "hermes-auxiliary"
        # is a display name for the auxiliary lane and matches no price table, so
        # estimate_cost honestly returned 0.0 and every reading banner rendered the
        # unpriced cell $0.00000* even though the tokens were real. The model the
        # reflex consult was dispatched to is jev_native_model (see _transport
        # callers above), so that is the key. An unknown model still prices 0.0
        # and still renders the asterisk — failure is fully open, never a guess.
        price_model = str(cfg.get("jev_native_model") or "jev-latest")
        cost = 0.0
        try:
            from . import usage_ledger

            cost = float(usage_ledger.estimate_cost(price_model, int(ti), int(to)) or 0.0)
        except Exception:  # noqa: BLE001 — a missing price is 0.0, never a guess
            pass
        row = next_rule_id(session_id)
        verdict = _verdict_class(parsed, cfg)
        # R74: fold this fire into the turn's accumulator, then park the
        # RENDERED SUMMARY under the CONSTANT task id "reflex" — the anchor
        # kind's R9d replace path then overwrites its own segment instead of
        # appending a second line (one banner per turn).
        snap, delivered = _acc_update(
            session_id, int(ti), int(to), float(cost or 0.0), row,
            str(seam or "reading"), verdict)
        if delivered:
            _log(session_id, "reflex_banner_dropped", row=row,
                 reason="turn_already_delivered", seam=str(seam or ""))
            return ""
        banner = _park_summary(session_id, snap, cfg)
        if not banner:
            return ""
        _db.park_anchor_banner(session_id, banner, task_id="reflex")
        try:
            with _ACC_LOCK:
                cur = _ACC.get(str(session_id or ""))
                if isinstance(cur, dict) and int(cur.get("fires") or 0) == int(snap.get("fires") or 0):
                    cur["last_text"] = banner
        except Exception:  # noqa: BLE001 — bookkeeping never breaks delivery
            pass
        _log(session_id, "reflex_receipt_parked", row=row, seam=str(seam or ""),
             tokens_in=int(ti), tokens_out=int(to), est_cost=round(float(cost or 0.0), 6),
             fires=int(snap.get("fires") or 0), verdict=verdict,
             rows="%d-%d" % (int(snap.get("first_row") or 0), int(snap.get("last_row") or 0)),
             top=float((parsed.get("options") or [{}])[0].get("p") or 0.0))
        return banner
    except Exception:  # noqa: BLE001 — a receipt must never break a delivery
        return ""


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
        block = fence(block)
        if not block:
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

        # R73 P3: ask scored as its own term, never concatenated into the window
        cands = _dx.candidate_set(window, cap=int(c.get("max_options") or 7), ask=ask)
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
        _msgs = request.get("messages")
        _card = _agent_card(_msgs if isinstance(_msgs, list) else None)
        data = _transport(_dx.payload(build_state(ask, "", window, card=_card), cands,
                                      str(c.get("jev_native_model") or "jev-latest")), c)
        if not isinstance(data, dict):
            return False
        parsed = _dx.parse(data, cands)
        if not parsed:
            return False
        ok, why = _dx.gate(parsed, float(c.get("min_top") or 0.45),
                           float(c.get("min_margin") or 0.10))
        _soft = ""
        if not ok:
            # Goran 2026-10-27: the lane fires on ANY midturn and always hands back the
            # dials. It is not a fork detector that gives way when no fork is found - the
            # options are context even when none of them separates. A gate failure therefore
            # downgrades the claim ("no clear lead"), never the delivery. Only a genuinely
            # empty option set still stands down.
            if why == "no_option_set" or not (parsed.get("options") or parsed.get("mindset")):
                _rw._note(reason=why, seam_note="nothing to deliver")
                _log(sid, "reflex_held", reason=why, seam="every_call")
                return False
            _soft = why
            _rw._note(reason=why, soft=True, top=parsed["options"][0]["p"],
                      margin=parsed["margin"])
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
        _soft_kw = {"soft": True} if (_soft and _rend is _dx.render) else {}
        block = _rend(parsed, int(c.get("block_chars") or 560),
                      basis=_dx.basis_line("", window), **_soft_kw)
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
            # P1: a delivered reading is a billed call with a visible effect, so it ships with
            # a receipt through the same banner path as every other lane.
            _receipt(sid, parsed, data, "every_call")
            with _LAST_BLOCK_LOCK:
                _LAST_BLOCK[sid] = (sig, now)
                while len(_LAST_BLOCK) > _PENDING_MAX * 2:
                    _LAST_BLOCK.pop(next(iter(_LAST_BLOCK)))
            _remember(sid, parsed, block, n_before)
            _DELIVERED_AT[sid] = time.time()
            _log(sid, "reflex_weights_delivered", chars=len(block),
                 weights=_weights_str(parsed), margin=_margin(parsed),
                 # card_chars makes "did the reading know its role" auditable per agent:
                 # without it the only evidence was a code read, and a silent 0 stays invisible
                 card_chars=len(_card),
                 mode=("soft:" + _soft) if _soft else "decisive",
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
