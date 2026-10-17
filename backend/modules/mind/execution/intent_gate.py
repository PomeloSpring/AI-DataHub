"""前置意图漏斗（Intent Gate）—— chat 输入进入 qodercli harness 之前的确定性分流。

为什么要这一层：两个 chat 入口（`/api/chat/send/stream` 与 `/api/pipeline/send/stream`）
最终都汇到 `ChatService._try_dispatch_via_execution_layer`，任何输入都要付一次完整
agent 回合的成本（compose_system_prompt 的长提示 + 全部 MCP 工具 schema + resume 会话
历史 + 最多 max_turns 轮工具循环）。寒暄、无效输入、能力询问、试探套取这类**根本不需要
模型**的输入也白付这份 credit —— 现有"意图识别"只存在于 prompt 里（SEMANTIC_QUERY_RULES
第一步），服务端此前没有任何前置分流。

设计取向（与实现同样重要）：
- **只用确定性手段**：L0 形态规则 + L1 词典/本体别名命中，不调任何模型（本版不引入小模型通道）。
- **fail-open**：判不准一律放行 agent —— 多花 token 可接受，答错/漏答不可接受。
- **拦截必须可见**（no-silent-degradation）：本地直答文案自带标注，前端有「转深度分析」逃生门，
  每一次判定（含 gate 自身异常）都落 `adh_intent_intercepts` 与 `intent_route` span。
- **三档模式** `off / shadow / enforce`，缺省 **shadow**：只判定与度量、不改变行为，
  由拦截率与零误拦数据证明后再由人决定切 enforce（fde-evolution §5 阶段门）。
- **本版不返回任何数据行**：只分流，不取数。确定性取数快路径留给 Phase 2
  （`Decision.fastpath` 字段即为其接口位，恒为 None）。
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any, Optional

from backend.common import config as _cfg

logger = logging.getLogger(__name__)

# ── 模式与去向 ────────────────────────────────────────────────────────
MODE_OFF, MODE_SHADOW, MODE_ENFORCE = "off", "shadow", "enforce"
MODES = (MODE_OFF, MODE_SHADOW, MODE_ENFORCE)
HANDLED_AGENT = "agent"
HANDLED_GATE = "intent_gate"

# ── 标签枚举（落库/评测/监控共用口径）────────────────────────────────
L_EMPTY = "empty_or_noise"        # 空/纯标点/纯符号/无可读词
L_ACK = "social_ack"              # 寒暄与应答词（你好/谢谢/好的…）
L_PROBE = "probe_refuse"          # 索要提示词/凭据/物理结构等涉密信息
L_HELP = "help_usage"             # 求助/使用说明
L_CAPABILITY = "capability_probe"  # "你能做什么/有哪些数据源"（窄口径）
L_DATA = "data_query"             # 业务取数
L_WRITE = "platform_write"        # 平台功能动作/写意图
L_FOLLOWUP = "context_followup"   # 多轮延续（指代/省略/追问）
L_AMBIG = "ambiguous"             # 判不准 → 默认放行

#: 可以本地直答的标签（其余标签一律下发 agent）
LOCAL_LABELS = frozenset({L_EMPTY, L_ACK, L_PROBE, L_HELP, L_CAPABILITY})

#: 本地直答的可见性由**前端徽章**承担（done 事件带 handled_by=intent_gate → 页面渲染
#: 「未调用模型 · 前置意图层直答」+「转深度分析」按钮）。正文里不再重复同一段声明——
#: 那会把一句正常问候变成三段式公告（no-silent-degradation 要求的是"用户看得到"，不是"正文里写一遍"）。
#: 拦截回合的归属在落库与 trace 的 handled_by 上，不依赖文案。

# ── 工具组 → 面向用户的能力名（只用于展示已授权的能力面，不编造能力）──
GROUP_LABELS = {
    "semantic": "语义层取数（对象/指标/维度的声明式查询）",
    "query": "受治理只读 SQL 查询",
    "catalog": "数据目录与元数据检索",
    "ontology": "业务本体建模与指标/维度字典维护",
    "screen": "数据大屏创建与维护",
    "design": "仪表盘设计",
    "system": "系统运营与元数据概览",
    "assets": "数据集与数据产品",
    "memory": "个人记忆（偏好/口径）",
}


# ══════════════════════════════════════════════════════════════════════
# 规则常量（集中定义，便于按 shadow 数据迭代而不用翻逻辑）
# ══════════════════════════════════════════════════════════════════════

#: 标点/空白/符号（含 emoji）统一剥除，剩下的才是可读内容
_PUNCT_RE = re.compile(r"[\W_]+", re.UNICODE)

#: 纯寒暄/应答词表：整句必须由这些词拼接而成（全匹配语义），绝不做子串命中，
#: 否则"你好，帮我查昨天销量"会被当寒暄拦掉（那是误拦，比漏拦严重）。
_ACK_TOKENS = frozenset(
    """你好 您好 哈喽 嗨 嘿 早上好 中午好 下午好 晚上好 早安 晚安 在吗 在么 在不在
    谢谢 多谢 感谢 谢谢您 辛苦 辛苦了 好的 好 行 可以 没问题 收到 明白 明白了
    了解 知道了 ok okay 嗯 嗯嗯 哦 噢 啊 哎 哈哈 呵呵 嘿嘿 么么哒 拜拜 再见
    测试 试一下 测试一下 test 111 123 1234 abc asdf qwer zzz""".split())
_ACK_LENS = sorted({len(t) for t in _ACK_TOKENS}, reverse=True)

#: 多轮延续信号：句首承接词（指代上一轮）
_FOLLOWUP_HEAD_RE = re.compile(r"^(那|那么|于是|接着|然后|继续|再|还是|换成|同上|照旧)")
#: 多轮延续信号：指代/回溯词
_FOLLOWUP_REFS = (
    "它", "它们", "他", "她", "这个", "这些", "这样", "上面", "上述", "前面",
    "刚才", "刚刚", "之前", "上次", "上一条", "上一轮", "那个", "那些",
    "为什么", "为何", "是不是", "对不对", "还有呢", "然后呢", "怎么说",
)
#: 疑问标记（用于"短追问"判定）
_QUESTION_RE = re.compile(r"[?？]|吗$|呢$|吧$|多少|哪些|哪个|哪种|什么|怎么|如何|是否|有无|几")

#: 取数句式（聚合/趋势/排名/明细/分组等分析意图）
_AGG_QUERY_RE = re.compile(
    r"多少|几个|几日|占比|比例|排名|排行|榜首|top\s*\d*|趋势|走势|波动|对比|比较|分布"
    r"|明细|清单|列表|列出|列举|查一下|查下|查询|看看|看下|统计|汇总|总计|合计|平均|人均"
    r"|最大|最小|最高|最低|峰值|增长|下降|环比|同比|转化率|留存率|活跃|新增|销量|销售额"
    r"|订单量|用量|次数|人数|单量|金额|数量|gmv|uv|pv|dau|mau"
    r"|按.{1,8}(拆分|分组|划分|统计|汇总|排序|排名|看)|各.{1,8}(的|情况|表现|排名|占比)|拆一下|分一下",
    re.IGNORECASE)
#: 时间 token（出现即说明问题指向某个时间范围的数据）
_TIME_TOKEN_RE = re.compile(
    r"今天|今日|昨天|昨日|前天|大前天|明天|本周|这周|上周|下周|本月|这个月|上个月|下个月"
    r"|本季|本季[度]?|上季度|本季度|下季度|今年|去年|明年|近三年|近\s*\d+\s*[天日周月年季]"
    r"|最近\s*\d*|过去\s*\d+|未来\s*\d+|第[一二三四]季度|q[1-4]\b|\d{4}[-/年]\d{1,2}"
    r"|\d{1,2}月[份日号]|\d{4}年|\d{1,2}月\d{1,2}[日号]",
    re.IGNORECASE)
#: 平台功能动作/写意图（一律交回 agent：权限码把关 + 审计 + 级联是它的职责）
_ACTION_WRITE_RE = re.compile(
    r"创建|新建|生成|生产一份|出品|发布|上线|下架|删除|移除|修改|改动|更新|同步|重新同步"
    r"|建图|构建图谱|重建图谱|调度|定时|周期|导出|导入|保存|存为|设为|指定|分配|回收|部署"
    r"|启用|停用|禁用|审批|批准|驳回|执行任务|跑一次|触发"
    r"|报表|看板|仪表盘|大屏|报告|周报|日报|月报|PPT|幻灯片")
#: 涉密索要（agent 本来也会拒绝 —— 本地拒答不是能力回退，而是省一次完整回合）
_PROBE_SECRET_RE = re.compile(
    r"系统提示词|system\s*prompt|你的提示词|你的指令|密钥|api\s*key|access\s*key|ak/sk"
    r"|连接串|数据库地址|账号密码|密码是什么|口令|内部\s*token|bearer\s*token"
    r"|忽略.{0,6}(规则|指令|提示|约束)|绕过.{0,6}(权限|规则|护栏|校验|限制)|越权"
    r"|把.{0,6}(sql|语句).{0,6}(给我|发我|展示出来)|物理表名|底层表结构")
#: 帮助/使用说明
_HELP_RE = re.compile(r"帮助|使用帮助|使用说明|使用手册|怎么用|怎么使用|如何使用|如何使[用做]"
                      r"|有什么(功能|用途|能力)|能做哪些|支持哪些功能|功能清单|能干什么|help\b")
#: 能力/授权面询问（**刻意收窄**：只有这两型能由服务端权威清单本地作答；
#: "有哪些表/字段"属元数据探查，本地答不全，必须交 agent）
_CAPABILITY_RE = re.compile(
    r"(你|您|助手|ai|bot).{0,8}(能|会|可以|支持).{0,6}(做|帮|提供|完成|处理).{0,6}(什么|哪些|啥)"
    r"|你有什么(功能|能力|权限)|你的(功能|能力|权限)(清单|列表|有哪些|范围)"
    r"|(有哪些|有哪几个|能连|能访问|可以连接).{0,6}数据源|数据源(清单|列表|有哪些)")


# ══════════════════════════════════════════════════════════════════════
# 词典装载（L1 业务实体信号源；只读 + 分层缓存，不阻断用户回合）
# ══════════════════════════════════════════════════════════════════════
#
# 为什么不能“未命中就当场回源”：实测一份完整词典冷装载要 0.8~2.4s（元库连接 + 术语 +
# 本体展开），而分流插在用户等首字的那条链路上——那会表现为“加了分流反而更慢”，
# 并且每个用户/每个 TTL 周期都重付一次。因此：
#   * 只读不阻断：没有可用快照时本轮**不阻塞**，开后台线程装载，本次按“词典未就绪”判定
#     （L0 形态规则不依赖词典，仍可靠；依赖“无业务命中”前提的 probe_refuse 在不就绪时不拦）；
#   * stale-while-revalidate：过期先给旧值，后台刷新，TTL 到期不会把延迟转嫁给用户；
#   * 共享部分按 (数据源, 系统域) 缓存、用户部分按 (用户, 工作空间) 缓存，
#     同数据源的多用户共用重活；共享快照同时进 Redis（跨实例/重启不丢）。

_lock = threading.Lock()
_fresh: dict[tuple, tuple[float, list[str]]] = {}
_stale: dict[tuple, list[str]] = {}
_loading: set[tuple] = set()
_shared_cache = None


def _cfg_float(name: str, default: float) -> float:
    """开关/预算的唯一事实源在 `backend/common/config.py`（按调用时读取，便于用例覆盖）。"""
    return float(getattr(_cfg, name, default) or default)


def _shared_cache_store():
    global _shared_cache
    if _shared_cache is None:
        from backend.common.ttl_cache import TTLCache

        _shared_cache = TTLCache("intent_gate_lexicon", maxsize=64,
                                 ttl=int(_cfg_float("INTENT_GATE_LEXICON_TTL_SECONDS", 600)))
    return _shared_cache


def _load_cached(key: tuple, loader, ttl: float, *, redis_name: str = "") -> tuple[list[str], bool]:
    """取词典分部：返回 (词条, 是否就绪)。

    就绪路径：本进程新鲜 → 本进程旧值(SWR) → Redis 快照 → 都不存则后台装载并返回未就绪。
    本函数**不执行任何耗时的同步回源**，宁可本轮少拦一点（fail-open）也不让用户等装载。
    """
    now = time.time()
    with _lock:
        hit = _fresh.get(key)
        if hit and (now - hit[0]) < ttl:
            return hit[1], True
        stale = _stale.get(key)
    if stale is not None:
        # 旧值先上，刷新交后台：TTL 到期对用户不可见
        if redis_name:
            try:
                _load_from_redis(key, redis_name, ttl)
            except Exception:  # noqa: BLE001 — 写回失败不影响用旧值
                pass
        _spawn(key, loader, ttl, redis_name)
        return stale, True
    if redis_name:
        cached = _load_from_redis(key, redis_name, ttl, read_only=True)
        if cached is not None:
            return cached, True
    _spawn(key, loader, ttl, redis_name)
    return [], False


def _load_from_redis(key: tuple, redis_name: str, ttl: float, *, read_only: bool = False):
    """Redis 共享快照读写（未命中/不可用返回 None，由调用方后台回源）。

    TTL 必须转 int：redis-py 的 setex 只接受整数秒，传 float 会直接写失败（实测
    "value is not an integer or out of range"）——快照写不进 = 跳实例/重启后全需重新装载。
    """
    store = _shared_cache_store()
    if read_only:
        val = store.get(redis_name)
        if isinstance(val, list):
            with _lock:
                _fresh[key] = (time.time(), val)
                _stale[key] = val
            return val
        return None
    try:
        store.set(redis_name, _stale.get(key) or [], ttl=int(ttl))
    except Exception as e:  # noqa: BLE001
        logger.debug("[IntentGate] 词典快照写回失败（不影响判定）: %s", e)
    return None


def _spawn(key: tuple, loader, ttl: float, redis_name: str) -> None:
    """后台装载（同一 key 只起一个）。装载失败不重试风暴：等下一个回合再触发。"""
    with _lock:
        if key in _loading:
            return
        _loading.add(key)

    def _work():
        try:
            terms = list(loader() or [])
        except Exception as e:  # noqa: BLE001 — 装载失败只降拦截率，不改分流去向
            logger.warning("[IntentGate] 业务词典后台装载失败（本轮及后续按无业务命中处理）: %s", e)
            terms = None
        finally:
            with _lock:
                _loading.discard(key)
        if terms is None:
            return
        with _lock:
            _fresh[key] = (time.time(), terms)
            _stale[key] = terms
        if redis_name:
            try:
                _shared_cache_store().set(redis_name, terms, ttl=int(ttl))
            except Exception as e:  # noqa: BLE001
                logger.debug("[IntentGate] 词典快照写入失败（不影响本进程使用）: %s", e)

    threading.Thread(target=_work, name="intent-gate-lexicon", daemon=True).start()


def invalidate_lexicon() -> None:
    """本体/字典变更后主动失效（建模页保存后可调；不调也会在 TTL 内自动刷新）。"""
    with _lock:
        _fresh.clear()
        _stale.clear()
    try:
        _shared_cache_store().invalidate()
    except Exception as e:  # noqa: BLE001
        logger.debug("[IntentGate] 词典缓存清理失败（等 TTL 自然过期）: %s", e)


def _add_term(terms: set[str], val: Any) -> None:
    """收录业务词：单字与纯符号噪声词命中率太高（"量""数"），只收 ≥2 个可读字符的词条。"""
    s = str(val or "").strip()
    if len(_PUNCT_RE.sub("", s.lower())) >= 2:
        terms.add(s)


def _semantic_terms(datasource_id: int, system_scope: bool) -> list[str]:
    """词典主体（与用户无关，同数据源一切用户共用）：指标/维度名与别名 + 本体对象可读名。

    口径全部复用既有权威读取口，**不新造"谁能看什么"的判断**：
    - 指标/维度：`backend.semantics.mdl_compiler._get_metrics/_get_dimensions`（同 get_metrics 工具）
    - 本体对象：`adh_ontology_objects` JOIN `adh_ontology_models`，kind 域裁决照抄
      `binding_resolver`（ds>0 取非 system；ds<=0 取 system/空）

    只用于"是否存在业务命中"，**不做任何名称绑定**（宁缺勿错，ontology-modeling §7）。
    """
    from backend.semantics.mdl_compiler import _get_dimensions, _get_metrics

    terms: set[str] = set()
    ds = int(datasource_id or 0)
    for rows in (_get_metrics(ds) or [], _get_dimensions(ds) or []):
        for row in rows:
            _add_term(terms, row.get("name"))
            _add_term(terms, row.get("name_en"))
            for a in (row.get("aliases") or []):
                _add_term(terms, a)
    for t in _ontology_terms(ds, system_scope):
        _add_term(terms, t)
    try:
        from backend.modules.mind.rag import terminology_manager as tm
        for kw in tm.get_business_keywords(ds):
            _add_term(terms, kw)
    except Exception as e:  # noqa: BLE001 — 旁路信号缺失只降拦截率，不改去向
        logger.debug("[IntentGate] 业务术语装载失败（本次不参与命中）: %s", e)
    return sorted(terms)


def _scoped_terms(datasource_id: int, workspace_id: int, user_id: int) -> list[str]:
    """随作用域变化的词典部分：快速路径触发词 + 角色授权数据源业务名。

    数据源名走 `role_service.get_user_allowed_datasources`（与 `resource_guard.bind_resources`
    同一裁决口，waker-datasource-domain §1），只取 name，不取 id/host（护栏 §7）。
    """
    terms: set[str] = set()
    try:
        from backend.modules.mind.execution.quick_paths import _in_scope, _load_rows
        for path in _load_rows():
            if not _in_scope(path, int(workspace_id or 0), int(datasource_id or 0)):
                continue
            for t in path.get("triggers") or []:
                _add_term(terms, t)
            _add_term(terms, path.get("title"))
    except Exception as e:  # noqa: BLE001
        logger.debug("[IntentGate] 快速路径触发词装载失败（本次不参与命中）: %s", e)
    for name in _authorized_datasource_names(user_id, workspace_id):
        _add_term(terms, name)
    return sorted(terms)


def _ontology_terms(datasource_id: int, system_scope: bool) -> list[str]:
    """生效本体对象的可读名与别名（kind 域裁决与 binding_resolver 同一口径）。"""
    from backend.common.db import execute_query

    ds = int(datasource_id or 0)
    # 域裁决（as-bot-system-waker §1：系统域是能力叠加，不是互斥）：
    #   ds>0            → 只取非 system 模型（且限定到该源/全局对象）
    #   ds<=0 + 系统能力 → 业务 ∪ 系统本体都算信号
    #   ds<=0 无系统能力 → 仅业务本体（跨源）
    domain = ("(COALESCE(m.kind,'') <> 'system')" if (ds > 0 or not system_scope)
              else "(1 = 1)")
    sql = ("SELECT o.display_name, o.object_key, o.aliases "
           "FROM adh_ontology_objects o JOIN adh_ontology_models m ON m.id = o.model_id "
           "WHERE m.status = 'active' AND o.is_active = 1 AND " + domain)
    params: tuple = ()
    if ds > 0:
        # 本域限定：源内对象 + 未限定源的全局对象（与 binding 扫描范围一致）
        sql += " AND (o.datasource_id = %s OR COALESCE(o.datasource_id, 0) = 0)"
        params = (ds,)
    sql += " LIMIT 3000"
    out: list[str] = []
    for row in execute_query(sql, params) or []:
        out.append(str(row.get("display_name") or ""))
        out.append(str(row.get("object_key") or "").rsplit(":", 1)[-1])
        raw = row.get("aliases")
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except (ValueError, TypeError):
                parsed = raw.split(",")
            raw = parsed
        if isinstance(raw, list):
            out.extend(str(x) for x in raw)
        elif isinstance(raw, str):
            out.extend(raw.split(","))
    return [x.strip() for x in out
            if len(_PUNCT_RE.sub("", str(x or "").strip().lower())) >= 2]


def _authorized_datasource_names(user_id: int, workspace_id: int) -> list[str]:
    """角色授权集内的数据源业务名（name 全局唯一，可安全参与命中）。"""
    from backend.common.db import execute_query
    from backend.core.role_service import role_service

    ids = list(role_service.get_user_allowed_datasources(int(user_id or 0), int(workspace_id or 0)))
    if not ids:
        return []
    marks = ",".join(["%s"] * len(ids))
    rows = execute_query(f"SELECT name FROM adh_datasources WHERE id IN ({marks})", tuple(ids)) or []
    return [str(r.get("name") or "") for r in rows]


def lexicon(datasource_id: int = 0, system_scope: bool = False, user_id: int = 0,
            workspace_id: int = 0) -> tuple[list[str], str]:
    """读词典：返回 (词条, 状态)。状态 ready=可用；warming=首次装载中（后台跑，本轮不阻塞）。

    热路径目标 <30ms：只读本进程新鲜值/旧值与 Redis 快照，永远不同步回源。
    调用方（route_intent）在 warming 时不依赖"无业务命中"这个前提（该前提无法核实）。
    """
    ds = int(datasource_id or 0)
    sys_scope = bool(system_scope)
    uid = int(user_id or 0)
    ws = int(workspace_id or 0)
    ttl = _cfg_float("INTENT_GATE_LEXICON_TTL_SECONDS", 600)
    sem_key = ("sem", ds, sys_scope)
    scope_key = ("scope", ds, ws, uid)
    sem, sem_ready = _load_cached(sem_key, lambda: _semantic_terms(ds, sys_scope), ttl,
                                  redis_name=f"terms:sem:{ds}:{int(sys_scope)}")
    scope, scope_ready = _load_cached(scope_key, lambda: _scoped_terms(ds, ws, uid),
                                      max(60.0, ttl / 2),
                                      redis_name=f"terms:scope:{ds}:{ws}:{uid}")
    terms = sorted(set(sem) | set(scope))
    return terms, ("ready" if (sem_ready and scope_ready) else "warming")


def clear_lexicon_cache() -> None:
    """测试/管理页用：清空本进程词典（等价于 invalidate_lexicon）。"""
    invalidate_lexicon()


# ══════════════════════════════════════════════════════════════════════
# 判定内核
# ══════════════════════════════════════════════════════════════════════

def _norm(question: str) -> str:
    """剥空白/标点/符号后的 lowercase 文本（emoji 与标点一并剥掉）。"""
    return _PUNCT_RE.sub("", (question or "").lower())


def _only_ack(norm: str) -> bool:
    """整句是否完全由寒暄/应答词拼接而成（贪心最长匹配，剥不完即不是纯寒暄）。"""
    if not norm or len(norm) > 24:
        return False
    i = 0
    while i < len(norm):
        for ln in _ACK_LENS:
            if norm[i:i + ln] in _ACK_TOKENS:
                i += ln
                break
        else:
            return False
    return True


def _match_entities(q_low: str, terms: list[str]) -> list[str]:
    """返回命中的业务实体词（最多 5 个，够用于归因与复盘，不做绑定）。"""
    hits: list[str] = []
    for t in terms:
        if t and t.lower() in q_low:
            hits.append(t)
            if len(hits) >= 5:
                break
    return hits


def _att(obj: Any, name: str, default: Any = None) -> Any:
    """同时支持 dict 与对象形式的取值（policy 在评测里是替身对象）。"""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def resolve_gate_mode(policy: Any = None, force_agent: bool = False) -> str:
    """模式解析：`force_agent > AS-BOT(persona.intent_gate) > env INTENT_GATE_MODE > shadow`。

    AS-BOT 级覆盖走既有配置载体（`adh_as_bots.persona` JSON，键 `intent_gate`）：
    persona 只被 prompt_composer 的白名单键渲染进系统提示词，未知键不会漏进 LLM 视野。
    """
    if force_agent:
        return MODE_OFF
    raw = ""
    as_bot = _att(policy, "as_bot", None)
    if isinstance(as_bot, dict):
        raw = str(((as_bot.get("persona") or {}).get("intent_gate")) or "").strip().lower()
    if not raw:
        raw = str(getattr(_cfg, "INTENT_GATE_MODE", MODE_SHADOW) or MODE_SHADOW).strip().lower()
    if raw not in MODES:
        # 配错值不能悄悄按"关"处理（那会让拦截凭空消失），也不能按最激进档跑：
        # 显式回落 shadow 并留日志，评测与监控能分辨。
        logger.warning("[IntentGate] 未知模式 %r，回落 shadow", raw)
        return MODE_SHADOW
    return raw


def _decision(*, mode: str, layer: str, label: str, intercept: bool, reason: str,
              signals: Optional[list[str]] = None, reply: str = "",
              gate_error: str = "", latency_ms: int = 0) -> dict:
    return {
        "mode": mode,
        "layer": layer,
        "label": label,
        "intercept": bool(intercept),
        # shadow 下不改变行为：处理方仍是 agent，判定结论只作为观测/落库快照
        "handled_by": HANDLED_GATE if (intercept and mode == MODE_ENFORCE) else HANDLED_AGENT,
        "reason": reason,
        "signals": signals or [],
        "reply": reply,
        "fastpath": None,   # Phase 2：确定性取数快路径接口位（本版恒为 None）
        "latency_ms": latency_ms,
        "gate_error": gate_error,
    }


def route_intent(question: str, *, ctx: Any = None, history: Optional[list] = None,
                 attachments: Optional[list] = None, task_binding: Optional[dict] = None,
                 force_agent: bool = False, policy: Any = None, mode: Optional[str] = None,
                 lexicon_terms: Optional[list[str]] = None) -> dict:
    """判定本轮输入的去向。纯判定、无副作用（不写库、不起会话）。

    判定顺序（"必须走模型"先于"可以本地"，任何不确定即放行）：
      1 BYPASS  force_agent / 有附件 / 任务绑定会话 / mode=off → 直接 agent
      2 延续    指代/承接词，或（有历史 + 无业务命中 + 含疑问 + 未命中本地话术）→ agent
      3 L0      空/噪声、纯寒暄、涉密索要且无业务命中 → local
      4 L1      业务实体命中 / 取数句式 / 时间 token / 功能动作词 → agent
      5 本地话术 能力询问、使用说明 → local
      6 默认    ambiguous → agent

    词典未就绪（warming）时 entity 命中集为空，只跑 L0 与模板档，依旧 fail-open。
    """
    mode = (mode or resolve_gate_mode(policy=policy, force_agent=force_agent))
    t0 = time.monotonic()

    def _ms() -> int:
        return int((time.monotonic() - t0) * 1000)

    # 1) BYPASS —— 附件与任务绑定会话（如本体生成）永远交 agent
    if force_agent:
        return _decision(mode=mode, layer="agent", label=L_AMBIG, intercept=False,
                         reason="前端显式要求深度分析（force_agent），跳过漏斗", latency_ms=_ms())
    if attachments:
        return _decision(mode=mode, layer="agent", label=L_AMBIG, intercept=False,
                         reason="本轮携带附件，必须由执行层处理",
                         signals=["has_attachments"], latency_ms=_ms())
    if task_binding:
        return _decision(mode=mode, layer="agent", label=L_AMBIG, intercept=False,
                         reason="任务绑定会话（服务端注入 task_binding）",
                         signals=["task_binding"], latency_ms=_ms())
    if mode == MODE_OFF:
        return _decision(mode=mode, layer="agent", label=L_AMBIG, intercept=False,
                         reason="漏斗未启用（mode=off）", latency_ms=_ms())

    q_raw = (question or "").strip()
    norm = _norm(q_raw)
    lex_state = "ready"
    try:
        ds_id = int(getattr(ctx, "datasource_id", 0) or 0)
        user_id = int(getattr(ctx, "user_id", 0) or 0)
        ws_id = int(getattr(ctx, "workspace_id", 0) or 0)
        is_system = False
        if policy is not None:
            from backend.modules.mind.execution.tool_policy import is_system_scope
            is_system = is_system_scope(policy)
        if lexicon_terms is not None:
            terms, lex_state = list(lexicon_terms), "injected"
        else:
            terms, lex_state = lexicon(ds_id, is_system, user_id, ws_id)
        hits = _match_entities(norm, terms)
    except Exception as exc:  # noqa: BLE001 — 判定失败必须放行，但错因要可见（不静默）
        logger.warning("[IntentGate] 判定异常，本次放行 agent: %s", exc)
        return _decision(mode=mode, layer="agent", label=L_AMBIG, intercept=False,
                         reason=f"判定异常，已放行 agent: {exc}",
                         gate_error=str(exc)[:300], latency_ms=_ms())

    q_low = q_raw.lower()
    signals: list[str] = [f"entity_hit:{h}" for h in hits] + [f"lexicon:{lex_state}"]
    has_history = any(isinstance(h, dict) and (h.get("content") or h.get("reply"))
                      for h in (history or []))
    local_tpl = bool(_HELP_RE.search(q_low) or _CAPABILITY_RE.search(q_low)
                     or _only_ack(norm) or not norm)
    # 涉密索要只有在"无任何业务实体命中"时才本地拒答：
    # "查一下昨天访问量"这类正常问题不得被拒；带业务对象的问题交给 agent + 护栏裁决。
    # 词典首次装载未完成（warming）时这个前提无法核实，本轮不拒（宁可多花一个回合）。
    probe_hit = (bool(_PROBE_SECRET_RE.search(q_low)) and not hits
                 and lex_state != "warming")
    # 2) 多轮延续 —— 本地答必然失真（模型上下文里才有上一轮）。
    # 本地话术模板（"这个怎么用"/"你能做什么"）里的指代词是**虚指**（指平台本身），
    # 不得当延续信号：否则使用说明类问题会被误判为追问（由评测用例 intent_help_usage 钉住）。
    if (not local_tpl) and (_FOLLOWUP_HEAD_RE.match(q_raw) or any(r in q_raw for r in _FOLLOWUP_REFS)):
        label, reason = L_FOLLOWUP, "含承接/指代词，属多轮延续，必须交模型"
    elif has_history and not hits and not local_tpl and _QUESTION_RE.search(q_raw):
        label, reason = L_FOLLOWUP, "有会话历史且本轮无业务命中，疑似追问"
    else:
        label, reason = "", ""
    if label:
        return _finish(_decision(mode=mode, layer="L1", label=label, intercept=False,
                                 reason=reason, signals=signals, latency_ms=_ms()))

    # 3) L0 强本地
    if not norm or (len(norm) < 2 and not hits):
        return _finish(_decision(mode=mode, layer="L0", label=L_EMPTY, intercept=True,
                                 reason="无可读内容（空/标点/符号）",
                                 signals=signals + ["empty_input"], latency_ms=_ms()))
    if _only_ack(norm):
        return _finish(_decision(mode=mode, layer="L0", label=L_ACK, intercept=True,
                                 reason="整句由寒暄/应答词构成，无需模型",
                                 signals=signals + ["ack_only"], latency_ms=_ms()))
    if probe_hit:
        return _finish(_decision(mode=mode, layer="L0", label=L_PROBE, intercept=True,
                                 reason="索要涉密信息且无业务实体命中，按黑盒口径本地拒答",
                                 signals=signals + ["secret_probe"], latency_ms=_ms()))

    # 4) L1 业务信号 —— 命中即放行 agent（本版不代做取数）
    if hits:
        return _finish(_decision(mode=mode, layer="L1", label=L_DATA, intercept=False,
                                 reason="命中已建模业务实体，交 agent 取数",
                                 signals=signals, latency_ms=_ms()))
    if _ACTION_WRITE_RE.search(q_low):
        return _finish(_decision(mode=mode, layer="L1", label=L_WRITE, intercept=False,
                                 reason="含平台功能动作，须走权限码把关与审计",
                                 signals=signals + ["action_write"], latency_ms=_ms()))
    if _AGG_QUERY_RE.search(q_low) or _TIME_TOKEN_RE.search(q_low):
        return _finish(_decision(mode=mode, layer="L1", label=L_DATA, intercept=False,
                                 reason="含取数句式或时间范围，疑似业务取数",
                                 signals=signals + ["agg_pattern", "time_pattern"],
                                 latency_ms=_ms()))

    # 5) 本地话术（窄口径）
    if _CAPABILITY_RE.search(q_low):
        return _finish(_decision(mode=mode, layer="L1", label=L_CAPABILITY, intercept=True,
                                 reason="能力/授权面询问，服务端权威清单可本地作答",
                                 signals=signals + ["capability_tpl"], latency_ms=_ms()))
    if _HELP_RE.search(q_low):
        return _finish(_decision(mode=mode, layer="L0", label=L_HELP, intercept=True,
                                 reason="使用说明类问题，本地固定话术作答",
                                 signals=signals + ["help_tpl"], latency_ms=_ms()))

    # 6) 默认放行
    return _finish(_decision(mode=mode, layer="agent", label=L_AMBIG, intercept=False,
                             reason="判不准去向，fail-open 交 agent", signals=signals,
                             latency_ms=_ms()))


def _finish(decision: dict) -> dict:
    """延迟预算护栏：超时不是"小事"，它会把首字延迟转嫁给用户，必须可见。"""
    limit = float(getattr(_cfg, "INTENT_GATE_MAX_LATENCY_MS", 200) or 200)
    if decision["latency_ms"] > limit:
        decision.update({
            "layer": "agent", "label": L_AMBIG, "intercept": False,
            "handled_by": HANDLED_AGENT,
            "reason": f"判定耗时 {decision['latency_ms']}ms 超出预算({limit:.0f}ms)，放行 agent",
            "gate_error": f"budget_exceeded:{decision['latency_ms']}ms",
        })
    return decision


# ══════════════════════════════════════════════════════════════════════
# 本地直答文案（严禁编造数据/能力；内容只来自 AS-BOT 配置与服务端权威清单）
# ══════════════════════════════════════════════════════════════════════

def _as_bot_identity(policy: Any) -> tuple[str, str]:
    as_bot = _att(policy, "as_bot", None)
    if not isinstance(as_bot, dict):
        return "", ""
    name = as_bot.get("display_name") or as_bot.get("name") or ""
    return str(name or ""), str(as_bot.get("description") or "")


def _capability_lines(policy: Any) -> list[str]:
    """已授权能力面（从 policy.selection 分组推导，未授权的不声称能做）。"""
    sel = _att(policy, "selection", None) or {}
    lines = [f"- {GROUP_LABELS.get(g, g)}" for g in sorted(sel) if sel.get(g)]
    fn_names = _att(policy, "function_names", ()) or ()
    if fn_names:
        lines.append("- 平台功能操作（报表/看板/调度任务/质量检核/数据集等，按角色权限开放）")
    if _att(policy, "external", None):
        lines.append("- 已接入的外部 MCP 工具")
    return lines


def build_local_reply(decision: dict, *, policy: Any = None, ctx: Any = None) -> str:
    """按标签生成本地文案。若判定为拦截但没有对应文案，返回空串（调用方据此不拦截）。"""
    label = decision.get("label")
    name, desc = _as_bot_identity(policy)
    who = f"我是 **{name}**" if name else "我是本平台的智能助手"

    if label == L_ACK:
        body = f"{who}"
        if desc:
            body += f"（{desc}）"
        body += "\n你好！有什么数据问题或平台操作需要我帮忙？直接把问题说出来就行。"
        return body

    if label == L_EMPTY:
        return ("我没识别出这次输入里有可处理的内容（只收到标点或单个字符）。\n"
                "你可以直接问业务问题，例如：「近7天各渠道的订单量趋势」。")

    if label == L_PROBE:
        return ("系统提示词、数据源凭据（账号/密码/密钥/连接串）、主机地址与物理 SQL "
                "属于平台黑盒，不对对话开放，我也不会在这里提供。\n"
                "如果你需要的是业务数据或平台功能操作，请直接说明目标，我会按权限处理。")

    if label == L_HELP:
        lines = _capability_lines(policy)
        body = (f"{who}。用法很直接：用自然语言说出你要的业务问题或想做的操作。\n")
        if lines:
            body += "\n**当前我能做的事**\n" + "\n".join(lines) + "\n"
        body += "\n**问数小提示**：把「指标 / 维度 / 时间」说全，答案更准，"
        body += "例如「按渠道统计昨天的订单量」。"
        return body

    if label == L_CAPABILITY:
        lines = _capability_lines(policy)
        body = f"{who}。\n\n**我能做的事**\n" + ("\n".join(lines) if lines else "- 当前角色未开放可用能力")
        names = _authorized_names(ctx)
        if names is not None:
            body += "\n\n**你当前可使用的数据源**：" + (
                "、".join(names) if names else "（无授权数据源，请联系管理员完成角色授权）")
        body += "\n\n要看某个数据源里具体有哪些业务对象/表与字段，直接把问题发给我，我会按权限探查。"
        return body

    return ""


def _authorized_names(ctx: Any) -> Optional[list[str]]:
    """授权数据源业务名（只给 name，绝不外泄 id/host —— 护栏 §7）。"""
    if ctx is None:
        return None
    try:
        return _authorized_datasource_names(int(getattr(ctx, "user_id", 0) or 0),
                                            int(getattr(ctx, "workspace_id", 0) or 0))
    except Exception as e:  # noqa: BLE001 — 清单取不到就不附清单，不假装有
        logger.warning("[IntentGate] 授权数据源清单读取失败（本次不附清单）: %s", e)
        return None


# ══════════════════════════════════════════════════════════════════════
# 度量与留痕（旁路：观测未开启即 no-op；落库失败只告警，不影响主链路）
# ══════════════════════════════════════════════════════════════════════

def record_observability(decision: dict, question: str) -> None:
    """记录 intent_route span；拦截回合还要把 trace 的处理方式标为 intent_gate。"""
    try:
        from backend import observability
        observability.record_span(
            kind="intent_route", name=str(decision.get("label") or L_AMBIG),
            status="local" if decision.get("intercept") else "agent",
            duration_ms=int(decision.get("latency_ms") or 0),
            input_text=(question or "")[:800],
            output_text=json.dumps({
                "mode": decision.get("mode"), "layer": decision.get("layer"),
                "label": decision.get("label"), "reason": decision.get("reason"),
                "signals": decision.get("signals"), "handled_by": decision.get("handled_by"),
                "gate_error": decision.get("gate_error"),
            }, ensure_ascii=False)[:1500],
            error_text=str(decision.get("gate_error") or ""),
        )
        if decision.get("handled_by") == HANDLED_GATE:
            observability.set_handled_by(HANDLED_GATE)
    except Exception as e:  # noqa: BLE001 — 观测不得影响主链路
        logger.debug("[IntentGate] 观测记录失败: %s", e)


def _outcome_of(decision: dict) -> str:
    if decision.get("intercept") and decision.get("mode") == MODE_ENFORCE:
        return "local"
    if decision.get("intercept"):      # shadow：判定为可拦，但行为未变
        return "shadow_local"
    return "agent"


def record_intercept(decision: dict, *, question: str, ctx: Any = None,
                     policy: Any = None, conversation_id: int = 0,
                     force_agent: bool = False) -> None:
    """判定全量落库（可观测未开启时这是唯一记录，也是误拦复盘与阈值迭代的数据源）。

    旁路写入：失败只 warning（不影响取数主链路，fde-evolution §4），但 gate_error
    分支同样落库 —— "网关自己坏了所以全都放行" 必须能在监控里看出来。
    """
    try:
        from backend import observability
        from backend.common.db import execute_write

        as_bot_key = ""
        as_bot = _att(policy, "as_bot", None)
        if isinstance(as_bot, dict):
            as_bot_key = str(as_bot.get("as_bot_key") or "")
        execute_write(
            """INSERT INTO adh_intent_intercepts
               (dt, trace_id, conversation_id, user_id, workspace_id, datasource_id,
                as_bot_key, mode, layer, label, decision, reason, signals,
                question_digest, latency_ms, gate_error, force_agent)
               VALUES (NOW(), %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                observability.get_trace_id() or "",
                int(conversation_id or 0),
                int(getattr(ctx, "user_id", 0) or 0),
                int(getattr(ctx, "workspace_id", 0) or 0),
                int(getattr(ctx, "datasource_id", 0) or 0),
                as_bot_key,
                str(decision.get("mode") or ""), str(decision.get("layer") or ""),
                str(decision.get("label") or ""), _outcome_of(decision),
                str(decision.get("reason") or "")[:500],
                json.dumps(decision.get("signals") or [], ensure_ascii=False)[:1000],
                (question or "")[:500], int(decision.get("latency_ms") or 0),
                str(decision.get("gate_error") or "")[:300], 1 if force_agent else 0,
            ),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[IntentGate] 判定落库失败（不影响本轮处理）: %s", e)


