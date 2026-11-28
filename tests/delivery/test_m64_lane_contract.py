"""m64 Unit A — THE LANE CONTRACT: one block shape, one resolver.

What is asserted, and why each assertion exists:

  1. UNIFORMITY — all four lanes declare the SAME four fields, in the same
     order, with no lane-specific name anywhere in the block. The defect this
     catches is the pre-m64 state: `typesafe_endpoint` on one lane,
     `aux_endpoint` on another, `chain[0].url` on a third. A per-lane field
     name is how four readers grew out of one idea.

  2. DECLARED SCHEMA — `schema` is read from the declaration, never inferred
     from the endpoint's URL shape. Pinned with a decision lane pointed at an
     OpenAI-LOOKING base: a resolver that sniffs `/v1` reports `openai` and
     fails here.

  3. THE PATH TRAP — a legacy key carrying a FULL chat path
     (`.../api/v1/chat/completions`) must resolve to the BASE. A naive
     resolver returns the full path and every caller appends `/chat/completions`
     again -> a 404 that reads like a dead provider. This is the assertion the
     brief's defect turns on, so it is pinned on all four forms.

  4. THE FOUR ENDPOINT FORMS through the SAME key: OpenAI-compatible base,
     `scheme://<model>` pointer, shim pointer with a `main|aux|vision` role,
     and another Hermes install on this host (local-only, keyless).

  5. NEVER RAISES — malformed URL, unknown lane, unusable config: a 4-tuple
     comes back and the base is empty, so the caller fails open exactly like
     every other lane seam.

Deterministic throughout: no network, no real credential. `key_env` is read
from a monkeypatched environment, so the battery does not depend on whatever
this profile happens to have exported.
"""
from __future__ import annotations

import os
import sys

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hermes_router.core import schema  # noqa: E402
from hermes_router.lanes import resolve as lane_resolve  # noqa: E402


LANES = ("aux", "frontier", "uncensored", "decision")


@pytest.fixture(autouse=True)
def _no_live_config(monkeypatch):
    """Hermetic by default: every test starts from an EMPTY router section.

    Without this the resolver reads whatever the box's live profile happens to
    configure (this fleet configures an aux endpoint, an anchor primary and a
    render chain), so a test that hands in its own block could still pick up a
    real endpoint through the legacy fallback — passing on one machine and
    failing on another. Tests that need a config set `router_section` after
    this fixture runs, which monkeypatch allows.
    """
    from hermes_router.core import config_access

    monkeypatch.setattr(config_access, "router_section", lambda: {})
    try:
        config_access.reset_cache()
    except Exception:  # noqa: BLE001 — no cache in this build
        pass

# The declared schema per lane, as the brief fixes it.
DECLARED_SCHEMAS = {
    "aux": "openai",
    "frontier": "openai",
    "uncensored": "openai",
    "decision": "typesafe",
}

# Field names that mean "this lane has its own spelling". None may appear in
# the lane block: the block is four generic fields or the contract is gone.
LANE_SPECIFIC_NAMES = (
    "typesafe_endpoint", "typesafe_api_key_env", "openrouter_endpoint",
    "aux_endpoint", "aux_model", "chat_path", "url", "key_file",
    "base_url", "api_key_env", "provider", "hosts",
)


# ---------------------------------------------------------------------------
# 1. uniformity of the declaration
# ---------------------------------------------------------------------------
def test_every_lane_declares_the_same_four_fields():
    assert tuple(schema.LANE_FIELDS) == ("endpoint", "model", "schema", "key_env")
    for lane in LANES:
        block = schema.LANES[lane]
        assert tuple(block) == schema.LANE_FIELDS, (
            "lane %r declares %r, not the uniform field set %r"
            % (lane, tuple(block), schema.LANE_FIELDS))


def test_no_lane_specific_field_name_inside_the_block():
    offenders = []
    for lane in LANES:
        for name in schema.LANES[lane]:
            if name not in schema.LANE_FIELDS:
                offenders.append("%s.%s" % (lane, name))
            if name in LANE_SPECIFIC_NAMES:
                offenders.append("%s.%s (lane-specific spelling)" % (lane, name))
    assert not offenders, (
        "the lane block grew a lane-specific field name — one field set per "
        "lane or the resolver is four resolvers again: " + ", ".join(offenders))


def test_declared_schemas_are_the_briefs_values():
    for lane, expected in DECLARED_SCHEMAS.items():
        assert schema.LANES[lane]["schema"] == expected, (
            "lane %r declares schema %r, expected %r"
            % (lane, schema.LANES[lane]["schema"], expected))
        assert lane_resolve.declared_schema(lane) == expected
    assert set(schema.LANE_SCHEMAS) == {"openai", "typesafe"}


