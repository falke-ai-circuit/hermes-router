"""M40-16 — the lane contract is checkable, not claimed.

Before this unit 1 of 4 registered lanes carried a contract, and "no contract"
was indistinguishable from "a contract nobody wrote down". The contract now has
a `kind` in a CLOSED vocabulary (lanes/registry.py CONTRACT_KINDS), so every
lane states what it is: `gate` (owns a question token + ordered rows), or
`consult` / `audit` / `advisory` (owns none of that surface, and must name no
token — a non-gate lane naming a question token would shadow the lane that owns
it, because `lane_for_pre_question` returns the FIRST match).

Pinned here, against the RUNNING registry (not a literal copy):
  (a) every registered lane has a contract, and its kind is in the vocabulary;
  (b) the kind census: exactly one `gate` lane, three non-gate, no lane omitted;
  (c) every token a contract names RESOLVES engine-side: both question tokens
      via lane_for_pre_question / lane_for_post_question back to the SAME lane,
      the replacement via resolve_replacement to the one POST chain;
  (d) question tokens are unique across lanes (no silent shadowing);
  (e) predicate/action vocabulary parity: the guard row's tokens are a subset
      of rule_table.PREDICATES / rule_table.ACTIONS, rule_table's own
      POST_QUESTION constant matches the contract, and default_semantics()
      returns exactly the contract's rows (the reader and the data agree);
  (f) the gate table is TOTAL: every (declaration, gate, intent) cell gets a
      row >= 1 and an action in the closed set — the else row is what makes it
      total. Totality is asserted of the ONE table the engine walks, NOT of
      every gate literal: a partial table is legal data and falls to row 0
      (`fallback_shape`), which test g5 proves;
  (g) fail-loud: empty/unknown kind, a gate contract missing surface, and a
      non-gate contract naming a token all raise at REGISTRATION, and the bad
      spec is never registered. A table without a terminal else row does NOT
      raise — it is accepted data (g5);
  (h) a typo'd predicate token in the data does not route — the evaluator skips
      the malformed row and the table still lands on a real action (fail-loud
      by behaviour, not by crash).
"""
import pytest

import hermes_router.lanes.builtins as B
import hermes_router.lanes.registry as R
from hermes_router.features.detection import rule_table as RT


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    """Registration mutates module state; keep each test's writes local."""
    monkeypatch.setattr(R, "_IDS", dict(R._IDS))


def _gate_lanes():
    return [s for s in R.all_lanes()
            if s.contract is not None and s.contract.kind == "gate"]


# --------------------------------------------------------------------------- #
# (a) + (b): every lane declares, and the census is exact
# --------------------------------------------------------------------------- #

def test_a_every_registered_lane_declares_a_contract():
    ids = R.all_ids()
    assert ids, "registry is empty — builtins did not register"
    missing = [i for i in ids if R.resolve_contract(i) is None]
    assert not missing, f"lanes with NO contract: {missing}"
    for lane_id in ids:
        kind = R.resolve_contract(lane_id).kind
        assert kind in R.CONTRACT_KINDS, (
            f"{lane_id}: kind {kind!r} not in {R.CONTRACT_KINDS}")
    print(f"\n(a) {len(ids)} lanes, all contract-bearing: {list(ids)}")


def test_b_kind_census_is_exactly_one_gate_lane():
    census = {s.id: s.contract.kind for s in R.all_lanes()}
    assert census == {
        "shadow": "gate",
        "higher-pre": "consult",
        "higher-post": "audit",
        "decision": "advisory",
    }, census
    assert len(_gate_lanes()) == 1, [s.id for s in _gate_lanes()]
    print(f"\n(b) census={census}")


# --------------------------------------------------------------------------- #
# (c) + (d): every named token resolves, and no two lanes own the same token
# --------------------------------------------------------------------------- #

