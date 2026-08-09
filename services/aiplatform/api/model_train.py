"""Model Train API — Fine-tune embedding model using feedback data.

Migrated from backend/api/model_train.py
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from services.shared.common.db import DBConnection, execute_query

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/stats")
def get_training_stats():
    """Get feedback statistics for training."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS cnt FROM adh_search_feedback WHERE satisfied = 1")
                positive = cur.fetchone()["cnt"]
                cur.execute("SELECT COUNT(*) AS cnt FROM adh_search_feedback WHERE satisfied = 0")
                negative = cur.fetchone()["cnt"]
                cur.execute("SELECT COUNT(*) AS cnt FROM adh_search_feedback")
                total = cur.fetchone()["cnt"]
        return {"positive": positive, "negative": negative, "total": total}
    except Exception as e:
        logger.error("Get training stats failed: %s", e)
        return {"positive": 0, "negative": 0, "total": 0}


@router.get("/samples")
def get_training_samples(limit: int = Query(20, ge=1, le=100)):
    """Preview training samples."""
    try:
        rows = execute_query(
            """SELECT id, question, tables_used, expected_table, satisfied, created_at
               FROM adh_search_feedback
               ORDER BY created_at DESC LIMIT %s""",
            (limit,),
        )
        for r in rows:
            if hasattr(r.get("created_at"), "isoformat"):
                r["created_at"] = r["created_at"].isoformat()
        return rows
    except Exception as e:
        logger.error("Get training samples failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/versions")
def list_model_versions():
    """List trained model versions."""
    try:
        import os
        models_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))), "models")
        if not os.path.exists(models_dir):
            return []
        versions = []
        for d in sorted(os.listdir(models_dir), reverse=True):
            model_path = os.path.join(models_dir, d)
            if os.path.isdir(model_path):
                versions.append({
                    "name": d,
                    "path": model_path,
                    "created_at": os.path.getctime(model_path),
                })
        return versions
    except Exception as e:
        logger.error("List model versions failed: %s", e)
        return []


@router.post("/start")
def start_training():
    """Start model fine-tuning job."""
    # TODO: Implement actual training
    return {"status": "not_implemented", "message": "Training not yet implemented"}


@router.post("/load")
def load_model_version(data: dict):
    """Load a specific model version."""
    # TODO: Implement model loading
    return {"status": "not_implemented", "message": "Model loading not yet implemented"}
