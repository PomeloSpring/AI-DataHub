# ChatBI 前端架构分析（看板编辑器）

> 最近更新：2026-10-22（随「看板编辑器交互与画布优化」批次 1–8 落地同步）
> 范围：`pages/DashboardEditor.tsx` + `components/editor/*` + `hooks/useCanvasInteraction.ts`
> + `lib/dashboardDesign.ts` + `lib/dashboardPageLayout.ts` + `lib/canvasTheme.ts`
>
> ⚠️ 本文件此前描述的是重构前的形态（1477 行巨型文件、无 undo/redo、无 rAF 节流、
> 双编辑模式并存），这些均已不成立。保留历史结论只会误导后续改动，故整体重写。

---

## 一、当前结构（单一职责拆分已完成）

```
pages/DashboardEditor.tsx        工作台装配：状态编排、DndContext、对话框、快捷键
├─ components/editor/
│   ├─ EditorToolbar.tsx         工具栏（模式/断点/撤销重做/选择·平移·网格·标尺·试运行/缩放/保存）
│   ├─ ComponentLibrary.tsx      左侧字模库：字模 / 图表 / 数据集 / 控件 / 图层
│   ├─ PageLayoutEditor.tsx      页面（网格）模式的编辑画布
│   ├─ SelectionOverlay.tsx      选中态覆盖层（工具条 + 缩放手柄），渲染在缩放层之外
│   ├─ CanvasHint.tsx            左下角操作提示，8s 后自动折叠
│   ├─ ParamSelect.tsx           参数名选择框 + 控件选项编辑器
│   └─ PropertyPanel.tsx         右侧属性面板（控件的唯一配置入口）
├─ hooks/
│   ├─ useCanvasInteraction.ts   画布内移动/缩放/平移/缩放视口（Pointer Events + rAF）
│   ├─ useEditorCharts.ts        草稿 + 历史(undo/redo) + 多选 + 保存
│   └─ useOverlayPosition.ts     视口尺寸测量（供覆盖层换算）
└─ lib/
    ├─ dashboardDesign.ts        字模解析、吸附、尺寸/最小值事实源（minSizeOf）
    ├─ dashboardPageLayout.ts    网格布局数学（v2 行单位 + v1 等值换算）
    ├─ canvasTheme.ts            画布与覆盖层唯一配色源 + 坐标换算
    ├─ editorDrag.ts             库→画布拖拽载荷契约
    └─ mergeRefs.ts              React 18 无 useMergeRefs 的替代
```

两套布局模型（页面网格 / 大屏像素）是**产品能力**，不是重复实现：它们共用
`DashboardChartCard`、`canvasTheme`、`SelectionOverlay`、`useCanvasInteraction`。

---

## 二、已固化的契约（改动前先读）

### 1. 尺寸下限只在交互层
- `chartRect()` / `storedRect()` **不再**在读取时把已存值抬到下限（否则小尺寸任何途径都改不小）。
  只有非法值（≤0 / NaN）回落默认尺寸。
- 交互下限的唯一事实源是 `minSizeOf(chartType)`（`lib/dashboardDesign.ts`），按控件类型分级。
- 网格模式另有 `pageMinHeight()`（复用 `minSizeOf`）+ `movePageItem()` 写入时把关。
- 属性面板必须把下限**显示出来**（"本类型最小 …，小于此值会自动抬回"），不得静默夹值。

### 2. 网格行单位有版本
- `PAGE_ROW = 4`（v2），`pageLayout.version` 决定某图表的行单位按 v1(8) 还是 v2(4) 解释。
- v1 数据在读取时按像素等值换算（`upgradeRows`），保存后统一落 `PAGE_LAYOUT_VERSION`。
- 改 `PAGE_ROW` 或换算公式必须同步 `lib/__tests__/dashboardPageLayout.test.ts` 的偏差断言。

### 3. 编辑态控件默认 inert
- `DashboardChartCard` 在 `draft && widget && interactive === false` 时给内容层加 `inert`，
  使 `pointerdown` 落到卡片容器（否则表单元素吃掉事件，卡片既选不中也拖不动）。
- 「试运行」开关（工具栏 / 快捷键 `I`，持久化 `adh-editor-interact-mode`）才放开交互。
- 控件分支内**禁止**再写 `if (draft) return` 这类静默吞输入的守卫。

### 4. 覆盖层不得进入缩放上下文
- 选中工具条与缩放手柄由 `SelectionOverlay` 渲染在视口层，位置由
  `canvasRectToScreen(rect, surface, viewport, scale, pan)` 算出，与 `CanvasRulers` 同式。
