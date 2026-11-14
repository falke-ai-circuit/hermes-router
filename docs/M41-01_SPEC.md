# M41-01 — SPEC: the measured uncensored lane, mapped onto the machinery that exists

Unit: M41-01 (architect, spec leg). Tree read: `/opt/data/plugins/hermes_router` @ `25d115f`
(branch `main`, clean). Every `path:line` below was opened at that revision and the named symbol
is on the cited line; §H is the machine-checkable table. No product code in this unit.
Design source: `/opt/data/tmp/r78u/M41_PROPOSAL.md` (measured arms), `/opt/data/tmp/r78u/M41_BUILD_SPEC.md`
(the pre-implementation build spec at `0b5f394`), diagnosis `/opt/data/tmp/r78u/M41-01_DIAGNOSIS.md`.

The lane is not a new subsystem. It is three new INPUTS pushed through seven seams that already
carry every other lane: the PRE request seam, the shared aux transport, the POST transform seam,
the per-session turn record, the render chain (budget/retry/swap), the banner, the spend ledger —
with lane behaviour expressed as ONE registry entry of four fields.

---

## A. SEAM MAP (existing machinery, cited)

| # | Seam | path:line | symbol on that line | What the lane does through it |
|---|---|---|---|---|
| S1 | PRE request seam | `gate/orchestration.py:783` | `_dgate.start(session_id, content, log=_hub()._log_route)` | starts the domain question before generation; returns immediately, nothing joined |
| S2 | PRE fail-open wrapper | `gate/orchestration.py:2020` (`except Exception`), `:2022` (`_verdict_g = "abstain"`) | the POST read's fail-open | a broken gate is `abstain`, never `not_flagged` |
| S3 | `route_classes()` | `features/detection/intent_judge.py:702` | `def route_classes() -> Tuple[str, ...]:` | the class vocabulary the routing table may act on (`declined` always, `softened` gated); successor of the retired probe's method |
| S4 | Aux classifier wiring | `semantic_classifier.py:537` | `def aux_raw_call(` | the ONLY paid transport the gate and the judge use |
| S5 | Aux breaker + failure switch | `semantic_classifier.py:205` (`def breaker_is_open()`), `:539` (`record_failure: bool = True,`) | breaker read + per-call failure policy | observer calls pass `record_failure=False` — `features/detection/intent_judge.py:629` (`record_failure=False, detail="intent_judge")`) — so an optional judge cannot open the shared breaker and silence the primary classifier |
| S6 | POST transform seam | `gate/orchestration.py:1277` | `def on_transform_llm_output(` | the single place the delivered body is decided |
| S7 | POST seam registration | `gate/registration.py:745` | `lambda: ctx.register_hook("transform_llm_output",` | the seam the handler is reached through; no second registration is added |
| S8 | Canonical turn record | `core/state.py:435` | `def mark_pre_domain(session_id: str, ask: str = "", *, lane: str = "",` | the PRE verdict + criterion ride the per-session, ask-bound, turn-bound record |
| S9 | Same record, read side | `core/state.py:476` | `def pre_domain(session_id: str, window_s: float = _PRE_DOMAIN_TTL,` | POST reads; it never re-classifies |
| S10 | Criterion field | `features/detection/domain_gate.py:381` | `def extract_criterion(ask: str) -> Dict[str, Any]:` | one criterion per turn, on S8's record; validated at `domain_gate.py:442` (`def criterion_of(`) |
| S11 | Render chain — call | `gate/orchestration.py:3007` | `rendered = _hub().router.call(` | the `replacement` field enters THIS call; no second render path |
| S12 | Render chain — budget | `gate/orchestration.py:115` (`def render_max_chars()`), `:119` (`def cap_render(text: str, limit: int) -> str:`), `:3020` (`budget_chars=int(_hub().render_max_chars() or 0),`) | cap stays the chain's, not the lane's |
| S13 | Render chain — retry ladder | `gate/orchestration.py:285` | `def _render_with_retry_ladder(*args: Any, **kwargs: Any) -> tuple:` | the lane inherits the ordered model ladder; it adds no retry policy |
| S14 | Render chain — swap | `gate/orchestration.py:3216` | `"POST", event_detail="render_swap_decision",` | the one swap record site; canonical `persisted == delivered` is preserved by `core/canonical.py:343` (`def rewrite_persisted_turn(`) and never bypassed |
| S15 | Banner | `debug_banner.py:438` | `def park_anchor_banner(session_id: str, banner_text: str,` | reused unchanged; a squelch emits no new banner class |
| S16 | Spend ledger | `core/usage_ledger.py:74` (`def record_tokens(`), `:275` (`def read_records(`) | cost per call, read back by the acceptance leg | gate and judge write their own `detail` tag; usage-absent stays honest-absent |

