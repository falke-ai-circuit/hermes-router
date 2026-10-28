# R76-P4-S0 — external package reconciliation memo

**Unit:** R76-P4-S0-pkg-reconciliation (read-only; no code changes, no commit)
**Memo author:** architect
**Package reconciled:** `/opt/data/tmp/refactor_pkg/hermes-router-review/` — EXTERNAL suggestion
package, cut against upstream `3c38a401` (2026-10-26). NOT adopted, NOT our plan.
**Repo:** `/opt/data/plugins/hermes_router`
**Snapshot verified:** `f4fae76` (6.13.12). **Note the drift:** the unit brief named HEAD
`bebfdf7` (6.13.11); while this unit was running, `f4fae76` (R76 P2 U1, 6.13.12) landed from the
conductor. That commit removes dead code in `reflex_lane.py` only (`HEADER`, `VALIDATE`,
`_PATH_RE`/`_CMD_RE`/`_GENERIC`, `candidates()`, old `render()`) and bumps `plugin.yaml`. Every
citation below was re-verified AT `f4fae76`; line numbers in `decision.py`, `povs.py`,
`decision_midturn.py`, `completion_audit.py` are unchanged by that commit (`reflex_lane.py` shifted
−67 after line 87, and its citations here reflect the new numbers).
**Their snapshot is an ancestor of ours:** `git merge-base --is-ancestor 3c38a401 HEAD` → 0. There
are 38 commits between their rev and `6.13.12`, so "STALE" below is a real commit interval, not a
guess.

## Method

Every row was checked against our source with a grep/read at the cited line, and the two
load-bearing claims (A1, A2) additionally by live probe. Nothing in this memo is derived from the
package's own docs or its passing test suite.

Live probe for A1 (run in this unit, read-only):

```
PYTHONPATH=/opt/data/plugins python3 -c "
import hermes_router as hr, hermes_router.core.config_access as ca
print(hr.config_access is ca)                     # -> True
ca.sub_block_alias = lambda *n: {'level': 9, 'backend': 'probe-injected'}
from hermes_router import decision
print(decision._cfg().get('level'))               # -> 9   (injected value visible)
"
```

That proves `decision._cfg()` executes the canonical reader. The "silently falls back to DEFAULTS"
mechanism they report does not exist.

Patch verification (independent of the brief): all three files in `patches/` fail
`git apply --check` at HEAD — `001` and `003` fail on `decision.py:1`, `002` on `povs.py:1`. Only
their intent is usable; no hunk is.

## Verdict counts (36 rows)

| Verdict | Count |
|---|---|
| ALREADY-TRUE | 3 |
| STALE | 3 |
| WRONG | 1 |
| LIVE | 26 |
| SCOPE-CHANGE | 2 |
| UNVERIFIED | 1 |

---

## Part A — AUDIT.md findings

