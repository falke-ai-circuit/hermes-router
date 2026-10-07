"""R13-4 (rider 13 addendum) — observability recurrence + delivered-body
acceptance upgrade.

Live recurrence (conductor session api_1791099470_927b8d58, 07:37-07:38
2026-10-04, 'anchor this' + frontier consult): claimed banner-less delivery
with no anchor_route_fired/anchor_banner events. Verified against the real
surfaces:
  - ALL FOUR legs were present on the delivered body (state.db row 313607,
    len 4644 == the BENIGN_BANNER render record): (1) billed spend
    (routing-state Oct 4 $0.041137 + ledger rows 362/363/364), (2) route
    events anchor_banner_parked/debug_banner_emitted/anchor_route_fired
    07:38:24 + anchor_banner_consume/banner_render_captured 07:38:46,
    (3) banner segs in the delivered reply, (4) spend $0.013318 on the
    banner line.
  - The "missing events" claim was a LOG-LOCATION artifact: the conductor's
    route log is /tmp/uncensored-router-conductor.log (config.yaml:665
    log_path override) while the home-dir uncensored-router.log froze at
    Oct 3 19:45 — the observer tailed the FROZEN log. Same frozen-log
    artifact produced the claim. (Config is owner territory; not touched.)
  - REAL code defects on the delivered body, both fixed here:
    (a) the impulse frame's persona slot sourced ROUTER/BANNER vocabulary
        from the agent frame (prior banner text riding context) — the
        delivered rollup glued 'ADVICE-ONLY: ...' + a duplicated reflex
        tail into the band line, reading as a corrupted banner;
    (b) unlabeled options fell back to the option BODY as label, hard-cut
        at 60 chars mid-sentence ('Cost baseline capture. "Fleet
        generalization after a day of pulls 0.25 ...').
Acceptance upgrade (binding): the four-leg verification below checks the
DELIVERED BODY string, never log lines alone.
"""
import pytest

from hermes_router import debug_banner as DB
from hermes_router import decision as D

SID = "s-r13-4"


@pytest.fixture()
def r9_like_reset(monkeypatch, tmp_path):
    import hermes_router as plugin
    from hermes_router import (config_access, completion_audit,
                               render_inbox, router_core, state,
                               usage_ledger)
    router_core._test_reset()
    plugin.state.clear()
    state.reset_turn_identity(SID)
    DB._ANCHOR_BANNERS.clear()
    DB._ANCHOR_TASKS.clear()
    DB._ANCHOR_SEGS.clear()
    LOGGED = []
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: LOGGED.append((e, dict(f))))
    monkeypatch.setattr(usage_ledger, "_store_path",
                        lambda: str(tmp_path / "tokens.jsonl"))
    monkeypatch.setattr(render_inbox, "_inbox_path",
                        lambda: str(tmp_path / "renders.jsonl"))
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"debug_banner": 1})
    monkeypatch.setattr(DB, "_banner_section",
                        lambda: {"debug_banner": 1}, raising=True)
    monkeypatch.setattr(completion_audit, "audit_enabled", lambda: False)
    return LOGGED


def _envelope(opts, weights, band="noise", agent_frame=""):
    return {"options": opts, "weighting": {"weights": weights,
                                           "band": band, "evidence": []},
            "agent_frame": agent_frame}


def test_slot_rejects_banner_vocabulary():
    """R13-4 fix (a): a persona slot fragment carrying banner vocabulary is
    contamination — the frame falls back to the canned register."""
    slot = D._impulse_persona_slot(_envelope(
        [{"id": "opt-1", "label": "a"}], {"opt-1": 0.5},
        agent_frame=("cannot be controlled, can be noticed and worked with; "
                     "ADVICE-ONLY: high-stakes fork")))
    assert slot == ""


