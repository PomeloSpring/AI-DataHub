import { useMemo, type CSSProperties, type ReactNode } from 'react';
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
import { useMotion } from '../hooks/useMotion';
import { motionCssVars, enterClass, enterStaggerStyle, revealClass, sectionStaggerStyle } from '../lib/motion';
import { splitMarkdownBlocks, splitMarkdownSections } from '../lib/markdownSections';
import { cn } from '../lib/utils';

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
   * 逐段滑入)。chat 回答输出用;报告/AS-BOT 等默认不开。
   */
  sections?: boolean;
  /** 流式输出中:最后一张阶段卡带进行中状态。 */
  streaming?: boolean;
  /**
   * 内容详情正文的出现效果（走动效契约的 `reveal` 段，不是 chat 的 `enter` 段）。
   * 默认档 `none` = 一个 class 也不加、不插包裹层（与今天逐字节相同）；
   * `fadeIn` = 整块渐显；`sectionStagger` = 按标题分段错峰（图片/表格块同样参与排序）。
   */
  reveal?: boolean;
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
 * 出现效果的包裹层：只在真的开了 reveal 时才插 div。
 * 默认档（`reveal.effect='none'`）必须与今天逐字节相同，不能为了挂 class 多出一层 DOM。
 */
function RevealBlock({ show, className, style, children }: { show: boolean; className?: string; style?: CSSProperties; children: ReactNode }) {
  return show ? <div className={className} style={style}>{children}</div> : <>{children}</>;
}

/**
 * 渲染助手回答:普通文本走 ReactMarkdown,```chart 块解析后渲染 ChartPicker,
 * ```mermaid 块渲染流程图/时序图,```artifact 块渲染可下载文件产物,
 * ```design 块在该位置内嵌仪表盘设计卡片(LLM 需要用户打开面板时输出)。
 * 解析失败降级为代码块。图表/卡片内嵌于 content,天然随消息持久化与回放。
 */
export default function MarkdownWithCharts({ text, className, conversationId, onDrill, onDesignContinue, sections = false, streaming = false, reveal = false }: Props) {
  const segments = useMemo(() => splitChartBlocks(text || ''), [text]);
  // 逐段滑入的节奏（时长/错峰/上限）取自动效解析口，不再写死 90ms/450ms
  const { motion } = useMotion();
  const enter = enterClass(motion.enter);
  // 正文出现：只在内容详情面生效（reveal），chat 的阶段卡继续用 enter 段
  const revealOn = reveal && motion.reveal.effect !== 'none';
  const revealCls = revealOn ? revealClass(motion.reveal) : '';
  const revealStagger = revealOn && motion.reveal.effect === 'sectionStagger';
  // 分段/分块的错峰序号按“实际出现顺序”连续排（markdown 段与图表块同一条序列）
  let blockIndex = 0;
  // CSS 变量跟着块走：时长写在 :root 的是默认档，预设改了时长不往下传就等于没改
  const revealVars = revealCls ? motionCssVars(motion) as CSSProperties : undefined;
  const blockStyle = (): CSSProperties | undefined => {
    const delay = revealStagger ? sectionStaggerStyle(blockIndex++, motion) as CSSProperties | undefined : undefined;
    return revealVars || delay ? { ...(revealVars || {}), ...(delay || {}) } : undefined;
  };
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
                    className={enter}
                    style={streaming ? undefined : enterStaggerStyle(j, motion.enter)}
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
          return revealStagger
            // 正文分段渐入：按标题切段（标题行留在段内，一个字符都不丢），每段自己渐入
            ? <div key={i}>
              {splitMarkdownBlocks(seg.text).map((chunk, j) => (
                <div key={j} className={cn(proseCls, revealCls)} style={blockStyle()}>
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{chunk}</ReactMarkdown>
                </div>
              ))}
            </div>
            : <div key={i} className={cn(proseCls, revealCls)} style={blockStyle()}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{seg.text}</ReactMarkdown>
            </div>;
        }
        if (seg.kind === 'chart') {
          return (
            <div key={i} className={cn('my-3 rounded-lg border p-3 bg-card', revealCls)} style={blockStyle()}>
              {seg.title && <div className="text-sm font-semibold mb-1">{seg.title}</div>}
              <ChartPicker data={seg.data} defaultType={seg.chartType} sql={seg.sql} onDrill={onDrill} />
            </div>
          );
        }
        if (seg.kind === 'mermaid') {
          return <RevealBlock key={i} show={!!revealCls} className={revealCls} style={blockStyle()}><MermaidBlock code={seg.code} title={seg.title} /></RevealBlock>;
        }
        if (seg.kind === 'sql') {
          return <RevealBlock key={i} show={!!revealCls} className={revealCls} style={blockStyle()}><SqlCard code={seg.code} /></RevealBlock>;
        }
        if (seg.kind === 'graph') {
          return <RevealBlock key={i} show={!!revealCls} className={revealCls} style={blockStyle()}><GraphCard title={seg.title} nodes={seg.nodes} edges={seg.edges} /></RevealBlock>;
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
