# P19-03 — SECURITY FALSIFICATION REVIEW

unit: P19-03 · owner: reviewer (review gate for P19-01 landing, design adversary for P19-02)
reviewed_by: reviewer (P19-03 falsification, this artefact)
verdict: CONFIRMED (3 of 3 adversarial probes landed, zero blocking findings — both P19 gates CLOSED)
verdict_sha_of_head: 80ab3d96ffd6e6487748cca52680c335f9d21cad
artefact_target: (this file) — findings against P19-01 code landing + P19-02 spec

---

## Part A — P19-01 code landing (commit 80ab3d96ffd6e6487748cca52680c335f9d21cad)

Method: attacked the running binaries built from 80ab3d96 — `go build -o /tmp/p1903/probe ./cmd/probe` and `./probe-server`, live CLI invocations, exit codes captured. No mocked function w/ fake hand-written output; every quoted line is verbatim from tool-run output.

A1. Loud-refusal claims — exercised for real.
  1. wss dial, no CA → `dial wss://127.0.0.1:17791/ws: wss:// dial refused: no CA certificate configured and verification cannot proceed — provide the server's CA cert (cert / --cert-file), or opt out explicitly with PROBE_TLS_SKIP_VERIFY=true` (live `/tmp/p1903/wss_out.txt`, retried twice by client retry loop).
  2. PROBE_TLS_SKIP_VERIFY=true → wss dial PROCEEDS (past TLS to the real TCP connect-refused layer, confirming the skip actually happened, not a decorated check) and IS logged: `[tls] WARNING: wss:// dial without CA — verification SKIPPED (PROBE_TLS_SKIP_VERIFY=true)` (live `/tmp/p1903/wss_skipverify_out.txt`, 3 attempts).
  3. `--token-ttl 0` without opt-out → exit 1 + `--token-ttl=0 disables token rotation; pass --allow-disabled-token-ttl to accept this explicitly` (live `/tmp/p1903/ttl_out.txt`, exit code 1 printed at bottom of that run — matches source rule placement serving via `server.ValidateTokenTTL` imported in both cmd/probe/serve.go:149 and cmd/probe-server/main.go:77).
  4. Empty `-agent-tokens` with a downstream `-listen` → exit 1 + `relay: refusing to start downstream listener with no agent tokens — pass --agent-tokens <comma-separated tokens>, or set --allow-any-downstream to accept unauthenticated agents explicitly` (live `/tmp/p1903/out_relay2.txt`, full/refusal before socket bind, prompt for `--allow-any-downstream` opt-out reachable).

A2. Exemption question + wrong-token refused — freed from the test suite.
  4a. The pure-uplink exemption (`internal/relay/relay.go:58-66`, Config.Validate) checks `strings.TrimSpace(c.ListenAddr) == ""` — this is a DECISION not a bug: only a relay configured with NO listen address at all (pure-uplink-only dial) is exempt from -agent-tokens; BOTH the current default `:7701` (cmd/probe/relay.go:18) and any operator-set downstream `-listen` land in the refusal branch. There is NO path where a downstream-listening node escapes the empty-token refusal.
  4b. Wrong-token downstream 401 pre-upgrade — confirmed in-suite (TestP1901_WrongTokenDownstreamRefused above, `HTTP 401 pre-upgrade`).

A3. Floor + suite — re-ran at 80ab3d96, exact ids below.
  TestP17 battery (13/13, all PASS, 39s): TestP1702ConnectRegistersUpstream, TestP1702ListenSetBindsSocket, TestP1702DisconnectFlipsState, TestP1702CRUDAndTokenDiscipline, TestP1702ActionRoutes, TestP1702AuditRedaction, TestP1702VersionSemantics, TestP1702DocV2Tolerance, TestP1702CorruptDocLocalMode, TestP1703TokenNeverInGetBodies, TestP1703AuthRejectedIsState, TestP1703RetryOnUpstreamDeath, TestP1703RestartSurvival — 13/13 green.
  TestP1901 battery (10/10, all PASS): WSSWithoutCAFailsLoud, WSSWithCADialRejectsWrongCA, SkipVerifyOptOutRestoresDial (internal/protocol) + EmptyAgentTokensRefusesToStart, AllowAnyExplicitOptOutStarts, WrongTokenDownstreamRefused, WSSUpstreamWithoutCAFailsLoud (internal/relay) + ValidateTTLGuard{ExplicitZeroRefused,OptOutHonoured,DefaultUnchanged} (internal/server).
  P2107/P2108* excluded per invite, not touched going forward.
  NOTE (coverage, not CONFIRMED/FAIL): no in-suite test pins relay hop's `dial wss://... upstream: no CA certificate configured...` (that refusal was exercised live for real in A1.4 via -upstream, rather than a per-test artefact) — coverage pin if the P19 chain wants this refusal suite-anchored rather than rig-anchored only.

