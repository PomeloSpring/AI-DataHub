"""QMind 客户端 — 通过 qmind CLI 对接 Qoder 云端知识库(notebook).

QMind 是 Qoder 的知识库能力,以「笔记本(notebook)」为单位。它与本系统之间不是
自建 HTTP 服务,而是通过 qoder 生态里的 `qmind` 命令行交互(与 qmind 技能用法一致)。

运行时口径(qodercn 独立工作空间, 2026-10 切换):
- CLI 为 npm 包 `@qoder-ai/qmind-cli`(Node 版), 缓存于项目 `runtime/qmind-cli/`,
  由项目内 `runtime/node` 驱动; 缺失时 npm 自动缓存安装(或 setup.sh 预装);
- 目标是 **qoder.cn(qodercn)体系**: CLI 用成对 `--sash/--dashboard` flag 指到
  CN 网关(`--env` 只有 Global 的 prod/test/daily, 环境变量形态会被 CLI 拒);
- 凭据/缓存一律落在 `runtime/qmind/home/`(QMIND_HOME), 不读宿主机用户环境;
- `qmind notebook list --format json`  → 列出(检索)全部 Qoder 知识库;
- `qmind retrieve --nb <id> -q <q> --format json` → 对指定笔记本做知识检索。

对外暴露:
- list_notebooks()            供后台「从 Qoder 检索知识库」列出多个知识库
- retrieve_notebooks(...)     多 notebook **一次联合召回**（云端 rerank），检索通道首选入口
- qmind_retrieve(...)         qmind 通道检索:无命中透明回退既有 graphrag 元数据检索,
                              回退事实与原因必须标注（不静默）

多知识库并存时的「该查哪个」不在这里判：本模块只负责 qmind 这一条通道。跨 provider
分派与跨源归并见 `knowledge_retriever` + `providers/`（通道差异不得泄漏给 LLM）。
"""

import json
import logging
import os
import subprocess
from datetime import datetime
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_CLI_TIMEOUT = 60
# npm 包名与项目内缓存目录(qodercn 口径, 见模块 docstring)
_QMIND_CLI_PKG = "@qoder-ai/qmind-cli"

# ── 简易熔断: 连续失败达阈值后短路一段时间, 避免每次问答都 spawn CLI 子进程 ──
_CB_FAIL_THRESHOLD = 3
_CB_COOLDOWN_SEC = 120
#: 熔断键按 provider 分命名空间。历史缺陷：全局单键 `chatbi:qmind:breaker:*`，
#: 任何一次外部通道抖动都会把 qmind 一起熔掉 120s —— 旁路故障传进了主链路。
_BREAKER_PREFIX = "chatbi:kb:qmind:breaker"


def _breaker_client():
    from backend.common.cache.factory import get_cache
    # 熔断是协调状态，必须使用会抛错的 Redis 原语，不能走 best-effort 缓存接口。
    return get_cache("qmind_breaker")._get_redis()


