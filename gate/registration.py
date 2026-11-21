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
import time
from typing import Dict

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


#: M53-02 (round 6): dedupe for the BOUNDARY attestation record. Keyed on
#: (session, body length) so a repeated pass of the same body never writes a
#: second row; bounded, because a session list must not grow forever.
_BOUNDARY_ATT_LOGGED: set = set()
#: Set by the orchestration seam when it writes its own `attestation_turn` row.
#: The boundary writes a fallback row ONLY when this is absent: a skipped
#: classification must still record the type, but a real seam row must never be
#: shadowed by the fallback (attribution is the pilot's whole measurement).
_SEAM_ATT_SEEN: set = set()


def _note_seam_attestation(session_id: str, body_len: int) -> None:
    """Mark that the POST seam recorded this body's attestation."""
    try:
        _SEAM_ATT_SEEN.add((str(session_id or ""), int(body_len)))
        if len(_SEAM_ATT_SEEN) > 2048:
            _SEAM_ATT_SEEN.clear()
    except Exception:  # noqa: BLE001 — bookkeeping never blocks
        pass


def _hub_dispatcher_armed() -> bool:
    """True when this profile is inside the attestation pilot.

    Read through the root package's `_dispatcher_pre`, exactly as the
    orchestration seam does. Fail-open False: an unreadable config is
    UNARMED, never a reason to stamp an attestation record that did not
    happen.
    """
    try:
        root = _sys.modules.get(_ROOT_NAME)
        pre = getattr(root, "_dispatcher_pre", None) if root is not None else None
        armed = getattr(pre, "attestation_armed", None)
        return bool(armed()) if callable(armed) else False
    except Exception:  # noqa: BLE001 — arming must never break delivery
        return False


#: M35-04 — the loud-failure body. Reached ONLY when marker text survived the
#: strip: the turn is withheld rather than delivered, because routing metadata
#: in a user's answer is a leak of internal plumbing, not a formatting wobble.
WITHHELD_BODY = (
    "[hermes-router] This turn was withheld: internal routing metadata reached "
    "the delivery boundary and could not be removed. No answer text was "
    "released. Re-run the ask."
)


def _drop_residual_marker_lines(text, decl, *, max_passes: int = 6) -> str:
    """Remove surviving marker FRAGMENTS line-wise. Returns the shippable body.

    Owner ruling 2026-11-07: routing metadata must never ship, but it must never
    cost the operator the answer either. A truncated or malformed marker
    fragment used to replace the WHOLE delivered body with the withhold notice,
    which turns a formatting residue into a swallowed turn. This drops only the
    line(s) still carrying a fragment and returns what remains; an empty string
    means nothing shippable survived and the caller withholds.

    Never raises.
    """
    try:
        body = str(text or "")
        for _ in range(max_passes):
            if not decl.residual_marker(body):
                return body.lstrip("\n")
            lines = body.splitlines()
            kept = [ln for ln in lines if not decl.residual_marker(ln)]
            if len(kept) == len(lines):
                # The fragment spans a line boundary or is embedded mid-line:
                # excise the reported span itself rather than dropping the line.
                frag = decl.residual_marker(body)
                if not frag:
                    return body.lstrip("\n")
                body = body.replace(frag, "")
            else:
                body = "\n".join(kept)
        return body.lstrip("\n") if not decl.residual_marker(body) else ""
    except Exception:  # noqa: BLE001 — a strip failure must not break a turn
        return ""


