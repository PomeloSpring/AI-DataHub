"""Ontology Modeling Service — FDE 对象中心本体（本地轻量实现）。

工作流：页面触发 LLM 生成草案(JSON 事实源) → 用户检查/编辑 → 确认激活 →
逐对象 MD 段写入 adh_ontology_objects（元数据库，供对象关键词检索与前端预览）。

三格式：JSON（接口流转，唯一事实源）/ YAML（结构可读）/ MD（AI 识别与检索）。
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


_EMBEDDING_DIM = 768


def _placeholder_embedding() -> str:
    """Zero-vector placeholder for the logically-disabled embedding column.

    The column is retained (Doris ARRAY<FLOAT> NOT NULL / MySQL JSON) but is no
    longer produced by an embedding model nor read by any retrieval path.
    """
    return "[" + ", ".join(["0.0"] * _EMBEDDING_DIM) + "]"


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
                "WHERE (datasource_id = %s OR datasource_id = 0) AND is_active = 1 LIMIT 200",
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
                    "SELECT name, name_en, agg_type, formula, target_table, "
                    "description FROM adh_metrics "
                    "WHERE (datasource_id = %s OR datasource_id = 0) AND is_active = 1 LIMIT 200",
                    (datasource_id,),
                )
                metrics = cur.fetchall()
            except Exception as e:
                logger.warning("metrics collect skipped: %s", e)

            dimensions = []
            try:
                cur.execute(
                    "SELECT name, name_en, target_table, target_column, category, "
                    "aliases, value_labels, description FROM adh_dimensions "
                    "WHERE (datasource_id = %s OR datasource_id = 0) AND is_active = 1 LIMIT 300",
                    (datasource_id,),
                )
                dimensions = cur.fetchall()
            except Exception as e:
                logger.warning("dimensions collect skipped: %s", e)

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
        "dimensions": dimensions,
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

    metrics = [m for m in meta["metrics"]
               if not m.get("target_table") or m["target_table"] in table_names]
    if metrics:
        lines.append("Metrics:")
        for m in metrics[:40]:
            line = f"  - {m['name']}"
            if m.get("name_en"):
                line += f" ({m['name_en']})"
            if m.get("formula"):
                line += f" = {m['formula']}"
            if m.get("description"):
                line += f" ({m['description']})"
            lines.append(line)
        lines.append("")

    # 语义维度字典(含枚举业务标签): 让 NL2SQL 老管道也能把码值翻成业务含义
    dims = [d for d in (meta.get("dimensions") or [])
            if d.get("target_table") in table_names]
    if dims:
        def _json_field(v):
            if isinstance(v, (str, bytes)):
                try:
                    return json.loads(v)
                except (ValueError, TypeError):
                    return None
            return v
        lines.append("Semantic dimensions (name | aliases | enum labels):")
        for d in dims[:80]:
            line = f"  - {d['name']} @ {d['target_table']}.{d.get('target_column') or ''}"
            al = _json_field(d.get("aliases")) or []
            if al:
                line += f" | aliases: {', '.join(str(a) for a in al)}"
            vl = _json_field(d.get("value_labels")) or {}
            if vl:
                line += " | 枚举: " + ", ".join(f"{k}={v}" for k, v in sorted(vl.items()))
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


def list_models(datasource_id: int = None, include_archived: bool = False) -> list:
    """模型列表（不含大字段）。

    默认排除 archived —— 归档的历史版本属于对应模型的「版本管理」，
    由 list_versions 按 (datasource_id, name) 分组呈现，不再混在主列表。
    """
    sql = (
        "SELECT id, datasource_id, name, status, object_count, created_by, "
        "created_at, updated_at FROM adh_ontology_models"
    )
    conditions: list[str] = []
    params: list = []
    if datasource_id:
        conditions.append("datasource_id = %s")
        params.append(datasource_id)
    if not include_archived:
        conditions.append("status <> 'archived'")
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY updated_at DESC"
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    return [_row_to_model(r, include_content=False) for r in rows]


def list_versions(model_id: int) -> list:
    """同一本体模型（按 datasource_id + name 归组）的全部版本，含归档。

    主列表只展示非归档模型；被归档的历史版本归入此接口，作为对应模型的
    「版本管理」视图。当前 active 版本排在首位，其余按更新时间倒序。
    """
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT datasource_id, name FROM adh_ontology_models WHERE id = %s",
                (model_id,),
            )
            base = cur.fetchone()
            if not base:
                raise ValueError("模型不存在")
            cur.execute(
                "SELECT id, datasource_id, name, status, object_count, created_by, "
                "created_at, updated_at FROM adh_ontology_models "
                "WHERE datasource_id = %s AND name = %s "
                "ORDER BY (status = 'active') DESC, updated_at DESC",
                (base["datasource_id"], base["name"]),
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
    """保存本体编辑（JSON 事实源），服务端重新派生 YAML/MD。

    draft：直接改内容。active：允许就地编辑，保存时按 primary_table 依据真实元数据
    重算 execution_binding、重展开 adh_ontology_objects、重写绑定并同步图谱，
    使本体与「表 & 字段 / 指标」等数据目录联动。archived 不可编辑。
    """
    model = get_model(model_id)
    if not model:
        raise ValueError("模型不存在")
    if model["status"] == "archived":
        raise ValueError("已归档模型不可编辑；如需修改请在「版本管理」中回滚激活该版本")

    try:
        doc = json.loads(json_content)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 解析失败: {e}")
    if not isinstance(doc.get("objects"), list) or not doc["objects"]:
        raise ValueError("JSON 必须包含非空 objects 数组")

    # 补齐上下文，供绑定重算 / 派生使用
    doc["datasource_id"] = model["datasource_id"]
    doc.setdefault("domain", "")
    doc.setdefault("description", "")

    # 激活态编辑：依据真实表元数据重算绑定并联动刷新对象/绑定/图谱
    if model["status"] == "active":
        _sync_active_model(doc, model_id, model["datasource_id"])

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

    # 激活态编辑：json_content 已落库，此时再重建图谱，确保 _merge_ontology_models
    # 读到最新对象集（否则图谱会滞后一次保存）。
    if model["status"] == "active":
        from services.datacatalog.services import ontology_yaml_import as _yimp
        _yimp._rebuild_graph(model["datasource_id"])
    return get_model(model_id)


def _expand_objects(doc: dict, model_id: int, datasource_id: int) -> int:
    """把 canonical doc 的对象展开写入 adh_ontology_objects（幂等：先清本模型再插）。

    返回写入对象数。activate 与激活态编辑保存共用。
    """
    objects = doc.get("objects") or []
    records = []
    for i, obj in enumerate(objects):
        records.append({
            "id": _gen_id() + i,
            "model_id": model_id,
            "datasource_id": datasource_id,
            "object_key": obj.get("key", ""),
            "display_name": obj.get("display_name", obj.get("key", "")),
            "aliases": ", ".join(obj.get("aliases") or []),
            "description": (obj.get("description") or "")[:1000],
            "md_section": _object_md(obj),
            "is_active": 1,
            "embedding": _placeholder_embedding(),
        })
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            # 下线其它模型对象 + 清理本模型旧对象（重激活/重保存幂等）
            cur.execute(
                "DELETE FROM adh_ontology_objects WHERE datasource_id = %s AND model_id != %s",
                (datasource_id, model_id),
            )
            cur.execute(
                "DELETE FROM adh_ontology_objects WHERE model_id = %s",
                (model_id,),
            )
            for rec in records:
                cols = ", ".join(f"`{k}`" for k in rec.keys())
                placeholders = ", ".join(["%s"] * len(rec))
                cur.execute(
                    f"INSERT INTO adh_ontology_objects ({cols}) VALUES ({placeholders})",
                    list(rec.values()),
                )
        conn.commit()
    return len(records)


def _enum_item_to_label(item: str) -> tuple[str, str] | None:
    """把本体 enum 项("3=UPLOAD（已上传）"/"0:否")解析为 (code, 业务标签)。

    括号内有中文 → 取中文作标签(可读性优先); 否则用等号右侧原文。
    """
    import re
    s = str(item or "").strip()
    if not s:
        return None
    mm = re.match(r"^(\w+)\s*[=:：]\s*(.+)$", s)
    if not mm:
        return None
    code, rest = mm.group(1), mm.group(2).strip()
    cn = re.search(r"[（(]([^（）()]*[\u4e00-\u9fff][^（）()]*)[）)]", rest)
    if cn:
        label = cn.group(1).strip()
    else:
        label = re.sub(r"[（(].*?[）)]", "", rest).strip() or rest
    return code, label


def sync_enums_to_dimensions(doc: dict, datasource_id: int) -> dict:
    """把本体对象属性的 enum/中文名沉淀进语义维度字典。

    回写目标(adh_dimensions, 按 target_table+target_column 命中):
    - properties[].enum → value_labels {code: label}
    - properties[].name(英文属性名)/description(业务名) → aliases
    已有人工 value_labels 与本体冲突时不覆盖, 只记日志。
    字典中无维度行的枚举属性 → 不自动建维度, 列入 gaps 供人工决策。
    """
    updated, conflicts, gaps = 0, [], []
    # 同一列可能被多对象引用: 合并后再写
    by_col: dict[tuple[str, str], dict] = {}
    for obj in doc.get("objects") or []:
        for p in obj.get("properties") or obj.get("attributes") or []:
            col_ref = str(p.get("column") or "")
            if "." not in col_ref:
                continue
            tbl, col = col_ref.split(".", 1)
            entry = by_col.setdefault((tbl, col), {"labels": {}, "aliases": set()})
            for it in (p.get("enum") or []):
                parsed = _enum_item_to_label(it)
                if parsed:
                    entry["labels"][parsed[0]] = parsed[1]
            for a in (p.get("name"), p.get("description")):
                a = str(a or "").strip()
                if not a or a == col:
                    continue
                entry["aliases"].add(a)
        # 对象级别名不进列, 只收属性
    if not by_col:
        return {"updated": 0, "conflicts": [], "gaps": []}

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            for (tbl, col), entry in by_col.items():
                cur.execute(
                    "SELECT id, name, aliases, value_labels, description FROM adh_dimensions "
                    "WHERE is_active = 1 AND target_table = %s AND target_column = %s",
                    (tbl, col),
                )
                rows = cur.fetchall()
                if not rows:
                    if entry["labels"]:
                        gaps.append({"table": tbl, "column": col,
                                     "enum_size": len(entry["labels"])})
                    continue
                for d in rows:
                    # -- value_labels 合并(人工非空优先)
                    old_labels = d.get("value_labels")
                    if isinstance(old_labels, (str, bytes)):
                        try:
                            old_labels = json.loads(old_labels)
                        except (ValueError, TypeError):
                            old_labels = None
                    new_labels = dict(entry["labels"])
                    if isinstance(old_labels, dict) and old_labels:
                        diff = {k: v for k, v in new_labels.items()
                                if k in old_labels and old_labels[k] != v}
                        if diff:
                            conflicts.append({"dimension_id": d["id"], "diff": diff})
                        new_labels = {**new_labels, **old_labels}  # 人工值覆盖本体值
                    if new_labels == (old_labels or {}):
                        labels_sql = None
                    else:
                        labels_sql = json.dumps(new_labels, ensure_ascii=False)
                    # -- aliases 追加合并
                    old_alias = d.get("aliases")
                    if isinstance(old_alias, (str, bytes)):
                        try:
                            old_alias = json.loads(old_alias)
                        except (ValueError, TypeError):
                            old_alias = None
                    old_alias = list(old_alias or [])
                    # 与维度名同义的别名不收录(如 名称"案例状态" vs 别名"案例状态（核心状态机）")
                    def _trivial(x: str) -> bool:
                        y = re.sub(r"[（(].*?[）)]", "", str(x)).strip()
                        return y == d["name"] or str(x).strip() == d["name"]
                    merged_alias = sorted({str(x) for x in old_alias
                                           if not _trivial(x)} | {a for a in entry["aliases"]
                                                                   if not _trivial(a)})
                    alias_sql = None
                    if merged_alias != old_alias:
                        alias_sql = json.dumps(merged_alias, ensure_ascii=False)
                    if labels_sql is None and alias_sql is None:
                        continue
                    sets, params = [], []
                    if labels_sql is not None:
                        sets.append("value_labels = %s"); params.append(labels_sql)
                    if alias_sql is not None:
                        sets.append("aliases = %s"); params.append(alias_sql)
                    sets.append("updated_at = %s"); params.append(_now())
                    cur.execute(
                        f"UPDATE adh_dimensions SET {', '.join(sets)} WHERE id = %s",
                        params + [d["id"]],
                    )
                    updated += 1
        conn.commit()

    if conflicts:
        logger.warning("[ontology] enum→dims 冲突(保留人工标签): %s", conflicts)
    if gaps:
        logger.info("[ontology] 有枚举语义但字典缺维度行: %s", gaps)
    logger.info("[ontology] sync_enums_to_dimensions: updated=%d conflicts=%d gaps=%d",
                updated, len(conflicts), len(gaps))
    return {"updated": updated, "conflicts": conflicts, "gaps": gaps}


def _sync_active_model(doc: dict, model_id: int, datasource_id: int) -> None:
    """激活态编辑保存时的联动刷新：重算 binding → 重展开对象 → 重写绑定。

    会就地修改 doc.objects[].execution_binding，使回写的 JSON 与结构化绑定保持权威一致。
    图谱重建不在此处触发：需等 save_draft 将最新 json_content 落库后再调，
    否则 _merge_ontology_models 会读到旧内容，使图谱滞后一次保存。
    """
    from services.datacatalog.services import ontology_yaml_import as _yimp

    table_info = _yimp._load_table_info_map(datasource_id)
    ds_name = _yimp._datasource_name_by_id(datasource_id)
    doc["datasource_name"] = ds_name
    for obj in doc.get("objects") or []:
        obj["execution_binding"] = _yimp._build_execution_binding(
            obj.get("primary_table") or "", datasource_id, table_info, datasource_name=ds_name)
    _expand_objects(doc, model_id, datasource_id)
    _yimp._persist_bindings(doc, model_id)
    # 本体 enum/属性中文名 → 语义维度字典(别名与枚举标签的唯一联动点)
    sync_enums_to_dimensions(doc, datasource_id)


def activate(model_id: int) -> dict:
    """激活模型：旧 active 归档，逐对象 MD 段写入 adh_ontology_objects（供对象检索/预览）。"""
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

    # 2. 展开对象写入元数据库（不再向量化；embedding 列写零向量占位）
    count = _expand_objects(doc, model_id, datasource_id)
    # 3. 枚举/别名同步进维度字典
    sync_enums_to_dimensions(doc, datasource_id)

    logger.info("[ontology] activated model %s: %d objects written", model_id, count)
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
# 对象检索（供本体对象关键词检索与前端预览）
# ═══════════════════════════════════════════════════════════════════

def search_objects(question: str, datasource_id: int = 0, limit: int = 5) -> list[dict]:
    """本体对象关键词检索（元数据库 LIKE，无向量），返回命中对象 + 所属模型 json_content。"""
    tokens: list[str] = []
    q = (question or "").strip()
    for part in re.split(r"[\s,，、;；/、]+", q):
        p = part.strip()
        if len(p) >= 2 and p not in tokens:
            tokens.append(p)
    if q and len(q) >= 2 and q not in tokens:
        tokens.insert(0, q)
    if not tokens:
        return []

    cols = ["object_key", "display_name", "aliases", "description", "md_section"]
    conditions = ["is_active = 1"]
    params: list = []
    if datasource_id:
        conditions.append("datasource_id = %s")
        params.append(datasource_id)
    subs = []
    for t in tokens:
        like = f"%{t}%"
        for c in cols:
            subs.append(f"{c} LIKE %s")
            params.append(like)
    conditions.append("(" + " OR ".join(subs) + ")")
    where = "WHERE " + " AND ".join(conditions)

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id, model_id, object_key, display_name, aliases, description "
                f"FROM adh_ontology_objects {where} LIMIT {int(limit)}",
                params,
            )
            hits = cur.fetchall()
            if not hits:
                return []

            # 附带所属模型的 JSON（展开物理元数据用）
            model_ids = list({h["model_id"] for h in hits})
            placeholders = ", ".join(["%s"] * len(model_ids))
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
