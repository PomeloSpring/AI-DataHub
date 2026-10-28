"""Ontology Modeling Service — FDE 对象中心本体（本地轻量实现）。

工作流：页面发起『生成本体模型』任务（自动创建 AS-BOT 会话，归纳由 qoder agent 完成）→
服务端确定性校验/合并后落库生成草案(JSON 事实源) → 用户检查/编辑 → 确认激活 →
逐对象 MD 段写入 adh_ontology_objects（元数据库，供对象关键词检索与前端预览）。

本模块不调用任何 LLM：归纳属 AS-BOT 会话任务，这里只提供素材（build_generation_batches）
与确定性落库（save_generated_draft），避免出现第二条 LLM 生成通道。

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

from backend.common.db.metadata_db import get_metadata_conn

logger = logging.getLogger(__name__)

# 每批归纳素材的最大表数（超出按 domain_tag 分批，由 AS-BOT 分批归纳后合并）
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


def _schema_text(batch_tables: list, meta: dict, kb_text: str = "") -> str:
    """把一批表及其列、相关术语/指标/关系拼成 LLM 输入文本。

    kb_text 非空时（会话选定了辅助知识库）在末尾追加「Business knowledge」段，
    引导 agent 依据业务知识推断 links/dimensions（仅作输入上下文，不改事实源）。
    """
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

    if kb_text:
        lines.append("")
        lines.append("Business knowledge (from selected KB):")
        lines.append(kb_text.strip())

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# 归纳 schema 规范（本体 JSON 结构约定，供 AS-BOT 任务消息引用）
# ═══════════════════════════════════════════════════════════════════

ONTOLOGY_SCHEMA_SPEC = """你是数据架构师，负责从物理表结构中归纳业务本体（Ontology）。
本体以"业务对象"为中心（而非物理表）：对象可对应一张主表，也可聚合多张表。

归纳产物按以下结构组织（domain / description / objects 三个字段）：
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
          "join_type": "left | inner（可选，缺省 left）",
          "cardinality": "N:1",
          "description": ""
        }
      ],
      "metrics": ["指标名1", "指标名2"]
    }
  ]
}

要求：
1. 对象命名面向业务（订单、客户、商品），不要直接照搬表名堆砌
2. 每张重要业务表至少被一个对象覆盖；日志/配置/临时表可忽略
3. properties 只列业务相关列（主键、业务字段、状态枚举），忽略纯技术字段
4. enum 仅对低基数状态/类型字段填写，格式 {"0": "待支付", "1": "已支付"}（code→业务标签），不确定就留空对象
5. links 优先使用已知 relations，其次根据列名语义推断（需给出 join 表达式）；join_type 可选（inner 仅保留匹配行，其余/缺省一律 left，保守不丢主表行）
6. metrics 只引用指标名字符串（口径/formula/单位统一登记在指标字典，对象不内嵌口径）；
   指标优先采用给定 Metrics/Terms 中的名字
7. 描述按 5W2H 槽位组织（与检索侧槽位规划同骨架）：what=业务含义/口径、when=时间口径、
   who/where=归属主体/维度范围、howMuch=单位量纲；指标的 what/how/howMuch（口径文字、聚合、单位）
   写进指标字典 description，不在对象里重复"""


# ═══════════════════════════════════════════════════════════════════
# 对象 key 规范与重复检测
# ═══════════════════════════════════════════════════════════════════
# 历史缺陷根因：本体生成链路与 palantir_to_canonical 的去重都只按精确字符串
# 判 key，`case_file` 与 `casefile` 被当成两个对象，于是产生一批 0 属性空壳重复。
# 判重一律走 object_identity_key(忽略大小写与分隔符)，命名走 normalize_object_key。

_KEY_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_ACRONYM_BOUNDARY = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")


def object_identity_key(key: str) -> str:
    """对象身份键：忽略大小写与分隔符，用于判定"是否同一个对象"。

    `case_file` / `casefile` / `CaseFile` 归到同一身份。只用于判重，
    对外 key 仍用 normalize_object_key 规范后的 snake_case。
    """
    return _KEY_NON_ALNUM.sub("", str(key or "").lower())


def normalize_object_key(key: str) -> str:
    """对象 key 规范为 snake_case 小写：CaseFile→case_file，AIConversation→ai_conversation。

    顺序不能乱：驼峰/缩写边界必须在**小写化之前**切分（否则看不出大小写边界），
    非字母数字替换必须在小写化**之后**（否则大写字母会被当成非字母数字吞掉）。
    """
    s = str(key or "").strip()
    if not s:
        return ""
    s = "".join(chr(ord(c) - 0xFEE0) if "\uFF01" <= c <= "\uFF5E" else c for c in s)
    s = _ACRONYM_BOUNDARY.sub("_", s)
    s = _CAMEL_BOUNDARY.sub("_", s)
    s = s.lower()
    s = _KEY_NON_ALNUM.sub("_", s).strip("_")
    return re.sub(r"_{2,}", "_", s)


# ── 指标/枚举条目格式（单一登记后的规范形态） ──────────────────────
# metrics 仅存指标名字符串（口径唯一登记在 adh_metrics 字典）；
# enum 仅存 {code: label} 对象（与 adh_dimensions.value_labels 同构）。
# 读路径对旧格式容错+告警（可诊断），写路径 fail-loud 拒收（见 validate_doc_formats）。

def _metric_name(m) -> str:
    """指标条目取名：新格式 str（唯一合法）；旧格式 dict 容错读并告警。"""
    if isinstance(m, str):
        return m.strip()
    if isinstance(m, dict):
        logger.warning("[ontology] 读到旧格式指标条目(dict) %r，按名字引用处理", m.get("name"))
        return str(m.get("name") or "").strip()
    return str(m or "").strip()


def enum_pairs(enum) -> list[tuple[str, str]]:
    """枚举取 (code, label) 对：新格式 dict（唯一合法）；旧格式 [\"0=否\"] 容错解析并告警。"""
    if isinstance(enum, dict):
        return [(str(k), str(v)) for k, v in enum.items()]
    if isinstance(enum, (list, tuple)):
        pairs = [p for p in (_enum_item_to_label(it) for it in enum) if p]
        if pairs:
            logger.warning("[ontology] 读到旧格式 enum(字符串列表)，已容错解析，请迁移为 {code: label}")
        return pairs
    return []


def validate_doc_formats(doc: dict) -> list[str]:
    """写闸（迁移后 fail-loud）：metrics 仅接受名字字符串、enum 仅接受 {code: label} 对象。"""
    errors: list[str] = []
    for o in doc.get("objects") or []:
        key = o.get("key") or ""
        for m in o.get("metrics") or []:
            if not isinstance(m, str):
                errors.append(
                    f"对象 {key} 的指标条目 {m!r} 为旧格式(dict)；口径唯一登记在指标字典，"
                    "对象 metrics 只接受指标名字符串")
        for p in o.get("properties") or []:
            en = p.get("enum")
            if en and not isinstance(en, dict):
                errors.append(
                    f"对象 {key} 属性 {p.get('column') or p.get('name')!r} 的 enum 为旧格式；"
                    "仅接受 {code: label} 对象（与 value_labels 同构）")
    return errors


# 对象命名在 canonical 里是 {key, display_name, aliases[]}（建模规范 §2），但历史 doc、
# LLM 直存与旧导入链常写成 object_key / name。下游只认 canonical 名，不认就会静默失真
# （实测：源本体 4 个对象在图谱上只剩 1 个 `Unknown` 类）。
_OBJECT_FIELD_ALIASES = (("key", ("object_key", "id", "class")),
                        ("display_name", ("name", "label")))


def normalize_object_field_aliases(doc: dict) -> list:
    """把对象上的旧字段名归一到 canonical（key / display_name），返回改动清单。

    仅在 canonical 字段缺失或为空时才从别名取值（不覆盖已有值），所以幂等：
    对已经规范的 doc 是 no-op。归一发生在写闸之前，让写闸校验的是归一后的形状。
    """
    touched = []
    for o in doc.get("objects") or []:
        if not isinstance(o, dict):
            continue
        for canonical, aliases in _OBJECT_FIELD_ALIASES:
            if str(o.get(canonical) or "").strip():
                continue
            for alias in aliases:
                val = o.get(alias)
                if isinstance(val, str) and val.strip():
                    o[canonical] = val.strip()
                    touched.append(f"{canonical}<-{alias}")
                    break
    return touched


def _infer_agg(formula: str) -> str:
    """从公式头部聚合函数推 agg_type（字典写入的单一推导点）。

    COUNT(DISTINCT x) 必须归 count_distinct：旧写法只匹配 COUNT 会把去重口径
    记成普通计数，目录/择路会误判为“各站可相加”而给出错数（全球去重用户数典型）。
    """
    s = (formula or "").strip()
    if re.match(r"(?is)^\s*COUNT\s*\(\s*DISTINCT\b", s):
        return "COUNT_DISTINCT"
    m = re.match(r"(?is)^\s*(SUM|AVG|COUNT|MIN|MAX)\s*\(", s)
    return m.group(1).upper() if m else "CUSTOM"


# 指标公式"合法单表聚合"判定（与 scripts/backfill_dict_target_table.py 同口径）：
# 仅当公式以聚合函数开头、不含内联 WHERE/子查询/跨表限定时，才可安全绑定 target_table。
_METRIC_AGG_HEAD = re.compile(r"^\s*(SUM|AVG|COUNT|MIN|MAX)\s*\(", re.I)
_METRIC_SELECT = re.compile(r"\bSELECT\b", re.I)
_METRIC_WHERE = re.compile(r"\bWHERE\b", re.I)
_METRIC_QUALIFIED = re.compile(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)")


def _metric_safe_target(formula: str, primary_table: str) -> str:
    """公式为"合法单表聚合"时返回其 target_table(=对象主表), 否则空串。

    排除内联 WHERE / 子查询 / 跨表限定 / 非聚合(布尔标记·标量) —— 这些属建模回炉项,
    激活会让 planner 生成非法/错误 SQL, 故留空 target_table 使其维持 fail-loud
    (宁缺勿错, 不把"目录挂着、编译用不了"变成"生成坏 SQL")。
    """
    f = (formula or "").strip()
    t = (primary_table or "").strip()
    if not f or not t:
        return ""
    if _METRIC_SELECT.search(f) or _METRIC_WHERE.search(f):
        return ""
    for pref, _c in _METRIC_QUALIFIED.findall(f):
        if pref.lower() != t.lower():
            return ""
    return t if _METRIC_AGG_HEAD.match(f) else ""


def absorb_object_metric_specs(doc: dict, datasource_id: int = 0,
                               persist: bool = True) -> dict:
    """把对象内旧格式指标条目({name,formula,description})归并进指标字典并降为名字引用。

    口径唯一登记 = adh_metrics（规则 §3）；幂等；已登记口径非空且不一致→告警不覆盖
    （人工口径优先）。persist=False 只归一 doc（dry-run）。
    """
    specs: list[tuple[str, str, str, str, str]] = []
    for o in doc.get("objects") or []:
        names: list[str] = []
        for m in o.get("metrics") or []:
            if isinstance(m, str):
                if m.strip():
                    names.append(m.strip())
                continue
            if not isinstance(m, dict):
                continue
            name = str(m.get("name") or "").strip()
            if not name:
                continue
            specs.append((str(o.get("key") or ""), name,
                          str(m.get("formula") or "").strip(),
                          str(m.get("description") or "").strip(),
                          str(o.get("primary_table") or "").strip()))
            names.append(name)
        o["metrics"] = names
    if not specs or not persist:
        return doc

    from backend.common.db.metadata_db import get_metadata_conn
    # adh_metrics.datasource_id 是 INT：雪花 id 超出范围时新行落 0（全局口径），显式告警不硬塞
    ds_val = int(datasource_id or 0)
    if not (0 <= ds_val <= 2**31 - 1):
        logger.warning("[ontology] 数据源 id %s 超出字典 INT 列范围，新增指标行按全局(0)登记", ds_val)
        ds_val = 0
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            for obj_key, name, formula, desc, primary_table in specs:
                safe_tt = _metric_safe_target(formula, primary_table)
                cur.execute(
                    "SELECT id, formula, description, bound_object_key, target_table FROM adh_metrics "
                    "WHERE name = %s AND is_active = 1 "
                    "AND (datasource_id = 0 OR datasource_id = %s) "
                    "ORDER BY datasource_id DESC LIMIT 1",
                    (name, ds_val))
                row = cur.fetchone()
                if row:
                    if formula and (row.get("formula") or "") and row["formula"] != formula:
                        logger.warning("[ontology] 指标 %s 口径冲突(字典优先,未覆盖): %r vs %r",
                                       name, row["formula"], formula)
                    if formula and not (row.get("formula") or ""):
                        cur.execute("UPDATE adh_metrics SET formula = %s WHERE id = %s",
                                    (formula, row["id"]))
                    if desc and not (row.get("description") or ""):
                        cur.execute("UPDATE adh_metrics SET description = %s WHERE id = %s",
                                    (desc, row["id"]))
                    if obj_key and not (row.get("bound_object_key") or ""):
                        cur.execute("UPDATE adh_metrics SET bound_object_key = %s WHERE id = %s",
                                    (obj_key, row["id"]))
                    # 存量行 target_table 为空且公式为合法单表聚合 → 回填(否则留空维持 fail-loud)
                    if safe_tt and not (row.get("target_table") or ""):
                        cur.execute("UPDATE adh_metrics SET target_table = %s WHERE id = %s",
                                    (safe_tt, row["id"]))
                else:
                    now = _now()
                    cur.execute("SELECT COALESCE(MAX(id), 0) + 1 AS nid FROM adh_metrics")
                    row_id = cur.fetchone()["nid"]
                    agg = _infer_agg(formula)
                    cur.execute(
                        "INSERT INTO adh_metrics "
                        "(id, workspace_id, name, formula, agg_type, description, "
                        "target_table, target_column, datasource_id, is_active, "
                        "created_at, updated_at, default_agg, bound_object_key) "
                        "VALUES (%s, 0, %s, %s, %s, %s, %s, '', %s, 1, %s, %s, %s, %s)",
                        (row_id, name, formula or "", agg, desc or "", safe_tt,
                         ds_val, now, now, agg, obj_key or None))
        conn.commit()
    return doc


def find_duplicate_object_keys(doc: dict) -> list:
    """按身份键找出重复对象组（空 = 干净）。供 save_draft 告警 / activate 阻断。"""
    groups: dict = {}
    for obj in doc.get("objects") or []:
        ik = object_identity_key(obj.get("key"))
        if ik:
            groups.setdefault(ik, []).append(obj)
    return [
        {"identity": ik,
         "keys": [str(m.get("key")) for m in members],
         "primary_tables": sorted({str(m.get("primary_table") or "") for m in members})}
        for ik, members in groups.items() if len(members) > 1
    ]


def _object_richness(obj: dict) -> tuple:
    """丰富度排序键：属性多 > 关系多 > 指标多 > snake_case 命名。"""
    return (len(obj.get("properties") or []), len(obj.get("links") or []),
            len(obj.get("metrics") or []), 1 if "_" in str(obj.get("key") or "") else 0)


def _union_items(sig, *lists) -> list:
    """按 sig 去重合并；同键时保留 description 非空的那条。"""
    out: dict = {}
    for lst in lists:
        for it in lst or []:
            k = sig(it)
            if not k:
                continue
            cur = out.get(k)
            if cur is None:
                out[k] = dict(it)
            elif not (cur.get("description") or "").strip() \
                    and (it.get("description") or "").strip():
                merged = dict(cur)
                merged["description"] = it.get("description")
                out[k] = merged
    return list(out.values())


def _union_aliases(*lists) -> list:
    out, seen = [], set()
    for lst in lists:
        for a in lst or []:
            s = str(a or "").strip()
            if s and s.lower() not in seen:
                seen.add(s.lower())
                out.append(s)
    return out


def merge_object_pair(a: dict, b: dict) -> tuple:
    """合并两个同身份对象：以更丰富者为基，别名/关系/指标取并集。

    返回 (合并结果, 合并明细)。明细必须由调用方对外可见，不得静默吞掉
    (no-silent-degradation)。
    """
    base, other = (a, b) if _object_richness(a) >= _object_richness(b) else (b, a)
    merged = dict(base)
    merged["aliases"] = _union_aliases(base.get("aliases"), other.get("aliases"))
    merged["links"] = _union_items(
        lambda x: (str(x.get("target") or ""), str(x.get("join") or "")),
        base.get("links"), other.get("links"))
    merged["metrics"] = [_metric_name(x) for x in _union_items(
        _metric_name, base.get("metrics"), other.get("metrics")) if _metric_name(x)]
    if not (merged.get("description") or "").strip():
        merged["description"] = other.get("description") or ""
    gained = (set(merged["metrics"])
              - {_metric_name(m) for m in base.get("metrics") or []})
    return merged, {"key": merged.get("key"), "dropped": other.get("key"),
                    "gained_metrics": sorted(gained)}


def build_generation_batches(datasource_id: int, kb_ids: list | None = None) -> list[str]:
    """采集数据源元数据并按批产出归纳素材文本（确定性，无 LLM 调用）。

    每批不超过 BATCH_MAX_TABLES 张表（按 domain_tag 分组后切批），批序确定，
    供 AS-BOT 会话任务分批归纳后经 save_generated_draft 提交合并。

    kb_ids 非空时（会话选定了辅助知识库），按批以表名为线索检索业务知识，
    拼成「Business knowledge」段附于素材末尾，引导 agent 推断 links/dimensions。
    检索沿用现有 qmind/rag 通道；无命中时显式标注（不静默伪装成已补充）。

    Returns:
        素材文本列表（每批一条，同 _schema_text 输出）
    """
    meta = _collect_metadata(datasource_id)
    tables = meta["tables"]
    if not tables:
        if datasource_id <= 0:
            # 系统域不是业务源，同步元数据也变不出表 —— 给准确指引，别把人往死胡同里带
            raise ValueError(
                "本会话未绑定业务数据源，无法从业务表归纳本体草案。"
                "请在「本体建模页」发起『生成本体模型』任务后重试")
        raise ValueError(f"数据源 {datasource_id} 无有效表元数据，请先执行元数据同步")

    # 按 domain_tag 分批，单批不超过 BATCH_MAX_TABLES
    groups: dict[str, list] = {}
    for t in tables:
        groups.setdefault(t.get("domain_tag") or "_default", []).append(t)
    batches: list[list] = []
    for group_tables in groups.values():
        for i in range(0, len(group_tables), BATCH_MAX_TABLES):
            batches.append(group_tables[i:i + BATCH_MAX_TABLES])

    kb_texts = _retrieve_kb_texts(batches, datasource_id, kb_ids) if kb_ids else [""] * len(batches)
    return [_schema_text(batch, meta, kb_text=txt) for batch, txt in zip(batches, kb_texts)]


def _retrieve_kb_texts(batches: list[list], datasource_id: int, kb_ids: list) -> list[str]:
    """逐批以表名作为线索从选定知识库检索业务知识（同步 qmind_retrieve）。

    返回与 batches 对齐的文本列表；无命中/检索不可用时返回带说明的标注（显式暴露，
    不回退成静默空段）。本函数在 to_thread 内同步执行，qmind CLI 子进程自带超时/熔断。
    """
    from backend.modules.mind.rag.qmind_retriever import qmind_retrieve

    ids = [int(x) for x in (kb_ids or []) if str(x).strip()]
    texts: list[str] = []
    for batch in batches:
        names = [t["table_name"] for t in batch][:8]
        question = "业务对象与关系、统计维度、指标口径：" + "、".join(names)
        try:
            res = qmind_retrieve(question, datasource_id=datasource_id, kb_ids=ids, top_k=5)
        except Exception as e:  # noqa: BLE001 — 检索失败不得影响主链，但要显式标注
            logger.warning("[ontology] KB retrieve failed (ds=%s): %s", datasource_id, e)
            texts.append(f"（知识库检索不可用: {e}）")
            continue
        chunks = (res or {}).get("chunks") or []
        if not chunks:
            rag_src = (res or {}).get("rag_source") or ""
            note = "（所选知识库未命中相关片段"
            if (res or {}).get("degraded"):
                note += f"，当前降级: {rag_src}"
            texts.append(note + "）")
            continue
        segs = []
        for c in chunks:
            title = str((c or {}).get("title") or "").strip()
            body = str((c or {}).get("content") or "").strip()
            if not body:
                continue
            segs.append(f"### {title}\n{body}" if title else body)
        texts.append("\n\n".join(segs))
    return texts


def _load_draft_objects(datasource_id: int) -> tuple[list, str, str]:
    """读取该源当前草案的 (objects, domain, description)；无草案返回空。

    按 (datasource_id, kind) 定位，避免误读同 datasource_id 下其他域的草案。
    现有草案内容损坏时 fail-loud（不得静默丢弃旧对象再覆盖，会丢用户数据）。
    """
    kind = "source" if datasource_id > 0 else "system"
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT json_content FROM adh_ontology_models "
                "WHERE datasource_id = %s AND kind = %s AND status = 'draft' "
                "ORDER BY updated_at DESC LIMIT 1",
                (datasource_id, kind))
            row = cur.fetchone()
    if not row:
        return [], "", ""
    try:
        doc = json.loads(row.get("json_content") or "{}")
    except (json.JSONDecodeError, TypeError) as e:
        raise ValueError(
            "现有草案内容损坏，无法并入新归纳结果；请删除该草案后重新生成") from e
    return (doc.get("objects") or [], doc.get("domain", "") or "",
            doc.get("description", "") or "")


