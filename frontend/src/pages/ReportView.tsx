import { useState, useEffect, useRef } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { Badge } from '@/components/ui/badge';
import { Lock, Eye } from 'lucide-react';
import client from '@/api/client';
import { useThemeStore, applyTheme } from '../stores/themeStore';

interface Report {
  id: number;
  task_id: number;
  title: string;
  content: string;
  format: string;
  access_mode: string;
  view_count: number;
  created_at: string;
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
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Use ref to prevent double-fetch in React StrictMode
  const fetchedRef = useRef(false);
  useEffect(() => {
    if (!reportId || fetchedRef.current) return;
    fetchedRef.current = true;
    setLoading(true);
    const params: any = {};
    if (token) params.token = token;

    client.get(`/reports/${reportId}`, { params })
      .then(({ data }) => {
        setReport(data);
        setError('');
      })
      .catch(e => {
        setError(e?.response?.data?.detail || '加载失败');
      })
      .finally(() => setLoading(false));
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
          <p className="text-sm text-muted-foreground">此报告为私有访问，需要有效的访问令牌</p>
        </div>
      </div>
    );
  }

  if (!report) return null;

  return (
    <div className="bg-background text-foreground">
      {/* Header */}
      <header className="border-b bg-background/80 backdrop-blur sticky top-0 z-10">
        <div className="max-w-4xl mx-auto px-4 h-12 flex items-center justify-between">
          <span className="font-bold text-sm">AI-DataHub 报告</span>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Eye className="w-3 h-3" />
            {report.view_count} 次查看
          </div>
        </div>
      </header>

      {/* Report Meta */}
      <div className="max-w-4xl mx-auto px-4 py-6">
        <h1 className="text-2xl font-bold">{report.title}</h1>
        <div className="flex items-center gap-2 mt-2">
          <Badge variant="outline">{report.format === 'html' ? 'HTML' : 'Markdown'}</Badge>
          <Badge variant="outline" className={report.access_mode === 'public' ? 'text-green-500' : 'text-orange-500'}>
            {report.access_mode === 'public' ? '公开' : '私有'}
          </Badge>
          <span className="text-xs text-muted-foreground">
            {new Date(report.created_at).toLocaleString()}
          </span>
        </div>
      </div>

      {/* Report Content */}
      <div className="max-w-4xl mx-auto px-4 pb-12">
        {report.format === 'html' ? (
          <article
            className="prose prose-sm max-w-none dark:prose-invert"
            dangerouslySetInnerHTML={{ __html: report.content }}
          />
        ) : (
          <article className="prose prose-sm max-w-none dark:prose-invert">
            <pre className="whitespace-pre-wrap text-sm leading-relaxed font-sans bg-transparent p-0">
              {report.content}
            </pre>
          </article>
        )}
      </div>
    </div>
  );
}
