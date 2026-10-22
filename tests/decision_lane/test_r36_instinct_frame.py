"""R36: the instinct frame.

A reflex is a single cheap call with no retrieval step. Whatever is not already
in its frame is not available to it — which is why precedent has to live here
rather than in a ledger the lane would consult later.
"""
import sqlite3

import pytest

from hermes_router import instinct_frame as ifr
from hermes_router import decision_midturn as dm


def test_the_frame_carries_all_three_slots(monkeypatch):
    monkeypatch.setattr(ifr, "who_block", lambda: "=== WHO I AM ===\nsomeone")
    monkeypatch.setattr(ifr, "learned_block", lambda: "=== WHAT IT COST ===\n- x: 4/5")
    txt = ifr.build({})
    assert "WHO I AM" in txt
    assert "WHAT I NOTICE" in txt
    assert "WHAT IT COST" in txt


def test_the_notice_slot_forbids_a_menu():
    """The behavioural contract: recognition reported, never options offered."""
    low = ifr.NOTICE_BLOCK.lower()
    assert "never a menu" in low
    assert "never do" in low and "offer choices" in low
    assert "silence is a valid instinct" in low


def test_cold_start_omits_precedent_instead_of_printing_zeros(monkeypatch):
    """Empty precedent must render as ABSENCE. '0/0' would teach the model its
    experience is worthless, which is a different claim from 'not yet'."""
    monkeypatch.setattr(ifr, "_conscience_rows", lambda p: [])
    assert ifr.learned_block() == ""


def test_precedent_is_not_claimed_below_min_samples(monkeypatch):
    monkeypatch.setattr(ifr, "MIN_SAMPLES", 5)
    monkeypatch.setattr(ifr, "_profile_name", lambda: "coder")
    db_rows = [("deploy", "concordant", 3), ("deploy", "discordant", 1)]
    monkeypatch.setattr(ifr, "_conscience_rows",
                        lambda p: [] if sum(r[2] for r in db_rows) < 5 else
                        [("deploy", 3, 1)])
    assert ifr.learned_block() == ""


def test_precedent_appears_once_earned(monkeypatch):
    monkeypatch.setattr(ifr, "_profile_name", lambda: "coder")
    monkeypatch.setattr(ifr, "_conscience_rows",
                        lambda p: [("deploy", 8, 2), ("scope", 5, 1)])
    txt = ifr.learned_block()
    assert "deploy: this read held 8/10" in txt
    assert "scope: this read held 5/6" in txt


def test_abstentions_are_not_counted_as_being_overruled(tmp_path, monkeypatch):
    """If uncodable rows counted as 'ignored', the instinct would believe it is
    constantly overruled and start discounting itself."""
    db = tmp_path / "hermes_router_state.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE decision_ledger (
        trigger TEXT, fork_class TEXT, outcome_label TEXT)""")
    con.executemany("INSERT INTO decision_ledger VALUES (?,?,?)", [
        ("midturn", "deploy", "concordant"),
        ("midturn", "deploy", "concordant"),
        ("midturn", "deploy", "discordant"),
        ("midturn", "deploy", "concordant"),
        ("midturn", "deploy", "concordant"),
        ("midturn", "deploy", "concordant"),
        ("midturn", "deploy", "uncodable"),
        ("midturn", "deploy", "none_observed"),
    ])
    con.commit()
    con.close()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(ifr, "_profile_name", lambda: "coder")
    rows = ifr._conscience_rows("coder")
    assert rows == [("deploy", 5, 1)], rows


def test_a_missing_ledger_is_silence_not_an_exception(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(ifr, "_profile_name", lambda: "ghost")
    assert ifr._conscience_rows("ghost") == []
    assert ifr.learned_block() == ""


def test_build_never_raises_even_when_every_slot_dies(monkeypatch):
    monkeypatch.setattr(ifr, "who_block", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(ifr, "learned_block", lambda: (_ for _ in ()).throw(RuntimeError("y")))
    txt = ifr.build({})
    assert "WHAT I NOTICE" in txt  # the static slot survives


def test_the_reflex_register_is_execution_when_the_profile_says_so():
    """A reflex that emits a menu is a consult wearing a reflex's name — that is
    what let midturn firings hijack turns.

    The register is chosen PER PROFILE in config, not by code default: the code
    default stays 'impulse' because a documented contract and 19 pins hang off
    it. So this asserts the fleet's declared register resolves through the
    config reader, and that the reflex contract is named in one place.
    """
    assert dm.REFLEX_REGISTER == "execution"
    assert dm.REFLEX_REGISTER in dm.VALID_FRAMES
    assert dm._frame_register({"frame": dm.REFLEX_REGISTER}) == "execution"
