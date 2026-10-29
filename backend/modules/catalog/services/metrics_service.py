"""Metrics service - CRUD logic for metrics and dimensions."""

import json
import logging
import re
from datetime import datetime
from typing import Optional

from backend.common.db import DBConnection
from backend.modules.catalog.services import metric_formula as _metric_formula
from backend.modules.catalog.services import physical_ref as _physical_ref

from backend.modules.catalog.services.ontology_service import object_identity_key  # noqa: F401

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 前端表单字段 ↔ adh_metrics 真实列的映射(页面用 name/display_name/category/…,
# 语义层 mdl_compiler/run_semantic_query 读的是 name(中文名)/name_en/formula/agg_type)
#
# metric_type 不再映射到 category：category 的语义是**业务域**(可观测/客户/案例…)，
# 而「基础/衍生/复合」是指标口径标签，两者是正交维度，历史混写一列导致
# 分类下拉里业务域和口径标签混杂。metric_type 仅作旧客户端入参兼容(见 _legacy_kind_tags)，
# 口径标签的真实落位是 tags 列。
_FRONTEND_TO_DB = {
    "display_name": "name",
    "calculation_type": "agg_type",
    "expression": "formula",
    "unit": "unit",
    "description": "description",
    "target_table": "target_table",
    "target_column": "target_column",
    "is_active": "is_active",
    "category": "category",
    "bound_object_key": "bound_object_key",
    "formula_dsl": "formula_dsl",
}

_ALIAS_SELECT = (
    "id, name, name AS display_name, name_en, category, "
    "agg_type AS calculation_type, agg_type, formula AS expression, formula, "
    "unit, target_table, target_column, description, "
    "COALESCE(aliases, '[]') AS aliases, COALESCE(tags, '[]') AS tags, "
    "bound_object_key, COALESCE(certified, 0) AS certified, "
    "is_active, workspace_id, owner, created_at, updated_at"
)

# ── 字典筛选（列表过滤 + facet 词表）───────────────────────────────
# 哨兵值: 表示“空值桶”（未分类 / 未归属 / 未设置）。前端下拉里的“未分类”
# 这一项的 value 就是它，由 dict_facets 的聚合结果带出来，前端不硬编码值域。
# 为什么必须有 dict_facets: 指标列表是服务端分页的，用“当前页出现过的值”凑下拉
# 选项必然漏值、计数也不准；而写死一套固定枚举更糟 —— 历史缺陷就是把 category
# 写成 basic/derived/composite，库里真实值域却是 可观测/客户/案例…，
# 两项永远查不出结果，等于放了个“能点但永远空”的死筛选。
UNSET_FACET = "__unset__"

# 口径标签（tags 列）的旧表单口径 → 中文标签：历史上「基础/衍生/复合」被当作 category
# 写进库（值 basic/derived/composite），现统一以中文标签落 tags 列。
_METRIC_KIND_TAGS = {"basic": "基础", "derived": "衍生", "composite": "复合"}


def _legacy_kind_tags(val) -> list:
    """旧入参 metric_type（或库里存量的 category 脏值）→ 口径标签；非口径值原样回传。

    业务域值（可观测/客户…）不是标签，不得被塞进 tags —— 由调用方留在 category/丢弃。
    """
    raw = str(val or "").strip()
    return [_METRIC_KIND_TAGS[raw]] if raw in _METRIC_KIND_TAGS else ([] if not raw else [raw])


def _norm_bool_filter(name: str, val) -> Optional[int]:
    """'1'/'0'/True/False → 0/1；空 → None(不过滤)；其他值抛 ValueError(路由层转 400)。

    非法取值不得当成“未传”静默忽略 —— 那会把调用方的拼写错误伪装成正常结果。
    """
    if val is None or (isinstance(val, str) and val.strip() in ("", "all", "ALL")):
        return None
    s = str(val).strip().lower()
    if s in ("1", "true", "yes"):
        return 1
    if s in ("0", "false", "no"):
        return 0
    raise ValueError(f"{name} 仅支持 1/0（1=是, 0=否）")


def _apply_equality_filter(conditions: list, params: list, column: str, value) -> None:
    """字典文本列的等值过滤；UNSET_FACET 命中“空值桶”(NULL 或空串)。"""
    if not value:
        return
    if value == UNSET_FACET:
        conditions.append(f"COALESCE({column}, '') = ''")
        return
    conditions.append(f"`{column}` = %s")
    params.append(value)


def _apply_upper_filter(conditions: list, params: list, column: str, value) -> None:
    """大小写不敏感的值域过滤（agg_type 历史上混过大小写，库里存大写、表单传小写）。"""
    if not value:
        return
    if value == UNSET_FACET:
        conditions.append(f"COALESCE({column}, '') = ''")
        return
    conditions.append(f"UPPER(COALESCE({column}, '')) = %s")
    params.append(str(value).strip().upper())


def _norm_aliases(val):
    """别名入参归一: list/逗号串 → JSON 字符串; 空 → '[]'。列型为 JSON。"""
    if val is None:
        return "[]"
    if isinstance(val, str):
        items = [x.strip() for x in val.split(",") if x.strip()]
    elif isinstance(val, (list, tuple)):
        items = [str(x).strip() for x in val if str(x).strip()]
    else:
        items = []
    # 去重保序
    seen: list[str] = []
    for x in items:
        if x not in seen:
            seen.append(x)
    return json.dumps(seen, ensure_ascii=False)


def _norm_tags(val) -> str:
    """口径标签入参归一: list/逗号串 → JSON 数组字符串; 空 → '[]'。列型为 JSON。

    与 aliases 同一形状（标签是短字符串集，不存在别名那种顺位语义）。
    """
    return _norm_aliases(val)


def _json_list(val) -> list:
    """DB JSON 列/字符串 → 字符串清单（解不开给空清单，不抛）。"""
    if val is None:
        return []
    if isinstance(val, (list, tuple)):
        return [str(x).strip() for x in val if str(x).strip()]
    try:
        arr = json.loads(val) if isinstance(val, str) else val
    except (TypeError, ValueError):
        return []
    return [str(x).strip() for x in arr if str(x).strip()] if isinstance(arr, list) else []


def _isoformat(rows: list) -> list:
    for r in rows:
        for k in ("created_at", "updated_at"):
            if hasattr(r.get(k), "isoformat"):
                r[k] = r[k].isoformat()
    return rows


def _model_object_keys(model_id: int) -> list[str]:
    """取某本体模型当前生效的对象 key 集合(模型工作区作用域的锚点)。

    源自 adh_ontology_objects(激活/保存时由 _expand_objects 展开写入, 幂等)。
    """
    if not model_id:
        return []
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT object_key FROM adh_ontology_objects "
                "WHERE model_id = %s AND is_active = 1", (model_id,))
            return [r["object_key"] for r in cur.fetchall() if r.get("object_key")]


def _apply_model_scope(conditions: list, params: list,
                       model_id: Optional[int], scope: Optional[str]) -> bool:
    """把模型作用域追加到 WHERE 条件; 返回 False 表示必然空集(调用方可短路)。

    scope: 'model'=只看本模型对象挂接的资产(需 model_id); 'unbound'=只看待归属(未绑定);
    其他/空 = 不加过滤(全局字典视图旧行为不变)。MySQL ci 排序规则使 IN 天然大小写不敏感。
    """
    if scope == "unbound":
        conditions.append("(bound_object_key IS NULL OR bound_object_key = '')")
        return True
    if scope == "model" or model_id:
        keys = _model_object_keys(int(model_id or 0))
        if not keys:
            return False
        conditions.append(
            f"bound_object_key IN ({', '.join(['%s'] * len(keys))})")
        params.extend(keys)
        return True
    return True


