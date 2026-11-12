-- ============================================================================
-- 内容版本管理（期次）+ 报告中心提升通道 —— 幂等，可重复执行
--
-- 需求（用户 2026-11-12）：同一个主题会发布不同版本（例如每月一次的行业分析报告），
-- 门户每次都展示最新的一期，读者点进去还能看到历史期次；但也有内容只保留最新的。
-- 另外要支持从工作空间的「报告中心」把报告提交到内容管理审核。
--
-- 落地口径（与 backend/modules/platform/services/content_service.py 逐条对应）：
--   1) **期次 = 已发布的 revision**。正文本来就按 revision 存（head/live 双指针），
--      本批不新建"每期一条内容"的表：一个主题 = 一条条目（slug 稳定、门户一张卡片），
--      发布这个动作让被发布的 revision 成为一个已发布期次（release_seq 在本篇内单调
--      递增、release_label 是人话名如 2026-10）。读者面寻址用**期次号**，不把
--      revision id 下发（护栏 §7：内部标识不进响应体）。
--   2) **门户展示的仍是 live_revision_id**（= 当前这一期），不引入第二个"哪一期在线"
--      的事实源；历史期次是 live 之外那些 release_seq>0 的 revision，列表由流水算。
--   3) **versioning 是读者可见性策略，不是删除开关**：keep_history=读者可看历史期次；
--      latest_only=只展示最新一期，旧期次不再对读者出现，但编排面版本清单、审核流水、
--      切换展示版本（回滚）照常。切回 keep_history 即恢复可见 —— 不毁数据、不可逆操作
--      一条都不做（与「附件不进版本链」同一批显式取舍的写法：能把后果说清就不删东西）。
--   4) **提升通道复用编辑权**：`/api/content/promotions*` 追加进 article:manage 已登记的
--      api_pattern（不新增权限码、不新增菜单：写草稿这件事的裁决点本来就在那一码上）。
--      这里**只动 api_pattern 一列** —— ai_action_key / ai_access / description 分属
--      content_ai_action_migration.sql 与 home_feed_migration.sql 拥有，历史缺陷是
--      幂等 upsert 把别人拥有的列按本行的空串覆盖回去（AI 工具因此静默消失）。
--   5) **回滚（切换展示版本）进审核流水**：action 取值补 rollback，注释是这套状态机的
--      可读文档，飘了就会有人以为库里不该出现这个值（与 republish 那批同一写法）。
--
-- 执行: venv/bin/python logs/apply_migration.py docker/mysql/content_release_migration.sql
--       （SET @ddl + PREPARE/EXECUTE 依赖会话变量，必须单连接顺序执行，不能逐句借还连接；
--        切句按行尾分号做，所以**字符串字面量内不得出现分号**）
-- ============================================================================

USE adh2;

-- ----------------------------------------------------------------------------
-- 1) adh_content_revisions：期次三列（0/空 = 这一版从没发布过，只是编辑历史）
-- ----------------------------------------------------------------------------
SET @ddl := (SELECT IF(COUNT(*) = 0,
    'ALTER TABLE adh_content_revisions ADD COLUMN release_seq INT NOT NULL DEFAULT 0
       COMMENT ''本篇内单调递增的期次号(发布时分配)，0=从未发布过，只是编辑历史''',
    'SELECT 1') FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_content_revisions'
      AND COLUMN_NAME = 'release_seq');
PREPARE s1 FROM @ddl; EXECUTE s1; DEALLOCATE PREPARE s1;

SET @ddl := (SELECT IF(COUNT(*) = 0,
    'ALTER TABLE adh_content_revisions ADD COLUMN release_label VARCHAR(64) NOT NULL DEFAULT ''''
       COMMENT ''期次业务名(发布人填, 如 2026-10)，缺省按发布日期 YYYY-MM 自动生成''',
    'SELECT 1') FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_content_revisions'
      AND COLUMN_NAME = 'release_label');
PREPARE s2 FROM @ddl; EXECUTE s2; DEALLOCATE PREPARE s2;

SET @ddl := (SELECT IF(COUNT(*) = 0,
    'ALTER TABLE adh_content_revisions ADD COLUMN released_at DATETIME NULL
       COMMENT ''这一版首次上线时间(历史期次清单按它排序)''',
    'SELECT 1') FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_content_revisions'
      AND COLUMN_NAME = 'released_at');
PREPARE s3 FROM @ddl; EXECUTE s3; DEALLOCATE PREPARE s3;

