# Security Policy

## Scope

`hermes-router` is an in-process plugin for Hermes Agent that intercepts
LLM request/response flow. It **sees 100% of prompt and completion traffic**
on every agent profile it is installed on, and it holds credentials for
frontier and uncensored model providers.

## Reporting a vulnerability

Report privately: open a GitHub Security Advisory on
`falke-ai-circuit/hermes-router` (Security tab → Report a vulnerability),
or contact the repository owner directly.

Please do **not** open a public issue for anything involving:

- credential leakage (API keys, provider secrets),
- prompt content escaping the local machine to an unexpected endpoint,
- the uncensored render chain delivering content outside its configured lane,
- authentication bypass on the router control surface (`router_control` tool,
  `/router` command token guard).

## Threat model notes

- The plugin performs **outbound HTTPS calls** to configured frontier
  (anchor chain) and uncensored-chain endpoints only. It never proxies
  traffic to third parties beyond those endpoints. Any unexpected
  outbound destination is a vulnerability.
- Secrets are resolved at call time from process env → profile dotenv.
  Secrets are never logged; route logs record event names, sizes, and
  session IDs, never key material or prompt bodies.
- State sidecars (spend ledger, backoff ledger, pending-render map)
  are written under the Hermes profile directory with user-only
  permissions. Treat them as sensitive: they reference session IDs.
- The uncensored lane is **deliberately unfiltered** per operator
  configuration. Operators are responsible for complying with the laws
  and provider ToS of their jurisdiction. The plugin ships no content
  of its own.
- Sentinel markers (`HIGHER-SELF … TURN`, `UNCENSORED-ROUTER INJECTION`,
  etc.) are string-matched, not cryptographically attested. A user who
  can inject text into the conversation can forge them to suppress
  routing. This is documented as a known limitation, not a supported
  boundary.

## Supported versions

Only the latest tagged release receives security fixes. The plugin
follows the fleet deploy model: fixes land on `main`, are released
immediately, and are rolled to all installed profiles the same day.
