"""
MCP Market Seeder
Insert curated MCP server entries into chatbi.adh_mcp_registry.

Usage:
    python -m sync.seed_mcp_market
"""

import os
import sys
import time as _time
from datetime import datetime
from pathlib import Path

import pymysql
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Load .env from backend/ (primary) and project root (fallback)
_project_root = Path(__file__).resolve().parent.parent
load_dotenv(_project_root / "backend" / ".env", override=True)
load_dotenv(_project_root / ".env", override=False)


# ── 精选 28 个高频 MCP 服务 ───────────────────────────────────────
#
# 选择标准：
#   1. Anthropic 官方 servers（最高质量，持续维护）
#   2. 社区高星项目（GitHub 100+ stars）
#   3. 覆盖常见使用场景（数据库/文件/搜索/开发/云/协作）
#
# 每个条目的 required_env 是用户安装时需要填写的环境变量

_MCP_SERVERS = [
    # ══════════════════════════════════════════════════════════════
    # 数据库 (5)
    # ══════════════════════════════════════════════════════════════
    {
        "name": "PostgreSQL",
        "package_name": "@modelcontextprotocol/server-postgres",
        "description": "只读 SQL 查询，支持 PostgreSQL 数据探索和分析",
        "author": "Anthropic",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/postgres",
        "install_type": "npm",
        "category": "database",
        "tags": "postgres,sql,查询,分析",
        "required_env": '[{"name":"DATABASE_URL","desc":"连接串 postgresql://user:pass@host:5432/db","required":true}]',
        "is_verified": 1, "is_popular": 1, "stars": 15000, "sort_order": 1,
    },
    {
        "name": "SQLite",
        "package_name": "@modelcontextprotocol/server-sqlite",
        "description": "本地 SQLite 数据库操作，支持 SQL 执行、数据分析和可视化",
        "author": "Anthropic",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/sqlite",
        "install_type": "npm",
        "category": "database",
        "tags": "sqlite,sql,本地数据库,分析",
        "is_verified": 1, "is_popular": 1, "stars": 15000, "sort_order": 2,
    },
    {
        "name": "MySQL",
        "package_name": "@benborla29/mcp-server-mysql",
        "description": "MySQL/MariaDB 只读 SQL 查询，支持数据库探索和分析",
        "author": "benborla29",
        "homepage": "https://github.com/benborla29/mcp-server-mysql",
        "install_type": "npm",
        "category": "database",
        "tags": "mysql,mariadb,sql,查询",
        "required_env": '[{"name":"MYSQL_HOST","desc":"主机地址","required":true},{"name":"MYSQL_PORT","desc":"端口(默认3306)","required":false},{"name":"MYSQL_USER","desc":"用户名","required":true},{"name":"MYSQL_PASS","desc":"密码","required":true},{"name":"MYSQL_DB","desc":"数据库名","required":true}]',
        "is_verified": 0, "is_popular": 1, "stars": 900, "sort_order": 3,
    },
    {
        "name": "Redis",
        "package_name": "@modelcontextprotocol/server-redis",
        "description": "Redis 键值存储操作，支持 GET/SET/SCAN/HASH 等命令",
        "author": "Anthropic",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/redis",
        "install_type": "npm",
        "category": "database",
        "tags": "redis,cache,缓存,键值",
        "required_env": '[{"name":"REDIS_URL","desc":"连接串 redis://host:6379","required":true}]',
        "is_verified": 1, "is_popular": 0, "stars": 15000, "sort_order": 4,
    },
    {
        "name": "MongoDB",
        "package_name": "mcp-server-mongodb",
        "description": "MongoDB 文档数据库操作，支持聚合查询和数据分析",
        "author": "kiliczill",
        "homepage": "https://github.com/kiliczill/mcp-server-mongodb",
        "install_type": "npm",
        "category": "database",
        "tags": "mongodb,nosql,文档数据库,聚合",
        "required_env": '[{"name":"MONGODB_URI","desc":"连接串 mongodb://host:27017/db","required":true}]',
        "is_verified": 0, "is_popular": 0, "stars": 200, "sort_order": 5,
    },

    # ══════════════════════════════════════════════════════════════
    # 文件系统 (2)
    # ══════════════════════════════════════════════════════════════
    {
        "name": "Filesystem",
        "package_name": "@modelcontextprotocol/server-filesystem",
        "description": "安全的本地文件系统访问，支持读写、搜索、目录管理",
        "author": "Anthropic",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem",
        "install_type": "npm",
        "category": "filesystem",
        "tags": "file,文件,目录,读写,搜索",
        "default_args": '["/tmp"]',
        "is_verified": 1, "is_popular": 1, "stars": 15000, "sort_order": 1,
    },
    {
        "name": "Google Drive",
        "package_name": "@modelcontextprotocol/server-gdrive",
        "description": "Google Drive 文件搜索、读取和管理",
        "author": "Anthropic",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/gdrive",
        "install_type": "npm",
        "category": "filesystem",
        "tags": "google,drive,云盘,文件",
        "required_env": '[{"name":"GDRIVE_CREDENTIALS_PATH","desc":"OAuth 凭证文件路径","required":true}]',
        "is_verified": 1, "is_popular": 0, "stars": 15000, "sort_order": 2,
    },

    # ══════════════════════════════════════════════════════════════
    # 开发工具 (7)
    # ══════════════════════════════════════════════════════════════
    {
        "name": "GitHub",
        "package_name": "@modelcontextprotocol/server-github",
        "description": "GitHub API：仓库管理、Issue/PR 操作、代码搜索、文件读写",
        "author": "Anthropic",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/github",
        "install_type": "npm",
        "category": "devtools",
        "tags": "github,git,代码,issue,pr,仓库",
        "required_env": '[{"name":"GITHUB_PERSONAL_ACCESS_TOKEN","desc":"GitHub Personal Access Token","required":true}]',
        "is_verified": 1, "is_popular": 1, "stars": 15000, "sort_order": 1,
    },
    {
        "name": "GitLab",
        "package_name": "@modelcontextprotocol/server-gitlab",
        "description": "GitLab API：项目管理、Issue/MR 操作、代码搜索",
        "author": "Anthropic",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/gitlab",
        "install_type": "npm",
        "category": "devtools",
        "tags": "gitlab,git,代码,issue,mr",
        "required_env": '[{"name":"GITLAB_PERSONAL_ACCESS_TOKEN","desc":"GitLab PAT","required":true},{"name":"GITLAB_API_URL","desc":"API地址(默认gitlab.com)","required":false}]',
        "is_verified": 1, "is_popular": 0, "stars": 15000, "sort_order": 2,
    },
    {
        "name": "Git",
        "package_name": "@modelcontextprotocol/server-git",
        "description": "本地 Git 仓库操作：diff、log、blame、show",
        "author": "Anthropic",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/git",
        "install_type": "npm",
        "category": "devtools",
        "tags": "git,diff,log,版本控制",
        "is_verified": 1, "is_popular": 0, "stars": 15000, "sort_order": 3,
    },
    {
        "name": "Puppeteer",
        "package_name": "@modelcontextprotocol/server-puppeteer",
        "description": "浏览器自动化：网页截图、点击、表单填写、JS 执行",
        "author": "Anthropic",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/puppeteer",
        "install_type": "npm",
        "category": "devtools",
        "tags": "puppeteer,browser,截图,自动化,爬虫",
        "is_verified": 1, "is_popular": 1, "stars": 15000, "sort_order": 4,
    },
    {
        "name": "Docker",
        "package_name": "@modelcontextprotocol/server-docker",
        "description": "Docker 容器管理：容器/镜像/网络/卷的 CRUD 操作",
        "author": "QuantGeek",
        "homepage": "https://github.com/QuantGeekDev/docker-mcp",
        "install_type": "npm",
        "category": "devtools",
        "tags": "docker,container,容器,部署,镜像",
        "is_verified": 0, "is_popular": 0, "stars": 350, "sort_order": 5,
    },
    {
        "name": "Sentry",
        "package_name": "@modelcontextprotocol/server-sentry",
        "description": "Sentry 错误监控：查看 Issue 详情、堆栈追踪、事件分析",
        "author": "Anthropic",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/sentry",
        "install_type": "npm",
        "category": "devtools",
        "tags": "sentry,监控,错误,bug,日志",
        "required_env": '[{"name":"SENTRY_AUTH_TOKEN","desc":"Sentry Auth Token","required":true},{"name":"SENTRY_ORG","desc":"组织 slug","required":true}]',
        "is_verified": 1, "is_popular": 0, "stars": 15000, "sort_order": 6,
    },
    {
        "name": "Linear",
        "package_name": "@modelcontextprotocol/server-linear",
        "description": "Linear 项目管理：Issue 查询、创建、状态更新",
        "author": "Anthropic",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/linear",
        "install_type": "npm",
        "category": "devtools",
        "tags": "linear,项目管理,issue,任务",
        "required_env": '[{"name":"LINEAR_API_KEY","desc":"Linear API Key","required":true}]',
        "is_verified": 1, "is_popular": 0, "stars": 15000, "sort_order": 7,
    },

    # ══════════════════════════════════════════════════════════════
    # 搜索引擎 (3)
    # ══════════════════════════════════════════════════════════════
    {
        "name": "Brave Search",
        "package_name": "@modelcontextprotocol/server-brave-search",
        "description": "Brave 搜索引擎：网页搜索和本地 POI 搜索",
        "author": "Anthropic",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/brave-search",
        "install_type": "npm",
        "category": "search",
        "tags": "brave,search,搜索,网页",
        "required_env": '[{"name":"BRAVE_API_KEY","desc":"Brave Search API Key (brave.com/api)","required":true}]',
        "is_verified": 1, "is_popular": 1, "stars": 15000, "sort_order": 1,
    },
    {
        "name": "Fetch",
        "package_name": "@modelcontextprotocol/server-fetch",
        "description": "HTTP 请求工具：抓取网页内容、调用 REST API、转 HTML 为 Markdown",
        "author": "Anthropic",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/fetch",
        "install_type": "npm",
        "category": "search",
        "tags": "fetch,http,网页,api,爬虫",
        "is_verified": 1, "is_popular": 0, "stars": 15000, "sort_order": 2,
    },
    {
        "name": "Google Maps",
        "package_name": "@modelcontextprotocol/server-google-maps",
        "description": "Google Maps：地点搜索、路线规划、地理编码、距离计算",
        "author": "Anthropic",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/google-maps",
        "install_type": "npm",
        "category": "search",
        "tags": "google,maps,地图,地点,路线",
        "required_env": '[{"name":"GOOGLE_MAPS_API_KEY","desc":"Google Maps API Key","required":true}]',
        "is_verified": 1, "is_popular": 0, "stars": 15000, "sort_order": 3,
    },

    # ══════════════════════════════════════════════════════════════
    # 云服务 (2)
    # ══════════════════════════════════════════════════════════════
    {
        "name": "AWS S3",
        "package_name": "@modelcontextprotocol/server-aws-s3",
        "description": "AWS S3 对象存储：文件上传下载、桶管理、对象列表",
        "author": "Anthropic",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/aws-s3",
        "install_type": "npm",
        "category": "cloud",
        "tags": "aws,s3,对象存储,云存储,文件",
        "required_env": '[{"name":"AWS_ACCESS_KEY_ID","desc":"AWS Access Key","required":true},{"name":"AWS_SECRET_ACCESS_KEY","desc":"AWS Secret Key","required":true},{"name":"AWS_REGION","desc":"区域(如 us-east-1)","required":false}]',
        "is_verified": 1, "is_popular": 0, "stars": 15000, "sort_order": 1,
    },
    {
        "name": "Cloudflare",
        "package_name": "@cloudflare/mcp-server-cloudflare",
        "description": "Cloudflare 管理：Workers/R2/KV/D1/浏览器渲染",
        "author": "Cloudflare",
        "homepage": "https://github.com/cloudflare/mcp-server-cloudflare",
        "install_type": "npm",
        "category": "cloud",
        "tags": "cloudflare,cdn,workers,边缘计算,dns",
        "required_env": '[{"name":"CLOUDFLARE_API_TOKEN","desc":"Cloudflare API Token","required":true}]',
        "is_verified": 1, "is_popular": 0, "stars": 600, "sort_order": 2,
    },

    # ══════════════════════════════════════════════════════════════
    # 通讯协作 (2)
    # ══════════════════════════════════════════════════════════════
    {
        "name": "Slack",
        "package_name": "@modelcontextprotocol/server-slack",
        "description": "Slack 工作区：消息发送、频道管理、消息搜索、用户查询",
        "author": "Anthropic",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/slack",
        "install_type": "npm",
        "category": "communication",
        "tags": "slack,消息,团队,通知,协作",
        "required_env": '[{"name":"SLACK_BOT_TOKEN","desc":"Slack Bot Token (xoxb-...)","required":true},{"name":"SLACK_TEAM_ID","desc":"Team ID","required":true}]',
        "is_verified": 1, "is_popular": 0, "stars": 15000, "sort_order": 1,
    },
    {
        "name": "Notion",
        "package_name": "@anthropic/mcp-server-notion",
        "description": "Notion 工作区：页面/数据库读写、搜索、内容管理",
        "author": "Anthropic",
        "homepage": "https://github.com/anthropics/anthropic-cookbook/tree/main/misc/mcp",
        "install_type": "npm",
        "category": "communication",
        "tags": "notion,笔记,文档,知识库",
        "required_env": '[{"name":"NOTION_API_KEY","desc":"Notion Integration Token","required":true}]',
        "is_verified": 0, "is_popular": 0, "stars": 100, "sort_order": 2,
    },

    # ══════════════════════════════════════════════════════════════
    # AI / ML (2)
    # ══════════════════════════════════════════════════════════════
    {
        "name": "HuggingFace",
        "package_name": "mcp-server-huggingface",
        "description": "HuggingFace Hub：模型搜索、数据集浏览、推理调用",
        "author": "evalstate",
        "homepage": "https://github.com/evalstate/mcp-server-huggingface",
        "install_type": "npm",
        "category": "ai",
        "tags": "huggingface,ml,模型,推理,数据集",
        "is_verified": 0, "is_popular": 0, "stars": 150, "sort_order": 1,
    },
    {
        "name": "OpenAI",
        "package_name": "mcp-server-openai",
        "description": "OpenAI API：图像生成(DALL-E)、语音转文字(Whisper)等",
        "author": "TriNerd",
        "homepage": "https://github.com/pierreclr/mcp-server-openai",
        "install_type": "npm",
        "category": "ai",
        "tags": "openai,dall-e,whisper,图像,语音",
        "required_env": '[{"name":"OPENAI_API_KEY","desc":"OpenAI API Key","required":true}]',
        "is_verified": 0, "is_popular": 0, "stars": 50, "sort_order": 2,
    },

    # ══════════════════════════════════════════════════════════════
    # 大数据 (2)
    # ══════════════════════════════════════════════════════════════
    {
        "name": "Apache Flink",
        "package_name": "flink-mcp",
        "description": "Apache Flink SQL Gateway MCP 服务，支持 Flink SQL 执行、作业管理、集群监控",
        "author": "CLEDAR",
        "homepage": "https://github.com/cledar/flink-mcp",
        "install_type": "pip",
        "category": "bigdata",
        "tags": "flink,sql,流计算,大数据,ETL",
        "required_env": '[{"name":"FLINK_SQL_GATEWAY_URL","desc":"Flink SQL Gateway 地址 (如 http://localhost:8083)","required":true}]',
        "is_verified": 0, "is_popular": 1, "stars": 50, "sort_order": 0,
    },
    {
        "name": "Apache Spark",
        "package_name": "@jaemin-jo/spark-mcp",
        "description": "Spark MCP Server，连接 Spark AI Scheduler，支持 SQL 查询、作业调度、集群管理",
        "author": "Spark AgentAI",
        "homepage": "https://github.com/jaemin-jo/spark-mcp",
        "install_type": "npm",
        "category": "bigdata",
        "tags": "spark,sql,大数据,调度,分析",
        "is_verified": 0, "is_popular": 1, "stars": 30, "sort_order": 1,
    },

    # ══════════════════════════════════════════════════════════════
    # 阿里云 (5)
    # ══════════════════════════════════════════════════════════════
    {
        "name": "阿里云",
        "package_name": "aliyun-mcp-server",
        "description": "阿里云 MCP 服务，支持 ECS、OSS、RDS 等云资源管理和操作",
        "author": "nailuoGG",
        "homepage": "https://github.com/nailuoGG/aliyun-mcp-server",
        "install_type": "npm",
        "category": "cloud",
        "tags": "aliyun,阿里云,ecs,oss,rds,云服务器",
        "required_env": '[{"name":"ALICLOUD_ACCESS_KEY","desc":"阿里云 AccessKey ID","required":true},{"name":"ALICLOUD_SECRET_KEY","desc":"阿里云 AccessKey Secret","required":true},{"name":"ALICLOUD_REGION","desc":"区域(如 cn-hangzhou)","required":true}]',
        "is_verified": 0, "is_popular": 1, "stars": 20, "sort_order": 3,
    },
    {
        "name": "阿里云无影",
        "package_name": "wuying-agentbay-mcp-server",
        "description": "阿里云无影 AgentBay MCP 服务，云端桌面和应用管理",
        "author": "Alibaba Cloud Wuying",
        "homepage": "https://github.com/aliyun/wuying-agentbay-mcp-server",
        "install_type": "npm",
        "category": "cloud",
        "tags": "aliyun,无影,桌面,云电脑,agent",
        "required_env": '[{"name":"WUYING_API_KEY","desc":"无影 API Key","required":true}]',
        "is_verified": 0, "is_popular": 0, "stars": 10, "sort_order": 4,
    },
    {
        "name": "阿里云可观测",
        "package_name": "mcp-server-aliyun-observability",
        "description": "阿里云官方可观测 MCP 服务，支持 SLS 日志自然语言转SQL/SPL、智能运维助手、云监控数据查询，覆盖日志服务和云监控两大产品",
        "author": "Alibaba Cloud",
        "homepage": "https://github.com/aliyun/alibabacloud-observability-mcp-server",
        "install_type": "pip",
        "category": "cloud",
        "tags": "aliyun,sls,云监控,可观测,日志,metrics,trace",
        "required_env": '[{"name":"ALIBABA_CLOUD_ACCESS_KEY_ID","desc":"阿里云 AccessKey ID","required":true},{"name":"ALIBABA_CLOUD_ACCESS_KEY_SECRET","desc":"阿里云 AccessKey Secret","required":true}]',
        "is_verified": 1, "is_popular": 1, "stars": 149, "sort_order": 6,
    },
    {
        "name": "阿里云 DMS",
        "package_name": "alibabacloud-dms-mcp-server",
        "description": "阿里云官方数据管理(DMS) MCP 服务，支持40+数据源(MySQL/PostgreSQL/Oracle/MongoDB/Redis/PolarDB/MaxCompute等)统一管理、NL2SQL、SQL执行、数据变更工单、SQL优化",
        "author": "Alibaba Cloud",
        "homepage": "https://github.com/aliyun/alibabacloud-dms-mcp-server",
        "install_type": "pip",
        "category": "cloud",
        "tags": "aliyun,dms,数据库管理,nl2sql,sql,数据治理,多数据源",
        "required_env": '[{"name":"ALIBABA_CLOUD_ACCESS_KEY_ID","desc":"阿里云 AccessKey ID","required":true},{"name":"ALIBABA_CLOUD_ACCESS_KEY_SECRET","desc":"阿里云 AccessKey Secret","required":true},{"name":"CONNECTION_STRING","desc":"单库模式: dbName@host:port (可选，不填则为多实例模式)","required":false}]',
        "is_verified": 1, "is_popular": 1, "stars": 47, "sort_order": 7,
    },

    # ══════════════════════════════════════════════════════════════
    # 其他 (2)
    # ══════════════════════════════════════════════════════════════
    {
        "name": "Time",
        "package_name": "@modelcontextprotocol/server-time",
        "description": "时间/时区工具：时间转换、时区查询、当前时间获取",
        "author": "Anthropic",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/time",
        "install_type": "npm",
        "category": "other",
        "tags": "time,timezone,时间,时区,转换",
        "is_verified": 1, "is_popular": 0, "stars": 15000, "sort_order": 1,
    },
    {
        "name": "Memory",
        "package_name": "@modelcontextprotocol/server-memory",
        "description": "基于知识图谱的持久化记忆，让 AI 记住上下文信息",
        "author": "Anthropic",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/memory",
        "install_type": "npm",
        "category": "other",
        "tags": "memory,记忆,知识图谱,持久化,上下文",
        "is_verified": 1, "is_popular": 0, "stars": 15000, "sort_order": 2,
    },
]


