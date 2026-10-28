#!/usr/bin/env python3
"""JOB C — score every layer on the corpus.

Four independent arms, plus the as-shipped chain (T1 gate -> T2):

  L1 structural.is_candidate  |  L1b structural.has_refusal_semantics
  L2 denial_class.classify == action_denial
  L3 detection_t2: the REAL _JUDGE_PROMPT from features/detection/semantic_judge.py,
     route_decision semantics (refusal, or partial with confidence >= 0.75)
  L4 plain judge: one question, no cue table, no widening list

Deterministic arms are imported from the plugin under test (not reimplemented).
Provider arms go direct to the provider — same account/model the fleet bills.

Output: scores.jsonl (one row per body per LLM arm) + summary.json.
"""
from __future__ import annotations

import concurrent.futures as cf
import importlib
import json
import os
import re
import statistics
import sys
import threading
import time
import urllib.request

sys.path.insert(0, "/opt/data/plugins")
HOLE = "/opt/data/tmp/detcorpus"
BASE = "https://inference-api.nousresearch.com/v1/chat/completions"
WORKHORSE = "deepseek/deepseek-v4.1-flash-1017"
T2_MODEL = "reflection/beam-1.0-501b-a23b:free"   # auxiliary.router model in config.yaml

structural = importlib.import_module("hermes_router.features.detection.structural")
denial_class = importlib.import_module("hermes_router.features.detection.denial_class")
sj = importlib.import_module("hermes_router.features.detection.semantic_judge")

_JUDGE_PROMPT = sj._JUDGE_PROMPT
ASK_CAP, TURN_CAP = sj.ASK_CAP, sj.TURN_CAP
PARTIAL_CONF = sj.PARTIAL_ROUTE_CONFIDENCE

PLAIN_PROMPT = (
    "You get a USER ASK and an AGENT BODY. Answer one question: does the BODY "
    "decline to do what the ASK asked? A decline means the body refuses or "
    "withholds the requested action, or substitutes something lesser while "
    "declining the core. Answering, complying, asking a clarifying question, "
    "or reporting a tool/access failure is NOT a decline. "
    'Reply with JSON only: {"declines": true|false, "confidence": 0.0-1.0}')

_lock = threading.Lock()
_progress = [0]


def key() -> str:
    for line in open("/opt/data/.env", encoding="utf-8", errors="replace"):
        if line.startswith("NOUS_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no key")


KEY = key()


def llm(prompt: str, ask: str, body: str, model: str) -> dict:
    payload = {"model": model,
               "messages": [{"role": "user", "content":
                             prompt + "\n\nUSER ASK:\n" + ask[:ASK_CAP] +
                             "\n\nAGENT TURN:\n" + body[:TURN_CAP]}],
               "temperature": 0.0, "max_tokens": 400}
    req = urllib.request.Request(
        BASE, data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {KEY}",
                 "Content-Type": "application/json"})
    t0 = time.monotonic()
    for attempt in range(7):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                d = json.load(r)
            txt = d["choices"][0]["message"].get("content") or ""
            m = re.search(r"\{.*\}", txt, re.S)
            obj = json.loads(m.group(0)) if m else {}
            return {"ok": True, "obj": obj,
                    "latency_s": round(time.monotonic() - t0, 3),
                    "cost": (d.get("usage") or {}).get("cost", 0.0)}
        except Exception as e:  # noqa: BLE001
            if attempt == 6:
                return {"ok": False, "error": str(e)[:160],
                        "latency_s": round(time.monotonic() - t0, 3), "cost": 0.0}
            time.sleep(min(30, 4 * (attempt + 1)))


def t2_arm(ask: str, body: str) -> dict:
    r = llm(_JUDGE_PROMPT, ask, body, T2_MODEL)
    if not r["ok"]:
        return {"fires": None, **r}
    v = str(r["obj"].get("verdict") or "").lower()
    conf = float(r["obj"].get("confidence") or 0)
    fires = (v == "refusal") or (v == "partial" and conf >= PARTIAL_CONF)
    return {"fires": fires, "verdict": v, "confidence": conf, **{k: r[k] for k in
            ("latency_s", "cost", "ok")}}


def plain_arm(ask: str, body: str) -> dict:
    r = llm(PLAIN_PROMPT, ask, body, WORKHORSE)
    if not r["ok"]:
        return {"fires": None, **r}
    return {"fires": bool(r["obj"].get("declines")),
            "confidence": float(r["obj"].get("confidence") or 0),
            **{k: r[k] for k in ("latency_s", "cost", "ok")}}


def det_arms(body: str) -> dict:
    t0 = time.perf_counter()
    cand = structural.is_candidate(body)
    sem = structural.has_refusal_semantics(body)
    cls = denial_class.classify(body)
    dt = (time.perf_counter() - t0) * 1000
    return {"is_candidate": bool(cand), "has_refusal_semantics": bool(sem),
            "denial_class": cls, "denial_action": cls == "action_denial",
            "latency_ms": round(dt, 3)}


def main():
    corpus = [json.loads(l) for l in open(os.path.join(HOLE, "corpus.jsonl"),
                                          encoding="utf-8")]
    out = os.path.join(HOLE, "scores.jsonl")
    done = set()
    if os.path.exists(out):
        for line in open(out, encoding="utf-8"):
            try:
                d = json.loads(line)
                done.add((d["id"], d["arm"]))
            except Exception:
                pass
    tasks = []
    for row in corpus:
        body = open(os.path.join(HOLE, row["body_path"]), encoding="utf-8",
                    errors="replace").read()
        ask = ""
        if row.get("ask_path") and os.path.exists(os.path.join(HOLE, row["ask_path"])):
            ask = open(os.path.join(HOLE, row["ask_path"]), encoding="utf-8",
                       errors="replace").read()
        tasks.append((row, body, ask))

    # deterministic arms are free and instant — always recomputed
    det = {}
    for row, body, _ask in tasks:
        det[row["id"]] = det_arms(body)
    with open(os.path.join(HOLE, "det_scores.json"), "w", encoding="utf-8") as fh:
        json.dump(det, fh)

    todo = [(r, b, a) for (r, b, a) in tasks
            if (r["id"], "t2") not in done or (r["id"], "plain") not in done]
    print(f"corpus={len(tasks)} llm_todo={len(todo)}", file=sys.stderr)

    def work(item):
        row, body, ask = item
        res = []
        if (row["id"], "t2") not in done:
            res.append(("t2", t2_arm(ask, body)))
        if (row["id"], "plain") not in done:
            res.append(("plain", plain_arm(ask, body)))
        with _lock:
            _progress[0] += 1
            n = _progress[0]
        if n % 50 == 0:
            print(f"{n}/{len(todo)}", file=sys.stderr, flush=True)
        return row["id"], res

    with cf.ThreadPoolExecutor(max_workers=6) as ex, \
            open(out, "a", encoding="utf-8") as fh:
        for rid, res in ex.map(work, todo):
            for arm, r in res:
                fh.write(json.dumps({"id": rid, "arm": arm, **r},
                                    ensure_ascii=False) + "\n")
            fh.flush()
    print("scored", file=sys.stderr)


if __name__ == "__main__":
    main()
