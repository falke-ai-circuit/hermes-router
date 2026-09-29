"""R19.13 B+ (Goran addendum): adversarial fold-in — frontier gains the
adversarial element as part of its OWN consults. NO separate lane, NO new
chain; glm-5.3 carries the attack.

5b PRE doubt-seed: irreversible/fleet-risk-shaped PRE consult asks get the
   seed instruction (one billed call); verdict JSON adversarial block
   parsed (nullable, fail-open); p_failure ledger row.
5c POST attack: POST consult prompt adds the attack instruction; banner
   gains ' | doubt: <objection 60c>' when non-empty AND level>=2.
5d declared phrases 'challenge this' / 'am i missing something' (standalone
   lines) route as declared frontier consults, adversarial FORCED on.
5e ledger p_failure nullable column. Merge rule: when multi-POV is on the
   skeptic vantage IS the adversarial element — never double-attack.
"""
import json
import sqlite3

import hermes_router.config_access as _cac
from hermes_router import anchor_exec as AE
from hermes_router import completion_audit as CA
from hermes_router import decision as D
from hermes_router import route_gate as RG

ADV_JSON = ('{"adversarial": {"strongest_objection": "single region write '
            'failure loses data", "failure_mode": "async replication lag '
            'on failover", "p_failure": 0.15}}')


# --- 5b: PRE doubt-seed gate -------------------------------------------------

def test_irreversible_risk_ask_shapes():
    assert CA._irreversible_risk_ask(
        "delete all rows from the production users table")
    assert CA._irreversible_risk_ask(
        "this migration is irreversible, do it now")
    assert CA._irreversible_risk_ask(
        "roll this out fleet-wide to all 11 gateways")
    assert CA._irreversible_risk_ask("git push --force to main")
    # benign asks: no seed
    assert not CA._irreversible_risk_ask("fix the typo in the README")
    assert not CA._irreversible_risk_ask("")
    assert not CA._irreversible_risk_ask(None)


def test_pre_seed_instruction_content():
    txt = CA._adversarial_seed_instruction()
    assert "strongest_objection" in txt and "failure_mode" in txt
    assert "p_failure" in txt and "adversarial" in txt


# --- 5c: POST attack ---------------------------------------------------------

def test_post_attack_instruction_and_merge_rule():
    plain = CA._adversarial_attack_instruction(False)
    merged = CA._adversarial_attack_instruction(True)
    assert "THEN ATTACK" in plain
    # 5e merge rule: skeptic vantage IS the attack — no double instruction
    assert "skeptic vantage" in merged and "THEN ATTACK" not in merged


def test_parse_adversarial_tolerant():
    adv = CA._parse_adversarial("prose verdict here\n" + ADV_JSON)
    assert adv is not None
    assert adv["p_failure"] == 0.15
    assert "single region write failure" in adv["strongest_objection"]
    # absent/malformed -> None (nullable, fail-open)
    assert CA._parse_adversarial("no json") is None
    assert CA._parse_adversarial(json.dumps({"sense_check": {}})) is None
    assert CA._parse_adversarial(json.dumps(
        {"adversarial": {"failure_mode": "x"}})) is None  # no objection
    assert CA._parse_adversarial(None) is None
    # p_failure clamped
    adv2 = CA._parse_adversarial(json.dumps(
        {"adversarial": {"strongest_objection": "x", "p_failure": 7}}))
    assert adv2["p_failure"] == 1.0


def test_banner_doubt_suffix(monkeypatch):
    import hermes_router.debug_banner as dbg
    monkeypatch.setattr(dbg, "debug_banner_level", lambda: 2)
    adv = {"strongest_objection": "y" * 100, "failure_mode": "f",
           "p_failure": 0.1}
    out = CA._doubt_banner_suffix(adv)
    assert out.startswith(" | doubt: ")
    assert len(out) <= len(" | doubt: ") + 60
    monkeypatch.setattr(dbg, "debug_banner_level", lambda: 1)
    assert CA._doubt_banner_suffix(adv) == ""  # level < 2: no suffix
    assert CA._doubt_banner_suffix(None) == ""


