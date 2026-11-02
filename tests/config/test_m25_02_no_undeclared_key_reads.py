# tests/config/test_m25_02_no_undeclared_key_reads.py — M25-02 GATE 2.
#
# GATE 2: no config key is READ that core/schema.py does not DECLARE.
#
# Why this gate exists next to test_p4_schema_budgets.py::test_c (which does a
# regex version of the same thing): test_c is a PINNED RED whose message names
# two real offenders. A pinned red is useful only while the list under it is
# shrinking. This gate states the rule POSITIVELY — zero undeclared reads — so
# the moment the list is empty the rule holds and the gate is green, instead of
# a permanent "known-bad, keep going" row. M25-02 declared the two keys test_c
# named (closure_pass, unknown_route — see core/schema.py), so the static half
# below starts at zero offenders. No allowlist, no pair of exceptions.
#
# TWO HALVES, because a static matcher alone is a half-truth:
#
#   (a) STATIC — every config_access.get("...") / get("...") / sub_block("...")
#       / sub_block_alias("a", "b") call whose argument is a LITERAL (or an
#       implicit concatenation of literals, which the parser already folds into
#       one Constant) must name a path declared in schema.SCHEMA. The scan is
#       AST-based, not regex: a commented-out call, a docstring example and a
#       string in a test fixture are not reads, and only a Call node is.
#
#   (b) DYNAMIC — f"providers.{name}.base_url" and "paths." + name cannot be
#       resolved statically, so the gate does not pretend to. It resolves the
#       JOIN PREFIX — the literal text before the first dynamic segment — and
#       requires that some declared key starts with it. That is the property
#       that actually matters: a dynamic read must stay inside a namespace the
#       schema owns. f"billing.{name}.key" would pass every literal check ever
#       written and still invent a namespace; this half refuses it.
#
#   (c) UNRESOLVED — a call that hands config_access.get()/sub_block() a bare
#       variable. Nothing static can follow that, so the sites are PINNED
#       exactly: a new one is a red ("review this pass-through, then extend the
#       pin"), a vanished one is a red ("the pin must shrink"). Stated blind
#       spot, not a silent one.
#
# Out of scope by construction (stated, not hidden): values arriving from the
# environment or a config file at runtime; a key assembled with no literal
# prefix at all (caught by (c), not by (b)); whether the DECLARED default is
# the right value (that is test_m23's job).
import ast
import os

import pytest

PKG_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))  # tests/config/ -> repo root

# Shipping code only: the gate is about what the plugin READS at runtime.
EXCLUDE_DIRS = {
    "tests", "__pycache__", ".git", "node_modules", ".pytest_cache",
    ".mypy_cache", "reviews", "scripts", "tools", "benchmarks",
}

READER_ATTRS = {"get", "sub_block", "sub_block_alias"}
# Receivers that are the single config accessor under another name.
ACCESSOR_RECEIVERS = {"config_access", "_ca", "ca", "_schema"}

# The dynamic config-path joins this gate can see at M25-02. Each entry is the
# JOIN PREFIX — the literal text before the first dynamic segment. Pinned
# exactly: a new prefix means a read invented a namespace, which is the whole
# failure mode this half exists to catch.
DYNAMIC_JOIN_PREFIXES = {
    "providers.",   # anchor_chain.py:124, core/config_access.py:346/382/425
    "paths.",       # core/telemetry.py:112
}

# Pass-through call sites: the argument is a variable, so no static matcher can
# follow it. Pinned exactly, both directions.
UNRESOLVED_PASSTHROUGH = {
    ("core/budgets.py", "get"),
    ("core/config_access.py", "sub_block"),
    ("features/envelope/unified.py", "get"),
}


