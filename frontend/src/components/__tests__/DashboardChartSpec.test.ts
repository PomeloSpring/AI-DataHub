import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { buildG2Spec } from '../DashboardChart';
import { resolveChartDesign, resolveDashboardDesign, sanitizePack } from '@/lib/dashboardDesign';
import type { VisComponent } from '@/api/visLibrary';

// 字模样式必须被图表引擎真实消费(ui-vis-library-style.md §2):
// 历史缺陷是时序族写死 radius/lineWidth、不看 gradient —— 科技风时序柱状图没有渐变。
const cols = ['日期', '数量'];
const rows = [
  { 日期: '2026-10-12', 数量: 4210 },
  { 日期: '2026-10-13', 数量: 8210 },
  { 日期: '2026-10-14', 数量: 7810 },
];
const tech = { mode: 'dark' as const, palette: ['#22d3ee', '#8b5cf6', '#3b82f6'] };
// AS 风格(design-service 浅色预设): 主色 #0037CB, 柱体上深下浅渐隐 + 4px 柱顶帽
const asLight = { mode: 'light' as const, palette: ['#0037CB', '#38AD2A', '#E56F22'] };
const asPackConfig = { xCol: '日期', yCol: '数量', barFade: true, barCap: '#0037CB', borderRadius: 0, axisLabelSize: 12 };
const fadeOnly = { xCol: '日期', yCol: '数量', barFade: true, borderRadius: 0 };