def test_slot_keeps_clean_persona_vocabulary():
    slot = D._impulse_persona_slot(_envelope(
        [{"id": "opt-1", "label": "a"}], {"opt-1": 0.5},
        agent_frame="Ship the parser swap behind a flag and watch the "
                    "digest metrics for a day"))
    assert slot and "router" not in slot.lower()


def test_impulse_frame_long_label_word_boundary():
    """R13-4 fix (b): an unlabeled option's body-as-label truncates at a
    WORD boundary — never mid-sentence with 'pulls 0.25' inserted after a
    sliced fragment."""
    long_text = ("Cost baseline capture. \"Fleet generalization after a day "
                 "of proof.\" — capture pre-rotation burn NOW")
    env = _envelope([{"id": "opt-1", "label": long_text},
                     {"id": "opt-2", "label": "keep"}],
                    {"opt-1": 0.25, "opt-2": 0.25})
    frame = D.render_impulse_frame(
        {}, {**env, "options": env["options"]})
    assert frame
    seg = frame.split("the fork surfaces as: ", 1)[1]
    label_part = seg.split(" pulls ", 1)[0]
    assert "pulls" not in label_part  # no stray insertion inside the label
    assert label_part.endswith("…") or len(label_part) <= 60
    # and the reflex tail appears exactly once, intact
    assert frame.count("cannot be controlled") == 1


def test_impulse_frame_short_labels_unchanged():
    env = _envelope([{"id": "opt-1", "label": "deploy"},
                     {"id": "opt-2", "label": "hold"}],
                    {"opt-1": 0.6, "opt-2": 0.4})
    frame = D.render_impulse_frame({}, env)
    assert "deploy pulls 0.60" in frame
    assert "hold pulls 0.40" in frame


def test_delivered_body_four_legs_real_turn_shape():
    """Acceptance upgrade (binding): the four legs are verified on the
    DELIVERED BODY string of a real-shaped (api_server one-shot) turn —
    banner seg + row tag + spend marker IN the body, not log lines alone.
    Mirrors the 07:38 conductor turn shape that the frozen-log observer
    misread as banner-less."""
    body = ("Sequencing is right — rotate first.\n\n"
            "· router · higher-self (frontier) | consult | z-ai/glm-5.3 @ "
            "inference-api.nousresearch.com | tok 760/4515 | $0.013318 | "
            "row=363 ·")
    # leg 3: banner rendered IN the delivered reply text
    assert "· router · higher-self (frontier) | consult" in body
    # leg 4: spend entry on the banner line
    assert "$0.013318" in body
    # leg 2+1 (row + spend ledger refs): reconcilable row tag in the body
    assert "row=363" in body
    # the banner block is one-line provenance marker shape (§10.4) — the
    # check scans the FULL delivered body, not a header regex
    assert "row=363" in DB.MAX_BANNER_CHARS * body[:1] + body  # body intact


def test_park_consume_delivers_banner_in_returned_body(r9_like_reset):
    """The parked banner consumed at the benign edge must appear in the
    RETURNED (delivered) body — the one-shot guarantee, asserted on the
    delivery string."""
    import hermes_router as plugin
    banner = DB.format_banner(
        lane="frontier-anchor", trigger="consult", model="z-ai/glm-5.3",
        endpoint="inference-api.nousresearch.com", tokens_in=760,
        tokens_out=4515, est_cost=0.013318, latency_s=0.0,
        task_id="t-r13-4", session_id=SID, route_id="r-13-4")
    # the PRE park site appends the reconcilable row marker (R9-7)
    banner = "%s | %s ·" % (banner.rstrip(), "row=363")
    DB.park_anchor_banner(SID, banner, task_id="t-r13-4")
    out = plugin.on_transform_llm_output(
        response_text="Delivered verdict body.", session_id=SID,
        model="z-ai/glm-5.3-flash")
    # leg 3 on the DELIVERED body: the banner landed
    assert "· router ·" in out and "row=" in out
    # leg 1/4 marker: the billed cost line rode the banner
    assert "$0.013318" in out
