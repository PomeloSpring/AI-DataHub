"""QMind 客户端 — 通过 qmind CLI 对接 Qoder 云端知识库(notebook).

QMind 是 Qoder 的知识库能力,以「笔记本(notebook)」为单位。它与本系统之间不是
自建 HTTP 服务,而是通过 qoder 生态里的 `qmind` 命令行交互(与 qmind 技能用法一致):

- 二进制放置于项目 `runtime/qmind/qmind`(与 qodercli 同层, 随仓库走);
- 缺失时按需下载到 `runtime/qmind/`; 也可通过 setup.sh 预装;
- 认证凭据由 `qmind login` 落盘在 `~/.qmind/credentials.json`,本模块不接触 token;
- `qmind notebook list -format json`  → 列出(检索)全部 Qoder 知识库;
- `qmind retrieve -nb <id> -q <q> -format json` → 对指定笔记本做知识检索。

对外暴露:
- list_notebooks()            供后台「从 Qoder 检索知识库」列出多个知识库
- qmind_retrieve(...)         knowledge_search 的默认后端:命中绑定则用 CLI 检索,
                              否则透明回退既有 graphrag 元数据检索,保证行为不劣化。
"""

import json
import logging
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_CLI_TIMEOUT = 60
_OSS_BASE = "https://qoder-ide-cn.oss-accelerate.aliyuncs.com/qmind/cli"

# ── 简易熔断: 连续失败达阈值后短路一段时间, 避免每次问答都 spawn CLI 子进程 ──
_CB_FAIL_THRESHOLD = 3
_CB_COOLDOWN_SEC = 120
def _breaker_client():
    from services.shared.common.cache.factory import get_cache
    # 熔断是协调状态，必须使用会抛错的 Redis 原语，不能走 best-effort 缓存接口。
    return get_cache("qmind_breaker")._get_redis()


def _breaker_allowed(now: float = None) -> bool:
    return not bool(_breaker_client().exists("chatbi:qmind:breaker:open"))


def _breaker_record(success: bool, now: float = None):
    # 过期时间由 Redis 计时，不依赖不同实例的本地时钟。
    _breaker_client().eval("""
        if ARGV[1] == '1' then
            redis.call('DEL', KEYS[1], KEYS[2]); return 0
        end
        local n = redis.call('INCR', KEYS[1])
        if n >= tonumber(ARGV[2]) then
            redis.call('SET', KEYS[2], '1', 'EX', ARGV[3])
            redis.call('DEL', KEYS[1]); return 1
        end
        return 0
    """, 2, "chatbi:qmind:breaker:fails", "chatbi:qmind:breaker:open",
        "1" if success else "0", _CB_FAIL_THRESHOLD, _CB_COOLDOWN_SEC)


# ── CLI 解析 / 按需下载 ──────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parents[3]  # → 仓库根
_RUNTIME_QMIND_DIR = _PROJECT_ROOT / "runtime" / "qmind"


def _platform_slug() -> tuple[str, str, str]:
    """返回 (os, arch, ext),与 qmind 技能下载脚本保持一致."""
    u = platform.system().lower()
    if u.startswith("win") or u in ("msys", "mingw", "cygwin"):
        os_name = "windows"
    elif u == "darwin":
        os_name = "darwin"
    else:
        os_name = "linux"
    m = platform.machine().lower()
    arch = "amd64" if m in ("x86_64", "amd64") else "arm64" if m in ("aarch64", "arm64") else m
    ext = ".exe" if os_name == "windows" else ""
    return os_name, arch, ext


def _qmind_candidates() -> list[Path]:
    """二进制自动识别顺序: 项目 runtime/qmind → 用户缓存 ~/.cache/qmind/bin。

    项目目录随仓库走, 与运行用户无关(修复以 root 跑时 home 漂移), 也免逐节点手配。
    显式 QMIND_BIN 由 qmind_bin_path() 单独短路, 不进入本清单。
    """
    os_name, arch, ext = _platform_slug()
    name = f"qmind-{os_name}-{arch}{ext}"
    return [
        _RUNTIME_QMIND_DIR / "qmind",       # 项目固定单文件(推荐部署形态)
        _RUNTIME_QMIND_DIR / name,          # 项目带平台后缀
        Path.home() / ".cache" / "qmind" / "bin" / name,  # 兼容旧缓存
    ]


