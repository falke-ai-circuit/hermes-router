# R73 / P2-b — dead-code and duplication inventory (grep + trace proof)

Repo: `/opt/data/plugins/hermes_router`
HEAD: `c43a56b` ("R73 P1 (6.13.4): a delivered reading ships with a receipt"), `git describe` = `v2.3.9-525-gc43a56b`
Worktree: clean (`git status --porcelain` empty) before and after this audit.
Mode: read-only. No source file was modified. The only write is this document.
Probe harness: `/opt/data/profiles/analyst/cache/scratch/r73_branch_probe.py` (outside the repo).

## Verdict labels used

| Label | Meaning |
|---|---|
| LIVE | Reached by a caller that itself has a caller chain into the plugin seam. |
| DEAD | Zero references anywhere in any of the 13 trees (core repo + 12 profile copies), including tests, docs, dynamic dispatch and star-imports. |
| DUPLICATE-OF-X | Same job as X, different copy; drift already present unless stated. |
| UNREACHABLE-BY-INSPECTION | Branch cannot be entered; proven by dominance argument **and** runtime line trace. |
| UNPROVEN | Not establishable by the probes run here; the command that would settle it is given. |

Three proof classes were used, and each claim names which one backs it:

1. **Grep** — `grep -rn` over the repo, plus a cross-tree sweep of all 12 profile copies.
2. **AST** — store-without-load detection for assigned-but-never-read names.
3. **Trace** — `sys.settrace` line-hit sweep over `decision_extract.render` (806 calls: limits 0-399, 560, 800, 2000 x soft True/False) and over `reflex_lane._agent_card`.

---

## 1. `reflex_lane.parse_answers` — the fork parser

### Command

```
cd /opt/data/plugins/hermes_router && grep -rn "parse_answers" . | grep -v "\.pyc"
```

### Hits (all of them)

```
./reflex_lane.py:305:def parse_answers(data: Any, cands: List[str]) -> Optional[Dict[str, Any]]:
```

One hit: the definition. No call site, no test, no doc, no config reference.

Ruled out the three ways a "no callers" grep can lie:

```
cd /opt/data/plugins/hermes_router && grep -rn "from .reflex_lane import \*\|from hermes_router.reflex_lane import \*" --include=*.py .
cd /opt/data/plugins/hermes_router && grep -rn "getattr(.*\(parse_answers\|payload\|render\)" --include=*.py .
```
Both return nothing — no star-import, no attribute-by-string dispatch.

Cross-tree (the plugin is copied into every profile; the core repo is not the only consumer):

```
grep -rn "parse_answers\|reflex_lane\.payload\|reflex_lane\.render\|_rl\.payload\|_rl\.render" \
  /opt/data/profiles/*/plugins/hermes_router /opt/data/plugins/hermes_router --include=*.py | grep -v "def parse_answers"
```
Returns nothing. 13 trees, zero callers.

### Verdict: **DEAD**

Every live caller is on `decision_extract.parse`:

| Call site | Call |
|---|---|
| `reflex_lane.py:427` (midturn seam, `fire()`) | `parsed = _dx.parse(data, cands)` |
| `reflex_lane.py:996` (every-call seam, `run_on_request()`) | `parsed = _dx.parse(data, cands)` |
| `reflex_lane.py:413`, `975` | `from . import decision_extract as _dx` |

The only four entry points into the module are `fire`, `fire_from_hook`, `inject_pass`, `run_on_request`:

```
grep -rn "reflex_lane" --include=*.py . | grep -v "^./tests/" | grep -v "^./reflex_lane.py"
./gate/orchestration.py:277:        from .. import reflex_lane as _rl
./decision_midturn.py:423:            from . import reflex_lane as _rl
./decision_midturn.py:1835:            from . import reflex_lane as _rl
```
and the four call sites resolve to `run_on_request` / `inject_pass` / `fire_from_hook` / `fire`.

### The family dies with it (same proof class)

`parse_answers` is the only consumer of the whole fork-era code path. Evidence: attribute-call census inside `reflex_lane.py`.