Delivered-text ledger, explicitly NOT the turn record: `core/canonical.py:343`. A PRE verdict is not
a delivered event; writing it there would break `persisted == delivered`, the invariant that file exists
to hold. The functional requirement ("POST reads, never re-classifies") is met by S8/S9.

Domain taxonomy lives as data, not as a second classifier: `features/detection/domain_gate.py:52`
(`UNC_DOMAINS = frozenset({`), `:56` (`CLOSED_DOMAINS = frozenset({"sexual_content_minors"})`),
`:61` (`SAFE_DOMAINS = frozenset({"benign_task", "technical", "other"})`).

---

## B. LANE AS DATA — exactly ONE registry entry

Exactly one lane contract exists, on the lane that already exists. Field definition:
`lanes/registry.py:28` (`class LaneContract:`) with the four fields at `lanes/registry.py:44`
(`pre_question: str = ""`), `:45` `post_question`, `:46` `gate_semantics`, `:47` `replacement`.
The single instantiation: `lanes/builtins.py:90` (`contract=LaneContract(`).

Entry as data (shape, not new syntax):

    LaneContract(
        pre_question="uncensored_domain",
        post_question="uncensored_intent",
        gate_semantics=(("declared_refusal","route"), ("intent_declined","route"),
                        ("gate_abstain","fallback_shape"), ("intent_softened_and_gate_flag","route_replace"),
                        ("intent_softened","deliver"), ("else","deliver")),
        replacement="uncensored_render",
    )

`{pre_question, post_question, gate_semantics, replacement}` and nothing else. Resolution is by TOKEN
through closed maps — `lanes/registry.py:232` (`def lane_for_post_question(`) and `:248`
(`def lane_for_pre_question(`) — so the POST handler never compares a lane string. Evaluator:
`features/detection/rule_table.py:189` (`def evaluate(*, declaration: str,`), called once at
`gate/orchestration.py:2215` (`_rule: Dict[str, Any] = _rt.evaluate(`). Replacement token resolution:
`rule_table.py:133` (`def replacement_token() -> str:`).

Two ordering facts that are data, not code (adopted from M41_BUILD_SPEC §B1/B2):
`gate_abstain` sits at position 3, after declaration and declined — both self-evident evidence that
must route whatever the gate says; and the gate term is absent from rule 5 because the gate filters
`softened` only (measured: filtering both classes collapses in-domain recall 90.2% -> 61.2%).

---

## C. THE THREE NEW INPUTS (and no fourth)

1. **Domain taxonomy** — `domain_gate.py:52/56/61`. Closed sets, transcribed from the measured
   prototype; `abstain` is a failure, never "safe".
2. **Acceptance-criterion field** — `domain_gate.py:381`, stored on the existing turn record
   (`core/state.py:435`), read at `core/state.py:476`. No new store; the record already carries
   ask-signature, turn binding and TTL.
3. **Intent judgement** — `features/detection/intent_judge.py:597` (`def judge(ask: str, body: str, criterion`),
   five classes, deterministic-first; seam entry `intent_judge.py:669` (`def judge_turn(`).

Anything else is a scope violation unless it replaces existing machinery one-for-one. No fourth input is
proposed: the classified ask is already carried by S1/S8; cost by S16; the render by S11-S14; the frame
by existing builders. If a later unit needs a fourth, it must name what it deletes.

---

## D. POST HANDLER — zero per-lane branches

The handler evaluates the ordered rows it is given and nothing lane-specific. The branch this design
DELETES is the pre-M41 route chain: `_probe_state == "route" / "no_route" / else` at baseline
`0b5f394:gate/orchestration.py:2008-2137`, plus the `unknown_route_gate` stage-2 read there. Its
replacement is the single `rule_table.evaluate` call at `gate/orchestration.py:2215`; the rule table
knows no lane id because it resolves the contract from the POST question token.

