"""R76 P2 U5 — the frontier empty-consult failure banner goes through format_banner.

Before this unit anchor_exec.py:1681 built the line by hand:

    "%s higher-self (frontier) | consult failed | empty response | %s %s"
    % (BANNER_HEAD, model, BANNER_TAIL)

which skipped everything format_banner exists to do: VALID_LANES/FORBIDDEN_LANES
validation, the "://" host redaction, the _END_LABELS pre/post mapping and the unpriced
marker. It is on the failure path, i.e. the path least likely to be exercised.

Correction (R76 P2 U5b): the renderer closes the VALIDATION and label claims only.
metadata_only returns before `ep_s` and the usage cells are USED — the endpoint is
read and host-redacted, and the token/cost args are converted, BEFORE that branch,
then discarded, because this shape renders no endpoint cell and no usage cell. So a
secret-bearing endpoint renders byte-identically to "" and no unpriced marker can
appear: the values are read and dropped, not skipped. The frontier call also passes a
trigger literal that is not an _END_LABELS key. Those are documented-not-asserted
below, not claimed.

FROZEN below is the string that path actually parked BEFORE the change — captured by
driving maybe_execute_anchored with anchored_call returning an empty consult, not by
reading the source. Every pin here compares against it.
"""
from __future__ import annotations

import pathlib

import pytest

import hermes_router.anchor_exec as ax
from hermes_router import anchor_chain, debug_banner, router_core

# captured from the pre-change failure path (model "test/model-a")
FROZEN = ("· router · higher-self (frontier) | consult failed | empty response "
          "| test/model-a ·")

TRIGGER = "consult failed | empty response"


def _render(lane: str = "frontier-anchor", model: str = "test/model-a") -> str:
    return debug_banner.format_banner(lane, TRIGGER, model, "", 0, 0, 0.0, 0.0,
                                      metadata_only=True)


# --- the pin that proves the text did not move ------------------------------

def test_the_renderer_reproduces_the_frozen_string_exactly():
    assert _render() == FROZEN


def test_the_failure_path_parks_the_frozen_string(monkeypatch):
    """Drive the real empty-consult path; byte-identical to the pre-change output."""
    ep = anchor_chain.parse_anchor_uri("openrouter://test/model-a", "primary")
    rec = {"task_id": "t-u5", "lane": "complexity",
           "mode": router_core.MODE_CONSULT, "endpoint": ep, "route_id": "r-u5",
           "orientation": True, "reason": "complexity_orientation"}
    monkeypatch.setattr(router_core, "pending_model_swap", lambda sid: rec)
    monkeypatch.setattr(router_core, "clear_anchor_backoff", lambda *a, **kw: None)
    monkeypatch.setattr(router_core, "store_consult_result", lambda *a, **kw: None)
    chain = anchor_chain.load_anchor_chain()
    monkeypatch.setattr(anchor_chain, "load_anchor_chain", lambda *a, **kw: chain)
    monkeypatch.setattr(anchor_chain, "cap_check", lambda *a, **kw: (True, 0.0, 0.01))
    monkeypatch.setattr(anchor_chain, "estimate_call_cost", lambda *a, **kw: 0.01)
    monkeypatch.setattr(anchor_chain, "record_spend", lambda *a, **kw: None)
    from hermes_router import decision as _dec
    monkeypatch.setattr(_dec, "ledger_write", lambda *a, **kw: 1)
    monkeypatch.setattr(ax, "anchored_call",
                        lambda endpoint, payload: (None, None, None, None))
    monkeypatch.setattr(ax, "_retry_anchored_async", lambda *a, **kw: None)

    parked = {}
    monkeypatch.setattr(debug_banner, "park_anchor_banner",
                        lambda sid, text, **kw: parked.setdefault("text", text))

    ax.maybe_execute_anchored(
        "api_u5", {"messages": [{"role": "user",
                                 "content": "Analyse how the router resolves a model alias"}],
                   "max_tokens": 200})
    assert parked.get("text") == FROZEN, parked


# --- the validation that now applies ---------------------------------------

def test_an_invalid_lane_is_refused_where_the_hand_string_rendered(monkeypatch):
    # the old hand-formatted line had no lane argument at all: it rendered for ANY lane.
    assert debug_banner.format_banner("not-a-lane", TRIGGER, "m", "", 0, 0, 0.0, 0.0,
                                      metadata_only=True) == ""
    assert debug_banner.format_banner("aux", TRIGGER, "m", "", 0, 0, 0.0, 0.0,
                                      metadata_only=True) == ""


