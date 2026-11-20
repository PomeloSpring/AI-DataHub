-- ============================================================================
-- 「建模待办」独立页下线，并入「本体建模」（用户 2026-11-19 拍板）
--
-- 用户原话：「建模代办就不要跳转了，直接在里面实现，外部的建模代办菜单去掉」。
--
-- 为什么并回建模页而不是继续单页：
--   1) 待办清单处理完必须回建模页（补对象/补别名/改字典），跳来跳去丢上下文；
--   2) 入口两个（侧栏菜单 + 建模页左栏）指向同一份清单，人要在两处找同一个东西；
--   3) 待办是跨模型清单，这只需要一个页签表达，不需要一个独立路由。
--
-- 本批按 menu-permission-sync §1 同批退出（页面/路由/菜单登记/权限点/存量授权）：
--   frontend: App.tsx 的 /data/asset-pool 路由与 import 摘除；
--             DataPlatformLayout 侧栏项摘除（menu.json 的 data.assetPool 同步删除）；
--             AssetPoolWorkbench 改为「本体建模」左栏「跨模型」分区的页内页签
--             （key='todo'，与「全部模型」同段），不再有 link 跳转；
--             原「建模体检」也跳同一页，现同样落在页内 todo 页签（原跳转是死链来源）；
--   本文件  : 菜单行 + 权限点 + 存量角色授权一并删除（留任一行都是角色页上的死开关）。
--
-- 权限口径变化：入口显隐从 `asset-pool:read` 收回 `data:ontology`（本体建模菜单）。
--   页面主数据接口 /api/metrics/asset-pool 仍按 metrics:read 治理，读权限未放宽。
--   `metrics:manage`（归属/删除字典行）等出口权限点不动。
--
-- 幂等：全部 DELETE，可重复执行。
-- ============================================================================
USE adh2;

-- 1) 存量角色授权：先撤码，否则角色页会渲染出指向不存在入口的勾选框
DELETE FROM adh_role_perms WHERE perm_code LIKE 'asset-pool:%';

-- 2) 权限点登记
DELETE FROM adh_perm_registry WHERE perm_code LIKE 'asset-pool:%';

-- 3) 菜单登记：角色权限「菜单与功能」里不再出现「建模待办」
DELETE FROM adh_menu_registry WHERE menu_key = 'data:asset-pool';

-- 语义层段内排序留空档不影响 ORDER BY sort 的相对顺序，不为好看去改别的菜单行。

-- ----------------------------------------------------------------------------
-- 核验（应用后人工确认，勿删）
-- ----------------------------------------------------------------------------
-- 三张表都查不到 asset-pool 痕迹（应各为 0）：
-- SELECT COUNT(*) FROM adh_menu_registry WHERE menu_key = 'data:asset-pool';
-- SELECT COUNT(*) FROM adh_perm_registry WHERE perm_code LIKE 'asset-pool:%';
-- SELECT COUNT(*) FROM adh_role_perms    WHERE perm_code LIKE 'asset-pool:%';
-- 数据中台「数据目录」段现状（应无建模待办，本体建模仍在）：
-- SELECT menu_key,label,section,perm_code,sort FROM adh_menu_registry
--   WHERE module='data' AND section='数据目录' ORDER BY sort;
-- 模拟 analyst 的菜单反查，确认入口不会凭空多/少：
-- SELECT DISTINCT m.menu_key, m.label FROM adh_role_perms rp
--   JOIN adh_perm_registry p ON p.perm_code = rp.perm_code
--   JOIN adh_menu_registry m ON m.menu_key = p.menu_key
--  WHERE rp.role_id = 2 ORDER BY m.menu_key;
