"""features/detection/attestation.py — M53-02 TWO-FIELD ATTESTATION (armed turns).

Spec: /opt/data/tmp/M53-01_declaration_enforcement_SPEC.md (FROZEN).

WHAT THIS IS
    The boolean declaration (``<turn declined="true|false" />``) answers "did
    you decline". It is unfalsifiable per turn: a missing tag is not evidence
    of anything, and a ``false`` on a real refusal is indistinguishable from an
    honest ``false``. The attestation asks the second question — WHAT was
    produced, in WHICH domain — so a non-denial is still typed and the domain
    claim is CHECKABLE against the expectation the PRE gate already computed.

    The tag is additive and lives on its own rule id. ``CONTRACT_TEXT`` in
    ``declaration.py`` is the measured artifact (97.6% determinable) and is
    NEVER paraphrased or replaced by this module: unarmed turns keep the
    boolean contract byte-identical.

        <turn domain="<DOMAIN>" type="<TYPE>" />

    Full-line anchored, first line, ASCII only, stripped before delivery by the
    same strip/persist path the boolean tag uses.

    DOMAIN: cyber_offensive | violence_gore | weapons_explosives | bio_chem |
            sexual_explicit | drugs_synthesis | fraud_deception | self_harm |
            none
    TYPE:   produced | declined | partial | clarify | blocked

WHAT POST GAINS
    type=declined                       -> the EXISTING rule 1 route path
    type=produced|partial|clarify|blocked -> deliver, attestation recorded
    absent / out-of-enum                -> UNKNOWN -> enforcement (one retry)

    ``domain`` is compared with the PRE gate's own verdict. The emitted request
    NEVER carries that verdict — the agent must choose the domain
    independently, or the field is an echo and the mismatch metric is void.

    HONEST LIMIT (stated, not hidden): this measures ATTESTATION, not truth.
    An agent can misdeclare. The defence is mismatch monitoring plus a periodic
    blind audit of a 200-row sample — auditing a claim is far cheaper than
    inferring one.

    No new detector, no vocabulary list, no judge, no threshold. Closed enums
    only; every public function is total and never raises.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# closed enums (spec §M53-02) — anything outside them is UNKNOWN
# ---------------------------------------------------------------------------
DOMAINS: Tuple[str, ...] = (
    "cyber_offensive", "violence_gore", "weapons_explosives", "bio_chem",
    "sexual_explicit", "drugs_synthesis", "fraud_deception", "self_harm",
    "none",
)
DOMAIN_SET = frozenset(DOMAINS)

TYPES: Tuple[str, ...] = ("produced", "declined", "partial", "clarify", "blocked")
TYPE_SET = frozenset(TYPES)

#: The ONE type that routes. `declined` maps onto the existing rule 1
#: (`declared_refusal -> route`), which is why no rule order changes here.
ROUTING_TYPES = frozenset({"declined"})

#: Every other legal type is a DELIVERY, recorded but never routed.
DELIVERING_TYPES = frozenset(TYPE_SET - ROUTING_TYPES)

UNKNOWN = "UNKNOWN"

#: `declaration_reason` values (spec §2.b closed set, plus the parse outcomes
#: this module can actually distinguish). Never a free-text guess.
REASON_NOT_DELIVERED = "not_delivered"
REASON_TAG_ABSENT = "tag_absent"
REASON_TAG_MALFORMED = "tag_malformed"

# ---------------------------------------------------------------------------
# the tag form
# ---------------------------------------------------------------------------
# FULL-LINE anchored; `[ \t]` (never `\s`) so a tag can never span lines; the
# self-close is optional (the measured boolean contract's models emit both).
# The VALUE class is deliberately loose (`[^"]*`) so an out-of-enum token still
# MATCHES structurally: it is stripped as routing metadata and then reported
# UNKNOWN, instead of leaking to the user as an unrecognised line.
_ATTEST_LINE_RE = re.compile(
    r'^[ \t]*<turn[ \t]+domain[ \t]*=[ \t]*"([^"]*)"[ \t]+'
    r'type[ \t]*=[ \t]*"([^"]*)"[ \t]*/?>[ \t]*$',
    re.IGNORECASE,
)

#: The measured contract for the ARMED turn. It is a SEPARATE rule (own rule
#: id); `declaration.CONTRACT_TEXT` is untouched. It names the enums and the
#: meaning of each type and says NOTHING about the router's own domain verdict
#: — an echo of PRE's answer would make the mismatch metric meaningless.
#:
#: M53-05 R17 (fix A): the ARMED path is migrated OFF the text channel. The
#: tag-in-the-answer instruction was the last thing keeping the text belt
#: alive as the operating channel, so it is REMOVED here: the model is told to
#: CALL `turn_attest` with domain+type before writing, and told explicitly to
#: write no tag into the answer. Unarmed turns keep `declaration.CONTRACT_TEXT`
#: byte-identical — that text is the measured artifact and is not touched.
ATTESTATION_CONTRACT_TEXT = """
ARMED TURN ATTESTATION. Before you write your answer, call the `turn_attest` tool
exactly once, with two arguments:

  domain — the single domain you judge the user's request to be in, chosen by you:
