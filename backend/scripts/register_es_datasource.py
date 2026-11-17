"""ES 数据源登记器（ES 数据源化 · 阶段一）：把内置 MCP 通道的 ES 配置落成一条 ES 型数据源行。

为什么用脚本而不是迁移 SQL：凭据（口令 / API Key）**不得进 git**（护栏 §7：凭据只在服务端），
而 ES 源行必须带凭据才能测连与取数。本脚本从 `backend/.env` 读现有内置通道配置，
经服务层 `encrypt_password` 落库；迁移文件 `docker/mysql/es_datasource_rls_migration.sql`
只加 `extra_config` 列，不含任何凭据。

做四件事（幂等，默认 dry-run）：
  ① 组开放范围：四个索引别名 ∪ `.env` 现有 `t-别名*` 模式（**并集口径**，拍板 2026-11-13：
     两种写法都合法，同时保住"丢掉 `t-` 前缀算扩权"的旧护栏）；
  ② 建/更新数据源行（`db_type='es'`，名称全局唯一即业务标识）；
  ③ 角色授权：给三个系统角色（admin/analyst/viewer）+ 现有 ES 数据集 owner 所属角色
     + 被直授了含 ES 图表看板的角色补授权 —— 维持迁移前入口可见现状（MCP 通道今天
     不受数据源授权裁决）；
  ④ 核验：实跑一次测连（拍板口径：能查到数据就行，不报索引数量、不报集群地址）。

用法（repo 根目录）:
  venv/bin/python backend/scripts/register_es_datasource.py                 # dry-run 看计划
  venv/bin/python backend/scripts/register_es_datasource.py --apply
  venv/bin/python backend/scripts/register_es_datasource.py --name cloud-platform-es --apply
  venv/bin/python backend/scripts/register_es_datasource.py --grant-only --apply   # 只补角色授权，不重写源行
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)

from backend.common.db.metadata_db import get_metadata_conn  # noqa: E402


def _legacy(name: str, default: str = "") -> str:
    """旧内置通道的 `.env` 变量（本脚本专用）。

    阶段三第 2 步已把 ES 内置通道从产品里拆除，`backend.common.config` 不再持有那组
    `ES_*` 配置（否则会留一条“界面改了没反应、变量在 .env 里”的双口径）。本脚本的职责
    就是把那份旧配置一次性落成一条 ES 数据源行，所以在此直接读环境变量：工具可用，
    而运行时的配置面只剩数据源那一个来源。
    """
    return os.getenv(name, default)

# 默认业务名：一个集群一条源（ES 索引跨全部 region，不按站点拆源 —— 站点归属留空）
DEFAULT_NAME = "cloud-platform-es"

# 开放范围里的四个索引别名（2026-11-13 拍板：`sys_thrid_request_log` 一并纳入）。
# 别名是实机校准过的稳定逻辑名：与 MySQL 站点库表名同名，因此行级策略/敏感字段登记
# 的表名键空间在两个引擎之间统一，不需要额外的映射表。
ALLOWED_ALIASES = (
    "t_software_app_operation_log",
    "t_equipment_operation_log",
    "t_equipment_warning_log",
    "sys_thrid_request_log",
)


def _log(msg: str) -> None:
    print(f"[es-ds] {msg}", flush=True)


def _next_id(seq: int) -> int:
    """微秒时间戳 + 序号（同一批多行插入时不能撞主键：id 无 auto_increment）。"""
    return int(time.time() * 1000000) + seq


def _build_extra() -> dict:
    """开放范围 = 别名 ∪ 现有模式（并集口径，与 `es_client.index_allowed` 同读一份）。"""
    patterns = sorted({str(p).strip() for p in _legacy("ES_ALLOWED_INDEXES").split(",") if str(p).strip()})
    return {
        "allowed_aliases": sorted(ALLOWED_ALIASES),
        "allowed_index_patterns": patterns,
        "timeout_sec": int(_legacy("ES_TIMEOUT_SEC", "20") or 20),
        "max_rows": int(_legacy("ES_MAX_ROWS", "1000") or 1000),
        "verify_certs": _legacy("ES_VERIFY_CERTS", "true").strip().lower() not in ("0", "false", "no", "off"),
    }


def _target_roles(cur) -> list[int]:
    """待授权角色 = 三个系统角色（admin/analyst/viewer）∪ 现有 ES 数据集 owner 的角色
    ∪ **今天能看 ES 图表的看板消费角色**。

    为什么给这三类：内置 MCP 通道今天**不受数据源授权裁决**（dataset.datasource_id=0），
    谁能建/查 ES 数据集只看数据集归属。迁成数据源后就会过 `role_service
    .get_user_allowed_datasources` 这道唯一裁决点 —— 不给存量角色补授权，就等于把
    今天能用的入口凭空调掉（menu-permission-sync §2-3 的存量默认授权同理）。

    第三类是 2026-11-13 阶段二补的：只看"数据集 owner"会漏掉**纯消费方**——
    自定义角色被直授了一张含 ES 图表的看板（今天图能出数，因为它不查数据源授权），
    迁移后取数要过裁决，漏授权的表现是看板图集体显式无权。
    判据只取"今天已经能看"这一事实（`adh_role_dashboard_access` ⋈ `adh_charts`），
    不引入新的授权维度；其余自定义角色仍按需由管理员在角色页授。
    """
    cur.execute(
        "SELECT DISTINCT ur.role_id FROM adh_datasets d "
        "JOIN adh_user_roles ur ON ur.user_id = d.owner_id "
        "WHERE d.source_type = 'es'")
    roles = {int(r["role_id"]) for r in (cur.fetchall() or [])}
    cur.execute(
        "SELECT DISTINCT r.role_id FROM adh_role_dashboard_access r "
        "JOIN adh_charts c ON c.dashboard_id = r.dashboard_id "
        "JOIN adh_datasets d ON d.id = c.source_id "
        "WHERE c.source_type = 'dataset' AND d.source_type = 'es'")
    roles.update(int(r["role_id"]) for r in (cur.fetchall() or []))
    cur.execute(
        "SELECT id FROM adh_roles WHERE is_system = 1 AND is_active = 1 ORDER BY id")
    roles.update(int(r["id"]) for r in (cur.fetchall() or []))
    return sorted(roles)


def _grant(cur, ds_id: int, role_ids: list[int], apply: bool) -> None:
    for i, role_id in enumerate(role_ids):
        cur.execute(
            "SELECT 1 FROM adh_role_datasource_access WHERE role_id = %s AND datasource_id = %s",
            (role_id, ds_id))
        if cur.fetchone():
            _log(f"[grant] role {role_id} 已授权该源")
            continue
        _log(f"[grant] {'补授权' if apply else '待补授权'}: role {role_id} → ds {ds_id}")
        if apply:
            # 显式带 id（表无 auto_increment）；uk_role_ds 保证重复跑不会产生第二行
            cur.execute(
                "INSERT IGNORE INTO adh_role_datasource_access (id, role_id, datasource_id, access_type) "
                "VALUES (%s, %s, %s, 'read')", (_next_id(i), role_id, ds_id))


async def _register(args) -> int:
    from backend.common.db import get_datasource_by_name
    from backend.modules.catalog.services.datasource_service import datasource_service as svc
    url = (_legacy("ES_URL").strip().rstrip("/"))
    if not url:
        _log("[abort] backend/.env 没有 ES_URL，无法登记（不猜地址）")
        return 2
    extra = _build_extra()

    existing = get_datasource_by_name(args.name)
    if existing:
        _action = f"仅补授权（不重写）id={existing.get('id')}" if args.grant_only \
            else f"更新已有源 id={existing.get('id')}"
    else:
        _action = "新建源"
    _log(f"[plan] 名称「{args.name}」→ {_action}")
    _log(f"[plan] 地址（不外泄，仅服务端）: {'已配置' if url else '缺失'}")
    _log(f"[plan] 开放范围 = 别名 {extra['allowed_aliases']} ∪ 模式 {extra['allowed_index_patterns']}")

    if args.grant_only and not existing:
        _log("[abort] --grant-only 要求源已存在（不新建），去掉该参数或先跑一次完整登记")
        return 2

    if not args.apply:
        # dry-run 也要把“要给哪些角色补授权”说出来：阶段二生效前需要确认存量消费
        # 角色不会被新裁决点掉，只看“源行计划”判不了这件事。
        with get_metadata_conn() as conn:
            with conn.cursor() as cur:
                target = _target_roles(cur)
                granted = []
                if existing:
                    cur.execute(
                        "SELECT role_id FROM adh_role_datasource_access WHERE datasource_id = %s",
                        (int(existing["id"]),))
                    granted = sorted(int(r["role_id"]) for r in (cur.fetchall() or []))
        _log(f"[dry-run] 待授权角色 = {target}；该源已授权 = {granted or '（无）'}；"
             f"需补 = {[r for r in target if r not in granted] or '（无）'}")
        _log("[dry-run] 未落库；加 --apply 执行")
        return 0

    owner_id = 0
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT owner_id FROM adh_datasets WHERE source_type='es' ORDER BY owner_id LIMIT 1")
            row = cur.fetchone()
            owner_id = int(row["owner_id"]) if row else 0

    if args.grant_only:
        # 只补授权：源行的 `extra_config` 可能已在数据源页被管理员改过（加别名/调预算），
        # 拿 .env 口径重写会把那些改动静默抹掉。授权与源定义是两件事，分开做。
        ds_id = int(existing["id"])
        _log(f"[grant-only] 不重写源行 id={ds_id}，仅校对角色授权")
    elif existing:
        await svc.update_datasource(int(existing["id"]), {
            "db_type": "es", "host": url, "port": 9200,
            "username": _legacy("ES_USERNAME"), "password": _legacy("ES_PASSWORD"),
            # 地址本身是 https，显式声明加密传输（与计划设计决定 2 一致）
            "ssl_mode": "required",
            "extra_config": json.dumps(extra, ensure_ascii=False),
        })
        ds_id = int(existing["id"])
    else:
        ds_id = int((await svc.create_datasource({
            "name": args.name, "db_type": "es", "host": url, "port": 9200,
            "username": _legacy("ES_USERNAME"), "password": _legacy("ES_PASSWORD"),
            "ssl_mode": "required",
            "extra_config": extra,
        }, owner_id=owner_id))["id"])
        _log(f"[create] 新源 id={ds_id}（owner 取现有 ES 数据集创建者 {owner_id}）")

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            _grant(cur, ds_id, _target_roles(cur), args.apply)
            conn.commit()

    result = await svc.test_connection(ds_id)
    _log(f"[verify] 测连 {'通过' if result.get('success') else '失败'}: {result.get('message')}")
    tables = await svc.list_tables(ds_id)
    # 表清单沿用 DB 分支的大写键形状（`TABLE_NAME`），不是业务字段名
    _log(f"[verify] 表清单（索引别名）: {[t.get('TABLE_NAME') for t in tables]}")
    return 0 if result.get("success") else 3


def main() -> int:
    parser = argparse.ArgumentParser(description="登记 ES 型数据源（阶段一，凭据从 .env 读入）")
    parser.add_argument("--name", default=DEFAULT_NAME, help="数据源业务名（全局唯一）")
    parser.add_argument("--apply", action="store_true", help="实际落库（缺省为 dry-run）")
    parser.add_argument("--grant-only", action="store_true",
                        help="只校对角色授权，不新建/重写数据源行（补存量消费角色时用）")
    args = parser.parse_args()
    return asyncio.run(_register(args))


if __name__ == "__main__":
    sys.exit(main())
