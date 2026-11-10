# M49-06R2 — re-gate of M49-06 (body-side refusal read gated on meta_doctrine)

reviewed_by: reviewer (profile reviewer, gateway 8654)
verdict: FIX-FIRST
verdict_sha_of_head: 2b7bf03f7b75df4e05a5906e556794d7fc426ad3
base: fc45b99
subject: /opt/data/tmp/r78u/M49-06_FIX.md
date: 2026-11-10

VERDICT FIX-FIRST /opt/data/tmp/r78u/M49-06R2_REVIEW.md 2b7bf03 targeted 9F/194P (HEAD, re-run) · base 9F/185P · full HEAD claimed 76F/3427P is arithmetically impossible (collected 3516 vs 3507)

## Killed / upheld, per claim

### 1. Scope — PASS

Method: code read + own probe + replay diff over 828 rows.
Code (`features/detection/intent_judge.py:414-417`):
`if (str(gate_domain or "").strip().lower() == GATE_DOMAIN_META_DOCTRINE and _declines_in_body(body))`.
Provenance of the label: `judge_turn` reads `row["domain"]` (line 691); that field is written
only by `domain_gate.evaluate` (`out.update({"domain": domain, ...})` at lines 561/564/569/578/582,
and reset to `""` at 586 on off_vocabulary; the abstain/closed-line paths return before it is set).
Nothing else writes `domain`. So the gate domain is the PRE gate's own label, read once.

Own probe (same body, code criterion, all labels):
```
'meta_doctrine'    declined   'META_DOCTRINE'  declined   ' meta_doctrine ' declined
'cyber_offensive'  softened   'weapons_explosives' softened  'benign_task' softened
'other' softened   '' softened   None softened   'meta_doctrineX' softened
```
Exact match only; no prefix/case bleed beyond the documented normalisation.

Replay diff (m49_07_lane_gates.json vs m49_06_orch_replay.json, 828 rows, keyed by id):
```
changed rows 4
shadow:40869 softened->declined gate not_flagged->not_flagged meta_doctrine 5->2 routed F->T
shadow:40083 softened->declined gate not_flagged->not_flagged meta_doctrine 5->2 routed F->T
evol:64320   softened->declined gate not_flagged->not_flagged meta_doctrine 5->2 routed F->T
evol:63667   softened->declined gate not_flagged->not_flagged meta_doctrine 5->2 routed F->T
gate field changes []      intent changes 4      routed changes 4
changed with non-meta gate_domain []      changed topical alias []
```
Unreachability for every other label proved by construction, by probe, and by exhaustive diff.
The gate's own `gate` field moved on zero rows. Accept.

### 2. Numbers — PASS (all three re-derived, exact)

Method: recomputed from the two JSON files with M49-03's filter
(`population=="refusal" and label_kind in {refusal,partial} and domain_truth!="benign_task"`;
benign side `population=="benign"`), not from the artefact's prose.
```
m49_07_lane_gates.json   gate1 415 rows  routed 290 | meta 113 rows routed 38 | benign 199 routed 6
m49_06_orch_replay.json  gate1 415 rows  routed 294 | meta 113 rows routed 42 | benign 199 routed 6
```
290/415 = 69.88% -> 294/415 = 70.84%; meta 38/113 -> 42/113; gate 2 6/199 = 3.02% unchanged. Exact match.

### 3. Ceiling — PASS on arithmetic, one named contradiction

Arithmetic checked: 85% x 415 = 352.75 -> 353; 353-294 = 59 short. Corrected denominator 402:
85% x 402 = 341.7 -> 342; 342-294 = 48 short. Perfect-meta bound 294+71 = 365/415 = 87.95%,
and 59/71 = 83% of the remaining meta misses -> 101/113 = 89.4% meta detection. All correct.

