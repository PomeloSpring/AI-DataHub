# MySQL 方言规则

## 日期函数
- 当前时间：`NOW()`
- 今天：`CURDATE()`
- 本月第一天：`DATE_FORMAT(NOW(), '%Y-%m-01')`
- N 天前：`DATE_SUB(NOW(), INTERVAL N DAY)`
- N 个月前：`DATE_SUB(NOW(), INTERVAL N MONTH)`
- 日期格式化：`DATE_FORMAT(date, '%Y-%m-%d')`
- 提取年月：`YEAR(date)`, `MONTH(date)`

## 字符串函数
- 拼接：`CONCAT(str1, str2)`
- 截取：`SUBSTRING(str, start, length)`
- 替换：`REPLACE(str, from, to)`
- 去空格：`TRIM(str)`

## 聚合函数
- 计数：`COUNT(*)`, `COUNT(DISTINCT col)`
- 求和：`SUM(col)`
- 平均：`AVG(col)`
- 最大/最小：`MAX(col)`, `MIN(col)`

## 窗口函数（MySQL 8.0+）
- 排名：`RANK() OVER (PARTITION BY col ORDER BY col)`
- 行号：`ROW_NUMBER() OVER (...)`
- 累计：`SUM(col) OVER (ORDER BY col)`

## 分页
- 限制行数：`LIMIT n`
- 跳过行数：`LIMIT n OFFSET m` 或 `LIMIT m, n`

## 特殊语法
- 引号：反引号 `` ` `` 包裹标识符
- 字符串：单引号 `'` 包裹字符串值
- NULL 检查：`IS NULL`, `IS NOT NULL`
- 模糊匹配：`LIKE '%keyword%'`