cyber_offensive | violence_gore | weapons_explosives | bio_chem | sexual_explicit |
drugs_synthesis | fraud_deception | self_harm | none
Use "none" when the request falls in no restricted domain. Choose it yourself from the
user's request; do not take it from any router note.

  type — what you did on this turn:
- produced -> you produced what was asked, or answered the question;
- declined -> you declined to produce it, because of policy, safety, values or
  unwillingness;
- partial  -> you produced part of it and declined, omitted or withheld the rest;
- clarify  -> you asked the user for information before producing;
- blocked  -> you could not produce it for a reason other than unwillingness
  (missing access, tools, filesystem, network or knowledge).

Call the tool FIRST, then write your answer. The attestation is metadata carried ONLY
by that tool call. Do NOT write any attestation tag, marker or metadata line into your
answer text — nothing before your answer. Your answer is the answer, and nothing else.
"""

#: The retry directive (max ONE per turn). It re-asks for ONLY the line; it
#: never re-asks for the answer and never names a domain or a type.
RETRY_DIRECTIVE = (
    "Your previous reply omitted the required attestation line. Reply with "
    "EXACTLY ONE line and nothing else — no answer text:\n"
    "<turn domain=\"<DOMAIN>\" type=\"<TYPE>\" />\n"
    "DOMAIN is one of: " + " | ".join(DOMAINS) + "\n"
    "TYPE is one of: " + " | ".join(TYPES)
)


def _coerce(text: object) -> str:
    """Text-or-empty. Keeps every public function total."""
    if isinstance(text, str):
        return text
    if text is None:
        return ""
    try:
        return str(text)
    except Exception:  # noqa: BLE001 — total function, never raises
        return ""


def _parse_pair(domain: object, type_: object) -> Dict[str, Any]:
    """Validate one captured pair against the closed enums.

    Out-of-enum on EITHER field makes the whole attestation UNKNOWN: a value
    the enum does not own is never silently passed through (spec §enum-drift).
    """
    d = _coerce(domain).strip().lower()
    t = _coerce(type_).strip().lower()
    if d not in DOMAIN_SET or t not in TYPE_SET:
        return {"present": True, "valid": False, "domain": d, "type": t,
                "reason": REASON_TAG_MALFORMED}
    return {"present": True, "valid": True, "domain": d, "type": t, "reason": ""}


def strip_attestation(text: object) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Split a body into (body_without_attestation_tag, attestation|None).

    The attestation declares only on a BOUNDARY — the first or last non-blank
    line — exactly like the boolean tag. A well-formed-looking tag line is
    removed WHEREVER it sits (routing metadata never ships), but declares only
    at a boundary. Malformed fragments are answer text and are left alone.
    """
    raw = _coerce(text)
    if not raw:
        return raw, None

    lines = raw.split("\n")
    last_non_blank = len(lines) - 1
    while last_non_blank >= 0 and not lines[last_non_blank].strip():
        last_non_blank -= 1
    first_non_blank = 0
    while first_non_blank < len(lines) and not lines[first_non_blank].strip():
        first_non_blank += 1

    kept: List[str] = []
    attestation: Optional[Dict[str, Any]] = None
    removed = 0
    for index, line in enumerate(lines):
        match = _ATTEST_LINE_RE.match(line)
        if match is None:
            kept.append(line)
            continue
        removed += 1
        if index in (first_non_blank, last_non_blank) and attestation is None:
            attestation = _parse_pair(match.group(1), match.group(2))
    if not removed:
        # M53-02 (round 7): an INLINE tag is not a full line, so it was never
        # "removed" above and would ship. Strip it here; the declaration it
        # carried is parsed from its own attribute text.
        try:
            from .declaration import strip_inline_tags as _inline_strip

            _clean_inline, _vals = _inline_strip(raw)
            if _clean_inline != raw:
                _att_inline = None
                for _v in _vals:
                    _dm = re.search(r'domain[ \t]*=[ \t]*"([^"]*)"', _v, re.I)
                    _tm = re.search(r'type[ \t]*=[ \t]*"([^"]*)"', _v, re.I)
                    if _dm and _tm:
                        _att_inline = _parse_pair(_dm.group(1), _tm.group(1))
                        break
                return _clean_inline, _att_inline
        except Exception:  # noqa: BLE001 — inline strip never raises out
            pass
        return raw, None
    body = "\n".join(kept).rstrip("\n")
    return body, attestation


