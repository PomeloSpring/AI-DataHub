import { useEffect, useMemo, useRef, useState } from 'react';
import { Chart } from '@antv/g2';
import { Pause, Play, RotateCcw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useThemeStore } from '@/stores/themeStore';
import { readToken } from '@/lib/chartTheme';
import { useVisConfig } from '@/lib/visStyle';
import { buildTimeFrames, frameRows, inferChartFields } from '@/lib/chartFrames';

interface Props {
  chartType: 'bar_race' | 'timeline_play';
  data: { columns: string[]; rows: any[] };
  config?: Record<string, any>;
  style?: React.CSSProperties;
}

// 字模缺失时的内置默认(与 seed vis_analysis_migration_v2.sql 同值);
// topN/speedMs 为可视化旋钮(字模 style_config 可调)
const DEFAULT_CHART = {
  colorScheme: ['hsl(var(--chart-1))', 'hsl(var(--chart-2))', 'hsl(var(--chart-3))'],
  borderRadius: 3,
  lineWidth: 2,
  topN: 12,
  speedMs: 1400,
};

/**
 * 时间动画图表:G2 实例常驻,帧推进走 changeData 过渡动画。
 * - bar_race(竞速条形图): 每帧=某时刻各系列按数值降序的前 N 条,横向条形逐帧滑动换位;
 * - timeline_play(时序播放): 累计至当前时刻的折线随播放生长,呈现指标演进过程。
 * 时长/速率可调,播完循环;数据不足(无时间列/单帧)时直接静态渲染。
 */