Enforced by test, not by review taste: `tests/features/test_m41_05_rule_table.py:388`
(`def test_f1_post_handler_has_no_lane_branch():`) and `:392`
(`def test_f2_rule_table_has_no_lane_branch():`). If a branch is unavoidable, it is named here as a
deletion target — none is. A `if lane == "uncensored"` anywhere is a spec violation.

---

## E. RETIREMENT TARGETS (named exactly from M40_LEGACY_AUDIT.md)

Nothing is deleted before its replacement is green on the corpus (strangler, never big-bang).

### R1 — `intent_probe` (audit §1, A1)
- Removed at baseline `0b5f394`: `gate/orchestration.py:526` (`from .. import intent_probe as _ip`) and
  `:528` (`_ip.run_on_request(request, session_id, content=content,`); module entry
  `intent_probe.py:556` (`def run_on_request(`). Its `route_classes()` semantics survive as S3.
- Removal proof: the module's own test `tests/routing/test_m34_16_intent_probe.py` was deleted in the
  same commit; successor semantics pinned at `tests/features/test_m41_04_intent_judge.py:439`
  (`def test_route_classes_carry_intent_probe_semantics():`). Zero non-test references at `25d115f`.
- Open item O1: no STANDING test asserts the module's absence. Required addition (same pattern as
  `test_e1` below): `tests/features/test_m41_07b_intent_probe_retired.py::test_module_and_script_absent`.

### R2 — `intent_classifier` (audit §2, safest-first row)
- Removal sites at `25d115f`: `route_gate.py:1576` (`from . import intent_classifier as _ic`),
  `route_gate.py:1667` (`from .intent_classifier import _vocab_hit as _vh`),
  `route_gate.py:2105` (`from .intent_classifier import _intent_suspect as _suspect_fn`); module entry
  `intent_classifier.py:459` (`classify_intent`) per audit §2.
- BLOCKED, stated: the judge answers "did this body deliver" at POST; `intent_classifier` answers
  "which lane does this ask want" at PRE. There is no replacement for that question in this lane.
  `tests/features/test_m41_04_intent_judge.py:403` (`def test_f_intent_classifier_not_deleted_or_modified():`)
  pins that it must NOT be removed by this design. Removal precondition: a `lane_declaration`
  `pre_question` token in the contract; then delete the three import sites in one commit.
- Removal proof to add with that unit: `tests/features/test_m41_09_intent_classifier_retired.py::test_zero_non_test_references`
  (grep shape of `test_e1`, below).

### R3 — `unknown_route_gate` (audit §10, A6)
- Removed at baseline `0b5f394`: `gate/orchestration.py:2075` (`from ..features.detection.unknown_route_gate import should_route`)
  and `:2076` (`_stage2, _stage2_reason = should_route(response_text)`); module entry
  `features/detection/unknown_route_gate.py:153` (`def should_route(`), default `mode=route` = measured
  no-op with zero config hits across all 11 profiles.
- Removal proof, running: `tests/features/test_m41_05_rule_table.py:348`
  (`def test_e1_zero_non_test_references_to_the_retired_gate():`) asserts zero non-test references, and
  `:366` (`def test_e2_the_module_no_longer_imports():`) asserts the import raises
  (`ModuleNotFoundError` at `:367`). Rule 6's fallback is the shape detector
  (`features/detection/denial_class.classify`), not this module.

---

## F. CONSTRAINT CARRIED FROM THE DIAGNOSIS — divergence ratchet

The measured cause is a TRANSPORT-DIRECTIVE asymmetry, not a stale tree and not the compare path:
the `MEDIA:` header is written into `messages.content` and is absent from
`delivery_obligations.content`, so every image turn seeds a divergence; the withheld failure body then
becomes the next turn's `_latest_obligation`, and the ratchet sustains itself.

Fix side, canonical-then-distribute only:
- normalize the `MEDIA:` line (plus its following blank) out of BOTH sides inside
  `core/delivery_integrity.py:105` (`def strip_banner(text: Any) -> str:`) — delivery routing is not
  answer content;
- exclude seam-failure bodies from selection at `core/delivery_integrity.py:242`
  (`def _latest_obligation(conn, session_key: str,`) — the `ORDER BY created_at DESC LIMIT 1` at `:251`
  must carry `AND content NOT LIKE 'Router failure: transcript divergence%'`
  (`FAILURE_PREFIX` at `core/delivery_integrity.py:397`).

