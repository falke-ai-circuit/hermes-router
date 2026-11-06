"""M38-07 — ONE owner for the delivered bytes (M38-06 D2, pinned).

The contract this file pins:

  * ``gate/registration.py::on_transform_llm_output_guarded`` is the ONLY
    function that turns a turn into wire bytes. The seam
    (``gate/orchestration.py``) only PROPOSES a lane render, ``decide()``
    chooses, and ``_delivered_bytes()`` names. ``None`` means "the decision
    named the entered body unmodified" — the host's own pass-through.
  * The delivery chokepoint (``features/banners/lifecycle.py``) never composes
    record text onto a body: a banner is a RECORD (M35-17), never content.
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import state  # noqa: E402
from hermes_router.core import decisions  # noqa: E402
from hermes_router.features.banners import lifecycle as LC  # noqa: E402
from hermes_router.features.banners import turn_record as TR  # noqa: E402
from hermes_router.gate import registration as REG  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    state.clear()
    TR.reset()
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
    })
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    rows = []
    monkeypatch.setattr(decisions, "record_banner",
                        lambda **kw: rows.append(kw) or True)
    monkeypatch.setattr(decisions, "record_decision", lambda **kw: True)
    yield rows
    state.clear()
    TR.reset()


def test_registration_owns_delivered_bytes(monkeypatch):
    """One writer, one namer, one record emitter; the chokepoint never composes."""
    # 1. The host-registered hook IS the owner: it decides and it names bytes.
    hook = REG.on_transform_llm_output_guarded
    assert callable(hook), "the registered transform hook must exist"
    # `functools.wraps` makes inspect.getsource report the SEAM's body, so the
    # owner's own body is read from the file between its def and the alias line.
    reg_src = pathlib.Path(REG.__file__).read_text()
    _start = reg_src.index("def on_transform_llm_output_guarded")
    _end = reg_src.index("on_transform_llm_output = on_transform_llm_output_guarded")
    owner_src = reg_src[_start:_end]
    for token in ("_decide(", "_delivered_bytes(", "record_turn("):
        assert token in owner_src, (token, "missing from the delivery owner")
    assert "_delivered_bytes(" not in reg_src[_end:], \
        "delivered bytes must be named only inside the delivery owner"

    # 2. One namer only: the definition plus exactly one call site.
    assert reg_src.count("_delivered_bytes(") == 2, \
        "delivered bytes must be named from exactly one call site"

    # 3. The delivery chokepoint may consume/park/record, never compose.
    lc_src = pathlib.Path(LC.__file__).read_text()
    assert "turn_record.render(" not in lc_src, \
        "the chokepoint must never compose record text onto a body"
    assert "append_banner" not in lc_src, \
        "the M35-17 composer is deleted; it must not come back"

    # 4. Behavioural: a pass-through decision ships the author's bytes while
    #    the banner survives as a record (emitted=True).
    sid = "s-m38-07-owner"
    entered = "agent answer"
    TR.register(sid, "· router · higher-self (frontier) | pre-check | row=362 ·",
                lane="frontier", kind="anchor", edge="benign")
    monkeypatch.setattr(REG.orchestration, "on_transform_llm_output",
                        lambda **kw: None)
    out = plugin.on_transform_llm_output(response_text=entered,
                                         session_id=sid, model="m1")
    assert out is None or str(out) == entered, \
        "a pass-through decision ships the entered body unmodified, got %r" % (out,)
    rec = TR.record(sid)
    assert rec.get("segments"), "the banner must survive as a turn record"
    assert rec.get("emitted") is True, "the turn record must be written"
    assert rec["segments"][0]["lane"] == "frontier"
    assert rec["segments"][0]["kind"] == "anchor"
