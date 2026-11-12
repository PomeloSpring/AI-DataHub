"""Dataset Service — BI 治理建模层（语义对象 × SQL × ES 索引三种定义类型）。

语义对象 × SQL 是**双定义**(非二选一): 执行 SQL 是查询的实际执行通道; 本体对象绑定供语义层
检索/业务口径(字段随本体自动演进)。两者可互相转换:
- compile_from_semantic: 语义意图 → 编译 SQL(固化为执行定义)
- extract_from_sql:      执行 SQL 输出列 → 语义字段映射建议(未命中回抛待映射)

ES 型数据集（source_type='es'）是**第三种类型，不参与双定义互转**：它面向平台内置
Elasticsearch 通道（`core/es_client.py`），按 2026-11-04 产品决策不进治理域（ES SQL 无 JOIN
与可包裹子查询、sqlglot 无 ES 方言，因此 RLS/列级策略在本通道技术上不可实现），数据面
闸门 = 部署侧索引模式白名单 ∧ 数据集 `es_pattern` 收窄。详见下方「ES 分支」段与
`es_client` / `es_tools` 的模块注释。

数据集同时是平台内**唯一的"可信任预制 SQL"载体**：语义层编译不出的形状（漏斗/路径
的两级聚合、窗口函数、自关联）由数据集正文承担，参数与输出列由声明约束；
具体编译在 `semantics/dataset_compiler.py`（与语义层 planner 共用同一实现）。

取数契约(数据护城河):
- 有执行 SQL: 保存即只读校验 → 参数渲染/输出列聚合编译 → 行级 scope 子查询包裹
  → governed_execute(敏感基线+RLS+审计)
- 仅语义对象: intent → resolve_binding → plan → execute_semantic(七闸门链, 与 Chat 同源)
- ES 索引: 参数渲染 → `es_client.run_sql`（白名单硬门）; 结果必带 `execution_mode='es'`
  与 `governance` 标注 + 落审计 —— 这是"行为可区分"的强制底线，UI 不标注不等于不记。
- 身份只信服务端 JWT 解析后传入; 无可信身份 fail-closed(NoIdentityError 向上传播)。
- 数据集级行范围"只收紧不放宽": 与用户查询条件 AND 合并, 叠加在 RLS/敏感屏蔽之上。
  ES 型数据集**不得配行级 scope**（靠子查询包裹注入，ES 不成立），保存时直接拒。
- 可见性由角色授权裁决(adh_role_dataset_access): admin 全可见, 其余 fail-closed。
"""
from __future__ import annotations

import json
import logging
import re

from backend.common.db import execute_query, execute_write
from backend.core.governed_query import NoIdentityError

logger = logging.getLogger(__name__)

# 字段名白名单(scope 拼接 SQL 用, 防注入): \w 含中文/字母/数字/下划线(语义层维度名常为中文),
# 不匹配反引号/引号/分号/空白等注入字符
_FIELD_RE = re.compile(r"^\w{1,64}$")
# scope/查询 op 白名单(与语义层 intent filters 对齐)
_OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "like"}
_DEFAULT_LIMIT = 200
# 单次取数的行数硬上限：不分页（统计展示型数据集）时一次最多回这么多行。
# 2026-11-11 拍板由 2000 收到 1000：表值图真分页上线后，这个值只管“不分页的整结果集”
# 该不该继续往浏览器里堆，不再承担“假装分页时多拉点”的角色。
_MAX_LIMIT = 1000
# SQL 源内层保底 LIMIT(外层 scope 过滤前不移析过多行)
_INNER_LIMIT = 5000
# 执行阶段失败对外统一文案(原始报错仅进服务端日志, 护栏 §7)
_EXEC_FAIL_HINT = ("取数在执行阶段失败(通常是数据源连接/凭据不可用)。"
                   "请在数据集基本信息中选择可用的执行数据源或稍后重试, 勿向用户展示连接细节。")

# 确定性失败的面向人文案（数据集页/看板/报表看到的是这里，不是 LLM 工具提示）。
# 判类口径复用 `semantics.contract.classify_exec_failure`（单一真源，不对错因文本
# 再写一套正则）；旧写法把**每一个**执行失败都说成“连接/凭据不可用，稍后重试”，
# 而方言不符/表列不存在是重试永远复现的缺陷，看板会一直转圈到有人去看 SQL 为止
# （历史缺陷：会话 1783069459277）。
_EXEC_FAIL_HINT_BY_CATEGORY = {
    "sql_dialect": ("SQL 与所选执行数据源的方言不匹配，被服务端拒绝。重试不会好："
                    "请核对该数据集的执行数据源是否就是数据实际所在的那个源，"
                    "并按该源方言重写标识符引号/时间函数。"),
    "missing_object": ("所选执行数据源里找不到本次查询引用的表或列。重试不会好："
                       "请在数据集基本信息里改选正确的执行数据源，或先做一次元数据同步再核对表名。"),
}


def _exec_category_of(exc) -> str:
    """从异常上取错因类别（抛错方在原始引擎异常还在手上时已分好）。

    两个载体同一含义：`governed_query` 的 `RuntimeError.exec_category`（raw SQL 路）与
    `SemanticError.detail['failure_category']`（语义路）。拿不到时回 ''，由调用方再按原文判。
    """
    cat = str(getattr(exc, "exec_category", "") or "").strip()
    if cat:
        return cat
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        return str(detail.get("failure_category") or "").strip()
    return ""


def _exec_fail_text(reason: str, db_type: str = "", category: str = "") -> str:
    """执行失败 → 面向人的可操作文案（按错因分类；抖动类保留原有默认源提示）。

    `category` 非空时直接用它：分类只在原始异常那里做一次。旧写法是下游拿
    `str(e)` 再猜一遍，而 raw SQL 路的 `str(e)` 已是 `governed_query` 翻译好的中文，
    判据（1054 / does not exist 等英文标记）必失配 → 确定性缺表失败被说成
    “连接/凭据不可用，稍后重试”（2026-11-10 现网：两个遗留看板 9 张图就这么被误判）。
    """
    cat = str(category or "").strip()
    if not cat:
        from backend.semantics.contract import classify_exec_failure

        cat, _llm_hint = classify_exec_failure(str(reason or ""), db_type)
    return _EXEC_FAIL_HINT_BY_CATEGORY.get(cat) or _EXEC_FAIL_HINT

# 口径来源(DDL 见 docker/mysql/dataset_origin_column_migration.sql)：只由服务端写入，
# 人工在数据集页编辑不得改它——"这份口径是人写的还是 AI 会话产物收编来的"必须可查、
# 可筛、可审计，所以是独立列而不是 description 自由文本(自由文本会被改写导致来源消失)。
DATASET_ORIGINS = {"manual", "chart_backfill", "ai_session"}
ORIGIN_LABELS = {"manual": "人工定义", "chart_backfill": "看板收编", "ai_session": "AI 会话产物"}


def normalize_origin(value) -> str:
    """来源取值归一：未识别/为空一律按 manual（存量默认），不猜第三种来源。"""
    origin = str(value or "").strip().lower()
    return origin if origin in DATASET_ORIGINS else "manual"


def _parse_json(value, default):
    if isinstance(value, (dict, list)):
        return value
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default


def _sql_str(v) -> str:
    """SQL 字符串字面量安全转义(单数据源 MySQL 方言; 值来自管理员配置的 scope)."""
    return "'" + str(v).replace("\\", "\\\\").replace("'", "''") + "'"


# ═══════════════════════════════════════════════════════════════════
# CRUD
# ═══════════════════════════════════════════════════════════════════

def _derive_source_type(object_key: str, sql_query: str, es_pattern: str = "") -> str:
    """展示用来源标注(不驱动分支): 双定义并存时为 'both'; ES 型优先判 es。

    ES 与其他类型**不共存**（`validate_es_definition` 会拒掉 object_key），所以 es 命中
    就直接返回，不再参与 both 组合。
    """
    if str(es_pattern or "").strip():
        return "es"
    has_obj, has_sql = bool(object_key), bool(sql_query)
    return "both" if has_obj and has_sql else ("semantic" if has_obj else "sql")


def is_es_dataset(ds: dict) -> bool:
    """该数据集行是否走 ES 通道（只用 source_type/es_pattern 两个字段判别）。"""
    row = ds or {}
    if str(row.get("source_type") or "").strip().lower() == "es":
        return True
    return bool(str(row.get("es_pattern") or "").strip())


def es_channel_state() -> dict:
    """内置 ES 通道就绪态（直接复用 `builtin_mcp.state_of` 这个单一事实源）。

    不在这里另算一套"通道可用否"：MCP 服务页与数据集页必须同一口径，否则
    会出现页面 A 说就绪、页面 B 说未配置的漂移。只给结论/原因/数量，不回配置值。
    """
    from backend.modules.mind.execution import builtin_mcp

    return builtin_mcp.state_of("elasticsearch")


def require_es_channel() -> dict:
    """通道未就绪即拒（fail-loud），不回空结果。返回就绪态供调用方复用。"""
    state = es_channel_state()
    if not state.get("ready"):
        raise ValueError(
            f"内置 Elasticsearch 通道当前不可用：{state.get('status_message') or state.get('state')}。"
            f"该配置由部署侧托管，请联系管理员处理后重试（不要向用户展示空结果当作“没数据”）")
    return state


# 能承载数据集取数的**内置 MCP 服务**（viz 侧能力声明：内置 MCP 不止一个，只有登记在此的
# 才会出现在数据集「通道」筛选里）。key = builtin_mcp 的服务名（= adh_mcp_servers.name，
# 删除重建 id 会变、name 不变），value = list_datasets 用的规范通道名。
# 名称/就绪态一律回源 builtin_mcp，不在这里另抄一份文案（防两处漂移）。
_DATASET_MCP_CHANNELS: dict[str, str] = {"elasticsearch": "es"}


def mcp_channel_options() -> list[dict]:
    """数据集清单「取数通道」筛选的 MCP 服务清单（供选择框渲染：显示用 label，选择用 value）。

    未就绪的服务**照常列出并带原因**：悄悄少一项会让用户读成“没这个通道”，而事实是
    部署侧没开（no-silent-degradation）。这里不要求 dataset:es——筛选属消费面，
    受权限把关的是配置面（索引范围/字段清单，见 es_pattern_options）。
    """
    from backend.modules.mind.execution import builtin_mcp

    out: list[dict] = []
    for item in builtin_mcp.catalog():
        name = str(item.get("name") or "").strip()
        channel = _DATASET_MCP_CHANNELS.get(name)
        if not channel:
            continue
        state = builtin_mcp.state_of(name)
        out.append({
            # value 是给选择框当 key 的实现细节，页面展示只看 label（ui-resource-display §1）
            "value": f"mcp:{name}",
            "channel": channel,
            "server": name,
            "label": _ES_CHANNEL_LABEL if channel == "es" else (item.get("label") or name),
            "group": "MCP 服务（内置）",
            "ready": bool(state.get("ready")),
            "state": state.get("state") or "",
            "status_message": state.get("status_message") or "",
            "open_view_count": int(state.get("open_view_count") or 0),
        })
    return out


def _canonical_channel_filter(channel: Any) -> str:
    """把筛选值归一成服务端规范通道名：'' / 'es' / 'mcp:elasticsearch' 都合法。

    未知值显式拒并给出候选，不得静默当“不限”——那会把“筛过了但空清单”误读成“没数据集”。
    'mcp:<服务名>' 是按 MCP 分维度的写法（前端筛选器按服务分组渲），'es' 是同义旧写法，
    两者都收：历史调用与用例不因此改口径。
    """
    raw = str(channel or "").strip().lower()
    if raw in ("", "all"):
        return ""
    if raw == "es":
        return "es"
    valid = ", ".join([f"mcp:{k}" for k in _DATASET_MCP_CHANNELS] + ["es"])
    if raw.startswith("mcp:"):
        mapped = _DATASET_MCP_CHANNELS.get(raw.split(":", 1)[1])
        if mapped:
            return mapped
        raise ValueError(f"不支持的取数通道筛选：{channel or '(空)'}；可选 {valid}，或留空表示不限")
    raise ValueError(f"不支持的取数通道筛选：{channel or '(空)'}；可选 {valid}，或留空表示不限")


