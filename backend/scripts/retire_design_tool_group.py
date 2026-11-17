"""一次性迁移：把已退役的 design 工具组从所有 AS-BOT 配置里摘掉。

背景：大屏/仪表盘改为会话内**直建**（`create_data_screen` 逐图落 `adh_datasets` 数据集
并挂成数据集图），`datahub_design` 组（request/get/prepare_dashboard_design +
两个设计域检索）整组退役 —— 会话里不再打开设计面板、不再让人填/确认 SQL。

为什么必须同批迁移配置：`tool_policy.compile_policy` 以 `TOOL_SERVER_TOOLS` 为唯一
权威校验组名，库里残留 `design` 会让该 AS-BOT 装配时报「存在未知业务工具组」而
整只不可用（不是少一个工具，是全挂）。

写法纪律（与 `migrate_render_tool_group.py` 一致）：逐行读 tools JSON → 只动
`$.mcp.design` 与 `$.groups` → 按 as_bot_key 单行 UPDATE；**绝不**整列覆盖 tools
（历史上幂等 upsert 把别的批次拥有的列写成空值，AI 工具静默消失）。

用法（默认 dry-run，只打印会改什么）：
    venv/bin/python -m backend.scripts.retire_design_tool_group            # 预览
    venv/bin/python -m backend.scripts.retire_design_tool_group --apply    # 应用并核验
"""
import json
import sys

from backend.common.db.metadata_db import get_metadata_conn

RETIRED_GROUP = "design"


def _normalize(raw) -> dict:
    try:
        tools = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (ValueError, TypeError):
        return {}
    return tools if isinstance(tools, dict) else {}


def _strip(tools: dict) -> tuple[dict, bool]:
    """返回 (去掉 design 后的 tools, 是否发生变更)。

    必须在副本上改：`mcp = tools.get("mcp")` 拿到的是同一个对象，直接 pop 会把
    入参也改掉，"结果 vs 原值"永远相等 → 变更静默不落库。
    """
    changed = False
    raw_mcp = tools.get("mcp")
    mcp = dict(raw_mcp) if isinstance(raw_mcp, dict) else {}
    if RETIRED_GROUP in mcp:
        mcp.pop(RETIRED_GROUP, None)
        changed = True
    out = dict(tools)
    if changed:
        out["mcp"] = mcp
    groups = out.get("groups")
    if isinstance(groups, list) and RETIRED_GROUP in groups:
        out["groups"] = [g for g in groups if g != RETIRED_GROUP]
        changed = True
    return out, changed


def migrate(apply: bool = False) -> int:
    conn = get_metadata_conn()
    updates = []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT as_bot_key, is_active, tools FROM adh_as_bots ORDER BY id")
            rows = cur.fetchall()
            for r in rows:
                tools = _normalize(r["tools"])
                stripped, changed = _strip(tools)
                if not changed:
                    print(f"  无需变更 {r['as_bot_key']}")
                    continue
                updates.append((json.dumps(stripped, ensure_ascii=False), r["as_bot_key"]))
                before = len(((tools.get("mcp") or {}).get(RETIRED_GROUP)) or [])
                print(f"  {'将' if not apply else '已'}摘除 design 组: {r['as_bot_key']}"
                      f" active={r['is_active']} 移除工具数={before}"
                      f" groups={stripped.get('groups')}")
            if apply and updates:
                with conn.cursor() as wcur:
                    for tools_json, key in updates:
                        wcur.execute("UPDATE adh_as_bots SET tools=%s WHERE as_bot_key=%s",
                                     (tools_json, key))
                conn.commit()
    finally:
        conn.close()
    print(f"{'应用' if apply else 'dry-run'}完成，待改/已改行数: {len(updates)}")
    if apply:
        verify()
    return len(updates)


def verify() -> int:
    """核验：全表不再有 design 组残留（JSON 坏行也算残留，只数 affected 发现不了）。"""
    left = 0
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT as_bot_key, tools FROM adh_as_bots ORDER BY as_bot_key")
            for r in cur.fetchall():
                tools = _normalize(r["tools"])
                has_group = RETIRED_GROUP in ((tools.get("mcp") or {}))
                in_groups = RETIRED_GROUP in (tools.get("groups") or [])
                if has_group or in_groups:
                    left += 1
                    print(f"  ⚠ 仍残留 design: {r['as_bot_key']} mcp={has_group} groups={in_groups}")
                else:
                    print(f"  {r['as_bot_key']}: design 已清空")
    finally:
        conn.close()
    print(f"核验完成，残留行数: {left}")
    return left


if __name__ == "__main__":
    migrate(apply="--apply" in sys.argv)