def qmind_bin_path() -> Path:
    """解析 qmind 可执行文件位置。

    QMIND_BIN 显式设置时以其为唯一答案(便于 CI/临时覆盖, 不做二次回退);
    否则在 runtime/qmind/ 目录中自动挑选第一个真实可执行文件,
    未命中返回项目规范路径供下载/错误提示。
    """
    override = os.environ.get("QMIND_BIN")
    if override:
        return Path(override).expanduser()
    for candidate in _qmind_candidates():
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return _RUNTIME_QMIND_DIR / "qmind"


def ensure_qmind_cli() -> Path | None:
    """确保 qmind CLI 可用;缺失时从公共 OSS 下载到 runtime/qmind/.

    也可通过 bash runtime/qmind/setup.sh 预装。
    """
    bin_path = qmind_bin_path()
    if bin_path.exists() and os.access(bin_path, os.X_OK):
        return bin_path
    os_name, arch, ext = _platform_slug()
    url = f"{_OSS_BASE}/qmind-{os_name}-{arch}{ext}"
    try:
        bin_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("[qmind] downloading CLI from %s", url)
        subprocess.run(["curl", "-fsSL", "-o", str(bin_path), url], check=True, timeout=120)
        if ext != ".exe":
            bin_path.chmod(0o755)
        return bin_path
    except Exception as e:  # noqa: BLE001  下载失败不致命,调用方按空结果处理
        logger.warning("[qmind] CLI download failed: %s", e)
        return None


def _cli_env() -> dict:
    """构造 qmind CLI 子进程环境.

    CLI 支持两种认证:交互式 `qmind login` 落盘 credentials,或设置 `QMIND_TOKEN`
    为个人 token(`pt-...`,CLI 会自动换取短期 job token)。服务器/容器无交互登录时,
    用 `.env` 的 `QODER_PERSONAL_ACCESS_TOKEN`(即 pt-... PAT)注入 `QMIND_TOKEN`,
    使知识库检索无需依赖 `qmind login`。已有 `QMIND_TOKEN` 时优先保留。
    """
    env = dict(os.environ)
    if not env.get("QMIND_TOKEN"):
        pat = env.get("QODER_PERSONAL_ACCESS_TOKEN", "")
        if pat:
            env["QMIND_TOKEN"] = pat
    return env


def _run_cli_ex(args: list[str]) -> tuple[dict | None, str]:
    """执行 qmind CLI 并解析 JSON 输出; 返回 (data, err). 失败时 data=None 并计入熔断."""
    bin_path = ensure_qmind_cli()
    if not bin_path:
        _breaker_record(False)
        return None, "qmind CLI 不可用"
    try:
        proc = subprocess.run(
            [str(bin_path), *args],
            capture_output=True, text=True, timeout=_CLI_TIMEOUT,
            env=_cli_env(),
        )
        if proc.returncode != 0:
            err = (proc.stderr or "").strip()[:300]
            logger.warning("[qmind] `%s` failed (rc=%s): %s",
                           " ".join(args), proc.returncode, err)
            _breaker_record(False)
            return None, err
        out = json.loads(proc.stdout or "{}")
        _breaker_record(True)
        return out, ""
    except subprocess.TimeoutExpired:
        logger.warning("[qmind] `%s` timed out", " ".join(args))
        _breaker_record(False)
        return None, "timeout"
    except FileNotFoundError:
        logger.warning("[qmind] CLI not found at %s", bin_path)
        _breaker_record(False)
        return None, "cli not found"
    except json.JSONDecodeError as e:
        logger.warning("[qmind] non-JSON output: %s", e)
        _breaker_record(False)
        return None, f"non-JSON output: {e}"
    except Exception as e:  # noqa: BLE001
        logger.warning("[qmind] run error: %s", e)
        _breaker_record(False)
        return None, str(e)[:300]