# ═══════════════════════════════════════════════════════════════════
# 可用数据源范围(一份定义 × N 个源, 同构本体 deploy_sites)
# ═══════════════════════════════════════════════════════════════════
# datasource_id=0 = 默认引擎(平台元数据库), 不参与角色数据源授权裁决
# (既有 governed_execute 对 0/None 不做源级 RBAC, 存量行为零变化)。
_DEFAULT_ENGINE_ID = 0
_DEFAULT_ENGINE_LABEL = "默认引擎"
# ES 型数据集的"执行源"位显示的业务可读标注：它不挂在任何数据源上，不得拿
# "默认引擎"或裸 id 充当名称（ui-resource-display §1）。
_ES_CHANNEL_LABEL = "内置 Elasticsearch 通道"


def _es_patterns_live() -> tuple:
    """当前部署开放的索引模式白名单（唯一事实源是 config，每次现读不缓存）。"""
    from backend.core import es_client

    return es_client.allowed_patterns()


def es_scope_live(pattern: str) -> bool:
    """数据集登记的范围当前是否仍可查（通道就绪 ∧ 落在白名单内）。

    必须按 `index_allowed` 的 fnmatch 口径判，**不能拿字面值去比对白名单元组**：
    数据集登记的是时间片段收窄后的模式（如 `logs-*2026.11`），它被白名单里的
    `logs-*` 覆盖但不字面相等——用 `in` 会把它误判成"范围已失效"而从清单里掉出去。
    """
    from backend.core import es_client

    want = str(pattern or "").strip()
    return bool(want) and es_client.index_allowed(want)


def _load_datasource_catalog() -> dict[str, dict]:
    """数据源业务名 → {id, name, db_type, site_code}（供按名解析）。

    不落共享缓存：这张表是权限/范围判定的事实源，跨进程缓存会让“新建/改名源”在
    TTL 内被解成「已不存在」（误报失效），且测试把 execute_query 换为假表时会将假数据
    写进共享 Redis、污染在线进程。调用方自己按“一次操作内复用”传 catalog（见 list_datasets）。
    """
    rows = execute_query("SELECT id, name, db_type, site_code FROM adh_datasources") or []
    return {str(r["name"]): {"id": int(r["id"]), "name": str(r["name"]),
                             "db_type": str(r.get("db_type") or ""),
                             "site_code": str(r.get("site_code") or "")} for r in rows}


def _datasource_name_by_id(datasource_id: int, catalog: dict[str, dict] | None = None) -> str:
    """数据源 id → 业务名（找不到返回空串，由调用方决定是否标注失效）。"""
    did = int(datasource_id or 0)
    if not did:
        return ""
    for row in (catalog if catalog is not None else _load_datasource_catalog()).values():
        if int(row["id"]) == did:
            return str(row.get("name") or "")
    return ""


def available_source_names(ds: dict, catalog: dict[str, dict] | None = None) -> list[str]:
    """数据集可用范围的业务名清单 = [执行源名] + available_datasources（去重保序）。

    执行源隐式属于可用范围（无需在 available_datasources 里重复登记），与
    本体 `deploy_sites` 「主源 ∪ 路由源」口径一致。
    """
    cat = catalog if catalog is not None else _load_datasource_catalog()
    names: list[str] = []
    exec_name = _datasource_name_by_id(int(ds.get("datasource_id") or 0), cat)
    if exec_name:
        names.append(exec_name)
    raw = _parse_json(ds.get("available_datasources"), [])
    if not isinstance(raw, list):
        raw = []
    for item in raw:
        nm = str(item or "").strip()
        if nm and nm not in names:
            names.append(nm)
    return names


def usable_sources(ds: dict,
                   catalog: dict[str, dict] | None = None) -> tuple[list[dict], list[str]]:
    """可用范围 → (已解析源清单 [{id,name,db_type,site_code}], 悬空/失效名清单)。

    悬空名（源已删/改名）不得静默丢弃：随错误文案对外暴露（no-silent-degradation）。
    """
    cat = catalog if catalog is not None else _load_datasource_catalog()
    ds_id = int(ds.get("datasource_id") or 0)
    usable: list[dict] = []
    missing: list[str] = []
    if ds_id == _DEFAULT_ENGINE_ID:
        # 默认引擎（元数据库）始终可用，不依赖角色源授权
        usable.append({"id": _DEFAULT_ENGINE_ID, "name": "", "db_type": "mysql",
                       "site_code": ""})
    for nm in available_source_names(ds, cat):
        row = cat.get(nm)
        if not row:
            if nm not in missing:
                missing.append(nm)
            continue
        if int(row["id"]) not in {u["id"] for u in usable}:
            usable.append({"id": int(row["id"]), "name": nm,
                           "db_type": row["db_type"], "site_code": row["site_code"]})
    if ds_id and ds_id not in {u["id"] for u in usable}:
        # 执行源行已不存在（id 查不到名）：登记为失效项，不得当"没有可用源"静默处理
        missing.append(f"执行数据源(id:{ds_id} 已不存在)")
    return usable, missing


def _allowed_source_ids(identity: dict) -> set[int]:
    """角色已授权数据源 id 集（唯一裁决点，空=无权，不得解释为全量）。"""
    from backend.core.role_service import role_service

    return {int(i) for i in (role_service.get_user_allowed_datasources(
        int(identity.get("user_id") or 0), int(identity.get("workspace_id") or 0)) or [])}


def usable_scope(ds: dict, identity: dict, *, catalog: dict[str, dict] | None = None,
                 allowed_ds: set[int] | None = None) -> tuple[list[dict], list[str]]:
    """当前身份可执行的源 = 可用范围 ∩ 角色授权源（默认引擎直通）。

    admin 不开后门：enforcer Step 1 的源级 RBAC 对 admin 同样生效（旁路在 Step 3），
    此处口径必须与取数侧一致，否则出现"列表看得见、一取数就报错"的空承诺。
    `allowed_ds` 供清单类循环一次取授权集后复用（避免 N 次角色查询）。
    """
    usable, missing = usable_sources(ds, catalog)
    allowed = allowed_ds if allowed_ds is not None else _allowed_source_ids(identity)
    return ([s for s in usable if s["id"] == _DEFAULT_ENGINE_ID or s["id"] in allowed],
            missing)


def _scope_label(items: list[dict]) -> str:
    return "、".join((s.get("name") or _DEFAULT_ENGINE_LABEL) for s in items) or "无"


def resolve_effective_source(ds: dict, identity: dict, *, site: str = "",
                            datasource: str = "", preferred_id: int = 0,
                            model_id: int = 0,
                            catalog: dict[str, dict] | None = None) -> int:
    """生效数据源解析（服务端定源，LLM/前端只给业务名，不猜源）。

    顺序固定（不得调序）：
      1. 显式 `site`（站点业务名/代码）或 `datasource`（源业务名）→ 必落在可用范围∩授权集内；
      2. `preferred_id`（会话生效源/看板上下文）∈ 交集 → 用它；
      3. 数据集执行源 ∈ 交集 → 用执行源（存量行为零变化）；
      4. 交集唯一 → 自动选定；
      5. 交集多义 → 抛含候选业务名的错误；
      6. 交集为空 → 报错（该数据集对当前身份不可用）。
    """
    scope, missing = usable_scope(ds, identity, catalog=catalog)
    exec_id = int(ds.get("datasource_id") or 0)
    ds_name = str(ds.get("name") or "")
    range_hint = ("；可用数据源范围：" + _scope_label(scope)) if scope else ""

    # ── 1. 显式指定 ──────────────────────────────────────────────
    want_ds = str(datasource or "").strip()
    if want_ds:
        hit = next((s for s in scope if s["name"] == want_ds), None)
        if hit:
            return int(hit["id"])
        if want_ds in available_source_names(ds, catalog):
            # 在可用范围内但角色无授权 → 越权（不换成别的源重试）
            raise PermissionError(
                f"当前角色无权访问数据源「{want_ds}」，无法在数据集「{ds_name}」上执行；"
                f"当前可执行的数据源：{_scope_label(scope)}。请如实告知用户，不要换源重试。")
        raise ValueError(
            f"数据集「{ds_name}」不适用数据源「{want_ds}」{range_hint}"
            + (f"；配置中的失效源：{'、'.join(missing)}" if missing else "")
            + "。请用 list_datasources 查看候选，或询问用户，不要猜源名。")
    want_site = str(site or "").strip()
    if want_site:
        from backend.core.site_service import SiteError, resolve_site_to_source

        # 只在"具体可用且已授权"的源集内解析：传空清单会让 site_service 不按授权过滤
        # （datasources_of_site 的 IN 筛选会被跳过），等于越权拿同站任意源。
        real_ids = [s["id"] for s in scope if s["id"] != _DEFAULT_ENGINE_ID]
        if not real_ids:
            raise ValueError(
                f"数据集「{ds_name}」无可按站点「{want_site}」执行的数据源"
                + ("（仅在默认引擎上执行；要跨站执行请把站点数据源登记到可用数据源范围）"
                   if any(s["id"] == _DEFAULT_ENGINE_ID for s in scope)
                   else f"；当前可执行的数据源：{_scope_label(scope)}")
                + "。请如实告知用户，不要换站重试。")
        try:
            hit_id = resolve_site_to_source(want_site, real_ids, model_id=int(model_id or 0))
        except SiteError as e:
            # SiteError 文案只含站点名与源业务名（护栏 §7），原样外抛可诊断
            raise ValueError(f"数据集「{ds_name}」按站点定源失败：{e}") from e
        return int(hit_id)

    # ── 2. 会话/看板偏好源 ────────────────────────────────────────
    pref = int(preferred_id or 0)
    if pref:
        hit = next((s for s in scope if s["id"] == pref), None)
        if hit:
            return int(hit["id"])

    # ── 3. 执行源 ─────────────────────────────────────────────────
    if any(s["id"] == exec_id for s in scope):
        return exec_id

    # ── 4/5. 交集唯一 / 多义 ──────────────────────────────────────
    if len(scope) == 1:
        return int(scope[0]["id"])
    if scope:
        raise ValueError(
            f"数据集「{ds_name}」可在 {len(scope)} 个数据源上执行（{_scope_label(scope)}），"
            f"无法确定取哪个；请用 site（站点业务名）或 datasource（数据源业务名）指定其一，"
            f"或询问用户。")

    # ── 6. 交集为空 ───────────────────────────────────────────────
    all_usable, _miss = usable_sources(ds, catalog)
    detail = (f"（配置中的失效源：{'、'.join(missing)}）" if missing
              else (f"（可用范围：{_scope_label(all_usable)}）" if all_usable else "（未配置可用范围）"))
    raise PermissionError(
        f"当前角色无权访问数据集「{ds_name}」的任何可用数据源{detail}。"
        f"请检查角色数据源授权或改选其他数据集，勿向用户展示连接细节。")


def effective_source_name(effective_id: int) -> str:
    """生效源 id → 对外业务名（默认引擎给可读标注，绝不回裸 id）。"""
    if not int(effective_id or 0):
        return _DEFAULT_ENGINE_LABEL
    return _datasource_name_by_id(int(effective_id)) or _DEFAULT_ENGINE_LABEL


