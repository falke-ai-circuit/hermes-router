"""R37-1 (5.13.0) — the POST frontier is genuinely SYNC: revision folded in.

Measured defect this closes: 246 frontier consults parsed, 92% grounded, 89.3% of
sampled advisories DIVERGENT (they name an action the agent did not take) — the
advice is not redundant. But only 20.9% of divergent advisories were ever acted
on, because `audit_gate` made a SECOND frontier call (`revise_with_verdict`)
after the consult had already spent the hook budget: the revision pass was
handed ~0.05s, `completion_audit_revision_skipped reason=budget` fired, and
`revised=False` was recorded on 64 of 64 events fleet-wide. The pass had never
once changed a delivered body.

The fix is one call that returns both halves:

    {"note": <verdict as today>, "revised_response": <full corrected response> | null}

Pins here (named for grep):
  * test_r37_1_folded_response_is_delivered
  * test_r37_1_null_revision_delivers_original_and_stashes
  * test_r37_1_malformed_json_delivers_original_without_raising
  * test_r37_1_banner_appended_once_and_absent_from_reconciled_body
  * test_r37_1_generation_time_cap_and_no_posthoc_truncation
"""
import itertools
import pathlib

from hermes_router import completion_audit as ca
from hermes_router import router_core
from hermes_router.core import canonical

SRC = pathlib.Path(__file__).resolve().parents[2]

_SIDS = itertools.count()
ASK = "close this out and tell me what changed"
ORIGINAL = (
    "The banner was parked at turn one and consumed at a mid-turn edge where the "
    "rewrite could not match the persisted row, so it never attached to the "
    "delivered body. Storage is keyed per session, which is why the drop was "
    "invisible across three profiles: the park turn and the live turn were "
    "compared as if they were the same turn, and a park that outlived its turn "
    "was silently discarded rather than held for the next delivery boundary. The "
    "row that would have carried it was already rewritten once, so the second "
    "exact-match could never land and the banner was consumed into nothing.")
REVISED = ("The banner was parked at turn one (debug_banner.park_anchor_banner) "
           "and consumed at a mid-turn edge where rewrite_persisted_turn could "
           "not match the persisted row, so the segment never attached to the "
           "delivered body. Retention is keyed on the park turn, not the live "
           "turn, which is why the drop was invisible for three profiles.")


def _sid():
    return "sess-r37-1-%d" % next(_SIDS)


def _wire(monkeypatch, revised, note="- The row was never checked before "
                                     "claiming delivery."):
    """Drive the gate with a folded consult result. No second call is wired."""
    seen = {"events": [], "rewrites": [], "banners": []}
    monkeypatch.setattr(ca, "_consult_meta", lambda *a, **k: {
        "note": note, "model": "m", "endpoint": "e",
        "revised_response": revised,
        "revised_source": ("folded" if revised else "none"),
        "revised_reason": ("folded" if revised else "needs_tool_work"),
        "tokens_in": 1, "tokens_out": 1, "cost": 0.0})
    monkeypatch.setattr(ca, "_log",
                        lambda ev, **kw: seen["events"].append((ev, kw)))
    monkeypatch.setattr(ca, "audit_topology", lambda: "sync")
    monkeypatch.setattr(ca, "audit_sync_seconds", lambda: 45)
    monkeypatch.setattr(ca, "audit_enabled", lambda: True)
    monkeypatch.setattr(ca, "audit_mode", lambda: "always")
    monkeypatch.setattr(ca, "stash_verdict",
                        lambda sid, v: seen.setdefault("stashed", []).append(v))
    monkeypatch.setattr(router_core, "post_audit_min_turns", lambda: 1)

    # the persisted row is reconciled over the ENTRY text, like the strip branch
    def _rewrite(sid, refusal_text, delivered_text, **kw):
        seen["rewrites"].append((sid, refusal_text, delivered_text))
        return True

    monkeypatch.setattr(canonical, "rewrite_persisted_turn", _rewrite)

    from hermes_router import debug_banner as db

    def _append(text, banner, **kw):
        seen["banners"].append(banner)
        return str(text) + str(banner)

    monkeypatch.setattr(db, "debug_banner_enabled", lambda: True)
    monkeypatch.setattr(db, "format_banner", lambda **k: "BANNER-R37")
    monkeypatch.setattr(db, "append_banner", _append)
    return seen


