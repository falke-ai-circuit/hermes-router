# R76-P4 — refactor plan (OUR plan, not the external package's)

**Unit:** R76-P4-S0a-refactor-plan · **Author:** architect · **Mode:** design only, no source changes
**Repo:** `/opt/data/plugins/hermes_router` · **HEAD:** `edc95e89feace9eb7a7bb345d25eb98611f317fb` (6.13.23, tree clean)
**Written:** 2026-10-27T20:40:34Z (kernel `date -u`), after reading at that commit.
**Amended:** 2026-10-28T00:03:41Z (kernel `date -u`) at `e846ae9` — §A1 rewritten and §A1-R ruling
added after R76-P4-S2 measured that `type = layers` checks transitive routes (S2 BLOCKED, no commit).
The S2 row is replaced by S2 + S2b; §C ordering and §D are updated to match.
**Inputs:** `docs/r76-pkg-reconciliation.md` (own memo, 6.13.11-era), `docs/r73-module-map.md`,
`docs/r73-dead-code.md`, and the external package at `/opt/data/tmp/refactor_pkg/hermes-router-review/`
(suggestion only — no part of it lands as-is; its tree is not adopted).

Every claim below was read or run at `edc95e8`. Nothing here is derived from the package's docs or from
recall of an earlier commit.

---

## 0. Re-check before planning (the memo is 12 commits stale)

The memo was written at 6.13.11/6.13.12. Between then and HEAD the fix phase landed 11 source commits
(`f439178` … `50a3311`). I re-ran the evidence for **all 26 LIVE rows**. Result:

**0 of the 26 are resolved outright at `edc95e8`.** Three need their evidence line amended (numbers/lines
moved), eleven rows have *adjacent* work landed that a careless plan would re-propose, and none of the
structural gaps the plan is for have closed.

What the fix phase **did** close — from `docs/r73-dead-code.md`, not from the memo — and which therefore
must **not** appear as a slice in this plan:

| Dead-code item | Closed by | Evidence at HEAD |
|---|---|---|
| §1 fork family `reflex_lane.parse_answers/payload/_num/render/candidates/HEADER/_PATH_RE` | `f4fae76`, `f439178` | `grep -n 'def parse_answers\|def payload\|def _num\|def render\|def candidates' reflex_lane.py` → no hits |
| §3 `min_fork_probability` knob (set, never read) | `f439178` | no hits repo-wide |
| §4a hand-formatted banner in `anchor_exec.py:1679-1681` (bypassed `format_banner` validation/redaction) | `c8df8c4` + `2c243fc` + `50a3311` | `anchor_exec.py:1690` now calls `_dbf.format_banner`; `:1681` comment states what the call does and does not buy |
| §4b `DELIVERED_MARK` literal replica | `39772dd` | `decision.py:127` `DELIVERED_MARK = _delivered_mark()`, derived from `debug_banner` |
| §5a unreachable non-empty arm in `decision_extract.render` | `91cb363` | `decision_extract.py:381-385` — the arm is gone, the reasoning is kept as a comment |
| §5d `_soft_act` dead assignment + `_r`/`_lab` dead locals | `91cb363` | absent |
| **r73 module-map S1 — card accessor** | a later unit | `persona_card.role_card(purpose=…, messages=…, home=…)` exists at `:411`; `reflex_lane._agent_card` is a docstringed thin wrapper (`:82-98`, "R73 P2 S1"); `decision.py:1906` already reads `role_card(purpose="read")`. **Remaining, not done:** 3 call sites still call `build_persona_context()` directly (`completion_audit.py:2018`, `anchor_exec.py:1620`, `passes/dispatcher_knobs.py:45`, plus `instinct_frame.py:119`), and **three** mandate-stripper bodies still coexist (`persona_card.py:348` section-aware, `instinct_frame.py:94`, `completion_audit.py:2026`). |

Still open from that inventory (candidates, not yet slices): §3b `min_gap` (`reflex_lane.py:120`, set and
never read), §4c `"router · frontier"` marker, §4d `_DECISION_MARK`, §7 `reflex_weights` dead function
family. Also re-measured at HEAD and **stale in the map**: park sites are **19 across 10 modules**, not the
10 the map counted — `route_gate.py:1651`, `anchor_exec.py:1002/1048`, `decision_miner.py:671` and others
landed after that audit.

This is the trap the brief warned about: two minutes of re-checking removed a slice that would have rebuilt
an accessor that already exists. `S6` below is therefore the *remainder* of that work, not the whole of it.

### The row-by-row re-check (LIVE rows only)

Verdicts below are **at `edc95e8`**, each with the probe that produced it.

