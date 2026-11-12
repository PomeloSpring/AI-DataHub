"""Chart API — 单图重查与缓存读取的旧入口（`/api/charts`）。

2026-11-06 取数通道收口后，本模块**不再自己执行 SQL**：
- 旧版这里有一份与看板详情并行的取数实现（直接执行 `adh_charts.sql_query`、接受前端
  递交 `count_sql`、且不校验看板可见性），等于第二条取数通道 + 越权读图；
- 现在两个端点都只是 `dashboard_service` 的薄封装：分派与治理一律走
  `ChartService.refresh_chart`（可见性校验 + 数据集/声明式语义/AS-BOT 设计三通道）。

前端主链路走 `/api/dashboard/{dashboard_id}/charts/{chart_id}/refresh`；本前缀保留给
只知道 chart_id 的调用方（架构文档 viz 段），不是第二条口径。
"""

import logging
from typing import Optional

import pymysql
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.common.auth import get_current_user, get_workspace_id
from backend.common.db import execute_query
from backend.core.governed_query import NoIdentityError
from backend.modules.viz.services.dashboard_service import (
    chart_service,
    check_dashboard_visible,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class ChartRefreshRequest(BaseModel):
    params: Optional[dict] = {}
    page_size: Optional[int] = None
    page_num: Optional[int] = None


@router.post("/{chart_id}/refresh")
def refresh_chart(
    chart_id: int,
    req: ChartRefreshRequest = ChartRefreshRequest(),
    user: dict = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
):
    """按查看者身份重查图表（取数通道与看板详情同一实现）。

    图表只需 chart_id：先回查它所属看板，再交给 `refresh_chart` 做可见性裁决 + 通道分派；
    不接受任何调用方递交的 SQL/统计语句（旧 `count_sql` 是第二条取数通道，已废除）。
    """
    row = execute_query("SELECT dashboard_id FROM adh_charts WHERE id = %s",
                        (chart_id,), fetchone=True)
    if not row:
        raise HTTPException(status_code=404, detail="图表不存在")
    dashboard_id = int(row.get("dashboard_id") or 0)
    if not check_dashboard_visible(dashboard_id, user.get("user_id") or 0):
        # 不回“该图属于哪个看板”以外的信息，也不返回数据行（越权读取一律拒）
        raise HTTPException(status_code=403, detail="无权访问该图表所在看板")
    try:
        return chart_service.refresh_chart(
            dashboard_id, chart_id,
            params=req.params,
            page_size=req.page_size,
            page_num=req.page_num,
            user_id=user.get("user_id") or 0,
            workspace_id=workspace_id,
            username=user.get("username", "") or "",
        )
    except NoIdentityError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except pymysql.Error:
        logger.exception("Failed to refresh chart %s", chart_id)
        raise HTTPException(status_code=400, detail="图表取数失败，请联系管理员查看服务端日志")
    except Exception:
        logger.exception("Failed to refresh chart %s", chart_id)
        raise HTTPException(status_code=400, detail="图表取数失败，请联系管理员查看服务端日志")
