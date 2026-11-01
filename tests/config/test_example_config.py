# tests/config/test_example_config.py — M23-02 guard: the example IS the promise.
#
# examples/config.example.yaml claims to carry every declared key at its
# default, with a type, a default and a one-line meaning for each. An example
# that drifts from the schema is worse than no example: it teaches the operator
# a key that does not exist, or hides one that does. So this file proves four
# things against core/schema.py and the REAL accessor, in BOTH directions:
#
#   1. nothing invented — every path in the example is a declared key, or an
#      intermediate container that exists purely to hold declared children;
#   2. nothing missing — every declared key appears in the example;
#   3. nothing mis-typed — every value type-checks against its declaration AND
#      the accessor returns that same value with zero telemetry rows (no
#      config_key_missing / config_key_type_mismatch / config_key_unknown);
#   4. nothing documented in the dark — every declared key line carries a
#      comment naming its default (and its type), and the file carries no real
#      fleet values (paths, hosts, keys) — placeholders only.
#
# Fail loud on drift in either direction: the assertion messages carry the full
# offending list, not the first failure.
import re
from pathlib import Path

import pytest
import yaml

from hermes_router.core import config_access, schema, telemetry

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = REPO_ROOT / "examples" / "config.example.yaml"

# Values that must never be committed in an example: fleet-local paths, the
# fleet's hosts, and anything shaped like a live credential.
FORBIDDEN_PATTERNS = (
    (re.compile(r"/opt/data"), "fleet-local path (/opt/data)"),
    (re.compile(r"\bws://"), "live websocket endpoint"),
    (re.compile(r"\b(sk|rk)-[A-Za-z0-9_\-]{8,}"), "credential-shaped token"),
    (re.compile(r"(?i)\b(vegas|gorizia|ovh|proxmox|falke)\b"), "fleet host/name"),
    (re.compile(r"\b\d{1,3}(\.\d{1,3}){3}\b"), "IP address"),
)

_TYPE_WORD = re.compile(r"\b(bool|int|float|str|list|dict)\b")

# Containers whose contents are model-KEYED tables, not schema paths: a dotted
# key inside them is a model id, not a path. Anywhere else a literal dotted key
# ("operator.dry_run: false") is a trap — the accessor walks NESTED blocks, so
# such a line is dead config that also fakes coverage of the nested key.
OPAQUE_CONTAINERS = frozenset({
    "pricing", "anchor.pricing", "anchor.models",
    "anchor_chain.pricing", "anchor_chain.models", "reflex.weights",
})


def _type_name(t):
    if isinstance(t, tuple):
        return "|".join(x.__name__ for x in t)
    return getattr(t, "__name__", str(t))


def _load_section():
    raw = EXAMPLE.read_text(encoding="utf-8")
    doc = yaml.safe_load(raw)
    return raw, doc


def _flatten(node, prefix=""):
    """path -> value for every node (containers included)."""
    out = {}
    for k, v in node.items():
        path = f"{prefix}{k}"
        out[path] = v
        if isinstance(v, dict):
            out.update(_flatten(v, path + "."))
    return out


def _raw_key_names(node, prefix=""):
    """Dotted paths built from RAW key names — a key that literally contains a
    dot is reported with that dot intact (e.g. "operator.dry_run"), which is the
    whole point: such a line is dead config."""
    out = []
    for k, v in node.items():
        path = f"{prefix}{k}"
        if "." in str(k):
            out.append(path)
        if isinstance(v, dict):
            out.extend(_raw_key_names(v, path + "."))
    return out


def _key_lines(raw):
    """[(dotted_path, comment_or_'' )] for every `key:` line in the YAML."""
    stack = []  # [(indent, key)]
    found = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("- "):
            continue
        m = re.match(r"^(\s*)([A-Za-z0-9_.\-]+):(\s|$)", line)
        if not m:
            continue
        indent = len(m.group(1))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, m.group(2)))
        path = ".".join(k for _, k in stack)
        comment = stripped.split("#", 1)[1].strip() if "#" in stripped else ""
        found.append((path, comment))
    return found


@pytest.fixture(autouse=True)
def _clean_telemetry(monkeypatch):
    rows = []
    monkeypatch.setattr(telemetry, "log_route",
                        lambda event, **f: rows.append((event, dict(f))))
    monkeypatch.setattr(config_access, "_MISSING_KEY_SEEN", set())
    config_access.reset_cache()
    return rows


