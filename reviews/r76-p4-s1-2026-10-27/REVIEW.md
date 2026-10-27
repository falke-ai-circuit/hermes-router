# R76-P4-S1 — guard tells the truth, baseline pinned

**Commit reviewed:** `0e4ce3a16615fa70ee4bf1622a6d33dd57ee81cb` (parent `756e8ed`)
**Repo:** `/opt/data/plugins/hermes_router` · tree clean at review time
**Verdict: FIX-FIRST** — the guard repairs and the pin mechanism work as claimed; the
`optional` class and two of the slice's own documented claims do not.

Reviewed by: reviewer (verifying role). Recipient modelled: the next slice's author /
deploy gate running `lint-imports` and `scripts/baseline_check.py` and trusting their output.

---

## Findings

| # | Sev | Finding | Evidence |
|---|---|---|---|
| **F1** | **blocking (design)** | The `optional` class accepts **any** red at the pinned node id without looking at *why* it is red. A real regression in `test_m1` (e.g. the envelope stops carrying `mindset` at all, or key set changes) is absorbed exactly like the state-dependent failure. Colour-only acceptance = the class the brief calls blocking. | Probe D in `probes/pin_and_optional_battery.txt`: baseline `optional …::test_opt` + run where `test_opt` fails → **EXIT 0**. |
| **F2** | **blocking (guard lies)** | An `optional` node id that **never ran** (typo, renamed test, deselected, file removed) is reported as `optional member green this run` and the gate exits **0**. The module docstring (`baseline_check.py:35-38`) promises the opposite: "A permanently green optional entry is stale and this script reports it as drift, because an exemption that matches nothing is a lie about the code". The code comment at `:227-231` says the reverse of the docstring ("Not a failure"). This is the exact defect class S1 exists to end — an exemption matching nothing, accepted. | Probe C: `optional tests/…::test_never_exists_anywhere` → `optional member green this run` → **EXIT 0** (`probes/pin_and_optional_battery.txt`). |
| **F3** | blocking (doc/code drift) | `--show-variant` is documented in the module docstring (`:14`) and does not exist; argparse rejects it. A documented interface that errors out on use. | Probe G: `--show-variant` → `error: unrecognized arguments` → **EXIT 2**. |
| **F4** | blocking (claim fails repro) | `tests/baseline_failures.txt:31-32` and `docs/r76-p4-baseline-triage.md:49-56` both state `test_m1` is *"Red here on both invocations (standalone module run AND full suite)"* and that order-dependence *"did not reproduce here"*. At this commit the full suite reports `7 failed … 2 errors` with **`test_m1` PASSING**, while the standalone module run still fails. The test **is** order/state-dependent — the strongest support for keeping the class, and simultaneously a direct falsification of the pin's own prose. | `logs/full_suite_baseline_check_0e4ce3a.log` (`7 failed, 2528 passed … [m1 listed as] optional member green this run`); standalone: `1 failed, 16 passed` on `tests/decision_lane/test_r53_midturn_mindset.py`. Cold `anchor_exec._mindset_history()` → `{}`, `decision._current_mindset("s-r53-mindset")` → `{"scope": None, "rigour": None}`. |
| **N1** | non-blocking | `.importlinter` comment claims *"every direct core -> hermes_router.<submodule> edge is still caught"*. False for **root-recorded** edges: inside `core/routing_caps` and `core/telemetry` a *new* `from .. import <package-re-exported name>` is entirely invisible (the exemption is pair-exact against the root node). Demonstrated with a nonsense name; the live instance is `core/routing_caps.py:422 from .. import router_tools`, i.e. a real **core → api** edge hidden today. Bounded to 2 modules and declared in the file; S2's layers contract will not close it either (it duplicates the exemption). | `probes/guard_mutation_battery.txt` MUTATION 2 rows 5-6 (`exit 0`). |
| **N2** | non-blocking | The brief's claim 2 ("exemptions that match no live edge **deleted**, each with a grep proof") is not evidenced by the diff: **zero entries were deleted**, because none were dangling — every pre-S1 entry was malformed, and all correspond to live edges. Net effect on the exemption surface is **+1** (`core.telemetry -> hermes_router`, correctly declared as debt in the config comment). Report the property, not the deletion: verified independently, 9/9. | `git show 0e4ce3a -- .importlinter`; drop-one-at-a-time battery: all 9 entries → BROKEN (proves each matches a live edge). |
| **N3** | non-blocking | The `hermes_cli` exemption is module-pair-wide, so `core/config_access` may import *any* `hermes_cli.*` submodule invisibly (`from hermes_cli import utils` → KEPT). Consistent with the contract's stated intent ("except the config_access adapter"), but worth knowing: it is one adapter-wide hole, not one edge. | Probe MUTATION 2 row 4. |

---

## Verification table (brief items 1-8)

