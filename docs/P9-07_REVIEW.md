VERDICT FIX-FIRST /opt/data/tmp/probe119/P9-01_UPDATE_CONTRACT.md 792c113c1246 (unit head 0352f3b) · suite 449 PASS / 0 FAIL across 4 packages

# P9-07 — independent review of the P9-01 update contract

| field | value |
|---|---|
| reviewer | reviewer (independent, owner=reviewer=independent) |
| artefact | `/opt/data/tmp/probe119/P9-01_UPDATE_CONTRACT.md` — 62,841 B, sha256 `792c113c12463d563ca60b0c52552799d853c55310f0229bb34c81f57d373174` |
| unit head | `0352f3b` (branch `r119-build`) |
| tree graded | detached worktree `/opt/data/tmp/probe119/wt-p907` at `0352f3b` — identical source to `3ad56e5`; `git diff --stat 3ad56e5 0352f3b` = 1 file, 673 insertions, documentation only |
| suite run by me | `go test ./internal/agent/ ./internal/upgrade/ ./internal/server/ ./internal/protocol/ -count=1 -v` → **449 --- PASS, 0 --- FAIL**, 4 packages ok (log `/opt/data/tmp/probe119/p907_suite.log`) |
| scratch paths | `/opt/data/tmp/probe119/wt-p907` (worktree, unmodified product code) · `/opt/data/tmp/probe119/wt-p907/web/dist/index.html` (untracked placeholder so `embed.go` compiles; build-only, not product code) · suite log as above |
| shape | `SHAPE none` — the contract adds no code; P9-01 is documentation-only (`git diff --name-status 3ad56e5 0352f3b` = `A .workflow/architect/P9-01_UPDATE_CONTRACT.md`) |

## Method

1. Framed the request: not "is the design good", but "is it testable, internally consistent, and true to the tree" (brief §DO NOT).
2. Read the contract end to end; extracted every `file:line` citation.
3. Opened each cited region in a detached worktree at the unit head; re-derived each claim from source, not from the contract's summary.
4. Re-ran the four affected Go packages and kept my own counts.
5. Hostile pass: three attacks per R-item where relevant (stale citation / arithmetic / bootstrap / race).

RECIPIENT EXPERIENCE: I did not read the contract's citations and accept them — I opened each cited range, ran the suites, and re-derived the byte/chunk arithmetic and the watchdog budget from source. What I could not do: execute a Windows host, so all Windows-only behaviour is graded on code and on P8-10's own UNVERIFIED-ON-HOST marking, never passed by adjacency.

---

## R1 — The four confirmation checks · **FAIL**

Three of four checks name an exact, re-runnable API call and a defined failure state. Check (c) does not survive the tree.

**(a) version from the artifact — PASS (as a check).** Exact calls named: `POST /agents/{id}/fs-hash {path:<canonical>}` then `exec "<canonical> --version"` (contract §2.1 row a, lines 145). Failure semantics at §2.4 line 167 (`F1 ARTIFACT_MISMATCH` pre-promotion, `F4` post). Evidence fields enumerated in the job (`sha_agent`, `sha_expected`, `version_observed`). The executable leg is real: `cmd/probe/main.go:74` handles `--version` and prints `PROBE <version>` (`main.go:75`), and `cmd/probe-client/main.go:180-185` keeps the same flag specifically as an updater API. Not a "the log says so" check.

**(b) same agent name — PASS (as a check), one tautology.** `GET /api/v1/agents/{id}` exists at `internal/server/api_v1.go:310`. But `name == id` is true by construction: `server_ws.go:93` sets `agentID := info.Name` and `registry.Register(agentID, info.Name, ...)` (`server_ws.go:107`). So the discriminating part of (b) is only `version == expected` plus `last_seen`. That is still evidence, but the check is narrower than it reads.

**(d) functional probe — PASS (as a check), and the X3 "correction" is a non-defect.** The marker call is exact and its FAIL state is defined (§2.4 line 170, `F4 PROBE_FAILED`, one retry at 30 s). See R2/X3: the Windows command was never broken because `exec` already wraps in `cmd /c` (`internal/platform/platform_windows.go:241-247`, `internal/platform/shellargv.go:30-36`).

**(c) old PID absent — FAIL.** Two independent defects:

1. **The named identity source does not exist.** §4.3 L1 (line 242) says identity is "`AgentUpdateResult.OldPID` … and the superseded connection's PID recorded server-side at connect (`internal/server/server_ws.go:93`)". `server_ws.go:93` is `agentID := info.Name`. `protocol.AgentInfo` (`internal/protocol/messages.go:219-227`) carries name, version, os, arch, mode, protocol_version, capabilities — **no PID**. `cmd/probe/connect.go` reports no PID (only the `--canary-old-pid` flag, `connect.go:55,158`). One of the two declared identity sources is fabricated.
2. **The only cross-check on today's wire is a name, and on POSIX that name is truncated.** §4.3 L2 (line 243) and §2.1(c) (line 147) fall back to `name == filepath.Base(canonical)`. The POSIX collector is `ps -eo pid,comm,...` (`internal/agent/agent_proc.go:161`), and Linux `comm` is capped at 15 characters. For the real binary names in this repo (`probe-client-linux`, 18 chars; `hermes-remote-server-v20`, 23 chars) the comparison can never match → the contract's own rule fires `F4 PID_IDENTITY_MISMATCH`, the kill is suppressed, and the old process survives on every Linux agent. That is the exact failure mode the brief asked me to test for: a kill decision resting on a name.

Windows `tasklist /FO CSV` is unaffected (`agent_proc.go:129-157` returns the full image name), so this is a POSIX-only, but total, break.

The check does name an exact API (`POST /agents/{id}/proc-list`, route at `api_v1.go:352-353`) and a FAIL state (`F4 OLD_PID_ALIVE`, `journal.go:158` `OutcomeOldPIDAlive` verified). The problem is that the evidence it produces is wrong on Linux and one of its two stated identity sources is missing.

---

## R2 — X1-X4 corrections · **FAIL** (two demonstrated, two asserted)

| # | verdict | evidence |
|---|---|---|
| **X1** | **HALF — the defect is real, the fix cites a source that does not exist** | `ProcessInfo` is `{pid,name,cpu_percent,memory_mb}` (`messages.go:587-592`) ✓ and `windowsProcessList` parses name+PID only (`agent_proc.go:129-157`) ✓. But the replacement identity source "superseded connection's PID … `server_ws.go:93`" is false (see R1(c)). The `ProcessInfo.Exe/StartedAt` additive extension is correctly labelled availability-gated. |
| **X2** | **DEMONSTRATED as a design instruction, mis-cited as existing code** | The sandbox refusal is real: `agent_proc.go:44-52` returns `permission_denied` unless `a.spawnedPIDs[params.PID]` (`:46`). The map and mutex are declared at `agent.go:227-229`; the only insertion site in the tree is `agent_proc.go:105` (proc-start). No canary registration exists — so the contract's "mandatory registration step" is new work, correctly flagged as mandatory, but its citation "`agent.go:227-229`, `:105` pattern" points at a declaration and at an unrelated insert. Consequence in R6: the legacy hop has no canary at all, so the fix as scoped cannot cover it. |
| **X3** | **ASSERTED — corrects a non-defect** | The claim is "there is no `echo` executable on Windows; `exec` spawns the string as a command". The second half is wrong: `windowsPlatform.Exec` runs every command through `cmd` (`platform_windows.go:241-250`, `shellArgv` returns `/c <command>`), so v1's `exec "echo probe-update-ok"` would have printed the marker. The corrected form `cmd /c echo probe-update-ok` works too (nested `cmd /c`), so the change is harmless — but the defect it claims to fix is not measurable in this tree. |
| **X4** | **DEMONSTRATED (citation exact)** | `canary.go:203-206` is `if a.cfg.CanaryOldPID > 0 { stopProcess(a.cfg.CanaryOldPID) }` — the self-kill line, exactly as cited. The authority/execution split is consistent with the owner's quoted words. Note the split is contradicted by the 60 s fallback (R3). |

---

## R3 — Kill-old semantics vs the owner's ask · **FAIL**

Ordering is right on the primary path; the fallback breaks the rule it was written to enforce.

- Ordering, primary: swap is durable (`PROMOTED`) and checks (a)+(b) true, **then** the server issues `proc_kill{PID: old_pid, Signal: 9}` to the new connection, **then** the new process executes `taskkill`/`kill -9` (§4.1 line 221, §4.2 rows 1-2). That is "swap in, then kill from new" with the server as authority — matches the owner's two sentences and the X4 quote.
- Authority placement: server issues, new process executes, old process never terminates itself (§4.2 row 0, `canary.go:203-206` gated). Internally consistent **except**:
- **The 60 s fallback is a self-initiated kill.** §4.2 row 3 (line 230) lets the new process kill the old after 60 s "with no server order". §4.1 states "No kill of any kind before `PROMOTED` is durable **and checks (a)+(b) pass**". Row 3's trigger is "60 s after S4", with no (a)/(b) precondition. So the fallback can terminate the old process while the new one has not yet reclaimed the canonical name — leaving zero agents serving that name until a reconnect. It also directly contradicts row 0's "self-kill at any time is a protocol violation" and the owner's "not self-kill before it". This is the one place where the contract's own rule and its own table disagree.
- Failed kill outcome: **defined, not silent** — §4.4 (lines 252-256): access denied → `F4 OLD_PID_ALIVE` + `kill_requires_elevation`, locked exe → kill proceeds (lock blocks delete, not `TerminateProcess`; consistent with `swapRename` semantics at `canary.go:160-165`), already gone → success with `already_exited=true`, zombie reconnect → server accept filter. No silence path found.

