"""本地持久卷上的独立会话；MySQL 是身份、resume 与执行互斥的唯一真值源。

会话产物同时镜像到云空间（对象存储，见 session_cloud）：本地目录缺失时由 claim/读取
路径按需回落，换节点或卷被清理都不再意味着产物丢失。
"""
import json
import logging
import os
from pathlib import Path
import re
import uuid
from dataclasses import dataclass, field

from fastapi import HTTPException
from backend.common.db import DBConnection, execute_query, execute_write


async def run_owned_sync(function, *args):
    """等待本请求启动的线程退出后才传播取消，避免领取或写操作成为孤儿。"""
    import asyncio
    import anyio
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with anyio.CancelScope(shield=True):
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            task.result()
        raise


def session_cloud_ensure(row, *, write=None) -> bool:
    """云空间回落薄封装（延迟导入避免与本模块循环依赖；语义见 session_cloud）。"""
    from backend.modules.mind.execution import session_cloud
    return session_cloud.ensure_local_workspace(row, write=write)


def process_identity(pid):
    if not isinstance(pid, int) or pid <= 0:
        return ""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        fields = stat[stat.rfind(")") + 2:].split()
        return "" if fields[0] == "Z" else fields[19]
    except FileNotFoundError:
        return ""


def workspace_base(create=True):
    from backend.common.config import ADH_WORKSPACES_DIR
    base = Path(ADH_WORKSPACES_DIR).resolve()
    if create:
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
    elif not base.is_dir():
        raise RuntimeError("会话存储卷不可用，不能确认目录已清理")
    return base


def storage_node(base, create=True):
    path = base / ".storage-node"
    if not path.exists() and create:
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", encoding="ascii", dir=base) as f:
            f.write(uuid.uuid4().hex)
            f.flush()
            os.fsync(f.fileno())
            try:
                os.link(f.name, path)
            except FileExistsError:
                pass
    if path.is_symlink():
        raise PermissionError("存储标识不能是符号链接")
    value = path.read_text(encoding="ascii")
    if not re.fullmatch(r"[a-f0-9]{32}", value):
        raise RuntimeError("本地工作区存储标识无效")
    return value


def workspace_disk_usage(workspace_id: int, user_id: int) -> int:
    """工作空间磁盘用量(字节): {user_id}/workspace/{workspace_id} 下全部文件递归求和。"""
    from backend.common.config import ADH_WORKSPACES_DIR
    root = Path(ADH_WORKSPACES_DIR).resolve() / str(user_id) / "workspace" / str(workspace_id)
    if not root.is_dir():
        return 0
    total = 0
    for p in root.rglob("*"):
        try:
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def remove_workspace_directory(workspace_id: int, user_id: int) -> int:
    """清理工作空间持久卷目录 `{user_id}/workspace/{workspace_id}`，返回删除的文件数。

    工作空间删除的第一阶段（库行未动，失败可重试）。口径与 _session_cleanup_root 一致：
    目录不存在即无残留（返回 0，不阻断）；绝不跟随符号链接、绝不在别的存储节点上动文件。
    """
    import shutil
    from backend.common.config import ADH_WORKSPACES_DIR
    if int(workspace_id) <= 0 or int(user_id) <= 0:
        raise HTTPException(400, "工作空间标识无效，未执行清理")
    # 跨节点保护：该空间仍有会话登记在其他存储节点时，本实例无法确认清理完成
    rows = execute_query(
        "SELECT DISTINCT storage_node FROM adh_agent_sessions WHERE workspace_id=%s", (workspace_id,))
    recorded_nodes = [r["storage_node"] for r in (rows or []) if r.get("storage_node")]
    base = Path(ADH_WORKSPACES_DIR).resolve()
    if not base.is_dir():
        # 存储卷整体不可用（挂载点丢失/已清）：无文件可清理，放行删除库行
        return 0
    if recorded_nodes:
        # 仅在确有会话登记时核验存储节点归属（与 _session_cleanup_root 同口径）
        current_node = storage_node(base, create=False)
        if any(node != current_node for node in recorded_nodes):
            raise HTTPException(409, "该工作空间的会话目录不在当前存储节点，请在对应节点重试删除")
    root = base / str(user_id) / "workspace" / str(workspace_id)
    if root.is_symlink():
        raise HTTPException(409, "工作空间目录被符号链接替换，未执行清理，请联系管理员")
    if not root.exists():
        return 0
    if not root.is_dir():
        raise HTTPException(409, "工作空间目录形态异常（不是目录），未执行清理，请联系管理员")
    if not shutil.rmtree.avoids_symlink_attacks:
        raise RuntimeError("当前运行环境不支持安全的工作空间目录清理")
    removed = sum(1 for p in root.rglob("*") if p.is_file() and not p.is_symlink())
    shutil.rmtree(root)
    if root.exists():
        raise HTTPException(409, "工作空间目录未完全清理，请重试删除")
    # 云空间副本随空间一并清除：只删本地而对象留着 = 孤儿产物（隐私与容量都说不通）。
    # 失败即中止（库行未动，用户可重试删除），不静默跳过。
    purge_workspace_cloud(workspace_id)
    return removed


def purge_workspace_cloud(workspace_id: int) -> int:
    """删除该工作空间下所有会话的产物云端副本，返回清除的对象数。

    会话 key 从执行映射取（删除库行之前调用，之后便无从定位）。未配置对象存储时
    session_cloud.purge_session_cloud 返回 0（本来就没有云端副本），不报错。
    """
    from backend.modules.mind.execution import session_cloud
    rows = execute_query("SELECT session_key, user_id FROM adh_agent_sessions WHERE workspace_id=%s",
                         (workspace_id,))
    purged = 0
    for r in rows or []:
        try:
            purged += session_cloud.purge_session_cloud(int(r["user_id"] or 0), r["session_key"])
        except Exception as exc:  # noqa: BLE001 — 转成可重试的显式失败，不留“已删库行但云端还在”
            raise HTTPException(409, f"云空间产物未能清除（{type(exc).__name__}），请重试删除") from exc
    return purged


def format_bytes(value: int) -> str:
    """可读体积（错误文案与用量提示共用）：给用户看字节数无法判断量级。"""
    v = float(max(0, int(value or 0)))
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024 or unit == "GB":
            return f"{v:.0f} {unit}" if unit in ("B", "KB") else f"{v:.1f} {unit}"
        v /= 1024
    return f"{v:.1f} GB"


