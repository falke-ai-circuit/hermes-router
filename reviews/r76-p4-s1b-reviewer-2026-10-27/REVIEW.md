# R76-P4-S1b — the exemption must explain itself, and a stale one must fail

**Commit reviewed:** `1f4a2f0` (parent `52c9bec`, tree clean)
**Repo:** `/opt/data/plugins/hermes_router`
**Verdict: PASS**

Reviewed by: reviewer (verifying role). Recipient modelled: the next slice author / deploy gate that runs
`scripts/baseline_check.py` and reads its verdict; and the human reading `tests/baseline_failures.txt` to learn
*why* a red is tolerated.

**Mandatory probe exit codes (my own probes, built from scratch at this HEAD, coder copies not used):**

| Probe | Under S1 (`0e4ce3a`) | At S1b (`1f4a2f0`) | Required |
|---|---|---|---|
| **C** — never-run optional id | EXIT **0** | EXIT **1** (`OPTIONAL ID NOT PRESENT IN RUN … that is DRIFT`) | non-zero ✓ |
| **D** — real failure inside an optional, different cause | EXIT **0** | EXIT **1** (`OPTIONAL REASON MISMATCH … observed: tests/test_pinned.py:14: AssertionError`) | non-zero ✓ |

---

## Findings (F1-F4 + fleet items)

| # | Sev | Result | Evidence |
|---|---|---|---|
| **F1** | was blocking | **CLOSED.** An accepted optional red now requires `reason=<class>` and `expect="<recorded failure text>"` in the pin, is printed with both at run time, and a red whose failure text does not contain `expect` fails as `OPTIONAL REASON MISMATCH` instead of being absorbed. A pin missing `reason`, missing `expect`, or naming an unknown class is refused *before* the suite runs (`PIN FILE INVALID`). | Probe D → EXIT 1 with mismatch message (`probes/probe_results_C_D_U.txt`); `U_no_reason` / `U_no_expect` / `U_bad_reason` → EXIT 1 (same file); tolerated red prints `reason=state-dependent; failure signature matched expect='assert None is not None'` (`probes/legit_matching_signature_exit0.log`) |
| **N4** | non-blocking, **residual on F1's thesis** | The signature is *textual*, so it distinguishes causes only when the rendered failure text differs. Two genuinely different causes that both render `assert None is not None` at `:104` are still absorbed — I built exactly that and the gate exits **0** (`optional member RED (tolerated)`). F1's own example regression ("envelope stops carrying `mindset` at all") would render the *same* text at the *same* assertion line, so for that specific failure mode the pin is still colour-blind. The mechanism raises the bar from "any red" to "any red that renders I like the recorded one"; it does not prove cause. The alternative (make `test_m1` request `_env` and pin it **required**) remains the only full close — recommended, not required by this brief. | `probes/residual_same_text_absorbed.log` (EXIT 0) |
| **F2** | was blocking | **CLOSED.** Split into `present-and-green` vs `absent`; absent sets `ok = False` and names the drift. `opt_absent` covers typo / renamed / file-deleted, and the deselect case is caught independently: a test that exists but is deselected also lands in `absent`, because presence is read from the junit run, not from the filesystem. | Probe C → EXIT 1, message names `? …test_never_exists_anywhere (declared reason=state-dependent)`; Probe C2 (deselected) → EXIT 1 with the same drift message (both in `probes/probe_results_C_D_U.txt`) |
| **F3** | was blocking | **CLOSED by deletion.** The `--show-variant` usage line is gone from the docstring (`Usage` now lists only the bare run and `--junit`); argparse has exactly `--baseline, --junit, --pytest-arg, --no-run`; behaviour matches the doc (the variant line is printed unconditionally in every run). Zero mentions of `show-variant` anywhere in the tracked tree outside `reviews/` (`git grep`). Old claim confirmed present at `0e4ce3a:scripts/baseline_check.py:14`. | `git grep`; `python3 scripts/baseline_check.py --show-variant` → EXIT 2 `unrecognized arguments` (correct, since nothing claims it) |
| **F4** | was blocking | **CLOSED.** Corrected text states the OBSERVED split, and both halves reproduce here: standalone `1 failed, 16 passed` with `assert None is not None` at `tests/decision_lane/test_r53_midturn_mindset.py:104`; full suite `7 failed, 2528 passed … 2 errors` with `optional member green this run`. The doc also records that the earlier "red on both invocations" claim was an assumption the suite falsified, which is the honest form. | Quoted text below; `logs/test_m1_standalone.log`; `logs/full_suite_reviewer.log` |

Quoted corrected prose (`docs/r76-p4-baseline-triage.md`, F4):

> **Observed** at `0e4ce3a` (the S1 commit): the standalone module run fails
> (`tests/decision_lane/test_r53_midturn_mindset.py` → `1 failed, 16 passed`, `assert None is not None` at `:104`),
> while the **full suite at that HEAD reports `test_m1` PASSING** (`7 failed, 2528 passed … 2 errors`), and
> `baseline_check.py` prints `optional member green this run`. So the result is **state-dependent**, not a fixed red …
> an earlier draft of this doc claimed the test was red on both invocations and that order-dependence "did not
> reproduce"; that was an assumption, and the suite falsified it.

Only nit: it attributes the numbers to `0e4ce3a` while I measured at `1f4a2f0`; S1b touches neither the test nor
runtime, so the attribution holds (my numbers match the quoted ones exactly).

---

## Verification table (brief items 5-9)

