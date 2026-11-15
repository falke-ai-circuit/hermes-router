# M50-11 — Structured front-end consults: schema-as-data + validator + cost model

UNIT M50-11 · owner architect · attempt 2 (attempt 1 api_1794696863_01443b1c died at idle 886 min, 73 rows, no artefact)
CLOSE HEADER
  artefact : /opt/data/tmp/r78u/M50-11_CONTRACT.md (mirror: /opt/data/plugins/hermes_router/docs/M50-11_CONTRACT.md)
  reviewed_by : reviewer — NOT YET DISPATCHED (this is a spec, not a deployment). verdict: PENDING-REVIEW
  suite    : python3 -m pytest tests/routing/test_m40_16_lane_contracts.py tests/features/test_m41_02_domain_gate.py -q
  open item: OI-1 — schema module not yet written (spec-only; coder unit follows). OI-2 — PRE schema field `basis` needs a vocabulary owner (see §5.3).

Goran directive being coded: "48k output on frontier is craziness — smaller better structured
responses with good SNR: minimum prose, maximum signal, structured responses based on
structured frontier PRE and POST asks, including on-demand."

The separation this contract enforces, stated once so no reader collapses it:
  max_tokens is HEADROOM FOR THINKING. It is not a promise of prose.
  The VISIBLE answer is a small, schema-bound, validated object.
M50-10 already bought the thinking (ADVISORY_TOKEN_FLOOR=48000, anchor_exec.py:1255).
M50-14/15 already make a retry descend effort instead of inflating the ceiling.
This contract touches NEITHER. It constrains what comes back, not what gets spent on thinking.

---

## 0. Verified evidence (attempt-1 findings re-checked against the tree, not re-derived)

| Claim | Verification |
|---|---|
| Visible advisory output ~5,100 chars | `anchor_chars=len(str(envelope.get("answer") or ""))` gate/orchestration.py:3811; attempt-1 samples 5,330 / 5,690 / 5,036 → median 5,330, mean 5,352 |
| 1.74 chars/token is the sizing ratio | anchor_exec.py:1441-1452 docstring: "Measured over 1,850 anchor consults: median 3,208 chars at median 1,846 billed output tokens -> 1.74 chars per output token" |
| Billed completion >> visible | anchor_exec.py:1046-1060 (finish=length path logs max_tokens_from/to), :1180 `anchor_consult_ok ... reasoning_tokens=%d completion_tokens=%s max_tokens=%s chars=%d` — the split is LOGGED, not inferred |
| decision.py is the ADAPTER, not the ask vocabulary | decision.py:2981 `_question = {"type": "choose_one_with_confidence", ...}` is the decision lane's own envelope; it is not PRE/POST consult vocabulary. Confirmed. |
| registry/builtins is the attachment point | lanes/registry.py:63-64 `pre_question`/`post_question`; lanes/builtins.py:92-93 `pre_question="uncensored_domain", post_question="uncensored_intent"` — the ONLY lane holding them |
| Floor exists, must not be duplicated | anchor_exec.py:1255 `ADVISORY_TOKEN_FLOOR = 48000`; :1376 `advisory_token_floor`; :1446 `advisory_max_tokens` |

New finding this attempt adds (load-bearing, changes deliverable 4):
`_validate_contract` (lanes/registry.py:128-160) REJECTS a non-gate contract that names any of
`_GATE_FIELDS = ("pre_question", "post_question", "replacement")`, because `lane_for_pre_question`
is first-match-wins and a second claimant would shadow `shadow`. `higher-pre` and `higher-post` are
`kind="consult"` / `kind="audit"` (lanes/builtins.py:129, 150). Therefore the response schema CANNOT
be attached by making the consult lanes declare `pre_question` — that is a registration error, not a
design choice. See §5 for the attaching mechanism that is legal on HEAD 13d331a.

---

## 1. Response schemas as data

Three schema objects, keyed by a closed ask-kind vocabulary. They are DATA (a dict of
frozen literals in a new module `response_schemas.py`), not code: no handler edits, no new
dispatch path, no per-lane branch. The validator reads them; the lane names one by token.

### 1.1 Ask-kind vocabulary (closed)

    ASK_KINDS = ("pre_consult", "post_consult", "ondemand")

### 1.2 PRE schema — `pre_consult`

