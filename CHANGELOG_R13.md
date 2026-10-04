# 4.16.3 — 2026-10-04 (FIX-FIRST rider 13 — misfire/gate battery: fork-preservation engagement + benign-prose consult gates)

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
