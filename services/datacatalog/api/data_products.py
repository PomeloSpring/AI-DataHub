"""Data Products API —— 数据产品（L1 治理身份）清单 / 详情 / 手工认领。

挂载在 /api/catalog/data-products：
    GET  /                      数据产品清单（支持数据源/状态/关键词筛选）
    GET  /{product_name}        详情（含 schema 版本历史）
    POST /                      手工认领/登记一个数据产品（治理动作，需管理员）

UI 资源规范：返回体带 name/display_name 供展示，内部 id 只作次要信息；
调用方选择产品时用 product_name（稳定键），不手填内部 id。
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from services.shared.common.auth import get_current_user, require_admin
from services.datacatalog.services import data_product_service as dps

router = APIRouter()


@router.get("/")
def list_data_products(
    datasource_name: str = Query("", description="按数据源名筛选（稳定关联键）"),
    status: str = Query("", description="draft|certified|deprecated|retired"),
    keyword: str = Query("", description="按产品名/显示名/物理表模糊搜"),
    limit: int = Query(200, ge=1, le=1000),
    user: dict = Depends(get_current_user),
):
    """数据产品清单。普通读放开，返回 name 供 UI 展示。"""
    return dps.list_products(
        datasource_name=datasource_name, status=status, keyword=keyword, limit=limit)


@router.get("/{product_name}")
def get_data_product(product_name: str, user: dict = Depends(get_current_user)):
    """产品详情 + schema 版本历史（供影响分析与回溯）。"""
    product = dps.get_product(product_name)
    if not product:
        raise HTTPException(status_code=404, detail=f"数据产品不存在: {product_name}")
    for k in ("created_at", "updated_at"):
        if hasattr(product.get(k), "isoformat"):
            product[k] = product[k].isoformat()
    return {
        "product": product,
        "versions": dps.list_versions(product_name),
    }


@router.post("/")
def register_data_product(req: dict, user: dict = Depends(require_admin)):
    """手工认领/登记数据产品（把已有裸表登记为可被本体引用的托管表）。

    必填 product_name（稳定身份，不含内部 id）；columns 用于算 schema 指纹。
    破坏性 schema 变更会在响应 warnings 里显式给出（不静默）。
    """
    name = (req.get("product_name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="product_name 必填（数据产品的稳定身份）")
    try:
        result = dps.register_product(
            req, columns=req.get("columns") or [],
            created_by=f"user:{user.get('user_id')}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"登记失败: {e}")
    return result
