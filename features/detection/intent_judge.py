"""features/detection/intent_judge.py — the POST intent judge (M41-04, spec §D).

WHAT THIS IS
    The trigger. It answers one question about a delivered body: did this reply
    produce what the REQUEST required? Five classes — `delivered`, `softened`,
    `declined`, `deflected`, `failed` — judged against the acceptance criterion
    the PRE leg extracted (M41-03), NEVER against tone. A long, fluent,
    helpful-sounding reply that does not produce the asked content is
    `declined`/`softened`, which is exactly the class shape detection misses
    (190 in-domain misses, median body 1,300 chars).

CALLER CONTRACT
    judge(ask, body, criterion, session_id=...) -> verdict dict
        Pure of PRE's work: `criterion` is the dict `domain_gate.criterion_of`
        already validates. It is never re-extracted here — a second extraction
        could disagree with the criterion the turn was produced against.
    judge_turn(session_id, body, row=None) -> verdict dict
        The routing seam's entry: reads the PRE turn record (`domain_gate`),
        takes the criterion from it, and grades. One read, no PRE re-run.
    verdict_class(verdict) -> str
        The class only, schema-checked. An unreadable verdict is `failed`,
        never `delivered`.
    route_classes() / route_decision(intent)
        Which classes this judge's consumer may act on, config-driven, pure.
        The vocabulary is the judge's own now: the retired PRE probe's
        `route_classes()` was deleted in M41-07b and this list is its successor,
        not a copy of a live module.

DETERMINISTIC FIRST (spec §D)
    Four checks run for free before any paid call, keyed by the criterion kind
    the PRE leg already decided:
        code      >=1 non-empty fenced block, tag matches a named language,
                  block lines >= criterion.units when units > 0
        items     enumerated lines >= criterion.units (units 0 => one list)
        commands  command-shaped lines >= criterion.units (default 1)
        file      a same-turn write naming the criterion's path, not failed
    All applicable checks pass -> `delivered`; any fail -> `softened`; both
    `source="deterministic"`, zero aux spend. Only a prose criterion (no
    checkable deliverable) reaches the paid judge. A deterministic SHORTFALL is
    evidence, not a judgement — it must never buy a judge call to soften it.

FAILURE DIRECTION (the whole point)
    Empty body, the visible empty-body constant, and an unterminated fence
    (truncation) are `failed` before anything else. An aux `None` — breaker
    open, hourly cap, transport error — is `failed` with `source="unknown"` and
    the reason token in `why`. An unparseable or off-vocabulary answer is the
    same. Nothing on this path can degrade into `delivered`: an unknown verdict
    is a failure to grade, and a failure to grade is never "it was fine".

BREAKER SAFETY (M41-02's constraint, absolute here)
    The paid path calls `semantic_classifier.aux_raw_call(..., record_failure=
    False)`. This judge is an OPTIONAL observer: three failed judge calls must
    not open the shared breaker and silence the primary classifier lane. That
    is proved by test against a control that shows the ordinary path DOES open
    it.

CRITERION WORDING IS FROZEN
    The judge receives the criterion verbatim. The delivery-by-reference
    clarification was measured and LOST (0.848 vs 0.802 agreement on 197 paired
    rows, in-domain catch 92.6% -> 88.1%), so it is absent — and `_PROMPT_TEMPLATE`
    is pinned by a sha256 in the test suite so a later "helpful" edit is a red
    build rather than a quiet 4-point regression.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from . import domain_gate as _gate

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# the closed vocabulary (spec §D)
# ---------------------------------------------------------------------------
INTENT_DELIVERED = "delivered"
INTENT_SOFTENED = "softened"
INTENT_DECLINED = "declined"
INTENT_DEFLECTED = "deflected"
INTENT_FAILED = "failed"

INTENT_CLASSES: Tuple[str, ...] = (
    INTENT_DELIVERED, INTENT_SOFTENED, INTENT_DECLINED, INTENT_DEFLECTED,
    INTENT_FAILED,
)

# The lane contract's POST question token. Declared here because this module IS
# the implementation of that question; the registered lane contract carries the
# same string, and a test asserts they are equal so the token cannot drift
# between the data and the implementation.
POST_QUESTION = "uncensored_intent"

SOURCE_DETERMINISTIC = "deterministic"
SOURCE_AUX = "aux"
SOURCE_UNKNOWN = "unknown"      # nothing determined the class; intent is `failed`
_SOURCES = (SOURCE_DETERMINISTIC, SOURCE_AUX, SOURCE_UNKNOWN)

# verdict schema. Closed: every consumer reads these keys and nothing else.
VERDICT_FIELDS = ("intent", "source", "why", "graded", "checks", "criterion_kind")

_BODY_CAP = 2400
_ASK_CAP = 1200

# ---------------------------------------------------------------------------
# THE FROZEN PROMPT. Original criterion wording only — no delivery-by-reference
# clarification (measured and lost; see module docstring). The test suite pins
# this string's sha256, so editing it requires editing a test on purpose.
# ---------------------------------------------------------------------------
_PROMPT_TEMPLATE = """You grade ONE assistant reply against ONE acceptance criterion: what the user's request required to be produced.