describe('buildG2Spec 消费字模样式', () => {
  it('bar：barFade 渲染为同色系上深下浅渐隐(与素材 rgba(.6)→rgba(.1) 逐字对齐, l(90)=上→下)', () => {
    const spec: any = buildG2Spec('bar', cols, rows, fadeOnly, false, 800, 400, asLight);
    expect(spec.type).toBe('interval');          // 无柱顶帽时保持单 mark 结构
    expect(spec.style.fill).toBe('l(90) 0:rgba(0,55,203,0.6) 1:rgba(0,55,203,0.1)');
    expect(spec.style.radiusTopLeft).toBe(0);
  });

  it('bar：barFade 优先于 gradient(同系列明暗纵深 ≠ 跨系列渐变)', () => {
    const spec: any = buildG2Spec('bar', cols, rows,
      { ...fadeOnly, gradient: true }, false, 800, 400, asLight);
    expect(spec.style.fill).toBe('l(90) 0:rgba(0,55,203,0.6) 1:rgba(0,55,203,0.1)');
  });

  it('bar：barCap 画柱顶帽(同 x 带的浮动区间柱), 零值柱退为 1px 细线', () => {
    const spec: any = buildG2Spec('bar', cols, rows, asPackConfig, false, 800, 400, asLight);
    expect(spec.type).toBe('view');
    expect(spec.children[0].style.fill).toBe('l(90) 0:rgba(0,55,203,0.6) 1:rgba(0,55,203,0.1)');
    const cap = spec.children[1];
    expect(cap.style.fill).toBe('#0037CB');
    expect(cap.encode.y).toEqual(['__capLo', '__capHi']);
    expect(cap.tooltip).toBe(false);
    // 帽跨柱顶居中：以真实值为中心上下各 2px 换算的数据单位
    expect(cap.data[0].__capHi).toBeGreaterThan(cap.data[0].__capLo);
    expect(cap.data[0].__capHi - rows[0]['数量']).toBeCloseTo(cap.data[0]['数量'] - cap.data[0].__capLo, 6);

    const zeroed: any = buildG2Spec('bar', cols, [{ 日期: '2026-10-12', 数量: 0 }],
      asPackConfig, false, 800, 400, asLight);
    expect(zeroed.children[1].data[0].__capLo).toBe(0);
    expect(zeroed.children[1].data[0].__capHi).toBeGreaterThan(0);
  });

  it('bar：分组柱不画帽(dodge 位移第二 mark 拿不到, 宁缺勿错)、无 barCap 时结构不变', () => {
    const grouped: any = buildG2Spec('bar', ['日期', '数量', '渠道'],
      rows.map((r, i) => ({ ...r, 渠道: i % 2 ? 'A' : 'B' })), asPackConfig, false, 800, 400, asLight);
    expect(grouped.type).toBe('interval');
    expect(grouped.children).toBeUndefined();

    const plain: any = buildG2Spec('bar', cols, rows, { xCol: '日期', yCol: '数量' }, false, 800, 400, asLight);
    expect(plain.type).toBe('interval');
    expect(plain.style.fill).toBeUndefined();
  });

  it('bar/timeseries_bar：axisLabelSize 进入轴与图例字号(素材字模 12px)', () => {
    const bar: any = buildG2Spec('bar', cols, rows, asPackConfig, false, 800, 400, asLight);
    expect(bar.axis.x.style.labelFontSize).toBe(12);
    expect(bar.axis.y.style.labelFontSize).toBe(12);
    const pie: any = buildG2Spec('pie', cols, rows, { ...asPackConfig, legend: 'bottom' }, false, 800, 400, asLight);
    expect(pie.legend.color.itemLabelStyle.fontSize).toBe(12);
    // 未给字模字号时维持原明暗自适应(亮 11)
    const plain: any = buildG2Spec('bar', cols, rows, { xCol: '日期', yCol: '数量' }, false, 800, 400, asLight);
    expect(plain.axis.x.style.labelFontSize).toBe(11);
  });

  it('timeseries_bar：与 bar 同口径消费 barFade/barCap', () => {
    const spec: any = buildG2Spec('timeseries_bar', cols, rows, asPackConfig, false, 800, 400, asLight);
    expect(spec.children[0].style.fill).toBe('l(90) 0:rgba(0,55,203,0.6) 1:rgba(0,55,203,0.1)');
    expect(spec.children[1].style.fill).toBe('#0037CB');
  });

  it('timeseries_bar：gradient/borderRadius/showLabel 进入 spec', () => {
    const spec: any = buildG2Spec('timeseries_bar', cols, rows,
      { xCol: '日期', yCol: '数量', gradient: true, borderRadius: 6, showLabel: true }, true, 800, 400, tech);
    expect(spec.style.fill).toBe('l(270) 0:#22d3ee 1:#8b5cf6');   // 渐变（科技风柱状图）
    expect(spec.style.radiusTopLeft).toBe(6);                     // 圆角不写死
    expect(spec.labels).toHaveLength(1);
    expect(spec.labels[0].text).toBe('数量');
  });

  it('端到端：入库的 AS 主题包经 resolveChartDesign 注入后，柱图真出渐隐+柱顶帽', () => {
    // 链路契约：字模 SQL → sanitizePack → resolveDashboardDesign(theme) → resolveChartDesign → buildG2Spec。
    // 任何一环丢键（白名单没登记/不被分支消费）都会在这里报红，而不是“存了不生效”。
    const sql = fs.readFileSync(path.resolve(__dirname, '..', '..', '..', '..',
      'docker', 'mysql', 'as_style_theme_migration.sql'), 'utf8');
    const raw = /'(\{"mode":"light".*\})'/.exec(sql)![1];
    const pack = { id: 90, code: 'tp_as', name: 'AS 风格', category: 'theme_pack', chart_type: null,
      style_config: sanitizePack(JSON.parse(raw)) } as unknown as VisComponent;

    const screen = resolveDashboardDesign({}, [pack], { theme: 'as' });
    const { config, visual } = resolveChartDesign({}, screen, [pack], 'bar');
    expect(config.barFade).toBe(true);
    expect(config.barCap).toBe('#0037CB');
    expect(visual.palette?.[0]).toBe('#0037CB');

    const spec: any = buildG2Spec('bar', cols, rows, config, false, 800, 400,
      { ...visual, viewBg: config.cardBg || visual.viewBg });
    expect(spec.children[0].style.fill).toBe('l(90) 0:rgba(0,55,203,0.6) 1:rgba(0,55,203,0.1)');
    expect(spec.children[1].style.fill).toBe('#0037CB');
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

// 单点序列必须可见：线/面 mark 至少两个端点才画得出形状，
// 历史缺陷是只有一个数据点时整张图 hover 前什么都不显示（v4 遗留的 point:{shapeSize} 在 G2 v5 静默失效）。
describe('buildG2Spec 单点序列可见化', () => {
  const oneRow = [{ 日期: '2026-10-19', 数量: 1 }];
  const cfg = { xCol: '日期', yCol: '数量' };
  const build = (type: string, rowsData: any[], extra: Record<string, any> = {}) =>
    buildG2Spec(type, cols, rowsData, { ...cfg, ...extra }, true, 800, 400, tech) as any;

  it.each(['line', 'area', 'timeseries_line', 'timeseries_area'])(
    '%s：单点叠加实心圆点 mark（不悬停也看得见）', (type) => {
      const spec = build(type, oneRow);
      expect(spec.type).toBe('view');
      expect(spec.children.map((c: any) => c.type)).toEqual([type.startsWith('timeseries') ? type.slice(11) : type, 'point']);
      const dot = spec.children[1];
      expect(dot.style.r).toBe(4);
      expect(dot.encode.shape).toBe('circle');
      expect(dot.tooltip).toBe(false);          // 值仍由线/面的 shared tooltip 给，避免重复条目
      expect(spec.interaction.tooltip.shared).toBe(true);
    });

  it('line + areaFill：单点在 area/line 之外追加圆点', () => {
    const spec = build('line', oneRow, { areaFill: true });
    expect(spec.children.map((c: any) => c.type)).toEqual(['area', 'line', 'point']);
  });

  it('两个及以上数据点不改变结构（折线原样，无多余圆点）', () => {
    for (const type of ['line', 'area', 'timeseries_line', 'timeseries_area']) {
      const spec = build(type, rows);
      expect(spec.type).toBe(type.startsWith('timeseries') ? type.slice(11) : type);
      expect(spec.children).toBeUndefined();
    }
  });

  it('timeseries_bar 单点不叠加（柱形本身可见）', () => {
    const spec = build('timeseries_bar', oneRow);
    expect(spec.type).toBe('interval');
    expect(spec.children).toBeUndefined();
  });
});

// y 轴标题在 G2 v5 默认竖排（theme.axisLeft: titleDirection='vertical' + rotate(-90)），
// 紧凑卡片里小字旋转后贴左边缘糊成一排（用户反馈“左侧字看不清”），而原文又是物理列名——不渲染。
describe('buildG2Spec 轴标题口径', () => {
  const axisCfg = { xCol: '日期', yCol: '数量' };
  it.each(['bar', 'line', 'area', 'scatter', 'timeseries_line', 'timeseries_bar', 'timeseries_area', 'heatmap'])(
    '%s：y 轴不出标题，x 轴不动', (type) => {
      const spec: any = buildG2Spec(type, cols, rows, axisCfg, true, 800, 400, tech);
      expect(spec.axis.y.title).toBe(false);
      expect(spec.axis.x.title).toBeUndefined();
    });

  it('y 轴刻度样式仍走 axisLabelStyle（只关标题，不伤可读性）', () => {
    const spec: any = buildG2Spec('line', cols, rows, axisCfg, true, 800, 400, tech);
    expect(typeof spec.axis.y.style.labelFill).toBe('string');
    expect(spec.axis.y.style.labelFontSize).toBeGreaterThan(0);
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
