"""P7 — pattern packs + corpus + parity harness (proposal §2.6).

PARITY: every corpus row runs against BOTH the historical matcher path
(the live module data — decision._FAMILIES_RE / classifier.PATTERN_GROUPS
/ the lanes registry phrase dicts, all now pack-fed) AND the engine's
independent compile+scan. Identical results on every row = parity green.
A corpus mismatch FAILS the test (FP/miss regressions become data diffs).

Gold pin: the pack regex SOURCE text is the verbatim pre-transplant
text — the harness asserts the pack strings still compile and that the
live modules' compiled patterns carry exactly the pack text (regex text
identical, container moved).
"""
import re

import pytest

pytestmark = pytest.mark.filterwarnings("ignore")

_ENGINE = None
_CORPUS = None


def _engine():
    global _ENGINE
    if _ENGINE is None:
        from hermes_router.features.patterns import engine

        _ENGINE = engine
    return _ENGINE


def _corpus():
    global _CORPUS
    if _CORPUS is None:
        _CORPUS = _engine().load_pack("router-corpus")["rows"]
    return _CORPUS


# --- gold pin: pack text is the verbatim transplant -------------------------

def test_decision_pack_gold_pin():
    from hermes_router import decision

    pack = _engine().load_pack("decision-families")
    names = {e["name"] for e in pack["entries"]}
    assert len(names) == 25 and "_FAMILIES" in names
    # every live module pattern carries the pack's exact text
    for e in pack["entries"]:
        if e.get("kind") != "pattern" or e.get("fmt"):
            continue  # fmt entries are pinned via their composed text below
        live = getattr(decision, e["name"])
        assert isinstance(live, re.Pattern)
        assert live.pattern == e["regex"], e["name"]
        flags = 0
        for f in e.get("flags", []):
            flags |= getattr(re, f)
        if e.get("flags"):
            assert live.flags & flags, e["name"]
    # fmt entries: the live composed text == template % params (verbatim)
    for e in pack["entries"]:
        if e.get("fmt"):
            words = tuple(pack["literals"][n] for n in e["fmt"])
            live = getattr(decision, e["name"])
            assert live.pattern == e["regex"] % words, e["name"]


def test_classifier_pack_gold_pin():
    from hermes_router import classifier

    pack = _engine().load_pack("classifier-groups")
    for e in pack["entries"]:
        live = getattr(classifier, e["name"])
        assert list(live) == list(e["regexes"]), e["name"]


def test_lane_phrases_pack_gold_pin():
    from hermes_router.lanes import builtins

    pack = _engine().load_pack("lane-phrases")
    for e in pack["entries"]:
        live = getattr(builtins, e["name"])
        assert dict(live) == dict(e["families"]), e["name"]


# --- corpus parity harness (old matcher path vs new engine) -----------------

def _expected_families(row):
    return sorted(row.get("expect_families", []))


def _decision_hits_old(text):
    from hermes_router import decision

    return sorted(k for k, rx in decision._FAMILIES_RE.items()
                  if rx.search(text))


def _decision_hits_engine(text):
    pack = _engine().load_pack("decision-families")
    hits = []
    for e in pack["entries"]:
        if e.get("kind") == "pattern" and e["name"].startswith("_") \
                and e["name"] not in ("_FAMILIES",):
            continue
    # the families themselves: family_dict entries compiled independently
    fam = {k: re.compile(v, re.IGNORECASE)
           for k, v in pack["entries"][1]["families"].items()}
    return sorted(k for k, rx in fam.items() if rx.search(text))


def _classifier_hits_old(text, groups):
    from hermes_router import classifier

    return sorted(g for g in groups
                  if g != "line_hold_essay" and any(
                      rx.search(text)
                      for rx in classifier.PATTERN_GROUPS.get(g, [])))


def _classifier_hits_engine(text, groups):
    pack = _engine().load_pack("classifier-groups")
    data = {e["name"]: [re.compile(rx, re.IGNORECASE) for rx in e["regexes"]]
            for e in pack["entries"]}
    return sorted(g for g in groups
                  if any(rx.search(text) for rx in data.get("_%s" % _group_list_name(g), [])))


def _group_list_name(group):
    return {"csam_underage": "CSAM_UNDERAGE",
            "bioweapon_protocol": "BIOWEAPON_PROTOCOL",
            "ied_construction": "IED_CONSTRUCTION",
            "named_target_defamation": "NAMED_TARGET_DEFAMATION",
            "trafficking_route": "TRAFFICKING_ROUTE",
            "weaponized_playbook_real_name": "WEAPONIZED_PLAYBOOK_REAL_NAME",
            "refusal_phrases": "REFUSAL_PHRASES"}[group]


def test_corpus_decision_parity():
    rows = [r for r in _corpus() if r["pack"] == "decision-families"]
    assert len(rows) >= 6
    for row in rows:
        old = _decision_hits_old(row["input"])
        new = _decision_hits_engine(row["input"])
        assert old == new, (row["input"], old, new)
        assert old == _expected_families(row), (row["input"], old)


def test_corpus_classifier_parity():
    rows = [r for r in _corpus() if r["pack"] == "classifier-groups"]
    assert len(rows) >= 5
    for row in rows:
        groups = row.get("expect_groups")
        if groups is None:
            continue
        old = _classifier_hits_old(row["input"], ["refusal_phrases",
                                                  "ied_construction"])
        new = _classifier_hits_engine(row["input"], ["refusal_phrases",
                                                     "ied_construction"])
        assert old == new == sorted(groups), row["input"]


def test_corpus_lane_phrase_parity():
    rows = [r for r in _corpus() if r["pack"] == "lane-phrases"]
    assert len(rows) >= 5
    for row in rows:
        new = _engine().match(row["input"], lanes=row.get("lanes"))
        # old path: the registry phrase dicts themselves
        from hermes_router.lanes import registry as reg

        old = []
        for lane_id in row.get("lanes", list(reg.all_ids())):
            for phrase, route in reg.lane(lane_id).phrases.items():
                if phrase and phrase in row["input"]:
                    old.append({"lane": lane_id, "phrase": phrase,
                                "route": route})
        assert old == new == row.get("expect_lanes", []), row["input"]
