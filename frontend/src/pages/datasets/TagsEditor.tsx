/** 标签编辑器（数据集标签的唯一录入控件）。
 *
 * 标签是**自由文本分类**（业务域、物化目的等），只供清单页筛选检索，
 * **不参与**取数口径与权限裁决（可见性仍由 dataset_visible_to 决定）。
 * 形状与看板标签一致：字符串数组，最多 MAX_DATASET_TAGS 个、单个 ≤ MAX_TAG_LENGTH。
 * 与后端 dataset_service.normalize_tags/validate_tags 同一上限：前端先拦一次给出可读提示，
 * 服务端仍然是最终把关（越界显式 400，不静默截断）。
 */
import { useState } from 'react';
import { X } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { toast } from '@/lib/toast';
import { t as tr } from '@/i18n/runtime';

export const MAX_DATASET_TAGS = 12;
export const MAX_TAG_LENGTH = 32;

/** 读路径归一（详情/清单返回的 tags 可能是 JSON 字符串，也可能缺失）。 */
export function normalizeTagList(value: any): string[] {
  if (value == null || value === '') return [];
  let items: any = value;
  if (typeof value === 'string') {
    const text = value.trim();
    if (!text) return [];
    if (text.startsWith('[')) {
      try { items = JSON.parse(text); } catch { items = [text]; }
    } else {
      items = [text];
    }
  }
  if (!Array.isArray(items)) return [];
  const out: string[] = [];
  for (const item of items) {
    if (typeof item !== 'string') continue;
    const tag = item.trim();
    if (!tag || out.includes(tag)) continue;
    out.push(tag.slice(0, MAX_TAG_LENGTH));
    if (out.length >= MAX_DATASET_TAGS) break;
  }
  return out;
}

export default function TagsEditor({
  value, onChange, disabled, suggestions = [], ariaLabel,
}: {
  value: any;
  onChange: (tags: string[]) => void;
  disabled?: boolean;
  /** 库里已用过的标签（来自 GET /datasets/tags），点一下即可复用，避免同义异写 */
  suggestions?: string[];
  ariaLabel?: string;
}) {
  const [draft, setDraft] = useState('');
  const tags = normalizeTagList(value);

  const addMany = (raws: string[]) => {
    const next = [...tags];
    let blocked = '';
    for (const raw of raws) {
      const tag = raw.trim();
      if (!tag) continue;
      if (tag.length > MAX_TAG_LENGTH) { blocked = tr('k_bdcb481fcf', { p0: MAX_TAG_LENGTH }); continue; }
      if (next.includes(tag)) { blocked = blocked || tr('k_3ae54eac79'); continue; }
      if (next.length >= MAX_DATASET_TAGS) { blocked = tr('k_1577955d58', { p0: MAX_DATASET_TAGS }); break; }
      next.push(tag);
    }
    if (next.length !== tags.length) onChange(next);
    if (blocked) toast.error(blocked);
    setDraft('');
  };
  const add = (raw: string) => addMany([raw]);

  const remove = (tag: string) => onChange(tags.filter(x => x !== tag));
  const remaining = suggestions.filter(s => !tags.includes(s)).slice(0, 12);

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-1 min-h-7">
        {tags.map(tag => (
          <Badge key={tag} variant="secondary" className="text-[11px] flex items-center gap-1">
            {tag}
            {!disabled && (
              <button type="button" aria-label={tr('k_b409ff9925', { p0: tag })}
                className="text-muted-foreground hover:text-foreground"
                onClick={() => remove(tag)}>
                <X className="h-3 w-3" />
              </button>
            )}
          </Badge>
        ))}
        {tags.length === 0 && !disabled && (
          <span className="text-xs text-muted-foreground">{tr('k_48fb8d2f23')}</span>
        )}
      </div>
      {!disabled && (
        <Input
          value={draft}
          aria-label={ariaLabel || tr('k_ae0a7afece')}
          placeholder={tr('k_48fb8d2f23')}
          className="h-8 text-xs max-w-md"
          onChange={e => {
            const v = e.target.value;
            // 逗号即确认（批量粘贴 "a,b,c" 也成立）
            if (v.includes(',') || v.includes('，')) {
              addMany(v.split(/[,，]/));
            } else {
              setDraft(v);
            }
          }}
          onKeyDown={e => {
            if (e.key === 'Enter') { e.preventDefault(); add(draft); }
            if (e.key === 'Backspace' && !draft && tags.length) remove(tags[tags.length - 1]);
          }}
          onBlur={() => add(draft)}
        />
      )}
      {!disabled && remaining.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {remaining.map(tag => (
            <button key={tag} type="button"
              className="text-[11px] px-1.5 py-0.5 rounded border text-muted-foreground hover:text-foreground hover:bg-muted/50"
              onClick={() => add(tag)}>
              + {tag}
            </button>
          ))}
        </div>
      )}
      <p className="text-[10px] text-muted-foreground">
        {tr('k_a6e4ebdad2')} · {tr('k_1577955d58', { p0: MAX_DATASET_TAGS })}
      </p>
    </div>
  );
}