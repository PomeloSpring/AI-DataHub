"""物化节点执行器 — 数据集 → 已登记数据源表（定时物化的唯一执行核心）。

定位（`data-sync-engineering.md`）：
- 本通道属**数据工程**：读授权数据 → 计算 → 写授权表，不向人或 LLM 返回数据行；
- 因此不套交互式取数的 200/2000 行默认上限，改用**显式预算**（超预算即拒），
  绝不静默截断（截断后报成功 = 错数）；
- 取数仍走统一治理口 `governed_execute`（敏感屏蔽/RLS/RBAC/审计），写入走
  `target_writer.write_dataframe_atomic`（staging + 原子切换 + 目标侧对账）。

任务配置（DAG 节点 config；单任务物化 = 只含一个该节点的 DAG）::

    {
      "dataset": "数据集名",                 # 选择框（业务名，服务端解析 id）
      "target_datasource": "目标数据源名",   # 选择框（adh_datasources.name）
      "target_table": "目标表名",            # 必须已存在（不隐式建表）
      "write_mode": "overwrite",             # overwrite | append | partition_overwrite
      "partition": {                         # 仅 partition_overwrite 必填
        "column": "dt",                      # 目标/口径日期列（用于分区对账）
        "date_arg": "dt",                    # 数据集参数名（按日期渲染，必须已声明）
        "target_partition": "p{yyyyMMdd}", # Doris 分区名模板（Doris 必填）
        "grain": "day",                      # day|month|year（回看单位/分区键随粒度）
        "lookback_days": 1                   # 回看格数（天/月/年，含当前格；迟到数据重跑）
      },
      "args": {"地区": "全部"},               # 固定参数（必须与数据集声明一致）
      "site": "cn",                          # 可选：站点业务名（多站路由）
      "batch_size": 1000,
      "budget": {"max_rows": 200000, "max_seconds": 1800}
    }

失败一律抛 `NodeExecutionError`（带 error_code），由 DAG 执行器记 failed：
不写半态（staging 未切换即视为未生效）、不拿写入回报当成功依据。

多分区回看（lookback_days>1）的提交边界：**每个分区各自原子提交**，中途失败
不会留下半个分区；已提交的日期不会回滚（按业务日期幂等，重跑同名分区覆盖）。
失败信息会带上已完成的日期列表，避免“整份回滚”的误解。

敏感字段边界（诚实记账）：读侧沿用统一治理口（`block` 剔除、`mask` 脱敏），
因此 mask 列落库为脱敏后的值；若要"数仓存原值"必须另立特权读取通道，
本模块不做静默绕过（不得以物化为名绕过敏感基线）。
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone

from backend.modules.flow.dag.write_gate import WriteBusy

logger = logging.getLogger(__name__)

DEFAULT_MAX_ROWS = 200_000
DEFAULT_MAX_SECONDS = 1800
DEFAULT_LOOKBACK_DAYS = 1
MAX_BATCH_SIZE = 20_000
RUNTIME_TZ = timezone(timedelta(hours=8))  # 与调度默认时区 Asia/Shanghai 同口径

WRITE_MODES = ("overwrite", "append", "partition_overwrite")
# 物化时间粒度：day=逐日 / month=逐月（每月 1 日）/ year=逐年（每年 1 月 1 日）。
# 分档意义：日档服务"近 30 天"、月档服务"当月/近 12 月"、年档服务"本年累计"；
# 三档各自按自己的分区键覆盖，互不覆盖。回看单位随粒度变化（天/月/年）。
GRAINS = ("day", "month", "year")
GRAIN_LOOKBACK_LIMIT = {"day": 366, "month": 120, "year": 30}
GRAIN_LOOKBACK_LABEL = {"day": "天数", "month": "月数", "year": "年数"}
# 显式窗口（历史初始化 / 分段补数）单次运行的格数上限，约合日档 10 年。
# 与 GRAIN_LOOKBACK_LIMIT 分开：日常回看是“小窗口每次重刷”，历史初始化是
# “大窗口一次跑完”，两者不是同一个夹取口径，混用会要么跑不了历史、要么
# 一次误填就排上千个分区把任务池拖垮。
WINDOW_CELLS_LIMIT = 3660


class MaterializeError(RuntimeError):
    """物化节点失败（error_code 可诊断；原始细节仅进服务端日志）。"""

    def __init__(self, message: str, error_code: str = "MATERIALIZE_FAILED"):
        self.error_code = error_code
        super().__init__(message)


def _today() -> date:
    return datetime.now(RUNTIME_TZ).date()


def int_or_default(value, default: int) -> int:
    """只把"未配置"（None/空串）当作默认值；显式 0/负数必须由调用方回报。

    早期写 `int(x or default)` 会把配成 0 的预算/批量静默吃成默认值，
    管理员以为任务被收紧，实际跑的是默认口径（no-silent-degradation §1）。
    校验器（dag_validator）与执行期共用本函数，避免两套容错口径漂移。
    """
    if value is None or value == "":
        return default
    return int(value)


def _run_dates(lookback_days: int, grain: str = "day") -> list[date]:
    """回看窗口的日期序列（升序），grain 决定“一格”的长度。

    - day  ：逐日，返回最近 N 天；
    - month：逐月，返回最近 N 个月的每月 1 日（当前月也返回 1 日 → 每天刷新当月分区）；
    - year ：逐年，返回最近 N 年的每年 1 月 1 日（当年也返回 1 月 1 日）。
    越界不静默夹取：范围由调用方先校验并给可读错误（口径见 dag_validator）。
    """
    grain = str(grain or "day").strip().lower()
    if grain not in GRAINS:
        raise ValueError(f"物化粒度须为 {'/'.join(GRAINS)} 之一（收到 {grain}）")
    days = int(lookback_days)
    limit = GRAIN_LOOKBACK_LIMIT[grain]
    if not 1 <= days <= limit:
        raise ValueError(f"回看{GRAIN_LOOKBACK_LABEL[grain]}须在 1~{limit} 之间（收到 {days}）")
    end = _today()
    if grain == "day":
        return [end - timedelta(days=offset) for offset in range(days - 1, -1, -1)]
    if grain == "month":
        base = end.replace(day=1)
        out: list[date] = []
        for offset in range(days - 1, -1, -1):
            month_index = base.month - offset
            year = base.year + (month_index - 1) // 12
            out.append(date(year, (month_index - 1) % 12 + 1, 1))
        return out
    return [date(end.year - offset, 1, 1) for offset in range(days - 1, -1, -1)]


def _parse_date(value, field: str) -> date:
    """严格解析 YYYY-MM-DD（不接受自然语言/宽松格式）。"""
    try:
        return date.fromisoformat(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 须为 YYYY-MM-DD 日期（收到 {value!r}）") from exc


def _align_grain(value: date, grain: str) -> date:
    """对齐到该粒度的「格起点」（天=当天 / 月=月初 / 年=年初）。"""
    if grain == "month":
        return value.replace(day=1)
    if grain == "year":
        return date(value.year, 1, 1)
    return value


def _next_cell(value: date, grain: str) -> date:
    """下一格起点（天 +1 天 / 月 +1 月 / 年 +1 年）。"""
    if grain == "month":
        return date(value.year + (1 if value.month == 12 else 0),
                    1 if value.month == 12 else value.month + 1, 1)
    if grain == "year":
        return date(value.year + 1, 1, 1)
    return value + timedelta(days=1)


def _prev_cell(value: date, grain: str) -> date:
    """上一格起点（天 -1 天 / 月 -1 月 / 年 -1 年）。"""
    if grain == "month":
        return date(value.year - (1 if value.month == 1 else 0),
                    12 if value.month == 1 else value.month - 1, 1)
    if grain == "year":
        return date(value.year - 1, 1, 1)
    return value - timedelta(days=1)


def _overwrite_window(grain: str, lookback_days,
                      run_params: dict) -> tuple[date | None, date | None, dict]:
    """整表覆盖的**整个回看窗口** `[start, end)`（不是单格）。

    与 `partition_overwrite` 的「逐格提交」不同：整表覆盖一次写全窗口，窗口若
    退化成单格，后跑的那一格会把其它期全部抹掉（表里只剩一格数据）。
    窗口来源：显式 `start_date/end_date`（历史回填）优先，否则从当前格往回数
    `lookback_days` 格（含当前格）。
    """
    grain_key = str(grain or "day").strip().lower()
    if grain_key not in GRAINS:
        raise ValueError(f"物化粒度须为 {'/'.join(GRAINS)} 之一（收到 {grain_key}）")
    selected = run_params.get("grains")
    if selected:
        chosen = [str(g).strip().lower() for g in selected]
        if grain_key not in chosen:
            return None, None, {
                "mode": "skipped", "grain": grain_key,
                "reason": f"本次运行只选择了 {chosen} 粒度（未包含 {grain_key}）",
            }
    start_raw, end_raw = run_params.get("start_date"), run_params.get("end_date")
    if start_raw or end_raw:
        end_cell = _align_grain(_parse_date(end_raw, "end_date") if end_raw else _today(),
                                grain_key)
        if start_raw:
            begin = _align_grain(_parse_date(start_raw, "start_date"), grain_key)
        else:
            # 只给 end：按节点声明的回看格数向前推（整表覆盖没有分区水位线可续跑）
            begin = end_cell
            for _ in range(max(0, int_or_default(lookback_days, DEFAULT_LOOKBACK_DAYS) - 1)):
                begin = _prev_cell(begin, grain_key)
        if begin > end_cell:
            raise ValueError(f"起始格 {begin.isoformat()} 晚于结束格 {end_cell.isoformat()}")
        cells = 1
        cursor = begin
        while cursor < end_cell:
            cursor = _next_cell(cursor, grain_key)
            cells += 1
        return begin, _next_cell(end_cell, grain_key), {
            "mode": "history", "grain": grain_key, "cells": cells,
            "start": begin.isoformat(), "end": end_cell.isoformat(),
        }
    lookback = int_or_default(lookback_days, DEFAULT_LOOKBACK_DAYS)
    limit = GRAIN_LOOKBACK_LIMIT[grain_key]
    if not 1 <= lookback <= limit:
        raise ValueError(
            f"回看{GRAIN_LOOKBACK_LABEL[grain_key]}须在 1~{limit} 之间（收到 {lookback}）")
    end_cell = _align_grain(_today(), grain_key)
    begin = end_cell
    for _ in range(lookback - 1):
        begin = _prev_cell(begin, grain_key)
    return begin, _next_cell(end_cell, grain_key), {
        "mode": "lookback", "grain": grain_key, "lookback_days": lookback,
        "cells": lookback, "start": begin.isoformat(), "end": end_cell.isoformat(),
    }


def window_dates(grain: str, *, start: date | None = None, end: date | None = None,
                 lookback_days: int | None = None,
                 floor: date | None = None) -> list[date]:
    """物化窗口的格序列（升序）。三种模式，优先级从高到低：

    1. **显式区间**（`start_date`/`end_date`）：从 start 对齐格逐格到 end（默认今天）。
       这是唯一能“用 DAG 自己跑历史”的入口——回看只能从今天往回数，表达不了历史；
    2. **续跑**（只给 end，不给 start）：从 `floor`（上次成功位置/水位线）的下一格起。
       历史初始化跑到一半失败，下次不必从头再来；
    3. **回看**（都不给）：从今天往回 `lookback_days` 格（日常调度口径，行为不变）。

    越界不静默夹取：超过 `WINDOW_CELLS_LIMIT` 格直接报错要求分段（静默夹取会
    把“跑完了历史”伪装成成功，实际只跑了尾部，属错数）。
    """
    grain = str(grain or "day").strip().lower()
    if grain not in GRAINS:
        raise ValueError(f"物化粒度须为 {'/'.join(GRAINS)} 之一（收到 {grain}）")
    if start is None and end is None and floor is None:
        return _run_dates(int_or_default(lookback_days, DEFAULT_LOOKBACK_DAYS), grain)

    end_cell = _align_grain(end or _today(), grain)
    if start is not None:
        begin = _align_grain(start, grain)
    elif floor is not None:
        # 成功格本身已刷过，续跑从下一格开始（当日重刷由回看窗口负责）
        begin = _next_cell(_align_grain(floor, grain), grain)
    else:
        raise ValueError(
            "未指定起始日期，且没有可续跑的成功位置（水位线为空）："
            "首次历史初始化必须显式给出 start_date")

    cells: list[date] = []
    cursor = begin
    while cursor <= end_cell:
        cells.append(cursor)
        if len(cells) > WINDOW_CELLS_LIMIT:
            raise ValueError(
                f"单次窗口超过 {WINDOW_CELLS_LIMIT} 格（{grain}）："
                "请用 start_date/end_date 分段执行，平台不会静默截断")
        cursor = _next_cell(cursor, grain)
    return cells


def _watermark_position(watermark_key: str | None) -> date | None:
    """「上一次执行成功的位置」（水位线）；没有记录返回 None。

    只在**续跑模式**（只给 end_date、不给 start_date）下读它：日常回看绝不能
    拿它当跳过依据——月/年档的“当前格”每天都要重算（当天数据在长），
    一旦把已完成的格当成“不用再跑”，图表就会停在昨天的快照上。
    """
    if not watermark_key:
        return None
    from backend.modules.flow.dag.dag_service import dag_service

    value = dag_service.get_watermark(watermark_key)
    return _parse_date(value, "水位线") if value else None


def _resolve_window(grain: str, partition: dict, run_params: dict,
                    watermark_key: str | None) -> tuple[list[date], dict]:
    """把「运行参数 / 水位线 / 回看格数」收敛成一条窗口，并给出可审计的摘要。"""
    grain_key = str(grain or "day").strip().lower()
    # 本次运行只跑指定粒度：历史初始化时月/年档回塑多年，日档只服务近月视图，
    # 不区分粒度会把日档也从起始日逐天扫一遍（几千格、必超预算）。
    # 这是“本次跳过”而非改节点配置：明确回报 skipped + 原因，不静默。
    selected = run_params.get("grains")
    if selected:
        chosen = [str(g).strip().lower() for g in selected]
        if grain_key not in chosen:
            return [], {
                "mode": "skipped", "grain": grain_key,
                "reason": f"本次运行只选择了 {chosen} 粒度（未包含 {grain_key}）",
            }
    start_raw = run_params.get("start_date")
    end_raw = run_params.get("end_date")
    if start_raw or end_raw:
        start = _parse_date(start_raw, "start_date") if start_raw else None
        end = _parse_date(end_raw, "end_date") if end_raw else None
        floor = None
        if start is None:
            floor = _watermark_position(watermark_key)
            if floor is None:
                raise ValueError(
                    "运行只指定了 end_date（续跑），但没有可续跑的成功位置（水位线为空）；"
                    "首次初始化请显式给出 start_date")
        cells = window_dates(grain, start=start, end=end, floor=floor)
        return cells, {
            "mode": "history" if start is not None else "resume",
            "start": (start or floor).isoformat() if (start or floor) else None,
            "end": (end or _today()).isoformat(),
            "cells": len(cells),
            "resumed_from": floor.isoformat() if floor else None,
        }
    lookback = int_or_default(partition.get("lookback_days"), DEFAULT_LOOKBACK_DAYS)
    cells = window_dates(grain, lookback_days=lookback)
    return cells, {"mode": "lookback", "cells": len(cells), "lookback_days": lookback}


def _partition_name(template: str, value: date, extra: dict | None = None) -> str:
    """分区名模板渲染（{yyyyMMdd} / {yyyy-MM-dd} / {date} + 固定分区列值）。

    多站同表时分区名必须带站点（如 `p_r1_{yyyyMMdd}`）——否则两站写同名分区，
    后跑的 OVERWRITE 会把前一站整分区替掉（数据消失且无报错）。
    """
    name = (str(template or "")
            .replace("{yyyyMMdd}", value.strftime("%Y%m%d"))
            .replace("{yyyy-MM-dd}", value.strftime("%Y-%m-%d"))
            .replace("{date}", value.strftime("%Y-%m-%d")))
    for key, fixed_value in (extra or {}).items():
        name = name.replace("{" + str(key) + "}", str(fixed_value))
    return name


def _resolve_dataset(name: str, identity: dict) -> dict:
    from backend.modules.viz.services.dataset_service import (
        _assert_dataset_visible, get_dataset_by_name,
    )
    ds = get_dataset_by_name(str(name or "").strip())
    if not ds:
        raise MaterializeError(f"数据集 '{name}' 不存在或已停用", "DATASET_MISSING")
    if str(ds.get("status") or "") != "active":
        raise MaterializeError(f"数据集 '{name}' 已停用，不能物化", "DATASET_INACTIVE")
    # 归属/可见性：属主、admin 或按角色共享给该用户，否则拒绝（fail-closed）
    _assert_dataset_visible(ds, identity, action="物化")
    if not str(ds.get("sql_query") or "").strip():
        raise MaterializeError(
            f"数据集 '{name}' 未配置执行 SQL，无法物化；"
            "语义型数据集的行数上限由语义层编译决定，请改用 SQL 型数据集做物化",
            "DATASET_NOT_SQL")
    return ds


def _assert_target_authorized(datasource_name: str, user_id: int, workspace_id: int) -> dict:
    """目标数据源必须已登记，且任务属主的角色对该源有授权（fail-closed）。"""
    from backend.common.db import get_datasource_by_name
    from backend.core.role_service import role_service

    source = get_datasource_by_name(str(datasource_name or ""))
    if not source:
        raise MaterializeError(
            f"目标数据源 '{datasource_name}' 未登记，请先在数据源管理中登记",
            "TARGET_DATASOURCE_MISSING")
    allowed = role_service.get_user_allowed_datasources(int(user_id or 0), int(workspace_id or 0))
    if int(source["id"]) not in {int(x) for x in allowed or []}:
        raise MaterializeError(
            f"任务属主的角色未被授权写入目标数据源 '{datasource_name}'，物化已拒绝",
            "TARGET_UNAUTHORIZED")
    return source


def _declared_params(ds: dict) -> set[str]:
    """数据集声明的可传参数集（逻辑参数 key ∪ 模板变量名）。

    与前端选择项、编译期 `declared_names` 同源（`param_summary`）：只用模板变量名
    会误拒“多变量聚合为一个逻辑参数”的合法 key。
    """
    from backend.semantics.param_plan import param_summary

    names: set[str] = set()
    try:
        for group in param_summary(ds) or []:
            if group.get("key"):
                names.add(str(group["key"]))
            for target in group.get("vars") or []:
                if target.get("var"):
                    names.add(str(target["var"]))
    except Exception as exc:  # noqa: BLE001 摘要算不出来不应阻断：交由编译期报错
        logger.warning("[Materialize] 数据集参数摘要解析失败: %s", exc)
    return names


def _compile_sql(ds: dict, args: dict, max_rows: int) -> tuple[str, list[str], int]:
    """按预算编译数据集正文（预算外多取 1 行用于识别"超预算"）。"""
    from backend.semantics.dataset_compiler import (
        DatasetCompileError, MATERIALIZE_MAX_LIMIT, compile_dataset,
    )

    budget_rows = max(1, min(int(max_rows), MATERIALIZE_MAX_LIMIT))
    try:
        compiled = compile_dataset(
            ds,
            args=args,
            limit=budget_rows + 1,
            inner_limit=0,          # 不补 5000 保底：由外层预算 LIMIT 兜住
            max_limit=budget_rows + 1,
        )
    except DatasetCompileError as exc:
        raise MaterializeError(str(exc), "DATASET_COMPILE_FAILED") from exc
    return compiled.sql, list(compiled.resolved_tables or []), budget_rows


def _blocked_columns(source_id: int, workspace_id: int, tables: list[str]) -> set[str]:
    """该源这些表生效的合规屏蔽列（`adh_sensitive_fields`，全局 ∪ 指定，admin 同样生效）。

    策略读不到直接上抛：宁可拒跑，也不能在"不知道哪些列被封"的情况下把数据搬进数仓。
    """
    from backend.core.enforcer import permission_enforcer

    blocked: set[str] = set()
    for name in tables:
        cols = permission_enforcer.get_blocked_columns(
            int(workspace_id or 0), int(source_id or 0), str(name)) or []
        blocked.update(str(c).strip().lower() for c in cols if c)
    return blocked


def _wildcard_projection(tree) -> bool:
    """是否存在「直接打在物理表上的投影通配」（`FROM t` + `SELECT *` / `t.*`）。

    两个不能误判的形态：
    - `COUNT(*)`：星在函数内部，不是拉全表列；
    - `SELECT * FROM (子查询)`：列由内层决定，内层自己会被单独检查（编译器就是
      这么包一层的：`SELECT <dims>,<measures> FROM (正文) AS _ds_base`）。
    只有“物理表当 FROM，且投影是星”时，才算无法证明敏感列不会被带进来。
    """
    from sqlglot import exp

    ctes = {str(c.alias_or_name).lower() for c in tree.find_all(exp.CTE)}
    for select in tree.find_all(exp.Select):
        star = False
        for proj in select.expressions:
            node = proj.this if isinstance(proj, exp.Alias) else proj
            if isinstance(node, exp.Star) or (
                    isinstance(node, exp.Column) and isinstance(node.this, exp.Star)):
                star = True
                break
        if not star:
            continue
        source = getattr(select.args.get("from_") or select.args.get("from") or None,
                         "this", None)
        if isinstance(source, exp.Table) and str(source.name).lower() not in ctes:
            return True
    return False


def _assert_sensitive_columns_excluded(sql: str, source_id: int,
                                       blocked: set[str], dataset: str) -> None:
    """物化口径不得引用 block 列：**编译期拒绝**，不是"落库后再脱敏"。

    与读侧的区别：读侧少这一列只是少一列；物化里少一个参与 GROUP BY 的列会
    直接把分组与口径改掉，并且跑完就报"成功"——那是静默改数（错数），比拒跑糟得多。
    `mask` 列不在此拦：数仓存原值、脱敏留在读侧编译期注入（data-sync-engineering §5）。
    """
    if not blocked:
        return
    from backend.common.db.datasource_db import get_datasource_by_id
    from backend.semantics.sql_guard import dialect_of_db_type, parse_readonly
    from sqlglot import exp

    db_type = (get_datasource_by_id(int(source_id or 0)) or {}).get("db_type") or "mysql"
    try:
        tree = parse_readonly(sql, dialect_of_db_type(db_type))
    except Exception as exc:  # noqa: BLE001 解析失败不得放行（无法证明"没用敏感列"）
        raise MaterializeError(
            f"数据集 '{dataset}' 的物化 SQL 无法解析，不能核验敏感列边界，已拒绝执行：{exc}",
            "SENSITIVE_CHECK_FAILED") from exc

    hit = sorted({str(c.name).lower() for c in tree.find_all(exp.Column)
                  if str(c.name or "").lower() in blocked})
    if hit:
        raise MaterializeError(
            f"数据集 '{dataset}' 的物化口径引用了合规屏蔽列 {hit}（敏感数据已设为禁止使用）。"
            "物化不会替你「剔除该列后继续」——那会静默改变分组与口径；请改口径后重跑",
            "SENSITIVE_COLUMN_BLOCKED")
    if _wildcard_projection(tree):
        raise MaterializeError(
            f"数据集 '{dataset}' 用 `*` 取数，而源表含有合规屏蔽列 {sorted(blocked)}，"
            "无法证明它不会被带进数仓；请把投影列显式列出后重跑",
            "SENSITIVE_COLUMN_BLOCKED")


def _fetch(sql: str, datasource_id: int, identity: dict,
           timeout_sec: int | None = None) -> tuple[list[str], list[dict]]:
    """治理取数（敏感/RLS/RBAC/审计同分析面，一次取回；行数由外层 LIMIT 兜）。

    `timeout_sec` 是该期取数的读超时预算：批处理不能沿用交互式 30s 天花板
    （年档/全量窗口的 UNION 多分支扫描必然超；超时后“稍后重试”永远不好）。
    它仍受 `max_seconds` 总预算与并发闸门约束，不是旁路开关。
    """
    from backend.core.governed_query import governed_execute

    result = governed_execute(
        sql, int(datasource_id or 0) or None,
        int(identity.get("user_id") or 0),
        int(identity.get("workspace_id") or 0),
        identity.get("username") or "",
        timeout_sec=timeout_sec)
    return list(result.get("columns") or []), list(result.get("rows") or [])


def _to_dataframe(columns: list[str], rows: list[dict]):
    import pandas as pd

    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns)


def export_materialize_sql(config: dict, identity: dict,
                          run_params: dict | None = None) -> list[str]:
    """物化节点导出为**真实可执行 SQL**（含窗口参数与逐站展开）。

    为什么需要：导出的脚本要能被人拿去审阅/手工重放，只写一句“取数 SQL 由平台编译”
    等于没有导出。这里复用执行期同一条编译链（`_overwrite_window` / `_resolve_window`
    + `_compile_sql`），保证导出 SQL 与真正跑的那一份同源。

    边界（诚实标注）：
    - 正式执行是 **staging 表 + INSERT OVERWRITE TABLE 原子切换 + 目标侧 COUNT 对账**；
      跨站 SQL 分属不同源库，无法合成一条语句，所以导出的是一组逐站 `INSERT INTO`
      片段（人工重放前请先清空目标表，否则重复行）；
    - `LIMIT` 是执行期行预算（预算+1 用于识别超预算），不是“截断后报成功”。
    """
    ds = _resolve_dataset(config.get("dataset"), identity)
    target_ds = str(config.get("target_datasource") or "").strip()
    target_table = str(config.get("target_table") or "").strip()
    if not target_table:
        raise MaterializeError("物化节点未指定目标表", "CONFIG_INCOMPLETE")
    write_mode = str(config.get("write_mode") or "overwrite").strip().lower()
    if write_mode not in WRITE_MODES:
        raise MaterializeError(f"写入模式不合法: {write_mode}", "CONFIG_INVALID")
    budget = config.get("budget") or {}
    max_rows = int_or_default(budget.get("max_rows"), DEFAULT_MAX_ROWS)
    fixed_args = config.get("args") if isinstance(config.get("args"), dict) else {}
    sites = [str(x).strip() for x in (config.get("sites") or []) if str(x).strip()]
    site_args = config.get("site_args") if isinstance(config.get("site_args"), dict) else {}
    params = dict(run_params or {})

    lines: list[str] = [
        f"-- 物化: 数据集 {ds.get('name')} → {target_ds or '(未指定数据源)'}.{target_table} "
        f"({write_mode})",
    ]
    # 逐格参数：[(说明, {日期参数: 值})]
    cells: list[tuple[str, dict]] = [("整窗口一次写入", {})]
    if write_mode == "partition_overwrite":
        partition = config.get("partition") or {}
        grain = str(partition.get("grain") or "day").strip().lower()
        dates, info = _resolve_window(grain, partition, params, None)
        lines.append(f"-- 分区覆盖: 列 {partition.get('column')} / 日期参数 "
                     f"{partition.get('date_arg')} / 粒度 {grain} "
                     f"→ {len(dates)} 格（{info.get('start')} ~ {info.get('end')}）")
        cells = [(f"日期格 {d.isoformat()}", {str(partition.get('date_arg')): d.isoformat()})
                 for d in dates]
    else:
        window_cfg = config.get("window") or {}
        if window_cfg:
            grain = str(window_cfg.get("grain") or "day").strip().lower()
            lookback = int_or_default(window_cfg.get("lookback_days"), DEFAULT_LOOKBACK_DAYS)
            start, end, info = _overwrite_window(grain, lookback, params)
            if start is None:
                lines.append(f"-- 本次窗口无内容：{info.get('reason') or '未选中该粒度'}")
                return lines
            declared = _declared_params(ds)
            missing = [k for k in ("window_start", "window_end") if k not in declared]
            if missing:
                raise MaterializeError(
                    f"数据集 '{ds.get('name')}' 未声明窗口参数 {missing}",
                    "DATASET_PARAM_MISSING")
            lines.append(f"-- 窗口: [{start.isoformat()}, {end.isoformat()}) 粒度={grain} "
                         f"回看 {info.get('lookback_days', lookback)} 格"
                         f"（mode={info.get('mode')}，整表覆盖一次写全窗口）")
            cells = [("整窗口一次写入",
                      {"window_start": start.isoformat(), "window_end": end.isoformat()})]
        else:
            lines.append("-- 未声明 window：按数据集默认参数编译（单次全窗口取数）")
    lines.append(f"-- 行预算: max_rows={max_rows}（执行器超预算即拒绝，不静默截断）")
    lines.append("-- 正式执行 = staging 表 + INSERT OVERWRITE TABLE 原子切换 + 目标侧 COUNT 对账；")
    lines.append("-- 下列片段为同源取数（跨站 SQL 分属不同源库，逐站 INSERT INTO 便于人工重放，")
    lines.append("-- 重放前请先清空目标表，否则会追加重复行）。")
    lines.append("")

    # 站点参数先全量校验再编译：漏站时不得先编出前几站的 SQL 再报错（半截脚本更危险）
    if sites:
        missing_sites = [s for s in sites if not site_args.get(s)]
        if missing_sites:
            raise MaterializeError(
                f"site_args 缺少站点 {missing_sites} 的参数（导出无法展开该站）",
                "CONFIG_INCOMPLETE")

    site_codes = sites or [""]
    for cell_label, cell_args in cells:
        for site in site_codes:
            extra = dict(site_args.get(site) or {}) if site else {}
            if site and not extra:
                raise MaterializeError(
                    f"site_args 缺少站点 {site} 的参数（导出无法展开该站）", "CONFIG_INCOMPLETE")
            args = {**fixed_args, **extra, **cell_args}
            sql, tables, _ = _compile_sql(ds, args, max_rows)
            header = f"-- {cell_label}" + (f" · 站点 {site}" if site else "")
            if site:
                header += f"（{', '.join(f'{k}={v}' for k, v in extra.items())}）"
            lines.append(header)
            if tables:
                lines.append(f"-- 读取表: {', '.join(tables)}")
            lines.append(f"INSERT INTO `{target_table}`")
            lines.append(sql.strip().rstrip(";") + ";")
            lines.append("")
    return lines


def _write(df, target_ds: str, target_table: str, write_mode: str, run_date,
           partition: dict, target_partition: str, batch_size: int, context: dict,
           writer, timeout_sec: int | None = None) -> dict:
    """调原子写入并把引擎错误归到可诊断错误码（不把失败降级成"看起来成功"）。

    `timeout_sec`：本次写入的读超时预算（= 总预算剩余）。写侧与读侧同一条线，
    不继承交互式 30s：Doris staging + OVERWRITE 大分区在 30s 内完不成，会被客户
    端判成 2013「连接丢失」，而服务端 DDL 已落地，随后整轮都撞 already exists。
    """
    partition_args = None
    if run_date is not None:
        partition_args = {"column": partition.get("column"),
                          "value": run_date.strftime("%Y-%m-%d"),
                          "target_partition": target_partition,
                          # 多站同表：分区键列与固定值一并下递（写入与对账都按站点隔离）
                          "key_columns": list(partition.get("key_columns") or []),
                          "fixed": dict(partition.get("fixed") or {})}
    try:
        return writer(
            df, target_ds, target_table,
            write_mode=write_mode,
            partition=partition_args,
            batch_size=batch_size,
            timeout_sec=timeout_sec,
            context=f"materialize run={context.get('run_id')} node={context.get('node_key')}"
                    + (f" partition={run_date.isoformat()}" if run_date else ""))
    except PermissionError:
        raise
    except WriteBusy as exc:
        # 写闸门排队超预算：目标是好的，只是并发档位已满。保留原处置建议
        # （调 mate_max_concurrency / 错开调度），不混进“写入失败”
        logger.warning("[Materialize] 目标写入排队超预算 target=%s.%s", target_ds, target_table)
        raise MaterializeError(f"{exc}", exc.error_code) from exc
    except Exception as exc:  # noqa: BLE001 统一转可诊断错误码，原始细节进日志
        logger.exception("[Materialize] 写入失败 target=%s.%s mode=%s",
                         target_ds, target_table, write_mode)
        code = "RECONCILE_FAILED" if "对账" in str(exc) else "TARGET_WRITE_FAILED"
        raise MaterializeError(
            f"写入目标表 {target_ds}.{target_table} 失败：{exc}", code) from exc


def run_materialize_node(config: dict, context: dict) -> dict:
    """执行物化节点（单/多日期分区），返回行数、分区与对账结果。"""
    from backend.modules.flow.dag.target_writer import write_dataframe_atomic

    dataset_name = str(config.get("dataset") or "").strip()
    target_ds = str(config.get("target_datasource") or "").strip()
    target_table = str(config.get("target_table") or "").strip()
    write_mode = str(config.get("write_mode") or "overwrite")
    if not dataset_name or not target_ds or not target_table:
        raise MaterializeError("物化节点缺少数据集/目标数据源/目标表", "CONFIG_INCOMPLETE")
    if write_mode not in WRITE_MODES:
        raise MaterializeError(f"物化写入模式不合法: {write_mode}", "CONFIG_INVALID")

    identity = dict(context.get("owner_identity") or {})
    identity["workspace_id"] = int(context.get("workspace_id") or 0)
    if not int(identity.get("user_id") or 0):
        raise MaterializeError("物化任务未认领创建者，无法执行（OWNER_REQUIRED）",
                               "OWNER_REQUIRED")

    budget = config.get("budget") or {}
    max_rows = int_or_default(budget.get("max_rows"), DEFAULT_MAX_ROWS)
    max_seconds = int_or_default(budget.get("max_seconds"), DEFAULT_MAX_SECONDS)
    batch_size = int_or_default(config.get("batch_size"), 1000)
    if max_rows < 1 or max_seconds < 1:
        raise MaterializeError("物化预算必须为正数（行数/秒）", "BUDGET_INVALID")
    if not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise MaterializeError(
            f"批量写入行数须在 1~{MAX_BATCH_SIZE} 之间", "BUDGET_INVALID")

    ds = _resolve_dataset(dataset_name, identity)
    _assert_target_authorized(target_ds, identity["user_id"], identity["workspace_id"])

    from backend.modules.viz.services.dataset_service import resolve_effective_source

    ds_base = ds
    effective_source = resolve_effective_source(
        ds, identity, site=str(config.get("site") or ""))
    ds = {**ds, "datasource_id": effective_source}

    # 多站合并（方案 B）：一个节点算 5 站、一次整表覆盖。只允许 overwrite——
    # 分区覆盖是“一站一格一个分区”，多站写同一分区会互相抹掉。
    sites: list[str] = []
    raw_sites = config.get("sites")
    if raw_sites is not None:
        if not isinstance(raw_sites, list) or not raw_sites:
            raise MaterializeError("sites 须为非空数组（站点业务名）", "CONFIG_INVALID")
        sites = [str(x).strip() for x in raw_sites if str(x).strip()]
        if not sites:
            raise MaterializeError("sites 不能为空", "CONFIG_INVALID")
        if write_mode != "overwrite":
            raise MaterializeError(
                "多站合并写同一张表只支持整表覆盖（write_mode=overwrite）："
                "分区覆盖会让后跑的站点取掉前一站的分区", "CONFIG_INVALID")

    # 每站自己的 SQL 参数（如 `region` 这类“站点自身标识”必须随站变化）。
    # 只给 sites 不给 site_args，5 站会用同一份参数算出同一个 region 值 —— 结果是 5 站
    # 数据全部标成一站，而且跑完报成功（静默错数），所以缺一个站就直接拒跑。
    site_args_cfg: dict[str, dict] = {}
    if sites:
        raw_site_args = config.get("site_args")
        if not isinstance(raw_site_args, dict) or not raw_site_args:
            raise MaterializeError(
                "多站合并必须提供 site_args（各站点自己的 SQL 参数，如 region）："
                "缺失会让所有站点写成同一站", "CONFIG_INCOMPLETE")
        missing_sites = [s for s in sites
                         if not isinstance(raw_site_args.get(s), dict) or not raw_site_args.get(s)]
        if missing_sites:
            raise MaterializeError(
                f"site_args 缺少站点 {missing_sites} 的参数（已提供: {sorted(raw_site_args)}）",
                "CONFIG_INCOMPLETE")
        site_args_cfg = {str(k): dict(v) for k, v in raw_site_args.items()}

    fixed_args = config.get("args") if isinstance(config.get("args"), dict) else {}
    partition = config.get("partition") or {}
    # 本次运行的参数（物化时间窗口 start_date/end_date）：由 dag_executor 从
    # adh_dag_runs.params_json 注入，不写在节点 config 里（同一份编排要兼顾日常与历史）。
    run_params = dict(context.get("run_params") or {})
    dates: list[date] = []
    date_arg = ""
    window_info: dict = {"mode": "none"}
    window_args: dict = {}
    if write_mode == "partition_overwrite":
        date_arg = str(partition.get("date_arg") or "").strip()
        part_col = str(partition.get("column") or "").strip()
        if not date_arg or not part_col:
            raise MaterializeError(
                "分区覆盖必须指定「日期参数名」与「分区/日期列」", "CONFIG_INCOMPLETE")
        declared = _declared_params(ds)
        if date_arg not in declared:
            raise MaterializeError(
                f"数据集 '{dataset_name}' 未声明参数 '{date_arg}'，无法按日期增量物化"
                f"（已声明: {sorted(declared) or '无'}）", "DATASET_PARAM_MISSING")
        grain = str(partition.get("grain") or "day").strip().lower()
        try:
            dates, window_info = _resolve_window(
                grain, partition, run_params, context.get("watermark_key"))
        except (TypeError, ValueError) as exc:
            raise MaterializeError(str(exc), "CONFIG_INVALID") from exc
        if not dates:
            # 续跑已到终点：没有待刷新的格。必须显式回报，绝不能退回 `runs=[None]`
            # 跑一次“无日期”的全表覆盖（那会把分区语义偷换成整表覆盖）。
            return {
                "rows_read": 0, "rows_written": 0,
                "dataset": ds.get("name"), "target": f"{target_ds}.{target_table}",
                "write_mode": write_mode, "partitions": [], "reconciled": True,
                "strategy": "",
                "budget": {"max_rows": max_rows, "max_seconds": max_seconds},
                "window": {**window_info, "skipped": True,
                           "reason": window_info.get("reason")
                           or "窗口内没有待刷新的格（上次成功位置已覆盖到终点）"},
                "last_committed_date": "",
                "skipped": True,
            }
    else:
        # 整表覆盖：一次写全窗口。未声明 window 的节点保持旧行为（单次 SQL 全表覆盖）。
        window_cfg = config.get("window") or {}
        if window_cfg:
            grain = str(window_cfg.get("grain") or "day").strip().lower()
            lookback = int_or_default(window_cfg.get("lookback_days"), DEFAULT_LOOKBACK_DAYS)
            try:
                start, end, window_info = _overwrite_window(grain, lookback, run_params)
            except (TypeError, ValueError) as exc:
                raise MaterializeError(str(exc), "CONFIG_INVALID") from exc
            if start is None:
                return {
                    "rows_read": 0, "rows_written": 0,
                    "dataset": ds.get("name"), "target": f"{target_ds}.{target_table}",
                    "write_mode": write_mode, "partitions": [], "reconciled": True,
                    "strategy": "",
                    "budget": {"max_rows": max_rows, "max_seconds": max_seconds},
                    "window": {**window_info, "skipped": True},
                    "last_committed_date": "", "skipped": True,
                }
            declared = _declared_params(ds_base)
            missing = [k for k in ("window_start", "window_end") if k not in declared]
            if missing:
                raise MaterializeError(
                    f"数据集 '{dataset_name}' 未声明窗口参数 {missing}（已声明: "
                    f"{sorted(declared) or '无'}），整表覆盖无法按窗口取数",
                    "DATASET_PARAM_MISSING")
            window_args = {"window_start": start.isoformat(), "window_end": end.isoformat()}
        elif run_params.get("start_date") or run_params.get("end_date"):
            raise MaterializeError(
                "该物化节点是整表覆盖但未声明 window，运行参数（时间窗口）无处生效："
                "请用节点 config.window（grain + lookback_days）控制窗口", "CONFIG_INVALID")

    started = time.monotonic()
    partitions: list[dict] = []
    total_read = 0
    total_written = 0
    strategies: set[str] = set()
    datasets_tables: set[str] = set()
    last_columns: list[str] = []
    last_df = None
    blocked_cache: dict[int, set[str]] = {}

    runs = dates or [None]
    for run_date in runs:
        context["stop_check"]()
        if time.monotonic() - started > max_seconds:
            raise MaterializeError(
                f"物化超预算：已耗时超过 {max_seconds} 秒（预算即拒，不写半态）；"
                f"已提交分区: {[p['date'] for p in partitions] or '无'}",
                "BUDGET_EXCEEDED")

        args = dict(fixed_args)
        if run_date is not None:
            args[date_arg] = run_date.strftime("%Y-%m-%d")
        args.update(window_args)

        # 每个站点单独取数（同一份数据集口径），合并后**一次整表覆盖**：
        # 分站分别写入会让后跑的站点取掉前一站的数据。
        frames = []
        site_columns: list[str] | None = None
        for site in (sites or [None]):
            context["stop_check"]()
            source_id = int(effective_source or 0)
            site_args = dict(args)
            if site is not None:
                source_id = int(resolve_effective_source(
                    ds_base, identity, site=str(site)) or 0)
                site_args.update(site_args_cfg.get(str(site)) or {})
            ds_site = {**ds_base, "datasource_id": source_id}
            sql, resolved_tables, budget_rows = _compile_sql(ds_site, site_args, max_rows)
            datasets_tables.update(resolved_tables)

            # 敏感边界硬门（编译后、取数前）：block 列一律拒跑，不让"剔除后继续"静默改口径。
            # 按源缓存（同一次运行同源多期/多站不重复查库）。
            if source_id not in blocked_cache:
                blocked_cache[source_id] = _blocked_columns(
                    source_id, int(identity["workspace_id"]), sorted(resolved_tables))
            _assert_sensitive_columns_excluded(
                sql, source_id, blocked_cache[source_id], dataset_name)

            # 单次读超时 = 剩余总预算（至少 1s）：预算即拒同一条线，不让批处理继承
            # 交互式 30s 默认（年档多分支扫描必超）。总预算仍由循环开头的 max_seconds 把关。
            remaining = max(1, int(max_seconds - (time.monotonic() - started)))
            columns, rows = _fetch(sql, source_id, identity, timeout_sec=remaining)
            # 预算即拒：撞到 max_rows+1 就是"还有更多"，绝不可截断入库
            if len(rows) > budget_rows:
                raise MaterializeError(
                    f"物化结果超过行数预算 {budget_rows} 行（读取到 {len(rows)} 行）。"
                    "请缩小口径、加日期分区或提高预算后重跑；平台不会静默截断。"
                    f"已提交分区: {[p['date'] for p in partitions] or '无'}",
                    "BUDGET_EXCEEDED")
            if site_columns is None:
                site_columns = columns
            elif columns != site_columns:
                raise MaterializeError(
                    f"站点 {site} 返回列与其它站不一致（{columns} vs {site_columns}）："
                    "多站合并要求同一份数据集口径", "DATASET_COLUMNS_MISMATCH")
            frames.append(_to_dataframe(columns, rows))

        if len(frames) == 1:
            df = frames[0]
        else:
            import pandas as pd
            df = pd.concat(frames, ignore_index=True)
        # 合并后的总行数才是真正的写入量：单站预算过、合并后超也算超预算
        if len(df) > max_rows:
            raise MaterializeError(
                f"多站合并结果超过行数预算 {max_rows} 行（{len(df)} 行）："
                "请提高预算或缩小窗口；平台不会静默截断", "BUDGET_EXCEEDED")

        last_columns = list(df.columns)
        last_df = df
        rows_read = len(df)
        target_partition = ""
        if run_date is not None:
            target_partition = _partition_name(
                str(partition.get("target_partition") or ""), run_date,
                partition.get("fixed") or {})
        write_result = _write(df, target_ds, target_table, write_mode, run_date,
                             partition, target_partition, batch_size, context,
                             write_dataframe_atomic,
                             timeout_sec=max(1, int(max_seconds - (time.monotonic() - started))))
        total_read += rows_read
        total_written += int(write_result.get("rows_written") or 0)
        strategies.add(str(write_result.get("strategy") or ""))
        partitions.append({
            "date": run_date.isoformat() if run_date else "",
            "rows_read": rows_read,
            "rows_written": int(write_result.get("rows_written") or 0),
            "target_partition": target_partition,
            "target_count": write_result.get("target_count"),
            "reconciled": bool(write_result.get("reconciled")),
        })

    context["stop_check"]()

    # 「最后一次执行成功的位置」：物化节点同样写水位线（不能只有 sync 节点有）。
    # 它是历史初始化续跑的起点（只给 end_date 时从下一格开始），也是运行页
    # “上次成功位置”的展示来源。重刷更旧的区间不会把水位线拉回去
    # （advance_watermark 内部只前进），避免续跑逻辑倒退。
    last_committed = partitions[-1]["date"] if partitions else ""
    if write_mode == "partition_overwrite" and last_committed and context.get("watermark_key"):
        from backend.modules.flow.dag.dag_service import dag_service
        try:
            dag_service.advance_watermark(
                context["watermark_key"], last_committed, total_written)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[Materialize] 水位线推进失败 node=%s", context.get("node_key"))
            raise MaterializeError(
                f"物化已提交分区 {[p['date'] for p in partitions]}（数据已落库），"
                f"但「最后成功位置」记录失败，续跑会从旧位置开始：{exc}",
                "WATERMARK_FAILED") from exc

    # 血缘（旁路）：数据集触及表 → 目标表
    try:
        from backend.modules.flow.dag.node_runners import _record_lineage
        from backend.modules.viz.services.dataset_service import effective_source_name
        _record_lineage(
            f"INSERT INTO `{target_table}` SELECT * FROM ({' UNION '.join(sorted(datasets_tables)) or 'dataset'})",
            effective_source_name(effective_source), context,
            target_datasource_name=target_ds)
    except Exception as exc:  # noqa: BLE001 旁路
        logger.warning("[Materialize] 血缘采集失败（不影响物化结果）: %s", exc)

    # 质量检查与数据产品登记（沿用同步节点口径）
    quality = None
    product = {"registered": False}
    try:
        from backend.modules.flow.dag.node_runners import (
            _register_product_after_write, _run_quality_checks,
        )
        quality = _run_quality_checks(config, context, target_ds, target_table, "materialize")
        from backend.modules.viz.services.dataset_service import effective_source_name
        product = _register_product_after_write(
            last_df, target_ds, target_table, "materialize",
            f"materialize:{context.get('node_key') or ''}",
            [effective_source_name(effective_source)], context)
    except Exception as exc:  # noqa: BLE001 旁路（质量/产品登记失败不谎报主链路失败）
        logger.warning("[Materialize] 质量/产品登记失败（不影响物化结果）: %s", exc)

    # 目标表结构登记入 catalog（表行 + 列）：物化产出的表是下游检索/血缘/敏感登记
    # 的起点，不登记就「产品目录有、元数据目录无」，LLM 只能猜列名（见
    # node_runners._sync_catalog_after_write 的说明）。失败显式带回、不阻断落地。
    try:
        from backend.modules.flow.dag.node_runners import _sync_catalog_after_write
        catalog = _sync_catalog_after_write(target_ds, target_table, context)
    except Exception as exc:  # noqa: BLE001 旁路
        catalog = {"synced": False, "table": f"{target_ds}.{target_table}",
                   "error": f"{type(exc).__name__}: {exc}"}
        logger.warning("[Materialize] 目标表结构登记失败（不影响物化结果）: %s", exc)

    return {
        "rows_read": total_read,
        "rows_written": total_written,
        "dataset": ds.get("name"),
        "target": f"{target_ds}.{target_table}",
        "write_mode": write_mode,
        "partitions": partitions,
        "reconciled": all(item.get("reconciled") for item in partitions),
        "strategy": ",".join(sorted(s for s in strategies if s)),
        "budget": {"max_rows": max_rows, "max_seconds": max_seconds},
        "window": window_info,
        "last_committed_date": last_committed,
        "columns": last_columns,
        "quality": quality,
        "data_product": product,
        "catalog": catalog,
    }
