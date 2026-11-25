/**
 * CanonicalDocPanel — 本体 canonical 文档的只读查看面板（左侧能力分区「文档」入口）。
 *
 * 形态：右侧整屏分栏（覆盖在画布上，左侧留出上下文），**不是居中弹窗**。
 * 数据：`/catalog/ontology/models/{id}/view?as=json|yaml|md`
 *   → 服务端 hydrate 字典口径后渲染；读的是**当前生效版**，草案正文在「变更对比」里看。
 *   服务端对该只读投影做进程内缓存（正文/字典变动即失效），响应带 `cached` 标记，这里如实显示。
 * SPARQL 档：打的是同一份事实源的 RDF 投影（`POST /graph/query`，命名图按 kind 分图），
 *   本面板只放行查询类语句（SELECT/ASK/CONSTRUCT/DESCRIBE），写入/更新不在界面开口子
 *   （重建命名图走工具栏的「图谱生成」按钮）。
 * 边界：纯只读，不提供编辑/保存（唯一编辑源仍是 canonical doc 的保存草案链路）。
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Check, Copy, FileText, Loader2, Play, RotateCw, X } from 'lucide-react';
import { toast } from '@/lib/toast';
import client from '@/api/client';
import { ontologyApi } from '@/api/ontology';
import { hasPerm } from '@/stores/permissionStore';
import { useTranslation } from 'react-i18next';

const DOC_FORMATS = [
  { key: 'json', label: 'JSON' },
  { key: 'yaml', label: 'YAML' },
  { key: 'md', label: 'MD' },
] as const;
type DocAs = (typeof DOC_FORMATS)[number]['key'];

const LINE_H = 18;

/** SPARQL 结果单元格归一：兼容 {value,type,datatype} 绑定对象与裸字符串。 */
function sparqlCell(v: any): string {
  if (v == null) return '';
  if (typeof v === 'object') return String(v.value ?? JSON.stringify(v));
  return String(v);
}

/** 命名图按 kind 分图（与写入侧 graph_target_for_model 同口径）：system→ds:-1、business→ds:0（跨源聚合图）。
 *  严禁拿 datasource_id 当图哨兵：业务模型的 ds:N 并不存在，查了必空。 */
function namedGraphDs(kind?: string, datasourceId?: number): number {
  if (kind === 'system') return -1;
  if (kind === 'business') return 0;
  const ds = Number(datasourceId || 0);
  return ds === -1 ? -1 : ds;
}