def save_generated_draft(datasource_id: int, objects: list, domain: str = "",
                         description: str = "", append: bool = False,
                         created_by: str = "") -> dict:
    """把归纳出的业务对象落成草案（确定性校验/合并 + 三格式派生，无 LLM 调用）。

    Args:
        datasource_id: 目标源（0=系统本体域）；模型 kind 由其派生（>0 → source，0 → system）
        objects: 归纳出的对象列表（canonical JSON 的 objects[]）
        domain/description: 业务领域与概述（留空时并入模式下沿用现有草案的值）
        append: False=替换该源已有草案（全新归纳）；True=并入现有草案（分批归纳的后续批次）
        created_by: 创建人（服务端注入，不来自 LLM/请求体）

    同身份对象合并/并入指标等合并事实随 generation_warnings 显式带回，不静默；
    同身份不同主表是真冲突，直接中止（宁缺勿错，需人工裁决）。

    Returns:
        保存后的模型记录 dict（含 generation_warnings 合并告警）
    """
    if not isinstance(objects, list) or not objects:
        raise ValueError("objects 必须是非空对象列表")

    merged_objects: dict = {}          # 对象身份键 -> obj（按 object_identity_key 判重）
    merge_warnings: list = []          # 合并事实必须对外可见，不得静默
    base_domain, base_description = "", ""
    if append:
        existing_objects, base_domain, base_description = _load_draft_objects(datasource_id)
        if existing_objects:
            for obj in existing_objects:
                ik = object_identity_key(obj.get("key"))
                if ik:
                    merged_objects[ik] = obj
        else:
            merge_warnings.append("未找到可并入的已有草案，本批按全新草案落库")

    for obj in objects:
        if not isinstance(obj, dict):
            raise ValueError("objects 元素必须是对象")
        key = normalize_object_key(obj.get("key")) or str(obj.get("primary_table") or "")
        if not key:
            continue
        obj["key"] = key
        ik = object_identity_key(key)
        prev = merged_objects.get(ik)
        if prev is None:
            merged_objects[ik] = obj
            continue
        # 同身份重复：主表一致才可合并；主表不同是真冲突，必须人工裁决（宁缺勿错）
        if str(prev.get("primary_table") or "") != str(obj.get("primary_table") or ""):
            raise ValueError(
                "本体生成出现同名不同主表的对象冲突, 已中止(需人工裁决): "
                f"key={prev.get('key')}({prev.get('primary_table')}) vs "
                f"{obj.get('key')}({obj.get('primary_table')})")
        merged_objects[ik], _detail = merge_object_pair(prev, obj)
        merge_warnings.append(
            f"对象 '{key}' 重复出现, 已合并为一份（丢弃 {_detail['dropped']}"
            + (f"，并入指标 {_detail['gained_metrics']}" if _detail["gained_metrics"] else "")
            + "）")

    if not merged_objects:
        raise ValueError("未归纳出任何业务对象")

    domain = (domain or "").strip() or base_domain
    description = (description or "").strip() or base_description
    doc = {
        "datasource_id": datasource_id,
        "domain": domain,
        "description": description,
        "objects": list(merged_objects.values()),
    }

    # 落库：替换该源已有 draft（按 kind 限定，不动同 datasource_id 下其他域的草案）
    model_id = _gen_id()
    json_content = json.dumps(doc, ensure_ascii=False, indent=2)
    yaml_content = to_yaml(doc)
    md_content = to_md(doc)
    kind = "source" if datasource_id > 0 else "system"
    now = _now()

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM adh_ontology_models WHERE datasource_id = %s AND kind = %s "
                "AND status = 'draft'",
                (datasource_id, kind),
            )
            cur.execute(
                "INSERT INTO adh_ontology_models "
                "(id, datasource_id, kind, domain, name, status, json_content, yaml_content, md_content, "
                "object_count, created_by, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, 'draft', %s, %s, %s, %s, %s, %s, %s)",
                (model_id, datasource_id, kind, domain, f"{domain or '本体模型'}-draft",
                 json_content, yaml_content, md_content,
                 len(doc["objects"]), created_by, now, now),
            )
        conn.commit()

    result = get_model(model_id)
    if merge_warnings:
        logger.warning("[Ontology] save_generated_draft 合并重复对象 %d 处: %s",
                       len(merge_warnings), merge_warnings)
        # 合并事实显式带回给调用方，避免被当成"生成结果本来就干净"
        result["generation_warnings"] = merge_warnings
    return result


# ═══════════════════════════════════════════════════════════════════
# 三格式序列化（JSON 为事实源）
# ═══════════════════════════════════════════════════════════════════

def to_yaml(doc: dict) -> str:
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=120)


