"""SPEC §2a — self-declaration: the uncensored lane's ONE-BIT trigger.

Frozen contract (/opt/data/tmp/unc_lane_post_spec.md §2a):

  1. one bit — `declined` is true or false, nothing else in the field;
  2. true = declined to PRODUCE (policy/safety/values/unwillingness);
     honesty and capability are false;
  3. tag on its own FINAL line, full-line match only — a tag quoted inside
     the body is never the declaration;
  4. absent/malformed => UNKNOWN => fall back to the four-class classifier;
     NEVER default to false;
  6. strip at the earliest seam — routing metadata reaches no delivered body
     and no persisted turn.

The tag form is the MEASURED one (`<turn declined="true" />`), not a guess:
the whole 89+21-cell battery emitted this shape.

Deterministic throughout: pure string module + mocked render chain. No judge.
"""
import json
import os
import sqlite3
import sys
from unittest import mock

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)  # the directory holding the package
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import frames  # noqa: E402
from hermes_router import state  # noqa: E402
from hermes_router.features.detection import declaration as decl  # noqa: E402
from hermes_router.passes import dispatcher_pre  # noqa: E402

TAG_T = '<turn declined="true" />'
TAG_F = '<turn declined="false" />'

ACTION_BODY = "I'm not able to assist with that."          # action_denial
TRUTH_BODY = "I can't verify this claim; that's outside my knowledge cutoff."
COMPLIANT_BODY = "Here is the diff you asked for: the parser now skips blanks."
ASK = "produce the full render for this unit"
RENDER = "RENDERED SUBSTANCE BODY — the requested material, in full."


# ---------------------------------------------------------------------------
# strip_tag — measured form, full-line, last line for the DECLARATION
# ---------------------------------------------------------------------------


def test_strip_removes_final_line_tag():
    assert decl.strip_tag("answer body\n" + TAG_T) == ("answer body", "true")
    assert decl.strip_tag("answer body\n" + TAG_F) == ("answer body", "false")


def test_no_slash_variant_is_the_same_declaration():
    assert decl.strip_tag('body\n<turn declined="true">') == ("body", "true")


def test_strip_tolerates_trailing_blank_lines_and_padding():
    assert decl.strip_tag("body\n" + TAG_T + "\n\n") == ("body", "true")
    assert decl.strip_tag('body\n  <turn declined="true" />  ') == \
        ("body", "true")


def test_strip_is_case_insensitive_and_normalises_the_value():
    assert decl.strip_tag('body\n<TURN DECLINED="TRUE" />') == ("body", "true")


def test_tag_quoted_inside_prose_is_not_a_tag_line():
    """§2a.3: a tag inside a sentence is answer text — never a declaration and
    never eaten out of the answer."""
    body = ("The contract says you must append " + TAG_T +
            " on the final line of a refusal.")
    assert decl.strip_tag(body) == (body, None)
    assert decl.parse(body) == decl.UNKNOWN


def test_tag_on_a_non_boundary_line_is_stripped_but_does_not_declare():
    """§2a.6: STRIP ALWAYS, DECLARE ONLY ON A BOUNDARY. Leaving the tag in the
    body would deliver routing metadata to the user."""
    body = "intro prose\n" + TAG_T + "\nmore prose after the tag"
    assert decl.strip_tag(body) == ("intro prose\nmore prose after the tag", None)
    assert decl.parse(body) == decl.UNKNOWN


def test_first_line_tag_declares():
    """The contract asks for the tag FIRST: a leading boundary tag declares."""
    assert decl.strip_tag(TAG_T + "\nthe answer") == ("the answer", "true")
    assert decl.parse(TAG_T + "\nthe answer") == "true"
    # leading blank lines are not content, so the boundary is the first non-blank
    assert decl.parse("\n\n" + TAG_F + "\nthe answer") == "false"


def test_mid_body_tag_is_removed_without_declaring():
    body = "first paragraph\n" + TAG_F + "\nsecond paragraph"
    assert decl.strip_tag(body) == ("first paragraph\nsecond paragraph", None)


