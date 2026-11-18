# P6-03 — build and prove (v1.18.6 artifact)

Full evidence: `/opt/data/tmp/probe1/P6-03_BUILD_PROOF.md`

- source: worktree /opt/data/projects/PROBE_UPGRADE/worktree, HEAD 9eeff87d9359fe83f5c5832cbac64717290fd9f3, no source edits; toolchain go1.23.12
- artifacts (Makefile lines only):
  - build/probe-client-linux-amd64 — sha256 0547bd68f6ebf27f1d187e1637b00bf8498763bc2d33ab755b374423e4a5b501, 10225188 B, `1.18.6`/`v1.18.6` in strings, runtime banner `PROBE Client v1.18.6`
  - build/probe-client-linux-arm64 — sha256 cc766ead3e2932c8b8fdaf4f6003f5626f766c697021543d4496a5eadcadcd98, 9795703 B
  - build/probe-client-windows-amd64.exe — sha256 6870d4eaa30f03b8ee6a555614aac42bca0e12ebc445f2f43f36f7bc1f9c504d, 10738176 B, PE32+ x86-64 (header parse; `file` absent on host)
- live registration (linux leg, ACCEPTANCE): client logged `Connecting to ws://probe:7701/ws as 'P6-PROOF-1'` then `agent.go:260: Connected.`; server `GET /api/v1/agents` row `P6-PROOF-1` version `1.18.6`, os linux/amd64, status `active`, `error_count 0`, heartbeat advancing (22:29:52Z, then 22:30:12Z). Baseline 19 agents -> 20.
- cleanup: `kill 699925` (SIGTERM) then `DELETE /api/v1/agents/P6-PROOF-1` -> `{"ok":true,"data":{"removed":"P6-PROOF-1"}}`
- windows: UNVERIFIED-ON-HOST — cross-compile is not connection proof
- feature presence at 9eeff87: integrity hash before execute `internal/agent/agent_update.go:86-96` + `:173-185` (hashFile `:237`); exec quoting `internal/platform/shellargv.go:28-45` + `platform_windows.go:238-251` (windows-only symbols, verified via `go tool nm`); fs-list timeout `internal/agent/agent.go:40,45-56,594-611`; fs-read range `internal/agent/agent.go:625-640` + `platform_linux.go:65-80` / `platform_windows.go:172-184` + `protocol/messages.go:220-228`
- suite: `go1.23.12 test ./... -count=1` -> 8 packages ok, 0 FAIL
- open items: (1) `make cross` red at 9eeff87 — darwin-amd64/arm64 fail with `undefined: debugManager` etc. (no darwin debug impl; agent_debug.go windows-only, agent_debug_linux.go filename-locked); (2) `DELETE /api/v1/agents/{id}` returns removed but `registry.Unregister` (internal/server/registry.go:196-207) only marks inactive, so the row persists (P6-PROOF-1 remains listed inactive); (3) live server reports v1.18.5, artifacts exist only on this host.