def list_metrics(
    page: int = 1,
    size: int = 20,
    metric_type: Optional[str] = None,
    tags: Optional[str] = None,
    search: str = "",
    model_id: Optional[int] = None,
    scope: Optional[str] = None,
    category: Optional[str] = None,
    agg_type: Optional[str] = None,
    certified: Optional[str] = None,
    bound_object_key: Optional[str] = None,
    has_target_table: Optional[str] = None,
    is_active: Optional[str] = None,
) -> dict:
    """List metrics with pagination and filters.

    Args:
        page: Page number (1-based)
        size: Page size
        metric_type: 旧口径(同时匹配 category 或 agg_type)，保留兼容
        tags: 口径标签过滤(逗号分隔; 值域来自 dict_facets 的 tags 分桶)
        search: Search keyword(名称/英文名/描述/别名)
        model_id: 本体模型作用域(配合 scope='model'): 只列 bound_object_key ∈ 该模型对象集的指标
        scope: 'model' | 'unbound' | 空(全局, 旧行为)
        category: 业务域分类等值过滤(值域来自 dict_facets, UNSET_FACET=未分类)
        agg_type: 计算/聚合方式过滤(大小写不敏感)
        certified: '1'=仅已认证口径 / '0'=仅草稿口径
        bound_object_key: 归属本体对象过滤(UNSET_FACET=未归属)
        has_target_table: '1'=已派生物理表 / '0'=未派生(该指标无法被 planner 编译)
        is_active: '1'=仅启用口径 / '0'=仅已停用口径 / 空=全部(旧行为)

    Returns:
        dict with total and items
    """
    # 指标字典**不按 workspace 分域**（ontology-modeling §3：口径唯一登记在 adh_metrics，
    # 是跨工作空间/跨数据源的全局权威；与 list_all_dimensions、create_dimension 同口径）。
    # 曾按 workspace_id 精确等值过滤，导致个人工作站(ws=301) 下 61 条全局/默认空间字典行
    # 全部被排除，指标中心显示“暂无指标数据”而维度 Tab 正常 —— 两个 Tab 口径分叉的根因。
    # 模型作用域(scope=model)看的是该模型对象挂接的资产: 本体模型是全局语义资产
    # (adh_ontology_models 无 workspace_id 列), 其挂接指标多为全局字典(ws=0)。
    conditions = []
    params = []

    if metric_type:
        conditions.append("(category = %s OR agg_type = %s)")
        params.extend([metric_type, metric_type])
    if tags:
        # tags 是 JSON 数组列，用 JSON_CONTAINS 做元素命中；旧写法 FIND_IN_SET(..., category)
        # 查的是根本不存在的列（本参数以前一旦传入必 1054）。
        tag_list = [t.strip() for t in str(tags).split(",") if t.strip()]
        for tag in tag_list:
            conditions.append("JSON_CONTAINS(COALESCE(tags, '[]'), %s)")
            params.append(json.dumps(tag, ensure_ascii=False))
    if search:
        # 别名也是用户会搜的词(字典解析按 aliases 命中), 搜索口不覆盖别名
        # 会让“明明改了别名却搜不到”成为预期外差异。JSON 列 LIKE 在字典量级(千行)可接受。
        conditions.append(
            "(name LIKE %s OR name_en LIKE %s OR description LIKE %s OR COALESCE(aliases, '') LIKE %s)")
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%", f"%{search}%"])
    _apply_equality_filter(conditions, params, "category", category)
    _apply_upper_filter(conditions, params, "agg_type", agg_type)
    _apply_equality_filter(conditions, params, "bound_object_key", bound_object_key)
    cert = _norm_bool_filter("certified", certified)
    if cert is not None:
        conditions.append("COALESCE(certified, 0) = %s")
        params.append(cert)
    bound_tb = _norm_bool_filter("has_target_table", has_target_table)
    if bound_tb is not None:
        conditions.append("COALESCE(target_table, '') <> ''" if bound_tb
                          else "COALESCE(target_table, '') = ''")
    # 状态: 旧行为是不分状态全列(本体停用会级联置 is_active=0，那些行一直还在页面上)，
    # 不拿默认值改掉它 —— 否则存量口径会从字典页“凭空消失”；需要时由筛选显式限定。
    act = _norm_bool_filter("is_active", is_active)
    if act is not None:
        conditions.append("COALESCE(is_active, 0) = %s")
        params.append(act)
    if not _apply_model_scope(conditions, params, model_id, scope):
        return {"total": 0, "items": []}

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS total FROM adh_metrics {where}", params)
            total = cur.fetchone()["total"]

            offset = (page - 1) * size
            cur.execute(
                f"SELECT {_ALIAS_SELECT} "
                f"FROM adh_metrics {where} "
                f"ORDER BY name LIMIT %s OFFSET %s",
                params + [size, offset],
            )
            rows = cur.fetchall()
            _isoformat(rows)

    return {"total": total, "items": rows}


# 字典写入口统一派生 target_table：口径唯一实现见 services/metric_formula.py
# （与 ontology_service._metric_safe_target / scripts.backfill_dict_target_table 同源）：
# bound_object_key→对象主表，且仅当公式为合法单表聚合。
# 消除"get_metrics 按 bound_object_key 广告、planner 按 target_table 加载"的不一致。
_METRIC_AGG_HEAD = _metric_formula.AGG_HEAD
_METRIC_SELECT = _metric_formula.SELECT_TOKEN
_METRIC_WHERE = _metric_formula.WHERE_TOKEN
_METRIC_QUALIFIED = _metric_formula.QUALIFIED


def _object_binding(cur, bound_object_key: str, datasource_id: int = 0) -> tuple[str, int]:
    """按 bound_object_key 从部署绑定层取 `(物理表, 表所在数据源)`。

    物理表唯一命中才返回（歧义/无→''）；表所在数据源用于列元数据裁决——字典行
    `datasource_id` 常为 0（全局口径），拿它去查实存列永远为空，会把
    `session.id` 这类嵌套列误判为跨表引用（实际缺陷：指标 target_table 永远回不了值）。
    """
    if not bound_object_key:
        return "", 0
    try:
        if datasource_id:
            cur.execute(
                "SELECT DISTINCT physical_table, datasource_id FROM adh_ontology_bindings "
                "WHERE object_key=%s AND status='active' AND physical_table<>'' "
                "AND (datasource_id=%s OR datasource_id=0)", (bound_object_key, datasource_id))
        else:
            cur.execute(
                "SELECT DISTINCT physical_table, datasource_id FROM adh_ontology_bindings "
                "WHERE object_key=%s AND status='active' AND physical_table<>''",
                (bound_object_key,))
        rows = [r for r in (cur.fetchall() or []) if str(r.get("physical_table") or "").strip()]
        tabs = {str(r["physical_table"]).strip() for r in rows}
        if len(tabs) != 1:
            return "", 0
        tab = tabs.pop()
        ds = {int(r.get("datasource_id") or 0) for r in rows if int(r.get("datasource_id") or 0)}
        same_tab_ds = {int(r.get("datasource_id") or 0) for r in rows
                       if str(r["physical_table"]).strip() == tab and int(r.get("datasource_id") or 0)}
        return tab, (next(iter(same_tab_ds)) if same_tab_ds else (ds.pop() if ds else 0))
    except Exception as e:  # noqa: BLE001 — 解析不到不阻断写入，回落空 target_table
        logger.debug("[metrics] bound_object_key→绑定解析失败 obj=%s: %s", bound_object_key, e)
        return "", 0


def _object_primary_table(cur, bound_object_key: str, datasource_id: int = 0) -> str:
    """按 bound_object_key(+数据源) 从部署绑定层取对象主物理表；唯一命中才返回，歧义/无→''。"""
    if not bound_object_key:
        return ""
    try:
        if datasource_id:
            cur.execute(
                "SELECT DISTINCT physical_table FROM adh_ontology_bindings "
                "WHERE object_key=%s AND status='active' AND physical_table<>'' "
                "AND (datasource_id=%s OR datasource_id=0)", (bound_object_key, datasource_id))
        else:
            cur.execute(
                "SELECT DISTINCT physical_table FROM adh_ontology_bindings "
                "WHERE object_key=%s AND status='active' AND physical_table<>''",
                (bound_object_key,))
        tabs = {str(r["physical_table"]).strip() for r in (cur.fetchall() or [])
                if r.get("physical_table")}
        tabs.discard("")
        return tabs.pop() if len(tabs) == 1 else ""
    except Exception as e:  # noqa: BLE001 — 解析不到不阻断写入，回落空 target_table
        logger.debug("[metrics] bound_object_key→primary_table 解析失败 obj=%s: %s", bound_object_key, e)
        return ""


def _known_columns(cur, datasource_id: int, table: str) -> set:
    """表实存列名（带点列身份裁决用，复用调用方游标）。拿不到→空集（保守口径）。"""
    if not table:
        return set()
    try:
        cur.execute(
            "SELECT column_name FROM adh_column_metadata "
            "WHERE table_name = %s AND is_active = 1 "
            "AND (datasource_id = %s OR datasource_id = 0)", (table, int(datasource_id or 0)))
        rows = cur.fetchall() or []
        return {str(r.get("column_name")) for r in rows if r.get("column_name")}
    except Exception as e:  # noqa: BLE001 — 列元数据不可用不阻断写入，回到保守口径
        logger.debug("[metrics] 读 %s 列元数据失败(按保守口径判定): %s", table, e)
        return set()


