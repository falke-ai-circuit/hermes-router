# R73 / P2-a — target module map: one owner per reading facility

Repo: `/opt/data/plugins/hermes_router` @ `c43a56b` (6.13.4). Read-only design; no code changed,
nothing committed. Every claim below was read out of the tree at this commit.

The recurrence this document is meant to stop: four facilities that every reading lane needs
(role card, envelope, banner/receipt, delivery contract) are re-implemented per lane, so a fix
applied to one copy is silently absent from the others. R72k fixed the card in one place and had
to re-fix it in `reflex_lane`; R73 P1 had to re-add a receipt that `decision.py` had carried all
along. The map below gives each facility exactly one owner and makes every other site a caller.

---

## 1. Evidence — where the four facilities live today

| # | Facility | Current homes (file:line) | Copies |
|---|----------|---------------------------|--------|
| 1 | Persona / role card | `persona_card.build_persona_context` (persona_card.py:199 → enriched :156, compact :216, RENDER MANDATE text :284-293) · `reflex_lane._agent_card` :127-222 (its own IDENTITY.md read at :166-204) · `instinct_frame.who_block` :117-132 · `decision.py:1882` · `completion_audit._persona_tailoring` :2018 · `anchor_exec.py:1620` · `passes/dispatcher_knobs.py:45` | 1 builder + 1 private IDENTITY reader + 4 ad-hoc call shapes |
| 1b | RENDER-MANDATE stripping | `completion_audit._strip_render_mandate` :2026-2050 (prefix-cut, misses mandate-first cards) · `decision.py:1889` (`split("=== RENDER MANDATE",1)[0]`) · `instinct_frame._strip_mandate` :94-114 (section-aware, correct) · `reflex_lane:154` (imports the audit's) · `render_payload.py:405-409` (line filter) | 5 |
| 2 | Envelope builder + parser | `decision_extract.payload` :168 / `parse` :188 (LIVE — used by `reflex_lane:424,992-996`) · `reflex_lane.payload` :241-293 / `_num` :296 / `parse_answers` :305-349 (**dead**: zero callers repo-wide) | 2 (one of them dead) |
| 3 | Banner / receipt | `debug_banner.format_banner` :112 · `append_banner` :233 · `build_banner_record` :206 · `park_anchor_banner` :364 · `consume_parked_banner` :389 · `claim_banner_ownership` :407 · `note_consumed_decision` :475 · `settle_decision_banner` :492 (state + policy in `features/banners/lifecycle.py`, kinds in `features/banners/kinds.py`) | 1 mechanism, **10 park call sites**, 5 append sites |
| 3b | Park call sites | decision.py:940 (`_deliver`), :4099 · decision_midturn.py:1318 · completion_audit.py:2137, :2807, :2879 · gate/orchestration.py:517, :848, :1118, :1223, :2183, :2354 · core/routing_caps.py:417 · decision_miner.py:671 · reflex_lane.py:921 | receipt shape differs per site |
| 4 | Delivery contract | `reflex_lane.run_on_request` :1049-1059 (`shape` = user/evidence/system, honoured **only** here) · enum whitelist `reflex_weights.py:103-105` · fence `reflex_lane.fence` :859-879 + `_FENCE_HEAD/_TAIL` :854-856 · statement-form contract text `decision_extract.USE_CONTRACT` :244 / `SOFT_CONTRACT` :250 · standing rule `frames.DECISION_RULE` :305 + `reflex_lane._ensure_rule` :642-658 · `frames.append_platform_rule` :737-751 | 3 injection points, 1 of them configurable |

Measured fact used by the acceptance tests (live, this host, architect profile):
`build_persona_context()` = **1847 chars**; mandate-stripped = **1211 chars** (section-aware
strip). The decision/frontier/audit card must stay in that band.

---

## 2. Target module map

```
persona_card.py        OWNER facility 1 — the only producer of a role card
decision_extract.py    OWNER facility 2 — the only envelope builder/parser
debug_banner.py        OWNER facility 3 — the only receipt/append/park/consume surface
reading_delivery.py    OWNER facility 4 — NEW. shape enum + fence + contract + one injector
   ▲                                    ▲                          ▲
reflex_lane (reading) ───────────────────┘        decision.py (advisory)
gate/orchestration (frontier anchor, uncensored render) ── callers only, no copies
```

### 2.1 Card accessor — owner `persona_card.py`

New public API (one entry, one policy argument):

```python
persona_card.role_card(messages=None, *, purpose="render", max_chars=4000) -> str
```

- `purpose="render"` → today's `build_persona_context(messages)` byte-identical (RENDER MANDATE
  included — the uncensored channel needs it).