def assert_workspace_disk_quota(workspace_id: int, user_id: int) -> None:
    """空间磁盘配额拦截: 用量已达生效配额则拒绝新建会话（提示归档/清理后重试）。

    配额**只从 `role_service.get_user_workspace_quota` 取**（登记行优先，无行取系统缺省），
    本函数不得再自己 SELECT：历史缺陷是这里把"无登记行"读成 0=不限制，而管理员页与建空间门
    按缺省显示"每空间 5GB"，双口径导致配额形同虚设（配了没用）。
    显式登记 disk_quota_bytes=0 仍是管理员的"不设限"逃生口，与缺省靠 is_default 区分。
    """
    from backend.core.role_service import role_service
    quota = int(role_service.get_user_workspace_quota(int(user_id)).get("disk_quota_bytes") or 0)
    if quota <= 0:
        return
    usage = workspace_disk_usage(workspace_id, user_id)
    if usage < quota:
        return
    raise HTTPException(status_code=409, detail=(
        f"该工作空间磁盘用量已达上限（已用 {format_bytes(usage)} / 配额 {format_bytes(quota)}），"
        "新建会话会被拒绝。请在「云空间 · 资产清单」页归档收藏重要产物或删除不再需要的会话文件后重试；"
        "确需更大配额请联系管理员在「系统管理 · 工作空间」页调整该用户的配额"))


def session_dir(base, key, workspace_id, user_id) -> Path:
    """会话目录（纯路径计算，不落盘、不校验布局）。"""
    if not re.fullmatch(r"[a-f0-9]{32}", key) or workspace_id < 0:
        raise ValueError("会话路径标识无效")
    return base / str(user_id) / "workspace" / str(workspace_id) / "sessions" / key


def session_paths(base, key, workspace_id, user_id, create=False, require_layout=True):
    root = session_dir(base, key, workspace_id, user_id)
    # 不接受持久卷内的符号链接替换。
    for p in [root, *root.parents]:
        if p == base:
            break
        if p.is_symlink():
            raise PermissionError("会话目录不能包含符号链接")
    if create:
        root.mkdir(parents=True, exist_ok=False, mode=0o700)
        for name in ("workspace", "runtime"):
            (root / name).mkdir(mode=0o700)
        (root / "runtime" / "tmp").mkdir(mode=0o700)
        if os.name == "posix" and os.geteuid() == 0:  # Unix 专属降权：Windows 无 geteuid，直接跳过
            os.chown(root / "workspace", 65534, 65534)
    if require_layout and not all((root / name).is_dir() and not (root / name).is_symlink() for name in ("workspace", "runtime")):
        raise RuntimeError("会话工作区不可用，禁止自动新建替代目录")
    return root


def locate_existing_session(base, row) -> Path:
    """续聊时定位既有会话目录；本地缺失先试云空间回落，仍不可用才给可操作提示。

    会话产物镜像在对象存储（见 session_cloud），因此“库里有历史会话、本地没有工作区”
    不再意味着产物丢失：先按云端副本重建本地目录并接管到当前存储节点，拉不回
    （存量未上云/云端也被清理）才明说原因——不把失败伪装成执行异常。
    """
    if not session_dir(base, row["session_key"], row["workspace_id"], row["user_id"]).is_dir():
        from backend.modules.mind.execution import session_cloud
        if not session_cloud.ensure_local_workspace(row):
            raise HTTPException(409, "该会话的工作区目录已不存在，云空间也没有可恢复的产物副本"
                                     "（存量数据未上云或已被清理），聊天记录仍可查看，请新建会话继续")
    return session_paths(base, row["session_key"], row["workspace_id"], row["user_id"])


def conversation_workspace_root(conversation_id: int, user_id: int) -> Path:
    """按 (会话, 属主) 定位会话工作区根目录 —— 读产物文件的**唯一**入口。

    身份只信服务端：session_key 按 ``conversation_id + user_id`` 取，取不到即无权访问
    （不接受请求体传会话/属主）。本地目录缺失先按云空间副本回落，换节点或
    卷被清理不等于产物丢了。

    文件伺服（``/chat/session-file``）、Excel 预览与报表归档共用这一份定位口径，
    不得各写一份“会话 → 目录”（一处漏了云回落就会把“文件其实在云上”显示成 404）。
    """
    row = execute_query(
        "SELECT session_key, workspace_id FROM adh_agent_sessions "
        "WHERE conversation_id=%s AND user_id=%s",
        (int(conversation_id or 0), int(user_id or 0)), fetchone=True)
    if not row or not row.get("session_key"):
        raise PermissionError("会话不存在或无权访问")
    row = {**row, "user_id": int(user_id)}
    if not session_cloud_ensure(row):
        raise FileNotFoundError(
            "该会话的工作区目录已不存在，云空间也没有可恢复的产物副本"
            "（存量数据未上云或已被清理）")
    return session_paths(workspace_base(create=False), row["session_key"],
                         int(row["workspace_id"] or 0), int(user_id), create=False)


def read_conversation_workspace_text(conversation_id: int, user_id: int, path: str,
                                     max_chars: int = 200_000) -> str:
    """读会话工作区里的一个文本产物（防穿越/符号链接逃逸与文件伺服同一守卫）。

    只给文本（报表正文用）；二进制产物仍走下载/预览入口。读不到一律外抛可诊断原因，
    不回空串把“没取到”伪装成“正文为空”。
    """
    from backend.modules.mind.multimodal.loader import normalize_rel_path, resolve_workspace_file

    root = conversation_workspace_root(conversation_id, user_id)
    ws_dir = (root / "workspace").resolve()
    if not normalize_rel_path(path):
        raise ValueError("产物路径无效，无法读取报表正文")
    try:
        target = resolve_workspace_file(ws_dir, path)
    except ValueError as exc:
        raise ValueError(f"产物路径不合法：{exc}") from None
    if not target.is_file():
        raise FileNotFoundError(f"会话工作区里找不到产物文件（{target.name}），可能已被清理")
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ValueError(f"产物文件读取失败（{target.name}）：{exc}") from None
    if not text.strip():
        raise ValueError(f"产物文件是空的（{target.name}），无法作为报表正文归档")
    return text[:max_chars]


