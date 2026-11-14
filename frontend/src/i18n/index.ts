import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import './types';
import zhCommon from './locales/zh-CN/common.json';
import zhMenu from './locales/zh-CN/menu.json';
import enCommon from './locales/en-US/common.json';
import enMenu from './locales/en-US/menu.json';

/**
 * 多语言唯一初始化处（zh-CN / en-US）。
 *
 * 口径（架构设计拍板）：
 * - 默认 zh-CN，不自动跟随浏览器语言（保存量用户零感知）；
 *   语言选择优先级：localStorage(adh_locale) > zh-CN。
 * - common + menu 随主包同步加载（壳层首屏必需）；页面命名空间
 *   （chat/workspace/dashboard/report）经下方内置 backend 按 import() 懒加载，
 *   Vite 自动 code-split，不引 i18next-http-backend。
 * - 缺失 key 不静默：dev 控制台显式告警，生产收集上报（旁路，不影响主链路）；
 *   zh/en key 一致性靠语言包同批维护（原 i18n-check 门禁脚本经用户决策于 2026-11-14 移除），fallback 只是运行时兜底。
 * - 业务数据/品牌名/后端错误文案不进语言包（见架构设计 §一 边界）。
 */

export const LOCALES = ['zh-CN', 'en-US'] as const;
export type Locale = (typeof LOCALES)[number];
export const DEFAULT_LOCALE: Locale = 'zh-CN';
export const LOCALE_STORAGE_KEY = 'adh_locale';

export const isLocale = (v: unknown): v is Locale =>
  typeof v === 'string' && (LOCALES as readonly string[]).includes(v);

/** 读取持久化语言；非法/缺失一律回默认（不猜浏览器语言） */
export function getStoredLocale(): Locale {
  try {
    const v = localStorage.getItem(LOCALE_STORAGE_KEY);
    return isLocale(v) ? v : DEFAULT_LOCALE;
  } catch {
    return DEFAULT_LOCALE;
  }
}

/**
 * menu_key → menu 命名空间 key：登记表用冒号（workspace:dashboards），
 * 冒号是 i18next 的 ns 分隔符，语言包里统一落点号（workspace.dashboards）。
 * 显隐反查仍用原 menu_key（权限码口径不动），这里只转显示层。
 */
export const menuI18nKey = (menuKey: string): string => menuKey.replace(/:/g, '.');

/** 页面命名空间懒加载：只服务非内置包；失败显式报错（不静默吞成空包） */
const lazyBackend = {
  type: 'backend' as const,
  read(language: string, namespace: string, callback: (err: unknown, data: unknown) => void) {
    import(`./locales/${language}/${namespace}.json`)
      .then(mod => callback(null, mod.default))
      .catch(err => {
        console.error(`[i18n] 语言包加载失败: ${language}/${namespace}`, err);
        callback(err, false);
      });
  },
};

/** 生产缺失 key 采集（可观测旁路：只记录不抛出，不影响取数/渲染主链路） */
const missingKeys = new Set<string>();
export const getMissingKeys = (): string[] => [...missingKeys];

void i18n
  .use(lazyBackend)
  .use(initReactI18next)
  .init({
    lng: getStoredLocale(),
    fallbackLng: DEFAULT_LOCALE,
    supportedLngs: LOCALES as unknown as string[],
    ns: ['common', 'menu'],
    defaultNS: 'common',
    partialBundledLanguages: true,
    resources: {
      'zh-CN': { common: zhCommon, menu: zhMenu },
      'en-US': { common: enCommon, menu: enMenu },
    },
    interpolation: { escapeValue: false }, // React 已转义
    react: { useSuspense: false }, // 懒加载包未就绪时不挂起整页，避免闪白
    saveMissing: false,
    missingKeyHandler: (lngs, ns, key) => {
      const id = `${ns}:${key}`;
      missingKeys.add(id);
      if (import.meta.env.DEV) {
        console.warn(`[i18n] 缺失文案 key: ${id}（langs: ${lngs.join(',')}）`);
      }
    },
  });

/** 切换语言：即时生效 + 持久化；非法值显式拒绝（不静默忽略后假装已切） */
export function setLocale(locale: Locale): void {
  if (!isLocale(locale)) {
    throw new Error(`[i18n] 不支持的语言: ${String(locale)}（可选: ${LOCALES.join(', ')}）`);
  }
  try {
    localStorage.setItem(LOCALE_STORAGE_KEY, locale);
  } catch {
    // 隐私模式等写入失败不阻断切换，仅本次会话生效
  }
  void i18n.changeLanguage(locale);
}

// 多 tab 同步：另一标签页切语言时本页跟随（storage 事件只在跨 tab 触发）
if (typeof window !== 'undefined') {
  window.addEventListener('storage', e => {
    if (e.key !== LOCALE_STORAGE_KEY) return;
    if (isLocale(e.newValue) && e.newValue !== i18n.language) {
      void i18n.changeLanguage(e.newValue);
    }
  });
}

// <html lang> 随语言更新（无障碍/字体渲染依赖）
const syncHtmlLang = (lng: string) => {
  if (typeof document !== 'undefined') document.documentElement.lang = lng;
};
syncHtmlLang(i18n.language);
i18n.on('languageChanged', syncHtmlLang);

export default i18n;