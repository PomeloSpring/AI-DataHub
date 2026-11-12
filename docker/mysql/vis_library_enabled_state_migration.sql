-- ============================================================================
-- 字模库「禁用」状态：adh_vis_components.is_enabled
-- ----------------------------------------------------------------------------
-- 背景（2026-11-12 反馈）：字模库此前只有"下架"（软删除 is_active=0）一档，
-- 管理员想临时停用一套主题包（既不想被套用、也不想系统主题里还能选）没有入口：
-- 下架会把条目从清单里摘掉、历史引用只能靠挂载快照兜底，等于"删了"而不是"停用"。
--
-- 两列各管一件事，不得混用：
--   is_active  0=已下架（软删除）：清单里不再给出，历史引用回退创建时快照；
--   is_enabled 0=已禁用（停用）  ：条目仍在库里可查、可再启用，但一律**不可被选用**。
--
-- 禁用的生效口径（唯一裁决点：前端 lib/dashboardDesign.ts 的 isVisUsable、
-- 后端 vis_library_service.list_components(include_disabled=False)）：
--   1) 大屏/看板编辑器「UI 字模库」面板、报表模板主题下拉、AS-BOT list_vis_components
--      都不再给出该字模（主题包无法套用）；
--   2) theme_pack 对应的系统主题（暗色/亮色/科技风/…）从顶栏主题菜单里摘掉（不显示，
--      而不是留一个点不动的灰项）；正在使用该主题的人会被切到可用主题并收到提示；
--   3) 报告主题令牌按"未读到"处理并在提示词里显式写明原因（no-silent-degradation），
--      不得静默换成别的主题包。
--
-- 幂等：DDL 经 information_schema 守卫，可重复执行；存量行默认 1（启用），行为零变化。
-- ============================================================================

-- ── 1) 加列（幂等）────────────────────────────────────────────────────────
SET @col_exists = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_vis_components'
      AND COLUMN_NAME = 'is_enabled'
);
SET @ddl = IF(@col_exists = 0,
    'ALTER TABLE adh_vis_components ADD COLUMN is_enabled TINYINT(1) NOT NULL DEFAULT 1
     COMMENT ''1=启用 0=禁用（停用但保留：不可被选用，主题包不能套用、系统主题菜单里不再出现）'' AFTER is_active',
    'SELECT 1');
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ── 2) 存量回填：只在列刚建出来时兜一次（DEFAULT 已给 1，此处幂等无副作用）──
UPDATE adh_vis_components SET is_enabled = 1 WHERE is_enabled IS NULL;

-- ── 3) 核验（人工执行，不参与建库）───────────────────────────────────────
--    SELECT code, name, category, is_active, is_enabled FROM adh_vis_components
--      WHERE category = 'theme_pack' ORDER BY sort_order;
--    预期：九套主题包 + tp_as 均在，is_enabled 全为 1（禁用后该行为 0）。
