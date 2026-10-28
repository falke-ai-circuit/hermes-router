#!/usr/bin/env python3
"""Census of DIRECT upward imports across the package, by AST (all imports, module-level and function-local)."""
import ast, os, json, collections

PKG_DIR = "/opt/data/plugins/hermes_router"
PKG = "hermes_router"
# ladder: lower rank = higher layer (strictly above)
LAYERS = ["api", "gate", "passes", "features", "lanes", "core"]
RANK = {name: i for i, name in enumerate(LAYERS)}   # api=0 (top) ... core=5 (bottom)

def layer_of(modpath_parts):
    if len(modpath_parts) >= 2 and modpath_parts[0] == PKG and modpath_parts[1] in RANK:
        return modpath_parts[1]
    return None

def modname(path):
    rel = os.path.relpath(path, PKG_DIR)
    parts = rel[:-3].split(os.sep)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join([PKG] + parts)

def resolve(node, mod):
    """Yield (target_module, level_desc) for one ImportFrom node inside module `mod`."""
    if node.level:  # relative
        base = mod.split(".")
        if mod.endswith("__init__"):
            pass
        # package of mod
        is_pkg = os.path.isdir(os.path.join(PKG_DIR, *mod.split(".")[1:]))
        pkg_parts = base if is_pkg else base[:-1]
        up = node.level - 1
        anchor = pkg_parts[:len(pkg_parts) - up] if up else pkg_parts
        tgt_base = anchor + (node.module.split(".") if node.module else [])
    else:
        tgt_base = node.module.split(".") if node.module else []
    if not tgt_base:
        return
    yield ".".join(tgt_base)

edges = []
for dirpath, dirnames, filenames in os.walk(PKG_DIR):
    dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".git", "tests", "docs")]
    for fn in filenames:
        if not fn.endswith(".py"):
            continue
        path = os.path.join(dirpath, fn)
        if os.sep + "tests" + os.sep in path or os.sep + "scripts" + os.sep in path:
            continue
        mod = modname(path)
        src_layer = layer_of(mod.split("."))
        try:
            tree = ast.parse(open(path).read())
        except SyntaxError as e:
            print("PARSE-ERR", path, e); continue
        # build parent map to know if an import is function-local
        parents = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.ImportFrom):
                targets = list(resolve(node, mod))
            elif isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            for tgt in targets:
                if not tgt.startswith(PKG):
                    continue
                if tgt == PKG:
                    edges.append({"src": mod, "src_layer": src_layer, "tgt": tgt,
                                  "tgt_layer": "ROOT", "line": node.lineno,
                                  "scope": "module" if isinstance(parents.get(node), ast.Module) else "local",
                                  "raw": ast.unparse(node)})
                    continue
                tgt_layer = layer_of(tgt.split("."))
                if tgt_layer is None:
                    continue
                edges.append({"src": mod, "src_layer": src_layer, "tgt": tgt,
                              "tgt_layer": tgt_layer, "line": node.lineno,
                              "scope": "module" if isinstance(parents.get(node), ast.Module) else "local",
                              "raw": ast.unparse(node)})

upward = [e for e in edges if e["src_layer"] and e["tgt_layer"] not in (None, "ROOT")
          and RANK[e["tgt_layer"]] < RANK[e["src_layer"]]]

print("total hermes_router-internal import statements:", len(edges))
print("upward LAYER edges:", len(upward))
for e in sorted(upward, key=lambda x: (x["src"], x["line"])):
    print(f"  {e['src']}:{e['line']} [{e['scope']}] {e['src_layer']} -> {e['tgt_layer']}  ({e['raw']})")

root_edges = [e for e in edges if e["tgt_layer"] == "ROOT" and e["src_layer"] and e["src_layer"] != "api"]
print("\nROOT(hub) import edges from layered modules:", len(root_edges),
      "in", len({e['src'] for e in root_edges}), "modules")
print("by layer:", collections.Counter(e["src_layer"] for e in root_edges))
json.dump({"upward": upward, "root_edges": root_edges, "n_import_stmts": len(edges)},
          open("/opt/data/tmp/s2rev/ast_census.json", "w"), indent=1)
