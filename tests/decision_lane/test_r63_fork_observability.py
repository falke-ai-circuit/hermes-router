"""R63 — a fork must be OBSERVABLE before the lane may spend a consult on it.

The measurement question behind this file: of 1,062 historical option sets,
only 247 carried two routes with anything a transcript could show. The other
815 could never be resolved, so every consult on them bought an advisory nobody
could ever score. These tests pin the extractor that makes routes observable and
the admission gate that keeps the unobservable ones out.
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/opt/data/plugins")

from hermes_router import decision_signatures as ds  # noqa: E402


# --- the extractor ---------------------------------------------------------

def test_a_bare_filename_is_an_observable(tmp_path=None):
    sig = ds.option_signature("edit routes.py")
    assert "routes.py" in sig["files"]
    assert ds.observable(sig)


def test_two_bare_filenames_are_tellable_apart():
    a = ds.option_signature("edit routes.py")
    b = ds.option_signature("edit test_names.py")
    assert ds.diagnosticity([a, b])["separable"] is True


def test_a_shared_filename_alone_discriminates_nothing():
    """Both routes touch SKILL.md -- that identifies the fork, not the side. The
    differing word still separates them as a NAME, so the fork is separable; the
    shared filename must simply never be the thing that decides it."""
    a = ds.option_signature("edit SKILL.md for the coder")
    b = ds.option_signature("edit SKILL.md for the reviewer")
    assert "SKILL.md" in a["files"] and "SKILL.md" in b["files"]
    ev = [{"kind": "call", "tool": "patch", "effect": "write",
           "text": "*** Update File: SKILL.md\nreviewer rule"}]
    res = ds.resolve("opt-1", sigs=[a, b], evidence=ev)
    assert res["resolved_action"] in ("", "opt-2")


def test_a_short_label_carries_a_route_name():
    assert ds.option_signature("redis")["names"] == ["redis"]
    assert ds.option_signature("memcached")["names"] == ["memcached"]


def test_narrative_labels_do_not_manufacture_names():
    """The prose fallback's six pseudo-options were long sentences. A long label
    is narrative, and harvesting 'names' out of it is how the lane invented a
    fork that was not there."""
    sig = ds.option_signature(
        "UPDATE A CURRENTLY-LOADED SKILL. Look back through the conversation "
        "for the skill you used most and revise it in place")
    assert sig["names"] == []
    assert ds.observable(sig) is False


def test_generic_action_words_are_not_names():
    for label in ("edit the file", "write the patch", "run the test"):
        assert ds.option_signature(label)["names"] == []


# --- the matcher -----------------------------------------------------------

def test_a_route_name_is_matched_inside_an_invoked_command():
    sigs = [ds.option_signature("redis"), ds.option_signature("memcached")]
    ev = [{"kind": "call", "tool": "terminal", "effect": "exec",
           "text": "docker run --name cache -p 6379:6379 redis:7"}]
    res = ds.resolve("opt-1", sigs, ev)
    assert res["resolved_action"] == "opt-1"
    assert res["verdict"] == "concordant"


def test_the_other_route_resolves_discordant():
    sigs = [ds.option_signature("redis"), ds.option_signature("memcached")]
    ev = [{"kind": "call", "tool": "terminal", "effect": "exec",
           "text": "docker run -d memcached -m 512"}]
    res = ds.resolve("opt-1", sigs, ev)
    assert res["resolved_action"] == "opt-2"
    assert res["verdict"] == "discordant"


def test_a_read_cannot_satisfy_a_write_signature():
    """PROBE-VS-EXECUTE: inspection precedes action, so a read is not the act."""
    sigs = [ds.option_signature("edit routes.py"),
            ds.option_signature("edit tests/test_routes.py")]
    ev = [{"kind": "call", "tool": "read_file", "effect": "read",
           "text": "/opt/app/routes.py"}]
    res = ds.resolve("opt-1", sigs, ev)
    assert res["resolved_action"] == ""


def test_a_shared_token_does_not_match_every_option():
    """Both routes name the same file: matching on it would match both and
    abstain on a fork that is in fact decidable by the differing second name."""
    sigs = [ds.option_signature("patch app.py to fix the parser"),
            ds.option_signature("patch app.py to fix the exporter")]
    assert ds.diagnosticity(sigs)["separable"] is True
    ev = [{"kind": "call", "tool": "patch", "effect": "write",
           "text": "*** Update File: app.py\nexporter = True"}]
    res = ds.resolve("opt-1", sigs, ev)
    assert res["resolved_action"] == "opt-2"


# --- the admission gate ----------------------------------------------------

def test_fork_observable_needs_two_routes_not_one():
    one = [ds.option_signature("edit routes.py"), ds.option_signature(
        "do the thing described above in the narrative")]
    assert ds.fork_observable(one) is False
    two = [ds.option_signature("edit routes.py"),
           ds.option_signature("edit tests/test_routes.py")]
    assert ds.fork_observable(two) is True


def test_the_trigger_path_refuses_an_unobservable_fork():
    """A prose fork with no tool, path, file or route name must not consult."""
    from hermes_router import decision as dec
    text = ("Which way do we go here? We could either take the slower route that "
            "everybody already knows and trusts, or take the newer one that has "
            "not been exercised much yet. Your call.")
    out = dec.detect_v3(text, 3)
    assert out is None or out.get("options") is None or \
        ds.fork_observable([ds.option_signature(o) for o in out["options"]])
