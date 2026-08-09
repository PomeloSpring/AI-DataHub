-- Graph Entities Migration
-- 为知识图谱新增指标和维度表

-- 指标表
CREATE TABLE IF NOT EXISTS adh_metrics (
  id INT PRIMARY KEY AUTO_INCREMENT,
  name VARCHAR(200) NOT NULL COMMENT '指标名称',
  name_en VARCHAR(200) COMMENT '英文名称',
  formula TEXT COMMENT '计算公式',
  unit VARCHAR(50) COMMENT '单位',
  agg_type VARCHAR(50) COMMENT '聚合类型(SUM/AVG/COUNT/MAX/MIN)',
  target_table VARCHAR(200) COMMENT '目标表',
  target_column VARCHAR(200) COMMENT '目标字段',
  description TEXT COMMENT '描述',
  owner VARCHAR(100) COMMENT '负责人',
  category VARCHAR(100) COMMENT '指标分类',
  datasource_id INT DEFAULT 0 COMMENT '数据源ID(0表示全局)',
  is_active TINYINT DEFAULT 1 COMMENT '是否启用',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_name (name),
  INDEX idx_category (category),
  INDEX idx_datasource (datasource_id),
  INDEX idx_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='业务指标表';

-- 维度表
CREATE TABLE IF NOT EXISTS adh_dimensions (
  id INT PRIMARY KEY AUTO_INCREMENT,
  name VARCHAR(200) NOT NULL COMMENT '维度名称',
  name_en VARCHAR(200) COMMENT '英文名称',
  hierarchy VARCHAR(500) COMMENT '层级关系(JSON格式，如["国家","省","市"])',
  level INT DEFAULT 0 COMMENT '当前层级(0为基础维度)',
  target_table VARCHAR(200) COMMENT '目标表',
  target_column VARCHAR(200) COMMENT '目标字段',
  description TEXT COMMENT '描述',
  category VARCHAR(100) COMMENT '维度分类',
  datasource_id INT DEFAULT 0 COMMENT '数据源ID(0表示全局)',
  is_active TINYINT DEFAULT 1 COMMENT '是否启用',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_name (name),
  INDEX idx_category (category),
  INDEX idx_datasource (datasource_id),
  INDEX idx_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='分析维度表';

-- 指标-维度关联表
CREATE TABLE IF NOT EXISTS adh_metric_dimensions (
  id INT PRIMARY KEY AUTO_INCREMENT,
  metric_id INT NOT NULL COMMENT '指标ID',
  dimension_id INT NOT NULL COMMENT '维度ID',
  relation_type VARCHAR(50) DEFAULT 'GROUP_BY' COMMENT '关联类型(GROUP_BY/FILTER/BREAKDOWN)',
  description VARCHAR(500) COMMENT '描述',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_metric_dimension (metric_id, dimension_id),
  INDEX idx_metric (metric_id),
  INDEX idx_dimension (dimension_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='指标-维度关联表';

-- 插入示例数据
INSERT INTO adh_metrics (name, name_en, formula, unit, agg_type, target_table, target_column, description, category, datasource_id) VALUES
('GMV', 'Gross Merchandise Volume', 'SUM(order_amount)', '元', 'SUM', 'orders', 'amount', '商品交易总额', '交易', 0),
('DAU', 'Daily Active Users', 'COUNT(DISTINCT user_id)', '人', 'COUNT', 'user_events', 'user_id', '日活跃用户数', '用户', 0),
('订单量', 'Order Count', 'COUNT(order_id)', '单', 'COUNT', 'orders', 'order_id', '订单总数', '交易', 0),
('客单价', 'Average Order Value', 'SUM(order_amount) / COUNT(order_id)', '元', 'AVG', 'orders', 'amount', '平均每单金额', '交易', 0);

INSERT INTO adh_dimensions (name, name_en, hierarchy, level, target_table, target_column, description, category, datasource_id) VALUES
('时间', 'Time', '["年","季","月","日"]', 0, NULL, NULL, '时间维度', '基础', 0),
('地区', 'Region', '["国家","省","市","区"]', 0, 'users', 'region', '地理区域维度', '地理', 0),
('渠道', 'Channel', '["一级渠道","二级渠道"]', 0, 'orders', 'channel', '销售渠道维度', '渠道', 0),
('商品类目', 'Category', '["一级类目","二级类目","三级类目"]', 0, 'products', 'category', '商品分类维度', '商品', 0);
