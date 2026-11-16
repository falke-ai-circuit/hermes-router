# M51-08 AUDIT — independent verification of the negative_low_effort slice

unit: M51-08
role: reviewer (independent; author of none of these units; read-only)
head: 87463689a8ea7e272b9cc903e2ac57e26f53f42b
repo: /opt/data/plugins/hermes_router
slice: 92e6b39 (completion_audit.py arm) + 8746368 (core/delivery_decision.py banner)
run: 2026-11-16 11:08–11:20 UTC

VERDICT TIER A: PASS
VERDICT TIER B: DEFERRED (no real fire row exists yet — zero is neither pass nor failure)

## Repo anchor

    $ git rev-parse HEAD          87463689a8ea7e272b9cc903e2ac57e26f53f42b
    $ git rev-list --left-right --count origin/main...HEAD
    0       0

Both commits present on main, ahead=0.

## A1 ARM PRESENT AND SELECTABLE — PASS

Label exists in the standard POST fire chain, beside work_mass / closure /
risk_report, and is emitted through the EXISTING post_reflection_fired row:

    completion_audit.py:1615  if not _fire:
    completion_audit.py:1617      _nle = negative_low_effort_gate(_wm_ctx, response_text or "")
    completion_audit.py:1619      if _nle.get("fire"):
    completion_audit.py:1620          _fire = True
    completion_audit.py:1621          _fire_trigger = "negative_low_effort"

    completion_audit.py:1640  _log("post_reflection_fired", trigger=(_fire_trigger or "unknown"),
                                  tools=int(_wm["tools"]), files=..., muts=...,
                                  rework=..., paired=bool(_pair), session_id=session_id)

No `negative_low_effort=` field on that row: the label rides `trigger=` (R42 coverage),
exactly as the brief requires. Threshold and nominator:

    completion_audit.py:520   _NLE_MAX_TOOLS = 5
    completion_audit.py:525   _DECLARED_LIMIT_RE  (EN/IT/HR/DE families, loose nominator)
    completion_audit.py:612   def negative_low_effort_gate(context, response_text)
                              refusal-class first -> declared-limit text test -> tools <= 5
                              every error -> {"fire": False} (fail-open)

## A2 DIFF SCOPE — PASS

    $ git show --stat 92e6b39
     completion_audit.py | 240 ++++++++++++++++++++++++++++++++++++++++++--
     1 file changed, 231 insertions(+), 9 deletions(-)

One file. PRE, risk.stage1, dispatcher_pre.py: 0 lines. The 9 deletions are signature
rewrites, not behaviour removals (each added `negative_low_effort: bool = False`):

    -                   fold_char_cap: int = 0) -> List[Dict[str, str]]:
    +                   negative_low_effort: bool = False) -> List[Dict[str, str]]:
    -                         scope_id: str = "", scope_source: str = "") -> None:
    +                         scope_id: str = "", scope_source: str = "",
                                  negative_low_effort: bool = False) -> None:

Grep of every added line for `_cfg(`/`config_access`/`banner`/`consult(`/new sync call:
0 hits. Default False at every call site, so no existing caller changes behaviour.
No second round trip: the route rides the existing payload tail (`_audit_payload`, 2474):

    + (_negative_route_instruction() if negative_low_effort else "")

Out of scope: nothing. The threaded boolean reaches `_consult_meta` kwargs only.

## A3 DEPLOY PROOF BY BYTES — PASS (11/11), process-start VERIFIED (own measurement)

Expected repo bytes match the brief exactly:

    completion_audit.py        899330ccff6991612fae1dcebd779c8b
    core/delivery_decision.py  7e5513d4dde63427302378cfbdb98cea

Per-profile md5, 11 live profiles — all equal:

    analyst architect coder conductor evol operative orchestrator
    researcher reviewer shadow valmet   -> 899330cc... / 7e5513d4...

The deploy script fingerprint b160fca8b71b was not used (version string only).

Two further profile trees carry the plugin and do NOT match, and neither is a live
profile — so the 11-profile deploy set is complete, not short:

    recovery      f1f9678f... / f678c96f...   no gateway-recovery service exists
    m3629canary   f1f9678f... / f678c96f...   s6 slot: down (SIGTERM) 149380s

