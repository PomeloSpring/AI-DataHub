"""DataFusion 方言桥 — MySQL/Doris 方言 SQL → DataFusion 执行引擎的唯一转译边界。

背景:语义层/LLM 按数据源原生方言(MySQL)生成 SQL,RLS 改写也是 MySQL 方言;
而执行侧 DataFusion(Rust)用自己的函数集规划,遇到 `DATE_SUB(x, INTERVAL 7 DAY)`
等 MySQL 函数在 plan 期直接报 Invalid function。sqlglot 30.x 无内置 datafusion
方言,本模块基于**对运行中引擎的实测**补齐这一映射,作为 engine 边界的单一事实源。

实测真值(2026-09,datafusion-gateway @8082,勿凭方言文档臆断):
  ✓ `x - INTERVAL '7 DAY'` / `INTERVAL '7' DAY` 两种 interval 字面量均可
  ✓ CURRENT_DATE / NOW() / COALESCE / EXTRACT(YEAR FROM d) / STRING_AGG / DATE_BIN
  ✓ DATE_PART('epoch', ts) 可做时间差;FROM_UNIXTIME;DATE_TRUNC('month', ts)
  ✗ date_sub/date_add/year()/month()/strftime/date_diff/unix_timestamp 均不存在
  ✗ TO_CHAR 只接受字符串语义,对 timestamp 会**原样返回格式串**(静默错值!)
    → 严禁把 DATE_FORMAT 转成 TO_CHAR;常见前缀模式转 SUBSTR(CAST(ts AS VARCHAR))
  ✗ `date - date` 返回 Duration(Second),不能用作 DATEDIFF 的天数差

转译失败(未覆盖语法)时 engine_client 保留原 SQL 下发:引擎报错后上层回退
MySQL 直连执行,正确性优先于引擎加速。
"""
from __future__ import annotations

from sqlglot import exp
from sqlglot.dialects.postgres import Postgres

# MySQL DATE_FORMAT 常见前缀模式 → ISO 串截取长度(ts 转字符串形如 'YYYY-MM-DD HH:MM:SS')
_FORMAT_PREFIX_LEN = {
    "%Y": 4,
    "%Y-%m": 7,
    "%Y-%m-%d": 10,
    "%Y-%m-%d %H": 13,
    "%Y-%m-%d %H:%i": 16,
    "%Y-%m-%d %H:%i:%s": 19,
    "%Y/%m": 7,
    "%Y/%m/%d": 10,
}

# TIMESTAMPDIFF 单位 → 秒除数;MONTH/YEAR 无恒定秒数,不映射(回退原 SQL)
_TSDIFF_DIVISOR = {
    "SECOND": 1,
    "MINUTE": 60,
    "HOUR": 3600,
    "DAY": 86400,
    "WEEK": 604800,
}


# ── TRANSFORMS 生成函数(模块级,避免 staticmethod 存入 dict 后的描述符坑) ──

def _gen_timetostr(generator, expression: exp.TimeToStr) -> str:
    """DATE_FORMAT:禁止落到 TO_CHAR(引擎语义不符,返回静默错值)。"""
    fmt = expression.args.get("format")
    fmt_text = fmt.name if fmt is not None else ""
    n = _FORMAT_PREFIX_LEN.get(fmt_text)
    if n is None:
        # 未覆盖的模式 → 抛错走"保留原SQL+引擎报错+直连回退"
        raise NotImplementedError(f"DATE_FORMAT pattern {fmt_text!r}")
    this = generator.sql(expression, "this")
    return f"SUBSTR(CAST(CAST({this} AS TIMESTAMP) AS VARCHAR), 1, {n})"


def _gen_datediff(generator, expression: exp.DateDiff) -> str:
    """DATEDIFF(a,b):MySQL 语义=日期天数差;date-date 在引擎返回 Duration,不可用。"""
    this = generator.sql(expression, "this")
    expr = generator.sql(expression, "expression")
    return (
        "CAST((DATE_PART('epoch', CAST(CAST(" + this + " AS DATE) AS TIMESTAMP))"
        " - DATE_PART('epoch', CAST(CAST(" + expr + " AS DATE) AS TIMESTAMP)))"
        " / 86400 AS BIGINT)"
    )


class DataFusion(Postgres):
    """DataFusion SQL 方言(Postgres 系基座 + 实测修正的函数生成)。

    注:Postgres.Generator 对 TimeToStr/DateDiff 走 TRANSFORMS 表(优先于方法覆盖),
    故这里必须用 TRANSFORMS 合并而非重写方法。
    """

    class Generator(Postgres.Generator):
        # ── EXTRACT(YEAR/MONTH/...):引擎返回浮点(2026.0),MySQL 语义为整数 ──
        def extract_sql(self, expression: exp.Extract) -> str:
            return f"CAST({super().extract_sql(expression)} AS BIGINT)"

        # ── TIMESTAMPDIFF(unit,a,b):epoch 差整除;MONTH/YEAR 不支持 ──
        def timestampdiff_sql(self, expression: exp.TimestampDiff) -> str:
            unit_arg = expression.args.get("unit")
            unit = (unit_arg.name if unit_arg is not None else "").upper()
            div = _TSDIFF_DIVISOR.get(unit)
            if div is None:
                raise NotImplementedError(f"TIMESTAMPDIFF unit {unit!r}")
            a = self.sql(expression, "expression")
            b = self.sql(expression, "this")
            return (
                f"CAST((DATE_PART('epoch', CAST({b} AS TIMESTAMP))"
                f" - DATE_PART('epoch', CAST({a} AS TIMESTAMP))) / {div} AS BIGINT)"
            )

        TRANSFORMS = {
            **Postgres.Generator.TRANSFORMS,
            exp.TimeToStr: _gen_timetostr,
            exp.DateDiff: _gen_datediff,
        }


def to_datafusion(sql: str) -> str:
    """MySQL/Doris 方言 → DataFusion。解析/生成失败时抛异常,由调用方决定降级。"""
    import sqlglot

    stmts = [s for s in sqlglot.transpile(sql, read="mysql", write="datafusion") if s.strip()]
    if not stmts:
        raise ValueError("empty transpile result")
    return ";".join(stmts)
