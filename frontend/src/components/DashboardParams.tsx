import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import type { DashboardParam } from '../stores/dashboardStore';

interface Props {
  params: DashboardParam[];
  values: Record<string, any>;
  onChange: (name: string, value: any) => void;
}

export default function DashboardParams({ params, values, onChange }: Props) {
  if (!params || params.length === 0) return null;

  return (
    <div className="flex items-center gap-4 px-4 py-2 border-b bg-muted/20 flex-wrap">
      {params.map((p) => {
        const val = values[p.name] ?? p.default ?? '';

        return (
          <div key={p.name} className="flex items-center gap-2">
            <Label className="text-xs text-muted-foreground whitespace-nowrap">{p.label || p.name}</Label>
            {p.type === 'select' ? (
              <Select value={val} onValueChange={(v) => onChange(p.name, v)}>
                <SelectTrigger className="h-8 w-[140px] text-xs">
                  <SelectValue placeholder={p.placeholder || '请选择'} />
                </SelectTrigger>
                <SelectContent>
                  {(p.options || []).map((opt) => (
                    <SelectItem key={opt} value={opt} className="text-xs">{opt}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : p.type === 'number' ? (
              <Input
                type="number"
                value={val}
                placeholder={p.placeholder || ''}
                onChange={(e) => onChange(p.name, e.target.value)}
                className="h-8 w-[120px] text-xs"
              />
            ) : p.type === 'date' ? (
              <Input
                type="date"
                value={val}
                onChange={(e) => onChange(p.name, e.target.value)}
                className="h-8 w-[140px] text-xs"
              />
            ) : (
              <Input
                type="text"
                value={val}
                placeholder={p.placeholder || ''}
                onChange={(e) => onChange(p.name, e.target.value)}
                className="h-8 w-[140px] text-xs"
              />
            )}
          </div>
        );
      })}
    </div>
  );
}
