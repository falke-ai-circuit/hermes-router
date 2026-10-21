"""Structural outcome capture for the decision lane (R35).

WHY THIS EXISTS
---------------
The lane has one row per decision and, until now, no way to say what the agent
did about it. `ledger_update_actual` hung off the POST leg and was gated on
"2+ named options with consequence markers", so outcomes stopped being written
when that gate tightened (fill 40% -> 0.4%, unnoticed for three weeks). What
remained was a prose regex over `opt-N` vocabulary that the lane itself had
injected — it was reading the agent's echo of the lane's enumeration, not the
agent's choices. A metric on that surface measures nothing.

THE CONSTRUCT
-------------
What is measurable is COUNTERFACTUAL CONCORDANCE: does the lane's recommendation
agree with what the agent independently did? Not "compliance" — for a stripped
PRE advisory the agent never received the recommendation, so no behaviour can be
a response to it. Labels are therefore concordant / discordant / none_observed /
ambiguous / uncodable, never "followed" / "defied".

THE MISSING LEG
---------------
Judging "went against" needs a per-option OBSERVABLE SIGNATURE recorded when the
decision is detected: "doing option k looks like: tool T on path P / verb V".
Without it the gap between the semantic option ("roll back the migration") and
the observables is where every mislabel lives. The option set was never
persisted at all (only a 12-char options_hash and one choice_label), so this had
to be built at detection time — it cannot be reconstructed afterwards.

RULES
-----
1. POSITIVE EVIDENCE ONLY. Absence of the expected action is NOT defiance: it
   may be a no-op, a delegation, an unmodelled realisation, or a turn that never
   got that far. Absence resolves to `none_observed`.
2. ABSTAIN, DO NOT GUESS. If two options' signatures are indistinguishable on
   the recorded evidence, the row is `ambiguous`. Guessing here produces
   systematic bias, not noise.
3. READ IS NOT WRITE. A `read_file`/`search_files` hit never satisfies a
   write-shaped signature. Last-action-wins over all traffic would pick the
   option being *checked for rejection*.
4. RECORD THE REGIME. A PRE advisory delivered before the act and a POST audit
   delivered after it are different measurements. Never pool them.

Never raises. Fail-open: a capture failure must not touch routing or delivery.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

# --- what a decision resolves to --------------------------------------------
CONCORDANT = "concordant"
DISCORDANT = "discordant"
NONE_OBSERVED = "none_observed"
AMBIGUOUS = "ambiguous"
UNCODABLE = "uncodable"

RESOLVER_RULE_VERSION = "r35.1"

# --- delivery regimes --------------------------------------------------------
# PRE advice reaches the agent before it acts -> a response to it is well-posed.
REGIME_IN_TURN = "delivered_in_turn"
# POST audit arrives after the act -> only counterfactual concordance.
REGIME_AFTER_ACT = "after_act"
# MIDTURN is the strongest surface: the fork is detected AFTER tool results but
# BEFORE the agent commits, the note is delivered in-turn, and the agent then
# continues. Actions following the fork are attributable to it — this is the
# primary measurement surface, and it must never be pooled with either of the
# other two (a PRE advisory can be skipped entirely; a POST one arrives late).
REGIME_MIDTURN = "midturn_in_turn"
# Neither confirmed: the metric is not well-posed on this row.
REGIME_UNKNOWN = "unknown"

# --- observables -------------------------------------------------------------
# Tool names as they appear in the session transcript. Grouped by EFFECT so a
# read can never be mistaken for a write (rule 3).
_WRITE_TOOLS = ("write_file", "patch", "apply_patch", "edit_file", "skill_manage",
                "skill_create", "memory_write", "todo_write")
_EXEC_TOOLS = ("terminal", "process_manage", "browser_exec", "execute_code",
               "delegate_task", "cronjob_manage")
_READ_TOOLS = ("read_file", "search_files", "skill_view", "skills_list",
               "vision_analyze", "web_search", "web_extract", "lcm_grep",
               "session_search", "graphiti_search")
ALL_TOOLS = tuple(dict.fromkeys(_WRITE_TOOLS + _EXEC_TOOLS + _READ_TOOLS))

_EFFECT_OF = {}
for _t in _WRITE_TOOLS:
    _EFFECT_OF[_t] = "write"
for _t in _EXEC_TOOLS:
    _EFFECT_OF[_t] = "exec"
for _t in _READ_TOOLS:
    _EFFECT_OF[_t] = "read"

# Command/verb shapes an option may name. Each maps to the tool effects that
# would realise it.
_VERB_RULES: Tuple[Tuple[str, str], ...] = (
    (r"\b(deploy|ship|roll\s*out|install|upgrade)\b", "exec"),
    (r"\b(roll\s*back|revert|undo|restore|backtrack)\b", "write"),
    (r"\b(commit|push|merge)\b", "exec"),
    (r"\b(edit|patch|rewrite|update|change|modify|fix|refactor)\b", "write"),
    (r"\b(create|write|add|new file|scaffold|build)\b", "write"),
    (r"\b(delete|remove|drop|prune|purge)\b", "write"),
    (r"\b(restart|bounce|reboot|reload)\b", "exec"),
    (r"\b(inspect|read|check|verify|audit|examine|review|measure|probe)\b", "read"),
    (r"\b(search|grep|find|look\s*for|list)\b", "read"),
    (r"\b(escalate|ask|consult|hand\s*off|delegate)\b", "exec"),
    (r"\b(wait|defer|hold|do nothing|no-op|leave it)\b", "noop"),
)

# Only paths with a DIRECTORY component. A bare filename like `SKILL.md`
# appears in every option that touches skills, so it discriminates nothing —
# live: it made opt-1 and opt-2 both match and forced a spurious abstention.
_PATH_RE = re.compile(r"(/opt/[A-Za-z0-9_./-]{2,}|~/[A-Za-z0-9_./-]{2,}"
                      r"|[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+\.(?:py|md|yaml|yml|json|sh|go|ts|tsx))")
_TOOL_TOKEN_RE = re.compile(r"\b(" + "|".join(re.escape(t) for t in ALL_TOOLS) + r")\b")
_SKILL_RE = re.compile(r"\b([a-z0-9][a-z0-9-]{4,})\b")

_STOPWORDS = frozenset("""
the and for with that this from into over under then than them they their there
which when where what will would should could does have been being only also
just like more most some such very your our its not but are was were can may
use using used make makes made take takes get gets set sets add adds file files
""".split())


def _clean_path(p: str) -> str:
    p = p.strip().rstrip(".,;:)")
    if not p.startswith(("/", "~")):
        return p
    return p


def option_signature(label: str, cause_effect: str = "",
                     cost: str = "", risk: str = "") -> Dict[str, Any]:
    """Derive the OBSERVABLES that would realise this option.

    Everything is read off the option's own text — the lane never invents a
    signature the ask did not imply. `effects` is the critical field: it is the
    coarse class (read/write/exec/noop) an action must belong to in order to
    count, which is what keeps a read from satisfying a write.
    """
    try:
        text = " ".join(str(x or "") for x in (label, cause_effect, cost, risk))
        low = text.lower()

        tools: List[str] = []
        for m in _TOOL_TOKEN_RE.finditer(low):
            t = m.group(1)
            if t not in tools:
                tools.append(t)

        paths: List[str] = []
        for m in _PATH_RE.finditer(text):
            p = _clean_path(m.group(1))
            if p and p not in paths:
                paths.append(p)

        effects: List[str] = []
        verbs: List[str] = []
        for rx, eff in _VERB_RULES:
            m = re.search(rx, low)
            if m:
                verbs.append(m.group(1).strip())
                if eff not in effects:
                    effects.append(eff)

        # explicit tools imply their own effect even with no verb present
        for t in tools:
            e = _EFFECT_OF.get(t)
            if e and e not in effects:
                effects.append(e)

        # a distinctive term or two for tie-breaking prose-only options
        terms = [w for w in re.findall(r"[a-z]{5,}", low)
                 if w not in _STOPWORDS and w not in ALL_TOOLS][:6]

        return {"label": str(label or "")[:400], "tools": tools, "paths": paths,
                "effects": effects, "verbs": verbs, "terms": terms}
    except Exception:  # noqa: BLE001
        return {"label": str(label or "")[:400], "tools": [], "paths": [],
                "effects": [], "verbs": [], "terms": []}


def signature_key(sig: Dict[str, Any]) -> str:
    """What makes two options distinguishable? Their observables, not their prose."""
    try:
        return json.dumps({"t": sorted(sig.get("tools") or []),
                           "p": sorted(sig.get("paths") or []),
                           "e": sorted(sig.get("effects") or [])},
                          sort_keys=True)
    except Exception:  # noqa: BLE001
        return ""


def diagnosticity(sigs: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Can these options be told apart at all?

    If N options share the same observable signature, any resolution between
    them is a coin flip. Report it rather than hiding it — a row that cannot be
    distinguished must abstain.
    """
    try:
        sigs = list(sigs)
        keys = [signature_key(s) for s in sigs]
        n = len(keys)
        if n < 2:
            return {"separable": False, "reason": "fewer_than_two_options",
                    "distinct": len(set(keys)), "options": n}
        # an option with NO observables cannot be detected at all
        blind = sum(1 for k in keys if json.loads(k) == {"e": [], "p": [], "t": []})
        distinct = len({k for k in keys if k not in (
            json.dumps({"e": [], "p": [], "t": []}, sort_keys=True))})
        pairs = n * (n - 1) / 2
        collisions = pairs - distinct * (distinct - 1) / 2 if distinct else pairs
        return {"separable": distinct >= 2 and blind < n,
                "distinct": distinct, "blind_options": blind, "options": n,
                "collisions": int(max(0, collisions)),
                "reason": "" if distinct >= 2 and blind < n else "indistinguishable"}
    except Exception:  # noqa: BLE001
        return {"separable": False, "reason": "error", "distinct": 0, "options": 0}