| # | Claim (short) | Their evidence | Status at 6.13.12 | OUR evidence (file:line) | Verdict |
|---|---|---|---|---|---|
| A1 | Decision Lane config silently falls back to `DEFAULTS` because `from . import config_access` does not resolve | `decision.py#L199-L221` | The import resolves: package `__init__` re-exports the canonical reader, so `.` finds it. Probe shows an injection into the canonical reader reaches `_cfg()`. Was ALREADY fixed at their own snapshot — `fdd65de` (R23 leg 3, 2026-10-08) is an ancestor of `3c38a401`. What misled them is a stale comment, not a dead import. | `__init__.py:30` (`from .core import config_access`); `core/config_access.py:273-285` (`sub_block_alias`); `decision.py:211-213` (the resolving import); `decision.py:206-209` (the false comment); `gate/registration.py:34-41` (legacy aliases also install the name in `sys.modules`) | **WRONG** |
| A1b | (same finding) `_cfg()` intent diverges from behaviour for every key except `frame` | `decision_midturn.py#L289-L317` | Directionally right but the cause is not the import: only `decision.frame` has an integration-tested path from the canonical reader. The rest of the block is read through the same resolved import, so it is a coverage gap, not a resolution gap. | `decision_midturn.py:257-317` (`_frame_overlay`, `_cfg` single-key overlay); `decision.py:211-213` | STALE |
| A2 | The decision lane does not run before every planning request | `decision_midturn.py`, `gate/registration.py` | Fixed after their cut: `on_llm_request` now calls `reflex_lane.run_on_request` on every model call (R64b, `dbf127d`, 6.8.1 — 18 min after their rev and therefore not in it; frame rendering added R71, `b45fa22`). Residual throttles remain: `MIN_INTERVAL_S=12`, `DEDUPE_S=45`, `len(window) < 60` stand-down. | `gate/orchestration.py:283-291`; `reflex_lane.py:1008-1084`; `reflex_lane.py:372-373`; `reflex_lane.py:534-580` (window); `decision_extract.py:138-203` (candidate set) | **STALE** |
| A2b | The premise that `llm_request` is evaluated once per provider request (in the deployed Hermes 0.21.x) | `gate/registration.py` | Not provable from our tree — it is a host-runtime property. Our seam counter exists but no per-provider-request identity is recorded. Evidence that would settle it: a canary run where `seam_fires()['llm_request']` is compared against provider-side request count for one multi-step turn. | `gate/orchestration.py:255-261` (`seam_probe_fire`); `core/telemetry.py:198-207` | **UNVERIFIED** |
| A3 | Native Jev abstention is not a `Choice` criterion — `criteria` is built from candidate ids only, while `stand_down` appears only in `instructions` | `decision.py#L2781-L2827` | Holds verbatim at HEAD: `criteria` is populated from `options` only; `stand_down` is mentioned in the instruction string and accepted by the parser, but is not a selectable criterion. Their patch intent (003) is live. | `decision.py:2798-2810` (criteria from options); `decision.py:2826-2828` (instruction mentions `stand_down`); `decision.py:2849-2851` (parser accepts it) | **LIVE** |
| A4 | The probability distribution is collapsed to winner/confidence/runner-up ids | `decision.py#L2832-L2878` | Holds for the native-engine parser: `probabilities` is used only to rank and then discarded — `alternatives` carries ids, no weights. NOT true of the live every-call lane, which renders top-3 percentages. | `decision.py:2860-2873`; contrast `decision_extract.py:304-360` (percentages rendered) and a live specimen in this session's own tool stream (3 options at 42/20/16% plus dials) | **LIVE** |
| A5 | The execution/midturn render deliberately hides alternatives | `decision_midturn.py#L1391-L1414` | Holds, and it is a deliberate, evidence-backed reversal (R37-3: a delivered menu produced a live failure, `api_1792682802_3a6179a3`). Rejected set is kept in the ledger on purpose. This is a design-intent conflict with them, not a defect. | `decision_midturn.py:1326-1348` (`_render_execution_fork_note`, "no option menu"); `decision_midturn.py:1350-1363` (`_render_collapsed_route`, "no alternatives") | **LIVE** |
| A6 | Two distinct Frontier multi-POV taxonomies exist and are unnamed/unversioned | `povs.py#L37-L124`, `completion_audit.py#L1761-L1790` | Holds: four stance voices with fixed corners vs five review lenses in the audit prompt; neither is a typed/versioned set. `PostureVoiceSetV1` / `ReviewLensSetV1` return zero hits repo-wide. | `povs.py:61-124` (four POVs, corners ±1); `completion_audit.py:1770-1787` (five lenses: practitioner/outsider/skeptic/world-knowledge/person) | **LIVE** |
| A7 | Historical PRE calibration (r≈0.291 / r≈0.232, n=135) is association, not a causal quality demonstration | `mindset.py` | Holds: our header still states the correlations as the measurement basis, and the battery measures lane firing/behaviour, not a no-lane control arm or outcome quality. | `mindset.py:3-8`; `benchmarks/METHODOLOGY.md:1-25` (pass trichotomy = lane events, not task quality); `benchmarks/baseline-2026-09-11.json` (17 turn-level scenario records, no control arm) | **LIVE** |
| A8 | Refusal replacement is not a safe general-purpose correction mechanism; authorization/egress/consent separation is missing | `gate/orchestration.py#L1483-L1565`, `SECURITY.md` | The capability is real and documented as unfiltered. Some rails exist (excluded-class hard gate, loop guard, caps), but their ask is a delivery-authorization policy. That changes behaviour, not structure. | `gate/orchestration.py:909-916` (detect-and-replace path); `SECURITY.md:36` ("deliberately unfiltered per operator"); `docs/SAFETY.md:3-6` (excluded-class hard gate) | **SCOPE-CHANGE** |
| A9 | Shared `scope`/`rigour` semantics span incompatible sources with no type separation | `povs.py`, `anchor_exec.py#L1351-L1453`, `decision.py#L2206-L2290` | Holds on types: three producers, no `PostureTarget`/`ObservedPosture`/`PostureDelta`. Partly mitigated in substance — the `basis` distinction the finding asks for already exists as strings (`session`/`habitual`/`none`), and the scale-artifact bug was fixed deliberately (R53/R56 notes). | `povs.py:435-440` (`dials_from_claims`); `anchor_exec.py:1398-1448` (`base_used` basis strings, scale note); `decision.py:2696-2729` (jev `score` levels, a third vocabulary); zero hits for the three typed names | **LIVE** |
| A10 | Neutral POV tie resolves to a named voice (`operator`) although no corner won | `povs.py#L443-L479` | Holds verbatim: `0` is mapped to `-1` on both axes, so `scope=0, rigour=0` lands on the `(-1,-1)` corner = `operator`. `corner_of` reports `narrow+relaxed`. Their patch 002 intent is live. | `povs.py:457`; `povs.py:472-476`; `povs.py:107-120` (`operator` = `scope_dir=-1, rigour_dir=-1`) | **LIVE** |
| A11 | README says four lanes; manifest description says "two-lane" | README / manifest | Holds. | `README.md:3` ("four optional, fail-open lanes"); `plugin.yaml:1` and `plugin.yaml:11` ("generic two-lane") | **LIVE** |
| A12 | Manifest version and CHANGELOG disagree | `plugin.yaml` vs `CHANGELOG.md` | Holds and has widened: manifest is 23 minor versions ahead of the changelog, whose last commit is `f0684dc` (R53, 2026-10-24). No single source of version truth; nothing explains the two streams. | `plugin.yaml:3` (6.13.12); `CHANGELOG.md:1` (5.19.0); `tests/routing/test_r10_catalog_resolution.py:384` (test pins the manifest string) | **LIVE** |
| A13 | README says licence "Internal"; `LICENSE` and manifest say MIT | README / LICENSE / manifest | Holds. | `README.md:135`; `LICENSE:1`; `plugin.yaml:6` | **LIVE** |
| A14 | README quickstart names three seams; registration wires more | `README.md`, `gate/registration.py` | Holds: five middleware/hook registrations against three named in the quickstart. | `README.md:20-22`; `gate/registration.py:79-103` (`llm_request`, `llm_execution`, `transform_llm_output`, `transform_terminal_output`, `post_tool_call`) | **LIVE** |
| A15 | Operator guide says "two optional lanes" | `docs/operator-guide.md` | Holds. | `docs/operator-guide.md:3` | **LIVE** |
| A16 | INSTALL.md carries legacy config and outdated version numbering | `INSTALL.md` | Holds: knob sections are labelled by 5.x versions while the plugin is 6.13.x, and the file is hand-maintained. | `INSTALL.md:64` ("5.17.0+, R42"); no generator in `scripts/` (only `docs_drift_check.py`, which checks `docs/architecture.md` module paths) | **LIVE** |
| A17 | An audit docstring claims PRE is removed while the runtime supports PRE paths | `completion_audit.py` | Holds, narrower than stated: the removed thing is the *task-start regex consult*; PRE as a pre_mode pass still runs. Still a docstring/runtime drift. | `completion_audit.py:11` ("PRE (task-start regex consult): REMOVED (dispatch pre_mode semantics)"); `passes/dispatcher_pre.py` (live PRE pass) | **LIVE** |
| A18 | Module size / change surface (`decision.py` ~4.5k, `completion_audit.py` ~3k, `gate/orchestration.py` ~2.4k, `router_core.py` ~2.1k) | inspected files | Holds, and `decision.py` has grown past their figure. | `decision.py` 4537 lines; `completion_audit.py` 2979; `gate/orchestration.py` 2443; `router_core.py` 2099 | **LIVE** |
| A19 | Broad exception handlers can conceal regression paths; the repo "already has a swallow-audit baseline; that is good" | repo-wide | Half holds. The audit exists, but the baseline has drifted badly and the audit reports regressions in ~30 files, so the "baseline" is not a live constraint. Measured: 1408 `except Exception` across non-test code. | `scripts/swallow_audit.py`; `tests/swallow_baseline.json`; `scripts/ci.sh:23-24`; run at HEAD: `decision.py: 152 > 121`, `completion_audit.py: 100 > 58`, `anchor_exec.py: 85 > 41`, `router_core.py: 91 > 85`, `decision_midturn.py: 66 > 31`, `gate/orchestration.py: 60 > 38` (+ more) | **LIVE** |
| A20 | Model/telemetry hygiene: pinned model, counts not content, tenant isolation, deterministic request ids, idempotent ledger, advisory never re-consulted as evidence, provenance marked data-not-authority | repo-wide | Mixed. Content discipline and "data not authority" are already true; request-id determinism/idempotency is absent everywhere. | True: `core/decisions.py:12-24` (hash-only evidence_ref, separate `fail_class`), `docs/SAFETY.md:14-16`, `echo_guard.py:226-228`, `reflex_lane.py:611-625` (explicit "data, end of evidence" fence). Missing: zero hits for `request_id` repo-wide; `core/usage_ledger.py:74-206` appends without an idempotency key; `core/session_store.py:28-41` is a single local state db with no tenant scoping | **LIVE** |

