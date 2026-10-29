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

PLUS the upstream half of the same measurement: the PARSER must not destroy the
distribution before the ledger can see it. `_systemone_verdict_content` used to
keep only the ranking (`alternatives`) and throw the magnitudes away, which is
why `_option_probability_map` returned {} for every row. The pins below hold the
parser to the same rule the ledger already follows: carry what was measured,
omit what was not — never a sentinel, never a clamp, never a made-up zero.
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


# --- the PARSER must not destroy the distribution ----------------------------
#
# typesafe answers shape: {answers: {choice: {choice, confidence,
# probabilities: {opt-id: p}}}}. Before this, the ranking survived as
# `alternatives` and the magnitudes were dropped, so the ledger's
# _option_probability_map saw nothing on every row.

ENV = {"options": [{"id": "opt-1"}, {"id": "opt-2"}, {"id": "opt-3"}]}


def _parse(choice="opt-1", confidence=0.8, probabilities=None, env=None):
    ans = {"choice": choice, "confidence": confidence}
    if probabilities is not None:
        ans["probabilities"] = probabilities
    data = {"answers": {"choice": ans}}
    return json.loads(
        decision._systemone_verdict_content(data, env or ENV))


def test_parser_carries_the_full_distribution_including_the_winner():
    """(a) a complete answers shape -> values preserved exactly, chosen id
    included. A distribution without its winner cannot be audited."""
    v = _parse(probabilities={"opt-1": 0.55, "opt-2": 0.3, "opt-3": 0.15})
    assert v["choice"] == "opt-1"
    assert v["probabilities"] == {"opt-1": 0.55, "opt-2": 0.3, "opt-3": 0.15}
    assert "opt-1" in v["probabilities"]        # the winner is IN the map
    assert v["confidence"] == 0.8               # and is still not a probability
    # only ids the envelope offered
    assert set(v["probabilities"]) <= {o["id"] for o in ENV["options"]}


def test_parser_never_persists_the_sort_sentinel_or_a_bool():
    """(b) unparseable / boolean / -1.0 entries are ABSENT from the map and no
    sentinel appears anywhere in the emitted JSON. -1.0 is a sort placeholder,
    not a measurement: writing it would read as data in a later audit."""
    v = _parse(probabilities={"opt-1": 0.5, "opt-2": "n/a", "opt-3": True})
    assert v["probabilities"] == {"opt-1": 0.5}
    assert "opt-2" not in v["probabilities"] and "opt-3" not in v["probabilities"]
    assert -1.0 not in v["probabilities"].values()
    assert "-1.0" not in json.dumps(v["probabilities"])
    # no numeric sentinel anywhere in the emitted JSON
    body = json.loads(json.dumps(v))
    assert all(isinstance(x, float) and x >= 0.0
               for x in body["probabilities"].values())
    # an explicit -1.0 the provider itself sends is equally not a measurement
    v2 = _parse(probabilities={"opt-1": 0.5, "opt-2": -1.0})
    assert v2["probabilities"] == {"opt-1": 0.5}


def test_parser_omits_out_of_range_values_instead_of_clamping():
    """(c) out-of-range means the provider's shape changed; inventing a bound
    would hide that. Omitted, not clamped."""
    v = _parse(probabilities={"opt-1": 0.7, "opt-2": 1.4, "opt-3": -0.2})
    assert v["probabilities"] == {"opt-1": 0.7}
    assert set(v["probabilities"]) == {"opt-1"}      # 1.4 not clamped to 1.0
    assert "opt-3" not in v["probabilities"]         # -0.2 not clamped to 0.0


def test_parser_omits_the_key_entirely_when_nothing_is_usable():
    """(5) no usable distribution -> no key at all, so nothing downstream can
    mistake absence for a measured zero. Behaviour of the early returns is
    unchanged."""
    assert "probabilities" not in _parse(probabilities={})
    assert "probabilities" not in _parse(probabilities=None)
    assert "probabilities" not in _parse(
        probabilities={"opt-2": "x", "opt-3": 2.0})
    # stand_down and out-of-set returns stay exactly as they were
    stand_down = json.loads(decision._systemone_verdict_content(
        {"answers": {"choice": {"choice": decision.STAND_DOWN_CHOICE,
                                "confidence": 0.1,
                                "probabilities": {"opt-1": 0.9}}}}, ENV))
    assert stand_down == {"choice": decision.STAND_DOWN_CHOICE,
                          "confidence": 0.0, "alternatives": []}
    assert decision._systemone_verdict_content(
        {"answers": {"choice": {"choice": "opt-9", "confidence": 0.9,
                                "probabilities": {"opt-1": 0.9}}}}, ENV) == ""


def test_parser_alternatives_are_unchanged_by_the_new_key():
    """(d) `alternatives` is pinned to today's output: same order (descending
    probability, sentinel-ranked unparseable entries last), same membership
    (ids only, chosen id excluded)."""
    probs = {"opt-3": 0.15, "opt-1": 0.55, "opt-2": 0.3}
    v = _parse(probabilities=probs)
    assert v["alternatives"] == ["opt-2", "opt-3"]
    # unparseable entries still rank last and stay id-only in `alts`
    v2 = _parse(probabilities={"opt-1": 0.55, "opt-2": "n/a", "opt-3": 0.15})
    assert v2["alternatives"] == ["opt-3", "opt-2"]
    assert all(isinstance(a, str) for a in v2["alternatives"])
    # out-of-set ids never enter `alts`, and gain no probability either
    v3 = _parse(probabilities={"opt-1": 0.55, "opt-9": 0.45})
    assert v3["alternatives"] == []
    assert "opt-9" not in v3["probabilities"]


# --- (e) end to end: parser -> ledger --------------------------------

def test_parser_verdict_reaches_options_json_end_to_end(env, monkeypatch):
    """(e) A verdict PRODUCED BY THIS PARSER, fed through the real fire path,
    lands its probabilities in decision_ledger.options_json."""
    def fake_backend(envelope, cfg):
        # answers built from the option ids the envelope actually offers
        ids = [o["id"] for o in envelope.get("options") or [] if isinstance(o, dict)]
        assert len(ids) == 2
        content = decision._systemone_verdict_content(
            {"answers": {"choice": {
                "choice": ids[0], "confidence": 0.8,
                "probabilities": {ids[0]: 0.72, ids[1]: 0.28}}}},
            envelope)
        assert content, "parser failed open on a valid answers shape"
        meta = {"model": "m", "endpoint": "e", "tokens_in": 10,
                "tokens_out": 5, "latency_s": 0.1}
        return (content, meta, "ok")

    monkeypatch.setattr(decision, "call_backend", fake_backend)
    cfg = dict(decision.DEFAULTS)
    cfg["enabled"] = True
    cfg["on_demand"] = {"manual": True, "midturn": True}
    monkeypatch.setattr(decision, "_cfg", lambda: cfg)
    envelope = decision.build_envelope(SID, ASK,
                                       decision._manual_verbatim_options(ASK),
                                       "manual", cfg)
    ids = [o["id"] for o in envelope["options"]]
    decision._v3_worker(envelope,
                        {"session_id": SID, "task_id": "t-e2e",
                         "trigger": "manual"}, cfg, lambda e, **f: None)

    rows = _rows(env)
    assert len(rows) == 1
    persisted = json.loads(rows[0]["options_json"])
    got = {o["id"]: o.get("probability") for o in persisted}
    assert got == {ids[0]: 0.72, ids[1]: 0.28}
    assert rows[0]["choice"] == ids[0]
    assert all(0.0 <= p <= 1.0 for p in got.values())
