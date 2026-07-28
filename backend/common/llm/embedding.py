"""
Embedding module — generate text embeddings for RAG vector search.

Uses shibing624/text2vec-base-chinese (768-dim) for semantic embedding.
Falls back to character n-gram hashing (768-dim) if model fails to load.

Supports runtime model switching via reload_model(model_path).
Configured via EMBEDDING_MODEL_PATH in .env or environment variables.
"""

import hashlib
import logging
import math
import os
import time
from collections import OrderedDict

from backend.common.config import EMBEDDING_MODEL_PATH, EMBEDDING_DIM, EMBEDDING_HF_ENDPOINT

logger = logging.getLogger(__name__)

# ── Embedding cache (LRU, max 256 entries) ────────────────────────────

_EMBED_CACHE: OrderedDict[str, list] = OrderedDict()
_EMBED_CACHE_MAX = 256

# ── Model singleton ────────────────────────────────────────────────────

_model = None
_model_loaded = False
_model_name = ""  # track which model is currently loaded


def _get_model():
    """Lazy-load the sentence-transformers model (singleton).

    The model is stored in a module-level global variable to prevent garbage collection.
    Once loaded, it will persist for the lifetime of the process.
    """
    global _model, _model_loaded, _model_name

    # Check if model is still valid (not garbage collected)
    if _model_loaded and _model is not None:
        return _model

    # Reset state if model was garbage collected
    if _model_loaded and _model is None:
        logger.warning("Embedding model was garbage collected, reloading...")
        _model_loaded = False

    _model_loaded = True

    # Default to HF mirror for China network
    if "HF_ENDPOINT" not in os.environ:
        os.environ["HF_ENDPOINT"] = EMBEDDING_HF_ENDPOINT

    model_path = EMBEDDING_MODEL_PATH

    try:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading embedding model: %s ...", model_path)
        _model = SentenceTransformer(model_path)
        _model_name = model_path
        logger.info("Embedding model loaded successfully (dim=%d).", EMBEDDING_DIM)
    except Exception as e:
        logger.warning("Failed to load embedding model: %s. Falling back to hash embedding.", e)
        _model = None
        _model_name = ""

    return _model


def reload_model(model_path: str = None) -> dict:
    """Reload the embedding model. If model_path is provided, switch to that model.

    Returns a dict with model info for API response.
    """
    global _model, _model_loaded, _model_name

    if model_path:
        # Update config at runtime
        import backend.common.config as cfg
        cfg.EMBEDDING_MODEL_PATH = model_path

    # Clear current model
    _model = None
    _model_loaded = False
    _model_name = ""

    # Clear embedding cache (old vectors are from previous model)
    _EMBED_CACHE.clear()

    # Reload
    t0 = time.time()
    model = _get_model()
    elapsed = round(time.time() - t0, 2)

    return get_model_info()


def get_model_info() -> dict:
    """Return current embedding model status."""
    from backend.common.config import EMBEDDING_MODEL_PATH as cfg_path, EMBEDDING_DIM as cfg_dim
    model = _model  # don't trigger lazy load
    return {
        "model_path": _model_name or cfg_path,
        "embedding_dim": cfg_dim,
        "model_loaded": model is not None,
        "model_type": "sentence-transformers" if model is not None else "hash-fallback",
    }


# ── Text2vec embedding (primary) ──────────────────────────────────────

def generate_embedding(text: str) -> list:
    """Generate a semantic embedding vector from text.

    Returns a 768-dimensional float vector using text2vec-base-chinese.
    Falls back to 768-dim hash embedding if model is unavailable.
    Results are cached (LRU, 256 entries).
    """
    if not text:
        return [0.0] * EMBEDDING_DIM

    # Check cache
    if text in _EMBED_CACHE:
        _EMBED_CACHE.move_to_end(text)
        return _EMBED_CACHE[text]

    model = _get_model()
    if model is not None:
        try:
            vec = model.encode(text, normalize_embeddings=True)
            result = vec.tolist()
            if len(result) != EMBEDDING_DIM:
                logger.warning(
                    "Model returned %d-dim vector, expected %d. Using as-is.",
                    len(result), EMBEDDING_DIM,
                )
            _EMBED_CACHE[text] = result
            if len(_EMBED_CACHE) > _EMBED_CACHE_MAX:
                _EMBED_CACHE.popitem(last=False)
            return result
        except Exception as e:
            logger.warning("Model encode failed: %s, falling back to hash.", e)

    # Fallback: n-gram hash embedding (256-dim)
    hash_vec = _hash_embedding(text)
    logger.warning(
        "Using hash fallback embedding (%d-dim). "
        "HNSW index expects %d-dim — vector search may fail. "
        "Install sentence-transformers or set EMBEDDING_MODEL_PATH.",
        len(hash_vec), EMBEDDING_DIM,
    )
    _EMBED_CACHE[text] = hash_vec
    if len(_EMBED_CACHE) > _EMBED_CACHE_MAX:
        _EMBED_CACHE.popitem(last=False)
    return hash_vec


# ── Hash embedding (fallback) ─────────────────────────────────────────

_HASH_DIM = EMBEDDING_DIM  # Must match EMBEDDING_DIM for HNSW index compatibility


def _text_to_ngrams(text, n=2):
    text = text.lower().strip()
    if len(text) < n:
        return [text] if text else []
    return [text[i:i + n] for i in range(len(text) - n + 1)]


def _hash_embedding(text: str) -> list:
    """Fallback: character bigram hashing to 768-dim vector."""
    ngrams = _text_to_ngrams(text, n=2)
    if not ngrams:
        return [0.0] * _HASH_DIM

    vec = [0.0] * _HASH_DIM
    freq = {}
    for ng in ngrams:
        freq[ng] = freq.get(ng, 0) + 1
    for ng, count in freq.items():
        h = int(hashlib.md5(ng.encode("utf-8")).hexdigest(), 16)
        pos = h % _HASH_DIM
        vec[pos] += 1.0 + math.log(count)

    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec


# ── Utility ───────────────────────────────────────────────────────────

def embedding_to_sql_literal(vec: list) -> str:
    """Convert embedding vector to a Doris SQL array literal."""
    return "[" + ", ".join(f"{x:.6f}" for x in vec) + "]"
