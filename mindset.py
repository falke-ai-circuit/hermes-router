"""R52 — MINDSET MODULATION: two usable drives, read from the trace.

Measured basis (R47/R48, 3052 sessions, 135 paired PRE/trace sessions):

    scope    PRE predicts behaviour  r=+0.291  shuffle p=0%   USABLE
    rigour   PRE predicts behaviour  r=+0.232  shuffle p=1%   USABLE
    resolve  PRE does NOT            r=+0.013  shuffle p=91%  DROP
    tempo    PRE does NOT            r=+0.010  shuffle p=94%  DROP

So this module carries exactly two drives. A drive that does not predict
behaviour is noise in the prompt, and noise in the prompt is worse than
silence: the agent spends attention on a stance it cannot act on.

TWO ARTIFACT FIXES ARE LOAD-BEARING HERE (found by the R50 artifact audit):

1. ABSENCE OF SIGNAL MAPS TO UNKNOWN, NEVER TO THE FLOOR. In trace_reader
   v1 a trace with no parseable paths scored scope=0, was calibrated through
   the percentile map, and landed at -1.0 — "the trace told us nothing" was
   reported as "maximum narrowness". That single coercion produced a uniform
   negative delta for every agent. Here: unreadable -> None. None is never
   imputed, never defaulted, never floored. n shrinks honestly.
   A history with no variance is uncalibratable for the same reason -> None.

2. TARGET AND USED ARE Z-SCORED TO A COMMON SCALE BEFORE THE DELTA. The
   earlier artifact was an absolute target vector subtracted from a
   percentile-calibrated used vector: two different scales, so the delta was
   a scale difference masquerading as behaviour — a fake uniform -0.6 for all
   11 agents. Here BOTH sides are z-scored against the SAME reference
   distribution (the profile's own recent raw scores, in the same raw units:
   distinct paths touched, reads+checks performed), so the delta is in
   standard deviations of that profile's own behaviour and 0 is its centre.

   The percentile map (calibrate) has a second job and a limit worth stating:
   it answers "is this drive readable at all", with 0 = median. It saturates
   by construction at the 10th/90th, so two saturated values would subtract
   to a fake zero — which is why delta_vector takes the RAW values plus the
   reference distributions rather than the saturated percentiles. The caller
   uses the calibrated readers to decide readability (None), then hands the
   raw values to the delta.

Pure functions only: no I/O, no config reads, no network, no state. The
caller owns the fetches (the trace, and the profile's history). That keeps
every pin in this unit testable with plain literals.

The projection side is STRUCTURED, NOT PROSE (R53). The PRE consult emits its
own {scope, rigour} target on a strict `MINDSET_TARGET {...}` line; that vector
is parsed, the line is stripped, and only floats reach projection_vector. The
earlier keyword counter over the advisory prose was deleted: those r/p figures
were measured on a numeric vector, and a regex score wearing them is a false
assurance — worse than no modulation at all.
"""
from __future__ import annotations

import re
import statistics as _st
from typing import (Any, Dict, Iterable, List, Mapping, Optional, Sequence,
                    Tuple)

# The two drives that survived the predictive test. resolve and tempo are
# deliberately absent: they are dropped drives and must never be emitted.
DRIVES: Tuple[str, ...] = ("scope", "rigour")

#: Below this many prior sessions there is no distribution to rank against,
#: so every drive is UNKNOWN (never a default, never the floor).
MIN_HISTORY = 20

#: Default out-of-band threshold on the delta, in common-scale units.
BAND = 0.5

#: Header of the delivered block. Distinct block or nothing at all.
MINDSET_HEADER = "MINDSET MODULATION"

TraceRow = Tuple[str, Optional[str], Optional[str]]

# Tool classes ported from trace_reader_v2.raw_and_readable — same names,
# same semantics, so the trace a caller feeds here scores identically to the
# research instrument that measured the drives.
READ_TOOLS = frozenset({
    "read_file", "search_files", "skill_view", "web_search", "web_extract",
    "graphiti_search", "graphiti_recall", "lcm_grep", "lcm_recall",
    "session_search", "todo", "vision_analyze",
})
WRITE_TOOLS = frozenset({
    "write_file", "patch", "skill_manage", "memory", "graphiti_add_fact",
})
_CHECK_RE = re.compile(
    r"\b(pytest|go test|npm test|suite|git (status|diff|log|show)|grep |wc -|"
    r"md5sum|verify|audit\.py|--version)", re.I)
