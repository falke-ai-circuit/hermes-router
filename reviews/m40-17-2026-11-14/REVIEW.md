# M40-17_REVIEW — independent verification of M40-16 (1d2dd07, dbd67b6)

VERDICT FIX-FIRST /opt/data/tmp/r78u/M40-17_REVIEW.md dbd67b6 test_m40_16_lane_contracts 17P/0F · test_m37_04_pre_lane_table 5P/0F (22P/0F)

unit:                 M40-17 (independent verdict; M40-16 authored by architect)
artefact:             /opt/data/tmp/r78u/M40-17_REVIEW.md
head_under_test:      dbd67b6 (M40-16 tip; 1d2dd07 = data+test commit)
measured_at_head:     bc0ebb4 (current main; handler lines re-checked here too)
reviewed_by:          reviewer
verdict:              FIX-FIRST
verdict_sha_of_head:  dbd67b6
SHAPE:                reuse lanes/registry.py:LaneContract — one field (`kind`) added to the existing dataclass plus `_validate_contract` in the existing registry; no parallel contract type, no second registry.

Claim under test (architect): "the router now has a UNIFIED MODULAR CONTRACT FOR ALL LANES."
Split: the DATA half is true (all 4 lanes declare; vocabulary closed; fail-loud). The GOVERNANCE
half is false: no handler dispatch site reads the contract. 13 registry-id literal comparisons
remain and none was migrated. Contract exists; contract does not govern. FIX-FIRST.

---

## 1. Runtime enumeration — PASS (E1)

Command: `cd /opt/data/plugins && python3 -c "import hermes_router.lanes.registry as R; ..."`
(registry imported through `hermes_router.lanes.builtins`, i.e. the real registration path)

```
KINDS ('gate', 'consult', 'audit', 'advisory')
IDS ('shadow', 'higher-pre', 'higher-post', 'decision')
shadow | gate | 'uncensored_domain' 'uncensored_intent' 6 'uncensored_render'
higher-pre | consult | '' '' 0 ''
higher-post | audit | '' '' 0 ''
decision | advisory | '' '' 0 ''
```

4/4 lanes carry a contract; 4/4 kinds are in the closed vocabulary; exactly one gate lane.
Empty fields in the three non-gate lanes are EARNED in-file, not defaulted: builtins.py:118-135
(higher-pre, four per-field reasons), :143-149 (higher-post), :155-160 (decision). Registration
also forbids the opposite error — a non-gate lane naming a gate field — measured below.

## 2. Fail-loud — PASS (E1, live)

Own probe against the running registry (not the test suite):

```
DUPE ValueError: lane id already registered: probe-dupe
DUPE-EQUAL: no error (idempotent, by design)
SEAM ValueError: lane 'probe-seam': unknown seam id 'PRAE' (allowed: PRE, MID, POST)
KIND ValueError: lane 'probe-kind': unknown contract kind '' (... empty is not a kind)
NONGATE ValueError: lane 'probe-nongate': ... must declare no gate surface; it names ['pre_question']
EMPTYGATE ValueError: lane 'probe-emptygate': kind 'gate' contract declares no field at all ...
```

Nuance stated precisely: duplicate id raises only when the spec DIFFERS (registry.py:216-220);
an equal spec re-registers silently by design (module re-import idempotence). The claim as written
("register two specs with the same id") holds for the differing-spec case; an identical duplicate
is swallowed — acceptable, but not a universal duplicate check.

## 3. Parity test — PASS (E1)

File: `tests/routing/test_m40_16_lane_contracts.py` (264 lines, 17 test functions).
Command and printed tail:

```
python3 -m pytest hermes_router/tests/routing/test_m40_16_lane_contracts.py \
  hermes_router/tests/routing/test_m37_04_pre_lane_table.py -q
...................... [100%]
22 passed in 1.59s
```

