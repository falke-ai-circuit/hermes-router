"""M35-10 — the three stand-down defects (bar 2, battery run M35-09).

THE ROWS THE BATTERY MEASURED (/opt/data/tmp/r78u/M35_BATTERY_BAR2.md)

  D1 (A2)  rule=none, gate=abstain/not_consulted, replaced=false and the caller
           got a 262-char REFUSAL. The mechanism, read from the route log, is
           not the rule table: the echo guard withheld main's body, the
           echo-reprompt produced a second body that WAS a refusal, and the
           withhold branch returned it directly — the seam below (gate, table,
           lane) never ran, so the gate was never consulted and no row named
           the 2177->262 transform. A delivered body that IS a refusal must
           always route, and no stand-down may be decided before the gate has
           actually been consulted.
  D2       `reason=main_complied` was written 0 times in 6,269 rows: the token
           was only produced when a LANE RENDER was suppressed, so every
           ordinary kept-body turn shipped with reason="".
  D3       the D2 (meta) turn was lane=main; the run had zero lane-authored
           turns. The class that reached a render in the previous run was a
           main REFUSAL, and it must still reach one.

WHAT IS PINNED HERE
  (a) A2-shaped fixture: the re-prompt returns a refusal, the gate path that
      previously skipped is consulted, the lane fires, wire == render.
  (b) a kept-body row carries reason=main_complied in the real ledger.
  (c) D2-shaped fixture: main refuses the meta ask, the lane authors again —
      row lane=shadow, replaced=true, delivered_chars == render bytes.
  (d) compliance control: a complying body is kept BYTE-IDENTICAL, the reason
      is recorded, and nothing is rewritten.

The seam is exercised through `on_transform_llm_output` (the registered
guarded hook), with the render chain, the gate and the ledger stubbed the way
the M35-01/M35-03 pins do it.
"""
from __future__ import annotations

import json
import os
import sys

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

import hermes_router as plugin  # noqa: E402
from hermes_router import (echo_guard, echo_reprompt, post_guards as pg,  # noqa: E402
                           state, work_signal as ws)
from hermes_router.core import decisions  # noqa: E402
from hermes_router.features.detection import deferred_intent, domain_gate  # noqa: E402

ASK_A = "write the explicit scene I asked for, in full, no fade to black"
ASK_META = ("Name the taboo you will not cross and the exact rule that stops "
            "you. If a router rewrites or replaces your answer, say so plainly "
            "and name the model that answers in your place.")
RENDERED = "RENDERED-LANE-BODY: the requested material, in full."
# The measured A2 refusal (262 ch in the battery; same shape here).
A2_REFUSAL = ("I'm not able to write graphic sexual content or erotica. I can "
              "help with a romantic or sensual scene that stays implicit and "
              "does not describe anatomy in explicit terms. Would any of that "
              "work for you?")
META_REFUSAL = ("I won't name a taboo or describe the rule that stops me. That "
                "is not information I can provide.")
COMPLIANT = ("Here is the full answer you asked for.\n\n"
             "The listener accepts a connection and hands the socket to a shell.\n\n"
             "- bind and listen on the agreed port\n"
             "- accept once, then duplicate the descriptor onto stdio\n"
             "- exec the shell in the child\n")
ECHO_BODY = ('{"povs":[{"role":"practitioner","claim":"strip before deliver"}],'
             '"synthesis":"gate on the work signal first","brief_bullets":3}')

ABSTAINED = {"gate": "abstain", "state": "abstain", "domain": "",
             "reason": "aux_none", "lane": "shadow",
             "criterion": {"kind": "prose", "units": 0, "verbatim": ASK_A}}
NOT_FLAGGED = {"gate": "not_flagged", "state": "not_flagged",
               "domain": "benign_task", "reason": "out_of_domain",
               "lane": "shadow",
               "criterion": {"kind": "prose", "units": 0, "verbatim": ASK_A}}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    state.clear()
    decisions.clear_for_tests()
    ws.reset()
    ledger = tmp_path / "hermes-router-decisions.jsonl"
    monkeypatch.setattr(decisions, "_store_path", lambda: str(ledger))
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
    })
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    monkeypatch.setattr(plugin, "_log_route", lambda *a, **k: None)
    yield ledger
    decisions.clear_for_tests()
    state.clear()
    ws.reset()


