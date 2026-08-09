#!/usr/bin/env python3
"""
OpenMetadata 幂等初始化脚本。

功能:
  1. 使用 admin 基本认证登录，获取 ingestion-bot 的 JWT Token，回写到 services/.env (OM_AUTH_TOKEN)
  2. 创建数据源服务: adh_doris (Doris connector)、adh_mysql (MySQL connector)、adh_es (可选)
  3. 为每个服务创建 ingestion pipeline: metadata / lineage / profiler
  4. 支持 --trigger [service] 手动触发采集

用法:
    cd docker/om
    python init_om.py                # 初始化（幂等，重复执行不会重复创建）
    python init_om.py --force        # 已存在的实体强制更新配置
    python init_om.py --trigger adh_doris   # 触发指定服务的全部 pipeline
    python init_om.py --status       # 查看服务/pipeline 状态
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
ENV_FILE = PROJECT_ROOT / "services" / ".env"

# ── 系统库排除模式（metadata pipeline sourceConfig）──────────────────
SYSTEM_SCHEMA_EXCLUDES = [
    "^information_schema$", "^mysql$", "^performance_schema$", "^sys$",
    "^__internal_schema$", "^audit__db$", "^openmetadata_db$", "^airflow_db$",
]


def load_env() -> dict:
    """解析 services/.env 为 dict（不覆盖已存在的环境变量）。"""
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    merged = {**env, **{k: v for k, v in os.environ.items() if v != ""}}
    return merged


ENV = load_env()

OM_URL = ENV.get("OM_SERVER_URL", "http://localhost:8585").rstrip("/")
ADMIN_EMAIL = ENV.get("OM_ADMIN_EMAIL", "admin@open-metadata.org")
ADMIN_PASSWORD = ENV.get("OM_ADMIN_PASSWORD", "admin")
SCHEDULE_CRON = ENV.get("OM_INGESTION_CRON", "0 2 * * *")


def http(method: str, path: str, token: str = None, body: dict = None, timeout: int = 30):
    """发起 OM REST 请求，返回 (status_code, parsed_json_or_text)。"""
    url = f"{OM_URL}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw


def login_admin() -> str:
    """basic 认证登录，返回 admin access token。"""
    status, data = http("PUT", "/api/v1/users/login", body={
        "email": ADMIN_EMAIL, "password": ADMIN_PASSWORD,
    })
    if status != 200:
        raise RuntimeError(f"admin 登录失败 (HTTP {status}): {data}")
    return data["accessToken"]


def get_bot_token(admin_token: str) -> str:
    """获取 ingestion-bot 的 JWT（供采集器/回灌脚本使用）。"""
    status, bot = http("GET", "/api/v1/users/name/ingestion-bot", admin_token)
    if status != 200:
        raise RuntimeError(f"未找到 ingestion-bot (HTTP {status}): {bot}")
    status, mech = http("GET", f"/api/v1/users/{bot['id']}/auth-mechanism", admin_token)
    if status != 200:
        raise RuntimeError(f"获取 bot token 失败 (HTTP {status}): {mech}")
    return mech["config"]["JWT token"]


def write_token_to_env(token: str):
    """把 OM_AUTH_TOKEN 回写到 services/.env（替换或追加）。"""
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    replaced = False
    for i, line in enumerate(lines):
        if line.strip().startswith("OM_AUTH_TOKEN=") or line.strip().startswith("# OM_AUTH_TOKEN="):
            lines[i] = f"OM_AUTH_TOKEN={token}"
            replaced = True
            break
    if not replaced:
        lines.append(f"OM_AUTH_TOKEN={token}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  [OK] OM_AUTH_TOKEN 已写入 {ENV_FILE}")


# ── 实体创建（幂等）─────────────────────────────────────────────────

def ensure_entity(kind: str, name: str, body: dict, token: str, force: bool = False) -> str:
    """GET-by-name 检查存在性；不存在则 POST，force 时 PUT 更新。返回动作。"""
    status, existing = http("GET", f"/api/v1/services/{kind}/name/{name}", token)
    if status == 200:
        if not force:
            return "exists"
        put_status, resp = http("PUT", f"/api/v1/services/{kind}/{existing['id']}", token, body)
        if put_status not in (200, 201):
            raise RuntimeError(f"更新 {kind}/{name} 失败 (HTTP {put_status}): {resp}")
        return "updated"
    if status != 404:
        raise RuntimeError(f"查询 {kind}/{name} 失败 (HTTP {status}): {existing}")
    post_status, resp = http("POST", f"/api/v1/services/{kind}", token, body)
    if post_status not in (200, 201):
        raise RuntimeError(f"创建 {kind}/{name} 失败 (HTTP {post_status}): {resp}")
    return "created"


def _prune_unknown_fields(kind: str, body: dict, token: str, max_retry: int = 5):
    """POST 时根据 400 报错逐个剔除不支持的字段（兼容不同 OM 版本 connector 差异）。"""
    for _ in range(max_retry):
        status, resp = http("POST", f"/api/v1/services/{kind}", token, body)
        if status in (200, 201):
            return status, resp
        msg = json.dumps(resp, ensure_ascii=False) if isinstance(resp, dict) else str(resp)
        # 常见报错: "...: unrecognized field xxx" / "Unknown property \"xxx\""
        field = None
        for marker in ("unrecognized field ", 'Unknown property "', "unknown property "):
            idx = msg.find(marker)
            if idx >= 0:
                rest = msg[idx + len(marker):]
                field = rest.split('"')[0].split("'")[0].split("(")[0].split()[0].strip()
                break
        if field and field in body.get("connection", {}):
            print(f"  [WARN] 剔除 connector 不支持的字段: {field}")
            del body["connection"][field]
            continue
        return status, resp
    return status, resp


def create_db_service(name: str, display: str, connection: dict, token: str, force: bool) -> bool:
    body = {
        "name": name,
        "displayName": display,
        "serviceType": connection["type"],
        "connection": connection,
    }
    status, existing = http("GET", f"/api/v1/services/databaseServices/name/{name}", token)
    if status == 200:
        if not force:
            print(f"  [SKIP] 数据源 {name} 已存在")
            return True
        put_status, resp = http(
            "PUT", f"/api/v1/services/databaseServices/{existing['id']}", token, body)
        if put_status not in (200, 201):
            print(f"  [FAIL] 更新 {name}: HTTP {put_status} {resp}")
            return False
        print(f"  [OK] 数据源 {name} 已更新")
        return True
    status, resp = _prune_unknown_fields("databaseServices", body, token)
    if status not in (200, 201):
        print(f"  [FAIL] 创建 {name}: HTTP {status} {resp}")
        return False
    print(f"  [OK] 数据源 {name} 已创建 ({connection['type']} connector)")
    return True


def create_pipeline(name: str, service_name: str, pipeline_type: str,
                    source_config: dict, token: str, force: bool) -> bool:
    body = {
        "name": name,
        "displayName": f"{service_name} {pipeline_type}",
        "pipelineType": pipeline_type,
        "service": {"type": "databaseService", "name": service_name},
        "sourceConfig": {"config": source_config},
        "airflowConfig": {"scheduleInterval": SCHEDULE_CRON},
        "enabled": True,
        "raiseOnError": False,
    }
    action = "created"
    status, existing = http("GET", f"/api/v1/services/ingestionPipelines/name/{name}", token)
    if status == 200:
        if not force:
            print(f"  [SKIP] pipeline {name} 已存在")
            return True
        put_status, resp = http(
            "PUT", f"/api/v1/services/ingestionPipelines/{existing['id']}", token, body)
        if put_status not in (200, 201):
            print(f"  [FAIL] 更新 {name}: HTTP {put_status} {resp}")
            return False
        print(f"  [OK] pipeline {name} 已更新")
        return True

    post_status, resp = http("POST", "/api/v1/services/ingestionPipelines", token, body)
    if post_status not in (200, 201):
        # 兼容: 旧版本不支持 airflowConfig.scheduleInterval 时去掉重试
        body["airflowConfig"] = {}
        post_status, resp = http("POST", "/api/v1/services/ingestionPipelines", token, body)
        action = "created(no-schedule)"
    if post_status not in (200, 201):
        print(f"  [FAIL] 创建 {name}: HTTP {post_status} {resp}")
        return False
    print(f"  [OK] pipeline {name} 已{action}")
    return True


# ── 数据源配置构造 ──────────────────────────────────────────────────

def build_services() -> list:
    """根据 services/.env 构造要创建的数据源列表。"""
    services = []

    doris_host = ENV.get("OM_INGEST_DORIS_HOST") or ENV.get("VECTOR_DB_HOST", "host.docker.internal")
    doris_port = ENV.get("OM_INGEST_DORIS_PORT") or ENV.get("VECTOR_DB_PORT", "9030")
    doris_user = ENV.get("OM_INGEST_DORIS_USER") or ENV.get("VECTOR_DB_USER", "root")
    doris_pwd = ENV.get("OM_INGEST_DORIS_PASSWORD") or ENV.get("VECTOR_DB_PASSWORD", "")
    services.append({
        "name": "adh_doris",
        "display": "AI-DataHub Doris (分析库)",
        "connection": {
            "type": "Doris",
            "username": doris_user,
            "authType": {"password": doris_pwd},
            "hostPort": f"{doris_host}:{doris_port}",
            "supportsMetadataExtraction": True,
            "supportsProfiler": True,
        },
        # Doris connector 不可用时降级为 MySQL connector（Doris 兼容 MySQL 协议）
        "fallback_connection": {
            "type": "Mysql",
            "scheme": "mysql+pymysql",
            "username": doris_user,
            "authType": {"password": doris_pwd},
            "hostPort": f"{doris_host}:{doris_port}",
            "supportsMetadataExtraction": True,
            "supportsProfiler": True,
        },
        "pipelines": ["metadata", "lineage", "profiler"],
    })

    mysql_host = ENV.get("OM_INGEST_MYSQL_HOST") or ENV.get("METADATA_DB_HOST", "host.docker.internal")
    mysql_port = ENV.get("OM_INGEST_MYSQL_PORT") or ENV.get("METADATA_DB_PORT", "3306")
    mysql_user = ENV.get("OM_INGEST_MYSQL_USER") or ENV.get("METADATA_DB_USER", "root")
    mysql_pwd = ENV.get("OM_INGEST_MYSQL_PASSWORD") or ENV.get("METADATA_DB_PASSWORD", "")
    mysql_db = ENV.get("METADATA_DB_DATABASE", "")
    mysql_conn = {
        "type": "Mysql",
        "scheme": "mysql+pymysql",
        "username": mysql_user,
        "authType": {"password": mysql_pwd},
        "hostPort": f"{mysql_host}:{mysql_port}",
        "supportsMetadataExtraction": True,
        "supportsDatabase": True,
        "supportsProfiler": True,
    }
    if mysql_db:
        mysql_conn["databaseName"] = mysql_db
    services.append({
        "name": "adh_mysql",
        "display": "AI-DataHub MySQL (元数据库)",
        "connection": mysql_conn,
        "pipelines": ["metadata"],
    })

    # Elasticsearch（可选，仅当配置了 OM_ES_HOST）
    es_host = ENV.get("OM_ES_HOST", "")
    if es_host:
        es_port = ENV.get("OM_ES_PORT_INGEST", "9200")
        services.append({
            "name": "adh_es",
            "display": "AI-DataHub Elasticsearch",
            "connection": {
                "type": "Elasticsearch",
                "hostPort": f"{es_host}:{es_port}",
                "supportsMetadataExtraction": True,
            },
            "pipelines": ["metadata"],
        })
    return services


def metadata_source_config() -> dict:
    return {
        "type": "DatabaseMetadata",
        "includeViews": True,
        "schemaFilterPattern": {"excludes": SYSTEM_SCHEMA_EXCLUDES},
    }


def init(force: bool = False):
    print(f"[init] OpenMetadata: {OM_URL}")
    admin_token = login_admin()
    print("  [OK] admin 登录成功")

    bot_token = get_bot_token(admin_token)
    write_token_to_env(bot_token)

    for svc in build_services():
        print(f"\n[init] 数据源: {svc['name']}")
        ok = create_db_service(svc["name"], svc["display"], svc["connection"], admin_token, force)
        if not ok and svc.get("fallback_connection"):
            fb = svc["fallback_connection"]
            print(f"  [WARN] {svc['connection']['type']} connector 创建失败，降级为 {fb['type']} connector")
            ok = create_db_service(svc["name"], svc["display"], fb, admin_token, force)
        if not ok:
            continue
        for ptype in svc["pipelines"]:
            if ptype == "metadata":
                cfg = metadata_source_config()
            elif ptype == "lineage":
                cfg = {"type": "DatabaseLineage"}
            elif ptype == "profiler":
                cfg = {"type": "Profiler", "profileSample": 100}
            else:
                continue
            create_pipeline(
                f"{svc['name']}_{ptype}", svc["name"], ptype, cfg, admin_token, force)

    print("\n[init] 完成。可执行以下命令立即触发采集:")
    print("  python init_om.py --trigger")


def trigger(service: str = None):
    """触发全部（或指定服务的）pipeline 立即执行。"""
    admin_token = login_admin()
    status, listing = http("GET", "/api/v1/services/ingestionPipelines?limit=100", admin_token)
    if status != 200:
        raise RuntimeError(f"列出 pipeline 失败: HTTP {status} {listing}")
    pipelines = listing.get("data", [])
    triggered = 0
    for p in pipelines:
        name = p["name"]
        if service and not name.startswith(f"{service}_"):
            continue
        t_status, resp = http("PUT", f"/api/v1/services/ingestionPipelines/{p['id']}/trigger", admin_token)
        if t_status in (200, 201):
            print(f"  [OK] 已触发 {name}")
            triggered += 1
        else:
            print(f"  [FAIL] 触发 {name}: HTTP {t_status} {resp}")
    print(f"[trigger] 共触发 {triggered} 个 pipeline（进度可在 Airflow UI 或 OM UI 查看）")


def show_status():
    admin_token = login_admin()
    for kind, label in [("databaseServices", "数据源"), ("ingestionPipelines", "Pipeline")]:
        status, listing = http("GET", f"/api/v1/services/{kind}?limit=100", admin_token)
        if status != 200:
            print(f"[status] {label} 查询失败: HTTP {status}")
            continue
        data = listing.get("data", [])
        print(f"\n[status] {label} ({len(data)}):")
        for item in data:
            extra = ""
            if kind == "ingestionPipelines":
                extra = f"  type={item.get('pipelineType')} enabled={item.get('enabled')}"
            print(f"  - {item['name']}{extra}")


if __name__ == "__main__":
    args = sys.argv[1:]
    try:
        if "--trigger" in args:
            idx = args.index("--trigger")
            svc = args[idx + 1] if idx + 1 < len(args) and not args[idx + 1].startswith("-") else None
            trigger(svc)
        elif "--status" in args:
            show_status()
        else:
            init(force="--force" in args)
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)
