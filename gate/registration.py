"""R23 leg 3 — plugin registration/wiring, lifted verbatim from the plugin
root `__init__.py` (v5 size-debt close-out; pure move, zero body edits).

The hub keeps thin delegating stubs (`register`, `__getattr__`, the on_*
re-exports) so the patch surface (`plugin.<name>`) and the gateway
registration contract are unchanged.
"""
from __future__ import annotations

import functools
import logging
import sys as _sys

from . import orchestration
from . import turn_close

# The hub logger is the PLUGIN ROOT module's logger — the registration
# warning rows keep the exact logger name they had pre-move.
_ROOT_NAME = __package__.rsplit(".", 1)[0]
logger = logging.getLogger(
    _sys.modules[_ROOT_NAME].__name__ if _ROOT_NAME in _sys.modules
    else _ROOT_NAME)


def install_legacy_aliases(root_name: str) -> None:
    """P1 legacy-path module aliases in sys.modules so EVERY import spelling
    of the moved modules keeps working (package attrs, `import
    hermes_router.X`, `from .X import Y`, monkeypatch string targets) — one
    shared module object per module (conftest Trap 5: single-namespace
    discipline). Registered at hub import, not lazily, so even attribute
    access before first use resolves identically. This is the back-compat
    shim; direct `hermes_router.core.X` imports are the new canonical
    spelling (see scripts/rewrite_imports.py)."""
    from ..api import (commands, commands_config, commands_diag,
                       commands_runtime, config_writer, router_tools)
    from ..core import (canonical, config_access, decisions, routing_caps,
                        session_store, state, telemetry, usage_ledger)

    for _m in (canonical, config_access, decisions, routing_caps,
               session_store, state, telemetry, usage_ledger, config_writer,
               commands, commands_config, commands_diag, commands_runtime,
               router_tools):
        _sys.modules.setdefault(f"{root_name}.{_m.__name__.rsplit('.', 1)[-1]}", _m)


on_llm_request = orchestration.on_llm_request
on_llm_execution = orchestration.on_llm_execution
on_transform_terminal_output = orchestration.on_transform_terminal_output


def _hub_log_route():
    """The plugin root's `_log_route`, or None outside a package context."""
    root = _sys.modules.get(_ROOT_NAME)
    return getattr(root, "_log_route", None) if root is not None else None


#: M35-04 — the loud-failure body. Reached ONLY when marker text survived the
#: strip: the turn is withheld rather than delivered, because routing metadata
#: in a user's answer is a leak of internal plumbing, not a formatting wobble.
WITHHELD_BODY = (
    "[hermes-router] This turn was withheld: internal routing metadata reached "
    "the delivery boundary and could not be removed. No answer text was "
    "released. Re-run the ask."
)


def _sanitize_delivery(text, *, session_id: str = "") -> tuple:
    """Strip marker text at the DELIVERY BOUNDARY. (body, outcome, marker).

    The strip inside the orchestrator is one seam on ONE path; this one sits on
    the edge where the bytes are settled, so it also covers the paths that never
    entered the routing table (router disabled, guard fail-open, passthrough
    early return). Well-formed tag lines are removed; a surviving fragment
    (`<turn declined=…` truncated, malformed) withholds the whole body instead
    of shipping it. Never raises: an unreadable body is delivered unchanged by
    the caller's judgement, never by an exception.
    """
    try:
        from ..features.detection import declaration as _decl

        # `None` is the pass-through contract ("deliver the body you gave me"),
        # not an empty body: sanitizing it to "" would blank the user's answer.
        if text is None:
            return None, "", ""
        # Cheap fast path: no candidate marker ⇒ nothing to strip. The scan
        # below is line-wise regex work on every delivered body, and the
        # boundary sits on the delivery path.
        if "<turn" not in str(text).lower():
            return text, "", ""
        clean, marker = _decl.scrub_markers(text)
        if marker:
            lg = _hub_log_route()
            if lg is not None:
                try:
                    lg("POST", event_detail="marker_leak_withheld",
                       reason="marker_survived_boundary_strip",
                       marker=str(marker)[:32],
                       severity="error",
                       session_id=str(session_id or ""))
                except Exception:  # noqa: BLE001 — telemetry never breaks delivery
                    pass
            return WITHHELD_BODY, "marker_leak_withheld", str(marker)
        return clean, "", ""
    except Exception:  # noqa: BLE001 — the boundary never breaks a turn
        return text, "", ""