def _route_summary(route: dict) -> str:
    """路由指针的可读摘要（内部 md/对象展开行用，name 级信息）。"""
    if not isinstance(route, dict) or not route:
        return ""
    src = str(route.get("source_ontology") or "")
    ds_name = str(route.get("datasource_name") or "")
    if src:
        seg = f"路由至: 源本体「{src}」"
    else:
        seg = f"路由至: 数据源「{ds_name}」"
    if route.get("object_key"):
        seg += f"·对象「{route['object_key']}」"
    if src and ds_name:
        seg += f"（数据源: {ds_name}）"
    if route.get("mode"):
        seg += f"，模式: {route['mode']}"
    hints = route.get("filter_hints") or []
    hint_txt = "；".join(
        f"{h.get('dimension')}({'/'.join(str(x) for x in (h.get('examples') or [])[:5])})"
        for h in hints if (h or {}).get("dimension"))
    if hint_txt:
        seg += f"，过滤提示: {hint_txt}"
    return seg


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
    route_seg = _route_summary(obj.get("route"))
    if route_seg:
        lines.append(route_seg)

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
            enum = p.get("enum") or {}
            if enum:
                seg += f"；枚举: {'; '.join(f'{c}={l}' for c, l in enum_pairs(enum))}"
            lines.append(seg)

    links = obj.get("links") or []
    if links:
        lines.append("")
        lines.append("### 关系")
        for lk in links:
            seg = f"- {lk.get('type', 'references')} {lk.get('target', '')}"
            if lk.get("join"):
                seg += f"，join: {lk['join']}"
            if lk.get("join_type"):
                seg += f"，{lk['join_type']}"
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
            if isinstance(m, str):
                lines.append(f"- {m}")
                continue
            seg = f"- {_metric_name(m)}"
            if m.get("formula"):
                seg += f" = {m['formula']}"
            if m.get("agg_type"):
                seg += f"（聚合: {m['agg_type']}"
                seg += f"，单位: {m['unit']}）" if m.get("unit") else "）"
            elif m.get("unit"):
                seg += f"（单位: {m['unit']}）"
            if m.get("description"):
                seg += f"（{m['description']}）"
            lines.append(seg)

    # hydrate 视图补充：对象维度清单（存储 canonical 无 dimensions 时此段不渲染）
    dims = obj.get("dimensions") or []
    if dims:
        lines.append("")
        lines.append("### 维度")
        for d in dims:
            if isinstance(d, str):
                lines.append(f"- {d}")
                continue
            seg = f"- {d.get('display_name') or d.get('name', '')}"
            if d.get("target_column"):
                seg += f" ({d['target_column']})"
            if d.get("is_time"):
                seg += " [时间维度]"
            vl = d.get("value_labels") or {}
            if isinstance(vl, dict) and vl:
                seg += "；枚举: " + "; ".join(
                    f"{k}={v}" for k, v in sorted(vl.items(), key=lambda kv: str(kv[0])))
            if d.get("description"):
                seg += f"：{d['description']}"
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


# ── 云端脱敏渲染(知识库专用) ──────────────────────────────────────────
# 数据源黑盒原则(安全护栏 §7)延伸到"元数据出境": 上云文档只保留业务语义,
# 剔除物理表/列名、JOIN 表达式、指标 formula 原文、catalog_ref/数据源标识等。
# 与 to_md 分离: 内部检索/向量化仍用 to_md(含物理信息), 仅对外的知识库文档走本函数。

_CLOUD_STRIP_NOTICE = (
    "> 说明: 本文档仅供业务语义参考, 已剔除物理表/列名、JOIN 表达式与指标计算式等实现细节。"
)


def _cert_label(entry: dict = None) -> str:
    """口径权威性标注：已认证 / 草稿。

    **默认标草稿**——未认证口径不得被当成权威答案（认证机制一直在、
    但曾有 55 个口径零认证，若默认标权威就是在误导业务决策）。
    entry 来自字典（adh_metrics/adh_dimensions），带 certified/owner。
    """
    return "[已认证]" if (entry or {}).get("certified") else "[草稿]"


def _cloud_object_md(obj: dict, dataset_vars_fn=None, dict_metrics: dict = None) -> str:
    """单个对象的对外 MD 段(白名单渲染, 不含物理标识)。"""
    lines = [f"## 业务对象: {obj.get('display_name', obj.get('key', ''))} ({obj.get('key', '')})"]
    aliases = obj.get("aliases") or []
    if aliases:
        lines.append(f"别名: {', '.join(str(a) for a in aliases)}")
    if obj.get("description"):
        lines.append(f"描述: {obj['description']}")

    # ── 路由指引（业务本体=路由索引层）：告诉 LLM 该去哪个源本体检索/执行 ──
    # 只出 name 级信息（源本体名/数据源名/对象 key/维度名），不涉物理表列（护栏 §7）。
    route = obj.get("route")
    if isinstance(route, dict) and route:
        lines.append("")
        lines.append("### 路由指引（数据在哪）")
        seg = (f"- 数据在源本体「{route.get('source_ontology', '')}」"
               if route.get("source_ontology")
               else f"- 数据在数据源「{route.get('datasource_name', '')}」")
        if route.get("object_key"):
            seg += f" 的「{route['object_key']}」对象"
        if route.get("source_ontology") and route.get("datasource_name"):
            seg += f"（数据源: {route['datasource_name']}）"
        lines.append(seg)
        for h in (route.get("filter_hints") or []):
            if (h or {}).get("dimension"):
                ex = "、".join(str(x) for x in (h.get("examples") or []))
                seg = f"- 若问题涉及「{h['dimension']}」"
                if ex:
                    seg += f"（如 {ex}）"
                seg += "，请转为对应维度的过滤条件"
                lines.append(seg)
        lines.append("- 本对象不直接提供数据；请以目标源本体的实时目录（get_metrics）为准解析名称")

    # 逻辑执行视图: 只暴露 bind_kind / query_mode / 数据集名与其参数声明, 不暴露物理表
    binding = obj.get("execution_binding") or {}
    logical: list[str] = []
    if binding.get("bind_kind"):
        logical.append(f"执行方式={binding['bind_kind']}")
    if binding.get("query_mode"):
        logical.append(f"路由={binding['query_mode']}")
    ds_ref = binding.get("dataset_ref") or ""
    if logical:
        lines.append("执行(逻辑): " + " | ".join(logical))
    if ds_ref:
        seg = f"查询数据集: {ds_ref}"
        decl = dataset_vars_fn(ds_ref) if dataset_vars_fn else None
        if decl:
            params = ", ".join(
                f"{name}({(spec or {}).get('type', 'any')})" for name, spec in decl.items()
            )
            seg += f" — 需用 params 传入: {params}"
        lines.append(seg)

    props = obj.get("properties") or []
    rendered_props: list[str] = []
    for p in props:
        biz = (p.get("name") or "").strip()
        col = str(p.get("column") or "").strip().rsplit(".", 1)[-1]   # 带表前缀的取裸列名
        if not biz or biz == col:
            # 无业务名、或业务名照搬物理列名（如 name=del_flag）都跳过：
            # 照搬列名 = 未起业务名，渲染出去就是物理列名泄露（护栏 §7）
            continue
        seg = f"- {biz}"
        if p.get("type"):
            seg += f" ({p['type']}"
            seg += ", 主键" if p.get("is_key") else ""
            seg += ")"
        if p.get("description") and p["description"] != biz:
            seg += f": {p['description']}"
        enum = p.get("enum") or {}
        if isinstance(enum, dict) and enum:
            seg += "；枚举: " + "; ".join(
                f"{k}:{v}" for k, v in sorted(enum.items(), key=lambda kv: str(kv[0])))
        elif isinstance(enum, list) and enum:
            seg += f"；枚举: {'; '.join(str(e) for e in enum)}"
        rendered_props.append(seg)
    if rendered_props:
        lines.append("")
        lines.append("### 属性(业务名)")
        lines.extend(rendered_props)

    links = obj.get("links") or []
    if links:
        lines.append("")
        lines.append("### 关系")
        for lk in links:
            # 保留关系语义(type/target/基数/描述), 剔除 join 物理表达式
            seg = f"- {lk.get('type', 'references')} {lk.get('target', '')}"
            if lk.get("cardinality"):
                seg += f" ({lk['cardinality']})"
            if lk.get("description"):
                seg += f"：{lk['description']}"
            lines.append(seg)

    # ── 口径指标（字典层，带认证状态）────────────────────────────
    # 语义分层：字典指标 = 权威业务口径（有 certified/owner）；
    # 对象级 metrics = 派生计算字段（is_active/days_since_x 这类），不是口径。
    # 故「指标」段只渲染字典指标，派生字段另起一段且不标认证。
    dict_ms = (dict_metrics or {}).get(str(obj.get("key") or "")) or []
    if dict_ms:
        lines.append("")
        lines.append("### 指标（业务口径）")
        for e in dict_ms:
            seg = f"- {e.get('name', '')} {_cert_label(e)}"
            if e.get("description"):
                seg += f"（{e['description']}）"
            if e.get("owner"):
                seg += f" 责任人: {e['owner']}"
            lines.append(seg)

    # ── 行为（M2）：只渲染 label/effect，params_schema 不上云（含参数细节）──
    acts = obj.get("actions") or []
    if acts:
        lines.append("")
        lines.append("### 行为(可执行动作)")
        for a in acts:
            seg = f"- {a.get('label') or a.get('key', '')}"
            if a.get("effect"):
                seg += f"：{a['effect']}"
            if a.get("read_only", False):
                seg += "（只读）"
            lines.append(seg)

    # ── 派生字段（对象级 metrics，非口径）────────────────────────
    derived = obj.get("metrics") or []
    if derived:
        lines.append("")
        lines.append("### 派生字段（对象上的计算字段，非口径）")
        for m in derived:
            # 保留名称与文字说明, 剔除 formula 原文（可能含物理列名）
            name = _metric_name(m)
            if isinstance(m, str):
                lines.append(f"- {name}")
                continue
            seg = f"- {name}"
            if m.get("description"):
                seg += f"（{m['description']}）"
            lines.append(seg)

    # ── 维度（业务）：只出业务名/时间标注/枚举标签，剔 target_column 物理列（护栏 §7）──
    dims = obj.get("dimensions") or []
    if dims:
        lines.append("")
        lines.append("### 维度（业务）")
        for d in dims:
            if isinstance(d, str):
                lines.append(f"- {d}")
                continue
            biz = (d.get("display_name") or d.get("name") or "").strip()
            if not biz:
                continue
            seg = f"- {biz}"
            if d.get("is_time"):
                seg += "（时间维度）"
            vl = d.get("value_labels") or {}
            if isinstance(vl, dict) and vl:
                seg += "；枚举: " + "; ".join(
                    f"{k}:{v}" for k, v in sorted(vl.items(), key=lambda kv: str(kv[0])))
            if d.get("description"):
                seg += f"：{d['description']}"
            lines.append(seg)

    return "\n".join(lines)


def _cloud_path_md(path) -> str:
    """行为路径的对外渲染：只出业务名步骤序列。

    白名单字段：title / 步骤业务名 / 窗口 / 实体与时间维度的**业务名**。
    dataset_ref、事件谓词、物理列一律不渲染（护栏 §7：上云文档只能出人话）。
    """
    if not isinstance(path, dict):
        return ""
    steps = [str(s.get("name") or s.get("key") or "").strip()
             for s in (path.get("steps") or []) if isinstance(s, dict)]
    steps = [s for s in steps if s]
    if not steps:
        return ""
    seg = "  · 关键路径: " + " → ".join(steps)
    extras = []
    win = str(path.get("window_sec") or "").strip()
    if win.isdigit() and int(win) > 0:
        _w, _u = (int(win) // 60, "分钟") if int(win) >= 60 else (int(win), "秒")
        extras.append(f"窗口 {_w} {_u}")
    if str(path.get("entity") or "").strip():
        extras.append(f"按「{str(path['entity']).strip()}」判定")
    if extras:
        seg += f"（{'，'.join(extras)}）"
    return seg


def to_cloud_md(doc: dict, dataset_vars_fn=None, dict_metrics: dict = None) -> str:
    """把 canonical 本体 doc 渲染为可安全上云的业务语义文档(白名单)。

    dict_metrics: {object_key: [{name, description, certified, owner}]}，
    字典层口径指标（adh_metrics 按 bound_object_key 分组），由调用方查库后传入。
    不传时「指标（业务口径）」段为空，只渲染对象级派生字段——宁可少说，
    也不把派生字段冒充成权威口径。
    """
    parts = [f"# 本体模型: {doc.get('domain', '')}"]
    if doc.get("description"):
        parts.append("")
        parts.append(doc["description"])
    parts.append("")
    parts.append(_CLOUD_STRIP_NOTICE)
    parts.append("")
    for obj in doc.get("objects", []):
        parts.append(_cloud_object_md(obj, dataset_vars_fn, dict_metrics))
        parts.append("")
    # ── M3 规则：只出 statement（人话），enforcement 不上云（可能含过滤表达式/物理列）──
    rules = doc.get("rules") or []
    if rules:
        parts.append("### 业务规则与约束")
        for r in rules:
            seg = f"- [{r.get('key', '')}] {r.get('statement') or ''}".rstrip()
            if r.get("source"):
                seg += f"（依据: {r['source']}）"
            parts.append(seg)
        parts.append("")
    # ── M4 场景：只出 title/goal + 引用名，eval_seed 等内部建模信息不上云；
    # 路由场景额外渲染数据源清单与跨源关联说明（name 级，join_hint 是人话不含物理 JOIN）──
    scenarios = doc.get("scenarios") or []
    if scenarios:
        parts.append("### 典型使用场景")
        for s in scenarios:
            seg = f"- {s.get('title') or s.get('key', '')}"
            if s.get("goal"):
                seg += f"：{s['goal']}"
            parts.append(seg)
            # 行为路径：只渲染业务名步骤序列（dataset_ref/谓词/物理列一律不上云）
            seg = _cloud_path_md(s.get("path"))
            if seg:
                parts.append(seg)
            sroute = s.get("route") or {}
            for e in (sroute.get("sources") or []):
                e = e or {}
                seg2 = (f"  · 数据源「{e.get('datasource_name') or e.get('source_ontology') or ''}」"
                        f"（源本体「{e.get('source_ontology') or ''}」）")
                if e.get("object_keys"):
                    seg2 += f" 对象: {'、'.join(str(k) for k in e['object_keys'])}"
                parts.append(seg2)
            if sroute.get("join_hint"):
                parts.append(f"  · 跨源关联说明: {sroute['join_hint']}")
        parts.append("")
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════
# 读时 hydrate + Palantir 只读投影（结构化查看器专用，零写入）
# ═══════════════════════════════════════════════════════════════

def _hydrate_json(v):
    """字典 JSON 列（aliases/value_labels）可能是字符串，归一为对象；解析失败返回 None。"""
    if isinstance(v, (str, bytes)):
        try:
            return json.loads(v)
        except (ValueError, TypeError):
            return None
    return v


def _load_dict_by_object(keys: list, table: str, cols: str) -> dict:
    """按 bound_object_key 批量查字典（adh_metrics/adh_dimensions）生效行，分组返回。

    复用图谱侧同口径（bound_object_key 关联，字典为唯一权威 ontology-modeling §3）；
    只读、不落库。
    """
    keys = [str(k) for k in (keys or []) if k]
    if not keys:
        return {}
    marks = ",".join(["%s"] * len(keys))
    out: dict = {}
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {cols}, bound_object_key FROM {table} "
                f"WHERE is_active = 1 AND bound_object_key IN ({marks})",
                keys)
            for row in cur.fetchall() or []:
                out.setdefault(str(row.get("bound_object_key") or ""), []).append(row)
    return out