def test_the_label_comes_from_the_renderer_not_a_literal():
    # a lane the renderer labels differently must render that label, never "higher-self"
    other = debug_banner.format_banner("uncensored-render", TRIGGER, "m", "", 0, 0, 0.0,
                                       0.0, metadata_only=True)
    assert "shadow-self (uncensored)" in other
    assert "higher-self" not in other


def test_metadata_only_enforces_lane_validation_but_not_endpoint_redaction():
    """Pin the TRUE guarantee at the frontier failure path (R76 P2 U5b).

    Enforced: lane validation (an invalid lane is still refused) and the label
    mapping. NOT enforced on the rendered line: endpoint redaction — the endpoint
    IS read and host-redacted, then discarded, because metadata_only returns
    before `ep_s` is used and this shape renders no endpoint cell. A
    secret-bearing URI therefore renders byte-identically to "". This is the
    documented-not-asserted half of the U5 claim, pinned so it cannot silently
    drift back into being advertised as a redaction surface.
    """
    assert _render(lane="not-a-lane") == ""
    secret = "openrouter://api.secret-host.example/v1/very-secret?key=abc"
    with_secret = debug_banner.format_banner(
        "frontier-anchor", TRIGGER, "test/model-a", secret, 0, 0, 0.0, 0.0,
        metadata_only=True)
    assert with_secret == _render()  # endpoint is inert on this shape
    doc = " ".join((debug_banner.format_banner.__doc__ or "").split())
    assert "Endpoint REDACTION does NOT reach the rendered line" in doc
    assert "the values are read, then dropped, not skipped" in doc


def test_the_documented_mechanism_matches_observable_behaviour():
    """The claim says the endpoint is READ and host-redacted, then discarded.

    Drive the shape with an object whose __str__ records invocation. The read
    must happen — a skipped read leaves the log empty — and the rendered line
    must carry no endpoint. Both halves are measured, so neither "returns
    before it is ever read" nor "a secret host rides in the line" can be
    asserted without the code contradicting it. This is the pin the false
    sentence could not survive.
    """
    calls: list[int] = []

    class _Probe:
        def __str__(self) -> str:
            calls.append(1)
            return "openrouter://api.secret-host.example/v1"

    line = debug_banner.format_banner(
        "frontier-anchor", TRIGGER, "test/model-a", _Probe(), 0, 0, 0.0, 0.0,
        metadata_only=True)
    assert calls, "metadata_only must READ the endpoint: str() is invoked"
    assert line == _render()          # ...and the redacted host is discarded
    assert "secret-host" not in line  # no endpoint cell on the failure shape
    assert "@" not in line


# Phrase halves are concatenated so this tripwire's own text never matches
# itself; the scan is whitespace-flattened so line wrapping cannot hide a
# reinstated claim (the U5a wording was wrapped across lines at two sites).
_FALSE_CLAIMS = (
    "before the endpoint is " + "ever read",
    "before the endpoint and " + "usage args are read",
    "before the endpoint/usage args " + "are read",
    "before the endpoint\n" + "is read",
)


def _flat(path: pathlib.Path) -> str:
    return " ".join(path.read_text(errors="ignore").split())


def test_no_site_still_claims_the_endpoint_goes_unread():
    """The claim's homes — docstring, call-site comment, pin text — are guarded."""
    import inspect

    srcs = {
        "debug_banner.py": str(debug_banner.__file__),
        "anchor_exec.py": str(ax.__file__),
        "this_test_module": str(__file__),
    }
    for name, path in srcs.items():
        flat = _flat(pathlib.Path(path))
        for phrase in _FALSE_CLAIMS:
            assert " ".join(phrase.split()) not in flat, (name, phrase)
    # the pin docstring is runtime-visible too
    pin_doc = inspect.getdoc(
        test_metadata_only_enforces_lane_validation_but_not_endpoint_redaction) or ""
    assert "before the endpoint" not in " ".join(pin_doc.split())


def test_no_hand_formatted_banner_survives_in_anchor_exec():
    src = pathlib.Path(ax.__file__).read_text(errors="ignore")
    assert '"%s higher-self (frontier) | consult failed | "' not in src
    assert "metadata_only=True" in src


def test_the_normal_shape_is_untouched_by_the_new_keyword():
    normal = debug_banner.format_banner("decision", "midturn", "jev-latest", "",
                                        10, 5, 0.000001, 0.3, level=1)
    assert " | tok 10/5 | " in normal and normal.endswith("$0.000001")
    assert debug_banner.BANNER_TAIL not in normal.rsplit("| tok", 1)[-1].rstrip()