| Item | Result | Evidence |
|---|---|---|
| 5. Legitimate state-dependent red still accepted, reason visible | **PASS** — EXIT 0, and the acceptance is *narrated*, not silent: `optional member RED (tolerated): … reason=state-dependent; failure signature matched expect='assert None is not None'`. | `probes/legit_matching_signature_exit0.log` (E1) |
| 6. `.importlinter` contracts still "kept" | **PASS** — `2 kept, 0 broken` (L0 `KEPT (8 ignored imports)`, `hermes_cli` `KEPT (1 ignored import)`), exit 0; `.importlinter` is byte-identical to `52c9bec` (not in the commit's file list), so S1's repair cannot have been disturbed. | `logs/lint_imports_reviewer.log` (E1); `git diff --name-only 1f4a2f0^ 1f4a2f0` |
| 7. Scope | **PASS** — exactly 8 paths: `scripts/baseline_check.py`, `tests/baseline_failures.txt`, `docs/r76-p4-baseline-triage.md`, and 5 self-evidence files under `reviews/r76-p4-s1b-2026-10-27/`. **Zero** files under `hermes_router/`, zero `.importlinter`, `plugin.yaml` diff = 0 lines (version stays `6.13.23`). No plugin runtime edit → no FAIL. On committing its own logs: **acceptable here** — the logs sit in the coder's own fresh directory, do not touch or overwrite my `reviews/r76-p4-s1-2026-10-27/` verdict, and this fleet's slice contract requires the gate's raw evidence to travel with the commit that claims it (a separate artefact commit would leave the claim unevidenced at the sha under review). The cleaner split is `probes/`+`logs/` in a follow-up commit; not a defect. | `git diff --stat/--name-only`; `git diff … -- plugin.yaml \| wc -l` = 0 |
| 8. Full suite + failure set vs pinned | **PASS** — runner summary **verbatim**: `7 failed, 2528 passed, 2 skipped, 1 deselected, 1 xfailed, 2 warnings, 2 errors in 313.23s (0:05:13)`. `baseline_check: pinned required=9 optional=1` / `actual failures/errors=9` / `variant = 9 red (no optional members red)` / `optional member green this run: …test_m1…` / `OK — failure set equals the pinned baseline`, **exit 0**. The 9 actual = the 9 pinned (7 FAILED + 2 teardown ERRORs, the two `test_r10_2_*` nodes pinned as red). | `logs/full_suite_reviewer.log`, `logs/junit_reviewer.xml` (E1) |
| 9. Behaviour neutrality | **PASS** — nothing under the package changed; nothing imports `baseline_check` or reads `tests/baseline_failures.txt` / `.importlinter` at runtime (`grep` over py/yaml/toml/cfg/ini outside `reviews/` returns no consumer beyond the script itself); `plugin.yaml` untouched; no banner, row, emitted text or lane-firing decision is emitted by this script. Neutral by construction, not by assertion. | grep + diffs (E1) |

### Evidence classes
E1 (I ran it): probes C/C2/D/D2/U ×3/legit, `baseline_check.py` full run at HEAD, standalone `test_m1`,
`lint-imports`, greps and git diffs. No claim in this verdict rests on the coder's own logs; the coder's
`reviews/r76-p4-s1b-2026-10-27/probes|logs` were not opened for grading.

### Hostile attacks (three, all addressed)
1. **A3** — pin an optional id that never runs → absorbed as green? *Defeated:* EXIT 1, drift named (Probe C).
2. **A4** — keep the id, swap the cause of failure → absorbed as the exemption? *Defeated for textually distinct causes:* EXIT 1, `OPTIONAL REASON MISMATCH` (Probe D). *Not defeated for a cause that renders the same assertion text* → recorded as N4, non-blocking.
3. **A5** — S1b quietly rewrote the guard or the fixture to make its own probes pass? *Defeated:* `.importlinter` and every runtime/test source file are absent from the commit; the only executable change is in the dev script, and I graded it with my own synthetic repo, not the coder's fixtures.

### Drift check (triple loop)
Request-drift: brief demanded machine-checkable reason + stale-fail + F3/F4 prose truth; all four addressed, and the
one thing it did *not* do (make `test_m1` a required test) was explicitly optional in S1's own fix list.
Implementation-drift: the code does what the docstring says in every branch I exercised (invalid pin, absent id,
mismatched red, matching red, green-but-present). Verification-drift: my probes are re-derivable from
`probes/build_and_run.py` (it copies `1f4a2f0:scripts/baseline_check.py` and writes its own `tests/`).

## Residual (non-blocking, to track)
- **N4** (above): textual signature ≠ causal proof; the full close is `test_m1(monkeypatch, _env)` + pinned
  **required**, deleting the `optional` class. Recommend it for the next guard slice.

## Artefacts
- `probes/build_and_run.py` — my harness (copies the script from the commit, synthesises the repo).
- `probes/probe_results_C_D_U.txt` — C, C2, D, D2 and the three invalid-pin cases, with exit codes.
- `probes/legit_matching_signature_exit0.log` — item 5.
- `probes/residual_same_text_absorbed.log` — N4.
- `probes/S1_battery_for_reference.txt` — the S1 battery this one replaces (superseded, kept for delta).
- `logs/full_suite_reviewer.log`, `logs/junit_reviewer.xml` — full suite + `baseline_check` verdict.
- `logs/test_m1_standalone.log` — standalone red at `:104`.
- `logs/lint_imports_reviewer.log` — `2 kept, 0 broken`.
