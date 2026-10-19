import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { buildG2Spec } from '../DashboardChart';

// 字模样式必须被图表引擎真实消费(ui-vis-library-style.md §2):
// 历史缺陷是时序族写死 radius/lineWidth、不看 gradient —— 科技风时序柱状图没有渐变。
const cols = ['日期', '数量'];
const rows = [
  { 日期: '2026-10-12', 数量: 4210 },
  { 日期: '2026-10-13', 数量: 8210 },
  { 日期: '2026-10-14', 数量: 7810 },
];
const tech = { mode: 'dark' as const, palette: ['#22d3ee', '#8b5cf6', '#3b82f6'] };

describe('buildG2Spec 消费字模样式', () => {
  it('timeseries_bar：gradient/borderRadius/showLabel 进入 spec', () => {
    const spec: any = buildG2Spec('timeseries_bar', cols, rows,
      { xCol: '日期', yCol: '数量', gradient: true, borderRadius: 6, showLabel: true }, true, 800, 400, tech);
    expect(spec.style.fill).toBe('l(270) 0:#22d3ee 1:#8b5cf6');   // 渐变（科技风柱状图）
    expect(spec.style.radiusTopLeft).toBe(6);                     // 圆角不写死
    expect(spec.labels).toHaveLength(1);
    expect(spec.labels[0].text).toBe('数量');
  });

  it('timeseries_bar：无渐变/无标签时不写对应样式（默认单色无标签）', () => {
    const spec: any = buildG2Spec('timeseries_bar', cols, rows,
      { xCol: '日期', yCol: '数量', gradient: false, showLabel: false }, true, 800, 400, tech);
    expect(spec.style.fill).toBeUndefined();
    expect(spec.labels).toEqual([]);
  });

  it('timeseries_line：smooth/lineWidth 生效（默认平滑，显式 false 取直线）', () => {
    const smooth: any = buildG2Spec('timeseries_line', cols, rows,
      { xCol: '日期', yCol: '数量', lineWidth: 2.5 }, true, 800, 400, tech);
    expect(smooth.encode.shape).toBe('smooth');
    expect(smooth.style.lineWidth).toBe(2.5);

    const straight: any = buildG2Spec('timeseries_line', cols, rows,
      { xCol: '日期', yCol: '数量', smooth: false, lineWidth: 3 }, true, 800, 400, tech);
    expect(straight.encode.shape).toBeUndefined();
    expect(straight.style.lineWidth).toBe(3);
  });

  it('timeseries_area：areaOpacity 生效', () => {
    const spec: any = buildG2Spec('timeseries_area', cols, rows,
      { xCol: '日期', yCol: '数量', areaOpacity: 0.42 }, true, 800, 400, tech);
    expect(spec.style.fillOpacity).toBe(0.42);
  });

  it('色板来自主题包 visual（不是默认盘）', () => {
    const spec: any = buildG2Spec('timeseries_bar', cols, rows,
      { xCol: '日期', yCol: '数量' }, true, 800, 400, tech);
    expect(spec.scale.color.range).toEqual(tech.palette);
  });
});

describe('静态护栏：禁裸渲染 DashboardChart', () => {
  // ui-vis-library-style.md §1: 任何渲染面必须接 useChartDesign/resolveChartDesign 管线,
  // 否则字模库改了该页不跟(本次缺陷的同类缺口曾出现在 ModalPage/数据集预览/设计面板)。
  const SRC = path.resolve(__dirname, '..', '..');
  const pipelineImports = ['useChartDesign', 'resolveChartDesign'];

  function walk(dir: string, out: string[] = []): string[] {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full, out);
      else if (/\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) out.push(full);
    }
    return out;
  }

  it('渲染 <DashboardChart> 的文件必须走字模管线', () => {
    // 只认 JSX 渲染：`<DashboardChart` 前不能是标识符字符（排除 Partial<DashboardChart> 等泛型写法）
    const RENDER_RE = /(?<![A-Za-z0-9_])<DashboardChart[\s>]/;
    const offenders: string[] = [];
    for (const file of walk(SRC)) {
      const code = fs.readFileSync(file, 'utf8');
      if (!RENDER_RE.test(code)) continue;
      if (!pipelineImports.some(k => code.includes(k))) offenders.push(path.relative(SRC, file));
    }
    expect(offenders).toEqual([]);
  });
});
