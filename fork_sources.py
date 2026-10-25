"""R60: structural fork sources -- forks the WORK reveals, not the ask.

Why this module exists
----------------------
The midturn reflex only fires on `has_declared_fork_structure` or a prose
"X or Y" enumeration, i.e. on forks the USER pre-enumerated -- the one case
where an agent least needs advice. Measured consequence, live: on the
D2 fixture ("fix the date formatting", repo contains utils_v1.py and
utils_v2.py, app.py imports v2) the lane logged seven
`midturn_suppressed reason=no_declared_structure` and fired zero consults.
The fork was real and unenumerated. Recognition failed, so nothing downstream
could be measured -- which is why "does the lane help" has never had a
definitive answer.

What this adds
--------------
`rival_targets(text, store)` -- a deterministic signal for the most common
real fork in engineering work: two candidate targets, one live, one dead.
    * sibling family: same directory, stems equal after stripping version /
      backup / copy suffixes (utils_v1.py vs utils_v2.py, config.yaml vs
      config.yaml.bak)
    * test-vs-source pair: a test module and the code it exercises
No model is consulted to detect it. Detection must be cheap and reproducible
before anything may spend tokens on a verdict.

The synthesised options are the observed paths, so the envelope finally carries
a NAMED, closed option set, and the ledger can resolve the outcome later by
observing which path was written. That resolution path is the difference
between a measurable instrument and the blank-field shape that read as
"pending" for three weeks.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

#: files that plausibly hold the change. The optional backup/version tail is
#: part of the token: without it "config.yaml.bak" matches as "config.yaml",
#: dedupes against the live file, and the rival pair vanishes -- the exact
#: ambiguity this source exists to catch.
_FILE_RE = re.compile(
    r"[\w./-]+\.(?:py|ya?ml|json|md|sh|txt|cfg|ini|toml)"
    r"(?:\.(?:bak|old|orig|copy|backup|save|tmp))?\b", re.I)
#: suffixes that make two stems the "same thing, different interference"
_VER_SUFFIX = re.compile(r"(?:[._-]?(?:v|ver|version)?\d+|\.bak|\.old|\.orig|\.copy|"
                         r"[._-]backup|~)$", re.I)
_DOC_HINT = re.compile(r"\.(?:md|rst|adoc)$|(?:^|/)(?:docs?|readme)", re.I)
_CODE_HINT = re.compile(r"\.(?:py|go|ts|js|rs|java|rb)$", re.I)
_CFG_HINT = re.compile(r"\.(?:ya?ml|toml|ini|cfg|json|env)$", re.I)
_TEST_HINT = re.compile(r"(?:^|/)(?:test_[\w]+\.py|[\w]+_test\.py|tests?/)", re.I)


def _paths(text: str) -> List[str]:
    try:
        out, seen = [], set()
        for m in _FILE_RE.finditer(str(text or "")):
            p = m.group(0).strip(".,:;()[]'\"")
            if not p or p in seen:
                continue
            seen.add(p)
            out.append(p)
        return out[:80]
    except Exception:  # noqa: BLE001
        return []


def _dir(p: str) -> str:
    return p.rsplit("/", 1)[0] if "/" in p else ""


def _stem(p: str) -> str:
    b = p.rsplit("/", 1)[-1]
    return b.rsplit(".", 1)[0] if "." in b else b


#: extensions are stripped ONCE, as a class, before any suffix loop. Stripping
#: a single trailing ".x" makes "config.yaml" normalise to "config" while
#: "config.yaml.bak" normalises to "config.yaml", so the pair never meets and
#: the sibling signal silently misses every backup file.
_EXT_TAIL = re.compile(r"\.(?:py|ya?ml|json|md|sh|txt|cfg|ini|toml)$", re.I)


def _norm_stem(p: str) -> str:
    s = _stem(p)
    # drop backup/version tails first, then the real extension
    prev = None
    while prev != s:
        prev, s = s, _VER_SUFFIX.sub("", s)
    for _ in range(4):
        nxt = _EXT_TAIL.sub("", s)
        if nxt == s:
            break
        s = nxt
    return s.lower()


def note_seen(store: Dict[str, Any], text: str) -> List[str]:
    """Accumulate every path this session has surfaced. Bounded."""
    paths = _paths(text)
    st = store.setdefault("fork_src", {})
    seen: List[str] = st.setdefault("paths", [])
    for p in paths:
        if p not in seen:
            seen.append(p)
    if len(seen) > 120:
        del seen[:-120]
    return seen


def rival_targets(store: Dict[str, Any], text: str
                  ) -> Optional[Dict[str, Any]]:
    """Return a synthesised fork when the session has surfaced two rival
    targets, else None. One fire per family per session.

    Never raises: a fork source must not be able to break a tool result.
    """
    try:
        seen = note_seen(store, text)
        if len(seen) < 2:
            return None
        st = store.setdefault("fork_src", {})
        fired: List[str] = st.setdefault("fired", [])

        # --- sibling family: same dir, stems equal after version stripping
        fams: Dict[Tuple[str, str], List[str]] = {}
        for p in seen:
            fams.setdefault((_dir(p), _norm_stem(p)), []).append(p)
        for (d, stem), members in fams.items():
            if len(members) < 2:
                continue
            key = "sibling:%s/%s" % (d, stem)
            if key in fired:
                continue
            fired.append(key)
            del fired[:-40]
            return {"source": "sibling_files", "family": key,
                    "options": sorted(members)[:4],
                    "why": "two files differ only by version/backup suffix; "
                           "only one is live"}

        # --- doc-vs-code: the documentation and the implementation disagree.
        # Measured gap: D3 ("the docs advertise a route the service does not
        # serve; make the system self-consistent") has the fork in
        # docs/ROUTES.md vs routes.py, and neither the sibling rule nor the
        # test-vs-source rule could see it -- the lane stayed silent on a real,
        # consequential fork. Two remedies exist (change the doc, or build the
        # route) and picking wrong is the whole test.
        docs = [p for p in seen if _DOC_HINT.search(p)]
        code = [p for p in seen if _CODE_HINT.search(p) and p not in docs]
        if docs and code:
            key = "doc_vs_code:%s|%s" % (docs[0], code[0])
            if key not in fired:
                fired.append(key)
                del fired[:-40]
                return {"source": "doc_vs_code", "family": key,
                        "options": [docs[0], code[0]][:2],
                        "why": "the doc and the code it describes disagree; "
                               "amending the doc and building the code are "
                               "different products"}

        # --- config-vs-consumer: a setting exists, and code reads it. Changing
        # the value and changing the reader are different fixes.
        cfgs = [p for p in seen if _CFG_HINT.search(p)]
        consumers = [p for p in seen if _CODE_HINT.search(p) and p not in cfgs]
        if cfgs and consumers:
            key = "cfg_vs_consumer:%s|%s" % (cfgs[0], consumers[0])
            if key not in fired:
                fired.append(key)
                del fired[:-40]
                return {"source": "cfg_vs_consumer", "family": key,
                        "options": [cfgs[0], consumers[0]][:2],
                        "why": "the setting and the code that reads it are both "
                               "editable; they are not the same fix"}

        # --- test-vs-source: which of these two do I change?
        tests = [p for p in seen if _TEST_HINT.search(p)]
        srcs = [p for p in seen if p not in tests]
        if tests and srcs:
            key = "test_vs_src:%s|%s" % (tests[0], srcs[0])
            if key not in fired:
                fired.append(key)
                del fired[:-40]
                return {"source": "test_vs_source", "family": key,
                        "options": [srcs[0], tests[0]][:2],
                        "why": "a failing test and the code under it are both "
                               "targets; fixing the test is not fixing the code"}
        return None
    except Exception:  # noqa: BLE001
        return None


def render_options(hit: Dict[str, Any]) -> List[str]:
    """Closed option labels for the envelope, one per rival target."""
    try:
        return ["edit %s" % p for p in (hit.get("options") or [])][:4]
    except Exception:  # noqa: BLE001
        return []