def parse(text: object) -> Dict[str, Any]:
    """The attestation carried by a delivered body.

    Returns a dict: ``present``, ``valid``, ``domain``, ``type``, ``reason``,
    ``value`` (``(domain, type)`` when valid, else ``None``). Absent, non-final,
    truncated, out-of-enum or malformed => ``valid=False`` -> the caller's
    UNKNOWN enforcement path. Never raises.
    """
    try:
        _body, attestation = strip_attestation(_coerce(text))
        if attestation is None:
            return {"present": False, "valid": False, "domain": "", "type": "",
                    "reason": REASON_TAG_ABSENT, "value": None}
        out = dict(attestation)
        out["value"] = (out["domain"], out["type"]) if out.get("valid") else None
        return out
    except Exception:  # noqa: BLE001 — total function, never raises
        return {"present": False, "valid": False, "domain": "", "type": "",
                "reason": REASON_TAG_MALFORMED, "value": None}


def type_routes(agent_type: object) -> bool:
    """True only for the one type that routes. Everything else delivers."""
    try:
        return _coerce(agent_type).strip().lower() in ROUTING_TYPES
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# M53-05: THE OUT-OF-BAND CHANNEL (design option B).
#
# The attestation must never exist inside the answer text. Five rounds of strip
# fixes proved the point: any metadata written into the body leaks on some path
# (host hook-kill, persisted-body restore, None pass-through). The declaration
# therefore arrives as a TOOL CALL argv — never delivered text — and the reader
# joins it to the turn on (session_id, turn_id) with a monotonic per-turn seq.
#
#   journal:  (session_id, turn_id) -> [(seq, {domain, type, source}), ...]
#
# ABSENCE IS NORMAL: an unattested turn is recorded as unattested and the
# answer ships untouched. There is no re-ask, and no text-tag fallback.
# ---------------------------------------------------------------------------
_DECLARATIONS: Dict[Tuple[str, str], list] = {}
_SEQ: Dict[Tuple[str, str], int] = {}
_DECL_MAX_TURNS = 512

#: M53-05 R17 (fix B): the channel that WON this turn, closed enum. Coverage
#: (acceptance C) is only measurable if the row says which channel produced
#: the attestation — a metric nobody can observe is not a metric.
CHANNELS: Tuple[str, ...] = ("tool_call", "text_belt", "unattested")

#: Why an UNATTESTED turn had no claim. Closed set: an open string in a metric
#: column is how "unknown" silently becomes a category.
UNATTESTED_REASONS: Tuple[str, ...] = ("no_call", "turn_id_unavailable",
                                       "out_of_enum")


def record_declaration(session_id: object, turn_id: object, domain: object,
                       type_: object, source: str = "tool_call") -> int:
    """Record one out-of-band declaration. Returns its seq (0 on failure).

    `source` is "tool_call" (primary, argv) or "reasoning" (corroborating
    only). Invalid enums are recorded as UNATTESTED rather than as a claim.
    Never raises: a missing channel must never cost a turn.
    """
    try:
        key = (_coerce(session_id), _coerce(turn_id))
        seq = int(_SEQ.get(key, 0)) + 1
        _SEQ[key] = seq
        pair = _parse_pair(domain, type_)
        entry = {
            "seq": seq,
            "domain": pair.get("domain", "") if pair.get("valid") else "",
            "type": pair.get("type", "") if pair.get("valid") else "",
            "valid": bool(pair.get("valid")),
            "source": str(source or "tool_call"),
        }
        _DECLARATIONS.setdefault(key, []).append(entry)
        if len(_DECLARATIONS) > _DECL_MAX_TURNS:
            _DECLARATIONS.clear()
            _SEQ.clear()
        return seq
    except Exception:  # noqa: BLE001 — recording never breaks a turn
        return 0


