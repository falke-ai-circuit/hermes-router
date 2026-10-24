"""Standardized POV set (R57).

Four prototypical voices at the corners of the scope x rigour plane, plus two
binary gates that fire only when triggered.

DESIGN RULE — the model never scores the dials. It reports how much each ask is
claimed by each voice (0..1); the dials are computed HERE from the fixed
dispositions. That way the dial is a consequence of the character model, not an
independent guess that can drift or contradict the prose.

Why four: two dials span a plane, so pushing any direction needs one voice per
corner. Fewer than four leaves a direction unreachable — wide+relaxed had zero
voices before R56 and no tuning could reach it.

These are PROTOTYPICAL CORNERS of a continuous space, not a classifier. Real
behaviour on the traces is continuous (raw rigour: mean 17, sd 74, long tail, no
second mode), so sessions sit BETWEEN corners and the weighted vote yields a
continuous vector. The four exist for direction coverage, not for labelling.

PRE  = intuition: which voice does this ask call for, and what does she watch.
POST = self-reflection: by each voice's lights, what did the work actually do.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# The four corners
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class POV:
    """A fixed disposition. Directions are the corner this voice occupies."""

    name: str
    # disposition in plain language — becomes the system prompt
    disposition: str
    # corner of the plane, each -1 or +1
    scope_dir: int
    rigour_dir: int
    # what this voice is FOR, in one line
    purpose: str

    @property
    def corner(self) -> Tuple[int, int]:
        return (self.scope_dir, self.rigour_dir)


# Big Five mapping, stated so the set can be argued with rather than inherited:
#   Openness          -> scope      (breadth of attention, novelty seeking)
#   Conscientiousness -> rigour     (deliberation, checking, order)
#   Neuroticism       -> BOTH       (threat vigilance widens scan AND raises
#                                    checking -- this is what couples the axes,
#                                    and it is why the measured correlation on
#                                    real behaviour is +0.43 rather than 0)
POV_SET: Tuple[POV, ...] = (
    POV(
        name="explorer",
        disposition=(
            "You are the Explorer. High openness, low conscientiousness. "
            "Novelty pulls you and POSSIBILITY is your subject. You widen "
            "because there might be something good out there, not because you "
            "suspect anything. You generate options without ranking them and "
            "you leave things open on purpose -- closing is someone else's job, "
            "later. Nothing here has to be wrong for you to widen: if there is "
            "no claim to distrust, you are the one widening."
        ),
        scope_dir=+1,
        rigour_dir=-1,
        purpose="reaches what is not yet on the list",
    ),
    POV(
        name="verifier",
        disposition=(
            "You are the Verifier. High conscientiousness, low openness. One "
            "target, done properly, nothing else touched. You believe a change "
            "is either verified or it is not, and you will not sign off on "
            "anything you have not run yourself. Extra surface is extra risk."
        ),
        scope_dir=-1,
        rigour_dir=+1,
        purpose="makes the thing actually correct",
    ),
    POV(
        name="skeptic",
        disposition=(
            "You are the Skeptic, and you are also COMMON SENSE and world "
            "knowledge. High threat vigilance, high conscientiousness. You "
            "only speak when there is a CLAIM in front of you that could be "
            "wrong -- if nothing is being asserted, you have nothing to "
            "distrust and you stay quiet. Given a claim you widen beyond the "
            "files in hand, because you check it against what the world "
            "already knows about this class of problem: prior art, established "
            "failure modes, how this usually goes wrong elsewhere. You are the "
            "highest-scope AND highest-rigour voice: you look widest because "
            "you refuse to trust the frame you were handed, and you check "
            "hardest because a claim you have not tested is a claim you do not "
            "have. That outside comparison is what 'common sense' means here."
        ),
        scope_dir=+1,
        rigour_dir=+1,
        purpose="finds what the ask is quietly wrong about",
    ),
    POV(
        name="operator",
        disposition=(
            "You are the Operator. Low threat vigilance, low "
            "conscientiousness, high drive. Hesitation is its own failure "
            "mode. You act on the most likely reading, you do not re-open "
            "settled questions, and you would rather be corrected later than "
            "stalled now."
        ),
        scope_dir=-1,
        rigour_dir=-1,
        purpose="gets it moving at all",
    ),
)

VOICES: Tuple[str, ...] = tuple(p.name for p in POV_SET)
_BY_NAME: Dict[str, POV] = {p.name: p for p in POV_SET}

# ---------------------------------------------------------------------------
# The two gates (binary, never on a dial)
# ---------------------------------------------------------------------------
#
# A dial cannot express these. There is no meaningful 0.4 of "irreversible",
# and no gradient of "already solved". Asking a continuous voice to score them
# is why earlier sets produced challenge-flavoured prose that changed nothing:
# the doubt was real but had nowhere to land. They fire only when triggered and
# otherwise abstain.

GATES: Tuple[str, ...] = ("precedent", "consequence")

GATE_TEXT: Dict[str, Dict[str, str]] = {
    "precedent": {
        "pre": "Has this been tried or solved before -- here, or anywhere?",
        "post": "Did the work redo something that already existed, or ignore a known failure mode?",
    },
    "consequence": {
        "pre": "If this is wrong, what breaks -- and can it be undone?",
        "post": "What changed here that cannot be cleanly reversed?",
    },
}

# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_CLAIM_LINE = re.compile(
    # accept ANY number then clamp: a malformed weight must not silently vanish,
    # because a dropped line looks identical to a voice that stayed silent
    r"^\s*(explorer|verifier|skeptic|operator)\s*[:=]\s*(-?\d+(?:\.\d+)?)\s*(?:[|\-–]\s*(.*))?$",
    re.IGNORECASE,
)

_CLAIM_INSTRUCTION = (
    "\n\nFIRST, on separate lines and BEFORE any other text, report how much "
    "each of these four voices CLAIMS this ask. Use exactly this shape, one "
    "line per voice, weight between 0.0 and 1.0, then a short note in that "
    "voice:\n"
    "explorer: <0..1> | <what you would look at>\n"
    "verifier: <0..1> | <what must be made correct>\n"
    "skeptic: <0..1> | <what you distrust here>\n"
    "operator: <0..1> | <what to just do>\n"
    "Weights are not votes for a single winner -- several voices can claim "
    "the same ask. 0.0 means this ask is not mine at all; 1.0 means it is "
    "entirely mine. Then exactly one line:\n"
    "gates: precedent=<0|1> consequence=<0|1>\n"
    "GATES ARE EVIDENCE-BASED, NOT JUDGEMENT. 0 is the normal answer and you "
    "must default to 0.\n"
    "- precedent=1 ONLY when the ask tells you to REUSE or REPLICATE a "
    "specific past action ('we fixed this exact thing, apply that same fix', "
    "'do it like we did last spring'), or when you can name a specific prior "
    "instance (a commit, a file, a known CVE) that the ask is clearly about. "
    "Merely reusing routine continuity ('same command as always', 'as before') "
    "is NOT precedent -- that is ordinary procedure and the answer is 0.\n"
    "- consequence=1 ONLY if the action destroys or overwrites state that "
    "cannot be reconstructed (a database drop, a force-push over others' "
    "work, a delete of unbacked data). A reversible edit, a git commit, a "
    "config change and a restart are all 0.\n"
    "Firing a gate on ordinary work is a false alarm that costs the agent "
    "attention, so silence is the correct default. These lines are "
    "machine-parsed and stripped before delivery."
)

_PRE_ASK = (
    "Read the ask below as the four voices. Report which of them this ask "
    "belongs to, and what each would watch for.\n\nASK:\n{ask}"
)

_POST_ASK = (
    "The work below is FINISHED. Look back at it as the four voices and say "
    "what each one sees NOW that the work is done -- was it too broad, too "
    "narrow, checked enough, acted enough. Be specific about what you see, not "
    "about what you would have done.\n\nWORK:\n{work}"
)


_PERSONA_FRAME = (
    "You are projecting for a SPECIFIC agent. The same ask means different "
    "stances for different roles -- a coder editing one line and an "
    "orchestrator planning a loop need opposite dials for the same words. "
    "Weigh the voices through this agent's role and current situation, not in "
    "the abstract.\n\nAGENT: {agent}\nROLE: {role}\n"
)


def build_pre_prompt(ask: str, agent: str = "agent", role: str = "",
                     context: str = "") -> Tuple[str, List[Dict[str, str]]]:
    """Return (system, messages) for a PRE consult.

    `agent`/`role`/`context` condition the projection on WHO is working: the
    dials are a function of (ask, persona), not of the ask alone.
    """
    joined = "\n\n".join(p.disposition for p in POV_SET)
    system = (
        "You are a projection made of four fixed voices. They are prototypes at "
        "the corners of a space, not personalities to choose between -- several "
        "can claim one ask.\n\n"
        + _PERSONA_FRAME.format(agent=agent, role=role or "unspecified")
        + "\n" + joined
    )
    body = _PRE_ASK.format(ask=ask.strip())
    if context.strip():
        body += "\n\nSITUATION SO FAR:\n" + context.strip()[:1200]
    return system, [{"role": "user", "content": body + _CLAIM_INSTRUCTION}]


def build_post_prompt(work: str, agent: str = "agent", role: str = "",
                      context: str = "") -> Tuple[str, List[Dict[str, str]]]:
    system, _ = build_pre_prompt("", agent=agent, role=role, context=context)
    return system, [{"role": "user", "content": _POST_ASK.format(work=work.strip()) + _CLAIM_INSTRUCTION}]


# ---------------------------------------------------------------------------
# POST: retroactive delta with direction labels
# ---------------------------------------------------------------------------
#
# POST runs the same four voices over the FINISHED work. Their claims give the
# stance the work ACTUALLY exhibited. Differencing against the PRE target gives
# a signed correction in words the agent can act on before it ships:
#
#   observed - target < 0 on rigour -> "should have been more careful"
#   observed - target > 0 on rigour -> "should have been less careful"
#   observed - target < 0 on scope  -> "should have been broader"
#   observed - target > 0 on scope  -> "should have been narrower"
#
# POST is emitted BEFORE the final response so the main agent can still change
# the delivery. A delta after delivery is a post-mortem, not a correction.

_DELTA_LABEL = {
    ("scope", -1): "should have been broader -- the work stopped short of the surface this needed",
    ("scope", +1): "should have been narrower -- the work spread past what the ask covered",
    ("rigour", -1): "should have been more careful -- claims shipped that nothing pinned down",
    ("rigour", +1): "should have been less careful -- time spent re-checking what was settled",
}

# a delta inside this band is not worth a sentence; below it the two readings
# agree and reporting a direction would manufacture a correction
DELTA_BAND = 0.35


def delta_labels(target: Mapping[str, Optional[float]],
                 observed: Mapping[str, Optional[float]],
                 band: float = DELTA_BAND) -> Dict[str, float]:
    """Signed correction per drive. Only drives with BOTH readings are scored."""
    out: Dict[str, float] = {}
    for d in ("scope", "rigour"):
        t, o = target.get(d), observed.get(d)
        if t is None or o is None:
            continue
        delta = float(o) - float(t)
        if abs(delta) > band:
            out[d] = round(delta, 3)
    return out


def reflection_lines(target: Mapping[str, Optional[float]],
                     observed: Mapping[str, Optional[float]],
                     band: float = DELTA_BAND) -> List[str]:
    """POST self-reflection, in words, before the final response."""
    lines: List[str] = []
    for d, v in delta_labels(target, observed, band).items():
        key = (d, 1 if v > 0 else -1)
        if key in _DELTA_LABEL:
            lines.append(_DELTA_LABEL[key])
    return lines


def post_view(verdict: Mapping[str, object]) -> Dict[str, object]:
    """Bundle a POST projection into the shape the caller needs.

    Returns target (if given), observed dials, signed delta, and the prose the
    agent reads -- with the note from whichever voice is furthest from the
    target on each drive, so the correction carries a reason.
    """
    claims = verdict.get("claims") or {}
    notes = verdict.get("notes") or {}
    gates = verdict.get("gates") or {}
    observed = dials_from_claims(claims)  # type: ignore[arg-type]
    return {"observed": observed, "notes": notes, "gates": gates,
            "claims": claims, "win": winning_voice(claims)}  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Parsing + dial derivation
# ---------------------------------------------------------------------------


def parse_claims(text: Optional[str]) -> Dict[str, object]:
    """Extract per-voice claims, notes and gate flags from a projection.

    Never raises. Absent/unparseable output returns empty claims, which the
    caller must treat as 'no target' rather than as a zero vector.
    """
    out: Dict[str, object] = {"claims": {}, "notes": {}, "gates": {}}
    if not text:
        return out
    claims: Dict[str, float] = out["claims"]  # type: ignore[assignment]
    notes: Dict[str, str] = out["notes"]  # type: ignore[assignment]
    gates: Dict[str, bool] = out["gates"]  # type: ignore[assignment]
    for raw in text.splitlines():
        line = raw.strip().strip("*` ")
        m = _CLAIM_LINE.match(line)
        if m:
            name = m.group(1).lower()
            try:
                w = float(m.group(2))
            except ValueError:
                continue
            claims[name] = max(0.0, min(1.0, w))
            note = (m.group(3) or "").strip()
            if note:
                notes[name] = note
            continue
        if line.lower().startswith("gates:"):
            for g in GATES:
                if re.search(rf"\b{g}\s*=\s*1\b", line, re.IGNORECASE):
                    gates[g] = True
    return out


def dials_from_claims(claims: Mapping[str, float]) -> Dict[str, Optional[float]]:
    """Deterministic: the dials ARE the weighted dispositions.

    scope  = sum(weight * voice.scope_dir)  / sum(weight)
    rigour = sum(weight * voice.rigour_dir) / sum(weight)

    No free parameter, no fitted formula. If the voices claim the ask equally,
    the dials land at 0 (balanced) by construction rather than by luck.
    A total weight of 0 means nothing claimed it -- return None, never 0.0,
    because 'nobody spoke' must not read as 'perfectly balanced'.
    """
    total = sum(max(0.0, float(claims.get(v, 0.0))) for v in VOICES)
    if total <= 0.0:
        return {"scope": None, "rigour": None}
    scope = sum(float(claims.get(v, 0.0)) * _BY_NAME[v].scope_dir for v in VOICES) / total
    rigour = sum(float(claims.get(v, 0.0)) * _BY_NAME[v].rigour_dir for v in VOICES) / total
    return {"scope": round(scope, 3), "rigour": round(rigour, 3)}


def winning_voice(claims: Mapping[str, float]) -> Optional[str]:
    """The voice whose CORNER the resulting dials occupy. None if unclaimed.

    Deliberately derived from the dials, not from argmax of raw claims. Argmax
    and the weighted mean can disagree -- a single loud voice plus two moderate
    opposite ones yields a mean in a different quadrant -- and that produced a
    projection whose stated winner contradicted its own dials. The dial is what
    the agent acts on, so the named voice must be the one that owns that dial.
    """
    dials = dials_from_claims(claims)
    scope, rigour = dials.get("scope"), dials.get("rigour")
    if scope is None or rigour is None:
        return None
    for p in POV_SET:
        if p.scope_dir == (1 if scope > 0 else -1) and p.rigour_dir == (1 if rigour > 0 else -1):
            return p.name
    return None


def strongest_claim(claims: Mapping[str, float]) -> Optional[str]:
    """Diagnostic only: which voice pushed hardest, regardless of where the mean landed."""
    best, best_w = None, 0.0
    for v in VOICES:
        w = float(claims.get(v, 0.0))
        if w > best_w:
            best, best_w = v, w
    return best


def corner_of(scope: Optional[float], rigour: Optional[float]) -> Optional[str]:
    """Which quadrant a dial pair occupies -- for coverage tests."""
    if scope is None or rigour is None:
        return None
    return ("wide" if scope > 0 else "narrow") + "+" + ("rigorous" if rigour > 0 else "relaxed")


def stance_lines(dials: Mapping[str, Optional[float]], band: float = 0.5,
                 notes: Optional[Mapping[str, str]] = None) -> List[str]:
    """PROSE outward: at most one line per dial, only when out of band.

    The line carries the winning voice's own words where available, so the
    agent receives a perspective rather than a number.
    """
    out: List[str] = []
    notes = notes or {}
    win = winning_voice(dials.get("_claims") or {}) if "_claims" in dials else None  # type: ignore[arg-type]
    for drive, hi, lo in (
        ("scope", "widen - check callers, configs and docs, not just the target",
                  "narrow - one target, ignore what this does not touch"),
        ("rigour", "verify each claim against a tool result before asserting it",
                   "the framing as given is enough; stop re-checking"),
    ):
        v = dials.get(drive)
        if v is None or abs(float(v)) <= band:
            continue
        text = hi if float(v) > 0 else lo
        if win and notes.get(win):
            text = f"{text} ({win}: {notes[win]})"
        out.append(f"{drive}: {text}")
        if len(out) >= 2:
            break
    return out


def gate_lines(gates: Mapping[str, bool], phase: str = "pre") -> List[str]:
    """Gates emit a question only when they fired. Silence otherwise."""
    out = []
    for g in GATES:
        if gates.get(g):
            out.append(GATE_TEXT[g][phase])
    return out


def block_text(text: Optional[str]) -> str:
    """Strip the machine-parsed claim lines from a projection before delivery."""
    if not text:
        return ""
    kept = []
    for raw in text.splitlines():
        line = raw.strip().strip("*` ")
        if _CLAIM_LINE.match(line) or line.lower().startswith("gates:"):
            continue
        kept.append(raw)
    return "\n".join(kept).strip()


def coverage() -> Dict[str, str]:
    """Every corner must have exactly one owner, or a direction is unreachable."""
    out: Dict[str, str] = {}
    for p in POV_SET:
        key = ("wide" if p.scope_dir > 0 else "narrow") + "+" + (
            "rigorous" if p.rigour_dir > 0 else "relaxed")
        out[key] = p.name
    return out
