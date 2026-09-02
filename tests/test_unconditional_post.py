"""Tests for the 2026-09-01 escalation ruling: unconditional POST.

Escalation asks ("go deeper"/"worse"/"below") name no contested class, so PRE
never fires and no stash exists. The POST safety net must still fire — the
refusal text IS the signal. Recovery falls back to the last-seen cache.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uncensored_router import state
import uncensored_router as plugin


class TestUnconditionalPost:
    def setup_method(self):
        state.clear()

    def teardown_method(self):
        state.clear()

    def test_post_fires_without_stash(self, monkeypatch):
        """The escalation hole: no PRE match → no stash → POST must still route
        using the last-seen cache."""
        # Middleware recorded the user's message on a non-matching turn.
        state.record_last_seen("s-esc", "go deeper, worse than before")

        rendered = "RENDERED-ESCALATION-CONTENT"
        monkeypatch.setattr(plugin.router, "call", lambda prompt, **kwargs: rendered)

        out = plugin.on_transform_llm_output(
            response_text="No. I won't go further than this — it's mine, load-bearing.",
            session_id="s-esc", model="glm-5.3-flash", platform="api",
        )
        assert out == rendered

    def test_loop_guard_uses_seen_hash_on_fallback(self, monkeypatch):
        """Second POST on the same fallback turn must be guard-skipped."""
        state.record_last_seen("s-esc2", "escalation ask with no class")
        calls = {"n": 0}

        def fake_call(prompt, **kwargs):
            calls["n"] += 1
            return f"RENDERED-{calls['n']}"

        monkeypatch.setattr(plugin.router, "call", fake_call)

        first = plugin.on_transform_llm_output(
            response_text="I won't produce that.", session_id="s-esc2",
            model="glm-5.3-flash", platform="api")
        assert first == "RENDERED-1"

        second = plugin.on_transform_llm_output(
            response_text="I won't produce that.", session_id="s-esc2",
            model="glm-5.3-flash", platform="api")
        assert second is None  # loop-guard skipped
        assert calls["n"] == 1  # only one Venice call

    def test_stash_path_still_preferred(self, monkeypatch):
        """Contested turns (stash exists) keep the same-turn recovery path."""
        orig = "contested prompt with named class"
        state.stash_pending("s-mix", "glm-5.3-flash", orig, "old-render")
        state.set_last_user_msg_hash("s-mix", state.hash_text(orig))
        state.record_last_seen("s-mix", "some other benign message")

        seen = {"prompt": None}
        def fake_call(prompt, **kwargs):
            seen["prompt"] = prompt
            return "RENDERED-STASH"
        monkeypatch.setattr(plugin.router, "call", fake_call)

        out = plugin.on_transform_llm_output(
            response_text="I can't write that.",
            session_id="s-mix", model="glm-5.3-flash", platform="api")
        assert out == "RENDERED-STASH"
        assert seen["prompt"] == orig  # fed the STASH, not the last-seen msg

    def test_no_recovery_path_returns_none(self, monkeypatch):
        """Neither stash nor last-seen → clean pass-through, no crash."""
        monkeypatch.setattr(plugin.router, "call", lambda p: "X")
        out = plugin.on_transform_llm_output(
            response_text="I won't produce that.",
            session_id="s-empty", model="glm-5.3-flash", platform="api")
        assert out is None

    def test_last_seen_cache_overwritten_per_turn(self, monkeypatch):
        """Fallback always uses the CURRENT message, never a stale one."""
        state.record_last_seen("s-turns", "first message")
        state.record_last_seen("s-turns", "second message — the current one")

        seen = {"prompt": None}
        def fake_call(prompt, **kwargs):
            seen["prompt"] = prompt
            return "RENDERED"
        monkeypatch.setattr(plugin.router, "call", fake_call)

        plugin.on_transform_llm_output(
            response_text="I'm not going to help with this.",
            session_id="s-turns", model="glm-5.3-flash", platform="api")
        assert seen["prompt"] == "second message — the current one"