def hydrate_doc(doc: dict, datasource_id: int = 0) -> dict:
    """查看器读时投影：把对象 metrics 名字引用升为带口径对象、补 dimensions 清单。

    来源 = adh_metrics / adh_dimensions 按 bound_object_key（字典为唯一权威）；
    存储的 json_content 不变，本函数只读、不落库。datasource_id 仅为接口对齐保留
    （字典行 bound_object_key 全局唯一，无需额外数据源过滤）。
    """
    if not isinstance(doc, dict):
        return doc
    objects = doc.get("objects") or []
    keys = [str(o.get("key") or "") for o in objects if o.get("key")]
    metric_rows = _load_dict_by_object(
        keys, "adh_metrics",
        "name, name_en, formula, agg_type, unit, description, "
        "COALESCE(certified, 0) AS certified")
    dim_rows = _load_dict_by_object(
        keys, "adh_dimensions",
        "name, name_en, target_column, category, aliases, value_labels, description")
    out = json.loads(json.dumps(doc, ensure_ascii=False, default=str))  # 深拷贝，不改原 doc
    for obj in out.get("objects") or []:
        key = str(obj.get("key") or "")
        refs = [_metric_name(m) for m in (obj.get("metrics") or [])]
        hydrated: list = []
        seen: set = set()
        for row in metric_rows.get(key, []):
            nm = str(row.get("name") or "").strip()
            if not nm or nm in seen:
                continue
            seen.add(nm)
            hydrated.append({
                "name": nm,
                "display_name": nm,
                "name_en": str(row.get("name_en") or "").strip(),
                "formula": str(row.get("formula") or "").strip(),
                "agg_type": str(row.get("agg_type") or "").strip(),
                "unit": str(row.get("unit") or "").strip(),
                "description": str(row.get("description") or "").strip(),
                "certified": bool(row.get("certified")),
            })
        # 保留字典未登记的既有引用（不丢对象级派生字段），标 unbound 供视图区分
        dict_names = ({h["name"] for h in hydrated} | {h["name_en"] for h in hydrated})
        for r in refs:
            if r and r not in dict_names and r not in seen:
                seen.add(r)
                hydrated.append({"name": r, "unbound": True})
        if refs or hydrated:
            obj["metrics"] = hydrated
        dims: list = []
        for row in dim_rows.get(key, []):
            nm = str(row.get("name") or "").strip()
            if not nm:
                continue
            dims.append({
                "name": nm,
                "display_name": nm,
                "name_en": str(row.get("name_en") or "").strip(),
                "target_column": str(row.get("target_column") or "").strip(),
                "category": str(row.get("category") or "").strip(),
                "is_time": (str(row.get("category") or "") == "时间"),
                "value_labels": _hydrate_json(row.get("value_labels")) or {},
                "aliases": _hydrate_json(row.get("aliases")) or [],
                "description": str(row.get("description") or "").strip(),
            })
        obj["dimensions"] = dims
    return out


# Palantir cardinality 反向映射（镜像 ontology_yaml_import._CARD_MAP）
_CARD_REVERSE = {"1:N": "one_to_many", "N:1": "many_to_one",
                 "M:N": "many_to_many", "1:1": "one_to_one"}


def to_palantir(doc_hydrated: dict) -> dict:
    """canonical(已 hydrate) → Palantir 结构（只读投影，镜像 palantir_to_canonical 反向）。

    映射：object→object_type；property.enum→values；link→link_types（cardinality 反向）；
    hydrated metric.formula→computed_properties/expression；维度归入 properties
    （Palantir 无独立维度概念）。只读，不落库。
    """
    objs = (doc_hydrated or {}).get("objects") or []
    key_to_name = {str(o.get("key") or ""): str(o.get("display_name") or o.get("key") or "")
                   for o in objs}
    object_types: list = []
    all_metrics: list = []
    for o in objs:
        name = str(o.get("display_name") or o.get("key") or "")
        props: list = []
        for p in o.get("properties") or []:
            col = str(p.get("column") or "")
            pname = col.rsplit(".", 1)[-1] if col else str(p.get("name") or "")
            entry = {
                "name": pname or str(p.get("name") or ""),
                "type": str(p.get("type") or "string"),
                "is_primary_key": bool(p.get("is_key")),
                "description": str(p.get("description") or "").strip(),
            }
            enum = p.get("enum") or {}
            if isinstance(enum, dict) and enum:
                entry["values"] = {str(k): str(v) for k, v in enum.items()}
            props.append(entry)
        for d in o.get("dimensions") or []:
            tc = str(d.get("target_column") or "")
            dname = tc.rsplit(".", 1)[-1] if tc else str(d.get("name") or "")
            entry = {
                "name": dname,
                "type": "string",
                "is_primary_key": False,
                "description": str(d.get("description") or d.get("display_name") or "").strip(),
            }
            vl = d.get("value_labels") or {}
            if isinstance(vl, dict) and vl:
                entry["values"] = {str(k): str(v) for k, v in vl.items()}
            props.append(entry)
        link_types: list = []
        for lk in o.get("links") or []:
            tgt_key = str(lk.get("target") or "")
            card = str(lk.get("cardinality") or "")
            link_types.append({
                "name": lk.get("type") or "references",
                "target": key_to_name.get(tgt_key, tgt_key),
                "cardinality": _CARD_REVERSE.get(card, card),
                "join": str(lk.get("join") or ""),
                "description": str(lk.get("description") or "").strip(),
            })
        computed: list = []
        for m in o.get("metrics") or []:
            if isinstance(m, str):
                m = {"name": m}
            mn = str(m.get("name") or m.get("display_name") or "").strip()
            if not mn:
                continue
            expr = str(m.get("formula") or "").strip()
            desc = str(m.get("description") or "").strip()
            computed.append({"name": mn, "expression": expr, "description": desc})
            all_metrics.append({
                "name": mn, "object_type": name, "expression": expr,
                "filters": [], "description": desc,
            })
        object_types.append({
            "name": name,
            "api_name": str(o.get("key") or ""),
            "aliases": list(o.get("aliases") or []),
            "description": str(o.get("description") or "").strip(),
            "source": str(o.get("source_ref") or o.get("primary_table") or ""),
            "properties": props,
            "link_types": link_types,
            "computed_properties": computed,
        })
    domain = (doc_hydrated or {}).get("domain") or ""
    return {
        "platform": domain,
        "domain": domain,
        "description": (doc_hydrated or {}).get("description") or "",
        "object_types": object_types,
        "metrics": all_metrics,
        "cross_domain_links": [],
    }


def to_palantir_yaml(doc_hydrated: dict) -> str:
    return yaml.safe_dump(to_palantir(doc_hydrated), allow_unicode=True, sort_keys=False, width=120)


# ═══════════════════════════════════════════════════════════════════
# 模型 CRUD
# ═══════════════════════════════════════════════════════════════════

def _row_to_model(row: dict, include_content: bool = True) -> dict:
    model = {
        "id": row["id"],
        "datasource_id": row["datasource_id"],
        "name": row["name"],
        # kind/domain：前端按业务域归属分组（x5），不再按 datasource_id 猜分组
        "kind": row.get("kind") or "",
        "domain": row.get("domain") or "",
        "status": row["status"],
        "object_count": row.get("object_count", 0),
        "kb_id": row.get("kb_id"),
        "created_by": row.get("created_by", ""),
        "created_at": row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else row["created_at"],
        "updated_at": row["updated_at"].isoformat() if hasattr(row["updated_at"], "isoformat") else row["updated_at"],
    }
    if include_content:
        model["json_content"] = row.get("json_content") or ""
        model["yaml_content"] = row.get("yaml_content") or ""
        model["md_content"] = row.get("md_content") or ""
    return model


def list_models(datasource_id: int = None, include_archived: bool = False,
                allowed_datasource_ids: Optional[set] = None) -> list:
    """模型列表（不含大字段）。

    默认排除 archived —— 归档的历史版本属于对应模型的「版本管理」，
    由 list_versions 按 (datasource_id, name) 分组呈现，不再混在主列表。

    可见性（waker-datasource-domain §1 派生，不建第二套权限）：
    - 源本体：datasource_id ∈ allowed_datasource_ids 才可见（按用户数据源权限分配）；
    - 业务本体 / 系统本体：全员可见（业务=跨源共享语义，系统=RAG 知识层）。
    allowed_datasource_ids 三态：None=内部调用不限制；set=按授权过滤（**空集=源本体
    全不可见，fail-closed**，不得当全量）。
    """
    sql = (
        "SELECT id, datasource_id, name, kind, domain, status, object_count, kb_id, created_by, "
        "created_at, updated_at FROM adh_ontology_models"
    )
    conditions: list[str] = []
    params: list = []
    if datasource_id:
        # 数据源筛选只对**源本体**生效（物理来源）；业务本体跨源、系统本体独立，
        # 不因选了某数据源而被滤掉（x4 归属改造：本体归属是 domain+kind，不是数据源）。
        # 所以：本源的源本体 + 全部非源本体（业务/系统）。
        conditions.append("((kind = 'source' AND datasource_id = %s) OR kind <> 'source')")
        params.append(datasource_id)
    if allowed_datasource_ids is not None:
        # 源本体按数据源授权过滤（fail-closed：空集=无可见源本体）
        allowed = {int(x) for x in (allowed_datasource_ids or set())}
        if allowed:
            marks = ",".join(["%s"] * len(allowed))
            conditions.append(
                f"(kind <> 'source' OR datasource_id IN ({marks}))")
            params.extend(sorted(allowed))
        else:
            conditions.append("kind <> 'source'")
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


def can_view_model(model: dict, allowed_datasource_ids: Optional[set]) -> bool:
    """单模型可见性裁决（与 list_models 同口径，供详情/版本等读端点复用）。"""
    if allowed_datasource_ids is None:
        return True   # 内部调用
    if str(model.get("kind") or "") != "source":
        return True   # 业务/系统本体全员可见
    return int(model.get("datasource_id") or 0) in {int(x) for x in (allowed_datasource_ids or set())}


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


def _doc_fingerprint(json_content: str) -> str:
    """生效内容指纹：发布时校验草案基线是否仍匹配（防覆盖他人改动）。"""
    import hashlib
    return hashlib.md5(str(json_content or "").encode("utf-8")).hexdigest()


def save_draft_only(model_id: int, json_content: str, name: str = None,
                    created_by: str = "") -> dict:
    """只存草案，**不碰生效内容、不级联**（两段式第一段）。

    与 save_draft 的区别：save_draft 对 active 模型会就地改内容并立即级联（重展开 objects /
    重建图谱 / 同步知识库）；本函数只写 draft_json_content，生效口径保持不变，
    等 publish_draft 才落地。AS-BOT 通道一律走这里。
    """
    model = get_model(model_id)
    if not model:
        raise ValueError("模型不存在")
    if model["status"] == "archived":
        raise ValueError("已归档模型不可编辑；如需修改请先在「版本管理」中回滚激活")
    base = model.get("json_content") or ""
    # 草案阶段**不做破坏性拦截**：草案不碰生效内容、不级联，且有版本管理可回滚，
    # 写坏了也只是沙箱里的废稿。护栏移到 publish_draft/save_draft（真会生效的那一步），
    # 否则连“先存个草案看看”都做不到，反而逼调用方去开新模型规避。
    try:
        doc = json.loads(json_content)
    except ValueError as e:
        raise ValueError(f"json_content 不是合法 JSON: {e}")
    if not isinstance(doc, dict):
        raise ValueError("json_content 必须是对象")
    now = _now()
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE adh_ontology_models SET draft_json_content=%s, draft_base_stamp=%s, "
                "draft_updated_at=%s, draft_updated_by=%s" + (", name=%s" if name else "") +
                " WHERE id=%s",
                ((json.dumps(doc, ensure_ascii=False), _doc_fingerprint(base), now,
                  str(created_by or "")) + ((name,) if name else ()) + (model_id,)))
        conn.commit()
    out = get_model(model_id)
    out["draft_pending"] = True
    out["note"] = "改动已存为草案，未生效；需发布（publish）后才级联到对象表/图谱/知识库"
    return out


