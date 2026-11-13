"""数据源 API — 管理数据库连接。

从 backend/api/datasource.py 迁移而来。
表: adh_datasources
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.common.auth import get_current_user, get_workspace_id, require_admin
from backend.core.governed_query import NoIdentityError
from backend.modules.catalog.services.datasource_service import datasource_service

logger = logging.getLogger(__name__)
router = APIRouter()


# ── 端点 ──────────────────────────────────────────────────────────────


@router.get("/")
async def list_datasources(
    workspace_id: int = Query(0, description="工作空间 ID"),
    user: dict = Depends(get_current_user),
):
    """列出所有数据源。"""
    return await datasource_service.list_datasources(workspace_id, user_id=user.get("user_id") or 0)


@router.get("/authorized")
async def list_authorized_datasources(
    workspace_id: int = Query(0, description="工作空间 ID（可选，0=个人域）"),
    user: dict = Depends(get_current_user),
):
    """列出当前用户角色授权的数据源（聊天选择器用）。

    纯角色裁决：数据源可用集=用户角色授权；空授权 fail-closed 返回空列表。
    必须定义在 /{ds_id} 之前，否则 "authorized" 会被当作 ds_id 解析(422)。
    缓存 60s（角色授权变更时由权限管理端失效）。
    """
    from backend.common.ttl_cache import datasource_cache
    cache_key = f"auth_ds:{user.get('user_id')}:{workspace_id}"
    cached = datasource_cache.get(cache_key)
    if cached is not None:
        return cached
    result = await datasource_service.list_authorized_datasources(
        user.get("user_id") or 0, workspace_id)
    datasource_cache.set(cache_key, result)
    return result


@router.get("/authorized-sites")
async def list_authorized_sites(
    workspace_id: int = Query(0, description="工作空间 ID（可选，0=个人域）"),
    user: dict = Depends(get_current_user),
):
    """当前用户可查站点（聊天站点选择器用）——**角色数据源授权的派生视图**。

    不构成第二套「谁能查什么」裁决：可查站点 = 角色已授权数据源携带的 site_code
    （见 waker-datasource-domain.md §1）；空授权 fail-closed 返回空列表。
    聚合态（全部站点）仅在可查站点数 ≥2 时提供。只回业务名，不含 id 之外的物理信息。

    必须定义在 /{ds_id} 之前，否则 "sites"/"authorized-sites" 会被当作 ds_id 解析(422)。
    """
    from backend.common.ttl_cache import datasource_cache
    from backend.core.site_service import authorized_sites

    # 缓存**整个响应体**而不是内层清单：只缓存 list 会让命中/未命中两条路径的
    # 响应结构不一致（{items:[..]} vs [..]），前端 data.items 变 undefined → 选择器静默消失。
    cache_key = f"sites:auth:{user.get('user_id')}:{workspace_id}"
    cached = datasource_cache.get(cache_key)
    if isinstance(cached, dict) and "items" in cached:
        return cached
    result = authorized_sites(user.get("user_id") or 0, workspace_id)
    payload = {"items": result}
    datasource_cache.set(cache_key, payload)
    return payload


@router.get("/sites")
async def list_sites(
    include_aggregate: bool = Query(True, description="是否包含聚合态站点（全部站点）"),
    include_inactive: bool = Query(False, description="管理页需要看到停用行"),
    user: dict = Depends(get_current_user),
):
    """站点清单（adh_sites 事实源）：数据源归属选择器与聊天站点选择器的候选。"""
    from backend.core.site_service import list_sites as _list_sites

    return {"items": _list_sites(only_active=not include_inactive,
                                 include_aggregate=include_aggregate)}


@router.post("/sites")
async def create_site(
    req: dict,
    user: dict = Depends(require_admin),
):
    """新增/更新站点（管理员）。

    `field_values` 只在请求里带这个键时才写（没带 = 保持原映射），避免编辑名称/排序
    时把「站点→字段取值」抹掉（部分更新不抹配置）。
    """
    from backend.core.site_service import upsert_site

    try:
        return upsert_site(
            req.get("site_code") or "", req.get("display_name") or "",
            is_aggregate=int(req.get("is_aggregate") or 0),
            sort_order=int(req.get("sort_order") or 0),
            is_active=1 if req.get("is_active", 1) else 0,
            field_values=(req.get("field_values") if "field_values" in req else None))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/sites/{site_code}")
async def delete_site(
    site_code: str,
    user: dict = Depends(require_admin),
):
    """删除站点：仍被数据源/本体引用时拒绝（不级联删部署拓扑）。"""
    from backend.core.site_service import delete_site as _delete_site

    try:
        return _delete_site(site_code)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("/{ds_id}")
async def get_datasource(
    ds_id: int,
    user: dict = Depends(get_current_user),
):
    """获取单个数据源（密码已脱敏）。"""
    ds = await datasource_service.get_datasource(ds_id)
    if not ds:
        raise HTTPException(status_code=404, detail="数据源不存在")
    return ds


@router.post("/")
async def create_datasource(
    req: dict,
    user: dict = Depends(require_admin),
):
    """创建数据源（需要管理员权限）。"""
    try:
        return await datasource_service.create_datasource(req, owner_id=user.get("user_id", 0))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{ds_id}")
async def update_datasource(
    ds_id: int,
    req: dict,
    user: dict = Depends(require_admin),
):
    """更新数据源（需要管理员权限）。"""
    try:
        return await datasource_service.update_datasource(ds_id, req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{ds_id}")
async def delete_datasource(
    ds_id: int,
    user: dict = Depends(require_admin),
):
    """删除数据源（需要管理员权限）。"""
    return await datasource_service.delete_datasource(ds_id)


@router.post("/{ds_id}/test")
async def test_connection(
    ds_id: int,
    user: dict = Depends(get_current_user),
):
    """测试数据源连接。"""
    result = await datasource_service.test_connection(ds_id)
    if not result["success"] and result["message"] == "数据源不存在":
        raise HTTPException(status_code=404, detail="数据源不存在")
    return result


@router.get("/{ds_id}/tables")
async def list_tables(
    ds_id: int,
    user: dict = Depends(get_current_user),
):
    """列出数据源中的表。"""
    try:
        return await datasource_service.list_tables(ds_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{ds_id}/tables/{table_name}/columns")
async def list_columns(
    ds_id: int,
    table_name: str,
    user: dict = Depends(get_current_user),
):
    """列出表的列信息。"""
    try:
        return await datasource_service.list_columns(ds_id, table_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{ds_id}/execute")
async def execute_sql(
    ds_id: int,
    req: dict,
    user: dict = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
):
    """数据源裸 SQL 试跑 —— 经统一治理取数口（与数据集/看板/语义查数/Playground 同一条链）。

    身份只信服务端 JWT，不接受 body 递交 user_id（伪造无效果）；无身份 fail-closed 403。
    先查数据源存在性是为了把“不存在”留在 404（其余 ValueError 属本次请求本身的问题，回 400）；
    服务层同样会再校验一次 —— 两处判定同一事实，不拿错因文案当判据。
    """
    sql = req.get("sql", "").strip()
    if not sql:
        raise HTTPException(status_code=400, detail="SQL 不能为空")
    if not await datasource_service.get_datasource_raw(ds_id):
        raise HTTPException(status_code=404, detail="数据源不存在")

    try:
        return await datasource_service.execute_sql(
            ds_id, sql,
            user_id=user.get("user_id") or 0,
            workspace_id=int(workspace_id or 0),
            username=user.get("username", "") or "")
    except NoIdentityError as e:
        raise HTTPException(status_code=403, detail=str(e) or "缺少可信用户身份, 拒绝取数")
    except PermissionError as e:
        # 与数据集/Playground 同一口径：闸门拦截 = 403（旧实现回 400，同一个拦因三个面三个码）
        raise HTTPException(status_code=403, detail=f"权限拦截: {e}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.warning("[datasources] execute failed ds_id=%s: %s", ds_id, e)
        raise HTTPException(status_code=400, detail="SQL 执行失败")