```
cd /opt/data/plugins/hermes_router && grep -n "payload(\|parse(\|render(\|parse_answers\|_agent_card\|HEADER" reflex_lane.py
```

| Symbol | Line | Refs | Verdict |
|---|---|---|---|
| `payload` | `:241` | definition only; `:424` and `:992` both call `_dx.payload` | **DEAD** |
| `parse_answers` | `:305` | definition only | **DEAD** |
| `render` | `:351` | definition only; `:444-445` selects `_dx.render` / `_dx.render_instinct` | **DEAD** |
| `_num` | `:296` | only `:310`, `:312`, `:323`, `:334` — all inside `parse_answers` | **DEAD** (with parser) |
| `HEADER` | `:48` | only `:357`, inside `render` | **DEAD** |
| `candidates` | `:76` | no call site in any tree | **DEAD** |
| `_PATH_RE`, `_CMD_RE`, `_GENERIC` | `:58-65` | only used inside `candidates` | **DEAD** (with it) |
| `VALIDATE = "Fork present"` | `:49` | zero references anywhere | **DEAD** |

Note on `candidates()`: it is the fork-era candidate builder, superseded by `decision_extract.candidate_set`. `reflex_lane.py:415-416` and `:977` both call `_dx.candidate_set(...)`. Proof of no callers:

```
grep -rn "\bcandidates(" --include=*.py . | grep -v "def candidates\|candidate_set"
```
(no hits outside the definition)

---

## 2. `decision_extract.payload` vs `reflex_lane.payload`

### Commands

```
cd /opt/data/plugins/hermes_router && sed -n '241,293p' reflex_lane.py
cd /opt/data/plugins/hermes_router && sed -n '168,185p' decision_extract.py
```

### Hit inventory

| Aspect | `reflex_lane.payload` (`:241-293`) | `decision_extract.payload` (`:168-185`) |
|---|---|---|
| Envelope | `{"model", "state", "questions"}` | `{"model", "state", "questions"}` — identical shell |
| Gate question | `"fork"` / `type: "noul"` (`:247-258`) | `"action"` / `type: "choice"` (`:173-179`) |
| Per-candidate | `c1..cN`, one `noul` each (`:268-273`) | single `a1..aN` inside `choice.criteria` (`:178`) |
| state truncation | none (`:245`) | `state[:14000]` (`:171`) |
| model | `_rw.MODEL_DEFAULT` if present else `"jev-latest"` (`:244`) | explicit `model` parameter, default `"jev-latest"` (`:168`) |
| scope/rigour rubric | inlined, `:274-291` | module constants `SCOPE_LEVELS` / `RIGOUR_LEVELS`, `:44-49` |

Both are handed to the *same* transport (`reflex_lane._transport`, `:381`), i.e. two request contracts for one lane.

### Rubric duplication has already drifted

| Level | `decision_extract.py:44-49` | `reflex_lane.py:274-291` |
|---|---|---|
| scope[0] | `one file only` | `a single file, nothing else matters` |
| scope[1] | `one file plus its immediate caller` | `a single file plus its immediate caller` |
| scope[2] | `a few files inside one module` | identical |
| scope[3] | `several modules must be cross-read` | `several modules have to be cross-read` |
| scope[4] | `the whole tree has to be mapped before acting` | identical |
| rigour[0-2] | `none, it is obviously safe` / `a glance at the result` / `run the local check once` | identical |
| rigour[3] | `verify each step before committing` | `verify each step before committing to it` |
| rigour[4] | `verify every step against independent evidence` | identical |

Seven of ten levels match verbatim, three have drifted on wording. That is the signature of a copy, not a deliberate second rubric.

### Verdict: **DUPLICATE-OF `decision_extract.payload`** (and DEAD, per §1)

`decision_extract`'s own module docstring (`:1-21`) records that the `choice` distribution **outperformed** independent `noul`s (top-1 0.764 vs 0.655-0.759, better Brier), and `:259-267` of the dead copy asserts the opposite conclusion ("ONE NOUL PER CANDIDATE, never a single `choice`"). The dead copy is the superseded design, still carrying its rationale comment.

