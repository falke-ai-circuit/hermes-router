#!/usr/bin/env python3
"""Reproduce NOTE variants at e846ae9: (a) both true upward edges exempted, (b) hub neutralised."""
import os, subprocess, sys, re

TREE = "/opt/data/tmp/s2rev/hist/b/hermes_router"
PARENT = "/opt/data/tmp/s2rev/hist/b"

TRUE_EDGES = [("hermes_router.gate.registration", f"hermes_router.api.{m}") for m in
              ["commands", "commands_config", "commands_diag", "commands_runtime", "config_writer", "router_tools"]]
TRUE_EDGES += [("hermes_router.passes.dispatcher_pre", "hermes_router.api.router_tools")]

sys.path.insert(0, PARENT)
os.environ["PYTHONPATH"] = PARENT
import grimp

graph = grimp.build_graph("hermes_router")
hub_edges = []
for m in graph.modules:
    if m == "hermes_router":
        for t in graph.find_modules_directly_imported_by(m):
            hub_edges.append(("hermes_router", t))
    else:
        if "hermes_router" in graph.find_modules_directly_imported_by(m):
            hub_edges.append((m, "hermes_router"))
print("hub-involved DIRECT edges:", len(hub_edges),
      "| out:", sum(1 for a, b in hub_edges if a == "hermes_router"),
      "| in:", sum(1 for a, b in hub_edges if b == "hermes_router"))

HEAD = """[importlinter]
root_packages =
    hermes_router
include_external_packages = True

[importlinter:contract:ladder]
name = layers ladder
type = layers
layers =
    hermes_router.api
    hermes_router.gate
    hermes_router.passes
    hermes_router.features
    hermes_router.lanes
    hermes_router.core
"""

def run(label, ignores):
    cfg = HEAD + ("ignore_imports =\n" + "".join(f"    {a} -> {b}\n" for a, b in ignores) if ignores else "")
    open(os.path.join(TREE, ".importlinter"), "w").write(cfg)
    p = subprocess.run(["lint-imports"], cwd=TREE, env=dict(os.environ, PYTHONPATH=PARENT),
                       capture_output=True, text=True)
    out = p.stdout + p.stderr
    heads = [l for l in out.splitlines() if "is not allowed to import" in l]
    routes = [l for l in out.splitlines() if l.strip().startswith("- ")]
    summary = [l for l in out.splitlines() if l.startswith("Contracts:")]
    print(f"{label}: {summary} | pair-headers={len(heads)} routes={len(routes)}")
    open(f"/opt/data/tmp/s2rev/{label}.txt", "w").write(out)
    return len(heads), len(routes)

run("base", [])
run("both_exempt", TRUE_EDGES)
run("hub_neutralised", TRUE_EDGES + hub_edges)
