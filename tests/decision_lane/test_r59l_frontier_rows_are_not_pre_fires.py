"""R59L: the ledger must not report the frontier lane as decision pre-fires.

Found live while confirming the single-seam gate. One turn on coder under
seam=midturn_prellm produced exactly what the gate promises -- midturn fired,
``decision_suppressed reason=seam_midturn_only`` logged for both pre and post --
and the ledger STILL gained three ``pre_fork`` rows, because they were frontier
accounting (fork_class frontier_consult / frontier_post / frontier_adversarial_
parse_fail) falling through ``trigger_kind``'s ``pre_fork`` default.

That default is why the fleet's 3,583 coder pre_fork rows are ~69% frontier
accounting: the column was never naming the writer, it was naming a default.
"""
import sys

sys.path.insert(0, "/opt/data/plugins")

from hermes_router import decision  # noqa: E402


def test_frontier_triggers_are_named_not_defaulted():
    assert decision.trigger_kind("frontier_consult") == "frontier_consult"
    assert decision.trigger_kind("frontier_post") == "frontier_post"
    assert decision.trigger_kind(
        "frontier_adversarial_parse_fail") == "frontier_adversarial_parse_fail"
    assert decision.trigger_kind("pre_cooldown") == "pre_cooldown"


def test_decision_triggers_keep_their_kinds():
    """The fix must not disturb the decision lane's own labels."""
    assert decision.trigger_kind("pre") == "pre_fork"
    assert decision.trigger_kind("post") == "post_fork_scan"
    assert decision.trigger_kind("manual") == "on_demand"
    assert decision.trigger_kind("midturn_hook") == "midturn_hook"


def test_frontier_rows_are_not_decision_forks():
    for t in ("frontier_consult", "frontier_post",
              "frontier_adversarial_parse_fail"):
        assert decision.is_decision_fork(t) is False
        # and a suffixed variant must not sneak in via the _fork rule
        assert decision.is_decision_fork(t + "_fork") is False


def test_pre_fork_denominator_no_longer_contains_frontier():
    """The measurement consequence: 'pre_fork' means the decision lane fired."""
    kinds = {decision.trigger_kind(t) for t in
             ("frontier_consult", "frontier_post", "frontier_adversarial_parse_fail")}
    assert "pre_fork" not in kinds
