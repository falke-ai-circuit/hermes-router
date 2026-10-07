#!/usr/bin/env python3
"""lift_orchestrators.py — P3a one-shot: lift the module-level function defs
of hermes_router/__init__.py into gate/orchestration.py VERBATIM (dispatch
block A, P3a), rewriting every hub-global reference to a late-bound
_hub().NAME lookup so behavior AND the monkeypatch surface are preserved
byte-for-byte:

- moved-to-moved calls: X() -> _hub().X()  (hub binds orchestration names,
  so a test patching plugin.X intercepts exactly as it did when the defs
  lived in __init__ module globals)
- moved-to-hub refs (constants, module imports, logger): _hub().X
- locals/params/import-bindings inside each def are never rewritten
  (AST shadowing analysis, not regex)

Run from the repo root: python scripts/lift_orchestrators.py [--dry-run]
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INIT = ROOT / "__init__.py"
OUT = ROOT / "gate" / "orchestration.py"

KEEP_DEFS = {"register"}          # stays in __init__
HEADER = '''"""gate/orchestration.py — the three on_* orchestrators (+ their hub-local
helpers), lifted VERBATIM out of the package hub at P3a (proposal §1.6).

Every reference this module makes to a hub-level name goes through the
late-bound _hub() seam (sys.modules lookup at CALL time — no import cycle;
gate may import L0-L2 directly, never the hub at module level). This
preserves, byte-for-byte, the pre-lift patch semantics: a test monkeypatching
plugin.<name> intercepts every call exactly as it did when the defs lived in
__init__ module globals, because _hub() resolves the same attribute.
"""
from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional


def _hub():
    """Late-bound package hub (call time — no import cycle)."""
    return sys.modules["hermes_router"]


'''

# NOTE on module-level mutable state: defs moved here own no hub-level
# mutable state directly; the hub keeps its constants (DEFAULT_LOG_PATH,
# _LOG_LOCK, DEFAULT_PENDING_TTL) and sys.modules alias registration.
# The hub re-binds the moved names (see __init__ post-lift edit).


def _collect_module_names(tree):
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                names.add((a.asname or a.name).split(".")[0])
    return names


def _collect_locals(fn_node):
    """Every name bound anywhere inside fn_node (params, assigns, imports,
    comprehensions, except handlers, nested defs, walrus)."""
    local = set()

    def args_of(a):
        out = set()
        for arg in a.posonlyargs + a.args + a.kwonlyargs:
            out.add(arg.arg)
        if a.vararg:
            out.add(a.vararg.arg)
        if a.kwarg:
            out.add(a.kwarg.arg)
        return out

    for node in ast.walk(fn_node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            local |= args_of(node.args)
        if isinstance(node, ast.arg):
            local.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            local.add(node.id)
        elif isinstance(node, ast.Import):
            for a in node.names:
                local.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                local.add(a.asname or a.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            local.add(node.name)
        elif isinstance(node, ast.comprehension) and isinstance(node.target, ast.Name):
            local.add(node.target.id)
        elif isinstance(node, ast.comprehension) and isinstance(node.target, ast.Tuple):
            for e in node.target.elts:
                if isinstance(e, ast.Name):
                    local.add(e.id)
    return local


def main(dry_run: bool) -> int:
    src = INIT.read_text()
    tree = ast.parse(src)
    hub_names = _collect_module_names(tree)

    moved = [n for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name not in KEEP_DEFS]
    moved_names = {n.name for n in moved}

    # typing names stay direct (imported in the header)
    direct = {"Any", "Dict", "List", "Optional", "Tuple", "_hub"}
    builtins_safe = {"__name__", "__package__"}

    pieces = []
    for fn in moved:
        seg_start, seg_end = None, None
        # include decorator lines + def line start
        if fn.decorator_list:
            seg_start = min(d.lineno for d in fn.decorator_list)
        else:
            seg_start = fn.lineno
        seg_end = fn.end_lineno
        segment_lines = src.split("\n")[seg_start - 1:seg_end]
        segment_src = "\n".join(segment_lines)

        locals_ = _collect_locals(fn)
        # first-level function params of the def itself are in locals_
        rewrites = []
        for node in ast.walk(fn):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                name = node.id
                if (name in hub_names and name not in locals_
                        and name not in direct and name not in builtins_safe):
                    rewrites.append((node.lineno - seg_start,
                                     node.col_offset, node.end_col_offset, name))
        # apply from last to first, per line
        rewrites.sort(key=lambda r: (r[0], r[1]), reverse=True)
        for line_no, c0, c1, name in rewrites:
            line = segment_lines[line_no]
            segment_lines[line_no] = line[:c0] + f"_hub().{name}" + line[c1:]
        pieces.append("\n".join(segment_lines))

    out_src = HEADER + "\n\n" + "\n\n\n".join(pieces) + "\n"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if dry_run:
        print(f"DRY-RUN: would write {OUT} ({len(pieces)} defs, "
              f"{sum(1 for p in pieces for _ in [1])} defs lifted)")
        return 0
    OUT.write_text(out_src)
    print(f"wrote {OUT}: {len(pieces)} defs lifted")
    return 0


if __name__ == "__main__":
    sys.exit(main("--dry-run" in sys.argv))
