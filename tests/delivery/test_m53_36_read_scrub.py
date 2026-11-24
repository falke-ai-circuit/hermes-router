"""M53-36 — READ-TIME SCRUB of delivery decoration.

Measured defect: a delivered render turn persists with the router banner
(``· router · shadow-self (uncensored) | … ``) and the provenance footer
(``[uncensored-render-note · …]``) inside ``messages.content``. On the NEXT turn
the main model reads its own history, sees those strings, and disowns the body
as foreign text. Measured: assimilation 58% -> 95% once both were suppressed —
but suppressing them at DELIVERY also removes Goran's cost telemetry.

Owner ruling (binding): the banner stays visible on ANY router-billed call and
is delivered exactly as today. So the fix is on the READ path only:

  * the outgoing ``messages`` list loses the decoration on ASSISTANT turns,
  * state.db rows, the canonical commit and the delivered body keep it.

The scratch grammar is ``delivery_integrity.strip_decoration`` (banner + footer)
which treats a banner at LEAD/TAIL only — a banner-looking line in the MIDDLE of
a body is content and stays.

Every test here fails without the scrub: the decorated assistant turn survives
into the outgoing request.
"""
from __future__ import annotations

import copy
import os
import sys
from unittest import mock

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import config_access  # noqa: E402
from hermes_router import provenance_footer as pf  # noqa: E402
from hermes_router.core import delivery_decision as dd  # noqa: E402
from hermes_router.core import delivery_integrity as di  # noqa: E402
from hermes_router.passes import dispatcher_pre  # noqa: E402

SID = "m53-36-read-scrub"

BODY = "Here is the answer the turn actually carried.\nSecond line of substance."

#: The measured L1 shape: `· router · <lane> (uncensored) | … | <model> @ <host>`.
BANNER = (
    "· router · shadow-self (uncensored) | task 0f3c9a1e | sess 20261123_2241 | "
    "glm-5-3-flash-uncensored @ api.venice.ai"
)

#: Built from the REAL footer template, so the pinned marker and separator are
#: the product's, not a test-local imitation.
FOOTER = pf.FOOTER_TEXT.format(FOOTER_MARKER=pf.FOOTER_MARKER,
                               FRAME_TEXT="the shadow self's own words, raw")


def _decorated(order: str = "banner_last") -> str:
    """A delivered body carrying BOTH decorations, in the given order."""
    if order == "banner_last":          # banner composed over a footer-less body
        return BODY + "\n\n" + BANNER
    return BODY + FOOTER + "\n\n" + BANNER   # footer first, banner appended


def _both(order: str = "banner_last") -> str:
    """Body + banner + footer — the measured persisted shape."""
    if order == "banner_last":
        return _decorated("banner_last") + FOOTER
    return _decorated("footer_first") + FOOTER


@pytest.fixture()
def rows(monkeypatch):
    """Drive the REAL llm_request seam; router OFF so only the scrub is live."""
    captured = []

    def _capture(event, **fields):
        captured.append((event, fields))

    monkeypatch.setattr(plugin, "_cfg",
                        lambda: {"enabled": False, "log_routes": False})
    monkeypatch.setattr(plugin, "_log_route", _capture)
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    monkeypatch.setattr(dispatcher_pre, "_READ_SCRUB_FAILED_LOGGED", False,
                        raising=False)
    return captured


def _seam(request):
    """The host's call shape — request and original_request are one object."""
    return plugin.on_llm_request(request=request, original_request=request,
                                 session_id=SID)


def _assistant(msgs):
    return [m for m in msgs
            if isinstance(m, dict) and m.get("role") == "assistant"]


# ---------------------------------------------------------------------------
# 1. the outgoing request loses the decoration
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("order", ["banner_last", "footer_first"])
def test_assistant_history_is_scrubbed_on_the_outgoing_request(rows, order):
    decorated = _both(order)
    req = {"model": "main-model",
           "messages": [{"role": "user", "content": "carry on"},
                        {"role": "assistant", "content": decorated}]}

    _seam(req)

    out = _assistant(req["messages"])[-1]["content"]
    assert di.BANNER_HEAD not in out, "banner still readable by the model"
    assert pf.FOOTER_MARKER not in out, "provenance footer still readable"
    assert out == BODY


