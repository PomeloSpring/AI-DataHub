# SLS（阿里云日志服务）查询方言规则

SLS 查询不是普通 SQL，采用「查询语句 | 分析语句」的管道结构，必须严格遵守。

## 查询结构
- 完整格式：`<search> | <SQL 分析>`，如：`status >= 500 | SELECT count(*) AS c, host GROUP BY host ORDER BY c DESC LIMIT 20`
- `<search>` 部分为 SLS 检索语法（不是 SQL）：
  - 全文：`error`；短语：`"connection refused"`
  - 字段过滤：`status:500`, `method:'GET'`，组合 `status:500 AND host:"api.example.com"`
  - 通配：`host:api*`；数值范围：`latency in [100 200)` 或 `latency > 100`
  - 全量（无过滤条件）：`*`
- `<search>` 部分禁止使用 WHERE/SELECT/JOIN 等 SQL 关键字；过滤逻辑尽量放这里（性能好于 SQL WHERE）

## 分析语句（`|` 之后是标准 SQL 子集）
- 支持 SELECT / WHERE / GROUP BY / ORDER BY / JOIN（logstore 间 join 用双引号库名）
- 必须显式 `LIMIT`（默认上限 100，聚合排名类建议 ≤1000）
- 不支持 OFFSET 之外多数存储过程语法；不支持 INSERT/UPDATE/DELETE/DDL（SLS 本身也不提供）

## 时间函数
- 查询时间范围由 API 参数 from/to 指定，SQL 内通常不再重复过滤 `__time__`
- SQL 内取时间：`to_unixtime(now())`；时间列分组：`date_trunc('minute', __time__)`
- 格式化：`date_format(__time__, '%Y-%m-%d %H:%i')`（注意是 Presto 风格函数）
- 时间序列补点：`time_series(__time__, '1m', '%Y-%m-%d %H:%i', '0')`

## 日志专用字段与函数
- 保留字段：`__time__`(unix 秒), `__topic__`, `__source__`
- JSON 字段提取：`json_extract_scalar(col, '$.key')`
- 近似去重：`approx_distinct(col)`（大数据量首选，替代 COUNT DISTINCT）
- 百分位：`approx_percentile(col, 0.95)`（P95 延迟类问题）

## 输出约定
- query_type 使用 "sql"（`|` 之后的部分是 SQL）；整条「查询|分析」放入 sql 字段一行输出
- 标识符：字段名含特殊字符或大写时用双引号，如 `"HTTP_REFERER"`
- 字符串值：单引号；正则等反斜杠需双写（如 `'\d+'` 写作 `\\d+` 场景按平台要求）
