#!/usr/bin/env python3
"""P7 — extract the historical regex families / pattern vocabularies into
pack JSON (VERBATIM, via AST source segments) and splice the modules to
the pack loader. One-shot migration tool (run once at P7).

Extracts:
  decision.py   — module-level regex family assigns (_*_RE, _FAMILIES,
                  _FAMILIES_RE derived, _NAMED_ENUM_WORD) -> packs/decision-families.json
  classifier.py — module-level str-list vocabularies -> packs/classifier-groups.json
  lanes/builtins.py — DECLARED_USER phrase dicts -> packs/lane-phrases.json
"""
from __future__ import annotations

import ast
import json
import os

ROOT = "/opt/data/plugins/hermes_router"
PACKS = os.path.join(ROOT, "features", "patterns", "packs")

_DECISION_TARGETS = {
    "_EMOTION_WORD_RE", "_ENUM_ITEM_RE", "_NAMED_ENUM_WORD", "_NAMED_ENUM_RE",
    "_NAMED_ENUM_LOOSE_RE", "_FAMILIES", "_FAMILIES_RE", "_RISK_HIGH_RE",
    "_FORK_KEEP_DIE_RE", "_FORK_ESCALATE_RE", "_FORK_DEPLOY_RE", "_FORK_DOC_RE",
    "_OPT_LINE_RE", "_OPT_OR_RE", "_INLINE_OPTION_LABEL_RE",
    "_INTERROGATIVE_BODY_RE", "_EXPLICIT_PAREN_FORK_RE", "_ASK_SHAPED_RE",
    "_BANNER_VOCAB_RE", "_CAUSE_SEP_RE", "_COST_RE", "_RISK_IRREV_RE",
    "_RISK_REV_RE", "_RISK_BLAST_RE", "_ACTUAL_OPT_RE", "_POST_NONDECISION_RE",
}
_BUILTINS_TARGETS = {"DECLARED_USER_PHRASES", "DECLARED_USER_VARIANTS"}


def _flag_names(node, src):
    out = []
    for kw in node.keywords:
        if kw.arg == "flags":
            out.append(ast.get_source_segment(src, kw.value).split(".", 1)[-1])
    return out


def _lit_or_name(seg):
    """String-literal VALUE when the segment is one; raw source (a Name
    like LANE_SHADOW) otherwise — resolved by the consuming module."""
    try:
        return ast.literal_eval(seg)
    except Exception:
        return ast.literal_eval("(" + seg + ")") if '"' in seg or seg.count("'") >= 2 else seg


def _lit(seg):
    """The VALUE of a string-literal source segment (implicit multi-line
    concatenation included) — the verbatim regex text."""
    try:
        return ast.literal_eval(seg)
    except Exception:
        return ast.literal_eval("(" + seg + ")")


def _compile_call(node, src):
    """Return (regex_src, flags, fmt_params) for re.compile(...) nodes."""
    args = node.args
    src_node = args[0]
    fmt = []
    flags = []
    if len(args) > 1:  # positional flags: re.compile(src, re.IGNORECASE)
        fs = ast.get_source_segment(src, args[1])
        if fs:
            flags = [f.strip().split(".")[-1].replace(" | ", "|")
                     for f in fs.split("|") if f.strip()]
    if isinstance(src_node, ast.BinOp) and isinstance(src_node.op, ast.Mod):
        tmpl = (src_node.left.value if isinstance(src_node.left, ast.Constant)
                else ast.get_source_segment(src, src_node.left))
        rh = src_node.right
        names = rh.elts if isinstance(rh, ast.Tuple) else [rh]
        for n in names:
            fmt.append(ast.get_source_segment(src, n).split(".", 1)[-1])
        try:
            tmpl = _lit(tmpl)
        except Exception:
            pass
        return tmpl, (flags or _flag_names(node, src)), fmt
    if isinstance(src_node, ast.Constant):
        seg = src_node.value
    else:
        try:
            seg = _lit(ast.get_source_segment(src, src_node))
        except Exception:
            seg = ast.get_source_segment(src, src_node)
    return seg, (flags or _flag_names(node, src)), fmt