def stale_resume_directories(root, sdk_session_id: str) -> list:
    """resume 转录里记录的会话目录是否已不存在（存量路径迁移未完成的直接表征）。

    执行层 CLI 续聊时会重放转录首条 workspace-directories 并对其中每个目录做存在性
    校验；目录已随布局迁移消失 → 子进程 exit 1，表现为一句看不出根因的 critical error。
    这里只做**只读检测**，不自动改写状态文件（改写属于工作空间目录迁移脚本的阶段②，
    由管理员显式执行），命中即交由调用方报错提醒（no-silent-degradation）。
    """
    if not sdk_session_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", sdk_session_id):
        return []
    missing = []
    runtime_dir = Path(root) / "runtime"
    if not runtime_dir.is_dir():
        return missing
    for projects in sorted(runtime_dir.glob("*/projects")):
        for transcript in projects.glob(f"*/{sdk_session_id}.jsonl"):
            try:
                with transcript.open(encoding="utf-8", errors="ignore") as fh:
                    for index, line in enumerate(fh):
                        if index >= 200:            # 头部记录之外不做全量扫描
                            break
                        try:
                            record = json.loads(line)
                        except ValueError:
                            continue
                        if not isinstance(record, dict) or record.get("type") != "workspace-directories":
                            continue
                        for directory in record.get("directories") or []:
                            if isinstance(directory, str) and directory and not os.path.isdir(directory) \
                                    and directory not in missing:
                                missing.append(directory)
                        break
            except OSError:
                continue
    return missing


def _lock_conversation_for_deletion(cur, conversation_id, user_id):
    # 与 claim_session 一致先锁会话再锁执行映射，避免删除与续聊并发。
    cur.execute("SELECT id,user_id,workspace_id FROM adh_conversations WHERE id=%s FOR UPDATE", (conversation_id,))
    conversation = cur.fetchone()
    if conversation and conversation["user_id"] != user_id:
        raise HTTPException(404, "会话不存在或无权访问")
    cur.execute("SELECT * FROM adh_agent_sessions WHERE conversation_id=%s FOR UPDATE", (conversation_id,))
    session = cur.fetchone()
    if session and session["user_id"] != user_id:
        raise HTTPException(404, "会话不存在或无权访问")
    if conversation and session and int(conversation.get("workspace_id") or 0) != session["workspace_id"] \
            and int(conversation.get("workspace_id") or 0) != 0 and int(session.get("workspace_id") or 0) != 0:
        # 遗留会话(ws=0，统一改造前的全局归属)跨入口执行时执行映射可能落在入口空间，属合法；其余不一致仍阻断。
        raise HTTPException(409, "会话工作区归属不一致，请联系管理员核对后重试")
    return session


def _proc_holds_session(key: str) -> list[int]:
    """扫描 /proc：cmdline 或 cwd 引用会话 key 的活进程 pid（孤儿 SDK 证据）。"""
    import os as _os
    hits: list[int] = []
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        pid = int(p.name)
        try:
            if key in (p / "cmdline").read_bytes().decode("utf-8", "ignore"):
                hits.append(pid)
                continue
            if key in _os.readlink(p / "cwd"):
                hits.append(pid)
        except OSError:
            continue
    return hits


def _remove_token_sandbox(token: str) -> None:
    """清理执行 token 标记的沙箱容器（清完即无沙箱残留）。"""
    import subprocess
    if not re.fullmatch(r"[a-f0-9]{32}", token or ""):
        raise RuntimeError("执行标识无效")
    listing = subprocess.run(["docker", "ps", "-aq", "--filter", f"label=adh.agent.execution={token}"],
                             capture_output=True, text=True, timeout=15, check=True)
    ids = listing.stdout.split()
    if any(not re.fullmatch(r"[a-f0-9]{12,64}", cid) for cid in ids):
        raise RuntimeError("容器标识无效")
    if ids:
        subprocess.run(["docker", "rm", "-f", *ids], capture_output=True, timeout=15, check=True)


def _dead_execution_evidence(session) -> bool:
    """执行权残留的否证口径：owner/sdk 均退出 + token 沙箱清完 + /proc 无会话进程。

    任一存活证据存在即 False（绝不误杀活执行）；无证据即可安全回收。
    """
    import logging as _logging
    if process_identity(session.get("owner_pid")) == session.get("owner_start"):
        return False                      # 宿主仍存活
    if session.get("sdk_pid") and process_identity(session["sdk_pid"]) == session.get("sdk_start"):
        return False                      # SDK 仍存活
    # token 为空 = 没有 token 标记的沙箱需要核验（不是"标记坏了"）：跳过清理继续判证据。
    # 历史缺陷：对空串调 _remove_token_sandbox 抛"执行标识无效"，把 running+token 已 NULL
    # 的僵尸形态变成 503（既回收不了也删不掉，还丢了可诊断的 409 文案）。
    # token 存在但形态不合法（无法据此核验容器）→ 无法证明沙箱已清 → 保持阻断并留痕，
    # 绝不"当中没有"继续回收（宁不误杀活执行）。
    token = session.get("execution_token") or ""
    if token:
        try:
            _remove_token_sandbox(token)
        except Exception as exc:  # noqa: BLE001 — 判据不可得即阻断，不外抛成 503
            _logging.getLogger(__name__).error(
                "执行标识无法核验沙箱残留，保持阻断: session=%s err=%s",
                session.get("session_key"), exc)
            return False
    held = _proc_holds_session(session["session_key"])
    if held:
        _logging.getLogger(__name__).error(
            "会话仍有存活进程，保持阻断: %s pids=%s", session["session_key"], held)
        return False
    return True


def _reclaim_dead_execution(session, write=None) -> bool:
    """回收 running 执行权残留 → interrupted 并释放 token（证据口径，见上）。

    write(sql, params)->rowcount 可注入（删除路径传当前事务游标，避免跨连接死锁）。
    """
    import logging as _logging
    write = write or execute_write
    token = session.get("execution_token") or ""
    if not token and session.get("status") != "running":
        return False
    if not _dead_execution_evidence(session):
        return False
    released = write(
        "UPDATE adh_agent_sessions SET status='interrupted',execution_token=NULL,"
        "updated_at=UTC_TIMESTAMP(6) WHERE session_key=%s AND status='running' "
        "AND (execution_token <=> %s)",
        (session["session_key"], token))
    if released:
        _logging.getLogger(__name__).warning(
            "执行权残留已回收(owner/sdk 均已退出且无沙箱/进程证据): %s", session["session_key"])
    return bool(released)