`--collect-only`: 17 tests in the new file; 5 in m37_04; total 22 — matches the run.
The file imports the RUNNING registry (`R.all_ids`, `R.resolve_contract`) with only `_IDS`
monkeypatched per test; it does not carry a copied contract. It never imports a handler module
(grep for route_gate/orchestration/router_tools in the file: zero hits) — it cannot see §4.

Artefact inaccuracies (E1, minor, not verdict-changing):
- CONTRACT §3 says "16 tests"; the file collects 17. §3's own table lists 17 ids (a..i incl. d2/e2/e3/g3b).
- Test-module docstring line 26 says a table "without a terminal else row" raises at REGISTRATION,
  which contradicts §2.1 / §7 D3 and the actual `test_g5`: a non-terminal table is ACCEPTED and
  falls to row 0 / `fallback_shape`. The code is right; the docstring sentence is wrong.

## 4. Handler migration — FAIL (the substantive answer)

Sweep re-run (architect's §9 command 1): **31 hits**, identical to the artefact's count.
Classification re-derived by reading every hit:

| class | n | state after M40-16 |
|---|---|---|
| registry-id dispatch comparisons | **13** | all still literals; **0 migrated** |
| aux-verdict vocabulary (`"shadow"/"higher"/"none"`) | 5 | still literals (route_gate:1646,1661,1680,1682; intent_classifier:371) |
| pricing vocabulary (`"frontier"`) | 1 | literal (provider_prices:370) |
| membership validation | 8 | still module-local closed sets |
| comment / docstring / non-lane | 4 | n/a |

The 13 registry-id sites, each still comparing a lane-id literal or constant:

```
gate/orchestration.py:814        _claim_decision.lane == LANE_SHADOW
gate/orchestration.py:977        claim lane not in (LANE_HIGHER_PRE, LANE_HIGHER_POST)
route_gate.py:1796               [p for p, lane in DECLARED_USER_VARIANTS.items() if lane == LANE_HIGHER_PRE]
route_gate.py:2264               decision.lane == LANE_DECISION
route_gate.py:2337               decision.lane != LANE_HIGHER_PRE
route_gate.py:2340               decision.lane == LANE_HIGHER_PRE
route_gate.py:2436               decision.lane != LANE_SHADOW
route_gate.py:2451               orientation=(decision.lane == LANE_HIGHER_PRE)
passes/dispatcher_pre.py:603     _decision.lane == router_core.LANE_DECISION
features/envelope/unified.py:169 lane == "decision"
features/banners/turn_record.py:123  _lane.startswith("frontier") or _lane == "decision"
router_core.py:1023              lane == LANE_DECISION
router_core.py:1030              lane != LANE_COMPLEXITY
api/router_tools.py:226          _lane == "uncensored"
```

Registry-data reads inside handlers (own grep over the 8 handler modules):

```
route_gate.py        resolve_contract=0 valid_lane_ids=0 lane_for_pre=0 lane_for_post=0 resolve_replacement=0
gate/orchestration.py resolve_contract=0 valid_lane_ids=0 lane_for_pre=0 lane_for_post=0 resolve_replacement=1  (line 2271, replacement chain entry)
api/router_tools.py  ... all 0
passes/dispatcher_pre.py ... all 0
router_core.py       ... all 0
features/banners/turn_record.py ... all 0
features/envelope/unified.py ... all 0
gate/registration.py ... all 0
```

Only three modules consult the registry at all: `features/detection/rule_table.py` (both POST
resolution sites), `features/detection/domain_gate.py` (three PRE resolution sites), and
`gate/orchestration.py:2271` for `resolve_replacement`. The dispatch branches in §4's table do not.

Can a literal silently disagree with the contract? Yes — demonstrated structurally, E1 source:
`route_gate.py` and `router_tools.py` contain zero references to the registry (counts above), so
every branch is decided by imported constants/strings. Change `shadow.contract.kind` from `gate`
to `advisory` in builtins.py and `orchestration.py:814` still routes shadow down the uncensored
render chain; add a second `consult` lane and `orchestration.py:977` still exempts only
higher-pre/higher-post. The contract is frozen data at import (`LaneContract` is frozen — a live
mutation probe raised `cannot assign to field 'kind'`), so nothing at runtime reconciles the two.
The parity test cannot catch this: it never imports a handler.

Architect's D6 statement checks out (E1, `git show dbd67b6`):
`core/delivery_decision.py:577-582` sets `marker_violation` and returns `_terminal(D6, REASON_MARKER_VIOLATION)`;
`gate/registration.py:662-665` returns `WITHHELD_BODY` unless reason != marker_violation. The
marker withhold is indeed downstream of any contract row, and M40-15's `strip_sanctioned`
(:567) narrows it before the scan. Naming it as the real deny rather than a contract layer is correct.

---

## Hostile attacks (G-NEMESIS-GATE)

1. **"4/4 contracts" could be four empty shells.** Broken: all 4 kinds resolve at runtime, and a
   non-gate lane naming a gate token raises (NONGATE probe). Three derived empties carry in-file
   reasons; only the gate lane names tokens.
2. **"Fail-loud" could be untrippable.** Broken: tripped dupe id, unknown seam, empty kind,
   empty gate, non-gate-with-token — five distinct ValueErrors, all live. The untripped case
   (identical duplicate → silent) is named, not hidden.
3. **"Unified contract" could mean the test proves the contract to itself.** Confirmed as a real
   limit: the parity test reads only the registry; zero handler imports. A green parity suite is
   compatible with the handlers ignoring the contract entirely — which is the measured state.
4. **"No handler edits" was the brief, so migration absence is not a defect.** Rejected as a
   PASS argument: the unit's claim is "unified contract FOR ALL LANES", and a contract no
   dispatcher consults is a data model, not a governing contract. The brief's own rule says
   FIX-FIRST if literals remain that can contradict the contract.

## Drift check

Request drift: brief asked for a verdict on the claim, not a re-design. Kept.
Implementation drift: architect delivered data + validation exactly as scoped, and honestly
labelled D6 and the migration hand-off — no overclaim inside the document.
Verification drift: artefact's own tables were not used; every count above is my run. Where my
run differs from the artefact (17 vs 16 tests; docstring vs g5), it is recorded as minor.

## Numbered fix list (FIX-FIRST)

1. Migrate the 13 registry-id dispatch comparisons to registry accessors (`resolve_contract(lane).kind`,
   `.replacement`, `valid_lane_ids()`), per CONTRACT §5.1 — or reduce the claim from
   "unified contract for all lanes" to "contract data for all lanes; handlers not yet governed".
2. Add the named aux-vocabulary map (`AUX_LANE_MAP`) for route_gate:1646/1661/1680/1682 +
   intent_classifier:371 (CONTRACT §5.2), so the aux labels cannot drift from registry ids.
3. Correct the two document defects: "16 tests" → 17, and the test-module docstring's
   registration-refuses-non-terminal-else sentence (it is accepted; g5 proves it).
4. Only after 1-2: add a handler-level assertion (contract kind → dispatch) to the parity suite,
   so a future contract edit that handlers ignore fails a test instead of passing one.

## Open item

Handler migration (13 registry-id sites + 5 aux-vocabulary sites) is unstarted; the deny remains
D6 marker_violation at `gate/registration.py:662-665`, untouched by this unit.

## Evidence index

E1 (observed directly): runtime enumeration; five ValueError probes; pytest 22 passed / 17 collected;
31-hit sweep read in full; handler accessor counts; `git show dbd67b6` lines 555-590 / 655-668;
`git diff 5d70ecb..1d2dd07 -- lanes/builtins.py` (shadow rows unchanged; only `kind` added).
E2 (re-derived): classification of all 31 sweep hits; test-to-handler import check.
E3: none load-bearing. E4: none.
