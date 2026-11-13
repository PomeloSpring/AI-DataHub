-- ============================================================================
-- ES 数据源化 · 阶段一（adh_datasources.extra_config：索引别名与开放范围）
--
-- 背景（2026-11-13 拍板的四阶段路线，本文件只服务阶段一）：
-- ES 一直是「内置 MCP 通道」（凭据与索引范围在 backend/.env，dataset.datasource_id=0），
-- 不是数据源行。拍板先把 **ES 数据源 + RLS 注入策略**建起来，再逐步迁数据集（阶段二/三），
-- 最后去掉 MCP 配置才补本体/维度/指标（阶段四）。
--
-- 为什么必须新增一列（而不是塞进 database_name 或复用 ssl_mode）：
-- ES 没有「库」这一层，执行范围由**索引别名**决定；而开放范围是数据源级配置，
-- 既不是全局环境变量（多集群就写不下），也不该混进凭据字段（凭据页会把它当密码脱敏）。
--
-- 口径（与代码层同源，改动需一并核对）：
--   1. `db_type='es'`（或 'elasticsearch'）是新增类型，判定唯一入口
--      `semantics.dialect.ES_DB_TYPES`；它**没有 DB-API 连接**，
--      `common/db/datasource_db.get_datasource_conn` 对该类型显式拒绝（不许"当 mysql 连一下"）。
--   2. 表清单 / 行级策略键 / 敏感字段登记表名 = **索引别名**（实机 2026-11-13：集群 1379 条
--      别名，四个目标都有同名别名，别名与 `t-别名*` 模式的覆盖索引集合双向差集为 0、计数相等）。
--      别名与 MySQL 站点库的表名同名，因此键空间与既有治理登记天然统一，不需要额外映射表。
--   3. 开放范围 = `allowed_aliases` ∪ `allowed_index_patterns`（**并集口径**，拍板于 2026-11-13）：
--      两种写法都合法，同时保留"丢掉 `t-` 前缀的模式算扩权"这一旧护栏
--      （`t_software_app_operation_log*` 不在登记范围内 —— 它匹配的是另一族索引）。
--   4. **本文件不写任何凭据**（护栏 §7：凭据只在服务端）。ES 源行由
--      `backend/scripts/register_es_datasource.py` 从 backend/.env 读取并经 encrypt_password 落库。
--   5. 行级策略在 ES 上的注入形态 = 谓词 **AND 进顶层 WHERE**，不是通用实现的
--      `FROM (SELECT * FROM t WHERE p) AS t`：实机验证 ES 派生表的 `SELECT *` 不透传
--      `.keyword` 子字段（400 Unknown column），包裹形态在本引擎根本不成立。
--      实现见 `semantics/sql_guard._inject_filters_es`；策略命不中表引用时显式拒绝执行。
--
-- 幂等：MySQL 的 ADD COLUMN 不支持 IF NOT EXISTS，先查 information_schema 再执行；可重复跑。
-- ============================================================================

SET @c1 = (SELECT COUNT(*) FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_datasources' AND COLUMN_NAME = 'extra_config');
SET @ddl = IF(@c1 = 0,
    'ALTER TABLE adh_datasources ADD COLUMN extra_config JSON NULL COMMENT ''引擎专属配置（非 SQL 型数据源用）。ES: {"allowed_aliases":[索引别名], "allowed_index_patterns":["t-别名*"], "timeout_sec":N, "max_rows":N, "verify_certs":bool}；开放范围为空 = 不开放任何索引（fail-closed，建源时即拒）''',
    'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
