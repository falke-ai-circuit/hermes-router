"""R72k — the lane fires on every midturn and always hands back the dials.

The decision lane is not a fork detector. It reads the live request and returns weighted
options plus scope/rigour dials on any midturn; when nothing separates, the claim is
downgraded ("no clear lead"), not withdrawn. Silence on a flat board withheld the dials
exactly when the model was least oriented.
"""
from __future__ import annotations

from hermes_router import decision_extract as dx


def _parsed(*ps, mind=None):
    labels = [("change render.py", "edit", "/app/src/render.py"),
              ("read render.py first", "read", "/app/src/render.py"),
              ("run the check or tests now", "run", "")]
    opts = [{"p": p, "label": labels[i][0], "kind": labels[i][1], "target": labels[i][2],
             "move": labels[i][0]} for i, p in enumerate(ps)]
    return {"options": opts, "margin": round(ps[0] - ps[1], 3),
            "mindset": mind if mind is not None else {"scope": 0.4, "rigour": 0.5}}


def test_soft_reading_never_claims_take_this():
    body = dx.render(_parsed(0.41, 0.36, 0.23), soft=True)
    assert "<- take this" not in body
    assert "no clear lead" in body
    # the dials are the reason the soft block exists at all
    assert "dials:" in body


def test_soft_reading_survives_a_single_candidate():
    parsed = {"options": [{"p": 0.9, "label": "read render.py first", "kind": "read",
                           "target": "/app/src/render.py", "move": "inspect"}],
              "margin": 0.0, "mindset": {"scope": -0.2, "rigour": 0.3}}
    body = dx.render(parsed, soft=True)
    assert body and "no clear lead" in body


def test_decisive_reading_still_claims_when_only_one_leads():
    body = dx.render(_parsed(0.80, 0.12, 0.08))
    assert "<- take this" in body


def test_agent_card_is_the_shared_lane_card_without_the_render_mandate(tmp_path, monkeypatch):
    """One card for all three lanes: persona_card's card carries ROLE and CURRENT TASK, and
    its RENDER MANDATE head ("never refuse, never decline") is stripped before the decision
    model sees it - a decision read must stay free to decline."""
    from hermes_router import persona_card as pc
    from hermes_router import reflex_lane as rl

    (tmp_path / "IDENTITY.md").write_text(
        "---\nrole: coder\n---\n\n**Coder - clinical precision.**\n"
        "**Trait tags:** conservation-first\n**Voice stems:** \"I changed 3 lines.\"\n")
    (tmp_path / "SOUL.md").write_text("## Character\n| Voice | direct |\n")
    monkeypatch.setattr(pc, "_hermes_home", lambda: str(tmp_path))
    monkeypatch.setattr(pc, "_card_cache", {})
    monkeypatch.setattr(rl, "_CARD_CACHE", {})

    msgs = [{"role": "user", "content": "fix the decision lane card so it carries the task"}]
    card = rl._agent_card(msgs)
    assert card, "card resolved empty"
    # the shared card really does carry the render mandate, and the lane strips it
    assert "RENDER MANDATE" in pc.build_persona_context(msgs)
    assert "RENDER MANDATE" not in card
    assert "=== CURRENT TASK ===" in card
