# tests/config/test_m23_grouped_schema.py — M23-01 guard tests.
#
# The grouped schema (spec M20_CONFIG_CONSULT §5 + Appendix A) is only worth
# declaring if three things hold, so there are three proofs plus the pins that
# keep the compatibility promise honest:
#
#   1. every declared key LOADS — a fixture carrying every default validates
#      with zero violations and no config_unknown_key / config_key_missing row;
#   2. an OLD name RESOLVES — get() on the pre-M23 spelling lands on the grouped
#      path, and get() on the grouped path falls back to the old spelling that
#      is actually deployed;
#   3. an UNKNOWN path still EMITS telemetry (the gate that stops drift
#      re-entering through an undeclared read).
#
# Plus: the 123 pre-M23 keys are all still declared (nothing removed in v1) and
# the paths group stays code-owned (route-log integrity must not become
# operator-writable by being standardised).
import pytest

from hermes_router.core import config_access, schema, telemetry


@pytest.fixture(autouse=True)
def _clean_telemetry(monkeypatch):
    """Rows captured per test; the once-per-process missing-key memo cleared so
    each test sees its own first read."""
    rows = []
    monkeypatch.setattr(telemetry, "log_route",
                        lambda event, **f: rows.append((event, dict(f))))
    monkeypatch.setattr(config_access, "_MISSING_KEY_SEEN", set())
    config_access.reset_cache()
    return rows


def _section_from_defaults():
    """A config section carrying EVERY declared key at its schema default."""
    section = {}
    for k in schema.SCHEMA:
        parts = k.path.split(".")
        node = section
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = k.default
    return section


def _use_section(monkeypatch, section):
    monkeypatch.setattr(config_access, "router_section", lambda: section)


# --- 1. every declared key loads ------------------------------------------

def test_schema_has_all_ten_groups_and_keys(monkeypatch):
    assert schema.GROUPS == ("operator", "render", "classify", "anchor",
                            "decision", "reflex", "persona", "budgets",
                            "paths", "pricing")
    for group in schema.GROUPS:
        keys = schema.keys_in_group(group)
        # `pricing` is model-keyed (pricing.<model>.{input,output}_per_1m), so
        # its declared container is the key itself, not a fixed leaf.
        assert keys or schema.key(group) is not None, \
            "group %r declares nothing" % group
        for k in keys:
            assert k.type is not None, "%s has no declared type" % k.path
            assert k.path.startswith(group + ".")


def test_every_grouped_key_loads(monkeypatch, _clean_telemetry):
    """A config carrying every GROUPED key at its default validates clean and
    reads back its own default — no unknown key, no missing key."""
    section = {}
    for group in schema.GROUPS:
        for k in schema.keys_in_group(group):
            if group_of_path(k.path) == k.path:
                section[k.path] = k.default
                continue
            parts = k.path.split(".")
            node = section
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = k.default
    _use_section(monkeypatch, section)
    for group in schema.GROUPS:
        for k in schema.keys_in_group(group):
            got = config_access.get(k.path)
            assert got == k.default or (got is None and k.default is None), \
                "%s did not load its declared default (got %r)" % (k.path, got)
    events = [r[0] for r in _clean_telemetry]
    assert "config_unknown_key" not in events
    assert "config_key_missing" not in events


def test_every_v1_key_still_loads(monkeypatch, _clean_telemetry):
    """The 123 pre-M23 keys keep working on a pre-M23 config: each one reads
    its own value back with no unknown/missing row (the alias step must fall
    back to the deployed spelling when the grouped path is absent)."""
    section = {}
    for k in schema._V1_KEYS:
        parts = k.path.split(".")
        node = section
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = k.default
    _use_section(monkeypatch, section)
    for k in schema._V1_KEYS:
        got = config_access.get(k.path)
        assert got == k.default or (got is None and k.default is None), \
            "%s regressed on a pre-M23 config (got %r)" % (k.path, got)
    assert "config_unknown_key" not in [r[0] for r in _clean_telemetry]


def group_of_path(path):
    head = str(path).split(".", 1)[0]
    return head if "." not in str(path) else ""


def test_full_default_fixture_validates_clean(monkeypatch, _clean_telemetry):
    """Grouped + v1 defaults together (the shape a migrated profile has): the
    load-time validator reports zero violations."""
    section = _section_from_defaults()
    _use_section(monkeypatch, section)
    assert config_access.validate_config() == 0
    events = [r[0] for r in _clean_telemetry]
    assert "config_unknown_key" not in events


def test_nothing_removed_from_v1(monkeypatch):
    assert len(schema._V1_KEYS) == 123, "the pre-M23 inventory must stay 123"
    declared = {k.path for k in schema.SCHEMA}
    missing = [k.path for k in schema._V1_KEYS if k.path not in declared]
    assert not missing, "keys dropped from SCHEMA: %s" % missing


# --- 2. old names resolve via alias ---------------------------------------