def _derive_target_table(cur, bound_object_key, formula, datasource_id: int = 0) -> str:
    """合法单表聚合且能唯一定位对象主表时返回 target_table，否则 ''（宁缺勿错，不猜）。

    带点 token 以列元数据实存为裁决（SLS/ARMS 列名本体含点，不等于跨表限定）；
    列元数据按**表所在数据源**查（字典行的 datasource_id=0 不是表的归属源）。
    """
    table, table_ds = _object_binding(cur, bound_object_key, datasource_id)
    if not table:
        return ""
    return _metric_formula.metric_safe_target(
        formula, table, _known_columns(cur, table_ds or datasource_id, table))


def _object_binding_by_key(object_key: str, identity_key: str = "") -> tuple[str, int]:
    """从部署绑定层取对象 `(物理表, 数据源)`（新连接，供无游标调用方使用）。

    本体 doc 不携带物理绑定（v0.5 B6b），因此属性名→物理列的解析必须回绑定层拿表名。
    """
    from backend.common.db.metadata_db import get_metadata_conn
    try:
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                tab, ds = _object_binding(cur, object_key)
                if tab or not identity_key:
                    return tab, ds
                # 对象 key 大小写/分隔符变体（与 object_identity_key 同口径）再试一次
                cur.execute(
                    "SELECT DISTINCT object_key, physical_table, datasource_id "
                    "FROM adh_ontology_bindings "
                    "WHERE status='active' AND physical_table<>''")
                for r in cur.fetchall() or []:
                    if object_identity_key(r.get("object_key") or "") == identity_key:
                        return _object_binding(cur, str(r.get("object_key") or ""))
                return "", 0
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — 拿不到绑定不阻断，回到未验证口径
        logger.debug("[metrics] 对象 %s 绑定解析失败: %s", object_key, e)
        return "", 0


def resolve_object_property(object_key: str, property_name: str) -> dict:
    """按对象属性的**业务名**解析物理列（维度写入用）。

    物理列不得由 LLM 手敲（护栏 §7 + 拍板“人只审业务层，物理映射由工具对着元数据生成/校验”），
    事实源是本体 doc 的 `objects[].properties[] = {name(业务名), column(物理列引用)}`；
    column 可能是「表.列」（MySQL 常见），也可能是**列名本体含点**（SLS/ARMS 嵌套字段
    `view.url`），两者不得靠正则区分，以对象绑定表的实存列为唯一裁决。

    Returns:
        {"table", "column", "matched"} —— 命中时 matched=True；`verified` 表示
        该列已对着绑定表实存列核验（元数据缺失时为 False，不假装已验证）；
        未命中时 matched=False 且带 `available`（该对象可用属性业务名，供回抛候选），
        若名字命中但登记的物理列在绑定表上不存在则额外带 `reason`。
        宁缺勿错：名字对不上就回抛候选，绝不模糊绑一个列。
    """
    from backend.modules.catalog.services.ontology_service import object_identity_key

    want = str(property_name or "").strip()
    obj_key = str(object_key or "").strip()
    out = {"table": "", "column": "", "matched": False, "available": [], "verified": False}
    if not obj_key or not want:
        return out
    ik = object_identity_key(obj_key)
    for doc in _iter_active_docs():
        for o in doc.get("objects") or []:
            if object_identity_key(o.get("key") or o.get("object_key") or "") != ik:
                continue
            props = o.get("properties") or []
            out["available"] = [str(p.get("name") or p.get("display_name") or "").strip()
                                for p in props if (p.get("name") or p.get("display_name"))]
            for p in props:
                names = [str(p.get("name") or "").strip(), str(p.get("display_name") or "").strip(),
                         *[str(a).strip() for a in (p.get("aliases") or [])]]
                if want in [n for n in names if n]:
                    col = str(p.get("column") or "").strip()
                    if not col:
                        break   # 命中了业务名但没有物理列 → 不猜，回抛
                    # 带点 column 不得一律 `rpartition(".")` 拆「表.列」（历史缺陷：SLS 嵌套列
                    # `view.url` 被拆成 table=view / column=url，写出来的维度行 target_table
                    # 是不存在的表 → planner 按 target_table=物理表加载不到 → 建了维度但
                    # 取数永远解析不出）。表/列身份按列元数据实存裁决（physical_ref 同源）。
                    pt, table_ds = _object_binding_by_key(obj_key, ik)
                    known = _physical_ref.columns_of_table(table_ds, pt) if pt else set()
                    table, column, verdict = _physical_ref.resolve_column_ref(col, pt, known)
                    if verdict == "bad":
                        logger.warning(
                            "[metrics] 对象 %s 属性 %r 的物理列 %r 在该表(%s)实存列中不存在，"
                            "不写入维度（建模回炉项）", obj_key, want, col, pt or "?")
                        out["reason"] = "property_column_not_found"
                        break
                    out.update({"table": table or pt, "column": column,
                                "matched": True, "verified": verdict == "ok"})
                    return out
            if out["available"]:
                return out          # 找到对象但未命中属性 → 带候选返回
    return out


def _iter_active_docs() -> list:
    """加载全部 active 模型的 canonical doc（模型数量级小，一次取完）。"""
    import json as _json

    from backend.common.db.metadata_db import get_metadata_conn
    docs = []
    try:
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT json_content FROM adh_ontology_models WHERE status = 'active'")
                for row in cur.fetchall() or []:
                    raw = row.get("json_content")
                    if not raw:
                        continue
                    try:
                        doc = _json.loads(raw) if isinstance(raw, str) else raw
                    except ValueError:
                        continue
                    if isinstance(doc, dict):
                        docs.append(doc)
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — 解析不到不阻断写入，由调用方回退
        logger.debug("[metrics] active 模型 doc 加载失败: %s", e)
    return docs


def create_metric(data: dict) -> dict:
    """Create a new metric.

    Args:
        data: Metric data dict

    Returns:
        dict with id and success
    """
    now = _now()

    # 前端 name 是英文标识(如 gmv), display_name 是业务名(如 交易总额);
    # 语义层按 name 取指标, 因此 name 存中文业务名, name_en 存标识。
    name_en = (data.get("name") or "").strip()
    biz_name = (data.get("display_name") or data.get("name") or "").strip()
    if not biz_name:
        raise ValueError("metric name is required")
    agg_type = (data.get("calculation_type") or data.get("agg_type") or "count").upper()
    formula = data.get("expression") or data.get("formula") or ""
    # 口径必须能在归属对象的物理表上成立，否则取数恒为 0（宁缺勿错，不静默落库）
    if data.get("bound_object_key"):
        _require_formula_matches_object(str(data["bound_object_key"]), formula)

    with DBConnection() as conn:
        with conn.cursor() as cur:
            # Check duplicate (同名中文名或英文标识) —— **全局唯一**，不按 workspace 分域：
            # 语义层按 name 解析指标（planner/get_metrics），同名跨空间多条会造成口径歧义；
            # 与 create_dimension 的全局查重口径保持一致。
            cur.execute(
                "SELECT id FROM adh_metrics WHERE name = %s OR name_en = %s LIMIT 1",
                (biz_name, name_en or biz_name),
            )
            if cur.fetchone():
                raise ValueError(f"Metric '{biz_name}' already exists")

            # 字典写入口的引用完整性: bound_object_key 必须指向当前生效对象(ontology-modeling §4),
            # 悬空创建即拒, 不等到激活阶段才暴露
            _require_object_keys(cur, [data.get("bound_object_key")])
            # target_table 未显式给出时，按 bound_object_key→对象主表派生（与生成路径同口径），
            # 使 get_metrics 广告的指标 planner 也能加载编译。
            target_table = (data.get("target_table") or "").strip() or _derive_target_table(
                cur, data.get("bound_object_key"), formula, data.get("datasource_id", 0))

            # adh_metrics.id 是 INT, 不能用时间戳主键
            cur.execute("SELECT COALESCE(MAX(id), 0) + 1 AS nid FROM adh_metrics")
            row_id = cur.fetchone()["nid"]
            # category 只存业务域；旧表单口径(basic/derived/composite)若从 metric_type
            # 流入则改归 tags 标签，不再污染域值域（也不能当域默认值）
            legacy_kind = _legacy_kind_tags(data.get("metric_type"))
            is_kind_label = data.get("metric_type") in _METRIC_KIND_TAGS or (
                len(legacy_kind) == 1 and legacy_kind[0] in ("基础", "衍生", "复合"))
            category = (data.get("category")
                        or (None if is_kind_label else data.get("metric_type"))
                        or "业务")
            kind_tags = _json_list(data.get("tags")) if data.get("tags") is not None else []
            kind_tags += [t for t in legacy_kind if t and t not in kind_tags]
            cur.execute(
                "INSERT INTO adh_metrics "
                "(id, workspace_id, name, name_en, formula, unit, agg_type, "
                "target_table, target_column, description, owner, category, datasource_id, "
                "is_active, created_at, updated_at, default_agg, bound_object_key, aliases, tags) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    row_id,
                    # 字典行是跨工作空间口径：workspace_id 恒为 0（与 datasource_id 同口径），
                    # 不再从调用方（UI 当前工作站 / AS-BOT 会话）继承，避免同一字典被按空间碎片化
                    0,
                    biz_name,
                    name_en,
                    formula,
                    data.get("unit", ""),
                    agg_type,
                    target_table,
                    data.get("target_column", ""),
                    data.get("description", ""),
                    data.get("owner", ""),
                    category,
                    data.get("datasource_id", 0),
                    data.get("is_active", 1),
                    now,
                    now,
                    agg_type,
                    data.get("bound_object_key") or None,
                    _norm_aliases(data.get("aliases")),
                    _norm_tags(kind_tags),
                ),
            )

    return {"id": row_id, "success": True}


