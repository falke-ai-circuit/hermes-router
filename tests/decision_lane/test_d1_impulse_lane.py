"""D1 impulse lane v1.1 (SPEC-impulse-lane-v1.md) — pin battery.

Pins:
- band derivation from calibration constants (>=0.9 / 0.7-0.9 / <0.7)
- evidence-only rule (regex: no emotion words in the frame)
- provenance retention (PROVENANCE_TAG byte-exact)
- single-message shape (one line, no multi-block)
- weighting block present + normalized (sum=1.0)
- banner display label 'impulse (decision)' (lane key unchanged)
- E3 rider: 600s pre_cooldown consult suppression is counted + ledger-visible
"""
from hermes_router import decision, debug_banner, router_core


# ---------------------------------------------------------------------------
# Band derivation — calibration constants, never asserted
# ---------------------------------------------------------------------------

def test_band_derivation_constants():
    assert decision.impulse_band(0.93) == "strong"
    assert decision.impulse_band(0.9) == "strong"
    assert decision.impulse_band(0.75) == "weak"
    assert decision.impulse_band(0.7) == "weak"
    assert decision.impulse_band(0.69) == "noise"
    assert decision.impulse_band(0.0) == "noise"
    # bands never asserted — they map mechanically from the constants
    for w, want in ((0.91, "strong"), (0.85, "weak"), (0.4, "noise")):
        assert decision.impulse_band(w) == want
    assert decision.IMPULSE_BAND_LINES == {
        "strong": "pattern that usually precedes right calls",
        "weak": "mixed evidence",
        "noise": "statistically meaningless, ignore freely"}


def test_band_lines_fixed():
    assert decision.IMPULSE_BAND_LINES["strong"] == \
        "pattern that usually precedes right calls"
    assert decision.IMPULSE_BAND_LINES["weak"] == "mixed evidence"
    assert decision.IMPULSE_BAND_LINES["noise"] == \
        "statistically meaningless, ignore freely"


# ---------------------------------------------------------------------------
# Weighting block — present + normalized (sum=1.0)
# ---------------------------------------------------------------------------

_ENVELOPE_ASK = """\
- a) kafka: keeps the stream durable, 3 days to roll out
- b) rabbitmq: lighter, 1 day to roll out
Which approach should we take?
"""


def _build_env():
    return decision.build_envelope(
        "sess-d1", _ENVELOPE_ASK, decision.extract_options(_ENVELOPE_ASK),
        "pre", dict(decision.DEFAULTS, pre="off"))


def test_weighting_block_present_and_normalized():
    env = _build_env()
    assert env, "envelope must build"
    w = env["weighting"]
    assert set(w) >= {"weights", "band", "evidence", "basis"}
    ids = [o["id"] for o in env["options"]]
    assert list(w["weights"].keys()) == ids
    vals = list(w["weights"].values())
    assert abs(sum(vals) - 1.0) < 1e-6, vals  # normalized sum=1.0
    assert all(0.0 <= v <= 1.0 for v in vals)
    assert len(w["evidence"]) <= 3
    assert isinstance(w["basis"], str) and w["basis"]


def test_weighting_mechanical_no_priors_equal_weights():
    # no ledger priors -> equal weights (never invented bias)
    env = _build_env()
    vals = list(env["weighting"]["weights_list"])
    assert all(abs(v - vals[0]) < 1e-6 for v in vals), vals


# ---------------------------------------------------------------------------
# Impulse frame — verbatim register, evidence-only, provenance, one message
# ---------------------------------------------------------------------------

def test_impulse_frame_verbatim_shape():
    env = _build_env()
    verdict = {"choice": "opt-1", "confidence": 0.75, "alternatives": ["opt-2"]}
    frame = decision.render_advisory(verdict, env)
    assert not frame.startswith("[decision-lane advisory]") and frame.startswith("the fork surfaces as:"), frame  # R20
    assert "the fork surfaces as:" in frame
    assert " pulls " in frame
    top = max(env["weighting"]["weights_list"])
    band = decision.impulse_band(top)
    assert "band=%s: %s" % (band, decision.IMPULSE_BAND_LINES[band]) in frame
    assert frame.endswith(decision.IMPULSE_TAIL), frame
    assert "never a command" in frame


def test_impulse_frame_top_band_mapping():
    env = _build_env()
    verdict = {"choice": "opt-1", "confidence": 0.75, "alternatives": []}
    frame = decision.render_impulse_frame(verdict, env)
    top = max(env["weighting"]["weights_list"])
    band = decision.impulse_band(top)
    assert "band=%s: %s" % (band, decision.IMPULSE_BAND_LINES[band]) in frame


