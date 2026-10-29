"""Measurement only: the ledger persists the option BOARD, not just its names.

WHY. `decision_ledger.options_json` carried `{"id","label"}` per option while the
envelope at write time already held the fork's causal frame (`cause_effect`,
`cost`, `priors`, `risk`) — so no past decision could be re-audited for ranking
quality, calibration, or near-duplicate option sets. Nothing downstream reads
these extra keys at write time; the change is persistence, never routing.

Pins:
  (a) a per-option probability and the row's margin land in `options_json`
      when the verdict actually provides them;
  (b) when it does not, the keys are OMITTED — never null, never 0.0, which is
      indistinguishable from a real zero-probability pick;
  (c) an oversized option set still yields PARSEABLE JSON under the 8,000-char
      cap (whole options dropped from the tail, then an id+label fallback);
  (d) the fire path writes exactly the frame the envelope carried.
"""
import json
import sqlite3

import pytest

from hermes_router import decision

SID = "s-opt-board"

ASK = (
    "decide this: we can do A) batch-append all rows in one shot, faster "
    "but riskier on partial failure, or B) synchronous per-row appends, "
    "slower but each insert is independently reversible. which do you pick?"
)

FRAMED = [
    {"id": "opt-1", "label": "batch-append all rows in one shot",
     "cause_effect": "one write instead of N", "cost": "riskier on partial failure",
     "priors": "2 prior verdict(s): opt-1 chosen 1x", "risk": "wide blast radius"},
    {"id": "opt-2", "label": "synchronous per-row appends",
     "cause_effect": "each insert independently reversible", "cost": "slower",
     "priors": None, "risk": "reversible"},
]


# --- (a)+(b) the serializer's contract ----------------------------------------

def test_probabilities_and_margin_land_when_the_verdict_provides_them():
    verdict = {"choice": "opt-1", "confidence": 0.62,
               "alternatives": ["opt-2"],
               "probabilities": {"opt-1": 0.62, "opt-2": 0.31},
               "margin": 0.31}
    out = json.loads(decision.ledger_options_json(FRAMED, verdict))
    assert out[0]["id"] == "opt-1" and out[1]["id"] == "opt-2"
    assert out[0]["probability"] == 0.62
    assert out[1]["probability"] == 0.31
    assert out[0]["margin"] == 0.31 and out[1]["margin"] == 0.31
    # the frame rides along too
    assert out[0]["cause_effect"] == "one write instead of N"
    assert out[0]["risk"] == "wide blast radius"
    assert out[1]["cost"] == "slower"


def test_absent_probability_is_omitted_never_zero():
    """The real verdict shape the lane produces (validate_verdict): choice +
    confidence + alternatives, no distribution. `confidence` is ONE pick's
    confidence, not a per-option probability — promoting it would corrupt the
    calibration audit this column exists to serve."""
    verdict = {"choice": "opt-1", "confidence": 0.62,
               "alternatives": ["opt-2"]}
    out = json.loads(decision.ledger_options_json(FRAMED, verdict))
    for o in out:
        assert "probability" not in o
        assert "margin" not in o
        assert o["id"] and o["label"]


def test_a_missing_frame_field_is_omitted_not_nulled():
    opts = [{"id": "opt-1", "label": "a", "cause_effect": None,
             "cost": "", "priors": "1 prior", "risk": None}]
    out = json.loads(decision.ledger_options_json(opts))
    assert "cause_effect" not in out[0]
    assert "cost" not in out[0]
    assert "risk" not in out[0]
    assert out[0]["priors"] == "1 prior"


# --- (c) truncation never emits half-serialized JSON --------------------------

def _big_board(n=40, pad=400):
    return [{"id": "opt-%d" % i, "label": "option %d" % i,
             "cause_effect": "x" * pad, "cost": "y" * pad,
             "risk": "reversible"} for i in range(1, n + 1)]


