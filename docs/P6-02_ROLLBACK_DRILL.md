# P6-02 — Rollback drill: broken build through the product's own update path

| field | value |
|---|---|
| unit | P6-02 (workflow wf_probe1, milestone P6) |
| owner | operative = delivery |
| repo | /opt/data/projects/PROBE_UPGRADE/worktree, branch probe-upgrade-w1 |
| HEAD | 5d0bdacebd99b5c12d7221716ee4a20bd02d56cc (verified `git rev-parse HEAD`) |
| live server | http://139.99.209.235:7701 (= probe:7701), server_version v1.18.5 |
| server under test | drill client built from HEAD source, appVersion v1.18.6 |
| outcome | **PARTIAL — assertions (a),(b),(c) PASS; assertion (d) BLOCKED (no journal exists on this path). Rollback observed: NO — the swap never ran, so no restore was exercised.** |
| unit_cap | 40 min — used ~10 min to first blocked assertion |

## 0. Headline

The drill executed the product's own update path end to end: server dispatch → agent download →
SHA-256 verify → `.next` staging → spawn of the staged binary → staged binary exits → old process
keeps running. Assertions (a), (b) and (c) hold. Assertion (d) cannot be evaluated on this host:
**no journal is written by any code reachable on the Linux/`probe-client` path** — the only
`p2boot.journal` writer in the tree is `internal/upgrade/scripts/p2boot.ps1`, a PowerShell script,
and `pwsh`/`powershell` are not installed (`which pwsh powershell` → rc=1). The 60 s
`n_rollback_s` deadline is likewise never armed on this path. Steps 5(d) and 6 are therefore
reported blocked, not passed.

Rollback itself was **not exercised**: the broken build died before the canary could prove health,
so the binary on disk was never displaced and no restore was needed. That is the product behaving
as designed for a pre-swap abort, but it is not a rollback.

## 1. Known-good artifact (step 1)

```
$ sha256sum build/probe-client-linux-amd64
0547bd68f6ebf27f1d187e1637b00bf8498763bc2d33ab755b374423e4a5b501  build/probe-client-linux-amd64
```
Matches the brief's expected `0547bd68…b501` for 1.18.6 at 5d0bdac. No rebuild was needed.

## 2. Deliberately broken build (step 2)

Built from HEAD source, not from a hand-edited worktree: `git archive HEAD | tar -x -C
/opt/data/tmp/probe1/p602/broken` (repo untouched, `git status --short` empty at close).

Single mutation in the extracted copy, `cmd/probe-client/main.go`, immediately after `flag.Parse()`
in `runClient()`:

```go
	// P6-02 drill: deliberately broken build. Exits before any connection is
	// attempted, so no sustained session can ever be established.
	log.Printf("[p6-02] broken build: exiting before connect")
	os.Exit(3)
```

Build:
```
$ cd /opt/data/tmp/probe1/p602/broken
$ env -u GOROOT -u GOPATH CGO_ENABLED=0 /opt/data/go/bin/go1.23.12 build -trimpath \
    -o /opt/data/tmp/probe1/p602/broken/probe-client-broken ./cmd/probe-client
rc=0
$ sha256sum probe-client-broken
f5c9372ff816b3ef644e237dd64dbaf9ff6a3252390210463fc2d48f5a625f2e  probe-client-broken
10123826 bytes
```

Broken artifact kept at `/opt/data/tmp/probe1/p602/broken/probe-client-broken`
(sha256 `f5c9372ff816b3ef644e237dd64dbaf9ff6a3252390210463fc2d48f5a625f2e`, 10123826 bytes).

Direct proof it fails the establishment check:
```
$ ./probe-client-broken --config probe-client.json
2026/11/18 23:37:07.806797 main.go:150: [p6-02] broken build: exiting before connect
exit=3
```

## 3. Good client ACTIVE before any swap (step 3)

Launched locally: `./probe-client --config probe-client.json` in `/opt/data/tmp/probe1/p602/good`,
config `{"server":"ws://probe:7701/ws","token":"falke-admin-2026","name":"P6-02-DRILL",
"mode":"silent","permissions":"full"}`. Canonical binary = the 0547bd68 artifact.

Row before any swap (GET /api/v1/agents, literal):
```
{"agent_id": "P6-02-DRILL", "name": "P6-02-DRILL", "version": "1.18.6", "os": "linux",
 "arch": "amd64", "mode": "outbound", "connected_at": "2026-11-18T23:36:10Z",
 "last_heartbeat": "2026-11-18T23:39:43Z", "status": "active", "uptime_seconds": 221,
 "error_count": 0, "health_score": 0.8832956432262222}
```
```
$ sha256sum /opt/data/tmp/probe1/p602/good/probe-client
0547bd68f6ebf27f1d187e1637b00bf8498763bc2d33ab755b374423e4a5b501  probe-client
$ ls *.next        -> No such file or directory
```