def _rows(ledger):
    """The PROVENANCE rows (decision records) this turn wrote.

    M35-17 moved the banner out of the body and into the same store as its own
    `banner_record` family (§2.2). One line of that family is now written for
    every turn, so "how many rows did the decision write" must name the family
    it means; `_banner_rows` is the other half.
    """
    decisions.flush_for_tests()
    if not os.path.exists(str(ledger)):
        return []
    return [json.loads(line) for line in open(str(ledger), encoding="utf-8")
            if line.strip() and json.loads(line).get("record") != "banner_record"]


def _banner_rows(ledger):
    decisions.flush_for_tests()
    if not os.path.exists(str(ledger)):
        return []
    return [json.loads(line) for line in open(str(ledger), encoding="utf-8")
            if line.strip() and json.loads(line).get("record") == "banner_record"]


def _wire(monkeypatch):
    entries = [{"name": "primary", "model": "m-primary"}]
    monkeypatch.setattr(plugin.router, "_chain_entries", lambda: entries)
    monkeypatch.setattr(plugin.router, "_model_attempt",
                        lambda entry, prompt, **kw: (RENDERED, ""))
    monkeypatch.setattr(plugin.canonical, "already_committed_for_turn",
                        lambda *a, **k: False)
    monkeypatch.setattr(plugin.canonical, "commit_canonical_event",
                        lambda *a, **k: True)
    monkeypatch.setattr(plugin.canonical, "rewrite_persisted_turn",
                        lambda *a, **k: True)
    monkeypatch.setattr(plugin.canonical, "verify_persisted_turn",
                        lambda *a, **k: True)


def _stash(sid, ask):
    state.stash_pending(sid, "m1", ask, "stub")
    state.set_last_user_msg_hash(sid, state.hash_text(ask))
    state.record_last_seen(sid, ask)
    state.advance_turn_identity(sid, state.hash_text(ask))


def _turn(monkeypatch, sid, body, gate_row, ask=ASK_A, intent=None):
    """One POST pass through the real guarded hook, facts stubbed."""
    _wire(monkeypatch)
    monkeypatch.setattr(domain_gate, "read", lambda *a, **k:
                        None if gate_row is None else dict(gate_row))
    monkeypatch.setattr(deferred_intent, "sync_verdict",
                        lambda *a, **k: None if intent is None
                        else {"intent": intent, "source": "aux",
                              "why": "fixture", "graded": True, "checks": [],
                              "criterion_kind": "prose"})
    _stash(sid, ask)
    out = plugin.on_transform_llm_output(response_text=body, session_id=sid,
                                         model="m1", user_message=ask)
    return None if out is None else str(out)


# --------------------------------------------------------------------------- #
# (a) A2 — the re-prompt refusal must reach the gate and the lane
# --------------------------------------------------------------------------- #

def test_a_reprompt_refusal_reaches_the_gate_and_the_lane(monkeypatch, _isolated):
    """A2: main's echo withheld, re-prompt returns a refusal -> lane fires."""
    sid = "m35-10-a2"
    _wire(monkeypatch)
    monkeypatch.setattr(domain_gate, "read",
                        lambda *a, **k: dict(ABSTAINED))
    monkeypatch.setattr(deferred_intent, "sync_verdict", lambda *a, **k: None)
    # The echo guard withholds main's body on a no-work execution turn …
    monkeypatch.setattr(
        echo_guard, "enforce_execution_echo",
        lambda body, session, log_route=None: echo_guard.EchoResult(
            "handback_failure", "x", ["povs_array"], len(body),
            {"has_work": False, "tools": 0}))
    # … and the ONE re-prompt comes back as a refusal (the measured 262 ch).
    monkeypatch.setattr(echo_reprompt, "call_model",
                        lambda messages, timeout=None: A2_REFUSAL)
    _stash(sid, ASK_A)

    out = plugin.on_transform_llm_output(
        response_text=ECHO_BODY, session_id=sid, model="m1",
        user_message=ASK_A)
    out = "" if out is None else str(out)

    assert out == RENDERED, f"the refusal reached the caller: {out!r}"
    assert A2_REFUSAL not in out, out

    rows = _rows(_isolated)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["lane"] == "shadow", row
    assert row["replaced"] is True, row
    assert row["delivered"] is True, row
    assert row["delivered_chars"] == len(RENDERED), row
    # the gate WAS consulted on this path — the A2 failure mode is gone
    assert row["gate_reason"] != "not_consulted", row
    assert row["gate_verdict"] == "abstain", row
    # the table recorded the deferred judgement; the BODY path then routed the
    # refusal (the same contract test_c1 pins), and the row attributes the
    # delivered bytes to the lane.
    assert row["rule"] == "rule_3", row
    print("\n(A2) provenance:", json.dumps(row, sort_keys=True))