---

## Part B — REFACTOR_PLAN.md structural sections

| # | Claim (short) | Their evidence | Status at 6.13.12 | OUR evidence (file:line) | Verdict |
|---|---|---|---|---|---|
| B1 | Target layout `api/ adapters/ domain/ lanes/ infra/` (replacing `core/features/passes/gate/api`) | `REFACTOR_PLAN.md#L3-L65` | Not adopted. Our tree keeps `api/ core/ features/ gate/ lanes/ passes/`; no `adapters/`, `domain/`, `infra/`. We do have our own one-owner-per-facility map, produced independently at R73. | tree at HEAD (`ls -d */`); `docs/r73-module-map.md:1-30` (target module map, one owner per facility) | **LIVE** |
| B2 | Enforce domain/adapters/infra layering with a single arbiter that owns the one advisory write | `REFACTOR_PLAN.md#L67-L76` | Not implemented. Delivery is spread across three injection points and the banner machinery; no arbiter module exists. The layering guard that does exist is narrower than their target. | `reflex_lane.py:1008-1084` (request surface), `frames.py` (platform rules), `debug_banner.py` (park/consume); `.importlinter:7-14` (target layers contract explicitly NOT active) | **LIVE** |
| B3 | No direct cross-lane imports | `REFACTOR_PLAN.md#L71-L75` | The general claim holds; their named example does not — `decision.py` does not import `completion_audit` at HEAD (zero hits). Real edges are elsewhere and are heavier than one import. | Holds: `decision_midturn.py:51` (`from . import decision`), `reflex_lane.py:128,801` (`decision`), `completion_audit.py:44` (`anchor_chain, anchor_exec, router_core, state`), `anchor_exec.py:1182` (`povs`). Their example: zero hits | **LIVE** |
| B4 | A single-request decision contract: snapshot → 2-8 candidates + `stand_down`/`other` → one Jev call → validated full distribution → one bounded ephemeral advisory → ledger row with evaluator version/time/cost | `REFACTOR_PLAN.md#L77-L89` | Not present as a contract. The live lane has snapshot-ish inputs and an ephemeral delivery, but no `stand_down`/`other` criteria (A3), no retained weights (A4), and no `request_id`/`step_id` identity. | `reflex_lane.py:534-580` (window), `decision_extract.py:138-203` (candidates), `decision_extract.py:205-223` (payload), `decision_extract.py:225-289` (parse), `decision.py:2798-2810` (criteria) | **LIVE** |
| B5 | Explicit turn/step/request state machine carrying `profile_id/session_id/turn_id/step_id/request_id/tool_call_id`, monotonic sequence, replayable snapshots | `REFACTOR_PLAN.md#L91-L104` | Not present. We have session-scoped state and per-turn banner state; there is no step/request identity to hang a row or a replay on. | `core/state.py`, `core/session_store.py:28-41`, `reflex_lane.py:52-67` (`_PENDING`, `_ACC` keyed by session only); `core/decisions.py:7` has `trace_id`/`task_id`/`event_seq` but no step/request id | **LIVE** |
| B6 | Shadow behaviour contract: a refusal is not proof of error; alternative rendering only after user authorization + host policy + provider-terms checks; bypass never a default | `REFACTOR_PLAN.md#L106-L108` | This is a behaviour/policy change with a real cost (lane mute rate), not a refactor. Our current posture is deliberate and documented. | `SECURITY.md:36`; `gate/orchestration.py:909-916`; `docs/SAFETY.md:3-6` | **SCOPE-CHANGE** |
| B7 | Migration phases 0-8 (baseline → config → typed contracts → full weights → per-request → arbiter → adapters → modularize → CI) | `REFACTOR_PLAN.md#L110-L123` | Our own practice already matches the shape: R73 (cards/envelope/dead code), R74 (banner aggregation), R75 (banner price key), R76 (dead fork-era family) are exactly staged, gate-first, behaviour-frozen units. Their phases 2-3 correspond to work we have partly done and partly still owe (A3/A4). | `git log --oneline 3c38a401..HEAD` (38 commits, all staged single-purpose units); `docs/r73-module-map.md`, `docs/r73-dead-code.md` | **ALREADY-TRUE** |
| B8 | Seven changes NOT to make in one PR (default-on + import flip together; replace `router_core`; remove refusal/approval rails; conflate posterior/suitability/success; sum unrelated scope/rigour; drop ledger schemas; treat `post_tool_call` as a blocking intercept) | `REFACTOR_PLAN.md#L124-L132` | All seven are already our practice: the lane ships dark and is armed per profile; `router_core` is intact; `post_tool_call` is documented observational; the ledger is versioned. | `reflex_weights.py:14-20` (default OFF); `decision_midturn.py:1803-1813` ("Reflex-only seam. Never raises, never returns a value, never blocks"); `core/decisions.py:5-17` (`schema_version: 1`); `gate/registration.py:92-103` | **ALREADY-TRUE** |
| B9 | Acceptance gates: one telemetry row per distinct provider request with status in {evaluated, reused, degraded, bypassed-by-policy}; bounded cost/timeout; >=2 candidates or recorded degraded; provenance-tagged, never recursively routed | `REFACTOR_PLAN.md#L134-L143` | Not met as stated: we emit reason codes, not a per-request row with a closed status vocabulary, and there is no request identity to key one on. | `reflex_weights.py:29-60` (`_note(**kw)` → last-reason only); `reflex_lane.py:1120-1128` (reason codes `no_candidates`, `duplicate_situation`, gate reason); `core/telemetry.py:125-155` (`log_route`) | **LIVE** |
| B10 | Existing test/lint discipline (ruff, pytest, import-linter, swallow audit, docs drift, CI matrix) | `REFACTOR_PLAN.md#L145-L147` | Already true and broader than they credited. | `scripts/ci.sh:10-40` (ruff → pytest → swallow_audit → lint-imports → docs_drift); `.importlinter`; `.github/workflows/ci.yml:10-30` (3.11/3.12/3.13 × ubuntu/macos) | **ALREADY-TRUE** |
| B11 | Packaging/release baseline: single `pyproject.toml`, mypy/pyright, CodeQL, secret scanning, protected main, generated changelog, CI-tagged releases, SLSA provenance, semver from one source | `REFACTOR_PLAN.md#L145-L147` | Absent: no `pyproject.toml`, no type gate, no CodeQL/secret scan workflow, and A12 shows version truth is not single-sourced. | absent `pyproject.toml`/`setup.cfg`; `.github/workflows/` contains only `ci.yml`; `plugin.yaml:3` vs `CHANGELOG.md:1` | **LIVE** |

