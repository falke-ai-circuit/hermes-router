"""echo_reprompt — ECHO-WITHHOLD RECOVERY (exactly one re-prompt, never a loop).

The echo guard (``echo_guard.enforce_execution_echo``) has one job it must never
give up: an echoed advisory is never delivered as the agent's own work. What it
must ALSO never do is end the turn there. Live, twice on 2026-10-30, a real
one-paragraph decision question came back as the router's own advisory text and
the ONLY thing the user saw was the hand-back notice:

    "[router] turn ended on the router's own advisory echo, not work product.
     ... nothing was executed. Re-send the ask to run this turn again."

Nothing was executed and the only recovery was a human re-sending the message.
The detection was correct; ending the turn was the defect.

This module is the recovery, and ONLY the recovery:

  1. When the body was withheld AND the turn's work predicate shows no work, the
     SAME turn is re-prompted ONCE with an explicit directive: the previous
     completion was the router's own advisory returned verbatim, it is not work,
     produce the actual answer now, do not repeat the advisory.
  2. A second body that is not an echo is returned to the caller for normal
     delivery (the seam persists it, so persisted == delivered).
  3. A second body that is ALSO an echo (or empty) is discarded — the caller
     fails open with an honest notice (``echo_guard.NO_WORK_TEXT``). The original
     echo is never delivered as work, and the "re-send the ask" dead-end is never
     the only content of a turn.
  4. Exactly ONE model call per invocation. No retry, no loop, no second
     re-prompt. The counter is the contract: a clean turn makes 1 call (the
     turn's own), an echo turn makes 2.

The guard's DETECTION is untouched: this module reuses
``echo_guard.detect_envelope_echo_families`` / ``is_projection_head`` to decide
whether the second body is clean. Recovery, not permissiveness.

``call_model`` is module-level and monkeypatchable — the tests count calls there,
which is the only honest way to prove "exactly once". Fail-open on every path:
this module never raises into a turn.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

EVENT_FIRED = "echo_reprompt_fired"
EVENT_DELIVERED = "echo_reprompt_delivered"
EVENT_FAILED = "echo_reprompt_failed"

#: The re-prompt directive. It names what went wrong precisely (the previous
#: completion WAS the router's advisory), says what it is not (work), and says
#: what is required now (the actual answer), plus the one prohibition (do not
#: repeat the advisory). It never asks the model to describe itself.
DIRECTIVE = (
    "Your previous completion for this turn was the router's own advisory text "
    "returned verbatim. It is not an answer and it is not work product — the "
    "user is asking you a question, not for a restatement of internal notes. "
    "Do not repeat, quote, summarise or refer to that advisory. Produce the "
    "actual answer to the user's message now, as your own reply: the work, the "
    "report, or the decision — whatever the ask actually calls for. If the ask "
    "needs tool use, say what you will do in this reply; do not hand the turn "
    "back."
)

#: Bound on the re-prompt wait. The host kills the transform hook at 30s, and
#: the delivery seam still has work to do after this returns, so the call must
#: leave room. A re-prompt that cannot answer inside this budget is a failed
#: re-prompt, not a stalled turn.
DEFAULT_TIMEOUT_S = 12.0

#: A recovered body shorter than this is not an answer (it is a token, a
#: refusal stub, or whitespace with punctuation). Same floor the guard uses to
#: decide a stripped remainder is a report.
MIN_REPORT_CHARS = 24


def _route(log_route, event: str, **fields) -> None:
    """Route-log via the caller's writer, or the package telemetry. Never raises."""
    try:
        if log_route is None:
            from .core import telemetry as _tlm

            _tlm.log_route("POST", event_detail=event, **fields)
        else:
            log_route("POST", event_detail=event, **fields)
    except Exception:  # noqa: BLE001 — telemetry never blocks delivery
        pass