Process-start, measured here (the brief expected this to be unverifiable locally; it
is not — s6-svstat + ps answer it directly):

    $ /command/s6-svstat /run/service/gateway-<p>
    analyst 2025s  architect 1932s  coder 1932s  conductor 1687s  evol 1932s
    operative 2024s  orchestrator 1932s  researcher 2020s  reviewer 1933s
    shadow 1932s  valmet 1932s            (all: up)
    $ ps -o lstart= -p 526766  ->  Mon Nov 16 10:40:16 2026

Against 8746368's commit time (Mon Nov 16 10:34:03 2026) every service start
(10:36:12–10:40:16) is AFTER the commit, so the running processes loaded these bytes.
This is my own measurement, not conductor's 10:35–10:36 report.

## A4 INVARIANTS INTACT — PASS

Work-mass precedence is structural — the arm is inside `if not _fire:` after all three
heavier arms, and disjoint by predicate (heavy needs tools>=40 / files>=5 / muts>=10;
this arm requires <=5 rows):

    completion_audit.py:1542  if _wm["fire"]:
    completion_audit.py:1546      _fire = True
    completion_audit.py:1547      _fire_trigger = "work_mass_" + str(_wm["reason"])
    completion_audit.py:1615  if not _fire:            # closure, then risk_report, then NLE

One-consult-per-turn and the once-per-task marker are untouched (zero lines of
eligible() edited; the diff of that function is empty):

    completion_audit.py:1061  key = _fire_marker_key(session_id, ask, model, scope_id=scope_id)
    completion_audit.py:1062  if _already_fired(key):
    completion_audit.py:1063      return False, "already_fired_this_task"
    completion_audit.py:1097  if _st.pre_fired_this_turn(session_id, current_turn=turn_n):
    completion_audit.py:1098      return False, ("pre_consult_this_turn" if not paired
                                                       else "pre_consult_same_ask")
    completion_audit.py:2572  _mark_fired(key)     # marker set at FIRE, before the consult

Empty/blank consult output collapses to NONE in code, not in the prompt:

    $ normalise_negative_route("   \n ")      -> 'NONE'
    $ normalise_negative_route("NO-FINDINGS.") -> 'NONE'
    $ normalise_negative_route("Re-run with --force") -> 'Re-run with --force'

Direct gate probe (my run, synthetic context):
    declared limit + 2 tool rows  -> fire=True  (fixture-wired context)
    clean body                    -> no_declared_limit
    refusal ("I won't ...")       -> refusal_class, no fire
    synthetic context without request["messages"] -> effort_unreadable, no fire
The last line is expected, not a defect: `_turn_messages` reads
context["request"]["messages"] else the session DB; a made-up session id has no
transcript. Live work_mass rows (95 `trigger=work_mass_mass`, 4 `work_mass_impact`)
prove that context source is populated at the real seam.

## A5 BANNER SURVIVAL — PASS (with one behavioural note, F2)

    core/delivery_decision.py:501  def _is_own_banner_line(line)
    core/delivery_decision.py:515      return str(line or "").lstrip().startswith(_BANNER_HEAD)
    core/delivery_decision.py:546      if _is_own_banner_line(line): banners.append(line); continue
    core/delivery_decision.py:558      core_text = "\n".join(kept).strip()
    core/delivery_decision.py:559      if not core_text: return ""   # banners only -> void

Probes (my run):
    body + banner            -> 'The answer stands.\n· router · higher-self lane fired ·'
    banners only             -> ''
    quoted span, original case -> marker_is_echo True  (exemption is case-insensitive)
    unquoted marker          -> marker_is_echo False   (still a leak)

    $ python3 -m pytest tests/delivery/test_m50_19_marker_never_withholds.py -q
    7 passed in 0.72s
    $ python3 -m pytest tests/delivery -q
    116 passed in 50.69s

F2 (minor, non-blocking): a LEAD banner is relocated to the TAIL.
    strip_marker_lines("· router · ...\nThe answer stands.")
      -> 'The answer stands.\n· router · ...'
