VERDICT FIX-FIRST /opt/data/tmp/r78u/M50-21_REVIEW.md 3520fb5 43P/0F

unit: M50-21 · reviewed_by: reviewer · verdict: FIX-FIRST · verdict_sha_of_head: 3520fb5
reviewed artefact: /opt/data/tmp/r78u/M50-20_REMEASURE.md (9234 B, reviewed_by PENDING at review time)
head measured: 3520fb5d8461b216cc1a27d0f0087076b0a5cbe1 (git status --porcelain = empty, verified)
suite: tests/routing/test_r78u_m50_02_budget_retry.py + tests/higher_self/test_m50_14_retry_descends_effort.py
       + tests/routing/test_r78u_m50_04_failover_bound.py + tests/higher_self/test_banner_template_compliance.py
       -> 43 passed, 0 failed in 8.17s (E3, run by me at 3520fb5)
recipient: orchestrator + Goran — the measurement is the only proof the M50 anchor fixes landed.
RECIPIENT EXPERIENCE: re-ran every count the artefact prints against uncensored-router.log and
uncensored-router-renders.jsonl; used the artefact as the recipient would (read its verdict column
first, then tried to reproduce each number). 6 of 7 counts reproduce; 2 claims fail.

SHAPE none — measurement unit, no code added (no file, function, branch or helper in this unit).

## CLAIM 1 — empty_response after vs before — SPLIT (after reproduces, before does not)

Window bounds actually used: after = 2026-11-15T20:24:57Z..2026-11-15T20:31:30Z (driver launched all
12 turns at 20:25:02Z; max secs 334.8 -> last delivery 20:30:37Z). Before = whole UTC day
2026-11-14T00:00:00Z..2026-11-15T00:00:00Z.

grep -c per profile, after window:
  awk '$1>="2026-11-15T20:24:57Z" && $1<="2026-11-15T20:31:30Z" && /empty_response/' <p>/uncensored-router.log
  analyst 0/0sess  architect 0/0  coder 0/0  conductor 0/0  evol 0/0  operative 0/0
  orchestrator 0/0  researcher 0/0  reviewer 0/0  shadow 0/0  valmet 0/0  m3629canary 0/0
  anchor_route_failed = 0 and anchor_budget_retry|anchor_retry = 0 on all 12 in the same window.
  Log is live in-window (324/49/55 lines on analyst/conductor/reviewer), so 0 is measured, not empty.
  AFTER LEG REPRODUCES: 0 lines / 0 sessions, 11/11 driven, 11/11 reachable.

grep -h "^2026-11-14T.*empty_response" */uncensored-router.log  (whole day, 12 profiles):
  architect 1 line / 1 session (api_1794646732_5ebd1953, 09:13:19Z)
  coder     1 line / 1 session (api_1794684231_56ee65ad, 20:06:20Z)
  conductor 3 lines / 1 session (20260813_160517_d56f96a7, 21:08:50/21:42:43/22:35:28Z)
  reviewer  3 lines / 2 sessions (api_1794679847_19825b68 x2 @18:25:14/18:33:17Z; api_1794696047_345169ae @22:54:07Z)
  all others 0.
  TOTAL = 8 lines / 5 sessions across 4 of 12 profiles.
  Artefact's bullet "6 lines / 3 sessions across 3 of 12 profiles ... plus reviewer 3 lines / 2 sessions"
  does not reproduce and is internally impossible: its own four rows sum to 8 lines / 5 sessions.
  (Its three brief-named profiles alone are 5 lines / 3 sessions, not 6/3.) BEFORE LEG FAILS.

## CLAIM 2 — banners 11/11, 1 per driven turn, 0 dropped — PARTIAL

Canonical head regex (BANNER-SPEC §1/§2), applied to the `render` field of uncensored-router-renders.jsonl:
  · router · (impulse \(decision\)|higher-self \(frontier\)|shadow-self \(uncensored\))