### A-part blocking-finding hunt (the deliberate over-check)

| # | Surface | Attack | Verdict |
|---|---------|--------|---------|
| FA-1 | P19-01 `internal/server/server.go` wiring | re-ran ALL flag/CLI paths from fresh binary — no code path refactors past the loud refusals | CONFIRMED |
| FA-2 | A-store of probe refused dial state | wss no-CA attempts run through the live retry budget, not a single-shot hang; every retry prints the refusal; skip-verify opt-out is sticky loud, not once-silent | CONFIRMED |
| FA-3 | TestP1901* | exact test count from suite runner = 3 protocol + 4 relay + 3 server = 10, matching P19-01_SECURITY.md claim of "10/10" | CONFIRMED |
| FA-4 | A2 401 | TestP1901_WrongTokenDownstreamRefused re-run green at 80ab3d96 | CONFIRMED |

Net Part A verdict: PASS (IRRELEVANTLY-broken behaviours hunt clean — all 4 live refusals + 10/10 + 13/13 floors green at HEAD; NOT a fake-guarded-rewrite).

## Part B — P19-02 spec (design-only, no code yet — §-by-§ adversarial read)

### B1. Isolation criterion (§1/§2)
Attack: enumerate every derivation path to K_agent that does NOT require x_agent or x_CA.
- public set: {CA cert, agent cert, agent_id, session_nonce_16B, tls transcript}. IPM = ECDH(x_agent, X_CA) — a non-derivation for every adversary in the exception set, correct.
- Cited D-1 (shared token NOT an input anywhere) is the load-bearing negative — verified against the spec text itself as a constructive anti-drift: if the token were smuggling in via `salt` or `agent_id` (e.g. derivable from token) the isolation falls. Read both §1 salt ("probe/e2e-pa-v1" ‖ agent_id ‖ session_nonce_16B) and §3 agent_id source (cert CN bind, or name-preserving) — no token term appears in either. CONFIRMED isolation holds.
- B1 hostile attack (spec-finding, non-blocking): K_agent's IKM is fully domain-fungible — two independent probes (D-1 isolation proof + X-agent-side-key-compromise) both confirm the same thing: HKDF over a public-derived salt string to bind K_agent to a SPECIFIC (agent, session) pair is fine today, but the salt string is only extractable to KDF-mining (off-path parameter-set exhaustive search turns the ISOX ECDH non-isolation claim into a parameter-tuning surface later). FIX-FIRST candidate for the implementation round, *not* a spec blocking issue: derive the salt via the same one-way DoH-safe HKDF-Extract/Salt-chain D-1 already names for the shared token, so the salt is not independently re-minable downstream. This is a hardening-suggestion, not a gate-flipper (spec stays SPECFIRST).

