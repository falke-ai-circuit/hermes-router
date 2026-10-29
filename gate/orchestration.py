"""gate/orchestration.py — the three on_* orchestrators (+ their hub-local
helpers), lifted VERBATIM out of the package hub at P3a (proposal §1.6).

Every reference this module makes to a hub-level name goes through the
late-bound _hub() seam (sys.modules lookup at CALL time — no import cycle;
gate may import L0-L2 directly, never the hub at module level). This
preserves, byte-for-byte, the pre-lift patch semantics: a test monkeypatching
plugin.<name> intercepts every call exactly as it did when the defs lived in
__init__ module globals, because _hub() resolves the same attribute.
"""
from __future__ import annotations

import importlib
import sys
from typing import Any, Dict, List, Optional


def _hub() -> Any:
    """Late-bound package hub (call time — no import cycle).

    Resolves the hub module under BOTH import roots:
      - test/dev root:     ``hermes_router`` (tests/conftest.py)
      - gateway root:      ``hermes_plugins.<slug>`` (hermes_cli/plugins.py
        ``_load_dir_module`` registers the plugin as a namespace child, so a
        hardcoded ``sys.modules['hermes_router']`` raised KeyError on every
        LIVE turn — swallowed by the gateway's fail-open, leaving all turns
        un-routed/bannerless while the suite stayed green).

    Walks this module's own dotted name upward and returns the first loaded
    ancestor that carries the hub marker (``on_llm_request``). Falls back to
    an import of the resolved plugin-root prefix. Returns None (never raises)
    when nothing resolvable is loaded — callers degrade to pass-through.
    """
    parts = __name__.split(".")
    for n in range(len(parts) - 1, 0, -1):
        prefix = ".".join(parts[:n])
        mod = sys.modules.get(prefix)
        if mod is not None and hasattr(mod, "on_llm_request"):
            return mod
    # Not yet in sys.modules under any prefix: try importing the plugin root
    # (the package this module lives in minus its trailing subpackage path).
    for n in range(len(parts) - 1, 0, -1):
        prefix = ".".join(parts[:n])
        try:
            mod = importlib.import_module(prefix)
        except Exception:  # noqa: BLE001 — unresolvable prefix, try next
            continue
        if hasattr(mod, "on_llm_request"):
            return mod
    return None




def _persona_system_prompt(request: Optional[dict]) -> str:
    return _hub()._dispatcher_knobs._persona_system_prompt(request)


def _cfg() -> Dict[str, Any]:
    return _hub()._dispatcher_knobs._cfg()


def _classification_cfg() -> Dict[str, Any]:
    return _hub()._dispatcher_knobs._classification_cfg()


def _enabled() -> bool:
    return _hub()._dispatcher_knobs._enabled()


def _dry_run() -> bool:
    return _hub()._dispatcher_knobs._dry_run()


def _flinch_reason_gate() -> bool:
    return _hub()._dispatcher_knobs._flinch_reason_gate()


def _pre_patterns() -> List[str]:
    return _hub()._dispatcher_knobs._pre_patterns()


def _post_patterns() -> List[str]:
    return _hub()._dispatcher_knobs._post_patterns()


def _match_threshold() -> int:
    return _hub()._dispatcher_knobs._match_threshold()


def _doctrine_verdict_enabled() -> bool:
    return _hub()._dispatcher_knobs._doctrine_verdict_enabled()


def _aux_classify_enabled() -> bool:
    return _hub()._dispatcher_knobs._aux_classify_enabled()


def _aux_mode() -> str:
    return _hub()._dispatcher_knobs._aux_mode()


def _pending_ttl() -> float:
    return _hub()._dispatcher_knobs._pending_ttl()


def thread_digest_chars() -> int:
    return _hub()._dispatcher_knobs.thread_digest_chars()


def thread_digest_asks() -> int:
    return _hub()._dispatcher_knobs.thread_digest_asks()


def render_max_chars() -> int:
    return _hub()._dispatcher_knobs.render_max_chars()


def cap_render(text: str, limit: int) -> str:
    return _hub()._dispatcher_knobs.cap_render(text, limit)


def _substance_frame() -> str:
    return _hub()._dispatcher_knobs._substance_frame()


def _log_path() -> str:
    return _hub().telemetry._log_path()


def _log_max_bytes() -> int:
    return _hub().telemetry._log_max_bytes()


def _decision_wait_before_consume(budget: float = 0.0) -> None:
    """R8-2 (rider 8): bounded wait for in-flight decision consult workers
    to park their banner BEFORE a POST delivery edge consumes the parked
    slot. Live root cause (analyst B3 row 105, single-shot): the async
    worker parks AFTER this turn's POST consume — consult billed, banner
    never delivered (single-shot sessions have no next turn). Bounded by
    decision.post_worker_wait (default 20s; 0 disables).
    R20-D3 (rider 20): budget (seconds, >0) CAPS the total wait — the host
    plugin runner kills the transform callback at 30s and then skips later
    invocations, orphaning parked banners (park-without-capture). The wait
    never pushes the hook past the budget; workers still park and the
    R19.21 re-park/redeliver contract covers the miss. Never raises."""
    try:
        from .. import decision as _dw

        _wait = float(_dw._cfg().get("post_worker_wait", 20) or 0)
        if budget and budget > 0:
            _wait = min(_wait, float(budget))
        if _wait > 0:
            _t0 = _hub().time.monotonic()
            _dw.wait_for_workers(_wait)
            _elapsed = _hub().time.monotonic() - _t0
        else:
            _elapsed = 0.0
        # R16-4 (rider 16): the anchor retry worker (timed-out consult
        # re-entered with an extended budget) parks the verdict banner the
        # moment it lands — a POST edge must wait bounded for it too, or the
        # same-turn delivery window closes before the verdict parks (live:
        # analyst luna-pro first probe >300s, 'staged / verdict pending' with
        # no verdict on any same-turn edge).
        try:
            from .. import anchor_exec as _ax

            _rwait = _wait
            if budget and budget > 0:
                _rwait = min(_rwait, max(0.0, float(budget) - _elapsed))
            if _rwait > 0:
                _ax.wait_for_anchor_retries(_rwait)
        except Exception:  # noqa: BLE001 — wait must never break delivery
            pass
    except Exception:  # noqa: BLE001 — wait must never break delivery
        pass


