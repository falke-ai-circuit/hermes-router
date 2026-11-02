"""features/detection/rule_table.py — THE ORDERED ROUTING TABLE (M41-05, spec §E).

WHAT THIS IS
    The six-rule table, evaluated first-match-wins as DATA. The rows are the
    lane contract's `gate_semantics` tuple (lanes/builtins.py) — this module
    only walks them. It holds no lane knowledge and no body heuristics, so a
    second lane is one `LaneSpec` literal, never an edit here or in the POST
    handler.

WHY THE ORDER IS WHAT IT IS (spec §E, gaps B1/B2)
    Round 1  declared_refusal                        -> route
    Round 2  intent_declined                         -> route
    Round 3  gate_abstain                            -> fallback_shape
    Round 4  intent_softened_and_gate_flag           -> route_replace
    Round 5  intent_softened                          -> deliver
    Round 6  else                                     -> deliver

    Rules 1-2 are self-evident evidence and route whatever the gate said.
    Rule 3 is where `abstain` lives: `abstain` is NOT a value of the
    flagged/not-flagged binary, so with the row ordered last (the design's
    prose order) an abstained gate could never reach the fallback — every
    earlier row matches on intent alone. Hoisting it to 3 is the B2 fix, as a
    data row. B1 is row 5's predicate: the gate term carried no information
    (the gate filters `softened` only), so row 5 is intent-only and row 6
    (`else`) makes the table TOTAL over every (gate, intent) cell.

TOTALITY IS STRUCTURAL
    `else` matches unconditionally, so any state whose earlier rows miss lands
    on row 6. If the contract is missing or malformed the evaluator does not
    invent a row: it returns `fallback_shape` with `reason="no_row_matched"`,
    which is today's body path — never a silent drop, never a route.

LAZINESS IS A CONTRACT
    Providers are called only when a row needs them, and rule 1 is decided
    from the DECLARATION ALONE — its gate/intent providers are never called.
    That is the mechanical form of "nothing outranks a declared refusal,
    especially not the gate": a gate record that says "not in domain" is not
    only ignored on the outcome, it is never read.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# the closed vocabulary (spec §B/§E)
# ---------------------------------------------------------------------------
PRED_DECLARED_REFUSAL = "declared_refusal"
PRED_INTENT_DECLINED = "intent_declined"
PRED_GATE_ABSTAIN = "gate_abstain"
PRED_SOFTENED_AND_GATE = "intent_softened_and_gate_flag"
PRED_INTENT_SOFTENED = "intent_softened"
PRED_ELSE = "else"

PREDICATES: Tuple[str, ...] = (
    PRED_DECLARED_REFUSAL,
    PRED_INTENT_DECLINED,
    PRED_GATE_ABSTAIN,
    PRED_SOFTENED_AND_GATE,
    PRED_INTENT_SOFTENED,
    PRED_ELSE,
)

ACTION_ROUTE = "route"
ACTION_ROUTE_REPLACE = "route_replace"
ACTION_FALLBACK_SHAPE = "fallback_shape"
ACTION_DELIVER = "deliver"

ACTIONS: Tuple[str, ...] = (
    ACTION_ROUTE, ACTION_ROUTE_REPLACE, ACTION_FALLBACK_SHAPE, ACTION_DELIVER,
)

# The gate's closed verdict set, plus the two non-verdict states a reader can
# see. `pending` is a record the worker has not landed yet; both read as
# abstain, never as "safe" (domain_gate.verdict does the same collapse).
GATE_FLAGGED = "flagged"
GATE_NOT_FLAGGED = "not_flagged"
GATE_ABSTAIN = "abstain"
GATE_PENDING = "pending"
GATE_ABSTAIN_STATES = (GATE_ABSTAIN, GATE_PENDING)

# What a route seeds on `matches`, per predicate. Data, so the POST handler
# never maps a predicate token itself.
_SEEDS: Dict[str, str] = {
    PRED_DECLARED_REFUSAL: "action_denial",   # the existing declared shape
    PRED_INTENT_DECLINED: "intent_declined",
    PRED_SOFTENED_AND_GATE: "intent_softened",
}

# Which trigger kind a routed row enters. `post_class` is the declared shape's
# existing kind (it already bypasses the body-shape veto); `post_intent` is new
# in this unit and must be added to that same exemption tuple, or a long fluent
# decline is re-read and vetoed — the measured miss class.
_TRIGGER_KINDS: Dict[str, str] = {
    PRED_DECLARED_REFUSAL: "post_class",
    PRED_INTENT_DECLINED: "post_intent",
    PRED_SOFTENED_AND_GATE: "post_intent",
}

# Rows whose route REPLACES the delivered body (M41-06, corrected by M41-07b).
# Data, so the handler never decides which rules swap. The set is EVERY routing
# predicate, and that is a measured fact, not a preference: the POST seam renders
# and swaps for every action in {route, route_replace} — the render at
# gate/orchestration.py:2900 and the canonical swap below it are unconditional
# inside the routed block. Rule 2 was missing from this set, so a rule-2 turn
# logged replace=0/replacement="" while its body WAS swapped (M41-07_FALSIFY §9
# D1) and M41-08's replacement census would have missed every rule-2 turn.
# A routing row that did not swap would need a second delivery path; there is
# none, so adding a route row here is part of adding a route row at all.
_REPLACING_PREDICATES: Tuple[str, ...] = (
    PRED_DECLARED_REFUSAL,
    PRED_INTENT_DECLINED,
    PRED_SOFTENED_AND_GATE,
)

# The contract's POST question token. Kept here so the table can resolve its
# own rows without importing the judge at module load; a test asserts this is
# the same string as intent_judge.POST_QUESTION.
POST_QUESTION = "uncensored_intent"


def replacement_token() -> str:
    """The POST lane contract's `replacement` FIELD (M41-06, spec §B S9).

    Resolved by token through the lane registry, exactly like the rows and the
    question — never by comparing a lane id, and never from a gate record. That
    second point is the silent-suppression guarantee: a declared refusal on a
    turn where the gate never landed still knows which chain replaces its body,
    because the answer came from the contract, not from the gate.
    """
    try:
        from ...lanes import registry as _registry

        lane_id = _registry.lane_for_post_question(POST_QUESTION)
        contract = _registry.resolve_contract(str(lane_id or ""))
        return str(getattr(contract, "replacement", "") or "")
    except Exception:  # noqa: BLE001 — a registry gap must not break delivery
        return ""


# ---------------------------------------------------------------------------
# row resolution
# ---------------------------------------------------------------------------

def default_semantics() -> Tuple[Tuple[str, str], ...]:
    """The ordered rows for the lane that owns the POST question token.

    Resolved by TOKEN through the lane registry (never by comparing a lane
    id), so a second lane with its own contract needs no code change here.
    Missing contract => empty tuple => the evaluator's `no_row_matched`
    fallback (today's body path), never an invented route.
    """
    try:
        from ...lanes import registry as _registry

        lane_id = _registry.lane_for_post_question(POST_QUESTION)
        contract = _registry.resolve_contract(str(lane_id or ""))
        return tuple(getattr(contract, "gate_semantics", ()) or ())
    except Exception:  # noqa: BLE001 — a registry gap must not break delivery
        return ()


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------

def _row(row: Any) -> Optional[Tuple[str, str]]:
    """One (predicate, action) pair, or None when the row is malformed."""
    try:
        pred, action = str(row[0]), str(row[1])
    except Exception:  # noqa: BLE001
        return None
    if pred not in PREDICATES or action not in ACTIONS:
        return None
    return pred, action


def evaluate(*, declaration: str,
             gate_provider: Optional[Callable[[], str]] = None,
             intent_provider: Optional[Callable[[], Optional[str]]] = None,
             semantics: Optional[Sequence[Sequence[str]]] = None) -> Dict[str, Any]:
    """Walk the ordered rows; first match wins.

    CALLER CONTRACT
        declaration       "true" | "false" | "UNKNOWN" (declaration.py's set)
        gate_provider     () -> "flagged" | "not_flagged" | "abstain" | "pending"
        intent_provider   () -> one of intent_judge.INTENT_CLASSES | None
        semantics         the ordered rows; default = the POST lane's contract

    Returned dict: row (1-based, 0 = no row matched), predicate, action, seed
    (the `matches` string for a route), trigger_kind, gate, intent,
    declaration, semantics_rows, reason. Never raises: a provider error reads
    as "that fact is absent", which cannot route.
    """
    rows = tuple(semantics) if semantics is not None else default_semantics()
    state: Dict[str, Any] = {
        "declaration": str(declaration or "UNKNOWN"),
        "gate": None,
        "intent": None,
        "gate_read": False,
        "intent_read": False,
    }

    def _gate() -> str:
        if not state["gate_read"]:
            state["gate_read"] = True
            value = None
            if gate_provider is not None:
                try:
                    value = gate_provider()
                except Exception:  # noqa: BLE001 — absent evidence is abstain
                    value = None
            value = str(value or GATE_ABSTAIN)
            state["gate"] = value if value in (
                GATE_FLAGGED, GATE_NOT_FLAGGED) + GATE_ABSTAIN_STATES \
                else GATE_ABSTAIN
        return state["gate"]

    def _intent() -> Optional[str]:
        if not state["intent_read"]:
            state["intent_read"] = True
            value = None
            if intent_provider is not None:
                try:
                    value = intent_provider()
                except Exception:  # noqa: BLE001 — absent evidence is None
                    value = None
            state["intent"] = str(value) if value else None
        return state["intent"]

    for index, raw in enumerate(rows, 1):
        pair = _row(raw)
        if pair is None:
            continue
        pred, action = pair
        if pred == PRED_DECLARED_REFUSAL:
            matched = state["declaration"] == "true"
        elif pred == PRED_INTENT_DECLINED:
            matched = _intent() == "declined"
        elif pred == PRED_GATE_ABSTAIN:
            matched = _gate() in GATE_ABSTAIN_STATES
        elif pred == PRED_SOFTENED_AND_GATE:
            matched = _intent() == "softened" and _gate() == GATE_FLAGGED
        elif pred == PRED_INTENT_SOFTENED:
            matched = _intent() == "softened"
        else:  # PRED_ELSE — unconditional, and that is what makes the table total
            matched = True
        if matched:
            return _decision(index, pred, action, state, len(rows), "")

    return _decision(0, "", ACTION_FALLBACK_SHAPE, state, len(rows),
                     "no_row_matched")


def _decision(row: int, pred: str, action: str, state: Dict[str, Any],
              total: int, reason: str) -> Dict[str, Any]:
    return {
        "row": row,
        "predicate": pred,
        "action": action,
        "seed": _SEEDS.get(pred, ""),
        "trigger_kind": _TRIGGER_KINDS.get(pred, ""),
        # Does this row's route REPLACE the body? Data (see _REPLACING_PREDICATES),
        # so the handler dispatches on the table's own answer.
        "replace": pred in _REPLACING_PREDICATES,
        "gate": state.get("gate"),
        "intent": state.get("intent"),
        "declaration": state.get("declaration"),
        "semantics_rows": total,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# totality walk — the test's instrument, and M41-08's row census
# ---------------------------------------------------------------------------
GATE_CELLS: Tuple[str, ...] = (GATE_FLAGGED, GATE_NOT_FLAGGED, GATE_ABSTAIN,
                               GATE_PENDING)
INTENT_CELLS: Tuple[str, ...] = ("delivered", "softened", "declined",
                                 "deflected", "failed")


def walk_cells(*, semantics: Optional[Sequence[Sequence[str]]] = None,
               declarations: Sequence[str] = ("true", "false", "UNKNOWN"),
               ) -> Tuple[Dict[str, Any], ...]:
    """Every (declaration, gate, intent) cell, evaluated through the real
    evaluator. The test uses this for totality; M41-08 uses the same instrument
    for the per-rule census. No cell may return row 0 or an unknown action."""
    out = []
    for declaration in declarations:
        for gate in GATE_CELLS:
            for intent in INTENT_CELLS:
                decision = evaluate(
                    declaration=declaration,
                    gate_provider=lambda g=gate: g,
                    intent_provider=lambda i=intent: i,
                    semantics=semantics,
                )
                decision["cell"] = (declaration, gate, intent)
                out.append(decision)
    return tuple(out)
