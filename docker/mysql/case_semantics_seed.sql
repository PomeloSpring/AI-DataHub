-- ============================================================
-- 案例对象(object=case / t_case_records)语义层指标与维度种子
--
-- 背景: Chat 用例"查看最近7天的病例数量"需要声明式聚合。此前
-- t_case_records 上 0 指标 0 维度, planner 只能回落 SELECT * 行预览。
-- 口径来自知识库: total_cases = COUNT(*), del_flag=0(排除已删除)。
--
-- 幂等: 按 (target_table, name_en) 判重, 可重复执行。
-- ============================================================

INSERT INTO adh_metrics
  (workspace_id, name, name_en, formula, unit, agg_type,
   target_table, target_column, description, category,
   datasource_id, is_active, default_agg, bound_object_key)
SELECT 1, '案例数量', 'case_count',
       'COUNT(CASE WHEN del_flag = 0 THEN id ELSE NULL END)',
       '个', 'COUNT',
       't_case_records', 'id',
       '案例(病例)记录数, 排除已删除记录(del_flag=0)。业务别名: total_cases / 口扫案例数',
       '业务', 0, 1, 'COUNT', 'case'
WHERE NOT EXISTS (
  SELECT 1 FROM adh_metrics
  WHERE target_table = 't_case_records' AND name_en = 'case_count'
);

INSERT INTO adh_dimensions
  (name, name_en, level, target_table, target_column,
   description, category, datasource_id, is_active, bound_object_key)
SELECT '创建日期', 'create_date', 0, 't_case_records', 'create_time',
       '案例创建时间, 语义查询按 time_grain(day/week/month) 自动分桶, 物理列为 create_time',
       '时间', 0, 1, 'case'
WHERE NOT EXISTS (
  SELECT 1 FROM adh_dimensions
  WHERE target_table = 't_case_records' AND name_en = 'create_date'
);
