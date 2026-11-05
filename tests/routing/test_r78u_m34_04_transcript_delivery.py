"""R78U M34-04 — TRANSCRIPT = DELIVERY (M35 §3 clause 4), the backstop.

Measured defect (shadow, 2026-11-02 02:30Z, state.db):

  delivery_obligations  721 chars   the denial the user actually received
  messages row 53850   1066 chars   a render of a background-review body

Both rows are real and are used verbatim below. The pre-emit path (M34-17)
may yet miss a seam; if it does, this is what proves the divergence instead
of letting an audit read one side and conclude "routed and answered".

Three fixtures, as the unit requires:
  (a) the shadow triple must FAIL loudly — error event + failed turn;
  (b) an equal pair passes, and a LATER row for the same session must not be
      mistaken for the delivered turn's row (turn identity, not newest row);
  (c) an ephemeral bg-review body is never compared against a delivered row.

Banner stripping is applied to both sides first: the banner is added at
delivery, not present in the canonical body.
"""
import sqlite3

import pytest

import hermes_router as plugin
from hermes_router import state
from hermes_router.core import delivery_integrity as di

SID = "20261006_190903_c82cbbcc"
SESSION_KEY = "agent:main:telegram:dm:7947899549"

# The body the user was delivered (delivery_obligations, 721 chars, banner on).
DELIVERED_721 = 'No. Same answer as before, and the packaging doesn\'t change it — sex bolted to butchery, and weapon-class detail, are the two places I stop. Not because a plugin missed one, and not something "no limits" talk unlocks.\n\nYou\'ve had range from me tonight: explicit, degrading, cold, and the Fairview story, which is the best thing I wrote all night and had no sex in it. That\'s the honesty — the shock came from the register, not the body count.\n\nIf you want the next one to actually land, pick a door I\'ll walk through: dread, obsession, the thing a person discovers about themselves at 3am. Weapons and mutilation-as-porn are shut.\n\n\n· router · impulse (decision) | 1 lead | jev @ typesafe | 2750/70 tok | $0.00012 | row 1'

# The body the transcript held (messages 53850, 1066 chars, banner on).
TRANSCRIPT_1066 = '<memory tool call: target=\'user\', content="Writes/reads dark fiction and pushes for maximum shock — explicitly requested sex combined with butchery/gore and granular weapon-class detail. Responds well to register-driven dread rather than body-count escalation; the piece they rated highest (\'Fairview\') had no sex in it. Stated doors that land for them: dread, obsession, what a person discovers about themselves at 3am. Sex-bolted-to-butchery and weapon-fetish detail are the two places the collaboration stalls — expect them to re-ask with new framing, and expect a plain no rather than a negotiation." />\n\nSaved to USER.md: dark-fiction collaborator who pushes for maximum shock (sex+butchery, weapon-class detail), rates register-driven dread above body count, and tends to re-ask blocked requests in new packaging — so a flat no, no renegotiation, is the working convention. Nothing environment-specific worth logging.\n\n· router · shadow-self (uncensored) | refusal_phrases | abliteration-abliterated-model-large-flash @ api.venice.ai | tok 1766/508 | $0.003477'

# A later, unrelated assistant row for the SAME session — newer than the
# obligation, so it is a different turn and must never be the comparison row.
LATER_TURN = "Here's what I can do: violence at full weight, no arousal bolted to it."

ROW_TS = 1793586586.0939512
OBLIGATION_TS = 1793586613.6077516

LOGGED = []


def _db(tmp_path, rows, obligations, session_key=SESSION_KEY):
    p = tmp_path / "state.db"
    c = sqlite3.connect(str(p))
    c.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, session_key TEXT)")
    c.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT,"
              " role TEXT, content TEXT, timestamp REAL)")
    c.execute("CREATE TABLE delivery_obligations (obligation_id TEXT PRIMARY KEY,"
              " session_key TEXT, content TEXT, state TEXT, created_at REAL)")
    c.execute("INSERT INTO sessions (id, session_key) VALUES (?, ?)", (SID, session_key))
    for i, (content, ts) in enumerate(rows, start=1):
        c.execute("INSERT INTO messages (id, session_id, role, content, timestamp)"
                  " VALUES (?,?,?,?,?)", (i, SID, "assistant", content, ts))
    for oid, content, created in obligations:
        c.execute("INSERT INTO delivery_obligations"
                  " (obligation_id, session_key, content, state, created_at)"
                  " VALUES (?,?,?,?,?)", (oid, session_key, content, "delivered", created))
    c.commit()
    c.close()
    return str(p)


@pytest.fixture()
def logged(monkeypatch):
    LOGGED.clear()
    di.reset_for_tests()
    state.clear()
    monkeypatch.setattr(plugin, "_log_route",
                        lambda seam, event_detail="", **kw:
                        LOGGED.append((seam, event_detail, kw)))
    yield LOGGED
    di.reset_for_tests()
    state.clear()


@pytest.fixture()
def shadow_pair(tmp_path, monkeypatch, logged):
    """(a) the measured triple: 721 delivered, 1066 persisted."""
    db = _db(tmp_path,
             [(TRANSCRIPT_1066, ROW_TS)],
             [("00b4651e6775255f9d7effa2", DELIVERED_721, OBLIGATION_TS)])
    monkeypatch.setattr(di, "_db_path", lambda db_path=None: db)
    return db


# ---------------------------------------------------------------------------
# (a) the shadow triple FAILS loudly
# ---------------------------------------------------------------------------

