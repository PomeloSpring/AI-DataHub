"""Embedding Model Manager — install, uninstall, list, and validate embedding models.

Default model list: 5 preset models + locally downloaded models (deduped).
HuggingFace API search is opt-in (user triggers via separate endpoint).

When setting as active model, validates that the model outputs 768-dim vectors.
Models are stored in the HuggingFace cache directory (default ~/.cache/huggingface).
"""

import logging
import os
import shutil
import time
import signal
import threading
from pathlib import Path
from functools import wraps
from typing import Optional

from services.shared.common.config import EMBEDDING_MODEL_PATH, EMBEDDING_MODEL_CACHE_DIR, EMBEDDING_DIM

logger = logging.getLogger(__name__)

# ── Install progress tracking ──────────────────────────────────────────

_install_progress: dict[str, dict] = {}
_install_lock = threading.Lock()


def get_install_progress(model_id: str) -> Optional[dict]:
    """Get current install progress for a model. Returns None if not installing."""
    with _install_lock:
        return _install_progress.get(model_id)


def _set_progress(model_id: str, **kwargs):
    with _install_lock:
        if model_id not in _install_progress:
            _install_progress[model_id] = {"status": "downloading", "percent": 0, "message": ""}
        _install_progress[model_id].update(kwargs)


def _clear_progress(model_id: str):
    with _install_lock:
        _install_progress.pop(model_id, None)


class _InstallProgressCallback:
    """Callback for snapshot_download to track progress."""

    def __init__(self, model_id: str):
        self.model_id = model_id
        self._last_update = 0

    def __call__(self, progress_info):
        """Called by huggingface_hub progress hooks."""
        now = time.time()
        if now - self._last_update < 0.5:  # throttle to 2 updates/sec
            return
        self._last_update = now

        try:
            if hasattr(progress_info, 'total') and hasattr(progress_info, 'completed'):
                total = progress_info.total or 0
                completed = progress_info.completed or 0
                if total > 0:
                    percent = min(99, int(completed * 100 / total))
                    _set_progress(
                        self.model_id,
                        percent=percent,
                        message=f"下载中 {percent}%  ({_format_size(completed)} / {_format_size(total)})",
                    )
                else:
                    _set_progress(
                        self.model_id,
                        percent=0,
                        message=f"下载中 {_format_size(completed)}...",
                    )
        except Exception:
            pass


# ── Preset models (always shown, 5 models) ────────────────────────────

PRESET_MODELS = [
    {
        "id": "shibing624/text2vec-base-chinese",
        "name": "text2vec-base-chinese",
        "description": "轻量级中文语义向量模型，基于 CoSENT 训练",
        "dim": 768,
        "tags": ["默认", "轻量"],
    },
    {
        "id": "BAAI/bge-base-zh-v1.5",
        "name": "bge-base-zh-v1.5",
        "description": "智源 BGE 中文基座模型，C-MTEB 排行榜前列",
        "dim": 768,
        "tags": ["推荐", "高精度"],
    },
    {
        "id": "moka-ai/m3e-base",
        "name": "m3e-base",
        "description": "M3E 中文语义向量模型，通用场景",
        "dim": 768,
        "tags": ["通用"],
    },
    {
        "id": "shibing624/text2vec-base-chinese-paraphrase",
        "name": "text2vec-base-chinese-paraphrase",
        "description": "中文改写向量模型，适合语义相似度",
        "dim": 768,
        "tags": ["改写"],
    },
]

_PRESET_IDS = {m["id"] for m in PRESET_MODELS}


# ── Cache directory helpers ────────────────────────────────────────────

def _get_cache_dir() -> Path:
    if EMBEDDING_MODEL_CACHE_DIR:
        return Path(EMBEDDING_MODEL_CACHE_DIR)
    return Path.home() / ".cache" / "huggingface" / "hub"


def _model_cache_path(model_id: str) -> Path:
    cache_name = "models--" + model_id.replace("/", "--")
    return _get_cache_dir() / cache_name


def is_model_installed(model_id: str) -> bool:
    cache_path = _model_cache_path(model_id)
    if not cache_path.exists():
        return False
    try:
        snapshots = cache_path / "snapshots"
        if snapshots.exists() and any(snapshots.iterdir()):
            return True
    except Exception:
        pass
    return False


def get_model_size(model_id: str) -> int:
    cache_path = _model_cache_path(model_id)
    if not cache_path.exists():
        return 0
    total = 0
    try:
        for f in cache_path.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    except Exception:
        pass
    return total


# ── Local model scanning ──────────────────────────────────────────────

def _scan_local_models() -> list:
    """Scan cache dir for downloaded models not in presets."""
    cache_dir = _get_cache_dir()
    if not cache_dir.exists():
        return []

    local_models = []
    try:
        for entry in cache_dir.iterdir():
            if not entry.is_dir() or not entry.name.startswith("models--"):
                continue
            model_id = entry.name[len("models--"):].replace("--", "/")
            if model_id in _PRESET_IDS:
                continue  # skip presets, they're already in the list
            snapshots = entry / "snapshots"
            if snapshots.exists() and any(snapshots.iterdir()):
                local_models.append(model_id)
    except Exception as e:
        logger.warning("Failed to scan local model cache: %s", e)

    return local_models