- 禁止再用 `transform: scale(1/scale)` 反向补偿（小缩放下会放大数倍压住卡片标题）。
- `handlePanStart` 的排除选择器必须包含 `[data-selection-overlay]` / `[role="slider"]`，
  否则平移手势会在 capture 阶段 `stopPropagation` 抢走手柄的 pointerdown（已踩过）。

### 5. 画布配色只有一个源
- 一律取 `lib/canvasTheme.ts` 的 `CANVAS_COLORS`，全部走 `hsl(var(--*))`。
- `lib/__tests__/canvasTheme.test.ts` 有静态护栏：编辑器文件出现裸 hex / fuchsia / blue-500
  或 `scale(1/scale)` 即失败。

### 6. 控件配置单入口
- `PropertyPanel` 是控件 `config` 的唯一编辑面；`ChartConfigPanel` 对 `widget_` 直接返回 null。
- 渲染层（`DashboardChart` 控件分支）读取的每个字段都必须有面板入口，否则即"想配配不了"。
- 新增 `widgetStyle` 旋钮必须三方同步：`WIDGET_KEYS` 白名单 + `DashboardChart` 消费 + 用例。

### 7. 资源引用显示用 name、选择用选择框
- `paramKey` / `bind_param` 从看板 `page_params` 中选（可就地新建），引用未定义参数必须红标提醒。
- 导出按钮的 `targetChartId` 用图表 name 选择；未配置时按钮 disabled 并给出可见原因，
  **禁止**回退到"第一个非控件图表"。

### 8. 保存走整板一次提交
- `PUT /dashboard/{id}/batch`：先全量预检（缺名称/引用了已不存在的图表即整体中止，一笔不写），
  再逐条应用并返回 `results[{op,index,chart_id,ok,error}]`。
- 前端按条目定位失败项并指名图表 name；部分成功也要把已落库的条目从草稿剔掉并建立新基线
  （否则重试会重复创建）。元数据库连接是 autocommit，不要假设有跨语句事务回滚。

---

## 三、仍存在的已知限制（不是新缺陷）

| 项 | 现状 | 影响 |
|---|---|---|
| 草稿状态实现 | `useEditorCharts` 用 ref + 强制渲染；卡片级 `memo` 因 chart 对象身份稳定而生效 | 属性面板输入不会重建其他图表的 G2 实例（已有用例锁定），但编辑器整体仍会重渲染 |
| 多选拖拽 | 支持 Shift/Ctrl 点选、批量删除、方向键批量微调、批量对齐缩放 | 框选与"拖动一个同时移动整组"未实现 |
| 图层叠放 | 仅大屏模式有意义；网格模式由行列决定层叠 | 图层面板在小屏模式下禁用上下移并给出说明 |
| 离开页面拦截 | 仍用原生 `window.confirm` | popstate 必须同步判定，改异步会丢失阻止导航能力；应用内破坏性操作已全部改 Dialog |
| 控件类型覆盖 | 图表卡与控件卡各自只能在同类内换型 | 跨类改型会被拒并提示，需删除后重新拖入 |

---

## 四、回归门禁

| 触碰范围 | 必跑 |
|---|---|
| `dashboardDesign.ts` / `DashboardChart.tsx` / `DashboardCanvas.tsx` / `useChartDesign.ts` | `ChartPickerTheme.test.tsx`、`DashboardChartSpec.test.ts`、`lib/__tests__/dashboardDesign.test.ts` |
| `canvasTheme.ts` / `SelectionOverlay.tsx` / `PageLayoutEditor.tsx` / `EditorToolbar.tsx` | `lib/__tests__/canvasTheme.test.ts`、`components/__tests__/DashboardCanvas.interaction.test.tsx` |
| `dashboardPageLayout.ts` / 行单位与版本换算 | `lib/__tests__/dashboardPageLayout.test.ts` |
| 控件配置面 / 参数选择化 | `components/__tests__/PropertyPanelWidget.test.tsx`、`components/__tests__/ParamSelect.test.tsx` |
| 保存链路（前端 `useEditorCharts` 或后端 batch） | `tests/test_dashboard_batch_save.py`、`tests/test_dashboard_design.py`、`tests/test_dashboard_chart_update.py` |
| 任何前端批次 | `npx tsc --noEmit`（根 tsconfig 口径，含 `@/*` 别名）零错误 + `npx vitest run` 全绿 |
