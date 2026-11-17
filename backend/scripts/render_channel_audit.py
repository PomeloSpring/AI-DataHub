"""选道分布复盘（M5 观察项）：把 G5 的 `delivery_channel` span 汇成一份可读分布。

为什么要有这个脚本而不是直接拍板加约束：v2 把"该出整页文档还是图表卡"的裁决权交给了模型，
已知风险是 LLM 天然偏爱写长文档（⇒ 系统性偏 `render_html`），而 html 单文件自包含拿不到
地理底图与富图型。要不要收紧（方案 §10 待确认 1）只能用**真实分布**判断 —— 所以每次裁决都
记了 span（G5 是评估地基，不可省），这里只是把它读出来。

口径：
- 主交付物按通道分：`chart`（图表片段，可多次）/ `html`（整页文档）/ `spec`（结构化章节报告）；
- "重试"取同一 trace 内同通道的最大 `attempt`（>1 说明第一份被打回过）；
- 硬门/体检命中次数来自回执里的 `blockers`（原因原文计数，不归类，归类会丢信息）。

只读：不写库、不改授权。默认窗口 14 天（对齐方案里"上线后跑两周"）。

用法：
    venv/bin/python -m backend.scripts.render_channel_audit                 # 近 14 天
    venv/bin/python -m backend.scripts.render_channel_audit --days 30       # 近 30 天
    venv/bin/python -m backend.scripts.render_channel_audit --json          # 机读输出
"""
import argparse
import datetime as dt
import json
import sys
from collections import Counter

from backend.common.db.metadata_db import get_metadata_conn

SPAN_KIND = "delivery_channel"
CHANNELS = ("chart", "html", "spec")
CHANNEL_LABELS = {"chart": "图表片段 render_chart", "html": "整页文档 render_html",
                  "spec": "章节报告 render_report"}
# 主交付物只有 html/spec 两种（chart 是片段，一轮可多张）：占比只算真落成的交付件，
# 拿调用数当分母会把“一次交付重试三遍”读成“三遍都选了 html”。
PRIMARY_CHANNELS = ("html", "spec")
# 建议线：html 占主交付物超过这个比例、且 spec 明显偏低时，提示值得评估软约束。
# 这是**提示**不是判据 —— 判据由人按分布与产出质量一起定（方案 §10）。
HTML_SHARE_HINT = 0.7


