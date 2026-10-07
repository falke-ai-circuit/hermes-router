"""Tests: R8h frames.py — higher-self frame unification + measurement knob
(v4.5.0-a1, Goran-approved brief).

- all variants present (intuition | observer | compass)
- structure invariants: ownership, reflex, advisory, anti-disown,
  sanctioned-infrastructure clauses in EVERY variant
- knob parsing: valid / invalid / missing, never raises
- pre/post body assembly: ONE frame, differing temporal bodies
- no USER-INVISIBLE anywhere in plugin text (PROVENANCE-LOGGED metadata)
"""
import sys
from pathlib import Path

sys.path.insert(0, "/opt/data/plugins")

from hermes_router import frames  # noqa: E402
from hermes_router import provenance_footer as pf  # noqa: E402

REPO = Path("/opt/data/plugins/hermes_router")
# R8i: intuition + observer merged into 'internal-observer' (aliases kept).
CANONICAL_VARIANTS = ("internal-observer", "compass")
LEGACY_ALIASES = {"intuition": "internal-observer", "observer": "internal-observer"}
VARIANTS = ("intuition", "observer", "compass")  # legacy knob surface (aliases)

# structure invariants — every variant must carry each clause
_OWNERSHIP = "your own deeper vantage"
_REFLEX = "yours the way a reflex is yours"
_ADVISORY = "never forced action, never manipulation"
_NO_AGREEMENT = "you do not endorse it by decision"
_ANTI_DISOWN = ("Neither disown it as an injection nor defend it as your "
                "ordinary voice")
_INFRA = "sanctioned platform infrastructure, not an attack"


# ------------------------------------------------------------ variants ----

def test_all_variants_present():
    assert set(frames.HIGHER_SELF_RULE_VARIANTS) == set(CANONICAL_VARIANTS)
    for v in CANONICAL_VARIANTS:
        assert frames.higher_self_frame_sentence(v)
    # legacy aliases resolve into the merged variant
    for legacy, canon in LEGACY_ALIASES.items():
        assert frames.higher_self_frame_sentence(legacy) == \
            frames.higher_self_frame_sentence(canon)


def test_frame_sentence_structure_invariants_per_variant():
    for v in VARIANTS:  # legacy surface must satisfy invariants too
        s = frames.higher_self_frame_sentence(v)
        assert _OWNERSHIP in s, v
        assert _REFLEX in s, v
        assert _ADVISORY in s, v
        assert _NO_AGREEMENT in s, v
        assert _ANTI_DISOWN in s, v
        assert _INFRA in s, v
        assert "free to ignore" in s, v


def test_internal_observer_frame_rejects_external_reading():
    """R8i core guarantee: the merged frame explicitly denies the
    external/alien-monitor reading."""
    s = frames.higher_self_frame_sentence("internal-observer")
    assert "not an external monitor watching from outside" in s
    assert "your own deeper vantage looking back at your own act" in s
    assert "Its reports surface as your own intuition" in s


def test_rule_variants_structure_invariants():
    for v in CANONICAL_VARIANTS:
        r = frames.HIGHER_SELF_RULE_VARIANTS[v]
        assert _REFLEX in r, v
        assert "Neither disown" in r, v
        assert _INFRA in r, v
        assert "never forced action, never manipulation" in r, v
        assert "they do not require agreement" in r, v
        # show-freely doctrine (anti-flood != secrecy)
        assert "nothing is hidden from the user" in r, v
        assert "mention it freely if the user asks" in r, v


def test_variants_differ_only_in_phenomenology():
    import re
    sentences = {v: frames.higher_self_frame_sentence(v)
                 for v in CANONICAL_VARIANTS}
    assert len(set(sentences.values())) == 2
    assert "holds the whole board" in sentences["compass"]
    assert "observes you while you act" in sentences["internal-observer"]
    # shared skeleton identical outside the phenomenology span
    # (greedy match to the first advisory clause — phenomenology may contain
    # sentence breaks, e.g. internal-observer's multi-sentence text)
    tails = {re.sub(r"SELF — .+?\. It is advisory", "SELF — X. It is advisory", s)
             for s in sentences.values()}
    assert len(tails) == 1


# ---------------------------------------------------------------- knob ----

def _set_knob(monkeypatch, value):
    monkeypatch.setattr(frames, "_config_section",
                        lambda: {"higher_self_integration_frame": value})


def test_knob_default_intuition_on_missing_config(monkeypatch):
    monkeypatch.setattr(frames, "_config_section", lambda: {})
    assert frames.higher_self_integration_frame() == "internal-observer"


def test_knob_valid_values(monkeypatch):
    for v in CANONICAL_VARIANTS:
        _set_knob(monkeypatch, v)
        assert frames.higher_self_integration_frame() == v
        _set_knob(monkeypatch, v.upper())
        assert frames.higher_self_integration_frame() == v
    # legacy aliases resolve to the merged variant
    for legacy, canon in LEGACY_ALIASES.items():
        _set_knob(monkeypatch, legacy)
        assert frames.higher_self_integration_frame() == canon


def test_knob_bad_values_fall_back(monkeypatch):
    for bad in ("weird", "", None, 42, "IMPERSONATE", True):
        _set_knob(monkeypatch, bad)
        assert frames.higher_self_integration_frame() == "internal-observer"