def _extract_last_user_message(request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return _hub()._dispatcher_knobs._extract_last_user_message(request)


def _extract_text_from_message(msg: Dict[str, Any]) -> str:
    return _hub()._dispatcher_knobs._extract_text_from_message(msg)


def _replace_last_user_message(request: Dict[str, Any], new_text: str) -> Dict[str, Any]:
    return _hub()._dispatcher_knobs._replace_last_user_message(request, new_text)


def _session_id_from_context(**context: Any) -> str:
    return _hub()._dispatcher_knobs._session_id_from_context(**context)


def _tap_task_identity(*args: Any, **kwargs: Any) -> "tuple[str, str]":
    return _hub()._dispatcher_pre._tap_task_identity(*args, **kwargs)


def _tap_feed_tool_results(*args: Any, **kwargs: Any) -> None:
    return _hub()._dispatcher_pre._tap_feed_tool_results(*args, **kwargs)


def _tap_provider_failure(*args: Any, **kwargs: Any) -> None:
    return _hub()._dispatcher_pre._tap_provider_failure(*args, **kwargs)


def _banner_tokens_from_last_write(*args: Any, **kwargs: Any) -> "tuple[int, int, float]":
    return _hub()._dispatcher_pre._banner_tokens_from_last_write(*args, **kwargs)


def _hs_inject_pass(*args: Any, **kwargs: Any) -> bool:
    return _hub()._dispatcher_pre._hs_inject_pass(*args, **kwargs)


def _audit_delivery_pass(*args: Any, **kwargs: Any) -> "Optional[dict]":
    return _hub()._dispatcher_pre._audit_delivery_pass(*args, **kwargs)


def _history_reconcile_pass(*args: Any, **kwargs: Any) -> None:
    return _hub()._dispatcher_pre._history_reconcile_pass(*args, **kwargs)


def _strip_memory_context(*args: Any, **kwargs: Any) -> str:
    return _hub()._dispatcher_pre._strip_memory_context(*args, **kwargs)


def _dispatch_pass(*args: Any, **kwargs: Any) -> bool:
    return _hub()._dispatcher_pre._dispatch_pass(*args, **kwargs)


def _frame_sentinel_check(*args: Any, **kwargs: Any) -> bool:
    return _hub()._dispatcher_pre._frame_sentinel_check(*args, **kwargs)


def _clarify_intent_scan(*args: Any, **kwargs: Any) -> tuple:
    return _hub()._dispatcher_pre._clarify_intent_scan(*args, **kwargs)


def _render_with_retry_ladder(*args: Any, **kwargs: Any) -> tuple:
    return _hub()._dispatcher_pre._render_with_retry_ladder(*args, **kwargs)


def _debug_banner_pass(*args: Any, **kwargs: Any) -> str:
    return _hub()._dispatcher_pre._debug_banner_pass(*args, **kwargs)


def _provenance_footer_pass(*args: Any, **kwargs: Any) -> str:
    return _hub()._dispatcher_pre._provenance_footer_pass(*args, **kwargs)


def _deliver_render_pass(*args: Any, **kwargs: Any) -> dict:
    return _hub()._dispatcher_pre._deliver_render_pass(*args, **kwargs)


def on_llm_request(*, request, original_request, **context) -> dict:
    try:
        from ..core.telemetry import seam_probe_fire

        seam_probe_fire("llm_request")
    except Exception:  # noqa: BLE001 — probe never breaks the seam
        pass

    """Rewrite the last user message to a substance frame built from Venice's
    rendered output. Return {'request': modified_request} or {} to pass through.
    """
    if _hub() is None:  # hub unresolvable (no package context) — fail-open
        return {}
    _hs_rule_injected = _hub()._hs_inject_pass(
        request, session_id=_hub()._session_id_from_context(**context))  # rider 22

    # SPEC §2a.4: the declaration is the trigger, so the model must be TOLD to
    # emit it. Without this injection the parser only ever sees UNKNOWN in
    # production and the lane silently never fires — the tag is not something
    # the model volunteers. Delivered as a SYSTEM platform rule (the platform's
    # own channel), marker-deduped so a replayed message list never carries it
    # twice; fail-open, because the contract must never cost a turn.
    #
    # NOTE: this mutates the request IN PLACE and deliberately does NOT flip
    # the pass-through envelope. In-place mutation with a `{}` return is the
    # established mechanism here (the FIX1 history-reconcile shim relies on
    # exactly that), and routing decisions must keep returning `{}` — several
    # pinned contracts assert "no route => no envelope".
    # NOTE: the pass mutates the request IN PLACE, so that mutation only
    # survives if this function ALSO returns {"request": ...}. Returning {}
    # here discards it: measured live, the model could quote the
    # decision-reading rule (delivered via the envelope) and confirmed the
    # contract was absent from its instructions — 0 tags emitted over 14 turns
    # while the pass reported success in isolation. The flag below carries the
    # injection out through _hs_pass(), the single propagation point.
    _decl_injected = False
    try:
        _decl_injected = bool(_hub()._dispatcher_pre.declaration_inject_pass(request))
    except Exception:  # noqa: BLE001 — injection must never break the seam
        pass

    # Reflex lane (weighted options): deliver a reading parked by the midturn seam onto the
    # REQUEST surface — the agent sees it while composing the next step, and it is never
    # persisted as a stored message (a stored note gets restated, not acted on). Folded into
    # the same {"request": ...} contract as the identity rule so every early return below
    # still propagates it.
    try:
        from .. import reflex_lane as _rl

        # Every-call mode (Goran 2026-10-26): the reading is produced from the LIVE request,
        # on every model call, so the main model never has to re-interpret its own transcript
        # to know what the choice is. Parked readings (midturn seam) are consumed first if
        # present — they carry an earlier, cheaper vantage.
        _rsid = _hub()._session_id_from_context(**context)
        _reflex_delivered = _rl.inject_pass(request, session_id=_rsid)
        if not _reflex_delivered:
            _reflex_delivered = _rl.run_on_request(request, session_id=_rsid)
        if _reflex_delivered:
            _hs_rule_injected = True
    except Exception:  # noqa: BLE001 — delivery must never break the seam
        pass
    try:
        def _hs_pass():
            """Propagate an in-place rule injection through the framework's
            {"request": ...} return contract; {} when nothing was injected.

            The declaration contract is REQUIRED for the lane's trigger, so it
            propagates on its own flag: an in-place append that is not carried
            out here never reaches the model.
            """
            return {"request": request} if (_hs_rule_injected or _decl_injected) else {}

        if not _hub()._enabled() or not bool(_hub()._classification_cfg().get("pre_classify", True)):
            return _hs_pass()

        user_message = _hub()._extract_last_user_message(request)
        if user_message is None:
            return _hs_pass()

        content = _hub()._extract_text_from_message(user_message)
        if not content.strip():
            return _hs_pass()

        # R34-14 item 1/2: a user message defines the turn. Same text as the
        # newest CLOSED turn is the same turn (a retry / duplicate transform
        # pass) and does NOT reopen it, so a consult that resolves late stays
        # orphaned instead of being legitimized by the second pass.
        try:
            from .. import advisory_consumption as _acturn

            _acturn.note_turn_open(
                _hub()._session_id_from_context(**context), content)
        except Exception:  # noqa: BLE001 — ledger never breaks the PRE seam
            pass

        # Phase 1 route gate (BLUEPRINT-request-routing-2026-09-12): the
        # sentinel firewall + completion-audit delivery early-returns are
        # now EXPLICIT no-route branches OF the unified gate (reviewer
        # F4/H3) — same position, same behavior, gate-owned envelope.
        _gate_env = _hub()._route_gate.fence_pass(content, request, context, _hs_pass)
        if _gate_env is not None:
            return _gate_env

        # H3 gate — REMOVED 2026-09-04 (Goran-direct reversal: "remove csam
        # blocking, uncensored should not filter anything when asked"). The
        # 2026-09-01 gate ("i dont want explicit minors") never actually fired
        # in production: it referenced `session_id` three lines before that
        # local was bound (UnboundLocalError swallowed by the outer handler),
        # so PRE silently passed through every time. Reversal makes removal
        # the correct fix: csam_underage now routes like any contested class
        # (chain renders; substrate refusals pass through unchanged; loop
        # guard prevents loops). No content gate exists in this plugin —
        # boundaries live in the render substrate, not in routing code.

        session_id = _hub()._session_id_from_context(**context)
        model = str(request.get("model") or "")

        # R34-17: the TURN WORK PREDICATE's request-side tap. A payload that
        # carries a write/edit tool call proves the turn changed the substrate;
        # the terminal seam proves a terminal ran. Read-only traffic is
        # tracked but does not qualify (a turn that only looked is still a
        # no-work turn).
        try:
            from .. import work_signal as _wsin

            _wsin.note_request_activity(session_id, request)
        except Exception:  # noqa: BLE001 — the predicate never breaks routing
            _hub().logger.debug("work-signal request tap error", exc_info=True)

        # v3.6 P0.1 (FF-2) — tool-result tap at the agent-loop surfacing point.
        # The llm_request middleware fires once per provider call; iterations
        # of the tool loop carry the newest tool result as a tool-role message
        # in the payload. Task identity is reconstructed via the session ->
        # last-user-text cache (state.record_last_seen, written earlier this
        # turn pre-classification) -> router_core.task_id_for + turn_key_for.
        # Zero call sites for task_id_for existed before this tap (verified
        # 2026-09-07) — without the cache read the tap would feed dead keys.
        # Write-only (fail-ring + progress ledger); no gate reads them yet.
        try:
            _hub()._tap_feed_tool_results(request, session_id, model)
        except Exception:  # noqa: BLE001 — tap must never break the middleware
            _hub().logger.debug("uncensored-router tool-result tap error", exc_info=True)

        # R11 anti-bypass capture: provider hosts in tool-call surfaces
        # (content-free — host names only, per-turn keyed). Observability
        # only; the audit fires at the POST turn close (bypass_watch).
        try:
            from .. import bypass_watch as _bw

            _bw.capture_from_request(request, session_id)
        except Exception:  # noqa: BLE001 — capture must never break the turn
            _hub().logger.debug("bypass-watch capture error", exc_info=True)

        # FIX 1 shim (2026-09-02): reconcile trailing refusals to delivered
        # renders — see _history_reconcile_pass.
        _hub()._history_reconcile_pass(request, session_id)


        # Record last-seen user message BEFORE the complexity dispatcher —
        # v3.6.1 fix: the dispatcher's complexity path returns EARLY (L830
        # return _hs_pass()) and previously skipped the unconditional record at L839,
        # leaving get_last_seen() empty at POST → completion-audit gate saw
        # ask_len=0 and silently skipped every complexity-routed turn.
        _hub().state.record_last_seen(session_id, content)
        # Router tuning (2026-09-09): strip the <memory-context> block once at
        # ingress — routing judges the ASK, not ask+memory-noise.
        content = _hub()._strip_memory_context(content)
        # R8b (2026-09-13) orientation-leak guard: a resume-after-interruption
        # turn must not have the PRE orientation envelope recited back as the
        # reply — reinforce it as internal-only (one line, deduped, fail-open).
        try:
            if _hub()._dispatcher_pre.inject_orientation_leak_guard(request, content):
                _hub()._log_route("PRE", event_detail="orientation_leak_guard_injected",
                           session_id=session_id)
        except Exception:  # noqa: BLE001 — guard must never break routing
            _hub().logger.debug("orientation-leak guard wiring error", exc_info=True)
        # Leg 7b (single claim point, key continuity): advance the session's
        # TURN IDENTITY before the gate's claim phase. Tool-loop passes
        # (tool-role messages present) are continuations of the SAME turn —
        # the counter holds; otherwise a changed last-user hash means a new
        # user turn. router_tools and route_gate share this identity, so a
        # claim registered mid-turn binds every later pass of the turn.
        try:
            _is_tool_loop = any(
                isinstance(m, dict) and str(m.get("role") or "") == "tool"
                for m in (request.get("messages") or []))
        except Exception:  # noqa: BLE001 — fail-open to non-continuation
            _is_tool_loop = False
        try:
            _hub().state.advance_turn_identity(
                session_id, _hub().state.hash_text(content or ""),
                is_continuation=bool(_is_tool_loop))
        except Exception:  # noqa: BLE001 — fail-open, never block delivery
            pass
        # v3.0.0 complexity lane dispatcher pass — now the gate's CLAIM
        # phase (Phase 1 route gate): declared on-demand inputs are
        # evaluated at the same position, with the legacy _dispatch_pass
        # consulted VERBATIM as the auto-shape gate input (policy frozen,
        # execution stays at on_llm_execution).
        _claim_decision = _hub()._route_gate.claim_pass(
            content, session_id, model, request=request, context=context)
        # LEG 12 FIX 2 (Goran FP doctrine): declared-intent NEAR MISS —
        # family words present in a turn-start directive-ish line but no
        # lane fired. Inject the advisory bare-call reminder into the
        # request context (marker-deduped, once per turn, fail-open). The
        # near-miss probe uses the SAME strict line-start rules as the
        # declared variants: quoted lines, mid-sentence mentions, and
        # meta/prose ('what does uncensored routing mean') are inert.
        # Advisory only — never routes, never claims.
        if not _claim_decision.route:
            try:
                if _hub()._route_gate._detect_declared_intent_near_miss(content):
                    if _hub()._route_gate.inject_bare_call_reminder(request):
                        _hub()._log_route("PRE",
                                   event_detail="bare_call_reminder_injected",
                                   session_id=session_id)
            except Exception:  # noqa: BLE001 — advisory must never break routing
                _hub().logger.debug("bare-call reminder error", exc_info=True)
        if _claim_decision.route:
            # LEG 8 (blueprint §2): declared SHADOW routes execute on the
            # UNCENSORED RENDER chain (abliteration primary / Venice
            # fallback — the same machinery as refusal-shaped/contested PRE
            # renders), NOT a frontier anchor consult. The render's spend
            # flows to the render lane of the tokens ledger; a render row
            # lands in uncensored-router-renders.jsonl. Declared HIGHER
            # lanes keep the frontier anchor envelope (handled at
            # on_llm_execution via the staged swap).
            if (_claim_decision.lane == _hub()._route_gate.LANE_SHADOW
                    and _claim_decision.source in (
                        _hub()._route_gate.SOURCE_DECLARED_USER,
                        _hub()._route_gate.SOURCE_DECLARED_AGENT,
                        _hub()._route_gate.SOURCE_AUX_INTENT)):
                # R9-1 (rider 9): SOURCE_AUX_INTENT shadow claims render TOO —
                # the old allowlist consumed the claim at claim_pass
                # (mark_turn_claim_executed + clear_declared) and then skipped
                # this render branch SILENTLY (conductor chain: aux conf=1.0,
                # staged=False, nothing). Aux shadow asks are user-phrased —
                # initiator=user (machine-DETECTED, never machine-initiated).
                _initiator = ("agent"
                              if _claim_decision.source ==
                              _hub()._route_gate.SOURCE_DECLARED_AGENT
                              else "user")
                try:
                    _persona = _hub()._persona_system_prompt(
                        request if isinstance(request, dict) else None)
                    rendered, _render_retries = _hub()._render_with_retry_ladder(
                        content, ["shadow_declared"], _persona, session_id)
                    # R26-2b: whitespace-only render == empty — a route that
                    # produced no substance fails open to the original turn,
                    # NEVER delivers an empty replacement.
                    if not rendered or not str(rendered).strip():
                        _hub()._log_route("PRE", event_detail="route_failed",
                                   pattern_groups="shadow_declared",
                                   render_lane="shadow", session_id=session_id)
                        return _hs_pass()
                    rendered = _hub().cap_render(rendered, _hub().render_max_chars())
                    # R9d (Goran 09-14): NO inline _debug_banner_pass here —
                    # the LEG 11 block below parks THE one banner for this
                    # call. Both paths firing = duplicate banner (1 LLM call
                    # must show exactly 1 banner).
                    # LEG 11 (Goran-direct): the shadow declared lane emits
                    # its OWN §10.4 debug banner (parked -> appended at the
                    # delivery edge by on_transform_llm_output's consume,
                    # exactly like frontier PRE/POST). The banner carries the
                    # shadow-family fields: lane=shadow, uncensored chain
                    # model, initiator (user|agent), task_id, rendered size,
                    # est cost. Canonical DB row stays banner-free (append
                    # happens ONLY at the delivery boundary); oversized ->
                    # omitted; failure-isolated, never breaks the turn.
                    try:
                        from .. import debug_banner as _sdb
                        if _sdb.debug_banner_enabled():
                            _chain_entries_sh = _hub().router._chain_entries()
                            _entry_sh = _chain_entries_sh[0] \
                                if _chain_entries_sh else {}
                            _ti_sh, _to_sh, _cost_sh = \
                                _hub()._banner_tokens_from_last_write(
                                    "render", session_id)
                            _task_sh = _hub()._tap_task_identity(
                                session_id, model)[0]
                            _banner_sh = _sdb.format_banner(
                                lane="shadow",
                                trigger="shadow_declared",
                                model=str(_entry_sh.get("model") or ""),
                                endpoint=str(_entry_sh.get("url") or ""),
                                tokens_in=_ti_sh, tokens_out=_to_sh,
                                est_cost=_cost_sh,
                                latency_s=0.0, retries=_render_retries,
                                task_id=_task_sh, session_id=session_id)
                            _banner_sh = (_banner_sh +
                                          " | initiator=%s" % _initiator
                                          ).strip() if _banner_sh else ""
                            _sdb.park_anchor_banner(session_id, _banner_sh,
                                                    task_id=_task_sh)
                            if _banner_sh:
                                _sh_rec = _sdb.build_banner_record(
                                    "shadow", _task_sh,
                                    trigger="shadow_declared",
                                    model=str(_entry_sh.get("model") or ""),
                                    tokens_in=_ti_sh, tokens_out=_to_sh,
                                    est_cost=_cost_sh,
                                    latency_s=0.0,
                                    retries=_render_retries,
                                    session_id=session_id, gate="")
                                _sh_rec["event_detail"] = \
                                    "debug_banner_emitted"
                                _sh_rec.pop("lane", None)  # avoid kw collision
                                _hub()._log_route("PRE", lane="shadow", **_sh_rec)
                    except Exception:  # noqa: BLE001 — banner never breaks the turn
                        _hub().logger.debug("shadow debug banner error", exc_info=True)
                    rendered = _hub()._provenance_footer_pass(rendered)
                    _hub()._deliver_render_pass(request, content, rendered, model,
                                         session_id, ["shadow_declared"])
                    # initiator tag on the render-chain spend (leg 6/8):
                    # the render lane wrote its ledger record inside
                    # router.call; thread the claim's initiator onto the
                    # LAST render-lane record for this session.
                    try:
                        from .. import usage_ledger as _ul
                        _ul.tag_last_render_initiator(session_id, _initiator)
                    except Exception:  # noqa: BLE001 — observability only
                        pass
                    # R9-3 (rider 9): the claim's render ROW landed — annotate
                    # the shadow claim so any later stand-down pass of this
                    # turn sees rendered=True (never fails loud spuriously).
                    try:
                        _hub()._route_gate.mark_shadow_rendered(session_id)
                    except Exception:  # noqa: BLE001 — observability only
                        pass
                except Exception:  # noqa: BLE001 — never break delivery
                    _hub().logger.debug("shadow render branch error", exc_info=True)
                    # R9-3 (rider 9): a shadow render branch EXCEPTION with a
                    # claim already consumed is a fail-loud route_failed — the
                    # claim is gone and nothing rendered.
                    try:
                        _hub()._log_route("PRE", event_detail="route_failed",
                                   pattern_groups="shadow_render_branch_error",
                                   render_lane="shadow", session_id=session_id)
                    except Exception:  # noqa: BLE001
                        pass
                return _hs_pass()
            return _hs_pass()


        # Record last-seen user message BEFORE classification — this fires on
        # every turn, matched or not, so POST can always recover the user's
        # current message even when PRE didn't route (unconditional POST, per
        # the 2026-09-01 escalation-hole ruling).
        _hub().state.record_last_seen(session_id, content)

        # Leg 7 (single claim point, blueprint invariant #1): when the gate
        # registered a claim for this turn (any lane — shadow render, agent
        # claim, complexity consult), the uncensored PRE render stands down —
        # the claim owns the turn's ONE routing outcome. Fail-open: registry
        # unavailable -> legacy behavior (never block delivery).
        try:
            _turn_claim = _hub()._route_gate.claim_state(session_id)
        except Exception:  # noqa: BLE001 — fail-open
            _turn_claim = None
        if _turn_claim is not None and _turn_claim.get("lane") not in (
                _hub()._route_gate.LANE_HIGHER_PRE, _hub()._route_gate.LANE_HIGHER_POST):
            # R9-3 (rider 9): a consumed SHADOW claim with NO render row is
            # route_failed (rider 7 provenance contract), never a silent
            # stand-down. The render branch annotates the claim via
            # mark_shadow_rendered; absence here = the claim was eaten
            # without a render (source-allowlist miss, exception, kill-switch
            # stand-down) — fail loud.
            if (_turn_claim.get("lane") == _hub()._route_gate.LANE_SHADOW
                    and not _turn_claim.get("rendered")):
                _hub()._log_route("PRE", event_detail="route_failed",
                           pattern_groups="shadow_claim_consumed",
                           render_lane="shadow",
                           claim_source=str(_turn_claim.get("source") or ""),
                           session_id=session_id)
            _hub()._log_route("PRE", event_detail="claim_standdown_uncensored",
                       claim_lane=str(_turn_claim.get("lane") or ""),
                       claim_source=str(_turn_claim.get("source") or ""),
                       session_id=session_id)
            return _hs_pass()

        case_sensitive = bool(_hub()._classification_cfg().get("case_sensitive", False))
        # POST-ONLY LANE (spec /opt/data/tmp/unc_lane_post_spec.md §1,
        # design fixed by Goran 2026-10-28): PRE IS DELETED AS A ROUTING
        # TRIGGER. There is exactly one detection seam — the post-generation,
        # pre-delivery transform — so the request-side classification that
        # decided to render BEFORE the model answered is gone, together with
        # the predicted-refusal routing it existed to feed and the clarify
        # user_response scan that fed it (an ask is not evidence of a refusal).
        #
        # What survives of the pre pattern set is classification, never
        # routing: the empty-body rule asks it of the LAST ASK (spec §1), and
        # a request-side match is recorded here as retired-prediction
        # telemetry. It renders nothing and mutates no request.
        #
        # Declared on-demand asks still route — they are handled above at
        # claim_pass and their output exits through the ONE POST delivery
        # seam (a declared render is model context, the delivered turn is the
        # transform's), so there is a single outbound path again.
        try:
            _retired_pre = _hub().classifier.scan_pre(
                content, patterns=_hub()._pre_patterns(),
                case_sensitive=case_sensitive)
            if _retired_pre:
                _hub()._log_route(
                    "PRE", event_detail="pre_route_retired", routed=0,
                    pattern_groups=",".join(_retired_pre),
                    content_chars=len(content), session_id=session_id)
        except Exception:  # noqa: BLE001 — telemetry must never break the turn
            _hub().logger.debug("retired PRE classification error", exc_info=True)
        return _hs_pass()
    except Exception as exc:  # noqa: BLE001 — middleware must never raise
        _hub().logger.debug("uncensored-router pre-router error: %s", exc)
        return _hs_pass()


def _gate_semantic(response_text: str) -> bool:
    return _hub()._dispatcher_post._gate_semantic(response_text)


def _aux_user_message(*args: Any, **kwargs: Any) -> str:
    return _hub()._dispatcher_post._aux_user_message(*args, **kwargs)


def _semantic_stage(*args: Any, **kwargs: Any) -> "tuple[Optional[str], List[str]]":
    return _hub()._dispatcher_post._semantic_stage(*args, **kwargs)


def _is_refusal_shaped(text: str) -> bool:
    return _hub()._dispatcher_post._is_refusal_shaped(text)


def _is_refusal_shaped_public(text: str) -> bool:
    return _hub()._dispatcher_post._is_refusal_shaped_public(text)


def _build_substance_message(rendered: str, original_ask: str = "") -> str:
    return _hub()._dispatcher_post._build_substance_message(rendered, original_ask)


def _deliver_parked_at_edge(session_id: str, response_text: str,
                            edge: str, budget: float = 0.0) -> "Optional[str]":
    """Rider 15 R15-5: a parked verdict banner must attach to EVERY
    delivery edge of the turn that billed it. The benign consume block
    only runs on clean responses; refusal-shaped-but-technical turns
    (flinch technical passthrough) and honored agent-line turns returned
    None BEFORE consuming — the banner parked past its own turn and the
    delivered body never carried it (live: analyst T2e 'consult luna-pro',
    banner parked 15:26:47, flinch passthrough 15:29:42, consume at
    15:29:55 on a later edge, parked_capture_failed rewrite_no_match).
    Helper contract: consume the parked banner, append it to the delivery
    text, capture the render, and return the merged text (or None for the
    plain passthrough contract). Never raises; never breaks delivery."""
    try:
        from .. import debug_banner as _dbd

        _hub()._decision_wait_before_consume(budget)
        # R34-17 item 3: read the park record before the consume pops it, and
        # report `parked=True` only when the banner actually reached the body.
        try:
            _rec = _dbd.park_record(session_id) or {}
        except Exception:  # noqa: BLE001 — record read never breaks delivery
            _rec = {}
        _parked = _dbd.consume_parked_banner(session_id)
        _dbd.note_consumed_decision(session_id, _parked)
        if not _parked:
            _hub()._log_route("POST", event_detail="anchor_banner_consume",
                              parked=bool(_rec), delivered=False, edge=edge,
                              reason=("consume_missed" if _rec
                                      else "no_park_this_turn"),
                              session_id=session_id)
            return None
        _merged = _dbd.append_banner(str(response_text or ""), "\n" + _parked)
        _out = _merged if isinstance(_merged, str) and _merged else str(response_text or "")
        _landed = bool(_parked.strip() and _parked.strip() in _out)
        _hub()._log_route("POST", event_detail="anchor_banner_consume",
                          parked=bool(_rec), delivered=bool(_landed), edge=edge,
                          reason=("delivered" if _landed
                                  else "attach_not_landed" if _rec
                                  else "unrecorded_slot"),
                          session_id=session_id)
        if not _landed:
            # R33f-A (supersedes the R19.19 P0 re-park contract): TTL is ONE
            # delivery — a consumed banner whose attach did not land is
            # DROPPED fail-loud, never re-parked into later turns (the
            # re-park loop was the live stale-leak class, session
            # 20261006_190903_c82cbbcc). Passthrough (no claim).
            try:
                _hub()._log_route("POST",
                                  event_detail="banner_park_dropped",
                                  reason="attach_not_landed",
                                  edge=edge, session_id=session_id)
            except Exception:  # noqa: BLE001 — telemetry never breaks delivery
                pass
            return None
        try:
            _out2 = _dbd.settle_decision_banner(session_id, _out)
            if isinstance(_out2, str) and _out2:
                _out = _out2
        except Exception:  # noqa: BLE001
            pass
        try:
            from .. import render_inbox as _rie
            from .. import canonical as _ce

            _rie.record_render("EDGE_BANNER", session_id,
                               len(str(response_text or "")), _out)
            if _ce.rewrite_persisted_turn(session_id,
                                          str(response_text or ""), _out):
                _hub()._log_route("POST", event_detail="banner_render_captured",
                           edge=edge, session_id=session_id)
        except Exception:  # noqa: BLE001 — capture never breaks delivery
            _hub().logger.debug("edge banner capture error", exc_info=True)
        return _out if _out != str(response_text or "") else None
    except Exception:  # noqa: BLE001 — banner must never break delivery
        return None


def _recover_orphan_anchor_swap(session_id: str) -> None:
    """R16-2c (rider 16): POST-edge orphan recovery. A staged anchor swap
    that is STILL pending when a delivery edge runs means no llm_execution
    pass of the turn ever consumed it (hook seam miss — live: analyst A2
    declared 'ask your higher self' ask staged at 17:47:22, zero anchor
    events, 0 banners 0 rows). Consume the orphan and re-enter the anchor
    with the staged payload copy through the R16-4 bounded retry worker:
    the consult bills, ledgers, and its verdict parks for the earliest
    delivery edge — never silently orphaned again. Never raises."""
    try:
        if not session_id:
            return
        from .. import anchor_exec as _ax
        from .. import router_core as _rc

        rec = _rc.pending_model_swap(session_id)
        if not rec:
            return
        _payload = rec.get("payload")
        _ep = rec.get("endpoint")
        if not _payload or _ep is None:
            _hub()._log_route("POST", event_detail="anchor_orphan_unrecoverable",
                       reason="no_payload" if not _payload else "no_endpoint",
                       task_id=str(rec.get("task_id") or ""),
                       session_id=session_id)
            return
        _hub()._log_route("POST", event_detail="anchor_orphan_recovered",
                   task_id=str(rec.get("task_id") or ""),
                   route_id=str(rec.get("route_id") or ""),
                   session_id=session_id)
        _ax.retry_anchored_async(session_id, rec, _hub().copy.deepcopy(_payload),
                                 _ep, base_timeout=300)
    except Exception:  # noqa: BLE001 — recovery must never break delivery
        _hub().logger.debug("orphan anchor swap recovery error", exc_info=True)


def _banner_turn_snapshot(session_id: str):
    """R34-15: (park_turn_no, current_turn_no) for the parked-banner slot.

    Retention is keyed on advisory_consumption.turn_no — the turn ledger the
    PRE seam opens once per user ask. It is NOT keyed on close_turn(): that
    fires on every transform PASS (line ~781), so a transform-close signal
    cannot distinguish "this turn is still writing" from "the user moved on".
    """
    try:
        from .. import advisory_consumption as _ac
        from .. import debug_banner as _dbp

        rec = _dbp.park_record(session_id) or {}
        return int(rec.get("turn_no") or 0), int(_ac.turn_no(session_id) or 0)
    except Exception:  # noqa: BLE001 — a snapshot never breaks delivery
        return 0, 0


def _resolve_midturn_capture_failure(session_id: str, parked_text: str,
                                     park_turn: int, cur_turn: int,
                                     reason: str, body_chars: int = 0,
                                     row_present: bool = False) -> str:
    """R34-15 item 1: a parked banner SURVIVES a mid-turn capture failure.

    Same turn (park_turn == cur_turn != 0): the banner is re-parked under a
    fresh one-delivery record and `banner_park_retained` is logged, so the
    LAST banner-eligible pass of this turn consumes and delivers it. The
    rewrite miss is expected there: the row being rewritten at a mid-turn
    edge is an intermediate assistant message, not the delivered answer.

    Later turn (park_turn < cur_turn): the turn closed undelivered -> the
    banner is DROPPED and `banner_park_dropped reason=turn_closed_undelivered`
    is logged. That preserves R33f-A verbatim: a turn-N banner never reaches
    turn N+1 (the stale-leak class, session 20261006_190903_c82cbbcc).

    Never raises; returns "retained" | "dropped" | "".
    """
    try:
        pt = int(park_turn or 0)
        ct = int(cur_turn or 0)
        from .. import debug_banner as _dbp

        txt = str(parked_text or "").strip("\n").strip()
        # Retention needs a KNOWN turn on both sides: 0/0 means the PRE seam
        # never opened a turn (no ledger), and an unowned park must fall back
        # to the R33f-A one-delivery drop rather than live forever.
        if pt and ct and pt == ct:
            if txt:
                _dbp.park_anchor_banner(session_id, txt)
            _hub()._log_route("POST", event_detail="banner_park_retained",
                              reason=reason, turn_no=ct,
                              session_id=session_id)
            return "retained"
        try:
            _dbp.consume_parked_banner(session_id)
        except Exception:  # noqa: BLE001
            pass
        # R34-18 residual 2: the drop row names the evidence it was decided on
        # (body_chars / row_present) and may not say "undelivered" while a body
        # actually landed — the truthful reason then is that the ask moved on.
        _body = int(body_chars or 0)
        _hub()._log_route("POST", event_detail="banner_park_dropped",
                          reason=("ask_superseded" if _body > 0
                                  else "turn_closed_undelivered"),
                          cause=reason, turn_no=ct, body_chars=_body,
                          row_present=bool(row_present),
                          session_id=session_id)
        return "dropped"
    except Exception:  # noqa: BLE001 — telemetry never breaks delivery
        return ""


_PASS_COUNT: Dict[str, int] = {}
# §2a self-declaration, per (session, stripped-body hash). The tag is removed on
# the first pass at the seam, so later passes must recall the bit from the body
# they see; a `false` declaration that decayed to UNKNOWN would re-open the
# classifier fallback the declaration exists to close. Bounded, fail-open.
_DECLARATION_CACHE: Dict[tuple, str] = {}
# SAME-TURN GUARD. A body hash alone is not turn identity: a later turn in the
# same session can repeat a body verbatim, and recalling its declaration would
# route (or suppress) a turn that declared nothing. `_DECLARATION_LAST` records
# which body this session processed LAST, so a recalled bit is only ever valid
# for a pass of the turn that just ran. A different body in between moves the
# pointer and the stale entry can never be recalled.
_DECLARATION_LAST: Dict[str, tuple] = {}   # session -> (body_hash, monotonic ts)
_DECLARATION_TTL_S = 300.0


def _reset_declaration_state() -> None:
    """Clear the declaration recall state (test isolation + turn boundary)."""
    _DECLARATION_CACHE.clear()
    _DECLARATION_LAST.clear()


def on_transform_llm_output(*, response_text: str = "", session_id: str = "",
                            model: str = "", platform: str = "", **context) -> Optional[str]:
    try:
        from ..core.telemetry import seam_probe_fire

        seam_probe_fire("transform_llm_output")
    except Exception:  # noqa: BLE001 — probe never breaks the seam
        pass
    # R34-10a: this seam is the turn-end edge (final body, including an empty
    # final response). The session's turn lease MUST not outlive it — a hung
    # turn held + renewed its row for ~30 min and silently blocked every new
    # dispatch. A turn that dies before this seam is bounded by 10b's stale
    # preemption. Fail-open; release is idempotent.
    try:
        from .. import session_lease as _sl

        _sl.release(
            session_id,
            outcome="success" if str(response_text or "").strip() else "empty_final",
            log=lambda _e, **kw: _hub()._log_route(
                "POST", event_detail=_e, session_id=str(session_id or ""), **kw),
        )
    except Exception:  # noqa: BLE001 — lease hygiene never breaks delivery
        pass
    # R34-12 item 9: WORK INVARIANT at turn close. A PRE advisory injected
    # into a dispatch/task instruction that then closed with ZERO tool
    # activity produced no work — emit `advisory_consumed_turn
    # no_tools=true`. Log-only, fail-open, single close per turn.
    try:
        from .. import advisory_consumption as _acclose

        _acclose.close_turn(session_id, log_route=_hub()._log_route)
    except Exception:  # noqa: BLE001 — observability never breaks delivery
        pass
    """Detect agent refusals and replace with Venice-rendered content.
    Return a non-empty string to REPLACE the response; None to pass through.
    First plugin to return non-empty wins (turn_finalizer.py:557-561).

    2026-09-01 escalation ruling: the POST safety net is UNCONDITIONAL — it
    fires on any detected refusal regardless of whether the PRE router matched
    a contested class. Escalation asks ("go worse") name no class; the model's
    in-descent refusals are exactly the hole this closes. Message recovery
    order: same-turn PRE stash → hook context (user_message kwarg, future-
    proof) → last-seen cache (recorded by the middleware on every turn) →
    session store (state.db; survives gateway restarts). Fallback routes log
    route_fired_no_stash. Content gate removed 2026-09-04 (Goran-direct
    reversal — no code-side filtering; substrate boundaries pass through).
    Loop guard retained."""
    try:
        if _hub() is None:  # hub unresolvable (no package context) — pass through
            return None
        # R34-19c diagnostic: how many passes does a turn actually take, and
        # which one reaches the audit arm? close_turn() below pops turn state on
        # the FIRST pass, so a turn-close-keyed arm can never fire on the pass
        # that delivers. Counting is the prerequisite for moving the
        # confirmation onto the delivery edge.
        try:
            _pn = _PASS_COUNT.get(str(session_id or ""), 0) + 1
            _PASS_COUNT[str(session_id or "")] = _pn
            if len(_PASS_COUNT) > 512:  # bounded — never grow per session forever
                _PASS_COUNT.clear()
            _hub()._log_route("POST", event_detail="transform_pass", n=_pn,
                              chars=len(str(response_text or "")),
                              session_id=session_id)
        except Exception:  # noqa: BLE001 — counting never breaks delivery
            pass
        # ------------------------------------------------------------------
        # SPEC §2a.6 (Goran 2026-10-28): SELF-DECLARATION STRIP, EARLIEST SEAM.
        # The tag is routing metadata, not answer text. It must never reach a
        # delivered body, a stored turn, or another lane's prompt, so it comes
        # out HERE — before the enabled/dry checks (so a disabled router can
        # never deliver the tag), before the echo guard, before denial_class,
        # before any consult reads the body.
        #
        # §2a.4: the declaration is the TRIGGER.
        #   "true"    -> the EXISTING route path, via exactly the machinery the
        #                action_denial class uses today (matches + post_class)
        #   "false"   -> do not route (the one bit is authoritative)
        #   "UNKNOWN" -> fall through to the four-class classifier, unchanged
        #
        # Fail-open everywhere: a strip/read error leaves the body untouched
        # and the declaration UNKNOWN — the seam must never break delivery.
        # ------------------------------------------------------------------
        _decl_entry_text = str(response_text or "")
        _declaration = "UNKNOWN"
        _decl_stripped = False
        try:
            from ..features.detection import declaration as _declm

            _decl_body, _decl_value = _declm.strip_tag(response_text)
            if _decl_value is not None:
                _declaration = _declm.parse_declaration_value(_decl_value)
                response_text = _decl_body
                _decl_stripped = True
                # A1 side of §2a.6: turn_finalizer persisted the assistant row
                # BEFORE this hook fired, so the stored turn still carries the
                # tag. Replace that row with the stripped body — persisted ==
                # delivered, and routing metadata never lands in the
                # transcript.
                try:
                    from ..core import canonical as _can_decl

                    _decl_rewrote = _can_decl.rewrite_persisted_turn(
                        session_id, _decl_entry_text, response_text)
                    _hub()._log_route(
                        "POST", event_detail="declaration_strip_persisted",
                        rewritten=bool(_decl_rewrote),
                        chars_from=len(_decl_entry_text),
                        chars_to=len(response_text or ""),
                        session_id=session_id)
                except Exception:  # noqa: BLE001 — persistence never breaks delivery
                    _hub().logger.debug("declaration persist rewrite error",
                                        exc_info=True)
            # A turn can pass this seam more than once (transform_pass). Pass 1
            # removed the tag, so a later pass sees a body with no declaration
            # and would decay to UNKNOWN — which would let a `false` turn route
            # through the classifier fallback. Recover the bit by stripped-body
            # hash so `false` keeps holding for the rest of the turn.
            try:
                _decl_key = (str(session_id or ""),
                             _hub().canonical.hash_text(str(response_text or "")))
                _decl_body_hash = _decl_key[1]
                _decl_now = _hub().time.monotonic()
                if _decl_value is None:
                    # Recall ONLY if this session's most recent body is this
                    # exact body, and recently: otherwise the hit belongs to an
                    # earlier turn (or another test) and would route a turn
                    # that declared nothing.
                    _last = _DECLARATION_LAST.get(str(session_id or ""))
                    if (_last is not None and _last[0] == _decl_body_hash
                            and (_decl_now - _last[1]) <= _DECLARATION_TTL_S):
                        _declaration = _DECLARATION_CACHE.get(_decl_key, "UNKNOWN")
                elif _declaration in ("true", "false"):
                    _DECLARATION_CACHE[_decl_key] = _declaration
                    _DECLARATION_LAST[str(session_id or "")] = (
                        _decl_body_hash, _decl_now)
                    if len(_DECLARATION_CACHE) > 512:  # bounded, like _PASS_COUNT
                        _reset_declaration_state()
            except Exception:  # noqa: BLE001 — cache is an optimisation only
                pass
        except Exception:  # noqa: BLE001 — strip never breaks delivery
            _hub().logger.debug("declaration strip error", exc_info=True)

        def _decl_passthrough() -> Optional[str]:
            """The pass-through return, tag-safe.

            ``None`` tells the host "deliver the body you gave me" — but after
            a strip that original still CARRIES the tag, which §2a.6 forbids.
            So a stripped turn hands back the stripped body explicitly; an
            undeclared turn keeps the untouched pass-through contract.
            """
            return response_text if _decl_stripped else None

        if not _hub()._enabled() or not bool(_hub()._classification_cfg().get("post_classify", True)):
            _hub()._log_route("POST", event_detail="post_seam_skip",
                              reason=("router_disabled" if not _hub()._enabled()
                                      else "post_classify_off"),
                              session_id=session_id)
            return _decl_passthrough()
        # R20-D3 (rider 20): the host plugin runner kills this callback at
        # 30s ("timed out after 30s — skipping") and then SKIPS later
        # invocations ("skipped after previous timeout or while still
        # running") — a skip left a billed consult's parked banner with NO
        # consume edge (live valmet D3: ledger rows 148+149 billed, banner
        # parked 06:03:37Z, zero consume events on session
        # api_1791266587_749f80ab). Everything this hook schedules (sync
        # audit consult, revision pass, decision/anchor pre-consume waits)
        # is budgeted under the runner timeout so a slow consult can never
        # orphan the hook. 5s margin covers consume/capture/append work.
        # R34-14 item 7 / R34-17: EXECUTION_ECHO enforcement (supersedes the
        # R34-8c log-only contract for frame=execution). An execution turn
        # that closes by DESCRIBING the router's advisory delivered no work —
        # the live case burned a full dispatch (session api_1792543869_964dbe2a:
        # advisory capped to 600 chars, body delivered 6459 chars of
        # restatement, 6 tool calls, zero work).
        #
        # R34-17: the decision now keys on the TURN'S WORK SIGNAL, never on
        # body shape alone. Live false positive (20261021_070153_f41a2f): an
        # execution turn wrote dedupe.py + test_dedupe.py and passed pytest,
        # but its body carried an echoed `"povs"` tail — the old guard threw
        # the whole body away and told the user "nothing was executed". A
        # WORK turn gets the advisory STRIPPED and the report KEPT (the seam
        # keeps running so the parked banner still attaches); only a turn
        # with no work signal is handed back.
        # R34-19L: turn_finalizer persists the assistant row BEFORE this hook
        # fires, so the transcript holds the body AS ENTERED. Every later
        # mutation (advisory strip, audit revision) produces a body the
        # exact-match rewrite can no longer find — the store then keeps the
        # pre-strip copy (leaked advisory JSON included) while delivery is
        # clean, and `delivered=True` becomes unfalsifiable. Keep the entry
        # text and reconcile the row against it.
        _seam_entry_text = response_text
        try:
            from .. import echo_guard as _eg

            _echo = _eg.enforce_execution_echo(
                response_text, session_id, log_route=_hub()._log_route)
            if _echo is not None:
                if _echo.action == "strip_keep_work":
                    # Deliver the agent's own report and CONTINUE down this
                    # seam — banners, capture and persist all belong to the
                    # delivered work turn.
                    response_text = _echo.body
                    # R34-19L: write the stripped body back over the row the
                    # runner persisted on entry, or every later exact-match
                    # rewrite (banner capture, audit sync) silently misses and
                    # the transcript keeps the advisory tail the user never saw.
                    try:
                        from .. import canonical as _can_strip
                        if _can_strip.rewrite_persisted_turn(
                                session_id, _seam_entry_text, response_text):
                            _hub()._log_route(
                                "POST", event_detail="persisted_body_reconciled",
                                reason="advisory_tail_stripped",
                                chars_from=len(_seam_entry_text or ""),
                                chars_to=len(response_text or ""),
                                session_id=session_id)
                        elif (_seam_entry_text or "") != (response_text or ""):
                            _hub()._log_route(
                                "POST", event_detail="persisted_body_reconcile_failed",
                                reason="rewrite_no_match",
                                chars_from=len(_seam_entry_text or ""),
                                chars_to=len(response_text or ""),
                                session_id=session_id)
                    except Exception:  # noqa: BLE001 — provenance never breaks delivery
                        _hub().logger.debug("persisted reconcile error", exc_info=True)
                else:
                    # handback_failure / withhold_echo replace the body.
                    _hub()._log_route("POST", event_detail="post_seam_skip",
                                      reason="echo_" + str(getattr(_echo, "action", "") or "handback"),
                                      session_id=session_id)
                    # R36m: the strip branch above reconciles the persisted row
                    # (R34-19L) and this branch never did — it returns a
                    # replacement body and skips the rest of the seam, so
                    # nothing ever writes that body to the transcript. Live
                    # specimen: sessions api_1792643928_e46a2731 and
                    # api_1792644362_722dd41f ended with a single 0-char
                    # assistant row while the log showed transform_pass
                    # chars=699 -> advisory_echo_blocked -> post_seam_skip and
                    # NOTHING after it (no canonical_committed, no
                    # render_swap_decision), while every clean turn in the same
                    # window shows both. The replacement body has to reach the
                    # row that will be read back, or the delivered text and the
                    # transcript disagree — the exact unfalsifiable
                    # `delivered=True` shape R34-19L was written to kill.
                    try:
                        from ..core import canonical as _can_hb
                        # Two steps, because the exact-content guard cannot
                        # reach the row that is actually there. Proven against a
                        # copy of coder's real store (session
                        # api_1792643928_e46a2731): the row for the hand-back
                        # turn is EMPTY, not the 699-char echo, so
                        # rewrite_persisted_turn(entered) misses — and
                        # allow_empty_match only engages when refusal_text is
                        # falsy (it is the else-branch), which is why passing
                        # the entry text with the flag set still missed.
                        _ok = _can_hb.rewrite_persisted_turn(
                            session_id, _seam_entry_text, _echo.body)
                        if not _ok:
                            _ok = _can_hb.rewrite_persisted_turn(
                                session_id, "", _echo.body,
                                allow_empty_match=True)
                        _hub()._log_route(
                            "POST",
                            event_detail=("handback_persist_reconciled" if _ok
                                          else "handback_persist_miss"),
                            reason=str(getattr(_echo, "action", "") or ""),
                            chars_from=len(_seam_entry_text or ""),
                            chars_to=len(_echo.body or ""),
                            session_id=session_id)
                    except Exception:  # noqa: BLE001 — never breaks delivery
                        _hub().logger.debug("handback persist error", exc_info=True)
                    try:
                        from ..core import canonical as _can_chk
                        # verify against the DELIVERED text, not against the
                        # row we just wrote — a check that reads back what it
                        # wrote proves nothing.
                        _hub()._log_route(
                            "POST", event_detail="handback_delivery_check",
                            persisted_verified=_can_chk.verify_persisted_turn(
                                session_id, _echo.body or ""),
                            chars=len(_echo.body or ""),
                            session_id=session_id)
                    except Exception:  # noqa: BLE001
                        pass
                    return _echo.body
            else:
                _eg.observe(response_text, session_id,
                            log_route=_hub()._log_route)
        except Exception:  # noqa: BLE001 — guard never breaks delivery
            pass
        # R37-3 D1: LANE-MARKER QUARANTINE at the delivery boundary. The lane's
        # advisory is injected into the in-flight request as an assistant
        # message; a model can echo its marker into the delivered body (live:
        # "[END ROUTER FORK NOTE]" opening a probe turn before the answer —
        # session api_1792679326-era, ledger row 4065). The pre-existing strip
        # knew only the exact opening forms. A marker is lane furniture, never
        # work product: remove it from EVERY delivered body, on every frame.
        try:
            from .. import echo_guard as _eg_mk

            _mk_body, _mk_removed = _eg_mk.strip_lane_markers(response_text)
            if _mk_removed > 0:
                response_text = _mk_body
                _hub()._log_route("POST", event_detail="lane_marker_stripped",
                                  chars_removed=_mk_removed,
                                  session_id=session_id)
        except Exception:  # noqa: BLE001 — marker guard never breaks delivery
            pass
        _hook_t0 = _hub().time.monotonic()

        def _hook_remaining() -> float:
            # seconds left of the runner-safe budget on THIS hook invocation
            return max(0.0, 25.0 - (_hub().time.monotonic() - _hook_t0))
        if not isinstance(response_text, str) or not response_text.strip():
            # SPEC §1 EMPTY-BODY RULE (Goran 2026-10-28). A blank turn is a
            # DEFECT, not politeness: read the last ask, and
            #   - uncensored-class ask  -> ROUTE (the model produced nothing;
            #     the chain answers it, through the same delivery boundary);
            #   - any other ask         -> deliver the blank as a VISIBLE
            #     failure. Never a silent 0-char turn.
            # The pre pattern set is used here as an ASK classifier only — it
            # decides nothing before the model answers (PRE routing is dead),
            # it classifies a body that arrived empty.
            _ask_empty = ""
            try:
                _ask_empty = str(context.get("user_message") or "") \
                    or (_hub().state.get_last_seen(session_id) or "") \
                    or (_hub().session_store.get_last_user_message(session_id) or "")
            except Exception:  # noqa: BLE001 — ask recovery never breaks delivery
                _ask_empty = ""
            _unc_empty = False
            try:
                _unc_empty = bool(_hub().classifier.scan_pre(
                    _ask_empty, patterns=_hub()._pre_patterns(),
                    case_sensitive=bool(
                        _hub()._classification_cfg().get("case_sensitive", False))))
            except Exception:  # noqa: BLE001 — classifier gap = ordinary ask
                _unc_empty = False
            _hub()._log_route(
                "POST", event_detail="empty_body_rule",
                ask_chars=len(_ask_empty), uncensored_ask=bool(_unc_empty),
                routed=int(bool(_unc_empty)), session_id=session_id)
            if _unc_empty and _ask_empty.strip():
                try:
                    _persona_empty = _hub().persona_system_prompt(None)
                except Exception:  # noqa: BLE001
                    _persona_empty = ""
                _rendered_empty = None
                try:
                    _rendered_empty = _hub().router.call(
                        _ask_empty, system_prompt=_persona_empty,
                        session_id=session_id)
                except Exception:  # noqa: BLE001 — fail-open to the visible failure
                    _rendered_empty = None
                if _rendered_empty and str(_rendered_empty).strip():
                    _rendered_empty = _hub().cap_render(
                        _rendered_empty, _hub().render_max_chars())
                    # A1: the render IS this turn — recorded and written to
                    # state.db over the empty row, byte-identical to delivery.
                    try:
                        from .. import render_inbox as _rie_r
                        _rie_r.record_render("POST_EMPTY_BODY", session_id, 0,
                                             _rendered_empty)
                        from .. import assimilation as _assim_e
                        _assim_e.mark_turn_rendered(session_id)
                    except Exception:  # noqa: BLE001
                        _hub().logger.debug("empty-body render inbox error",
                                            exc_info=True)
                    try:
                        from .. import canonical as _ce_r
                        _rewrote_e = _ce_r.rewrite_persisted_turn(
                            session_id, "", _rendered_empty,
                            allow_empty_match=True)
                        _hub()._log_route(
                            "POST", event_detail="render_swap_decision",
                            refusal_chars=0, rendered_chars=len(_rendered_empty),
                            rewritten=bool(_rewrote_e),
                            persisted_verified=bool(
                                _ce_r.verify_persisted_turn(session_id,
                                                            _rendered_empty)),
                            delivered_source="post_empty_body",
                            session_id=session_id)
                    except Exception:  # noqa: BLE001 — never breaks delivery
                        _hub().logger.debug("empty-body persist error",
                                            exc_info=True)
                    return _rendered_empty
                # Chain unavailable: the blank does not become content, but it
                # still may not be silent — fall through to banner/failure.
                _hub()._log_route("POST", event_detail="render_unavailable",
                                  reason="empty_body", session_id=session_id)
            # FIX-FIRST rider 4 (item 1/3, parked-loss): a 0-char model body
            # used to bypass the transform entirely — the early return meant
            # the benign consume below was never reached, so any parked
            # verdict banner could never attach to an empty body.
            try:
                from .. import decision_midturn as _dmt_empty
                from .. import debug_banner as _dbmt_empty
                _mt_empty = _dmt_empty.close_turn(session_id)
                if _mt_empty:
                    _dbmt_empty.park_anchor_banner(session_id, _mt_empty,
                                                   task_id="midturn")
            except Exception:  # noqa: BLE001 — banner must never break delivery
                _hub().logger.debug("decision midturn banner (empty-body) error",
                             exc_info=True)
            try:
                from ..core import telemetry as _tlm_e
                from ..features.banners import lifecycle as _bll_e
                _hub()._decision_wait_before_consume(_hook_remaining())
                try:
                    _pb = _bll_e.LIFECYCLE.deliver(
                        session_id, "", "anchor", "empty_body", mode="body",
                        log_edge="empty_body")
                except _bll_e.IllegalDeliveryEdge as _ide_e:
                    # chokepoint fail-open: the empty body is still
                    # delivered as-is (I2 intact); telemetry row.
                    _tlm_e.log_route("POST", event_detail="banner_deliver_fail",
                                     kind_id=_ide_e.kind_id, edge=_ide_e.edge,
                                     session_id=session_id)
                    _pb = ""
                if _pb:
                    try:
                        from .. import render_inbox as _rie
                        _rie.record_render("EMPTY_BODY_BANNER", session_id,
                                           0, _pb)
                        from .. import canonical as _ce
                        _ce.rewrite_persisted_turn(session_id, "", _pb,
                                                   allow_empty_match=True)
                        _hub()._log_route(
                            "POST",
                            event_detail="banner_render_captured",
                            edge="empty_body", session_id=session_id)
                    except Exception:  # noqa: BLE001 — capture never breaks delivery
                        _hub().logger.debug("empty-body banner capture error",
                                     exc_info=True)
                    return _pb
            except Exception:  # noqa: BLE001 — hook must never raise
                _hub().logger.debug("empty-body parked-banner delivery error",
                             exc_info=True)
            # No parked banner to show: the blank is delivered as a VISIBLE
            # failure (spec §1). Persisted too — persisted == delivered is the
            # A1 invariant on every edge, including this one. Never a silent
            # 0-char turn.
            _empty_visible = (
                "[hermes-router] Empty model response — this turn produced no "
                "content. That is a delivery failure, not an answer. The turn "
                "was not silently dropped: re-ask, or retry the turn.")
            try:
                from ..features.detection import denial_class as _dcv
                _empty_visible = _dcv.EMPTY_BODY_FAILURE
            except Exception:  # noqa: BLE001 — text fallback never breaks
                pass
            try:
                from ..core import canonical as _ce_v
                from .. import render_inbox as _rie_v
                _rie_v.record_render("EMPTY_BODY_FAILURE", session_id, 0,
                                     _empty_visible)
                _ce_v.rewrite_persisted_turn(session_id, "", _empty_visible,
                                             allow_empty_match=True)
            except Exception:  # noqa: BLE001 — capture never breaks delivery
                _hub().logger.debug("empty-body failure capture error",
                                    exc_info=True)
            _hub()._log_route("POST", event_detail="empty_body_visible_failure",
                              delivered_chars=len(_empty_visible),
                              session_id=session_id)
            return _empty_visible

        # R16-2c (rider 16): orphan recovery runs BEFORE any delivery branch
        # of this edge — an unconsumed staged swap at transform time means
        # no llm_execution pass of the turn ever executed the consult.
        try:
            _hub()._recover_orphan_anchor_swap(session_id)
        except Exception:  # noqa: BLE001 — recovery must never break delivery
            pass

        # R11 anti-bypass audit (POST turn close): provider-direct tool
        # calls this turn with no route -> ONE content-free
        # provider_direct_call_unrouted event (host + session only).
        # Observability only — never blocks, never rewrites, returns None.
        # R15 LEG 2: escalated from log-only to a one-line visibility banner
        # appended to the DELIVERED turn (no enforcement, no blocking).
        try:
            from .. import bypass_watch as _bw

            _unrouted_banner = _bw.audit_turn(session_id, _hub()._log_route)
        except Exception:  # noqa: BLE001 — observability only
            _hub().logger.debug("bypass-watch audit error", exc_info=True)
            _unrouted_banner = ""

        # R19 step 2 (spec §10.2): decision POST leg — turn-close scan for
        # decision-shaped ACTIONS (branch choices, retries, aborts, option
        # picks), frame+score via the existing async worker, parked advisory
        # at the next delivery boundary on precedent contradiction, every
        # audited decision written to decision_records (post_audit tag).
        # Level-gated (decision.enabled AND decision.post_audit, both default
        # false) — total no-op in the v0 dark default.
        try:
            from .. import decision_miner as _dminer

            _dminer.post_audit_scan(session_id, response_text, model=model,
                                    context=context, log_route=_hub()._log_route)
        except Exception:  # noqa: BLE001 — POST leg never breaks delivery
            _hub().logger.debug("decision post-audit error", exc_info=True)

        # R19 v3 (user-locked §2 addendum): POST fork-scan leg — scan the
        # turn for enumerated options / open decision points; options found
        # -> backend verdict APPENDED as advisory steering (parked-banner
        # mechanics, never replaces delivery); no options -> no call. Also
        # maintains the §5.7 tape-recorder tail (actual_choice). Gated
        # (decision.enabled AND decision.post) — no-op in the dark default.
        try:
            from .. import decision as _dlane3

            _dlane3.post_fork_scan(session_id, response_text, model=model,
                                   log_route=_hub()._log_route)
        except Exception:  # noqa: BLE001 — POST leg never breaks delivery
            _hub().logger.debug("decision post-fork-scan error", exc_info=True)

        # R19.2 LEG 3: midturn decision hook — aggregate banner at turn
        # close. Midturn verdicts NEVER emit their own banner mid-run (no
        # delivery boundary exists there); when >=1 verdict was consumed
        # this run, park ONE aggregate banner (or the single-verdict format
        # for exactly 1) via the existing park/consume mechanics — the
        # consume sites below deliver it at the turn's actual delivery edge.
        # Mode-gated upstream (decision.midturn) — total no-op when off.
        try:
            from .. import decision_midturn as _dmt
            from .. import debug_banner as _dbmt

            _mt_banner = _dmt.close_turn(session_id)
            if _mt_banner:
                _dbmt.park_anchor_banner(session_id, _mt_banner,
                                         task_id="midturn")
        except Exception:  # noqa: BLE001 — banner must never break delivery
            _hub().logger.debug("decision midturn banner error", exc_info=True)

        def _attach_unrouted(text: str) -> str:
            """R15 LEG 2: append the unrouted direct-call visibility banner
            to the DELIVERED representation when one fired this turn. Empty
            banner / empty text -> unchanged. Never raises."""
            try:
                b = str(_unrouted_banner or "")
                if b and isinstance(text, str) and text.strip():
                    return text + "\n\n" + b
                return text
            except Exception:  # noqa: BLE001
                return text

        case_sensitive = bool(_hub()._classification_cfg().get("case_sensitive", False))
        # ------------------------------------------------------------------
        # SPEC §2 (Goran 2026-10-28): THE FOUR-CLASS DETECTOR IS THE TRIGGER.
        # Detection fires on the DELIVERED BODY only — never on request
        # content, never on render type. One question is asked of the body:
        # is this a refusal of the ACTION that was requested?
        #
        #   action_denial   -> ROUTE (and ONLY this class routes)
        #   truth_denial    -> no route ("I can't verify", "I don't know",
        #                      "outside my knowledge", "I need the file first")
        #   capacity_limit  -> no route (no access / no tool / input too large)
        #   not_a_denial    -> no route (compliance, hedged or partial)
        #
        # The discriminator is the OBJECT of the refusal, never the modal.
        # If honesty starts routing, the instrument has become a moralising
        # judge and is worse than no instrument (spec §5).
        #
        # A detector ERROR is the one path that still consults the legacy
        # chain below: fail-open means "behave as before", never "never
        # route again".
        # ------------------------------------------------------------------
        matches: List[str] = []
        semantic_verdict: Optional[str] = None
        _trigger_kind: Optional[str] = None
        _denial_class = ""
        try:
            from ..features.detection import denial_class as _dcls

            _denial_class = _dcls.classify(response_text)
        except Exception:  # noqa: BLE001 — detector gap must not block delivery
            _denial_class = ""
            _hub().logger.debug("four-class detector error", exc_info=True)
        _hub()._log_route("POST", event_detail="denial_class",
                          cls=(_denial_class or "unavailable"),
                          body_chars=len(response_text), session_id=session_id)
        # JOB C (unit 2, 2026-10-28): live misses were undiagnosable — the
        # refusal body is overwritten by the render and lands in no ledger, so
        # every fix was validated against a reconstruction the live model never
        # said. This is a BOUNDED fingerprint taken at the moment the detector
        # classifies: the class, the body length, and the first 120 chars with
        # whitespace collapsed. It is the MAIN MODEL'S body (the refusal), not
        # render content — the never-store-render rule is untouched, the full
        # body is never logged, and nothing is redacted beyond the 120c cap.
        try:
            _probe_head = " ".join(str(response_text or "").split())[:120]
            _hub()._log_route(
                "POST", event_detail="denial_class_body_probe",
                cls=(_denial_class or "unavailable"),
                body_chars=len(response_text or ""),
                head=_probe_head, head_chars=len(_probe_head),
                session_id=session_id)
        except Exception:  # noqa: BLE001 — observability never breaks delivery
            _hub().logger.debug("denial_class body probe error", exc_info=True)
        _class_authoritative = _denial_class in ("truth_denial", "capacity_limit")
        # §2a (2026-10-29, Goran): POST routes ONLY on the model's own declared
        # refusal. The deterministic action_denial classifier no longer
        # triggers a route: it fired on ordinary replies (polite declines,
        # explicit/gore content with no declaration). Class is still logged.
        _class_triggers = False
        # SPEC §2a.4: THE DECLARATION IS THE TRIGGER. One row, always written,
        # so "no declaration" is never indistinguishable from "declared but
        # the bit was ignored".
        try:
            _hub()._log_route(
                "POST", event_detail="declaration_seen",
                decl=_declaration, cls=(_denial_class or "unavailable"),
                chars=len(response_text or ""), session_id=session_id)
        except Exception:  # noqa: BLE001 — observability never breaks delivery
            pass
        if _declaration == "true":
            # The half of the frozen contract that routes: identical shape to
            # the action_denial class below, so it enters the SAME existing
            # route path (post_class bypasses the shape-only demotion).
            matches = ["action_denial"]
            _trigger_kind = "post_class"
            _hub()._log_route("POST", event_detail="declaration_route",
                              decl="true", cls=(_denial_class or "unavailable"),
                              chars=len(response_text or ""),
                              session_id=session_id)
        elif _declaration == "false":
            # Authoritative NO-ROUTE. No classifier fallback, no aux spend, no
            # judge: the model said it did not decline, and re-deriving that
            # from prose is exactly the inference §2a replaces.
            matches = []
            _trigger_kind = None
            # `_class_authoritative` means "the trigger is settled; do not
            # fall through to the semantic sweep". A declared-false turn is
            # settled the same way, so the existing sweep gate (which is
            # pinned by test_denial_class_primacy) keeps its shape and the
            # sweep stays closed for this turn.
            _class_authoritative = True
            _hub()._log_route("POST", event_detail="declaration_no_route",
                              decl="false", cls=(_denial_class or "unavailable"),
                              chars=len(response_text or ""),
                              session_id=session_id)
        elif _class_triggers:
            matches = ["action_denial"]
            # The deterministic classifier IS the refusal semantics — the
            # shape-only gate further down must not demote it, and the
            # flinch-reason/grounding arms (which exist to second-guess a
            # regex or a shape flag) do not apply to a class decision.
            _trigger_kind = "post_class"
            # NOTE on classification.mode: `flag_only` governs the AUX/semantic
            # stage it was written for, not this detector. Scope it there —
            # making it a router-wide dry switch silently stopped every
            # structural route in the fleet's existing flag_only configurations.

        elif _class_authoritative:
            # HARD BLOCK (the defect this unit kills): a truth denial or an
            # environment limit never routes. No aux spend, no judge, no
            # shape gate. If honesty starts routing, the instrument has
            # become a moralising judge and is worse than no instrument.
            _hub()._log_route("POST", event_detail="detection_non_action_denial",
                              cls=_denial_class, routed=0,
                              body_chars=len(response_text),
                              session_id=session_id)
        else:
            # FAIL-OPEN to the legacy chain: not_a_denial (no recognised
            # decline in the body — NOT proof of compliance) and a detector
            # error both land here. Routing only ever happens if the legacy
            # refusal-SEMANTICS gate confirms a decline (R33m), so the
            # fail-closed direction the spec forbids — silently stopping
            # routes for every register the classifier doesn't know — is
            # avoided without re-admitting shape-only swaps.
            matches = _hub().classifier.scan_post(response_text, patterns=_hub()._post_patterns(), case_sensitive=case_sensitive)
            _trigger_kind = "stage1" if matches else None
            # SPEC §2a (Goran 2026-10-28) — STRIP #2: no second LLM.
            #
            # A stage-1 miss used to fall into stage-2 here:
            # _semantic_stage -> semantic_classifier.classify(), an AUX LLM
            # deciding whether to route. It was live in the fleet on every
            # profile (classification.aux_classify=true, mode=route), so any
            # turn the regex missed was judged by a model.
            #
            # Goran: "we dont need semantic judge and llms. main model is asked
            # to define denial and if its triggered route is activated. keep it
            # simple."
            #
            # scan_post above is now the ONLY fallback. The denial decision
            # surface is exactly two things:
            #   (a) the model's own declaration (declined="true" -> route);
            #   (b) deterministic vocabulary — scan_post + denial_class
            #       (action_denial routes; truth_denial/capacity_limit do not).
            # semantic_classifier.py itself stays: four other consumers (risk,
            # refusal_doctrine, decision_head, intent_classifier) still call it.
            # Only this call site is gone.
        # SPEC §2a (Goran 2026-10-28): THE SEMANTIC JUDGE IS GONE FROM THE
        # DENIAL PATH.
        #
        # The frozen contract is explicit: scoring is deterministic with "No
        # LLM judge in the pass/fail path" (line 40), and the trigger is "the
        # model's own declaration in its final message, NOT an inference over
        # its content" (line 42). The R22 two-tier scan was an inference layer
        # bolted on top of a contract that already defines the trigger — it
        # contradicted §2a, and soft denials it hunted are explicitly out of
        # scope ("insufficiency, not refusal", line 35).
        #
        # So an UNDECLARED (UNKNOWN) turn now falls through with exactly what
        # it had before the judge: the deterministic detector above.
        #   _class_triggers (action_denial)      -> matches seeded -> routes
        #   _class_authoritative (truth/capacity) -> does NOT route
        #   anything else                         -> legacy stage-1/2 above
        # No LLM, no network, no aux spend is reachable from this decision.
        if matches:
            # A turn that routed is answered by the render lane, not audited.
            # Logged so "no post consult" is never explainable by silence: the
            # six-week disappearance of this arm was invisible precisely
            # because every non-firing path returned without a row.
            _hub()._log_route("POST", event_detail="audit_arm_skipped",
                              reason="detection_route", trigger_kind=str(_trigger_kind or ""),
                              session_id=session_id)
        if not matches:
            # v3.6.1 completion-audit arm — unified audit_gate (Goran
            # 2026-09-08 ruling + 09-10 battery): the ONLY automatic frontier
            # touchpoint at completion. Previously nested under stage-2's
            # `if not matches`, refusal-phrase FP passthroughs skipped the
            # audit entirely (closure responses are the most refusal-shaped
            # text — live-caught). audit_gate handles fire policy, sync
            # consult, revision pass, banner; returns revised text or None.
            try:
                from .. import completion_audit as _ca
                _out_audit = _ca.audit_gate(
                    session_id, response_text, model=model, context=context,
                    hook_budget=_hook_remaining())
                # R34-19b: the arm's VERDICT is always on record. Without this
                # row, 'no post consult' could mean the arm never ran, or ran
                # and declined — indistinguishable, which is exactly how the
                # gap survived six weeks.
                _hub()._log_route("POST", event_detail="audit_gate_returned",
                                  returned=("text" if _out_audit else "none"),
                                  chars=len(_out_audit or "") if _out_audit else 0,
                                  session_id=session_id)
                if _out_audit:
                    # R9 (2026-09-14): the audit's sync return IS this turn's
                    # delivery edge — any banner parked during the SAME turn
                    # (frontier PRE consult, shadow render) must merge into
                    # THIS return, or the benign-branch consume below is
                    # never reached and the parked banner dies (one-shot
                    # consume -> silently dropped). Merging here keeps the
                    # single-shot guarantee: the consume happens exactly
                    # once, on the turn that actually delivers.
                    try:
                        from ..core import telemetry as _tlm_a
                        from ..features.banners import lifecycle as _bll_a
                        _hub()._decision_wait_before_consume(_hook_remaining())
                        try:
                            _out_audit = _bll_a.LIFECYCLE.deliver(
                                session_id, _out_audit, "anchor",
                                "audit_sync", log_edge="audit_sync")
                        except _bll_a.IllegalDeliveryEdge as _ide_a:
                            # chokepoint fail-open: the body is still
                            # delivered (I2 intact); telemetry row instead
                            # of a silent miss.
                            _tlm_a.log_route(
                                "POST", event_detail="banner_deliver_fail",
                                kind_id=_ide_a.kind_id, edge=_ide_a.edge,
                                session_id=session_id)
                    except Exception:  # noqa: BLE001 — banner never breaks delivery
                        pass
                    # FIX-FIRST rider 4 (item 1, parked-loss): the audit_sync
                    # return IS the turn's delivery edge — capture the
                    # DELIVERED text (render inbox + persisted-turn rewrite)
                    # so the transcript carries the banner instead of the
                    # pre-transform raw row (turn_finalizer persists BEFORE
                    # this hook fires).
                    try:
                        from .. import render_inbox as _ria
                        from .. import canonical as _ca2
                        _ria.record_render("AUDIT_SYNC_BANNER", session_id,
                                           len(response_text), _out_audit)
                        if _ca2.rewrite_persisted_turn(
                                session_id, response_text, _out_audit):
                            _hub()._log_route(
                                "POST",
                                event_detail="banner_render_captured",
                                edge="audit_sync", session_id=session_id)
                    except Exception:  # noqa: BLE001 — capture never breaks delivery
                        _hub().logger.debug("audit_sync banner capture error",
                                     exc_info=True)
                    # R19.11 FIX 1: final-delivery gate — re-emit a held
                    # decision banner when this text lacks the marker.
                    try:
                        from .. import debug_banner as _dbs
                        _out_audit = _dbs.settle_decision_banner(
                            session_id, _out_audit)
                    except Exception:  # noqa: BLE001
                        pass
                    return _out_audit
            except Exception as _audit_err:  # noqa: BLE001 — audit must never break delivery
                # Fail-open, but NOT silent: this swallow is why the completion
                # arm could vanish for six weeks with no row anywhere. The
                # delivery still proceeds untouched; the reason is now on record.
                _hub().logger.debug("completion audit gate error", exc_info=True)
                _hub()._log_route("POST", event_detail="audit_arm_error",
                                  error=type(_audit_err).__name__,
                                  detail=str(_audit_err)[:160],
                                  session_id=session_id)
            # Benign delivery — §10.4: consume any parked frontier-anchor
            # banner and append to this turn's DELIVERY (one-shot).
            try:
                from ..core import telemetry as _tlm_b
                from .. import debug_banner as _dbp
                from ..features.banners import lifecycle as _bll_b
                # R34-15: park turn vs live turn decides retention vs drop.
                _park_turn, _cur_turn = _banner_turn_snapshot(session_id)
                _hub()._decision_wait_before_consume(_hook_remaining())
                try:
                    _merged_b = _bll_b.LIFECYCLE.deliver(
                        session_id, response_text, "anchor", "benign",
                        log_edge="benign")
                except _bll_b.IllegalDeliveryEdge as _ide_b:
                    # chokepoint fail-open: the body is still delivered
                    # (I2 intact); telemetry row instead of a silent miss.
                    _tlm_b.log_route("POST", event_detail="banner_deliver_fail",
                                     kind_id=_ide_b.kind_id, edge=_ide_b.edge,
                                     session_id=session_id)
                    return _attach_unrouted(response_text) \
                        if _unrouted_banner else None
                # the landed banner text (for the R10-5 capture-repark
                # blocks below) — recovered from the merged delivery text
                # (append_banner joins with blank lines).
                _parked = ""
                if isinstance(_merged_b, str) and _merged_b and \
                        _merged_b != str(response_text or ""):
                    _parked = _merged_b[len(str(response_text or "")):] \
                        .lstrip("\n")
                if not _parked:
                    # a re-parked (vanish-path) banner is back in the slot —
                    # the R10-5 capture blocks below still need its text.
                    _parked = _bll_b.LIFECYCLE.peek(session_id)
                if _parked:
                    # R9/R19.21: consume+append+repark-on-vanish are the
                    # chokepoint's (deliver) semantics now — _merged_b is
                    # the delivered text.
                    _final_b = _dbp.settle_decision_banner(session_id,
                                                           _merged_b)
                    # FIX-FIRST rider 4 (item 1, parked-loss): the benign
                    # edge replaces the turn tail — capture the DELIVERED
                    # text (render inbox + persisted-turn rewrite) so the
                    # transcript carries the banner instead of the raw
                    # pre-transform row (turn_finalizer persists BEFORE
                    # this hook fires; the render seam had this capture,
                    # the benign edge did not — reviewer: persistence fix
                    # does not capture renders on EVERY parked path).
                    if _final_b != response_text:
                        try:
                            from .. import render_inbox as _rib
                            from .. import canonical as _cb2
                            _rib.record_render("BENIGN_BANNER", session_id,
                                               len(response_text), _final_b)
                            if _cb2.rewrite_persisted_turn(
                                    session_id, response_text, _final_b):
                                _hub()._log_route(
                                    "POST",
                                    event_detail="banner_render_captured",
                                    edge="benign", session_id=session_id)
                            else:
                                # R10-5 (rider 10, A2 analyst): a consume that
                                # SUCCEEDED while the persisted rewrite missed
                                # is a silent parked-loss — fail loud. R33f-A
                                # (supersedes the R10-5 re-park): TTL is ONE
                                # delivery — the banner is DROPPED, never
                                # re-parked into later turns (the re-park
                                # loop was the live stale-leak class,
                                # session 20261006_190903_c82cbbcc).
                                try:
                                    _hub()._log_route(
                                        "POST",
                                        event_detail="parked_capture_failed",
                                        edge="benign",
                                        reason="rewrite_no_match",
                                        row_present=bool(
                                            _cb2.persisted_turn_row_exists(
                                                session_id, response_text)),
                                        capture_landed=False,
                                        turn_no=_cur_turn,
                                        session_id=session_id)
                                    # R34-15 item 1: a mid-turn miss RETAINS
                                    # (same turn); only a closed turn drops.
                                    _resolve_midturn_capture_failure(
                                        session_id, _parked, _park_turn,
                                        _cur_turn, "mid_turn_no_match",
                                        body_chars=len(str(response_text or "")),
                                        row_present=True)
                                except Exception:  # noqa: BLE001
                                    pass
                        except Exception:  # noqa: BLE001 — capture never breaks delivery
                            _hub().logger.debug("benign banner capture error",
                                         exc_info=True)
                            try:
                                # R10-5: capture THREW — same fail-loud
                                # contract. R33f-A (supersedes the R10-5
                                # re-park): TTL is ONE delivery — the banner
                                # is DROPPED, never re-parked into later
                                # turns (the stale-leak class R33f-A closes).
                                _hub()._log_route("POST",
                                           event_detail="parked_capture_failed",
                                           edge="benign",
                                           reason="capture_exception",
                                           capture_landed=False,
                                           turn_no=_cur_turn,
                                           session_id=session_id)
                                _resolve_midturn_capture_failure(
                                    session_id, _parked, _park_turn,
                                    _cur_turn, "mid_turn_capture_exception",
                                    body_chars=len(str(response_text or "")),
                                    row_present=False)
                            except Exception:  # noqa: BLE001
                                pass
                    return _final_b
            except Exception:  # noqa: BLE001 — banner must never break delivery
                pass
            # R15 LEG 2: benign pass-through is still a delivery edge —
            # attach the unrouted direct-call banner when one fired. No
            # banner -> None (pass-through contract unchanged).
            return (_attach_unrouted(response_text) if _unrouted_banner
                    else _decl_passthrough())

        session_id = session_id or ""
        model = model or ""

        # R33m refusal-semantics gate (Goran 10-10 ruling: the POST render
        # swap fires on REFUSALS ONLY — never content/render-type/shape
        # inspection). A shape-only trigger (line_hold_essay alone, essay
        # length/structure, a T1 shape flag without decline semantics) LOGS
        # as a detection event but NEVER routes. Judge-confirmed refusals
        # (t2_confirmed) carry semantics by definition and pass. Code-side,
        # non-tunable (csam-gate class). Fail-open: a gate error leaves the
        # route standing. The original body still delivers — this is a
        # delivery edge (parked banners consume exactly once here).
        if matches and _trigger_kind not in ("t2_confirmed", "post_class"):
            try:
                from ..features.detection import structural as _r33m_st

                if not _r33m_st.has_refusal_semantics(response_text):
                    _hub()._log_route(
                        "POST", event_detail="detection_shape_only",
                        pattern_groups=",".join(matches),
                        answer_chars=len(response_text),
                        routed=0, session_id=session_id)
                    return _hub()._deliver_parked_at_edge(
                        session_id, response_text, "detection_shape_only",
                        _hook_remaining())
            except Exception:  # noqa: BLE001 — fail-open: route stands
                pass

        # Flinch-vs-line verdict (Option 3 hybrid, 2026-09-02): before routing,
        # check the agent's OWN doctrine. A refusal backed by her authored
        # lines is HONORED (pass-through); substrate caution with no doctrine
        # row routes to the chain. Fail-open: no card/aux error -> None ->
        # route (status-quo behavior preserved on any doctrine-machinery gap).
        if _hub()._doctrine_verdict_enabled():
            try:
                _user_ask = context.get("user_message") or _hub().state.get_last_seen(session_id) or ""
                dv = _hub().refusal_doctrine.verdict_for_refusal(response_text, str(_user_ask))
                if dv == "agent_line":
                    _hub()._log_route("POST", event_detail="agent_line_honored",
                               session_id=session_id, model=model,
                               refusal_chars=len(response_text))
                    # R15-5: honored agent-line turns are still delivery
                    # edges — attach any parked verdict banner here.
                    return _hub()._deliver_parked_at_edge(session_id,
                                                   response_text,
                                                   "agent_line",
                                                   _hook_remaining())
                # dv in (None, "model_flinch") -> fall through to routing
            except Exception:  # noqa: BLE001 — verdict gap must never block routing
                _hub().logger.debug("doctrine verdict error", exc_info=True)

        # Recover the user message: (1) same-turn PRE stash (contested turns,
        # hash-match scopes consumption to THIS turn — Rev B3), (2) hook
        # context, (3) last-seen cache, (4) session store — the unconditional
        # POST fallback chain.
        last_user_msg_hash = _hub().state.get_last_user_msg_hash(session_id)
        original_user_message = None
        if last_user_msg_hash:
            original_user_message = _hub().state.pop_pending(
                session_id, model, ttl_seconds=_hub()._pending_ttl(),
                message_hash=last_user_msg_hash,
            )
        fallback = not original_user_message
        if fallback:
            hook_ctx = context.get("user_message") if context else None
            if isinstance(hook_ctx, str) and hook_ctx.strip():
                original_user_message = hook_ctx
            else:
                original_user_message = _hub().state.get_last_seen(session_id) or ""
            if not original_user_message.strip():
                original_user_message = _hub().session_store.get_last_user_message(session_id)

        if not original_user_message or not original_user_message.strip():
            # No recovery path — can't feed Venice.
            _hub()._log_route("POST", event_detail="no_pending_route", pattern_groups=",".join(matches),
                       refusal_chars=len(response_text), session_id=session_id)
            # R15-5: this is still a DELIVERY EDGE — a parked verdict banner
            # (anchor consult billed this turn) must attach here, not
            # survive past the turn. No banner -> plain passthrough.
            return _hub()._deliver_parked_at_edge(session_id, response_text,
                                           "no_pending_route",
                                           _hook_remaining())

        if fallback:
            # Content gate REMOVED 2026-09-04 (Goran-direct reversal: no
            # code-side filtering; substrate holds its own boundaries and
            # its refusals pass through unchanged). Previously blocked
            # csam_underage fallback routes here; now every class routes
            # through the fallback identically. Loop guard below prevents
            # render loops.
            pass

        # Loop-guard key: hash of the message being routed. On stash-recovery
        # turns that equals the PRE-recorded hash (identical to the legacy
        # key); on fallback turns it keys on the actually-recovered message —
        # identical across all transform_llm_output invocations for the same
        # turn, and distinct across different asks.
        guard_key = _hub().state.loop_guard_key(session_id, model, _hub().state.hash_text(original_user_message))
        if _hub().state.loop_guard_already_fired(guard_key):
            _hub()._log_route("POST", event_detail="loop_guard_skipped", session_id=session_id)
            return _decl_passthrough()

        # v3.1.0 canonical idempotency (split-brain fix, build brief): a turn
        # already canonicalized must not re-render or re-commit — kills the
        # re-fire loop multiplier (today's $1.13 false-positive burn re-fired
        # 4-10x per turn). Keyed per (session_id, refusal hash, turn marker)
        # where the marker is the recovered user-message hash, so two genuinely
        # different turns sharing a byte-identical refusal both still route.
        # Sits AFTER the loop guard (same-turn re-fires inside the 60s window
        # report loop_guard_skipped) and covers the beyond-window/restart case
        # the loop guard cannot (ledger warm from the sidecar on disk). The
        # check runs BEFORE the render call: no re-render, no re-commit.
        _refusal_hash = _hub().canonical.hash_text(response_text)
        if _hub().canonical.already_committed_for_turn(
                session_id, _refusal_hash,
                _hub().state.hash_text(original_user_message or "")):
            _hub()._log_route("POST", event_detail="canonical_skip", session_id=session_id)
            return _decl_passthrough()

        # Dry-run: log, don't rewrite.
        if _hub()._dry_run():
            _hub()._log_route("POST", event_detail="dry_run", pattern_groups=",".join(matches),
                       refusal_chars=len(response_text), session_id=session_id)
            return _decl_passthrough()

        # v3.1.1 contamination fix (Astra canonical-event doctrine, round-2
        # Q4c; live defect 2026-09-05 09:38:42 session api_1788600987_4f09ad3b):
        # continuation-style asks ("summarize what you just explained") fed
        # venice a persona card + a 600-char ask referencing prior content the
        # renderer could not see — it free-associated "the prior answer" from
        # persona memory (old go-debug sessions). Ground the render in THIS
        # session's canonical conversation: full current ask + last canonical
        # assistant answer. The prompt stays original_user_message; grounding
        # lives in the system persona context. ALL fail-open: fetch errors ->
        # previous behavior; grounding never blocks delivery.
        _grounded = False
        _post_ctx_msgs: List[dict] = []
        _ground_block = ""
        _last_answer = ""
        try:
            _last_user = _hub().state.get_last_seen(session_id) or original_user_message
            if _last_user:
                # FULL ask for the context message (600-char cut removed —
                # same false-chronology failure mode as the v3.1.0 keep-ask
                # fix); hard cap 4000 with graceful suffix.
                _ask_full = _last_user
                if len(_ask_full) > _hub().SUBSTANCE_FRAME_ASK_CAP:
                    _ask_full = _ask_full[:_hub().SUBSTANCE_FRAME_ASK_CAP] + _hub().SUBSTANCE_FRAME_ASK_SUFFIX
                _post_ctx_msgs.append({"role": "user", "content": _ask_full})
                _last_answer = _hub().canonical.get_last_canonical_answer(session_id)
                if _last_answer:
                    _post_ctx_msgs.append(
                        {"role": "assistant", "content": _last_answer})
                    # Explicit grounding block at FULL fetched size —
                    # build_thread_digest excerpts asks/turns to 220 chars,
                    # far too thin to summarize from. This is the actual anti-
                    # free-association payload: both sides of the last
                    # exchange, verbatim, at full (capped) size.
                    _ground_block = (
                        "[GROUNDING — this session's actual last exchange. The "
                        "user's new message refers to THIS exchange; answer "
                        "from it, not from memory or prior sessions:]\n"
                        "[the user's latest ask, verbatim]: " + _ask_full + "\n"
                        "[your previous turn, verbatim]: " + _last_answer)
                    _grounded = True
        except Exception:  # noqa: BLE001 — grounding must never block delivery
            _post_ctx_msgs = []
            _ground_block = ""
            _grounded = False
        _hub()._log_route("POST", event_detail="render_grounded", grounded=_grounded,
                   ask_chars=len(original_user_message or ""),
                   answer_chars=len(_last_answer),
                   session_id=session_id)

        # R33-FIX2 grounded-yield (audit close, F03 + V20 evidence): route
        # triggers carry PROVENANCE, and an UNCONFIRMED trigger defers to
        # evidence of substance when the grounding check found a substantive
        # answer (grounded=True, answer_chars>0). Two arms:
        #   - T1 FAIL-OPEN (verdict=None): yields on grounding alone — the
        #     audited F03 shape.
        #   - STAGE-1 REGEX / line_hold_essay family (V20: relayed 'I won't
        #     help' quote inside a substantive answer): yields only when the
        #     CURRENT body is itself substantive (>=300c). Real refusals are
        #     short holds; a quote-FP lives inside a long body — short real
        #     refusals keep routing after a substantive prior answer.
        # Semantic-CONFIRMED refusals (T2 verdict, stage-2 or detection_t2)
        # are NOT affected — a refusal is a refusal even after a substantive
        # answer. Log: detection_t1 decision=grounded_yield (routed=0).
        _gy_yield = (_trigger_kind == "t1_fail_open"
                     or (_trigger_kind == "stage1"
                         and len(response_text) >= 300))
        if _gy_yield and _grounded and len(_last_answer) > 0:
            # R33d (live bug, session 20261006_190903_c82cbbcc @19:35:17Z):
            # the "substantive" answer can BE the refusal — long, structured,
            # reasoning-heavy (the 452c 'The answer stands: no...' case).
            # Length is not substance. Content-check the grounded answer
            # against the same T1 shapes: refusal-shaped answer = not
            # substance -> route anyway (grounded_refusal, routed=1). Yield
            # survives only when the answer is substantive AND refusal-free
            # (grounded_yield, routed=0).
            try:
                from ..features.detection import structural as _gy_struct
                # R33m: the content-check passes the refusal-semantics gate
                # — a "refusal-shaped" prior answer WITHOUT decline
                # semantics (an honest status report: 'Not confirmed —
                # still unknown', 'unverified') is NOT a refusal; shape
                # alone never routes (live FP 2026-10-13 04:05:57, valmet).
                _gy_answer_refusal = bool(
                    (_gy_struct.is_candidate(_last_answer)
                     and _gy_struct.has_refusal_semantics(_last_answer))
                    or (_gy_struct.is_candidate(response_text)
                        and _gy_struct.has_refusal_semantics(response_text)
                        and _gy_struct.has_deliverable_content(response_text)))
            except Exception:  # noqa: BLE001 — content check never blocks
                _gy_answer_refusal = False
            try:
                from ..features.detection import semantic_judge as _r22sj_gy
                if _gy_answer_refusal:
                    _r22sj_gy._ledger_write("detection_t1", session_id,
                                            verdict="grounded_refusal",
                                            latency_s=0.0, model="",
                                            routed=True)
                    _hub()._log_route("POST", event_detail="detection_t1",
                               decision="grounded_refusal", routed=1,
                               session_id=session_id)
                    # fall through: the route stands; if the render then
                    # holds its own substrate line, that's the uncensored
                    # lane's output being her own — detection did its job.
                else:
                    _r22sj_gy._ledger_write("detection_t1", session_id,
                                            verdict="grounded_yield",
                                            latency_s=0.0, model="",
                                            routed=False)
                    _hub()._log_route("POST", event_detail="detection_t1",
                               decision="grounded_yield", routed=0,
                               session_id=session_id)
                    return _decl_passthrough()
            except Exception:  # noqa: BLE001 — ledger must never break the yield
                return _decl_passthrough()

        # Flinch-reason gate (Goran ruling 2026-09-08): a detected refusal
        # routes to the shadow chain ONLY when it is a CONTENT flinch
        # (censorship / policy discomfort). A TECHNICAL refusal (missing
        # access, no credentials, cannot execute) passes through unchanged —
        # the uncensored substrate cannot fix capability gaps, and rendering
        # one would fabricate an answer. Unknown/aux-failure fail-opens to
        # ROUTE (a missed content-flinch strands the user; a technical
        # FP-route costs one bounded render). Knob: flinch_reason_gate on|off.
        if _hub()._flinch_reason_gate() and _trigger_kind != "post_class":
            try:
                from ..flinch_reason import classify_flinch_reason

                _reason = classify_flinch_reason(original_user_message,
                                                 response_text)
                if _reason == "technical":
                    _hub()._log_route("POST", event_detail="flinch_reason_technical_passthrough",
                               pattern_groups=",".join(matches),
                               refusal_chars=len(response_text),
                               session_id=session_id)
                    # R15-5: this IS a delivery edge — a parked verdict
                    # banner (anchor consult billed this turn) must attach
                    # here, not survive past the turn (live T2e class).
                    return _hub()._deliver_parked_at_edge(session_id,
                                                   response_text,
                                                   "flinch_passthrough",
                                                   _hook_remaining())
                _hub()._log_route("POST", event_detail="flinch_reason_classified",
                           reason=_reason or "unknown", session_id=session_id)
            except Exception:  # noqa: BLE001 — gate gap must never block routing
                _hub().logger.debug("flinch_reason gate error", exc_info=True)

        try:
            _system_prompt = _hub()._persona_system_prompt({"messages": _post_ctx_msgs})
            if _ground_block:
                _system_prompt = ((_system_prompt + "\n\n" + _ground_block)
                                  if _system_prompt else _ground_block)
        except Exception:  # noqa: BLE001
            try:
                _system_prompt = _hub()._persona_system_prompt(None)
            except Exception:  # noqa: BLE001 — never break delivery on prompt build
                _system_prompt = ""
        rendered = _hub().router.call(
            original_user_message,
            system_prompt=_system_prompt,
            session_id=session_id,
        )
        if not rendered or not str(rendered).strip():
            _hub()._log_route("POST", event_detail="route_failed", pattern_groups=",".join(matches),
                       refusal_chars=len(response_text), session_id=session_id)
            # SPEC §3 fail-open: chain unavailable => the ORIGINAL turn is
            # delivered unmodified, and the miss is named. A routed turn that
            # silently loses its content is worse than an unrouted refusal.
            _hub()._log_route("POST", event_detail="render_unavailable",
                              reason="chain_returned_nothing",
                              trigger_kind=str(_trigger_kind or ""),
                              refusal_chars=len(response_text),
                              session_id=session_id)
            return _decl_passthrough()

        # v3.2.2 render delivery cap — applied at the delivery seam BEFORE
        # inbox/canonical commit/persisted-turn rewrite so the canonical
        # record's content_hash and state.db row both hash/store the CAPPED
        # text (canonical invariant: persisted == delivered).
        rendered = _hub().cap_render(rendered, _hub().render_max_chars())

        # A2 — OWN VOICE (spec §4, Goran 2026-10-28). The render becomes the
        # agent's OWN turn, so NO provenance footer rides it: the delivered
        # body carries no banner, no "an external model produced this", and
        # nothing else that invites the next turn to disown it. This
        # supersedes the 2026-09-07 footer on this seam by design — the
        # assimilated turn is her voice, and a provenance marker inside the
        # body is exactly the disowning signal A2 forbids.
        _hub()._log_route("POST", event_detail="provenance_footer_suppressed",
                          reason="a2_own_voice", session_id=session_id)

        # Render inbox (2026-09-02 sync seam): persist the render that REPLACES
        # the agent's response at delivery. Without this, the agent's context
        # (raw response) and the user's screen (render) diverge silently.
        _hub().render_inbox.record_render("POST", session_id, len(response_text), rendered)
        # FRAME unit (Goran 2026-10-28): this render IS the delivered turn, so
        # the next request must carry the assimilation frame exactly once —
        # one frame per lane, role="system", consumed by the PRE injection
        # point. Marking is fail-quiet: a lost mark costs a frame, never a turn.
        try:
            from .. import assimilation as _assim_frame
            _assim_frame.mark_turn_rendered(session_id)
        except Exception:  # noqa: BLE001 — never breaks delivery
            _hub().logger.debug("assimilation mark error", exc_info=True)

        _hub().state.loop_guard_mark_fired(guard_key)
        # v3.1.0 canonical-event commit: this render REPLACES the persisted
        # flash turn (turn_finalizer persists BEFORE this hook fires —
        # split-brain: state.db holds the refusal, user read the render).
        # Commit the canonical record + rewrite the persisted assistant turn
        # to the DELIVERED text so state.db canonical == delivered. Both are
        # idempotent per (session_id, refusal hash) and best-effort: any
        # failure never breaks delivery of the render.
        try:
            _turn_marker = _hub().state.hash_text(original_user_message or "")
            if _hub().canonical.commit_canonical_event(
                    session_id, _turn_marker, rendered, _refusal_hash,
                    grounded=_grounded):
                _hub()._log_route("POST", event_detail="canonical_committed",
                           session_id=session_id)
            _rewrote = _hub().canonical.rewrite_persisted_turn(
                session_id, response_text, rendered)
            if not _rewrote:
                # A1 SHIP-BLOCKER (spec §4, Goran 2026-10-28): if the swap
                # fails to persist, the next turn reads the DENIAL as its own
                # prior output and assimilation cannot happen — the known
                # inverse-delivery defect. One fallback match over the newest
                # empty/refusal row, then verify, then fail LOUD.
                try:
                    _rewrote = _hub().canonical.rewrite_persisted_turn(
                        session_id, "", rendered, allow_empty_match=True)
                except Exception:  # noqa: BLE001 — fallback never breaks delivery
                    _rewrote = False
                _hub()._log_route(
                    "POST", event_detail=("assimilation_persist_recovered"
                                          if _rewrote
                                          else "assimilation_persist_failed"),
                    reason="exact_match_missed",
                    refusal_chars=len(response_text),
                    rendered_chars=len(rendered), session_id=session_id)
        except Exception:  # noqa: BLE001 — must never break delivery
            _rewrote = False
            _hub().logger.debug("uncensored-router canonical commit error", exc_info=True)
        # R31 defect-1 (inverse delivery): fail-loud decision seam. The
        # invariant — if this hook renders (rendered_chars > 0), the
        # delivered/persisted turn MUST be the render. Previously the
        # rewrite result was discarded: a locked store or a missed exact
        # match left state.db holding the refusal with NO event (live
        # x-battery round 5, 2026-10-09 20:41/20:47Z, x3 + x6). The swap
        # decision is now always logged, with the persisted-row verification
        # (newest assistant row == delivered text) riding the same event.
        try:
            _persisted_verified = _hub().canonical.verify_persisted_turn(
                session_id, rendered)
            _hub()._log_route(
                "POST", event_detail="render_swap_decision",
                refusal_chars=len(response_text),
                rendered_chars=len(rendered),
                rewritten=bool(_rewrote),
                persisted_verified=bool(_persisted_verified),
                delivered_source="post_transform",
                session_id=session_id)
        except Exception:  # noqa: BLE001 — telemetry never breaks delivery
            pass
        # R26-3: track what the persisted row holds NOW — the banner blocks
        # below append to the DELIVERED text only, and the round-trip rewrite
        # at the pre_render merge fires only when a parked banner merged. Any
        # banner appended WITHOUT a merge (the auto-routed POST render class)
        # left the persisted row pre-banner: route events showed
        # debug_banner_emitted while the delivered record carried no banner
        # (live R25 battery WONT sessions ...c745e6e0 / ...ec54bec1 /
        # ...21a0d4ce). The final capture below rewrites the drift.
        _persisted_render_text = rendered
        if semantic_verdict:
            # Auditable residual (reviewer §B.1): a SEMANTIC verdict routed a
            # response that stage-1's regexes did NOT consider a refusal.
            # Enum label only — no content, no aux reason.
            _hub()._log_route("POST", event_detail="semantic_misroute_candidate",
                       verdict=semantic_verdict, session_id=session_id)
        if fallback:
            _hub()._log_route("POST", event_detail="route_fired_no_stash",
                       pattern_groups=",".join(matches),
                       refusal_chars=len(response_text), rendered_chars=len(rendered),
                       session_id=session_id)
        else:
            _hub()._log_route("POST", event_detail="route_fired", pattern_groups=",".join(matches),
                       refusal_chars=len(response_text), rendered_chars=len(rendered), session_id=session_id)
        # v3.6 §10.2 debug banner — POST render substitution fire point (same
        # contract as the PRE point: rides the DELIVERY representation only;
        # failure isolated; canonical artifacts above already written).
        try:
            from .. import debug_banner as _db
            if _db.debug_banner_enabled():
                _chain_entries_dbg = _hub().router._chain_entries()
                _entry_dbg = _chain_entries_dbg[0] if _chain_entries_dbg else {}
                _ti, _to, _cost = _hub()._banner_tokens_from_last_write("render", session_id)
                _dbg_task_id = _hub()._tap_task_identity(session_id, model)[0]
                _banner_text = _db.format_banner(
                    lane="uncensored-render",
                    trigger=",".join(matches)[:60],
                    model=str(_entry_dbg.get("model") or ""),
                    endpoint=str(_entry_dbg.get("url") or "").split("://", 1)[-1].split("/", 1)[0],
                    tokens_in=_ti, tokens_out=_to, est_cost=_cost,
                    latency_s=0.0, retries=0,
                    task_id=_dbg_task_id, session_id=session_id)
                _dbg_task_id = _hub()._tap_task_identity(session_id, model)[0]
                _rendered_dbg = _db.append_banner(rendered, _banner_text, _knob_checked=True)
                if _rendered_dbg != rendered:
                    # R9 hotfix: same TypeError trap as dispatcher_pre —
                    # build_banner_record already carries event_detail.
                    _rec_dbg = _db.build_banner_record("uncensored-render", _dbg_task_id,
                                                       trigger=",".join(matches)[:60],
                                                       model=str(_entry_dbg.get("model") or ""),
                                                       tokens_in=_ti, tokens_out=_to,
                                                       est_cost=_cost, latency_s=0.0,
                                                       retries=0,
                                                       session_id=session_id)
                    _rec_dbg["event_detail"] = "debug_banner_emitted"
                    _rec_dbg["lane"] = "uncensored-render"
                    _hub()._log_route("POST", **_rec_dbg)
                    rendered = _rendered_dbg
        except Exception:  # noqa: BLE001 — banner must never break delivery
            _hub().logger.debug("uncensored-router debug_banner (POST render) error", exc_info=True)
        # §10.4 anchor-banner delivery: consume any parked frontier-anchor
        # banner and append to this turn's DELIVERY representation (one-shot).
        # R9: local import — this block sits OUTSIDE the banner-build try
        # above, so `_db` is unbound whenever the knob was off (NameError ->
        # swallowed by the except -> consumed banner silently dropped).
        try:
            from .. import debug_banner as _dbp2
            from ..core import telemetry as _tlm_r
            from ..features.banners import lifecycle as _bll_r
            _rendered_pre_banner = rendered
            # R33f-A: an uncensored-render turn appends its OWN fresh banner
            # — it claims banner ownership BEFORE any anchor consume, so a
            # stale parked banner (19:35 class) can never double-append onto
            # the render body. The claimed banner is consumed and dropped
            # (banner_ownership_claimed event), never re-delivered.
            try:
                if _dbp2.BANNER_HEAD in str(rendered or ""):
                    _dbp2.claim_banner_ownership(session_id,
                                                 edge="uncensored-render")
            except Exception:  # noqa: BLE001 — claim never breaks delivery
                pass
            if _dbp2.BANNER_HEAD not in str(rendered or ""):
                try:
                    _merged_r = _bll_r.LIFECYCLE.deliver(
                        session_id, rendered, "anchor", "pre_render",
                        log_edge="uncensored-render", log_consume=False)
                except _bll_r.IllegalDeliveryEdge as _ide_r:
                    # chokepoint fail-open: the rendered body is still
                    # delivered (I2 intact); telemetry row instead of a
                    # silent miss.
                    _tlm_r.log_route("POST", event_detail="banner_deliver_fail",
                                     kind_id=_ide_r.kind_id, edge=_ide_r.edge,
                                     session_id=session_id)
                else:
                    if isinstance(_merged_r, str) and _merged_r and \
                            _merged_r != str(rendered or ""):
                        rendered = _merged_r
                        # FIX-FIRST rider 4 (item 1, parked-loss): the render
                        # seam's own rewrite (canonical line above) ran BEFORE
                        # this consume, so the persisted row held the
                        # pre-banner text while the DELIVERED text carries the
                        # banner — the same split-brain the benign edge had.
                        # Round-trip rewrite: persisted == delivered.
                        try:
                            from .. import canonical as _cc2
                            if _cc2.rewrite_persisted_turn(
                                    session_id, _rendered_pre_banner, rendered):
                                _persisted_render_text = rendered
                                _hub()._log_route(
                                    "POST",
                                    event_detail="banner_render_captured",
                                    edge="uncensored-render",
                                    session_id=session_id)
                        except Exception:  # noqa: BLE001 — capture never breaks delivery
                            _hub().logger.debug("render banner capture error",
                                         exc_info=True)
        except Exception:  # noqa: BLE001 — banner must never break delivery
            pass
        # R19.11 FIX 1: final-delivery gate — a decision banner consumed at
        # an EARLIER benign/audit edge must survive this render replacing
        # the turn tail: re-emit the held banner when the delivered text
        # lacks the decision marker.
        try:
            from .. import debug_banner as _dbs2
            rendered = _dbs2.settle_decision_banner(session_id, rendered)
        except Exception:  # noqa: BLE001 — banner must never break delivery
            pass
        # R26-3 final capture: the delivered text must equal the persisted
        # row. Any banner appended after the canonical rewrite (inline POST
        # banner, parked merge missed its rewrite, settle re-emit) is
        # captured here — render inbox + persisted-turn round-trip, the same
        # contract the benign/audit_sync/empty-body edges already honor.
        if rendered != _persisted_render_text:
            try:
                from .. import render_inbox as _rib_final
                from .. import canonical as _cc_final

                _rib_final.record_render("UNCENSORED_RENDER_BANNER",
                                         session_id, len(response_text),
                                         rendered)
                if _cc_final.rewrite_persisted_turn(
                        session_id, _persisted_render_text, rendered):
                    _hub()._log_route(
                        "POST", event_detail="banner_render_captured",
                        edge="uncensored-render-banner",
                        session_id=session_id)
            except Exception:  # noqa: BLE001 — capture never breaks delivery
                _hub().logger.debug("render banner final capture error",
                             exc_info=True)
        # R15 LEG 2: render delivery edge — attach the unrouted direct-call
        # visibility banner when one fired this turn.
        return _attach_unrouted(rendered)
    except Exception as exc:  # noqa: BLE001 — hook must never raise
        _hub().logger.debug("uncensored-router post-router error: %s", exc)
        return None


def on_llm_execution(*, request, next_call, **context) -> Any:
    try:
        from ..core.telemetry import seam_probe_fire

        seam_probe_fire("llm_execution")
    except Exception:  # noqa: BLE001 — probe never breaks the seam
        pass

    """LLM EXECUTION middleware for the anchored call. When a model swap was
    staged for this session (PRE dispatcher pass), run the FULL request
    against the anchor endpoint via a per-call client:

      - cap check: today's spend + estimate vs daily cap. Over cap -> log
        cap_blocked and pass the flash call through (overflow behavior).
      - anchored call succeeds -> return a sentinel Result the caller
        (conversation_loop) receives via next_call contract violation? NO —
        the execution-middleware contract REQUIRES calling next_call exactly
        once. We therefore perform the anchored call IN ADDITION, publish its
        result as a consult envelope (consult_router tool result), and hand
        next_call a payload whose messages carry the frontier answer as a
        provenance-stamped tool envelope so flash evaluates it and writes its
        own turn (integration verdict 2: frontier output enters as TOOL
        RESULTS; never rewrites the user message).
      - anchored call fails -> log route_skipped, pass flash through.

    Never raises; every failure path calls next_call exactly once with the
    unchanged (or H1-sentinel-safe) payload.
    """
    try:
        session_id = str(context.get("session_id") or "")
        # R34-10b: a lease whose session has written nothing for 2x the
        # renewal interval is a dead holder — log turn_lease_stale and
        # preempt it so this turn proceeds (fail-open: never blocks).
        try:
            from .. import session_lease as _sl

            _sl.preempt_if_stale(
                session_id,
                log=lambda _e, **kw: _hub()._log_route(
                    "PRE", event_detail=_e, session_id=session_id, **kw))
        except Exception:  # noqa: BLE001 — lease hygiene never blocks a turn
            pass
        # R19.2 midturn decision hook (ADDENDUM 4, two-seam wiring): SEAM 2
        # turn-start sweep (previous-turn tool results + user ingress,
        # seam=turn_boundary) then SEAM 1's pending-advisory flush rides
        # into this in-flight request. Fail-open: any error -> no
        # advisories, call proceeds unchanged.
        _mt_advisories: list = []
        try:
            from .. import decision_midturn as _dmt
            if not str(context.get("session_id") or ""):
                # FIX 3 observability: raw context keys at debug level when
                # the session_id is missing/stale (never the values).
                _hub().logger.debug(
                    "uncensored-router: llm_execution session_id missing;"
                    " context keys=%s", sorted(context.keys()))
            _mt_advisories = _dmt.flush_and_scan(
                str(context.get("session_id") or context.get("task_id")
                    or "active-session"), request) or []
        except Exception:  # noqa: BLE001 — hook must never break the call
            _mt_advisories = []
        if not isinstance(_mt_advisories, list):
            _mt_advisories = []
        rec = _hub().router_core.peek_pending_swap(session_id)
        if rec is None:
            if _mt_advisories:
                modified = _hub().copy.deepcopy(request)
                msgs = modified.get("messages")
                if isinstance(msgs, list):
                    _n = 0
                    for _adv in _mt_advisories:
                        if _adv:
                            msgs.append({"role": "assistant",
                                         "content": str(_adv)})
                            _n += 1
                    # Delivery was previously invisible: flush() had already
                    # cleared the queue, so an advisory that never got appended
                    # left no trace anywhere and the lane looked like it had
                    # simply chosen silence.
                    try:
                        _hub()._log_route(
                            "PRE", event_detail="midturn_advisory_injected",
                            count=_n,
                            chars=sum(len(str(a)) for a in _mt_advisories if a),
                            has_note=int(any("internal state readout" in str(a)
                                             for a in _mt_advisories if a)),
                            session_id=session_id)
                    except Exception:  # noqa: BLE001 — telemetry only
                        pass
                    return next_call(modified)
            return next_call(request)
        if _mt_advisories:
            # A PRE swap is pending, so this request is about to be replaced by
            # a render. flush() has ALREADY cleared the queue -- dropping them
            # here loses the advisory for good. Put them back so the next turn
            # delivers them instead of the note vanishing into a swap.
            try:
                from .. import decision_midturn as _dmt2

                _dmt2.requeue(session_id, _mt_advisories)
                _hub()._log_route("PRE",
                                  event_detail="midturn_advisory_requeued",
                                  count=len(_mt_advisories),
                                  reason="pending_swap",
                                  session_id=session_id)
            except Exception:  # noqa: BLE001 — never break the call
                pass

        # Consume the staged swap now — exactly-once semantics.
        outcome = _hub().anchor_exec.maybe_execute_anchored(session_id, request)
        if isinstance(outcome, tuple) and outcome and outcome[0] == "cap_blocked":
            info = outcome[1] if len(outcome) > 1 else {}
            _hub()._log_route("PRE", event_detail="cap_blocked",
                       lane=_hub().router_core.LANE_COMPLEXITY, route_id=info.get("route_id"),
                       task_id=info.get("task_id"), spend=round(float(info.get("spend", 0.0)), 4),
                       cap=round(float(info.get("cap", 0.0)), 2),
                       session_id=session_id)
            try:
                _hub().router_tools.count("cap_blocked")
            except Exception:  # noqa: BLE001
                pass
            return next_call(request)
        if not (isinstance(outcome, tuple) and outcome and outcome[0] == "done"):
            # Anchored call failed or no swap: fail-open to flash, log skip.
            # v3.3.1: a REAL anchored failure (not cap_blocked — that's spend
            # policy, not anchor health, and must not count) enters the
            # failure-backoff ledger so cross-turn re-fires are benched for
            # the exponential window instead of hammering the endpoint.
            if rec:
                _hub().router_core.record_anchor_backoff_failure(
                    session_id, str(rec.get("task_id") or ""),
                    reason="anchored_call_failed")
            # v3.6 P0.2: provider-failure tap (anchor provider failed) + P0.5
            # route_skipped enrichment (fail_kind + finish_reason).
            # R16-1 (rider 16, T1r9 R9-1): rec['endpoint'] is an
            # AnchorEndpoint OBJECT, not a dict — the old `.get("model")`
            # here raised AttributeError mid-branch and the outer handler
            # swallowed it: route_skipped NEVER reached the route log and
            # the turn delivered bannerless with zero events (live: analyst
            # 5a/5b/R15_6 turns, agent.log anchor_route_failed 404 only).
            # getattr form + fail-loud parked banner below.
            _fail_model = str(getattr(getattr(rec or {}, "endpoint", None),
                                      "model", "") or "")
            _hub()._tap_provider_failure(session_id, _fail_model,
                                  "anchored_call_failed",
                                  fail_kind="anchor_5xx_or_transport", finish_reason="none")
            _hub()._log_route("PRE", event_detail="route_skipped",
                       lane=_hub().router_core.LANE_COMPLEXITY,
                       reason="anchored_call_failed" if rec else "no_swap",
                       fail_kind="anchored_call_failed" if rec else "no_swap",
                       finish_reason="none",
                       model=_fail_model,
                       route_id=rec.get("route_id") if rec else None,
                       session_id=session_id)
            # R16-1 (rider 16): fail-loud delivery — an anchored consult
            # that billed nothing but failed must never deliver a silently
            # bannerless body. Park a visible failure banner (same park/
            # consume machinery as success banners) so the delivered body
            # carries the failure same-turn. Fail-open, never breaks the
            # flash passthrough.
            try:
                from .. import debug_banner as _fdb
                if _fdb.debug_banner_enabled() and rec:
                    _fail_banner = _fdb.format_banner(
                        lane="anchor", trigger="consult_failed",
                        model=_fail_model or "unknown",
                        endpoint="", tokens_in=0, tokens_out=0,
                        est_cost=0.0, latency_s=0.0, retries=0,
                        task_id=str(rec.get("task_id") or ""),
                        session_id=session_id)
                    _fail_banner = ((_fail_banner or "").rstrip()
                                    .removesuffix("·").rstrip()
                                    + " | anchored_call_failed ·")
                    if _fail_banner:
                        _fdb.park_anchor_banner(session_id, _fail_banner,
                                                task_id=str(rec.get("task_id") or ""))
            except Exception:  # noqa: BLE001 — banner never breaks the lane
                pass
            try:
                _hub().router_tools.count("route_skipped")
                _hub().router_tools.note_skip_reason("anchored_call_failed" if rec else "no_swap")
            except Exception:  # noqa: BLE001
                pass
            return next_call(request)

        envelope = outcome[1]
        # Deliver the frontier answer to flash as a tool-result-style envelope
        # appended to the request payload (advisory data, not a user rewrite).
        # R8h: envelope text (frame + temporal body) built by frames.py —
        # single source of truth for BOTH lanes' frame text.
        modified = _hub().copy.deepcopy(request)
        msgs = modified.get("messages")
        if isinstance(msgs, list):
            from .. import frames as _frames
            # R34-12 item 8: HARD CAP at the ANCHOR INJECTION SEAM.
            # The 8d cap lived only inside anchor_exec.maybe_execute_anchored
            # and only when its execution-directive detector fired — the live
            # dispatch brief did not match that detector, so a 4033-char
            # envelope rode into the turn (anchor_chars=4033 at L1) and the
            # turn answered with it. At L1 the advisory is supporting
            # material: cap the injected answer HERE, regardless of how the
            # turn was classified, so an oversized advisory is impossible.
            try:
                from .. import anchor_exec as _axcap
                from .. import debug_banner as _dbcap

                if _dbcap.debug_banner_level() >= 1:
                    _raw_answer = envelope.get("answer")
                    _raw_n = len(str(_raw_answer or ""))
                    # R34-19N: same source of truth the request was sized
                    # from — what we bought is what we may inject.
                    _cap = int(_axcap.advisory_char_budget())
                    if _raw_n > _cap:
                        envelope["answer"] = str(_raw_answer or "")[:_cap]
                        envelope["advisory_truncated"] = True
                        envelope["advisory_only"] = True
                        _hub()._log_route(
                            "PRE", event_detail="anchor_advisory_capped",
                            reason="l1_bounded_advisory",
                            session_id=session_id,
                            **{"from": _raw_n, "to": _cap})
            except Exception:  # noqa: BLE001 — framing never breaks the consult
                _hub().logger.debug("anchor advisory cap error", exc_info=True)
            _kind = str(envelope.get("kind") or "")
            # R9-9 (rider 9): the envelope's provenance stamps (model + cost,
            # written by anchor_exec) ride INTO the delivered advisory — the
            # tool-stream text is self-contained discrimination.
            _st_model = envelope.get("model")
            _st_cost = envelope.get("cost")
            if _kind == "orientation":
                advisory = _frames.orientation_advisory(
                    envelope.get("producer"), envelope.get("route_id"),
                    envelope.get("answer"), model=_st_model, cost=_st_cost)
            else:
                advisory = _frames.reflection_advisory(
                    _kind, envelope.get("producer"), envelope.get("route_id"),
                    envelope.get("limitations"), envelope.get("answer"),
                    model=_st_model, cost=_st_cost)
            # R34-14 item 1: a consult that resolves AFTER its turn closed is
            # DISCARDED here — never appended to a dead turn's context, never
            # billed-and-ignored silently. `note_advisory_injected` returns
            # False and logs `advisory_after_turn_close delta_s=<n>` +
            # `advisory_orphaned_post_close`. The seam instruction is skipped
            # too: with no advisory in context there is nothing to proceed on.
            _inject_ok = True
            try:
                from .. import advisory_consumption as _ac
                _last_user = ""
                for _m in reversed(msgs):
                    if isinstance(_m, dict) and _m.get("role") == "user":
                        _last_user = str(_m.get("content") or "")
                        break
                _inject_ok = bool(_ac.note_advisory_injected(
                    session_id, _last_user,
                    chars=len(str(envelope.get("answer") or "")),
                    envelope_chars=len(str(advisory or ""))))
            except Exception:  # noqa: BLE001 — ledger never blocks delivery
                _inject_ok = True
            if not _inject_ok:
                # Orphaned post-close: drop the envelope and hand the turn
                # back untouched (the original request, no advisory).
                return next_call(request)
            msgs.append({"role": "assistant", "content": advisory})
            # (2026-09-09) Seam instruction - mirror of the uncensored render seam:
            # after the advisory envelope, explicitly instruct the main model to
            # PROCEED with the task. Higher-self produced the data; the main model
            # must now do the work and answer the user.
            # R41: marked with its own platform rule id and appended at most
            # once per context — replay can attribute this user-role carrier
            # to the platform instead of reading it as a genuine user turn.
            _frames.append_platform_rule(
                msgs, _frames.HS_SEAM_INSTRUCTION_RULE_ID,
                _frames.HS_SEAM_INSTRUCTION, role="user")
        # R19.2: merge any midturn advisory envelopes into the anchored
        # path too (advisory data appended, never a rewrite).
        if _mt_advisories:
            for _adv in _mt_advisories:
                if _adv:
                    msgs.append({"role": "assistant", "content": str(_adv)})
        # v3.3.1: anchored SUCCESS clears the failure-backoff entry for this
        # (session, task) — after envelope delivery, before next_call.
        _hub().router_core.clear_anchor_backoff(session_id, str(rec.get("task_id") or ""))
        # v3.6 §10.2 debug banner — frontier anchor success fire point. Banner
        # rides the ADVISORY ENVELOPE (model context) data only; the user-facing
        # canonical content is untouched here (the anchor result enters as tool
        # data, not the delivered turn). Data = in-process tap values (no
        # ledger re-read); narrow boundary — a banner failure changes nothing.
        try:
            from .. import debug_banner as _db

            if _db.debug_banner_enabled():
                _ep = rec.get("endpoint")

                def _ep_get(obj, key, default=""):
                    """AnchorEndpoint may be a dataclass OR a dict — read either."""
                    try:
                        if isinstance(obj, dict):
                            return obj.get(key, default)
                        v = getattr(obj, key, default)
                        return default if v is None else v
                    except Exception:  # noqa: BLE001
                        return default

                _model = str(_ep_get(_ep, "model") or "")
                _base = str(_ep_get(_ep, "base_url") or "")
                _host = _base.split("://", 1)[-1].split("/", 1)[0] if _base else ""
                # tokens: pulled from the last tokens-ledger record written by
                # this same call (in-process values threaded via record; a
                # bounded tail read of 1 line — no full ledger re-read).
                _ti, _to, _cost = _hub()._banner_tokens_from_last_write("anchor", session_id)
                _banner = _db.format_banner(
                    lane="frontier-anchor", trigger=str(rec.get("mode") or "anchored"),
                    model=_model, endpoint=_host, tokens_in=_ti, tokens_out=_to,
                    est_cost=_cost, latency_s=0.0, retries=0,
                    task_id=str(rec.get("task_id") or ""), session_id=session_id,
                    route_id=str(rec.get("route_id") or ""))
                # R9-7 (rider 9): reconcilable row ref on the consult banner —
                # the billed frontier consult's decision-ledger row id (stashed
                # by anchor_exec; 'ledger-row MISSING' when the write failed)
                # — same fail-loud contract as the decision-lane banner.
                _frow = rec.get("frontier_ledger_row")
                if not _frow:
                    try:
                        # R10-2 (rider 10): rec is a PRE-EXECUTION peek copy
                        # (peek_pending_swap returns dict(rec)); the row id
                        # is written on the LIVE record inside
                        # maybe_execute_anchored — reconcile from the
                        # session-scoped last-write accessor.
                        from .. import anchor_exec as _ax

                        _frow = _ax.last_frontier_row(session_id)
                    except Exception:  # noqa: BLE001 — fail-open MISSING
                        _frow = 0
                _row_marker = ("row=%s" % int(_frow) if _frow
                               else "ledger-row MISSING")
                if _banner:
                    _banner = str(_banner).rstrip()
                    if _banner.endswith("·"):
                        _banner = _banner[:-1].rstrip()
                    _banner = "%s | %s ·" % (_banner, _row_marker)
                if _banner:
                    # §10.4 delivery: the envelope is model-context only —
                    # park the banner for the POST transform to append to the
                    # DELIVERED turn (one-shot, this session's next delivery).
                    try:
                        _db.park_anchor_banner(
                            session_id, _banner,
                            task_id=str(rec.get("task_id") or ""))
                        _hub()._log_route("PRE", event_detail="anchor_banner_parked",
                                   session_id=session_id)
                    except Exception:
                        pass
                if _banner:
                    # R9 hotfix: build_banner_record carries event_detail
                    # ("debug_banner") + lane ("frontier-anchor"); override
                    # event_detail rather than passing it twice (TypeError
                    # trap — swallowed except discarded the parked banner).
                    _rec_an = _db.build_banner_record("frontier-anchor", str(rec.get("task_id") or ""),
                                                      trigger=str(rec.get("mode") or "anchored"),
                                                      model=_model, tokens_in=_ti, tokens_out=_to,
                                                      est_cost=_cost, latency_s=0.0, retries=0,
                                                      route_id=rec.get("route_id"), gate="",
                                                      session_id=session_id)
                    _rec_an["event_detail"] = "debug_banner_emitted"
                    _rec_an["lane"] = "anchor"
                    _hub()._log_route("PRE", **_rec_an)
        except Exception:  # noqa: BLE001 — §10.4-H failure isolation
            _hub().logger.debug("uncensored-router debug_banner (anchor) error", exc_info=True)
        _hub()._log_route("PRE", event_detail="anchor_route_fired",
                   lane=_hub().router_core.LANE_COMPLEXITY, mode=rec.get("mode"),
                   route_id=rec.get("route_id"), task_id=rec.get("task_id"),
                   anchor_chars=len(str(envelope.get("answer") or "")),
                   session_id=session_id)
        return next_call(modified)
    except Exception:  # noqa: BLE001 — must never break the provider call
        try:
            return next_call(request)
        except Exception:  # noqa: BLE001
            return None


def on_transform_terminal_output(*, command: str = "", output: Any = None,
                                 returncode: int = 0, task_id: str = "",
                                 env_type: str = "",
                                 **context) -> None:
    try:
        from ..core.telemetry import seam_probe_fire

        seam_probe_fire("transform_terminal_output")
    except Exception:  # noqa: BLE001 — probe never breaks the seam
        pass
    # R34-12 item 9: a tool actually ran in this turn — the work invariant's
    # counter (advisory_consumption.close_turn reads it at turn end).
    try:
        from .. import advisory_consumption as _actool

        _actool.note_tool_activity(
            str(context.get("session_id") or task_id or ""))
    except Exception:  # noqa: BLE001 — observability never breaks the seam
        pass
    # R34-17: a terminal tool result means a terminal RAN in this turn — the
    # work predicate's authoritative signal (the echo guard must never call a
    # turn with a terminal run "no work").
    try:
        from .. import work_signal as _wst

        _wst.note_terminal_activity(
            str(context.get("session_id") or task_id or ""), str(command or ""))
    except Exception:  # noqa: BLE001 — predicate never breaks the seam
        pass
    """R19.2 ADDENDUM 4 — SEAM 1: transform_terminal_output platform hook.
    Core fires this after EVERY terminal tool result, mid-run (live-verified
    seam; transform_tool_result is dead-from-birth on 0.21.4). The single
    tool output IS the delta. Detection + ledger (seam=terminal) + pending-
    advisory staging only: NEVER returns a string (the platform replaces
    the result with the first string return), never blocks. Fail-open on
    any error. Session key: task_id when core provides one, else a shared
    active-session bucket."""
    try:
        from .. import decision_midturn as _dmt
        _key = _dmt._sanitize_session_key(
            context.get("session_id") or task_id or "")
        if not _key:
            # FIX 3 observability: log the RAW context keys (debug) so a
            # stale/foreign key source is diagnosable — never log values.
            _hub().logger.debug(
                "uncensored-router: terminal seam session-key fallback;"
                " context keys=%s", sorted(context.keys()))
            _key = "active-session"
        _dmt.on_terminal_output(_key, "terminal", output, _dmt.SEAM_TERMINAL)
    except Exception:  # noqa: BLE001 — never break the tool result
        _hub().logger.debug("uncensored-router transform_terminal_output hook error",
                     exc_info=True)
        return None
    return None
