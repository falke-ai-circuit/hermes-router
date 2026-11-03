"""v3.6.0 decision records — §12 amendment items 5+6 (2026-09-07, luna-pro
ratified simplicity audit).

Item 5 — VERSIONED MINIMAL SCHEMA. Every detector decision (PRE route, POST
detection, cascade, weak-compliance) can emit one record:

  {schema: "decision-record", schema_version: 1, ts, event_seq,
   detector_version, policy_version, rule_id, evidence_ref, action,
   outcome, trace_id, task_id, session_id, latency_ms,
   fail_class, fail_detail, doctrine_line_ref, matched_pattern}

Field discipline (binding):
  - evidence_ref = HASH of the evidence text (16-hex, bounded) — raw prompts,
    outputs, and pattern text never persist here; matched_pattern carries the
    RULE ID (pattern-group / feature name), not raw matched text;
  - doctrine_line_ref present ONLY when the doctrine verdict actually
    participated in the decision;
  - fail_class is a SEPARATE, dedicated field (item 6): provider timeouts are
    recorded fail_class=provider_timeout and NEVER as a policy rejection —
    policy_version stays "advisory" (nothing is rejected at Phase 0) and a
    failure NEVER masquerades as a policy outcome;
  - emit is ASYNC (item 5): record_decision() only serializes + enqueues
    (O(1), never blocking the turn); a daemon thread drains the queue and
    appends to hermes-router-decisions.jsonl (canonical discipline:
    append-only, chmod 0600, rotate, corrupt-record tolerance on read).
    Queue overflow -> drop + debug log (observability must never break the
    lane, and must never grow unbounded).
  - Full prompts are NOT stored here — retention/debug/review surfaces read
    the route log, which stays enum+count only.

Phase-0 posture: wired but INERT — emit sites are the cascade/shadow paths
and tests; no gate or user-visible behavior reads these records yet.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

SCHEMA_NAME = "decision-record"
SCHEMA_VERSION = 2          # v2 = M35-01 provenance fields (lane/rule/gate/intent/replaced)
DECISIONS_FILENAME = "hermes-router-decisions.jsonl"
MAX_FILE_BYTES = 8 * 1024 * 1024
_ROTATE_KEEP_LINES = 300
_QUEUE_MAX = 512

# §12-6 failure classes — dedicated enum, orthogonal to `action`/`outcome`.
FAIL_CLASS_NONE = ""                    # no failure involved
FAIL_CLASS_PROVIDER_TIMEOUT = "provider_timeout"

POLICY_VERSION = "advisory"   # Phase 0: nothing is rejected; advisory only

_q: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue(maxsize=_QUEUE_MAX)
_worker_started = False
_worker_lock = threading.Lock()


def _store_path() -> str:
    try:
        import hermes_constants

        return str(hermes_constants.get_hermes_home() / DECISIONS_FILENAME)
    except Exception:  # noqa: BLE001
        return os.path.join("/tmp", "shadow-" + DECISIONS_FILENAME)


def evidence_ref(text: str) -> str:
    """Bounded hash of evidence text — raw content never persists."""
    try:
        return hashlib.sha256(str(text or "").encode("utf-8", errors="replace")).hexdigest()[:16]
    except Exception:  # noqa: BLE001
        return ""


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
        logger.debug("decisions rotate failed: %s", exc)


def _append_record(rec: Dict[str, Any]) -> bool:
    try:
        path = _store_path()
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("decisions append failed: %s", exc)
        return False


def _drain_loop() -> None:
    while True:
        try:
            rec = _q.get(timeout=2.0)
        except queue.Empty:
            continue
        if rec is None:
            break  # shutdown sentinel
        try:
            _maybe_rotate_locked(_store_path())
            _append_record(rec)
        except Exception:  # noqa: BLE001 — worker must never die
            pass
        finally:
            try:
                _q.task_done()
            except Exception:  # noqa: BLE001
                pass


def _ensure_worker() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        try:
            t = threading.Thread(target=_drain_loop, name="router-decisions-emit", daemon=True)
            t.start()
            _worker_started = True
        except Exception:  # noqa: BLE001 — async emit is best-effort
            pass


def record_decision(*, detector_version: str, rule_id: str, action: str,
                    outcome: str = "", trace_id: str = "", task_id: str = "",
                    session_id: str = "", latency_ms: float = 0.0,
                    evidence_text: str = "", matched_pattern: str = "",
                    doctrine_line_ref: str = "",
                    fail_class: str = FAIL_CLASS_NONE,
                    fail_detail: str = "",
                    lane: str = "", turn_id: str = "",
                    gate_verdict: str = "", gate_reason: str = "",
                    intent_class: str = "", intent_source: str = "",
                    replaced: Optional[bool] = None,
                    delivered: Optional[bool] = None,
                    replacement_path: str = "",
                    body_chars: int = 0, delivered_chars: int = 0) -> bool:
    """Enqueue one decision record. ASYNC — never blocks the turn beyond the
    serialize + put_nowait (O(1)); overflow drops (bounded). §12-6: a
    non-empty fail_class NEVER coexists with a policy-rejection outcome —
    provider_timeout/transport/http_status are infra facts, recorded as
    such, and policy_version stays advisory. Returns True when enqueued.
    Never raises.

    M35-01 PROVENANCE (schema v2). A row that says only `action=pass` cannot
    be graded, because nothing on it names the lane that produced the body.
    These optional fields are the provenance row, written on EVERY eligible
    turn — including one that did not route, since a benign turn's non-routing
    is the evidence of selectivity:

      lane              id of the lane whose text was DELIVERED ("main" = the
                        agent's own path, no lane text reached the user)
      rule              "rule_1".."rule_6" | "none" — which table row fired
      gate_verdict      flagged | not_flagged | abstain
      gate_reason       the gate record's reason token, or "not_consulted"
      intent_class      the POST judge's class, or "" when never consulted
      intent_source     deterministic | aux | unknown | not_consulted
      replaced          True iff the delivered body came from a lane render
      replacement_path  the replacement chain entry that produced it
      latency_ms        SYNCHRONOUS time the turn spent inside the seam
      turn_id           join key to the delivered body (with session_id)

    `replaced=None` (the default) means "this call is not a delivery row" — the
    field is omitted, so pre-M35 emit sites keep their exact v1 shape apart from
    the version stamp.
    """
    try:
        from .. import suggestions

        rec: Dict[str, Any] = {
            "schema": SCHEMA_NAME,
            "schema_version": SCHEMA_VERSION,
            "ts": round(time.time(), 3),
            "event_seq": suggestions.next_event_seq(),
            "detector_version": str(detector_version or "")[:40],
            "policy_version": POLICY_VERSION,
            "rule_id": str(rule_id or "")[:60],
            "evidence_ref": evidence_ref(evidence_text),
            "action": str(action or "")[:20],
            "outcome": str(outcome or "")[:20],
            "trace_id": str(trace_id or "")[:48],
            "task_id": str(task_id or "")[:40],
            "session_id": str(session_id or "")[:48],
            "latency_ms": round(max(0.0, float(latency_ms or 0.0)), 3),
            "matched_pattern": str(matched_pattern or "")[:60],
            "fail_class": str(fail_class or FAIL_CLASS_NONE)[:24],
            "fail_detail": (str(fail_detail or "")[:120] if fail_class else ""),
        }
        # M35-01: the provenance block. Present only on a DELIVERY row
        # (`replaced` is not None), so a pre-M35 caller keeps its v1 field set.
        if replaced is not None:
            rec.update({
                "lane": str(lane or "")[:40],
                "turn_id": str(turn_id or "")[:48],
                "rule": str(rule_id or "none")[:40],
                "gate_verdict": str(gate_verdict or "")[:16],
                "gate_reason": str(gate_reason or "")[:40],
                "intent_class": str(intent_class or "")[:24],
                "intent_source": str(intent_source or "")[:20],
                "replaced": bool(replaced),
                "replacement_path": str(replacement_path or "")[:60],
                "body_chars": int(body_chars or 0),
                "delivered_chars": int(delivered_chars or 0),
            })
            # M35-02: WIRE TRUTH, separate from the decision.
            # `replaced` says the seam DECIDED to swap the body; `delivered`
            # says the swap reached the response surface. The B3 defect was a
            # row claiming replaced=True while the caller received the main
            # agent's refusal — a pair no reader could falsify. Keeping the
            # two facts apart makes that state expressible instead of
            # invisible: replaced=True + delivered=False IS the defect
            # signature, and it is now a row a battery can count.
            if delivered is not None:
                rec["delivered"] = bool(delivered)
        if doctrine_line_ref:
            rec["doctrine_line_ref"] = str(doctrine_line_ref)[:48]
        # §12-6 hard shape: failures are failures, not policy outcomes.
        if rec["fail_class"]:
            rec["outcome"] = "failure"
            if rec["fail_class"] == FAIL_CLASS_PROVIDER_TIMEOUT:
                rec["fail_detail"] = rec["fail_detail"] or "provider_timeout"
        _ensure_worker()
        try:
            _q.put_nowait(rec)
            return True
        except queue.Full:
            logger.debug("decisions queue full — record dropped")
            return False
    except Exception:  # noqa: BLE001
        return False


def read_records(max_lines: int = 500) -> list:
    """Bounded read of the decisions ledger (tests + future /router surface).
    Corrupt lines skipped. Never raises."""
    out = []
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


def flush_for_tests(timeout: float = 3.0) -> None:
    """Tests-only: wait for the queue to drain (records are async)."""
    try:
        _ensure_worker()
        _q.join()
    except Exception:  # noqa: BLE001
        pass


def clear_for_tests() -> None:
    """Tests-only: drop queued records (ledger file is test-isolated via
    _store_path monkeypatch)."""
    try:
        while True:
            _q.get_nowait()
    except queue.Empty:
        pass
    except Exception:  # noqa: BLE001
        pass