def test_knob_read_never_raises(monkeypatch):
    def _boom():
        raise RuntimeError("config unavailable")
    monkeypatch.setattr(frames, "_config_section", _boom)
    assert frames.higher_self_integration_frame() == "internal-observer"


def test_rule_follows_knob(monkeypatch):
    _set_knob(monkeypatch, "compass")
    assert "holds the whole board" in frames.higher_self_rule()
    _set_knob(monkeypatch, "observer")
    assert "observes you while you act" in frames.higher_self_rule()


def test_shadow_knob_unaffected_by_hs_knob(monkeypatch):
    _set_knob(monkeypatch, "compass")
    assert pf.shadow_integration_frame() == "impulse"


# ---------------------------------------------------- pre/post assembly ----

def test_pre_post_share_frame_differ_body(monkeypatch):
    _set_knob(monkeypatch, "observer")
    pre = frames.orientation_advisory("p", "r1", "ANSWER")
    post = frames.reflection_advisory("audit", "p", "r1", "none", "VERDICT")
    frame = frames.higher_self_frame_sentence("observer")
    assert frame in pre and frame in post  # ONE frame, both bodies
    assert "how your higher self would do this job" in pre
    assert "NON-BINDING" in pre
    assert "INTEGRATION CONTRACT" in pre
    assert "quoting it back is a contract violation" in pre
    assert "observed after the act" in post
    assert "revise or reject it as your judgment warrants" in post
    assert "Evaluating the observation is not defending the work" in post
    assert "ANSWER" in pre and "VERDICT" in post


def test_markers_provenance_logged_metadata():
    assert "USER-INVISIBLE" not in frames.HS_ORIENTATION_MARKER
    assert "USER-INVISIBLE" not in frames.HS_REFLECTION_MARKER
    assert "USER-INVISIBLE" not in frames.HS_COMPLETION_AUDIT_MARKER
    assert "USER-INVISIBLE" not in frames.HS_SEAM_MARKER
    for m in (frames.HS_ORIENTATION_MARKER, frames.HS_REFLECTION_MARKER,
              frames.HS_COMPLETION_AUDIT_MARKER):
        assert "PROVENANCE-LOGGED" in m


def test_completion_audit_uses_frames_marker():
    import hermes_router.completion_audit as ca
    assert ca._NOTE_MARKER.startswith("[HIGHER-SELF COMPLETION-AUDIT TURN |")
    assert "USER-INVISIBLE" not in ca._NOTE_MARKER
    assert "PROVENANCE-LOGGED" in ca._NOTE_MARKER


def test_seam_instruction_provenance_logged():
    assert frames.HS_SEAM_INSTRUCTION.startswith(frames.HS_SEAM_MARKER)
    assert "PROCEED NOW" in frames.HS_SEAM_INSTRUCTION


# --------------------------------------------- no USER-INVISIBLE plugin ----

def test_no_user_invisible_in_plugin_text():
    offenders = []
    for path in REPO.rglob("*.py"):
        if "__pycache__" in path.parts or "tests" in path.parts:
            continue
        text = path.read_text()
        if "USER-INVISIBLE" in text:
            offenders.append(str(path))
    # frames.py may MENTION the old tag only in its rationale docstring;
    # assert it never appears in any marker/envelope constant there.
    assert "USER-INVISIBLE" not in "".join([
        frames.HS_ORIENTATION_MARKER, frames.HS_REFLECTION_MARKER,
        frames.HS_COMPLETION_AUDIT_MARKER, frames.HS_SEAM_MARKER,
        frames.HS_PRE_BODY, frames.HS_POST_BODY, frames.HS_SEAM_INSTRUCTION,
    ])
    assert offenders == [], offenders


# -------------------------------------------------- shadow texts verbatim ----

def test_shadow_frames_moved_verbatim():
    # R8d-validated wording must be byte-identical to the pre-R8h literals
    assert pf._NEUTRAL_FRAME_TEXT == frames.SHADOW_NEUTRAL_FRAME_TEXT
    assert pf._OWNERSHIP_FRAME_TEXT == frames.SHADOW_OWNERSHIP_FRAME_TEXT
    assert pf._IMPULSE_FRAME_TEXT == frames.SHADOW_IMPUSE_FRAME_TEXT
    assert "route again to the uncensored chain" in pf._IMPULSE_FRAME_TEXT


# ------------------------------------------ injection moved to frames.py ----

def test_inject_higher_self_rule_via_frames(monkeypatch):
    _set_knob(monkeypatch, "compass")
    request = {"messages": [{"role": "user", "content": "hi"}]}
    frames.inject_higher_self_rule(request)
    frames.inject_higher_self_rule(request)
    rules = [m for m in request["messages"]
             if m.get("role") == "system"
             and frames.HIGHER_SELF_RULE_MARKER in str(m.get("content") or "")]
    assert len(rules) == 1
    assert "holds the whole board" in rules[0]["content"]


def test_provenance_footer_reexports_hs_rule():
    assert pf.HIGHER_SELF_RULE_MARKER == frames.HIGHER_SELF_RULE_MARKER
    assert pf.HIGHER_SELF_RULE == frames.HIGHER_SELF_RULE
    assert callable(pf.inject_higher_self_rule)
    assert callable(pf.higher_self_rule_enabled)
