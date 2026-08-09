-- Semantic Layer Migration
-- Business knowledge semantic modeling and vectorization

-- Semantic models table
CREATE TABLE IF NOT EXISTS adh_semantic_models (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    workspace_id BIGINT,
    model_name VARCHAR(200) NOT NULL,
    description TEXT,
    is_active TINYINT DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    created_by VARCHAR(100),
    INDEX idx_workspace (workspace_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Semantic entities table (dimensions, measures, metrics)
CREATE TABLE IF NOT EXISTS adh_semantic_entities (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    model_id BIGINT NOT NULL,
    entity_name VARCHAR(200) NOT NULL,
    entity_type VARCHAR(50) NOT NULL,  -- dimension, measure, metric
    description TEXT,
    source_table VARCHAR(200),
    source_columns JSON,
    embedding_vector TEXT,  -- vector embedding for semantic search
    has_embedding TINYINT DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_model (model_id),
    INDEX idx_entity_type (entity_type),
    FOREIGN KEY (model_id) REFERENCES adh_semantic_models(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Semantic relations table
CREATE TABLE IF NOT EXISTS adh_semantic_relations (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    model_id BIGINT NOT NULL,
    source_entity_id BIGINT NOT NULL,
    target_entity_id BIGINT NOT NULL,
    relation_type VARCHAR(50) NOT NULL,  -- has_many, belongs_to, many_to_many, has_one
    description TEXT,
    join_condition TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_model (model_id),
    INDEX idx_source (source_entity_id),
    INDEX idx_target (target_entity_id),
    FOREIGN KEY (model_id) REFERENCES adh_semantic_models(id) ON DELETE CASCADE,
    FOREIGN KEY (source_entity_id) REFERENCES adh_semantic_entities(id) ON DELETE CASCADE,
    FOREIGN KEY (target_entity_id) REFERENCES adh_semantic_entities(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Semantic attributes table
CREATE TABLE IF NOT EXISTS adh_semantic_attributes (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    entity_id BIGINT NOT NULL,
    attribute_name VARCHAR(200) NOT NULL,
    attribute_type VARCHAR(50) NOT NULL,  -- dimension, measure, metric
    data_type VARCHAR(50) DEFAULT 'string',  -- string, number, date, boolean
    source_column VARCHAR(200),
    expression TEXT,  -- for calculated metrics (e.g., SUM(amount))
    description TEXT,
    embedding_vector TEXT,
    has_embedding TINYINT DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_entity (entity_id),
    INDEX idx_attribute_type (attribute_type),
    FOREIGN KEY (entity_id) REFERENCES adh_semantic_entities(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Workspace semantic model binding
CREATE TABLE IF NOT EXISTS adh_workspace_semantic_models (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    workspace_id BIGINT NOT NULL,
    model_id BIGINT NOT NULL,
    is_active TINYINT DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_workspace_model (workspace_id, model_id),
    FOREIGN KEY (model_id) REFERENCES adh_semantic_models(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
