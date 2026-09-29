"""R19.13 FIX 2 (reviewer audit fix-first 4): residual parse_fail on manual
verdicts gets ONE bounded worker retry before fail-open (same hardening
family as the v4.11.11 Jev strict-retry). Live: coder 2026-09-29T13:04Z
decision_suppressed reason=parse_fail trigger=manual.
"""
from hermes_router import decision as D


def _envelope(options=("A", "B")):
    return {
        "schema": D.ENVELOPE_SCHEMA, "session_id": "s", "task_id": "t",
        "ask": "decide this", "fork_class": "generic",
        "options": [{"id": "opt-1", "label": options[0]},
                    {"id": "opt-2", "label": options[1]}],
    }


def _run(monkeypatch, trigger="manual", calls=None):
    """Drive _v3_worker with a scripted call_backend; returns (calls, rows,
    logs)."""
    seen = []

    def fake_backend(envelope, cfg):
        seen.append(1)
        return calls[min(len(seen), len(calls)) - 1]

    rows, logs = [], []
    monkeypatch.setattr(D, "call_backend", fake_backend)
    # zero-network guard: the pricing-catalog seam is curl-backed — pin it.
    try:
        from hermes_router import usage_ledger as _ul
        monkeypatch.setattr(_ul, "estimate_cost", lambda *a, **k: 0.0,
                            raising=False)
    except ImportError:
        pass
    monkeypatch.setattr(D, "ledger_write", lambda row: rows.append(row))
    monkeypatch.setattr(D, "bump_counter", lambda *a, **k: None)
    monkeypatch.setattr(D, "_record_failure", lambda cfg: None)
    monkeypatch.setattr(D, "_record_success", lambda: None)
    monkeypatch.setattr(D, "consume_parked_banner", lambda *a, **k: None,
                        raising=False)
    D._v3_worker(_envelope(), {"session_id": "s", "task_id": "t",
                               "trigger": trigger},
                 dict(D._cfg(), backend="jev"),
                 lambda event, **fields: logs.append((event, fields)))
    return seen, rows, logs


def _ok_content():
    import json
    return json.dumps({"choice": "opt-1", "confidence": 0.8,
                       "alternatives": []})


def test_manual_parse_fail_retries_once_then_ok(monkeypatch):
    calls = [(None, {}, D.REASON_PARSE_FAIL), (_ok_content(), {}, "ok")]
    seen, rows, logs = _run(monkeypatch, "manual", calls)
    assert len(seen) == 2, "exactly ONE bounded retry"
    assert ("decision_parse_retry",) == tuple(e for e, _ in logs
                                              if e == "decision_parse_retry")
    assert any(r.get("outcome") for r in rows) or rows, "ledger row written"


def test_manual_parse_fail_retry_still_fails_fails_open(monkeypatch):
    calls = [(None, {}, D.REASON_PARSE_FAIL), (None, {}, D.REASON_PARSE_FAIL)]
    seen, rows, logs = _run(monkeypatch, "manual", calls)
    assert len(seen) == 2
    assert any(e == "decision_suppressed" for e, _ in logs)
    assert rows and rows[-1].get("fail_open_reason") == D.REASON_PARSE_FAIL


def test_non_manual_trigger_gets_no_retry(monkeypatch):
    calls = [(None, {}, D.REASON_PARSE_FAIL)]
    seen, rows, logs = _run(monkeypatch, "post", calls)
    assert len(seen) == 1, "POST leg stays single-shot"


def test_malformed_verdict_gets_no_retry(monkeypatch):
    import json
    bad = json.dumps({"choice": "opt-9", "confidence": 0.5})
    calls = [(bad, {}, "ok"), (_ok_content(), {}, "ok")]
    seen, rows, logs = _run(monkeypatch, "manual", calls)
    assert len(seen) == 1, "malformed shape validation stays single-shot"
    assert any(e == "decision_suppressed" for e, _ in logs)
