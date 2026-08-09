"""Ontology Modeling Service — FDE 对象中心本体（本地轻量实现）。

工作流：页面触发 LLM 生成草案(JSON 事实源) → 用户检查/编辑 → 确认激活 →
逐对象 MD 段向量化写入 adh_ontology_objects（Doris HNSW），供 ontology_first 检索。

三格式：JSON（接口流转，唯一事实源）/ YAML（结构可读）/ MD（向量化与 AI 识别）。
保存 JSON 时服务端重新派生 YAML/MD，保证三格式一致。
"""

import json
import logging
import re
import time
from datetime import datetime
from typing import Optional

import yaml

from services.shared.common.db.metadata_db import get_metadata_conn

logger = logging.getLogger(__name__)

# 每批送入 LLM 的最大表数（超出按 domain_tag 分批后合并）
BATCH_MAX_TABLES = 20


def _gen_id() -> int:
    return int(time.time() * 1000000)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ═══════════════════════════════════════════════════════════════════
# 元数据采集（生成输入）
# ═══════════════════════════════════════════════════════════════════

def _collect_metadata(datasource_id: int) -> dict:
    """采集数据源的表/列/术语/指标/关系，作为本体生成输入。"""
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name, table_comment, table_business_desc, keywords, "
                "domain_tag, region_tag FROM adh_table_info "
                "WHERE datasource_id = %s AND is_active = 1 ORDER BY table_name",
                (datasource_id,),
            )
            tables = cur.fetchall()

            cur.execute(
                "SELECT table_name, column_name, data_type, column_comment, "
                "business_desc, is_key FROM adh_column_metadata "
                "WHERE datasource_id = %s AND is_active = 1",
                (datasource_id,),
            )
            columns = cur.fetchall()

            cur.execute(
                "SELECT term_cn, term_en, term_aliases, term_type, target_table, "
                "target_column, calculation, description FROM adh_business_terms "
                "WHERE datasource_id = %s AND is_active = 1 LIMIT 200",
                (datasource_id,),
            )
            terms = cur.fetchall()

            cur.execute(
                "SELECT source_table, source_column, target_table, target_column, "
                "relation_type, join_type, description FROM adh_table_relations "
                "WHERE datasource_id = %s AND is_active = 1 LIMIT 200",
                (datasource_id,),
            )
            relations = cur.fetchall()

            metrics = []
            try:
                cur.execute(
                    "SELECT metric_name, metric_display_name, metric_type, "
                    "calculation_logic, description FROM adh_metrics "
                    "WHERE datasource_id = %s AND is_active = 1 LIMIT 200",
                    (datasource_id,),
                )
                metrics = cur.fetchall()
            except Exception as e:
                logger.warning("metrics collect skipped: %s", e)

    col_map: dict[str, list] = {}
    for c in columns:
        col_map.setdefault(c["table_name"], []).append(c)
    for t in tables:
        t["columns"] = col_map.get(t["table_name"], [])

    return {
        "tables": tables,
        "terms": terms,
        "metrics": metrics,
        "relations": relations,
    }


