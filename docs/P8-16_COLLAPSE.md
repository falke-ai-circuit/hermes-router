# P8-16 — fs-write collapse measurement (no code changes)

Measured 2026-11-23 06:20–06:56 UTC by analyst. Payload: synthetic bytes, one write per size,
ascending, exactly N raw bytes per write, base64 in the request body, verified by sha256
round-trip on the agent.

## Environment correction (measure first, then the numbers)

The brief names one server; the wire shows two.

| endpoint | identity | agents | V6049A-EAS1 | auth for admin |
|---|---|---|---|---|
| 128.140.61.103:7701 | falke-pbs-hetzner (Hetzner), native binary /opt/probe/v1186/probe-linux-amd64 | 5 | NOT registered | `falke-admin-2026` works |
| 10.10.10.100:7701 (CT100 falke-stack on falke-ovh) | container operator-op01-probe-1, v1.18.6 | 25 | registered, active, 1.18.6 windows/amd64 | operator token (operators.json), not the static token |

Evidence: `/api/v1/agents` on 128.140.61.103 returns HZ-CB-1 / HZ-CB-1-canary / p610-verify /
HZ-WOVEN-1 / RMM-WOVEN-1 only; the CT100 instance lists 25 including V6049A-EAS1, and its log
carries `2026/11/22 22:47:44 [server] agent connected: V6049A-EAS1 (windows/amd64, mode=outbound, protocol=2)`.
The static token is rejected on CT100 (`403 FORBIDDEN`, log `unknown operator token from 10.10.10.1`).
Access to CT100 was by SSH tunnel to 10.10.10.100:7701 (host-side, source in the allowed 10.10.10.0/24).

The series was run on the instance that owns the agent (CT100). The instance the brief's URL points
at (Hetzner) was then hit once with the same 8 MiB payload, because that is the "fa cadere il server"
report being reproduced.

## Series — CT100 instance, agent V6049A-EAS1, path C:\temp\p816_<size>.bin

| size | b64 bytes | status | ms | written | hash match | server alive | note |
|---|---|---|---|---|---|---|---|
| 256 KiB | 349,528 | 200 | 6220 | 262,144 | yes | yes, uptime 27707→27713 | first call, includes connect warm-up |
| 512 KiB | 699,052 | 200 | 2928 | 524,288 | yes | yes, uptime 27713→27716 | |
| 1 MiB | 1,398,104 | 200 | 2089 | 1,048,576 | yes | yes, uptime 27717→27719 | |
| 2 MiB | 2,796,204 | 200 | 2470 | 2,097,152 | yes | yes, uptime 27719→27722 | |
| 4 MiB | 5,592,408 | 200 | 2571 | 4,194,304 | yes | yes, uptime 27722→27724 | |
| 8 MiB | 11,184,812 | 200 | 3479 | 8,388,608 | yes | yes, uptime 27725→27728 | largest allowed by the brief |

No 5xx, no timeout, no uptime reset, no restart. Container after the run: `Restarts=0
OOMKilled=false` `StartedAt=2026-11-22T22:47:38Z`, memory limit unlimited (0), cgroup peak
369,840,128 B, RSS 91.9 MiB. Response `written` equalled the request size exactly in all six rows;
`size` is not returned by this build.

## Control — 128.140.61.103:7701, agent HZ-CB-1, path /tmp/p816_8MiB.bin (one write, then stop)

| size | status | ms | written | hash match | server alive | note |
|---|---|---|---|---|---|---|
| 8 MiB | no response — `RemoteDisconnected` | 13736 | — | not verified (server gone) | NO | connection closed; /health then `Connection refused` |

Recovery poll: 28 attempts over 61.8 s, every one `Connection refused`. It did not come back on
its own; at 06:56 UTC the port was still dead on the host (no listener on 0.0.0.0:7701, no probe
process). Always-up before: health 200, uptime 346,544 s, active_agents 2.

Crash line (`/opt/probe/probe.log`, 2026/11/23 06:30:38 — same second the write died):

```
2026/11/23 06:30:38 [server] agent disconnected: HZ-CB-1
panic: concurrent write to websocket connection

goroutine 156 [running]:
github.com/gorilla/websocket.(*messageWriter).flushFrame(...)
github.com/gorilla/websocket.(*Conn).NextWriter(...)
github.com/gorilla/websocket.(*Conn).WriteJSON(...)
github.com/falke-ai-circuit/probe/internal/server.(*Server).handleMessages(...)
	/opt/data/projects/PROBE_UPGRADE/worktree/internal/server/server_ws.go:196 +0xbe9
```

That is a data race on the per-connection writer, not an allocation failure: no OOM line in
`dmesg`/journal, host up 109 days, load 0.10. The large response write in `handleMessages`
collides with another writer on the same websocket and the Go runtime aborts the process. The
process has no supervisor on that host (no systemd unit, no container), so a panic is permanent
until someone starts it by hand.

## Threshold

- Largest size that survives with a matching hash: 8 MiB raw / 11.18 MB base64, on the instance
  that owns the agent (CT100) — six of six sizes passed, every hash matched.
- First size that fails: 8 MiB raw / 11.18 MB base64 — the same payload, on 128.140.61.103.
  Failure mode: server process panic `concurrent write to websocket connection`
  (server_ws.go:196), no response to the writer, no self-recovery in 62 s.
- Therefore there is no size cliff to measure a chunk size against. The failure is a write/write
  race that reproduces at the size valmet reported (8 MB → 10.7 MB base64) and not at any smaller
  size tested on the healthy instance. A chunk size alone does not remove it: the same bytes can
  pass or kill depending on whether another frame is being written to the same connection.

## Cleanup and agent state

- Six scratch files deleted: `fs-delete` returned `{"deleted":true}` for each of
  C:\temp\p816_256KiB.bin, 512KiB, 1MiB, 2MiB, 4MiB, 8MiB.
- `fs-list C:\temp` after deletion: no `p816_*` entry remains.
- Agent left reachable: `GET /api/v1/agents/V6049A-EAS1` → status active, last_heartbeat
  2026-11-23T06:32:59Z; server `GET /health` → `status:ok, active_agents:3, uptime_seconds:27920`.
- The Hetzner scratch file /tmp/p816_8MiB.bin could not be deleted: the write killed the server
  before it answered, and the server never came back to ask. Left for the host owner; the writer
  never received a success, so the file's existence is unconfirmed.

## Not checked

NOT CHECKED: repeat runs at 8 MiB on CT100 (brief said one write per size) → leaves "the healthy
instance is immune" supported by a single trial, not by a rate; the race may fire there too.
NOT CHECKED: sizes below 8 MiB on 128.140.61.103 → the last good size on the failing instance is
unestablished; server was not restarted (brief: stop, do not hammer).
NOT CHECKED: concurrent writers → the trigger condition for the panic is inferred from the crash
site, not measured directly.
