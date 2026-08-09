# DataFoundry UI Design System

> 一套基于 React + Tailwind CSS 4 的现代工作台 UI 设计体系，适用于数据平台、AI Agent 界面、Dashboard 等场景。

---

## 技术栈

| 层面 | 选型 |
|------|------|
| 框架 | React 19 + Next.js 15 (App Router, `"use client"`) |
| 样式 | Tailwind CSS 4.1 + `@tailwindcss/postcss` |
| 主题桥接 | `@theme inline` 将 CSS 变量注入 Tailwind theme |
| 图标 | 内联 SVG 组件（无外部图标库） |
| 终端 UI | React + Ink 7（可选） |

---

## 1. 设计令牌（Design Tokens）

所有令牌定义为 CSS 自定义属性（`globals.css` 的 `:root` 下），通过 `@theme inline` 桥接到 Tailwind，支持 `bg-surface`、`text-muted` 等类名直接使用。

### 1.1 色彩系统

#### 中性色（工作台基调）

```css
:root {
  --primary: #0d0d0d;          /* 近黑，主操作色 */
  --primary-light: #3a3a3a;    /* 深灰，hover 态 */
  --accent: #737373;            /* 中灰，辅助强调 */
  --surface: #ffffff;           /* 纯白，面板背景 */
  --surface-subtle: #f7f7f8;   /* 浅灰白，卡片/次级背景 */
  --border: #ececf0;            /* 边框色 */
  --foreground: #0d0d0d;        /* 主文字色 */
}
```

#### 文字层级

```css
:root {
  --text-secondary: #4d4d4d;   /* 次级文字 */
  --text-tertiary: #8a8a99;    /* 三级文字/辅助说明 */
}
```

#### 语义步骤色（低饱和度宝石色调）

用于不同类型的操作步骤/状态着色，色彩克制、不刺眼：

```css
:root {
  --step-inspect: #4d6f96;     /* 钢蓝 — 检查 */
  --step-query: #74628f;       /* 暗紫 — 查询 */
  --step-transform: #3f827f;   /* 青绿 — 转换 */
  --step-fetch: #3f769b;       /* 海蓝 — 获取 */
  --step-visualize: #635c8e;   /* 石板紫 — 可视化 */
  --step-knowledge: #3f7480;   /* 深青 — 知识 */
  --step-success: #3f7d63;     /* 森绿 — 成功 */
  --step-warning: #9a6a30;     /* 琥珀 — 警告 */
  --step-error: #a24f49;       /* 砖红 — 错误 */
}
```

#### 桥接到 Tailwind

```css
@theme inline {
  --color-primary: var(--primary);
  --color-primary-light: var(--primary-light);
  --color-surface: var(--surface);
  --color-surface-subtle: var(--surface-subtle);
  --color-border: var(--border);
  --color-foreground: var(--foreground);
  --color-muted: var(--text-secondary);
  --color-step-inspect: var(--step-inspect);
  /* ... 其他步骤色同理 */
}
```

使用方式：`bg-step-inspect/10`、`text-step-error`、`border-step-success/30`

---

## 2. 字体排版

### 2.1 字体栈

```css
:root {
  --font-inter: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI',
    'PingFang SC', 'Microsoft YaHei', 'Noto Sans CJK SC',
    'WenQuanYi Micro Hei', sans-serif;

  --font-fira-code: 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas',
    'Liberation Mono', monospace;
}
```

### 2.2 字号规范

| 尺寸 | Tailwind 类 | 用途 |
|------|-------------|------|
| 10px | `text-[10px]` | 徽标数字、时间戳 |
| 11px | `text-[11px]` | 区域标签（大写 + `tracking-[0.08em]`）、指标标签 |
| 12px | `text-xs` | 次级正文、描述文字、表单提示 |
| 14px | `text-sm` | 主体文字、面板标题（`font-semibold`） |
| 16px | `text-base` | 正文（较少使用） |
| 18px | `text-lg` | 指标数值（`font-semibold tabular`） |
| 20px | `text-xl` | 卡片标题（`font-semibold tracking-tight`） |
| 24px | `text-2xl` | KPI 大数字（`font-semibold tracking-tight`） |

### 2.3 数字对齐

```html
<span class="tabular">1,234</span>
```

```css
.tabular { font-variant-numeric: tabular-nums; }
```

