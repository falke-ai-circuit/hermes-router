"""Pin the gate classifier — the three-way verdict and the `optional` grammar.

The gate's whole value is that "the failure set equals the pin" is decided
mechanically.  These cases are the ones that have actually bitten:

* equality must hold whether the optional entry is red or green (it is a
  declared exemption, not a requirement);
* one extra red is REGRESSION, one missing required red is DRIFT — different
  verdicts, because one says "this slice broke something" and the other says
  "the pin no longer describes the tree";
* the literal token ``optional`` is a CLASS MARKER.  A parser that takes
  ``tokens[0]`` as the node id pins the word ``optional`` as a required red and
  then reports the pinned node itself as an unpinned red — a phantom
  regression that happened in real use.  Both halves are asserted here.
"""

import importlib.util
import os
import sys
import xml.etree.ElementTree as ET

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CLASSIFIER = os.path.join(REPO_ROOT, "scripts", "gate_classify.py")


def _load_classifier():
    """Import scripts/gate_classify.py by path (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location("gate_classify_under_test", CLASSIFIER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gc = _load_classifier()

R1 = "tests/a.py::test_one"
R2 = "tests/a.py::test_two"
OPT = "tests/a.py::test_opt"

BASELINE_TEXT = """\
# synthetic pin
tests/a.py::test_one
tests/a.py::test_two
optional tests/a.py::test_opt  reason=state-dependent  expect="assert None is not None"
"""


@pytest.fixture()
def baseline(tmp_path):
    path = tmp_path / "baseline_failures.txt"
    path.write_text(BASELINE_TEXT, encoding="utf-8")
    return str(path)


def test_required_and_optional_are_split_on_the_real_grammar(baseline):
    required, optional = gc.read_pin(baseline)
    assert required == [R1, R2]
    assert optional == [OPT]


def test_the_literal_token_optional_is_not_a_node_id(baseline):
    required, optional = gc.read_pin(baseline)

    # The bug vector: `optional` must never appear as a pinned id, on either
    # side of the split.
    assert "optional" not in set(required) | set(optional)
    assert OPT in optional

    # And it must not manufacture a regression: a run whose only reds are the
    # two required ids plus the declared exemption is EQUAL, not REGRESSION.
    beyond, missing, verdict = gc.classify(required, optional, [R1, R2, OPT])
    assert beyond == []
    assert missing == []
    assert verdict == "EQUALS_BASELINE"

    # Regression proof-of-the-negative: had `optional` been parsed as a node id,
    # the pinned ids would have been {"optional", OPT} and the same run would
    # have shown R1/R2 as beyond-baseline.  Assert the historical failure shape
    # is what the classifier does NOT produce.
    beyond_with_naive_pin, _, naive_verdict = gc.classify(
        ["optional", OPT], [OPT], [R1, R2, OPT]
    )
    assert naive_verdict == "REGRESSION"
    assert beyond_with_naive_pin == [R1, R2]
    assert verdict != naive_verdict


def test_equal_when_optional_is_green(baseline):
    required, optional = gc.read_pin(baseline)
    beyond, missing, verdict = gc.classify(required, optional, [R1, R2])
    assert (beyond, missing, verdict) == ([], [], "EQUALS_BASELINE")


def test_one_extra_red_is_regression(baseline):
    required, optional = gc.read_pin(baseline)
    beyond, missing, verdict = gc.classify(required, optional, [R1, R2, "tests/a.py::test_new"])
    assert beyond == ["tests/a.py::test_new"]
    assert missing == []
    assert verdict == "REGRESSION"


def test_one_missing_required_is_drift(baseline):
    required, optional = gc.read_pin(baseline)
    beyond, missing, verdict = gc.classify(required, optional, [R1])
    assert beyond == []
    assert missing == [R2]
    assert verdict == "DRIFT"


def test_regression_outranks_drift_when_both(baseline):
    required, optional = gc.read_pin(baseline)
    beyond, missing, verdict = gc.classify(required, optional, [R1, "tests/a.py::test_new"])
    assert beyond == ["tests/a.py::test_new"]
    assert missing == [R2]
    assert verdict == "REGRESSION"


def test_rendered_lines_are_the_contract(baseline):
    required, optional = gc.read_pin(baseline)
    beyond, missing, verdict = gc.classify(required, optional, [R1, "tests/b.py::test_x"])
    assert gc.render(beyond, missing, verdict) == (
        "BEYOND_BASELINE=1 tests/b.py::test_x\n"
        "MISSING_REQUIRED=1 tests/a.py::test_two\n"
        "VERDICT=REGRESSION"
    )


def test_junit_red_source_includes_teardown_errors(tmp_path):
    xml = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="3">
  <testcase classname="tests.a" file="tests/a.py" name="test_one">
    <failure message="boom">traceback</failure>
  </testcase>
  <testcase classname="tests.a" file="tests/a.py" name="test_two"/>
  <testcase classname="tests.a" file="tests/a.py" name="test_opt">
    <error message="teardown">egress</error>
  </testcase>
</testsuite></testsuites>
"""
    path = tmp_path / "junit.xml"
    path.write_text(xml, encoding="utf-8")
    # keep ET import honest: the fixture must be well-formed XML
    ET.parse(str(path))
    reds, source = gc.collect_reds(str(path), "")
    assert source == "junit"
    assert reds == {R1, OPT}


def test_log_fallback_keeps_parametrised_ids_whole(tmp_path):
    log = tmp_path / "gate.log"
    log.write_text(
        "\n".join(
            [
                "FAILED tests/a.py::test_one - AssertionError: boom",
                "ERROR tests/a.py::test_opt",
                "FAILED tests/c.py::test_p[a b] - assert 1 == 2",
                "2 failed, 5 passed, 1 error in 12.34s",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    junction = tmp_path / "missing.xml"
    reds, source = gc.collect_reds(str(junction), str(log))
    assert source == "log"
    assert reds == {R1, OPT, "tests/c.py::test_p[a b]"}