def _schema_text(batch_tables: list, meta: dict) -> str:
    """把一批表及其列、相关术语/指标/关系拼成 LLM 输入文本。"""
    table_names = {t["table_name"] for t in batch_tables}
    lines = []
    for t in batch_tables:
        header = f"Table: {t['table_name']}"
        comment = t.get("table_business_desc") or t.get("table_comment") or ""
        if comment:
            header += f" -- {comment}"
        lines.append(header)
        lines.append("Columns:")
        for c in t.get("columns", []):
            line = f"  - {c['column_name']} ({c['data_type']})"
            desc = c.get("business_desc") or c.get("column_comment") or ""
            if desc:
                line += f" -- {desc}"
            if c.get("is_key") == "true":
                line += " [KEY]"
            lines.append(line)
        lines.append("")

    rels = [r for r in meta["relations"]
            if r["source_table"] in table_names or r["target_table"] in table_names]
    if rels:
        lines.append("Known relations:")
        for r in rels:
            lines.append(
                f"  - {r['source_table']}.{r['source_column']} -> "
                f"{r['target_table']}.{r['target_column']} "
                f"({r.get('relation_type', '1:N')}, {r.get('join_type', 'INNER')})"
            )
        lines.append("")

    terms = [tm for tm in meta["terms"]
             if not tm.get("target_table") or tm["target_table"] in table_names]
    if terms:
        lines.append("Business terms:")
        for tm in terms[:60]:
            line = f"  - {tm['term_cn']}"
            if tm.get("term_aliases"):
                line += f"(别名: {tm['term_aliases']})"
            if tm.get("description"):
                line += f": {tm['description']}"
            if tm.get("calculation"):
                line += f" [口径: {tm['calculation']}]"
            lines.append(line)
        lines.append("")

    metrics = [m for m in meta["metrics"]]
    if metrics:
        lines.append("Metrics:")
        for m in metrics[:40]:
            line = f"  - {m.get('metric_display_name') or m['metric_name']}"
            if m.get("calculation_logic"):
                line += f" = {m['calculation_logic']}"
            if m.get("description"):
                line += f" ({m['description']})"
            lines.append(line)

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# LLM 生成
# ═══════════════════════════════════════════════════════════════════

_SYSTEM_PROMPT = """你是数据架构师，负责从物理表结构中归纳业务本体（Ontology）。
本体以"业务对象"为中心（而非物理表）：对象可对应一张主表，也可聚合多张表。

输出严格 JSON（不要任何解释文字、不要 markdown 代码块），结构：
{
  "domain": "业务领域名称",
  "description": "该数据源业务概述，2-3句",
  "objects": [
    {
      "key": "英文小写标识，如 order",
      "display_name": "中文名，如 订单",
      "aliases": ["别名1", "别名2"],
      "description": "业务对象说明，包含使用场景",
      "primary_table": "主物理表名",
      "properties": [
        {
          "column": "table.column 全限定名",
          "name": "中文属性名",
          "type": "物理类型",
          "is_key": false,
          "description": "业务含义",
          "enum": ["0=待支付", "1=已支付"]
        }
      ],
      "links": [
        {
          "target": "目标对象 key",
          "type": "belongs_to / has_many / references",
          "join": "a.col = b.col",
          "cardinality": "N:1",
          "description": ""
        }
      ],
      "metrics": [
        {"name": "GMV", "formula": "SUM(pay_amount)", "description": ""}
      ]
    }
  ]
}

要求：
1. 对象命名面向业务（订单、客户、商品），不要直接照搬表名堆砌
2. 每张重要业务表至少被一个对象覆盖；日志/配置/临时表可忽略
3. properties 只列业务相关列（主键、业务字段、状态枚举），忽略纯技术字段
4. enum 仅对低基数状态/类型字段填写，格式 "值=含义"，不确定就留空数组
5. links 优先使用已知 relations，其次根据列名语义推断（需给出 join 表达式）
6. 指标优先采用给定 Metrics/Terms 中的口径"""