def _applied(seen):
    return [kw for ev, kw in seen["events"]
            if ev == "completion_audit_sync_applied"]


# --------------------------------------------------------------------------
# 1. folded -> delivered
# --------------------------------------------------------------------------

def test_r37_1_folded_response_is_delivered(monkeypatch):
    sid = _sid()
    seen = _wire(monkeypatch, REVISED)
    out = ca.audit_gate(sid, ORIGINAL, ask_override=ASK)

    assert out is not None and REVISED in out, out
    assert ORIGINAL not in out, "the pre-correction body must not be delivered"
    assert _applied(seen)[-1].get("revised") is True
    assert _applied(seen)[-1].get("revised_source") == "folded"
    assert not seen.get("stashed"), "an applied revision does not ride next turn"


# --------------------------------------------------------------------------
# 2. null -> original delivered + verdict stashed
# --------------------------------------------------------------------------

def test_r37_1_null_revision_delivers_original_and_stashes(monkeypatch):
    sid = _sid()
    seen = _wire(monkeypatch, None)
    out = ca.audit_gate(sid, ORIGINAL, ask_override=ASK)

    assert out.startswith(ORIGINAL), out
    assert REVISED not in out
    assert seen.get("stashed"), "the verdict must ride to the next turn"
    assert _applied(seen)[-1].get("revised_source") == "none"
    assert _applied(seen)[-1].get("reason") == "needs_tool_work"
    assert not seen["rewrites"], "no body swap happened, so no reconcile"


# --------------------------------------------------------------------------
# 3. malformed / absent JSON -> original delivered, nothing raises
# --------------------------------------------------------------------------

def test_r37_1_malformed_json_delivers_original_without_raising(monkeypatch):
    # parse level: prose, truncated JSON, fenced garbage, a bare list
    for bad in ('{"note": "unterminated',
                "just some prose advice",
                "```json\n{oops}\n```",
                "[1, 2, 3]",
                "",
                '{"revised_response": 42}'):
        note, rev, reason = ca._parse_folded_verdict(bad, ORIGINAL, 8000)
        assert rev is None, bad
        assert reason, bad
        assert isinstance(note, str)

    # gate level: an unstructured verdict and no revision still delivers the
    # turn and logs the reason
    sid = _sid()
    seen = _wire(monkeypatch, None, note='{"note": "trunc')
    out = ca.audit_gate(sid, ORIGINAL, ask_override=ASK)
    assert out.startswith(ORIGINAL)
    assert _applied(seen)[-1].get("revised") is False


def test_r37_1_a_revision_that_fails_its_checks_is_dropped_whole():
    """Fail-open at parse: an echo shell, an audit artifact, or an over-budget
    body is dropped ENTIRELY — never delivered half-applied, never truncated."""
    cap = ca._fold_char_cap(ORIGINAL)
    envelope = '{"note": "- x", "revised_response": "ok"}'      # echo shell
    _, rev, reason = ca._parse_folded_verdict(envelope, ORIGINAL, cap)
    assert rev is None and reason == "too_short"

    big = '{"note": "- x", "revised_response": "%s"}' % ("z" * (cap + 50))
    _, rev, reason = ca._parse_folded_verdict(big, ORIGINAL, cap)
    assert rev is None and reason == "over_char_budget"

    import json as _json

    marked = _json.dumps({"note": "- x",
                          "revised_response": ca._NOTE_MARKER + "q" * 400})
    _, rev, reason = ca._parse_folded_verdict(marked, ORIGINAL, 8000)
    assert rev is None and reason == "carries_note_marker", reason

    # false-positive guard: an answer that legitimately talks about this
    # plugin's own banner code is an ANSWER, not an audit artifact
    prose = _json.dumps({"note": "- x", "revised_response":
                         "debug_banner.park_anchor_banner parks it. " + "u" * 400})
    _, rev, reason = ca._parse_folded_verdict(prose, ORIGINAL, 8000)
    assert reason == "folded", reason


