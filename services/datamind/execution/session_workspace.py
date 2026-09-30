"""本地持久卷上的独立会话；MySQL 是身份、resume 与执行互斥的唯一真值源。"""
import json
import os
from pathlib import Path
import re
import uuid
from dataclasses import dataclass, field

from fastapi import HTTPException
from services.shared.common.db import DBConnection, execute_query, execute_write


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
    from services.shared.common.config import ADH_WORKSPACES_DIR
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


def session_paths(base, key, workspace_id, create=False, require_layout=True):
    if not re.fullmatch(r"[a-f0-9]{32}", key) or workspace_id < 0:
        raise ValueError("会话路径标识无效")
    root = base / f"ws_{workspace_id}" / "sessions" / key
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
        if os.geteuid() == 0:
            os.chown(root / "workspace", 65534, 65534)
    if require_layout and not all((root / name).is_dir() and not (root / name).is_symlink() for name in ("workspace", "runtime")):
        raise RuntimeError("会话工作区不可用，禁止自动新建替代目录")
    return root


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
    if conversation and session and int(conversation.get("workspace_id") or 0) != session["workspace_id"]:
        raise HTTPException(409, "会话工作区归属不一致，请联系管理员核对后重试")
    return session


def _session_cleanup_root(session):
    """定位待清理目录；返回 None 表示目录已不存在（工作区为空），无需清理。

    仅当目录确实存在时才核验存储节点归属；目录缺失时不阻断删除，
    但绝不创建存储卷或标记文件（避免挂载丢失被误判为已清理）。
    """
    if session["status"] not in ("idle", "interrupted", "closed", "deleting") or session.get("execution_token"):
        raise HTTPException(409, "会话仍在执行或尚未确认停止，请停止后再删除")
    from services.shared.common.config import ADH_WORKSPACES_DIR
    key = session["session_key"]
    expected = f"ws_{session['workspace_id']}/sessions/{key}"
    if not re.fullmatch(r"[a-f0-9]{32}", key or "") or session["relative_dir"] != expected:
        raise HTTPException(409, "会话目录映射不一致，未执行清理，请联系管理员")
    base = Path(ADH_WORKSPACES_DIR).resolve()
    if not base.is_dir():
        raise HTTPException(503, "会话存储卷未挂载，无法确认目录已清理，请在对应节点重试删除")
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


def delete_conversation_workspace(conversation_id, user_id):
    """两阶段删除：持久化 deleting → 清理精确会话目录 → 删除映射与会话。

    清理失败保留会话和 deleting 状态，用户可重试；换实例/进程重启也不会
    恢复已经部分删除的目录。两阶段均用数据库行锁，不以进程内标志防重。
    """
    if not user_id or int(user_id) <= 0:
        raise HTTPException(401, "缺少可信用户身份")
    with DBConnection() as conn:
        conn.begin()
        with conn.cursor() as cur:
            session = _lock_conversation_for_deletion(cur, conversation_id, user_id)
            if session is None:
                # 非 Agent 会话无独立目录；绝不根据会话 ID 猜目录或删共享空间。
                cur.execute("DELETE FROM adh_conversations WHERE id=%s AND user_id=%s", (conversation_id, user_id))
                return {"success": True}
            root = _session_cleanup_root(session)
            if root is None:
                # 工作区已空：无文件需清理，同一事务内原子删除映射与会话。
                cur.execute("DELETE FROM adh_agent_sessions WHERE session_key=%s AND user_id=%s", (session["session_key"], user_id))
                cur.execute("DELETE FROM adh_conversations WHERE id=%s AND user_id=%s", (conversation_id, user_id))
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
                cur.execute("DELETE FROM adh_agent_sessions WHERE session_key=%s AND user_id=%s AND status='deleting'",
                            (session["session_key"], user_id))
                if cur.rowcount != 1:
                    raise HTTPException(409, "目录已清理，会话映射尚未删除，请重试")
            cur.execute("DELETE FROM adh_conversations WHERE id=%s AND user_id=%s", (conversation_id, user_id))
    return {"success": True}


