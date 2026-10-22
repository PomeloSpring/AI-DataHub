import { useEffect, useState } from 'react';
import { HelpCircle } from 'lucide-react';

/**
 * 画布左下角操作提示：默认 8s 后折叠成 `?` 图标，hover 再展开。
 *
 * 原先是一条常驻文案永久占住画布下沿，与标尺、内容抢画面；折叠后信息仍在、噪声归零。
 */
export default function CanvasHint({ text, extra }: { text: string; extra?: string }) {
  const [collapsed, setCollapsed] = useState(false);
  useEffect(() => {
    const timer = window.setTimeout(() => setCollapsed(true), 8000);
    return () => window.clearTimeout(timer);
  }, []);
  if (collapsed) {
    return <button type="button" aria-label="画布快捷键" title={text}
      className="absolute bottom-3 left-8 z-30 rounded-full border bg-background/95 p-1.5 text-muted-foreground shadow-sm hover:text-foreground"
      onMouseEnter={() => setCollapsed(false)} onFocus={() => setCollapsed(false)}>
      <HelpCircle className="h-4 w-4" />
    </button>;
  }
  return <div className="pointer-events-none absolute bottom-3 left-8 z-30 max-w-[calc(100%-4rem)] rounded border bg-background/95 px-3 py-1.5 text-xs shadow-sm"
    onMouseLeave={() => setCollapsed(true)}>
    {text}
    {extra && <span className="ml-3 text-muted-foreground">{extra}</span>}
  </div>;
}