def get_metric(metric_id: int) -> Optional[dict]:
    """Get metric detail with dimensions."""
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_ALIAS_SELECT} FROM adh_metrics WHERE id = %s",
                (metric_id,),
            )
            metric = cur.fetchone()
            if not metric:
                return None

            _isoformat([metric])

            # Get dimensions(关系表 JOIN 维度字典)
            metric["dimensions"] = _fetch_metric_dimensions(cur, metric_id)

    return metric


def _fetch_metric_dimensions(cur, metric_id: int) -> list:
    cur.execute(
        "SELECT md.id, md.metric_id, md.relation_type, md.description, "
        "d.id AS dimension_id, d.name, d.name AS display_name, d.name_en, "
        "d.target_table, d.target_column AS column_name, d.category AS data_type "
        "FROM adh_metric_dimensions md "
        "JOIN adh_dimensions d ON md.dimension_id = d.id "
        "WHERE md.metric_id = %s "
        "ORDER BY d.name",
        (metric_id,),
    )
    return cur.fetchall()


# SQL 关键字/常用函数：不算列名（抽出公式里的列引用时排除）
_SQL_NON_COLUMN = {
    "select", "from", "where", "group", "by", "order", "having", "and", "or", "not", "in", "on", "as",
    "count", "sum", "avg", "min", "max", "distinct", "case", "when", "then", "else", "end", "null",
    "is", "like", "between", "limit", "offset", "over", "partition", "interval", "day", "month", "year",
    "hour", "minute", "second", "date", "timestamp", "cast", "coalesce", "ifnull", "abs", "round", "now",
}


def _formula_columns(formula: str) -> set:
    """保守抽出 formula 里引用的列名（已限定在归属对象表上，故取裸列名即可）。"""
    import re
    text = re.sub(r"'[^']*'|\"[^\"]*\"", " ", str(formula or ""))   # 去字符串字面量
    cols = set()
    for m in re.finditer(r"([A-Za-z_][\w$]*)\s*\.\s*([A-Za-z_][\w$]*)", text):
        cols.add(m.group(2).lower())          # t.col → col
    for tok in re.findall(r"[A-Za-z_][\w$]*", text):
        if tok.lower() not in _SQL_NON_COLUMN:
            cols.add(tok.lower())
    return cols


def _object_property_columns(object_key: str) -> tuple[str, list[str]]:
    """归属对象的物理列名清单（来自 canonical doc 的 properties[].column）。"""
    from backend.modules.catalog.services.ontology_service import object_identity_key
    ik = object_identity_key(str(object_key or "").strip())
    cols: list[str] = []
    for doc in _iter_active_docs():
        for o in doc.get("objects") or []:
            if object_identity_key(o.get("key") or o.get("object_key") or "") != ik:
                continue
            for p in o.get("properties") or []:
                col = str(p.get("column") or "").split(".")[-1].strip()
                if col and col.lower() not in [c.lower() for c in cols]:
                    cols.append(col)
    return ik, cols


def _require_formula_matches_object(bound_object_key: str, formula: str) -> None:
    """指标口径必须能在归属对象的物理表上成立 —— 否则取数恒为 0（宁缺勿错）。

    历史缺陷：`案例侧集成记录数` 挂在 case 上，物理按案例表 COUNT，而它要数的是集成记录表的行，
    结果静默返回 0；`source_type` 根本不在案例表里也能落库。错数比无答案危害大。
    """
    want = _formula_columns(formula)
    if not want:
        return
    _ik, cols = _object_property_columns(bound_object_key)
    if not cols:
        raise ValueError(
            f"归属对象「{bound_object_key}」没有已登记的属性/物理列，无法校验指标口径。"
            f"请先在本体模型中给该对象补 properties[].column，或改挂到实体相符的对象上")
    have = {c.lower() for c in cols}
    bad = sorted(w for w in want if w not in have)
    if bad:
        raise ValueError(
            f"指标口径与归属对象不符：{', '.join(bad)} 不在对象「{bound_object_key}」的物理列里。"
            f"该对象可用列: {', '.join(cols[:30]) or '（无）'}。"
            f"若要统计的实体不在该对象上（例：集成记录数挂在案例对象上），请改挂实体相符的对象；"
            f"口径错了会静默返回 0，不能落库")


def _require_object_keys(cur, keys) -> None:
    """字典写入的 bound_object_key 引用校验: 非空 key 必须在当前生效对象集内。"""
    want = [str(k).strip() for k in keys if k and str(k).strip()]
    if not want:
        return
    ph = ", ".join(["%s"] * len(want))
    cur.execute(
        f"SELECT object_key FROM adh_ontology_objects "
        f"WHERE is_active = 1 AND object_key IN ({ph})", tuple(want))
    ok = {str(r["object_key"]).lower() for r in cur.fetchall()}
    bad = [k for k in want if k.lower() not in ok]
    if not bad:
        return
    # 区分两种失败，别把「未展开」误报成「未创建」（历史缺陷：业务本体 doc 里明明有对象，
    # 但 adh_ontology_objects 无展开行，提示却叫用户“去创建对象”，照做只会卡住）
    cur.execute(
        "SELECT m.id, m.name FROM adh_ontology_models m "
        "WHERE m.status = 'active' AND m.object_count > 0 "
        "AND NOT EXISTS (SELECT 1 FROM adh_ontology_objects o WHERE o.model_id = m.id)")
    unexpanded = [f"{r['name']}" for r in cur.fetchall()]
    if unexpanded:
        raise ValueError(
            f"归属对象不可挂靠: {', '.join(bad)}。"
            f"这些对象存在于模型定义里但尚未展开到生效对象集 —— 模型「{'、'.join(unexpanded)}」"
            f"有对象定义却无展开行（级联未跑）。请在本体建模页重新激活该模型以触发展开，"
            f"而非新建对象")
    raise ValueError(f"归属对象不存在或已失效: {', '.join(bad)}（请先在本体模型中创建并激活对象）")