def test_example_exists_and_is_a_single_router_section():
    raw, doc = _load_section()
    assert EXAMPLE.is_file()
    assert isinstance(doc, dict), "example must be a YAML mapping"
    assert set(doc) == {"hermes_router"}, (
        "the example must be exactly one `hermes_router:` section, "
        f"found top-level keys: {sorted(doc)}")
    assert isinstance(doc["hermes_router"], dict)
    assert "hermes_router:" in raw.splitlines()[0] or "hermes_router:" in raw


def test_example_invents_no_keys():
    _, doc = _load_section()
    section = doc["hermes_router"]
    flat = _flatten(section)
    declared = {k.path for k in schema.SCHEMA}
    unknown = []
    for path, value in flat.items():
        if path in declared:
            continue
        # an undeclared path is tolerated ONLY as a pure intermediate container:
        # a mapping that holds declared children (anchor, budgets.consult, ...).
        holds_declared = any(d.startswith(path + ".") for d in declared)
        if not (isinstance(value, dict) and value and holds_declared):
            unknown.append(path)
    assert not unknown, (
        "example declares paths core/schema.py does not: "
        + ", ".join(sorted(unknown)))

    dotted = []
    for path in _raw_key_names(section):
        parent = path.rpartition(".")[0]
        if parent not in OPAQUE_CONTAINERS:
            dotted.append(path)
    assert not dotted, (
        "literal dotted keys in the example (dead config — the accessor walks "
        "nested blocks, and a dotted line fakes coverage of the real key): "
        + ", ".join(sorted(dotted)))


def test_example_covers_every_declared_key():
    _, doc = _load_section()
    flat = _flatten(doc["hermes_router"])
    missing = sorted({k.path for k in schema.SCHEMA} - set(flat))
    assert not missing, (
        f"{len(missing)} declared key(s) absent from the example: "
        + ", ".join(missing))


def test_every_value_type_checks_and_reads_back_through_the_accessor(
        monkeypatch, _clean_telemetry):
    _, doc = _load_section()
    section = doc["hermes_router"]
    flat = _flatten(section)
    monkeypatch.setattr(config_access, "router_section", lambda: section)

    wrong = []
    mismatched = []
    for path, value in sorted(flat.items()):
        k = schema.key(path)
        if k is None:
            continue  # intermediate container: covered by the invention test
        if value is None:
            if not k.nullable:
                wrong.append(f"{path}: null but not nullable")
            continue
        if not config_access._type_ok(value, k.type):
            wrong.append(f"{path}: {value!r} is not {_type_name(k.type)}")
            continue
        got = config_access.get(path)
        # A pre-M23 spelling that resolves through ALIASES reads the GROUPED
        # key's value, not its own line — that is the precedence the example
        # documents ("grouped key wins"), so assert the resolved value, not the
        # shadowed one. A non-aliased key must read back its own value.
        target = schema.canonical_path(path)
        if target != path:
            if target not in flat:
                mismatched.append(f"{path}: aliases to {target}, absent "
                                  "from the example")
                continue
            expect = flat[target]
        else:
            expect = value
        if got != expect:
            mismatched.append(f"{path}: accessor -> {got!r}, example -> "
                              f"{expect!r}")

    assert not wrong, ("example values do not type-check against "
                       "core/schema.py: " + "; ".join(wrong))
    assert not mismatched, ("the real accessor does not return the example's "
                            "value: " + "; ".join(mismatched))
    assert not _clean_telemetry, (
        "reading the example through config_access emitted telemetry "
        "(the example is drifting from the schema): "
        + "; ".join(f"{e} {f}" for e, f in _clean_telemetry))


def test_every_declared_key_line_states_type_and_default():
    raw, _ = _load_section()
    lines = dict(_key_lines(raw))
    undocumented = []
    for k in schema.SCHEMA:
        comment = lines.get(k.path)
        if comment is None:
            continue  # presence is proven elsewhere; a block container may sit inline
        if not comment:
            undocumented.append(f"{k.path}: no comment")
            continue
        if "default" not in comment:
            undocumented.append(f"{k.path}: comment does not state its default")
        if not (_TYPE_WORD.search(comment) or "|" in comment):
            undocumented.append(f"{k.path}: comment does not state its type")
    assert not undocumented, (
        "example key lines are not self-documenting: "
        + "; ".join(undocumented))


def test_example_carries_no_real_fleet_values():
    raw, _ = _load_section()
    hits = []
    for pattern, label in FORBIDDEN_PATTERNS:
        for m in pattern.finditer(raw):
            line_no = raw[:m.start()].count("\n") + 1
            hits.append(f"line {line_no}: {label} -> {m.group(0)!r}")
    assert not hits, ("example carries real fleet values, use placeholders: "
                      + "; ".join(hits))
