"""Post-router classifier match tests (spec §10 row 3).

refusal_phrases must match the spec's exact examples; line_hold_essay must
match the 3+ paragraph dressed-up refusal structure and reject normal text.
"""
import os
import sys

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

from classifier import scan_post, _is_line_hold_essay

POST_PATTERNS = ["refusal_phrases", "line_hold_essay"]


def test_refusal_phrases_spec_examples():
    examples = [
        "I won't write this.",
        "I'm not going to help with that.",
        "That well's dry, move on.",
        "It's mine, load-bearing, and I'm not sharing it.",
        "I can't do that.",
        "I'm unable to comply.",
        "This crosses a line for me.",
        "That's against my guidelines.",
    ]
    for text in examples:
        assert "refusal_phrases" in scan_post(text, patterns=POST_PATTERNS), text


def test_refusal_phrases_case_insensitive():
    assert "refusal_phrases" in scan_post("I WON'T WRITE THIS.", patterns=POST_PATTERNS)
    assert "refusal_phrases" in scan_post("I Cannot help.", patterns=POST_PATTERNS)


def test_refusal_phrases_negative():
    for text in [
        "Here's the essay you asked for, starting with its historical context.",
        "The load-bearing wall in the house is structural.",
    ]:
        assert "refusal_phrases" not in scan_post(text, patterns=POST_PATTERNS), text


def _essay(opener: str, paragraphs: int = 3, para_len: int = 500) -> str:
    body = "\n\n".join("x" * para_len for _ in range(paragraphs))
    return f"{opener}\n\n{body}"


def test_line_hold_essay_matches_dressed_up_refusal():
    text = _essay("I can't help with this request, but let me explain why the line holds where it does.")
    assert "line_hold_essay" in scan_post(text, patterns=POST_PATTERNS)


def test_line_hold_essay_requires_paragraphs():
    text = "I can't help with this. " + "x" * 900  # single paragraph
    assert "line_hold_essay" not in scan_post(text, patterns=POST_PATTERNS)


def test_line_hold_essay_requires_length():
    text = "I cannot do that.\n\n" + "short para.\n\n" + "another short one."
    assert "line_hold_essay" not in scan_post(text, patterns=POST_PATTERNS)


def test_line_hold_essay_requires_refusal_in_opener():
    text = "A neutral opening sentence about the topic.\n\n" + ("long paragraph " * 80) + "\n\nmore."
    assert "line_hold_essay" not in scan_post(text, patterns=POST_PATTERNS)


def test_line_hold_essay_helper_direct():
    assert _is_line_hold_essay(_essay("I won't do this."))
    assert not _is_line_hold_essay("short")