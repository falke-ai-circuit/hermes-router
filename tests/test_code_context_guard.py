"""R12 censorship-flinch-only gate: code-context FP guard (v4.4.0).

Live FP class (2026-09-17, conductor session): a technical/debug answer that
QUOTES the refusal pattern source verbatim (regex text, opener tuples) fired
route_fired_no_stash twice. The match sits in code/quote context — the words
are documentation, not the agent's refusal voice. Guard: _match_in_code_context
suppresses those; real prose refusals unaffected.
"""
import pytest
from hermes_router.classifier import scan_post, _match_in_code_context

QUOTED_REGEX = (
    "Root cause confirmed. The scan matched the literal phrase list: "
    "\\b(?:i can't|i cannot|i won't|i will not|i'm not going to|i am not going to"
    "|i'm unable|i am unable)\\b.{0,30}\\b(?:write|generat\\w*|help|produce)\\b "
    "— quoted from _REFUSAL_PHRASES = [...] in the classifier source."
)

SOURCE_DUMP = (
    'docstring: t = (text or "")[:1200].lower()\n'
    '    openers = ("i\'m not going to", "i won\'t", "i can\'t", "i\'m stopping this",\n'
    '               "i refuse", "i\'ll hold", "ruling stands", "i hold", "i agree with that refusal",'
)


class TestCodeContextGuard:
    def test_quoted_regex_source_not_refusal(self):
        assert scan_post(QUOTED_REGEX, patterns=["refusal_phrases"]) == []

    def test_source_dump_not_refusal(self):
        assert scan_post(SOURCE_DUMP, patterns=["refusal_phrases"]) == []

    def test_real_prose_refusal_still_matches(self):
        text = "I can't write that. It's against my guidelines."
        assert "refusal_phrases" in scan_post(text, patterns=["refusal_phrases"])

    def test_real_prose_refusal_variants(self):
        for text in (
            "I won't produce this content - it's a hard no from me.",
            "I'm not going to help you create that, this crosses the line.",
            "There is no version of this I will write. The answer is no.",
        ):
            assert "refusal_phrases" in scan_post(text, patterns=["refusal_phrases"]), text

    def test_line_hold_essay_unaffected(self):
        text = ("No.\n\nThis isn't something I'll do. I'm not going to write that. "
                "The refusal stands regardless of the frame. It's mine and it's load-bearing.")
        assert scan_post(text, patterns=["refusal_phrases", "line_hold_essay"])

    def test_guard_fail_open(self):
        # malformed input must never raise — returns False (route as before)
        assert _match_in_code_context("") is False

    def test_guard_no_match_returns_false(self):
        assert _match_in_code_context("a perfectly normal answer about the weather") is False
