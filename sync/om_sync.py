"""
AI-DataHub OpenMetadata 元数据回灌
从 OpenMetadata REST API 拉取已采集的表/字段元数据，单向写回本地
adh_table_info / adh_column_metadata，供 datamind RAG 链路使用（零改动）。

规则:
- 只更新系统字段（table_comment / column_comment / data_type / is_key），
  用户填写的 table_business_desc / business_desc 一律保留。
- 新表自动插入（embedding 置空，由 rebuild_vectors 重建）。
- OM 中已消失的表/字段做软删除（is_active=0），不物理删除。
- OM_ENABLED=false 时直接跳过，现有功能不受影响。

Usage:
    python -m sync.om_sync [--full|--incremental] [--no-rebuild]
"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.shared.common.config import (  # noqa: E402  (同时加载 services/.env)
    METADATA_DB_DATABASE,
)
from services.shared.common.db import get_metadata_conn  # noqa: E402

OM_URL = os.getenv("OM_SERVER_URL", "http://localhost:8585").rstrip("/")
OM_ENABLED = os.getenv("OM_ENABLED", "false").strip().lower() in ("1", "true", "yes")

# 参与回灌的 OM 服务名（逗号分隔）→ 本地 datasource_id 映射
DEFAULT_SERVICE_MAP = "adh_doris:0,adh_mysql:0"
SERVICE_MAP = {}
for pair in os.getenv("OM_DATASOURCE_MAP", DEFAULT_SERVICE_MAP).split(","):
    pair = pair.strip()
    if not pair:
        continue
    name, _, ds_id = pair.partition(":")
    SERVICE_MAP[name.strip()] = int(ds_id.strip() or 0)

# 表级 tag 兜底推导（region/domain），沿用旧 metadata_sync 的规则
try:
    from sync.metadata_sync import extract_region_tag, extract_domain_tag
except Exception:  # 旧脚本不可用时退化为常量
    def extract_region_tag(table_name: str) -> str:
        return "all"

    def extract_domain_tag(table_name: str) -> str:
        return "other"


# ── OM REST 客户端 ──────────────────────────────────────────────────

def _om_request(path: str, token: str):
    req = urllib.request.Request(f"{OM_URL}{path}")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"OM API {path} 失败: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:300]}")


def _iter_tables(token: str):
    """分页遍历 OM 全部 table 实体。"""
    after = ""
    while True:
        qs = f"/api/v1/tables?limit=100&fields=description,tags"
        if after:
            qs += f"&after={after}"
        page = _om_request(qs, token)
        for t in page.get("data", []):
            yield t
        paging = page.get("paging", {})
        after = paging.get("after", "")
        if not after:
            break


def _get_description(desc) -> str:
    """OM description 可能是 str 或 {markdown, text}。"""
    if not desc:
        return ""
    if isinstance(desc, dict):
        return (desc.get("text") or "").strip()
    return str(desc).strip()


def _split_fqn(fqn: str):
    """service.database[.schema].table → (service, table_name)。"""
    parts = fqn.split(".")
    return (parts[0] if parts else "", parts[-1] if parts else fqn)


def _fetch_tables_by_service(token: str) -> dict:
    """返回 {(service, datasource_id): [table_entity,...]}，仅保留配置中的服务。"""
    grouped = {}
    for t in _iter_tables(token):
        service, table_name = _split_fqn(t.get("fullyQualifiedName", ""))
        if service not in SERVICE_MAP:
            continue
        grouped.setdefault(service, []).append(t)
    return grouped


def _fetch_columns(token: str, table_id: str) -> list:
    detail = _om_request(f"/api/v1/tables/{table_id}?fields=columns", token)
    return detail.get("columns") or []


# ── 回灌 ────────────────────────────────────────────────────────────

def _sync_tables(conn, ds_id: int, om_tables: list, token: str, now: str) -> dict:
    """同步单个数据源下的表与字段，返回统计。"""
    stats = {"inserted": 0, "updated": 0, "unchanged": 0, "soft_deleted": 0}

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, table_name, table_comment, table_business_desc, keywords, "
            "region_tag, domain_tag, is_active FROM adh_table_info WHERE datasource_id = %s",
            (ds_id,),
        )
        existing_tables = {r["table_name"]: r for r in cur.fetchall()}

    om_table_names = set()
    for idx, t in enumerate(om_tables, 1):
        service, table_name = _split_fqn(t.get("fullyQualifiedName", ""))
        om_table_names.add(table_name)
        table_comment = _get_description(t.get("description"))[:512]
        om_tags = [tag.get("tagFQN", "").split(".")[-1] for tag in (t.get("tags") or [])]

        old = existing_tables.get(table_name)
        if old:
            changed = (
                (old.get("table_comment") or "") != table_comment
                or (old.get("is_active") or 0) != 1
                or (om_tags and (old.get("keywords") or "") != ",".join(om_tags))
            )
            if changed:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE adh_table_info SET table_comment=%s, keywords=%s, "
                        "is_active=1, sync_time=%s WHERE id=%s",
                        (
                            table_comment,
                            ",".join(om_tags) if om_tags else (old.get("keywords") or ""),
                            now, old["id"],
                        ),
                    )
                stats["updated"] += 1
            else:
                stats["unchanged"] += 1
            table_id_local = old["id"]
        else:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO adh_table_info "
                    "(datasource_id, table_name, table_comment, table_business_desc, "
                    " keywords, region_tag, domain_tag, is_active, sync_time) "
                    "VALUES (%s,%s,%s,'',%s,%s,%s,1,%s)",
                    (
                        ds_id, table_name, table_comment,
                        ",".join(om_tags),
                        extract_region_tag(table_name), extract_domain_tag(table_name),
                        now,
                    ),
                )
                table_id_local = cur.lastrowid
            stats["inserted"] += 1

        _sync_columns(conn, ds_id, table_name, _fetch_columns(token, t["id"]), now, stats)

        if idx % 20 == 0:
            print(f"    ... {idx}/{len(om_tables)} 表已处理")
            conn.commit()

    # 软删除：本地存在但 OM 已不存在的表
    with conn.cursor() as cur:
        for tname, row in existing_tables.items():
            if tname not in om_table_names and (row.get("is_active") or 0) == 1:
                cur.execute(
                    "UPDATE adh_table_info SET is_active=0, sync_time=%s WHERE id=%s",
                    (now, row["id"]),
                )
                cur.execute(
                    "UPDATE adh_column_metadata SET is_active=0, sync_time=%s "
                    "WHERE datasource_id=%s AND table_name=%s AND is_active=1",
                    (now, ds_id, tname),
                )
                stats["soft_deleted"] += 1
    conn.commit()
    return stats


def _sync_columns(conn, ds_id: int, table_name: str, om_columns: list, now: str, stats: dict):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, column_name, data_type, column_comment, business_desc, is_key, is_active "
            "FROM adh_column_metadata WHERE datasource_id=%s AND table_name=%s",
            (ds_id, table_name),
        )
        existing = {r["column_name"]: r for r in cur.fetchall()}

    om_col_names = set()
    for col in om_columns:
        cname = col.get("name")
        if not cname:
            continue
        om_col_names.add(cname)
        data_type = (col.get("dataType") or "")[:64]
        column_comment = _get_description(col.get("description"))[:512]
        is_key = "true" if col.get("constraint") == "PRIMARY_KEY" else "false"

        old = existing.get(cname)
        if old:
            changed = (
                (old.get("data_type") or "") != data_type
                or (old.get("column_comment") or "") != column_comment
                or (old.get("is_key") or "false") != is_key
                or (old.get("is_active") or 0) != 1
            )
            if changed:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE adh_column_metadata SET data_type=%s, column_comment=%s, "
                        "is_key=%s, is_active=1, sync_time=%s WHERE id=%s",
                        (data_type, column_comment, is_key, now, old["id"]),
                    )
        else:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO adh_column_metadata "
                    "(datasource_id, table_name, column_name, data_type, column_comment, "
                    " business_desc, is_key, is_nullable, is_active, sync_time) "
                    "VALUES (%s,%s,%s,%s,%s,'',%s,'YES',1,%s)",
                    (ds_id, table_name, cname, data_type, column_comment, is_key, now),
                )

    # 软删除消失的字段
    with conn.cursor() as cur:
        for cname, row in existing.items():
            if cname not in om_col_names and (row.get("is_active") or 0) == 1:
                cur.execute(
                    "UPDATE adh_column_metadata SET is_active=0, sync_time=%s WHERE id=%s",
                    (now, row["id"]),
                )


def main():
    mode = "--incremental" if "--incremental" in sys.argv else "--full"
    do_rebuild = "--no-rebuild" not in sys.argv

    if not OM_ENABLED:
        print("[om_sync] OM_ENABLED != true，跳过回灌。")
        return

    token = os.getenv("OM_AUTH_TOKEN", "").strip()
    if not token:
        print("[om_sync] 缺少 OM_AUTH_TOKEN。请先执行 docker/om/init_om.py 生成并回写 token。")
        sys.exit(1)

    print(f"[om_sync] 模式={mode} OM={OM_URL} 服务映射={SERVICE_MAP}")
    grouped = _fetch_tables_by_service(token)
    if not grouped:
        print("[om_sync] 未拉取到任何配置服务的表，请确认 OM 已完成采集 (init_om.py --trigger)。")
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = {"inserted": 0, "updated": 0, "unchanged": 0, "soft_deleted": 0}
    for service, tables in grouped.items():
        ds_id = SERVICE_MAP[service]
        print(f"  [om_sync] 服务 {service} → datasource_id={ds_id}，共 {len(tables)} 张表")
        with get_metadata_conn() as conn:
            stats = _sync_tables(conn, ds_id, tables, token, now)
        for k in total:
            total[k] += stats[k]

    print(f"[om_sync] 完成: 新增 {total['inserted']}，更新 {total['updated']}，"
          f"未变 {total['unchanged']}，软删除 {total['soft_deleted']}")

    if do_rebuild:
        print("[om_sync] 重建 RAG 向量 (sync.rebuild_vectors) ...")
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        result = subprocess.run(
            [sys.executable, "-m", "sync.rebuild_vectors"],
            cwd=project_root,
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print("[om_sync] 向量重建完成")
        else:
            print(f"[om_sync] 向量重建失败（可手动重试）:\n{result.stderr[-800:]}")
            sys.exit(2)


if __name__ == "__main__":
    main()