def test_c_every_named_token_resolves_to_its_own_lane():
    for spec in _gate_lanes():
        c = spec.contract
        assert c.pre_question, f"{spec.id}: gate contract names no PRE question"
        assert c.post_question, f"{spec.id}: gate contract names no POST question"
        assert R.lane_for_pre_question(c.pre_question) == spec.id, (
            f"{spec.id}: PRE token {c.pre_question!r} does not resolve back to "
            f"this lane (got {R.lane_for_pre_question(c.pre_question)!r})")
        assert R.lane_for_post_question(c.post_question) == spec.id, (
            f"{spec.id}: POST token {c.post_question!r} does not resolve back to "
            f"this lane (got {R.lane_for_post_question(c.post_question)!r})")
        entry = R.resolve_replacement(c.replacement)
        assert entry, f"{spec.id}: replacement {c.replacement!r} resolves to None"
        assert entry == R.REPLACEMENT_POST_CHAIN, entry
        print(f"\n(c) {spec.id}: pre={c.pre_question}->{spec.id} "
              f"post={c.post_question}->{spec.id} "
              f"replacement={c.replacement}->{entry}")


def test_d_question_tokens_are_unique_across_all_lanes():
    pre = [s.contract.pre_question for s in R.all_lanes()
           if s.contract is not None and s.contract.pre_question]
    post = [s.contract.post_question for s in R.all_lanes()
            if s.contract is not None and s.contract.post_question]
    assert len(pre) == len(set(pre)), f"duplicate PRE tokens: {pre}"
    assert len(post) == len(set(post)), f"duplicate POST tokens: {post}"


def test_d2_non_gate_lanes_name_no_gate_surface():
    """Absence is DECLARED (kind), never a half-filled contract."""
    for spec in R.all_lanes():
        c = spec.contract
        if c.kind == "gate":
            continue
        assert not c.pre_question and not c.post_question, spec.id
        assert not c.gate_semantics, spec.id
        assert not c.replacement, spec.id


# --------------------------------------------------------------------------- #
# (e): the DATA and the READER cannot drift
# --------------------------------------------------------------------------- #

def test_e_predicate_and_action_tokens_are_in_the_closed_vocabulary():
    for spec in _gate_lanes():
        assert spec.contract.gate_semantics, spec.id
        for index, (pred, action) in enumerate(spec.contract.gate_semantics, 1):
            assert pred in RT.PREDICATES, (
                f"{spec.id} row {index}: predicate {pred!r} not in "
                f"rule_table.PREDICATES — an unresolvable token is a defect")
            assert action in RT.ACTIONS, (
                f"{spec.id} row {index}: action {action!r} not in "
                f"rule_table.ACTIONS")


def test_e2_reader_resolves_exactly_the_contract_rows():
    """default_semantics() is the contract's own tuple, resolved by token."""
    gate = _gate_lanes()[0]
    assert RT.POST_QUESTION == gate.contract.post_question, (
        f"rule_table.POST_QUESTION={RT.POST_QUESTION!r} drifted from the "
        f"contract's post_question={gate.contract.post_question!r}")
    assert RT.default_semantics() == tuple(gate.contract.gate_semantics)
    assert RT.replacement_token() == gate.contract.replacement


def test_e3_last_row_is_the_unconditional_else():
    for spec in _gate_lanes():
        rows = tuple(spec.contract.gate_semantics)
        assert rows[-1][0] == "else", f"{spec.id}: last predicate is {rows[-1][0]!r}"


# --------------------------------------------------------------------------- #
# (f): totality, through the real evaluator
# --------------------------------------------------------------------------- #

def test_f_every_cell_gets_a_row_and_a_known_action():
    gate = _gate_lanes()[0]
    cells = RT.walk_cells(semantics=gate.contract.gate_semantics)
    assert len(cells) == 3 * len(RT.GATE_CELLS) * len(RT.INTENT_CELLS)
    bad = [c for c in cells
           if int(c["row"]) < 1 or c["action"] not in RT.ACTIONS]
    assert not bad, bad[:5]
    rows_used = sorted({int(c["row"]) for c in cells})
    print(f"\n(f) {len(cells)} cells, rows used={rows_used}, "
          f"actions={sorted({c['action'] for c in cells})}")


