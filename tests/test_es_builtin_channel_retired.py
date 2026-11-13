"""ES 内置通道退役护栏（阶段三第 2 步）—— 内置通道不得以任何形式复活。

内置通道（`.env` 托管的 `datasource_id=0` ES 目标 + 数据集行的 `source_type='es'`/`es_pattern`）
在 2026-11-04 被划在治理域之外，2026-11-13 的 ES 数据源化推翻了这个口径：ES 现在是正式数据源，
行/列权限与审计都在治理口里。库里已零行，本文件把"机件已删除"钉成用例 —— 行为用例只能证明
"现在没走"，证明不了"以后不会有人再加回来"（同一思路见 tests/test_data_moat_enforcement.py）。

锁四件事：
1. **机件不存在**：`es_client.legacy_ctx`、`dataset_service._query_es` 等入口不得再被定义；
   `es_client` 不得再 import 全局配置（多一个配置来源就是双口径）；
2. **无源即拒**：`ctx_for(0)` 与不带 ctx 的 ES 调用显式报错并给出可行的路，不回退任何全局默认；
3. **写回即拒**：数据集新建/更新递交 `es_pattern` 或 `source_type='es'` 必须被拒（不是静默存成
   普通 SQL 集，那会让用户以为配好了 ES），且拒绝文案给出可操作去向；
4. **配置面端点不挂载**：`/datasets/es-patterns`·`/es-fields`·`/validate-es-sql`·`/channels`
   不得回到路由表；`config` 里也不得留下 `ES_URL` 这类取数通道配置。
"""
import inspect
import io
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.common import config  # noqa: E402
from backend.core import es_client  # noqa: E402
from backend.modules.viz.api import datasets as datasets_api  # noqa: E402
from backend.modules.viz.services import dataset_registry as reg  # noqa: E402
from backend.modules.viz.services import dataset_service as svc  # noqa: E402

# 退役后不得再存在的名字（属性级：存在即说明机件被搬回来）
RETIRED_ATTRS = {
    "es_client": ["legacy_ctx"],
    "dataset_service": ["_query_es", "is_es_dataset", "require_es_channel", "es_channel_state",
                        "es_scope_live", "es_pattern_options", "es_field_options",
                        "validate_es_sql", "mcp_channel_options", "_es_probe_errors",
                        "_require_es_config_perm", "_ES_GOVERNANCE_NOTE",
                        "_ES_CHANNEL_LABEL", "_es_patterns_live", "_DATASET_MCP_CHANNELS"],
    "dataset_registry": ["is_es_definition", "validate_es_definition", "ES_SOURCE_TYPE"],
}
_OWNERS = {"es_client": es_client, "dataset_service": svc, "dataset_registry": reg}

# 已退役的全局配置项（`ES_QUERY_DEDUP_TTL_SEC` 属数据源侧性能旋钮，不在其列）
RETIRED_CONFIG = ["ES_MCP_ENABLED", "ES_URL", "ES_USERNAME", "ES_PASSWORD", "ES_API_KEY",
                  "ES_VERIFY_CERTS", "ES_TIMEOUT_SEC", "ES_MAX_ROWS", "ES_ALLOWED_INDEXES"]


@pytest.mark.parametrize("owner,name", [(o, n) for o, names in RETIRED_ATTRS.items()
                                        for n in names])
def test_退役机件不得再被定义(owner, name):
    assert not hasattr(_OWNERS[owner], name), f"{owner}.{name} 已随内置通道退役，不得复活"


def test_es_client_不再读全局配置():
    """`es_client` 只能从数据源行取配置：留一条 `.env` 兜底就会出现"改了界面没反应"。"""
    assert not hasattr(es_client, "config"), "es_client 不得 import 全局 config"
    src = inspect.getsource(es_client)
    assert "from backend.common import config" not in src


@pytest.mark.parametrize("attr", RETIRED_CONFIG)
def test_全局配置不再有内置通道项(attr):
    assert not hasattr(config, attr), f"config.{attr} 已退役，ES 连接配置只在数据源行上"


