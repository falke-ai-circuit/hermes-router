#!/usr/bin/env python3
"""rewrite_imports.py — one-shot MECHANICAL import rewriter for package moves
(proposal §P1; used by the restructure phases, never by runtime code).

Applies a fixed module→new-subpackage mapping over the given files (default:
tests/ + benchmarks/), rewriting only unambiguous spellings:

    import hermes_router.<m>          -> import hermes_router.core.<m>
    import hermes_router.<m> as X     -> import hermes_router.core.<m> as X
    from hermes_router.<m> import ... -> from hermes_router.core.<m> import ...
    "hermes_router.<m>.attr"          -> "hermes_router.core.<m>.attr"
        (monkeypatch string targets — safe: quoted literals)

Everything else (package-level `from hermes_router import <m>`,
`hermes_router.<m>.__x` attribute reads, `from . import <m>`) is NOT
rewritten: the hub re-exports keep those working unchanged.

Idempotent: a file whose imports already name the NEW path is skipped for
that rule. --check mode exits 1 if any rewrite WOULD apply (CI drift guard).
"""
from __future__ import annotations

import os
import re
import sys

# Module → new dotted suffix. Extend per phase (P1: the core/ seven).
MODULE_MAP = {
    "config_access": "core.config_access",
    "state": "core.state",
    "canonical": "core.canonical",
    "session_store": "core.session_store",
    "usage_ledger": "core.usage_ledger",
    "routing_caps": "core.routing_caps",
    "decisions": "core.decisions",
    # P3a: api/ cluster
    "router_tools": "api.router_tools",
    "commands": "api.commands",
    "commands_config": "api.commands_config",
    "commands_diag": "api.commands_diag",
    "commands_runtime": "api.commands_runtime",
    "config_writer": "api.config_writer",
}


def _rules_for(mod: str, new: str):
    rules = []
    rules.append((
        re.compile(r"^(\s*)import hermes_router\.%s(\s+as\s+\w+)?(\s*(?:#.*)?)$" % mod, re.M),
        lambda match, _new=new: "%simport hermes_router.%s%s%s" % (
            match.group(1), _new, match.group(2) or "", match.group(3))))
    rules.append((
        re.compile(r"^(\s*)from hermes_router\.%s import (\([^)]*\)|[^(\n]*?)(\s*(?:#.*)?)$" % mod, re.M),
        lambda match, _new=new: "%sfrom hermes_router.%s import %s%s" % (
            match.group(1), _new, match.group(2), match.group(3))))
    # string rule: keep the opening quote char exactly
    def _string_sub(match, _new=new):
        return match.group(1) + "hermes_router." + _new + "." + \
            match.group(0).split(".", 2)[2]
    rules.append((
        re.compile(r"(['\"])hermes_router\.%s\." % mod),
        _string_sub))
    return rules


ALL_RULES = [r for m, n in MODULE_MAP.items() for r in _rules_for(m, n)]


def rewrite_text(text: str):
    count = 0
    for m, new in MODULE_MAP.items():
        if "hermes_router.%s" % new in text:
            continue
        for rule, fn in _rules_for(m, new)[:-1]:
            text, n = rule.subn(lambda match, _fn=fn: _fn(match, new), text)
            count += n
        rule, fn = _rules_for(m, new)[-1]
        text, n = rule.subn(lambda match, _fn=fn: _fn(match, new), text)
        count += n
    return text, count


def main(argv):
    check = "--check" in argv
    paths = [a for a in argv[1:] if a != "--check"]
    if not paths:
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.dirname(here)
        paths = [os.path.join(root, "tests"), os.path.join(root, "benchmarks")]
    targets = []
    for p in paths:
        if os.path.isdir(p):
            for dirpath, _, files in os.walk(p):
                targets.extend(os.path.join(dirpath, f) for f in files
                               if f.endswith(".py") and "__pycache__" not in dirpath)
        else:
            targets.append(p)
    total = 0
    for path in sorted(targets):
        with open(path, "r", encoding="utf-8") as fh:
            orig = fh.read()
        new, n = rewrite_text(orig)
        if n:
            total += n
            if check:
                print("WOULD REWRITE %s: %d" % (path, n))
            else:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(new)
                print("rewrote %s: %d" % (path, n))
    if check and total:
        print("CHECK FAILED: %d rewrite(s) pending" % total)
        return 1
    print("done: %d rewrite(s)" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
