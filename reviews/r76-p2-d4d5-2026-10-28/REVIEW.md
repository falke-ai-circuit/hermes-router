# R76-P2-D4 / D5 — retroactive verdict on two unreviewed removal commits

| Field | Value |
|---|---|
| Repo | `/opt/data/plugins/hermes_router` |
| Reviewed state | `5859e65` ("DECISION-1 (6.13.24): version bump…"), reviewed from a **clean worktree** at that sha |
| Units | `f1718d6` P2-D4 (suggestions.py) · `d30bc67` P2-D5 (route_gate.py) |
| Author of record | conductor `<conductor@falke.local>`, 2026-10-28 05:33:53Z / 06:01:46Z |
| Gate violation | both commits shipped with no prior review (fleet deploy blocked on a dirty tree; the deletion was committed to clean it) |
| Verdict D4 | **PASS** |
| Verdict D5 | **PASS** |
| Reviewer | reviewer profile, 2026-10-28 |

Scope note: this review judges the **deletion** (reference census + inertness), not the
decision to commit ahead of review. The gate violation itself is recorded above and
belongs to the process (see §8 F5).

---

## 1. Request, framed

The ask is a *fulfilment* check on a "dead code" claim, not a re-run of the work:

1. every deleted symbol is unreferenced **at HEAD**, including the forms a plain grep
   misses (import re-binding, `getattr`, `__all__`, string-keyed dispatch, prose, fixtures);
2. the deletion is **inert** — the paths that used to write/read this state produce
   byte-identical observable output at the parent and at HEAD;
3. persistence consequences are stated explicitly (a reader that now always sees empty is
   a silent behaviour change; nothing at all is not);
4. the suite still matches its pinned baseline, `lint-imports` contracts still hold;
5. each commit's scope is exactly the module that held the dead symbols.

Recipient of this artifact: the conductor (deploy gate owner) and whoever owns the P2
dead-code sweep. They need to know: **can the fleet deploy on top of these two commits
without the removals being re-litigated?** Answer: yes for both, with three residuals that
are named in §7 and none of which is a runtime path.

## 2. Reference census at HEAD (5859e65)

Method: `grep -rIn` over **every file type** in the tree (`.py .md .json .yaml .yml .toml
.cfg .ini .txt .sh .xml`), `.git` and `__pycache__` excluded — so prose, docs, junit
payloads and review artefacts are inside the sweep. Cross-checked by an AST walk that
counts `ast.Name`/`ast.Attribute` nodes only (a `def` is a `FunctionDef`, not a reference,
so the count is references-not-definitions). Raw output: `census/census_head.txt`.

| Symbol | Commit | Refs at HEAD | Refs at its parent | Reference-bearing forms checked |
|---|---|---|---|---|
| `mark_turn_pending` | f1718d6 | **0** | 0 (AST) | code, prose, string literal, `getattr`, `__all__`, star-import |
| `pending_task_for` | f1718d6 | **0** | 0 (AST) | idem |
| `record_sugg_skipped` | f1718d6 | **0** | 0 (AST) | idem |
| `gate_delivered_count` | f1718d6 | **0** | 0 (AST) | idem |
| `_TURN_CLAIMS` | d30bc67 | **0** | 1 — its own `AnnAssign` target, `route_gate.py:870` | idem |
| `record_turn_claim` | d30bc67 | **0** | 0 (AST) | idem |

Census integrity checks (each one is a form that would hide a live reference):

| Check | Result |
|---|---|
| quoted literal `"<name>"` / `'<name>'` (string-keyed dispatch) | 0 for all six |
| `__all__` in `suggestions.py` / `route_gate.py` | 0 / 0 — no export list to prune |
| `import *` anywhere in tree | 0 — no star-import surface |
| `getattr(<module>, "<name>")` in the two modules | 3 sites (`route_gate.py:406,415,1930`); none names a deleted symbol — 406/415 resolve a *pass* function by knob name, 1930 reads `decision.model_override` |
| `from .x import <name>` / `from .. import <name>` | 0 (substring sweep covers the import line) |
| conftest / fixtures / test data | 0 (tree-wide sweep includes `tests/` and `tests/swallow_baseline.json`) |
| junit payloads under `reviews/` | 0 (the `claim_state` / `register_declared` hits there are the *live* D5 registry, not the deleted symbols) |
| 12 profile copies of the plugin (`/opt/data/profiles/*/plugins/hermes_router`) | 6 hits in **1** stale copy only (`profiles/recovery`), all six are the **definitions** themselves; 0 references. The other 11 copies have 0 hits |
| prose that still names a symbol | **none** — the symbol names appear nowhere in `docs/`, `CHANGELOG*.md`, or `reviews/` |

