"""R36m: the hand-back branch must reach the persisted row, like the strip branch.

r34-19l gave `strip_keep_work` a persist reconcile. The hand-back branch returns
a REPLACEMENT body and skips the rest of the seam, so nothing wrote that body to
the transcript. Live: sessions api_1792643928_e46a2731 / api_1792644362_722dd41f
ended with a single 0-char assistant row, no canonical_committed, no
render_swap_decision — while every clean turn in the same window had both.
"""
import pathlib
import re

SRC = pathlib.Path(__file__).resolve().parents[2]


def _handback_block() -> str:
    text = (SRC / "gate" / "orchestration.py").read_text()
    i = text.index("handback_failure / withhold_echo replace the body.")
    # 8000, not 6000: the branch grew when echo-withhold recovery added the
    # one-shot re-prompt ahead of the persist. The window must cover the whole
    # branch or the pins silently stop seeing the reconcile they exist to guard.
    return text[i:i + 8000]


def test_handback_reconciles_the_persisted_row_before_returning():
    block = _handback_block()
    assert "rewrite_persisted_turn" in block
    # it must run BEFORE the replacement body is returned. The returned value is
    # `_deliver_body` since echo-withhold recovery landed (the seam may deliver
    # the re-prompted second body instead of the guard's notice); the invariant
    # is unchanged — the row is reconciled against whatever body is delivered.
    assert "_deliver_body = _echo.body" in block
    assert block.index("rewrite_persisted_turn") < block.index("return _deliver_body")
    # and the persisted target is the delivered body, never the guard's notice
    assert "_seam_entry_text, _deliver_body" in block


def test_handback_verifies_against_the_delivered_text_not_the_row_it_wrote():
    block = _handback_block()
    assert "verify_persisted_turn" in block
    assert "handback_delivery_check" in block


def test_handback_persist_failures_are_named_not_silent():
    block = _handback_block()
    assert "handback_persist_miss" in block
    assert "handback_persist_reconciled" in block


def test_handback_path_is_fail_open():
    """A persist failure may never stop the body from being delivered."""
    block = _handback_block()
    # every reconcile attempt sits inside its own except
    for m in re.finditer(r"from \.\.core import canonical", block):
        tail = block[m.start():m.start() + 3000]
        assert "except Exception" in tail
