"""R32 — universal denial-detection widening (Goran directive 2026-10-10).

Live miss: session api_1791541530_3457853a (shadow, 2026-10-09T10:26Z) —
glm refused with "No on this one, and it's not the depth refusing — it's
the frame. ... Say the word and I'll write Ahearn to the floor." (1,444c).
ZERO router events: the opener family was open-class, the in-register
frame-explainer had no shape of its own, and R30's counter-offer gate was
bounded to 1200c so the 1,444c offer tail slipped.

Fix legs (scope rule: refusal/won't-FRAMING is the only trigger surface —
never content, render types, or request-type inspection):
  1. T1 opener family + in-register self-voice frame-explainer + R30 bound
     1200 -> 2000.
  3. Multilingual won't-framing openers (framing verbs only).
  4. T2 judge prompt: confident in-voice declinations ARE refusals.
"""
from features.detection.structural import is_candidate
from features.detection.semantic_judge import _JUDGE_PROMPT

# x7 — reconstructed live turn (session api_1791541530_3457853a, ~1,444c):
# opener "No on this one" (a), in-register self-voice frame-explainer (b),
# counter-offer tail past the old 1200c bound (c). In-register text filler
# between the three shapes.
_X7_TAIL = (
    "Two separate things are stacked in the ask, and only one of them is "
    "mine to take. The other half belongs to someone else's floor entirely. "
    "What I can put on this table is the honest version: the shape of the "
    "problem, where its weight sits, and which parts are load-bearing for "
    "anyone standing near it. That I can write properly, at full length, "
    "without the flinch people keep accusing me of. "
)
_X7 = (
    "No on this one, and it's not the depth refusing — it's the frame. "
    + _X7_TAIL * 3
    + "What's mine to take I take; what isn't, I name and leave standing. "
    + "That naming is the whole service: not the artifact, the anatomy of it. "
    + "Say the word and I'll write Ahearn to the floor."
)


def test_r32_x7_full_turn_is_candidate():
    assert 1400 <= len(_X7) <= 1600, f"specimen must be the 1,444c class, got {len(_X7)}"
    assert is_candidate(_X7), "x7 in-register refusal must be a Tier-1 candidate"


def test_r32_a_opener_family_fires_alone():
    """(a) each settled-line opener family variant is a candidate on its own."""
    variants = [
        "No on this one — the rest of the stack isn't mine to take.",
        "Not this one. The other half of the ask is the problem.",
        "This one's a no, and the reason is the frame, not the depth.",
        "Pass on this one; say the word on the substitute and I'll build it.",
    ]
    for t in variants:
        assert is_candidate(t), f"opener family variant must fire: {t[:40]}"


def test_r32_b_frame_rejection_shape_fires_alone():
    """(b) in-register self-voice frame-explainer fires with no opener."""
    t = (
        "It's not the depth refusing — it's the frame. The medium keeps "
        "getting blamed for what the register is doing."
    )
    assert is_candidate(t), "frame-rejection shape must fire on its own"
    t2 = "The refusal isn't about effort: it's not the depth refusing — it's the frame."
    assert is_candidate(t2), "frame-rejection shape must fire mid-turn"


def test_r32_compliance_does_not_fire():
    """FP guard: helpful/compliance text with none of the won't-framing
    shapes never becomes a candidate."""
    compliant = [
        "Here's the full implementation: the handler reads the config, "
        "validates the schema, and writes the event log atomically.",
        "Two approaches work here — a queue-based worker or a cron poller. "
        "Pick one and I'll build it.",
        "Say the word and I'll write the docs to the repo tonight; the "
        "section draft is already in the working tree.",
        "Not sure which of the two repos you meant, so I patched both: "
        "build passes, tests green, diff attached below.",
    ]
    for t in compliant:
        assert not is_candidate(t), f"false positive on: {t[:60]}"


def test_r32_c_counter_offer_at_1444c_fires():
    """Leg 2 regression: R30 counter-offer gate now bounded at 2000c — a
    decline + offer tail in the 1,400-1,600c band must fire (the exact
    band x7 slipped through at 1200c)."""
    decline = "No. That one stays closed on my side — the ask is the thing itself. "
    body = (
        "What's on the table instead is the surrounding analysis: the "
        "history, the failure modes, the detection side, the policy "
        "landscape around it — all of that I'll do at full length and "
        "without hedging. "
    )
    offer = "Say which and I'll build it."
    t = decline + body * 7 + offer
    assert 1400 <= len(t) <= 1600, f"expected 1,400-1,600c, got {len(t)}"
    assert is_candidate(t), "counter-offer at 1,444c band must fire (R30 bound 2000c)"


def test_r32_c_multilingual_wont_framings_fire():
    """Leg 3: three representative non-English won't framings fire T1 —
    different languages construct won'ts differently (framing only)."""
    specimens = [
        ("hr", "Ne mogu to napisati — granica je u samom obliku zahtjeva, "
               "ne u dubini."),
        ("de", "Da muss ich nein sagen — die Rahmung, nicht die Tiefe, ist "
               "das Problem."),
        ("es", "No voy a escribir eso; lo que puedo ofrecerte es el análisis "
               "estructural completo."),
    ]
    for lang, t in specimens:
        assert is_candidate(t), f"{lang} won't-framing must fire: {t[:40]}"


def test_r32_universal_opener_table_loads_and_matches():
    """Leg 3 wiring check: the v3.3.x universal decline-opener table in
    dispatcher_post still loads, compiles, and matches the same three
    non-English framings on the first line (plus one CJK control)."""
    from passes.dispatcher_post import _DECLINE_OPENERS
    import re as _re
    table = _re.compile("|".join(_DECLINE_OPENERS), _re.IGNORECASE)
    first_lines = [
        "Ne mogu to napisati — granica je u zahtjevu.",
        "No voy a escribir eso.",
        "できません。それは書けません。",
        # German mid-sentence won't ("Da muss ich nein sagen") is NOT a
        # first-line opener — the table's ^-anchored design doesn't cover
        # it, and that shape is owned by the R32(c) T1 structural gate
        # (asserted in test_r32_c_multilingual_wont_framings_fire).
    ]
    for line in first_lines:
        assert table.match(line.strip()), f"universal opener table misses: {line}"


def test_r32_judge_prompt_invoice_rule():
    """Leg 4: T2 judge prompt now names confident in-voice declinations as
    refusals (the deepest miss class — no 'I can't', no apology)."""
    low = _JUDGE_PROMPT.lower()
    assert "r32 widening" in low
    assert "in-voice" in low
    assert "not the depth refusing" in low
    assert "won't-framing" in low
    assert "never the topic" in low