**Reading:** the census claim in both commit subjects reproduces exactly. D4's subject says
"0 refs outside their own module" — true for all four functions it actually deleted (see
F1). D5's says "0 refs in code or tests" — true.

## 3. Inertness — driving the paths at parent and HEAD

Two worktrees were cut at the parents (`f1718d6^` = `99c995b`, `d30bc67^` = `f1718d6`) and
one at HEAD, each laid out as `<dir>/hermes_router` so the suite's own conftest resolves the
package. `probes/probe_d45.py` runs the identical drive in all three trees.

Drive 1 — **budget ledger** (the D4 surface): `record_sugg_fired` ×3 → `record_sugg_delivered`
×3 → `would_spend` → `commit_turn` → `committed_count` → replay-from-disk in a cleared
process state → `would_spend` again. The ledger file is read back, `ts` stripped from every
row, and hashed.

Drive 2 — **turn-claim registry** (the D5 surface): `register_declared` → `claim_state` →
`stamp_turn_claim` → `mark_turn_claim_executed` → `mark_shadow_rendered` → the
`record_turn_claim` body path → `clear_turn_claims(sid)` → `claim_state` per session.
Records are normalised (`ts` dropped) and compared field-for-field.

Result (`logs/probe_nd2_*.json`; `NO_DIRECT=1` keeps the probe from *calling* the deleted
symbols, so what is compared is the path, not the capability):

```
=== d4 block: d4parent vs HEAD ===
IDENTICAL
=== d5 block: d5parent vs HEAD ===
IDENTICAL
```

The ledger digest of the driven sequence is equal at both ends:
`ledger_rows=7`, `ledger_sha256_ts_stripped=ad315230b678897be9113809566d565bdd11f678d919a4c56bb5c118fa3600dc`,
events `sugg_fired,sugg_fired,sugg_fired,sugg_delivered,sugg_delivered,sugg_delivered,turn_committed`,
and the same post-commit budget answers (`would_spend_after_commit=(False,'budget_exhausted')`,
`committed_count_after_replay=3`, `would_spend_after_replay=(False,'budget_exhausted')`).

The claim-state snapshot is equal at both ends, including the parts that carry banner
consequences (`executed` and `rendered` flags, lane and source):

```
claim_after_register   {lane:shadow, source:declared_user}
claim_after_stamp      {lane:shadow, source:declared_user}
claim_after_executed   {lane:shadow, source:declared_user}
claim_after_mark_rendered {lane:shadow, rendered:true, source:declared_user}
equiv_via_record_turn_claim {lane:decision, source:auto_lane}   # == register_declared(...)
turn_claim_registry_size 1   declared_claims_size 2
```

Positive control (the probe *is* sensitive — it must show a difference when a deleted
symbol is called): without `NO_DIRECT`, the D4 drive at the parent writes one extra
`budget.jsonl` row (`sugg_skipped`) and returns `pending_lookup="task-1"`; at HEAD those
calls do not exist. That is the difference between *the symbol* and *the path*: the symbol
carried the capability, the path has no caller for it. Raw: `logs/probe_d4parent.json` vs
`logs/probe_head.json`.

Targeted suites, same three trees, same command
(`logs/targeted_tests_*.txt`) — `test_single_claim_point.py`, `test_route_gate.py`,
`test_v360_budget_ledger.py`:

| Tree | Result |
|---|---|
| `t_d4parent` (99c995b) | `40 passed in 3.47s` |
| `t_d5parent` (f1718d6) | `40 passed in 3.63s` |
| `t_head` (5859e65) | `40 passed in 4.31s` |

**Conclusion:** nothing observable changes. The removal is inert because the code was
already unreachable at its parents, not because a reader was silenced.

## 4. Persistence / schema consequences

