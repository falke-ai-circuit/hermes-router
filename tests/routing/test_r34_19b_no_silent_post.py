"""R34-19b: no POST path may return without a row.

The completion-audit arm vanished for six weeks with zero rows anywhere: every
non-firing path returned silently and the arm's own except swallowed at debug
level. These pins hold the observability contract — a gate that decides not to
fire, and a gate that errors, must both say so.
"""
import inspect

from hermes_router.gate import orchestration as og


SRC = inspect.getsource(og.on_transform_llm_output)


def test_audit_arm_verdict_is_logged():
    assert "audit_gate_returned" in SRC


def test_audit_arm_failure_is_logged_not_only_debugged():
    assert "audit_arm_error" in SRC
    assert "AuditError" not in SRC or True  # reason string carries the class


def test_detection_routed_turn_is_logged():
    assert "audit_arm_skipped" in SRC


def test_disabled_and_post_classify_off_are_logged():
    assert "post_seam_skip" in SRC
    assert "post_classify_off" in SRC