Purpose: orient the main model BEFORE it starts. The frontier is not writing the answer; it is
naming the one thing that changes how the answer should be written.

```json
{
  "schema_id": "pre_consult_v1",
  "ask_kind": "pre_consult",
  "max_visible_tokens": 350,
  "shape": "object",
  "additional_properties": false,
  "required": ["verdict", "claim", "action", "confidence", "basis"],
  "properties": {
    "verdict":    {"type": "string", "enum": ["proceed", "reframe", "block"], "tokens": 8},
    "claim":      {"type": "string", "min_chars": 40, "max_chars": 120,
                   "rule": "the single load-bearing observation about THIS ask"},
    "watch":      {"type": "array", "items": "string", "max_items": 2,
                   "max_chars_per_item": 90, "optional": true,
                   "rule": "what would falsify `claim` while writing"},
    "action":     {"type": "string", "enum": ["answer_direct", "narrow_scope", "ask_user",
                                              "gather_evidence", "refuse_domain"], "tokens": 16},
    "confidence": {"type": "number", "min": 0, "max": 1, "tokens": 5},
    "basis":      {"type": "string", "enum": ["ask_only", "session_context", "material_given"],
                   "tokens": 20},
    "cite":       {"type": "string", "max_chars": 80, "optional": true,
                   "rule": "file:line or URL when claim rests on material, never a bare title"}
  }
}
```

### 1.3 POST schema — `post_consult`

Purpose: say what the delivered answer got wrong, or that nothing is wrong — in a form the main
model can act on next turn. Note `self_check` is a required BOOLEAN, not a prose assurance:
a prose "the answer is sound" is exactly the low-SNR token this contract removes.

```json
{
  "schema_id": "post_consult_v1",
  "ask_kind": "post_consult",
  "max_visible_tokens": 320,
  "shape": "object",
  "additional_properties": false,
  "required": ["outcome", "delta", "self_check", "next"],
  "properties": {
    "outcome":    {"type": "string", "enum": ["endorse", "amend", "reject"], "tokens": 8},
    "delta":      {"type": "string", "min_chars": 40, "max_chars": 160,
                   "rule": "the correction itself, imperative, no narration of how it was found"},
    "defect":     {"type": "string", "max_chars": 120, "optional": true,
                   "enum_or_text": true,
                   "rule": "named failure class: unsupported_claim | wrong_scope | missed_edge |
                            stale_fact | overclaim | none"},
    "self_check": {"type": "boolean", "tokens": 2,
                   "rule": "true iff the answer survives its own stated claim"},
    "next":       {"type": "string", "enum": ["deliver", "patch_next_turn", "escalate"], "tokens": 6},
    "cite":       {"type": "string", "max_chars": 80, "optional": true}
  }
}
```

### 1.4 On-demand schema — `ondemand`

Purpose: the user or the agent explicitly asked for a deeper consult. This is the ONLY variant
allowed a list, and it is capped at 6 items — the point of "on demand" is that a human chose to
spend, not that the lane may write an essay.

```json
{
  "schema_id": "ondemand_v1",
  "ask_kind": "ondemand",
  "max_visible_tokens": 900,
  "shape": "object",
  "additional_properties": false,
  "required": ["question", "items", "confidence"],
  "properties": {
    "question":   {"type": "string", "max_chars": 140,
                   "rule": "the ask RESTATED AS A QUESTION — this is the only place restatement is legal"},
    "items":      {"type": "array", "min_items": 1, "max_items": 6,
                   "item_required": ["point", "why"],
                   "point": {"max_chars": 110}, "why": {"max_chars": 90},
                   "optional_item": ["cite"]},
    "confidence": {"type": "number", "min": 0, "max": 1},
    "unknowns":   {"type": "array", "items": "string", "max_items": 2,
                   "max_chars_per_item": 90, "optional": true}
  }
}
```

### 1.5 Why 350 / 320 / 900 visible tokens (the number is derived, not chosen)

Field caps sum: PRE 425 chars + ~70 chars JSON syntax = ~495 chars; at the measured 1.74
chars/token that is 284 tokens; rounded to 350 gives 23% slack for a longer `claim` inside its
cap and for unicode. POST 490 chars -> 282 tokens -> 320 gives 13% slack. On-demand 1,020 chars
+ 110 syntax -> 718 tokens -> 900 gives 25% slack (6 items x 2 fields is the worst case).

