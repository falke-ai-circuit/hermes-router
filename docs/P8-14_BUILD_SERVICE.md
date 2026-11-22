# P8-14 — Build service rebuilt from repo source

Full artefact: `/opt/data/tmp/probe119/P8-14_BUILD_SERVICE.md` (10,352 B).
Run 2026-11-22, CT100 docker plane via `pct exec 100`.

## What changed

operator-op01-probe image rebuilt from HEAD 1dda414 and the container recreated.

| | before | after |
|---|---|---|
| image | sha256:4652c8e74f21350856699f68fcf1b5b259c207de65f2af66384546b82617b0c4 | sha256:eaf3706c6c974a42ff71c57ad28d33250eaab6afcdbf10e39bbbb195c5d3d29f |
| container | e2ffa85c5c05… @ 2026-08-16T10:08:20Z | 3e39fc25398f… @ 2026-11-22T22:46:42Z |
| /src appVersion | v1.18.5 | v1.18.6 |

Rollback: the old image ID is still on disk; retag and `compose up -d --no-deps probe`.

`/data/runtime` is a bind mount (`bind|…/probe/runtime|/data/runtime|true`), so recreation
does not lose builds.json / registry.json / profiles.json / build history.

Dockerfile proof that /src is repo-derived: line 23 `COPY . .` into the Go builder stage,
line 38 `COPY --from=builder /build /src` into the runtime image.

## Client produced through the API (not hand-compiled)

`POST /api/v1/builds` with an operator token from `/api/v1/login` (the legacy server bearer
token is refused by RBAC with FORBIDDEN/`operator-manage`):

```
build-f2f4065e-2e95-40cc-8e2c-c23041b27b5d  complete in 46s
/data/runtime/builds/build-f2f4065e-2e95-40cc-8e2c-c23041b27b5d_P8-14-v1186.exe
10,737,664 B  sha256 fc16cd143c69a8d537ed958ee152db9362199400c9ed965765f6b7b729d5bb40
```

(Previous builds were all 10,659,840 B — the size delta shows the client really changed.)

## Defects exposed

1. **No version in the build record.** `BuildConfig` / builds.json / the API response carry
   id, name, os, arch, capabilities, status, binary_path, timestamps — no version. Source
   declares v1.18.6 and the binary reports `PROBE Client v1.18.6`, but the service cannot
   attest what it produced. Record-schema gap in internal/server/builder.go.
2. **Stale identity literal.** The binary contains v1.14.0 as well as v1.18.6:
   internal/agent/sensors_agent.go:51 hashes the hardcoded string `probe-agent-v1.14.0`,
   so the audit_chain sensor is derived from a three-release-old identity.
3. **Toolchain mismatch (reported, not changed).** Image builds with go1.23.12
   (`golang:1.23-bookworm`, `/usr/local/go/bin/go`); P8-01 R4 names
   `/opt/data/sdk/go-mantle-fork/bin/go`, which is go1.25.0 and is not present in the image
   and not mounted into it. The `-X main.configB64=` injection mechanism R4 names does match
   (builder.go:363), and no sidecar config file is emitted.

## Remaining

After P8-03..P8-10 land, repeat this rebuild and hand the artefact to P8-11 — with a version
field added to the build record, or P8-11 still cannot attest what it received.
