"""v3.6.0 Phase-0 consult-suggestion budget ledger (BLUEPRINT v3.6.0 §2.5, P0.4).

The three structural consult gates (PRE/MID/POST) carry ONE suggestion each
with a hard ceiling of 3 per DELIVERED turn. The counter COMMITS on the
delivered terminal; a turn that never delivers (crash / /stop / client
timeout / session reset) spends tokens but NOT budget — its fired suggestions
flip to `abandoned` and the resumed task keeps its full allowance.

Persistence: `budget.jsonl` in the router's own profile-scoped storage dir
(same family as canonical + tokens ledgers). _TASK_STATE is in-memory (TTL
1h, cap 128) and dies with the gateway; the interrupted-turn rule must
survive restarts, so every budget event is appended here and replayed lazily
at dispatch time (bounded by task TTL).

Events (one JSON object per line, append-only):
  sugg_fired     {task_id, gate, seq, ts}
  sugg_delivered {task_id, gate, seq, ts}
  sugg_skipped   {task_id, gate, seq, reason, ts}
  turn_committed {task_id, delivered: [seq, ...], ts}
  turn_abandoned {task_id, abandoned: [seq, ...], ts}

canonical.py discipline (binding):
  - append-only, single open, chmod 0600, rotate past 8MB;
  - corrupt line -> skipped on replay (fail-open toward budget AVAILABILITY,
    never toward double-charging: an unparseable record can never count as a
    commit);
  - every failure -> silent no-op + debug log. Persistence must NEVER break
    the hook (the plugin's global posture).

PHASE-0 POSTURE: wired but INERT — the write-path functions exist and are
imported by __init__ (tap presence assert at register), but no gate fires
them yet (gates land in Phases 1-3). Events that DO flow today are the
turn-terminal commit/abandon lifecycle driven by transform_llm_output's
bookkeeping — zero spend, zero user-visible change.

Budget-gate reads (would_spend) consult replayed state so a future gate-fire
decision can enforce: sum(committed) < 3 (one-per-gate enforcement lands
with the gate registry in Phase 1).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

BUDGET_FILENAME = "hermes-router-budget.jsonl"
MAX_FILE_BYTES = 8 * 1024 * 1024  # rotate past 8 MB (canonical discipline)
_ROTATE_KEEP_LINES = 300
_REPLAY_SCAN_LINES = 4000  # bounded warm-up scan

SUGGESTIONS_PER_TASK = 3   # hard ceiling (§2.5 Rule 1) — settled, not a knob
VALID_GATES = ("pre", "mid", "post")

_lock = threading.Lock()
# task_id -> {"fired": set[seq], "delivered": set[seq], "skipped": set[seq],
#             "abandoned": set[seq], "seq": int}
_state: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
    "fired": set(), "delivered": set(), "skipped": set(), "abandoned": set(),
    "seq": 0,
})
# turn identity -> task_id for pending-abandon resolution: (session_id,
# turn_key) -> task_id. The interrupted-turn rule keys on the NEXT dispatch
# for the same turn (same task_id via resume identity) OR ledger replay.
_pending_turns: Dict[Tuple[str, str], str] = {}
_loaded = False

# ---- in-process event sequencing (§10.4-E shared-infra correlation) ----
_seq_lock = threading.Lock()
_event_seq = 0


def next_event_seq() -> int:
    """Monotonic event seq shared by tokens-ledger + banner writes so
    concurrent tool-cycle events never misattribute (blueprint §10.4-E)."""
    global _event_seq
    with _seq_lock:
        _event_seq += 1
        return _event_seq


def _store_path() -> str:
    """Profile-scoped ledger path via hermes_constants.get_hermes_home().
    Falls back to /tmp with a shadow-qualified name (mirrors canonical.py)."""
    try:
        import hermes_constants

        return str(hermes_constants.get_hermes_home() / BUDGET_FILENAME)
    except Exception:  # noqa: BLE001
        return os.path.join("/tmp", "shadow-" + BUDGET_FILENAME)


def _maybe_rotate_locked(path: str) -> None:
    try:
        if os.path.getsize(path) <= MAX_FILE_BYTES:
            return
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        keep = lines[-_ROTATE_KEEP_LINES:]
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.writelines(keep)
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001
        logger.debug("budget ledger rotate failed: %s", exc)


def _append(rec: Dict[str, Any]) -> bool:
    """Append one record. Canonical discipline: append-only, single open,
    chmod 0600, every failure silent. Returns True when written."""
    try:
        path = _store_path()
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with _lock:
            _maybe_rotate_locked(path)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        return True
    except Exception as exc:  # noqa: BLE001 — persistence must never break the hook
        logger.debug("budget ledger append failed: %s", exc)
        return False


def _load_locked() -> None:
    """Warm in-memory state from the ledger (bounded scan, corrupt lines
    skipped — fail-open toward availability, NEVER toward double-charging:
    only parseable, well-typed records mutate state)."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        path = _store_path()
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()[-_REPLAY_SCAN_LINES:]
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            _apply_replay(rec)
    except Exception as exc:  # noqa: BLE001
        logger.debug("budget ledger warm-up failed: %s", exc)


