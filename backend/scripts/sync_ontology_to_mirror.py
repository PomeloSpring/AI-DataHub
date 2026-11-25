"""把本项目主库的「本体域资产」同步到镜像库（默认 8.133.196.68:3306/app）。

方向：主库 -> 镜像库，只写**内容真的不同**的行，幂等可重复执行。

同步范围（本体域，配置型资产）：
  adh_ontology_models       — canonical doc（json_content）
  adh_ontology_objects      — 对象展开行（按 model_id+object_key 对齐，两边 id 常不同）
  adh_ontology_bindings     — 对象物理绑定（column_map/product_ref/execution_binding）
  adh_metrics / adh_dimensions — 指标、维度字典（增量行）
  adh_data_products         — 数据产品登记（本体对象物理表的认领行）

**不同步**运行时数据与会话/审计（adh_conversations、adh_audit_logs、adh_llm_*、
adh_query_audit、adh_content_* 等）与权限配置（adh_role_perms 等）：那些表在两个环境
各有独立写入，全表覆盖会抹掉镜像环境自己的运行记录。如需同步权限/菜单配置，另行确认。

凭据：镜像库密码只从环境变量 MIRROR_DB_PASSWORD 读取，不落盘、不回显。

用法：
  export MIRROR_DB_PASSWORD=***            # 必填，缺失直接报错
  env PYTHONPATH=. venv/bin/python backend/scripts/sync_ontology_to_mirror.py           # dry-run
  env PYTHONPATH=. venv/bin/python backend/scripts/sync_ontology_to_mirror.py --apply   # 执行
  # 可选：--host / --port / --user / --database / --backup-dir
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pymysql

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.common.config import (  # noqa: E402
    METADATA_DB_DATABASE,
    METADATA_DB_HOST,
    METADATA_DB_PASSWORD,
    METADATA_DB_PORT,
    METADATA_DB_USER,
)

# 同步范围：本体域配置资产
TABLES_BY_ID = (
    "adh_ontology_models",
    "adh_ontology_bindings",
    "adh_metrics",
    "adh_dimensions",
    "adh_data_products",
)
# objects 表两侧 id 常因各自重展开而不同 -> 按 (model_id, object_key) 对齐
OBJECTS_TABLE = "adh_ontology_objects"
OBJECTS_KEY = ("model_id", "object_key")
# 仅时间戳：判定"内容是否不同"时排除（内容相同则不写，保留镜像侧原时间）
TIME_COLS = {"create_time", "update_time", "created_at", "updated_at", "last_verified_at"}

MODEL_NAMES = {
    1789467214865307: "联耀业务本体",
    1792410799803865: "官网可观测本体",
    1793775073151614: "应用使用事件本体",
    1795601851214052: "官网业务本体",
}


def _connect(host: str, port: int, user: str, password: str, database: str):
    return pymysql.connect(
        host=host, port=port, user=user, password=password, database=database,
        charset="utf8mb4", connect_timeout=10, autocommit=False,
    )


def _cols(conn, table: str) -> list[str]:
    with conn.cursor() as c:
        c.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=DATABASE() AND table_name=%s ORDER BY ordinal_position",
            (table,),
        )
        return [r[0] for r in c.fetchall()]


def _rows(conn, table: str, cols: list[str]) -> list[dict[str, Any]]:
    sql = "SELECT " + ", ".join(f"`{c}`" for c in cols) + f" FROM `{table}`"
    with conn.cursor(pymysql.cursors.DictCursor) as c:
        c.execute(sql)
        return list(c.fetchall())


def _norm(v: Any) -> str:
    if v is None:
        return "\x00NULL"
    if isinstance(v, bytes):
        return "0x" + v.hex()
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return format(v, "f")
    return str(v)


def _content_differs(a: dict, b: dict, cols: list[str]) -> list[str]:
    """返回内容不同的列（排除纯时间列）。"""
    out = []
    for c in cols:
        if c in TIME_COLS:
            continue
        if _norm(a.get(c)) != _norm(b.get(c)):
            out.append(c)
    return out


def _upsert_sql(table: str, cols: list[str], key: str = "id") -> str:
    ph = ", ".join(["%s"] * len(cols))
    sets = ", ".join(f"`{c}`=VALUES(`{c}`)" for c in cols if c != key)
    return (
        f"INSERT INTO `{table}` (" + ", ".join(f"`{c}`" for c in cols) + f") VALUES ({ph}) "
        f"ON DUPLICATE KEY UPDATE {sets}"
    )


def _update_sql(table: str, cols: list[str], where: str) -> str:
    sets = ", ".join(f"`{c}`=%s" for c in cols)
    return f"UPDATE `{table}` SET {sets} WHERE {where}"


def _jsonable(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        elif isinstance(v, bytes):
            out[k] = {"__bytes_hex__": v.hex()}
        elif isinstance(v, Decimal):
            out[k] = str(v)
        else:
            out[k] = v
    return out


def plan_and_apply(L, R, apply: bool, backup_dir: str) -> dict:
    summary: dict[str, Any] = {"tables": {}, "warnings": []}
    backups: dict[str, list] = {}

    # ── 1) 按主键 id 对齐的表 ─────────────────────────────────────
    for table in TABLES_BY_ID:
        lcols, rcols = _cols(L, table), _cols(R, table)
        if lcols != rcols:
            raise RuntimeError(
                f"[{table}] 两侧列结构不一致，拒绝同步：本库独有={sorted(set(lcols)-set(rcols))} "
                f"镜像独有={sorted(set(rcols)-set(lcols))}")
        lrows = {r["id"]: r for r in _rows(L, table, lcols)}
        rrows = {r["id"]: r for r in _rows(R, table, lcols)}
        only_l = sorted(set(lrows) - set(rrows))
        only_r = sorted(set(rrows) - set(lrows))
        changed = [i for i in sorted(set(lrows) & set(rrows))
                   if _content_differs(lrows[i], rrows[i], lcols)]
        summary["tables"][table] = {
            "local": len(lrows), "mirror": len(rrows),
            "insert": len(only_l), "update": len(changed),
            "mirror_only": len(only_r), "insert_ids": only_l[:10], "update_ids": changed[:10],
        }
        if only_r:
            summary["warnings"].append(
                f"{table}: 镜像库有 {len(only_r)} 行本库没有（id 示例 {only_r[:5]}），"
                "按镜像同步口径不删除，请人工确认")
        if not apply:
            continue
        backups[table] = [_jsonable(rrows[i]) for i in changed if i in rrows]
        with R.cursor() as c:
            if only_l:
                c.executemany(_upsert_sql(table, lcols), [tuple(lrows[i][x] for x in lcols) for i in only_l])
            for i in changed:
                # 只覆盖内容不同的行：整行（含时间列）按本库写，保持镜像与本库一致
                c.execute(_update_sql(table, lcols, "id=%s"),
                          tuple(lrows[i][x] for x in lcols) + (i,))

    # ── 2) objects：按 (model_id, object_key) 对齐 ────────────────
    lcols = _cols(L, OBJECTS_TABLE)
    rcols = _cols(R, OBJECTS_TABLE)
    if lcols != rcols:
        raise RuntimeError(f"[{OBJECTS_TABLE}] 两侧列结构不一致，拒绝同步")
    def _key(r): return tuple(r[k] for k in OBJECTS_KEY)
    lrows = {_key(r): r for r in _rows(L, OBJECTS_TABLE, lcols)}
    rrows = {_key(r): r for r in _rows(R, OBJECTS_TABLE, lcols)}
    only_l = sorted(set(lrows) - set(rrows), key=str)
    only_r = sorted(set(rrows) - set(lrows), key=str)
    changed = [k for k in sorted(set(lrows) & set(rrows), key=str)
               if _content_differs(lrows[k], rrows[k], lcols)]
    summary["tables"][OBJECTS_TABLE] = {
        "local": len(lrows), "mirror": len(rrows),
        "insert": len(only_l), "update": len(changed),
        "mirror_only": len(only_r),
        "update_keys": [f"{k[0]}/{k[1]}" for k in changed[:10]],
    }
    if only_r:
        summary["warnings"].append(
            f"{OBJECTS_TABLE}: 镜像库有 {len(only_r)} 个对象键本库没有，未删除：{only_r[:5]}")
    if apply:
        backups[OBJECTS_TABLE] = [_jsonable(rrows[k]) for k in changed]
        upd_cols = [c for c in lcols if c not in OBJECTS_KEY]
        with R.cursor() as c:
            for k in only_l:
                c.execute(_upsert_sql(OBJECTS_TABLE, lcols), tuple(lrows[k][x] for x in lcols))
            for k in changed:
                c.execute(
                    f"UPDATE `{OBJECTS_TABLE}` SET " + ", ".join(f"`{x}`=%s" for x in upd_cols)
                    + " WHERE model_id=%s AND object_key=%s",
                    tuple(lrows[k][x] for x in upd_cols) + (k[0], k[1]))

    if apply and backup_dir:
        os.makedirs(backup_dir, exist_ok=True)
        path = os.path.join(backup_dir, "mirror_ontology_backup_%s.json"
                            % datetime.now().strftime("%Y%m%d%H%M%S"))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(backups, f, ensure_ascii=False)
        summary["backup"] = path
    return summary


def _verify(L, R) -> dict:
    """同步后自检：镜像侧关键口径与本库对齐。"""
    out: dict[str, Any] = {}

    def one(conn, sql, args=None):
        with conn.cursor() as c:
            c.execute(sql, args or ())
            return c.fetchone()

    for t in ("adh_ontology_models", "adh_ontology_objects", "adh_ontology_bindings",
              "adh_metrics", "adh_dimensions", "adh_data_products"):
        out[t] = {"local": one(L, f"SELECT COUNT(*) FROM `{t}`")[0],
                  "mirror": one(R, f"SELECT COUNT(*) FROM `{t}`")[0]}

    for mid in MODEL_NAMES:
        def stat(conn):
            row = one(conn, "SELECT json_content FROM adh_ontology_models WHERE id=%s", (mid,))
            if not row or not row[0]:
                return None
            d = json.loads(row[0])
            objs = d.get("objects") or []
            return {
                "objects": len(objs),
                "properties": sum(len(o.get("properties") or []) for o in objs),
                "top_metrics": len(d.get("metrics") or []),
                "top_dimensions": len(d.get("dimensions") or []),
            }
        out[f"model:{mid}"] = {"local": stat(L), "mirror": stat(R)}

    # 绑定列映射是否带到镜像（联耀 user 对象抽查 create_by）
    for conn_name, conn in (("local", L), ("mirror", R)):
        row = one(conn, "SELECT column_map FROM adh_ontology_bindings "
                        "WHERE model_id=%s AND object_key='user' "
                        "ORDER BY id DESC LIMIT 1", (1789467214865307,))
        cm = row[0] if row and row[0] else "{}"
        out.setdefault("binding_user_column_map", {})[conn_name] = json.loads(cm or "{}")

    for conn_name, conn in (("local", L), ("mirror", R)):
        out.setdefault("dict_counts", {})[conn_name] = {
            "metrics": one(conn, "SELECT COUNT(*) FROM adh_metrics WHERE is_active=1")[0],
            "dimensions": one(conn, "SELECT COUNT(*) FROM adh_dimensions WHERE is_active=1")[0],
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="本体域资产：主库 -> 镜像库 同步")
    ap.add_argument("--host", default=os.getenv("MIRROR_DB_HOST", "8.133.196.68"))
    ap.add_argument("--port", type=int, default=int(os.getenv("MIRROR_DB_PORT", "3306")))
    ap.add_argument("--user", default=os.getenv("MIRROR_DB_USER", "app"))
    ap.add_argument("--database", default=os.getenv("MIRROR_DB_DATABASE", "app"))
    ap.add_argument("--backup-dir", default=os.getenv("MIRROR_BACKUP_DIR", "/tmp"))
    ap.add_argument("--apply", action="store_true", help="实际写库（默认 dry-run）")
    args = ap.parse_args()

    pw = os.getenv("MIRROR_DB_PASSWORD", "")
    if not pw:
        print("[FATAL] 未提供镜像库密码：请设置 MIRROR_DB_PASSWORD 环境变量（不从命令行/文件读取）")
        return 2

    L = _connect(METADATA_DB_HOST, METADATA_DB_PORT, METADATA_DB_USER, METADATA_DB_PASSWORD,
                 METADATA_DB_DATABASE)
    R = _connect(args.host, args.port, args.user, pw, args.database)
    print(f"[info] 主库 {METADATA_DB_HOST}:{METADATA_DB_PORT}/{METADATA_DB_DATABASE} "
          f"-> 镜像 {args.host}:{args.port}/{args.database}  "
          f"模式={'APPLY' if args.apply else 'DRY-RUN'}")

    try:
        summary = plan_and_apply(L, R, args.apply, args.backup_dir)
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        if args.apply:
            R.commit()
            print("[ok] 已提交；开始自检")
            print(json.dumps(_verify(L, R), ensure_ascii=False, indent=2, default=str))
        else:
            R.rollback()
            print("[dry-run] 未写库。确认差异后加 --apply 执行。")
        return 0
    except Exception as e:  # noqa: BLE001 — 失败必须回滚并暴露原因，不静默吞
        R.rollback()
        print(f"[FATAL] 同步失败已回滚：{type(e).__name__}: {e}")
        return 1
    finally:
        L.close()
        R.close()


if __name__ == "__main__":
    sys.exit(main())
