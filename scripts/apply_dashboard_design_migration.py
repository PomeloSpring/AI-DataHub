"""应用已审批的仪表盘设计 schema 迁移，不修改现有业务记录。"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def apply():
    from services.shared.common.db.metadata_db import get_metadata_conn
    migration = Path(__file__).resolve().parents[1] / 'docker/mysql/dashboard_design_migration.sql'
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            for statement in migration.read_text(encoding='utf-8').split(';'):
                if statement.strip():
                    cur.execute(statement)
        conn.commit()
    finally:
        conn.close()


if __name__ == '__main__':
    if sys.argv[1:] != ['--apply']:
        raise SystemExit('请传 --apply 显式应用迁移')
    apply()
    print('仪表盘设计迁移已应用')
