-- ---------------------------------------------------------------------------
-- 收敛存量孤儿审核流水（幂等，可重复执行）
--
-- 背景：`adh_content_reviews / _revisions / _files / _user_state / _kb_sync` 都没有建外键，
-- 删条目时的清理全靠 content_service.delete_article 里的显式 DELETE。历史上那一串
-- 只写了四条，漏了 reviews —— 于是每次"删草稿"都留下一批指向不存在条目的流水行。
-- 服务层已补上第五条 DELETE（见 delete_article 的注释），本文件只负责收敛**存量**残留。
--
-- 危害不是脏数据好看与否：门户与「审核发布」页都按 article_id 取流水，孤儿行渲染不出来，
-- 但任何按 article_id 聚合的巡检/统计会把它当成"另有内容没走完流程"（本批实测就撞上了
-- 5 行 submit 流水）。这些行指向的版本快照已被删除，没有追溯价值，删。
--
-- 注意：只删 article_id 已不存在的行，任何在线/草稿条目的审计流水都不受影响。
-- ---------------------------------------------------------------------------
DELETE r FROM adh_content_reviews r
LEFT JOIN adh_content_articles a ON a.id = r.article_id
WHERE a.id IS NULL;