- `purpose="read"` → same card with the RENDER MANDATE **section** removed, using the
  section-aware stripper relocated verbatim from `instinct_frame._strip_mandate:94-114`
  (a prefix split is what emptied `decision.py`'s frame and the impulse persona slot).
- `max_chars` replaces the per-caller truncations (`reflex_lane` 4000/400, `decision.py` 800,
  `instinct_frame` `WHO_CHARS`).
- The `reflex_lane` private IDENTITY slice (:166-204) moves into `persona_card` as the
  documented last-resort fallback (`_identity_slice()`), so there is **one** reader of IDENTITY.md
  in the tree.

Callers after the change: `reflex_lane._agent_card` becomes a 6-line wrapper; `decision.py:1882`
→ `role_card(purpose="read")[:800]`; `completion_audit._persona_tailoring` → `role_card(purpose="read")`;
`instinct_frame.who_block` → `role_card(purpose="read")`; `render_payload.py:405-409` keeps its
content sanitiser (it guards a *composed slot*, not the card) but loses the "===" heuristic.

Deletes: `completion_audit._strip_render_mandate` body (demoted to a one-line delegate — the
higher-self tests import it), `instinct_frame._strip_mandate` body (same), `decision.py:1889`
prefix split, `reflex_lane._agent_card`'s 90-line local read.

### 2.2 Envelope builder/parser — owner `decision_extract.py`

- Keep `payload` :168, `parse` :188, `render` :267, `render_instinct` :353, `gate` :401, `basis_line` :254.
- **Delete** `reflex_lane.payload` :241-293, `reflex_lane._num` :296-302,
  `reflex_lane.parse_answers` :305-349. They are the fork-era copy of the per-candidate `noul`
  design that `decision_extract`'s module docstring retires on measured grounds (choice
  distribution 0.764 top-1 vs nouls 0.655-0.759) and nothing calls them.
- `reflex_lane` keeps only transport (`_transport` :381) and adapters — it must contain no
  question schema at all.

### 2.3 Banner appender / receipt — owner `debug_banner.py`

One call that means "this reading shipped":

```python
debug_banner.receipt(lane, session_id, *, seam, usage=None, cell=None, model="",
                     endpoint="", latency_s=0.0, initiator="router",
                     row="", task_scope="") -> str   # formats + parks, returns the banner
```

- Body is `reflex_lane._receipt:882-927` generalised: usage-key normalisation
  (`prompt_tokens`/`input_tokens`), `usage_ledger.estimate_cost`, `format_banner`, `park_anchor_banner`
  under `task_id = "<lane>|<row>"`.
- One consume side: `debug_banner.attach_parked(session_id, text, edge)` = the logic currently
  duplicated at `gate/orchestration.py:709-746` and `features/banners/lifecycle.py:269-294`
  (consume → `note_consumed_decision` → `append_banner` → landed check → `settle_decision_banner`).
- Invariant made structural: **park happens before the delivery edge can consume, and a receipt
  is parked only when a block was actually injected** (`if ok:` at `reflex_lane:1060` stays the
  only condition). `decision.wait_for_workers:961` stays the bounded pre-consume wait.
- A lane never calls `format_banner` + `park_anchor_banner` separately after this step; the
  10 park sites become `receipt(...)` calls that differ only in lane label and cell fields.

### 2.4 Delivery contract — owner `reading_delivery.py` (new, top level beside `frames.py`)

