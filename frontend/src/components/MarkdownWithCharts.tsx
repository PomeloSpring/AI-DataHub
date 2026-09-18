import { useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import ChartPicker from './ChartPicker';
import MermaidBlock from './MermaidBlock';
import ArtifactCard from './ArtifactCard';
import { splitChartBlocks } from '../lib/chartBlocks';

interface Props {
  text: string;
  className?: string;
}

/**
 * 渲染助手回答:普通文本走 ReactMarkdown,```chart 块解析后渲染 ChartPicker,
 * ```mermaid 块渲染流程图/时序图,```artifact 块渲染可下载文件产物。
 * 解析失败降级为代码块。图表内嵌于 content,天然随消息持久化与回放。
 */
export default function MarkdownWithCharts({ text, className }: Props) {
  const segments = useMemo(() => splitChartBlocks(text || ''), [text]);
  // prose 排版色已由 globals.css 令牌化(--tw-prose-* → 主题变量),自动随主题
  const proseCls = className || 'leading-relaxed prose prose-sm max-w-none';

  return (
    <>
      {segments.map((seg, i) => {
        if (seg.kind === 'markdown') {
          if (!seg.text.trim()) return null;
          return (
            <div key={i} className={proseCls}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{seg.text}</ReactMarkdown>
            </div>
          );
        }
        if (seg.kind === 'chart') {
          return (
            <div key={i} className="my-3 rounded-lg border p-3 bg-card">
              {seg.title && <div className="text-sm font-semibold mb-1">{seg.title}</div>}
              <ChartPicker data={seg.data} defaultType={seg.chartType} sql={seg.sql} />
            </div>
          );
        }
        if (seg.kind === 'mermaid') {
          return <MermaidBlock key={i} code={seg.code} title={seg.title} />;
        }
        if (seg.kind === 'artifact') {
          return (
            <ArtifactCard
              key={i}
              type={seg.type}
              filename={seg.filename}
              content={seg.content}
              description={seg.description}
            />
          );
        }
        // 解析失败:降级为代码块
        return (
          <pre
            key={i}
            className="my-2 p-3 bg-muted text-foreground rounded-lg border text-xs leading-relaxed overflow-auto font-mono"
          >
            {seg.text}
          </pre>
        );
      })}
    </>
  );
}