def _make_install_cmd(srv: dict) -> str:
    """自动生成 Docker 构建信息 JSON (install_cmd 字段)。"""
    import json
    install_type = srv.get("install_type", "npm")
    pkg = srv["package_name"]

    if install_type == "npm":
        info = {
            "base_image": "node:18-slim",
            "install_cmd": f"npm install -g {pkg}",
            "entrypoint": f"npx -y {pkg}",
        }
    elif install_type == "pip":
        # 模块名：包名中 '-' 替换为 '_'
        module = pkg.replace("-", "_")
        info = {
            "base_image": "python:3.10-slim",
            "install_cmd": f"pip install --no-cache-dir {pkg}",
            "entrypoint": f"python -m {module}",
            "add_main": True,  # 自动添加 __main__.py（如果缺失）
        }
    else:
        return ""

    return json.dumps(info, ensure_ascii=False)


def seed_mcp_market() -> None:
    """Insert curated MCP servers into adh_mcp_registry."""
    conn = pymysql.connect(
        host=os.environ.get("METADATA_DB_HOST", os.environ.get("DORIS_HOST", "127.0.0.1")),
        port=int(os.environ.get("METADATA_DB_PORT", os.environ.get("DORIS_PORT", "3306"))),
        user=os.environ.get("METADATA_DB_USER", os.environ.get("DORIS_USER", "root")),
        password=os.environ.get("METADATA_DB_PASSWORD", os.environ.get("DORIS_PASSWORD", "")),
        database=os.environ.get("METADATA_DB_DATABASE", "adh"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    inserted = 0
    skipped = 0
    updated = 0

    try:
        with conn.cursor() as cur:
            for i, srv in enumerate(_MCP_SERVERS):
                # 自动生成 Docker 构建信息（如果未显式指定）
                if "install_cmd" not in srv:
                    srv["install_cmd"] = _make_install_cmd(srv)

                # 按 package_name 去重：已存在则更新，不存在则插入
                cur.execute(
                    "SELECT id FROM adh_mcp_registry WHERE package_name = %s",
                    (srv["package_name"],),
                )
                existing = cur.fetchone()

                if existing:
                    # 更新已有条目
                    cur.execute(
                        "UPDATE adh_mcp_registry SET "
                        "name=%s, description=%s, author=%s, homepage=%s, "
                        "install_type=%s, install_cmd=%s, default_args=%s, required_env=%s, "
                        "category=%s, tags=%s, stars=%s, is_verified=%s, "
                        "is_popular=%s, sort_order=%s, updated_at=%s "
                        "WHERE package_name=%s",
                        (
                            srv["name"], srv.get("description", ""),
                            srv.get("author", ""), srv.get("homepage", ""),
                            srv.get("install_type", "npm"),
                            srv.get("install_cmd", ""),
                            srv.get("default_args", ""),
                            srv.get("required_env", ""),
                            srv.get("category", "other"),
                            srv.get("tags", ""),
                            srv.get("stars", 0),
                            srv.get("is_verified", 0),
                            srv.get("is_popular", 0),
                            srv.get("sort_order", i),
                            now, srv["package_name"],
                        ),
                    )
                    updated += 1
                else:
                    # 新增
                    row_id = int(_time.time() * 1000000) + i
                    cur.execute(
                        "INSERT INTO adh_mcp_registry "
                        "(id, name, package_name, description, author, homepage, "
                        "install_type, install_cmd, default_args, required_env, "
                        "category, tags, logo_url, stars, downloads, "
                        "is_verified, is_popular, sort_order, created_at, updated_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            row_id, srv["name"], srv["package_name"],
                            srv.get("description", ""), srv.get("author", ""),
                            srv.get("homepage", ""), srv.get("install_type", "npm"),
                            srv.get("install_cmd", ""), srv.get("default_args", ""),
                            srv.get("required_env", ""), srv.get("category", "other"),
                            srv.get("tags", ""), srv.get("logo_url", ""),
                            srv.get("stars", 0), srv.get("downloads", 0),
                            srv.get("is_verified", 0), srv.get("is_popular", 0),
                            srv.get("sort_order", i), now, now,
                        ),
                    )
                    inserted += 1

        conn.commit()
        print(f"[seed_mcp_market] Done — inserted {inserted}, updated {updated}, skipped {skipped}.")
    except Exception as exc:
        conn.rollback()
        print(f"[seed_mcp_market] ERROR: {exc}")
    finally:
        conn.close()


if __name__ == "__main__":
    seed_mcp_market()