def _run_cli(args: list[str]) -> dict | None:
    """执行 qmind CLI 并解析 JSON 输出;失败返回 None(并计入熔断)."""
    data, _err = _run_cli_ex(args)
    return data


# ── 知识库(notebook)列举 ────────────────────────────────────────────


def is_unavailable_error(err: str) -> bool:
    """判定错误属于「当前凭据对目标 notebook 不可用」(无权/不存在, 如切换账号)。

    仅此类错误允许触发 notebook 自动重建; 网络抖动/服务端 5xx 不在此列,
    避免故障期间误建一堆重复知识库。
    """
    e = (err or "").lower()
    return ("insufficient notebook permission" in e
            or '"errorcode":"forbidden"' in e
            or "not found" in e)


def probe_notebook(notebook_id: str) -> tuple[bool, str]:
    """探测 notebook 对当前凭据是否可达, 返回 (ok, err)。

    以 exit code 为准(rc=0 即可达) —— `notebook get` 无视 -format json 输出
    table 文本, 不能走 JSON 解析路径判定。
    """
    bin_path = ensure_qmind_cli()
    if not bin_path:
        return False, "qmind CLI 不可用"
    try:
        proc = subprocess.run(
            [str(bin_path), "notebook", "get", notebook_id],
            capture_output=True, text=True, timeout=_CLI_TIMEOUT,
            env=_cli_env(),
        )
        if proc.returncode == 0:
            return True, ""
        return False, (proc.stderr or "").strip()[:300]
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:300]


def create_notebook(title: str, description: str = "") -> str | None:
    """在当前凭据账号下新建 notebook, 返回新 notebook_id(失败返回 None)。"""
    args = ["notebook", "create", "-title", title, "-format", "json"]
    if description:
        args += ["-desc", description]
    data, err = _run_cli_ex(args)
    if not data:
        logger.warning("[qmind] notebook create failed: %s", err)
        return None
    return data.get("id") or data.get("notebook_id") or (data.get("notebook") or {}).get("id")


def list_notebooks() -> list[dict]:
    """检索 Qoder 云端全部 QMind 知识库(笔记本).

    Returns: [{notebook_id, title, org_id, description, status}] —— 失败/无凭据时返回 []。
    """
    data = _run_cli(["notebook", "list", "-format", "json"])
    if not data:
        return []
    notebooks = data.get("notebooks") or data.get("data") or []
    out: list[dict] = []
    for nb in notebooks:
        if not isinstance(nb, dict):
            continue
        nb_id = nb.get("id") or nb.get("notebook_id")
        if not nb_id:
            continue
        out.append({
            "notebook_id": nb_id,
            "title": nb.get("title") or "",
            "org_id": nb.get("orgId") or nb.get("org_id") or "",
            "description": nb.get("description") or "",
            "status": nb.get("status") or "active",
        })
    return out


# ── 知识检索 ─────────────────────────────────────────────────────────

def retrieve_notebook(notebook_id: str, question: str, top_k: int = 5) -> list[dict]:
    """对单个笔记本检索,返回标准化 chunk 列表(失败返回 [])."""
    data = _run_cli(["retrieve", "-nb", notebook_id, "-q", question, "-format", "json"])
    if not data:
        return []
    raw = data.get("results") or data.get("chunks") or data.get("data") or []
    chunks: list[dict] = []
    for item in raw[: max(1, int(top_k))]:
        if not isinstance(item, dict):
            continue
        text = item.get("content") or item.get("text") or item.get("answer") or ""
        if not text:
            continue
        chunks.append({
            "title": item.get("title") or item.get("source") or "",
            "content": text,
            "score": item.get("score"),
        })
    return chunks


