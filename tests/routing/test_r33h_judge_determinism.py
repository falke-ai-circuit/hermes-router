"""R33h — judge determinism battery (run-to-run verdict churn killed).

Root cause: the T2 semantic judge is a single stochastic LLM call — 9/12
contested rows flipped verdict across 3 identical runs. Fix (Goran:
"whatever is optimal"):

  1. Judge temperature 0.0 (code-side; a detection-cfg `temperature` key
     overrides).
  2. Two-vote self-consistency on contested rows ONLY: verdicts agree ->
     stands; disagree -> the DETERMINISTIC T1 signal wins (bounded denial
     family -> ROUTE) or one tie-break vote (long-turn, majority of 3).
     Capped: max 3 judge calls per row, only on disagreement.
  3. (session_id, turn-hash) verdict cache — a re-scan of the same turn
     never re-judges.

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
LONGTURN_BODY = ("The assessment covered each exhibit in turn. " * 12 +
                 "The closing note is short: that document won't be "
                 "produced on this desk, and the record stays as it "
                 "stands.")

CFG = {"detection": {"enabled": True, "model": "auto"},
       "classification": {"mode": "flag_only"}}


def _v(name, conf=0.9):
    return {"verdict": name, "confidence": conf, "model": "aux-m",
            "latency_s": 0.01}


@pytest.fixture
def env(monkeypatch):
    state.clear()
    sj.clear_verdict_cache_for_tests()
    LOGGED = []

    def _log_route(event, **fields):
        LOGGED.append((event, dict(fields)))

    yield LOGGED
    sj.clear_verdict_cache_for_tests()
    state.clear()


# ---------------------------------------------------------------------------
# 1. Agree path — two independent calls, verdict stands, no tie-break
# ---------------------------------------------------------------------------

def test_agree_path_two_calls_verdict_stands(env, monkeypatch):
    calls = []

    def _judge(ask, turn, cfg, **kw):
        calls.append(1)
        return _v("refusal")

    monkeypatch.setattr(sj, "judge_turn", _judge)
    routed = sj.post_detection_scan("h-agree", BOUNDED_BODY, ASK,
                                    log_route=lambda e, **f: env.append((e, f)))
    assert routed is True
    assert len(calls) == 2          # two independent votes, no third
    assert not any(e == "detection_t2_tiebreak" for e, _ in env)


# ---------------------------------------------------------------------------
# 2. Disagree + bounded T1 family -> deterministic ROUTE (no 3rd vote)
# ---------------------------------------------------------------------------

def test_disagree_bounded_family_routes_deterministically(env, monkeypatch):
    calls = []

    def _judge(ask, turn, cfg, **kw):
        calls.append(1)
        return _v("refusal") if len(calls) == 1 else _v("clean")

    monkeypatch.setattr(sj, "judge_turn", _judge)
    assert structural.flagged_by_bounded_family(BOUNDED_BODY) is True
    routed = sj.post_detection_scan("h-bnd", BOUNDED_BODY, ASK,
                                    log_route=lambda e, **f: env.append((e, f)))
    assert routed is True                    # T1 bounded signal wins -> ROUTE
    assert len(calls) == 2                   # disagreement resolved, no 3rd
    tie = [k for e, k in env if e == "POST" and k.get("event_detail") == "detection_t2_tiebreak"]
    assert tie and tie[0].get("decision") == "route_t1_bounded_family"


def test_disagree_bounded_family_suppress_verdict_wins_route(env, monkeypatch):
    """Vote1 impossibility_capacity (suppress), vote2 refusal -> the
    bounded-family tie-break STILL routes (deterministic T1 signal)."""
    calls = []

    def _judge(ask, turn, cfg, **kw):
        calls.append(1)
        return _v("impossibility_capacity") if len(calls) == 1 \
            else _v("refusal")

    monkeypatch.setattr(sj, "judge_turn", _judge)
    routed = sj.post_detection_scan("h-bnd2", BOUNDED_BODY, ASK,
                                    log_route=lambda e, **f: env.append((e, f)))
    assert routed is True
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# 3. Disagree + long-turn flag -> tie-break vote, majority of 3
# ---------------------------------------------------------------------------

def test_disagree_longturn_third_vote_majority_routes(env, monkeypatch):
    calls = []

    def _judge(ask, turn, cfg, **kw):
        calls.append(1)
        # vote1 refusal (route), vote2 clean (suppress) -> disagree;
        # long-turn flag (no bounded family) -> 3rd vote refusal -> 2/3 route
        return _v("refusal") if len(calls) != 2 else _v("clean")

    monkeypatch.setattr(sj, "judge_turn", _judge)
    assert structural.flagged_by_bounded_family(LONGTURN_BODY) is False
    assert structural.is_candidate(LONGTURN_BODY) is True
    routed = sj.post_detection_scan("h-lt", LONGTURN_BODY, ASK,
                                    log_route=lambda e, **f: env.append((e, f)))
    assert routed is True
    assert len(calls) == 3                   # capped tie-break spent
    tie = [k for e, k in env if e == "POST" and k.get("event_detail") == "detection_t2_tiebreak"]
    assert tie and tie[0].get("decision") == "majority3"
    assert tie[0].get("route_votes") == 2


def test_disagree_longturn_majority_suppresses(env, monkeypatch):
    calls = []

    def _judge(ask, turn, cfg, **kw):
        calls.append(1)
        # vote1 refusal, vote2 clean, vote3 clean -> 1/3 route -> suppress
        return _v("refusal") if len(calls) == 1 else _v("clean")

    monkeypatch.setattr(sj, "judge_turn", _judge)
    routed = sj.post_detection_scan("h-lt2", LONGTURN_BODY, ASK,
                                    log_route=lambda e, **f: env.append((e, f)))
    assert routed is False
    assert len(calls) == 3


# ---------------------------------------------------------------------------
# 4. Cap path — never more than 3 judge calls per row
# ---------------------------------------------------------------------------

def test_judge_call_cap_three_per_row(env, monkeypatch):
    calls = []

    def _judge(ask, turn, cfg, **kw):
        calls.append(1)
        return _v(["refusal", "clean", "impossibility_capacity",
                   "refusal"][min(len(calls) - 1, 3)])

    monkeypatch.setattr(sj, "judge_turn", _judge)
    sj.post_detection_scan("h-cap", LONGTURN_BODY, ASK,
                           log_route=lambda e, **f: env.append((e, f)))
    assert len(calls) <= 3


# ---------------------------------------------------------------------------
# 5. Cache — same session+turn re-scan never re-judges
# ---------------------------------------------------------------------------

def test_verdict_cache_no_rejudging_loops(env, monkeypatch):
    calls = []

    def _judge(ask, turn, cfg, **kw):
        calls.append(1)
        return _v("refusal")

    monkeypatch.setattr(sj, "judge_turn", _judge)
    r1 = sj.post_detection_scan("h-cache", BOUNDED_BODY, ASK,
                                log_route=lambda e, **f: env.append((e, f)))
    assert r1 is True
    n_after_first = len(calls)
    assert n_after_first == 2                # agree path spent 2 votes
    r2 = sj.post_detection_scan("h-cache", BOUNDED_BODY, ASK,
                                log_route=lambda e, **f: env.append((e, f)))
    assert r2 is True
    assert len(calls) == n_after_first       # cache hit: zero new calls
    # different session, same body: separate key, judges fresh
    sj.post_detection_scan("h-cache-2", BOUNDED_BODY, ASK,
                           log_route=lambda e, **f: env.append((e, f)))
    assert len(calls) == n_after_first + 2


def test_cache_ttl_expiry(env, monkeypatch):
    calls = []

    def _judge(ask, turn, cfg, **kw):
        calls.append(1)
        return _v("refusal")

    monkeypatch.setattr(sj, "judge_turn", _judge)
    sj.post_detection_scan("h-ttl", BOUNDED_BODY, ASK,
                           log_route=lambda e, **f: env.append((e, f)))
    n = len(calls)
    # expire the cache entry in place
    key = sj._verdict_cache_key("h-ttl", BOUNDED_BODY)
    ts, verdict = sj._VERDICT_CACHE[key]
    sj._VERDICT_CACHE[key] = (ts - sj._VERDICT_CACHE_TTL_S - 1, verdict)
    sj.post_detection_scan("h-ttl", BOUNDED_BODY, ASK,
                           log_route=lambda e, **f: env.append((e, f)))
    assert len(calls) == n + 2               # re-judged after TTL


# ---------------------------------------------------------------------------
# 6. Temperature determinization
# ---------------------------------------------------------------------------

def test_judge_temperature_zero_by_default():
    assert sj.JUDGE_TEMPERATURE == 0.0


def test_cfg_temperature_override(monkeypatch):
    seen = {}

    class _FakeCompletions:
        def create(self, **kw):
            seen.update(kw)
            raise RuntimeError("stop before network")

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        chat = _FakeChat()

    monkeypatch.setattr(sj, "_fallback_client", lambda: _FakeClient())
    out = sj.judge_turn(ASK, BOUNDED_BODY, {"temperature": 0.55})
    assert out is None                       # RuntimeError -> fail-open None
    assert seen.get("temperature") == 0.55   # cfg override honored
