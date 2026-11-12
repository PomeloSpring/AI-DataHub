-- ============================================================================
-- 「SQL 示例对」功能下线（用户 2026-11-05 拍板: 与数据集重合）
--
-- 为什么是"重合"而不是"重复实现":
--   1) 该页管理的是 adh_knowledge_items 里 knowledge_type='sql_pair' 的行
--      (问答对: question / answer_sql / explanation / related_tables);
--   2) 全仓检索**没有任何取数或提示词消费方读这批行** —— 唯一读 adh_knowledge_items
--      的运行时是 quick_paths.match, 它硬限定 knowledge_type='quick_path'。
--      页面副标题写的"作为 LLM 的 few-shot 参考"因此是空承诺(配了也不进模型视野);
--   3) "一段可复用的业务 SQL + 口径说明 + 关联表"在平台里的正规载体是**数据集**
--      (adh_datasets: SQL/语义双轨 + 参数 + 角色授权 + 被看板/ChatBI/Playground 真实消费)。
--   两个入口做同一件事、其中一个还没下游 —— 去掉后者, 不留"配了没用"的配置面。
--
-- 本批按 menu-permission-sync §1 同批退出(页面/路由/菜单登记/权限点/存量授权):
--   frontend: pages/catalog/SqlPairs.tsx 删除, App.tsx 路由与 DataPlatformLayout 侧栏同批摘除;
--   backend : knowledge.py 的 TYPE_PERMS/TYPE_LABELS 去掉 sql_pair(改登 RETIRED_TYPES,
--             旧调用带该 type 过来回 410 可诊断原因, 不当"未知类型"糊弄);
--             perm_link.SECRET_BOUND_NONE_PERMS 去掉 sql-pairs:*;
--   本文件  : 菜单行 + 权限点 + 存量角色授权一并删除(留任一行都是角色页上的死开关)。
--
-- 数据怎么办: **不删存量 sql_pair 行**。库里当前为 0 行, 但别的部署可能有内容,
--   静默删用户数据不在本批授权范围。读路径已全部下线, 这些行等同冻结;
--   确要清理由人执行(先跑 SELECT 看清再删):
--     -- SELECT id,title,workspace_id FROM adh_knowledge_items WHERE knowledge_type='sql_pair';
--     -- DELETE FROM adh_knowledge_items WHERE knowledge_type='sql_pair';
--
-- 幂等: 全部 DELETE, 可重复执行。
-- ============================================================================
USE adh2;

-- 1) 存量角色授权: 先撤码, 否则角色页会渲染出指向不存在功能的勾选框
DELETE FROM adh_role_perms WHERE perm_code LIKE 'sql-pairs:%';

-- 2) 权限点登记(api_pattern 门控一并消失, 中间件不再为这类路径放行)
DELETE FROM adh_perm_registry WHERE perm_code LIKE 'sql-pairs:%';

-- 3) 菜单登记: 角色权限「菜单与功能」里不再出现「SQL 示例对」
DELETE FROM adh_menu_registry WHERE menu_key = 'data:sql-pairs';

-- 语义层段内排序保持与侧栏一致(本体建模 5 → 建模待办 6 → 分析场景 8);
-- 留空档不影响 ORDER BY sort 的相对顺序, 不为了好看去改别的菜单行。

-- ----------------------------------------------------------------------------
-- 核验(应用后人工确认, 勿删)
-- ----------------------------------------------------------------------------
-- 三张表都查不到 sql-pairs 痕迹:
-- SELECT COUNT(*) FROM adh_menu_registry  WHERE menu_key = 'data:sql-pairs';
-- SELECT COUNT(*) FROM adh_perm_registry  WHERE perm_code LIKE 'sql-pairs:%';
-- SELECT COUNT(*) FROM adh_role_perms     WHERE perm_code LIKE 'sql-pairs:%';
-- 语义层菜单现状(应无 SQL 示例对, 且分析场景仍在):
-- SELECT menu_key,label,section,module,sort,perm_code FROM adh_menu_registry
--   WHERE module='data' AND section='语义层' ORDER BY sort;
-- 模拟非 admin 角色反查菜单, 确认入口不会凭空多/少(menu-permission-sync §5):
-- SELECT DISTINCT m.menu_key, m.label FROM adh_role_perms rp
--   JOIN adh_perm_registry p ON p.perm_code = rp.perm_code
--   JOIN adh_menu_registry m ON m.menu_key = p.menu_key
--  WHERE rp.role_id = 2 AND m.section = '语义层' ORDER BY m.sort;