@functools.wraps(orchestration.on_transform_llm_output)
def on_transform_llm_output_guarded(**kwargs):
    """R78U M8 delivery-edge guards IN FRONT OF the POST orchestrator.

    `functools.wraps` is load-bearing, not decoration: two shipped pins read
    the seam at SOURCE level (`test_r9_banner_delivery_seam` asserts the local
    debug_banner import and the LIFECYCLE.deliver chokepoint live in
    `on_transform_llm_output`; `test_register_is_idempotent` asserts the
    registered hook IS the plugin attribute). `inspect.getsource` unwraps through
    `__wrapped__`, so the orchestrator's own body is what those pins still see,
    while the registered object applies the guards in front of it.

    The orchestrator decides whether a turn ROUTES; these guards decide whether
    the replacement it returns may REPLACE the agent's body:

      1. an identity rewrite (the render re-says the original) is dropped — it
         delivers nothing and cost a call;
      2. a non-English decline cue no longer inherits the English-object
         default that called benign prose `action_denial` — such a body must
         be SHAPED like a refusal (terse, nothing delivered);
      3. the model's own self-declaration outranks classifier-only inference:
         `declined="false"` settles the turn, `declined="true"` keeps its
         route.

    Transparent by construction: with no guard firing, the orchestrator's
    answer is returned unchanged, and any guard exception returns it too. The
    echo guard and the declaration contract are read, never modified.
    """
    _pg = None
    _pc_token = None
    _prov = None
    _turn = None
    try:
        from .. import post_guards as _pg

        _pc_token = _pg.open_pre_call(
            kwargs.get("response_text", "") or "",
            session_id=str(kwargs.get("session_id") or ""),
            log_route=_hub_log_route(),
        )
    except Exception:  # noqa: BLE001 — no window = no pre-call gate, status quo
        _pg = None
        _pc_token = None
    try:
        from ..core import turn_provenance as _prov_mod

        _prov = _prov_mod
        # M35-04: the POST pass ARRIVED, so the turn the PRE edge armed is not
        # a lost turn — disarm it before opening this pass's own record. Both
        # the begin and the orchestration call below run inside the in-flight
        # window so the PRE edge can predict a host skip (see `seam_blocked`).
        _prov.disarm(str(kwargs.get("session_id") or ""))
        _turn = _prov.begin(session_id=str(kwargs.get("session_id") or ""),
                            model=str(kwargs.get("model") or ""),
                            body=str(kwargs.get("response_text") or ""))
    except Exception:  # noqa: BLE001 — instrumentation never breaks delivery
        _prov = None
        _turn = None
    try:
        if _prov is not None:
            _prov.seam_enter()
        out = orchestration.on_transform_llm_output(**kwargs)
    except Exception:
        try:
            if _prov is not None:
                # M35-04: an exception is not a decision. The turn is closed as
                # SKIPPED with the error named, so a failed pass is attributable
                # rather than reading like an ordinary delivery.
                try:
                    _turn.skip("seam_error")
                except Exception:  # noqa: BLE001
                    pass
                _prov.finish(_turn, delivered=None, outcome="seam_error")
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        try:
            if _prov is not None:
                _prov.seam_exit()
        except Exception:  # noqa: BLE001
            pass
        try:
            if _pg is not None:
                _pg.close_pre_call(_pc_token)
        except Exception:  # noqa: BLE001 — closing never breaks delivery
            pass
    # M35-02: THE DELIVERY EDGE OWNS THE ROW.
    # The row was finished BEFORE the guards ran, so a guard that drops the
    # replacement produced a row claiming replaced=True while the caller got
    # the main body — B3's shape, one seam earlier. The body is settled FIRST
    # (`final`), then the row is written from it, so the ledger can only ever
    # describe bytes that actually reach the response surface.
    _original = kwargs.get("response_text", "") or ""
    _guard_reason = ""
    try:
        from .. import post_guards as _pg

        # NB (M35-08B): the reason window is cleared by `open_pre_call`, at the
        # start of this delivery edge. Clearing it here would erase the reason a
        # PRE-call stand-down recorded for a compliant body.
        final = _pg.apply_guards(
            _original,
            out,
            log_route=_hub_log_route(),
            session_id=str(kwargs.get("session_id") or ""),
            # M35-08B: only a LANE RENDER may be stood down for a compliant
            # body. The provenance already records whether the seam replaced
            # the turn with a lane's answer; a banner append, the phantom-write
            # scrub or a router-failure surface never set it, and must ship.
            is_lane_render=bool(getattr(_turn, "replaced", False)),
        )
        # M35-08: the guard's verdict travels with the body, so the row can say
        # WHY main's body was kept instead of defaulting every keep to a
        # blocked-render failure. M35-10: the reason is read on EVERY pass, not
        # only when the guard returned None — a kept-body turn whose transform
        # is not a suppression (the bar-2 `main_complied` case) carries its
        # token through the same window.
        _guard_reason = _pg.last_guard_reason(
            str(kwargs.get("session_id") or ""))
        # M35-10 (bar 2, D2): the ordinary benign turn returns None from the
        # orchestrator ("deliver the body you gave me"), so no guard ever
        # classified it and the row shipped with reason="". The bar requires
        # the token on EVERY kept-body row: when nothing was replaced and no
        # guard spoke, classify the original body here and name the PASS.
        if final is None and not bool(getattr(_turn, "replaced", False)) \
                and not _guard_reason:
            try:
                _clean, _tag = _pg.read_declaration(_original)
                if _pg.classify_original(_clean) == _pg.COMPLIANCE:
                    _guard_reason = _pg.MAIN_COMPLIED
            except Exception:  # noqa: BLE001 — an unreadable body keeps the old row
                pass
    except Exception:  # noqa: BLE001 — guards fail open to the orchestrator
        final = out
    _failure = ""
    if _prov is not None:
        try:
            _failure = _prov.settle_delivery(
                _turn, final, log=_hub_log_route(),
                session_id=str(kwargs.get("session_id") or ""),
                suppress_reason=_guard_reason)
        except Exception:  # noqa: BLE001
            _failure = ""
    _kept_main = bool(
        _turn is not None
        and str(getattr(_turn, "reason", "") or "") == "main_complied"
        and _turn.route_declared())
    if _failure in ("post_guard_suppressed", "main_complied") or (
            final is None and _kept_main):
        # The guard kept the agent's body, so the orchestrator's replacement is
        # NOT delivered. `None` would tell the host "deliver the body you gave
        # me" — the TAGGED original — and re-leak routing metadata the seam
        # already stripped. The delivered text is the seam's own passthrough:
        # the original minus the declaration tag. The `main_complied` keep
        # takes the same path: the bytes on the wire are main's, named, not a
        # host default.
        try:
            from ..features.detection import declaration as _decl_g

            _stripped_g, _tag_g = _decl_g.strip_tag(_original)
            if _tag_g is not None and str(_stripped_g) != str(_original):
                final = str(_stripped_g)
            else:
                final = str(_original or "")
        except Exception:  # noqa: BLE001 — keep the guard's answer
            final = str(_original or "")
        # The store was rewritten to the render by the seam; the wire keeps
        # the agent's body. Put the row back so transcript == delivery (the
        # M34-04 contract) instead of leaving the render in state.db as a
        # turn the user never read. A `main_complied` keep had no render to
        # undo, so the store is already the delivered body.
        if _failure == "post_guard_suppressed":
            try:
                from ..core import canonical as _can_g

                if _can_g.rewrite_persisted_turn(
                        str(kwargs.get("session_id") or ""),
                        str(out or ""), str(final or "")):
                    _lg = _hub_log_route()
                    if _lg is not None:
                        _lg("POST", event_detail="persisted_body_restored",
                            reason="render_suppressed",
                            session_id=str(kwargs.get("session_id") or ""))
            except Exception:  # noqa: BLE001 — restore never breaks delivery
                pass
    # M35-04 — THE DELIVERY BOUNDARY. Whatever produced `final` (lane render,
    # agent body, guard-suppressed passthrough, early bail), the bytes that
    # leave this function are scrubbed of marker text here. A surviving
    # fragment turns the turn into a loud withholding, never a leak.
    _marker_outcome = ""
    try:
        if final is None and _original:
            # The host is being told "deliver the body you gave me" — so the
            # ENTRY text is what ships, and it is the one a dropped replacement
            # leaves unexamined (the M35-02 identity-rewrite guard path). Scrub
            # it too; returning None while a marked original ships is the A2
            # leak with extra steps.
            _orig_clean, _orig_marker, _ = _sanitize_delivery(
                _original, session_id=str(kwargs.get("session_id") or ""))
            if _orig_marker:
                final = _orig_clean
                _marker_outcome = "marker_leak_withheld"
            elif str(_orig_clean) != str(_original):
                final = _orig_clean
        else:
            final, _marker_outcome, _marker_text = _sanitize_delivery(
                final, session_id=str(kwargs.get("session_id") or ""))
        if _marker_outcome and _prov is not None:
            try:
                _turn.withhold("marker_survived_boundary_strip")
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001 — the boundary never breaks delivery
        pass
    # M35-01: the provenance row, written on EVERY pass (routed or not) so a
    # benign turn's non-routing is on record and the lane can be graded.
    try:
        if _prov is not None:
            _prov.finish(_turn, delivered=final)
    except Exception:  # noqa: BLE001
        pass
    return final


