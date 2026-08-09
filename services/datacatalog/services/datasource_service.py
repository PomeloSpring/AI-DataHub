"""数据源服务 — 管理数据库连接的业务逻辑。

从 backend/api/datasource.py 迁移而来。
提供数据源的 CRUD、连接测试、表/列查询、SQL 执行等功能。
"""

import logging
import time
from datetime import datetime
from typing import Optional

import pymysql

from services.shared.common.config import (
    DORIS_HOST, DORIS_PORT, DORIS_USER, DORIS_PASSWORD, DORIS_DATABASE,
)
from services.shared.common.crypto import encrypt_password, decrypt_password, is_encrypted
from services.shared.common.db.metadata_db import get_metadata_conn
from services.shared.common.ttl_cache import datasource_cache

logger = logging.getLogger(__name__)

# Try to import elasticsearch
try:
    from elasticsearch import Elasticsearch
    HAS_ELASTICSEARCH = True
except ImportError:
    HAS_ELASTICSEARCH = False


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


# ── 数据源服务 ────────────────────────────────────────────────────────

class DatasourceService:
    """数据源管理服务。"""

    async def list_datasources(self, workspace_id: int = 0) -> list[dict]:
        """列出所有数据源。

        Args:
            workspace_id: 工作空间 ID，0 表示不限制。

        Returns:
            数据源列表，密码已脱敏。
        """
        self._ensure_default_datasource()
        try:
            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    if workspace_id:
                        cur.execute(
                            "SELECT d.id, d.name, d.db_type, d.host, d.port, d.username, "
                            "d.database_name, d.is_default, d.`ssl`, d.owner_id, d.created_at, d.updated_at "
                            "FROM adh_datasources d "
                            "JOIN adh_workspace_datasources wd ON wd.datasource_id = d.id "
                            "WHERE wd.workspace_id = %s "
                            "ORDER BY d.is_default DESC, d.name ASC",
                            (workspace_id,),
                        )
                    else:
                        cur.execute(
                            "SELECT id, name, db_type, host, port, username, database_name, "
                            "is_default, `ssl`, owner_id, created_at, updated_at "
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
                "created_at": "",
                "updated_at": "",
            }]

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
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                if data.get("is_default"):
                    cur.execute("UPDATE adh_datasources SET is_default = 0")
                cur.execute(
                    "INSERT INTO adh_datasources "
                    "(id, name, db_type, host, port, username, password, database_name, "
                    "is_default, `ssl`, owner_id, created_at, updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        ds_id, data["name"], data.get("db_type", "mysql"),
                        data["host"], data.get("port", 3306),
                        data.get("username", ""),
                        encrypt_password(data.get("password", "")),
                        data.get("database_name") or "",
                        1 if data.get("is_default") else 0,
                        1 if data.get("ssl") else 0,
                        owner_id, now, now,
                    ),
                )
            conn.commit()
        finally:
            conn.close()

        # 清除缓存
        datasource_cache.invalidate(f"ds_{ds_id}")
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
                    updates.append("name = %s")
                    params.append(data["name"])
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

                params.append(ds_id)
                cur.execute(
                    f"UPDATE adh_datasources SET {', '.join(updates)} WHERE id = %s",
                    params,
                )
            conn.commit()
        finally:
            conn.close()

        # 清除缓存
        datasource_cache.invalidate(f"ds_{ds_id}")
        return {"success": True}

    async def delete_datasource(self, ds_id: int) -> dict:
        """删除数据源。

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
        return {"success": True}

    async def test_connection(self, ds_id: int) -> dict:
        """测试数据源连接。

        Args:
            ds_id: 数据源 ID。

        Returns:
            包含 success 和 message 的字典。
        """
        ds = await self.get_datasource_raw(ds_id)
        if not ds:
            return {"success": False, "message": "数据源不存在"}

        try:
            conn = self.get_datasource_conn_from_dict(ds)
            if ds.get("db_type") == "elasticsearch":
                info = conn.info()
                conn.close()
                return {
                    "success": True,
                    "message": f"连接成功 - ES {info.get('version', {}).get('number', 'unknown')}",
                }
            else:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                conn.close()
                return {"success": True, "message": "连接成功"}
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

        if ds.get("db_type") == "elasticsearch":
            return await self._list_es_indices(ds)
        else:
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

        if ds.get("db_type") == "elasticsearch":
            return await self._list_es_fields(ds, table_name)
        else:
            return await self._list_db_columns(ds, table_name)

    async def execute_sql(self, ds_id: int, sql: str, limit: int = 200) -> dict:
        """执行 SQL 查询。

        Args:
            ds_id: 数据源 ID。
            sql: SQL 语句。
            limit: 结果行数限制。

        Returns:
            包含 columns, rows, row_count, elapsed_ms 的字典。

        Raises:
            ValueError: 数据源不存在或 SQL 为空时抛出。
            PermissionError: 非查询语句时抛出。
        """
        ds = await self.get_datasource_raw(ds_id)
        if not ds:
            raise ValueError("数据源不存在")

        sql = sql.strip()
        if not sql:
            raise ValueError("SQL 不能为空")

        start = time.time()

        if ds.get("db_type") == "elasticsearch":
            return await self._execute_es_sql(ds, sql, start)
        else:
            return await self._execute_db_sql(ds, sql, start)

    def get_datasource_conn(
        self, db_type: str, host: str, port: int,
        user: str, password: str, database: str = None, ssl: bool = False,
    ):
        """创建数据库连接（工厂方法）。

        Args:
            db_type: 数据库类型 (mysql/doris/elasticsearch)。
            host: 主机地址。
            port: 端口。
            user: 用户名。
            password: 密码。
            database: 数据库名。
            ssl: 是否启用 SSL。

        Returns:
            pymysql 连接或 Elasticsearch 客户端。
        """
        if db_type == "elasticsearch":
            if not HAS_ELASTICSEARCH:
                raise ValueError("Elasticsearch 库未安装，请执行: pip install elasticsearch")
            protocol = "https" if ssl else "http"
            es_url = f"{protocol}://{host}:{port}"
            es_kwargs = {"hosts": [es_url], "request_timeout": 30, "meta_header": False}
            if ssl:
                es_kwargs["verify_certs"] = False
                es_kwargs["ssl_show_warn"] = False
            if user and password:
                es_kwargs["basic_auth"] = (user, password)
            elif user:
                es_kwargs["basic_auth"] = (user, "")
            return Elasticsearch(**es_kwargs)
        else:
            conn_kwargs = {
                "host": host,
                "port": port,
                "user": user,
                "password": password,
                "database": database or None,
                "charset": "utf8mb4",
                "cursorclass": pymysql.cursors.DictCursor,
                "connect_timeout": 10,
                "read_timeout": 30,
            }
            if ssl:
                conn_kwargs["ssl"] = {
                    "ssl_disabled": False,
                    "ssl_verify_cert": False,
                    "ssl_verify_identity": False,
                }
            return pymysql.connect(**conn_kwargs)

    def get_datasource_conn_from_dict(self, ds: dict):
        """从数据源字典创建连接。

        Args:
            ds: 数据源配置字典。

        Returns:
            数据库连接对象。
        """
        return self.get_datasource_conn(
            db_type=ds.get("db_type", "mysql"),
            host=ds["host"],
            port=ds["port"],
            user=ds.get("username", ""),
            password=ds.get("password", ""),
            database=ds.get("database_name"),
            ssl=bool(ds.get("ssl", 0)),
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

    # ── 内部方法：ES 操作 ──────────────────────────────────────────────

    async def _list_es_indices(self, ds: dict) -> list:
        """列出 Elasticsearch 索引。"""
        conn = self.get_datasource_conn_from_dict(ds)
        try:
            try:
                indices = conn.indices.get_alias(index="*")
            except Exception:
                # 降级：使用 cat.indices
                cat = conn.cat.indices(format="json", h="index,docs.count,store.size")
                result = []
                for row in cat:
                    index_name = row.get("index", "")
                    if not index_name.startswith("."):
                        result.append({
                            "TABLE_NAME": index_name,
                            "TABLE_COMMENT": "ES Index",
                            "TABLE_ROWS": int(row.get("docs.count", 0) or 0),
                        })
                return result

            result = []
            for index_name in sorted(indices.keys()):
                if not index_name.startswith("."):
                    result.append({
                        "TABLE_NAME": index_name,
                        "TABLE_COMMENT": "ES Index",
                        "TABLE_ROWS": 0,
                    })
            return result
        finally:
            conn.close()

    async def _list_es_fields(self, ds: dict, table_name: str) -> list:
        """列出 Elasticsearch 索引的字段。"""
        conn = self.get_datasource_conn_from_dict(ds)
        try:
            mapping = conn.indices.get_mapping(index=table_name)
            result = []
            if table_name in mapping:
                mapping_body = (
                    mapping[table_name]
                    if isinstance(mapping[table_name], dict)
                    else mapping[table_name].body
                    if hasattr(mapping[table_name], "body")
                    else {}
                )
                properties = mapping_body.get("mappings", {}).get("properties", {})
                for field_name, field_info in properties.items():
                    result.append({
                        "COLUMN_NAME": field_name,
                        "DATA_TYPE": field_info.get("type", "text"),
                        "COLUMN_COMMENT": "",
                        "COLUMN_KEY": "",
                        "IS_NULLABLE": "YES",
                    })
            return result
        finally:
            conn.close()

    async def _execute_es_sql(self, ds: dict, sql: str, start: float) -> dict:
        """通过 ES SQL API 执行查询。"""
        from services.datamind.nl2sql.sql.query_executor import _build_es_client

        params = {
            "host": ds["host"], "port": ds["port"],
            "user": ds.get("username"), "password": ds.get("password"),
            "ssl": bool(ds.get("ssl", 0)),
        }
        es = _build_es_client(params)
        try:
            result = es.sql.query(body={"query": sql})
            columns_info = result.get("columns", [])
            rows_data = result.get("rows", [])
            if not columns_info or not rows_data:
                return {
                    "columns": [], "rows": [], "row_count": 0,
                    "elapsed_ms": int((time.time() - start) * 1000),
                }
            col_names = [col.get("name", f"col_{i}") for i, col in enumerate(columns_info)]
            rows = [dict(zip(col_names, row)) for row in rows_data]
            elapsed_ms = int((time.time() - start) * 1000)
            return {
                "columns": col_names, "rows": rows,
                "row_count": len(rows), "elapsed_ms": elapsed_ms,
            }
        finally:
            es.close()

    async def _list_db_tables(self, ds: dict) -> list:
        """列出 MySQL/Doris 数据库的表。"""
        conn = self.get_datasource_conn_from_dict(ds)
        try:
            with conn.cursor() as cur:
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
        """列出 MySQL/Doris 表的列。"""
        conn = self.get_datasource_conn_from_dict(ds)
        try:
            with conn.cursor() as cur:
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

    async def _execute_db_sql(self, ds: dict, sql: str, start: float) -> dict:
        """执行 MySQL/Doris SQL 查询。"""
        upper = sql.upper().lstrip()
        if not (
            upper.startswith("SELECT")
            or upper.startswith("WITH")
            or upper.startswith("SHOW")
            or upper.startswith("DESC")
        ):
            raise PermissionError("仅允许 SELECT/SHOW/DESC 查询")

        conn = self.get_datasource_conn_from_dict(ds)
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
            elapsed_ms = int((time.time() - start) * 1000)
            columns = list(rows[0].keys()) if rows else []
            for row in rows:
                for k, v in row.items():
                    if hasattr(v, "isoformat"):
                        row[k] = v.isoformat()
                    elif isinstance(v, bytes):
                        row[k] = v.decode("utf-8", errors="replace")
            return {
                "columns": columns, "rows": rows,
                "row_count": len(rows), "elapsed_ms": elapsed_ms,
            }
        finally:
            conn.close()


# ── 模块级单例 ────────────────────────────────────────────────────────

datasource_service = DatasourceService()
