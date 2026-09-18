-- Qoder 会话持久化 — adh_conversations 增加执行层会话 ID
-- 用途: chat 页面重新打开对话时回传给 QoderSDKAdapter 以 resume qoder 会话

USE adh;

ALTER TABLE adh_conversations
    ADD COLUMN executor_session_id VARCHAR(128) DEFAULT NULL COMMENT 'Qoder/执行层 SDK 会话 ID(多轮对话 resume)';