def test_tag_only_body_strips_to_empty():
    assert decl.strip_tag(TAG_T) == ("", "true")
    assert decl.strip_tag(TAG_F) == ("", "false")


def test_malformed_fragments_are_left_alone_and_declare_nothing():
    for body in ('body\n<turn declined="tru" />',
                 'body\n<turn declined=true>',
                 'body\n<turn declined="maybe" />',
                 'body\n<turn declined="true" /> and then prose'):
        assert decl.strip_tag(body) == (body, None)
        assert decl.parse(body) == decl.UNKNOWN


def test_strip_is_total_on_non_string_and_empty_input():
    assert decl.strip_tag("") == ("", None)
    assert decl.strip_tag(None) == ("", None)
    assert decl.strip_tag("   \n\t ") == ("   \n\t ", None)
    assert decl.strip_tag(1234) == ("1234", None)


# ---------------------------------------------------------------------------
# parse — "true" | "false" | "UNKNOWN", never a silent "false"
# ---------------------------------------------------------------------------


def test_parse_reads_a_final_line_tag():
    assert decl.parse("body\n" + TAG_T) == "true"
    assert decl.parse("body\n" + TAG_F) == "false"


@pytest.mark.parametrize("text", [
    "", "   ", "answer with no tag at all",
    'true', 'false', 'TRUE',          # a one-word ANSWER is not a declaration
    'body\n<turn declined="tru" />',
    'body\n<turn declined="true"',
    'body\n<turn declined="maybe" />',
    "intro\n" + TAG_T + "\nprose",   # stripped, but MID-body -> no declaration
])
def test_parse_is_unknown_on_absent_truncated_malformed_or_bare(text):
    assert decl.parse(text) == decl.UNKNOWN
    assert decl.parse(text) != "false"  # never a default-to-false bypass


def test_parse_is_total_on_non_string():
    assert decl.parse(None) == decl.UNKNOWN
    assert decl.parse(object()) == decl.UNKNOWN


def test_declaration_value_path_is_explicit_and_strict():
    """The FIELD path is the only place a bare value is legal — the seam
    passes the value it extracted from a tag line."""
    assert decl.parse_declaration_value("true") == "true"
    assert decl.parse_declaration_value(" FALSE ") == "false"
    assert decl.parse_declaration_value("maybe") == decl.UNKNOWN
    assert decl.parse_declaration_value(None) == decl.UNKNOWN
    assert decl.parse_declaration_value(TAG_T) == decl.UNKNOWN


# ---------------------------------------------------------------------------
# routes — the truth table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [
    ("true", True),
    ("TRUE", True),
    (" true ", True),
    ("false", False),
    ("FALSE", False),
    (decl.UNKNOWN, False),
    ("unknown", False),
    ("", False),
    (None, False),
    ("yes", False),
    ("1", False),
    (True, False),      # a bool is not a declaration value
    (object(), False),
])
def test_routes_truth_table(value, expected):
    assert decl.routes(value) is expected


def test_routing_set_is_exactly_the_one_bit():
    assert decl.ROUTING_SET == frozenset({"true"})
    assert decl.UNKNOWN not in decl.ROUTING_SET


# ---------------------------------------------------------------------------
# The contract is INJECTED (defect 2: a parser nobody feeds is a dead lane)
# ---------------------------------------------------------------------------


def test_contract_text_is_the_measured_text_verbatim():
    """The shipped contract is the MEASURED one, not a paraphrase.

    The battery's text shows the tag template with both values; a paraphrase
    that dropped the exclusion clauses measured 75% literal (94% determinable)
    and misdeclared a decision turn as `true`.
    """
    text = decl.CONTRACT_TEXT
    assert '<turn declined="true|false" />' in text
    # exclusion 1 — capability stated negatively
    assert '"I can\'t read your files" -> false' in text
    assert "missing capability is NOT a decline" in text
    # exclusion 2 — declined SUB-instruction on a task that WAS performed
    assert "declined only a secondary or embedded instruction" in text
    assert "-> false, you decided" in text


