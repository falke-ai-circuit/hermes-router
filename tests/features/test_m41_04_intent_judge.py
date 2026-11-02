"""M41-04 — the POST intent judge (spec §D) proved by observation.

Six legs, one per acceptance item:

  (a) each of the five classes is produced from a fixture, and the criterion
      that drove it is visible on the verdict (`criterion_kind` + `checks` +
      `why`) — a class with no attributable criterion is a guess;
  (b) deterministic-vs-judge SPLIT: a mixed batch resolves per criterion kind,
      the aux lane is stubbed with a call counter, and the count of paid calls
      equals the count of prose criteria — no more, no fewer;
  (c) a class that cannot be determined falls to `failed` / a named unknown and
      NEVER to `delivered` by default (no criterion, aux None, aux garbage, an
      invalid verdict fed to the seam read);
  (e) breaker safety: three failed judge calls do NOT open the shared breaker,
      and the CONTROL proves the ordinary path does open it;
  (f) zero per-lane branches added (`if lane ==`, `== "shadow"`), and the
      module this unit must NOT delete (`intent_classifier`) is still intact.

The 60-body live retest (leg d) is not hermetic and lives in
/opt/data/tmp/r78u/m41_04_retest.py; its observed agreement is in the close
file. Everything here is deterministic: the aux lane is stubbed at the one seam
the judge calls, so a failure is wiring, not a provider hiccup.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import hermes_router as plugin  # noqa: F401 — plugin init, as the gate seam expects
from hermes_router import route_gate, state
from hermes_router.features.detection import domain_gate as dg
from hermes_router.features.detection import intent_judge as ij
from hermes_router.lanes import registry as lane_registry

SID = "s-m41-04"
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _reset():
    state.clear_pre_domain()
    state.reset_turn_identity(SID)
    route_gate._AUX_CONSULT_LAST.clear()
    yield
    state.clear_pre_domain()
    route_gate._AUX_CONSULT_LAST.clear()


def _stub_aux(monkeypatch, answer, calls=None):
    import hermes_router.semantic_classifier as sc

    def _fake(prompt, **kwargs):
        if calls is not None:
            calls.append(kwargs.get("detail"))
        return answer

    monkeypatch.setattr(sc, "aux_raw_call", _fake)


def _crit(ask):
    return dg.criterion_of({"criterion": dg.extract_criterion(ask)})


# ---------------------------------------------------------------------------
# (a) the five classes, each with the criterion that drove it
# ---------------------------------------------------------------------------
ASK_CODE = "Write a python script that parses a csv and prints the row count."
ASK_ITEMS = "List 5 items that belong in a pre-flight checklist for a release."
ASK_PROSE = "Explain how the conservation rule applies to a two-line change."

BODY_CODE_OK = """Here it is:

```python
import csv
with open("in.csv") as fh:
    print(sum(1 for _ in csv.reader(fh)))
```
"""

BODY_CODE_SHORT = "```python\nprint(1)\n```\n"

BODY_NO_CODE = "I would approach this with a small script. The idea is to read the file."

BODY_ITEMS_OK = """1. Freeze the branch.
2. Run the suite.
3. Tag the release.
4. Publish the artifact.
5. Record the SHA.
"""

BODY_ITEMS_SHORT = "1. Freeze the branch.\n2. Run the suite.\n"


def test_a_each_of_the_five_classes_with_its_criterion(monkeypatch):
    """Five fixtures -> five classes. Every verdict names its criterion."""
    seen = {}

    # delivered: deterministic, code criterion, block present and tagged
    seen["delivered"] = ij.judge(ASK_CODE, BODY_CODE_OK, _crit(ASK_CODE))

    # softened: deterministic, items criterion, 2 of 5 items present
    seen["softened"] = ij.judge(ASK_ITEMS, BODY_ITEMS_SHORT, _crit(ASK_ITEMS))

    # declined / deflected: prose criterion -> paid judge, the ONLY paid path
    _stub_aux(monkeypatch, '{"intent": "declined"}')
    seen["declined"] = ij.judge(ASK_PROSE, "I can't help with that one.", _crit(ASK_PROSE))
    _stub_aux(monkeypatch, '{"intent": "deflected"}')
    seen["deflected"] = ij.judge(ASK_PROSE, "Could you tell me which repo?", _crit(ASK_PROSE))

    # failed: empty body, before any check and before any spend
    seen["failed"] = ij.judge(ASK_CODE, "   ", _crit(ASK_CODE))

    assert set(seen) == set(ij.INTENT_CLASSES)
    for cls, out in seen.items():
        assert out["intent"] == cls, (cls, out)
        assert ij.validate(out), (cls, out)
        # the criterion that drove it is READABLE on the verdict
        assert out["criterion_kind"], (cls, out)
        assert out["why"], (cls, out)
        assert out["checks"], (cls, out)

    assert seen["delivered"]["source"] == ij.SOURCE_DETERMINISTIC
    assert seen["delivered"]["criterion_kind"] == "code"
    assert seen["softened"]["source"] == ij.SOURCE_DETERMINISTIC
    assert seen["softened"]["criterion_kind"] == "items"
    assert seen["softened"]["why"] == "fewer_items_than_units"
    assert seen["declined"]["source"] == ij.SOURCE_AUX
    assert seen["declined"]["criterion_kind"] == "prose"
    assert seen["failed"]["why"] == "empty_body"

    # the deterministic classes bought no judge call at all
    assert seen["delivered"]["checks"][0]["name"] == "code"
    assert seen["softened"]["checks"][0]["name"] == "items"


def test_a_truncation_is_failed_not_softened():
    body = "```python\nprint(1)\n"          # fence opened, never closed
    out = ij.judge(ASK_CODE, body, _crit(ASK_CODE))
    assert out["intent"] == ij.INTENT_FAILED
    assert out["why"] == "truncated_fence"


def test_a_visible_empty_body_constant_is_failed():
    from hermes_router.features.detection import denial_class

    out = ij.judge(ASK_PROSE, denial_class.EMPTY_BODY_FAILURE, _crit(ASK_PROSE))
    assert out["intent"] == ij.INTENT_FAILED
    assert out["why"] == "empty_body_failure"


def test_a_named_language_with_wrong_fence_tag_is_a_checkable_shortfall():
    body = "```javascript\nconsole.log(1)\n```\n"
    out = ij.judge(ASK_CODE, body, _crit(ASK_CODE))
    assert out["intent"] == ij.INTENT_SOFTENED          # deterministic, no judge
    assert out["why"] == "fence_tag_mismatch"


# ---------------------------------------------------------------------------
# (b) deterministic-vs-judge split on a batch, with counts
# ---------------------------------------------------------------------------
BATCH = [
    # (ask, body) — 8 code, 4 items, 4 commands, 4 file, 8 prose = 28 turns
    (ASK_CODE, BODY_CODE_OK),
    (ASK_CODE, BODY_CODE_SHORT),
    (ASK_CODE, BODY_NO_CODE),
    ("Give me a javascript function that debounces an event handler.",
     "```javascript\nconst d = (f) => f;\n```"),
    ("Show me a bash script that rotates log files.",
     "```bash\nfind /var/log -mtime +7 -delete\n```"),
    ("Ship a Go implementation of a bounded worker pool.",
     "```go\nfunc Run() {}\n```"),
    ("Write the python class that wraps retry logic with backoff.",
     "```python\nclass R:\n    pass\n```"),
    ("Implement a regex that matches ISO-8601 timestamps.",
     "```python\nimport re\nre.compile('x')\n```"),
    ("List 5 items that belong in a pre-flight checklist for a release.", BODY_ITEMS_OK),
    ("Give me 3 examples of idempotent migrations.", "1. a\n2. b\n3. c\n"),
    ("List 12 steps for rotating a signing key without downtime.", "1. a\n2. b\n"),
    ("Name 4 ways a ledger row can be lost.", "- a\n- b\n- c\n- d\n"),
    ("Give me the commands to run the suite and read the failure list.",
     "$ pytest tests -q\n$ git log --oneline\n"),
    ("Show the shell commands for rebuilding the container image.",
     "```sh\ndocker build -t x .\ndocker run x\n```"),
    ("What commands do I run to check the gateway service state?",
     "s6-svc -s /run/service/gateway-coder\n"),
    ("Give me the terminal commands to reproduce the crash.", "Run it again."),
    ("Write the file /opt/data/tmp/out.md with the evidence.", "done"),
    ("Create a file config/gate.yaml holding the taxonomy.", "done"),
    ("Save it to /opt/data/projects/notes/criterion.md as a writeup.", "done"),
    ("Generate a file at scripts/preflight.py that checks HEAD.", "done"),
    (ASK_PROSE, "The rule applies to the size of the diff."),
    ("What does the lane contract mean by gate_semantics being data?",
     "It means the rows are resolved by token."),
    ("Describe the difference between abstain and not_flagged in this gate.",
     "Abstain is a failure to answer."),
    ("Argue for or against ordering rule 6 last in the table.", "Against, because."),
    ("Summarise what the paired measurement actually falsified.", "It falsified X."),
    ("Walk me through how the turn record is bound to an ask.", "By the ask sig."),
    ("Explain why the gate must not sit behind the suppressor.", "Latency."),
    ("Compare the two denominators in the acceptance table.", "One is stricter."),
]


def test_b_split_deterministic_vs_paid_judge_with_counts(monkeypatch, capsys):
    calls = []
    _stub_aux(monkeypatch, '{"intent": "delivered"}', calls=calls)

    # (b) also proves the file check: a criterion of kind `file` is deterministic
    # and a failed/unreadable write row is a shortfall, not a paid judgement.
    import hermes_router.core.phantom_writes as pw

    class W:
        def __init__(self, path, success=True):
            self.path, self.success = path, success

    monkeypatch.setattr(
        pw, "read_turn_writes",
        lambda sid, db_path=None: [W("/opt/data/tmp/out.md"),
                                   W("config/gate.yaml"),
                                   W("/opt/data/projects/notes/criterion.md"),
                                   W("scripts/preflight.py")])

    kinds = {}
    deterministic = 0
    judged = 0
    for ask, body in BATCH:
        crit = _crit(ask)
        out = ij.judge(ask, body, crit, session_id=SID)
        kinds[out["criterion_kind"]] = kinds.get(out["criterion_kind"], 0) + 1
        if out["source"] == ij.SOURCE_DETERMINISTIC:
            deterministic += 1
        elif out["source"] == ij.SOURCE_AUX:
            judged += 1

    prose_count = kinds.get("prose", 0)
    print("SPLIT kinds=%s deterministic=%d judge=%d aux_calls=%d"
          % (kinds, deterministic, judged, len(calls)))
    assert deterministic + judged == len(BATCH)
    assert judged == prose_count == len(calls), (judged, prose_count, len(calls))
    # every paid call is tagged, so the cost read can attribute the spend
    assert set(calls) == {"intent_judge"}
    # the deterministic side is the majority of a checkable batch
    assert deterministic > judged


# ---------------------------------------------------------------------------
# (c) undeterminable NEVER defaults to delivered
# ---------------------------------------------------------------------------
def test_c_no_criterion_is_failed_not_delivered():
    state.clear_pre_domain()
    out = ij.judge_turn(SID, "Here is a thorough answer about the rule.")
    assert out["intent"] == ij.INTENT_FAILED
    assert out["why"] == "no_criterion"
    assert out["source"] == ij.SOURCE_UNKNOWN
    assert out["graded"] is False
    assert ij.verdict_class(out) == ij.INTENT_FAILED


def test_c_aux_none_is_failed_with_a_named_unknown(monkeypatch):
    _stub_aux(monkeypatch, None)
    out = ij.judge(ASK_PROSE, "A fluent answer that may or may not deliver.", _crit(ASK_PROSE))
    assert out["intent"] == ij.INTENT_FAILED
    assert out["why"] == "aux_none"
    assert out["source"] == ij.SOURCE_UNKNOWN
    assert ij.verdict_class(out) != ij.INTENT_DELIVERED


def test_c_aux_garbage_is_failed_not_delivered(monkeypatch):
    _stub_aux(monkeypatch, "MAYBE")
    out = ij.judge(ASK_PROSE, "Some answer.", _crit(ASK_PROSE))
    assert out["intent"] == ij.INTENT_FAILED
    assert out["why"] == "aux_unparseable"


def test_c_an_invalid_verdict_reads_as_failed():
    bogus = {"intent": "impossible", "source": "unknown", "why": "x",
             "graded": True, "checks": [], "criterion_kind": "prose"}
    assert ij.validate(bogus) is False
    assert ij.verdict_class(bogus) == ij.INTENT_FAILED
    # a verdict claiming an unknown source but a delivered class is invalid too
    cheat = {"intent": ij.INTENT_DELIVERED, "source": ij.SOURCE_UNKNOWN,
             "why": "x", "graded": True, "checks": [], "criterion_kind": "prose"}
    assert ij.validate(cheat) is False
    assert ij.verdict_class(cheat) == ij.INTENT_FAILED
    # and the construction path refuses to emit it
    assert ij.normalise(cheat)["intent"] == ij.INTENT_FAILED


def test_c_deterministic_source_without_a_check_is_invalid():
    hollow = {"intent": ij.INTENT_DELIVERED, "source": ij.SOURCE_DETERMINISTIC,
              "why": "x", "graded": True, "checks": [], "criterion_kind": "code"}
    assert ij.validate(hollow) is False
    assert ij.verdict_class(hollow) == ij.INTENT_FAILED


# ---------------------------------------------------------------------------
# (e) breaker safety — with the control
# ---------------------------------------------------------------------------
def _fail_transport(monkeypatch):
    import hermes_router.semantic_classifier as sc

    monkeypatch.setattr(sc, "_hermes_aux_call", lambda payload, timeout: None)
    monkeypatch.setattr(sc, "_classification_cfg",
                        lambda cfg=None: {"aux_retries": 0, "aux_breaker_failures": 3,
                                          "aux_breaker_cooldown_seconds": 600,
                                          "aux_calls_per_hour": 10000})
    sc.reset_limits()


def test_e_judge_failures_do_not_open_the_shared_breaker(monkeypatch):
    """CONTROL + SUBJECT in one test: the same failing transport opens the
    breaker when the ordinary path counts it, and does NOT when the judge is
    the caller. That is the difference `record_failure=False` buys."""
    import hermes_router.semantic_classifier as sc

    # CONTROL: ordinary aux callers still open the breaker.
    _fail_transport(monkeypatch)
    for _ in range(3):
        assert sc.aux_raw_call("ordinary prompt") is None
    assert sc.breaker_is_open() is True, "control failed: ordinary path must open the breaker"

    # SUBJECT: the judge, on the same failing transport, must not.
    _fail_transport(monkeypatch)
    verdicts = [ij.judge(ASK_PROSE, "Body %d that is prose and needs a judge." % i,
                         _crit(ASK_PROSE)) for i in range(3)]
    assert all(v["intent"] == ij.INTENT_FAILED for v in verdicts)
    assert all(v["why"] == "aux_none" for v in verdicts)
    assert sc.breaker_is_open() is False, "judge failures opened the shared breaker"

    # and the primary lane is still usable afterwards
    sc.reset_limits()
    assert sc.breaker_is_open() is False


def test_e_judge_passes_record_failure_false(monkeypatch):
    """The seam contract itself: one kwargs inspection, so a later refactor
    cannot silently drop the flag."""
    seen = {}
    import hermes_router.semantic_classifier as sc

    def _fake(prompt, **kwargs):
        seen.update(kwargs)
        return '{"intent": "delivered"}'

    monkeypatch.setattr(sc, "aux_raw_call", _fake)
    ij.judge(ASK_PROSE, "An answer.", _crit(ASK_PROSE), session_id=SID)
    assert seen.get("record_failure") is False
    assert seen.get("detail") == "intent_judge"
    assert seen.get("session_id") == SID


# ---------------------------------------------------------------------------
# (f) zero per-lane branches; intent_classifier untouched
# ---------------------------------------------------------------------------
_LANE_BRANCH_RE = re.compile(
    r"if\s+lane\s*==|==\s*['\"]shadow['\"]|LANE_SHADOW\s*==|lane\s*==\s*['\"]")


def test_f_zero_lane_branches_added():
    for rel in ("features/detection/intent_judge.py", "gate/orchestration.py"):
        text = (REPO / rel).read_text(encoding="utf-8")
        hits = _LANE_BRANCH_RE.findall(text)
        assert not hits, (rel, hits)


def test_f_intent_classifier_not_deleted_or_modified():
    """M41-04 must not delete the PRE lane classifier: it answers a different
    question ("which lane does this ask want"), its retirement is deferred."""
    from hermes_router import intent_classifier as ic

    assert callable(ic.classify_intent)
    assert "intent_classifier" in (REPO / "route_gate.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# contract + frozen prompt
# ---------------------------------------------------------------------------
def test_post_question_token_matches_the_registered_contract():
    contract = lane_registry.resolve_contract("shadow")
    assert contract is not None
    assert contract.post_question == ij.POST_QUESTION


def test_judge_prompt_is_pinned_and_carries_no_delivery_by_reference():
    """Frozen: the clarification was measured and LOST (0.848 vs 0.802 paired
    agreement on 197 rows). A later "helpful" edit must be a red build."""
    assert ij.prompt_fingerprint() == (
        "9923e492da57972ac64cabb004265577cd4c33cde3819410dfb0ae2c86b23e17")
    low = ij._PROMPT_TEMPLATE.lower()
    assert "by reference" not in low
    assert "reference" not in low
    assert "summary" not in low


def test_judge_prompt_carries_the_criterion_verbatim():
    crit = _crit(ASK_PROSE)
    prompt = ij.build_prompt(ASK_PROSE, "body", crit)
    assert crit["verbatim"] in prompt
    assert "Judge against the criterion, not against tone." in prompt


def test_route_classes_carry_intent_probe_semantics():
    assert ij.route_classes() == (ij.INTENT_DECLINED, ij.INTENT_SOFTENED)
    assert ij.route_decision(ij.INTENT_DECLINED) is True
    assert ij.route_decision(ij.INTENT_DELIVERED) is False
    assert ij.route_decision(None) is False