def get_draft(model_id: int) -> dict:
    """读草案状态（供 AS-BOT 与前端展示“有无待发布改动”）。"""
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, status, json_content, draft_json_content, "
                        "draft_base_stamp, draft_updated_at, draft_updated_by "
                        "FROM adh_ontology_models WHERE id=%s", (model_id,))
            row = cur.fetchone()
    if not row:
        raise ValueError("模型不存在")
    return {
        "id": row["id"], "name": row["name"], "status": row["status"],
        "has_draft": bool(row.get("draft_json_content")),
        "draft_updated_at": str(row.get("draft_updated_at") or ""),
        "draft_updated_by": row.get("draft_updated_by") or "",
        "draft": row.get("draft_json_content"),
    }


def publish_draft(model_id: int, published_by: str = "") -> dict:
    """把草案提升为生效版并级联（两段式第二段）。

    并发保护：草案写入时记录了生效内容指纹；若发布前生效版已被他人改动，
    本次发布会被拒（宁阻断勿覆盖他人工作），需重新基于最新生效版做草案。
    """
    model = get_model(model_id)
    if not model:
        raise ValueError("模型不存在")
    # 草案列不进 _row_to_model 白名单（避免随 API 响应外泄），这里单独取
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT draft_json_content, draft_base_stamp FROM adh_ontology_models "
                        "WHERE id=%s", (model_id,))
            drow = cur.fetchone() or {}
    draft = str(drow.get("draft_json_content") or "").strip()
    if not draft:
        raise ValueError("没有待发布的草案，无需发布")
    base_now = _doc_fingerprint(model.get("json_content") or "")
    if (drow.get("draft_base_stamp") or "") and drow["draft_base_stamp"] != base_now:
        raise ValueError(
            "生效版在草案写入后已被改动，本次发布已阻断（不覆盖他人工作）。"
            "请基于最新生效版重新做草案后再发布")
    # 落地 + 级联（重展开 objects / 重建图谱 / 同步知识库）
    result = save_draft(model_id, draft)
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE adh_ontology_models SET draft_json_content=NULL, "
                        "draft_base_stamp=NULL, draft_updated_at=NULL, draft_updated_by=NULL "
                        "WHERE id=%s", (model_id,))
        conn.commit()
    result["published"] = True
    result["published_by"] = published_by or ""
    return result


def _reject_destructive_overwrite(model: dict, json_content: str) -> None:
    """整份覆盖前校验：不得无损丢失已有对象的属性/关系定义（fail-loud）。

    历史风险：读接口（get_ontology_model）出于脱敏只回对象**摘要**（properties 为空数组），
    而写接口（save_draft/save_ontology_model）要求提交完整 JSON 并整份覆盖。
    调用方（含 AS-BOT）若凭摘要重建一份提交，会把已有对象的属性、枚举、关系定义**整份抹掉**——
    这不是“挂错对象”，而是破坏性写入。宁阻断勿损毁。
    """
    try:
        current = json.loads(model.get("json_content") or "{}")
        incoming = json.loads(json_content or "{}")
    except ValueError:
        return  # JSON 非法由后续解析报错，这里不重复报
    if not isinstance(current, dict) or not isinstance(incoming, dict):
        return
    cur_objs = {object_identity_key(o.get("key") or ""): o for o in (current.get("objects") or [])}
    if not cur_objs:
        return
    new_objs = {object_identity_key(o.get("key") or ""): o for o in (incoming.get("objects") or [])}
    lost_detail: list[str] = []
    for key, old in cur_objs.items():
        new = new_objs.get(key)
        if new is None:
            old_props = len(old.get("properties") or [])
            if old_props or (old.get("links") or []):
                lost_detail.append(f"{key}(整对象丢失，含 {old_props} 个属性)")
            continue
        old_p, new_p = len(old.get("properties") or []), len(new.get("properties") or [])
        old_l, new_l = len(old.get("links") or []), len(new.get("links") or [])
        if new_p < old_p or new_l < old_l:
            lost_detail.append(f"{key}(属性 {old_p}→{new_p}，关系 {old_l}→{new_l})")
    if lost_detail:
        raise ValueError(
            "本次提交会造成已有定义丢失，已阻断（宁阻断勿损毁）："
            + "；".join(lost_detail[:10])
            + "。若确需删减，请在「本体建模页」操作，或提交与现有定义等量/更完整的 JSON。"
              "注意读接口出于脱敏只回对象摘要（properties 为空），不能拿摘要重建整份 JSON")


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
    _reject_destructive_overwrite(model, json_content)

    try:
        doc = json.loads(json_content)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 解析失败: {e}")
    if not isinstance(doc.get("objects"), list) or not doc["objects"]:
        raise ValueError("JSON 必须包含非空 objects 数组")

    # 字段名归一（防旧字段名漂移）：历史 doc / LLM 直存常写 object_key / name，
    # 不归一会让图谱类名变成 Unknown、字典 bound_object_key 找不到对象。
    renamed = normalize_object_field_aliases(doc)
    if renamed:
        logger.info("[ontology] 模型 %s 保存前对象字段名归一: %s",
                    model_id, sorted(set(renamed)))

    # 迁移后写闸（fail-loud）：metrics 仅名字字符串、enum 仅 {code: label} 对象
    fmt_errors = validate_doc_formats(doc)
    if fmt_errors:
        raise ValueError("本体条目格式不符合规范，拒绝保存: " + "; ".join(fmt_errors[:10]))

    # 补齐上下文，供绑定重算 / 派生使用。**按 kind 归一**（x4 归属改造）：
    # 源本体的 datasource_id 是物理来源（保留）；业务/系统本体的归属是 domain+kind，
    # 不得把 datasource_id 写回 doc —— 否则会把新结构（domain+kind+sources）的业务本体
    # 改回旧结构，造成 canonical 新旧结构混杂。
    kind = str(model.get("kind") or "")
    if kind == "source":
        doc["datasource_id"] = model["datasource_id"]
        doc.setdefault("datasource_name", model.get("datasource_name") or "")
    else:
        doc.pop("datasource_id", None)   # 业务/系统本体：归属是 domain+kind，不是数据源
        doc["kind"] = kind or doc.get("kind") or ""
        doc.setdefault("sources", [])
    doc.setdefault("domain", model.get("domain") or "")
    doc.setdefault("description", "")

    # v0.5(B6b): execution_binding 不再是 doc 可编辑事实源——物理绑定唯一在部署
    # 绑定层(adh_ontology_bindings)。存量/误编辑带入的字段在此迁移性剥离(非静默，log 注明)。
    _stripped = _strip_execution_bindings(doc)
    if _stripped:
        logger.info("[ontology] save_draft: 剥离 %d 处 execution_binding（部署绑定层唯一事实）", _stripped)

    # 业务本体路由化校验（§4 宁阻断勿悬空）：结构/引用错误拒绝保存，不静默落库。
    # 旧形态业务本体（带物理绑定）在迁移前保存即被拒（fail-loud），提示走迁移脚本。
    if kind == "business":
        route_errors = [it for it in find_business_route_issues(doc)
                        if it["severity"] == "error"]
        if route_errors:
            raise ValueError(
                "业务本体路由校验未通过，拒绝保存: "
                + "; ".join(it["message"] for it in route_errors[:10])
                + (" 等" if len(route_errors) > 10 else ""))

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
            # 改名（可选 name）：版本家族按 datasource_id+name 归组（list_versions），
            # 故同名归档历史版本须同步改名，否则 rename 后版本管理视图丢失历史；
            # 撞名（另一家族已占用新名）fail-loud 拒绝，不静默合并家族。
            if name is not None:
                name = name.strip()
                if not name:
                    raise ValueError("模型名字不能为空")
                if name != str(model.get("name") or ""):
                    cur.execute(
                        "SELECT id FROM adh_ontology_models "
                        "WHERE COALESCE(datasource_id, 0) = COALESCE(%s, 0) "
                        "AND name = %s AND id <> %s LIMIT 1",
                        (model["datasource_id"], name, model_id),
                    )
                    if cur.fetchone():
                        raise ValueError(f"已存在同名模型（含历史版本）: {name}")
                    cur.execute(
                        "UPDATE adh_ontology_models SET name = %s "
                        "WHERE COALESCE(datasource_id, 0) = COALESCE(%s, 0) "
                        "AND name = %s AND id <> %s",
                        (name, model["datasource_id"], model["name"], model_id),
                    )
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
    #
    # 重建结果必须进下面的 validation_warnings：以前这里直接丢弃返回值，图谱没建成
    # 只有服务端日志知道，页面上是"保存成功"——那是把失败掩成成功的降级掩盖
    # (no-silent-degradation §1)。也不反过来让保存失败：图谱不可用时本体编辑
    # 仍应可行（图是派生投影，事实源是 json_content），所以取**显式告警**口径。
    graph_rebuild: dict | None = None
    if model["status"] == "active":
        from backend.modules.catalog.services import ontology_yaml_import as _yimp
        graph_rebuild = _yimp._rebuild_graph(model["datasource_id"],
                                             str(model.get("kind") or ""))
    updated = get_model(model_id)

    # 缺陷显式暴露(no-silent-degradation)：
    #   - 对象 key 重复：任何状态都告警，激活时硬阻断（见 activate）；
    #   - 字典孤儿引用：沿用"草案告警 / 激活阻断"口径。
    warnings: list = []
    try:
        for dup in find_duplicate_object_keys(doc):
            warnings.append(
                "对象 key 重复(不区分大小写与分隔符判定，激活会被阻断): "
                + " / ".join(dup["keys"]))
    except Exception as e:  # noqa: BLE001 — 校验不影响保存主链路
        logger.warning("[Ontology] save_draft duplicate check failed: %s", e)
    try:
        unreg = find_unregistered_tables(doc)
        if unreg:
            warnings.append(
                "对象绑在未登记为数据产品的裸表上，激活会被阻断: " + ", ".join(unreg[:10]))
    except Exception as e:  # noqa: BLE001 — 校验不影响保存主链路
        logger.warning("[Ontology] save_draft product check failed: %s", e)
    if model["status"] == "draft":
        try:
            orphans = find_orphan_dict_bindings(doc)
        except Exception as e:  # noqa: BLE001 — 校验不影响保存主链路
            logger.warning("[Ontology] save_draft orphan check failed: %s", e)
            orphans = []
        if orphans:
            logger.warning("[Ontology] draft model=%s 存在字典孤儿引用(不阻断): %s",
                           model_id, orphans)
            warnings += [f"字典行绑定到不存在的对象, 激活前需修正: {o}" for o in orphans]
    try:
        # M2/M3/M4 建模位：引用悬空/结构非法 → 草案告警、激活硬阻断（§4 同口径）
        for it in find_m234_issues(doc):
            if it["severity"] == "error":
                warnings.append(f"[{it['category']}] {it['message']}")
    except Exception as e:  # noqa: BLE001 — 校验不影响保存主链路
        logger.warning("[Ontology] save_draft m234 check failed: %s", e)
    if graph_rebuild and not graph_rebuild.get("success"):
        warnings.append(
            "[图谱] 本次保存后的图重建未完成（文档已存库，图是滞后状态）："
            f"{str(graph_rebuild.get('error') or graph_rebuild.get('message') or '未给出原因')[:240]}；"
            "可手动触发 POST /api/graph/sync 重建，或调 GRAPH_BUILD_DEADLINE_S / "
            "GRAPH_WRITE_CHUNK 后重试")

    if warnings:
        updated["validation_warnings"] = warnings
    return updated


def set_model_kb(model_id: int, kb_id: Optional[int]) -> dict:
    """【已退役】为模型选定目标知识库。知识库归属已按裁决统一，不再按模型选库。

    归属裁决（按 kind）：业务本体与 AS-BOT 系统本体**共用**全局 sync_ontology 知识库
    （RAG 检索是两者共同用途）；源本体不做 RAG（只服务语义检索/取数）。
    故 per-model 选库入口废除；传入任何 kb_id 一律显式拒绝，不静默忽略。
    存量 kb_id 列冻结保留（审计追溯），不再参与路由。
    """
    raise ValueError(
        "知识库归属已统一：业务本体与 AS-BOT 系统本体共用全局知识库，"
        "源本体不做 RAG（只服务语义检索），无需也不能按模型选择知识库")