def retrieve_notebook_strict(notebook_id: str, question: str, top_k: int = 5) -> list[dict]:
    """设计任务检索：失败与无命中严格区分，不执行任何回退。"""
    if not _breaker_allowed():
        raise RuntimeError("知识库熔断中，请稍后重试")
    data = _run_cli(["retrieve", "-nb", notebook_id, "-q", question, "-format", "json"])
    if data is None:
        raise RuntimeError("业务知识库检索失败")
    raw = data.get("results") or data.get("chunks") or data.get("data") or []
    if not isinstance(raw, list):
        raise RuntimeError("知识库结果格式不受支持")
    return [{"title": item.get("title") or item.get("source") or "",
             "content": item.get("content") or item.get("text") or item.get("answer") or ""}
            for item in raw[:top_k] if isinstance(item, dict)]


def _parse_cfg(value) -> dict:
    if isinstance(value, dict):
        return value
    if value in (None, ""):
        return {}
    try:
        return json.loads(value)
    except Exception:
        return {}


def _bound_qmind_kbs(kb_ids: list | None) -> list[dict]:
    """加载启用的 QMind 知识库(带 notebook_id);kb_ids 指定时按绑定范围过滤."""
    from services.shared.common.db import execute_query

    if kb_ids is not None and not kb_ids:
        return []
    try:
        ids = [int(x) for x in (kb_ids or [])]
        if any(x <= 0 for x in ids):
            raise ValueError("知识库标识无效")
    except (TypeError, ValueError) as exc:
        raise ValueError("知识库授权列表无效") from exc
    try:
        if ids:
            placeholders = ", ".join(["%s"] * len(ids))
            rows = execute_query(
                f"SELECT id, name, source_config FROM adh_knowledge_bases "
                f"WHERE kb_type = 'qmind' AND status = 'active' AND id IN ({placeholders})",
                tuple(ids),
            )
        else:
            rows = execute_query(
                "SELECT id, name, source_config FROM adh_knowledge_bases "
                "WHERE kb_type = 'qmind' AND status = 'active' ORDER BY id"
            )
        kbs = []
        for r in rows or []:
            cfg = _parse_cfg(r.get("source_config"))
            nb_id = cfg.get("notebook_id")
            if nb_id:
                kbs.append({"id": r["id"], "name": r.get("name"), "notebook_id": nb_id, "cfg": cfg})
        return kbs
    except Exception as e:  # noqa: BLE001
        logger.warning("[qmind] load knowledge bases failed: %s", e)
        return []


def _fallback_graphrag(question: str, datasource_id: int, tagged: bool) -> dict:
    """回退到既有 graphrag 元数据检索策略(行为不变)."""
    from services.datamind.rag.strategies import get_strategy

    result = get_strategy("graphrag").retrieve(question=question, datasource_id=datasource_id)
    result = dict(result or {})
    if tagged:  # 存在绑定的 QMind 库但检索不可用 → 标注回退
        result["rag_source"] = f"{result.get('rag_source', 'graphrag')}|qmind_fallback"
    return result


def extract_object_keys(chunks: list[dict], limit: int = 8) -> list[str]:
    """从知识库 chunk 提取命中的本体对象 key(T7 种子反哺的提取器)。

    与 to_cloud_md 的渲染格式同源: 「## 业务对象: <display_name> (<key>)」。
    只抽 key 字符串, 不回任何物理信息; 顺带兼容内部 to_md 的同格式标题。
    """
    import re
    pat = re.compile(r"##\s*业务对象:.*?\(([^()\s]+)\)")
    out: list[str] = []
    seen: set[str] = set()
    for c in chunks or []:
        for m in pat.finditer(str(c.get("content") or "")):
            k = m.group(1).strip()
            if k and k not in seen:
                seen.add(k)
                out.append(k)
            if len(out) >= limit:
                return out
    return out


def _extract_doc_stamp(chunks: list[dict]) -> dict | None:
    """从 chunk 正文解析同步写入的版本戳注释(任一 chunk 命中即可)。

    形如: <!-- model-version-stamp model_id=123 model_version=2026-... synced_at=... -->
    """
    import re
    pat = re.compile(
        r"model-version-stamp\s+model_id=(\S+)\s+model_version=(\S+)\s+synced_at=(\S+)")
    for c in chunks or []:
        m = pat.search(str(c.get("content") or ""))
        if m:
            return {"model_id": m.group(1), "model_version": m.group(2),
                    "synced_at": m.group(3)}
    return None


