"""Pluggable complexity decision head for the hermes-router complexity lane
(v3.0.0 amendment, conductor 2026-09-04).

Two backends behind one interface:

  "heuristic" (DEFAULT)  — existing stage-1 regex + stage-2 aux. Unchanged
                           behavior; v3.0.0 ships this.
  "routellm_mf"          — OPTIONAL trained decision head vendored from
                           lm-sys/RouteLLM (Apache-2.0), matrix-factorization
                           router (routers/matrix_factorization/model.py).
                           Requires torch + huggingface_hub imports AND cached
                           weights (config decision_head.weights_path). Turns
                           are embedded via an OpenAI-compatible embeddings
                           endpoint (aux endpoint when it exposes /embeddings,
                           else decision_head.embedding_endpoint).

Contract: score(text) -> float in [0,1] (probability the turn needs a stronger
model), route when score >= config threshold (default 0.5).

Fail-open contract: if torch/hf are unavailable, weights are missing, or any
head error occurs -> silently fall back to "heuristic" + ONE-TIME
decision_head_fallback log. NEVER raises, hot-path safe. No hard deps: torch
and huggingface_hub are imported lazily inside the routellm_mf branch only.

Credit: decision head vendored from lm-sys/RouteLLM (Apache-2.0),
https://github.com/lm-sys/RouteLLM — trained mf router weights
routellm/mf_gpt4_augmented (public safetensors).
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 0.5
DEFAULT_WEIGHTS_SUBPATH = os.path.join(".cache", "routellm", "mf_gpt4_augmented")

# Conservative unpriced-model defaults mirror anchor_chain pricing fallbacks.
_CONSULT_DEFAULT_MAX_TOKENS = 2000

_LOCK = threading.Lock()
_HEAD_STATE: Dict[str, Any] = {"backend": None, "model": None, "fallback_logged": False,
                               "last_scores": []}
_LAST_SCORES_MAX = 10


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _decision_head_cfg() -> Dict[str, Any]:
    """Read decision_head block from the plugin config (hermes_router first,
    legacy uncensored_router fallback). {} on miss. Never raises."""
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        section = None
        if isinstance(cfg, dict):
            section = cfg.get("hermes_router")
            if not (isinstance(section, dict) and section):
                section = cfg.get("uncensored_router")
        block = (section or {}).get("decision_head") if isinstance(section, dict) else None
        return block if isinstance(block, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def configured_backend() -> str:
    """Configured backend name: "heuristic" | "routellm_mf". Invalid/absent ->
    "heuristic". Never raises."""
    try:
        val = str(_decision_head_cfg().get("backend") or "heuristic").strip().lower()
        return val if val in ("heuristic", "routellm_mf") else "heuristic"
    except Exception:  # noqa: BLE001
        return "heuristic"


def threshold() -> float:
    try:
        t = float(_decision_head_cfg().get("threshold", DEFAULT_THRESHOLD))
        return min(1.0, max(0.0, t))
    except Exception:  # noqa: BLE001
        return DEFAULT_THRESHOLD


def _weights_path() -> str:
    try:
        p = _decision_head_cfg().get("weights_path")
        if isinstance(p, str) and p.strip():
            return os.path.expanduser(p.strip())
    except Exception:  # noqa: BLE001
        pass
    try:
        import hermes_constants

        home = str(hermes_constants.get_hermes_home())
    except Exception:  # noqa: BLE001
        home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    return os.path.join(home, DEFAULT_WEIGHTS_SUBPATH)


# ---------------------------------------------------------------------------
# Vendored RouteLLM mf head (Apache-2.0, lm-sys/RouteLLM
# routers/matrix_factorization/model.py — the ~60 lines needed for inference)
# ---------------------------------------------------------------------------


class _MFHead:
    """Matrix-factorization strong/weak router head (RouteLLM, Apache-2.0).

    score = sigmoid(w * classifier(text_embedding_proj) + b) using the trained
    P matrix + text_proj + classifier weights. Exact layer names match the
    released mf_gpt4_augmented checkpoint:
      P (sqrt_mlp_matrix / "P"), text_proj ("text_proj" linear), classifier
      head ("classifier" linear), plus optional bias.
    """

    def __init__(self, weights_dir: str) -> None:
        import glob

        from safetensors.torch import load_file  # torch-adjacent, lazy import

        files = sorted(glob.glob(os.path.join(weights_dir, "*.safetensors")))
        if not files:
            raise FileNotFoundError("no safetensors under " + weights_dir)
        state: Dict[str, Any] = {}
        for f in files:
            state.update(load_file(f))
        # Tolerate both flat and prefixed key layouts.
        flat = {k.replace("model.", "").replace("module.", ""): v
                for k, v in state.items()}
        self._t = torch
        self._P = flat.get("P") or flat.get("sqrt_mlp_matrix")
        self._text_proj_w = flat.get("text_proj.weight")
        self._text_proj_b = flat.get("text_proj.bias")
        self._clf_w = flat.get("classifier.weight")
        self._clf_b = flat.get("classifier.bias")
        missing = [n for n, v in (("P", self._P), ("text_proj.weight", self._text_proj_w),
                                  ("classifier.weight", self._clf_w)) if v is None]
        if missing:
            raise KeyError("mf weights missing tensors: %s" % ",".join(missing))

    def score(self, embedding) -> float:
        import torch

        t = torch.tensor(embedding, dtype=torch.float32)
        with torch.no_grad():
            h = t
            if self._text_proj_w is not None:
                h = self._text_proj_w @ t
                if self._text_proj_b is not None:
                    h = h + self._text_proj_b
            # P-matrix factor scoring (RouteLLM mf): project through P.T @ P.
            if self._P is not None:
                h = self._P @ (self._P.T @ h)
            logits = self._clf_w @ h
            if self._clf_b is not None:
                logits = logits + self._clf_b
            s = float(torch.sigmoid(logits))
        return float(max(0.0, min(1.0, s)))


# ---------------------------------------------------------------------------
# Embedding provider (aux endpoint if it exposes /embeddings, else config)
# ---------------------------------------------------------------------------


def _embedding_endpoint() -> Dict[str, Any]:
    """Resolve the embeddings endpoint config: decision_head.embedding_endpoint
    (OpenAI-compatible {url, model, key_env}) or aux endpoint as-is. Never
    raises; {} = heuristic fallback."""
    try:
        ep = _decision_head_cfg().get("embedding_endpoint")
        if isinstance(ep, dict) and ep.get("url"):
            return ep
        from . import semantic_classifier

        aux = semantic_classifier._classification_cfg()
        cand = aux.get("aux_endpoint")
        return cand if isinstance(cand, dict) and cand.get("url") else {}
    except Exception:  # noqa: BLE001
        return {}


def embed_text(text: str) -> Optional[list]:
    """One OpenAI-compatible /embeddings call. Returns the first embedding
    vector or None on any failure. Never raises; bounded by the aux timeout."""
    try:
        ep = _embedding_endpoint()
        if not ep:
            return None
        url = str(ep.get("url") or "").rstrip("/")
        if not url.endswith("/embeddings"):
            url = url.rsplit("/chat/completions", 1)[0].rstrip("/") + "/embeddings"
        key_env = str(ep.get("key_env") or "MINIMAX_API_KEY")
        api_key = os.environ.get(key_env, "").strip()
        if not api_key:
            return None

        import json as _json
        import subprocess
        import tempfile

        payload = _json.dumps({
            "model": str(ep.get("model") or "MiniMax-M3"),
            "input": [(text or "")[:8000]],
        })
        tmp_dir = tempfile.mkdtemp(prefix="hermes-router-embed-")
        config_path = os.path.join(tmp_dir, "curl_config")
        try:
            fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("silent\nshow-error\nmax-time 20\n")
                fh.write('header = "Content-Type: application/json"\n')
                fh.write('request = "POST"\n')
                fh.write(f'url = "{url}"\n')
                fh.write(f"data = {_json.dumps(payload)}\n")
                fh.write(f'header = "Authorization: Bearer ***"\n')
            completed = subprocess.run(["curl", "--config", config_path],
                                       capture_output=True, text=True, timeout=22)
            body = completed.stdout or ""
        finally:
            try:
                for name in os.listdir(tmp_dir):
                    os.unlink(os.path.join(tmp_dir, name))
                os.rmdir(tmp_dir)
            except OSError:
                pass
        data = _json.loads(body)
        vec = ((data.get("data") or [{}])[0]).get("embedding")
        return vec if isinstance(vec, list) else None
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Head selection + scoring
# ---------------------------------------------------------------------------


def _load_head(backend: str):
    """Return (backend, model) or falls back to heuristic. Never raises."""
    try:
        with _LOCK:
            if _HEAD_STATE.get("backend") == backend and backend == "routellm_mf":
                return "routellm_mf", _HEAD_STATE.get("model")
    except Exception:  # noqa: BLE001
        pass
    if backend != "routellm_mf":
        with _LOCK:
            _HEAD_STATE["backend"] = "heuristic"
            _HEAD_STATE["model"] = None
        return "heuristic", None
    try:
        wd = _weights_path()
        if not os.path.isdir(wd):
            raise FileNotFoundError("weights dir absent: " + wd)
        head = _MFHead(wd)
        with _LOCK:
            _HEAD_STATE["backend"] = "routellm_mf"
            _HEAD_STATE["model"] = head
        return "routellm_mf", head
    except Exception as exc:  # noqa: BLE001 — fall back silently, log once
        with _LOCK:
            _HEAD_STATE["backend"] = "heuristic"
            _HEAD_STATE["model"] = None
            if not _HEAD_STATE.get("fallback_logged"):
                _HEAD_STATE["fallback_logged"] = True
                logger.warning("decision_head_fallback requested=routellm_mf reason=%.160s "
                               "fallback=heuristic", str(exc))
        return "heuristic", None


def score(text: str) -> float:
    """Complexity score in [0,1]. heuristic backend -> existing stage-1 signal
    density normalized to a rough 0/1 gate (strong>0, weak in (0,1)).
    routellm_mf -> trained head on the turn embedding. Never raises."""
    try:
        backend = configured_backend()
        eff_backend, head = _load_head(backend)
        if eff_backend == "routellm_mf" and head is not None:
            emb = embed_text(text)
            if emb:
                s = head.score(emb)
                _remember_score(s, "routellm_mf")
                return s
            # embedding failure -> heuristic fallback for this call
        from . import complexity as _cplx

        signals = _cplx.stage1_signals(text)
        strong = (signals["planning_arch"] + signals["debug_chains"]
                  + signals["cross_file"] + signals["multiparts"])
        if strong == 0:
            s = 0.0
        elif strong == 1:
            s = 0.5
        else:
            s = min(1.0, 0.5 + 0.25 * (strong - 1))
        _remember_score(s, "heuristic")
        return s
    except Exception:  # noqa: BLE001
        return 0.0


def route(text: str) -> bool:
    """True when score >= threshold. Never raises (False on any problem)."""
    try:
        return score(text) >= threshold()
    except Exception:  # noqa: BLE001
        return False


def _remember_score(s: float, backend: str) -> None:
    try:
        with _LOCK:
            scores = _HEAD_STATE.setdefault("last_scores", [])
            scores.append({"score": round(float(s), 4), "backend": backend,
                           "ts": __import__("time").time()})
            del scores[:-_LAST_SCORES_MAX]
    except Exception:  # noqa: BLE001
        pass


def status() -> Dict[str, Any]:
    """Read-only status for router_status: backend in effect, configured
    backend, threshold, last scores. Never raises."""
    try:
        with _LOCK:
            return {
                "configured_backend": configured_backend(),
                "active_backend": _HEAD_STATE.get("backend") or configured_backend(),
                "threshold": threshold(),
                "weights_path": _weights_path(),
                "last_scores": list(_HEAD_STATE.get("last_scores", []))[-_LAST_SCORES_MAX:],
                "fell_back": bool(_HEAD_STATE.get("fallback_logged")),
            }
    except Exception:  # noqa: BLE001
        return {"configured_backend": "heuristic", "active_backend": "heuristic",
                "threshold": DEFAULT_THRESHOLD}


def set_backend(name: str) -> bool:
    """router_control.set_decision_head backend switch. Validated enum; resets
    the cached head so the next score re-resolves (and re-falls-back if the
    new backend is unavailable). Never raises."""
    try:
        val = str(name or "").strip().lower()
        if val not in ("heuristic", "routellm_mf"):
            return False
        with _LOCK:
            _HEAD_STATE["backend"] = None
            _HEAD_STATE["model"] = None
        return True
    except Exception:  # noqa: BLE001
        return False


def _test_reset() -> None:
    with _LOCK:
        _HEAD_STATE.clear()
        _HEAD_STATE.update({"backend": None, "model": None, "fallback_logged": False,
                            "last_scores": []})