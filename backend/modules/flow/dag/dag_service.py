"""DAG 数据访问层 — 工作流定义 / 运行实例 / 节点实例 / 水位线。

表：adh_dag_workflows / adh_dag_runs / adh_dag_node_runs / adh_sync_watermarks。

分布式口径（distributed-first）：
- 任务表为唯一队列真值；节点认领一律条件更新（queued→running 写租约），
  两实例并发只执行一次；
- run_key 唯一键幂等，重复投递不产生重复运行；
- 水位线比较推进（advance 只前进不回退），重复投递不产生重复副作用。

失败暴露口径（no-silent-degradation）：本层不吞写入失败，SQL/约束错误直接抛出。
"""

import json
import logging
import time as _time
from datetime import datetime, timedelta
from typing import Optional

from backend.common.db import execute_query, execute_write

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 租约上限：workflow.timeout_seconds 是「整轮预算」（本项目 8 小时），直接当租约
# 会让卡死的 run 在 8 小时里一直显示 running、没有任何回收点。认领时最多给 1 小时，
# 真正跑起来之后由执行体的心跳线程按 RUN_HEARTBEAT_LEASE_SECONDS 续短租约：
# 执行体一旦消失（worker 被杀 / 机器下线 / background 线程随 web 重启），租约 3 分钟
# 内过期，对账一轮就能收口，不用再等整轮预算。
_LEASE_CAP_SECONDS = 3600

# 心跳：执行体每 60s 续一次 180s 的租约。单节点预算 max_seconds ≤ 1800s 由心跳覆盖，
# 正常长跑不会被误杀；执行体死掉后最多 3 分钟即可判死。
RUN_HEARTBEAT_INTERVAL_SECONDS = 60
RUN_HEARTBEAT_LEASE_SECONDS = 180

# queued 的容忍上限：排队行没有租约（从未被认领），只能按入队时间判「队列堵塞」。
# 单任务最长 ≤ 8 分钟，30 分钟可覆盖顺序排队；超时说明没有执行体在消费
# （worker 未起 / 代码版本不一致 import 失败 / 队列堆积），必须显式收口。
QUEUE_STALL_MINUTES = 30


def _lease_seconds(timeout_seconds: int) -> int:
    return min(max(60, int(timeout_seconds or 0)), _LEASE_CAP_SECONDS)


def _as_datetime(value):
    """把库里的时间值（datetime / ISO 字符串）统一成 naive datetime，解析不了回 None。"""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    try:
        return datetime.fromisoformat(str(value).strip().replace("T", " "))
    except ValueError:
        return None


def is_stale_run(row: dict) -> bool:
    """运行实例是否「失联/堵塞」：running 看租约过期，queued 看排队超时。

    两态判据不同：running 由心跳续租，过期即执行体不在了；queued 从未被认领、
    本就没有租约，只能按入队时间判（阈值 ``QUEUE_STALL_MINUTES``）。判据与
    ``cleanup_stale_runs`` 同源：清单标记什么，收口就收什么，不会「标了不收」。
    """
    status = str(row.get("status") or "")
    if status not in ("queued", "running"):
        return False
    if status == "queued":
        created = _as_datetime(row.get("created_at")) or _as_datetime(row.get("started_at"))
        return bool(created and created <= datetime.now() - timedelta(minutes=QUEUE_STALL_MINUTES))
    lease = _as_datetime(row.get("lease_expires_at"))
    if lease is not None:
        return lease <= datetime.now()
    started = _as_datetime(row.get("started_at")) or _as_datetime(row.get("created_at"))
    return bool(started and started <= datetime.now() - timedelta(minutes=10))


def parse_day_bound(value: Optional[str], label: str) -> Optional[datetime]:
    """把 ``YYYY-MM-DD`` 解析成当天零点（止日期由调用方加一天减一秒）。

    非法格式直接抛 ValueError（fail-loud）：静默忽略会把「筛了但没筛」当成空清单，
    用户会误判成“这段时间没有运行”。
    """
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        return datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"{label}格式应为 YYYY-MM-DD（当前：{text}）")


def _escape_like(keyword: str) -> str:
    """LIKE 通配符转义：任务名里的 % / _ 按字面量匹配，不当通配。"""
    return keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _generate_id() -> int:
    return int(_time.time() * 1000000)