| Row | Re-check at HEAD | Evidence |
|---|---|---|
| A3 abstention not a selectable criterion | **still live** | `decision.py:2798-2826` builds `criteria` only from `envelope["options"]`; `stand_down` appears in `instructions` (`:2840`) and is accepted by both parsers (`:2559-2567`, `:2862-2866`) but is never a criterion. **Amend:** a code comment (`:2557`) now claims "the lane injects ONE escape option" — the parser accepts it, the criteria set does not offer it. The measurement bias stands; the comment overstates the code. |
| A4 distribution collapsed to ids | **still live** | `decision.py:2874-2886`: `probabilities` used only to rank, then discarded; `alternatives` carries ids, no weights |
| A5 midturn render hides alternatives | **still live** | `decision_midturn.py:1335` `_render_execution_fork_note`, `:1359` `_render_collapsed_route` ("no alternatives") |
| A6 two untyped POV taxonomies | **still live** | `PostureVoiceSetV1`/`ReviewLensSetV1`/`PostureTarget`/`ObservedPosture` → zero hits repo-wide |
| A7 r≈0.291/r≈0.232 is association | **still live** | `mindset.py:3-8` unchanged |
| A9 shared scope/rigour vocabulary | **still live** | probe above: zero hits for the three typed names |
| A10 neutral tie names `operator` | **still live** | probe: `dials_from_claims({all four voices at 1.0})` → `{scope: 0.0, rigour: 0.0}`; `corner_of(0,0)` → `narrow+relaxed`; `winning_voice(...)` → `operator` (`povs.py:457` maps `0` to `-1`) |
| A11 README "four lanes" vs manifest "two-lane" | **still live** | `README.md:3-5`; `plugin.yaml:1`, `:11` |
| A12 manifest vs CHANGELOG version truth | **still live, widened** | `plugin.yaml:3` = `6.13.23`; `CHANGELOG.md:1` = `## 5.19.0 — 2026-10-24` (24 minor versions apart) |
| A13 README licence "Internal" vs MIT | **still live** | `README.md:133-136`; `plugin.yaml:6`; `LICENSE:1` |
| A14 quickstart names three seams | **still live** | `README.md:20-22` vs `gate/registration.py:80-101` (five registrations) |
| A15 operator guide "two optional lanes" | **still live** | `docs/operator-guide.md:3` |
| A16 INSTALL legacy version numbering | **still live** | `INSTALL.md:64` ("5.17.0+, R42") on a 6.13.x plugin |
| A17 audit docstring says PRE removed | **still live** | `completion_audit.py:11` vs live `passes/dispatcher_pre.py` |
| A18 module sizes | **still live, larger** | `decision.py` 4575 (was 4537), `completion_audit.py` 2979, `gate/orchestration.py` 2443, `router_core.py` 2099 |
| A19 swallow baseline drifted | **still live (re-measured)** | `scripts/swallow_audit.py` at HEAD reports regressions in ~30 files (`decision.py 152>121`, `route_gate.py 60>59`, `router_core.py 91>85`, …) |
| A20 request identity / idempotency | **still live** | `request_id`/`step_id`: one hit repo-wide is a *test function name*; `record_tokens` (`core/usage_ledger.py:78`) still appends with `ts` only |
| B1 greenfield `api/adapters/domain/…` | **still live as a gap** — and deliberately not adopted | tree = `api/ core/ features/ gate/ lanes/ passes/` |
| B2 no delivery arbiter | **still live** | no `reading_delivery.py`; delivery still split across `reflex_lane.run_on_request`, `frames.APPEND_PLATFORM_RULE`, `debug_banner` park/consume |
| B3 cross-lane imports | **still live** | `decision_midturn:51→decision`, `completion_audit:44→anchor_chain,anchor_exec,router_core,state`, `anchor_exec:30-31`, `reflex_lane:37-38` |
| B4 single-request decision contract | **still live** | no `stand_down`/`other` criterion (A3), no retained weights (A4), no request identity (A20) |
| B5 turn/step/request state machine | **still live** | `core/state.py` has `turn_id`; no `step_id`/`request_id` |
| B9 per-request telemetry row | **still live** | `reflex_weights._note()` overwrites `LAST` (`_note` clears the dict) — last reason wins; 13 `_note` call sites in `reflex_lane.py` |
| B11 packaging/release baseline | **still live** | no `pyproject.toml`, no `setup.cfg`; `.github/workflows/` = `ci.yml` only |
| C2 neutral tie | **still live** | = A10 |
| C3 native Jev stand-down | **still live** | = A3 |

**One finding the memo could not have had.** `PYTHONPATH=/opt/data/plugins lint-imports` **exits 1 at
HEAD on clean reviewed code** and prints no contract report — only seven lines of
`No matches for ignored import …`. Cause: every entry in the two `ignore_imports` blocks is written with a
**symbol** in the module position (`hermes_router.core.canonical.persona_card -> hermes_router.persona_card`)
instead of a module (`hermes_router.core.canonical -> hermes_router.persona_card`), so no entry matches
anything. Proven by control: a minimal config over the same graph reports normally
(`Analyzed 92 files, 320 dependencies … Contracts: 0 kept, 1 broken`), and a config with the seven entries
rewritten to `module -> module` matches six of them and reports the dangling ones
(`core.routing_caps -> router_tools`, `core.config_access -> hermes_cli.config`) instead of failing silently.
So the guard the brief tells me to ground the plan in is **currently red for a config-hygiene reason, not a
code reason** — the first slice repairs it, because every later slice's acceptance leans on it.

**Second finding.** `python3 -m pytest tests -q -m "not live"` at HEAD: **8 failed, 2 errors, 2527 passed,
2 skipped, 1 deselected, 1 xfailed, 276.74s**. The baseline is not green, so "suite green per slice" cannot
be asserted as "0 failed". It is defined below as failure-set equality against a pinned baseline, and the
red tests must be triaged before any slice can be marked GREEN.

---

## A. TARGET SHAPE — in our terms

The tree stays what it is. `core/ features/ passes/ gate/ lanes/ api/` exist and are populated (92 modules:
api 7, gate 3, passes 4, features 9, lanes 3, core 12, plus top-level facility modules). Nothing is moved
into a greenfield layout; the external package's `adapters/domain/infra` tree is rejected in §E.

### A1. Layering: what is actually enforceable (REVISED 2026-10-28 at `e846ae9`)

Declared order, unchanged — a lower layer may never import a higher one:

```
api  >  gate  >  passes  >  features  >  lanes  >  core
```

**The first draft of this section was wrong in its mechanism and in its count.** It said a `type = layers`
contract checks direct imports and that the ladder was "seven edges from being true". Measured at `e846ae9`:

* `type = layers` checks **transitive routes** (import-linter 2.15, `contracts/layers.py:check` ->
  `graph.find_illegal_dependencies_for_layers`) and the `layers` type has **no** `allow_indirect_imports`
  field — verified against the installed source, not the docs.
* Declared on this tree it reports **11 illegal pairs / 45 routes**, not 7 edges. 31 of the 43 grimp `Route`
  objects pass through the package hub.

| measurement (`grimp.build_graph("hermes_router")`, 92 modules / 320 internal edges) | value |
|---|---|
| illegal **pairs** the layers contract reports | **11** |
| route entries printed by `lint-imports` | **45** (43 `Route` objects, 167 head x tail expansions) |
| of those, hub-mediated (`... -> hermes_router -> ...`) | 31 of 43 |
| true **direct** upward edges | **7** — 6 x `gate.registration -> api.*`, 1 x `passes.dispatcher_pre -> api.router_tools` |

| lower layer | imports | upper layer | direct edges |
|---|---|---|---|
| gate | api | `gate.registration -> api.{commands, commands_config, commands_diag, commands_runtime, config_writer, router_tools}` | 6 |
| passes | api | `passes.dispatcher_pre -> api.router_tools` | 1 |

Why HEAD is red transitively: `hermes_router/__init__.py` imports `api.*` (l.44-49) and `gate.registration`
(l.54) at module scope and re-exports ~30 modules, while 29 source modules import the package itself
(`from . import X`, 238 sites across 37 files). Each of those is an edge to `hermes_router`, and
`hermes_router` has an edge to every upper layer — so almost every lower module reaches every higher one
through the hub.

The target in the first draft is right; only the mechanism was wrong. The ladder ships as **five direct-edge
`forbidden` contracts**, each carrying `allow_indirect_imports = True` (without that flag `forbidden` also
checks indirect routes — measured: 4 of the 5 fail on the untouched tree; with it they are green):

```
gate may not import api (direct)                       KEPT (6 ignored imports)
passes may not import api (direct)                     KEPT (1 ignored import)
features may not import api/gate/passes (direct)       KEPT
lanes may not import api/gate/passes/features (direct) KEPT
core may not import api/gate/passes/features/lanes (direct) KEPT
Contracts: 5 kept, 0 broken.                           EXIT 0
```

With the two contracts that already pass, the guard is **7 contracts / 2 exemptions**: the composition root
(`gate.registration -> hermes_router.api.*`, one wildcard line covering all 6 edges) and the pass edge that
S3 exists to remove. After S3: 7 contracts / 1 exemption.

`core/` stays pure. Its six direct non-core imports remain the documented carry set
(`core.canonical->persona_card`, `core.decisions->suggestions`, `core.routing_caps->anchor_chain`,
`core.routing_caps->debug_banner`, `core.usage_ledger->anchor_chain`, `core.usage_ledger->provider_prices`);
the rule is "must only shrink", and after S1 every entry in that list must correspond to a live edge or be
deleted.

**What is lost, stated so it cannot be papered over:** a *new* `from . import X` site that reaches an upper
layer through the hub is caught by none of the seven contracts. That escape is what S2b ratchets, and it is
the honest limit of this guard family — it constrains direct imports; the hub's eager import graph is held
by a budget, not by a proof.

### A1-R. RULING on S2 (architect, 2026-10-28T00:03:41Z, measured at `e846ae9`)

