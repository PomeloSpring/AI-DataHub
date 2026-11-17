/**
 * 说明卡（widget_label）双语输入位（plan §P6：图表标题与说明卡）。
 *
 * 判据：
 *  - 英文 textarea 存在（`data-testid="note-content-en"`），中文事实源走 `config.content`；
 *  - 编辑 English 只上送 `onWidgetI18nChange(id, 'content', en)`，**不动中文列**；
 *  - `chart.i18n.config['en-US'].content` 是英文输入的回填口（后端 localize_display 已展开的覆盖层）。
 */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import PropertyPanel from '../editor/PropertyPanel';

const widgetLabel = (over: any = {}) => ({
  id: 21, name: '说明卡', chart_type: 'widget_label',
  config: { content: '本看板只读，数据每 5 分钟刷新' },
  position: { x: 0, y: 0, w: 300, h: 60 },
  ...over,
});

function panelProps(chart: any, extra: Record<string, any> = {}) {
  return {
    isOpen: true, onClose: () => {}, selectedChart: chart, selectedElementType: 'control' as const,
    canvasSize: { width: 1920, height: 1080 }, setCanvasSize: () => {}, canvasBgColor: '', setCanvasBgColor: () => {},
    gridSize: 20, setGridSize: () => {}, scale: 1, setScale: () => {}, allCharts: [chart],
    isNewChart: () => false, onPropertyChange: () => {}, onPositionChange: () => {},
    onWidgetConfigChange: () => {}, onWidgetI18nChange: () => {}, onDelete: () => {}, onChartConfig: () => {},
    tab: 'data', onTab: () => {}, ...extra,
  };
}

describe('说明卡 · 双语输入位', () => {
  it('存在 English textarea，回填 chart.i18n.config.en-US.content', () => {
    const chart = widgetLabel({ i18n: { config: { 'en-US': { content: 'Read-only dashboard' } } } });
    render(<PropertyPanel {...panelProps(chart)} />);
    const en = screen.getByTestId('note-content-en') as HTMLTextAreaElement;
    expect(en.value).toBe('Read-only dashboard');
    // 中文输入位仍是事实源
    expect(screen.getByDisplayValue('本看板只读，数据每 5 分钟刷新')).toBeTruthy();
  });

  it('编辑 English 只回调 onWidgetI18nChange，不动中文事实源', () => {
    const chart = widgetLabel();
    const onCfg = vi.fn();
    const onI18n = vi.fn();
    render(<PropertyPanel {...panelProps(chart, { onWidgetConfigChange: onCfg, onWidgetI18nChange: onI18n })} />);
    fireEvent.change(screen.getByTestId('note-content-en'), { target: { value: 'Refreshed every 5 min' } });
    expect(onI18n).toHaveBeenCalledWith(chart.id, 'content', 'Refreshed every 5 min');
    // 中文事实源未被写：onWidgetConfigChange 一次都没被 English 输入触发
    expect(onCfg).not.toHaveBeenCalled();
  });
});
