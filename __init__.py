"""uncensored-router plugin — wiring for pre-router middleware + post-router hook.

Spec §5 (pre-router, llm_request middleware) + §6 (post-router,
transform_llm_output hook) + §6.1/§6.2 (shared state) + §9 (logging).

Register(ctx) wires:
  ctx.register_middleware("llm_request", on_llm_request)
  ctx.register_hook("transform_llm_output", on_transform_llm_output)

All errors are swallowed into no-op pass-through ({}/None) — a router failure
must never crash the agent turn (spec §5/§6 constraints).
"""
from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

from . import classifier
from . import method_card
from . import persona_card
from . import router
from . import semantic_classifier
from . import refusal_doctrine
from .core import canonical
from .core import config_access  # noqa: F401 — re-export (single config accessor)
from .core import decisions  # noqa: F401 — re-export
from .core import routing_caps  # noqa: F401 — re-export
from .core import session_store
from .core import state
from .core import telemetry
from .core import usage_ledger  # noqa: F401 — re-export
from .core.telemetry import _impl as _log_route  # moved impl (P1)


from . import anchor_chain
from . import anchor_exec
from . import complexity
from . import router_core
from .api import config_writer  # noqa: F401 — P3a: api/ move
from .api import commands  # noqa: F401
from .api import commands_config  # noqa: F401
from .api import commands_diag  # noqa: F401
from .api import commands_runtime  # noqa: F401
from .api import router_tools  # noqa: F401

# P1 legacy-path module aliases + P3a orchestration wiring live in
# gate/registration.py (R23 leg 3 size debt — lifted verbatim; the hub keeps
# thin delegating stubs so the patch surface plugin.<name> is unchanged).
from .gate import registration as _registration  # noqa: E402

_registration.install_legacy_aliases(__name__)

logger = logging.getLogger(__name__)

# Config (H4/P1): log-path ownership moved to core.telemetry; the hub keeps
# the names as aliases for back-compat patch targets.
DEFAULT_LOG_PATH = telemetry.DEFAULT_LOG_PATH
DEFAULT_LOG_MAX_BYTES = telemetry.DEFAULT_LOG_MAX_BYTES
DEFAULT_PENDING_TTL = 300

_LOG_LOCK = telemetry._LOG_LOCK


# Stage-2 (semantic) knobs — all reads go through _classification_cfg() so
# tests patch _cfg exactly like stage-1.

SEMANTIC_MIN_LEN_NO_OPENER = 400  # gate arm (b): short responses are cheap aux probes


# v3.2.2 render delivery cap — platform-seam cap on DELIVERED text only;
# generation budget untouched. Default 0 = no truncation (back-compat).

RENDER_TRUNCATION_MARKER = "\n\n[render truncated at platform limit]"


# R5 leg 1: PRE-lane taps/passes live in dispatcher_pre.py (thin re-exports;
# late-bound through it so plugin.* monkeypatches reach the moved code).

from . import dispatcher_knobs as _dispatcher_knobs
from . import dispatcher_pre as _dispatcher_pre  # noqa: E402
from . import route_gate as _route_gate  # noqa: E402


# R5 leg 2: POST-lane semantic family lives in dispatcher_post.py.

from . import dispatcher_post as _dispatcher_post  # noqa: E402

SEMANTIC_MIN_LEN_NO_OPENER = _dispatcher_post.SEMANTIC_MIN_LEN_NO_OPENER
_DECLINE_OPENERS = _dispatcher_post._DECLINE_OPENERS  # noqa: F401 — compat alias
_DECLINE_RE = None  # compiled lazily (kept for _gate_semantic compat)


SUBSTANCE_FRAME_ASK_CAP = _dispatcher_post.SUBSTANCE_FRAME_ASK_CAP
SUBSTANCE_FRAME_ASK_SUFFIX = _dispatcher_post.SUBSTANCE_FRAME_ASK_SUFFIX


# P3a: orchestrators + hub helpers live in gate/orchestration.py, re-bound
# here through gate/registration so the patch surface (plugin.<name>) is
# unchanged; every other moved name resolves through __getattr__ (lazy).
on_llm_request = _registration.on_llm_request
on_llm_execution = _registration.on_llm_execution
on_transform_llm_output = _registration.on_transform_llm_output
on_transform_terminal_output = _registration.on_transform_terminal_output


def __getattr__(name: str):
    """P3a lazy export — see gate/registration.hub_getattr."""
    return _registration.hub_getattr(name)


def register(ctx) -> None:
    """Wire hooks + middleware + tools — see gate/registration.register."""
    return _registration.register(ctx)
