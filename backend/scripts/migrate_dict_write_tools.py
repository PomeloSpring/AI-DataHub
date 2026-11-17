"""一次性迁移: 给已启用 ontology 工具组的 AS-BOT 幂等补上字典写工具勾选。

背景: save_metric / save_dimension 的设计拍板早已存在（服务层 create_metric /
create_dimension 也已就绪并标注"供 AS-BOT 工具复用"），但工具层从未落地过 ——
全仓 grep 与 `git log --all -S save_metric` 均零命中，导致 AS-BOT 面对
"给这个对象加个指标/维度"只能拒答并推荐去页面。工具补齐后，存量 AS-BOT 的
`tools.mcp.ontology` 仍需显式勾选才会注册（逐工具授权，未勾选即不可调用）。

口径: 只给**已经启用 ontology 组**的 AS-BOT 追加，不给纯业务问答型 AS-BOT 扩权
（字典写会改变指标口径，属建模能力，跟随本体建模授权）。
幂等: 重复执行只保证这两个名字在列，不改动其他工具与组。
"""
import json

from backend.common.db.metadata_db import get_metadata_conn

DICT_WRITE_TOOLS = ["save_metric", "save_dimension"]


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
                    continue
                if not isinstance(t, dict):
                    continue
                mcp = t.get("mcp")
                if not isinstance(mcp, dict):
                    continue
                current = mcp.get("ontology") or []
                if not current:
                    continue          # 未启用本体工具组 → 不扩权
                merged = list(dict.fromkeys(list(current) + DICT_WRITE_TOOLS))
                if merged == list(current):
                    continue
                mcp["ontology"] = merged
                t["mcp"] = mcp
                groups = t.get("groups")
                if isinstance(groups, list) and "ontology" not in groups:
                    groups.append("ontology")
                updates.append((json.dumps(t, ensure_ascii=False), r["as_bot_key"]))
            for tools_json, key in updates:
                cur.execute("UPDATE adh_as_bots SET tools=%s WHERE as_bot_key=%s",
                            (tools_json, key))
                changed += 1
            conn.commit()
    finally:
        conn.close()
    print(f"迁移完成, 更新 AS-BOT 行数: {changed}")

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT as_bot_key, tools FROM adh_as_bots ORDER BY as_bot_key")
            for r in cur.fetchall():
                t = json.loads(r["tools"]) if isinstance(r["tools"], str) else (r["tools"] or {})
                onto = ((t or {}).get("mcp") or {}).get("ontology") or []
                got = [x for x in DICT_WRITE_TOOLS if x in onto]
                print(f"  {r['as_bot_key']}: ontology={len(onto)} 个工具, 字典写={got or '（未启用本体组）'}")
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