export default function AnimatedTimeChart({ chartType, data, config, style }: Props) {
  const theme = useThemeStore(s => s.theme);
  // 样式取字模库 chart_style(cs_bar_race / cs_timeline_play),含速率/条数旋钮
  const visCfg = useVisConfig(chartType === 'bar_race' ? 'cs_bar_race' : 'cs_timeline_play', DEFAULT_CHART);
  const columns = data?.columns || [];
  const rows = useMemo(() => data?.rows || [], [data]);
  const { xField, yField, groupField } = useMemo(
    () => inferChartFields(columns, rows, config), [columns, rows, config]);
  const isRace = chartType === 'bar_race';
  // 竞速图无系列列时按单系列处理(每帧一根条,数值随时刻演进)
  const catField = groupField || '__category';
  const topN = Number(visCfg.topN) > 0 ? Number(visCfg.topN) : 12;

  const frames = useMemo(() => buildTimeFrames(rows, xField), [rows, xField]);
  const [idx, setIdx] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [speed, setSpeed] = useState(1);
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<any>(null);

  const frameData = useMemo(() => {
    if (!frames.length) return [];
    const frame = frames[Math.min(idx, frames.length - 1)];
    let picked = frameRows(rows, {
      field: xField,
      frame,
      mode: isRace ? 'at' : 'upto',
      valueField: yField,
      topN: isRace ? topN : undefined,
    });
    if (isRace) {
      // 竞速帧按数值升序供 G2 域序(转置后最大条在最上方);无系列列时补单系列键
      picked = [...picked].sort((a, b) => (Number(a[yField]) || 0) - (Number(b[yField]) || 0));
      if (!groupField) picked = picked.map(r => ({ ...r, __category: yField }));
    }
    return picked;
  }, [rows, frames, idx, isRace, xField, yField, groupField, topN]);

  // G2 实例:主题/字段/类型变化时重建;帧推进不重建(交给 changeData 平滑过渡)
  useEffect(() => {
    const el = containerRef.current;
    if (!el || !frames.length) return;
    el.innerHTML = '';
    const colors = visCfg.colorScheme;
    const textColor = readToken('--muted-foreground', '#666');
    const chart = new Chart({ container: el, autoFit: true });
    const spec: Record<string, any> = isRace ? {
      type: 'interval',
      data: frameData,
      coordinate: { transform: [{ type: 'transpose' }] },
      encode: { x: catField, y: yField, color: catField },
      scale: { color: { range: colors }, y: { nice: true } },
      axis: {
        x: { title: false, labelAutoHide: true, style: { labelFill: textColor } },
        y: { title: false, style: { labelFill: textColor } },
      },
      legend: false,
      interaction: { tooltip: { shared: true } },
      style: { radiusTopLeft: visCfg.borderRadius, radiusBottomLeft: visCfg.borderRadius },
    } : {
      type: 'line',
      data: frameData,
      encode: { x: xField, y: yField, color: groupField || undefined },
      scale: { color: { range: colors } },
      axis: {
        x: { title: false, labelAutoRotate: true, style: { labelFill: textColor } },
        y: { title: false, style: { labelFill: textColor } },
      },
      legend: groupField ? { color: { position: 'top' } } : false,
      style: { lineWidth: visCfg.lineWidth },
      interaction: { tooltip: { shared: true } },
    };
    try {
      chart.options(spec);
      chart.render();
      chartRef.current = chart;
    } catch (e) {
      console.error('AnimatedTimeChart render error:', e);
    }
    return () => {
      try { chart.destroy(); } catch { /* noop */ }
      chartRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chartType, xField, yField, catField, groupField, theme, frames.length]);

  // 帧推进 → changeData(保留图实例,过渡动画由 G2 插值)
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !frames.length) return;
    try { chart.changeData(frameData); } catch { /* 首帧已随 options 渲染 */ }
  }, [frameData, frames.length]);

  // 自动播放:速率可调,播完循环
  useEffect(() => {
    if (!playing || frames.length < 2) return;
    const timer = setTimeout(() => setIdx(i => (i + 1) % frames.length), (Number(visCfg.speedMs) || 1400) / speed);
    return () => clearTimeout(timer);
  }, [playing, idx, speed, frames.length, visCfg.speedMs]);

  if (!columns.length || !rows.length) {
    return <div className="flex h-40 items-center justify-center text-sm text-muted-foreground">暂无数据</div>;
  }
  if (!xField || !yField) {
    return <div className="flex h-40 items-center justify-center text-sm text-muted-foreground">缺少时间列或数值列,无法播放</div>;
  }

  return (
    <div style={style}>
      <div ref={containerRef} style={{ height: 320 }} />
      {/* 播放控制条:播放/暂停 + 帧进度 + 速率 */}
      <div className="mt-1 flex items-center gap-2">
        <Button
          variant="outline" size="sm" className="h-7 w-7 p-0"
          onClick={() => setPlaying(p => !p)}
          title={playing ? '暂停' : '播放'}
        >
          {playing ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
        </Button>
        <Button
          variant="ghost" size="sm" className="h-7 w-7 p-0"
          onClick={() => { setIdx(0); setPlaying(true); }}
          title="从头播放"
        >
          <RotateCcw className="h-3.5 w-3.5" />
        </Button>
        <input
          type="range"
          min={0}
          max={Math.max(0, frames.length - 1)}
          value={Math.min(idx, frames.length - 1)}
          onChange={e => { setPlaying(false); setIdx(Number(e.target.value)); }}
          className="flex-1 accent-primary"
          aria-label="时间进度"
        />
        <span className="w-24 truncate text-xs text-muted-foreground" title={String(frames[Math.min(idx, frames.length - 1)])}>
          {frames[Math.min(idx, frames.length - 1)] ?? ''}
        </span>
        <select
          value={speed}
          onChange={e => setSpeed(Number(e.target.value))}
          className="h-7 rounded-md border bg-card px-1 text-xs text-muted-foreground"
          aria-label="播放速率"
        >
          <option value={0.5}>0.5×</option>
          <option value={1}>1×</option>
          <option value={2}>2×</option>
          <option value={4}>4×</option>
        </select>
      </div>
    </div>
  );
}
