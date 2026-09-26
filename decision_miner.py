"""R19 step 2 — decision_miner: the lane's memory as decision RECORDS.

Spec §10 (extension, user-directed): the lane's precedent base must be
decision-shaped episodes, not prose soup. Two producers feed one store:

- MINED (provenance_tag="mined"): bounded, resumable walk of the loading
  profile's own session history (state.db messages, read-only URI,
  sqlite_master-guarded) + evol.jsonl tail. Detection reuses
  decision.detect() marker families + outcome heuristics (subsequent
  error/undo/continue signals; explicit user corrections weigh most).
- POST-AUDITED (provenance_tag="post_audit"): written by the POST leg
  (decision_miner.post_audit_scan) for every decision-shaped ACTION the
  agent took during an autonomous run.

Storage: `decision_records` table in the PLUGIN state DB (own DB —
hermes_router_state.db; NO core schema change) with an FTS5 index and a
miner resume-cursor table. Re-runnable: the cursor remembers the last
scanned timestamp per source; already-stored records are deduped on id.

Echo-loop guard (frontier #3, applied at miner level too): retrieval
excludes anything tagged lane_advisory — the tag never occurs on records,
but the exclusion is enforced in WHERE clause AND in Python so an echo
loop is impossible by construction even if a future producer tried.

Bounded: miner_max_records rows in the store, miner_scan_days window,
per-run batch caps, per-snippet caps. Fail-open throughout — every public
entry swallows exceptions and returns degraded-but-safe results.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

TAG_MINED = "mined"
TAG_LIVE = "live"
TAG_POST_AUDIT = "post_audit"
TAG_LANE_ADVISORY = "lane_advisory"  # never stored; excluded from retrieval

PROVENANCE_TAGS = (TAG_MINED, TAG_LIVE, TAG_POST_AUDIT)

SITUATION_CAP = 500
OUTCOME_CAP = 300

# Schema (plugin-owned DB only)
_SCHEMA = """
CREATE TABLE IF NOT EXISTS decision_records (
    id TEXT PRIMARY KEY,
    ts REAL,
    situation_text TEXT,
    options TEXT,
    chosen TEXT,
    outcome TEXT,
    outcome_ts REAL,
    source TEXT,
    provenance_tag TEXT,
    created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_decision_records_ts
    ON decision_records (ts);
CREATE VIRTUAL TABLE IF NOT EXISTS decision_records_fts USING fts5(
    situation_text, outcome, content='decision_records', content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS decision_records_ai AFTER INSERT ON decision_records BEGIN
    INSERT INTO decision_records_fts(rowid, situation_text, outcome)
    VALUES (new.rowid, new.situation_text, new.outcome);
END;
CREATE TRIGGER IF NOT EXISTS decision_records_ad AFTER DELETE ON decision_records BEGIN
    INSERT INTO decision_records_fts(decision_records_fts, rowid,
                                     situation_text, outcome)
    VALUES ('delete', old.rowid, old.situation_text, old.outcome);
END;
CREATE TABLE IF NOT EXISTS miner_cursor (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

_LOCK = threading.Lock()


def _cfg() -> Dict[str, Any]:
    """Decision block incl. miner knobs (delegates to decision._cfg so the
    dual-block reader stays in one place). Never raises."""
    try:
        from . import decision

        return decision._cfg()
    except Exception:  # noqa: BLE001
        return dict(decision.DEFAULTS) if _has_decision() else {}


def _has_decision() -> bool:
    try:
        from . import decision  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Plugin state DB (own DB — no core schema change)
# ---------------------------------------------------------------------------

def plugin_db_path() -> str:
    """Plugin state DB path: hermes_home/hermes_router_state.db. '' on miss."""
    try:
        import hermes_constants

        return str(hermes_constants.get_hermes_home() / "hermes_router_state.db")
    except Exception:  # noqa: BLE001
        return ""


def _connect(db_path: str = "", timeout: float = 5.0) -> Optional[sqlite3.Connection]:
    try:
        path = db_path or plugin_db_path()
        if not path:
            return None
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        conn = sqlite3.connect(path, timeout=timeout)
        conn.executescript(_SCHEMA)
        return conn
    except Exception:  # noqa: BLE001 — fail-open: unusable store = no-op miner
        return None


# ---------------------------------------------------------------------------
# Records write / read
# ---------------------------------------------------------------------------

def record_id(source: str, ts: Any, situation: str) -> str:
    """Stable dedupe id: sha1(source|ts|situation[:200])."""
    import hashlib

    try:
        h = hashlib.sha1(
            ("%s\x00%s\x00%s" % (source, ts, (situation or "")[:200]))
            .encode("utf-8", "replace")).hexdigest()
        return "drec_%s" % h[:20]
    except Exception:  # noqa: BLE001
        return "drec_%d" % (time.time_ns() % 10_000_000)


def write_record(situation_text: str, options: List[str], chosen: str,
                 outcome: str, outcome_ts: Optional[float], source: str,
                 provenance_tag: str, ts: Optional[float] = None,
                 db_path: str = "") -> Optional[str]:
    """Insert one decision record (+FTS trigger row). Dedupes on id.
    Returns the record id, or None on any failure. Never raises."""
    try:
        if provenance_tag not in PROVENANCE_TAGS:
            return None  # lane_advisory or junk tags are never stored
        conn = _connect(db_path)
        if conn is None:
            return None
        try:
            rid = record_id(source, ts if ts is not None else time.time(),
                            situation_text)
            cur = conn.execute(
                "INSERT OR IGNORE INTO decision_records"
                " (id, ts, situation_text, options, chosen, outcome,"
                "  outcome_ts, source, provenance_tag, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (rid, float(ts if ts is not None else time.time()),
                 str(situation_text or "")[:SITUATION_CAP],
                 json.dumps([str(o)[:120] for o in (options or [])][:8]),
                 str(chosen or "")[:200],
                 str(outcome or "")[:OUTCOME_CAP],
                 (float(outcome_ts) if outcome_ts is not None else None),
                 str(source or "")[:120], provenance_tag, time.time()))
            conn.commit()
            return rid if cur.rowcount else None
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return None


def record_count(db_path: str = "") -> int:
    try:
        conn = _connect(db_path)
        if conn is None:
            return 0
        try:
            return int(conn.execute(
                "SELECT count(*) FROM decision_records").fetchone()[0])
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return 0


def retrieve(task_text: str, limit: int = 8, db_path: str = "",
             cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """FTS top-K decision records for frame building. Echo-loop guard:
    provenance_tag <> lane_advisory enforced in SQL and again in Python.
    Age-capped by miner_scan_days. Fail-open []."""
    try:
        cfg = cfg or _cfg()
        conn = _connect(db_path)
        if conn is None:
            return []
        try:
            terms = " OR ".join(
                w for w in re.findall(r"[A-Za-z0-9_]{3,}",
                                      str(task_text or ""))[:12])
            if not terms:
                return []
            age_cap = float(cfg.get("miner_scan_days") or 90) * 86400.0
            cutoff = time.time() - age_cap
            rows = conn.execute(
                "SELECT r.id, r.ts, r.situation_text, r.options, r.chosen,"
                " r.outcome, r.outcome_ts, r.source, r.provenance_tag"
                " FROM decision_records_fts f"
                " JOIN decision_records r ON r.rowid = f.rowid"
                " WHERE decision_records_fts MATCH ?"
                "   AND r.provenance_tag <> ?"
                "   AND (r.ts IS NULL OR r.ts >= ?)"
                " ORDER BY r.ts DESC LIMIT ?",
                (terms, TAG_LANE_ADVISORY, cutoff, int(limit)),
            ).fetchall()
            out: List[Dict[str, Any]] = []
            for r in rows:
                # second, in-Python echo-filter (belt and braces)
                if str(r[8]) == TAG_LANE_ADVISORY:
                    continue
                try:
                    options = json.loads(r[3]) if r[3] else []
                except Exception:  # noqa: BLE001
                    options = []
                out.append({"id": str(r[0]), "ts": r[1],
                            "situation": str(r[2] or ""),
                            "options": options, "chosen": str(r[4] or ""),
                            "outcome": str(r[5] or ""), "outcome_ts": r[6],
                            "source": str(r[7] or ""), "tag": str(r[8])})
            return out
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------------------
# Cursor (resume)
# ---------------------------------------------------------------------------

def _cursor_get(conn: sqlite3.Connection, key: str) -> Optional[str]:
    try:
        row = conn.execute("SELECT value FROM miner_cursor WHERE key = ?",
                           (key,)).fetchone()
        return str(row[0]) if row else None
    except Exception:  # noqa: BLE001
        return None


def _cursor_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    try:
        conn.execute("INSERT OR REPLACE INTO miner_cursor (key, value)"
                     " VALUES (?,?)", (key, str(value)))
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Detection: marker families (reuse decision.detect families) + outcomes
# ---------------------------------------------------------------------------

_OPTION_RE = re.compile(
    r"\b(option [a-d1-4]|approach [ab12]|alternative[sd]?:)\b", re.IGNORECASE)
_EITHER_RE = re.compile(r"\beither\b(.{0,80})\bor\b", re.IGNORECASE | re.DOTALL)

_CHOSEN_RE = re.compile(
    r"\b(went with|chose|chosen|choosing|picked|going with|"
    r"we'?ll (use|go with|apply)|applying|rollback(?:ed)? to|"
    r"revert(?:ed)? to)\b\s*(.{0,120})", re.IGNORECASE)

# Outcome heuristics — subsequent error/undo/continue signals. Explicit
# user corrections weigh most (weight 3 vs 1).
_OUTCOME_SIGNALS: List[Any] = [
    (3, re.compile(r"\b(no,? (that|this)|that'?s wrong|not what i (said|asked)"
                   r"|correction:|undo that|revert this|roll ?back)\b",
                   re.IGNORECASE)),
    (1, re.compile(r"\b(error|failed|failure|crash(?:ed)?|broke(?:n)?|"
                   r"doesn'?t work|didn'?t work|not working|exception|"
                   r"traceback|regression)\b", re.IGNORECASE)),
    (1, re.compile(r"\b(undo|revert|rollback|redo|try again|retrying|"
                   r"instead (of|we)|switch(?:ed)? to|going back)\b",
                   re.IGNORECASE)),
    (1, re.compile(r"\b(continues?|proceeding|worked|passing now|"
                   r"fixed now|resolved)\b", re.IGNORECASE)),
]


def detect_episode(text: str) -> Optional[Dict[str, Any]]:
    """Decision-shaped episode detector for mining: reuses decision.detect()
    marker families (aggressive semantics — any family, since historical
    prose is scarcer than live traffic), plus option enumeration. None when
    not decision-shaped. Never raises."""
    try:
        if not isinstance(text, str) or len(text.strip()) < 12:
            return None
        from . import decision as _d

        hit = _d.detect(text, 3)
        if not hit or not hit.get("families"):
            return None
        options: List[str] = [m.group(0) for m in _OPTION_RE.finditer(text)]
        m = _EITHER_RE.search(text)
        if m:
            options.append(("either%s" % m.group(1))[:120])
        return {"families": hit["families"], "options": options[:8]}
    except Exception:  # noqa: BLE001
        return None


def outcome_signal(text: str) -> Optional[str]:
    """Outcome heuristic: strongest signal class in text, or None.
    'user_correction' > 'error' > 'undo' > 'continue'. Never raises."""
    try:
        if not isinstance(text, str) or not text.strip():
            return None
        best: Any = None
        for weight, rx in _OUTCOME_SIGNALS:
            if rx.search(text):
                if best is None or weight > best[0]:
                    best = (weight, rx)
        if best is None:
            return None
        joined = " ".join(rx.pattern[:24] for w, rx in _OUTCOME_SIGNALS[:1])
        if best[1] is _OUTCOME_SIGNALS[0][1]:
            return "user_correction"
        if _OUTCOME_SIGNALS[1][1].search(text):
            return "error"
        if _OUTCOME_SIGNALS[2][1].search(text):
            return "undo"
        return "continue"
    except Exception:  # noqa: BLE001
        return None


def _chosen_of(text: str) -> str:
    try:
        m = _CHOSEN_RE.search(text or "")
        return (m.group(2) or "").strip()[:200] if m else ""
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------------------
# Miner: bounded, resumable walk
# ---------------------------------------------------------------------------

def _messages_iter(conn_ro: sqlite3.Connection, cutoff: float,
                   batch: int) -> Any:
    """Yield (session_id, ts, content) message rows newer than cutoff,
    oldest-first, batch-bounded. sqlite_master-guarded."""
    have = conn_ro.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
        " AND name = 'messages'").fetchall()
    if not have:
        return
    for sid, ts, content in conn_ro.execute(
            "SELECT session_id, timestamp, content FROM messages"
            " WHERE content IS NOT NULL AND trim(content) <> ''"
            "   AND timestamp IS NOT NULL AND timestamp >= ?"
            " ORDER BY timestamp ASC LIMIT ?",
            (cutoff, int(batch))):
        yield sid, ts, content


def run_miner(db_path: str = "", history_db: str = "",
              evol_path: str = "",
              cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One bounded, resumable mining pass over session history + evol.jsonl.
    Writes provenance_tag='mined' records. Returns {added, skipped, cursor}.
    Fail-open: any failure degrades to fewer/no records, never raises."""
    try:
        cfg = cfg or _cfg()
        max_records = max(1, int(cfg.get("miner_max_records") or 5000))
        scan_days = float(cfg.get("miner_scan_days") or 90)
        cutoff = time.time() - scan_days * 86400.0
        batch = 2000
        added = 0
        skipped = 0
        conn = _connect(db_path)
        if conn is None:
            return {"added": 0, "skipped": 0, "cursor": None,
                    "reason": "no_plugin_db"}
        try:
            # store-cap guard: stop mining when the store is full
            if record_count(db_path) >= max_records:
                return {"added": 0, "skipped": 0, "cursor": None,
                        "reason": "store_full"}
            # ---- session history (read-only URI, timeout 2.0) ----
            hist = history_db or _history_db_path()
            if hist and os.path.exists(hist):
                try:
                    ro = sqlite3.connect("file:%s?mode=ro" % hist,
                                         uri=True, timeout=2.0)
                    try:
                        last_ts_s = _cursor_get(conn, "sessions:last_ts")
                        last_ts = float(last_ts_s) if last_ts_s else cutoff
                        max_seen = last_ts
                        for sid, ts, content in _messages_iter(ro, last_ts,
                                                               batch):
                            try:
                                ep = detect_episode(str(content))
                                if not ep:
                                    continue
                                rid = write_record(
                                    situation_text=str(content),
                                    options=ep["options"],
                                    chosen=_chosen_of(str(content)),
                                    outcome="", outcome_ts=None,
                                    source="session:%s" % (sid or "?"),
                                    provenance_tag=TAG_MINED,
                                    ts=float(ts), db_path=db_path)
                                if rid:
                                    added += 1
                                else:
                                    skipped += 1
                                max_seen = max(max_seen, float(ts))
                                if record_count(db_path) >= max_records:
                                    break
                            except Exception:  # noqa: BLE001 — per-row fail-open
                                continue
                        _cursor_set(conn, "sessions:last_ts", repr(max_seen))
                        conn.commit()
                    finally:
                        ro.close()
                except Exception:  # noqa: BLE001 — history source fail-open
                    pass
            # ---- evol.jsonl (bounded tail scan, byte-offset cursor) ----
            epath = evol_path or _evol_db_path()
            if epath and os.path.exists(epath):
                try:
                    off_s = _cursor_get(conn, "evol:offset")
                    off = int(float(off_s)) if off_s else 0
                    size = os.path.getsize(epath)
                    if off > size:
                        off = 0  # rotated/truncated ledger — restart walk
                    with open(epath, "rb") as fh:
                        fh.seek(off)
                        chunk = fh.read(262144)
                    new_off = off
                    for line in chunk.splitlines():
                        new_off += len(line) + 1
                        try:
                            rec = json.loads(line.decode("utf-8", "replace"))
                        except Exception:  # noqa: BLE001
                            continue
                        if not isinstance(rec, dict):
                            continue
                        basis = str(rec.get("basis") or "")
                        ep = detect_episode(basis)
                        if not ep:
                            continue
                        rid = write_record(
                            situation_text=basis,
                            options=ep["options"],
                            chosen=_chosen_of(basis),
                            outcome="", outcome_ts=None,
                            source="evol:%s" % str(rec.get("id") or "?")[:80],
                            provenance_tag=TAG_MINED,
                            ts=_parse_ts(rec.get("timestamp")),
                            db_path=db_path)
                        if rid:
                            added += 1
                        else:
                            skipped += 1
                        if record_count(db_path) >= max_records:
                            break
                    _cursor_set(conn, "evol:offset", repr(new_off))
                    conn.commit()
                except Exception:  # noqa: BLE001 — evol source fail-open
                    pass
            return {"added": added, "skipped": skipped,
                    "cursor": {"count": record_count(db_path)}}
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — the miner never raises
        logger.debug("decision_miner run error: %s", e)
        return {"added": 0, "skipped": 0, "cursor": None, "reason": "error"}


def _parse_ts(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        s = str(value).strip()
        if s.isdigit():
            return float(s)
        from datetime import datetime

        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:  # noqa: BLE001
        return None


def _history_db_path() -> str:
    try:
        from . import session_store

        return session_store._state_db_path()
    except Exception:  # noqa: BLE001
        return ""


def _evol_db_path() -> str:
    try:
        import hermes_constants

        return str(hermes_constants.get_hermes_home() / "evol.jsonl")
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------------------
# POST leg (spec §10.2): turn-close scan of decision-shaped ACTIONS
# ---------------------------------------------------------------------------

_ACTION_FAMILIES: Dict[str, str] = {
    "branch_choice": r"\b(went with|chose|choosing|going with|picking|"
                     r"switch(?:ed|ing)? to|instead of|approach [ab12]\b|"
                     r"option [a-d1-4]\b|took the)\b",
    "retry": r"\b(retr(?:y|ying)|try(?:ing)? again|re-?run(?:ning)?|"
             r"second attempt)\b",
    "abort": r"\b(abort(?:ed|ing)?|abandon(?:ed|ing)?|skip(?:ped|ping)?|"
             r"giv(?:e|ing) up on|rolling back|revert(?:ed|ing)?)\b",
}

_ACTION_RE = {k: re.compile(v, re.IGNORECASE) for k, v in _ACTION_FAMILIES.items()}


def detect_actions(response_text: str) -> List[Dict[str, Any]]:
    """Decision-shaped ACTIONS taken during the run: (family, action_text)
    tuples extracted from the response. Bounded to 4 per turn. Never raises."""
    out: List[Dict[str, Any]] = []
    try:
        text = str(response_text or "")
        if len(text.strip()) < 12:
            return out
        for para in re.split(r"\n+", text)[:40]:
            para = para.strip()
            if len(para) < 12:
                continue
            fams = sorted(k for k, rx in _ACTION_RE.items() if rx.search(para))
            if not fams:
                continue
            out.append({"families": fams, "action_text": para[:SITUATION_CAP]})
            if len(out) >= 4:
                break
    except Exception:  # noqa: BLE001
        return []
    return out


def post_audit_scan(session_id: str, response_text: str, model: str = "",
                    context: Optional[Dict[str, Any]] = None,
                    log_route: Optional[Any] = None,
                    cfg: Optional[Dict[str, Any]] = None) -> None:
    """POST turn-close scan (on_transform boundary, mirrors the R15 L3
    completion-audit seam): identify decision-shaped ACTIONS taken during
    the run, frame+score each via the existing async worker, park an
    advisory at the next delivery boundary when the choice contradicts
    strong precedent, and write every POST-audited decision to
    decision_records (provenance_tag='post_audit') — memory grows as a
    side effect of operation. Level-gated: decision.enabled AND
    decision.post_audit (default FALSE — dark, consistent with v0).
    Never raises."""
    try:
        cfg = cfg or _cfg()
        if not (bool(cfg.get("enabled")) and bool(cfg.get("post_audit"))):
            return  # dark default: total no-op
        actions = detect_actions(response_text)
        if not actions:
            return
        for act in actions:
            task_text = act["action_text"]
            from . import decision as _d

            frame = _d.build_frame(session_id, task_text, cfg)
            if not frame:
                write_record(
                    situation_text=task_text, options=[], chosen="",
                    outcome="", outcome_ts=None,
                    source="post:%s" % (session_id or "?")[:80],
                    provenance_tag=TAG_POST_AUDIT, ts=time.time(),
                    db_path="")
                _log(log_route, "decision_post_recorded", reason="no_frame",
                     families=",".join(act["families"]),
                     session_id=session_id, provenance="post_audit")
                continue
            if not _d._WORKER_SEM.acquire(blocking=False):
                _log(log_route, "decision_post_suppressed",
                     reason="cap_exhausted", session_id=session_id)
                continue
            t = threading.Thread(
                target=_post_worker,
                args=(frame, task_text, act, cfg,
                      dict(session_id=session_id, model=model),
                      log_route),
                daemon=True)
            t.start()
            _log(log_route, "decision_post_dispatched",
                 families=",".join(act["families"]), mode="async",
                 session_id=session_id, provenance="post_audit")
    except Exception:  # noqa: BLE001 — POST leg never breaks delivery
        logger.debug("decision post_audit_scan error", exc_info=True)


def _log(log_route: Any, event: str, **fields: Any) -> None:
    try:
        if log_route is not None:
            log_route(event, **fields)
    except Exception:  # noqa: BLE001
        pass


def _post_worker(frame: Dict[str, Any], task_text: str, act: Dict[str, Any],
                 cfg: Dict[str, Any], ids: Dict[str, str],
                 log_route: Any) -> None:
    """Async POST worker: score the action against precedent, park an
    advisory when the agent's choice contradicts strong precedent (score
    says apply_precedent at >= threshold confidence while the taken action
    family is a retry/abort — i.e. the agent is fighting the precedent),
    and record the audited decision. Mirrors decision._async_worker shape.
    Never raises."""
    try:
        from . import decision as _d

        verdict, reason = _d.score(frame, task_text, cfg)
        _d._hourly_tick(verdict is not None)
        sid = ids.get("session_id", "")
        # record the POST-audited decision (every audit — memory side effect)
        chosen = "retry_or_abort" if set(act.get("families", [])) & {"retry", "abort"} \
            else "branch_choice"
        write_record(
            situation_text=task_text, options=[], chosen=chosen,
            outcome=(str(reason) if verdict is None
                     else "advisory_contradicts_precedent"),
            outcome_ts=time.time() if verdict is not None else None,
            source="post:%s" % (sid or "?")[:80],
            provenance_tag=TAG_POST_AUDIT, ts=time.time(), db_path="")
        _log(log_route, "decision_post_recorded",
             families=",".join(act.get("families", [])),
             verdict=(verdict or {}).get("decision") if verdict else None,
             reason=str(reason), session_id=sid, provenance="post_audit")
        # deliverable: parked advisory when the choice contradicts strong
        # precedent (high-confidence apply_precedent + retry/abort action)
        if verdict is not None and verdict.get("decision") == "apply_precedent" \
                and float(verdict.get("confidence") or 0.0) >= \
                float(cfg.get("confidence_threshold") or 0.60) \
                and chosen == "retry_or_abort":
            envelope = _d.render_envelope(verdict, frame)
            if envelope:
                try:
                    from . import debug_banner

                    debug_banner.park_anchor_banner(
                        sid, envelope, task_id="")
                    _log(log_route, "decision_post_advisory_parked",
                         confidence=round(float(verdict.get("confidence")
                                                or 0.0), 3),
                         precedents=",".join(verdict.get("precedents", [])),
                         session_id=sid, provenance="post_audit")
                except Exception:  # noqa: BLE001 — banner never breaks POST
                    pass
    except Exception:  # noqa: BLE001
        logger.debug("decision post worker error", exc_info=True)
    finally:
        try:
            from . import decision as _d

            _d._WORKER_SEM.release()
        except Exception:  # noqa: BLE001
            pass
