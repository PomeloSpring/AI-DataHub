-- Brand Settings Migration
-- 品牌设置从本地 JSON 文件迁移到 MySQL,支持分布式多实例一致性
-- 单行表,所有服务通过 METADATA_DB 统一读写

USE adh;

CREATE TABLE IF NOT EXISTS adh_brand_settings (
    id              INT          NOT NULL DEFAULT 1 COMMENT '固定单行(id=1)',
    app_name        VARCHAR(128) NOT NULL DEFAULT 'AI-DataHub',
    logo_url        VARCHAR(1024) DEFAULT '',
    show_icon       TINYINT      NOT NULL DEFAULT 1,
    show_text       TINYINT      NOT NULL DEFAULT 1,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 幂等:首次插入默认行(已存在则跳过)
INSERT IGNORE INTO adh_brand_settings (id, app_name, logo_url, show_icon, show_text)
VALUES (1, 'AI-DataHub', '', 1, 1);