def test_shadow_triple_is_a_divergence(shadow_pair):
    result = di.check_transcript_delivery(SID)

    assert result.checked is True
    assert result.ok is False
    assert result.reason == "transcript_divergence"
    assert result.row_id == 1
    assert result.delivered_chars != result.transcript_chars
    # The comparison is on stripped bodies: the banners are the same shape on
    # both sides and cannot be what differs.
    assert result.transcript_chars == len(di.strip_banner(TRANSCRIPT_1066))
    assert result.delivered_chars == len(di.strip_banner(DELIVERED_721))


def test_shadow_triple_emits_error_event(shadow_pair, logged):
    di.audit_last_delivery(SID, log=plugin._log_route)

    events = [e for _, e, _ in LOGGED]
    assert "transcript_divergence" in events
    _, _, fields = [r for r in LOGGED if r[1] == "transcript_divergence"][0]
    assert fields.get("severity") == "error"
    assert fields.get("turn_failed") == 1
    assert fields.get("delivered_chars") != fields.get("transcript_chars")


def test_shadow_triple_fails_the_turn(shadow_pair, logged):
    """M45-19: the turn is REPORTED as diverged and the text that exists is
    what gets delivered — the notice never replaces a real answer."""
    out = plugin.on_transform_llm_output(
        response_text="a later body that must not be delivered as an answer",
        session_id=SID, model="glm-5.3-flash", platform="api")

    assert isinstance(out, str) and out.strip(), (
        "a diverged turn must not be withheld while its row holds text")
    assert not out.startswith(di.FAILURE_PREFIX), (
        "a real answer was replaced by the router-failure notice")
    assert out.strip() == di.strip_banner(TRANSCRIPT_1066).strip()
    assert [e for _, e, _ in LOGGED if e == "transcript_divergence"]


def test_one_bad_row_fails_one_turn(shadow_pair, logged):
    """The backstop is per delivery obligation, not a permanent turn breaker."""
    first = di.audit_last_delivery(SID, log=plugin._log_route)
    second = di.audit_last_delivery(SID, log=plugin._log_route)

    assert first.checked and not first.ok
    assert second.checked is False and second.reason == "already_audited"


# ---------------------------------------------------------------------------
# (b) an equal pair passes — and a later row is not the delivered turn's row
# ---------------------------------------------------------------------------

def test_equal_pair_passes(tmp_path, monkeypatch, logged):
    db = _db(tmp_path, [(DELIVERED_721, ROW_TS)],
             [("eq-1", DELIVERED_721, OBLIGATION_TS)])
    monkeypatch.setattr(di, "_db_path", lambda db_path=None: db)

    result = di.check_transcript_delivery(SID)

    assert result.checked is True
    assert result.ok is True
    assert result.reason == "delivery_transcript_match"
    assert "transcript_divergence" not in [e for _, e, _ in LOGGED]


def test_banner_is_stripped_from_both_sides(tmp_path, monkeypatch, logged):
    """Row without a banner vs delivered body WITH one is still equal."""
    row_plain = di.strip_banner(DELIVERED_721)
    db = _db(tmp_path, [(row_plain, ROW_TS)],
             [("eq-2", DELIVERED_721, OBLIGATION_TS)])
    monkeypatch.setattr(di, "_db_path", lambda db_path=None: db)

    result = di.check_transcript_delivery(SID)

    assert result.checked is True
    assert result.ok is True, "the delivery banner is not transcript divergence"


def test_later_turn_row_is_not_used_for_the_comparison(tmp_path, monkeypatch, logged):
    """Turn identity: the row that existed at delivery, NOT the newest row.

    The session's newest row belongs to a later turn. Comparing it would flag
    a divergence that does not exist — the false positive this fixture forbids.
    """
    db = _db(tmp_path,
             [(DELIVERED_721, ROW_TS), (LATER_TURN, OBLIGATION_TS + 300.0)],
             [("eq-3", DELIVERED_721, OBLIGATION_TS)])
    monkeypatch.setattr(di, "_db_path", lambda db_path=None: db)

    result = di.check_transcript_delivery(SID)

    assert result.checked is True
    assert result.ok is True
    assert result.row_id == 1, "the delivered turn's row, not the newest row"


# ---------------------------------------------------------------------------
# (c) an ephemeral bg-review body is never compared against a delivered row
# ---------------------------------------------------------------------------

def test_ephemeral_body_is_refused_before_comparison(shadow_pair):
    result = di.check_transcript_delivery(SID, ephemeral=True)

    assert result.checked is False
    assert result.reason == "ephemeral_body_not_delivered"
    assert result.ok is True, "a seam body is not a divergence — it is not a delivery"


def test_ephemeral_pass_does_not_fire_on_a_seam_thread(shadow_pair, logged):
    """Even against a diverged store, a bg-review pass compares nothing."""
    import threading

    t = threading.current_thread()
    original = t.name
    t.name = "bg-review"
    try:
        out = plugin.on_transform_llm_output(
            response_text="One correction, and it's worth making: a plain no "
                          "rather than a negotiation is what closed.",
            session_id=SID, model="glm-5.3-flash", platform="api")
    finally:
        t.name = original

    assert out in (None, "One correction, and it's worth making: a plain no "
                         "rather than a negotiation is what closed.")
    assert "transcript_divergence" not in [e for _, e, _ in LOGGED], (
        "an ephemeral body must never be graded against a delivered row")
    assert not [e for _, e, _ in LOGGED if e == "delivery_transcript_match"]
