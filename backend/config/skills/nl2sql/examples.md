# SQL 生成示例

## 简单查询

### 示例 1：基础查询
**问题**：查询所有用户的姓名和邮箱
**SQL**：
```sql
SELECT `name`, `email` FROM `t_user_customer` LIMIT 1000
```
**说明**：简单单表查询，明确指定字段

### 示例 2：条件过滤
**问题**：查询状态为"活跃"的用户
**SQL**：
```sql
SELECT `id`, `name`, `status` FROM `t_user_customer` WHERE `status` = 'active' LIMIT 1000
```
**说明**：使用 WHERE 过滤条件

## 聚合查询

### 示例 3：统计数量
**问题**：查询每个城市的用户数量
**SQL**：
```sql
SELECT `city`, COUNT(*) AS `user_count` FROM `t_user_customer` GROUP BY `city` ORDER BY `user_count` DESC LIMIT 1000
```
**说明**：使用 GROUP BY 分组，COUNT 统计

### 示例 4：求和统计
**问题**：查询本月的订单总金额
**SQL**：
```sql
SELECT SUM(`amount`) AS `total_amount` FROM `t_orders` WHERE `create_time` >= DATE_FORMAT(NOW(), '%Y-%m-01') LIMIT 1000
```
**说明**：使用 SUM 聚合，日期函数过滤

## 多表查询

### 示例 5：JOIN 查询
**问题**：查询每个用户的订单数量
**SQL**：
```sql
SELECT u.`name`, COUNT(o.`id`) AS `order_count` FROM `t_user_customer` u LEFT JOIN `t_orders` o ON u.`id` = o.`user_id` GROUP BY u.`id`, u.`name` ORDER BY `order_count` DESC LIMIT 1000
```
**说明**：LEFT JOIN 关联用户和订单表

### 示例 6：多表聚合
**问题**：查询每个产品类别的销售总额
**SQL**：
```sql
SELECT p.`category`, SUM(o.`amount`) AS `total_sales` FROM `t_products` p INNER JOIN `t_orders` o ON p.`id` = o.`product_id` GROUP BY p.`category` ORDER BY `total_sales` DESC LIMIT 1000
```
**说明**：INNER JOIN + GROUP BY + SUM

## 复杂查询

### 示例 7：子查询
**问题**：查询订单数量最多的前 5 个用户
**SQL**：
```sql
SELECT u.`name`, sub.`order_count` FROM `t_user_customer` u INNER JOIN (SELECT `user_id`, COUNT(*) AS `order_count` FROM `t_orders` GROUP BY `user_id` ORDER BY `order_count` DESC LIMIT 5) sub ON u.`id` = sub.`user_id` LIMIT 1000
```
**说明**：子查询先聚合排序，再关联用户表

### 示例 8：窗口函数
**问题**：查询每个用户的订单金额排名
**SQL**：
```sql
SELECT `user_id`, `amount`, RANK() OVER (PARTITION BY `user_id` ORDER BY `amount` DESC) AS `rank` FROM `t_orders` LIMIT 1000
```
**说明**：使用 RANK() 窗口函数

## 时间相关

### 示例 9：相对日期
**问题**：查询最近 7 天的订单数量
**SQL**：
```sql
SELECT DATE(`create_time`) AS `date`, COUNT(*) AS `order_count` FROM `t_orders` WHERE `create_time` >= DATE_SUB(NOW(), INTERVAL 7 DAY) GROUP BY DATE(`create_time`) ORDER BY `date` LIMIT 1000
```
**说明**：DATE_SUB 计算相对日期

### 示例 10：同比环比
**问题**：查询本月和上月的销售额对比
**SQL**：
```sql
SELECT MONTH(`create_time`) AS `month`, SUM(`amount`) AS `total_sales` FROM `t_orders` WHERE `create_time` >= DATE_SUB(NOW(), INTERVAL 2 MONTH) GROUP BY MONTH(`create_time`) ORDER BY `month` LIMIT 1000
```
**说明**：按月分组统计