---

## 3. `min_fork_probability` / `fork_probability` — knobs that gate nothing

### Command

```
cd /opt/data/plugins/hermes_router && grep -rni "fork_probability" --include=*.py --include=*.yaml --include=*.yml --include=*.json --include=*.md .
```

### Hits (all of them)

```
./reflex_lane.py:314:            _rw._note(reason="no_fork_probability")
./reflex_lane.py:376:    c.setdefault("min_fork_probability", 0.5)
```

`:314` is not a gate — it is a telemetry note inside `parse_answers` (itself DEAD, §1), fired when the fork answer is missing.

`:376` defaults the key into the config dict. Nothing reads it back:

```
cd /opt/data/plugins/hermes_router && grep -rn "min_fork_probability" --include=*.py . | grep -v setdefault
```
(no hits)

The live gate keys are different names, read at both seams:

```
./reflex_lane.py:431:        ok, why = _dx.gate(parsed, float(c.get("min_top") or 0.45),
./reflex_lane.py:432:                           float(c.get("min_margin") or 0.10))
./reflex_lane.py:999:        ok, why = _dx.gate(parsed, float(c.get("min_top") or 0.45),
./reflex_lane.py:1000:                           float(c.get("min_margin") or 0.10))
```

### Verdict: **DEAD** — `min_fork_probability` is set and never read. It gates nothing.

Sibling in the same three-line block, same status: `min_gap` (`:377`) — zero readers, `grep -rn "min_gap" --include=*.py .` returns only the `setdefault`. Presence of both knobs in `_cfg()` (`:373-378`) makes them look configurable; neither is wired.

Adjacent stale claim: `reflex_weights.py:86` comments that "configured min_top / min_margin / every_call / timeout was quietly run" — those three *are* live (`reflex_lane.py:964`, `:431`, `:999`); the comment is accurate, and it is the fork-era pair that never got wired.

---

## 4. Banner text builders outside `debug_banner.format_banner`

### Command

```
cd /opt/data/plugins/hermes_router && grep -rn "format_banner(" --include=*.py . | grep -v "^./tests/" | grep -v "def format_banner"
```
13 hits. All 13 are legitimate `format_banner` consumers — there is exactly one banner renderer for the normal path. The duplicates are elsewhere:

```
cd /opt/data/plugins/hermes_router && grep -rn "· router ·\|DELIVERED_MARK\|_DECISION_MARK\|BANNER_HEAD" --include=*.py . | grep -v "^./tests/"
```

| Location | Text | Role | Verdict |
|---|---|---|---|
| `anchor_exec.py:1679-1681` | `"%s higher-self (frontier) \| consult failed \| empty response \| %s %s" % (_dbf.BANNER_HEAD, _fb_model, _dbf.BANNER_TAIL)` | **hand-formats a whole banner string** | **DUPLICATE-OF `format_banner`** — and a live bypass: it skips `VALID_LANES`/`FORBIDDEN_LANES` validation (`debug_banner.py:123`), the `://`-host redaction (`:136-138`), the `_END_LABELS` pre/post mapping (`:146-152`), and the `$0.0000*` unpriced-model marker (`:161-166`). |
| `decision.py:117` | `DELIVERED_MARK = "· router · impulse (decision)"` | literal replica of `BANNER_HEAD` (`debug_banner.py:53`) + the `_lane_label` ternary result (`:174-177`); used as an echo filter at `decision.py:461-465` | **DUPLICATE-OF `BANNER_HEAD` + lane label** — drift-coupled: rename the lane label in `debug_banner` and this filter silently stops matching, with no test coupling the two. |
| `completion_audit.py:115` | `"router · frontier"` inside `_AUDIT_ARTIFACT_MARKERS` | exclusion marker so prior verdict text never enters a consult payload | **DUPLICATE-OF `BANNER_HEAD` prefix** — and already weak: it omits the leading `"· "`, so it matches a superset of real banners. Intentional looseness or drift is **UNPROVEN**. |
| `debug_banner.py:469` | `_DECISION_MARK = "· router · decision"` | used at `:480`, `:503` for parked-banner identity | **DUPLICATE inside `debug_banner` but outside `format_banner`** — a third spelling of the same head, differing from `BANNER_HEAD` (`· router ·`) and from `DELIVERED_MARK` (`· router · impulse (decision)`). |
| `reflex_weights.py:30` | `HEADER = "[reflex: read-only reading of the fork in front of you]"` vs `reflex_lane.py:48` `HEADER = "[reflex: a reading of the choice in front of you, not a request]"` | block headers (not banners) for the same lane, in two modules | **DUPLICATE family** — see §7; the `reflex_weights` one is dead. |

