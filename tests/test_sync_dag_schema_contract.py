"""同步任务表 schema 契约回归：代码引用的列必须存在于权威迁移 DDL。

背景（真实事故）：sync_dag_migration.sql 用 CREATE TABLE IF NOT EXISTS 建 adh_sync_tasks，
撞上运行时同名旧结构表被静默跳过，beat 查询 `timezone` 报 1054 → **整个周期调度加载失败**。
本契约把"代码引用列 ⊆ 迁移 DDL"固化成门禁；结构对齐迁移的 DDL 也必须与权威迁移一致。
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DDL_FILES = {
    "authority": REPO / "docker/mysql/sync_dag_migration.sql",
    "realign": REPO / "docker/mysql/sync_dag_schema_realign_migration.sql",
}
CODE_DIR = REPO / "backend/modules/flow"
TABLES = ("adh_sync_tasks", "adh_sync_logs")

_SQL_KEYWORDS = {
    "and", "or", "not", "null", "now", "count", "sum", "max", "min", "coalesce", "concat",
    "case", "when", "then", "else", "end", "distinct", "left", "right", "join", "on", "as",
    "values", "set", "where", "interval", "minute", "day", "desc", "asc", "order", "by",
    "limit", "offset", "group", "json_length", "ifnull", "if", "date_add", "date_sub",
    "current_timestamp", "select", "from", "insert", "into", "update", "delete", "is",
    "in", "like", "between", "exists", "union", "all", "duplicate", "key", "id",
}


def _ddl_columns(path: Path) -> dict:
    """解析迁移文件中各表的列名集合。"""
    text = path.read_text(encoding="utf-8")
    result = {}
    for table in TABLES:
        m = re.search(rf"CREATE TABLE IF NOT EXISTS {table}\s*\((.*?)\)\s*ENGINE",
                      text, re.S)
        assert m, f"{path.name} 缺少 {table} 的建表语句"
        cols = set()
        for line in m.group(1).splitlines():
            line = line.strip().rstrip(",")
            if not line or line.startswith(("PRIMARY", "UNIQUE", "INDEX", "KEY", "--")):
                continue
            name = re.match(r"^`?([a-z_][a-z0-9_]*)`?\s+\w+", line)
            if name:
                cols.add(name.group(1))
        result[table] = cols
    return result


def _clean_token(token: str):
    """返回 (列名, 是否带表别名前缀)；别名/前缀均剥掉。"""
    token = re.sub(r"\bAS\s+\w+", "", token, flags=re.I)   # 丢别名
    token = token.strip().strip("`")
    qualified = bool(re.match(r"^[a-z_][a-z0-9_]*\.", token))
    token = re.sub(r"^[a-z_][a-z0-9_]*\.", "", token)       # 丢表别名前缀 sl./t.
    return token.lower(), qualified


def _referenced_columns() -> tuple:
    """扫描 backend/modules/flow 的 SQL 字面量，收集对两张表的列引用。

    返回 (按表归属的列引用, 带表别名前缀的列引用)。后者来自 JOIN 查询，无法
    静态归属到具体表，只要求存在于两张表的并集中。
    """
    refs = {table: set() for table in TABLES}
    refs_any = set()
    insert_re = re.compile(rf"INSERT INTO\s+({'|'.join(TABLES)})\s*\(([^)]*)\)", re.S)
    # 限定单个子查询内捕获，不跨 UNION 的其它 SELECT
    select_re = re.compile(
        rf"SELECT\s+((?:(?!\bFROM\b).)+?)\s+FROM\s+({'|'.join(TABLES)})\b", re.S)
    update_re = re.compile(rf"UPDATE\s+({'|'.join(TABLES)})\s+SET\s+(.+?)(?:WHERE|$)", re.S)

    def consider(table, raw, allow_qualified: bool):
        for piece in re.split(r",|\n", raw):
            token, qualified = _clean_token(piece)
            if not token or "(" in piece or "*" in piece or "'" in piece:
                continue
            if "=" in piece:                      # UPDATE 赋值取左值
                token, qualified = _clean_token(piece.split("=")[0])
            if not re.fullmatch(r"[a-z_][a-z0-9_]*", token):
                continue
            if token in _SQL_KEYWORDS:
                continue
            if qualified and allow_qualified:
                refs_any.add(token)
            else:
                refs[table].add(token)

    for py in CODE_DIR.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for table, cols in insert_re.findall(text):
            consider(table, cols, allow_qualified=False)
        for cols, table in select_re.findall(text):
            consider(table, cols, allow_qualified=True)
        for table, cols in update_re.findall(text):
            consider(table, cols, allow_qualified=True)
    return refs, refs_any


@pytest.fixture(scope="module")
def authority():
    return _ddl_columns(DDL_FILES["authority"])


def test_realign_migration_matches_authority(authority):
    """结构对齐迁移的列集必须与权威迁移一致（复制的 DDL 不得漂移）。"""
    assert _ddl_columns(DDL_FILES["realign"]) == authority


def test_code_referenced_columns_exist_in_ddl(authority):
    """代码 SQL 引用的列必须在 DDL 中声明——1054 类事故的回归门禁。"""
    refs, refs_any = _referenced_columns()
    known = authority["adh_sync_tasks"] | authority["adh_sync_logs"]
    for table in TABLES:
        missing = refs[table] - authority[table]
        assert not missing, (
            f"{table} 被代码引用但未在 {DDL_FILES['authority'].name} 声明的列: "
            f"{sorted(missing)}（新增列请同步迁移 DDL）")
    missing_any = refs_any - known
    assert not missing_any, (
        f"JOIN 查询引用了两表均不存在的列: {sorted(missing_any)}（新增列请同步迁移 DDL）")


def test_beat_sync_query_columns_present(authority):
    """beat 周期调度依赖的关键列必须存在（事故直接现场）。"""
    assert {"id", "schedule_cron", "timezone", "is_active", "owner_id"} <= authority["adh_sync_tasks"]