def test_r37_1_fenced_and_chatty_envelopes_still_parse():
    """A model that fences its JSON or precedes it with a sentence is still
    usable — the parse is tolerant, the contract is not."""
    import json

    body = REVISED
    for raw in ('```json\n%s\n```' % json.dumps({"note": "- n", "revised_response": body}),
                'Here is my read.\n%s' % json.dumps({"note": "- n", "revised_response": body})):
        note, rev, reason = ca._parse_folded_verdict(raw, ORIGINAL, 8000)
        assert rev == body and reason == "folded", (reason, note)


# --------------------------------------------------------------------------
# 4. banner: exactly once, after the swap, never inside the reconciled body
# --------------------------------------------------------------------------

def test_r37_1_banner_appended_once_and_absent_from_reconciled_body(monkeypatch):
    sid = _sid()
    seen = _wire(monkeypatch, REVISED)
    out = ca.audit_gate(sid, ORIGINAL, ask_override=ASK)

    assert out.count("BANNER-R37") == 1, "the banner must be appended exactly once"
    assert len(seen["banners"]) == 1

    # persisted == delivered: the reconcile ran on the SWAPPED body, matched
    # against the text the runner persisted on entry
    assert len(seen["rewrites"]) == 1, seen["rewrites"]
    _sid_, entry, body = seen["rewrites"][0]
    assert entry == ORIGINAL, "the row is found by the ENTRY text"
    assert body == REVISED, "the swapped body is what is reconciled"
    assert "BANNER-R37" not in body, "the banner is never part of the body"
    assert ca._NOTE_MARKER not in body
    # the banner comes AFTER the swap in the delivered string
    assert out.index(REVISED) < out.index("BANNER-R37")


# --------------------------------------------------------------------------
# 5. generation-time cap; no post-hoc truncation of a delivered answer
# --------------------------------------------------------------------------

def test_r37_1_generation_time_cap_and_no_posthoc_truncation(monkeypatch):
    from hermes_router import anchor_exec

    monkeypatch.setattr(anchor_exec, "advisory_char_budget", lambda: 1800)
    # advisory_char_budget() is the FLOOR: a draft that fits it caps at it ...
    assert ca._fold_char_cap("x" * 100) == 1800
    # ... a longer draft keeps its own allowance (capping it would force the
    # model to COMPRESS the answer — post-hoc truncation moved into the prompt)
    assert ca._fold_char_cap("x" * 5000) == 5600
    # the folded half buys a real, non-zero token allowance
    assert ca._fold_revision_tokens(1800) > 0
    assert ca._fold_revision_tokens(5600) > ca._fold_revision_tokens(1800)

    # the cap is STATED in the ask it bounds
    folded = ca._audit_payload(ASK, "", ORIGINAL, 2400,
                               fold_revision=True, fold_char_cap=1234)
    assert "at most 1234 characters" in folded[1]["content"]
    assert "OUTPUT FORMAT" in folded[1]["content"]
    # the async/legacy ask is untouched: no fold contract, no second half
    plain = ca._audit_payload(ASK, "", ORIGINAL, 2400)
    assert "OUTPUT FORMAT" not in plain[1]["content"]
    assert "revised_response" not in plain[1]["content"]

    # a revised body LONGER than the advisory budget is delivered IN FULL: this
    # path must never cut a delivered answer to fit a cap
    long_revised = "R" * 3000
    sid = _sid()
    seen = _wire(monkeypatch, long_revised)
    out = ca.audit_gate(sid, ORIGINAL, ask_override=ASK)
    assert long_revised in out, "delivered answer was truncated"
    assert len(out) > 3000
    assert _applied(seen)[-1].get("revised") is True


def test_r37_1_no_delivery_path_uses_a_second_frontier_call():
    """Structural pin: the delivery path may not depend on a second in-hook
    frontier call, and may not truncate a delivered body after the fact."""
    src = (SRC / "completion_audit.py").read_text()
    gate = src[src.index("def audit_gate"):src.index("def _sense_check_verdict_json")]
    assert "revise_with_verdict(" not in gate, \
        "the delivery path still calls the second frontier pass"
    assert "_delivered = _delivered[" not in gate
    assert "_delivered)" in gate and "rewrite_persisted_turn" in gate
    # revise_with_verdict stays importable for the older pins / manual probes
    assert callable(ca.revise_with_verdict)
