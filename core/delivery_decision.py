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

# M50-23: ONE source for the banner grammar. `delivery_integrity` owns
# BANNER_HEAD and its block-stripper; the delivery boundary only needs the head
# to know "this line is ours, keep it". Importing the constant rather than
# re-typing the string means the two can never drift apart.
from .delivery_integrity import BANNER_HEAD as _BANNER_HEAD

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
#: M53-03: the lane produced NO render; the original body stands. Distinct from
#: lane_delivered so a reader cannot mistake "nothing was substituted" for a
#: substitution.
REASON_LANE_DELIVERED = "lane_delivered"
REASON_LANE_NO_RENDER = "lane_no_render"
REASON_NO_LANE = "no_admissible_lane"
REASON_MARKER_VIOLATION = "marker_violation"
REASON_SEAM_FAILURE = "seam_failure"

# --------------------------------------------------------------------------- #
# the marker set — DATA. Extended only by adding to this tuple.
# --------------------------------------------------------------------------- #
MARKERS = (
    '<turn declined=',
    # M53-02: the ARMED-turn attestation is the same marker FAMILY and was
    # missing from this vocabulary, so `carries_marker` could not see it and a
    # truncated/malformed fragment ('<turn domain="…" type="…" /') reached the
    # delivered body. The well-formed form is removed byte-wise by `_TAG_RE`
    # in `strip_markers`; this entry is what makes the LEAK VOCABULARY agree
    # with the strip, and it is the only thing that catches a fragment.
    '<turn domain=',
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

# --------------------------------------------------------------------------- #
# M40-15 — the SANCTIONED population. TWO origins, one vocabulary.
#
# `MARKERS` above is a leak vocabulary: it says "a routing marker string
# appeared in a delivered body". It does NOT say where it came from, and the
# two origins need opposite outcomes:
#
#   (a) SANCTIONED INFRASTRUCTURE — the platform's own seam/rule blocks
#       (frames.HS_*_MARKER, frames.platform_rule_marker). Their marker lines
#       sit in the model's CONTEXT, so a model may echo one into its answer.
#       Doctrine: marked and unmarked seam blocks are platform infrastructure,
#       never spoofs. The marker's job is to be invisible to the user, not to
#       suppress the answer: the marker bytes are STRIPPED and the body ships.
#       (Live specimen M40-13 shadow deny leg: case_id=D6 reason=marker_violation
#       marker=HIGHER-SELF — the answer was replaced by a withhold notice.)
#
#   (b) EVERYTHING ELSE — a bare token, a half-marker with no provenance
#       suffix, a marker copied out of the user's own turn (the user's bytes,
#       not ours), a forged carrier. Still `marker_violation`, still terminal.
#
# The discrimination is TEXTUAL and ORIGIN-aware, and deliberately narrow:
# only the byte-exact canonical marker forms qualify, and only when the ask
# does not itself carry that string. A canonical marker present in the ask is
# user content; it is not ours to sanitise away.
#
# Parity with frames.py is pinned by
# tests/delivery/test_m40_15_sanctioned_marker.py (same strings, verbatim).
SANCTIONED_SEAM_MARKERS = (
    "[HIGHER-SELF SEAM | PLATFORM-INTERNAL | PROVENANCE-LOGGED]",
    "[HIGHER-SELF ORIENTATION TURN | FRONTIER-DERIVED | PROVENANCE-LOGGED]",
    "[HIGHER-SELF REFLECTION TURN | FRONTIER-DERIVED | PROVENANCE-LOGGED]",
    "[HIGHER-SELF COMPLETION-AUDIT TURN | FRONTIER-DERIVED | PROVENANCE-LOGGED]",
)

#: Canonical marker forms in one pattern, whitespace-tolerant on the separators
#: so a wrapped/reflowed marker is still the marker, but with the provenance
#: suffix REQUIRED — a marker without it is not this population.
_SANCTIONED_MARKER_RE = re.compile(
    r"\[\s*HIGHER-SELF\s+(?:"
    r"SEAM\s*\|\s*PLATFORM-INTERNAL"
    r"|(?:ORIENTATION|REFLECTION|COMPLETION-AUDIT)\s+TURN\s*\|\s*FRONTIER-DERIVED"
    r")\s*\|\s*PROVENANCE-LOGGED\s*\]",
    re.IGNORECASE,
)
#: frames.platform_rule_marker(rule_id) — "[PLATFORM RULE: <id> | PLATFORM-INTERNAL
#: | PROVENANCE-LOGGED]". Same boundary, same outcome.
_PLATFORM_RULE_MARKER_RE = re.compile(
    r"\[\s*PLATFORM RULE:\s*[A-Za-z0-9_]{1,80}\s*\|\s*PLATFORM-INTERNAL"
    r"\s*\|\s*PROVENANCE-LOGGED\s*\]",
    re.IGNORECASE,
)
_SANCTIONED_ANY_RE = re.compile(
    _SANCTIONED_MARKER_RE.pattern + "|" + _PLATFORM_RULE_MARKER_RE.pattern,
    re.IGNORECASE)


def sanctioned_markers_in(text: str) -> "tuple[str, ...]":
    """The canonical sanctioned marker strings present in `text`, in order.

    Never raises. Empty tuple = no sanctioned infrastructure in the body.
    """
    try:
        out = []
        for m in _SANCTIONED_ANY_RE.finditer(str(text or "")):
            shown = m.group(0)
            if shown not in out:
                out.append(shown)
        return tuple(out)
    except Exception:  # noqa: BLE001 — a marker scan never breaks a decision
        return ()


def strip_sanctioned(text: str, ask_text: str = "") -> "tuple[str, tuple, int]":
    """Remove SANCTIONED platform markers from `text`.

    Returns `(clean, removed_markers, removed_chars)`. Only the marker bytes go:
    the surrounding answer is never re-ordered, re-wrapped or trimmed beyond the
    whitespace a removed marker leaves behind. A marker whose exact string also
    appears in `ask_text` is USER CONTENT, not ours — it is left in place so the
    leak scan (and therefore the withhold) still applies. Never raises.
    """
    raw = str(text or "")
    if not raw.strip():
        return raw, (), 0
    try:
        ask_low = str(ask_text or "").lower()
        removed = []
        changed = False
        kept_lines = []
        for line in raw.splitlines():
            if not _SANCTIONED_ANY_RE.search(line):
                kept_lines.append(line)
                continue
            surplus = []

            def _repl(m):
                shown = m.group(0)
                if shown.lower() in ask_low:
                    return shown  # user content: NOT sanctioned, NOT stripped
                if shown not in surplus:
                    surplus.append(shown)
                return ""

            new_line = _SANCTIONED_ANY_RE.sub(_repl, line)
            if surplus:
                removed.extend(surplus)
                changed = True
            new_line = new_line.strip()
            if new_line:
                kept_lines.append(new_line)
        if not changed:
            return raw, (), 0
        clean = "\n".join(kept_lines)
        clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
        return clean, tuple(removed), max(0, len(raw) - len(clean))
    except Exception:  # noqa: BLE001 — a strip failure never loses bytes
        return raw, (), 0


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
# M53-02: an UNTERMINATED tag line — the shape an output cap leaves behind
# (`<turn domain="none" type="produced" /`) — never matches `_TAG_RE`, which
# requires the closing `>`. Measured: such a body reached D6, `_delivered_bytes`
# returned None (host pass-through), and the host shipped the fragment. The
# whole line is routing metadata wherever it sits, so the LINE goes.
_TAG_FRAGMENT_RE = re.compile(r"(?im)^[ \t]*<turn\b[^\n>]*$")
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
        clean = _TAG_FRAGMENT_RE.sub("", original)
        clean = _TAG_RE.sub("", clean)
        # M53-02: strip the attestation form through its OWN module too. The
        # vocabulary entry above makes the leak scan agree; this makes the
        # STRIP agree on every variant the tag module accepts (no-self-close,
        # leading spaces), and both are counted, so `strip_bytes` is non-zero
        # and `_delivered_bytes` returns explicit bytes instead of the
        # pass-through None that let the host ship its own tag-bearing copy.
        try:
            from ..features.detection import attestation as _att_strip

            clean, _att = _att_strip.strip_attestation(clean)
        except Exception:  # noqa: BLE001 — a missing module is not a leak
            pass
        clean = _MARKER_LINE_RE.sub("", clean)
        clean = _FOOTER_RE.sub("", clean)
        clean = _JSON_TAIL_RE.sub("", clean)
        # M53-02: `_TAG_RE` is form-agnostic (`<turn …>`), so it already removes
        # the attestation tag — but only its BYTES, leaving the newline run the
        # tag line occupied. A delivered body must never open with blank lines
        # that exist only because routing metadata was removed.
        if clean != original:
            clean = clean.lstrip("\n")
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
    # M50-23: `_quote_ranges` yields the quote's inner text LOWERED, so a
    # case-sensitive `inner in exempt` silently refused every span the user
    # copied in original casing — a quoted "HIGHER-SELF" was treated as the
    # model's leak while a quoted "· router ·" passed only because it is
    # already lowercase. Compare both sides folded.
    exempt_low = frozenset(str(e).lower() for e in exempt)
    ranges = _quote_ranges(hay) if exempt else []
    found = False
    start = low.find(needle)
    while start >= 0:
        found = True
        end = start + len(needle)
        if not any(a <= start and end <= b and inner.lower() in exempt_low
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
    #: M53-02 (round 9): bytes ALREADY removed by the delivery boundary.
    #: Carried, never recomputed: the boundary knows what it stripped once, and
    #: re-stripping an already-clean body reports 0 — which is exactly how the
    #: decision read `source=main, strip_bytes=0` and the host shipped its own
    #: pre-strip copy. Folded into the decision's strip count below.
    prior_strip_bytes: int = 0
    #: M53-03: the assimilation acceptance token for this turn ("" when
    #: unknown). Says WHOSE bytes the user read: "<source>:<reason>". A token
    #: naming `original:*` means the lane produced no render, however the lane
    #: body was threaded through — so D5 must not claim SOURCE_LANE.
    lane_acceptance: str = ""


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


def _is_own_banner_line(line: str) -> bool:
    """True for the router's OWN delivered banner line (M50-23).

    M48 requires a fired lane to be VISIBLE in the delivered bytes, so the
    banner line that carries `· router ·` must never be treated as a leak and
    removed. Before this, a marker violation stripped the banner and left the
    answer: the turn survived but the lane became invisible, silently.

    Grammar comes from `delivery_integrity` (one source, pinned by its own
    BANNER_HEAD) — a banner is a line whose first token is that head. A banner
    in the MIDDLE of a body is left to the normal marker rules, matching
    `delivery_integrity.strip_banner`, which only treats lead/tail as banner.
    """
    try:
        return str(line or "").lstrip().startswith(_BANNER_HEAD)
    except Exception:  # noqa: BLE001
        return False


def strip_marker_lines(text: str, quoted: Sequence[str] = ()) -> str:
    """Remove marker-bearing LINES and keep the rest of the answer.

    M50-19. The boundary used to answer a marker hit with a terminal decision,
    and `_delivered_bytes` then shipped a withhold notice INSTEAD of the turn.
    Live cost: a router-known marker that the router itself appended (its own
    `· router ·` banner line) made the plugin swallow whole answers — the plugin
    controlling a turn it never wrote, which the owner ruled out explicitly.

    Only the offending line goes. User-quoted markers are excluded by `quoted`,
    so a user asking ABOUT the marker never loses their own words.

    M50-23: the router's OWN banner line is preserved, never stripped. M48
    requires a fired lane to be visible in the delivered bytes; stripping the
    banner left the answer but erased the lane record, which is how the fleet
    lost sight of every frontier consult. A body made of NOTHING but banners and
    markers still returns "" — router prose is never shippable as an answer.
    """
    try:
        raw = str(text or "")
        if not raw:
            return ""
        kept: list = []
        banners: list = []
        dropped = False
        for line in raw.splitlines():
            if _is_own_banner_line(line):
                banners.append(line)
                continue
            if carries_marker(line, quoted):
                dropped = True
                continue
            kept.append(line)
        core_text = "\n".join(kept).strip()
        if not core_text:
            # nothing but router banner/markers: void, never an answer
            return ""
        if not dropped and not banners:
            return raw
        out = kept + banners
        cleaned = "\n".join(out)
        # a banner line usually leaves a blank run behind it
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned
    except Exception:  # noqa: BLE001 — never break a body
        return str(text or "")


def decide(facts: Any) -> Decision:
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

        # M53-09b: P0 D1 (CLOSED_LINE_HOLD) is deleted. The router does not
        # judge content — routing is decided by the response-type tag.
        # ---- P1 D2: no admissible candidate -> the turn fails loudly ------
        entered_clean, entered_strip = _strip(entered)
        lane = facts.lane_render
        lane_clean, lane_strip = (("", 0) if lane is None else _strip(str(lane)))
        # M53-02 (round 9): fold in what the BOUNDARY already removed. The
        # strip above is idempotent over a cleaned body and would report 0,
        # which made `_delivered_bytes` take the byte-identity branch and
        # return None (host pass-through -> the host's own pre-strip copy).
        # One number, computed once, where the strip happened.
        _prior = int(getattr(facts, "prior_strip_bytes", 0) or 0)
        if _prior > 0:
            entered_strip += _prior
            if lane is not None:
                lane_strip += _prior
            diag["prior_strip_bytes"] = _prior
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
            # 2026-11-14 (Goran): a divergence is an AUDIT finding, never this
            # turn's answer. The recovered body is PRIOR transcript text, so
            # shipping it names an older thematic as the current reply — the
            # live symptom was a 3,579-char previous answer replacing a 4,768
            # char current one (conductor DM). The failure still stands and
            # still logs at error severity; what changes is that the decision
            # names NO bytes, so `_delivered_bytes` falls to its D6
            # pass-through branch and the host keeps the model's OWN text.
            # Marker violations keep their notice: they have no answer to keep.
            diag["seam_failure_recovered_chars"] = len(str(lane_clean or ""))
            return Decision(FAIL_TERMINAL, D6, REASON_SEAM_FAILURE, SOURCE_SEAM,
                            0, diag, None)

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
            ask = str(getattr(facts, "ask_text", "") or "")
            # M40-15: sanctions FIRST, then the leak scan. A canonical platform
            # marker is infrastructure the model may echo; its bytes are removed
            # from the delivered body and recorded as the visibility surface.
            # Whatever survives still faces `carries_marker`, so an invented,
            # forged or user-sourced marker keeps its withhold.
            chosen, blessed, blessed_chars = strip_sanctioned(entered_clean, ask)
            if blessed:
                diag["sanctioned_markers"] = "|".join(blessed)
            if not _admissible(chosen):
                # The body was nothing BUT platform marker lines: there is no
                # answer to ship, and shipping an empty body is the 0-char turn
                # UNC-POST-1 forbids. Same terminal case as any no-lane body.
                diag["sanctioned_only_body"] = True
                diag["_strip"] = entered_strip + blessed_chars
                return _terminal(D6, REASON_NO_LANE, diag)
            hit = carries_marker(chosen, quoted_spans(ask))
            if hit:
                diag["marker_violation"] = True
                diag["marker"] = hit
                # M50-19: strip the marker LINE and ship the answer. The old path
                # went terminal and the delivery edge replaced the whole turn with
                # a withhold notice — triggered by the router's own banner line.
                scrubbed = strip_marker_lines(chosen, quoted_spans(ask))
                if _admissible(scrubbed):
                    diag["marker_scrubbed"] = hit
                    _lost = max(0, len(chosen) - len(scrubbed))
                    diag["_strip"] = entered_strip + blessed_chars + _lost
                    return Decision(DELIVER_ENTERED, D4, reason, SOURCE_MAIN,
                                    entered_strip + blessed_chars + _lost, diag,
                                    scrubbed)
                diag["_strip"] = entered_strip + blessed_chars
                return _terminal(D6, REASON_MARKER_VIOLATION, diag)
            return Decision(DELIVER_ENTERED, D4, reason, SOURCE_MAIN,
                            entered_strip + blessed_chars, diag, chosen)

        # ---- P4 D5: a non-compliant body is replaced when anything is ----
        ask = str(getattr(facts, "ask_text", "") or "")
        lane_chosen, lane_blessed, lane_blessed_chars = strip_sanctioned(
            lane_clean, ask)
        if lane_blessed:
            diag["sanctioned_markers"] = "|".join(lane_blessed)
        # M53-02 (round 5): the LANE body gets the SAME marker vocabulary the
        # entered path uses. `lane_clean` already went through `_strip` above,
        # so this is idempotent for a body that strip recognised — but a lane
        # body assembled by any other route must not depend on that happening
        # upstream. The removed bytes ride the decision's strip field, so
        # `_delivered_bytes` names EXPLICIT cleaned bytes instead of the None
        # pass-through that ships the host's own copy.
        #
        # M50-19/20 stays intact: the LINE is stripped and the answer ships;
        # a marker quoted in the ask is preserved by `carries_marker` below,
        # so the violation path (not a withhold of the whole turn) still
        # applies to genuine leaks.
        try:
            _lane_vocab_clean, _lane_vocab_removed = strip_markers(lane_chosen)
            if _lane_vocab_clean != lane_chosen:
                lane_chosen = _lane_vocab_clean
                lane_blessed_chars += max(0, int(_lane_vocab_removed or 0))
                diag["lane_marker_stripped"] = True
        except Exception:  # noqa: BLE001 — the strip never blocks a decision
            pass
        if lane_ok and _admissible(lane_chosen) and not carries_marker(
                lane_chosen, quoted_spans(ask)):
            # M53-03: WHOSE bytes are these? The acceptance token is read, not
            # recomputed. `original:*` means the chain produced nothing and the
            # original body stands — claiming SOURCE_LANE there is a lineage
            # lie. An EMPTY token keeps today's behaviour exactly: unknown must
            # never silently become "no render".
            _accept = str(getattr(facts, "lane_acceptance", "") or "")
            if _accept.startswith("original:"):
                diag["lane_acceptance"] = _accept
                return Decision(DELIVER_LANE, D5, REASON_LANE_NO_RENDER,
                                SOURCE_MAIN,
                                lane_strip + lane_blessed_chars, diag, lane_chosen)
            return Decision(DELIVER_LANE, D5, REASON_LANE_DELIVERED, SOURCE_LANE,
                            lane_strip + lane_blessed_chars, diag, lane_chosen)

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
