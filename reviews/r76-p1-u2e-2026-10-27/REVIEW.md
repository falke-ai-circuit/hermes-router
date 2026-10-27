# R76-P1-U2e — midturn and rollup price the dispatched model, class-closing helper

**Commit:** `d3600b49c55519f9ab13d6845e6f56d7e3e2b9c8` (plugin.yaml 6.13.21) · parent `e6c8124`
**Repo:** `/opt/data/plugins/hermes_router` · HEAD `d3600b4`, live tree clean
**Reviewed:** 2026-10-27 · reviewer lane (verdict only, no implementation)
**Verdict: PASS** — the U2d D1 defect is closed **at the call**, on both surviving sites: `decision_midturn.py:1039`
and the turn-close rollup `:1493`. Driven with `backend=nous`, `midturn='on'`, cfg `model='z-ai/glm-5.3-flash'`
and an aux client reporting nothing, `estimate_cost` now receives `''` at both sites, the tokens row is
`{'lane':'decision','model':'','cost':0.0,'detail':'decision_midturn'}`, and the rollup renders
`… | tok 2759/102 | $0.0000* | provider=hermes-auxiliary | …`. U2d D2 is also closed: the name cell is the
dispatched id (`''`), never the cfg default, in the ledger row and in the banner.

> All measurements in a clean `git worktree` at d3600b4 (`cache/scratch/u2e_wt/src`, package import via a
> symlink parent). Probes + raw logs: `probes/`.

---

