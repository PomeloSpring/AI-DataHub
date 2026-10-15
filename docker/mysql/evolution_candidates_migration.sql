-- ============================================================================
-- 自进化闭环（反馈 → 定时归纳 → 候选 → 人工审核四出口）迁移 (幂等, 无 USE)
--
-- 现状缺口（为什么必须建这套东西）
-- ----
-- 1) **采集近乎为零**：adh_message_feedback 全库 1 条。前端踩之后的引导是
--    placeholder="期望的表名（可选，如 t_user_customer）" —— 让业务用户手输
--    **物理表名**，同时违反 ui-resource-display §2（不得手填内部标识）与
--    护栏 §7（物理表名向客户端暴露）。
-- 2) **归纳完全没有**：system_jobs.BUILTIN_JOBS 只有 runs_reconcile /
--    kb_sync_reconcile；反馈表与别名建议队列表**零关联**，踩了不产生任何候选；
--    absorb_business_terms.py / check_alias_projection.py 是手动 dry-run 脚本，
--    未进调度也未进 UI。
-- 3) **闭环在最需要它的场景是断的**：adh_alias_suggestions 现存 9 条 pending 里
--    8 条 target_ref 为空（DAU / activation_rate / 站点 / 企业省份 …正是"指标还
--    没建"的真实缺口），而 alias_suggestion._approve_dict_alias 对不存在的字典
--    行直接 raise —— 越需要归纳的词，审核越点不通。
--
-- 关键设计
-- --------
-- * **单一事实源**：候选一律落 adh_evolution_candidates。adh_alias_suggestions
--   的历史行搬迁后退役为冻结表（保留结构与历史，任何读路径不得消费），不保留双写
--   —— 两处真值源迟早飘。
-- * **分类必须可解释**：classify_reason 逐条落库。拿正则或"看起来像指标"糊弄
--   分类，人就只能凭直觉审核，闭环退化成猜谜。
-- * **binding_gap 单独成类**（实测后修正的分类判据）：旧队列 9 条里 7 条的指标/维度
--   其实早已在字典 is_active=1，只是 target_table/bound_object_key 与对象主表不一致
--   而对本对象不可达。归成"缺资产"会引人在字典里建重复行（同一概念两个口径，
--   比缺指标更难发现），归成"缺别名"也治不了不可达；必须指向"核绑定"。
-- * **四出口不另造权限**：转字典草稿/收编别名/转术语都复用既有 service 与权限码，
--   转评测用例复用 eval/store.create_case（评测侧 ai_access='none' 的纪律不变：
--   评价 AI 的标尺不能由 AI 自己写）。
-- * **验证闭环可证伪**：converted_ref 指向评测用例时，下一次运行 pass 才置
--   verified，仍 fail 置 stale 并可见 —— 不许"转出去了"就当"修好了"。
-- * `eval_failure` 只能由人在评测页点「转候选」时产生，**归纳作业不自动扫失败**：
--   作业要自动判读就得落逐用例 observed 快照（新增存储），拿失败文案里的自由文本
--   去猜"缺什么"只会造伪候选。自动接法待单独评审，不在这里静默占位。
-- ============================================================================

