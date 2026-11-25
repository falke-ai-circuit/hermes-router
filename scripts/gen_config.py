#!/usr/bin/env python3
"""Canonical router config generator — single source of truth is ``core/schema.py``.

Why this exists
---------------
A hand-written config example rots: the schema gains a key, the example doesn't,
and a fresh install silently runs on hidden defaults. So the example is
GENERATED from the schema and pinned by a test
(``tests/config/test_canonical_config_example.py``) that fails when the two
drift. Any Hermes install can therefore use ``config.example.yaml`` as the
canonical starting point, and the file is guaranteed to define every parameter
the running plugin understands.

Usage
-----
    python3 scripts/gen_config.py --emit-example
        Rewrite config.example.yaml from the schema (commit the result).

    python3 scripts/gen_config.py --check
        Exit 1 if config.example.yaml is out of sync with the schema (CI/test).

    python3 scripts/gen_config.py --apply /opt/data/.hermes --overrides ov.yaml
        Merge the generated block into <profile>/config.yaml (timestamped
        backup first). Optional YAML file supplies environment-specific values
        (log paths, render chain, provider keys) without editing the generator.

The generated block always defines EVERY schema key. Installation-specific
values belong in the overrides file, never in the example, so the example stays
portable across Hermes profiles and versions.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE = os.path.join(REPO, "config.example.yaml")

#: The single config section the plugin reads (core/config_access.py —
#: ``_SECTION_KEYS = ("hermes_router",)``). The legacy ``router`` section and
#: its key-merge fallback were DELETED fleet-wide, so writing to any other key
#: produces a config the plugin never reads — indistinguishable from no config.
SECTION = "hermes_router"

HEADER = """\
# hermes-router — canonical configuration example (GENERATED, do not hand-edit)
#
# Source of truth: core/schema.py ({} parameters).
# Regenerate:      python3 scripts/gen_config.py --emit-example
# Verify in sync:  python3 scripts/gen_config.py --check
#
# The plugin reads ONE section: `hermes_router` (core/config_access.py).
#
# Copy the `router:` block into a profile's config.yaml, or apply it directly:
#   python3 scripts/gen_config.py --apply <profile_home>
# Environment-specific values (log paths, render/anchor chains, provider keys)
# do not belong here — pass them with --overrides so this file stays portable.
"""

# Values that are environment-specific are emitted as empty/neutral placeholders
# so the example never pretends to know another install's paths or providers.
NEUTRAL = {
    "log_path": "",          # per-install: <profile>/logs/hermes-router.log
    "state_dir": "",         # per-install
    "render.chain": [],      # per-install: provider://model
    "anchor.chain": [],
}


def load_schema():
    # The repo root IS the package directory (hermes_router/), so the import
    # path is its PARENT. REPO alone makes `from hermes_router...` fail.
    sys.path.insert(0, os.path.dirname(REPO))
    sys.path.insert(0, REPO)
    from hermes_router.core import schema as S
    return S


def nest(path, value):
    out = value
    for part in reversed(path.split(".")):
        out = {part: out}
    return out


def deep_merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def build_block(S, overrides=None):
    """Full router block: every schema key at its declared default.

    Order-independent: dotted (leaf) paths are written first, then container
    entries only fill gaps. Otherwise a schema entry like ``detection`` with a
    ``{}`` default can land last and clobber ``detection.enabled``.
    """
    block = {}
    for k in S.SCHEMA:
        if "." not in k.path:
            continue
        default = NEUTRAL.get(k.path, getattr(k, "default", None))
        deep_merge(block, nest(k.path, default))
    for k in S.SCHEMA:
        if "." in k.path or k.path in block:
            continue
        block[k.path] = NEUTRAL.get(k.path, getattr(k, "default", None))
    if overrides:
        for path, val in overrides.items():
            deep_merge(block, nest(path, val))
    return block


def yaml_safe():
    try:
        import yaml
    except ImportError:  # pragma: no cover
        sys.exit("PyYAML required: pip install pyyaml")
    return yaml


def flatten(d, prefix=""):
    """Flatten nested dict to {dotted.path: value} for set comparison."""
    out = {}
    for k, v in (d or {}).items():
        path = f"{prefix}{k}"
        if isinstance(v, dict) and v:
            out.update(flatten(v, path + "."))
        else:
            out[path] = v
    return out


def emit_example(S):
    yaml = yaml_safe()
    block = build_block(S)
    body = yaml.safe_dump({SECTION: block}, sort_keys=False,
                          default_flow_style=False, allow_unicode=True)
    text = HEADER.format(len(S.SCHEMA)) + "\n" + body
    with open(EXAMPLE, "w") as f:
        f.write(text)
    print(f"  wrote {EXAMPLE} ({len(S.SCHEMA)} keys, section {SECTION!r})")


def leaf_paths(S):
    """Schema paths that carry a value — containers excluded.

    A container is a no-dot entry (``budgets``) that has children
    (``budgets.consult.max_tokens``). flatten() emits only leaf values, so the
    comparison must be leaf-to-leaf or it reports every group as missing.
    """
    all_paths = {k.path for k in S.SCHEMA}
    containers = {
        p for p in all_paths
        if any(q.startswith(p + ".") for q in all_paths)
    }
    return all_paths - containers


def check(S):
    yaml = yaml_safe()
    if not os.path.exists(EXAMPLE):
        print(f"  MISSING: {EXAMPLE}")
        return 1
    with open(EXAMPLE) as f:
        doc = yaml.safe_load(f) or {}
    have = set(flatten(doc.get(SECTION) or {}))

    want = leaf_paths(S)
    missing = sorted(want - have)
    extra = sorted(have - want)
    if missing or extra:
        print(f"  OUT OF SYNC — missing {len(missing)}, extra {len(extra)}")
        for p in missing[:10]:
            print(f"    missing: {p}")
        for p in extra[:10]:
            print(f"    extra:   {p}")
        return 1
    print(f"  in sync: {len(want)} keys (section {SECTION!r})")
    return 0


def merge_preserving(existing, generated):
    """Fill gaps from the schema WITHOUT overwriting anything already set.

    Existing leaves always win: a profile's hand-tuned render chain, key_file
    paths, caps and pricing are operational decisions, not defaults to correct.
    Only keys the profile is missing get added, which is exactly what closes a
    schema-violation count without changing behaviour.
    """
    added = 0
    for k, v in generated.items():
        if k not in existing:
            existing[k] = v
            added += 1
        elif isinstance(existing[k], dict) and isinstance(v, dict):
            added += merge_preserving(existing[k], v)
    return added


def apply(S, profile_home, overrides_file):
    yaml = yaml_safe()
    cfg_path = os.path.join(profile_home, "config.yaml")
    if not os.path.exists(cfg_path):
        sys.exit(f"  no config.yaml at {cfg_path}")
    overrides = {}
    if overrides_file:
        with open(overrides_file) as f:
            doc = yaml.safe_load(f) or {}
        overrides = doc.get(SECTION) or doc.get("router") or {}
    generated = build_block(S, overrides)
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f) or {}
    existing = cfg.get(SECTION)
    backup = cfg_path + ".bak-routergen-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(cfg_path, backup)
    if isinstance(existing, dict) and existing:
        before = len(flatten(existing))
        added = merge_preserving(existing, generated)
        cfg[SECTION] = existing
        print(f"  mode:    MERGE (preserved {before} existing key(s), added {added})")
    else:
        cfg[SECTION] = generated
        print(f"  mode:    CREATE (no existing section)")
    with open(cfg_path, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, default_flow_style=False,
                       allow_unicode=True)
    print(f"  backup:  {backup}")
    print(f"  applied: {cfg_path} (section {SECTION!r}, {len(S.SCHEMA)} schema keys, "
          f"{len(overrides)} override(s))")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--emit-example", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--apply", metavar="PROFILE_HOME")
    ap.add_argument("--overrides", metavar="YAML_FILE")
    a = ap.parse_args()
    S = load_schema()
    if a.emit_example:
        emit_example(S)
    if a.check:
        return check(S)
    if a.apply:
        apply(S, a.apply, a.overrides)
    if not (a.emit_example or a.check or a.apply):
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