def _evaluate_doc_freshness(datasource_id: int, chunks: list[dict]) -> dict:
    """比对知识库文档版本戳与本地当前 active 本体, 返回 {doc_stale, doc_version}。

    doc_stale=True 仅当能确认文档版本落后于 active 版本; 无版本戳/无 active 模型 → None(不下结论)。
    """
    stamp = _extract_doc_stamp(chunks)
    if not stamp:
        return {"doc_stale": None, "doc_version": None}
    try:
        from services.datacatalog.services.ontology_kb_sync import current_active_version
        active = current_active_version(datasource_id)
    except Exception as e:  # noqa: BLE001
        logger.debug("[qmind] freshness check skipped: %s", e)
        return {"doc_stale": None, "doc_version": stamp.get("model_version")}
    if not active:
        return {"doc_stale": None, "doc_version": stamp.get("model_version")}
    stale = str(stamp.get("model_version")) != str(active.get("model_version"))
    return {"doc_stale": stale, "doc_version": stamp.get("model_version")}


def qmind_retrieve(
    question: str,
    datasource_id: int = 0,
    kb_ids: list | None = None,
    top_k: int = 5,
    system_scope: bool = False,
) -> dict:
    """默认 QMind 检索入口:优先命中的绑定知识库经 qmind CLI 检索,否则回退 graphrag.

    Args:
        question: 用户问题。
        datasource_id: 回退 graphrag 时按数据源过滤元数据。
        kb_ids: AS-BOT 绑定的知识库 ID;指定时仅在这些库中检索。
        top_k: 检索条数(可被知识库 source_config.top_k 覆盖)。
        system_scope: 系统能力形态标记(能力叠加，域规则更新)。命中/回退行为与业务一致，
            仅在结果中标注 ``system_scope=True`` 供分桶归因；系统运营问题应走 system_* 工具，
            不拿业务知识充数由 prompt 引导承担（不再是检索硬限定）。

    Returns:
        QMind 命中: {chunks, count, rag_source='qmind', knowledge_base, notebook_id};
        否则 graphrag 统一结果 dict(系统能力时额外携带 ``system_scope=True`` 标注)。
    """
    def _mark_system(result) -> dict:
        out = dict(result or {})
        if system_scope:
            out["system_scope"] = True
        return out

    kbs = _bound_qmind_kbs(kb_ids)
    # 熔断开启且确有绑定的 QMind 库: 短路回退本地 hybrid, 不逐次 spawn CLI(标注降级)。
    if kbs and not _breaker_allowed():
        result = _fallback_graphrag(question, datasource_id, tagged=True)
        result = _mark_system(result)
        result["rag_source"] = f"{result.get('rag_source', 'graphrag')}|qmind_circuit_open"
        result["degraded"] = True
        return result
    for kb in kbs:
        k = int(kb["cfg"].get("top_k") or top_k)
        chunks = retrieve_notebook(kb["notebook_id"], question, k)
        if chunks:
            logger.info("[qmind] kb=%s notebook=%s retrieved %d chunks",
                        kb.get("name"), kb["notebook_id"], len(chunks))
            fresh = _evaluate_doc_freshness(datasource_id, chunks)
            return {
                "chunks": chunks,
                "count": len(chunks),
                "rag_source": "qmind",
                "knowledge_base": kb.get("name"),
                "notebook_id": kb["notebook_id"],
                "hit_object_keys": extract_object_keys(chunks),
                **fresh,
            }
    # 无命中(未绑定 / CLI 不可用 / 无结果)→ 回退；系统能力形态同样回退业务元数据，
    # 以 rag_source/system_scope 标注来源（能力叠加，检索结果可区分可诊断）。
    return _mark_system(_fallback_graphrag(question, datasource_id, tagged=bool(kbs)))