def extract(path, targets):
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    entries, spans = [], []
    for n in tree.body:
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            # annotated module constant (_FAMILIES: Dict[str, str] = {...})
            name, v, lineno, end = n.target.id, n.value, n.lineno, n.end_lineno
        elif isinstance(n, ast.Assign) and len(n.targets) == 1 and \
                isinstance(n.targets[0], ast.Name):
            name, v, lineno, end = n.targets[0].id, n.value, n.lineno, n.end_lineno
        else:
            continue
        if name not in targets:
            continue
        v = n.value
        if isinstance(v, ast.Call) and getattr(v.func, "attr", "") == "compile":
            rx, flags, fmt = _compile_call(v, src)
            flags = [f if not f.startswith("re.") else f.split(".", 1)[-1]
                     for f in flags]
            entry = {"name": name, "kind": "pattern", "regex": rx,
                     "flags": flags}
            if fmt:
                entry["fmt"] = fmt
        elif isinstance(v, ast.Constant):
            entry = {"name": name, "kind": "literal", "regex": v.value}
            pass
        elif isinstance(v, (ast.List, ast.Tuple)):
            strs = []
            for e in v.elts:
                strs.append(e.value if isinstance(e, ast.Constant) else
                            _lit_or_name(ast.get_source_segment(src, e)))
            entry = {"name": name, "kind": "strlist", "regexes": strs,
                     "flags": ["IGNORECASE"]}
        elif isinstance(v, ast.Dict):
            entry = {"name": name, "kind": "family_dict" if name == "_FAMILIES"
                     else "strdict",
                     "families": {(k.value if isinstance(k, ast.Constant)
                                   else ast.get_source_segment(src, k)):
                                  (val.value if isinstance(val, ast.Constant)
                                   else _lit_or_name(
                                       ast.get_source_segment(src, val)))
                                  for k, val in zip(v.keys, v.values)}}
        elif isinstance(v, ast.DictComp):
            # derived (_FAMILIES_RE) — rebuilt by the loader; drop the
            # original comprehension so the loader's derivation is the
            # only path (old code deletes in the same commit, parity
            # proven by the gold-vs-pack regex text check).
            spans.append((lineno - 1, end, name))
            continue  # not a pack entry
        else:
            raise SystemExit("unhandled value for %s: %r" % (name, v))
        entries.append(entry)
        spans.append((lineno - 1, end, name))
    return entries, spans, src


def splice(src, spans, loader_line, loader_anchor_first=True):
    lines = src.split("\n")
    drop = set()
    for (s, e, name) in spans:
        for i in range(s, e):
            drop.add(i)
    out = []
    inserted = False
    for i, ln in enumerate(lines):
        if i in drop:
            if not inserted:
                out.append(loader_line)
                inserted = True
            continue
        out.append(ln)
    return "\n".join(out)


