# tests/test_p4_schema_budgets.py — P4 guard tests (proposal §2.3/§2.4).
#
# (a) no `from hermes_cli.config import load_config` outside config_access.py
#     (+ api/config_writer.py: the WRITER surface reads/writes the whole
#     config file, not a section read — documented deviation)
# (b) no literal "max_tokens": <int> outside core/budgets.py + tests
#     (+ api/router_tools.py doctor-ping 16: behavior-neutral carve-out)
# (c) every dotted path literal read via config_access.get()/sub_block()
#     exists in SCHEMA (AST probe)
# (d) no legacy-section reads outside the writer migration surface
import ast
import os
import re

import pytest

PKG_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))  # tests/config/ -> repo root


def _py_files():
    for root, dirs, files in os.walk(PKG_ROOT):
        dirs[:] = [d for d in dirs if d not in ("tests", "__pycache__", ".git", "node_modules")]
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(root, f)


def _rel(p):
    return os.path.relpath(p, PKG_ROOT)


def test_a_no_load_config_outside_config_access():
    hits = []
    for path in _py_files():
        src = open(path, encoding="utf-8").read()
        if "from hermes_cli.config import load_config" in src:
            hits.append(_rel(path))
    assert sorted(hits) == ["api/config_writer.py", "core/config_access.py"]


def test_b_no_max_tokens_literals_outside_budgets():
    pat = re.compile(r'"max_tokens"\s*:\s*\d')
    hits = []
    for path in _py_files():
        rel = _rel(path)
        if rel == "core/budgets.py" or rel.startswith("tests/"):
            continue
        for i, line in enumerate(open(path, encoding="utf-8"), 1):
            if pat.search(line):
                hits.append("%s:%d" % (rel, i))
    assert sorted(hits) == ["api/router_tools.py:434"], (
        "bare max_tokens literals outside core/budgets.py: %s" % hits)


def test_c_every_get_path_in_schema():
    from hermes_router.core import schema

    known = {k.path for k in schema.SCHEMA}
    # sub_block literal names must be registered (top-level dict keys)
    block_pat = re.compile(r'sub_block(?:_alias)?\(\s*["\']([a-z0-9_.]+)["\']')
    get_pat = re.compile(r'config_access\.get\(\s*["\']([a-z0-9_.]+)["\']')
    unknown = []
    for path in _py_files():
        rel = _rel(path)
        if rel.startswith("tests/"):
            continue
        src = open(path, encoding="utf-8").read()
        for pat in (block_pat, get_pat):
            for m in pat.finditer(src):
                dotted = m.group(1)
                if dotted not in known:
                    unknown.append("%s: %s" % (rel, dotted))
    assert not unknown, "config paths not in SCHEMA: %s" % sorted(unknown)


def test_d_no_legacy_section_reads_outside_writer():
    pat = re.compile(r'\.get\(\s*["\']uncensored_router["\']')
    hits = []
    for path in _py_files():
        rel = _rel(path)
        if rel.startswith("api/"):
            continue  # writer migration surface (documented deviation)
        for i, line in enumerate(open(path, encoding="utf-8"), 1):
            if pat.search(line):
                hits.append("%s:%d" % (rel, i))
    assert not hits, "legacy section reads: %s" % hits


def test_budget_defaults_match_current_literals():
    from hermes_router.core import budgets

    assert budgets.PROFILES["consult"]["max_tokens"] == 512
    assert budgets.PROFILES["verdict"]["max_tokens"] == 512
    assert budgets.PROFILES["completion_audit"]["max_tokens"] == 12000
    assert budgets.PROFILES["render"]["max_tokens"] == 2048
    assert budgets.PROFILES["probe"]["max_tokens"] == 256


def test_budget_is_probe_replaces_heuristic():
    from hermes_router.core import budgets

    assert budgets.is_probe(256) is True
    assert budgets.is_probe(499) is True
    assert budgets.is_probe(500) is False
    assert budgets.is_probe(512) is False
    assert budgets.is_probe(None) is False
    assert budgets.is_probe("256") is False
    assert budgets.is_probe(True) is False


def test_budget_config_overlay(tmp_path, monkeypatch):
    from hermes_router.core import budgets
    from hermes_router.core import config_access

    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"budgets": {"consult": {"max_tokens": 777}}})
    assert budgets.budget("consult")["max_tokens"] == 777
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    assert budgets.budget("consult")["max_tokens"] == 512


def test_get_missing_returns_typed_default_and_row(tmp_path, monkeypatch):
    from hermes_router.core import config_access
    from hermes_router.core import telemetry

    rows = []
    monkeypatch.setattr(telemetry, "log_route",
                        lambda event, **f: rows.append((event, f)))
    monkeypatch.setattr(config_access, "router_section", lambda: {})

    v = config_access.get("pending_routes_ttl_seconds")
    assert v == 300
    assert rows[-1][0] == "config_key_missing"

    v2 = config_access.get("unknown.path.zzz")
    assert v2 is None
    assert rows[-1][0] == "config_key_unknown"


def test_get_type_mismatch_returns_default(tmp_path, monkeypatch):
    from hermes_router.core import config_access
    from hermes_router.core import telemetry

    rows = []
    monkeypatch.setattr(telemetry, "log_route",
                        lambda event, **f: rows.append((event, f)))
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"render_max_chars": "not-an-int"})
    assert config_access.get("render_max_chars") == 0
    assert rows[-1][0] == "config_key_type_mismatch"


def test_get_present_value_roundtrip(tmp_path, monkeypatch):
    from hermes_router.core import config_access
    from hermes_router.core import telemetry

    rows = []
    monkeypatch.setattr(telemetry, "log_route",
                        lambda event, **f: rows.append((event, f)))
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"complexity": {"consult_cooldown_turns": 0}})
    assert config_access.get("complexity.consult_cooldown_turns") == 0
    assert not rows


def test_validate_config_counts_violations(tmp_path, monkeypatch):
    from hermes_router.core import config_access
    from hermes_router.core import telemetry

    rows = []
    monkeypatch.setattr(telemetry, "log_route",
                        lambda event, **f: rows.append((event, f)))
    # partial block: parent present, leaf dropped (violation)
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"complexity": {"other": 1}})
    n = config_access.validate_config()
    assert n >= 1
    assert any(r[0] == "config_key_missing" for r in rows)
