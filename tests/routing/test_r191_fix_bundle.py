"""R19.1 — Decision Lane fix bundle test battery.

LEG 1: platform-provenance filter BEFORE detection (bracketed platform
envelopes skipped + reason-coded log; dispatch-shaped plain prefixes and
real user asks with the same vocabulary still detected).
LEG 2: on_demand.manual is the PRIMARY trusted trigger — fires at PRE at
any level (standalone-line AND same-line prefix form), bypasses the
multi-family requirement; level 0 never fires.
LEG 3: backend HTTP failures map to distinct reason codes
(backend_auth / backend_quota / backend_http_<code>), never collapse
into reason=timeout.
"""
import json

import pytest

import hermes_router as plugin
from hermes_router import debug_banner, decision, router_core

SID = "s-r191"
TASK_ID = "t-r191"

LOGGED = []


@pytest.fixture()
def _reset(monkeypatch, tmp_path):
    router_core._test_reset()
    plugin.state.clear()
    LOGGED.clear()
    debug_banner._ANCHOR_BANNERS.clear()
    decision.reset_limits()
    decision.reset_v3_limits()
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: LOGGED.append((e, dict(f))))
    monkeypatch.setattr(decision, "_cfg", lambda: dict(decision.DEFAULTS))
    monkeypatch.setattr(router_core, "_decision_cfg",
                        lambda: dict(decision.DEFAULTS))
    monkeypatch.setattr(router_core, "consult_cooldown_turns",
                        lambda: 0, raising=True)
    monkeypatch.setattr("hermes_router.core.usage_ledger.estimate_cost",
                        lambda *a, **k: 0.0, raising=True)
    monkeypatch.setattr(decision, "_ledger_connect",
                        lambda db_path="": _tmp_conn(tmp_path))
    yield tmp_path
    decision.reset_limits()
    decision.reset_v3_limits()
    debug_banner._ANCHOR_BANNERS.clear()


def _tmp_conn(tmp_path):
    import os
    import sqlite3

    path = str(tmp_path / "plugin.db")
    os.makedirs(tmp_path, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0)
    conn.executescript(decision._LEDGER_SCHEMA)
    return conn


def _enable(monkeypatch, **over):
    cfg = dict(decision.DEFAULTS)
    cfg["enabled"] = True
    cfg.update(over)
    monkeypatch.setattr(decision, "_cfg", lambda: cfg)
    monkeypatch.setattr(router_core, "_decision_cfg", lambda: cfg)
    return cfg


def _logged(event):
    return [f for e, f in LOGGED if e == event]


# ---------------------------------------------------------------------------
# LEG 1 — provenance filter before detection
# ---------------------------------------------------------------------------

_FORK_ASK = ("which approach should we take? weigh the tradeoffs: "
             "option a) ship the stub now or option b) finish the e2e first")


@pytest.mark.parametrize("marker", [
    "[Durable Summary] orchestrator run 20260927 finished; 3 forks open.",
    "[OUT-OF-BAND USER MESSAGE] dispatch digest for coder ingress.",
    "[ASYNC DELEGATION BATCH COMPLETE] 2/3 subagents returned results.",
    "[Your active task list] 1. decide option a or option b",
])
def test_platform_envelope_skipped(_reset, marker):
    text = marker + "\n" + _FORK_ASK
    hit = decision.detect_v3(text, 2)
    assert hit is not None and hit["trigger"] == "provenance_skip"
    # platform payloads must never route the decision lane
    assert decision.handle_decision_v3(
        session_id=SID, task_id=TASK_ID, task_text=text,
        log_route=lambda e, **f: LOGGED.append((e, dict(f)))) is None
    sup = _logged("decision_suppressed")
    assert sup and sup[-1]["reason"] == decision.REASON_PROVENANCE_SKIP


def test_marker_within_first_80_chars_skipped(_reset):
    pad = "x" * 30
    text = pad + " [Depth-2 Summary] — " + _FORK_ASK
    assert len(text.split("[Depth-2 Summary]")[0]) < 80
    hit = decision.detect_v3(text, 2)
    assert hit is not None and hit["trigger"] == "provenance_skip"


def test_marker_past_window_not_skipped(_reset):
    pad = "x" * 200
    text = pad + " [Durable Summary] — " + _FORK_ASK
    hit = decision.detect_v3(text, 2)
    assert hit is None or hit["trigger"] != "provenance_skip"


@pytest.mark.parametrize("prefix", [
    "ORCH DIRECTIVE — ",
    "BUILD TASK — ",
    "ADDENDUM: ",
    "CONTINUE — ",
])
def test_dispatch_shaped_prefixes_are_user_turns(_reset, prefix):
    # NOT platform envelopes: real user asks in dispatch vocabulary detect.
    text = prefix + _FORK_ASK
    hit = decision.detect_v3(text, 2)
    assert hit is not None and hit["trigger"] == "pre"
    assert "provenance_skip" not in str(hit)


def test_real_user_ask_with_platform_vocab_still_detected(_reset):
    text = ("BUILD TASK — decide this: option a) kafka or option b) rabbitmq. "
            "which one should we pick? weigh the tradeoffs")
    hit = decision.detect_v3(text, 2)
    assert hit is not None and hit["trigger"] != "provenance_skip"
    assert "manual_ask" in hit["families"]  # decide-this phrasing counted


