"""M47-21 — evidence-preserving tool-body condensation.

Why this exists: head+tail elision cut consult input 92% and LOST the mid-context
fact in 3 of 3 measured reps (rubric 3/4 vs 4/4). Evidence is not at the edges of
a tool result. Line-aware selection that keeps state-changing lines and drops
no-change noise held 4/4 at -95% input (10,871 -> 568 tokens), reasoning_effort
left at max.

These pins protect the properties that made that true, and the escape hatch.
"""
from hermes_router import anchor_exec as ax

NOISE = "".join(
    f"2026-11-14T02:{10 + i // 60:02d}:{i % 60:02d}Z heartbeat shard={i % 7} "
    f"rss_mb={600 + i} queue={i % 13} note=no_change\n" for i in range(200))
BODY = (
    "2026-11-14T02:10:01Z deploy start target=fleet attempt=7\n"
    "2026-11-14T02:10:04Z WARN plugin_loader duplicate_install_dir=router.old-20261020\n"
    "2026-11-14T02:10:12Z verify FAIL expected=6.17.9 reported=5.0.1 affected=11/11\n"
    + NOISE +
    "2026-11-14T02:14:02Z ROOTCAUSE stale_plugin_dir_shadows_deployed_copy\n"
    + NOISE +
    "2026-11-14T02:30:02Z verify PASS reported=6.17.9 affected=11/11\n"
)


def test_mid_context_evidence_survives():
    """The fact head+tail elision throws away must survive selection."""
    out = ax._condense_tool_body(BODY, 1200, 600)
    assert "stale_plugin_dir_shadows_deployed_copy" in out


def test_head_and_tail_framing_survive():
    out = ax._condense_tool_body(BODY, 1200, 600)
    assert "deploy start target=fleet" in out
    assert "verify PASS reported=6.17.9" in out


def test_no_change_noise_is_dropped():
    out = ax._condense_tool_body(BODY, 1200, 600)
    assert out.count("note=no_change") < BODY.count("note=no_change")
    assert len(out) < len(BODY) / 2


def test_a_dropped_count_is_reported_not_silent():
    """A silent cut is indistinguishable from a complete body to the reader."""
    out = ax._condense_tool_body(BODY, 1200, 600)
    assert "dropped from tool result" in out


def test_non_line_structured_bodies_fall_back_to_positional():
    """A single long line (diff/JSON) must not be shredded by line selection."""
    blob = "x" * 50000
    out = ax._condense_tool_body(blob, 1200, 600)
    assert out.startswith("x" * 1200)
    assert "chars elided" in out


def test_short_bodies_are_untouched():
    short = "line one\nline two"
    assert ax._condense_tool_body(short, 1200, 600) == short


def test_positional_mode_restores_the_old_behaviour(monkeypatch):
    monkeypatch.setattr(ax, "_tool_trim_mode", lambda: "positional")
    out = ax._condense_tool_body(BODY, 1200, 600)
    assert "stale_plugin_dir_shadows_deployed_copy" not in out
    assert "chars elided" in out
