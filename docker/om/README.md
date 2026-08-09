# OpenMetadata 元数据平台（docker/om）

AI-DataHub 的元数据中枢，基于 [OpenMetadata](https://open-metadata.org/) 1.6.3 官方编排适配。
负责：元数据自动采集（Doris/MySQL/ES）、SQL 血缘、数据 Profiling、质量测试（UI 内置）。

## 组件与端口

| 组件 | 容器 | 宿主机端口 | 说明 |
|---|---|---|---|
| OM Server | openmetadata_server | 8585（API/UI）、8586（admin） | 默认账号 `admin / admin` |
| Airflow（采集调度） | openmetadata_ingestion | 18080 | OM 内置 ingestion，`admin / admin` |
| OM 元数据库 | openmetadata_mysql | 3308（默认 3307，被占时经 `OM_MYSQL_PORT` 覆盖） | 与业务 MySQL 隔离，勿混用 |
| Elasticsearch | openmetadata_elasticsearch | 9201 | OM 检索后端，安全插件已关闭 |

端口均可通过 `services/.env` 中的 `OM_SERVER_PORT` / `OM_MYSQL_PORT` / `OM_ES_PORT` / `OM_AIRFLOW_PORT` 覆盖。

## 资源要求

- 内存建议 ≥ 6G（OM Server 2G + ES 1G + Airflow ~1.5G + MySQL）
- 磁盘 ≥ 10G（数据卷 `om-db-data` / `om-es-data`）

## 使用流程

```bash
# 1. 启动（首次拉镜像较慢；国内网络如拉取 docker.getcollate.io 慢，
#    可将 compose 中镜像改为 Docker Hub 同名镜像 openmetadata/server:1.6.3 等）
./start.sh

# 2. 初始化数据源 + 采集管道（幂等，可重复执行）
python init_om.py
#    - 自动获取 ingestion-bot JWT 并回写 services/.env 的 OM_AUTH_TOKEN
#    - 创建 adh_doris / adh_mysql 数据源与 metadata/lineage/profiler pipeline
#    - Doris connector 失败时自动降级为 MySQL connector（协议兼容）

# 3. 立即触发采集（默认 cron 为每日 02:00，由 OM_INGESTION_CRON 覆盖）
python init_om.py --trigger
python init_om.py --status

# 4. 采集完成后，回灌到本地 adh_* 表（供 datamind RAG 使用）
cd ../.. && python -m sync.om_sync

# 停止
./stop.sh            # 保留数据
./stop.sh --purge    # 清除全部数据卷
```

## 采集器如何连接数据源

- 远程数据库（当前 `.env` 中的公网地址）：容器直接外网可达，无需处理。
- 宿主机本地数据库：连接串 host 填 `host.docker.internal`
  （compose 已为 ingestion/server 注入 `host-gateway` 映射），
  可用 `OM_INGEST_DORIS_HOST` / `OM_INGEST_MYSQL_HOST` 覆盖。

## 与 AI-DataHub 的关系

```
OpenMetadata ──采集──> Doris/MySQL/ES
     │
     ├─ 前端「元数据中心」页 iframe 直连 8585（目录/血缘/质量 UI）
     ├─ datacatalog /api/catalog/om/* 代理（搜索/详情/血缘 API）
     └─ sync/om_sync.py 回灌 adh_table_info / adh_column_metadata
        （单向写，保留用户 business_desc；RAG 链路零改动）
```

## 常见问题

- **Doris usage 采集不支持**：OM Doris connector 不提供 usage pipeline，仅支持 metadata/lineage/profiler。
- **iframe 被浏览器拦截**：默认已关闭 OM 的 frame 防护；如需收紧，设置
  `OM_FRAME_OPTION_ENABLED=true` 并配 `OM_FRAME_ORIGIN=http://localhost:3000`（仅同 origin 可嵌）。
- **pipeline 触发失败**：确认 Airflow 容器健康（`docker logs openmetadata_ingestion`），
  首次启动 Airflow 初始化约需 2-3 分钟。
- 详细集成文档见 `docs/guides/openmetadata-integration.md`。
