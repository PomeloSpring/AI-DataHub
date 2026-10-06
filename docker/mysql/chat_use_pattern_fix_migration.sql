-- chat:use 权限码接口绑定修正迁移 (幂等, 可重复执行)
--
-- 背景: 数据查看者/数据分析者等角色在智能问数页报
--   "会话列表加载失败" / "Waker 权限清单加载失败"。
-- 根因(权限码 api_pattern/api_method 与实际路由错配):
--   * chat:use 的 api_method 仅 POST, 而 会话列表/详情、按角色解析可用 Waker
--     均为 GET /api/chat/* → 被 API 权限中间件 403;
--   * /api/chat/wakers(当前用户按角色可用的 Waker 清单, 使用域) 挂在
--     waker:read 下, 而该码只授予管理域角色 → 使用智能问数的角色 403。
--
-- 修正语义:
--   * chat:use 覆盖智能问数使用域的读写(GET/POST/...): /api/chat/* 全方法;
--   * waker:read / waker:manage 收窄为管理域清单 /api/admin/wakers*;
--     用户按角色解析自己可用 Waker 的 /api/chat/wakers 归 chat:use(/api/chat/* 已含)。
-- ============================================================================

UPDATE adh_perm_registry
   SET api_pattern = '/api/pipeline*,/api/chat/*,/api/agent*,/api/execution*',
       api_method  = '*'
 WHERE perm_code = 'chat:use';

UPDATE adh_perm_registry
   SET api_pattern = '/api/admin/wakers*'
 WHERE perm_code IN ('waker:read', 'waker:manage');
