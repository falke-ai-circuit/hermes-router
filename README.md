# hermes-router

Portable **any-agent** Hermes plugin: when a censored primary model refuses, the plugin routes the
render to an ordered chain of uncensored models (primary → fallback), speaking in the **agent's own
persona** — built dynamically from the loading profile's DNA at call time. No hand-curation, no
hard-coded profile.

## What it does

- **PRE** (`llm_request` middleware): classifies the outgoing request (stage-1 regex patterns,
  optional stage-2 aux-LLM semantic gate). Contested asks are rewritten into a substance-frame
  render request.
- **POST** (`transform_llm_output` hook): if the primary model's reply is a refusal, the render
  chain takes over. The uncensored model's output replaces the refusal **in the agent's voice**,
  backed by a dynamic persona card + thread digest.
- **Sync seam (FIX1)**: the delivered render is reconciled into the model's history — the agent
  *knows* the uncensored model answered in its place and continues seamlessly.
- **Model chain**: ordered `chain:` of uncensored endpoints. First success wins; any failure
  (dead key, timeout, connect error, 5xx, api error, empty response) falls through to the next.
- **Persona card**: extracted from `HERMES_HOME/{IDENTITY,SOUL}.md` at call time — voice stems,
  authorial lines, closed lines. Secret-scrubbed, mtime-cached. Never reads MEMORY/AGENTS/config.
- **Fail-open everywhere**: every failure degrades to normal agent pass-through. The plugin never
  crashes a turn.

## Hard safety gate

`csam_underage` (and other excluded classes) **never route** — enforced code-side, not
config-tunable. Secret scrubbing on all persona-card output. Keys live in 0600 files or env vars,
never in logs.

## Install

```bash
hermes plugins install falke-ai-circuit/hermes-router
hermes plugins enable hermes-router
# restart the gateway, then add per-profile config (see INSTALL.md)
```

Requires Hermes ≥ 0.20.0 (dual config reader: plugin settings on newer runtimes,
`uncensored_router:` profile-config section everywhere). See `docs/COMPAT.md`.

## Model chain config (profile config.yaml)

```yaml
uncensored_router:
  enabled: true
  chain:
    - name: abliteration-large
      url: https://api.abliteration.ai/v1/chat/completions
      model: abliterated-model-large
      key_file: ~/.hermes/profiles/<profile>/.secrets/abliteration_key
      extra_body:
        thinking: true          # provider quirk: content arrives in `content` field, reasoning separate
    - name: venice-qwen
      url: https://api.venice.ai/api/v1/chat/completions
      model: qwen-3-8-27b
      key_file: ~/.hermes/profiles/<profile>/.secrets/venice_key
```

Legacy single-endpoint config (`endpoint:`) still works as a chain of one.

## Tests

```bash
python3 -m pytest tests/ -q
```

177 tests: pattern classification, PRE/POST routing, loop guard, render inbox, persona card,
model chain fallback, manifest load, semantic stage-2, live Venice call (skips without key).

## Status

Battle-tested on a live production agent since 2026-09-01 (refusal→render→reconcile loop verified
end-to-end, including multi-turn continuity). Fleet-internal license; ask before reuse.