Permission note: first dispatch was refused by the agent itself —
`{"error":"command type 'agent_update' is not allowed under permissions 'read-only'"}`.
`permissions.go` allows `TypeAgentUpdate` in no tier except full, so the agent was relaunched with
`"permissions":"full"` for the drill. See finding F3.

## 4. Driving the swap through the in-tree path (step 4)

Payload made visible to the server at `/data/p602-broken` (the probe container's `/data` volume;
the workspace path `/opt/data/workspace-operative/probe/runtime` maps to `/data/runtime`, which is
root-owned, hence the volume copy). Dispatched via the shipped endpoint:

```
T0=2026-11-18T23:39:55Z
$ curl -X POST -H "Authorization: Bearer <admin>" -H 'Content-Type: application/json' \
    -d '{"binary_path":"/data/p602-broken","version":"1.18.7-broken"}' \
    http://139.99.209.235:7701/api/v1/agents/P6-02-DRILL/update
{"ok":true,"data":{"message":"agent_update command sent. The agent will download and restart.
 "Check /api/agents for the new version.","status":"update_in_progress","version":"1.18.7-broken"}}
T1=2026-11-18T23:40:25Z
```

Agent-side sequence (literal, `logs/probe-client-20261118-233943.log`):
```
2026/11/18 23:39:55.184826 agent_update.go:38: [update] received update command: version=1.18.7-broken, file=p602-broken, ws_path=""
2026/11/18 23:39:55.185032 agent_update.go:55: [update] downloading from http://139.99.209.235:7701/download/p602-broken?token=falke-admin-2026 to /opt/data/tmp/probe1/p602/good/p602-broken.tmp
2026/11/18 23:39:55.257322 agent_update.go:78: [update] downloaded 10123826 bytes
2026/11/18 23:39:55.288585 agent_update.go:96: [update] SHA256 verified: f5c9372ff816b3ef644e237dd64dbaf9ff6a3252390210463fc2d48f5a625f2e
2026/11/18 23:39:55.288638 agent_update.go:100: [update] current executable: /opt/data/tmp/probe1/p602/good/probe-client
2026/11/18 23:39:55.289236 agent_update.go:150: [update] new process started: PID=712210, old PID=712162
```
Spawned process log (its own file, `logs/probe-client-20261118-233955.log`, 85 bytes):
```
2026/11/18 23:39:55.293159 main.go:150: [p6-02] broken build: exiting before connect
```

Disk state after (t+2…12 s samples, all identical):
```
.next sha256  f5c9372ff816b3ef644e237dd64dbaf9ff6a3252390210463fc2d48f5a625f2e  probe-client.next  (10123826 bytes)
canonical    0547bd68f6ebf27f1d187e1637b00bf8498763bc2d33ab755b374423e4a5b501  probe-client
processes matching probe-client.next: 0
```

## 5. Assertions (step 5)

| # | assertion | result |
|---|---|---|
| a | broken build does NOT stay running | **PASS** — PID 712210 exited within 108 ms of spawn; `ps` count for `probe-client.next` = 0; only its 85-byte log remains |
| b | previous binary is restored | **PASS, but vacuously** — canonical still 0547bd68…b501 because it was never displaced. No restore ran; `.next` remains staged as the failed candidate |
| c | agent active again under the SAME name | **PASS** — one row, `P6-02-DRILL`, status active, `last_heartbeat` 23:40:43Z advancing, `error_count` 0, same `connected_at` 23:36:10Z (old process never died) |
| d | journal shows the rollback events with timestamps | **BLOCKED** — no journal file is produced on this path. `ls *.journal p2boot.*` → No such file or directory. `grep -rn "journal" internal/agent cmd` (non-test) → no matches: no Go code writes one. `internal/upgrade` is imported by nothing (`grep -rln "internal/upgrade"` → no importers outside the package); its only emitter is `scripts/p2boot.ps1`, which requires `powershell`/`pwsh` — absent on this host (rc=1), and its contract is Windows-path only (`IsAbsWinPath`, `C:` paths) |

Literal journal lines: none exist to quote. That is the finding, not an omission.

## 6. Elapsed vs n_rollback_s (step 6)

| event | timestamp | source |
|---|---|---|
| dispatch issued | 2026-11-18T23:39:55Z | curl T0 |
| update received by agent | 23:39:55.184826 | agent log |
| staged binary spawned | 23:39:55.289236 | agent log |
| staged binary exited | 23:39:55.293159 | spawned-process log |
| old agent still active | 23:40:43Z heartbeat | API row |