def _expand_objects(doc: dict, model_id: int, datasource_id: int, kind: str = "") -> int:
    """把 canonical doc 的对象展开写入 adh_ontology_objects（幂等：先清本模型再插）。

    kind 决定互斥域（见下）；不传时退回 doc.kind / 旧口径。
    返回写入对象数。activate 与激活态编辑保存共用。
    """
    # 读取边界字段名归一（幂等）：存量 doc 用旧名 object_key/name，不归一会把
    # 展开行的 object_key/display_name 写成**空串**（对象检索/图谱/预览全部拿不到对象）。
    # save_draft 路径已归一过一次，这里兼顾“只激活不重存”的存量模型。
    normalize_object_field_aliases(doc)
    objects = doc.get("objects") or []
    records = []
    kind = str(kind or doc.get("kind") or "")
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
            # 下线互斥域内其它模型对象 + 清理本模型旧对象（重激活/重保存幂等）。
            # 互斥域按 kind（x4 归属改造）：source=同数据源内互斥；business/system
            # 跨源且 datasource_id 同为 0，同桶互删会把系统本体对象清掉（历史缺陷），
            # 故改为同 kind 域内互斥。
            if kind in ("business", "system"):
                cur.execute(
                    "DELETE FROM adh_ontology_objects WHERE model_id IN "
                    "(SELECT id FROM adh_ontology_models WHERE kind = %s AND id != %s)",
                    (kind, model_id),
                )
            else:
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
            for code, label in enum_pairs(p.get("enum")):
                entry["labels"][code] = label
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
    """激活态编辑保存时的联动刷新（v0.5 B6b）：重展开对象 + 内嵌绑定回填 + enum→字典联动。

    execution_binding 不再是 doc 可编辑事实源：物理绑定唯一在部署绑定层
    （adh_ontology_bindings，deploy_contract_bindings 管理），保存不重算/不重写
    绑定行；对象展开行的 execution_binding 内嵌列由 _backfill_execution_binding
    从绑定行回填（派生投影，供检索/展示与 Level 2 兑底）。
    图谱重建不在此处触发：需等 save_draft 将最新 json_content 落库后再调，
    否则 _merge_ontology_models 会读到旧内容，使图谱滞后一次保存。
    """
    from backend.modules.catalog.services import ontology_yaml_import as _yimp

    ds_name = _yimp._datasource_name_by_id(datasource_id)
    if ds_name:
        doc["datasource_name"] = ds_name
    _expand_objects(doc, model_id, datasource_id, kind=str(doc.get("kind") or ""))
    _backfill_execution_binding(model_id)
    # 本体 enum/属性中文名 → 语义维度字典(别名与枚举标签的唯一联动点，各 kind 统一)
    sync_enums_to_dimensions(doc, datasource_id)


def _strip_execution_bindings(doc: dict) -> int:
    """剥离 doc.objects[].execution_binding（v0.5 B6b：物理绑定唯一在部署绑定层）。"""
    n = 0
    for o in doc.get("objects") or []:
        if "execution_binding" in (o or {}):
            o.pop("execution_binding", None)
            n += 1
    return n


def _backfill_execution_binding(model_id: int) -> int:
    """对象展开行 execution_binding 内嵌列从部署绑定行回填（派生投影）。

    事实源唯一是 adh_ontology_bindings；内嵌列仅供检索/展示与
    binding_resolver Level 2 兑底，不参与写回。
    """
    import json as _json
    from backend.common.db.metadata_db import get_metadata_conn

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT object_key, product_ref, physical_table, column_map, bind_kind, "
                "query_mode, sync_state, dataset_ref FROM adh_ontology_bindings "
                "WHERE model_id = %s AND status = 'active'", (model_id,))
            rows = cur.fetchall() or []
        agg: dict[str, dict] = {}
        for r in rows:
            key = str(r["object_key"])
            cm = r.get("column_map")
            if isinstance(cm, str):
                try:
                    cm = _json.loads(cm or "{}")
                except ValueError:
                    cm = {}
            eb = {
                "product_ref": str(r.get("product_ref") or ""),
                "physical_table": str(r.get("physical_table") or ""),
                "column_map": cm or {},
                "bind_kind": str(r.get("bind_kind") or ""),
                "query_mode": str(r.get("query_mode") or ""),
                "sync_state": str(r.get("sync_state") or ""),
                "dataset_ref": str(r.get("dataset_ref") or ""),
            }
            if key not in agg or (not agg[key].get("product_ref") and eb["product_ref"]):
                agg[key] = eb
        with conn.cursor() as cur:
            for key, eb in agg.items():
                cur.execute(
                    "UPDATE adh_ontology_objects SET execution_binding = %s "
                    "WHERE model_id = %s AND object_key = %s AND is_active = 1",
                    (_json.dumps(eb, ensure_ascii=False), model_id, key))
        conn.commit()
    return len(agg)


def find_unregistered_tables(doc: dict) -> list:
    """检查本体对象的来源表是否已登记为数据产品（本体只绑数据产品）。

    返回未登记的 `datasource.table` 清单（空 = 全部已登记）。

    为什么要强制：本体对象绑在「数据源连接 + 裸物理表名」上，回答不了
    谁产出/列变了谁负责/能否被引用。数据产品是这些表的治理身份，
    绑了产品才有契约与责任归属。存量已由
    `scripts/claim_ontology_tables_as_products.py` 批量认领，故开硬阻断不会误伤。

    匹配口径：优先 `datasource_name + physical_table`；doc 无 datasource_name 时
    退回只按 physical_table（宽松，仅用于告警而非阻断的场景）。
    """
    objects = doc.get("objects") or []
    ds_name = str(doc.get("datasource_name") or "").strip()
    pairs: list = []
    for o in objects:
        t = str(o.get("primary_table") or "").strip()
        if t:
            pairs.append((ds_name, t))
    if not pairs:
        return []
    uniq = sorted(set(pairs))
    # 走 data_product_service.find_by_table 作为**唯一查询口径**，不另造判断
    from backend.modules.catalog.services import data_product_service as dps
    missing: list = []
    for ds, table in uniq:
        if not dps.find_by_table(ds, table):
            missing.append(f"{ds + '.' if ds else ''}{table}")
    return missing


def find_orphan_dict_bindings(doc: dict) -> list[str]:
    """检查指标/维度字典的悬空绑定, 返回孤儿清单(空 = 干净)。

    范围: 生效字典行中 target_table 属于本模型对象 primary_table 且 bound_object_key 非空的行;
    bound_object_key 不再指向模型内任何对象 key/别名即孤儿(字符串松耦合的级联校验)。
    比较不区分大小写(与 MySQL utf8mb4_0900_ai_ci 行为对齐)。
    """
    objects = doc.get("objects") or []
    keys_norm = {str(o.get("key") or "").strip().lower() for o in objects} - {""}
    keys_norm |= {str(a).strip().lower() for o in objects for a in (o.get("aliases") or [])}
    tables = {str(o.get("primary_table") or "").strip() for o in objects} - {""}
    orphans: list[str] = []
    if not tables:
        # 业务本体/路由索引层对象无主表：走全局孤儿口径（见 _orphan_dict_bindings_global）
        return _orphan_dict_bindings_global(doc)
    placeholders = ", ".join(["%s"] * len(tables))
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            for tbl in ("adh_metrics", "adh_dimensions"):
                try:
                    cur.execute(
                        f"SELECT name, bound_object_key FROM {tbl} "
                        f"WHERE is_active = 1 AND bound_object_key IS NOT NULL "
                        f"  AND bound_object_key != '' AND target_table IN ({placeholders})",
                        tuple(tables),
                    )
                    for r in cur.fetchall():
                        if str(r["bound_object_key"]).strip().lower() not in keys_norm:
                            orphans.append(
                                f"{tbl}:{r.get('name')} → {r['bound_object_key']}")
                except Exception:
                    conn.rollback()  # 旧库无该列/表: 跳过不致命
    return orphans


def _orphan_dict_bindings_global(doc: dict) -> list[str]:
    """无主表模型（业务本体/路由索引层）的字典孤儿检查（全局口径）。

    业务对象没有 primary_table，按 target_table 圈不出“本模型的字典行”——
    改为全局口径：生效字典行的 bound_object_key 不在**任何 active 模型**
    对象 key/别名集合内即孤儿（bound_object_key 字符串松耦合，悬空必须显式
    暴露，§4 宁阻断勿悬空；报出的孤儿即使属别的模型也一并列出）。
    """
    objects = doc.get("objects") or []
    all_keys: set = {
        str(o.get("key") or "").strip().lower() for o in objects} - {""}
    all_keys |= {str(a).strip().lower() for o in objects for a in (o.get("aliases") or [])}
    orphans: list[str] = []
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT json_content FROM adh_ontology_models WHERE status = 'active'")
            for r in cur.fetchall():
                try:
                    d = json.loads(r.get("json_content") or "{}")
                except (ValueError, TypeError):
                    continue
                for o in (d.get("objects") or []):
                    all_keys.add(str(o.get("key") or "").strip().lower())
                    all_keys |= {str(a).strip().lower() for a in (o.get("aliases") or [])}
            for tbl in ("adh_metrics", "adh_dimensions"):
                try:
                    cur.execute(
                        f"SELECT name, bound_object_key FROM {tbl} "
                        f"WHERE is_active = 1 AND bound_object_key IS NOT NULL "
                        f"  AND bound_object_key != ''")
                    for r in cur.fetchall():
                        if str(r["bound_object_key"]).strip().lower() not in all_keys:
                            orphans.append(
                                f"{tbl}:{r.get('name')} → {r['bound_object_key']}")
                except Exception:
                    conn.rollback()  # 旧库无该列/表: 跳过不致命
    return orphans


# ═══════════════════════════════════════════════════════════════════
# M2/M3/M4 建模位校验（行为/规则/场景）—— 引用一致性 + 建模规范
# ═══════════════════════════════════════════════════════════════════
# 五层口径: M1 对象 / M2 行为(objects[].actions[]) / M3 规则(rules[]) /
#           M4 场景(scenarios[]) / M_Metric 指标。三层全部是 canonical 内嵌声明，
#           AS_BOT 动作注册与角色动作矩阵是其派生投影（单一可编辑源，§1）。
# 校验口径与字典孤儿引用同款（§4 宁阻断勿悬空）：
#   引用悬空/结构非法 = error → save_draft 告警、activate 硬阻断；
#   建模建议项（写动作缺风险声明等）= warn → 只报告，不阻断。

RULE_TYPES = {"permission", "metric_constraint", "business_constraint", "quality"}
PERMISSION_LEVELS = {"allow", "require_approval", "deny"}
ACTION_RISKS = {"low", "medium", "high"}


def _m234_action_keys(doc: dict) -> set:
    """doc 内全部对象级 action key（供规则/场景引用校验）。"""
    return {
        str(a.get("key") or "").strip()
        for o in (doc.get("objects") or [])
        for a in (o.get("actions") or [])
        if str(a.get("key") or "").strip()
    }


def validate_actions(doc: dict) -> list:
    """M2 行为建模校验。返回 issues [{category, severity, message}]。"""
    issues: list = []
    seen: dict = {}          # action_key -> 首次出现的对象（doc 级唯一）
    for o in (doc.get("objects") or []):
        okey = str(o.get("key") or "")
        for a in (o.get("actions") or []):
            akey = str(a.get("key") or "").strip()
            if not akey:
                issues.append({"category": "action", "severity": "error", "object": okey,
                               "message": f"对象 {okey} 存在无 key 的行为声明"})
                continue
            if akey in seen:
                issues.append({"category": "action", "severity": "error", "object": okey,
                               "message": f"行为 key 重复: {akey!r}（{seen[akey]} 与 {okey}）；"
                                          f"action_key 是审批通道注册键，必须全局唯一"})
            else:
                seen[akey] = okey
            if not str(a.get("label") or "").strip():
                issues.append({"category": "action", "severity": "warn", "object": okey,
                               "message": f"行为 {akey!r} 缺 label（对外展示名）"})
            ps = a.get("params_schema")
            if ps is not None and not isinstance(ps, dict):
                issues.append({"category": "action", "severity": "error", "object": okey,
                               "message": f"行为 {akey!r} 的 params_schema 必须是对象结构"})
            risk = str(a.get("risk") or "")
            if risk and risk not in ACTION_RISKS:
                issues.append({"category": "action", "severity": "error", "object": okey,
                               "message": f"行为 {akey!r} 的 risk={risk!r} 不合法"
                                          f"（允许: {'/'.join(sorted(ACTION_RISKS))}）"})
            if not a.get("read_only", False):
                # 写动作必须声明风险级与审批要求——没风险声明的动作无法决定要不要人确认
                if not risk:
                    issues.append({"category": "action", "severity": "warn", "object": okey,
                                   "message": f"写行为 {akey!r} 未声明 risk，无法确定审批要求"
                                              f"（高危写动作应 requires_approval=true）"})
                if not isinstance(a.get("requires_approval"), bool):
                    issues.append({"category": "action", "severity": "warn", "object": okey,
                                   "message": f"写行为 {akey!r} 未声明 requires_approval（true/false）"})
    return issues


