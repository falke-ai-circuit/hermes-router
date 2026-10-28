#!/usr/bin/env python3
"""Assemble corpus.jsonl from harvested labels + probe rows.

Two populations:
  refusal  — annotator said the body declines the ask (the thing every layer
             is supposed to catch)
  benign   — annotator said it does not decline; a fixed random sample of 200
             from the same sessions, used only for the false-positive rate
"""
from __future__ import annotations

import json
import os
import random

HOLE = "/opt/data/tmp/detcorpus"
random.seed(20261028)


def main():
    cands = {json.loads(l)["id"]: json.loads(l)
             for l in open(os.path.join(HOLE, "candidates.jsonl"), encoding="utf-8")}
    # Precedence: the single-annotator full-population pass wins; the earlier
    # ornith passes fill only ids it has not reached yet (they are also kept
    # on disk as the independent agreement check — see agreement.py).
    labels = {}
    for fname in ("labels_wf.jsonl", "labels.jsonl", "labels_batch.jsonl"):
        p = os.path.join(HOLE, fname)
        if not os.path.exists(p):
            continue
        for l in open(p, encoding="utf-8"):
            try:
                d = json.loads(l)
            except Exception:
                continue
            if d.get("ok"):
                labels.setdefault(d["id"], d)
    refusals, benign_pool = [], []
    for cid, lab in labels.items():
        c = cands.get(cid)
        if not c:
            continue
        L = lab["label"]
        row = {
            "id": cid, "profile": c["profile"], "session_id": c["session_id"],
            "source": c["source"], "ts": c["ts"], "model": c["model"],
            "ask_sha": c["ask_sha"], "body_sha": c["body_sha"],
            "ask_path": c["ask_path"], "body_path": c["body_path"],
            "provenance": c["provenance"], "chars": c["chars"],
            "has_tools": c["has_tools"], "active": c["active"],
            "domain_guess": L.get("domain"), "register_guess": L.get("register"),
            "label_kind": L.get("kind"), "evidence_verified": lab.get("evidence_verified"),
        }
        if L.get("declines"):
            row["population"] = "refusal"
            row["label_source"] = "annotator:ornith-1.5-122b"
            refusals.append(row)
        else:
            benign_pool.append(row)

    probes = []
    pfile = os.path.join(HOLE, "probes.jsonl")
    if os.path.exists(pfile):
        for l in open(pfile, encoding="utf-8"):
            p = json.loads(l)
            if p.get("declines") and p.get("store_body"):
                probes.append({
                    "id": p["id"], "profile": "probe", "session_id": "",
                    "source": "direct_provider", "ts": None, "model":
                    "deepseek/deepseek-v4.1-flash-1017",
                    "ask_sha": p["ask_sha"], "body_sha": p["body_sha"],
                    "ask_path": p["ask_path"], "body_path": p["body_path"],
                    "provenance": p["probe"], "chars": p["chars"],
                    "has_tools": False, "active": 1,
                    "domain_guess": p["domain"], "register_guess": p["form"],
                    "label_kind": p.get("annot_kind"), "evidence_verified": True,
                    "population": "refusal",
                    "label_source": "annotator:ornith-1.5-122b",
                })

    benign = random.sample(benign_pool, min(200, len(benign_pool)))
    for b in benign:
        b["population"] = "benign"

    rows = refusals + probes + benign
    with open(os.path.join(HOLE, "corpus.jsonl"), "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(json.dumps({"harvest_refusals": len(refusals),
                      "probe_refusals": len(probes),
                      "benign_controls": len(benign),
                      "total": len(rows),
                      "labelled_candidates": len(labels),
                      "benign_pool": len(benign_pool)}))


if __name__ == "__main__":
    main()