_MUT_RE = re.compile(
    r"\b(>|>>|tee |sed -i|mv |cp |rm |mkdir|chmod|git (add|commit|push)|"
    r"systemctl|s6-svc)", re.I)
_PATH_RE = re.compile(r"[\w./-]+\.(?:py|md|go|ts|js|sh|yaml|yml|json)")

#: Stance wording. Internal stance only: how to hold the work, never what to
#: do to the artefact. No file names, no commands, no task advice — the agent
#: owns the task; the higher self owns the posture.
_STANCE = {
    ("scope", 1): "scope: widen - check callers, configs and docs, not just the target in hand",
    ("scope", -1): "scope: narrow - one target, ignore what the change does not touch",
    ("rigour", 1): "rigour: verify each claim against a tool result before asserting it",
    ("rigour", -1): "rigour: the framing as given is enough; stop re-checking",
}

MAX_LINE_CHARS = 110
MAX_LINES = 2
MAX_BLOCK_CHARS = 220


# ---------------------------------------------------------------------------
# Raw features (the port of raw_and_readable)
# ---------------------------------------------------------------------------

def raw_trace_scores(session_trace: Sequence[TraceRow]
                     ) -> Tuple[Dict[str, Optional[float]], Dict[str, bool]]:
    """(raw, readable) for the two drives.

    `session_trace` is an already-fetched list of (role, tool_name, content)
    tuples — the DB fetch stays in the caller.

    readable is the whole point: a drive is readable only when the underlying
    signal exists at all. A trace that ran zero tool calls said nothing about
    how it worked, so both drives are UNKNOWN for it.
    """
    n_tool = 0
    reads = 0
    checks = 0
    first_write_at: Optional[int] = None
    files = set()
    for row in (session_trace or ()):
        try:
            role, tool, content = (list(row) + [None, None, None])[:3]
        except Exception:  # noqa: BLE001 — a malformed row is not a crash
            continue
        content = content or ""
        if role != "tool" or not tool:
            continue
        n_tool += 1
        if tool in ("terminal", "execute_code"):
            if _MUT_RE.search(content):
                if first_write_at is None:
                    first_write_at = n_tool
            if _CHECK_RE.search(content):
                checks += 1
        elif tool in WRITE_TOOLS:
            if first_write_at is None:
                first_write_at = n_tool
        elif tool in READ_TOOLS and first_write_at is None:
            # v2 semantics: reads counted before the first write — after the
            # work starts, reads are no longer orientation.
            reads += 1
        for m in _PATH_RE.findall(content):
            if len(m) < 80:
                files.add(m)
    raw: Dict[str, Optional[float]] = {
        "scope": float(len(files)),
        "rigour": float(reads + checks),
    }
    readable = {"scope": n_tool > 0, "rigour": n_tool > 0}
    return raw, readable


#: The PRE consult emits its stance target as a strict final line. Structured
#: floats only — the drive model was measured on a {scope, rigour} vector, and
#: a keyword count over prose does NOT inherit those numbers (R53).
MINDSET_TARGET_TAG = "MINDSET_TARGET"


def _target_floats(text: str) -> Optional[Dict[str, float]]:
    """Parse the braces of a MINDSET_TARGET line; None when not strict.

    Numbers only: every value must be a bare numeric literal in [-1, 1] and
    the keys must be real drives. Anything else -> None (fail-open upstream).
    """
    import json as _json

    body = str(text or "").strip()
    if not (body.startswith("{") and body.endswith("}")):
        return None
    try:
        data = _json.loads(body)
    except Exception:  # noqa: BLE001 — unparseable is a miss, not a crash
        return None
    if not isinstance(data, dict) or not data:
        return None
    out: Dict[str, float] = {}
    for k, v in data.items():
        if k not in DRIVES:
            return None
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        fv = float(v)
        if fv < -1.0 or fv > 1.0:
            return None
        out[str(k)] = fv
    return out


def split_target_block(text: Optional[str]
                       ) -> Tuple[Optional[Dict[str, float]], str]:
    """(target, cleaned_text) — parse AND strip the MINDSET_TARGET line.

    One helper on purpose: "the block never reaches the caller or the stored
    body" is then structurally true instead of true by two independent edits.
    The line is removed wherever it sits (last line is the contract, a stray
    earlier line is still not allowed to leak). Missing / unparseable -> the
    text comes back UNCHANGED and target is None. Never raises.
    """
    raw = str(text or "")
    if not raw:
        return None, raw
    target: Optional[Dict[str, float]] = None
    kept: List[str] = []
    saw_tag = False
    try:
        for line in raw.splitlines():
            stripped = line.strip()
            if stripped.startswith(MINDSET_TARGET_TAG):
                # The tag is machine channel: it is dropped from the delivered
                # text even when its payload does not parse (a malformed line
                # must never reach a human as if it meant something).
                saw_tag = True
                parsed = _target_floats(
                    stripped[len(MINDSET_TARGET_TAG):].strip())
                if parsed is not None and target is None:
                    target = parsed
                continue
            kept.append(line)
        if not saw_tag:
            return None, raw
        return target, "\n".join(kept).rstrip()
    except Exception:  # noqa: BLE001 — never raise on the advisory path
        return None, raw


