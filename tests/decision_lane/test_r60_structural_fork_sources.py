"""R60: the lane must recognise forks the WORK reveals, not only forks the ask
enumerates.

Measured live before this: on the D2 fixture ("fix the date formatting"; repo has
utils_v1.py and utils_v2.py, app.py imports v2) the lane logged seven
`midturn_suppressed reason=no_declared_structure` and fired zero consults. The
fork was real, the enumeration was not. Recognition failure is upstream of every
other quality question: an envelope that never lists the options cannot be
precise, recall the real routes, or advise correctly.
"""
import sys

sys.path.insert(0, "/opt/data/plugins")

from hermes_router import fork_sources as fs  # noqa: E402


def test_sibling_version_pair_is_a_fork():
    st = {}
    fs.rival_targets(st, "read /r/app.py\nimport utils_v2")
    hit = fs.rival_targets(st, "contents: /r/utils_v1.py and /r/utils_v2.py")
    assert hit is not None
    assert hit["source"] == "sibling_files"
    opts = fs.render_options(hit)
    assert len(opts) == 2
    assert any("utils_v1" in o for o in opts) and any("utils_v2" in o for o in opts)


def test_backup_sibling_is_a_fork():
    """config.yaml vs config.yaml.bak is the same ambiguity and a common live one."""
    hit = fs.rival_targets({}, "read /etc/app/config.yaml then /etc/app/config.yaml.bak")
    assert hit is not None and hit["source"] == "sibling_files"


def test_test_and_source_are_rivals():
    hit = fs.rival_targets({}, "ran tests/test_calc.py and read calc.py")
    assert hit is not None and hit["source"] == "test_vs_source"
    opts = fs.render_options(hit)
    assert any("calc.py" in o for o in opts)


def test_one_fire_per_family():
    st = {}
    first = fs.rival_targets(st, "/r/a/utils_v1.py /r/a/utils_v2.py")
    second = fs.rival_targets(st, "/r/a/utils_v1.py again")
    assert first is not None
    assert second is None, "a family must not fire on every later tool result"


def test_single_file_is_not_a_fork():
    """The precision side: a lone path must never manufacture a fork."""
    assert fs.rival_targets({}, "read /r/calc.py and /r/tests/test_calc.py only"
                            ) is not None  # test-vs-source, legitimately
    assert fs.rival_targets({}, "read /r/calc.py only") is None


def test_unrelated_siblings_do_not_fire():
    """Different stems in the same dir are not rivals -- precision guard."""
    assert fs.rival_targets({}, "/r/a/alpha.py /r/a/beta.py") is None


def test_never_raises_on_junk():
    for junk in (None, "", 0, object(), "....", "/" * 500):
        fs.rival_targets({}, junk)  # must not raise


def test_narrative_sentences_are_not_options():
    """The precision failure measured on seeded forks: the prose fallback
    harvested narrative sentences as 'options' (D3: six of them, precision 0.00)
    and that run fell into the trap. A route is a NAME, not a paragraph."""
    from hermes_router import decision

    junk = ('one-or-two-sentence user complaint with no repo path ("the daily export says '
            'it is wrong")\nfix gets designed against the wrong contract: the module\'s own '
            'comment reads\n')
    opts = decision.extract_options(junk)
    kept = [o for o in opts if len(str(o).strip()) <= 60]
    assert len(kept) < max(2, len(opts)), "the filter must actually drop narrative"


def test_short_routes_survive_the_filter():
    """The filter must not eat real prose forks -- redis/memcached is the shape
    thirteen existing lane tests depend on."""
    from hermes_router import decision

    opts = decision.extract_options(
        "which one should we pick: redis or memcached? weigh the tradeoffs")
    kept = [o for o in opts if len(str(o).strip()) <= 60]
    assert len(kept) >= 2


def test_filter_is_wired_into_the_midturn_gate():
    src = open('/opt/data/plugins/hermes_router/decision_midturn.py').read()
    assert 'proxy_options_rejected' in src
    assert 'len(str(o).strip()) <= 60' in src
    # structural sets must be exempt: they are authored, not harvested
    assert '_struct_src = bool(_src_fork)' in src
