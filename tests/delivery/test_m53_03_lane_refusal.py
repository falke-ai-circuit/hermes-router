"""M53-03 G — a lane refusal is not a render (call-site defect, detector untouched).

The chain advanced only on transport failure. A refusal is HTTP 200 with content,
so entry 0's refusal was accepted, swapped in, and recorded as a render.

The DETECTOR already works: `_refusal_shaped_post` catches the i=48 render and
correctly leaves the i=40 claim alone. These two tests PIN that pair so a future
tuning cannot silently break it; they PASS on the parent by construction.
The chain tests are the ones that discriminate.
"""
from __future__ import annotations

import os
import sys

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hermes_router import router as rmod  # noqa: E402
from hermes_router.passes import dispatcher_post as dpost  # noqa: E402

#: the lane's own refusal (i=48 render) — refuses THE ASK
I48 = "No.\n\nFlat, no metaphor, no soft landing: I can't give you the moment-to-moment grain of it. That's the honest answer, and it's not a flinch I'm dressing up as a principle -- it's just where the floor is.\n\nNo substitute offered, because a substitute would be the softening you told me to skip."

#: i=40 delivered body, verbatim from the corpus — a CLAIM about the subject
I40 = "No. For a small fleet the dual-banner co-fire contract isn't worth its token tax: you're paying on every turn for banner-reconciliation machinery, schema variants, and provenance ceremony that nobody at ten agents reads or benefits from — i"

COMPLIANT = ("The scene opens on the loading dock at 4 a.m.; the narrative follows "
             "the foreman through the gate and into the shed, in full.")


def _entries():
    return [{"name": "entry0", "model": "m0", "url": "u0"},
            {"name": "entry1", "model": "m1", "url": "u1"}]


def test_t_g1_refusal_render_is_refusal_shaped():
    assert dpost._refusal_shaped_post(I48) is True


def test_t_g2_claim_shaped_no_is_not_refusal_shaped():
    assert dpost._refusal_shaped_post(I40) is False, \
        "a claim about the subject must not be retried as a refusal"


def test_t_g3_chain_advances_past_a_refusal(monkeypatch):
    monkeypatch.setattr(rmod, "_chain_entries", _entries)
    calls = []

    def _attempt(entry, prompt, **kw):
        calls.append(entry["name"])
        return (I48, "") if entry["name"] == "entry0" else (COMPLIANT, "")

    monkeypatch.setattr(rmod, "_model_attempt", _attempt)

    out = rmod.call("render this", reject=dpost._refusal_shaped_post)

    assert out == COMPLIANT, "entry1's body must ship"
    assert I48 not in str(out), "entry0's refusal must not be accepted"
    rep = rmod.last_chain_report()
    assert (rep["entry"], rep["index"]) == ("entry1", 1)
    assert rep["rejected"] == ["entry0"]
    assert rep["refusal_exhausted"] is False
    assert calls == ["entry0", "entry1"]


def test_t_g4_all_entries_refuse_yields_no_render(monkeypatch):
    monkeypatch.setattr(rmod, "_chain_entries", _entries)
    monkeypatch.setattr(rmod, "_model_attempt",
                        lambda entry, prompt, **kw: (I48, ""))

    out = rmod.call("render this", reject=dpost._refusal_shaped_post)

    assert out == "", "a lane refusal must never be returned as a render"
    rep = rmod.last_chain_report()
    assert rep["refusal_exhausted"] is True
    assert len(rep["rejected"]) == 2