---

## 3. 间距与圆角

### 3.1 间距体系

遵循 Tailwind 默认 4px 网格，常用组合：

| 场景 | 间距 |
|------|------|
| 紧凑内边距（按钮、标签） | `px-2.5 py-0.5` / `px-3 py-1.5` |
| 面板内边距 | `p-3` / `p-4` |
| 卡片间间距 | `gap-2` / `gap-3` |
| 区块间距 | `gap-4` / `gap-6` |

### 3.2 圆角规范

| 元素 | 圆角 |
|------|------|
| 按钮、输入框、标签 | `rounded-lg` (8px) |
| 面板、卡片 | `rounded-xl` (12px) |
| 弹窗/模态框 | `rounded-2xl` (16px) |
| 药丸/徽标 | `rounded-full` |

---

## 4. 组件模式

### 4.1 按钮

```tsx
// 主按钮
const btnPrimary = "rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-white hover:bg-primary-light cursor-pointer transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/20"

// 次按钮
const btnSecondary = "rounded-lg border border-border bg-surface px-3 py-1.5 text-xs font-medium text-muted hover:bg-surface-subtle cursor-pointer transition-colors duration-200"

// 幽灵按钮
const btnGhost = "rounded-lg px-2.5 py-1 text-xs font-medium text-muted hover:bg-surface-subtle cursor-pointer transition-colors duration-200"

// 认证按钮（全宽）
const btnAuth = "h-10 w-full rounded-md bg-primary text-white font-semibold cursor-pointer transition-colors hover:bg-primary-light"
```

### 4.2 输入框

```tsx
// 标准输入
const inputStandard = "h-10 w-full rounded-md border border-border bg-surface px-3 text-sm focus:border-primary focus:ring-2 focus:ring-primary/10 outline-none"

// 搜索/过滤输入
const inputSearch = "h-8 rounded-lg border border-border bg-white px-2.5 text-xs"

// 聊天输入（自适应高度、透明背景）
const inputChat = "w-full resize-none bg-transparent text-[15px] leading-6 outline-none"
```

### 4.3 面板 & 卡片

```tsx
// 面板外壳
const panelShell = "rounded-xl border border-border bg-surface p-3 shadow-[var(--shadow-card)]"

// KPI 卡片
const kpiCard = "rounded-xl border border-border bg-surface-subtle p-3 shadow-sm"

// 空状态
const emptyState = "rounded-lg border border-dashed border-border bg-surface-subtle p-4 text-center"
```

### 4.4 药丸标签 & 状态徽标

```tsx
// 通用药丸
const chip = "inline-flex items-center gap-1.5 rounded-full border border-border bg-surface-subtle px-2.5 py-0.5 text-[11px] font-medium"

// 状态徽标（配合语义色）
const statusBadge = "rounded-full border px-2 py-0.5 text-xs font-semibold"
// 例: bg-step-success/10 border-step-success/30 text-step-success
```

### 4.5 模态框 & 浮层

```tsx
// 遮罩层
const overlayBackdrop = "fixed inset--0 z-50 bg-foreground/40 backdrop-blur-sm"

// 弹窗面板
const overlayPanel = "flex flex-col overflow-hidden rounded-2xl border border-border bg-surface shadow-2xl"

// 下拉菜单
const dropdownMenu = "absolute z-40 min-w-[168px] overflow-hidden rounded-lg border border-border bg-surface py-1 shadow-lg"

// 菜单项
const menuItem = "px-3 py-1.5 text-xs text-muted transition-colors duration-150 hover:bg-surface-subtle hover:text-foreground"
```

### 4.6 数据表格

```tsx
const dataTableShell = "max-w-full overflow-x-auto overscroll-x-contain rounded-xl border border-border"
const dataTable = "w-full min-w-max text-left text-[11px]"
// 表头: sticky + bg-surface-subtle + shadow-[inset_0_-1px_0_var(--border)]
// 行: border-t border-border hover:bg-primary-light/5
```

---

## 5. 语义色调系统（Tone System）

通过函数返回结构化的类名组合，实现一致的色彩编码：

