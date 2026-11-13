"""渲染机文档对象服务 — Chat 交付形态的正文事实源（正文外置、消息内引用）。

为什么要有这一层：` ```chart ` 的正文由模型从查询结果**抄写**进围栏、整份 HTML 报告
原先只能落会话工作区文件，两侧都有同一类缺口 —— 数字不过服务端、报告中心拿不到正文
（`report_service.conversation_artifacts` 只认 `path`）、流式期未闭合的 HTML 会被当
正文逐字念出来。收口办法是把正文变成受管文档对象：渲染机 tool 写入本表，消息里只留
`{"doc_id": "..."}` 引用，前端按引用取正文渲染。

口径边界（不得越界）：
- 本服务**不取数**：取数一律经 `backend.semantics.execute`（护栏 §1），spec 形态的
  章节数据由渲染机在执行口拿到后交这里落库，并带 `row_counts` 对账事实。
- 归属只校验 `user_id`（与会话/设计草稿同一口径）；`workspace_id` 仅作管理归属。
- `message_uuid` 只用于审计与回放定位，**不参与唯一约束**：观测未开启时它是空串，
  当身份用会让同会话第二份报告撞键。
- 体检不过 = 返回可诊断原因，由渲染机原样回给模型改选，**不得**在此静默清洗正文
  （no-silent-degradation §1：静默删外链/截正文会把缺陷藏到用户看到错报告那天）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from typing import Any, Optional

from backend.common.db import execute_query, execute_write

# 正文体积上限：对齐 session_workspace.read_conversation_workspace_text 的现值，
# 换环境要改一并改两处（不同处写死两份阈值必然漂移）。
RENDER_DOC_MAX_CHARS = int(os.getenv("RENDER_DOC_MAX_CHARS", "200_000"))

# 硬编码色值阈值：历史缺陷是报告里 130+ 处写死 hex（与看板两套配色）。
# 少量 hex 允许出现在非配色场景（如打印色/图片数据），超阈值才判"绕过了主题令牌"。
_MAX_INLINE_HEX_COLORS = 24

DOC_KINDS = ("doc", "chart")
BODY_KINDS = ("html", "spec", "chart-data")


class RenderDocumentError(ValueError):
    """文档对象不可用：带 code 让渲染机与前端能区分成因（不是一律"失败请重试"）。"""

    def __init__(self, message: str, code: str = "invalid"):
        super().__init__(message)
        self.message = message
        self.code = code


# ── 落库与读取 ────────────────────────────────────────────────────────────

def save_document(*, conversation_id: int, user_id: int, workspace_id: int = 0,
                  kind: str = "doc", body_kind: str, title: str, body: str,
                  theme: Optional[str] = None, template_key: Optional[str] = None,
                  message_uuid: str = "", doc_id: Optional[str] = None,
                  warnings: Optional[list] = None, row_counts: Optional[dict] = None) -> dict:
    """新建或更新一个文档对象（同 doc_id 再写 = 覆盖正文，供"继续编辑报告"复用）。

    幂等与重复落库：id 由 uuid4 hex 生成，重复调用同一 doc_id 走 UPDATE，不会产生
    第二份正文；不带 doc_id 则视为新建（同轮已有主交付物时由渲染机 G1 硬门先拒）。
    """
    if kind not in DOC_KINDS:
        raise RenderDocumentError(f"不支持的文档类型: {kind}", "invalid_kind")
    if body_kind not in BODY_KINDS:
        raise RenderDocumentError(f"不支持的正文形态: {body_kind}", "invalid_body_kind")
    if int(user_id or 0) <= 0:
        raise RenderDocumentError("文档缺少属主，不能落库", "no_identity")
    text = str(body or "")
    if not text.strip():
        raise RenderDocumentError("正文为空，不能落成文档对象", "empty_body")
    size = len(text)
    if size > RENDER_DOC_MAX_CHARS:
        raise RenderDocumentError(
            f"正文 {size} 字符，超过上限 {RENDER_DOC_MAX_CHARS}：请精简内容或改用结构化交付",
            "too_large")
    digest = hashlib.md5(text.encode("utf-8")).hexdigest()
    new_id = str(doc_id or "").strip() or uuid.uuid4().hex
    now = "UTC_TIMESTAMP(6)"
    params = (new_id, int(conversation_id or 0), (str(message_uuid or "") or None),
              int(user_id), int(workspace_id or 0), kind, body_kind,
              str(title or "")[:255], theme, template_key, text, digest, size,
              json.dumps(list(warnings or []), ensure_ascii=False),
              json.dumps(row_counts or {}, ensure_ascii=False, default=str))
    existing = execute_query("SELECT id FROM adh_render_documents WHERE id=%s",
                             (new_id,), fetchone=True)
    if existing:
        execute_write(
            "UPDATE adh_render_documents SET title=%s, theme=%s, template_key=%s, body=%s, "
            "digest=%s, size_chars=%s, warnings=%s, row_counts=%s, updated_at=" + now + " "
            "WHERE id=%s AND user_id=%s",
            (str(title or "")[:255], theme, template_key, text, digest, size,
             json.dumps(list(warnings or []), ensure_ascii=False),
             json.dumps(row_counts or {}, ensure_ascii=False, default=str),
             new_id, int(user_id)))
    else:
        execute_write(
            "INSERT INTO adh_render_documents (id, conversation_id, message_uuid, user_id, "
            "workspace_id, kind, body_kind, title, theme, template_key, body, digest, size_chars, "
            "warnings, row_counts, created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s," + now + "," + now + ") "
            "ON DUPLICATE KEY UPDATE id=id", params)
    return {"doc_id": new_id, "digest": digest, "size_chars": size,
            "kind": kind, "body_kind": body_kind, "created": not bool(existing)}


def load_document(doc_id: str, *, user_id: int, include_body: bool = True) -> dict:
    """按引用取文档对象；不存在与无权访问**分开报**（前端要给用户三态文案）。

    归属只校验属主本人（与会话/设计草稿同口径）：文档内容已经在本会话展示给属主，
    不再套数据权限快照 —— 但跨属主读取一律拒（防越权看别人的报告正文）。
    """
    ident = str(doc_id or "").strip()
    if not ident:
        raise RenderDocumentError("缺少文档引用 doc_id", "invalid_ref")
    if int(user_id or 0) <= 0:
        raise RenderDocumentError("无可信身份，不能读取文档", "no_identity")
    cols = ("id, user_id, conversation_id, message_uuid, kind, body_kind, title, theme, template_key, "
            "digest, size_chars, warnings, row_counts, updated_at")
    cols += ", body" if include_body else ""
    row = execute_query(f"SELECT {cols} FROM adh_render_documents WHERE id=%s",
                        (ident,), fetchone=True)
    if not row:
        raise RenderDocumentError("文档不存在或已被清理", "not_found")
    if int(row.get("user_id") or 0) != int(user_id):
        raise RenderDocumentError("无权访问该文档", "forbidden")
    out = dict(row)
    out.pop("user_id", None)  # 属主 id 是实现细节，不外泄给调用方（内部标识不下发）
    for ts in ("updated_at",):
        if hasattr(out.get(ts), "isoformat"):
            out[ts] = out[ts].isoformat()
    for jf in ("warnings", "row_counts"):
        raw = out.get(jf)
        if isinstance(raw, str):
            try:
                out[jf] = json.loads(raw or "null")
            except (ValueError, TypeError):
                # 落库侧写坏时不当"没有提示"：标注不可解析，让页面能显式提示
                out[jf] = None
                out[f"{jf}_unreadable"] = True
    return out


def conversation_documents(conversation_id: int, *, user_id: int, kind: str = "") -> list:
    """本会话的文档清单（不含正文）：归档定位与"继续编辑上一份报告"用。"""
    if int(conversation_id or 0) <= 0 or int(user_id or 0) <= 0:
        return []
    sql = ("SELECT id, kind, body_kind, title, theme, template_key, message_uuid, digest, "
           "size_chars, updated_at FROM adh_render_documents "
           "WHERE conversation_id=%s AND user_id=%s")
    params: list = [int(conversation_id), int(user_id)]
    if kind:
        sql += " AND kind=%s"
        params.append(kind)
    sql += " ORDER BY created_at DESC LIMIT 50"
    rows = execute_query(sql, tuple(params)) or []
    for row in rows:
        if hasattr(row.get("updated_at"), "isoformat"):
            row["updated_at"] = row["updated_at"].isoformat()
    return list(rows)


def purge_conversation_documents(conversation_id: int, cur=None) -> int:
    """随会话删除清理文档：只删库行不留孤儿正文（与会话消息同批清除的口径）。"""
    if int(conversation_id or 0) <= 0:
        return 0
    sql = "DELETE FROM adh_render_documents WHERE conversation_id=%s"
    if cur is not None:
        cur.execute(sql, (int(conversation_id),))
        return cur.rowcount
    execute_write(sql, (int(conversation_id),))
    return 0


def purge_workspace_documents(workspace_id: int) -> int:
    """随工作空间删除清理（按空间整片清除，口径同 adh_conversations）。"""
    if int(workspace_id or 0) <= 0:
        return 0
    execute_write("DELETE FROM adh_render_documents WHERE workspace_id=%s", (int(workspace_id),))
    return 0


# ── HTML 体检（五项）：只拒"做不到/会泄露"，不判"该不该用 HTML" ────────────────

# 外部资源引用：单文件自包含是硬约束（禁外链 ⇒ 离线打开必须一致）。
# xmlns 命名空间声明不是远程资源（SVG 规范要求写死 URI），必须放行，否则内联 SVG
# 会被整批判为违规 —— 这是本项最容易误杀的地方。
_NS_URIS = ("http://www.w3.org/1999/", "http://www.w3.org/2000/svg", "http://purl.org/")
_EXT_RESOURCE_RES: tuple[tuple[str, re.Pattern], ...] = (
    ("script_src", re.compile(r"(?i)<script[^>]+\bsrc\s*=\s*[\"']?\s*(?:https?:)?//", re.S)),
    ("link_href", re.compile(r"(?i)<link[^>]+\bhref\s*=\s*[\"']?\s*(?:https?:)?//", re.S)),
    ("remote_src_href", re.compile(r"(?i)<(?:img|iframe|use|image|audio|video|source|embed|object)"
                                   r"[^>]+(?:src|href|xlink:href)\s*=\s*[\"']?\s*(?:https?:)?//", re.S)),
    ("css_import", re.compile(r"(?i)@import\s+(?:url\()?\s*[\"']?\s*(?:https?:)?//")),
    ("css_remote_url", re.compile(r"(?i)url\(\s*[\"']?\s*(?:https?:)?//")),
    ("font_remote", re.compile(r"(?i)@font-face[\s\S]{0,400}?url\(\s*[\"']?\s*(?:https?:)?//")),
)

_RICH_CHART_TYPES = ("china_map", "world_map", "map_heat", "sankey", "tree", "treemap",
                     "radar", "boxplot", "calendar_heatmap", "bar_race", "timeline_play",
                     "word_cloud", "funnel", "chord")

# 伪地图特征：自绘散布圆点冒充底图（历史缺陷：气泡散布 = 用户以为瓦片加载失败）。
# 判据用结构而不是关键词 —— 真内联小图标也用 circle，但不会有"多圆 + 无路径底图"。
_SVG_BLOCK = re.compile(r"(?is)<svg\b[\s\S]*?</svg>")


def _looks_like_pseudo_map(html: str) -> bool:
    for svg in _SVG_BLOCK.findall(html or ""):
        circles = len(re.findall(r"(?i)<circle\b", svg))
        paths = len(re.findall(r"(?i)<path\b", svg))
        if circles >= 6 and paths == 0:
            return True
    return False


def _count_inline_hex_colors(html: str) -> int:
    """统计硬编码 hex 色值出现次数（`:root` 令牌定义本身不算违规，只数引用侧）。"""
    body = re.sub(r"(?is):root\s*\{[^}]*\}", "", html or "")
    return len(re.findall(r"(?i)#[0-9a-f]{6}\b|#[0-9a-f]{3}\b", body))


def validate_report_html(html: str, *, theme: Optional[str] = None,
                         rich_chart_types: Optional[list] = None) -> tuple[list, list]:
    """体检整份 HTML 文档，回 ``(阻断项, 非阻断提示)``；两项都带可读原因与建议出路。

    五项：① 外部资源 ② 伪地图 ③ 主题令牌缺失/硬编码色值 ④ 体积超限 ⑤ 内部标识泄露。
    G2 富图型档位（方案待确认项 1 采纳推荐档）：**伪图已画出 = 拒收**（那是错图，不是
    风格差异）；本轮取到过富图型数据但正文只提了一句没伪图 = 警告放行并标注去向。
    """
    text = str(html or "")
    blockers: list[str] = []
    notes: list[str] = []

    if not text.strip():
        return (["正文为空，不能落成报告文档"], notes)

    # ① 外部资源
    hits: list[str] = []
    for label, pattern in _EXT_RESOURCE_RES:
        m = pattern.search(text)
        if not m:
            continue
        snippet = m.group(0)[:120]
        if any(uri in snippet for uri in _NS_URIS):
            continue
        hits.append(f"{label}: {snippet}")
    if hits:
        blockers.append(
            "HTML 引用了外部资源，离线打开会与生成时不一致（自包含是硬约束）：" + "；".join(hits[:3]) +
            "。请改为内联 SVG/base64，或把需要外部渲染的内容交平台渲染面。")

    # ② 伪地图
    if _looks_like_pseudo_map(text):
        blockers.append(
            "文档里出现了「多圆点、无底图路径」的 SVG，形似地图实为自绘伪图（平台没有内置省界数据，"
            "单文件也拿不到瓦片）。请改用 Top N 表格呈现地域分布，或改用结构化交付（report/spec）"
            "由平台出真底图。")

    # ③ 主题令牌
    if ":root" not in text or "--primary" not in text:
        blockers.append(
            "文档缺少主题令牌块（:root 与 --primary）：配色会退回浏览器默认样式，与看板两套观感。"
            "请注入系统下发的「报告主题令牌」后再提交。")
    else:
        hex_count = _count_inline_hex_colors(text)
        if hex_count > _MAX_INLINE_HEX_COLORS:
            blockers.append(
                f"文档硬编码了 {hex_count} 处色值（阈值 {_MAX_INLINE_HEX_COLORS}），绕过了主题令牌 —— "
                "改主题后这份报告不会跟着变。请只引用 var(--*) 令牌。")
    if not str(theme or "").strip():
        notes.append("未声明主题 id，按查看者当前主题渲染；建议写明实际采用的主题。")

    # ④ 体积
    if len(text) > RENDER_DOC_MAX_CHARS:
        blockers.append(
            f"正文 {len(text)} 字符，超过文档上限 {RENDER_DOC_MAX_CHARS}。"
            "请精简明细或把明细拆成数据文件交付（artifact），不要截断塞进来。")

    # ⑤ 内部标识（护栏 §7）：这道门原先只在归档时有，运行时正文没有 —— 补在入库前。
    hits5 = _sensitive_hits(text)
    if hits5:
        blockers.append(
            "文档正文含有不应下发的系统信息（命中: " + ", ".join(hits5) +
            "）。请改用业务名称重新表述，物理表/连接信息/治理改写后的 SQL 不属于报告内容。")

    # G2：本轮取到过富图型/地理数据，但 HTML 通道呈现不了
    wanted = sorted({str(t or "").strip().lower() for t in (rich_chart_types or []) if t}
                    & set(_RICH_CHART_TYPES))
    if wanted and not any("伪图" in b for b in blockers):
        notes.append(
            "本轮数据适合 " + "、".join(wanted) + " 等图型，HTML 单文件通道拿不到平台底图与这些图型，"
            "相关内容已退为表格；需要真图型请改用结构化交付（report/spec）。")

    return blockers, notes


def find_sensitive_hits(text: str) -> list[str]:
    """出站守卫的公开口（护栏 §7）：渲染机入库前自检正文用，不拼内部实现细节。"""
    return _sensitive_hits(text)


def _sensitive_hits(text: str) -> list[str]:
    """出站守卫清单（viz→mind 延迟 import：顶层横向 import 被分层契约硬禁）。

    清单不可用时**不能当"已检查通过"**：返回一个显式类别，让调用方按未通过处理。
    """
    try:
        from backend.modules.mind.execution.outbound_guard import find_sensitive_hits
        return list(find_sensitive_hits(text) or [])
    except Exception:  # noqa: BLE001
        return ["guard_unavailable"]


def spec_sections(body: Any) -> list:
    """解析 spec 正文的章节清单（容错：坏 JSON 回空列表，调用方按"无章节"处理）。"""
    doc = body
    if isinstance(doc, str):
        try:
            doc = json.loads(doc or "{}")
        except (ValueError, TypeError):
            return []
    if not isinstance(doc, dict):
        return []
    sections = doc.get("sections")
    return list(sections) if isinstance(sections, list) else []


# ── spec 形态：章节正文的渲染与归档快照 ────────────────────────────────
# 为什么需要快照：报告中心存的是“归档那一刻的正文”，而交付对象随会话删除会被清理；
# spec 的渲染面在前端组件里（图表可下钻、主题即时换装），服务端没有浏览器渲染面。
# 因此快照里图表退为表格并写明原因 —— 这是把降级说出来，而不是静默伪图（计划 §4 硬门同口径）。
SPEC_SECTION_KINDS = ("kpi", "chart", "table", "text", "mermaid")
_MAX_SPEC_SECTIONS = 24
_MAX_SNAPSHOT_TABLE_ROWS = 100


def _esc(value: Any) -> str:
    from html import escape
    return escape(str(value if value is not None else ""), quote=True)


def _spec_table(data: dict) -> str:
    """结果集 → HTML 表格（前端图表卡在快照里的等价形态）。"""
    columns = [str(c) for c in ((data or {}).get("columns") or [])]
    raw_rows = (data or {}).get("rows") or []
    if not columns or not raw_rows:
        return '<p class="skeleton-hint">本节无可展示的数据行。</p>'
    rows = raw_rows[:_MAX_SNAPSHOT_TABLE_ROWS]
    head = "".join(f"<th>{_esc(c)}</th>" for c in columns)
    body_lines = []
    for row in rows:
        if isinstance(row, dict):
            cells = [row.get(c) for c in columns]
        elif isinstance(row, (list, tuple)):
            cells = list(row)[:len(columns)]
        else:
            cells = [row]
        body_lines.append("<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in cells) + "</tr>")
    total = _esc((data or {}).get("row_count") or len(raw_rows))
    more = (f'<p class="skeleton-hint">仅展示前 {len(rows)} 行（共 {total} 行）。</p>'
            if len(raw_rows) > len(rows) else "")
    return (f'<table><thead><tr>{head}</tr></thead><tbody>{"".join(body_lines)}</tbody></table>'
            + more)


def render_spec_to_html(body: Any, *, title: str = "", theme: Optional[str] = None,
                        notes: Optional[list] = None) -> str:
    """spec 正文 → 自包含 HTML 快照（归档进报告中心与导出用）。"""
    doc = body
    if isinstance(doc, str):
        try:
            doc = json.loads(doc or "{}")
        except (ValueError, TypeError):
            doc = {}
    if not isinstance(doc, dict):
        doc = {}
    heading = _esc(title or doc.get("title") or "分析报告")
    # 骨架口的 <title> 是占位名（模板预览场景）；归档快照必须换成真标题：
    # 浏览器标签与下载文件名都读它，留着“报告标题”四个字就是存了一份不像样的文件。
    # 样式与报表骨架共用那一处（两份 CSS 必然漂移）；viz 内部同层 import，不开跨层边
    from backend.modules.viz.services.report_service import _skeleton_html_head

    head = [line.replace('<title>报告标题</title>', f'<title>{heading}</title>')
            for line in _skeleton_html_head(theme)]
    parts: list[str] = ['<!DOCTYPE html>', '<html lang="zh-CN">', *head, f'<h1>{heading}</h1>']
    meta = []
    for label, value in (("生成主题", doc.get("theme") or theme or ""),
                         ("模板", doc.get("template_name") or ""),
                         ("章节数", len(spec_sections(doc)) or "")):
        if value:
            meta.append(f"{label}：{_esc(value)}")
    if meta:
        parts.append('<p class="skeleton-hint">' + " · ".join(meta) + '</p>')
    for note in (notes or [])[:6]:
        parts.append(f'<p class="skeleton-hint">{_esc(note)}</p>')

    for section in spec_sections(doc):
        if not isinstance(section, dict):
            continue
        kind = str(section.get("kind") or "").strip().lower()
        sec_title = _esc(section.get("title") or "")
        parts.append(f"<h2>{sec_title}</h2>" if sec_title else "")
        narrative = str(section.get("narrative") or "").strip()
        if narrative:
            parts.append(f"<p>{_esc(narrative)}</p>")
        if section.get("error"):
            # 取数失败写因由与出路，不拿上一版数字顶替（计划 §3 分支 F）
            parts.append(f'<div class="chart-slot">本节暂无数据：{_esc(section["error"])}'
                         f'（不得沿用历史数字，请重新发起该查询）</div>')
            continue
        data = section.get("data") if isinstance(section.get("data"), dict) else {}
        if kind == "text":
            if not narrative:
                parts.append('<p class="skeleton-hint">本节没写正文。</p>')
        elif kind == "mermaid":
            parts.append('<div class="chart-slot">流程图/脑图需在平台渲染面查看，快照保留源码：'
                         f'<pre>{_esc(section.get("code") or "")}</pre></div>')
        elif kind in ("kpi", "table"):
            parts.append(_spec_table(data))
        elif kind == "chart":
            parts.append(f'<div class="chart-slot">图型 {_esc(section.get("chart_type") or "自动")}：'
                         '本快照为归档静态正文，图表退为表格呈现（服务端无浏览器渲染面，'
                         '不手写伪图）。在对话里打开本报告可看到平台渲染的真图表。</div>')
            parts.append(_spec_table(data))
        else:
            parts.append(f'<p class="skeleton-hint">未知章节形态 {_esc(kind)}，未渲染。</p>')
    parts.append('</body></html>')
    return "\n".join(p for p in parts if p)


# ── 图表数据对象：归一化 + 形态校验（render_chart 的取数结果在这里落成正文）────────
# 为什么规则要在代码里而不是只写在提示词里：` ```chart ` 的图型是模型抄进围栏的，前端
# `resolveChartType` 认不出的图型会**静默退成柱状图**（雷达图画成柱子的历史缺陷），
# `columns.length < 2` 时整张卡片直接不渲染 —— 用户看到的是"没有图"，不是"哪里错了"。
# 渲染机通道把这些形态事实提前判掉，不合规回可重试原因（握手纠错），不再静默。

# 无需用数值列的图型（纯文本展示）
_CHART_NO_NUMERIC = ("text_display",)
# 需要时间列的图型（列名或取值能认出时间，否则时序语义不成立）
_CHART_NEEDS_TIME = ("timeseries_line", "timeseries_bar", "timeseries_area",
                     "calendar_heatmap", "bar_race", "timeline_play")
# 地理类：map_heat 必须经纬度成对；china_map/world_map 认地区名列或经纬度
_CHART_COORD = ("map_heat",)
_CHART_GEO = ("china_map", "world_map")
# 需要两个数值列（X/Y 都是度量）
_CHART_TWO_METRIC = ("scatter", "bubble")
# 需要多指标对比（单指标画出来没有对比信息）
_CHART_MULTI_METRIC = ("radar",)
# 需要两个维度列（源→目标 / 行×列 / 父→子）
_CHART_TWO_DIM = ("sankey", "tree", "heatmap")
# 需要一个维度列（分类轴）：缺了就只能退化成一条没有类别的柱
_CHART_NEEDS_DIM = ("bar", "line", "area", "pie", "funnel", "rose", "radial_bar",
                    "waterfall", "word_cloud", "treemap", "boxplot",
                    "calendar_heatmap", "bar_race", "timeline_play",
                    "timeseries_line", "timeseries_bar", "timeseries_area")

# 列名线索与前端 `lib/geoColumns.ts` / `lib/geoPoints.ts` 同一份关键词：
# 校验侧与渲染侧口径不一致时，会出现"服务端放行、前端认不出列"的裂缝（历史同类坑）。
_LAT_NAME = re.compile(r"(?i)纬度|(^|[^a-z])lat(itude)?([^a-z]|$)")
_LNG_NAME = re.compile(r"(?i)经度|(^|[^a-z])(lng|lon|long|longitude)([^a-z]|$)")
_PLACE_NAME = re.compile(r"(?i)省|市|地区|区域|城市|地名|国家|国别|洲|"
                         r"place|province|city|region|district|country|nation|continent")
_TIME_NAME = re.compile(r"(?i)日期|时间|月份|年份|季度|时刻|date|datetime|time|month|"
                        r"year|quarter|day|week|_dt$|^dt_")
_TIME_VALUE = re.compile(r"^\s*(19|20)\d{2}[-/.年]\d{1,2}|^\s*\d{1,2}[-/]\d{1,2}/(19|20)\d{2}|"
                         r"^\s*(19|20)\d{2}Q[1-4]|^\s*(19|20)\d{2}\s?\d{2}:\d{2}")
# 地区取值线索：列名没写“省/市/地区”时，用后缀 + 常见名兼容判断“这列像不像地区”。
# 这不是地理编码（真定位仍由前端 gazetteer `lib/geoPoints.ts` 裁决）：这里只判
# “有没有可能是地区列”这个形态事实，判宽一点代价小（画不出时地图会显式说“无有效点”），
# 误判拒了反而是能力损失（模型无法改列名）。
_REGION_SUFFIX = re.compile(r"(省|市|自治区|自治州|特别行政区|地区|盟|县|州|国|大区|Region|Province|State|City|Country)$", re.I)
_REGION_NAMES = {"内蒙古", "黑龙江", "北京", "上海", "天津", "重庆", "河北", "山西", "辽宁", "吉林",
                 "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南", "广东", "广西",
                 "海南", "四川", "贵州", "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆",
                 "中国", "美国", "日本", "韩国", "德国", "法国", "英国", "加拿大", "澳大利亚",
                 "巴西", "印度", "俄罗斯", "西班牙", "意大利", "泰国", "新加坡", "马来西亚",
                 "印度尼西亚", "越南", "墨西哥", "China", "USA", "United States", "Japan", "Korea",
                 "Germany", "France", "UK", "United Kingdom", "Canada", "Brazil", "India", "Asia",
                 "Europe", "Africa", "Oceania", "America"}


def _is_number(value: Any) -> bool:
    """数值判定与前端 `isNum` 同口径：bool 不算数（Python 里 True 是 int，不能放行）。"""
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        try:
            float(value)
        except ValueError:
            return False
        return True
    return False


def normalize_chart_rows(columns: list, rows: list) -> tuple[list, list]:
    """把取数结果归一化成 JSON 可表达且前端认得出的类型（Decimal→float、时间→字符串）。

    必须在校验**之前**做：校验判的是"前端拿到这份数据能不能画出这个图型"，而 JSON 序列化
    会把 Decimal 写成字符串 —— 拿归一化前的形状校验，就会出现"服务端说合格、前端认不出数值列"
    的裂缝（图表卡内置的数值列识别只看首行取值）。
    """
    import decimal
    import datetime as _dt

    def cell(value: Any) -> Any:
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, decimal.Decimal):
            return float(value)
        if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
            return value.isoformat(sep=" ") if isinstance(value, _dt.datetime) else str(value)
        if isinstance(value, bytes):
            return value.decode("utf-8", "replace")
        return str(value)

    names = [str(c) for c in (columns or [])]
    out: list[dict] = []
    for row in rows or []:
        if isinstance(row, dict):
            out.append({str(k): cell(v) for k, v in row.items()})
        elif isinstance(row, (list, tuple)):
            out.append({names[i]: cell(v) for i, v in enumerate(row) if i < len(names)})
        else:
            out.append({names[0]: cell(row)} if names else {})
    return names, out


def _looks_like_region(values: list) -> bool:
    """首 20 个非空值里命中 >=30% 则当“像地区列”（与前端 detectPlaceColumn 的阈值同口径）。"""
    vals = [str(v).strip() for v in (values or [])[:20] if v is not None and str(v).strip()]
    if not vals:
        return False
    hit = sum(1 for v in vals if _REGION_SUFFIX.search(v) or v in _REGION_NAMES)
    return hit / len(vals) >= 0.3


def chart_column_facts(columns: list, rows: list) -> dict:
    """按**消费方口径**归纳列角色（数值列/维度列/时间列/地理列）。

    数值列判定看首行取值类型，与 ChartPicker 的 `typeof rows[0][c] === 'number'` 一致：
    服务端按更宽松的"整列多数值"放行而前端认首行为字符串时，图表会没有 Y 轴 —— 那正是
    要消除的"看起来合格、画出来是空的"。宁可在这里回弹让模型重取。
    """
    names = [str(c) for c in columns or []]
    head = (rows or [])[:20]
    first = head[0] if head and isinstance(head[0], dict) else {}
    numeric = [c for c in names if isinstance(first.get(c), (int, float))
               and not isinstance(first.get(c), bool)]
    dimension = [c for c in names if isinstance(first.get(c), str)]
    # 数值被当成字符串下发（模型抄 rows 时最常见的形态错）：单独识别出来给出原因，
    # 不笼统说“缺数值列”（前者改写法就好，后者要重新取数）
    quoted_numeric = [c for c in names if c not in numeric and c in dimension
                      and any(_is_number(r.get(c)) for r in head if isinstance(r, dict))
                      and not any(isinstance(r.get(c), str)
                                  and not _is_number(r.get(c)) for r in head if isinstance(r, dict))]
    lat = next((c for c in names if _LAT_NAME.search(c)), "")
    lng = next((c for c in names if _LNG_NAME.search(c)), "")
    place = next((c for c in dimension if _PLACE_NAME.search(c)), "")
    if not place:
        place = next((c for c in dimension
                      if _looks_like_region([r.get(c) for r in head if isinstance(r, dict)])), "")
    time_col = next((c for c in names if _TIME_NAME.search(c)), "")
    if not time_col:
        time_col = next((c for c in names if head
                         and _TIME_VALUE.match(str(head[0].get(c) or ""))), "")
    return {"numeric_cols": numeric, "dimension_cols": dimension,
            "quoted_numeric_cols": quoted_numeric,
            "lat_col": lat, "lng_col": lng, "place_col": place, "time_col": time_col,
            "row_count": len(rows or [])}


def validate_chart_shape(columns: list, rows: list, chart_type: str) -> tuple[list, list, dict]:
    """校验「这份数据 + 这个图型」是否成立，回 ``(阻断项, 非阻断提示, 列角色事实)``。

    只判"画不出来/画出来没有信息量"的形态事实，不判"该不该用这个图型"的审美（那是模型的裁决）。
    入参必须是 ``normalize_chart_rows`` 之后的形状（校验对象 = 前端将要拿到的那份数据）。
    """
    kind = str(chart_type or "").strip().lower()
    facts = chart_column_facts(columns, rows)
    numeric, dims = facts["numeric_cols"], facts["dimension_cols"]
    blockers: list[str] = []
    notes: list[str] = []

    if not facts["row_count"]:
        return (["图表数据为空：本轮没有可展示的取数结果，请先取到数据再出图（不得用空数据占位）"],
                notes, facts)
    if len(columns or []) < 2:
        return ([f"图表至少需要两列（维度列 + 数值列），当前只有 {len(columns or [])} 列："
                 "前端会整张卡片不渲染（用户看到的是「没有图」而不是「图型不对」）。"
                 "请让查询带上分组维度，或改用文字结论 + Markdown 表格交付"], notes, facts)

    if kind not in _CHART_NO_NUMERIC and not numeric:
        if facts["quoted_numeric_cols"]:
            blockers.append(
                f"数值列被写成了字符串（带引号）：{('、'.join(facts['quoted_numeric_cols'][:4]))}。"
                "前端按取值类型识别 Y 轴，字符串形态会被当成维度列 —— 请把数值去引号后重试（数字不加引号）")
        else:
            blockers.append(
                f"图型 {kind} 需要数值列，但首行里一个都没有（识别到的列：{'、'.join(dims) or '全是文本'}）。"
                "数值列必须是数字（不加引号）；明细文本请改用 Markdown 表格，不要塞进图表卡")
    if kind in _CHART_TWO_METRIC and len(numeric) < 2:
        blockers.append(
            f"图型 {kind} 需要两个数值列（X 轴与 Y 轴都是度量），当前只识别到 {len(numeric)} 个："
            "请重新取数带上两个指标，或改用 bar/line")
    if kind in _CHART_MULTI_METRIC and len(numeric) < 3:
        blockers.append(
            f"雷达图需要多指标对比（至少 3 个数值列），当前 {len(numeric)} 个："
            "单指标请用 bar/line，两个指标用 scatter 或 bar")
    if kind in _CHART_NEEDS_TIME and not facts["time_col"]:
        blockers.append(
            f"图型 {kind} 需要时间列（列名含 日期/月份/时间，或取值形如 2026-01-05），当前没有："
            "时序图型没有时间轴就没有演进可读。请重新取数带上时间维度，或改用 bar/line")
    if kind in _CHART_COORD and not (facts["lat_col"] and facts["lng_col"]):
        blockers.append(
            "地图热力需要成对的经度列与纬度列（列名含 经度/lng 与 纬度/lat），"
            "当前识别不到。请按经纬度重新取数；只有地区名时改用 china_map 或 Top N 表格")
    if kind in _CHART_GEO and not (facts["place_col"] or (facts["lat_col"] and facts["lng_col"])):
        blockers.append(
            f"图型 {kind} 需要地理列（地区名列如 省份/城市/国家，或经纬度列），当前两样都没有："
            "请重新取数带上地区维度；只有一般分类维度时改用 bar/pie，地域分布退 Top N 表格")
    if kind in _CHART_TWO_DIM and len(dims) < 2:
        blockers.append(
            f"图型 {kind} 需要两个维度列（源→目标 / 行×列），当前只识别到 {len(dims)} 个维度列："
            "请重新取数带上成对维度，或改用 bar")
    if kind in _CHART_NEEDS_DIM and not dims:
        blockers.append(
            f"图型 {kind} 需要一个维度列（分类轴），当前全是数值列："
            "请重新取数带上分组维度，或改用 big_number_trend 展示单一指标")

    if facts["row_count"] == 1 and kind not in _CHART_NO_NUMERIC + ("big_number_trend", "gauge",
                                                                    "table_value"):
        notes.append("只有一行数据，成图信息量有限：单指标结论可用 big_number_trend 或直接文字表述")
    if kind == "bar_race" and len(dims) < 2:
        notes.append("竞速条形图建议再加一个系列列（如 月份 + 产品 + 销量），否则逐帧只有一组条")
    if kind in _CHART_GEO + _CHART_COORD:
        notes.append("地图类图型由平台渲染面出图（HTML 单文件通道拿不到底图与瓦片）")
    return blockers, notes, facts