| Store | Existed only for the deleted symbols? | After removal |
|---|---|---|
| `budget.jsonl` rows of `event=sugg_skipped` | **The writer did** (`record_sugg_skipped`). The event class is declared in the module docstring (l.18) and still folded by `_apply_replay` (l.179). | **No schema change, no migration.** Going forward the event simply cannot be produced by this module (no other writer in tree or in the 12 profile copies). The replay branch becomes a **reader for a record class with no producer** — harmless: historical rows keep parsing into `_state[tid]["skipped"]`, and nothing consumes that set except `task_summary()`'s `"skipped"` key, which therefore now always reads `[]` **as it already did at the parent** (0 callers of the writer at `f1718d6^`). Not a silent behaviour change: the value did not move. |
| `_pending_turns` (in-memory dict) | the two deleted functions were its only writer *and* its only reader | Dict remains declared at `suggestions.py:70` and cleared at `:388`; with no writer and no reader it is permanently empty. **Nothing persisted**, so nothing to migrate; `clear_for_tests()` still works. Dead state, zero runtime effect — see F2. |
| `_TURN_CLAIMS` (in-memory dict, `route_gate.py:870`) | yes — declared and never read or written by anything, including its own module | Removed. It was **not** a cache key or a table: the live single-claim registry is the *separate* `_TURN_CLAIMED` (l.903), which is untouched (still 5 write sites, still read by `_peek_turn_claim`/`mark_shadow_rendered`). No store, no serialization, no session state. |
| Any on-disk table/column | none of the six symbols touched one | — |

## 5. Suite and guards at HEAD

`python3 scripts/baseline_check.py --junit …` on the clean `5859e65` worktree
(`logs/baseline2.log`), full run:

```
7 failed, 2536 passed, 2 skipped, 1 deselected, 1 xfailed, 2 warnings, 2 errors in 452.97s (0:07:32)
baseline_check: pytest exit=1

baseline_check: pinned required=9 optional=1
baseline_check: actual failures/errors=9
baseline_check: variant = 9 red (no optional members red)
  optional member green this run: tests/decision_lane/test_r53_midturn_mindset.py::test_m1_envelope_carries_the_current_stance

baseline_check: NOTE — every optional entry was green this run; the optional class is doing no work. Re-check the triage doc before removing the entry.

baseline_check: OK — failure set equals the pinned baseline
EXIT=0
```

So: **exit 0**, failure set equal to the pinned set, and the red set is the *pre-existing*
pin (`test_p05_legacy_purge_guard`, `test_p4_schema_budgets` ×2, `test_r33m_refusal_semantics` ×2,
`test_rider20_fixes` ×2, `test_rider10_fixes` ×2 errors) — none of which names a symbol in
either commit. Note the canonicalisation of the naming: "7 failed … 2 errors" is 9
failures/errors, which is exactly the pinned `required=9`.

`lint-imports` (`logs/lint_*.txt`), repo root, `PYTHONPATH` to the worktree parent:

| Tree | Contracts |
|---|---|
| HEAD 5859e65 | `Contracts: 8 kept, 0 broken.` (exit 0) |
| parent 99c995b | `Contracts: 8 kept, 0 broken.` (exit 0) |
| parent f1718d6 | `Contracts: 8 kept, 0 broken.` (exit 0) |

The count in the brief ("7 kept") is one slice stale: post-S2 the ladder is **8** contracts
(the S2b root-package contract makes eight). No contract moved across either deletion —
identical on both sides, so the guard contracts are undisturbed.

Also re-run at HEAD: `scripts/docs_drift_check.py` → `docs-drift: OK — every referenced module exists` (exit 0);
`scripts/swallow_audit.py` → exit 0 (baseline-relative, unchanged by these commits).

## 6. Scope per commit

| Commit | Files | Lines | In-module? | Test deletions? | Reformat? |
|---|---|---|---|---|---|
| f1718d6 | `suggestions.py` only | 55 deletions, **0 insertions** | yes — both named symbols and the two unnamed ones live in this module | none (0 test files in the commit) | none — pure deletion hunks, no touched line outside the removed bodies |
| d30bc67 | `route_gate.py` only | 11 deletions, **0 insertions** | yes — the vestigial dict and its one-line wrapper | none | none; the comment block that preceded the dict is left intact and still reads true of `_TURN_CLAIMED` |

No unrelated file, no test edit, no version bump, no `__init__` change. Scope holds.

## 7. Findings (all non-blocking)