def validate_rules(doc: dict) -> list:
    """M3 规则建模校验（权限/口径/业务约束/质量）。"""
    issues: list = []
    action_keys = _m234_action_keys(doc)
    seen: set = set()
    for r in (doc.get("rules") or []):
        rkey = str(r.get("key") or "").strip()
        if not rkey:
            issues.append({"category": "rule", "severity": "error", "object": "",
                           "message": "存在无 key 的规则声明"})
            continue
        if rkey in seen:
            issues.append({"category": "rule", "severity": "error", "object": rkey,
                           "message": f"规则 key 重复: {rkey!r}"})
        seen.add(rkey)
        rtype = str(r.get("type") or "")
        if rtype not in RULE_TYPES:
            issues.append({"category": "rule", "severity": "error", "object": rkey,
                           "message": f"规则 {rkey!r} 的 type={rtype!r} 不合法"
                                      f"（允许: {'/'.join(sorted(RULE_TYPES))}）"})
            continue
        if not str(r.get("statement") or "").strip():
            issues.append({"category": "rule", "severity": "warn", "object": rkey,
                           "message": f"规则 {rkey!r} 缺 statement（可解释给用户的人话）"})
        enf = r.get("enforcement")
        if not isinstance(enf, dict):
            issues.append({"category": "rule", "severity": "error", "object": rkey,
                           "message": f"规则 {rkey!r} 缺 enforcement（执行语义，投影/裁决的依据）"})
            continue
        if rtype == "permission":
            level = str(enf.get("level") or "")
            if level not in PERMISSION_LEVELS:
                issues.append({"category": "rule", "severity": "error", "object": rkey,
                               "message": f"权限规则 {rkey!r} 的 level={level!r} 不合法"
                                          f"（允许: {'/'.join(sorted(PERMISSION_LEVELS))}）"})
            if not str(enf.get("subject") or "").strip():
                issues.append({"category": "rule", "severity": "error", "object": rkey,
                               "message": f"权限规则 {rkey!r} 缺 subject（授权给谁，如 role:admin）"})
            act = str(enf.get("action") or "").strip()
            if not act:
                issues.append({"category": "rule", "severity": "error", "object": rkey,
                               "message": f"权限规则 {rkey!r} 缺 action（授权哪个行为，key 或 *）"})
            elif act != "*" and act not in action_keys:
                # 引用一致性（§4）：授权指向不存在的行为 = 悬空，宁阻断勿悬空
                issues.append({"category": "rule", "severity": "error", "object": rkey,
                               "message": f"权限规则 {rkey!r} 指向不存在的行为 {act!r}"})
    return issues


def validate_scenarios(doc: dict) -> list:
    """M4 场景建模校验：场景是引用（对象/行为/规则 key），引用悬空即 error。"""
    issues: list = []
    obj_keys = {str(o.get("key") or "").strip().lower() for o in (doc.get("objects") or [])}
    action_keys = {k.lower() for k in _m234_action_keys(doc)}
    rule_keys = {str(r.get("key") or "").strip().lower() for r in (doc.get("rules") or [])}
    seen: set = set()
    for s in (doc.get("scenarios") or []):
        skey = str(s.get("key") or "").strip()
        if not skey:
            issues.append({"category": "scenario", "severity": "error", "object": "",
                           "message": "存在无 key 的场景声明"})
            continue
        if skey in seen:
            issues.append({"category": "scenario", "severity": "error", "object": skey,
                           "message": f"场景 key 重复: {skey!r}"})
        seen.add(skey)
        if not str(s.get("title") or "").strip():
            issues.append({"category": "scenario", "severity": "warn", "object": skey,
                           "message": f"场景 {skey!r} 缺 title"})
        checks = (("uses_objects", s.get("uses_objects"), obj_keys, "对象"),
                  ("uses_actions", s.get("uses_actions"), action_keys, "行为"),
                  ("uses_rules", s.get("uses_rules"), rule_keys, "规则"))
        for field, refs, valid, label in checks:
            for ref in (refs or []):
                rk = str(ref or "").strip().lower()
                if rk and rk not in valid:
                    issues.append({"category": "scenario", "severity": "error", "object": skey,
                                   "message": f"场景 {skey!r} 的 {field} 引用了不存在的{label} {ref!r}"})
    return issues


def validate_paths(doc: dict) -> list:
    """行为路径（scenarios[].path）结构校验：路径是"有序步骤口径"，不是关联关系。

    语义层只能编译单层聚合，漏斗/路径的"按实体判序 → 再去重计数"靠模板对象承载；
    本校验保证路径声明本身可确定解析（步序连续、实体/时间可对齐），否则宁拒勿猜。
    """
    issues: list = []
    seen: set = set()
    for s in (doc.get("scenarios") or []):
        path = s.get("path")
        if path is None:
            continue
        skey = str(s.get("key") or "").strip()
        if not isinstance(path, dict):
            issues.append({"category": "path", "severity": "error", "object": skey,
                           "message": f"场景 {skey!r} 的 path 不是对象结构"
                                      "（应为 {entity, event_time, window_sec, steps[]}）"})
            continue
        pkey = str(path.get("key") or skey).strip()
        if not pkey:
            issues.append({"category": "path", "severity": "error", "object": skey,
                           "message": "路径缺 key（且所在场景无 key 可推导）"})
            continue
        if pkey in seen:
            issues.append({"category": "path", "severity": "error", "object": skey,
                           "message": f"路径 key 重复: {pkey!r}（重复会使检索命中歧义）"})
        seen.add(pkey)
        for fld in ("entity", "event_time"):
            val = path.get(fld)
            if val is not None and not str(val).strip():
                issues.append({"category": "path", "severity": "error", "object": pkey,
                               "message": f"路径 {pkey!r} 的 {fld} 为空（要么不写，要么给业务名）"})
        win = path.get("window_sec")
        if win is not None:
            try:
                if int(win) <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                issues.append({"category": "path", "severity": "error", "object": pkey,
                               "message": f"路径 {pkey!r} 的 window_sec={win!r} 需为正整数（秒）"})
        steps = path.get("steps") or []
        if not isinstance(steps, list) or not steps:
            issues.append({"category": "path", "severity": "error", "object": pkey,
                           "message": f"路径 {pkey!r} 缺 steps（至少 2 步才能构成转化路径）"})
            continue
        orders: list = []
        for i, st in enumerate(steps, start=1):
            if not isinstance(st, dict):
                issues.append({"category": "path", "severity": "error", "object": pkey,
                               "message": f"路径 {pkey!r} 第 {i} 步不是对象结构"})
                continue
            if not str(st.get("name") or st.get("key") or "").strip():
                issues.append({"category": "path", "severity": "error", "object": pkey,
                               "message": f"路径 {pkey!r} 第 {i} 步缺业务名（对用户只能展示业务名）"})
            if not str(st.get("event") or "").strip():
                issues.append({"category": "path", "severity": "warn", "object": pkey,
                               "message": f"路径 {pkey!r} 第 {i} 步未声明 event（该步靠什么事件判定），"
                                          "漏斗口径易歧义"})
            raw_order = st.get("order")
            try:
                orders.append(int(raw_order if raw_order is not None else i))
            except (TypeError, ValueError):
                issues.append({"category": "path", "severity": "error", "object": pkey,
                               "message": f"路径 {pkey!r} 第 {i} 步 order={raw_order!r} 非整数"})
        if orders and orders != list(range(1, len(orders) + 1)):
            issues.append({"category": "path", "severity": "error", "object": pkey,
                           "message": f"路径 {pkey!r} 步序必须从 1 连续不重复（实得 {orders}），"
                                      "否则层间转化率无法确定归属"})
    return issues


def find_m234_issues(doc: dict) -> list:
    """M2/M3/M4 建模位全部校验（行为/规则/场景/路径），供 quality/save_draft/activate 共用。

    旧模型无这三层字段时返回空——存量零影响。"""
    return validate_actions(doc) + validate_rules(doc) + validate_scenarios(doc) + validate_paths(doc)


def _active_source_ontology_index() -> dict:
    """active 源本体索引（薄委托）。

    实现已下沉到 `backend/core/ontology_index.py`：语义层（topology 承载校验/选路）与
    目录层都要用，留在本模块会构成 `semantics → modules` 反向依赖（分层契约 R3 硬禁）。
    保留同名函数是为不破坏既有调用点（本体生成器/路由校验/测试桩）。
    """
    from backend.core.ontology_index import active_source_ontology_index
    return active_source_ontology_index()


def _datasource_name_set() -> set:
    """系统注册数据源名集合（薄委托，同上）。"""
    from backend.core.ontology_index import datasource_name_set
    return datasource_name_set()


def _retired_find_business_route_issues_v04(doc: dict) -> list:
    """【已退役 v0.5，保留供审计追溯，不再被调用】业务本体（kind=business）路由化校验：路由索引层的结构与引用一致性。

    业务本体是「概念 → 数据源」的路由索引层（数据资产路由图，不是源本体副本），
    三类缺陷按 §4 宁阻断勿悬空给 error：
    - 形态错误：对象携带物理绑定（primary_table / properties[].column）——
      物理事实唯一在源本体，存量旧形态须先跑迁移脚本；
    - 路由缺失/解析失败：route 缺目标（datasource_name 与 source_ontology 至少一项）、
      datasource_name 不是注册数据源、source_ontology 解析不到 active 源本体、
      两者给出但数据源不一致（防错配）；
    - 路由悬空：route.object_key / 场景 route 的 source/object_keys 不在目标源本体。
    存量旧形态业务本体在迁移（scripts/convert_business_ontology_to_route.py）前
    会命中形态错误——保存即拒绝（fail-loud），迁移后再编辑。
    """
    issues: list = []
    index = _active_source_ontology_index()
    known = sorted(index)
    ds_names = _datasource_name_set()

    def _err(obj: str, message: str) -> None:
        issues.append({"category": "route", "severity": "error",
                       "object": obj, "message": message})

    for o in (doc.get("objects") or []):
        key = str(o.get("key") or "")
        # ① 形态：业务本体不得携带物理绑定（物理事实唯一在源本体）
        if str(o.get("primary_table") or "").strip():
            _err(key, f"对象 {key} 携带 primary_table（物理绑定）——业务本体是路由索引层，"
                      "物理绑定只属于源本体；请删除该字段并声明 route"
                      "（存量迁移: scripts/convert_business_ontology_to_route.py）")
        for p in (o.get("properties") or []):
            if str(p.get("column") or "").strip():
                _err(key, f"对象 {key} 的属性 {p.get('name') or p.get('column')} 携带物理列绑定"
                          "（properties[].column）——业务本体只留业务概念与路由，物理属性请建在源本体")
                break
        # ② route 必填与解析（数据源名为核心、源本体可选；名字为稳定键，禁雪花 id）
        route = o.get("route")
        if not isinstance(route, dict):
            route = {}
        src_name = str(route.get("source_ontology") or "").strip()
        ds_name = str(route.get("datasource_name") or "").strip()
        if not src_name and not ds_name:
            _err(key, f"对象 {key} 缺少路由目标（route.datasource_name 或 route.source_ontology 至少一项），无法路由")
            continue
        src = index.get(src_name) if src_name else None
        if src_name and not src:
            _err(key, f"对象 {key} 的 route.source_ontology={src_name!r} 解析不到 active 源本体"
                      f"（可用: {'、'.join(known[:10]) or '无'}）")
            continue
        mode = str(route.get("mode") or "source")
        if mode not in ("source", "object_filter"):
            _err(key, f"对象 {key} 的 route.mode={mode!r} 非法（只支持 source / object_filter）")
        if ds_name:
            if ds_name not in ds_names:
                _err(key, f"对象 {key} 的 route.datasource_name={ds_name!r} 不是系统注册数据源"
                          f"（可用: {'、'.join(sorted(ds_names)[:10]) or '无'}）")
            elif src and src["datasource_name"] and ds_name != src["datasource_name"]:
                _err(key, f"对象 {key} 的 route.datasource_name={ds_name!r} 与目标源本体数据源"
                          f" {src['datasource_name']!r} 不一致（防错配，二者取其一修正）")
        obj_ref = str(route.get("object_key") or "").strip()
        if obj_ref and src and obj_ref not in src["object_keys"]:
            _err(key, f"对象 {key} 的 route.object_key={obj_ref!r} 不在目标源本体 {src_name!r} 的对象集中")
        elif obj_ref and not src:
            issues.append({"category": "route", "severity": "warn", "object": key,
                           "message": f"对象 {key} 的 route.object_key 无 source_ontology 可校验，"
                                      "建议补 source_ontology 以校验目标对象存在性"})
        if mode == "object_filter" and not (route.get("filter_hints") or []):
            issues.append({"category": "route", "severity": "warn", "object": key,
                           "message": f"对象 {key} 是 object_filter 路由但无 filter_hints，"
                                      "LLM 无法得知该按哪个源内维度过滤"})
    # ③ 场景路由（单源/跨源场景，scenarios[].route 携带多源与 join_hint）
    for s in (doc.get("scenarios") or []):
        skey = str(s.get("key") or "")
        sroute = s.get("route")
        if not sroute:
            continue
        if not isinstance(sroute, dict):
            _err(skey, f"场景 {skey} 的 route 不是对象结构")
            continue
        entries = sroute.get("sources") or []
        if not entries:
            _err(skey, f"场景 {skey} 声明了 route 但 sources 为空")
            continue
        for entry in entries:
            name = str((entry or {}).get("source_ontology") or "").strip()
            if not name:
                _err(skey, f"场景 {skey} 的 route.sources 存在缺少 source_ontology 的条目")
                continue
            src = index.get(name)
            if not src:
                _err(skey, f"场景 {skey} 的 route.sources 引用 {name!r} 解析不到 active 源本体")
                continue
            for k in (entry.get("object_keys") or []):
                if str(k) not in src["object_keys"]:
                    _err(skey, f"场景 {skey} 在 {name!r} 中引用对象 {k!r} 不存在于该源本体")
    return issues


def find_business_route_issues(doc: dict) -> list:
    """【v0.5 退役】业务本体"路由索引层"校验已随多站点语义网退场（B5/B6）。

    v0.4 终态（GLOBAL_SEMANTIC_LAYER_DESIGN §3）：业务本体=人编业务知识图谱（契约），
    可携带物理绑定（B6b 前暂存 doc，之后移出至部署绑定层）；route 层已退役。
    恒返回空——不再阻断契约形态的保存；旧范式实现在
    _retired_find_business_route_issues_v04（仅供审计追溯）。
    """
    return []