def list_datasets(identity: dict, keyword: str = "", datasource_id: int = 0,
                  channel: str = "") -> list[dict]:
    """列出数据集(按角色授权过滤可见性, admin bypass); datasource_id>0 时按可用范围筛选。

    可见性 = 角色数据集授权 ∧ (可用数据源范围 ∩ 角色授权源 ≠ ∅)：
    只有 B 权限的用户也能看到“执行源为 A、但 B 在可用范围内”的数据集（并能在 B 上跑）；
    完全无交集的直接隐藏，避免“点得开、一取数就报错”的空承诺。

    `channel` = 按取数通道筛：''=不限，`es` / `mcp:elasticsearch`=只走内置 Elasticsearch
    通道（同义，后者是按 MCP 服务分维度的写法，与筛选器选项 value 一致）。
    ES 型不挂在 `adh_datasources` 上，拿 datasource_id 永远筛不到它（历史缺口：清单里
    看得到、数据源下拉里选不出）。通道与数据源两个筛选互斥，传错值显式拒，
    不静默当“不限”（no-silent-degradation）。
    """
    from backend.core.role_service import role_service

    channel = _canonical_channel_filter(channel)
    if channel == "es" and int(datasource_id or 0):
        raise ValueError("「数据源」与「ES 通道」两个筛选不能同时生效：ES 型数据集不挂在数据源上")

    user_id = int(identity.get("user_id") or 0)
    user_role = identity.get("role") or ""

    # 被引计数随主查询一次 LEFT JOIN 取回：元数据库在远端（单往返百 ms 级），
    # 逐行 COUNT 会把 24 行清单拖成 24 次往返（实测 11s+）。
    sql = ("SELECT d.id, d.name, d.description, d.source_type, d.object_key, d.es_pattern, "
           "d.datasource_id, d.available_datasources, d.chart_type, d.chart_preset, d.owner_id, "
           "d.status, d.origin, "
           "d.field_config, d.created_at, d.updated_at, COALESCE(ref.n, 0) AS `references`, "
           # 入参证据只用于服务端算摘要，算完即剥（见 _fill_param_summaries）：
           # 一趟带回而不是二次回源，清单的往返次数不随行数增长（有契约用例锁）。
           "d.variables, d.sql_query, d.sql_bodies "
           "FROM adh_datasets d "
           "LEFT JOIN (SELECT source_id, COUNT(*) AS n FROM adh_charts "
           "WHERE source_type='dataset' GROUP BY source_id) ref ON ref.source_id = d.id "
           "WHERE d.status='active'")
    params: list = []

    # admin 全可见, 其余角色 fail-closed: 只展示已授权的数据集
    if user_role != "admin":
        allowed_ids = role_service.get_user_allowed_datasets(user_id)
        if not allowed_ids:
            return []
        ph = ",".join(["%s"] * len(allowed_ids))
        sql += f" AND d.id IN ({ph})"
        params.extend(allowed_ids)

    if keyword:
        sql += " AND (d.name LIKE %s OR d.description LIKE %s OR d.object_key LIKE %s)"
        like = f"%{keyword}%"
        params += [like, like, like]
    sql += " ORDER BY d.updated_at DESC LIMIT 200"
    rows = execute_query(sql, tuple(params)) or []

    # 数据源筛选（数据集页顶栏选择器）：语义 = 该源在可用范围内（含执行源）。
    # 按可用范围/交集的判定要拿全量源清单与授权集，故在 Python 侧一次算好（源表行数个位数）。
    catalog = _load_datasource_catalog()
    filter_name = _datasource_name_by_id(int(datasource_id or 0), catalog) if datasource_id else ""
    if datasource_id and not filter_name and int(datasource_id) != _DEFAULT_ENGINE_ID:
        return []                      # 筛选的源已不存在 → 无命中（不静默当"全部"）
    allowed_ds = _allowed_source_ids(identity)
    # ES 通道就绪态在循环外取一次（纯配置读，不入库）：逐行再读是多余的，
    # 且中途拿到不同值会造成"同一页里一半可见一半不可见"。
    es_ready = bool(es_channel_state().get("ready"))
    # 语义字典按数据源复用（一次请求内同一源只取一遍）：同上，不得逐行回源。
    dict_memo: dict[int, tuple[list, list]] = {}

    out: list[dict] = []
    for r in rows:
        # 口径来源显示业务名（ui-resource-display）：前端不得自己映射枚举，且 ES 型同样要标
        r["origin"] = normalize_origin(r.get("origin"))
        r["origin_label"] = ORIGIN_LABELS[r["origin"]]
        if is_es_dataset(r):
            # ES 型不挂在数据源上：`usable_scope`（可用范围 ∩ 角色授权源）对它不适用，
            # 走同一套判定会永远得到"默认引擎"这个不相干标签。可见性按其自身口径判：
            # 通道就绪 ∧ 登记的索引模式仍在白名单内（与取数侧 fail-loud 同口径，
            # 不开"点得开、一取数就报错"的空承诺）。
            pattern = str(r.get("es_pattern") or "").strip()
            if not (es_ready and es_scope_live(pattern)):
                continue
            if datasource_id:          # 按具体数据源筛选时 ES 不属于任何源 → 不命中
                continue
            r["chart_preset"] = _parse_json(r.get("chart_preset"), {})
            r["field_count"] = len(resolve_fields(r, memo=dict_memo))
            r["datasource_name"] = _ES_CHANNEL_LABEL
            r["available_source_names"] = [pattern]
            r["usable_now"] = [pattern]
            out.append(r)
            continue
        usable, _missing = usable_scope(r, identity, catalog=catalog, allowed_ds=allowed_ds)
        if not usable:
            continue                   # 无任何可执行源 → 不可见（与取数侧 fail-closed 同口径）
        if channel == "es":
            continue                   # 只要 ES 通道时，挂在数据源上的数据集不命中
        if datasource_id and datasource_id not in {u["id"] for u in usable}:
            continue
        r["chart_preset"] = _parse_json(r.get("chart_preset"), {})
        r["field_count"] = len(resolve_fields(r, memo=dict_memo))
        # 资源引用一律显示业务名（ui-resource-display）：前端不得回退成 `ID:${id}`
        r["datasource_name"] = _datasource_name_by_id(
            int(r.get("datasource_id") or 0), catalog) or _DEFAULT_ENGINE_LABEL
        r["available_source_names"] = [u["name"] or _DEFAULT_ENGINE_LABEL
                                       for u in usable_sources(r, catalog)[0]]
        r["usable_now"] = [u["name"] or _DEFAULT_ENGINE_LABEL for u in usable]
        out.append(r)
    _fill_param_summaries(out)
    return out


def _fill_param_summaries(rows: list[dict]) -> None:
    """给清单行补「逻辑参数摘要」：看板编辑器自动装配参数控件的唯一数据来源。

    摘要求的是 `variables` + 正文形状（`semantics.param_plan`，与取数翻译同一函数，
    不得前端另推一套），而正文属护栏 §7 的禁止外泄项 —— 所以在这里**算完就地剥掉**：
    清单响应只留业务可读的摘要，不留 SQL、不留 datasource 内部标识。
    """
    if not rows:
        return
    from backend.semantics import param_plan

    for r in rows:
        r["param_summary"] = param_plan.param_summary(r)
        for leak in ("variables", "sql_query", "sql_bodies"):
            r.pop(leak, None)


def get_dataset(dataset_id: int) -> dict | None:
    row = execute_query(
        "SELECT * FROM adh_datasets WHERE id = %s", (dataset_id,), fetchone=True)
    return row


def dataset_paginable(ds: dict) -> bool:
    """该数据集的结果是不是“可按页取的明细行集”（数据集级口径，不是图表的）。

    为什么放在数据集而不是图表：能不能翻到第 N 页由正文形状决定（明细行集 vs 统计
    汇总），而正文只在数据集层；图表只决定“要不要按页展示”。
    列缺失（未应用迁移的库）按 False 处理 —— 不开分页是原行为，不算错。
    """
    return bool(int(ds.get("paginable") or 0))


def _paging_request(ds: dict, params: dict) -> tuple[int, int] | None:
    """取数参数 → (page_size, page_num)；数据集未声明支持分页时返回 None（不按页取）。

    不报错而是回 None：统计展示型数据集本就整页返回，图表拿不到 paging 块即不画分页条。
    真被截断（行数撞到上限）时由结果里的 `truncated` 显式暴露，不靠这里拦。
    """
    from backend.modules.viz.services import es_pagination

    page = es_pagination.resolve_page(params)
    if page is None or not dataset_paginable(ds):
        return None
    return page


def get_dataset_by_name(name: str) -> dict | None:
    return execute_query(
        "SELECT * FROM adh_datasets WHERE name = %s AND status='active'",
        (name,), fetchone=True)


def _validate_and_normalize(req: dict, existing: dict | None = None) -> dict:
    """保存前校验（预制 SQL 正文只读、参数声明可渲染、字段声明合法）。

    不通过直接拒（fail-loud），不存下来等上线静默报错。返回服务端归一化产物
    （touched_tables 由正文静态提取，不信请求体自报）。
    """
    from backend.modules.viz.services.dataset_registry import (
        is_es_definition, validate_definition, validate_es_definition)

    merged = dict(existing or {})
    merged.update({k: v for k, v in (req or {}).items() if k in (
        "sql_query", "sql_bodies", "variables", "field_config", "dialect",
        "es_pattern", "source_type", "object_key")})
    if is_es_definition(merged):
        # ES 型止于 `validate_es_definition`（其中会查白名单与范围收窄）；不能回退到
        # 下面的跨引擎编译校验链——那会拿 sqlglot 去解 ES 语法（必误判）。
        try:
            errors, es_stored = validate_es_definition(merged)
        except Exception as e:  # noqa: BLE001 — 校验器自身异常也必须拒，不得默认放行
            logger.exception("[dataset] ES 定义校验异常")
            raise ValueError(f"数据集校验未完成: {str(e)[:200]}") from e
        if errors:
            raise ValueError("数据集定义不符合规范，已拒绝保存: " + "; ".join(errors[:8]))
        return es_stored or {}
    if not str(merged.get("sql_query") or "").strip() \
            and not merged.get("sql_bodies"):
        return {}                      # 纯语义对象数据集: 无预制 SQL, 不跑正文校验
    try:
        errors, stored = validate_definition(merged)
    except Exception as e:  # noqa: BLE001 — 校验器自身异常也必须拒，不得默认放行
        logger.exception("[dataset] 定义校验异常")
        raise ValueError(f"数据集校验未完成: {str(e)[:200]}") from e
    if errors:
        raise ValueError("数据集定义不符合规范，已拒绝保存: " + "; ".join(errors[:8]))
    return stored or {}


def _normalize_available_datasources(value, catalog: dict[str, dict]) -> str:
    """可用数据源范围归一化（存业务名数组）。

    未知名直接拒（fail-loud）：存下来等于把“配了但永远解不到源”的坑推到运行期。
    不校验调用者对该源有无权限：数据集是全局资产，管理源权限 ≠ 使用源权限。
    """
    if value in (None, ""):
        return "[]"
    if isinstance(value, str):
        value = _parse_json(value, None)
    if not isinstance(value, list):
        raise ValueError("available_datasources 必须是数据源业务名数组")
    names: list[str] = []
    unknown: list[str] = []
    for item in value:
        nm = str(item or "").strip()
        if not nm or nm in names:
            continue
        if nm in catalog:
            names.append(nm)
        else:
            unknown.append(nm)
    if unknown:
        known = "、".join(sorted(catalog)) or "（尚未注册数据源）"
        raise ValueError(f"可用数据源不存在: {'、'.join(unknown)}；已注册数据源: {known}。"
                         f"请从清单选择，不要手输 id 或不存在的源名。")
    return json.dumps(names, ensure_ascii=False)


def _dialect_coverage_warnings(ds_row: dict, catalog: dict[str, dict]) -> list[str]:
    """可用范围内各源的方言覆盖检查（保存时显式告警，不阻断）。

    告警而非静默：跨引擎正文缺失会在运行期由编译器 `DatasetCompileError` 拒绝（不回退其
    他方言正文），但建模者应该在保存时就看到缺口，而不是上线后用户报错才知道。
    """
    from backend.semantics import dialect as sem_dialect
    from backend.semantics.sql_params import pick_body

    sql_query = str(ds_row.get("sql_query") or "").strip()
    bodies = _parse_json(ds_row.get("sql_bodies"), {})
    if not sql_query and not bodies:
        return []                      # 纯语义对象数据集: 无预制正文, 不涉及方言
    usable, _missing = usable_sources(ds_row, catalog)
    base_family = sem_dialect.family_of(_dataset_db_type(ds_row))
    warnings: list[str] = []
    for s in usable:
        db_type = s["db_type"] or "mysql"
        engine = sem_dialect.engine_of(db_type)
        family = sem_dialect.family_of(db_type)
        label = s["name"] or _DEFAULT_ENGINE_LABEL
        _body, err = pick_body(ds_row.get("sql_bodies"), sql_query, engine, family)
        if err:
            warnings.append(f"数据源「{label}」({db_type}) 无可执行正文: {err}")
        elif not bodies and family != base_family:
            warnings.append(
                f"数据源「{label}」方言族为 {family}，与执行源({base_family}) 不同且未登记"
                f"多方言正文(sql_bodies 为空)，跨引擎可能语法不兼容；请为该引擎补登正文或从可用范围移除")
    return warnings


