"""R34 item 8f — lane_ab harness: the read-only contract + PASS/FAIL rule.

The harness must never edit config (it only READS the live lane state) and
its comparison rule is Goran's: lanes-on wins if it yields >= the artifact
of lanes-off with fewer or equal turns.
"""
import importlib.util
import os
import sys

_P = "/opt/data/plugins"
if _P not in sys.path:
    sys.path.insert(0, _P)

_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "scripts", "lane_ab.py")


def _mod():
    spec = importlib.util.spec_from_file_location("lane_ab", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_read_lane_state_reads_without_writing(tmp_path):
    mod = _mod()
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "hermes_router:\n"
        "  complexity:\n"
        "    level: 2\n"
        "  decision:\n"
        "    midturn: \"off\"\n"
        "    frame: execution\n", encoding="utf-8")
    before = cfg.read_text(encoding="utf-8")
    state = mod.read_lane_state(str(tmp_path))
    assert state == {"complexity_level": 2, "midturn": "off",
                     "frame": "execution"}
    assert cfg.read_text(encoding="utf-8") == before   # never writes


def test_compare_rule():
    mod = _mod()
    off = {"commits": 1, "files_touched": 2, "turns": 5, "wall_s": 100}
    assert "PASS" in mod.compare(
        off, {"commits": 1, "files_touched": 2, "turns": 3, "wall_s": 80})
    assert "PASS" in mod.compare(
        off, {"commits": 2, "files_touched": 4, "turns": 5, "wall_s": 120})
    # fewer artifacts -> FAIL regardless of turn count
    assert "FAIL" in mod.compare(
        off, {"commits": 0, "files_touched": 0, "turns": 2, "wall_s": 50})
    # same artifacts but MORE turns -> FAIL
    assert "FAIL" in mod.compare(
        off, {"commits": 1, "files_touched": 2, "turns": 7, "wall_s": 50})