Checked and cleared (not duplicates):

| Location | What it is | Verdict |
|---|---|---|
| `render_payload.py:222` `render_decision_banner` | wrapper: builds cost/endpoint then calls `debug_banner.format_banner` at `:253` | **LIVE**, single renderer preserved |
| `completion_audit.py:1556` `_sense_banner_suffix`, `:1637` `_doubt_banner_suffix` | suffix/adjunct strings appended to an already-rendered banner | **LIVE adjuncts**, not renderers |
| `gate/orchestration.py:243` `_debug_banner_pass`, `:800` `_banner_turn_snapshot` | one-line `_hub()` delegate; park-record reader (returns turn numbers, contains no text) | **LIVE shims**, not builders |
| `completion_audit.py:1402, 2114, 2794, 2867`; `gate/orchestration.py:505, 1886, 2172, 2319`; `passes/dispatcher_pre.py:601`; `anchor_exec.py:992, 1037`; `reflex_lane.py:912`; `render_payload.py:253` | ordinary `format_banner` calls | **LIVE** |

The one worth fixing is `anchor_exec.py:1679-1681`: it is the only place that constructs banner text without the renderer, and it is on the failure path (empty consult), i.e. the path least likely to be exercised in a green test run.

---

## 5. Unreachable / dead branches in `decision_extract.render`

Method: line trace, not reading. 806 calls = limits `0..399`, `560`, `800`, `2000`, each with `soft=False` and `soft=True`, over a 3-option parsed dict with both dials set.

### 5a. The over-budget shedding path — LIVE, but its tail return's non-empty arm is unreachable

Hits per line:

| Line | Hits | Meaning |
|---|---|---|
| `:337` `optional = [reg] + ...` | 806 | path always prepared |
| `:339` `if len(...) <= limit: return` | 806 | budget test always evaluated |
| `:341` `for drop in optional:` | 2829 | shed loop entered repeatedly |
| `:344` `return "\n".join(keep)` (inside loop) | 109 | loop returns after shedding |
| `:345` `return "" if len(...) > limit else "\n".join(keep)` | 639 | tail return |

Cross-check: `empty_outputs = 639`, `nonempty_outputs = 167`, and `hit_345 = 639`. **Every single execution of line 345 returned `""`.** The non-empty arm never fired in 639 attempts, across every limit from 0 to 2000.

Dominance argument (why this is structural, not sampling luck): `optional` always contains `reg` (`:337`, list literal with `reg` first), so the `:341` loop always executes at least one iteration; any iteration that brings the block under budget returns at `:344`; therefore reaching `:345` *means* the block is still over budget, so the condition at `:345` is always true.

### Verdict: the `else "\n".join(keep)` arm at `decision_extract.py:345` is **UNREACHABLE-BY-INSPECTION** (grep + dominance + 639/639 trace). The shedding path as a whole is **LIVE** (`:344` fired 109 times) — do not delete the loop, only the dead arm.

### 5b. The soft branch — LIVE, not unreachable, but carries a dead assignment

| Line | Hits | Meaning |
|---|---|---|
| `:303` `if margin >= DECISIVE_MARGIN and has_target and not soft:` | 403 (of 806; only the `soft=False` half can enter) | decisive arm |
| `:311` `if soft:` | 403 | soft arm taken on every soft call |
| `:314` `_soft_act = True` | 403 | assigned every time |