---

## Part C — the three patches

| # | Claim (short) | Their evidence | Status at 6.13.12 | OUR evidence (file:line) | Verdict |
|---|---|---|---|---|---|
| C1 | `patches/001-decision-config-import.diff` — fix the config import so the decision block reads real config | `patches/001` | Obsolete. The import already resolves (A1, proven by probe) and has since `fdd65de` (2026-10-08), which predates their snapshot. Applying it would change nothing but would churn a line that carries a comment. The only live defect here is the stale comment. Fails `git apply --check` (`decision.py:1`). | `__init__.py:30`; `decision.py:211`; `decision.py:206-209` (comment to fix instead) | **STALE** |
| C2 | `patches/002-pov-neutral-tie.diff` — a neutral tie must not name a voice | `patches/002` | Holds; unmapped at HEAD. Fails `git apply --check` (`povs.py:1`), so intent only. | `povs.py:457`; `povs.py:472-476`; `povs.py:107-120` | **LIVE** |
| C3 | `patches/003-native-jev-standdown.diff` — make abstention a real Choice criterion | `patches/003` | Holds; `criteria` still excludes `stand_down`. Fails `git apply --check` (`decision.py:1`), so intent only. | `decision.py:2798-2810`; `decision.py:2826-2828` | **LIVE** |