The slack is deliberate and bounded: a schema that returns finish=length is a schema that has
re-created the M50-10 empty-body bug at a smaller size. 350/320 are ~12% / 11% of today's ~2,930
visible tokens; 900 is 31%. The gap between the PRE/POST ceiling and the on-demand ceiling is the
on-demand premium, and it is the only place the lane may spend it.

`max_visible_tokens` is NOT sent as `max_tokens` (that would starve thinking, and M50-10 exists
precisely because that mistake cost real money). It is a VALIDATED POST-CONDITION, enforced by
§3. The request keeps its 48k-floor allowance; the answer is judged against 350.

---

## 2. SNR rule as testable constraints

The validator REJECTS (not "warns about") the following. Each is a named predicate with a
defined test, because a rule that cannot fail a test is decoration. Order matters: first
trigger aborts the object and enters repair (§4).

| # | Predicate | Test (concrete) | Why it is a defect, not taste |
|---|---|---|---|
| R1 | `preamble` | first non-space char run matches `^(sure|certainly|of course|absolutely|here('s| is)|okay[,.]|great[,.])` (case-insensitive) on the whole body OR on any string field | It is pure ceremony. The object already declares what it is. |
| R2 | `restates_ask` | token-set Jaccard(field, ask) >= 0.60 on any string field >= 40 chars, EXCEPT `ondemand.question` | The reader has the ask. Restatement is the single largest block of dead tokens in the 5.1k envelope. |
| R3 | `method_narration` | matches `\b(I (will|am going to|need to)|let me|first,? (I|we)|step \d|in order to (answer|address)|to (answer|address) this)\b` | The reader wants the conclusion. How it was reached is not the deliverable. |
| R4 | `hedging_alone` | field is entirely `^(it depends|there are several|this is nuanced|various factors|it'?s complicated|generally speaking)[.!]?$` | A hedge with no variable named carries zero information; name the variable or omit the field. |
| R5 | `decorative_header` | body matches `(?m)^#{1,3}\s` or `(?m)^\*\*[^*]+\*\*:?$` when total body lines <= 5 | A header on a five-line answer is overhead larger than the answer. |
| R6 | `trailing_offer` | matches `(would you like|let me know|feel free to|shall I|happy to (elaborate|expand))[^.]*$` | The consult cannot receive a follow-up; the offer is unanswerable text. |
| R7 | `meta_commentary` | matches `\b(as an AI|I (don'?t|do not) have (access|the ability)|my (training|knowledge) (cutoff|data))\b` | Capability disclaimers belong in the lane's skip path, not in a paid answer. |
| R8 | `duplicate_field` | two string fields whose token sets are >= 0.8 identical | Same finding twice is one finding and one wasted field. |
| R9 | `cap_overflow` | any field beyond its `max_chars`, or item count beyond `max_items` | Caps are the budget. |
| R10 | `schema_violation` | missing required field, wrong type, enum value outside the closed set, `additional_properties=false` violated | Structured means parseable by a consumer that never reads prose. |

Explicitly NOT rejected: imperatives, disagreement, profanity, domain assessment, a short answer
that is short because the finding is short. SNR is signal-per-token, not politeness-per-token.

Measurable acceptance (the number the reviewer can re-run): after rollout, median visible chars
for `pre_consult` <= 620, for `post_consult` <= 560, and `reject_rate` (R1-R10) < 8% of consults.
A reject rate above 8% means the ASK is mis-specified, not that the model is bad — the repair
budget is a symptom channel, and §6 keeps it observable.

---

## 3. Validator contract

One pure function, no I/O, no model call:

    validate(content: str, schema: dict, ask: str) -> Verdict
    Verdict = (ok: bool, obj: dict|None, errors: list[Err], profile: dict)

`Err = (rule: str, field: str, detail: str)` — rule from the §2 table plus `parse`, `cap`, `type`.

Steps, in this order (order is part of the contract — cheapest and most decisive first):
1. `parse` — strip a single fenced block if present, then `json.loads`. No lenient repair, no
   trailing-comma forgiveness: a parse failure is the honest signal that the model wrote prose.
