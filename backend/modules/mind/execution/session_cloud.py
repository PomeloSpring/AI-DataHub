"""云空间会话产物上云 — 会话工作区文件的对象存储镜像与按需回落。

为什么需要：会话产物（用户上传附件 + Agent 生成的报表/图表/xlsx）原先只落在本地持久卷
`{user_id}/workspace/{workspace_id}/sessions/{session_key}/workspace/`，实例迁移或卷被清理
后「库里有历史会话、本地没有工作区」→ 续聊被 409 拦住、产物面板打不开（产物实质丢失）。

口径：
- **只镜像 workspace/**：runtime/ 是执行层 CLI 的 HOME（可能落网关配置/凭据），绝不上云；
  回落后续聊不 resume 旧 SDK 会话（`sdk_session_id` 置空），上下文由库里的历史消息重建。
- **对象 key 首段是用户 ID**：`{prefix}/{user_id}/sessions/{session_key}/workspace/{相对路径}`，
  不受空间改名/迁移影响（与资产域 build_asset_key 同一布局哲学）。
- **云端是唯一可靠副本，本地是工作缓存**：每轮结束在后台增量推送（新增/改动上传、
  本地已删的删对象）；需要本地调度或读取产物而本地目录缺失时，自动从云端拉回。
- **未配置对象存储 = 不上云**（不往本地回退目录 copy 一份冒充“已同步”）：文件面板据云端
  实际清单标记同步态，用户看得见「未同步」，而不是被代码静默掩盖。
- 用户可在「个人设置」关闭自动上云（默认开启）；关闭只影响**推送**，已有云端副本仍可回落。

失败暴露（与 no-silent-degradation 对齐）：同步是后台动作，不打断对话（用户决策），
但失败必须可诊断 —— 服务端 error 日志 + 文件面板的「未同步」标记（据云端清单实判）。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# 增量判定依据（rsync 式启发）：本地记录 size+mtime，变化即重传；不做全量哈希以免大表文件白付成本
MANIFEST_NAME = ".cloud-sync.json"
# 单文件上限（MB）：超限不上传，面板标未同步并留可诊断原因（不静默截断）
DEFAULT_MAX_FILE_MB = 512
_SESSION_KEY_RE = re.compile(r"[a-f0-9]{32}")


class CloudSyncError(RuntimeError):
    """云端不可用/不可判定（区别于“云端确实没有这个会话的副本”）。"""


def _max_file_bytes() -> int:
    try:
        mb = int(os.getenv("ADH_CLOUD_SYNC_MAX_FILE_MB", DEFAULT_MAX_FILE_MB))
    except ValueError:
        mb = DEFAULT_MAX_FILE_MB
    return max(mb, 1) * 1024 * 1024


# ── 对象 key 布局（唯一构建点）───────────────────────────────────────────────

def build_session_prefix(user_id: int, session_key: str) -> str:
    """会话产物前缀：`{prefix}/{user_id}/sessions/{session_key}/workspace`。

    任何读写会话产物对象的代码不得自行拼 key（与 build_asset_key 同纪律）。
    """
    from backend.common.config import OBJECT_STORAGE_PREFIX
    if not user_id or int(user_id) <= 0:
        raise ValueError("缺少可信用户身份，不能定位云空间产物")
    if not _SESSION_KEY_RE.fullmatch(session_key or ""):
        raise ValueError("会话标识无效")
    return f"{OBJECT_STORAGE_PREFIX}/{int(user_id)}/sessions/{session_key}/workspace"


def safe_rel_path(raw: str) -> str:
    """云端对象名 → 工作区相对路径（拒绝对路径/`..`/符号链接段，防回落时被构造路径逃逸）。"""
    s = (raw or "").strip().lstrip("/")
    parts = []
    for seg in s.split("/"):
        if seg in ("", "."):
            continue
        if seg == ".." or "\x00" in seg:
            raise ValueError(f"非法产物路径: {raw}")
        parts.append(seg)
    if not parts:
        raise ValueError(f"非法产物路径: {raw}")
    return "/".join(parts)


# ── 用户开关（默认开启）──────────────────────────────────────────────────────

def cloud_sync_enabled(user_id: int) -> bool:
    """用户是否开启「会话产物自动上云」（无偏好行 = 默认开启）。"""
    if not user_id or int(user_id) <= 0:
        return False
    from backend.common.db.metadata_db import get_metadata_conn
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT cloud_sync_enabled FROM adh_user_preferences WHERE user_id=%s",
                        (int(user_id),))
            row = cur.fetchone()
    except Exception as exc:  # noqa: BLE001 — 偏好不可读时按默认(开启)处理，不让读表失败变成“功能没上云”
        logger.warning("读取云同步偏好失败，按默认开启处理: user=%s err=%s", user_id, exc)
        return True
    finally:
        conn.close()
    if not row:
        return True
    return bool(row.get("cloud_sync_enabled"))


def set_cloud_sync_enabled(user_id: int, enabled: bool) -> None:
    from backend.common.db.metadata_db import get_metadata_conn
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_user_preferences (user_id, cloud_sync_enabled) VALUES (%s, %s) "
                "ON DUPLICATE KEY UPDATE cloud_sync_enabled=VALUES(cloud_sync_enabled)",
                (int(user_id), 1 if enabled else 0))
        conn.commit()
    finally:
        conn.close()


# ── 本地快照与增量清单 ───────────────────────────────────────────────────────

def _workspace_dir(root) -> Path:
    return Path(root) / "workspace"


def local_files(root) -> dict:
    """workspace/ 下的产物文件 → {相对路径: {"size", "mtime_ns"}}（不跟随符号链接）。"""
    ws = _workspace_dir(root)
    out: dict = {}
    if not ws.is_dir():
        return out
    for p in sorted(ws.rglob("*")):
        try:
            if not p.is_file() or p.is_symlink():
                continue
            st = p.stat()
            out[str(p.relative_to(ws))] = {"size": int(st.st_size), "mtime_ns": int(st.st_mtime_ns)}
        except OSError:
            continue
    return out


def _manifest_path(root) -> Path:
    return Path(root) / "runtime" / MANIFEST_NAME


def read_manifest(root) -> dict:
    try:
        data = json.loads(_manifest_path(root).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (ValueError, OSError) as exc:
        logger.warning("云同步清单不可读，按全量重传处理: %s (%s)", _manifest_path(root), exc)
        return {}
    files = data.get("files") if isinstance(data, dict) else None
    return files if isinstance(files, dict) else {}


def write_manifest(root, files: dict) -> None:
    """原子写清单（临时文件 + replace，0o600）：崩溃不留半截 JSON。"""
    path = _manifest_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    payload = json.dumps({"files": files, "synced_at": datetime.now(timezone.utc).isoformat()},
                         ensure_ascii=False)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


# ── 推送（每轮结束 / 产物写盘后）─────────────────────────────────────────────

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _session_lock(session_key: str) -> threading.Lock:
    """同一会话的同步串行化（web 为单进程常驻；清单读改写不能交叠）。"""
    with _locks_guard:
        lock = _locks.get(session_key)
        if lock is None:
            lock = _locks[session_key] = threading.Lock()
        return lock


def sync_available() -> bool:
    """对象存储是否真的可用（未配置即“无云可上”，不假装已同步）。"""
    from backend.common.object_storage import get_object_storage
    return get_object_storage().is_object_storage


def sync_session(root, user_id: int, session_key: str, *, reason: str = "turn") -> dict:
    """把会话工作区产物增量镜像到云空间。

    返回 {"uploaded", "deleted", "skipped", "bytes", "note"}；**不抛**给主链路，
    但失败原因写在返回值与 error 日志里（面板据云端实际清单标未同步）。
    """
    result = {"uploaded": 0, "deleted": 0, "skipped": 0, "bytes": 0, "note": ""}
    if not _SESSION_KEY_RE.fullmatch(session_key or ""):
        result["note"] = "会话标识无效，未执行上云"
        logger.error("会话产物上云跳过（会话标识无效）: %s", session_key)
        return result
    if not cloud_sync_enabled(user_id):
        result["note"] = "用户已关闭自动上云"
        return result
    from backend.common.object_storage import get_object_storage
    storage = get_object_storage()
    if not storage.is_object_storage:
        result["note"] = "对象存储未配置，产物仅存本地"
        logger.warning("云空间未配置对象存储，会话产物不上云: session=%s", session_key)
        return result
    ws = _workspace_dir(root)
    if not ws.is_dir():
        result["note"] = "会话工作区目录不存在"
        return result
    prefix = build_session_prefix(user_id, session_key)
    cap = _max_file_bytes()
    with _session_lock(session_key):
        manifest = read_manifest(root)
        snapshot = local_files(root)
        for rel, meta in snapshot.items():
            entry = manifest.get(rel) or {}
            if entry.get("size") == meta["size"] and entry.get("mtime_ns") == meta["mtime_ns"]:
                continue
            if meta["size"] > cap:
                result["skipped"] += 1
                logger.error("产物超过单文件上限未上云: session=%s path=%s bytes=%s cap=%s",
                             session_key, rel, meta["size"], cap)
                continue
            target = ws / rel
            try:
                storage.upload_file(f"{prefix}/{rel}", target,
                                    content_type=_guess_type(rel))
            except Exception:  # noqa: BLE001 — 单文件失败不中断整轮同步
                result["skipped"] += 1
                logger.exception("产物上云失败: session=%s path=%s", session_key, rel)
                continue
            manifest[rel] = {"size": meta["size"], "mtime_ns": meta["mtime_ns"]}
            result["uploaded"] += 1
            result["bytes"] += meta["size"]
        for rel in [k for k in manifest if k not in snapshot]:
            try:
                storage.delete(f"{prefix}/{rel}")
            except Exception:  # noqa: BLE001
                logger.exception("云端产物删除失败(本地已删): session=%s path=%s", session_key, rel)
                continue
            manifest.pop(rel, None)
            result["deleted"] += 1
        write_manifest(root, manifest)
    if result["skipped"]:
        result["note"] = f"{result['skipped']} 个文件未上云（超限或上传失败），详见服务端日志"
    logger.info("会话产物上云(%s): session=%s uploaded=%s deleted=%s skipped=%s bytes=%s",
                reason, session_key, result["uploaded"], result["deleted"],
                result["skipped"], result["bytes"])
    return result


def _guess_type(rel: str) -> str:
    import mimetypes
    return mimetypes.guess_type(rel)[0] or "application/octet-stream"


def schedule_sync(root, user_id: int, session_key: str, *, reason: str = "api") -> None:
    """后台推送（不占用请求耗时）：产物写盘类接口用完即调，用户无需等待也无需被告知。"""
    import threading

    def _run():
        try:
            sync_session(root, user_id, session_key, reason=reason)
        except Exception:  # noqa: BLE001 — 后台同步失败只暴露到日志与面板同步态
            logger.exception("会话产物后台上云失败: session=%s reason=%s", session_key, reason)

    threading.Thread(target=_run, name=f"cloud-sync:{session_key[:8]}", daemon=True).start()


# ── 云端清单与同步标记 ───────────────────────────────────────────────────────

def cloud_files(user_id: int, session_key: str) -> dict:
    """云端产物清单 → {相对路径: size}。云端不可用时外抛 CloudSyncError（不返回空集冒充“云端没有”）。"""
    from backend.common.object_storage import get_object_storage
    storage = get_object_storage()
    if not storage.is_object_storage:
        return {}
    prefix = build_session_prefix(user_id, session_key) + "/"
    try:
        objects = storage.list_keys(prefix)
    except Exception as exc:  # noqa: BLE001
        raise CloudSyncError(f"云空间产物清单不可读: {exc}") from exc
    out: dict = {}
    for obj in objects:
        key = obj.get("key") or ""
        if not key.startswith(prefix):
            continue
        rel = key[len(prefix):]
        if not rel or rel.endswith("/"):
            continue
        try:
            out[safe_rel_path(rel)] = int(obj.get("size") or 0)
        except ValueError:
            logger.warning("忽略非法产物对象名: %s", key)
    return out


def sync_status(user_id: int, session_key: str, files: list) -> dict:
    """给文件清单补「是否已同步云空间」标记（以云端实际对象为准，不凭清单文件自证）。

    返回 {"enabled", "configured", "reason", "files"}：files 每项加 `synced`（云端有同名同
    大小对象才算已同步）。云端不可判定 → 全部标未同步并给出 reason，而不是猜一个状态。
    """
    enabled = cloud_sync_enabled(user_id)
    if not sync_available():
        for f in files:
            f["synced"] = False
        return {"enabled": enabled, "configured": False,
                "reason": "对象存储未配置，产物只在本地", "files": files}
    try:
        remote = cloud_files(user_id, session_key)
    except CloudSyncError as exc:
        for f in files:
            f["synced"] = False
        return {"enabled": enabled, "configured": True, "reason": str(exc), "files": files}
    for f in files:
        f["synced"] = remote.get(f["path"]) == f["size"]
    return {"enabled": enabled, "configured": True, "reason": "", "files": files}


# ── 回落（需要本地调度或读取产物时）───────────────────────────────────────────

def has_cloud_backup(user_id: int, session_key: str) -> bool:
    try:
        return bool(cloud_files(user_id, session_key))
    except CloudSyncError:
        logger.exception("云空间副本探测失败: session=%s", session_key)
        return False


def _adopt_ownership(ws) -> None:
    """回落文件属主与会话工作区既有口径一致（root 下降权 nobody，见 loader.write_upload_file）。

    否则 root 落下的 0644 文件会让以 nobody 运行的执行层改不动自己刚“恢复回来”的产物。
    """
    if os.geteuid() != 0:
        return
    for p in [ws, *ws.rglob("*")]:
        try:
            os.chown(p, 65534, 65534)
        except OSError:
            logger.warning("回落文件降权失败: %s", p)


def restore_workspace(root, user_id: int, session_key: str, remote: dict = None) -> dict:
    """把云端产物拉回本地会话工作区（已存在的同名同大小文件跳过）。

    返回 {"restored", "bytes"}；云端不可用时外抛 CloudSyncError（把“拉不动”与“云端没有”
    区分开，前者必须让运维看得见）。`remote` 可由调用方传入已取到的清单，省一次 LIST。
    """
    if remote is None:
        remote = cloud_files(user_id, session_key)
    if not remote:
        return {"restored": 0, "bytes": 0}
    from backend.common.object_storage import get_object_storage
    storage = get_object_storage()
    prefix = build_session_prefix(user_id, session_key)
    ws = _workspace_dir(root)
    ws.mkdir(parents=True, exist_ok=True, mode=0o700)
    restored = 0
    total = 0
    snapshot = local_files(root)
    files: dict = {}
    for rel, size in remote.items():
        target = ws / rel
        cached = snapshot.get(rel)
        if cached and cached["size"] == size:
            files[rel] = {"size": size, "mtime_ns": cached["mtime_ns"]}
            continue
        try:
            if not storage.download_to_file(f"{prefix}/{rel}", target):
                logger.error("云端产物对象读取不到，跳过: session=%s path=%s", session_key, rel)
                continue
        except Exception:  # noqa: BLE001 — 单文件失败继续拉其余文件，计数与日志留痕
            logger.exception("云端产物回落失败: session=%s path=%s", session_key, rel)
            continue
        st = target.stat()
        files[rel] = {"size": int(st.st_size), "mtime_ns": int(st.st_mtime_ns)}
        restored += 1
        total += int(st.st_size)
    _adopt_ownership(ws)
    write_manifest(root, files)
    logger.info("云空间产物回落: session=%s restored=%s bytes=%s", session_key, restored, total)
    return {"restored": restored, "bytes": total}


def ensure_local_workspace(row, *, write=None) -> bool:
    """会话工作区本地缺失时从云空间自愈，返回本地是否可用。

    row 需含 session_key / workspace_id / user_id。成功后把执行映射登记到当前存储节点，
    并清空 `sdk_session_id`（runtime/ 不上云，旧 resume 转录不在这台机器上；历史消息仍在
    库里，下一轮按 DB 历史重建上下文）。`write` 可注入（claim 路径传当前事务游标，
    避免跨连接对同一行加锁死锁）。
    """
    from backend.modules.mind.execution import session_workspace as sw

    key = row.get("session_key") or ""
    user_id = int(row.get("user_id") or 0)
    workspace_id = int(row.get("workspace_id") or 0)
    if not _SESSION_KEY_RE.fullmatch(key) or not user_id:
        return False
    try:
        base = sw.workspace_base(create=False)
    except RuntimeError:
        # 存储卷整体不存在（新节点首次使用）：回落本身要落盘，直接按需创建
        base = sw.workspace_base(create=True)
    try:
        root = sw.session_dir(base, key, workspace_id, user_id)
    except ValueError:
        logger.error("会话路径标识无效，跳过云端回落: session=%s", key)
        return False
    if root.is_dir():
        return True
    if not sync_available():
        logger.error("本地产物缺失且对象存储未配置，无法从云空间回落: session=%s", key)
        return False
    with _session_lock(key):
        if root.is_dir():
            return True
        try:
            remote = cloud_files(user_id, key)
        except CloudSyncError:
            logger.exception("云空间回落不可用，本地产物缺失: session=%s", key)
            return False
        if not remote:
            # 云端确实没有副本（存量未上云/已被清理）：不建空目录假装“恢复成功”，
            # 让调用方给出可操作提示。
            logger.error("云空间没有该会话的产物副本: session=%s", key)
            return False
        try:
            # 走 session_paths 建标准布局（workspace/ + runtime/tmp + 降权），
            # 只 mkdir 一个 workspace/ 会让后续校验判为“会话工作区不可用”。
            root = sw.session_paths(base, key, workspace_id, user_id, create=True)
        except FileExistsError:
            pass
        try:
            stats = restore_workspace(root, user_id, key, remote=remote)
        except Exception:  # noqa: BLE001
            logger.exception("云空间回落失败: session=%s", key)
            return False
    if not root.is_dir():
        return False
    node = sw.storage_node(base)
    # backend_sessions 同 sdk_session_id 一起清空：runtime/ 不上云，各后端的转录都不在这台机器上
    sql = ("UPDATE adh_agent_sessions SET storage_node=%s,sdk_session_id='',backend_sessions=NULL,"
           "version=version+1,updated_at=UTC_TIMESTAMP(6) "
           "WHERE session_key=%s AND user_id=%s AND status IN ('idle','interrupted')")
    params = (node, key, user_id)
    try:
        changed = write(sql, params) if write is not None else sw.execute_write(sql, params)
    except Exception:  # noqa: BLE001 — 目录已就位但登记失败：留给下一轮重试，不伪装成已恢复
        logger.exception("云空间回落后的会话登记更新失败: session=%s", key)
        return False
    if changed != 1:
        logger.error("云空间回落后登记未生效(状态已变化): session=%s", key)
        return False
    logger.warning("会话工作区已从云空间重建(换节点/清理后自愈): session=%s restored=%s bytes=%s node=%s",
                   key, stats.get("restored"), stats.get("bytes"), node)
    return True


def purge_session_cloud(user_id: int, session_key: str) -> int:
    """删除会话的云空间产物副本（会话删除/清理时不留孤儿对象）。失败外抛由调用方处理。"""
    if not _SESSION_KEY_RE.fullmatch(session_key or "") or not user_id:
        return 0
    from backend.common.object_storage import get_object_storage
    storage = get_object_storage()
    if not storage.is_object_storage:
        return 0
    return storage.delete_prefix(build_session_prefix(user_id, session_key) + "/")
