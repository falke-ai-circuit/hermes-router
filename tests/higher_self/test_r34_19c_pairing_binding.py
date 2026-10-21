"""R34-19c: the ask fingerprint is advisory, not absolute.

Live-caught root cause of the missing confirmation: the PRE stores the original
user ask, the POST seam resolves its ask from a different source, and strict
fingerprint equality rejected the record the PRE had just written. Evidence:
paired=True stored_chars=3158 store_id=<same> store_len=1 has_key=True on both
ends of the same turn, with _pair None at the gate.

Session + TTL + one-shot consumption is the real boundary; a fingerprint
mismatch is logged (pair_sig_mismatch_accepted), not fatal.
"""
from hermes_router.core import state as st
from hermes_router import completion_audit as ca


def setup_function(_fn):
    st._PRE_ADVISORY.clear()


def test_strict_match_pairs():
    st.mark_pre_advisory("S1", "orientation", "brief text", ask="the real ask")
    assert ca._pre_pairing("S1", "the real ask")


def test_mismatched_ask_still_pairs_and_is_logged():
    rows = []
    st.mark_pre_advisory("S2", "orientation", "brief text", ask="the real ask")
    import hermes_router.completion_audit as self_mod
    orig = self_mod._log
    self_mod._log = lambda m, **kw: rows.append((m, kw))
    try:
        rec = ca._pre_pairing("S2", "a DIFFERENT ask resolved at the post seam")
    finally:
        self_mod._log = orig
    assert rec is not None, "the stored pair must not be lost to a sig mismatch"
    assert any(m == "pair_sig_mismatch_accepted" for m, _ in rows)


def test_no_record_stays_unpaired():
    assert ca._pre_pairing("S3", "anything") is None


def test_empty_ask_does_not_loosen_into_a_pair():
    # an empty ask must not conjure a pairing out of thin air
    assert ca._pre_pairing("S4", "") is None
