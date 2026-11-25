-- ═══════════════════════════════════════════════════════════════
-- DAG 运行实例参数（adh_dag_runs.params_json）
--
-- 为什么需要它
-- ------------
-- 定时物化的历史初始化必须能给**一次运行**指定时间窗口（start_date/end_date），
-- 否则只能靠外部脚本循环"伪造多次运行"——那样根本验证不了 DAG 编排本身对不对，
-- 也留不下"这次跑到哪一期"的可审计记录（用户 2026-11-24 明确要求）。
-- 参数必须落在运行实例上（而不是节点 config），因为：
--   * 同一份编排定义要被日常调度与历史初始化共用，不能把日期写死进图；
--   * 运行记录页要能回答"这次跑了哪个区间、从哪个成功位置续跑"。
--
-- 幂等与边界
-- ----------
-- * 用 information_schema 判断列是否已存在，重复执行只打印提示（不报错、不改数据）；
-- * 不写死库名：用 DATABASE()，环境库是 adh2 还是 adh 都按连接配置执行；
-- * 列只存服务端校验过的 {start_date,end_date}（dag_validator.validate_run_params），
--   不承载凭据/连接信息。
--
-- 执行: mysql -u root -p <docker/mysql/dag_run_params_migration.sql>
-- ═══════════════════════════════════════════════════════════════

SET @db := DATABASE();

SET @sql := IF(
    (SELECT COUNT(*) FROM information_schema.columns
      WHERE table_schema = @db AND table_name = 'adh_dag_runs'
        AND column_name = 'params_json') = 0,
    'ALTER TABLE adh_dag_runs ADD COLUMN params_json JSON NULL COMMENT ''运行参数（物化起始窗口等，服务端校验后落库）'' AFTER stats_json',
    'SELECT ''adh_dag_runs.params_json 已存在，跳过'' AS msg'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 核验
SELECT column_name, data_type, is_nullable
  FROM information_schema.columns
 WHERE table_schema = @db AND table_name = 'adh_dag_runs' AND column_name = 'params_json';