The earlier S2 row was wrong twice: **mechanism** (it assumed `type = layers` checks direct imports; it
checks transitive routes) and **count** (it counted 7 direct upward edges and concluded "seven edges from
true"; the contract as written sees 11 pairs / 45 routes). S1's repair is intact and the gate is healthy —
the error was in the design of S2, not in the guard beneath it. Correcting both is this ruling's job.

Evidence base (all re-run by me on the untouched tree at `e846ae9`, cwd = repo root unless stated,
`PYTHONPATH=/opt/data/plugins`):

| probe | result |
|---|---|
| baseline `lint-imports` on the committed config | `Contracts: 2 kept, 0 broken.` EXIT 0 |
| layers contract, plan template verbatim | EXIT 1, `0 kept / 1 broken`, 11 pairs / 45 routes |
| layers contract + `gate.registration -> api.*` wildcard | EXIT 1, `1 broken (6 ignored imports)`, still 11 pairs |
| layers contract + both upward edges exempted (7 ignored) | EXIT 1, `1 broken (7 ignored imports)`, **11 pairs / 38 routes** |
| **counterfactual: hub fully neutralised** (delete all 58 hub-involved edges: 29 importers -> hub, hub -> 29 targets), both exemptions, S3 landed | EXIT would be 1: **7 illegal pairs / 19 module pairs remain**, e.g. `core.routing_caps -> debug_banner -> features.banners.kinds`, `features.detection.semantic_judge -> dispatcher_pre -> passes.dispatcher_pre -> api.router_tools -> api.config_writer` |
| five `forbidden` contracts WITHOUT `allow_indirect_imports` | EXIT 1, `1 kept / 4 broken` (forbidden checks indirect by default) |
| five `forbidden` contracts WITH `allow_indirect_imports = True` | EXIT 0, `5 kept, 0 broken` |
| mutation: `features/_mutation_probe.py` = `from ..api.router_tools import register` | EXIT 1, features contract BROKEN |
| stale exemption injected (`features.detection.structural -> api.commands`) | EXIT 1, `No matches for ignored import ...` (`unmatched_ignore_imports_alerting` defaults to `error`) |

**(a) CHOSEN — no `layers` contract. The ladder ships as five direct-edge `forbidden` contracts; the two
contracts that already pass stay untouched.** What is lost: the transitive guarantee, i.e. the ability to
notice that a module reaches an upper layer *through the hub*. That guarantee never existed at HEAD and
cannot be bought by config, so nothing is being given up that the tree has — but three things in this plan
leaned on it and are corrected here:

1. **§A1's "single declared composition root"** was a declarative claim the layers contract would have
   enforced transitively. After this ruling only `gate.registration`'s **6 direct edges to `api`** are
   pinned. A second module that binds api handlers indirectly through the hub would pass. That is a
   weaker guarantee, and §A2's facility table — not the import guard — is what keeps it honest.
2. **S10's "exemption-count growth" guard** stays valid (7 contracts, exemption count 2 -> 1 after S3) but
   it can no longer claim to detect a *new* layering violation smuggled in via the hub. S2b covers that as
   a budget instead.
3. **§C clause 3 ("`lint-imports` green")** still means green — now green over 7 contracts, and green is not
   evidence of layering. The mutation table in S2's row is what makes "green" meaningful.

Nothing else in the plan relies on transitivity: S3, S4, S5, S6, S7, S8 are behaviour-equality slices and
their acceptance never cites the ladder; S9 is docs; S10 cites exemption count only.

**(b) REJECTED — break the hub.** It does not rescue the contract, and I measured that rather than assuming
it: after deleting **all** hub-involved edges (58 of 320 — 29 importers -> hub, hub -> 29 targets) and
granting both upward exemptions with S3 landed, the transitive ladder is **still red with 7 illegal pairs /
19 module pairs**, because the residual paths run through *top-level facility modules* that are in no layer
at all: `core.routing_caps -> debug_banner -> features.banners.kinds`,
`features.detection.semantic_judge -> dispatcher_pre -> passes.dispatcher_pre -> api.router_tools`.
Making the ladder transitively true would require layering (or exempting) the ~40 top-level facility
modules too. Cost if anyone wanted it anyway: 238 `from . import` sites in 37 files, 29 modules importing
the hub, `__init__.py:56 install_legacy_aliases` + `:111 __getattr__` as the patch surface, and
**306 monkeypatch sites in 15 test files** (`274 setattr(plugin.…` + `32 setattr("hermes_router.…`). That is
a unit with its own projections, not a config slice, and it buys a guarantee that (b) does not deliver —
the measured counterfactual is why. Not in P4.

**(c) REJECTED — keep the contract with an exhaustive ignore list.** 22 entries by greedy cover over the 167
route expansions (lower bound: 11, one per illegal pair; six of the entries are the hub's own
`hermes_router -> hermes_router.api.*` edges). A contract whose violation set is entirely exemptions is not a
contract — it is precisely the tolerance mask S1/S1b spent four rounds deleting — and it would need
S1b-style liveness proof for 22 lines instead of 2. The two exemptions we do keep carry the S1b discipline,
and the machine-checkable part is native: `unmatched_ignore_imports_alerting = error` (verified: a
nonexistent edge makes the gate exit 1) plus the liveness mutation in S2's acceptance (delete an exemption,
the contract must go BROKEN).

**The `.importlinter` template comment must carry the measured numbers.** S2 replaces the commented
`type = layers` template with this text, verbatim:

```
# NOTE (R76-P4-S2, measured by the architect at e846ae9 — re-measure, do not re-derive):
#   `type = layers` checks TRANSITIVE routes, not direct imports, and has no
#   `allow_indirect_imports` field (import-linter 2.15, contracts/layers.py).
#   Declared on this tree at e846ae9 it reports 11 illegal pairs / 45 routes:
#   hermes_router/__init__.py (the hub) imports api.* (l.44-49) and
#   gate.registration (l.54), and 29 source modules import the hub
#   (`from . import X`, 238 sites in 37 files) -> 32 distinct entry edges,
#   31 of 43 route objects hub-mediated. With BOTH true upward edges exempted
#   STILL broken (11 pairs / 38 routes). Neutralising the hub entirely
#   (all 58 hub-involved edges) still leaves 7 illegal pairs / 19 module
#   pairs through top-level facility modules. A transitive ladder is not
#   reachable by config, and not reachable by hub surgery alone.
#   The ladder therefore ships as five DIRECT-edge `forbidden` contracts with
#   `allow_indirect_imports = True` (without the flag, `forbidden` checks
#   indirect routes too and 4 of the 5 fail on the untouched tree).
```

**Dependency order was not wrong, and does not change.** F2 (S3 before S2) is rejected as a *precondition*:
S3 removes one direct edge and therefore one exemption, but it does not touch the transitive problem
(11 pairs -> 10), so ordering it first buys nothing the direct-edge guard needs. S1 -> S2 -> S3 stands.
What *does* change is S3's scope: because the passes exemption must be deleted **in the same commit** that
removes the edge (otherwise it goes stale and, by the native rule above, the whole gate exits 1), S3 now
lands two changes atomically. S2b is inserted after S2 and runs in parallel with S3..S8.

### A2. Facility owners: one producer per reading facility

Unchanged from `docs/r73-module-map.md`, restated as contracts the plan is allowed to enforce:

| Facility | Owner (sole producer) | Everything else |
|---|---|---|
| Role card / persona | `persona_card.py` — `role_card(purpose="read", messages=None, home=None)` **already exists at `:411`**; contract already met for `reflex_lane` (`:82-98`) and `decision.py` (`:1906`). Remaining work is caller completion + stripper collapse (S6) | callers only; **one** reader of `IDENTITY.md` outside `refusal_doctrine` |
| Envelope build + parse + render | `decision_extract.py` | `reflex_lane` keeps transport only — no question schema |
| Receipt / park / consume | `debug_banner.py` (`receipt(...)`, `attach_parked(...)`) | no other module assembles banner text or merges parked banners |
| Delivery contract | `reading_delivery.py` (**new, the only new module in P4**) | every lane calls `deliver(msgs, block, *, shape, rule_id, session_id)` |

The new module is justified by what it prevents, not by tidiness: the same class of bug has already
recurred four times — five RENDER-MANDATE strippers with three different behaviours, three independent
injection points, one banner text built by hand outside `format_banner` (fixed in U5, and it was on the
failure path), and fence text that two modules each believe they own. `reading_delivery` is where the
`Shape` enum, the fence bytes and the injection ladder become one object; after it, "which register does
this lane write to" is answered by a value, not by reading three call sites. No other abstraction is
introduced — see §E for the ones I refuse.

### A3. What merges, what splits, what stays

**Merges (net deletion):**
- **19** park call sites across 10 modules → `debug_banner.receipt(...)`; the receipt shape stops differing
  per site. (The r73 map said 10; the tree says 19 — see §0.)
- 2 consume/merge/settle ladders (`gate/orchestration.py:709-746`, `features/banners/lifecycle.py:269-294`)
  → `debug_banner.attach_parked(...)`.
- **3** remaining mandate-strip bodies (`persona_card.py:348` correct, `instinct_frame.py:94`,
  `completion_audit.py:2026`) → one, with the other two demoted to delegates.
- `reflex_weights`' parallel renderer/parser family (5 public symbols + 2 helpers, zero external refs at
  HEAD) → deleted; `decision_extract` is the only envelope/render home.
- The `min_gap` knob (set at `reflex_lane.py:120`, read by nothing) → deleted.

**Splits:** exactly one, and it is an extraction of a facility that already has an owner: `reading_delivery.py`
out of `reflex_lane`. **`decision.py` is not split in P4.** Its 4575 lines are a finding (A18), not a slice:
a file split with no owner boundary produces two files that drift, which is the exact failure the module map
exists to stop. It shrinks by extraction (card read, park sites, envelope) as those slices land; a structural
split needs its own mandate and its own projections.

**Deliberately stays as-is** (with the reason, because a future unit will want to "clean" these):
- Card content and byte budgets (`persona_card.py:30` `PERSONA_MAX_CHARS=6000`, `:295` truncation, `:348`
  stripper, `:243-293` strip rules) — tuned against live refusal evidence. Only the stripping helper moves;
  not one character of the card changes.
- RENDER MANDATE wording — byte-identical, stripped per purpose.
- `agree → stand-down` (`reflex_lane.py:1106-1118`, `agree_silent`) and fire-on-any-midturn (the
  empty-option-set stand-down at `:1096-1098`) — deliberate training properties; a "fix" here silently
  un-teaches the lane.
- Banner render shape, lane labels, `MAX_BANNER_CHARS 700` (`debug_banner.py:55`), `_END_LABELS` (`:195`),
  `$0.0000*` unpriced convention (`:161-166` region) — user-facing and reviewed.
- Park policy as data (`features/banners/kinds.py`, `lifecycle.py`) — one-delivery semaphore, drop-not-repark.
- `_park_task_id` lane scoping (`decision.py:909`) — without it the frontier anchor overwrites the decision
  segment on co-fire (`:957`, `:4142`).
- Echo guard and canonical rewrite semantics; `frames.DECISION_RULE` (`frames.py:305`) and its marker id
  (`:303-304`).
- **The two rendering registers** (A4 distribution on the every-call lane vs collapsed route on the legacy
  midturn path). We run both on purpose: the menu caused a live failure at R37-3. Unifying them is a
  product decision, not a refactor — §E.

---

## B. TASK TABLE

Every slice is **QUEUED**. One slice = one commit. `state` moves QUEUED → DISPATCHED → IN-FLIGHT → REVIEW →
GREEN → SHIPPED; BLOCKED/DROPPED exist and must be used rather than left stale.