def _sanitize_delivery(text, *, session_id: str = "") -> tuple:
    """Strip marker text at the DELIVERY BOUNDARY. (body, outcome, marker).

    The strip inside the orchestrator is one seam on ONE path; this one sits on
    the edge where the bytes are settled, so it also covers the paths that never
    entered the routing table (router disabled, guard fail-open, passthrough
    early return). Well-formed tag lines are removed; a surviving fragment is
    now REMOVED LINE-WISE and the remainder is delivered. Withholding is the
    last resort, used only when nothing shippable survives. Never raises: an
    unreadable body is delivered unchanged by the caller's judgement, never by
    an exception.
    """
    try:
        from ..features.detection import declaration as _decl

        # `None` is the pass-through contract ("deliver the body you gave me"),
        # not an empty body: sanitizing it to "" would blank the user's answer.
        if text is None:
            return None, "", ""
        # M40-07: the whole-block strip runs BEFORE the cheap fast path. The
        # fast path keys on the declaration tag alone, and a router reading
        # block carries no `<turn`, so reading text shipped to the user
        # un-scanned. Block-level is mandatory here: the line-wise drop below
        # leaves the block's BODY behind once its header line is removed.
        try:
            from .. import frames as _frames

            if isinstance(text, str) and text:
                stripped = _frames.strip_platform_rule_blocks(text)
                if stripped != text:
                    if not stripped.strip():
                        lg = _hub_log_route()
                        if lg is not None:
                            try:
                                lg("POST", event_detail="marker_leak_withheld",
                                   reason="body_was_only_platform_block",
                                   marker="[PLATFORM RULE: "[:32],
                                   severity="error",
                                   session_id=str(session_id or ""))
                            except Exception:  # noqa: BLE001 — telemetry never breaks delivery
                                pass
                        return (WITHHELD_BODY, "marker_leak_withheld",
                                "[PLATFORM RULE: ")
                    text = stripped
        except Exception:  # noqa: BLE001 — the strip must never break a turn
            pass
        # Cheap fast path: no candidate marker ⇒ nothing to strip. The scan
        # below is line-wise regex work on every delivered body, and the
        # boundary sits on the delivery path.
        if "<turn" not in str(text).lower():
            return text, "", ""
        clean, marker = _decl.scrub_markers(text)
        if marker:
            # GRACEFUL DEGRADATION (owner ruling 2026-11-07). Strip the
            # offending line(s) and ship the answer. The old behaviour swapped
            # the entire body for WITHHELD_BODY ("No answer text was released.
            # Re-run the ask.") — a routing residue billed to the operator.
            degraded = _drop_residual_marker_lines(clean, _decl)
            lg = _hub_log_route()
            if str(degraded or "").strip():
                if lg is not None:
                    try:
                        lg("POST", event_detail="marker_fragment_removed",
                           reason="marker_survived_boundary_strip",
                           marker=str(marker)[:32],
                           severity="warning",
                           dropped_chars=max(0, len(str(clean or "")) - len(degraded)),
                           session_id=str(session_id or ""))
                    except Exception:  # noqa: BLE001 — telemetry never breaks delivery
                        pass
                return degraded, "", ""
            # Nothing shippable survived — withhold is the LAST resort, not the
            # first response to a fragment.
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


#: M45-10 — the distinguishable state for a turn that has NO text in scope
#: after a delivery-decision failure. Never "": an empty string is
#: indistinguishable from a legitimate empty answer AND from the swallow this
#: marker exists to expose, so the no-text case gets its own named bytes.
NO_TEXT_AFTER_DECISION_FAILURE = (
    "[hermes-router] Delivery decision failed and this turn produced no text "
    "to fall back to. The failure is recorded in the router ledger."
)


def _decision_failure_marker(err) -> str:
    """Name a swallowed decision-block exception: type + repr head (M45-10)."""
    try:
        return "delivery_decision_error:%s:%s" % (
            type(err).__name__, repr(err)[:120])
    except Exception:  # noqa: BLE001 — naming a failure never raises
        return "delivery_decision_error:unprintable"