So the soft branch is genuinely reachable and does work: `soft=True` is threaded from both live seams (`reflex_lane.py:446` `_soft_kw = {"soft": True} if (_soft and _rend is _dx.render) else {}`), and it fired on every soft-path call in the sweep. Note `:303`'s `and not soft` is *correct* by construction, not dead — soft calls are routed to `:308` deliberately.

### Verdict: soft branch **LIVE**. `_soft_act = True` at `:314` is a **DEAD ASSIGNMENT**.

```
python3 -c "import ast;src=open('/opt/data/plugins/hermes_router/decision_extract.py').read();..."
# AST store-without-load scan
DEAD_LOCAL render {'_soft_act': 314}
```

`_soft_act` is stored at `:314` and never loaded anywhere in the function or module. It is a residue of the older logic where the soft sentence was appended conditionally after the fact.

Two more dead locals found by the same scan (cosmetic, zero behavioural risk):

```
DEAD_LOCAL salient_paths {'_r': 98}      # loop var in `for p, _r in sorted(...)`
DEAD_LOCAL candidate_set {'_lab': 135}   # unpacked third tuple element, unused
```

---

## 6. The two card paths: `persona_card.build_persona_context` vs `reflex_lane._agent_card`

### Command

```
cd /opt/data/plugins/hermes_router && grep -rn "build_persona_context\|_agent_card" --include=*.py .
```

### Hits

`build_persona_context` (`persona_card.py:199`) — 6 live consumers:
```
./decision.py:1882:        txt = persona_card.build_persona_context()
./completion_audit.py:2018:        card = _strip_render_mandate(_pc.build_persona_context())
./anchor_exec.py:1620:                            _card = _pc.build_persona_context()
./reflex_lane.py:146:        shared = str(_pc.build_persona_context(messages) or "").strip()   # inside _agent_card
./render_payload.py:387 (docstring reference)
./tests/higher_self/test_persona_card.py (6 hits), ./tests/higher_self/test_r34_19g_higher_self_acts.py (2 hits)
```

`_agent_card` (`reflex_lane.py:127`) — 2 live consumers, both inside the module's live seams:
```
./reflex_lane.py:422:        _card = _agent_card()
./reflex_lane.py:991:        _card = _agent_card(_msgs if isinstance(_msgs, list) else None)
```

### They are not parallel implementations — `_agent_card` delegates first

`_agent_card` body order:
1. `:142-146` — `from . import persona_card as _pc`; `shared = build_persona_context(messages)`.
2. `:154` — strips the render mandate via `completion_audit._strip_render_mandate` (reason documented at `:148-152`: the shared card is authored for the render lane and ends with a never-refuse mandate that would corrupt a decision read).
3. `:162-163` — returns the shared card when `len(shared) > 60`.
4. `:166-222` — **only if the shared builder yielded nothing usable**, builds its own card: own frontmatter-stripping IDENTITY.md parser (`:175-202`), own `CARD_CHARS = 400` cap (`:43`), own 300s TTL cache (`:44`, `:169-172`, `:220-221`), own `=== CURRENT TASK ===` appender (`:207-219`).

### Reachability of the fallback — proven, not assumed

The fallback is not theoretical. With `persona_card.build_persona_context` monkeypatched to return `""` (runtime only — no file in the repo was touched), the traced line set was:

```
fallback_lines_hit=[141, 142, 146, 147, 166, 167, 168, 169, 170, 171, 173, 174, 175, 176, 177,
178, 179, 180, 181, 184, 185, 186, 187, 188, 193, 194, 195, 196, 198, 199, 200, 202,
207, 208, 209, 210, 211, 212, 213, 214, 217, 218, 220, 221, 222]
shared_path_called=True
```

Every line of the fallback executed. So:

### Verdict

- `persona_card.build_persona_context` — **LIVE** (single source of truth; 6 consumers).
- `reflex_lane._agent_card` — **LIVE** and **not a duplicate**: it is a documented adapter (strip-then-delegate).
- The fallback block `reflex_lane.py:166-222` — **DUPLICATE-OF `persona_card`'s compact path** (second IDENTITY.md parser + second cache + second task appender), reachable only on shared-builder failure. It is 57 lines of duplicated parsing maintained at a different truncation limit (400 vs `persona_card_chars`, default 2500, `persona_card.py:145-151`), which means on the fallback path the decision model silently gets a 6x smaller card.

