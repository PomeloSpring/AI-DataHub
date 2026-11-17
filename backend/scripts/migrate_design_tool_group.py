"""一次性迁移: 把看板设计专用工具从 screen/semantic 组搬到独立 design 组。

背景: get_business_semantics/search_business_knowledge 原注册在 semantic 组,
request/get/prepare_dashboard_design 原在 screen 组; 现统一收进 datahub_design。
adh_as_bots.tools.mcp 按 {group:[tool]} 存储, 挪组后 tool_policy 会用 TOOL_SERVER_TOOLS
对账, 旧组里的这些名字会被判"不在 allowed"而静默丢弃 → 必须迁移到 design 数组。
幂等: 重复执行只保证 design 数组含这些工具、旧数组不含。
"""
from backend.common.db.metadata_db import get_metadata_conn
import json

DESIGN_TOOLS = [
    "request_dashboard_design", "get_dashboard_design", "prepare_dashboard_design",
    "get_business_semantics", "search_business_knowledge",
]
DESIGN_SET = set(DESIGN_TOOLS)


def migrate():
    conn = get_metadata_conn()
    changed = 0
    with conn.cursor() as cur:
        cur.execute("SELECT as_bot_key, tools FROM adh_as_bots WHERE tools IS NOT NULL")
        rows = cur.fetchall()
        updates = []
        for r in rows:
            raw = r["tools"]
            try:
                t = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except (ValueError, TypeError):
                continue
            if not isinstance(t, dict):
                continue
            mcp = t.get("mcp")
            if not isinstance(mcp, dict):
                continue
            moved = []
            # 从各旧组抽走设计工具, 收集到 design
            for g in list(mcp.keys()):
                if g == "design":
                    continue
                names = mcp.get(g) or []
                keep = [n for n in names if n not in DESIGN_SET]
                if len(keep) != len(names):
                    moved += [n for n in names if n in DESIGN_SET]
                    mcp[g] = keep
            existing = mcp.get("design") or []
            new_design = list(dict.fromkeys(list(existing) + moved))  # 去重保序
            if new_design:
                mcp["design"] = new_design
                groups = t.get("groups")
                if isinstance(groups, list) and "design" not in groups:
                    groups.append("design")
            else:
                mcp.pop("design", None)
            t["mcp"] = mcp
            updates.append((json.dumps(t, ensure_ascii=False), r["as_bot_key"]))
            changed += 1
        for tools_json, key in updates:
            cur.execute("UPDATE adh_as_bots SET tools=%s WHERE as_bot_key=%s", (tools_json, key))
        conn.commit()
    conn.close()
    print(f"迁移完成, 处理 AS-BOT 行数: {changed}")
    # 打印结果核对
    conn = get_metadata_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT as_bot_key, tools FROM adh_as_bots")
        for r in cur.fetchall():
            t = json.loads(r["tools"]) if isinstance(r["tools"], str) else (r["tools"] or {})
            mcp = (t or {}).get("mcp", {})
            print(f"  {r['as_bot_key']}: design={mcp.get('design')} | "
                  f"semantic残留={[x for x in (mcp.get('semantic') or []) if x in DESIGN_SET]} | "
                  f"screen残留={[x for x in (mcp.get('screen') or []) if x in DESIGN_SET]}")
    conn.close()


if __name__ == "__main__":
    migrate()
