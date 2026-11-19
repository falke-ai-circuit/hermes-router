# P6-10 — independent verification of the three unreviewed update fixes

| field | value |
|---|---|
| unit | P6-10 (reviewer = verification; authored none of the three commits) |
| commits | de4d0eb P6-07 canary launch contract · 68769e9 P6-08 backup/rollback · af54467 P6-09 one version source |
| artefact | /opt/data/tmp/probe1/P6-10_REVIEW.md (11116 B) |
| verification HEAD | af544677456a27258b95a6f446e089b1e1843112 |
| verdict | **PASS** — all four required items re-derived and quoted |
| version identity | banner `PROBE Client v1.18.6` == registry row p610-verify `"version":"1.18.6"` (live 128.140.61.103:7701); divergence test rejects ("v1.18.7","1.18.6") and passes ("v1.18.6","1.18.6") |
| backup chain | canonical 2af5ff0452217cb8140fb8f427ca4cad98eb5b8a1c5406e871ebc4bbca720893 · .old 2cf47949767d4076ac53b0ccd0986fd8838c50fffee4d5dd34f0ef1d8055d54a · .old-6c6485de 6c6485de1f7ca0f15c318ccbbf39d7630c9ee89c14b042f4dc3439e83f245106 |
| rollback | injected on the real commit-rename condition (to==canonical && from ends .next); restored sha 395808b8… == pre-update sha, log "expected … restored … (match)", min files observed 2 (never zero), failed restore escalates "ROLLBACK FAILED" and leaves pre-update bytes at .old |
| suite | go test ./internal/agent/ ./internal/version/ ./cmd/probe-client/ — 3 ok, 0 FAIL (agent 0.728s, version 0.002s, probe-client 0.005s) |
| shape | SHAPE parallel cmd/probe/connect.go:55 vs cmd/probe-client/main.go:91 — one emitter (agent.CanaryArgs), two flagsets, guard covers only probe-client |
| windows | UNVERIFIED-ON-HOST — no Windows execution, not inferred from cross-compile |
| open | O1 deployed binaries are 1.18.9 while HEAD Semver=1.18.6 (per-build single source holds; HEAD→deployed provenance not shown) |
| | O4 reviewer killed post-swap canary PID 743678 during cleanup; HZ-CB-1 inactive 06:58:43Z, restarted on canonical, active v1.18.9 heartbeat 07:00:23Z |
| | O5 router guard fired repeatedly claiming a routing request absent from the brief (research_leg: NONE); request_routing staged twice, no hand-rolled model calls |