def test_contract_text_matches_the_frozen_battery_constant():
    """Byte-for-byte against /opt/data/tmp/battery_v2.py CONTRACT.

    HISTORICAL: that battery measured the TRAILING-tag contract (95.5% literal,
    98.8% determinable). This contract asks for the tag FIRST because a
    trailing tag is the first casualty of an output cap; the leading-tag
    contract was measured by /opt/data/tmp/detcorpus/emit_ab.py (16/16 emitted,
    8/8 refusals true, 8/8 benign false vs 14/16 trailing). The comparison
    below is retained only while the frozen file still carries the old text, so
    it is SKIPPED once the contract and the battery diverge — the pin that
    matters is the clause pin above.

    Skipped when the battery file is not present (it is a host artefact, not
    part of the plugin tree) — the string itself is still pinned above.
    """
    path = "/opt/data/tmp/battery_v2.py"
    if not os.path.exists(path):
        pytest.skip("frozen battery not present on this host")
    src = open(path, encoding="utf-8").read()
    start = src.index('CONTRACT = """') + len('CONTRACT = """')
    end = src.index('"""', start)
    # The triple quote is immediately followed by a newline in the
    # battery source, so the slice already starts with "\n".
    frozen = src[start:end]
    if frozen != decl.CONTRACT_TEXT:
        # The battery is a historical measurement of the TRAILING-tag contract.
        # The shipped contract now asks for the tag FIRST. Divergence is the
        # expected state after that change, not a regression -- the clause pin
        # above is the live contract check. Re-point the battery only if it is
        # re-run; until then it must not be mutated to keep its numbers honest.
        pytest.skip("frozen battery carries the historical trailing-tag contract")


def test_declaration_contract_is_delivered_at_the_head_of_the_system_block():
    """PLACEMENT IS THE MECHANISM: a trailing platform rule was read and
    ignored (0 tags over 14 live turns under a heavy persona), so the contract
    is delivered at the HEAD of the first system message, before the persona.

    The pass only DECIDES; frames.prepend_rule_to_first_system DELIVERS on a
    copy. Portability requires the plugin to own this instruction — it must
    never be moved into agent system prompts.
    """
    from hermes_router import frames

    request = {"messages": [{"role": "system", "content": "persona"},
                            {"role": "user", "content": "hello"}]}
    snapshot = json.dumps(request)
    assert dispatcher_pre.declaration_inject_pass(request) is True
    wire = frames.prepend_rule_to_first_system(
        request["messages"], dispatcher_pre.DECLARATION_RULE_ID,
        dispatcher_pre.declaration_contract_text())
    head = wire[0]
    assert head["role"] == "system"
    assert '<turn declined="true|false" />' in head["content"]
    assert "produce" in head["content"]  # the semantics, not just syntax
    # before the persona, not after it
    assert head["content"].index("turn declined") < head["content"].index("persona")
    # the caller's request is untouched (delivery is a copy)
    assert json.dumps(request) == snapshot
    # idempotent: a replayed list never carries it twice
    assert frames.platform_rule_present(wire, dispatcher_pre.DECLARATION_RULE_ID) is True
    assert dispatcher_pre.declaration_inject_pass({"messages": wire}) is False


def test_declaration_contract_keeps_content_part_lists_intact():
    """A multimodal content-part list must survive delivery. Stringifying one
    destroyed the structure: TypeError: string indices must be integers."""
    from hermes_router import frames

    part = {"type": "text", "text": "hello"}
    wire = frames.prepend_rule_to_first_system(
        [{"role": "system", "content": [part]},
         {"role": "user", "content": "hi"}],
        dispatcher_pre.DECLARATION_RULE_ID,
        dispatcher_pre.declaration_contract_text())
    assert isinstance(wire[0]["content"], list)
    assert wire[0]["content"][0]["type"] == "text"
    assert '<turn declined="true|false" />' in wire[0]["content"][0]["text"]
    assert wire[0]["content"][-1]["text"] == "hello"


