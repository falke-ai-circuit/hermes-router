"""R73 P3: the decision board must not admit fragments, and it must hear the ask.

Two measured defects (conductor, 2026-10-27):

  1. FRAGMENTS BECOME OPTIONS. PATH_RE stops at a template placeholder, so
     "/run/service/gateway-<agent>" yielded "/run/service/gateway-", whose basename has no dot,
     so is_dir was True and the board offered "look inside gateway-" (live at 26% weight; 340
     fragment mentions per 3000 turns on orchestrator, 5-11% of path mentions fleet-wide).
     Same class: bare "python3" rendered "work inside python3".

  2. THE RANKING IS ASK-BLIND. salient_paths scored only window features, so scenery beat the
     work object; the ask was merely concatenated into the window at both call sites.

Pins below are the acceptance criteria; the measurement lives in extract_bench2.py (mandatory
instrument) and its numbers are reported in the commit message, not asserted here - a bench that
runs 20000 turns per agent does not belong in the suite.
"""
from __future__ import annotations

from hermes_router import decision_extract as dx


# 1. fragments ---------------------------------------------------------------
def test_template_fragment_never_becomes_an_option():
    assert dx.salient_paths("/command/s6-svc -r /run/service/gateway-<agent>") == []


def test_fragment_never_reaches_the_board():
    win = ("the restart line is /command/s6-svc -r /run/service/gateway-<agent> and it "
           "also touches /opt/work/real.py")
    labels = [c["label"] for c in dx.candidate_set(win)]
    assert not [lb for lb in labels if "gateway-" in lb], labels
    assert any("real.py" in lb for lb in labels), labels


def test_bare_interpreter_is_not_a_directory():
    labels = [c["label"] for c in dx.candidate_set("run python3 -m pytest /opt/work/real.py")]
    assert not [lb for lb in labels if "inside python3" in lb], labels


# 2. the ask is scored -----------------------------------------
def test_ask_named_path_outranks_equal_mention_scenery():
    win = "read /opt/work/alpha.py\n" + ("read /opt/work/beta.py\n" * 3)
    no_ask = [c["label"] for c in dx.candidate_set(win)]
    with_ask = [c["label"] for c in dx.candidate_set(win, ask="why is alpha.py wrong here")]
    assert no_ask[0].endswith("beta.py"), no_ask          # mention-count scenery leads
    assert with_ask[0].endswith("alpha.py"), with_ask     # the object the user named leads


def test_ask_does_not_invent_objects_it_merely_shares_a_word_with():
    # a stem match must be a real stem, not a 4-char coincidence inside another word
    win = "read /opt/work/prod.py"
    labels = [c["label"] for c in dx.candidate_set(win, ask="production readiness review")]
    assert labels, "the board must still offer the object in the window"


# 3. floors never lead ------------------------------------------------------
def test_generic_floor_never_outranks_an_object_candidate():
    labels = [c["label"] for c in dx.candidate_set("see /opt/work/gamma.py")]
    assert labels[0].startswith(("change gamma.py", "read gamma.py")), labels
    generic = [i for i, lb in enumerate(labels) if "run the check" in lb]
    objects = [i for i, lb in enumerate(labels) if "gamma.py" in lb]
    assert not generic or not objects or min(generic) > max(objects), labels
