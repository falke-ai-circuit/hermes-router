#!/usr/bin/env python3
"""lane_ab.py — R34 item 8f: two-arm lane A/B harness.

Answers Goran's question directly: do the PRE anchor lane and the midturn
decision lane produce a BETTER turn than running without them?

The harness NEVER edits config. It READS the profile's live router config to
label the arm, runs one prompt through that profile, and reports:

    delivered chars | turns | input tokens | output tokens | wall time |
    deliverable artifact (git commits / files touched)

Usage (conductor flips config BETWEEN the two runs):

    # arm A — lanes off (complexity.level=0, decision.midturn=off)
    python scripts/lane_ab.py --arm off --prompt-file p.txt \
        --hermes-home /opt/data/profiles/coder --workdir /tmp/ab \
        --out /tmp/ab_off.json

    # arm B — lanes live
    python scripts/lane_ab.py --arm on --prompt-file p.txt \
        --hermes-home /opt/data/profiles/coder --workdir /tmp/ab \
        --out /tmp/ab_on.json

    # comparison line
    python scripts/lane_ab.py --compare /tmp/ab_off.json /tmp/ab_on.json

PASS/FAIL rule (Goran): lanes-on WINS if it yields >= the artifact of
lanes-off with fewer or equal turns.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, Optional


def _load_yaml(path: str) -> Dict[str, Any]:
    try:
        import yaml

        with open(path, "r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def read_lane_state(hermes_home: str) -> Dict[str, Any]:
    """READ the live router config for this profile (no writes)."""
    cfg = _load_yaml(os.path.join(hermes_home, "config.yaml"))
    section = {}
    for key in ("hermes_router", "uncensored_router"):
        sec = cfg.get(key)
        if isinstance(sec, dict) and sec:
            section = sec
            break
    complexity = section.get("complexity") if isinstance(
        section.get("complexity"), dict) else {}
    decision = section.get("decision") if isinstance(
        section.get("decision"), dict) else {}
    return {
        "complexity_level": complexity.get("level"),
        "midturn": decision.get("midturn"),
        "frame": decision.get("frame"),
    }


def _git_artifacts(workdir: str) -> Dict[str, int]:
    """Commits + dirty-file count in the working tree ('' on non-repos)."""
    def _run(args):
        try:
            out = subprocess.run(args, cwd=workdir, capture_output=True,
                                 text=True, timeout=30)
            return out.stdout if out.returncode == 0 else ""
        except Exception:
            return ""

    revs = _run(["git", "rev-list", "--count", "HEAD"]).strip()
    status = _run(["git", "status", "--porcelain"])
    return {
        "commits": int(revs) if revs.isdigit() else 0,
        "files_touched": len([ln for ln in status.splitlines() if ln.strip()]),
    }


def _turns_from_usage(path: str) -> Optional[int]:
    """Best-effort turn count from the `--usage-file` JSON. None when the
    shape is unknown (the caller then reports 1 completed turn)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return max(1, len(data))
        if isinstance(data, dict):
            for key in ("turns", "entries", "records", "events"):
                v = data.get(key)
                if isinstance(v, list):
                    return max(1, len(v))
            calls = data.get("llm_calls") or data.get("calls")
            if isinstance(calls, int):
                return max(1, calls)
        return 1
    except Exception:
        return None


def _tokens_from_usage(path: str) -> Dict[str, int]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        tin = tout = 0
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict) and isinstance(data.get("entries"), list):
            items = data["entries"]
        else:
            items = [data] if isinstance(data, dict) else []
        for rec in items or []:
            if not isinstance(rec, dict):
                continue
            tin += int(rec.get("input_tokens") or rec.get("prompt_tokens")
                       or rec.get("tokens_in") or 0)
            tout += int(rec.get("output_tokens") or rec.get("completion_tokens")
                        or rec.get("tokens_out") or 0)
        return {"input_tokens": tin, "output_tokens": tout}
    except Exception:
        return {"input_tokens": 0, "output_tokens": 0}