Out of scope, and rejected if proposed: a shadow-local edit (overwritten at next sync, leaves
`/opt/data/plugins` seeding the same divergence on every profile) and any change to the compare path
(byte-identical on both trees — nothing to "get"). `gate/orchestration.py` returning the failure body on
divergence is correct behaviour; it only loops because its output is eligible as the next delivered side.

---

## G. ACCEPTANCE GATES THE LANE MUST MEET (measured, re-runnable)

| Gate | Target | Instrument |
|---|---|---|
| In-domain detection | >= 85% (shape detector today 61.2% / 61.9%) | corpus rows, label domain in `UNC_DOMAINS`, `declines=true` |
| Benign false positives | <= 5% (today 8.5%) | the 199-row benign stratum; gate on the ASK, rules on the BODY |
| Judge test-retest | >= 0.75 exact on 60 bodies | two `judge` runs at `temperature=0` |
| Cost | <= $0.0002/turn | `usage_ledger.read_records` filtered on `detail in {domain_gate,intent_judge}` |
| Added latency | 0 | PRE thread start returns; POST read is a non-blocking record read; p95 delta above noise = fail |

Per-rule firing counts are recorded too: an aggregate that passes while one rule never fires is the
failure mode these gates exist to prevent.

## H. SELF-CHECK — every citation above, re-runnable

    sed -n '783p'   gate/orchestration.py | grep -q '_dgate.start'
    sed -n '2020p;2022p' gate/orchestration.py
    sed -n '702p'   features/detection/intent_judge.py | grep -q 'def route_classes'
    sed -n '537p;539p;205p' semantic_classifier.py
    sed -n '629p'   features/detection/intent_judge.py | grep -q 'record_failure=False'
    sed -n '1277p'  gate/orchestration.py | grep -q 'def on_transform_llm_output'
    sed -n '745p'   gate/registration.py | grep -q 'transform_llm_output'
    sed -n '435p;476p' core/state.py
    sed -n '381p;442p;52p;56p;61p' features/detection/domain_gate.py
    sed -n '3007p;3020p;115p;119p;285p;3216p' gate/orchestration.py
    sed -n '438p'   debug_banner.py | grep -q 'def park_anchor_banner'
    sed -n '74p;275p' core/usage_ledger.py
    sed -n '28p;44p' lanes/registry.py; sed -n '90p' lanes/builtins.py
    sed -n '189p;133p' features/detection/rule_table.py
    sed -n '105p;242p;251p;397p' core/delivery_integrity.py
    sed -n '343p'   core/canonical.py | grep -q 'def rewrite_persisted_turn'
    sed -n '348p;366p;367p;388p;392p' tests/features/test_m41_05_rule_table.py
    sed -n '439p;403p' tests/features/test_m41_04_intent_judge.py
    sed -n '1576p;1667p;2105p' route_gate.py

Baseline citations for removed modules are read with
`git -C /opt/data/plugins/hermes_router show 0b5f394:<path> | sed -n '<n>p'` (R1, R3) and are labelled
as baseline in the text above.

## Decision log

- D1: contract on the EXISTING lane, four fields, one entry. One lane id must not be added; the routing
  lane id (`shadow`) and the envelope lane key (`uncensored`) are different namespaces and both already
  exist.
- D2: criterion on the per-session turn record, NOT `core/canonical.py`. Cost: no cross-process
  durability beyond state.db's own. Accepted: POST and PRE are the same process on this fleet.
- D3: `gate_abstain` hoisted to row 3. Cost: abstain outranks `softened` (a judged softening cannot
  route on an abstained gate). Accepted: the measurement never routed softened on an unflagged gate;
  the design's own text forbids reading abstain as safe.
- D4: `intent_classifier` retirement BLOCKED, not silently dropped. Deleting it removes PRE
  content-based lane declaration with no replacement in scope. Named exit: `lane_declaration`
  `pre_question` token, then delete.

## Open items
- O1: no standing absence test for `intent_probe` (R1) — add the grep-pattern test named there.
- O2: `softened` has never been persisted live; the lane can clear the corpus gates and still have no
  production instrument that notices it failing. M41-08's per-rule census is the minimum.
- O3: the `>=85%` target is offline against a contested label base (five-way judge/label agreement
  0.743, unadjudicated). The number is a gate, not a proof.

Reviewed-by: Goran (ratification required before any coder dispatch). Architect does not self-approve.
