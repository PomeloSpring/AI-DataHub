#!/usr/bin/env python3
"""存量工作空间目录迁移: data/workspaces/ws_{wid}/ → data/user/{uid}/workspace/{wid}/

两个阶段都必须做，缺任一个都会留下"能查到会话但执行即崩"的半成品状态：

① 目录搬迁 + DB 水位（`adh_agent_sessions.relative_dir`）；
② 会话目录内执行层 CLI（qodercli）状态的**绝对路径改写**——CLI 把 resume 用的
   工作目录记在 `runtime/<配置目录>/projects/<工程 slug>/<sessionId>.jsonl` 首条
   `workspace-directories` 记录里，目录名本身也是旧路径的 slug。只做①的话，续聊时
   CLI 会去 `addDirectory(旧绝对路径)`，目录不存在即 `resolveAndValidateDir` 抛错、
   子进程 exit 1，表现为 AS-BOT "An unexpected critical error occurred"。

幂等：已迁移的 relative_dir / 已改写的状态文件都不会被再次改动。执行前请停止 web 进程。

用法:
    cd /home/star/project/feature/AI-DataHub
    venv/bin/python -m backend.scripts.migrate_workspace_user_paths [--dry-run]
    # 只补②（目录/DB 已迁完，仅 CLI 状态残留旧路径）:
    venv/bin/python -m backend.scripts.migrate_workspace_user_paths --skip-db
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

# 确保项目根可导入: 本文件在 backend/scripts/ 下，向上三级到项目根
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# CLI 状态里带路径的都是文本文件；二进制（加密 state.json 等）一律不碰。
TEXT_SUFFIXES = {".jsonl", ".json", ".log", ".txt"}
MAX_TEXT_BYTES = 64 * 1024 * 1024
# 以工程 slug（旧绝对路径的 '-' 形式）命名子目录的状态目录
STATE_SLUG_DIRS = ("projects", "logs/sessions", "tmp")


def project_slug(path: Path | str) -> str:
    """执行层 CLI 的工程目录名: 绝对路径里的 / 与 _ 一律替换为 -。"""
    return str(path).replace("/", "-").replace("_", "-")


def migrate_layout(new_base: Path, old_base: Path, dry_run: bool) -> int:
    """阶段①：搬 ws_{wid} 目录并把 relative_dir 更新为新布局（返回迁移条数）。"""
    from backend.common.db.metadata_db import get_metadata_conn

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT session_key, user_id, workspace_id, relative_dir "
                "FROM adh_agent_sessions WHERE relative_dir LIKE 'ws\\_%%'")
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        print("阶段①: 无需迁移（所有会话 relative_dir 已是新格式或无存量记录）。")
        return 0

    moved_dirs: set[str] = set()
    migrated = 0
    for row in rows:
        old_rel = row["relative_dir"]          # e.g. "ws_301/sessions/abc..."
        uid = int(row["user_id"])
        wid = int(row["workspace_id"])
        key = row["session_key"]
        new_rel = f"{uid}/workspace/{wid}/sessions/{key}"
        if dry_run:
            print(f"[DRY-RUN] 阶段① {old_rel}  →  {new_rel}")
            migrated += 1
            continue
        ws_dir_marker = f"ws_{wid}"
        if ws_dir_marker not in moved_dirs:
            old_ws = old_base / ws_dir_marker
            new_ws = new_base / str(uid) / "workspace" / str(wid)
            if old_ws.is_dir() and not new_ws.exists():
                new_ws.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old_ws), str(new_ws))
                print(f"  moved: {old_ws}  →  {new_ws}")
            elif not old_ws.is_dir() and new_ws.is_dir():
                pass                             # 已搬迁
            else:
                print(f"  WARN: 源目录不存在或目标已存在, 跳过: {old_ws}", file=sys.stderr)
            moved_dirs.add(ws_dir_marker)
        migrated += 1

    if not dry_run:
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                for row in rows:
                    new_rel = (f"{int(row['user_id'])}/workspace/{int(row['workspace_id'])}"
                               f"/sessions/{row['session_key']}")
                    cur.execute(
                        "UPDATE adh_agent_sessions SET relative_dir=%s "
                        "WHERE session_key=%s AND relative_dir=%s",
                        (new_rel, row["session_key"], row["relative_dir"]))
            conn.commit()
        finally:
            conn.close()
        old_node = old_base / ".storage-node"
        new_node = new_base / ".storage-node"
        if old_node.is_file() and not new_node.exists():
            new_base.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_node), str(new_node))
            print(f"  moved: {old_node}  →  {new_node}")
    print(f"阶段①: {migrated} 条记录{'已计划' if dry_run else '已迁移'}。")
    return migrated


def _replace_paths_in_file(path: Path, pairs: list[tuple[bytes, bytes]], dry_run: bool) -> bool:
    """按字节替换文本状态文件里的旧路径；无命中返回 False（幂等关键）。"""
    try:
        if path.stat().st_size > MAX_TEXT_BYTES:
            return False
        raw = path.read_bytes()
    except OSError:
        return False
    updated = raw
    for old, new in pairs:
        if old in updated:
            updated = updated.replace(old, new)
    if updated == raw:
        return False
    if dry_run:
        return True
    tmp = path.with_name(path.name + ".migrate.tmp")
    try:
        with tmp.open("wb") as fh:
            fh.write(updated)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    return True


def _merge_dir(src: Path, dst: Path) -> None:
    """递归合并 src 到 dst；同名文件保留 dst（迁移后新写入的为准），旧副本改名留证。"""
    dst.mkdir(parents=True, exist_ok=True)
    for child in sorted(src.iterdir()):
        target = dst / child.name
        if child.is_dir():
            _merge_dir(child, target)
            if not any(child.iterdir()):
                child.rmdir()
        elif target.exists():
            shutil.move(str(child), str(dst / f"{child.name}.pre-migration"))
        else:
            shutil.move(str(child), str(target))


def _relocate_dir(src: Path, dst: Path, dry_run: bool) -> bool:
    """把 src 更名为 dst；dst 已存在时合并历史，不覆盖同名项、不丢转录。"""
    if dry_run:
        return True
    if dst.exists() and not dst.is_dir():
        print(f"  WARN: 目标同名且非目录, 跳过: {dst}", file=sys.stderr)
        return False
    if dst.exists():
        _merge_dir(src, dst)
        try:
            src.rmdir()
        except OSError:
            print(f"  WARN: 合并后源目录非空, 保留待人工核对: {src}", file=sys.stderr)
    else:
        src.rename(dst)
    return True


def repair_session_state(session_root: Path, old_root: Path, dry_run: bool) -> tuple[int, int]:
    """阶段②（单会话）：把 CLI 状态里的旧绝对路径/slug 改写为当前目录。

    返回 (重命名目录数, 改写文件数)。目录不存在或无残留时返回 (0, 0)。
    """
    old_str, new_str = str(old_root), str(session_root)
    old_slug, new_slug = project_slug(old_root), project_slug(session_root)
    if old_str == new_str:
        return 0, 0
    pairs = [(old_str.encode(), new_str.encode()), (old_slug.encode(), new_slug.encode())]
    runtime_dir = session_root / "runtime"
    if not runtime_dir.is_dir():
        return 0, 0
    renamed = 0
    for cfg in sorted(p for p in runtime_dir.iterdir() if p.is_dir()):
        for sub in STATE_SLUG_DIRS:
            parent = cfg / sub
            if not parent.is_dir():
                continue
            for child in sorted(parent.iterdir()):
                if not child.is_dir() or old_slug not in child.name:
                    continue
                target = parent / child.name.replace(old_slug, new_slug)
                if _relocate_dir(child, target, dry_run):
                    renamed += 1
                    print(f"  rename: {child.relative_to(session_root)}  →  {target.relative_to(session_root)}")
    rewritten = 0
    for dirpath, _dirnames, filenames in os.walk(runtime_dir):
        for name in filenames:
            path = Path(dirpath) / name
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            if _replace_paths_in_file(path, pairs, dry_run):
                rewritten += 1
                print(f"  rewrite: {path.relative_to(session_root)}")
    return renamed, rewritten


def repair_sdk_state(new_base: Path, old_base: Path, dry_run: bool) -> tuple[int, int, int]:
    """阶段②：扫描新布局下所有会话目录，改写残留的旧绝对路径（返回命中会话数/目录/文件）。"""
    if not new_base.is_dir():
        print(f"阶段②: 存储根不存在，跳过: {new_base}")
        return 0, 0, 0
    hit = renamed = rewritten = 0
    for session_root in sorted(new_base.glob("*/workspace/*/sessions/*")):
        if not session_root.is_dir():
            continue
        parts = session_root.parts
        if not (parts[-5].isdigit() and parts[-3].isdigit() and parts[-4] == "workspace" and parts[-2] == "sessions"):
            continue                             # 非 {uid}/workspace/{wid}/sessions/{key} 布局
        wid = int(parts[-3])
        key = parts[-1]
        r, w = repair_session_state(session_root, old_base / f"ws_{wid}" / "sessions" / key, dry_run)
        if r or w:
            hit += 1
            renamed += r
            rewritten += w
    prefix = "阶段②: " + ("计划修复" if dry_run else "修复完成")
    print(f"{prefix}: {hit} 个会话, 重命名 {renamed} 个状态目录, 改写 {rewritten} 个状态文件。")
    return hit, renamed, rewritten


def audit_db_against_disk(new_base: Path) -> int:
    """对账：DB 有会话映射但工作区目录已不存在（历史数据真丢了，无法迁移）。

    只报告不删记录：这些会话续聊时会得到“请新建会话”的显式提示，
    但不能让数据丢失只在运行时以莫名错误形式出现（no-silent-degradation）。
    返回缺失个数。
    """
    from backend.common.db.metadata_db import get_metadata_conn

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT session_key, relative_dir, status FROM adh_agent_sessions")
            rows = cur.fetchall()
    finally:
        conn.close()
    missing = [r for r in rows if not (new_base / str(r["relative_dir"])).is_dir()]
    for row in missing:
        print(f"  WARN: 会话目录不存在（无法恢复，聊天记录仍在）: "
              f"{row['session_key'][:8]}… {row['relative_dir']} status={row['status']}", file=sys.stderr)
    print(f"对账: {len(rows) - len(missing)}/{len(rows)} 个会话工作区可用"
          + (f"，{len(missing)} 个已丢失（需告知用户新建会话）" if missing else "。"))
    return len(missing)


def recreate_missing_dirs(new_base: Path, dry_run: bool) -> int:
    """为“表里有映射、磁盘上目录已不存在”的存量会话重建工作区并归位到可用工作空间。

    旧目录已确认不在磁盘上（无从找回），这些会话会永远卡在“目录不存在”：
    重建目录 + 清空失效的 SDK 会话标识后，下一轮由服务端重放聊天历史建新 SDK 会话
    （与 interrupted 恢复同一条路径，不伪造任何历史）；ws=0 遗留归属改挂到属主默认
    工作空间，表里的 workspace_id / relative_dir 同步改写。
    只对 idle/interrupted 且未持有执行权的行生效，绝不移动已存在的目录。
    """
    from backend.common.db.metadata_db import get_metadata_conn
    from backend.modules.mind.execution.session_workspace import session_paths

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT session_key, conversation_id, user_id, workspace_id, relative_dir, "
                        "status, sdk_session_id FROM adh_agent_sessions "
                        "ORDER BY workspace_id, created_at")
            rows = cur.fetchall()
            missing = [r for r in rows if not (new_base / str(r["relative_dir"])).is_dir()]
            for row in missing:
                if row["status"] not in ("idle", "interrupted"):
                    print(f"  SKIP: 状态不可重建（{row['status']}）: {row['session_key'][:8]}…", file=sys.stderr)
                    continue
                uid, wid = int(row["user_id"]), int(row["workspace_id"])
                if wid <= 0:                     # ws=0 为统一改造前的遗留归属，不再新建该目录
                    cur.execute("SELECT id FROM adh_workspaces WHERE owner_id=%s "
                                "ORDER BY is_default DESC, id ASC LIMIT 1", (uid,))
                    home = cur.fetchone()
                    if not home:
                        print(f"  ERROR: 用户 {uid} 无可用工作空间，无法重建 "
                              f"{row['session_key'][:8]}…", file=sys.stderr)
                        continue
                    wid = int(home["id"])
                target = new_base / str(uid) / "workspace" / str(wid) / "sessions" / row["session_key"]
                action = ("已存在(只清 sdk_session_id)" if target.is_dir() else "新建") + \
                         (f" ws {row['workspace_id']}→{wid}" if wid != int(row["workspace_id"]) else "")
                print(f"  {'[DRY-RUN] ' if dry_run else ''}recreate: {row['session_key'][:8]}… {action}  {target}")
                if dry_run:
                    continue
                if not target.is_dir():
                    session_paths(new_base, row["session_key"], wid, uid, create=True)
                cur.execute("UPDATE adh_agent_sessions SET workspace_id=%s, relative_dir=%s, "
                            "sdk_session_id='', status='idle', version=version+1, "
                            "updated_at=UTC_TIMESTAMP(6) "
                            "WHERE session_key=%s AND status=%s AND execution_token IS NULL",
                            (wid, str(target.relative_to(new_base)), row["session_key"], row["status"]))
                if cur.rowcount != 1:
                    raise RuntimeError(f"会话 {row['session_key'][:8]}… 状态已变化，未提交归位，请重试")
                if row["conversation_id"] and int(row["workspace_id"]) != wid:
                    cur.execute("UPDATE adh_conversations SET workspace_id=%s "
                                "WHERE id=%s AND workspace_id=0", (wid, row["conversation_id"]))
            if not dry_run:
                conn.commit()
    finally:
        conn.close()
    print(f"重建: {'计划' if dry_run else '完成'} {len(missing)} 个目录缺失的会话。")
    return len(missing)


def main() -> int:
    parser = argparse.ArgumentParser(description="迁移工作空间目录到 user/{uid}/workspace/{wid} 布局，并修复存量会话的 CLI resume 状态路径")
    parser.add_argument("--dry-run", action="store_true", help="仅打印计划，不实际移动/改写")
    parser.add_argument("--skip-db", action="store_true",
                        help="跳过阶段①（目录/DB 已迁完），只补阶段② CLI 状态路径改写")
    parser.add_argument("--legacy-base", default=str(_PROJECT_ROOT / "data" / "workspaces"),
                        help="旧布局根目录（默认 data/workspaces）")
    parser.add_argument("--recreate-missing-dirs", action="store_true",
                        help="为“表里有映射、目录已不存在”的会话重建工作区并归位到默认工作空间")
    args = parser.parse_args()

    from backend.common.config import ADH_WORKSPACES_DIR
    new_base = Path(ADH_WORKSPACES_DIR).resolve()
    old_base = Path(args.legacy_base).resolve()
    failures: list[str] = []

    if args.skip_db:
        print("阶段①: 已按 --skip-db 跳过（也不做 DB 对账）。")
    else:
        try:
            migrate_layout(new_base, old_base, args.dry_run)
            if not args.dry_run:
                audit_db_against_disk(new_base)
            if args.recreate_missing_dirs:
                recreate_missing_dirs(new_base, args.dry_run)
        except Exception as exc:                 # 元数据库不可达等：显式暴露，不静默跳过
            failures.append(f"阶段①未完成: {exc}")
            print(f"ERROR: {failures[-1]}", file=sys.stderr)

    repair_sdk_state(new_base, old_base, args.dry_run)

    if old_base.is_dir() and any(old_base.iterdir()):
        leftovers = ", ".join(sorted(p.name for p in old_base.iterdir())[:10])
        print(f"WARN: 旧布局根仍有残留，需人工确认后清理: {old_base}（{leftovers}）", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
