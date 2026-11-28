"""Dashboard API -- CRUD for dashboards, charts, snapshots, and data sources.

Migrated from backend/api/dashboard.py. Uses service layer for all business logic.
"""

import logging

import pymysql
from fastapi import APIRouter, Depends, HTTPException, Query as QueryParam
from pydantic import BaseModel, Field, field_validator
from typing import Optional

from backend.common.auth import get_current_user, get_workspace_id, require_admin
from backend.core import viz_query_pool
from backend.core.governed_query import NoIdentityError
from backend.modules.viz.services.dashboard_refresh_service import dashboard_refresh_service
from backend.modules.viz.services.dashboard_service import (
    dashboard_service,
    chart_service,
    snapshot_service,
    build_visible_groups,
    check_dashboard_editable,
    chart_identity_from_user,
    reorder_groups,
    _DASHBOARD_PAGE_SIZE_MAX,
)

logger = logging.getLogger(__name__)
router = APIRouter()

#: 移动被拒时给用户的可读原因（不是内部枚举，直接进前端提示）
_MOVE_REASON_TEXT = {
    "boundary": "已经在首尾，无法继续移动",
    "pinned_zone": "默认看板（置顶区）与非置顶看板之间不能互换顺序",
    "not_found": "看板不在当前工作空间的清单里",
}


def _split_csv(value: Optional[str]) -> list:
    """逗号分隔的查询参数 → 去空去重的列表（tags / favorite_ids 共用，不各写一份解析）。"""
    if not value:
        return []
    out: list = []
    for part in str(value).split(","):
        s = part.strip()
        if s and s not in out:
            out.append(s)
    return out


def _require_editable(dashboard_id: int, user: dict) -> None:
    """写操作看板前的属主/权限把关(fail-closed): 仅 admin 或看板属主可写。

    角色授权(adh_role_dashboard_access)只授予「可看」; 系统分配给角色的看板
    不得被角色成员改写 —— 这里是写链路唯一裁决点(前端隐藏入口只是体验层)。
    """
    if not check_dashboard_editable(dashboard_id, user["user_id"], user.get("role") or ""):
        raise HTTPException(
            status_code=403,
            detail="仅看板属主或管理员可修改该看板（角色授予的可见权不含编辑权）",
        )


def _chart_identity(user: dict) -> dict:
    """图表写入口的可信身份（委托服务层唯一构造，字段名不得两处各写一套）。"""
    return chart_identity_from_user(user)


# ── Pydantic Models ─────────────────────────────────────────────────────────


class DashboardCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    layout: Optional[list] = []
    filters: Optional[dict] = {}
    params: Optional[list] = []
    tags: Optional[list] = []
    status: Optional[str] = "designing"
    is_public: bool = False
    is_default: bool = False
    carousel_interval: int = 0
    workspace_id: Optional[int] = 0


class DashboardUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    layout: Optional[list] = None
    filters: Optional[dict] = None
    params: Optional[list] = None
    tags: Optional[list] = None
    status: Optional[str] = None
    is_public: Optional[bool] = None
    is_default: Optional[bool] = None
    carousel_interval: Optional[int] = None