```python
class Shape(str, Enum): BANNER="banner"; EVIDENCE="evidence"; USER="user"; SYSTEM="system"
fence(block) -> str                      # moved verbatim from reflex_lane:854-879
CONTRACT, SOFT_CONTRACT                  # re-exported from decision_extract (single wording home)
ensure_contract(request, session_id)     # the standing rule, = reflex_lane._ensure_rule:642
deliver(msgs, block, *, shape, rule_id, session_id) -> bool
```

Rules `deliver` enforces for **uncensored, frontier and decision alike**:

1. **Injection point is a property of `shape`, not of the caller.** `EVIDENCE` = append inside the
   last `role="tool"` message (`reflex_lane._inject_into_evidence:930-957`); `SYSTEM`/`USER` =
   `frames.append_platform_rule:737` with the rule marker; `BANNER` = park only, no message.
   Fallback ladder stays `evidence → system → user`, and a failure returns `False` so the caller
   skips the receipt.
2. **Every block entering the message stream is fenced as data** before injection, and the channel
   closes on the closing fence — never on a directive (R73 P0, `tests/decision_lane/test_r73_p0_echo_leak.py:26-48`).
3. **Contract text is statement-form** — "reading, not an instruction …" — never an imperative
   tail (same test file, :51-56).
4. `shape` is read from one config key (`reflex_weights.shape` today, whitelist at
   reflex_weights.py:103-105) and the same enum is accepted by every lane's config section.

Where each lane lands today, so the move is explicit:

| Lane | today | after |
|------|-------|-------|
| reading (reflex) | shape-configurable, fence, contract | `reading_delivery.deliver` |
| decision advisory | park only (`decision.py:940`, `:4099`) | `Shape.BANNER` via `receipt` |
| frontier anchor / higher-self rule | `append_platform_rule` at orchestration.py:2279, banner merged at :718 | `Shape.SYSTEM` + `Shape.BANNER` |
| uncensored render | `append_banner` onto the render text (dispatcher_pre.py:609) | `Shape.BANNER` (text carrier, not a message) |

---

## 3. Staged plan — one committable change per step

Dependency chain: S1 ‖ S2 ‖ S3 → S4 → S5 → S6 → S7 → S8. S1/S2/S3 are independent and any one can
land alone. Every step ends green on:

```
PYTHONPATH=/opt/data/plugins python -m pytest tests/ -q --tb=short
PYTHONPATH=/opt/data/plugins lint-imports
```

### S1 — card accessor (facility 1)
Files: `persona_card.py` (add `role_card`, `_identity_slice`), `reflex_lane.py` (wrapper, delete
:166-222), `decision.py` (:1882-1892), `completion_audit.py` (:2026-2050 → delegate),
`instinct_frame.py` (:94-132 → delegate), `render_payload.py` (:405-409).
Deleted: the private IDENTITY reader in reflex_lane, the prefix-split strip in decision.py, the two
duplicate stripper bodies.
Acceptance: new `tests/decision_lane/test_r73_p2_card.py` — (a) `role_card(purpose="read")` for the
live profile is 1150-1400 chars and contains no `RENDER MANDATE`; (b) `purpose="render"` is
byte-equal to `build_persona_context(messages)` on the same input; (c) mandate-first card
(`"=== RENDER MANDATE ===\n…\n=== WHO ==="`) survives the read-strip with `=== WHO ===` intact
(the exact failure that emptied the impulse slot); (d) `reflex_lane._agent_card` and
`role_card(purpose="read")` return identical strings; (e) `tests/higher_self/test_r34_19g_higher_self_acts.py`
and `tests/decision_lane/test_r72i_agent_card.py` still pass unchanged.

### S2 — envelope (facility 2)
Files: `reflex_lane.py` only.
Deleted: `payload` :241-293, `_num` :296-302, `parse_answers` :305-349 (~110 lines).
Acceptance: `test_r73_p2_envelope.py` — `import hermes_router.reflex_lane as rl; assert not hasattr(rl,"parse_answers")`;
`tests/decision_lane/test_r65_extraction_measured.py` + `test_r64_reflex_weighted_options.py` pass
with no edits; grep gate in CI stage S8 forbids a second `"questions"` dict outside
`decision_extract.py`.