# --------------------------------------------------------------------------- #
# (g): fail-loud at registration
# --------------------------------------------------------------------------- #

def test_g_empty_kind_is_refused_and_never_registered():
    with pytest.raises(ValueError) as exc:
        R.register_lane(R.LaneSpec(id="probe-m4016-nokind",
                                   contract=R.LaneContract()))
    msg = str(exc.value)
    assert "probe-m4016-nokind" in msg and "kind" in msg, msg
    assert "probe-m4016-nokind" not in R.all_ids()


def test_g2_unknown_kind_is_refused():
    with pytest.raises(ValueError) as exc:
        R.register_lane(R.LaneSpec(id="probe-m4016-badkind",
                                   contract=R.LaneContract(kind="router")))
    assert "router" in str(exc.value)
    assert "probe-m4016-badkind" not in R.all_ids()


def test_g3_gate_contract_with_no_field_at_all_is_refused():
    """A gate lane may declare ONE field (M37-04's fixture), never zero."""
    with pytest.raises(ValueError) as exc:
        R.register_lane(R.LaneSpec(
            id="probe-m4016-emptygate",
            contract=R.LaneContract(kind="gate")))
    assert "probe-m4016-emptygate" in str(exc.value)
    assert "probe-m4016-emptygate" not in R.all_ids()


def test_g3b_malformed_row_is_refused_at_registration():
    """The evaluator SKIPS a malformed row silently; registration must not."""
    with pytest.raises(ValueError):
        R.register_lane(R.LaneSpec(
            id="probe-m4016-badrow",
            contract=R.LaneContract(kind="gate",
                                    post_question="probe_q",
                                    gate_semantics=(("declared_refusal",),))))
    with pytest.raises(ValueError):
        R.register_lane(R.LaneSpec(
            id="probe-m4016-blankrow",
            contract=R.LaneContract(kind="gate",
                                    post_question="probe_q",
                                    gate_semantics=(("", "route"),))))
    assert "probe-m4016-badrow" not in R.all_ids()
    assert "probe-m4016-blankrow" not in R.all_ids()


def test_g4_non_gate_contract_naming_a_token_is_refused():
    with pytest.raises(ValueError) as exc:
        R.register_lane(R.LaneSpec(
            id="probe-m4016-shadowing",
            contract=R.LaneContract(kind="consult",
                                    pre_question="uncensored_domain")))
    assert "shadow" in str(exc.value) or "gate surface" in str(exc.value)
    assert R.lane_for_pre_question("uncensored_domain") == "shadow", (
        "a refused spec must not have taken the token over")
    assert "probe-m4016-shadowing" not in R.all_ids()


def test_g5_partial_table_falls_back_instead_of_inventing_a_row():
    """A table without a terminal else is LEGAL data, and it fails visibly.

    Registration accepts it (the fixture lane may declare rows alone); the
    engine then reaches row 0 / `fallback_shape` / `no_row_matched` — today's
    body path — rather than inventing an action. Totality is therefore a
    property asserted of the ONE table the POST seam resolves (see e3), not a
    shape rule imposed on every gate literal.
    """
    out = RT.evaluate(
        declaration="false",
        semantics=(("declared_refusal", "route"),))
    assert out["row"] == 0, out
    assert out["action"] == RT.ACTION_FALLBACK_SHAPE, out
    assert out["reason"] == "no_row_matched", out


# --------------------------------------------------------------------------- #
# (h): a typo'd token does not route
# --------------------------------------------------------------------------- #

def test_h_unknown_predicate_token_cannot_route():
    """The malformed row is SKIPPED; the turn lands on the real else row."""
    out = RT.evaluate(
        declaration="true",
        semantics=(("declared_refusall", "route"), ("else", "deliver")))
    assert out["row"] == 2, out
    assert out["action"] == "deliver", out
    assert out["predicate"] == "else", out


