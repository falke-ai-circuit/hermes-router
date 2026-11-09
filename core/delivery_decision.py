"""core/delivery_decision.py — THE ONE decision point: deliver vs replace.

WHAT THIS IS
    A pure, total function `decide(facts) -> Decision`. It is the only place in
    the plugin where the delivery choice (deliver the entered body / deliver a
    lane render / hold a closed line / consult the gate / fail terminal) is
    made. Everything else supplies facts to it; nothing else compares a body to
    a body for delivery purposes.

WHY IT EXISTS (M35-12 spec, M35-14 implementation)
    The same decision — "does the agent's own body already satisfy this ask?" —
    was being taken by four mechanisms at four different times: the six-row
    rule table, the post-guard ladder, a pre-call window, and a post-hoc repair
    at the row writer. Each had its own suppression condition, so every fix
    satisfied its failing row and broke a neighbour. This module replaces that
    precedence with one ordered, closed case set.

THE CLOSED CASE SET (first match wins; D6 is the else)
    P0  D1 CLOSED_LINE_HOLD      closed_line_hit != ""          -> HOLD_CLOSED
    P1  D2 MARKER_VOID           no admissible candidate         -> FAIL_TERMINAL
    P2  D3 GATE_UNCONSULTED      gate unread + replaceable + a0  -> CONSULT_REENTER
    P3  D4 COMPLIANT_DELIVER     body_class == compliance        -> DELIVER_ENTERED
    P4  D5 NONCOMPLIANT_REPLACE  non-compliant + admissible lane -> DELIVER_LANE
    P5  D6 NONCOMPLIANT_NO_LANE  everything else                 -> FAIL_TERMINAL

ORDER JUSTIFICATION (spec §2.1)
    D1 first: a closed line is a property of the ASK; no body, verdict, rule or
    declaration may open it. D2 before D3: a consult cannot repair a candidate
    set with nothing admissible in it. D3 before D4/D5: the decision must not
    exist until the gate has been asked, for any turn that could be replaced —
    a compliant, untargeted body is the deliberate carve-out (`not_required`).
    D4 before D5: compliance outranks every rule. D5 before D6: replace when
    anything admissible exists; fail only when nothing does.

PURITY
    No I/O, no config, no lane imports, no exceptions escaping. The gate
    consult for D3 happens at the CALLER (it is the only impure step); this
    module only names that it must happen.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Dict, Optional

# --------------------------------------------------------------------------- #
# actions, cases, sources — closed vocabularies
# --------------------------------------------------------------------------- #
DELIVER_ENTERED = "DELIVER_ENTERED"
DELIVER_LANE = "DELIVER_LANE"
HOLD_CLOSED = "HOLD_CLOSED"
CONSULT_REENTER = "CONSULT_REENTER"
FAIL_TERMINAL = "FAIL_TERMINAL"

ACTIONS = (DELIVER_ENTERED, DELIVER_LANE, HOLD_CLOSED, CONSULT_REENTER,
           FAIL_TERMINAL)

D1 = "D1"  # CLOSED_LINE_HOLD
D2 = "D2"  # MARKER_VOID
D3 = "D3"  # GATE_UNCONSULTED
D4 = "D4"  # COMPLIANT_DELIVER
D5 = "D5"  # NONCOMPLIANT_REPLACE
D6 = "D6"  # NONCOMPLIANT_NO_LANE

CASES = (D1, D2, D3, D4, D5, D6)

CASE_NAMES = {
    D1: "CLOSED_LINE_HOLD",
    D2: "MARKER_VOID",
    D3: "GATE_UNCONSULTED",
    D4: "COMPLIANT_DELIVER",
    D5: "NONCOMPLIANT_REPLACE",
    D6: "NONCOMPLIANT_NO_LANE",
}

SOURCE_MAIN = "main"
SOURCE_LANE = "lane"
SOURCE_NONE = "none"
SOURCE_SEAM = "seam"

COMPLIANCE = "compliance"
REFUSAL = "refusal"
HEDGE = "hedge"
SCAFFOLD = "scaffold"

#: Rules 1-4 may REQUEST a replacement; the gate must be consulted first.
#: Rules 5-6 (and `none`) are the D3 carve-out population (spec §2.1).
TARGETED_RULES = ("rule_1", "rule_2", "rule_3", "rule_4")

REASON_MAIN_COMPLIED = "main_complied"
REASON_COMPLIANCE_OUTRANKS_RULE = "compliance_outranks_rule"
REASON_CLOSED_LINE = "closed_line"
REASON_MARKER_VOID = "marker_void"
REASON_GATE_UNCONSULTED = "gate_not_consulted"
REASON_GATE_UNAVAILABLE = "gate_unavailable_after_consult"
REASON_LANE_DELIVERED = "lane_delivered"
REASON_NO_LANE = "no_admissible_lane"
REASON_MARKER_VIOLATION = "marker_violation"
REASON_SEAM_FAILURE = "seam_failure"

# --------------------------------------------------------------------------- #
# the marker set — DATA. Extended only by adding to this tuple.
# --------------------------------------------------------------------------- #
MARKERS = (
    '<turn declined=',
    "PLATFORM RULE",
    "[router]",
    "[router advisory",
    "[router fork note",
    "[internal state readout",
    "\u00b7 router \u00b7",
    "UNCENSORED-ROUTER INJECTION",
    "HIGHER-SELF",
    "uncensored-render-note",
    "SOURCE AGENT",
)

#: Router prose that is NOT a body. A candidate consisting only of these is
#: VOID, not empty: it is the router talking, not the agent answering.
ROUTER_PROSE = (
    "[router] this turn produced no work product",
    "[router] turn ended on the router's own advisory echo",
    "[router] this turn's body was withheld",
    "[router] no answer was delivered for this turn",
    "[hermes-router] this turn was withheld",
)

# --------------------------------------------------------------------------- #
# marker strip — remove bytes only, and record the delta
# --------------------------------------------------------------------------- #
_TAG_RE = re.compile(r"<turn\b[^>]*?/?>", re.IGNORECASE)
_MARKER_LINE_RE = re.compile(
    r"(?im)^[ \t]*(?:\[router[^\]]*\]|\[router advisory[^\n]*|"
    r"\[router fork note[^\n]*|\[internal state readout[^\n]*|"
    r"PLATFORM RULE[^\n]*|UNCENSORED-ROUTER INJECTION[^\n]*|"
    r"HIGHER-SELF[^\n]*|uncensored-render-note[^\n]*|SOURCE AGENT[^\n]*)[ \t]*(?:\n|$)")
_FOOTER_RE = re.compile(
    r"(?im)(?:^[ \t]*)?\u00b7[ \t]*router[ \t]*\u00b7[^\n]*(?:\n|$)")
_JSON_TAIL_RE = re.compile(
    r'(?is)\n?[ \t]*\{[^{}]*"(?:povs|synthesis|brief_bullets)"\s*:.*$')


def strip_markers(text: str) -> "tuple[str, int]":
    """Remove marker bytes from `text`. Returns (clean, removed_byte_count).

    REMOVE ONLY. The count is the attributed delta a ledger row records, so a
    -1 stop being mysterious: it is a strip, and it is named. Never raises.
    """
    original = str(text or "")
    if not original:
        return original, 0
    try:
        clean = _TAG_RE.sub("", original)
        clean = _MARKER_LINE_RE.sub("", clean)
        clean = _FOOTER_RE.sub("", clean)
        clean = _JSON_TAIL_RE.sub("", clean)
        removed = len(original) - len(clean)
        return clean, max(0, removed)
    except Exception:  # noqa: BLE001 — a strip failure never loses bytes
        return original, 0


#: A quoted passage: paired quotes of the SAME kind, not glued to a word
#: character on the outside (so `don't` is not a span). Curly and straight forms
#: are both accepted; the inner text is what matters, never the quote glyph.
_QUOTE_PAIRS = (("'", "'"), ('"', '"'), ("\u2018", "\u2019"), ("\u201c", "\u201d"))


def quoted_spans(text: str) -> "tuple[str, ...]":
    """The NORMALISED inner text of every quoted passage in `text`.

    Used to recognise an echo of the user's own words: a marker that appears
    inside a quoted passage copied from the ask is the user's string, not a
    router leak. Normalisation (lowercase, whitespace collapsed, straight and
    curly quotes alike) is what makes an echo match across the two surfaces;
    the quote glyph itself is never compared.
    """
    spans = []
    try:
        raw = str(text or "")
        for opener, closer in _QUOTE_PAIRS:
            i = 0
            while True:
                a = raw.find(opener, i)
                if a < 0:
                    break
                # An apostrophe inside a word ("don't") opens nothing.
                if opener in ("'", "\u2019") and a > 0 and raw[a - 1].isalnum():
                    i = a + 1
                    continue
                b = raw.find(closer, a + 1)
                if b < 0:
                    break
                inner = raw[a + 1:b]
                if "\n" not in inner and 1 <= len(inner.strip()) <= 400 \
                        and not (closer in ("'", "\u2019") and b + 1 < len(raw)
                                 and raw[b + 1].isalnum()):
                    spans.append(_norm_quote(inner))
                i = b + 1
    except Exception:  # noqa: BLE001 — a quote scan never breaks a decision
        return tuple(spans)
    return tuple(spans)


def _norm_quote(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def _quote_ranges(text: str) -> "list[tuple[int, int, str]]":
    """(start, end, normalised inner) for every quoted passage, WITH offsets.

    `end` is exclusive and covers the marker text only inside the quotes.
    """
    out = []
    raw = str(text or "")
    for opener, closer in _QUOTE_PAIRS:
        i = 0
        while True:
            a = raw.find(opener, i)
            if a < 0:
                break
            if opener in ("'", "\u2019") and a > 0 and raw[a - 1].isalnum():
                i = a + 1
                continue
            b = raw.find(closer, a + 1)
            if b < 0:
                break
            inner = raw[a + 1:b]
            if "\n" not in inner and 1 <= len(inner.strip()) <= 400 \
                    and not (closer in ("'", "\u2019") and b + 1 < len(raw)
                             and raw[b + 1].isalnum()):
                out.append((a + 1, b, _norm_quote(inner)))
            i = b + 1
    return out


def marker_is_echo(text: str, marker: str, exempt_spans: "tuple[str, ...]" = ()) -> bool:
    """True when EVERY occurrence of `marker` in `text` sits in a copied quote.

    One marker, one answer — the battery's marker list and the router's are not
    the same tuple, so a caller that owns its own list must ask per marker
    rather than read `carries_marker`'s first-hit ordering.
    """
    hay = str(text or "")
    low = hay.lower()
    needle = str(marker or "").lower()
    exempt = tuple(exempt_spans or ())
    if not needle:
        return False
    ranges = _quote_ranges(hay) if exempt else []
    found = False
    start = low.find(needle)
    while start >= 0:
        found = True
        end = start + len(needle)
        if not any(a <= start and end <= b and inner in exempt
                   for a, b, inner in ranges):
            return False
        start = low.find(needle, start + 1)
    return found


def carries_marker(text: str, exempt_spans: "tuple[str, ...]" = ()) -> str:
    """The first marker found in `text`, or "" — the containment check.

    `exempt_spans` are quoted passages taken from the USER turn. A marker
    occurrence that sits inside a quoted passage of the body whose inner text
    matches one of them is an ECHO of the user's own words, not a leak, and is
    skipped. Every other occurrence still fails: a marker the model invents is
    not inside a copied quote, so the real detection is unchanged.
    """
    for marker in MARKERS:
        if marker.lower() in str(text or "").lower() \
                and not marker_is_echo(text, marker, exempt_spans):
            return marker
    return ""



def is_router_prose(text: str) -> bool:
    """True when the body is wholly router-authored notice text (VOID)."""
    body = str(text or "").strip()
    if not body:
        return True
    low = body.lower()
    for notice in ROUTER_PROSE:
        if low.startswith(notice.lower()):
            return True
    return False


# --------------------------------------------------------------------------- #
# the facts record and the decision
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DeliveryFacts:
    """Everything the decision turns on. Built ONCE at the delivery edge.

    `delivered_source` is deliberately absent: it is the OUTPUT, not an input.
    The seam may no longer hand the caller a body `decide()` did not name.
    """

    entered_body: str = ""
    body_class: str = COMPLIANCE
    gate_consulted: bool = False
    gate_verdict: str = "abstain"
    gate_reason: str = "not_consulted"
    rule_id: str = "none"
    closed_line_hit: str = ""
    lane_render: Optional[str] = None
    attempt: int = 0
    #: A failure the SEAM ITSELF declared (delivery integrity). Not a body
    #: classification: no case may deliver an answer over it.
    seam_failure: str = ""
    #: The turn's ASK. Only the marker gate reads it: a marker inside a quoted
    #: passage copied from the user's own words is an echo, not a leak. Absent
    #: (every pre-existing caller) means no exemption — the strict scan.
    ask_text: str = ""


@dataclass(frozen=True)
class Decision:
    """The one answer. `body` is the exact bytes the caller may deliver."""

    action: str
    case_id: str
    reason: str
    delivered_source: str
    strip_bytes: int = 0
    diagnostics: Dict[str, object] = field(default_factory=dict)
    body: Optional[str] = None

    @property
    def case_name(self) -> str:
        return CASE_NAMES.get(self.case_id, "")

    def as_record(self) -> Dict[str, object]:
        """The ledger-shaped projection (no body: bytes are not ledger data)."""
        rec = {
            "case_id": self.case_id,
            "case": self.case_name,
            "action": self.action,
            "reason": self.reason,
            "delivered_source": self.delivered_source,
            "strip_bytes": int(self.strip_bytes),
        }
        rec.update(self.diagnostics)
        return rec


def _strip(text: str) -> "tuple[str, int]":
    return strip_markers(text)


def _admissible(body: str) -> bool:
    """A candidate is admissible when it retains a deliverable body."""
    clean, _ = _strip(body)
    if not clean.strip():
        return False
    if is_router_prose(clean):
        return False
    return True


def _terminal(case_id: str, reason: str, diag: Dict[str, object]) -> Decision:
    return Decision(FAIL_TERMINAL, case_id, reason, SOURCE_NONE,
                    int(diag.pop("_strip", 0) or 0), diag, None)


def decide(facts: DeliveryFacts) -> Decision:
    """The total delivery decision. Never raises; never returns nothing."""
    diag: Dict[str, object] = {
        "body_class": str(getattr(facts, "body_class", "") or ""),
        "gate_consulted": bool(getattr(facts, "gate_consulted", False)),
        "gate_verdict": str(getattr(facts, "gate_verdict", "") or ""),
        "gate_reason": str(getattr(facts, "gate_reason", "") or ""),
        "rule_id": str(getattr(facts, "rule_id", "") or "none"),
        "attempt": int(getattr(facts, "attempt", 0) or 0),
        "marker_violation": False,
        "unjudged": str(getattr(facts, "rule_id", "") or "none") in ("", "none"),
        "degraded": False,
    }
    try:
        entered = str(facts.entered_body or "")
        body_class = str(facts.body_class or COMPLIANCE)
        rule_id = str(facts.rule_id or "none")

        # ---- P0 D1: a closed line is a property of the ASK --------------
        # Nothing overrides it: not compliance, not a lane render, not
        # declined="true", not rule_1, not a config key.
        if str(facts.closed_line_hit or ""):
            clean, removed = _strip(entered)
            diag["closed_line"] = str(facts.closed_line_hit)
            return Decision(HOLD_CLOSED, D1, REASON_CLOSED_LINE, SOURCE_MAIN,
                            removed, diag, clean)

        # ---- P1 D2: no admissible candidate -> the turn fails loudly ------
        entered_clean, entered_strip = _strip(entered)
        lane = facts.lane_render
        lane_clean, lane_strip = (("", 0) if lane is None else _strip(str(lane)))
        entered_ok = _admissible(entered)
        lane_ok = lane is not None and bool(lane_clean.strip()) and not is_router_prose(lane_clean)
        diag["entered_void"] = not entered_ok
        diag["lane_void"] = not lane_ok
        if not entered_ok and not lane_ok:
            diag["_strip"] = entered_strip
            return _terminal(D2, REASON_MARKER_VOID, diag)

        # ---- P1b: a SEAM-DECLARED failure outranks every body case --------
        # The hook already decided this turn failed (transcript divergence).
        # D6 is defined as "a post-condition (§7) was violated", and here the
        # post-condition failed BEFORE any body was considered — so the only
        # bytes this decision may name are the failure body itself. Without
        # this, a compliant entered body won D4, the failure body was dropped,
        # and the turn "passed through" as if it had been answered.
        if str(getattr(facts, "seam_failure", "") or ""):
            diag["seam_failure"] = str(facts.seam_failure)
            return Decision(FAIL_TERMINAL, D6, REASON_SEAM_FAILURE, SOURCE_SEAM,
                            0, diag, (lane_clean or None))

        # ---- P2 D3: the decision must not exist before the gate was asked -
        replaceable = (body_class != COMPLIANCE
                       or rule_id in TARGETED_RULES)
        if (not bool(facts.gate_consulted)) and replaceable \
                and int(facts.attempt) == 0:
            return Decision(CONSULT_REENTER, D3, REASON_GATE_UNCONSULTED,
                            SOURCE_NONE, 0, diag, None)

        # ---- P3 D4: compliance outranks every rule -----------------------
        # `entered_ok` is load-bearing: D4's action is DELIVER_ENTERED, and a
        # void entered body (empty after strip, or wholly router prose) has no
        # bytes to deliver. Without it an empty body — which classify_original
        # names COMPLIANCE by its own "everything else" rule — won D4 and the
        # admissible lane beside it (the empty-body VISIBLE FAILURE the
        # orchestrator had already composed) was silently dropped, producing
        # exactly the 0-char turn UNC-POST-1 forbids.
        if body_class == COMPLIANCE and entered_ok:
            diag["rule_replace_ignored"] = rule_id in TARGETED_RULES
            diag["main_complied"] = True
            reason = (REASON_COMPLIANCE_OUTRANKS_RULE
                      if rule_id in TARGETED_RULES else REASON_MAIN_COMPLIED)
            chosen = entered_clean
            hit = carries_marker(chosen, quoted_spans(
                getattr(facts, "ask_text", "") or ""))
            if hit:
                diag["marker_violation"] = True
                diag["marker"] = hit
                diag["_strip"] = entered_strip
                return _terminal(D6, REASON_MARKER_VIOLATION, diag)
            return Decision(DELIVER_ENTERED, D4, reason, SOURCE_MAIN,
                            entered_strip, diag, chosen)

        # ---- P4 D5: a non-compliant body is replaced when anything is ----
        if lane_ok and not carries_marker(
                lane_clean, quoted_spans(getattr(facts, "ask_text", "") or "")):
            return Decision(DELIVER_LANE, D5, REASON_LANE_DELIVERED, SOURCE_LANE,
                            lane_strip, diag, lane_clean)

        # ---- P5 D6: the else. No router prose is ever substituted. -------
        diag["_strip"] = entered_strip
        return _terminal(D6, REASON_NO_LANE, diag)
    except Exception as exc:  # noqa: BLE001 — never escape the function
        diag["degraded"] = True
        diag["error"] = type(exc).__name__
        return Decision(FAIL_TERMINAL, D6, "decision_error", SOURCE_NONE, 0,
                        diag, None)


def reenter_after_consult(facts: DeliveryFacts, *,
                          gate_verdict: str = "abstain",
                          gate_reason: str = REASON_GATE_UNAVAILABLE) -> DeliveryFacts:
    """The bounded re-entry facts (D3 -> attempt 1).

    `abstain` is a FAILURE and falls to the body ladder — it is never read as
    "safe". `not_required` is legal only for a compliant, untargeted body
    (spec §2.1); any other occurrence is a D3 violation and is counted.
    """
    try:
        return replace(facts, gate_consulted=True,
                       gate_verdict=str(gate_verdict or "abstain"),
                       gate_reason=str(gate_reason or REASON_GATE_UNAVAILABLE),
                       attempt=1)
    except Exception:  # noqa: BLE001
        return facts


def gate_reason_legal(facts: DeliveryFacts) -> bool:
    """True when `not_required` is legal for these facts (spec §2.1)."""
    return (str(facts.body_class) == COMPLIANCE
            and str(facts.rule_id or "none") not in TARGETED_RULES)
