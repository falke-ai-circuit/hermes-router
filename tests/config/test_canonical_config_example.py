"""The canonical config example must define every parameter the schema knows.

If this fails, the schema gained (or lost) a key and `config.example.yaml` was
not regenerated. Fix:

    python3 scripts/gen_config.py --emit-example

Rationale: the example is the portable starting point for any Hermes install.
A stale example means a fresh install silently runs on hidden defaults — the
exact class of bug this file exists to prevent.
"""
import importlib.util
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GEN = os.path.join(REPO, "scripts", "gen_config.py")


def _load_gen_module():
    """Import scripts/gen_config.py in-process.

    NOT a subprocess: tests/conftest.py installs a zero-egress guard that
    blocks subprocess.run inside tests, and shelling out would also need a
    second interpreter just to compare two sets.
    """
    spec = importlib.util.spec_from_file_location("gen_config_under_test", GEN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_example_exists():
    assert os.path.exists(os.path.join(REPO, "config.example.yaml")), (
        "config.example.yaml missing — run: python3 scripts/gen_config.py --emit-example"
    )


def test_example_is_in_sync_with_schema():
    gen = _load_gen_module()
    schema = gen.load_schema()
    rc = gen.check(schema)
    assert rc == 0, (
        "config.example.yaml is out of sync with core/schema.py — "
        "fix: python3 scripts/gen_config.py --emit-example"
    )


def test_example_defines_router_block_only():
    """The example carries one portable block — never per-install values.

    Section name matters: the plugin reads `hermes_router` (core/config_access.py
    _SECTION_KEYS). An example under any other key is config nothing reads.
    """
    import yaml
    with open(os.path.join(REPO, "config.example.yaml")) as f:
        doc = yaml.safe_load(f) or {}
    assert set(doc) == {"hermes_router"}, (
        f"example must carry exactly one `hermes_router` block, got {sorted(doc)}"
    )
    router = doc["hermes_router"]
    # environment-specific keys must ship neutral so the file stays portable
    for path, expected in (("log_path", ""), ("paths.state_dir", "")):
        node = router
        for part in path.split("."):
            node = node.get(part, {})
        assert node == expected, f"{path} must be neutral ({expected!r}) in the example"
