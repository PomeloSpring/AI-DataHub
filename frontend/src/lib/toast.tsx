import { Toaster as SonnerToaster, toast as sonnerToast, type ExternalToast } from 'sonner';

/**
 * toast 提示的**唯一事实源**：按类型分档驻留 + 每条都能主动关闭。
 *
 * 历史问题：main.tsx 只配了 `richColors position`，sonner 默认驻留 4s 且不带关闭按钮，
 * “所有更改已保存”这类确认条长期赖在右上角又关不掉，反而干扰使用。
 *
 * 分档理由：全站 716 处调用里 error 占 456（64%），一刀切设很短会牺牲错误可读性；
 * 而真正碍眼的是成功确认条，它要的就是一闪即逝。
 * 数值取自项目既有规范《Toast 消息时长分档策略》，不自定一套：
 *   - success / info   2s：确认类，读完即走（本次诉求的直接来源）
 *   - warning / error  3s：需读完报错内容
 * 任何调用点仍可传 `duration` 覆盖（例如需要长驻的可传 `duration: Infinity`）。
 */

export const TOAST_DURATION = {
  success: 2000,
  info: 2000,
  warning: 3000,
  error: 3000,
} as const;

export type ToastOptions = ExternalToast;

const respond = (
  fire: (message: string, data?: ExternalToast) => string | number,
  duration: number,
) => (message: string, options?: ToastOptions) =>
  fire(message, { duration, ...options });

/**
 * 无类型的默认提示 `toast(...)`。**带可点按钮时自动取长档**：
 * 2s 内按钮根本来不及点（如删除后的「撤销」），会把撤销功能实际废掉。
 */
const baseToast = (message: string, options?: ToastOptions) =>
  sonnerToast(message, {
    duration: options?.action || options?.cancel ? TOAST_DURATION.error : TOAST_DURATION.info,
    ...options,
  });

export const toast = Object.assign(baseToast, {
  success: respond(sonnerToast.success, TOAST_DURATION.success),
  info: respond(sonnerToast.info, TOAST_DURATION.info),
  warning: respond(sonnerToast.warning, TOAST_DURATION.warning),
  error: respond(sonnerToast.error, TOAST_DURATION.error),
  /** 原样透传的底层能力：dismiss / promise / custom 等未做分档 */
  dismiss: sonnerToast.dismiss,
  custom: sonnerToast.custom,
  promise: sonnerToast.promise,
});

/** 全站 toast 挂载点：配置只在这一处，改这里全端生效 */
export function AppToaster() {
  return (
    <SonnerToaster
      richColors
      position="top-right"
      // 每条都带 ×：用户想什么时候消就什么时候消（主要诉求）
      closeButton
      // 默认值兜底；各类型实际用 TOAST_DURATION
      duration={TOAST_DURATION.warning}
      // 连续保存时不要堆成一片盖住界面
      visibleToasts={3}
    />
  );
}
