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
#: shell scripts ARE code. An operations agent forks between a playbook and the
#: script it claims to describe as often as between a doc and a module, and with
#: `sh` missing the doc-vs-code source refused the whole family on the holdout.
_CODE_HINT = re.compile(r"\.(?:py|go|ts|tsx|js|jsx|rs|java|rb|c|cc|cpp|h|"
                        r"sh|bash|zsh|ps1|sql|tf)$", re.I)
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


def _stems_relate(a: str, b: str) -> bool:
    """True when two file names plausibly describe the same subject.

    routes.py and ROUTES.md relate; README.md and router_core.py do not. Keeps
    doc-vs-code from pairing a repository's front page with a random module.
    """
    sa, sb = _norm_stem(a), _norm_stem(b)
    if not sa or not sb:
        return False
    if len(sa) < 4 or len(sb) < 4:
        return False
    return sa in sb or sb in sa



# ---------------------------------------------------------------------------
# Domain-general fork detection
# ---------------------------------------------------------------------------
# The file rules above only see engineering repos. The fleet is not only
# engineers: an analyst weighs two root causes, a researcher two sources, a
# valmet engineer two protocol readings, an operative two hosts. A decision lane
# that only recognises file pairs recognises a minority of the fleet's real
# forks, so the generic layer reads the TEXT -- any text, any domain -- for the
# shapes in which a fork is actually voiced.


_STOP = frozenset((
    "it", "this", "that", "the", "a", "an", "them", "these", "those", "one",
    "other", "both", "either", "neither", "same", "different", "thing", "way",
))


#: Leading verbs and connectors that leak into a span ("Reconnect to prod-db-02",
#: "We should revert the doc"). Stripped once, and only from the front: the route
#: is the object, not the sentence that introduced it.
_SPAN_CUES = re.compile(
    r"^(?:we\s+(?:should|could|can|might|may)|i\s+(?:would|should)|let'?s|"
    r"(?:re)?connect\s+to|switch\s+to|move\s+to|revert\s+to|go\s+with|"
    r"prefer|pick|choose|use|try|take)\s+", re.I)


#: A span is an option only if it is a phrase a human would read as one. Real
#: traffic (sampled from 48 live fires) produced JSON fragments, markdown table
#: rows and mid-sentence cuts; those are not routes, they are debris. Judged on
#: the sample, roughly 40% of otherwise-genuine forks carried span damage.
_SPAN_JUNK = re.compile(r"[|`{}\[\]\"\\]|\t")

#: A pronoun-only span ("it does not", "they are") is a sentence fragment, not a
#: route -- and "either it compiles or it does not" is not a fork at all.
#: Pronoun + auxiliary only ("it does not", "they are"). A route like "we keep
#: the ledger" must survive -- the rejection is about grammatical fragments, not
#: about first person.
_FRAGMENT = re.compile(
    r"^(?:it|they|this|that|there)\b"
    r"(?:\s+(?:does|do|did|is|was|were|will|would|can|could|should|might|has|have)\b.*)?$",
    re.I)


def _trim_span(s: str) -> str:
    t = " ".join((s or "").split())
    if ":" in t:
        head, tail = t.split(":", 1)
        # "Route A: rewrite the parser" is a label followed by the route. But a
        # colon also shows up inside JSON ("total_lines": 1) and inside prose
        # ("note: it failed"). Taking the tail unconditionally turned the span
        # `delete /opt/x/probe_b.py.", "total_lines": 1}` into the route `1}` and
        # killed the whole fork. Only a short head followed by a substantial tail
        # is a label.
        if (tail.strip() and len(head.split()) <= 6
                and (len(tail.split()) >= 3 or len(tail) >= 0.6 * len(head))):
            t = tail.strip()
    prev = None
    while prev != t:
        prev, t = t, _SPAN_CUES.sub("", t).strip()
    # trailing clause is commentary, not part of the route
    t = re.split(r"\s+(?:before|after|while|because|so\s+that|in\s+order\s+to|"
                 r"when|until|unless)\s+", t, maxsplit=1)[0].strip()
    return t