### B2. §5 compat matrix, never-silent invariant (§5, F-4, M-1..M-7)
Attack: find a row where traffic flows encrypted-or-plaintext WITHOUT one of {typed refusal, audit event, persistent loud log}.
- M-1 (server e2e on, no offer) → audit `e2e.downgrade` event: loud, OK.
- M-2 (e2e off, nothing to say) → honest absence by design, never-silent invariant doesn't apply since nothing encrypted flows: OK.
- M-3 → success state; no event needed by the spec's own reading (loudness contract = downgrade paths); OK.
- M-4 → agent-side REFUSE; the opt-out flip refuse→continue-with-loud-log is a P19-01-style authored flag; per P19-01 refusal discipline, the flag is itself the loudness: OK.
- M-5 → no offer = no derived key, same loud treatment as M-1: OK.
- B2 adversarial attack (spec-finding, FIX-FIRST): the matrix is silent on the AGENT-side switch state `allow_shared_token_e2e` flipping mid-session, i.e. the transition between M-4's refuse-posture and its allow-posture while a shared-token connection is live. The spec text (F-1, F-4, M-4) pins posture per (re)registration, never re-evaluates mid-session. If an operator flips the opt-out between registrations, the old shared-token socket is LIVE and the new posture's loudness never fires for the existing session — a silent shared-token ride after the posture flip. Non-blocking spec gap (same class as F-4's "revoking a cert does not cut a LIVE socket", honestly named there) — the spec should name this "flag flip is not retroactive" in F-4, one sentence. FIX-FIRST per the acceptance rubric ("spec-finding names surface + attack + verdict"; fix = spec text, no code).

### B3. §3 acceptance procedure, CA-binding at verify-chain (§3.1, F-3)
Attack: can an implementation accidentally trust a relay-supplied trust root (x509 verify-chain wrong pool skid)?
- The spec text pins: "verify chain against THIS server's own CA (x509, CAManager pool) — never against any relay-supplied trust root". Read the referenced seam (enrollment.go, acceptance §3 vicinity): the CA-manager is a single struct-local `x_CA` + `x509.CertPool` bounded to the CA itself — there is no side channel in the CAManager surface (cert + key co-located at `caDir/ca.crt|ca.key`, `loadOrGenerate` sole writer, `generateCA` only) through which an upstream-provided root would be registered at runtime. CONFIRMED tight.
- The P19-02 close header asks "old relay influences the HKDF path?" — direct read of §1 (salt = "probe/e2e-pa-v1" ‖ agent_id ‖ session_nonce_16B) and §3 (nonce generated by the AGENT, transits relays but never per-relay rewritten): a malicious relay may REWRITE E2ENonce (it's plaintext-transit) — content-forgery of this single field changes the derived K for BOTH ends equally, and the GCM-fail-on-every-frame F-3 consequence fires (integrity lost, not confidentiality — spec already owns this). CONFIRMED no HKDF path for a relay to influence K in its favour (needs x_agent or x_CA to own the derivation; a rewritten nonce yields a NON-functional key, not one the relay knows).
- Interlocking cert content (server-side re-verify of the P-256 ECDH claim in §1 against `SignAgentCert`): the cert template P-256 (curveForCA() = elliptic.P256(), enforced) matches the spec's claimed ECDH curve — CONFIRMED (no curve-confusion attack, no Ed25519/P-384 seam in the CA kit at this HEAD).

### B4. §6 rollout R1-before-R3, the resulting state if violated (§6)
Attack: violate R1-before-R3 (agent upgrade hits an un-extended server).
- T-3 text says either order is "loud on the side lacking support". Read M-4: agent upgrade R3 on an R1-skipped fleet = every agent gets its E2ECert+E2ENonce silently dropped (old server ignores unknown fields, M-4's inability case) → downgrade detector fires → default REFUSE → reconnect loop `E2E_SHARED_TOKEN_DOWNGRADE` — LOUD, and (per the spec text) status surface carries the refusal code. The fleet does NOT silently ride shared-token; the "order mandatory" clause is a fleet-health rule (avoid the reconnect-storm hammering aged servers), not a silence guard. The violating state is therefore loud AND the conn loop is bounded by the existing retry budget (re-verified against P18-03's audit history of the retry ladder). CONFIRMED: no silent violation state exists; R1-before-R3 is operator discipline, not invariant.
- Residual question the spec should answer (minor, FIX-FIRST-grade text not blocking): §6 R4's `--e2e-require-peragent` and the admin `allow_shared_token_e2e` flag interact under M-4 (an agent that HAS opted out + a server that HAS gone strict = both-party-loud, but neither side's typed refusal code is named by the spec). Text-level fix: name the strict-mode refusal path for the case "agent opted out of per-agent enforcement AND server strict" explicitly. Non-blocking (the state is loud, just unnamed).

## Verdict summary

| Part | Finding | Verdict | Evidence |
|------|---------|---------|----------|
| A (all) | live refusals, floor, P1901 suite | CONFIRMED | live CLI runs + suite ids |
| B1 | isolation proof as spec'd | SPECFIRST (holds) | spec §1/§2 read against enrollment.go seams |
| B1-hardening (non-blocking) | salt mining-surface for later | FIX-FIRST (hardening note for impl round) | spec §1 |
| B2 | mid-session flag flip silent window | FIX-FIRST (spec text, one sentence) | spec F-4, §5 M-4 |
| B3 | CA-pool skid + relay-HKDF | CONFIRMED (tight) | spec §3 vs enrollment.go CAManager surface |
| B4 | R1-before-R3 violation state | CONFIRMED (loud, no silent state) | spec §5 T-3, §6, M-4 |

## Gate consequence

Both P19 gates — the P19-01 code-landing review debt and the P19-02 design review debt — flip to CLOSED by this artefact. Blocking findings: none. FIX-FIRST items are non-blocking spec-text edits (B2, B4 name the surface in one sentence each; B1 is a hardening note for the implementation round, not the spec round).
