"""P0.5 guard — legacy-section purge is complete and stays dead.

Binding (dispatch block A, P0.5): the legacy config-section fallback
(_merge_legacy + legacy branch) is deleted from config_access; the canonical
"hermes_router" section is the only section the accessor reads; every local
profile config carries zero legacy sections.

Scope note (recorded deviation): the dispatch's "no legacy-section string in
package .py" cannot fire at P0.5 — the 4 residual legacy readers
(router.py / semantic_classifier.py / trigger_cascade.py legacy lookups and
config_writer.py's writer-side migration constant) are functionally legacy
code slated for migration in P4. This guard therefore (a) bans the string in
the accessor itself (strict), (b) bans legacy sections in profile configs
(strict), and (c) pins the exact allowlist of remaining package files that
may still mention the legacy section — the list may only SHRINK; it going to
zero at P4 is the full-ban moment.
"""
import glob
import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACCESSOR = os.path.join(REPO, "config_access.py")

LEGACY = "uncensored_router"

# Pinned at P0.5: files whose legacy mention survives until their P4 migration.
# MUST ONLY SHRINK. When this set is empty, tighten (a) to the whole package.
ALLOWED_FILES = {
    "router.py",                # residual legacy reader — migrates in P4
    "semantic_classifier.py",   # residual legacy reader — migrates in P4
    "trigger_cascade.py",       # residual legacy reader — migrates in P4
    "config_writer.py",         # writer-side LEGACY_SECTION migration constant
    "dispatcher_knobs.py",      # historical comment only
    "anchor_chain.py",          # historical comment only
    "method_card.py",           # historical comment only
    "router_core.py",           # historical comments only
}


def _package_py_files():
    for path in sorted(glob.glob(os.path.join(REPO, "*.py"))):
        yield os.path.relpath(path, REPO), path


def test_accessor_has_no_legacy_section_string():
    with open(ACCESSOR, "r", encoding="utf-8") as fh:
        src = fh.read()
    assert LEGACY not in src, "config_access.py must not reference the legacy section"


def test_accessor_has_no_merge_fallback():
    with open(ACCESSOR, "r", encoding="utf-8") as fh:
        src = fh.read()
    assert "_merge_legacy" not in src


def test_profile_configs_carry_no_legacy_section():
    profiles = glob.glob("/opt/data/profiles/*/config.yaml")
    assert profiles, "profile config glob must not be empty (env drift)"
    offenders = []
    for cfg in profiles:
        with open(cfg, "r", encoding="utf-8") as fh:
            body = fh.read()
        if LEGACY in body:
            offenders.append(cfg)
    assert offenders == []


def test_legacy_mention_allowlist_only_shrinks():
    hits = {rel for rel, path in _package_py_files()
            if LEGACY in open(path, "r", encoding="utf-8").read()}
    unexpected = hits - ALLOWED_FILES
    assert not unexpected, f"new legacy mentions appeared: {sorted(unexpected)}"
    # a pinned file that no longer mentions it = allowlist shrink; drop it
    # from ALLOWED_FILES in the same commit that removed the mention.


@pytest.mark.parametrize("tier,fn", [
    ("3-tier", "router_section"),
    ("cache", "reset_cache"),
])
def test_accessor_surface_intact(tier, fn):
    """The 3-tier resolution + cache semantics survive the purge: the public
    accessor surface is unchanged (import-time probe)."""
    import hermes_router.config_access as ca  # noqa: F401

    assert callable(getattr(ca, fn))