DELIMITER $$
DROP PROCEDURE IF EXISTS adh_evo_add_column$$
CREATE PROCEDURE adh_evo_add_column(IN tbl VARCHAR(64), IN col VARCHAR(64), IN definition TEXT)
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
DROP PROCEDURE IF EXISTS adh_evo_add_index$$
CREATE PROCEDURE adh_evo_add_index(IN tbl VARCHAR(64), IN idx VARCHAR(64), IN definition TEXT)
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
-- 1) 进化候选（三类信号源汇聚后的唯一待办实体）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS adh_evolution_candidates (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  term            VARCHAR(128) NOT NULL COMMENT '待收编/待建档的业务词（只存业务名，不存物理列）',
  kind            ENUM('new_metric','new_dimension','alias','binding_gap','term') NOT NULL
                  COMMENT '处置方向：建指标草稿/建维度草稿/收编为已有别名/核绑定可达性/进术语缓冲区',
  source          ENUM('parse_fail','feedback','script','eval_failure') NOT NULL
                  COMMENT '信号来源：语义解析失败 / 用户点踩反馈 / 术语收编脚本 / 评测失败（页面上人工点「转候选」时写入）',
  datasource_name VARCHAR(128) NOT NULL DEFAULT '' COMMENT '所属数据源业务名（全局唯一，删重建仍可对上）',
  object_hint     VARCHAR(128) NOT NULL DEFAULT '' COMMENT '建议归属的本体对象业务名（选择框来源，非物理表）',
  target_ref      VARCHAR(128) NOT NULL DEFAULT '' COMMENT 'kind=alias 时的目标字典名/对象 key',
  target_type     VARCHAR(32)  NOT NULL DEFAULT '' COMMENT 'kind=alias 时的目标类型 metric|dimension|object',
  classify_reason VARCHAR(512) NOT NULL DEFAULT '' COMMENT '为什么判成该 kind（planner 结构化 type / 命中候选 / 用户所选原因），可审计',
  evidence        JSON COMMENT '证据：问句样本/trace_id/hit 次数/候选清单；禁含物理表列与 SQL',
  hit_count       INT NOT NULL DEFAULT 1 COMMENT '同词重复出现次数（去重累加，排序依据）',
  status          ENUM('pending','converted','rejected','stale','verified') NOT NULL DEFAULT 'pending'
                  COMMENT 'converted=已执行出口动作; verified=转成的评测用例已通过; stale=转出去了但用例仍失败',
  converted_ref   VARCHAR(128) NOT NULL DEFAULT '' COMMENT '产出物标识：字典行业务名 / 评测用例 case_key',
  converted_kind  VARCHAR(32)  NOT NULL DEFAULT '' COMMENT '产出物类型：metric|dimension|alias|term|eval_case',
  decided_by      VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '审核人（服务端注入，不信任请求体）',
  decided_at      DATETIME NULL,
  last_checked_at DATETIME NULL COMMENT '最近一次由归纳作业回填 verified/stale 的时间',
  created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_evo_term (term, datasource_name, kind),
  INDEX idx_evo_status (status, hit_count),
  INDEX idx_evo_converted (converted_kind, converted_ref)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='自进化候选（反馈/解析失败/脚本归纳出的语义资产待办）';

-- ----------------------------------------------------------------------------
-- 2) 反馈采集：不再要用户输物理表名，改为业务名 + 不满意原因分派
--    expected_table 冻结（保留历史，任何新读路径不得消费）；新口径走 expected_ref
-- ----------------------------------------------------------------------------
CALL adh_evo_add_column('adh_message_feedback', 'dissatisfaction_reason',
    "VARCHAR(32) NOT NULL DEFAULT '' COMMENT 'wrong_answer|not_understood|missing_metric|missing_dimension|data_incomplete；决定归纳成哪类候选'");
CALL adh_evo_add_column('adh_message_feedback', 'expected_ref',
    "VARCHAR(128) NOT NULL DEFAULT '' COMMENT '用户期望的业务对象/指标/维度名（选择框来源），替代 expected_table 的物理表名'");
CALL adh_evo_add_column('adh_message_feedback', 'digest_status',
    "VARCHAR(16) NOT NULL DEFAULT 'new' COMMENT '归纳作业水位: new|consumed|ignored，避免同一批负反馈被反复归类'");
CALL adh_evo_add_index('adh_message_feedback', 'idx_evo_digest',
    'INDEX idx_evo_digest (digest_status, satisfied, id)');

-- ----------------------------------------------------------------------------
-- 3) 旧队列表的历史搬迁不在这里做
--    kind 分类是业务规则（依赖 planner 结构化 type + 字典命中判定），必须只有
--    一个实现口径：放在 evolution_service.classify_term()，由
--    `venv/bin/python -m backend.modules.catalog.services.evolution_service backfill`
--    执行（幂等）。在 SQL 里再写一份 CASE 分类 = 两处真值源，迟早飘。
--    adh_alias_suggestions 搬迁后退役为冻结表：保留结构与历史，新读路径不得消费。
-- ----------------------------------------------------------------------------
