# P9-08 — UPDATE CONTRACT **v3** (remediation of the six blocking P9-07 findings; no code)

**SELF-CHECK (A5):** F1 **CLOSED-IN-V3** · F2 **CLOSED-IN-V3** · F3 **CLOSED-IN-V3** (fallback deleted, not conditioned) · F4 **CLOSED-IN-V3** · F5 **CLOSED-IN-V3** · F6 **CLOSED-IN-V3**

| field | value |
|---|---|
| unit | P9-08 · milestone P9 · workflow wf_probe1 · owner `architect=contract-remediation` |
| artefact | `/opt/data/tmp/probe119/P9-08_CONTRACT_V3.md` (canonical copy committed at `.workflow/architect/P9-08_CONTRACT_V3.md`) |
| version | **v3** — delta over v2; changelog in §2 (A2). v2 is `/opt/data/tmp/probe119/P9-01_UPDATE_CONTRACT.md`, 62,841 B, sha256 `792c113c12463d563ca60b0c52552799d853c55310f0229bb34c81f57d373174` |
| reviewed_by | **pending** — independent reviewer leg (P9-07's successor) not yet run on v3 |
| verdict | **REMEDIATION COMPLETE — six blocking findings closed; NOT RATIFIED, ratification is Goran's decision** |
| verdict_sha_of_head | _filled by the close record — this header does not grade the tree it sits in_ |
| tree read | `/opt/data/projects/PROBE_UPGRADE/v119wt`, branch `r119-build`, unit head **`0352f3b71d801902ea32703299aebf18bcfe069f`** |
| source identity | `git diff --stat 0352f3b 2a2ed08` → **3 files, +226, all under `.workflow/architect/` (documentation only)**; therefore every `file:line` below resolves identically at `0352f3b` and at the current head `2a2ed08`. `git status --porcelain` = 0 lines |
| research leg | **NONE** — evidence is the P9-07 review plus source re-opened in the tree. No external lookup, no new measurement claimed |
| shape | **SHAPE none** — this unit adds no code. The closure commit adds this document only |
| method | (1) read P9-07 end to end; (2) for every citation in v2 and in the review, opened the range in the tree and re-derived the claim; (3) wrote the delta as normative text with the exact line that carries it; (4) re-ran the four affected packages (suite in §9) |

**What this document is and is not.** It is the delta that a reviewer can grade *alone*: v3 inherits v2's §0.1 evidence rows, §2 four checks (with the corrections below), §5 idempotency, §6 channel arithmetic, §7 API shapes (except §7.4), §8 UI, §9 Windows console, §10 backward compatibility, §13 projections, §15 acceptance map. v2 text that a subsection below explicitly replaces is **void**, not "also true". Nothing here implements anything; P9-03..P9-06 stay blocked behind ratification.

---

## 1. Citation discipline for this pass

The P9-07 review's central method was re-deriving every citation, and it found one fabricated source and five wrong ones. v3 was written under the opposite rule: **no `file:line` appears below that I did not open in this tree at `0352f3b` in this session.** Where a v2 citation was wrong, the correction names the wrong line and the right one, so the reviewer can see the error was found rather than papered over.

Citations re-opened for this document (all verified in `0352f3b`):

`internal/agent/agent.go:176-178, 227-230, 432-440, 453-455, 578, 1117-1145` ·
`internal/agent/agent_proc.go:36-76, 44-52, 82-110, 128-157, 159-180` ·
`internal/agent/agent_update.go:18-29, 47-51, 84-95, 100-122, 124-150, 159-180` ·
`internal/agent/canary.go:15-26, 33-75, 77-86, 97-139, 141-175, 177-209, 211-235` ·
`internal/agent/canary_launch.go:20-31, 33-53` ·
`internal/protocol/messages.go:205, 219-227, 587-592, 598-606, 787-793, 798-803` ·
`internal/server/server_ws.go:16-34, 82-116, 118-142, 166-191` ·
`internal/server/server_update.go:18-25, 104-128, 130-159` ·
`internal/server/server_api.go:112-113, 285-320, 470-490, 795-813` ·
`internal/server/idempotency.go:1-70` ·
`internal/server/api_v1.go:186-222, 330-357, 386-387, 415-423` ·
`internal/server/registry.go:32-55` ·
`internal/upgrade/journal.go:9-52, 54-85, 87-147, 149-205` ·
`internal/upgrade/rollback.go:18-40, 42-71, 73-91, 93-116` ·
`internal/platform/platform_windows.go:239-260` · `internal/platform/shellargv.go:26-34` · `cmd/probe/connect.go:48-56, 99-110` · `go.mod`

---

## 2. Changelog v2 → v3 (A2 — grade the delta only)

| # | v2 (as reviewed) | v3 | why (cite) | closes |
|---|---|---|---|---|
| **C1** | §4.1/§4.2: server issues `proc_kill`; new process executes; canary registers `CanaryOldPID` in `spawnedPIDs`; **60 s autonomous fallback** (row 3) | **one** authorization mechanism (§3/F1) with exactly two registration sites; **fallback deleted**; new invariant I-1 | `agent_proc.go:44-52` gate has one input (`spawnedPIDs`); a fallback kill with no order is the owner-forbidden self-kill | F1, F3 |
| **C2** | §4.3 L1: identity from `AgentUpdateResult.OldPID` **and** "superseded connection's PID recorded server-side at connect (`server_ws.go:93`)" | `server_ws.go:93` is `agentID := info.Name` — **the citation was fabricated and is deleted**. A real on-wire PID is added (`AgentInfo.PID`) with a real record site; legacy jobs declare `identity_source:"update_result"` and `conn_pid:null` | `server_ws.go:93`, `:107`, `:113-116`; `messages.go:219-227` carries no PID | F2 |
| **C3** | §4.3 L2: `name == filepath.Base(canonical)` | truncation-safe `commMatches` (POSIX 15-byte `comm`) **plus** a mandatory file-identity proof (inode on POSIX, start-time+name on Windows) — the name check is a pre-filter, never the decision | `agent_proc.go:161` (`ps -eo pid,comm,…`), Linux `TASK_COMM_LEN`; `agent_proc.go:129-157` (tasklist full name) | F2 |
| **C4** | §3.1 `watchdog_n = 90 s`, "no slack"; §3.2 restore with no exclusion primitive | **`watchdog_n = 120 s`** (40 connect + 60 promote + 20 margin) **and** an exclusive swap lease + monotonic epoch; the watchdog is a non-blocking contender that never overrides a live swap | `canary.go:149-175` (two renames, no lock), `canary.go:126-139` (unlocked restore) | F4 |
| **C5** | §3.3/§1.1 journal lines written by "the old process / the canary" with no component named | writer named: `internal/upgrade` gains `writer.go`; file `<canonical>.update.journal`; append+fsync **before** the state is reported; `internal/agent` imports `internal/upgrade` (first importer in the tree) | `journal.go:67-85` (`FormatLine`), `:110-147`, `:170-205`; zero importers today | F5 |
| **C6** | §7.4: `LOCK_BUSY`, `UPDATE_IN_FLIGHT`, `UPDATE_PREVIOUSLY_FAILED` as a new 409 family, plus a body shape never specified | two **existing** codes reused (`IDEMPOTENCY_IN_FLIGHT`, `IDEMPOTENCY_STATE_UNKNOWN`) + **one** new name (`UPDATE_PREVIOUSLY_FAILED`); exact body in the tree's own flat `{code,message,detail}` envelope | `server_api.go:303`, `:309`, `:482-490`; `api_v1.go:186-204` | F6 |
| **C7** | §3.3 example journal line `ts=2026-11-23T13:02:11Z` | **`ts=<unix-seconds int>`** — the parser is `strconv.ParseInt(…,10,64)`; an RFC3339 stamp parses to 0 and silently loses the event time | `journal.go:126` (parser) vs `:67-71` (`FormatLine` emits `Unix()`) | self-found, see §5 |
| **C8** | §2.1(b) discriminator `name == id` | dropped as a check — it is true by construction; (b) is `conn_pid`, `version_registered`, `canary_row_remaining:false`, `last_seen` | `server_ws.go:93` + `:107` (`Register(agentID, info.Name, …)`) | self-found |
| **C9** | §0.4 X3: "there is no `echo` on Windows", probe changed to `cmd /c echo …` | **X3 retracted as a defect.** `exec` already wraps in `cmd`; the marker command is a bare `echo probe-update-ok` on both platforms. Keeping `cmd /c` is permitted but it is not a fix | `platform_windows.go:241-253` + `shellargv.go:28-34` (`/c <command>`) | review note |
| **C10** | three names for one number (`watchdog_n`, `PROBE_UPDATE_WATCHDOG_S`, `confirm_timeout_s`) | **one** knob: `PROBE_UPDATE_WATCHDOG_S` (default 120). `confirm_timeout_s` is derived server-side and validated `>= watchdog`, never stored as a second number | v2 D-03/§7.1 inconsistency | R4 note |
| **C11** | §2.4 cites `journal.go:158 OutcomeOldPIDAlive` as the event | both named: event string = `OLDPID_ALIVE` (`journal.go:38`), outcome constant = `OutcomeOldPIDAlive = "old_pid_alive"` (`:158`) — they are different objects and the job field must carry the event | `journal.go:38`, `:158` | self-found |
| **C12** | §7.2 sample shows `identity_exe` on a 1.18.6 job | sample shows `identity_source` + `conn_pid`; `identity_exe` only when `identity_source:"proc-list-exe"` | §3/F2 | review note |

Non-blocking citations from the review's own notes are corrected here rather than left: **M16** route is `api_v1.go:422` (the cited `352-356` is proc-list/proc-kill/proc-start); **M13** struct is `server_update.go:20-25` (the cited `server_ws.go:20-25` is `writeToAgentLocked`); `NotifiedAt` is written at `server_update.go:154` and read nowhere.

---

## 3. The six findings — per-finding table (A1)

| finding | v3 answer, in one line | source region re-opened (0352f3b) | what a test can assert |
|---|---|---|---|
| **F1** bootstrap kill authorization | A **`kill_authorization`** record (new map, beside `spawnedPIDs`), populated by exactly two registrations — canary argv at startup, and a server-issued `update_adopt` on the legacy hop — single-PID, single-use, TTL-bounded; the sandbox gate accepts `spawnedPIDs[pid] \|\| killAuth valid(pid)`; **bypass is not used** | `agent_proc.go:44-52`, `agent.go:227-230`, `agent.go:453-455`, `canary_launch.go:24-31`, `connect.go:54-55`, `server_api.go:808` | `permissions="standard"` + `killAuth[pid]` → kill allowed; same without `killAuth` → `permission_denied`; expired/reused/other-PID → refused; a normal (non-canary, non-adopt) agent holds zero authorizations |
| **F2** check-C identity | Delete the fabricated source. Add `AgentInfo.PID` (omitempty, real record site at connect) **and** a truncation-safe POSIX `comm` match **and** a mandatory file-identity proof; Windows rule stated separately (full image name + creation time) | `server_ws.go:82-116`, `messages.go:219-227`, `messages.go:587-592`, `agent_proc.go:129-157`, `agent_proc.go:161`, `agent.go:432-440` | `commMatches("probe-client-lin","probe-client-linux")==true`; a 15-byte different-name prefix `==false`; `AgentInfo{PID:…}` survives the connect path; a 1.18.6 job reports `identity_source:"update_result"`, `conn_pid:null`; PID-absent → check (c) passes |
| **F3** the 60 s fallback | **Deleted.** Invariant I-1: no kill without a server order carrying `job_id`. The residual (server dead after S4 → two processes) is stated with its bound and its convergence path | `canary.go:203-206` (the line to remove), `server_ws.go:120-139`, `server_update.go:20-25` | `grep -c 'stopProcess(' internal/agent` == 0 reachable from the update path; server does not delete `pendingUpdates` before the ack; a restart between S4 and the order issues exactly one order |
| **F4** watchdog/swap race | Exclusive swap **lease** (`<canonical>.swap.lock`) held across the whole mutation window + monotonic **epoch**; the swap always wins; `N = 120 s`; the watchdog is a bounded, non-blocking contender that escalates instead of clobbering | `canary.go:149-175`, `canary.go:126-139`, `canary.go:97-120`, `journal.go:89-107` | fault-injection pause between rename 1 and 2 → exactly one winner, `sha(canonical)==expected`, no `.next`; second evaluation sees `epoch` advanced → `WATCHDOG_SKIPPED`; a confirmed job has zero restore lines |
| **F5** journal has no writer | `internal/upgrade/writer.go` (append+fsync), file `<canonical>.update.journal`, writers named per state, `internal/agent → internal/upgrade` becomes the package's first import edge, `ts=<unix>` | `journal.go:9-10, 67-85, 126, 149-159, 170-205`, `agent_update.go:47-51` | `FormatLine`→`ParseJournal` round-trip preserves name and time; a failing `Sync` produces `journal_ok:false` and no silent state advance; two writers produce N intact lines |
| **F6** 409 dialect | Two existing codes + one new name; exact status/body/`kind`; per-case client behavior; `LOCK_BUSY` and `UPDATE_IN_FLIGHT` deleted | `server_api.go:303, 309, 482-490`, `api_v1.go:186-204`, `idempotency.go:11-19` | each conflict returns the exact `code` + `detail.kind`; a replay returns the same `update_id`; the client performs no automatic retry on `STATE_UNKNOWN` |

---

### F1 — bootstrap kill authorization (CLOSED-IN-V3)

**The measured defect.** `handleProcKill` refuses in `sandboxed`/`standard` unless the PID is in `spawnedPIDs` (`agent_proc.go:44-52`). The only insertion site in the tree is `proc_start` for background processes (`agent_proc.go:104-106`). On the first hop the old client is 1.18.6: it has no canary flow, so the 1.19 binary it starts is a **plain agent** that never registered the parent PID. The server's order therefore hits the gate and every first-hop update ends in a refusal. v2's fix ("the canary inserts `CanaryOldPID` at canary startup") covers only the canary hop — and does not exist on hop 1.

**v3 mechanism — one object, two registrations.**

```go
// internal/agent — beside spawnedPIDs (agent.go:227-230)
type killAuthorization struct {
    JobID     string    // server job id, or "" for the argv-sourced canary grant
    Source    string    // "canary_argv" | "server_adopt"
    ExpiresAt time.Time // single use: deleted on first successful kill or on expiry
}
killAuth   map[int]killAuthorization // max 1 live entry; guarded by the same mutex
```

The sandbox gate becomes one clause wider and nothing else (`agent_proc.go:44-52`, mirrored at `agent.go:1125-1133` for the legacy `task_stop` path):

```go
allowed := a.spawnedPIDs[params.PID] || a.killAuthValid(params.PID, params.JobID)
```

`killAuthValid` returns true only if the entry exists, is unexpired, and — when the command carries `job_id` — the job ids match. `params.JobID` is a new optional field on `TaskStopParams` (`messages.go:598-601`); old agents ignore it, new agents require it for auth-sourced kills.

**Registration site R-a — canary hop (1.19+ only).** At startup, when `CanaryMode && CanaryOldPID > 0`: register `killAuth[CanaryOldPID]{Source:"canary_argv", ExpiresAt: now+600s}`. The fields exist (`agent.go:176-178`), the argv is built by the launcher (`canary_launch.go:24-31`) and parsed by `connect.go:54-55`, and the canary sequence is armed at `agent.go:453-455`. A process that is **not** in canary mode registers nothing — this is the property that keeps a normal agent from authorizing a kill of its own parent shell or of systemd.

**Registration site R-b — legacy hop (any version → 1.19).** A new server→agent message `update_adopt`, sent **only** to agents advertising capability `update.adopt` (capability gating already exists: `hasCapabilityStrict`, `server_api.go:471`; the agent's handler map is `agent.go:531-598`, entry at `:578` for updates). Payload:

```json
{"job_id":"upd_01HX…","old_pid":41288,"expected_sha256":"9f2c…","canonical_path":"/opt/probe/probe-client-linux","expires_in_s":300}
```

The agent accepts and registers `killAuth[old_pid]{JobID: job_id, Source:"server_adopt", ExpiresAt: now+expires_in_s}` **only if all four hold**, each checked on the agent's own filesystem, not on the server's word:

1. `canonical_path == strings.TrimSuffix(os.Executable(), ".next")` — the agent is the process at that path (the same normalisation the updater itself uses, `agent_update.go:47-51`);
2. `sha256(canonical_path) == expected_sha256` — the swap really happened, so this process is the promoted candidate and not a bystander;
3. `old_pid != os.Getpid()` and `old_pid` is not an ancestor further up than one hop (reject `old_pid <= 1`);
4. `old_pid` is not already covered by a live entry (idempotent repeat → return the same `job_id`, no second entry).

Any failure replies `update_adopt_result{accepted:false, reason:"<code>"}` and registers nothing → the job goes F4 `ADOPT_REFUSED`. Silence is not an option: the server has a 10 s adopt deadline and treats timeout as `ADOPT_TIMEOUT` (no kill).

**What registers a PID, and when — the answer in one line.** Either the launcher, on the successor's argv at process start (canary hop, `canary_launch.go:24-31`), or the server, at the moment it decides the superseded connection is gone and the canonical name is held by the expected version (legacy hop, in the same place that today blindly sleeps 3 s and kills — `server_ws.go:130-139`). Nothing else ever registers a PID.

**Why not `bypass`.** `server_api.go:795-813` states in the tree that `Bypass` "is intentionally never set by the server-side forwarder" precisely so an API caller cannot escalate. Using it for update kills would delete that property for every command class. v3 does not amend that comment; the authorization is narrower than bypass (one pid, one job, one use, expiring) and is refused by the same gate when absent.

**What is deliberately NOT authorized.** No wildcard, no "any PID whose executable looks like ours", no PID discovered by scanning `proc-list`. A kill of an unauthorized PID still returns `permission_denied`, now with `detail{reason:"no_kill_authorization", pid, job_id}` so the server can distinguish "the agent refused by policy" from "the kill failed at the OS".

**Tests a reviewer can demand.**
1. `permissions="standard"`, `killAuth[41288]` present, `proc_kill{pid:41288, job_id:J}` → `killed:true`; the same call without `killAuth` → `permission_denied` (**this is the F1 regression test, and it fails on v2**).
2. Expiry: `ExpiresAt` in the past → refused. Single use: second call → refused. Wrong pid: entry for 41288, command for 41100 → refused. Wrong job id → refused.
3. `update_adopt` with a mismatched `expected_sha256` → `accepted:false`, map empty, job → `ADOPT_REFUSED`.
4. A normal (non-canary) agent that receives `update_adopt` addressed to it still registers (it is the promoted candidate) — but an agent whose canonical sha does not match registers nothing.
5. A 1.18.6 agent never receives `update_adopt`: capability gate (`server_api.go:471`) is asserted by a test that builds the envelope list for a capability-less agent and sees no adopt.

**Residual (stated, not hidden).** Authorization does not lift the *permission tier*: a `read-only` agent still refuses every kill; the job then fails F4 with the tier named. And if the old process exited before the adopt, absence is success — no authorization is needed because nothing is killed (`§4.4 "already gone"` semantics, unchanged).

---

### F2 — check (c) identity (CLOSED-IN-V3)

**The measured defects.** (i) `server_ws.go:93` is `agentID := info.Name`; the protocol carries no PID (`messages.go:219-227`), so half of v2's stated identity source did not exist. (ii) `name == filepath.Base(canonical)` cannot match on POSIX for real names: the collector is `ps -eo pid,comm,%cpu,%mem --no-headers` (`agent_proc.go:161`) and Linux `comm` is the 15-byte task name (`TASK_COMM_LEN`), while `probe-client-linux` is 18 bytes and `hermes-remote-server-v20` is 23. Under v2's own rule that fires F4 `PID_IDENTITY_MISMATCH` and suppresses the kill on every Linux agent.

**v3 identity, three layers, with the decision moved off the name.**

**L0 — real on-wire PID (additive).** `protocol.AgentInfo` gains `PID int \`json:"pid,omitempty"\`` (`messages.go:219-227`). Populated where the info envelope is built, `agent.go:432-440`, with `os.Getpid()`. The server's decode is a plain `json.Unmarshal` (`server_ws.go:82-89`), so the field is additive in both directions and a 1.18.6 agent simply sends nothing (`omitempty` → 0). Recorded at connect beside the connection map (`server_ws.go:113-116`):

```go
s.connPID[agentID] = info.PID   // 0 = the agent did not report one (pre-1.19)
```

This is the *real* "superseded connection's PID recorded server-side at connect". The fabricated citation is deleted, and the job records `conn_pid` with an explicit null for legacy agents.

**L1 — the PID itself is inherited, never scanned.** `AgentUpdateResult.OldPID` (`messages.go:798-803`) is the old process's report of itself, persisted by the server at `server_update.go:133-158` (`pendingUpdate{OldPID}`, struct at `server_update.go:20-25`). This source is real today on every version — it is what the fallback in v2 wrongly bolted a second, non-existent source onto. v3 uses it alone on the legacy hop: `identity_source:"update_result"`.

**L2 — truncation-safe name pre-filter (POSIX) and full-name rule (Windows).**

```
commMatches(comm, base):
  comm == base                                   → true
  len(comm) == 15 && len(base) > 15 &&
      comm == base[:15]                          → true
  otherwise                                      → false
```

Examples that must be in the test table: `("probe-client-lin", "probe-client-linux") → true`; `("hermes-remote-s", "hermes-remote-server-v20") → true`; `("probe-client", "probe-client-linux") → false` (a 12-byte comm is not truncation); `("probe-client-liX", "probe-client-linux") → false`; `("probe.exe","probe.exe") → true`. Case-sensitivity: POSIX exact; Windows `strings.EqualFold`, because `tasklist /FO CSV /NH` (`agent_proc.go:129-157`) returns the image file name with its on-disk case and no 15-byte cap. Windows tolerates one documented truncation: tasklist's Image Name column has historically capped at 25 characters, so the Windows rule also accepts `len(name)==25 && len(base)>25 && HasPrefix(base, name)`. That cap is **UNVERIFIED-ON-HOST** (O6) — which is exactly why the name is not the decision.

**L3 — file identity, the actual decision (1.19+, additive, both OSes).** `ProcessInfo` today carries `pid,name,cpu_percent,memory_mb` only (`messages.go:587-592`). v3 makes the additive extension mandatory for 1.19 agents (it was "availability-gated" and effectively unused in v2):

| OS | collector | rule |
|---|---|---|
| Linux | `os.Readlink("/proc/<pid>/exe")` → path; start ticks from `/proc/<pid>/stat` field 22 × `/proc/stat btime` | the target's exe resolves to `<canonical>.old` **or** `os.SameFile(stat(/proc/<pid>/exe), stat(<canonical>.old))` — an inode comparison, therefore rename-proof; and `started_at < job.requested_at` |
| Windows | `QueryFullProcessImageNameW` + `GetProcessTimes` (`golang.org/x/sys v0.20.0` is already a dependency — `go.mod`) | image name matches (L2 rule) **and** creation time `< job.requested_at`. Windows reports the **load-time path**, which after `canonical → .old` is still `canonical` — so the Windows rule accepts `exe == canonical` for a predecessor whose start time precedes the job, and never expects `.old` |

**Check (c), restated.** `POST /agents/{id}/proc-list` (`api_v1.go:352-353`) →
- no row with `pid == old_pid` → **(c) true**. Absence is the goal; never treat absence as suspicious.
- row present, L3 proof says *this is my predecessor* → **(c) false**, state F4 `OLD_PID_ALIVE` (event `OLDPID_ALIVE`, `journal.go:38`), retry the kill once with the authorization, then escalate (`kill_requires_elevation`).
- row present, L2 name matches nothing and no L3 proof → **(c) not evaluable → F4 `PID_IDENTITY_MISMATCH`, do not kill.** A recycled PID is the failure this prevents, and the pending record's TTL is what bounds the window (v2 M13, unchanged: `pendingUpdate` has no TTL today — §5.3 adds one).

**Tests.** `commMatches` table above; `AgentInfo` round-trip with `pid` and a server-side assertion that `connPID[agentID]` is populated (and `0` for a legacy envelope); a proc-list fixture whose `comm` is truncated → (c) evaluates on L3 and passes; a fixture where the PID exists, is a *different* image, and L3 has no proof → `PID_IDENTITY_MISMATCH` with no kill issued; a Linux agent end-to-end where `<canonical>` was renamed to `.old` → `os.SameFile` proves it.

**Residual.** Name-only identity is weak and v3 says so instead of pretending: on a 1.18.6 agent that also lacks L3, the only discriminants are the inherited PID and the name pre-filter, and the job records `identity_source:"update_result"` so the reviewer of a real update knows exactly that.

---

### F3 — the 60 s fallback (CLOSED-IN-V3 by deletion)

**The measured defect.** v2 §4.2 row 3 let the new process kill the old 60 s after S4 "with no server order". That contradicts the owner's instruction and v2's own row 0, and its trigger carries no (a)/(b) precondition, so it can fire while the new process has not reclaimed the canonical name — zero agents serving that name.

**v3 deletes it.** There is no fallback row, no `kill_grace`, no `kill_authority:"fallback"` field. One invariant replaces the table:

> **I-1.** No component kills the superseded PID without a server-issued order carrying `job_id`. The only executor is the process holding a `kill_authorization` for exactly that PID (F1). The old process never terminates itself; the new process never initiates a kill.

Consequently the question "what stops it from firing on a live transfer" has the only honest answer: **nothing can fire what does not exist.** The single kill path is the server's job-scoped order.

**Two defects in the current server path are fixed while deleting the fallback** (both measured, both cited):
1. `server_ws.go:120-139` **deletes** `pendingUpdates[agentID]` at `:123` *before* the 3 s sleep and the kill. A server restart in that window loses the order permanently. v3: the record is deleted only on `TaskStopResult{killed:true}` or on a verified absence (proc-list), and the record carries `job_id`, `expires_at` (120 s, renewed on transition) and `attempts`.
2. The 3 s blind sleep is replaced by the adopt→order handshake of F1: order only after `update_adopt_result{accepted:true}` (or after `canary_connected` for a 1.19 source hop, where R-a already registered). No timer decides a kill.

**Residual, stated plainly (E15 revised).** If the server dies after S4 and never returns, the old and new processes both run. The registry is server-side, so nothing can converge — the old process cannot prove a successor holds the name without the server, and inventing a local proof is exactly the self-kill the owner forbade. On the server's return the persisted job reconciles and issues one order; the zombie filter (v2 §4.4, unchanged: refuse a connection whose reported PID equals `pending.OldPID`, now real because `AgentInfo.PID` exists — with the version discriminant as the 1.18.6 fallback) resolves the flap. Until then the cost is two processes on one name, visible in the status window (`§9.3 PID`) and in the job. That cost is accepted because the alternative can leave zero agents.

**Tests.** (i) A grep test asserting no `stopProcess(`/`proc.Kill()` call site is reachable from the update path (the `canary.go:203-206` block removed) — this test fails on v2. (ii) Server does not delete `pendingUpdates` before an ack: simulate a restart between S4 and the order and assert exactly one order is issued after resume. (iii) No order is issued before `PROMOTED` is durable (journal `PROMOTED` line present) and (a)+(b) true.

---

### F4 — watchdog / swap race (CLOSED-IN-V3)

**The measured defect.** The swap is two renames with no exclusion (`canary.go:149-175`: `canonical → .old` at `:163`, `.next → canonical` at `:167`). The watchdog's branch (ii) calls `restoreBackup` (`canary.go:126-139`) with no lock. With `N = 90 s` and zero slack, a canary connecting at t=39 s and promoting for 50 s puts the watchdog inside the window where `canonical` is missing; branch (ii) restores `.old → canonical`, then the swap's second rename lands on a target that POSIX silently clobbers and Windows refuses.

**v3 primitive — an exclusive swap lease plus a monotonic epoch.** Both live beside the canonical binary, both are files because the mutex is cross-process:

| object | `<canonical>.swap.lock` | `<canonical>.swap.epoch` |
|---|---|---|
| holder writes | acquires exclusively for the **entire** mutation window: archive → rename 1 → rename 2 → sha assert | appends one line per step |
| POSIX | `syscall.Flock(fd, LOCK_EX\|LOCK_NB)` | `O_APPEND` single write per line |
| Windows | `windows.LockFileEx` over the first byte (`golang.org/x/sys`, already in `go.mod`) | append under a 1-byte `LockFileEx` range |
| watchdog | **non-blocking** contender only; never waits, never queues | reads the epoch at t0 and again **after** acquiring |

Epoch line format (parsed by the same `ParseJournal` rules — `ts=<unix>`, uppercase event, `k=v`; `journal.go:9-10, 67-85, 89-107`):

```
ts=1763917331 SWAP_EPOCH epoch=7 holder=canary step=rename1 sha_before=1a77… sha_after=1a77… 
ts=1763917332 SWAP_EPOCH epoch=7 holder=canary step=done   sha_before=1a77… sha_after=9f2c…
```

**Who wins, stated as a rule.** The lease holder always wins, and the swap is always the holder. The watchdog's evaluation becomes:

1. read `epoch_at_t0`;
2. try to acquire the lease **non-blocking**; busy → `WATCHDOG_SKIPPED reason=lock_busy` and retry at most 3× at 5 s; still busy → branch (iii) escalate (never restore);
3. lease acquired → re-read `epoch`; if it advanced since t0 → release, `WATCHDOG_SKIPPED reason=epoch_changed`, escalate;
4. epoch unchanged → re-verify the branch condition **inside the lease** (`canonical` missing ∧ `sha(<canonical>.old) == sha(self)`) → only then `restoreBackup` with the sha asserted (`canary.go:126-139` semantics), journaling `WATCHDOG_RESTORED lock=held epoch=<n> restored_sha=<hex> match=true`.

Fail-safe direction, named: the watchdog prefers **keeping the new binary and escalating** over restoring. A restore is only ever performed while holding the lease, on an unchanged epoch, with the pre-condition re-checked. Branch (iii) — promoted, not confirmed — still never rolls back (v2 §3.2, unchanged).

**N and its single source of truth.** `watchdog_n = 120 s` = `canary_connect_deadline` 40 + `promote_deadline` 60 + **`margin_n` 20**. The margin's only job is to make contention rare; the lease is what makes contention safe. v2's "no slack because slack hides a hang" is kept in spirit and bounded: a hang is still reported at 120 s, and the margin is not silent — it is a named constant with a journal line (`wait_s=120`). One knob: `PROBE_UPDATE_WATCHDOG_S` (default 120). `confirm_timeout_s` is derived server-side and validated `>= watchdog_n` at request time rather than stored as a second number (C10).

**The observable that proves the ordering ran.**
- journal: `WATCHDOG_RESTORED lock=held epoch=<n>` / `WATCHDOG_SKIPPED reason=<lock_busy|epoch_changed> epoch=<n>` / `SWAP_EPOCH epoch=<n> step=<start|rename1|rename2|done>`;
- invariant **I-2**: exactly one `step=done` per successful update; a `confirmed` job has **zero** `WATCHDOG_RESTORED` lines and zero `step=start` without `step=done`;
- status record (`§9.2`, `status.json`): `swap{locked,epoch,holder,step}` — the same fact visible from the Windows status window without reading the journal.

**Tests.** (a) Fault-injection hook that pauses the swapper between rename 1 and rename 2 while the watchdog fires → assert **exactly one** of {swap completes, watchdog restores}, `sha(canonical) == expected` in the swap case, no `.next`, `.old` never clobbered, and the loser journaled its skip. (b) Watchdog fires with the lease free and the epoch unchanged → restore happens exactly once, sha asserted. (c) Two watchdog evaluations → the second sees the epoch advanced and skips. (d) A grep-level invariant test on a recorded journal: one `step=done`, zero restores for a confirmed job.

---

### F5 — journal has no writer (CLOSED-IN-V3)

**The measured defect.** v2 defined outcome rows and `event=…` evidence for every state, and no component writes any of it: `internal/upgrade` has `FormatLine`/`ParseJournal`/`Analyze` (`journal.go:67-85, 110-147, 170-205`), `FormatLine` is called only from `journal_test.go`, no `.update.journal` path string exists, and the package has **zero importers** in the tree.

**Writer, file, durability.**

| item | v3 |
|---|---|
| package | `internal/upgrade` gains `writer.go` — `Writer{Open(path) / Append(name string, kv ...string) error / Close()}`, built on `FormatLine` (`journal.go:67-85`) so the format cannot drift |
| file | `<canonical>.update.journal`, where `canonical = strings.TrimSuffix(os.Executable(), ".next")` — the same normalisation the updater already performs (`agent_update.go:47-51`) |
| line | `ts=<unix-seconds int> EVENT k=v …`, uppercase event matching `isJournalEventName` (`journal.go:89-107`: `A-Z`, `0-9`, `_` only) |
| import edge | `internal/agent` **imports** `internal/upgrade`. Verified no cycle: `grep -rn 'internal/agent' internal/upgrade` = 0 hits; module path `github.com/falke-ai-circuit/probe` (`go.mod`). This is the package's first importer and it also makes §7.3's "existing rollback kit" reachable rather than merely present |
| durability | one complete line per `Append`, then **`File.Sync()` before the state is reported to the server**. A state whose journal line is not durable is not reported as reached — the v2 doctrine "a state the journal cannot prove is not a state" gets its mechanism here |
| concurrency | POSIX: `O_APPEND`, one write per line (atomic per write on a regular file). Windows: one-write-per-line under a 1-byte `LockFileEx` range, because Go's `O_APPEND` is a seek-then-write emulation. Every line carries `job=<id> seq=<n>` so a theoretical interleave is detectable by `Analyze` |
| rotation | at job open (S1), under the swap lease (F4), if size > 1 MiB → rename to `.update.journal.1`; **never truncated mid-job** |

**Who writes which state** (this is the row v2 lacked):

| state / event | writer |
|---|---|
| `JOB_OPENED`, `ARTIFACT_VERIFIED`, `CHANNEL_FALLBACK`, `WATCHDOG_TIMEOUT`, `WATCHDOG_RESTORED`, `WATCHDOG_SKIPPED`, `ABANDONED` | old process (`handleAgentUpdate`, `agent_update.go:30-151`; watchdog goroutine armed at the `StartCanary` call, `:131-136`) |
| `ADOPTED`, `CANARY_CONNECTED`, `SWAP_EPOCH`, `PROMOTED`, `KILL_ORDER`, `KILL_ACK`, `OLD_KILLED` | new process — canary before promotion, canonical after (`canary.go:33-75, 186-209`); `KILL_ACK` on the `proc_kill` reply (`agent_proc.go:54-75`) |
| job row / `checks.*` | **server** — the job store, not the journal. The server writes no journal line, by design: it cannot see the filesystem it would be swearing about |

**Failure path (designed, not implied).** `Open`/`Append`/`Sync` error → the transition is reported with `journal_ok:false` and, pre-promotion, the job fails `JOURNAL_UNAVAILABLE` (the update does not proceed on an unprovable state). Post-promotion, a journal fault is a warning, because promotion is not undone by a logging fault — consistent with branch (iii)'s "a monitoring gap must not cause a rollback". Disk-full and read-only-directory are the two named causes.

**Tests.** (i) `FormatLine` → `ParseJournal` round-trip preserves the event name and `ts` as a Unix integer (**this is the C7 regression test; it fails on v2's RFC3339 example**). (ii) A writer stub whose `Sync` fails → server sees `journal_ok:false`, no state advance, no promotion. (iii) Two writers (old + canary) appending 50 lines each → 100 intact parseable lines, `job`/`seq` fields monotonic per writer. (iv) Rotation at 1 MiB preserves the previous file as `.1`. (v) `Analyze` (`journal.go:170-205`) returns `OutcomeOldPIDAlive` on an `OLDPID_ALIVE` line — the outcome constant and the event name are different objects (C11) and the test pins both.

---

### F6 — the 409 dialect (CLOSED-IN-V3)

**The measured defect.** v2 introduced `LOCK_BUSY`, `UPDATE_IN_FLIGHT`, `UPDATE_PREVIOUSLY_FAILED` — three names with 0 hits in the tree, two of which are the same condition as the existing `IDEMPOTENCY_IN_FLIGHT`, and never specified the body.

**v3 vocabulary — two existing codes, one new name.**

| HTTP | code | when | `detail.kind` |
|---|---|---|---|
| 409 | **`IDEMPOTENCY_IN_FLIGHT`** (exists, `server_api.go:303`) | a live lease, a different artifact mid-flight, or a duplicate request for the same agent | `lease` \| `different_artifact` \| `duplicate_request` |
| 409 | **`IDEMPOTENCY_STATE_UNKNOWN`** (exists, `server_api.go:309`) | the job row exists but its state cannot be re-derived (server restarted mid-transition, journal unreadable) | `unreadable_journal` \| `restart_mid_transition` |
| 409 | **`UPDATE_PREVIOUSLY_FAILED`** (new — the only new name) | the previous attempt for this agent+artifact is terminal `failed`/`rolled_back` and the caller did not send `new_attempt:true` | — |
| 409 | `CAPABILITY_MISSING`, `SERVER_BUILD_TOO_OLD`, `INTEGRITY_MISMATCH` | unchanged from v2 §7.4 | — |
| 413 / 503 / 504 / 400 / 401 / 403 / 404 | unchanged from v2 §7.4 | | |

`LOCK_BUSY` and `UPDATE_IN_FLIGHT` are **deleted**. Justification for the one survivor: nothing is in flight and nothing was replayed — it is a policy refusal that becomes legal with `new_attempt:true`, and no existing code expresses it (`idempotency.go:11-19` has exactly three states: `IN_FLIGHT`, `DONE`, `UNKNOWN`).

**Exact body — the tree's own envelope, not a new one.** The error writer is flat (`writeTypedErrorDetail`, `server_api.go:480-490`):

```json
{
  "code": "IDEMPOTENCY_IN_FLIGHT",
  "message": "update already in flight for agent V6049A-EAS1",
  "detail": {
    "kind": "different_artifact",
    "retryable": true,
    "retry_after_ms": 1000,
    "agent_id": "V6049A-EAS1",
    "conflict": {
      "update_id": "upd_01HX…",
      "sha256": "9f2c…",
      "state": "promoted",
      "holder": "op_goran",
      "expires_at": "2026-11-23T13:10:00Z",
      "status_url": "/api/v1/agents/V6049A-EAS1/update/upd_01HX…"
    }
  }
}
```

Through the v1 surface `reformatV1` lifts the typed body onto the v1 envelope unchanged (`api_v1.go:186-204` → `writeErrorDetail`), so a v1 caller sees the same `code`/`message`/`detail`; no second dialect for legacy callers. Lease-only case:

```json
{"code":"IDEMPOTENCY_IN_FLIGHT","message":"update lease for agent V6049A-EAS1 is held by another operator",
 "detail":{"kind":"lease","retryable":true,"retry_after_ms":1000,
           "conflict":{"update_id":"upd_01HX…","holder":"op_selin","expires_at":"2026-11-23T13:10:00Z"}}}
```

Retry-after-failure case:

```json
{"code":"UPDATE_PREVIOUSLY_FAILED","message":"previous update attempt ended in failed",
 "detail":{"retryable":true,"retry_after_ms":0,"new_attempt_required":true,
           "conflict":{"update_id":"upd_01HOld…","state":"failed","failure":{"code":"CANARY_NEVER_CONNECTED"}}}}
```

State-unknown case:

```json
{"code":"IDEMPOTENCY_STATE_UNKNOWN","message":"the job's state cannot be re-derived; inspect the node before retrying",
 "detail":{"kind":"unreadable_journal","retryable":false,"conflict":{"update_id":"upd_01HX…","status_url":"…"}}}
```

**What the client does with each** (normative for §8's Update tab and the dialog on `Agents.tsx`):

| response | client behavior |
|---|---|
| `kind:"lease"` | do not retry; disable Start; show holder + `expires_at`; poll `status_url` at 1 s; offer `force+replace_lease` only behind an explicit confirm dialog, audited |
| `kind:"different_artifact"` | do not retry; show the in-flight job (sha + state) and disable Start for a different build; polling continues |
| `kind:"duplicate_request"` | treat as a replay: open the existing job (`update_id`), never start a second one |
| `UPDATE_PREVIOUSLY_FAILED` | show the previous failure code; a Retry button re-posts with `new_attempt:true` (one click, explicit) |
| `IDEMPOTENCY_STATE_UNKNOWN` | no automatic retry, ever; show "state unknown — inspect"; the only actions are poll and operator rollback. This mirrors the tree's own doctrine for a lost result (`idempotency.go:13-16`) |

**Tests.** Each case asserts status 409 + exact `code` + exact `detail.kind`; a duplicate of a non-terminal job returns `202` with the same `update_id` and `replayed:true` (v2 §5.3 unchanged); a retry without `new_attempt` returns `UPDATE_PREVIOUSLY_FAILED` and with it returns `202` + a new `update_id`; a client unit test asserts no request is issued after `STATE_UNKNOWN`.

---

## 4. R1-R9 reconciliation (A3)

| R | P9-07 grade | v3 disposition | carried by |
|---|---|---|---|
| **R1** four confirmation checks | FAIL — (c) identity | **addressed.** (a) unchanged (PASS); (b) tautology removed, replaced by `conn_pid` + `version_registered` + `canary_row_remaining` + `last_seen` (C8); (c) rebuilt on L0/L1/L2/L3 (§F2); (d) unchanged, and X3's "defect" is retracted (C9) | §F2, §F3 |
| **R2** X1-X4 | FAIL (2 demonstrated, 2 asserted) | **addressed.** X1: fabricated source deleted, real one added (§F2 L0). X2: authorization rebuilt as `killAuth` with two named registrations, not a `spawnedPIDs` insertion at an unrelated site (§F1). X3: **retracted as a non-defect** — `exec` already wraps in `cmd` (`platform_windows.go:241-253`, `shellargv.go:28-34`), so v3 specifies a bare `echo probe-update-ok` on both platforms and stops calling it a fix. X4: authority/execution split kept, and the fallback that contradicted it is deleted (§F3) | §F1, §F2, §F3 |
| **R3** kill-old semantics | FAIL — the fallback | **addressed by deletion** + invariant I-1 + the two server-path defects (record deleted before the kill; 3 s blind sleep) fixed | §F3 |
| **R4** watchdog N=90 | FAIL — unjustified N, zero slack, race, knob drift | **addressed.** N=120 with a named 20 s margin; lease+epoch ordering; one knob (C10) | §F4 |
| **R5** channel decision | PASS (one reword) | **no design change.** The row's "made the server fall over" is reworded as a superseded premise: the 10.7 MB collapse was the write/write race fixed at `server_ws.go:16-34`, and the decision rests on the 1.00× vs 1.33× byte counts, control-channel isolation and the 30 s forward deadline. Cosmetic `chunks`/`chunk_bytes` conflation in the HTTP 202 body noted and left as v2 (documentation-only) | v2 §6, reworded here |
| **R6** old-client bootstrap | FAIL — 3 of 6 steps break | **addressed.** S2 evidence: the journal now has a writer (§F5) **and** the server-observable alternative is kept (`artifact_verified` reply / `fs-hash`), so S2 survives even if a legacy process cannot journal. S5: authorization exists on the legacy hop (§F1 R-b) — the guaranteed F4 is removed. Watchdog on hop 1: **the server owns it**, because 1.18.6 has no local watchdog — on job TTL expiry before `promoted`, the server quarantines the staged file with **existing** agent commands (`fs-delete` → `TypeFileRemove`, route `api_v1.go:350-351`), no new capability. Hop 1 is still the only un-proven hop and the job carries `legacy_flow:true` | §F1, §F5, v2 §10 |
| **R7** idempotency vocabulary | FAIL — second dialect | **addressed.** Two existing codes reused, one new name justified, exact bodies in the tree's envelope (§F6) | §F6 |
| **R8** UI verdict | PASS | **no change.** Additive verdict stands; §8.1 gains the F6 client behaviors and `swap{}` from the status record | v2 §8 + §F6 |
| **R9** Windows console vs service | PASS | **no change.** Status record gains `swap{locked,epoch,holder}` and `journal_tail` (§9.3 rows), both additive to the same file | v2 §9 + §F4 |

**Explicit rebuttals.** One: P9-07's R2/X3 assertion that v1's `exec "echo …"` was broken. Rebutted with its own evidence — `windowsPlatform.Exec` runs every command through `cmd` (`platform_windows.go:241-253`) and `shellArgv` returns `/c <command>` (`shellargv.go:28-34`); the marker would have printed. The change is harmless and permitted, the *defect claim* is withdrawn. No other review point is rebutted; the remaining five are closed as above.

---

## 5. Additional corrections v3 makes on its own evidence (not review findings)

| # | defect in v2 | evidence | v3 |
|---|---|---|---|
| **C7** | journal example `ts=2026-11-23T13:02:11Z` | the parser is `strconv.ParseInt(..., 10, 64)` (`journal.go:126`); `FormatLine` emits `now.Unix()` (`:67-71`) | `ts=<unix int>`; a round-trip test pins it |
| **C8** | check (b) `name == id` | `server_ws.go:93` + `:107` make it true by construction | dropped; (b) rests on `conn_pid`, version, canary-row absence, `last_seen` |
| **C9** | X3 declared a Windows defect | `platform_windows.go:241-253`, `shellargv.go:28-34` | retracted |
| **C11** | `journal.go:158` cited as the event for "old PID alive" | `:158` is `OutcomeOldPIDAlive = "old_pid_alive"`; the event string is `OLDPID_ALIVE` at `:38` | both named; job carries the event, `Analyze` returns the outcome |
| **C12** | sample `identity_exe` on a 1.18.6 job | `ProcessInfo` has no exe today (`messages.go:587-592`) | sample shows `identity_source` + `conn_pid` |
| **C13** | `notified_at` never read; pending record immortal (`server_update.go:154`, `:20-25`) | read nowhere in the tree | TTL 120 s + attempts + `job_id` on `pendingUpdate`; deleted only on ack (`§F3`) |
| **C14** | M16/M13 citations (review's own note) | route is `api_v1.go:422`; struct is `server_update.go:20-25` | corrected |

---

## 6. What v3 does **not** change (so only the delta needs grading)

Inherited verbatim from v2, with the section pointers: §0.1 measured facts M1-M22 (two citations corrected, C14); §2.1 checks (a) and (d), §2.2 sha→version bridge, §2.3 the rejected slogans, §2.4 failure semantics (except the (c) row, now `§F2`); §5 idempotency + lease + behaviour matrix (lease refusal now expressed as `IDEMPOTENCY_IN_FLIGHT kind:"lease"`, §F6); §6 channel decision, arithmetic, 256 KiB/64 MiB, pre-flight; §7.1-§7.3 request/status/rollback shapes; §8 UI contract; §9 Windows console; §10 backward compatibility (with the R6 additions); §11 failure matrix (E15 replaced, E14 rewritten as "authorization absent", E5 gains the server-side hop-1 cleanup); §12 decision log (D-03 → 120 s + lease; D-04 → fallback removed; D-09 unchanged); §13 projections; §14 open items.

---

## 7. Acceptance map (A1-A5)

| item | where | verdict |
|---|---|---|
| **A1** per-finding table with finding / v3 answer / source region re-opened / testable assertion, every `file:line` resolving | §3 table + §3/F1-F6 | **MET** — 46 citations re-opened in this session at `0352f3b`; source identity proven by `git diff --stat 0352f3b 2a2ed08` (3 files, +226, `.workflow/architect/` only) |
| **A2** explicit v3 + changelog vs v2 | §2 (C1-C12) + §5 (C7-C14) | **MET** |
| **A3** R1-R9 addressed or rebutted | §4 (8 addressed/carried, R5+R8+R9 no-change, 1 rebuttal with its `file:line`) | **MET** |
| **A4** no code; only this document added under `.workflow/` | §9 evidence: `git status` clean, single-file commit | **MET** |
| **A5** self-check line at the top, one token per finding | line 3 | **MET** |

---

## 8. Open items (carried, plus new — none blocks a reviewer)

| # | item | owner | blocks |
|---|---|---|---|
| O1 | OD-6 field re-measurement on the real EAS1 host | measurement unit | nothing in this contract |
| O2 | Windows runtime leg (service install → restart → logoff) UNVERIFIED-ON-HOST | whoever has a Windows host | §9 acceptance, E9 real behaviour |
| O3 | kill under mixed principals untested | coder + Windows host | E9 |
| O4 | HTTP reachability pre-flight remains attempt-based | coder | D-01's cost line |
| O5 | fleet-level rollout coordinator (6-month projection) | not this unit | — |
| **O6** | `tasklist` Image Name truncation at 25 chars is **UNVERIFIED-ON-HOST** (F2 L2 Windows rule); mitigated because the decision rests on creation time (L3) | reviewer with a Windows host | nothing on Linux; weakens the Windows name pre-filter only |
| **O7** | the 1.18.6 **source** is not in this tree, so the legacy hop is derived from wire types and code paths (same limit P9-07 recorded). A 1.18.6 binary does exist outside the tree — `/opt/data/tmp/v119/bin/probe-client-1186`, `--version` → `PROBE Client v1.18.6`, 11,059,052 B — so the hop **can** be executed; **this unit did not execute it** (design unit, no live server leg). The `update_adopt` path is specified, not run | coder unit after ratification | field proof of R6 |
| **O8** | `read-only` permission tier cannot be updated by this contract at all (the kill and the file writes are refused by tier) — the job must refuse up front with `CAPABILITY_MISSING` rather than fail at F4 | coder | nothing; named so it is not discovered in the field |

---

## 9. Self-verification of this unit

| check | result |
|---|---|
| artefact written | `/opt/data/tmp/probe119/P9-08_CONTRACT_V3.md` |
| source tree untouched | `git -C /opt/data/projects/PROBE_UPGRADE/v119wt status --porcelain` → empty; the closure commit adds `.workflow/architect/P9-08_CONTRACT_V3.md` only |
| suite at `0352f3b` for the four packages | `PATH=/tmp/go/bin GOFLAGS=-mod=vendor go test ./internal/agent/ ./internal/upgrade/ ./internal/server/ ./internal/protocol/ -count=1 -v` → **449 `--- PASS`, 0 `--- FAIL`, 4 packages `ok`**, exit 0. Log `/opt/data/tmp/probe119/p908_suite.log`. Matches the parent's 449/0 at the same tree |
| no code changed | `git show --stat <closure>` = 1 file |
| ratification | **NOT declared.** Goran's decision; P9-03..P9-06 remain blocked |

---

## 10. Close record

| field | value |
|---|---|
| artefact | `/opt/data/tmp/probe119/P9-08_CONTRACT_V3.md` (canonical copy `.workflow/architect/P9-08_CONTRACT_V3.md` in `v119wt`) |
| version | **v3** — six blocking findings closed (F1-F6); changelog §2 + §5 |
| reviewed_by | **pending** (independent reviewer leg on v3) |
| verdict | **REMEDIATION COMPLETE / NOT RATIFIED** — a review verdict on v3 is owed; ratification is Goran's |
| verdict_sha_of_head | `0352f3b71d801902ea32703299aebf18bcfe069f` — the tree this document grades |
| code changed | **none** |
| open item | O7: the legacy hop's `update_adopt` path is specified from wire types, not executed against a live 1.18.6 binary |

*End of P9-08. No source file was modified.*