def update_metric(metric_id: int, data: dict) -> bool:
    """Update a metric.

    Args:
        metric_id: Metric ID
        data: Fields to update

    Returns:
        True if updated, False if not found
    """
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM adh_metrics WHERE id = %s", (metric_id,))
            if not cur.fetchone():
                return False
            if data.get("bound_object_key"):
                _require_object_keys(cur, [data.get("bound_object_key")])

            fields = []
            params = []
            # 前端键 → 真实列映射
            mapped: dict = {}
            for fe_key, db_key in _FRONTEND_TO_DB.items():
                if fe_key in data:
                    mapped[db_key] = data[fe_key]
            if "name" in data and "display_name" not in data and "name" not in mapped:
                # 前端 name 是标识 → 写 name_en, 避免误覆盖中文业务名
                mapped["name_en"] = data["name"]
            if "calculation_type" in data:
                mapped["agg_type"] = (data["calculation_type"] or "").upper()
            # 口径公式的两种写法都必须被接受：expression(前端键) 与 formula(真实列名)，
            # 与 create_metric 同口径（expression 优先、空值回退 formula）。
            # 历史缺陷：_FRONTEND_TO_DB 只登记 expression，直接传 formula 的调用
            # （迁移脚本 / AS-BOT save_metric / 前端把 GET 回来的整行原样 PUT）被静默丢弃 ——
            # 改了没生效也不报错，比报错更坏（no-silent-degradation）。
            if "expression" in data or "formula" in data:
                mapped["formula"] = data.get("expression") or data.get("formula") or ""
            if "is_active" in mapped:
                # 状态列归一为 0/1：停用 = 不参与语义检索与取数（与 AS-BOT 未发布同义）。
                # 空值/非法值显式拒，不得默默当 0 把口径下线（也不得当“未传”让调用方以为改了）。
                flag = _norm_bool_filter("is_active", mapped["is_active"])
                if flag is None:
                    raise ValueError("is_active 仅支持 1/0（1=启用, 0=停用）")
                mapped["is_active"] = flag
            for key in ("name", "name_en", "formula", "agg_type", "unit", "description",
                        "category", "target_table", "target_column", "bound_object_key",
                        "formula_dsl", "is_active", "default_agg", "certified", "owner"):
                if key in mapped:
                    fields.append(f"{key} = %s")
                    params.append(mapped[key])
            if "metric_type" in data and "category" not in mapped:
                # 旧客户端只发 metric_type：不得再把口径标签写进 category（业务域）列；
                # basic/derived/composite → tags，其余值按旧语义落 category（存量调用方兼容）。
                legacy = _legacy_kind_tags(data.get("metric_type"))
                if legacy and legacy[0] in ("基础", "衍生", "复合"):
                    fields.append("tags = %s")
                    params.append(_norm_tags(legacy))
                else:
                    fields.append("category = %s")
                    params.append(data.get("metric_type"))
            if "aliases" in data:
                fields.append("aliases = %s")
                params.append(_norm_aliases(data["aliases"]))
            if "tags" in data:
                fields.append("tags = %s")
                params.append(_norm_tags(data["tags"]))

            # bound_object_key 在更新且未显式改 target_table 时，按对象主表派生补齐（同 create 口径）
            if data.get("bound_object_key") and "target_table" not in mapped:
                _f = data.get("expression") or data.get("formula") or ""
                _tt = _derive_target_table(
                    cur, data.get("bound_object_key"), _f,
                    data.get("datasource_id", 0)) if _f else ""
                if _tt:
                    fields.append("target_table = %s")
                    params.append(_tt)

            if not fields:
                return True

            fields.append("updated_at = %s")
            params.append(_now())
            params.append(metric_id)

            cur.execute(f"UPDATE adh_metrics SET {', '.join(fields)} WHERE id = %s", params)

    return True


def delete_metric(metric_id: int) -> bool:
    """Delete a metric and its dimensions.

    Args:
        metric_id: Metric ID

    Returns:
        True if deleted, False if not found
    """
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM adh_metrics WHERE id = %s", (metric_id,))
            if not cur.fetchone():
                return False

            cur.execute("DELETE FROM adh_metric_dimensions WHERE metric_id = %s", (metric_id,))
            cur.execute("DELETE FROM adh_metrics WHERE id = %s", (metric_id,))

    return True


def get_dimensions(metric_id: int) -> list:
    """Get dimensions for a metric (relation table JOIN adh_dimensions)."""
    with DBConnection() as conn:
        with conn.cursor() as cur:
            return _fetch_metric_dimensions(cur, metric_id)


def add_dimension(metric_id: int, data: dict) -> dict:
    """Link a dimension to a metric.

    维度字典在 adh_dimensions, 本表只是 metric↔dimension 关系;
    传入的维度名在字典中不存在时自动创建字典行。
    """
    now = _now()

    dim_name = (data.get("display_name") or data.get("name") or "").strip()
    dim_en = (data.get("name") or "").strip() if data.get("display_name") else ""
    if not dim_name:
        raise ValueError("dimension name is required")

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, target_table FROM adh_metrics WHERE id = %s", (metric_id,))
            metric_row = cur.fetchone()
            if not metric_row:
                raise ValueError(f"Metric {metric_id} not found")

            # 在维度字典中查找(中文名或英文标识命中即可)
            cur.execute(
                "SELECT id FROM adh_dimensions WHERE name = %s OR (name_en <> '' AND name_en = %s) "
                "ORDER BY id LIMIT 1",
                (dim_name, dim_en or dim_name),
            )
            hit = cur.fetchone()
            if hit:
                dimension_id = hit["id"]
            else:
                # adh_dimensions.id 是 INT, 不能用时间戳主键
                cur.execute("SELECT COALESCE(MAX(id), 0) + 1 AS nid FROM adh_dimensions")
                dimension_id = cur.fetchone()["nid"]
                cur.execute(
                    "INSERT INTO adh_dimensions "
                    "(id, name, name_en, level, target_table, target_column, description, "
                    "category, datasource_id, is_active, created_at, updated_at) "
                    "VALUES (%s, %s, %s, 0, %s, %s, %s, %s, 0, 1, %s, %s)",
                    (
                        dimension_id,
                        dim_name,
                        dim_en,
                        data.get("target_table") or metric_row["target_table"] or "",
                        data.get("column_name") or data.get("target_column") or "",
                        data.get("description", ""),
                        data.get("category") or "属性",
                        now,
                        now,
                    ),
                )

            # Check duplicate link
            cur.execute(
                "SELECT id FROM adh_metric_dimensions WHERE metric_id = %s AND dimension_id = %s",
                (metric_id, dimension_id),
            )
            if cur.fetchone():
                raise ValueError(f"Dimension '{dim_name}' already linked to this metric")

            # adh_metric_dimensions.id 是 INT
            cur.execute("SELECT COALESCE(MAX(id), 0) + 1 AS nid FROM adh_metric_dimensions")
            row_id = cur.fetchone()["nid"]
            cur.execute(
                "INSERT INTO adh_metric_dimensions "
                "(id, metric_id, dimension_id, relation_type, description, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    row_id,
                    metric_id,
                    dimension_id,
                    data.get("relation_type") or "GROUP_BY",
                    data.get("description", ""),
                    now,
                ),
            )

    return {"id": row_id, "dimension_id": dimension_id, "success": True}


# ── 全局维度字典(adh_dimensions) — 指标中心唯一人工编辑入口 ──────────────
# run_semantic_query 解析维度名/别名/枚举标签的权威来源。与本体回写链路
# (sync_enums_to_dimensions)协同: aliases/value_labels 人工值优先, 本体激活只补空白不覆盖。
# name / datasource_id 不开放——是字典键与血缘锚点。
# is_active 开放为**状态维护入口**：AS-BOT 未发布的维度以 is_active=0 落库，
# 若字典层不给恢复口，这些行永远只能靠改库恢复（人工页面上的死数据）。

_DIM_SELECT = (
    "id, COALESCE(name, '') AS name, COALESCE(name_en, '') AS name_en, "
    "COALESCE(category, '') AS category, COALESCE(level, 0) AS level, "
    "COALESCE(hierarchy, '') AS hierarchy, COALESCE(target_table, '') AS target_table, "
    "COALESCE(target_column, '') AS target_column, "
    "COALESCE(bound_object_key, '') AS bound_object_key, "
    "aliases, value_labels, COALESCE(certified, 0) AS certified, "
    "COALESCE(is_active, 1) AS is_active, "
    "COALESCE(datasource_id, 0) AS datasource_id, COALESCE(description, '') AS description"
)

# 白名单可编辑列(字典键/血缘锚点不在此列表, 一律拒绝写入; bound_object_key 为归属引用, 开放)
# target_table/target_column 例外开放：它们既是「血缘锚点」，也正是**建模字段错配的唯一病灶**
# （历史缺陷：白名单不含它们 → AS-BOT save_dimension 解析出的物理列与前端表单填写值被静默丢弃，
# 改了等于没改，错映射永远修不掉）。开放的前提是写入前按列元数据实存裁决（见
# _normalize_dim_physical_binding）：错列显式拒绝，绝不静默落库。
_DIM_EDITABLE = [
    "name_en", "category", "level", "hierarchy",
    "aliases", "value_labels", "description", "certified", "bound_object_key",
    "is_active", "target_table", "target_column",
]


