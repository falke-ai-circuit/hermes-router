"""conftest — importable plugin package for tests (package has __init__.py).

Tests import the plugin as a PACKAGE (`import hermes_router`) so all
modules share one namespace (Trap 5: bare `import state` vs
`from hermes_router import state` would create two module objects with
diverging mutable state). PLUGIN_DIR alone is NOT enough for the package
import — the PARENT dir (plugins/) must be on sys.path too.

v3.0.0: the Hermes runtime (/opt/hermes — hermes_cli, hermes_constants,
agent.*) is added to sys.path GUARDED (only when resolvable on disk and not
already importable) so monkeypatch targets like `hermes_cli.config.load_config`
resolve in dev-container test runs. Outside a Hermes checkout the insert is
skipped silently — the suite stays green wherever the plugin is deployed.
"""
import os
import sys

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PARENT_DIR, PLUGIN_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:  # hermes_cli needed only by tests that monkeypatch config loading
    import hermes_cli  # noqa: F401
except ImportError:
    _HERMES_RUNTIME = "/opt/hermes"
    if os.path.isdir(os.path.join(_HERMES_RUNTIME, "hermes_cli")) and _HERMES_RUNTIME not in sys.path:
        sys.path.insert(0, _HERMES_RUNTIME)