class ChartCreate(BaseModel):
    """新增图表。

    取数口径不再属于图表（2026-11-06 拍板）：图表只能挂角色授权清单内的数据集
    （source_type='dataset' + source_id）或声明式语义意图。口径收口在「数据集」页
    （有字段声明/认证/血缘/角色清单），看板只做可视化装配。
    `sql_query` 字段保留只为把旧客户端的提交送到服务层显式拒掉（400 + 出路），
    不静默丢弃后当保存成功；服务层 `assert_chart_data_source` 是唯一把关点。
    """
    name: str
    chart_type: str
    sql_query: Optional[str] = Field(None, deprecated=True,
                                    description="已下线：口径请在数据集页定义，图表只挂数据集")
    config: Optional[dict] = None
    position: Optional[dict] = None
    # 缺省不写通道名：未绑数据集时服端落成占位图，不默认长进已下线的 'query' 通道
    source_type: Optional[str] = None
    source_id: Optional[int] = None
    # Phase 5: 大屏语义化 — 存声明式 SemanticQuery（SQL 由语义层编译，非人写）
    semantic_query: Optional[dict] = None
    query_source: Optional[str] = "raw_sql"  # raw_sql | semantic
    # 模板导入的示例数据（{columns, rows}）：服务层校验仅允许 config.demoData=true 的图表写入
    data_cache: Optional[dict | str] = None
    # 双语覆盖层：{field: {locale: text}}；图表 name + config 里的 title/content/label 走这里
    i18n: Optional[dict] = None


class ChartUpdate(BaseModel):
    """图表局部更新（同 ChartCreate：`sql_query` 带内容到服务层即 400）。"""
    name: Optional[str] = None
    chart_type: Optional[str] = None
    sql_query: Optional[str] = Field(None, deprecated=True,
                                    description="已下线：口径请在数据集页定义，图表只挂数据集")
    config: Optional[dict] = None
    position: Optional[dict] = None
    source_type: Optional[str] = None
    source_id: Optional[int] = None
    semantic_query: Optional[dict] = None
    query_source: Optional[str] = None
    # 双语覆盖层（与 ChartCreate 同一契约；留空即清空某字段的英）
    i18n: Optional[dict] = None

    @field_validator("name", "chart_type", "source_type", "query_source")
    @classmethod
    def validate_required_metadata(cls, value):
        if value is None or not value.strip():
            raise ValueError("已提交的元数据字段不能为空")
        return value


class ChartBatchSave(BaseModel):
    """整板草稿提交：新增/修改/删除一次带上来，避开 N 次往返。

    added/changed 是裸结构（旧前端逐条保存的同状载荷），口径把关放在服务层：
    逐条预检不过则整笔中止并在 errors 里指名图表（不留半截草稿）。
    """
    added: Optional[list] = []
    changed: Optional[dict] = {}
    deleted: Optional[list] = []
    metadata: Optional[dict] = None


class DashboardRefreshConfigRequest(BaseModel):
    schedule_type: str = "disabled"
    interval_seconds: Optional[int] = None
    cron_expression: Optional[str] = None
    timezone: Optional[str] = "Asia/Shanghai"
    cache_ttl_seconds: Optional[int] = None


class DashboardRefreshToggleRequest(BaseModel):
    is_active: bool


class ChartRefreshRequest(BaseModel):
    """重查请求。

    **没有 `count_sql`**：旧版这个字段等于让前端递一条可执行 SQL（第二取数通道），
    行总数现在由服务端从数据集编译产物生成；分页只在数据集通道可用。
    分页只接受 `page_size`（每页行数，服务端硬上限 100）与 `page_num`（页号，从 1 起），
    不接受 offset —— 偏移量由服务端算，不让调用方决定扫到第几行。
    """
    params: Optional[dict] = {}
    page_size: Optional[int] = None
    page_num: Optional[int] = None


class LayoutItem(BaseModel):
    chart_id: int
    position: dict


class LayoutRequest(BaseModel):
    layouts: list[LayoutItem]


class ReorderRequest(BaseModel):
    orders: list[dict]


class MoveRequest(BaseModel):
    """上移/下移：-1 = 上移，1 = 下移（服务端在同口径排序里找相邻行交换 sort_order）。"""
    dashboard_id: int
    direction: int


class SetVisibleRolesRequest(BaseModel):
    role_ids: list[int]


# ── Dashboard Endpoints ─────────────────────────────────────────────────────


