"""Oxigraph Store — RDF triple store operations for GraphRAG.

Replaces the previous Neo4j store with Oxigraph SPARQL-based operations.
Uses named graphs per datasource for isolation: ``<adh:ds:{id}>``.

Node model (RDF triples):
    adh:table:{name}       a adh:Table ;  rdfs:label "..." ; adh:comment "..." .
    adh:col:{table}.{col}  a adh:Column ; rdfs:label "..." ; adh:dataType "..." .
    adh:table:{t} adh:hasColumn adh:col:{t}.{c} .
    adh:table:{t1} adh:join adh:table:{t2} .
    adh:term:{name}        a adh:Term ;   adh:mapsTo adh:col:{t}.{c} .
    adh:metric:{name}      a adh:Metric ; adh:defines adh:col:{t}.{c} .
    adh:dataset:{name}     a adh:Dataset ; adh:comment "..." ;
                           adh:touchTable adh:table:{t} ; adh:queriesObject adh:obj:{k} .
"""

import logging
from typing import Any

from backend.common.rdf.sparql_client import OxigraphClient, get_sparql_client
from backend.common.rdf.namespaces import ADH_NS

logger = logging.getLogger(__name__)

# 系统域专用图哨兵 datasource_id: 与 ds:0("聚合全部", 供 ChatBI 全局检索)区分开,
# 专供 AS-BOT 系统能力本体图谱使用 —— 只含 datasource_id IS NULL/0 的系统元数据与系统本体模型,
# 杜绝业务本体(如 test-alb, datasource_id>0)串入(见规则 as-bot-system-waker.md)。
SYSTEM_DATASOURCE_ID = -1


def is_system_scope(datasource_id: int) -> bool:
    return datasource_id == SYSTEM_DATASOURCE_ID


def graph_target_for_model(kind: str, datasource_id: int) -> int:
    """本体模型 → 目标命名图的 datasource 哨兵（**写入与状态对账共用同一口径**）。

    图谱按 **kind** 分图，不按 datasource_id：
      system → ``ds:-1``（AS-BOT 系统域图）; business → ``ds:0``（跨源业务聚合图）。
      源本体已退役（WS2 合并为 business）；具体数据源的命名图 ``ds:N`` 只留物理血缘（无本体）。
    
    为什么必须共用：旧实现只在写入侧按 kind 分图，而徽章用的
    ``ontology_kb_sync.model_sync_status`` 自己按 ``ds:{datasource_id}`` 查图，
    于是两个活着的 kind 全部误报：系统本体(ds_id=0) 查到的是**业务聚合图**
    （显示“滞后 17/12”，而真系统图 ds:-1 与实际同步），业务本体(ds_id>0)
    查到的是空图（显示“未生成”，而它在 ds:0 里完整存在）。口径分叉就是缺陷本身。
    """
    ds = int(datasource_id or 0)
    if kind == "system":
        return SYSTEM_DATASOURCE_ID
    if kind == "business":
        return 0
    # 未知/缺失 kind（含退役前的残留 source）：按 datasource_id 哨兵推断
    return SYSTEM_DATASOURCE_ID if is_system_scope(ds) else ds


