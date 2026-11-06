"""M38-05 — fleet golden table: the same ask must hit the same gate.

Pin for conductor rulings R1-R5 (/opt/data/tmp/r78u/M38-04_RULINGS.md) and the
R5 "minimal invariant" paragraph. Four halves:

(a) effective gate-key tuple, loaded per profile under its OWN HERMES_HOME, is
    byte-equal across all 11 — read through the plugin's accessors, never raw
    YAML (a raw-YAML comparison cannot see defaults, and a default is what let
    nine profiles "look" identical while shadow and coder diverged);
(b) declared-values-not-defaults: `anchor_chain.judge` and `persona_mode` are
    present as config keys in all 11 (a silent default is not a decision on the
    record), and judge is never the primary (a self-judging consult);
(c) absence: the two dead keys R5 deletes exist in no profile and in no example
    config, and no profile .env carries PERSONA_MODE under either spelling — the
    dead-name decoy R4 kills must not come back;
(d) cap_render(text, 0) is byte-identical passthrough (R3's fleet value).

WHY A SUBPROCESS PER PROFILE: config_access.router_section() resolves tier 1
through hermes_cli.config.load_config(), which answers with the CALLING
PROCESS's profile. An in-process loop over eleven HERMES_HOME values would read
one config eleven times and go green for the wrong reason — exactly the probe
artifact M38-02 §1.3 caught. Each profile is therefore probed in a fresh
interpreter with HERMES_HOME set, and `local_exec` lifts the suite's blanket
subprocess ban (the probe is local and network-free).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PROFILES = [
    "analyst", "architect", "coder", "conductor", "evol", "operative",
    "orchestrator", "researcher", "reviewer", "shadow", "valmet",
]

REPO = Path(__file__).resolve().parents[2]
PARENT = REPO.parent
HERMES_RUNTIME = "/opt/hermes"
PROFILES_DIR = Path(os.environ.get("M38_PROFILES_DIR", "/opt/data/profiles"))

FLEET_JUDGE_URI = "nous://openai/gpt-6-luna-pro"
FLEET_JUDGE_MODEL = "openai/gpt-6-luna-pro"

pytestmark = pytest.mark.local_exec

# Probe run in a fresh interpreter per profile. Prints one JSON object.
_PROBE = r"""
import json, os, sys
sys.path.insert(0, {parent!r})
sys.path.insert(0, {runtime!r})
from hermes_router import config_access, anchor_chain, completion_audit, instinct_triggers, router_core
from hermes_router.passes import dispatcher_knobs as k

sec = config_access.router_section()
cls = sec.get("classification") or {{}}
ae = cls.get("aux_endpoint") or {{}}
cx = router_core._complexity_cfg() or {{}}
dec = sec.get("decision") or {{}}
ac = config_access.sub_block("anchor_chain") or {{}}
chain = anchor_chain.load_anchor_chain()
judge, primary = chain.endpoint_for("judge"), chain.endpoint_for("primary")

def model(ep):
    return getattr(ep, "model", None) if ep else None