@router.get("/")
def list_dashboards_endpoint(
    user: dict = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
    search: str = QueryParam(""),
    status: Optional[str] = QueryParam(None),
    tags: Optional[str] = QueryParam(None),
    favorite_ids: Optional[str] = QueryParam(None),
    page: int = QueryParam(1, ge=1),
    page_size: Optional[int] = QueryParam(None, ge=1, le=_DASHBOARD_PAGE_SIZE_MAX),
):
    """List dashboards (workspace scoped, 按角色可见集过滤).

    两档**显式**返回形态（同一资源「管理清单要真分页、选择器要全量在手」）：
    - 传 `page_size` → 信封 {items,total,page,page_size}，筛选/搜索/状态/标签全部下推 SQL，
      `total` 是过滤后的 COUNT（系统配置-看板管理页用这档）；
    - 不传 `page_size` → 裸数组全量，编辑器顶栏切板与图表下钻链接选择器需要整份清单，
      这些面不挂分页条。
    """
    try:
        if page_size is None:
            return dashboard_service.list_dashboards(
                user["user_id"], workspace_id, user.get("role") or "")
        return dashboard_service.list_dashboards_page(
            user["user_id"], workspace_id, user.get("role") or "",
            search=search or "", status=(status or "").strip(),
            tags=_split_csv(tags), favorite_ids=_split_csv(favorite_ids),
            page=page, page_size=page_size)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("Failed to list dashboards")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tags")
def list_dashboard_tags_endpoint(
    user: dict = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
):
    """可见域内的看板标签并集（管理页标签候选；分页后前端不再持有全量，不能就地求并集）。

    路由必须注册在 `GET /{dashboard_id}` 之前，否则 "tags" 会被当成看板 id 解析。
    """
    try:
        return dashboard_service.list_dashboard_tags(
            user["user_id"], workspace_id, user.get("role") or "")
    except Exception as e:
        logger.exception("Failed to list dashboard tags")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/move")
def move_dashboard_endpoint(
    req: MoveRequest,
    user: dict = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
):
    """上移/下移：与清单排序口径下的相邻看板交换 sort_order（服务端定位，跨页也成立）。

    同样必须注册在 `/{dashboard_id}` 系列路由之前。写权限沿用唯一裁决点 `_require_editable`。
    """
    try:
        _require_editable(req.dashboard_id, user)
        result = dashboard_service.move_dashboard(
            user["user_id"], req.dashboard_id, req.direction, workspace_id, user.get("role") or "")
        if not result.get("moved"):
            raise HTTPException(status_code=409, detail=_MOVE_REASON_TEXT.get(result.get("reason"), "移动未完成"))
        return result
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("Failed to move dashboard")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/")
def create_dashboard_endpoint(
    req: DashboardCreate,
    user: dict = Depends(get_current_user),
):
    """Create a new dashboard."""
    try:
        did = dashboard_service.create_dashboard(req.model_dump(), user["user_id"])
        return {"id": did}
    except Exception as e:
        logger.exception("Failed to create dashboard")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/snapshots")
def list_snapshots_endpoint(
    days: int = QueryParam(7),
    user: dict = Depends(get_current_user),
):
    """Get recent chart snapshots from chat executions."""
    try:
        return snapshot_service.list_snapshots(user["user_id"], days)
    except Exception as e:
        logger.exception("Failed to list snapshots")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/snapshots/{snapshot_id}/data")
def get_snapshot_data_endpoint(
    snapshot_id: int,
    user: dict = Depends(get_current_user),
):
    """Get full snapshot data including data rows."""
    try:
        row = snapshot_service.get_snapshot_data(snapshot_id, user["user_id"])
        if not row:
            raise HTTPException(status_code=404, detail="快照不存在")
        return row
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get snapshot data")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reorder")
def reorder_dashboards_endpoint(
    req: ReorderRequest,
    user: dict = Depends(get_current_user),
):
    """Update sort_order for dashboards."""
    try:
        for item in req.orders:
            _require_editable(int(item.get("id") or 0), user)
        dashboard_service.reorder_dashboards(user["user_id"], req.orders)
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to reorder dashboards")
        raise HTTPException(status_code=500, detail=str(e))