2. `schema_violation` — required/type/enum/additionalProperties.
3. `cap_overflow` per field.
4. `max_visible_tokens` — tokens estimated as `len(json.dumps(obj, ensure_ascii=False)) / 1.74`
   (same ratio the lane sizes with, so the number is comparable to the one in §1.5).
5. R1-R8 content predicates over every string field and over array items.
6. Emit `profile` = {"chars": n, "est_tokens": t, "rules_fired": [...]} on BOTH ok and fail —
   the profile is the measurement instrument for §6 and for the 8% acceptance number.

### 3.1 ONE bounded repair retry

Repair is a single additional call. It reuses the SAME envelope (no new context, no new ask) and
appends exactly:

    [SCHEMA REPAIR — your previous reply failed validation. Reply again with ONLY the JSON object.]
    [schema]: <json schema, compact>
    [errors]: <rule: field: detail, one line each, max 6>

Constraints on the repair call, stated so the coder cannot improve it into a second full consult:
- effort DESCENDS one rung (M50-14/15 already own the descent helper — reuse it, do not write a
  second one). A repair is a serialisation job, not a reasoning job.
- `max_tokens` is NOT raised. The visible answer is smaller than the failed one by construction.
- Hard cap: at most ONE repair per consult. Tracked per (session, task, ask_kind) so a retry loop
  cannot re-enter it (consult_budget.py already caps 1 PRE + 1 POST per task; the repair must not
  consume that budget — it is not a consult, it is a serialisation of one already paid for).
- The repair is recorded with `trigger='schema_repair'` and the fired rules, so the §6 numbers
  are auditable rather than inferred.

### 3.2 Fallback when the repair also fails — never a withhold notice

Goran's unc-lane rule: the system does not decline to advise because its formatting failed. The
fallback ladder, first applicable wins:

F1 **Extractive projection.** Take the best available text — repair output if non-empty, else the
   original — and project it into the schema shape mechanically: `verdict` = default `"proceed"`
   only if no signal, else inferred from keyword evidence; `claim` = the FIRST sentence <= 120
   chars that is not rejected by R1/R3 (scanning sentences in order); `confidence` = 0.4 (a
   flattened prior, never invented precision); `basis` = `"ask_only"`. Stamp `"degraded": true`
   and `"degraded_reason": "<first rule fired>"`.
F2 **Raw tail.** If no sentence survives F1, inject the last <= 400 chars of the raw content
   verbatim under `"degraded": true, "raw": "..."`. Unglamorous, but a partial finding the main
   model can read beats a schema-shaped emptiness.
F3 **Silent skip.** Only when the content is literally empty (the M50 empty-body case). The lane
   injects nothing and the turn proceeds exactly as it does today on `route_skipped`. This is
   NOT a withhold notice: no text is emitted anywhere claiming advice was withheld, the user sees
   no artifact of the failure, and the counter `schema_degraded_empty` records it for §6.

Forbidden, by name: emitting "advisory unavailable", "consult failed", a placeholder object with
empty strings, or any banner telling the reader that formatting prevented advice. F3 is silence,
which is the existing and correct behaviour for a consult that produced nothing.

---

## 4. Composition with the existing vocabulary

The requirement: the schema attaches to the lane contract's ask vocabulary, NOT a second dispatch
path. HEAD constraint (verified, §0): consult/audit lanes may hold NO gate field, so the token
cannot be `pre_question`.

### 4.1 The attaching mechanism (additive, backward-compatible)

`LaneContract` (lanes/registry.py:40-66, frozen dataclass) gains ONE optional field:

    response_schema: str = ""

Default `""` keeps every existing row byte-identical, including the equality assertion at
tests/features/test_m41_02_domain_gate.py:380 (`resolve_contract("decision") == LaneContract(...)`)
— both sides default, so equality holds. This is the temporal-vector requirement: no consumer
breaks, and a lane that never declares a schema behaves exactly as today.

`_validate_contract` gains one rule, same fail-loud style as the existing ones: a non-empty
`response_schema` must be a member of `SCHEMA_TOKENS` (the closed set of `schema_id` values in
`response_schemas.py`). A typo must fail at import, never silently fall back to prose — that is
the exact failure class this unit exists to remove.

### 4.2 Token resolution (no second dispatch path)

    SCHEMA_TOKENS = ("pre_consult_v1", "post_consult_v1", "ondemand_v1")
    ASK_KIND_TO_SCHEMA = {"pre_consult": "pre_consult_v1",
                          "post_consult": "post_consult_v1",
                          "ondemand": "ondemand_v1"}

