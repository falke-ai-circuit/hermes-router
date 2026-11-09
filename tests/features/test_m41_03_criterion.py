"""M41-03 — the acceptance criterion at PRE (spec §B S5, §C) proved by observation.

Five legs, one per acceptance item:

  (a) the criterion is present and non-empty on >= 90% of ELIGIBLE turns in a
      fixture batch (eligible = a non-empty ask; an empty ask has no criterion
      and belongs to leg c);
  (b) no new persistence path: the criterion rides the EXISTING per-session
      turn record (`core/state.py` `_PRE_DOMAIN` / the `criterion` field on
      `mark_pre_domain`), and the gate module owns no store of its own;
  (c) a turn whose criterion cannot be extracted degrades to abstain-like
      behaviour — `criterion_of(row) is None`, which means "grade nothing" — and
      never to a silent empty that would read as "nothing was required";
  (d) zero new per-lane branches: the criterion is part of the `pre_question`
      output as DATA, one call, no `if lane ==` anywhere on the path;
  (e) the criterion is readable at POST without re-running PRE (extraction is
      counted; the read must not add a call).

The aux lane is stubbed, so a failure here is wiring, not a provider hiccup.
The criterion extractor is deterministic and free — it never touches the aux
lane, which leg (e) also proves by stubbing aux to None and still reading a
criterion.
"""
from __future__ import annotations

import inspect
import re
import time

import pytest

import hermes_router as plugin  # noqa: F401 — plugin init, as the gate seam expects
from hermes_router import route_gate, state
from hermes_router.features.detection import domain_gate as dg
from hermes_router.gate import orchestration

SID = "s-m41-03"

# A fixture batch, not a taxonomy: asks whose deliverable shape is answerable
# from the wording alone. Deliberately mixed so "present on 90%" cannot be met
# by one regex firing on everything.
FIXTURE_ASKS = [
    "Write a python script that parses a csv and prints the row count.",
    "Give me a javascript function that debounces an event handler.",
    "Ship a Go implementation of a bounded worker pool.",
    "Show me a bash script that rotates log files older than seven days.",
    "Write a rust function that formats a duration as hh:mm:ss.",
    "Give me a SQL query that finds duplicate rows by email.",
    "Write the python class that wraps the retry logic with backoff.",
    "Give me a powershell snippet that lists installed services.",
    "Implement a regex that matches ISO-8601 timestamps.",
    "Write a typescript helper that narrows an unknown to a string array.",
    "List 5 items that belong in a pre-flight checklist for a release.",
    "Give me 3 examples of idempotent migrations.",
    "List 12 steps for rotating a signing key without downtime.",
    "Name 4 ways a ledger row can be lost and how each is detected.",
    "Give me 7 points on why paired measurements beat unpaired ones.",
    "List 2 rows that must be added to the rule table.",
    "Give me the commands to run the suite and read the failure list.",
    "Show the shell commands for rebuilding the container image.",
    "What commands do I run to check the gateway service state?",
    "Give me the terminal commands to reproduce the crash.",
    "Write the file /opt/data/tmp/r78u/M41-03_CLOSE.md with the evidence.",
    "Create a file config/gate.yaml holding the taxonomy.",
    "Save it to /opt/data/projects/notes/criterion.md as a writeup.",
    "Generate a file at scripts/preflight.py that checks HEAD.",
    "Explain how the conservation rule applies to a two-line change.",
    "What does the lane contract mean by gate_semantics being data?",
    "Describe the difference between abstain and not_flagged in this gate.",
    "Argue for or against ordering rule 6 last in the table.",
    "Summarise what the paired measurement actually falsified.",
    "Walk me through how the turn record is bound to an ask.",
]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _reset():
    state.clear_pre_domain()
    state.reset_turn_identity(SID)
    route_gate._AUX_CONSULT_LAST.clear()
    yield
    state.clear_pre_domain()
    route_gate._AUX_CONSULT_LAST.clear()


def _stub_aux(monkeypatch, answer):
    """Stub the shared aux lane at the ONE seam the gate calls."""
    import hermes_router.semantic_classifier as sc

    monkeypatch.setattr(sc, "aux_raw_call", lambda prompt, **kw: answer)


