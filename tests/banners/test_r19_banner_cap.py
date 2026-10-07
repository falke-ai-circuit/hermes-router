"""R19.8: POST advisory banner cap per session (reviewer rollout condition)."""
from hermes_router import decision


def test_banner_cap_blocks_beyond_limit(monkeypatch):
    parked = []
    monkeypatch.setattr(decision, "_BANNER_COUNTS", {})
    monkeypatch.setattr(decision.render_envelope, "__call__",
                        lambda *a, **k: "ENV") if False else None
    import hermes_router.debug_banner as db

    class FakeBanner:
        @staticmethod
        def park_anchor_banner(sid, env, task_id=""):
            parked.append(sid)
    monkeypatch.setattr(db, "park_anchor_banner", FakeBanner.park_anchor_banner)
    monkeypatch.setattr(decision, "render_envelope", lambda v, f: "ENV")
    cfg = {"confidence_threshold": 0.60, "post_banner_cap_per_run": 2}
    verdict = {"confidence": 0.9, "decision": "apply_precedent", "precedents": []}
    frame = {}
    for _ in range(4):
        decision._deliver(verdict, "ok", frame, "sess-cap", "task", "text",
                          cfg, lambda *a, **k: None, sync=False)
    assert len(parked) == 2, parked
