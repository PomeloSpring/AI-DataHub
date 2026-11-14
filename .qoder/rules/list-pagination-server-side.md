---
trigger: always_on
description: 带分页控件的列表/数据集必须服务端真分页（COUNT 定总数 + LIMIT/OFFSET 取页、筛选与搜索下推 SQL），禁止"拉全量到前端再切片"的假分页；选择器候选等确需全量在手的场景是显式例外。
---

# 列表真分页（服务端分页 ≠ 前端切片）— 强制规范

> 页面上挂着分页条（`PaginationBar`）就等于对用户承诺"翻到第 N 页拿的是第 N 页的数据、共 X 条是真的 X 条"。
> 把整张表拉到浏览器再 `slice()` 出来是**假分页**：总数随每次加载抖动、数据一多先卡死后崩、翻页各拿各的全量。
> 视同缺陷，必须修而非妥协。典型翻车现场：`/api/vis-library/components?include_inactive=true&include_disabled=true` 管理页曾前端切片。

## 1. 判定：什么清单必须服务端真分页
- **凡渲染分页条的列表/数据集**（管理清单、日志、审计、资产目录、字模库、看板/数据集清单等），分页一律在**服务端**做。
- 判据只有一条：**结果集会不会超过一页**。会 → 服务端分页；确小且需全量在手（下拉候选、选择器、兼容过滤面板）→ 才允许一次性取全（见 §5 例外），且**不得挂分页条**。
- `useClientPagination` 的定位不变（仅用于"后端一次性返回全量、量中小"的配置类选择面）；**不得**把它贴在带分页条的管理清单上当真分页用。

## 2. 后端契约（服务端说了算）
- 分页入参：`page`（≥1）、`page_size`（每页行数，服务端 `Query(ge=1, le=上限)` **夹死**，越界不照单全收）；可选搜索 `q` / 各筛选参数。
- 返回契约固定 `{items, total, page, page_size}`（与 `content/articles`、`dataset` 分页同源）：
  - **`total` = 服务端 `SELECT COUNT(*)`（过滤后的全量行数），不是当前页 `items.length`**；把 total 写成本页长度即假分页。
  - 当前页用 `LIMIT %s OFFSET %s` 取，**偏移量由服务端按 `(page-1)*page_size` 算**，不接受调用方直接递交 `offset`。
  - **筛选、搜索、状态两档（如 `include_inactive`/`include_disabled`）全部下推 SQL**，不得"服务端返回、前端再 filter 再切片"。
- 排序必须**确定且含唯一 tiebreaker**（如 `ORDER BY sort_order, is_builtin DESC, id ASC`，末尾 `id` 兜底）；否则 `LIMIT/OFFSET` 相邻页会重行/漏行。数据行型通道若正文缺顶层 `ORDER BY`，按 `security-guardrails`/`dataset` 口径显式回 `stable_order=false` 提示，不替业务猜排序列。
- 分页方法（如 `list_components_page`）与不分页的全量方法（如 `list_components`）**共用同一份筛选/排序构造**（`_build_filters`/`_ORDER_BY`），禁止两套谓词漂移。

## 3. 前端契约（驱动自服务端，不自算总数）
- 分页条的 `total` 用**服务端返回的 total**；翻页/换页大小/切分类/改搜索 → **带 `page`/`page_size`/筛选参数重新请求**，渲染返回的 `items`，不得在本地对全量再 `filter+slice`。
- 搜索框**防抖**（停手再发请求）并**改筛选即回到第 1 页**（停在旧页码 + 新关键词会拿到错位的空清单）。
- **档位候选必须 ≤ 服务端 `page_size` 上限**（前端给 100、服务端 `le=50` 只会换 422 = 死选项）；改 `page_size` 时同步 `setPage(1)`。
- 删改后重取当前页；若因删除导致当前页越界（末页删空），收敛到最后一个有效页重取，不停在空白页。

## 4. 禁止的反模式（假分页速查）
- ❌ 带分页条的清单接口无 `LIMIT/OFFSET`，一次返回全量，前端 `useClientPagination` 切片充数；
- ❌ `total` 取当前页条数（或 `items.length`）而非服务端过滤后 COUNT；
- ❌ 服务端把 `include_*`/状态全放行、靠前端 filter 出可见集（口径分裂，且全量泄露到浏览器）；
- ❌ 排序无唯一 tiebreaker 的深分页（翻两页看到同一批行、漏掉另一些行）；
- ❌ 前端档位/参数越过服务端边界，或让调用方直接递交 `offset`/`count_sql`（第二统计通道，见 `security-guardrails`）。

## 5. 显式例外（不静默）
- **选择器/下拉/兼容过滤面板需要全量在手**（如编辑器字模面板 `useVisLibrary`、图表兼容筛选）：允许同一接口**不传 `page_size` 时返回全量数组**、前端本地过滤——这类面**不挂分页条**。同一资源"管理清单要真分页、选择面板要全量"是**两档不同用途**，用"是否传 `page_size`"显式区分返回形态（信封 vs 数组），不是同一请求两种静默行为。
- **数据行型取数分页**（表值图/看板/报表的 `page_size`/`page_num`）走 `dataset_service`/`semantics.execute` 治理口（`security-guardrails` §5），本规则只收敛**元数据/配置清单**的分页，不改变那条主链路的围栏。

## 6. 落地与回归
- 改这类接口/页面前先读服务端 `Query` 的 `ge/le` 与 service 层 `min(...)` 钳制，再定前端档位（"入口判据不得窄于/宽于服务端能力"）。
- 后端用例必须锁：`total` 来自 COUNT（≠ 页长）、`LIMIT/OFFSET` 由服务端算、筛选/搜索**下推 SQL**、`page_size` 越界被夹、排序含唯一 tiebreaker（见 `tests/test_vis_library_pagination.py`）。
- 前端用例必须锁：翻页带 `page`/`page_size` 重新请求、总数渲染自服务端 total、搜索防抖回第 1 页、删末页收敛不空白（见 `pages/admin/__tests__/VisLibraryPage.test.tsx` 分页桩）。
- 触碰 `useClientPagination.ts` / `PaginationBar.tsx` / 某清单接口分页逻辑时，按对应域规则补跑回归（字模库另按 `ui-vis-library-style.md` §5 门禁）。
- 与 `no-silent-degradation.md`：把全量拉到前端"看着能用"正是被禁止的静默掩盖——分页错配必须显式（真分页或明确的全量选择面）。
