-- ---------------------------------------------------------------------------
-- 「重新上架」(republish) 动作登记（幂等，可重复执行）
--
-- 背景：内容中心此前只有 publish / archive 两个改门户可见性的动作，已下架的条目
-- 没有任何回到线上的入口（只能重新走一遍创作→审核→发布，且管理页连按钮都不给）。
-- 现在补 republish：把**下架时在线的那一版**放回门户（live := 那一版）。被放回的
-- 这一版当初就是审核通过才上线的，下架只收门户入口、不推翻审核结论，因此不需要
-- 重新审核；它**不会**把下架之后新改的 head 顺带放出去（那一版没审过）。
--
-- 本文件只做一件事：把 adh_content_reviews.action 的取值注释补上 republish，
-- 让 information_schema 与 `content_service.republish_article` 写下的流水一致
-- （注释是这套状态机的可读文档，飘了就会有人以为库里不该出现这个值）。
-- 列本身是 VARCHAR(16)，republish 长 9 字符，无需改类型/长度。
--
-- 幂等：MODIFY COLUMN 重复执行结果相同，不动数据、不动索引。
-- ---------------------------------------------------------------------------
ALTER TABLE adh_content_reviews
  MODIFY COLUMN action VARCHAR(16) NOT NULL
  COMMENT 'submit|approve|reject|waive|reassign|publish|archive|republish';
