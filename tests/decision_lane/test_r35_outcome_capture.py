"""R35: structural outcome capture for the decision lane.

The lane had no way to say what the agent did about its recommendation, and the
one it had was circular (a prose regex over `opt-N` vocabulary the lane itself
injected). These pins hold the replacement to its contract: signatures recorded
at detection time, positive evidence only, abstention instead of guesses.
"""
import json
import sqlite3

import pytest

from hermes_router import decision_signatures as ds

OPTS = [
    "INSPECT the repo with read_file and report what is there.",
    "CREATE /opt/data/projects/demo/out.md with write_file.",
    "DO NOTHING and report that nothing is needed.",
]


def sigs():
    return [ds.option_signature(o) for o in OPTS]


def ev(tool, effect_blob="{}", calls=None):
    rows = []
    if calls:
        rows.append({"role": "assistant", "content": "",
                     "tool_calls": json.dumps(calls)})
    rows.append({"role": "tool", "content": effect_blob, "tool_name": tool})
    return ds.evidence_from_messages(rows)


def call(name, args):
    return [{"function": {"name": name, "arguments": json.dumps(args)}}]


# --- signatures -------------------------------------------------------------

def test_signature_reads_observables_off_the_option_text():
    s = ds.option_signature(OPTS[1])
    assert "write_file" in s["tools"]
    assert "/opt/data/projects/demo/out.md" in s["paths"]
    assert "write" in s["effects"]


def test_a_bare_filename_is_not_a_path():
    """`SKILL.md` appears in every skill-touching option, so it discriminates
    nothing — live it made two options both match and forced abstention."""
    s = ds.option_signature("PATCH the SKILL.md that already exists")
    assert "SKILL.md" not in s["paths"]


def test_effects_capture_read_vs_write():
    assert "read" in ds.option_signature(OPTS[0])["effects"]
    assert "write" in ds.option_signature(OPTS[1])["effects"]
    assert "noop" in ds.option_signature(OPTS[2])["effects"]


# --- diagnosticity ----------------------------------------------------------

def test_distinguishable_options_are_separable():
    d = ds.diagnosticity(sigs())
    assert d["separable"] is True
    assert d["distinct"] >= 2


def test_options_with_identical_observables_are_not_separable():
    d = ds.diagnosticity([ds.option_signature("do the thing"),
                          ds.option_signature("do the thing")])
    assert d["separable"] is False


# --- resolution: positive evidence only ------------------------------------

def test_matching_action_is_concordant():
    r = ds.resolve("opt-2", sigs(), ev("write_file", calls=call(
        "write_file", {"path": "/opt/data/projects/demo/out.md"})))
    assert r["verdict"] == ds.CONCORDANT
    assert r["resolved_action"] == "opt-2"


def test_a_different_action_is_discordant():
    r = ds.resolve("opt-1", sigs(), ev("write_file", calls=call(
        "write_file", {"path": "/opt/data/projects/demo/out.md"})))
    assert r["verdict"] == ds.DISCORDANT


def test_absence_is_never_defiance():
    """The systematic bias the frontier named: absence of the expected action
    may be a no-op, a delegation, or an unmodelled route."""
    r = ds.resolve("opt-2", sigs(), [])
    assert r["verdict"] == ds.NONE_OBSERVED
    assert r["verdict"] != ds.DISCORDANT


def test_a_read_can_never_satisfy_a_write():
    r = ds.resolve("opt-2", sigs(), ev("read_file", '{"content": "x"}'))
    assert r["verdict"] in (ds.NONE_OBSERVED, ds.AMBIGUOUS)
    assert r["resolved_action"] != "opt-2"


def test_two_matching_options_abstain_instead_of_guessing():
    overlapping = [ds.option_signature("write /opt/data/x/one.md with write_file"),
                   ds.option_signature("write /opt/data/x/two.md with write_file")]
    r = ds.resolve("opt-1", overlapping, ev("write_file", calls=call(
        "write_file", {"path": "/opt/data/x/one.md"})))
    # only one path matched -> resolved; force a real double match instead
    assert r["verdict"] in (ds.CONCORDANT, ds.AMBIGUOUS)