def preflight_request(req, user):
    from services.shared.common.auth import authorize_workspace
    from services.datamind.execution.models import ExecutionContext
    from services.datamind.execution.tool_policy import resolve_policy
    # workspace_id=0 为全局助手会话，仅按 user_id 隔离，无需工作空间授权
    workspace_id = req.workspace_id or 0
    if workspace_id:
        authorize_workspace(user, workspace_id)
    agent_mode = getattr(req, "pipeline_mode", "agent") == "agent" or bool(req.attachments)
    ctx = ExecutionContext(user_id=user["user_id"], user_role=user.get("role", ""),
                           workspace_id=req.workspace_id or 0,
                           extra={"waker_key": getattr(req, "waker_key", "") or ""})
    cid = getattr(req, "conversation_id", 0) or 0
    if cid:
        validate_conversation(ctx, cid)
    if not agent_mode:
        if ctx.extra.get("waker_key"):
            raise HTTPException(422, "Waker 会话不能切换到旧执行管线，请新建会话")
        return
    if cid:
        row = execute_query("SELECT status FROM adh_agent_sessions WHERE conversation_id=%s", (cid,), fetchone=True)
        if row and row["status"] == "deleting":
            raise HTTPException(409, "该会话正在删除或等待清理重试，不能继续执行")
        if row and row["status"] == "closed":
            raise HTTPException(409, "该会话已清空，请新建会话")
        if row and row["status"] != "idle":
            raise HTTPException(409, "会话正在执行或执行中断，请确认状态后操作")
    try:
        resolve_policy(ctx)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def validate_conversation(ctx, conversation_id):
    from services.shared.common.auth import authorize_workspace
    # workspace_id=0 为全局助手会话，仅按 user_id 隔离，无需工作空间授权
    if ctx.workspace_id:
        authorize_workspace({"user_id": ctx.user_id, "role": ctx.user_role}, ctx.workspace_id)
    row = execute_query("SELECT * FROM adh_conversations WHERE id=%s AND user_id=%s",
                        (conversation_id, ctx.user_id), fetchone=True)
    if not row:
        raise HTTPException(404, "会话不存在或无权访问")
    if int(row.get("workspace_id") or 0) != ctx.workspace_id:
        raise HTTPException(403, "会话不属于当前工作空间")
    chosen = ctx.extra.get("waker_key")
    if row.get("waker_key") and chosen and row["waker_key"] != chosen:
        raise HTTPException(409, "切换 Waker 需要新建会话")
    if row.get("waker_key"):
        ctx.extra["waker_key"] = row["waker_key"]
    return row


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
    sdk_pid: int = 0
    sdk_start: str = ""
    sandbox_used: bool = False
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
        changed = execute_write(
            "UPDATE adh_agent_sessions SET status=%s, sdk_session_id=COALESCE(NULLIF(%s,''),sdk_session_id), "
            "execution_token=NULL, updated_at=UTC_TIMESTAMP(6) "
            "WHERE session_key=%s AND execution_token=%s AND status='running'",
            ("idle" if success or initialized_only else "interrupted", sdk_session_id, self.key, self.token),
        )
        self.closed = True
        if changed != 1:
            raise RuntimeError("会话执行状态更新冲突")


def claim_session(task, backend, config):
    from services.datamind.execution.tool_policy import resolve_policy
    ctx = task.context
    if not ctx or ctx.user_id <= 0:
        raise PermissionError("缺少可信执行身份")
    if config.get("cwd") or config.get("allowed_dirs"):
        raise ValueError("Waker 仅允许独立会话目录，请移除执行层 cwd/allowed_dirs 宽目录配置")
    cid = int(ctx.extra.get("conversation_id") or 0)
    if cid:
        validate_conversation(ctx, cid)
    ceiling = config.get("allowed_tools")
    policy = resolve_policy(ctx, ceiling)
    ctx.extra["waker_key"] = policy.waker["waker_key"]
    layer_id = int(config.get("_layer_id") or 0)
    layer_bound = False
    if layer_id:
        from services.datamind.execution import service
        from services.datamind.execution.tool_policy import live_ceiling
        layer_bound = any(r["id"] == layer_id for r in service.get_workspace_layers(ctx.workspace_id))
        from types import SimpleNamespace
        current = live_ceiling(ctx, SimpleNamespace(layer_id=layer_id, layer_bound=layer_bound, backend=backend))
        policy = resolve_policy(ctx, current)
        ceiling = current
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
                if conv.get("waker_key") and conv["waker_key"] != policy.waker["waker_key"]:
                    raise HTTPException(409, "切换 Waker 需要新建会话")
                cur.execute("SELECT * FROM adh_agent_sessions WHERE conversation_id=%s FOR UPDATE", (cid,))
                row = cur.fetchone()
            else:
                row = None
            if row:
                if (row["user_id"], row["workspace_id"], row["waker_key"], row["backend"]) != (
                        ctx.user_id, ctx.workspace_id, policy.waker["waker_key"], backend):
                    raise HTTPException(403, "执行会话身份或后端不匹配，请新建会话")
                if row["storage_node"] != node:
                    raise HTTPException(409, "会话工作区不在当前存储节点，无法恢复")
                if row["status"] == "deleting":
                    raise HTTPException(409, "该会话正在删除或等待清理重试，不能继续执行")
                if row["status"] == "closed":
                    raise HTTPException(409, "该会话已清空，请新建会话")
                if row["status"] == "running":
                    raise HTTPException(409, "该会话正在执行或上次执行尚未确认停止")
                root = session_paths(base, row["session_key"], ctx.workspace_id)
                if row["status"] == "interrupted":
                    raise HTTPException(409, "上次执行已中断，请新建会话；不会自动重放有副作用的请求")
            else:
                if ctx.extra.get("session_id"):
                    raise HTTPException(409, "旧 SDK 会话不能直接接管，请新建会话")
                if cid and conv.get("executor_session_id"):
                    raise HTTPException(409, "旧执行会话需重新建立安全工作区，请新建会话；聊天记录仍保留")
                key = uuid.uuid4().hex
                root = session_paths(base, key, ctx.workspace_id, create=True)
                row = {"session_key": key, "sdk_session_id": ""}
                cur.execute(
                    "INSERT INTO adh_agent_sessions (session_key,conversation_id,user_id,workspace_id,waker_key,backend,"
                    "storage_node,relative_dir,policy_hash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (key, cid or None, ctx.user_id, ctx.workspace_id, policy.waker["waker_key"], backend,
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
                cur.execute("UPDATE adh_conversations SET waker_key=%s WHERE id=%s", (policy.waker["waker_key"], cid))
    runtime = SessionWorkspace(row["session_key"], token, root, policy, ceiling, row.get("sdk_session_id") or "",
                               layer_id=layer_id, layer_bound=layer_bound, backend=backend)
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
            logging.getLogger(__name__).error("会话未记录 SDK 进程，保持阻断: %s", row["session_key"])
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