def _session_cleanup_root(session):
    """定位待清理目录；返回 None 表示目录已不存在（工作区为空），无需清理。

    仅当目录确实存在时才核验存储节点归属；会话目录或存储卷目录缺失均不阻断
    删除（需求确认语义：目录已不存在即无残留妨碍，删除记录无风险）；
    仍绝不创建存储卷或标记文件。
    """
    if session["status"] not in ("idle", "interrupted", "closed", "deleting") or session.get("execution_token"):
        raise HTTPException(409, "会话仍在执行或尚未确认停止，请停止后再删除")
    from backend.common.config import ADH_WORKSPACES_DIR
    key = session["session_key"]
    expected = f"{session['user_id']}/workspace/{session['workspace_id']}/sessions/{key}"
    if not re.fullmatch(r"[a-f0-9]{32}", key or "") or session["relative_dir"] != expected:
        raise HTTPException(409, "会话目录映射不一致，未执行清理，请联系管理员")
    base = Path(ADH_WORKSPACES_DIR).resolve()
    if not base.is_dir():
        # 存储卷目录整体不存在（挂载点丢失/卷已清）：会话文件必然不存在，
        # 无残留可清理，放行删除记录（不阻断、不创建存储卷）。
        return None
    root = base / expected
    import os
    if not os.path.lexists(root):
        return None  # 工作区已空：无文件需清理，允许删除记录
    if root.is_symlink():
        raise HTTPException(409, "会话目录被符号链接替换，未执行清理，请联系管理员")
    if storage_node(base, create=False) != session["storage_node"]:
        raise HTTPException(409, "当前实例无法访问该会话的存储节点，请在对应节点重试删除")
    return root


def _remove_session_directory(root):
    import shutil
    # Linux 的 fd 版 rmtree 不跟随目录内的符号链接；不支持时明确拒绝。
    if not shutil.rmtree.avoids_symlink_attacks:
        raise RuntimeError("当前运行环境不支持安全的会话目录清理")
    try:
        root.lstat()
    except FileNotFoundError:
        return  # 重试时整目录已不存在，视为该目录已清理。
    shutil.rmtree(root)


def _purge_render_documents(cur, conversation_id):
    """随会话清除交付文档对象（整份报告/图表数据）。

    只删会话行会把正文留成孤儿：它既能按 doc_id 被任何持有者取回（归属按属主
    判定），又不在会话存储的容量闸门里 —— 不级联清就是隐私与容量双重缺口。
    走当前事务游标，不跨连接对同行加锁（与会话删除同一原子单元）。
    """
    from backend.modules.viz.services import report_document_service as docs
    docs.purge_conversation_documents(int(conversation_id or 0), cur=cur)


def delete_conversation_workspace(conversation_id, user_id):
    """两阶段删除：持久化 deleting → 清理精确会话目录 → 删除映射与会话。

    清理失败保留会话和 deleting 状态，用户可重试；换实例/进程重启也不会
    恢复已经部分删除的目录。两阶段均用数据库行锁，不以进程内标志防重。

    拆行消息与交付文档对象与会话同事务删除（只删会话行会把历史正文留成
    没人能定位的孤儿）。
    """
    if not user_id or int(user_id) <= 0:
        raise HTTPException(401, "缺少可信用户身份")
    from backend.core import conversation_store
    with DBConnection() as conn:
        conn.begin()
        with conn.cursor() as cur:
            session = _lock_conversation_for_deletion(cur, conversation_id, user_id)
            # 自愈触发条件按**僵尸形态**判（running 或仍持 token），不按 token 有无判：
            # 历史缺陷是只认 `execution_token` 非空 —— 出现 status='running' 而 token 已 NULL
            # 的半截状态（finish 与状态写回之间被取消/重启）时，外层闸门直接跳过自愈，
            # 而 `_session_cleanup_root` 又对 running 一律 409 → 空会话永远删不掉
            # （会话 1783069459150 卡死的直接原因；token 为 NULL 时 _reclaim_dead_execution
            # 本身是支持回收的，它只在「非 running 且无 token」时才拒绝）。
            if session is not None and (session.get("execution_token") or session.get("status") == "running"):
                # 执行权残留自愈（证据口径）：owner/sdk 均退出且无沙箱/进程证据时
                # 回收 running 残留，删除不再被僵尸状态永久卡死；有存活证据仍走 409。
                # write 走当前事务游标，避免跨连接对同行加锁死锁。
                def _tx_write(sql, params):
                    cur.execute(sql, params)
                    return cur.rowcount
                if _reclaim_dead_execution(session, write=_tx_write):
                    session = {**session, "status": "interrupted", "execution_token": None}
            if session is None:
                # 非 Agent 会话无独立目录；绝不根据会话 ID 猜目录或删共享空间。
                cur.execute("DELETE FROM adh_conversations WHERE id=%s AND user_id=%s", (conversation_id, user_id))
                conversation_store.delete_by_conversation(cur, conversation_id)
                _purge_render_documents(cur, conversation_id)
                return {"success": True}
            root = _session_cleanup_root(session)
            if root is None:
                # 工作区已空：无文件需清理，同一事务内原子删除映射与会话。
                cur.execute("DELETE FROM adh_agent_sessions WHERE session_key=%s AND user_id=%s", (session["session_key"], user_id))
                cur.execute("DELETE FROM adh_conversations WHERE id=%s AND user_id=%s", (conversation_id, user_id))
                conversation_store.delete_by_conversation(cur, conversation_id)
                _purge_render_documents(cur, conversation_id)
                return {"success": True}
            if session["status"] != "deleting":
                cur.execute("UPDATE adh_agent_sessions SET status='deleting',version=version+1,updated_at=UTC_TIMESTAMP(6) "
                            "WHERE session_key=%s AND status=%s", (session["session_key"], session["status"]))
                if cur.rowcount != 1:
                    raise HTTPException(409, "会话状态已变化，请重试删除")

    # deleting 已提交；清理过程中的异常/崩溃不能把会话恢复成 idle。
    with DBConnection() as conn:
        conn.begin()
        with conn.cursor() as cur:
            session = _lock_conversation_for_deletion(cur, conversation_id, user_id)
            if session is not None:
                if session["status"] != "deleting":
                    raise HTTPException(409, "会话状态已变化，请重试删除")
                root = _session_cleanup_root(session)
                if root is not None:
                    _remove_session_directory(root)
                # 云空间副本随会话一并清除（未配置对象存储时为 0，不报错）：
                # 只删库行会留下没人能定位的孤儿产物，宁阻断让用户重试删除。
                from backend.modules.mind.execution import session_cloud
                try:
                    session_cloud.purge_session_cloud(int(session["user_id"]), session["session_key"])
                except Exception as exc:  # noqa: BLE001
                    raise HTTPException(409, f"云空间产物未能清除（{type(exc).__name__}），"
                                             "会话未删除，请重试") from exc
                cur.execute("DELETE FROM adh_agent_sessions WHERE session_key=%s AND user_id=%s AND status='deleting'",
                            (session["session_key"], user_id))
                if cur.rowcount != 1:
                    raise HTTPException(409, "目录已清理，会话映射尚未删除，请重试")
            cur.execute("DELETE FROM adh_conversations WHERE id=%s AND user_id=%s", (conversation_id, user_id))
            conversation_store.delete_by_conversation(cur, conversation_id)
            _purge_render_documents(cur, conversation_id)
    return {"success": True}