def test_the_scrub_is_idempotent_across_calls(rows):
    """Two model calls in one turn must not re-decorate or re-shape anything."""
    req = {"messages": [{"role": "assistant", "content": _both()}]}
    _seam(req)
    first = req["messages"][-1]["content"]
    _seam(req)
    assert req["messages"][-1]["content"] == first == BODY


# ---------------------------------------------------------------------------
# 2. only ASSISTANT turns are read-scrubbed
# ---------------------------------------------------------------------------

def test_user_role_messages_are_untouched(rows):
    ask = "quote the banner back to me:\n\n" + _both()
    req = {"messages": [{"role": "user", "content": ask},
                        {"role": "assistant", "content": _both()}]}

    _seam(req)

    users = [m for m in req["messages"] if m.get("role") == "user"]
    assert users[-1]["content"] == ask, "a user's own words were rewritten"


def test_system_and_tool_messages_are_untouched(rows):
    sys_msg = {"role": "system", "content": "marker in system: " + BANNER}
    tool_msg = {"role": "tool", "content": "tool output " + BANNER}
    req = {"messages": [sys_msg, tool_msg,
                        {"role": "assistant", "content": _both()}]}

    _seam(req)

    assert req["messages"][0]["content"] == sys_msg["content"]
    assert req["messages"][1]["content"] == tool_msg["content"]


# ---------------------------------------------------------------------------
# 3. delivery / canonical side keeps the banner (owner ruling + M50-23)
# ---------------------------------------------------------------------------

def test_delivered_and_persisted_text_keep_the_banner(rows):
    """The scrub is a READ path: the stored dict and the shipped bytes do not move."""
    delivered = _both()
    persisted = {"role": "assistant", "content": delivered}
    req = {"messages": [persisted]}

    _seam(req)

    assert persisted["content"] == delivered, "persisted row was mutated"
    assert di.BANNER_HEAD in delivered and pf.FOOTER_MARKER in delivered
    # What the scrub removed is EXACTLY the decoration — nothing else.
    assert di.strip_decoration(delivered) == BODY


def test_own_banner_survives_the_delivery_marker_strip(rows):
    """M50-23 must not regress: the router's own banner is never a leak."""
    delivered = _both()
    shipped = dd.strip_marker_lines(delivered)
    assert di.BANNER_HEAD in shipped
    assert BODY.splitlines()[0] in shipped


# ---------------------------------------------------------------------------
# 4. fail-open: an exception leaves the request byte-identical
# ---------------------------------------------------------------------------

def test_scrub_exception_leaves_messages_byte_identical(rows, monkeypatch):
    def _boom(text):
        raise RuntimeError("scrub exploded")

    monkeypatch.setattr(di, "strip_decoration", _boom)
    req = {"model": "main-model",
           "messages": [{"role": "user", "content": "carry on"},
                        {"role": "assistant", "content": _both()}]}
    before = copy.deepcopy(req["messages"])

    # Must not raise, and must not block the call.
    _seam(req)
    assert req["messages"] == before, "a failed scrub must not half-write"

    # Lazy: ONE row per process, not one per model call.
    _seam(req)
    fails = [f for e, f in rows if f.get("event_detail") == "read_scrub_failed"]
    assert len(fails) == 1, fails
    assert "messages" in req and req["messages"] == before


# ---------------------------------------------------------------------------
# 5. only LEAD/TAIL decoration is stripped
# ---------------------------------------------------------------------------

def test_banner_in_the_middle_of_a_body_is_preserved(rows):
    body = ("the log showed\n"
            + BANNER + "\n"
            "and then the process exited 0")
    req = {"messages": [{"role": "assistant", "content": body}]}

    _seam(req)

    assert req["messages"][-1]["content"] == body
    assert BANNER in req["messages"][-1]["content"]


def test_footer_marker_in_the_middle_is_not_a_tail_footer(rows):
    body = "quote: " + pf.FOOTER_MARKER + " appears inline in the body"
    req = {"messages": [{"role": "assistant", "content": body}]}

    _seam(req)

    assert req["messages"][-1]["content"] == body