# 旧版这两个入口服务的是“图表数据源 = 快照/保存查询”的退役模型（且 preview 会按
# source_id 直接回数据行）：图表取数已收口到角色授权内的数据集，入口一并删除，
# 不留“文档里还能选快照/保存查询”的第二口径。


# ── Dashboard Groups (仪表盘组/看板组合: 看板目录切换维度) ────────────


class GroupCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    # 缺省 None = 不动已有顺序（编辑名称/说明时前端不回传 sort，传 0 会把已排好的组合抹到首位）
    sort: Optional[int] = None


class GroupReorderRequest(BaseModel):
    orders: list[dict]


class GroupItemsRequest(BaseModel):
    dashboard_ids: list[int]


class GroupRolesRequest(BaseModel):
    role_ids: list[int]


def _group_payload(g: dict) -> dict:
    """组行 + 组内看板 id/角色 id（谓词在服务层 `group_members`，AI 工具与端点同一份）。"""
    from backend.modules.viz.services.dashboard_service import group_members
    return {**g, **group_members(g["id"])}


@router.get("/groups")
def list_groups_endpoint(user: dict = Depends(get_current_user)):
    """全部仪表盘组(管理端组合管理用; 含组内看板与绑定角色)."""
    from backend.modules.viz.services.dashboard_service import list_group_rows
    return [_group_payload(g) for g in list_group_rows()]


@router.get("/groups/visible")
def list_visible_groups_endpoint(user: dict = Depends(get_current_user)):
    """当前用户可见的仪表盘组(组按角色分配), 每组带 组内∩角色可见 的看板清单.

    可见但未入组的看板归"未分组"兜底组(id=0); 组只做编排不授予可见(fail-closed)。
    看板行携带卡片瀑布清单展示字段(description/updated_at/chart_count/preview/editable),
    预览只回几何矩形不含口径/SQL(护栏 §7)。
    性能: 可见性裁决实时(不进缓存), 卡片几何/分组结构走共享载荷缓存(写路径失效)。
    """
    return build_visible_groups(user["user_id"], user.get("role") or "")


@router.post("/groups")
def create_group_endpoint(req: GroupCreate, admin: dict = Depends(require_admin)):
    """新建仪表盘组(仅 admin). SQL 谓词在服务层，AI 工具 `manage_dashboard_groups` 调同一份。"""
    from backend.modules.viz.services.dashboard_service import create_group
    group_id = create_group(req.name, req.description or "", req.sort)
    return {"success": True, "id": group_id}


@router.put("/groups/{group_id}")
def update_group_endpoint(group_id: int, req: GroupCreate, admin: dict = Depends(require_admin)):
    """更新仪表盘组(仅 admin)."""
    from backend.modules.viz.services.dashboard_service import DashboardGroupNotFound, update_group
    try:
        update_group(group_id, req.name, req.description or "", req.sort)
    except DashboardGroupNotFound:
        raise HTTPException(status_code=404, detail="仪表盘组不存在")
    return {"success": True}


@router.post("/groups/reorder")
def reorder_groups_endpoint(req: GroupReorderRequest, admin: dict = Depends(require_admin)):
    """保存组合展示顺序(仅 admin, 看板组合卡上移/下移).

    只写 sort 列; 顺序是编排事实, 入库后浏览端下次拉目录即生效(载荷缓存已同批失效)。
    """
    try:
        reorder_groups(req.orders)
        return {"success": True, "count": len(req.orders or [])}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Failed to reorder dashboard groups")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/groups/{group_id}")
def delete_group_endpoint(group_id: int, admin: dict = Depends(require_admin)):
    """删除仪表盘组及其成员/角色绑定(仅 admin, 不删看板本身)."""
    from backend.modules.viz.services.dashboard_service import DashboardGroupNotFound, delete_group
    try:
        delete_group(group_id)
    except DashboardGroupNotFound:
        raise HTTPException(status_code=404, detail="仪表盘组不存在")
    return {"success": True}


