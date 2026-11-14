/**
 * i18next 类型安全：t() 的 key 编译期校验 + 自动补全。
 * 以 zh-CN 语言包为类型源（en-US 同构靠语言包同批维护）。
 * 新增命名空间时在此登记资源类型（与 locales 目录同步）。
 */
import type common from './locales/zh-CN/common.json';
import type menu from './locales/zh-CN/menu.json';
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
      workspace: typeof workspace;
      chat: typeof chat;
      dashboard: typeof dashboard;
      data: typeof data;
      report: typeof report;
    };
  }
}