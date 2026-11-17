/**
 * i18next 类型安全：t() 的 key 编译期校验 + 自动补全。
 *
 * `ui` 命名空间是批量 i18n 迁移产物（1900+ hash key），逐字 enum 会让 tsc 慢且脆，
 * 故放宽为 index signature；其他命名空间保持严格类型（enum + 编译期 key 校验）。
 */
import type common from './locales/zh-CN/common.json';
import type menu from './locales/zh-CN/menu.json';
import type onboarding from './locales/zh-CN/onboarding.json';
import type workspace from './locales/zh-CN/workspace.json';
import type chat from './locales/zh-CN/chat.json';
import type dashboard from './locales/zh-CN/dashboard.json';
import type data from './locales/zh-CN/data.json';
import type report from './locales/zh-CN/report.json';

declare module 'i18next' {
  interface CustomTypeOptions {
    defaultNS: 'common';
    resources: {
      common: typeof common;
      menu: typeof menu;
      onboarding: typeof onboarding;
      workspace: typeof workspace;
      chat: typeof chat;
      dashboard: typeof dashboard;
      data: typeof data;
      report: typeof report;
      ui: { [key: string]: string };
    };
  }
}