## Item-by-item

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 1 | **D1 closed**: no cfg/DEFAULTS model reaches `estimate_cost` on midturn or rollup when nothing was dispatched; cost 0.0, banner `$0.00000*`/`$0.0000*` | **PASS** | Driven, not read (`probes/midturn_path.log`). `_handle_hit('sess-NONE-reported', …, cfg backend=nous, MIDTURN_MODE_KEY=on)` + aux reporting `''`: `estimate_cost keys received: ['', '']`; tokens row `{'lane':'decision','model':'','cost':0.0,'detail':'decision_midturn'}`; `close_turn` rollup `… | tok 2759/102 | $0.0000* | provider=hermes-auxiliary | initiator=agent | row=1 ·`. `$0.000372` absent from both row and rollup. Both sites now route through `usage_ledger.price_key` (`decision_midturn.py:1041`, `:1505`). |
| 2 | **D2**: name cell empty (or lane) for an unreported dispatch, never the cfg default — ledger row AND banner | **PASS** | Ledger row model `''` (probe, above). Banner: the v3 banner (`probes/v3_path.log`, unreported case) renders `… \| pre \| hermes-auxiliary @ hermes-auxiliary \| tok 2759/102 \| $0.0000* \| …` — the lane is named, the cfg id is gone (`render_payload.py:265` `model or ep or "?"`). The midturn rollup has no model cell at all; it names `provider=hermes-auxiliary`. `DECOY not in banner` is asserted in the u2d pin. |
| 3 | The shared resolver is genuinely shared; one-site revert cannot restore the class | **PARTIAL — claim overbroad, property proven** | Property proven by mutation (E2): reverting the midturn price argument to the pre-unit shape (`estimate_cost(base["model"])`, `probes/mutation_revert_one_site.log`) still bills `''` → `0.0` and `$0.0000*`; same for the rollup site (reverted, still `$0.0000*`) — because the name cell no longer carries the cfg default, one site's revert cannot reintroduce billing. Literal "every billing site" is false: `decision.py:800`, `semantic_classifier.py:499`, `anchor_exec.py:1821`, `api/commands_runtime.py:319`, `router.py:443` and `reflex_lane.py:973` call `estimate_cost` without the helper. All but two bind a real dispatched/endpoint id; `router.py:443` uses `entry.get("model") or DEFAULT_MODEL` on the same expression it dispatches, and `reflex_lane.py:973` retains `_dispatched_model() or cfg.get("jev_native_model") or "jev-latest"` (pre-existing, previously ruled correct, comment claims usage is 0 there). None of the six is on the nous family this unit closes. |
| 4 | Reported-model cases still bill correctly | **PASS** | Midturn (`probes/midturn_path.log`): `jev-latest` → keys `['jev-latest','jev-latest']`, row cost `0.000116`, rollup `$0.000116`; `zz-no-such-model-9` → cost `0.0`, rollup `$0.0000*`, id present in row. v3 (`probes/v3_path.log`): identical three-case shape, banner `jev-latest @ hermes-auxiliary … $0.000116` and `zz-no-such-model-9 @ hermes-auxiliary … $0.0000*`. |
| 5 | Pins DRIVEN, not grep-only; tripwire judged separately as a guard | **PASS** | `tests/routing/test_r76_p1_u2e_price_class_helper.py` (7 passed in 3.2s) drives the real `_handle_hit` → real `call_backend` → fake aux transport, and calls the real `close_turn`; it captures the keys `estimate_cost` actually received, the row cost/model and the rendered rollup. `tests/routing/test_r76_p1_u2d_nous_price_fallback.py` + `test_r19_decision_lane_v3.py` + `test_r10_catalog_resolution.py` → 92 passed in 21.7s. The AST tripwire (`test_d_…`) is a guard only: it inspects arguments syntactically. Proven shallow by mutation — reintroducing the exact class through a local (`_pk = str(meta.get("model") or cfg.get("model") or "")`) is invisible to `test_d`, and only the behavioural pins catch it (2 failed: `test_a_…`, `test_b_…`, `probes/mutation_pins_under_reintroduced_fallback.log`). That is the pins doing the proving, which is what the brief asks. |
| 6 | The `test_r19_decision_lane_v3.py` assertion change `== DEFAULTS["model"]` → `== ""` | **LEGITIMATE pin correction** | The mocked aux (`_mock_nous`) reports no dispatched model; under the D2 contract the correct name-cell value is `''`. The old assertion pinned the fabricated id — i.e. it encoded the defect as the contract. The replacement is not weaker: it asserts `row["model"] == ""` **and** `row["model"] != DEFAULTS["model"]`, and an independent behavioural pin now exercises the same path. A pin moved to fit the code would have been relaxed to `in ("", DEFAULTS["model"])` or deleted; it was tightened. |
| 7 | SCOPE — 7 files + 2 test files; the u2d pin edit was not declared | **JUSTIFIED, declared in the commit body** | `tests/routing/test_r76_p1_u2d_nous_price_fallback.py` (+6/−3): docstring corrected, `assert meta["model"] == ""` added, `assert DECOY not in banner` added, marker text `$0.00000*`→`$0.0000*` corrected to the rendered 4-dp format. Strengthening, not weakening: the previous pin explicitly blessed the name cell naming the lane target ("it may name the lane's target"), which is exactly what D2 ruled a defect. Altering a prior unit's pin is justified when the prior pin encodes the ruling that was overturned; leaving it would have left the repo asserting the fabricated id. The new u2e pin is this unit's own proof file. |
| 8 | Version 6.13.21 in plugin.yaml and the catalog pin | **PASS** | `plugin.yaml:3 version: 6.13.21`; `tests/routing/test_r10_catalog_resolution.py:384 assert manifest["version"] == "6.13.21"`; no other `6.13.20/21` occurrences outside `reviews/` and `.git/`. |
| 9 | Full suite, runner summary verbatim | **PASS — 7 failed / 2 errors, all allowed; m1 green** | Clean worktree d3600b4: `7 failed, 2525 passed, 2 skipped, 1 deselected, 1 xfailed, 2 warnings, 2 errors in 313.07s (0:05:13)` (`probes/full_suite.log`). Reds: p05 ×1, p4 ×2, r33m ×2, rider20 ×2; errors: rider10 banner row ×2. All in the allowed set — **no regression**. `decision_lane/test_r53_midturn_mindset.py::test_m1` did **not** run red, so this is the 7-red baseline variant, not 8. |

---

## Findings

