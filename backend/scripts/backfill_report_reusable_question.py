"""报表可复跑问题归位器：把「续问句」登记的问题改回会话里实际取数那一轮。

为什么需要本脚本（真机缺陷：报表「direct-ip 页面访问 IP 归属地分布报告」）：
报表往往存于「帮我输出成html形式的报告」「地图瓦片怎么没有在报告中」这类**续问**回合
—— 本轮只有 write/edit、没有取数。旧登记口径把这句原样写进
`adh_reports.analysis_source.origin.question`，于是「创建同款定时任务」与
「用最新数据重新生成」复跑的是这句没有分析对象的话（定时任务跑出来的东西与报表无关）。

新口径见 `backend/modules/viz/services/report_service.py: reproducible_question`：
本轮没取数 → 往前找最近一个真取过数的回合，用它的问题当分析主体，本轮诉求作为「后续要求」带走。
本脚本把改造前已落库的报表按**同一份实现**回填，不在脚本里另写一套判断。

纪律：
* 默认 dry-run，`--apply` 才落库；
* `origin.question_edited=true`（属主在弹窗里亲手改过）的行**一律不动**；
* 会话消息已清理 / 回合定位不到的行保留原值并列成例外清单，不猜、不静默改。

用法（repo 根目录）:
  venv/bin/python backend/scripts/backfill_report_reusable_question.py
  venv/bin/python backend/scripts/backfill_report_reusable_question.py --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)

from backend.common.db import execute_query, execute_write          # noqa: E402
from backend.core import conversation_store                          # noqa: E402
from backend.modules.viz.services import report_service as reports   # noqa: E402


def _rows() -> list[dict]:
    return execute_query(
        "SELECT id, title, source_type, analysis_source FROM adh_reports "
        "WHERE analysis_source LIKE '%conversation_id%' ORDER BY created_at") or []


def _source(row: dict) -> dict:
    raw = row.get("analysis_source")
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def plan() -> tuple[list[dict], list[str]]:
    """回 ``(待改清单 [{id, title, old, new, note}], 例外说明清单)``。"""
    rows = _rows()
    changes: list[dict] = []
    skipped: list[str] = []

    for row in rows:
        source = _source(row)
        origin = source.get("origin")
        origin = origin if isinstance(origin, dict) else {}
        rid = int(row["id"])
        old = str(origin.get("question") or "").strip()
        if origin.get("question_edited"):
            continue                                   # 属主亲手改过：归位不得覆盖
        cid = int(origin.get("conversation_id") or 0)
        uuid = str(origin.get("message_uuid") or "")
        if str(origin.get("channel") or "") == "chat" and cid and uuid:
            messages = conversation_store.load_messages(cid) or []
            answer = next((m for m in messages if isinstance(m, dict)
                           and m.get("role") == "assistant"
                           and str(m.get("message_uuid") or "") == uuid), None)
            if answer is None:
                skipped.append(f"报表 {rid}「{row.get('title')}」：会话 {cid} 里找不到消息 {uuid}"
                               "（消息已清理），保留原问题")
                continue
            new, note = reports.reproducible_question(messages, answer)
        else:
            # report_refresh 产物不在这里算（它不持有回合定位键）：下面第二遍按来源报表的最终值继承
            continue
        if not new or new == old:
            continue
        changes.append({"id": rid, "title": row.get("title") or "", "old": old,
                        "new": new, "note": note, "source": source, "origin": origin})

    # 重跑产物按来源报表的**最终值**继承（来源本轮也被改时，两版必须同一句）
    by_id = {c["id"]: c for c in changes}
    for row in rows:
        source = _source(row)
        origin = source.get("origin") if isinstance(source.get("origin"), dict) else {}
        rid = int(row["id"])
        if rid in by_id or str(origin.get("channel") or "") != "report_refresh" \
                or origin.get("question_edited"):
            continue
        carried_id = int(source.get("refreshed_from") or 0)   # 在 analysis_source 顶层，不在 origin 里
        new = (by_id.get(carried_id) or {}).get("new") or ""
        if new and new != str(origin.get("question") or "").strip():
            by_id[rid] = {"id": rid, "title": row.get("title") or "",
                          "old": str(origin.get("question") or "").strip(), "new": new,
                          "note": reports.QUESTION_CARRIED_NOTE, "source": source,
                          "origin": origin}
            changes.append(by_id[rid])
    return changes, skipped


def apply_change(item: dict) -> None:
    source = dict(item["source"])
    source["origin"] = {**item["origin"], "question": item["new"],
                        "question_note": item["note"]}
    execute_write("UPDATE adh_reports SET analysis_source=%s, updated_at=%s WHERE id=%s",
                  (json.dumps(source, ensure_ascii=False, default=str),
                   reports._now(), item["id"]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="真正落库（默认只出计划）")
    args = ap.parse_args()

    changes, skipped = plan()
    print(f"待归位报表：{len(changes)} 份；保持不动：{len(skipped)} 份")
    for c in changes:
        print(f"\n─ 报表 {c['id']}「{c['title']}」")
        print(f"  旧：{c['old']}")
        print(f"  新：{c['new']}")
    for line in skipped:
        print(f"  · {line}")
    if not args.apply:
        print("\n[dry-run] 未写入。确认无误后加 --apply")
        return 0
    for c in changes:
        apply_change(c)
    print(f"\n已归位 {len(changes)} 份报表的可复跑问题")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
