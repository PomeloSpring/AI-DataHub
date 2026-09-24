-- 语义修正：清空历史消息不应关闭执行会话。
-- 旧版 chat.py 清空分支会把 adh_agent_sessions 置为 'closed'，导致同一会话续聊被 409 阻断、
-- 被迫新建会话。现代码已改为"清空即重置"（idle + 清 sdk_session_id，下一轮零上下文继续），
-- 本迁移负责重新开放存量被误判关闭的行。
UPDATE adh_agent_sessions
SET status = 'idle', sdk_session_id = NULL, execution_token = NULL, updated_at = UTC_TIMESTAMP(6)
WHERE status = 'closed';