def create_dataset(req: dict, identity: dict, *, origin: str = "manual") -> dict:
    """新建数据集。

    `origin` 为**仅限服务端调用方**的关键字参数（收编脚本/会话产物沉淠）：
    REST 请求体里的同名字段不参与判定（否则任何人可把自己的口径伪造成
    「AI 会话产物」或反向洗白），API 层不传即按 manual 落库。
    """
    name = (req.get("name") or "").strip()
    if not name:
        raise ValueError("数据集名称必填")
    object_key = (req.get("object_key") or "").strip()
    sql_query = (req.get("sql_query") or "").strip()
    datasource_id = int(req.get("datasource_id") or 0)
    es_pattern = (req.get("es_pattern") or "").strip()
    es_mode = is_es_dataset({"source_type": req.get("source_type"), "es_pattern": es_pattern})

    # 双定义并存: 至少一种查询定义, 两者可互转；ES 型只要求正文
    if es_mode:
        _require_es_config_perm(identity, "create")
        require_es_channel()          # 通道未就绪 → 不开放新建（不开"存下来永远跑不通"的空承诺）
        if object_key:
            raise ValueError("ES 数据集不能同时绑定本体对象（ES 不进治理域）")
        if datasource_id:
            raise ValueError("ES 数据集不挂在数据源上：datasource_id 请置空，执行范围由索引模式决定")
        if not sql_query:
            raise ValueError("ES 数据集必须提供 ES SQL 正文")
    elif not object_key and not sql_query:
        raise ValueError("数据集至少需要一种查询定义: 绑定本体对象或提供执行 SQL(两者可并存互转)")
    if sql_query and not es_mode:
        _validate_select(sql_query)
    stored = _validate_and_normalize(req)

    # 可用数据源范围：按业务名校验（未知名拒绝）+ 跨引擎正文覆盖告警；ES 型不适用（不挂在源上）
    catalog = _load_datasource_catalog()
    available_json = "[]" if es_mode else _normalize_available_datasources(
        req.get("available_datasources"), catalog)

    if execute_query("SELECT id FROM adh_datasets WHERE name=%s", (name,), fetchone=True):
        raise ValueError(f"数据集名称已存在: {name}")

    field_config = json.dumps(req.get("field_config") or [], ensure_ascii=False)
    new_id = int(execute_query("SELECT COALESCE(MAX(id),0)+1 AS n FROM adh_datasets",
                               fetchone=True)["n"])
    execute_write(
        "INSERT INTO adh_datasets (id, name, description, source_type, object_key, es_pattern, "
        "datasource_id, available_datasources, sql_query, sql_bodies, variables, touched_tables, field_config, "
        "chart_type, chart_preset, paginable, owner_id, origin, status) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active')",
        (new_id, name, req.get("description") or "",
         _derive_source_type(object_key, sql_query, es_pattern), object_key, es_pattern,
         0 if es_mode else datasource_id, available_json, sql_query,
         ("{}" if es_mode else
          (stored.get("sql_bodies") or json.dumps(req.get("sql_bodies") or {}, ensure_ascii=False))),
         _as_json_text(req.get("variables"), "{}"),
         stored.get("touched_tables"),
         field_config,
         (req.get("chart_type") or "").strip(),
         json.dumps(req.get("chart_preset") or {}, ensure_ascii=False),
         1 if req.get("paginable") else 0,
         int(identity.get("user_id") or 0), normalize_origin(origin)))
    ds = get_dataset(new_id) or {}
    warnings = [] if es_mode else _dialect_coverage_warnings(
        {**ds, "available_datasources": available_json}, catalog)
    for w in warnings:
        logger.warning("[dataset] %s: %s", name, w)
    if warnings:
        ds["warnings"] = warnings
    return ds


def _as_json_text(value, fallback: str) -> str:
    """声明类字段入库存 JSON 文本（已给字符串则原样保留）。"""
    if value in (None, ""):
        return fallback
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def update_dataset(dataset_id: int, req: dict, identity: dict) -> dict:
    ds = get_dataset(dataset_id)
    if not ds:
        raise ValueError("数据集不存在")
    _require_editor(ds, identity)
    # 有效口径 = patch 叠在存量行上（改定义类型/改范围常只带局部字段）
    merged = {**ds, **{k: v for k, v in req.items() if v is not None}}
    es_mode = is_es_dataset(merged)
    if es_mode != is_es_dataset(ds):
        # 定义类型切换是权限面上最敏感的一步（进出治理域），同样要 dataset:es + 通道就绪
        _require_es_config_perm(identity, "switch")
        require_es_channel()
    updates, params = [], []
    for col in ("description", "object_key", "sql_query", "es_pattern"):
        if req.get(col) is not None:
            updates.append(f"{col} = %s")
            params.append(req[col])
    for col in ("sql_bodies", "variables"):
        if req.get(col) is not None:
            updates.append(f"{col} = %s")
            params.append(_as_json_text(req[col], "{}" if col == "variables" else "{}"))
    if req.get("certified") is not None:
        # 认证是口径背书：只能由编辑者显式给出，服务端记录认证人
        updates.append("certified = %s")
        params.append(1 if req["certified"] else 0)
        updates.append("approved_by = %s")
        params.append(str(identity.get("username") or identity.get("user_id") or ""))
    if req.get("chart_type") is not None:
        updates.append("chart_type = %s")
        params.append(str(req["chart_type"]).strip())
    if req.get("paginable") is not None:
        # “结果是明细行集还是统计汇总”由数据集作者声明（表值图能不能真翻页靠它）
        updates.append("paginable = %s")
        params.append(1 if req["paginable"] else 0)
    if req.get("chart_preset") is not None:
        if not isinstance(req["chart_preset"], dict):
            raise ValueError("chart_preset 必须是对象 {xCol,yCol,groupCol,limit}")
        updates.append("chart_preset = %s")
        params.append(json.dumps(req["chart_preset"], ensure_ascii=False))
    if req.get("datasource_id") is not None:
        if es_mode and int(req["datasource_id"] or 0):
            raise ValueError("ES 数据集不挂在数据源上：datasource_id 请置空")
        updates.append("datasource_id = %s")
        params.append(int(req["datasource_id"]))
    if req.get("available_datasources") is not None:
        # 可用数据源范围（业务名数组）：未知名直接报错，不存下来等运行期发现问题
        if es_mode:
            raise ValueError("ES 数据集不挂在数据源上，没有可用数据源范围可配；"
                             "请改设索引范围（es_pattern）")
        updates.append("available_datasources = %s")
        params.append(_normalize_available_datasources(
            req["available_datasources"], _load_datasource_catalog()))
    if req.get("field_config") is not None:
        updates.append("field_config = %s")
        params.append(json.dumps(req["field_config"], ensure_ascii=False))
    if req.get("status") in ("active", "inactive"):
        updates.append("status = %s")
        params.append(req["status"])
    # 更新后至少保留一种查询定义(语义对象/执行 SQL/ES 正文), source_type 随之重算
    object_key = str(req["object_key"] if req.get("object_key") is not None
                     else ds.get("object_key") or "").strip()
    sql_query = str(req["sql_query"] if req.get("sql_query") is not None
                    else ds.get("sql_query") or "").strip()
    es_pattern = str(req["es_pattern"] if req.get("es_pattern") is not None
                     else ds.get("es_pattern") or "").strip()
    if not object_key and not sql_query:
        raise ValueError("数据集至少需要一种查询定义: 绑定本体对象或提供执行 SQL(两者可并存互转)")
    # 改 SQL 必须重新过校验（只读正文 + 参数声明 + 字段声明），不通过不保存
    body_touched = any(req.get(k) is not None for k in
                       ("sql_query", "sql_bodies", "variables", "field_config", "es_pattern"))
    if sql_query and not es_mode and (req.get("sql_query") or body_touched):
        _validate_select(sql_query)
    if es_mode:
        require_es_channel()
    stored = _validate_and_normalize(req, existing=ds) if body_touched else {}
    if stored.get("touched_tables") is not None:
        updates.append("touched_tables = %s")
        params.append(stored["touched_tables"])
    if stored.get("sql_bodies") is not None and req.get("sql_bodies") is None:
        updates.append("sql_bodies = %s")
        params.append(stored["sql_bodies"])
    if es_mode:
        # ES 只有一种方言：残留的多引擎正文必须清掉，否则存下一段永不命中却看似已配的死定义
        updates.append("sql_bodies = %s")
        params.append("{}")
        updates.append("available_datasources = %s")
        params.append("[]")
        updates.append("datasource_id = %s")
        params.append(0)
    # 正文/参数/字段变更即认证失效（防"改完仍挂已认证"）
    if any(req.get(k) is not None for k in ("sql_query", "sql_bodies", "variables",
                                            "field_config")) and req.get("certified") is None:
        updates.append("certified = %s")
        params.append(0)
        updates.append("approved_by = %s")
        params.append(None)
    updates.append("source_type = %s")
    params.append(_derive_source_type(object_key, sql_query, es_pattern))
    if updates:
        execute_write(f"UPDATE adh_datasets SET {', '.join(updates)} WHERE id = %s",
                      tuple(params) + (dataset_id,))
    saved = get_dataset(dataset_id) or {}
    # 保存后重算方言覆盖告警（显式返回，不静默；运行期仍由编译器拒绝无正文的源）；ES 不适用
    warnings = [] if es_mode else _dialect_coverage_warnings(saved, _load_datasource_catalog())
    for w in warnings:
        logger.warning("[dataset] %s: %s", saved.get("name"), w)
    if warnings:
        saved["warnings"] = warnings
    return saved


def delete_dataset(dataset_id: int, identity: dict) -> dict:
    ds = get_dataset(dataset_id)
    if not ds:
        raise ValueError("数据集不存在")
    _require_editor(ds, identity)
    refs = _references(dataset_id)
    if refs:
        raise ValueError(
            "数据集被看板引用, 无法删除: " + ", ".join(r["dashboard_name"] for r in refs[:5]))
    execute_write("DELETE FROM adh_dataset_scopes WHERE dataset_id = %s", (dataset_id,))
    execute_write("DELETE FROM adh_role_dataset_access WHERE dataset_id = %s", (dataset_id,))
    execute_write("DELETE FROM adh_datasets WHERE id = %s", (dataset_id,))
    return {"success": True}


def _require_editor(ds: dict, identity: dict):
    if identity.get("role") == "admin":
        return
    if int(ds.get("owner_id") or 0) != int(identity.get("user_id") or 0):
        raise ValueError("仅创建者或管理员可编辑/删除该数据集")


def _validate_select(sql: str):
    """SQL 源校验: 只允许 SELECT/WITH 单语句(复用护城河 validate_sql).

    缺 LIMIT 的扫描先补默认 LIMIT 再校验(不放行无界扫描, 护栏 §5)。
    """
    from backend.core.query_executor import validate_sql
    probe = sql.strip().rstrip(";")
    if "limit" not in probe.lower():
        probe += f" LIMIT {_INNER_LIMIT}"
    ok, msg = validate_sql(probe)
    if not ok:
        raise ValueError(f"SQL 校验失败: {msg}")


# ═══════════════════════════════════════════════════════════════════
# 语义对象 × SQL 互转(双定义)
# ═══════════════════════════════════════════════════════════════════

def _require_manage(identity: dict, path: str):
    """编译/映射类操作把关: 角色须持有 dataset:manage(fail-closed, 与 API 中间件同口径)."""
    from backend.common.api_permission import check_api_permission
    if not check_api_permission(identity.get("role") or "", "POST", path):
        raise PermissionError("角色未授予「dataset:manage」权限, 无法编译 SQL 或对齐语义字段")


def _require_es_config_perm(identity: dict, scene: str = "use"):
    """ES 型数据集的**配置面**把关：角色须持有 `dataset:es`（fail-closed）。

    只 gate 配置侧（新建/切换定义类型/改索引范围/校验正文/看字段/预览）；**不 gate
    已保存图表的取数** —— 渲染一张 ES 数据集图表属消费面（按 dataset:read + 看板
    可见性裁决），把它压进 dataset:es 会造出“管理员配好了、普通用户看不了图”的空承诺。

    为什么不走 API 中间件的路径匹配：ES 端点与数据集端点同前缀（`/api/datasets*`），
    持有 `dataset:manage` 的角色按路径就会命中 ES 端点，分不出两者；所以该码的
    `api_pattern` 故意留空，改由本函数按码裁决。
    """
    from backend.core.role_service import role_service

    if (identity or {}).get("role") == "admin":
        return
    codes = set(role_service.get_user_role_perm_codes(int(identity.get("user_id") or 0)))
    if "dataset:es" not in codes:
        action = {"create": "新建", "switch": "切换为", "read": "查看"}.get(scene, "配置")
        raise PermissionError(
            f"当前角色未授予「dataset:es」（使用 Elasticsearch 数据集）权限，"
            f"无法{action} ES 型数据集定义。ES 不进数据权限治理域，默认不开放。")


