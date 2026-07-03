-- Register analysis dimension agents as built-in agents
INSERT INTO adh_agents (name, display_name, description, agent_type, system_prompt, config, is_active, is_default)
VALUES
    ('traffic', '流量分析', '分析流量数据：UV/PV统计、页面访问排行、时段分布、跳出率分析。', 'builtin', '', '{"max_retries": 0, "max_iterations": 15}', 1, 0),
    ('user_profiling', '用户画像', '分析用户画像：地域分布、设备类型、新老用户比例、用户活跃度分层。', 'builtin', '', '{"max_retries": 0, "max_iterations": 15}', 1, 0),
    ('funnel', '转化漏斗', '分析关键路径转化漏斗：定义漏斗步骤、计算各步骤转化率和流失率。', 'builtin', '', '{"max_retries": 0, "max_iterations": 15}', 1, 0),
    ('retention', '留存分析', '分析用户留存：次日/7日/30日留存率、用户生命周期、流失预警。', 'builtin', '', '{"max_retries": 0, "max_iterations": 15}', 1, 0),
    ('anomaly', '异常检测', '检测数据异常：流量突变、统计异常点、同比环比异常。', 'builtin', '', '{"max_retries": 0, "max_iterations": 15}', 1, 0),
    ('trend', '趋势分析', '分析数据趋势：时间序列趋势、增长率、拐点识别、周期性规律。', 'builtin', '', '{"max_retries": 0, "max_iterations": 15}', 1, 0)
ON DUPLICATE KEY UPDATE
    display_name = VALUES(display_name),
    description = VALUES(description),
    config = VALUES(config),
    updated_at = NOW();