def _span_ok(s: str) -> bool:
    """A route is a name, not a paragraph -- same guard the midturn path applies.

    Kept here so every domain inherits it: "one-or-two-sentence user complaint
    with no repo path" is not an option in any domain.
    """
    t = (s or "").strip(" \t\n,;:.\"'()[]")
    if not (2 <= len(t) <= 90):
        return False
    if t.lower() in _STOP:
        return False
    if not re.search(r"[^\W_]", t):
        return False
    if _SPAN_JUNK.search(t):
        return False
    if _FRAGMENT.match(t):
        return False
    if t.count(",") > 2:
        return False
    return True


#: connectives that voice a fork in prose. Deliberately domain-free: the same
#: four shapes carry an analyst's hypotheses, a researcher's sources, a valmet
#: engineer's protocol readings and an operative's hosts.
#: One span of prose. A dot is allowed when it is part of a token
#: (`probe_a.py`, `config.yaml`, `v1.2`) and forbidden as a sentence end --
#: without that lookahead the commonest real fork on the fleet
#: ("Either delete /opt/tmp/probe_a.py or delete /opt/tmp/probe_b.py") matched
#: nothing, because both routes carry a dot inside a FILENAME.
_SPAN = r"(?:[^.;\n\"}{\[\],]|\.(?=\S))"

_FORK_CONNECTIVES = (
    r"either\s+(?P<a>" + _SPAN + r"{2,80}?)\s+or\s+(?P<b>" + _SPAN + r"{2,80})",
    r"(?P<a>" + _SPAN + r"{2,80}?)\s+(?:vs\.?|versus)\s+(?P<b>" + _SPAN + r"{2,80})",
    r"(?P<a>" + _SPAN + r"{2,80}?)\s+(?:rather than|instead of)\s+(?P<b>" + _SPAN + r"{2,80})",
    r"between\s+(?P<a>" + _SPAN + r"{2,80}?)\s+and\s+(?P<b>" + _SPAN + r"{2,80})",
    r"(?P<a>" + _SPAN + r"{2,80}?)\s+could\s+be\s+(?:the\s+)?(?:cause|problem|issue|fault)\s*[,;]?\s*(?:or|and)\s+(?P<b>" + _SPAN + r"{2,80})",
    # cue-anchored inline choice ("Two options: refund the order or reship it").
    # The cue word is what keeps this tight -- a bare "X or Y" is far too common in
    # ordinary prose and would make the lane trigger-happy.
    r"(?:options?|alternatives?|choices?|candidates?)\s*:\s*(?P<a>" + _SPAN + r"{2,80}?)\s+or\s+(?P<b>" + _SPAN + r"{2,80})",
)
# Compiled separately, not as one alternation: Python forbids reusing a named
# group across alts in a single pattern, and each connective is tried in order.
_CONNECTIVE_RES = tuple(re.compile(c, re.I) for c in _FORK_CONNECTIVES)

#: a list introduced by a domain-free cue word. Covers "Possible causes:",
#: "Candidates:", "Options:", "Hypotheses:" -- used by every role in practice.
_LIST_CUE = re.compile(
    r"(?im)^[ \t]*(?:possible[ \t]+)?(?:candidates?|options?|alternatives?|"
    r"hypothes[ie]s|suspects?|possible[ \t]+causes?|root[ \t]+causes?|"
    r"approaches?|readings?|explanations?)[ \t]*:?[ \t]*$")
# 2 chars is a legitimate candidate: `fd` and `rg` are tools, and a list of
# two-letter routes was invisible while the bound was 3.
_LIST_ITEM = re.compile(r"(?m)^[ \t]*(?:\(?\d+[.)]|[-*\u2022])[ \t]+(.{2,60})$")


#: Cheap substring prefilter. The connective regexes cost ~12 ms on a 3 kB text
#: and scale with size; the lane runs on EVERY tool result, so paying that on
#: text that cannot possibly hold a fork is not affordable. A plain `in` test is
#: ~1000x cheaper and rejects the overwhelming majority of payloads.
_CUE_WORDS = (
    "either", " versus", " vs.", " vs ", "rather than", "instead of", "between",
    "could be", "option", "alternative", "choice", "candidate", "hypothes",
    "suspect", "possible cause", "root cause", "approach", "reading", "explanation",
)

#: Beyond this, a payload is data rather than deliberation. Bounded so one huge
#: tool result cannot stall a turn.
_SCAN_CAP = 20000