def compile_from_semantic(req: dict, identity: dict) -> dict:
    """语义对象 → 执行 SQL: 把声明式意图编译为 SQL, 供固化为数据集执行定义.

    护栏 §7 例外: 编译 SQL 仅回显给持有 dataset:manage 的数据集编辑者(人工编辑面板),
    不进 LLM/聊天/图表响应。未解析词直接拒绝(宁缺勿错, 不静默绑定)。
    """
    _require_manage(identity, "/api/datasets/compile-from-semantic")
    object_key = (req.get("object_key") or "").strip()
    if not object_key:
        raise ValueError("object_key 必填(请先选择本体对象)")
    datasource_id = int(req.get("datasource_id") or 0)
    from backend.semantics.binding_resolver import resolve_binding
    from backend.semantics.intent import parse_intent
    from backend.semantics.planner import plan

    payload = {
        "object": object_key,
        "metrics": req.get("measures") or req.get("metrics") or [],
        "dimensions": req.get("dimensions") or [],
        "filters": req.get("filters") or [],
        "order": req.get("order") or [],
        "limit": min(int(req.get("limit") or _DEFAULT_LIMIT), _MAX_LIMIT),
        "datasource_id": datasource_id,
    }
    q, err, _notes = parse_intent(payload)
    if err:
        raise ValueError(f"查询意图非法: {err}")
    binding, _bw = resolve_binding(q.object, datasource_id=q.datasource_id)
    if binding is None:
        raise ValueError(f"对象 '{q.object}' 未绑定到任何物理表, 请检查本体模型")
    p = plan(q, binding)
    unresolved = list((p.provenance or {}).get("unresolved_terms") or [])
    if unresolved:
        raise ValueError("存在未解析的业务词, 拒绝编译(请先在语义层补齐字典/别名): "
                         + ", ".join(str(t) for t in unresolved))
    if not p.sql:
        raise ValueError("语义层护栏拒绝了本次编译: "
                         + "; ".join(str(w) for w in (p.warnings or [])))
    return {"sql": p.sql, "dialect": p.dialect,
            "dimensions": list(q.dimensions or []), "measures": list(q.metrics or []),
            "warnings": list(p.warnings or [])}


def extract_from_sql(req: dict, identity: dict) -> dict:
    """执行 SQL → 语义字段映射建议: 探测输出列, 按 精确名→name_en→别名 匹配语义字段.

    未命中列显式回抛待映射(maps_to 为空 + 候选业务名), 不静默绑定
    (ontology-modeling §7 宁缺勿错)。探测取数经 governed_execute 治理入口。
    """
    _require_manage(identity, "/api/datasets/extract-from-sql")
    sql = (req.get("sql_query") or "").strip()
    if not sql:
        raise ValueError("SQL 不能为空")
    _validate_select(sql)
    uid = int(identity.get("user_id") or 0)
    if not uid:
        raise NoIdentityError("缺少可信用户身份, 拒绝探测取数")
    datasource_id = int(req.get("datasource_id") or 0)
    inner = sql.rstrip(";")
    if "limit" not in inner.lower():
        inner += f" LIMIT 1"
    probe = f"SELECT * FROM ({inner}) AS _ds_extract LIMIT 1"
    from backend.core.governed_query import governed_execute
    result = governed_execute(probe, datasource_id or None, uid,
                              int(identity.get("workspace_id") or 0),
                              identity.get("username") or "")
    columns = [str(c) for c in (result.get("columns") or [])]
    first_row = (result.get("rows") or [{}])[0]
    semantic = _semantic_fields(datasource_id, (req.get("object_key") or "").strip())
    by_name = {f["field"]: f for f in semantic}
    by_en = {str(f.get("label") or ""): f for f in semantic if f.get("label")}
    by_alias: dict = {}
    for f in semantic:
        for a in f.get("aliases") or []:
            by_alias.setdefault(str(a), f)

    fields = []
    for col in columns:
        f = by_name.get(col)
        source = "name"
        if f is None:
            f = by_en.get(col)
            source = "name_en"
        if f is None:
            f = by_alias.get(col)
            source = "alias"
        if f is not None:
            fields.append({"field": col, "maps_to": f["field"],
                           "role": f.get("role") or "dimension", "match_source": source})
        else:
            v = (first_row or {}).get(col)
            role = "measure" if isinstance(v, (int, float)) and not isinstance(v, bool) else "dimension"
            fields.append({"field": col, "maps_to": "", "role": role, "match_source": "none",
                           "candidates": [sf["field"] for sf in semantic][:10]})
    return {"fields": fields,
            "semantic_fields": [f["field"] for f in semantic],
            "unmapped": [f["field"] for f in fields if not f["maps_to"]]}


# ═══════════════════════════════════════════════════════════════════
# 字段定义
# ═══════════════════════════════════════════════════════════════════

def field_formats_for(ds: dict, columns: list) -> dict:
    """数据集字段声明的显示格式 → 按本次输出列筛出要下发的那几项（列名 → 格式令牌）。

    为什么**随取数结果下发**而不是复制进图表 config：字段格式属于数据集口径，
    挂到每张图上就多了 N 份第二事实源（改一次要重挂每张图，且看板引入时容易丢）。
    跟着结果走，存量图表下次刷新即生效，各渲染面（轴标签 / tooltip / 表值图 / 明细表）
    拿到的永远是同一份声明。

    匹配键同时认 SQL 输出列名（`field`）与语义字段业务名（`maps_to`）：SQL 路出前者、
    语义路出后者（SLS ASCII 槽位已由 `apply_output_header` 映回业务名）。
    只回本次结果里真实出现的列，空清单不下发（不给前端塞一个永远用不上的键）。
    """
    config = _parse_json(ds.get("field_config"), [])
    if not isinstance(config, list) or not columns:
        return {}
    by_name: dict = {}
    for entry in config:
        if not isinstance(entry, dict):
            continue
        fmt = str(entry.get("display_format") or "").strip()
        if not fmt:
            continue
        for key in (str(entry.get("field") or "").strip(),
                    str(entry.get("maps_to") or "").strip()):
            if key:
                by_name.setdefault(key, fmt)
    return {col: by_name[col] for col in columns if col in by_name}


def resolve_fields(ds: dict, datasource_id: int | None = None, *, memo: dict | None = None) -> list[dict]:
    """数据集字段: 绑定语义对象时以语义字典为主(随本体自动演进),
    field_config 保存「SQL 输出列 → 语义字段」映射(maps_to)与 SQL 独有列;纯 SQL 源读 field_config.

    每项带 from 标记('semantic' 语义字典演进 | 'config' field_config 手工),
    供前端区分可编辑范围(仅 config 项可改名/改角色)。
    `datasource_id` 可指定按哪个源取字典（跨源数据集切到生效源后，字典行按生效源或
    ds=0 全局行取，否则出现"能取数但字段/口径目录空"的半截功能）。
    `memo` 为调用方提供的请求级字典缓存（{datasource_id: (metrics, dims)}），
    清单类循环传入以免 N×2 次字典回源。
    """
    config = _parse_json(ds.get("field_config"), [])
    object_key = (ds.get("object_key") or "").strip()
    if not object_key:
        for e in config:
            e["from"] = "config"
        return config
    semantic = _semantic_fields(int(ds.get("datasource_id") or 0)
                                if datasource_id is None else int(datasource_id),
                                object_key, memo=memo)
    for f in semantic:
        f["from"] = "semantic"
    if not config:
        return semantic
    by_name = {f["field"]: dict(f) for f in semantic}
    fields = list(by_name.values())
    for e in config:
        target = str(e.get("maps_to") or "")
        if target in by_name:
            by_name[target]["sql_column"] = str(e.get("field") or "")
            # 显示格式是「这个字段怎么给人看」的声明，语义字典行没有承载位，只能由
            # field_config 补到映射后的语义字段上（否则格式配在 SQL 列名上、语义路
            # 输出的是业务名，配了等于没配）。
            fmt = str(e.get("display_format") or "").strip()
            if fmt:
                by_name[target]["display_format"] = fmt
        else:
            extra = dict(e)
            extra["from"] = "config"
            extra["unmapped"] = bool(target)  # 有 maps_to 但语义字段不存在 = 悬空映射
            fields.append(extra)
    return fields


def _semantic_fields(datasource_id: int, object_key: str,
                     *, memo: dict | None = None) -> list[dict]:
    try:
        from backend.semantics.mdl_compiler import _get_dimensions, _get_metrics

        # 字典行本身不缓存（改字典必须即时可见），只在一次请求内按源复用
        rows = memo.get(datasource_id) if memo is not None else None
        if rows is None:
            rows = (_get_metrics(datasource_id), _get_dimensions(datasource_id))
            if memo is not None:
                memo[datasource_id] = rows
        metrics, dims = rows
        metrics = [m for m in metrics if (m.get("bound_object_key") or "") == object_key]
        dims = [d for d in dims if (d.get("bound_object_key") or "") == object_key]
    except Exception as e:  # noqa: BLE001
        logger.warning("[dataset] semantic fields failed ds=%s obj=%s: %s",
                       datasource_id, object_key, e)
        return []
    fields = []
    for d in dims:
        fields.append({
            "field": d["name"], "label": d.get("name_en") or d["name"],
            "role": "dimension", "is_time": (d.get("category") or "") == "时间",
            "aliases": d.get("aliases") or [], "enum": d.get("value_labels") or {},
            "description": d.get("description") or "",
        })
    for m in metrics:
        fields.append({
            "field": m["name"], "label": m.get("name_en") or m["name"],
            "role": "measure", "unit": m.get("unit") or "",
            "aliases": m.get("aliases") or [],
            "calculation": m.get("formula") or "",
            "description": m.get("description") or "",
        })
    return fields


# ═══════════════════════════════════════════════════════════════════
# 行级范围(分享时生效; 只收紧不放宽)
# ═══════════════════════════════════════════════════════════════════

def list_scopes(dataset_id: int) -> list[dict]:
    rows = execute_query(
        "SELECT * FROM adh_dataset_scopes WHERE dataset_id = %s ORDER BY id",
        (dataset_id,)) or []
    for r in rows:
        r["filters"] = _parse_json(r.get("filters"), [])
    return rows


def set_scopes(dataset_id: int, scopes: list[dict], identity: dict) -> dict:
    """全量替换数据集行范围. 每条: {subject_type, subject_id, filters:[{field,op,value}]}"""
    ds = get_dataset(dataset_id)
    if not ds:
        raise ValueError("数据集不存在")
    _require_editor(ds, identity)
    if is_es_dataset(ds) and (scopes or []):
        # 行级 scope 靠 `SELECT * FROM (正文) AS _ds_scoped WHERE ...` 子查询包裹注入，
        # ES SQL 不接受这种形状（也无列级策略可用）。存下 scope 等于配了一个永不生效的
        # 权限开关，比不配更糟（no-silent-degradation），所以这里显式拒。
        raise ValueError("ES 型数据集不支持行级范围（靠子查询包裹注入，ES SQL 不成立）；"
                         "请把收窄条件直接写进查询正文，或改用数据源型数据集")
    cleaned = []
    for s in scopes or []:
        st = s.get("subject_type")
        if st not in ("user", "role"):
            raise ValueError("subject_type 仅支持 user|role")
        sid = int(s.get("subject_id") or 0)
        if not sid:
            raise ValueError("subject_id 必填")
        filters = []
        for f in (s.get("filters") or []):
            field = str(f.get("field") or "")
            op = str(f.get("op") or "eq")
            if not _FIELD_RE.match(field) or op not in _OPS:
                raise ValueError(f"非法 scope 条件: field={field} op={op}")
            if f.get("value") is None:
                continue
            filters.append({"field": field, "op": op, "value": f["value"]})
        if filters:
            cleaned.append((st, sid, json.dumps(filters, ensure_ascii=False)))
    execute_write("DELETE FROM adh_dataset_scopes WHERE dataset_id = %s", (dataset_id,))
    for st, sid, fj in cleaned:
        execute_write(
            "INSERT INTO adh_dataset_scopes (dataset_id, subject_type, subject_id, filters) "
            "VALUES (%s,%s,%s,%s)", (dataset_id, st, sid, fj))
    return {"success": True, "count": len(cleaned)}


def _scope_filters(dataset_id: int, identity: dict) -> list[dict]:
    """当前用户命中的全部行范围条件(个人 + 角色), AND 合并."""
    uid = int(identity.get("user_id") or 0)
    if not uid:
        return []
    params: list = [dataset_id, uid]
    ph = "(subject_type='user' AND subject_id=%s)"
    role_name = identity.get("role") or ""
    if role_name:
        role = execute_query("SELECT id FROM adh_roles WHERE name=%s",
                             (role_name,), fetchone=True)
        if role:
            ph += " OR (subject_type='role' AND subject_id=%s)"
            params.append(int(role["id"]))
    rows = execute_query(
        f"SELECT filters FROM adh_dataset_scopes WHERE dataset_id=%s AND ({ph})",
        tuple(params)) or []
    merged: list[dict] = []
    for r in rows:
        merged.extend(_parse_json(r.get("filters"), []))
    return merged


