# P6-05 — independent verification of P6-03

| field | value |
|---|---|
| unit | P6-05 (reviewer = verification; did not build it) |
| subject | /opt/data/tmp/probe1/P6-03_BUILD_PROOF.md (sha256 3b07b8968e367e40967a9a9874d4038d5e8b5778bc0d88a8b4aa77c915ae3bbe) |
| build HEAD | 9eeff87d9359fe83f5c5832cbac64717290fd9f3 |
| verdict | **PASS** — all five claims reproduced independently |
| sha256 re-derived | linux-amd64 0547bd68f6ebf27f1d187e1637b00bf8498763bc2d33ab755b374423e4a5b501 (10225188 B) |
| | linux-arm64 cc766ead3e2932c8b8fdaf4f6003f5626f766c697021543d4496a5eadcadcd98 (9795703 B) |
| | windows-amd64.exe 6870d4eaa30f03b8ee6a555614aac42bca0e12ebc445f2f43f36f7bc1f9c504d (10738176 B) |
| suite | go1.23.12 test ./... — 8 ok, 0 FAIL (re-run by reviewer) |
| live proof | client ran as P6-05-VERIFY / P6-05-VERIFY-2; both rows read back from /proc/612171/root/data/runtime/registry.json with version 1.18.6, status active, error_count 0, heartbeat advancing |
| defect | DELETE /api/v1/agents/{id} → 200 {"ok":true,"removed":...} but row survives as inactive — reproduced 2/2 |
| windows | UNVERIFIED-ON-HOST, as the artefact states — no connection claimed |
| open item | DELETE does not remove the registry row (server-side fix, not this unit) |
| sha256 of this review | see /opt/data/tmp/probe1/P6-05_REVIEW.md |

Full review: /opt/data/tmp/probe1/P6-05_REVIEW.md