@router.put("/groups/{group_id}/items")
def set_group_items_endpoint(group_id: int, req: GroupItemsRequest,
                             admin: dict = Depends(require_admin)):
    """全量替换组内看板(仅 admin)."""
    from backend.modules.viz.services.dashboard_service import (
        DashboardGroupNotFound, set_group_items)
    try:
        result = set_group_items(group_id, req.dashboard_ids)
    except DashboardGroupNotFound:
        raise HTTPException(status_code=404, detail="仪表盘组不存在")
    # dropped 显式回传（no-silent-degradation）：正常流程读口已过滤，非空说明有陈旧/越权引用被拒登记
    return {"success": True, "count": result["count"], "dropped": result["dropped"]}


@router.put("/groups/{group_id}/roles")
def set_group_roles_endpoint(group_id: int, req: GroupRolesRequest,
                             admin: dict = Depends(require_admin)):
    """全量替换组的角色绑定(仅 admin). 组按角色分配, 不授予看板可见性."""
    from backend.modules.viz.services.dashboard_service import (
        DashboardGroupNotFound, set_group_roles)
    try:
        count = set_group_roles(group_id, req.role_ids)
    except DashboardGroupNotFound:
        raise HTTPException(status_code=404, detail="仪表盘组不存在")
    return {"success": True, "count": count}


@router.get("/{dashboard_id}")
def get_dashboard_endpoint(
    dashboard_id: int,
    user: dict = Depends(get_current_user),
):
    """Get a dashboard with its charts (角色不可见 → 404, 不暴露存在性)."""
    try:
        dashboard = dashboard_service.get_dashboard(
            dashboard_id, user["user_id"], user.get("role") or "")
        if not dashboard:
            raise HTTPException(status_code=404, detail="Dashboard not found")
        return dashboard
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get dashboard")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{dashboard_id}")
def update_dashboard_endpoint(
    dashboard_id: int,
    req: DashboardUpdate,
    user: dict = Depends(get_current_user),
):
    """Update a dashboard."""
    try:
        _require_editable(dashboard_id, user)
        updated = dashboard_service.update_dashboard(
            dashboard_id, req.model_dump(exclude_none=True), user["user_id"],
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Dashboard not found or no changes")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to update dashboard")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{dashboard_id}")
def delete_dashboard_endpoint(
    dashboard_id: int,
    user: dict = Depends(get_current_user),
):
    """Delete a dashboard and its charts."""
    try:
        _require_editable(dashboard_id, user)
        deleted = dashboard_service.delete_dashboard(dashboard_id, user["user_id"])
        if not deleted:
            raise HTTPException(status_code=404, detail="Dashboard not found")
        # 组合成员清理与载荷失效都在服务层 delete_dashboard 里做（人/AI 同一条链，不在此补第二刀）
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to delete dashboard")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{dashboard_id}/copy")
def copy_dashboard_endpoint(
    dashboard_id: int,
    user: dict = Depends(get_current_user),
):
    """Copy a dashboard with all its charts."""
    try:
        new_dashboard = dashboard_service.copy_dashboard(dashboard_id, user["user_id"])
        if not new_dashboard:
            raise HTTPException(status_code=404, detail="仪表盘不存在")
        return new_dashboard
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to copy dashboard")
        raise HTTPException(status_code=500, detail=str(e))


# ── Dashboard Visibility Roles (看板可见角色配置, 双向入口的看板侧) ────


@router.get("/{dashboard_id}/visible-roles")
def get_visible_roles_endpoint(
    dashboard_id: int,
    user: dict = Depends(get_current_user),
):
    """获取可看到该看板的角色列表."""
    from backend.core.role_service import role_service
    return role_service.get_dashboard_roles(dashboard_id)


