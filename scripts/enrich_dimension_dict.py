#!/usr/bin/env python3
"""语义维度字典增强回填(幂等):

1. 从 active 本体模型的 objects.properties[].enum/description 同步
   value_labels + aliases(复用 ontology_service.sync_enums_to_dimensions, 单一实现);
2. 本体没覆盖、但列注释含 "0:否,1:是" / "3=UPLOAD(已上传)" 模式的, 解析为候选写入;
3. 为常用指标补 aliases(业务别名);
4. 为状态类维度(category='状态')的枚举 label 生成术语词条, 进术语库/图谱;
5. 打印"有枚举语义但字典缺维度行"的缺口清单供人工决策(不自动建维度)。

用法: ./venv/bin/python scripts/enrich_dimension_dict.py [datasource_id]
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, 'services/.env'))
load_dotenv(os.path.join(ROOT, '.env'))

from services.shared.common.db.metadata_db import get_metadata_conn
from services.datacatalog.services.ontology_service import (
    sync_enums_to_dimensions, _enum_item_to_label)

DS_DEFAULT = 1780478236183
WS = 1


def _rid():
    return int(time.time() * 1000000)


def _now():
    return time.strftime('%Y-%m-%d %H:%M:%S')


# ── 1. 本体 enum → value_labels/aliases ──────────────────────────
def step_ontology(conn, ds):
    with conn.cursor() as cur:
        cur.execute("SELECT id, json_content FROM adh_ontology_models "
                    "WHERE datasource_id=%s AND status='active' LIMIT 1", (ds,))
        row = cur.fetchone()
    if not row:
        print('[1] 无 active 本体模型, 跳过')
        return {'updated': 0, 'conflicts': [], 'gaps': []}
    doc = json.loads(row['json_content']) if isinstance(row['json_content'], str) else row['json_content']
    result = sync_enums_to_dimensions(doc, ds)
    print(f"[1] 本体同步: updated={result['updated']} conflicts={len(result['conflicts'])} "
          f"gaps={len(result['gaps'])}")
    return result


# ── 1.5 为"本体有枚举但字典无维度行"的缺口列建维度(治理步骤) ────
def _status_category(col: str, desc: str) -> str:
    s = f"{col} {desc}"
    if re.search(r"status|state|状态", s, re.I):
        return '状态'
    if re.search(r"type|kind|类型|种类", s, re.I):
        return '属性'
    return '属性'


def step_fill_gaps(conn, ds):
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM adh_ontology_models WHERE datasource_id=%s AND status='active' LIMIT 1", (ds,))
        m = cur.fetchone()
        if not m:
            return 0
        cur.execute("SELECT json_content FROM adh_ontology_models WHERE id=%s", (m['id'],))
        doc = cur.fetchone()['json_content']
        doc = json.loads(doc) if isinstance(doc, str) else doc
        # 表 → 本体对象键(snake_case 优先)
        cur.execute("SELECT object_key, physical_table FROM adh_ontology_bindings "
                    "WHERE model_id=%s AND bind_kind='primary' AND status='active'", (m['id'],))
        tbl2obj: dict[str, str] = {}
        for r in cur.fetchall():
            k, t = r['object_key'], r['physical_table']
            if not t:
                continue
            if t not in tbl2obj or ('_' in k and '_' not in tbl2obj[t]):
                tbl2obj[t] = k
        created = 0
        seen_cols: set[tuple[str, str]] = set()
        for obj in doc.get('objects') or []:
            for p in obj.get('properties') or []:
                col_ref = str(p.get('column') or '')
                if '.' not in col_ref or not (p.get('enum') or []):
                    continue
                tbl, col = col_ref.split('.', 1)
                if (tbl, col) in seen_cols:
                    continue
                seen_cols.add((tbl, col))
                cur.execute("SELECT id FROM adh_dimensions WHERE is_active=1 "
                            "AND target_table=%s AND target_column=%s", (tbl, col))
                if cur.fetchone():
                    continue
                labels = {}
                for it in p['enum']:
                    parsed = _enum_item_to_label(it)
                    if parsed:
                        labels[parsed[0]] = parsed[1]
                if len(labels) < 2:
                    continue
                name = str(p.get('description') or p.get('name') or col).strip()[:64]
                # 避免跨表同名(两个"状态")导致解析歧义: 重名时拼上列名
                cur.execute("SELECT id FROM adh_dimensions WHERE name=%s", (name,))
                if cur.fetchone():
                    name = f"{name}({col})"[:64]
                cat = _status_category(col, name)
                labels_json = json.dumps(labels, ensure_ascii=False)
                alias_json = json.dumps(sorted({str(p.get('name') or ''), col} - {name, ''}),
                                        ensure_ascii=False)
                cur.execute("SELECT COALESCE(MAX(id),0)+1 AS nid FROM adh_dimensions")
                nid = cur.fetchone()['nid']
                cur.execute(
                    "INSERT INTO adh_dimensions (id, name, name_en, level, target_table, "
                    "target_column, description, category, datasource_id, is_active, "
                    "created_at, updated_at, bound_object_key, aliases, value_labels) "
                    "VALUES (%s,%s,%s,0,%s,%s,%s,%s,0,1,%s,%s,%s,%s,%s)",
                    (nid, name, p.get('name') or '', tbl, col,
                     f'本体枚举属性自动建档({obj.get("key")}); 来源: 数据字典补全',
                     cat, _now(), _now(), obj.get('key') or tbl2obj.get(tbl) or None,
                     alias_json, labels_json))
                created += 1
                print(f'[1.5] 建维度 {tbl}.{col} name={name} enum={len(labels)}')
        conn.commit()
    return created


# ── 2. 列注释解析补充(只写已有维度行, 不覆盖已有 value_labels) ─────
_COMMENT_SEG = re.compile(r'(\d{1,2})\s*[:=：]\s*([^,，;；、()（）\s]{1,12})')

def step_comments(conn, ds):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name, column_name, "
            "COALESCE(NULLIF(business_desc,''), column_comment) AS cmt "
            "FROM adh_column_metadata WHERE datasource_id=%s AND is_active=1", (ds,))
        cols = cur.fetchall()
        n_written = 0
        for c in cols:
            cmt = str(c.get('cmt') or '')
            segs = _COMMENT_SEG.findall(cmt)
            if len(segs) < 2:
                continue  # 单个 0/1 之外的模式不可靠, 只收多段枚举
            labels = {}
            for code, label in segs:
                parsed = _enum_item_to_label(f"{code}={label}")
                if parsed:
                    labels[parsed[0]] = parsed[1]
            if len(labels) < 2:
                continue
            cur.execute(
                "SELECT id, value_labels FROM adh_dimensions WHERE is_active=1 "
                "AND target_table=%s AND target_column=%s", (c['table_name'], c['column_name']))
            dims = cur.fetchall()
            for d in dims:
                old = d.get('value_labels')
                if isinstance(old, (str, bytes)):
                    try:
                        old = json.loads(old)
                    except (ValueError, TypeError):
                        old = None
                if old:
                    continue  # 已有标签(本体或人工)优先
                cur.execute("UPDATE adh_dimensions SET value_labels=%s, updated_at=%s WHERE id=%s",
                            (json.dumps(labels, ensure_ascii=False), _now(), d['id']))
                n_written += 1
                print(f"[2] {c['table_name']}.{c['column_name']} <- 注释解析: {labels}")
        conn.commit()
    print(f'[2] 注释解析补充: {n_written} 个维度')


# ── 3. 指标别名(常用业务叫法) ─────────────────────────────────────
METRIC_ALIASES = {
    '案例数量': ['病例数', 'case数', 'total_cases', '口扫案例数'],
    '订单量': ['订单数', '下单量', 'order_count'],
    '用户数': ['注册用户数', '客户数', 'user_count'],
    '企业数': ['公司数', '商户数', 'company_count'],
    '设备总数': ['设备数', 'equipment_count'],
    '工单数': ['售后工单数', 'work_order_count'],
    'AI会话数': ['会话数', '对话数', 'ai_conversation_count'],
}

def step_metric_aliases(conn):
    n = 0
    with conn.cursor() as cur:
        for name, alias in METRIC_ALIASES.items():
            cur.execute("SELECT id, aliases FROM adh_metrics WHERE name=%s AND workspace_id=%s",
                        (name, WS))
            row = cur.fetchone()
            if not row:
                continue
            old = row.get('aliases')
            if isinstance(old, (str, bytes)):
                try:
                    old = json.loads(old)
                except (ValueError, TypeError):
                    old = None
            merged = sorted(set(old or []) | set(alias))
            if list(old or []) != merged:
                cur.execute("UPDATE adh_metrics SET aliases=%s, updated_at=%s WHERE id=%s",
                            (json.dumps(merged, ensure_ascii=False), _now(), row['id']))
                n += 1
        conn.commit()
    print(f'[3] 指标别名补充: {n}')


# ── 4. 状态类维度的枚举 label 生成术语词条 ─────────────────────────
def step_terms_from_labels(conn, ds):
    n = 0
    with conn.cursor() as cur:
        cur.execute(
            "SELECT name, target_table, target_column, value_labels FROM adh_dimensions "
            "WHERE is_active=1 AND category='状态' AND value_labels IS NOT NULL")
        dims = cur.fetchall()
        for d in dims:
            labels = d.get('value_labels')
            if isinstance(labels, (str, bytes)):
                try:
                    labels = json.loads(labels)
                except (ValueError, TypeError):
                    continue
            for code, label in (labels or {}).items():
                label = str(label).strip()
                if not label or len(label) > 12:
                    continue
                cur.execute("SELECT id FROM adh_business_terms WHERE term_cn=%s AND workspace_id=%s",
                            (label, WS))
                if cur.fetchone():
                    continue
                cur.execute(
                    "INSERT INTO adh_business_terms (id, datasource_id, workspace_id, term_cn, "
                    "term_en, term_aliases, term_type, target_table, target_column, calculation, "
                    "description, usage_count, is_active, created_at, updated_at) "
                    "VALUES (%s,%s,%s,%s,'','','dimension',%s,%s,'',"
                    "%s,0,1,%s,%s)",
                    (_rid(), ds, WS, label, d['target_table'], d['target_column'],
                     f'枚举值: {d["name"]}={code}', _now(), _now()))
                n += 1
        conn.commit()
    print(f'[4] 枚举 label 生成术语: {n} 条')
    return n


def main():
    ds = int(sys.argv[1]) if len(sys.argv) > 1 else DS_DEFAULT
    conn = get_metadata_conn()
    try:
        step_ontology(conn, ds)
        filled = step_fill_gaps(conn, ds)
        r2 = step_ontology(conn, ds)  # 新建行后再同步一次 labels/aliases
        step_comments(conn, ds)
        step_metric_aliases(conn)
        step_terms_from_labels(conn, ds)
        print(f'[1.5] 缺口建维度: {filled}')
        if r2['gaps']:
            print('[5] 剩余缺口(本体有枚举但字典无维度行, 需人工决策):')
            for g in r2['gaps']:
                print(f"    - {g['table']}.{g['column']} (enum {g['enum_size']} 值)")
        else:
            print('[5] 无缺口')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