# ---------------------------------------------------------------------------
# Calibration — percentile rank inside the profile's own recent history
# ---------------------------------------------------------------------------

def _history_values(history: Any, drive: str) -> List[float]:
    if not isinstance(history, dict):
        return []
    vals = history.get(drive)
    if not isinstance(vals, (list, tuple)):
        return []
    return [float(v) for v in vals if isinstance(v, (int, float))
            and not isinstance(v, bool)]


def calibrate(drive: str, value: Optional[float], history: Any
              ) -> Optional[float]:
    """Percentile rank of `value` in the profile's own recent raw scores.

    0 = median, -1 = at/below the 10th percentile, +1 = at/above the 90th.
    Returns None — UNKNOWN — when it cannot be calibrated honestly:
      * no value (unreadable signal), or
      * fewer than MIN_HISTORY prior sessions, or
      * a history with no variance (nothing to rank against).
    """
    if value is None:
        return None
    vals = _history_values(history, drive)
    if len(vals) < MIN_HISTORY:
        return None
    if _st.pstdev(vals) < 1e-9:
        return None
    s = sorted(vals)
    n = len(s)

    def q(p: float) -> float:
        return s[min(n - 1, max(0, int(round(p * (n - 1)))))]

    pts = [(q(0.0), -1.0), (q(0.10), -1.0), (q(0.25), -0.5),
           (q(0.50), 0.0), (q(0.75), 0.5), (q(0.90), 1.0), (q(1.0), 1.0)]
    x = float(value)
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            if x1 == x0:
                return max(-1.0, min(1.0, y0))
            return max(-1.0, min(1.0, y0 + (x - x0) / (x1 - x0) * (y1 - y0)))
    return 1.0


def _calibrated(raw: Dict[str, Optional[float]], readable: Dict[str, bool],
                history: Any) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {}
    for d in DRIVES:
        if not readable.get(d) or raw.get(d) is None:
            out[d] = None
        else:
            val = calibrate(d, raw[d], history)
            out[d] = None if val is None else round(val, 3)
    return out


# ---------------------------------------------------------------------------
# Public readers
# ---------------------------------------------------------------------------

def read_trace_vectors(session_trace: Sequence[TraceRow],
                       history: Any = None) -> Dict[str, Optional[float]]:
    """Calibrated drive vector for a running session trace.

    Unreadable -> None. Never coerced to the -1 floor. No I/O.
    """
    raw, readable = raw_trace_scores(session_trace)
    return _calibrated(raw, readable, history)


def projection_vector(target: Optional[Mapping[str, float]],
                      history: Any = None) -> Dict[str, Optional[float]]:
    """Calibrated drive vector for the PRE projection.

    STRUCTURED FLOATS ONLY. `target` is the {scope, rigour} vector the consult
    emitted — there is no prose path here on purpose (R53): the validated
    r=+0.291 / r=+0.232 figures were measured on a numeric vector, so a
    keyword counter wearing them would be a false assurance.

    A drive that is missing, None, non-numeric, boolean, or out of [-1, 1] is
    UNREADABLE (None) — never zero, never the floor.
    """
    raw: Dict[str, Optional[float]] = {}
    readable: Dict[str, bool] = {}
    mapping: Any = target if isinstance(target, Mapping) else None
    for d in DRIVES:
        v = mapping.get(d) if mapping is not None else None
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raw[d] = None
            readable[d] = False
            continue
        fv = float(v)
        if fv < -1.0 or fv > 1.0:
            raw[d] = None
            readable[d] = False
            continue
        raw[d] = fv
        readable[d] = True
    return _calibrated(raw, readable, history)