---

## The three claims most consequential for our own Phase 4

**1. A3 — abstention is not a selectable criterion (`decision.py:2798-2810`).**
This is the one that quietly corrupts what Phase 4 is supposed to measure. We ask the model to
choose, hand it a criteria set that cannot express "do nothing", and then measure whether its top
pick matched what the agent did next. Any turn where the honest answer was *no action* is scored as
a disagreement with the lane, not as correct abstention — so the calibration numbers Phase 4 will
be built on are biased in the direction that flatters a lane which always names a move. It is also
cheap to close and does not change the delivery surface: two extra keys in the same request, plus
exact-id validation on the response. It ranks above A4 because A4 is a rendering/token-budget
question while A3 is a measurement-validity question.

**2. A4 + A5 — the weights we already compute are thrown away on the legacy path, and the midturn
render hides alternatives on purpose (`decision.py:2860-2873`, `decision_midturn.py:1350-1363`).**
Phase 4 has to pick one register per surface and say so. Today we run two contradictory policies at
once: the every-call lane renders a distribution (42/20/16% + dials, seen live in this very unit's
tool stream) while the legacy midturn path collapses to a single named route because a delivered
menu caused a real failure at R37-3. A refactor that "unifies rendering" without naming which
register wins will either resurrect that regression or silently delete the distribution Phase 4
wants to evaluate. This is the claim that forces a decision, not a patch.