def test_oversized_board_is_truncated_to_parseable_json():
    payload = decision.ledger_options_json(_big_board())
    assert len(payload) <= 8000
    out = json.loads(payload)                       # must PARSE, not merely fit
    assert isinstance(out, list) and out
    assert [o["id"] for o in out] == ["opt-%d" % i for i in range(1, len(out) + 1)]
    assert all(o["label"] for o in out)


def test_whole_set_falls_back_to_id_and_label_when_one_option_overflows():
    payload = decision.ledger_options_json(
        [{"id": "opt-1", "label": "a" * 60, "cause_effect": "x" * 500},
         {"id": "opt-2", "label": "b" * 60, "cause_effect": "y" * 500}], cap=220)
    assert len(payload) <= 220
    out = json.loads(payload)
    assert out and all(set(o) == {"id", "label"} for o in out)


def test_a_single_oversized_label_still_yields_valid_json():
    payload = decision.ledger_options_json(
        [{"id": "opt-1", "label": "z" * 9000}])
    assert len(payload) <= 8000
    out = json.loads(payload)
    assert out and out[0]["id"] == "opt-1"
    assert out[0]["label"]


def test_no_options_serializes_to_an_empty_array():
    assert decision.ledger_options_json([]) == "[]"
    assert decision.ledger_options_json(None) == "[]"


# --- (d) end to end: the fire persists the frame it was given ----------------

@pytest.fixture()
def env(monkeypatch, tmp_path):
    decision.reset_limits()
    decision.reset_v3_limits()
    path = str(tmp_path / "plugin.db")
    conn = sqlite3.connect(path, timeout=5.0)
    conn.executescript(decision._LEDGER_SCHEMA)
    conn.commit()
    conn.close()
    monkeypatch.setattr(decision, "_ledger_connect",
                        lambda db_path="": sqlite3.connect(path))
    monkeypatch.setattr("hermes_router.core.usage_ledger.estimate_cost",
                        lambda *a, **k: 0.0, raising=True)
    yield path
    decision.reset_limits()
    decision.reset_v3_limits()


def _rows(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM decision_ledger ORDER BY id")]
    conn.close()
    return rows


def test_the_fire_writes_the_framed_board_it_was_given(env, monkeypatch):
    def fake_backend(envelope, cfg):
        meta = {"model": "m", "endpoint": "e", "tokens_in": 10,
                "tokens_out": 5, "latency_s": 0.1}
        return (json.dumps({"choice": "opt-1", "confidence": 0.8,
                            "alternatives": ["opt-2"]}), meta, "ok")

    monkeypatch.setattr(decision, "call_backend", fake_backend)
    cfg = dict(decision.DEFAULTS)
    cfg["enabled"] = True
    cfg["on_demand"] = {"manual": True, "midturn": True}
    monkeypatch.setattr(decision, "_cfg", lambda: cfg)
    # the real envelope for this ask, then the REAL worker body (synchronously:
    # _invoke dispatches the same function on a daemon thread).
    envelope = decision.build_envelope(SID, ASK,
                                       decision._manual_verbatim_options(ASK),
                                       "manual", cfg)
    env_opts = [o for o in envelope.get("options") or [] if isinstance(o, dict)]
    assert len(env_opts) == 2
    decision._v3_worker(envelope,
                        {"session_id": SID, "task_id": "t-1",
                         "trigger": "manual"}, cfg, lambda e, **f: None)

    rows = _rows(env)
    assert len(rows) == 1
    persisted = json.loads(rows[0]["options_json"])
    assert len(persisted) == len(env_opts)
    for src, got in zip(env_opts, persisted):
        assert got["id"] == src["id"] and got["label"] == src["label"]
        # every non-empty frame field the envelope carried survived the write
        for k in ("cause_effect", "cost", "priors", "risk"):
            if src.get(k):
                assert got[k] == src[k]
            else:
                assert k not in got
        # the verdict carries no distribution -> nothing fabricated
        assert "probability" not in got and "margin" not in got
    assert rows[0]["choice"] == "opt-1"