ALIAS_CASES = (
    # (old name, grouped path, section carrying ONLY the grouped path, value)
    ("enabled", "operator.enabled", {"operator": {"enabled": False}}, False),
    ("dry_run", "operator.dry_run", {"operator": {"dry_run": True}}, True),
    ("render_max_chars", "render.max_chars", {"render": {"max_chars": 77}}, 77),
    ("render_method_spec", "render.method_spec",
     {"render": {"method_spec": "shape"}}, "shape"),
    ("chain", "render.chain",
     {"render": {"chain": [{"name": "primary"}]}}, [{"name": "primary"}]),
    ("endpoint", "render.chain",
     {"render": {"chain": [{"url": "https://x/v1"}]}}, [{"url": "https://x/v1"}]),
    ("classification.mode", "classify.mode", {"classify": {"mode": "shadow"}},
     "shadow"),
    ("classification.pre_classify", "classify.pre_enabled",
     {"classify": {"pre_enabled": False}}, False),
    ("classification.aux_endpoint", "classify.aux.endpoint",
     {"classify": {"aux": {"endpoint": "https://aux/v1"}}},
     "https://aux/v1"),
    ("classification.aux_breaker_cooldown_seconds",
     "classify.aux.breaker_cooldown_s",
     {"classify": {"aux": {"breaker_cooldown_s": 900}}}, 900),
    ("anchor_chain.primary", "anchor.primary",
     {"anchor": {"primary": "nous://m"}}, "nous://m"),
    ("anchor_chain.daily_cap_usd", "anchor.daily_cap_usd",
     {"anchor": {"daily_cap_usd": 5.5}}, 5.5),
    ("complexity.level", "anchor.complexity_level",
     {"anchor": {"complexity_level": 3}}, 3),
    ("persona_mode", "persona.mode", {"persona": {"mode": "none"}}, "none"),
    ("reflex_weights", "reflex.weights",
     {"reflex": {"weights": {"enabled": True}}}, {"enabled": True}),
    ("provenance_footer", "render.provenance_footer",
     {"render": {"provenance_footer": True}}, True),
    ("base_url", "providers.nous.base_url",
     {"providers": {"nous": {"base_url": "https://inference/v1"}}},
     "https://inference/v1"),
)


@pytest.mark.parametrize("old,new,section,expected", ALIAS_CASES)
def test_old_name_resolves_to_grouped_path(monkeypatch, _clean_telemetry,
                                           old, new, section, expected):
    assert schema.ALIASES.get(old) == new
    _use_section(monkeypatch, section)
    assert config_access.get(old) == expected
    assert not [r for r in _clean_telemetry if r[0] == "config_unknown_key"]


REVERSE_CASES = (
    # (grouped path, old-shaped section still deployed, value)
    ("operator.enabled", {"enabled": False}, False),
    ("render.max_chars", {"render_max_chars": 12}, 12),
    ("persona.mode", {"persona_mode": "full"}, "full"),
    ("anchor.primary", {"anchor_chain": {"primary": "openrouter://m"}},
     "openrouter://m"),
    ("classify.mode", {"classification": {"mode": "off"}}, "off"),
)


@pytest.mark.parametrize("new,section,expected", REVERSE_CASES)
def test_grouped_path_falls_back_to_deployed_old_name(monkeypatch,
                                                      _clean_telemetry,
                                                      new, section, expected):
    _use_section(monkeypatch, section)
    assert config_access.get(new) == expected


def test_grouped_path_wins_when_both_spellings_present(monkeypatch,
                                                       _clean_telemetry):
    _use_section(monkeypatch,
                 {"operator": {"enabled": False}, "enabled": True})
    assert config_access.get("operator.enabled") is False
    assert config_access.get("enabled") is False


def test_sub_block_alias_reads_legacy_block(monkeypatch, _clean_telemetry):
    _use_section(monkeypatch, {"classification": {"mode": "shadow"}})
    assert config_access.sub_block("classify") == {"mode": "shadow"}
    _use_section(monkeypatch, {"classify": {"mode": "route"},
                               "classification": {"mode": "shadow"}})
    assert config_access.sub_block("classify") == {"mode": "route"}


def test_alias_table_is_derived_from_legacy_of():
    # single source of truth: an alias exists only because the grouped key
    # recorded the old name.
    for old, new in schema.ALIASES.items():
        assert old != new
        assert schema.key(new) is not None, "alias target not declared: %s" % new
        assert old in schema.key(new).legacy_of.split(",")
    for k in schema.SCHEMA:
        for old in schema.legacy_paths(k.path):
            assert schema.ALIASES[old] == k.path
    assert len(schema.ALIASES) >= 25


# --- 3. unknown path emits telemetry --------------------------------------

def test_unknown_path_emits_telemetry(monkeypatch, _clean_telemetry):
    _use_section(monkeypatch, {})
    assert config_access.get("operator.no_such_key") is None
    assert [r[0] for r in _clean_telemetry][-1] == "config_key_unknown"


def test_declared_but_absent_key_emits_missing_row(monkeypatch,
                                                   _clean_telemetry):
    _use_section(monkeypatch, {})
    assert config_access.get("operator.dry_run") is False
    assert [r[0] for r in _clean_telemetry][-1] == "config_key_missing"


# --- invariants the declaration must keep ---------------------------------

def test_route_log_keys_are_code_owned():
    """Standardising the route-log keys into `paths` must NOT make them
    operator-writable (api/config_writer.py FORBIDDEN_KEYS)."""
    for path in ("paths.log_path", "paths.log_max_bytes", "paths.log_routes"):
        k = schema.key(path)
        assert k is not None, "%s not declared" % path
        assert k.control_writable is False, "%s must be code-owned" % path


def test_legacy_shaped_old_name_keeps_its_own_type(monkeypatch,
                                                 _clean_telemetry):
    """anchor_chain.primary is a DICT on the live nested shape while
    anchor.primary is a str — the old name must keep reading as it did."""
    _use_section(monkeypatch,
                 {"anchor_chain": {"primary": {"provider": "nous"}}})
    assert config_access.get("anchor_chain.primary") == {"provider": "nous"}


def test_no_duplicate_paths_declared():
    paths = [k.path for k in schema.SCHEMA]
    assert len(paths) == len(set(paths))