Incidental defect found while tracing, worth a ticket: the fallback's `s.lstrip("*")` at `:195` strips only leading asterisks, so an IDENTITY opening `**Analyst** — root-cause engine.` renders as the traced `card_repr` = `'Analyst** — root-cause engine.'` — a mangled role name, on exactly the degraded path.

---

## 7. Additional finding — a third fork-family implementation in `reflex_weights.py`

Not in the brief, but it is the largest duplication in the lane and it changes the §1 answer from "one dead parser" to "one dead parser plus a dead parallel module".

`reflex_lane` imports `reflex_weights` as `_rw` and uses exactly four symbols:

```
cd /opt/data/plugins/hermes_router && grep -o "_rw\.[A-Za-z_]*" reflex_lane.py | sort | uniq -c | sort -rn
     16 _rw._note
      2 _rw.cfg_for
      2 _rw.DRIVES
      1 _rw.MODEL_DEFAULT
```

Everything else in `reflex_weights.py` is unreferenced:

```
cd /opt/data/plugins/hermes_router && grep -rn "\.extract(\|\.summarise(\|build_request(\|_as_prob(\|_clip(" --include=*.py . | grep -v "^./reflex_weights.py"
(rc=1, empty)
```

| Symbol | Line | External refs | Verdict |
|---|---|---|---|
| `parse` | `:133` | 0 (only called by dead `extract`) | **DEAD** |
| `render` | `:224` | 0 | **DEAD** |
| `build_request` | `:263` | 0 (only called by dead `extract`) | **DEAD** |
| `extract` | `:284` | 0 | **DEAD** |
| `summarise` | `:297` | 0 | **DEAD** |
| `_as_prob`, `_clip` | `:112`, `:129` | only inside the dead four | **DEAD** (with them) |
| `_note` | `:48` | 16 in `reflex_lane` | **LIVE** |
| `cfg_for` | `:57` | 2 in `reflex_lane`, 1 test | **LIVE** |
| `DRIVES` | `:38` | 2 in `reflex_lane` | **LIVE** |

Its `render` (`:224-245`) is a **second block renderer for the same lane**: own `HEADER` (`:30`), own `fork` shape, own `%4.2f` option format — against `decision_extract.render`'s `%2d%%` format. Authored before the fold into `decision_extract`; retained after it.

Also stale: `reflex_lane.py:69` docstring says "Armed per profile via `hermes_router.reflex_weights.enabled`", but `reflex_weights.py` has no `enabled` — the function is `reflex_lane.enabled` (`:68`). A docstring pointing at a function that does not exist.

### Verdict: **DEAD** module body (5 of 9 public symbols), **DUPLICATE-OF `decision_extract.render`/`payload`** for the block-building half.

---

## 8. Summary table