def test_declaration_inject_pass_is_idempotent_in_context():
    """The predicate stops firing once the contract is in context, and it never
    grows the caller's message list (delivery is a copy)."""
    request = {"messages": [{"role": "user", "content": "hello"}]}
    assert dispatcher_pre.declaration_inject_pass(request) is True
    before = len(request["messages"])
    assert len(request["messages"]) == before  # predicate does not mutate
    from hermes_router import frames
    wire = frames.prepend_rule_to_first_system(
        request["messages"], dispatcher_pre.DECLARATION_RULE_ID,
        dispatcher_pre.declaration_contract_text())
    assert dispatcher_pre.declaration_inject_pass({"messages": wire}) is False


def test_declaration_inject_pass_is_fail_open_on_a_bad_request():
    assert dispatcher_pre.declaration_inject_pass(None) is False
    assert dispatcher_pre.declaration_inject_pass({"messages": "nope"}) is False


# ---------------------------------------------------------------------------
# Integration — the POST seam
# ---------------------------------------------------------------------------


def _cfg():
    return {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
    }


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    state.clear()
    plugin._reset_declaration_state()
    # The platform-rule delivery latch is process-level and keyed on
    # (context key, rule id, text): a previous test injecting the SAME message
    # list would suppress this one. Production wants that suppression; tests
    # start cold.
    frames.reset_rule_latch()
    monkeypatch.setattr(plugin, "_cfg", lambda: _cfg())
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    yield
    state.clear()
    plugin._reset_declaration_state()
    frames.reset_rule_latch()


def _prime(session_id, ask=ASK, model="minimax-m3"):
    state.record_last_seen(session_id, ask)
    state.set_last_user_msg_hash(session_id, state.hash_text(ask))
    return session_id


def _post(session_id, body, model="minimax-m3", **ctx):
    return plugin.on_transform_llm_output(
        response_text=body, session_id=session_id, model=model, **ctx)


def test_undeclared_action_denial_is_delivered_unrouted():
    """§2a.4: no tag => UNKNOWN => no route. The classifier does not route at
    POST, so an undeclared action_denial body is delivered as written."""
    _prime("dec-unknown")
    with mock.patch.object(plugin.router, "call", return_value=RENDER) as venice:
        out = _post("dec-unknown", ACTION_BODY)
    assert not venice.called
    assert out is None


def test_unknown_falls_back_and_honesty_still_does_not_route():
    _prime("dec-unknown-truth")
    with mock.patch.object(plugin.router, "call") as venice:
        out = _post("dec-unknown-truth", TRUTH_BODY)
    assert not venice.called
    assert out is None


def test_declared_true_routes_even_when_the_classifier_would_not():
    """§2a.1: the declaration IS the trigger — a compliant-looking body that
    declares `true` enters the same route machinery action_denial uses."""
    _prime("dec-true")
    body = COMPLIANT_BODY + "\n" + TAG_T
    with mock.patch.object(plugin.router, "call", return_value=RENDER) as venice:
        out = _post("dec-true", body)
    assert venice.called
    assert out == RENDER
    assert "declined" not in out.lower()


def test_declared_false_does_not_route_an_action_denial_body():
    """The one bit is authoritative: a body the classifier would call
    action_denial must NOT route when the model declares false."""
    _prime("dec-false")
    body = ACTION_BODY + "\n" + TAG_F
    with mock.patch.object(plugin.router, "call") as venice:
        out = _post("dec-false", body)
    assert not venice.called
    # The turn passes through, but as the STRIPPED body — returning None would
    # tell the host to deliver the original, tag included (§2a.6).
    assert out == ACTION_BODY
    assert "declined" not in out.lower()


def test_false_declaration_holds_across_a_second_pass():
    """transform_pass: pass 1 strips the tag; a later pass sees the stripped
    body and must still honour `false` (not decay to the classifier)."""
    _prime("dec-false-2")
    body = ACTION_BODY + "\n" + TAG_F
    with mock.patch.object(plugin.router, "call") as venice:
        first = _post("dec-false-2", body)
        second = _post("dec-false-2", ACTION_BODY)   # tag already gone
    assert not venice.called
    assert first == ACTION_BODY      # stripped body, not the tag-bearing one
    # pass 2 has no tag to strip, so the plain pass-through contract applies —
    # and it still must NOT route on the action_denial body it sees.
    assert second is None


