"""Decision extraction — proven design, folded into the lane.

WHAT WAS MEASURED (499 real failure points, 11 agents, 3 disjoint samples, ground truth = the
file the agent actually edited next):

  choice distribution over candidates   top1 0.764 pooled (0.700 / 0.797 / 0.767), Brier 0.078-0.103
  per-candidate noul statements          top1 0.655-0.759, Brier 0.121-0.137  (worse calibrated)
  most-mentioned-path heuristic          top1 0.390
  last-mentioned-path heuristic           top1 0.487
  chance (1/k)                           0.204-0.211

So: a `choice` distribution beats independent nouls on every metric, and both beat every
trivial heuristic by a wide margin. The weights are also informative about their own
reliability — mean top weight was 0.774 when the pick was right and 0.549 when it was wrong,
which is a usable gate.

Consequence for the block handed to the main model: it carries a distribution that sums to 1,
because that is the shape that was actually calibrated, plus scope/rigour deltas in the same
call. Not a list of independent percentages: those read as "everything is 80%" in practice,
which is worse than no reading at all.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

MOVES: List[Tuple[str, str, str]] = [
    # (move id, class, label) — the class is what the answer is scored against
    ("inspect", "read", "inspect the evidence more closely before changing anything"),
    ("edit_minimal", "edit", "make the minimal targeted edit at the point of failure"),
    ("edit_restructure", "edit", "restructure the surrounding code, not just the failing line"),
    ("run_check", "run", "run the check or tests now and read the result"),
    ("search", "search", "search the tree for the other sites this affects"),
    ("query_state", "run", "query the live state (service, process, database) to settle it"),
    ("stop_report", "none", "stop and report; the next step needs a decision"),
]

PATH_RE = re.compile(r"(?:/opt|/tmp|/home|/root|/var|/etc|/srv|/run)[\w./\-]{3,90}"
                     r"|(?:^|[\s\"'(=])((?:[\w.\-]+/){1,7}[\w.\-]+\.(?:py|js|ts|tsx|go|rs|ya?ml|"
                     r"json|md|sh|toml|sql|css|html|txt|cfg|ini))")

SCOPE_LEVELS = ["one file only", "one file plus its immediate caller",
                "a few files inside one module", "several modules must be cross-read",
                "the whole tree has to be mapped before acting"]
RIGOUR_LEVELS = ["none, it is obviously safe", "a glance at the result",
                 "run the local check once", "verify each step before committing",
                 "verify every step against independent evidence"]


def salient_paths(text: str, cap: int = 4) -> List[str]:
    """Paths the evidence names, most recent first. Selection only — no plausibility filter."""
    got: List[str] = []
    for m in PATH_RE.finditer(text or ""):
        # trailing sentence punctuation is part of the sentence, not of the path; leaving it in
        # made every target end in a dot and scored perfect hits as misses
        p = (m.group(1) or m.group(0)).strip().strip("\"'()").rstrip(".,;:!?)]}")
        if len(p) < 5:
            continue
        if p not in got:
            got.append(p)
        if len(got) >= cap:
            break
    return got


def candidate_set(window: str, cap: int = 7) -> List[Dict[str, str]]:
    """Target-bearing actions first (the object of the next move), then the move classes.

    The proof used file-choice among visible paths and scored 0.764 top-1; the generic classes
    ride alongside so the block still says WHICH KIND of move when no file is in play.
    """
    out: List[Dict[str, str]] = []
    seen = set()
    paths = salient_paths(window, cap=2)
    if not paths:
        # No named object means nothing to choose between — the block would be generic advice,
        # and only object-bearing candidates were ever measured as accurate. Stay silent.
        return []
    for p in paths:
        base = os.path.basename(p.rstrip("/")) or p
        for mv, cls, _lab in (("fix", "edit", ""), ("inspect", "read", "")):
            label = ("change %s" if mv == "fix" else "read %s first") % base
            if label in seen:
                continue
            seen.add(label)
            out.append({"move": mv, "kind": cls, "target": p, "label": label})
    for mv, cls, lab in MOVES:
        if lab in seen or len(out) >= cap:
            continue
        if not any(o["kind"] == cls for o in out) or mv in ("run_check", "search"):
            seen.add(lab)
            out.append({"move": mv, "kind": cls, "target": "", "label": lab})
    # A block must carry at least one verification move: options that never include "check it"
    # teach the main model to act without verifying.
    if not any(o["kind"] == "run" for o in out):
        out.append({"move": "run_check", "kind": "run", "target": "",
                    "label": "run the check or tests now and read the result"})
    return out[:cap] if len(out) >= 3 else []


def payload(state: str, cands: List[Dict[str, str]], model: str = "jev-latest") -> Dict[str, Any]:
    return {
        "model": model,
        "state": state[:14000],
        "questions": {
            "action": {
                "type": "choice",
                "instructions": ("Which single move does a competent agent actually take next "
                                 "given the evidence above? Weigh by what the evidence shows, "
                                 "not by which option sounds safest or most central."),
                "criteria": {("a%d" % i): c["label"] for i, c in enumerate(cands, start=1)},
            },
            "scope": {"type": "score", "criteria": SCOPE_LEVELS,
                      "instructions": "How much surface must be examined to get this right?"},
            "rigour": {"type": "score", "criteria": RIGOUR_LEVELS,
                       "instructions": "How much checking must happen before acting on it?"},
        },
    }


def parse(data: Any, cands: List[Dict[str, str]]) -> Optional[Dict[str, Any]]:
    try:
        ans = (data or {}).get("answers") or {}
        act = ans.get("action") or {}
        probs = act.get("probabilities") or {}
        opts: List[Dict[str, Any]] = []
        for i, c in enumerate(cands, start=1):
            try:
                p = float(probs.get("a%d" % i))
            except (TypeError, ValueError):
                continue
            opts.append({"p": round(p, 4), "move": c["move"], "kind": c["kind"],
                         "target": c.get("target", ""), "label": c["label"]})
        if len(opts) < 2:
            return None
        opts.sort(key=lambda o: -o["p"])
        total = sum(o["p"] for o in opts)
        if total <= 0:
            return None
        if abs(total - 1.0) > 0.05:
            opts = [dict(o, p=round(o["p"] / total, 4)) for o in opts]
        mind: Dict[str, float] = {}
        for d in ("scope", "rigour"):
            try:
                s = float((ans.get(d) or {}).get("score"))
            except (TypeError, ValueError):
                continue
            mind[d] = round(max(-1.0, min(1.0, (s - 2.0) / 2.0)), 3)
        return {"options": opts, "mindset": mind,
                "confidence": round(float(act.get("confidence") or opts[0]["p"]), 3),
                "margin": round(float(opts[0]["p"]) - float(opts[1]["p"]), 3)}
    except Exception:  # noqa: BLE001
        return None


# One envelope for every decision call, the way the frontier and uncensored lanes each
# carry exactly one marker. The register (board, prose, mindset) is a FIELD inside the
# envelope, not a different envelope - four different headers for one lane meant the
# contract that teaches the model how to treat a reading could be read as four things.
DECISION_MARKER = "[DECISION READING | DECISION-LANE | PROVENANCE-LOGGED]"
HEAD_FRAME = DECISION_MARKER

# Above this gap the reading is a move to take; below it the two top moves are close
# enough that committing without checking is the mistake. Measured separation: correct
# picks carried a mean top weight of 0.774, wrong ones 0.549 - so a real lead is the
# signal, and a flat distribution is the lane's own noise band.
DECISIVE_MARGIN = 0.20

# One word per direction, so a dial arrives as a thing to do rather than a number to
# interpret. Same vocabulary as mindset._STANCE, compressed to fit an every-call block.
_DIAL_WORD = {("scope", 1): "widen", ("scope", -1): "narrow",
              ("rigour", 1): "verify", ("rigour", -1): "settle"}

# The contract. Measured: 23% of readings that cleared the gate were wrong, so the block
# must license deviation explicitly - otherwise it is an instruction dressed as a reading,
# and a wrong instruction is worse than silence.
USE_CONTRACT = ("  act: take the top unless the evidence in front of you says otherwise; "
                "if you deviate, name why in one line")


def basis_line(tool: str, text: str, limit: int = 92) -> str:
    """Provenance: what the reading was derived from. One line, never invented."""
    try:
        head = " ".join(str(text or "").split())[:limit]
        if not head:
            return ""
        if tool:
            return "  from: %s -> %s" % (str(tool)[:24], head)
        return "  from: %s" % head
    except Exception:  # noqa: BLE001
        return ""


def render(parsed: Dict[str, Any], limit: int = 560, basis: str = "") -> str:
    """The block. A distribution, because that is the shape that measured as calibrated.

    Four parts, in the order the main model needs them: what this is, what it was read
    from, the weighted moves, the dials, and how to act on it. Same shape as the frontier
    and uncensored frames - identity, provenance, payload, contract.
    """
    try:
        opts = parsed.get("options") or []
        if len(opts) < 2:
            return ""
        reg = "  register: board"
        lines = [HEAD_FRAME, reg]
        if basis:
            lines.append(str(basis)[:120])
        # R72: the block says how decided the reading is, because the two regimes want
        # different behaviour. A decisive read is a move to take; a near-tie is a warning
        # to verify before committing. A flat menu of three percentages asks the model to
        # re-deliberate - which is exactly the deliberation a reflex exists to replace.
        p_top = float(opts[0]["p"])
        # margin = lead of the top move over the runner-up. Prefer the value the parser
        # recorded so the block and the gate agree on one number.
        margin = parsed.get("margin")
        margin = float(margin) if isinstance(margin, (int, float)) else p_top - float(opts[1]["p"])
        if margin >= DECISIVE_MARGIN:
            lines.append("  %2d%%  %s   <- take this" % (round(p_top * 100),
                                                         str(opts[0]["label"])[:58]))
            for o in opts[1:3]:
                lines.append("  %2d%%  %s" % (round(float(o["p"]) * 100), str(o["label"])[:60]))
        else:
            for o in opts[:3]:
                lines.append("  %2d%%  %s" % (round(float(o["p"]) * 100), str(o["label"])[:60]))
            lines.append("  <- too close to call: check before you commit")
        mind = parsed.get("mindset") or {}
        bits = []
        for d in ("scope", "rigour"):
            v = mind.get(d)
            if not isinstance(v, float):
                continue
            if abs(v) < 0.15:
                bits.append("%s steady" % d)
                continue
            bits.append("%s %s (%+0.2f)" % (d, _DIAL_WORD.get((d, 1 if v > 0 else -1), "shift"), v))
        if bits:
            lines.append("  dials: " + " | ".join(bits))
        lines.append(USE_CONTRACT)
        # Over budget: shed optional parts in order of least behavioural value. The
        # contract is the part that changes behaviour, so it goes last; provenance and the
        # register line are audit furniture. Indices are looked up by identity, not by
        # position - an earlier version assumed a fixed layout and returned an EMPTY block
        # once another line was inserted, which silently muted the lane.
        optional = [reg] + ([str(basis)[:120]] if basis else []) + ([lines[-2]] if "dials: " in lines[-2] else [])
        keep = list(lines)
        if len("\n".join(keep)) <= limit:
            return "\n".join(keep)
        for drop in optional:
            keep = [ln for ln in keep if ln != drop]
            if len("\n".join(keep)) <= limit:
                return "\n".join(keep)
        return "" if len("\n".join(keep)) > limit else "\n".join(keep)
    except Exception:  # noqa: BLE001
        return ""


INSTINCT_TAG = DECISION_MARKER


def render_instinct(parsed: Dict[str, Any], limit: int = 460, basis: str = "") -> str:
    """The same reading, in the register of a hunch rather than a dashboard.

    A menu of three percentages invites the model to deliberate - which is the deliberation
    a reflex exists to replace. An instinct is one assertion, the competing pull named, and
    the two dispositions to hold while acting on it. Percentages stay, because a hunch that
    cannot be audited is not a reading, but they ride inside a sentence instead of a table.
    """
    try:
        opts = parsed.get("options") or []
        if len(opts) < 2:
            return ""
        top, second = opts[0], opts[1]
        p_top = float(top["p"])
        margin = parsed.get("margin")
        margin = float(margin) if isinstance(margin, (int, float)) else p_top - float(second["p"])
        lead = str(top["label"])[:64]
        alt = str(second["label"])[:64]
        if margin >= DECISIVE_MARGIN:
            head = ("%s instinct: %s - clear read (%d%%)."
                    % (INSTINCT_TAG, lead, round(p_top * 100)))
        else:
            head = ("%s instinct: %s, but only just (%d%%) - %s is nearly as likely (%d%%). "
                    "Check before committing." % (INSTINCT_TAG, lead, round(p_top * 100),
                                                  alt, round(float(second["p"]) * 100)))
        hold = _dial_sentence(parsed.get("mindset") or {})
        body = head + ((" " + hold) if hold else "")
        if basis:
            body += "\n" + str(basis)[:110]
        return body if len(body) <= limit else (head + ((" " + hold) if hold else ""))[:limit]
    except Exception:  # noqa: BLE001
        return ""


def _dial_sentence(mind: Dict[str, Any]) -> str:
    """Dials as one clause of posture, not a numeric row."""
    words = []
    for d in ("scope", "rigour"):
        v = mind.get(d)
        if not isinstance(v, float):
            continue
        if abs(v) < 0.15:
            words.append("%s unchanged" % d)
        else:
            words.append("%s %s" % (d, _DIAL_WORD.get((d, 1 if v > 0 else -1), "shift")))
    return ("hold " + ", ".join(words) + ".") if words else ""


def gate(parsed: Dict[str, Any], min_top: float = 0.45, min_margin: float = 0.10) -> Tuple[bool, str]:
    """Speak only when the reading is decisive enough to be worth the main model's attention.

    min_top 0.45 / min_margin 0.10 come from the measured separation: correct picks carried a
    mean top weight of 0.774, wrong ones 0.549. A flat or barely-separated distribution is the
    lane's noise band and must stay silent rather than steer.
    """
    opts = parsed.get("options") or []
    if len(parsed.get("options") or []) < 2:
        return False, "no_option_set"
    if float(opts[0]["p"]) < min_top:
        return False, "top_below_floor"
    if float(opts[0]["p"]) - float(opts[1]["p"]) < min_margin:
        return False, "no_separation"
    if opts[0]["move"] == "stop_report":
        return False, "stop_report"
    return True, "ok"
