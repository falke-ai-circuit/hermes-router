"""v3.3.6 method-card tests: config override + skill lift + fail-open."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hermes_router import method_card


def test_override_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    # no skills dirs — lift returns "" but override present:
    monkeypatch.setattr("hermes_router.router._load_router_config",
                        lambda: {"render_method_spec": "STRUCTURED DOSSIER SPEC"})
    card = method_card.build_method_context(force_refresh=True)
    assert "STRUCTURED DOSSIER SPEC" in card


def test_fail_open_no_home(monkeypatch):
    monkeypatch.setattr(method_card, "_hermes_home", lambda: "")
    card = method_card.build_method_context(force_refresh=True)
    assert card == ""


def test_skill_lift_with_ask(tmp_path):
    sk = tmp_path / "skills" / "dossier" / "SKILL.md"
    sk.parent.mkdir(parents=True)
    sk.write_text("---\nname: dossier\ndescription: Build structured research dossiers.\n---\n"
                  "# Output format\n1. Taxonomy 2. Breakdown 3. Failure modes")
    card = method_card.build_method_context(hermes_home=str(tmp_path),
                                            ask="build me a research dossier now",
                                            force_refresh=True)
    assert "dossier" in card.lower()
    assert "taxonomy" in card.lower()