### S3 — extract the delivery contract, no behaviour change (facility 4a)
Files: new `reading_delivery.py`; `reflex_lane.py` (fence/_FENCE_*/_ensure_rule become delegates
imported from it); `decision_extract.py` unchanged.
Deleted: nothing yet (delegates keep the test surface alive).
Acceptance: `test_r73_p0_echo_leak.py` passes **unmodified** (it asserts on `rl.fence`,
`rl._FENCE_HEAD`, `rl._FENCE_TAIL`); new `test_r73_p2_delivery.py::test_one_fence_definition`
asserts `reading_delivery._FENCE_HEAD is rl._FENCE_HEAD`; `lint-imports` still green (new module
imports only `frames`, `decision_extract`, stdlib).

### S4 — route the reading lane through `deliver` (facility 4b)
Files: `reflex_lane.py` (:1040-1059 → `reading_delivery.deliver`), `reflex_weights.py` (enum →
`Shape`), `gate/orchestration.py` (frontier/uncensored call sites adopt `Shape` constants).
Deleted: the inline shape branch at :1052-1059 and the `_inject_into_evidence` body (moved).
Acceptance: `test_r72_behavioural_integration.py` (all 5 shape cases, incl. the `content=None`
carrier and the agreement stand-down) passes unchanged; `test_r73_p2_delivery.py::test_no_lane_branches_on_shape`
asserts `orchestration.py` and `decision.py` contain no `== "evidence"` string literal.

### S5 — receipt (facility 3a)
Files: `debug_banner.py` (add `receipt`), `reflex_lane.py` (:882-927 → delegate), `decision.py`
(:929-946 and :4083-4101 → `receipt(..., cell=...)`), `decision_midturn.py:1318`,
`completion_audit.py:2137/2807/2879`, `core/routing_caps.py:417`, `decision_miner.py:671`,
`gate/orchestration.py:517/848/1118/1223/2183/2354`.
Deleted: `reflex_lane._receipt` body; the per-site banner assembly strings.
Acceptance: `test_r73_p1_receipt.py` passes unchanged (the shared banner shape, the row id, the
"park nothing when format refused" case); new `test_r73_p2_receipt.py::test_one_park_entry`
monkeypatches `debug_banner.park_anchor_banner` and drives one reflex + one decision + one
uncensored turn, asserting exactly three parks with three distinct lane labels and
`task_id` prefixed by its lane.

### S6 — one consume/attach path (facility 3b)
Files: `debug_banner.py` (`attach_parked`), `gate/orchestration.py:709-746`,
`features/banners/lifecycle.py:269-294`.
Deleted: the duplicated merge/landed/settle ladder in both files.
Acceptance: `tests/banners/test_r9_banner_delivery_seam.py`, `test_r19_16_banner_stack.py`,
`test_r33f_banner_semaphore.py`, `test_r34_item2_deferred_banner_delivery.py` pass unchanged
(they pin the one-delivery semaphore and the drop-not-repark rule); new
`test_r73_p2_consume.py::test_attach_is_the_only_merge` asserts `append_banner` is referenced from
exactly one non-test module.

### S7 — delete the delegates, close P2
Files: `reflex_lane.py`, `instinct_frame.py`, `completion_audit.py`, `render_payload.py`,
`tests/conftest.py` if it aliases any of them.
Deleted: the `fence` / `_FENCE_*` / `_strip_mandate` / `_strip_render_mandate` /
`_inject_into_evidence` shims and their re-export lines.
Acceptance: full suite green; `test_r73_p2_deadcode.py` asserts none of those names exist on
`reflex_lane` / `instinct_frame` / `completion_audit`; `git grep` for `RENDER MANDATE` returns only
`persona_card.py` + reading_delivery's sanitiser + tests.

