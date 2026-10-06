import { useEffect, useState } from 'react';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import client from '@/api/client';

// 数据源选择（UI 资源规范：显示 name，选择用选择框，禁止手输内部 ID）
export interface DatasourceOption { name: string; db_type?: string; }

export function useDatasources() {
  const [options, setOptions] = useState<DatasourceOption[]>([]);
  useEffect(() => {
    client.get('/datasources/authorized').then(({ data }) => {
      const items = Array.isArray(data) ? data : data?.items || [];
      setOptions(items.map((d: any) => ({ name: d.name, db_type: d.db_type })));
    }).catch(() => setOptions([]));
  }, []);
  return options;
}

export function DatasourceSelect({
  value, onChange, allowEmpty = false, placeholder = '选择数据源',
}: {
  value?: string; onChange: (v: string) => void;
  allowEmpty?: boolean; placeholder?: string;
}) {
  const options = useDatasources();
  return (
    <Select value={value || (allowEmpty ? '__none__' : '')} onValueChange={v => onChange(v === '__none__' ? '' : v)}>
      <SelectTrigger><SelectValue placeholder={placeholder} /></SelectTrigger>
      <SelectContent>
        {allowEmpty && <SelectItem value="__none__">不写入</SelectItem>}
        {options.map(opt => (
          <SelectItem key={opt.name} value={opt.name}>
            {opt.name}{opt.db_type ? ` (${opt.db_type})` : ''}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
