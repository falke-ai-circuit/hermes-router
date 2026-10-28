import ast, os, re, json, sys

SYMS = ["parse", "render", "build_request", "extract", "summarise", "_as_prob", "_clip"]
root = sys.argv[1]
out = {s: [] for s in SYMS}
out["min_gap"] = []
out["reflex_weights_module_mentions"] = []
dyn = []
for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__", ".venv", "node_modules")]
    for fn in filenames:
        if not fn.endswith(".py"):
            continue
        p = os.path.join(dirpath, fn)
        rel = os.path.relpath(p, root)
        try:
            src = open(p, encoding="utf-8").read()
        except Exception:
            continue
        if "reflex_weights" in src:
            out["reflex_weights_module_mentions"].append(rel)
        if re.search(r"getattr\s*\(|__all__|globals\(\)|importlib", src):
            for m in re.finditer(r"getattr\s*\([^)]*\)|__all__\s*=\s*[^\n]*|importlib[^\n]*", src):
                if any(s in m.group(0) for s in SYMS) or "reflex" in m.group(0):
                    dyn.append([rel, m.group(0)[:160]])
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        defined_here = rel == "reflex_weights.py"
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "reflex_weights" in node.module:
                for a in node.names:
                    if a.name in out:
                        out[a.name].append(f"{rel}:{node.lineno}: from-import {a.name}")
            if isinstance(node, ast.Attribute) and node.attr in out:
                # only count attribute access on a reflex_weights-ish alias
                v = node.value
                name = getattr(v, "id", getattr(v, "attr", ""))
                if name in ("_rw", "rw", "reflex_weights", "reflex_weights_mod"):
                    out[node.attr].append(f"{rel}:{node.lineno}: {name}.{node.attr}")
        for s in SYMS:
            if s == "render" or s == "parse" or s == "extract":
                continue  # too generic for bare-name grep; ast Name nodes below
        # bare Name use of a deleted symbol inside this module
        if defined_here:
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id in out and not isinstance(node.ctx, ast.Store):
                    out[node.id].append(f"{rel}:{node.lineno}: bare-name(inside module)")
        for m in re.finditer(r"\bmin_gap\b", src):
            ln = src[:m.start()].count("\n") + 1
            out["min_gap"].append(f"{rel}:{ln}")
print(json.dumps({"root": root, "census": out, "dynamic": dyn}, indent=1))
