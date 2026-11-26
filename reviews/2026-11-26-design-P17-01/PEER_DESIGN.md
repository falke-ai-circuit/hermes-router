# P17-01 — PEER DESIGN: peer lifecycle, servers CRUD, state machine, credentials, audit

**owner:** architect · **status:** spec complete (design only, no code) · **unit:** P17-01 (R17-01-FEDSPEC leg)
**research_leg inputs read before specifying:** P23-00_ASIS.md (§2 degraded vocabulary), P24-01_RELEASEPROTOCOL.md §6 (R2 preconditions → not a peer concern; encryption lands R2, so this spec carries NO crypto surface), P26-01_APISUITE.md (the /api/v1 surface a peer must not break — 129 routes, golden fingerprints), P22-07_TUNNELF4.md (publish-verified-after-first-byte; forward_policy vocabulary)
**foundation measured on disk at worktree HEAD 5208a77 (r119-build):** F4 tunnel fixed live 20/20+25/25; F3 typed admission (admission_refusal.go); F5 refusal propagation (internal/relay/refusal.go — ForwardRefusal, Retryable()==false for FORWARD_*); relay_all settable (c593f4d); P23-11 reds F4+relay_all closed pending review; suite green (840+, 3 named pre-existing flakes).
**Deviations from board, named:** (1) servers.json was fixed by P22-02 as the *observation-of-record* (runtime-written, fingerprints only, "reads go through the live peer map, never a second cache"). CRUD therefore lands as a separate `default_peers` (operator *intent*) namespace inside the SAME serversDoc — not as overwriting the runtime `peers` records. URI chosen is `/api/v1/peers` with `/api/v1/servers` kept as the read-only inventory, because POST-to-a-list-that-means-complete ("servers") would imply PUT-replace-everything semantics the inventory refutes. (2) The board's "version" state is named `version-mismatch` and defined precisely below (§4).

---

## §1 Lifecycle mechanism: IN-PROCESS relay (internal/relay as importable package with Config)

**Decision:** the peer backend imports the existing `internal/relay` package into the server process and runs its uplink loop as a managed component (`peerManager` in `internal/server`, one goroutine set per configured default_peer). The alternative — exec `probe relay` as a subprocess per peer — is rejected.

