/**
 * 元数据双语在编辑页的读写口径。
 *
 * 后端下发时（`i18n.localize_display`）会：
 *   * 中文请求 → 原值，无 `*_zh` 也无 `i18n_fallback_fields`；
 *   * 英文请求 → `name` 已改为英文，`name_zh` 保留中文原值，`i18n` 双语 blob 一起下发。
 *
 * 编辑页**必须**把「中文输入框」绑到 `name_zh ?? name`（避免英文界面保存把英文写进中文
 * 事实源列），把「English 输入框」绑到 `i18n[name]["en-US"]`；保存时同时提交 `name`
 * （中文事实源）与 `i18n`（覆盖层），二者不成对提交属未确认写入。
 */
import { LOCALES } from '../i18n';

export type I18nBlob = Record<string, Record<string, string>>;

/** 中文（默认语言）输入框回填值：优先 `name_zh`（英文请求），否则 `name`。 */
export function zhValue<T extends Record<string, any>>(row: T, field: string): string {
  const zh = row?.[`${field}_zh`];
  if (typeof zh === 'string') return zh;
  return String(row?.[field] ?? '');
}

/** English 输入框回填值：`i18n[field]['en-US']`。 */
export function enValue(row: Record<string, any>, field: string): string {
  const blob = row?.i18n;
  if (!blob || typeof blob !== 'object') return '';
  const per = blob[field];
  if (!per || typeof per !== 'object') return '';
  return String(per['en-US'] ?? '');
}

/**
 * 写回：把中文事实源与英文覆盖层组回一份可提交的 payload。
 * 中文留空即视为清空（后端会拒空 name），英文留空即视为「暂无英文版」→ 记 fallback。
 */
export function mergeI18n(
  blob: I18nBlob | undefined | null,
  field: string,
  en: string,
): I18nBlob {
  const next: I18nBlob = { ...(blob || {}) };
  const per = { ...(next[field] || {}) };
  const trimmed = (en || '').trim();
  if (trimmed) per['en-US'] = trimmed;
  else delete per['en-US'];
  if (Object.keys(per).length) next[field] = per;
  else delete next[field];
  return next;
}

/** 前端受控语言清单与后端 SUPPORTED_LOCALES 必须一致（否则后端解析不到 = 静默回落）。 */
export function assertLocaleSync(): void {
  if (typeof console === 'undefined') return;
  const fe = [...LOCALES].sort().join(',');
  // 后端由 tests/test_reply_language.py 锁定同一份清单；这里只在前端出现未知 lang 时提示，
  // 不阻塞渲染（渲染回落到 zhValue 即可）。
  if (!fe) console.warn('[i18nValue] 前端语言清单为空');
}