Commit-start → abandoned: **108 ms** (23:39:55.184826 → 23:39:55.293159). No `COMMIT_START` event
is written anywhere on this path, so this is measured from update receipt, not from a journal line.

Comparison to `DefaultNRollbackS = 60`: **DIVERGES — the deadline is never armed.** `grep -rn
"NRollbackS" --include=*.go` (non-test) returns only `manifest.go` (declaration and default
fill); its single consumer is `p2boot.ps1:681` (`Get-OptInt -Obj $m -Key 'n_rollback_s' -Default
60`). The Linux path's only timing constant is `canaryProveInterval = 10 * time.Second`
(`internal/agent/canary.go:21`), and it was not reached because the canary never connected.

## 7. Findings (recorded, not patched)

**F1 — the update path cannot reach a swap on `probe-client` agents.** `handleAgentUpdate` spawns
the staged binary with `[]string{"connect", "--config", …, "--canary", "--canary-old-pid", …}`
(agent_update.go:143). Those are `cmd/probe`'s subcommand interface: `--canary`/`--canary-old-pid`
are defined only in `cmd/probe/connect.go:53-54`. `cmd/probe-client` has a single flag
(`--config`, main.go:50) and no subcommands, so Go's `flag.Parse` stops at the positional `connect`
and never sees `--canary`. Consequence: a *healthy* staged build would re-register under the
canonical name without entering canary mode and without ever swapping — the `.next` file would stay
on disk indefinitely and the old process would never be stopped. The rollback sequence in
`canary.go` is therefore dead code for this agent class.

**F2 — no journal on the Linux path at all.** Journal events (`COMMIT_START`, `PARKED`,
`ROLLBACK`, `ROLLED_BACK_EARLY/LATE`, `OutcomeRollbackFailed`) and the `n_rollback_s` patience
deadline exist only in the PowerShell commit script. Nothing in `internal/agent/` writes a journal,
and `internal/upgrade` has no caller in `cmd/`. Any rollback claim for Linux agents is unobservable
by construction.

**F3 — `TypeAgentUpdate` is unreachable below `permissions: full`.** `permissions.go` lists it in
no tier (sandboxed/standard/read-only all fall to `default: false`). Fleet agents at read-only or
standard cannot be updated at all; the server reports success anyway (see F4).

**F4 — dispatch result is a false positive.** The endpoint returns
`{"status":"update_in_progress"}` on any `forwardToAgentWithTimeout` error, including "agent not
connected" (server_update.go). Confirmed here in a different form: an agent-side permission refusal
still yields HTTP 200 `update_in_progress`.

**F5 — DELETE leaves the row listed (observed on the live v1.18.5 server).** Cleanup:
```
$ curl -X DELETE .../api/v1/agents/P6-02-DRILL
{"ok":true,"data":{"removed":"P6-02-DRILL"}}
$ GET /api/v1/agents  -> P6-02 rows: 1
{"agent_id":"P6-02-DRILL", …, "status":"inactive", "last_heartbeat":"2026-11-18T23:41:01Z"}
$ GET /api/v1/agents/P6-02-DRILL -> HTTP 200 (same row)
```
HEAD's handler calls `s.registry.Remove(agentID)` (api_v1.go:513) and returns `removed`, so the
persistence is server-side behaviour of the deployed v1.18.5, not necessarily of HEAD. The row is
gone from the *active* set but not from the listing; "the row is gone" is NOT demonstrated.
NOT VERIFIED whether HEAD's build behaves the same — that needs a locally run 1.18.6 server, out of
this unit's scope. This reproduces the DELETE-leaves-row defect already reported by P6-05 (2/2 on
this same live v1.18.5 server): the P6-04 fix is in HEAD, not in the deployed image.

## 8. Cleanup (step 7)

- local client killed: `pgrep -af "probe-client --config"` → 0
- `.next`, temp `.tmp`, and the workspace/volume copies of the broken payload removed
  (`pct exec 100 -- rm -f …/_data/p602-broken` → `removed_volume_copy`)
- active agents after cleanup: 2 — `operative-stack-test`, `gorproxmox` (both pre-existing, neither
  created by this unit). P6-02-DRILL is `inactive` (see F5)
- repo untouched: `git status --short` empty; no commit required from this unit

## NOT CHECKED

- Windows/`p2boot.ps1` commit→rollback behaviour (no PowerShell on host, target V6049A-EAS1 offline)
- Whether a healthy (non-broken) build would swap on `probe-client` — F1 is read from source, not
  exercised, because exercising it risks leaving a duplicate canonical-name agent behind
- HEAD server's DELETE persistence (F5) — only the deployed v1.18.5 was observed
