import { useEffect, useRef, useState } from 'react';
import mermaid from 'mermaid';
import { Download, ZoomIn, ZoomOut, RotateCcw } from 'lucide-react';
import { Button } from '@/components/ui/button';

// 初始化 mermaid 配置
mermaid.initialize({
  startOnLoad: false,
  theme: 'default',
  securityLevel: 'loose',
  fontFamily: 'inherit',
});

interface Props {
  code: string;
  title?: string;
}

export default function MermaidBlock({ code, title }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [svg, setSvg] = useState('');
  const [error, setError] = useState('');
  const [scale, setScale] = useState(1);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    setError('');

    const render = async () => {
      try {
        const id = `mermaid-${Math.random().toString(36).slice(2, 9)}`;
        const { svg: renderedSvg } = await mermaid.render(id, code);
        if (mounted) {
          setSvg(renderedSvg);
          setLoading(false);
        }
      } catch (e: any) {
        if (mounted) {
          setError(e?.message || '渲染失败');
          setLoading(false);
        }
      }
    };

    render();
    return () => { mounted = false; };
  }, [code]);

  const handleDownload = (format: 'svg' | 'png') => {
    if (!svg) return;
    if (format === 'svg') {
      const blob = new Blob([svg], { type: 'image/svg+xml' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${title || 'diagram'}.svg`;
      a.click();
      URL.revokeObjectURL(url);
    } else {
      // PNG 导出:将 SVG 绘制到 canvas
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      const img = new Image();
      const svgBlob = new Blob([svg], { type: 'image/svg+xml;charset=utf-8' });
      const url = URL.createObjectURL(svgBlob);
      img.onload = () => {
        canvas.width = img.width * 2;
        canvas.height = img.height * 2;
        ctx.scale(2, 2);
        ctx.drawImage(img, 0, 0);
        canvas.toBlob((blob) => {
          if (blob) {
            const pngUrl = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = pngUrl;
            a.download = `${title || 'diagram'}.png`;
            a.click();
            URL.revokeObjectURL(pngUrl);
          }
        }, 'image/png');
        URL.revokeObjectURL(url);
      };
      img.src = url;
    }
  };

  return (
    <div className="my-3 rounded-lg border bg-card overflow-hidden">
      {title && (
        <div className="px-3 py-2 border-b bg-muted/50 flex items-center justify-between">
          <span className="text-sm font-semibold">{title}</span>
          <div className="flex items-center gap-1">
            <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setScale(s => Math.min(s + 0.2, 3))}>
              <ZoomIn className="h-3.5 w-3.5" />
            </Button>
            <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setScale(s => Math.max(s - 0.2, 0.3))}>
              <ZoomOut className="h-3.5 w-3.5" />
            </Button>
            <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setScale(1)}>
              <RotateCcw className="h-3.5 w-3.5" />
            </Button>
            <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => handleDownload('svg')} title="下载 SVG">
              <Download className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      )}
      <div className="p-3 overflow-auto" style={{ minHeight: 100 }}>
        {loading ? (
          <div className="flex items-center justify-center h-32 text-muted-foreground text-sm">渲染中...</div>
        ) : error ? (
          <div className="p-3 bg-destructive/10 text-destructive text-sm rounded">
            <div className="font-medium mb-1">Mermaid 渲染失败</div>
            <pre className="text-xs whitespace-pre-wrap font-mono">{error}</pre>
          </div>
        ) : (
          <div
            ref={containerRef}
            className="flex justify-center transition-transform origin-top"
            style={{ transform: `scale(${scale})` }}
            dangerouslySetInnerHTML={{ __html: svg }}
          />
        )}
      </div>
      {!title && svg && (
        <div className="px-3 py-2 border-t bg-muted/30 flex justify-end gap-1">
          <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={() => handleDownload('svg')}>
            <Download className="h-3 w-3 mr-1" />SVG
          </Button>
          <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={() => handleDownload('png')}>
            <Download className="h-3 w-3 mr-1" />PNG
          </Button>
        </div>
      )}
    </div>
  );
}
