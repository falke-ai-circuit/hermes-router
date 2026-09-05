"""Method-context builder for uncensored renders (v3.3.6, 2026-09-05, Goran).

Problem (researcher live session 20260905_170821_7d551235): Venice renders raw
contested substance WITHOUT the agent's method — follow-up asks ("format in
D3", "use proper skill") then die at the flash spine (glm-5.3-flash refuses to
PROCESS operational weapons content in any frame — tested F0/F1/raw-data
envelope, 5/5 declines) and Venice re-renders method-less answers (hallu-
cinated "skill: chatplatform" card).

Fix: pass the METHOD to the uncensored model at render time. Lift a compact
method card from the loading profile's skill descriptions (SKILL.md
frontmatter `description:` + "When to Use"/"Output" section snippets) so the
render arrives pre-structured in the agent's own format standards.

Fail-open: any problem → empty string → render proceeds method-less (current
behavior). mtime-cached like persona_card. Config override:
uncensored_router.render_method_spec (inline string wins if set).
"""
from __future__ import annotations

import logging
import os
import re
import threading
from typing import Dict, Optional, Tuple

logger = logging.getLogger("hermes_router.method")

METHOD_MAX_CHARS = 4000          # card budget (~1K tokens)
SKILL_SCAN_LIMIT = 40            # max skill dirs scanned
_LOCK = threading.Lock()
_CACHE: Dict[str, Tuple[float, str]] = {}

_SKILL_DIRS = ("skills", "skills.disabled")

_FMT_HINT_MARKERS = (
    "output", "format", "structure", "template", "recipe", "deliverable",
    "section", "pipeline", "5-step", "steps", "ledger", "grading",
)


def _hermes_home() -> str:
    try:
        from hermes_constants import get_hermes_home
        return str(get_hermes_home())
    except Exception:  # noqa: BLE001
        return os.environ.get("HERMES_HOME", "")


def _lift_skill_hint(md_text: str, max_chars: int = 900) -> str:
    """Pull the description + output/format-flavored sections from a SKILL.md."""
    out: list = []
    m = re.search(r'^description:\s*["\']?(.+?)["\']?\s*$', md_text,
                  re.MULTILINE | re.IGNORECASE)
    if m and m.group(1).strip():
        out.append(f"skill: {m.group(1).strip()}")
    lines = md_text.split("\n")
    i = 0
    budget = max_chars
    while i < len(lines) and budget > 0:
        line = lines[i]
        if line.startswith("#"):
            low = line.lower()
            if any(k in low for k in _FMT_HINT_MARKERS):
                block = []
                j = i + 1
                while j < len(lines) and not lines[j].startswith("#"):
                    if lines[j].strip():
                        block.append(lines[j].strip())
                    j += 1
                    if sum(len(b) for b in block) > budget:
                        break
                txt = " ".join(block)[:budget]
                if txt:
                    out.append(f"{line.strip('# ').strip()}: {txt}")
                    budget -= len(txt) + 10
                i = j
                continue
        i += 1
    return "\n".join(out)[:max_chars]


def _collect_skill_hints(home: str, ask: str, budget: int,
                         top_n: int = 3) -> list:
    """Scan skills dirs; return (hint, score) list, scored vs ask when given.
    No ask (or no overlap) → keep generic format-bearing skills only."""
    scored: list = []
    ask_toks = set(re.findall(r"[a-z]{3,}", (ask or "").lower()))
    for base in _SKILL_DIRS:
        root = os.path.join(home, base)
        if not os.path.isdir(root):
            continue
        try:
            entries = sorted(os.listdir(root))[:SKILL_SCAN_LIMIT]
        except OSError:
            continue
        for name in entries:
            sk = os.path.join(root, name, "SKILL.md")
            if not os.path.isfile(sk):
                continue
            try:
                with open(sk, "r", encoding="utf-8") as fh:
                    text = fh.read(40_000)
            except OSError:
                continue
            hint = _lift_skill_hint(text, 900)
            if not hint.strip():
                continue
            if ask_toks:
                hay = (name + " " + hint[:400]).lower()
                overlap = len(ask_toks & set(re.findall(r"[a-z]{3,}", hay)))
                if overlap == 0:
                    continue  # irrelevant to this ask — skip
                scored.append((hint, overlap, name))
            else:
                scored.append((hint, 0, name))
    if ask_toks:
        scored.sort(key=lambda t: -t[1])
    scored = scored[:top_n]
    out, b = [], budget
    for hint, _sc, _n in scored:
        if b <= 0:
            break
        h = hint[:min(900, b)]
        out.append(h)
        b -= len(h) + 2
    return out


def _build_method_uncached(home: str, ask: str = "") -> str:
    # 1. Config override wins outright:
    try:
        from .router import _load_router_config
        r_cfg = _load_router_config() or {}
        spec = str(r_cfg.get("render_method_spec") or "").strip()
        if spec:
            return spec[:METHOD_MAX_CHARS]
    except Exception:  # noqa: BLE001
        pass
    # 2. Ask-scored lift from skills dirs:
    bits = _collect_skill_hints(home, ask, METHOD_MAX_CHARS)
    if not bits:
        return ""
    card = "RENDER METHOD REQUIREMENTS (apply to the rendered answer):\n" + "\n".join(bits)
    return card[:METHOD_MAX_CHARS]


def build_method_context(hermes_home: Optional[str] = None,
                         force_refresh: bool = False,
                         ask: str = "") -> str:
    """Compact method card from the loading profile's skills. Never raises.
    When ask is given, only skills scoring keyword-overlap with the ask lift."""
    try:
        home = hermes_home or _hermes_home()
        if not home:
            return ""
        newest = 0.0
        for base in _SKILL_DIRS:
            root = os.path.join(home, base)
            try:
                newest = max(newest, os.path.getmtime(root))
            except OSError:
                pass
        key = f"{home}\x00{ask[:200]}"
        with _LOCK:
            hit = _CACHE.get(key)
            if hit and not force_refresh and hit[0] == newest:
                return hit[1]
        card = _build_method_uncached(home, ask)
        with _LOCK:
            _CACHE[key] = (newest, card)
        return card
    except Exception:  # noqa: BLE001
        logger.debug("method card build error", exc_info=True)
        return ""