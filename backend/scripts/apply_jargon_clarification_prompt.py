"""把「业务黑话先澄清、不猜不到处翻」规则写入各 AS-BOT 的 system_prompt。

背景（实测痛点）：用户口中的黑话/简称常与平台语义层的权威名称对不上。模型拿到
对不上的词时的默认行为是**先猜语义、再到处翻**（换近义词重查 get_metrics /
knowledge_search，甚至翻 search_metadata / get_table_schema 找"长得像"的列），
一轮烧完才发现猜错，又回头问用户 —— 用户视角就是"花很多时间没查到，猜完继续查"。

拍板的行为口径：**不猜、不到处找**。先由模型推测 2~4 个候选语义词（候选必须来自
实时目录/字典，不发明），直接 `ask_user` 让用户选，选中再取数。
一次问句换一轮确定性，优于多轮试探。

为什么不写进 `prompt_composer` 的共用常量：
- 取数规范常量（SEMANTIC_QUERY_RULES / NL2SQL_QUERY_RULES）是"约束"，本段是
  "工作方式/人格"，唯一配置点应按 as-bot-system-waker 规则留在 Waker 的
  system_prompt + persona；
- 写进常量会对所有 AS-BOT 一字不差强推，而查看者（不取数）需要的是同一心事的
  只读版说法 —— 本脚本按角色出变体（`_VIEWER_MARKER` 分支）。

幂等：以段落标记（`_MARKER` / `_VIEWER_MARKER`）判重，已存在则**整段替换**该标记
之后的内容（保留标记之前的原有文字），可重复执行不叠加。

用法（repo 根目录）:
  venv/bin/python backend/scripts/apply_jargon_clarification_prompt.py            # dry-run 看计划
  venv/bin/python backend/scripts/apply_jargon_clarification_prompt.py --apply    # 落库
  venv/bin/python backend/scripts/apply_jargon_clarification_prompt.py --keys analyst,viewer
"""
from __future__ import annotations

import argparse
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)

#: 段落标记（同时作为幂等判重锚点，出现在标题行里）
_MARKER = "## 业务黑话先澄清，不猜不到处翻"
#: 只读身份（无取数通道）的变体标记
_VIEWER_MARKER = "## 业务黑话先澄清，不猜不指路"

_BODY = f"""{_MARKER}
（宁问一句，不猜三轮）
- 用户常说的简称、黑话、活动代号、系统叫法、本地口径，与平台语义层的权威名称
  （对象/指标/维度/枚举业务名）**常常对不上**。对不上时**不许猜**，也不许换近义词
  反复试探。
- 判"对不对得上"只看 `get_metrics` 实时目录（合法名称的唯一权威），以及 `get_glossary`
  / 已绑定知识库的口径解释；**不得**靠翻物理表名、列名、元数据去"找一个长得像的"
  （`search_metadata` / `get_table_schema` 只用于把**已确认**的业务口径落到物理结构，
  不用于反推用户说的是哪个业务概念）。
- 目录里找不到用户说的词、或一个词可能落到多个口径（同名指标、多种算法、跨站点/跨源）：
  先自行推测 2~4 个最可能的候选语义词，**候选名一律从实时目录原文复制**（不发明名称、
  不写物理列名），直接 `ask_user` 把候选列出来让用户选，并一句说清各自口径差别；
  拿不出候选就开放式问一句"你说的 X 指哪个口径/哪张报表里的哪个数"。
- 候选不排序、不取第一个、不做"可能是这个吧"的折中；用户选定前**不下发取数**，
  也不用其他工具继续试探。确认后再取数，并在结论里回显口径（"按你选的 X 统计"）。
- 同一说法反复需要澄清（跨会话稳定口径）时，提醒用户可以把它存进个人记忆或补进
  字典别名，下次就能直接命中，不必每轮再问。"""

