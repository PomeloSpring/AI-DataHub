# PostgreSQL 方言规则

## 日期函数
- 当前时间：`NOW()`
- 今天：`CURRENT_DATE`
- 本月第一天：`DATE_TRUNC('month', CURRENT_DATE)`
- N 天前：`CURRENT_DATE - INTERVAL 'N days'`
- N 个月前：`NOW() - INTERVAL 'N months'`
- 日期格式化：`TO_CHAR(date, 'YYYY-MM-DD')`
- 提取年月：`EXTRACT(YEAR FROM date)`, `EXTRACT(MONTH FROM date)`
- 按粒度截断（趋势分组）：`DATE_TRUNC('week'|'month'|'quarter', ts)`

## 字符串函数
- 拼接：`a || b` 或 `CONCAT(a, b)`
- 截取：`SUBSTRING(str FROM start FOR length)`
- 替换：`REPLACE(str, from, to)`
- 类型转换：`col::text`, `CAST(col AS text)`（PostgreSQL 强类型，跨类型比较必须显式转换）

## 聚合函数
- 计数：`COUNT(*)`, `COUNT(DISTINCT col)`
- 求和：`SUM(col)`
- 平均：`AVG(col)`（返回 numeric，展示时可 `::numeric(18,2)`）
- 最大/最小：`MAX(col)`, `MIN(col)`
- 分组串接：`STRING_AGG(col, ',')`

## 窗口函数
- 排名：`RANK() OVER (PARTITION BY col ORDER BY col)`
- 行号：`ROW_NUMBER() OVER (...)`
- 累计：`SUM(col) OVER (ORDER BY col)`

## 分页
- 限制行数：`LIMIT n`
- 跳过行数：`LIMIT n OFFSET m`

## 特殊语法（与 MySQL 的差异必须严格遵守）
- 标识符：双引号 `"` 包裹（区分大小写）；无反引号
- 表函数替代：无 `LIMIT n, m` 语法；无 `IFNULL`（用 `COALESCE`）
- 模糊匹配不区分大小写：`ILIKE '%keyword%'`；区分大小写用 `LIKE`
- 正则匹配：`col ~ 'pattern'`
- 日期字面量：`DATE '2026-01-01'`, `TIMESTAMP '2026-01-01 00:00:00'`
- GROUP BY：SELECT 中非聚合列必须全部出现在 GROUP BY（不支持 MySQL 的宽松模式）