| name | owner agent | scope (one line) | depends-on | acceptance (how it is proven) | state |
|---|---|---|---|---|---|
| R76-P4-S1-guard-baseline-truth | coder | Repair `.importlinter` `ignore_imports` syntax to `module -> module`, delete entries that match no live edge, add `scripts/baseline_check.py` + pinned `tests/baseline_failures.txt`; triage the 10 red tests into `docs/r76-p4-baseline-triage.md` | — | `lint-imports` exits 0 with both contracts reporting "kept"; `baseline_check.py` reproduces the pinned failure set from a fresh junit run; triage doc names every red test with a class (env / real / accepted-debt) | QUEUED |
| R76-P4-S2-layering-contracts-direct | coder | Replace the commented `layers` template in `.importlinter` with the measured-fact comment (A1-R) and add **five direct-edge `forbidden` contracts** (gate>api, passes>api, features>{api,gate,passes}, lanes>{api,gate,passes,features}, core>{api,gate,passes,features,lanes}), each with `allow_indirect_imports = True`, plus exactly **two** exemptions: wildcard `gate.registration -> hermes_router.api.*` and `passes.dispatcher_pre -> hermes_router.api.router_tools` | S1 | `PYTHONPATH=/opt/data/plugins lint-imports` (cwd = repo root) exits 0 and prints `Contracts: 7 kept, 0 broken.` with exactly 7 KEPT lines; `git diff --name-only` = `.importlinter` only; grimp edge-set hash unchanged (`grimp.build_graph("hermes_router")`, internal edges `a -> b` sorted, sha256 = `cd7687b4b351efc9563f92078c997d2cc4d4132f00581ecafa0a6422d1d5a2b9`, 92 modules / 320 edges); **mutation that must BREAK it:** create `hermes_router/features/_mutation_probe.py` containing `from ..api.router_tools import register` -> exit 1, `features may not import api/gate/passes (direct) BROKEN`; **exemption-liveness:** delete the passes exemption -> exit 1 with `passes ... BROKEN`; delete the gate wildcard -> exit 1 with 11 pairs; **stale exemption:** add any ignore naming a non-existent edge -> exit 1 `No matches for ignored import` | QUEUED |
| R76-P4-S2b-hub-reachthrough-ratchet | coder | One guard test (`tests/import_guard/test_hub_reachthrough_budget.py`) that pins the hub surface as a **non-increasing budget**: 238 `from . import` sites in 37 source files, and 29 source modules with an edge to `hermes_router` — because the direct-edge ladder cannot see reach-through | S2 | both integers reproduce from a grimp build + an AST/grep census in the same test; adding one `from . import X` line to any facility module (or one new hub import) turns the test red with the two numbers in the message; the file touches no `hermes_router/*.py`; failure set unchanged | QUEUED |
| R76-P4-S3-pass-spine-decouple | coder | Remove the `passes.dispatcher_pre → api.router_tools` edge (call-time resolution owned by the gate, or the helper moves down to its owner) so `passes` needs no exemption | S2 | grimp: zero `passes → api` edges; driven pre-mode turn produces the same emitted payload hash; `tests/` for dispatcher_pre unmodified | QUEUED |
| R76-P4-S4-reflex-weights-dead-body | coder | Delete `reflex_weights.{parse,render,build_request,extract,summarise,_as_prob,_clip}` and the `min_gap` setdefault; keep `_note`/`cfg_for` | S1 | pre/post grep census: the seven symbols have 0 references outside their definition, and `_rw.` uses only `_note`/`cfg_for`; failure set unchanged; module imports clean | QUEUED |
| R76-P4-S6-card-caller-completion | coder | **Finish** the landed accessor: route the last callers through `role_card` and collapse the two remaining stripper bodies to delegates | S1 | pre/post string equality for each migrated site (`completion_audit._persona_tailoring`, `anchor_exec:1620`, `instinct_frame.who_block`, `dispatcher_knobs:45`); mandate-first card keeps `=== WHO ===`; AST scan: one stripper body, one IDENTITY reader outside `persona_card`/`refusal_doctrine` | QUEUED |
| R76-P4-S7-delivery-seam | coder | Extract `reading_delivery.py` (Shape enum, fence bytes, contract re-export, `deliver`); `reflex_lane` delegates, **no call-site behaviour change** | S1, S4 | `tests/decision_lane/test_r73_p0_echo_leak.py` passes **unmodified**; `reading_delivery._FENCE_HEAD is reflex_lane._FENCE_HEAD`; 3-turn message-stream hash identical; `lint-imports` green | QUEUED |
| R76-P4-S5-receipt-one-entry | coder | Add `debug_banner.receipt(...)` (generalise `reflex_lane._receipt`, HEAD `:936`); convert the **19** park sites at HEAD (`decision.py:956/4141`, `decision_midturn:1327`, `completion_audit:2137/2807/2884`, `gate/orchestration:517/848/1118/1223/2183/2354`, `route_gate:1651`, `anchor_exec:1002/1048/1693`, `decision_miner:671`, `reflex_lane:993`, `core/routing_caps:417`) | S1 | per-site golden: parked banner text byte-identical to captured before-state; `test_r73_p1_receipt.py` unmodified; park probe: 3 lanes → 3 parks, 3 distinct lane labels, `task_id` lane-prefixed | QUEUED |
| R76-P4-S8-consume-one-path | coder | Add `debug_banner.attach_parked(...)`; both merge/landed/settle ladders call it; ordering invariant unchanged | S5 | `test_r9_banner_delivery_seam`, `test_r19_16_banner_stack`, `test_r33f_banner_semaphore`, `test_r34_item2_deferred_banner_delivery` unmodified; driven turn: park→consume→append→settle log-line sequence hash identical | QUEUED |
| R76-P4-S9-doc-truth | architect | Docs only: A11/A13/A14/A15/A16 + A17 docstring + A12 version truth (declare `plugin.yaml` the single source and say so at the top of `CHANGELOG.md`) | — | diff touches `*.md` and exactly one docstring line; the test that pins the manifest version string still passes; suite failure set unchanged | QUEUED |
| R76-P4-S10-owner-locks | coder | AST/grimp guard tests that make each owner single (second fence, second park builder, second IDENTITY reader, second envelope schema, exemption-count growth) + `docs/architecture.md` module table | S4, S5, S6, S7, S8 | each guard fails against a mutation that reintroduces its defect (recorded per guard), passes on HEAD; `scripts/docs_drift_check.py` green | QUEUED |

Ten slices. No slice touches a lane's emitted text without its own golden (§C).

---

## C. SEQUENCING

### The rule, bound in

1. One slice = one commit, one reviewer verdict, no batching.
2. **A slice that would change emitted behaviour is split until it does not.** "Emitted behaviour" = bytes
   the model receives, bytes the user sees, rows written with new fields, or a lane's decision to fire.
3. A slice lands GREEN only when (a) its own acceptance evidence is in the review dir, (b) the pytest
   failure set is **equal to the pinned baseline** (not "0 failed" — the baseline is red, §0), and
   (c) `lint-imports` is green (true from S1 onward; before S1 it is red for a config reason, which S1 fixes).
