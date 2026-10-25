"""The decision lane must recognise forks from ANY agent's traffic, not only from
engineering repos.

Three of the four original sources key on repo structure (sibling files, tests,
docs, config). An analyst weighs two root causes, a researcher two sources, a
valmet engineer two protocol readings, an operative two hosts -- none of those
are file pairs, so a lane that only sees file pairs recognises a minority of the
fleet's forks while reporting itself healthy.
"""
import sys

sys.path.insert(0, "/opt/data/plugins")

from hermes_router import fork_sources as fs  # noqa: E402


def test_analyst_hypotheses_are_a_fork():
    hit = fs.rival_targets(
        {}, "The cause is either a stale cache layer or a misconfigured retry budget.")
    assert hit is not None and hit["source"] == "either_or"
    assert len(hit["options"]) == 2


def test_valmet_protocol_reading_is_a_fork():
    hit = fs.rival_targets(
        {}, "Either the PLC tag is scaled in tenths or the HMI converts twice.")
    assert hit is not None and hit["source"] == "either_or"


def test_operative_host_choice_is_a_fork():
    hit = fs.rival_targets(
        {}, "Reconnect to prod-db-02 rather than prod-db-01 before the failover.")
    assert hit is not None
    assert hit["options"][0] == "prod-db-02", hit["options"]


def test_listed_candidates_are_a_fork():
    hit = fs.rival_targets(
        {}, "Possible causes:\n- stale cache\n- retry budget exhausted\n- network partition")
    assert hit is not None and hit["source"] == "listed_candidates"
    assert len(hit["options"]) == 3


def test_generic_sources_keep_precision():
    """Domain-general must not mean trigger-happy. Plain prose carries no fork."""
    for benign in ("The report describes the system and its owners in detail.",
                   "yes or no",
                   "Done. Tests pass and the change is committed."):
        assert fs.rival_targets({}, benign) is None, benign


def test_family_fires_once_per_session():
    st = {}
    ask = "The cause is either a stale cache layer or a retry budget."
    assert fs.rival_targets(st, ask) is not None
    assert fs.rival_targets(st, ask) is None


def test_narrative_span_is_rejected_in_every_domain():
    """The 60-char guard applies to generic sources too: a route is a name."""
    long_prose = ("Either we take the one-or-two-sentence user complaint with no "
                  "repo path and design against it or we take the other option.")
    hit = fs.rival_targets({}, long_prose)
    assert hit is None or all(len(o) <= 60 for o in hit["options"])


def test_registry_is_data_not_code():
    """Templated: a new source is a row in GENERIC_DETECTORS, and the lane, its
    guards and its one-fire-per-family discipline are inherited."""
    names = [n for n, _ in fs.GENERIC_DETECTORS]
    assert names == ["either_or", "listed_candidates"]
    for name, fn in fs.GENERIC_DETECTORS:
        assert callable(fn)
        hit = fn({}, "either a stale cache or a retry budget")
        assert hit is None or {"family", "options"} <= set(hit)


def test_enabled_sources_is_portable_and_fails_open():
    on = fs.enabled_sources()
    assert "either_or" in on and "sibling_files" in on
    assert isinstance(on, tuple)


def test_module_is_portable_by_construction():
    """No absolute paths, no hermes_cli, no profile coupling: this module must
    work unchanged in another Hermes installation."""
    src = open(fs.__file__).read()
    for bad in ("/opt/data", "profiles/", "hermes_cli", "HERMES_HOME"):
        assert bad not in src, "%s would pin the module to this host" % bad


# ---------------------------------------------------------------------------
# A DIFFERENT Hermes install may have completely different agents.
# Nothing here may assume our eleven roles, our domains, or our config schema.
# ---------------------------------------------------------------------------
FOREIGN = [
    # legal assistant
    ("legal", "Either we file in the county court or we escalate to the appellate "
              "division before the limitation date."),
    # medical triage assistant
    ("triage", "Either the presentation is an atypical migraine or a transient "
               "ischaemic attack; the vitals do not separate them."),
    # warehouse / logistics agent
    ("logistics", "Ship from hub-berlin rather than hub-prague, because the customs "
                  "window closes at 18:00."),
    # customer support bot
    ("support", "Two options: refund the order or reship it with express handling."),
    # finance assistant
    ("finance", "Possible causes:\n- duplicate settlement entry\n- FX rate applied twice\n"
                "- missing accrual"),
    # scientific research agent
    ("science", "The signal is either thermal drift in the detector or a genuine "
                "periodicity in the sample."),
]


def test_foreign_domains_are_recognised_without_any_role_assumption():
    for domain, text in FOREIGN:
        hit = fs.rival_targets({}, text)
        assert hit is not None, "%s produced no fork: %s" % (domain, text)
        assert 2 <= len(hit["options"]) <= 4, (domain, hit["options"])
        assert all(len(o) <= 60 for o in hit["options"]), (domain, hit["options"])


def test_module_names_no_agent_role():
    """Portability: the extractor's CODE must not know that coder/reviewer/analyst
    exist. Comments may name them as examples; code may not branch on them."""
    lines = []
    for ln in open(fs.__file__).read().splitlines():
        if ln.lstrip().startswith("#"):
            continue
        lines.append(ln.split("#", 1)[0])
    src = "\n".join(lines).lower()
    for role in ("coder", "reviewer", "orchestrator", "valmet", "shadow",
                 "operative", "architect", "analyst"):
        assert role not in src, "%s is fleet-specific and would not port" % role


def test_works_with_no_config_seam_at_all():
    """A fresh install may not expose our config reader. Recognition must not
    depend on it: fail open, keep every source."""
    import builtins

    real_import = builtins.__import__

    def deny(name, *a, **kw):
        if "config_access" in name:
            raise ImportError("not present in this installation")
        return real_import(name, *a, **kw)

    builtins.__import__ = deny
    try:
        on = fs.enabled_sources()
    finally:
        builtins.__import__ = real_import
    assert "either_or" in on and "sibling_files" in on


def test_requires_no_store_shape():
    """Foreign hosts pass whatever session state they keep; an empty dict, a
    dict with unrelated keys, or a nested dict must all work."""
    text = "Either we file in the county court or we escalate."
    for store in ({}, {"unrelated": 1}, {"fork_src": {"paths": [], "fired": []}}):
        assert fs.rival_targets(store, text) is not None


def test_long_clause_does_not_swallow_the_fork():
    """Regression: the second span used to require a sentence terminator within 60
    chars, so any fork followed by a long clause matched nothing at all."""
    hit = fs.rival_targets(
        {}, "Either we file in the county court or we escalate to the appellate "
            "division before the limitation date.")
    assert hit is not None
    assert "escalate" in hit["options"][1], hit["options"]
