"""语义执行引擎编排 —— DataFusion 规划/校验 + 远程下推（Phase 6.2）。

remote_exec.rs 思路的 Python 重做：远程表下推为**远程 SQL 在源库执行、拉回 Arrow**，
DataFusion 只做规划/校验/跨源联邦；**不引本地 Rust 编译链**（连接器质量不足再评估
PyO3 扩展，届时必须锁同一 DataFusion 版本 ABI，见 adapter.py）。

护栏 §5 执行前强制（不满足即拒绝，fail-loud）：
- `force_limit` / `size_class=huge` / `query_mode=raw_source` → `bounded_query`
  自动补/收紧外层 LIMIT **后再校验**（缺 LIMIT 的扫描不得直接放行）；
- 仅 SELECT/WITH，禁 DDL/DML/多语句（`validate_sql`）；
- `timeout_sec` → 连接读超时强制；`max_rows` → 拉回行数上限（与 LIMIT 取严）。
"""

from __future__ import annotations

from typing import Any, Optional, Union

from backend.semantics.execution.connectors import get_connector
from backend.semantics.execution.session import SessionPool, get_session_pool, policy_fingerprint
from backend.semantics.models import Guardrail
from backend.semantics.sql_guard import bounded_query

_DEFAULT_LIMIT = 1000

GuardrailLike = Union[Guardrail, dict[str, Any], None]


def _guardrail(guardrail: GuardrailLike) -> Guardrail:
    if guardrail is None:
        return Guardrail()
    if isinstance(guardrail, Guardrail):
        return guardrail
    return Guardrail(**dict(guardrail))


def _need_limit(gr: Guardrail) -> bool:
    """护栏 §5：强制限流触发条件。"""
    return bool(gr.force_limit) or gr.size_class == "huge" or gr.query_mode == "raw_source"


class SemanticEngine:
    """执行编排：校验 → 限流强制 → 远程下推拉回 Arrow。"""

    def __init__(self, pool: Optional[SessionPool] = None):
        self._pool = pool if pool is not None else get_session_pool()

    # ── 会话（Phase 6.3 RLS/mask 视图化强制挂点） ──────────────

    def session(self, policy: Optional[dict[str, Any]] = None, *, extra: str = "") -> Any:
        """按 policy 指纹取分桶 SessionContext。"""
        return self._pool.get(policy_fingerprint(policy, extra=extra))

    # ── 护栏 §5 执行前强制 ────────────────────────────────────

    def bounded_sql(
        self,
        sql: str,
        guardrail: GuardrailLike = None,
        *,
        dialect: str = "mysql",
        max_rows: Optional[int] = None,
    ) -> tuple[str, bool]:
        """需要限流时自动补/收紧外层 LIMIT。返回 (sql, 是否强制限流)。"""
        gr = _guardrail(guardrail)
        need = _need_limit(gr)
        limit = int(max_rows or gr.max_rows or _DEFAULT_LIMIT)
        out = bounded_query(sql, limit=limit, dialect=dialect) if need else sql
        return out, need

    def validate(self, sql: str, *, require_limit: bool = False) -> None:
        """SQL 安全校验（只读/禁 DDL-DML/多语句/LIMIT）；不通过即拒绝（fail-loud）。"""
        from backend.core.query_executor import validate_sql

        ok, err = validate_sql(sql, require_limit=require_limit)
        if not ok:
            raise PermissionError(err or "SQL 未通过安全校验")

    # ── 远程下推执行 ──────────────────────────────────────────

    def execute_pushdown(
        self,
        sql: str,
        *,
        datasource_id: int = 0,
        db_type: str = "mysql",
        guardrail: GuardrailLike = None,
        dialect: str = "mysql",
        max_rows: Optional[int] = None,
    ) -> Any:
        """校验 + 限流强制后远程下推执行，拉回 Arrow 表。

        方言口径：`sql` 为 planner 产出的目标源方言；远程下推原样执行
        （内嵌 DataFusion 执行经 `connectors.base.to_pushdown_sql` 转换）。
        """
        gr = _guardrail(guardrail)
        sql_out, need = self.bounded_sql(sql, gr, dialect=dialect, max_rows=max_rows)
        self.validate(sql_out, require_limit=need)

        row_cap = max_rows or gr.max_rows
        connector = get_connector(db_type, datasource_id=datasource_id)
        return connector.execute_pushdown(
            sql_out,
            timeout_sec=gr.timeout_sec,
            max_rows=int(row_cap) if row_cap else None,
        )