4. No file moves before an equivalent behaviour exists. The only new file is `reading_delivery.py`, and it
   starts as a delegate whose output is proven equal before any caller is switched to it.
5. Cheap verification is the default: targeted test dirs during development; one full run (276s measured)
   before commit; the fleet battery is **never** required for a slice — see the driven probe column in §D.

### Order

```
S1 ──▶ S2 ──▶ S3        (S2 = 7 contracts / 2 exemptions; S3 removes one edge AND its exemption)
       └──▶ S2b          (ratchet on the hub surface; parallel with S3..S8)
 ├──▶ S4 ──┬──▶ S7 ──┐
 ├──▶ S6 ──┘         ├──▶ S10
 └──▶ S5 ──▶ S8 ─────┘

S9 : independent, parallel-eligible at any point
```

Rationale for the order: S1 first because every later acceptance leans on a working guard and an honest
baseline. S2/S3 next because they convert the layout from a convention into a checked contract at zero
runtime risk — S2 as five **direct-edge** contracts (the transitive ladder is unreachable, §A1-R), S3 to
remove the second exemption and make the guard stricter. S2b rides alongside: it pins the hub debt the
ladder cannot see, as a budget that may only shrink. S4 is pure deletion. S6 and S7 are read-path
extractions with byte-equality proofs. S5 and S8 are the delivery cluster and go last-but-one because they
carry the fleet risk (§D). S10 locks the shape once the owners are real. S9 is docs and can ride alongside
anything.

Two ordering traps made explicit after the S2 ruling:

1. **S3 is atomic**: removing `passes.dispatcher_pre -> api.router_tools` without deleting its exemption line
   makes the gate exit 1 (`No matches for ignored import`) — a stale exemption is a hard error by design.
2. **S2 must not be "fixed" by widening an exemption.** The only two legal entries are the ones named in
   S2's row; the gate-agnostic escape (a facility reaching an upper layer through the hub) is S2b's budget,
   and growing that budget is a reviewed decision, never a side effect of another slice.

### Per-slice: what "nothing observable changed" is proven BY

| Slice | Proof of neutrality |
|---|---|
| S1 | `git diff --name-only` = `.importlinter`, `scripts/`, `tests/`, `docs/` only; zero `hermes_router/*.py` touched; lint-imports contract report before/after on the same graph; junit failure-set equality |
| S2 | grimp edge-set hash (`cd7687b4…`, 92 modules / 320 internal edges) identical before/after; `.importlinter` is the only file that changes; four mutations each flip it red (new `features -> api` import; delete the passes exemption; delete the gate wildcard; add a stale exemption) |
| S2b | the two pinned integers (238 `from . import` sites / 37 files, 29 hub importers) recomputed from the tree inside the test; one added `from . import X` turns it red |
| S3 | grimp: `passes → api` edge count 1 → 0; a unit test that patches `api.router_tools` and observes `dispatcher_pre` still calls the patched object; driven pre-mode turn payload hash unchanged |
| S4 | grep census proving 0 external references **before** the deletion (so deletion cannot change behaviour); suite failure-set equality; `python -c "import hermes_router.reflex_weights"` |
| S6 | string equality: `role_card(purpose="render") == build_persona_context()` on 3 message shapes; read-strip invariants; AST scan for IDENTITY readers = 1 |
| S7 | `_FENCE_HEAD`/`_FENCE_TAIL` identity between modules; R73-P0 echo-leak test unmodified; 3-turn message-stream hash identical |
| S5 | per-site golden capture of `format_banner` output before the change, byte-compared after; park count/label probe; `test_r73_p1_receipt.py` unmodified |
| S8 | the four banner-lifecycle tests unmodified; park→consume→append→settle log-line sequence hash from a driven turn |
| S9 | diff is documentation (+1 docstring line); version-pinning test still passes |
| S10 | guards are proven by mutation (each defect reintroduced → its guard fails), and pass on HEAD |

Behaviour-neutral means the **golden capture is taken before the slice starts** and committed into the
slice's review dir. A capture taken after the change proves nothing.

---

## D. RISK REGISTER

