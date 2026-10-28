#!/usr/bin/env python3
"""R76-P4-S2 liveness battery: one real upward direct import per contract, run on a scratch copy."""
import json, shutil, subprocess, os, re, sys

ROOT = "/opt/data/tmp/s2rev"
PKG = os.path.join(ROOT, "hermes_router")
PRISTINE = os.path.join(ROOT, "pristine")

MUTATIONS = [
    # (id, contract name fragment, file under package, injected line, target layer)
    ("M1-gate",  "gate may not import api",        "gate/orchestration.py",        "from hermes_router.api import routes  # MUT",        "gate"),
    ("M2-passes","passes may not import api",      "passes/dispatcher_post.py",    "from hermes_router.api import router_tools  # MUT",  "passes"),
    ("M3-features","features may not import",      "features/patterns/__init__.py","from hermes_router.gate import registration  # MUT", "features"),
    ("M4-lanes", "lanes may not import",           "lanes/registry.py",            "from hermes_router.passes import dispatcher_pre  # MUT", "lanes"),
    ("M5-core",  "core may not import api",        "core/state.py",                "from hermes_router.lanes import registry  # MUT",    "core"),
]

def run_lint(cwd_dir):
    env = dict(os.environ, PYTHONPATH=ROOT)
    p = subprocess.run(["lint-imports"], cwd=cwd_dir, env=env, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr

def contract_lines(out):
    keep = {}
    for line in out.splitlines():
        m = re.match(r"^(.*?) (KEPT|BROKEN)(?: \((.*)\))?$", line.strip())
        if m:
            keep[m.group(1).strip()] = (m.group(2), m.group(3) or "")
    return keep

def reset():
    if os.path.isdir(PKG):
        shutil.rmtree(PKG)
    shutil.copytree(PRISTINE, PKG)

results = []
reset()
rc, out = run_lint(PKG)
results.append({"id": "CONTROL", "rc": rc, "contracts": contract_lines(out),
                "tail": [l for l in out.splitlines() if l.startswith("Contracts:")]})

for mid, frag, rel, inject, layer in MUTATIONS:
    reset()
    path = os.path.join(PKG, rel)
    if not os.path.exists(path):
        results.append({"id": mid, "error": f"missing {rel}"})
        continue
    with open(path) as f:
        body = f.read()
    with open(path, "w") as f:
        f.write(body + "\n" + inject + "\n")
    rc, out = run_lint(PKG)
    contracts = contract_lines(out)
    hit = {k: v for k, v in contracts.items() if frag in k}
    detail = [l.strip() for l in out.splitlines() if "->" in l or "is not allowed" in l]
    results.append({"id": mid, "file": rel, "injected": inject, "rc": rc,
                    "target": hit,
                    "all_broken": {k: v for k, v in contracts.items() if v[0] == "BROKEN"},
                    "detail": detail[:6],
                    "tail": [l for l in out.splitlines() if l.startswith("Contracts:")]})

# exemption liveness: remove each ignore entry, contract must BREAK on the live edge
EXEMPT = [("X1-gate-exempt", "hermes_router.gate.registration -> hermes_router.api.*", "gate may not import api"),
          ("X2-passes-exempt", "hermes_router.passes.dispatcher_pre -> hermes_router.api.router_tools", "passes may not import api")]
cfg_path = os.path.join(PKG, ".importlinter")
orig_cfg = open(cfg_path).read()
for xid, line, frag in EXEMPT:
    reset()
    cfg = open(os.path.join(PKG, ".importlinter")).read()
    assert line in cfg, line
    open(os.path.join(PKG, ".importlinter"), "w").write(cfg.replace("    " + line + "\n", ""))
    rc, out = run_lint(PKG)
    contracts = contract_lines(out)
    hit = {k: v for k, v in contracts.items() if frag in k}
    results.append({"id": xid, "removed": line, "rc": rc, "target": hit,
                    "detail": [l.strip() for l in out.splitlines() if "->" in l and "api" in l][:8],
                    "tail": [l for l in out.splitlines() if l.startswith("Contracts:")]})

reset()
with open(os.path.join(PKG, ".importlinter"), "w") as f:
    f.write(orig_cfg)

print(json.dumps(results, indent=2))
