"""conftest — importable plugin package for tests (package has __init__.py).

Tests import the plugin as a PACKAGE (`import uncensored_router`) so all
modules share one namespace (Trap 5: bare `import state` vs
`from uncensored_router import state` would create two module objects with
diverging mutable state). PLUGIN_DIR alone is NOT enough for the package
import — the PARENT dir (plugins/) must be on sys.path too.
"""
import os
import sys

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PARENT_DIR, PLUGIN_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)