"""R78U M47-09 — POST consult scope identity is a PLATFORM-SEAM TURN ID.

M47-08 ruling (B): the consult scope is `s:<session_id>|t:<turn_id>`, ONE key
for the model-turn counter AND the per-task cap — not an ask-text hash. Four
rounds (a697594, c6a7a04, 4bd22e6, 92095be) each closed one text shape and
opened the next, because the transformation vocabulary of the injected text
belongs to the host.

What these tests pin, one per ruling shape (all seven):
  T1  plain ask — writer scope == reader scope; 3 model turns → floor refuses
      1-3 and the 4th is allowed.
  T2  trailing `<memory-context>` block — identical.
  T3  block + host trailer AFTER the closer (the live `analyst/48054` shape).
  T4  the four host prefixes — identical, no prefix vocabulary needed.
  T5  the user quotes `<memory-context>` in their own ask — two distinct asks
      keep two distinct scopes and the stored ask is NOT truncated.
  T6  turn >= 2 with a stale `_LAST_SEEN` holding the previous ask — the
      counter lands on the NEW turn's key.
  T7  two 5,000-char asks sharing a 4,000-char prefix — distinct scopes.
Plus: a duplicate send inside one turn stays ONE scope (advance holds), and
`scope_source=text_fallback` appears only when both the argument and the stamp
are absent.
"""
from __future__ import annotations

import copy

import pytest

pytest_plugins = ("tests.conftest",)


@pytest.fixture(autouse=True)
def _config_cache_neutral():
    """Leave `config_access._cache` exactly as this module found it."""
    from hermes_router import config_access

    saved = copy.deepcopy(getattr(config_access, "_cache", None))
    yield
    cache = getattr(config_access, "_cache", None)
    if isinstance(cache, dict) and isinstance(saved, dict):
        cache.clear()
        cache.update(saved)


@pytest.fixture()
def budget():
    from hermes_router import consult_budget as cb, state

    cb.reset()
    state.clear()
    state.reset_turn_identity(None)
    state.reset_scope(None)
    yield cb
    cb.reset()
    state.clear()
    state.reset_turn_identity(None)
    state.reset_scope(None)


class _NoEndpointChain:
    """Stops the POST right AFTER the budget gate so its reason is readable."""

    def endpoint_for(self, _name):  # noqa: D401 — test double
        return None


def _plugin(monkeypatch, post_floor: int = 4):
    import sys

    _P = "/opt/data/plugins"
    if _P not in sys.path:
        sys.path.insert(0, _P)
    import hermes_router as plugin
    from hermes_router import config_access, route_gate

    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"level": 0,
                                 "complexity": {"consult_min_turns": 1,
                                                "consult_post_min_turns":
                                                    post_floor,
                                                "consult_max_per_task": 1}})
    monkeypatch.setattr(route_gate, "fence_pass",
                        lambda content, request, context, hs: None)
    monkeypatch.setattr(plugin, "_log_route", lambda e, **f: None)
    monkeypatch.setattr("hermes_router.anchor_chain.load_anchor_chain",
                        lambda: _NoEndpointChain())
    return plugin


def _seam(plugin, session_id: str, text: str, model: str = "glm-5.3",
          api_request_id: str = "api:1") -> None:
    """Drive the REAL llm_request seam exactly as the host calls it."""
    req = {"model": model,
           "messages": [{"role": "user", "content": text}]}
    try:
        plugin.on_llm_request(
            request=req, original_request=req,
            session_id=session_id, task_id="host-effective-task",
            turn_id="turn-1", api_request_id=api_request_id,
            api_call_count=1, model=model, provider="test",
            base_url="http://test", api_mode="chat", platform="test")
    except Exception:  # noqa: BLE001 — the seam is fail-open by contract
        pass


def _post(diag_holder: dict, session_id: str, ask: str, model: str = "glm-5.3",
          scope_id: str = ""):
    """Reach the live POST gate: `_consult_meta` runs `consult_budget.check`.

    The `scope_id` argument is passed only when the gate accepts it, so this
    harness also runs against the pre-M47-09 build — where it must go RED on
    the shapes that discriminate, not error out on a signature.
    """
    import inspect

    from hermes_router import completion_audit

    diag_holder.clear()
    kw = {}
    try:
        if "scope_id" in inspect.signature(
                completion_audit._consult_meta).parameters:
            kw["scope_id"] = scope_id
    except Exception:  # noqa: BLE001 — harness detail only
        kw = {}
    completion_audit._consult_meta(session_id, ask, "resp text", None, model,
                                   "fire-key", socket_timeout=45,
                                   diag=diag_holder, **kw)
    return diag_holder.get("reason")


