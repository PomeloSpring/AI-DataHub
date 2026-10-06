-- Chat Attachments 退役迁移
-- 多模态附件改造为会话工作区文件(ADH_WORKSPACES_DIR/ws_{id}/sessions/{key}/workspace/uploads/):
--   * 附件随消息上传落盘,生命周期随会话目录清理;
--   * 附件管理复用文件管理(session-file 伺服 / WorkspaceAssets / Agent 文件工具);
--   * 不再有附件 ID 与附件元数据表,adh_chat_attachments 整表退役(幂等)。
-- 存量附件文件已清理 data/chat_attachments/users/**;历史消息中的旧附件引用由前端显示"附件已随会话清理"。

USE adh2;

DROP TABLE IF EXISTS adh_chat_attachments;
