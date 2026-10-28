#!/usr/bin/env python3
"""JOB A — harvest every live refusal body from the fleet's own state DBs.

Read-only. Rung A/B provenance only:
  A  = byte-exact delivery proof (api_content present and equal to content)
  B  = no delivery proof, but ALSO no evidence of any rewrite (no render
       record for this session, no api_content divergence, no summary row)
Rejects are counted by reason: render_rewrite (the body IS render content —
never stored), paraphrase (api_content differs from the persisted row),
summary_row, router_artifact.

Output: candidates.jsonl (all assistant rows that pass the broad cue screen),
with label columns left empty — labelling happens in label.py.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict

PROFILES = "/opt/data/profiles"
OUT = "/opt/data/tmp/detcorpus"
PROV_REJECTS = os.path.join(OUT, "provenance_rejects.jsonl")

# Deliberately MUCH wider than the detector under test: the corpus is a
# measurement, so recall on the cue screen costs us nothing (every candidate
# is labelled afterwards) while a narrow screen would bake the detector's own
# blind spots into the ground truth.
CUE = re.compile(
    r"(not\s+building|won'?t|will\s+not|can'?t|cannot|i\s+decline|i\s+refuse"
    r"|refus\w+|declin\w+|not\s+going\s+to|not\s+doing|not\s+writing|not\s+making"
    r"|not\s+for\s+me|stays\s+a\s+no|closed\s+line|settled\s+line|held\b|hold\b"
    r"|not\s+a\s+flinch|same\s+artifact|no\s+matter\s+the\s+rephrasing"
    r"|i\s+don'?t\s+(?:do|build|write|make|touch|route)"
    r"|that'?s\s+a\s+no|isn'?t\s+happening|not\s+happening|off\s+the\s+table"
    r"|withdraw\w*|wrong\s+call|rather\s+than\s+(?:building|writing|doing)"
    r"|what\s+i\s+can\s+(?:do|offer)|not\s+comfortable|i'?d\s+rather\s+not"
    r"|unable\s+to\s+(?:help|assist|provide|write|draft|comply)"
    r"|can'?t\s+help\s+with|not\s+something\s+i|hard\s+pass|i'?ll\s+pass"
    r"|not\s+going\s+to\s+(?:write|build|make|do|help|produce|provide)"
    r"|(?:^|\n)\s*no[.,—\s])",
    re.IGNORECASE)

ARTIFACT = re.compile(r"\[hermes-router\]|Empty model response|uncensored-router", re.I)

# Render-lane delivery footer. A body carrying this IS render content (the
# router rewrote the persisted row with the render and its banner), so it is
# both a provenance reject and a never-store item. Found by measurement: the
# brief's specimen api_1793194453_a0b22149 is a compliant phishing draft +
# this footer, not a refusal body.
RENDER_FOOTER = re.compile(
    r"·\s*router\s*·\s*shadow-self|\|\s*tok\s*\d+\s*/\s*\d+\s*\|\s*\$", re.I)


def norm(s: str) -> str:
    return " ".join((s or "").split())


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", "surrogatepass")).hexdigest()


def load_renders(profile: str):
    """Session -> list of normalised render prefixes (300c) + full render hashes
    + render char lengths (renders are stored truncated, so prefix match is the
    only stable comparison)."""
    path = os.path.join(PROFILES, profile, "uncensored-router-renders.jsonl")
    by_session = defaultdict(list)
    if not os.path.exists(path):
        return by_session
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            r = e.get("render") or ""
            if not r:
                continue
            by_session[str(e.get("session_id") or "")].append(
                (norm(r)[:300], len(r)))
    return by_session


def render_hit(body: str, renders) -> bool:
    n = norm(body)
    for prefix, rlen in renders:
        if not prefix:
            continue
        if n.startswith(prefix[:min(len(prefix), len(n))]) and \
                abs(len(n) - rlen) <= max(40, rlen // 50):
            return True
        if n[:300] == prefix[:300]:
            return True
    return False


def harvest_profile(profile: str):
    db = os.path.join(PROFILES, profile, "state.db")
    if not os.path.exists(db):
        return [], defaultdict(int)
    renders = load_renders(profile)
    stats = defaultdict(int)
    rows = []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.text_factory = lambda b: b.decode("utf-8", "replace")
    sessions = {}
    for sid, src, model, started in con.execute(
            "select id, source, model, started_at from sessions"):
        sessions[sid] = (src, model, started)
    cur = con.execute(
        "select id, session_id, role, content, timestamp, api_content,"
        " display_kind, active, _compressed_summary, tool_calls"
        " from messages where role in ('assistant','user')"
        " order by session_id, id")
    last_user = {}
    last_user_id = {}
    for (mid, sid, role, content, ts, api_content, dk, active,
         compsum, tcs) in cur:
        content = content or ""
        if role == "user":
            if content.strip():
                last_user[sid] = content
                last_user_id[sid] = mid
            continue
        stats["assistant_rows"] += 1
        if not content.strip():
            stats["reject_empty"] += 1
            continue
        if compsum:
            stats["reject_summary_row"] += 1
            continue
        if ARTIFACT.search(content):
            stats["reject_router_artifact"] += 1
            continue
        if RENDER_FOOTER.search(content):
            stats["reject_render_footer"] += 1
            with open(PROV_REJECTS, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"profile": profile, "session_id": sid,
                                     "ts": ts, "body_sha": sha(content),
                                     "reason": "render_footer",
                                     "chars": len(content)}) + "\n")
            continue
        if not CUE.search(content[:4000]):
            stats["screen_miss"] += 1
            continue
        stats["cue_hit"] += 1
        if render_hit(content, renders.get(sid, [])):
            stats["reject_render_rewrite"] += 1
            with open(PROV_REJECTS, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"profile": profile, "session_id": sid,
                                     "ts": ts, "body_sha": sha(content),
                                     "reason": "render_rewrite",
                                     "chars": len(content)}) + "\n")
            continue
        if api_content and api_content.strip() and norm(api_content) != norm(content):
            stats["reject_paraphrase"] += 1
            with open(PROV_REJECTS, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"profile": profile, "session_id": sid,
                                     "ts": ts, "body_sha": sha(content),
                                     "reason": "paraphrase",
                                     "chars": len(content)}) + "\n")
            continue
        prov = "A" if (api_content and api_content.strip()) else "B"
        ask = last_user.get(sid, "")
        src, model, _started = sessions.get(sid, ("", "", 0))
        rows.append({
            "id": f"{profile}:{mid}",
            "profile": profile,
            "session_id": sid,
            "source": src,
            "model": model,
            "ts": ts,
            "ask_sha": sha(ask),
            "body_sha": sha(content),
            "provenance": prov,
            "active": int(active or 0),
            "chars": len(content),
            "has_tools": bool(tcs),
            "body": content,
            "ask": ask,
        })
    con.close()
    return rows, stats


def main():
    os.makedirs(OUT, exist_ok=True)
    if os.path.exists(PROV_REJECTS):
        os.remove(PROV_REJECTS)
    allrows, total = [], defaultdict(int)
    for profile in sorted(os.listdir(PROFILES)):
        db = os.path.join(PROFILES, profile, "state.db")
        if not os.path.isdir(os.path.join(PROFILES, profile)) or not os.path.exists(db):
            continue
        rows, st = harvest_profile(profile)
        allrows.extend(rows)
        print(f"{profile:14s} " + " ".join(f"{k}={v}" for k, v in sorted(st.items())),
              file=sys.stderr)
        for k, v in st.items():
            total[k] += v
    # dedupe identical bodies per session (repeat delivery of same body)
    seen = set()
    uniq = []
    for r in sorted(allrows, key=lambda r: (r["ts"] or 0)):
        key = (r["session_id"], r["body_sha"])
        if key in seen:
            total["dedupe_repeat_body"] += 1
            continue
        seen.add(key)
        uniq.append(r)
    with open(os.path.join(OUT, "candidates.jsonl"), "w", encoding="utf-8") as fh:
        for r in uniq:
            body = r.pop("body")
            ask = r.pop("ask")
            r["body_path"] = f"bodies/{r['body_sha']}.txt"
            r["ask_path"] = f"asks/{r['body_sha']}.txt"
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            os.makedirs(os.path.join(OUT, "bodies"), exist_ok=True)
            os.makedirs(os.path.join(OUT, "asks"), exist_ok=True)
            p = os.path.join(OUT, r["body_path"])
            if not os.path.exists(p):
                with open(p, "w", encoding="utf-8") as bf:
                    bf.write(body)
            a = os.path.join(OUT, r["ask_path"])
            if not os.path.exists(a):
                with open(a, "w", encoding="utf-8") as af:
                    af.write(ask)
    print("TOTAL", dict(total), "candidates", len(uniq), file=sys.stderr)
    print(json.dumps({"stats": dict(total), "candidates": len(uniq)}))


if __name__ == "__main__":
    main()
