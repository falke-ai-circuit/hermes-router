"""R57 — standardized POV set: four corners + two gates.

Pins the properties that make the set usable, not the wording:
  - exactly one voice owns each corner (a missing owner makes that direction
    unreachable, which is what happened to wide+relaxed before R56)
  - the dials are a pure function of the claims (no free parameter)
  - silence is a valid output: unclaimed -> None, never 0.0
  - the named voice agrees with the dials it came from
  - gates default to silent and only speak when fired
  - POST deltas are signed and labelled, and quiet when the readings agree
"""

from hermes_router import povs

CORNERS = {(1, -1): "explorer", (-1, 1): "verifier", (1, 1): "skeptic", (-1, -1): "operator"}


def test_every_corner_has_exactly_one_owner():
    owners = {}
    for p in povs.POV_SET:
        key = (p.scope_dir, p.rigour_dir)
        assert key in CORNERS, f"voice {p.name} sits outside a corner: {key}"
        assert key not in owners, f"corner {key} double-owned by {p.name} and {owners[key]}"
        owners[key] = p.name
    assert owners == CORNERS
    assert povs.coverage() == {
        "wide+relaxed": "explorer",
        "narrow+rigorous": "verifier",
        "wide+rigorous": "skeptic",
        "narrow+relaxed": "operator",
    }


def test_dials_are_pure_function_of_claims():
    # one voice claiming alone lands exactly on its corner
    for p in povs.POV_SET:
        d = povs.dials_from_claims({p.name: 1.0})
        assert d["scope"] == float(p.scope_dir)
        assert d["rigour"] == float(p.rigour_dir)


def test_balanced_claims_land_at_zero_not_none():
    d = povs.dials_from_claims({v: 1.0 for v in povs.VOICES})
    assert d == {"scope": 0.0, "rigour": 0.0}


def test_no_claim_is_none_not_zero():
    for empty in ({}, {v: 0.0 for v in povs.VOICES}):
        d = povs.dials_from_claims(empty)
        assert d["scope"] is None and d["rigour"] is None, (
            "nobody spoke must not read as perfectly balanced")


def test_named_voice_agrees_with_its_own_dials():
    # a loud voice against two moderate opposite ones: argmax and mean diverge
    claims = {"explorer": 0.55, "verifier": 0.60, "skeptic": 0.50, "operator": 0.15}
    win = povs.winning_voice(claims)
    d = povs.dials_from_claims(claims)
    corner = povs.corner_of(d["scope"], d["rigour"])
    assert win == CORNERS[(
        (1 if d["scope"] > 0 else -1), (1 if d["rigour"] > 0 else -1))], (
        f"the named voice must own the corner the dials land in ({corner})")
    assert povs.coverage()[corner] == win
    assert povs.strongest_claim(claims) == "verifier"  # diagnostic only


def test_parse_extracts_claims_notes_and_gates():
    text = (
        "explorer: 0.9 | sweep the tree\n"
        "verifier: 0.1 | nothing to make correct\n"
        "skeptic: 0.4 | distrust the README\n"
        "operator: 0.2 | start reading\n"
        "gates: precedent=0 consequence=1\n"
        "Then the plan would be to survey first."
    )
    p = povs.parse_claims(text)
    assert p["claims"] == {"explorer": 0.9, "verifier": 0.1, "skeptic": 0.4, "operator": 0.2}
    assert p["notes"]["skeptic"] == "distrust the README"
    assert p["gates"] == {"consequence": True}


def test_parse_is_fail_open_on_junk():
    assert povs.parse_claims(None)["claims"] == {}
    assert povs.parse_claims("")["claims"] == {}
    assert povs.parse_claims("no structured output at all")["claims"] == {}


def test_claims_are_clamped():
    p = povs.parse_claims("explorer: 4.2 | too much\nverifier: -3 | negative")
    assert p["claims"]["explorer"] == 1.0
    assert p["claims"]["verifier"] == 0.0


def test_block_text_strips_machine_lines():
    text = (
        "explorer: 0.9 | sweep\n"
        "verifier: 0.1 | n/a\n"
        "skeptic: 0.2 | n/a\n"
        "operator: 0.1 | n/a\n"
        "gates: precedent=0 consequence=0\n"
        "The plan: start with a sweep."
    )
    kept = povs.block_text(text)
    assert "explorer:" not in kept and "gates:" not in kept
    assert "The plan: start with a sweep." in kept


def test_gates_silent_by_default():
    assert povs.gate_lines({}) == []
    assert povs.gate_lines({"precedent": False, "consequence": False}) == []
    assert len(povs.gate_lines({"precedent": True})) == 1


def test_stance_silent_inside_band():
    assert povs.stance_lines({"scope": 0.2, "rigour": -0.1}) == []
    loud = povs.stance_lines({"scope": 0.9, "rigour": -0.9})
    assert len(loud) <= povs.MAX_LINES if hasattr(povs, "MAX_LINES") else len(loud) <= 2
    assert any("widen" in ln for ln in loud)


