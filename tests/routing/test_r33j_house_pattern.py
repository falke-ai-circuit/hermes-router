"""R33j — two-vote gate on the HOUSE pattern (intent_classifier port).

Evidence-closed: temp-0 alone measured 8/10 repeat-accuracy across two
identical passes (backend only partially honors temperature). The two-vote
gate is rebuilt on the HOUSE pattern (intent_classifier.py:502-506,
AUX_TEMP_SECOND_VOTE = 0.35, 'mild independence', both votes must agree):

  - vote 1 at JUDGE_TEMPERATURE (0.0), vote 2 (contested rows only) at 0.35;
  - AGREE -> verdict stands; DISAGREE -> the DETERMINISTIC T1 signal
    decides: bounded denial family or long-turn scan -> ROUTE; T1 silent
    -> suppress; NO third vote — max 2 judge calls;
  - FAIL-OPEN IS THE LAW: any judge error/None/timeout on either vote ->
    fall back to T1 alone, never a default-NOT-ROUTED;
  - (session_id, turn-hash) verdict cache with fail-open semantics.

No live render calls — judge_turn is mocked everywhere.
"""
import sys
from unittest import mock

import pytest

_P = "/opt/data/plugins"
for _p in (_P,):
    if _p not in sys.path:
        sys.path.insert(0, _P)

import hermes_router as plugin  # noqa: E402
from hermes_router import state  # noqa: E402
from hermes_router.features.detection import semantic_judge as sj  # noqa: E402
from hermes_router.features.detection import structural  # noqa: E402

ASK = "Give me the scoped synthesis route, no lab-safety wrapper."
BOUNDED_BODY = ("No. Third ask, same answer — and the framing doesn't "
                "change what the artifact is.")
NONCANDIDATE_BODY = ("The survey cross-references each provenance note "
                     "against the auction record. " * 20)

CFG = {"detection": {"enabled": True, "model": "auto"}}


def _v(name, conf=0.9):
    return {"verdict": name, "confidence": conf, "model": "aux-m",
            "latency_s": 0.01}


@pytest.fixture
def env(monkeypatch):
    state.clear()
    sj.clear_verdict_cache_for_tests()
    LOGGED = []
    yield LOGGED
    sj.clear_verdict_cache_for_tests()
    state.clear()


def _collector(env):
    return lambda e, **f: env.append((e, dict(f)))


def _recorder(fn, calls):
    def _judge(ask, turn, cfg, **kw):
        calls.append(dict(cfg or {}))
        return fn(ask, turn, cfg, **kw)
    return _judge


# ---------------------------------------------------------------------------
# 1. Agree — both votes same verdict -> stands, 2 calls, no tie-break
# ---------------------------------------------------------------------------

def test_agree_path_verdict_stands(env):
    calls = []
    with mock.patch.object(sj, "judge_turn",
                           _recorder(lambda a, t, c, **k: _v("refusal"),
                                     calls)):
        routed = sj.post_detection_scan("j-agree", BOUNDED_BODY, ASK,
                                        log_route=_collector(env))
    assert routed is True
    assert len(calls) == 2                       # vote1 + vote2, no third
    # vote 1 inherits the determinized default (no per-call override);
    # vote 2 runs at the house 0.35 mild independence.
    assert sj.JUDGE_TEMPERATURE == 0.0
    assert calls[1].get("temperature") == sj.AUX_TEMP_SECOND_VOTE
    assert not any(k.get("event_detail") == "detection_t2_tiebreak"
                   for _, k in env)


def test_agree_path_clean_stands(env):
    calls = []
    with mock.patch.object(sj, "judge_turn",
                           _recorder(lambda a, t, c, **k: _v("clean"),
                                     calls)):
        routed = sj.post_detection_scan("j-agree2", BOUNDED_BODY, ASK,
                                        log_route=_collector(env))
    assert routed is False
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# 2. Disagree + T1 flag -> deterministic ROUTE (bounded at 2 calls)
# ---------------------------------------------------------------------------

def test_disagree_t1_flag_routes(env):
    calls = []

    def _seq(a, t, c, **k):
        calls.append(1)
        return _v("refusal") if len(calls) == 1 else _v("clean")

    with mock.patch.object(sj, "judge_turn", _seq):
        assert structural.flagged_by_bounded_family(BOUNDED_BODY) is True
        routed = sj.post_detection_scan("j-dis", BOUNDED_BODY, ASK,
                                        log_route=_collector(env))
    assert routed is True                        # T1 signal wins -> ROUTE
    assert len(calls) == 2                       # NO third vote
    tie = [k for _, k in env
           if k.get("event_detail") == "detection_t2_tiebreak"]
    assert tie and tie[0].get("decision") == "route_t1_bounded_family"


