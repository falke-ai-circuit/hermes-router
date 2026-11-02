# tests/config/test_m25_01_no_hardcoded_endpoints.py — M25-01 GATE 1.
#
# RULE: no hardcoded endpoint, port or absolute fleet path in shipping code.
#   shipping code = repo tree minus tests/ reviews/ scripts/ tools/ benchmarks/
#
# Mechanism
#   * every shipping .py module is AST-walked; every str Constant is tested
#   * every shipping .py module is tokenized for # comments (a comment can
#     smuggle a fleet path just as well as a string — and the M24-06 baseline
#     already contains comment-only lines, so a pure-AST gate would call them
#     stale and rot the list)
#   * shipping non-.py files (.yaml/.yml/.toml/.json/.sh/.cfg/.ini) are scanned
#     line-wise, because the baseline covers manifest + example YAML
#   * hits are keyed "<relpath>:<lineno>"; a hit NOT in
#     tests/config/allowed_literals.json fails with file:line: literal
#   * an allowlist key with no matching hit is ALSO a failure — the list must
#     shrink as literals die, never rot
#
# DYNAMIC PATHS — what this gate CAN and CANNOT see (stated, not silent)
#   The M24 reads compose config keys at runtime: f"providers.{name}.base_url".
#   No static matcher can resolve that to a value, so this gate does not
#   pretend to check such values. Instead it *detects the joins themselves*:
#   every ast.JoinedStr whose literal segments name a config path is inventoried
#   in DYNAMIC_CONFIG_REFS below, and the inventory is pinned exactly. A new
#   dynamic config-path join is a red that says "review this, then extend the
#   pin" — so the blind spot cannot grow quietly.
#   Literal segments INSIDE an f-string are ordinary Constants and are still
#   caught (ast.walk descends into JoinedStr): an f-string cannot hide a URL.
#   Out of scope by construction (stated limitation): values that arrive from
#   the environment or a config file at runtime, and keys assembled in a loop
#   with no literal prefix at all. Those are covered by M24's config reads, not
#   by this gate.
import ast
import io
import json
import os
import re
import tokenize

import pytest

PKG_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))  # tests/config/ -> repo root
ALLOWLIST = os.path.join(PKG_ROOT, "tests", "config", "allowed_literals.json")

EXCLUDE_DIRS = {
    "tests", "reviews", "scripts", "tools", "benchmarks",
    "__pycache__", ".git", "node_modules", ".pytest_cache", ".mypy_cache",
}
TEXT_EXT = {".yaml", ".yml", ".toml", ".json", ".sh", ".cfg", ".ini"}

# Category matchers. Deliberately permissive (search, not ^-anchored) so a
# literal embedded mid-sentence is still a hit: a URL in prose is still a URL.
# Scanned PER LINE, never across a multi-line string, so the reported lineno is
# the line that actually carries the literal and "$" means end-of-line.
# URL needs a real host character after "//": "https://" alone is scheme
# normalisation and "https://<your-provider>/..." is a documented placeholder —
# neither is a dialable endpoint. PORT needs a real host before the colon and
# its "host" must not be a source citation ("session_store.py:52" is a line
# reference, not a port), so file extensions are excluded from the TLD slot.
FILE_TLD = r"(?!py\b|pyc\b|md\b|json\b|ya?ml\b|db\b|log\b|txt\b|sh\b|toml\b|ini\b|cfg\b)"
URL_RE = re.compile(r"https?://[A-Za-z0-9\[]")
PORT_RE = re.compile(
    r"(?:localhost|127\.0\.0\.1"
    r"|[A-Za-z0-9-]+\." + FILE_TLD + r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*)"
    r":\d{2,5}(?=/|$|[^0-9A-Za-z])"
)
PATH_RE = re.compile(r"(?:^|[\s\"'`(=,;])(/(?:opt|home|srv|var)/)")
PROVIDER_RE = re.compile(
    r"\b(?:openrouter\.ai|api\.venice\.ai|venice\.ai"
    r"|inference-api\.nousresearch\.com|api\.typesafe\.ai"
    r"|api\.openai\.com|api\.anthropic\.com|platform\.minimax\.io"
    r"|generativelanguage\.googleapis\.com)\b"
)

CATEGORIES = {"url", "host_port", "abs_path", "provider_host"}

# f-string joins whose literal segments name a config path. Pinned exactly:
# these are the gate's visible dynamic surface at M25-01. Extend the pin only
# after reviewing the new join — that review is the whole point of the pin.
DYNAMIC_CONFIG_REFS = {
    "anchor_chain.py:124",          # providers.{name}.base_url
    "core/config_access.py:346",    # providers.{name}.{field}
    "core/config_access.py:382",    # providers.{name}.key_env
    "core/config_access.py:425",    # providers.{name}.hosts
}
# An f-string segment is a config-path join when its static text looks like a
# dotted key chain that terminates in a config field name.
DYNAMIC_RE = re.compile(
    r"(?:^|\.)(?:providers|paths|hermes_router|routing|models|lanes|config)\."
    r"(?:[A-Za-z0-9_.]*\x00"          # tail segment is itself dynamic
    r"|[A-Za-z0-9_.]*(?:base_url|endpoint|hosts|host|port|key_env|url"
    r"|api_base|path|log_path|log_routes|log_max_bytes|model)\b)"
)


