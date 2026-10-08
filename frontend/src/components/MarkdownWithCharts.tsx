import { useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import ChartPicker from './ChartPicker';
import MermaidBlock from './MermaidBlock';
import ArtifactCard from './ArtifactCard';
import SqlCard from './SqlCard';
import GraphCard from './GraphCard';
import DesignCard from './DesignCard';
import { splitChartBlocks } from '../lib/chartBlocks';

interface Props {
  text: string;
  className?: string;
  /** 当前会话 ID, 供按引用的文件产物卡片构造下载/预览 URL。 */
  conversationId?: number | null;
  /** 图上下钻: 点击图表元素时回传下钻问题(不传则不启用)。 */
  onDrill?: (question: string) => void;
  /** 设计卡片「返回对话继续完善」时回传追问文本(不传则按钮不可用)。 */
  onDesignContinue?: (text: string) => void;
}

/**
 * 渲染助手回答:普通文本走 ReactMarkdown,```chart 块解析后渲染 ChartPicker,
 * ```mermaid 块渲染流程图/时序图,```artifact 块渲染可下载文件产物,
 * ```design 块在该位置内嵌仪表盘设计卡片(LLM 需要用户打开面板时输出)。
 * 解析失败降级为代码块。图表/卡片内嵌于 content,天然随消息持久化与回放。
 */
export default function MarkdownWithCharts({ text, className, conversationId, onDrill, onDesignContinue }: Props) {
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
              <ChartPicker data={seg.data} defaultType={seg.chartType} sql={seg.sql} onDrill={onDrill} />
            </div>
          );
        }
        if (seg.kind === 'mermaid') {
          return <MermaidBlock key={i} code={seg.code} title={seg.title} />;
        }
        if (seg.kind === 'sql') {
          return <SqlCard key={i} code={seg.code} />;
        }
        if (seg.kind === 'graph') {
          return <GraphCard key={i} title={seg.title} nodes={seg.nodes} edges={seg.edges} />;
        }
        if (seg.kind === 'artifact') {
          return (
            <ArtifactCard
              key={i}
              type={seg.type}
              filename={seg.filename}
              content={seg.content}
              path={seg.path}
              theme={seg.theme}
              description={seg.description}
              conversationId={conversationId}
            />
          );
        }
        if (seg.kind === 'design') {
          return <DesignCard key={i} designId={seg.designId} onContinue={onDesignContinue} />;
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
