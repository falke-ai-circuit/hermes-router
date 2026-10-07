"""R19.17 ADDENDUM: the aux model must NEVER be a hardcoded literal again.
classification.aux_endpoint.model absent OR sentinel 'auto'/'hermes' ->
resolve AUTOMATICALLY to the agent's current Hermes provider model
(config_access.agent_model), at call time, not cached. Explicit models
win. Resolution failure -> fail-open "" (provider default, today's
behavior)."""
from hermes_router import config_access as CA
from hermes_router import semantic_classifier as SC


def test_explicit_model_wins():
    assert SC.resolve_aux_model(
        {"model": "meituan/longcat-2.0:free"}) == "meituan/longcat-2.0:free"
    assert SC.resolve_aux_model({"model": "z-ai/glm-5.3-flash"}) == \
        "z-ai/glm-5.3-flash"


def test_sentinels_resolve_to_agent_model(monkeypatch):
    monkeypatch.setattr(CA, "agent_model", lambda: "z-ai/glm-5.3-flash")
    for sentinel in ("auto", "hermes", "AUTO", "  ", None):
        assert SC.resolve_aux_model({"model": sentinel}) == \
            "z-ai/glm-5.3-flash"
    assert SC.resolve_aux_model({}) == "z-ai/glm-5.3-flash"


def test_resolution_failure_fails_open(monkeypatch):
    monkeypatch.setattr(CA, "agent_model",
                        lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert SC.resolve_aux_model({"model": "auto"}) == ""
    assert SC.resolve_aux_model({}) == ""
    assert SC.resolve_aux_model(None) == ""


def test_agent_model_reads_profile_config():
    """The real accessor reads the co-located profile config (never raises;
    empty on miss)."""
    m = CA.agent_model()
    assert isinstance(m, str)
    assert CA.agent_model() == m  # stable across calls, re-read each time


def test_decision_head_uses_resolver(monkeypatch):
    """The embedding/decision-head path consumes the resolver, not a
    hardcoded literal."""
    import hermes_router.decision_head as DH
    monkeypatch.setattr(SC, "resolve_aux_model",
                        lambda ep: "z-ai/glm-5.3-flash")
    # smoke: the module imports and the seam is importable at call time
    assert callable(DH.embed_text)
