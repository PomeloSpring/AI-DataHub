# NULL 处理规则

## 检查 NULL
- 使用 IS NULL 和 IS NOT NULL
- 不要使用 = NULL 或 != NULL

## 处理 NULL
- 使用 COALESCE(col, default_value) 提供默认值
- 使用 IFNULL(col, default_value) 处理 NULL
- 聚合函数自动忽略 NULL（COUNT 除外）

## 示例
```sql
-- 错误
WHERE name = NULL

-- 正确
WHERE name IS NULL

-- 提供默认值
SELECT COALESCE(name, '未知') AS name FROM users
```
