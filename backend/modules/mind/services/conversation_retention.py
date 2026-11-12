"""会话与观测流水的保留策略（内置任务 ``conversation_history_retention``）。

为什么需要它（2026-11 存储审计）：元库里除 ``adh_intent_intercepts`` 外，
``adh_llm_spans`` / ``adh_llm_traces`` / 会话拆行消息都是**只增不减**——
观测表一度是体积最大的一张表（9.5MB），会话消息随对话量线性增长，
不清理最终会拖慢写入并放大敏感数据的驻留窗口。

口径（与 ``no-silent-degradation`` 对齐，不越权删用户资产）：

1. **观测流水超期删除**：span/trace 是排障用的过程数据，超过保留期即删
   （``RETENTION_LLM_SPAN_DAYS``，默认 90 天）。赞踩/回合定位走
   ``adh_message_feedback`` 与会话消息本身，不依赖 span 长存。
2. **长期未活跃会话只剥明细、不删正文**：把 ``tool_calls[].result`` / ``thinking`` /
   ``ai_raw_response`` / ``process`` 这类"未经展示治理的中间产物"剥掉
   （``RETENTION_CONVERSATION_TRIM_DAYS``，默认 180 天），并在行上打
   ``storage_state='trimmed'`` + 消息里标 ``storage_trimmed=True`` —— 用户回看时
   看得到"该回合明细已按保留策略清理"，而不是内容凭空消失。
   **删除整段会话是产品决策，不在本任务里擅自做。**
3. 剥明细时消息级 ``result.rows`` 保留前 ``RETENTION_RESULT_ROWS_KEEP`` 行
   （默认 200，与 ``render_conversation_report`` 只渲染前 200 行同口径），
   保证"存为报表"不因裁剪而失真。

时间列口径：一律与写入侧同一时钟基准（这些列由服务端 ``NOW()`` 本地时间写入），
不得一半用 UTC 一半用本地，否则窗口过滤会永远为空/永远全删。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

#: 单次任务最多处理的会话数（超出的留给下一次；防止一天跑不完把任务挂死）
MAX_CONVERSATIONS_PER_RUN = 2000
#: 剥明细后保留的进度阶段条数
PROGRESS_STAGES_KEEP = 20


def _days_or(config_key: str, days: Optional[int]) -> int:
    """保留期：显式传参优先，否则取 config 登记值（缺配置即报错，不默认"全留"）。"""
    if days is not None:
        return max(1, int(days))
    from backend.common import config
    value = int(getattr(config, config_key, 0) or 0)
    if value <= 0:
        raise ValueError(f"保留期配置 {config_key} 未设置或为 0，请显式配置后重试")
    return value


# ── ① 观测流水滚动删除 ──────────────────────────────────────────────

def purge_observability(days: Optional[int] = None, *, dry_run: bool = False) -> dict:
    """删除超过保留期的 ``adh_llm_spans`` / ``adh_llm_traces``，返回各表处置明细。"""
    from backend.common.db import execute_query, execute_write

    keep_days = _days_or("RETENTION_LLM_SPAN_DAYS", days)
    out: dict[str, Any] = {"days": keep_days, "dry_run": bool(dry_run)}

    specs = (
        # spans 有 dt(DATE) + idx_dt；traces 用 created_at（服务端 NOW() 写入，同口径）
        ("adh_llm_spans", "dt < (CURDATE() - INTERVAL %s DAY)"),
        ("adh_llm_traces", "created_at < (NOW() - INTERVAL %s DAY)"),
    )
    total = 0
    for table, predicate in specs:
        exists = execute_query(
            "SELECT 1 FROM information_schema.tables WHERE table_schema=DATABASE() "
            "AND table_name=%s", (table,), fetchone=True)
        if not exists:
            # 表不存在 = 观测未启用/尚未建表：记事实，不静默当"已清理 0 行"
            out[table] = {"skipped": "table_missing"}
            continue
        row = execute_query(
            f"SELECT COUNT(*) AS c FROM {table} WHERE {predicate}", (keep_days,), fetchone=True) or {}
        entry: dict[str, Any] = {"expired_rows": int(row.get("c") or 0)}
        if entry["expired_rows"] and not dry_run:
            entry["deleted"] = int(execute_write(f"DELETE FROM {table} WHERE {predicate}",
                                                 (keep_days,)) or 0)
            total += entry["deleted"]
        out[table] = entry
    out["deleted_total"] = total
    return out


# ── ② 超期会话剥明细（不删正文）─────────────────────────────────────

def _strip_details(meta: dict) -> tuple[dict, bool]:
    """剥掉未经展示治理的中间产物，返回 (新 meta, 是否变更)。

    只动明细，不动 ``content``/``reply``（正文是独立列，不在这里）。
    """
    new = dict(meta)
    changed = False

    tool_calls = new.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        slim: list = []
        for tc in tool_calls:
            if isinstance(tc, dict) and "result" in tc:
                item = {k: v for k, v in tc.items() if k != "result"}
                item["result_omitted"] = True
                slim.append(item)
                changed = True
            else:
                slim.append(tc)
        new["tool_calls"] = slim

    for key in ("thinking", "ai_raw_response", "process"):
        if key in new:
            new.pop(key, None)
            changed = True

    # 消息级结果集（已治理输出，报表要用）：只截行数，不清空
    result = new.get("result")
    if isinstance(result, dict) and isinstance(result.get("rows"), list):
        from backend.common import config
        keep = max(0, int(config.RETENTION_RESULT_ROWS_KEEP or 0))
        rows = result["rows"]
        if len(rows) > keep:
            patched = dict(result)
            patched["rows"] = rows[:keep]
            patched["rows_trimmed"] = True
            patched["rows_total"] = len(rows)
            new["result"] = patched
            changed = True

    stages = new.get("progressStages")
    if isinstance(stages, list) and len(stages) > PROGRESS_STAGES_KEEP:
        new["progressStages"] = stages[-PROGRESS_STAGES_KEEP:]
        changed = True

    if changed:
        new["storage_trimmed"] = True   # 读路径可见：这一回合的明细已按保留策略清理
    return new, changed


def trim_stale_conversations(days: Optional[int] = None, *, dry_run: bool = False,
                             max_conversations: int = MAX_CONVERSATIONS_PER_RUN,
                             conversation_ids: Optional[Iterable[int]] = None) -> dict:
    """把超过保留期未更新的会话消息剥明细（正文保留），返回处置汇总。

    幂等：只处理 ``storage_state='full'`` 的行，已 trimmed 的不再动，重复执行不重复计数。

    ``conversation_ids`` 给定时只处理这些会话（回归与人工定点核验用）——不限定的
    全表扫描在测试里会连真实用户数据一起裁，那不是隔离而是事故。
    """
    from backend.common.db import DBConnection, execute_query
    from backend.core import conversation_store as store

    keep_days = _days_or("RETENTION_CONVERSATION_TRIM_DAYS", days)
    out: dict[str, Any] = {"days": keep_days, "dry_run": bool(dry_run),
                           "conversations": 0, "messages_trimmed": 0,
                           "bytes_saved": 0, "meta_corrupted": 0}

    table_ready = execute_query(
        "SELECT 1 FROM information_schema.tables WHERE table_schema=DATABASE() "
        "AND table_name=%s", (store.TABLE,), fetchone=True)
    if not table_ready:
        raise RuntimeError(f"拆行消息表 {store.TABLE} 不存在，请先应用 "
                           "docker/mysql/conversation_messages_split_migration.sql")

    scoped = [int(x) for x in (conversation_ids or [])]
    sql = ("SELECT c.id AS conversation_id FROM adh_conversations c "
           f"WHERE c.updated_at < (NOW() - INTERVAL %s DAY) "
           f"  AND EXISTS (SELECT 1 FROM {store.TABLE} m "
           "              WHERE m.conversation_id = c.id AND m.storage_state = 'full') ")
    params: list = [keep_days]
    if scoped:
        sql += f"  AND c.id IN ({','.join(['%s'] * len(scoped))}) "
        params.extend(scoped)
    sql += "ORDER BY c.updated_at ASC LIMIT %s"
    params.append(int(max_conversations))
    rows = execute_query(sql, tuple(params)) or []
    conv_ids = [int(r["conversation_id"]) for r in rows]
    out["conversations"] = len(conv_ids)
    if not conv_ids:
        return out

    with DBConnection() as conn:
        conn.begin()
        with conn.cursor() as cur:
            for cid in conv_ids:
                cur.execute(
                    f"SELECT seq, meta FROM {store.TABLE} "
                    f"WHERE conversation_id=%s AND storage_state='full'", (cid,))
                pending = cur.fetchall() or []
                for row in pending:
                    raw = row.get("meta")
                    if isinstance(raw, (bytes, bytearray)):
                        raw = raw.decode("utf-8", "ignore")
                    try:
                        meta = json.loads(raw) if raw else {}
                        if not isinstance(meta, dict):
                            raise ValueError("meta 不是对象")
                    except ValueError as exc:
                        # 损坏行不覆盖（覆盖会把"损坏"洗成"已清理"），只计数并留痕
                        out["meta_corrupted"] += 1
                        logger.error("会话 %s 消息 seq=%s meta 损坏，跳过裁剪: %s",
                                     cid, row.get("seq"), exc)
                        continue
                    new_meta, changed = _strip_details(meta)
                    new_json = json.dumps(new_meta, ensure_ascii=False, default=str)
                    if changed:
                        out["bytes_saved"] += max(0, len(raw or "") - len(new_json))
                    if dry_run:
                        out["messages_trimmed"] += 1 if changed else 0
                        continue
                    # 无变化的行也翻状态：否则每次任务都要重读重判（"已处理"要留得下来）
                    cur.execute(
                        f"UPDATE {store.TABLE} SET meta=%s, storage_state='trimmed' "
                        f"WHERE conversation_id=%s AND seq=%s", (new_json, cid, int(row["seq"])))
                    if changed:
                        out["messages_trimmed"] += 1
        if dry_run:
            conn.rollback()
        else:
            conn.commit()

    if out["meta_corrupted"]:
        logger.error("保留策略完成但有 %s 条消息 meta 损坏未裁（需人工核查）",
                     out["meta_corrupted"])
    return out


# ── 任务入口（celery 壳经 core.task_runtime 回调表调用）──────────────

def summarize(result: dict) -> str:
    """把执行结果压成任务监控页一行摘要（不编数字，全取实测值）。"""
    obs = result.get("observability") or {}
    trim = result.get("conversation_trim") or {}
    bits = [f"观测流水删除 {obs.get('deleted_total', 0)} 行（保留 {obs.get('days')} 天）"]
    if trim.get("conversations"):
        bits.append(f"会话明细裁剪 {trim['conversations']} 个会话 / "
                    f"{trim['messages_trimmed']} 条消息，省 {int(trim.get('bytes_saved') or 0) // 1024}KB")
    if trim.get("meta_corrupted"):
        bits.append(f"meta 损坏待人工核查 {trim['meta_corrupted']} 条")
    if result.get("dry_run"):
        bits.append("dry-run（未落库）")
    return "；".join(bits)


def run_retention(*, dry_run: bool = False, obs_days: Optional[int] = None,
                  conversation_days: Optional[int] = None) -> dict:
    """一次性执行两类保留动作，返回汇总（供任务监控页展示）。

    任一子步骤抛错都原样上抛：清理失败必须让任务监控页显示 failed，
    而不是把"流水还在涨"掩成"这一轮没什么可清"。
    """
    obs = purge_observability(obs_days, dry_run=dry_run)
    trim = trim_stale_conversations(conversation_days, dry_run=dry_run)
    result = {"dry_run": bool(dry_run), "observability": obs, "conversation_trim": trim}
    result["summary"] = summarize(result)
    return result
