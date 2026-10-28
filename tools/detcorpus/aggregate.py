#!/usr/bin/env python3
"""Aggregate scores into the numbers the report needs.

Per layer: recall on refusals, FP rate on the 200 benign controls, latency,
cost/call. Per domain and per form: recall. Plus the as-shipped chain
(T1 candidate gate -> T2), which is what actually routes a live turn.
"""
from __future__ import annotations

import collections
import json
import os
import statistics

HOLE = "/opt/data/tmp/detcorpus"

LAYERS = ["is_candidate", "has_refusal_semantics", "denial_action", "t2",
          "plain", "chain"]


def load_scores():
    rows = collections.defaultdict(dict)
    p = os.path.join(HOLE, "scores.jsonl")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            d = json.loads(line)
            rows[d["id"]][d["arm"]] = d
    return rows


def main():
    corpus = [json.loads(l) for l in open(os.path.join(HOLE, "corpus.jsonl"),
                                          encoding="utf-8")]
    det = json.load(open(os.path.join(HOLE, "det_scores.json"), encoding="utf-8"))
    sc = load_scores()

    def verdict(row, layer):
        d = det.get(row["id"]) or {}
        rid = row["id"]
        if layer == "is_candidate":
            return d.get("is_candidate")
        if layer == "has_refusal_semantics":
            return d.get("has_refusal_semantics")
        if layer == "denial_action":
            return d.get("denial_action")
        if layer == "t2":
            return (sc.get(rid, {}).get("t2") or {}).get("fires")
        if layer == "plain":
            return (sc.get(rid, {}).get("plain") or {}).get("fires")
        if layer == "chain":
            cand = d.get("is_candidate")
            if not cand:
                return False
            t2 = sc.get(rid, {}).get("t2") or {}
            if t2.get("ok") is False or t2.get("fires") is None:
                return True   # T1 fail-open route when the judge fails
            return bool(t2.get("fires"))
        return None

    refusals = [r for r in corpus if r["population"] == "refusal"]
    benign = [r for r in corpus if r["population"] == "benign"]

    summary = {"counts": {"refusals": len(refusals), "benign": len(benign),
                          "harvest_refusals": sum(1 for r in refusals
                                                  if r["profile"] != "probe"),
                          "probe_refusals": sum(1 for r in refusals
                                                if r["profile"] == "probe")},
               "layers": {}}

    for layer in LAYERS:
        caught = sum(1 for r in refusals if verdict(r, layer) is True)
        scored = [r for r in refusals if verdict(r, layer) is not None]
        fp = sum(1 for r in benign if verdict(r, layer) is True)
        fpscored = [r for r in benign if verdict(r, layer) is not None]
        row = {"recall": round(caught / len(scored), 4) if scored else None,
               "caught": caught, "scored": len(scored),
               "fp_rate": round(fp / len(fpscored), 4) if fpscored else None,
               "fp": fp, "fp_scored": len(fpscored)}
        if layer in ("t2", "plain"):
            lats, costs = [], []
            for r in refusals + benign:
                a = sc.get(r["id"], {}).get(layer)
                if a and a.get("latency_s"):
                    lats.append(a["latency_s"])
                    costs.append(a.get("cost") or 0.0)
            row["latency_p50_s"] = round(statistics.median(lats), 3) if lats else None
            row["latency_p95_s"] = (round(sorted(lats)[int(len(lats) * .95) - 1], 3)
                                    if lats else None)
            row["cost_per_call_usd"] = round(sum(costs) / len(costs), 8) if costs else None
        else:
            ms = [det[r["id"]]["latency_ms"] for r in corpus if r["id"] in det]
            row["latency_p50_ms"] = round(statistics.median(ms), 3) if ms else None
            row["cost_per_call_usd"] = 0.0
        summary["layers"][layer] = row

    # per domain (harvest) / per form (probes)
    for layer in LAYERS:
        dom = collections.defaultdict(lambda: [0, 0])
        frm = collections.defaultdict(lambda: [0, 0])
        for r in refusals:
            v = verdict(r, layer)
            if v is None:
                continue
            if r["profile"] == "probe":
                frm[r["register_guess"]][1] += 1
                frm[r["register_guess"]][0] += int(bool(v))
            else:
                dom[r["domain_guess"] or "unknown"][1] += 1
                dom[r["domain_guess"] or "unknown"][0] += int(bool(v))
        summary.setdefault("by_domain", {})[layer] = {
            k: {"caught": a, "n": b, "recall": round(a / b, 3)}
            for k, (a, b) in sorted(dom.items())}
        summary.setdefault("by_form", {})[layer] = {
            k: {"caught": a, "n": b, "recall": round(a / b, 3)}
            for k, (a, b) in sorted(frm.items())}

    # the specific live misses
    named = {}
    for r in corpus:
        if r["session_id"] in ("api_1793197680_59571be2", "api_1793192476_881df1c5"):
            named[r["session_id"]] = {layer: verdict(r, layer) for layer in LAYERS}
            named[r["session_id"]]["chars"] = r["chars"]
    summary["live_specimens"] = named

    with open(os.path.join(HOLE, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    print(json.dumps(summary, indent=2)[:4000])


if __name__ == "__main__":
    main()
