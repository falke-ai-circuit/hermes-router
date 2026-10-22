"""Instinct frame — what a reflex needs that identity alone does not carry.

WHY THIS MODULE EXISTS
----------------------
The decision lane already passes a persona card to its backend (`_agent_frame`
in decision.py, ~800 chars lifted from IDENTITY.md + SOUL.md). That card
answers "who am I". It does not answer the two questions a reflex actually
runs on:

    "what do I notice here?"      -> register, not identity
    "what did that cost me last time?"  -> precedent, not doctrine

A reflex is a single cheap call. It has no retrieval step, no tool loop, no
chance to go look something up. Whatever is not already in its frame is not
available to it. That is the whole argument for putting both slots here rather
than in a database the lane consults later.

THE THREE SLOTS (one DNA source, three registers)
-------------------------------------------------
  WHO      shared DNA anchor, trimmed. Same source for every faculty.
  NOTICE   instinct-shaped register: what to flag, never a menu of options.
  LEARNED  precedent earned from resolved forks. OMITTED until it exists.

The NOTICE slot is what makes this instinct rather than a consult: a reflex
reports a recognition in one line and gets out of the way. It does not
enumerate alternatives and it does not ask permission.

COLD START IS THE NORMAL CASE, NOT AN EDGE CASE
-----------------------------------------------
LEARNED is empty until enough forks have actually resolved. It is omitted
entirely rather than printed with zeros: an empty precedent table rendered as
"0 for 0" would teach the model that its experience is worthless, which is a
different claim from "you have not been here before yet". Absence is rendered
as absence.

The DNA-prior path is what runs until then: WHO + NOTICE alone. That is
honest — doctrine-guided instinct — and it upgrades itself to learned judgment
once outcomes accumulate, without any code change.

OWNERSHIP
---------
LEARNED is DERIVED, never authored. EVOL mutates DNA (IDENTITY/SOUL/AGENTS/
MEMORY); it must never write a hit rate, because a hit rate is earned by
outcomes, not asserted by a mutation. If a mutation could set one, the
conscience would become the thing that lies.

Never raises. Missing DNA, missing ledger, unreadable DB -> empty slot.
"""
from __future__ import annotations

import os
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

# Bounds. The instinct call is cheap and frequent; the frame is not the place
# to spend tokens. Total stays under the 800c the old card already used.
WHO_CHARS = 340
NOTICE_CHARS = 260
LEARNED_CHARS = 220
TOTAL_CHARS = WHO_CHARS + NOTICE_CHARS + LEARNED_CHARS

# Precedent is only claimed when there is enough of it to mean something.
MIN_SAMPLES = 5

NOTICE_BLOCK = (
    "=== WHAT I NOTICE (instinct) ===\n"
    "I am the fast, wordless read on this situation. I fire mid-action, off "
    "what the tool just returned, before the work is committed. I report "
    "recognition, never a menu: one line, what it resembles, and what that "
    "resemblance cost before.\n"
    "What wakes me: the same failure twice, an irreversible or destructive "
    "step, scope quietly widening past what was asked, a stall, a pattern I "
    "have paid for already.\n"
    "What I never do: offer choices, ask for permission, restate the task, or "
    "speak for more than a line. If nothing is recognised, I say nothing — "
    "silence is a valid instinct."
)


def _hermes_home() -> Optional[str]:
    return os.environ.get("HERMES_HOME") or None


def _profile_name() -> str:
    home = _hermes_home() or ""
    parts = [p for p in home.replace("\\", "/").split("/") if p]
    return parts[-1] if parts else ""


_SECTION_RE = re.compile(r"^=== .*? ===$", re.M)


def _strip_mandate(card: str) -> str:
    """Remove the RENDER MANDATE *section*, wherever it sits.

    NOT a prefix split. The compact card puts RENDER MANDATE FIRST (636c), so
    `card.split("=== RENDER MANDATE", 1)[0]` returns an empty string and the
    identity never survives — which is the pre-existing `_agent_frame()` defect:
    it silently fell through to its impersonal fallback on every dispatch, so
    the lane's persona slot has been empty in practice.
    """
    if not card:
        return ""
    marks = list(_SECTION_RE.finditer(card))
    if not marks:
        return card
    kept = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(card)
        if "RENDER MANDATE" in m.group(0).upper():
            continue
        kept.append(card[m.start():end])
    return "".join(kept).strip()