| # | Item | Verdict | Proof |
|---|---|---|---|
| 1 | `reflex_lane.parse_answers` (`:305`) | **DEAD** | grep, 1 hit = def; 13 trees; no star-import/getattr |
| 1b | `reflex_lane.payload` (`:241`) | **DEAD** | grep, def only |
| 1c | `reflex_lane.render` (`:351`) | **DEAD** | grep, def only |
| 1d | `_num` / `HEADER` / `candidates` / regexes / `VALIDATE` | **DEAD** | dependency closure of the above |
| 1e | "every caller on `decision_extract.parse`" | **TRUE** | `:427`, `:996` only parse sites |
| 2 | `reflex_lane.payload` vs `decision_extract.payload` | **DUPLICATE-OF `decision_extract.payload`**, drift in 3/10 rubric levels | sed diff |
| 3 | `min_fork_probability` | **DEAD** — defaulted `:376`, never read | grep, 2 hits, 1 is a telemetry note |
| 3b | `min_gap` (`:377`) | **DEAD** — same | grep |
| 4 | `anchor_exec.py:1679-1681` banner | **DUPLICATE-OF `format_banner`** + validation/redaction bypass | grep + `format_banner` signature |
| 4b | `decision.py:117` `DELIVERED_MARK` | **DUPLICATE-OF `BANNER_HEAD`+label**, drift-coupled | grep |
| 4c | `completion_audit.py:115` `"router · frontier"` | **DUPLICATE-OF `BANNER_HEAD` prefix** | grep |
| 4d | `debug_banner.py:469` `_DECISION_MARK` | **DUPLICATE** (in-module, outside `format_banner`) | grep |
| 4e | `render_payload.render_decision_banner` | **LIVE** wrapper, not a duplicate | grep + body |
| 5a | `decision_extract.render:345` non-empty arm | **UNREACHABLE-BY-INSPECTION** | dominance + 639/639 trace |
| 5b | over-budget shedding loop `:341-344` | **LIVE** (109 hits) | trace |
| 5c | soft branch `:311-314` | **LIVE** (403 hits) | trace |
| 5d | `_soft_act` (`:314`) | **DEAD assignment** | AST store/load |
| 5e | `_r` (`:98`), `_lab` (`:135`) | **DEAD locals** | AST |
| 6 | `persona_card.build_persona_context` | **LIVE** | 6 consumers |
| 6b | `reflex_lane._agent_card` | **LIVE** adapter, not a duplicate | body order + trace |
| 6c | fallback `:166-222` | **DUPLICATE-OF persona_card compact path**, reachable (traced) | trace with monkeypatched shared builder |
| 7 | `reflex_weights.parse/render/build_request/extract/summarise` | **DEAD** + **DUPLICATE-OF `decision_extract`** | grep, 0 external refs |

## 9. UNPROVEN, and the command that would settle each

| Claim left open | Why unproven | Command that would prove it |
|---|---|---|
| `completion_audit.py:115` `"router · frontier"` omitting `"· "` is intentional slack vs. drift | A string literal's intent is not derivable by grep | `git log -L 112,117:completion_audit.py --oneline` and read the introducing commit message |
| No *external* (non-plugin) process imports `reflex_lane.payload` / `parse_answers` | Only the 13 plugin trees and their tests were swept; site-packages, wheels and installed entry points were not | `python3 -c "import hermes_router, sys; print(hermes_router.__file__)"` then `grep -rn "parse_answers\|reflex_lane.payload" /opt/data/venvs /opt/data/.local/lib 2>/dev/null`, plus `python3 -X importtime -c "import hermes_router"` to confirm which tree actually loads |
| `min_fork_probability` is not read through a config-schema mirror (e.g. a settings UI that lists keys generically) | grep covers code, not a runtime-generated key list | `grep -rn "min_fork_probability" /opt/data/profiles/*/config.yaml` and, if a settings surface exists, `python3 -m hermes_router.settings_dump \| grep fork` (confirm the module name first: `ls hermes_router/api/`) |
| The `:166-222` fallback never fires in production (only that it *can*) | Reachability was proven by forced failure, not by observed failure | `grep -r "persona_card_built\|card_fallback" *.log` over the plugin logs for a fallback marker — note **no marker is currently emitted**, so this is unobservable today and would need one added before the question is answerable |

Items 1-7 in §8 are all backed by reproduced command output; nothing in the table above is load-bearing for them.

## 10. Cross-artifact note

`docs/r73-module-map.md` exists in the worktree as untracked, was not authored in this session (mtime 05:39, one minute before this file), and independently reaches the same verdict on the fork parser:

> `reflex_lane.payload` :241-293 / `_num` :296 / `parse_answers` :305-349 (**dead**: zero callers repo-wide)

That is independent corroboration of §1, reached by a different method (module inventory, not caller census). One discrepancy worth recording, since it will matter if that map is used as a deletion brief: it gives `_agent_card`'s fallback extent as `:166-204`, while the trace in §6 shows the local slice runs `:166-222` — `:207-219` (the `=== CURRENT TASK ===` appender) and `:220-221` (cache write) are part of the same fallback and are not covered by `:166-204`. Deleting on the narrower range would leave the task appender and cache write behind.
