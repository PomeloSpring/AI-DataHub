-- ============================================================================
-- 评测工程能力（Eval Engine）建表迁移 (幂等)
--
-- 背景
-- ----
-- 既定架构决策：评测是**项目内可复用的工程能力**，不是 tests/ 目录下的附属脚本。
-- 之前只落了 tests/eval/runner.py（离线 intent→binding→plan 编译回归），存在两个缺口：
--   1) 评测核心放在 tests/ 下，生产服务（Celery 业务评测任务、管理页）无法反向导入；
--   2) 用例写死在 YAML，运营/建模同学无法在页面上增删改查，也无法按版本对比。
--
-- 本迁移把用例与运行结果落到元数据库，支撑：
--   * 用例 DB 管理 + 页面 CRUD（不再依赖改文件发版）
--   * 三层评测集：compile(语义层编译) / retrieval(本体层检索) / llm(LLM 功能)
--   * 运行结果落库 + 版本对比 + 发布前检查（不得低于基线）
--
-- 关键设计
-- --------
-- * case_key 是**跨版本稳定标识**（同 suite 内唯一）：版本对比按 case_key 对齐，
--   用例改文案不影响对齐；删除用例在对比里体现为"基线有、当前无"，可诊断。
-- * passed 是**确定性断言**的结果，score 是 LLM 辅助评分（仅评价解释质量）——
--   二者分开，绝不让 LLM 评分决定安全/取数正确性（既定决策）。
-- * sources 记归因分桶：compile 层是解析档位(dict_exact/dict_alias/phys_col…)，
--   retrieval 层是检索来源(qmind_hit/graphrag/bm25/local_hybrid_fallback…)。
--   两类桶语义不同，报告里必须分开呈现，不得混为一谈（有 pitfall 记录）。
-- ============================================================================

-- ── 1) 评测用例（页面可增删改查）─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS adh_eval_cases (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  case_key VARCHAR(128) NOT NULL COMMENT '稳定标识，跨版本对比按它对齐；同 suite 内唯一',
  suite VARCHAR(32) NOT NULL COMMENT '评测集: compile=语义层编译 / retrieval=本体层检索 / llm=LLM 功能',
  question VARCHAR(512) NOT NULL COMMENT 'golden question（自然语言问法）',
  tags VARCHAR(512) NOT NULL DEFAULT '' COMMENT '逗号分隔标签，用于分桶报告（如 alias,time_window）',
  payload JSON COMMENT '执行适配器入参（intent/检索关键词/工具轨迹等），结构随 suite 不同',
  expected JSON COMMENT '期望结果（断言契约），结构随 suite 不同',
  note VARCHAR(512) NOT NULL DEFAULT '' COMMENT '用例覆盖意图说明（为什么要有这条）',
  is_active TINYINT(1) NOT NULL DEFAULT 1 COMMENT '停用不删，保留历史对比',
  sort INT NOT NULL DEFAULT 0,
  created_by VARCHAR(64) NOT NULL DEFAULT '',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_eval_case (suite, case_key),
  INDEX idx_eval_case_suite (suite, is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='评测用例（DB 管理，页面增删改查）';

-- ── 2) 评测运行记录 ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS adh_eval_runs (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  run_key VARCHAR(64) NOT NULL COMMENT '运行标识（幂等键，重复投递不重复计算）',
  suite VARCHAR(32) NOT NULL COMMENT 'compile | retrieval | llm | all',
  trigger_type VARCHAR(24) NOT NULL DEFAULT 'manual' COMMENT 'manual | celery | ci',
  status VARCHAR(24) NOT NULL DEFAULT 'queued' COMMENT 'queued | running | done | failed',
  total INT NOT NULL DEFAULT 0,
  passed INT NOT NULL DEFAULT 0,
  accuracy DECIMAL(6,4) NOT NULL DEFAULT 0,
  by_tag JSON COMMENT '按标签分桶 {tag: {passed,total,accuracy}}',
  by_source JSON COMMENT '按归因分桶（解析档位 或 检索来源，随 suite 而定）',
  baseline_run_id BIGINT DEFAULT NULL COMMENT '本次对比的基线运行 id（NULL=与默认基线比）',
  baseline_ok TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否未低于基线；0=回退（发布前检查拦住）',
  regression JSON COMMENT '回退明细 {case_key: {baseline, current, reason}}',
  error VARCHAR(1024) NOT NULL DEFAULT '' COMMENT '运行级失败原因（fail-loud，不静默）',
  started_at DATETIME DEFAULT NULL,
  finished_at DATETIME DEFAULT NULL,
  created_by VARCHAR(64) NOT NULL DEFAULT '',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_eval_run (run_key),
  INDEX idx_eval_run_suite (suite, created_at),
  INDEX idx_eval_run_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='评测运行记录';

-- ── 3) 逐用例结果 ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS adh_eval_results (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  run_id BIGINT NOT NULL COMMENT 'adh_eval_runs.id',
  case_key VARCHAR(128) NOT NULL,
  suite VARCHAR(32) NOT NULL,
  passed TINYINT(1) NOT NULL COMMENT '确定性断言结果（不由 LLM 评分决定）',
  reason VARCHAR(1024) NOT NULL DEFAULT '' COMMENT '失败原因，需可诊断',
  score DECIMAL(6,4) DEFAULT NULL COMMENT 'LLM 辅助评分（仅解释质量，可为空）',
  sources JSON COMMENT '归因分桶：解析档位 或 检索来源',
  duration_ms INT NOT NULL DEFAULT 0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_eval_result (run_id, case_key),
  INDEX idx_eval_result_run (run_id),
  INDEX idx_eval_result_case (case_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='评测逐用例结果';

-- ── 4) 评测接口的权限码 ─────────────────────────────────────────────────────
-- 不登记的话，非 admin 调 /api/eval* 的写操作会被 api_permission 中间件 fail-closed 拦掉。
--
-- **两者都是 ai_access='none'**，且不登记 ai_action_key（不生成 LLM 功能工具）：
-- 评测用例是评价 AI 的标尺，绝不能让 AI 自己增删改 —— 那就是自评自证，
-- 比没有评测更危险。这类操作只能由人在页面上做。
INSERT IGNORE INTO adh_perm_registry
  (perm_code, label, module, module_label, description, api_pattern, api_method, menu_key, sort,
   ai_access, ai_action_key, ai_note)
VALUES
  ('eval:read', '查看评测', 'system', '系统配置', '查看评测用例与运行结果',
   '/api/eval*', 'GET', '', 350,
   'none', '', '评测用例是评价 AI 的标尺，不开放给 AI 自行读改（防自评自证）'),
  ('eval:manage', '管理评测', 'system', '系统配置', '增删改评测用例、触发评测运行',
   '/api/eval*', 'POST,PUT,DELETE', '', 351,
   'none', '', '评测用例是评价 AI 的标尺，不开放给 AI 自行增删改（防自评自证）');
