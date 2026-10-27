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


_ERR_WORDS = ("traceback", "error", "exception", "failed", "failure", "assert")


# Names that are tools, not places. `python3` has no dot, so the old is_dir heuristic
# ("no dot => directory") rendered "work inside python3" at 3% on a live turn.
TOOL_NAMES = frozenset((
    "python", "python3", "node", "npm", "npx", "git", "bash", "sh", "zsh", "pytest", "go",
    "make", "cargo", "pip", "pip3", "curl", "sed", "awk", "grep", "rg", "docker", "s6-svc",
))


def _is_fragment(text: str, end: int, p: str) -> bool:
    """A match that stops at a template placeholder is half a path, not an object.

    PATH_RE stops at `<`, so `/run/service/gateway-<agent>` yields `/run/service/gateway-`, whose
    basename has no dot and therefore rendered "look inside gateway-" at 26% on a live turn
    (340 fragment mentions per 3000 turns on orchestrator, 5-11% of all path mentions fleet-wide).
    """
    nxt = text[end:end + 1]
    if nxt and nxt in "<>*{}$%":
        return True
    return p.rstrip("/").endswith(("/", "-", "_", "."))


def salient_paths(text: str, cap: int = 4, ask: str = "") -> List[str]:
    """Paths the evidence names, ranked by how likely the next move is about them.

    Recency alone was measured too weak: recall fell from 0.50 (window naming one object) to
    0.24 (five or more), because the relevant object is often named early - in the command
    that produced the result, or in the traceback - while the last line mentions a log file.
    Selection only: no plausibility filter, just a better order.
    """
    text = text or ""
    hits: Dict[str, Dict[str, Any]] = {}
    for m in PATH_RE.finditer(text):
        # trailing sentence punctuation is part of the sentence, not of the path; leaving it in
        # made every target end in a dot and scored perfect hits as misses
        p = (m.group(1) or m.group(0)).strip().strip("\"'()").rstrip(".,;:!?)]}")
        if len(p) < 5:
            continue
        if _is_fragment(text, m.end(), p):
            continue
        if os.path.basename(p.rstrip("/")).lower() in TOOL_NAMES:
            continue
        rec = hits.setdefault(p, {"n": 0, "pos": m.start(), "err": False,
                                  "raising": False})
        rec["n"] += 1
        rec["pos"] = max(rec["pos"], m.start())
        window = text[max(0, m.start() - 160):m.start() + 160].lower()
        if any(w in window for w in _ERR_WORDS):
            rec["err"] = True
        # the file that RAISED is a likelier edit target than one merely named in the error
        # message: a traceback's "File ..." line and its "cannot import from ..." mention are
        # not equally good evidence
        if "file " in text[max(0, m.start() - 24):m.start()].lower():
            rec["raising"] = True
    if not hits:
        return []
    tail = len(text) - 400
    # words the ask actually used - matched against candidate stems, never as a substring of
    # the whole ask (a 4-char stem inside "production" is not relevance)
    _ask_terms = set(re.findall(r"[a-z0-9_]{4,}", (ask or "").lower()))
    def score(item):
        p, r = item
        s = r["n"] * 2.0
        if r["err"]:
            s += 3.0
        if r.get("raising"):
            s += 2.0
        if r["pos"] >= tail:
            s += 2.0
        if "." in os.path.basename(p.rstrip("/")):
            s += 1.0
        # ASK RELEVANCE (R73 P3): the ask used to be concatenated into the window and then
        # scored as if it were scenery, so a file printed by an earlier listing outranked the
        # object the user actually named ("why is valmet flagging frontier as injection" ->
        # "read orchestration.py first"). The ask is scored as its own term, and it is strong
        # enough to beat mention-count scenery.
        if _ask_terms:
            stem = os.path.splitext(os.path.basename(p.rstrip("/")))[0].lower()
            if len(stem) >= 4 and stem in _ask_terms:
                s += 6.0
        return (-s, -r["pos"])
    return [p for p, _r in sorted(hits.items(), key=score)[:cap]]


