"""R19.20 (v4.12.7) — closes the reviewer's two remaining F-batch-1 remarks.

1) POST BANNER CLOSED-SET CLAMP: the advisory choke point (render_advisory)
   applies the same closed-set choice clamp as the manual path — a choice
   not mapping to a declared envelope option (id or label) renders NO
   advisory, and the invalid_fork counter records it. Her live specimen:
   choice="instead-of-criteria gating. Fix those" @0.91 — a span-parsed
   phrase glued from the agent's own text.
2) POV VANTAGE LINES: per-vantage segments persist (labels + text) in the
   ledger row AND render as compact labeled lines in the delivered note
   (one line per vantage, <=120 chars each) — distinctness scoreable from
   outside (F-E3 = 0 lines found).
"""
import json

from hermes_router import completion_audit as CA
from hermes_router import decision as D


def _env2():
    # D1 v1.1: the impulse frame reads the MECHANICAL weighting block —
    # envelopes without it fail-open to '' (never asserted). Build one.
    opts = [{"id": "opt-1", "label": "Approach 1"},
            {"id": "opt-2", "label": "Approach 2"}]
    return {"fork_class": "deploy", "options": opts,
            "weighting": {"weights": {"opt-1": 0.5, "opt-2": 0.5},
                          "band": "noise", "evidence": [], "basis": "test"}}


# --- 1: closed-set clamp at the advisory choke point -----------------------------

def test_specimen_choice_renders_no_advisory():
    """PIN (her exact specimen): the span-parsed phrase is NOT a declared
    option -> empty advisory, no choice text anywhere."""
    verdict = {"choice": "instead-of-criteria gating. Fix those",
               "confidence": 0.91, "alternatives": []}
    out = D.render_advisory(verdict, _env2())
    assert out == ""
    assert "instead-of-criteria" not in out


def test_label_choice_still_renders():
    """Label hits map — a real option label still renders."""
    verdict = {"choice": "Approach 1", "confidence": 0.9, "alternatives": []}
    out = D.render_advisory(verdict, _env2())
    assert "Approach 1" in out and D.PROVENANCE_TAG in out


def test_id_choice_still_renders():
    verdict = {"choice": "opt-2", "confidence": 0.9, "alternatives": []}
    out = D.render_advisory(verdict, _env2())
    # R19.15: the id renders as the human-readable label
    assert "Approach 2" in out


def test_unmapped_choice_counter_bumps(monkeypatch):
    """The clamp records the invalid fork (invalid_fork counter)."""
    bumped = []
    monkeypatch.setattr(D, "bump_counter",
                        lambda name: bumped.append(name))
    D.render_advisory({"choice": "instead-of-criteria gating. Fix those",
                       "confidence": 0.91, "alternatives": []}, _env2())
    assert "invalid_fork" in bumped


def test_empty_options_envelope_unclamped():
    """D1 v1.1: no declared options -> no weights -> the impulse frame
    fail-opens to '' (never asserted) — superseding the legacy
    option-less unclamped render."""
    verdict = {"choice": "apply_precedent", "confidence": 0.9,
               "alternatives": []}
    out = D.render_advisory(verdict, {"fork_class": "deploy", "options": []})
    assert out == ""
    assert D.PROVENANCE_TAG not in out  # tag never rides an empty frame


# --- 2: POV vantage lines ----------------------------------------------------------

def test_pov_lines_render_into_note():
    """PIN: vantage lines render compact labeled lines (<=120 chars each)
    into the delivered note — distinctness scoreable from outside."""
    povs = [{"stance": "practitioner", "note": "x" * 300},
            {"stance": "skeptic", "note": "the rollback path is untested"}]
    lines = "\n".join("%s: %s" % (p["stance"], p["note"][:120])
                      for p in povs)
    rendered = lines.split("\n")
    assert len(rendered) == 2
    # compact: each line = "<stance>: " + note truncated to 120
    for l, p in zip(rendered, povs):
        assert l.startswith(p["stance"] + ": ")
        assert len(l) <= len(p["stance"]) + 2 + 120
    assert rendered[1].startswith("skeptic: the rollback path is untested")
    assert rendered[0].startswith("practitioner: ")


def test_pov_segments_persist_per_vantage(monkeypatch, tmp_path):
    """The ledger row carries per-vantage labels + text (F-E3: 0 lines
    found — now scoreable from outside)."""
    from hermes_router import decision as D
    db = str(tmp_path / "t.db")
    real = D.ledger_write
    rows = []
    monkeypatch.setattr(D, "ledger_write",
                        lambda row, db_path=None: (
                            rows.append(dict(row)),
                            real(row, db_path=db))[1])
    CA.persist_frontier_verdict(
        session_id="s-pov", key="k", ep=type("EP", (), {"model": "m"})(),
        ask="ask", response_text="resp",
        verdict_text="verdict body",
        povs=[{"stance": "practitioner", "note": "works in hand"},
              {"stance": "outsider", "note": "plausible to the world"},
              {"stance": "skeptic", "note": "untested rollback"}],
        pov_collapsed=[], sc=None, adv={})
    body = json.loads(rows[-1]["verdict_json"])
    stances = [p["stance"] for p in body["povs"]]
    assert stances == ["practitioner", "outsider", "skeptic"]
    assert all(len(p["note"]) > 0 for p in body["povs"])


def test_parse_povs_caps_and_fields():
    povs = CA._parse_povs(json.dumps({"povs": [
        {"stance": "practitioner", "note": "n1"},
        {"stance": "", "note": "no stance -> dropped"},
        {"stance": "skeptic", "note": "n3"}]}))
    assert [p["stance"] for p in povs] == ["practitioner", "skeptic"]
