"""R63n — the structural fork sources were unreachable on the commonest shape.

`on_terminal_output` extracted declared options from the tool result and returned
immediately when there were none. A repo listing, a diff, a file body — the shapes every
real working turn produces — never carry an enumeration, so the structural fork-source
block below that gate never ran. Measured live on the reviewer's battery: 17 `no_options`
events, `midturn_fork_source_eval paths=0` at every evaluable turn, and 0 consults across
seven tasks that each contained a genuine fork (which file to edit, doc vs code, config vs
code).

These tests pin that the seam reaches the sources with no declared options present, that
accumulation happens across separate tool results, and that a single suppression is
logged when neither source finds anything.
"""
from __future__ import annotations

import pytest

from hermes_router import decision_midturn as dm
from hermes_router import fork_sources as fs


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    dm.reset_midturn()
    monkeypatch.setattr(dm, "_cfg", lambda: {})
    monkeypatch.setattr(dm, "_mode", lambda cfg: "on")
    monkeypatch.setattr(dm, "_instinct_fire", lambda *a, **k: None)
    yield
    dm.reset_midturn()


def _events():
    """Capture _log calls as (name, fields) tuples."""
    out = []

    def _fake_log(session_id, name, **fields):
        out.append((name, fields))

    return out, _fake_log


def test_a_repo_listing_reaches_the_structural_sources(monkeypatch):
    """No enumeration in the text — the declared gate must not return early.

    The sibling pair below is a real fork ("which of these two is live"), and the
    only way the lane can see it is by consulting fork_sources.
    """
    events, fake = _events()
    monkeypatch.setattr(dm, "_log", fake)

    dm.on_terminal_output("s-repo", "terminal",
                          "-rw-r--r-- parse_utils.py\n-rw-r--r-- parse_utils_old.py")

    names = [n for n, _ in events]
    assert "midturn_fork_source_eval" in names, (
        "the structural source was never evaluated on a tool result with no "
        "declared options -- that is the R63n regression")
    ev = dict(events)["midturn_fork_source_eval"]
    assert ev["paths"] >= 2, ev
    assert ev["found"] == "sibling_files", ev


def test_accumulation_survives_separate_tool_results(monkeypatch):
    """Two reads arriving in two results must still form a pair."""
    events, fake = _events()
    monkeypatch.setattr(dm, "_log", fake)

    dm.on_terminal_output("s-acc", "terminal", "reading lib/parse_utils.py")
    dm.on_terminal_output("s-acc", "terminal", "reading lib/parse_utils_old.py")

    paths = dm._state("s-acc").get("fork_src", {}).get("paths") or []
    assert "lib/parse_utils.py" in paths and "lib/parse_utils_old.py" in paths, paths


def test_a_real_fork_produces_options_not_a_suppression(monkeypatch):
    """The structural hit must reach the envelope as two named routes."""
    events, fake = _events()
    monkeypatch.setattr(dm, "_log", fake)

    dm.on_terminal_output("s-fire", "terminal",
                          "main.py\nparse_utils.py\nparse_utils_old.py\ntests/test_main.py")

    names = [n for n, _ in events]
    assert "midturn_fork_source" in names, names
    hit = dict(events)["midturn_fork_source"]
    assert hit["options"] >= 2, hit


def test_a_result_with_no_fork_still_suppresses_exactly_once(monkeypatch):
    """Removing the early return must not double-log the suppression."""
    events, fake = _events()
    monkeypatch.setattr(dm, "_log", fake)

    dm.on_terminal_output("s-quiet", "execute_code", "just a number: 42")

    suppressed = [f for n, f in events if n == "midturn_suppressed"]
    assert len(suppressed) == 1, suppressed
    assert suppressed[0]["reason"] == "no_options", suppressed


def test_sources_are_not_consulted_when_the_mode_is_off(monkeypatch):
    monkeypatch.setattr(dm, "_mode", lambda cfg: "off")
    touched = []
    monkeypatch.setattr(fs, "rival_targets", lambda *a, **k: touched.append(a))
    dm.on_terminal_output("s-off", "terminal", "parse_utils.py parse_utils_old.py")
    assert touched == []
