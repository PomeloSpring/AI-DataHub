"""Model Training API — Fine-tune embedding model using feedback data.

Provides endpoints to:
- Get training data statistics
- Preview training samples
- Start fine-tuning job
- List model versions
- Load/switch model version
"""

import json
import logging
import os
import time
from datetime import datetime
from typing import Optional

import pymysql
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.api.auth import get_current_user, require_admin
from backend.models.schemas import UserInfo
from backend.common.config import (
    DORIS_HOST, DORIS_PORT, DORIS_USER, DORIS_PASSWORD, METADATA_DB_DATABASE,
    EMBEDDING_MODEL_PATH,
)
from backend.common.db.metadata_db import get_metadata_conn

logger = logging.getLogger(__name__)
router = APIRouter()

MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "models")


def _get_metadata_conn():
    """Get a connection from the pool."""
    return get_metadata_conn()


class TrainRequest(BaseModel):
    epochs: int = 3
    batch_size: int = 16
    learning_rate: float = 2e-5
    use_lora: bool = True
    lora_rank: int = 8
    lora_alpha: int = 16


class LoadModelRequest(BaseModel):
    model_path: str


# ── Helpers ───────────────────────────────────────────────────────────

def _get_feedback_stats() -> dict:
    """Get feedback statistics."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM adh_search_feedback WHERE satisfied = 1")
            positive = cur.fetchone()["cnt"]
            cur.execute("SELECT COUNT(*) AS cnt FROM adh_search_feedback WHERE satisfied = 0")
            negative = cur.fetchone()["cnt"]
            cur.execute("SELECT COUNT(*) AS cnt FROM adh_search_feedback")
            total = cur.fetchone()["cnt"]
        return {"positive": positive, "negative": negative, "total": total}
    finally:
        conn.close()


def _build_training_data() -> list:
    """Build (query, positive_text, negative_text) triplets from feedback."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # Get positive feedback with table info
            cur.execute("""
                SELECT f.question, f.tables_used, f.expected_table, f.top_tables,
                       t.table_name, t.table_comment, t.keywords
                FROM adh_search_feedback f
                LEFT JOIN adh_table_info t ON FIND_IN_SET(t.table_name, f.tables_used) > 0
                    AND t.datasource_id = f.datasource_id AND t.is_active = 1
                WHERE f.satisfied = 1 AND f.tables_used != ''
            """)
            positive_rows = cur.fetchall()

            # Get negative feedback
            cur.execute("""
                SELECT f.question, f.tables_used, f.expected_table, f.top_tables,
                       t.table_name, t.table_comment, t.keywords
                FROM adh_search_feedback f
                LEFT JOIN adh_table_info t ON FIND_IN_SET(t.table_name, f.tables_used) > 0
                    AND t.datasource_id = f.datasource_id AND t.is_active = 1
                WHERE f.satisfied = 0 AND f.tables_used != ''
            """)
            negative_rows = cur.fetchall()

        # Build triplets
        triplets = []

        # From positive feedback: query -> used tables (positive), top but not used (negative)
        for row in positive_rows:
            query = row["question"]
            pos_text = f"{row['table_name']} {row.get('table_comment') or ''} {row.get('keywords') or ''}"
            if not pos_text.strip() or not row["table_name"]:
                continue
            # Find negative candidates from top_tables that weren't used
            used = set((row["tables_used"] or "").split(","))
            top = (row["top_tables"] or "").split(",")
            for t in top:
                if t and t not in used:
                    # Need to get this table's info
                    triplets.append({
                        "query": query,
                        "positive": pos_text.strip(),
                        "negative_table": t,
                        "source": "positive_feedback",
                    })

        # From negative feedback: expected table (positive), used tables (negative)
        for row in negative_rows:
            query = row["question"]
            neg_text = f"{row['table_name']} {row.get('table_comment') or ''} {row.get('keywords') or ''}"
            if not neg_text.strip() or not row["table_name"]:
                continue
            if row["expected_table"]:
                # Get expected table info
                conn2 = _get_metadata_conn()
                try:
                    with conn2.cursor() as cur2:
                        cur2.execute(
                            "SELECT table_name, table_comment, keywords FROM adh_table_info "
                            "WHERE table_name = %s AND is_active = 1 LIMIT 1",
                            (row["expected_table"],),
                        )
                        exp = cur2.fetchone()
                        if exp:
                            pos_text = f"{exp['table_name']} {exp.get('table_comment') or ''} {exp.get('keywords') or ''}"
                            triplets.append({
                                "query": query,
                                "positive": pos_text.strip(),
                                "negative": neg_text.strip(),
                                "source": "negative_feedback_with_expected",
                            })
                finally:
                    conn2.close()
            else:
                # No expected table - use top_tables as negative candidates
                top = (row["top_tables"] or "").split(",")
                for t in top:
                    if t and t != row["table_name"]:
                        triplets.append({
                            "query": query,
                            "positive_table": t,  # Will be resolved later
                            "negative": neg_text.strip(),
                            "source": "negative_feedback_top",
                        })

        # Resolve unresolved table references
        unresolved = set()
        for t in triplets:
            if "negative_table" in t:
                unresolved.add(t["negative_table"])
            if "positive_table" in t:
                unresolved.add(t["positive_table"])

        table_cache = {}
        if unresolved:
            conn3 = _get_metadata_conn()
            try:
                with conn3.cursor() as cur3:
                    placeholders = ", ".join(["%s"] * len(unresolved))
                    cur3.execute(
                        f"SELECT table_name, table_comment, keywords FROM adh_table_info "
                        f"WHERE table_name IN ({placeholders}) AND is_active = 1",
                        list(unresolved),
                    )
                    for r in cur3.fetchall():
                        table_cache[r["table_name"]] = f"{r['table_name']} {r.get('table_comment') or ''} {r.get('keywords') or ''}".strip()
            finally:
                conn3.close()

        # Finalize triplets
        final = []
        for t in triplets:
            if "negative_table" in t:
                neg_text = table_cache.get(t["negative_table"], "")
                if neg_text and t["positive"]:
                    final.append({"query": t["query"], "positive": t["positive"], "negative": neg_text})
            elif "positive_table" in t:
                pos_text = table_cache.get(t["positive_table"], "")
                if pos_text and t["negative"]:
                    final.append({"query": t["query"], "positive": pos_text, "negative": t["negative"]})
            elif "positive" in t and "negative" in t:
                final.append({"query": t["query"], "positive": t["positive"], "negative": t["negative"]})

        return final
    finally:
        conn.close()