def _assert_floor(budget, plugin, monkeypatch, session_id: str, ask: str):
    """3 drives refuse 1..3, the 4th is allowed, and one scope held all four."""
    from hermes_router import state

    plugin = _plugin(monkeypatch)
    _scope_for = getattr(state, "scope_for", lambda _sid: None)
    for i in (1, 2, 3):
        _seam(plugin, session_id, ask, api_request_id="t1:api:%d" % i)
    scope = _scope_for(session_id)
    assert scope and scope.startswith("s:" + session_id + "|t:"), scope
    assert budget.model_turns(scope) == 3, budget.model_turns(scope)

    for attempt in (1, 2, 3):
        assert _post({}, session_id, ask) == "below_min_turns", attempt

    _seam(plugin, session_id, ask, api_request_id="t1:api:4")
    assert budget.model_turns(scope) == 4
    # Past the floor: the run reaches the endpoint leg, which the stub refuses.
    assert _post({}, session_id, ask) == "no_primary_endpoint"
    return scope


# ---------------------------------------------------------------- shapes ----

def test_t1_plain_ask_one_scope(budget, monkeypatch):
    """Shape 1: plain ask — writer scope == reader scope, floor 1-3 / allow 4."""
    scope = _assert_floor(budget, None, monkeypatch, "s-m4709-t1",
                          "wire the post scope to the turn identity")
    assert scope == "s:s-m4709-t1|t:1"


def test_t2_trailing_memory_block(budget, monkeypatch):
    """Shape 2: trailing `<memory-context>` close at end of text."""
    ask = ("wire the post scope to the turn identity\n\n"
           "<memory-context>\nrecalled: earlier decisions\n</memory-context>")
    _assert_floor(budget, None, monkeypatch, "s-m4709-t2", ask)


def test_t3_block_plus_host_trailer(budget, monkeypatch):
    """Shape 3: the closer is followed by a host trailer (549 live rows)."""
    ask = ("wire the post scope to the turn identity\n\n"
           "<memory-context>\nrecalled: earlier decisions\n</memory-context>"
           "\n\n[System note: A new message has arrived while you were working. "
           "Read it before continuing.]")
    _assert_floor(budget, None, monkeypatch, "s-m4709-t3", ask)


@pytest.mark.parametrize("prefix", [
    "[System note: A new message has arrived while you were working.] ",
    "[Interrupted context: the previous response was cut off.] ",
    "[Model switch: the provider changed mid-turn.] ",
    "[Gateway shutdown notice: the process is stopping.] ",
])
def test_t4_host_prefixes_need_no_vocabulary(budget, monkeypatch, prefix):
    """Shape 4: each host prefix — the scope never looks at the text."""
    ask = prefix + "wire the post scope to the turn identity"
    _assert_floor(budget, None, monkeypatch, "s-m4709-t4", ask)


def test_t5_user_quoted_marker_keeps_two_scopes(budget, monkeypatch):
    """Shape 5: the user quotes the marker — text identity is irreducible."""
    from hermes_router import state

    plugin = _plugin(monkeypatch)
    sid, model = "s-m4709-t5", "glm-5.3"
    ask1 = "explain <memory-context> and what the platform does with it"
    ask2 = "why does <memory-context> appear twice in my request"

    _seam(plugin, sid, ask1, model=model, api_request_id="t5:api:1")
    scope1 = state.scope_for(sid)
    _seam(plugin, sid, ask2, model=model, api_request_id="t5:api:2")
    scope2 = state.scope_for(sid)

    assert scope1 == "s:s-m4709-t5|t:1", scope1
    assert scope2 == "s:s-m4709-t5|t:2", scope2
    assert scope1 != scope2
    assert budget.model_turns(scope1) == 1
    assert budget.model_turns(scope2) == 1
    # The classifier sees the user's bytes: no 120 -> 27 truncation.
    assert state.get_last_seen(sid) == ask2


def test_t6_turn_two_ignores_stale_last_seen(budget, monkeypatch):
    """Shape 6: a stale `_LAST_SEEN` must not own the identity."""
    from hermes_router import state

    plugin = _plugin(monkeypatch)
    sid, model = "s-m4709-t6", "glm-5.3"
    ask1 = "first ask, closes turn one"
    ask2 = "[System note: new message] second ask opens turn two"

    _seam(plugin, sid, ask1, model=model, api_request_id="t6:api:1")
    # The cache is deliberately left holding ask1 — the old reader started here.
    assert state.get_last_seen(sid) == ask1

    _seam(plugin, sid, ask2, model=model, api_request_id="t6:api:2")
    assert state.current_turn_id(sid) == 2
    scope2 = state.scope_for(sid)
    assert scope2 == "s:s-m4709-t6|t:2", scope2
    assert budget.model_turns(scope2) == 1, budget.model_turns(scope2)
    assert budget.model_turns("s:s-m4709-t6|t:1") == 1
    # The POST gate for turn two reads turn two's scope, not ask1's.
    assert _post({}, sid, ask2) in ("below_min_turns", "no_primary_endpoint")