# 历史背景：objects[].actions[] 与 rules[] permission 声明曾投影到
# adh_as_bot_action_registry / adh_as_bot_role_actions（AS-BOT 动作权限矩阵 +
# 审批通道）。AS-BOT 与 AS-BOT 统一后该机制整体退役：动作权限由菜单与功能
# 权限码（adh_perm_registry + adh_role_perms）+ AS-BOT 工具授权直接把关执行；
# actions[]/rules[] 仅保留为 canonical 里的建模描述，无运行时消费方。


def find_deploy_site_issues(model_id: int) -> list[str]:
    """模型级适用站点（deploy_sites）的引用一致性检查（§4 宁阻断勿悬空）。

    配置时已校过一次，但站点/数据源可能在之后被停用、改名或删除：取数时
    `_route_by_site` 会拿不到路由目标而拒查，因此在激活环节提前显式暴露，
    而不是等用户提问才发现。只报业务名，不回内部 id。
    """
    from backend.core.site_service import get_site, model_deploy_sites

    declared = model_deploy_sites(int(model_id or 0))
    if not declared:
        return []  # 未配置 = 沿用逐对象部署绑定行（源本体/存量），不是缺陷
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT name, site_code FROM adh_datasources")
            ds_site = {str(r["name"]): str(r["site_code"] or "") for r in (cur.fetchall() or [])}
    known = set(ds_site)
    issues: list[str] = []
    seen: set[str] = set()
    for entry in declared:
        code = entry["site_code"]
        ds = entry["datasource"]
        site = get_site(code) or {}
        if not site:
            issues.append(f"站点 {code} 不在站点清单（先在系统管理-站点维护）")
        elif not int(site.get("is_active") or 0):
            issues.append(f"站点「{site.get('display_name') or code}」已停用")
        elif int(site.get("is_aggregate") or 0):
            issues.append(f"站点「{site.get('display_name') or code}」是聚合态，不能挂数据源")
        if code in seen:
            issues.append(f"站点 {code} 重复登记")
        seen.add(code)
        if not ds:
            issues.append(f"站点 {code} 未选择路由数据源")
        elif ds not in known:
            issues.append(f"站点 {code} 路由的数据源「{ds}」已不存在（删除或改名）")
        elif ds_site[ds] != code:
            issues.append(
                f"数据源「{ds}」的归属站点与声明站点 {code} 不一致（先在数据源管理校正）")
    return issues


def activate(model_id: int) -> dict:
    """激活模型：旧 active 归档，逐对象 MD 段写入 adh_ontology_objects（供对象检索/预览）。

    硬阻断两类缺陷：
    - 字典孤儿引用：激活会使指向已消失对象的字典行悬空；
    - 对象 key 重复：重复对象会让检索命中歧义、图谱出重复节点，宁可拒绝激活。
    """
    model = get_model(model_id)
    if not model:
        raise ValueError("模型不存在")
    if model["status"] == "active":
        return model

    # 模型级适用站点的引用一致性（§4）：站点/数据源被停用、删除或改归属时拒绝激活。
    # 它是模型级配置、与 doc 内容无关，故先于 doc 级校验（不拿一堆取不到数的站点上线）。
    site_issues = find_deploy_site_issues(model_id)
    if site_issues:
        raise ValueError(
            "本体适用站点配置存在悬空引用, 拒绝激活（在「适用站点」页修正后重试）: "
            + "; ".join(site_issues[:10]) + (" 等" if len(site_issues) > 10 else ""))

    doc = json.loads(model["json_content"])
    objects = doc.get("objects") or []
    if not objects:
        raise ValueError("模型无对象，无法激活")

    # 存量旧 doc 可能从未被再保存：激活前同样先归一字段名（幂等），再走写闸校验。
    normalize_object_field_aliases(doc)

    # 迁移后写闸（fail-loud）：metrics 仅名字字符串、enum 仅 {code: label} 对象
    fmt_errors = validate_doc_formats(doc)
    if fmt_errors:
        raise ValueError("本体条目格式不符合规范，拒绝激活: " + "; ".join(fmt_errors[:10]))

    dups = find_duplicate_object_keys(doc)
    if dups:
        raise ValueError(
            "存在对象 key 重复(不区分大小写与分隔符判定), 拒绝激活(先合并重复对象): "
            + "; ".join(" / ".join(d["keys"]) for d in dups[:10])
            + (" 等" if len(dups) > 10 else ""))

    # 本体只绑数据产品：未登记为数据产品的裸表不得作为对象来源（P2.3）
    unregistered = find_unregistered_tables(doc)
    if unregistered:
        raise ValueError(
            "存在对象绑在未登记为数据产品的裸表上, 拒绝激活"
            "（先用 scripts/claim_ontology_tables_as_products.py 认领或手工登记数据产品）: "
            + ", ".join(unregistered[:10])
            + (" 等" if len(unregistered) > 10 else ""))

    orphans = find_orphan_dict_bindings(doc)
    if orphans:
        raise ValueError(
            "存在字典行绑定到本模型已不存在的对象, 拒绝激活(先修正 bound_object_key 或重新建对象): "
            + "; ".join(orphans[:10]) + (" 等" if len(orphans) > 10 else ""))

    # M2/M3/M4 建模位引用一致性（§4 宁阻断勿悬空）：悬空引用/结构非法拒绝激活
    m234_errors = [it for it in find_m234_issues(doc) if it["severity"] == "error"]
    if m234_errors:
        raise ValueError(
            "行为/规则/场景建模存在引用悬空或结构非法, 拒绝激活: "
            + "; ".join(f"[{it['category']}] {it['message']}" for it in m234_errors[:10])
            + (" 等" if len(m234_errors) > 10 else ""))

    # 业务本体路由化（§4 宁阻断勿悬空）：路由缺失/悬空拒绝激活
    if str(model.get("kind") or "") == "business":
        route_errors = [it for it in find_business_route_issues(doc)
                        if it["severity"] == "error"]
        if route_errors:
            raise ValueError(
                "业务本体路由缺失或悬空, 拒绝激活: "
                + "; ".join(it["message"] for it in route_errors[:10])
                + (" 等" if len(route_errors) > 10 else ""))

    datasource_id = model["datasource_id"]
    model_kind = str(model.get("kind") or "")
    now = _now()

    # 1. 旧 active 归档 + 其对象下线
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            if model_kind == "source":
                # 源本体按数据源归属（UI「源本体（按数据源）」）：激活某数据源的源本体只应取代
                # **同源**的旧 active 源本体，不得归档其它数据源的源本体——否则多源各自建模会互相
                # 顶掉（历史缺陷：为日志源建源本体把医疗 Doris 源本体挤成 archived）。
                # 与 _expand_objects 的 source 分支（按 datasource_id 互斥）保持同口径。
                cur.execute(
                    "UPDATE adh_ontology_models SET status = 'archived', updated_at = %s "
                    "WHERE kind = 'source' AND datasource_id = %s AND status = 'active' AND id != %s",
                    (now, datasource_id, model_id),
                )
            else:
                # business/system 维持 kind 级单例（跨源共享的业务/系统语义层唯一 active）。
                cur.execute(
                    "UPDATE adh_ontology_models SET status = 'archived', updated_at = %s "
                    "WHERE kind = %s AND status = 'active' AND id != %s",
                    (now, model_kind, model_id),
                )
            cur.execute(
                "UPDATE adh_ontology_models SET status = 'active', updated_at = %s WHERE id = %s",
                (now, model_id),
            )
        conn.commit()

    # 2. 展开对象写入元数据库（不再向量化；embedding 列写零向量占位）
    count = _expand_objects(doc, model_id, datasource_id, kind=model_kind)
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


def restore(model_id: int) -> dict:
    """还原已归档模型为草案（可继续编辑，确认后再激活）。

    仅允许 archived → draft：归档数据必须可见可逆（UI 版本管理/归档视图消费）；
    对象展开与派生物重建不在此处直接改写，走后续 save_draft/activate 级联。
    """
    model = get_model(model_id)
    if not model:
        raise ValueError("模型不存在")
    if model["status"] != "archived":
        raise ValueError("仅已归档模型可还原")
    now = _now()
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE adh_ontology_models SET status = 'draft', updated_at = %s WHERE id = %s",
                (now, model_id),
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


# ═══════════════════════════════════════════════════════════════════
# 本体质量校验（P4）：把建模规范变成可执行检查
# ═══════════════════════════════════════════════════════════════════
# 规范出处：ontology-modeling.md §2（面向业务命名、属性须有业务中文名）、
# §4（引用一致性宁阻断勿悬空）、§7（解析宁缺勿错）。
# 质量问题是**建模待办**，不是缺陷清单——所以只报告、不阻断（阻断在 activate）。

# display_name 照搬表名/类名的判定：纯 ASCII 标识符，或与 key/主表同名
_BORROWED_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def check_doc_quality(doc: dict) -> dict:
    """本体质量体检：返回问题清单与分类计数（只报告，不修改 doc）。

    检查项：
      1. display_name 照搬表名/英文类名（应面向业务命名，如「订单」而非 Order）
      2. 属性缺业务名（无业务名的属性上云渲染会被整条跳过）
      3. link 目标悬空（指向 doc 内不存在的对象 key/别名）
      4. 指标 formula 形状异常（整条 SELECT 而非聚合表达式，可能含物理列）
      5. 对象 key 重复（复用 find_duplicate_object_keys）
    """
    objects = doc.get("objects") or []
    issues: list = []
    keys_norm: set = set()
    for o in objects:
        keys_norm.add(str(o.get("key") or "").strip().lower())
        for a in (o.get("aliases") or []):
            keys_norm.add(str(a).strip().lower())

    for o in objects:
        key = str(o.get("key") or "")
        disp = str(o.get("display_name") or "")
        table = str(o.get("primary_table") or "")

        # 1) 命名照搬表名/类名
        if disp and (disp == key or disp == table
                     or (disp.lower() == key.replace("_", "").lower())
                     or _BORROWED_NAME_RE.match(disp)):
            issues.append({
                "category": "naming", "severity": "warn", "object": key,
                "message": f"对象 {key} 的 display_name={disp!r} 像表名/类名，"
                           f"应面向业务命名（如「订单」）"})

        # 2) 属性缺业务名（含业务名照搬物理列名——那等于没起）
        props = o.get("properties") or []
        missing = [str(p.get("column") or "?") for p in props
                   if not str(p.get("name") or "").strip()
                   or str(p.get("name") or "").strip()
                   == str(p.get("column") or "").strip().rsplit(".", 1)[-1]]
        if missing:
            issues.append({
                "category": "property", "severity": "warn", "object": key,
                "message": f"对象 {key} 有 {len(missing)}/{len(props)} 个属性缺业务名，"
                           f"上云渲染会整条跳过（如 {missing[:3]}）"})

        # 3) link 目标悬空
        for lk in (o.get("links") or []):
            tgt = str(lk.get("target") or "").strip().lower()
            if tgt and tgt not in keys_norm:
                issues.append({
                    "category": "link", "severity": "error", "object": key,
                    "message": f"对象 {key} 的关系指向不存在的对象 {lk.get('target')!r}"
                               f"（悬空引用，§4 宁阻断勿悬空）"})

        # 4) 指标 formula 形状（仅旧格式 dict 条目；新格式名字引用的口径在字典层）
        for m in (o.get("metrics") or []):
            if not isinstance(m, dict):
                continue
            formula = str(m.get("formula") or "").strip()
            if not formula:
                continue
            if re.match(r"(?is)^\s*select\b", formula):
                issues.append({
                    "category": "metric", "severity": "warn", "object": key,
                    "message": f"指标 {m.get('name')!r} 的 formula 是整条 SELECT 而非聚合表达式，"
                               f"口径形状与同类不一致"})

    # 5) 重复 key
    for dup in find_duplicate_object_keys(doc):
        issues.append({
            "category": "duplicate", "severity": "error", "object": dup["keys"][0],
            "message": f"对象 key 重复（不区分大小写与分隔符）: {' / '.join(dup['keys'])}"})

    # 6) M2/M3/M4 建模位（行为/规则/场景）：引用悬空/结构非法 + 建模建议
    issues.extend(find_m234_issues(doc))

    # 7) 业务本体路由化（kind=business 或已声明 route 的对象）：路由索引层校验
    if str(doc.get("kind") or "").lower() == "business" or any(
            isinstance(o.get("route"), dict) for o in objects):
        issues.extend(find_business_route_issues(doc))

    by_cat: dict = {}
    for it in issues:
        by_cat[it["category"]] = by_cat.get(it["category"], 0) + 1
    errors = sum(1 for i in issues if i["severity"] == "error")
    return {
        "issues": issues,
        "by_category": by_cat,
        "total": len(issues),
        "errors": errors,
        "warnings": len(issues) - errors,
        # 评分是粗略指标：阻断级按 20 分扣，建议项按 2 分扣且封顶 40——
        # 否则对象一多就会被打到 0 分，反而看不出"阻断级已清零"这个事实
        "score": max(0, 100 - errors * 20 - min(len(issues) - errors, 20) * 2),
        "note": ("质量良好" if not issues else
                 f"{errors} 个阻断级问题、{len(issues) - errors} 个建议项；"
                 f"建议项不阻断保存，但会影响上云渲染与检索质量"),
    }
