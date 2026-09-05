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

import pytest

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


@pytest.fixture(autouse=True)
def _isolate_canonical_ledger(tmp_path, monkeypatch):
    """v3.1.0: point the canonical-event ledger, its state.db seam, and the
    render inbox at test-scoped paths so suite runs never read/write the live
    profile hermes home (hermes-router-canonical.jsonl,
    uncensored-router-renders.jsonl) or the real state.db. Path-function
    patches only — no sys.modules injection (a fake hermes_constants leaks
    into hermes_cli.config / persona_card import chains)."""
    try:
        import hermes_router.canonical as _canon
    except ImportError:
        yield
        return
    sidecar = tmp_path / "v310-canonical-home" / "hermes-router-canonical.jsonl"
    inbox = tmp_path / "v310-canonical-home" / "uncensored-router-renders.jsonl"
    recon = tmp_path / "v310-canonical-home" / "hermes-router-reconciled.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    _canon.clear_for_tests()
    monkeypatch.setattr(_canon, "_store_path", lambda: str(sidecar))
    from hermes_router import render_inbox as _rinbox
    monkeypatch.setattr(_rinbox, "_inbox_path", lambda: str(inbox))
    # v3.2.3: the persistent reconcile-consume sidecar is test-isolated too,
    # and both consume layers reset per test (no cross-test marker bleed).
    monkeypatch.setattr(_rinbox, "_reconciled_path", lambda: str(recon))
    _rinbox.clear_consumed_for_tests()
    db = tmp_path / "v310-canonical-state.db"
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " session_id TEXT, role TEXT, content TEXT, timestamp REAL,"
        " api_content TEXT)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(_canon, "_state_db_path", lambda: str(db))
    yield
    _canon.clear_for_tests()
    _rinbox.clear_consumed_for_tests()