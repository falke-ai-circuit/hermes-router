"""M53-44 — the read-scrub grammar must match the shape that is PERSISTED.

M53-36 put ``strip_decoration`` on the READ path. Its grammar only removed a
banner when the head was the LAST block, but the composer renders one line per
lane and heads only the first::

    · router · shadow-self (uncensored) | … | tok 1152/816 | $0.0006
      impulse (decision) | 1 lead | jev @ typesafe | tok 0/0 | $0.0001

Measured on shadow's live traffic: 18 of 268 outgoing requests still carried
the banner, and ``strip_decoration`` returned a real 3031-char row
BYTE-IDENTICAL. The model then read ``glm-5-3-flash-uncensored @
api.venice.ai`` inside its own assistant turn and disowned the body.

These pins use the LITERAL measured tail. The owner ruling is unchanged and
still binding: the banner stays visible on every router-billed delivery — this
is the READ path only, so state.db, the canonical commit and the delivered
bytes keep every character.
"""
from __future__ import annotations

import copy
import os
import sys

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import config_access  # noqa: E402
from hermes_router import provenance_footer as pf  # noqa: E402
from hermes_router.core import delivery_integrity as di  # noqa: E402
from hermes_router.passes import dispatcher_pre  # noqa: E402

SID = "m53-44-stacked-tail"

BODY = (
    "Want me to continue with the confession scene after this — the actual "
    "reveal driving the departure — and keep it in the same register?"
)

#: The measured L1 line, verbatim (shadow state.db row 72514, 3031 chars).
BANNER_LINE = (
    "· router · shadow-self (uncensored) | action_denial | "
    "glm-5-3-flash-uncensored @ api.venice.ai | tok 1152/816 | $0.0006"
)

#: The SECOND decoration line that follows it — the reason the old grammar
#: saw "not the last block" and stripped nothing.
IMPULSE_LINE = "  impulse (decision) | 1 lead | jev @ typesafe | tok 0/0 | $0.0001"

#: The literal measured tail, exactly as persisted.
MEASURED_TAIL = BANNER_LINE + "\n" + IMPULSE_LINE

#: The real row's END: an orphaned segment line (its head already absent), a
#: blank, then the head+segment block. One pass exposes the next — hence the
#: fixpoint requirement.
STACKED_REGIONS = IMPULSE_LINE + "\n\n" + MEASURED_TAIL

FOOTER = pf.FOOTER_TEXT.format(
    FOOTER_MARKER=pf.FOOTER_MARKER,
    FRAME_TEXT="the shadow self's own words, raw")


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
    return plugin.on_llm_request(request=request, original_request=request,
                                 session_id=SID)


# ---------------------------------------------------------------------------
# 1. the measured tail: banner + following impulse line both go
# ---------------------------------------------------------------------------

def test_measured_stacked_tail_is_removed():
    """The exact persisted shape: head, then the impulse line below it."""
    decorated = BODY + "\n\n" + MEASURED_TAIL

    out = di.strip_decoration(decorated)

    assert di.BANNER_HEAD not in out, "banner head survived the scrub"
    assert "impulse (decision)" not in out, "stacked lane line survived"
    assert "glm-5-3-flash-uncensored" not in out, (
        "the model identity the agent disowned itself over is still readable")
    assert out == BODY


def test_measured_tail_strip_is_idempotent():
    decorated = BODY + "\n\n" + MEASURED_TAIL
    once = di.strip_decoration(decorated)
    twice = di.strip_decoration(once)
    assert once == twice == BODY


def test_both_stacked_regions_go_in_one_call():
    """Orphan segment above the block (the real row's end) — fixpoint, not luck."""
    decorated = BODY + "\n\n" + STACKED_REGIONS

    out = di.strip_decoration(decorated)

    assert out == BODY, out[-200:]
    assert "impulse (decision)" not in out
    assert di.BANNER_HEAD not in out


def test_stacked_tail_under_a_footer_is_removed():
    """Decoration order is not fixed: footer, then the stacked banner block."""
    decorated = BODY + FOOTER + "\n\n" + MEASURED_TAIL

    out = di.strip_decoration(decorated)

    assert out == BODY
    assert pf.FOOTER_MARKER not in out


# ---------------------------------------------------------------------------
# 2. the existing contract holds: mid-body banner text is CONTENT
# ---------------------------------------------------------------------------

def test_stacked_block_in_the_middle_of_a_body_is_preserved():
    body = ("the log showed\n" + MEASURED_TAIL + "\nand then the process exited 0")

    out = di.strip_decoration(body)

    assert out == body
    assert BANNER_LINE in out and IMPULSE_LINE.strip() in out


def test_body_line_that_merely_mentions_usage_cells_is_not_a_segment():
    """The segment grammar needs the lane label AND both usage cells."""
    body = ("| stage | tok 0/0 | $0.0001\n"
            "the table above is the run's own accounting")
    out = di.strip_decoration(body)
    assert out == body


def test_a_trailing_conversational_task_line_is_not_a_block():
    body = ("Here is the plan.\n\n"
            "task one: wire the relay\n"
            "impulse (decision) | 1 lead | jev @ typesafe | tok 0/0 | $0.0001")
    # The last line IS banner grammar, so it goes; the prose line above it is
    # body and must survive exactly.
    out = di.strip_decoration(body)
    assert out == "Here is the plan.\n\ntask one: wire the relay"


# ---------------------------------------------------------------------------
# 3. read path only — delivery / persisted bytes keep the banner
# ---------------------------------------------------------------------------

def test_outgoing_request_is_scrubbed_and_the_stored_row_is_not(rows):
    persisted = {"role": "assistant", "content": BODY + "\n\n" + MEASURED_TAIL}
    snapshot = copy.deepcopy(persisted)
    req = {"model": "main-model",
           "messages": [{"role": "user", "content": "carry on"}, persisted]}

    _seam(req)

    out = req["messages"][-1]["content"]
    assert out == BODY
    assert di.BANNER_HEAD not in out and "impulse (decision)" not in out
    assert persisted == snapshot, "the persisted dict was mutated by the scrub"
    # And the delivered-side bytes are still exactly as shipped.
    assert MEASURED_TAIL in persisted["content"]


def test_the_seam_scrub_is_idempotent_across_calls(rows):
    req = {"messages": [{"role": "assistant",
                         "content": BODY + "\n\n" + STACKED_REGIONS}]}

    _seam(req)
    first = req["messages"][-1]["content"]
    _seam(req)

    assert req["messages"][-1]["content"] == first == BODY