# ── Endpoints ─────────────────────────────────────────────────────────

@router.get("/stats")
def training_stats(user: UserInfo = Depends(get_current_user)):
    """Get feedback and training data statistics."""
    stats = _get_feedback_stats()

    # List existing model versions
    versions = []
    if os.path.isdir(MODELS_DIR):
        for name in sorted(os.listdir(MODELS_DIR)):
            path = os.path.join(MODELS_DIR, name)
            if os.path.isdir(path):
                # Check if it's a valid model directory
                has_config = os.path.exists(os.path.join(path, "config.json"))
                versions.append({
                    "name": name,
                    "path": path,
                    "is_valid": has_config,
                    "created": datetime.fromtimestamp(os.path.getctime(path)).strftime("%Y-%m-%d %H:%M"),
                })

    # Estimate training samples
    try:
        triplets = _build_training_data()
        sample_count = len(triplets)
    except Exception as e:
        logger.warning("Failed to build training data: %s", e)
        sample_count = 0

    return {
        "feedback": stats,
        "training_samples": sample_count,
        "versions": versions,
        "current_model": EMBEDDING_MODEL_PATH,
    }


@router.get("/samples")
def preview_samples(limit: int = 20, user: UserInfo = Depends(get_current_user)):
    """Preview training samples."""
    triplets = _build_training_data()
    return {
        "total": len(triplets),
        "samples": triplets[:limit],
    }


@router.get("/template/csv")
def download_csv_template():
    """Download CSV template for training data upload."""
    from fastapi.responses import Response

    csv_content = "query,positive_table,negative_table\n"
    csv_content += "现在有多少用户,t_user_customer,open_user_account\n"
    csv_content += "最近一周的订单数,t_order_records,t_user_customer\n"

    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=training_template.csv"},
    )