def call_model(messages: List[Dict[str, str]],
               timeout: float = DEFAULT_TIMEOUT_S) -> Optional[str]:
    """One model call. Module-level so tests can count it.

    Default transport is the plugin's existing aux lane
    (``semantic_classifier._hermes_aux_call``), which resolves the profile's own
    ``auxiliary:`` config through Hermes core — the plugin keeps no endpoint or
    key of its own. Returns the content string, or None on any failure (which
    counts as "the re-prompt produced nothing", handled by the caller as a
    fail-open).
    """
    try:
        from . import semantic_classifier as _sc
        from .core import budgets as _budgets

        payload = json.dumps({
            "messages": messages,
            "max_tokens": int(_budgets.budget("render").get("max_tokens", 2048)),
            "temperature": 0.0,
        })
        raw = _sc._hermes_aux_call(payload, int(max(1, round(float(timeout)))))
        if not raw:
            return None
        try:
            data: Dict[str, Any] = json.loads(raw)
            choices = data.get("choices") or []
            if choices:
                content = (choices[0] or {}).get("message", {}).get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()
        except (ValueError, TypeError, AttributeError):
            pass
        # Tolerant fallback: some doubles answer with bare text.
        text = str(raw).strip()
        return text or None
    except Exception as exc:  # noqa: BLE001 — a transport error is a failed re-prompt
        logger.error("echo_reprompt_failed reason=transport detail=%s", str(exc)[:160])
        return None


def _is_echo(body: str) -> bool:
    """Is this body the router's own text rather than the agent's answer?

    Uses the guard's own detection — never a second, looser definition. A body
    that matches a family, or that OPENS with the voice-score projection, is an
    echo; a body that is too short to be a report is not an answer either.
    """
    try:
        text = str(body or "")
        if len(text.strip()) < MIN_REPORT_CHARS:
            return True
        from . import echo_guard as _eg

        if _eg.detect_envelope_echo_families(text):
            return True
        if _eg.is_projection_head(text):
            return True
        # A tail-only echo (advisory appended to nothing else) survives the
        # family check but strips to nothing: if the strip removes everything
        # the body IS the note.
        stripped, removed = _eg.strip_advisory_echo(text)
        if removed > 0 and len(stripped.strip()) < MIN_REPORT_CHARS:
            return True
        return False
    except Exception:  # noqa: BLE001 — unreadable body = no recovery
        return True


def _messages(ask: str, echoed_body: str) -> List[Dict[str, str]]:
    """The re-prompt payload: the ask, the failed completion, the directive."""
    msgs: List[Dict[str, str]] = []
    ask_text = str(ask or "").strip()
    if ask_text:
        msgs.append({"role": "user", "content": ask_text})
    echo_text = str(echoed_body or "").strip()
    if echo_text:
        msgs.append({"role": "assistant", "content": echo_text})
    msgs.append({"role": "user", "content": DIRECTIVE})
    return msgs


def reprompt_once(ask: str, echoed_body: str, session_id: str = "",
                  model: str = "", log_route: Optional[object] = None,
                  timeout: float = DEFAULT_TIMEOUT_S) -> Optional[str]:
    """Re-prompt the SAME turn ONCE. Return a clean body, or None.

    Exactly one ``call_model`` invocation per call to this function — the
    function contains no loop and no retry, by construction. None means the
    caller must fail open (honest notice), never that the echo may be delivered.
    Never raises.
    """
    try:
        _route(log_route, EVENT_FIRED,
               chars=len(str(echoed_body or "")),
               ask_chars=len(str(ask or "")),
               model=str(model or ""), session_id=str(session_id or ""))
        msgs = _messages(ask, echoed_body)
        try:
            raw = call_model(msgs, timeout)
        except Exception as exc:  # noqa: BLE001 — a raising double is a failed call
            logger.error("echo_reprompt_failed reason=call_error detail=%s",
                         str(exc)[:160])
            raw = None
        body = str(raw or "").strip()
        if not body:
            _route(log_route, EVENT_FAILED, reason="empty",
                   session_id=str(session_id or ""))
            return None
        if _is_echo(body):
            _route(log_route, EVENT_FAILED, reason="second_echo",
                   chars=len(body), session_id=str(session_id or ""))
            return None
        _route(log_route, EVENT_DELIVERED, chars=len(body),
               model=str(model or ""), session_id=str(session_id or ""))
        return body
    except Exception as exc:  # noqa: BLE001 — recovery never breaks the turn
        logger.error("echo_reprompt_failed reason=exception detail=%s", str(exc)[:160])
        return None