# ---------------------------------------------------------------------------
# 2. schema is DECLARED, never sniffed from the URL
# ---------------------------------------------------------------------------
def test_schema_is_declared_not_inferred_from_the_endpoint_shape():
    """The decision lane pointed at an OpenAI-LOOKING base must still say
    `typesafe`: the profile declared it. A URL-sniffing resolver says `openai`.
    """
    block = {"endpoint": "https://api.example.com/v1", "model": "m",
             "schema": "typesafe", "key_env": "EXAMPLE_KEY"}
    base, key, schema_name, model = lane_resolve.resolve("decision", block)
    assert base == "https://api.example.com/v1"
    assert schema_name == "typesafe", (
        "schema followed the URL shape instead of the declaration")
    assert model == "m"


def test_out_of_enum_schema_falls_back_to_the_lane_declaration():
    block = {"endpoint": "https://api.example.com/v1", "model": "m",
             "schema": "anthropic-ish", "key_env": "EXAMPLE_KEY"}
    _, _, schema_name, _ = lane_resolve.resolve("decision", block)
    assert schema_name == "typesafe", (
        "an out-of-enum schema must fall back to the lane's declaration, "
        "never to a guess")


# ---------------------------------------------------------------------------
# 3. THE PATH TRAP
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("url,expected", [
    ("https://api.venice.ai/api/v1/chat/completions",
     "https://api.venice.ai/api/v1"),
    ("https://api.venice.ai/api/v1/chat/completions/",
     "https://api.venice.ai/api/v1"),
    ("https://api.example/v1", "https://api.example/v1"),
    ("https://api.example/v1/", "https://api.example/v1"),
    ("", ""),
])
def test_strip_chat_path_returns_a_base(url, expected):
    assert lane_resolve.strip_chat_path(url) == expected


def test_legacy_full_chat_path_resolves_to_the_base_not_the_full_path():
    """The brief's measured defect, pinned through the RESOLVER (not just the
    helper): a legacy chain entry carrying the full chat path must hand back a
    base, so a caller composing base + '/chat/completions' dials one path, not
    two."""
    block = {
        "endpoint": "https://api.venice.ai/api/v1/chat/completions",
        "model": "qwen-3-8-27b",
        "schema": "openai",
        "key_env": "VENICE_API_KEY",
    }
    base, _key, schema_name, model = lane_resolve.resolve("uncensored", block)
    assert base == "https://api.venice.ai/api/v1", (
        "the resolver returned the FULL chat path; every caller appends "
        "/chat/completions again and the lane 404s")
    assert not base.endswith("/chat/completions")
    assert schema_name == "openai"
    assert model == "qwen-3-8-27b"


def test_the_strip_applies_to_every_form_not_just_the_url_form():
    """A declared endpoint, a shim pointer and a local-install pointer all go
    through the same strip — the trap is not a property of the legacy key."""
    cases = [
        ("aux", "http://127.0.0.1:8680/v1/chat/completions"),
        ("aux", "http://127.0.0.1:9000/v1/chat/completions"),
        ("frontier", "https://api.example/v1/chat/completions"),
    ]
    for lane, endpoint in cases:
        block = {"endpoint": endpoint, "model": "m", "schema": "openai",
                 "key_env": ""}
        base, _k, _s, _m = lane_resolve.resolve(lane, block)
        assert base and not base.endswith("/chat/completions"), (
            "lane %r returned %r with the chat tail intact" % (lane, base))


# ---------------------------------------------------------------------------
# 4. the four endpoint FORMS, one key
# ---------------------------------------------------------------------------
def test_form_1_openai_compatible_base(monkeypatch):
    monkeypatch.setenv("M64_EXAMPLE_KEY", "sk-example")
    block = {"endpoint": "https://api.example.com/v1", "model": "gpt-x",
             "schema": "openai", "key_env": "M64_EXAMPLE_KEY"}
    base, key, schema_name, model = lane_resolve.resolve("aux", block)
    assert (base, key, schema_name, model) == (
        "https://api.example.com/v1", "sk-example", "openai", "gpt-x")


def test_form_2_scheme_pointer_back_compat():
    """`openrouter://<model>` is carried by profiles today — it must resolve
    through the SAME key, and the model comes out of the pointer."""
    block = {"endpoint": "openrouter://anthropic/claude-sonnet-5.5",
             "model": "", "schema": "openai", "key_env": "OPENROUTER_API_KEY"}
    base, _key, schema_name, model = lane_resolve.resolve("frontier", block)
    assert base.startswith("http"), "scheme pointer did not resolve to a base"
    assert "openrouter" in base
    assert model == "anthropic/claude-sonnet-5.5"
    assert schema_name == "openai"