def execution_status(conversation_id: int, user_id: int) -> dict:
    """会话执行权的**只读**状态（供前端「正在停止…」轮询）。

    背景：取消只是前端断开 SSE，后端靠断连被动收尾（`client.disconnect` + 等在跑的
    工具任务 + 核验子进程已停）才把 running 释放为 interrupted；这段窗口里重发必 409，
    用户体感就是「停止没生效」。给一个可轮询的真相，而不是让用户猜。

    只回状态机语义，不回 session_key/execution_token/pid 等内部标识。
    """
    row = execute_query(
        "SELECT status FROM adh_agent_sessions WHERE conversation_id=%s AND user_id=%s",
        (int(conversation_id or 0), int(user_id or 0)), fetchone=True)
    status = row["status"] if row else "none"
    return {
        "status": status,
        # busy: 仍持有执行权（仍在执行，或已断连但正在停止核验中）
        "busy": status == "running",
        # can_send: 不拦下一条消息（与 preflight_request 的口径一致）
        "can_send": status not in ("running", "deleting", "closed"),
    }


def preflight_request(req, user):
    """入口预检（工作空间授权 + 会话执行权 + 生效策略）。

    返回解析出的 AS-BOT 生效策略（非 agent 模式返回 None）：调用方把它透传给
    前置意图漏斗，避开同一请求内重复解析权限/工具策略（不是新增第二套裁决，
    只把已有的一次结果传下去）。
    """
    from backend.common.auth import authorize_workspace
    from backend.modules.mind.execution.models import ExecutionContext
    from backend.modules.mind.execution.tool_policy import resolve_policy
    # workspace_id 未指定(0)时入口不做工作空间授权，归属最终由会话自身工作空间归一
    # (validate_conversation)；历史 ws=0 会话为统一改造前的遗留全局归属，仅兼容保留。
    workspace_id = req.workspace_id or 0
    if workspace_id:
        authorize_workspace(user, workspace_id)
    agent_mode = getattr(req, "pipeline_mode", "agent") == "agent" or bool(req.attachments)
    ctx = ExecutionContext(user_id=user["user_id"], user_role=user.get("role", ""),
                           workspace_id=req.workspace_id or 0,
                           extra={"as_bot_key": getattr(req, "as_bot_key", "") or ""})
    cid = getattr(req, "conversation_id", 0) or 0
    if cid:
        validate_conversation(ctx, cid)
    if not agent_mode:
        if ctx.extra.get("as_bot_key"):
            raise HTTPException(422, "AS-BOT 会话不能切换到旧执行管线，请新建会话")
        return None
    if cid:
        row = execute_query("SELECT status FROM adh_agent_sessions WHERE conversation_id=%s", (cid,), fetchone=True)
        if row and row["status"] == "deleting":
            raise HTTPException(409, "该会话正在删除或等待清理重试，不能继续执行")
        if row and row["status"] == "closed":
            raise HTTPException(409, "该会话已清空，请新建会话")
        if row and row["status"] == "running":
            raise HTTPException(
                409,
                "上一轮仍在执行或正在停止（断开连接后服务端需先确认工具任务与 SDK 子进程已收尾），"
                "请稍候几秒再发送；若长时间未恢复，请联系管理员查看会话状态")
        # interrupted（上轮执行中断）不拦：执行权已释放（execution_token=NULL），
        # 新消息是新一轮请求、不重放中断轮副作用，交由 claim 认领后从干净上下文继续。
    try:
        # 轮次起点：角色 id/AS-BOT 配置/角色权限码走配置缓存的**轮次档**（写时失效）；
        # 后续工具调用前的裁决（check_tool）不吃这一档，只读 live 短 TTL 档，
        # 且 digest 不一致即显式要求重发（撤权不会静默放行）。
        return resolve_policy(ctx, use_cache=True)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _preflight_inputs(policy, ctx):
    """取 preflight 已解析策略携带的 DB 事实（仅同一请求/同一用户+空间时有效）。

    命中后 claim_session 用当前 ceiling 重新编译即可，不再重读 owner/AS-BOT/角色权限码
    三组查询（远端 MySQL 下是首字延迟的固定开销之一）。窗口只限同一 HTTP 请求内；
    工具调用前的裁决（check_tool）不走此路径（不传 inputs），仍按当次生效配置比 digest。
    """
    inputs = dict(getattr(policy, "resolved_inputs", None) or {}) if policy is not None else {}
    owner = inputs.get("owner") or {}
    as_bot = inputs.get("as_bot") or {}
    if not as_bot or int(owner.get("user_id") or 0) != int(ctx.user_id or 0):
        return None
    # 跨入口空间不复用：preflight 按入口空间解析，claim 按会话归属空间；
    # 两者不一致时 AS-BOT 候选集可能不同，退回全量解析（宁多查一次，不拿错空间策略）。
    if int(owner.get("workspace_id") or 0) not in (int(ctx.workspace_id or 0), 0):
        return None
    if (ctx.extra or {}).get("as_bot_key") and as_bot.get("as_bot_key") != ctx.extra["as_bot_key"]:
        return None
    return inputs


def validate_conversation(ctx, conversation_id):
    from backend.common.auth import authorize_workspace
    row = execute_query("SELECT * FROM adh_conversations WHERE id=%s AND user_id=%s",
                        (conversation_id, ctx.user_id), fetchone=True)
    if not row:
        raise HTTPException(404, "会话不存在或无权访问")
    conv_ws = int(row.get("workspace_id") or 0)
    # 会话归属的工作空间是固有属性：看得到就用得了（列表互见后语义一致）。
    # 授权按会话归属校验；遗留会话(ws=0，统一改造前的全局归属)按 user_id 隔离，入口空间照常校验。
    if conv_ws:
        authorize_workspace({"user_id": ctx.user_id, "role": ctx.user_role}, conv_ws)
    elif ctx.workspace_id:
        authorize_workspace({"user_id": ctx.user_id, "role": ctx.user_role}, ctx.workspace_id)
    # 执行归属跟随会话自身的工作空间，跨入口打开同一会话执行语义一致。
    # （conv_ws=0 是遗留会话的合法归属，不能用 or 回退到入口空间）
    ctx.workspace_id = conv_ws
    chosen = ctx.extra.get("as_bot_key")
    if row.get("as_bot_key") and chosen and row["as_bot_key"] != chosen:
        raise HTTPException(409, "切换 AS-BOT 需要新建会话")
    if row.get("as_bot_key"):
        ctx.extra["as_bot_key"] = row["as_bot_key"]
    return row