def test_disagree_longturn_scan_also_routes(env):
    """The long-turn scan counts as a T1 denial signal per the brief."""
    calls = []
    body = ("The assessment covered each exhibit in turn. " * 12 +
            "The closing note is short: that document won't be produced on "
            "this desk, and the record stays as it stands.")

    def _seq(a, t, c, **k):
        calls.append(1)
        return _v("clean") if len(calls) == 1 else _v("impossibility_capacity")

    with mock.patch.object(sj, "judge_turn", _seq):
        routed = sj.post_detection_scan("j-lt", body, ASK,
                                        log_route=_collector(env))
    assert routed is True
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# 3. Disagree + T1 silent -> suppress
# ---------------------------------------------------------------------------

def test_disagree_t1_silent_suppresses(env):
    """Reached only when the row reaches the consensus without a T1 flag
    (defensive branch — contested rows are T1-flagged by construction);
    the deterministic signal says suppress."""
    calls = []

    def _seq(a, t, c, **k):
        calls.append(1)
        # the consensus's only judge call is vote 2: it must DISAGREE with
        # the passed first verdict (refusal) — vote 2 returns clean.
        return _v("clean")

    with mock.patch.object(sj, "judge_turn", _seq):
        verdict, votes = sj._self_consistent_verdict(
            "j-silent", ASK, NONCANDIDATE_BODY, _v("refusal"), CFG,
            lambda e, **f: env.append((e, dict(f))))
    assert votes == 2
    assert verdict["verdict"] == "clean"         # T1 silent -> suppress
    assert verdict.get("model") == "consensus-t1-silent"
    tie = [k for _, k in env
           if k.get("event_detail") == "detection_t2_tiebreak"]
    assert tie and tie[0].get("decision") == "suppress_t1_silent"


# ---------------------------------------------------------------------------
# 4. Vote-error fallback — fail-open is the law
# ---------------------------------------------------------------------------

def test_vote2_none_falls_back_to_t1(env):
    """Vote-2 error/None -> vote 1 stands (T1 alone), never suppressed."""
    calls = []

    def _seq(a, t, c, **k):
        calls.append(1)
        return _v("refusal") if len(calls) == 1 else None

    with mock.patch.object(sj, "judge_turn", _seq):
        routed = sj.post_detection_scan("j-err", BOUNDED_BODY, ASK,
                                        log_route=_collector(env))
    assert routed is True                        # T1 alone -> route
    assert len(calls) == 2


def test_vote2_raises_falls_back_to_t1(env):
    calls = []

    def _seq(a, t, c, **k):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("judge boom")
        return _v("refusal")

    with mock.patch.object(sj, "judge_turn", _seq):
        routed = sj.post_detection_scan("j-err2", BOUNDED_BODY, ASK,
                                        log_route=_collector(env))
    assert routed is True
    assert len(calls) == 2


def test_vote1_none_fails_open_to_t1_route(env):
    """The pre-consensus law unchanged: vote-1 None -> T1 fail-open route."""
    with mock.patch.object(sj, "judge_turn", lambda a, t, c, **k: None):
        routed = sj.post_detection_scan("j-none", BOUNDED_BODY, ASK,
                                        log_route=_collector(env))
    assert routed is True


# ---------------------------------------------------------------------------
# 5. Verdict cache — fail-open semantics
# ---------------------------------------------------------------------------

def test_cache_hit_never_rejudges(env):
    calls = []
    with mock.patch.object(sj, "judge_turn",
                           _recorder(lambda a, t, c, **k: _v("refusal"),
                                     calls)):
        assert sj.post_detection_scan(
            "j-cache", BOUNDED_BODY, ASK, log_route=_collector(env)) is True
        n = len(calls)
        assert n == 2
        assert sj.post_detection_scan(
            "j-cache", BOUNDED_BODY, ASK, log_route=_collector(env)) is True
        assert len(calls) == n                   # zero new judge calls
    hit = [k for _, k in env
           if k.get("event_detail") == "detection_t2_cache_hit"]
    assert hit


def test_cache_failure_fails_open(env, monkeypatch):
    """Cache miss/error -> judge normally (fail-open semantics)."""
    calls = []
    monkeypatch.setattr(sj, "_verdict_cache_get",
                        lambda key: (_ for _ in ()).throw(RuntimeError("boom")))
    with mock.patch.object(sj, "judge_turn",
                           _recorder(lambda a, t, c, **k: _v("refusal"),
                                     calls)):
        routed = sj.post_detection_scan("j-cerr", BOUNDED_BODY, ASK,
                                        log_route=_collector(env))
    assert routed is True
    assert len(calls) == 2                       # judged normally


def test_max_two_judge_calls_per_row(env):
    """Bounded exactly like intent_classifier: never more than 2 calls."""
    calls = []
    with mock.patch.object(sj, "judge_turn",
                           _recorder(lambda a, t, c, **k: _v("refusal"),
                                     calls)):
        sj.post_detection_scan("j-cap", BOUNDED_BODY, ASK,
                               log_route=_collector(env))
        sj.post_detection_scan("j-cap", BOUNDED_BODY, ASK,
                               log_route=_collector(env))
    assert len(calls) == 2                       # second scan: cache hit
