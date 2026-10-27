"""指标/维度字典筛选（列表过滤 + facet 词表）回归（离线, 假 DB 连接）。

背景：字典页原先只有一个关键词搜索 + 一个写死 basic/derived/composite 的“指标类型”
下拉。库里 category 的真实值域是 可观测/客户/案例…，与那三项**无交集**，选任何一项都
返回 0 行 —— 一个“能点但永远查不出结果”的死筛选。收敛为：

1. 筛选值域必须由后端在全量字典上聚合（GET /metrics/filters），前端不得写死枚举；
2. 指标列表是服务端分页的 → 过滤必须下推 SQL（不能先分页再筛）；
3. 空值桶（未分类/未归属）用哨兵 `__unset__` 表达，不得当成“未传”忽略；
4. 非法筛选值 fail-loud（抛错→400），不静默返回全量；
5. facet 计数口径必须与对应列表接口一致（否则下拉数字与结果总数对不上）。
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.modules.catalog.api import metrics as metrics_api          # noqa: E402
from backend.modules.catalog.services import metrics_service as ms      # noqa: E402


def _norm(sql):
    return " ".join(str(sql).split())


class _Cur:
    """按“SQL 形状”回放预设行: responses 为 [(需要同时命中的子串元组, rows)]，首匹配生效。"""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []
        self.rows = []

    def execute(self, sql, params=None):
        s = _norm(sql)
        self.calls.append((s, list(params or [])))
        for subs, rows in self.responses:
            need = subs if isinstance(subs, (tuple, list)) else (subs,)
            if all(x in s for x in need):
                self.rows = rows
                return
        self.rows = []
        return None

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def __init__(self, responses):
        self.cur = _Cur(responses)

    def cursor(self):
        return self.cur

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def conn(monkeypatch):
    """列表类查询: COUNT 回放 total=75, 行查询回放空列表, 但 SQL 全程留痕。"""
    c = _Conn([
        (("COUNT(*) AS total",), [{"total": 75, "certified": 0, "active_n": 52,
                                   "unbound": 0, "with_table": 38, "with_column": 9,
                                   "with_labels": 29}]),
        (("COUNT(*) AS total",), [{"total": 75}]),
    ])
    monkeypatch.setattr(ms, "DBConnection", lambda: c)
    return c


def _metric_sql(conn):
    """取最近那条“列行”查询(带 SELECT 列清单), 尾部两个参数是分页 LIMIT/OFFSET。"""
    rows = [c for c in conn.cur.calls
            if "FROM adh_metrics" in c[0] and "AS display_name" in c[0]]
    return rows[-1]


class TestMetricListFilters:
    def test_category_exact_and_unset_bucket(self, conn):
        ms.list_metrics(size=20, category="可观测")
        sql, params = _metric_sql(conn)
        assert "`category` = %s" in sql
        assert params[:-2] == ["可观测"]
        # 空值桶: 选“未分类”必须查 category 为 NULL/空串的行, 不能当没传
        ms.list_metrics(size=20, category=ms.UNSET_FACET)
        sql2, params2 = _metric_sql(conn)
        assert "COALESCE(category, '') = ''" in sql2
        assert params2[:-2] == []

    def test_agg_type_is_case_insensitive(self, conn):
        ms.list_metrics(size=20, agg_type="count_distinct")
        sql, params = _metric_sql(conn)
        assert "UPPER(COALESCE(agg_type, '')) = %s" in sql
        assert params[:-2] == ["COUNT_DISTINCT"]

    def test_certified_status_and_target_table(self, conn):
        ms.list_metrics(size=20, certified="1", is_active="0", has_target_table="1")
        sql, params = _metric_sql(conn)
        assert "COALESCE(certified, 0) = %s" in sql
        assert "COALESCE(is_active, 0) = %s" in sql
        assert "COALESCE(target_table, '') <> ''" in sql
        assert params[:-2] == [1, 0]

    def test_bound_object_key_and_unset(self, conn):
        ms.list_metrics(size=20, bound_object_key="rum_event")
        sql, params = _metric_sql(conn)
        assert "`bound_object_key` = %s" in sql and params[:-2] == ["rum_event"]
        ms.list_metrics(size=20, bound_object_key=ms.UNSET_FACET)
        sql2, _ = _metric_sql(conn)
        assert "COALESCE(bound_object_key, '') = ''" in sql2

    def test_search_covers_aliases(self, conn):
        """别名是字典解析的合法入口, 搜索口漏掉别名 = 改了别名却搜不到。"""
        ms.list_metrics(size=20, search="口扫")
        sql, params = _metric_sql(conn)
        assert "COALESCE(aliases, '') LIKE %s" in sql
        assert params[:-2] == ["%口扫%"] * 4

    def test_tags_filter_uses_json_column(self, conn):
        """口径标签走 tags JSON 列（旧写法 FIND_IN_SET(..., category) 查的是不存在的列，
        本参数一旦传入必 1054）。"""
        ms.list_metrics(size=20, tags="基础,复合")
        sql, params = _metric_sql(conn)
        assert sql.count("JSON_CONTAINS(COALESCE(tags, '[]'), %s)") == 2
        assert params[:-2] == ['"基础"', '"复合"']

    def test_row_payload_carries_certified_and_owner(self, conn):
        """认证/负责人两列要随列表返回: 缺列会让 UI 的认证标记与负责人永远空(假装有数据)。"""
        ms.list_metrics(size=20)
        sql, _ = _metric_sql(conn)
        select_part = sql.split("FROM adh_metrics")[0]
        assert "AS certified" in select_part
        assert "owner" in select_part

    def test_list_select_returns_category_not_metric_type(self, conn):
        """返回体只给 category（业务域）+ tags（口径标签）：`category AS metric_type` 会把
        两个维度在客户端重新捏成一个字段，混杂卷来。"""
        ms.list_metrics(size=20)
        sql, _ = _metric_sql(conn)
        select_part = sql.split("FROM adh_metrics")[0]
        assert "metric_type" not in select_part
        assert "category" in select_part and "AS tags" in select_part

    def test_default_call_keeps_legacy_sql(self, conn):
        """不传新参数时不得凭空加谓词(旧行为: 全列, 含 is_active=0 的行)。"""
        ms.list_metrics(size=20, search="GMV")
        sql, _ = _metric_sql(conn)
        where = sql.split("WHERE", 1)[1]
        for frag in ("certified", "target_table", "bound_object_key", "`category`", "agg_type"):
            assert frag not in where
        assert "is_active" not in where

    def test_invalid_bool_filter_raises(self, conn):
        """非法值不得当成“未传”默默返回全量。"""
        with pytest.raises(ValueError, match="certified"):
            ms.list_metrics(size=20, certified="maybe")
        with pytest.raises(ValueError, match="has_target_table"):
            ms.list_metrics(size=20, has_target_table="yes-please")


class TestMetricKindWriteContract:
    """category = 业务域，tags = 口径标签（基础/衍生/复合）：两个正交维度不得再写进同一列。

    历史缺陷：表单把 metric_type(basic/derived/composite) 直接落 category，分类下拉里
    业务域与口径标签混杂，选一次「基础指标」就把域值域写脏。
    """

    def test_create_keeps_domain_in_category_and_kind_in_tags(self, monkeypatch):
        c = _Conn([
            (("SELECT id FROM adh_metrics WHERE name",), []),
            (("object_key FROM adh_ontology_objects",), [{"object_key": "case"}]),
            (("MAX(id)",), [{"nid": 900}]),
        ])
        monkeypatch.setattr(ms, "DBConnection", lambda: c)
        ms.create_metric({"name": "case_count", "display_name": "案例数",
                          "formula": "COUNT(id)", "agg_type": "count",
                          "bound_object_key": "case",
                          "category": "可观测", "metric_type": "basic", "tags": ["复合"]})
        insert = [call for call in c.cur.calls if call[0].startswith("INSERT INTO adh_metrics")][-1]
        sql, params = insert
        assert " tags)" in sql.replace("\n", " ")
        cols = sql.split("(", 1)[1].split(")")[0]
        got = dict(zip([x.strip() for x in cols.split(",")], params))
        assert got["category"] == "可观测"            # 业务域不得被 metric_type 冲掉
        assert json.loads(got["tags"]) == ["复合", "基础"]   # 显式标签 + 旧口径归一

    def test_create_defaults_domain_and_never_writes_kind_to_category(self, monkeypatch):
        c = _Conn([
            (("SELECT id FROM adh_metrics WHERE name",), []),
            (("object_key FROM adh_ontology_objects",), [{"object_key": "case"}]),
            (("MAX(id)",), [{"nid": 901}]),
        ])
        monkeypatch.setattr(ms, "DBConnection", lambda: c)
        ms.create_metric({"name": "case_avg", "display_name": "案例均时长",
                          "formula": "COUNT(id)", "agg_type": "count",
                          "bound_object_key": "case", "metric_type": "basic"})
        insert = [call for call in c.cur.calls if call[0].startswith("INSERT INTO adh_metrics")][-1]
        sql, params = insert
        cols = sql.split("(", 1)[1].split(")")[0]
        got = dict(zip([x.strip() for x in cols.split(",")], params))
        assert got["category"] == "业务"                 # 域默认值（不是 basic）
        assert json.loads(got["tags"]) == ["基础"]

    def test_update_routes_legacy_metric_type_to_tags(self, monkeypatch):
        c = _Conn([(("SELECT id FROM adh_metrics WHERE id",), [{"id": 5}])])
        monkeypatch.setattr(ms, "DBConnection", lambda: c)
        ms.update_metric(5, {"metric_type": "derived"})
        upd = [call for call in c.cur.calls if call[0].startswith("UPDATE adh_metrics")][-1]
        assert "tags = %s" in upd[0] and "category = %s" not in upd[0]
        assert json.loads(upd[1][0]) == ["衍生"]

    def test_update_category_and_tags_land_in_own_columns(self, monkeypatch):
        c = _Conn([(("SELECT id FROM adh_metrics WHERE id",), [{"id": 5}])])
        monkeypatch.setattr(ms, "DBConnection", lambda: c)
        ms.update_metric(5, {"category": "客户", "tags": ["基础"]})
        upd = [call for call in c.cur.calls if call[0].startswith("UPDATE adh_metrics")][-1]
        assert "category = %s" in upd[0] and "tags = %s" in upd[0]
        assert upd[1][:2] == ["客户", '["基础"]']


def _facet_conn(monkeypatch):
    responses = [
        # 指标 summary
        (("FROM adh_metrics", "COUNT(*) AS total"),
         [{"total": 75, "active_n": 52, "certified": 3, "unbound": 4,
           "with_table": 38, "with_column": 9}]),
        (("FROM adh_metrics", "AS tags"),
         [{"tags": '["基础", "复合"]'}, {"tags": '["基础"]'}, {"tags": "[]"}]),
        (("FROM adh_metrics", "agg_type"),
         [{"value": "COUNT", "count": 34}, {"value": "", "count": 2}]),
        (("FROM adh_metrics", "category"),
         [{"value": "可观测", "count": 14}, {"value": None, "count": 22},
          {"value": "客户", "count": 5}]),
        (("FROM adh_metrics", "bound_object_key"),
         [{"value": "rum_event", "count": 14}, {"value": "", "count": 4}]),
        # 维度 summary / category / 归属
        (("FROM adh_dimensions", "COUNT(*) AS total"),
         [{"total": 59, "active_n": 59, "certified": 19, "unbound": 4,
           "with_table": 38, "with_column": 57, "with_labels": 29}]),
        (("FROM adh_dimensions", "category"),
         [{"value": "属性", "count": 29}, {"value": "状态", "count": 13}]),
        (("FROM adh_dimensions", "bound_object_key"),
         [{"value": "rum_event", "count": 19}, {"value": "case", "count": 8}]),
        (("FROM adh_ontology_objects",),
         [{"object_key": "rum_event", "display_name": "RUM观测事件"},
          {"object_key": "dataset", "display_name": "数据集"}]),
    ]
    c = _Conn(responses)
    monkeypatch.setattr(ms, "DBConnection", lambda: c)
    return c


class TestDictFacets:
    def test_facets_buckets_and_counts(self, monkeypatch):
        conn = _facet_conn(monkeypatch)
        out = ms.dict_facets()
        # 分类: 真实值域(不是写死的 basic/derived/composite)，空值归入哨兵桶
        assert {"value": "可观测", "count": 14} in out["metrics"]["categories"]
        assert {"value": ms.UNSET_FACET, "count": 22} in out["metrics"]["categories"]
        # 选项按条数降序(最常选的值靠顶部), 计数不得丢失
        counts = [f["count"] for f in out["metrics"]["categories"]]
        assert counts == sorted(counts, reverse=True)
        assert sum(counts) == 41
        # 聚合方式大小写归一 + 空桶
        assert {"value": "COUNT", "count": 34} in out["metrics"]["agg_types"]
        assert {"value": ms.UNSET_FACET, "count": 2} in out["metrics"]["agg_types"]
        # 口径标签分桶：JSON 数组拆元素计数，降序（与 categories 的**业务域**是两个维度）
        assert out["metrics"]["tags"] == [{"value": "基础", "count": 2},
                                        {"value": "复合", "count": 1}]
        # 计数口径与列表接口一致(指标含停用行, 维度只算 is_active=1)
        assert out["metrics"]["total"] == 75 and out["metrics"]["active"] == 52
        assert out["metrics"]["inactive"] == 23 and out["metrics"]["draft"] == 72
        assert out["dimensions"]["with_value_labels"] == 29
        # 指标表的 summary SQL 不得引用只在维度存在的列(真库上会 1054)
        m_summary = [c for c in conn.cur.calls
                     if "FROM adh_metrics" in c[0] and "COUNT(*) AS total" in c[0]][0][0]
        assert "value_labels" not in m_summary

    def test_object_options_carry_business_name(self, monkeypatch):
        _facet_conn(monkeypatch)
        out = ms.dict_facets()
        objs = {o["key"]: o for o in out["objects"]}
        assert objs["rum_event"]["display_name"] == "RUM观测事件"
        assert objs["rum_event"]["metric_count"] == 14
        assert objs["rum_event"]["dimension_count"] == 19
        # 字典引用了但对象表里没有的 key: 仍进清单(名字留空由前端回退显示 key)，不静默丢弃
        assert objs["case"]["display_name"] == "" and objs["case"]["dimension_count"] == 8
        # 未被任何字典引用的对象也在名字表里(供归属列显示名字)
        assert objs["dataset"]["metric_count"] == 0

    def test_model_scope_short_circuits_without_query(self, monkeypatch):
        """模型无生效对象 → 该表 facet 归零, 不得回退成“不过滤的全量计数”。"""
        conn = _Conn([(("FROM adh_ontology_objects", "object_key FROM"), [])])
        monkeypatch.setattr(ms, "DBConnection", lambda: conn)
        out = ms.dict_facets(model_id=99, scope="model")
        assert out["metrics"]["total"] == 0 and out["dimensions"]["total"] == 0
        assert out["metrics"]["categories"] == [] and out["objects"] == []
        assert out["metrics"]["tags"] == []
        assert not any("adh_metrics" in c[0] or "FROM adh_dimensions" in c[0]
                       for c in conn.cur.calls)


class TestFiltersRoute:
    def test_route_registered_before_metric_id(self):
        paths = [r.path for r in metrics_api.router.routes]
        assert "/filters" in paths
        assert paths.index("/filters") < paths.index("/{metric_id}"), \
            "/filters 必须注册在 /{metric_id} 之前, 否则被参数路由吞掉"

    def test_list_route_passes_filters_to_service(self, monkeypatch):
        captured = {}

        def _fake(**kwargs):
            captured.update(kwargs)
            return {"total": 1, "items": []}

        monkeypatch.setattr(ms, "list_metrics", _fake)
        metrics_api.list_metrics(
            page=1, size=20, metric_type=None, tags=None, search="GMV",
            model_id=None, scope=None, category="可观测", agg_type="sum",
            certified="1", bound_object_key=ms.UNSET_FACET,
            has_target_table="0", is_active="1")
        assert captured["category"] == "可观测"
        assert captured["agg_type"] == "sum"
        assert captured["certified"] == "1"
        assert captured["bound_object_key"] == ms.UNSET_FACET
        assert captured["has_target_table"] == "0"
        assert captured["is_active"] == "1"

    def test_list_route_rejects_invalid_filter(self, monkeypatch):
        monkeypatch.setattr(ms, "list_metrics",
                            lambda **kw: (_ for _ in ()).throw(ValueError("certified 仅支持 1/0")))
        with pytest.raises(Exception) as exc:
            metrics_api.list_metrics(
                page=1, size=20, metric_type=None, tags=None, search="",
                model_id=None, scope=None, category=None, agg_type=None,
                certified="maybe", bound_object_key=None, has_target_table=None,
                is_active=None)
        assert getattr(exc.value, "status_code", None) == 400

    def test_filters_route_passes_scope(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(ms, "dict_facets", lambda **kw: seen.update(kw) or {"objects": []})
        metrics_api.dict_filters(model_id=7, scope="model")
        assert seen == {"model_id": 7, "scope": "model"}
        with pytest.raises(Exception) as exc:
            metrics_api.dict_filters(model_id=None, scope="bogus")
        assert getattr(exc.value, "status_code", None) == 400