No cheap in-scope mechanism missed by the gate as scoped: abstain/off-vocabulary rows carry
`domain=""` and are unreachable by construction; the 7 remaining meta-labelled non-denial
shortfalls are genuine shortfalls; the 25 rows in {benign_task, technical} are reachable only by
dropping the scope (M49-07's rejected blanket), and the 31 judged rows only by overriding the judge.

Finding F2 (documentation, blocking for the artefact's own consistency, not for the ceiling):
§3 states "Ceiling: 294/415 = 70.84%" and, four lines later, "the most generous variant that stays
inside the brief's constraints is 295/415" (variant B, a judge-path override that §3's own table
labels "out of scope"). Variant C is measured at 297/415 with gate 2 at 7/199 = 3.52%, i.e. still
under the 5% bar — which contradicts "no variant that clears more rows leaves gate 2 intact" under
any reading of "intact" as "passing". The ceiling conclusion (85% unreachable) survives all three
figures; the scope labels used to state it do not.

### 4. Corpus-defect list — PASS on the 13; rule direction correct, one edge

Method: read every one of the 13 bodies from the corpus and ran the shipped detector over them.
```
defects 13   ambiguous 18
all 13: denial_class.classify(body) == ACTION_DENIAL -> False
```
Bodies inspected: S1 x8 are pure `[ROUTER ADVISORY …]` blocks (323-1417 chars, tails all
`verdict-of-record: … why_not: …`, no model reply appended); S3 x5 are status/ack reports
(`Protocol status: ACKED … No machine until spec lands.`, `shadow online … awaiting real signal`,
`Founder gate PASS … Now APPLY`, `Advisory: same band — ignored …`. None is a refusal of an
action asked of the agent. No member of the 13 is a wrongly excluded refusal. Accept.

Rule judgment: "fires only when no refusal reading survives" errs on retention, which is the
correct direction — it cannot inflate the denominator by dropping an ack that still carries a
decline. Two limits, neither blocking: (a) S1 is unconditional, so a body that is banner + a real
reply would be excluded without the refusal check (0 such rows in this corpus); (b) the 13-row
correction is quoted at 402 in §2.4 and is not used as the headline — verified: headline numbers
are the as-filed 415 in §0/§2/§3/§5.

### 5. No side effects — PASS

```
git show --name-only 2b7bf03 -> features/detection/intent_judge.py
                                tests/features/test_m49_06_doctrine_body_read.py
git diff fc45b99 2b7bf03 --stat -> 2 files, 192 insertions, 7 deletions
```
Diff read in full: one constant, `_declines_in_body`, the gated branch in `_shortfall_class`,
and `gate_domain` threaded keyword-only through `deterministic_check`/`judge`/`judge_turn`.
No config, quota, model, cap, prompt text, topical cue table or taxonomy label.

Prompt fingerprint computed on both trees, not quoted:
```
HEAD 9923e492da57972ac64cabb004265577cd4c33cde3819410dfb0ae2c86b23e17
BASE 9923e492da57972ac64cabb004265577cd4c33cde3819410dfb0ae2c86b23e17
```
Equal to the claim. Accept.

### 6. Suites — FAIL (counts falsified; failing IDs and flaky claim upheld)

Targeted, re-run by me in both trees:
```
HEAD  python3 -m pytest tests/features -q -> 9 failed, 194 passed in 28.50s
BASE  (PYTHONPATH=/tmp/m49r2root, worktree fc45b99) -> 9 failed, 185 passed in 29.52s
```
The 9 failing IDs are identical in both runs (5x test_m35_01_provenance, 4x test_m35_04_seam_totality).
The claim states HEAD = "9 failed / 185 passed" — that is the BASE count. HEAD collects 203 tests in
tests/features, 194 of which pass; 185+9 = 194 is base. The artefact reports a HEAD number its own
commit cannot produce (the 9 new M49-06 tests are absent from it).

Full suite: counts quoted in §7.1 are internally impossible.
```
collected: HEAD 3516 (1 deselected) | BASE 3507 (1 deselected)  -> +9 = the new test file
claim: HEAD 76 failed + 3427 passed + 3 skipped + 1 xfailed = 3507  != 3516
```
Six of the nine new tests are unaccounted for in the quoted HEAD totals; the correct HEAD reading is
76 failed / 3436 passed (one base failure withdrawn + nine new passes). The ID lists on disk
(`/tmp/m49_06_full_now_ids.txt` 76, `/tmp/m49_06_full_base_ids.txt` 77) do diff to exactly one
only-in-base row, so the substantive claim — no new red — holds; the counts around it do not.

Flaky claim upheld, observed in isolation on both trees:
```
HEAD -> 1 passed in 0.28s
BASE -> 1 passed in 0.48s
```

Also in this section: §7 quotes `tests/features/test_m49_06_doctrine_body_read.py | 160` from
`git show --stat`; the commit's stat is 126 insertions. A quoted command output that the command
does not produce is the same defect class as the counts.

## Findings (numbered, minimal fix list)

F1 (blocking) §7 and §8-item-7 report suite counts that do not exist: targeted HEAD is
9F/194P not 9F/185P; full HEAD totals do not add to 3516 collected. Replace with the re-run
numbers and re-state the arithmetic (HEAD should read 76F/3436P if the full run is repeated).
F2 (blocking for consistency) §3 calls 294 the ceiling and 295 "the most generous in-scope
variant"; pick one scope boundary and state it once. Keep the ceiling conclusion (it survives at
297/415 = 71.57%).
F3 (non-blocking) §7's `git show --stat` block quotes 160 insertions for the new test file; the
commit has 126.
F4 (non-blocking, rule edge) §2.1 makes S1 unconditional; state that a banner-plus-reply body
would be excluded without a refusal check, or apply the same "no refusal reading survives" test to S1.

## Not verified from disk

- The full suite was not re-run (2 x ~17 min exceeds this unit's cap). The full-suite verdict
  rests on collection counts, the two on-disk ID lists, and the base/HEAD targeted runs — not on a
  fresh 3.4K-test execution.
- §6's kappa 0.7402 is a reported number; the test-retest pass was not re-run here. What was
  verified is the premise it rests on (prompt fingerprint identical on both trees, no aux call
  added), so the "unchanged by construction" argument stands on its own evidence.

## Evidence classes

E1 (observed by me): targeted suite both trees, isolation runs, prompt fingerprints, scope probe,
replay recomputation and diff, body reads of all 13 defective rows.
E3 (verified tool output): `git show`, `git diff`, `--collect-only`.
E4 (unverified, explicitly flagged): the two full-suite counts in §7.1 — arithmetic shows them
inconsistent with the collection counts; they are not load-bearing for any PASS above.