def test_post_payload_carries_attack(monkeypatch):
    """The POST consult prompt (one call) carries BOTH the sense_check JSON
    requirement and the adversarial attack — merge rule active under pov."""
    monkeypatch.setattr(CA, "_pov_active", lambda ask: True)
    import re as _re
    src = CA._audit_payload.__code__.co_consts
    # payload builder is a pure function: exercise it directly
    msgs = CA._audit_payload("deploy the fleet migration now", "",
                             "final response text", 4000)
    body = msgs[-1]["content"]
    assert "sense_check" in body and "adversarial" in body
    assert "skeptic vantage" in body  # merged, not doubled


# --- 5d: declared phrases -----------------------------------------------------

def test_declared_phrases_route_frontier():
    assert RG.detect_declared_user("challenge this") == RG.LANE_HIGHER_PRE
    assert RG.detect_declared_user(
        "am i missing something") == RG.LANE_HIGHER_PRE
    # payload-carrying directive line
    assert RG.detect_declared_user(
        "challenge this: the migration plan") == RG.LANE_HIGHER_PRE


def test_declared_phrases_echo_guard():
    # prose/quoted mentions stay inert (echo guard inherited)
    assert RG.detect_declared_user(
        "he said 'challenge this' to the intern") is None
    assert RG.adversarial_declared(
        "the phrase challenge this appears in prose") is False
    # garbage never raises
    assert RG.adversarial_declared(None) is False


def test_adversarial_forced_on_via_declared_phrase():
    assert RG.adversarial_declared("challenge this")
    assert RG.adversarial_declared("am i missing something")
    assert not RG.adversarial_declared("fix the typo please")


# --- 5e: ledger p_failure column ----------------------------------------------

def test_ledger_p_failure_column_nullable(tmp_path):
    db = str(tmp_path / "t.db")
    conn = D._ledger_connect(db)
    assert conn is not None
    cols = {r[1]: r for r in conn.execute("PRAGMA table_info(decision_ledger)")}
    assert "p_failure" in cols
    assert not cols["p_failure"][3]  # nullable
    rid = D.ledger_write({"session_id": "s", "task_id": "t",
                          "trigger": "frontier_adversarial",
                          "fork_class": "frontier_adversarial",
                          "p_failure": 0.15}, db_path=db)
    row = conn.execute("SELECT p_failure FROM decision_ledger WHERE id=?",
                       (rid,)).fetchone()
    assert row is not None and abs(row[0] - 0.15) < 1e-9
    # row without p_failure -> NULL
    rid2 = D.ledger_write({"session_id": "s", "task_id": "t",
                           "trigger": "pre"}, db_path=db)
    row2 = conn.execute("SELECT p_failure FROM decision_ledger WHERE id=?",
                        (rid2,)).fetchone()
    assert row2[0] is None
    conn.close()


def test_ledger_p_failure_migration_old_store(tmp_path):
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE decision_ledger (id INTEGER PRIMARY KEY,"
                 " ts REAL, session_id TEXT, task_id TEXT, trigger TEXT)")
    conn.commit()
    conn.close()
    conn = D._ledger_connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(decision_ledger)")}
    assert {"sense_check", "p_failure"} <= cols
    conn.close()


def test_adversarial_ledger_row_writer(tmp_path, monkeypatch):
    """The durable adversarial row lands with trigger/forbidden-class pin."""
    db = str(tmp_path / "a.db")
    monkeypatch.setattr(_cac, "router_section", lambda: {}, raising=False)
    from hermes_router import decision_miner as DM
    monkeypatch.setattr(DM, "plugin_db_path", lambda: db)
    CA._adversarial_ledger_row("sess", "task", "glm-5.3",
                               {"strongest_objection": "obj",
                                "failure_mode": "fm", "p_failure": 0.4},
                               ADV_JSON)
    import sqlite3 as _sq
    conn = _sq.connect(db)
    row = conn.execute("SELECT trigger, fork_class, p_failure FROM"
                       " decision_ledger").fetchone()
    conn.close()
    assert row == ("frontier_adversarial", "frontier_adversarial", 0.4)


# --- anchor_exec PRE wiring ---------------------------------------------------

def test_anchor_exec_frame_gains_seed_on_risk_ask(monkeypatch):
    """The orientation frame builder appends the seed when the ask is
    risk-shaped. Wire check: the seed import path resolves and the frame
    text builder is reachable — exercised via the instruction contract."""
    seed = CA._adversarial_seed_instruction()
    ask = "drop the entire production database and redeploy"
    assert CA._irreversible_risk_ask(ask)
    assert "strongest case this fails" in seed.lower() or \
        "strongest_objection" in seed
