"""数据源服务 — 管理数据库连接的业务逻辑。

从 backend/api/datasource.py 迁移而来。
提供数据源的 CRUD、连接测试、表/列查询、SQL 执行等功能。
"""

import json
import logging
import time
from datetime import datetime
from typing import Any, Optional

import pymysql

from backend.common.config import (
    DORIS_HOST, DORIS_PORT, DORIS_USER, DORIS_PASSWORD, DORIS_DATABASE,
)
from backend.common.crypto import encrypt_password, decrypt_password, is_encrypted
from backend.common.db.metadata_db import get_metadata_conn
from backend.common.ttl_cache import datasource_cache

logger = logging.getLogger(__name__)

# Try to import psycopg2 (PostgreSQL / SLS-PG 协议)
try:
    import psycopg2
    import psycopg2.extras
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

# 走 PG 协议/语法的数据源类型(SLS 以 PG 兼容协议接入)
POSTGRES_DB_TYPES = ("postgres", "postgresql", "pg", "sls")


# ── 模块级状态 ────────────────────────────────────────────────────────

_default_ds_checked = False


# ── 辅助函数 ──────────────────────────────────────────────────────────

def _now() -> str:
    """返回当前时间字符串。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ts_id() -> int:
    """生成基于时间戳的 ID。"""
    return int(time.time() * 1000)


def _sanitize_row(row: dict) -> dict:
    """将行中的 datetime 字段转换为 ISO 字符串。"""
    if not row:
        return row
    for key in ("created_at", "updated_at"):
        if hasattr(row.get(key), "isoformat"):
            row[key] = row[key].isoformat()
    return row


def _sanitize_rows(rows: list) -> list:
    """批量转换行中的 datetime 字段。"""
    return [_sanitize_row(r) for r in rows]


# ── Elasticsearch 型数据源（2026-11-13 拍板：ES 从内置 MCP 通道迁为正式数据源）──
# ES 没有 database_name、也没有 socket 连接（走 REST `/_sql`），“库”这一层由**索引别名**
# 承担。因此配置面校验只看开放范围：开放范围为空 = 不开放任何索引（fail-closed，与
# `es_client.index_allowed` 空白名单恒拒同口径），不允许存下一个“永远跑不通”的数据源。
from backend.semantics.dialect import ES_DB_TYPES as _ES_DB_TYPES  # 单一事实源（含连接工厂拒绝）

# adh_datasources 列可用性缓存（迁移未应用时回落旧列清单，不让整表读写因此失败）
_COLUMN_SUPPORT_CACHE: dict[str, bool] = {}


def _is_es_db_type(value: Any) -> bool:
    return str(value or "").strip().lower() in _ES_DB_TYPES


def _supports_column(conn, column: str) -> bool:
    """`adh_datasources` 是否已有某列（一次性 information_schema 探测 + 缓存）。

    不硬编码 schema 名：本环境元库名可能与迁移文件里的 `USE` 不同（历史踩坑：
    硬编码 `USE adh` 打到错误 schema，迁移“执行成功”但一行没改）。
    """
    if column in _COLUMN_SUPPORT_CACHE:
        return _COLUMN_SUPPORT_CACHE[column]
    ok = False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_datasources' "
                "AND COLUMN_NAME = %s LIMIT 1", (column,))
            ok = bool(cur.fetchone())
    except Exception:  # noqa: BLE001 — 探不到就当不支持，回落旧列清单
        conn.rollback()
        ok = False
    _COLUMN_SUPPORT_CACHE[column] = ok
    return ok


# ── 数据源高级配置：唯一可配清单（只列**有真实消费方**的旋钮）──────────────────
# 为什么不让多余键存库：`extra_config` 写了但代码不消费 = “配了没用”的空承诺；
# 拼错的键静默入库、调了没反应是最难排查的一类缺陷，所以未知键直接拒，
# 不做“先存着以后再用”。区间上限 = 治理可接受的天花板（超界报错不给默认值）。
# 闸门旋钮单源定义：区间与 `core.query_gate` 的 clamp 边界同一组常量，两处写数字必然漂移。
from backend.core import query_gate as _query_gate

# 取数并发闸门（两类数据源共用，消费方 `core/query_gate.py`）：
# 过去只管“单次查询多久”，不管“同时多少条”—— 一次整板刷新 N 图 × M 个用户会把源侧
# 连接数/配额打穿，结果是集体超时且没人知道自己在排队。现在按源设上限：
# 满员先排队（FIFO），排不过 `queue_wait_sec` 才拒，并给用户可读的“稍后重试”。
GATE_EXTRA_NUMBERS: dict[str, tuple[int, int, str]] = {
    "max_concurrency": (_query_gate.LIMIT_FLOOR, _query_gate.LIMIT_CEIL,
                        "最大并发查询数（超出先排队，排不到会提示稍后重试）"),
    "queue_wait_sec": (int(_query_gate.WAIT_FLOOR), int(_query_gate.WAIT_CEIL),
                       "排队等待上限（秒，0 = 不排队直接拒）"),
}
ES_EXTRA_NUMBERS: dict[str, tuple[int, int, str]] = {
    **GATE_EXTRA_NUMBERS,
    "timeout_sec": (1, 300, "单次查询超时（秒）"),
    "max_rows": (1, 100_000, "单次拉回行数上限"),
    # 连接池与并发上限是两件事：池只管 HTTP 连接**复用**（省 TLS 握手），
    # requests 在池满时仍会新建连接，不拦并发；拿 `pool_size` 当限流是错的。
    "pool_size": (1, 64, "连接池数量（同一数据源可复用的 HTTP 连接数）"),
}
ES_EXTRA_LISTS = ("allowed_aliases", "allowed_index_patterns")
SQL_EXTRA_NUMBERS: dict[str, tuple[int, int, str]] = {
    **GATE_EXTRA_NUMBERS,
    # 以前这两个值写死在 `datasource_db.get_datasource_conn`（connect 10s / read 30s），
    # 跨源预算不同只能改代码；现在缺省仍取同一组常量（行为零变化），配了才覆盖。
    "connect_timeout_sec": (1, 120, "建立连接超时（秒）"),
    "read_timeout_sec": (1, 600, "查询执行超时（秒）"),
}


def _parsed_extra(extra: Any, label: str) -> dict:
    """bytes/JSON 字符串/None → dict（列是 JSON，不同驱动会回 str 或 bytes）。"""
    if isinstance(extra, (bytes, bytearray)):
        extra = extra.decode("utf-8", errors="replace")
    if isinstance(extra, str):
        try:
            extra = json.loads(extra or "{}")
        except ValueError as exc:
            raise ValueError(f"{label}格式不正确（应为对象）") from exc
    if extra is None:
        return {}
    if not isinstance(extra, dict):
        raise ValueError(f"{label}格式不正确（应为对象）")
    return dict(extra)


def _normalized_numbers(extra: dict, spec: dict[str, tuple[int, int, str]], label: str) -> None:
    """数值旋钮就地归一：非整数/越界都报错并带区间与含义（不回默认值、不静默删键）。"""
    for key, (lo, hi, meaning) in spec.items():
        if extra.get(key) is None or extra.get(key) == "":
            extra.pop(key, None)
            continue
        try:
            value = int(extra[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label}{meaning}必须是整数秒/整数") from exc
        if not lo <= value <= hi:
            raise ValueError(f"{label}{meaning}必须在 {lo}~{hi} 之间（当前 {value}）")
        extra[key] = value


def _reject_unknown_extra_keys(extra: dict, known: tuple[str, ...], label: str) -> None:
    unknown = sorted(set(extra) - set(known))
    if unknown:
        raise ValueError(
            f"{label}不认识这些配置项：{'、'.join(unknown)}；"
            f"可配项只有：{'、'.join(known)}（写了不生效的配置项比不配更容易误判）")


def _validated_es_extra(db_type: Any, extra: Any) -> Optional[dict]:
    """ES 源的 `extra_config` 校验与归一；非 ES 源返回 None（不给其他类型塞无关字段）。

    开放范围允许同时登别名与 `t-别名*` 模式（并集口径）：同一份数据两种写法都能查，
    阶段二逐个改写正文期间不会因“清单收了别名而正文还是模式”误拒存量。
    """
    if not _is_es_db_type(db_type):
        return None
    label = "ES 数据源的索引范围/预算配置："
    extra = _parsed_extra(extra, label)
    aliases = sorted({str(x).strip() for x in (extra.get("allowed_aliases") or []) if str(x).strip()})
    patterns = sorted({str(x).strip() for x in (extra.get("allowed_index_patterns") or []) if str(x).strip()})
    if not aliases and not patterns:
        raise ValueError(
            "ES 数据源必须至少登记一个可查索引别名（开放范围为空 = 不开放任何索引，"
            "不允许建一个永远跑不通的数据源）")
    extra["allowed_aliases"] = aliases
    extra["allowed_index_patterns"] = patterns
    _normalized_numbers(extra, ES_EXTRA_NUMBERS, label)
    extra["verify_certs"] = bool(extra.get("verify_certs", True))
    _reject_unknown_extra_keys(
        extra, ES_EXTRA_LISTS + tuple(ES_EXTRA_NUMBERS) + ("verify_certs", "api_key"), label)
    return extra


def _validated_sql_extra(db_type: Any, extra: Any) -> Optional[dict]:
    """SQL 型数据源（mysql/doris/postgres/sls）的高级配置：连接超时与查询超时。

    没配任何东西时返回 None：不给没需求的源塑一张空配置表（保持 `extra_config` 为 NULL）。
    ES 型走 `_validated_es_extra`，这里一律返 None，两边不互掌字段。
    """
    if _is_es_db_type(db_type):
        return None
    label = "数据源的高级配置："
    extra = _parsed_extra(extra, label)
    if not extra:
        return None
    _normalized_numbers(extra, SQL_EXTRA_NUMBERS, label)
    _reject_unknown_extra_keys(extra, tuple(SQL_EXTRA_NUMBERS), label)
    return extra or None


def _validated_extra(db_type: Any, extra: Any) -> Optional[dict]:
    """按类型分派的高级配置校验（创建/更新两条路共用，不允许多口径）。"""
    if _is_es_db_type(db_type):
        return _validated_es_extra(db_type, extra)
    return _validated_sql_extra(db_type, extra)


def _test_es_connection(ds: dict) -> dict:
    """ES 型数据源的测连：逐个开放别名跑一条最小查询（拍板口径：能查到数据就行）。

    不走语义执行引擎的下推测连：ES 不是 socket 连接，`SELECT 1 LIMIT 1` 在 ES 上
    也不合法（必须 FROM 索引）。探测与取数共用 `es_client` 同一个入口，避免
    “测连通过但查询失败”的通道不一致缺陷（本文件头已记为缺陷）。
    不报索引数量/不报集群标识（护栏 §7）。
    """
    from backend.core import es_client

    try:
        ctx = es_client.ctx_for(ds.get("id"))
        probes = [es_client.probe_target(target, ctx) for target in ctx.allowed]
    except es_client.EsError as exc:
        # 连不通/超时/凭据错：直接把可诊断原因回给管理员（不当“没数据”也不当“连接成功”）
        return {"success": False, "message": str(exc)}

    usable = [p for p in probes if p["status"] in ("ok", "empty")]
    missing = [p for p in probes if p["status"] == "missing"]
    rejected = [p for p in probes if p["status"] == "rejected"]
    if not usable:
        if missing:
            return {"success": False, "message":
                    f"已登记的索引别名在集群侧都查不到：{'、'.join(p['target'] for p in missing)}"
                    "（请核对别名是否被改名或删除）"}
        return {"success": False,
                "message": rejected[0]["reason"] if rejected else "未登记任何可查索引别名"}
    parts = [f"可查 {len(usable)}/{len(probes)} 个索引别名"]
    if missing:
        parts.append(f"其中 {len(missing)} 个集群侧不存在：{'、'.join(p['target'] for p in missing)}")
    if rejected:
        parts.append(f"{len(rejected)} 个探测被集群拒绝（请检查写法）")
    return {"success": True, "message": "，".join(parts)}


def _es_target_for(ctx, table_name: Any) -> str:
    """把“表名”还原成正文能直接 FROM 的目标（优先原样，其次同一目标的别名/模式写法）。"""
    from backend.core import es_client

    raw = es_client._norm_index(table_name)
    if raw in ctx.allowed:
        return raw
    alias = es_client.es_alias_of(raw)
    for entry in ctx.allowed:
        if es_client.es_alias_of(entry) == alias:
            return entry
    return raw


def es_table_list(ds: dict) -> list:
    """ES 型数据源的“表清单”= 已登记的索引**别名**（去重后）。

    公开给元数据同步复用（`metadata_sync._sync_es_metadata`）：字段/表名口径只能有一处，
    两处各写一份就会漂移（本文件头已记为同类缺陷）。

    两件事是有意的：
    - 不向集群展开具体索引（一个别名背 300+ 个月分片，展开会拖死页面并塞爆模型上下文）；
    - 清单以别名而非 `t-别名*` 模式给出：行级策略/敏感字段在元库按裸表名登记，
      拿别名当“表名”才能让同一份策略在 ES 与 MySQL 两个通道都命得中（键空间统一）。
    """
    from backend.core import es_client

    ctx = es_client.ctx_for(ds.get("id"))
    names = sorted({es_client.es_alias_of(t) for t in ctx.allowed if es_client.es_alias_of(t)})
    return [{"TABLE_NAME": n, "TABLE_COMMENT": "Elasticsearch 索引别名（data view，按别名查询）",
             "TABLE_ROWS": None, "DATA_LENGTH": None} for n in names]


def es_column_list(ds: dict, table_name: str) -> list:
    """ES 的列清单 = `_field_caps` 的**字段并集**（拍板：取并集，不逐索引展开）。

    与 `es_table_list` 同为跨模块公开口径（元数据同步 / 数据源浏览 / 敏感页共用）。

    同时把 `.keyword` 子字段单列一行：ES 上 text 字段不能精确比较/聚合，正文实际引用的是
    `xxx.keyword`；列级策略要命得中，就得能按**正文里真实出现的那个名字**登记 ——
    只给父字段会让策略配了却永远不生效（静默不脱敏）。
    """
    from backend.core import es_client

    ctx = es_client.ctx_for(ds.get("id"))
    info = es_client.describe_index_fields(_es_target_for(ctx, table_name), ctx)
    rows: list[dict] = []
    for item in info.get("fields") or []:
        exact = item.get("exact_match_field") or ""
        rows.append({"COLUMN_NAME": item.get("field") or "",
                     "DATA_TYPE": item.get("type") or "",
                     "COLUMN_COMMENT": ("分组/等值请用 " + exact) if exact else "",
                     "COLUMN_KEY": "", "IS_NULLABLE": "YES"})
        if exact:
            rows.append({"COLUMN_NAME": exact, "DATA_TYPE": "keyword",
                         "COLUMN_COMMENT": "子字段（正文里按这个名字引用）",
                         "COLUMN_KEY": "", "IS_NULLABLE": "YES"})
    for item in info.get("sub_fields") or []:
        rows.append({"COLUMN_NAME": item.get("field") or "",
                     "DATA_TYPE": item.get("type") or "",
                     "COLUMN_COMMENT": "object/nested 子字段（ES SQL 只能引用子字段本身）",
                     "COLUMN_KEY": "", "IS_NULLABLE": "YES"})
    return rows


def _validated_site_code(value) -> str:
    """数据源归属站点校验（空 = 未站点化）。

    聚合态（全球）不是具体部署站点，不得写进数据源；非法值直接抛 ValueError
    带可选站点名（fail-loud，不静默丢弃用户的选择）。
    """
    code = str(value or "").strip()
    if not code:
        return ""
    from backend.core.site_service import validate_site_code

    return validate_site_code(code, allow_aggregate=False)


def _invalidate_topology_caches() -> None:
    """数据源拓扑（站点归属）变更后失效可查站点/授权源派生缓存。"""
    try:
        datasource_cache.invalidate_prefix("sites:")
        datasource_cache.invalidate_prefix("auth_ds:")
    except Exception as e:  # noqa: BLE001 — 缓存层故障不阻断写路径
        logger.debug("[datasource] topology cache invalidate skipped: %s", e)


def _drop_es_sessions() -> None:
    """丢弃 ES 连接池：改了地址/证书校验/池大小后不得继续用旧握手。

    不报错不阻断（与缓存层同口径）：连接池只是性能旁路，重建后下一个请求自然拿到新配置。
    """
    try:
        from backend.core import es_client
        es_client.reset_sessions()
    except Exception as e:  # noqa: BLE001
        logger.debug("[datasource] es session reset skipped: %s", e)


def _sync_query_gate(ds_id: Any) -> None:
    """保存/删除后把并发上限与排队预算推到闸门（改了立刻生效，不重启、不打断在跑的查询）。

    同步失败时**丢桶**而不是只记日志：闸门不会每查询重读配置（只在建桶时读一次），
    失败后若保留旧桶，新上限就要到下次重启才生效——那正是“配了没生效”。丢桶后下一次
    取数会从库重读；取数主链路不因为旁路同步失败而被阻断（FDE §4）。
    """
    try:
        from backend.core import query_gate
        from backend.common.db.datasource_db import get_datasource_by_id

        row = get_datasource_by_id(int(ds_id or 0))
        if row:
            query_gate.sync_datasource_gate(int(ds_id or 0), row=row)
        else:
            query_gate.reset_gates(int(ds_id or 0))
    except Exception as e:  # noqa: BLE001
        logger.warning("[datasource] 取数并发闸门同步失败，下一次取数重建闸门: %s", e)
        try:
            from backend.core import query_gate
            query_gate.reset_gates(int(ds_id or 0))
        except Exception:  # noqa: BLE001
            logger.debug("[datasource] query gate bucket drop skipped", exc_info=True)


# ── 数据源服务 ────────────────────────────────────────────────────────

class DatasourceService:
    """数据源管理服务。"""

    async def list_datasources(self, workspace_id: int = 0, user_id: int = 0) -> list[dict]:
        """列出所有数据源。

        Args:
            workspace_id: 工作空间 ID，0 表示不限制（全局管理列表）。
            user_id: 请求用户 ID；workspace_id>0 时按其角色授权集过滤（纯角色裁决）。

        Returns:
            数据源列表，密码已脱敏。
        """
        self._ensure_default_datasource()
        try:
            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    if workspace_id:
                        # 纯角色裁决: 工作空间不再绑定数据源(adh_workspace_datasources 退役);
                        # 按请求用户角色授权集过滤, 空授权 fail-closed 返回空列表。
                        from backend.core.role_service import role_service
                        allowed = sorted(set(role_service.get_user_allowed_datasources(
                            user_id, workspace_id)))
                        if not allowed:
                            return []
                        marks = ','.join(['%s'] * len(allowed))
                        extra_col = ", d.extra_config" if _supports_column(conn, "extra_config") else ""
                        cur.execute(
                            "SELECT d.id, d.name, d.db_type, d.host, d.port, d.username, "
                            "d.database_name, d.is_default, d.`ssl`, d.owner_id, d.site_code, "
                            "d.created_at, d.updated_at" + extra_col + " "
                            f"FROM adh_datasources d WHERE d.id IN ({marks}) "
                            "ORDER BY d.is_default DESC, d.name ASC",
                            tuple(allowed),
                        )
                    else:
                        extra_col = ", extra_config" if _supports_column(conn, "extra_config") else ""
                        cur.execute(
                            "SELECT id, name, db_type, host, port, username, database_name, "
                            "is_default, `ssl`, owner_id, site_code, created_at, updated_at" + extra_col + " "
                            "FROM adh_datasources ORDER BY is_default DESC, name ASC"
                        )
                    rows = cur.fetchall()
                    return _sanitize_rows(rows)
            finally:
                conn.close()
        except Exception as e:
            logger.error("列出数据源失败: %s", e)
            # MySQL 不可用时返回默认 Doris 配置
            return [{
                "id": 0,
                "name": f"Doris ({DORIS_HOST})",
                "db_type": "doris",
                "host": DORIS_HOST,
                "port": DORIS_PORT,
                "database_name": DORIS_DATABASE,
                "is_default": 1,
                "ssl": 0,
                "owner_id": 0,
                "site_code": "",
                "created_at": "",
                "updated_at": "",
            }]

    async def list_authorized_datasources(self, user_id: int, workspace_id: int = 0) -> list[dict]:
        """列出当前用户角色授权的数据源（纯角色裁决；聊天选择器等“我的可用数据源”消费）。

        数据源可用集 = 用户角色授权(adh_user_roles ⋈ adh_role_datasource_access)；
        空授权 fail-closed 返回空列表（不解释为全量）。仅回传选择器所需最小字段
        (id/name/db_type/is_default/site_code)，不含主机/账号等连接信息（守 security §7）。
        """
        from backend.core.role_service import role_service
        allowed = sorted(set(role_service.get_user_allowed_datasources(user_id, workspace_id)))
        if not allowed:
            return []
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                marks = ','.join(['%s'] * len(allowed))
                cur.execute(
                    "SELECT id, name, db_type, is_default, site_code "
                    f"FROM adh_datasources WHERE id IN ({marks}) "
                    "ORDER BY is_default DESC, name ASC",
                    tuple(allowed),
                )
                return cur.fetchall()
        finally:
            conn.close()

    async def get_datasource(self, ds_id: int) -> Optional[dict]:
        """获取单个数据源（密码已脱敏）。

        Args:
            ds_id: 数据源 ID。

        Returns:
            数据源字典，不存在返回 None。
        """
        ds = await self.get_datasource_raw(ds_id)
        if ds and ds.get("password"):
            ds["password"] = "***"
        return ds

    async def get_datasource_raw(self, ds_id: int) -> Optional[dict]:
        """获取单个数据源（包含解密后的密码）。

        Args:
            ds_id: 数据源 ID。

        Returns:
            数据源字典，密码已解密。不存在返回 None。
        """
        try:
            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM adh_datasources WHERE id = %s", (ds_id,))
                    row = cur.fetchone()
                    if row and row.get("password"):
                        password = row["password"]
                        if is_encrypted(password):
                            try:
                                row["password"] = decrypt_password(password)
                            except ValueError as e:
                                logger.warning(
                                    "解密数据源 %s 密码失败: %s", ds_id, e
                                )
                    return row
            finally:
                conn.close()
        except Exception:
            # MySQL 不可用时，对 id=0 返回默认 Doris 配置
            if ds_id == 0:
                return {
                    "id": 0, "name": f"Doris ({DORIS_HOST})", "db_type": "doris",
                    "host": DORIS_HOST, "port": DORIS_PORT, "username": DORIS_USER,
                    "password": DORIS_PASSWORD, "database_name": DORIS_DATABASE,
                    "is_default": 1, "ssl": 0, "owner_id": 0,
                }
            return None

    async def create_datasource(self, data: dict, owner_id: int = 0) -> dict:
        """创建数据源。

        Args:
            data: 数据源信息字典，包含 name, db_type, host, port, username, password 等。
            owner_id: 创建者用户 ID。

        Returns:
            包含新数据源 ID 的字典。
        """
        ds_id = _ts_id()
        now = _now()
        ds_name = (data.get("name") or "").strip()
        if not ds_name:
            raise ValueError("数据源名称不能为空")
        site_code = _validated_site_code(data.get("site_code"))
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # name 为全局唯一标识：先查重，给出友好提示（DB 层另有 UNIQUE 兜底）
                cur.execute("SELECT 1 FROM adh_datasources WHERE name = %s LIMIT 1", (ds_name,))
                if cur.fetchone():
                    raise ValueError(f"数据源名称「{ds_name}」已存在；名称为全局唯一标识，请换一个")
                if data.get("is_default"):
                    cur.execute("UPDATE adh_datasources SET is_default = 0")
                ssl_mode = (data.get("ssl_mode") or "disabled")
                es_type = _is_es_db_type(data.get("db_type"))
                # 高级配置按类型分派：ES 拿索引范围 + 超时/最大行/连接池，SQL 型拿连接/查询超时
                es_extra = _validated_extra(data.get("db_type"), data.get("extra_config"))
                # 列清单按实际库结构拼装（而不是主查询 + 失败回落两套写法）：
                # 回落链里“剔除 ssl_mode/site_code 重试”会静默把站点归属抹成默认值，
                # 那类“配置写进去了但丢字段”比直接报错难查得多。
                col_pairs = [
                    ("id", ds_id), ("name", ds_name),
                    ("db_type", data.get("db_type", "mysql")),
                    ("host", data["host"]),
                    ("port", data.get("port") or (9200 if es_type else 3306)),
                    ("username", data.get("username", "")),
                    ("password", encrypt_password(data.get("password", ""))),
                    # ES 没有库名这一层（执行范围由索引别名决定），固定留空
                    ("database_name", "" if es_type else (data.get("database_name") or "")),
                    ("is_default", 1 if data.get("is_default") else 0),
                    ("`ssl`", 1 if data.get("ssl") else 0),
                    ("owner_id", owner_id), ("created_at", now), ("updated_at", now),
                ]
                if _supports_column(conn, "ssl_mode"):
                    col_pairs.append(("ssl_mode", ssl_mode))
                if _supports_column(conn, "site_code"):
                    col_pairs.append(("site_code", site_code))
                if es_extra is not None and _supports_column(conn, "extra_config"):
                    col_pairs.append(("extra_config", json.dumps(es_extra, ensure_ascii=False)))
                cols_sql = ", ".join(c for c, _ in col_pairs)
                marks_sql = ",".join(["%s"] * len(col_pairs))
                cur.execute(
                    f"INSERT INTO adh_datasources ({cols_sql}) VALUES ({marks_sql})",
                    tuple(v for _, v in col_pairs),
                )
            conn.commit()
        finally:
            conn.close()

        # 清除缓存
        datasource_cache.invalidate(f"ds_{ds_id}")
        _invalidate_topology_caches()
        _drop_es_sessions()
        _sync_query_gate(ds_id)
        return {"id": ds_id}

    async def update_datasource(self, ds_id: int, data: dict) -> dict:
        """更新数据源。

        Args:
            ds_id: 数据源 ID。
            data: 要更新的字段字典。

        Returns:
            操作结果字典。
        """
        now = _now()
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                updates = ["updated_at = %s"]
                params = [now]

                if data.get("name") is not None:
                    new_name = (data["name"] or "").strip()
                    if not new_name:
                        raise ValueError("数据源名称不能为空")
                    cur.execute(
                        "SELECT 1 FROM adh_datasources WHERE name = %s AND id <> %s LIMIT 1",
                        (new_name, ds_id),
                    )
                    if cur.fetchone():
                        raise ValueError(f"数据源名称「{new_name}」已存在；名称为全局唯一标识，请换一个")
                    updates.append("name = %s")
                    params.append(new_name)
                if data.get("db_type") is not None:
                    updates.append("db_type = %s")
                    params.append(data["db_type"])
                if data.get("host") is not None:
                    updates.append("host = %s")
                    params.append(data["host"])
                if data.get("port") is not None:
                    updates.append("port = %s")
                    params.append(data["port"])
                if data.get("username") is not None:
                    updates.append("username = %s")
                    params.append(data["username"])
                if data.get("password"):  # 跳过空密码，避免覆盖已有密码
                    updates.append("password = %s")
                    params.append(encrypt_password(data["password"]))
                if data.get("database_name") is not None:
                    updates.append("database_name = %s")
                    params.append(data["database_name"])
                if data.get("is_default") is not None:
                    if data["is_default"]:
                        cur.execute("UPDATE adh_datasources SET is_default = 0")
                    updates.append("is_default = %s")
                    params.append(1 if data["is_default"] else 0)
                if data.get("ssl") is not None:
                    updates.append("`ssl` = %s")
                    params.append(1 if data["ssl"] else 0)
                if data.get("ssl_mode") is not None:
                    updates.append("ssl_mode = %s")
                    params.append(data["ssl_mode"])
                if data.get("site_code") is not None:
                    updates.append("site_code = %s")
                    params.append(_validated_site_code(data["site_code"]))
                if "extra_config" in data or _is_es_db_type(data.get("db_type")):
                    # 改成 ES 型但未带开放范围时，拿库里已有的范围再校一次：
                    # 否则会出现“类型改了对、库里的范围还是空”→ 每次取数都拒，
                    # 而拒因看起来像数据问题而不是配置问题。
                    # 显式传 extra_config=null = 清空高级配置、回到代码缺省：只按
                    # `is not None` 判会让配上去的超时改不回来（只能加不能减 = 半个入口）。
                    eff_type = data.get("db_type")
                    extra_raw = data.get("extra_config")
                    has_extra_col = _supports_column(conn, "extra_config")
                    if eff_type is None:
                        cur.execute("SELECT db_type FROM adh_datasources WHERE id = %s", (ds_id,))
                        eff_type = (cur.fetchone() or {}).get("db_type")
                    if extra_raw is None and has_extra_col and "extra_config" not in data:
                        cur.execute(
                            "SELECT extra_config FROM adh_datasources WHERE id = %s", (ds_id,))
                        extra_raw = (cur.fetchone() or {}).get("extra_config")
                    extra_json = _validated_extra(eff_type, extra_raw)
                    if _is_es_db_type(eff_type):
                        # ES 的范围/预算是必需项（空范围在校验器里已抛），只写非空归一结果
                        if extra_json is not None and has_extra_col:
                            updates.append("extra_config = %s")
                            params.append(json.dumps(extra_json, ensure_ascii=False))
                    elif has_extra_col:
                        # SQL 型：没配任何键（None / {}）归一为 NULL，不给没需求的源塑空配置
                        updates.append("extra_config = %s")
                        params.append(
                            json.dumps(extra_json, ensure_ascii=False) if extra_json else None)

                params.append(ds_id)
                try:
                    cur.execute(
                        f"UPDATE adh_datasources SET {', '.join(updates)} WHERE id = %s",
                        params,
                    )
                except Exception:
                    # ssl_mode/site_code 列未迁移时剔除该字段重试
                    conn.rollback()
                    dropped = False
                    for col in ("ssl_mode = %s", "site_code = %s"):
                        if col in updates:
                            idx = updates.index(col)
                            updates.pop(idx)
                            params.pop(idx + 1)  # 首位是 updated_at, 字段参数同下标+1
                            dropped = True
                    if not dropped:
                        raise
                    cur.execute(
                        f"UPDATE adh_datasources SET {', '.join(updates)} WHERE id = %s",
                        params,
                    )
            conn.commit()
        finally:
            conn.close()

        # 清除缓存
        datasource_cache.invalidate(f"ds_{ds_id}")
        _invalidate_topology_caches()
        _drop_es_sessions()
        _sync_query_gate(ds_id)
        return {"success": True}

    async def delete_datasource(self, ds_id: int) -> dict:
        """删除数据源（同时从归属站点拓扑中消失）。

        Args:
            ds_id: 数据源 ID。

        Returns:
            操作结果字典。
        """
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM adh_datasources WHERE id = %s", (ds_id,))
            conn.commit()
        finally:
            conn.close()

        # 清除缓存
        datasource_cache.invalidate(f"ds_{ds_id}")
        _invalidate_topology_caches()
        _drop_es_sessions()
        # 删了源就丢桶：留着旧桶会在“删除重建同名新源”时把旧上限带到新源上
        # （闸门按 id 分桶且只在建桶时读配置，不主动丢就只能等重启）。
        _sync_query_gate(ds_id)
        return {"success": True}

    async def test_connection(self, ds_id: int) -> dict:
        """测试数据源连接 — 必须经 DataEngine 执行通道探测(与查询执行同链路)。

        禁止降级为 pymysql 直连探测: 直连只验证配置写对, 会"测试通过但查询失败"
        (执行走 DataEngine), 通道不一致本身就是缺陷。

        Args:
            ds_id: 数据源 ID。

        Returns:
            包含 success 和 message 的字典。
        """
        ds = await self.get_datasource_raw(ds_id)
        if not ds:
            return {"success": False, "message": "数据源不存在"}

        if _is_es_db_type(ds.get("db_type")):
            # ES 没有 socket 连接，测连走本模块自己的探测（与取数同一入口）
            return _test_es_connection(ds)

        try:
            # 经 semantics.execution 远程下推测连（动态连接，无需预注册）
            from backend.semantics.execution.engine import SemanticEngine

            SemanticEngine().execute_pushdown(
                "SELECT 1 LIMIT 1",
                datasource_id=int(ds_id),
                db_type=ds.get("db_type") or "mysql",
            )
            return {"success": True, "message": "连接成功（经语义执行引擎）"}
        except Exception as e:
            logger.error(
                "数据源连接测试失败 ds_id=%s: %s (type=%s)",
                ds_id, e, type(e).__name__,
                exc_info=True,
            )
            error_msg = str(e)
            if hasattr(e, "errno"):
                error_msg = f"[{e.errno}] {getattr(e, 'errmsg', str(e))}"
            return {"success": False, "message": error_msg}

    async def list_tables(self, ds_id: int) -> list:
        """列出数据源中的表。

        Args:
            ds_id: 数据源 ID。

        Returns:
            表信息列表。

        Raises:
            ValueError: 数据源不存在时抛出。
        """
        ds = await self.get_datasource_raw(ds_id)
        if not ds:
            raise ValueError("数据源不存在")

        if _is_es_db_type(ds.get("db_type")):
            return es_table_list(ds)
        return await self._list_db_tables(ds)

    async def list_columns(self, ds_id: int, table_name: str) -> list:
        """列出表的列信息。

        Args:
            ds_id: 数据源 ID。
            table_name: 表名。

        Returns:
            列信息列表。

        Raises:
            ValueError: 数据源不存在时抛出。
        """
        ds = await self.get_datasource_raw(ds_id)
        if not ds:
            raise ValueError("数据源不存在")

        if _is_es_db_type(ds.get("db_type")):
            return es_column_list(ds, table_name)
        return await self._list_db_columns(ds, table_name)

    async def execute_sql(self, ds_id: int, sql: str, *,
                          user_id: int = 0, workspace_id: int = 0,
                          username: str = "") -> dict:
        """数据源裸 SQL 试跑 —— 与数据集/看板/语义查数/Playground **同一条取数链路**。

        本方法过去自建执行支路（MySQL 侧 `pymysql` 直连 + ES 侧直调 `es_client`），
        于是“同一句 SQL 在数据源页能查出未脱敏的全量、在看板上只出授权后的行”，
        列级屏蔽/脱敏、行级策略、源级 RBAC、查询审计四项全部缺席 —— 那不是功能差异，
        是护城河上的一道口子（护栏 §1：任何返回数据行的取数必经治理执行口）。
        现在只保留一个出口 `core.governed_query.governed_execute`：
        - 身份由 API 层从 JWT 传入，无可信身份 fail-closed（`NoIdentityError`），绝不回退裸连；
        - 只读校验、方言、行数上限（正文无外层 LIMIT 时服务端补）、重名列消歧、
          错因分类、成功/拒绝/失败三态审计，一律与其余场景逐字相同；
        - 旧签名的 `limit` 入参删除：它从未被消费（旧实现是把结果全量拉回来），
          留着一个不生效的参数比没有参数更容易让人误判口径。

        ES/SLS/Doris 不再在这里按 db_type 分派 —— 分派在 `core.query_executor.execute_query`
        一处完成（ES 型源走 `es_client` 硬门，其余走远程下推），本模块不重复一套。

        Args:
            ds_id: 数据源 ID。
            sql: SQL 语句（只读）。
            user_id: 服务端解析出的当前用户 id（0 = 无身份，直接拒）。
            workspace_id: 工作空间上下文（RBAC/RLS 裁决用）。
            username: 审计用用户名。

        Returns:
            包含 columns, rows(dict 数组), row_count, elapsed_ms 的字典。

        Raises:
            ValueError: 数据源不存在、SQL 为空或执行失败（文案按错因分类，不含引擎原始细节）。
            PermissionError: 非只读语句、越权或行级策略拒绝。
            NoIdentityError: 缺少可信用户身份（API 层映射 403）。
        """
        from backend.core.governed_query import governed_execute

        ds = await self.get_datasource_raw(ds_id)
        if not ds:
            raise ValueError("数据源不存在")

        sql = sql.strip()
        if not sql:
            raise ValueError("SQL 不能为空")

        return governed_execute(sql, int(ds_id), int(user_id or 0),
                                int(workspace_id or 0), username or "")

    def get_datasource_conn(
        self, db_type: str, host: str, port: int,
        user: str, password: str, database: str = None, ssl: bool = False,
        ssl_mode: str = None,
        connect_timeout: int = 10, read_timeout: int = 30,
    ):
        """创建数据库连接（工厂方法）。

        Args:
            db_type: 数据库类型 (mysql/doris/postgres/sls)。
            host: 主机地址。
            port: 端口。
            user: 用户名。
            password: 密码。
            database: 数据库名。
            ssl: 是否启用 SSL（兼容旧字段）。
            ssl_mode: SSL 模式 (disabled/preferred/required)，设置时优先于 ssl。
            connect_timeout: 握手超时（秒），来自数据源高级配置（缺省 10 = 旧写死值）。
            read_timeout: 查询超时（秒），来自数据源高级配置（缺省 30 = 旧写死值）。

        Returns:
            pymysql / psycopg2 连接。
        """
        db_type = (db_type or "mysql").lower()
        # 归一化有效 ssl_mode(MySQL 列可能回传 bytes)
        if isinstance(ssl_mode, bytes):
            ssl_mode = ssl_mode.decode("utf-8", errors="replace")
        effective_ssl_mode = ssl_mode or "disabled"
        if effective_ssl_mode == "disabled" and ssl:
            effective_ssl_mode = "required"

        if db_type in POSTGRES_DB_TYPES:
            if not HAS_PSYCOPG2:
                raise ValueError("psycopg2 未安装，请执行: pip install psycopg2-binary")
            pg_kwargs = {
                "host": host,
                "port": port,
                "user": user,
                "password": password,
                "dbname": database or "postgres",
                "cursor_factory": psycopg2.extras.RealDictCursor,
                "connect_timeout": int(connect_timeout or 10),
            }
            if effective_ssl_mode == "required":
                pg_kwargs["sslmode"] = "require"
            elif effective_ssl_mode == "preferred":
                pg_kwargs["sslmode"] = "prefer"
            return psycopg2.connect(**pg_kwargs)
        else:
            conn_kwargs = {
                "host": host,
                "port": port,
                "user": user,
                "password": password,
                "database": database or None,
                "charset": "utf8mb4",
                "cursorclass": pymysql.cursors.DictCursor,
                "connect_timeout": int(connect_timeout or 10),
                "read_timeout": int(read_timeout or 30),
            }
            if effective_ssl_mode == "required":
                conn_kwargs["ssl"] = {"ssl_mode": "REQUIRED"}
                conn_kwargs["ssl_disabled"] = False
            elif effective_ssl_mode == "preferred":
                conn_kwargs["ssl"] = {"ssl_mode": "PREFERRED"}
                conn_kwargs["ssl_disabled"] = False
            return pymysql.connect(**conn_kwargs)

    def get_datasource_conn_from_dict(self, ds: dict):
        """从数据源字典创建连接（高级配置里的超时在此消费，不另读一遍库）。

        Args:
            ds: 数据源配置字典。

        Returns:
            数据库连接对象。
        """
        from backend.common.db.datasource_db import resolve_timeouts

        connect_sec, read_sec = resolve_timeouts(ds)
        return self.get_datasource_conn(
            db_type=ds.get("db_type", "mysql"),
            host=ds["host"],
            port=ds["port"],
            user=ds.get("username", ""),
            password=ds.get("password", ""),
            database=ds.get("database_name"),
            ssl=bool(ds.get("ssl", 0)),
            ssl_mode=ds.get("ssl_mode"),
            connect_timeout=connect_sec,
            read_timeout=read_sec,
        )

    async def get_datasource_by_id(self, ds_id: int) -> Optional[dict]:
        """获取数据源（带解密和缓存）。

        先查缓存，未命中则查数据库并缓存结果。

        Args:
            ds_id: 数据源 ID。

        Returns:
            数据源字典（密码已解密），不存在返回 None。
        """
        cache_key = f"ds_{ds_id}"
        cached = datasource_cache.get(cache_key)
        if cached is not None:
            return cached

        ds = await self.get_datasource_raw(ds_id)
        if ds:
            datasource_cache.set(cache_key, ds)
        return ds

    def _ensure_default_datasource(self):
        """自动创建默认 Doris 数据源（如果表为空）。"""
        global _default_ds_checked
        if _default_ds_checked:
            return
        try:
            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) AS cnt FROM adh_datasources")
                    row = cur.fetchone()
                    if row and row["cnt"] > 0:
                        _default_ds_checked = True
                        return

                    ds_id = _ts_id()
                    now = _now()
                    cur.execute(
                        "INSERT INTO adh_datasources "
                        "(id, name, db_type, host, port, username, password, database_name, "
                        "is_default, owner_id, created_at, updated_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            ds_id, f"Doris ({DORIS_HOST})", "doris",
                            DORIS_HOST, DORIS_PORT, DORIS_USER,
                            encrypt_password(DORIS_PASSWORD), DORIS_DATABASE,
                            1, 0, now, now,
                        ),
                    )
                conn.commit()
                _default_ds_checked = True
            finally:
                conn.close()
        except Exception:
            pass  # MySQL 不可用，跳过默认数据源创建

    async def _list_db_tables(self, ds: dict) -> list:
        """列出 MySQL/Doris/Postgres/SLS 数据库的表。"""
        conn = self.get_datasource_conn_from_dict(ds)
        db_type = (ds.get("db_type") or "").lower()
        try:
            with conn.cursor() as cur:
                if db_type == "sls":
                    # SLS PG 协议把 LogStore 映射在同名 project 的 schema 下（官方文档"查询元数据"）；
                    # 仅用文档保证的 information_schema 字段，不依赖 table_catalog/::regclass/
                    # obj_description/table_type（SLS 不填充或不支持）。
                    cur.execute(
                        "SELECT table_name AS \"TABLE_NAME\", "
                        "NULL AS \"TABLE_COMMENT\", NULL AS \"TABLE_ROWS\", NULL AS \"DATA_LENGTH\" "
                        "FROM information_schema.tables "
                        "WHERE table_schema = %s ORDER BY table_name",
                        (ds.get("database_name") or "",),
                    )
                elif db_type in POSTGRES_DB_TYPES:
                    cur.execute(
                        "SELECT table_name AS \"TABLE_NAME\", "
                        "obj_description((table_schema || '.' || table_name)::regclass, 'pg_class', 'comment') AS \"TABLE_COMMENT\", "
                        "NULL AS \"TABLE_ROWS\", NULL AS \"DATA_LENGTH\" "
                        "FROM information_schema.tables "
                        "WHERE table_catalog = %s AND table_type = 'BASE TABLE' "
                        "AND table_schema NOT IN ('pg_catalog', 'information_schema') "
                        "ORDER BY table_schema, table_name",
                        (ds.get("database_name") or "",),
                    )
                else:
                    cur.execute(
                        "SELECT TABLE_NAME, TABLE_COMMENT, TABLE_ROWS, DATA_LENGTH "
                        "FROM information_schema.TABLES "
                        "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE' "
                        "ORDER BY TABLE_NAME",
                        (ds.get("database_name") or "",),
                    )
                return cur.fetchall()
        finally:
            conn.close()

    async def _list_db_columns(self, ds: dict, table_name: str) -> list:
        """列出 MySQL/Doris/Postgres/SLS 表的列。"""
        conn = self.get_datasource_conn_from_dict(ds)
        db_type = (ds.get("db_type") or "").lower()
        try:
            with conn.cursor() as cur:
                if db_type == "sls":
                    # SLS 仅保证 column_name/data_type（按官方文档"查询字段结构"），
                    # 不依赖 ordinal_position/col_description/table_catalog。
                    cur.execute(
                        "SELECT column_name AS \"COLUMN_NAME\", data_type AS \"DATA_TYPE\", "
                        "'' AS \"COLUMN_COMMENT\", '' AS \"COLUMN_KEY\", 'YES' AS \"IS_NULLABLE\" "
                        "FROM information_schema.columns "
                        "WHERE table_name = %s ORDER BY column_name",
                        (table_name,),
                    )
                elif db_type in POSTGRES_DB_TYPES:
                    cur.execute(
                        "SELECT column_name AS \"COLUMN_NAME\", data_type AS \"DATA_TYPE\", "
                        "col_description((table_schema || '.' || table_name)::regclass, ordinal_position) AS \"COLUMN_COMMENT\", "
                        "'' AS \"COLUMN_KEY\", is_nullable AS \"IS_NULLABLE\" "
                        "FROM information_schema.columns "
                        "WHERE table_catalog = %s AND table_name = %s "
                        "AND table_schema NOT IN ('pg_catalog', 'information_schema') "
                        "ORDER BY table_schema, ordinal_position",
                        (ds.get("database_name") or "", table_name),
                    )
                else:
                    cur.execute(
                        "SELECT COLUMN_NAME, DATA_TYPE, COLUMN_COMMENT, COLUMN_KEY, IS_NULLABLE "
                        "FROM information_schema.COLUMNS "
                        "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
                        "ORDER BY ORDINAL_POSITION",
                        (ds.get("database_name") or "", table_name),
                    )
                return cur.fetchall()
        finally:
            conn.close()


# ── 模块级单例 ────────────────────────────────────────────────────────

datasource_service = DatasourceService()
