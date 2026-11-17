"""一次性迁移: 给全部 AS-BOT 幂等补上 memory 工具组勾选。

背景: memory 工具组(save_memory/list_memory/delete_memory)实现早已存在, 但从未登记进
sdk_tools.TOOL_SERVER_TOOLS, 因此不会被注册进 MCP server —— LLM 视野里没有写记忆的
工具, "AI 自动学习保存记忆"这条承诺一直是空的(adh_user_memory 长期 0 行)。
组登记补齐后, 存量 AS-BOT 的 tools.mcp 仍需显式勾选才会注册(逐工具授权)。

授权口径: 记忆只读写**本人**的行(user_id 由服务端执行上下文注入, 不接受 LLM 传值),
不返回任何业务数据行, 不经数据源, 因此按"全部 AS-BOT 默认开启"处理 —— 与 assets 组
同属个人工作区能力, 不属于护城河管辖的取数面。

幂等: 重复执行只保证 mcp.memory 含这三个工具、groups 含 memory, 不改动其他组。
"""
import json

from backend.common.db.metadata_db import get_metadata_conn

MEMORY_TOOLS = ["save_memory", "list_memory", "delete_memory"]


def migrate():
    conn = get_metadata_conn()
    changed = 0
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT as_bot_key, tools FROM adh_as_bots")
            rows = cur.fetchall()
            updates = []
            for r in rows:
                raw = r["tools"]
                try:
                    t = json.loads(raw) if isinstance(raw, str) else (raw or {})
                except (ValueError, TypeError):
                    t = {}
                if not isinstance(t, dict):
                    t = {}
                mcp = t.get("mcp")
                if not isinstance(mcp, dict):
                    mcp = {}
                before = json.dumps(t, sort_keys=True, ensure_ascii=False)
                merged = list(dict.fromkeys(list(mcp.get("memory") or []) + MEMORY_TOOLS))
                mcp["memory"] = merged
                t["mcp"] = mcp
                groups = t.get("groups")
                if isinstance(groups, list) and "memory" not in groups:
                    groups.append("memory")
                if json.dumps(t, sort_keys=True, ensure_ascii=False) != before:
                    updates.append((json.dumps(t, ensure_ascii=False), r["as_bot_key"]))
            for tools_json, key in updates:
                cur.execute("UPDATE adh_as_bots SET tools=%s WHERE as_bot_key=%s",
                            (tools_json, key))
                changed += 1
            conn.commit()
    finally:
        conn.close()
    print(f"迁移完成, 更新 AS-BOT 行数: {changed}")
    # 核对结果
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT as_bot_key, tools FROM adh_as_bots ORDER BY as_bot_key")
            for r in cur.fetchall():
                t = json.loads(r["tools"]) if isinstance(r["tools"], str) else (r["tools"] or {})
                mem = ((t or {}).get("mcp") or {}).get("memory")
                print(f"  {r['as_bot_key']}: memory={mem}")
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