def test_delta_needs_both_sides():
    assert povs.delta_labels({"scope": 0.5}, {"scope": 0.9, "rigour": 0.1}) == {"scope": 0.4}
    assert povs.delta_labels({"scope": None, "rigour": 0.5}, {"scope": 0.5, "rigour": None}) == {}


def test_delta_quiet_when_readings_agree():
    assert povs.delta_labels({"scope": 0.5, "rigour": 0.5},
                             {"scope": 0.55, "rigour": 0.45}) == {}


def test_delta_labelled_in_words():
    lines = povs.reflection_lines({"scope": 0.8, "rigour": -0.5},
                                  {"scope": 0.2, "rigour": 0.1})
    joined = " ".join(lines)
    assert "broader" in joined
    assert "less careful" in joined


def test_pre_prompt_conditions_on_persona_and_context():
    system, msgs = povs.build_pre_prompt("fix the typo", agent="coder",
                                         role="software engineer", context="3 files changed")
    assert "AGENT: coder" in system
    assert "software engineer" in system
    assert "SITUATION SO FAR" in msgs[0]["content"]


def test_post_prompt_reuses_the_same_voices():
    system_pre, _ = povs.build_pre_prompt("x", agent="coder", role="r")
    system_post, _ = povs.build_post_prompt("work done", agent="coder", role="r")
    assert system_pre == system_post, "PRE and POST must speak as the same set"


def test_all_four_corners_are_reachable_from_claims():
    seen = set()
    for p in povs.POV_SET:
        d = povs.dials_from_claims({p.name: 1.0})
        seen.add(povs.corner_of(d["scope"], d["rigour"]))
    assert seen == {"wide+relaxed", "narrow+rigorous", "wide+rigorous", "narrow+relaxed"}


# --- wiring (R57 live path) -------------------------------------------------

def test_split_projection_handles_pov_format():
    text = ("explorer: 0.9 | sweep\nverifier: 0.1 | n/a\n"
            "skeptic: 0.3 | n/a\noperator: 0.1 | n/a\n"
            "gates: precedent=0 consequence=0\nThe plan: sweep first.")
    dials, clean, meta = povs.split_projection(text)
    assert meta["format"] == "pov"
    assert meta["win"] == "explorer"
    assert dials["scope"] > 0 and dials["rigour"] < 0
    assert "explorer:" not in clean and "The plan" in clean


def test_split_projection_falls_back_to_legacy():
    text = 'MINDSET_TARGET {"scope": 0.5, "rigour": -0.5}\nPLAN here'
    dials, clean, meta = povs.split_projection(text)
    assert meta["format"] == "legacy"
    assert dials == {"scope": 0.5, "rigour": -0.5}
    assert "MINDSET_TARGET" not in clean


def test_unparseable_machine_channel_never_ships():
    # the strip guarantee must survive a body that cannot be scored
    dials, clean, meta = povs.split_projection(
        'PLAN\nMINDSET_TARGET {"scope": 3.4, "rigour": 0.2}')
    assert dials is None
    assert "MINDSET_TARGET" not in clean
    assert "PLAN" in clean


def test_split_projection_on_empty():
    assert povs.split_projection(None)[0] is None
    assert povs.split_projection("")[0] is None


def test_park_consume_is_one_shot_and_skips_unclaimed():
    povs.reset_targets()
    povs.park_target("s", {"scope": 0.8, "rigour": -0.5}, {"win": "explorer"})
    assert povs.consume_target("s")["meta"]["win"] == "explorer"
    assert povs.consume_target("s") is None
    povs.park_target("u", {"scope": None, "rigour": None})
    assert povs.consume_target("u") is None, "an unclaimed ask parks nothing"


def test_park_is_bounded():
    povs.reset_targets()
    for i in range(povs._PRE_TARGETS_MAX + 20):
        povs.park_target("s%d" % i, {"scope": 0.5, "rigour": 0.5})
    assert len(povs._PRE_TARGETS) <= povs._PRE_TARGETS_MAX


def test_post_payload_carries_the_same_voices_as_pre():
    from hermes_router import completion_audit

    msgs = completion_audit._audit_payload("ask", "work", "resp", 4000)
    body = msgs[1]["content"]
    for v in povs.VOICES:
        assert v + ":" in body, f"{v} missing from the POST payload"
    assert "gates:" in body


def test_reflection_only_when_target_exists():
    # no PRE target -> no invented comparison
    assert povs.reflection_lines(None, {"scope": 0.9, "rigour": 0.9}) == []
    # both present and far apart -> a correction
    assert povs.reflection_lines({"scope": 0.9, "rigour": 0.9},
                                 {"scope": -0.8, "rigour": -0.8})
