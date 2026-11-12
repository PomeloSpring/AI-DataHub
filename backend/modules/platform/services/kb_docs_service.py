"""知识库文档（目录树）服务 — 查看/编辑 Qoder 云端知识库(qmind notebook)里的文档。

边界(用户 2026-11-04 拍板):
* 知识库**管理**(建库、绑 AS-BOT、类型与凭据配置)仍在系统配置
  (``/api/knowledge-bases*``, ``kb:read``/``kb:manage``), 这里一行都不动;
* 本服务只做**单个知识库内部的文档**: 以目录树查看与编辑, 入口在内容中心
  (``/api/content/knowledge-bases*``, ``kbdoc:read``/``kbdoc:write``);
* 知识库是**一个目录**(notebook 里的 source 树), 不是单个文件 —— 因此树按
  ``parentSourceId`` 组装, 扁平库自然呈现为一层, 不假造层级;
* 语料与门户内容是两件事: 这里改的是 AS-BOT 检索的语料, **不走内容审核流**,
  但每次写入都落审计(谁在何时改了哪篇云端文档)。

三条实现口径:
1. **只提交修改的部分**。云端没有"原地改正文"的接口(CLI 自己同步目录也是
   "传新件 → 删旧件"两步), 所以保存一篇 = upload + delete old, 其余文档一律不碰 ——
   不做整库重传(那会把没改的文档也换 id、重跑解析)。
2. **派生物不许改**。本体上云文档由 ``ontology_kb_sync`` 推送并带
   ``model-version-stamp`` 头, 在这里改它, 下次同步就被覆盖(ontology-modeling §1:
   严禁独立编辑派生物)。读取时检测、写入前再检测一次, 一律拒并指回本体建模页。
3. **失败即暴露**。CLI 任何失败原样抛可读错因(含"当前凭据对这个库不可用"),
   不回空树、不假装保存成功(no-silent-degradation)。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from datetime import datetime, date

from backend.common.db import execute_query

logger = logging.getLogger(__name__)

# 本体上云文档的版本戳(qmind_retriever._evaluate_doc_freshness 读的是同一个头)
_STAMP_RE = re.compile(r"model-version-stamp\s+model_id=(\S+)\s+model_version=(\S+)")

# 云端语料的硬阻断类别: 只拦"真凭据/连接串"。物理表名、生成 SQL 对内部语料是
# 正常内容(本体文档本来就讲口径), 照门户那套硬拦会把可用语料整体拒掉。
_WRITE_HARD_LEAKS = {"connection_string", "credential_in_url", "sensitive_field"}
_WRITE_WARN_LEAKS = {"generated_sql", "raw_db_error", "error_stack", "legacy_leak"}
_LEAK_LABELS = {
    "sensitive_field": "内部键名（datasource_id / host / password 等）",
    "connection_string": "数据库连接串",
    "credential_in_url": "URL 里带账号密码",
    "generated_sql": "带库表限定的生成 SQL",
    "error_stack": "程序报错栈",
    "raw_db_error": "数据库原始报错文本",
    "legacy_leak": "内部标识（physical_table / catalog_ref）",
}
MAX_DOC_BYTES = 2 * 1024 * 1024          # 单篇语料上限: 云端解析与页面编辑都吃得消的量级


def _writable_kb_types() -> tuple:
    """可写文档通道 = provider 声明 `capabilities.write` 的那些类型。

    不手抄白名单：手抄的清单必然与通道能力漂移（接百炼时最容易漏的就是这一点，
    把“有意只读”当成“还没实现”就会写错对用户的文案）。
    """
    from backend.modules.mind.rag.providers import PROVIDERS
    return tuple(p.kb_type for p in PROVIDERS if p.capabilities.write)


#: 保留模块常量名（既有调用与用例按属性读），但值从 provider 注册表派生。
class _WritableTypes(tuple):
    def __contains__(self, item):
        return item in _writable_kb_types()


EDITABLE_KB_TYPES = _WritableTypes(_writable_kb_types())


class KbDocError(Exception):
    """对用户可读的业务失败; ``status`` 映射 HTTP 状态码, ``detail`` 原样回前端。"""

    def __init__(self, detail: str, status: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status = status


def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _iso(value) -> str:
    if isinstance(value, dict):                      # CLI 的 updatedAt 形如 {seconds, nanos}
        secs = int(value.get("seconds") or 0)
        if not secs:
            return ""
        return datetime.fromtimestamp(secs).strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d %H:%M:%S") if isinstance(value, datetime) else value.isoformat()
    return str(value or "")


def _qmind_cli():
    """延迟导入: 只有真正走云端时才要求 CLI/Node 就位(导入失败要说清, 不静默)。"""
    from backend.modules.mind.rag import qmind_retriever as qr
    return qr


# ── 知识库定位 ────────────────────────────────────────────────────────────────

def _kb_row(ref: str) -> dict:
    """按业务名定位知识库（重名一律拒: 猜错就是改错库的语料）。"""
    name = str(ref or "").strip()
    if not name:
        raise KbDocError("知识库名称不能为空", 404)
    rows = execute_query(
        "SELECT name, description, kb_type, status, source_config, qmind_notebook_id "
        "FROM adh_knowledge_bases WHERE name=%s", (name,)) or []
    if not rows:
        known = [r["name"] for r in (execute_query("SELECT name FROM adh_knowledge_bases ORDER BY name") or [])]
        raise KbDocError(f"知识库「{name}」不存在。现有知识库：{' / '.join(known) or '（还没登记）'}", 404)
    if len(rows) > 1:
        raise KbDocError(f"有 {len(rows)} 个知识库都叫「{name}」，无法判定改哪一个。"
                         "请先在系统配置「知识库」里改成唯一名称", 409)
    return rows[0]


def _notebook_id(row: dict) -> str:
    """取 notebook 定位。列与 JSON 两处都可能存在（历史导入两条路都走过）。"""
    nb = str(row.get("qmind_notebook_id") or "").strip()
    if not nb:
        cfg = row.get("source_config")
        if isinstance(cfg, str):
            try:
                cfg = json.loads(cfg or "{}")
            except (TypeError, ValueError):
                cfg = {}
        nb = str((cfg or {}).get("notebook_id") or "").strip()
    if not nb:
        raise KbDocError(f"知识库「{row['name']}」还没有绑定云端 notebook，"
                         "请先在系统配置「知识库」里重新从 Qoder 检索导入", 409)
    return nb


def list_bases() -> list[dict]:
    """内容中心可选的知识库清单。

    非 qmind 类型**照样列出来**并标注不支持（藏起来会让用户以为库没了 —— 那是
    把"本批没做"伪装成"没有数据"）。
    """
    rows = execute_query(
        "SELECT name, description, kb_type, status, source_config, qmind_notebook_id "
        "FROM adh_knowledge_bases ORDER BY name") or []
    out = []
    for r in rows:
        nb = str(r.get("qmind_notebook_id") or "").strip()
        if not nb:
            cfg = r.get("source_config")
            if isinstance(cfg, str):
                try:
                    cfg = json.loads(cfg or "{}")
                except (TypeError, ValueError):
                    cfg = {}
            nb = str((cfg or {}).get("notebook_id") or "").strip()
        supports = str(r.get("kb_type") or "") in EDITABLE_KB_TYPES
        reason = ""
        if not supports:
            # 两种“不支持”必须分开说：外部语料是**有意只读**（产品决策），
            # 未接入类型是**本批没做**（待补能力）。混成一句会把决策说成缺陷，
            # 也会让管理员不去配真正需要的那条通道（no-silent-degradation）。
            reason = _unsupported_reason(str(r.get("kb_type") or ""))
        elif not nb:
            reason = "这个库还没有绑定云端 notebook，请先在系统配置「知识库」里导入"
        elif str(r.get("status") or "") != "active":
            reason = f"知识库状态是「{r.get('status')}」，未启用；先在系统配置里启用再编辑"
        out.append({"name": r["name"], "description": r.get("description") or "",
                    "kb_type": r.get("kb_type") or "", "status": r.get("status") or "",
                    "supports_docs": bool(supports and nb), "can_edit": bool(supports and nb and str(r.get("status")) == "active"),
                    "reason": reason})
    return out


# ── 写入能力边界 ──────────────────────────────────────────────

def _unsupported_reason(kb_type: str) -> str:
    """按 provider 能力位生成“为什么不能写”的原文（单一措辞源）。"""
    from backend.modules.mind.rag.providers import provider_for

    provider = provider_for({"kb_type": kb_type})
    if provider is None:
        return f"文档树只支持平台同步的云端知识库（qmind）；「{kb_type}」类型的文档通道本批未接入"
    if not provider.capabilities.write:
        return "该库为外部只读语料（由企业在其源系统维护），平台不向其写入文档"
    if not provider.capabilities.doc_tree:
        return f"「{kb_type}」可写入但文档树通道本批未接入"
    return f"「{kb_type}」类型的文档通道本批未接入"


# ── 目录树 ──────────────────────────────────────────────────────────────────

def _node_out(s: dict) -> dict:
    """节点出参。塑形与建树的唯一实现在 qmind_retriever（两处各写一份必然飘）。"""
    from backend.modules.mind.rag.qmind_retriever import source_node
    return source_node(s)


def tree(kb_name: str) -> dict:
    """知识库目录树（children 嵌套）。

    云端 source 是**扁平列表 + parentSourceId**；本函数按父子组装，父不存在
    （或父没出现在清单里）的条目挂到根 —— 宁可层级看起来保守，也不能让文档
    从树上凭空消失（消失 = 用户以为库里没有）。
    """
    row = _kb_row(kb_name)
    qr = _qmind_cli()
    nb = _notebook_id(row)
    try:
        items = qr.list_sources(nb)
    except qr.QMindCliError as e:
        raise KbDocError(f"读取云端目录失败：{e}", 502) from e
    roots, docs, folders, flat = qr.build_source_tree(items)
    return {"base": {"name": row["name"], "description": row.get("description") or "",
                     "kb_type": row.get("kb_type") or "", "status": row.get("status") or ""},
            "nodes": roots, "doc_count": docs, "folder_count": folders, "flat": flat}


def _find_node(kb_name: str, source_id: str) -> tuple[dict, dict]:
    """定位节点（顺带拿回知识库行）。云端无"按 id 取单条"的稳接口，走清单查一次。"""
    source_id = str(source_id or "").strip()
    if not source_id:
        raise KbDocError("缺少文档标识", 400)
    row = _kb_row(kb_name)
    qr = _qmind_cli()
    try:
        items = qr.list_sources(_notebook_id(row))
    except qr.QMindCliError as e:
        raise KbDocError(f"读取云端目录失败：{e}", 502) from e
    hit = next((s for s in items if str(s.get("id")) == source_id), None)
    if not hit:
        raise KbDocError("这个文档/目录在知识库裡已经不在了（可能刚被改名或删除），请刷新目录树", 404)
    return row, hit


# ── 读写 ──────────────────────────────────────────────────────────────────────

def _derived_reason(content: str) -> str:
    """派生物判定：带本体版本戳的文档由同步链路生成，不许在这里改。"""
    m = _STAMP_RE.search((content or "")[:600])
    if not m:
        return ""
    return (f"这是本体建模自动上云的派生文档（model_id={m.group(1)}，"
            f"版本 {m.group(2)[:19]}）。在这里改会在下次本体同步时被整体覆盖 —— "
            "请去「数据中台 → 本体建模」改模型说明与口径，保存后自动重推云端")


def _corpus_reason(content: str) -> str:
    """内容中心同步生成的语料：人工不许在语料侧改（下次同步会整体覆盖）。

    与 `_derived_reason`（本体派生）是同一条纪律的两个通道，但**必须分开两个函数**：
    自动通道 `save_doc_for_sync` 反过来只认“带本通道戳才允许覆盖”，合成一份会让
    同步链路把自己拦死。不拦人工侧的代价是隐形的：在语料里改好的字，下次同步
    静默被顶掉，没人知道改动去哪了。
    """
    m = _SYNC_STAMP_RE.search((content or "")[:600])
    if not m:
        return ""
    return (f"这是内容中心同步生成的语料（条目 {m.group(1)}，版本 {m.group(2)}）。"
            "在这里改会在下次同步时被整体覆盖 —— 请去「内容中心 → 内容管理」改正文，"
            "发布后重新同步；想让这段文字长期留在语料里，请在知识库里另建一篇")


def _leak_scan(content: str) -> tuple[list[str], list[str]]:
    """写入口的凭据/内部标识扫描（复用出站守卫同一份清单，不另列正则）。

    拿不到清单就当成"未通过"硬拒 —— 与 content_service 同一口径：扫描器不可用
    不等于内容安全。
    """
    try:
        from backend.modules.mind.execution.outbound_guard import find_sensitive_hits
        labels = list(find_sensitive_hits(content or "") or [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("[kb-docs] 内部标识扫描不可用, 按未通过处理: %s", exc)
        return ["内部标识扫描暂不可用（不能当成已检查通过）"], []
    hard = [_LEAK_LABELS.get(l, l) for l in labels if l in _WRITE_HARD_LEAKS]
    warn = [_LEAK_LABELS.get(l, l) for l in labels if l in _WRITE_WARN_LEAKS]
    return hard, warn


def read_doc(kb_name: str, source_id: str) -> dict:
    row, node = _find_node(kb_name, source_id)
    if node.get("isDir"):
        raise KbDocError("这是一个目录，不是文档。点开目录下的文档再编辑", 400)
    qr = _qmind_cli()
    try:
        content = qr.read_source_content(_notebook_id(row), source_id)
    except qr.QMindCliError as e:
        raise KbDocError(f"读取文档正文失败：{e}", 502) from e
    out = _node_out(node)
    out["content"] = content
    out["content_sha256"] = _sha(content)
    out["derived"] = bool(_derived_reason(content) or _corpus_reason(content))
    out["read_only_reason"] = _derived_reason(content) or _corpus_reason(content)
    return out


def save_doc(kb_name: str, source_id: str, content: str, expected_sha256: str, actor: str) -> dict:
    """保存一篇（其余文档不动）。

    云端无原地更新 → upload 新件 + delete 旧件。顺序不能反: 先删再传一旦传失败,
    语料就真没了。反过来最坏是"新旧并存"，可诊断、可清理。
    """
    text = content if isinstance(content, str) else ""
    if not text.strip():
        raise KbDocError("正文是空的：不许用「清空」代替删除（要下线这篇请在树上删掉文档）")
    if len(text.encode("utf-8")) > MAX_DOC_BYTES:
        raise KbDocError(f"单篇不能超过 {MAX_DOC_BYTES // 1024}KB，现在 "
                         f"{len(text.encode('utf-8')) // 1024}KB。请拆分文档")
    hard, warns = _leak_scan(text)
    if hard:
        raise KbDocError("不能写入云端语料：" + "；".join(hard)
                         + "。知识库内容会被 AS-BOT 检索并可能进入回答，凭据与连接串一律不许进语料", 422)
    row, node = _find_node(kb_name, source_id)
    if node.get("isDir"):
        raise KbDocError("这是一个目录，不能按文档保存", 400)
    qr = _qmind_cli()
    nb = _notebook_id(row)
    # 乐观并发：以"打开时读到的那份"为基线，云端当前内容已被别人改过就拒
    try:
        remote = qr.read_source_content(nb, source_id)
    except qr.QMindCliError as e:
        raise KbDocError(f"保存前复核云端内容失败：{e}", 502) from e
    if expected_sha256 and _sha(remote) != str(expected_sha256):
        raise KbDocError("这篇在你编辑期间已被别人改过。请先复制走你的改动、重新加载后再保存", 409)
    # 两类自动生成的语料都不许人工在树上改（本体派生 / 内容同步），否则下次同步静默毁掉改动
    blocked = _derived_reason(remote) or _corpus_reason(remote) or _derived_reason(text)
    if blocked:
        raise KbDocError(blocked, 403)

    title = str(node.get("title") or "").strip() or "untitled.md"
    new_id = _write_temp_and_upload(qr, nb, text, title,
                                    parent_id=str(node.get("parentSourceId") or ""),
                                    path=str(node.get("path") or ""))
    warning = ""
    try:
        qr.delete_source(nb, source_id)
    except qr.QMindCliError as e:
        # 新件已生效，旧件删不掉 = 云端会同时存在两篇（检索会重复命中）。必须说出来
        warning = (f"新版已上传（新文档 id {new_id}），但旧版没能删除（{e}）。"
                   "云端会短暂同时命中新旧两版，请稍后重试保存或在 Qoder 端删掉旧版")
        logger.warning("[kb-docs] 旧件删除失败 kb=%s source=%s: %s", kb_name, source_id, e)
    logger.info("[kb-docs] 保存 kb=%s old=%s new=%s by=%s", kb_name, source_id, new_id, actor)
    out = {"source_id": new_id, "previous_source_id": source_id, "title": title,
           "content_sha256": _sha(text), "warnings": warns,
           "message": ("已提交这一篇（其余文档未改动）。云端要重新解析一遍，"
                       "AS-BOT 检索稍后才用得到新内容" if not warning else "已提交，但有残留需处理")}
    if warning:
        out["warning"] = warning
    if warns:
        out["message"] += f"；扫描提醒：{'；'.join(warns)}（未阻断，请自行确认）"
    return out


def create_doc(kb_name: str, title: str, content: str, parent_id: str, actor: str,
               *, for_sync: bool = False) -> dict:
    """新建一篇文档。

    ``for_sync`` 只给「内容 → 语料」自动通道：它生成的正文本来就带内容版本戳（那是对
    下一轮的归属凭据）。人工通道不传这个开关，所以伪造内容戳新建会被拦 ——
    同一个入口靠调用方自报身份分域，不另开一份实现（两份必飘）。
    """
    name = str(title or "").strip().replace("/", "-").replace("\\", "-")
    if not name:
        raise KbDocError("文档名不能为空")
    if not name.lower().endswith(".md"):
        name += ".md"
    text = content if isinstance(content, str) else ""
    hard, warns = _leak_scan(text)
    if hard:
        raise KbDocError("不能写入云端语料：" + "；".join(hard), 422)
    if _derived_reason(text):
        raise KbDocError("新建文档不许带本体版本戳（那是同步链路的产物标记）", 422)
    if _corpus_reason(text) and not for_sync:
        # 伪造内容戳 = 拿下一篇语料的“归属凭据”，同步通道会把它当自己生成的直接顶掉
        raise KbDocError("新建文档不许带内容同步版本戳（那是同步链路的产物标记）", 422)
    row = _kb_row(kb_name)
    qr = _qmind_cli()
    nb = _notebook_id(row)
    if parent_id:
        _, pnode = _find_node(kb_name, parent_id)
        if not pnode.get("isDir"):
            raise KbDocError("只能在目录下新建文档（选中的父节点是文档）", 400)
    new_id = _write_temp_and_upload(qr, nb, text or f"# {name[:-3]}\n\n", name, parent_id=parent_id)
    logger.info("[kb-docs] 新建 kb=%s title=%s id=%s by=%s", kb_name, name, new_id, actor)
    out = {"source_id": new_id, "title": name, "warnings": warns,
           "message": "已新建文档（云端需重新解析后才可被检索命中）"}
    if warns:
        out["message"] += f"；扫描提醒：{'；'.join(warns)}（未阻断，请自行确认）"
    return out


def create_folder(kb_name: str, title: str, parent_id: str, actor: str) -> dict:
    name = str(title or "").strip().replace("/", "-").replace("\\", "-")
    if not name:
        raise KbDocError("目录名不能为空")
    row = _kb_row(kb_name)
    qr = _qmind_cli()
    if parent_id:
        _, pnode = _find_node(kb_name, parent_id)
        if not pnode.get("isDir"):
            raise KbDocError("只能在目录下再建目录（选中的父节点是文档）", 400)
    try:
        new_id = qr.create_source_dir(_notebook_id(row), name, parent_id=parent_id)
    except qr.QMindCliError as e:
        raise KbDocError(f"新建目录失败：{e}", 502) from e
    logger.info("[kb-docs] 新建目录 kb=%s title=%s id=%s by=%s", kb_name, name, new_id, actor)
    return {"source_id": new_id, "title": name, "message": f"已新建目录「{name}」"}


def _write_temp_and_upload(qr, notebook_id: str, text: str, title: str,
                           *, parent_id: str = "", path: str = "") -> str:
    """落临时文件 → 上传 → 清理临时文件。失败原样外抛（由调用方转可读错因）。"""
    fd, tmp = tempfile.mkstemp(prefix="kbdoc-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o600)
        try:
            return qr.upload_source(notebook_id, tmp, title, parent_id=parent_id, path=path)
        except qr.QMindCliError as e:
            raise KbDocError(f"上传云端失败：{e}", 502) from e
    finally:
        try:
            os.unlink(tmp)
        except OSError as e:
            logger.warning("[kb-docs] 临时文件清理失败 %s: %s", tmp, e)


# ── 供「内容 → 语料」同步通道使用的两个入口 ────────────────────────────
#
# 与人工编辑（save_doc）的唯一差别是**归属判定**：自动通道必须确认“这篇确实是
# 本通道生成的”才允许覆盖或删除 —— 判据是文档头部的 content-version-stamp。
# 人工编辑通道反过来：带戳的派生文档不许在树上改。两边合起来才能既自动又不毁人。
_SYNC_STAMP_RE = re.compile(r"content-version-stamp\s+article=(\S+)\s+revision=(\d+)")


def _sync_stamp(content: str) -> str:
    m = _SYNC_STAMP_RE.search((content or "")[:600])
    return m.group(1) if m else ""


def save_doc_for_sync(kb_name: str, source_id: str, content: str, *, actor: str = "") -> dict:
    """更新一篇由「内容同步」通道生成的语料（传新件 + 删旧件）。

    与 save_doc 的差别：不带 expected_sha256（调用方是发布版正文，不是编辑器基线），
    但**必须**确认云端那篇带本通道版本戳 —— 顶掉人工语料不可接受。
    """
    text = content if isinstance(content, str) else ""
    if not text.strip():
        raise KbDocError("渲染出的语料是空的，不许用「清空」代替删除", 400)
    if len(text.encode("utf-8")) > MAX_DOC_BYTES:
        raise KbDocError(f"单篇不能超过 {MAX_DOC_BYTES // 1024}KB，现在 "
                         f"{len(text.encode('utf-8')) // 1024}KB。请把内容拆成几篇再同步", 400)
    hard, warns = _leak_scan(text)
    if hard:
        raise KbDocError("不能写入云端语料：" + "；".join(hard)
                         + "。知识库内容会被 AS-BOT 检索并可能进入回答，凭据与连接串一律不许进语料", 422)
    row, node = _find_node(kb_name, source_id)
    if node.get("isDir"):
        raise KbDocError("映射指向的是目录，不能按文档更新", 400)
    qr = _qmind_cli()
    nb = _notebook_id(row)
    try:
        remote = qr.read_source_content(nb, source_id)
    except qr.QMindCliError as e:
        raise KbDocError(f"更新前复核云端内容失败：{e}", 502) from e
    if not _sync_stamp(remote):
        raise KbDocError("云端这篇不是内容同步通道生成的（没有版本戳），自动通道不许覆盖人工语料。"
                         "请在「知识库文档」里人工处理，或让内容作者换个文档名", 403)
    if _derived_reason(remote) or _derived_reason(text):
        raise KbDocError(_derived_reason(remote) or _derived_reason(text), 403)

    title = str(node.get("title") or "").strip() or "untitled.md"
    new_id = _write_temp_and_upload(qr, nb, text, title,
                                    parent_id=str(node.get("parentSourceId") or ""),
                                    path=str(node.get("path") or ""))
    warning = ""
    try:
        qr.delete_source(nb, source_id)
    except qr.QMindCliError as e:
        warning = (f"新版已上传，但旧版没能删除（{e}）。云端会短暂同时命中新旧两版，"
                   "请稍后重试同步或在 Qoder 端删掉旧版")
        logger.warning("[kb-docs] 同步旧件删除失败 kb=%s source=%s: %s", kb_name, source_id, e)
    logger.info("[kb-docs] 同步更新 kb=%s old=%s new=%s by=%s", kb_name, source_id, new_id, actor)
    out = {"source_id": new_id, "previous_source_id": source_id, "title": title,
           "content_sha256": _sha(text), "warnings": warns,
           "message": "已更新语料（云端要重新解析一遍才检索得到）"}
    if warning:
        out["warning"] = warning
    return out


def delete_source_for_sync(kb_name: str, source_id: str, *, actor: str = "") -> dict:
    """撤掉本通道在旧库里生成的那篇（换目标知识库时用）。

    只删带本通道版本戳的文档：没戳 = 人工内容，自动通道无权删除。
    """
    row, node = _find_node(kb_name, source_id)
    if node.get("isDir"):
        raise KbDocError("映射指向的是目录，不能删除", 400)
    qr = _qmind_cli()
    nb = _notebook_id(row)
    try:
        remote = qr.read_source_content(nb, source_id)
    except qr.QMindCliError as e:
        raise KbDocError(f"删除前复核云端内容失败：{e}", 502) from e
    if not _sync_stamp(remote):
        raise KbDocError("云端这篇不是内容同步通道生成的，自动通道不许删除人工语料", 403)
    try:
        qr.delete_source(nb, source_id)
    except qr.QMindCliError as e:
        raise KbDocError(f"旧库文档撤销失败：{e}", 502) from e
    logger.info("[kb-docs] 同步撤旧 kb=%s source=%s by=%s", kb_name, source_id, actor)
    return {"deleted": True, "title": str(node.get("title") or "")}