print(json.dumps({{
    "cache_path": config_access._cache.get("path"),
    "classification": {{
        "pre_patterns": k._pre_patterns(),
        "post_patterns": k._post_patterns(),
        "match_threshold": k._match_threshold(),
        "mode": k._aux_mode(),
        "aux_classify": k._aux_classify_enabled(),
        "doctrine_verdict": k._doctrine_verdict_enabled(),
        "aux_endpoint": {{key: ae.get(key) for key in ("url", "model", "key_env", "max_tokens")}},
    }},
    "complexity": {{
        "enabled": bool(cx.get("enabled", True)),
        "pre_mode": str(cx.get("pre_mode") or "route"),
        "audit_mode": str(cx.get("audit_mode") or "complex"),
        "work_mass_min_tools": completion_audit.work_mass_min_tools(),
    }},
    "decision": {{
        "enabled": bool(dec.get("enabled", True)),
        "seam": str(dec.get("seam") or "midturn_prellm"),
        "post": bool(dec.get("post", False)),
        "pre": str(dec.get("pre") or "shadow"),
        "midturn": str(dec.get("midturn") or "on"),
        "recognition": instinct_triggers.mode_of(None),
    }},
    "anchor_chain": {{
        "primary": model(primary),
        "judge": model(judge),
        "failover": ac.get("failover"),
        "models": ac.get("models"),
        "pricing": ac.get("pricing"),
    }},
    "render_max_chars": k.render_max_chars(),
}}, sort_keys=True))
"""


def _probe(profile: str) -> dict:
    """Effective gate-key tuple for one profile, read under its own HERMES_HOME."""
    env = dict(os.environ)
    env["HERMES_HOME"] = str(PROFILES_DIR / profile)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(PARENT), HERMES_RUNTIME] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE.format(parent=str(PARENT), runtime=HERMES_RUNTIME)],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, f"{profile}: probe failed\n{proc.stderr[-4000:]}"
    assert proc.stdout.strip(), f"{profile}: probe printed nothing\n{proc.stderr[-4000:]}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def fleet() -> dict:
    assert PROFILES_DIR.is_dir(), f"fleet profiles dir not found: {PROFILES_DIR}"
    return {p: _probe(p) for p in PROFILES}


def _config(profile: str) -> dict:
    with open(PROFILES_DIR / profile / "config.yaml", encoding="utf-8") as fh:
        return (yaml.safe_load(fh) or {}).get("hermes_router") or {}


def test_a_effective_gate_key_tuple_byte_equal_across_fleet(fleet):
    """(a) One value per gate key fleet-wide, resolved through the accessors."""
    baseline = json.dumps(fleet[PROFILES[0]], sort_keys=True, separators=(",", ":"))
    divergences = [
        f"{profile}:\n    {json.dumps(fleet[profile], sort_keys=True, separators=(',', ':'))}"
        for profile in PROFILES
        if json.dumps(fleet[profile], sort_keys=True, separators=(",", ":")) != baseline
    ]
    assert not divergences, (
        "gate-key tuple diverges from {} fleet baseline {}:\n  {}".format(
            PROFILES[0], baseline, "\n  ".join(divergences))
    )


def test_b_declared_values_not_defaults(fleet):
    """(b) judge + persona_mode are DECLARED in every config, judge != primary."""
    declared_judge = []
    declared_persona = []
    for profile in PROFILES:
        section = _config(profile)
        chain = section.get("anchor_chain") or {}
        if chain.get("judge") != FLEET_JUDGE_URI:
            declared_judge.append(f"{profile}: anchor_chain.judge={chain.get('judge')!r}")
        if "persona_mode" not in section:
            declared_persona.append(f"{profile}: persona_mode ABSENT (silent default)")
        else:
            want = "none" if profile == "orchestrator" else "voice_stems"
            if section.get("persona_mode") != want:
                declared_persona.append(
                    f"{profile}: persona_mode={section.get('persona_mode')!r} want {want!r}")

    assert not declared_judge, "judge must be declared fleet-wide as %r:\n  %s" % (
        FLEET_JUDGE_URI, "\n  ".join(declared_judge))
    assert not declared_persona, "persona_mode must be declared fleet-wide:\n  " + "\n  ".join(
        declared_persona)

    self_judging = [
        f"{profile}: judge={row['anchor_chain']['judge']!r} == primary="
        f"{row['anchor_chain']['primary']!r}"
        for profile, row in fleet.items()
        if row["anchor_chain"]["judge"] == row["anchor_chain"]["primary"]
    ]
    assert not self_judging, "judge must never equal primary:\n  " + "\n  ".join(self_judging)
    assert all(row["anchor_chain"]["judge"] == FLEET_JUDGE_MODEL for row in fleet.values())


def test_c_dead_keys_absent_and_timeout_is_the_constant(fleet):
    """(c) R5 dead keys gone everywhere; R4 dead env name gone; aux timeout = 45."""
    survivors = []
    for profile in PROFILES:
        section = _config(profile)
        cls = section.get("classification") or {}
        endpoint = cls.get("aux_endpoint") or {}
        if "aux_source" in cls:
            survivors.append(f"{profile}: classification.aux_source={cls['aux_source']!r}")
        if "timeout_seconds" in endpoint:
            survivors.append(
                f"{profile}: classification.aux_endpoint.timeout_seconds={endpoint['timeout_seconds']!r}")
    assert not survivors, "dead keys must not exist fleet-wide:\n  " + "\n  ".join(survivors)

    example_path = REPO / "examples" / "config.example.yaml"
    if example_path.is_file():
        with open(example_path, encoding="utf-8") as fh:
            example_text = fh.read()
        assert "aux_source" not in example_text, "examples/config.example.yaml still carries aux_source"
        assert "timeout_seconds: 25" not in example_text, (
            "examples/config.example.yaml still advertises the 25s aux timeout")

    env_survivors = []
    for profile in PROFILES:
        env_path = PROFILES_DIR / profile / ".env"
        if not env_path.is_file():
            continue
        for lineno, line in enumerate(env_path.read_text(encoding="utf-8").splitlines(), 1):
            if "PERSONA_MODE" in line:
                env_survivors.append(f"{profile}/.env:{lineno}: {line.strip()}")
    assert not env_survivors, (
        "PERSONA_MODE must not appear in any profile .env under either spelling "
        "(config is the single source of truth):\n  " + "\n  ".join(env_survivors))

    from hermes_router import semantic_classifier

    assert semantic_classifier.HERMES_AUX_TIMEOUT_SECONDS == 45
    # No config key survives, so the module constant IS the effective value fleet-wide.
    assert all("timeout_seconds" not in (row["classification"]["aux_endpoint"] or {})
               for row in fleet.values())


def test_d_cap_render_limit_zero_is_byte_identical_passthrough():
    """(d) R3 fleet value 0: the cap is disabled and delivery is untouched."""
    from hermes_router.passes.dispatcher_knobs import cap_render

    samples = [
        "",
        "short",
        "a" * 6001,
        "x" * 100_000,
        "unicode — ✓ 漢字 " * 2000,
        "line\nline\r\nline",
    ]
    for text in samples:
        out = cap_render(text, 0)
        assert out == text, f"limit 0 mutated text (len in={len(text)} out={len(out)})"
        assert out.encode("utf-8") == text.encode("utf-8"), "limit 0 is not byte-identical"

    # Negative limits are the same contract; and a live cap still truncates, so
    # this test cannot pass against a no-op stub. The limit is chosen above the
    # truncation-marker length: below it the marker alone exceeds the cap, which
    # is a separate (pre-existing) product question, not R3's fleet value.
    assert cap_render("abc" * 1000, -1) == "abc" * 1000
    capped = cap_render("a" * 100, 60)
    assert capped != "a" * 100 and len(capped) <= 60