After window, per driven session id:
  analyst 2 heads in ONE body (4031 chars, +138.5s): impulse (decision) AND higher-self (frontier)
  architect 2 recs 1 head each · coder 1 · conductor 1 · evol 2 recs 1 each · operative 2 recs 1 each
  orchestrator 1 on driven (+2 ambient decision heads in session 20261006_192644_08377425)
  researcher 2 recs 1 each · reviewer 2 recs 1 each · shadow 1 · valmet 2 recs 1 each
  -> 11/11 driven sessions carry >=1 canonical head. THAT PART REPRODUCES.
  -> "1 per driven turn" FAILS on analyst: two heads in one delivered body. BANNER-SPEC §1 "exactly one
     `· router ·` per body. A second head is a violation"; §5 "a second compose must never stack a second
     banner". The artefact's table prints "1 (1)" and its quoted analyst banner lists only the impulse
     line, so the second head is unreported, not absent. Verbatim tail of that render:
       · router · impulse (decision) | 1 verdict+1 hold | ×2 | row=1032 ·/jev @ typesafe | tok 1647/74 | $0.0002 | provider=typesafe | initiator=agent
       · router · higher-self (frontier) | pre-check | anthropic/claude-sonnet-5.5 @ 127.0.0.1:8680 | tok 1605/1712 | $0.0203
  -> "0 dropped" holds only as "0 unrecovered": anchor_banner_promoted fired in-window on 6 profiles
     (architect, evol, operative, researcher, reviewer, valmet) with reason=promoted_from_stash, i.e. the
     park->consume step missed on those turns and the chokepoint promotion rescued it. Say "0 lost",
     not "0 dropped".
  §5 persisted-transcript claim VERIFIED independently: GET /api/sessions/api_1794774302_b2dd3a43/messages
  on analyst 8646 -> HTTP 200, 11 messages, literal "· router ·" = 0, "impulse (decision)" = 0,
  "higher-self (frontier)" = 0; assistant tails end on ordinary prose. Banner is delivery-surface only,
  so "banners disappeared" from persisted transcripts is the spec, not a regression.

## CLAIM 3 — coverage gap — ARTEFACT CORRECT (banked)

0 anchor_budget_retry / anchor_retry / _recovered / _empty lines in-window on all 11 driven profiles
(and 0 on m3629canary). anchor_exec.py:1262 ADVISORY_TOKEN_FLOOR = 48000; the retry arm is gated on
finish=length (anchor_exec.py ~1046-1070) and M50-14b/15 choose raise-vs-descend before logging.
Therefore: "frontier triggers correctly" holds for the HAPPY path only — lane fires, banner renders,
body delivered 11/11. The floor (M50-10) and the effort-descent ladder (M50-14/15) were NOT exercised
and remain unproven by live evidence. Historical counters show the arm can fire (architect 64,
shadow 62, conductor 51, reviewer 46 retry-matching lines all-time) but none in this window.
What would prove it, named: (a) a forced finish=length probe — drive one anchored consult with the
pre-fix input shape (reasoning_effort=max, max_tokens at/below the observed thinking peak, e.g. 1200)
and assert anchor_budget_retry -> anchor_budget_retry_recovered + a delivered body; (b) an at-ceiling
variant where the ceiling cannot grow, asserting the effort descent (effort_from=max, effort_to=high,
at_ceiling=true) and _retry_is_worth_paying=true, per M50-14b/15.

## CLAIM 4 — baseline discrepancy — SETTLED (and the artefact's before column does not survive)

11-14 renders ledger with canonical heads, records per profile, reproduces the artefact EXACTLY:
  analyst 3 · architect 16 · coder 23 · conductor 14 · evol 1 · operative 9 · orchestrator 48 ·
  reviewer 9 · valmet 4 ; zero on researcher, shadow, m3629canary -> 9 of 12 profiles.
