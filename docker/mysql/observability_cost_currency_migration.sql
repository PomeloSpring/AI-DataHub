-- Observability 成本列币种化迁移
-- 背景：Qoder 按 credit 计量、不回报账单成本（实测 ResultMessage.total_cost_usd 与
--       model_usage[].costUSD 恒为 0），旧列名 cost_usd 与实际存储的折算币种不符。
--       成本改为 cost_amount + cost_currency：CLI 上报时币种为 USD，
--       按 OBSERVABILITY_CREDIT_COST_RATE 折算时取配置币种（默认 CNY，
--       对应 Qoder CN 企业资源包官方价 80 元 / 2,000 Credits）。
--
-- 幂等：MySQL 的 CHANGE/ADD COLUMN 不支持 IF NOT EXISTS，重复执行会报
--       "Duplicate column name" / "Can't DROP" —— 属预期，按语句顺序执行一次即可。
-- 配套：backend/scripts/backfill_llm_trace_usage.py 会按新列补算历史成本。

USE adh2;

ALTER TABLE adh_llm_traces
    CHANGE COLUMN cost_usd cost_amount DECIMAL(18,6) NULL COMMENT '本回合成本(CLI 上报或按 credit 单价折算)',
    ADD COLUMN cost_currency VARCHAR(8) NULL COMMENT '成本币种: USD=CLI 上报, CNY=按资源包单价折算' AFTER cost_amount;

ALTER TABLE adh_llm_spans
    CHANGE COLUMN cost_usd cost_amount DECIMAL(18,6) NULL COMMENT '币种随所属 trace';
