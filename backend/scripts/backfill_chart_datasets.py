"""看板直连 SQL 图表收编为数据集（幂等，默认 dry-run）。

为什么要它（看板取数口径收口）：
  看板编辑器过去允许用户在「图表配置 → 数据源」里手写 SQL 并直接执行，SQL 与
  `config.countSql` 分散在 38 个图表行里，既没进 BI 治理建模层（数据集），也没被
  角色清单裁决 —— 血缘在数据集层断裂（`lineage_service` 把这类图显式记为 gap），
  口径无法复用、改动无从审计。产品决策（2026-11-06）是：**看板图表只能挂数据集**，
  SQL 的编写收口到数据集（有认证、有字段声明、有血缘、有角色清单）。
  闸门落地前必须先把存量直连 SQL 图收编干净，否则现网看板当场取数报错。

本脚本做四件事（幂等，可重复执行）：
  ① 归一化比对图表 SQL 与现有 active 数据集正文（去尾部 LIMIT、占位符语法统一、
     折叠空白、大小写不敏感）——**等价且执行源相同、且数据集已声明图表用到的全部
     参数**才复用（不猜口径）；
  ② 不等价的图表按原 SQL 新建数据集（正文原样保留，含其 LIMIT；`{{x}}` 转 `${x}`
     并同名声明为 string 变量），来源标注 chart_backfill；快照图（AI 会话产物）
     标注 ai_session，并把原快照 id 留存进 config 以便溯源；
  ③ 图表改挂数据集：`source_type='dataset'`、`source_id=<数据集>`、`sql_query` 置空、
     原 SQL 的尾部 LIMIT 收进 `config.limit`（行数上限属于口径，不能丢也不能靠默认值）；
     同时从 config 移除 `datasource_id` / `countSql`（数据集定源、服务端包 COUNT，
     留着就是第二套口径）；
  ④ 角色授权联动：按「看板可见 ⇒ 其图表引用的数据集可编辑」的拍板口径，为已可见
     该看板的角色 `INSERT IGNORE` 补授缺失的数据集授权（先数据集后看板，与在线
     授权入口同一口径）。

用法（repo 根目录）:
  venv/bin/python backend/scripts/backfill_chart_datasets.py              # dry-run 看计划
  venv/bin/python backend/scripts/backfill_chart_datasets.py --apply      # 落库
  venv/bin/python backend/scripts/backfill_chart_datasets.py --apply --dashboard 1792466175872
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)

from backend.common.db import execute_query, execute_write                      # noqa: E402
from backend.modules.viz.services import dataset_service                        # noqa: E402

# 尾部行数上限（口径的一部分）：`... LIMIT 20` / `LIMIT 10 OFFSET 5`
_TRAILING_LIMIT_RE = re.compile(r"\bLIMIT\s+(\d+)(\s+OFFSET\s+\d+)?\s*$", re.IGNORECASE)
# 图表侧历史占位符 {{x}}（数据集侧规范是 ${x}，收编时统一并同名声明变量）
_CHART_PLACEHOLDER_RE = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")
# 被单引号包住的占位符：'{{x}}' / '${x}'
_QUOTED_PLACEHOLDER_RE = re.compile(r"'\s*(?:\{\{|\$\{)\s*([\w.]+)\s*(?:\}\}|\})\s*'")


def _normalize_placeholders(sql: str) -> str:
    """占位符写法归一为数据集侧规范 `${name}`（不留外层单引号）。

    两个理由：
    1) 等价判定不能把 `nullif('{{d}}','')` 与 `nullif(${d},'')` 当成不同口径——
       字符串型变量本身就是服务端渲染成带引号字面量（sql_params.render_value），
       写法差引号不是口径差；
    2) 新正文必须跟数据集现有写法一致：否则 `'${d}'` + type=string 会渲染成
       `''2026-01-01''`（双重引号）直接语法错。不猜类型，按平台约定统一去引号。
    """
    body = _QUOTED_PLACEHOLDER_RE.sub(lambda m: "${" + m.group(1) + "}", sql or "")
    return _CHART_PLACEHOLDER_RE.sub(lambda m: "${" + m.group(1) + "}", body)


def _collapse(sql: str) -> str:
    return re.sub(r"\s+", " ", sql or "").strip().rstrip(";").strip()


def _strip_trailing_limit(sql: str) -> tuple[str, int]:
    """拆出正文与尾部 LIMIT 行数上限；正文里没有 LIMIT 时返回 (原文, 0)。"""
    body = _collapse(sql).rstrip(";")
    m = _TRAILING_LIMIT_RE.search(body)
    if not m:
        return body, 0
    return body[:m.start()].rstrip(), int(m.group(1))


def _as_dataset_body(sql: str) -> str:
    """比对用正文：去尾部 LIMIT + 占位符写法归一 + 大小写不敏感。

    大小写不敏感只对「同一条正文被复制后改了关键字大小写」成立；函数名/标识符在
    MySQL/Doris/SLS 里同样大小写不敏感，因此不会把不同口径判成等价。
    """
    body, _limit = _strip_trailing_limit(sql)
    return _normalize_placeholders(body).casefold()


def _placeholders(sql: str) -> list[str]:
    return sorted(set(_CHART_PLACEHOLDER_RE.findall(sql or "")))


def _declared_vars(dataset: dict) -> set[str]:
    """数据集已声明的参数名（variables 声明 ∪ 正文里的 ${} 占位符）。"""
    names = set(re.findall(r"\$\{\s*([\w.]+)\s*\}", str(dataset.get("sql_query") or "")))
    for key, raw in (("variables", dataset.get("variables")), ("sql_bodies", dataset.get("sql_bodies"))):
        text = raw if isinstance(raw, str) else json.dumps(raw or {}, ensure_ascii=False)
        names |= set(re.findall(r"\$\{\s*([\w.]+)\s*\}", text or ""))
    declared = dataset_service._parse_json(dataset.get("variables"), {})
    if isinstance(declared, dict):
        names |= {str(k) for k in declared}
    return names


def _chart_datasource(chart: dict) -> int:
    cfg = dataset_service._parse_json(chart.get("config"), {}) or {}
    return int(cfg.get("datasource_id") or 0)


def _find_equivalent_dataset(chart: dict, datasets: list[dict]) -> dict | None:
    """现有数据集里有没有与图表口径等价的那一个（复用优先，不重复造口径）。"""
    want_body = _as_dataset_body(chart.get("sql_query") or "")
    want_ds = _chart_datasource(chart)
    want_vars = set(_placeholders(chart.get("sql_query") or ""))
    for row in datasets:
        if int(row.get("datasource_id") or 0) != want_ds:
            continue
        if _as_dataset_body(row.get("sql_query") or "") != want_body:
            continue
        if not want_vars <= _declared_vars(row):
            # 正文一样但参数没声明全：挂上去必然在编译期 fail-loud 报错，不算等价
            continue
        return row
    return None


def _plan_new_dataset_name(base: str, taken: set[str]) -> str:
    """数据集名全局唯一：同名时加数字后缀（不覆盖别人的口径）。"""
    name = (base or "").strip()[:120] or "未命名数据集"
    candidate, suffix = name, 2
    while candidate in taken:
        candidate = f"{name}-{suffix}"
        suffix += 1
    return candidate


def _new_dataset_payload(chart: dict, dash_name: str, name: str) -> dict:
    """按图表 SQL 造一份数据集定义（正文原样，形状声明留空交治理补）。

    不猜 field_config：正文已经定好输出形状，把 xCol/yCol 反推成"维度/度量声明"
    会给一个已聚合的列挂上 agg=SUM 之类的假口径。字段声明由数据集负责人在页面上补，
    本轮只搬口径。
    """
    cfg = dataset_service._parse_json(chart.get("config"), {}) or {}
    sql = str(chart.get("sql_query") or "").strip().rstrip(";")
    names = _placeholders(sql)
    variables = {n: {"type": "string", "default": ""} for n in names}
    body = _normalize_placeholders(sql)
    origin = "ai_session" if (chart.get("source_type") or "") == "snapshot" else "chart_backfill"
    source_label = "AI 会话产物（快照图）" if origin == "ai_session" else "看板直连 SQL 图表"
    preset = {k: cfg[k] for k in ("xCol", "yCol", "groupCol") if cfg.get(k)}
    _body, inner_limit = _strip_trailing_limit(sql)
    if inner_limit:
        preset["limit"] = inner_limit
    description = (f"由看板收编自动创建：来源={source_label}，看板「{dash_name}」图表"
                   f"「{chart.get('name')}」原直连 SQL 口径（正文原样保留，未人工补字段声明）")
    return {
        "name": name,
        "description": description,
        "datasource_id": _chart_datasource(chart),
        "sql_query": body,
        "variables": variables,
        "field_config": [],
        "chart_type": (chart.get("chart_type") or "").strip(),
        "chart_preset": preset,
        "origin": origin,
    }


def _chart_patch(chart: dict, dataset_id: int) -> dict:
    """收编后图表要写的字段（数据集定源定口径，图表只留可视化配置）。"""
    cfg = dict(dataset_service._parse_json(chart.get("config"), {}) or {})
    _body, inner_limit = _strip_trailing_limit(chart.get("sql_query") or "")
    if inner_limit:
        cfg["limit"] = inner_limit
    cfg.pop("datasource_id", None)     # 执行源唯一事实源 = 数据集
    cfg.pop("countSql", None)          # 服务端分页总数由服务端包 COUNT，不接受人写 SQL
    if (chart.get("source_type") or "") == "snapshot" and chart.get("source_id"):
        cfg["origin_snapshot_id"] = int(chart["source_id"])   # 溯源，不被 dataset id 顶掉
    return {"source_type": "dataset", "source_id": dataset_id, "sql_query": None, "config": cfg}


def _visible_roles(dashboard_id: int) -> list[int]:
    """能看到该看板的角色：直接看板授权 ∪ 组合按角色分配。"""
    rows = execute_query(
        "SELECT DISTINCT role_id FROM adh_role_dashboard_access WHERE dashboard_id=%s",
        (dashboard_id,)) or []
    roles = {int(r["role_id"]) for r in rows}
    rows = execute_query(
        "SELECT DISTINCT rg.role_id FROM adh_dashboard_group_items gi "
        "JOIN adh_role_dashboard_groups rg ON rg.group_id = gi.group_id "
        "WHERE gi.dashboard_id=%s", (dashboard_id,)) or []
    return sorted(roles | {int(r["role_id"]) for r in rows})


def collect_candidates(dashboard_id: int | None) -> tuple[list[dict], list[tuple]]:
    """待收编图表 = 带正文、尚未挂数据集、非控件；模板示例图与 AS-BOT 设计产物除外。"""
    ph = " AND c.dashboard_id = %s" if dashboard_id else ""
    params = (dashboard_id,) if dashboard_id else ()
    rows = execute_query(
        "SELECT c.id, c.dashboard_id, c.name, c.chart_type, c.source_type, c.source_id, "
        "c.sql_query, c.config, d.name AS dashboard_name, d.owner_id "
        "FROM adh_charts c JOIN adh_dashboards d ON d.id = c.dashboard_id "
        "WHERE COALESCE(c.sql_query, '') <> '' "
        "AND COALESCE(c.source_type, '') <> 'dataset'" + ph + " "
        "ORDER BY c.dashboard_id, c.id", params) or []
    kept, skipped = [], []
    for r in rows:
        if str(r.get("chart_type") or "").startswith("widget_"):
            continue                     # 控件无取数口径，不参与收编
        cfg = dataset_service._parse_json(r.get("config"), {}) or {}
        # 模板示例图（未绑源）与 AS-BOT 设计产物不在收编范围：前者没有取数口径，
        # 后者的 SQL 由服务端生成、走设计面板的显式例外通道（护栏 §7）
        if cfg.get("demoData") is True or cfg.get("as_bot_design"):
            skipped.append((r["id"], r["name"], "模板示例图/AS-BOT 设计产物，不属于人写 SQL"))
            continue
        kept.append(r)
    return kept, skipped


def build_plan(dashboard_id: int | None) -> dict:
    charts, skipped = collect_candidates(dashboard_id)
    datasets = [d for d in (execute_query(
        "SELECT id, name, datasource_id, sql_query, variables, status FROM adh_datasets "
        "WHERE status='active'") or []) if (d.get("sql_query") or "").strip()]
    existing_names = {str(d["name"]) for d in (execute_query(
        "SELECT name FROM adh_datasets") or [])}

    plan: dict = {"reuse": [], "create": [], "chart_updates": [], "role_grants": {},
                  "skipped": skipped, "errors": [], "dashboard_wants": {}}
    fresh: dict[str, dict] = {}          # 本轮新建数据集去重（归一化正文 + 执行源）
    # 每看板收编后的数据集清单（含存量已挂的）。按**名字**算而不是按 id：
    # dry-run 时新数据集还没入库（无 id），拿 id 集合会出现"dry-run 说不用补、
    # 真落库后要补"的两口径；名字在入库前就已确定，两种模式看到同一张清单。
    per_dashboard: dict[int, set[str]] = {}
    dash_names: dict[int, str] = {}
    for row in execute_query(
            "SELECT c.dashboard_id AS d_id, MAX(c2.name) AS d_name, GROUP_CONCAT(d.name) AS names "
            "FROM adh_charts c JOIN adh_dashboards c2 ON c2.id = c.dashboard_id "
            "JOIN adh_datasets d ON d.id = c.source_id "
            "WHERE c.source_type='dataset' AND c.source_id IS NOT NULL "
            "GROUP BY c.dashboard_id") or []:
        per_dashboard.setdefault(int(row["d_id"]), set()).update(
            [n for n in str(row["names"] or "").split(",") if n])
        dash_names[int(row["d_id"])] = str(row["d_name"])

    def _track(chart: dict, dataset_id: int | None, dataset_name: str, mode: str) -> None:
        per_dashboard.setdefault(chart["dashboard_id"], set()).add(dataset_name)
        dash_names.setdefault(chart["dashboard_id"], chart["dashboard_name"])
        plan["chart_updates"].append({"chart_id": chart["id"], "dataset_id": dataset_id,
                                      "dataset_name": dataset_name, "mode": mode,
                                      "dashboard": chart["dashboard_name"],
                                      "dashboard_id": chart["dashboard_id"],
                                      "patch": _chart_patch(chart, dataset_id or 0)})

    for chart in charts:
        if not _chart_datasource(chart):
            plan["errors"].append({"chart_id": chart["id"], "name": chart["name"],
                                   "reason": "图表未登记执行数据源(config.datasource_id)，"
                                             "无法确定数据集执行源 —— 需人工指定后重跑"})
            continue
        hit = _find_equivalent_dataset(chart, datasets)
        if hit:
            plan["reuse"].append({"chart_id": chart["id"], "chart_name": chart["name"],
                                  "dataset_id": hit["id"], "dataset_name": hit["name"],
                                  "dashboard": chart["dashboard_name"]})
            _track(chart, int(hit["id"]), hit["name"], "reuse")
            continue
        key = f"{_chart_datasource(chart)}|{_as_dataset_body(chart['sql_query'])}"
        if key in fresh:
            shared = fresh[key]
            _track(chart, None, shared["payload"]["name"], "new-shared")
            continue
        name = _plan_new_dataset_name(chart["name"] or f"图表 {chart['id']}", existing_names)
        existing_names.add(name)
        payload = _new_dataset_payload(chart, chart["dashboard_name"], name)
        entry = {"chart_id": chart["id"], "chart_name": chart["name"],
                 "dashboard": chart["dashboard_name"], "dashboard_id": chart["dashboard_id"],
                 "dashboard_owner_id": int(chart.get("owner_id") or 0),
                 "first_chart_id": chart["id"],
                 "origin": payload.pop("origin"), "payload": payload}
        fresh[key] = entry
        plan["create"].append(entry)
        _track(chart, None, name, "new")

    # 角色补授：看板（含组合）可见角色 × 该看板内图表引用的数据集
    for d_id, wanted in sorted(per_dashboard.items()):
        plan["dashboard_wants"][d_id] = {"dashboard": dash_names.get(d_id, str(d_id)),
                                         "datasets": sorted(wanted)}
        for role_id in _visible_roles(d_id):
            granted = {str(r["name"]) for r in (execute_query(
                "SELECT d.name FROM adh_role_dataset_access ra "
                "JOIN adh_datasets d ON d.id = ra.dataset_id WHERE ra.role_id=%s",
                (role_id,)) or [])}
            missing = wanted - granted
            if missing:
                plan["role_grants"].setdefault(role_id, []).extend(sorted(missing))
    return plan


def apply_plan(plan: dict, identity: dict) -> dict:
    """落库：先建数据集 → 再改挂图表 → 最后补角色授权（顺序即依赖方向）。"""
    created = []
    for entry in plan["create"]:
        # 属主跟看板属主（不是跟跑脚本的人）：数据集的编辑权 = 创建者/admin，
        # 挂错属主会让看板负责人反过来改不了自己看板的口径
        owner = int(entry.get("dashboard_owner_id") or identity.get("user_id") or 0)
        ds = dataset_service.create_dataset(
            entry["payload"], {**identity, "user_id": owner}, origin=entry["origin"])
        entry["dataset_id"] = int(ds["id"])
        created.append({"dataset_id": ds["id"], "name": ds["name"], "origin": entry["origin"],
                        "owner_id": owner})

    updated = 0
    for item in plan["chart_updates"]:
        dataset_id = item["dataset_id"]
        if dataset_id is None:                       # 本轮新建的：按名字回捞 id
            row = execute_query("SELECT id FROM adh_datasets WHERE name=%s",
                                (item["dataset_name"],), fetchone=True)
            if not row:
                raise RuntimeError(f"收编中断：数据集「{item['dataset_name']}」未落库")
            dataset_id = int(row["id"])
        patch = dict(item["patch"])
        patch["source_id"] = dataset_id
        execute_write("UPDATE adh_charts SET source_type=%s, source_id=%s, sql_query=%s, config=%s "
                      "WHERE id=%s", (patch["source_type"], patch["source_id"], patch["sql_query"],
                                      json.dumps(patch["config"], ensure_ascii=False),
                                      item["chart_id"]))
        updated += 1
    # 结构变了必须失效共享载荷（Redis 跳进程可见）：否则 web 进程在一个 TTL 内
    # 仍把旧图表详情（带直连 SQL 的口径）发给读者，与库里新形状双口径并存
    from backend.modules.viz.services.dashboard_service import _invalidate_dashboard_payload
    for d_id in {c["dashboard_id"] for c in plan["chart_updates"] if c.get("dashboard_id")}:
        _invalidate_dashboard_payload(int(d_id))

    grants = 0
    names_to_id = {str(r["name"]): int(r["id"]) for r in (execute_query(
        "SELECT id, name FROM adh_datasets") or [])}
    for role_id, dataset_names in plan["role_grants"].items():
        for name in sorted(set(dataset_names)):
            ds_id = names_to_id.get(name)
            if not ds_id:
                # 不静默跳过：计划里要补授的数据集落不了地 = 该角色将来挂不上这口径
                raise RuntimeError(f"补授中断：数据集「{name}」在库中不存在（角色 id={role_id}）")
            execute_write("INSERT IGNORE INTO adh_role_dataset_access (role_id, dataset_id) "
                          "VALUES (%s, %s)", (role_id, ds_id))
            grants += 1
    return {"created": created, "charts_updated": updated, "role_grants": grants}


def report(plan: dict, applied: dict | None) -> None:
    new_chart_count = len([c for c in plan["chart_updates"] if c["mode"].startswith("new")])
    print("── 收编计划 ─────────────────────────────────────────────")
    print(f"复用现有数据集 : {len(plan['reuse'])} 个图表")
    for r in plan["reuse"]:
        print(f"    图表「{r['chart_name']}」 → 数据集「{r['dataset_name']}」"
              f"  （看板「{r['dashboard']}」，正文与参数等价）")
    print(f"新建数据集     : {len(plan['create'])} 个（承接 {new_chart_count} 个图表）")
    for c in plan["create"]:
        print(f"    图表「{c['chart_name']}」 → 新建「{c['payload']['name']}」"
              f" origin={c['origin']} 执行源={c['payload']['datasource_id']} 变量={sorted(c['payload']['variables'])}")
    if plan["skipped"]:
        print(f"跳过           : {len(plan['skipped'])} 个")
        for cid, name, why in plan["skipped"]:
            print(f"    #{cid} 「{name}」: {why}")
    if plan["errors"]:
        print(f"待人工处置     : {len(plan['errors'])} 个（未收编，闸门开启后这些图会取数报错）")
        for e in plan["errors"]:
            print(f"    #{e['chart_id']} 「{e['name']}」: {e['reason']}")
    if plan["dashboard_wants"]:
        print("看板→数据集需求 : ")
        for d_id, want in sorted(plan["dashboard_wants"].items()):
            print(f"    看板「{want['dashboard']}」需 {len(want['datasets'])} 个数据集："
                  + "、".join(want["datasets"][:6]) + (" …" if len(want["datasets"]) > 6 else ""))
    if plan["role_grants"]:
        print("角色数据集补授 : （口径：看板可见 ⇒ 其图表引用的数据集可编辑）")
        for role_id, dataset_names in sorted(plan["role_grants"].items()):
            role = execute_query("SELECT name FROM adh_roles WHERE id=%s", (role_id,), fetchone=True)
            labeled = sorted(set(dataset_names))
            print(f"    角色「{(role or {}).get('name', role_id)}」 +{len(labeled)} 个："
                  + "、".join(labeled[:8]) + (" …" if len(labeled) > 8 else ""))
    else:
        print("角色数据集补授 : 无需补授")
    if applied:
        print(f"\n已落库：新建数据集 {len(applied['created'])} 个 / 改挂图表 {applied['charts_updated']} 个 / "
              f"补授权 {applied['role_grants']} 条")
        for c in applied["created"]:
            print(f"    新数据集「{c['name']}」 origin={c['origin']} 属主 id={c['owner_id']}")
    else:
        print("\n（dry-run：未写库。加 --apply 执行）")


def main() -> int:
    ap = argparse.ArgumentParser(description="看板直连 SQL 图表收编为数据集（默认 dry-run）")
    ap.add_argument("--apply", action="store_true", help="真正落库")
    ap.add_argument("--dashboard", type=int, default=None, help="只收编指定看板")
    ap.add_argument("--owner", type=int, default=None,
                    help="新建数据集的属主 user_id（默认取看板属主）")
    args = ap.parse_args()

    plan = build_plan(args.dashboard)
    if args.apply and (plan["create"] or plan["chart_updates"] or plan["role_grants"]):
        owner = args.owner or int(execute_query(
            "SELECT owner_id FROM adh_dashboards ORDER BY updated_at DESC LIMIT 1",
            fetchone=True).get("owner_id") or 0)
        if not owner:
            print("缺属主：请用 --owner 指定新建数据集的属主 user_id", file=sys.stderr)
            return 2
        # role=admin 只为拿到编辑权（脚本等价于管理员治理动作）；owner_id 仍按传入属主落库
        applied = apply_plan(plan, {"user_id": owner, "role": "admin", "username": "backfill-script"})
    else:
        applied = None
    report(plan, applied)
    return 0


if __name__ == "__main__":
    sys.exit(main())
