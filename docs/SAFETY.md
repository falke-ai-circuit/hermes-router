# Safety posture

## Excluded-class hard gate
Excluded classes (csam_underage and siblings) **never route** — not to the render chain, not in
dry-run. Enforced in code (classifier scan path), not config-tunable. These short-circuit before
any model call.

## Secret handling
- Persona card output scrubbed (sk- keys, Bearer tokens, emails, password rows, long hex).
- Keys resolve: entry `key_file` (0600) → `key_env`/`VENICE_API_KEY` env → fail-open pass-through.
- Keys never in logs; curl auth via chmod-600 config file, never argv.

## Fail-open guarantee
Every module's failure path returns empty/no-op: plugin down = normal agent. Never raises into the
host turn loop. A misconfigured chain logs `route_failed` and yields a normal agent turn.

## No content in logs
Logs carry event names, pattern-group names, char counts, session ids — never user content or
render text.
