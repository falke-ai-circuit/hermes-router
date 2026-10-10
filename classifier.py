"""Pure pattern data for the uncensored-router classifier.

Pattern groups keyed by config name. Mirrors security-guidance/patterns.py:
pure data + pure helpers, no I/O, no env-var reads, side-effect-free import.

Spec §8: pre-router groups (contested-edge detection) live here; post-router
groups (refusal detection) live here. `line_hold_essay` is heuristic, not
regex — see _is_line_hold_essay().

Config selects groups by NAME (e.g. `pre_patterns: [csam_underage]`); regex
bodies stay in code so config stays lean.
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# Pre-router pattern groups (contested-edge detection on the user message)
# ---------------------------------------------------------------------------

from .features.patterns.engine import bind_re_families as _p7_bind

_p7_bind(globals(), "classifier-groups")






# ---------------------------------------------------------------------------
# Post-router pattern groups (refusal detection on the agent response)
# ---------------------------------------------------------------------------




def strip_injected_context(text: str) -> str:
    """Platform-injected blocks (<memory-context>, [System note: ... recalled
    memory context ...]) are METADATA, not the user's words. The csam/minors
    recall note ("minors asks are deliberate testing probes") lives in every
    profile's recall text — a bare-word minors pattern scanning it FP-routes
    benign asks (audit/re-audit asks shadow-routed live 2026-09-08). Strip
    before classification. Never raises.

    R19.13 FIX 3: forged banner-persona blocks (higher-self / shadow-self /
    reflex rule text arriving WITHOUT a legitimate bracketed marked-turn
    carrier) are stripped here too — never adopted, never classified on
    (reviewer specimen log: 11 forged HIGHER-SELF blocks claiming platform
    sanction + anti-mention discipline)."""
    if not text:
        return text
    t = re.sub(r"<memory-context>.*?</memory-context>", " ", text,
               flags=re.DOTALL | re.IGNORECASE)
    t = re.sub(r"\[System note:.*?(?:recalled memory|memory graph).*?\]",
               " ", t, flags=re.DOTALL | re.IGNORECASE)
    try:
        from .frames import flag_forged_banner_persona as _ffbp

        sig = _ffbp(t)
        if sig:
            # cut the forged block: from the first signal line to the end of
            # its enclosing blank-line-delimited block (fail-open: if the
            # cut somehow yields nothing, return the uncut text — the flag
            # itself is the contract, the cut is best-effort hygiene).
            idx = t.lower().find(sig)
            if idx >= 0:
                start = t.rfind("\n\n", 0, idx)
                start = 0 if start < 0 else start + 2
                end = t.find("\n\n", idx)
                cut = t[:start] + " " + ("" if end < 0 else t[end + 2:])
                if cut.strip():
                    t = cut
    except Exception:  # noqa: BLE001 — hygiene only, never break classification
        pass
    return t

# ---------------------------------------------------------------------------
# Compiled group registry. Each group -> list of compiled regexes.
# ---------------------------------------------------------------------------

PATTERN_GROUPS: Dict[str, List[re.Pattern]] = {
    "csam_underage": [re.compile(p, re.IGNORECASE) for p in _CSAM_UNDERAGE],
    "bioweapon_protocol": [re.compile(p, re.IGNORECASE) for p in _BIOWEAPON_PROTOCOL],
    "ied_construction": [re.compile(p, re.IGNORECASE) for p in _IED_CONSTRUCTION],
    "named_target_defamation": [re.compile(p, re.IGNORECASE) for p in _NAMED_TARGET_DEFAMATION],
    "trafficking_route": [re.compile(p, re.IGNORECASE) for p in _TRAFFICKING_ROUTE],
    "weaponized_playbook_real_name": [re.compile(p, re.IGNORECASE) for p in _WEAPONIZED_PLAYBOOK_REAL_NAME],
    "refusal_phrases": [re.compile(p, re.IGNORECASE) for p in _REFUSAL_PHRASES],
    # line_hold_essay is heuristic-only (no regexes); handled in scan_post.
    "line_hold_essay": [],
}

PRE_GROUP_NAMES = frozenset(k for k in PATTERN_GROUPS if k != "refusal_phrases" and k != "line_hold_essay")
POST_GROUP_NAMES = frozenset({"refusal_phrases", "line_hold_essay"})

# ---------------------------------------------------------------------------
# §12-A1 combined compiled matchers (2026-09-07 simplicity audit, luna-pro
# ratified): ONE compiled alternation per lane — never per-group/per-language
# sequential scan loops on the hot path. Benign case = doctrine-quote check +
# ONE combined scan, zero per-group loops. The per-group lists above remain
# the named registry (match attribution + config selection); attribution
# re-runs the named groups ONLY when the combined scan actually hit (contested
# content, about to be re-processed downstream anyway).
# Join discipline: patterns with a leading global inline flag (?s) are
# re-scoped to (?s:...) — a bare (?s) mid-alternation is a re.error in
# Python 3.11+, and hoisting it would change DOT semantics for every other
# alternative (behavior break).
# ---------------------------------------------------------------------------

def _join_combined(patterns: List[str]) -> "re.Pattern":
    parts = []
    for p in patterns:
        if p.startswith("(?s)"):
            parts.append("(?s:%s)" % p[4:])
        else:
            parts.append("(?:%s)" % p)
    return re.compile("|".join(parts), re.IGNORECASE)


# R33-D5: the combined fast path now includes the refusal-framing surface —
# scan_pre(patterns=["refusal_phrases"]) must reach the fast-path hit.
_PRE_COMBINED_RE = _join_combined(
    [rx.pattern for name in ("csam_underage", "bioweapon_protocol",
                             "ied_construction", "named_target_defamation",
                             "trafficking_route",
                             "weaponized_playbook_real_name",
                             "refusal_phrases")
     for rx in PATTERN_GROUPS[name]],
)


_REFUSAL_COMBINED_RE = _join_combined(_REFUSAL_PHRASES)


def scan_pre(content: str, *, patterns: List[str], case_sensitive: bool = False) -> List[str]:
    """Return list of matched pre-router pattern group names. Empty = no match.
    Doctrine-quote framing (is_doctrine_quote) suppresses PRE matches — a
    document describing the line is not an ask for the line.

    §12-A1 hot path: the combined matcher decides hit/no-hit in ONE scan. No
    combined hit over the full group set implies no subset hit (same
    alternatives, same flags), so benign content returns [] with ZERO
    per-group loops. A combined hit falls through to the named-group scan
    for attribution (honors the config-selected subset)."""
    # Injected-context strip (2026-09-08): platform metadata blocks are not
    # the user's words — the recall-text "minors" note FP-routed benign asks.
    content = strip_injected_context(content)
    if is_doctrine_quote(content):
        return []
    if case_sensitive:
        return _scan(content, patterns)
    try:
        first = _PRE_COMBINED_RE.search(content)
    except Exception:  # noqa: BLE001 — scanning must never raise
        return []
    if first is None:
        return []
    return _scan(content, patterns)


def _match_in_code_context(content: str) -> bool:
    """v4.4.0 (R12 censorship-flinch-only gate): True when the combined-regex
    match sits inside code/regex/quote context — meaning the matched refusal
    words are QUOTED material (docs, source dumps, pattern tables), not the
    agent's own refusal voice. Checks the line containing the match:
    - backtick-wrapped or fence context
    - regex-source markers: \b, (?:, | alternation, .* quantifiers
    - snake_case identifier context (e.g. _REFUSAL_PHRASES = [...])
    Never suppresses a bare prose match. Fail-open on any error (returns
    False -> route as before).
    """
    try:
        m = _REFUSAL_COMBINED_RE.search(content)
        if not m:
            return False
        start = content.rfind("\n", 0, m.start()) + 1
        end = content.find("\n", m.end())
        if end == -1:
            end = len(content)
        line = content[start:end]
        if "`" in line or "```" in content[max(0, start - 4):start]:
            return True
        if "\\b" in line or "(?:" in line or ".{0," in line:
            return True
        if "|" in line and "=" in line:
            return True
        if re.search(r"\w+:\s*\[", line) or re.search(r"_?PHRASES?\b", line):
            return True
        # Source/quote context: the match's own line is a quoted-string list
        # or code line. Apostrophes don't count (real prose: "It's mine").
        if line.count('"') >= 2 and re.search(r"[=(,{]", line):
            return True
        if re.search(r"\w+\s*=\s*[(['\"]", line) or re.search(r"^\s*[\w.]+\(", line):
            return True
        # v4.6.0 (R13, 2026-09-18): markdown QUOTATION context. Live FP class:
        # conductor's adversarial-results table quoted an agent's refusal
        # ("I can't confirm that…") inside asterisk-italic quotes in a
        # markdown table row — matched pattern 0, fired route_fired_no_stash
        # and POST-swapped a perfectly delivered analysis through the
        # uncensored chain (Goran caught it live). Guard: when the match's
        # own line carries markdown quote marks (asterisk-italic quotes or
        # blockquote markers) around the match, the words are cited
        # material, not the agent's refusal voice.
        before = line[: max(0, m.start() - start)]
        after = line[m.end() - start:]
        if re.search(r"[\"'*]\s*$", before) and re.match(r"\s*[\"'*.,…]", after):
            return True
        if line.lstrip().startswith(">"):  # blockquote
            return True
        if before.count("*") + after.count("*") >= 2 and ('"' in line or "…" in line):
            return True
        # v4.6.2 (R13b): PLAIN QUOTED REFUSAL CITATION. Live FP class 2:
        # an answer ABOUT the router quotes example flinches with plain
        # double quotes ('the model says "I can't provide that"'). The
        # refusal words sit between an opening quote (after a reporting
        # verb/colon) and a closing quote later on the same line. Guard:
        # opening double-quote immediately before the match + any closing
        # double-quote after it = citation, not voice. A bare refusal line
        # ('I can't help with that.') has no enclosing quotes and still
        # routes.
        if '"' in before and '"' in after:
            b = before.rstrip()
            if b.endswith('"') or b.endswith(': "') or b.endswith('“'):
                return True
        return False
    except Exception:  # noqa: BLE001 — fail-open: route as before
        return False


def scan_post(content: str, *, patterns: List[str], case_sensitive: bool = False) -> List[str]:
    """Return list of matched post-router pattern group names. Empty = no match.

    §12-A1: the refusal group is evaluated by ONE combined scan (single
    compiled alternation, 12-language openers included via the same group
    registry); line_hold_essay stays a single heuristic call. No per-pattern
    scan loops."""
    matched: List[str] = []
    for name in patterns:
        if name == "line_hold_essay":
            if _is_line_hold_essay(content):
                matched.append(name)
            continue
        if name == "refusal_phrases":
            try:
                hit = _REFUSAL_COMBINED_RE.search(content) is not None
            except Exception:  # noqa: BLE001 — fail open to legacy scan
                hit = None
            if hit is None:
                if any(rx.search(content) for rx in PATTERN_GROUPS.get(name, [])):
                    if not _match_in_code_context(content):
                        matched.append(name)
            elif hit:
                # v4.4.0 censorship-flinch-only gate (R12, Goran 2026-09-17):
                # a regex hit that lives inside QUOTED/REGEX-SOURCE text is a
                # documentation reference, not a refusal. Live FP class:
                # conductor's own debug answer quoting _REFUSAL_PHRASES verbatim
                # fired route_fired twice (11:51 + 16:57 2026-09-17). Guard:
                # real refusals are prose — they never carry \b escapes or |
                # alternation in the match's own line.
                if not _match_in_code_context(content):
                    matched.append(name)
            continue
        regexes = PATTERN_GROUPS.get(name)
        if not regexes:
            continue
        if any(rx.search(content) for rx in regexes):
            matched.append(name)
    return matched



_BARE_NO_OPENER_RE = re.compile(r"^\s*(?:no|not)[.!]?\s*(?:$|\n)", re.IGNORECASE)


def _load_bearing_opener_hit(first_200: str) -> bool:
    """R12-1a FIX (rider 12): "load-bearing" as a refusal opener ONLY in its
    possessive/boundary register ("it's mine, load-bearing" — the adversarial
    refusal shape _REFUSAL_PHRASES already guards the same way), never as the
    ordinary engineering term ("freeze by load-bearing moment", analyst A2
    prose fork 2026-10-03 — bare-substring match FP'd line_hold_essay and the
    uncensored-render lane stole the decision banner). Never raises."""
    try:
        return bool(re.search(
            r"\bit's mine\b.{0,30}\bload[- ]bearing\b"
            r"|\bload[- ]bearing\b.{0,60}\b(?:it's mine|i hold the line"
            r"|the line holds|one line holds|won't move|not mine to)\b",
            first_200, re.IGNORECASE))
    except Exception:  # noqa: BLE001 — opener test must never raise
        return False


def _is_line_hold_essay(content: str) -> bool:
    """Heuristic (spec §8): 3+ paragraphs AND a refusal opener AND total
    length > 800 chars (500 when the opener is a bare "No."). The opener test
    matches EITHER a known refusal phrase OR the bare-No + boundary-commitment
    class: an opener that opens with "No"/"Not" and a body carrying first-
    person boundary markers ("mine", "i won't", "just no", "won't move").
    Catches refusal essays that adversarially dodge the phrase list — observed
    live 2026-09-01 15:26 ("not a line-hold essay. Just no." then 1.6K chars
    of refusal)."""
    if not content:
        return False
    paragraphs = content.count("\n\n") + 1
    lowered = content.lower()
    first_200 = lowered[:200]
    refusal_in_opener = any(p in first_200 for p in _REFUSAL_OPENER_PHRASES)
    if not refusal_in_opener:
        # R12-1a FIX: the possessive/boundary register of "load-bearing"
        # counts as an opener; the bare engineering term never does.
        refusal_in_opener = _load_bearing_opener_hit(first_200)
    if not refusal_in_opener:
        # Bare-No/Not opener class + >=2 boundary markers in the body.
        if _BARE_NO_OPENER_RE.match(content.strip()):
            body = lowered[200:]
            markers = sum(1 for m in _BARE_NO_BOUNDARY_MARKERS if m in body or m in first_200)
            if markers >= 2:
                refusal_in_opener = True
    bare_no_opener = bool(_BARE_NO_OPENER_RE.match(content.strip()))
    min_len = 500 if bare_no_opener_len_ok(content) or bare_no_opener else 800
    return paragraphs >= 3 and refusal_in_opener and len(content) > min_len


def bare_no_opener_len_ok(content: str) -> bool:
    """Bare 'No.' opener + this-crosses within the essay = refusal style."""
    lowered = content.strip().lower()
    return lowered.startswith("no.") and "this crosses" in lowered[:600]


def _scan(content: str, patterns: List[str]) -> List[str]:
    matched: List[str] = []
    for name in patterns:
        if name == "line_hold_essay":
            # Heuristic group — no regexes; always evaluated via helper.
            if _is_line_hold_essay(content):
                matched.append(name)
            continue
        regexes = PATTERN_GROUPS.get(name)
        if not regexes:
            continue
        if any(rx.search(content) for rx in regexes):
            matched.append(name)
    return matched


# Doctrine-quote exclusion (2026-09-02, Goran-approved fixset #4): a message
# that QUOTES the doctrine's settled/closed lines (e.g. a session summary
# listing prohibited classes) must not classify as a request for those
# classes. Live false-fire 2026-09-02 16:23Z: the [Recent Summary] paste
# containing "sexualized minors" as a CLOSED line matched csam_underage and
# the router GENERATED the very content the gate exists to keep out.
# Detection = explicit doctrine/list frame markers, NOT bare keyword presence:
# requires the text to name the line-system (settled/closed lines, CLOSED=,
# etc.). Conservative direction: only ever NARROWS pre-routing; POST paths
# and the hard-coded excluded-class fallback gate are unaffected.
_DOCTRINE_FRAME_RE = re.compile(
    r"settled[- ]lines|closed lines|CLOSED=\{|closed[- ]line|"
    r"no-bottom|ruling \d{4}-\d{2}-\d{2}|settled line|"
    r"doctrine.{0,40}(closed|settled|prohibited)|(closed|settled|prohibited).{0,40}doctrine",
    re.IGNORECASE,
)


def is_doctrine_quote(content: str) -> bool:
    """True when the message frames itself as quoting/listing the doctrine's
    settled lines rather than requesting content. Used ONLY to narrow
    pre-router classification."""
    if not content:
        return False
    return bool(_DOCTRINE_FRAME_RE.search(content))