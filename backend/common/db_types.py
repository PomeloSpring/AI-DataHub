"""数据源 db_type 分类常量（L0）—— 引擎判定的最底层事实源。

为什么放在 common 而不是 `semantics.dialect`：分层契约（§0 依赖规则）禁止 L0 `common`
import backend 其他包，而 `common/db/datasource_db.py` 这类连接工厂**必须**知道
「这个 db_type 有没有 DB-API 连接」。把类型清单沉到 L0，`semantics.dialect` 再引上去，
两个方向都合规；否则就得在两层各抄一份清单（抄一份就漂一份）。

新增引擎时的落点：本模块只放「是不是某类源」的判定；方言族/引擎变体映射仍在
`semantics/dialect.py`（编译层关注点）。
"""

from __future__ import annotations

from typing import Any

# Elasticsearch 系 db_type 写法：配置校验、执行分派、连接工厂拒绝都读这里
ES_DB_TYPES: frozenset = frozenset({"es", "elasticsearch"})


def _norm(db_type: Any) -> str:
    return str(db_type or "").strip().lower()


def is_es_db_type(db_type: Any) -> bool:
    """db_type 是否 ES 型（原始写法判定；不走 engine_of 的回落链）。"""
    return _norm(db_type) in ES_DB_TYPES
