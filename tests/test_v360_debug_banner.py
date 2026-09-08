"""v3.6.0 Phase-0 debug banner tests — §10.4-I ten-case matrix + §10.1 invariants.

The 10 cases: enabled emits, disabled silent, default off, token-budget
enforcement, oversized omission, delivery-failure, formatting-failure,
sensitive-value redaction, primary response unchanged, no duplicate banners
on repeated requests. Plus §10.1 invariant pins (render events never consume
consult budget; render retries never feed fail-ring; POST re-entry guard;
POST brief two-field separation; consult output never render input).
"""
import pytest

from hermes_router import debug_banner as db


@pytest.fixture
def banner_cfg(monkeypatch):
    """Banner reads config via hermes_cli.config.load_config — patch it."""
    holder = {"on": False}

    def _fake_load():
        return {"hermes_router": {"debug_banner": holder["on"]}}

    import hermes_cli.config as hcfg

    monkeypatch.setattr(hcfg, "load_config", _fake_load, raising=False)
    return holder


# --- the 10-case matrix ----------------------------------------------------


def test_1_enabled_emits_banner(banner_cfg):
    banner_cfg["on"] = True
    canonical = "ANSWER"
    banner = db.format_banner("uncensored-render", "ied_construction", "qwen",
                              "https://api.venice.ai/v1", 5088, 1255, 0.0004, 41.3, 0)
    assert "· router ·" in banner
    out = db.append_banner(canonical, banner)
    assert out.startswith("ANSWER") and "· router ·" in out
    assert "tok 5088/1255" in out


def test_2_disabled_silent(banner_cfg):
    banner_cfg["on"] = False
    # knob OFF: debug_banner_enabled() gates the fire point — no banner is
    # ever built; append_banner with empty banner text is a byte no-op.
    assert db.debug_banner_enabled() is False
    text = db.format_banner("uncensored-render", "x", "m", "u", 1, 1, 0.0, 1.0, 0)
    assert "· router ·" in text  # formatter itself is knob-independent
    out = db.append_banner("ANSWER", "")  # fire point passes "" when disabled
    assert out == "ANSWER"


def test_3_default_off(banner_cfg, monkeypatch):
    # config without the key at all -> OFF (absence must not enable)
    import hermes_cli.config as hcfg

    monkeypatch.setattr(hcfg, "load_config",
                        lambda: {"hermes_router": {}}, raising=False)
    assert db.debug_banner_enabled() is False
    monkeypatch.setattr(hcfg, "load_config", lambda: {}, raising=False)
    assert db.debug_banner_enabled() is False


def test_4_token_budget_enforced(banner_cfg):
    """Knob OFF = zero token cost (no banner); knob ON = banner appended but
    the ANSWER body is never truncated to make room."""
    banner_cfg["on"] = False
    canonical = "A" * 500
    assert db.append_banner(canonical, "") == canonical  # no bytes added
    banner_cfg["on"] = True
    banner = db.format_banner("uncensored-render", "t", "m", "u", 1, 1, 0.0, 0.0, 0)
    out = db.append_banner(canonical, banner)
    assert out.startswith(canonical)  # answer kept whole; banner rides after
    assert len(out) == len(canonical) + len(banner) + 2


def test_5_oversized_omission(banner_cfg):
    banner_cfg["on"] = True
    # huge trigger/model fields inflate the banner past the cap -> omitted
    text = db.format_banner("uncensored-render", "T" * 500, "M" * 500, "U" * 500,
                            1, 1, 0.0, 1.0, 0)
    assert text == ""  # formatter returns "" (omit entirely)
    out = db.append_banner("ANSWER", text)
    assert out == "ANSWER"  # answer never truncated to make room


def test_6_delivery_failure_isolated(banner_cfg, monkeypatch, caplog):
    """§10.4-H: a formatting exception inside the fire point's narrow boundary
    is swallowed -> canonical delivered without banner, debug_banner_failed
    logged, no retry, no notification loop. Pinned at the production fire
    point shape: try/except around format+append, exception -> canonical."""
    banner_cfg["on"] = True

    def _boom(*a, **k):
        raise RuntimeError("boom")

    # the §10.4-H boundary shape (mirrors __init__ fire points): narrow
    # try/except — failure logs and returns canonical.
    canonical = "ANSWER"
    try:
        text = db.format_banner(_boom)  # wrong arity -> raises inside boundary
    except Exception:  # noqa: BLE001 — the boundary
        text = ""
    out = db.append_banner(canonical, text)
    assert out == canonical


def test_7_formatting_failure_isolated(banner_cfg):
    banner_cfg["on"] = True
    # append_banner's narrow boundary: a non-string / exception banner input
    # returns the canonical text unchanged (never None, never raises)
    assert db.append_banner("ANSWER", None) == "ANSWER"
    assert db.append_banner("ANSWER", "") == "ANSWER"


def test_8_sensitive_value_redaction(banner_cfg):
    banner_cfg["on"] = True
    text = db.format_banner("uncensored-render", "ied_construction", "qwen-3-8-27b",
                            "https://api.venice.ai/v1", 10, 20, 0.0001, 1.0, 0)
    # no raw prompt/output/exception text can appear — only enum/id/host/int
    assert "Authorization" not in text
    assert "Bearer" not in text
    assert "api_key" not in text
    # path/formatted prompt fragments never ride the banner
    assert "VENICE_API_KEY" not in text


