-- Register report agent as built-in agent
INSERT INTO adh_agents (name, display_name, description, agent_type, system_prompt, config, is_active, is_default)
VALUES (
    'report',
    '报告生成',
    '根据任务执行结果和报告样式模板，生成完整的数据分析报告。不是简单替换占位符，而是基于数据给出分析洞察、异常发现和趋势判断。',
    'builtin',
    '',  -- system_prompt loaded from file (config/agents/report/system.md)
    '{"max_retries": 0, "max_iterations": 3}',
    1,
    0
) ON DUPLICATE KEY UPDATE
    display_name = VALUES(display_name),
    description = VALUES(description),
    config = VALUES(config),
    updated_at = NOW();