def _apply_replay(rec: Dict[str, Any]) -> None:
    """Fold one parsed record into in-memory state. Well-typedness checked —
    a malformed record degrades to a no-op (never invents a commit)."""
    try:
        ev = str(rec.get("event") or "")
        task_id = str(rec.get("task_id") or "")
        if not task_id:
            return
        st = _state[task_id]
        if ev == "sugg_fired":
            st["fired"].add(int(rec.get("seq") or 0))
        elif ev == "sugg_delivered":
            seq = int(rec.get("seq") or 0)
            st["delivered"].add(seq)
            st["fired"].discard(seq)
        elif ev == "sugg_skipped":
            st["skipped"].add(int(rec.get("seq") or 0))
        elif ev == "turn_committed":
            for seq in rec.get("delivered") or []:
                seq = int(seq)
                st["delivered"].add(seq)
                st["fired"].discard(seq)
                # committed-frontier marker: a replayed turn_committed means
                # these seqs are ALREADY committed — commit_turn must never
                # re-append them (no-double-charge, dedupes across restarts).
                st.setdefault("committed", set()).add(seq)
        elif ev == "turn_abandoned":
            for seq in rec.get("abandoned") or []:
                seq = int(seq)
                st["abandoned"].add(seq)
                st["fired"].discard(seq)
    except Exception:  # noqa: BLE001 — malformed record = no-op
        return


# ---------------------------------------------------------------------------
# Write-path API (Phase-0: wired but inert — gates call these from Phase 1+)
# ---------------------------------------------------------------------------


def mark_turn_pending(task_id: str, session_id: str, turn_key: str) -> None:
    """Register (session_id, turn_key) -> task_id so the next dispatch on the
    same turn (resume identity) can resolve the interrupted-turn rule.
    In-memory only (task TTL bounds it); ledger replay covers restarts.
    Never raises."""
    try:
        with _lock:
            _load_locked()
            _pending_turns[(str(session_id or ""), str(turn_key or ""))] = str(task_id or "")
            while len(_pending_turns) > 512:
                _pending_turns.pop(next(iter(_pending_turns)))
    except Exception:  # noqa: BLE001
        return


def pending_task_for(session_id: str, turn_key: str) -> Optional[str]:
    """Task id with uncommitted suggestions for this (session, turn), or None.
    Never raises."""
    try:
        with _lock:
            _load_locked()
            return _pending_turns.get((str(session_id or ""), str(turn_key or "")))
    except Exception:  # noqa: BLE001
        return None


def record_sugg_fired(task_id: str, gate: str) -> Optional[int]:
    """Gate fired one suggestion: append `sugg_fired`, return the seq.
    Budget accounting happens at COMMIT (delivered terminal) — firing only
    registers intent. Never raises; None on any failure."""
    try:
        gate = str(gate or "").lower()
        if gate not in VALID_GATES:
            return None
        with _lock:
            _load_locked()
            st = _state[str(task_id or "")]
            st["seq"] = int(st.get("seq", 0)) + 1
            seq = int(st["seq"])
        ok = _append({"event": "sugg_fired", "task_id": str(task_id or ""),
                      "gate": gate, "seq": seq, "ts": round(time.time(), 3)})
        if not ok:
            return None
        with _lock:
            _state[str(task_id or "")]["fired"].add(seq)
        return seq
    except Exception:  # noqa: BLE001
        return None


def record_sugg_delivered(task_id: str, gate: str, seq: int) -> bool:
    """The suggestion actually reached the agent (envelope landed pre-terminal).
    Delivered VALID suggestions are the only ones that count at commit. Never
    raises."""
    try:
        gate = str(gate or "").lower()
        if gate not in VALID_GATES or not seq:
            return False
        ok = _append({"event": "sugg_delivered", "task_id": str(task_id or ""),
                      "gate": gate, "seq": int(seq), "ts": round(time.time(), 3)})
        if ok:
            with _lock:
                st = _state[str(task_id or "")]
                st["delivered"].add(int(seq))
                st["fired"].discard(int(seq))
        return ok
    except Exception:  # noqa: BLE001
        return False


def record_sugg_skipped(task_id: str, gate: str, seq: Optional[int],
                        reason: str) -> bool:
    """Frontier timeout / malformed / cap / outage — budget untouched (only
    DELIVERED VALID suggestions count). Never raises."""
    try:
        gate = str(gate or "").lower()
        if gate not in VALID_GATES:
            return False
        ok = _append({"event": "sugg_skipped", "task_id": str(task_id or ""),
                      "gate": gate, "seq": int(seq or 0), "reason": str(reason or "")[:60],
                      "ts": round(time.time(), 3)})
        if ok and seq:
            with _lock:
                _state[str(task_id or "")]["skipped"].add(int(seq))
        return ok
    except Exception:  # noqa: BLE001
        return False


