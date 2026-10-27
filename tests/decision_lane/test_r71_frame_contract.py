"""R71 — the frame the main model reads: identity, provenance, payload, contract.

The block used to be a bare distribution: a header, percentages, dials. Nothing told the
model what it was looking at, what it had been derived from, or what to do with it. Frontier
and uncensored both carry that scaffolding; this lane did not, so "did the model use it" was
a question about a block that never said it had a use.

Four pins, each falsifiable:
  1. the frame names its lane and denies being a request (echo guard depends on it)
  2. provenance comes from the evidence and is never invented
  3. dials arrive as words, and a near-zero dial reads as steady rather than as a shift
  4. the contract survives truncation; provenance is what gets dropped
"""
from hermes_router import decision_extract as dx


PARSED = {
    "options": [
        {"p": 0.73, "label": "fix src/render.py", "kind": "edit", "target": "src/render.py"},
        {"p": 0.45, "label": "open src/render.py", "kind": "read", "target": "src/render.py"},
        {"p": 0.41, "label": "ask the user which way to go", "kind": "none", "target": ""},
        {"p": 0.10, "label": "stop and report", "kind": "none", "target": ""},
    ],
    "margin": 0.28,
    "mindset": {"scope": -0.86, "rigour": 0.04},
}


def test_frame_names_the_lane_and_denies_being_a_request():
    """R72 standardised the envelope: one marker for every decision surface, the way the
    frontier and uncensored lanes each carry one. The registers became fields inside it."""
    block = dx.render(PARSED, basis=dx.basis_line("run pytest", "1 failed, 0 passed"))
    assert block.splitlines()[0] == dx.DECISION_MARKER
    assert "DECISION-LANE" in block
    assert "PROVENANCE-LOGGED" in block
    assert "register: board" in block
    # R73 P0: the contract is a STATEMENT about the block, not an order to the reader. A tail
    # directive ("act: take the top unless ...") sat at the generation boundary and read as the
    # next thing to do: a coder turn answered the reading instead of working, the body came back
    # as an echo, and the guard withheld the turn. The block still says what it is and still
    # licenses deviation - just without an imperative tail.
    assert "reading, not an instruction" in block
    assert not block.rstrip().lower().splitlines()[-1].lstrip().startswith("act:")


def test_provenance_comes_from_the_evidence_provided():
    basis = dx.basis_line("run pytest", "1 failed, 0 passed in 0.4s")
    assert basis.startswith("  from: run pytest")
    assert "1 failed" in basis
    block = dx.render(PARSED, basis=basis)
    assert basis in block


def test_provenance_is_absent_when_there_is_no_evidence_rather_than_fabricated():
    assert dx.basis_line("", "") == ""
    assert dx.basis_line("", "   \n  ") == ""
    block = dx.render(PARSED, basis="")
    assert "from:" not in block


def test_dials_arrive_as_words_and_a_small_dial_reads_as_steady():
    block = dx.render(PARSED, basis="  from: run pytest -> 1 failed")
    assert "scope narrow" in block, block
    assert "rigour steady" in block, block
    # the raw value still travels, so the reading stays auditable
    assert "-0.86" in block


def test_the_contract_survives_truncation_and_provenance_is_what_drops():
    basis = dx.basis_line("run pytest", "1 failed, 0 passed " + "x" * 400)
    full = dx.render(PARSED, basis=basis)
    tight = dx.render(PARSED, limit=len(full) - 20, basis=basis)
    assert tight, "a frame that cannot fit should shorten, not vanish"
    assert dx.USE_CONTRACT in tight
    assert basis not in tight


def test_render_is_silent_below_two_options():
    assert dx.render({"options": [PARSED["options"][0]], "mindset": {}}) == ""