# The plugin attribute IS the registered hook (identity pin:
# test_register_is_idempotent): the guarded entry is the only
# transform_llm_output the hub exposes.
on_transform_llm_output = on_transform_llm_output_guarded

_MOVABLE_ORCH_NAMES = tuple(
    n for n in dir(orchestration)
    if not n.startswith("__") and n != "_hub")


def hub_getattr(name: str):
    """P3a lazy export of the lifted names (dispatch: registration +
    __getattr__ for test-patched names only). Cached into globals so the
    attribute becomes real after first resolution (monkeypatch restore-safe:
    monkeypatch getattrs before setattr, which materializes the binding)."""
    try:
        value = getattr(orchestration, name)
    except AttributeError:
        raise AttributeError(
            f"module {_ROOT_NAME!r} has no attribute {name!r}") from None
    globals()[name] = value
    return value


def register(ctx) -> None:
    """Wire hooks + middleware + tools. Registration errors are isolated
    (P6 stage-1: the registration path migrates to core.telemetry.isolate —
    a failure emits the `router.swallow` warning row + per-gate counter
    surfaced via /router diag + router_status, never raised; a broken
    registration would disable the whole plugin in one profile)."""
    from ..core import telemetry as _t

    def _reg(gate: str, fn) -> None:
        _t.isolate(gate, fn, on_fail="pass")

    _reg("register.llm_request",
         lambda: ctx.register_middleware("llm_request", on_llm_request))
    _reg("register.llm_execution",
         lambda: ctx.register_middleware("llm_execution", on_llm_execution))
    _reg("register.transform_llm_output",
         lambda: ctx.register_hook("transform_llm_output",
                                   on_transform_llm_output_guarded))
    # M35-04: the TURN-CLOSE edge. post_llm_call observes the body the
    # finalizer is about to deliver, and it runs even when the host skipped
    # transform_llm_output — the edge that closes an otherwise unattributable
    # turn and catches a marker that reached the delivered bytes.
    _reg("register.post_llm_call",
         lambda: ctx.register_hook("post_llm_call",
                                   turn_close.on_post_llm_call))
    # R19.2 ADDENDUM 4: midturn decision hook SEAM 1 — fires after every
    # terminal tool result, mid-run. Registration failure never disables
    # other lanes.
    _reg("register.transform_terminal_output",
         lambda: ctx.register_hook("transform_terminal_output",
                                   on_transform_terminal_output))
    # R36g: REFLEX SEAM — every tool result, not just terminal.
    # transform_terminal_output is terminal-only by construction, so the reflex
    # could never see the same cause arrive through a second tool. post_tool_call
    # carries function_name + result for every tool. Reflex-only: fork detection
    # stays on the terminal seam untouched.
    def _reg_reflex_seam() -> None:
        from .. import decision_midturn

        ctx.register_hook("post_tool_call",
                          decision_midturn.on_post_tool_call)

    _reg("register.post_tool_call", _reg_reflex_seam)
    # v3.0.0: router control tools (phase 3) — registered defensively so a
    # tool registration failure never disables the middleware lanes.
    def _reg_tools() -> None:
        from .. import router_tools

        router_tools.register(ctx)

    _reg("register.router_tools", _reg_tools)
    # v3.5.0: /router chat command surface — LCM 3-branch pattern, env-gated
    # (HERMES_ROUTER_ENABLE_SLASH_COMMAND, default off). commands.
    # register_slash_command performs the collision self-check and the
    # flagship gateway-authz posture self-check (blueprint 6b.1) internally
    # and logs the one-line posture verdict.
    def _reg_slash() -> None:
        from .. import commands

        commands.register_slash_command(ctx)

    _reg("register.slash_command", _reg_slash)
    # P4 (proposal §2.3): LOAD-TIME VALIDATION pass — validate the whole
    # resolved config section once at plugin load; schema violations
    # (partial blocks / type mismatches) emit fail-loud telemetry rows
    # instead of surfacing as silent Nones mid-turn. Never raises.
    def _reg_validate() -> None:
        from ..core import config_access as _ca

        _viol = _ca.validate_config()
        if _viol:
            logger.warning(
                "hermes-router: config load validation found %d schema violation(s)",
                _viol)

    _reg("register.config_validation", _reg_validate)
    # P6 §2.7: seam liveness probe — register the fire counters (no extra
    # middleware; the on_* entries increment via seam_probe_fire).
    _reg("register.seam_probe", lambda: _t.seam_probe_register(ctx))