# ═══════════════════════════════════════════════════════════════════
# ES 分支（内置 Elasticsearch 通道：数据集的第三种定义类型）
# ═══════════════════════════════════════════════════════════════════
# 本分支**不经** `semantics.execute` / `governed_execute`，原因不是"没接"，而是技术上不成立：
# ES SQL 无 JOIN、只接受可扁平化的子查询（RLS 的包裹注入手法没有落点），且 sqlglot 无
# elasticsearch 方言（列级策略的编译期改写没有解析器）。2026-11-04 的产品决策据此把 ES
# 划在治理域之外，闸门收敛为两条、实现只在 `core/es_client.py` 一处：
#   部署侧索引模式白名单（fail-closed，空白名单 = 全拒）∧ 数据集 es_pattern 收窄。
# 代价必须在非 UI 层留痕（no-silent-degradation §1「行为可区分」）：结果带
# `execution_mode='es'` + `governance` 标注，成功与拒绝均落审计（护栏 §9）。
# 前提（写在配置、代码与页面三处）：白名单内索引的数据**应已脱敏**；若接入未脱敏索引
# 或需按角色限制行/列可见性，必须重新评估本通道口径，不是在这里加兜底。

# 与 `es_tools._GOVERNANCE_NOTE` 同口径（那边是会话通道，这里是配置/取数通道，
# 字段必须一致，否则审计与评测无法用同一个条件分辨“未套策略的通道”）。
_ES_GOVERNANCE_NOTE = {
    "channel": "builtin_elasticsearch",
    "column_policy": "not_applied",
    "row_filter": "not_applied",
    "gate": "index_allowlist+dataset_scope",
    "note": "内置 Elasticsearch 通道：按产品决策不参与列级/行级数据权限治理；"
            "可查范围 = 部署侧开放的索引模式 ∧ 数据集已选范围，模式内数据应已脱敏。",
}

# 写给建模人员的方言规则：每条都有实机凭据（集群 ES 8.9.1，探针 logs/probe_es_sql_shapes{,2,3,4}.py），
# 不凭通用 SQL 直觉写。唯一对外返回的文案源（避免前端另写一份飘移）。
_ES_SQL_HINT = (
    "Elasticsearch SQL 是受限方言（以下均经本集群实测）：只能单条 SELECT、FROM 只能一个索引模式、"
    "无 JOIN/CTE，必须显式 LIMIT。索引名含连字符与点，不包双引号会被当成减法；必须写 "
    'FROM "模式名"（模式原样复制，不展开成具体月索引）。字段名同样要双引号："@timestamp"'
    "（反引号不行，实测报 parsing_exception）。日期桁用 DATE_TRUNC('DAY'|'WEEK'|'MONTH'|"
    "'QUARTER', \"字段\")；DATE_FORMAT 实测会语法通过但把格式串当常量返回（错数），已拒收。"
    "text 字段可直接 GROUP BY（ES 自动取 .keyword 子桁）。时间片段接在模式末尾（如 \"logs-*2026.11\"）"
    "只收窄**索引**、不收窄行（实测该模式仍含上月数据）；要按时间收行请写 WHERE \"@timestamp\" 范围。"
    "全模式聚合（实测 2.7 亿行）会超过默认 20s 超时，请靠 WHERE/时间片段收小后再挂图。"
)


def es_pattern_options(identity: dict) -> dict:
    """可查索引范围清单 + 通道就绪态 + 方言提示（数据集页选择框的数据源，同屏给结论）。

    未就绪时**不返回空清单了事**：把原因一起回（前端据此提示而不是渲成“暂无索引”，
    否则配置缺口会被当成业务结论：这个范围就是没数据）。
    """
    from backend.core import es_client

    _require_es_config_perm(identity, "read")
    state = es_channel_state()
    patterns = list(es_client.allowed_patterns()) if state.get("ready") else []
    return {
        "ready": bool(state.get("ready")),
        "state": state.get("state"),
        "status_message": state.get("status_message") or "",
        "patterns": patterns,
        "governance": _ES_GOVERNANCE_NOTE,
        "sql_hint": _ES_SQL_HINT,
    }


def es_field_options(pattern: str, identity: dict) -> dict:
    """索引范围的字段清单（写 ES SQL 与配字段映射前确认形状）。

    字段能力直接决定图能不能画：`aggregatable=false` 的字段不能当桁，text 字段要拿
    `exact_match_field`（.keyword）——原样透传 `describe_index_fields` 的标注，不在这里重新判定。
    """
    from backend.core import es_client

    _require_es_config_perm(identity, "read")
    want = str(pattern or "").strip()
    if not want:
        raise ValueError("缺少索引范围（pattern）：请从清单选择，不要手输")
    if not es_client.index_allowed(want):
        raise ValueError(
            f"索引范围「{want}」不在当前部署开放的范围内；当前可查: "
            f"{', '.join(es_client.allowed_patterns()) or '（无，部署侧尚未登记）'}")
    try:
        info = es_client.describe_index_fields(want)
    except es_client.EsError as exc:
        # 模式未匹配到任何字段时 ES 回 200+空 fields，`describe_index_fields` 已按约定显式报错；
        # 文案含可查清单，属可操作提示，原样外抛（护栏 §7：EsError 不带地址/凭据）
        raise ValueError(str(exc)) from exc
    return {
        "pattern": info["pattern"],
        "fields": info["fields"],
        "sub_fields": info["sub_fields"],
        "governance": _ES_GOVERNANCE_NOTE,
    }


def validate_es_sql(req: dict, identity: dict) -> dict:
    """保存前校验 ES 正文并探测输出列（仅回列名与一行样本，不回全量数据）。

    与 `validate-sql`（治理源）同构但手法不同：ES 不支持子查询包裹，不能拿
    `SELECT * FROM (...) LIMIT 1` 去探列，因此直接按正文执行并用 fetch_size=1 收小回页；
    列名以 ES 响应的 columns 为凭，不靠猜。
    """
    from backend.core import es_client
    from backend.semantics.sql_params import parse_variables, render_body

    _require_es_config_perm(identity, "read")
    require_es_channel()
    sql = str(req.get("sql") or req.get("sql_query") or "").strip()
    pattern = str(req.get("es_pattern") or "").strip()
    if not sql:
        raise ValueError("ES SQL 不能为空")
    decls = parse_variables(req.get("variables"))
    body, errs = render_body(sql, decls, req.get("args") or {})
    errors, _indexes = _es_probe_errors(body, decls, pattern)
    if errs:
        errors = errs + errors
    if errors:
        raise ValueError("; ".join(errors[:6]))
    try:
        result = es_client.run_sql(body, fetch_size=1)
    except es_client.EsError as exc:
        raise ValueError(str(exc)) from exc
    columns = [str(c.get("name")) for c in (result.get("columns") or [])]
    types = {str(c.get("name")): str(c.get("type") or "")
             for c in (result.get("columns") or [])}
    first = (result.get("rows") or [[]])[0]
    fields = []
    for i, name in enumerate(columns):
        v = first[i] if i < len(first) else None
        # 角色推断与治理源同口径（数值→度量，其余→维度），但 ES 自带的 type 更可信
        numeric = isinstance(v, (int, float)) and not isinstance(v, bool)
        fields.append({"field": name, "label": name,
                       "role": "measure" if numeric else "dimension",
                       "es_type": types.get(name, "")})
    return {"fields": fields, "columns": columns, "indexes": result.get("indexes") or [],
            "governance": _ES_GOVERNANCE_NOTE}


def _es_probe_errors(body: str, decls: dict, pattern: str) -> tuple[list[str], list[str]]:
    """委托注册层做静态校验（同一个实现，不在这里重复一套判定）。"""
    from backend.modules.viz.services.dataset_registry import probe_es_sql

    return probe_es_sql(body, decls, pattern)


