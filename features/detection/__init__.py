"""R22 two-tier denial detection (spec /opt/data/tmp/r22_two_tier_detection_spec.md).

Two tiers, POST pipeline, all lanes:
  Tier 1 — structural regex gate (features.detection.structural): FREE,
           high-recall, vocabulary-light. Candidates only; never decides
           alone (except fail-open).
  Tier 2 — semantic judge (features.detection.semantic_judge): typed CLOSED
           JSON verdict via the profile's aux model ('auto' resolution),
           8s timeout, fail-open to the Tier-1 decision.

POST-only. PRE untouched. Routing reuses the EXISTING model_flinch render
path (no new lane). Ledger events detection_t1/detection_t2 in the existing
router state db (hermes_router_state.db, detection_ledger table).
"""
