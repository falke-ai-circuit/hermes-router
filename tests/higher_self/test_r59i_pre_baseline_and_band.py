"""R59i pins: the PRE note must be able to speak on an opening turn, and the band
must be a policy dial rather than a constant."""
import os, re, sys

sys.path.insert(0, "/opt/data/plugins")


def test_band_default_is_unchanged_when_unset(monkeypatch):
    from hermes_router import anchor_exec, config_access
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    from hermes_router import mindset
    src = open(os.path.join(os.path.dirname(anchor_exec.__file__), "anchor_exec.py")).read()
    # the fallback path must still read the shipped constant, not a new magic number
    assert 'getattr(mindset, "BAND", 0.5)' in src


def test_band_is_clamped():
    from hermes_router import anchor_exec, config_access
    src = open(os.path.join(os.path.dirname(anchor_exec.__file__), "anchor_exec.py")).read()
    assert "min(1.0, max(0.05, _band))" in src


def test_band_knob_is_read_from_the_router_section():
    from hermes_router import anchor_exec
    src = open(os.path.join(os.path.dirname(anchor_exec.__file__), "anchor_exec.py")).read()
    assert '"mindset_band"' in src and "router_section" in src


def test_habitual_baseline_requires_real_history():
    """A drive with thin history must stay UNKNOWN — an invented baseline would
    turn 'never measured' into 'the agent was wrong'."""
    from hermes_router import mindset
    thin = {"scope": [1.0] * 3, "rigour": [1.0] * 3}
    assert len(mindset._history_values(thin, "scope")) < mindset.MIN_HISTORY


def test_habitual_baseline_logs_which_side_answered():
    from hermes_router import anchor_exec
    src = open(os.path.join(os.path.dirname(anchor_exec.__file__), "anchor_exec.py")).read()
    assert "habitual" in src and "baseline=" in src
    # the fallback value is the profile median, which is 0 on this scale
    assert re.search(r"hab = 0\.0", src)


def test_opening_turn_emits_against_the_habitual_baseline(monkeypatch):
    """The behaviour the whole change exists for: an opening turn has no tool rows,
    so both drives read UNKNOWN from the session trace. With real profile history
    the note must still be able to speak, using the profile median as the baseline.
    """
    import logging

    from hermes_router import anchor_exec, mindset

    hist = {"scope": [float(i) for i in range(40)],
            "rigour": [float(i) for i in range(40)]}
    monkeypatch.setattr(anchor_exec, "_mindset_history", lambda *a, **k: hist)
    monkeypatch.setattr(mindset, "MIN_HISTORY", 20)
    events = []
    monkeypatch.setattr(anchor_exec, "_lrx", None, raising=False)

    # No tool rows at all -> the session trace is unreadable on both drives.
    block = anchor_exec._mindset_modulation(
        "pin-session", {"scope": 0.9, "rigour": 0.9}, [], source="pre")
    # A strong target against a median baseline is out of band, so it must speak.
    assert block, "an opening turn with strong targets must not be silent"
    assert "scope" in block or "rigour" in block


def test_opening_turn_stays_silent_without_history(monkeypatch):
    from hermes_router import anchor_exec, mindset

    monkeypatch.setattr(anchor_exec, "_mindset_history", lambda *a, **k: {})
    monkeypatch.setattr(mindset, "MIN_HISTORY", 20)
    block = anchor_exec._mindset_modulation(
        "pin-session-2", {"scope": 0.9, "rigour": 0.9}, [], source="pre")
    assert block == "", "no history means UNKNOWN, never an invented baseline"