@router.put("/{dashboard_id}/visible-roles")
def set_visible_roles_endpoint(
    dashboard_id: int,
    req: SetVisibleRolesRequest,
    admin: dict = Depends(require_admin),
):
    """全量替换看板的可见角色(仅 admin). 空列表=一律不可见(fail-closed)."""
    from backend.core.role_service import role_service
    if not dashboard_service.get_dashboard(
            dashboard_id, admin["user_id"], admin.get("role") or ""):
        raise HTTPException(status_code=404, detail="Dashboard not found")
    ok = role_service.set_dashboard_roles(dashboard_id, req.role_ids)
    return {"success": ok, "count": len(set(req.role_ids or []))}


# ── Chart Endpoints ─────────────────────────────────────────────────────────


@router.post("/{dashboard_id}/charts")
def add_chart_endpoint(
    dashboard_id: int,
    req: ChartCreate,
    user: dict = Depends(get_current_user),
):
    """Add a chart to a dashboard.

    口径把关在服务层 `create_chart`（数据集必须在角色授权清单内且可执行）。
    """
    try:
        _require_editable(dashboard_id, user)
        cid = chart_service.create_chart(dashboard_id, req.model_dump(),
                                        identity=_chart_identity(user))
        return {"id": cid}
    except HTTPException:
        raise
    except ValueError as e:
        # 示例数据形状/权限不合规是调用方问题, 回 400 + 原因(不笼统 500)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Failed to add chart")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{dashboard_id}/charts/{chart_id}")