def _build_local_model_entry(model_id: str) -> dict:
    return {
        "id": model_id,
        "name": model_id.split("/")[-1] if "/" in model_id else model_id,
        "description": "本地已下载模型",
        "dim": None,  # unknown until loaded
        "tags": ["本地"],
    }


# ── Default model list (presets + local) ──────────────────────────────

def list_models() -> list:
    """List preset models + locally downloaded models (deduped)."""
    model_map = {}

    # 1. Preset models
    for m in PRESET_MODELS:
        model_map[m["id"]] = m

    # 2. Local models not in presets
    for local_id in _scan_local_models():
        if local_id not in model_map:
            model_map[local_id] = _build_local_model_entry(local_id)

    # 3. Enrich with local status
    return _enrich_with_status(model_map)


_HF_API_TIMEOUT = 10  # seconds per query


def _run_with_timeout(func, timeout_sec: int):
    """Run func() with a timeout. Raises TimeoutError if exceeded.

    Uses signal.SIGALRM on Unix, falls back to no timeout on Windows.
    """
    if os.name == "nt":
        # Windows: no SIGALRM, just run directly
        return func()

    def _handler(signum, frame):
        raise TimeoutError("操作超时")

    old_handler = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(timeout_sec)
    try:
        result = func()
        signal.alarm(0)
        return result
    except TimeoutError:
        raise
    finally:
        signal.signal(signal.SIGALRM, old_handler)


def search_online_models(timeout: int = 15) -> list:
    """Query HuggingFace API for sentence-transformers models (opt-in).

    Args:
        timeout: Total timeout in seconds for all API queries.
    """
    try:
        from huggingface_hub import HfApi
    except ImportError:
        raise RuntimeError("huggingface_hub 未安装")

    if "HF_ENDPOINT" not in os.environ:
        from services.shared.common.config import EMBEDDING_HF_ENDPOINT
        os.environ["HF_ENDPOINT"] = EMBEDDING_HF_ENDPOINT

    api = HfApi()
    seen = set()
    results = []
    t0 = time.time()

    for query in ["chinese sentence embedding", "text2vec chinese", "bge chinese"]:
        # Check remaining time
        elapsed = time.time() - t0
        if elapsed >= timeout:
            logger.warning("HF API search timeout (%ds reached), returning partial results", timeout)
            break

        remaining = max(3, timeout - int(elapsed))
        try:
            def _fetch(q=query):
                return list(api.list_models(
                    search=q,
                    pipeline_tag="sentence-similarity",
                    sort="downloads",
                    direction=-1,
                    limit=15,
                ))

            models = _run_with_timeout(_fetch, remaining)
            for m in models:
                mid = m.id or m.modelId
                if mid in seen:
                    continue
                seen.add(mid)
                results.append({
                    "id": mid,
                    "name": mid.split("/")[-1] if "/" in mid else mid,
                    "description": getattr(m, "pipeline_tag", "") or "",
                    "dim": None,
                    "downloads": getattr(m, "downloads", 0) or 0,
                    "likes": getattr(m, "likes", 0) or 0,
                    "tags": [],
                })
        except TimeoutError:
            logger.warning("HF API query '%s' timed out after %ds", query, remaining)
            break
        except Exception as e:
            logger.debug("HF API query '%s' failed: %s", query, e)

    results.sort(key=lambda x: x.get("downloads", 0), reverse=True)
    return results[:30]


# ── Enrich models with install/active status ──────────────────────────

def _enrich_with_status(model_map: dict) -> list:
    models = []
    for mid, info in model_map.items():
        installed = is_model_installed(mid)
        is_active = mid == EMBEDDING_MODEL_PATH
        local_size = get_model_size(mid) if installed else 0

        status = "active" if is_active else ("installed" if installed else "available")
        preset = next((p for p in PRESET_MODELS if p["id"] == mid), None)

        models.append({
            "id": mid,
            "name": info.get("name", mid.split("/")[-1]),
            "description": info.get("description", ""),
            "dim": info.get("dim"),
            "tags": info.get("tags", []),
            "downloads": info.get("downloads", 0),
            "likes": info.get("likes", 0),
            "status": status,
            "installed": installed,
            "active": is_active,
            "local_size_bytes": local_size,
            "local_size_display": _format_size(local_size),
        })

    def _sort_key(m):
        status_order = {"active": 0, "installed": 1, "available": 2}
        return (status_order.get(m["status"], 9), -m.get("downloads", 0))

    models.sort(key=_sort_key)
    return models


# ── Install / Uninstall ───────────────────────────────────────────────