class OxigraphStore:
    """Oxigraph-backed RDF store for the knowledge graph."""

    def __init__(self, client: OxigraphClient = None):
        self._client = client or get_sparql_client()

    # ── Named graph helpers ──────────────────────────────────────────

    @staticmethod
    def graph_uri(datasource_id: int = 0) -> str:
        return f"{ADH_NS}ds:{datasource_id}"

    # ── Node CRUD ────────────────────────────────────────────────────

    def create_table_node(self, name: str, comment: str = "",
                          business_desc: str = "", datasource_id: int = 0):
        self.create_table_nodes(
            [{"name": name, "comment": comment, "business_desc": business_desc}],
            datasource_id=datasource_id)

    def create_table_nodes(self, items: list[dict], datasource_id: int = 0,
                           timeout: float = None) -> int:
        """批量建表节点（一个 INSERT DATA 装整批）—— 理由见 create_column_nodes。"""
        graph = self.graph_uri(datasource_id)
        body: list[str] = []
        for t in items or []:
            # 先 strip 再判空：只判 falsy 会让 "  " 这类空白值逃过校验，
            # 生成一个 label 为空白的垃圾节点（图里不可用、又占指标数）。
            name = str(t.get("name") or "").strip()
            if not name:
                continue
            iri = f"{ADH_NS}table:{_safe(name)}"
            body.append(
                f"<{iri}> a <{ADH_NS}Table> ;"
                f" <http://www.w3.org/2000/01/rdf-schema#label> \"{_esc(name)}\" ;"
                f" <{ADH_NS}comment> \"{_esc(t.get('comment') or '')}\" ;"
                f" <{ADH_NS}businessDesc> \"{_esc(t.get('business_desc') or '')}\" ."
            )
        if not body:
            return 0
        self._client.update(
            f"INSERT DATA {{ GRAPH <{graph}> {{ {' '.join(body)} }} }}",
            timeout=timeout)
        return len(body)

    def create_column_node(self, table: str, column: str, data_type: str = "",
                           comment: str = "", datasource_id: int = 0):
        self.create_column_nodes(
            [{"table": table, "column": column,
              "data_type": data_type, "comment": comment}],
            datasource_id=datasource_id)

    def create_column_nodes(self, items: list[dict], datasource_id: int = 0,
                            timeout: float = None) -> int:
        """批量建列节点：一个 INSERT DATA 内装整批三元组。

        为什么必须批量：旧实现每列一次 ``update()`` —— 一次 HTTP 往返。全目录重建
        （``datasource_id=0`` 是「聚合全部」语义）要写 1.5 万+ 列，就是 1.5 万次
        串行往返 —— 实测表现为 ``save_draft`` 卡死数百秒不返回（不是网络慢，
        是次数错）。

        返回写入节点数。空批不发请求（避免空 INSERT 被引擎拒）。
        """
        graph = self.graph_uri(datasource_id)
        body: list[str] = []
        for c in items or []:
            # 同上：必须先 strip 再判空，否则空白列名会生成垃圾节点
            table = str(c.get("table") or "").strip()
            column = str(c.get("column") or "").strip()
            if not table or not column:
                continue
            col_iri = f"{ADH_NS}col:{_safe(table)}.{_safe(column)}"
            tbl_iri = f"{ADH_NS}table:{_safe(table)}"
            body.append(
                f"<{col_iri}> a <{ADH_NS}Column> ;"
                f" <http://www.w3.org/2000/01/rdf-schema#label> \"{_esc(column)}\" ;"
                f" <{ADH_NS}dataType> \"{_esc(c.get('data_type') or '')}\" ;"
                f" <{ADH_NS}comment> \"{_esc(c.get('comment') or '')}\" ;"
                f" <{ADH_NS}tableName> \"{_esc(table)}\" ."
                f" <{tbl_iri}> <{ADH_NS}hasColumn> <{col_iri}> ."
            )
        if not body:
            return 0
        self._client.update(
            f"INSERT DATA {{ GRAPH <{graph}> {{ {' '.join(body)} }} }}",
            timeout=timeout)
        return len(body)

    def create_term_node(self, name_cn: str, name_en: str = "",
                         description: str = "", calculation: str = "",
                         datasource_id: int = 0):
        iri = f"{ADH_NS}term:{_safe(name_cn)}"
        graph = self.graph_uri(datasource_id)
        self._client.update(f"""
            INSERT DATA {{
                GRAPH <{graph}> {{
                    <{iri}> a <{ADH_NS}Term> ;
                        <{ADH_NS}nameCn> "{_esc(name_cn)}" ;
                        <{ADH_NS}nameEn> "{_esc(name_en)}" ;
                        <{ADH_NS}comment> "{_esc(description)}" ;
                        <{ADH_NS}calculation> "{_esc(calculation)}" .
                }}
            }}
        """)

    def create_metric_node(self, name: str, description: str = "",
                           datasource_id: int = 0):
        iri = f"{ADH_NS}metric:{_safe(name)}"
        graph = self.graph_uri(datasource_id)
        self._client.update(f"""
            INSERT DATA {{
                GRAPH <{graph}> {{
                    <{iri}> a <{ADH_NS}Metric> ;
                        <http://www.w3.org/2000/01/rdf-schema#label> "{_esc(name)}" ;
                        <{ADH_NS}comment> "{_esc(description)}" .
                }}
            }}
        """)

    def create_datasource_node(self, ds_id: int, name: str,
                               db_type: str = ""):
        iri = f"{ADH_NS}datasource:{ds_id}"
        graph = self.graph_uri(0)
        self._client.update(f"""
            INSERT DATA {{
                GRAPH <{graph}> {{
                    <{iri}> a <{ADH_NS}DataSource> ;
                        <http://www.w3.org/2000/01/rdf-schema#label> "{_esc(name)}" ;
                        <{ADH_NS}dbType> "{_esc(db_type)}" .
                }}
            }}
        """)

    # ── Relationship CRUD ────────────────────────────────────────────

    def create_join_relation(self, table1: str, table2: str,
                             join_type: str = "", datasource_id: int = 0):
        t1 = f"{ADH_NS}table:{_safe(table1)}"
        t2 = f"{ADH_NS}table:{_safe(table2)}"
        graph = self.graph_uri(datasource_id)
        self._client.update(f"""
            INSERT DATA {{
                GRAPH <{graph}> {{
                    <{t1}> <{ADH_NS}join> <{t2}> .
                    <{t2}> <{ADH_NS}join> <{t1}> .
                }}
            }}
        """)

    def create_term_mapping(self, term_name: str, table: str, column: str,
                            datasource_id: int = 0):
        term_iri = f"{ADH_NS}term:{_safe(term_name)}"
        col_iri = f"{ADH_NS}col:{_safe(table)}.{_safe(column)}"
        graph = self.graph_uri(datasource_id)
        self._client.update(f"""
            INSERT DATA {{
                GRAPH <{graph}> {{
                    <{term_iri}> <{ADH_NS}mapsTo> <{col_iri}> .
                }}
            }}
        """)

    def create_metric_column_relation(self, metric_name: str, table: str,
                                      column: str, datasource_id: int = 0):
        m_iri = f"{ADH_NS}metric:{_safe(metric_name)}"
        col_iri = f"{ADH_NS}col:{_safe(table)}.{_safe(column)}"
        graph = self.graph_uri(datasource_id)
        self._client.update(f"""
            INSERT DATA {{
                GRAPH <{graph}> {{
                    <{m_iri}> <{ADH_NS}defines> <{col_iri}> .
                }}
            }}
        """)

    def create_dimension_node(self, name: str, description: str = "",
                              category: str = "", datasource_id: int = 0):
        iri = f"{ADH_NS}dimension:{_safe(name)}"
        graph = self.graph_uri(datasource_id)
        description = description or ""
        category = category or ""
        self._client.update(f"""
            INSERT DATA {{
                GRAPH <{graph}> {{
                    <{iri}> a <{ADH_NS}Dimension> ;
                        <http://www.w3.org/2000/01/rdf-schema#label> "{_esc(name)}" ;
                        <{ADH_NS}comment> "{_esc(description)}" ;
                        <{ADH_NS}category> "{_esc(category)}" .
                }}
            }}
        """)

    def create_dimension_column_relation(self, dimension_name: str, table: str,
                                         column: str, datasource_id: int = 0):
        d_iri = f"{ADH_NS}dimension:{_safe(dimension_name)}"
        col_iri = f"{ADH_NS}col:{_safe(table)}.{_safe(column)}"
        graph = self.graph_uri(datasource_id)
        self._client.update(f"""
            INSERT DATA {{
                GRAPH <{graph}> {{
                    <{d_iri}> <{ADH_NS}usesDimension> <{col_iri}> .
                }}
            }}
        """)

    def create_object_metric_relation(self, object_key: str, metric_name: str,
                                      datasource_id: int = 0):
        """对象→指标(归属): 源于字典 bound_object_key, 与 hasProperty/hasDimension 对称。

        指标节点唯一 IRI = adh:metric:{name}(字典权威), 不再生成 objmetric 重复节点。
        """
        object_key = (object_key or "").strip()
        metric_name = (metric_name or "").strip()
        if not object_key or not metric_name:
            return
        obj_iri = f"{ADH_NS}obj:{_safe(object_key)}"
        m_iri = f"{ADH_NS}metric:{_safe(metric_name)}"
        graph = self.graph_uri(datasource_id)
        self._client.update(f"""
            INSERT DATA {{
                GRAPH <{graph}> {{
                    <{obj_iri}> <{ADH_NS}hasMetric> <{m_iri}> .
                }}
            }}
        """)

    def create_object_dimension_relation(self, object_key: str, dimension_name: str,
                                         datasource_id: int = 0):
        """对象→维度(归属): 源于字典 bound_object_key, 与对象→指标对称。"""
        object_key = (object_key or "").strip()
        dimension_name = (dimension_name or "").strip()
        if not object_key or not dimension_name:
            return
        obj_iri = f"{ADH_NS}obj:{_safe(object_key)}"
        d_iri = f"{ADH_NS}dimension:{_safe(dimension_name)}"
        graph = self.graph_uri(datasource_id)
        self._client.update(f"""
            INSERT DATA {{
                GRAPH <{graph}> {{
                    <{obj_iri}> <{ADH_NS}hasDimension> <{d_iri}> .
                }}
            }}
        """)

    def create_dataset_node(self, name: str, description: str = "",
                            source_type: str = "", object_key: str = "",
                            variables: str = "", tables: list[str] = None,
                            datasource_id: int = 0):
        """Create an adh:Dataset node (governed query asset) with touched tables.

        数据集是图谱里的一等节点，供检索层用 SPARQL 进行业务提问的 grounding。
        **不写 SQL 正文**：图内容会被检索到并交给 LLM，而预制 SQL 由服务端按名执行，
        把正文入图 = 把物理表/实现细节送到模型眼前（护栏 §7）。
        """
        name = str(name or "").strip()
        if not name:
            return
        iri = f"{ADH_NS}dataset:{_safe(name)}"
        graph = self.graph_uri(datasource_id)
        touch_triples = ""
        for t in (tables or []):
            tbl_iri = f"{ADH_NS}table:{_safe(t)}"
            touch_triples += f"\n                    <{iri}> <{ADH_NS}touchTable> <{tbl_iri}> ."
        obj_triple = ""
        if str(object_key or "").strip():
            obj_triple = (f"\n                    <{iri}> <{ADH_NS}queriesObject> "
                          f"<{ADH_NS}obj:{_safe(object_key)}> .")
        self._client.update(f"""
            INSERT DATA {{
                GRAPH <{graph}> {{
                    <{iri}> a <{ADH_NS}Dataset> ;
                        <http://www.w3.org/2000/01/rdf-schema#label> "{_esc(name)}" ;
                        <{ADH_NS}comment> "{_esc(description)}" ;
                        <{ADH_NS}sourceType> "{_esc(source_type)}" ;
                        <{ADH_NS}variables> "{_esc(variables)}" .{touch_triples}{obj_triple}
                }}
            }}
        """)

    # ── Bulk operations ──────────────────────────────────────────────

    def load_turtle(self, turtle_str: str, datasource_id: int = 0):
        """Bulk-load Turtle RDF into a named graph."""
        graph = self.graph_uri(datasource_id)
        self._client.load_rdf(turtle_str, graph_uri=graph)

    def clear_graph(self, datasource_id: int = 0):
        """Clear all triples in a datasource's named graph."""
        graph = self.graph_uri(datasource_id)
        try:
            self._client.clear_graph(graph)
        except Exception as e:
            logger.warning("Failed to clear graph %s: %s", graph, e)

    # ── Stats ────────────────────────────────────────────────────────

    def count_triples(self, datasource_id: int = 0) -> int:
        return self._client.count_triples(self.graph_uri(datasource_id))

    def health(self) -> bool:
        return self._client.health()


# ── Helpers ──────────────────────────────────────────────────────────

def _safe(name: str) -> str:
    """Sanitize name for use in RDF IRI local name."""
    if not name:
        return "unnamed"
    result = ""
    for ch in name:
        if ch.isalnum() or ch == "_":
            result += ch
        else:
            result += "_"
    if result and result[0].isdigit():
        result = "_" + result
    return result or "unnamed"


def _esc(text: str) -> str:
    """Escape text for RDF literal."""
    return (text.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r"))