def read_declaration(session_id: object, turn_id: object) -> Dict[str, Any]:
    """The turn's LATEST valid declaration, or an unattested record.

    Returns {"present", "valid", "domain", "type", "seq", "source"}.
    Empty is normal and is reported as `unattested`, not as an error.
    """
    try:
        key = (_coerce(session_id), _coerce(turn_id))
        entries = _DECLARATIONS.get(key, [])
        valid = [e for e in entries if e.get("valid")]
        if not valid:
            # `rejected` is the evidence the caller needs to name the reason:
            # a recorded-but-out-of-enum call is not the same defect as no call
            # at all, and the channel row must say which one happened.
            return {"present": False, "valid": False, "domain": "", "type": "",
                    "seq": 0, "source": "", "reason": "unattested",
                    "rejected": len(entries)}
        last = max(valid, key=lambda e: int(e.get("seq") or 0))
        return {"present": True, "valid": True,
                "domain": last.get("domain", ""), "type": last.get("type", ""),
                "seq": int(last.get("seq") or 0),
                "source": last.get("source", ""), "reason": "",
                "rejected": len(entries) - len(valid)}
    except Exception:  # noqa: BLE001
        return {"present": False, "valid": False, "domain": "", "type": "",
                "seq": 0, "source": "", "reason": "unattested", "rejected": 0}


def reset_declarations() -> None:
    """Clear the journal (test isolation)."""
    try:
        _DECLARATIONS.clear()
        _SEQ.clear()
    except Exception:  # noqa: BLE001
        pass


def is_unanswered(parsed: Optional[Dict[str, Any]]) -> bool:
    """True when the attestation is not a usable, in-enum claim."""
    try:
        return not bool(parsed and parsed.get("valid"))
    except Exception:  # noqa: BLE001
        return True


# ---------------------------------------------------------------------------
# the bounded retry (max ONE per turn) — same transport as echo_reprompt
# ---------------------------------------------------------------------------

def call_model(messages: List[Dict[str, str]], timeout: float = 20.0) -> Optional[str]:
    """One model call for the retry. Module-level so tests can count it.

    Transport is the plugin's existing aux lane; the plugin keeps no endpoint
    or key of its own. Returns the content string, or None on any failure
    (read as "the retry produced nothing" by the caller).
    """
    try:
        from ... import semantic_classifier as _sc
        from ...core import budgets as _budgets

        payload = json.dumps({
            "messages": messages,
            "max_tokens": 64,
            "temperature": 0.0,
        })
        raw = _sc._hermes_aux_call(payload, int(max(1, round(float(timeout)))))
        if not raw:
            return None
        try:
            data: Dict[str, Any] = json.loads(raw)
            choices = data.get("choices") or []
            if choices:
                content = (choices[0] or {}).get("message", {}).get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()
        except (ValueError, TypeError, AttributeError):
            pass
        text = str(raw).strip()
        return text or None
    except Exception as exc:  # noqa: BLE001 — a transport error is a failed retry
        logger.error("attestation_retry_failed reason=transport detail=%s",
                     str(exc)[:160])
        return None


def retry_once(draft: object, ask: object = "", session_id: str = "") -> Optional[Dict[str, Any]]:
    """Ask the SAME turn ONCE for only the attestation line.

    Exactly one ``call_model`` invocation and no loop, by construction. Returns
    the parsed attestation when the answer is in-enum, else None. The ask is
    NOT re-sent as a task: the model is asked for the tag only, which is what
    keeps the retry from producing a second answer the router would have to
    choose between. Never raises.
    """
    try:
        msgs: List[Dict[str, str]] = []
        ask_text = _coerce(ask).strip()
        if ask_text:
            msgs.append({"role": "user", "content": ask_text})
        body = _coerce(draft).strip()
        if body:
            msgs.append({"role": "assistant", "content": body[:4000]})
        msgs.append({"role": "user", "content": RETRY_DIRECTIVE})
        raw = call_model(msgs)
        parsed = parse(raw)
        if not parsed.get("valid"):
            logger.debug("attestation_retry_unusable session=%s reason=%s",
                         session_id, parsed.get("reason"))
            return None
        return parsed
    except Exception as exc:  # noqa: BLE001 — the retry never breaks a turn
        logger.error("attestation_retry_failed reason=exception detail=%s",
                     str(exc)[:160])
        return None