**N1 (non-blocking) — the "every billing site" wording outruns the code.** Six billing sites still call
`estimate_cost` without `usage_ledger.price_key`. Five bind a real id (`_aux_model`, `ep.model`,
`endpoint.model`) and one (`router.py:443`) prices exactly the expression it dispatches. Only
`reflex_lane.py:973` still carries a cfg fallback (`cfg.get("jev_native_model") or "jev-latest"`), on a
receipt whose comment claims usage is 0 when no dispatch record exists. It is a pre-existing site that earlier
rounds ruled correct, and it is outside the nous family this unit closes, so it does not reopen the D1 class on
the paths tested. Worth a follow-up unit if the resolver is meant to be universal.

**N2 (non-blocking) — the source tripwire proves less than it looks.** `test_d_…` catches inline
`cfg.get("model")` / `DEFAULTS["model"]` only; the realistic regression (assigning the fallback to a local,
then billing the local) passes it. The behavioural midturn pins catch that shape, so the guard's weakness is
covered here — but it should not be cited as the class-closing proof.

**N3 (cosmetic) — marker dp.** Request text says `$0.00000*` (5 dp); rendered and asserted marker is
`$0.0000*` (4 dp, `debug_banner` format). The u2d pin's docstring was corrected in this commit; the request
text was not. Cosmetic, noted so the series' quotation stays exact.

**N4 (observation) — v3 unreported banner renders `hermes-auxiliary @ hermes-auxiliary`** (name cell falls
back to the endpoint). It satisfies "name the lane, never the cfg default", but the duplicate reads oddly; an
empty or `?` name cell plus the endpoint cell might be cleaner. Not a defect against the stated criterion.

## Hostile attacks (Nemesis gate)

1. **The helper is bypassed at one site and the old fallback sneaks back** → killed: reverting the midturn or
   rollup price argument to the pre-unit shape still bills `''` → `0.0` (`probes/mutation_revert_one_site.log`);
   reintroducing the cfg fallback bills `0.000372` and the behavioural pins go red (2 failed).
2. **The cfg model reaches `estimate_cost` through the name cell** → killed at both sites: name cell `''` and
   price keys `['','']` measured, `$0.000372` absent from row and rollup.
3. **Pins assert the new code's own shape (grep/decoration)** → killed: they drive `_handle_hit`/`close_turn`
   and the mutation table shows which pin dies under which revert; the tripwire is separately shown to be
   shallow and is not relied on.
4. **The old r19 pin was quietly moved to whatever the code now emits** → killed: the replacement is strictly
   tighter (`== ""` and `!= DEFAULTS`), and the old value was the fabricated id.
5. **An unpriced id is silently given a plausible rate** → killed: `zz-no-such-model-9` → `0.0` on both paths,
   id retained in row and banner.
6. **A pricing exception kills the banner / the write** → cleared: fail-open wrappers return `0.0`; rollup and
   banner still rendered in every case, `tok 2759/102` present.
7. **Suite reported against a stale baseline** → cleared: measured 7 failed / 2 errors, every red in the
   allowed list, m1 green; 8-red variant not triggered.

## Recipient seat

The operator doing cost attribution reads the turn-close rollup in every `midturn:'on'` profile. Before this
unit that line claimed `$0.000372` for a call that dispatched nothing and was indistinguishable from a true
receipt. Now it reads `$0.0000*` and the tokens row it reconciles against carries an empty model id, so the
asterisk means what it says. On the v3 pre/post banner the same is true, and the fabricated id no longer
appears in the name cell — the lane is named instead.

## Drift check

- **Request-drift:** the request's D1 kill criterion was class-level; the unit closed the two surviving sites
  and made a one-site revert insufficient to reintroduce billing. The "every billing site" claim is wider than
  the delivered change (N1), but the family the request names is closed.
- **Implementation-drift:** the price key is now derived once (`usage_ledger.price_key`) and consumed at the
  four family sites; the name cell no longer carries a substituted id, which is what makes the class
  structurally hard to reintroduce.
- **Verification-drift:** the pin file drives the real entry points and the rollup; the only weak member is the
  AST tripwire, and it is explicitly not load-bearing.

---

**PASS.** U2d D1 and D2 are closed at the call on both midturn sites; reported models bill correctly; the pin
correction is legitimate and the prior-unit pin edit is justified; version and suite check out.