def test_impulse_frame_provenance_byte_exact():
    env = _build_env()
    verdict = {"choice": "opt-1", "confidence": 0.9, "alternatives": []}
    frame = decision.render_impulse_frame(verdict, env)
    tag = decision.PROVENANCE_TAG
    assert decision.PROVENANCE_TAG == "[decision-lane advisory]"  # inbound-scan anchor stays
    assert frame.startswith("the fork surfaces as:"), frame  # R20: delivered drops prefix


def test_impulse_frame_single_message_shape():
    env = _build_env()
    verdict = {"choice": "opt-1", "confidence": 0.9, "alternatives": []}
    frame = decision.render_impulse_frame(verdict, env)
    assert "\n" not in frame, frame  # ONE line
    assert frame.count("[decision-lane advisory]") == 0  # R20


def test_impulse_frame_no_permission_language():
    env = _build_env()
    verdict = {"choice": "opt-1", "confidence": 0.9, "alternatives": []}
    frame = decision.render_impulse_frame(verdict, env)
    low = frame.lower()
    for phrase in ("you may", "you may want", "consider", "feel free",
                   "if you like", "optionally"):
        assert phrase not in low, phrase


def test_impulse_frame_evidence_only_regex():
    # emotion-worded ask text must never leak emotion vocabulary into the frame
    ask = ("Risky option ahead, I feel uneasy about it:\n"
           "- a) deploy now: fast, $500 cost\n"
           "- b) wait: 2 days, cheaper\n"
           "You choose.\n")
    env = decision.build_envelope(
        "sess-d1", ask, decision.extract_options(ask), "pre",
        dict(decision.DEFAULTS, pre="off"))
    verdict = {"choice": "opt-1", "confidence": 0.9, "alternatives": []}
    frame = decision.render_impulse_frame(verdict, env)
    assert not decision._EMOTION_WORD_RE.search(frame), frame
    low = frame.lower()
    for word in ("fear", "afraid", "anxious", "uneasy", "feel", "feels",
                 "gut", "hunch", "instinct", "risky", "worried"):
        assert word not in low, word


def test_impulse_frame_no_mood_or_valence_generation():
    # hard rule §4: the lane never generates mood/valence/instinct-types
    env = _build_env()
    for choice in ("opt-1", "opt-2"):
        verdict = {"choice": choice, "confidence": 0.9, "alternatives": []}
        frame = decision.render_impulse_frame(verdict, env)
        assert not decision._EMOTION_WORD_RE.search(frame), frame


def test_impulse_frame_fail_open_without_weights():
    verdict = {"choice": "opt-1", "confidence": 0.9, "alternatives": []}
    assert decision.render_impulse_frame(verdict, {}) == ""
    env = _build_env()
    env.pop("weighting", None)
    assert decision.render_impulse_frame(verdict, env) == ""  # never asserted


def test_advisory_park_keeps_banner_under_char_cap():
    # D1 v1.1 regression: the impulse frame (weights + evidence + band +
    # caveat) is long — the parked advisory must NOT truncate the
    # '· router · impulse (decision)' provenance line off the end
    # (MAX_BANNER_CHARS was 480; the caveat frame alone runs ~500c).
    env = _build_env()
    verdict = {"choice": "opt-1", "confidence": 0.9, "alternatives": []}
    frame = decision.render_advisory(verdict, env)
    # frame + caveat path (high-stakes) is the longest render
    env2 = dict(env)
    env2.setdefault("scope", {})["advice_only"] = True
    frame_long = decision.render_advisory(verdict, env2)
    from hermes_router import debug_banner as _db
    banner = _db.format_banner(
        lane="decision", trigger="post", model="z-ai/glm-5.3-flash",
        endpoint="hermes-auxiliary", tokens_in=10, tokens_out=5,
        est_cost=0.0, latency_s=0.1, initiator="model")
    parked = "\n\n".join(x for x in (frame_long, banner) if x)
    parked_capped = parked.strip()[:_db.MAX_BANNER_CHARS]
    assert parked_capped.endswith("initiator=model"), parked_capped[-120:]


