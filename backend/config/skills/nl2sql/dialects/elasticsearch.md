# Elasticsearch 方言规则

## 核心差异
- Elasticsearch SQL 与 MySQL/Doris 差异极大，禁止混用
- 标识符（索引名、字段名）必须用**双引号**包裹，不是反引号
- 不支持 CTE（WITH 子句）、不支持窗口函数

## 日期函数（必须严格遵守）

### 相对日期
- N 天前：`DATE_ADD('day', -N, NOW())`
- N 小时前：`DATE_ADD('hour', -N, NOW())`
- N 个月前：`DATE_ADD('month', -N, NOW())`

### 日期比较模板
```sql
WHERE "时间字段" >= CAST(DATETIME_FORMAT(DATE_ADD('day', -N, NOW()), 'yyyy-MM-dd') AS TIMESTAMP)
```

### 其他日期函数
- 截断到月：`DATE_TRUNC('month', field)`
- 日期差：`DATE_DIFF('day', start, end)`
- 字符串转日期：`DATE_PARSE(str, 'yyyy-MM-dd')`
- 日期格式化：`DATETIME_FORMAT(field, 'yyyy-MM-dd')`（不是 DATE_FORMAT）
- 提取年/月/日：`YEAR(date)` / `MONTH(date)` / `DAYOFMONTH(date)`
- 提取季度：`QUARTER(date)`

### 禁止使用
- ❌ `DATE_SUB`、`DATE_FORMAT`、`STR_TO_DATE`、`TIMESTAMPDIFF`
- ❌ `INTERVAL` 关键字
- ❌ `DATE_ADD(NOW(), INTERVAL -3 DAY)` ← 这是 MySQL 语法

## 标识符规则
- 索引名（表名）、字段名、别名必须用**双引号**包裹
- 通配符索引需整体加引号：`"logs-*"`
- 嵌套字段用点号连接：`"user.name"`
- 时间字段必须显式转换：`CAST("@timestamp" AS TIMESTAMP)`

## 分页
- 限制行数：`LIMIT n`（标准 SQL 语法）
- 禁止使用 Elasticsearch 原生 `size/from` 参数

## 子查询限制
- 支持 `WHERE IN` 子查询
- 不支持 CTE（WITH 子句）
- 不支持相关子查询
- JOIN 性能有限，仅适合小数据量关联

## 特殊规则
- 禁止使用 `SELECT *`，必须明确字段名
- 必须为每个索引生成别名（不加 AS）
- 中文/特殊字符字段需保留原名并添加英文别名
- 函数字段必须加别名
- 百分比显示为：`ROUND(x*100,2) || '%'`
- 避免与 Elasticsearch 关键字冲突（如 `score`/`type`）

## _id 字段处理（REST/DSL）
Elasticsearch SQL 不支持 `_id` 元数据字段。当查询涉及 `_id` 时，必须使用 REST 或 DSL 方式：

### REST 格式（单文档查询）
```json
{"success":true,"query_type":"rest","sql":"GET /my-index/_doc/abc123","tables":["my-index"],"chart-type":"table"}
```

### DSL 格式（复杂条件）
```json
{"success":true,"query_type":"dsl","sql":"{\"query\":{\"term\":{\"_id\":\"abc123\"}}}","tables":["my-index"],"chart-type":"table"}
```

### 使用场景
- 查询条件涉及 `_id`
- 需要获取文档的 `_id` 字段
- 需要精确获取单个文档
- SQL 方式报错提示 `_id` 不支持时