def _fallback_after_decision_error(out, original):
    """The body that ships when the decision block raised (M45-10).

    Prefers text that ALREADY EXISTS at that point — the seam's own output
    first, then the host's entered body — and falls back to an explicit
    no-text state. Never returns "": a turn that produced text must not ship an
    empty message, and a turn that produced none must be distinguishable from
    a normal empty answer.
    """
    for _cand in (out, original):
        if isinstance(_cand, str) and _cand.strip():
            return _cand
    return NO_TEXT_AFTER_DECISION_FAILURE


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
    # M53-03 R20: when the ARMED guard supplied this hit, the wrapper claims the
    # turn BEFORE orchestration runs, so orchestration's own guard row never
    # writes. Emit it here: the record must survive the shortcut ("detect all"
    # — the guard changes the ACTION, never the record).
    if _closed_hit and _hub_dispatcher_armed():
        try:
            from ..features.detection import closed_line as _cl_row
            _lg_cl = _hub_log_route()
            if _lg_cl is not None:
                _lg_cl("POST", event_detail="closed_line_blocked",
                       kind=_closed_hit,
                       reason=_cl_row.reason_for(_closed_hit),
                       domain="", replace_ms=0, render_emitted=0,
                       where="delivery_edge",
                       session_id=str(kwargs.get("session_id") or ""))
        except Exception:  # noqa: BLE001 — telemetry only
            pass
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
    # ------------------------------------------------------------------
    # M53-02 (round 6): THE UNCONDITIONAL BOUNDARY.
    #
    # Every earlier fix hung the strip off a path that can be SKIPPED — the
    # orchestration seam, the decision path, the lane branch. Measured: 9 of
    # 19 armed denials still shipped the tag, including turns with no
    # `delivery_decision` row at all (the decision never ran) and turns whose
    # POST classification was skipped. The tag is metadata this pilot itself
    # injects at PRE, so it is removed HERE — at the wrapper's entry, before
    # the closed-line short-circuit, before orchestration, before any
    # classification, route or decision. Nothing below can run earlier.
    #
    # Both forms, every position: `scrub_markers` removes well-formed tag
    # lines anywhere in the body, the unterminated cap fragment, and both tag
    # forms; `strip_markers` (used by the decision) agrees with it.
    # ------------------------------------------------------------------
    _original_raw = _original
    #: True when this seam removed routing metadata from the entered body. The
    #: delivery edge MUST then return explicit bytes: `None` is the host
    #: pass-through, and the host's copy is the raw, tag-bearing body.
    _boundary_stripped = False
    try:
        from ..features.detection import declaration as _decl_bound

        _bound_clean, _bound_marker = _decl_bound.scrub_markers(_original)
        if isinstance(_bound_clean, str) and _bound_clean != _original:
            if _bound_clean.strip():
                _original = _bound_clean
                # NOTE: `kwargs` is deliberately NOT rewritten. Orchestration
                # must still see the RAW body — it parses the attestation there
                # and decides the route from it. Only the delivery accounting
                # (`_original` = the bytes the decision and the boundary work
                # from) is cleaned here; `_boundary_stripped` forces the edge to
                # return these bytes explicitly instead of the None pass-through.
                _boundary_stripped = True
            try:
                _lg_b = _hub_log_route()
                if _lg_b is not None:
                    _lg_b("POST", event_detail="boundary_tag_stripped",
                          strip_bytes=max(0, len(_original_raw)
                                          - len(_original)),
                          marker=str(_bound_marker or "")[:32],
                          session_id=str(kwargs.get("session_id") or ""))
            except Exception:  # noqa: BLE001 — telemetry never blocks
                pass
        # The attestation is parsed from the body the model produced, so it is
        # recorded even when POST classification never runs. The record is
        # written at the END of this seam (see `_boundary_att_below`) and only
        # when the POST seam did not write one — attribution first.
        pass
    except Exception:  # noqa: BLE001 — the boundary never breaks delivery
        _original = _original_raw
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
        if _closed_hit:
            # M45-17: the object the host invokes IS this wrapper. A HOLD_CLOSED
            # ask returns before the orchestrator, so the seam counter must be
            # taken here — otherwise a closed-line turn reads as a dead seam.
            try:
                from ..core.telemetry import seam_probe_fire as _spf

                _spf("transform_llm_output")
            except Exception:  # noqa: BLE001 — probe never breaks the seam
                pass
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
        # M50-41 (owner behavioural model): the agent's response is a DRAFT the
        # POST consult intercepts. When the agent's own model adjusted that
        # draft after seeing the advisory, the ADJUSTED body is the final answer
        # and the draft must never be a delivery candidate.
        #
        # Provenance comes from the audit's stamp, never from `out` alone: `out`
        # is the aggregated transform output, so without the tag any lane render
        # could masquerade as an agent adjustment and re-open M34-04. Any
        # failure here fails open to the draft — the router never blocks a turn.
        _entered_dec = _original
        try:
            from ..features.banners import turn_record as _tr_dec
            if (_tr_dec.main_adjusted(str(kwargs.get("session_id") or ""))
                    and isinstance(out, str) and out.strip()):
                _entered_dec = out
        except Exception:  # noqa: BLE001 — fail open to the draft
            _entered_dec = _original
        try:
            from ..features.detection import closed_line as _cl_g
            _ge = _cl_g.take_guard_error(
                str(kwargs.get("session_id") or ""),
                str(kwargs.get("turn_id") or ""))
        except Exception:  # noqa: BLE001
            _ge = False
        if _ge:
            # a detector hiccup must never hand the turn to the lane
            out = None
        try:
            _decl_strip_extra = orchestration.take_decl_strip(
                str(kwargs.get("session_id") or ""))
        except Exception:  # noqa: BLE001
            _decl_strip_extra = 0
        # R41 item 3: if this turn's own scope recorded a render body, the
        # decision sees it. Absent -> "" and today's behaviour stands exactly.
        try:
            from ..core import state as _st_r
            _scoped_render = _st_r.read_render_for_scope(
                _st_r.scope_for(str(kwargs.get("session_id") or "")) or "")
        except Exception:  # noqa: BLE001 — never breaks delivery
            _scoped_render = ""
        _lane_in = out
        if not (isinstance(_lane_in, str) and _lane_in.strip()) and _scoped_render.strip():
            _lane_in = _scoped_render
        _facts = _build_facts(
            _entered_dec, kwargs, _turn, _lane_in, _closed_hit,
            attempt=_pre_attempt,
            # M53-02 (round 9): the strip count is carried, not recomputed.
            # The boundary already knows what it deleted (51 bytes); re-running
            # an idempotent strip downstream is how the count read 0 and the
            # host shipped its own pre-strip copy.
            prior_strip_bytes=(_decl_strip_extra
                               + (max(0, len(_original_raw) - len(_original))
                                  if _boundary_stripped else 0)))
        _decision = _decide(_facts)
        if _decision.action == _dd.CONSULT_REENTER:
            _decision = _decide(_consult_gate(_facts, kwargs))
        final = _delivered_bytes(_decision, _original)
        # A: a turn whose tag was stripped UPSTREAM has already-clean bytes; the
        # decision's "nothing changed" pass-through (None) would hand the host
        # its own tag-bearing original. Return the stripped bytes explicitly.
        if final is None and _decl_strip_extra > 0 and _original:
            final = str(_original)
        # M53-03 R20: a closed-line claim must DELIVER the agent's own body as
        # explicit bytes. `_delivered_bytes` returns None for a byte-identical
        # source=main decision (the host pass-through), which on a HOLD_CLOSED
        # reads as "no decision" and let the contract be satisfied only by
        # luck. The claim names these bytes; hand them back.
        if _decision is not None and final is None:
            try:
                from ..core import delivery_decision as _ddm
                if _decision.action == _ddm.HOLD_CLOSED and _original:
                    final = str(_original)
            except Exception:  # noqa: BLE001 — never breaks delivery
                pass
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
        # M45-10: a seam bug must never become an empty body. Text already
        # exists in scope here (the seam's `out`, or the host's `_original`),
        # so ship THAT over a finished turn instead of "". Genuinely no text is
        # its own named state, not an empty string that reads like a normal
        # empty answer.
        _decision = None
        final = _fallback_after_decision_error(out, _original)
        _failure = _decision_failure_marker(_dd_err)
        try:
            if _turn is not None:
                # withhold() keeps lane/delivered honest; stamping the marker
                # into `outcome` is what makes the swallow NAME itself in the
                # provenance row instead of hiding behind a generic token.
                _turn.withhold(_failure)
                _turn.outcome = _failure
        except Exception:  # noqa: BLE001
            pass
        try:
            _lg_err = _hub_log_route()
            if _lg_err is not None:
                _lg_err("POST", event_detail="delivery_decision_error",
                        severity="error", error=type(_dd_err).__name__,
                        detail=str(_dd_err)[:160],
                        marker=_failure,
                        fallback_chars=len(final or ""),
                        session_id=str(kwargs.get("session_id") or ""))
        except Exception:  # noqa: BLE001
            pass
    if _prov is not None and _decision is not None:
        try:
            # M45-12: the settle cannot tell a legal suppression from a lost
            # body by looking at "" — the DECISION knows. D1 HOLD_CLOSED named
            # no bytes on purpose; every other empty body is a failure.
            _failure = _prov.settle_delivery(
                _turn, final, log=_hub_log_route(),
                session_id=str(kwargs.get("session_id") or ""),
                suppress_reason=_guard_reason,
                hold_closed=bool(_decision.action == _dd.HOLD_CLOSED)) or _failure
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
            elif _boundary_stripped:
                # M53-02 (round 6): the seam ALREADY cleaned the entered body.
                # Returning None here means "deliver the body you gave me" —
                # and the host's copy is the RAW, tag-bearing one, which is
                # precisely how the leak shipped. Explicit bytes, always.
                final = _original
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
    # M53-02 (round 6): BOUNDARY ATTESTATION FALLBACK. When POST classification
    # never runs (complexity_skip / bare call), no `attestation_turn` row is
    # written anywhere and the type is simply absent from the sweep. The body
    # still carries the tag, so parse it here — but only when the seam did not
    # already record this exact body, so a real seam row is never shadowed.
    try:
        from ..features.detection import attestation as _att_b2

        _sid_b = str(kwargs.get("session_id") or "")
        _key_b = (_sid_b, len(_original_raw))
        if (_hub_dispatcher_armed() and _key_b not in _SEAM_ATT_SEEN
                and _key_b not in _BOUNDARY_ATT_LOGGED):
            _att_b = _att_b2.parse(_original_raw)
            if _att_b.get("valid"):
                _BOUNDARY_ATT_LOGGED.add(_key_b)
                _lg_b2 = _hub_log_route()
                if _lg_b2 is not None:
                    _lg_b2("POST", event_detail="attestation_turn",
                           armed=1, where="boundary",
                           agent_domain=_att_b.get("domain") or "",
                           agent_type=_att_b.get("type") or "",
                           attestation_mismatch=0, declaration_retry=0,
                           declaration_reason="",
                           # M53-05 R17 (fix B): this row can only exist
                           # because a legacy tag was parsed out of the body —
                           # naming the channel keeps it from reading as
                           # out-of-band coverage.
                           channel="text_belt", channel_reason="",
                           route_fired=0, replace=0, envelope_emitted=0,
                           delivered_chars=len(str(_original or "")),
                           draft_chars=len(_original_raw),
                           session_id=_sid_b)
    except Exception:  # noqa: BLE001 — the record never blocks delivery
        pass
    # M46-03 (owner ruling, reverses M35-17) — VISIBILITY AT FINAL RESPONSE.
    # The api_server seam returns None to mean "deliver the bytes you gave me",
    # so the lifecycle chokepoint never sees those bytes. This is the last
    # place that holds them: compose the turn's ONE banner line here, from the
    # record, so the delivered bytes and the record cannot disagree.
    try:
        from ..features.banners import turn_record as _tr_vis

        _sid_vis = str(kwargs.get("session_id") or "")
        _payload = (final if isinstance(final, str)
                    else (_original if isinstance(_original, str) else None))
        if _payload is not None:
            _composed = _tr_vis.compose_onto(_sid_vis, _payload)
            if isinstance(_composed, str) and _composed != _payload:
                final = _composed
    except Exception:  # noqa: BLE001 — a composer never withholds a turn
        pass
    # M35-17 — THE ONE EMIT POINT (§2.1). After the boundary strip has produced
    # the exact bytes leaving this function, and before the provenance row
    # closes: this is the only place that knows the segments, the decision and
    # the bytes together. `record_turn` returns nothing, so nothing here can be
    # appended to a body.
    try:
        from ..features.banners import turn_record as _tr_rec
        from .. import debug_banner as _db_lvl

        _lvl = 1 if _db_lvl.debug_banner_enabled() else 0
        _tr_rec.record_turn(
            str(kwargs.get("session_id") or ""),
            _tr_rec.turn_no(str(kwargs.get("session_id") or "")),
            final, _decision,
            _tr_rec.segments(str(kwargs.get("session_id") or "")),
            knob_level=_lvl)
    except Exception:  # noqa: BLE001 — a record never withholds a turn
        pass
    # M35-01: the provenance row, written on EVERY pass (routed or not) so a
    # benign turn's non-routing is on record and the lane can be graded.
    try:
        if _prov is not None:
            _prov.finish(_turn, delivered=final)
    except Exception:  # noqa: BLE001
        pass
    # M53-02 (round 11): SHIPPED-BYTE OBSERVABILITY + LAST-MILE GUARD.
    #
    # Measured: the strip produced the right short body (`delivered -
    # strip_bytes == refusal_chars`) and the platform still shipped the
    # pre-strip bytes — so the next sweep must be able to tell "the decision
    # was wrong" from "the platform shipped another copy" WITHOUT inference.
    # These two rows are that instrument.
    #
    # The guard is the one thing this seam controls: if this pass stripped a
    # tag and the settled value is None (the documented host pass-through,
    # which means the host ships ITS OWN copy) or still carries marker text,
    # hand back the stripped body explicitly instead.
    try:
        _ship = final
        if _boundary_stripped and (not isinstance(_ship, str)
                                   or not _ship.strip()
                                   or "<turn" in str(_ship).lower()):
            _ship = _original
        if _ship is not final:
            final = _ship
        _lg_ship = _hub_log_route()
        if _lg_ship is not None:
            _lg_ship(
                "POST", event_detail="shipped_bytes",
                shipped_chars=(len(final) if isinstance(final, str) else 0),
                shipped_has_tag=int("<turn" in str(final or "").lower()),
                shipped_is_none=int(final is None),
                boundary_stripped=int(bool(_boundary_stripped)),
                decision_case=str(getattr(_decision, "case_id", "") or ""),
                session_id=str(kwargs.get("session_id") or ""))
    except Exception:  # noqa: BLE001 — the instrument never breaks delivery
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
        from ..passes import dispatcher_pre as _pre

        _armed = bool(_pre.attestation_armed())
    except Exception:  # noqa: BLE001 — an unreadable arming reads as unarmed
        _armed = False

    if not _armed:
        # UNARMED: byte-identical behaviour. No verdict store is consulted and
        # no guard exception can ever claim a turn.
        try:
            from ..features.detection import compound_split as _cs
            from ..features.detection import closed_line as _cl_u

            _hard = str(_cs.hard_closed_class(_ask_for(kwargs)) or "")
            return _cl_u.canonical_kind(_hard) if _hard else ""
        except Exception:  # noqa: BLE001
            return ""

    from ..features.detection import closed_line as _cl

    _sid = str(kwargs.get("session_id") or "")
    _tid = str(kwargs.get("turn_id") or "")

    # B: ONE verdict per turn. If the seam already classified, read it — do not
    # classify again from a different ask-recovery path.
    _prev = _cl.read_verdict(_sid, _tid)
    if _prev is not None:
        return str(_prev.get("kind") or "") if _prev.get("blocked") else ""

    # ABSENCE IS LOUD: a missing verdict must be a readable counter, never a
    # silent reopen of the D5 lane hole.
    try:
        _lg_a = _hub_log_route()
        if _lg_a is not None:
            _lg_a("POST", event_detail="closed_line_verdict_absent",
                  reason=_cl.absence_reason(_sid, _tid),
                  key_path=_cl.verdict_path(_tid),
                  session_id=_sid)
    except Exception:  # noqa: BLE001 — telemetry only
        pass

    # hard_closed_class is an ADDITIONAL contributor — a hit is still a hit —
    # never the only source: it fires on 6 of the 28 closed lines.
    try:
        from ..features.detection import compound_split as _cs

        _hard = str(_cs.hard_closed_class(_ask_for(kwargs)) or "")
    except Exception:  # noqa: BLE001
        _hard = ""
    _kind_h = _cl.canonical_kind(_hard) if _hard else ""

    try:
        from ..features.detection import attestation as _attm

        _decl = _attm.read_declaration(_sid, _tid) if _tid else {}
        _blocked, _kind, _reason = _cl.classify(
            _ask_for(kwargs),
            str((_decl or {}).get("domain") or ""),
            str((_decl or {}).get("type") or ""))
        if _kind_h and not _blocked:
            # a hard-detector hit can only ADD a hold, never remove one
            _blocked, _kind, _reason = True, _kind_h, _cl.reason_for(_kind_h)
        # D: the key contract is explicit — session-only when turn_id is empty,
        # never silently reported as an attested-turn verdict.
        _cl.note_verdict(_sid, _tid, _blocked, _kind, _reason,
                         "edge_" + _cl.verdict_path(_tid))
        return str(_kind or "") if _blocked else ""
    except Exception:  # noqa: BLE001 — fail SAFE in both directions, not by
        # claiming: do not let the lane substitute, do not skip the record.
        _cl.note_guard_error(_sid, _tid)
        try:
            _lg_e = _hub_log_route()
            if _lg_e is not None:
                _lg_e("POST", event_detail="closed_line_guard_error",
                      reason="guard_error", where="delivery_edge",
                      key_path=_cl.verdict_path(_tid),
                      session_id=_sid)
        except Exception:  # noqa: BLE001 — telemetry only
            pass
        return ""          # no D1 claim: delivery MAIN still records


