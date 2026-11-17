"""一次性迁移：给具备取数能力的存量 AS-BOT 幂等补上 render 工具组勾选（交付形态渲染机）。

背景：Chat 的复杂分析原先只能得到一串离散 ```chart 卡片（正文与图割裂、没有整体可读
交付物），整份 HTML 报告只能作为会话工作区文件交付。M1 把 HTML 升成运行时一等交付
通道：`render_html` 渲染机把正文落成受管文档对象、消息内只留引用块，**由模型自选**
chart / html —— 服务端只设能力硬门与体检，不替模型判"该不该出整页文档"。

授权口径（为什么这样切）：
- 渲染机不是平台写动作：它不改配置/元数据/看板，只把本轮已展示给用户的内容落成
  会话内交付对象（与会话消息落库同级），故由 AS-BOT **工具授权**裁决，**不新增权限码**
  （与 request_dashboard_design 同类，它也没有独立权限码）。归档进报告中心仍走既有的
  会话内入口，不在本工具职责内。
- 默认只给**有取数组**（semantic/query）的 AS-BOT：没有数据可交付的角色，开了就是
  给模型广告一个用不上的入口（"配了没消费方"的反面形态）。管理员可按 AS-BOT 配置收回。
- 组内工具名必须与 `TOOL_SERVER_TOOLS["render"]` 对账：`tool_policy` 会按权威清单校验，
  写了未登记的名字会让整组被判"不在 allowed"而**静默丢弃**（历史同类坑）。

写法纪律：逐行读 tools JSON → 只动 `$.mcp.render` 与 `$.groups` → 按 as_bot_key 单行 UPDATE。
**绝不**整列覆盖 tools（历史上幂等 upsert 把别的批次拥有的列写成空值，AI 工具静默消失）。

用法（默认 dry-run，只打印会改什么）：
    venv/bin/python -m backend.scripts.migrate_render_tool_group            # 预览
    venv/bin/python -m backend.scripts.migrate_render_tool_group --apply    # 应用并核验
"""
import json
import sys

from backend.common.db.metadata_db import get_metadata_conn

# 与 sdk_tools.TOOL_SERVER_TOOLS["render"] 同源；M2/M3 接入 render_chart/render_report 时
# 必须一并更新这里，否则新工具不会下发给模型。
RENDER_TOOLS = ["render_chart", "render_html", "render_report"]
QUERY_BEARING_GROUPS = ("semantic", "query")


def _normalize(raw):
    try:
        t = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (ValueError, TypeError):
        return {}
    return t if isinstance(t, dict) else {}


def _can_take_data(bot_tools: dict) -> bool:
    """该 AS-BOT 是否真的会产分析交付物（勾了取数组）。"""
    mcp = bot_tools.get("mcp")
    if not isinstance(mcp, dict):
        return False
    return any(mcp.get(g) for g in QUERY_BEARING_GROUPS)


def _merge(tools: dict) -> dict:
    """返回合并后的 tools（只在需要时改；不改其他组、不整列重建）。

    必须在副本上改：旧写法 `mcp = tools.get("mcp")` 拿到的是同一个对象，
    `mcp["render"] = merged` 会把**入参**也改了，migrate() 里“合并结果 vs 原值”的比较
    于是永远相等 → 新工具静默不下发（本文件顶部警告的正是这类静默坑）。
    """
    raw_mcp = tools.get("mcp")
    mcp = dict(raw_mcp) if isinstance(raw_mcp, dict) else {}
    merged = list(dict.fromkeys(list(mcp.get("render") or []) + RENDER_TOOLS))
    if merged == list(mcp.get("render") or []):
        return tools
    mcp["render"] = merged
    out = dict(tools)
    out["mcp"] = mcp
    groups = out.get("groups")
    if isinstance(groups, list) and "render" not in groups:
        out["groups"] = list(groups) + ["render"]
    return out


def migrate(apply: bool = False) -> int:
    conn = get_metadata_conn()
    updates = []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT as_bot_key, is_active, tools FROM adh_as_bots ORDER BY id")
            rows = cur.fetchall()
            for r in rows:
                tools = _normalize(r["tools"])
                if not _can_take_data(tools):
                    print(f"  跳过 {r['as_bot_key']}: 未勾取数组，交付渲染机对它无消费方")
                    continue
                merged = _merge(tools)
                if json.dumps(merged, sort_keys=True, ensure_ascii=False) == \
                        json.dumps(tools, sort_keys=True, ensure_ascii=False):
                    print(f"  无需变更 {r['as_bot_key']}")
                    continue
                updates.append((json.dumps(merged, ensure_ascii=False), r["as_bot_key"]))
                print(f"  {'将' if not apply else '已'}追加 render 组: {r['as_bot_key']}"
                      f" active={r['is_active']} -> render={merged['mcp'].get('render')}")
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


def verify() -> None:
    """核验生效行：JSON 可解析 + $.mcp.render 含工具名（只数 affected rows 发现不了坏 JSON）。"""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT as_bot_key, tools FROM adh_as_bots ORDER BY as_bot_key")
            for r in cur.fetchall():
                tools = _normalize(r["tools"])
                render = ((tools.get("mcp") or {}).get("render")) or []
                bad = [t for t in render if t not in RENDER_TOOLS]
                flag = "  ⚠ 组内存在未登记工具名（会被 tool_policy 静默丢弃）" if bad else ""
                print(f"  {r['as_bot_key']}: render={render}{flag}")
    finally:
        conn.close()


if __name__ == "__main__":
    migrate(apply="--apply" in sys.argv)
