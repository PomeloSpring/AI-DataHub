## 架构

```
OpenMetadata (采集/血缘/质量/profile) ──REST API──> sync/om_sync.py ──> adh_* 表
       │                                                                 │
       ├── 前端「元数据中心」iframe 直连 8585                             ├── datamind RAG（零改动）
       └── datacatalog /api/catalog/om/* 代理                            └── datacatalog 现有页面
```

设计原则：

1. **OpenMetadata 是元数据事实源**，采集 Doris/MySQL/ES 的 schema、注释、血缘、profile。
2. **本地 adh_* 表是 AI 消费缓存**：回灌脚本单向写入，RAG 查询链路不直连 OM（避免时延）。
3. **用户编辑优先**：回灌只更新系统字段，`table_business_desc` / `business_desc` 永不覆盖。
4. **优雅降级**：`OM_ENABLED=false` 时所有新链路跳过，存量功能不受影响。

## 部署步骤

```bash
# 1. 启动 OM（MySQL/ES/Airflow/Server 一键编排，详见 docker/om/README.md）
cd docker/om && ./start.sh

# 2. 初始化数据源与采集管道（幂等）
python init_om.py
#    - admin 登录 → 获取 ingestion-bot JWT → 回写 services/.env OM_AUTH_TOKEN
#    - 创建 adh_doris（Doris connector，失败自动降级 MySQL connector）与 adh_mysql 服务
#    - 创建 metadata/lineage/profiler pipeline，默认 cron 每日 02:00

# 3. 手动触发首次采集
python init_om.py --trigger
python init_om.py --status

# 4. 启用集成：services/.env 中 OM_ENABLED=true

# 5. 回灌到本地元数据表并重建 RAG 向量
cd ../.. && python -m sync.om_sync

# 6. 重启 datacatalog 使代理生效
./start-all.sh datacatalog
```

## 日常运维

| 操作 | 命令 |
|---|---|
| 手动触发采集 | `cd docker/om && python init_om.py --trigger [adh_doris]` |
| 回灌（不重建向量） | `python -m sync.om_sync --no-rebuild` |
| 查看 pipeline 状态 | `cd docker/om && python init_om.py --status` |
| OM UI | http://localhost:8585（admin/admin） |
| Airflow UI | http://localhost:18080（admin/admin） |
| 停止/清除 | `./stop.sh` / `./stop.sh --purge` |

新增数据源：在 OM UI（Settings → Services → Databases）手工创建，
并在 `services/.env` 的 `OM_DATASOURCE_MAP` 中追加 `服务名:datasource_id`，
下次回灌即自动纳入。

## 前端入口

- **数据中台 → 数据目录 → 元数据中心**：iframe 嵌入 OM UI（目录/血缘/质量/Insights）。
  iframe 直连同主机 OM 端口（OM SPA 使用绝对路径资源，path 反代会破坏资产加载），
  可用 `frontend/.env` 的 `VITE_OM_URL` / `VITE_OM_PORT` 覆盖。
- **REST 代理**（服务端持有 token，前端无密钥）：
  - `GET /api/catalog/om/status` — 集成状态
  - `GET /api/catalog/om/search?q=订单` — 全文搜索
  - `GET /api/catalog/om/table/{fqn}` — 表详情
  - `GET /api/catalog/om/lineage/{fqn}` — 表级血缘

## 血缘说明

血缘有两层，互补使用：

1. **OM 采集血缘**（跨任务/跨系统）：lineage pipeline 解析查询日志生成，UI 直接可见。
   注意 Doris connector 不支持 usage 采集，Doris 侧血缘主要依赖手工登记的 SQL。
2. **datagov SQL 解析血缘**（登记时点）：`POST /api/lineage/parse-sql` 已从正则升级为
   sqlglot（`services/datagov/services/sql_lineage.py`），支持 CTE、子查询、JOIN、
   `CREATE TABLE AS`、`CREATE VIEW`、带库前缀表名，产出表级 + 字段级（尽力而为）血缘，
   写入 `adh_lineage_nodes/edges`，前端血缘页无需改动。

## 排障

| 现象 | 排查 |
|---|---|
| `init_om.py` 登录失败 | OM Server 未就绪（`curl localhost:8585/api/v1/system/version`）；确认 `docker logs openmetadata_server` |
| Doris 采集无数据 | 检查 ingestion 容器到 Doris 网络（公网 IP 直连或 `OM_INGEST_DORIS_HOST=host.docker.internal`）；Doris connector 失败时查看是否已降级为 Mysql connector |
| `om_sync` 报缺少 OM_AUTH_TOKEN | 先执行 `python docker/om/init_om.py` 生成并回写 |
| 回灌后 RAG 搜不到新表 | 确认 `rebuild_vectors` 执行成功；检查 `OM_DATASOURCE_MAP` 的 datasource_id 与查询侧一致 |
| iframe 空白 | 直接访问 http://localhost:8585 确认 OM 正常；检查浏览器是否拦截跨端口 iframe |
| 旧同步脚本回滚 | `sync/metadata_sync.py` 保留可用（deprecated），直连数据源采集 |

## 后续演进（二期）

- SSO 打通：OM 支持 OIDC，可对接 authservice 统一登录，取消 iframe 内二次登录。
- 质量规则：将 datagov 自研规则引擎逐步迁移到 OM Test Suite，经 API 同步结果。
- 现有 catalog 页面（表&字段、血缘页）用 `/api/catalog/om/*` 渐进替换数据源。