```tsx
interface ToneClassBundle {
  bg: string       // 背景色 (10% 透明度)
  border: string   // 边框色 (30% 透明度)
  text: string     // 文字色
  bar: string      // 强调条/指示器
  ring: string     // 聚焦环
}

// 步骤类型着色
function stepKindTone(kind: string): ToneClassBundle {
  const map: Record<string, ToneClassBundle> = {
    inspect: {
      bg: 'bg-step-inspect/10',
      border: 'border-step-inspect/30',
      text: 'text-step-inspect',
      bar: 'bg-step-inspect',
      ring: 'ring-step-inspect/20',
    },
    query: { /* ... */ },
    transform: { /* ... */ },
    // ...
  }
  return map[kind] ?? map.inspect
}

// 状态着色
function statusTone(status: string): ToneClassBundle {
  // success → step-success, error → step-error, warning → step-warning
}
```

使用场景：步骤卡片、任务状态、制品标签、操作反馈。

---

## 6. 布局系统

### 6.1 工作台三栏布局

```
┌──────────┬────────────────────────┬──────────────┐
│  左侧栏  │       聊天/主内容       │   右侧面板   │
│ 200-280px│    minmax(420px, 1fr)  │  320-640px   │
│ (可折叠) │      (居中 360-760px)   │  (可关闭)    │
└──────────┴────────────────────────┴──────────────┘
```

```tsx
// CSS Grid 三栏，始终保持三轨道以支持平滑过渡
const gridTemplate = `${fixedGridColumn(left)} minmax(${CHAT_MIN_WIDTH}, 1fr) ${fixedGridColumn(right)}`

// 固定栏宽列
function fixedGridColumn(width: number) {
  return `${width}px`
}
```

### 6.2 响应式策略

```
视口缩小时 → 先关闭右侧面板 → 再折叠左侧栏为 56px 图标模式
```

- 右侧面板在移动端切换为抽屉模式
- 聊天输入宽度动态计算：`min(聊天列宽, 760px)`，最小 360px

### 6.3 面板拖拽调整

- 使用 Pointer Events 实现拖拽手柄
- 双击重置为默认宽度
- 手柄为面板边缘的绝对定位覆盖层

---

## 7. 动画系统

所有动画均支持 `prefers-reduced-motion`：

```css
/* 步骤卡片进入 */
@keyframes step-in {
  from { opacity: 0; transform: translateY(6px); }
  to   { opacity: 1; transform: translateY(0); }
}
/* 使用: animate-[step-in_0.28s_ease-out] */

/* 运行中脉冲光晕 */
@keyframes step-border-pulse {
  0%, 100% { box-shadow: 0 0 0 0 transparent; }
  50%      { box-shadow: 0 0 0 3px var(--step-running-ring); }
}
/* 使用: animate-[step-border-pulse_2.2s_infinite] */

/* 光标闪烁 */
@keyframes caret-blink {
  0%, 100% { opacity: 1; }
  50%      { opacity: 0; }
}
/* 使用: animate-[caret-blink_1s_step-end_infinite] */

/* 浮层进入 */
@keyframes guide-popover-in {
  from { opacity: 0; transform: scale(0.96) translateY(4px); }
  to   { opacity: 1; transform: scale(1) translateY(0); }
}
/* 使用: animate-[guide-popover-in_0.18s_ease-out] */

/* 加载跳动点 */
@keyframes chat-loading-dot {
  0%, 80%, 100% { transform: scale(0); }
  40%           { transform: scale(1); }
}
/* 三点错开: animation-delay: 0s / 0.16s / 0.32s */

/* 会话活跃指示器 — 双轨道旋转 */
@keyframes session-sidebar-running-orbit {
  /* 内圈顺时针、外圈逆时针 */
}
```

### 过渡规范

| 元素 | 过渡 |
|------|------|
| 交互元素颜色变化 | `transition-colors duration-200` |
| 展开/折叠箭头 | `transition-transform` |
| 面板宽度 | `transition: grid-template-columns 0.2s ease` |
| 菜单项 | `transition-colors duration-150` |

---

## 8. 图标规范

全部使用内联 SVG，不依赖外部图标库：

```tsx
function SomeIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className={cn("h-4 w-4", className)}
    >
      {/* 路径 */}
    </svg>
  )
}
```

**规范要点：**
- `viewBox="0 0 20 20"`（Web）/ `"0 0 16 16"`（Console）
- `fill="none"` + `stroke="currentColor"` 继承文字色
- `strokeWidth` 范围 1.6 ~ 2.5
- 始终添加 `aria-hidden`
- 通过 `className` 控制尺寸

