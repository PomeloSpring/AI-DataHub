"""站点部署收敛器：把「对象 × 站点逐行登记」收敛为「一份契约 × 模型级适用站点」。

为什么需要本脚本（站点作用域改造，迁移 docker/mysql/site_scope_migration.sql）：
同构多站点下，旧口径要为每个对象在每个站点各登记一条 `adh_ontology_bindings`
（实测 27 对象 × 5 站 = 135 行，且物理表名/列映射**逐行完全相同**）——纯机械复制，
既难维护又漏配。新口径把站点声明上提到模型级：

  adh_ontology_models.deploy_sites = [{"site_code":"cn","datasource":"prd-cn"}, ...]

一次配置全对象共享；取数时 `binding_resolver._route_by_site` 用契约行描述形状、
把生效源改写到请求站。站点 schema 真不同的，按拍板「为该站单独配一个本体或使用
源本体」，不再回到逐对象逐站登记。

本脚本做四件事（幂等，默认 dry-run）：
  ① 从现存 active 绑定行派生各业务模型的 deploy_sites（**仅当该列为 NULL**，
     不覆盖人工配置）；
  ② 确保各站的 `adh_data_products` 行存在（数据产品是按站登记的治理身份，保留）；
  ③ 把与契约行**完全同构**的非契约站绑定行置 `status='deprecated'`（不物理删除，
     可回退）；
  ④ 不同构的行保留 active 并列成例外清单，交人工处置——**不静默删差异数据**。

用法（repo 根目录）:
  venv/bin/python backend/scripts/deploy_contract_bindings.py            # dry-run 看计划
  venv/bin/python backend/scripts/deploy_contract_bindings.py --apply    # 落库
  venv/bin/python backend/scripts/deploy_contract_bindings.py --model 1789467214865307
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)

# 同构判据：这几项全等才允许将站点行置 deprecated（任一不同即例外）
SHAPE_FIELDS = ("physical_table", "bind_kind", "dataset_ref", "join_expr", "column_map")


def _now(conn) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT UTC_TIMESTAMP(6) AS n")
        return str(cur.fetchone()["n"])


def _norm_shape(row: dict) -> tuple:
    """绑定行的"形状"指纹（catalog_ref 里的数据源段按本站归一后再比）。"""
    ds = str(row.get("datasource_name") or "")
    ref = str(row.get("catalog_ref") or "")
    if ds and ref:
        ref = ref.replace(ds, "<ds>")
    vals = []
    for f in SHAPE_FIELDS:
        v = row.get(f)
        if f == "column_map":
            try:
                parsed = json.loads(v) if isinstance(v, (str, bytes)) else v
            except (ValueError, TypeError):
                parsed = v
            vals.append(json.dumps(parsed or {}, sort_keys=True, ensure_ascii=False))
        else:
            vals.append(str(v or ""))
    return tuple(vals + [ref])


def _site_topology(cur) -> tuple[dict[str, int], dict[str, str], dict[str, list[str]]]:
    """(数据源名→id, 数据源名→site_code, site_code→[数据源名])，只含有站点的源。"""
    cur.execute("SELECT id, name, site_code FROM adh_datasources ORDER BY name")
    rows = cur.fetchall() or []
    ds_id = {str(r["name"]): int(r["id"]) for r in rows}
    ds_site = {str(r["name"]): str(r["site_code"] or "") for r in rows if r.get("site_code")}
    site_ds: dict[str, list[str]] = {}
    for name, code in ds_site.items():
        site_ds.setdefault(code, []).append(name)
    return ds_id, ds_site, site_ds


def _derive_deploy_sites(cur, model_id: int, ds_site: dict[str, str]) -> tuple[list[dict], list[str]]:
    """从该模型 active 绑定行派生站点声明；返回 (sites, 无法定站的源名)。"""
    cur.execute(
        "SELECT datasource_name FROM adh_ontology_bindings "
        "WHERE model_id = %s AND status = 'active' AND datasource_name <> '' "
        "GROUP BY datasource_name ORDER BY MIN(id)", (model_id,))
    names = [str(r["datasource_name"]) for r in (cur.fetchall() or [])]
    sites: list[dict] = []
    unmapped: list[str] = []
    seen: set[str] = set()
    for name in names:
        code = ds_site.get(name)
        if not code:
            unmapped.append(name)
            continue
        if code in seen:   # 一个站点只留首个源（多源同站由人工在本体建模页改选）
            continue
        seen.add(code)
        sites.append({"site_code": code, "datasource": name})
    return sites, unmapped


def run(apply: bool, only_model: int = 0) -> int:
    from backend.common.db.metadata_db import get_metadata_conn

    with get_metadata_conn() as conn:
        now = _now(conn)
        with conn.cursor() as cur:
            ds_id, ds_site, _site_ds = _site_topology(cur)
            cur.execute("SELECT site_code, display_name FROM adh_sites WHERE is_active = 1 "
                        "ORDER BY sort_order, site_code")
            site_rows = cur.fetchall() or []
            site_names = {str(r["site_code"]): str(r["display_name"]) for r in site_rows}
            site_order = [str(r["site_code"]) for r in site_rows]
            sql = ("SELECT id, name, kind FROM adh_ontology_models "
                   "WHERE COALESCE(kind, '') <> 'system'")
            params: list = []
            if only_model:
                sql += " AND id = %s"
                params.append(only_model)
            cur.execute(sql, tuple(params))
            models = cur.fetchall() or []
            if not models:
                print("没有需要收敛的业务模型")
                return 0

            plan_products: list[tuple] = []
            plan_deprecate: list[tuple] = []
            exceptions: list[tuple] = []
            for m in models:
                mid, mname = int(m["id"]), str(m["name"])
                cur.execute("SELECT deploy_sites FROM adh_ontology_models WHERE id = %s", (mid,))
                declared_raw = (cur.fetchone() or {}).get("deploy_sites")
                if isinstance(declared_raw, (bytes, bytearray)):
                    declared_raw = declared_raw.decode("utf-8", errors="replace")
                if isinstance(declared_raw, str):
                    try:
                        declared_raw = json.loads(declared_raw or "null")
                    except (ValueError, TypeError):
                        declared_raw = None

                cur.execute(
                    "SELECT id, object_key, datasource_name, product_ref, catalog_ref, bind_kind, "
                    "dataset_ref, join_expr, column_map, physical_table "
                    "FROM adh_ontology_bindings "
                    "WHERE model_id = %s AND status = 'active' ORDER BY object_key, id", (mid,))
                rows = cur.fetchall() or []
                by_object: dict[str, list[dict]] = {}
                for r in rows:
                    by_object.setdefault(str(r["object_key"]), []).append(r)

                # ① 派生模型级站点声明（仅 NULL 时写入，不覆盖人工配置）
                sites, unmapped = _derive_deploy_sites(cur, mid, ds_site)
                # 展示/路由顺序按站点清单的 sort_order，不按绑定行入库时间
                sites.sort(key=lambda s: (site_order.index(s["site_code"])
                                          if s["site_code"] in site_order else len(site_order),
                                          s["site_code"]))
                if declared_raw is None and sites:
                    print(f"\n[{mname}] deploy_sites 派生: "
                          + ", ".join(f"{s['site_code']}→{s['datasource']}" for s in sites)
                          + (f"（无法定站的源: {', '.join(unmapped)}）" if unmapped else ""))
                    if apply:
                        cur.execute("UPDATE adh_ontology_models SET deploy_sites = %s WHERE id = %s",
                                    (json.dumps(sites, ensure_ascii=False), mid))
                elif declared_raw:
                    print(f"\n[{mname}] deploy_sites 已人工配置（{len(declared_raw)} 站），不改写")
                sites = declared_raw or sites
                route_by_site = {str(s.get("site_code")): str(s.get("datasource") or "") for s in sites}

                # ② 各站数据产品行（保留原能力：产品是按站登记的治理身份）
                for code, target in route_by_site.items():
                    if not target or target not in ds_id:
                        continue
                    for obj_key, group in by_object.items():
                        contract = next((g for g in group
                                         if str(g.get("datasource_name") or "") == target), group[0])
                        table = str(contract.get("physical_table") or "")
                        if not table:
                            continue
                        product_name = f"{target}.{table}"
                        cur.execute("SELECT id FROM adh_data_products WHERE product_name = %s",
                                    (product_name,))
                        if cur.fetchone():
                            continue
                        plan_products.append((
                            product_name, table, site_names.get(code, code), target, table,
                            f"ontology:{mname}", now, now,
                        ))

                # ③/④ 绑定行收敛：契约行 = 本站路由目标行；同构副本置 deprecated
                for obj_key, group in by_object.items():
                    if len(group) <= 1:
                        continue
                    contract = None
                    for code, target in route_by_site.items():
                        if not target:
                            continue
                        hit = next((g for g in group if str(g.get("datasource_name") or "") == target), None)
                        if hit is not None:
                            contract = hit
                            break
                    if contract is None:
                        contract = group[0]   # 未声明路由目标：以最早行为契约行
                    shape = _norm_shape(contract)
                    for r in group:
                        if r is contract:
                            continue
                        if _norm_shape(r) == shape:
                            plan_deprecate.append((int(r["id"]), obj_key,
                                                   str(r.get("datasource_name") or ""), mname))
                        else:
                            exceptions.append((mname, obj_key, str(r.get("datasource_name") or ""),
                                               str(r.get("physical_table") or "")))

            print(f"\n计划：产品行 +{len(plan_products)}，绑定行置 deprecated {len(plan_deprecate)}，"
                  f"不同构例外 {len(exceptions)}")
            if exceptions:
                print("\n[例外清单] 以下站点行与契约行形状不同，保留 active，"
                      "请按「为该站单独配一个本体或使用源本体」处置：")
                for mname, obj_key, ds, table in exceptions:
                    print(f"  - {mname} / {obj_key} @ {ds} → {table}")
            if not apply:
                print("\n[dry-run] 加 --apply 落库")
                return 0

            for p in plan_products:
                cur.execute(
                    "INSERT INTO adh_data_products "
                    "(product_name, product_class, site, datasource_name, physical_table, "
                    "schema_hash, classification, producer_kind, producer_ref, status, "
                    "created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, '', 'internal', 'ontology', %s, 'draft', %s, %s)",
                    (p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7]))
            for row_id, obj_key, ds, _mname in plan_deprecate:
                cur.execute(
                    "UPDATE adh_ontology_bindings SET status = 'deprecated', updated_at = %s "
                    "WHERE id = %s AND status = 'active'", (now, row_id))
        conn.commit()
        print(f"\n[apply] 产品行 +{len(plan_products)}，deprecated {len(plan_deprecate)} 行"
              f"（一份契约 × 模型级站点已生效）")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true", help="落库（默认 dry-run）")
    p.add_argument("--model", type=int, default=0, help="只处理指定模型 id")
    args = p.parse_args()
    return run(apply=args.apply, only_model=args.model)


if __name__ == "__main__":
    raise SystemExit(main())
