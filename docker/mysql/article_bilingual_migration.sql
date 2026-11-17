-- ============================================================================
-- 内容中心文章双语化（按语言各一条 + 译文关联）— 加列 + 回填（幂等，可重复执行）
--
-- 背景（用户 2026-11-16 拍板）: 已发布文章要能出英文版。正文很长且要走
--   draft → in_review → approved → published 状态机，**不能**把中英正文塞进同一份
--   revision（那样一次审核要同时校双语、HTML 版式体积翻倍、英文版无法晚于中文上线）。
--   因此按语言各建一条文章，用 `translation_group` 关联：
--     * `lang`              —— 本篇正文的语言（zh-CN / en-US）；
--     * `translation_group` —— 同一篇内容跨语言的分组键，**取中文原条目的 slug**
--       （中文条目自关联自己，英文条目指向中文条目）。这样存量行天然成组，
--       不需要第二张映射表；
--     * `i18n`              —— 标题/摘要的双语覆盖层（短字段，与元数据同一口径）。
--   消费面（首页资讯流 / `/read/:slug`）按界面语言优先取对应语言在线版，
--   该语言无在线版时**返回另一语言版本并在响应里标 english_available=false**，
--   前端显示"该篇暂无英文版"——不得静默换语言（no-silent-degradation）。
--
-- 与slug的关系: 英文条目 slug 由创建方给（批量工具用 ASCII 短名 + `-en` 后缀），
--   前端**不靠命名约定猜译文**，一律用后端返回的 translation.slug 跳转。
--
-- 幂等纪律: ADD COLUMN / ADD KEY 走 information_schema 判定 + PREPARE，
--   回填只处理 translation_group = '' 的行，第二次执行 0 行受影响。
-- ============================================================================

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_content_articles'
      AND COLUMN_NAME = 'lang') = 0,
  'ALTER TABLE adh_content_articles ADD COLUMN lang VARCHAR(16) NOT NULL DEFAULT ''zh-CN'' COMMENT ''本篇正文语言（zh-CN|en-US），与界面语言同一受控清单''',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_content_articles'
      AND COLUMN_NAME = 'translation_group') = 0,
  'ALTER TABLE adh_content_articles ADD COLUMN translation_group VARCHAR(64) NOT NULL DEFAULT '''' COMMENT ''同一内容跨语言的分组键（取中文原条目 slug，中文条目自关联）''',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_content_articles'
      AND COLUMN_NAME = 'i18n') = 0,
  'ALTER TABLE adh_content_articles ADD COLUMN i18n JSON NULL COMMENT ''非默认语言译文（title/summary），正文不在此列（按语言各一条）''',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 回填分组键：存量条目全部是中文，自关联即成组（不回填就等于译文找不到原文）
UPDATE adh_content_articles
   SET translation_group = slug
 WHERE translation_group = ''
   AND slug <> '';

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_content_articles'
      AND INDEX_NAME = 'idx_article_lang_group') = 0,
  'ALTER TABLE adh_content_articles ADD KEY idx_article_lang_group (lang, translation_group)',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 核验（应用后执行）:
--   SELECT lang, COUNT(*) c, SUM(translation_group = slug) self_grouped
--     FROM adh_content_articles GROUP BY lang;