def _es_default_window(ds: dict, args: dict) -> tuple[dict, dict]:
    """ES 正文引用了 `start_time`/`end_time` 而值为空时，由服务端补滚动窗。

    为何只能在服务端补（全部为本集群实测，不是推测）：ES SQL 拿不到可用的“本地当前时刻”——
    - `NOW()` 不支持；`CURRENT_TIMESTAMP` 返回 UTC ISO 串（含 `T`/`Z`），与库里
      `yyyy-MM-dd HH:mm:ss` 的本地时间串**不能按字典序比**（同日会被判错方向）；
    - `DATE_FORMAT(x, 'yyyy-MM-dd HH:mm:ss')` 把格式串当**字面量**返回（实测得到字串本身）；
    - 拿日期算式直接比 text 列会退化成不过滤（实测 COUNT 与全量一致）。
    不补的代价：留空 = 全量扫所有月度索引（App 使用趋势实测 20s 读超时），
    并把几年历史灌进图表缓存。MySQL 侧同一口径写在正文里（`COALESCE(NULLIF(p,''), NOW())`），
    这里等价补齐，并把实际窗口回显在结果的 `default_window` 里告知调用方，
    不留“看不见的默认值”（no-silent-degradation）。
    """
    from datetime import datetime, timedelta

    from backend.semantics.sql_params import parse_variables

    body = str(ds.get("sql_query") or "")
    out = dict(args or {})
    applied: dict = {}
    if "${start_time}" not in body and "${end_time}" not in body:
        return out, applied
    try:
        days = max(1, int((parse_variables(ds.get("variables")).get("days") or {}).get("default") or 30))
    except (TypeError, ValueError):
        days = 30
    now = datetime.now()
    for key, value in (("start_time", (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")),
                       ("end_time", now.strftime("%Y-%m-%d %H:%M:%S"))):
        if f"${{{key}}}" not in body or str(out.get(key) or "").strip():
            continue          # 正文未引用 / 调用方显式给了值 → 不覆盖
        out[key] = value
        applied[key] = value
    if applied:
        applied["days"] = days
        applied["reason"] = "未传时间参数，服务端按数据集默认窗口补齐（ES SQL 无本地当前时刻表达式）"
    return out, applied


def _render_es_body(ds: dict, args: dict) -> str:
    """数据集正文 + 运行参数 → 可下发 ES SQL（参数按声明渲染成受控字面量）。

    未声明参数/缺参/类型不符全部报错（与编译层同口径），不静默丢参数后跑一条
    “少了一个筛选条件”的查——那会直接多数。
    """
    from backend.semantics.sql_params import parse_variables, render_body

    decls = parse_variables(ds.get("variables"))
    body, errs = render_body(str(ds.get("sql_query") or ""), decls, args if isinstance(args, dict) else {})
    if errs:
        raise ValueError("; ".join(errs[:6]))
    return body.strip().rstrip(";")


def _es_audit(identity: dict, ds: dict, sql: str, *, ok: bool, row_count: int = 0,
              reason: str = "") -> None:
    """ES 取数审计：只记索引模式与行数，**不记数据行**（避免审计成为敏感数据的第二副本）。

    失败不得影响主链路（护栏 §9），但失败本身必须可查：成功与拒绝均落一行。
    """
    try:
        from backend.common.auth import log_audit
        from backend.core import es_client

        indexes = ",".join(es_client.from_indexes(sql) or []) or str(ds.get("es_pattern") or "-")
        log_audit(
            int(identity.get("user_id") or 0), str(identity.get("username") or ""),
            action="es_dataset_query" if ok else "es_dataset_query_denied",
            target_type="elasticsearch", target_id=int(ds.get("id") or 0),
            detail=(f"dataset={ds.get('name')} index={indexes} rows={row_count}"
                    + (f" reason={reason}" if reason else "")),
        )
    except Exception as exc:  # noqa: BLE001 — 审计不得影响取数主链路
        logger.debug("[dataset] es audit skipped: %s", exc)


def _query_es(ds: dict, params: dict, identity: dict) -> dict:
    """ES 型数据集取数：参数渲染 → `es_client`（白名单硬门在执行口内）。

    与 sql/semantic 分支的三处不同是口径决定的，不是简化：
    - 不走 `resolve_effective_source`：ES 不是 adh_datasources 行，拿它求“可用范围 ∩
      角色授权源”恒为空，会把能查的数误拒成无权；执行范围由 es_pattern + 白名单裁决。
    - 不套行级 scope / 不做列级脱敏：技术上不可实现（见本节头注释），因此在定义时就
      拒配 scope，而不是运行期静默不套。
    - 分页走 `es_pagination`（translate + search_after 游标）而不是在 SQL 里拼 OFFSET：
      ES SQL 语法里没有 OFFSET（`LIMIT a, b` / `LIMIT n OFFSET m` 实机均回 400）。
    """
    from backend.common.df_serialize import _unique_columns
    from backend.core import es_client
    from backend.modules.viz.services import es_pagination

    uid = int(identity.get("user_id") or 0)
    if not uid:
        raise NoIdentityError("缺少可信用户身份, 拒绝 ES 取数(数据合规护城河)")
    require_es_channel()   # 通道未就绪 → 先给可读原因，不去打集群也不回空结果（否则被当成“没数据”）
    pattern = str(ds.get("es_pattern") or "").strip()
    if not pattern:
        raise ValueError("ES 数据集缺少索引范围（es_pattern），请在数据集页重新选择")
    # 时间参数留空 → 服务端补滚动窗（见 `_es_default_window`：ES 侧拿不到本地当前时刻）
    raw_args = params.get("args") or params.get("variables") or {}
    es_args, default_window = _es_default_window(ds, raw_args)
    sql = _render_es_body(ds, es_args)
    if params.get("filters") or params.get("order"):
        # 看板自由筛选器/排序需要往正文拼 WHERE，而 ES 无治理层 AST 改写可用；
        # 不拿字符串拼接冒充支持（拼错了就是错数），显式拒并给出可行的路。
        raise ValueError("ES 型数据集不支持运行时筛选器/排序追加（无 AST 改写可用）；"
                         "请把条件做成数据集参数 ${...} 写进正文")
    page = _paging_request(ds, params)
    limit = min(int(params.get("limit") or _DEFAULT_LIMIT), _MAX_LIMIT)
    if page:
        # 按页取数时行限就是页大小：不开“图上只显 20 行、实际拉了 500 行”的口径偏差
        limit = min(limit, page[0])
    try:
        _norm, declared_limit = es_client.parse_select(sql)
    except es_client.EsError as exc:
        _es_audit(identity, ds, sql, ok=False, reason=str(exc))
        raise ValueError(str(exc)) from exc
    if declared_limit is not None and declared_limit < limit:
        # 正文 LIMIT 比图表要的行数小是建模选择（控制扇描规模），不算错；但不得静默拉高，
        # 否则“图上只显示 50 根柱”与“正文写 LIMIT 50”对不上，排查时看不出来。
        logger.info("[dataset] es limit capped by body LIMIT: ds=%s body=%s want=%s",
                    ds.get("id"), declared_limit, limit)
    paged: dict | None = None
    try:
        if page:
            # 翻页上下文签名只用**不随时刻变化**的口径（正文模板 + 调用方给的参）：
            # 拿渲染后正文算会因滚动窗逐秒漂移，第 2 页永远找不到第 1 页的游标
            paged = es_pagination.query_page(
                pattern, sql, page_size=page[0], page_num=page[1],
                context_key=es_pagination.paging_key(
                    pattern, str(ds.get("sql_query") or ""), raw_args, page[0]))
            raw_columns, raw_rows = paged["columns"], paged["rows"]
            # 后续页检索的是首页确立的那份正文，审计/索引清单都得按它算
            sql = paged["sql"]
        else:
            raw = es_client.run_sql(sql, fetch_size=limit)
            raw_columns = [str(c.get("name")) for c in (raw.get("columns") or [])]
            raw_rows = raw.get("rows") or []
    except es_pagination.EsPagingUnsupported as exc:
        # 正文形态不可按页取（聚合/表达式/列映射对不上）：指名怎么改，不回“空结果”充数
        _es_audit(identity, ds, sql, ok=False, reason=str(exc))
        raise ValueError(str(exc)) from exc
    except es_client.EsError as exc:
        # 白名单/方言拒绝的文案含可查清单，属可操作提示，原样外抛（不含地址/凭据）
        _es_audit(identity, ds, sql, ok=False, reason=str(exc))
        raise ValueError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — ES 原始报错可能含索引表达式/集群信息
        logger.error("[dataset] es execution failed ds=%s: %s", ds.get("id"), exc)
        _es_audit(identity, ds, sql, ok=False, reason=type(exc).__name__)
        raise ValueError("Elasticsearch 取数失败，请联系管理员查看服务端日志") from exc

    # 两条执行通道同形（列名 + 按位值数组），重名列在此统一消歧后一次成行
    columns = _unique_columns(raw_columns)
    rows = [dict(zip(columns, r)) for r in raw_rows]
    _es_audit(identity, ds, sql, ok=True, row_count=len(rows))
    result = {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "dataset": ds.get("name"),
        "execution_mode": "es",
        "es_scope": pattern,
        "es_indexes": es_client.from_indexes(sql),
        "governance": _ES_GOVERNANCE_NOTE,
    }
    if paged is not None:
        result["total"] = paged.get("total")
        result["paging"] = {
            "mode": paged.get("paging_mode"),
            "page_size": paged.get("page_size"),
            "page_num": paged.get("page_num"),
            "total": paged.get("total"),
            # 集群无 PIT ⇒ 游标式只能往后走（随机跳页不可得）：前端据此只给顺序按钮
            "can_jump": False,
            # ES 通道行序恒确定：正文的 sort + `search_after` 补的 `_doc` 全序 tiebreaker
            "stable_order": True,
        }
    elif len(rows) >= limit:
        # 不分页且行数撞到上限：显式标 truncated，前端要提醒“可能还有更多”，
        # 不得把“共 1000 行”当成全量冒充在 446 万行的明细表上
        result["truncated"] = True
    else:
        # 没分页也没撞线：必须说出来“本次结果就是全部”（unpaged）。
        # 缺这条事实，渲染面无法区分“缓存里是完整结果”与“缓存是分页上线前的旧全量”，
        # 只能每次进页面都补一次重查——存量统计型表值图一直转圈就是这么来的。
        result["unpaged"] = True
    if default_window:
        # 可见地告知“本次用的是哪个窗口”，而不是让调用方把默认窗口误读成“全量/无数据”
        result["default_window"] = default_window
    return result


# ═══════════════════════════════════════════════════════════════════
# 统一取数入口(看板/预览/Chat 共用)
# ═══════════════════════════════════════════════════════════════════

def query_dataset(dataset_id: int, params: dict, identity: dict) -> dict:
    """按当前身份取数: 权限/RLS/敏感屏蔽/审计/scopes 全部服务端施加.

    params: {dimensions[], measures[], filters[], order[{by,desc}], limit,
             args{}, site?, datasource?, preferred_datasource_id?,
             page_size?, page_num?}
    分页两件是表值图真分页专用：`page_size` 每页行数（服务端硬上限 100）、`page_num` 页号（从 1 起）。
    偏移量由服务端算，不接受调用方递交 offset；**也没有 `count_sql`**：统计语句不得由调用方递交，
    统一由服务端从数据集编译产物生成。
    是否真的按页取还看数据集的 `paginable`（明细行集 vs 统计汇总）：未开启则分页参数无效，
    整结果集一次返回（上限 `_MAX_LIMIT`，撞到即标 `truncated`）。
    `site`/`datasource` 是业务名（唯一可接受的选源入参，服务端在可用范围∩授权集内
    解析为 id）；本函数**不接受** datasource_id（§7 黑盒，内部 id 不得由外部供给）。
    semantic 源走七闸门链; sql 源走 governed_execute(scope 以子查询包裹);
    ES 源走 `es_client`（内置通道，不进治理域，结果带显式标注与审计）。
    """
    ds = get_dataset(dataset_id)
    if not ds or ds.get("status") != "active":
        raise ValueError("数据集不存在或已停用")
    # ES 分支先于定源：它不挂在数据源上，把 resolve_effective_source 走在前面会
    # 因“可用范围 ∩ 授权集 = 空”而把能查的数误拒成无权（口径不同，不是换通道）。
    if is_es_dataset(ds):
        result = _query_es(ds, params, identity)
        formats = field_formats_for(ds, result.get("columns") or [])
        if formats:
            result["field_formats"] = formats
        return result
    if params.get("datasource_id"):
        raise ValueError("不接受数据源内部 id：请用 site（站点业务名）或 datasource（数据源业务名）指定")
    scopes = _scope_filters(dataset_id, identity)
    # 生效源解析（服务端定源，不猜）：可用数据源范围 ∩ 角色授权集
    effective = resolve_effective_source(
        ds, identity,
        site=str(params.get("site") or ""),
        datasource=str(params.get("datasource") or ""),
        preferred_id=int(params.get("preferred_datasource_id") or 0),
        model_id=int(params.get("model_id") or 0))
    # 生效源注回副本：下游（方言正文/编译方言/执行源/字段目录）一律按生效源，
    # 跨引擎时不得拿执行源的 db_type 去编译（历史单源假设）。
    ds = {**ds, "datasource_id": effective}
    # 执行路径显式选择(非降级): 执行 SQL 是实际执行通道; 仅绑定语义对象时走七闸门语义链。
    if (ds.get("sql_query") or "").strip():
        result = _query_sql(ds, params, scopes, identity)
    elif (ds.get("object_key") or "").strip():
        result = _query_semantic(ds, params, scopes, identity)
    else:
        raise ValueError("数据集缺少执行定义: 请配置执行 SQL 或绑定本体对象")
    # 对外只回生效源**业务名**（看板/报表/Chat 可诊断"这次跑的哪个源"），不回 id
    result.pop("datasource_id", None)
    result["datasource"] = effective_source_name(effective)
    # 字段显示格式随结果下发（数据集是权威源），渲染面据此把 ISO 时间串归成人读值
    formats = field_formats_for(ds, result.get("columns") or [])
    if formats:
        result["field_formats"] = formats
    return result


def _query_semantic(ds: dict, params: dict, scopes: list[dict], identity: dict) -> dict:
    from backend.semantics.binding_resolver import resolve_binding
    from backend.semantics.gates import execute_semantic
    from backend.semantics.intent import parse_intent
    from backend.semantics.planner import plan

    if _paging_request(ds, params):
        # 语义链（七闸门）只出 `LIMIT n`，没有 OFFSET/COUNT 形状可用；翻页需要包裹
        # 子查询，会绕过闸门编译产物。不做“只改显示行”的假分页（那会把同一页当第二页）。
        # 能走到这里说明数据集已标 `paginable`，而语义型根本翻不了 —— 指名改配置。
        raise ValueError("语义型数据集不支持按页取数（七闸门产物无 OFFSET/行总数形状）；"
                         "请改挂 SQL 型数据集，或在数据集关闭「支持分页」")

    filters = list(params.get("filters") or [])
    filters += [{"dim": s["field"], "op": s["op"], "value": s["value"]} for s in scopes]
    payload = {
        "object": ds.get("object_key") or "",
        "metrics": params.get("measures") or params.get("metrics") or [],
        "dimensions": params.get("dimensions") or [],
        "filters": filters,
        "order": params.get("order") or [],
        "limit": min(int(params.get("limit") or _DEFAULT_LIMIT), _MAX_LIMIT),
        "datasource_id": int(ds.get("datasource_id") or 0),
    }
    q, err, notes = parse_intent(payload)
    if err:
        raise ValueError(f"查询意图非法: {err}")

    binding, _bw = resolve_binding(q.object, datasource_id=q.datasource_id)
    if binding is None:
        raise ValueError(f"对象 '{q.object}' 未绑定到任何物理表, 请检查本体模型")
    p = plan(q, binding)
    if not p.sql:
        raise ValueError("语义层护栏拒绝了本次查询")
    try:
        se = execute_semantic(q, binding, p, {
            "user_id": int(identity.get("user_id") or 0),
            "username": identity.get("username") or "",
            "workspace_id": int(identity.get("workspace_id") or 0),
        }, question=f"dataset:{ds.get('name')}")
    except Exception as e:  # noqa: BLE001  原始报错仅进日志
        logger.error("[dataset] semantic execution failed ds=%s: %s", ds.get("id"), e)
        raise ValueError(_exec_fail_text(str(e), _dataset_db_type(ds), _exec_category_of(e)))
    if not se.allowed:
        # 执行阶段失败按错因分类给文案; 策略拒绝(权限/护栏)回声明式原因
        if getattr(se, "blocked_at", "") == "execute":
            logger.error("[dataset] semantic execute-stage denied: %s", se.reason)
            raise PermissionError(_exec_fail_text(str(se.reason), _dataset_db_type(ds)))
        raise PermissionError(se.reason or "查询被安全闸门拦截")
    result = dict(se.result or {})
    # SLS/Presto 编译产物的外层列名是 ASCII 槽位（o0/o1…）：语义路与 SQL 路必须同一口径映回业务名。
    # 旧写法只在 SQL 路（compiled.header_map）映、语义路直接下发槽位，看板/报表把“o0”当列名给用户。
    from backend.semantics.execute import apply_output_header
    result = apply_output_header(result, p.output_header_map or {})
    rows = result.get("rows") or []
    if len(rows) > _MAX_LIMIT:
        result["rows"] = rows[:_MAX_LIMIT]
        result["truncated"] = True
    else:
        result["unpaged"] = True   # 语义路不按页取数，未撞线即完整（与 SQL/ES 路同口径）
    result["applied_rls"] = se.applied_rls
    result["masked_columns"] = se.masked_columns
    result["dataset"] = ds.get("name")
    result["execution_mode"] = "semantic"
    return result


def _dataset_db_type(ds: dict) -> str:
    """数据集执行数据源的 db_type（决定正文变体与编译方言）；查不到回落 mysql。"""
    ds_id = int(ds.get("datasource_id") or 0)
    if not ds_id:
        return "mysql"
    try:
        from backend.common.db.datasource_db import get_datasource_by_id

        return str((get_datasource_by_id(ds_id) or {}).get("db_type") or "mysql")
    except Exception as e:  # noqa: BLE001 — 查不到不阻断，但保守用 mysql 族
        logger.warning("[dataset] db_type lookup failed for ds=%s: %s", ds_id, e)
        return "mysql"


def has_row_scopes(dataset_id: int) -> bool:
    """该数据集是否配了行级范围（供语义层绑定前判定）。"""
    row = execute_query(
        "SELECT COUNT(*) AS n FROM adh_dataset_scopes WHERE dataset_id = %s",
        (int(dataset_id or 0),), fetchone=True)
    return bool(row and int(row.get("n") or 0) > 0)


def _query_sql(ds: dict, params: dict, scopes: list[dict], identity: dict) -> dict:
    """SQL 数据集取数：编译委托 `semantics.dataset_compiler`（与语义层同一实现）。

    参数渲染/方言正文选择/输出列聚合全在编译器；本函数只负责把
    行级 scope 以子查询包裹叠加（只收紧不放宽）并送治理执行口。
    服务端分页（看板表值图「服务端分页」）也在这里施加：行数上限用外层
    `LIMIT n OFFSET m`（MySQL 系与 Presto/SLS 同语法），行总数用 COUNT 包裹统计——
    **两条都由服务端基于数据集编译产物生成**，不接受调用方递交的 count SQL
    （旧的 `count_sql` 参数是一条可任意递交的裸 SQL 取数通道，已废除）。
    """
    from backend.core.query_executor import validate_sql
    from backend.core.governed_query import governed_execute
    from backend.semantics.dataset_compiler import DatasetCompileError, compile_dataset

    args = params.get("args") or params.get("variables") or {}
    try:
        compiled = compile_dataset(
            ds,
            args=args if isinstance(args, dict) else {},
            dimensions=params.get("dimensions") or [],
            measures=params.get("measures") or params.get("metrics") or [],
            filters=params.get("filters") or [],
            order=params.get("order") or [],
            limit=params.get("limit"),
            time_grain=params.get("time_grain"),
            db_type=_dataset_db_type(ds),
        )
    except DatasetCompileError as e:
        # 参数非法/无本引擎正文: 直接告诉调用方原因（fail-loud，不换通道不降级）
        raise ValueError(str(e))
    for w in compiled.warnings:
        logger.info("[dataset] %s: %s", ds.get("name"), w)

    sql = compiled.sql
    if scopes:
        where = _build_where(scopes)
        limit = min(int(params.get("limit") or _DEFAULT_LIMIT), _MAX_LIMIT)
        sql = f"SELECT * FROM ({sql}) AS _ds_scoped{where} LIMIT {limit}"

    total = None
    page = _paging_request(ds, params)
    stable_order = True
    if page:
        sql, total, stable_order = _apply_server_pagination(sql, page[0], page[1], ds, identity)

    ok, msg = validate_sql(sql)
    if not ok:
        raise ValueError(f"SQL 校验失败: {msg}")

    try:
        result = governed_execute(
            sql, int(ds.get("datasource_id") or 0) or None,  # 0 → 默认引擎(与 Playground 同口径)
            int(identity.get("user_id") or 0),
            int(identity.get("workspace_id") or 0),
            identity.get("username") or "")
    except NoIdentityError:
        raise
    except Exception as e:  # noqa: BLE001  原始报错(含主机/账号)仅进日志, 对外按错因分类
        logger.error("[dataset] sql execution failed ds=%s: %s", ds.get("id"), e)
        raise ValueError(_exec_fail_text(str(e), _dataset_db_type(ds), _exec_category_of(e)))
    result["dataset"] = ds.get("name")
    result["execution_mode"] = "sql"
    if compiled.resolved_tables:
        result["resolved_tables"] = compiled.resolved_tables
    # SLS/Presto 拒非 ASCII 标识符：编译器外层投影用 ASCII 槽位 o{i} + header_map 映回业务名。
    # 与 semantics.execute._to_result 同口径，使数据集页/看板/报表看到中文列名。
    if compiled.header_map:
        cols = result.get("columns") or []
        result["columns"] = [compiled.header_map.get(c, c) for c in cols]
        rows = result.get("rows") or []
        if rows and isinstance(rows[0], dict):
            result["rows"] = [
                {compiled.header_map.get(k, k): v for k, v in r.items()} for r in rows
            ]
    if total is not None:
        result["total"] = total
    if page:
        result["paging"] = {
            "mode": "offset",
            "page_size": page[0],
            "page_num": page[1],
            "total": total,
            # LIMIT/OFFSET 可随机定位：页码直接可点（与 ES 游标式同一字段不同值，前端据此分流）
            "can_jump": True,
            # 正文顶层没有 ORDER BY 时，行序由执行引擎自定 → 相邻页会重行/漏行。
            # 分页参数没错，错在集合本身没有可翻的顺序 —— 必须显式说出来（前端出提醒）
            "stable_order": stable_order,
        }
    else:
        # 不分页：按编译产物尾部的 LIMIT 判断这次是不是把行取完了（撞线=可能还有更多）。
        # 与 ES 路同一件事必须同一说法：truncated / unpaged 二选一，渲染面据此决定
        # “要不要重查”“共 N 行能不能当成全量”。
        if len(result.get("rows") or []) >= _sql_row_cap(sql):
            result["truncated"] = True
        else:
            result["unpaged"] = True
    return result


# 编译产物尾部总是形如 `LIMIT n`（或 SLS flat 形态下正文自带的 `LIMIT n OFFSET m`）
_TRAILING_LIMIT_RE = re.compile(r"\s+LIMIT\s+\d+(\s+OFFSET\s+\d+)?\s*$", re.IGNORECASE)
_TOP_LEVEL_ORDER_RE = re.compile(r"\bORDER\s+BY\b", re.IGNORECASE)


def _sql_row_cap(sql: str) -> int:
    """不分页时本次查询的行数上限 = 编译产物尾部 `LIMIT n`；没有尾部 LIMIT 则按全局上限算。

    撞线就不能宣称“这就是全部”（引擎只是按上限截了返回），必须标 truncated。
    """
    m = _TRAILING_LIMIT_RE.search(sql)
    if not m:
        return _MAX_LIMIT
    return int(m.group(0).split()[1])


def _has_top_level_order_by(sql: str) -> bool:
    """正文最外层有没有 ORDER BY（括号深度 0，跳过引号内文本与注释）。

    翻页能不能稳就取决于它：没有顶层排序时 `LIMIT n OFFSET m` 的行序由执行引擎自定
    （活库实测：RUM 明细正文无 ORDER BY，相邻两页出过同一行），于是会重行/漏行。
    窗口函数 `OVER (ORDER BY …)` 与子查询里的排序都在括号内，不算。
    """
    depth = 0
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in ("'", '"', "`"):
            quote = ch
            i += 1
            while i < n:
                if sql[i] == "\\" and quote != "`":
                    i += 2
                    continue
                if sql[i] == quote:
                    break
                i += 1
        elif ch == "-" and sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j
        elif ch == "/" and sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and _TOP_LEVEL_ORDER_RE.match(sql, i):
            return True
        i += 1
    return False


def _apply_server_pagination(sql: str, page_size: int, page_num: int,
                             ds: dict, identity: dict) -> tuple[str, "int | None", bool]:
    """把已编译的数据集查询改成「取第 N 页」，并统计行总数。

    返回 (分页 SQL, 总数|None, 行序是否确定)。偏移量在这里由页号算出（`(page_num-1)*page_size`），
    调用方不递 offset；页大小已由 `es_pagination.resolve_page` 收到 ≤100。
    总数每页都统计（不只首页）：分页条要拿它算总页数，少了它就只能显“本批 N 行”、
    把“共 20 行”当成全量挂在实际上几万行的明细表上。
    统计口径 = 本次可执行的查询能返回的行数（不拿全表计数冒充）。
    不替正文补 ORDER BY：“按哪些列排”是口径，猜一个就是拿错的序冒充对的页。
    """
    from backend.core.query_executor import validate_sql
    from backend.core.governed_query import governed_execute

    base = _TRAILING_LIMIT_RE.sub("", sql).strip()
    page_offset = max(0, (int(page_num) - 1) * int(page_size))

    count_sql = f"SELECT COUNT(*) AS cnt FROM ({base}) AS _ds_cnt"
    ok, msg = validate_sql(count_sql, require_limit=False)
    if not ok:
        raise ValueError(f"行数统计 SQL 校验失败: {msg}")
    try:
        counted = governed_execute(
            count_sql, int(ds.get("datasource_id") or 0) or None,
            int(identity.get("user_id") or 0),
            int(identity.get("workspace_id") or 0),
            identity.get("username") or "")
    except NoIdentityError:
        raise
    except Exception as e:  # noqa: BLE001  原始报错仅进日志
        logger.error("[dataset] count query failed ds=%s: %s", ds.get("id"), e)
        # 不能静默去掉总数（前端会错读成“共 N 行”），也不能假成 0：带因报错。
        # 措辞不得断言成因（可能是正文不可包裹，也可能是数据源/表当前不可用），
        # 否则会把“源坏了”误报成“你 SQL 写错了”（no-silent-degradation：可诊断）。
        raise ValueError("分页的行数统计失败：统计查询在数据源上未执行成功"
                         "（可能是该正文形态不支持 COUNT 包裹，或数据源/物理表当前不可用）。"
                         "请在数据集页确认该正文能正常取数；若它是统计汇总结果，"
                         "请关闭数据集的「支持分页」。") from e
    total: int | None = None
    rows = counted.get("rows") or []
    if rows:
        first = rows[0]
        raw = first.get("cnt") or first.get("count(*)") or first.get("COUNT(*)") \
            or (list(first.values())[0] if first else 0)
        try:
            total = int(raw)
        except (TypeError, ValueError):
            total = None

    # 行序确定性与分页参数无关，但决定翻出去的两页会不会是同一批行：
    # 没有顶层 ORDER BY 就如实回 false，由前端在分页条上说出“翻页可能重行/漏行”
    return (f"{base} LIMIT {int(page_size)} OFFSET {page_offset}", total,
            _has_top_level_order_by(base))


def _build_where(filters: list[dict]) -> str:
    parts = []
    for f in filters:
        field, op, value = str(f["field"]), str(f.get("op") or "eq"), f.get("value")
        if not _FIELD_RE.match(field) or op not in _OPS:
            continue
        col = f"`{field}`"
        if op == "in" and isinstance(value, (list, tuple)):
            vals = ", ".join(_sql_str(v) for v in value[:100])
            if vals:
                parts.append(f"{col} IN ({vals})")
        elif op == "like":
            parts.append(f"{col} LIKE {_sql_str('%' + str(value) + '%')}")
        else:
            sym = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=",
                   "lt": "<", "lte": "<="}[op]
            lit = value if isinstance(value, (int, float)) else _sql_str(value)
            parts.append(f"{col} {sym} {lit}")
    return (" WHERE " + " AND ".join(parts)) if parts else ""


# ═══════════════════════════════════════════════════════════════════
# 引用关系(看板图表)
# ═══════════════════════════════════════════════════════════════════

def _references(dataset_id: int) -> list[dict]:
    return execute_query(
        "SELECT c.id AS chart_id, c.name AS chart_name, d.name AS dashboard_name "
        "FROM adh_charts c JOIN adh_dashboards d ON d.id = c.dashboard_id "
        "WHERE c.source_type = 'dataset' AND c.source_id = %s",
        (dataset_id,)) or []


