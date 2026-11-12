-- ============================================================================
-- 用户级动效偏好迁移：adh_user_preferences 增两列（出现效果的个人选择）
-- ----------------------------------------------------------------------------
-- 口径：
--   * motion_preset_code = 用户选定的动效预设（adh_vis_components.code，category=motion_preset）；
--     **NULL = 跟随系统默认（mp_default）**，语义上是「没设过」，因此不给存量用户回填 ——
--     加列前后解析结果都是 mp_default，属于「行为零变化」的那一半（另一半在前端解析层）。
--   * motion_override = 差量（只存用户显式改过的段/键，如 {"enter":{"durationMs":300}}）。
--     逐键合并、不整段替换：否则只调时长会把预设里的 effect 一起打回默认值
--     （实现见 frontend/src/lib/motion.ts resolveMotion 第 4 步）。
--     差量只在生效预设来自用户偏好时叠加；板上挂载的字模不被观看者本地差量改掉（分面裁决）。
--   * 偏好落库而不是 localStorage：与主题不同，动效要跨设备一致（个人设置页读写）。
--
-- 幂等：建表 IF NOT EXISTS（与 cloud_space_rename_migration.sql 同一份定义，只为让本批可独立
--       应用到只跑过部分迁移的库）；ADD COLUMN 先查 information_schema（MySQL 不支持 IF NOT EXISTS）。
-- ============================================================================

CREATE TABLE IF NOT EXISTS adh_user_preferences (
    user_id BIGINT NOT NULL PRIMARY KEY COMMENT '用户 ID(adh_users.id)',
    cloud_sync_enabled TINYINT(1) NOT NULL DEFAULT 1
        COMMENT '会话产物自动上云: 1=开启(默认), 0=关闭。关闭后产物只留本地, 换节点/清理即丢失',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

SET @ddl := (SELECT IF(COUNT(*) = 0,
    'ALTER TABLE adh_user_preferences ADD COLUMN motion_preset_code VARCHAR(64) NULL DEFAULT NULL
       COMMENT ''动效预设字模编码(adh_vis_components.code, category=motion_preset)；NULL=跟随系统默认(mp_default)''',
    'SELECT 1') FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_user_preferences'
      AND COLUMN_NAME = 'motion_preset_code');
PREPARE s1 FROM @ddl; EXECUTE s1; DEALLOCATE PREPARE s1;

SET @ddl := (SELECT IF(COUNT(*) = 0,
    'ALTER TABLE adh_user_preferences ADD COLUMN motion_override JSON NULL
       COMMENT ''动效差量偏好，只存用户显式改过的段/键，如 {"enter":{"durationMs":300}}；逐键合并进生效预设''',
    'SELECT 1') FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_user_preferences'
      AND COLUMN_NAME = 'motion_override');
PREPARE s2 FROM @ddl; EXECUTE s2; DEALLOCATE PREPARE s2;

-- 注意：本批**只加自己拥有的两列**，写路径也必须按列 upsert
-- （见 backend/modules/auth/services/user_preference_service.py：禁止整行覆盖，否则会把
--   cloud_sync_enabled 这类「别的批次拥有的列」刷回默认值 —— 历史缺陷同类）。

-- ============================================================================
-- 核验（人工执行，不参与建库）
--   SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS
--     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_user_preferences';
--   预期：motion_preset_code / motion_override 两行存在且 IS_NULLABLE='YES'（NULL=未设置），
--         cloud_sync_enabled 的定义与本批前一致（未被改动）。
--   SELECT COUNT(*) FROM adh_user_preferences WHERE motion_preset_code IS NOT NULL;
--   预期：0（不给存量用户回填，默认口径由解析层负责）。
-- ============================================================================
