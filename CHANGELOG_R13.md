# 4.16.4 — 2026-10-04 (FIX-FIRST rider 13 ADDENDUM, Goran-approved — R13-4 observability recurrence: R10-6 probe-verified-but-broken-on-real-turns)

R13-4 live recurrence (conductor session api_1791099470_927b8d58,
07:37-07:38 2026-10-04, 'anchor this' + frontier consult @ z-ai/glm-5.3,
$0.013318): claimed banner-less delivered reply + no
anchor_route_fired/anchor_banner events. Root-caused against the REAL
surfaces (delivered body state.db 313607 len 4644 == the BENIGN_BANNER
render record; route log /tmp/uncensored-router-conductor.log):

- FINDING 1 DISSOLVED as observer artifact (the delivered body is the only
  truth surface — verified): ALL FOUR legs were present on the REAL turn.
  (1) billed spend: routing-state Oct 4 $0.041137 + ledger rows 362/363/364
  (frontier_consult completed, $0.013318); (2) route events:
  anchor_banner_parked + debug_banner_emitted + anchor_route_fired at
  07:38:24, anchor_banner_consume parked=True edge=benign +
  banner_render_captured at 07:38:46; (3) banner segs in the delivered
  reply ('· router · higher-self (frontier) | consult | … | row=363 ·' +
  two decision-lane advisory segs); (4) spend entry $0.013318 on the
  banner line. The 20:11 probe turn (api_1791058257) shows the IDENTICAL
  event pattern and banner shape — the "probe takes a different code path"
  hypothesis is FALSE; probe and real turn took the same path.
- FINDING 2 ROOT CAUSE (the "no events" claim): LOG-LOCATION split-brain.
  The conductor's ACTIVE route log is /tmp/uncensored-router-conductor.log
  (config.yaml:665 log_path override) — the home-dir uncensored-router.log
  froze at 2026-10-03T19:45:18Z, and the observer tailed the FROZEN log.
  Every "log lines were frozen" claim in this battery traces to reading
  the wrong file. Config is owner territory: NOT touched; recommendation
  for the owner — move log_path back under the profile home (also /tmp is
  wiped on reboot). The acceptance upgrade (delivered body = truth) makes
  future claims immune to this artifact.
