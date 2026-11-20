"""SPARQL 语句形态判定（`/api/graph/query` 的路由依据）。

锁历史缺陷：按 `strip().startswith("SELECT")` 判形态，会让所有以 PREFIX 开头的查询误入
UPDATE 通道（Oxigraph /update 直接 400）——控制台自带示例全是 PREFIX 开头，点了必失败。
"""
import pytest
from fastapi import HTTPException

from backend.modules.semhub.api.graph import _sparql_form

PREFIXES = (
    "PREFIX adh: <http://ai-datahub.org/ontology/>\n"
    "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
)


def test_select_after_prefix_is_query():
    assert _sparql_form(PREFIXES + "SELECT ?s WHERE { ?s ?p ?o } LIMIT 1") == "query"


def test_ask_after_prefix_is_query():
    assert _sparql_form(PREFIXES + "ASK { ?s ?p ?o }") == "query"


def test_leading_comment_and_blank_lines_are_skipped():
    q = "# 概览命名图\n\n" + PREFIXES + "SELECT * WHERE { ?s ?p ?o }"
    assert _sparql_form(q) == "query"


def test_construct_is_graph():
    assert _sparql_form(PREFIXES + "CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }") == "graph"


def test_describe_is_graph():
    assert _sparql_form(PREFIXES + "DESCRIBE <http://ai-datahub.org/ontology/obj:X>") == "graph"


def test_insert_data_is_update():
    assert _sparql_form(PREFIXES + "INSERT DATA { adh:A adh:b adh:C }") == "update"


def test_delete_with_with_clause_is_update():
    assert _sparql_form(PREFIXES + "WITH <http://ai-datahub.org/ontology/ds:0> DELETE { ?s ?p ?o } WHERE { ?s ?p ?o }") == "update"


def test_empty_statement_is_explicit_400():
    """只有注释/PREFIX 时不得落到 UPDATE 通道，必须显式 400。"""
    with pytest.raises(HTTPException) as ei:
        _sparql_form(PREFIXES + "# 没有实际语句\n")
    assert ei.value.status_code == 400
