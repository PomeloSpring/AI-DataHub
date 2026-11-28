import { useEffect, useState } from 'react';
import { t as tr } from '@/i18n/runtime';
import { Button } from '@/components/ui/button';
import { toast } from '@/lib/toast';
import { dagApi, type DagGraph } from '@/api/dag';

// ── 物化节点「数据流 SQL」就地预览 ─────────────────────────────────
// 用**当前编辑中的配置**走「导出 SQL」同一条编译链（POST /dag/translate/
// dag-to-sql → export_materialize_sql），把平台编译出的完整
// INSERT INTO ... SELECT 直接展示在配置面板里，不必绕道导出对话框。
// - 只编译当前节点（单节点 graph，edges=[]）；多节点编排的整脚本仍归导出；
// - run_params 留空 = 按节点配置的默认窗口，与导出对话框的默认口径一致；
// - 防抖 600ms + 竞态取消：改配置不逐键打后端，旧响应不覆盖新配置；
// - 前端只挡「三件套为空」（数据集/目标数据源/目标表），其余配置问题
//   （分区缺日期参数、预算越界、数据集不存在…）交后端显式报错 + 重试，
//   不在前端重复实现校验口径（两套口径必然漂移）。
export function DataflowSqlPreview({ nodeKey, nodeName, config }: {
  nodeKey: string; nodeName?: string; config: Record<string, any>;
}) {
  const ready = Boolean(config?.dataset && config?.target_datasource && config?.target_table);
  // 内容签名：面板 set 每次都产出新引用，按内容触发才不会无谓重编译
  const signature = JSON.stringify(config || {});
  const [sql, setSql] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    if (!ready) {
      setSql(''); setError(null); setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    const timer = setTimeout(() => {
      const graph: DagGraph = {
        nodes: [{ key: nodeKey, name: nodeName || nodeKey, type: 'materialize', config }],
        edges: [],
      };
      dagApi.dagToSql(graph).then(({ data }) => {
        if (cancelled) return;
        const text = String(data?.sql || '').trim();
        // 编译链至少会回节点注释：空响应 = 链路异常，按失败喊出来而不是静默留空
        if (text) setSql(text);
        else { setSql(''); setError(tr('k_b063ca813b')); }
      }).catch((e: any) => {
        if (cancelled) return;
        setError(e?.response?.data?.detail || tr('k_b063ca813b'));
      }).finally(() => { if (!cancelled) setLoading(false); });
    }, 600);
    return () => { cancelled = true; clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature, reload, nodeKey, nodeName]);

  if (!ready) {
    return <p className="mt-2 text-[10px] text-muted-foreground">{tr('k_e0c9fb04e6')}</p>;
  }

  return (
    <div className="mt-2 border rounded-lg p-2 space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium">{tr('k_aa9dd1b250')}</span>
        {sql && !loading && !error && (
          <Button variant="outline" size="sm" className="h-5 px-1.5 text-[10px]"
            onClick={() => navigator.clipboard.writeText(sql)
              .then(() => toast.success(tr('k_52e6abbe5c')))}>
            {tr('k_79d3abe929')}
          </Button>
        )}
      </div>
      <p className="text-[10px] text-muted-foreground">{tr('k_54bfcfb4a1')}</p>
      {loading && <p className="text-[10px] text-muted-foreground">{tr('k_300ee3dee4')}</p>}
      {!loading && error && (
        <div className="flex items-center gap-2">
          <p className="text-[10px] text-destructive min-w-0 break-all">{error}</p>
          <Button variant="outline" size="sm" className="h-5 px-1.5 text-[10px] shrink-0"
            onClick={() => setReload(r => r + 1)}>
            {tr('k_e2d53a6d3a')}
          </Button>
        </div>
      )}
      {!loading && !error && sql && (
        <pre className="text-[10px] font-mono bg-muted/40 rounded p-1.5 max-h-56 overflow-auto
          whitespace-pre-wrap break-all leading-relaxed">{sql}</pre>
      )}
    </div>
  );
}