**制品类型用 Unicode 字符：**
- `▦` Dataset
- `SQL` SQL
- `◇` Chart
- `¶` Report
- `□` File

---

## 9. 滚动条样式

模拟 macOS / VS Code 风格，仅在 hover/focus 时显示：

```css
/* Firefox */
.scrollable {
  scrollbar-width: thin;
  scrollbar-color: transparent transparent;
}
.scrollable:hover {
  scrollbar-color: var(--accent) transparent;
}

/* Chrome / Safari */
.scrollable::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}
.scrollable::-webkit-scrollbar-thumb {
  background: transparent;
  border-radius: 3px;
}
.scrollable:hover::-webkit-scrollbar-thumb {
  background: var(--accent);
}
```

---

## 10. 暗色模式策略

### Web 端（当前仅亮色）

设计为近白背景（`#f7f7f8`）+ 近黑文字（`#0d0d0d`），无暗色模式切换。如需扩展暗色模式，只需新增：

```css
@media (prefers-color-scheme: dark) {
  :root {
    --primary: #ffffff;
    --primary-light: #d4d4d4;
    --surface: #1a1a1a;
    --surface-subtle: #242424;
    --border: #333333;
    --foreground: #f0f0f0;
    --text-secondary: #a0a0a0;
    --text-tertiary: #666666;
    /* 步骤色保持不变，低饱和度在暗色下依然可读 */
  }
}
```

### TUI 端（暗色主题系统）

```tsx
interface TuiThemeTokens {
  background: string       // #0B0F14
  text: string
  border: string
  structure: string        // 结构性元素色
  interaction: string      // 交互强调色
  status: { success, warning, error, info }
  selection: string
}

// 内置预设: mist-dark (青绿交互) / legacy-dark (蓝色交互)
```

---

## 11. 实用工具函数

### 11.1 类名合并

```tsx
import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
```

### 11.2 集中式类名导出（推荐模式）

将复用的 Tailwind 组合提取为命名常量，统一从 `ui-tokens.ts` 导出：

```tsx
// ui-tokens.ts
export const btnPrimaryClass = 'rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-white hover:bg-primary-light cursor-pointer transition-colors duration-200'
export const btnSecondaryClass = 'rounded-lg border border-border bg-surface px-3 py-1.5 text-xs font-medium text-muted hover:bg-surface-subtle cursor-pointer transition-colors duration-200'
export const panelShellClass = 'rounded-xl border border-border bg-surface p-3 shadow-[var(--shadow-card)]'
export const chipClass = 'inline-flex items-center gap-1.5 rounded-full border border-border bg-surface-subtle px-2.5 py-0.5 text-[11px] font-medium'
export const emptyStateClass = 'rounded-lg border border-dashed border-border bg-surface-subtle p-4 text-center'
export const overlayBackdropClass = 'fixed inset-0 z-50 bg-foreground/40 backdrop-blur-sm'
export const overlayPanelClass = 'flex flex-col overflow-hidden rounded-2xl border border-border bg-surface shadow-2xl'
export const AUTH_BUTTON_CLASS = 'h-10 w-full rounded-md bg-primary text-white font-semibold cursor-pointer transition-colors hover:bg-primary-light'
```

---

## 12. 设计原则总结

1. **低饱和度色彩** — 步骤色采用宝石色调，克制不张扬，长时间使用不疲劳
2. **统一的圆角层级** — 8px / 12px / 16px 三级，对应按钮/卡片/弹窗
3. **文字层级分明** — 10px ~ 24px 六级字号，配合 secondary/tertiary 色彩
4. **令牌驱动** — CSS 变量 → Tailwind 桥接，一处修改全局生效
5. **类名常量化** — 复杂 Tailwind 组合提取为 `const`，避免重复、便于维护
6. **语义色调函数** — `stepKindTone()` / `statusTone()` 返回结构化类名包
7. **动画克制** — 仅关键交互有动画，全部支持 `prefers-reduced-motion`
8. **无外部依赖图标** — 内联 SVG，继承文字色，体积小、风格统一
9. **三栏弹性布局** — CSS Grid 三轨道，始终平滑过渡，无布局跳变
10. **滚动条沉浸** — 默认隐藏，hover 时浮现，不抢夺内容空间