def test_t7_two_long_asks_sharing_a_4000_char_prefix(budget, monkeypatch):
    """Shape 7: distinct turns, distinct scopes — the hash's domain is gone."""
    from hermes_router import state

    plugin = _plugin(monkeypatch)
    sid, model = "s-m4709-t7", "glm-5.3"
    head = "x" * 4000
    ask1 = head + " tail one"
    ask2 = head + " tail two"

    _seam(plugin, sid, ask1, model=model, api_request_id="t7:api:1")
    scope1 = state.scope_for(sid)
    _seam(plugin, sid, ask2, model=model, api_request_id="t7:api:2")
    scope2 = state.scope_for(sid)

    assert scope1 != scope2, (scope1, scope2)
    assert scope1.endswith("|t:1") and scope2.endswith("|t:2")


def test_duplicate_send_inside_one_turn_stays_one_scope(budget, monkeypatch):
    """`advance_turn_identity` HOLDS on an unchanged last-user hash."""
    from hermes_router import state

    plugin = _plugin(monkeypatch, post_floor=1)
    sid, ask = "s-m4709-dup", "one ask, sent twice"
    _seam(plugin, sid, ask, api_request_id="dup:api:1")
    scope1 = state.scope_for(sid)
    _seam(plugin, sid, ask, api_request_id="dup:api:2")
    scope2 = state.scope_for(sid)
    assert scope1 == scope2 == "s:s-m4709-dup|t:1"
    assert budget.model_turns(scope1) == 2


# ------------------------------------------------------- resolution order ---

def test_scope_source_is_text_fallback_only_without_argument_and_stamp(
        budget, monkeypatch):
    """Ruling §3 resolution order, and the fallback is a COUNTED residual."""
    from hermes_router import completion_audit, state

    sid, ask, model = "s-m4709-src", "an ask with no stamp", "glm-5.3"

    scope, source = completion_audit._resolve_scope(sid, ask, model)
    assert source == "text_fallback", source

    scope, source = completion_audit._resolve_scope(sid, ask, model, "s:x|t:9")
    assert (scope, source) == ("s:x|t:9", "fire_time")

    state.record_scope(sid, "s:s-m4709-src|t:3")
    scope, source = completion_audit._resolve_scope(sid, ask, model)
    assert (scope, source) == ("s:s-m4709-src|t:3", "stamp")


def test_async_thread_keeps_the_fire_time_scope(budget, monkeypatch):
    """A later turn advancing the counter must not re-point a running audit."""
    from hermes_router import completion_audit, state

    plugin = _plugin(monkeypatch)
    sid, model = "s-m4709-async", "glm-5.3"
    ask1 = "ask one ages four model turns"
    for i in (1, 2, 3, 4):
        _seam(plugin, sid, ask1, model=model, api_request_id="a:api:%d" % i)

    captured, source = completion_audit._resolve_scope(sid, ask1, model)
    assert source == "stamp" and captured == "s:s-m4709-async|t:1"
    assert budget.model_turns(captured) == 4

    # A NEW turn lands while the audit thread is still running; the stamp moves.
    _seam(plugin, sid, "ask two", model=model, api_request_id="a:api:5")
    assert state.scope_for(sid) == "s:s-m4709-async|t:2"

    # With the fire-time capture the gate reads turn one's counter (allowed);
    # without it the stamp would send it to the fresh turn (floored).
    assert _post({}, sid, ask1, model, scope_id=captured) == "no_primary_endpoint"


def test_reader_and_writer_use_one_key_on_the_real_seam(budget, monkeypatch):
    """The counter the writer filled is the counter the gate reads."""
    from hermes_router import router_core, state

    plugin = _plugin(monkeypatch)
    sid, ask, model = "s-m4709-key", "one key for counter and cap", "glm-5.3"
    for i in (1, 2, 3):
        _seam(plugin, sid, ask, model=model, api_request_id="k:api:%d" % i)

    scope = state.scope_for(sid)
    claim_id = router_core.task_id_for(sid, ask, model)
    assert scope == "s:s-m4709-key|t:1"
    assert claim_id != scope  # two identities, joined by two fields on the row
    assert budget.model_turns(scope) == 3
    assert budget.model_turns(budget.scope_key(sid, claim_id, ask=ask,
                                               model=model)) == 0


# ------------------------------------------------------- verify: the floor --

def test_t8_floor_1_to_3_refused_4_allowed_on_a_prefixed_copy(budget,
                                                              monkeypatch):
    """The real seam on a prefixed copy: 1-3 `below_min_turns`, 4 the endpoint."""
    ask = ("[System note: A new message has arrived while you were working.] "
           "close the post scope unit")
    scope = _assert_floor(budget, None, monkeypatch, "s-m4709-t8", ask)
    assert scope == "s:s-m4709-t8|t:1"
