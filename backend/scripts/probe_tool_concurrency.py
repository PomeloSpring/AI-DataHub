"""**工具调用并发探针** —— 为「多站点并行取数为什么变成串行」取证。

背景（会话 1783069459151 实测）：模型在一轮里同时发起 5 站 `execute_sql`，
`[ExecLayer] tool start` 五跳都在 10:29:01 同一秒，结果却是 2.5 / 6.9 / 11.6 / 17.5 /
20.1 秒**阶梯式**返回，末位完成时刻 ≈ 五者内层耗时之和(13.9s) + 建连开销 ——
说明「并行发起」在链路上被串行化了。整轮 61 条 execute_sql 的内层平均 3.3s、
外层平均 12.3s（每条多花 9.0s），三轮撞 300s 墙钟都与此相关。

本探针绕过 SDK/MCP 分发层，**直接并发调用工具 handler**，把串行点夹出来：
  · handler 层并发有效（并发墙钟 ≈ max 单条）→ 串行化在 qoder CLI/MCP 的工具分发层，
    本仓侧解法是「一次调用内服务端扇出」（计划 C2），而不是让模型少写查询；
  · handler 层也串行（并发墙钟 ≈ Σ 单条）→ 串行点在本仓执行面（`asyncio.to_thread`
    线程池上限 / 元数据池 / 每查询新建跨区连接），就地修（计划 C3）。

安全性：只跑聚合计数（COUNT），经统一治理入口 `execute_sql` 执行（护栏 §1）并正常
落查询审计（护栏 §9）；不取任何业务明细行。需 backend/.env 指向的环境能连通各站 SLS 端点。

用法：
    venv/bin/python -m backend.scripts.probe_tool_concurrency [源业务名 ...]
    # 缺省 prd-sls-cn prd-sls-en prd-sls-eu prd-sls-uk prd-sls-jp
"""
from __future__ import annotations

import asyncio
import sys
import time

DEFAULT_SOURCES = ["prd-sls-cn", "prd-sls-en", "prd-sls-eu", "prd-sls-uk", "prd-sls-jp"]
SQL_TEMPLATE = ("SELECT COUNT(*) AS n FROM observability_rum "
                "WHERE __time__ >= to_unixtime(now()) - {secs}")
WINDOW_SECS = 3600


def _resolve_targets(names: list[str]) -> list[tuple[str, int]]:
    from backend.common.db.datasource_db import get_datasource_by_name

    out = []
    for name in names:
        ds = get_datasource_by_name(name)
        if not ds:
            print(f"[probe] 跳过不存在的数据源: {name}")
            continue
        if str(ds.get("db_type") or "").lower() != "sls":
            print(f"[probe] 跳过非 SLS 源: {name}(db_type={ds.get('db_type')})")
            continue
        out.append((name, int(ds["id"])))
    return out


def _identity() -> dict:
    """探针以真实管理员身份执行（护栏 §2：无可信身份不得取数）。"""
    from backend.common.db.metadata_db import get_metadata_conn
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, username, user_role FROM adh_users "
                        "WHERE user_role='admin' ORDER BY id LIMIT 1")
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise SystemExit("[probe] 找不到管理员身份，拒绝无身份取数")
    return row


def _make_ctx(admin: dict, datasource_ids: list[int]):
    from backend.modules.mind.execution.models import ExecutionContext
    return ExecutionContext(
        user_id=int(admin["id"]), username=str(admin["username"]),
        user_role=str(admin.get("user_role") or "admin"),
        workspace_id=0, datasource_id=datasource_ids[0],
        extra={"available_datasource_ids": datasource_ids,
               "question": "[probe] tool concurrency"})


async def _one_call(args: dict, ctx) -> tuple[str, float, str]:
    """在本任务上下文里跑一次 execute_sql，返回 (源名, 毫秒, 结果摘要)。"""
    from backend.modules.mind.execution.sdk_tools import query_tools as qt
    from backend.modules.mind.execution.sdk_tools.context import (
        ExecutionContextVar, set_execution_context)

    token = set_execution_context(ctx)
    t0 = time.monotonic()
    try:
        res = await qt.execute_sql(dict(args))
    finally:
        ExecutionContextVar.reset(token)
    ms = (time.monotonic() - t0) * 1000
    text = res["content"][0]["text"] if isinstance(res, dict) else str(res)
    note = ("ERROR " + text[:200]) if (isinstance(res, dict) and res.get("isError")) \
        else text[:70].replace("\n", " ")
    return str(args.get("datasource")), ms, note


async def main() -> int:
    names = sys.argv[1:] or DEFAULT_SOURCES
    targets = _resolve_targets(list(dict.fromkeys(names)))
    if not targets:
        print("[probe] 无可探测的 SLS 数据源")
        return 1
    admin = _identity()
    ds_ids = [ds_id for _n, ds_id in targets]
    print(f"[probe] 目标={[n for n, _ in targets]}  身份=admin/{admin['username']}  "
          f"SQL=COUNT(*) 窗口={WINDOW_SECS}s")

    calls = [{"sql": SQL_TEMPLATE.format(secs=WINDOW_SECS), "datasource": n} for n, _ in targets]

    # ── 串行基线 ─────────────────────────────────────────────────
    ctx = _make_ctx(admin, ds_ids)
    t0 = time.monotonic()
    serial = [await _one_call(a, ctx) for a in calls]
    serial_wall = time.monotonic() - t0
    print("\n[串行] 逐条执行：")
    for name, ms, note in serial:
        print(f"  {name:<12} {ms / 1000:>6.2f}s  {note}")
    print(f"  串行墙钟 = {serial_wall:.2f}s   单条之和 = "
          f"{sum(m for _n, m, _x in serial) / 1000:.2f}s")

    # ── 并发（等价于模型在一轮里同时发出 N 条工具调用）────────────
    t0 = time.monotonic()
    concurrent = await asyncio.gather(*[_one_call(a, _make_ctx(admin, ds_ids)) for a in calls])
    conc_wall = time.monotonic() - t0
    worst = max(m for _n, m, _x in concurrent) / 1000
    print("\n[并发] asyncio.gather 同时执行：")
    for name, ms, note in concurrent:
        print(f"  {name:<12} {ms / 1000:>6.2f}s  {note}")
    print(f"  并发墙钟 = {conc_wall:.2f}s   单条最大 = {worst:.2f}s")

    print(f"\n[判读] 并发墙钟/串行墙钟 = {(conc_wall / serial_wall if serial_wall else 0):.2f}")
    if conc_wall <= worst + 2:
        print("  → handler 层并发有效：串行化发生在 SDK/MCP 工具分发层（计划 C2：服务端扇出）")
    elif conc_wall >= serial_wall * 0.85:
        print("  → handler 层也是串行：串行点在本仓执行面（线程池上限/连接新建），按 C3 修")
    else:
        print("  → 部分并发：仍有共享瓶颈（每查询新建跨区连接或线程池上限），按 C3 逐项收窄")
    return 0


if __name__ == "__main__":
    asyncio.run(main())