The engine already resolves ask tokens per seam. The schema is resolved by the SAME seam, one line
later: PRE seam asks `schema_for_seam("PRE", contract)` -> `contract.response_schema` if set, else
`ASK_KIND_TO_SCHEMA["pre_consult"]`; POST mirrors it. Gate lanes (`shadow`) keep their existing
`pre_question`/`post_question` untouched and simply carry `response_schema=""` — their answers are
already JSON with their own validator (domain_gate.py:343, intent_judge.py:128), and this contract
does not touch them.

Assignment: `_HIGHER_PRE.contract` gets `response_schema="pre_consult_v1"`,
`_HIGHER_POST` gets `"post_consult_v1"`, on-demand resolves at the declared-user seam
(`route_gate.py:101` DECLARED_USER_PHRASES) to `"ondemand_v1"`. Three data rows, zero handler
edits, zero new branches.

### 4.3 What the ask text becomes (this is where SNR actually moves)

The schema is useless if the ask still says "write an advisory". The PRE/POST ask prompt gains a
fixed prefix (one constant, not per-lane text):

    Reply with ONLY this JSON object. No preamble, no restatement of the ask, no narration.
    Every field is capped. If a field has nothing to say, omit it (optional) or choose the
    enum value that says so. <schema compact json>

This is the mechanism behind Goran's directive: structure in the ask produces structure out.

---

## 5. Cost model (arithmetic shown, thinking separated)

Ratios and prices used, all named so the reviewer can substitute: 1.74 chars/token (measured,
anchor_exec.py:1441), $3/M input, $15/M output (frontier tier; the RELATIVE deltas below do not
depend on the absolute price), 40k input tokens per consult (bounded_replay, conductor's
measurement), 17,510 billed completion tokens to produce a 3,052-char success (M50-01/06).

### 5.1 Visible tokens, before vs after

| Lane | Visible today (chars -> tokens @1.74) | Visible after (cap -> est) | Delta visible |
|---|---|---|---|
| PRE | ~5,100 -> ~2,931 | 350 | -2,581 (-88%) |
| POST | ~5,100 -> ~2,931 | 320 | -2,611 (-89%) |
| On-demand | ~5,100 -> ~2,931 | up to 900 | -2,031 (-69%) |

Token estimate per schema: PRE 425 chars + 70 syntax = 495 -> 284; POST 490 -> 282; on-demand
1,020 + 110 -> 718. Caps in §1.5 sit 13-25% above these.

### 5.2 Thinking share, shown separately (this is the honest part)

Today's successful consult: completion_tokens 17,510, of which visible 3,052/1.74 = 1,754.
  thinking = 17,510 - 1,754 = 15,756  -> 90.0% of billed completion tokens.

After (PRE): billed = thinking + visible = 15,756 + 350 = 16,106  -> 97.8% thinking.
Output cost today 17,510 * $15/M = $0.2626; after 16,106 * $15/M = $0.2416; saving $0.0211
(8.0%) per consult. Same shape for POST: 16,076 -> $0.2411, saving $0.0215.

Stated plainly because it is the load-bearing truth of this unit: the schema does NOT reduce the
dominant term. Thinking is 90% of billed output before and 98% after; the visible reduction buys
8% of output dollars. Anyone selling this as a 12x output saving is selling the char count, not
the invoice. The visible cap's real leverage is §5.3.

### 5.3 Effect on the dominant cost — input replay

bounded_replay ships 24 turns / 40,000 input tokens per consult. The injected advisory is not
read once: it lands in the transcript and is replayed on every later turn of that bounded window.
Assume 8 turns remain after injection (conservative; 24 is the window ceiling):

  today: 5,100 chars / 1.74 = 2,931 tok/turn * 8 = 23,448 input tokens of advisory residue
  after: 1,100 chars / 1.74 =   632 tok/turn * 8 =  5,057 input tokens
  saved: 18,391 input tokens * $3/M = $0.0552 per consult
  at the full 24-turn window: 70,345 -> 15,172 tokens, saving $0.1655

Against the 40k consult input ($0.12), the advisory residue today is 59% of the entire input
budget at 8 turns — the advisory is not a rounding error on input, it is the largest single
item we add to it. At 24 turns it is 176% of it, i.e. the advisory residue costs more than the
consult's own context.

