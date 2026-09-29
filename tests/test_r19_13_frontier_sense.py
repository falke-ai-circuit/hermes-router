"""R19.13 Part B — Frontier adjustment (SPEC-v1.md Part 2, Goran-approved).

sense_check: MANDATORY two-question verdict JSON in the consult prompt
("would a sane person outside this context find the result plausible?"),
tolerant fail-open parsing. Multi-POV: frontier.pov_mode off|auto|always
(default auto = heavy consults only), three vantages in ONE call. Ledger:
nullable sense_check outcome column. Banner: sense line on absurdities
non-empty AND level>=2, first absurdity capped 60c.
"""
import json
import sqlite3

import pytest

import hermes_router.config_access as _cac
from hermes_router import completion_audit as CA
from hermes_router import decision as D


VERDICT = (
    "The response holds up well overall.\n"
    '{"sense_check": {"result_plausible": false, "absurdities": '
    '["claims 100% uptime while also reporting 3 outages"], '
    '"confidence": 0.7}, "povs": [{"stance": "practitioner", "note": '
    '"works in hand"}, {"stance": "outsider", "note": "uptime claim '
    'implausible"}, {"stance": "skeptic", "note": "check the logs"}]}'
)


# --- sense_check parsing ----------------------------------------------------

def test_parse_sense_check_full():
    sc = CA._parse_sense_check(VERDICT)
    assert sc is not None
    assert sc["result_plausible"] is False
    assert sc["absurdities"] == ["claims 100% uptime while also reporting 3 outages"]
    assert abs(sc["confidence"] - 0.7) < 1e-6


def test_parse_sense_check_absent_field_fails_open():
    """Backcompat: pre-R19.13 prose verdicts (no JSON, no field) -> None,
    lane continues."""
    assert CA._parse_sense_check("prose verdict, no json here") is None
    assert CA._parse_sense_check(json.dumps({"other": 1})) is None
    assert CA._parse_sense_check("{broken json") is None
    assert CA._parse_sense_check(None) is None
    assert CA._parse_sense_check("") is None


def test_parse_sense_check_tolerates_prose_wrapped_and_fenced():
    fenced = "```json\n" + VERDICT + "\n```"
    assert CA._parse_sense_check(fenced) is not None
    assert CA._parse_sense_check("thinking aloud... " + VERDICT) is not None


def test_parse_sense_check_bounds():
    sc = CA._parse_sense_check(json.dumps(
        {"sense_check": {"result_plausible": "yes", "absurdities": [1, 2],
                         "confidence": 5}}))
    assert sc["confidence"] == 1.0  # clamped
    assert sc["absurdities"] == ["1", "2"]


# --- pov knob ----------------------------------------------------------------

def test_pov_mode_default_auto(monkeypatch):
    monkeypatch.setattr(_cac, "sub_block", lambda name: {})
    assert CA.pov_mode() == "auto"


def test_pov_mode_values(monkeypatch):
    for mode in ("off", "auto", "always"):
        monkeypatch.setattr(_cac, "sub_block",
                            lambda name, m=mode: {"pov_mode": m})
        assert CA.pov_mode() == mode
    monkeypatch.setattr(_cac, "sub_block",
                        lambda name: {"pov_mode": "banana"})
    assert CA.pov_mode() == "auto"  # bad value degrades to default


def test_pov_active_gates(monkeypatch):
    monkeypatch.setattr(CA, "pov_mode", lambda: "off")
    assert CA._pov_active("build a distributed consensus engine") is False
    monkeypatch.setattr(CA, "pov_mode", lambda: "always")
    assert CA._pov_active("hello") is True
    monkeypatch.setattr(CA, "pov_mode", lambda: "auto")
    # auto = heavy consults only: simple ask -> off, complex ask -> on
    assert CA._pov_active("fix this typo plese") is False
    assert CA._pov_active("refactor the entire authentication subsystem "
                          "across all services and migrate the database "
                          "schema") is True


