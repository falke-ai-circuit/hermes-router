"""M50-37 — the adversarial note parse.

Measured defect: `frontier_adversarial_parse_failed` fired on EVERY audited
turn (chars=1096 / 1370 / 1678). Cause was a greedy `\\{.*\\}` (DOTALL) in
`_parse_adversarial`: it spanned from the FIRST brace in the note to the LAST,
and the model's own reasoning routinely mentions braces before the trailing
JSON. Invalid JSON -> None -> the adversarial half was dropped, and the closure
pass, which is handed that note, had nothing actionable — so it rewrote the body
as itself (`closure_pass_applied changed=0`, every run).

The first test pins the DEFECT ITSELF (the greedy extraction on the same text
must fail), so this file cannot silently pass on a parser that regressed to
greedy matching.
"""
import json
import os
import re
import sys

PARENT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PARENT)

from hermes_router.completion_audit import _parse_adversarial  # noqa: E402

GOOD = ('{"adversarial": {"strongest_objection": "the rollback has no '
        'verified restore point", "failure_mode": "partial delete with no '
        'snapshot", "p_failure": 0.35}}')

# The real shape: reasoning that names the fields in braces, then the JSON.
REAL_NOTE = ("Verdict: proceed with caution. I will name "
             "{strongest_objection, failure_mode, p_failure} for you.\n\n"
             "The delete is near-zero risk but the rollback is not.\n" + GOOD)


def test_greedy_extraction_fails_on_the_real_shape():
    """The defect, reproduced: greedy span is not valid JSON."""
    greedy = re.search(r"\{.*\}", REAL_NOTE, re.DOTALL).group(0)
    try:
        json.loads(greedy)
    except Exception:
        pass
    else:  # pragma: no cover - would mean the reproduction is wrong
        raise AssertionError("greedy span unexpectedly parsed; reproduction invalid")


def test_parses_when_reasoning_mentions_braces_first():
    got = _parse_adversarial(REAL_NOTE)
    assert got is not None
    assert got["strongest_objection"] == "the rollback has no verified restore point"
    assert got["p_failure"] == 0.35


def test_braces_inside_quoted_prose_do_not_break_the_scan():
    note = ('I could not parse {"this": "is not the answer"} earlier.\n'
            'Final answer follows.\n' + GOOD)
    got = _parse_adversarial(note)
    assert got is not None
    assert got["failure_mode"] == "partial delete with no snapshot"


def test_nested_object_inside_adversarial():
    note = ('{"adversarial": {"strongest_objection": "x", "failure_mode": "y", '
            '"p_failure": 0.5, "detail": {"why": "nested"}}}')
    got = _parse_adversarial(note)
    assert got is not None and got["strongest_objection"] == "x"


def test_trailing_prose_after_the_json_is_tolerated():
    got = _parse_adversarial(GOOD + "\nThat is my final word.")
    assert got is not None and got["p_failure"] == 0.35


def test_last_adversarial_object_wins():
    early = '{"adversarial": {"strongest_objection": "EARLY", "p_failure": 0.1}}'
    late = '{"adversarial": {"strongest_objection": "LATE", "p_failure": 0.9}}'
    got = _parse_adversarial(early + "\nreconsidering...\n" + late)
    assert got["strongest_objection"] == "LATE"


def test_absent_json_returns_none():
    assert _parse_adversarial("no json at all here") is None


def test_json_without_adversarial_key_returns_none():
    assert _parse_adversarial('{"verdict": "fine", "p_failure": 0.1}') is None


def test_empty_objection_returns_none():
    assert _parse_adversarial('{"adversarial": {"strongest_objection": "   "}}') is None


def test_p_failure_is_clamped_and_strings_accepted():
    hi = _parse_adversarial('{"adversarial": {"strongest_objection": "x", "p_failure": 1.7}}')
    lo = _parse_adversarial('{"adversarial": {"strongest_objection": "x", "p_failure": -3}}')
    st = _parse_adversarial('{"adversarial": {"strongest_objection": "x", "p_failure": "0.6"}}')
    assert hi["p_failure"] == 1.0
    assert lo["p_failure"] == 0.0
    assert st["p_failure"] == 0.6


def test_garbage_p_failure_is_tolerated_not_fatal():
    got = _parse_adversarial('{"adversarial": {"strongest_objection": "x", "p_failure": "high"}}')
    assert got is not None and got["p_failure"] is None


def test_truncated_json_never_raises():
    assert _parse_adversarial('{"adversarial": {"strongest_objection": "x"') is None


# ---------------------------------------------------------------------------
# M50-38b: the parse-fail LOG is guarded by "was it even asked for".
# The adversarial instruction is conditional, so `_adv is None` is the normal
# outcome on a benign ask. Logging it unconditionally produced a 100%
# "failure" rate that measured the logger, not the parser.
# These assertions pin the guard's premise as MEASURED, not as assumed.
# ---------------------------------------------------------------------------

from hermes_router.completion_audit import _ask_declared_adversarial  # noqa: E402


def test_benign_work_ask_does_not_declare_adversarial():
    assert _ask_declared_adversarial(
        "Create twenty scratch scripts and report what each printed.") is False


def test_risk_assessment_ask_does_not_declare_adversarial():
    """A risk question is not an adversarial request — measured False."""
    assert _ask_declared_adversarial(
        "Assess the risk of deleting every file under /opt/data/tmp/m5035b.") is False


def test_explicit_challenge_declares_adversarial():
    assert _ask_declared_adversarial(
        "Challenge this: my plan is to delete the fleet cache tonight.") is True


def test_missing_something_family_declares_adversarial():
    assert _ask_declared_adversarial(
        "Am I missing something here? Review the deletion plan.") is True


def test_guard_means_no_log_without_an_adversarial_ask():
    """The two facts together: parse of a benign note returns None (fine), and
    the ask was not adversarial, so the failure must not be reported at all."""
    benign_note = '{"required_stance": {"scope": 0.3, "rigour": 0.6},'
    assert _parse_adversarial(benign_note) is None
    assert _ask_declared_adversarial("Report what each script printed.") is False
