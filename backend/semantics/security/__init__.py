"""治理强制层（Phase 6.3）—— RLS/mask 视图化强制。

- `PolicyViewManager`：policy 裁决产物按会话指纹注册过滤视图（plan 期强制、
  天然覆盖 JOIN 两侧）；
- `filtered_view_sql` / `rewrite_to_views`：过滤视图体生成 + 表引用视图化改写
  （别名保留、无非法双重别名 —— 护栏 §4 行为在本层锁定）；
- `mask_expr`：mask 表达式按执行方言生成（裁决产物只带 mask 类型）。

分层：L1（semantics），只依赖 execution 会话池与 common。
"""

from backend.semantics.security.masking import is_known_mask, mask_expr
from backend.semantics.security.policy_views import (
    VIEW_PREFIX,
    PolicyViewManager,
    filtered_view_sql,
    rewrite_to_views,
    view_name,
)

__all__ = [
    "VIEW_PREFIX",
    "PolicyViewManager",
    "filtered_view_sql",
    "is_known_mask",
    "mask_expr",
    "rewrite_to_views",
    "view_name",
]