def install_model(model_id: str, hf_endpoint: str = None) -> dict:
    """Download a model from HuggingFace Hub (or configured mirror).

    Args:
        model_id: HuggingFace repo ID (e.g. "BAAI/bge-base-zh-v1.5"),
                  or a custom endpoint URL like "https://mirror.example.com/org/model".
        hf_endpoint: Optional custom HuggingFace endpoint (mirror URL).
                     If None, uses EMBEDDING_HF_ENDPOINT from config.
    """
    # Support custom mirror endpoint
    endpoint = hf_endpoint or os.environ.get("HF_ENDPOINT") or ""
    if not endpoint:
        from services.shared.common.config import EMBEDDING_HF_ENDPOINT
        endpoint = EMBEDDING_HF_ENDPOINT

    # Set endpoint env for huggingface_hub
    os.environ["HF_ENDPOINT"] = endpoint

    cache_dir = str(_get_cache_dir())

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        raise RuntimeError("huggingface_hub 未安装，请执行 pip install huggingface_hub")

    logger.info("Downloading model %s (endpoint=%s) to %s ...", model_id, endpoint, cache_dir)

    _set_progress(model_id, percent=0, message="准备下载...")

    t0 = time.time()
    try:
        callback = _InstallProgressCallback(model_id)
        path = snapshot_download(
            repo_id=model_id,
            cache_dir=cache_dir,
            local_files_only=False,
        )
        elapsed = round(time.time() - t0, 1)
        logger.info("Model %s downloaded in %.1fs to %s", model_id, elapsed, path)

        local_size = get_model_size(model_id)
        _set_progress(model_id, percent=100, status="done", message=f"安装完成 ({_format_size(local_size)})")

        return {
            "success": True,
            "model_id": model_id,
            "path": path,
            "elapsed_seconds": elapsed,
            "local_size_bytes": local_size,
            "local_size_display": _format_size(local_size),
        }
    except Exception as e:
        logger.error("Failed to download model %s: %s", model_id, e)
        _set_progress(model_id, percent=0, status="error", message=f"安装失败: {e}")
        raise RuntimeError(f"模型下载失败: {e}")
    finally:
        # Auto-clear after 30 seconds
        threading.Timer(30, _clear_progress, args=[model_id]).start()


def start_install_async(model_id: str, hf_endpoint: str = None) -> str:
    """Start model installation in background thread. Returns model_id immediately.

    Use get_install_progress(model_id) to poll progress.
    """
    existing = get_install_progress(model_id)
    if existing and existing.get("status") == "downloading":
        return model_id  # already installing

    def _run():
        try:
            install_model(model_id, hf_endpoint)
        except Exception:
            pass  # error is tracked in progress

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return model_id


def uninstall_model(model_id: str) -> dict:
    cache_path = _model_cache_path(model_id)
    if not cache_path.exists():
        return {"success": False, "message": f"模型 {model_id} 未安装"}

    if model_id == EMBEDDING_MODEL_PATH:
        return {"success": False, "message": "不能卸载当前正在使用的模型，请先切换到其他模型"}

    try:
        shutil.rmtree(cache_path)
        logger.info("Uninstalled model %s (removed %s)", model_id, cache_path)
        return {"success": True, "model_id": model_id}
    except Exception as e:
        logger.error("Failed to uninstall model %s: %s", model_id, e)
        return {"success": False, "message": f"卸载失败: {e}"}


# ── Set active model with validation ──────────────────────────────────

def set_active_model(model_path: str) -> dict:
    """Set a model as the active system embedding model.

    Loads the model, validates output dimension matches EMBEDDING_DIM (768).
    Returns model info on success, raises on failure.
    """
    # 1. Load the model and check dimension
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        raise RuntimeError("sentence_transformers 未安装")

    # Set HF endpoint for mirror download
    if "HF_ENDPOINT" not in os.environ:
        from services.shared.common.config import EMBEDDING_HF_ENDPOINT
        os.environ["HF_ENDPOINT"] = EMBEDDING_HF_ENDPOINT

    logger.info("Validating model %s (expecting %d-dim output)...", model_path, EMBEDDING_DIM)
    try:
        model = SentenceTransformer(model_path)
    except Exception as e:
        raise RuntimeError(f"模型加载失败: {e}")

    # 2. Test encode and check dimension
    try:
        test_vec = model.encode("测试文本", normalize_embeddings=True)
        actual_dim = len(test_vec)
    except Exception as e:
        raise RuntimeError(f"模型推理失败: {e}")

    if actual_dim != EMBEDDING_DIM:
        raise RuntimeError(
            f"模型输出维度不匹配：期望 {EMBEDDING_DIM} 维，实际 {actual_dim} 维。"
            f"请使用 {EMBEDDING_DIM} 维的模型，或修改 EMBEDDING_DIM 配置。"
        )

    # 3. Validation passed — reload in embedding module
    from services.shared.common.llm.embedding import reload_model
    info = reload_model(model_path)

    logger.info("Active embedding model set to %s (dim=%d)", model_path, actual_dim)
    return info


# ── Utility ───────────────────────────────────────────────────────────

def _format_size(size_bytes: int) -> str:
    if size_bytes <= 0:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"
