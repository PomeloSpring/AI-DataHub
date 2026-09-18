"""QMind 客户端 — 通过 qmind CLI 对接 Qoder 云端知识库(notebook).

QMind 是 Qoder 的知识库能力,以「笔记本(notebook)」为单位。它与本系统之间不是
自建 HTTP 服务,而是通过 qoder 生态里的 `qmind` 命令行交互(与 qmind 技能用法一致):

- 二进制按需下载到固定缓存目录 `~/.cache/qmind/bin/qmind-<os>-<arch>`;
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
from pathlib import Path

logger = logging.getLogger(__name__)

_CLI_TIMEOUT = 60
_OSS_BASE = "https://qoder-ide-cn.oss-accelerate.aliyuncs.com/qmind/cli"


# ── CLI 解析 / 按需下载 ──────────────────────────────────────────────

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


def qmind_bin_path() -> Path:
    """qmind 二进制路径:优先 QMIND_BIN 环境变量,否则固定缓存目录."""
    override = os.environ.get("QMIND_BIN")
    if override:
        return Path(override).expanduser()
    os_name, arch, ext = _platform_slug()
    return Path.home() / ".cache" / "qmind" / "bin" / f"qmind-{os_name}-{arch}{ext}"


def ensure_qmind_cli() -> Path | None:
    """确保 qmind CLI 可用;缺失时从公共 OSS 下载(与技能脚本一致)."""
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


def _run_cli(args: list[str]) -> dict | None:
    """执行 qmind CLI 并解析 JSON 输出;失败返回 None."""
    bin_path = ensure_qmind_cli()
    if not bin_path:
        return None
    try:
        proc = subprocess.run(
            [str(bin_path), *args],
            capture_output=True, text=True, timeout=_CLI_TIMEOUT,
            env=_cli_env(),
        )
        if proc.returncode != 0:
            logger.warning("[qmind] `%s` failed (rc=%s): %s",
                           " ".join(args), proc.returncode, (proc.stderr or "").strip()[:300])
            return None
        return json.loads(proc.stdout or "{}")
    except subprocess.TimeoutExpired:
        logger.warning("[qmind] `%s` timed out", " ".join(args))
        return None
    except FileNotFoundError:
        logger.warning("[qmind] CLI not found at %s", bin_path)
        return None
    except json.JSONDecodeError as e:
        logger.warning("[qmind] non-JSON output: %s", e)
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning("[qmind] run error: %s", e)
        return None


# ── 知识库(notebook)列举 ────────────────────────────────────────────

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

    try:
        ids = [int(x) for x in (kb_ids or []) if str(x).strip().isdigit()]
    except (TypeError, ValueError):
        ids = []
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


def qmind_retrieve(
    question: str,
    datasource_id: int = 0,
    kb_ids: list | None = None,
    top_k: int = 5,
) -> dict:
    """默认 QMind 检索入口:优先命中的绑定知识库经 qmind CLI 检索,否则回退 graphrag.

    Args:
        question: 用户问题。
        datasource_id: 回退 graphrag 时按数据源过滤元数据。
        kb_ids: Waker 绑定的知识库 ID;指定时仅在这些库中检索。
        top_k: 检索条数(可被知识库 source_config.top_k 覆盖)。

    Returns:
        QMind 命中: {chunks, count, rag_source='qmind', knowledge_base, notebook_id};
        否则 graphrag 统一结果 dict。
    """
    kbs = _bound_qmind_kbs(kb_ids)
    for kb in kbs:
        k = int(kb["cfg"].get("top_k") or top_k)
        chunks = retrieve_notebook(kb["notebook_id"], question, k)
        if chunks:
            logger.info("[qmind] kb=%s notebook=%s retrieved %d chunks",
                        kb.get("name"), kb["notebook_id"], len(chunks))
            return {
                "chunks": chunks,
                "count": len(chunks),
                "rag_source": "qmind",
                "knowledge_base": kb.get("name"),
                "notebook_id": kb["notebook_id"],
            }
    # 无命中(未绑定 / CLI 不可用 / 无结果)→ 回退,行为与原实现一致
    return _fallback_graphrag(question, datasource_id, tagged=bool(kbs))