def main():
    os.makedirs(PACKS, exist_ok=True)

    # --- decision.py -------------------------------------------------------
    path = os.path.join(ROOT, "decision.py")
    entries, spans, src = extract(path, _DECISION_TARGETS)
    names = [e["name"] for e in entries]
    assert len(names) == 25 and "_FAMILIES" in names, (names, len(names))
    # _NAMED_ENUM_WORD must be a literal binding BEFORE the fmt entries use it
    assert names.index("_NAMED_ENUM_WORD") < names.index("_NAMED_ENUM_RE")
    assert names.index("_NAMED_ENUM_WORD") < names.index("_NAMED_ENUM_LOOSE_RE")
    derived = ["_FAMILIES_RE"]
    pack = {"pack_id": "decision-families", "kind": "re-families",
            "entries": entries, "derived": derived,
            "literals": {e["name"]: e["regex"] for e in entries
                         if e.get("kind") == "literal"}}
    json.dump(pack, open(os.path.join(PACKS, "decision-families.json"), "w"),
              indent=1)
    loader = ("from .features.patterns.engine import bind_re_families as "
              "_p7_bind\n\n_p7_bind(globals(), \"decision-families\")")
    new_src = splice(src, spans, loader)
    open(path, "w", encoding="utf-8").write(new_src)
    print("decision.py: %d families -> decision-families.json" % len(entries))

    # --- classifier.py -----------------------------------------------------
    path = os.path.join(ROOT, "classifier.py")
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    spans, entries = [], []
    for n in tree.body:
        if not (isinstance(n, ast.Assign) and len(n.targets) == 1):
            continue
        name = getattr(n.targets[0], "id", None)
        v = n.value
        def _elem_val(e):
            if isinstance(e, ast.Constant):
                return e.value
            if isinstance(e, ast.JoinedStr):  # rf"..." — braces are literal
                return "".join(c.value for c in e.values
                               if isinstance(c, ast.Constant))
            raise SystemExit("unhandled vocab element: %r" % e)
        if isinstance(v, (ast.List, ast.Tuple)) and v.elts and all(
                isinstance(e, (ast.Constant, ast.JoinedStr)) for e in v.elts):
            strs = [_elem_val(e) for e in v.elts]
            entries.append({"name": name, "kind": "strlist", "regexes": strs,
                            "flags": ["IGNORECASE"]})
            spans.append((n.lineno - 1, n.end_lineno, name))
    pack = {"pack_id": "classifier-groups", "kind": "strlists",
            "entries": entries}
    json.dump(pack, open(os.path.join(PACKS, "classifier-groups.json"), "w"),
              indent=1)
    loader = ("from .features.patterns.engine import bind_re_families as "
              "_p7_bind\n\n_p7_bind(globals(), \"classifier-groups\")")
    new_src = splice(src, spans, loader)
    open(path, "w", encoding="utf-8").write(new_src)
    print("classifier.py: %d vocab lists -> classifier-groups.json"
          % len(entries))

    # --- lanes/builtins.py (data-file read — lanes imports nothing) --------
    path = os.path.join(ROOT, "lanes", "builtins.py")
    entries, spans, src = extract(path, _BUILTINS_TARGETS)
    # resolve Name values (LANE_SHADOW etc.) against builtins' own constants
    bt = ast.parse(open(path, encoding="utf-8").read())
    consts = {}
    for n in bt.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and \
                isinstance(n.targets[0], ast.Name) and \
                isinstance(n.value, ast.Constant):
            consts[n.targets[0].id] = n.value.value
    resolved = []
    for e in entries:
        fam = {k: consts.get(v, v) for k, v in e["families"].items()}
        assert all(isinstance(v, str) for v in fam.values()), fam
        resolved.append({"name": e["name"], "kind": "strdict",
                         "families": fam})
    pack = {"pack_id": "lane-phrases", "kind": "strdicts",
            "entries": resolved}
    json.dump(pack, open(os.path.join(PACKS, "lane-phrases.json"), "w"),
              indent=1)
    loader = (
        "# P7 §2.6: the phrase tables are pack DATA now (features/patterns/\n"
        "# packs/lane-phrases.json) — read as a data file (lanes/ imports\n"
        "# nothing); the dicts keep their names, consumers unchanged.\n"
        "import json as _json\nimport os as _os\n\n"
        "_PACK_PATH = _os.path.join(_os.path.dirname(_os.path.dirname(\n"
        "    _os.path.abspath(__file__))), 'features', 'patterns', 'packs',\n"
        "    'lane-phrases.json')\n"
        "_LANE_PACK = _json.load(open(_PACK_PATH, encoding='utf-8'))\n"
        "def _pack_dict(name):\n"
        "    for _e in _LANE_PACK['entries']:\n"
        "        if _e['name'] == name:\n"
        "            return dict(_e['families'])\n"
        "    raise KeyError(name)\n"
        "DECLARED_USER_PHRASES: Dict[str, str] = _pack_dict('DECLARED_USER_PHRASES')\n"
        "DECLARED_USER_VARIANTS: Dict[str, str] = _pack_dict('DECLARED_USER_VARIANTS')\n")
    new_src = splice(src, spans, loader)
    open(path, "w", encoding="utf-8").write(new_src)
    print("lanes/builtins.py: %d phrase dicts -> lane-phrases.json"
          % len(entries))


if __name__ == "__main__":
    main()
