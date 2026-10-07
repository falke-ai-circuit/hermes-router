"""core/patchpoints.py — the one LEGAL monkeypatch surface (proposal §1.4).

Tests patch exactly the module attributes published here. Anything else is
unsanctioned. Phase P1 publishes the route-telemetry override; later phases
extend this registry (cfg, knob readers) and migrate the conftest fixtures
onto it (§1.4 patchpoint migration), after which `plugin._cfg` /
`plugin._log_route` package-attr reach-ins are retired.

Published patchpoints:
    log_route_override — callable(event: str, **fields) -> None
        When set, core.telemetry.log_route dispatches HERE instead of the
        implementation. Set via monkeypatch.setattr(patchpoints,
        "log_route_override", fn) — monkeypatch restores automatically.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

# Override slots (module attrs so monkeypatch.setattr works directly).
log_route_override: Optional[Callable[..., Any]] = None
