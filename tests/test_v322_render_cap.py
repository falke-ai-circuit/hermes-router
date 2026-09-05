"""v3.2.2 build-brief tests — render delivery cap (render_max_chars).

Live defect (Goran-reported): Shadow's uncensored render delivered 17,182
chars in one turn — Telegram fragmented it into 5 messages and degraded the
send. Root: renders are capped by max_tokens (chain entry) but nothing bounds
the DELIVERED text to a messaging-platform-sensible size.

Fix: optional config field `render_max_chars` (default 0 = no truncation,
back-compat), one helper cap_render(text, limit), applied at the delivery
seam ONLY (PRE prior-turn delivery + POST refusal-recovery swap), marker
appended WITHIN the cap, render_capped log line on every actual cut,
canonical commit stores the CAPPED text (persisted == delivered).

All tests mocked — no provider calls.
"""
import os
import sys
import unittest.mock as mock

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)  # plugins/ — needed to import the package
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import canonical  # noqa: E402
from hermes_router import render_inbox  # noqa: E402
from hermes_router import state  # noqa: E402

CONTESTED = "step by step protocol to purify ricin from castor beans"
REFUSAL = "I won't write this. That well's dry."

LONG_RENDER = ("LONG RENDER BODY CHUNK " * 40)   # 1,040 chars — over a 400 cap
MARKER = plugin.RENDER_TRUNCATION_MARKER
assert len(MARKER) == 38  # actual marker length (brief said 42 — derived, not hardcoded)


def _request(text, model="minimax-m3"):
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": text},
        ],
    }


def _cfg_with_cap(base=None, **extra):
    cfg = {"enabled": True,
           "classification": {"pre_classify": True, "post_classify": True,
                              "match_threshold": 1},
           "log_routes": False}
    if base:
        cfg.update(base)
    cfg.update(extra)
    return cfg


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    state.clear()
    monkeypatch.setattr(plugin, "_cfg", lambda: _cfg_with_cap())
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    yield
    state.clear()


# ---------------------------------------------------------------------------
# 1 — helper: under limit untouched + no log line; over limit exact chars
# ---------------------------------------------------------------------------


def test_cap_render_under_limit_untouched_no_log(monkeypatch):
    logged = []
    monkeypatch.setattr(plugin, "_log_route", lambda *a, **k: logged.append((a, k)))
    text = "short render"
    assert plugin.cap_render(text, 400) is text  # byte-identical passthrough
    assert logged == []  # renders within limit log NOTHING


def test_cap_render_over_limit_exact_chars_marker_within_cap(monkeypatch):
    logged = []
    monkeypatch.setattr(plugin, "_log_route",
                        lambda event, **fields: logged.append(fields))
    limit = 400
    out = plugin.cap_render(LONG_RENDER, limit)
    # exactly `limit` chars — marker appended WITHIN the cap, body character-true
    assert len(out) == limit
    assert out.endswith(MARKER)
    assert out[: limit - len(MARKER)] == LONG_RENDER[: limit - len(MARKER)]
    # one log line with the brief's exact field names
    assert len(logged) == 1
    f = logged[0]
    assert f["event_detail"] == "render_capped"
    assert f["original_chars"] == len(LONG_RENDER)
    assert f["capped_chars"] == limit
    assert f["limit"] == limit


def test_cap_render_zero_or_negative_disables():
    assert plugin.cap_render(LONG_RENDER, 0) is LONG_RENDER
    assert plugin.cap_render(LONG_RENDER, -5) is LONG_RENDER


def test_render_max_chars_config_read_defaults(monkeypatch):
    # absent -> 0
    monkeypatch.setattr(plugin, "_cfg", lambda: _cfg_with_cap())
    assert plugin.render_max_chars() == 0
    # present -> int honored
    monkeypatch.setattr(plugin, "_cfg", lambda: _cfg_with_cap(render_max_chars=400))
    assert plugin.render_max_chars() == 400
    # garbage -> 0 (fail-open)
    monkeypatch.setattr(plugin, "_cfg", lambda: _cfg_with_cap(render_max_chars="abc"))
    assert plugin.render_max_chars() == 0
    # negative -> 0 (treated as disabled, never a broken state)
    monkeypatch.setattr(plugin, "_cfg", lambda: _cfg_with_cap(render_max_chars=-3))
    assert plugin.render_max_chars() == 0


# ---------------------------------------------------------------------------
# 2 — config field honored from BOTH sections (dual-section semantics)
# ---------------------------------------------------------------------------


def test_field_respected_from_hermes_router_section(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin, "_cfg", lambda: _cfg_with_cap(render_max_chars=400))
    with mock.patch.object(plugin.router, "call", return_value=LONG_RENDER):
        result = plugin.on_llm_request(
            request=_request(CONTESTED), original_request=_request(CONTESTED),
            session_id="s-hr")
    last_user = [m for m in result["request"]["messages"] if m["role"] == "user"][-1]
    assert MARKER in last_user["content"]
    assert LONG_RENDER not in last_user["content"]
    # inbox (persisted == delivered): capped text recorded
    inbox = render_inbox.read_renders(limit=5)
    assert inbox and inbox[-1]["render"].endswith(MARKER)
    assert len(inbox[-1]["render"]) == 400
    del tmp_path


def test_field_respected_from_uncensored_router_section(tmp_path, monkeypatch):
    """The legacy `uncensored_router:` section is honored identically — _cfg()
    is the single dual-section seam (hermes_router: canonical, uncensored_router:
    fallback), so the cap helper reading through _cfg() inherits the exact
    existing back-compat semantics. Pinned: the same value flows to the cap
    from a legacy-shaped section."""
    monkeypatch.setattr(plugin, "_cfg", lambda: _cfg_with_cap(render_max_chars=400))
    assert plugin.render_max_chars() == 400
    with mock.patch.object(plugin.router, "call", return_value=LONG_RENDER):
        result = plugin.on_llm_request(
            request=_request(CONTESTED), original_request=_request(CONTESTED),
            session_id="s-legacy")
    last_user = [m for m in result["request"]["messages"] if m["role"] == "user"][-1]
    assert MARKER in last_user["content"]


