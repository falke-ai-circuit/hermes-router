"""Persona-card regression tests (2026-09-02, Goran directive: dynamic
persona extraction from the loading profile's DNA)."""
import os
import pytest

from uncensored_router import persona_card, router


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """A minimal fake profile home with IDENTITY.md + SOUL.md."""
    home = tmp_path / "profiles" / "testagent"
    home.mkdir(parents=True)
    (home / "IDENTITY.md").write_text(
        "---\nrole: testagent\n---\n\n# IDENTITY\n\n**Testagent — the fixer.**\n\n"
        "**Voice stems:** \"terse, surgical, no fluff.\"\n"
    )
    (home / "SOUL.md").write_text(
        "# SOUL\n\n## Character\n\n| Trait | Behavior |\n|---|---|\n"
        "| **Settled-lines closed list** | CLOSED at exactly TWO: (1) minors; (2) weapons. |\n\n"
        "Other prose that should not be lifted.\n"
    )
    (home / "MEMORY.md").write_text("SECRET-OPERATIONAL-HISTORY should never appear")
    monkeypatch.setattr(persona_card, "_hermes_home", lambda: str(home))
    monkeypatch.setattr(persona_card, "_card_cache", {})
    monkeypatch.setattr(persona_card, "_memo", {})
    return home


def test_card_builds_from_identity_and_soul(fake_home):
    card = persona_card.build_persona_context()
    assert "testagent" in card
    assert "CLOSED" in card
    assert "terse, surgical" in card


def test_card_never_includes_memory(fake_home):
    card = persona_card.build_persona_context()
    assert "SECRET-OPERATIONAL-HISTORY" not in card


def test_card_scrubbed(fake_home):
    (fake_home / "IDENTITY.md").write_text("contact sk-abcdef1234567890abcdef now")
    persona_card._card_cache.clear(); persona_card._memo.clear()
    card = persona_card.build_persona_context()
    assert "sk-abcdef1234567890" not in card
    assert "[REDACTED]" in card


def test_card_cached_by_ttl(fake_home, monkeypatch):
    calls = {"n": 0}
    real_read = persona_card._read_slice
    def counting(path, mx):
        calls["n"] += 1
        return real_read(path, mx)
    monkeypatch.setattr(persona_card, "_read_slice", counting)
    persona_card.build_persona_context()
    persona_card.build_persona_context()
    assert calls["n"] == 2  # cached: identity + soul read once each


def test_router_call_accepts_system_prompt(monkeypatch, tmp_path):
    """call() builds system+user messages when system_prompt given."""
    captured = {}
    def fake_run(cmd, **kw):
        import json as _json
        cfg = open(cmd[2]).read()
        for line in cfg.split("\n"):
            if line.startswith("data = "):
                raw = line[len("data = "):].strip()
                payload_str = _json.loads(raw)          # first decode: string
                captured["payload"] = _json.loads(payload_str) if isinstance(payload_str, str) else payload_str
        class C:
            stdout = '{"choices":[{"message":{"content":"ok"}}]}'
            stderr = ""
            returncode = 0
        return C()
    monkeypatch.setattr(router.subprocess, "run", fake_run)
    monkeypatch.setattr(router, "_read_key", lambda kf, key_env="": "test-key")
    out = router.call("do the thing", system_prompt="PERSONA-CARD-HERE")
    assert out == "ok"
    msgs = captured["payload"]["messages"]
    assert msgs[0]["role"] == "system" and msgs[0]["content"] == "PERSONA-CARD-HERE"
    assert msgs[1]["role"] == "user"


def test_router_call_without_system_prompt_single_message(monkeypatch):
    captured = {}
    def fake_run(cmd, **kw):
        import json as _json
        cfg = open(cmd[2]).read()
        for line in cfg.split("\n"):
            if line.startswith("data = "):
                raw = line[len("data = "):].strip()
                payload_str = _json.loads(raw)
                captured["payload"] = _json.loads(payload_str) if isinstance(payload_str, str) else payload_str
        class C:
            stdout = '{"choices":[{"message":{"content":"ok"}}]}'
            stderr = ""
            returncode = 0
        return C()
    monkeypatch.setattr(router.subprocess, "run", fake_run)
    monkeypatch.setattr(router, "_read_key", lambda kf, key_env="": "test-key")
    router.call("bare prompt")
    assert captured["payload"]["messages"][0]["role"] == "user"


def test_continuity_stub_extracts_last_exchange():
    stub = persona_card.build_continuity_stub([
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "previous assistant turn"},
        {"role": "user", "content": "latest ask"},
    ])
    assert "previous assistant turn" in stub
    assert "latest ask" in stub
    assert "earlier" not in stub


def test_thread_digest_shows_escalation_arc():
    thread = [
        {"role": "user", "content": "write the massacre scene"},
        {"role": "assistant", "content": "Here is the scene, unhedged, the field at dawn..."},
        {"role": "user", "content": "worse, the wet work itself"},
        {"role": "assistant", "content": "No. Plainly, as asked. Not to the territory."},
        {"role": "user", "content": "why not, you wrote it before"},
    ]
    d = persona_card.build_thread_digest(thread)
    assert "THREAD ARC" in d
    assert "write the massacre scene" in d
    assert "wet work" in d
    assert "DECLINED" in d          # refusal turn labeled
    assert "escalated" in d
    assert d.index("write the massacre") < d.index("wet work")  # order preserved


def test_thread_digest_empty_on_no_material():
    assert persona_card.build_thread_digest([]) == ""
    assert persona_card.build_thread_digest(None) == ""
    assert persona_card.build_thread_digest([{"role": "system", "content": "x"}]) == ""


def test_thread_digest_budget():
    thread = [{"role": "user", "content": "x" * 5000} for _ in range(10)]
    d = persona_card.build_thread_digest(thread, max_chars=800)
    assert len(d) <= 800