def list_all_dimensions(model_id: Optional[int] = None,
                        scope: Optional[str] = None,
                        is_active: Optional[str] = None) -> dict:
    """全局维度字典只读列表(供指标中心「维度字典」Tab 与模型工作区复用)。

    model_id/scope 语义同 list_metrics: scope='model' 只看本模型对象挂接维度,
    'unbound' 看待归属, 缺省保持旧行为(全量)。

    is_active 三态（与“默认只给生效行”的旧消费方兼容）:
      None(未传) → 只列 is_active=1（Chat 上下文/看板参数枚举等取数向消费方不得看到停用行）;
      'all'      → 全部（字典维护页，停用行也要可见可恢复）;
      '1'/'0'    → 只看该状态。非法值抛 ValueError(路由层转 400)，不当成“未传”。

    为何本面不提供 category/certified 等过滤入参(而指标列表提供):
    维度列表**不分页**(字典量级，一次全量返回)，筛选在前端于全量集上做是精确的且即时;
    指标列表服务端分页(size=20)，过滤必须下推到 SQL，否则“先分页再过滤”会漏行。
    两个 Tab 的筛选口径差异源于分页形状，不是两套规则；选项词表统一走 dict_facets。
    """
    conditions: list = []
    params: list = []
    if is_active is None:
        conditions.append("is_active = 1")
    else:
        flag = _norm_bool_filter("is_active", is_active)  # 'all'/'' → None = 不按状态过滤
        if flag is not None:
            conditions.append("COALESCE(is_active, 1) = %s")
            params.append(flag)
    if not _apply_model_scope(conditions, params, model_id, scope):
        return {"total": 0, "items": []}
    # 条件可能为空(all + 全局视图) —— 裸 WHERE 是非法 SQL, 与 list_metrics 同口径守卫
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_DIM_SELECT} FROM adh_dimensions {where} "
                f"ORDER BY category, name", params)
            rows = cur.fetchall()
    return {"total": len(rows), "items": rows}


# ── 字典筛选词表(facets) ──────────────────────────────────────

def _facet_buckets(rows: list) -> list:
    """GROUP BY 结果 → [{value, count}]，空值归入 UNSET_FACET 桶(前端渲染为“未分类/未设置”)。"""
    merged: dict = {}
    for r in rows or []:
        raw = r.get("value")
        val = str(raw).strip() if raw not in (None, "") else ""
        key = val or UNSET_FACET
        merged[key] = merged.get(key, 0) + int(r.get("count") or 0)
    out = [{"value": k, "count": v} for k, v in merged.items()]
    out.sort(key=lambda x: (-x["count"], x["value"]))
    return out


def dict_facets(model_id: Optional[int] = None,
                scope: Optional[str] = None) -> dict:
    """指标/维度字典的筛选词表 + 覆盖计数(只读聚合，供筛选下拉直接消费)。

    返回:
      {"metrics": {total, active, inactive, certified, draft, unbound_objects,
                   with_target_table, without_target_table, with_target_column,
                   categories[], agg_types[], tags[]},
       "dimensions": {total, active, inactive, certified, draft, unbound_objects,
                      with_target_table, with_target_column, with_value_labels,
                      without_value_labels, categories[]},
       "objects": [{key, display_name, metric_count, dimension_count}]}

    设计约束(见 UNSET_FACET 注释): 选项必须来自全量集聚合，不得拿分页行凑，
    也不得前端写死枚举；字典是全局口径(不按 workspace 分域)，但支持模型作用域。

    计数口径与对应列表接口的**缺省**一致(否则下拉里的数字与结果总数对不上, 又成一个骗人的筛选):
    指标列表不分状态(list_metrics 旧行为)，facet 也不分、但额外报 active/inactive；
    维度列表缺省只走 is_active=1，facet 同口径 —— 字典页显式传 is_active='all' 拿全量后
    在本地算启用/停用计数(见 DimensionsDictTab.counts)，不消费本处的 dimensions.active/inactive。
    """
    out: dict = {"metrics": {}, "dimensions": {}, "objects": []}
    counts: dict = {}   # object_key → {"metrics": n, "dimensions": n}
    with DBConnection() as conn:
        with conn.cursor() as cur:
            for table, key in (("adh_metrics", "metrics"), ("adh_dimensions", "dimensions")):
                conds: list = ["is_active = 1"] if table == "adh_dimensions" else []
                params: list = []
                if not _apply_model_scope(conds, params, model_id, scope):
                    out[key] = {"total": 0, "certified": 0, "draft": 0, "unbound_objects": 0,
                                "categories": [], "agg_types": [], "tags": []}
                    continue
                where = ("WHERE " + " AND ".join(conds)) if conds else ""
                # 列清单按表拼：value_labels 只在 adh_dimensions 有，拿到指标表上会 1054
                cols = [
                    "COUNT(*) AS total",
                    "SUM(CASE WHEN COALESCE(is_active, 0) = 1 THEN 1 ELSE 0 END) AS active_n",
                    "SUM(CASE WHEN COALESCE(certified, 0) = 1 THEN 1 ELSE 0 END) AS certified",
                    "SUM(CASE WHEN COALESCE(bound_object_key, '') = '' THEN 1 ELSE 0 END) AS unbound",
                    "SUM(CASE WHEN COALESCE(target_table, '') <> '' THEN 1 ELSE 0 END) AS with_table",
                    "SUM(CASE WHEN COALESCE(target_column, '') <> '' THEN 1 ELSE 0 END) AS with_column",
                ]
                if table == "adh_dimensions":
                    cols.append(
                        "SUM(CASE WHEN JSON_LENGTH(value_labels) > 0 THEN 1 ELSE 0 END) AS with_labels")
                cur.execute(f"SELECT {', '.join(cols)} FROM {table} {where}", params)
                s = cur.fetchone() or {}
                total = int(s.get("total") or 0)
                cert = int(s.get("certified") or 0)
                summary: dict = {
                    "total": total, "certified": cert, "draft": total - cert,
                    "active": int(s.get("active_n") or 0),
                    "inactive": total - int(s.get("active_n") or 0),
                    "unbound_objects": int(s.get("unbound") or 0),
                    "with_target_table": int(s.get("with_table") or 0),
                    "without_target_table": total - int(s.get("with_table") or 0),
                    "with_target_column": int(s.get("with_column") or 0),
                }
                if table == "adh_dimensions":
                    n_labels = int(s.get("with_labels") or 0)
                    summary["with_value_labels"] = n_labels
                    summary["without_value_labels"] = total - n_labels
                cur.execute(
                    f"SELECT COALESCE(category, '') AS value, COUNT(*) AS count "
                    f"FROM {table} {where} GROUP BY value", params)
                summary["categories"] = _facet_buckets(cur.fetchall())
                if table == "adh_metrics":
                    cur.execute(
                        f"SELECT UPPER(COALESCE(agg_type, '')) AS value, COUNT(*) AS count "
                        f"FROM {table} {where} GROUP BY value", params)
                    summary["agg_types"] = _facet_buckets(cur.fetchall())
                    # 口径标签是 JSON 数组列（一行多标签），GROUP BY 拆不了数组元素；
                    # 字典量级（千行）直接拉标签列在 Python 侧计数，不依赖 JSON_TABLE 方言。
                    try:
                        cur.execute(
                            f"SELECT COALESCE(tags, '[]') AS tags FROM {table} {where}", params)
                        tag_rows = cur.fetchall() or []
                    except Exception as e:  # noqa: BLE001 — tags 列迁移未执行时词表为空(可诊断), 不阻断整个 facet
                        logger.warning("[dict_facets] tags 分桶跳过(迁移未执行?): %s", e)
                        tag_rows = []
                    tag_counts: dict = {}
                    for row in tag_rows:
                        for t in _json_list(row.get("tags")):
                            tag_counts[t] = tag_counts.get(t, 0) + 1
                    summary["tags"] = sorted(
                        ({"value": k, "count": v} for k, v in tag_counts.items()),
                        key=lambda x: (-x["count"], x["value"]))
                cur.execute(
                    f"SELECT COALESCE(bound_object_key, '') AS value, COUNT(*) AS count "
                    f"FROM {table} {where} GROUP BY value", params)
                for row in cur.fetchall() or []:
                    raw = row.get("value")
                    k = str(raw).strip() if raw not in (None, "") else ""
                    if not k:
                        continue   # 未归属桶不是一种对象选项，由 unbound_objects 计数承载
                    counts.setdefault(k, {"metrics": 0, "dimensions": 0})
                    counts[k]["metrics" if table == "adh_metrics" else "dimensions"] += int(
                        row.get("count") or 0)
                out[key] = summary

            # 对象业务名(ui-resource-display: 显示用 name，key 只作次要信息)。
            # 不按 is_active 过滤取名字: 本体重建会短窗口内清空/重写对象行，拿它做
            # “已失效”判定会隔次刷新飘标；名字取不到就回退 key(本身是可读语义名)。
            cur.execute(
                "SELECT object_key, "
                "       COALESCE(MAX(CASE WHEN is_active = 1 THEN NULLIF(display_name, '') END), "
                "                        NULLIF(MAX(NULLIF(display_name, '')), '')) AS display_name "
                "FROM adh_ontology_objects WHERE object_key <> '' GROUP BY object_key")
            names = {str(r["object_key"]): (r.get("display_name") or "")
                     for r in cur.fetchall() or [] if r.get("object_key")}

            # 排序: 挂接资产多的在前(下拉最常选项靠顶部)，同量按 key 字典序保证可预期
            def _weight(k: str) -> int:
                c = counts.get(k) or {}
                return -(int(c.get("metrics") or 0) + int(c.get("dimensions") or 0))
            out["objects"] = [
                {"key": k,
                 "display_name": names.get(k) or "",
                 "metric_count": (counts.get(k) or {}).get("metrics", 0),
                 "dimension_count": (counts.get(k) or {}).get("dimensions", 0)}
                for k in sorted(set(names) | set(counts), key=lambda k: (_weight(k), k))
            ]
    return out


