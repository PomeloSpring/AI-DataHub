-- ============================================================================
-- 换 Harness 后续聊：执行映射按后端各记一份 CLI 转录指针
--
-- 背景：adh_agent_sessions.sdk_session_id 只有一个槽位，而它属于**某一个** CLI 后端
-- （qoder 转录落 runtime/qoder-cn/logs/sessions/…，claude 落 runtime/projects/…/*.jsonl，
-- 格式与 id 规则都不通用）。默认执行层一旦切换，旧会话的指针就指向新后端读不到的转录，
-- claim_session 只能回 403「执行会话身份或后端不匹配，请新建会话」——换 Harness 等于把
-- 切换前建的所有会话一次性作废（实测存量 32 个 qoder 会话全部中招）。
--
-- 本迁移把「一个槽位」改成「按后端记账」：backend_sessions = {backend: sdk_session_id}。
-- 切换后端时续聊不再被阻断（问答文本与工作区产物保留，新后端没有指针 → 首轮不带 resume
-- 并按问答记录重建上下文），切回原后端仍能接回它自己的转录。指针归属清晰，不再出现
-- 跨后端混写把原转录指针抹掉、切回去也接不回的不可逆损失。
--
-- 幂等：ADD COLUMN 走 information_schema 判定 + PREPARE；回填只处理 backend_sessions
-- IS NULL 的行，第二次执行 0 行受影响。
-- ============================================================================

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_agent_sessions'
      AND COLUMN_NAME = 'backend_sessions') = 0,
  'ALTER TABLE adh_agent_sessions ADD COLUMN backend_sessions JSON NULL COMMENT ''各执行后端的 CLI 转录指针 {backend: sdk_session_id}（换 Harness 续聊与切回接回用）''',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 回填：把当前后端的既有指针搬进 map（不搬就等于历史转录指针全部丢失）
UPDATE adh_agent_sessions
   SET backend_sessions = JSON_OBJECT(backend, sdk_session_id)
 WHERE backend_sessions IS NULL
   AND backend IS NOT NULL AND backend <> ''
   AND sdk_session_id IS NOT NULL AND sdk_session_id <> '';
