"""SLS(PG 协议) 端点**默认时间窗探针** —— 为「未写时间谓词时到底扫了多少数据」取证。

背景（会话 1783069459151 实测）：`execute_sql` 对 SLS 源写的 SQL 若不带 `__time__`
谓词，引擎按**自身默认时间窗**扫描，返回的 row_count 与 `truncated=false` 都看不出
被截断 —— 同一条 `SELECT COUNT(*) FROM observability_rum` 无谓词时只得 183 行/时间
跨度 781 秒，加 `__time__ >= to_unixtime(now()) - 31536000` 后是 22,056,836 行。
调用方（LLM）把"默认窗"当"全量"回答就是错数，比无答案更坏（no-silent-degradation §1）。

本探针把默认窗的**实际量级**测出来（与哪个显式窗口的行数对齐），作为
`query_tools._sls_time_window_notice` 告警文案与 `data-analysis/SKILL.md` 的依据 ——
本仓纪律是「探针实证」，不猜引擎默认值。

安全性：只跑聚合计数（COUNT/MIN/MAX），不取任何业务明细行；执行仍经统一治理入口
`semantics.execute.execute_sql`（护栏 §1），因此每条探针也会落查询审计（护栏 §9）。

用法：
    venv/bin/python -m backend.scripts.probe_sls_default_time_window [datasource_name] [table]

第一轮实测结论（prd-sls-cn / observability_rum，2026-11-04）：
  无 __time__ 谓词 = 37 行（时间跨度 10.8 分钟，最早距今 13.9 分钟）
  __time__ >= now()-900(15m) = 37 行  <== 与无谓词完全一致 → 默认窗 = 最近 15 分钟
  now()-3600(1h) = 356 | -86400(24h) = 53,517 | -604800(7d) = 834,506
  -2592000(30d) = 4,021,753 | -31536000(365d) = 22,080,802
  即：漏写 __time__ 谓词会把「一年」答成「15 分钟」，量级差 60 万倍。
"""
from __future__ import annotations

import sys
import time

WINDOWS: list[tuple[str, int]] = [
    ("5m", 300),
    ("15m", 900),
    ("30m", 1800),
    ("1h", 3600),
    ("6h", 21600),
    ("24h", 86400),
    ("7d", 604800),
    ("30d", 2592000),
    ("365d", 31536000),
]


def _run(sql: str, datasource_id: int, user_id: int, username: str) -> dict:
    from backend.semantics.execute import PolicyContext, execute_sql

    res = execute_sql(sql, PolicyContext(
        user_id=user_id, username=username, workspace_id=0,
        datasource_id=datasource_id, question="[probe] sls_default_time_window"))
    names = [c.name for c in res.columns]
    if not res.rows:
        return {}
    return dict(zip(names, res.rows[0]))


def main() -> int:
    ds_name = (sys.argv[1] if len(sys.argv) > 1 else "prd-sls-cn")
    table = (sys.argv[2] if len(sys.argv) > 2 else "observability_rum")

    from backend.common.db.datasource_db import get_datasource_by_name

    ds = get_datasource_by_name(ds_name)
    if not ds:
        print(f"[probe] 数据源不存在: {ds_name}")
        return 1
    if str(ds.get("db_type") or "").lower() != "sls":
        print(f"[probe] {ds_name} 不是 SLS 源(db_type={ds.get('db_type')})")
        return 1
    ds_id = int(ds["id"])

    # 探针以真实管理员身份执行（护栏 §2 身份 fail-closed：无可信身份不得取数）
    from backend.common.db.metadata_db import get_metadata_conn
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, username FROM adh_users WHERE user_role='admin' ORDER BY id LIMIT 1")
            admin = cur.fetchone()
    finally:
        conn.close()
    if not admin:
        print("[probe] 找不到管理员身份，拒绝无身份取数")
        return 1

    print(f"[probe] ds={ds_name}(id={ds_id}) table={table} 身份=admin/{admin['username']}")
    try:
        no_pred = _run(f'SELECT COUNT(*) AS n, MIN("__time__") AS t_min, MAX("__time__") AS t_max '
                       f'FROM "{table}"', ds_id, int(admin["id"]), str(admin["username"]))
    except Exception as exc:  # noqa: BLE001 — 探针失败直接报出，不掩盖
        print(f"[probe] 无谓词查询失败: {exc}")
        return 1
    now_s = int(time.time())
    n0 = int(no_pred.get("n") or 0)
    tmin, tmax = int(no_pred.get("t_min") or 0), int(no_pred.get("t_max") or 0)
    print(f"  无 __time__ 谓词 : rows={n0:>10}  span={max(tmax - tmin, 0) / 60:>8.1f} 分钟  "
          f"最早距今={(now_s - tmin) / 60 if tmin else 0:>8.1f} 分钟  最新距今={(now_s - tmax) / 60 if tmax else 0:>8.1f} 分钟")

    for label, secs in WINDOWS:
        try:
            row = _run(f'SELECT COUNT(*) AS n FROM "{table}" '
                       f'WHERE "__time__" >= to_unixtime(now()) - {secs}',
                       ds_id, int(admin["id"]), str(admin["username"]))
        except Exception as exc:  # noqa: BLE001
            print(f"  {label:<16} : 失败 {exc}")
            continue
        n = int(row.get("n") or 0)
        flag = "  <== 与无谓词行数一致（默认窗≈此）" if n == n0 and n0 > 0 else ""
        print(f"  __time__>now-{label:<7}: rows={n:>10}{flag}")
        time.sleep(0.2)

    print("\n[probe] 结论判读：无谓词行数落在哪个显式窗口，SLS 引擎默认窗就是那个量级。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
