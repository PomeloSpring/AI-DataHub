import { useEffect, useState } from 'react';
import { SearchSelect } from '@/components/ui/search-select';
import { decisionsApi, type DictTerm } from '@/api/decisions';

/**
 * 指标/维度业务名选择器（配置页唯一的字典入口）。
 *
 * 为什么不是自由输入框：`ui-resource-display.md` §2 要求"一切从集合中选一个的输入
 * 必须用选择框"，而决策树条件的 metric/dimension 只有落在字典上才可审计。让人手输
 * 就等于把"写错一个字的口径"变成一条长期静默失效的建议（本模块的引擎对无法确认的
 * 名字一律 partial/skipped，不会猜）。
 *
 * 清单来自后端 `/decisions/query/dictionary`：它只回业务名与归属对象，物理表列名
 * 从这一层就进不了界面（不是靠前端过滤）。
 */
export default function DictTermSelect({
  kind, value, onChange, disabled, ariaLabel,
}: {
  kind: 'metric' | 'dimension';
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
  ariaLabel?: string;
}) {
  const [items, setItems] = useState<DictTerm[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchTerms = async () => {
    setLoading(true);
    setError(null);
    try {
      const { data } = await decisionsApi.dictionary(kind, '');
      setItems(data.items || []);
    } catch (err: any) {
      // 加载失败必须显式展示，不能伪装成"无候选"
      setError(err?.response?.data?.detail || `${kind === 'metric' ? '指标' : '维度'}字典加载失败`);
      setItems([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchTerms(); }, [kind]);

  return (
    <SearchSelect
      value={value}
      onValueChange={onChange}
      ariaLabel={ariaLabel || (kind === 'metric' ? '选择指标' : '选择维度')}
      disabled={disabled}
      loading={loading}
      error={error}
      onRetry={fetchTerms}
      placeholder={kind === 'metric' ? '搜索并选择指标' : '搜索并选择维度（可留空）'}
      emptyText="字典里还没有可用的业务名"
      options={items.map(t => ({
        value: t.name,
        label: t.name,
        description: [t.name_en, t.aliases?.length ? `别名 ${t.aliases.join('/')}` : '',
          t.has_object ? '' : '（字典行还没归属对象）'].filter(Boolean).join(' · '),
        keywords: [t.name_en, ...(t.aliases || [])].filter(Boolean).join(' '),
      }))}
    />
  );
}
