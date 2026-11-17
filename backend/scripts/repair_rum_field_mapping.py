"""RUM 观测本体「建模字段错配」修复（幂等迁移，dry-run 默认）。

现象：ChatBI 问「EDMCA 落地页访问量」全部 `blocked_at=preflight`，报
`无法解析指标「页面浏览量」…可用指标: []` + `时间维度「统计日期」无法解析, 可用时间维度: 无`。
根因不是"还没建模"，而是**建模字段错配**（同一个对象三层名字并存、两层不成立）：

    本体 doc properties[].column   rum.view_url    ← 哪儿都不存在
    字典 target_column            view_url        ← logstore-rum 无此列（只有数据集输出列有）
    部署绑定 column_map           view.url        ← 真实 SLS 列（唯一成立的一层）

来历：对象当初走「受治理数据集」——预制 SQL 把带点字段展平成 ASCII 列（`"view.url" AS view_url`、
`substr(from_unixtime(__time__)) AS event_date`），属性与字典列名都按这套展平名登记。
v0.5 B6b 把物理绑定收归部署绑定层后，绑定退化为 `bind_kind=primary + physical_table=logstore-rum`
直查原始 logstore，数据集通道消失，展平名却留了下来；再加上 19 指标 + 28 维度
`target_table` 全空（planner 按 `target_table=物理表` 加载字典 → 命中 0 行）。

口径（用户拍板）：**数据集只是快速路径，不是语义层核心逻辑**，因此按「直查物理源」对齐三层：
1. doc `properties[].column` → 真实列（`view.url`；时间维 → 引擎内置事件时间 `__time__`）；
   19 个度量属性归位成 `metrics[]` 名字引用（口径唯一登记在指标字典，ontology-modeling §3）；
   `doc.kind` 与模型列不一致（列 business / doc source）由 save_draft 按模型列归一；
2. 字典 `target_table`/`target_column` → 对象绑定物理表 + 真实列。写入一律走
   `metrics_service`，其 `_normalize_dim_physical_binding` 按列元数据实存裁决：
   该表没这列就抛错（宁阻断勿落错映射），元数据未同步则保留完整写法不丢路径段；
3. 伪公式 → 真实列的可编译单表聚合（`'成功资源请求数 / 有结果资源请求数 * 100'`、
   `'COUNT(DISTINCT user.id WHERE 有异常事件)'` 这类中文伪代码会被 planner 原样拼进 SELECT）；
4. 口径文字：旧数据集正文隐含 `WHERE "app.env"='prod'`，直查后不再隐式过滤 ——
   登记 `应用环境` 维度并把"需显式过滤"写进对象/字典描述（不静默改口径，见 no-silent-degradation）。

用法（repo 根目录）:
  venv/bin/python backend/scripts/repair_rum_field_mapping.py              # dry-run 报告
  venv/bin/python backend/scripts/repair_rum_field_mapping.py --apply
  ... --model-id 1792410799803865 --object-key rum_event
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)

MODEL_ID = 1792410799803865
OBJECT_KEY = "rum_event"

# 引擎内置事件时间（SLS LogStore，epoch 秒）：时间维登记在它上面，
# 粒度由业务名承载（统计日期=天 / 统计小时=小时，见 planner._grain_from_dim_name）。
TIME_COLUMN = "__time__"
TIME_PROPERTIES = ("统计日期", "统计小时")

# 业务名 → 真实物理列（来源=数据集正文里 `"real" AS flat` 的展平关系，逐条对回原列；
# 已由列元数据实存校验，脚本会对不上的直接报错不放过）。
PROPERTY_COLUMN: dict[str, str] = {
    "事件类型": "event_type",
    "应用名称": "app.name",
    "应用版本": "app.version",
    "应用渠道": "app.channel",
    "应用类型": "app.type",
    "页面名称": "view.name",
    "页面URL": "view.url",
    "页面域名": "view.domain",
    "资源名称": "resource.name",
    "资源类型": "resource.type",
    "资源请求结果": "resource.success",
    "设备类型": "device.type",
    "设备品牌": "device.brand",
    "操作系统": "os.type",
    "浏览器": "os.container",
    "省份": "geo.province",
    "城市": "geo.city",
    "国家": "geo.country",
    "网络运营商": "isp.name",
    "异常类型": "exception.type",
    "异常子类型": "exception.subtype",
    "异常名称": "exception.name",
    "会话ID": "session.id",
    "用户ID": "user.id",
    "客户端IP": "net.ip",
    "会话IP": "session.ip",
    # 旧数据集通道隐式过滤 env=prod，直查后由该维度显式承载（新建属性也须在同一映射里，
    # 否则二次运行会被当「未识别属性」过滤掉 → 幂等破坏）
    "应用环境": "app.env",
}

# 度量：属性里堆的其实是指标口径，归位到 metrics[] 名字引用（字典是唯一权威）。
MEASURE_PROPERTIES = (
    "事件总数", "会话数", "独立用户数", "独立IP数", "页面浏览量", "资源请求数",
    "资源错误数", "资源成功率(%)", "资源加载P95耗时(ms)", "异常数", "长任务数",
    "人均会话数", "人均会话时长(秒)", "异常用户数", "异常会话数",
    "资源加载平均耗时(ms)", "首屏时间FCP(ms)", "最大内容绘制LCP(ms)", "会话平均时长(ms)",
)

# 伪公式 → 可编译单表聚合（保持「聚合函数开头 + 无 WHERE/子查询 + 无跨表限定」语法，
# 见 metric_formula.metric_safe_target：不满足则 target_table 派生不出来 = 目录挂着但编译不了）。
METRIC_FORMULA_FIX: dict[str, str] = {
    "资源成功率(%)": (
        "SUM(CASE WHEN resource.success = 1 THEN 1 ELSE 0 END) * 100.0 "
        "/ NULLIF(SUM(CASE WHEN resource.success IS NOT NULL THEN 1 ELSE 0 END), 0)"),
    "人均会话数": "COUNT(DISTINCT session.id) * 1.0 / NULLIF(COUNT(DISTINCT user.id), 0)",
    "人均会话时长(秒)": (
        "SUM(session.time_spent) * 1.0 / NULLIF(COUNT(DISTINCT user.id), 0) / 1000"),
    "异常用户数": "COUNT(DISTINCT CASE WHEN event_type = 'exception' THEN user.id END)",
    "异常会话数": "COUNT(DISTINCT CASE WHEN event_type = 'exception' THEN session.id END)",
}

# 旧数据集正文的隐含默认过滤（env=prod）在直查形态下不再自动生效 → 显式登记维度承载该口径
NEW_PROPERTY_ENV = {"column": "app.env", "name": "应用环境", "type": "string",
                    "is_key": False,
                    "description": "部署环境（ARMS RUM 采集字段）。旧数据集通道曾默认只取 prod，"
                                   "直查物理源后不再隐式过滤：统计生产口径请显式加「应用环境=生产」过滤"}
# 枚举标签只登记实测存在的取值（arms-prd-cn 近 7 天 app.env 仅 prod/test 两值，探针取证）；
# 未命中的取值按 physical 原样输出（planner._label_to_code 只在标签集内反查）。
ENV_VALUE_LABELS = {"prod": "生产", "test": "测试"}

# 采集口径必须可见（避免“精确相等”默默少数）：实测 view.url 同一路径存在带/不带 query string
# 两种形状（`.../zh-CN/direct-ip` 与 `.../zh-CN/direct-ip?channel=EDMCA` 并存）。
DIM_DESC_NOTE: dict[str, str] = {
    "页面URL": ("页面完整 URL（ARMS RUM 观测维度）。采集侧时带时不带 query string（实测同一路径"
             "两种形状并存）：按渠道/活动统计请用包含匹配（op=like），精确相等会漏数"),
}

# 描述里的旧口径承诺（数据集正文默认 env=prod）——直查形态下必须改写，否则口径文字与行为不符
_DESC_OLD = "（ARMS RUM，默认 env=prod）"
_DESC_NEW = "（ARMS RUM 直查 logstore：不隐式过滤环境，生产口径请显式加「应用环境=生产」）"


def _load_binding(cur, model_id: int, object_key: str) -> dict:
    cur.execute(
        "SELECT id, physical_table, datasource_id, column_map, bind_kind, dataset_ref, product_ref "
        "FROM adh_ontology_bindings WHERE model_id=%s AND object_key=%s AND status='active'",
        (model_id, object_key))
    rows = cur.fetchall() or []
    if not rows:
        raise RuntimeError(f"对象 {object_key} 在模型 {model_id} 下没有生效的部署绑定行（无从确定物理表）")
    if len(rows) > 1:
        raise RuntimeError(f"对象 {object_key} 有多条生效绑定行 {len(rows)}，本脚本只处理单源单表形态")
    r = dict(rows[0])
    cm = r.get("column_map")
    r["column_map"] = json.loads(cm) if isinstance(cm, str) and cm else (cm or {})
    r["physical_table"] = str(r.get("physical_table") or "")
    r["datasource_id"] = int(r.get("datasource_id") or 0)
    return r


def _check_columns(table: str, ds_id: int) -> tuple[set, list[str]]:
    """待登记列全部对着列元数据实存校一遍；元数据为空（源未同步）时跳过校验并出声。"""
    from backend.modules.catalog.services.physical_ref import ENGINE_BUILTIN_COLUMNS, columns_of_table

    want = sorted(set(PROPERTY_COLUMN.values()) | {TIME_COLUMN})
    known = columns_of_table(ds_id, table)
    if not known:
        print(f"[warn] 数据源 {ds_id} 的表 {table} 无列元数据登记，跳过实存校验（元数据同步后应复查）")
        return set(), []
    bad = [c for c in want if c not in known and c not in ENGINE_BUILTIN_COLUMNS]
    return known, bad


def _repair_doc(doc: dict, object_key: str, table: str) -> tuple[dict, list[str]]:
    """doc properties 列名归位 + 度量归并进 metrics[]；返回(告警清单, 变更摘要)。"""
    notes: list[str] = []
    obj = next((o for o in doc.get("objects") or []
                if str(o.get("key") or o.get("object_key") or "") == object_key), None)
    if not obj:
        raise RuntimeError(f"doc 里没有对象 {object_key}")

    kept, moved, unknown = [], [], []
    for p in obj.get("properties") or []:
        name = str(p.get("name") or "").strip()
        if not name:
            unknown.append(str(p.get("column") or "?"))
            continue
        if name in MEASURE_PROPERTIES:
            moved.append(name)
            continue
        if name in TIME_PROPERTIES:
            p["column"] = TIME_COLUMN
            p["type"] = "timestamp"
            # 描述不给物理列名（护栏 §7：列名不得进 LLM/云端视野）
            p["description"] = (f"{name}（事件时间，按秒存于日志服务内置时间列；"
                                "粒度由维度业务名承载）")
            kept.append(name)
            continue
        real = PROPERTY_COLUMN.get(name)
        if not real:
            unknown.append(name)
            continue
        if str(p.get("column") or "").strip() != real:
            notes.append(f"属性 {name}: column {p.get('column')!r} → {real!r}")
        p["column"] = real
        kept.append(name)

    obj["properties"] = [p for p in (obj.get("properties") or [])
                         if str(p.get("name") or "").strip() in kept]
    if not any(str(p.get("name") or "") == "应用环境" for p in obj["properties"]):
        obj["properties"].append(dict(NEW_PROPERTY_ENV))
        notes.append(f"新增属性 应用环境 → {NEW_PROPERTY_ENV['column']}（承接旧数据集默认过滤）")

    refs = sorted(set(moved) | set(MEASURE_PROPERTIES))
    obj["metrics"] = refs
    obj["description"] = str(obj.get("description") or "").replace(
        "默认生产环境(app.env=prod)",
        "默认生产环境(app.env=prod，需显式过滤：直查物理源不自动附加该条件)")
    return {"moved_to_metrics": moved, "unknown_props": unknown, "kept": kept,
            "notes": notes}, obj


def _repair_dicts(object_key: str, table: str, apply: bool) -> dict:
    from backend.common.db.metadata_db import get_metadata_conn
    from backend.modules.catalog.services import metrics_service as ms

    out = {"dim_fixed": [], "dim_bad": [], "dim_desc": [], "met_fixed": [], "met_bad": [],
           "desc_fixed": 0}
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, target_table, target_column, description FROM adh_dimensions "
                "WHERE is_active=1 AND bound_object_key=%s ORDER BY id", (object_key,))
            dims = [dict(r) for r in cur.fetchall() or []]
            cur.execute(
                "SELECT id, name, formula, target_table, description FROM adh_metrics "
                "WHERE is_active=1 AND bound_object_key=%s ORDER BY id", (object_key,))
            mets = [dict(r) for r in cur.fetchall() or []]

    for d in dims:
        name = str(d["name"])
        col = TIME_COLUMN if name in TIME_PROPERTIES else PROPERTY_COLUMN.get(name)
        if not col:
            out["dim_bad"].append(f"{name}(id={d['id']}) 无真实列映射 → 需人工判定")
            continue
        payload: dict = {}
        if (d.get("target_table") or "") != table or (d.get("target_column") or "") != col:
            payload.update({"target_table": table, "target_column": col,
                            "bound_object_key": object_key})
            out["dim_fixed"].append(
                f"{name}: {d.get('target_table') or '∅'}.{d.get('target_column') or '∅'} → {table}.{col}")
        note = DIM_DESC_NOTE.get(name)
        if note and note not in str(d.get("description") or ""):
            payload["description"] = note
            out["dim_desc"].append(name)
        if payload and apply:
            ms.update_dimension(int(d["id"]), payload)

    for m in mets:
        name = str(m["name"])
        formula = METRIC_FORMULA_FIX.get(name) or str(m.get("formula") or "")
        desc = str(m.get("description") or "")
        payload: dict = {"bound_object_key": object_key, "formula": formula}
        if _DESC_OLD in desc:
            payload["description"] = desc.replace(_DESC_OLD, _DESC_NEW)
            out["desc_fixed"] += 1
        old_tt = str(m.get("target_table") or "")
        # 报告口径与写入口径一致：只有「真的变了」才计入修正（否则二次 dry-run 永远显示待改，
        # 幂等自检失真）
        if old_tt != table or str(m.get("formula") or "") != formula:
            out["met_fixed"].append(
                f"{name}: target_table {old_tt or '∅'} → {table}"
                + ("" if str(m.get("formula") or "") == formula else
                   f"；公式 {m.get('formula')!r} → {formula!r}"))
        if apply:
            ms.update_metric(int(m["id"]), payload)
    return out


def _ensure_env_dimension(apply: bool) -> str:
    """`应用环境` 维度行（doc 有属性、字典没行的话，ChatBI 无法按环境过滤 → 补登记）。"""
    from backend.common.db.metadata_db import get_metadata_conn
    from backend.modules.catalog.services import metrics_service as ms

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM adh_dimensions WHERE name=%s AND is_active=1",
                        (NEW_PROPERTY_ENV["name"],))
            if cur.fetchone():
                return "已存在"
    if apply:
        ms.create_dimension({
            "display_name": NEW_PROPERTY_ENV["name"], "name_en": "app_env",
            "bound_object_key": OBJECT_KEY, "category": "状态",
            "description": NEW_PROPERTY_ENV["description"],
            "value_labels": ENV_VALUE_LABELS, "is_active": 1})
        return "已创建"
    return "待创建(dry-run)"


def run(apply: bool, model_id: int, object_key: str) -> int:
    from backend.common.db.metadata_db import get_metadata_conn
    from backend.modules.catalog.services import ontology_service as osvc

    model = osvc.get_model(model_id)
    if not model:
        print(f"ERR: 模型 {model_id} 不存在", file=sys.stderr)
        return 2
    doc = json.loads(model["json_content"])

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            binding = _load_binding(cur, model_id, object_key)
    table = binding["physical_table"]
    ds_id = binding["datasource_id"]
    if not table:
        print("ERR: 绑定行没有物理表（对象当前不可取数），先修部署绑定再跑本脚本", file=sys.stderr)
        return 2
    if str(binding.get("bind_kind") or "") != "primary" or binding.get("dataset_ref"):
        print(f"ERR: 对象绑定形态不是直查物理源（bind_kind={binding.get('bind_kind')} "
              f"dataset_ref={binding.get('dataset_ref') or '∅'}）——本脚本只修直查形态", file=sys.stderr)
        return 2
    print(f"对象 {object_key} → 物理表 {table}（bind_kind=primary，产品 {binding.get('product_ref') or '∅'}）")

    known, bad = _check_columns(table, ds_id)
    if bad:
        print("ERR: 以下待登记列在表实存列中不存在，先核对映射再执行: " + ", ".join(bad), file=sys.stderr)
        return 2

    if str(doc.get("kind") or "") != str(model.get("kind") or ""):
        print(f"doc.kind={doc.get('kind')!r} 与模型列 kind={model.get('kind')!r} 不一致"
              "（save_draft 会按模型列归一——编辑表单据此判定分支，错则给错表单）")

    info, _obj = _repair_doc(doc, object_key, table)
    print(f"属性归位: 保留 {len(info['kept'])} 个真实列属性 | 度量移入 metrics[] {len(info['moved_to_metrics'])} 个"
          f" | metrics[] 引用 {len(doc['objects'][0].get('metrics') or [])} 个")
    for n in info["notes"][:8]:
        print("  -", n)
    if len(info["notes"]) > 8:
        print(f"  … 共 {len(info['notes'])} 条属性改名")
    if info["unknown_props"]:
        print(f"[warn] 未识别属性（脚本无映射，保持原样，需人工判定）: {info['unknown_props']}")

    dicts = _repair_dicts(object_key, table, apply)
    print(f"维度字典: 修正 {len(dicts['dim_fixed'])} 行 | 待人工 {len(dicts['dim_bad'])} 行")
    for x in dicts["dim_fixed"][:8]:
        print("  -", x)
    if len(dicts["dim_fixed"]) > 8:
        print(f"  … 共 {len(dicts['dim_fixed'])} 行")
    for x in dicts["dim_bad"]:
        print("  !", x)
    if dicts["dim_desc"]:
        print(f"维度口径文字补充: {dicts['dim_desc']}")
    print(f"指标字典: 修正 {len(dicts['met_fixed'])} 行（含伪公式改写）| 口径文字改写 {dicts['desc_fixed']} 行")
    for x in dicts["met_fixed"][:6]:
        print("  -", x)
    if len(dicts["met_fixed"]) > 6:
        print(f"  … 共 {len(dicts['met_fixed'])} 行")
    print(f"应用环境维度: {_ensure_env_dimension(apply)}")

    if not apply:
        print("[dry-run] 加 --apply 落库")
        return 0

    # active 模型 save_draft = 就地级联（重展开对象 + 内嵌绑定回填 + enum→字典联动 + 重建图谱）；
    # 部署绑定行不被触碰（B6b：物理形状唯一在绑定层，本脚本只读它）。
    res = osvc.save_draft(model_id, json.dumps(doc, ensure_ascii=False))
    warns = res.get("validation_warnings") or []
    if warns:
        print("[warn] save_draft 级联告警:")
        for w in warns[:10]:
            print("  -", w if isinstance(w, str) else json.dumps(w, ensure_ascii=False))
    print("[apply] 已落库（doc 级联 + 字典物理锚点/公式/口径文字）")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="RUM 建模字段错配修复（幂等，dry-run 默认）")
    p.add_argument("--apply", action="store_true", help="落库（默认只报告）")
    p.add_argument("--model-id", type=int, default=MODEL_ID)
    p.add_argument("--object-key", default=OBJECT_KEY)
    args = p.parse_args()
    return run(apply=args.apply, model_id=args.model_id, object_key=args.object_key)


if __name__ == "__main__":
    raise SystemExit(main())