def commit_turn(task_id: str) -> List[int]:
    """Delivered-terminal commit (§2.5): flip every delivered suggestion of
    this task to committed. Append `turn_committed {delivered: [...]}`. Runs
    on EVERY delivered terminal of a consulted task, independent of whether
    the POST consult itself fires. IDEMPOTENT: a seq already folded by a
    previous turn_committed (or by replay) is never re-committed — the
    no-double-charge invariant. Never raises; [] on no-op."""
    try:
        with _lock:
            _load_locked()
            st = _state[str(task_id or "")]
            if "committed" not in st:
                st["committed"] = set()
            # Commit = DELIVERED-NOT-YET-COMMITTED entries. turn_committed's
            # replay fold keeps delivered membership AND marks the committed
            # frontier, so live commits and restart replays both dedupe.
            uncommitted = sorted(int(s) for s in st["delivered"]
                                 if int(s) not in st["committed"])
        if not uncommitted:
            return []
        ok = _append({"event": "turn_committed", "task_id": str(task_id or ""),
                      "delivered": uncommitted, "ts": round(time.time(), 3)})
        if ok:
            with _lock:
                st2 = _state[str(task_id or "")]
                st2.setdefault("committed", set()).update(uncommitted)
        return uncommitted if ok else []
    except Exception:  # noqa: BLE001
        return []


def abandon_turn(task_id: str) -> List[int]:
    """Interrupted-turn rule (§2.5 Rule 2): no terminal event arrived for this
    task's turn — flip every still-fired (never delivered) suggestion to
    `abandoned`. Budget-exempt, tokens-spent. Appends `turn_abandoned`.
    Never raises; [] on no-op."""
    try:
        with _lock:
            _load_locked()
            st = _state[str(task_id or "")]
            abandoned = sorted(int(s) for s in st["fired"])
        if not abandoned:
            return []
        ok = _append({"event": "turn_abandoned", "task_id": str(task_id or ""),
                      "abandoned": abandoned, "ts": round(time.time(), 3)})
        if ok:
            with _lock:
                st = _state[str(task_id or "")]
                st["abandoned"].update(abandoned)
                st["fired"].difference_update(abandoned)
        return abandoned if ok else []
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------------------
# Budget-gate reads (Phase-1+ consumers; Phase-0 callers are tests only)
# ---------------------------------------------------------------------------


def committed_count(task_id: str) -> int:
    """Number of committed (delivered-in-completed-turn) suggestions for the
    task. The 3-cap reads THIS, never consult_count. Never raises."""
    try:
        with _lock:
            _load_locked()
            return len(_state.get(str(task_id or ""), {}).get("delivered", ()))
    except Exception:  # noqa: BLE001
        return 0


def gate_delivered_count(task_id: str, gate: str) -> int:
    """Delivered suggestions on this task for ONE gate (one-per-gate rule —
    gate-scoped counting lands with the gate registry in Phase 1; Phase 0
    exposes the task-level read). Never raises."""
    try:
        return committed_count(task_id)
    except Exception:  # noqa: BLE001
        return 0


def would_spend(task_id: str, gate: str) -> Tuple[bool, str]:
    """Budget invariant check for a gate-fire decision (§2.5): True when
    sum(committed) < 3. Never raises."""
    try:
        gate = str(gate or "").lower()
        if gate not in VALID_GATES:
            return False, "invalid_gate"
        if committed_count(task_id) >= SUGGESTIONS_PER_TASK:
            return False, "budget_exhausted"
        return True, ""
    except Exception:  # noqa: BLE001
        return False, "ledger_error"


def task_summary(task_id: str) -> Dict[str, Any]:
    """Diagnostics (future /router budget surface). Never raises."""
    try:
        with _lock:
            _load_locked()
            st = _state.get(str(task_id or ""), {})
            return {
                "task_id": str(task_id or ""),
                "fired": sorted(int(s) for s in st.get("fired", ())),
                "delivered": sorted(int(s) for s in st.get("delivered", ())),
                "skipped": sorted(int(s) for s in st.get("skipped", ())),
                "abandoned": sorted(int(s) for s in st.get("abandoned", ())),
                "committed_total": len(st.get("delivered", ())),
            }
    except Exception:  # noqa: BLE001
        return {"task_id": str(task_id or ""), "fired": [], "delivered": [],
                "skipped": [], "abandoned": [], "committed_total": 0}


def replay_events(max_lines: int = 2000) -> List[Dict[str, Any]]:
    """Bounded raw read of the budget ledger (tests + replay tooling).
    Corrupt/torn lines skipped. Never raises."""
    out: List[Dict[str, Any]] = []
    try:
        path = _store_path()
        if not os.path.exists(path):
            return out
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()[-max(1, int(max_lines)):]
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
        return out
    except Exception:  # noqa: BLE001
        return out


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def clear_for_tests() -> None:
    """Reset in-memory replay state (tests only; the ledger file itself is
    test-isolated via _store_path monkeypatch)."""
    global _loaded
    with _lock:
        _state.clear()
        _pending_turns.clear()
        _loaded = False
