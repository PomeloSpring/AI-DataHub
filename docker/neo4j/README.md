# Neo4j Docker 安装指南

## 📋 前置要求

- Docker 20.10+
- Docker Compose 2.0+

## 🚀 快速启动

### 方式一：使用启动脚本（推荐）

```bash
cd docker/neo4j
./start.sh
```

### 方式二：手动启动

```bash
cd docker/neo4j
docker-compose up -d
```

## 📊 访问信息

| 项目 | 值 |
|------|-----|
| 浏览器地址 | http://localhost:7474 |
| Bolt协议 | bolt://localhost:7687 |
| 用户名 | neo4j |
| 密码 | ai-datahub-2024 |

## 🔧 配置说明

### 内存配置

默认配置适用于开发环境。生产环境建议调整：

```yaml
environment:
  - NEO4J_server_memory_heap_initial__size=1G
  - NEO4J_server_memory_heap_max__size=2G
  - NEO4J_server_memory_pagecache_size=1G
```

### 插件配置

默认安装以下插件：
- **APOC**: Neo4j 的核心工具库
- **Graph Data Science**: 图算法库

## 📝 常用命令

```bash
# 查看容器状态
docker ps | grep neo4j

# 查看日志
docker logs -f ai-datahub-neo4j

# 停止服务
docker-compose down

# 重启服务
docker-compose restart

# 进入容器
docker exec -it ai-datahub-neo4j bash

# 备份数据
docker exec ai-datahub-neo4j neo4j-admin database dump neo4j --to-path=/data/backup
```

## 🔗 连接配置

在项目 `.env` 文件中添加：

```bash
# Neo4j 配置
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=ai-datahub-2024
```

## 🛠️ 故障排除

### 问题1：端口被占用

```bash
# 检查端口占用
lsof -i :7474
lsof -i :7687

# 修改端口映射
# 编辑 docker-compose.yml，修改 ports 配置
```

### 问题2：内存不足

```bash
# 减少内存配置
# 编辑 docker-compose.yml，降低内存值
```

### 问题3：权限问题

```bash
# 修复权限
sudo chown -R 1001:1001 neo4j_data neo4j_logs neo4j_plugins
```

## 📚 相关资源

- [Neo4j 官方文档](https://neo4j.com/docs/)
- [Neo4j Docker 镜像](https://hub.docker.com/_/neo4j)
- [APOC 插件文档](https://neo4j.com/labs/apoc/)
- [Graph Data Science 文档](https://neo4j.com/docs/graph-data-science/)

---

**维护者**：AI-DataHub Team
**最后更新**：2026-07-18
