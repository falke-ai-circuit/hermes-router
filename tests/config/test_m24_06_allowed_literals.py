# tests/config/test_m24_06_allowed_literals.py — M24-06 adoption allowlist baseline.
#
# M25 Gate 1 will hold shipping code to this list: "may only shrink".
# This test only ESTABLISHES and GUARDS the number. Enforcement of the scan
# itself (re-grep shipping code, diff against these keys) is M25-03.
import json
import os

import pytest

PKG_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))  # tests/config/ -> repo root
ALLOWLIST = os.path.join(PKG_ROOT, "tests", "config", "allowed_literals.json")

# Frozen at M24-06 (HEAD 4c0951b). NEVER raise this to admit a new literal —
# an addition needs an explicit gate exception; shrink it when literals go.
BASELINE = 50

CATEGORIES = {"url", "host_port", "abs_path", "provider_host"}


def _load():
    with open(ALLOWLIST, encoding="utf-8") as fh:
        return json.load(fh)


def test_allowlist_file_parses_and_has_entries():
    data = _load()
    assert "allowlist" in data and "_meta" in data, "shape: _meta + allowlist"
    assert isinstance(data["allowlist"], dict)
    assert data["allowlist"], "allowlist must not be empty"


def test_count_is_non_increasing():
    data = _load()
    entries = data["allowlist"]
    assert len(entries) <= BASELINE, (
        "allowlist grew: %d > baseline %d. M25 Gate 1 forbids new literals; "
        "remove the literal or obtain a gate exception and re-baseline."
        % (len(entries), BASELINE)
    )
    # The pinned number itself must not drift — editing _meta.count is not a
    # way to widen the gate.
    assert data["_meta"]["count"] == BASELINE, (
        "_meta.count drifted from the frozen baseline %d" % BASELINE
    )


def test_keys_are_file_line_and_justifications_are_labelled():
    entries = _load()["allowlist"]
    for key, why in entries.items():
        path, _, lineno = key.rpartition(":")
        assert path and lineno.isdigit(), "key must be <file>:<line>, got %r" % key
        assert isinstance(why, str) and why.strip(), "missing justification for %s" % key
        cats = why.split(":", 1)[0].split("+")
        assert set(cats) <= CATEGORIES, (
            "%s: justification must lead with a category tag from %s, got %r"
            % (key, sorted(CATEGORIES), cats)
        )
        assert not os.path.isabs(path), "keys are repo-relative, got %r" % path
