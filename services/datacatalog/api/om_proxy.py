"""OpenMetadata 代理 API — 搜索/表详情/血缘（服务端持有 token）。

挂载在 /api/catalog 前缀下:
    GET /api/catalog/om/status
    GET /api/catalog/om/search?q=&size=&index=
    GET /api/catalog/om/services
    GET /api/catalog/om/table/{fqn}
    GET /api/catalog/om/lineage/{fqn}
"""

import logging

from fastapi import APIRouter, HTTPException, Query

from ..services import om_client
from ..services.om_client import OMClientError

logger = logging.getLogger(__name__)
router = APIRouter()


def _call(fn, *args, **kwargs):
    """统一异常映射：OMClientError → HTTPException。"""
    try:
        return fn(*args, **kwargs)
    except OMClientError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))


@router.get("/om/status")
def om_status():
    """OM 集成状态（enabled/reachable/version），前端据此决定是否渲染嵌入入口。"""
    return _call(om_client.get_status)


@router.get("/om/search")
def om_search(
    q: str = Query(..., min_length=1, description="搜索关键词"),
    index: str = Query(None, description="限定索引，如 table_search_index"),
    size: int = Query(10, ge=1, le=100),
):
    """全文搜索 OM 实体。"""
    return _call(om_client.search, q, index=index, size=size)


@router.get("/om/services")
def om_services():
    """已注册的数据源服务列表。"""
    result = _call(om_client.list_services)
    return {
        "data": [
            {"id": s["id"], "name": s["name"], "serviceType": s.get("serviceType"),
             "displayName": s.get("displayName")}
            for s in result.get("data", [])
        ]
    }


@router.get("/om/table/{fqn:path}")
def om_table(fqn: str):
    """表详情（FQN 形如 adh_doris.adh.dwd_order）。"""
    return _call(om_client.get_table, fqn)


@router.get("/om/lineage/{fqn:path}")
def om_lineage(
    fqn: str,
    upstream_depth: int = Query(3, ge=0, le=10),
    downstream_depth: int = Query(3, ge=0, le=10),
):
    """表级血缘图（上下游节点与边）。"""
    return _call(om_client.get_lineage, fqn, upstream_depth, downstream_depth)
