/**
 * 全局 t 助手：模块级使用（无 hook、无 React 上下文），走 i18next 单例。
 *
 * 用于那些"结构上不便挂 useTranslation hook"的位置（模块顶层常量、
 * 复杂 render-prop、memo 内部小函数等）。切换语言时依赖 App 顶层
 * useTranslation 订阅触发的整树重渲染（低频操作，可接受）。
 */
import i18n from './index';
export const t = (key: string, opts?: Record<string, unknown>): string =>
  (i18n.t(key as never, { ns: 'ui', ...opts } as never) as unknown) as string;
