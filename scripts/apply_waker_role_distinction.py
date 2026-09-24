"""应用 Waker 职责区分迁移（ChatBI分析师=语义层 / 数据分析师=nl2sql）。

不采用朴素 split(';')：system_prompt/persona 文本内含分号。改为按单引号状态机切分语句，
并跳过 USE 行（连接已指向目标元数据库，如本地 docker=adh、远端=adh2）。幂等，可重复执行。
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MIGRATION = Path(__file__).resolve().parents[1] / 'docker/mysql/waker_role_distinction_migration.sql'


def split_statements(sql: str) -> list[str]:
    """按分号切分 SQL，忽略单引号字符串内的分号；'' 视为转义的单引号。"""
    stmts, buf, in_str, i, n = [], [], False, 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            if in_str and i + 1 < n and sql[i + 1] == "'":  # 转义 ''
                buf.append("''")
                i += 2
                continue
            in_str = not in_str
            buf.append(ch)
        elif ch == ';' and not in_str:
            stmts.append(''.join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    if ''.join(buf).strip():
        stmts.append(''.join(buf))
    return stmts


def _strip_line_comments(stmt: str) -> str:
    """去掉整行 -- 注释, 便于判断语句真正的开头关键词。"""
    kept = [ln for ln in stmt.splitlines() if not ln.lstrip().startswith('--')]
    return '\n'.join(kept).strip()


def apply():
    from services.shared.common.db.metadata_db import get_metadata_conn
    conn = get_metadata_conn()
    affected = []
    try:
        with conn.cursor() as cur:
            for stmt in split_statements(MIGRATION.read_text(encoding='utf-8')):
                body = _strip_line_comments(stmt)
                if not body or body.upper().startswith('USE '):
                    continue  # 跳过 USE：连接已在目标库
                cur.execute(body)
                if body.upper().startswith('UPDATE'):
                    affected.append(cur.rowcount)
        conn.commit()
    finally:
        conn.close()
    return affected


if __name__ == '__main__':
    if sys.argv[1:] != ['--apply']:
        raise SystemExit('请传 --apply 显式应用迁移')
    rows = apply()
    print(f'Waker 职责区分迁移已应用，更新行数: {rows}')
