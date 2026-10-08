"""R23 leg 1 — architecture-doc contract guard.

The layer diagram in docs/architecture.md is hand-maintained; this test
fails the suite loudly when the named layers drift out of the document or
the module table references files that no longer exist.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(ROOT, "docs", "architecture.md")

NAMED_LAYERS = (
    "core/",
    "features/",
    "passes/",
    "gate/",
    "api/",
    "lanes/",
)


def _doc_text() -> str:
    with open(DOC, encoding="utf-8") as fh:
        return fh.read()


def test_layer_diagram_names_all_layers():
    text = _doc_text()
    for layer in NAMED_LAYERS:
        assert layer in text, f"architecture.md layer diagram missing `{layer}`"


def test_diagram_stamped_with_generation_commit():
    text = _doc_text()
    assert re.search(r"[Gg]enerated on `[0-9a-f]{7,40}`", text), (
        "architecture.md missing the 'Generated on <commit>' stamp"
    )


def test_module_table_rows_exist_in_tree():
    text = _doc_text()
    prefixes = ["", "core/", "features/", "passes/", "gate/", "api/", "lanes/"]
    path_re = re.compile(
        r"`((?:core|features|passes|gate|api|lanes)/[A-Za-z0-9_./-]+\.py)`"
    )
    missing = []
    for m in path_re.finditer(text):
        rel = m.group(1)
        if not any(os.path.exists(os.path.join(ROOT, p + rel)) for p in prefixes):
            missing.append(rel)
    assert not missing, f"architecture.md references missing modules: {sorted(set(missing))}"


def test_dispatcher_passes_layer_is_real():
    """The L2 `passes/` package must exist and hold the moved dispatcher
    implementations; the root paths are re-export shims."""
    for mod in ("dispatcher_pre.py", "dispatcher_post.py", "dispatcher_knobs.py"):
        impl = os.path.join(ROOT, "passes", mod)
        shim = os.path.join(ROOT, mod)
        assert os.path.exists(impl), f"passes/{mod} missing"
        assert os.path.exists(shim), f"root shim {mod} missing"
        with open(shim, encoding="utf-8") as fh:
            assert "re-export shim" in fh.read(), f"{mod} is no longer a shim"
