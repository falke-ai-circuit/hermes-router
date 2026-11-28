"""m64 — LANE CONTRACT GATES (G1-G5).

Spec: m64v2 brief, STEP 4. These gates exist because the obvious test — "the
lane resolved to something" — PASSES ON THE BUG. Every gate here asserts a
property that a plausible-but-wrong implementation fails:

  G1 UNIFORM BLOCK      the four lanes declare the SAME field set; a missing
                        field or a lane-specific name fails.
  G2 POINTER FORMS      all four endpoint forms resolve through ONE key
                        (lanes.<lane>.endpoint) to base_url/key/schema/model.
  G3 PATH NORMALISATION a legacy ".../chat/completions" endpoint and a fresh
                        base URL produce a BYTE-IDENTICAL wire request. A
                        resolver that passes the string through unchanged
                        resolves fine and DOUBLES the tail -> this fails.
  G4 LEGACY COMPAT      with `lanes:` absent, all four lanes resolve from
                        today's keys. The legacy path is exercised by CALLING
                        today's accessors, mocked here to fixed values.
  G5 MANIFEST HYGIENE   no manifest field outside the known set; config_keys
                        >= 2 segments; lane config_schema entries are typed.

Deterministic and network-free: the legacy accessors are monkeypatched, and
the lanes-block path reads only the block passed to it.
"""
from __future__ import annotations

import os
import sys

import pytest
import yaml

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hermes_router.core import schema as core_schema  # noqa: E402
from hermes_router.lanes import resolve as R  # noqa: E402

# Hermes' plugin-manifest known-field set (plugins_manifest._KNOWN_MANIFEST_FIELDS,
# Hermes 0.21.4). Held as a CONSTANT, not imported: /opt/hermes on this host is
# 0.20.0 and has no plugins_manifest module, so importing it would make this
# gate error instead of checking anything. The list is the contract the brief
# measured, and G5 checks our manifest against it.
KNOWN_MANIFEST_FIELDS = frozenset({
    "name", "version", "description", "author", "requires_env",
    "provides_tools", "provides_hooks", "kind", "hooks", "label",
    "optional_env", "platforms", "external_dependencies", "pip_dependencies",
    "provides_browser_providers", "provides_web_providers", "manifest_version",
    "api_version", "requires_plugins", "python_dependencies", "config_schema",
    "license", "homepage", "tags", "capabilities", "emits", "listens",
    "hermes", "depends", "requires_hermes", "python_runtime",
    "provides_locales", "requires_auth", "oauth", "config_keys",
})


