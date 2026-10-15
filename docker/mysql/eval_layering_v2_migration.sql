-- ============================================================================
-- 评测分层 v2（layer × capability）+ 门禁双口径 迁移 (幂等, 无 USE, 可重复执行)
--
-- 背景（为什么现有口径必须改）
-- ----
-- 1) **假绿灯**：eval/compare.py 的 baseline_ok 只在「基线 pass → 当前 fail」时判回退，
--    于是"一条用例永久坏着"永远不会触发回退。实测 retrieval 集 5/14=35.7% 却
--    baseline_ok=1，/api/eval/precheck 输出绿字"各评测集均未低于基线，可以发布"。
--    这违反 no-silent-degradation §1（功能失败必须显式暴露），必须补**绝对下限**口径。
-- 2) **覆盖度无从判定**：suite 只按技术实现层切（compile/retrieval/llm），没有任何
--    横轴表达"用户可感知的能力契约"，导致"测试集内容是否符合要求"不可判定；
--    并且 compile 适配器把语义目录整体打桩成内存 fixture，72/72 绿的是规则回归，
--    不是真实本平台能力。新增 capability 列后，按 capability × layer 矩阵反推缺口。
-- 3) **长期失败无归处**：针对已废弃形态的用例只能删除，而删除会掩盖覆盖度流失
--    （compare.py 专门把 coverage_loss 列出来就是为了防这个）。新增 case_status
--    三态，让"退役/已知缺陷"显式可见而不是消失。
--
-- 关键设计
-- --------
-- * suite 仍是**适配器路由键**（不重命名，避免牵动已落库运行记录的对比对齐）；
--   新增 suite='semantic' 由代码侧 contract.py/runner.py 声明，本文件只落门禁默认值。
-- * capability 为必填契约组，空串视为"未登记"，页面与概览矩阵会显式计缺。
-- * case_status: active=跑且计入分母；known_defect=跑且计入分母（拖累下限，逼着修）；
--   retired=不跑、不计入分母，但页面显示条数与原因，历史对比仍可按 case_key 诊断。
--   retired/known_defect 必须带 status_note（保存侧硬校验），禁止空原因退役。
-- * 门禁判定 = relative_ok(沿用 baseline_ok 语义，含义不变) AND floor_ok(新增)。
--   两个口径分开存列，不得互相顶替：把 floor_ok 塞进 baseline_ok 会让"版本对比"
--   的历史语义漂移，是另一种掩盖。
-- ============================================================================

DELIMITER $$
DROP PROCEDURE IF EXISTS adh_ev2_add_column$$
CREATE PROCEDURE adh_ev2_add_column(IN tbl VARCHAR(64), IN col VARCHAR(64), IN definition TEXT)
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema=DATABASE() AND table_name=tbl)
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema=DATABASE() AND table_name=tbl AND column_name=col) THEN
        SET @ddl=CONCAT('ALTER TABLE `', tbl, '` ADD COLUMN `', col, '` ', definition);
        PREPARE stmt FROM @ddl;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END$$
DROP PROCEDURE IF EXISTS adh_ev2_add_index$$
CREATE PROCEDURE adh_ev2_add_index(IN tbl VARCHAR(64), IN idx VARCHAR(64), IN definition TEXT)
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema=DATABASE() AND table_name=tbl)
       AND NOT EXISTS (SELECT 1 FROM information_schema.statistics
               WHERE table_schema=DATABASE() AND table_name=tbl AND index_name=idx) THEN
        SET @ddl=CONCAT('ALTER TABLE `', tbl, '` ADD ', definition);
        PREPARE stmt FROM @ddl;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END$$
DELIMITER ;

-- ----------------------------------------------------------------------------
-- 1. 用例：能力契约 + 三态状态
-- ----------------------------------------------------------------------------
CALL adh_ev2_add_column('adh_eval_cases', 'capability',
    "VARCHAR(32) NOT NULL DEFAULT '' COMMENT '能力契约组: resolution/time/routing/governance/honesty/leak/clarify/evolution'");
CALL adh_ev2_add_column('adh_eval_cases', 'case_status',
    "VARCHAR(16) NOT NULL DEFAULT 'active' COMMENT 'active=跑且计分母 / known_defect=跑且计分母(拖累下限) / retired=不跑不计分母(必须带原因)'");
CALL adh_ev2_add_column('adh_eval_cases', 'status_note',
    "VARCHAR(512) NOT NULL DEFAULT '' COMMENT 'retired/known_defect 的原因, 保存侧硬校验不得为空'");
