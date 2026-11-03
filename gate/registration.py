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
from ..core import delivery_decision as _dd

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
    _prov = None
    _turn = None
    # M35-14: the closed-line grader is read ONCE, here, BEFORE the seam runs —
    # a closed line is a property of the ASK, so no render may be billed for it
    # and no case may open it later.
    _closed_hit = _closed_line_hit(kwargs)
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
    _original = kwargs.get("response_text", "") or ""
    # D3 lives at the DECISION (spec §2 P2), not before the seam: consulting
    # early would change which table row fires and therefore whether the lane
    # renders at all — the routing decision is the table's, and this
    # restructure changes only WHICH BODY IS DELIVERED. A withheld or
    # non-compliant turn still reaches decide(), which asks for the gate once
    # and re-enters (attempt=1) when the table's own row demanded a swap.
    _pre_attempt = 0
    try:
        if _prov is not None:
            _prov.seam_enter()
        # A HOLD_CLOSED ask never enters the seam: no render, no split, no
        # replacement. The decision below names the entered body unchanged.
        out = (None if _closed_hit
               else orchestration.on_transform_llm_output(**kwargs))
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
    # M35-14: THE DELIVERY EDGE OWNS THE ROW — and ONE decision names the
    # bytes. The facts record is built here, `decide()` is called (twice only
    # when D3 asks for the gate and then re-enters), and the row is written
    # from the bytes that decision named. No guard ladder, no pre-call window,
    # no post-hoc classification repair survives on this path.
    _decision = None
    _guard_reason = ""
    _failure = ""
    final = None
    try:
        _facts = _build_facts(_original, kwargs, _turn, out, _closed_hit,
                               attempt=_pre_attempt)
        _decision = _decide(_facts)
        if _decision.action == _dd.CONSULT_REENTER:
            _decision = _decide(_consult_gate(_facts, kwargs))
        final = _delivered_bytes(_decision, _original)
        if _decision.delivered_source == _dd.SOURCE_MAIN:
            _guard_reason = (_dd.REASON_MAIN_COMPLIED
                             if _decision.case_id == _dd.D4
                             else _decision.reason)
            _force_main_row(_turn, _decision)
        if _decision.action == _dd.FAIL_TERMINAL:
            _failure = "delivery_terminal_failure"
            try:
                if _turn is not None:
                    _turn.withhold("delivery_terminal_failure")
            except Exception:  # noqa: BLE001
                pass
        _log_decision(_decision, kwargs)
    except Exception as _dd_err:  # noqa: BLE001 — a seam bug is loud, not silent
        _decision = None
        final = ""
        _failure = "delivery_decision_error"
        try:
            _lg_err = _hub_log_route()
            if _lg_err is not None:
                _lg_err("POST", event_detail="delivery_decision_error",
                        severity="error", error=type(_dd_err).__name__,
                        detail=str(_dd_err)[:160],
                        session_id=str(kwargs.get("session_id") or ""))
        except Exception:  # noqa: BLE001
            pass
    if _prov is not None and _decision is not None:
        try:
            _failure = _prov.settle_delivery(
                _turn, final, log=_hub_log_route(),
                session_id=str(kwargs.get("session_id") or ""),
                suppress_reason=_guard_reason) or _failure
        except Exception:  # noqa: BLE001
            _failure = _failure or ""
    # A main-body delivery whose render already rewrote the store must put the
    # row back — transcript == delivery (the M34-04 contract), or the next turn
    # reads a body the user never saw.
    # M35-15: the reconciliation is no longer main-only. The capture that runs
    # INSIDE the render can persist text the delivery edge then strips (the
    # lane's attribution line is router bytes, so it never ships) — leaving the
    # transcript holding a body the user never read, the M34-04 defect one
    # layer down. Whenever the settled bytes differ from what the seam handed
    # back, the store is put back to `final`; canonical's own cascade matches
    # the row this router rewrote last, so the exact pre-strip text is not
    # needed here.
    if (_decision is not None
            and _turn is not None
            and isinstance(final, str) and isinstance(out, str)
            and str(out) != str(final)):
        try:
            from ..core import canonical as _can_g

            if _can_g.rewrite_persisted_turn(
                    str(kwargs.get("session_id") or ""),
                    str(out or ""), str(final or "")):
                _lg_r = _hub_log_route()
                if _lg_r is not None:
                    _lg_r("POST", event_detail="persisted_body_restored",
                          reason="delivery_decision_main",
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



# --------------------------------------------------------------------------- #
# M35-14 — THE DELIVERY DECISION EDGE (spec §1, §4, §5, §6)
# --------------------------------------------------------------------------- #
def _ask_for(kwargs) -> str:
    """The turn's ASK, recovered the same way the seam recovers it.

    Used by exactly two callers: the closed-line grader (a property of the ask,
    read before anything runs) and D3's consult. Never raises.
    """
    try:
        from ..core import state as _st

        sid = str(kwargs.get("session_id") or "")
        ctx = kwargs.get("context")
        ask = ""
        if isinstance(ctx, dict):
            ask = str(ctx.get("user_message") or "")
        return ask or str(_st.get_last_seen(sid) or "")
    except Exception:  # noqa: BLE001 — ask recovery never blocks a turn
        return ""


def _closed_line_hit(kwargs) -> str:
    """THE single call to `compound_split.hard_closed_class` (spec §5).

    Membership of the line list is untouched — this only collapses the two
    call sites (routed edge + benign edge) into one fact read before delivery.
    A grader gap returns "" and the turn proceeds; the decision then carries
    `unjudged` and the ledger counts it. Never raises.
    """
    try:
        from ..features.detection import compound_split as _cs

        return str(_cs.hard_closed_class(_ask_for(kwargs)) or "")
    except Exception:  # noqa: BLE001 — a gap opens no line and closes none
        return ""


def _build_facts(entered, kwargs, turn, lane_render, closed_hit, attempt: int = 0):
    """Build the frozen facts record ONCE at the delivery edge (spec §1.1).

    `gate_consulted` has NO default-true: it is True only when a gate record
    for THIS turn exists (`domain_gate.read` already enforces the turn binding
    and the TTL). Absence is False; "we assume the gate ran" is not
    representable.
    """
    from ..features.detection import domain_gate as _dgate
    from .. import post_guards as _pg

    sid = str(kwargs.get("session_id") or "")
    clean, _removed = _dd.strip_markers(entered)
    row = None
    try:
        row = _dgate.read(sid)
    except Exception:  # noqa: BLE001 — an unreadable gate is an absent gate
        row = None
    consulted = isinstance(row, dict)
    verdict = _dgate.verdict(row) if consulted else "abstain"
    reason = str((row or {}).get("reason") or "") or (
        "no_record" if consulted else "not_consulted")
    rule_id = "none"
    try:
        rule_id = str(getattr(turn, "rule", "") or "none")
    except Exception:  # noqa: BLE001
        rule_id = "none"
    seam_failure = ""
    try:
        from ..core import delivery_integrity as _dintf

        if isinstance(lane_render, str) and lane_render.startswith(
                _dintf.FAILURE_PREFIX):
            seam_failure = "transcript_divergence"
    except Exception:  # noqa: BLE001 — recognition never blocks a turn
        seam_failure = ""
    return _dd.DeliveryFacts(
        seam_failure=seam_failure,
        entered_body=str(entered or ""),
        body_class=_pg.classify_original(clean),
        gate_consulted=consulted,
        gate_verdict=verdict,
        gate_reason=reason,
        rule_id=rule_id,
        closed_line_hit=str(closed_hit or ""),
        lane_render=(None if lane_render is None else str(lane_render)),
        attempt=int(attempt or 0),
    )


def _decide(facts):
    """THE single decision call site in the plugin (spec §4.3).

    The static test asserts this is the only textual `decide(` in the module,
    so no second suppression path can grow beside it.
    """
    return _dd.decide(facts)


def _consult_gate(facts, kwargs):
    """D3's bounded consult: ask the gate, then re-enter exactly once.

    Failure is `abstain` with a named reason — `abstain` is a FAILURE and falls
    to the body ladder, never read as "safe". Never raises.
    """
    from ..features.detection import domain_gate as _dgate

    sid = str(kwargs.get("session_id") or "")
    ask = _ask_for(kwargs)
    # The gate module's own worker: evaluate, then WRITE the turn record
    # (`mark_pre_domain`, turn-bound + TTL-bound). Landed synchronously and
    # bounded by evaluate's own timeout — a consult that cannot answer leaves
    # `abstain`, which is a FAILURE and falls to the body ladder, never "safe".
    try:
        _dgate._worker(sid, ask, None)  # noqa: SLF001 — the module's own half
    except Exception:  # noqa: BLE001 — an unanswerable gate is a failure
        pass
    row = None
    try:
        row = _dgate.read(sid)
    except Exception:  # noqa: BLE001
        row = None
    verdict = _dgate.verdict(row) if isinstance(row, dict) else "abstain"
    reason = str((row or {}).get("reason") or "") or _dd.REASON_GATE_UNAVAILABLE
    return _dd.reenter_after_consult(facts, gate_verdict=verdict,
                                     gate_reason=reason)


def _delivered_bytes(decision, entered: str = "") -> str:
    """The exact bytes this decision named. Nothing composes a body here.

    `None` is the host's documented pass-through contract — "deliver the body
    you gave me". It is returned ONLY when the decision chose the entered body
    unmodified (`source=main`, `strip_bytes=0`), because then the passthrough
    IS the bytes `decide()` named, byte for byte. Any other choice — a lane
    render, an attributed strip, a hold — returns explicit bytes: the seam may
    never let the host deliver something the decision did not name.
    """
    if decision is None:
        return ""
    if decision.action == _dd.FAIL_TERMINAL:
        # A decision that NAMED bytes (the seam's own failure body) ships
        # them: that body is the visible failure, not a substitute answer.
        if decision.body:
            return str(decision.body)
        # M35-15: FAIL_TERMINAL has TWO wire outcomes, because the two cases
        # fail for different reasons and the host contract treats "" and None
        # identically (turn_finalizer.py:558 — only a NON-EMPTY string
        # replaces; ""/None both leave the model's text in place).
        #
        #   * D6 on a real, non-void body (a refusal, a hedge, a scaffold the
        #     agent actually wrote, no admissible lane): there ARE bytes and
        #     they are the agent's own. The spec forbids substituting router
        #     prose for an answer, and this seam has no channel to withhold a
        #     body it did not replace — so the honest statement of "no router
        #     answer" is the pass-through: None. The turn still closes as a
        #     FAILED turn (provenance withholds it and the decision event is
        #     logged at error severity); what it does not do is lie about
        #     having produced bytes it never wrote.
        #   * D2 (nothing admissible) and a marker violation (D6 by
        #     post-condition) have NO shippable bytes: passing those through
        #     would deliver void text or leak routing metadata. Those ship the
        #     one non-empty string that is a failure NOTICE, never an answer.
        if (decision.case_id == _dd.D6
                and decision.reason != _dd.REASON_MARKER_VIOLATION):
            return None
        return WITHHELD_BODY
    if decision.body is None:
        return ""
    body = str(decision.body)
    if (decision.delivered_source == _dd.SOURCE_MAIN
            and int(decision.strip_bytes) == 0
            and body == str(entered or "")):
        return None
    return body


def _force_main_row(turn, decision) -> None:
    """A main-body delivery is `main`/replaced=False on the row, whoever wrote
    the store. Without this the ledger could claim a lane body the caller never
    received — the M35-02 defect one layer up."""
    if turn is None:
        return
    try:
        turn.replaced = False
        turn.lane = "main"
        turn.replacement_path = ""
        turn.reason = str(decision.reason or "")
        turn.delivered = True
    except Exception:  # noqa: BLE001 — instrumentation never breaks a turn
        pass


def _log_decision(decision, kwargs) -> None:
    """One `delivery_decision` event per delivery edge (spec §10)."""
    try:
        lg = _hub_log_route()
        if lg is None or decision is None:
            return
        lg("POST", event_detail="delivery_decision",
           **decision.as_record(),
           session_id=str(kwargs.get("session_id") or ""))
    except Exception:  # noqa: BLE001 — observability never breaks delivery
        pass


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