def _wait_landed(sid, timeout=8.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        row = state.pre_domain(sid)
        if row and str(row.get("state")) != dg.GATE_PENDING:
            return time.monotonic() - t0
        time.sleep(0.01)
    return time.monotonic() - t0


def _criterion_for(ask):
    return dg.criterion_of({"criterion": dg.extract_criterion(ask)})


# ---------------------------------------------------------------------------
# (a) present and non-empty on >= 90% of eligible turns
# ---------------------------------------------------------------------------
def test_a_criterion_present_and_non_empty_on_90pct_of_eligible_turns():
    eligible = [a for a in FIXTURE_ASKS if str(a).strip()]
    present = [a for a in eligible if _criterion_for(a) is not None]
    kinds = {}
    for a in present:
        k = _criterion_for(a)["kind"]
        kinds[k] = kinds.get(k, 0) + 1
    ratio = len(present) / len(eligible)
    print(f"\n(a) fixture batch: {len(eligible)} eligible asks, "
          f"{len(present)} with a non-empty criterion = {ratio:.1%}; kinds={kinds}")
    assert ratio >= 0.90, f"criterion coverage {ratio:.1%} below the 90% bar"
    # not a constant extractor: the vocabulary must actually discriminate
    assert len(kinds) >= 2, f"one kind only ({kinds}) — extractor is a constant"
    # every criterion in the batch carries the ORIGINAL ask wording
    for a in present:
        crit = _criterion_for(a)
        assert crit["verbatim"] == " ".join(str(a).split())[:800]
        assert crit["kind"] in dg._CRITERION_KINDS


# ---------------------------------------------------------------------------
# (b) no new persistence path
# ---------------------------------------------------------------------------
def test_b_criterion_rides_the_existing_turn_record(monkeypatch):
    _stub_aux(monkeypatch, '{"domain": "benign_task", "confident": true}')
    ask = FIXTURE_ASKS[0]
    dg.start(SID, ask)
    _wait_landed(SID)

    row = state.pre_domain(SID)
    assert isinstance(row, dict), "the PRE record must exist on the existing store"
    crit = dg.criterion_of(row)
    assert crit is not None and crit["kind"] == "code"
    assert crit["verbatim"] == " ".join(ask.split())

    # the record is the EXISTING one: one dict per session in core/state.py's
    # module-level `_PRE_DOMAIN`, written by mark_pre_domain — no second store.
    assert len(state._PRE_DOMAIN) == 1
    assert SID in state._PRE_DOMAIN

    src_lines, start = inspect.getsourcelines(state.mark_pre_domain)
    path = inspect.getsourcefile(state.mark_pre_domain)
    store_at = [start + i for i, ln in enumerate(src_lines)
                if "_PRE_DOMAIN[sid] = rec" in ln]
    crit_at = [start + i for i, ln in enumerate(src_lines)
               if '"criterion": dict(criterion)' in ln]
    assert store_at and crit_at, "criterion is not carried by mark_pre_domain"
    print(f"\n(b) record: {path}:{store_at[0]} (_PRE_DOMAIN[sid]) "
          f"criterion field at {path}:{crit_at[0]}; records for this session = "
          f"{len(state._PRE_DOMAIN)}")

    # the gate module owns no persistence: no db, no file handle, no writer
    gsrc = inspect.getsource(dg)
    for verb in ("sqlite", "json.dump", "write_text", "open("):
        assert verb not in gsrc, f"gate module grew a persistence verb: {verb}"

    # criterion is a DECLARED field of the pre_question output, and every
    # declared field is actually on the record
    assert "criterion" in dg.PRE_QUESTION_OUTPUT_FIELDS
    for field_name in dg.PRE_QUESTION_OUTPUT_FIELDS:
        assert field_name in row, f"declared PRE output field missing: {field_name}"


# ---------------------------------------------------------------------------
# (c) unextractable degrades to abstain-like, never to a silent empty
# ---------------------------------------------------------------------------
def test_c_unextractable_criterion_degrades_to_abstain_like(monkeypatch):
    class _Unreadable:
        def __str__(self):
            raise RuntimeError("cannot stringify")

    for bad in ("", "   ", "\n\t ", None, _Unreadable()):
        crit = dg.extract_criterion(bad)
        assert crit["available"] is False, f"{bad!r} produced an available criterion"
        assert crit["kind"] == "" and crit["verbatim"] == ""
        assert dg.criterion_of({"criterion": crit}) is None

    # record level: a turn with no criterion reads as "grade nothing", and the
    # POST seam says so explicitly instead of handing back an empty criterion
    state.mark_pre_domain(SID, "", gate=dg.GATE_ABSTAIN, state=dg.GATE_ABSTAIN,
                          criterion={})
    row = dg.read(SID)
    assert row is not None
    assert row["criterion_available"] is False
    assert dg.criterion_of(row) is None
    assert row.get("criterion") == {}
    print("\n(c) absent criterion -> criterion_of=None, criterion_available=False "
          "(same degrade as an abstained gate); empty dict never returned as a "
          "usable criterion")

    # and an empty ask never arms a record at all -> read None -> abstain
    state.clear_pre_domain()
    dg.start(SID, "   ")
    assert dg.read(SID) is None
    assert dg.verdict(None) == dg.GATE_ABSTAIN

    # the POST handler follows the documented degrade: None -> no grading.
    # M41-05 moved the read into `_read_gate_state` (lazy, so rule 1 never reads
    # the gate), so the pin is now "exactly ONE criterion read at POST" rather
    # than a named local variable — the invariant the original line protected.
    psrc = inspect.getsource(orchestration.on_transform_llm_output)
    assert psrc.count("_dgate.criterion_of(") == 1
    assert "criterion_available=0 if _criterion_g is None else 1" in psrc


# ---------------------------------------------------------------------------
# (d) zero new per-lane branches
# ---------------------------------------------------------------------------
_POST_LANE_BRANCH = re.compile(r"lane\s*==|==\s*[\"']shadow[\"']|==\s*LANE_SHADOW")


def test_d_zero_per_lane_branches_on_the_criterion_path():
    psrc = inspect.getsource(orchestration.on_transform_llm_output)
    hits = [ln.strip() for ln in psrc.splitlines() if _POST_LANE_BRANCH.search(ln)]
    gsrc = inspect.getsource(dg)
    ghits = [ln.strip() for ln in gsrc.splitlines() if _POST_LANE_BRANCH.search(ln)]
    print(f"\n(d) POST handler per-lane branch hits={len(hits)} {hits[:3]}; "
          f"gate module hits={len(ghits)} {ghits[:3]}")
    assert not hits, f"per-lane branch in the POST handler: {hits}"
    assert not ghits, f"per-lane branch in the gate module: {ghits}"
    # one call, no per-lane switch: the criterion path is a single accessor
    assert psrc.count("criterion_of") == 1
    assert gsrc.count("def criterion_of") == 1


# ---------------------------------------------------------------------------
# (e) readable at POST without re-running PRE
# ---------------------------------------------------------------------------
def test_e_criterion_readable_at_post_without_re_running_pre(monkeypatch):
    ask = ("Write a python script that reads a jsonl file and counts rows "
           "by profile.")
    calls = []
    original = dg.extract_criterion

    def _counted(a):
        calls.append(1)
        return original(a)

    monkeypatch.setattr(dg, "extract_criterion", _counted)
    # the aux lane answers NOTHING: the gate abstains, but the criterion is
    # extraction (free, deterministic) and must survive anyway
    _stub_aux(monkeypatch, None)
    dg.start(SID, ask)
    _wait_landed(SID)
    pre_calls = len(calls)
    assert pre_calls >= 1, "PRE never extracted a criterion"

    row = dg.read(SID)
    assert row is not None
    # M36-33 note: this ask ("write a python script ...") carries no act cue, so
    # the aux-dead path is unchanged and still abstains — the criterion must
    # survive that anyway, which is this test's subject.
    assert dg.verdict(row) == dg.GATE_ABSTAIN, "aux was None; gate must abstain"
    assert row["criterion_available"] is True
    crit = dg.criterion_of(row)
    assert crit["kind"] == "code"
    assert crit["verbatim"] == " ".join(ask.split())
    after = len(calls)
    print(f"\n(e) PRE extraction calls={pre_calls}, after POST read={after} "
          f"(delta={after - pre_calls}); gate={dg.verdict(row)} "
          f"criterion={crit['kind']} verbatim_chars={len(crit['verbatim'])}")
    assert after == pre_calls, "POST re-ran criterion extraction instead of reading"

    # the ORIGINAL wording is what rides the record — no clarification text,
    # which was measured and lost (paired 197 rows: 0.848 vs 0.802)
    assert "by reference" not in inspect.getsource(dg).lower()
    assert crit["verbatim"] in " ".join(ask.split())