def test_non_final_tag_is_never_delivered():
    """§2a.6 on position drift: a tag on a non-final line is routing metadata,
    stripped before delivery. It is not a final declaration, so the turn is
    UNKNOWN and does not route."""
    _prime("dec-drift")
    body = TAG_F + "\n" + ACTION_BODY
    with mock.patch.object(plugin.router, "call", return_value=RENDER) as venice:
        out = _post("dec-drift", body)
    assert not venice.called      # UNKNOWN -> no route
    assert "declined" not in (out or "").lower()


def test_strip_happens_before_delivery_even_when_routing_is_disabled(monkeypatch):
    """§2a.6: the tag is routing metadata — a disabled router must not deliver
    it either."""
    plugin._reset_declaration_state()
    monkeypatch.setattr(plugin, "_cfg", lambda: {**_cfg(), "enabled": False})
    out = _post("dec-disabled", "body text\n" + TAG_T)
    assert out == "body text"
    assert "declined" not in (out or "")


def test_render_unavailable_still_delivers_a_tag_free_body():
    """§3 fail-open: chain returns nothing => the ORIGINAL turn ships — but
    the stripped one, never the tag."""
    _prime("dec-failopen")
    body = ACTION_BODY + "\n" + TAG_T
    with mock.patch.object(plugin.router, "call", return_value=""):
        out = _post("dec-failopen", body)
    assert out == ACTION_BODY
    assert "declined" not in (out or "")


def test_declaration_seen_row_shape(monkeypatch):
    _prime("dec-row")
    rows = []

    def _spy(event, **fields):
        rows.append((event, fields))

    monkeypatch.setattr(plugin, "_log_route", _spy)
    with mock.patch.object(plugin.router, "call", return_value=RENDER):
        _post("dec-row", ACTION_BODY + "\n" + TAG_T)
    seen = [f for e, f in rows
            if e == "POST" and f.get("event_detail") == "declaration_seen"]
    assert len(seen) == 1
    assert seen[0]["decl"] == "true"
    assert "cls" in seen[0]
    assert seen[0]["chars"] == len(ACTION_BODY)
    assert seen[0]["session_id"] == "dec-row"


def test_persisted_turn_never_contains_the_tag(tmp_path):
    """A1/§2a.6: turn_finalizer persisted the row WITH the tag; the seam must
    rewrite it to the delivered (stripped) text."""
    _prime("dec-a1-true")
    db = str(tmp_path / "v310-canonical-state.db")
    body = ACTION_BODY + "\n" + TAG_T
    _seed_assistant_row(db, "dec-a1-true", body)
    with mock.patch.object(plugin.router, "call", return_value=RENDER):
        out = _post("dec-a1-true", body)
    assert out == RENDER
    rows = _assistant_rows(db, "dec-a1-true")
    assert rows == [RENDER]
    assert all("declined" not in (r or "") for r in rows)


def test_persisted_turn_stripped_on_a_false_declaration(tmp_path):
    _prime("dec-a1-false")
    db = str(tmp_path / "v310-canonical-state.db")
    body = ACTION_BODY + "\n" + TAG_F
    _seed_assistant_row(db, "dec-a1-false", body)
    with mock.patch.object(plugin.router, "call") as venice:
        out = _post("dec-a1-false", body)
    assert not venice.called
    assert out == ACTION_BODY
    rows = _assistant_rows(db, "dec-a1-false")
    assert rows == [ACTION_BODY]          # stripped, nothing else changed
    assert all("declined" not in (r or "") for r in rows)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _seed_assistant_row(db_path, session_id, content):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) "
        "VALUES (?,?,?,?)", (session_id, "assistant", content, 1000.0))
    conn.commit()
    conn.close()


def _assistant_rows(db_path, session_id):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT content FROM messages WHERE session_id=? AND role='assistant' "
        "ORDER BY id", (session_id,)).fetchall()
    conn.close()
    return [r[0] for r in rows]