@router.get("/template/json")
def download_json_template():
    """Download JSON template for training data upload."""
    from fastapi.responses import Response

    template = [
        {"query": "现在有多少用户", "positive_table": "t_user_customer", "negative_table": "open_user_account"},
        {"query": "最近一周的订单数", "positive_table": "t_order_records", "negative_table": "t_user_customer"},
    ]

    return Response(
        content=json.dumps(template, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=training_template.json"},
    )


@router.post("/upload")
async def upload_training_data(file: bytes = None, user: UserInfo = Depends(require_admin)):
    """Upload training data from CSV or JSON file.

    CSV format: query,positive_table,negative_table
    JSON format: [{"query": "...", "positive_table": "...", "negative_table": "..."}]

    Tables are referenced by name, will be resolved to embedding text automatically.
    """
    import csv
    import io

    if not file:
        raise HTTPException(status_code=400, detail="请上传文件")

    content = file.decode("utf-8-sig")  # Handle BOM

    # Try JSON first
    try:
        data = json.loads(content)
        if isinstance(data, list):
            rows = data
        else:
            raise HTTPException(status_code=400, detail="JSON 格式错误：应为数组")
    except json.JSONDecodeError:
        # Try CSV
        try:
            reader = csv.DictReader(io.StringIO(content))
            rows = list(reader)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"CSV 解析失败: {e}")

    # Validate
    required_fields = {"query", "positive_table", "negative_table"}
    errors = []
    valid_rows = []
    for i, row in enumerate(rows):
        missing = required_fields - set(row.keys())
        if missing:
            errors.append(f"第 {i+1} 行缺少字段: {missing}")
            continue
        if not row["query"] or not row["positive_table"] or not row["negative_table"]:
            errors.append(f"第 {i+1} 行有空字段")
            continue
        valid_rows.append(row)

    if errors and not valid_rows:
        raise HTTPException(status_code=400, detail="; ".join(errors[:5]))

    # Resolve table names to embedding text
    all_tables = set()
    for row in valid_rows:
        all_tables.add(row["positive_table"])
        all_tables.add(row["negative_table"])

    table_cache = {}
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            placeholders = ", ".join(["%s"] * len(all_tables))
            cur.execute(
                f"SELECT table_name, table_comment, keywords FROM adh_table_info "
                f"WHERE table_name IN ({placeholders}) AND is_active = 1",
                list(all_tables),
            )
            for r in cur.fetchall():
                table_cache[r["table_name"]] = (
                    f"{r['table_name']} {r.get('table_comment') or ''} {r.get('keywords') or ''}".strip()
                )
    finally:
        conn.close()

    # Build training samples
    samples = []
    skipped = []
    for row in valid_rows:
        pos_text = table_cache.get(row["positive_table"])
        neg_text = table_cache.get(row["negative_table"])
        if not pos_text:
            skipped.append(f"{row['positive_table']} (未找到)")
            continue
        if not neg_text:
            skipped.append(f"{row['negative_table']} (未找到)")
            continue
        samples.append({
            "query": row["query"],
            "positive": pos_text,
            "negative": neg_text,
            "source": "uploaded",
        })

    return {
        "success": True,
        "total_rows": len(rows),
        "valid_rows": len(valid_rows),
        "samples_generated": len(samples),
        "skipped_tables": skipped[:10] if skipped else [],
        "samples": samples[:20],  # Preview first 20
    }


@router.get("/all-samples")
def all_samples(user: UserInfo = Depends(get_current_user)):
    """Get all training samples (feedback + uploaded)."""
    feedback_samples = _build_training_data()

    # Check for uploaded samples file
    uploaded_path = os.path.join(MODELS_DIR, "uploaded_samples.json")
    uploaded_samples = []
    if os.path.exists(uploaded_path):
        try:
            with open(uploaded_path, "r") as f:
                uploaded_samples = json.load(f)
        except Exception:
            pass

    all_samples = feedback_samples + uploaded_samples
    return {
        "total": len(all_samples),
        "feedback_count": len(feedback_samples),
        "uploaded_count": len(uploaded_samples),
        "samples": all_samples[:50],
    }


@router.post("/save-uploaded")
def save_uploaded_samples(samples: list, admin: UserInfo = Depends(require_admin)):
    """Save uploaded training samples to file for later training."""
    os.makedirs(MODELS_DIR, exist_ok=True)
    path = os.path.join(MODELS_DIR, "uploaded_samples.json")

    # Merge with existing
    existing = []
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                existing = json.load(f)
        except Exception:
            pass

    # Deduplicate by query+positive+negative
    seen = set()
    merged = []
    for s in existing + samples:
        key = (s.get("query", ""), s.get("positive", ""), s.get("negative", ""))
        if key not in seen:
            seen.add(key)
            merged.append({"query": key[0], "positive": key[1], "negative": key[2]})

    with open(path, "w") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    return {"success": True, "total": len(merged)}


