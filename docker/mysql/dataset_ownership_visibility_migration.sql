-- ============================================================================
-- 数据集归属与可见性（owner + visibility）迁移 —— 幂等，可重复执行
--
-- 背景（2026-11-20 用户拍板）：
--   数据集只有「角色授权」一个可见性口径，`owner_id` 仅用于编辑/删除权。结果是
--   chat / SQL Playground 这类「普通用户自建产物」必须靠给创建者角色补授才能被
--   本人与看板用上，副作用是**同角色的人都能看见并使用**；而 `visibility` 列虽在
--   建表时预留，却从未被任何代码消费（API 注释里写着“可见性功能已退役”）。
--
--   现改为两档归属语义（裁决点唯一 = `dataset_service.dataset_visible_to`）：
--     private    仅属主（admin 在管理面可见可编辑）—— chat / Playground 等个人产物
--     workspace  角色授权可见（`adh_role_dataset_access`）—— 内置与人工建模、已共享产物
--   `public` 暂不使用（保留枚举位，不得在页面提供开关）。
--
-- 回填判据（**不擅自收回任何人当前能看到的访问**）：
--   已有角色授权行的数据集 → workspace（保持“同角色可见”的现状）
--   从未被角色授权的数据集   → private（原本就没人能看见；属主因此获得自己的产物）
--   注：admin 在代码层 bypass（全可见），不受本列影响。
--
-- 幂等：information_schema 判列/索引后再 ALTER；回填只改“未定档”的历史行
--      （visibility IS NULL 或 ''），重复执行 0 处变更。
-- 不写库名：库由连接配置决定（环境库可能是 adh2，写死 USE adh 会静默打错 schema）。
-- ============================================================================

-- 1) 可见性列（建表已有；兜住手工建表/半途执行的历史库）
SET @ddl := (SELECT IF(COUNT(*) = 0,
    'ALTER TABLE adh_datasets ADD COLUMN visibility VARCHAR(16) NOT NULL DEFAULT ''workspace'' COMMENT ''可见性: private=仅属主 | workspace=角色授权可见 | public=全体(暂未启用)''',
    'SELECT 1') FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_datasets' AND COLUMN_NAME = 'visibility');
PREPARE s1 FROM @ddl; EXECUTE s1; DEALLOCATE PREPARE s1;

-- 2) 索引：清单按「属主 or 角色授权」过滤，可见性+属主是常查组合
SET @ddl := (SELECT IF(COUNT(*) = 0,
    'ALTER TABLE adh_datasets ADD INDEX idx_ds_visibility (visibility, owner_id)',
    'SELECT 1') FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_datasets' AND INDEX_NAME = 'idx_ds_visibility');
PREPARE s2 FROM @ddl; EXECUTE s2; DEALLOCATE PREPARE s2;

-- 3) 存量回填：先按“是否已有角色授权”定档（只改空档，已定档的不动）
UPDATE adh_datasets d
   SET d.visibility = IF(
        EXISTS (SELECT 1 FROM adh_role_dataset_access rda WHERE rda.dataset_id = d.id),
        'workspace', 'private')
 WHERE d.visibility IS NULL OR d.visibility = '';

-- 3b) 兜底：取值越界（历史手工写入的脏值）收敛到 workspace，不静默改私有
UPDATE adh_datasets
   SET visibility = 'workspace'
 WHERE visibility NOT IN ('private', 'workspace', 'public');

-- ----------------------------------------------------------------------------
-- 4) 核验（应用后人工确认，勿删）
-- ----------------------------------------------------------------------------
-- SELECT visibility, COUNT(*) FROM adh_datasets GROUP BY visibility;
--   期望：workspace = 有角色授权的行；private = 个人产物（origin='ai_session' /
--   Playground 保存）。若某 private 行仍被共享看板引用，说明它的共享级联漏了
--   （共享看板必须同批把引用数据集发布为 workspace，见 screen_tools 建屏链路）。
-- SELECT d.id, d.name, d.owner_id, d.visibility FROM adh_datasets d
--  WHERE d.visibility='private' AND EXISTS (
--        SELECT 1 FROM adh_charts c WHERE c.source_type='dataset' AND c.source_id=d.id);
-- SHOW INDEX FROM adh_datasets WHERE Key_name = 'idx_ds_visibility';