The docstring at 511-513 says a mid-body banner "is left to the normal marker rules",
but code appends every own-banner line to the tail regardless of position. Survival
(M48) holds; byte ORDER changes. Worth one line in the next unit, not a blocker for
this slice.

## DEFERRED-PENDING (M51-02) — graded because the artefact exists

    tests/test_m51_02_five_event_fixture.py  (10820 B, mtime 10:48, UNTRACKED)

    $ pytest tests/test_m51_02_five_event_fixture.py -q
    11 passed in 2.74s

Fail-when-arm-removed, demonstrated by me without editing the file (out-of-repo pytest
plugin that replaces `negative_low_effort_gate` with a no-fire lambda):

    6 failed, 5 passed in 1.39s
    FAILED test_e1_valmet_checkin_premature_handback_fires
    FAILED test_e1_predicate_label_is_the_only_nle_site
    FAILED test_refusal_class_never_fires_even_at_zero_effort
    FAILED test_tool_rows_exactly_five_fires
    FAILED test_tool_rows_six_does_not_fire
    FAILED test_no_declared_limit_never_nominates

The fire assertions are load-bearing; the fixture is not decoration. It is the edge
behaviour (threshold, refusal, nominator) that fails with the arm removed. E1/E2.
Node-id set-difference suite delta: NOT produced by M51-02 yet — remains owed.

## TIER B — DEFERRED

Reason: no real fire row exists. Measured at 11:09 UTC (deploy+35min) by my own read of
the route logs of all 11 profiles:

    $ grep -rh 'POST event_detail=post_reflection_fired' */uncensored-router.log \
        | grep -oE 'trigger=[a-z_]+' | sort | uniq -c
      352 trigger=cadence
      168 trigger=closure
       83 trigger=risk_report
        4 trigger=work_mass_impact
       95 trigger=work_mass_mass
        0 trigger=negative_low_effort

The earlier "11 rows" figure is confirmed as a grep artifact: every hit for the string
`negative_low_effort` is the BRIEF FILENAME inside unrelated rows, e.g.

    2026-11-16T10:20:22Z reflex_outcome ... paths=['/opt/data/plugins/hermes_router',
      '/opt/data/profiles/orchestrator/briefs/M50-22_negative_low_effort.md']

(coder 3, conductor 2, orchestrator 6, conductor renders 4 — none is a fire row).
Zero here means "not yet observed", never "passed" and never "failed".

What closes Tier B, in one sentence: one real post_reflection_fired row naming
trigger=negative_low_effort on a canary turn, with no second fire on that turn, a
fleet rate near ~1 per 17 sessions, and a ledger read of whether the agent attempted
the route on the next turn.

## Hostile attacks run (nemesis gate)

1. Inherit conductor's deploy/start window — refused; measured s6 uptime + ps lstart.
2. Accept the deploy fingerprint as proof — refused; per-profile md5 only.
3. Read "11 NLE rows" as live behaviour — refused; traced to a filename string.
4. Assume the fixture is real because it passes — falsified by bypassing the arm
   (6 failures), so the assertions bind the code.
5. Assume "no new field" from the commit message — checked the diff and the row
   kwargs; the label rides `trigger=`, no new field.
6. Assume the banner survives because a test passes — probed the function directly,
   including banners-only and quoted-casing cases.

Router note: a decision-lane reading during this audit proposed editing
completion_audit.py (naming _claiming_turn_tool_rows / _turn_messages). The brief
forbids edits and this lane is read-only; not followed, recorded here as observed
behaviour of the reading, not as a defect of this slice.

## Open items

1. TIER B unmeasured — needs a real low-effort canary turn (the arm's predicted rate
   is low by design).
2. M51-02 node-id set-difference suite delta still owed.
3. F2 banner-to-tail relocation vs docstring claim.
4. tests/test_m51_02_five_event_fixture.py was untracked (M51-02 in flight); committed
   as-found by this close, content byte-identical (md5 aef3258f237ff064c68b916110dc346a
   at commit time), ownership and its remaining obligations stay with M51-02.

reviewed_by: reviewer
verdict: TIER A PASS | TIER B DEFERRED
verdict_sha_of_head: 87463689a8ea7e272b9cc903e2ac57e26f53f42b