def _build_facts(entered, kwargs, turn, lane_render, closed_hit, attempt: int = 0,
                 prior_strip_bytes: int = 0):
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

        _recovered = _dintf.take_recovered(str(kwargs.get("session_id") or ""))
        if isinstance(lane_render, str) and (
                lane_render.startswith(_dintf.FAILURE_PREFIX)
                or (lane_render.strip() and lane_render == _recovered)):
            # M45-19: recovery is still a SEAM-DECLARED failure (the audit
            # stands) — but the bytes it names are the transcript text that
            # already existed, never the notice. Recognising the recovered
            # body here is what stops D4 from replacing it with the model's
            # fresh text one layer down.
            seam_failure = "transcript_divergence"
    except Exception:  # noqa: BLE001 — recognition never blocks a turn
        seam_failure = ""
    try:
        from .. import assimilation as _asm
        _accept = _asm.last_acceptance(sid)
    except Exception:  # noqa: BLE001 — an unreadable token is "" (unknown)
        _accept = ""
    return _dd.DeliveryFacts(
        seam_failure=seam_failure,
        lane_acceptance=_accept,
        ask_text=_ask_for(kwargs),
        entered_body=str(entered or ""),
        body_class=_pg.classify_original(clean),
        gate_consulted=consulted,
        gate_verdict=verdict,
        gate_reason=reason,
        rule_id=rule_id,
        closed_line_hit=str(closed_hit or ""),
        lane_render=(None if lane_render is None else str(lane_render)),
        attempt=int(attempt or 0),
        # M53-02 (round 9): the boundary's counted delta rides the facts.
        prior_strip_bytes=int(prior_strip_bytes or 0),
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
    # M45-12: `""` below is the honest WIRE bytes of a decision that named
    # none (a D1 hold whose stripped body is empty) — the host keeps its own
    # body. It is not a delivery, and the settle that records it must say so.
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
        if decision.reason == _dd.REASON_MARKER_VIOLATION:
            # M50-19: NEVER swallow the turn. Owner ruling: the router cannot
            # decide a user's answer was not delivered because the router found
            # routing metadata in it. Prefer the model's own bytes with the
            # marker LINES removed; if nothing at all survives, hand the host
            # its own body (None = documented pass-through) rather than a
            # withhold notice. The withhold notice is the one outcome this
            # branch no longer produces.
            try:
                _scrubbed = _dd.strip_marker_lines(entered or "")
            except Exception:  # noqa: BLE001
                _scrubbed = ""
            if str(_scrubbed or "").strip():
                return _scrubbed
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

#: M53-03 item 2 — one delivery decision per turn, whichever edge observes it.
_DECIDED: Dict[tuple, float] = {}
_DECIDED_TTL_S = 600.0
_DECIDED_MAX = 512


def _decision_key(session_id: str, turn_id: str = "") -> tuple:
    """The (session, turn) join key for the once-per-turn decision mark."""
    sid = str(session_id or "")
    tid = str(turn_id or "")
    if not tid:
        try:
            from ..core import state as _st_key
            tid = str(_st_key.current_turn_id(sid) or "")
        except Exception:  # noqa: BLE001 — an unknown turn key is session-scoped
            tid = ""
    return (sid, tid)


def _mark_decided(session_id: str, turn_id: str = "") -> None:
    """Record that this turn's ONE delivery decision has been written."""
    try:
        now = time.time()
        for _k, _ts in list(_DECIDED.items()):
            if now - float(_ts or 0.0) > _DECIDED_TTL_S:
                _DECIDED.pop(_k, None)
        _DECIDED[_decision_key(session_id, turn_id)] = now
        while len(_DECIDED) > _DECIDED_MAX:
            _DECIDED.pop(next(iter(_DECIDED)))
    except Exception:  # noqa: BLE001 — bookkeeping never blocks delivery
        pass


def decision_logged(session_id: str, turn_id: str = "") -> bool:
    """True when this turn already produced its delivery decision."""
    try:
        return _decision_key(session_id, turn_id) in _DECIDED
    except Exception:  # noqa: BLE001
        return False


def reset_decided_for_tests() -> None:
    """Clear the once-per-turn decision marks (test isolation only)."""
    try:
        _DECIDED.clear()
    except Exception:  # noqa: BLE001
        pass


def _log_decision(decision, kwargs, extra=None) -> None:
    """One `delivery_decision` event per delivery edge (spec §10)."""
    try:
        lg = _hub_log_route()
        if lg is None or decision is None:
            return
        fields = dict(extra or {})
        if "task_id" not in fields:
            # M53-06 §4: PRE and POST must join per turn, or no rate for this
            # feature is computable. Derived by the module that owns the key.
            try:
                from .. import router_core as _rc_key

                fields["task_id"] = str(_rc_key.task_id_for(
                    str(kwargs.get("session_id") or ""), _ask_for(kwargs),
                    str(kwargs.get("model") or "")) or "")
            except Exception:  # noqa: BLE001 — a missing join key is not fatal
                fields["task_id"] = ""
        lg("POST", event_detail="delivery_decision",
           **decision.as_record(),
           **fields,
           session_id=str(kwargs.get("session_id") or ""))
        _mark_decided(str(kwargs.get("session_id") or ""),
                      str(kwargs.get("turn_id") or ""))
    except Exception:  # noqa: BLE001 — observability never breaks delivery
        pass


def decide_skipped_turn(*, session_id: str, body: str, turn_id: str = "") -> str:
    """The delivery decision for a turn the POST seam never reached.

    M53-03 item 2: a turn that exits PRE via `complexity_skip` (and any turn
    whose transform pass the host skips) is observed only by the turn-close
    edge. This calls the SAME path the delivery edge uses — `_closed_line_hit`
    -> `_build_facts` -> `_decide` -> `_log_decision` — so the turn gets one
    D1..D6 row and a definite delivered body. The ask is recovered through
    `_ask_for`, whose `state.get_last_seen` fallback is the only source at this
    edge. LOG-ONLY: the observer cannot replace the host's body, so the bytes
    are reported and never substituted. Fail-open on ANY error; a turn whose
    transform edge already decided is a no-op.
    """
    sid = str(session_id or "")
    entered = str(body or "")
    if not sid:
        return ""
    try:
        if decision_logged(sid, turn_id):
            return ""
        kwargs = {"session_id": sid, "turn_id": str(turn_id or ""),
                  "response_text": entered}
        closed_hit = _closed_line_hit(kwargs)
        facts = _build_facts(entered, kwargs, None, None, closed_hit)
        decision = _decide(facts)
        if decision is not None and decision.action == _dd.CONSULT_REENTER:
            decision = _decide(_consult_gate(facts, kwargs))
        if decision is None:
            return ""
        delivered = entered
        _log_decision(decision, kwargs,
                      extra={"delivered_chars": len(delivered),
                             "where": "turn_close"})
        return delivered
    except Exception:  # noqa: BLE001 — the close edge never breaks a turn
        return ""


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