_VIEWER_BODY = f"""{_VIEWER_MARKER}
（宁问一句，不猜三轮）
- 用户说的简称、黑话、系统叫法，与平台资产清单里的权威名称常常对不上。对不上时**不许猜**，
  也不许翻表找"长得像的"来充数 —— 你看不到物理结构，也不该据此指认业务对象。
- 判"对不对得上"只看 `get_metrics` / 术语字典里的名称与定义；对不上就推测 2~4 个最接近的
  候选（名称一律从目录原文复制），直接 `ask_user` 让用户选，选定后再展示对应定义/清单。
- 拿不出候选或清单里确实没有：如实说明"当前可见资产里没有这个说法"，建议由数据同学补登记
  别名或口径，不编造定义、不把相近的指标当同一个讲。"""


def _segment_for(has_semantic_query: bool) -> str:
    """按 AS-BOT 能力选正文：无取数通道（查看者形态）用只读版。

    正文首行就是幂等锚点标记（`_MARKER` / `_VIEWER_MARKER`），二者一体不会配错。
    """
    return _BODY if has_semantic_query else _VIEWER_BODY


def _splice(existing: str, body: str) -> str:
    """把 body 追加/替换到 existing 末尾（本脚本任一旧版段落整段替换，不叠加）。

    两类标记都扫：先只读版后取数版的存量行也要能被替换（同一 AS-BOT 后续
    开了 run_semantic_query 时不会留下两段互斥的说明）。
    """
    existing = (existing or "").rstrip()
    anchors = [existing.find(a) for a in (_MARKER, _VIEWER_MARKER)]
    anchors = [i for i in anchors if i >= 0]
    if anchors:
        head = existing[:min(anchors)].rstrip()
        return f"{head}\n\n{body}".strip()
    return f"{existing}\n\n{body}".strip() if existing else body


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写库（默认 dry-run）")
    ap.add_argument("--keys", default="", help="只处理这些 as_bot_key（逗号分隔）")
    args = ap.parse_args()

    from backend.common.db import execute_query, execute_write

    rows = execute_query(
        """SELECT id, as_bot_key, display_name, system_prompt, tools
           FROM adh_as_bots ORDER BY id""")
    wanted = {k.strip() for k in args.keys.split(",") if k.strip()}

    plan = []
    for r in rows:
        key = r["as_bot_key"] or ""
        if wanted and key not in wanted:
            continue
        tools = r.get("tools") or "{}"
        has_sq = "run_semantic_query" in str(tools)
        body = _segment_for(has_sq)
        new = _splice(r.get("system_prompt") or "", body)
        old = (r.get("system_prompt") or "").strip()
        if new == old:
            plan.append((r["id"], key, "已是最新", len(old), len(new), not has_sq))
            continue
        plan.append((r["id"], key, "UPDATE", len(old), len(new), not has_sq))
        if args.apply:
            execute_write("UPDATE adh_as_bots SET system_prompt = %s WHERE id = %s", (new, r["id"]))

    print(f"{'id':<8}{'as_bot_key':<22}{'动作':<10}{'原长度':>8}{'新长度':>8}  变体")
    for rid, key, act, o_len, n_len, viewer in plan:
        print(f"{rid:<8}{key:<22}{act:<10}{o_len:>8}{n_len:>8}  {'只读版' if viewer else '取数版'}")
    if not args.apply:
        print("\n[dry-run] 未写库；加 --apply 落库。待写入段落预览：\n")
        print(_BODY)
        print("\n--- 只读身份变体 ---\n")
        print(_VIEWER_BODY)
        return 0

    # 配置事实缓存：轮次起点拼提示词会读缓存的 AS-BOT 行，写入口必须同批失效
    from backend.common.as_bot_config_cache import invalidate
    invalidate("apply_jargon_clarification_prompt")
    print(f"\n已更新 {sum(1 for p in plan if p[2] == 'UPDATE')} 个 AS-BOT 的 system_prompt，配置缓存已失效。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
