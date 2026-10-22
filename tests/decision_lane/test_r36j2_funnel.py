"""R36j2: every write path goes through the row-json funnel, including midturn."""
import ast
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parents[2]


def test_no_write_path_json_dumps_the_verdict_raw():
    """The midturn write bypassed verdict_row_json with a plain json.dumps, so
    the band stamping was silently skipped: row 3858, written after the 5.11.0
    bounce, landed with confidence_band=None while the fix was already live.
    This is the R35 shape repeating — the midturn path bypassing the patched
    one."""
    for name in ("decision.py", "decision_midturn.py"):
        text = (SRC / name).read_text()
        for i, line in enumerate(text.splitlines(), 1):
            if "verdict_json=json.dumps" in line:
                pytest.fail("%s:%d writes verdict_json raw: %s"
                            % (name, i, line.strip()))


def test_handle_hit_row_json_sources_are_bound_in_scope():
    """Guards the reasoning error I made while fixing this: `content` looked
    unbound because the AST check only inspected function ARGS. It is bound at
    _handle_hit L753 from call_backend. Assert the name is bound SOMEWHERE
    (arg, assignment, or with-target) before claiming a site raises."""
    tree = ast.parse((SRC / "decision_midturn.py").read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_handle_hit")
    bound = {a.arg for a in fn.args.args}
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign):
            # Walk the targets: `content, meta, reason = call_backend(...)` is a
            # TUPLE target, and a flat isinstance(t, ast.Name) check silently
            # misses it. That is what made me believe `content` was unbound and
            # "fix" a branch that was never broken.
            for t in n.targets:
                bound |= {x.id for x in ast.walk(t)
                          if isinstance(x, ast.Name)}
        elif isinstance(n, (ast.For, ast.With)):
            for x in ast.walk(n):
                if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store):
                    bound.add(x.id)
    assert "text" in bound and "content" in bound
    import re

    body = (SRC / "decision_midturn.py").read_text()
    # both call sites are line-wrapped, so match across whitespace
    sites = re.findall(r"verdict_row_json\(\s*verdict,\s*(content|text)\s*\)",
                       body)
    assert set(sites) == {"content", "text"}, sites


def test_verdict_row_json_stamps_the_band_for_a_midturn_shaped_verdict():
    import json

    from hermes_router import decision as dec

    row = json.loads(dec.verdict_row_json(
        {"choice": "opt-1", "confidence": 0.72,
         "alternatives": ["opt-2"]}, "tool text"))
    assert row["confidence_band"] == "weak"
    assert row["delta_source_excerpt"] == "tool text"