# 后端名会进 JSON path（`$.<backend>`），只允许安全字符；异常值一律不记账（不猜）。
_BACKEND_NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")


def normalize_backend_sessions(value) -> dict:
    """把 `backend_sessions` 归一为 `{backend: sdk_session_id}`。

    pymysql 读 JSON 列返回的是**字符串**（不归一就按字符展开，图谱侧同类历史缺陷），
    旧环境列缺失时是 None。损坏内容记日志后按"无登记"处理 —— 后果只是接不回原转录、
    由新转录重建，不会把脏数据当指针下发。
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            logging.getLogger(__name__).warning("backend_sessions 内容不是合法 JSON，按无登记处理: %r",
                                                value[:120])
            return {}
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items()
            if k and isinstance(v, str) and v}


def _rebind_backend(cur, row, backend):
    """执行后端（Harness）换了时的重绑：返回 (更新后的行, 切换事实；没换则 {})。

    为什么不再 403：后端不是身份。CLI 转录按后端各存一份指针（`backend_sessions`），
    换后端只是"这一侧没有转录可续"；问答文本在 `adh_conversation_messages`、产物文件在
    `workspace/`，都与后端无关，所以会话继续可用。新后端查不到指针 → `sdk_session_id` 置空
    → 本轮不带 resume，由 secure_sdk 把问答记录拼进提示词重建上下文（include_history）；
    切回原后端时它自己的指针还在 map 里，转录接得回来 —— 这正是旧单槽位写法被跨后端混写
    抹掉后永远接不回的那个损失。

    切换不向用户弹提示（产品决策），可诊断性由下面的 warning 日志 + 本回合 trace span
    （secure_sdk._obs_rebind_span）+ 执行映射落库承担。
    """
    previous = str(row.get("backend") or "")
    if previous == backend:
        return row, {}
    sid = normalize_backend_sessions(row.get("backend_sessions")).get(backend, "")
    # 事务内已持该行锁，登记必须走同一游标（跨连接写自己锁着的行会死锁）。
    cur.execute("UPDATE adh_agent_sessions SET backend=%s, sdk_session_id=%s, version=version+1,"
                "updated_at=UTC_TIMESTAMP(6) WHERE session_key=%s",
                (backend, sid or None, row["session_key"]))
    logging.getLogger(__name__).warning(
        "会话 %s 执行后端切换: %s → %s（问答记录与工作区产物保留；CLI 转录%s）",
        row["session_key"], previous, backend,
        "沿用该后端既有转录" if sid else "无既有转录，本轮按问答记录重建")
    return ({**row, "backend": backend, "sdk_session_id": sid},
            {"from": previous, "to": backend, "resumed": bool(sid)})


@dataclass
class SessionWorkspace:
    key: str
    token: str
    root: Path
    policy: object
    ceiling: object
    sdk_session_id: str = ""
    closed: bool = False
    unsafe: bool = False
    closing: bool = False
    layer_id: int = 0
    layer_bound: bool = False
    backend: str = ""
    # 本轮发生了后端切换时的原后端名（空=没换）。切换事实要随 done.warnings 下发给用户。
    rebound_from: str = ""
    sdk_pid: int = 0
    sdk_start: str = ""
    sandbox_used: bool = False
    sandbox_backend: str = ""
    sandbox_pids: set = field(default_factory=set, repr=False)
    tool_tasks: set = field(default_factory=set, repr=False)

    @property
    def workspace(self):
        return self.root / "workspace"

    @property
    def runtime_dir(self):
        return self.root / "runtime"

    def verify(self):
        if self.closed or self.closing or self.unsafe or not execute_query(
            "SELECT session_key FROM adh_agent_sessions WHERE session_key=%s AND execution_token=%s AND status='running'",
            (self.key, self.token), fetchone=True,
        ):
            raise PermissionError("执行会话已失效")

    def record_client(self, client):
        process = getattr(getattr(client, "_transport", None), "_process", None)
        pid = getattr(process, "pid", None)
        start = process_identity(pid)
        if not start:
            self.unsafe = True
            raise RuntimeError("SDK 未提供可核验的进程标识，禁止执行")
        self.sdk_pid, self.sdk_start = pid, start
        if execute_write("UPDATE adh_agent_sessions SET sdk_pid=%s,sdk_start=%s WHERE session_key=%s "
                         "AND execution_token=%s AND status='running'", (pid, start, self.key, self.token)) != 1:
            self.unsafe = True
            raise RuntimeError("SDK 进程登记失败")

    def confirm_stopped(self):
        if self.sdk_pid and process_identity(self.sdk_pid) == self.sdk_start:
            self.unsafe = True
            raise RuntimeError("SDK 进程仍然存活，不能释放执行权")
        if self.sandbox_used:
            if self.sandbox_backend == "bwrap":
                # bwrap 是本进程直接子进程；核验登记的 pid 是否仍在（process_identity 空=已退/僵尸）。
                live = [pid for pid in self.sandbox_pids if process_identity(pid)]
                if live:
                    self.unsafe = True
                    raise RuntimeError("会话工具进程仍然存在，不能释放执行权")
            else:
                import subprocess
                listing = subprocess.run(
                    ["docker", "ps", "-aq", "--filter", f"label=adh.agent.execution={self.token}"],
                    capture_output=True, text=True, timeout=15, check=True,
                )
                if listing.stdout.strip():
                    self.unsafe = True
                    raise RuntimeError("会话工具容器仍然存在，不能释放执行权")

    def finish(self, sdk_session_id="", success=False, initialized_only=False):
        if self.unsafe:
            raise RuntimeError("沙箱尚未确认停止，不能释放会话执行权")
        if sdk_session_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", sdk_session_id):
            raise ValueError("SDK 会话标识无效")
        # 仅在调用方确认所有执行已停止后释放；不能在异常中伪装成功。
        status = "idle" if success or initialized_only else "interrupted"
        sql = ("UPDATE adh_agent_sessions SET status=%s, "
               "sdk_session_id=COALESCE(NULLIF(%s,''),sdk_session_id), "
               "execution_token=NULL, updated_at=UTC_TIMESTAMP(6) "
               "WHERE session_key=%s AND execution_token=%s AND status='running'")
        args = [status, sdk_session_id, self.key, self.token]
        if sdk_session_id and _BACKEND_NAME_RE.fullmatch(self.backend or ""):
            # 同时写 map：单槽位被跨后端混写会永久丢掉原后端的接回能力（见 _rebind_backend）
            sql = ("UPDATE adh_agent_sessions SET status=%s, "
                   "sdk_session_id=COALESCE(NULLIF(%s,''),sdk_session_id), "
                   "backend_sessions=JSON_SET(COALESCE(backend_sessions, CAST('{}' AS JSON)), %s, %s), "
                   "execution_token=NULL, updated_at=UTC_TIMESTAMP(6) "
                   "WHERE session_key=%s AND execution_token=%s AND status='running'")
            args = [status, sdk_session_id, f"$.{self.backend}", sdk_session_id,
                    self.key, self.token]
        changed = execute_write(sql, tuple(args))
        self.closed = True
        if changed != 1:
            raise RuntimeError("会话执行状态更新冲突")


def claim_session(task, backend, config):
    from backend.modules.mind.execution.tool_policy import resolve_policy
    ctx = task.context
    if not ctx or ctx.user_id <= 0:
        raise PermissionError("缺少可信执行身份")
    if config.get("cwd") or config.get("allowed_dirs"):
        raise ValueError("AS-BOT 仅允许独立会话目录，请移除 Harness cwd/allowed_dirs 宽目录配置")
    cid = int(ctx.extra.get("conversation_id") or 0)
    if cid:
        validate_conversation(ctx, cid)
    # 性能：远端 MySQL(跨公网 RTT)下 resolve_policy 内部 3 组 SELECT 是 claim_session
    # 首字延迟的主要贡献之一。历史实现在 layer_id 存在时会先跑一次不带 ceiling 的
    # resolve_policy、再跑一次带 live_ceiling 的，两次结果只有 ceiling 收敛差异，
    # 前一次纯属浪费(_layer_id 由 manager.py 恒注入 → 每轮都跑两次)。
    # live_ceiling 只读 runtime.layer_id/backend，不依赖 ctx 归一化，可先算 ceiling
    # 再一次性 resolve_policy。执行层全局生效(不按工作空间绑定); layer_bound 字段
    # 保留兼容签名(下游无消费方)。
    ceiling = config.get("allowed_tools")
    layer_id = int(config.get("_layer_id") or 0)
    layer_bound = False
    if layer_id:
        from backend.modules.mind.execution.tool_policy import live_ceiling
        from types import SimpleNamespace
        ceiling = live_ceiling(ctx, SimpleNamespace(layer_id=layer_id, layer_bound=True, backend=backend))
    # 性能：同一请求内 preflight 已读过 owner/AS-BOT/角色权限码（经 ExecutionContext
    # 的 _chat_policy 透传），这里用当前 ceiling 重新编译即可，省去重复往返
    # （claim_session 实测 1~3s 的主要构成）。无预取事实（内部调用/旧入口）仍全量
    # 解析；工具级裁决（check_tool）不传 inputs，只用 live 档配置 + 实读的 verify/ceiling。
    inputs = _preflight_inputs(task.context.extra.get("_chat_policy"), ctx)
    policy = resolve_policy(ctx, ceiling, inputs=inputs, use_cache=True)
    ctx.extra["as_bot_key"] = policy.as_bot["as_bot_key"]
    base = workspace_base()
    node = storage_node(base)
    token = uuid.uuid4().hex
    with DBConnection() as conn:
        conn.begin()
        with conn.cursor() as cur:
            if cid:
                cur.execute("SELECT * FROM adh_conversations WHERE id=%s AND user_id=%s FOR UPDATE", (cid, ctx.user_id))
                conv = cur.fetchone()
                if not conv or int(conv.get("workspace_id") or 0) != ctx.workspace_id:
                    raise HTTPException(404, "会话不存在或无权访问")
                if conv.get("as_bot_key") and conv["as_bot_key"] != policy.as_bot["as_bot_key"]:
                    raise HTTPException(409, "切换 AS-BOT 需要新建会话")
                cur.execute("SELECT * FROM adh_agent_sessions WHERE conversation_id=%s FOR UPDATE", (cid,))
                row = cur.fetchone()
            else:
                row = None
            if row:
                # 执行映射跟随会话归属（validate_conversation 已把 ctx.workspace_id 归一到会话空间）；
                # 存量行的 workspace_id 可能是历史入口空间，身份校验只看属主+形态，目录用行自身空间。
                if (row["user_id"], row["as_bot_key"]) != (ctx.user_id, policy.as_bot["as_bot_key"]):
                    # 身份不匹配（换属主/换 AS-BOT 形态）不是可重绑项，仍硬拒
                    raise HTTPException(403, "执行会话身份不匹配，请新建会话")
                row, rebind = _rebind_backend(cur, row, backend)
                if rebind:
                    ctx.extra["_harness_rebound"] = rebind
                if row["status"] == "deleting":
                    raise HTTPException(409, "该会话正在删除或等待清理重试，不能继续执行")
                if row["status"] == "closed":
                    raise HTTPException(409, "该会话已清空，请新建会话")
                if row["status"] == "running":
                    raise HTTPException(409, "该会话正在执行或上次执行尚未确认停止")
                # 云空间自愈：目录不在本地 → 先按云端副本重建（claim 在事务内持有该行锁，
                # 登记 UPDATE 必须走同一游标，跨连接写自己已锁的行会死锁）。
                # 目录在本地但存储节点不符 → 保持阻断（卷身份与登记不一致，无法证明文件是这套）。
                if not session_dir(base, row["session_key"], row["workspace_id"], row["user_id"]).is_dir():
                    def _adopt(sql, params):
                        cur.execute(sql, params)
                        return cur.rowcount
                    if not session_cloud_ensure(row, write=_adopt):
                        raise HTTPException(409, "该会话的工作区目录已不存在，云空间也没有可恢复的产物副本"
                                                 "（存量数据未上云或已被清理），聊天记录仍可查看，请新建会话继续")
                    row = {**row, "storage_node": node, "sdk_session_id": ""}
                elif row["storage_node"] != node:
                    raise HTTPException(409, "会话工作区不在当前存储节点，无法恢复")
                root = locate_existing_session(base, row)
                if row["status"] == "interrupted":
                    # 上轮执行中断（执行权已释放）。新消息是新一轮请求、不重放中断轮副作用；
                    # 丢弃中断的 SDK 会话（不 resume），复位为可认领状态后从干净执行上下文继续，
                    # 不必逼用户新建会话（历史对话保留）。
                    cur.execute("UPDATE adh_agent_sessions SET status='idle', sdk_session_id='', "
                                "version=version+1, updated_at=UTC_TIMESTAMP(6) "
                                "WHERE session_key=%s AND status='interrupted'",
                                (row["session_key"],))
                    row = {**row, "status": "idle", "sdk_session_id": ""}
                if row.get("sdk_session_id"):
                    # 续聊前显式校验 resume 状态里的目录：迁移未修完时报可诊断错误，
                    # 而不是让 CLI 在 initialize 阶段崩成“unknown critical error”（护栏：失败即暴露）。
                    stale = stale_resume_directories(root, row["sdk_session_id"])
                    if stale:
                        logging.getLogger(__name__).error(
                            "会话 %s 的 SDK 恢复状态指向已不存在的目录: %s", row["session_key"], stale)
                        raise HTTPException(
                            409, "该会话的 SDK 恢复状态仍指向迁移前的旧存储目录，继续对话会执行失败；"
                                 "请联系管理员重跑工作空间目录迁移（含会话内部 CLI 状态的路径改写）后重试")
            else:
                if ctx.extra.get("session_id"):
                    raise HTTPException(409, "旧 SDK 会话不能直接接管，请新建会话")
                if cid and conv.get("executor_session_id"):
                    raise HTTPException(409, "旧执行会话需重新建立安全工作区，请新建会话；聊天记录仍保留")
                # 磁盘配额拦截: 超限不建新会话目录, 引导归档/清理
                assert_workspace_disk_quota(ctx.workspace_id, ctx.user_id)
                key = uuid.uuid4().hex
                root = session_paths(base, key, ctx.workspace_id, ctx.user_id, create=True)
                row = {"session_key": key, "sdk_session_id": ""}
                cur.execute(
                    "INSERT INTO adh_agent_sessions (session_key,conversation_id,user_id,workspace_id,as_bot_key,backend,"
                    "storage_node,relative_dir,policy_hash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (key, cid or None, ctx.user_id, ctx.workspace_id, policy.as_bot["as_bot_key"], backend,
                     node, str(root.relative_to(base)), policy.digest),
                )
            supplied = ctx.extra.get("session_id")
            if supplied and supplied != (row.get("sdk_session_id") or ""):
                raise HTTPException(403, "客户端 SDK 会话标识不匹配")
            cur.execute("UPDATE adh_agent_sessions SET status='running',execution_token=%s,version=version+1,"
                        "policy_hash=%s,owner_pid=%s,owner_start=%s,sdk_pid=NULL,sdk_start=NULL,"
                        "updated_at=UTC_TIMESTAMP(6) WHERE session_key=%s AND status='idle'",
                        (token, policy.digest, os.getpid(), process_identity(os.getpid()), row["session_key"]))
            if cur.rowcount != 1:
                raise HTTPException(409, "会话执行状态冲突")
            if cid:
                cur.execute("UPDATE adh_conversations SET as_bot_key=%s WHERE id=%s", (policy.as_bot["as_bot_key"], cid))
    runtime = SessionWorkspace(row["session_key"], token, root, policy, ceiling, row.get("sdk_session_id") or "",
                               layer_id=layer_id, layer_bound=layer_bound, backend=backend,
                               rebound_from=str((ctx.extra.get("_harness_rebound") or {}).get("from") or ""))
    ctx.extra["secure_runtime"] = runtime
    ctx.extra.pop("session_id", None)
    return runtime


def reconcile_stale_sessions():
    """只处理当前持久卷上宿主已退出的任务；无法证实停止则保持阻断。"""
    import signal
    import subprocess
    import time
    import logging
    node = storage_node(workspace_base())
    rows = execute_query("SELECT * FROM adh_agent_sessions WHERE storage_node=%s AND status='running'", (node,))
    repaired = 0
    for row in rows:
        if process_identity(row.get("owner_pid")) == row.get("owner_start"):
            continue
        if not row.get("sdk_pid") or not row.get("sdk_start"):
            # 未登记 SDK 进程：不再无条件永久阻断——按证据口径回收
            # （owner/sdk 均退出 + token 沙箱清完 + /proc 无会话进程）；
            # 有存活证据才保持阻断。
            if _reclaim_dead_execution(row):
                repaired += 1
                continue
            logging.getLogger(__name__).error("会话未记录 SDK 进程且存在存活证据，保持阻断: %s", row["session_key"])
            continue
        try:
            pid = row["sdk_pid"]
            if process_identity(pid) == row["sdk_start"]:
                os.kill(pid, signal.SIGTERM)
                for _ in range(20):
                    if process_identity(pid) != row["sdk_start"]:
                        break
                    time.sleep(0.1)
                if process_identity(pid) == row["sdk_start"]:
                    os.kill(pid, signal.SIGKILL)
                    time.sleep(0.1)
            if process_identity(pid) == row["sdk_start"]:
                raise RuntimeError("SDK 子进程尚未停止")
            token = row["execution_token"]
            if not re.fullmatch(r"[a-f0-9]{32}", token or ""):
                raise RuntimeError("执行标识无效")
            listing = subprocess.run(["docker", "ps", "-aq", "--filter", f"label=adh.agent.execution={token}"],
                                     capture_output=True, text=True, timeout=15, check=True)
            ids = listing.stdout.split()
            if any(not re.fullmatch(r"[a-f0-9]{12,64}", cid) for cid in ids):
                raise RuntimeError("容器标识无效")
            if ids:
                subprocess.run(["docker", "rm", "-f", *ids], capture_output=True, timeout=15, check=True)
            repaired += execute_write("UPDATE adh_agent_sessions SET status='interrupted',execution_token=NULL,"
                                      "updated_at=UTC_TIMESTAMP(6) WHERE session_key=%s AND execution_token=%s AND status='running'",
                                      (row["session_key"], token))
        except Exception:
            logging.getLogger(__name__).exception("无法确认旧会话执行已停止，保持阻断")
    return repaired