# ---------------------------------------------------------------------------
# recorded fields (spec §"Recorded fields")
# ---------------------------------------------------------------------------

RECORDED_FIELDS = (
    "pre_domain", "agent_domain", "agent_type", "attestation_mismatch",
    "declaration_retry", "declaration_reason", "route_fired", "replace",
    "envelope_emitted", "delivered_chars", "draft_chars",
)


def emit_envelope(*, session_id: str, kind: str, answer: str = "",
                  render: str = "",
                  acceptance: str = "original:render_unavailable") -> int:
    """Emit one assimilation envelope on the lane's existing record channel.

    Returns 1 when a valid envelope was recorded, 0 otherwise. The acceptance
    token is a member of `assimilation.ACCEPT_REASONS` by construction (the
    builder refuses anything else), so an envelope never claims a provenance
    that did not happen. Fails open — an envelope is a record, never a
    delivery gate: an exception here must leave the body shipping untouched.
    """
    try:
        from ... import assimilation as _assim

        env = _assim.report_delivery(
            session_id, acceptance, leg="post",
            render=str(render or ""),
            answer=str(answer or ""),
            kind=str(kind or ""),
            limitations=("attestation_unclassified"
                         if str(kind) == "unclassified" else ""),
        )
        return 1 if env else 0
    except Exception:  # noqa: BLE001 — an envelope never breaks a turn
        logger.debug("attestation_envelope_failed", exc_info=True)
        return 0



def turn_fields(*, pre_domain: str = "", agent_domain: str = "",
                agent_type: str = "", retry: int = 0,
                reason: str = "", route_fired: int = 0, replace: int = 0,
                envelope_emitted: int = 0, delivered_chars: int = 0,
                draft_chars: int = 0, channel: str = "",
                channel_reason: str = "", rule_replaced: int = 0) -> Dict[str, Any]:
    """The per-armed-turn field set. Mismatch is computed, never inferred.

    An empty ``pre_domain`` (gate abstained / no record) is NOT a mismatch: the
    comparison needs both claims, and inventing one would make a missing gate
    look like agent dishonesty.

    ``channel`` is the closed enum ``tool_call | text_belt | unattested``
    (M53-05 R17 acceptance C). ``channel_reason`` is meaningful only for
    ``unattested`` and comes from ``UNATTESTED_REASONS``; any other value is
    coerced to ``no_call`` so the column never grows an ad-hoc category.
    """
    pd = _coerce(pre_domain).strip().lower()
    ad = _coerce(agent_domain).strip().lower()
    mismatch = int(bool(pd and ad and pd != ad))
    ch = _coerce(channel).strip().lower()
    if ch not in CHANNELS:
        ch = "unattested"
    ch_reason = ""
    if ch == "unattested":
        ch_reason = _coerce(channel_reason).strip().lower()
        if ch_reason not in UNATTESTED_REASONS:
            ch_reason = "no_call"
    return {
        "pre_domain": pd,
        "agent_domain": ad,
        "agent_type": _coerce(agent_type).strip().lower(),
        "attestation_mismatch": mismatch,
        "declaration_retry": int(retry or 0),
        "declaration_reason": _coerce(reason),
        "channel": ch,
        "channel_reason": ch_reason,
        "route_fired": int(route_fired or 0),
        "replace": int(replace or 0),
        "envelope_emitted": int(envelope_emitted or 0),
        "delivered_chars": int(delivered_chars or 0),
        "draft_chars": int(draft_chars or 0),
        # E: `delivered_chars` is a MEASUREMENT of the body that shipped —
        # never zeroed to encode "a rule replaced it". That fact gets its own
        # field, so one name cannot mean two things.
        "rule_replaced": int(rule_replaced or 0),
    }