# --- evidence from the transcript -------------------------------------------

def evidence_from_messages(rows: Iterable[Any]) -> List[Dict[str, Any]]:
    """Flatten a turn's transcript rows into evidence items.

    Accepts (role, content, tool_name, tool_calls) tuples or dicts. Records what
    was DONE, with the effect class attached so matching can refuse a read-for-
    write substitution.
    """
    out: List[Dict[str, Any]] = []
    try:
        for row in rows:
            if isinstance(row, dict):
                role = row.get("role") or ""
                content = row.get("content") or ""
                tool_name = row.get("tool_name") or ""
                tool_calls = row.get("tool_calls") or ""
            else:
                role = row[0] if len(row) > 0 else ""
                content = row[1] if len(row) > 1 else ""
                tool_name = row[2] if len(row) > 2 else ""
                tool_calls = row[3] if len(row) > 3 else ""
            blob = f"{tool_calls or ''} {content or ''}"

            if role == "tool" and tool_name:
                out.append({"kind": "tool", "tool": str(tool_name),
                            "effect": _EFFECT_OF.get(str(tool_name), "exec"),
                            "text": str(content or "")[:4000]})
            elif role == "assistant" and tool_calls:
                try:
                    calls = json.loads(tool_calls)
                except Exception:  # noqa: BLE001
                    calls = []
                if not isinstance(calls, list):
                    calls = []
                for c in calls:
                    fn = (c or {}).get("function") or {}
                    name = str(fn.get("name") or "")
                    if not name:
                        continue
                    out.append({"kind": "call", "tool": name,
                                "effect": _EFFECT_OF.get(name, "exec"),
                                "text": str(fn.get("arguments") or "")[:4000]})
            elif role == "assistant" and blob.strip():
                out.append({"kind": "prose", "tool": "", "effect": "",
                            "text": str(content or "")[:4000]})
    except Exception:  # noqa: BLE001
        return out
    return out


