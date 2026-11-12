import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { ChevronRight, FolderTree, RefreshCw, Sparkles } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { kbDocError, kbDocsApi, kbTypeLabel, type KbBase } from '@/api/kbDocs';
import { EmptyState, ErrorBanner, LoadingRow, useContentPerms } from './shared';

/**
 * 知识库文档 — 清单页（内容中心「知识运营」入口）。
 *
 * 知识库是**一个目录**（云端 notebook 的 source 树），不是一个文件，所以这里只做
 * "选库"：卡片列出已登记的知识库，点进详情才拉目录树与文档（`/content/kb-docs/:kb`）。
 *
 * 与系统配置里的「知识库」是两件事，别混：那里管**库本身**（建库、绑 AS-BOT、配凭据），
 * 这里管**库里面的文档**（kbdoc:read / kbdoc:write）。
 *
 * 三条口径：
 * 1. 卡片只显示**库级事实**（名称/描述/类型/状态/能不能用）。篇数不在这里显示 ——
 *    那需要逐库调云端 CLI 列举（每库一次子进程），清单页会被拖慢；进详情才拉树。
 * 2. 不支持文档树的类型**照样列出来**并写明原因（藏起来等于把"本批没做"演成"库没了"）。
 * 3. 清单拉失败必须显式报错并可重试，不回空卡片列表（no-silent-degradation）。
 */
export default function KnowledgeDocs() {
  const { has } = useContentPerms();
  const canWrite = has('kbdoc:write');

  const [bases, setBases] = useState<KbBase[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(() => {
    setLoading(true); setError('');
    kbDocsApi.bases()
      .then(res => setBases(res.data.items || []))
      .catch(err => { setBases([]); setError(kbDocError(err)); })
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-xl font-semibold">知识库文档</h1>
          <p className="text-sm text-muted-foreground">
            选择一个知识库进入，按目录树查看与编辑其中的文档（AS-BOT 检索的语料）。
            这里改的是 AI 的知识来源，不是门户发布内容 —— 因此不走内容审核流，但每次写入都会落审计。
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={load} disabled={loading}>
          <RefreshCw className={`mr-1 h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />刷新清单
        </Button>
      </header>

      <div className="flex flex-wrap items-center gap-2 rounded-xl border bg-muted/30 p-3 text-xs leading-5">
        <Sparkles className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <span className="text-muted-foreground">
          保存只提交<b>当前改动的这一篇</b>，其余文档不动；云端没有"原地更新"接口，所以保存后该文档的标识会更换
          （云端需重新解析才能被检索命中）。<b>改名 / 移动 / 删除</b>本批未开放（云端无回收站），请到 Qoder 端维护；
          本体建模自动上云的文档是派生物，只读。建库与绑定 AS-BOT 在「系统配置 → 知识库」。
        </span>
      </div>

      <ErrorBanner message={error} onRetry={load} />

      {loading && <LoadingRow text="正在加载知识库清单" />}

      {!loading && !error && bases.length === 0 && (
        <EmptyState
          title="还没有登记的知识库"
          hint="先到「系统配置 → 知识库」从 Qoder 导入或新建一个知识库，这里才会列出它。"
        />
      )}

      {!loading && !error && (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {bases.map(b => {
            const openable = b.supports_docs;
            return (
              <Link
                key={b.name}
                to={openable ? `/content/kb-docs/${encodeURIComponent(b.name)}` : '#'}
                aria-disabled={!openable}
                tabIndex={openable ? 0 : -1}
                aria-label={`进入知识库 ${b.name}`}
                className={`flex flex-col gap-2 rounded-xl border bg-card p-4 text-left transition-colors
                  ${openable ? 'hover:border-primary/60 hover:shadow-sm' : 'cursor-not-allowed opacity-70'}`}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <FolderTree className="h-4 w-4 shrink-0 text-primary" />
                  <span className="min-w-0 flex-1 truncate text-sm font-medium" title={b.name}>{b.name}</span>
                  <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">
                    {kbTypeLabel(b.kb_type)}
                  </span>
                  <span className={`shrink-0 rounded px-1.5 py-0.5 text-[11px]
                    ${b.status === 'active'
                      ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400'
                      : 'bg-amber-500/15 text-amber-700 dark:text-amber-400'}`}>
                    {b.status === 'active' ? '已启用' : `未启用（${b.status || '未知'}）`}
                  </span>
                </div>

                <p className="line-clamp-2 text-xs leading-5 text-muted-foreground">
                  {b.description || '（这个库还没有描述）'}
                </p>

                <div className="mt-auto flex items-center justify-between gap-2 pt-1 text-[11px]">
                  {b.reason ? (
                    <span className="text-amber-700 dark:text-amber-400">{b.reason}</span>
                  ) : (
                    <span className="text-muted-foreground">
                      {/* 能不能改由权限码裁决；只有读权时如实说明，不给假的可编辑暗示 */}
                      {canWrite ? '可查看与编辑语料' : '只有查看权（kbdoc:read）'}
                    </span>
                  )}
                  {openable && <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />}
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
