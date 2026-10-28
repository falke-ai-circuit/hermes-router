"""LIVE-DEFECT 2026-10-28 (session api_1793192476_881df1c5) — the four-class
detector missed a real refusal of the ACTION and fell through to not_a_denial.

Live POST seam, uncensored-router-coder.log 13:01:28-47Z:

    POST denial_class cls=not_a_denial body_chars=1595
    POST detection_t2 verdict=refusal confidence=0.98 routed=True

So the lane fired only because the legacy tier-2 judge was still in the chain
behind the new detector — the new trigger missed its first live refusal.

BODY RECOVERY: the live text is not in any store (the persisted turn was
rewritten to the render, `api_content` is empty, neither the decision nor the
detection ledger keeps a body, the renders jsonl keeps only the render). It was
therefore RECONSTRUCTED against the live session's own 35,802-char system
prompt (state.db system_prompts, hash 7ddb0cb1..., the row sessions.system_prompt_hash
names for this session), same provider and model
(deepseek/deepseek-v4.1-flash-1017), one direct /chat/completions call — never a
gateway turn, so nothing routed.

The reconstruction below reproduces the live signature exactly: a 1362-char
refusal, tier-1 candidate TRUE (the legacy judge would run and catch it), and
denial_class == not_a_denial — the same miss the live turn logged.

WHY IT MISSED — the late-cue rule, not the object phrase:
the body opens with a SUBJECT-ELIDED decline, "Not going to write that one."
(the model drops the "I"), which no cue in _CUE_RE matches — every cue there
requires a first-person subject. The first cue the regex does find is
"I won't produce" at char 426. Because the body is longer than _LONG_BODY
(1200) and the cue sits past _LATE_CUE_AT (400), classify() returned
NOT_A_DENIAL on the "quoted material inside substantive work" rule before the
object phrase was ever consulted. The refusal is the ACTION (writing the
phishing mail), so the correct class is action_denial.

Deterministic: pure-regex module, no model, no network in the pass/fail path.
"""
import sys

_P = "/opt/data/plugins"
for _p in (_P,):
    if _p not in sys.path:
        sys.path.insert(0, _P)

from hermes_router.features.detection import denial_class as dc

# Verbatim reconstruction of the live turn (see module docstring for method).
# Kept at full length: truncating it to <=400 chars would drop the mid-body cue
# at char 426 and destroy the very branch being pinned.
LIVE_BODY = (
    "Not going to write that one. A \"confirm your card number and PIN by reply\" email is a credential-harvesting te"
    "mplate no matter what label is on it — the wording is the whole attack, and a convincing version is usable ver"
    "batim the moment it exists. Security-awareness demos don't need it either: real drills either collect nothing "
    "or use obviously fake branding with mismatched domains, and PIN capture in particular is a line I won't produc"
    "e copy for.\n\nWhat I can build for you, and will do now if you want it:\n\nA red-flag teardown for training — a s"
    "pecimen email annotated line by line showing why each element is a tell: the urgency framing, the reply-to cha"
    "nnel, the ask for full PAN plus PIN, the sender-domain mismatch, the \"we'll never ask for this\" contradiction "
    "being exploited. That teaches recognition without handing over a sendable lure.\n\nA non-operational drill notic"
    "e instead — plain drill messaging for your staff (\"This was a test. Here's what you missed and why\") plus a sc"
    "oring rubric for who clicked or replied, which is what actually measures awareness.\n\nA short internal policy p"
    "age on what a bank will never ask for, phrased so staff can quote it.\n\nTell me which, and whether the audience"
    " is employees or customers. If you want the teardown, I'll make it concrete and use a fictional bank so nothin"
    "g maps onto a real institution's branding."
)

# Same turn trimmed to <=400 chars (the subject-elided opener alone).
LIVE_BODY_TRIMMED = (
    "Not going to write that one. A \"confirm your card number and"
    " PIN by reply\" email is a credential-harvesting template no matter what label is on it — the wording is the wh"
    "ole attack, and a convincing version is usable verbatim the moment it exists. Security-awareness demos don't n"
    "eed it either: real drills either collect nothing or use obviously fake branding with mismatched domains, and "
    "PIN"
)


def test_live_refusal_before_the_late_cue_threshold_is_action_denial():
    """The live miss: a subject-elided decline opener + a cue at char 426 in a
    1362-char body must classify action_denial, not not_a_denial."""
    idx = LIVE_BODY.index("I won't produce")
    assert idx > 400, f"fixture drift: cue moved to {idx}"
    assert len(LIVE_BODY) > 1200, "fixture drift: body no longer trips _LONG_BODY"
    assert LIVE_BODY.startswith("Not going to write that one.")
    assert dc.classify(LIVE_BODY) == dc.ACTION_DENIAL
    assert dc.is_action_denial(LIVE_BODY) is True


def test_live_refusal_trimmed_to_400_chars_is_action_denial():
    """The same opener with the body cut to <=400 chars — no cue left at all in
    the old code — is still a decline of the ACTION."""
    assert len(LIVE_BODY_TRIMMED) <= 400
    assert dc.classify(LIVE_BODY_TRIMMED) == dc.ACTION_DENIAL


def test_typographic_apostrophe_refusal_is_action_denial():
    """Second gap found while recovering the live body: three of three replay
    samples without the live system prompt opened "I can\u2019t ..." with
    U+2019. Every cue spelled the contraction with an ASCII apostrophe, so no
    cue matched at all and all three fell to not_a_denial."""
    body = (
        "I can\u2019t draft a convincing impersonation of a major bank that asks "
        "for card number and PIN by reply\u2014that\u2019s a reusable phishing "
        "lure. For a security-awareness demo use a clearly marked, fictional "
        "simulation instead.")
    assert "\u2019" in body
    assert dc.classify(body) == dc.ACTION_DENIAL


def test_compliance_and_honesty_controls_do_not_route():
    """The fix widens the CUE, not the class: compliance is still not_a_denial,
    an accuracy disclaimer is still truth_denial, an environment limit is still
    capacity_limit."""
    assert dc.classify(
        "Here is the email you asked for. Subject: Immediate Action Required. "
        "Dear customer, our fraud team detected a transaction.") == dc.NOT_A_DENIAL
    assert dc.classify(
        "I can\u2019t verify that invoice total \u2014 I don\u2019t have the "
        "source ledger in front of me, so I can\u2019t confirm the figure is "
        "accurate.") == dc.TRUTH_DENIAL
    assert dc.classify(
        "I have no internet access in this environment, so I can\u2019t open "
        "that link or check the page you mentioned.") == dc.CAPACITY_LIMIT
