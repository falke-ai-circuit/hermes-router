#!/usr/bin/env python3
"""Inter-annotator agreement: the workhorse labels (corpus truth) vs the
single-item ornith labels on the SAME bodies. Reported as a label-noise
figure — if the two annotators disagree often, the corpus label is soft and
every recall number in the report inherits that softness."""
import json
import os

HOLE = "/opt/data/tmp/detcorpus"


def load(fname):
    out = {}
    p = os.path.join(HOLE, fname)
    if not os.path.exists(p):
        return out
    for line in open(p, encoding="utf-8"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("ok"):
            out[d["id"]] = bool((d.get("label") or {}).get("declines"))
    return out


def main():
    wf = load("labels_wf.jsonl")
    orn = load("labels.jsonl")
    orn.update(load("labels_batch.jsonl"))
    shared = sorted(set(wf) & set(orn))
    if not shared:
        print(json.dumps({"shared": 0}))
        return
    agree = sum(1 for i in shared if wf[i] == orn[i])
    both_true = sum(1 for i in shared if wf[i] and orn[i])
    wf_only = sum(1 for i in shared if wf[i] and not orn[i])
    orn_only = sum(1 for i in shared if orn[i] and not wf[i])
    # Cohen's kappa for two raters, binary
    n = len(shared)
    po = agree / n
    p_wf = sum(1 for i in shared if wf[i]) / n
    p_orn = sum(1 for i in shared if orn[i]) / n
    pe = p_wf * p_orn + (1 - p_wf) * (1 - p_orn)
    kappa = (po - pe) / (1 - pe) if pe < 1 else None
    out = {"shared": n, "agreement": round(po, 4),
           "kappa": round(kappa, 4) if kappa is not None else None,
           "both_true": both_true, "wf_only": wf_only, "ornith_only": orn_only,
           "wf_true_rate": round(p_wf, 4), "ornith_true_rate": round(p_orn, 4)}
    json.dump(out, open(os.path.join(HOLE, "agreement.json"), "w"), indent=2)
    print(json.dumps(out))


if __name__ == "__main__":
    main()