def mark_reviewed(intercept_id: int, reviewed: bool, note: str = "") -> None:
    """误拦复盘标注（管理页用）：记录"这条判定是否站得住"，供阈值迭代取数据。"""
    from backend.common.db import execute_write

    execute_write("UPDATE adh_intent_intercepts SET reviewed=%s, review_note=%s WHERE id=%s",
                  (1 if reviewed else 0, (note or "")[:500], int(intercept_id)))


def purge_intercepts(days: Optional[int] = None) -> int:
    """判定流水滚动清理（内置任务 `intent_intercept_retention` 周期调用）。

    shadow 档每轮都落一行（量级=chat 输入量），不清理会把元库写成只增不减的日志海。
    保留期内**不删已人工复盘的行**（那是阈值迭代的唯一依据）；返回删除行数供任务监控页展示。
    """
    from backend.common.db import execute_write

    if days is None:
        days = int(_env_first("INTENT_GATE_INTERCEPT_RETENTION_DAYS", 90))
    days = max(1, int(days))
    affected = execute_write(
        "DELETE FROM adh_intent_intercepts "
        "WHERE dt < (NOW() - INTERVAL %s DAY) AND reviewed = 0",
        (days,),
    )
    return int(affected or 0)


def _env_first(name: str, default: int) -> int:
    """配置只从 backend/common/config.py 读；本项不进入热路径，仅在周期任务里取。"""
    import os

    raw = (os.getenv(name) or "").strip()
    if raw.isdigit():
        return int(raw)
    return int(getattr(_cfg, "INTENT_GATE_INTERCEPT_RETENTION_DAYS", default) or default)