def candidate_set(window: str, cap: int = 7, ask: str = "") -> List[Dict[str, str]]:
    """Target-bearing actions first (the object of the next move), then the move classes.

    The proof used file-choice among visible paths and scored 0.764 top-1; the generic classes
    ride alongside so the block still says WHICH KIND of move when no file is in play.
    """
    out: List[Dict[str, str]] = []
    seen = set()
    # Two was measured too narrow: the move an agent actually makes next was in the set only
    # 28-34% of the time across three profiles' real transcripts, which makes a confident
    # reading confidently wrong. Five paths covers the realistic working set without turning
    # the block into a menu of the whole tree.
    paths = salient_paths(window, cap=5, ask=ask)
    if not paths:
        # No named object means nothing to choose between — the block would be generic advice,
        # and only object-bearing candidates were ever measured as accurate. Stay silent.
        return []
    # Drop paths that are a truncated prefix of another (a window cut mid-path produced
    # "HalfWidthPa" next to "HalfWidthPanel.tsx" - two options for one file, one of them
    # unactionable, both eating block space).
    filtered = []
    for p in paths:
        if any(q != p and q.startswith(p) for q in paths):
            continue
        filtered.append(p)
    paths = filtered or paths
    bases = [os.path.basename(p.rstrip("/")) or p for p in paths]
    for p in paths:
        base = os.path.basename(p.rstrip("/")) or p
        # a bare basename is ambiguous the moment two files share one - keep the parent
        # directory only when it actually disambiguates, because block space is scarce
        if bases.count(base) > 1:
            base = "/".join(p.rstrip("/").split("/")[-2:])
        is_dir = "." not in base
        for mv, cls, _lab in (("fix", "edit", ""), ("inspect", "read", "")):
            if is_dir:
                # "read tmp first" says nothing: a directory is a place to look, not an
                # object to act on. Measured live leading the board at 60%.
                label = ("work inside %s" if mv == "fix" else "look inside %s") % base
            else:
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
    # A body must be able to say "none of these": forcing a choice among offered moves is how
    # a reading becomes confidently wrong when the real next move is not in the set. Measured
    # recall is 0.5 at best (window naming one object) and ~0.3 when it names five or more, so
    # the escape route is not decoration - it is the honest option most of the time.
    if len(paths) >= 3 and len(out) < cap:
        lab = "take a route none of these name"
        seen.add(lab)
        out.append({"move": "elsewhere", "kind": "none", "target": "", "label": lab})
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
USE_CONTRACT = ("  reading, not an instruction: the top move is a suggestion and deviation is "
                "licensed - say in one line why you went elsewhere")

# A soft reading says "no clear lead" and then, in the same block, told the model to take the
# top - the two instructions contradicted each other. What the dials are for on a flat board is
# the stance, not a pick.
SOFT_CONTRACT = ("  reading, not an instruction: no move to take here - the dials describe how much "
                 "surface this step needs and how hard it needs checking; the options are context")


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


def render(parsed: Dict[str, Any], limit: int = 560, basis: str = "",
           soft: bool = False) -> str:
    """The block. A distribution, because that is the shape that measured as calibrated.

    Four parts, in the order the main model needs them: what this is, what it was read
    from, the weighted moves, the dials, and how to act on it. Same shape as the frontier
    and uncensored frames - identity, provenance, payload, contract.
    """
    try:
        opts = parsed.get("options") or []
        # A soft reading is what the lane sends when nothing separates: it still carries the
        # dials, because scope/rigour are useful on every midturn even when no single move
        # leads. Silence there was the lane "giving way" on the turns it had least to say -
        # which is also when the model most needed the dials.
        if len(opts) < (1 if soft else 2):
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
        # "take this" is a claim about an OBJECT. A generic class move ("run the check") can
        # lead the distribution without naming anything, and marking that as decisive told the
        # model to commit to a move that says nothing about what to commit to.
        _t = str(opts[0].get("target") or "").strip().rstrip("/")
        # a directory is a place, not an object: "take this" over one tells the model to commit
        # to a location, which is not a decision it can act on
        has_target = bool(_t) and "." in os.path.basename(_t)
        if margin >= DECISIVE_MARGIN and has_target and not soft:
            lines.append("  %2d%%  %s   <- take this" % (round(p_top * 100),
                                                         str(opts[0]["label"])[:58]))
            for o in opts[1:3]:
                lines.append("  %2d%%  %s" % (round(float(o["p"]) * 100), str(o["label"])[:60]))
        else:
            for o in opts[:3]:
                lines.append("  %2d%%  %s" % (round(float(o["p"]) * 100), str(o["label"])[:60]))
            if soft:
                lines.append("  <- no clear lead: the dials above still apply; the board is "
                             "context to weigh, not the move to take")
            else:
                lines.append("  <- class-level only: no single object leads, so pick from the "
                             "evidence" if not has_target else
                             "  <- too close to call: check before you commit")
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
        lines.append(SOFT_CONTRACT if soft else USE_CONTRACT)
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
        # Reaching here means the block is STILL over budget: `optional` always contains
        # `reg` (the list literal above leads with it), so the loop above ran at least once
        # and any iteration that brought the block under budget already returned. The old
        # tail was `"" if len(...) > limit else "\n".join(keep)`; its second arm could never
        # fire (R73 inventory: dominant by construction, 639/639 traced executions returned
        # ""). Shedding the loop is live - only the unreachable arm is gone.
        return ""
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