-- 读者/编辑两侧取"本篇已发布期次"都是按 (article_id, release_seq) 走，序号即寻址键
SET @ddl := (SELECT IF(COUNT(*) = 0,
    'ALTER TABLE adh_content_revisions ADD INDEX idx_article_release (article_id, release_seq)',
    'SELECT 1') FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_content_revisions'
      AND INDEX_NAME = 'idx_article_release');
PREPARE s4 FROM @ddl; EXECUTE s4; DEALLOCATE PREPARE s4;

-- ----------------------------------------------------------------------------
-- 2) adh_content_articles：版本策略列（存量行按 DEFAULT 落 keep_history，
--    等于"上线前后读者看到的东西不变"——历史可见性不因为加了这列而收紧）
-- ----------------------------------------------------------------------------
SET @ddl := (SELECT IF(COUNT(*) = 0,
    'ALTER TABLE adh_content_articles ADD COLUMN versioning VARCHAR(16) NOT NULL DEFAULT ''keep_history''
       COMMENT ''读者可见性策略: keep_history=可看历史期次 | latest_only=只展示最新一期(旧期次仅编排面留档)''',
    'SELECT 1') FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_content_articles'
      AND COLUMN_NAME = 'versioning');
PREPARE s5 FROM @ddl; EXECUTE s5; DEALLOCATE PREPARE s5;

-- ----------------------------------------------------------------------------
-- 3) 存量回填：把"当前在线的那一版"记成第 1 期
--    只在 release_seq=0 时写，可重复执行；不猜历史 —— 本批之前发布过几版无从追溯
--    （reviews 流水里的 publish 行确实能数出次数，但那些 revision 的正文当时没有
--     期次语义，硬编成"第 N 期"会让读者以为有一串历史版本能翻，实际翻出来全是
--     同一次改稿）。所以每篇只回填第 1 期，之后的期次由发布动作正常积累。
-- ----------------------------------------------------------------------------
UPDATE adh_content_revisions r
  JOIN adh_content_articles a ON a.id = r.article_id
SET r.release_seq   = 1,
    r.released_at   = COALESCE(a.published_at, a.updated_at),
    r.release_label = DATE_FORMAT(COALESCE(a.published_at, a.updated_at), '%Y-%m')
WHERE r.id = a.live_revision_id
  AND a.live_revision_id <> 0
  AND r.release_seq = 0;

-- ----------------------------------------------------------------------------
-- 4) 审核流水 action 注释补 rollback（切换展示版本必须留痕，与 republish 同理）
--    列本身 VARCHAR(16)，rollback 长 8 字符，无需改类型/长度。
-- ----------------------------------------------------------------------------
ALTER TABLE adh_content_reviews
  MODIFY COLUMN action VARCHAR(16) NOT NULL
  COMMENT 'submit|approve|reject|waive|reassign|publish|archive|republish|rollback';

-- ----------------------------------------------------------------------------
-- 5) article:manage 追加提升通道 pattern（只动 api_pattern；CONCAT + NOT LIKE 守卫
--    = 重复执行不会追加第二份，也不会覆写别的批次拥有的列）
-- ----------------------------------------------------------------------------
UPDATE adh_perm_registry
SET api_pattern = CONCAT(api_pattern, ',/api/content/promotions*')
WHERE perm_code = 'article:manage'
  AND api_pattern NOT LIKE '%/api/content/promotions%';

-- ----------------------------------------------------------------------------
-- 6) 核验（应用后人工确认，勿删）
-- ----------------------------------------------------------------------------
-- SELECT COLUMN_NAME, COLUMN_TYPE, COLUMN_DEFAULT, IS_NULLABLE
--   FROM information_schema.COLUMNS
--  WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='adh_content_revisions'
--    AND COLUMN_NAME IN ('release_seq','release_label','released_at');        -- 期望 3 行
-- SELECT COLUMN_NAME, COLUMN_DEFAULT FROM information_schema.COLUMNS
--  WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='adh_content_articles'
--    AND COLUMN_NAME='versioning';                                           -- 期望 keep_history
-- SELECT COUNT(*) AS released FROM adh_content_revisions WHERE release_seq>0;
--   期望：等于"当前在线条目数"（每篇回填第 1 期）
-- SELECT perm_code, api_pattern, ai_action_key FROM adh_perm_registry
--  WHERE perm_code='article:manage';
--   期望：api_pattern 末尾多一条 /api/content/promotions*，且 ai_action_key 仍是
--   content_article.list,...,content_review.submit 六个动作（一字未被本迁移改动）。
-- SELECT DISTINCT action FROM adh_content_reviews;                           -- 含 rollback 为正常
