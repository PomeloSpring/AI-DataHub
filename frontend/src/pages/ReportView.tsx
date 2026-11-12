import { useState, useEffect } from 'react';
import ReportContent from '@/components/ReportContent';
import { WIDE } from '@/lib/pageWidth';
import { fetchReportContent } from '@/api/report';
import { useParams, useSearchParams } from 'react-router-dom';
import { Badge } from '@/components/ui/badge';
import { Lock, Eye } from 'lucide-react';
import client from '@/api/client';
import { useThemeStore, applyTheme } from '../stores/themeStore';

interface Report {
  id: number;
  task_id: number;
  title: string;
  /** 正文直链（对象存储签名 URL 或同源代理路径）；响应体不再带 content */
  content_url?: string;
  format: string;
  access_mode: string;
  view_count: number;
  created_at: string;
  access_warning?: string;
  generation_status?: string;
}

export default function ReportView() {
  const { reportId } = useParams<{ reportId: string }>();
  const [searchParams] = useSearchParams();
  const token = searchParams.get('token');
  const { theme } = useThemeStore();

  useEffect(() => { applyTheme(theme); }, [theme]);

  // Override #root overflow to allow page scrolling
  useEffect(() => {
    const root = document.getElementById('root');
    if (root) {
      root.style.height = 'auto';
      root.style.overflow = 'auto';
    }
    return () => {
      if (root) {
        root.style.height = '100vh';
        root.style.overflow = 'hidden';
      }
    };
  }, []);

  const [report, setReport] = useState<Report | null>(null);
  const [body, setBody] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!reportId) return;
    let cancelled = false;
    setLoading(true);
    const params: any = {};
    if (token) params.token = token;

    let timer: ReturnType<typeof setTimeout> | undefined;
    // 与报告中心同一取正文口径（api/report.fetchReportContent）：这里不再自己拼一套
    const loadBody = (data: Report) => {
      if (!data.content_url) return Promise.resolve();
      return fetchReportContent(data.content_url).then(setBody).catch(e => {
        if (!cancelled) setError(e?.message || '读取报告正文失败');
      });
    };
    const refresh = () => client.get(`/reports/${reportId}`, { params })
      .then(({ data }) => {
        if (cancelled) return;
        setReport(data);
        setBody('');
        const pending = ['queued', 'running'].includes(data.generation_status);
        setError(pending ? '' : data.access_warning || '');
        if (pending) { timer = setTimeout(refresh, 2000); return; }
        return loadBody(data);
      })
      .catch(e => {
        if (!cancelled) setError(e?.response?.data?.detail || '加载失败');
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    refresh();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [reportId, token]);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="text-muted-foreground">加载中...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="text-center space-y-4">
          <Lock className="w-12 h-12 mx-auto text-muted-foreground" />
          <h1 className="text-xl font-bold">无法访问</h1>
          <p className="text-muted-foreground">{error}</p>
          <p className="text-sm text-muted-foreground">请确认登录身份、分享有效期及报告生成状态</p>
        </div>
      </div>
    );
  }

  if (!report) return null;

  return (
    // 与阅读页同一口径：#root 是 100vh + overflow:hidden，报告页必须自带滚动口，
    // 否则长报告超出视口的部分滚不到（报告恰恰是最长的那类内容）
    <div data-page-width="wide" className="flex h-full min-h-0 flex-col bg-background text-foreground">
      {/* Header */}
      <header className="z-10 border-b bg-background/80 backdrop-blur">
        <div className={`${WIDE} flex h-12 items-center justify-between`}>
          <span className="font-bold text-sm">AI-DataHub 报告</span>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Eye className="w-3 h-3" />
            {report.view_count} 次查看
          </div>
        </div>
      </header>

      <main className="relative min-h-0 flex-1 overflow-auto">
      {/* Report Meta */}
      <div className={`${WIDE} py-6`}>
        <h1 className="text-2xl font-bold">{report.title}</h1>
        <div className="flex items-center gap-2 mt-2">
          <Badge variant="outline">{report.format === 'html' ? 'HTML' : 'Markdown'}</Badge>
          {report.generation_status && (
            <Badge variant="outline">{({ queued: '排队中', running: '生成中', ready: '已就绪', degraded: '降级事实报告', failed: '生成失败', timeout: '生成超时' } as Record<string, string>)[report.generation_status] || report.generation_status}</Badge>
          )}
          <Badge variant="outline" className={report.access_mode === 'public' ? 'text-green-500' : 'text-orange-500'}>
            {report.access_mode === 'public' ? '公开' : '私有'}
          </Badge>
          <span className="text-xs text-muted-foreground">
            {new Date(report.created_at).toLocaleString()}
          </span>
        </div>
      </div>

      {/* Report Content —— 数据报告不挤在中间一列：宽表与图表需要可用宽度（与阅读页同一口径） */}
      <div className={`${WIDE} pb-12`}>
        {['queued', 'running'].includes(report.generation_status || '') ? (
          <p className="text-muted-foreground">报告尚未完成，页面会自动更新执行状态。</p>
        ) : error && !body ? (
          <p className="text-destructive text-sm">{error}</p>
        ) : !body ? (
          <p className="text-muted-foreground">加载报告正文...</p>
        ) : (
          <div data-report-print><ReportContent content={body} format={report.format} /></div>
        )}
      </div>
      </main>
    </div>
  );
}
