-- 别名回流建议队列表 (系统自进化闭环第一条实回路)
-- 语义层解析被拒的近似词(fuzzy_rejected)/未解析词自动落入, 人工审核通过后
-- 经 AS-BOT alias.approve 动作写回对应对象/字典别名并触发图谱+知识库重建。
-- 复用既有 AS-BOT 审批/权限体系(adh_as_bot_role_actions 控制谁能 approve)。

CREATE TABLE IF NOT EXISTS adh_alias_suggestions (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  term          VARCHAR(128) NOT NULL COMMENT '未命中的业务词/近似词',
  target_type   ENUM('object','metric','dimension') NOT NULL DEFAULT 'dimension'
                COMMENT '建议归属类型',
  target_ref    VARCHAR(128) NOT NULL DEFAULT ''
                COMMENT '建议挂载的对象/字典名(模糊命中候选或空)',
  datasource_id BIGINT NOT NULL DEFAULT 0,
  source        ENUM('fuzzy_rejected','unresolved') NOT NULL DEFAULT 'unresolved',
  candidates    JSON COMMENT '回抛的候选业务名清单(仅供审核参考, 不含物理列)',
  hit_count     INT NOT NULL DEFAULT 1 COMMENT '同词重复命中计数(去重累加)',
  status        ENUM('pending','approved','rejected') NOT NULL DEFAULT 'pending',
  created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  decided_by    INT DEFAULT NULL,
  UNIQUE KEY uk_term_ds_type_ref (term, datasource_id, target_type, target_ref)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
