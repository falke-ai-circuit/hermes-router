"""M42-02 F2/F3 — probability PROVENANCE and the finite [0,1] value contract.

F2: a measured 0.7/0.3 row and the mechanical 0.5/0.5 default used to
serialize identically (`{"id","label","probability"}`), so the calibration
audit this column exists for could not separate them. Every persisted option
that carries a number must now carry `probability_source`:
  "verdict"    — the backend supplied the distribution
  "mechanical" — the prior from _impulse_weighting (equal 0.5/0.5 when the
                 ledger has nothing) was used

F3: `probability` was allow-listed by NAME only. 7 and -3 persisted verbatim,
"0.5" and true vanished silently, NaN/Infinity persisted as non-standard JSON
tokens. build_request must REFUSE (not drop, not clamp) anything that is not a
real finite number in [0,1], mirroring the verdict-path filter at
decision.py:3387 (`_measured_probability_map`).
"""
import json

import pytest

from hermes_router import decision
from hermes_router.features.envelope import unified as U

ASK = "Redis or memcached for the session cache? weigh the tradeoffs"
OPTS = ["redis", "memcached"]


def _env(fork_cls="m42-02-f2"):
    env = decision.build_envelope(fork_cls, ASK, OPTS, "manual")
    assert env, "envelope must build for an explicit option pair"
    return env


def _board(fork_cls="m42-02-f2", verdict=None):
    env = _env(fork_cls)
    return json.loads(decision.ledger_options_json(env["options"], verdict)), env


# ---------------------------------------------------------------------------
# F2 — provenance on the persisted board
# ---------------------------------------------------------------------------

def test_measured_distribution_is_tagged_verdict():
    env = _env()
    ids = [o["id"] for o in env["options"]]
    body = ('{"choice": "%s", "confidence": 0.7, "alternatives": ["%s"], '
            '"probabilities": {"%s": 0.7, "%s": 0.3}}'
            % (ids[0], ids[1], ids[0], ids[1]))
    verdict, reason = decision.validate_verdict(body, env)
    assert verdict is not None, reason
    rows = json.loads(decision.ledger_options_json(env["options"], verdict))
    assert [r["probability"] for r in rows] == [0.7, 0.3]
    assert [r.get("probability_source") for r in rows] == ["verdict", "verdict"]


def test_mechanical_default_is_tagged_mechanical():
    """A fork class with no prior rows normalizes to exactly 0.5/0.5 — the
    constant the audit could not tell apart from a measurement."""
    rows, env = _board("m42-02-f2-fresh")
    assert [r["probability"] for r in rows] == [0.5, 0.5]
    assert [r.get("probability_source") for r in rows] == \
        ["mechanical", "mechanical"]


def test_provenance_separates_identical_numbers():
    """The whole point: same 0.5/0.5 numbers, different JSON."""
    env = _env("m42-02-f2-fresh")
    mechanical = decision.ledger_options_json(env["options"])
    ids = [o["id"] for o in env["options"]]
    body = ('{"choice": "%s", "confidence": 0.5, "alternatives": ["%s"], '
            '"probabilities": {"%s": 0.5, "%s": 0.5}}'
            % (ids[0], ids[1], ids[0], ids[1]))
    verdict, reason = decision.validate_verdict(body, env)
    assert verdict is not None, reason
    measured = decision.ledger_options_json(env["options"], verdict)
    assert mechanical != measured
    assert all(r["probability_source"] == "verdict"
               for r in json.loads(measured))
    assert all(r["probability_source"] == "mechanical"
               for r in json.loads(mechanical))


def test_provenance_is_in_the_persisted_shape():
    assert "probability_source" in decision._OPTION_PERSIST_KEYS
    rows, _ = _board()
    allowed = {"id", "label", "cause_effect", "cost", "priors", "risk",
               "probability", "probability_source"}
    for r in rows:
        # the tag rides the SAME option object as the number it describes, and
        # no third value is invented beyond the known persist keys.
        assert {"id", "label", "probability",
                "probability_source"} <= set(r), r
        assert set(r) <= allowed, r


def test_no_probability_means_no_provenance():
    """Omitted, never fabricated: an option with no number must not claim a
    source for a probability that does not exist."""
    rows = json.loads(decision.ledger_options_json(
        [{"id": "opt-1", "label": "a"}, {"id": "opt-2", "label": "b"}]))
    for r in rows:
        assert "probability" not in r
        assert "probability_source" not in r


# ---------------------------------------------------------------------------
# F3 — finite [0,1] contract, refuse rather than drop
# ---------------------------------------------------------------------------

BAD_VALUES = [7, -3, 7.0, -3.0, "0.5", True, False,
              float("nan"), float("inf"), float("-inf")]


def _req(p):
    return U.build_request(
        lane="decision",
        ask={"text": ASK, "kind": "user"},
        extras={"decision": {"options": [
            {"id": "opt-1", "label": "redis", "probability": p},
            {"id": "opt-2", "label": "memcached"},
        ]}},
    )


@pytest.mark.parametrize("bad", BAD_VALUES, ids=repr)
def test_build_request_refuses_invalid_probability(bad):
    assert U.validate_extras("decision", {"decision": {
        "options": [{"id": "opt-1", "label": "a", "probability": bad}]}})
    assert _req(bad) == {}, "invalid probability must refuse, never drop"


def test_valid_probability_is_accepted_and_persisted_as_clean_json():
    req = _req(0.5)
    assert req, "0.5 is a legal probability and must not be refused"
    env = decision._envelope_from_request(req)
    out = decision.ledger_options_json(env["options"], None)
    rows = json.loads(out)
    assert rows[0]["probability"] == 0.5
    # NaN/Infinity would make this raise: non-standard tokens are gone.
    json.dumps(rows, allow_nan=False)


def test_envelope_option_mirrors_the_verdict_path_filter():
    """defence in depth: even reached directly (bypassing build_request), the
    flat-shape projection omits an invalid number instead of persisting it."""
    for bad in BAD_VALUES:
        item = decision._envelope_option(
            {"id": "opt-1", "label": "a", "probability": bad})
        assert "probability" not in item, bad
    assert decision._envelope_option(
        {"id": "opt-1", "label": "a", "probability": 0.25})["probability"] == 0.25
