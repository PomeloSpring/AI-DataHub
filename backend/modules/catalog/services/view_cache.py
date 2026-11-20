"""本体只读视图（canonical 文档 JSON / YAML / MD 投影）的进程内缓存。

为什么需要：`GET /catalog/ontology/models/{id}/view` 每次都要 json.loads + `hydrate_doc`
（两次字典查询 + 整份深拷贝）+ 序列化。实测 18 对象 / 138KB 文档 ≈ 0.6-1.1s/次，
而它是**只读投影**：同一模型正文 + 同一份字典 → 结果必然逐字节相同，重复计算纯属浪费。

失效口径（"有数据变动就清缓存"，且不依赖写入口逐个挂钩子）：
- 模型正文变化 → 签名含 `json_content` 的 sha1，自然 miss；
- 字典（`adh_metrics` / `adh_dimensions`）增删改 → 签名含两表的 `(COUNT, MAX(updated_at))` 指纹
  （两表 `updated_at` 均为 `ON UPDATE CURRENT_TIMESTAMP`，绑定关系变化也会刷新该行）；
- 两档都不变 → 命中，零计算返回；
- TTL 兜底（默认 600s）：防漏点（如绕过 ORM 的脚本改库）长期脏读。

为什么不用"写入口 bump"：字典写入路径分散（页面 CRUD / AS-BOT 工具 / 迁移脚本 / 字典收编），
漏一个就是长期脏视图；指纹自证更稳。缓存仅在 web 单进程内，多副本部署各自持有（无跨副本诉求）。

指纹取不到（DB 异常）时**不缓存**：宁可这次慢一点，也不返回可能过期的视图。
"""
from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict

from backend.common.db.metadata_db import get_metadata_conn

TTL_SEC = 600.0
MAX_ENTRIES = 24

_store: "OrderedDict[tuple, tuple[float, str, str]]" = OrderedDict()
_lock = threading.Lock()
_stats = {"hit": 0, "miss": 0, "expired": 0}


def dict_fingerprint() -> str:
    """口径字典指纹：两表 `(COUNT, MAX(updated_at))`，**一次往返**取完。

    只查聚合，不读业务列；任一表不可读则抛异常（调用方据此跳过缓存），
    绝不返回一个“看起来没变”的假指纹。
    """
    sql = (
        "SELECT (SELECT COUNT(*) FROM adh_metrics) AS mc,"
        " (SELECT COALESCE(MAX(updated_at), '') FROM adh_metrics) AS mu,"
        " (SELECT COUNT(*) FROM adh_dimensions) AS dc,"
        " (SELECT COALESCE(MAX(updated_at), '') FROM adh_dimensions) AS du"
    )
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            row = cur.fetchone() or {}
    return (f"adh_metrics:{row.get('mc')}:{row.get('mu')}"
            f"|adh_dimensions:{row.get('dc')}:{row.get('du')}")


def signature(content_version: str, fp: str) -> str:
    """内容签名：正文内容版本 + 字典指纹。任一变化即视为新版本。

    `content_version` 用 `adh_ontology_models.updated_at`（`ON UPDATE CURRENT_TIMESTAMP`，
    任何保存/发布都会刷新）或正文哈希；命中路径只需取这一小列，
    **不必把整份 json_content（百 KB 级）拉出来**。
    """
    digest = hashlib.sha1((content_version or "").encode("utf-8")).hexdigest()[:16]
    return f"{digest}:{fp}"


def get(key: tuple, sig: str) -> str | None:
    now = time.monotonic()
    with _lock:
        item = _store.get(key)
        if not item:
            _stats["miss"] += 1
            return None
        ts, stored_sig, text = item
        if stored_sig != sig:
            del _store[key]
            _stats["miss"] += 1
            return None
        if now - ts > TTL_SEC:
            del _store[key]
            _stats["expired"] += 1
            _stats["miss"] += 1
            return None
        _store.move_to_end(key)
        _stats["hit"] += 1
        return text


def put(key: tuple, sig: str, text: str) -> None:
    with _lock:
        _store[key] = (time.monotonic(), sig, text)
        _store.move_to_end(key)
        while len(_store) > MAX_ENTRIES:
            _store.popitem(last=False)


def clear() -> None:
    with _lock:
        _store.clear()


def stats() -> dict:
    with _lock:
        return {"entries": len(_store), **_stats}
