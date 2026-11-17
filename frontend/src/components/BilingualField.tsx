// 双语输入位共用组件：中文事实源 + English 覆盖层。
//
// 后端 `i18n.localize_display` 在英文界面下会把 name 改写成英文、把中文事实源挪到
// `name_zh`；本组件负责把编辑页的两个输入位绑到正确的字段上：
//   * 中文输入 = `row[field + '_zh'] ?? row[field]`（避免英文界面保存把英文写进中文列，
//     这是**不可逆污染**，plan §P6 明列）。
//   * English 输入 = `row.i18n[field]['en-US']`（覆盖层，不写主字段）。
// 提交时由父组件从 `value` + `blob` 组回 payload；本组件不代发请求。
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { zhValue, enValue } from '@/lib/i18nValue';

type I18nBlob = Record<string, Record<string, string>>;

export default function BilingualField({
  row, field, label, enLabel, placeholder, enPlaceholder, multiline = false, rows = 2,
  disabled = false, onChangeValue, onChangeBlob,
}: {
  row: Record<string, any>;
  /** 后端主字段名（中文事实源列），同时也是 i18n 覆盖层的键 */
  field: string;
  /** 中文输入框 label */
  label: string;
  /** English 输入框 label（默认 `<label> English`） */
  enLabel?: string;
  placeholder?: string;
  enPlaceholder?: string;
  multiline?: boolean;
  rows?: number;
  disabled?: boolean;
  /** 中文事实源变更：把新值回传给父组件（父组件持有 form state） */
  onChangeValue: (next: string) => void;
  /** 覆盖层变更：给一份新的 i18n blob（`{[field]: {'en-US': text}}`，空 text = 清英） */
  onChangeBlob: (next: I18nBlob) => void;
}) {
  const zh = zhValue(row, field);
  const en = enValue(row, field);
  const En = multiline ? Textarea : Input;
  const Zh = multiline ? Textarea : Input;
  const extra = multiline ? { rows } : {};
  return (
    <div className="space-y-2">
      <div className="space-y-1">
        <label className="text-sm font-medium">{label}</label>
        <Zh
          {...extra}
          value={zh}
          disabled={disabled}
          onChange={(e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => onChangeValue(e.target.value)}
          placeholder={placeholder}
          aria-label={label}
        />
      </div>
      <div className="space-y-1">
        <label className="text-sm font-medium text-muted-foreground">
          {enLabel || `${label} English`}
        </label>
        <En
          {...extra}
          value={en}
          disabled={disabled}
          onChange={(e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
            const v = e.target.value;
            const prev: I18nBlob = { ...(row.i18n || {}) };
            const per = { ...(prev[field] || {}) };
            if (v.trim()) per['en-US'] = v;
            else delete per['en-US'];
            if (Object.keys(per).length) prev[field] = per;
            else delete prev[field];
            onChangeBlob(prev);
          }}
          placeholder={enPlaceholder || 'Optional English label'}
          aria-label={`${label} English`}
          data-testid={`i18n-en-${field}`}
        />
        {!en && !disabled && (
          <p className="text-[11px] text-muted-foreground" data-testid={`i18n-en-empty-${field}`}>
            留空 = 英文界面回落到中文；编辑本框可为英文界面单独命名。
          </p>
        )}
      </div>
    </div>
  );
}