def create_dimension(data: dict) -> dict:
    """独立创建维度字典行（与 create_metric 对称，供 AS-BOT 工具与前端复用）。

    data 字段：
      name / display_name → 业务名（必填）; name_en → 英文标识;
      target_table / target_column → 物理列; category → 分类（"时间"/"属性"/...）;
      description / bound_object_key / aliases / value_labels / certified / datasource_id
    bound_object_key 非空时引用校验（同 create_metric）。
    字典行不按 workspace 分域：workspace_id 入参不参与写入（与 create_metric 同口径）。
    """
    now = _now()
    biz_name = (data.get("display_name") or data.get("name") or "").strip()
    if not biz_name:
        raise ValueError("dimension name is required")
    name_en = (data.get("name_en") or "").strip()

    with DBConnection() as conn:
        with conn.cursor() as cur:
            # 查重（同中文名或同英文标识）
            cur.execute(
                "SELECT id FROM adh_dimensions WHERE (name = %s OR (name_en <> '' AND name_en = %s)) "
                "LIMIT 1",
                (biz_name, name_en or biz_name))
            if cur.fetchone():
                raise ValueError(f"Dimension '{biz_name}' already exists")
            _require_object_keys(cur, [data.get("bound_object_key")])

            cur.execute("SELECT COALESCE(MAX(id), 0) + 1 AS nid FROM adh_dimensions")
            row_id = cur.fetchone()["nid"]
            cur.execute(
                "INSERT INTO adh_dimensions "
                "(id, name, name_en, level, target_table, target_column, description, "
                "category, datasource_id, is_active, created_at, updated_at, "
                "bound_object_key, aliases, value_labels, certified) "
                "VALUES (%s, %s, %s, 0, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    row_id,
                    biz_name,
                    name_en,
                    data.get("target_table", ""),
                    data.get("target_column", ""),
                    data.get("description", ""),
                    data.get("category") or "属性",
                    data.get("datasource_id", 0),
                    # 原本硬编码 1：新建维度会立即进入语义检索/查询。现按入参取值，
                    # AS-BOT 默认传 0（未发布不生效），发布才置 1。
                    1 if int(data.get("is_active", 1) or 0) else 0,
                    now,
                    now,
                    data.get("bound_object_key") or None,
                    _norm_aliases(data.get("aliases")),
                    json.dumps(data["value_labels"], ensure_ascii=False) if data.get("value_labels") else None,
                    1 if data.get("certified") else 0,
                ),
            )
    return {"id": row_id, "success": True}


def _normalize_dim_physical_binding(row: dict, payload: dict) -> None:
    """维度物理锚点（target_table/target_column）写入前的实存裁决与归一。

    放开白名单不等于放弃治理：错列一旦落库，planner 按 target_table 能加载到该维度、
    却生成引用不存在列的 SQL（错数比无答案危害大）。因此：
    - `bad`（该表实存列里没有此列）→ `ValueError` 显式拒绝并给可诊断原因（宁阻断勿落错映射）；
    - `unverified`（目标源列元数据未同步，如跳站部署源）→ 放行并保留完整写法（不丢路径段）；
    - `ok` → 按裁决归一（「表.列」写法剥前缀、嵌套列名保留完整名）。
    """
    if "target_column" not in payload and "target_table" not in payload:
        return
    col = str(payload.get("target_column") if "target_column" in payload
              else (row.get("target_column") or "")).strip()
    table = str(payload.get("target_table") if "target_table" in payload
                else (row.get("target_table") or "")).strip()
    if not col:
        # 显式清空物理列 = 该维度退出可编译集（与 is_active=停用同义），两处一起清避免半截态
        if "target_column" in payload:
            payload["target_table"] = ""
            payload["target_column"] = ""
        return
    obj_key = str(payload.get("bound_object_key") or row.get("bound_object_key") or "").strip()
    btable, bds = _object_binding_by_key(obj_key) if obj_key else ("", 0)
    table = table or btable
    if not table:
        raise ValueError(
            f"维度物理列「{col}」没有可登记的物理表（target_table 为空时语义层无法编译取数）："
            "请同时填写物理表，或先给该维度指定归属对象（由部署绑定层提供表）")
    ds = int(bds or row.get("datasource_id") or 0)
    known = _physical_ref.columns_of_table(ds, table)
    ok_table, ok_col, verdict = _physical_ref.resolve_column_ref(col, table, known)
    if verdict == "bad":
        raise ValueError(
            f"物理列「{col}」在表「{table}」的实存列中不存在（建模字段错配），已拒绝写入维度。"
            "请核对列名（日志服务类数据源的嵌套字段是列名本体含点，如 view.url，"
            "不是「表.列」两段），或先同步该数据源的列元数据后重试")
    payload["target_table"] = ok_table or table
    payload["target_column"] = ok_col or col


def update_dimension(dim_id: int, data: dict) -> bool:
    """编辑全局维度字典白名单列。

    aliases: list[str]; value_labels: dict[str,str] — 以 JSON 列存储。
    仅更新传入的白名单字段; 非白名单字段(name/datasource_id)忽略。
    bound_object_key 开放: 模型工作区"归属到对象/资产池归属动作"的写入口(对象名是引用非锚点)。
    target_table/target_column 开放: 建模字段错配的修复通道，写入前按列元数据实存裁决
    （见 _normalize_dim_physical_binding：错列拒绝，元数据缺失保留完整写法不静默错拆）。
    is_active 开放: 启用/停用（停用 = 不参与语义检索与取数，与 AS-BOT 未发布同义），
    入参归一为 0/1 整数（布尔/'1'/'0' 均可），不得把 True/'1' 直写进 TINYINT 列。
    """
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, target_table, target_column, bound_object_key, "
                "COALESCE(datasource_id, 0) AS datasource_id FROM adh_dimensions WHERE id = %s",
                (dim_id,))
            row = cur.fetchone()
            if not row:
                return False
            payload = dict(data)
            _normalize_dim_physical_binding(row, payload)
            data = payload

            fields = []
            params = []
            for key in _DIM_EDITABLE:
                if key not in data:
                    continue
                val = data[key]
                if key in ("aliases", "value_labels"):
                    if val in (None, "", [], {}):
                        val = None
                    else:
                        val = json.dumps(val, ensure_ascii=False)
                elif key == "certified":
                    val = 1 if val else 0
                elif key == "is_active":
                    # 空值不得被当成“停用”（那会把一个漏传字段的调用变成线上口径失效）
                    flag = _norm_bool_filter(key, val)
                    if flag is None:
                        raise ValueError("is_active 仅支持 1/0（1=启用, 0=停用）")
                    val = flag
                elif key == "level":
                    val = int(val or 0)
                fields.append(f"{key} = %s")
                params.append(val)

            if not fields:
                return True

            params.append(dim_id)
            cur.execute(f"UPDATE adh_dimensions SET {', '.join(fields)} WHERE id = %s", params)

    return True


# ── 未归属资产池 (模型工作区缓冲区视图) ────────────────────────