def _json_loads(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
    return value


def _dataflow_summary(graph: dict) -> str:
    """列表展示用数据流摘要：源表 → 目标表（UI 资源规范：显示可读名）。"""
    sources, targets = [], []
    for node in (graph or {}).get("nodes") or []:
        config = node.get("config") or {}
        if node.get("type") == "materialize":
            if config.get("dataset"):
                sources.append(str(config["dataset"]))
            if config.get("target_datasource") and config.get("target_table"):
                targets.append(f"{config['target_datasource']}.{config['target_table']}")
        elif node.get("type") == "sync":
            if config.get("source_datasource") and config.get("source_table"):
                sources.append(f"{config['source_datasource']}.{config['source_table']}")
            if config.get("target_datasource") and config.get("target_table"):
                targets.append(f"{config['target_datasource']}.{config['target_table']}")
        elif node.get("type") == "sql_task":
            if config.get("source_datasource"):
                sources.append(config["source_datasource"])
            tgt = config.get("target") or {}
            if tgt.get("datasource") and tgt.get("table"):
                targets.append(f"{tgt['datasource']}.{tgt['table']}")
    src = "、".join(dict.fromkeys(sources)) or "—"
    dst = "、".join(dict.fromkeys(targets)) or "—"
    return f"{src} → {dst}"


class DagService:
    """DAG 工作流与运行实例的持久化操作。"""

    # ── Workflows ─────────────────────────────────────────────────

    @staticmethod
    def _sync_lineage(wf_id: int, action: str) -> None:
        """编排变更后同步定义层血缘（派生视图随事实源收敛，不靠人记得跑脚本）。

        为什么挂在服务层：血缘图的事实源是 graph_json，不是血缘表本身。只靠
        `rebuild_lineage` 手动重建，会让「删了旧 DAG / 改了目标表」之后图仍保留旧
        依赖边（历史缺陷：旧域边的目标表与新域边并存，用户据此下钻得到不存在的
        链路）。这里先对账清失效段落再补齐，重跑应 0 变更。

        失败不回滚编排保存（编排是事实源），但必须留 error 日志可诊断；
        兜底重算入口仍是 `rebuild_lineage`。
        """
        try:
            from backend.modules.gov.services.lineage_service import sync_workflow_lineage
            result = sync_workflow_lineage(wf_id)
            logger.info("[dag] %s 工作流 %s 后同步血缘: %s", action, wf_id, result)
        except Exception:  # noqa: BLE001 派生视图失败不得阻断编排保存
            logger.exception("[dag] %s 工作流 %s 后同步血缘失败（图可能停在旧编排，"
                             "请用血缘重建入口重算）", action, wf_id)

    def create_workflow(self, data: dict, owner_id: int, workspace_id: int = 0) -> int:
        wf_id = _generate_id()
        kind = data.get("kind") or "dag"
        if kind not in ("simple", "dag"):
            kind = "dag"
        changed = execute_write(
            "INSERT INTO adh_dag_workflows "
            "(id, name, description, kind, graph_json, version, cron_expression, timezone, "
            " is_active, workspace_id, owner_id, timeout_seconds, max_retries, "
            " created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                wf_id,
                data["name"],
                data.get("description", ""),
                kind,
                json.dumps(data["graph_json"]),
                data.get("cron_expression") or None,
                data.get("timezone") or "Asia/Shanghai",
                1 if data.get("is_active", True) else 0,
                workspace_id,
                owner_id,
                int(data.get("timeout_seconds") or 3600),
                int(data.get("max_retries") or 0),
                _now(), _now(),
            ),
        )
        if changed:
            self._sync_lineage(wf_id, "新建")
        return wf_id if changed else 0

    def update_workflow(self, wf_id: int, data: dict, expected_version: int = None) -> bool:
        """更新工作流（graph_json 变更时 version+1，乐观锁防并发覆盖）。"""
        if not data:
            return True
        sets, params = [], []
        for key in ("name", "description", "cron_expression", "timezone"):
            if key in data:
                sets.append(f"{key} = %s")
                params.append(data[key])
        for key in ("is_active",):
            if key in data:
                sets.append(f"{key} = %s")
                params.append(1 if data[key] else 0)
        for key in ("timeout_seconds", "max_retries"):
            if key in data:
                sets.append(f"{key} = %s")
                params.append(int(data[key]))
        if "graph_json" in data:
            sets.append("graph_json = %s")
            params.append(json.dumps(data["graph_json"]))
            sets.append("version = version + 1")
        if not sets:
            return True
        sets.append("updated_at = %s")
        params.append(_now())
        params.append(wf_id)
        where = "id = %s"
        if expected_version is not None:
            where += " AND version = %s"
            params.append(expected_version)
        changed = execute_write(
            f"UPDATE adh_dag_workflows SET {', '.join(sets)} WHERE {where}", params) > 0
        if changed and ("graph_json" in data or "name" in data):
            self._sync_lineage(wf_id, "更新")
        return changed

    def get_workflow(self, wf_id: int) -> Optional[dict]:
        rows = execute_query("SELECT * FROM adh_dag_workflows WHERE id = %s", (wf_id,), fetchone=True)
        if rows:
            rows["graph_json"] = _json_loads(rows.get("graph_json")) or {"nodes": [], "edges": []}
        return rows

    def list_workflows(self, workspace_id: int = 0, page: int = 1, size: int = 20) -> dict:
        where, params = "WHERE 1=1", []
        if workspace_id:
            where += " AND workspace_id = %s"
            params.append(workspace_id)
        total = execute_query(f"SELECT COUNT(*) AS total FROM adh_dag_workflows {where}",
                              params, fetchone=True)["total"]
        rows = execute_query(
            f"SELECT id, name, description, kind, graph_json, cron_expression, timezone, is_active, "
            f"version, workspace_id, owner_id, last_run_at, last_status, run_count, "
            f"timeout_seconds, max_retries, created_at, updated_at "
            f"FROM adh_dag_workflows {where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
            params + [size, (page - 1) * size]) or []
        items = []
        for row in rows:
            graph = _json_loads(row.pop("graph_json", None)) or {}
            row["kind"] = row.get("kind") or "dag"
            row["dataflow_summary"] = _dataflow_summary(graph)
            items.append(row)
        # 上次状态以**最近一条运行实例**为准：`adh_dag_workflows.last_status` 只在执行器
        # 正常收尾时回写，被运维中止/worker 被杀/排队中被取消的 run 不会写 → 清单显示
        # 上一次的旧状态，与运行历史里最新一条自相矛盾。
        latest = self.latest_run_by_workflow([r["id"] for r in items])
        for row in items:
            hit = latest.get(row["id"])
            if hit:
                row["last_status"] = hit.get("status")
                # 与运行历史的「开始」同口径（原 last_run_at 是收尾时刻，同一行两页对不上）
                row["last_run_at"] = hit.get("started_at") or hit.get("created_at") \
                    or row.get("last_run_at")
        return {"total": total, "page": page, "size": size, "items": items}

    def latest_run_by_workflow(self, workflow_ids: list) -> dict:
        """每个工作流最近一条运行实例（按 id 最大 = 真正最后发起的那次）。

        口径：有 queued/running 的实例时以它为“当前状态”（并行派发时先结束的旧 run
        不能把清单改回已完成），否则取 id 最大的终态实例。
        """
        ids = [int(w) for w in (workflow_ids or []) if w]
        if not ids:
            return {}
        placeholders = ",".join(["%s"] * len(ids))
        rows = execute_query(
            "SELECT r.workflow_id, r.id, r.status, r.started_at, r.created_at, r.finished_at "
            "FROM adh_dag_runs r JOIN (SELECT workflow_id, MAX(id) AS mid FROM adh_dag_runs "
            "      WHERE workflow_id IN (" + placeholders + ") GROUP BY workflow_id) m "
            "  ON m.mid = r.id",
            tuple(ids)) or []
        latest = {int(r["workflow_id"]): r for r in rows}
        active = execute_query(
            "SELECT r.workflow_id, r.id, r.status, r.started_at, r.created_at, r.finished_at "
            "FROM adh_dag_runs r JOIN (SELECT workflow_id, MAX(id) AS mid FROM adh_dag_runs "
            "      WHERE workflow_id IN (" + placeholders + ")"
            "        AND status IN ('queued','running') GROUP BY workflow_id) m "
            "  ON m.mid = r.id",
            tuple(ids)) or []
        for r in active:
            latest[int(r["workflow_id"])] = r
        return latest

    def delete_workflow(self, wf_id: int) -> bool:
        """删除工作流（含运行实例）；运行中的工作流禁止删除（终态保护）。"""
        active = execute_query(
            "SELECT COUNT(*) AS total FROM adh_dag_runs "
            "WHERE workflow_id = %s AND status IN ('queued','running')",
            (wf_id,), fetchone=True)["total"]
        if active:
            raise ValueError("工作流存在运行中的实例，不能删除")
        execute_write("DELETE FROM adh_dag_node_runs WHERE workflow_id = %s", (wf_id,))
        execute_write("DELETE FROM adh_dag_runs WHERE workflow_id = %s", (wf_id,))
        removed = execute_write("DELETE FROM adh_dag_workflows WHERE id = %s", (wf_id,)) > 0
        if removed:
            self._sync_lineage(wf_id, "删除")
        return removed

    def delete_run(self, run_id: int) -> bool:
        """删除单条运行记录（含节点实例明细）。

        只允许删已收尾的实例：排队/执行中的 run 被删掉后，仍持有租约的执行体
        会继续写节点行与统计，结果是「删了又有」+ 半截数据（no-silent-degradation）。
        必须先 stop 再删（run_count/last_status 由 latest_run_by_workflow 从剩余
        运行实例重新推导，不靠回写缓存，无需补偿）。
        """
        run = self.get_run(run_id)
        if not run:
            return False
        if run.get("status") in ("queued", "running"):
            raise ValueError("运行实例仍在排队/执行中，请先停止再删除")
        execute_write("DELETE FROM adh_dag_node_runs WHERE run_id = %s", (run_id,))
        return execute_write("DELETE FROM adh_dag_runs WHERE id = %s", (run_id,)) > 0

    def list_active_scheduled(self) -> list:
        """供 Beat 加载调度：启用中且 owner 已认领（owner_id<=0 不调度，fail-closed）。"""
        return execute_query(
            "SELECT * FROM adh_dag_workflows WHERE is_active = 1 AND cron_expression IS NOT NULL "
            " AND cron_expression != '' AND owner_id > 0") or []

    # ── Runs ──────────────────────────────────────────────────────

    @staticmethod
    def _with_params(row: Optional[dict]) -> Optional[dict]:
        """把 params_json 反序列化成 dict 再交付调用方（空值统一为 {}）。"""
        if row:
            row["params_json"] = _json_loads(row.get("params_json")) or {}
        return row

    def create_run(self, workflow_id: int, run_key: str, trigger_type: str,
                   workspace_id: int = 0, params: Optional[dict] = None) -> dict:
        """幂等创建运行实例（run_key 唯一键；重复投递返回既有 run）。

        `params` 是**本次运行**的参数（当前只有物化时间窗口 start_date/end_date），
        由 `dag_validator.validate_run_params` 校验后才允许落库。它不能写进节点
        config：同一份编排定义要被日常调度与历史初始化共用，日期写死进图就没法
        用 DAG 自己跑历史了（用户 2026-11-24 明确要求）。
        """
        existing = execute_query("SELECT * FROM adh_dag_runs WHERE run_key = %s",
                                 (run_key,), fetchone=True)
        if existing:
            return self._with_params(existing) or {}
        run_id = _generate_id()
        execute_write(
            "INSERT INTO adh_dag_runs "
            "(id, workflow_id, run_key, trigger_type, status, workspace_id, "
            " params_json, started_at, created_at) "
            "VALUES (%s, %s, %s, %s, 'queued', %s, %s, %s, %s)",
            (run_id, workflow_id, run_key, trigger_type, workspace_id,
             json.dumps(params, ensure_ascii=False) if params else None, _now(), _now()))
        created = execute_query("SELECT * FROM adh_dag_runs WHERE id = %s", (run_id,), fetchone=True)
        if not created:
            # 并发下被另一实例抢先创建，回到唯一键上的既有行
            created = execute_query("SELECT * FROM adh_dag_runs WHERE run_key = %s",
                                    (run_key,), fetchone=True)
        return self._with_params(created) or {}

    def claim_run(self, run_id: int, worker_id: str, timeout_seconds: int) -> bool:
        """条件更新认领：queued→running 写租约，两实例并发只认领成功一次。"""
        lease = (datetime.now() + timedelta(seconds=_lease_seconds(timeout_seconds))).strftime("%Y-%m-%d %H:%M:%S")
        return execute_write(
            "UPDATE adh_dag_runs SET status='running', worker_id=%s, lease_expires_at=%s "
            "WHERE id = %s AND status = 'queued'",
            (worker_id, lease, run_id)) == 1

    def renew_run_lease(self, run_id: int, timeout_seconds: int) -> bool:
        lease = (datetime.now() + timedelta(seconds=_lease_seconds(timeout_seconds))).strftime("%Y-%m-%d %H:%M:%S")
        return execute_write(
            "UPDATE adh_dag_runs SET lease_expires_at=%s WHERE id=%s AND status='running'",
            (lease, run_id)) == 1

    def finish_run(self, run_id: int, status: str, error_code: str = "",
                   error_message: str = "", stats: dict = None) -> bool:
        """终态保护：仅 running/queued 可结束，重复投递不覆盖终态。"""
        changed = execute_write(
            "UPDATE adh_dag_runs SET status=%s, error_code=%s, error_message=%s, "
            " stats_json=%s, finished_at=%s "
            "WHERE id=%s AND status IN ('queued','running')",
            (status, error_code, error_message, json.dumps(stats or {}), _now(), run_id))
        return changed == 1

    def cancel_run(self, run_id: int) -> bool:
        """终止 run，并把仍在 queued/running 的节点一并收尾。

        只改 run 不收节点，会让节点永远停在 running（列表显示“运行中”、执行体
        却已随 worker 消失），是“任务列表状态与历史结果对不上”的直接来源。
        """
        finished = _now()
        changed = execute_write(
            "UPDATE adh_dag_runs SET status='cancelled', error_code='CANCELLED', "
            " error_message=%s, finished_at=%s "
            "WHERE id=%s AND status IN ('queued','running')",
            ("已手动终止：正在执行的节点会跑完当前批次后停止，未开始的节点不再执行",
             finished, run_id)) == 1
        if changed:
            execute_write(
                "UPDATE adh_dag_node_runs SET status='cancelled', error_code='CANCELLED', "
                " error_message=%s, finished_at=%s "
                "WHERE run_id=%s AND status IN ('queued','running')",
                ("运行已被取消，节点执行体不再回报结果", finished, run_id))
        return changed

    def get_run(self, run_id: int) -> Optional[dict]:
        row = execute_query("SELECT * FROM adh_dag_runs WHERE id = %s", (run_id,), fetchone=True)
        if row:
            row["stats_json"] = _json_loads(row.get("stats_json")) or {}
            row["is_stale"] = is_stale_run(row)
        return self._with_params(row)

    def get_run_by_key(self, run_key: str) -> Optional[dict]:
        row = execute_query("SELECT * FROM adh_dag_runs WHERE run_key = %s",
                            (run_key,), fetchone=True)
        return self._with_params(row)

    @staticmethod
    def _build_run_filters(workflow_id: int = None, status: str = None,
                           trigger_type: str = None, start_from: str = None,
                           start_to: str = None, q: str = None):
        """运行清单与总数共用的谓词（口径单源，条件全部下推 SQL，不在前端过滤）。

        时间维度按 ``COALESCE(started_at, created_at)`` 判定：排队/未启动的实例
        started_at 为空，只用 started_at 会让“今天新建的”全部漏出时间窗。
        """
        where, params = "WHERE 1=1", []
        if workflow_id:
            where += " AND r.workflow_id = %s"
            params.append(int(workflow_id))
        if status:
            where += " AND r.status = %s"
            params.append(status)
        if trigger_type:
            where += " AND r.trigger_type = %s"
            params.append(trigger_type)
        day_from = parse_day_bound(start_from, "开始日期")
        if day_from:
            where += " AND COALESCE(r.started_at, r.created_at) >= %s"
            params.append(day_from.strftime("%Y-%m-%d 00:00:00"))
        day_to = parse_day_bound(start_to, "结束日期")
        if day_to:
            where += " AND COALESCE(r.started_at, r.created_at) <= %s"
            params.append(day_to.strftime("%Y-%m-%d 23:59:59"))
        keyword = (q or "").strip()
        if keyword:
            where += " AND w.name LIKE %s"
            params.append(f"%{_escape_like(keyword)}%")
        return where, params

    def list_runs(self, workflow_id: int = None, status: str = None,
                  trigger_type: str = None, start_from: str = None,
                  start_to: str = None, q: str = None,
                  page: int = 1, size: int = 20) -> dict:
        where, params = self._build_run_filters(
            workflow_id=workflow_id, status=status, trigger_type=trigger_type,
            start_from=start_from, start_to=start_to, q=q)
        base_from = ("FROM adh_dag_runs r "
                     "LEFT JOIN adh_dag_workflows w ON w.id = r.workflow_id ")
        # 失联判据与服务端收口同源：running 看租约、queued 看排队超时，都在库里算，
        # 避免应用机与 DB 时区不一致时忽真忽假。
        stale_expr = ("( (r.status = 'running' AND ("
                      "   (r.lease_expires_at IS NOT NULL AND r.lease_expires_at <= NOW())"
                      "   OR (r.lease_expires_at IS NULL"
                      "       AND COALESCE(r.started_at, r.created_at) <= NOW() - INTERVAL 10 MINUTE)"
                      "  ))"
                      f"  OR (r.status = 'queued'"
                      f"      AND COALESCE(r.created_at, r.started_at) <= NOW() - INTERVAL {int(QUEUE_STALL_MINUTES)} MINUTE)"
                      ") AS is_stale ")

        def _fetch():
            total = execute_query(f"SELECT COUNT(*) AS total {base_from} {where}",
                                  params, fetchone=True)["total"]
            rows = execute_query(
                f"SELECT r.*, w.name AS workflow_name, {stale_expr},"
                f"(SELECT COUNT(*) FROM adh_dag_node_runs n WHERE n.run_id = r.id) AS node_count "
                f"{base_from} {where} ORDER BY r.created_at DESC, r.id DESC LIMIT %s OFFSET %s",
                params + [size, (page - 1) * size])
            for row in rows or []:
                row["stats_json"] = _json_loads(row.get("stats_json")) or {}
                row["params_json"] = _json_loads(row.get("params_json")) or {}
            # 清单与详情同源：读取/写入行数也以节点终态反算（stats_json 可能为空）
            derived = self.aggregate_stats_for_runs(
                [r["id"] for r in rows or []],
                {r["id"]: int(((r.get("stats_json") or {}).get("nodes_total") or 0))
                 for r in rows or []})
            for row in rows or []:
                row["stats_json"] = {**row["stats_json"], **derived.get(row["id"], {})}
            return total, rows or []

        total, rows = _fetch()
        # 清单就是用户观察「为什么一直在跑」的入口：一旦看到租约过期的实例，先按服务端
        # 同一判据收口成 timeout/ORPHANED 再重取。beat 对账是 60s 一轮，但 background
        # 模式或不跑 beat 的环境没有这轮，残留实例会永远停在「运行中」。
        if any(int(row.get("is_stale") or 0) for row in rows):
            swept = self.cleanup_stale_runs()
            logger.info("[dag] 运行清单发现失联实例，已按租约收口: %s", swept)
            total, rows = _fetch()
        return {"total": total, "page": page, "size": size, "items": rows}

    def cleanup_stale_runs(self, timeout_minutes: int = 10) -> dict:
        """卡死清理：租约到期的 running / 排队超时的 queued / 孤儿节点按 timeout 收尾（幂等）。

        - running 判据只看**租约**：执行循环每节点 ``renew_run_lease`` 续租，正常长跑
          不会被误杀。旧实现用 ``started_at <= now - 10min``，会把跑超过 10 分钟的物化
          全判超时（错状态；与 no-silent-degradation 相违，属掩盖而非收敛）。
        - queued 判据看**入队时间**：排队行没有租约，一直不认领就说明没有执行体在消费
          （worker 未起 / 版本不一致 import 失败），不收口就会永远显示「排队中」。
        - ``timeout_minutes`` 仅兜底没有租约的历史 running 行（``lease_expires_at IS NULL``）。
        """
        now = _now()
        legacy_cutoff = (datetime.now() - timedelta(
            minutes=max(1, int(timeout_minutes or 10)))).strftime("%Y-%m-%d %H:%M:%S")
        queue_cutoff = (datetime.now() - timedelta(
            minutes=QUEUE_STALL_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
        runs = execute_write(
            "UPDATE adh_dag_runs SET status='timeout', error_code='LEASE_EXPIRED', "
            " error_message=%s, finished_at=%s "
            "WHERE status='running' AND (lease_expires_at <= %s "
            " OR (lease_expires_at IS NULL AND started_at <= %s))",
            ("执行体已失联（租约过期）：可能是 worker 退出、机器下线或进程重启，"
             "该实例已按超时收口；已写入目标表的数据不回滚", now, now, legacy_cutoff))
        # queued 从未被认领：不写 finished 之外的节点状态会留下 queued 节点，一并收口。
        queued = execute_write(
            "UPDATE adh_dag_runs SET status='timeout', error_code='QUEUE_TIMEOUT', "
            " error_message=%s, finished_at=%s "
            "WHERE status='queued' AND COALESCE(created_at, started_at) <= %s",
            (f"排队超过 {QUEUE_STALL_MINUTES} 分钟仍未被任何执行体认领"
             "（执行体未运行、版本不一致或队列堵塞），已按超时收口", now, queue_cutoff))
        # 节点：自身租约过期 / 无租约历史行超时 / 所属 run 已不在执行态（孤儿节点）。
        # 孤儿节点是“run 已终结但节点仍 running”的残留，必须收口，否则列表永远在跑。
        nodes = execute_write(
            "UPDATE adh_dag_node_runs SET status='timeout', error_code='ORPHANED', finished_at=%s "
            "WHERE status IN ('queued','running') AND (lease_expires_at <= %s "
            " OR (lease_expires_at IS NULL AND started_at <= %s) "
            " OR run_id IN (SELECT id FROM adh_dag_runs WHERE status NOT IN ('queued','running')))",
            (now, now, legacy_cutoff))
        return {"dag_runs": runs, "dag_queued_runs": queued, "dag_node_runs": nodes}

    # ── Node Runs ─────────────────────────────────────────────────

    def create_node_run(self, run_id: int, workflow_id: int, node_key: str,
                        node_type: str, node_config: dict, attempt: int = 1) -> int:
        node_run_id = _generate_id()
        execute_write(
            "INSERT INTO adh_dag_node_runs "
            "(id, run_id, workflow_id, node_key, node_type, node_config, status, attempt, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'queued', %s, %s)",
            (node_run_id, run_id, workflow_id, node_key, node_type,
             json.dumps(node_config or {}), attempt, _now()))
        return node_run_id

    def claim_node_run(self, node_run_id: int, worker_id: str, timeout_seconds: int) -> bool:
        lease = (datetime.now() + timedelta(seconds=max(60, timeout_seconds))).strftime("%Y-%m-%d %H:%M:%S")
        return execute_write(
            "UPDATE adh_dag_node_runs SET status='running', worker_id=%s, "
            " lease_expires_at=%s, started_at=%s "
            "WHERE id=%s AND status='queued'",
            (worker_id, lease, _now(), node_run_id)) == 1

    def finish_node_run(self, node_run_id: int, status: str, rows_read: int = 0,
                        rows_written: int = 0, elapsed_ms: int = 0,
                        error_code: str = "", error_message: str = "",
                        result: Optional[dict] = None) -> bool:
        """落节点终态；result 为可审计的执行结果摘要（物化分区/对账/写入策略）。

        result 只存平台自己产出的结构化摘要（不含数据行、凭据、生成 SQL），
        供执行日志页展示“写了哪些分区、是否对账通过”，不当成功判定依据。
        """
        return execute_write(
            "UPDATE adh_dag_node_runs SET status=%s, rows_read=%s, rows_written=%s, "
            " elapsed_ms=%s, error_code=%s, error_message=%s, result_json=%s, finished_at=%s "
            "WHERE id=%s AND status='running'",
            (status, rows_read, rows_written, elapsed_ms, error_code, error_message,
             json.dumps(result, ensure_ascii=False) if result else None,
             _now(), node_run_id)) == 1

    def skip_node_run(self, run_id: int, node_key: str, node_type: str,
                      reason: str, attempt: int = 1) -> int:
        node_run_id = _generate_id()
        execute_write(
            "INSERT INTO adh_dag_node_runs "
            "(id, run_id, node_key, node_type, status, attempt, error_code, error_message, "
            " started_at, finished_at, created_at) "
            "VALUES (%s, %s, %s, %s, 'skipped', %s, 'UPSTREAM_FAILED', %s, %s, %s, %s)",
            (node_run_id, run_id, node_key, node_type, attempt, reason, _now(), _now(), _now()))
        return node_run_id

    def list_node_runs(self, run_id: int) -> list:
        rows = execute_query(
            "SELECT * FROM adh_dag_node_runs WHERE run_id = %s ORDER BY created_at ASC",
            (run_id,)) or []
        for row in rows:
            row["node_config"] = _json_loads(row.get("node_config")) or {}
            row["result_json"] = _json_loads(row.get("result_json")) or {}
        return rows

    def latest_node_status(self, run_id: int) -> dict:
        """run 内每个 node_key 的最新 attempt 状态（调度/重跑判断用）。"""
        rows = execute_query(
            "SELECT n.node_key, n.status FROM adh_dag_node_runs n "
            "JOIN (SELECT node_key, MAX(attempt) AS ma FROM adh_dag_node_runs "
            "      WHERE run_id = %s GROUP BY node_key) m "
            " ON m.node_key = n.node_key AND m.ma = n.attempt WHERE n.run_id = %s",
            (run_id, run_id)) or []
        return {row["node_key"]: row["status"] for row in rows}

    def count_attempts(self, run_id: int, node_key: str) -> int:
        return execute_query(
            "SELECT COUNT(*) AS total FROM adh_dag_node_runs WHERE run_id = %s AND node_key = %s",
            (run_id, node_key), fetchone=True)["total"]

    def aggregate_run_stats(self, run_id: int, total_nodes: int = 0) -> dict:
        """按节点终态反算运行统计（运行页与节点明细必须同源）。

        `stats_json` 只在执行器正常收尾时写一次：被运维中止、进程被杀、仍在跑的
        run 里它是空的，运行页若只读它就会出现「0/15 成功」与节点表全绿互相矛盾。
        这里以每个节点最新 attempt 的终态为唯一真值（success/failed/skipped/
        cancelled 分别计数，行数按最新 attempt 累加）。
        """
        return self.aggregate_stats_for_runs([run_id], {run_id: total_nodes})[run_id]

    def aggregate_stats_for_runs(self, run_ids: list, total_nodes: dict | None = None) -> dict:
        """批量版本（运行历史清单与详情共用同一口径，避免两页各算一套）。

        清单页一次查整页 run 的节点终态（GROUP BY run_id），不会 N+1；
        未产生任何节点行的 run 返回全 0 骨架，调用方可直接渲染。
        """
        totals = total_nodes or {}
        ids = [int(r) for r in (run_ids or []) if r]
        out = {rid: {
            "nodes_total": int(totals.get(rid) or 0), "nodes_success": 0,
            "nodes_failed": 0, "nodes_skipped": 0, "nodes_cancelled": 0,
            "rows_read": 0, "rows_written": 0,
        } for rid in ids}
        if not ids:
            return out
        placeholders = ",".join(["%s"] * len(ids))
        rows = execute_query(
            "SELECT n.run_id, n.node_key, n.status, n.rows_read, n.rows_written "
            "FROM adh_dag_node_runs n JOIN (SELECT run_id, node_key, MAX(attempt) AS ma "
            "      FROM adh_dag_node_runs WHERE run_id IN (" + placeholders + ") "
            "      GROUP BY run_id, node_key) m "
            "  ON m.run_id = n.run_id AND m.node_key = n.node_key AND m.ma = n.attempt",
            tuple(ids)) or []
        per_run: dict = {}
        for row in rows:
            per_run.setdefault(int(row.get("run_id")), []).append(row)
        for rid, stats in out.items():
            nodes = per_run.get(rid) or []
            stats["nodes_total"] = max(int(stats["nodes_total"] or 0), len(nodes))
            for row in nodes:
                stats["rows_read"] += int(row.get("rows_read") or 0)
                stats["rows_written"] += int(row.get("rows_written") or 0)
                status = str(row.get("status") or "")
                if status == "success":
                    stats["nodes_success"] += 1
                elif status == "skipped":
                    stats["nodes_skipped"] += 1
                elif status == "cancelled":
                    stats["nodes_cancelled"] += 1
                elif status in ("failed", "timeout"):
                    stats["nodes_failed"] += 1
        return out

    # ── Watermarks（增量同步水位线：比较推进，幂等） ────────────────

    def get_watermark(self, node_key: str) -> Optional[str]:
        row = execute_query(
            "SELECT watermark_value FROM adh_sync_watermarks WHERE node_key = %s",
            (node_key,), fetchone=True)
        return row.get("watermark_value") if row else None

    def list_watermarks(self, workflow_id: int) -> dict:
        """该工作流各节点的「最后成功位置」，按节点 key 索引（编排图/详情页展示）。

        键是 `dag_executor` 注入的 `watermark_key = dag:{workflow_id}:{node_key}`；
        只按该前缀取值，`sync_task:{id}` 等别的水位线不会串进来。
        """
        prefix = f"dag:{int(workflow_id)}:"
        rows = execute_query(
            "SELECT node_key, watermark_value, last_rows, updated_at "
            "FROM adh_sync_watermarks WHERE node_key LIKE %s", (prefix + "%",)) or []
        out: dict = {}
        for row in rows:
            key = str(row.get("node_key") or "")
            if not key.startswith(prefix):
                continue
            out[key[len(prefix):]] = {
                "position": row.get("watermark_value"),
                "last_rows": int(row.get("last_rows") or 0),
                "updated_at": row.get("updated_at"),
            }
        return out

    def advance_watermark(self, node_key: str, value: str, rows: int = 0) -> bool:
        """推进水位线：仅当新值大于当前值才前进（字符串化比较，重复投递不回退）。"""
        current = self.get_watermark(node_key)
        if current is not None and str(value) <= str(current):
            return False
        execute_write(
            "INSERT INTO adh_sync_watermarks (id, node_key, watermark_value, last_rows, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE watermark_value = IF(VALUES(watermark_value) > watermark_value, "
            " VALUES(watermark_value), watermark_value), last_rows = VALUES(last_rows)",
            (_generate_id(), node_key, value, rows, _now(), _now()))
        return True

    # ── 调度运行统计回写 ─────────────────────────────────────────

    def mark_run_stats(self, workflow_id: int, status: str) -> None:
        execute_write(
            "UPDATE adh_dag_workflows SET last_run_at=%s, last_status=%s, "
            " run_count = run_count + 1 WHERE id=%s",
            (_now(), status, workflow_id))


dag_service = DagService()
