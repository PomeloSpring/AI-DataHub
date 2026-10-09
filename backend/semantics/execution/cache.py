"""引擎层结果缓存（计划 §6.2）—— 远程下推结果的短 TTL 缓存。

**性能旁路**（与既有缓存同口径）：命中加速、异常透明降级为未命中，绝不影响正确性。
值 = Arrow IPC 字节（无损往返，重复列名场景不入缓存）；key = 数据源 + SQL 指纹 + 行上限。
默认 TTL 60s，调用方按语义显式开启（`execute_pushdown(use_cache=True)`）。
"""

from __future__ import annotations

import hashlib
from typing import Optional

import pyarrow as pa

from backend.common.ttl_cache import TTLCache

result_cache = TTLCache(name="semantic_result", maxsize=256, ttl=60)


def cache_key(datasource_id: int, sql: str, max_rows: Optional[int]) -> str:
    digest = hashlib.sha256(sql.encode("utf-8")).hexdigest()[:16]
    return f"q:{int(datasource_id or 0)}:{digest}:{max_rows or 0}"


def to_ipc(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return sink.getvalue().to_pybytes()


def from_ipc(data: bytes) -> pa.Table:
    with pa.ipc.open_stream(pa.BufferReader(data)) as reader:
        return reader.read_all()