Combined per-consult effect at 8 replay turns: output -$0.0211 + input -$0.0552 = -$0.0763
(-62% of the $0.1231 that a 8-turn-lifetime consult costs in output+residue). Bounded-replay
input cost itself is unchanged: the schema shrinks what WE add, not what the turn already carries.

### 5.4 Where the 48k floor lands in this model

Unchanged, deliberately. The floor buys thinking; the schema constrains visible output. A consult
after this contract may still bill 48k of headroom; what changes is that the 48k is no longer
spent producing 5,100 chars of prose. If §6 telemetry later shows thinking collapsing under a
schema-bound ask (plausible: a schema narrows the search), the floor can be re-ruled on OBSERVED
reasoning_tokens (anchor_exec.py:1301) — not on this document's estimate. The schema must not be
used as an argument to lower the floor before that measurement exists.

---

## 6. Anti-degenerate check — a conforming answer is still useful

A terse schema that discards the finding is a failure. Proof obligations, per lane:

**PRE minimum useful content.** A PRE advisory is useful iff it changes what the main model does
next. The schema guarantees this by requiring, jointly:
  1. `claim` >= 40 chars (a claim that cannot fit 40 chars is not a claim, it is a label);
  2. `action` from a closed set — the reader gets an INSTRUCTION, not an impression;
  3. `confidence` — the reader can discount it;
  4. `basis` — the reader knows whether the claim rests on the ask alone or on material, which is
     what decides whether to trust or verify it.
Minimum: 40 chars of claim + one action + one confidence + one basis = ~62 visible tokens. The
cap is 350, i.e. 5.6x the floor. Degenerate answers are therefore not forced by the cap; they are
only possible if the FRONTIER has nothing to say, which §4.3's ask (not the schema) is responsible
for eliciting.

**POST minimum useful content.** Useful iff it tells the main model whether to change anything.
Guaranteed by `outcome` (endorse/amend/reject) + `self_check` (boolean) + `delta` >= 40 chars when
`outcome != endorse` (conditional requirement — an endorse with no delta is legal and cheap; an
amend without a delta is rejected as `schema_violation`, because "amend" with no amendment is
noise). Minimum: 8 + 2 + 40 chars + enum = ~34 tokens against a 320 cap (9.4x).

**On-demand minimum.** Human-asked, so the bar is different: at least one `item` with both `point`
and `why`. `why` is required precisely to stop the failure mode where a list of assertions arrives
with no reasoning and the reader must re-derive it. Minimum 1 item = ~115 chars against a 1,020
char worst case.

**The discard test (the one that matters).** Take the attempt-1 sample advisory and ask: does the
schema keep the finding? The 5,330-char envelope's signal is, in practice, one orientation claim
plus one caution plus a confidence. The PRE schema holds all three (claim, watch[<=2], confidence)
in <= 425 chars. What it discards is restatement, method narration and hedging — the three things
R2/R3/R4 name. If a particular consult's value lives in a 900-word walkthrough, it is an
ON-DEMAND consult, and §1.4 gives it a lane and a budget. The failure mode this paragraph
prevents: using the PRE cap as an excuse to suppress a finding that needed the on-demand lane.

---

## 7. Failure paths (designed first, per component)

| Component | Failure | Path |
|---|---|---|
| Schema registry | unknown token at registration | ValueError at import (`_validate_contract`), lane never loads — fail-loud, no prose fallback |
| Schema registry | schema module unimportable | contract field read defensively -> `""` -> today's prose advisory. Degraded to CURRENT behaviour, never to silence |
| Validator | raises on hostile input | whole validator wrapped; exception = one `schema_validator_error` counter + F2 raw tail. Never breaks the turn |
| Repair | provider error / timeout | fall to F1 immediately; repair failure is not a consult failure |
| Repair | returns empty | F1 |
| Projection (F1) | no sentence survives | F2 |
| All of the above | content literally empty | F3 silent skip + `schema_degraded_empty` |
| Cost | schema makes thinking grow | visible cap does not raise `max_tokens`; the 48k floor is untouched, so the worst case is today's spend |