def _might_hold_fork(text: str) -> bool:
    tl = text.lower()
    return any(w in tl for w in _CUE_WORDS)


def _det_either_or(store, text):
    """Two routes voiced with a connective -- no enumeration, no repo, no files."""
    if not text or not _might_hold_fork(text):
        return None
    text = text[:_SCAN_CAP]
    m = None
    for rx in _CONNECTIVE_RES:
        m = rx.search(text)
        if m:
            break
    if not m:
        return None
    a, b = _trim_span(m.group("a")), _trim_span(m.group("b"))
    if not (_span_ok(a) and _span_ok(b)):
        return None
    if a.lower() == b.lower():
        return None
    key = "either_or:%s|%s" % (re.sub(r"\W+", "", a.lower())[:40],
                               re.sub(r"\W+", "", b.lower())[:40])
    return {"family": key, "options": [a, b],
            "why": "two routes voiced with a connective; the text does not pick one"}


def _det_listed_candidates(store, text):
    """A cue word followed by 2+ list items -- the shape every role uses to
    present candidates it has not chosen between."""
    if not text or not _might_hold_fork(text):
        return None
    text = text[:_SCAN_CAP]
    m = _LIST_CUE.search(text)
    if not m:
        return None
    tail = text[m.end():m.end() + 2000]
    items = [i.strip() for i in _LIST_ITEM.findall(tail) if _span_ok(i)]
    if len(items) < 2:
        return None
    key = "listed:%s" % re.sub(r"\W+", "", items[0].lower())[:40]
    return {"family": key, "options": items[:4],
            "why": "a candidate list the text never resolved to one choice"}


#: registry. Adding a source = appending here; the lane, its guards and its
#: one-fire-per-family discipline are inherited, never re-implemented.
GENERIC_DETECTORS = (
    ("either_or", _det_either_or),
    ("listed_candidates", _det_listed_candidates),
)


def enabled_sources() -> Tuple[str, ...]:
    """Which sources may fire. Portable default: all of them.

    Read through the sanctioned config seam and fail open -- a router that stops
    recognising forks because a config key is missing is worse than one that
    looks in too many places, and the precision guards bound the cost.
    """
    names = tuple(n for n, _ in GENERIC_DETECTORS) + (
        "sibling_files", "test_vs_source", "doc_vs_code", "cfg_vs_consumer")
    try:
        from .core.config_access import read_config  # type: ignore

        cfg = read_config() or {}
        fs_cfg = ((cfg.get("hermes_router") or {}).get("decision") or {}).get(
            "fork_sources")
        if isinstance(fs_cfg, dict):
            allow = fs_cfg.get("enabled")
            if isinstance(allow, list) and allow:
                return tuple(n for n in names if n in allow)
    except Exception:  # noqa: BLE001
        pass
    return names


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
        st = store.setdefault("fork_src", {})
        fired = st.setdefault("fired", [])
        on = enabled_sources()

        # Generic detectors first: they read a single text and need no
        # accumulated path set, so a non-engineering agent gets recognition on
        # its very first substantive message instead of never.
        for _name, _fn in GENERIC_DETECTORS:
            if _name not in on:
                continue
            hit = _fn(store, text)
            if not hit:
                continue
            if hit["family"] in fired:
                continue
            fired.append(hit["family"])
            del fired[:-40]
            return {"source": _name, "family": hit["family"],
                    "options": hit["options"], "why": hit.get("why", "")}

        if len(seen) < 2:
            return None

        # --- sibling family: same dir, stems equal after version stripping
        # ---- engineering sources: need two surfaced paths ----
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
        # Ambiguity guard. Evaluated over the session's whole path list, a doc
        # plus any code file is "a fork" -- a root README beside three source
        # files would manufacture one out of nothing, which is the same
        # random-fork class that scored precision 0.00 on the prose path. So name
        # a rival only when the pairing is unambiguous: either the stems relate,
        # or the session has surfaced exactly one doc and one code file.
        if len(code) > 1 and not _stems_relate(docs[0] if docs else "",
                                              code[0] if code else ""):
            code = code[:1] if len(code) == 1 else []
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
        # Same ambiguity guard: naming one of five source files "the consumer"
        # is a guess dressed as a fork.
        if len(consumers) > 1:
            consumers = consumers[:1] if len(seen) <= 3 else []
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
