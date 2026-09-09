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
def _zero_network_guard(monkeypatch):
    """v3.6.0 zero-network suite guard (Goran-direct stress phase, 2026-09-07):
    NO test may make an outbound network call. Two layers:

    1. Model-provider credential env vars are REMOVED for the duration of each
       test — the plugin's key-resolution seams (router._read_key,
       semantic_classifier._resolve_key, anchor_exec._resolve_key) then fail
       closed to "" and every un-mocked LLM path fail-opens to pass-through
       without paying a 25s timeout. Root-caused 2026-09-07: the gateway env
       exports NOUS_API_KEY; pytest children inherited it, so un-mocked POST
       paths fired REAL aux calls to the nous inference API (live-caught:
       test_loop_guard flake + 541s suite runtime from 25s curl timeouts).
    2. outbound socket connects + curl subprocess spawns raise immediately —
       a hard tripwire: any future test that regresses into network I/O fails
       LOUDLY at the call site instead of silently burning provider tokens.

    Test-infra only — production key resolution and egress are untouched.
    Scoped to model-provider keys: BROWSERBASE/TELEGRAM/DISCORD/etc. are NOT
    LLM lanes and stay alone.
    """
    _MODEL_KEY_ENV_VARS = (
        "MINIMAX_API_KEY", "VENICE_API_KEY", "OPENROUTER_API_KEY",
        "NOUS_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    )
    for _var in _MODEL_KEY_ENV_VARS:
        monkeypatch.delenv(_var, raising=False)

    import socket as _socket
    import subprocess as _subprocess

    _calls: list = []

    def _blocked_socket(*args, **kwargs):  # noqa: ANN002, ANN003
        _calls.append(("socket", args))
        raise AssertionError(
            "TEST EGRESS BLOCKED: outbound socket.connect attempted during a "
            "test (%r) — the suite is zero-network by directive; mock the "
            "provider seam instead (see conftest._zero_network_guard)" % (args,))

    def _blocked_subprocess_run(*args, **kwargs):  # noqa: ANN002, ANN003
        argv = args[0] if args else kwargs.get("args", ())
        _calls.append(("subprocess", argv))
        raise AssertionError(
            "TEST EGRESS BLOCKED: subprocess.run attempted during a test "
            "(argv head: %r) — curl-backed provider lanes must be mocked in "
            "tests (see conftest._zero_network_guard)" % (list(argv)[:3] if argv else (),))

    monkeypatch.setattr(_socket.socket, "connect", _blocked_socket, raising=True)
    monkeypatch.setattr(_socket, "create_connection", _blocked_socket, raising=True)
    monkeypatch.setattr(_subprocess, "run", _blocked_subprocess_run, raising=True)

    yield
    # introspection hook for the zero-network proof test
    assert not _calls, "egress attempts recorded: %r" % (_calls[:5],)


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

@pytest.fixture(autouse=True)
def _flinch_reason_no_aux(monkeypatch):
    """Goran ruling 2026-09-08 gate: default tests to fail-open (route).
    The reason classifier is an AUX lane - tests that exercise the POST
    route path must not pay aux latency or egress. Tests of the gate itself
    override this patch explicitly (see test_flinch_reason_gate.py)."""
    try:
        import hermes_router.flinch_reason as _fr

        monkeypatch.setattr(_fr, "classify_flinch_reason",
                            lambda ask, ref: None)
    except ImportError:
        pass
    yield


@pytest.fixture(autouse=True)
def _hermes_aux_no_network(monkeypatch):
    """aux_source=hermes (live configs since 2026-09-08) resolves through
    agent.auxiliary_client — which reads the REAL profile config and would
    dial the real provider. Default tests to a no-client stub (fail-open);
    tests of the hermes path override explicitly."""
    try:
        import agent.auxiliary_client as _ac

        monkeypatch.setattr(_ac, "get_text_auxiliary_client",
                            lambda task="": (None, None), raising=False)
    except ImportError:
        pass
    # cfg-less aux reads default to legacy in tests (live config may say
    # aux_source: hermes) - legacy-seam tests then behave as authored.
    try:
        import hermes_router.semantic_classifier as _sc

        monkeypatch.setattr(_sc, "_classification_cfg",
                            lambda cfg=None: cfg if cfg is not None else {"aux_source": "legacy"},
                            raising=False)
    except ImportError:
        pass
    yield


@pytest.fixture(autouse=True)
def _aux_hermes_adapter(monkeypatch):
    """2026-09-08: the aux lane is Hermes-only (no legacy curl seam). Tests
    that patch sc._post_chat / sc.subprocess.run to simulate the aux
    transport keep working: route the Hermes dispatch through _post_chat so
    existing test doubles stay authoritative."""
    import hermes_router.semantic_classifier as _sc

    if not hasattr(_sc, "_original_hermes_aux"):
        _sc._original_hermes_aux = _sc._hermes_aux_call
    _prod_post_chat = _sc._post_chat

    def _dispatch(payload_json, timeout):
        # Only route through _post_chat when the TEST has overridden the
        # transport seam (sc._post_chat). Otherwise fail-open, no egress.
        if _sc._post_chat is _prod_post_chat:
            return None
        return _sc._post_chat("", "", payload_json, timeout)

    monkeypatch.setattr(_sc, "_hermes_aux_call", _dispatch, raising=True)
    yield

@pytest.fixture(autouse=True)
def _isolate_router_config(monkeypatch, request):
    """Pin router config knobs to defaults (banner OFF, higher-self OFF,
    empty complexity) unless the test requests live-config via the
    `live_router_config` marker. Root cause fixed (2026-09-09): fleet config
    (debug_banner:2, pre_mode/audit_mode enabled) leaked into unit tests via
    _banner_section()/load_config + the _LAST_GOOD_SECTION cache."""
    from hermes_router import debug_banner as _dbg
    from hermes_router import provenance_footer as _pf
    from hermes_router import router_core as _rc
    from hermes_router import persona_card as _pcard

    marker = request.node.get_closest_marker("live_router_config")
    if marker:
        yield
        return

    def _empty_section():
        return {}

    monkeypatch.setattr(_dbg, "_banner_section", _empty_section, raising=True)
    monkeypatch.setattr(_dbg, "_LAST_GOOD_SECTION", {}, raising=False)
    monkeypatch.setattr(_rc, "_complexity_cfg", lambda: {}, raising=True)
    monkeypatch.setattr(_pf, "higher_self_rule_enabled", lambda: False, raising=True)
    # 2026-09-09 leak fix: fleet config sets provenance_footer: true, which
    # appended live footers to every exact-delivery assertion (45 failures).
    # Same isolation class as the debug_banner/higher-self pins above.
    monkeypatch.setattr(_pf, "provenance_footer_enabled", lambda: False, raising=True)
    # 2026-09-09 router-tuning knobs: pin to legacy behavior unless the test
    # enables them explicitly (same isolation class as the pins above).
    monkeypatch.setattr(_pcard, "persona_card_chars_budget", lambda: 0, raising=True)
    monkeypatch.setattr(_rc, "pre_cooldown_seconds", lambda: 0, raising=True)
    monkeypatch.setattr(_rc, "post_audit_min_turns", lambda: 1, raising=True)
    yield
    _dbg._LAST_GOOD_SECTION.clear()
