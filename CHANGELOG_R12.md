## 4.4.0 — 2026-09-17 (R12: abliteration chain restore + code-context FP guard)

Two production defects fixed, both live-caught in conductor's session
(2026-09-17 11:51 + 16:57 route_fired_no_stash, Venice-rendered on a
technical answer that had zero refusal semantics):

1. **Code-context FP guard** (`classifier.py:_match_in_code_context`):
   the POST refusal scan matched the literal `_REFUSAL_PHRASES` source text
   quoted inside a debugging answer — documentation, not a refusal. The
   guard suppresses combined-regex hits whose own line carries code/quote
   context: backticks, `\b`/`(?:`/`.{0,` regex-source markers, quoted-string
   list lines, dict/identifier context. Real prose refusals are unaffected
   (7-case adversarial battery passes; line_hold_essay untouched).
   Fail-open: any guard error routes as before.

2. **Chain config restore** (fleet config, not plugin code): abliteration.ai
   `abliterated-model-large-v2` restored as PRIMARY render, Venice
   `qwen-3-8-27b` as FALLBACK — all 11 profiles (was Venice-only after the
   abliterated decommission left the chain with no primary decision).

Known remaining limitation (not fixed here): quoted refusal speech inside
prose ("she said 'I can't help'") still matches — pre-existing behavior,
documented, far rarer than the source-quote class.

Tests: tests/test_code_context_guard.py (7 cases).