def test_dispatch_provenance_skip_no_decision_route(_reset, monkeypatch):
    _enable(monkeypatch)
    text = "[Durable Summary] digest.\n" + _FORK_ASK
    rd = router_core.dispatch(text, session_id=SID, model="m")
    assert rd.lane != router_core.LANE_DECISION
    assert rd.mode == router_core.MODE_FLASH_DIRECT
    events = [f for e, f in LOGGED if e == "PRE"]
    assert any(d.get("event_detail") == "decision_provenance_skip"
               for d in events)


# ---------------------------------------------------------------------------
# LEG 2 — on-demand manual: PRIMARY trusted trigger at any level
# ---------------------------------------------------------------------------

def test_manual_same_line_prefix_fires_level2(_reset):
    hit = decision.detect_v3("decide this: redis or memcached?", 2)
    assert hit is not None and hit["trigger"] == "manual"
    assert hit["families"] == ["manual_ask"]


def test_manual_standalone_line_fires_level2(_reset):
    text = "context blah\n\ndecide this\n\noption a) x or option b) y"
    hit = decision.detect_v3(text, 2)
    assert hit is not None and hit["trigger"] == "manual"


def test_manual_fires_level1_and_level3(_reset):
    for lvl in (1, 3):
        hit = decision.detect_v3("decide this: a) x or b) y", lvl)
        assert hit is not None and hit["trigger"] == "manual", lvl


def test_manual_level0_never_fires(_reset):
    assert decision.detect_v3("decide this: a) x or b) y", 0) is None
    # and with the configured default level forced to 0
    import types
    cfg = dict(decision.DEFAULTS)
    cfg["level"] = 0
    assert decision.detect_v3("decide this: a) x or b) y", None,
                              cfg=cfg) is None


def test_non_manual_single_family_stands_down_level2(_reset):
    # fork present + ONE phrasing family, no manual phrasing -> no fire
    text = "which approach should we take?\na) x\nb) y"
    assert decision.detect_v3(text, 2) is None
    # same ask fires at level 3 (aggressive single-family)
    hit = decision.detect_v3(text, 3)
    assert hit is not None and hit["trigger"] == "pre"


def test_detect_v0_manual_bypasses_multi_family_level2(_reset):
    hit = decision.detect("you decide which way we go", 2)
    assert hit is not None and "manual_ask" in hit["families"]


def test_detect_v0_level0_off(_reset):
    assert decision.detect("decide this now", 0) is None


def test_manual_pre_route_end_to_end(_reset, monkeypatch):
    _enable(monkeypatch)
    rd = router_core.dispatch("decide this: redis or memcached?",
                              session_id=SID, model="m")
    assert rd.lane == router_core.LANE_DECISION
    assert rd.mode == router_core.MODE_DECISION_SCORE
    assert "manual_ask" in rd.reason


def test_manual_on_demand_toggle_off_still_gates(_reset, monkeypatch):
    _enable(monkeypatch, on_demand={"manual": False, "midturn": True})
    assert decision.detect_v3("decide this: a) x or b) y", 2) is None
    rd = router_core.dispatch("decide this: a) x or b) y",
                              session_id=SID, model="m")
    assert rd.lane != router_core.LANE_DECISION


# ---------------------------------------------------------------------------
# LEG 3 — reason-coded backend HTTP failures
# ---------------------------------------------------------------------------

_ENVELOPE_ASK = ("which approach? weigh the tradeoffs. option a) redis\n"
                 "option b) memcached")


def _run_jev(monkeypatch, code):
    _enable(monkeypatch, backend="jev", api_key_env="R191_KEY")
    monkeypatch.setenv("R191_KEY", "k")
    monkeypatch.setattr(decision, "_HTTP_ERROR_CODE", None, raising=False)

    def _fake_post(url, headers, payload, timeout):
        decision._HTTP_ERROR_CODE = code  # transport seam recorded the HTTPError
        return None

    monkeypatch.setattr(decision, "_http_post_json", _fake_post)
    content, meta, reason = decision.call_backend(
        decision.build_envelope(SID, _ENVELOPE_ASK, [], "pre",
                                decision._cfg()),
        decision._cfg())
    assert content is None
    return reason


def test_http_401_maps_backend_auth(_reset, monkeypatch):
    assert _run_jev(monkeypatch, 401) == decision.REASON_BACKEND_AUTH


def test_http_402_maps_backend_quota(_reset, monkeypatch):
    assert _run_jev(monkeypatch, 402) == decision.REASON_BACKEND_QUOTA


def test_http_503_maps_backend_http_code(_reset, monkeypatch):
    assert _run_jev(monkeypatch, 503) == "backend_http_503"


def test_http_404_maps_backend_http_code(_reset, monkeypatch):
    assert _run_jev(monkeypatch, 404) == "backend_http_404"


def test_transport_failure_still_timeout(_reset, monkeypatch):
    assert _run_jev(monkeypatch, None) == decision.REASON_TIMEOUT


def test_http_reason_unit():
    decision._HTTP_ERROR_CODE = 401
    assert decision._http_reason() == "backend_auth"
    decision._HTTP_ERROR_CODE = 402
    assert decision._http_reason() == "backend_quota"
    decision._HTTP_ERROR_CODE = 429
    assert decision._http_reason() == "backend_http_429"
    decision._HTTP_ERROR_CODE = None
    assert decision._http_reason() == decision.REASON_TIMEOUT
