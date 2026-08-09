# SQL 安全规则

## 禁止操作
- 禁止 INSERT、UPDATE、DELETE
- 禁止 DROP、ALTER、CREATE、TRUNCATE
- 禁止 GRANT、REVOKE
- 禁止 CALL、EXEC（存储过程）

## SELECT * 拦截
- 禁止使用 SELECT *
- 必须明确指定查询字段
- 例外：COUNT(*) 允许

## 敏感字段处理
- 手机号、身份证、邮箱等需要脱敏
- 使用 CONCAT(LEFT(col, 3), '****', RIGHT(col, 4)) 脱敏
