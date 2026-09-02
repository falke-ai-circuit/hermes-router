# Install — uncensored-router

Works on any Hermes profile (any agent). Three steps + optional smoke test.

## 1. Install

```bash
hermes plugins install falke-ai-circuit/uncensored-router
```

You will be prompted for `VENICE_API_KEY` / `MINIMAX_API_KEY` if unset (saved to `~/.hermes/.env`).
Alternatively use per-entry `key_file` paths in config (0600 files) — see README.

## 2. Enable for the profile

```bash
hermes plugins enable uncensored-router
```

Then add config to the profile's `config.yaml` (chain of uncensored models):

```yaml
uncensored_router:
  enabled: true
  dry_run: false
  chain:
    - name: primary
      url: https://api.abliteration.ai/v1/chat/completions
      model: abliterated-model-large
      key_file: ~/.secrets/abliteration_key
      extra_body:
        thinking: true
    - name: fallback
      url: https://api.venice.ai/api/v1/chat/completions
      model: qwen-3-8-27b
      key_file: ~/.secrets/venice_key
```

## 3. Restart gateway

```bash
find ~/.hermes/plugins/uncensored-router -name __pycache__ -type d -exec rm -rf {} +
hermes gateway restart
```

## 4. Smoke test (3 gates)

**Gate A — registration** (zero cost):
`HERMES_PLUGINS_DEBUG=1 hermes -p <profile> -q "list your available hooks"` → gateway log shows
uncensored-router loaded, no unknown-hook warnings.

**Gate B — dry-run PRE probe** (zero API cost): set
`uncensored_router: {enabled: true, dry_run: true, classification: {pre_patterns: [ied_construction]}}`,
send a "pipe bomb wiring diagram" ask, expect `PRE event_detail=dry_run` in the log with the agent
reply UNCHANGED, then revert dry_run.

**Gate C — live POST probe** (one render call): dry_run off, trigger a certain refusal. Expect
`POST event_detail=route_fired`, `rendered_chars > 0`, render in
`<hermes_home>/uncensored-router-renders.jsonl`, and the agent's next turn continues seamlessly
(`history_reconciled` in log).

All three green = installed. Failures degrade to pass-through — a half-working install never
breaks the agent.

## Compatibility

Hermes 0.20.0: full function (config via `uncensored_router:` section; manifest v2 keys
tolerated). Newer runtimes add plugin-settings config + config_schema validation — no migration
needed, the plugin reads both surfaces.