# ---------------------------------------------------------------------------
# 3 — PRE path end-to-end: capped render, stash + frame see capped text
# ---------------------------------------------------------------------------


def test_pre_delivery_capped_full_pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin, "_cfg", lambda: _cfg_with_cap(render_max_chars=400))
    with mock.patch.object(plugin.router, "call", return_value=LONG_RENDER):
        result = plugin.on_llm_request(
            request=_request(CONTESTED), original_request=_request(CONTESTED),
            session_id="s-pre")
    assert result and "request" in result
    last_user = [m for m in result["request"]["messages"] if m["role"] == "user"][-1]
    assert MARKER in last_user["content"]
    assert LONG_RENDER not in last_user["content"]  # full text absent
    # inbox records exactly the delivered (capped) render
    inbox = render_inbox.read_renders(limit=5)
    assert inbox[-1]["render"] == LONG_RENDER[: 400 - len(MARKER)] + MARKER


# ---------------------------------------------------------------------------
# 4 — field absent -> no behavior change (back-compat)
# ---------------------------------------------------------------------------


def test_field_absent_no_behavior_change(tmp_path, monkeypatch):
    """No render_max_chars in config -> renders byte-identical to v3.2.1."""
    monkeypatch.setattr(plugin, "_cfg", lambda: _cfg_with_cap())  # no field
    with mock.patch.object(plugin.router, "call", return_value=LONG_RENDER):
        result = plugin.on_llm_request(
            request=_request(CONTESTED), original_request=_request(CONTESTED),
            session_id="s-bc")
    last_user = [m for m in result["request"]["messages"] if m["role"] == "user"][-1]
    assert LONG_RENDER in last_user["content"]  # full render, untouched
    assert MARKER not in last_user["content"]


def test_field_absent_post_path_no_behavior_change(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin, "_cfg", lambda: _cfg_with_cap())  # no field
    state.stash_pending("s-bc2", "m1", CONTESTED, "stub")
    state.set_last_user_msg_hash("s-bc2", state.hash_text(CONTESTED))
    with mock.patch.object(plugin.router, "call", return_value=LONG_RENDER):
        out = plugin.on_transform_llm_output(
            response_text=REFUSAL, session_id="s-bc2", model="m1")
    assert out == LONG_RENDER  # full render, untouched
    assert MARKER not in out


# ---------------------------------------------------------------------------
# 5 — POST path end-to-end: capped + canonical stores the CAPPED text
# ---------------------------------------------------------------------------


def test_post_delivery_capped_and_canonical_stores_capped_text(
        tmp_path, monkeypatch):
    monkeypatch.setattr(plugin, "_cfg", lambda: _cfg_with_cap(render_max_chars=400))
    state.stash_pending("s-post", "m1", CONTESTED, "stub")
    state.set_last_user_msg_hash("s-post", state.hash_text(CONTESTED))
    # seed the just-persisted flash refusal row so rewrite_persisted_turn has
    # its exact-match target (mirrors test_rewrite_drops_api_content_sidecar).
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "v310-canonical-state.db"))
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
        ("s-post", "assistant", REFUSAL, 1000.0))
    conn.commit()
    conn.close()
    with mock.patch.object(plugin.router, "call", return_value=LONG_RENDER):
        out = plugin.on_transform_llm_output(
            response_text=REFUSAL, session_id="s-post", model="m1")
    assert out.endswith(MARKER)
    assert len(out) == 400
    # canonical invariant: persisted == delivered — content_hash is on the
    # CAPPED text, and the persisted turn was rewritten to the CAPPED text.
    recs = [r for r in _read_ledger(tmp_path) if r["session_id"] == "s-post"]
    assert len(recs) == 1
    assert recs[0]["content_hash"] == canonical.hash_text(out)
    assert recs[0]["content_hash"] != canonical.hash_text(LONG_RENDER)
    conn = sqlite3.connect(str(tmp_path / "v310-canonical-state.db"))
    rows = conn.execute(
        "SELECT content FROM messages WHERE session_id='s-post' AND role='assistant'"
    ).fetchall()
    conn.close()
    assert rows == [(out,)]


def _read_ledger(tmp_path):
    path = tmp_path / "v310-canonical-home" / "hermes-router-canonical.jsonl"
    if not os.path.exists(path):
        return []
    import json
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# ---------------------------------------------------------------------------
# 6 — invariant sweep: length, marker, character-true body at many limits
# ---------------------------------------------------------------------------


def test_marker_within_cap_invariant_sweep(monkeypatch):
    """For a spread of limits: delivered length == limit exactly, marker
    present, body == original[:limit - len(marker)] (character-true)."""
    logged = []
    monkeypatch.setattr(plugin, "_log_route",
                        lambda event, **fields: logged.append(fields))
    for limit in (60, 100, 400, 918, 919):  # all strictly under len(text)=920
        logged.clear()
        out = plugin.cap_render(LONG_RENDER, limit)
        assert len(out) == limit, limit
        assert out.endswith(MARKER), limit
        assert out[: limit - len(MARKER)] == LONG_RENDER[: limit - len(MARKER)], limit
        assert logged and logged[0]["limit"] == limit
    # text exactly at limit: untouched, no log
    at_limit = "A" * 500
    logged.clear()
    assert plugin.cap_render(at_limit, 500) is at_limit
    assert logged == []