def update_chart_endpoint(
    dashboard_id: int,
    chart_id: int,
    req: ChartUpdate,
    user: dict = Depends(get_current_user),
):
    """Update a chart within a dashboard.

    patch 只带局部字段时由服务层回读拼出有效口径后把关（含旧存量 SQL 图的收编拒）。
    """
    try:
        _require_editable(dashboard_id, user)
        patch = req.model_dump(exclude_unset=True)
        updated = chart_service.update_chart(
            dashboard_id, chart_id, patch, identity=_chart_identity(user),
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Chart not found")
        return {"success": True}
    except HTTPException:
        raise
    except ValueError as e:
        # 数据集不在可执行源上/示例数据不合规 = 调用方问题, 回 400 + 可读原因
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Failed to update chart")
        raise HTTPException(status_code=500, detail="图表配置保存失败，请重试") from e


@router.delete("/{dashboard_id}/charts/{chart_id}")
def delete_chart_endpoint(
    dashboard_id: int,
    chart_id: int,
    user: dict = Depends(get_current_user),
):
    """Remove a chart from a dashboard."""
    try:
        _require_editable(dashboard_id, user)
        deleted = chart_service.delete_chart(dashboard_id, chart_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Chart not found")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to delete chart")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{dashboard_id}/batch")
def batch_save_charts_endpoint(
    dashboard_id: int,
    req: ChartBatchSave,
    user: dict = Depends(get_current_user),
):
    """一次提交整板草稿（看板元数据 + 图表新增/修改/删除）。

    先全量预检再应用：预检不过则一笔不写，避开旧串行保存“存了一半失败”的半截状态；
    失败项逐条带可读原因返回，前端能指名是哪个图表没存上（不笼统报“保存未完成”）。
    """
    try:
        _require_editable(dashboard_id, user)
        errors: list = []
        ident = _chart_identity(user)
        saved_metadata = False
        if req.metadata:
            saved_metadata = dashboard_service.update_dashboard(
                dashboard_id, req.metadata, user["user_id"],
            )
            if not saved_metadata and req.metadata:
                errors.append("看板元数据未写入（无变更或看板不存在）")
        outcome = chart_service.batch_save(dashboard_id, req.added or [], req.changed or {},
                                          req.deleted or [], identity=ident)
        errors.extend(outcome["errors"])
        return {
            "success": bool(outcome["ok"]) and not errors,
            "results": outcome["results"],
            "created": outcome["created"],
            "metadata_saved": saved_metadata,
            "errors": errors,
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Failed to batch save dashboard draft")
        raise HTTPException(status_code=500, detail="看板保存失败，草稿已保留，可重试") from e


@router.post("/{dashboard_id}/charts/{chart_id}/refresh")
async def refresh_chart_endpoint(
    dashboard_id: int,
    chart_id: int,
    req: ChartRefreshRequest = ChartRefreshRequest(),
    user: dict = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
):
    """按查看者身份重查一个图表（只经数据集/声明式语义/AS-BOT 设计三条通道）。

    取数走**看板专用有界通道**（`core/viz_query_pool`）：端点是 `async def`，await
    期间不占 anyio 的全应用共享线程池；通道满/排队超预算 **显式 503**，不回"空结果"、
    也不无限期挂着用户的点击（`no-silent-degradation.md`）。
    """
    try:
        result = await viz_query_pool.run_async(
            chart_service.refresh_chart,
            dashboard_id, chart_id,
            params=req.params,
            page_size=req.page_size,
            page_num=req.page_num,
            user_id=user.get("user_id") or 0,
            workspace_id=workspace_id,
            username=user.get("username", "") or "",
            persist_cache=False,
        )
        return result
    except (viz_query_pool.VizQueryBusy, viz_query_pool.VizQueryExpired) as e:
        raise HTTPException(status_code=503, detail=str(e), headers={"Retry-After": "5"})
    except NoIdentityError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except pymysql.Error as e:
        raise HTTPException(status_code=400, detail=f"SQL 执行失败: {e}")
    except Exception as e:
        logger.exception("Failed to refresh chart")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{dashboard_id}/refresh", status_code=202)
async def refresh_all_charts_endpoint(
    dashboard_id: int,
    user: dict = Depends(get_current_user),
):
    """管理动作：整板刷新进入 Celery 专用队列，TTL 内直接命中缓存。"""
    try:
        return dashboard_refresh_service.request_refresh(dashboard_id, user)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/{dashboard_id}/refresh-config")
def get_refresh_config_endpoint(dashboard_id: int, user: dict = Depends(get_current_user)):
    from backend.modules.viz.services.dashboard_refresh_service import assert_refresh_manageable
    try:
        assert_refresh_manageable(dashboard_id, user)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return dashboard_refresh_service.get_config(dashboard_id)


@router.put("/{dashboard_id}/refresh-config")
def save_refresh_config_endpoint(dashboard_id: int, req: DashboardRefreshConfigRequest,
                                  user: dict = Depends(get_current_user)):
    try:
        return dashboard_refresh_service.save_config(dashboard_id, req.model_dump(), user)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/{dashboard_id}/refresh-config")
def toggle_refresh_config_endpoint(dashboard_id: int, req: DashboardRefreshToggleRequest,
                                    user: dict = Depends(get_current_user)):
    try:
        return dashboard_refresh_service.toggle_config(dashboard_id, req.is_active, user)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{dashboard_id}/refresh-runs/{run_id}")
def get_refresh_run_endpoint(dashboard_id: int, run_id: int,
                             user: dict = Depends(get_current_user)):
    try:
        run = dashboard_refresh_service.get_run(run_id, dashboard_id, user)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    if not run:
        raise HTTPException(status_code=404, detail="刷新实例不存在")
    return run


@router.put("/{dashboard_id}/layout")
def update_layout_endpoint(
    dashboard_id: int,
    req: dict,
    user: dict = Depends(get_current_user),
):
    """Batch update chart positions (layout save)."""
    try:
        _require_editable(dashboard_id, user)
        layouts = req.get("layouts", [])
        chart_service.update_layout(dashboard_id, layouts)
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to update layout")
        raise HTTPException(status_code=500, detail=str(e))