def test_去重窗口旋钮仍在数据源侧():
    """这一项不是通道配置（它只管"同参数结果复用多久"），摘通道时不得顺手删掉。"""
    assert isinstance(config.ES_QUERY_DEDUP_TTL_SEC, int)


def test_无数据源的_es_调用显式拒():
    with pytest.raises(es_client.EsError) as exc:
        es_client.ctx_for(0)
    assert "内置通道已退役" in str(exc.value) and "数据源" in str(exc.value)

    with pytest.raises(es_client.EsError):
        es_client.run_sql('SELECT "a" FROM "t_x" LIMIT 1')      # 不给 ctx = 不知打到哪个集群


def test_旧内置通道行不再被当成_es_执行通道():
    """`source_type='es'` + `es_pattern` 的行形状：判据只认执行源的 db_type，不看这两个字段。"""
    legacy_row = {"id": 900, "status": "active", "source_type": "es", "es_pattern": "logs-*",
                  "datasource_id": 0, "object_key": "", "sql_query": 'SELECT 1 FROM "logs-*" LIMIT 1',
                  "sql_bodies": "{}", "variables": "{}", "field_config": "[]",
                  "available_datasources": "[]", "owner_id": 1}
    assert svc.execution_is_es(legacy_row) is False


IDENTITY = {"user_id": 7, "username": "analyst-01", "role": "admin", "workspace_id": 1}
BODY = 'SELECT "a" AS x FROM "logs-*" LIMIT 10'


def test_写回_es_pattern_被拒并给出可行的路(monkeypatch):
    """不能静默存成普通 SQL 集：那会给用户“ES 数据集建好了”的假回执（空承诺）。

    `create_dataset` 先查名称重复（要回源），所以这里只钉到定义校验前：拒绝必须
    发生在落库之前，否则旧字段会被写进行里。
    """
    for req in ({"name": "旧形状", "sql_query": BODY, "es_pattern": "logs-*"},
                {"name": "旧形状", "sql_query": BODY, "source_type": "es"}):
        with pytest.raises(ValueError) as exc:
            svc.create_dataset(req, IDENTITY)
        msg = str(exc.value)
        assert "已退役" in msg and "db_type=es" in msg, f"拒绝文案要可操作: {msg}"


def test_更新路径同样不接受_es_pattern(monkeypatch):
    monkeypatch.setattr(svc, "get_dataset",
                        lambda _id: {"id": 1, "name": "x", "owner_id": 7, "status": "active",
                                     "source_type": "sql", "sql_query": BODY,
                                     "es_pattern": "", "datasource_id": 0})
    writes = []
    monkeypatch.setattr(svc, "execute_write",
                        lambda sql, params=None: writes.append(sql))
    with pytest.raises(ValueError) as exc:
        svc.update_dataset(1, {"es_pattern": "logs-*"}, IDENTITY)
    assert "已退役" in str(exc.value)
    assert writes == [], "拒绝必须发生在落库之前，不得先写再补注"


def test_配置面端点不挂在路由表上():
    paths = {getattr(r, "path", "") for r in datasets_api.router.routes}
    for gone in ("/es-patterns", "/es-fields", "/validate-es-sql", "/channels"):
        assert gone not in paths, f"{gone} 属内置通道配置面，退役后不得回到路由表"


def test_通道筛选只认执行通道():
    """`es` 的语义已换内核：执行通道为 ES 型数据源；按内置服务分维度的写法显式拒。"""
    assert svc._canonical_channel_filter("") == ""
    assert svc._canonical_channel_filter("es") == "es"
    with pytest.raises(ValueError) as exc:
        svc._canonical_channel_filter("mcp:elasticsearch")
    assert "不支持的取数通道筛选" in str(exc.value)


def test_es_client_入口都必须带目标():
    """入口层不得保留“缺 ctx 就拿全局配置兜底”的默认值（内置通道的真正机件）。

    收口点只有一个：`_require_ctx`。任何“没给 ctx 就自己拼一份”的写法都是第二条配置来源。
    """
    src = inspect.getsource(es_client)
    assert "legacy_ctx" not in src
    assert src.count("def _require_ctx") == 1, "兜底口只能有一个，否则新入口会漏接"
    # 真正打集群的那一层必须过这个收口（不依赖入口层是不是已被包上 ctx）
    assert "_require_ctx(ctx)" in inspect.getsource(es_client._request)


