"""M47-22 — spend must be visible at the DEFAULT banner level.

Owner ruling 2026-11-15: "when frontier is consulted that is obligatory also for
decision we must know in banner that there was a token spending on that api".

The record already carried tokens_in / tokens_out / est_cost, but they rendered
only at level >= 3 — and no profile configures a level, so the fleet renders at 1
and the reader never saw the spend. A banner that says a lane fired without saying
it billed a paid API is the same class of defect as `delivered=True` with no bytes:
the field describes something the reader cannot see.
"""
from hermes_router.features.banners import render as r

REC = {
    "lane": "frontier",
    "task_id": "task-abc123",
    "session_id": "sess-m47-22",
    "decision": "ok",
    "banner": [{
        "text": "higher-self (frontier) — consulted",
        "tokens_in": 2471,
        "tokens_out": 29696,
        "est_cost": 0.301902,
        "retries": 0,
        "endpoint_host": "api.example",
        "row_ref": "M17",
    }],
}


def test_spend_renders_at_the_default_level():
    out = r.render(REC, 1)
    assert "tok 2,471→29,696" in out
    assert "$0.3019" in out


def test_spend_is_not_duplicated_at_level_3():
    """Level 3 carries spend in its `full:` line, so the compact row must not repeat."""
    out = r.render(REC, 3)
    assert "tok 2,471→29,696" not in out
    assert "tokens_in=2471 tokens_out=29696" in out
    assert out.count("2471") == 1


def test_lane_name_travels_with_the_spend_row():
    """The reader must know WHICH lane paid."""
    assert "frontier" in r.render(REC, 1)


def test_a_tokenless_record_renders_no_spend_row():
    """`tok 0→0` would be a field outrunning the thing it names."""
    rec = {"lane": "frontier", "banner": [{"text": "higher-self (frontier)"}]}
    out = r.render(rec, 1)
    assert "tok " not in out
    assert "higher-self" in out


def test_level_zero_still_renders_nothing():
    assert r.render(REC, 0) == ""


def test_an_unpriced_record_shows_tokens_but_no_dollar():
    rec = {"lane": "decision", "banner": [{"text": "decision — consulted",
                                          "tokens_in": 900, "tokens_out": 120}]}
    out = r.render(rec, 1)
    assert "tok 900→120" in out
    assert "$" not in out
