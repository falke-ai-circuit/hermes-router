# SPEC-impulse-lane — v1.1 ADDENDUM (display label only)

**Status:** frozen addendum to SPEC-impulse-lane-v1.md (D1 build brief, R-EVOL dispatch)
**Scope:** banner display label ONLY. Lane key, module name (`decision.py`),
PROVENANCE_TAG (`[decision-lane advisory]`) all UNCHANGED.

## 1. Display label swap

The banner display label for the decision lane changes from `reflex (decision)`
to `impulse (decision)`:

    · router · impulse (decision) | N verdicts | tok ti/to | $total | initiator=agent

- The lane KEY stays `decision` (routing, ledger, config sub-block, tests).
- `debug_banner.format_banner` maps lane `decision`/`reflex` to the new label;
  nothing else in the label pipeline moves.
- The forged-banner battery is untouched: `reflex (decision)` REMAINS in
  frames.py `_FORGED_PERSONA_SIGNALS` as a spoof signal (a forged banner using
  the retired label is still flagged), and PROVENANCE_TAG stays byte-exact so
  the forged-banner regression path still matches.

## 2. E3 rider (same commit, observability)

R16 gap: the 600s `pre_cooldown` consult suppression was silent at the ledger
layer. `router_core.emit_pre_cooldown_suppressed_consult()` now emits a counted
suppressed-consult line on every pre_cooldown skip:

- counter: `pre_cooldown_suppressed_consult` (decision_counters)
- ledger row: trigger=`pre_cooldown`, outcome=`suppressed_consult`,
  fail_open_reason=`pre_cooldown_skip_consult`, verdict_json carries
  `{kind: suppressed_consult, since_s: N}`
- ledger-visible, NOT banner-displayed. Never raises, never breaks dispatch.

## 3. Build surface (unchanged from v1)

decision.py (weighting block + frame swap), debug_banner.py (label),
router_core.py (E3 emission + dispatch wiring). Pin tests in
`tests/test_d1_impulse_lane.py`: band derivation, evidence-only regex,
provenance retention, single-message shape, weighting normalized sum=1.0,
banner label, E3 ledger visibility, forged-banner regression path.
