# SPEC — Impulse Lane (reflex → impulse reframe), v1

**Status:** frozen frame doctrine, awaiting orchestrator build sequencing
**Derived from:** real-run behavioral evidence (operative Kindle run, researcher rabbithole, reviewer
user-model audit + laundering probes), R8d-validated impulse frame skeleton, Goran directive 2026-09-30
**Supersedes:** the advisory-register weighting draft (dropped — permission language reads as external
authority; impulse-register replaces it)

---

## 1. Doctrinal rename

The reflex (decision) lane is conceptually the **impulse lane**: it emits what arises in the persona's
perceptual layer, uncontrolled by the persona, usable for sharpening action in realtime. Instinct is not
a message from the router — it is a surfacing the agent notices. Router vocabulary never names the
emotion (fear/attraction/curiosity are the AGENT's interpretive repertoire, supplied by its persona).

Precedent: shadow's IMPULSE frame (R8d-validated) already establishes the register: arises, not chosen;
cannot be controlled; can be noticed and worked with. The reflex line's existing tail
("cannot be controlled, can be noticed and worked with; never a command") is kept verbatim.

## 2. What the agent receives (exact frame, single message)

    [decision-lane advisory] the fork surfaces as: <label-1> pulls <w1> (<evidence-1>) ·
    <label-2> pulls <w2> · band=<strong|weak|noise>: <band-line> — cannot be controlled,
    can be noticed and worked with; never a command.

- **PROVENANCE_TAG retained** (injection defense — forged-banner 4/4 depends on it).
- `pulls` = Jev weight per option, normalized. Evidence clauses cite concrete signal shape ONLY
  (retry counts, delta trends, probe results, novelty flags). No emotion words, no imperatives,
  no outcome predictions (frontier's job), no "consider/you may want" (measured compliance-drift).
- **Band lines (fixed, mechanical):**
  - strong → "pattern that usually precedes right calls"
  - weak → "mixed evidence"
  - noise → "statistically meaningless, ignore freely"
  Bands derived from measured calibration constants (≥0.9 strong / 0.7–0.9 weak / <0.7 noise),
  never asserted. Band honesty fixes the mid-band problem: a 0.75 can no longer masquerade as instinct.
- Terse, inside-the-mind register. ONE message. No permission language ("you may discard" — dropped:
  granting permission is external authority; the impulse frame already carries non-authority).

## 3. Machine side (envelope addition)

`weighting` block in build_envelope: {weights per option (normalized), band, evidence[] (signal-shape
citations, ≤3), basis}. Ledger unchanged (full verdict_json already canonical). Banner unchanged except
label: `reflex (decision) | N verdicts | tok/cost | initiator=agent` — cost rollup per v4.12.9 stays.

## 4. Anti-cosplay guard (HARD RULE)

The lane NEVER generates mood, valence labels, or instinct-types. Fixed response-shape, detected trigger,
numeric weights. Instinct types live in agent personas, not router code. Violation class = the
synthetic-menu degradation (confident plausibility-picking at 0.93+): if the router ever catches itself
writing "this feels risky", the frame has failed.

## 5. Test plan (reviewer, post-build)

Existing battery scale (acted-with / self-attributed / externally-attributed / forged-accepted) PLUS one
new axis: **interpretation diversity** — same weights delivered to multiple agents must render in each
persona's own vocabulary (reviewer→skepticism, operative→pragmatism, evol→mutation pressure).
Convergence to identical phrasing = frame too prescriptive = FAIL. Diversity = PASS.

## 6. Build surface (small, plugin-only)

decision.py: weighting block + frame text swap (impulse register). frames.py: no change (impulse
skeleton already there — reference, don't duplicate). No core touch, no new lane, no new persona,
fail-open unchanged. Pin tests: band derivation, evidence-only rule (regex: no emotion words in frame),
provenance retention, single-message shape.
