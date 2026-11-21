import { useEffect, useState } from 'react';
import { t as tr } from '@/i18n/runtime';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import client from '@/api/client';

// 物化数据集选择（UI 资源规范：显示 name，选择用选择框，禁止手输内部 ID）。
// 只列「可物化」的数据集：SQL 型（含 both）——语义型数据集的行数上限由语义层编译决定，
// 物化执行器会显式拒绝（materialize_runner: DATASET_NOT_SQL），这里不放进来避免做选项即失败。
export interface DatasetParam {
  key: string;
  label?: string;
  value_type?: string;
}

export interface DatasetOption {
  name: string;
  status?: string;
  source_type?: string;
  params: DatasetParam[];
}

function materializable(row: any): boolean {
  const type = String(row?.source_type || '');
  // 'sql' = 只有 SQL；'both' = 语义对象 + SQL 双定义（执行仍按 SQL 正文）
  return type === 'sql' || type === 'both';
}

export function useMaterializableDatasets(): DatasetOption[] {
  const [options, setOptions] = useState<DatasetOption[]>([]);
  useEffect(() => {
    // 不传 page_size = 选择面板档（全量 {items}），与 list-pagination §5 的两档契约一致
    client.get('/datasets/').then(({ data }) => {
      const items = Array.isArray(data) ? data : data?.items || [];
      setOptions(items.filter(materializable).map((d: any) => ({
        name: d.name,
        status: d.status,
        source_type: d.source_type,
        params: (d.param_summary || [])
          .filter((g: any) => g?.key)
          .map((g: any) => ({ key: String(g.key), label: g.label, value_type: g.value_type })),
      })));
    }).catch(() => setOptions([]));
  }, []);
  return options;
}

export function DatasetSelect({
  value, onChange, datasets, placeholder = tr('k_f4bf86975d'),
}: {
  value?: string; onChange: (v: string) => void;
  datasets: DatasetOption[]; placeholder?: string;
}) {
  return (
    <Select value={value || ''} onValueChange={onChange}>
      <SelectTrigger><SelectValue placeholder={placeholder} /></SelectTrigger>
      <SelectContent>
        {datasets.length === 0 && (
          <SelectItem value="__empty__" disabled>{tr('k_75c9abe4cf')}</SelectItem>
        )}
        {datasets.map(opt => (
          <SelectItem key={opt.name} value={opt.name}>{opt.name}</SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
