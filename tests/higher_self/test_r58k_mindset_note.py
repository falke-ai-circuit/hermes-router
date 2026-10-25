"""R58k: one renderer for every mindset source.

The defect this pins is a *delivery* defect, not a maths one. The values were
correct and the agent still never received them usefully: the advisory they
rode in was chat-shaped and imperative, the model answered it instead of
absorbing it, and the guard handed the turn back as `handback_failure` with
717 chars of pure echo and zero tool activity.

So these tests are about shape and voice, which is where the failure lived.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO.parent) not in sys.path:
    sys.path.insert(0, str(REPO.parent))

from hermes_router import mindset_note as N  # noqa: E402


# --- the note is state, not a request -------------------------------------

def test_header_declares_it_is_not_answerable():
    """The header must refuse a reply, because the failure was a reply."""
    out = N.note({"scope": 0.9}, source="pre")
    head = out.split("\n")[0]
    assert "internal" in head.lower()
    assert "not a request" in head.lower()
    assert "nothing to answer" in head.lower(), (
        "the agent echoed the advisory instead of working; the note must say "
        "up front that it is not waiting for an answer")


def test_note_carries_no_question_mark_and_no_second_person_imperative():
    """A question invites an answer; an imperative invites compliance talk."""
    for deltas in ({"scope": 1.0}, {"rigour": -1.0},
                   {"scope": -1.0, "rigour": 1.0}):
        for src in ("pre", "post", "on_demand", "midturn"):
            body = N.note(deltas, source=src)
            assert "?" not in body, "a question mark makes it a question"
            assert "you should" not in body.lower()
            assert "please" not in body.lower()


def test_state_clause_precedes_the_adjustment():
    """Description first, move second. A bare move is a command."""
    body = N.note({"scope": 1.0}, source="pre")
    line = [ln for ln in body.split("\n") if ln.startswith("scope:")][0]
    assert line.index("is narrower") < line.index("widen"), (
        "the state clause must come before the adjustment")


# --- silence is the default ----------------------------------------------

def test_no_drive_out_of_band_produces_no_note_at_all():
    assert N.note({"scope": 0.2, "rigour": -0.3}, source="pre") == ""
    assert N.note({}, source="pre") == ""
    assert N.note(None, source="pre") == "", (
        "an unreadable delta map must not render a bare header")


def test_band_edge_is_silent_but_just_past_it_is_not():
    assert N.note({"scope": 0.5}, source="pre", band=0.5) == ""
    assert N.note({"scope": 0.51}, source="pre", band=0.5) != ""


def test_unmeasured_drive_is_skipped_not_defaulted():
    """None means "not measured"; neutral would mean "no change needed"."""
    out = N.note({"scope": None, "rigour": 0.9}, source="pre")
    assert "scope:" not in out
    assert "rigour:" in out


# --- bounded by construction, never by truncation -------------------------

def test_every_line_fits_the_cap_without_being_cut():
    for d in (1, -1):
        for drive in ("scope", "rigour"):
            body = N.note({drive: float(d)}, source="pre")
            line = [ln for ln in body.split("\n") if ln.startswith(drive + ":")][0]
            assert len(line) <= N.MAX_LINE_CHARS
            assert line.rstrip().endswith("."), (
                "a line that stops mid-phrase reads as corruption: an earlier "
                "version sliced at MAX_LINE_CHARS and produced 'configs and.'")
            assert "  " not in line


def test_worst_case_two_drive_note_fits_the_block_cap():
    out = N.note({"scope": 1.0, "rigour": -1.0}, source="on_demand")
    assert len(out) <= N.MAX_NOTE_CHARS


# --- one voice across all four sources -----------------------------------

def test_all_four_sources_render_identical_copy_for_the_same_delta():
    """The dial must not be worded differently by whichever seam noticed it."""
    bodies = {src: N.note({"scope": 0.9}, source=src)
              for src in ("pre", "post", "on_demand", "midturn")}
    core = {src: b.split("\n")[1] for src, b in bodies.items()}
    assert len(set(core.values())) == 1, (
        "PRE/POST/on-demand/midturn must use the same sentence for the same "
        "dial; four wordings means four speakers to reconcile mid-turn")


def test_provenance_is_a_trailing_tag_not_a_speaker_name():
    out = N.note({"scope": 0.9}, source="midturn")
    assert out.split("\n")[0] == N.NOTE_HEADER
    assert out.rstrip().endswith(")")
    assert "mid-task" in out


# --- echo handling -------------------------------------------------------

def test_an_echoed_note_can_be_stripped_off_a_real_report():
    report = "Option A. Ran pytest: 3 passed, nothing to break."
    echoed = report + "\n\n" + N.note({"scope": 0.9}, source="pre")
    assert N.is_note(echoed)
    assert N.strip_note(echoed) == report


def test_append_never_nests_two_notes():
    first = N.note({"scope": 0.9}, source="pre")
    second = N.note({"rigour": -0.9}, source="midturn")
    out = N.append(first, second)
    assert out.count(N.NOTE_HEADER) == 1
    assert "rigour:" in out


def test_echo_guard_knows_the_sentinel():
    """If the guard cannot strip it, an echo ships as if it were work."""
    from hermes_router import echo_guard as E

    assert any("internal state readout" in mk for mk in E._HEADER_MARKERS)


# --- the wiring: no source keeps its own prose path ----------------------

def test_midturn_uses_the_shared_renderer():
    from hermes_router import decision_midturn as DM

    logs = []
    orig = DM._log
    DM._log = lambda sid, ev, **kw: logs.append(kw)
    try:
        out = DM._midturn_mindset_block(
            {"choice": "opt-1", "mindset": {"scope": 0.8}},
            {"mindset": {"scope": 0.1}}, "S")
        assert out.startswith(N.NOTE_HEADER)
        assert logs[-1]["reason"] == "emitted"

        # an unreadable stance must be reported as unmeasured, not as in-band
        out2 = DM._midturn_mindset_block(
            {"mindset": {"scope": 0.9}}, {"mindset": {"scope": None}}, "S")
        assert out2 == ""
        assert logs[-1]["reason"] == "scores_but_stance_unreadable", (
            "reporting this as in_band turns 'never measured' into "
            "'measured, no change needed'")
    finally:
        DM._log = orig


def test_no_delivery_site_still_calls_the_old_prose_renderers():
    """The old renderers may remain as internals; they must not deliver."""
    src = (REPO / "anchor_exec.py").read_text()
    assert "mindset.modulation_block(" not in src, (
        "anchor_exec must render through mindset_note, or the caps and "
        "vocabulary exist in two places and drift")
    mid = (REPO / "decision_midturn.py").read_text()
    assert "mindset.stance_lines(" not in mid, (
        "midturn must render through mindset_note for the same reason")


# --- the calibration base must never be built on the request path ---------

def test_history_build_is_off_the_request_path(monkeypatch):
    """A 1.5s build inside a 2.0s sqlite budget fails under load.

    When it failed it returned {}, every drive became UNKNOWN, and the note
    was never generated -- so the lane reported "unreadable" while looking
    perfectly healthy. The request path must return immediately and warm in
    the background instead.
    """
    from hermes_router import anchor_exec as AX

    monkeypatch.setattr(AX, "_MINDSET_HIST_CACHE", {})
    monkeypatch.setattr(AX, "_MINDSET_HIST_WARMING", {})
    started = []
    monkeypatch.setattr(AX.threading, "Thread",
                        lambda **kw: started.append(kw) or type(
                            "T", (), {"start": lambda self: None})())

    import time as _t
    t0 = _t.time()
    out = AX._mindset_history()
    elapsed = _t.time() - t0

    assert out == {}, "a cold cache must not block the turn to build"
    assert elapsed < 0.10, f"request path took {elapsed:.2f}s; must be non-blocking"
    assert started, "a cold cache must kick a background warm, not stay cold forever"


def test_failed_build_does_not_cache_for_the_long_ttl():
    """A transient timeout must expire fast, not poison 30 minutes."""
    assert N  # renderer imported
    from hermes_router import anchor_exec as AX

    assert AX._MINDSET_HIST_FAIL_TTL <= 120, (
        "an empty build cached for the full TTL turns one timeout into a "
        "half-hour outage that reads as 'unreadable', not 'failed'")
    assert AX._MINDSET_HIST_FAIL_TTL < AX._MINDSET_HIST_TTL