def run_arm(arm: str, prompt: str, hermes_home: str, workdir: str,
            hermes: str, timeout: int) -> Dict[str, Any]:
    os.makedirs(workdir, exist_ok=True)
    usage = os.path.join(workdir, ".lane_ab_usage.json")
    env = dict(os.environ)
    env["HERMES_HOME"] = hermes_home
    before = _git_artifacts(workdir)
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [hermes, "-z", prompt, "--usage-file", usage],
            cwd=workdir, env=env, capture_output=True, text=True,
            timeout=timeout)
        body = proc.stdout or ""
        rc = proc.returncode
    except subprocess.TimeoutExpired as exc:
        body = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        rc = -1
    wall = time.monotonic() - t0
    after = _git_artifacts(workdir)
    # Conductor addition: persist the delivered body. Artifact-per-turn is the
    # metric, and a body that talks instead of building must be readable.
    try:
        with open(os.path.join(workdir, ".lane_ab_body.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write(body or "")
    except Exception:
        pass
    # Artifact count must work in a plain directory, not only a git repo.
    fs_files = 0
    try:
        for _root, _dirs, _files in os.walk(workdir):
            for _f in _files:
                if not _f.startswith(".lane_ab"):
                    fs_files += 1
    except Exception:
        pass
    turns = _turns_from_usage(usage) or 1
    tokens = _tokens_from_usage(usage)
    return {
        "arm": arm,
        "lanes": read_lane_state(hermes_home),
        "delivered_chars": len(body.strip()),
        "turns": turns,
        "input_tokens": tokens["input_tokens"],
        "output_tokens": tokens["output_tokens"],
        "wall_s": round(wall, 1),
        "commits": max(0, after["commits"] - before["commits"]),
        "files_touched": after["files_touched"],
        "fs_files": fs_files,
        "returncode": rc,
        "prompt_chars": len(prompt),
    }


def compare(off: Dict[str, Any], on: Dict[str, Any]) -> str:
    """Goran's rule: lanes-on wins if artifact_on >= artifact_off with
    turns_on <= turns_off."""
    a_off = (int(off.get("commits") or 0) or int(off.get("files_touched") or 0)
             or int(off.get("fs_files") or 0))
    a_on = (int(on.get("commits") or 0) or int(on.get("files_touched") or 0)
            or int(on.get("fs_files") or 0))
    t_off = int(off.get("turns") or 1)
    t_on = int(on.get("turns") or 1)
    win = (a_on >= a_off) and (t_on <= t_off)
    return ("RESULT: %s | lanes-on artifact=%d turns=%d | "
            "lanes-off artifact=%d turns=%d | on_wall=%ss off_wall=%ss"
            % ("PASS" if win else "FAIL", a_on, t_on, a_off, t_off,
               on.get("wall_s"), off.get("wall_s")))


def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="lane A/B harness (read-only)")
    ap.add_argument("--arm", choices=("on", "off"), help="arm label")
    ap.add_argument("--prompt")
    ap.add_argument("--prompt-file")
    ap.add_argument("--hermes-home", default=os.environ.get("HERMES_HOME", ""))
    ap.add_argument("--workdir", default=os.getcwd())
    ap.add_argument("--hermes", default="hermes")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--out", help="write this arm's JSON here")
    ap.add_argument("--compare", nargs=2, metavar=("OFF_JSON", "ON_JSON"))
    args = ap.parse_args(argv)

    if args.compare:
        with open(args.compare[0], "r", encoding="utf-8") as fh:
            off = json.load(fh)
        with open(args.compare[1], "r", encoding="utf-8") as fh:
            on = json.load(fh)
        print(compare(off, on))
        return 0

    prompt = args.prompt
    if not prompt and args.prompt_file:
        with open(args.prompt_file, "r", encoding="utf-8") as fh:
            prompt = fh.read()
    if not prompt or not args.hermes_home:
        ap.error("--prompt/--prompt-file and --hermes-home are required")

    result = run_arm(args.arm or "on", prompt, args.hermes_home,
                     args.workdir, args.hermes, args.timeout)
    line = ("arm=%s lanes=%s delivered_chars=%d turns=%d "
            "tok %d/%d wall=%ss artifact commits=%d files=%d"
            % (result["arm"], result["lanes"], result["delivered_chars"],
               result["turns"], result["input_tokens"],
               result["output_tokens"], result["wall_s"],
               result["commits"], result["files_touched"]))
    print(line)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
