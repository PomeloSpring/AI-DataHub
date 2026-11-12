-- ============================================================================
-- 「工作站」→「云空间」改名 + 菜单权限配置同步 + 会话产物上云偏好（2026-11-04）
--
-- 改名范围（口径已确认，两轮纠偏后的最终版）：
--   1) 只退役「工作站」这个**称呼**：侧栏分组标题「工作站」→「云空间」；
--   2) 实体名「工作空间」**不改**，默认空间名「xx的工作站」→「xx的工作空间」
--      （早前一版误把它改成「xx的云空间」，本文件 §5 纠偏回实体名）；
--   3) 「云空间」此后**专指账号级的对话产物备份层**（对象存储镜像），与"某个空间"无关：
--      上云=备份（换节点/清理不丢），归档=资产（跨会话可 @ 引用），两件事不混用。
--
-- 菜单权限配置同批落地（menu-permission-sync §2 五件事）：
--   1) 登记 `workspace:assets`（侧栏「云空间」段下的资产清单入口，此前是前端
--      「恒可见」例外、库里无行 → 角色页配不到）；
--   2) 给它挂权限点 `asset:read`（角色页渲染得出、勾得了，不是死开关）；
--   3) 存量角色默认授权 `asset:read`：改为按权限码反查后必须给全部生效角色默认
--      授予，否则老用户入口凭空消失（管理员仍可按角色收回）；
--   4) 同段排序与侧栏一致：数据看板(1)→智能问数(2)→报表中心(3)→资产清单(4)→任务调度(5)；
--   5) 核验 SQL 见文末。
--
-- 另建 `adh_user_preferences`：用户级偏好（会话产物自动上云开关，默认开启）。
--   无行 = 取默认值，故不给存量用户回填。
--
-- 幂等：登记用 INSERT ... ON DUPLICATE KEY UPDATE，授权用 INSERT IGNORE，建表用
--   IF NOT EXISTS，替换用 REPLACE（未命中即空更新），可重复执行。
-- 施加：mysql -h... -P... -u... -p DATABASE < 本文件
-- ============================================================================

