/**
 * 回答转出的文档卡片（会话内入口，参照千问「已为你把消息转换成文档」）。
 *
 * 卡片只显示可读信息（标题 + 创建时间），点击进编辑面板；
 * 文档正文随会话消息落库，不另建资产表，因此这里不给"删除"之类入口（先不动）。
 */
import { FileType } from 'lucide-react';
import { formatDocTime } from '@/lib/markdownText';

interface Props {
  title: string;
  createdAt: string;
  onOpen: () => void;
}

export default function DocCard({ title, createdAt, onOpen }: Props) {
  const created = formatDocTime(createdAt, '创建于');
  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex w-full max-w-md items-center gap-3 rounded-xl border bg-card px-3 py-2.5 text-left shadow-sm transition-colors hover:border-primary/50 hover:bg-accent/40"
      title="打开编辑（可继续修改、复制或下载）"
    >
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
        <FileType className="h-5 w-5" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium">{title || '无标题'}</span>
        {created && <span className="block text-xs text-muted-foreground">{created}</span>}
      </span>
    </button>
  );
}