# --------------------------------------------------------------------------
# scanning
# --------------------------------------------------------------------------
def _shipping_py(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
        for fn in sorted(filenames):
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def _callee(node):
    """(receiver_tail, attr) for a Call; ("", name) for a bare Name call."""
    func = node.func
    if isinstance(func, ast.Attribute):
        try:
            base = ast.unparse(func.value)
        except Exception:  # noqa: BLE001 — unparse is best effort
            base = ""
        return base.split(".")[-1], func.attr
    if isinstance(func, ast.Name):
        return "", func.id
    return None, None


def _leading_literal(node):
    """The literal text a key argument STARTS with, or None if it starts with
    a runtime value. This is the join prefix for a dynamic read."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        # f"providers.{name}.base_url" -> first literal segment "providers."
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return value.value
            return None  # starts with {expr}: no prefix at all
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        # "paths." + name
        return _leading_literal(node.left)
    if isinstance(node, ast.Call):
        # "providers.{}.base_url".format(name)
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "format":
            return _leading_literal(func.value)
        if isinstance(func, ast.Attribute) and func.attr == "join":
            return None
    return None


def _is_literal(node):
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def config_reads(root):
    """Every config read in the shipping tree, classified.

    Returns dict with lists: static [(rel, lineno, kind, path)],
    dynamic [(rel, lineno, kind, prefix)], unresolved [(rel, lineno, kind)].
    """
    static, dynamic, unresolved = [], [], []
    for path in _shipping_py(root):
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        src = open(path, encoding="utf-8", errors="replace").read()
        tree = ast.parse(src, filename=rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            receiver, attr = _callee(node)
            if attr not in READER_ATTRS:
                continue
            # The accessor by name, or the module's own internal callers.
            if receiver not in ACCESSOR_RECEIVERS:
                if not (receiver == "" and rel == "core/config_access.py"):
                    continue
            args = list(node.args)
            if attr == "sub_block_alias":
                targets = args  # ("reflex", "decision") — every name is read
            else:
                targets = args[:1]
            if not targets:
                for kw in node.keywords:
                    if kw.arg in ("path", "name"):
                        targets = [kw.value]
                        break
            for arg in targets:
                if _is_literal(arg):
                    static.append((rel, node.lineno, attr, arg.value))
                    continue
                prefix = _leading_literal(arg)
                if prefix:
                    dynamic.append((rel, node.lineno, attr, prefix))
                else:
                    unresolved.append((rel, node.lineno, attr))
    return {"static": static, "dynamic": dynamic, "unresolved": unresolved}


def _declared():
    from hermes_router.core import schema

    return {k.path for k in schema.SCHEMA}


# --------------------------------------------------------------------------
# (a) STATIC — a literal read must be a declared path
# --------------------------------------------------------------------------
def test_no_literal_read_of_an_undeclared_path():
    reads = config_reads(PKG_ROOT)
    assert reads["static"], (
        "M25-02 GATE 2: no literal config reads found — the scanner is blind or "
        "the accessor call shape changed. Re-derive it before trusting a green."
    )
    known = _declared()
    offenders = sorted(
        "%s:%d: %s(%r)" % (rel, line, kind, path)
        for rel, line, kind, path in reads["static"]
        if path not in known
    )
    assert not offenders, (
        "M25-02 GATE 2: config key READ but not DECLARED in core/schema.py:\n"
        + "\n".join("  " + o for o in offenders)
        + "\nDeclare the key (typed, defaulted) in core/schema.py and add it to "
          "examples/config.example.yaml — or stop reading a key nobody "
          "declared. A read of an undeclared key has no typed default, no "
          "load-time validation row and no example coverage."
    )


def test_literal_reads_cover_the_block_readers_too():
    """sub_block()/sub_block_alias() are config reads: prove the scanner sees
    them, so a green above is not 'get() only'."""
    kinds = {kind for _, _, kind, _ in config_reads(PKG_ROOT)["static"]}
    assert "get" in kinds, "get() read sites missing from the scan"
    assert "sub_block" in kinds, "sub_block() read sites missing from the scan"


# --------------------------------------------------------------------------
# (b) DYNAMIC — a join prefix must belong to a declared namespace
# --------------------------------------------------------------------------
def test_every_dynamic_join_stays_inside_a_declared_namespace():
    reads = config_reads(PKG_ROOT)
    known = _declared()
    offenders = []
    for rel, line, kind, prefix in reads["dynamic"]:
        if not any(p.startswith(prefix) for p in known):
            offenders.append("%s:%d: %s(%r + ...)" % (rel, line, kind, prefix))
    assert not offenders, (
        "M25-02 GATE 2: dynamic config read outside every declared namespace:\n"
        + "\n".join("  " + o for o in offenders)
        + "\nDeclare at least one key under that prefix in core/schema.py, or "
          "stop composing paths in a namespace the schema does not own."
    )


def test_dynamic_join_prefix_inventory_is_pinned():
    """The prefix set is the gate's visible dynamic surface. Pinned exactly:
    new namespace = review it, vanished one = shrink the pin."""
    found = {prefix for _, _, _, prefix in config_reads(PKG_ROOT)["dynamic"]}
    new = sorted(found - DYNAMIC_JOIN_PREFIXES)
    gone = sorted(DYNAMIC_JOIN_PREFIXES - found)
    assert not new, (
        "M25-02 GATE 2: new dynamic config-path namespace(s) appeared — review "
        "the join, then extend DYNAMIC_JOIN_PREFIXES:\n"
        + "\n".join("  %r" % p for p in new)
    )
    assert not gone, (
        "M25-02 GATE 2: pinned dynamic namespaces vanished — shrink the pin:\n"
        + "\n".join("  %r" % p for p in gone)
    )


def test_dynamic_join_prefix_ends_on_a_dot():
    """A prefix must be a NAMESPACE, not a partial key: f"provider.{x}" is a
    typo that a startswith() check would otherwise wave through as a prefix of
    'providers.nous.base_url' only if it ended on a dot boundary."""
    bad = sorted({prefix for _, _, _, prefix in
                  config_reads(PKG_ROOT)["dynamic"] if not prefix.endswith(".")})
    assert not bad, (
        "M25-02 GATE 2: dynamic read whose literal prefix is not a dotted "
        "namespace (it cannot denote a key family): %r" % bad)


# --------------------------------------------------------------------------
# (c) UNRESOLVED — pass-through call sites are pinned, both directions
# --------------------------------------------------------------------------
def test_unresolvable_call_sites_are_pinned():
    found = {(rel, kind) for rel, _, kind in
             config_reads(PKG_ROOT)["unresolved"]}
    new = sorted(found - UNRESOLVED_PASSTHROUGH)
    gone = sorted(UNRESOLVED_PASSTHROUGH - found)
    assert not new, (
        "M25-02 GATE 2: config read of a RUNTIME expression (no literal "
        "prefix) appeared — review the pass-through, then extend "
        "UNRESOLVED_PASSTHROUGH:\n" + "\n".join("  %s %s" % f for f in new)
    )
    assert not gone, (
        "M25-02 GATE 2: pinned pass-through site(s) vanished — shrink the pin:\n"
        + "\n".join("  %s %s" % f for f in gone)
    )


# --------------------------------------------------------------------------
# self-check — a gate that cannot fail on planted litter is theatre
# --------------------------------------------------------------------------
@pytest.fixture()
def litter(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "reads.py").write_text(
        'from hermes_router.core import config_access\n'
        '\n'
        'A = config_access.get("providers.nous.base_url")\n'      # declared -> ok
        'B = config_access.get("made.up.key")\n'                  # undeclared -> red
        'C = config_access.sub_block("no_such_block")\n'          # undeclared -> red
        'D = config_access.get(f"providers.{A}.base_url")\n'      # declared ns -> ok
        'E = config_access.get(f"billing.{A}.key")\n'             # invented ns -> red
        'F = config_access.get(f"provider.{A}")\n'                # partial key -> red
        'G = config_access.sub_block_alias("reflex", "nope_alias")\n'  # one undeclared -> red
        'H = config_access.get(A)\n'                              # unresolvable
        '# config_access.get("commented.out.call") must not be a read\n'
        'DOC = "config_access.get(\'docstring.example\')"\n',
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "ignored.py").write_text(
        'from hermes_router.core import config_access\n'
        'X = config_access.get("not.scanned")\n', encoding="utf-8")
    return str(tmp_path)


def test_selfcheck_static_half_catches_undeclared_literals(litter):
    reads = config_reads(litter)
    paths = {p for _, _, _, p in reads["static"]}
    assert "providers.nous.base_url" in paths
    assert "made.up.key" in paths and "no_such_block" in paths
    assert "nope_alias" in paths, "sub_block_alias names must all be checked"
    assert "commented.out.call" not in paths, "a comment is not a read"
    assert "docstring.example" not in paths, "a string is not a read"
    assert "not.scanned" not in paths, "tests/ must not be scanned"


def test_selfcheck_dynamic_half_catches_invented_namespace(litter):
    prefixes = {p for _, _, _, p in config_reads(litter)["dynamic"]}
    assert "providers." in prefixes
    assert "billing." in prefixes and "provider." in prefixes
    known = _declared()
    assert any(p.startswith("providers.") for p in known)
    assert not any(p.startswith("billing.") for p in known)


def test_selfcheck_unresolved_half_is_not_silent(litter):
    found = {(rel, kind) for rel, _, kind in config_reads(litter)["unresolved"]}
    assert ("pkg/reads.py", "get") in found, (
        "a read of a bare variable must be reported, not silently skipped"
    )
