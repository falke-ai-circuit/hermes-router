"""Atomic config writer for hermes_router control operations (v3.0.0).

ALL plugin config edits flow through write_plugin_section():

  1. read current config.yaml (hermes_cli.config.load_config)
  2. mutate ONLY the plugin section (hermes_router canonical; legacy
     uncensored_router section migrated forward when present)
  3. write to <config>.tmp in the SAME directory
  4. JSON-parse-validate the temp file (YAML-safe subset: we round-trip
     through yaml.safe_load instead when yaml is importable)
  5. os.replace(tmp, config.yaml) — atomic on POSIX

Guards:
  - caps are visible but hard-capped: set_cap can only RAISE the cap (floor
    DEFAULT_DAILY_CAP_USD never lowered below the shipped floor)
  - NO action can alter route logging (log_path/log_routes/log_max_bytes are
    filtered out) or the loop guard (not config-addressable)
  - every validation failure aborts BEFORE os.replace — a bad write can never
    land; the original config.yaml stays intact.

Fail-open contract: functions return (ok, detail) and never raise.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

WRITE_LOCK = threading.Lock()

CANONICAL_SECTION = "hermes_router"
LEGACY_SECTION = "uncensored_router"

# Fields no control action may ever write (route logging + guard integrity).
FORBIDDEN_KEYS = {"log_path", "log_routes", "log_max_bytes"}


def _config_path() -> str:
    """Path of the ACTIVE profile config.yaml. hermes_cli.config resolves the
    profile-scoped path; fall back to HERMES_HOME/config.yaml. Never raises."""
    try:
        from hermes_cli.config import CONFIG_PATH  # preferred: resolved path

        return str(CONFIG_PATH)
    except Exception:  # noqa: BLE001
        pass
    try:
        from hermes_cli import config as hc

        for attr in ("config_path", "_config_path", "get_config_path"):
            val = getattr(hc, attr, None)
            if callable(val):
                return str(val())
            if isinstance(val, (str, os.PathLike)):
                return str(val)
    except Exception:  # noqa: BLE001
        pass
    try:
        import hermes_constants

        return os.path.join(str(hermes_constants.get_hermes_home()), "config.yaml")
    except Exception:  # noqa: BLE001
        return os.path.join(os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes"),
                            "config.yaml")


def _try_yaml_load(text: str) -> Optional[Dict[str, Any]]:
    try:
        import yaml

        data = yaml.safe_load(text)
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _read_config(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise OSError(f"config unreadable: {exc}") from exc
    data = _try_yaml_load(text)
    if data is not None:
        return data
    # JSON-configured installs.
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError as exc:
        raise ValueError(f"config parse failed: {exc}") from exc
    raise ValueError("config is neither YAML-mappable nor JSON")


def _dump_config(data: Dict[str, Any]) -> str:
    try:
        import yaml

        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
    except Exception:  # noqa: BLE001
        return json.dumps(data, indent=2, ensure_ascii=False)


def read_plugin_section() -> Dict[str, Any]:
    """Read-only view of the plugin section (canonical first). Never raises."""
    try:
        data = _read_config(_config_path())
        section = data.get(CANONICAL_SECTION)
        if isinstance(section, dict) and section:
            return section
        section = data.get(LEGACY_SECTION)
        return section if isinstance(section, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def write_plugin_section(mutator, *, _path: Optional[str] = None) -> Tuple[bool, str]:
    """Atomically mutate the plugin section via `mutator(section_dict)`.

    mutator receives a COPY of the section and mutates it in place; return
    value may be None (use mutated copy) or a replacement dict. Validation:
      - forbidden keys filtered (route logging / loop guard untouched)
      - temp file written to the same dir, validated (yaml/json parse),
        then os.replace'd atomically.

    Returns (ok, detail). Never raises.
    """
    path = _path or _config_path()
    try:
        with WRITE_LOCK:
            original = _read_config(path)
            section = original.get(CANONICAL_SECTION)
            if not (isinstance(section, dict) and section):
                section = original.get(LEGACY_SECTION)
            section = dict(section) if isinstance(section, dict) else {}
            section.pop("_migrated_from", None)

            result = mutator(section)
            if isinstance(result, dict):
                section = result

            # Guard: strip forbidden keys the mutator may have sneaked in.
            for k in FORBIDDEN_KEYS:
                section.pop(k, None)

            updated = dict(original)
            # Legacy section migrates forward to canonical on first write.
            updated.pop(LEGACY_SECTION, None)
            if not section:
                updated.pop(CANONICAL_SECTION, None)
            else:
                updated[CANONICAL_SECTION] = section

            new_text = _dump_config(updated)

            # Validate BEFORE replace: parse must round-trip to a dict.
            roundtrip = _try_yaml_load(new_text)
            if roundtrip is None:
                try:
                    roundtrip = json.loads(new_text)
                except json.JSONDecodeError:
                    roundtrip = None
            if not isinstance(roundtrip, dict):
                return False, "validation_failed: written config does not parse"

            d = os.path.dirname(path) or "."
            os.makedirs(d, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".hermes-config-", dir=d)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(new_text)
                os.replace(tmp, path)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            return True, "ok"
    except Exception as exc:  # noqa: BLE001 — never raise into tool handlers
        logger.debug("config write failed: %s", exc)
        return False, f"error: {exc}"


def bump_cap(current_cap: float, requested_cap: float, floor: float) -> Tuple[bool, str, float]:
    """Cap is config-settable UP only. Returns (ok, detail, effective_cap).
    Never raises."""
    try:
        want = float(requested_cap)
        have = float(current_cap)
    except (TypeError, ValueError):
        return False, "cap must be a number", current_cap
    if want < 0:
        return False, "cap must be >= 0", current_cap
    if want < have or want < floor:
        return False, f"cap is UP-only (have={have}, floor={floor})", current_cap
    return True, "ok", want