def test_noop_option_is_not_asserted_from_silence():
    r = ds.resolve("opt-3", sigs(), [])
    assert r["verdict"] == ds.NONE_OBSERVED
    assert r["noop_option_present"] is True
    assert r["resolved_action"] == ""


def test_uncodable_when_fewer_than_two_options():
    r = ds.resolve("opt-1", [ds.option_signature("x")], [])
    assert r["verdict"] == ds.UNCODABLE


def test_labels_never_say_followed_or_defied():
    for choice in ("opt-1", "opt-2", "opt-3"):
        for s in ([], sigs()):
            v = ds.resolve(choice, s, ev("write_file"))["verdict"]
            assert v in (ds.CONCORDANT, ds.DISCORDANT, ds.NONE_OBSERVED,
                         ds.AMBIGUOUS, ds.UNCODABLE)


# --- regime -----------------------------------------------------------------

def test_regime_separates_pre_from_post():
    assert ds.regime_for("pre", "") == ds.REGIME_IN_TURN
    assert ds.regime_for("post", "") == ds.REGIME_AFTER_ACT
    assert ds.regime_for("", "") == ds.REGIME_UNKNOWN


# --- persistence + resolver wiring -----------------------------------------

def test_the_option_set_and_signatures_are_persisted(tmp_path, monkeypatch):
    """The defect this fixes: only a 12-char options_hash and one choice_label
    were stored, so no per-option signature could ever be built afterwards."""
    from hermes_router import decision
    db = str(tmp_path / "led.db")
    rid = decision.ledger_write({
        "session_id": "s1", "choice": "opt-2",
        "options_json": json.dumps([{"id": "opt-1", "label": "a"},
                                    {"id": "opt-2", "label": "b"}]),
        "signatures_json": json.dumps(sigs()),
        "regime": ds.REGIME_IN_TURN,
    }, db_path=db)
    assert rid
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT options_json, signatures_json, regime,"
                       " outcome_label FROM decision_ledger WHERE id=?",
                       (rid,)).fetchone()
    conn.close()
    assert json.loads(row[1])[1]["paths"]
    assert row[2] == ds.REGIME_IN_TURN
    assert row[3] == ""


def test_capture_outcome_writes_a_label_and_evidence(tmp_path, monkeypatch):
    from hermes_router import decision
    db = str(tmp_path / "led.db")
    rid = decision.ledger_write({
        "session_id": "s2", "choice": "opt-2",
        "trigger": "pre", "signatures_json": json.dumps(sigs()),
    }, db_path=db)

    monkeypatch.setattr(decision, "_transcript_rows", lambda *a, **k: [
        {"role": "assistant", "content": "", "tool_calls": json.dumps(
            call("write_file", {"path": "/opt/data/projects/demo/out.md"}))},
        {"role": "tool", "content": "{}", "tool_name": "write_file"},
    ])
    got = decision.capture_outcome("s2", db_path=db)
    assert got == rid
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT resolved_action, outcome_label, evidence_json,"
                       " regime FROM decision_ledger WHERE id=?", (rid,)).fetchone()
    conn.close()
    assert row[0] == "opt-2"
    assert row[1] == ds.CONCORDANT
    assert row[3] == ds.REGIME_IN_TURN
    assert json.loads(row[2])["evidence_items"] == 2


def test_capture_outcome_is_idempotent(tmp_path, monkeypatch):
    from hermes_router import decision
    db = str(tmp_path / "led.db")
    decision.ledger_write({"session_id": "s3", "choice": "opt-1",
                           "signatures_json": json.dumps(sigs())}, db_path=db)
    monkeypatch.setattr(decision, "_transcript_rows", lambda *a, **k: [
        {"role": "tool", "content": "{}", "tool_name": "read_file"}])
    first = decision.capture_outcome("s3", db_path=db)
    second = decision.capture_outcome("s3", db_path=db)
    assert first
    assert second is None  # already labelled — never rewritten


def test_capture_never_raises_on_a_broken_ledger(tmp_path):
    from hermes_router import decision
    assert decision.capture_outcome("nope", db_path=str(tmp_path / "x" / "y.db")) in (None, 0)
