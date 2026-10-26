"""R63m — the fork sources could not see a fork in ordinary repo work.

Measured on the reviewer's clean battery: the midturn seam ran on all seven treated
cells and refused every one of them (44 suppression events, 0 consults). The router
log blamed the envelope, but the path accumulator was handing the detectors garbage:

    raw tool text                       extracted "path"
    ------------------------------      --------------------------
    "\\nREADME.md" (JSON-escaped)        "nREADME.md"      <- escape letter glued on
    "--- a//opt/.../repo/lib/x.py"      "a//opt/.../x.py" <- diff prefix kept
    "/opt/.../repo/lib/x.py" + "lib/x.py"  two entries for one file

With those poisoned inputs `_dir()`, `_stem()` and every hint regex miss, so
`found=none` was an extraction failure wearing an "no fork here" verdict.

These tests pin the normalisation, not the detectors' policy: a file has exactly one
canonical form and it is repo-relative.
"""
from __future__ import annotations

from hermes_router import fork_sources as fs


# --- the three measured defects -------------------------------------------------


def test_json_escape_letters_are_not_glued_onto_paths():
    """`\\nREADME.md` must yield "README.md", never "nREADME.md"."""
    got = fs._paths(r"\nREADME.md\n__init__.py\n" + "main.py")
    assert got == ["README.md", "__init__.py", "main.py"], got
    assert not any(p.startswith(("n", "t", "r")) and "." in p for p in got)


def test_diff_prefixes_are_stripped():
    """A unified diff's a/ and b/ prefix is not part of the path."""
    text = "--- a//opt/data/x/repo/lib/parse_utils.py\n+++ b//opt/data/x/repo/lib/parse_utils.py"
    got = fs._paths(text)
    assert got == ["lib/parse_utils.py"], got


def test_absolute_repo_paths_collapse_to_one_relative_entry():
    """The same file surfaced as an absolute path and a relative one is ONE file.

    Two entries for one file is how a sibling pair gets counted twice instead of
    being seen as a pair, and how the 120-entry bound is spent on duplicates.
    """
    text = ("/opt/data/tmp/lane_cells/clean1/H2/repo/lib/parse_utils_old.py\n"
            "lib/parse_utils_old.py")
    assert fs._paths(text) == ["lib/parse_utils_old.py"]


def test_normalization_survives_junk_input():
    for junk in ("", None, "no paths here", "\x00\x01", "a" * 5000):
        assert isinstance(fs._paths(junk), list)


# --- the pairing policy the garbage was blocking --------------------------------


def _accumulate(*texts):
    store = {}
    for t in texts:
        fs.note_seen(store, t)
    return store


def test_duplicate_module_repo_yields_the_real_rival_pair():
    """The H2 shape: main.py and its test. Before normalisation this repo produced
    `found=none`; the pair that fires must be the two files the task actually
    forks over, not a README."""
    store = _accumulate(
        r"\nREADME.md\n__init__.py\nmain.py\nparse_utils.py\n"
        r"parse_utils_old.py\ntest_main.py",
        "tests/test_main.py")
    hit = fs.rival_targets(store, "")
    assert hit, "no fork recognised in a repo that contains two rival targets"
    assert hit["source"] in ("test_vs_source", "sibling_files"), hit
    joined = " ".join(hit["options"])
    assert "README.md" not in joined, "README is documentation, not a source route"


def test_test_vs_source_never_names_a_doc_as_the_source():
    """README.md is not code. Pairing a test with the README names a fork nobody
    faces, and it was the first pairing the live session produced."""
    store = _accumulate("README.md tests/test_main.py main.py")
    hit = fs.rival_targets(store, "")
    assert hit["source"] == "test_vs_source", hit
    assert "README.md" not in " ".join(hit["options"]), hit
    assert "main.py" in hit["options"]


def test_test_vs_source_prefers_the_stem_related_partner():
    store = _accumulate("cfg/settings.py core/engine.py tests/test_engine.py")
    hit = fs.rival_targets(store, "")
    assert hit["source"] == "test_vs_source", hit
    assert set(hit["options"]) == {"tests/test_engine.py", "core/engine.py"}, hit


def test_same_stem_different_extension_is_its_own_family():
    """limits.py vs limits.yaml is not a version/backup pair; its why-text must not
    claim "only one is live"."""
    store = _accumulate("limits.py limits.yaml")
    hit = fs.rival_targets(store, "")
    assert hit and hit["family"].startswith("stem_pair:"), hit
    assert "version" not in hit["why"], hit["why"]


def test_version_sibling_still_reads_as_a_version_pair():
    store = _accumulate("lib/parse_utils.py lib/parse_utils_old.py")
    hit = fs.rival_targets(store, "")
    assert hit and hit["family"].startswith("sibling:"), hit


def test_one_fire_per_family_per_session():
    store = _accumulate("main.py tests/test_main.py")
    first = fs.rival_targets(store, "")
    assert first
    assert fs.rival_targets(store, "") is None, "the same family fired twice"


def test_render_options_labels_are_observable_commands():
    store = _accumulate("main.py tests/test_main.py")
    hit = fs.rival_targets(store, "")
    labels = fs.render_options(hit)
    assert labels == ["edit main.py", "edit tests/test_main.py"], labels