def _manifest():
    with open(os.path.join(PLUGIN_DIR, "plugin.yaml"), "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _block(**overrides):
    """A fully-declared lanes block (all four lanes, all four fields)."""
    blk = {
        lane: {"endpoint": "https://" + lane + ".example/v1",
               "model": lane + "-model",
               "schema": core_schema.lane_kind(lane),
               "key_env": lane.upper() + "_KEY"}
        for lane in core_schema.LANE_NAMES
    }
    blk.update(overrides)
    return blk


# ---------------------------------------------------------------------------
# G1 — UNIFORM BLOCK
# ---------------------------------------------------------------------------


def test_g1_uniform_block_is_identical_across_lanes():
    """The declaration table itself: every lane maps to the SAME field tuple
    object, so no lane can acquire a field the others lack."""
    spec = core_schema.lanes_spec()
    assert tuple(spec) == core_schema.LANE_NAMES
    tuples = {lane: tuple(fields) for lane, fields in spec.items()}
    assert len(set(tuples.values())) == 1, f"lanes diverge: {tuples}"
    assert set(core_schema.LANE_FIELDS) == {"endpoint", "model", "schema", "key_env"}


def test_g1_complete_block_has_no_violations():
    assert R.validate_lane_block(_block()) == []


def test_g1_missing_field_fails():
    """A lane missing one declared field violates uniformity."""
    blk = _block()
    del blk["decision"]["key_env"]
    problems = R.validate_lane_block(blk)
    assert any("lanes.decision" in p and "key_env" in p for p in problems), problems


def test_g1_lane_specific_field_name_fails():
    """A lane-specific spelling (e.g. `base_url` on one lane) is an undeclared
    field AND leaves the required one missing — both are reported."""
    blk = _block()
    blk["frontier"].pop("endpoint")
    blk["frontier"]["base_url"] = "https://frontier.example/v1"
    problems = R.validate_lane_block(blk)
    assert any("undeclared field 'base_url'" in p for p in problems), problems
    assert any("missing field 'endpoint'" in p for p in problems), problems


def test_g1_undeclared_lane_fails():
    blk = _block()
    blk["premium"] = dict(blk["aux"])
    assert any("not a declared lane" in p for p in R.validate_lane_block(blk))


def test_g1_manifest_lane_schema_is_uniform():
    """The manifest's typed lane block declares the same field set per lane."""
    lanes = _manifest()["config_schema"]["lanes"]
    assert tuple(lanes) == core_schema.LANE_NAMES
    field_sets = {tuple(sorted(spec)) for spec in lanes.values()}
    assert field_sets == {tuple(sorted(core_schema.LANE_FIELDS))}, field_sets


# ---------------------------------------------------------------------------
# G2 — POINTER FORMS (all four through ONE key)
# ---------------------------------------------------------------------------


def test_g2_form1_openai_base_url():
    """(1) OpenAI-compatible base URL."""
    res = R.resolve_lane("aux", _block(aux={
        "endpoint": "https://api.example/v1", "model": "some-model",
        "schema": "openai", "key_env": "AUX_KEY"}))
    assert res.base_url == "https://api.example/v1"
    assert res.model == "some-model"
    assert res.schema == "openai"
    assert res.key == ""  # env unset -> empty, never a fabricated key


def test_g2_form2_scheme_pointer(monkeypatch):
    """(2) scheme pointer — back-compat, resolved by anchor_chain's own table."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    res = R.resolve_lane("frontier", _block(frontier={
        "endpoint": "openrouter://vendor/model-x", "model": "",
        "schema": "openai", "key_env": "OPENROUTER_API_KEY"}))
    assert res.base_url == "https://openrouter.ai/api/v1"
    assert res.model == "vendor/model-x", "pointer model must survive"
    assert res.key == "or-key"


def test_g2_form3_shim_pointer_with_role_model(monkeypatch):
    """(3) shim pointer + a ROLE model: the role travels verbatim (the shim
    resolves main|aux|vision server-side); the base carries no chat tail."""
    for role in R.ROLE_NAMES:
        res = R.resolve_lane("aux", _block(aux={
            "endpoint": "http://127.0.0.1:8680/v1", "model": role,
            "schema": "openai", "key_env": "AUX_KEY"}))
        assert res.base_url == "http://127.0.0.1:8680/v1"
        assert res.model == role


def test_g2_form4_other_hermes_install_local_only():
    """(4) another Hermes install on the same host, local-only included."""
    res = R.resolve_lane("uncensored", _block(uncensored={
        "endpoint": "http://127.0.0.1:8681/v1", "model": "local-model",
        "schema": "openai", "key_env": "UNC_KEY"}))
    assert res.base_url == "http://127.0.0.1:8681/v1"
    assert res.model == "local-model"


def test_g2_all_four_forms_share_the_one_key():
    """Every form above is addressed through lanes.<lane>.endpoint — the same
    key, same shape out. This asserts the tuple contract itself."""
    forms = ["https://api.example/v1", "openrouter://vendor/model-x",
             "http://127.0.0.1:8680/v1", "http://127.0.0.1:8681/v1"]
    for endpoint in forms:
        res = R.resolve_lane("aux", _block(aux={
            "endpoint": endpoint, "model": "m", "schema": "openai",
            "key_env": "AUX_KEY"}))
        assert isinstance(res, tuple) and len(res) == 4
        base, key, schema_, model = res
        assert base and "/chat/completions" not in base
        assert schema_ in core_schema.LANE_KINDS
        assert model


def test_g2_declared_schema_beats_url_shape():
    """`schema` is DECLARED, never inferred: a /v1 base that declares typesafe
    stays typesafe, and an empty declaration falls back to the lane default."""
    res = R.resolve_lane("decision", _block(decision={
        "endpoint": "https://api.example/v1", "model": "m",
        "schema": "typesafe", "key_env": "D_KEY"}))
    assert res.schema == "typesafe"
    defaulted = R.resolve_lane("decision", _block(decision={
        "endpoint": "https://api.example/v1", "model": "m",
        "schema": "", "key_env": "D_KEY"}))
    assert defaulted.schema == core_schema.lane_kind("decision") == "typesafe"


# ---------------------------------------------------------------------------
# G3 — PATH NORMALISATION (the gate that fails on the bug)
# ---------------------------------------------------------------------------


def test_g3_legacy_full_path_and_fresh_base_are_byte_identical():
    """The defect this unit exists for: a legacy key carries a FULL chat path,
    a lane block carries a BASE. Both must reach the wire as the SAME bytes.

    A resolver that hands the endpoint through unchanged normalises nothing;
    the caller appends /chat/completions and the request goes to
    ".../chat/completions/chat/completions" — which fails SILENTLY (fail-open
    reads it as an ordinary model error). Byte equality is the only assertion
    that catches it.
    """
    spec = {"model": "qwen-3-8-27b", "schema": "openai", "key_env": "VENICE_KEY"}
    legacy = R.resolve_lane("uncensored", _block(uncensored=dict(
        spec, endpoint="https://api.venice.ai/api/v1/chat/completions")))
    fresh = R.resolve_lane("uncensored", _block(uncensored=dict(
        spec, endpoint="https://api.venice.ai/api/v1")))

    assert legacy.base_url == fresh.base_url == "https://api.venice.ai/api/v1"
    wl, wf = R.wire(legacy), R.wire(fresh)
    assert wl["url"] == wf["url"], f"wire URL drift:\n {wl['url']}\n {wf['url']}"
    assert wl["body"] == wf["body"]
    # prove-the-negative: the bug's own shape is absent from the result
    assert "/chat/completions/chat/completions" not in wl["url"]
    assert wl["url"].endswith("/api/v1/chat/completions")
    assert wl["url"].count("/chat/completions") == 1


def test_g3_doubled_and_trailing_slash_tails_collapse():
    """Even a doubled legacy tail collapses to the base — the loop in
    strip_chat_tail is what makes that true, and a single-pass strip leaves
    one copy behind."""
    cases = {
        "https://h.example/v1/chat/completions/chat/completions": "https://h.example/v1",
        "https://h.example/v1/chat/completions/": "https://h.example/v1",
        "https://h.example/v1/": "https://h.example/v1",
        "https://h.example/v1": "https://h.example/v1",
        "  https://h.example/v1/chat/completions  ": "https://h.example/v1",
    }
    for raw, expected in cases.items():
        assert R.strip_chat_tail(raw) == expected, raw


def test_g3_shim_pointer_matches_legacy_full_path():
    """The same equality for the loopback shim form (the brief's own example:
    classification.aux_endpoint.url carries the full path while a lane carries
    the base)."""
    spec = {"model": "aux", "schema": "openai", "key_env": "AUX_KEY"}
    legacy = R.resolve_lane("aux", _block(aux=dict(
        spec, endpoint="http://127.0.0.1:8680/v1/chat/completions")))
    fresh = R.resolve_lane("aux", _block(aux=dict(
        spec, endpoint="http://127.0.0.1:8680/v1")))
    assert R.wire(legacy) == R.wire(fresh)
    assert R.wire(legacy)["url"] == "http://127.0.0.1:8680/v1/chat/completions"


def test_g3_wire_composes_exactly_one_tail():
    for lane in core_schema.LANE_NAMES:
        res = R.resolve_lane(lane, _block())
        url = R.wire(res)["url"]
        assert url.count("/chat/completions") == 1
        assert url.endswith("/chat/completions")


# ---------------------------------------------------------------------------
# G4 — LEGACY COMPAT (lanes: ABSENT)
# ---------------------------------------------------------------------------


@pytest.fixture()
def legacy_accessors(monkeypatch):
    """Today's four accessors, mocked to fixed values. G4 asserts the resolver
    CALLS them (so "exactly as today" holds by construction) and maps their
    output into the one lane shape."""
    from hermes_router import anchor_chain, decision_head, router
    from hermes_router.core import aux_endpoint, provider_derivation

    calls = {"chain": 0, "anchor": 0, "decision": 0, "aux": 0}

    def _chain_entries():
        calls["chain"] += 1
        return [{"url": "https://api.venice.ai/api/v1/chat/completions",
                 "model": "qwen-3-8-27b", "key_env": "VENICE_API_KEY"}]

    def _read_key(key_file, key_env=""):
        return "legacy-unc-key"

    class _Ep:
        def __init__(self, base, model, env):
            self.base_url, self.model = base, model
            self.api_key_env, self.api_key_value = env, ""

    def _load_anchor_chain():
        calls["anchor"] += 1

        class _Chain:
            primary = _Ep("https://openrouter.ai/api/v1", "vendor/anchor-m", "OPENROUTER_API_KEY")
            judge = None
        return _Chain()

    def _embedding_endpoint():
        calls["decision"] += 1
        return {"url": "https://api.minimax.io/v1/chat/completions",
                "model": "minimax-m", "key_env": "MINIMAX_API_KEY"}

    def _fallback_base_url():
        calls["aux"] += 1
        return "https://inference-api.example/v1"

    monkeypatch.setattr(router, "_chain_entries", _chain_entries, raising=True)
    monkeypatch.setattr(router, "_read_key", _read_key, raising=True)
    monkeypatch.setattr(anchor_chain, "load_anchor_chain", _load_anchor_chain, raising=True)
    monkeypatch.setattr(decision_head, "_embedding_endpoint", _embedding_endpoint, raising=True)
    monkeypatch.setattr(aux_endpoint, "fallback_base_url", _fallback_base_url, raising=True)
    monkeypatch.setattr(provider_derivation, "_core_runtime",
                        lambda: {"api_key": "core-key"}, raising=True)
    monkeypatch.setattr(provider_derivation, "host_provider_model",
                        lambda: "host-model", raising=True)
    return calls


def test_g4_all_four_lanes_resolve_without_the_block(legacy_accessors):
    """`lanes:` absent -> every lane resolves from today's keys."""
    all_lanes = R.resolve_all(block={})
    assert set(all_lanes) == set(core_schema.LANE_NAMES)

    base, key, schema_, model = all_lanes["uncensored"]
    assert base == "https://api.venice.ai/api/v1"
    assert "/chat/completions" not in base
    assert (key, schema_, model) == ("legacy-unc-key", "openai", "qwen-3-8-27b")

    base, key, schema_, model = all_lanes["frontier"]
    assert (base, model, schema_) == ("https://openrouter.ai/api/v1",
                                      "vendor/anchor-m", "openai")

    base, key, schema_, model = all_lanes["decision"]
    assert base == "https://api.minimax.io/v1"
    assert (key, schema_, model) == ("", "typesafe", "minimax-m")

    base, key, schema_, model = all_lanes["aux"]
    assert (base, key, schema_, model) == ("https://inference-api.example/v1",
                                           "core-key", "openai", "host-model")


def test_g4_legacy_path_actually_calls_todays_accessors(legacy_accessors):
    """Prove-the-negative for A4: the legacy branch must not re-derive values
    locally — it must reach today's accessors. Counters, not hope."""
    R.resolve_all(block={})
    assert legacy_accessors == {"chain": 1, "anchor": 1, "decision": 1, "aux": 1}


def test_g4_block_present_never_merges_with_legacy(legacy_accessors):
    """Present -> new block ONLY. A lane declared in the block must not touch a
    legacy accessor, and a legacy value must not leak into it."""
    res = R.resolve_lane("uncensored", _block(uncensored={
        "endpoint": "https://new.example/v1", "model": "new-model",
        "schema": "openai", "key_env": "NEW_KEY"}))
    assert (res.base_url, res.model) == ("https://new.example/v1", "new-model")
    assert legacy_accessors["chain"] == 0, "legacy chain read despite a block"


def test_g4_absent_lane_falls_back_to_legacy(legacy_accessors):
    """A partially-migrated profile: a lane MISSING from the block resolves
    legacy, while declared lanes stay on the block. Never both for one lane."""
    blk = _block()
    blk.pop("frontier")
    assert R.resolve_lane("frontier", blk).model == "vendor/anchor-m"
    assert R.resolve_lane("aux", blk).base_url == "https://aux.example/v1"


def test_g4_empty_endpoint_is_treated_as_absent(legacy_accessors):
    """An endpoint declared as "" names nothing, so the lane is legacy — not a
    silent empty resolution."""
    blk = _block(aux={"endpoint": "", "model": "m", "schema": "openai",
                      "key_env": "K"})
    assert R.resolve_lane("aux", blk).base_url == "https://inference-api.example/v1"


def test_g4_unknown_lane_is_empty_not_an_exception():
    base, key, schema_, model = R.resolve_lane("premium")
    assert (base, key, schema_, model) == ("", "", "", "")


# ---------------------------------------------------------------------------
# G5 — MANIFEST HYGIENE
# ---------------------------------------------------------------------------


def test_g5_no_unknown_manifest_fields():
    """Every key is a known Hermes manifest field. The two that used to be
    declared here (middleware_schema, observer_schema) warned on every load."""
    unknown = sorted(set(_manifest()) - KNOWN_MANIFEST_FIELDS)
    assert unknown == [], f"unknown manifest field(s): {unknown}"
    assert "middleware_schema" not in _manifest()
    assert "observer_schema" not in _manifest()


def test_g5_known_fields_are_left_alone():
    """The fix must not have removed fields that ARE known and load-bearing."""
    manifest = _manifest()
    for field in ("hooks", "manifest_version", "provides_tools",
                  "requires_env", "config_schema"):
        assert field in manifest, field
    assert "transform_llm_output" in manifest["hooks"]
    # `middleware`/`*_schema` are the three fields m64 removes precisely
    # BECAUSE they are not known — they must not reappear (see the G5 field
    # test above and the absence pins in tests/config/test_manifest_loads.py).
    for removed in ("middleware", "middleware_schema", "observer_schema"):
        assert removed not in manifest, removed


def test_g5_config_keys_are_dotted_two_segments():
    """manifest_config_keys warns and SKIPS a 1-segment path."""
    keys = _manifest().get("config_keys")
    assert isinstance(keys, list) and keys
    for entry in keys:
        path = str(entry)
        assert path.count(".") >= 1, f"1-segment config_key skipped: {path}"
        assert len([s for s in path.split(".") if s]) >= 2, path
    # The lanes section is declared per-lane, never as the bare plugin name.
    assert any(str(k).startswith("hermes_router.lanes.") for k in keys), keys
    assert "hermes_router" not in keys


def test_g5_lane_config_schema_is_typed_not_prose():
    """Every lanes.<lane>.<field> carries an explicit type + default. The
    older type:dict entries put field names in PROSE strings, which types zero
    nested keys — this asserts the lane block does not repeat that."""
    lanes = _manifest()["config_schema"]["lanes"]
    for lane, spec in lanes.items():
        assert set(spec) == set(core_schema.LANE_FIELDS), lane
        for field, decl in spec.items():
            assert isinstance(decl, dict), f"{lane}.{field} is prose, not a mapping"
            assert "type" in decl, f"{lane}.{field} has no type"
            assert decl["type"] in ("str", "int", "bool", "float", "list", "dict"), \
                f"{lane}.{field} type={decl['type']!r}"
            assert "default" in decl, f"{lane}.{field} has no default"


def test_g5_schema_declares_every_lane_path():
    """core/schema.py declares one typed key per lanes.<lane>.<field> — the
    manifest and the typed inventory cannot drift."""
    for lane in core_schema.LANE_NAMES:
        for path in core_schema.lane_paths(lane):
            assert core_schema.key(path) is not None, path