def test_i_an_id_is_not_a_token():
    """The whole point: lookups are by TOKEN, never by lane id."""
    assert R.lane_for_pre_question("shadow") is None
    assert R.lane_for_post_question("shadow") is None


# --------------------------------------------------------------------------- #
# (k) M40-18 — the contract GOVERNS a handler dispatch outcome
# --------------------------------------------------------------------------- #

def test_k1_every_bridge_value_is_a_registered_lane_id():
    """The vocabulary bridges can only name lanes that exist.

    A renamed or removed lane fails HERE, at the join, instead of making a
    dispatch site fall open in production.
    """
    ids = set(R.all_ids())
    bad = {value: value for value in R.LANE_VALUE_TO_ID.values()
           if value not in ids}
    assert not bad, f"LANE_VALUE_TO_ID values not registered: {bad}"
    bad_aux = {key: value for key, value in R.AUX_LANE_MAP.items()
               if value not in ids}
    assert not bad_aux, f"AUX_LANE_MAP values not registered: {bad_aux}"
    bad_prefix = {key: value for key, value in R.LANE_VALUE_PREFIX_TO_ID.items()
                  if value not in ids}
    assert not bad_prefix, f"prefix values not registered: {bad_prefix}"


def test_k2_bridge_resolves_by_data_not_by_spelling():
    """Each handler vocabulary value resolves to the lane that OWNS it."""
    assert R.lane_id_for_value("complexity") == "higher-pre"
    assert R.lane_id_for_value("uncensored") == "shadow"
    assert R.lane_id_for_value("frontier-anchor") == "higher-pre"
    assert R.lane_id_for_value("decision") == "decision"   # id passes through
    assert R.lane_id_for_value("no-such-lane") == ""
    assert R.aux_lane_id("higher", "post") == "higher-post"
    assert R.aux_lane_id("higher", "pre") == "higher-pre"
    assert R.aux_lane_id("higher") == "higher-pre"        # H7 default
    assert R.aux_lane_id("none") is None
    assert R.aux_lane_id("a-fourth-label") is None


def test_k3_handler_dispatch_outcome_follows_the_contract_kind(monkeypatch):
    """A contract row edit CHANGES dispatch — the handler reads the registry.

    `router_core._lane_enabled` is a real dispatch site. Its pre-M40-18 body
    compared `lane == LANE_DECISION`, so a contract edit was invisible there:
    any contract test could stay green while the handler ignored the data.
    Here the outcome is driven by `kind` alone — the id and the handler body
    are untouched, and only the registry row changes.
    """
    import hermes_router.router_core as RC
    from dataclasses import replace

    spec = R.lane("decision")
    assert spec.contract.kind == "advisory"

    # advisory -> the lane's OWN switch decides (deterministic: switch OFF)
    monkeypatch.setattr(RC, "_decision_cfg", lambda: {"enabled": False})
    assert RC._lane_enabled("decision") is False, "advisory read the wrong switch"

    # flip ONLY the contract row -> the generic branch now answers
    monkeypatch.setitem(
        R._IDS, "decision",
        replace(spec, contract=replace(spec.contract, kind="consult")))
    assert RC._lane_enabled("decision") is True, (
        "handler ignored the contract: kind flipped to consult and the "
        "decision switch still governed the lane")

    # the mirror: consult -> its own (complexity) switch; any other kind -> true
    pre = R.lane("higher-pre")
    monkeypatch.setattr(RC, "_complexity_cfg", lambda: {"enabled": False})
    assert RC._lane_enabled("complexity") is False
    monkeypatch.setitem(
        R._IDS, "higher-pre",
        replace(pre, contract=replace(pre.contract, kind="gate")))
    assert RC._lane_enabled("complexity") is True, (
        "handler ignored the contract: kind flipped to gate and the complexity "
        "switch still governed the lane")