So the artefact's "before, disk" numbers are right about AMBIENT renders. But renders jsonl is the
delivery-surface record (spec §5): those are banner-bearing delivered bodies, so "banner ZERO on the
rest" cannot be true as a fleet statement — it is false against the same ledger it was drawn from.
It can only be true scoped to the failing probe turns. Tested that scope: for each 11-14 empty_response
turn, nearest render in the same session within +/-180s ->
  architect 09:13:19Z -> 1790 chars, 1 head (banner present)
  coder 20:06:20Z -> 597 chars, 0 heads ; conductor 21:08/21:42/22:35 -> 793/776/809 chars, 0 heads
  reviewer 18:25/18:33 -> 759/828 chars, 0 heads ; reviewer 22:54 -> 2433 chars, 2 heads
  i.e. 5 of 8 failing-turn bodies carried no banner, 3 did (2 after recovery in the same turn window).
Settlement: the brief's "ZERO on the rest" is a driven-probe-turn measurement (mostly true, 5/8);
the artefact's 9/12 is an ambient-render measurement (true and reproducible). They are different
quantities, so the before/after banner columns are not like-for-like: "before" counts ambient records
per profile across a day, "after" counts driven turns in a 7-minute window. The artefact never measured
11-14 driven-turn banner presence in the same unit, so no before/after banner delta can be banked.

## FINDINGS

F1 blocking — "before: 6 lines / 3 sessions" does not reproduce (8 lines / 5 sessions, 4 profiles);
   the bullet contradicts the artefact's own rows. Fix the aggregate or the window, then re-print.
F2 blocking — "1 banner per driven turn" false: analyst delivered 2 canonical heads in one body
   (4031-char render), a BANNER-SPEC §1 head-count violation and a §5 second-compose violation.
   Report per-body head counts, not records per session.
F3 blocking — before/after banner comparison mixes ambient-render records with driven-turn banners;
   the "before" column cannot support any improvement claim until an 11-14 driven-turn banner count
   is produced with the after-arm's method.
F4 minor — "0 dropped" overclaims: 6/11 driven turns required anchor_banner_promoted to reach the body.
F5 minor — M50-20_UNIT.json lists files_touched anchor_exec.py + tests/routing/test_r78u_m50_02_budget_retry.py
   and lines_added 136 / lines_removed 23 for a unit the brief defines as "live evidence only; no code
   changes", and the unit produced no commit (head is M50-19's). Provenance defect: M50-19's diff is
   attributed to M50-20.

## BANKED vs NOT BANKED

Survive: after-leg empty_response 0 lines / 0 sessions (11/11); 11/11 delivered bodies; 11/11 driven
sessions carry >=1 canonical head; 11-14 ambient banner record counts (9/12, per-profile numbers exact);
the retry-arm coverage limit and its named remedy; m3629canary / docs / recovery as gaps not passes;
the persisted-transcript-has-no-banner reading (spec §5, verified live).
Do not survive: "6 lines / 3 sessions on 11-14"; "1 banner per driven turn"; "0 dropped" unqualified;
any before/after banner improvement claim built on the current before column.

## NEMESIS GATE (3 hostile attacks, all addressed)

A1 "The after-leg zero is an empty log, not a measurement." -> in-window line counts 324/49/55 on three
   profiles; 0 empty_response among them. Addressed.
A2 "The 11 driven sessions were counted by session id, so ambient banners on the same profile inflate
   the pass." -> every head record was matched to the driven session id from m50_20_live.json; ambient
   records (e.g. orchestrator 20261006_192644_08377425) counted separately and excluded. Addressed.
A3 "The analyst double head is one lane rendered twice, i.e. cosmetic." -> two different labels with two
   different models/hosts and two different spend figures in one body; that is two composes in one
   delivery, which §1/§5 forbid. Addressed.
Recipient POV: the reader of this measurement needs a before column in the same unit as the after
column and a banner count that matches the spec's own head rule; neither exists yet.
