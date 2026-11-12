import { useRef, useState } from 'react';
import { Download, Paperclip, Trash2, Upload } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { formatBytes } from '@/api/monitoring';
import {
  CONTENT_FILE_ACCEPT, CONTENT_FILE_MAX_COUNT, CONTENT_FILE_MAX_MB,
  contentApi, contentError, downloadContentFile, type ContentFile,
} from '@/api/content';
import { ErrorBanner, NoticeBanner } from './shared';

/** 扩展名白名单（从 CONTENT_FILE_ACCEPT 派生，不另列一份清单） */
const ALLOWED_EXT = CONTENT_FILE_ACCEPT.split(',').map(s => s.trim().toLowerCase());

const extOf = (name: string) => {
  const i = name.lastIndexOf('.');
  return i < 0 ? '' : name.slice(i).toLowerCase();
};

/**
 * 内容附件面板（下载卡 + 可选的上传/删除）。
 *
 * 编辑器 / 预览 / 审核三处共用同一份，理由不是省事而是**判据必须一致**：审核页
 * 若自己写一版"只列文件名不给下载"的简化卡片，就出现"队列里标明是附件稿、点进去
 * 一个文件都下不下来"的空承诺。
 *
 * 三条不能省的约束：
 * 1. **没有预览按钮**。服务端下载头恒为 attachment（把上传的 HTML/PDF 以本站源
 *    inline 渲染是存储型 XSS），所以这里只给下载 —— 画一个预览再让它失灵更坑。
 * 2. **slug 为空 = 条目还没落库**（新建未保存）。这时上传按钮要禁用并说清原因，
 *    不能假装能传：附件按条目归属，服务端没有"先传后挂"的暂存区。
 * 3. **附件不进版本链**。表里没有 revision_id，换文件不会把已发布内容打回草稿，
 *    门户下载立即变。这个事实必须写在界面上，不能让作者以为附件也走了审核。
 */
export default function ArticleFiles({ slug, files, scope, editable = false, onRefresh }: {
  /** 空 = 条目还没保存，只能看不能传 */
  slug?: string;
  files: ContentFile[];
  /** 'articles' = 编排面（article:manage），'reviews' = 审核面（article:review） */
  scope: 'articles' | 'reviews';
  editable?: boolean;
  /** 上传/删除后让父层重拉详情（附件清单随详情带出，不在本组件另存一份真值） */
  onRefresh?: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const pick = () => {
    setError(''); setNotice('');
    if (!slug) return;   // 按钮此时已禁用，这里只防调用方把 editable 传错
    inputRef.current?.click();
  };

  const upload = async (file: File) => {
    setError(''); setNotice('');
    // 客户端先挡一层是省一次往返，不是裁决：服务端还会按同一份白名单再判一次
    if (!ALLOWED_EXT.includes(extOf(file.name))) {
      setError(`不支持的附件类型「${extOf(file.name) || '无扩展名'}」。只收 ${ALLOWED_EXT.join(' ')}`);
      return;
    }
    if (file.size > CONTENT_FILE_MAX_MB * 1024 * 1024) {
      setError(`「${file.name}」${formatBytes(file.size)}，超过单文件上限 ${CONTENT_FILE_MAX_MB}MB`);
      return;
    }
    if (!slug) { setError('请先保存草稿再上传附件'); return; }
    setBusy(true);
    try {
      const res = await contentApi.uploadFile(slug, file);
      setNotice(res.data.message);
      onRefresh?.();
    } catch (err) {
      setError(contentError(err));
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = '';
    }
  };

  const remove = async (f: ContentFile) => {
    if (!slug) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const res = await contentApi.removeFile(slug, f.file_id);
      // 对象没删干净时服务端会带 warning：存储里的残留文件不影响展示，但必须说出来
      // （不得拿它顶掉“已删除”这个结论，也不得只报成功而吞掉残留）
      setNotice(`已删除附件「${res.data.filename}」`
        + (res.data.warning ? ` —— ${res.data.warning}` : ''));
      onRefresh?.();
    } catch (err) {
      setError(contentError(err));
    } finally {
      setBusy(false);
    }
  };

  const download = async (f: ContentFile) => {
    if (!slug) return;
    setError('');
    try {
      await downloadContentFile(scope, slug, f);
    } catch (err) {
      setError(contentError(err));
    }
  };

  const full = files.length >= CONTENT_FILE_MAX_COUNT;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <PanelTitle />
        {editable && (
          <>
            <Button size="sm" variant="outline" onClick={pick} disabled={busy || full || !slug}>
              <Upload className="mr-1.5 h-3.5 w-3.5" />{busy ? '上传中' : '上传附件'}
            </Button>
            <input
              ref={inputRef} type="file" className="hidden" aria-label="选择附件文件"
              accept={CONTENT_FILE_ACCEPT}
              onChange={e => { const f = e.target.files?.[0]; if (f) void upload(f); }}
            />
          </>
        )}
      </div>

      <ErrorBanner message={error} />
      <NoticeBanner message={notice} onDismiss={() => setNotice('')} />

      {/* 未落库就直说“现在传不了、为什么”，而不是藏掉按钮让人以为附件功能没做 */}
      {editable && !slug && (
        <p className="text-xs text-amber-600">
          保存草稿后即可上传附件：附件挂在具体条目上，服务端没有“先传后挂”的暂存区。
        </p>
      )}

      {files.length === 0 ? (
        editable ? (
          <p className="text-xs text-muted-foreground">
            还没有附件。支持 Word / PPT / Excel / PDF / HTML，单个最大 {CONTENT_FILE_MAX_MB}MB，
            一条最多 {CONTENT_FILE_MAX_COUNT} 个；附件在门户里只提供下载，不做站内预览。
          </p>
        ) : <p className="text-xs text-muted-foreground">这篇没有附件。</p>
      ) : (
        <ul className="divide-y rounded-lg border">
          {files.map(f => (
            <li key={f.file_id} className="flex flex-wrap items-center gap-2 px-3 py-2 text-sm">
              <Paperclip className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <span className="rounded bg-muted px-1.5 py-0.5 text-[11px] font-medium">{f.label}</span>
              <span className="min-w-[120px] flex-1 truncate" title={f.filename}>{f.filename}</span>
              <span className="text-[11px] text-muted-foreground">{formatBytes(f.size)}</span>
              <span className="text-[11px] text-muted-foreground">{(f.uploaded_at || '').slice(0, 16).replace('T', ' ')}</span>
              <Button size="sm" variant="outline" onClick={() => void download(f)} disabled={!slug}>
                <Download className="mr-1 h-3.5 w-3.5" />下载
              </Button>
              {editable && (
                <Button size="sm" variant="ghost" className="text-destructive"
                  aria-label={`删除附件 ${f.filename}`} disabled={busy} onClick={() => void remove(f)}>
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}

      {editable && (
        <p className="text-[11px] text-muted-foreground">
          附件不进版本链：替换或删除会**立即**改变门户里能下载到的文件，不会把已发布内容
          打回草稿、也不会自动触发重新审核。换文件前请确认新文件本身就是可对外的那一版。
        </p>
      )}
    </div>
  );
}

/** 面板标题行（标题不硬编码在调用方，统一在这里出） */
function PanelTitle() {
  return <span className="text-sm font-medium">附件</span>;
}