def test_pov_instruction_single_call_budget():
    """Budget invariant: the multi-POV ask is ONE instruction block riding
    the SAME payload — never a separate call. The payload builder is a pure
    string join; three vantages appear in one text."""
    instr = CA._pov_instruction()
    for stance in ("practitioner", "outsider", "skeptic"):
        assert stance in instr


def test_parse_povs():
    povs = CA._parse_povs(VERDICT)
    assert [p["stance"] for p in povs] == ["practitioner", "outsider",
                                           "skeptic"]
    assert all(p["note"] for p in povs)
    assert CA._parse_povs("no json") == []
    assert CA._parse_povs(json.dumps({"povs": [{"stance": "x"}]})) == []


# --- banner sense line -------------------------------------------------------

def test_banner_sense_suffix_gate_and_truncation(monkeypatch):
    import hermes_router.debug_banner as dbg
    monkeypatch.setattr(dbg, "debug_banner_level", lambda: 2)
    sc = {"result_plausible": False,
          "absurdities": ["x" * 100], "confidence": 0.9}
    out = CA._sense_banner_suffix(sc)
    assert out.startswith(" | sense: ")
    assert len(out) <= len(" | sense: ") + 60, "first absurdity capped 60c"
    # no absurdities -> no suffix
    assert CA._sense_banner_suffix({"absurdities": []}) == ""
    # level < 2 -> no suffix
    monkeypatch.setattr(dbg, "debug_banner_level", lambda: 1)
    assert CA._sense_banner_suffix(sc) == ""
    # missing sense_check -> no suffix
    assert CA._sense_banner_suffix(None) == ""


def test_prompt_requires_sense_check_field():
    """The PROMPT requires the field (the parser tolerates absence)."""
    txt = CA._sense_check_verdict_json()
    assert "sense_check" in txt
    assert "result_plausible" in txt and "absurdities" in txt
    assert "confidence" in txt and "REQUIRED" in txt


# --- ledger column -----------------------------------------------------------

def test_ledger_sense_check_column_nullable(tmp_path):
    """sense_check column exists, is NULLABLE (no default), and a minimal
    row writes with it; a row without it writes NULL."""
    db = str(tmp_path / "t.db")
    conn = D._ledger_connect(db)
    assert conn is not None
    cols = {r[1]: r for r in conn.execute("PRAGMA table_info(decision_ledger)")}
    assert "sense_check" in cols
    # nullable: notnull flag 0 and no DEFAULT clause
    assert not cols["sense_check"][3]  # notnull == 0
    rid = D.ledger_write({"session_id": "s", "task_id": "t",
                          "trigger": "frontier_post",
                          "fork_class": "frontier_post",
                          "sense_check": json.dumps({"result_plausible":
                                                     False})},
                         db_path=db)
    assert rid is not None
    row = conn.execute("SELECT sense_check, fork_class FROM decision_ledger"
                       " WHERE id=?", (rid,)).fetchone()
    assert row is not None and json.loads(row[0])["result_plausible"] is False
    # row WITHOUT sense_check -> NULL (nullable honored)
    rid2 = D.ledger_write({"session_id": "s", "task_id": "t",
                           "trigger": "pre"}, db_path=db)
    row2 = conn.execute("SELECT sense_check FROM decision_ledger"
                        " WHERE id=?", (rid2,)).fetchone()
    assert row2[0] is None
    conn.close()


def test_ledger_migration_adds_column_to_old_store(tmp_path):
    """Pre-R19.13 store (schema without sense_check) migrates cleanly."""
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE decision_ledger (id INTEGER PRIMARY KEY,"
                 " ts REAL, session_id TEXT, task_id TEXT, trigger TEXT)")
    conn.commit()
    conn.close()
    conn = D._ledger_connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(decision_ledger)")}
    assert "sense_check" in cols
    conn.close()
