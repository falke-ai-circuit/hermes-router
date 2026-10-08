"""Thin re-export shim (R23 leg 1) — implementation moved to
passes/dispatcher_knobs.py; this path remains for importers not yet migrated. ALL
attribute reads AND writes delegate to the real module so late-binding
consumers and tests that patch attributes through the old path keep
working. A later rider deletes this shim once zero importers remain."""
import sys as _sys
import types as _types

from .passes import dispatcher_knobs as _impl  # noqa: F401 — the implementation


class _ShimModule(_types.ModuleType):
    def __getattr__(self, name):
        return getattr(_impl, name)

    def __setattr__(self, name, value):
        setattr(_impl, name, value)


_sys.modules[__name__].__class__ = _ShimModule