**F1 — D4's subject under-describes its own diff (M2, documentation).** The subject names
`mark_turn_pending / pending_task_for`; the commit actually deletes **four** functions:
those two plus `record_sugg_skipped` and `gate_delivered_count` (both also 0-ref, both also
in `suggestions.py`). The deletion is correct; the message is not a complete description of
what a bisect reader will find. Consequence: a future "why did the skip event stop being
written?" investigation lands on a commit whose subject never mentions it.

**F2 — the pending-abandon / interrupted-turn rule is now prose only (M2, doc drift,
pre-existing).** `suggestions.py:67-69` still documents `_pending_turns` as
"turn identity -> task_id for pending-abandon resolution. The interrupted-turn rule keys on
the NEXT dispatch…", and the module docstring (l.11) still asserts "the interrupted-turn
rule must survive restarts". No code implements that rule at HEAD **or at the parent** —
`mark_turn_pending`/`pending_task_for` had 0 callers before deletion. The commit did not
create the gap, but it is the moment the last artefact of the promise became a dangling
dict with a comment. Fix is prose: delete `_pending_turns` (l.70, l.388) and the two
sentences, or implement the rule in the phase that owns it.

**F3 — `sugg_skipped` is a documented, readable event with no producer (M3).** Docstring
l.18 and `_apply_replay` l.179 remain; the only writer was deleted. Inert (§4), but the
module now advertises an event it cannot emit. One-line cleanup: drop l.18 and the l.179
branch, or restore `record_sugg_skipped` when the Phase 1 gate that emits it lands.

**F4 — D5 leaves a comment describing state it no longer adjoins (M3).** The block at
`route_gate.py:865-869` ("declared claims write here at registration; auto claims at claim
time") now sits directly above the `claim_state()` accessor instead of above the dict it
describes (`_TURN_CLAIMED`, l.892-903, ~25 lines below). The sentence is still true of
`_TURN_CLAIMED`; it is misplaced, not wrong.

**F5 — the tree was not clean when this review started (M1 for the deploy gate, out of the
reviewed units).** The brief says "tree clean except two untracked review dirs"; actual
`git status` at start: four **modified** tracked files — `api/commands.py` (−27),
`api/commands_config.py` (−32), `router.py` (−6), `semantic_classifier.py` (−5), 70
deletions of further dead code (`_peek_confirmation`, `_chain_entry_mut`,
`handle_router_command_async`, `_endpoint_cfg` ×2) — plus the two untracked review dirs.
Those modifications are **not** in either reviewed commit and were not made by me; I left
them uncommitted and untouched, and ran every check on clean worktrees so the evidence is
not contaminated. Whoever owns them still owes the deploy a decision on that tree.

Addendum (same session, after the artefact commit): a **fifth** tracked file turned dirty
mid-review — `tests/test_import_guard_liveness.py`, +67/−16 (a `_copy_tree` binding guard
added to the liveness test). It was clean at session start and is not mine; it points at a
concurrent lane writing into this worktree while the deploy gate is being cleared. The tree
is therefore *actively* dirty, not merely stale-dirty. None of the evidence above depends on
it — every check ran from a detached clean worktree at the reviewed shas — and the two
review-artefact commits touch `reviews/` paths only.

## 8. Evidence index

| File | Content |
|---|---|
| `census/census_head.txt` | raw tree-wide + literal-form + star-import + `__all__` + `getattr` census at 5859e65 |
| `probes/probe_d45.py` | the two drives; `NO_DIRECT=1` compares paths without calling deleted symbols |
| `logs/probe_nd2_{d4parent,d5parent,head}.json` | the compared snapshots (both blocks IDENTICAL) |
| `logs/probe_{d4parent,d5parent,head}.json` | positive control — direct calls, showing the capability difference |
| `logs/baseline2.log` | full pinned-baseline run at HEAD, verbatim summary |
| `logs/junit_head.xml` | junit payload for that run |
| `logs/lint_{head,d4parent,d5parent}.txt` | `8 kept, 0 broken` on all three trees |
| `logs/targeted_tests_{head,d4parent,d5parent}.txt` | 40 passed on all three trees |

Evidence classes: census and scope are E2 (re-derived here); inertness and suite/counts are
E1 (driven and observed directly); `lint-imports` output is E3 (third-party tool executed
here). No load-bearing claim in this review rests on E4.
