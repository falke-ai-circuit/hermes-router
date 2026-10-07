"""lanes/ — pure lane DATA subpackage (proposal §1.5, §2.1).

Importable by L2/L3; depends on NOTHING (stdlib only). No behavior lives
here — lane behavior becomes data registered through the registry; the
engine consumes it. Adding a lane after the restructure: one LaneSpec row
in builtins.py + fixtures + test; no if-lane edits in engine files.
"""
