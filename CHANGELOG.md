# Changelog

## 2.1.0 — 2026-09-02
- Ordered model chain (`chain:`) with per-entry url/model/key_file/key_env/extra_body/timeout —
  first-success-wins, any failure class falls through (route_fallback audit log)
- Per-provider `extra_body` (abliteration.ai `thinking` flag)
- Hybrid key resolution: key_file → key_env → VENICE_API_KEY env → fail-open
- Profile-neutral defaults (no hardcoded profile literals)
- Dynamic persona card from loading profile's DNA; thread digest (escalation arc)
- Manifest v2 (config_schema, requires_env rich format, tags)
- FIX1 history-reconcile sync seam; FIX3 not-user's-voice markers; FIX4 doctrine-quote exclusion

## 2.0.0 — 2026-09-01
- PRE/POST dual-stage routing, render inbox, loop guard, semantic stage-2 gate