@router.post("/train")
def start_training(req: TrainRequest, user: UserInfo = Depends(require_admin)):
    """Start fine-tuning the embedding model.

    This runs synchronously (blocking). For production, consider using a background task.
    """
    triplets = _build_training_data()
    if len(triplets) < 10:
        raise HTTPException(
            status_code=400,
            detail=f"训练样本不足: {len(triplets)} 条，至少需要 10 条。请先积累更多用户反馈。",
        )

    # Check GPU availability
    try:
        import torch
        if not torch.cuda.is_available():
            raise HTTPException(
                status_code=400,
                detail="GPU 不可用，无法进行模型微调。需要 CUDA GPU。",
            )
        gpu_name = torch.cuda.get_device_name(0)
    except ImportError:
        raise HTTPException(status_code=400, detail="PyTorch 未安装，无法进行模型微调。")

    # Prepare training data
    from sentence_transformers import InputExample

    train_examples = []
    for t in triplets:
        train_examples.append(InputExample(texts=[t["query"], t["positive"], t["negative"]]))

    # Create model output directory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = os.path.join(MODELS_DIR, f"fine-tuned-{timestamp}")
    os.makedirs(output_dir, exist_ok=True)

    # Load base model
    from sentence_transformers import SentenceTransformer

    logger.info("Loading base model: %s", EMBEDDING_MODEL_PATH)
    model = SentenceTransformer(EMBEDDING_MODEL_PATH)

    # Apply LoRA if requested
    if req.use_lora:
        try:
            from peft import LoraConfig, get_peft_model

            lora_config = LoraConfig(
                r=req.lora_rank,
                lora_alpha=req.lora_alpha,
                target_modules=["query", "key", "value"],
                lora_dropout=0.1,
                bias="none",
            )
            model = get_peft_model(model, lora_config)
            logger.info("LoRA applied: rank=%d, alpha=%d", req.lora_rank, req.lora_alpha)
        except ImportError:
            logger.warning("peft not installed, falling back to full fine-tuning")

    # Train
    from torch.utils.data import DataLoader
    from sentence_transformers import losses

    train_dataloader = DataLoader(train_examples, shuffle=True, batch_size=req.batch_size)
    train_loss = losses.MultipleNegativesRankingLoss(model)

    logger.info("Starting training: %d samples, %d epochs, lr=%s",
                len(train_examples), req.epochs, req.learning_rate)

    model.fit(
        train_objectives=[(train_dataloader, train_loss)],
        epochs=req.epochs,
        warmup_steps=min(10, len(train_examples) // 4),
        output_path=output_dir,
        show_progress_bar=True,
    )

    # Save training metadata
    meta = {
        "base_model": EMBEDDING_MODEL_PATH,
        "timestamp": timestamp,
        "samples": len(triplets),
        "epochs": req.epochs,
        "batch_size": req.batch_size,
        "learning_rate": req.learning_rate,
        "use_lora": req.use_lora,
        "lora_rank": req.lora_rank if req.use_lora else None,
        "lora_alpha": req.lora_alpha if req.use_lora else None,
        "gpu": gpu_name,
    }
    with open(os.path.join(output_dir, "training_meta.json"), "w") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    logger.info("Training complete. Model saved to: %s", output_dir)

    return {
        "success": True,
        "model_path": output_dir,
        "samples": len(triplets),
        "epochs": req.epochs,
        "message": f"训练完成，模型已保存到 {output_dir}",
    }


@router.post("/load")
def load_model(req: LoadModelRequest, user: UserInfo = Depends(require_admin)):
    """Load/switch to a specific model version."""
    from backend.common.llm.embedding import reload_model, get_model_info

    if not os.path.isdir(req.model_path):
        raise HTTPException(status_code=400, detail=f"模型目录不存在: {req.model_path}")

    info = reload_model(req.model_path)

    return {
        "success": True,
        "model_info": info,
        "message": f"已切换到模型: {info['model_path']}",
    }


@router.delete("/versions/{version_name}")
def delete_version(version_name: str, user: UserInfo = Depends(require_admin)):
    """Delete a model version."""
    import shutil

    path = os.path.join(MODELS_DIR, version_name)
    if not os.path.isdir(path):
        raise HTTPException(status_code=404, detail="模型版本不存在")

    shutil.rmtree(path)
    return {"success": True, "message": f"已删除: {version_name}"}