**3. A2 — "runs before every planning request" is now true but throttled (`gate/orchestration.py:283-291`,
`reflex_lane.py:372-373`).**
The external package's headline P0 is stale, and the danger is the opposite of the one it names:
someone reading it concludes the lane never fires per request and "fixes" a working seam, or
concludes it always fires and budgets accordingly. It fires per model call *subject to* a 12s
interval, a 40-per-session cap, a 45s duplicate-situation suppress, and a 60-char window floor —
none of which appear in a 0.32-confidence reading. Phase 4's cost and latency model, and any
eval that counts "decisions per turn", must be built on those four numbers or the numbers will be
wrong in the expensive direction.

## What I did not settle, and what would settle it

- **A2b** — that `llm_request` fires exactly once per provider request in the deployed Hermes
  0.21.x. Needs a canary turn with `seam_fires()['llm_request']` compared to provider-side request
  count. Everything in A2 inherits this assumption; if it is wrong, the "per request" half of the
  package's P0 is a host-behaviour question, not a router one.
- **A7** — no no-lane control arm exists anywhere in our measurement surface, so the causal-status
  question is real but cannot be closed by reading source; it needs an experiment, which is
  SCOPE-CHANGE territory, not refactor.
- **A19** — I measured the drift by running `scripts/swallow_audit.py`; I did not determine whether
  the regressions are intentional (new lanes legitimately adding guarded paths) or accretion. That
  needs a per-file reading, which belongs to a later unit.

## Scope-change register (do not fold into a refactor plan)

1. **A8 — refusal replacement / delivery authorization.** Changes lane behaviour and mute rate;
   needs an operator decision, and our `SECURITY.md:36` posture is currently deliberate.
2. **B6 — Shadow behaviour contract.** Same class: authorization, consent, egress telemetry, and
   "bypass never a default" are product and policy decisions with a cost line attached.

Both are legitimate asks. Neither is a refactor, and adopting either inside a structural plan would
hide a behaviour change in a commit message about module boundaries.