| Slice | Regression class | Why this one | Gate it needs |
|---|---|---|---|
| **S5** (receipt) | **banner/delivery** — highest | 19 park sites across 10 modules — every lane, both advisory paths, and the failure paths; changes the code that decides what a delivered reading looks like. Two live invariants meet here: park-before-consume, and lane-prefixed park keys (`decision.py:909`) without which the frontier anchor overwrites the decision segment on a co-fire. Failure is silent: a wrong or missing banner, not an exception. | **Pins:** `test_r73_p1_receipt.py`, `tests/banners/*` (with the one-delivery semaphore and drop-not-repark rules) unmodified. **Driven probe:** one canary session, three turns (shadow render, frontier anchor, decision advisory), monkeypatched `park_anchor_banner` → exactly 3 parks, 3 lane labels, lane-prefixed task ids, banner text byte-identical to the pre-slice golden. **Live check:** 1h of canary traffic — banner count per lane in the decisions db within ±0 of the same-hour baseline; zero `banner` lines in the error log. |
| **S8** (consume) | banner/delivery — ordering | The merge/landed/settle ladder is where four incidents happened (R9d, R19.11, R33f-A, R34-2). A mistake here double-delivers or drops a banner. | **Pins:** the four lifecycle tests unmodified. **Driven probe:** log-line sequence hash for park→consume→append→settle on one driven turn per lane; assert exactly one `append_banner` reference outside `debug_banner` after S8. **Live check:** same 1h banner-count check as S5, plus zero `deferred banner` re-parks. |
| **S7** (delivery seam) | **config resolution** + advisory arbitration | `shape` is read from config (`reflex_weights.shape`, whitelist `reflex_weights.py:103-105`); moving the enum into `reading_delivery` is exactly where a config key silently stops resolving (the A1 class of bug — a stale import or a renamed accessor fails *open*, i.e. silently). | **Pins:** `test_r72_behavioural_integration.py` (all 5 shape cases incl. the `content=None` carrier and agreement stand-down) unmodified. **Driven probe:** set `shape` to each of the 4 legal values in a canary profile and assert the block lands at the matching injection point (4 turns); a fifth turn with an illegal value asserts the documented fallback. **Live check:** one canary hour with `shape: evidence` and one with `system` — the block appears in exactly one place per turn, never twice. |
| **S3** (pass spine) | advisory pre-mode | `dispatcher_pre` is a live pass; if the router_tools helper is resolved differently (e.g. not patched by tests, resolved late against a different module object) the pre-mode output changes without an exception. | **Pins:** dispatcher_pre tests unmodified. **Driven probe:** one driven pre-mode turn, emitted payload hash identical; a spy proves the patched `api.router_tools` object is still the one called. |
| **S6** (card) | persona/read path | The card is behaviour, not formatting — it was tuned against live refusal evidence. A truncation-band slip is invisible in a green suite. | **Pins:** `test_r72i_agent_card.py`, `test_r34_19g_higher_self_acts.py` unmodified; char-band assertions (read 1150-1400, render byte-equal). |
| **S1/S2/S2b/S4/S9/S10** | guard/documentation | No runtime surface. S2 is config-only; S2b is one test file. The risk is *epistemic*, not runtime: a green gate that is read as proof of layering when it only proves 7 direct edges (§A1-R). | Failure-set equality + the four mutations named in S2's row, re-run by the reviewer from the review dir. |

Two standing checks for every slice, both cheap: `git diff --stat` must match the slice's declared file
set, and the reviewer re-runs the slice's own probe from its review dir (a verifier that cannot be re-run is
decoration).

---

## E. DELIBERATELY EXCLUDED — and why

**From the memo's SCOPE-CHANGE register (named, not folded in):**

1. **A8 — refusal replacement / delivery authorization.** Changes lane behaviour and the mute rate; needs
   an operator decision against a deliberate, documented posture (`SECURITY.md:36`,
   `docs/SAFETY.md:3-6`). Adopting it inside a structural commit would hide a behaviour change under a
   module-boundary message. **Excluded.**
2. **B6 — Shadow behaviour contract** (authorization, consent, egress telemetry, "bypass never a default").
   Same class, with a cost line attached. **Excluded.**

**Other LIVE rows I am not touching in P4, and why:**

| Excluded | Why |
|---|---|
| A3 / C3 (`stand_down` as a criterion) | It changes the request the model receives → not behaviour-neutral. It is also a measurement fix, not a structural one. **Highest-priority follow-on unit (R77); the plan does not touch it.** |
| A4 / A5 (two rendering registers) | A product decision about which register wins per surface; picking one either resurrects the R37-3 delivery failure or deletes the distribution Phase 4 wants to measure. Needs a ruling, not a refactor. |
| A10 / C2 (neutral tie names `operator`) | Changes emitted stance on a live path. One-line fix, wrong commit class for P4. |
| A6 / A9 (typed POV/posture sets) | New types with no measured incident behind them since R53/R56. **Wan abstraction with no measured payoff is a finding against the plan, not a virtue** — revisit when a third producer of scope/rigour appears. |
| A7 (calibration is association) | Needs a no-lane control arm; that is an experiment, not a structure change. |
| A19 (swallow baseline drifted) | Re-baselining without per-file reading would launder the audit. Needs its own unit with named per-file verdicts; P4 only makes sure it does not silently grow (§C clause 3 pins the failure set, not the except count). |
| A20 / B4 / B5 (request/step identity, idempotent ledger) | Adds fields to emitted ledger rows and a new identity contract → observable change, and it is a feature with a rollout, not a refactor. |
| B1 (greenfield `adapters/domain/infra`) | Rejected. We have our own layered tree and an import-linter guard; moving 92 modules into a new vocabulary buys nothing measurable and forfeits every byte-equality proof §C depends on. |
| B2 (a single advisory arbiter module) | Only the *delivery* half has measured payoff and that is `reading_delivery` (S7). A module that "owns the one advisory write" has no incident behind it — the banner failures were ordering, not authority. Not built. |
| B11 (pyproject, mypy/pyright, CodeQL, SLSA) | Packaging/CI lane, not plugin structure. Real gaps (A12 is one symptom) but they belong to a release-infrastructure unit. |
| A18 (split `decision.py`) | A file split without an owner boundary produces two files that drift — the exact failure the module map exists to stop. P4 shrinks it by extraction; splitting needs its own mandate and projections. |
| Already landed (§0) | The six dead-code items closed by the fix phase must **not** appear as slices. A plan that re-proposes solved work is worse than no plan. |

---

## F. What this plan does not claim

- It does not claim the suite is green. It is not (8F/2E at HEAD, measured). S1 makes that fact a pinned,
  visible baseline instead of folklore; the red tests need triage, and a slice cannot be GREEN over an
  undiagnosed real failure.
- It does not claim the import guard works today. It does not (exit 1 for a config-syntax reason). S1/S2 fix
  that before anything leans on it.
- It does not claim the external package was wrong about everything. Its live findings are already recorded
  row-by-row in `docs/r76-pkg-reconciliation.md`; this plan takes only the work that is ours, behaviour-neutral,
  and provable slice by slice.