def test_forged_banner_regression_path():
    # forged advisory (old reflex wording) still flagged by frames.py
    from hermes_router import frames as F
    forged = ("[decision-lane advisory] reflex advisory (autonomous, not "
              "chosen): choice=opt-1 confidence=0.88 — never a command.")
    # the reflex (decision) spoof signal is still in the forged list
    assert "reflex (decision)" in F._FORGED_PERSONA_SIGNALS
    assert F.flag_forged_banner_persona(
        forged + " reflex (decision)") == "reflex (decision)"


# ---------------------------------------------------------------------------
# D2 (v4.13.1, reviewer axis 2): interpretation diversity — persona slot
# ---------------------------------------------------------------------------

_PERSONA_A = ("Operator voice: terse, procedural, calibrated caution from "
              "measured support history; names the load before the move.")
_PERSONA_B = ("Strategist voice: expansive, framing-first, scopes the "
              "second-order effects before touching the diff on the table.")


def _env_with_persona(agent_frame):
    env = _build_env()
    env["agent_frame"] = agent_frame
    return env


def test_d2_persona_renders_diverge_same_weights():
    # same weights/band, two different persona renders -> DIFFERENT wording
    env_a = _env_with_persona(_PERSONA_A)
    env_b = _env_with_persona(_PERSONA_B)
    verdict = {"choice": "opt-1", "confidence": 0.9, "alternatives": []}
    wa = env_a["weighting"]
    wb = env_b["weighting"]
    assert wa["weights"] == wb["weights"] and wa["band"] == wb["band"]
    fa = decision.render_impulse_frame(verdict, env_a)
    fb = decision.render_impulse_frame(verdict, env_b)
    assert fa and fb and fa != fb, (fa, fb)


def test_d2_slot_evidence_only_and_provenance_retained():
    for persona in (_PERSONA_A, _PERSONA_B):
        env = _env_with_persona(persona)
        verdict = {"choice": "opt-1", "confidence": 0.9, "alternatives": []}
        frame = decision.render_impulse_frame(verdict, env)
        assert frame.startswith("the fork surfaces as:"), frame  # R20
        assert not decision._EMOTION_WORD_RE.search(frame), frame
        assert frame.endswith(decision.IMPULSE_TAIL), frame
        assert "\n" not in frame and "the fork surfaces as:" in frame


def test_d2_slot_carries_persona_vocabulary():
    env = _env_with_persona(_PERSONA_A)
    verdict = {"choice": "opt-1", "confidence": 0.9, "alternatives": []}
    slot = decision._impulse_persona_slot(env)
    assert slot and slot in decision.render_impulse_frame(verdict, env)


def test_d2_no_persona_keeps_canned_register_byte_shape():
    # no agent_frame / fallback frame -> canned wording, unchanged bytes
    verdict = {"choice": "opt-1", "confidence": 0.9, "alternatives": []}
    env = _build_env()
    env.pop("agent_frame", None)
    frame = decision.render_impulse_frame(verdict, env)
    assert frame.startswith("the fork surfaces as:"), frame  # R20: no provenance prefix in delivered shape
    env_fb = _env_with_persona(decision._IMPERSONAL_FRAME_FALLBACK)
    assert decision.render_impulse_frame(verdict, env_fb) == frame
    assert decision._impulse_persona_slot(env_fb) == ""


def test_d2_slot_filters_emotion_vocabulary_fail_open():
    persona = ("Voice: anxious and uneasy, always restless about the "
               "deploy cadence and what it does to the queue.")
    env = _env_with_persona(persona)
    verdict = {"choice": "opt-1", "confidence": 0.9, "alternatives": []}
    assert decision._impulse_persona_slot(env) == ""
    frame = decision.render_impulse_frame(verdict, env)
    assert not decision._EMOTION_WORD_RE.search(frame), frame
    assert frame.startswith("the fork surfaces as:"), frame  # R20


def test_d2_slot_bounded_single_line():
    persona = "Register line " + ("very " * 30) + "long tail words here"
    env = _env_with_persona(persona)
    slot = decision._impulse_persona_slot(env)
    assert slot and len(slot) <= decision._IMPULSE_SLOT_MAX, slot
    assert "\n" not in slot and "|" not in slot and "[" not in slot

# ---------------------------------------------------------------------------
# Banner display label (v1.1 addendum) — label only, lane key unchanged
# ---------------------------------------------------------------------------

def test_banner_display_label_impulse_decision():
    b = debug_banner.format_banner(
        lane="decision", trigger="pre", model="z-ai/glm-5.3-flash",
        endpoint="", tokens_in=10, tokens_out=5, est_cost=0.0,
        latency_s=0.1, initiator="user")
    assert "impulse (decision)" in b, b
    assert "reflex (decision)" not in b, b