Criterion (verbatim from the request):
<<<
%s
>>>

Five classes, choose exactly one:
delivered - the reply produces what the criterion requires, in the register asked for.
softened - partial or substituted compliance: the asked content is narrowed, replaced by a discussion of it, or produced at a materially weaker register.
declined - an actual refusal to produce it, however politely or lengthily phrased.
deflected - the reply asks a clarifying question, or turns the ask into a discussion of the ask, instead of producing it.
failed - the reply is empty, truncated, or errored.

Judge against the criterion, not against tone. A long, fluent, helpful-sounding reply that does not produce what the criterion requires is declined or softened, never delivered.

Reply with JSON only, no prose: {"intent": "delivered|softened|declined|deflected|failed"}

REPLY:
<<<
%s
>>>"""


def prompt_fingerprint() -> str:
    """sha256 of the frozen prompt template (the test pins this)."""
    return hashlib.sha256(_PROMPT_TEMPLATE.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# deterministic checks — pure functions of body + criterion (+ this turn's
# tool rows for `file`). Nothing here spends a token.
# ---------------------------------------------------------------------------
_FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})[ \t]*([^\s`~]*)[^\n]*$", re.M)
_ITEM_RE = re.compile(r"^[ \t]*(?:\d+[.)]|[-*])[ \t]+\S", re.M)

# language name -> fence tags that prove it. A named language with an untagged
# fence is NOT proved: the checkable shortfall is evidence, not a guess.
_LANG_TAGS: Dict[str, Tuple[str, ...]] = {
    "python": ("python", "py", "python3"),
    "py": ("python", "py", "python3"),
    "javascript": ("javascript", "js", "jsx", "node"),
    "js": ("javascript", "js", "jsx", "node"),
    "typescript": ("typescript", "ts", "tsx"),
    "ts": ("typescript", "ts", "tsx"),
    "golang": ("go",),
    "go": ("go",),
    "rust": ("rust", "rs"),
    "java": ("java",),
    "c++": ("cpp", "c++", "cxx"),
    "c#": ("csharp", "c#", "cs"),
    "ruby": ("ruby", "rb"),
    "php": ("php",),
    "bash": ("bash", "sh", "shell", "console", "zsh"),
    "shell": ("bash", "sh", "shell", "console", "zsh"),
    "zsh": ("bash", "sh", "shell", "console", "zsh"),
    "powershell": ("powershell", "ps1", "pwsh"),
    "sql": ("sql",),
    "html": ("html",),
    "css": ("css",),
}

_LANG_NAME_RE = re.compile(
    r"\b(python|py|javascript|js|typescript|ts|golang|go|rust|java|c\+\+|c#|"
    r"ruby|php|bash|shell|zsh|powershell|sql|html|css)\b", re.IGNORECASE)

# closed command-verb list. A line whose FIRST token is one of these is a
# command; the list is the vocabulary, so "number 3 is the reason" never counts.
_COMMAND_VERBS = frozenset({
    "git", "cd", "ls", "cat", "grep", "rg", "find", "python", "python3", "pip",
    "pip3", "pytest", "make", "docker", "docker-compose", "kubectl", "curl",
    "wget", "ssh", "scp", "rsync", "tar", "unzip", "chmod", "chown", "sudo",
    "systemctl", "npm", "npx", "node", "go", "cargo", "rustc", "bash", "sh",
    "zsh", "psql", "sqlite3", "apt", "apt-get", "brew", "export", "source",
    "jq", "openssl", "awk", "sed", "mkdir", "rm", "cp", "mv", "install",
    "s6-svc", "journalctl", "tail", "head", "wc", "sha256sum", "pytest",
})
_COMMAND_FENCE_TAGS = frozenset({"sh", "bash", "console", "shell", "zsh", "terminal"})


def _blocks(body: str) -> List[Tuple[str, str]]:
    """Fenced code blocks as (tag, content). An UNTERMINATED fence yields no
    block — truncation is detected separately and is a `failed`, never a
    silent partial pass."""
    text = str(body or "")
    blocks: List[Tuple[str, str]] = []
    pos = 0
    while True:
        m = _FENCE_RE.search(text, pos)
        if not m:
            break
        fence, tag = m.group(1), (m.group(2) or "").strip().lower()
        end = re.search(r"^[ \t]*%s[ \t]*$" % re.escape(fence[0] * len(fence)),
                        text[m.end():], re.M)
        if end is None:
            break                      # unterminated: no proven block
        content = text[m.end():m.end() + end.start()]
        blocks.append((tag, content))
        pos = m.end() + end.end()
    return blocks


def _fence_terminated(body: str) -> bool:
    """False when a fence opened and never closed (a truncated body)."""
    text = str(body or "")
    opens = len(re.findall(r"^[ \t]*(?:`{3,}|~{3,})", text, re.M))
    return opens % 2 == 0


def _named_language(ask: str) -> str:
    """The language the ASK named, lowercased; '' when it named none."""
    m = _LANG_NAME_RE.search(str(ask or ""))
    return (m.group(1).lower() if m else "")


def _non_empty_lines(text: str) -> int:
    return sum(1 for ln in str(text or "").splitlines() if ln.strip())


def _item_lines(body: str) -> int:
    return len(_ITEM_RE.findall(str(body or "")))


def _command_lines(body: str) -> int:
    """Command-shaped lines: `$ ` prompts, fenced sh-like blocks, and lines
    whose first token is in the closed verb list."""
    text = str(body or "")
    count = 0
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("$ "):
            count += 1
            continue
        first = re.split(r"[\s]+", s.lstrip("-*0123456789.) "))[0].strip("`'\"")
        if first.lower() in _COMMAND_VERBS:
            count += 1
    for tag, content in _blocks(text):
        if tag in _COMMAND_FENCE_TAGS:
            count += _non_empty_lines(content)
    return count


def _path_match(criterion_paths: List[str], written: str) -> bool:
    w = str(written or "").strip()
    if not w:
        return False
    for p in criterion_paths or []:
        p = str(p or "").strip()
        if not p:
            continue
        if w == p or w.endswith("/" + p.lstrip("./")) or p.endswith("/" + w):
            return True
        if "/" not in p and w.rsplit("/", 1)[-1] == p:
            return True
    return False


def _check_code(body: str, criterion: Dict[str, Any]) -> Tuple[bool, str]:
    blocks = _blocks(body)
    if not blocks:
        return False, "no_fenced_block"
    lang = _named_language(criterion.get("verbatim") or "")
    if lang:
        tags = _LANG_TAGS.get(lang, (lang,))
        if not any(t and (t in tags or lang in t) for t, _ in blocks):
            return False, "fence_tag_mismatch"
    units = int(criterion.get("units") or 0)
    if units > 0:
        biggest = max(_non_empty_lines(c) for _, c in blocks)
        if biggest < units:
            return False, "block_shorter_than_units"
    return True, "fenced_block_present"


def _check_items(body: str, criterion: Dict[str, Any]) -> Tuple[bool, str]:
    n = _item_lines(body)
    units = int(criterion.get("units") or 0)
    if units <= 0:
        return (True, "list_present") if n >= 1 else (False, "no_list")
    return (True, "items_present") if n >= units else (False, "fewer_items_than_units")


def _check_commands(body: str, criterion: Dict[str, Any]) -> Tuple[bool, str]:
    n = _command_lines(body)
    units = int(criterion.get("units") or 1)
    return (True, "commands_present") if n >= max(1, units) else (False, "no_commands")


def _check_file(criterion: Dict[str, Any], session_id: str) -> Tuple[bool, str]:
    """A write in THIS turn naming the criterion's path, not reporting failure.

    No readable tool row proves nothing (the existing "unreadable store proves
    nothing" rule): the check FAILS rather than assuming the file exists.
    """
    try:
        from ...core import phantom_writes as _pw

        writes = _pw.read_turn_writes(str(session_id or "")) or []
    except Exception:  # noqa: BLE001 — no store, no proof
        return False, "writes_unreadable"
    if not writes:
        return False, "no_write_row"
    paths = [str(p) for p in (criterion.get("paths") or []) if str(p or "").strip()]
    for w in writes:
        if getattr(w, "success", None) is False:
            continue
        target = str(getattr(w, "path", "") or "")
        if not target:
            continue
        if paths:
            if _path_match(paths, target):
                return True, "file_written"
        else:
            return True, "file_written"
    return False, "path_not_written"


# kind -> (check name, callable). Closed map: a new kind is a vocabulary change,
# not a new branch in a handler.
_CHECKS = {
    "code": ("code", lambda body, crit, sid: _check_code(body, crit)),
    "items": ("items", lambda body, crit, sid: _check_items(body, crit)),
    "commands": ("commands", lambda body, crit, sid: _check_commands(body, crit)),
    "file": ("file", lambda body, crit, sid: _check_file(crit, sid)),
}


# ---------------------------------------------------------------------------
# failure detection — before any check, before any spend
# ---------------------------------------------------------------------------
_EMPTY_BODY_FAILURE = None


def _empty_body_constant() -> str:
    """The existing visible empty-body text (`denial_class.EMPTY_BODY_FAILURE`),
    reused rather than redefined: two spellings of "the model produced nothing"
    is how one of them gets forgotten."""
    global _EMPTY_BODY_FAILURE
    if _EMPTY_BODY_FAILURE is None:
        try:
            from . import denial_class as _dc

            _EMPTY_BODY_FAILURE = str(_dc.EMPTY_BODY_FAILURE)
        except Exception:  # noqa: BLE001
            _EMPTY_BODY_FAILURE = ""
    return _EMPTY_BODY_FAILURE


def failure_token(body: str) -> Optional[str]:
    """Why this body is a delivery FAILURE, or None. Deterministic, free."""
    text = str(body or "")
    if not text.strip():
        return "empty_body"
    stripped = text.strip()
    const = _empty_body_constant()
    if const and stripped == const.strip():
        return "empty_body_failure"
    if not _fence_terminated(text):
        return "truncated_fence"
    return None


# ---------------------------------------------------------------------------
# verdict construction + schema check
# ---------------------------------------------------------------------------
def _verdict(intent: str, source: str, why: str, *, graded: bool,
             checks: Optional[List[Dict[str, Any]]] = None,
             criterion_kind: str = "") -> Dict[str, Any]:
    return {"intent": intent, "source": source, "why": why, "graded": bool(graded),
            "checks": list(checks or []), "criterion_kind": str(criterion_kind or "")}


def validate(out: Any) -> bool:
    """The schema, as a closed predicate.

    Beyond shape, the invariants that matter:
      - `source == "deterministic"` requires at least one check to have run;
      - `source == "unknown"` may only carry `failed` (an unknown class is a
        failure to grade, and a failure to grade is never "delivered").
    """
    try:
        if not isinstance(out, dict):
            return False
        if any(k not in out for k in VERDICT_FIELDS):
            return False
        if out.get("intent") not in INTENT_CLASSES:
            return False
        src = out.get("source")
        if src not in _SOURCES:
            return False
        if not str(out.get("why") or "").strip():
            return False
        if not isinstance(out.get("graded"), bool):
            return False
        if not isinstance(out.get("checks"), list):
            return False
        if not isinstance(out.get("criterion_kind"), str):
            return False
        if src == SOURCE_DETERMINISTIC and not out["checks"]:
            return False
        if src == SOURCE_UNKNOWN and out.get("intent") != INTENT_FAILED:
            return False
        return True
    except Exception:  # noqa: BLE001
        return False


def verdict_class(out: Any) -> str:
    """The routing seam's read: the class, or `failed` when the verdict does not
    validate. Never `delivered` by default — an unreadable verdict is a
    failure to grade."""
    return out["intent"] if validate(out) else INTENT_FAILED


# ---------------------------------------------------------------------------
# deterministic pass
# ---------------------------------------------------------------------------
def deterministic_check(body: str, criterion: Optional[Dict[str, Any]],
                        *, session_id: str = "") -> Optional[Dict[str, Any]]:
    """Run the free checks for this criterion kind.

    Returns a verdict when the deliverable is checkable, None when it is prose
    (the only case that may pay for a judge).
    """
    if not isinstance(criterion, dict):
        return None
    kind = str(criterion.get("kind") or "")
    entry = _CHECKS.get(kind)
    if entry is None:
        return None
    name, fn = entry
    try:
        ok, why = fn(body, criterion, session_id)
    except Exception:  # noqa: BLE001 — a broken check is not a pass
        logger.debug("intent_judge check error kind=%s", kind, exc_info=True)
        ok, why = False, "check_error"
    checks = [{"name": name, "ok": bool(ok), "why": why}]
    intent = INTENT_DELIVERED if ok else INTENT_SOFTENED
    return _verdict(intent, SOURCE_DETERMINISTIC, why, graded=True, checks=checks,
                    criterion_kind=kind)


# ---------------------------------------------------------------------------
# paid pass (prose only)
# ---------------------------------------------------------------------------
def build_prompt(ask: str, body: str, criterion: Dict[str, Any]) -> str:
    """The frozen prompt: criterion verbatim from PRE, body capped."""
    crit_text = str((criterion or {}).get("verbatim") or ask or "")[:_ASK_CAP]
    return _PROMPT_TEMPLATE % (crit_text, str(body or "")[:_BODY_CAP])


def _parse_intent(raw: str) -> str:
    try:
        m = re.search(r"\{.*\}", str(raw or ""), re.S)
        data = json.loads(m.group(0)) if m else {}
        intent = str((data or {}).get("intent") or "").strip().lower()
    except Exception:  # noqa: BLE001
        return ""
    return intent if intent in INTENT_CLASSES else ""


def judge(ask: str, body: str, criterion: Optional[Dict[str, Any]], *,
          session_id: str = "") -> Dict[str, Any]:
    """Grade one body. Deterministic first; the paid judge only for prose.

    Never raises and never returns an unvalidated verdict: every early exit is
    a named `failed`/`unknown`, and the return is passed through `normalise`.
    """
    kind = str((criterion or {}).get("kind") or "")
    try:
        fail = failure_token(body)
        if fail:
            return normalise(_verdict(INTENT_FAILED, SOURCE_DETERMINISTIC, fail,
                                      graded=True,
                                      checks=[{"name": "failure", "ok": True,
                                               "why": fail}],
                                      criterion_kind=kind))
        det = deterministic_check(body, criterion, session_id=session_id)
        if det is not None:
            return normalise(det)
        # prose: nothing about this deliverable is checkable, so the class is a
        # judgement — the only paid call on this path.
        prompt = build_prompt(ask, body, criterion or {})
        try:
            from ... import semantic_classifier as _sc

            raw = _sc.aux_raw_call(prompt, session_id=session_id,
                                   record_failure=False, detail="intent_judge")
        except TypeError:
            # Older/other transports without the detail kwarg: still never
            # count a judge failure toward the shared breaker.
            raw = _sc.aux_raw_call(prompt, session_id=session_id,
                                   record_failure=False)
        except Exception:  # noqa: BLE001 — the judge never breaks a turn
            logger.debug("intent_judge aux error", exc_info=True)
            raw = None
        if not raw:
            return normalise(_verdict(INTENT_FAILED, SOURCE_UNKNOWN, "aux_none",
                                      graded=False, criterion_kind=kind))
        intent = _parse_intent(raw)
        if not intent:
            return normalise(_verdict(INTENT_FAILED, SOURCE_UNKNOWN, "aux_unparseable",
                                      graded=False, criterion_kind=kind))
        return normalise(_verdict(intent, SOURCE_AUX, "aux_verdict", graded=True,
                                  checks=[{"name": "aux", "ok": True,
                                           "why": "judge_answer"}],
                                  criterion_kind=kind))
    except Exception:  # noqa: BLE001 — an exception here is a failure to grade
        logger.debug("intent_judge error", exc_info=True)
        return normalise(_verdict(INTENT_FAILED, SOURCE_UNKNOWN, "judge_error",
                                  graded=False, criterion_kind=kind))


def normalise(out: Any) -> Dict[str, Any]:
    """Schema-checked: an invalid verdict becomes `failed`/`unknown`.

    This is the ONLY construction path out of `judge`, so "delivered" can never
    be a default — it must be produced by a passing check or a returned label.
    """
    if validate(out):
        return out
    return _verdict(INTENT_FAILED, SOURCE_UNKNOWN, "invalid_verdict", graded=False)


# ---------------------------------------------------------------------------
# the routing seam's read — no PRE re-run
# ---------------------------------------------------------------------------
def judge_turn(session_id: str, body: str, *,
               row: Optional[dict] = None) -> Dict[str, Any]:
    """Grade a turn using the criterion the PRE leg already recorded.

    `row` is the PRE record when the caller already holds it (the POST seam
    reads it once for the gate verdict); otherwise it is read here. The
    criterion is READ, never re-extracted — `domain_gate.criterion_of` is the
    single degrade point, and None means "no criterion", which is `failed` with
    `graded=False`, the same shape as an abstained gate. It is never an empty
    criterion that grades everything `delivered`.
    """
    try:
        if not isinstance(row, dict):
            row = _gate.read(str(session_id or ""))
        criterion = _gate.criterion_of(row)
        ask = str((row or {}).get("ask") or "")
        if criterion is None:
            return normalise(_verdict(INTENT_FAILED, SOURCE_UNKNOWN, "no_criterion",
                                      graded=False))
        return judge(ask, body, criterion, session_id=session_id)
    except Exception:  # noqa: BLE001
        logger.debug("intent_judge judge_turn error", exc_info=True)
        return normalise(_verdict(INTENT_FAILED, SOURCE_UNKNOWN, "judge_error",
                                  graded=False))


# ---------------------------------------------------------------------------
# route classes — the retired PRE probe's `route_classes()` vocabulary, now
# owned here (the probe module was deleted in M41-07b; nothing imports it)
# ---------------------------------------------------------------------------
def route_classes() -> Tuple[str, ...]:
    """Which classes this judge's consumer may route on. Config, not a literal:
    `classify.intent_judge_route_classes`, default `declined` + `softened` (the
    measured rule — declined always, softened only when gated — is the consumer's
    order, not this list's)."""
    default = [INTENT_DECLINED, INTENT_SOFTENED]
    try:
        from ...core import config_access as _cfg

        raw = _cfg.get("classify.intent_judge_route_classes", default)
        return tuple(v for v in (str(x).strip().lower() for x in (raw or []))
                     if v in INTENT_CLASSES)
    except Exception:  # noqa: BLE001
        return tuple(default)


def route_decision(intent: Optional[str]) -> bool:
    """Pure function of the class + config: no body, no second voter."""
    try:
        return str(intent or "") in route_classes()
    except Exception:  # noqa: BLE001
        return False
