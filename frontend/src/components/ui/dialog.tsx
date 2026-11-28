import * as React from "react"
import * as DialogPrimitive from "@radix-ui/react-dialog"
import { X } from "lucide-react"
import { cn } from "@/lib/utils"
import { useFullscreenContainer } from '@/hooks/useDashboardFullscreen'

const Dialog = DialogPrimitive.Root
const DialogTrigger = DialogPrimitive.Trigger
const DialogPortal = DialogPrimitive.Portal
const DialogClose = DialogPrimitive.Close

// ── 弹窗内的嵌套浮层不算“点弹窗外” ─────────────────────────────────────
// Radix 长期已知缺陷（primitives#2450 / #2961，官方 closed as not planned，2026-03
// 仍有人确认复现）：弹窗里展开 Select 后点别处，整个弹窗会被一起关掉。
// 机制：Select 内容层硬编码 disableOutsidePointerEvents: true（react-select:472，
// 不受 Root 的 modal 控制），展开时把弹窗那一层压成 pointer-events:none
// （react-dismissable-layer:131），于是这一下的命中测试落空到 <html>，被判成外点。
// 社区里被实机验证可用的解法是“嵌套浮层展开期间，弹窗一律不响应外点”
// （原话补丁：onPointerDownOutside={(e) => openSelect && e.preventDefault()}）。
// 这里做成全站同源，且不靠给每个弹窗提 openSelect 状态：只看“有没有浮层正展开”。
// 曾经试过并被实机否定掉的写法：按 target/坐标判“命中落空”——真浏览器里落空目标
// 与坐标受 portal 容器、transform、滚动影响，判据不稳（实测无效），已删除。
const NESTED_LAYER_SELECTOR =
  '[data-radix-popper-content-wrapper], [data-radix-select-viewport], [role="listbox"], [role="option"], [role="menu"], [role="dialog"]';

export function isNestedLayerTarget(target: EventTarget | null): boolean {
  return target instanceof Element && !!target.closest(NESTED_LAYER_SELECTOR);
}

/**
 * 正展开的嵌套模态浮层。Select 的 Content 同时带 role="listbox" 与 data-state
 * （item-aligned / popper 两种 position 都是，react-select:478-482），菜单是 role="menu"。
 * 只收这两类：Tooltip 的 data-state 是 delayed-open/instant-open（hover 不该挡住关窗）；
 * Popover/HoverCard 默认非模态、不会把弹窗层压成 pointer-events:none，拉进来只会误伤。
 */
const OPEN_NESTED_LAYER_SELECTOR = '[role="listbox"][data-state="open"], [role="menu"][data-state="open"]';

export function hasOpenNestedLayer(root: ParentNode = document): boolean {
  return !!root.querySelector(OPEN_NESTED_LAYER_SELECTOR);
}

/** 记录值的新鲜度：只给紧随其后的那次外点/焦点丢失背书，不替很久以后的交互背书 */
const NESTED_OPEN_TTL_MS = 1000;

type OutsideEvent = { target: EventTarget | null; preventDefault: () => void };

/**
 * @param nestedOpenAtPointerDown pointerdown 时刻的“有浮层展开”快照。
 * 必需：Radix Dialog 开了 deferPointerDownOutside，外点判定推迟到 click 才派发，
 * 而下拉在 pointerdown 阶段就已卸载 —— 到派发时再看 DOM 状态永远慢一步
 * （这是上一轮“按坐标判命中落空”实机无效的根因）。
 */
function keepNestedLayers(
  e: OutsideEvent,
  handler?: (event: any) => void,
  nestedOpenAtPointerDown = false,
) {
  if (isNestedLayerTarget(e.target) || nestedOpenAtPointerDown || hasOpenNestedLayer()) e.preventDefault();
  handler?.(e);
}

const DialogOverlay = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Overlay>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Overlay>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Overlay
    ref={ref}
    className={cn(
      "fixed inset-0 z-50 bg-foreground/40 backdrop-blur-sm data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
      className
    )}
    {...props}
  />
))
DialogOverlay.displayName = DialogPrimitive.Overlay.displayName