### S8 — regression lock + docs
Files: new `tests/routing/test_r73_p2_single_path.py`, `docs/architecture.md`, `docs/lanes.md`,
`CHANGELOG.md`, `plugin.yaml` bump.
Acceptance: the guard test is an AST scan, not a grep of prose — it fails if any module other than
`reading_delivery` defines a function containing `BEGIN ROUTER READING`, if any module other than
`debug_banner` calls `format_banner`/`park_anchor_banner`, or if any module other than
`persona_card` opens `IDENTITY.md`. This is the test that makes the same-class bug fail loudly
instead of returning after two releases.

---

## 4. Do not touch

| Item | Location | Why |
|------|----------|-----|
| Card content and budgets — `PERSONA_MAX_CHARS 6000`, slice budgets 2600/2600, `persona_card_chars` default 2500, the `voice_stems` / `voice_only` / `full` strip rules | persona_card.py:30-33, :145-153, :243-293 | This text is behaviour, not formatting: it was tuned against live refusal evidence (A/B, 09-03). Only the *stripping helper* moves; not one character of the card changes. Measured stripped card is 1211 chars — the target band is 1,210-1,275. |
| RENDER MANDATE wording itself | persona_card.py:284-293 | Byte-identical. Strip it per-purpose, never edit it. |
| `agree → stand-down` | reflex_lane.py:1022-1029 (`agree_silent`) | A reflex that contradicts on every turn gets discounted. Silence-on-agreement is a deliberate training property. |
| fire-on-any-midturn | reflex_lane.py:999-1014 | A gate failure downgrades the claim to soft (`no clear lead`), it never governs delivery; only an empty option set stands down. Do not "restore" the old stand-down. |
| Fence text (`_FENCE_HEAD`, `_FENCE_TAIL`) | reflex_lane.py:854-856 | R73 P0 established this wording and the closing-line invariant. It may move files; it may not change bytes. |
| `format_banner` render shape — lane labels (`impulse (decision)`, `higher-self`, `shadow-self`), `_END_LABELS`, `MAX_BANNER_CHARS 700`, `$0.0000*` unpriced convention | debug_banner.py:139-201 | User-facing and reviewed (R34-19b, Goran 09-14 identity framing). `receipt` fills fields; it does not re-render. |
| Park policy = data (`stack_policy`, `_MAX_PARK_SEGMENTS 4`, once-delivery semaphore, drop-not-repark) | features/banners/kinds.py, lifecycle.py | The banner loss classes (R9d, R19.11, R33f-A, R34-2) were each found the hard way. |
| `_park_task_id` lane scoping (`"decision|" + task_id`) | decision.py:893-906 | Without it the frontier anchor overwrites the decision segment on co-fire. Any `receipt` change must keep lane-prefixed park keys. |
| Echo guard and canonical rewrite semantics | gate/orchestration.py:983, echo_guard.py | Out of scope; P0 already set the delivery-side fix, and the guard is the safety net for anything that leaks past it. |
| `frames.DECISION_RULE` text and its marker id | frames.py:303-305 | The standing contract the model is taught to read; wording is measured. |

Known-but-out-of-scope, found while reading (recorded, not scheduled):
`persona_card.py:172-173` slices IDENTITY.md through `_strip_md_headers`, which does **not** drop
YAML frontmatter — the live stripped card begins `--- role: architect / last_reflect: …`. `S1`'s
accessor is the correct place to fix it, but it changes card bytes for every lane, so it needs its
own ruling and its own step; do not fold it into P2-a.

---

## 5. Risk notes for the reviewer

- S4 is the only step that can change what the model sees at the generation boundary. Land it alone,
  with `test_r72_behavioural_integration.py` unmodified as the gate.
- S5/S6 touch the park→consume ordering that four live incidents turned on. The one-delivery
  semaphore and the "park before consume, receipt only after inject" ordering are the two
  invariants to re-state in every commit message.
- `reading_delivery.py` must not import `reflex_lane` (cycle: reflex_lane imports it). It may import
  `frames`, `decision_extract`, `persona_card` — no other hermes_router module — and `.importlinter`
  should get a matching contract in S8.
