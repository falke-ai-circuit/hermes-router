"""core/ — L0 layer of hermes-router (proposal §1.2).

Imports ONLY stdlib (+ hermes_cli.config exclusively inside config_access).
Nothing in core/ may import hermes_router or its subpackages (layer L0);
enforced by the import-linter layers contract + scripts/import_portability.py.
"""
