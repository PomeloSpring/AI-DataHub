-- admin 角色全量权限码授权（根因修复：权限码体系上线时 admin 角色漏配授权）
--
-- 现象: Agent 工具判定 perm_link.resolve_function_actions 对 admin 全部返回
--       "角色未授予「xxx」权限", 导致工具不可用、Agent 无法取数。
-- 根因: adh_role_perms 中 role_id=1(admin) 仅有 1 个码(quality-review:manage),
--       而 get_user_role_ai_perms 为纯角色裁决(不含 admin bypass, 见
--       waker-datasource-domain 规则), admin 的全量能力必须由角色授权数据表达。
-- 修复: 幂等补全 admin 角色对全部生效权限码的授权(只增不改: 不动 ai_access 配置,
--       不加代码 bypass)。授权后各码是否开放给 AI 仍由 adh_perm_registry.ai_access
--       与涉密硬上界(perm_link.SECRET_BOUND_*)裁决。

INSERT INTO adh_role_perms (role_id, perm_code)
SELECT 1, p.perm_code
FROM adh_perm_registry p
WHERE p.is_active = 1
  AND NOT EXISTS (
    SELECT 1 FROM (SELECT * FROM adh_role_perms) rp
    WHERE rp.role_id = 1 AND rp.perm_code = p.perm_code
  );

-- 附带根治: 旧行 id=2147483647(早期显式插入占位)占满 int auto_increment 序列,
-- 导致任何新授权 INSERT 撞主键(Duplicate entry '2147483647')。id 为代理键,
-- 改 BIGINT 无语义影响, 保留旧行。
ALTER TABLE adh_role_perms MODIFY id BIGINT NOT NULL AUTO_INCREMENT;