-- ── 1. 用户偏好表（会话产物自动上云开关）────────────────────────────────────
CREATE TABLE IF NOT EXISTS adh_user_preferences (
    user_id BIGINT NOT NULL PRIMARY KEY COMMENT '用户 ID(adh_users.id)',
    cloud_sync_enabled TINYINT(1) NOT NULL DEFAULT 1
        COMMENT '会话产物自动上云: 1=开启(默认), 0=关闭。关闭后产物只留本地, 换节点/清理即丢失',
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户级偏好(取值保守默认, 无行即取默认值)';

-- ── 2. 菜单登记（侧栏「云空间」段）──────────────────────────────────────────
INSERT INTO adh_menu_registry (menu_key, label, section, module, perm_code, ai_access, sort, is_active)
VALUES ('workspace:assets', '资产清单', '云空间', 'workspace', 'asset:read', 'read', 4, 1)
ON DUPLICATE KEY UPDATE
    label = VALUES(label),
    section = VALUES(section),
    module = VALUES(module),
    ai_access = VALUES(ai_access),
    sort = VALUES(sort),
    is_active = 1;

-- 同段排序让位：任务调度顺延到 5（侧栏顺序 数据分析 → 云空间 → 自动化）
UPDATE adh_menu_registry SET sort = 5 WHERE menu_key = 'workspace:scheduled' AND module = 'workspace';

-- ── 3. 权限点登记 ───────────────────────────────────────────────────────────
INSERT INTO adh_perm_registry
    (perm_code, label, module, module_label, description, api_pattern, api_method, menu_key, sort, is_active, ai_access, ai_action_key, ai_note)
VALUES
    -- 云空间段资产清单入口的显隐反查点。接口本身是属主自检域（/api/assets*、
    -- /api/workspace-assets/* 见 api_permission._SELF_ROUTE_PATTERNS，不经权限码拦），
    -- 故 api_pattern 留 NULL：本码只管入口显隐，不新造第二套“谁能做什么”判断。
    ('asset:read', '查看资产清单', 'workspace', '工作空间',
     '查看「云空间」段的资产清单入口（归档收藏的会话产物，跟随用户跨工作空间）。资产读写接口按属主自检治理，本码只裁决入口显隐',
     NULL, NULL, 'workspace:assets', 181, 1, 'read', '',
     '资产清单是人浏览/下载入口；LLM 侧资产能力由 assets 工具组授权裁决，不由本码驱动')
ON DUPLICATE KEY UPDATE
    label = VALUES(label),
    module = VALUES(module),
    module_label = VALUES(module_label),
    description = VALUES(description),
    menu_key = VALUES(menu_key),
    sort = VALUES(sort),
    ai_access = VALUES(ai_access),
    ai_note = VALUES(ai_note),
    is_active = 1;

-- 菜单 perm_code 以权限点为真值源回填（幂等重跑时防两侧飘移）
UPDATE adh_menu_registry m
  JOIN adh_perm_registry p ON p.menu_key = m.menu_key AND p.perm_code = 'asset:read'
SET m.perm_code = p.perm_code
WHERE m.menu_key = 'workspace:assets';

-- ── 4. 存量角色默认授权（维持改名前的入口可见现状）──────────────────────────
INSERT IGNORE INTO adh_role_perms (role_id, perm_code, granted_at)
SELECT r.id, 'asset:read', NOW()
FROM adh_roles r
WHERE r.is_active = 1;

-- ── 5. 存量空间名纠偏（显示用 name，见 ui-resource-display）─────────────────
-- 「工作站」称呼退役 → 实体名「工作空间」；首版迁移误改出的「xx的云空间」一并纠回，
-- 因为云空间已定义为账号级备份层，拿它当某个空间的名字会让两处语义互相打架。
-- 纠偏只作用于自动创建的默认空间（is_default=1），不碰用户自己起的含「云空间」的名字。
UPDATE adh_workspaces
   SET name = REPLACE(name, '的工作站', '的工作空间')
 WHERE name LIKE '%的工作站%';
UPDATE adh_workspaces
   SET name = REPLACE(name, '的云空间', '的工作空间')
 WHERE is_default = 1 AND owner_id IS NOT NULL AND name LIKE '%的云空间%';
UPDATE adh_workspaces
   SET description = REPLACE(REPLACE(REPLACE(REPLACE(description,
           '个人工作站', '个人工作空间'), '默认工作站', '默认工作空间'),
           '个人云空间', '个人工作空间'), '默认云空间', '默认工作空间')
 WHERE description LIKE '%工作站%' OR description LIKE '%个人云空间%' OR description LIKE '%默认云空间%';

-- ── 6. 纠偏：还原被误改的「工作空间」配置面文案 ─────────────────────────────
-- 背景：本迁移首版把「工作站」和「工作空间」一起替换成了「云空间」，范围过大。
-- 实体名保持「工作空间」，因此这里按行还原（幂等：已正确的行不再命中）。
UPDATE adh_menu_registry
   SET label = '工作空间管理'
 WHERE menu_key = 'system:workspaces' AND label <> '工作空间管理';

UPDATE adh_perm_registry
   SET module_label = '工作空间'
 WHERE module = 'workspace' AND module_label = '云空间';

UPDATE adh_perm_registry
   SET module_label = '系统配置'
 WHERE perm_code IN ('workspace:read', 'workspace:manage') AND module = 'system';

UPDATE adh_perm_registry
   SET label = '查看工作空间',
       description = '查看用户的工作空间清单与用量（系统配置「工作空间管理」）'
 WHERE perm_code = 'workspace:read';

UPDATE adh_perm_registry
   SET label = '工作空间管理',
       description = '创建/删除/设默认工作空间与调整空间配额（系统配置「工作空间管理」）'
 WHERE perm_code = 'workspace:manage';

-- ── 7. 核验（应用后人工确认，勿删）──────────────────────────────────────────
-- SELECT menu_key,label,section,module,perm_code,sort FROM adh_menu_registry
--   WHERE module='workspace' OR menu_key='system:workspaces' ORDER BY module, sort;
-- SELECT perm_code,label,module,module_label,menu_key,ai_access FROM adh_perm_registry
--   WHERE perm_code IN ('asset:read','workspace:read','workspace:manage') OR module='workspace';
-- SELECT r.name, rp.perm_code FROM adh_roles r JOIN adh_role_perms rp ON rp.role_id=r.id
--   WHERE rp.perm_code='asset:read' ORDER BY r.name;
-- 模拟某角色的云空间段菜单反查（应含 workspace:assets）：
-- SELECT DISTINCT m.menu_key FROM adh_menu_registry m
--   JOIN adh_role_perms rp ON rp.perm_code = m.perm_code
--   JOIN adh_roles r ON r.id = rp.role_id
--   WHERE r.name='analyst' AND m.module='workspace' ORDER BY m.menu_key;
-- SELECT id,name,description FROM adh_workspaces WHERE is_default = 1 ORDER BY id;
--   期望：默认空间名是「xx的工作空间」，不得出现「工作站」或「的云空间」。
-- 只允许「云空间」出现在段名上，不得再出现在实体名位置：
-- SELECT menu_key,label FROM adh_menu_registry WHERE label LIKE '%云空间%';
-- SELECT perm_code,label,module_label FROM adh_perm_registry WHERE label LIKE '%云空间%'
--    OR module_label LIKE '%云空间%';
-- SELECT COUNT(*) FROM adh_workspaces WHERE name LIKE '%工作站%' OR name LIKE '%的云空间%';  -- 期望 0