CALL adh_ev2_add_index('adh_eval_cases', 'idx_eval_case_cap',
    'INDEX idx_eval_case_cap (suite, capability, case_status)');

-- ----------------------------------------------------------------------------
-- 2. 运行：门禁双口径 + 分母构成可见
-- ----------------------------------------------------------------------------
CALL adh_ev2_add_column('adh_eval_runs', 'floor_ok',
    "TINYINT(1) NOT NULL DEFAULT 1 COMMENT '绝对下限判定; 0=低于下限或用例数不足(长期坏掉却显示未回退的情形在此暴露)'");
CALL adh_ev2_add_column('adh_eval_runs', 'gate_verdict',
    "VARCHAR(24) NOT NULL DEFAULT '' COMMENT 'ok/regression/below_floor/both'");
CALL adh_ev2_add_column('adh_eval_runs', 'active_count',
    "INT NOT NULL DEFAULT 0 COMMENT '计入分母的活跃用例数(= known_defect + active)'");
CALL adh_ev2_add_column('adh_eval_runs', 'known_defect_count',
    "INT NOT NULL DEFAULT 0 COMMENT '显式标注的已知缺陷数, 页面常驻可见'");
CALL adh_ev2_add_column('adh_eval_runs', 'retired_count',
    "INT NOT NULL DEFAULT 0 COMMENT '退役未跑数, 页面常驻可见(防删用例刷通过率)'");

-- ----------------------------------------------------------------------------
-- 3. 门禁策略表（页面可编辑阈值；能力级覆盖层级的整体下限）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS adh_eval_gate_policy (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  suite VARCHAR(32) NOT NULL COMMENT '被测层: compile/semantic/retrieval/llm',
  capability VARCHAR(32) NOT NULL DEFAULT '' COMMENT '空串=该层整体下限; 非空=该契约组单独下限',
  floor_accuracy DECIMAL(6,4) NOT NULL DEFAULT 1.0 COMMENT '绝对正确率下限',
  min_cases INT NOT NULL DEFAULT 0 COMMENT '该桶最少活跃用例数; 不足即不通过(防覆盖度流失)',
  max_live_cases_per_run INT NULL COMMENT '仅 llm live 子集: 单次预算上限, NULL=不限',
  enabled TINYINT(1) NOT NULL DEFAULT 1,
  updated_by VARCHAR(64) NOT NULL DEFAULT '',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_eval_gate (suite, capability)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='评测门禁绝对下限策略（与相对回退判定正交）';

-- 默认下限。语义:
--   compile/semantic 是确定性层, 不允许任何失败(1.0);
--   retrieval 受真实检索召回波动, 0.9;
--   llm 的 handler/trace 子集是确定性的(1.0), live 子集单独成桶另配。
-- 用 INSERT IGNORE 保持幂等, 且不覆盖人在页面上调过的阈值。
INSERT IGNORE INTO adh_eval_gate_policy (suite, capability, floor_accuracy, min_cases, enabled)
VALUES
  ('compile',   '', 1.0000, 40, 1),
  ('semantic',  '', 1.0000, 20, 1),
  ('retrieval', '', 0.9000, 10, 1),
  ('llm',       '', 1.0000,  8, 1);

-- ----------------------------------------------------------------------------
-- 4. 权限码：评测与自进化的写侧一律 ai_access='none'
--    理由同 eval_engine_migration: 评测用例是评价 AI 的标尺, 不得自评自证。
-- ----------------------------------------------------------------------------
INSERT IGNORE INTO adh_perm_registry
  (perm_code, label, module, module_label, description, api_pattern, api_method, menu_key, sort,
   ai_access, ai_action_key, ai_note)
VALUES
  ('eval:gate', '配置评测门禁', 'system', '系统配置',
   '编辑各层/各能力契约的正确率绝对下限与用例数下界',
   '/api/eval/gate*', 'GET,POST,PUT,DELETE', '', 352,
   'none', '', '门禁阈值不得由 AI 自行放宽, 否则评测失去约束力'),
  ('evolution:read', '查看进化候选', 'system', '系统配置',
   '查看用户反馈归纳出的待开发指标/维度、别名与术语候选',
   '/api/evolution*', 'GET', '', 353,
   'none', '', '自进化候选的验收标尺, 不开放给 AI 读写'),
  ('evolution:manage', '处理进化候选', 'system', '系统配置',
   '审核候选并转指标/维度草稿、收编别名、转术语、转评测用例',
   '/api/evolution*', 'POST,PUT,DELETE', '', 354,
   'none', '', '收编/建字典是改语义资产的动作, 必须人工把关并留审计');
