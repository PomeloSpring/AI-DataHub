"""显式部署 Waker 会话表或核对本机中断执行；不会在请求中自动建表。"""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description="Waker 会话部署与恢复检查")
    parser.add_argument("action", choices=["migrate", "reconcile", "status"])
    args = parser.parse_args()
    from services.shared.common.db import execute_write, execute_query
    if args.action == "migrate":
        sql = (ROOT / "docker/mysql/agent_sessions_migration.sql").read_text(encoding="utf-8")
        execute_write(sql)
        print("Waker 会话表迁移完成")
    elif args.action == "reconcile":
        from services.datamind.execution.session_workspace import reconcile_stale_sessions
        print(f"已核对并标记中断的执行：{reconcile_stale_sessions()}")
    else:
        print(execute_query("SELECT status, COUNT(*) AS total FROM adh_agent_sessions GROUP BY status"))


if __name__ == "__main__":
    main()