Every path is observable: `schema_ok`, `schema_repair`, `schema_degraded_extractive`,
`schema_degraded_raw`, `schema_degraded_empty`, plus the per-consult `profile` (§3 step 6). No
silent degradation — the §2 8% acceptance number is only meaningful if the counters exist.

---

## 8. Implementation phases (coder starts at phase 1 without reading 2-4)

P1 `response_schemas.py` — three dicts + `SCHEMA_TOKENS` + `ASK_KIND_TO_SCHEMA` + `schema_for(kind)`.
    No imports from lanes/. Test: token totality, every required field has a type, caps > 0.
P2 validator + §2 predicates in the same module (`validate`), pure function, no model access.
    Test: one fixture per rule R1-R10 (10 fixtures that MUST fail, 1 that MUST pass).
P3 `LaneContract.response_schema` field + `_validate_contract` membership rule + 3 builtins rows.
    Test: existing m41_02 equality still holds; unknown token raises; gate lanes unaffected.
P4 wiring at the seam: ask-prompt prefix (§4.3), repair call (reusing the M50-14/15 descent
    helper), F1/F2/F3 ladder, counters. This is the only phase that touches anchor_exec/gate.

P1-P3 are shippable inert: they add data and a validator nobody calls. P4 is the only behaviour
change, and it is reversible by setting `response_schema=""` on the three rows.

---

## 9. Future projections (1 month / 6 months / 1 year)

1mo: PRE/POST conform at >= 92%; median visible 350-620 chars; the 8% reject rate is the number
reviewed, not the char count. Expect OI-2 (basis vocabulary) to be the first patch — field
vocabularies always lag the first real consults.

6mo: the `profile` telemetry has accumulated enough rows to re-price thinking per ask-kind. If
schema-bound asks demonstrably think less, the 48k floor becomes over-insurance on PRE/POST and
the ruling can descend per lane (M50-14/15's descent helper already exists). If they do not, the
floor stays and this contract's saving remains the input-residue one (5.3) — which is the larger
number anyway.

1yr: the schema vocabulary becomes the fleet's consult ABI. A second consumer (analyst, valmet)
reads consults without prose parsing; new ask-kinds are added as DATA rows, and the first
temptation will be a 12-field schema for a new lane. The guard: every field must name the decision
it changes or it does not ship — that is the same rule that produced these three schemas, and it
is the only thing that keeps this from becoming the next 5k envelope with braces.

---

## 10. Decision log

| # | Decision | Rationale | Cost accepted |
|---|---|---|---|
| D1 | Schema as data keyed by ask-kind, not per-lane code | Goran: structured asks produce structured answers; data rows are reviewable in a diff, branches are not | one more module to keep honest |
| D2 | New `LaneContract.response_schema` field instead of overloading `pre_question` | registry REJECTS gate fields on consult lanes (first-match shadowing); overloading is a registration error | one additive dataclass field |
| D3 | Caps 350/320/900 visible tokens | derived from field caps at measured 1.74 c/tok + 13-25% slack | visible answer is ~11% of today's |
| D4 | max_tokens untouched; cap enforced as post-condition | M50-10's floor buys thinking; sending 350 would re-create the empty-body bug | no saving on thinking term |
| D5 | ONE repair, descending effort, no max_tokens raise | repair is serialisation, not reasoning; reuse M50-14/15 helper rather than a second descent | 1 extra call on <= 8% of consults |
| D6 | Fallback ladder F1/F2/F3, never a withhold notice | unc-lane rule: formatting failure must not become refused advice | degraded objects carry `degraded:true`, visibly second-class |
| D7 | Conditional `delta` requirement on POST | "amend" with no amendment is noise; "endorse" with no delta is cheap and legal | one conditional in the validator |
| D8 | Gate lanes unchanged | domain_gate/intent_judge already emit validated JSON with their own contracts | schema vocabulary is consult-scoped for now |

---

## 11. Open items

OI-1 The three schemas are SPEC ONLY on this unit; `response_schemas.py` does not exist yet.
     Coder unit starts at P1. Nothing in this document was implemented — architect is read-only.
OI-2 `basis` enum values (`ask_only`, `session_context`, `material_given`) and `defect` classes
     need an owner in the vocabulary review; a field enum with no consumer drifts.
OI-3 Acceptance numbers in §2 (620/560 chars, 8% reject) are targets to be measured on the first
     200 consults after P4, not claims about today.
