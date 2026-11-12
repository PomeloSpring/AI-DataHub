import { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { Label } from '@/components/ui/label';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import client from '../api/client';

/**
 * ES 型数据集的「索引范围」选择器 + 通道状态 + 方言提示（新建与详情页共用一份实现）。
 *
 * 为什么单独成组件：范围清单、就绪态、ES SQL 方言规则这三件事必须与后端同源
 * （`GET /api/datasets/es-patterns`），两处各写一份就会与实机校准结果漂移。
 *
 * 口径要点：
 * - 选项只给**已开放的索引模式**（如 `logs-*`），不提供手填/时间片段收窄：实测
 *   模式末尾接时间片段只收窄**索引**、不收窄行（`logs-*2026.11` 仍含上月数据），
 *   把它做成可手输的输入框会诱导出"以为按时间筛过了"的错数；要按时间收行请写正文 WHERE。
 * - 通道不可用/清单为空时给显式原因而不是空下拉：否则配置缺口会被当成"没有索引"
 *   这个业务结论（no-silent-degradation）。
 */

interface EsOptions {
  ready: boolean;
  state: string;
  status_message: string;
  patterns: string[];
  sql_hint?: string;
}

export default function EsSourcePicker({ value, onChange, disabled = false, required = false }: {
  value: string;
  onChange: (pattern: string) => void;
  disabled?: boolean;
  /** 显示「索引范围 *」标签（新建表单里是必填项） */
  required?: boolean;
}) {
  const [opts, setOpts] = useState<EsOptions | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState('');

  const fetchOptions = () => {
    setLoading(true);
    setFailed('');
    client.get('/datasets/es-patterns')
      .then(({ data }) => setOpts({
        ready: !!data?.ready,
        state: data?.state || '',
        status_message: data?.status_message || '',
        patterns: Array.isArray(data?.patterns) ? data.patterns : [],
        sql_hint: data?.sql_hint || '',
      }))
      .catch((e: any) => {
        // 拉失败不能静默留空下拉：那会被读成"这个部署没有索引"，而事实是拿不到状态
        setOpts(null);
        setFailed(e?.response?.data?.detail || 'ES 通道状态获取失败');
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => { fetchOptions(); }, []);

  const selectDisabled = disabled || loading || !!failed || !opts?.ready || !opts.patterns.length;

  return (
    <div className="space-y-1.5">
      <Label>{required ? '索引范围 *' : '索引范围'}</Label>
      <Select value={value || undefined} onValueChange={onChange} disabled={selectDisabled}>
        <SelectTrigger aria-label="索引范围"><SelectValue placeholder={
          loading ? '加载中…'
            : failed ? '状态获取失败'
              : !opts?.ready ? '通道不可用'
                : !opts.patterns.length ? '未开放任何范围'
                  : '选择索引范围'
        } /></SelectTrigger>
        <SelectContent>
          {(opts?.patterns || []).map(p => <SelectItem key={p} value={p}>{p}</SelectItem>)}
        </SelectContent>
      </Select>

      {failed && (
        <p className="text-[10px] text-destructive" role="alert">
          {failed}
          <button type="button" className="ml-1 underline" onClick={fetchOptions}>重试</button>
        </p>
      )}
      {!failed && opts && !opts.ready && (
        <p className="text-[10px] text-destructive" role="alert">
          内置 Elasticsearch 通道当前不可用：{opts.status_message || opts.state}
          （配置由部署侧托管，请联系管理员）
        </p>
      )}
      {!failed && opts?.ready && !opts.patterns.length && (
        <p className="text-[10px] text-destructive" role="alert">
          当前未开放任何索引范围（白名单为空时默认不开放），请联系管理员登记后重试
        </p>
      )}
      {loading && <span className="ml-1"><Loader2 className="inline h-3 w-3 animate-spin" /></span>}

      {opts?.sql_hint && (
        <p className="text-[10px] leading-relaxed text-muted-foreground">{opts.sql_hint}</p>
      )}
      <p className="rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-[10px] text-amber-700 dark:text-amber-400">
        本通道不施加列级/行级数据权限（ES SQL 无 JOIN/可包裹子查询、无 ES 方言可改写），
        可查范围只由已开放的索引范围决定，范围内数据应已脱敏；
        要按角色限制行/列可见性请改用数据源型数据集。
      </p>
    </div>
  );
}
