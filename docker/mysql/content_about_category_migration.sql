-- ---------------------------------------------------------------------------
-- 新增内容栏目「关于本公司」（category_key=about）
--
-- 用途：首页 Hero 右侧的「企业简介」卡消费这个栏目下的在线内容。企业简介是
-- **内容资产**而不是前端文案——走内容中心的创作→审核→发布链路，改介绍不需要发版，
-- 也不在代码里硬编码（首页只消费发布结果，见 d881913f 的权限设计范式）。
--
-- sort=0：排在栏目清单最前。首页简介卡按"该栏目最新一篇在线内容"取，
-- 与资讯流的「最新/最热/精选」排序互不干扰。
--
-- 幂等：ON DUPLICATE KEY 只更新本行自己拥有的列，可重复执行。
-- ---------------------------------------------------------------------------
INSERT INTO adh_content_categories (category_key, label, description, icon, color, sort, is_active)
VALUES ('about', '关于本公司', '公司业务版图、发展脉络与前景（公开口径）', '🏢', '#475569', 0, 1)
ON DUPLICATE KEY UPDATE
  label = VALUES(label),
  description = VALUES(description),
  icon = VALUES(icon),
  color = VALUES(color),
  sort = VALUES(sort),
  is_active = VALUES(is_active);
