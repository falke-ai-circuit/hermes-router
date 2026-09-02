# Compatibility matrix

| Hermes version | Config surface | Notes |
|---|---|---|
| 0.20.0 | `uncensored_router:` in profile config.yaml | manifest v2 keys tolerated; requires_env prompts at install only |
| newer | plugin settings + legacy section | dual reader, no migration needed |

Hooks/middleware contract: `llm_request` middleware + `transform_llm_output` hook, keyword
payloads, callbacks accept `**kwargs` (additive-safe across core updates).