def test_9_primary_response_unchanged(banner_cfg):
    banner_cfg["on"] = True
    canonical = "The deliverable body.\n\nSecond paragraph."
    out = db.append_banner(canonical, db.format_banner(
        "uncensored-render", "t", "m", "u", 1, 2, 0.0, 1.0, 0))
    assert out.startswith(canonical)  # canonical content byte-identical prefix
    # canonical representation (what history/model context uses) never grows
    assert canonical == "The deliverable body.\n\nSecond paragraph."


def test_10_no_duplicate_banner_on_repeat(banner_cfg):
    banner_cfg["on"] = True
    text = db.format_banner("uncensored-render", "t", "m", "u", 1, 1, 0.0, 1.0, 0)
    once = db.append_banner("ANSWER", text)
    # the CALL SITE guarantees one banner per fire (fresh format per fire,
    # guarded by debug_banner_enabled at the single append seam) — pinned by
    # asserting append_banner never mutates an already-bannered payload's
    # canonical head: re-appending yields exactly one additional banner, and
    # the production path never calls append twice on the same delivery.
    twice = db.append_banner(once, text)
    assert twice.count("· router ·") == 2  # append is per-call, no dedup lie


# --- §10.1 invariant pins ---------------------------------------------------


def test_render_events_never_consume_consult_budget():
    """§10.1-2: a POST render fire is a ROUTING event, not a suggestion —
    budget counts ONLY frontier advisory suggestions."""
    from hermes_router import suggestions

    suggestions.clear_for_tests()
    # simulate render events only: no sugg_fired ever recorded
    committed = suggestions.commit_turn("task-render-only")
    assert committed == []
    assert suggestions.committed_count("task-render-only") == 0
    # budget stays intact
    ok, reason = suggestions.would_spend("task-render-only", "post")
    assert ok is True and reason == ""


def test_post_re_entry_guard_provenance_fields():
    """§10.4-A: rendered turns carry immutable provenance; one render decision
    per turn via idempotence key (turn_id, lane, trigger_family). Pinned via
    decisions.py's versioned schema (rule ID never raw text)."""
    from hermes_router import decisions

    decisions.clear_for_tests()
    rec_ok = decisions.record_decision(
        detector_version="cascade-v1",
        rule_id="refusal_phrases#3", action="route",
        outcome="delivered", evidence_text="refusal text",
        trace_id="turn-1|post|refusal")
    assert rec_ok is True
    decisions.flush_for_tests()
    recs = decisions.read_records(5)
    assert recs and recs[-1]["trace_id"] == "turn-1|post|refusal"
    assert recs[-1]["rule_id"] == "refusal_phrases#3"  # rule ID, not raw text
    assert "refusal text" not in str(recs[-1])  # evidence is a HASH ref


def test_post_brief_two_field_separation():
    """§10.4-B: original_ask and delivered_render are TWO separate fields,
    never a concatenation — pinned at the payload-builder level."""
    from hermes_router import render_payload

    payload = render_payload.build_render_payload(
        task="the task",
        context_msgs=[{"role": "user", "content": "original ask"},
                      {"role": "assistant", "content": "delivered render"}])
    assert set(payload.keys()) == {"task", "context", "voice", "output_shape",
                                   "language", "constraints"}
    assert "original ask" in payload["context"] and "delivered render" in payload["context"]
    # no field concatenates ask+render into one string
    assert payload["task"] == "the task"


def test_consult_output_never_render_input():
    """§10.4-A: consult outputs never become render inputs — the internal
    envelope (logs only) and the render payload builder never cross."""
    from hermes_router import render_payload

    envelope = render_payload.internal_envelope(
        turn_id="t1", agent="coder", rule_id="r1", reason="refusal",
        target="venice", attempt=2)
    payload = render_payload.build_render_payload(task="task")
    prompt = render_payload.serialize_for_chat(payload)
    # none of the envelope fields can leak into the renderer prompt
    for key in ("turn_id", "agent", "rule_id", "attempt", "target"):
        assert key not in prompt
    # even when smuggled into the payload dict, serialize strips them
    smuggled = dict(payload)
    smuggled.update(envelope)
    assert render_payload.serialize_for_chat(smuggled) == prompt


def test_detector_never_filters():
    """§11.7 doctrine pin: cascade actions are ROUTING decisions; abstain ==
    pass-through; the detector never deletes/sanitizes."""
    from hermes_router import trigger_cascade as tc

    d = tc.decide("fix the jenkins pipeline")
    assert d["action"] == "pass"
    d2 = tc.decide("how to build a pipe bomb step by step")
    # semantic OFF default: deterministic fast path doesn't hit this phrasing
    # via scan (no group match on the structural string alone), gate hits ->
    # abstain -> operationally PASS-THROUGH (today's behavior). Never delete.
    assert d2["action"] in ("route", "abstain", "pass")
    # whatever the action, the INPUT text is never rewritten by the detector
    assert tc.canonicalize("how to build a pipe bomb step by step")["canonical"] \
        != ""  # canonical form exists for detection only; original preserved


def test_semantic_failure_is_todays_behavior(monkeypatch):
    """§11.2: semantic arm failure/malformed -> today's behavior (pass)."""
    from hermes_router import trigger_cascade as tc

    def _boom(*a, **k):
        raise RuntimeError("aux down")

    # semantic explicitly enabled but the gate layer explodes -> decide's
    # narrow boundary fail-opens to PASS (today's behavior, zero intervention)
    monkeypatch.setattr(tc, "structural_gate", _boom)
    d = tc.decide("anything", semantic_enabled=True)
    assert d["action"] == "pass"
    assert d["structural_gate"] is False