---

## R4 — Watchdog N=90 s · **FAIL**

- **Owner and clock are right.** §3.2 puts the watchdog in the OLD process, at `t=90 s from dispatch`, and it acts locally: branch (i) quarantines, (ii) restores, (iii) escalates (§3.2 lines 195-197). It does **not** depend on the server — the failing party is not the watchdog's dependency. That half passes.
- **N is not measured.** §3.1 line 183: `canary_connect_deadline = 40 s` = "4× the measured handshake" — no handshake measurement is cited anywhere in the contract or in P8-02. Line 184: `promote_deadline = 50 s` = "10 s prove + 2 renames + sha over a 13.5 MB file on a contended disk" — an estimate, no run. Line 185 then hard-sums 40+50 with "no slack". So the number is internally consistent but empirically unjustified; the brief asked whether it is justified by anything measured, and it is not.
- **The zero-slack sum creates a swap/watchdog race.** If the canary connects at t=39 s and promotion takes its full 50 s, the watchdog evaluates at t=90 s in the middle of the two-rename window — after `canonical → .old` and before `.next → canonical`. At that instant `canonical` is missing, so branch (ii) fires and the old process renames `.old → canonical` (`canary.go:126-139` semantics). The canary then attempts `.next → canonical` and the rename fails (Windows refuses rename onto an existing target; POSIX silently clobbers the restored file). There is no lock or lease between the watchdog and `swapBinaryPaths`. The contract's "no slack, because slack hides a hang" is exactly what makes this collision possible by construction.
- **Knob mismatch:** D-03 names `PROBE_UPDATE_WATCHDOG_S`, §3.1 does not, and §7.1 pins `confirm_timeout_s` "must equal watchdog_n or be larger" — three places define the same number without a single source of truth.

---

## R5 — Channel decision · **PASS** (premise is superseded but caveated; arithmetic checks)

- **Premise:** P8-06 §7 measured the collapse as a write/write race — the unlocked pong reply in `server_ws.go` (`P8-06_TRANSFER.md:131`, fixed by `writeToAgentLocked`, `server_ws.go:16-30` in this tree). The contract does not hide this: §6.2 line 329 carries "(with the race, now fixed, P8-06 §7)". The decision therefore does **not** rest on the dead premise; it rests on 1.00× vs 1.33× bytes, on not sharing the control channel with bulk data, and on independence from the 30 s forward deadline (`server_update.go:111` verified). Those three survive the repair. One residual: the same table row still asserts the inflation "made the server fall over", which reads as a live cause to a coder who skips the parenthesis. Non-blocking, worth a reword.
- **Arithmetic:** 262,144 B × 256 = 67,108,864 B = 64 MiB exactly ✓. 13,556,224 / 262,144 = 51.71 → 52 chunks ✓. 67,108,864 / 5.8 MB/s ≈ 11.6 s ✓, and 5.8 MB/s is the measured rate (A1: 10,700,000 B in 1836 ms = 5.83 MB/s, `P8-06_TRANSFER.md:47`). Resume is consistent: `Range` → 206 + `Content-Range` (P8-06 §4) with `next_offset` continuing at the last acknowledged byte (§5).
- Minor conflation: `chunks`/`chunk_bytes` are WS-fallback parameters but are returned in the HTTP 202 body as if the HTTP leg were chunked. Cosmetic.

---

## R6 — Old-client bootstrap · **FAIL**

Walking §1.1 with a 1.18.5/1.18.6 process as the old actor:

| state | what the old client must newly understand | break? |
|---|---|---|
| S1 `requested` | nothing — `AgentUpdate` and `download_url/filename/ws_path/sha256/version` already exist (`messages.go:787-793`) | no |
| S2 `artifact_verified` | must emit `event=ARTIFACT_VERIFIED` **or** return an `artifact_verified` reply | **YES.** Neither exists. The update reply is `AgentUpdateResult{success,old_pid,new_pid,message}` (`messages.go:798-803`), and there is no journal writer in the agent at all: `internal/upgrade/journal.go` provides `FormatLine`/`ParseJournal`/`Analyze` only, `FormatLine` is called from tests alone (`journal_test.go:11`), no `.update.journal` path string exists anywhere, and `internal/upgrade` has **zero importers** in the entire tree (`grep -rl 'internal/upgrade' --include='*.go'` outside the package = 0). S2 has no evidence path on any version, not just 1.18.6. |
| S3 `canary_connected` | §1.2 line 120 explicitly legalises the legacy lane: canonical-name connection with expected version | no (declared) |
| S4 `promoted` | legacy lane: `sha(canonical) == expected` | no (declared) |
| S5 `old_killed` | server `proc_kill` executed by the new process | **YES.** X2's authorization fix registers `CanaryOldPID` in `spawnedPIDs` **at canary startup**. On the first hop there is no canary: 1.18.6 has no canary flow, so it starts the new 1.19 binary as a plain agent. The new process never registered the old PID, so `handleProcKill` refuses with `permission_denied` (`agent_proc.go:44-52`) → E14/F4, permanently, on every first-hop update of a standard/sandboxed agent. The contract names E14 but treats it as a missing-registration bug, not as the guaranteed state of the bootstrap lane. |
| watchdog | §3.2 assigns the watchdog to the OLD process; on hop 1 the old process is 1.18.6 | **YES (unstated).** No watchdog code exists in 1.18.6, so E5's "watchdog branch (i) at 90 s" cannot fire on the first hop; a staged `.next` is never quarantined. The server's `canary_connect_deadline` marks the job failed, but nothing on the host cleans up. |

Three of the six steps are bootstrap breaks. The contract's §10 table says "the four checks use only `fs-hash`/`proc-list`/`exec`, all present" — true and well-verified — but the breaks are in the *state evidence* and the *kill authorization*, which §10 does not walk.

---

## R7 — Idempotency/concurrency vocabulary · **FAIL** (one word reused, codes are a new dialect)

- `replayed` is genuinely P8-10's: every exec response carries it (`P8-10_HYGIENE.md:151-159`), `false` on a real run, `true` on a stored-body replay. Reusing the name and the flipped-on-replay semantics is correct.
- The 409 family is **not** the existing vocabulary. The tree's only idempotency codes are `IDEMPOTENCY_IN_FLIGHT` (`server_api.go:303`) and `IDEMPOTENCY_STATE_UNKNOWN` (`server_api.go:309`). The contract introduces `LOCK_BUSY`, `UPDATE_IN_FLIGHT`, `UPDATE_PREVIOUSLY_FAILED` (§7.4 lines 456-458) — I grepped the whole tree for each: **0 hits**. Two of the three are semantically the same condition as `IDEMPOTENCY_IN_FLIGHT` (a duplicate while work is in flight), so a client now has to distinguish `IDEMPOTENCY_IN_FLIGHT` from `UPDATE_IN_FLIGHT` for the same class of answer. That is a second dialect, not a reuse.
- The mechanism is also new, not reused: P8-10's implementation is `internal/server/idempotency.go` keyed on a caller-supplied `idempotency_key`; the contract's is a lease + job store keyed on artifact sha (§5.1-5.2). The word "reuse" overstates it; the design is sound but the claim of vocabulary continuity is not.

---

## R8 — UI verdict · **PASS**

Checked all six files against `web/src` at `0352f3b`:

| file | exists | cited line verified |
|---|---|---|
| `web/src/components/agent/UpdateTab.tsx` | **no — declared new** | correct to declare it |
| `web/src/pages/AgentDetail.tsx` | yes | tabs array at `:17` ✓, fetch at `:42` ✓, `agent.version` at `:106` ✓, render switch begins `:80` ✓ |
| `web/src/api/client.ts` | yes | `redeployAgent` at `:299` ✓ |
| `web/src/api/types.ts` | yes | — |
| `web/src/pages/Agents.tsx` | yes | capabilities editor hook at `:40-42` ✓ |
| `web/src/components/agent/ProcessesTab.tsx` | yes | — |

No cited file is missing, and I found no file that must change and is absent: the tab list, the render switch, the 5 s poll (`AgentDetail.tsx:47`), the flat `api` object and the per-agent dialog are all present exactly as the verdict claims. "Additive" is the correct verdict for the work described.

---

## R9 — Windows console vs service install · **PASS**

The two coexist rather than one replacing the other, verifiable in the tree:

- The service host is a separate `//go:build windows` file (`cmd/probe/service_windows.go:1`) whose `Execute` runs the same `runConnect` the console uses (`service_windows.go:37-40`) — the agent loop is shared, not forked.
- Service flags are dispatched before subcommand routing and are exact-match only (`cmd/probe/main.go:23-33`), so `--service` does not swallow `--service-status`; `--install-service` is an install-and-exit one-shot (`service_windows.go:217-235`), it does not disable foreground mode.
- The contract's design (§9.2) keeps `probe.exe --console` for foreground and adds `--status-window` as a separate non-elevated reader of `status.json`. Attach-console-to-service is rejected on the session-0 mechanism (§9.1), which is correct: a console allotted inside a service has no visible window station.
- The only unbuilt piece is `internal/agent/status_record.go`, correctly listed as **new** in §9.4. Runtime leg remains UNVERIFIED-ON-HOST per P8-10 §4 and O2 — graded on code, not asserted as tested.

---

## Fix list (blocking, in order of blast radius)

1. **Kill authorization on the bootstrap lane (R6).** Register the inherited PID in `spawnedPIDs` wherever a non-canary update starts a successor, or have `handleProcKill` accept a PID bound to a server-signed job. As written, every first-hop update of a standard/sandboxed agent ends F4.
2. **Check (c) identity (R1/R3).** Delete the `server_ws.go:93` citation (no PID is recorded at connect) or implement it. Replace `name == filepath.Base(canonical)` with a comparison against `ps`'s truncated `comm` (or compare a prefix), otherwise every POSIX agent suppresses its own kill.
3. **60 s fallback vs the no-self-kill rule (R3).** Either require (a)+(b) before the fallback may fire, or delete the fallback and let the server's pending update expire. As written, the fallback can leave zero agents serving the canonical name.
4. **Watchdog/swap exclusion (R4).** Take the same file/lease lock across `swapBinaryPaths` and branch (ii), or set N above `connect_deadline + promote_deadline` with a stated margin. Zero slack plus an unlocked restore is a live collision.
5. **Journal evidence (R6).** Either ship a writer (the package has formats and parsers only, and zero importers) or rest S2 and every `event=...` line on a server-observable surface. Right now the contract's most-repeated evidence form does not exist in the product.
6. **409 vocabulary (R7).** Reuse `IDEMPOTENCY_*` or justify a new family explicitly; do not claim reuse of a vocabulary that has two members and ships three new names.

## Non-blocking observations

- Two further wrong citations in the evidence base, both cosmetic but they weaken the "every claim is file:line" header: M16 cites `api_v1.go:352-356` for `GET /api/v1/builds/{id}/download` — that range is proc-list/proc-kill/proc-start; the route is `api_v1.go:422`. M13 cites `server_ws.go:20-25` for the `pendingUpdate` struct — that range is `writeToAgentLocked`; the struct is `server_update.go:20-25`. The substance of M13 is correct: `NotifiedAt` is written at `server_update.go:154` and never read.
- X3's defect is not real (exec already wraps in `cmd /c`); keep the platform-selected command if you like it, but do not present it as a fix to a measured defect.
- §2.1(c) shows `identity_exe` in the sample status body (line 427) although L3 is 1.19-only; the sample should show `identity_source` instead so a 1.18.6 job does not appear to have an exe.
- `internal/upgrade` has zero importers in the whole tree. The rollback story in §7.3 ("existing kit, no new agent command") therefore needs a wiring unit; "existing" currently means "present and tested", not "reachable".
- `confirm_timeout_s`, `watchdog_n` and `PROBE_UPDATE_WATCHDOG_S` are three names for one number. Pick one and derive the others.
- The contract's own header still says `independent reviewer: pending`; this review is that reviewer's output and is not an edit to the contract (per brief).

## Limits of this review

- No Windows host: §9's runtime leg, `taskkill` behaviour under mixed principals (O3/E9), and the locked-exe claim are graded on source and on P8-10's own UNVERIFIED-ON-HOST marking. They are not passed.
- No live 1.18.6 binary was available in this worktree to execute the bootstrap lane end to end; R6 is derived from the wire types and the code paths, which is sufficient to show a type-level break but is not a live run.
- Suite counts are mine on the four packages touched by every claim; the full repository suite was not re-run.

---

reviewed_by: reviewer (independent)
verdict: FIX-FIRST
verdict_sha_of_head: 0352f3b71d801902ea32703299aebf18bcfe069f
contract_sha256: 792c113c12463d563ca60b0c52552799d853c55310f0229bb34c81f57d373174
suite: 449 PASS / 0 FAIL, 4 packages (agent, upgrade, server, protocol)
ratification input: NOT-READY