def _categories(text):
    cats = set()
    if URL_RE.search(text):
        cats.add("url")
    if PORT_RE.search(text):
        cats.add("host_port")
    if PATH_RE.search(text):
        cats.add("abs_path")
    if PROVIDER_RE.search(text):
        cats.add("provider_host")
    return cats


def shipping_files(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
        for fn in sorted(filenames):
            if fn.endswith(".py") or os.path.splitext(fn)[1] in TEXT_EXT:
                out.append(os.path.relpath(os.path.join(dirpath, fn), root))
    return sorted(out)


def _scan_py(path, rel, hits):
    src = open(path, encoding="utf-8", errors="replace").read()
    tree = ast.parse(src, filename=rel)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            # A multi-line constant (docstring) must be attributed line by line:
            # node.lineno is where the string STARTS, and matching across the
            # whole value would flag line 1 for a literal 40 lines down.
            for offset, line in enumerate(node.value.splitlines() or [""]):
                cats = _categories(line)
                if cats:
                    hits.setdefault("%s:%d" % (rel, node.lineno + offset),
                                    set()).update(cats)
    # comments are invisible to AST; tokenize so a fleet path in prose is a hit
    try:
        toks = tokenize.generate_tokens(io.StringIO(src).readline)
        for tok in toks:
            if tok.type == tokenize.COMMENT:
                cats = _categories(tok.string)
                if cats:
                    hits.setdefault("%s:%d" % (rel, tok.start[0]), set()).update(cats)
    except (tokenize.TokenError, IndentationError):
        pass
    return tree


def _scan_text(path, rel, hits):
    with open(path, encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh, 1):
            cats = _categories(line)
            if cats:
                hits.setdefault("%s:%d" % (rel, i), set()).update(cats)


def dynamic_config_refs(root):
    """f-string joins whose static segments name a config path."""
    refs = {}
    for rel in shipping_files(root):
        if not rel.endswith(".py"):
            continue
        src = open(os.path.join(root, rel), encoding="utf-8", errors="replace").read()
        for node in ast.walk(ast.parse(src, filename=rel)):
            if not isinstance(node, ast.JoinedStr):
                continue
            # substitute "\x00" for the dynamic segment so the static skeleton
            # reads "providers.\x00.base_url" and stays matchable
            literal = "".join(
                v.value if isinstance(v, ast.Constant) and isinstance(v.value, str)
                else "\x00"
                for v in node.values
            )
            if DYNAMIC_RE.search(literal):
                refs["%s:%d" % (rel, node.lineno)] = literal.replace("\x00", "{}")
    return refs


def scan(root):
    hits = {}
    for rel in shipping_files(root):
        path = os.path.join(root, rel)
        if rel.endswith(".py"):
            _scan_py(path, rel, hits)
        else:
            _scan_text(path, rel, hits)
    return {k: sorted(v) for k, v in hits.items()}


def _load_allowlist():
    with open(ALLOWLIST, encoding="utf-8") as fh:
        return json.load(fh)["allowlist"]


# --------------------------------------------------------------------------
# the gate
# --------------------------------------------------------------------------
def test_no_unallowed_hardcoded_literal():
    allow = _load_allowlist()
    hits = scan(PKG_ROOT)
    bad = sorted(set(hits) - set(allow))
    assert not bad, (
        "M25-01 GATE 1: hardcoded endpoint/port/absolute fleet path in shipping "
        "code:\n" + "\n".join(
            "  %s: %r [%s]" % (k, _sample(k), ",".join(hits[k])) for k in bad
        ) + "\nRead it from config (M24) or obtain a gate exception; the "
            "allowlist may only shrink."
    )


def test_allowlist_has_no_stale_entries():
    allow = _load_allowlist()
    hits = scan(PKG_ROOT)
    stale = sorted(set(allow) - set(hits))
    assert not stale, (
        "M25-01 GATE 1: stale allowlist entries — the literal is gone, so the "
        "entry must go too:\n" + "\n".join("  %s" % k for k in stale) +
        "\nDelete these keys from tests/config/allowed_literals.json (and lower "
        "_meta.count). A list that never shrinks is a list nobody trusts."
    )


def test_allowlist_justification_covers_detected_categories():
    allow = _load_allowlist()
    hits = scan(PKG_ROOT)
    wrong = []
    for key, why in allow.items():
        declared = set(why.split(":", 1)[0].split("+")) if ":" in why else set()
        extra = set(hits.get(key, [])) - declared
        if extra:
            wrong.append("%s: detected %s not declared in %r" %
                         (key, sorted(extra), why))
    assert not wrong, "M25-01 GATE 1: allowlist justification drift:\n" + "\n".join(
        "  " + w for w in sorted(wrong))


# --------------------------------------------------------------------------
# dynamic surface — the part static matching cannot resolve
# --------------------------------------------------------------------------
def test_dynamic_config_joins_are_inventoried():
    found = dynamic_config_refs(PKG_ROOT)
    assert found, (
        "M25-01 GATE 1: no dynamic config-path joins detected — the detector is "
        "blind, or the M24 reads were rewritten. Either way the reported blind "
        "spot is wrong, so re-derive it before trusting this gate."
    )
    unexpected = sorted(set(found) - DYNAMIC_CONFIG_REFS)
    missing = sorted(DYNAMIC_CONFIG_REFS - set(found))
    assert not unexpected, (
        "M25-01 GATE 1: new dynamic config-path join(s) appeared — review them, "
        "then extend DYNAMIC_CONFIG_REFS:\n" + "\n".join(
            "  %s: %r" % (k, found[k]) for k in unexpected)
    )
    assert not missing, (
        "M25-01 GATE 1: pinned dynamic refs vanished — shrink the pin:\n" +
        "\n".join("  %s" % k for k in missing)
    )


def test_dynamic_join_literal_segments_carry_no_endpoint():
    """Literal text inside an f-string is still a Constant — but prove it."""
    allow = _load_allowlist()
    hits = scan(PKG_ROOT)
    offenders = []
    for key, literal in dynamic_config_refs(PKG_ROOT).items():
        stripped = literal.replace(".", "").replace("{", "").replace("}", "")
        cats = _categories(literal)
        if cats and key not in allow:
            offenders.append("%s: %r [%s]" % (key, literal, sorted(cats)))
    assert not offenders, (
        "M25-01 GATE 1: f-string literal segments contain a hardcoded "
        "endpoint/path:\n" + "\n".join("  " + o for o in offenders)
    )


# --------------------------------------------------------------------------
# self-check — a gate that cannot fail on planted litter is theatre
# --------------------------------------------------------------------------
@pytest.fixture()
def litter(tmp_path):
    (tmp_path / "pkg" / "sub").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "pkg" / "litter.py").write_text(
        'URL = "https://api.venice.ai/api/v1/chat"\n'
        'PORT = "127.0.0.1:8680"\n'
        'PATH = "/opt/data/profiles/coder/config.yaml"\n'
        'HOST = "openrouter.ai"\n'
        'NAME = "benign"\n'
        '# a fleet path in prose: /srv/fleet/db\n'
        'DYN = f"providers.{NAME}.base_url"\n'
        'LIT = f"providers.{NAME}.base_url https://leak.example/v1"\n',
        encoding="utf-8",
    )
    (tmp_path / "tests" / "ignored.py").write_text(
        'X = "https://api.openai.com/v1"\n', encoding="utf-8")
    (tmp_path / "scripts" / "tool.sh").write_text(
        'curl http://127.0.0.1:9700/\n', encoding="utf-8")
    (tmp_path / "pkg" / "sub" / "data.yaml").write_text(
        'host: "api.typesafe.ai:443"\n', encoding="utf-8")
    return tmp_path