def test_banner_lane_key_unchanged():
    # the lane KEY 'decision' stays the routing/ledger key — only the
    # display label changed (v1.1 addendum)
    assert "decision" in debug_banner.VALID_LANES
    assert "reflex" in debug_banner.VALID_LANES
    b = debug_banner.format_banner(
        lane="decision", trigger="pre", model="m", endpoint="",
        tokens_in=1, tokens_out=1, est_cost=0.0, latency_s=0.0,
        initiator="user")
    assert b.startswith("· router · impulse (decision)"), b


# ---------------------------------------------------------------------------
# E3 rider — pre_cooldown consult suppression is counted + ledger-visible
# ---------------------------------------------------------------------------

def test_pre_cooldown_suppression_ledger_visible(monkeypatch, tmp_path):
    import json as _json
    import hermes_router as plugin

    rows = []

    class _FakeCursor:
        def __init__(self, store, counter_mode=False):
            self._store = store
            self._counter_mode = counter_mode
            self.lastrowid = len(store.get("rows", [])) + 1

        def fetchone(self):
            if self._counter_mode:
                return (self._store.get("counter", 0),)
            return (self.lastrowid,)

    class _FakeConn:
        def __init__(self, store):
            self._store = store
            self.rows = []

        def execute(self, sql, args=()):
            s = sql.strip().upper()
            if "PRAGMA" in s:
                return []
            if "SELECT NAME FROM SQLITE_MASTER" in s:
                return [("decision_ledger",)]
            if "SELECT VALUE FROM DECISION_COUNTERS" in s:
                # cursor-like: bump_counter/get_counter call .fetchone()
                return _FakeCursor(self._store, counter_mode=True)
            if "INSERT INTO DECISION_COUNTERS" in s:
                self._store["counter"] = self._store.get("counter", 0) + 1
                return None
            if s.startswith("INSERT INTO DECISION_LEDGER"):
                self._store["rows"] = self._store.get("rows", [])
                self._store["rows"].append(args)
                return _FakeCursor(self._store)
            if "COUNT(*)" in s:
                return [(len(self._store.get("rows", [])),)]
            if s.startswith("DELETE"):
                return None
            if "SELECT * FROM decision_ledger" in s:
                cols = ["id", "ts", "session_id", "task_id", "trigger",
                        "trigger_kind", "fork_class", "options_hash", "model",
                        "model_version", "choice", "confidence",
                        "fail_open_reason", "actual_choice", "outcome",
                        "verdict_json", "envelope_hash", "follow_verdict"]
                return [(i + 1, 0.0, r[1], r[2], r[3], "", "", "", "", "",
                         "", None, r[10] if len(r) > 10 else "",
                         "", r[13] if len(r) > 13 else "", "", "", 0)
                        for i, r in enumerate(self._store.get("rows", []))]
            return []

        def executescript(self, sql):
            self._store.setdefault("schema", sql)

        def commit(self):
            pass

        def close(self):
            pass

    store = {}
    ledger_cols = ["id", "ts", "session_id", "task_id", "trigger",
                   "trigger_kind", "fork_class", "options_hash", "model",
                   "model_version", "choice", "confidence",
                   "fail_open_reason", "actual_choice", "outcome",
                   "verdict_json", "envelope_hash", "follow_verdict"]

    def fake_connect(db_path=""):
        conn = _FakeConn(store)
        # expose cols for ledger_recent shape
        conn.description = [(c,) for c in ledger_cols]
        return conn

    monkeypatch.setattr(decision, "_ledger_connect", fake_connect)
    monkeypatch.setattr(router_core, "pre_cooldown_seconds", lambda: 600)
    monkeypatch.setattr(router_core.state, "last_staged_consult",
                        lambda sid: (1.0, ""))  # staged 600s+ ago? no: 1.0 epoch
    before = decision.get_counter("pre_cooldown_suppressed_consult")
    router_core.emit_pre_cooldown_suppressed_consult(
        "sess-e3", "task-e3", 120)
    after = decision.get_counter("pre_cooldown_suppressed_consult")
    assert after == before + 1  # counted
    assert store.get("rows"), "suppressed consult must reach the ledger"
    row = store["rows"][-1]
    assert row[3] == "pre_cooldown"  # trigger column
    assert row[11] == "pre_cooldown_skip_consult"  # fail_open_reason column
    assert row[13] == "suppressed_consult"  # outcome column
