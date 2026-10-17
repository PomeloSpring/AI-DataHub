import { useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { FileText, MessageSquare } from 'lucide-react';
import ChartPicker from './ChartPicker';
import MermaidBlock from './MermaidBlock';
import ArtifactCard from './ArtifactCard';
import SqlCard from './SqlCard';
import GraphCard from './GraphCard';
import DesignCard from './DesignCard';
import StageCard from './chat/StageCard';
import { splitChartBlocks } from '../lib/chartBlocks';
import { splitMarkdownSections } from '../lib/markdownSections';

interface Props {
  text: string;
  className?: string;
  /** 当前会话 ID, 供按引用的文件产物卡片构造下载/预览 URL。 */
  conversationId?: number | null;
  /** 图上下钻: 点击图表元素时回传下钻问题(不传则不启用)。 */
  onDrill?: (question: string) => void;
  /** 设计卡片「返回对话继续完善」时回传追问文本(不传则按钮不可用)。 */
  onDesignContinue?: (text: string) => void;
  /**
   * 回答内容卡片化: 把 markdown 正文按标题切成阶段性卡片(卡头可滑动展开/收起,
   * 逐段滑入)。chat 回答输出用;报表/AS-BOT 等默认不开。
   */
  sections?: boolean;
  /** 流式输出中:最后一张阶段卡带进行中状态。 */
  streaming?: boolean;
}

// 表格块美化:横向滚动 + 独立卡片边框(仅 sections 卡片化模式生效)
const SECTION_MD_COMPONENTS = {
  table: ({ node: _node, ...props }: any) => (
    <div className="my-2 overflow-x-auto rounded-lg border bg-card">
      <table className="min-w-full text-xs" {...props} />
    </div>
  ),
};

/**
 * 渲染助手回答:普通文本走 ReactMarkdown,```chart 块解析后渲染 ChartPicker,
 * ```mermaid 块渲染流程图/时序图,```artifact 块渲染可下载文件产物,
 * ```design 块在该位置内嵌仪表盘设计卡片(LLM 需要用户打开面板时输出)。
 * 解析失败降级为代码块。图表/卡片内嵌于 content,天然随消息持久化与回放。
 */
export default function MarkdownWithCharts({ text, className, conversationId, onDrill, onDesignContinue, sections = false, streaming = false }: Props) {
  const segments = useMemo(() => splitChartBlocks(text || ''), [text]);
  // prose 排版色已由 globals.css 令牌化(--tw-prose-* → 主题变量),自动随主题
  const proseCls = className || 'leading-relaxed prose prose-sm max-w-none';

  return (
    <>
      {segments.map((seg, i) => {
        if (seg.kind === 'markdown') {
          if (!seg.text.trim()) return null;
          // 回答内容卡片化:按标题切成阶段性卡片,逐段滑入、卡头可滑动收起/展开。
          // 回放(非流式)时逐卡错峰滑入(长回答“流动”出现,而非一下子弹出);
          // 流式追加的卡不加延迟,避免最新内容滞留——只影响入场节奏,不阻塞交互
          if (sections) {
            const secs = splitMarkdownSections(seg.text);
            const lastIdx = secs.length - 1;
            return (
              <div key={i} className="space-y-2">
                {secs.map((sec, j) => (
                  <div
                    key={j}
                    className="animate-stage-in"
                    style={streaming ? undefined : { animationDelay: `${Math.min(j * 90, 450)}ms` }}
                  >
                    <StageCard
                      icon={sec.title ? FileText : MessageSquare}
                      title={sec.title || '回答'}
                      collapsible
                      defaultOpen
                      animate={false}
                      status={streaming && j === lastIdx && secs.length > 1 ? 'running' : undefined}
                      bodyClassName="px-3.5 pb-3.5"
                    >
                      <div className={proseCls}>
                        <ReactMarkdown remarkPlugins={[remarkGfm]} components={SECTION_MD_COMPONENTS}>
                          {sec.body.trim()}
                        </ReactMarkdown>
                      </div>
                    </StageCard>
                  </div>
                ))}
              </div>
            );
          }
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