def _loads(raw) -> dict:
    """span 的 output_text 可能是被观测截断过的半截 JSON：解析不了就只保留能确定的字段。"""
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def collect(days: int = 14) -> list[dict]:
    """读窗口内的选道 span（含 trace 侧的 entrypoint，用于区分对话/无人值守）。"""
    since = dt.date.today() - dt.timedelta(days=max(1, int(days)))
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT s.trace_id, s.name, s.status, s.output_text, s.started_at,
                          t.entrypoint
                   FROM adh_llm_spans s
                   LEFT JOIN adh_llm_traces t ON t.trace_id = s.trace_id
                   WHERE s.kind = %s AND s.dt >= %s
                   ORDER BY s.started_at""",
                (SPAN_KIND, since),
            )
            return list(cur.fetchall() or [])
    finally:
        conn.close()


def summarize(rows: list[dict]) -> dict:
    """span 行 → 分布摘要（纯函数，不碰库，便于用例锁口径）。"""
    per: dict[str, dict] = {c: {"calls": 0, "ok": 0, "error": 0, "docs": 0,
                                "attempts": [], "blockers": Counter(), "notes": Counter()}
                            for c in CHANNELS}
    # (trace, channel) → 最大 attempt：重试次数按同一 trace 同一通道取峰值，不数 span 条数
    peak: dict[tuple[str, str], int] = {}
    unattributed = 0
    entrypoints: Counter = Counter()
    for r in rows or []:
        payload = _loads(r.get("output_text"))
        channel = str(payload.get("channel") or "").strip()
        if channel == "doc":      # 兼容早期写法的取值（doc 就是整页文档通道）
            channel = "html"
        if channel not in per:
            name = str(r.get("name") or "")
            guessed = next((c for c in CHANNELS if name.endswith(c)), "")
            if not guessed:
                unattributed += 1
                continue
            channel = guessed
        bucket = per[channel]
        bucket["calls"] += 1
        status = str(r.get("status") or "").strip().lower()
        if status == "success":
            bucket["ok"] += 1
        else:
            bucket["error"] += 1
        if str(payload.get("doc_id") or "").strip():
            bucket["docs"] += 1
        try:
            attempt = int(payload.get("attempt") or 1)
        except (TypeError, ValueError):
            attempt = 1
        bucket["attempts"].append(max(1, attempt))
        key = (str(r.get("trace_id") or ""), channel)
        peak[key] = max(peak.get(key, 1), max(1, attempt))
        for b in payload.get("blockers") or []:
            bucket["blockers"][str(b)] += 1
        for n in payload.get("notes") or []:
            bucket["notes"][str(n)] += 1
        if r.get("entrypoint"):
            entrypoints[str(r["entrypoint"])] += 1

    out: dict = {"spans": len(rows or []), "unattributed": unattributed,
                 "entrypoints": dict(entrypoints), "channels": {}, "primary_share": {},
                 "blockers": {}, "notes": {}, "retried_spans": 0}
    for channel, bucket in per.items():
        attempts = bucket["attempts"]
        retried = sum(1 for a in attempts if a > 1)
        out["channels"][channel] = {
            "label": CHANNEL_LABELS[channel], "calls": bucket["calls"],
            "ok": bucket["ok"], "error": bucket["error"], "docs": bucket["docs"],
            "avg_attempt": round(sum(attempts) / len(attempts), 2) if attempts else 0,
            "max_attempt": max(attempts) if attempts else 0,
            "retried": retried,
            "traces_reaching_peak": sum(1 for (t, c), v in peak.items() if c == channel and v > 1),
        }
        out["blockers"][channel] = dict(bucket["blockers"])
        out["notes"][channel] = dict(bucket["notes"])
        out["retried_spans"] += retried
    primary_docs = sum(out["channels"][c]["docs"] for c in PRIMARY_CHANNELS)
    # 占比按**真落成交付件**算，不按调用次数：被硬门打回的重试也带 channel，拿调用数当分母
    # 会把“同一次交付试了三遍”读成“三次里两次选了 html”。
    for c in PRIMARY_CHANNELS:
        out["primary_share"][c] = (round(out["channels"][c]["docs"] / primary_docs, 3)
                                   if primary_docs else None)
    out["primary_docs"] = primary_docs
    return out


def verdict(summary: dict) -> list[str]:
    """把分布翻成"要不要收紧"的可读判断（只给线索，不代替人拍板）。"""
    lines: list[str] = []
    if not summary.get("spans"):
        lines.append("窗口内没有选道记录：渲染机未上线/未授权，或观测未开启。"
                     "没有样本就别拿分布当依据（也不构成“效果不好”的结论）。")
        return lines
    if summary.get("unattributed"):
        lines.append(f"有 {summary['unattributed']} 条 span 解析不出通道（回执被截断或写法变更），"
                     "下方分母不含它们 —— 数量大时先去修记账再读分布。")
    html_share = (summary.get("primary_share") or {}).get("html")
    spec_share = (summary.get("primary_share") or {}).get("spec")
    if html_share is None:
        lines.append("窗口内没有落成的主交付物（整页文档/章节报告都为零）：占比无分母，"
                     "先确认根本没人调用主渲染机，再下“选错了”的结论。")
    else:
        lines.append(f"主交付物占比（按落成交付件 {summary.get('primary_docs')} 份算）："
                     f"整页文档 html {html_share:.0%} / 章节报告 spec {(spec_share or 0):.0%}")
        if html_share >= HTML_SHARE_HINT and (spec_share or 0) <= 1 - HTML_SHARE_HINT:
            lines.append(f"提示：html 占比 ≥{HTML_SHARE_HINT:.0%} 且 spec 明显偏低 —— 值得按方案 §10 "
                         "待确认 1 评估软约束（G2 一律拒收 / 判断树里把富图型问得更硬），"
                         "但先抽查几份 html 是不是真不该出地图，别按占比直接收权。")
        elif spec_share and html_share < HTML_SHARE_HINT:
            lines.append("提示：两条文档通道都有真实使用，暂无收紧依据，继续观察。")
    for channel in CHANNELS:
        c = summary["channels"][channel]
        if c["error"]:
            rate = c["error"] / c["calls"] if c["calls"] else 0
            lines.append(f"{c['label']}：{c['error']}/{c['calls']} 次被打回（{rate:.0%}）")
        if c["retried"]:
            lines.append(f"{c['label']}：{c['retried']} 次是重试（第一份未通过体检/硬门）")
    return lines


def render_text(summary: dict, days: int) -> str:
    lines = [f"交付形态选道分布（近 {days} 天，span 共 {summary['spans']} 条）",
             f"{'通道':<22}{'调用':>6}{'成功':>6}{'打回':>6}{'交付件':>7}{'均重试':>8}{'峰重试':>8}"]
    for channel in CHANNELS:
        c = summary["channels"][channel]
        lines.append(f"{c['label']:<22}{c['calls']:>6}{c['ok']:>6}{c['error']:>6}"
                     f"{c['docs']:>7}{c['avg_attempt']:>8}{c['max_attempt']:>8}")
    if summary.get("entrypoints"):
        lines.append("入口分布：" + "、".join(f"{k} {v}" for k, v in
                                            sorted(summary["entrypoints"].items(),
                                                   key=lambda x: -x[1])))
    any_blocker = any(summary["blockers"].get(c) for c in CHANNELS)
    if any_blocker:
        lines.append("硬门/体检命中 Top（原因原文）：")
        pairs = [(c, reason, n) for c in CHANNELS
                 for reason, n in summary["blockers"][c].items()]
        for c, reason, n in sorted(pairs, key=lambda x: -x[2])[:12]:
            lines.append(f"  ×{n:<4} [{c}] {reason[:120]}")
    else:
        lines.append("硬门/体检命中：窗口内没有被打回的记录。")
    lines.append("")
    lines += verdict(summary)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="交付形态选道分布复盘（只读）")
    parser.add_argument("--days", type=int, default=14, help="回溯天数（默认 14）")
    parser.add_argument("--json", action="store_true", help="输出机读 JSON")
    args = parser.parse_args(argv)
    summary = summarize(collect(days=args.days))
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, default=str))
    else:
        print(render_text(summary, args.days))
    return 0


if __name__ == "__main__":
    sys.exit(main())