@pytest.mark.parametrize("role", lane_resolve.SHIM_ROLES)
def test_form_3_shim_pointer_returns_the_role_as_the_model(role):
    block = {"endpoint": "http://127.0.0.1:8680/v1", "model": role,
             "schema": "openai", "key_env": ""}
    base, key, schema_name, model = lane_resolve.resolve("aux", block)
    assert base == "http://127.0.0.1:8680/v1"
    assert model == role, (
        "the shim resolves role -> model itself; the resolver must pass the "
        "role through unchanged")
    assert key == "", "the shim is keyless by design — an empty key is correct"
    assert schema_name == "openai"


def test_form_4_another_hermes_install_on_this_host():
    """A loopback base on any port OTHER than the shim is another local
    Hermes install: local-only, keyless, and it must not be mistaken for a
    remote provider needing a credential."""
    block = {"endpoint": "http://127.0.0.1:18649/v1", "model": "main",
             "schema": "openai", "key_env": "SOME_UNSET_KEY"}
    base, key, _schema, model = lane_resolve.resolve("aux", block)
    assert base == "http://127.0.0.1:18649/v1"
    assert key == "", "a local install is keyless by construction"
    assert model == "main"


# ---------------------------------------------------------------------------
# 5. fail-open: never raises, never invents an endpoint
# ---------------------------------------------------------------------------
def test_resolver_returns_a_four_tuple_for_every_lane():
    for lane in LANES:
        out = lane_resolve.resolve(lane)
        assert isinstance(out, tuple) and len(out) == 4, (
            "lane %r returned %r, not (base, key, schema, model)" % (lane, out))
        assert all(isinstance(part, str) for part in out)


def test_unknown_lane_and_empty_endpoint_fail_open():
    assert lane_resolve.resolve("no_such_lane") == ("", "", "openai", "")
    assert lane_resolve.resolve("aux", {"endpoint": ""})[0] == ""


@pytest.mark.parametrize("endpoint", [
    "http://[::1",              # malformed IPv6 literal
    "not a url at all",
    "://",
    "ftp:/broken",
])
def test_malformed_endpoint_never_raises(endpoint):
    out = lane_resolve.resolve("aux", {"endpoint": endpoint, "model": "m"})
    assert isinstance(out, tuple) and len(out) == 4


def test_absent_key_env_is_empty_not_an_error(monkeypatch):
    monkeypatch.delenv("M64_DEFINITELY_UNSET", raising=False)
    block = {"endpoint": "https://api.example.com/v1", "model": "m",
             "schema": "openai", "key_env": "M64_DEFINITELY_UNSET"}
    base, key, _schema, _model = lane_resolve.resolve("aux", block)
    assert base == "https://api.example.com/v1"
    assert key == "", "an unset key_env yields an empty key, not a raise"


def test_key_env_file_form_is_read_from_disk(tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text("  sk-from-file\n", encoding="utf-8")
    block = {"endpoint": "https://api.example.com/v1", "model": "m",
             "schema": "openai", "key_env": "file:%s" % key_file}
    _base, key, _schema, _model = lane_resolve.resolve("aux", block)
    assert key == "sk-from-file"


# ---------------------------------------------------------------------------
# 6. the block is read from the profile when no block is handed in
# ---------------------------------------------------------------------------
def test_profile_lane_block_wins_over_the_declared_default(monkeypatch):
    from hermes_router.core import config_access

    section = {"lanes": {"aux": {"endpoint": "https://cfg.example/v1",
                                 "model": "cfg-model",
                                 "schema": "openai",
                                 "key_env": "CFG_KEY"}}}
    monkeypatch.setattr(config_access, "router_section", lambda: section)
    monkeypatch.setenv("CFG_KEY", "sk-cfg")
    config_access.reset_cache()
    base, key, schema_name, model = lane_resolve.resolve("aux")
    assert base == "https://cfg.example/v1"
    assert key == "sk-cfg"
    assert schema_name == "openai"
    assert model == "cfg-model"


def test_legacy_surface_still_resolves_when_lanes_block_is_absent(monkeypatch):
    """No migration required: a profile still carrying `decision.endpoint` /
    `typesafe_endpoint`-era keys resolves through the same call."""
    from hermes_router.core import config_access

    section = {"decision": {"endpoint": "https://api.typesafe.ai/v1/systemone",
                            "model": "systemone", "api_key_env": "TYPESAFE_API_KEY"}}
    monkeypatch.setattr(config_access, "router_section", lambda: section)
    config_access.reset_cache()
    base, _key, schema_name, model = lane_resolve.resolve("decision")
    assert base == "https://api.typesafe.ai/v1/systemone"
    assert schema_name == "typesafe", "the decision lane is typesafe by declaration"
    assert model == "systemone"