/** 只读面板只放行查询类语句：UPDATE/INSERT 之类不在界面开口子，图的重建走「图谱生成」。 */
function isReadOnlySparql(q: string): boolean {
  const body = q
    .split('\n')
    .filter((l) => !/^\s*(#|PREFIX\b|BASE\b)/i.test(l))
    .join('\n')
    .trim();
  return /^(SELECT|ASK|CONSTRUCT|DESCRIBE)\b/i.test(body);
}

export default function CanonicalDocPanel({ open, onOpenChange, modelId, modelName,
  modelKind, datasourceId,
  liveVersion, draftCount = 0, objectOptions = [], onOpenGraph, onOpenDraft }: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  modelId?: number;
  modelName?: string;
  /** 模型类型：决定 SPARQL 打哪个命名图（system→ds:-1、business→ds:0） */
  modelKind?: string;
  datasourceId?: number;
  /** 生效版版本标识（doc.model_version，缺省不显示 v 号） */
  liveVersion?: string;
  /** 未发布草案的变更项数（0 = 无草案） */
  draftCount?: number;
  /** 对象清单（定位对象下拉）：展示业务名，定位用 key */
  objectOptions?: { key: string; name?: string }[];
  /** 「切到图谱页签」：本页图谱已内嵌，回调交给调用方切页签并关闭本面板 */
  onOpenGraph?: () => void;
  /** 「草案 · N 项」：草案正文不在此渲染，跳「变更对比」 */
  onOpenDraft?: () => void;
}) {
  const { t } = useTranslation('ui');
  /** 第二档：文档预览 / SPARQL 查询（同一份事实源的图投影） */
  const [pane, setPane] = useState<'doc' | 'sparql'>('doc');
  const [as, setAs] = useState<DocAs>('json');
  // 每档单独缓存（前端侧）：切档不重复请求，刷新只丢当前档
  const [docs, setDocs] = useState<Record<string, { text: string; cached: boolean }>>({});
  /** 已取档位的 ref 镜像：effect 里用它判“是否已有”，避免把 docs 放进依赖（会把 effect 自触发成重复请求） */
  const docsRef = useRef<Record<string, { text: string; cached: boolean }>>({});
  const [error, setError] = useState('');
  const [tick, setTick] = useState(0);
  const [copied, setCopied] = useState(false);
  const [hlLine, setHlLine] = useState(-1);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // SPARQL 查询台（结果不跨模型保留，避免拿 A 模型的查询结果当成 B 模型的）
  const [sparqlQ, setSparqlQ] = useState('');
  const [sparqlRes, setSparqlRes] = useState<any>(null);
  const [sparqlErr, setSparqlErr] = useState('');
  const [sparqlRunning, setSparqlRunning] = useState(false);
  const graphDs = namedGraphDs(modelKind, datasourceId);
  const sparqlExamples = useMemo(() => {
    const G = `<http://ai-datahub.org/ontology/ds:${graphDs}>`;
    const pre = 'PREFIX adh: <http://ai-datahub.org/ontology/>\n'
      + 'PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n'
      + 'PREFIX owl: <http://www.w3.org/2002/07/owl#>\n';
    return [
      { label: t('k_ec2bfeda49'),
        query: `${pre}SELECT ?s ?p ?o WHERE { GRAPH ${G} { ?s ?p ?o } } LIMIT 50` },
      { label: t('k_aeba1b9334'),
        query: `${pre}SELECT ?obj ?name WHERE { GRAPH ${G} { ?obj a owl:Class ; rdfs:label ?name } } LIMIT 100` },
      { label: t('k_8355e02aff'),
        query: `${pre}SELECT ?from ?to ?type WHERE { GRAPH ${G} { ?l a adh:Link ; adh:linkFrom ?from ; adh:linkTo ?to ; adh:linkType ?type } } LIMIT 100` },
    ];
  }, [graphDs, t]);

  const runSparql = async () => {
    const q = sparqlQ.trim();
    if (!q) return;
    if (!isReadOnlySparql(q)) {
      setSparqlRes(null);
      setSparqlErr(t('k_c08ace30a8'));
      return;
    }
    setSparqlRunning(true);
    setSparqlRes(null);
    setSparqlErr('');
    try {
      const { data } = await client.post('/graph/query', { query: q });
      setSparqlRes(data);
    } catch (e: any) {
      // 查询失败必须显式回错（含服务端原因），不显示空结果表假装“0 行”
      setSparqlErr(String(e?.response?.data?.detail || e?.message || t('k_9b2b07bcae')));
    } finally {
      setSparqlRunning(false);
    }
  };

  // 换模型：前端缓存全部作废（否则会拿上一个模型的正文）
  useEffect(() => {
    docsRef.current = {};
    setDocs({}); setError(''); setHlLine(-1);
    setSparqlQ(''); setSparqlRes(null); setSparqlErr('');
  }, [modelId]);
  // 关闭面板：不保留高亮位置
  useEffect(() => { if (!open) { setHlLine(-1); setCopied(false); } }, [open]);

  useEffect(() => {
    if (!open || !modelId) return;
    if (docsRef.current[as]) return;
    let cancelled = false;
    setError('');
    ontologyApi.docView(modelId, as)
      .then(({ data }) => {
        if (cancelled) return;
        const next = { ...docsRef.current, [as]: { text: data?.text ?? '', cached: !!data?.cached } };
        docsRef.current = next;
        setDocs(next);
      })
      .catch((e: any) => {
        // 取不到文档必须显式报错（含原因），不得显示空白正文假装“文档是空的”
        if (!cancelled) setError(String(e?.response?.data?.detail || e?.message || t('k_4bd89c5430', { p0: '' })));
      });
    return () => { cancelled = true; };
    // 只在「打开面板 / 换模型 / 换档 / 手动刷新」时拉取；docs 用 ref 判在，不进依赖
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, modelId, as, tick]);

  const current = docs[as];
  /** 正文未到又未报错 = 服务端正在拼（hydrate 较慢）；不单独存 loading，避免取消竞态把它卡在「计算中」 */
  const loading = !current && !error;
  const text = current?.text ?? '';
  const lines = useMemo(() => (text ? text.split('\n') : []), [text]);

  const refresh = () => {
    const next = { ...docsRef.current };
    delete next[as];
    docsRef.current = next;
    setDocs(next);
    setTick((x) => x + 1);
  };
  const copy = async () => {
    if (!current) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      toast.success(t('k_e381a5763d'));
      setTimeout(() => setCopied(false), 1500);
    } catch (e: any) {
      toast.error(String(e?.message || t('k_4bd89c5430', { p0: '' })));
    }
  };

  /** 定位对象：在正文里找该 key 的首现行（JSON 找 `"key": "x"`，YAML/MD 找首个出现），滚到该行并高亮 */
  const locate = (key: string) => {
    if (!key || !lines.length) return;
    const needle = as === 'json' ? `"key": "${key}"` : key;
    let idx = lines.findIndex((l) => l.includes(needle));
    if (idx < 0) idx = lines.findIndex((l) => l.includes(key));
    if (idx < 0) {
      toast.error(t('k_14a7337b34'));
      return;
    }
    setHlLine(idx);
    const box = scrollRef.current;
    if (box) box.scrollTop = Math.max(0, idx * LINE_H - 60);
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex">
      {/* 左侧留出上下文（不抢焦点，点击关闭） */}
      <div className="flex-1 bg-background/40 backdrop-blur-[1px]" onClick={() => onOpenChange(false)} />
      <div className="flex h-full w-[min(1180px,calc(100vw-260px))] flex-col border-l border-border bg-background shadow-2xl">
        {/* ── 头：标题 / 事实源 / 生效版·草案 / 打开图谱 ── */}
        <div className="shrink-0 space-y-2 border-b px-4 py-3">
          <div className="flex items-start gap-2">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                <span className="text-sm font-semibold">{t('k_c6ae4b79a2')}</span>
                {modelName && <Badge variant="secondary" className="shrink-0 text-[10px]">{modelName}</Badge>}
                <Badge variant="outline" className="shrink-0 text-[10px] font-normal">{t('k_ffc1d065fb')}</Badge>
              </div>
              <div className="mt-1 text-[11px] text-muted-foreground">
                {t('k_296a884064').split('adh_ontology_models.json_content').map((seg, i) => (
                  <span key={i}>{i > 0 && <code className="rounded bg-muted px-1 font-mono">adh_ontology_models.json_content</code>}{seg}</span>
                ))}
              </div>
            </div>
            <button className="ml-auto shrink-0 rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
              title={t('k_4d0b4688c7')} onClick={() => onOpenChange(false)}>
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex items-center gap-0.5 rounded-md bg-muted/60 p-0.5">
              <span className="rounded bg-background px-2 py-0.5 text-xs font-medium shadow-sm">
                {liveVersion ? t('k_af38917e34', { p0: liveVersion }) : t('k_539cb3b0a6')}
              </span>
              <button type="button" onClick={onOpenDraft} disabled={!draftCount}
                title={draftCount ? t('k_f48a88596c') : ''}
                className={`rounded px-2 py-0.5 text-xs ${draftCount
                  ? 'text-amber-600 hover:bg-background'
                  : 'cursor-default text-muted-foreground/60'}`}>
                {t('k_db26b6b321', { p0: draftCount })}
              </button>
            </div>
            {loading && (
              <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
                <Loader2 className="h-3 w-3 animate-spin" />{t('k_7011734430')}
              </span>
            )}
            {!loading && current?.cached && (
              <Badge variant="outline" className="h-4 border-green-500/40 px-1 text-[10px] text-green-600">
                {t('k_6cdba4aad0')}
              </Badge>
            )}
            <button type="button" onClick={onOpenGraph}
              className="ml-auto text-[11px] text-primary hover:underline">
              {t('k_4310fcb599')} ↗
            </button>
          </div>
        </div>

        {/* ── 档位 + 定位/复制/刷新（定位/复制/刷新只对文档档位有意义，SPARQL 档不显示） ── */}
        <div className="flex shrink-0 flex-wrap items-center gap-1 border-b px-4 py-1.5">
          {DOC_FORMATS.map((f) => (
            <button key={f.key} type="button" onClick={() => { setPane('doc'); setAs(f.key); setHlLine(-1); }}
              className={`h-7 px-2.5 text-xs transition-colors ${pane === 'doc' && as === f.key
                ? 'border-b-2 border-primary font-medium text-primary'
                : 'text-muted-foreground hover:text-foreground'}`}>
              {f.key === 'md' ? t('k_6af87bfe5d') : f.label}
            </button>
          ))}
          {/* SPARQL 档位打的是 POST /graph/query（可执 update），服务端归 graph:manage；无码不渲染 */}
          {hasPerm('graph:manage') && (
            <button type="button" onClick={() => { setPane('sparql'); setHlLine(-1); }}
              className={`h-7 px-2.5 text-xs transition-colors ${pane === 'sparql'
                ? 'border-b-2 border-primary font-medium text-primary'
                : 'text-muted-foreground hover:text-foreground'}`}>
              {t('k_5e42489108')}
            </button>
          )}
          {pane === 'doc' && (
            <span className="ml-2 text-[11px] text-muted-foreground">{lines.length ? `${lines.length}` : ''}</span>
          )}
          <div className={`ml-auto flex items-center gap-1 ${pane === 'doc' ? '' : 'hidden'}`}>
            <select
              className="h-7 w-40 rounded-md border border-border bg-background px-1.5 text-xs text-muted-foreground"
              defaultValue="" disabled={!objectOptions.length}
              onChange={(e) => locate(e.target.value)}>
              <option value="" disabled>{t('k_14a7337b34')}</option>
              {objectOptions.map((o) => (
                <option key={o.key} value={o.key}>{o.name || o.key}</option>
              ))}
            </select>
            <Button size="sm" variant="ghost" className="h-7 px-2 text-xs" onClick={copy} disabled={!current}>
              {copied ? <Check className="mr-1 h-3.5 w-3.5" /> : <Copy className="mr-1 h-3.5 w-3.5" />}
              {copied ? t('k_e381a5763d') : t('k_4edd1d0087')}
            </Button>
            <Button size="sm" variant="ghost" className="h-7 px-2 text-xs" onClick={refresh} disabled={loading}>
              <RotateCw className={`mr-1 h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
              {t('k_38108eaa1d')}
            </Button>
          </div>
        </div>

        {/* ── 正文：文档档 = 行号 + 等宽正文（长文档不逐行包 DOM）；SPARQL 档 = 查询台 ── */}
        {pane === 'sparql' ? (
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="shrink-0 space-y-1.5 border-b px-4 py-2">
              <div className="text-[11px] text-muted-foreground">{t('k_30fd7d23e6', { p0: graphDs })}</div>
              <div className="flex flex-wrap items-center gap-1.5">
                {sparqlExamples.map((ex) => (
                  <button key={ex.label} type="button" onClick={() => setSparqlQ(ex.query)}
                    className="rounded-md border border-border px-2 py-0.5 text-[11px] transition-colors hover:bg-muted/60">
                    {ex.label}
                  </button>
                ))}
                <Button size="sm" variant="outline" className="ml-auto h-7 px-2 text-xs"
                  onClick={runSparql} disabled={sparqlRunning || !sparqlQ.trim()}>
                  {sparqlRunning
                    ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                    : <Play className="mr-1 h-3.5 w-3.5" />}
                  {t('k_4fb36a6129')}
                </Button>
              </div>
            </div>
            <textarea
              value={sparqlQ} onChange={(e) => setSparqlQ(e.target.value)} spellCheck={false}
              placeholder={t('k_5b833f3d46')}
              className="h-36 shrink-0 w-full resize-y border-b border-border bg-muted/20 px-3 py-2 font-mono text-[11.5px] leading-[18px] outline-none" />
            <div className="min-h-0 flex-1 overflow-auto">
              {sparqlErr ? (
                <div className="p-4 text-xs text-red-500">{sparqlErr}</div>
              ) : !sparqlRes ? (
                <div className="flex h-full items-center justify-center px-6 text-center text-[11px] text-muted-foreground">
                  {t('k_30fd7d23e6', { p0: graphDs })}
                </div>
              ) : (
                <div className="space-y-1 p-3">
                  {Array.isArray(sparqlRes.records) && sparqlRes.records.length ? (
                    <table className="w-full border-collapse text-[11.5px]">
                      <thead>
                        <tr className="bg-muted/50 text-left">
                          {Object.keys(sparqlRes.records[0]).map((k) => (
                            <th key={k} className="border-b px-2 py-1 font-medium">{k}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {sparqlRes.records.map((r: any, i: number) => (
                          <tr key={i} className="border-b last:border-0">
                            {Object.keys(sparqlRes.records[0]).map((k) => (
                              <td key={k} className="whitespace-pre-wrap break-all px-2 py-1 align-top">
                                {sparqlCell(r?.[k])}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    <pre className="text-[11.5px]">{JSON.stringify(sparqlRes, null, 2)}</pre>
                  )}
                  <div className="text-[11px] text-muted-foreground">
                    {t('k_bfe63997e6', { p0: sparqlRes.count ?? 0 })}
                  </div>
                </div>
              )}
            </div>
          </div>
        ) : (
          <div ref={scrollRef} className="relative min-h-0 flex-1 overflow-auto bg-muted/20">
            {error ? (
              <div className="space-y-2 p-4 text-xs text-red-500">
                <div>{t('k_4bd89c5430', { p0: error })}</div>
                <Button size="sm" variant="outline" className="h-7 text-xs" onClick={refresh}>
                  {t('k_e2d53a6d3a')}
                </Button>
              </div>
            ) : !current ? (
              <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />{t('k_7011734430')}
              </div>
            ) : (
              <>
                <div className="flex min-w-full font-mono text-[11.5px] leading-[18px]">
                  <pre className="sticky left-0 shrink-0 select-none border-r border-border bg-muted/30 px-2 py-2 text-right text-muted-foreground/60 tabular-nums">
                    {lines.map((_, i) => i + 1).join('\n')}
                  </pre>
                  <pre className="flex-1 whitespace-pre px-3 py-2">{text}</pre>
                </div>
                {hlLine >= 0 && hlLine * LINE_H < 200000 && (
                  <div className="pointer-events-none absolute inset-x-0 h-[18px] bg-amber-400/20"
                    style={{ top: 8 + hlLine * LINE_H }} />
                )}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
