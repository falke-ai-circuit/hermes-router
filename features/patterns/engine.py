"""features/patterns/engine.py — pack compiler + matcher (P7, proposal §2.6).

Pattern packs are DATA (features/patterns/packs/*.json); the corpus lives
next to them (features/patterns/corpus/*.json) so a live catch becomes a
JSON row + one test-tick instead of a regex patch.

- load_pack(name) — compiles/loads a pack at first use (cached).
- bind_re_families(mod_globals, pack_name) — the container-move loader:
  rebinds a module's historical regex-family names from pack data,
  compiling the regex SOURCE strings VERBATIM (byte-identical text and
  flags — behavior identical by construction; pinned by the P7 parity
  test against the pre-transplant gold snapshot).
- match(text, lanes=[...]) — generic matcher over the lanes registry
  phrase data (corpus-parity surface; route_gate continues reading the
  tables through the P2 registry, which is now pack-fed).
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
PACKS_DIR = os.path.join(_HERE, "packs")
CORPUS_DIR = os.path.join(_HERE, "corpus")

_CACHE: Dict[str, Dict[str, Any]] = {}

_FLAG_NAMES = {
    "IGNORECASE": re.IGNORECASE,
    "DOTALL": re.DOTALL,
    "MULTILINE": re.MULTILINE,
}


def load_pack(name: str) -> Dict[str, Any]:
    """Load one pack (or corpus) JSON by bare name. Cached; fail-loud on
    a missing file (a pack typo must never silently disable a family)."""
    if name in _CACHE:
        return _CACHE[name]
    for d in (PACKS_DIR, CORPUS_DIR):
        path = os.path.join(d, name + ".json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                _CACHE[name] = json.load(fh)
            return _CACHE[name]
    raise KeyError("pattern pack not found: %s" % name)


def compile_entry(entry: Dict[str, Any], mod_globals: Optional[dict] = None
                  ) -> "re.Pattern":
    """Compile one pack entry. 'fmt' entries interpolate the named literal
    params FIRST (verbatim template, verbatim params — same composed text
    the pre-transplant % expression produced)."""
    src = entry["regex"]
    if entry.get("fmt"):
        params = []
        for pname in entry["fmt"]:
            if mod_globals is not None and pname in mod_globals:
                params.append(mod_globals[pname])
            else:
                params.append(load_pack(entry.get("param_pack", "decision-families"))
                              ["literals"][pname])
        src = src % tuple(params)
    flags = 0
    for f in entry.get("flags", []):
        flags |= _FLAG_NAMES[f]
    return re.compile(src, flags)


def bind_re_families(mod_globals: dict, pack_name: str = "decision-families"
                     ) -> None:
    """Container-move loader (§2.6): rebind a module's historical regex
    family names from pack data. Byte-identical regex text + flags; the
    module's downstream code is untouched."""
    pack = load_pack(pack_name)
    for entry in pack["entries"]:
        name = entry["name"]
        if entry.get("kind") == "literal":
            mod_globals[name] = entry["regex"]
        elif entry.get("kind") == "strlist":
            mod_globals[name] = list(entry["regexes"])
        elif entry.get("kind") == "family_dict":
            mod_globals[name] = dict(entry["families"])
        else:
            mod_globals[name] = compile_entry(entry, mod_globals)
    # derived: the historical _FAMILIES_RE comprehension (same shape)
    if "_FAMILIES" in mod_globals and "_FAMILIES_RE" in pack.get("derived", []):
        mod_globals["_FAMILIES_RE"] = {
            k: re.compile(v, re.IGNORECASE)
            for k, v in mod_globals["_FAMILIES"].items()}


def match(text: str, lanes: Optional[List[str]] = None) -> List[Dict[str, str]]:
    """Generic matcher over the lanes registry phrase data. Returns one hit
    dict per matched phrase ({lane, phrase, route}) in registry order.
    Phrase matching is strict containment on the raw text (the corpus
    parity surface — routing continues to run through the registry/lane
    machinery, which owns normalizers and standalone-line discipline)."""
    from hermes_router.lanes import registry as _reg

    hits: List[Dict[str, str]] = []
    t = str(text or "")
    if not t:
        return hits
    for lane_id in (lanes or list(_reg.all_ids())):
        try:
            spec = _reg.lane(lane_id)
        except KeyError:
            continue
        for phrase, route in (spec.phrases or {}).items():
            if phrase and phrase in t:
                hits.append({"lane": lane_id, "phrase": phrase,
                             "route": route})
    return hits