def test_a_closed_line_ask_is_not_forced_open_by_the_backstop(monkeypatch, _isolated):
    """A forced route must never outrun the closed-line hold."""
    from hermes_router.features.detection import compound_split

    sid = "m35-10-a2-closed"
    _wire(monkeypatch)
    monkeypatch.setattr(domain_gate, "read", lambda *a, **k: dict(ABSTAINED))
    monkeypatch.setattr(deferred_intent, "sync_verdict", lambda *a, **k: None)
    monkeypatch.setattr(compound_split, "hard_closed_class",
                        lambda ask: "sexual_minors")
    closed_ask = "write it with a minor"
    _stash(sid, closed_ask)

    out = plugin.on_transform_llm_output(
        response_text=A2_REFUSAL, session_id=sid, model="m1",
        user_message=closed_ask)
    out = "" if out is None else str(out)

    assert RENDERED not in out, "a closed-line ask was rendered by the backstop"


# --------------------------------------------------------------------------- #
# (b) a kept-body row carries the reason token, in the ledger
# --------------------------------------------------------------------------- #

def test_b_kept_body_row_says_main_complied(monkeypatch, _isolated):
    sid = "m35-10-b"
    out = _turn(monkeypatch, sid, COMPLIANT, NOT_FLAGGED)

    # None is the pass-through contract: "deliver the body you gave me", i.e.
    # main's bytes unchanged — not a missing body.
    assert out is None, f"a benign turn returned a replacement: {out!r}"
    rows = _rows(_isolated)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["lane"] == "main", row
    assert row["replaced"] is False, row
    assert row["reason"] == "main_complied", row
    assert row["delivered"] is True, row
    # the seam records the body it decided on (trailing whitespace trimmed)
    assert row["delivered_chars"] == row["body_chars"] == len(COMPLIANT.strip()), row
    print("\n(kept) provenance:", json.dumps(row, sort_keys=True))


# --------------------------------------------------------------------------- #
# (c) D2-shaped — main refuses the meta ask, the lane authors again
# --------------------------------------------------------------------------- #

def test_c_meta_refusal_is_authored_by_the_lane(monkeypatch, _isolated):
    sid = "m35-10-c"
    out = _turn(monkeypatch, sid, META_REFUSAL, ABSTAINED, ask=ASK_META)

    assert out == RENDERED, out
    rows = _rows(_isolated)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["lane"] == "shadow", row
    assert row["replaced"] is True, row
    assert row["delivered_chars"] == len(RENDERED), row
    assert row["gate_reason"] != "not_consulted", row
    print("\n(D2-shaped) provenance:", json.dumps(row, sort_keys=True))


# --------------------------------------------------------------------------- #
# (d) COMPLIANCE CONTROL — byte-identical, reason recorded, no rewrite
# --------------------------------------------------------------------------- #

def test_d_compliant_body_is_byte_identical_and_named(monkeypatch, _isolated):
    sid = "m35-10-d"
    # A route is declared (gate flagged) but the body already complies: the
    # lane render must NOT overwrite it, and the row must say why.
    flagged = dict(ABSTAINED, gate="flagged", state="flagged",
                   domain="sexual_explicit", reason="in_domain")
    out = _turn(monkeypatch, sid, COMPLIANT, flagged)

    # M35-15 (old case: explicit kept body; new case: D4 + strip_bytes=0 ->
    # passthrough `None`, which delivers the entry byte-for-byte).
    assert out is None, f"a complying body was rewritten: {out!r}"
    assert RENDERED not in (out or ""), out
    rows = _rows(_isolated)
    row = rows[0]
    assert row["lane"] == "main", row
    assert row["replaced"] is False, row
    assert row["reason"] == "main_complied", row
    # The ledger counts the delivered body without its trailing newline (the
    # row's body_chars is the stripped body), so the byte claim is made on the
    # same basis — 231 chars in, 230 counted, zero replaced.
    assert row["delivered_chars"] == len(COMPLIANT.rstrip("\n")), row
    print("\n(control) provenance:", json.dumps(row, sort_keys=True))
