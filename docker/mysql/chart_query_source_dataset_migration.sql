-- ============================================================================
-- adh_charts.query_source 增加 'dataset' 通道（2026-11-17）
--
-- 背景：大屏直建通道（AS-BOT `create_data_screen` / `update_data_screen_chart`）
-- 每张图先落语义型数据集、再挂 `source_type='dataset'` 的行，写入
-- `query_source='dataset'` 标记“口径来自数据集”。该枚举值在库里从未登记，
-- 写入被 MySQL 拒（1265 Data truncated for column 'query_source'），
-- 表现为“AI 建大屏永远失败”，而错误文案与权限无关 —— 属 schema 漂移。
--
-- 取值口径（三条通道互斥，刷新分派按 source_type 优先）：
--   raw_sql  : 存量行/设计文档里的人写 SQL（默认值，保持兼容）
--   semantic : 声明式语义意图（semantic_query 带 object），由语义层编译执行
--   dataset  : 挂数据集（source_id=adh_datasets.id），刷新走 dataset_service
--
-- 幂等：MODIFY COLUMN 重复执行结果相同，不动数据、不动索引。
-- ============================================================================

ALTER TABLE adh_charts
    MODIFY COLUMN query_source ENUM('raw_sql','semantic','dataset') NOT NULL DEFAULT 'raw_sql'
    COMMENT '取数口径通道: raw_sql=人手写SQL / semantic=声明式语义意图 / dataset=挂数据集';