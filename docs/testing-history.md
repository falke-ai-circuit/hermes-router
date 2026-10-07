# Testing history — era-to-functional rename map (P8a)

Tests were renamed from their historical rider/fix-era filenames into
functional groups under tests/. Content untouched; git mv only (the
green suite after the rename is the behavioral proof). Group rules
were applied mechanically by filename keyword, catch-all routing/.
The one-liners below are the first header line of each file at rename
time (mechanically extracted, commit as docs).

## caps_ledgers/ (5 files)
- `tests/test_agent_cap_scope.py` → `tests/caps_ledgers/test_agent_cap_scope.py` — Leg 9 (blueprint H1): per-agent cap spend keys by AGENT IDENTITY
## banners/ (16 files)
- `tests/test_anchor_chain_and_config.py` → `tests/banners/test_anchor_chain_and_config.py` — v3.0.0 anchor chain + daily cap guard + config-writer atomicity tests.
## decision_lane/ (12 files)
- `tests/test_aux_fallback_verdict.py` → `tests/decision_lane/test_aux_fallback_verdict.py` — v4.2.1 — aux-error mechanical fallback + timeout alignment.
## classification/ (15 files)
- `tests/test_aux_intent_classifier.py` → `tests/classification/test_aux_intent_classifier.py` — LEG 13 — aux intent classifier for on-demand routing
## routing/ (69 files)
- `tests/test_cascade_battery_integration.py` → `tests/routing/test_cascade_battery_integration.py` — Leg 4 — integration + edge-case battery for the unified cascade route gate
## classification/ (15 files)
- `tests/test_classifier_post_match.py` → `tests/classification/test_classifier_post_match.py` — Post-router classifier match tests (spec §10 row 3).
- `tests/test_classifier_pre_match.py` → `tests/classification/test_classifier_pre_match.py` — Pre-router classifier match tests (spec §10 row 2).
- `tests/test_closed_line_guard.py` → `tests/classification/test_closed_line_guard.py` — Update closed-line guard tests: guards REMOVED per Goran-direct (nothing off).
## routing/ (69 files)
- `tests/test_closure_prose_hardening.py` → `tests/routing/test_closure_prose_hardening.py` — F2 closure-prose-collision regression suite (v3.9.2).
## classification/ (15 files)
- `tests/test_code_context_guard.py` → `tests/classification/test_code_context_guard.py` — R12 censorship-flinch-only gate: code-context FP guard (v4.4.0).
- `tests/test_csam_young_fp_fix.py` → `tests/classification/test_csam_young_fp_fix.py` — Regression: csam_underage young-girl/boy FP on IED-fiction (Goran 2026-09-07).
## decision_lane/ (12 files)
- `tests/test_d1_impulse_lane.py` → `tests/decision_lane/test_d1_impulse_lane.py` — D1 impulse lane v1.1 (SPEC-impulse-lane-v1.md) — pin battery.
## banners/ (16 files)
- `tests/test_d3_delivery_banner_body.py` → `tests/banners/test_d3_delivery_banner_body.py` — D3-DELIVERY rider 2 (v4.13.4) — api_server banner body-delivery battery.
## decision_lane/ (12 files)
- `tests/test_d3_delivery_midturn_key.py` → `tests/decision_lane/test_d3_delivery_midturn_key.py` — D3-DELIVERY FIX — midturn fork-consult park->deliver round-trip.
## banners/ (16 files)
- `tests/test_d3_jev_native.py` → `tests/banners/test_d3_jev_native.py` — D3 (v4.13.2) — systemone-native jev backend (typesafe direct) battery.
## routing/ (69 files)
- `tests/test_d3_residuals_fixfirst.py` → `tests/routing/test_d3_residuals_fixfirst.py` — D3 residuals fix-first (v4.13.5) — evol park-to-deliver + POST gate.
- `tests/test_d3_residuals_round2.py` → `tests/routing/test_d3_residuals_round2.py` — D3 residuals round 2 — pins for the midturn-mode boolean coercion fix.
## shadow/ (5 files)
- `tests/test_declared_shadow_render_lane.py` → `tests/shadow/test_declared_shadow_render_lane.py` — Leg 8 (blueprint §2): declared SHADOW executes on the UNCENSORED RENDER
## routing/ (69 files)
- `tests/test_declared_variants.py` → `tests/routing/test_declared_variants.py` — Leg 10 (live researcher-canary regression, conductor-verified):
- `tests/test_directive_payload_form.py` → `tests/routing/test_directive_payload_form.py` — Leg 5 — directive-line payload-form regression tests (canary live-probe).
## classification/ (15 files)
- `tests/test_doctrine_verdict.py` → `tests/classification/test_doctrine_verdict.py` — Option 3 verdict wiring tests: flinch routes, agent_line honored."""
## routing/ (69 files)
- `tests/test_fixset_2026_09_02.py` → `tests/routing/test_fixset_2026_09_02.py` — Fixset 2026-09-02 regression tests (Goran-approved 1+2+3+4).
## classification/ (15 files)
- `tests/test_flinch_reason_gate.py` → `tests/classification/test_flinch_reason_gate.py` — Flinch-reason gate (Goran ruling 2026-09-08): only CONTENT flinches route
## routing/ (69 files)
- `tests/test_frames.py` → `tests/routing/test_frames.py` — Tests: R8h frames.py — higher-self frame unification + measurement knob
- `tests/test_hermes_aux_source.py` → `tests/routing/test_hermes_aux_source.py` — Hermes-only aux lane (2026-09-08, Goran-direct: "we dont need legacy we
## higher_self/ (6 files)
- `tests/test_higher_self_identity.py` → `tests/higher_self/test_higher_self_identity.py` — Higher-self identity seam (Goran 2026-09-08 parity doctrine).
- `tests/test_initiator_provenance.py` → `tests/higher_self/test_initiator_provenance.py` — Leg 6 — initiator provenance through the billing sites.
## classification/ (15 files)
- `tests/test_injected_context_strip.py` → `tests/classification/test_injected_context_strip.py` — Injected-context strip + audit-trigger regression (2026-09-08).
## routing/ (69 files)
- `tests/test_live_smoke.py` → `tests/routing/test_live_smoke.py` — Live smoke test (v3.0.0, phase 4) — EXACTLY ONE network call, token-cheap.
## classification/ (15 files)
- `tests/test_loop_guard.py` → `tests/classification/test_loop_guard.py` — Loop guard tests (spec §10 row 7).
## config/ (3 files)
- `tests/test_manifest_loads.py` → `tests/config/test_manifest_loads.py` — Manifest + registration tests (spec §10 row 1).
## classification/ (15 files)
- `tests/test_p05_legacy_purge_guard.py` → `tests/classification/test_p05_legacy_purge_guard.py` — P0.5 guard — legacy-section purge is complete and stays dead.
## routing/ (69 files)
- `tests/test_p2_lane_registry_parity.py` → `tests/routing/test_p2_lane_registry_parity.py` — P2 parity — lane registry data == the original literals (proposal §2.1).
## config/ (3 files)
- `tests/test_p4_schema_budgets.py` → `tests/config/test_p4_schema_budgets.py` — tests/test_p4_schema_budgets.py — P4 guard tests (proposal §2.3/§2.4).
## banners/ (16 files)
- `tests/test_p5_banner_lifecycle.py` → `tests/banners/test_p5_banner_lifecycle.py` — P5 — BannerKind schema + BannerLifecycle + deliver() chokepoint.
## routing/ (69 files)
- `tests/test_p6_isolate_seam_probe.py` → `tests/routing/test_p6_isolate_seam_probe.py` — P6 — seam liveness probe + isolate()/record_swallow telemetry.
- `tests/test_p7_pattern_packs_parity.py` → `tests/routing/test_p7_pattern_packs_parity.py` — P7 — pattern packs + corpus + parity harness (proposal §2.6).
- `tests/test_pending_routes.py` → `tests/routing/test_pending_routes.py` — Pending-routes tests (spec §10 row 8 + §6.1).
## higher_self/ (6 files)
- `tests/test_persona_card.py` → `tests/higher_self/test_persona_card.py` — Persona-card regression tests (2026-09-02, Goran directive: dynamic
## decision_lane/ (12 files)
- `tests/test_post_fallback.py` → `tests/decision_lane/test_post_fallback.py` — POST-router fallback tests (Goran-direct 2026-09-01, narrowed dispatch).
## routing/ (69 files)
- `tests/test_post_router_hook.py` → `tests/routing/test_post_router_hook.py` — Post-router hook tests (spec §10 row 6).
- `tests/test_pre_router_middleware.py` → `tests/routing/test_pre_router_middleware.py` — Pre-router middleware tests (spec §10 row 5).
## higher_self/ (6 files)
- `tests/test_provenance_footer.py` → `tests/higher_self/test_provenance_footer.py` — Tests: provenance footer (2026-09-07 Goran-direct). Opt-in knob: default OFF
## routing/ (69 files)
- `tests/test_r10_catalog_resolution.py` → `tests/routing/test_r10_catalog_resolution.py` — R10 — provider-catalog model resolution (final fallback in
- `tests/test_r11_bypass_watch.py` → `tests/routing/test_r11_bypass_watch.py` — R11 Leg 2 — bypass_watch: provider_direct_call_unrouted observability.
- `tests/test_r11_frontier_family.py` → `tests/routing/test_r11_frontier_family.py` — R11 — frontier imperative-consult family (Goran 09-17 ruling).
- `tests/test_r13_4_delivered_body_legs.py` → `tests/routing/test_r13_4_delivered_body_legs.py` — R13-4 (rider 13 addendum) — observability recurrence + delivered-body
## decision_lane/ (12 files)
- `tests/test_r15_risk_consults.py` → `tests/decision_lane/test_r15_risk_consults.py` — R15 — risk-triggered consults (LEG 1) + on-demand consult fixes (LEG 2).
- `tests/test_r16_consult_cooldown.py` → `tests/decision_lane/test_r16_consult_cooldown.py` — R16 — N-turn consult cooldown keyed by normalized content hash.
## routing/ (69 files)
- `tests/test_r18_aux_pacing.py` → `tests/routing/test_r18_aux_pacing.py` — R18 — double-banner fix + aux consult burst pacing (Goran 09-24).
- `tests/test_r191_fix_bundle.py` → `tests/routing/test_r191_fix_bundle.py` — R19.1 — Decision Lane fix bundle test battery.
## banners/ (16 files)
- `tests/test_r19_11_banner_and_jev.py` → `tests/banners/test_r19_11_banner_and_jev.py` — R19.11 — decision-banner loss on two-lane turns + Jev parse hardening.
## routing/ (69 files)
- `tests/test_r19_12_gate_and_verbatim.py` → `tests/routing/test_r19_12_gate_and_verbatim.py` — R19.12 — two surgical decision-lane fixes (reviewer battery evidence).
- `tests/test_r19_13_adversarial_fold.py` → `tests/routing/test_r19_13_adversarial_fold.py` — R19.13 B+ (Goran addendum): adversarial fold-in — frontier gains the
## higher_self/ (6 files)
- `tests/test_r19_13_forged_persona.py` → `tests/higher_self/test_r19_13_forged_persona.py` — R19.13 FIX 3 (reviewer audit specimen log): forged banner-persona blocks.
## routing/ (69 files)
- `tests/test_r19_13_frontier_sense.py` → `tests/routing/test_r19_13_frontier_sense.py` — R19.13 Part B — Frontier adjustment (SPEC-v1.md Part 2, Goran-approved).
## caps_ledgers/ (5 files)
- `tests/test_r19_13_ledger_warn.py` → `tests/caps_ledgers/test_r19_13_ledger_warn.py` — R19.13 FIX 4 (reviewer audit fix-first 1): decision_ledger write failures
## routing/ (69 files)
- `tests/test_r19_13_manual_precedence.py` → `tests/routing/test_r19_13_manual_precedence.py` — R19.13 FIX 1 (reviewer audit fix-first 2): analyst manual-trigger asymmetry.
- `tests/test_r19_13_parse_retry.py` → `tests/routing/test_r19_13_parse_retry.py` — R19.13 FIX 2 (reviewer audit fix-first 4): residual parse_fail on manual
- `tests/test_r19_13_reflex_modular.py` → `tests/routing/test_r19_13_reflex_modular.py` — R19.13 second addition (Goran directive): reflex lane modularization.
- `tests/test_r19_14_hotfix.py` → `tests/routing/test_r19_14_hotfix.py` — R19.14 HOTFIX (Goran-directed, live-testing findings; user mid-testing
## banners/ (16 files)
- `tests/test_r19_15_banner_label.py` → `tests/banners/test_r19_15_banner_label.py` — R19.15 MICRO-FIX (Goran-directed, live review testing): reflex decision
- `tests/test_r19_16_banner_stack.py` → `tests/banners/test_r19_16_banner_stack.py` — R19.16 FIX 4 (Goran addendum): ALL fired banners must be shown, stacked,
## routing/ (69 files)
- `tests/test_r19_16_execute_once.py` → `tests/routing/test_r19_16_execute_once.py` — R19.16 (Goran: 'fire') — declared-frontier still dying: recall +
- `tests/test_r19_17_addendum2_outcomes.py` → `tests/routing/test_r19_17_addendum2_outcomes.py` — R19.17 ADDENDUM 2 (Goran-approved; outcome-labeling defects from the
- `tests/test_r19_17_aux_model_auto.py` → `tests/routing/test_r19_17_aux_model_auto.py` — R19.17 ADDENDUM: the aux model must NEVER be a hardcoded literal again.
- `tests/test_r19_17_family_failopen.py` → `tests/routing/test_r19_17_family_failopen.py` — R19.17 (Goran root-cause directive): the R19.16 fail-open now covers ALL
- `tests/test_r19_18_frame_context.py` → `tests/routing/test_r19_18_frame_context.py` — R19.18 (Goran approved): midturn envelope frame starvation — root cause
- `tests/test_r19_19_dual_audit.py` → `tests/routing/test_r19_19_dual_audit.py` — R19.19 (v4.12.6) — combined fix round from the reviewer's dual audit.
## banners/ (16 files)
- `tests/test_r19_20_banner_clamp.py` → `tests/banners/test_r19_20_banner_clamp.py` — R19.20 (v4.12.7) — closes the reviewer's two remaining F-batch-1 remarks.
## routing/ (69 files)
- `tests/test_r19_21_parity.py` → `tests/routing/test_r19_21_parity.py` — R19.21 (v4.12.8): closes the reviewer's final verification remarks.
- `tests/test_r19_22_turn_close_rollup.py` → `tests/routing/test_r19_22_turn_close_rollup.py` — R19.22 (v4.12.9, Goran — from operative's Kindle run): turn-close
## banners/ (16 files)
- `tests/test_r19_banner_cap.py` → `tests/banners/test_r19_banner_cap.py` — R19.8: POST advisory banner cap per session (reviewer rollout condition)."""
- `tests/test_r19_banner_standard.py` → `tests/banners/test_r19_banner_standard.py` — R19.9: decision banner standardization — provider name + persona label."""
## routing/ (69 files)
- `tests/test_r19_battery_fixes.py` → `tests/routing/test_r19_battery_fixes.py` — v4.11.4 battery-findings fixes (conductor live-verified):
## decision_lane/ (12 files)
- `tests/test_r19_decision_lane.py` → `tests/decision_lane/test_r19_decision_lane.py` — R19 — Lane 3 `decision` (v0 DARK) test battery.
- `tests/test_r19_decision_lane_v3.py` → `tests/decision_lane/test_r19_decision_lane_v3.py` — R19 — Decision Lane v3 (frozen spec 2026-09-27) test battery.
- `tests/test_r19_decision_miner.py` → `tests/decision_lane/test_r19_decision_miner.py` — R19 step 2 — decision_miner + POST leg test battery (spec §10).
- `tests/test_r19_midturn_hook.py` → `tests/decision_lane/test_r19_midturn_hook.py` — R19.2 ADDENDUM 3 — MIDTURN DECISION HOOK (transform_tool_result seam)
## banners/ (16 files)
- `tests/test_r19_parked_loss_stacking.py` → `tests/banners/test_r19_parked_loss_stacking.py` — FIX-FIRST rider 4 (v4.13.7) — parked-loss round-trip + D1 stacking + midturn pseudo-fire pins.
## routing/ (69 files)
- `tests/test_r6_aux_override.py` → `tests/routing/test_r6_aux_override.py` — R6 leg 2 — aux classifier seam for the named-model override.
- `tests/test_r6_model_override.py` → `tests/routing/test_r6_model_override.py` — R6 leg 1 — on-demand frontier consult with named model override.
- `tests/test_r7_user_only_override.py` → `tests/routing/test_r7_user_only_override.py` — R7: model_override restricted to user-initiated on-demand only.
## classification/ (15 files)
- `tests/test_r8_orientation_leak_guard.py` → `tests/classification/test_r8_orientation_leak_guard.py` — Tests: R8b orientation-leak guard (2026-09-13). Resume-turn detection
## shadow/ (5 files)
- `tests/test_r8_shadow_integration_frame.py` → `tests/shadow/test_r8_shadow_integration_frame.py` — Tests: R8a shadow_integration_frame knob (2026-09-13 Goran-direct).
## banners/ (16 files)
- `tests/test_r9_banner_delivery_seam.py` → `tests/banners/test_r9_banner_delivery_seam.py` — R9 (2026-09-14) — banner delivery-seam regression battery.
## classification/ (15 files)
- `tests/test_refusal_doctrine.py` → `tests/classification/test_refusal_doctrine.py` — Tests for refusal_doctrine (Option 3 hybrid) — flinch vs agent-line."""
## commands/ (5 files)
- `tests/test_request_routing_action.py` → `tests/commands/test_request_routing_action.py` — Leg 3 — request_routing action + detection tests
## routing/ (69 files)
- `tests/test_resilience.py` → `tests/routing/test_resilience.py` — Resilience / fault-injection suite (v3.9.0 polish release).
## banners/ (16 files)
- `tests/test_resilience_anchored_call.py` → `tests/banners/test_resilience_anchored_call.py` — F6 resilience pin: unresolvable provider key -> anchored_call returns
## routing/ (69 files)
- `tests/test_rev_audit_regressions.py` → `tests/routing/test_rev_audit_regressions.py` — Regression tests for Rev audit 2026-09-01 blockers B2 + B3.
- `tests/test_reviewer_regressions.py` → `tests/routing/test_reviewer_regressions.py` — Reviewer-audit regressions (2026-09-02): H1 PRE double-route sentinel,
- `tests/test_rider10_fixes.py` → `tests/routing/test_rider10_fixes.py` — FIX-FIRST rider 10 (v4.16.0) — T1R3 battery FAIL pins + conductor observability.
- `tests/test_rider13_fixes.py` → `tests/routing/test_rider13_fixes.py` — FIX-FIRST rider 13 (v4.16.3) — misfire/gate battery fixes.
- `tests/test_rider14_fixes.py` → `tests/routing/test_rider14_fixes.py` — Rider 14 pins (v4.16.5): R14-1 injection eventing on the consult entry,
- `tests/test_rider15_fixes.py` → `tests/routing/test_rider15_fixes.py` — Rider 15 — T1#8 verdict fixes (v4.16.5 -> 4.16.6 scope).
- `tests/test_rider16_fixes.py` → `tests/routing/test_rider16_fixes.py` — Rider 16 — T1r9 verdict fixes (v4.16.6 -> 4.17.0 scope).
- `tests/test_rider17_fixes.py` → `tests/routing/test_rider17_fixes.py` — Rider 17 — fix-first pins (T1 re-run #10, /opt/data/tmp/t1r10-results-2026-10-05.md).
- `tests/test_rider18_fixes.py` → `tests/routing/test_rider18_fixes.py` — Rider 18 — finish-stage pins (v4.18.0 -> 4.19.0 scope).
- `tests/test_rider19_fixes.py` → `tests/routing/test_rider19_fixes.py` — Rider 19 — apply-and-pin tests (v4.19.0 -> 4.20.0 scope).
- `tests/test_rider20_fixes.py` → `tests/routing/test_rider20_fixes.py` — Rider 20 — batch-fix pins (v4.20.0 -> 4.21.0 scope).
- `tests/test_rider21_fixes.py` → `tests/routing/test_rider21_fixes.py` — tests/test_rider21_fixes.py — RIDER 21 FABLE-PIN pins (v4.22.0).
- `tests/test_rider22_fixes.py` → `tests/routing/test_rider22_fixes.py` — RIDER 22 (Goran-direct): higher-self integration-rule delivery-boundary seam.
- `tests/test_rider7_ca_analysis_class.py` → `tests/routing/test_rider7_ca_analysis_class.py` — FIX-FIRST rider 7 — C-A regression tests: analysis-class frontier detection.
- `tests/test_rider8_fixes.py` → `tests/routing/test_rider8_fixes.py` — FIX-FIRST rider 8 (v4.14.0 c3dc0a3 -> rider 8): pin tests.
- `tests/test_rider9_fixes.py` → `tests/routing/test_rider9_fixes.py` — FIX-FIRST rider 9 (v4.15.0) — pin tests for the ten rider-9 items.
- `tests/test_route_gate.py` → `tests/routing/test_route_gate.py` — Leg 1 — unified route gate tests (BLUEPRINT-request-routing-2026-09-12).
- `tests/test_router_core_v3.py` → `tests/routing/test_router_core_v3.py` — v3.0.0 two-lane dispatcher tests: routing table, intensity matrix,
- `tests/test_router_live.py` → `tests/routing/test_router_live.py` — Live Venice router test (spec §10 row 4).
- `tests/test_router_tuning.py` → `tests/routing/test_router_tuning.py` — Router-tuning dispatch tests (2026-09-09, Goran-approved).
## caps_ledgers/ (5 files)
- `tests/test_routing_caps.py` → `tests/caps_ledgers/test_routing_caps.py` — Leg 2 — per-agent caps + ledger initiator tag tests
## higher_self/ (6 files)
- `tests/test_self_audit_trigger.py` → `tests/higher_self/test_self_audit_trigger.py` — Regression: self-audit/design-validation asks must hit planning_arch (2026-09-08).
## classification/ (15 files)
- `tests/test_semantic_stage.py` → `tests/classification/test_semantic_stage.py` — Semantic stage-2 tests — blueprint v2 §6 matrix (#1-11) + module units.
## caps_ledgers/ (5 files)
- `tests/test_session_store.py` → `tests/caps_ledgers/test_session_store.py` — Session-store recovery unit tests (POST fallback seam).
## shadow/ (5 files)
- `tests/test_shadow_banner.py` → `tests/shadow/test_shadow_banner.py` — Leg 11 (Goran-direct): the declared SHADOW lane emits its own §10.4
## routing/ (69 files)
- `tests/test_single_claim_point.py` → `tests/routing/test_single_claim_point.py` — Leg 7 — single claim point: one routing outcome per turn.
## classification/ (15 files)
- `tests/test_system_injected_skip.py` → `tests/classification/test_system_injected_skip.py` — Regression tests (2026-09-09): system-injected-only turns must NOT fire
## decision_lane/ (12 files)
- `tests/test_two_vote_confirm_gate.py` → `tests/decision_lane/test_two_vote_confirm_gate.py` — v4.2.0 — two-vote confirm gate for settled-line-adjacent PRE groups.
## shadow/ (5 files)
- `tests/test_uncensored_take_family.py` → `tests/shadow/test_uncensored_take_family.py` — LEG 12 — FP doctrine (Goran, binding) + bare-model-call guard.
- `tests/test_unconditional_post.py` → `tests/shadow/test_unconditional_post.py` — Tests for the 2026-09-01 escalation ruling: unconditional POST.
## routing/ (69 files)
- `tests/test_v310_canonical.py` → `tests/routing/test_v310_canonical.py` — v3.1.0 build-brief regression tests (2026-09-05, conductor-validated spec).
- `tests/test_v311_grounding.py` → `tests/routing/test_v311_grounding.py` — v3.1.1 build-brief regression tests (2026-09-05, conductor-verified defect).
## decision_lane/ (12 files)
- `tests/test_v320_one_consult_per_turn.py` → `tests/decision_lane/test_v320_one_consult_per_turn.py` — v3.2.0 close-out fix tests — one consult per turn on the complexity lane.
## banners/ (16 files)
- `tests/test_v321_anchor_strip.py` → `tests/banners/test_v321_anchor_strip.py` — v3.2.1 build-brief regression tests — strip memory-context from anchor payload.
## routing/ (69 files)
- `tests/test_v322_render_cap.py` → `tests/routing/test_v322_render_cap.py` — v3.2.2 build-brief tests — render delivery cap (render_max_chars).
- `tests/test_v323_reconcile.py` → `tests/routing/test_v323_reconcile.py` — v3.2.3 build-brief regression tests (2026-09-05, conductor-validated spec).
## banners/ (16 files)
- `tests/test_v331_anchor_backoff.py` → `tests/banners/test_v331_anchor_backoff.py` — v3.3.1 close-out fix tests — anchor failure backoff (re-fire suppression).
## routing/ (69 files)
- `tests/test_v336_method_card.py` → `tests/routing/test_v336_method_card.py` — v3.3.6 method-card tests: config override + skill lift + fail-open."""
## commands/ (5 files)
- `tests/test_v350_commands.py` → `tests/commands/test_v350_commands.py` — v3.5.0 Phase 2/3 tests — /router chat command surface (commands.py).
## caps_ledgers/ (5 files)
- `tests/test_v350_usage_ledger.py` → `tests/caps_ledgers/test_v350_usage_ledger.py` — v3.5.0 Phase 1 tests — tokens ledger + taps (blueprint 5.2).
## config/ (3 files)
- `tests/test_v360_budget_ledger.py` → `tests/config/test_v360_budget_ledger.py` — v3.6.0 Phase-0 budget-ledger tests (suggestions.py, §2.5 + P0.4).
## banners/ (16 files)
- `tests/test_v360_debug_banner.py` → `tests/banners/test_v360_debug_banner.py` — v3.6.0 Phase-0 debug banner tests — §10.4-I ten-case matrix + §10.1 invariants.
## routing/ (69 files)
- `tests/test_v360_stress.py` → `tests/routing/test_v360_stress.py` — v3.6.0 Phase-0 stress battery (Goran-direct addendum) + remaining P0.7 pins.
## commands/ (5 files)
- `tests/test_v360_tap_wiring.py` → `tests/commands/test_v360_tap_wiring.py` — v3.6.0 Phase-0 tap-wiring tests (P0.1/P0.2/P0.5 + acceptance asserts).
- `tests/test_v360_trigger_cascade.py` → `tests/commands/test_v360_trigger_cascade.py` — v3.6.0 Phase-0 cascade instrumentation tests (P0.7): three-state contract,
- `tests/test_v361_completion_audit.py` → `tests/commands/test_v361_completion_audit.py` — v3.6.1 completion-audit ruling tests (Goran 2026-09-08):