| Item | Result | Evidence |
|---|---|---|
| 1. `lint-imports` at 0e4ce3a | **PASS** — exit 0, `core/ must not import other hermes_router modules (L0) KEPT (8 ignored imports)`, `core/ must not import hermes_cli (except the config_access adapter) KEPT (1 ignored import)`, `Contracts: 2 kept, 0 broken.` | `logs/lint_imports_head_0e4ce3a.txt` (E1: ran it) |
| 1b. Premise: was it red before? | **PASS** — at parent `756e8ed`: exit **1**, 7 × `No matches for ignored import …`, no contract report. Claim 1's diagnosis reproduces exactly. | `logs/lint_imports_parent_756e8ed.txt` (E2: `git archive 756e8ed` into scratch) |
| 1c. Mutation: can the guard fail? | **PASS** — new `core.canonical -> hermes_router.frames` edge → `BROKEN (8 ignored imports)`, exit 1. `core -> features.banners.lifecycle` and `core -> gate.registration` also BROKEN. | `probes/guard_mutation_battery.txt` MUTATION 2 |
| 1d. `features -> api` mutation | **N/A at S1 (not a defect)** — no layers contract is active yet (deferred to S2 per plan §B row S2 and the config header). A `features -> api` edge is invisible today: exit **0**. That mutation is S2's acceptance criterion, not S1's. | MUTATION 2 last row |
| 2. `baseline_check.py` comparison | **PASS (core direction)** — exact set → exit 0; unpinned red → exit 1 `UNPINNED RED`; pinned red went green → exit 1 `PINNED-RED-NOW-GREEN`; empty baseline → exit 1. | `probes/pin_and_optional_battery.txt` A/E/F/B/H |
| 2b. Pin is not a rubber stamp | **PASS** — both directions tested (probes B, E, F) | same file |
| 3. Optional mechanism | **FAIL on design (F1, F2)** — genuinely state/order-dependent (F4), but colour-only acceptance absorbs real regressions and a never-run id counts as green. | probes C, D; suite vs standalone |
| 4. Exemption deletions | **PASS on the property / claim wording off (N2)** — 9/9 retained entries match live edges (drop-one → BROKEN each, with the exact line numbers: l.132, l.169, l.178, l.203/212, l.415, l.165, l.422, l.69, l.59/65/95/171). No dangling exemption ships. | drop-one battery |
| 5. Scope | **PASS** — `git show --stat` = `.importlinter`, `docs/r76-p4-baseline-triage.md`, `scripts/baseline_check.py`, `tests/baseline_failures.txt`; zero plugin source files; tree clean. | `git show --stat 0e4ce3a` |
| 6. Behaviour neutrality | **PASS (what I checked)** — (a) no `.py` under the package changed; (b) nothing imports `baseline_check` and nothing reads `.importlinter` at runtime (grep: only the script's own docstring); (c) `tests/baseline_failures.txt` is data consumed only by that script; (d) `plugin.yaml` diff is **0 lines**; (e) the live suite's only delta vs the triage's own runner line is `test_m1` flipping colour, which the commit cannot cause (no runtime file touched) — machine-state, not code. | grep + `git show 0e4ce3a -- plugin.yaml` |
| 7. Full suite | **PASS with the pinned set** — runner summary **verbatim**: `7 failed, 2528 passed, 2 skipped, 1 deselected, 1 xfailed, 2 warnings, 2 errors in 326.79s (0:05:26)`; `baseline_check: pinned required=9 optional=1` / `actual failures/errors=9` / **`OK — failure set equals the pinned baseline`**, exit 0. Differs from the triage's pinned runner line (`8 failed, 2527 passed … 636.88s`) by exactly `test_m1` — permitted by the optional class, but see F4. | `logs/full_suite_baseline_check_0e4ce3a.log` (E1) |
| 8. Version | **PASS** — `plugin.yaml` unchanged (`git show 0e4ce3a -- plugin.yaml` = empty diff; version stays `6.13.23`). Correct for a guard/docs slice. | same |

Triage spot-checks (M2, 3 of 10 rows): row 1 `decision_midturn.py:408,420` + `reflex_weights.py:69` → confirmed;
row 3 `closure_pass.py:221 config_access.sub_block("closure_pass")` → confirmed; row 9
`completion_audit.py:1300-1310 fold_revision=False **BY DESIGN**` → confirmed. Row classing is not folklore.

---

## Minimal fix list (ship after these + re-verify)

1. **Make a never-run optional id fail.** In `baseline_check.py`, split `opt_absent` into
   *"ran and passed"* vs *"did not appear in the junit at all"*; the latter must set `ok = False`
   (message: `OPTIONAL ID NOT PRESENT IN RUN`). This is the docstring's own promise; make code and doc agree.
2. **Close the absorption hole** — preferred, cheapest: fix the test (`test_m1(monkeypatch, _env)` in
   `tests/decision_lane/test_r53_midturn_mindset.py`, the one-word change the triage already recommends), then
   pin it as **required** and delete the `optional` class entirely. If the class must stay, accept a red only
   when the recorded failure reason matches the declared one (e.g. `assert None is not None` at `:104`);
   any other reason = drift.
3. **Fix the two claims that do not hold:** delete or implement `--show-variant`, and correct
   `tests/baseline_failures.txt:31-32` + `docs/r76-p4-baseline-triage.md:49-56` to the measured facts
   (red standalone, **green in the full suite at 0e4ce3a**).
4. **Narrow the root-recorded claim** in `.importlinter`: state that root-recorded edges from
   `core.routing_caps` / `core.telemetry` are invisible to the contract (one such edge exists today:
   `routing_caps.py:422` → `api.router_tools`), so no later reader believes L0 is fully guarded.

Items 1-4 are config/doc/test-pin edits — still inside a guard slice; no runtime code.

---

## Artefacts

- `probes/guard_mutation_battery.txt` — control, drop-one-ignore battery (9/9 BROKEN), positive edge probes.
- `probes/rerun_guard_mutation.py` — re-runnable drop-one battery (`git archive 0e4ce3a` → scratch, then run).
- `probes/pin_and_optional_battery.txt` — pin tests A/B/C/D/E/F/G/H with synthetic junit.
- `logs/full_suite_baseline_check_0e4ce3a.log` — the full-suite run + `baseline_check.py` output.
- `logs/lint_imports_head_0e4ce3a.txt`, `logs/lint_imports_parent_756e8ed.txt` — before/after guard.

Scratch trees used (not committed): `/opt/data/profiles/reviewer/cache/scratch/r76s1/wt` (archive of 0e4ce3a),
`…/pre` (archive of 756e8ed). The working tree was never mutated; all mutation probes ran on archive copies.