// 高度护栏（基准类，页面 className 可覆盖）：DialogContent 是 fixed 居中网格，
// 不限高时长表单（指标/维度编辑这类十几行 + 三元组预览）会顶出视口 ——
// 标题/关闭按钮在视口上方、保存/取消在视口下方，用户既滚不动也点不到（历史缺陷）。
// 故基准即带 max-h + 纵向滚动；各页自己的 max-h-[85vh] 之类经 twMerge 仍优先生效。
// 例外：需要占满整高的抽屉（PropertyPanel narrow）自行写 max-h-[100dvh] 覆盖。
//
// 宽度护栏（同一类缺陷的另一轴）：基准原先是 `w-full`（= 视口宽），窗口比弹窗
// max-w 还窄时，弹窗左右贴到窗口边、圆角与边框被切，输入框看着像“超出弹窗”（历史缺陷：
// 新建定时任务表单）。改 `w-[calc(100%-2rem)]` 后两侧永远留 16px 余量，窄窗口下弹窗自己
// 缩，而不是把内容挤到窗外；需要贴边/占满宽度的抽屉与弹窗用自己的 w-[…] 覆盖即可。
const BASE_CONTENT_CLASSES =
  "fixed left-[50%] top-[50%] z-50 grid w-[calc(100%-2rem)] max-w-lg max-h-[90vh] overflow-y-auto overflow-x-hidden translate-x-[-50%] translate-y-[-50%] gap-4 border border-border bg-background p-5 shadow-2xl duration-200 [&>*]:min-w-0 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95 data-[state=closed]:slide-out-to-left-1/2 data-[state=closed]:slide-out-to-top-[48%] data-[state=open]:slide-in-from-left-1/2 data-[state=open]:slide-in-from-top-[48%] rounded-2xl";

const DialogContent = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content>
>(({ className, children, onInteractOutside, onPointerDownOutside, ...props }, ref) => {
  // 在 capture 阶段拍“此刻有嵌套浮层展开”的快照：capture 比 Radix 的 bubble 监听早，
  // 也比浮层卸载早；focusOutside（焦点甩到 body）同样不带坐标，靠这份快照判。
  const nestedOpenRef = React.useRef<{ at: number; open: boolean }>({ at: 0, open: false });
  React.useEffect(() => {
    const onPointerDown = () => {
      nestedOpenRef.current = { at: Date.now(), open: hasOpenNestedLayer() };
    };
    document.addEventListener('pointerdown', onPointerDown, true);
    return () => document.removeEventListener('pointerdown', onPointerDown, true);
  }, []);
  const nestedOpenAtPointerDown = () =>
    nestedOpenRef.current.open && Date.now() - nestedOpenRef.current.at < NESTED_OPEN_TTL_MS;

  return (
  <DialogPortal container={useFullscreenContainer()}>
    <DialogOverlay />
    <DialogPrimitive.Content
      ref={ref}
      onInteractOutside={(e) => keepNestedLayers(e, onInteractOutside, nestedOpenAtPointerDown())}
      onPointerDownOutside={(e) => keepNestedLayers(e, onPointerDownOutside, nestedOpenAtPointerDown())}
      className={cn(
        BASE_CONTENT_CLASSES,
        className
      )}
      {...props}
    >
      {children}
      <DialogPrimitive.Close className="absolute right-4 top-4 rounded-sm opacity-70 ring-offset-background transition-opacity hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:pointer-events-none data-[state=open]:bg-accent data-[state=open]:text-muted-foreground">
        <X className="h-4 w-4" />
        <span className="sr-only">Close</span>
      </DialogPrimitive.Close>
    </DialogPrimitive.Content>
  </DialogPortal>
  );
})
DialogContent.displayName = DialogPrimitive.Content.displayName

const DialogHeader = ({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) => (
  <div
    className={cn(
      "flex flex-col space-y-1.5 text-center sm:text-left",
      className
    )}
    {...props}
  />
)
DialogHeader.displayName = "DialogHeader"

const DialogFooter = ({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) => (
  <div
    className={cn(
      "flex flex-col-reverse sm:flex-row sm:justify-end sm:space-x-2",
      className
    )}
    {...props}
  />
)
DialogFooter.displayName = "DialogFooter"

const DialogTitle = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Title>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Title>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Title
    ref={ref}
    className={cn(
      "text-lg font-semibold leading-none tracking-tight",
      className
    )}
    {...props}
  />
))
DialogTitle.displayName = DialogPrimitive.Title.displayName

const DialogDescription = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Description>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Description>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Description
    ref={ref}
    className={cn("text-sm text-muted-foreground", className)}
    {...props}
  />
))
DialogDescription.displayName = DialogPrimitive.Description.displayName

export {
  Dialog,
  DialogPortal,
  DialogOverlay,
  DialogClose,
  DialogTrigger,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
}