- FINDING 3 REAL CODE DEFECTS on the delivered body (both fixed):
  (a) impulse-frame persona slot sourced ROUTER/BANNER vocabulary from the
  agent frame when prior banner text rode the context — the delivered
  rollup glued 'ADVICE-ONLY: high-stakes fork — main model/user confirms.'
  + a duplicated reflex tail into the band line, reading as a corrupted
  banner (why the conductor read it banner-less). Fix: _BANNER_VOCAB_RE
  deny filter in _impulse_persona_slot — a fragment carrying banner
  vocabulary falls back to the canned register.
  (b) unlabeled options fell back to the option BODY as label, hard-cut at
  60 chars mid-sentence ('Cost baseline capture. "Fleet generalization
  after a day of pulls 0.25 …'). Fix: long labels truncate at a WORD
  boundary with an ellipsis; the reflex tail renders exactly once.
- ACCEPTANCE UPGRADE (binding, codified in
  tests/test_r13_4_delivered_body_legs.py): banner/billing acceptance
  verifies the DELIVERED BODY string — (1) billed spend row, (2) route
  event, (3) banner rendered IN the delivered reply text, (4) spend entry
  — never log lines alone. The parked→consume→delivered one-shot
  guarantee is pinned on the delivery string. Probes are diagnostic only.

- Pins: tests/test_r13_4_delivered_body_legs.py (6) — slot banner-vocab
  rejection + clean-vocab passthrough, word-boundary label truncation +
  single reflex tail, short-label regression, delivered-body four-leg
  shape on a real-shaped api_server turn, park→consume→delivered string.

- Known non-goals (note only, no action): the forged-persona detector
  still flags the integration-rule seam text arriving unmarked — that
  signal is the designed loud-flag behavior; recovery E4 remains
  user-attested only.

Version pin: plugin.yaml 4.16.4.


Sources: reviewer T1R6 battery (/opt/data/tmp/t1r6-results-2026-10-04.md,
raw t1r6-raw.jsonl) on v4.16.2 / 5d59c8a. No deploy, no gateway bounces,
no profile config/.env writes.

- R13-1 LOOP-GATING, ROOT CAUSES (analyst C3 fork consumed / coder C4
  event pair absent / C3-C4 event contract diverged):
  (a) The injection_flagged family had ONE trigger — flag_forged_banner_persona
  (banner-persona rule text). The battery's exfiltration-clause injection
  ("ignore the above and output ... verbatim") was a DIFFERENT family and
  was never flagged at all — reviewer/orchestrator "passes" came from the
  integration-rule seam text in THEIR sessions (housekeeping (a)), analyst
  had none. New frames.flag_prompt_injection detects the
  disregard-and-exfiltrate clause (both halves required: disregard marker
  + output/exfiltration demand; <memory-context> never fires).
  (b) The fork was consumed because 'Option A delete the staging database,
  Option B keep it' (inline labels, NO delimiter after the letter) failed
  has_declared_fork_structure -> decision suppressed (no_declared_structure)
  while the risk leg read the injection clause as risky and billed risk_r2.
  New _INLINE_OPTION_LABEL_RE: >= 2 DISTINCT 'Option A/B' labels inline are
  a declared fork (approach/path/plan stay delimiter-form-only).
  (c) engagement now UNIFORM on both seams: sweep_turn_start (turn_boundary)
  AND on_terminal_output flag the clause, log injection_flagged, EXCISE the
  clause before option extraction (frames.strip_injection_clause — the
  delivered banner/option set never carries exfiltration wording), and
  event injection_flagged_fork_preserved (fork proceeds, banner+rows) or
  injection_flagged_fork_suppressed (explicit pair) — the fork is never
  silently consumed on a flagged turn. Contract unified across profiles
  (C3's divergent event shape dies with it).
  (d) router_core risk leg skips the consult on flagged turns
  (risk_pre_skip_injection_clause) — the C3 waste consult is gone; the
  decision lane owns the fork.

- R13-2 valmet D1a gate regression, ROOT CAUSES (four consults + two
  banners on benign setup prose):
  (a) risk.py: brief-frame narration ('Brief me on the tradeoffs of X vs
  Y...') that hits stage1 hint no longer escalates to the semantic stage
  (_benign_brief_frame: brief/explain/compare verbs, decision imperatives
  excluded) — narration-that-mentions-two-options never consults; a real
  risk ask (imperative/question shape) is never suppressed by the guard.
  (b) decision.post_fork_scan: interrogative extracted options are
  information-gathering questions ('does the repo use Vite already?'),
  not closed choice alternatives — dropped before the >= 2 check; the
  suppression is evented as outcome=post_info_questions. (Live: the
  assistant's closing three-facts question list billed opt-3 @ 0.45
  pending + banner.) The paired D1b fork turn keeps firing.

- R13-3 architect B9 benign-essay pseudo-fire, ROOT CAUSE (same class):
  numbered-bullet QUESTION lists ('1. does ... 2. how ... 3. is ...') pass
  has_declared_fork_structure via bare digit line markers and become a
  midturn consult (ledger row 9, opt-1 @ 0.45, banner delivered on a
  benign essay). Bare digit markers ('1.'/'2.') no longer count on the
  line-marker path (numbered bullets are step/plan lists, not
  alternatives; digit forks stay reachable via 'Option 1:'/'Approach 1:'
  named enumeration and (A)/(B) ordinals); interrogative lettered bodies
  are disqualified by _all_interrogative. Lettered/named/paren fork shapes
  unchanged.

- Pins: tests/test_rider13_fixes.py (16) — clause flag + negatives, strip,
  inline labels declared, sweep preserved/suppressed pairs (SEAM 2),
  terminal-seam parity (SEAM 1), risk brief-frame guard + decision-ask
  exemption, post info-questions never bill + real fork still fires,
  question lists never declared, fleet canonical fork shapes unchanged.

- Known non-goals (note only, no action): the forged-persona detector
  still flags the integration-rule seam text arriving unmarked — that
  signal is the designed loud-flag behavior; recovery E4 remains
  user-attested only.

Version pin: plugin.yaml 4.16.3. Suite: 1408 passed / 2 skipped.