def delete_dimension(dim_id: int) -> bool:
    """删除维度字典行及其指标关系行(与 delete_metric 同口径的物理删除)。

    字典行是引用方(bound_object_key 指向对象), 删除不产生悬空引用;
    图谱/知识库里的残留由各自下次重建对账。
    """
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM adh_dimensions WHERE id = %s", (dim_id,))
            if not cur.fetchone():
                return False

            cur.execute("DELETE FROM adh_metric_dimensions WHERE dimension_id = %s", (dim_id,))
            cur.execute("DELETE FROM adh_dimensions WHERE id = %s", (dim_id,))

    return True


def asset_pool() -> dict:
    """聚合所有"尚未归属/悬空"的建模资产, 供工作区左栏资产池与各页计数徽章。

    - metrics/dimensions: bound_object_key 为空(unbound) 或指向已不再生效对象的孤儿(orphan);
    - terms: 未绑定任何表的黑话(target_table 为空);
    - suggestions: 进化候选（adh_evolution_candidates pending：待收编别名/待建指标维度/待核绑定）。
    只读聚合, 治理动作(归属/收编/退回)由各自既有写入口完成。
    """
    out: dict = {"metrics": [], "dimensions": [], "terms": [], "suggestions": [], "counts": {}}
    orphan_cond = (
        "(COALESCE(bound_object_key, '') = '' OR bound_object_key NOT IN "
        "(SELECT object_key FROM adh_ontology_objects WHERE is_active = 1))")
    reason_case = ("CASE WHEN COALESCE(bound_object_key, '') = '' "
                   "THEN 'unbound' ELSE 'orphan' END AS pool_reason")
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_ALIAS_SELECT}, {reason_case} FROM adh_metrics "
                f"WHERE is_active = 1 AND {orphan_cond} ORDER BY name LIMIT 200")
            out["metrics"] = _isoformat(cur.fetchall())
            cur.execute(
                f"SELECT {_DIM_SELECT}, {reason_case} FROM adh_dimensions "
                f"WHERE is_active = 1 AND {orphan_cond} ORDER BY name LIMIT 200")
            out["dimensions"] = cur.fetchall()
            cur.execute(
                "SELECT id, datasource_id, term_cn, term_en, term_aliases, term_type, "
                "       target_table, target_column, description "
                "FROM adh_business_terms WHERE is_active = 1 "
                "  AND COALESCE(target_table, '') = '' ORDER BY term_cn LIMIT 200")
            out["terms"] = cur.fetchall()
    try:
        # 进化候选（已收口到单一事实源）：旧 adh_alias_suggestions 退役为冻结表，
        # 本面不再读它，避免两处真值源飘移。
        # 故意在本函数自己的连接里查，不委托 evolution_service：那里会另开一条
        # 元库连接，既绕开本函数的连接生命周期（调用方/测试只能 mock DBConnection），
        # 又把"表未迁移 → 容错为空"这条现有语义弄丢。
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, term, kind, source, datasource_name, object_hint, "
                    "       target_type, target_ref, classify_reason, evidence, "
                    "       hit_count, updated_at "
                    "FROM adh_evolution_candidates WHERE status = 'pending' "
                    "ORDER BY hit_count DESC, updated_at DESC LIMIT 100")
                rows = cur.fetchall() or []
                for r in rows:
                    ev = r.get("evidence")
                    if isinstance(ev, str):
                        try:
                            ev = json.loads(ev)
                        except (TypeError, ValueError):
                            ev = {}
                    r["evidence"] = ev if isinstance(ev, dict) else {}
                    # 候选表里带近似候选，旧前端字段名是 candidates，保持兼容
                    r["candidates"] = r["evidence"].get("candidates") or []
                out["suggestions"] = rows
    except Exception as e:  # noqa: BLE001 — 候选表未迁移属正常, 不报错
        logger.debug("[asset_pool] suggestions skipped: %s", e)
    out["counts"] = {k: len(out[k]) for k in ("metrics", "dimensions", "terms", "suggestions")}
    out["counts"]["total"] = sum(out["counts"].values())
    return out


# ── 口径认证（P4）：区分「可信口径」与「草稿口径」 ─────────────────
# 为什么要认证：指标/维度是给业务看的权威口径，未经人工确认的 formula 可能是
# LLM 归纳或历史遗留，直接当权威答案会误导决策。认证 = 有人对其口径负责。
# 纪律：认证只是**标记权威性**，不改变解析/查询行为（否则 0 认证时知识库会空，
# 反而破坏现有功能）；未认证口径仍然可用，但必须在展示时标注为草稿。

CERTIFY_SCOPE = ("metric", "dimension")


def certify_metric(metric_id: int, certified: bool, owner: str = "",
                   certified_by: str = "") -> dict:
    """认证/解除认证指标口径。

    Args:
        metric_id: 指标 id
        certified: True=认证为可信口径；False=退回草稿
        owner: 口径责任人（认证时必填——谁认的、谁负责口径不漂）
        certified_by: 操作者标记（审计用，如 `user:1`）

    Returns:
        {"metric_id","name","certified","owner"}
    """
    return _certify("adh_metrics", metric_id, certified, owner, certified_by)


def certify_dimension(dim_id: int, certified: bool, owner: str = "",
                      certified_by: str = "") -> dict:
    """认证/解除认证维度口径（语义同 certify_metric）。"""
    return _certify("adh_dimensions", dim_id, certified, owner, certified_by)


def _certify(table: str, row_id: int, certified: bool, owner: str,
             certified_by: str) -> dict:
    if table not in ("adh_metrics", "adh_dimensions"):
        raise ValueError("不支持的口径类型")
    if certified and not str(owner or "").strip():
        # 认证必须有责任人：没有责任人的口径不叫认证，只是把 0 改成 1
        raise ValueError("认证口径必须指定 owner（口径责任人）")
    # 责任人列两表不同：adh_metrics.owner / adh_dimensions.owner_role
    owner_col = "owner" if table == "adh_metrics" else "owner_role"
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id, name, certified, {owner_col} AS owner FROM {table} WHERE id = %s",
                (row_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError(f"口径不存在: {row_id}")
            cur.execute(
                f"UPDATE {table} SET certified = %s, {owner_col} = %s, updated_at = %s WHERE id = %s",
                (1 if certified else 0, str(owner or "").strip() or (row.get("owner") or ""),
                 _now(), row_id))
    logger.info("[Metrics] certify %s id=%s certified=%s owner=%s by=%s",
                table, row_id, certified, owner, certified_by)
    return {"id": row_id, "name": row.get("name"), "certified": bool(certified),
            "owner": str(owner or "").strip() or (row.get("owner") or "")}


def certification_summary(datasource_id: int = 0) -> dict:
    """口径认证覆盖率（P4.3）：暴露「多少口径还没人负责」这个事实。

    认证率低不是缺陷，是**待办清单**——但必须可见，不能假装口径都是权威的。
    """
    scope = " AND datasource_id = %s" if datasource_id else ""
    params = (datasource_id,) if datasource_id else ()
    with DBConnection() as conn:
        with conn.cursor() as cur:
            # 责任人列两表不同：adh_metrics.owner / adh_dimensions.owner_role
            cur.execute(
                f"SELECT COUNT(*) total, SUM(COALESCE(certified,0)) cert, "
                f"       SUM(CASE WHEN COALESCE(owner,'')='' THEN 1 ELSE 0 END) no_owner "
                f"FROM adh_metrics WHERE is_active = 1{scope}", params)
            m = cur.fetchone() or {}
            cur.execute(
                f"SELECT COUNT(*) total, SUM(COALESCE(certified,0)) cert, "
                f"       SUM(CASE WHEN COALESCE(owner_role,'')='' THEN 1 ELSE 0 END) no_owner "
                f"FROM adh_dimensions WHERE is_active = 1{scope}", params)
            d = cur.fetchone() or {}

    def _pack(row):
        total = int(row.get("total") or 0)
        cert = int(row.get("cert") or 0)
        return {"total": total, "certified": cert, "draft": total - cert,
                "without_owner": int(row.get("no_owner") or 0),
                "certified_ratio": round(cert / total, 4) if total else 0.0}

    metrics, dims = _pack(m), _pack(d)
    total = metrics["total"] + dims["total"]
    cert = metrics["certified"] + dims["certified"]
    return {
        "metrics": metrics, "dimensions": dims,
        "overall": {"total": total, "certified": cert, "draft": total - cert,
                    "certified_ratio": round(cert / total, 4) if total else 0.0},
        "note": ("口径全部已认证" if total and cert == total else
                 f"仍有 {total - cert} 个口径未认证（草稿），展示时应标注为草稿而非权威答案"),
    }