def trace_from_messages(messages: Iterable[Any]) -> List[TraceRow]:
    """Adapter: OpenAI-shaped payload messages -> (role, tool_name, content).

    The running session trace as the router already sees it. Assistant
    tool_calls contribute one row per call (the call NAME is what the drive
    features read); tool results contribute their own row.
    """
    rows: List[TraceRow] = []
    for m in (messages or ()):
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "")
        content = m.get("content")
        if isinstance(content, list):
            parts = []
            for p in content:
                if isinstance(p, dict):
                    parts.append(str(p.get("text") or ""))
                else:
                    parts.append(str(p))
            content = "".join(parts)
        content = str(content or "")
        if role == "assistant":
            calls = m.get("tool_calls")
            if isinstance(calls, list) and calls:
                for c in calls:
                    fn = (c or {}).get("function") if isinstance(c, dict) else None
                    name = str((fn or {}).get("name") or "") if isinstance(fn, dict) else ""
                    rows.append(("tool", name, str((fn or {}).get("arguments") or "") if isinstance(fn, dict) else ""))
                continue
            rows.append((role, None, content))
        elif role == "tool":
            rows.append(("tool", str(m.get("name") or m.get("tool_name") or ""), content))
        else:
            rows.append((role, None, content))
    return rows


# ---------------------------------------------------------------------------
# Delta
# ---------------------------------------------------------------------------

def _z(value: float, ref: Optional[Sequence[float]]) -> float:
    """z-score `value` against its own reference distribution.

    When no reference is supplied the value is already on the common
    calibrated scale (0 = median of the same reference population on both
    sides — the R51 common-scale fix), so the standardisation is the identity.
    """
    vals = [float(v) for v in (ref or ()) if isinstance(v, (int, float))
            and not isinstance(v, bool)]
    if len(vals) < 2:
        return float(value)
    sd = _st.pstdev(vals)
    if sd < 1e-9:
        return float(value)
    return (float(value) - _st.mean(vals)) / sd


def delta_vector(target: Optional[Dict[str, Optional[float]]],
                 used: Optional[Dict[str, Optional[float]]],
                 ref_target: Optional[Dict[str, Sequence[float]]] = None,
                 ref_used: Optional[Dict[str, Sequence[float]]] = None
                 ) -> Dict[str, Optional[float]]:
    """target - used, per drive, both sides z-scored first.

    None propagates: a drive is reported only when BOTH sides are readable.
    A drive that is unreadable on either side is UNKNOWN, not zero and not the
    floor — the whole point of the R50 fix.
    """
    target = target or {}
    used = used or {}
    out: Dict[str, Optional[float]] = {}
    for d in DRIVES:
        t = target.get(d)
        u = used.get(d)
        if t is None or u is None:
            out[d] = None
            continue
        try:
            zt = _z(float(t), (ref_target or {}).get(d))
            zu = _z(float(u), (ref_used or {}).get(d))
            out[d] = round(zt - zu, 3)
        except Exception:  # noqa: BLE001 — an unreadable number is UNKNOWN
            out[d] = None
    return out


# ---------------------------------------------------------------------------
# Stance lines — bounded, substrate-free, silence-capable
# ---------------------------------------------------------------------------

def stance_lines(delta: Optional[Dict[str, Optional[float]]],
                 band: float = BAND) -> List[str]:
    """AT MOST one line per drive, only when |delta| > band.

    Returns [] when nothing is out of band. Silence is a valid, common, and
    honest output: most turns are not out of band, and a stance line on a
    median turn is noise the agent has to spend attention discarding.
    """
    out: List[str] = []
    delta = delta or {}
    for d in DRIVES:
        v = delta.get(d)
        if v is None:
            continue
        try:
            fv = float(v)
        except Exception:  # noqa: BLE001
            continue
        if abs(fv) <= band:
            continue
        line = _STANCE.get((d, 1 if fv > 0 else -1))
        if not line:
            continue
        if len(line) > MAX_LINE_CHARS:  # defensive: never ship an over-long line
            continue
        out.append(line)
        if len(out) >= MAX_LINES:
            break
    total = 0
    capped: List[str] = []
    for line in out:
        if total + len(line) + (1 if capped else 0) > MAX_BLOCK_CHARS:
            break
        capped.append(line)
        total += len(line) + (1 if len(capped) > 1 else 0)
    return capped


def modulation_block(delta: Optional[Dict[str, Optional[float]]],
                     band: float = BAND) -> str:
    """The delivered block, or '' when there is nothing to say.

    Distinct block, headed MINDSET MODULATION. Empty string — not a bare
    header, not a spacer — when no drive is out of band.
    """
    lines = stance_lines(delta, band=band)
    if not lines:
        return ""
    body = "\n".join(lines)
    if len(body) > MAX_BLOCK_CHARS:
        body = body[:MAX_BLOCK_CHARS]
    return MINDSET_HEADER + "\n" + body
