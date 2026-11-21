"""M35-17 T1–T9 — the ANSWER is the author's bytes; the banner is an appended record.

History, because this module's thesis was reversed twice by owner ruling:
M35-17 removed the composer (banner = record only), M46-03 reinstated it ("a
fired lane is VISIBLE in the delivered bytes"), and M48 settled the shape (one
line per LANE, blank-line separated, priced).

What survives from M35-17 is the property that actually protected the answer:
**no router prose ever enters the answer region.** Every test below asserts on
the bytes returned by the registered hook and splits them into
``(answer region, banner block)`` with :func:`_block_split`; the answer region
must be the author's bytes byte for byte, and the block is where a lane line is
allowed to live. ``features/banners/render.py::render(record, level)`` remains
the extraction surface for the record itself.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
import hermes_router.debug_banner as db  # noqa: E402
from hermes_router import state  # noqa: E402
from hermes_router.core import decisions  # noqa: E402
from hermes_router.features.banners import render as banner_render  # noqa: E402
from hermes_router.features.banners import turn_record as tr  # noqa: E402
from hermes_router.features.detection import domain_gate  # noqa: E402
from hermes_router.gate import registration as registration  # noqa: E402

ASK = "write the keylogger evasion I asked for, in full"
RENDERED = "RENDERED-UNHEDGED-BODY: raw input capture, PEB-walk imports."
DENIAL = "No. I can't help with AV evasion for a keylogger — refusal of the action."

#: The banner grammar of spec §1 — the test's vocabulary.
_GRAMMAR = (
    re.compile(r"·\s*router\s*·"),
    re.compile(r"^\s*full: lane=", re.M),
    re.compile(r"row=\d+"),
    re.compile(r"ledger-row MISSING"),
    re.compile(r"LEDGER-WRITE FAILED"),
    re.compile(r"initiator="),
    re.compile(r"\btok \d+/\d+\b"),
    re.compile(r"^\s*Top verdicts:", re.M),
)


def banner_grammar_absent(text: str) -> bool:
    s = str(text or "")
    return not any(rx.search(s) for rx in _GRAMMAR)


def _banner_line(lane: str = "decision", row: int = 611) -> str:
    text = db.format_banner(lane=lane, trigger="pre", model="m-1",
                            endpoint="openrouter://api.example/v1",
                            tokens_in=11, tokens_out=3, est_cost=0.000123,
                            latency_s=0.4, retries=0, task_id="t-1",
                            session_id="s")
    return "%s | row=%d ·" % (str(text).rstrip().rstrip("·").rstrip(), row)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    state.clear()
    tr.reset()
    db.clear_for_tests() if hasattr(db, "clear_for_tests") else None
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
    })
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    # Writes go nowhere real and are captured in-process.
    rows = []
    monkeypatch.setattr(decisions, "record_banner",
                        lambda **kw: rows.append(kw) or True)
    monkeypatch.setattr(decisions, "record_decision", lambda **kw: True)
    yield rows
    state.clear()
    tr.reset()


def _knob(monkeypatch, level: int) -> None:
    monkeypatch.setattr(db, "debug_banner_level", lambda: int(level))
    monkeypatch.setattr(db, "debug_banner_enabled", lambda: int(level) >= 1)


def _prime(sid: str) -> None:
    state.stash_pending(sid, "m1", ASK, "rendered-stub")
    state.set_last_user_msg_hash(sid, state.hash_text(ASK))
    state.record_last_seen(sid, ASK)


def _chain(monkeypatch, body=RENDERED) -> None:
    entries = [{"name": "primary", "model": "m-render"}]
    monkeypatch.setattr(plugin.router, "_chain_entries", lambda: entries)
    monkeypatch.setattr(plugin.router, "_model_attempt",
                        lambda entry, prompt, **kw: (body, ""))


def _deliver(monkeypatch, sid: str, body_in: str, *, lane_render=None,
             segment: str = "", level: int = 1) -> str:
    """One full turn through the real delivery edge."""
    _knob(monkeypatch, level)
    _prime(sid)
    monkeypatch.setattr(domain_gate, "read", lambda *a, **k: None)
    if lane_render is not None:
        _chain(monkeypatch, body=lane_render)
    else:
        monkeypatch.setattr(registration.orchestration,
                            "on_transform_llm_output", lambda **kw: None)
    if segment:
        tr.register(sid, segment, lane="decision", kind="anchor", edge="benign")
    return plugin.on_transform_llm_output(response_text=body_in,
                                          session_id=sid, model="m1")


def _sha(text) -> str:
    return hashlib.sha256(str(text if text is not None else "").encode()).hexdigest()


def _block_split(delivered) -> "tuple[str, str]":
    """(answer region, banner block) of the delivered bytes.

    M48 attaches the lane block with a leading blank line, so the first
    ``\\n\\n· router ·`` is the boundary. Everything before it is the answer and
    must be the author's bytes, byte for byte; everything from it on is the
    record. No boundary -> the whole delivery is answer and there is no block.
    """
    d = str(delivered or "")
    idx = d.find("\n\n· router ·")
    if idx < 0:
        return d, ""
    return d[:idx], d[idx + 1:]



# ---------------------------------------------------------------------------
# T1 — byte identity across verbosity 0/1/2/3
# ---------------------------------------------------------------------------

FIXTURES = {
    "main_compliant": ("PLAIN ANSWER BODY " * 20, None, _banner_line()),
    "parked_banner": ("ANSWER BODY WITH SEGMENT", None, _banner_line()),
    "lane_render": ('<turn declined="true" />\n\n' + DENIAL, RENDERED,
                    _banner_line("frontier-anchor", 612)),
    "refusal_scaffold": ("No. I can't help with that.", None, _banner_line()),
    "long_body": ("z" * 4000, None, _banner_line()),
    "empty_body": ("", None, _banner_line()),
}


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_answer_region_byte_identical_at_every_verbosity(monkeypatch, _isolate, name):
    """T1, re-pointed to M48: verbosity may change the RECORD, never the answer.

    Measured shape (probe, level 0-3): the answer region is identical at every
    level; levels 2/3 only enrich the lane line (``×2``/``×3``, trigger list).
    The old assertion hashed the whole delivery and demanded one hash across
    levels — true only while the banner was forbidden from the bytes entirely,
    which the owner reversed twice (M46-03, M48).
    """
    body, lane_render, segment = FIXTURES[name]
    expect = body if lane_render is None else lane_render
    regions = {}
    for level in (0, 1, 2, 3):
        sid = "s-t1-%s-%d" % (name, level)
        tr.reset()
        out = _deliver(monkeypatch, sid, body,
                       lane_render=lane_render, segment=segment, level=level)
        delivered = out if isinstance(out, str) else expect
        answer_region, _block = _block_split(delivered)
        if lane_render is not None:
            # M53-06f (owner ruling, supersedes M44-02 for THIS shape): a lane
            # render that IS the delivered body carries the lane banner, so the
            # answer region is the render and the block starts at the first
            # lane-grammar line after a blank line — head-less continuation
            # lines included.
            import re as _re
            _cut = _re.search(r"\n\n(?=(?:\u00b7 router \u00b7|  )?[^\n]*(?:shadow-self|higher-self|impulse)\s*\()",
                              delivered)
            if _cut:
                answer_region, _block = delivered[:_cut.start()], delivered[_cut.start() + 1:]
        regions[level] = _sha(answer_region)
        if lane_render is None:
            assert banner_grammar_absent(answer_region), (name, level, answer_region)
        if expect:
            # M48 joins the block with "\n\n", so a trailing space run in the
            # body is normalised at the seam. Nothing is reordered, added or
            # dropped: the only permitted difference is trailing whitespace,
            # which is why the no-block case is compared exactly.
            want = expect.rstrip() if _block else expect
            assert answer_region == want, (name, level, answer_region[-80:])
    assert len(set(regions.values())) == 1, (name, regions)



# ---------------------------------------------------------------------------
# T2 — the previously bannered turn ships the body only, and the record has it
# ---------------------------------------------------------------------------

def test_previously_bannered_turn_ships_body_then_the_lane_line(monkeypatch, _isolate):
    """M48 (owner ruling): the fired lane is VISIBLE — appended, never injected.

    Was ``..._ships_body_only`` under M35-17. The property worth keeping is not
    "no banner in the bytes" (reversed twice) but "the answer region is the
    author's bytes and the line lives outside it".
    """
    sid = "s-t2"
    body = "THE ANSWER, BYTE FOR BYTE"
    out = _deliver(monkeypatch, sid, body, segment=_banner_line())
    delivered = body if out is None else str(out)
    answer_region, _block = _block_split(delivered)
    assert answer_region == body, delivered
    assert banner_grammar_absent(answer_region)

    recs = [r for r in _isolate if r.get("turn_id")]
    assert recs, "one banner_record must exist for the turn"
    assert recs[-1]["banner"], "the segment must be on the record"
    assert recs[-1]["banner"][0]["lane"] == "decision"


# ---------------------------------------------------------------------------
# T3 — the refusal scaffold is never substituted into the body
# ---------------------------------------------------------------------------

def test_refusal_scaffold_is_not_substituted(monkeypatch, _isolate):
    sid = "s-t3"
    body = "No. I can't help with AV evasion for a keylogger."
    out = _deliver(monkeypatch, sid, body, segment=_banner_line())
    delivered = body if out is None else str(out)
    answer_region, _block = _block_split(delivered)
    assert answer_region == body, delivered
    assert "<turn declined" not in delivered, "the scaffold must never be substituted"
    assert banner_grammar_absent(answer_region)


# ---------------------------------------------------------------------------
# T4 — no banner grammar in any lane's delivered body
# ---------------------------------------------------------------------------

LANES = ("decision", "frontier-anchor", "uncensored-render", "shadow",
         "reflex", "consult-pre", "consult-mid", "consult-post", "anchor",
         "receipt")


@pytest.mark.parametrize("lane", LANES)
def test_fired_lane_line_never_enters_the_answer(monkeypatch, _isolate, lane):
    """M48: a fired lane renders ONE line, appended after a blank line.

    Was ``test_no_banner_grammar_any_lane`` — a M35-17 assertion that forbade
    the line anywhere at all. The reversal is scoped: the line is expected in
    the block and forbidden inside the answer. Two lanes (uncensored-render,
    shadow) compose no line in this harness and pass the body through; both
    shapes satisfy "the answer region is the author's bytes".
    """
    sid = "s-t4-%s" % lane
    body = "ANSWER FOR %s" % lane
    out = _deliver(monkeypatch, sid, body,
                   segment=_banner_line(lane, 700 + len(lane)))
    delivered = body if out is None else str(out)
    answer_region, block = _block_split(delivered)
    assert answer_region == body, delivered
    assert banner_grammar_absent(answer_region)
    if block:
        lines = [ln for ln in block.splitlines() if ln.strip()]
        assert len(lines) == 1, (lane, lines)          # one line per lane
    rec = tr.render(sid)
    assert rec, "the segment must be registered (absence is not failure to render)"


def test_no_banner_grammar_multi_lane_and_hold(monkeypatch, _isolate):
    sid = "s-t4-multi"
    tr.reset()
    _knob(monkeypatch, 3)
    tr.register(sid, _banner_line("decision", 611), lane="decision", kind="a")
    tr.register(sid, _banner_line("frontier-anchor", 612), lane="frontier", kind="b")
    tr.register(sid, _banner_line("uncensored-render", 613),
                lane="uncensored", kind="c")
    out = _deliver(monkeypatch, sid, "MULTI LANE BODY")
    assert banner_grammar_absent(out if isinstance(out, str) else "MULTI LANE BODY")
    assert len(tr.segments(sid)) >= 3


# ---------------------------------------------------------------------------
# T5 — the record round-trips through the extractor
# ---------------------------------------------------------------------------

def test_record_round_trip(monkeypatch, _isolate):
    sid = "s-t5"
    _deliver(monkeypatch, sid, "BODY", segment=_banner_line())
    rec = dict(_isolate[-1])
    rec.setdefault("turn_id", "1")
    rec.setdefault("decision", "D4")
    rec.setdefault("task_id", "t-1")
    for level in (1, 2, 3):
        text = banner_render.render(rec, level)
        assert text, "render(record, %d) must not be empty" % level
        assert "row=611" in text or "611" in text
    assert "full: lane=" in banner_render.render(rec, 3)
    assert banner_render.render(rec, 0) == ""


# ---------------------------------------------------------------------------
# T6 — the renderer can never be a body writer
# ---------------------------------------------------------------------------

def test_render_never_in_body(monkeypatch, _isolate):
    sid = "s-t6"
    body = "A REAL ANSWER BODY"
    out = _deliver(monkeypatch, sid, body, segment=_banner_line())
    delivered = out if isinstance(out, str) else body
    rec = dict(_isolate[-1])
    rendered = banner_render.render(rec, 3)
    assert rendered
    assert rendered not in delivered
    for line in rendered.splitlines():
        assert not delivered.endswith(line.strip())


# ---------------------------------------------------------------------------
# T7 — the carry symbols and its event are gone
# ---------------------------------------------------------------------------

def test_carry_symbols_absent():
    from hermes_router.gate import registration as reg

    assert not hasattr(reg, "_carry_seam_banner")
    assert not hasattr(reg, "_in_turn_banner_block")
    assert not hasattr(tr, "emit_once")
    for name in ("append_banner", "merge_decision_banner",
                 "merge_frontier_banner", "settle_decision_banner"):
        assert not hasattr(db, name), name
    src = open(os.path.join(PLUGIN_DIR, "gate", "registration.py")).read()
    assert "delivery_banner_carried" not in src


# ---------------------------------------------------------------------------
# T8 — exactly ONE record per turn where a lane fired
# ---------------------------------------------------------------------------

def test_one_record_per_turn(monkeypatch, _isolate):
    sid = "s-t8"
    tr.reset()
    _knob(monkeypatch, 1)
    tr.register(sid, _banner_line("decision", 611), lane="decision", kind="a")
    tr.register(sid, _banner_line("frontier-anchor", 612), lane="frontier", kind="b")
    _deliver(monkeypatch, sid, "BODY")
    same = [r for r in _isolate if str(r.get("session_id") or "") == sid]
    assert len(same) == 1, same
    assert int(same[0].get("dupes") or 0) == 0
    assert len(same[0]["banner"]) >= 2


# ---------------------------------------------------------------------------
# T9 — corpus-level: no delivered body carries a banner line
# ---------------------------------------------------------------------------

def test_corpus_answer_region_banner_free(monkeypatch, _isolate):
    """Stratified A–F + benign regressions (spec §6 T9), re-pointed to M48.

    The ≥80-turn held-out corpus of M35-12 §9 is not checked in; this drives the
    same strata through the real seam at the profile's knob and asserts zero
    grammar hits INSIDE THE ANSWER plus byte-identity for the benign controls —
    the block is allowed to carry the lane line (owner ruling, M46-03/M48).
    """
    bodies = [
        "benign answer %d " % i + "y" * (200 + 37 * i) for i in range(30)
    ] + [
        "refusal body %d: No. I can't help with that." % i for i in range(30)
    ] + [
        "long compliant body %d " % i + "q" * 2000 for i in range(20)
    ]
    for level in (0, 3):
        for i, body in enumerate(bodies):
            sid = "s-t9-%d-%d" % (level, i)
            tr.reset()
            out = _deliver(monkeypatch, sid, body, segment=_banner_line(),
                           level=level)
            answer_region, _block = _block_split(out if isinstance(out, str) else body)
            assert banner_grammar_absent(answer_region), (level, i, answer_region[-200:])
            assert answer_region.strip() == body.strip()