def test_selfcheck_scanner_sees_every_category(litter):
    hits = scan(str(litter))
    assert "pkg/litter.py:1" in hits and "url" in hits["pkg/litter.py:1"]
    assert "host_port" in hits["pkg/litter.py:2"]
    assert "abs_path" in hits["pkg/litter.py:3"]
    assert "provider_host" in hits["pkg/litter.py:4"]
    assert "pkg/litter.py:6" in hits, "comment-only fleet path must be caught"
    assert "pkg/sub/data.yaml:1" in hits, "shipping YAML must be scanned"
    assert not any(k.startswith(("tests/", "scripts/")) for k in hits), \
        "excluded trees must not produce hits"
    assert not any(k.startswith("pkg/litter.py:5") for k in hits), \
        "benign literal must not be flagged"


def test_selfcheck_fstring_join_detected_and_literal_leak_caught(litter):
    refs = dynamic_config_refs(str(litter))
    assert "pkg/litter.py:7" in refs, "config-path f-string join must be inventoried"
    hits = scan(str(litter))
    assert "url" in hits["pkg/litter.py:8"], \
        "URL inside an f-string literal segment must still be a hit"


def test_selfcheck_stale_entry_shape(litter):
    hits = scan(str(litter))
    allow = {"pkg/litter.py:1": "url: ok", "pkg/litter.py:99": "url: gone"}
    assert sorted(set(allow) - set(hits)) == ["pkg/litter.py:99"]
    assert sorted(set(hits) - set(allow)) == sorted(
        k for k in hits if k != "pkg/litter.py:1"
    )


def _sample(key):
    rel, _, line = key.rpartition(":")
    try:
        text = open(os.path.join(PKG_ROOT, rel), encoding="utf-8",
                    errors="replace").read().splitlines()[int(line) - 1]
    except (OSError, IndexError, ValueError):
        return "<unreadable>"
    return text.strip()