def _extract_json(text: str) -> dict:
    """从 LLM 输出中稳健提取 JSON 对象。"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("LLM 输出中未找到 JSON 对象")
    return json.loads(text[start:end + 1])


def generate_draft(datasource_id: int, progress_cb=None, created_by: str = "") -> dict:
    """LLM 归纳本体草案并落库（替换该数据源已有 draft）。

    Args:
        datasource_id: 数据源 ID
        progress_cb: 可选进度回调 fn(stage: str, detail: str)
        created_by: 创建人

    Returns:
        保存后的模型记录 dict
    """
    from services.shared.common.llm.llm_client import generate_sql

    def _progress(stage, detail=""):
        if progress_cb:
            try:
                progress_cb(stage, detail)
            except Exception:
                pass

    _progress("collect", "采集表结构与业务知识")
    meta = _collect_metadata(datasource_id)
    tables = meta["tables"]
    if not tables:
        raise ValueError(f"数据源 {datasource_id} 无有效表元数据，请先执行元数据同步")

    # 按 domain_tag 分批，单批不超过 BATCH_MAX_TABLES
    groups: dict[str, list] = {}
    for t in tables:
        groups.setdefault(t.get("domain_tag") or "_default", []).append(t)
    batches: list[list] = []
    for group_tables in groups.values():
        for i in range(0, len(group_tables), BATCH_MAX_TABLES):
            batches.append(group_tables[i:i + BATCH_MAX_TABLES])

    _progress("generate", f"共 {len(tables)} 张表，分 {len(batches)} 批归纳")
    merged_objects: dict[str, dict] = {}
    domain, description = "", ""
    for idx, batch in enumerate(batches, 1):
        user_prompt = (
            f"以下是数据源 {datasource_id} 第 {idx}/{len(batches)} 批物理表与业务知识：\n\n"
            f"{_schema_text(batch, meta)}\n\n"
            "请输出该批数据归纳的本体 JSON。"
        )
        result = generate_sql(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=8192,
        )
        doc = _extract_json(result["sql"])
        domain = domain or doc.get("domain", "")
        description = description or doc.get("description", "")
        for obj in doc.get("objects", []):
            key = obj.get("key") or obj.get("primary_table") or ""
            if key and key not in merged_objects:
                merged_objects[key] = obj
        _progress("batch_done", f"第 {idx}/{len(batches)} 批完成，累计 {len(merged_objects)} 个对象")

    if not merged_objects:
        raise ValueError("LLM 未归纳出任何业务对象")

    doc = {
        "datasource_id": datasource_id,
        "domain": domain,
        "description": description,
        "objects": list(merged_objects.values()),
    }

    # 落库：替换已有 draft
    model_id = _gen_id()
    json_content = json.dumps(doc, ensure_ascii=False, indent=2)
    yaml_content = to_yaml(doc)
    md_content = to_md(doc)
    now = _now()

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM adh_ontology_models WHERE datasource_id = %s AND status = 'draft'",
                (datasource_id,),
            )
            cur.execute(
                "INSERT INTO adh_ontology_models "
                "(id, datasource_id, name, status, json_content, yaml_content, md_content, "
                "object_count, created_by, created_at, updated_at) "
                "VALUES (%s, %s, %s, 'draft', %s, %s, %s, %s, %s, %s, %s)",
                (model_id, datasource_id, f"{domain or '本体模型'}-draft",
                 json_content, yaml_content, md_content,
                 len(doc["objects"]), created_by, now, now),
            )
        conn.commit()

    _progress("done", f"草案已生成：{len(doc['objects'])} 个业务对象")
    return get_model(model_id)


# ═══════════════════════════════════════════════════════════════════
# 三格式序列化（JSON 为事实源）
# ═══════════════════════════════════════════════════════════════════

def to_yaml(doc: dict) -> str:
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=120)


def _object_md(obj: dict) -> str:
    """单个对象的 MD 段 —— 即向量化文本。"""
    lines = [f"## 业务对象: {obj.get('display_name', obj.get('key', ''))} ({obj.get('key', '')})"]
    aliases = obj.get("aliases") or []
    if aliases:
        lines.append(f"别名: {', '.join(aliases)}")
    if obj.get("description"):
        lines.append(f"描述: {obj['description']}")
    if obj.get("primary_table"):
        lines.append(f"主表: {obj['primary_table']}")

    props = obj.get("properties") or []
    if props:
        lines.append("")
        lines.append("### 属性")
        for p in props:
            seg = f"- {p.get('column', '')}"
            if p.get("type"):
                seg += f" ({p['type']}"
                seg += ", 主键" if p.get("is_key") else ""
                seg += ")"
            if p.get("name") or p.get("description"):
                seg += f": {p.get('name', '')}"
                if p.get("description") and p["description"] != p.get("name"):
                    seg += f"，{p['description']}"
            enum = p.get("enum") or []
            if enum:
                seg += f"；枚举: {'; '.join(str(e) for e in enum)}"
            lines.append(seg)

    links = obj.get("links") or []
    if links:
        lines.append("")
        lines.append("### 关系")
        for lk in links:
            seg = f"- {lk.get('type', 'references')} {lk.get('target', '')}"
            if lk.get("join"):
                seg += f"，join: {lk['join']}"
            if lk.get("cardinality"):
                seg += f" ({lk['cardinality']})"
            if lk.get("description"):
                seg += f"：{lk['description']}"
            lines.append(seg)

    metrics = obj.get("metrics") or []
    if metrics:
        lines.append("")
        lines.append("### 指标")
        for m in metrics:
            seg = f"- {m.get('name', '')}"
            if m.get("formula"):
                seg += f" = {m['formula']}"
            if m.get("description"):
                seg += f"（{m['description']}）"
            lines.append(seg)

    return "\n".join(lines)


def to_md(doc: dict) -> str:
    parts = [f"# 本体模型: {doc.get('domain', '')}"]
    if doc.get("description"):
        parts.append("")
        parts.append(doc["description"])
    parts.append("")
    for obj in doc.get("objects", []):
        parts.append(_object_md(obj))
        parts.append("")
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════
# 模型 CRUD
# ═══════════════════════════════════════════════════════════════════

def _row_to_model(row: dict, include_content: bool = True) -> dict:
    model = {
        "id": row["id"],
        "datasource_id": row["datasource_id"],
        "name": row["name"],
        "status": row["status"],
        "object_count": row.get("object_count", 0),
        "created_by": row.get("created_by", ""),
        "created_at": row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else row["created_at"],
        "updated_at": row["updated_at"].isoformat() if hasattr(row["updated_at"], "isoformat") else row["updated_at"],
    }
    if include_content:
        model["json_content"] = row.get("json_content") or ""
        model["yaml_content"] = row.get("yaml_content") or ""
        model["md_content"] = row.get("md_content") or ""
    return model


def list_models(datasource_id: int = None) -> list:
    """模型列表（不含大字段）。"""
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            if datasource_id:
                cur.execute(
                    "SELECT id, datasource_id, name, status, object_count, created_by, "
                    "created_at, updated_at FROM adh_ontology_models "
                    "WHERE datasource_id = %s ORDER BY updated_at DESC",
                    (datasource_id,),
                )
            else:
                cur.execute(
                    "SELECT id, datasource_id, name, status, object_count, created_by, "
                    "created_at, updated_at FROM adh_ontology_models ORDER BY updated_at DESC"
                )
            rows = cur.fetchall()
    return [_row_to_model(r, include_content=False) for r in rows]


def get_model(model_id: int) -> Optional[dict]:
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM adh_ontology_models WHERE id = %s", (model_id,))
            row = cur.fetchone()
    return _row_to_model(row) if row else None


def save_draft(model_id: int, json_content: str, name: str = None) -> dict:
    """保存草案编辑（JSON 事实源），服务端重新派生 YAML/MD。仅 draft 可编辑。"""
    model = get_model(model_id)
    if not model:
        raise ValueError("模型不存在")
    if model["status"] != "draft":
        raise ValueError("仅 draft 状态的模型可编辑；如需修改已激活模型，请重新生成草案")

    try:
        doc = json.loads(json_content)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 解析失败: {e}")
    if not isinstance(doc.get("objects"), list) or not doc["objects"]:
        raise ValueError("JSON 必须包含非空 objects 数组")

    # 重新规范化并重派生
    json_norm = json.dumps(doc, ensure_ascii=False, indent=2)
    yaml_content = to_yaml(doc)
    md_content = to_md(doc)
    now = _now()

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE adh_ontology_models SET json_content = %s, yaml_content = %s, "
                "md_content = %s, object_count = %s, updated_at = %s"
                + (", name = %s" if name else "")
                + " WHERE id = %s",
                [json_norm, yaml_content, md_content, len(doc["objects"]), now]
                + ([name] if name else []) + [model_id],
            )
        conn.commit()
    return get_model(model_id)


def activate(model_id: int) -> dict:
    """激活模型：旧 active 归档，逐对象 MD 段向量化写入 adh_ontology_objects。"""
    from services.shared.common.llm.embedding import generate_embedding, embedding_to_sql_literal
    from services.shared.common.vector import get_vector_store

    model = get_model(model_id)
    if not model:
        raise ValueError("模型不存在")
    if model["status"] == "active":
        return model

    doc = json.loads(model["json_content"])
    objects = doc.get("objects") or []
    if not objects:
        raise ValueError("模型无对象，无法激活")

    datasource_id = model["datasource_id"]
    now = _now()

    # 1. 旧 active 归档 + 其对象下线
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE adh_ontology_models SET status = 'archived', updated_at = %s "
                "WHERE datasource_id = %s AND status = 'active' AND id != %s",
                (now, datasource_id, model_id),
            )
            cur.execute(
                "UPDATE adh_ontology_models SET status = 'active', updated_at = %s WHERE id = %s",
                (now, model_id),
            )
        conn.commit()

    # 2. 对象向量化（先下线旧对象，再写入新对象）
    store = get_vector_store()
    records = []
    for obj in objects:
        md_section = _object_md(obj)
        embedding = generate_embedding(md_section)
        records.append({
            "id": _gen_id() + len(records),
            "model_id": model_id,
            "datasource_id": datasource_id,
            "object_key": obj.get("key", ""),
            "display_name": obj.get("display_name", obj.get("key", "")),
            "aliases": ", ".join(obj.get("aliases") or []),
            "description": (obj.get("description") or "")[:1000],
            "md_section": md_section,
            "is_active": 1,
            "embedding": embedding_to_sql_literal(embedding),
        })

    # 按 model 粒度清理旧对象（DUPLICATE KEY 表用 DELETE + INSERT）
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM adh_ontology_objects WHERE datasource_id = %s AND model_id != %s",
                (datasource_id, model_id),
            )
        conn.commit()

    if records:
        store.upsert_batch("adh_ontology_objects", "id", records)

    logger.info("[ontology] activated model %s: %d objects vectorized", model_id, len(records))
    return get_model(model_id)


def archive(model_id: int) -> dict:
    """归档模型并下线其对象向量。"""
    model = get_model(model_id)
    if not model:
        raise ValueError("模型不存在")
    now = _now()
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE adh_ontology_models SET status = 'archived', updated_at = %s WHERE id = %s",
                (now, model_id),
            )
            if model["status"] == "active":
                cur.execute(
                    "UPDATE adh_ontology_objects SET is_active = 0 WHERE model_id = %s",
                    (model_id,),
                )
        conn.commit()
    return get_model(model_id)


def delete_model(model_id: int) -> bool:
    """删除模型（及其对象向量）。"""
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM adh_ontology_objects WHERE model_id = %s", (model_id,))
            cur.execute("DELETE FROM adh_ontology_models WHERE id = %s", (model_id,))
        conn.commit()
    return True


# ═══════════════════════════════════════════════════════════════════
# 对象检索（供 ontology_first 策略与前端预览）
# ═══════════════════════════════════════════════════════════════════

def search_objects(question: str, datasource_id: int = 0, limit: int = 5) -> list[dict]:
    """对象向量检索，返回命中对象 + distance + 所属模型 json_content。"""
    from services.shared.common.llm.embedding import generate_embedding
    from services.shared.common.vector import get_vector_store

    embedding = generate_embedding(question)
    filters = {"is_active": 1}
    if datasource_id:
        filters["datasource_id"] = datasource_id

    store = get_vector_store()
    hits = store.search(
        "adh_ontology_objects",
        embedding,
        limit=limit,
        filters=filters,
        output_columns=["id", "model_id", "object_key", "display_name", "aliases", "description"],
    )
    if not hits:
        return []

    # 附带所属模型的 JSON（展开物理元数据用）
    model_ids = list({h["model_id"] for h in hits})
    placeholders = ", ".join(["%s"] * len(model_ids))
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id, json_content FROM adh_ontology_models WHERE id IN ({placeholders})",
                model_ids,
            )
            model_jsons = {r["id"]: r["json_content"] for r in cur.fetchall()}

    for h in hits:
        try:
            doc = json.loads(model_jsons.get(h["model_id"], "{}"))
        except json.JSONDecodeError:
            doc = {}
        obj = next(
            (o for o in doc.get("objects", []) if o.get("key") == h["object_key"]),
            None,
        )
        h["object"] = obj
    return hits