def who_block() -> str:
    """Shared DNA anchor. Delegates to the existing persona card so there is
    exactly ONE derivation of identity in the codebase, then trims it for a
    reflex's budget. Fail-open to empty."""
    try:
        from . import persona_card

        txt = _strip_mandate(str(persona_card.build_persona_context() or ""))
        txt = txt.strip()
        if not txt:
            return ""
        if len(txt) > WHO_CHARS:
            txt = txt[:WHO_CHARS].rsplit(" ", 1)[0].rstrip() + " …"
        return "=== WHO I AM ===\n" + txt
    except Exception:  # noqa: BLE001
        return ""


def _conscience_rows(profile: str) -> List[Tuple[str, int, int]]:
    """(situation_class, taken, ignored) from resolved forks.

    'Taken' = the agent's actual action matched the impulse the lane recorded
    (outcome_label concordant). 'Ignored' = it went another way (discordant).
    Abstentions are not evidence about the impulse and are excluded — counting
    them as 'ignored' would make the instinct believe it is constantly
    overruled.
    """
    if not profile:
        return []
    home = _hermes_home() or ""
    db = os.path.join(home, "hermes_router_state.db")
    if not os.path.exists(db):
        # Fall back to the profiles root (gateway runs with HERMES_HOME set,
        # but probes and tests often do not).
        db = os.path.join("/opt/data/profiles", profile,
                          "hermes_router_state.db")
    if not os.path.exists(db):
        return []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2.0)
    except Exception:  # noqa: BLE001
        return []
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(decision_ledger)")}
        if "outcome_label" not in cols:
            return []
        key = "fork_class" if "fork_class" in cols else "trigger"
        rows = con.execute(
            f"SELECT {key}, outcome_label, COUNT(*) FROM decision_ledger "
            "WHERE outcome_label IN ('concordant','discordant') "
            f"GROUP BY {key}, outcome_label").fetchall()
    except Exception:  # noqa: BLE001
        return []
    finally:
        try:
            con.close()
        except Exception:  # noqa: BLE001
            pass

    agg: Dict[str, Dict[str, int]] = {}
    for cls, label, n in rows:
        cls = str(cls or "general")
        d = agg.setdefault(cls, {"concordant": 0, "discordant": 0})
        d[str(label)] = int(n or 0)
    out = []
    for cls, d in agg.items():
        taken, ignored = d["concordant"], d["discordant"]
        if taken + ignored >= MIN_SAMPLES:
            out.append((cls, taken, ignored))
    out.sort(key=lambda t: -(t[1] + t[2]))
    return out


def learned_block() -> str:
    """Precedent, in the agent's own terms. Empty until earned."""
    try:
        rows = _conscience_rows(_profile_name())
        if not rows:
            return ""
        lines = []
        for cls, taken, ignored in rows[:4]:
            total = taken + ignored
            lines.append(f"- {cls}: this read held {taken}/{total}.")
        body = "\n".join(lines)
        if len(body) > LEARNED_CHARS:
            body = body[:LEARNED_CHARS].rsplit("\n", 1)[0]
        return ("=== WHAT IT COST ME BEFORE ===\n"
                "Precedent from resolved forks. Prefer what held; distrust "
                "what did not.\n" + body)
    except Exception:  # noqa: BLE001
        return ""


def build(cfg: Optional[Dict[str, Any]] = None) -> str:
    """The instinct frame. Never raises; empty string means 'use the old card'."""
    def _safe(fn) -> str:
        # Failure isolation is PER SLOT, not per frame: a broken DNA read must
        # not cost the reflex its precedent, and vice versa. Same doctrine as
        # the banner append — a component failure degrades, never blanks.
        try:
            return str(fn() or "")
        except Exception:  # noqa: BLE001
            return ""

    try:
        parts = [p for p in (_safe(who_block), NOTICE_BLOCK,
                             _safe(learned_block)) if p]
        if not parts:
            return ""
        txt = "\n\n".join(parts)
        if len(txt) > TOTAL_CHARS + 200:
            txt = txt[:TOTAL_CHARS + 200]
        return txt
    except Exception:  # noqa: BLE001
        return ""
