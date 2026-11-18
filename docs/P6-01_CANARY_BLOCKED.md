# P6-01 — canary V6049A-EAS1 (probe v1.18.6) — BLOCKED

Unit: P6-01, workflow wf_probe1, milestone P6.
Release under test: probe v1.18.6, sha `9eeff87d9359fe83f5c5832cbac64717290fd9f3` (verified on origin/main 2026-11-18T22:01:09Z).

Outcome: not executed. Target agent `V6049A-EAS1` (windows/amd64) is not connected to the probe
server (`POST /api/v1/agents/V6049A-EAS1/exec` → 503 `agent V6049A-EAS1 not connected`); last
heartbeat 2026-11-18T22:03:16Z. No v1.18.6 windows agent payload exists in the build store, and the
live server reports v1.18.5, so its builder cannot produce one.

Assertions: 0/4. No update was delivered; old binary untouched by this unit; machine state
unverifiable while offline.

Product finding: `POST /api/v1/agents/{id}/update` answers HTTP 200 `update_in_progress` even when
the agent is disconnected (any forward error is mapped to that status) — phantom success for any
harness that trusts the status code.

Full evidence, request/response log and timeline: `/opt/data/tmp/probe1/P6-01_CANARY.md`.
No push; no fleet restart; no other agent touched.