def _breaker_allowed(now: float = None) -> bool:
    return not bool(_breaker_client().exists(f"{_BREAKER_PREFIX}:open"))


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
    """, 2, f"{_BREAKER_PREFIX}:fails", f"{_BREAKER_PREFIX}:open",
        "1" if success else "0", _CB_FAIL_THRESHOLD, _CB_COOLDOWN_SEC)


# ── CLI 解析 / 按需安装(qodercn Node 版) ────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parents[4]  # → 仓库根
_RUNTIME_DIR = _PROJECT_ROOT / "runtime"
_RUNTIME_QMIND_DIR = _RUNTIME_DIR / "qmind"           # HOME/凭据隔离目录
_RUNTIME_QMIND_CLI_DIR = _RUNTIME_DIR / "qmind-cli"   # npm 缓存的 qmind-cli
_NODE_BIN = _RUNTIME_DIR / "node" / "bin" / "node"

# qodercn 目标(CN 网关): 必须用成对 --sash/--dashboard 指定(实测验证);
# CLI 的 --env 只有 Global 的 prod/test/daily, QMIND_ENV 等环境变量形态会被 CLI 拒。
_DEFAULT_SASH = "https://openapi.qoder.com.cn"
_DEFAULT_DASHBOARD = "https://qoder.cn"


def _cn_target() -> tuple[str, str]:
    """解析 qodercn 目标网关(允许环境变量覆盖, 默认 CN)."""
    sash = os.environ.get("QMIND_SASH_TARGET") or _DEFAULT_SASH
    dash = os.environ.get("QMIND_DASHBOARD_TARGET") or _DEFAULT_DASHBOARD
    return sash, dash


def qmind_cli_entry() -> Path:
    """npm 缓存的 qmind-cli 入口(项目内固定路径, 不依赖宿主机)."""
    return (_RUNTIME_QMIND_CLI_DIR / "node_modules" / "@qoder-ai"
            / "qmind-cli" / "bin" / "cli.js")


def qmind_cli_argv(args: list[str]) -> list[str]:
    """完整命令行: node cli.js <args...> --sash <cn-sash> --dashboard <cn-dashboard>."""
    sash, dash = _cn_target()
    return [str(_NODE_BIN), str(qmind_cli_entry()), *args,
            "--sash", sash, "--dashboard", dash]


def ensure_qmind_cli() -> Path | None:
    """确保 npm 版 qmind-cli 就位;缺失时用项目内 npm 缓存安装到 runtime/qmind-cli/.

    也可通过 bash runtime/qmind/setup.sh 预装。安装的是 qodercn(qoder.cn)体系
    的 CLI(CN 网关域名内置于包内, 目标由 qmind_cli_argv 的成对 flag 指定)。
    """
    entry = qmind_cli_entry()
    if entry.is_file():
        return entry
    npm = _RUNTIME_DIR / "node" / "bin" / "npm"
    if not npm.is_file():
        logger.warning("[qmind] npm not found at %s; run bash runtime/qmind/setup.sh", npm)
        return None
    try:
        logger.info("[qmind] caching %s to %s", _QMIND_CLI_PKG, _RUNTIME_QMIND_CLI_DIR)
        subprocess.run(
            [str(npm), "install", "--prefix", str(_RUNTIME_QMIND_CLI_DIR),
             "--no-bin-links", "--no-audit", "--no-fund", "--save=false",
             _QMIND_CLI_PKG],
            check=True, timeout=600,
            env={**os.environ, "npm_config_cache": str(_RUNTIME_DIR / ".npm-cache")},
        )
        return entry if entry.is_file() else None
    except Exception as e:  # noqa: BLE001  安装失败不致命,调用方按空结果处理
        logger.warning("[qmind] npm install %s failed: %s", _QMIND_CLI_PKG, e)
        return None


def _cli_env() -> dict:
    """构造 qmind CLI 子进程环境(运行时独立于宿主机用户环境)。

    HOME/XDG/QMIND_HOME 一律收敛到 runtime/qmind/home: `qmind login` 的
    credentials 与 CLI 缓存都落在项目内, 不读宿主机 ~/.config、~/.cache(旧账号
    credentials/缓存曾与项目凭据串源, 换机器/换用户行为不一致)。PATH 前插项目
    node, 供子进程 shebang 解析。

    CLI 认证: 设置 `QMIND_TOKEN` 为个人 token(`pt-...`,qoder.cn 的 Personal
    Access Token, CLI 自动换取短期 job token)。注意 Node 版 CLI 对自定义
    `--sash/--dashboard` 目标(custom-<hash>)只读 `QMIND_DEBUG_TOKEN`, 故两者同置;
    并置 `QMIND_NON_INTERACTIVE=1` 禁止服务器环境下弹交互登录(fail-loud)。
    服务器/容器无交互登录时,用 `backend/.env` 注入的
    `QODERCN_PERSONAL_ACCESS_TOKEN`/`QODER_PERSONAL_ACCESS_TOKEN` 兜底, 已有
    `QMIND_TOKEN` 时优先保留。
    """
    env = dict(os.environ)
    home = _RUNTIME_QMIND_DIR / "home"
    home.mkdir(parents=True, exist_ok=True)
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["XDG_CACHE_HOME"] = str(home / ".cache")
    env["QMIND_HOME"] = str(home)
    env["QMIND_NON_INTERACTIVE"] = "1"
    node_bin = str(_NODE_BIN.parent)
    if node_bin not in (env.get("PATH") or ""):
        env["PATH"] = node_bin + os.pathsep + (env.get("PATH") or "")
    if not env.get("QMIND_TOKEN"):
        pat = (env.get("QODERCN_PERSONAL_ACCESS_TOKEN")
               or env.get("QODER_PERSONAL_ACCESS_TOKEN", ""))
        if pat:
            env["QMIND_TOKEN"] = pat
    # custom --sash/--dashboard 目标下 CLI 只认 QMIND_DEBUG_TOKEN(见模块 docstring)
    if env.get("QMIND_TOKEN") and not env.get("QMIND_DEBUG_TOKEN"):
        env["QMIND_DEBUG_TOKEN"] = env["QMIND_TOKEN"]
    return env


def _extract_err(stdout: str, stderr: str) -> str:
    """提取 CLI 错因: Node 版错误以 JSON 打在 stdout(rc=1, stderr 常为空),
    兼顾旧版 stderr 文本形态。JSON 形如 {"error": {"code": ..., "message": ...}}."""
    out = (stdout or "").strip()
    try:
        err = json.loads(out or "{}").get("error")
        if isinstance(err, dict):
            code = (err.get("code") or "").strip()
            msg = (err.get("message") or "").strip()
            return (f"{code}: {msg}" if code and msg else code or msg)[:300]
        if isinstance(err, str) and err.strip():
            return err.strip()[:300]
    except (json.JSONDecodeError, AttributeError):
        pass
    return (out or (stderr or "").strip())[:300] or "rc 非 0 且无错误输出"


def _run_cli_ex(args: list[str]) -> tuple[dict | None, str]:
    """执行 qmind CLI 并解析 JSON 输出; 返回 (data, err). 失败时 data=None 并计入熔断."""
    bin_path = ensure_qmind_cli()
    if not bin_path:
        _breaker_record(False)
        return None, "qmind CLI 不可用"
    try:
        proc = subprocess.run(
            qmind_cli_argv(args),
            capture_output=True, text=True, timeout=_CLI_TIMEOUT,
            env=_cli_env(),
        )
        if proc.returncode != 0:
            err = _extract_err(proc.stdout, proc.stderr)
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


# ── source（知识库里的目录与文档）级调用 ──────────────────────────────
#
# 与检索路径的两点差别，都必须在这里说清：
# 1. **失败外抛，不回空清单**。检索路径可以降级（聊天链路有显式 degraded 标注），
#    但目录树回 `[]` 会被用户读成"云端知识库一篇文档都没有"，那是假事实。
# 2. **不动熔断计数**。熔断保护的是聊天热路径（CLI 慢/挂时快速跳过）；后台一次
#    手工编辑失败不该把 AS-BOT 的检索熔掉 120s —— 那是把旁路故障传进主链路。

class QMindCliError(RuntimeError):
    """CLI 调用失败。``retryable=False`` 表示属"当前凭据/参数不行"，重试无意义。"""

    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


def _run_cli_checked(args: list[str]) -> dict:
    data, err = _run_cli_ex(args)
    if data is None:
        raise QMindCliError(err or "qmind CLI 调用失败",
                            retryable=not is_unavailable_error(err or "")
                            and "AUTH_REQUIRED" not in (err or ""))
    return data


def _run_cli_raw(args: list[str]) -> str:
    """执行 CLI 返回 stdout 原文（``source content`` 只出纯文本，不吃 --format json）。"""
    bin_path = ensure_qmind_cli()
    if not bin_path:
        raise QMindCliError("qmind CLI 不可用")
    try:
        proc = subprocess.run(
            qmind_cli_argv(args),
            capture_output=True, text=True, timeout=_CLI_TIMEOUT, env=_cli_env(),
        )
    except subprocess.TimeoutExpired as e:
        raise QMindCliError("qmind CLI 超时，请稍后重试") from e
    except FileNotFoundError as e:
        raise QMindCliError("qmind CLI 未安装（运行 bash runtime/qmind/setup.sh）") from e
    if proc.returncode != 0:
        err = _extract_err(proc.stdout, proc.stderr)
        logger.warning("[qmind] `%s` failed (rc=%s): %s", " ".join(args), proc.returncode, err)
        raise QMindCliError(err or "qmind CLI 调用失败",
                           retryable=not is_unavailable_error(err or "")
                           and "AUTH_REQUIRED" not in (err or ""))
    return proc.stdout or ""


def list_sources(notebook_id: str) -> list[dict]:
    """知识库（notebook）里的全部条目：目录与文档，按 --all 拉全分页。

    返回项关键字段：``id / title / isDir / parentSourceId / path / sourceType /
    status / updatedAt / errorMessage``。**失败外抛**，不返回空列表。
    """
    data = _run_cli_checked(["source", "list", "--nb", notebook_id, "--format", "json", "--all"])
    items = data.get("sources")
    if not isinstance(items, list):
        raise QMindCliError("qmind source list 返回形状不对（缺 sources 数组）")
    return items


def read_source_content(notebook_id: str, source_id: str) -> str:
    """读解析后的正文（markdown 文本）。空内容返回空串 —— 由调用方判"文档是空的"。"""
    return _run_cli_raw(["source", "content", str(source_id), "--nb", notebook_id])


def upload_source(notebook_id: str, local_path: str, title: str,
                  *, parent_id: str = "", path: str = "", source_type: str = "markdown") -> str:
    """上传本地文件为新 source，返回新 source_id。

    云端没有"原地改正文"的接口（CLI 的目录同步也是"传新件 → 删旧件"两步），
    所以保存 = 新增 + 删除旧件；调用方必须处理"新件已建、旧件删失败"的重复态。
    """
    args = ["source", "upload", "--nb", notebook_id, "--file", str(local_path),
            "--title", title, "--format", "json"]
    if source_type:
        args += ["--type", source_type]
    if parent_id:
        args += ["--parent", parent_id]
    if path:
        args += ["--path", path]
    data = _run_cli_checked(args)
    new_id = data.get("id") or (data.get("source") or {}).get("id")
    if not new_id:
        raise QMindCliError("qmind source upload 未返回新 source id")
    return str(new_id)


def create_source_dir(notebook_id: str, title: str, *, parent_id: str = "") -> str:
    args = ["source", "mkdir", "--nb", notebook_id, "--title", title, "--format", "json"]
    if parent_id:
        args += ["--parent", parent_id]
    data = _run_cli_checked(args)
    new_id = data.get("id") or (data.get("source") or {}).get("id")
    if not new_id:
        raise QMindCliError("qmind source mkdir 未返回新 source id")
    return str(new_id)


def source_node(s: dict) -> dict:
    """云端 source → 业务可读节点(其余字段是内部标识, 不外发)。

    刻意**不**带 ``uri`` / ``originUrl``(对象存储路径与带签名的下载链接)、``orgId`` /
    ``userId`` / ``notebookId`` —— 护栏 §7: 内部标识不进响应体, 带签名的 URL 更不行。
    """
    return {
        "source_id": str(s.get("id") or ""),
        "title": str(s.get("title") or ""),
        "is_dir": bool(s.get("isDir")),
        "parent_id": str(s.get("parentSourceId") or ""),
        "path": str(s.get("path") or ""),
        "source_type": str(s.get("sourceType") or ""),
        "status": str(s.get("status") or ""),
        "error_message": str(s.get("errorMessage") or ""),
        "updated_at": _fmt_ts(s.get("updatedAt")),
    }


def _fmt_ts(value) -> str:
    """CLI 的时间戳是 {seconds, nanos}; 兼容已解析成字符串的情形。"""
    if isinstance(value, dict):
        secs = int(value.get("seconds") or 0)
        return datetime.fromtimestamp(secs).strftime("%Y-%m-%d %H:%M:%S") if secs else ""
    return str(value or "")


def build_source_tree(items: list[dict]) -> tuple[list[dict], int, int, bool]:
    """扁平 source 清单 → 目录树。返回 (roots, 文档数, 目录数, 是否扁平库)。

    父不存在(或父没出现在清单里)的条目挂到根并打 ``orphan`` 标记 —— 宁可层级看起来
    保守, 也不能让文档从树上凭空消失(消失 = 用户以为库里没有, 那是假事实)。
    """
    nodes = [source_node(s) for s in items if s.get("id")]
    by_id = {n["source_id"]: {**n, "children": []} for n in nodes}
    roots: list[dict] = []
    for n in nodes:
        node = by_id[n["source_id"]]
        pid = n["parent_id"]
        if pid and pid in by_id:
            by_id[pid]["children"].append(node)
        else:
            if pid:
                node["orphan"] = True
            roots.append(node)
    _sort_tree(roots)
    docs = [n for n in nodes if not n["is_dir"]]
    folders = [n for n in nodes if n["is_dir"]]
    return roots, len(docs), len(folders), all(not n["parent_id"] for n in nodes)


def _sort_tree(nodes: list[dict]) -> None:
    """目录在前、同类按名称; 递归排序(展示层稳定, 不靠云端返回顺序)。"""
    nodes.sort(key=lambda n: (not n["is_dir"], n["title"].lower()))
    for n in nodes:
        if n.get("children"):
            _sort_tree(n["children"])


def delete_source(notebook_id: str, source_id: str) -> None:
    """删除 source（目录级联需 --force：无交互终端，确认只能由调用方的门禁来给）。"""
    _run_cli_raw(["source", "delete", str(source_id), "--nb", notebook_id, "--force"])


# ── 知识库(notebook)列举 ────────────────────────────────────────────


def is_unavailable_error(err: str) -> bool:
    """判定错误属于「当前凭据对目标 notebook 不可用」(无权/不存在, 如切换账号)。

    仅此类错误允许触发 notebook 自动重建; 网络抖动/服务端 5xx 不在此列,
    避免故障期间误建一堆重复知识库。凭据类(AUTH_REQUIRED)也不在此列 ——
    那是 token 无效, 重建 notebook 无意义。
    """
    e = (err or "").lower()
    return ("insufficient notebook permission" in e
            or "forbidden" in e
            or "not found" in e
            or "not_found" in e)


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
            qmind_cli_argv(["notebook", "get", notebook_id]),
            capture_output=True, text=True, timeout=_CLI_TIMEOUT,
            env=_cli_env(),
        )
        if proc.returncode == 0:
            return True, ""
        return False, _extract_err(proc.stdout, proc.stderr)
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:300]


def create_notebook(title: str, description: str = "") -> str | None:
    """在当前凭据账号下新建 notebook, 返回新 notebook_id(失败返回 None)。"""
    args = ["notebook", "create", "--title", title, "--format", "json"]
    if description:
        args += ["--desc", description]
    data, err = _run_cli_ex(args)
    if not data:
        logger.warning("[qmind] notebook create failed: %s", err)
        return None
    return data.get("id") or data.get("notebook_id") or (data.get("notebook") or {}).get("id")


def list_notebooks() -> list[dict]:
    """检索 Qoder 云端全部 QMind 知识库(笔记本).

    Returns: [{notebook_id, title, org_id, description, status}] —— 失败/无凭据时返回 []。
    """
    data = _run_cli(["notebook", "list", "--format", "json"])
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

#: qmind CLI 单次调用最多带 5 个 notebook（`retrieve --help` 原文：maximum 5）；
#: 超出按批拆分，**批间不做跨批排序**（排序口径只留上层归并一处，避免两套口径飘）。
_NB_BATCH = 5


def _norm_chunk(item: dict) -> dict:
    """CLI chunk → 平台归一形状（标题字段与黑盒都在这一处收口）。

    历史缺陷：原先取 `item.get("title") or item.get("source")`，而 CLI 实际字段是
    `sourceTitle` / `notebookTitle` —— 两个键都不存在，title 恒为空串，于是
    `knowledge-qa` 剧本要求的「每条结论标来源」在数据层就拿不到出处。

    黑盒（护栏 §7）：`originUrl` / `sourceUri` 是对象存储路径与带签名链接，一律不取；
    内部定位 id 以 `_` 前缀留在服务端，对外前由 `_drop_internal` 剥除。
    """
    text = str(item.get("content") or item.get("text") or item.get("answer") or "")
    if not text:
        return {}
    out = {
        "content": text,
        "title": str(item.get("sourceTitle") or item.get("title")
                     or item.get("notebookTitle") or "").strip(),
    }
    score = item.get("score")
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        out["score"] = round(float(score), 4)
    nb = item.get("notebookId") or item.get("notebook_id")
    if nb:
        out["_notebook_id"] = str(nb)
    src = item.get("sourceId") or item.get("source_id")
    if src:
        out["_source_id"] = str(src)
    return out


def _drop_internal(chunk: dict) -> dict:
    """剥除 `_` 前缀的内部定位键：notebook/source id 不进 LLM 视野也不进响应体。"""
    return {k: v for k, v in chunk.items() if not str(k).startswith("_")}


def _norm_citation(item: dict) -> dict:
    """云端 citations → 只取可读标题（按字段白名单取，URL/内部 id 一律不透出）。"""
    title = str(item.get("sourceTitle") or item.get("title") or item.get("name") or "").strip()
    if not title:
        return {}
    out = {"title": title}
    nb = str(item.get("notebookTitle") or "").strip()
    if nb:
        out["knowledge_base"] = nb
    return out


def retrieve_notebooks(notebook_ids: list, question: str, top_k: int = 5) -> dict:
    """一次调用问多个笔记本。返回 `{chunks, citations, errors}`。

    CLI 原生支持 `--nb a,b,c` 的**跨库联合召回 + 服务端 rerank**（实测返回体
    `reranked: true`，每条 chunk 带 `notebookId`/`notebookTitle` 可归属）。历史实现把它
    拆成 Python 侧逐库单问 + first-hit-wins，等于丢弃后续库的结果，并多付 N-1 次
    node 子进程启动（实测每次 ~1.1-1.5s）—— 本函数是收敛点。

    **失败与「库里确实没有」必须可区分**：调用失败原因进 `errors`，不静默当成空结果
    （no-silent-degradation §1）。
    """
    ids = [str(x).strip() for x in (notebook_ids or []) if str(x).strip()]
    if not ids:
        return {"chunks": [], "citations": [], "errors": []}
    limit = max(1, int(top_k))
    chunks: list[dict] = []
    citations: list[dict] = []
    errors: list[str] = []
    for i in range(0, len(ids), _NB_BATCH):
        batch = ids[i:i + _NB_BATCH]
        data, err = _run_cli_ex(["retrieve", "--nb", ",".join(batch), "-q", question,
                                 "--max-results", str(limit), "--format", "json"])
        if data is None:
            errors.append(err or "qmind retrieve 失败")
            continue
        raw = data.get("chunks") or data.get("results") or data.get("data") or []
        for item in raw:
            if isinstance(item, dict):
                c = _norm_chunk(item)
                if c:
                    chunks.append(c)
        for cit in data.get("citations") or []:
            if isinstance(cit, dict):
                n = _norm_citation(cit)
                if n:
                    citations.append(n)
    # 同一次调用的分是同一份 rerank 输出，可直接比；多批时分不可跨批比，此时
    # 保持 CLI 返回序（不拿不可比的分强行排序）。
    if len(ids) <= _NB_BATCH and all("score" in c for c in chunks):
        chunks.sort(key=lambda c: -float(c["score"]))
    return {"chunks": chunks, "citations": citations[:limit], "errors": errors}


def retrieve_notebook(notebook_id: str, question: str, top_k: int = 5) -> list[dict]:
    """对单个笔记本检索，返回标准化 chunk 列表（失败返回 []）。

    保留单库入口是给「已知只问一个库」的调用方（如设计任务 strict 路径）；
    多库场景请直接用 `retrieve_notebooks`，不要在这里循环单库调用。
    """
    return retrieve_notebooks([notebook_id], question, top_k)["chunks"]


def retrieve_notebook_strict(notebook_id: str, question: str, top_k: int = 5) -> list[dict]:
    """设计任务检索：失败与无命中严格区分，不执行任何回退。

    归一形状与主链路同源（`_norm_chunk`）：历史这里也取 `title`/`source` 两个不存在的键，
    标题恒为空 → 设计侧「无版本戳时按文档名核验来源」的分支永远进不去，
    只能把可核验的平台同步文档报成 `unverified_source`。
    """
    if not _breaker_allowed():
        raise RuntimeError("知识库熔断中，请稍后重试")
    data = _run_cli(["retrieve", "--nb", notebook_id, "-q", question,
                     "--max-results", str(max(1, int(top_k))), "--format", "json"])
    if data is None:
        raise RuntimeError("业务知识库检索失败")
    raw = data.get("chunks") or data.get("results") or data.get("data") or []
    if not isinstance(raw, list):
        raise RuntimeError("知识库结果格式不受支持")
    out = []
    for item in raw[: max(1, int(top_k))]:
        if not isinstance(item, dict):
            continue
        c = _norm_chunk(item)
        if c:
            out.append({k: v for k, v in _drop_internal(c).items() if k in ("title", "content")})
    return out


def _parse_cfg(value) -> dict:
    if isinstance(value, dict):
        return value
    if value in (None, ""):
        return {}
    try:
        return json.loads(value)
    except Exception:
        return {}


def _load_qmind_kbs(ids: list[int]) -> list[dict]:
    """回源查库解析 notebook 定位（不吞异常，也不做缓存；缓存由 _bound_qmind_kbs 负责）。"""
    from backend.common.db import execute_query

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


def _bound_qmind_kbs(kb_ids: list | None) -> list[dict]:
    """加载启用的 QMind 知识库(带 notebook_id);kb_ids 指定时按绑定范围过滤.

    id → notebook 定位走 `as_bot_config_cache` 的 **live 档**（默认 5s）：历史上一次
    `knowledge_search` 带 N 个问法就各自回元库查一遍 `source_config`（实测 0.17–0.71s/问，
    纯重复，且并发也各自付一次往返）——同一份清单在一次工具调用内被查 N 次没有任何价值。

    边界（必须分清，否则就是拿缓存弱化裁决）：本缓存只覆盖“往哪个 notebook 问”这一层映射，
    **不是授权/可用性裁决**：知识库是否仍启用、绑定是否越界由未缓存的
    `resource_guard.validate_knowledge_binding` 每次实读 fail-loud 判定（chat 路径同 handler
    内已在跑）；KB 写入口同批 invalidate，所以陈旧窗口上限就是一个 live TTL。

    查库异常不被缓存成空集：工厂函数里异常直接上抛（get_or_set 遇异常不写缓存），外层
    才转成告警 + 空清单（保持旧行为“不毁掉检索”，但不会把一次瞬时故障固化 5s）。
    返回值为缓存内对象引用，调用方**不得原地修改**（现调用方均为只读）。
    """
    from backend.common import as_bot_config_cache as config_cache

    if kb_ids is not None and not kb_ids:
        return []
    try:
        ids = [int(x) for x in (kb_ids or [])]
        if any(x <= 0 for x in ids):
            raise ValueError("知识库标识无效")
    except (TypeError, ValueError) as exc:
        raise ValueError("知识库授权列表无效") from exc
    # key 不去排序：与 KEY_KB 同一纪律（集合同序则复用，书写顺序不同那么归序会静默改变消费顺序）；
    # 实际调用方的清单来自 AS-BOT 存储的固定数组，同一绑定永远同序。
    key = f"{config_cache.KEY_KB_NOTEBOOK}" + (",".join(str(x) for x in ids) if ids else "all")
    try:
        return config_cache.get_or_set(key, lambda: _load_qmind_kbs(ids), live=True)
    except Exception as e:  # noqa: BLE001 — 不静默：回源失败只告警不毁检索，但不写缓存
        logger.warning("[qmind] load knowledge bases failed: %s", e)
        return []


def _fallback_graphrag(question: str, datasource_id: int, tagged: bool) -> dict:
    """回退到既有 graphrag 元数据检索策略(行为不变)."""
    from backend.modules.mind.rag.strategies import get_strategy

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
        from backend.modules.catalog.services.ontology_kb_sync import current_active_version
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
    """qmind 通道检索：一次联合召回全部绑定库，无命中时透明回退本地元数据。

    与历史实现的两处行为差别都是**修缺陷**，不是改设计：

    1. **不再 first-hit-wins**。旧循环逐库单问，第一个有命中的库直接返回、后面的库永远
       不被问；而那个“第一”来自一条**没有 ORDER BY** 的 SQL 的返回序（实践=kb id 升序），
       等于绑定顺序决定答案来源，用户既看不到也调不了。现在把绑定库一次性交给
       云端联合召回（实测返回体 `reranked: true`）。
    2. **回退必须可诊断**。旧实现只在“确有 qmind 库被问过”时才标注回退
       （tagged=bool(kbs)），于是“绑了库但没有已接入通道”会静默回退本地元数据且不置
       degraded —— 用户视角是正常知识答案，实际是元数据投影（no-silent-degradation §1）。

    本函数同时是写侧链路（本体建模取业务上下文）的检索入口，签名保持稳定；
    跨 provider 的聊天主路请走 `knowledge_retriever.knowledge_retrieve`。

    Args:
        question: 用户问题。
        datasource_id: 回退 graphrag 时按数据源过滤元数据，并用于新鲜度比对。
        kb_ids: AS-BOT 绑定的知识库 ID；指定时仅在这些库中检索。
        top_k: 检索条数（可被知识库 source_config.top_k 覆盖）。
        system_scope: 系统能力形态标记（能力叠加）。命中/回退行为与业务一致，
            仅在结果中标注 ``system_scope=True`` 供分桶归因；系统运营问题应走 system_* 工具，
            不拿业务知识充数由 prompt 引导承担（不再是检索硬限定）。

    Returns:
        命中: {chunks, count, rag_source='qmind', knowledge_bases, citations,
              hit_object_keys, doc_stale, doc_version}；部分批次失败时额外带 degraded。
        未命中: graphrag 统一结果 dict，按事实标注 rag_source/degraded/degraded_reason。
        黑盒：notebook_id / source_id / 签名 URL 一律不进返回体（护栏 §7）。
    """
    def _mark_system(result) -> dict:
        out = dict(result or {})
        if system_scope:
            out["system_scope"] = True
        return out

    def _fallback(reason: str, tagged: bool) -> dict:
        result = _fallback_graphrag(question, datasource_id, tagged=tagged)
        result = _mark_system(result)
        if reason:
            result["degraded"] = True
            result["degraded_reason"] = reason
        return result

    kbs = _bound_qmind_kbs(kb_ids)
    nb_ids = [str(k.get("notebook_id") or "").strip() for k in kbs if k.get("notebook_id")]
    if not nb_ids:
        # 绑了库但没有可问的 qmind 通道：回退可以，但必须把“没问成”这件事说出去
        # （tagged 看的是“本轮有没有绑定”，不是“有没有可问的库”——否则回退标签与
        # degraded 事实不一致，评测分桶会把降级当成正常命中）
        reason = ("" if not kb_ids else
                  "绑定的知识库没有可检索的 qmind 通道（类型不是 qmind 或未导入 notebook）")
        return _fallback(reason, tagged=bool(kbs or kb_ids))
    # 熔断开启且确有可问的库: 短路回退本地 hybrid, 不逐次 spawn CLI（标注降级）。
    if not _breaker_allowed():
        result = _fallback("qmind 知识库熔断中（连续失败已短路），本次未发起云端检索",
                           tagged=True)
        result["rag_source"] = f"{result.get('rag_source', 'graphrag')}|qmind_circuit_open"
        return result

    # 各库 top_k 取最大值作为联合召回上限：合并后无法再按库分配额度，
    # 取最大值是保守做法（宁可多召回后截，不能让某个库拿不到名额）。
    limit = max([int(k["cfg"].get("top_k") or top_k) for k in kbs] or [int(top_k)])
    got = retrieve_notebooks(nb_ids, question, limit)
    chunks = got["chunks"]
    if chunks:
        logger.info("[qmind] kb=%s notebooks=%d retrieved %d chunks (batch_errors=%d)",
                    ",".join(str(k.get("name") or "") for k in kbs), len(nb_ids),
                    len(chunks), len(got["errors"]))
        fresh = _evaluate_doc_freshness(datasource_id, chunks)
        out = {
            "chunks": [_drop_internal(c) for c in chunks],
            "count": len(chunks),
            "rag_source": "qmind",
            # 联合召回后答案可能来自多个库，单数 knowledge_base 会误导“哪个库说的”
            "knowledge_bases": [str(k.get("name") or "") for k in kbs if k.get("name")],
            "citations": got["citations"],
            "hit_object_keys": extract_object_keys(chunks),
            **fresh,
        }
        if got["errors"]:
            out["degraded"] = True
            out["degraded_reason"] = f"部分知识库批次检索失败: {'; '.join(got['errors'])[:200]}"
        return out
    if got["errors"]:
        # 全批失败 ≠ 库里没有：回退可以，但必须说是因为失败才回退
        return _fallback(f"qmind 检索失败: {'; '.join(got['errors'])[:200]}", tagged=True)
    # 云端问过且确实无结果 → 回退本地元数据，以 rag_source 标注来源（可区分可诊断）。
    return _fallback("", tagged=True)