**Why in-process (traceable):**
- `probe relay` is already an in-process *mode* of this same binary (`cmd/probe/main.go` mode registry: `mgr.StartMode("relay", cfgJSON)`); exec'ing it buys no isolation the OS already gives the peer's own process group, and adds: a distribute-and-version-skew problem (subprocess binary vs server binary age), a second log file with no audit-log sharing, a supervisor (`mgr`-equivalent) to rebuild, and an auth-path problem (subprocess needs the token ON ITS COMMAND LINE or in ITS OWN config file — a second at-rest surface, violating §6's containment).
- The peer IS the server's half of one feature: servers.json CRUD, credential store, audit logger, and the uplink must share ONE process to keep the identity chain of P24-01 §5 auditable ("deployed sha == image revision" is per-binary; a second binary doubles that surface).
- `internal/relay` already isolates uplink concerns: `UpstreamStatus`, `PeerHello`, `ForwardRefusal` (terminal-classification consumer of P22-05), reconnect backoff. Config carries listen/upstream/relay-id/token/max-agents — the in-process component needs only a dial loop, not a listener, so `Config.Listen` is unused downstream and is explicitly ignored (named so a reviewer doesn't hunt for a second listener).

**Failure modes (each owned):**
- **FM-1.1 relay panic takes down the server.** Owned: every uplink read/write loop in the imported package runs under a recover that converts panic → link down → `retrying` (§4) + audit event `peer.panic`. The panic must never cross the peerManager boundary; the SERVER's accept loops are untouched by this rule.
- **FM-1.2 relay code blocked = server's agent dispatch blocked.** Owned: the uplink loop writes on its OWN socket; contention with server work is bounded by the P15-12 lock order (`relay_ws.go` header): peerManager never takes a session's `channelsMu` while holding `relayMu` — same contract, new caller, restated as binding.
- **FM-1.3 config change while connected.** Owned: PUT/DELETE on a default_peer is applied live, not at-restart-only: disable→close uplink, state → (re)enabled→spawn. Fork-bomb guard: peerManager is a single owner; a re-apply of identical config is a no-op (Config equality), never a second goroutine set.
- **FM-1.4 transient upstream outage at server boot.** Owned: spawn starts in `retrying` with the existing 1s backoff; boot NEVER blocks on peer connect (server health must answer within its existing budget with peers still `retrying`).
- **FM-1.5 nested-relay path (relay.go §nested):** a default_peer that itself accepts agents (listen set) is permitted — the chain-metadata refusal in `relay_ws.go` (P21-04) already terminates cycles server-side; peerManager sends the chain it was given, adding itself.

## §2 /api/v1/servers (and /api/v1/peers) CRUD persisted to servers.json

**Reading of the board clause:** CRUD for the peer *configuration*, persisted. Inventory GET stays `/api/v1/servers` (P22-02 contract, `serverEntry` shapes unchanged in kind — the peer object additions in §3 land there). Configuration lives under:

```
GET    /api/v1/peers                 → {"ok":true,"data":{"peers":[ <peer-config> ]}}
POST   /api/v1/peers                 → body {id, upstream, token, enabled, listen?, max_agents?}; 201; 409 duplicate id
GET    /api/v1/peers/{id}            → 200 / 404 envelope
PUT    /api/v1/peers/{id}            → full replace; live-apply per §1 FM-1.3; token field OPTIONAL (absent = keep stored)
DELETE /api/v1/peers/{id}            → 204; closes the uplink; runtime peerRecord FOLLOWS ITS OWN RETENTION (not deleted) per P22-02 rules
POST   /api/v1/peers/{id}/action     → body {"action":"enable"|"disable"|"dial-now"} (dial-now = immediate reconnect, resets backoff)
```

Errors are the typed envelope everywhere (P26-01 relied on `ok/error{code,message}`): codes `PEER_EXISTS` 409, `PEER_NOT_FOUND` 404, `PEER_UPSTREAM_INVALID` 400 (not a ws:// or wss:// URL), `PEER_ID_INVALID` 400 (id charset identical to relay ids: `[A-Za-z0-9._-]{1,64}`), `PEER_TOKEN_REQUIRED` 400 on POST with no token and no matching existing secret. `PEER_*` joins the refusal family naming convention (FORWARD_*, PEER_*) — distinct prefix so an operator never reads a peer CRUD error as a forwarding refusal.

**Persistence:** a new namespace inside the existing serversDoc (no second file, no format fork):

```json
// serversDoc gains:
{
  "version": 2,
  "default_peers": {
    "lab-relay-1": {
      "id":            "lab-relay-1",
      "upstream":      "ws://10.0.0.5:7700/ws",
      "token_sealed":  "<CredentialStore-sealed blob, never plaintext, never a hash>",
      "token_fp":      "a1b2c3d4",
      "enabled":       true,
      "listen":        "",          // empty = pure uplink; set = also accept agents (nested relay)
      "max_agents":    8,
      "created_at":    "2026-11-26T00:00:00Z",
      "updated_at":    "2026-11-26T00:00:00Z"
    }
  }
}
```

Writes go through the EXISTING serversStore (one document, one lock, coalesced writes, `writeFileAtomic` 0600 — servers.go §"one document, one lock"). The store's own failure modes are already owned (unreadable → local-only serve + log; corrupt → quarantine + local-only); CRUD extends them with:
- **FM-2.1 token_sealed unwritable** (credential store init fails): POST fails 500 `PEER_STORE_INIT_FAILED` and NO peer is half-persisted (token sealed BEFORE the doc write; order is seal → doc → apply).
- **FM-2.2 doc v1 → v2 migration:** version bumped, `default_peers` initialised empty; an old binary reading a v2 doc must not crash — it reads `peers` only (field-by-field tolerance is the P22-02 style). Named here so the release ladder (R1 = 1.20.0) carries it.
- **FM-2.3 concurrent PUT + runtime stamping:** default_peers is touched ONLY by the API handler under the store lock; runtime records (peers map) are touched only by the relay/inventory code. One namespace, one writer class each — no cross-writer.

## §3 Peer object (built from relay_register; relayMetadata extended)

**Mechanism:** at a successful registration the upstream builds/refreshes the runtime peer from the arriving `relay_register` metadata. `relayMetadata` (relay_ws.go:16) already carries `listen_addr`, `max_agents`, `upstream`, `agent_count`, `chain` — the ONLY missing field is `version`:

```
// relayMetadata extension (single field):
Version string `json:"version,omitempty"`   // relay's own version.Semver; empty = pre-extension relay (degraded vocabulary, §4)
```

Empty is the P23-00 honest-absence rule: a v1.19.2-era relay sends no version; the field stays ABSENT (omitempty), never `""`-stamped, never defaulted to the server's own version (a defaulted value would be a lie about the wire).

**The peer object** = the existing `serverEntry` row for that peer, populated server-side at registration from metadata + peer_hello, persisted as `peerRecord` (servers.go:141) on session lifecycle:

```json
// GET /api/v1/servers — peer row (additions in <<>>):
{
  "id": "lab-relay-1",
  "name": "lab-relay-1",
  "role": "peer",
  "listen_addr": "127.0.0.1:17921",
  "listen_addr_raw": "127.0.0.1:17921",
  "listen_addr_source": "peer_metadata",
  "version": <<relayMetadata.Version, null when absent>>,
  "state": "up",
  "last_seen": "2026-11-26T00:00:00Z",
  "state_reason": "socket_open",
  "down_since": null,
  "transports": [ { "kind": "uplink", "url": null, "port": 17921, "ws_state": "open", "connected_at": "...", "source": "peer_metadata" } ],
  "counts": { "direct_agents": 0, "relayed_agents": 3 },
  << "peer_meta": {                        // built from relay_register metadata; every key emitted, null when absent (R3)
       "upstream":     "ws://10.0.0.5:7700/ws",
       "listen":       "127.0.0.1:17921",
       "agent_count":  3,
       "max_agents":   8,
       "chain":        ["server-…", "lab-relay-1"]
     },
     "link_state": "connected",            // §4 state machine
     "config_ref": { "id": "lab-relay-1", "source": "default_peers" }   // present ONLY when this peer was dialed BY us; absent for inbound relays
  >>,
  "forward_policy": null,
  "max_agents": 8,
  "token_fp": "a1b2c3d4",
  "token_fp_verified": true,
  "token_fp_scope": "shared"
}
```

**Important asymmetry (named):** `/api/v1/servers` on the UPSTREAM shows inbound relays (config_ref absent, peer_meta present). The DIALING server's own inventory shows its local row plus any peers IT serves; the dialing server's view of the UPSTREAM is not reachable from `/api/v1/servers` — it is the relay package's `UpstreamStatus` document (status.go), which §4's state machine reads. Both documents must agree on state vocabulary (they do: §4 defines the vocabulary once; `UpstreamStatus.state` up/down refines to link_state through §4's table).

**Failure modes:**
- **FM-3.1 metadata unparseable** — existing behavior holds (`meta = nil`, non-fatal, relay_ws.go:221); peer object then carries `peer_meta: {all keys null}` and counts from the channel map, NOT guesses (honest absence, P22-02 R3 rule).
- **FM-3.2 duplicate upstream registration id** — existing `duplicate_id_replaced` state_reason covers it; the displaced session's `down_since` is stamped (P23-00 T4 measured shape). No new mechanism.
- **FM-3.3 agent_count drift** — metadata's agent_count is a point-in-time claim; `counts` (conn-map read, servers.go agentCounts) is the live truth. The row shows both; they are allowed to disagree for one heartbeat. Never merge them.

## §4 State machine: connected / retrying / auth-rejected / version-mismatch

Vocabulary (closed; anything else is a bug — same discipline as stateReason§8.4):

| state | entered when | exits to | owner |
|---|---|---|---|
| `retrying` | spawn; transport drop (close 1006, read error, dial fail) | `connected` (next peer_hello) · `auth-rejected` (typed FORWARD_* refusal arrives) | peerManager; backoff 1s exponential, cap 30s, reset on success |
| `connected` | register accepted, peer_hello received (UpstreamState==up) | `retrying` (transport drop) · `version-mismatch` (§ below) | peerManager |
| `auth-rejected` | upstream sends typed `forward_refused` with FORWARD_* code (P22-05 consumer already classifies these `Retryable()==false`) | `connected` (ONLY via operator action: PUT with new token → `dial-now`) | operator-gated; NO automatic retry ever |
| `version-mismatch` | connected, and downstream negotiation fails the vocabulary: relay-side — upstream's peer_hello version older than the minimum carrying the frame types we send (peer object §3, refusal frames); upstream-side — relay's metadata.version older than the minimum for the fields the server requires | `connected` (operator upgrades the older end; PUT with a `min_version` bump → `dial-now`) · `retrying` (the link itself drops) | operator-gated |

Rules:
- **Terminal means hopeless, not broken:** `auth-rejected` and `version-mismatch` do not burn a reconnect loop (this is the measured lesson of P23-00 T5.3's infinite `close 1006` retry into a refusal). The watcher gate from P22-05 applies unchanged: Retryable=false → no retry.
- **Version semantics are READY-LEVEL, not comparison-to-server:** the server never refuses a relay merely OLDER than itself (degraded vocabulary, P23-00 §2 — an older peer still connects and gets `null`/absent fields). It refuses/halts only the frame exchanges it cannot have — that is when `version-mismatch` asserts, and the state carries the two versions: `{"peer_version":"1.19.2","required_min":"1.20.0"}` surfaces on the peer row (`link_state` object detail) and in the relay's UpstreamStatus. Both versions: kept honest, never defaulted to own.
- **Restart crossing:** state is RUNTIME; the only persisted trace is peerRecord's state_reason/down_since. After restart everything spawns as `retrying` — never as `connected` (claiming connected before peer_hello is the P22-02 non-forgery rule).

## §5 Restart recovery

Sequence on server start (peerManager bootstrap, for each enabled default_peers entry):
1. Load serversDoc.default_peers; for each enabled entry, seal-check the token (§6 store opens — an unwrappable token is `auth-rejected`-PRE state: entry marked `PEER_TOKEN_UNSEALABLE` in the peer row's link detail, NO retry, operator fix; avoiding a mismatch loop against a token that can never succeed).
2. Spawn in relay order: link to `retrying`, backoff from 1s. first_seen of the resulting runtime peerRecord REJOINS the persisted record (peerRecord already persists across sessions; a reformed relay is the SAME node — P21-07 session id/fingerprint semantics unchanged).
3. Recovery completion is the first peer_hello per peer (→ connected). No burst: spawns are staggered by 250ms per peer (N-peer restart must not look like a flood to a recovering upstream — and the upstream's own admission limiter is the P21-04 token bucket; bursts would be refused as frames, not as policy).
4. If servers.json is unreadable/corrupt at boot (existing quarantine path): default_peers is likewise treated as absent — server starts LOCAL-ONLY, logs `peers config unavailable — local mode`, health answers, NO half-federation (an uplink with a wrong or missing token is a security incident, not a degraded state).

**Failure modes:** FM-5.1 upstream still down at boot → stays retrying per FM-1.4 (covered; no separate path). FM-5.2 reboot storm (N peers all restarting after host reboot): the upstream sees staggered dials (3), and its per-link max_agents caps the flood — same refusal path already typed (F3). FM-5.3 config edited between crash and boot: boot reads only the doc (disk wins over any in-memory claim — fleet doctrine); whichever peer set is on disk is what runs.

## §6 Credential-at-rest: ENCRYPTED (sealed) + 0600, NOT hashed

**Decision:** the peer's upstream token is stored AES-sealed via the existing CredentialStore derivation (`credentials.go`: key = sha256 over the registry secret or `<store>/secret.key`, created 0600), written with `writeFileAtomic(path, raw, 0600)`. Hashing is REJECTED.

**Threat rationale (why the board's clause is right, restated for the record):**
- The relay must PRESENT the token upstream on every (re)dial. A hash proves knowledge of a *secret* only if the verifier stores/derives the same hash; the upstream verifies the RAW token (existing `bindRelayIdentity` path). A server-side hash cannot be replayed as the raw token — a hash-only store would make the peer UNDIALABLE or force storing the raw anyway. Therefore: hash = unusable; sealed = correct.
- Threat model the sealing DOES defend: offline theft of servers.json alone (a copied file yields nothing without secret.key). Threat it does NOT defend: same-privilege compromise of the server process or an operator reading memory — named honestly, because an operator confused about this boundary would over-trust the file. Defense there is the fingerprint discipline (below), not stronger at-rest crypto.
- File mode 0600 (and 0600 on secret.key — already the CredentialStore pattern) bounds the online-read surface to the server's own uid.

**API discipline:** token appears in a PUT/POST request body ONCE, is sealed before the doc write (§2 FM-2.1 ordering), and NEVER re-appears: no GET returns it, no audit entry records it, no error message echoes it. `GET /api/v1/peers` returns `token_fp` (sha256[:8], the established tokenFingerprint shape — same as inventory's token_fp so operators read one fingerprint vocabulary) plus `token_present: true`. A PUT that omits `token` keeps the stored secret (rotation done by PUT with a token; the response carries the NEW fp so the operator can confirm rotation landed).

## §7 Audit redaction rules

Audit is the existing JSONL `AuditEntry` stream (audit.go) — events ride `Action` names in a new family: `peer.created`, `peer.updated`, `peer.deleted`, `peer.state_changed`, `peer.registered`, `peer.refused`, `peer.panic` (FM-1.1), with `AgentID` carrying the peer id and `Params` carrying detail. Rules:

1. **Token: never.** Any key matching `(?i)token|secret|password|credential|authorization` in Params is replaced with `"<redacted:fp=a1b2c3d4>"` BEFORE the entry is appended (the redaction happens at the audit boundary — the only place plaintext exists in scope is the sealing call, so nothing downstream can leak what it never saw). The NewRedactor already in credentials.go is the enforcement point, extended with this key list; the default-deny list is the rule, allowlisting plaintext keys is forbidden.
2. **Upstream URL: kept.** It is operator config, observe-worthy during incidents (the P23 lab logs showed relay/upstream URLs printed constantly; the audit need not hide what the process log already prints).
3. **Refusals: kept.** FORWARD_* codes and reasons ride in full — that is the F5/P22-05 operator contract (status doc + log + audit agree on the code). Refusal reasons are server-produced strings and cannot contain the token by construction (they quote ids and limits, never secrets).
4. **Counts and state transitions: kept** (agent_count, link_state from→to) — they are the audit value.
5. **token_fp is not a secret** (8 hex chars of digest; the inventory already publishes it) and IS kept — it lets an auditor match "which credential formed this link" without the credential.
6. **Redaction errors fail closed:** if the redactor fails, the event is NOT written unredacted; it is written with Params replaced by `{"redaction_error":true}` (a missing audit on failure is also a defect — an opaque entry beats a leaking one and beats silence).

---

## Cross-checks against the research leg (traceability)

- P26-01's golden fingerprints: `/api/v1/servers` (GET) gains keys but loses none — golden-diff would flag the ADDITION, which is exactly a data-shape change to be re-captured at R1 proof time, not a regression.
- P24-01 R1 boundary: this spec ships NO crypto endpoints and NO crypto capability (encryption is R2-only; §6's sealing reuses the EXISTING internal CredentialStore plumbing and exposes no new endpoint family that advertises crypto).
- P22-07: peer relays inherit the forward_policy vocabulary including `relay_all` (c593f4d) and the first-byte-verified tunnel contract; no peer-specific tunnel behavior is added here.
- P23-00 §2 degraded vocabulary: an old relay (no version field) attaches with null version + honest-absence peer_meta — never a synthetic value.

## Verifier appendix (re-runnable probes for the reviewer)

```
# 1. relayMetadata extension point + peerRecord/serversDoc shapes cited above exist
grep -n "relayMetadata" /opt/data/projects/PROBE_UPGRADE/v119wt/internal/server/relay_ws.go | head -3
grep -n "type peerRecord struct\|type serversDoc struct" /opt/data/projects/PROBE_UPGRADE/v119wt/internal/server/servers.go
grep -n "type ForwardRefusal" /opt/data/projects/PROBE_UPGRADE/v119wt/internal/relay/refusal.go
# 2. CredentialStore seal/0600 pattern cited in §6
grep -n "writeFileAtomic(s.path, raw, 0600)" /opt/data/projects/PROBE_UPGRADE/v119wt/internal/server/credentials.go
grep -n "func (s \*CredentialStore) seal" /opt/data/projects/PROBE_UPGRADE/v119wt/internal/server/credentials.go
# 3. relay in-process mode precedent cited in §1
grep -n '"relay"' /opt/data/projects/PROBE_UPGRADE/v119wt/cmd/probe/main.go | head -3
```

No code was written, no product tree touched, nothing committed to r119-build, nothing deployed.