def test_es_方言提示仍有一个对外出口(monkeypatch):
    """`_ES_SQL_HINT` 原先只随 `/es-patterns` 下发；通道退役后它的唯一出口改成数据集详情。

    不接这一位的话，这段文案就成了孤儿常量（注释里写着“唯一对外返回的文案源”却没人返），
    下一个改前端的人只会自己照实机行为编一份 —— 那就是第二事实源。
    只对 ES 执行通道下发：非 ES 集看到 ES 方言规则会把人往错误的写法上引。
    """
    row = {"id": 82, "name": "x", "status": "active", "source_type": "sql", "object_key": "",
           "sql_query": 'SELECT 1 FROM "logs-*" LIMIT 1', "sql_bodies": "{}", "variables": "{}",
           "field_config": "[]", "available_datasources": "[]", "chart_preset": "{}",
           "datasource_id": 17, "owner_id": 7, "es_pattern": ""}
    for name, fn in {
        "_load_datasource_catalog": lambda: {},
        "_datasource_name_by_id": lambda *_a, **_k: "cloud-platform-es",
        "usable_sources": lambda *_a, **_k: ([], []),
        "usable_scope": lambda *_a, **_k: ([], []),
        "resolve_effective_source": lambda *_a, **_k: 17,
        "resolve_fields": lambda *_a, **_k: [],
        "_references": lambda *_a, **_k: [],
    }.items():
        monkeypatch.setattr(svc, name, fn)
    user = {"user_id": 7, "role": "admin", "username": "analyst-01"}

    monkeypatch.setattr(svc, "get_dataset", lambda _id: dict(row, is_es=True))
    out = datasets_api.get_dataset(82, user=user, workspace_id=1)
    assert out["es_sql_hint"] == svc._ES_SQL_HINT
    assert "LIMIT" in out["es_sql_hint"]

    monkeypatch.setattr(svc, "get_dataset", lambda _id: dict(row, is_es=False))
    out2 = datasets_api.get_dataset(82, user=user, workspace_id=1)
    assert "es_sql_hint" not in out2, "非 ES 通道不下发 ES 方言规则"


# 消费侧不得再拿退役行形状（source_type='es' / es_pattern）当 ES 判据：
# 库里 0 行后这种写法恒为 false，于是一道安全门会“代码还在、行为已停”地静默失效。
# 典型事故点：planner 的“ES 正文不得按目标源方言二次编译”门、business_asset 的资产路由。
# 只扫代码：说明文字里提“旧写法”是追述历史，允许留在注释里。
_ROW_SHAPE_FILES = [
    "backend/semantics/planner.py",
    "backend/modules/catalog/services/business_asset_ontology.py",
    "frontend/src/pages/datasets/DatasetDetail.tsx",
    "frontend/src/pages/datasets/Datasets.tsx",
    "frontend/src/lib/datasetPickerFilter.ts",
]


def _code_only(src: str) -> str:
    """剥掉块注释与行注释（含 JSX `{/* … */}`），只看真代码。"""
    src = re.sub(r"\{?/\*.*?\*/\}?", " ", src, flags=re.S)
    out = []
    for line in src.splitlines():
        cuts = [p for p in (line.find("#"), line.find("//")) if p >= 0]
        out.append(line[:min(cuts)] if cuts else line)
    return "\n".join(out)


@pytest.mark.parametrize("rel", _ROW_SHAPE_FILES)
def test_消费侧不再拿退役行形状当_es_判据(rel):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = _code_only(io.open(os.path.join(root, rel), encoding="utf-8").read())
    assert "es_pattern" not in src, f"{rel} 代码里不得再读 es_pattern（ES 判据 = 执行源 db_type）"
    hits = [ln.strip() for ln in src.splitlines()
            if "source_type" in ln and re.search(r"[=!]=\s*['\"]es['\"]", ln)]
    assert not hits, f"{rel} 不得拿 source_type=='es' 当 ES 判据（那种行形状已退役，门会静默失效）: {hits}"