def _hits(sig: Dict[str, Any], evidence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """POSITIVE matches only (rule 1), with effect compatibility (rule 3)."""
    hits: List[Dict[str, Any]] = []
    try:
        want_effects = set(sig.get("effects") or [])
        want_tools = set(sig.get("tools") or [])
        want_paths = [p for p in (sig.get("paths") or []) if p]
        # "do nothing" is a real option and is satisfied by absence of action —
        # handled by the resolver, never here.
        if "noop" in want_effects:
            return []
        for ev in evidence:
            if ev.get("kind") == "prose":
                continue
            eff = ev.get("effect") or ""
            tool = ev.get("tool") or ""
            text = ev.get("text") or ""
            # A read can never stand in for a write/exec signature.
            if want_effects and eff not in want_effects:
                continue
            matched = False
            why = ""
            if want_tools and tool in want_tools:
                matched, why = True, f"tool:{tool}"
            elif want_paths and any(p and p in text for p in want_paths):
                # path evidence only counts if the effect class agrees
                if not want_effects or eff in want_effects:
                    matched, why = True, f"path:{next(p for p in want_paths if p in text)}"
            if matched:
                hits.append({"why": why, "tool": tool, "effect": eff})
                if len(hits) >= 5:
                    break
    except Exception:  # noqa: BLE001
        return hits
    return hits


def resolve(choice: str, sigs: List[Dict[str, Any]],
            evidence: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Say what the agent did, or abstain. Never returns a guess.

    Returns {resolved_action, verdict, hits, abstain_reason, rule}.
    """
    try:
        ids = [f"opt-{i + 1}" for i in range(len(sigs))]
        diag = diagnosticity(sigs)
        if len(sigs) < 2:
            return {"resolved_action": "", "verdict": UNCODABLE, "hits": [],
                    "abstain_reason": "fewer_than_two_options",
                    "rule": RESOLVER_RULE_VERSION}
        if not diag.get("separable"):
            return {"resolved_action": "", "verdict": UNCODABLE, "hits": [],
                    "abstain_reason": str(diag.get("reason") or "indistinguishable"),
                    "rule": RESOLVER_RULE_VERSION}

        per_option: Dict[str, List[Dict[str, Any]]] = {}
        for opt, sig in zip(ids, sigs):
            h = _hits(sig, evidence)
            if h:
                per_option[opt] = h

        # an explicit "do nothing" option is satisfied only when nothing with a
        # matching effect happened at all
        noop_opts = [o for o, s in zip(ids, sigs) if "noop" in (s.get("effects") or [])]

        if not per_option:
            # Rule 1: absence is not defiance, and it is not a positive choice
            # either. Even when a "do nothing" option exists we cannot claim the
            # agent CHOSE it — the turn may have taken an unmodelled route, been
            # cut short, or been waiting on something. Record that the option was
            # present and let consumers apply their own stricter reading; never
            # manufacture a concordant/discordant label out of silence.
            return {"resolved_action": "", "verdict": NONE_OBSERVED, "hits": [],
                    "abstain_reason": "", "rule": RESOLVER_RULE_VERSION,
                    "noop_option_present": bool(noop_opts),
                    "noop_option": noop_opts[0] if noop_opts else ""}

        if len(per_option) > 1:
            # rule 2: two options matched -> the evidence does not decide it
            return {"resolved_action": "", "verdict": AMBIGUOUS,
                    "hits": [h for hs in per_option.values() for h in hs][:6],
                    "abstain_reason": "multiple_options_matched",
                    "matched": sorted(per_option),
                    "rule": RESOLVER_RULE_VERSION}

        opt = next(iter(per_option))
        # PROBE-VS-EXECUTE: inspection is a PRECONDITION of acting, not a
        # choice between options. When the only thing an agent did was read,
        # and the fork offered a committed action (write/exec), the read
        # cannot decide the fork — an agent about to write also inspects
        # first. Resolving here is what makes "last action before committing"
        # point at the option being checked for rejection.
        _committed = [i for i, sg in zip(ids, sigs)
                      if {"write", "exec"} & set(sg.get("effects") or [])]
        _hit_effects = {h.get("effect") for h in per_option[opt]}
        if _committed and _hit_effects and _hit_effects <= {"read"}:
            return {"resolved_action": "", "verdict": AMBIGUOUS,
                    "hits": per_option[opt], "abstain_reason": "probe_only",
                    "matched": sorted(per_option),
                    "rule": RESOLVER_RULE_VERSION}

        return {"resolved_action": opt,
                "verdict": CONCORDANT if choice == opt else DISCORDANT,
                "hits": per_option[opt], "abstain_reason": "",
                "rule": RESOLVER_RULE_VERSION}
    except Exception as _exc:  # noqa: BLE001
        return {"resolved_action": "", "verdict": UNCODABLE, "hits": [],
                "abstain_reason": f"error:{str(_exc)[:80]}",
                "rule": RESOLVER_RULE_VERSION}


def regime_for(trigger: str, trigger_kind: str = "") -> str:
    """Which measurement does this row support? Never pool across these."""
    try:
        t = f"{trigger or ''} {trigger_kind or ''}".lower()
        # midturn is checked FIRST: its trigger_kind is "midturn_hook" and its
        # delta source is a tool result, not a post-turn scan.
        if "midturn" in t:
            return REGIME_MIDTURN
        if "post" in t:
            return REGIME_AFTER_ACT
        if t.strip():
            return REGIME_IN_TURN
    except Exception:  # noqa: BLE001
        pass
    return REGIME_UNKNOWN
