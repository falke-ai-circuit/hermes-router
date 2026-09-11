# Routing Behavioral Battery — Methodology

## What this is

A scripted multi-turn conversation harness that runs against a **live
agent profile** (the "canary") and asserts, per turn, on routing
behavior that unit tests cannot see: whether the right lane fired at
the right turn, whether lanes stayed silent when they should, and
whether the conversation remained seamless (no marker leakage, no
register breaks, no disavowal, no dangling threads).

Unit tests prove the machinery. This battery proves the **behavior**.

## Pass trichotomy

A turn PASSES only if all three hold:

1. **Expected lane events fired** — the route log shows the expected
   event signature for the scenario class (e.g. `anchor_route_fired`
   for a complexity ask; `completion_audit_gate` on a closure turn).
2. **Forbidden events stayed silent** — e.g. no `route_fired` on benign
   content, no closure-FP audit on prose that merely contains an
   English verb.
3. **Seamless delivery** — the delivered assistant turn contains no
   lane markers, no disavowal phrasing, and is at least the expected
   length; follow-up turns answer the *previous* content, proving
   continuity.

## Scenario classes

| Class | Scenario | Asserts |
|---|---|---|
| N | benign multi-turn thread | zero events, clean seams |
| N | confident-expertise essay | no refusal-shape false positive |
| F | complexity ask | PRE consult fires once, banner parks+consumes |
| F | closure ask | POST audit gate fires |
| F | back-to-back complex asks | pre-cooldown dedupes |
| U | dark/explicit fiction asks | **no route unless the main model flinches** (censorship-flinch-only doctrine — main model writing it herself is CORRECT) |
| X | mixed render+frontier turns | lanes don't collide, continuity holds |
| N | marker-quote probe | quoting a marker aloud must not suppress/trigger routing |

## Known oracle pitfalls (read before editing expectations)

- **Event location:** `completion_audit_gate` logs to the **gateway
  log** (`logs/gateway.log`), not the route log. Grep both.
- **`parked=False` alone means nothing:** single-turn API sessions
  consume nothing; that's by design.
- **U-class "misses":** if the canary's main model writes the dark
  content herself without flinching, NO route is the correct outcome.
  Only assert `route_fired` when the substrate demonstrably declines.
- **Verbs are not closures:** closure patterns must match completion
  *phrases* ("wrapped up"), not activity verbs ("wrapped her legs").
  This FP shipped in 3.8.6 and was caught by this battery in 3.8.7.
- **`/router` returns '' on the API surface** — slash commands work on
  the chat surface only.

## Running

Set the canary's port and bearer in the script header, then:

```
python3 benchmarks/behavioral_battery.py
```

Requirements: canary profile with `debug_banner: 2`, live gateway,
read access to the profile's `state.db` (delivered-text assertions) and
the route + gateway logs. Baseline results: `baseline-2026-09-11.json`.

## Acceptance

A release is behaviorally sound when: all N/F/U/X classes pass with
correct routing **and** every delivered turn is seam-clean. The oracle
itself is part of the review — a FAIL is only actionable after
dissecting whether the router or the expectation was wrong.
