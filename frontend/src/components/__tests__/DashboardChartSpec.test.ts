import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { buildG2Spec, estimateTextWidth, resolveXAxisLabelLayout, truncateToWidth } from '../DashboardChart';
import { resolveChartDesign, resolveDashboardDesign, sanitizePack } from '@/lib/dashboardDesign';
import { DEFAULT_MOTION, applyG2Animate, g2Animate } from '@/lib/motion';
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

  it('x 轴不渲染裸列名标题（与 y 轴同口径），避免标题行溢出卡片下沿', () => {
    const bar: any = buildG2Spec('bar', cols, rows, { xCol: 'app_code', yCol: '数量' }, false, 800, 400, asLight);
    expect(bar.axis.x.title).toBe(false);
    expect(bar.axis.y.title).toBe(false);
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

  it('多系列柱图：默认分组（dodgeX），不再把几个系列挤进同一根柱', () => {
    const groupedRows = rows.flatMap((r, i) => [
      { ...r, 渠道: 'A', 数量: 100 + i }, { ...r, 渠道: 'B', 数量: 60 + i },
    ]);
    const spec: any = buildG2Spec('bar', ['日期', '数量', '渠道'], groupedRows,
      { xCol: '日期', yCol: '数量', groupCol: '渠道' }, false, 800, 400, asLight);
    expect(spec.encode.color).toBe('渠道');
    expect(spec.transform).toEqual([{ type: 'dodgeX' }]);
    // 显式要堆叠时不写 dodge（累计口径交给人选，不静默换口径）
    const stacked: any = buildG2Spec('bar', ['日期', '数量', '渠道'], groupedRows,
      { xCol: '日期', yCol: '数量', groupCol: '渠道', seriesLayout: 'stack' }, false, 800, 400, asLight);
    expect(stacked.transform).toBeUndefined();
    // 单系列不拿 dodge 凑结构
    const single: any = buildG2Spec('bar', cols, rows, { xCol: '日期', yCol: '数量' }, false, 800, 400, asLight);
    expect(single.transform).toBeUndefined();
  });

  it('多系列柱图：柱体按系列取色（历史缺陷：fill 写死 colors[0]，图例蓝/绿而柱子全蓝）', () => {
    const groupedRows = rows.flatMap((r, i) => [
      { ...r, 渠道: 'A', 数量: 100 + i }, { ...r, 渠道: 'B', 数量: 60 + i },
    ]);
    const spec: any = buildG2Spec('bar', ['日期', '数量', '渠道'], groupedRows,
      { xCol: '日期', yCol: '数量', groupCol: '渠道', barFade: true }, false, 800, 400, asLight);
    expect(typeof spec.style.fill).toBe('function');
    const fillA = spec.style.fill({ 渠道: 'A' }) as string;
    const fillB = spec.style.fill({ 渠道: 'B' }) as string;
    expect(fillA).not.toBe(fillB);
    // 两个系列各拿自己那一抹色（色板前两色），而不是共用主色
    const rgbOf = (c: string) => { const n = parseInt(c.slice(1), 16); return `${(n >> 16) & 255},${(n >> 8) & 255},${n & 255}`; };
    expect(fillA).toContain(`rgba(${rgbOf(asLight.palette![0])},`);
    expect(fillB).toContain(`rgba(${rgbOf(asLight.palette![1])},`);
    // 图例色标与柱体同源：range 就是色板
    expect(spec.scale.color.range).toEqual(asLight.palette);
  });

  it('多系列时序柱：与 bar 同口径（分组 + 按系列取色）', () => {
    const groupedRows = rows.flatMap((r, i) => [
      { 日期: r.日期, 渠道: 'A', 数量: 100 + i }, { 日期: r.日期, 渠道: 'B', 数量: 60 + i },
    ]);
    const spec: any = buildG2Spec('timeseries_bar', ['日期', '数量', '渠道'], groupedRows,
      { xCol: '日期', yCol: '数量', groupCol: '渠道', barFade: true }, false, 800, 400, asLight);
    expect(spec.transform).toEqual([{ type: 'dodgeX' }]);
    expect(typeof spec.style.fill).toBe('function');
    expect(spec.style.fill({ 渠道: 'A' })).not.toBe(spec.style.fill({ 渠道: 'B' }));
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

  it('timeseries_area：与 area 同口径出竖向渐隐（areaOpacity 作顶部实色）', () => {
    const spec: any = buildG2Spec('timeseries_area', cols, rows,
      { xCol: '日期', yCol: '数量', areaOpacity: 0.42 }, true, 800, 400, tech);
    expect(spec.style.fill).toBe('l(90) 0:rgba(34,211,238,0.42) 1:rgba(34,211,238,0.07)');
    expect(spec.style.fillOpacity).toBeUndefined();   // 色标已带 alpha，不再乘第二道
  });

  describe('面积图竖向渐隐（默认口径）', () => {
    const cfg = { xCol: '日期', yCol: '数量' };
    const build = (extra: Record<string, any> = {}, type = 'area') =>
      buildG2Spec(type, cols, rows, { ...cfg, ...extra }, true, 800, 400, tech) as any;

    it('未配 areaFade 也渐隐：顶部 0.55 → 底部 0.09', () => {
      // 面积图的通行画法：填色是纵深，不是色块；平涂一大片会把线形盖掉
      expect(build().style.fill).toBe('l(90) 0:rgba(34,211,238,0.55) 1:rgba(34,211,238,0.09)');
    });

    it('areaOpacity 继续生效，只是当顶部实色（存量字模不必重配）', () => {
      expect(build({ areaOpacity: 0.3 }).style.fill)
        .toBe('l(90) 0:rgba(34,211,238,0.3) 1:rgba(34,211,238,0.05)');
    });

    it('areaFade=false 回到平涂；数值则作为顶部 alpha', () => {
      expect(build({ areaFade: false, areaOpacity: 0.3 }).style).toEqual({ fillOpacity: 0.3 });
      expect(build({ areaFade: 0.8 }).style.fill)
        .toBe('l(90) 0:rgba(34,211,238,0.8) 1:rgba(34,211,238,0.13)');
    });

    it('多系列按各自颜色做纵深（写死主色会把系列洗成同色，与柱图同一历史缺陷）', () => {
      // 分组列由 detectGroupColumn 自己辨（重复值才算系列，不靠 config 手指）
      const grouped = [
        { 日期: '2026-10-12', 数量: 10, 渠道: 'A' },
        { 日期: '2026-10-13', 数量: 20, 渠道: 'B' },
        { 日期: '2026-10-14', 数量: 15, 渠道: 'A' },
      ];
      const spec: any = buildG2Spec('area', ['日期', '数量', '渠道'], grouped,
        cfg, true, 800, 400, tech);
      expect(typeof spec.style.fill).toBe('function');
      expect(spec.style.fill({ 渠道: 'A' })).toBe('l(90) 0:rgba(34,211,238,0.55) 1:rgba(34,211,238,0.09)');
      expect(spec.style.fill({ 渠道: 'B' })).toBe('l(90) 0:rgba(139,92,246,0.55) 1:rgba(139,92,246,0.09)');
    });

    it('line + areaFill 的 area 子 mark 同口径（不另写一套淡蓝）', () => {
      const spec = build({ areaFill: true, areaOpacity: 0.2 }, 'line');
      const child = spec.children.find((c: any) => c.type === 'area');
      expect(child.style.fill).toBe('l(90) 0:rgba(34,211,238,0.2) 1:rgba(34,211,238,0.04)');
    });

    it('纯线不填底；radar 极坐标保持平涂（竖向渐隐无方向可言）', () => {
      expect(build({}, 'timeseries_line').style.fillOpacity).toBe(0);
      const radar = buildG2Spec('radar', ['名称', 'a', 'b'], [{ 名称: 'x', a: 1, b: 2 }],
        { ...cfg, areaOpacity: 0.25 }, true, 800, 400, tech) as any;
      expect(radar.style.fillOpacity).toBe(0.25);
      expect(radar.style.fill).toBeUndefined();
    });
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

// 两轴标题在 G2 v5 默认渲染编码字段名（物理列名，如 app_code）：y 轴竖排小字贴左边缘糊成一排，
// x 轴标题又落在旋转标签下方、底部 padding 没为它留高 → 溢出卡片下沿（用户反馈）。
// 口径：两轴都不渲染裸列名标题（与 ui-resource-display「不甩物理名」一致）；需要轴名由业务名另行配置。
describe('buildG2Spec 轴标题口径', () => {
  const axisCfg = { xCol: '日期', yCol: '数量' };
  it.each(['bar', 'line', 'area', 'scatter', 'timeseries_line', 'timeseries_bar', 'timeseries_area', 'heatmap'])(
    '%s：y 轴与 x 轴都不出裸列名标题', (type) => {
      const spec: any = buildG2Spec(type, cols, rows, axisCfg, true, 800, 400, tech);
      expect(spec.axis.y.title).toBe(false);
      expect(spec.axis.x.title).toBe(false);
    });

  it('y 轴刻度样式仍走 axisLabelStyle（只关标题，不伤可读性）', () => {
    const spec: any = buildG2Spec('line', cols, rows, axisCfg, true, 800, 400, tech);
    expect(typeof spec.axis.y.style.labelFill).toBe('string');
    expect(spec.axis.y.style.labelFontSize).toBeGreaterThan(0);
  });
});

// 「坐标轴」页的五个旋钮（旋转角/轴显隐/Y轴名称/网格线）曾经只写 config、无人消费，
// 面板拉满也没反应（ui-vis-library-style.md §2：“存了不生效”即缺陷）——这里把消费关系钉住。
describe('buildG2Spec 消费坐标轴配置', () => {
  const axisCfg = { xCol: '日期', yCol: '数量' };
  const build = (extra: Record<string, any> = {}, type = 'bar') =>
    buildG2Spec(type, cols, rows, { ...axisCfg, ...extra }, true, 800, 400, tech) as any;

  it('xAxisLabelRotate → labelTransform，并关掉自动旋转（不让 G2 改写成 rotate(90)）', () => {
    const spec = build({ xAxisLabelRotate: 35 });
    expect(spec.axis.x.labelTransform).toBe('rotate(35)');
    expect(spec.axis.x.labelAutoRotate).toBe(false);
  });
  it('未设旋转角且标签不挤时不写 labelTransform（轴带维持历史版式 40px）', () => {
    const spec = build();
    expect(spec.axis.x.labelTransform).toBeUndefined();
    expect(spec.axis.x.labelAutoRotate).toBe(false);
    expect(spec.padding[2]).toBe(40);
  });
  it('旋转角超出范围被夹到 0–90', () => {
    expect(build({ xAxisLabelRotate: 180 }).axis.x.labelTransform).toBe('rotate(90)');
    expect(build({ xAxisLabelRotate: -20 }).axis.x.labelTransform).toBeUndefined();
  });
  it('showXAxis / showYAxis 关掉时整轴不渲染', () => {
    expect(build({ showXAxis: false }).axis.x).toBe(false);
    expect(build({ showYAxis: false }).axis.y).toBe(false);
  });
  it('showGrid=false 同时关掉两轴网格线（默认不动）', () => {
    const off = build({ showGrid: false });
    expect(off.axis.x.grid).toBe(false);
    expect(off.axis.y.grid).toBe(false);
    expect(build().axis.x.grid).toBeUndefined();
  });
  it('y 轴默认不出标题，但用户显式写了 Y轴名称就照他说的渲染', () => {
    expect(build().axis.y.title).toBe(false);
    expect(build({ yAxisName: '  页面浏览量  ' }).axis.y.title).toBe('页面浏览量');
  });
  it.each(['bar', 'line', 'area', 'scatter', 'timeseries_line', 'timeseries_bar', 'boxplot', 'bubble'])(
    '%s 分支同样消费旋转与网格配置', (type) => {
      const spec = build({ xAxisLabelRotate: 45, showGrid: false }, type);
      expect(spec.axis.x.labelTransform).toBe('rotate(45)');
      expect(spec.axis.x.grid).toBe(false);
    });
});

// 数据多/标签长时 X 轴挤成一坨（Chat 结果图反馈）：旋转角必须自己定并把轴带撑高，
// 因为 G2 的 labelAutoRotate 在显式 padding 下永远算不过碰撞检测（会复位成 0）。
describe('buildG2Spec X 轴类目标签防挤', () => {
  const urlCols = ['页面', 'PV'];
  const urlRows = [
    '/en/as-connected-vehicle-director', '/en/director', '/en/asconnect-region',
    '/en/product-as-2060', '/en/product-as-260', '/en/product-as-20',
    '/en/about', '/en/contact', '/en/news-list/', '/en/request-partner',
  ].map((p, i) => ({ 页面: p, PV: 1900 - i * 180 }));
  const buildUrl = (extra: Record<string, any> = {}, w = 1150, h = 360) =>
    buildG2Spec('bar', urlCols, urlRows, { xCol: '页面', yCol: 'PV', ...extra }, false, w, h) as any;

  it('长类目自动选角旋转，并把轴带（padding[2]）撑到够用', () => {
    const spec = buildUrl();
    expect(spec.axis.x.labelTransform).toMatch(/^rotate\((30|45|60|90)\)$/);
    expect(spec.padding[2]).toBeGreaterThan(40);
    // 轴带高度与 padding 同值，G2 的碰撞消解才知道有多少地方可摆
    expect(spec.axis.x.size).toBe(spec.padding[2]);
  });
  it('超宽标签截断（一个类目都不丢，完整值留在 tooltip）', () => {
    const spec = buildUrl();
    const long = '/en/as-connected-vehicle-director';
    expect(typeof spec.axis.x.labelFormatter).toBe('function');
    const out = spec.axis.x.labelFormatter(long, 0, []);
    expect(out.length).toBeLessThan(long.length);
    expect(out.endsWith('…')).toBe(true);
    // 不变式：截断后的标签在选定角度下的竖向投影，必须装得进撑开的轴带
    const deg = Number(/rotate\((\d+)\)/.exec(spec.axis.x.labelTransform)![1]);
    expect(estimateTextWidth(out, 11) * Math.sin((deg * Math.PI) / 180)).toBeLessThanOrEqual(spec.axis.x.size);
  });
  it('不靠抽稀掩盖：常规拥挤全量呈现（labelAutoHide 关），只有连竖排都放不下才允许抽稀', () => {
    expect(buildUrl().axis.x.labelAutoHide).toBe(false);
    expect(buildUrl().axis.x.labelAutoRotate).toBe(false);
    const crowded = Array.from({ length: 200 }, (_, i) => ({ 页面: `c${i}`, PV: i }));
    const spec = buildG2Spec('bar', urlCols, crowded, { xCol: '页面', yCol: 'PV' }, false, 800, 360) as any;
    expect(spec.axis.x.labelAutoHide).toBe(true);
  });
  it('类目很少但标签很长：不旋转（水平截断保字更多），轴带维持 40px', () => {
    const few = ['甲'.repeat(40), '乙'.repeat(40), '丙'.repeat(40)].map((p, i) => ({ 页面: p, PV: i + 1 }));
    const spec = buildG2Spec('bar', urlCols, few, { xCol: '页面', yCol: 'PV' }, false, 800, 400) as any;
    expect(spec.axis.x.labelTransform).toBeUndefined();
    expect(spec.padding[2]).toBe(40);
    expect(spec.axis.x.labelFormatter('甲'.repeat(40), 0, []).length).toBeLessThan(40);
  });
  it('用户显式设了角度就照他说的转（轴带仍按该角度撑高）', () => {
    const spec = buildUrl({ xAxisLabelRotate: 45 });
    expect(spec.axis.x.labelTransform).toBe('rotate(45)');
    expect(spec.padding[2]).toBeGreaterThan(40);
  });
  it('短标签不触发任何改变（时序图版式不回退）', () => {
    const spec = buildG2Spec('line', cols, rows, { xCol: '日期', yCol: '数量' }, false, 800, 400) as any;
    expect(spec.axis.x.labelTransform).toBeUndefined();
    expect(spec.axis.x.labelFormatter).toBeUndefined();
    expect(spec.padding[2]).toBe(40);
  });
  it('无 x 轴的图型（饼图）不被撑高', () => {
    const pie = buildG2Spec('pie', urlCols, urlRows, { xCol: '页面', yCol: 'PV' }, false, 1150, 360) as any;
    expect(pie.padding[2]).toBe(40);
  });
  it('时序族按 timeCol 的标签判角（xCol 指向短日期列时不应误读它）', () => {
    const c = ['页面', '日期', 'PV'];
    const r = Array.from({ length: 12 }, (_, i) => ({
      页面: `/en/some-quite-long-page-name-${i}`, 日期: `2026-10-0${(i % 3) + 1}`, PV: i,
    }));
    const spec = buildG2Spec('timeseries_bar', c, r, { xCol: '日期', timeCol: '页面', valCol: 'PV' }, false, 600, 360) as any;
    expect(spec.axis.x.labelTransform).toMatch(/^rotate\((30|45|60|90)\)$/);
    expect(spec.padding[2]).toBeGreaterThan(40);
  });
});

describe('resolveXAxisLabelLayout / 文本度量', () => {
  const opts = { width: 1150, height: 360, fontSize: 11 };
  it('空标签集不改动版式', () => {
    expect(resolveXAxisLabelLayout([], opts)).toMatchObject({ rotate: 0, bottomPadding: 40, truncate: false });
  });
  it('档位择优：旋转换到的横向宽度多于水平可容纳宽度才转', () => {
    const wide = resolveXAxisLabelLayout(['a', 'b'], { ...opts, width: 800 });
    expect(wide.rotate).toBe(0);
    const crowded = resolveXAxisLabelLayout(
      Array.from({ length: 12 }, (_, i) => `/en/some-quite-long-page-name-${i}`), opts,
    );
    expect(crowded.rotate).toBeGreaterThan(0);
    expect(crowded.bottomPadding).toBeGreaterThan(40);
  });
  it('全角字按 1em、半角按 0.6em 估宽；截断后不超宽', () => {
    expect(estimateTextWidth('中中', 10)).toBe(20);
    expect(estimateTextWidth('ab', 10)).toBeCloseTo(12);
    const cut = truncateToWidth('abcdefghij', 30, 10);
    expect(cut.endsWith('…')).toBe(true);
    expect(estimateTextWidth(cut, 10)).toBeLessThanOrEqual(30);
    expect(truncateToWidth('short', 30, 10)).toBe('short');
  });
});

describe('饼图（theta）：切片占满半径 + 外围标签防重叠', () => {
  // 历史缺陷：encode 里多写了 x='name'。theta = Transpose+Polar，x 通道落在半径维度，
  // 于是每个切片只占 1/类目数 的一段同心细环（环粗只剩几像素），且 stackY 按 x 分组后
  // 各切片角度互不累加（和 > 360°），小切片挤在同一角度区间 → 外围标签糊成一团。
  const pieCols = ['操作系统', '设备数'];
  const pieRows = [
    { 操作系统: 'iOS', 设备数: 37 },
    { 操作系统: 'Android', 设备数: 3 },
    { 操作系统: 'HarmonyOS', 设备数: 2 },
    { 操作系统: 'Linux', 设备数: 1 },
  ];
  const build = (config: Record<string, any> = {}) =>
    buildG2Spec('pie', pieCols, pieRows, { xCol: '操作系统', yCol: '设备数', ...config }, false, 520, 460) as any;

  it('只编码 y + color，不编码 x', () => {
    const spec = build();
    expect(spec.type).toBe('interval');
    expect(spec.encode.x).toBeUndefined();
    // 切片数据已归一为 {name, value} 两列，编码的是归一列名而非原数据列名
    expect(spec.encode.y).toBe('value');
    expect(spec.encode.color).toBe('name');
    expect(spec.transform).toEqual([{ type: 'stackY' }]);
    expect(spec.data).toEqual([
      { name: 'iOS', value: 37 },
      { name: 'Android', value: 3 },
      { name: 'HarmonyOS', value: 2 },
      { name: 'Linux', value: 1 },
    ]);
  });

  it('innerRadius 被 spec 真实消费：按占外半径的比例等比缩放，上界 0.75 防环粗退化成发丝', () => {
    // 主题包写 0.58 → 内半径 0.58·outer，环粗仍是外半径的 42%（不因给标签让位而变细）
    expect(build().coordinate.innerRadius).toBeCloseTo(0.4 * 0.72, 6);
    expect(build({ innerRadius: 0.58 }).coordinate.innerRadius).toBeCloseTo(0.58 * 0.72, 6);
    expect(build({ innerRadius: 0 }).coordinate.innerRadius).toBe(0);          // 实心饼
    expect(build({ innerRadius: 0.95 }).coordinate.innerRadius).toBeCloseTo(0.75 * 0.72, 6);
    // 不变式：任何配置下环粗不得少于外半径的 1/4
    for (const v of [undefined, 0, 0.4, 0.58, 0.95, 1.5]) {
      const c = build(v === undefined ? {} : { innerRadius: v }).coordinate;
      expect((c.outerRadius - c.innerRadius) / c.outerRadius).toBeGreaterThanOrEqual(0.25 - 1e-9);
    }
  });

  it('带标签时外半径给标签留环带；标签走 spider 竖向避让并保留引导线', () => {
    const spec = build();
    expect(spec.coordinate.outerRadius).toBe(0.72);
    expect(spec.labels[0].position).toBe('spider');
    expect(spec.labels[0].connector).toBe(true);
    expect(spec.labels[0].connectorLineWidth).toBe(1);
    // 行高必须大于字号，否则相邻标签的竖向避让仍会压在一起
    expect(spec.labels[0].labelHeight).toBeGreaterThan(spec.labels[0].style.fontSize);
    // 引导线取短值：列位 = 外半径 + 两段引线 + 间距，过长会把标签推出画布（与 0.72 外半径配套调）
    expect(spec.labels[0].connectorLength).toBeLessThanOrEqual(8);
    expect(spec.labels[0].connectorLength2).toBeLessThanOrEqual(8);
  });

  it('关掉标签后不渲染标签并把环铺满', () => {
    const spec = build({ showLabel: false });
    expect(spec.labels).toEqual([]);
    expect(spec.coordinate.outerRadius).toBe(0.9);
    expect(spec.coordinate.innerRadius).toBeCloseTo(0.4 * 0.9, 6);
  });

  it('长名字截断（完整名仍在 tooltip/图例），字号跟随字模 axisLabelSize', () => {
    const text = build().labels[0].text;
    expect(text({ name: 'iOS' })).toBe('iOS');
    const long = text({ name: 'SomeQuiteLongOperatingSystemName' });
    expect(long.endsWith('…')).toBe(true);
    expect(estimateTextWidth(long, 11)).toBeLessThanOrEqual(80);
    const sized = build({ axisLabelSize: 12 });
    expect(sized.labels[0].style.fontSize).toBe(12);
    expect(estimateTextWidth(sized.labels[0].text({ name: 'SomeQuiteLongOperatingSystemName' }), 12)).toBeLessThanOrEqual(80);
  });
});

describe('widget_decision（看板决策建议卡）真实消费控件旋钮', () => {
  // ui-vis-library-style.md §2：存了不生效就是缺陷。控件样式只走 widget_style 命名空间。
  const chromeOf = (widgetStyle: Record<string, any>, visual?: any) =>
    ((buildG2Spec('widget_decision', [], [], { widgetStyle }, false, 360, 220, visual) as any)._chrome);

  it('背景/文字/边框/圆角/内边距/字号/控件高都落到渲染面', () => {
    const chrome = chromeOf({
      backgroundColor: '#eef2ff', textColor: '#111827', borderColor: '#4f46e5',
      borderWidth: 2, borderStyle: 'dashed', borderRadius: 12, padding: '6px 8px',
      fontSize: 15, height: 180,
    });
    expect(chrome).toMatchObject({
      background: '#eef2ff', color: '#111827', borderColor: '#4f46e5',
      borderWidth: 2, borderStyle: 'dashed', borderRadius: 12, padding: '6px 8px',
      fontSize: 15, height: 180,
    });
  });

  it('没写旋钮时回落主题色（不硬编码色值），控件高不无中生有', () => {
    const dark = chromeOf({}, { mode: 'dark' });
    const light = chromeOf({}, { mode: 'light' });
    expect(dark.background).not.toBe(light.background);
    expect(typeof dark.color).toBe('string');
    expect(dark.color).not.toBe(light.color);
    expect(chromeOf({}).height).toBeUndefined();
  });

  it('chart_style 命名空间的同名键不会盖掉控件样式（两套混挂会互相覆盖）', () => {
    const chrome = chromeOf({});
    // 本图把色值写在 chart_style 段（错命名空间），控件样式仍应走主题基线
    const wrong = ((buildG2Spec('widget_decision', [], [],
      { backgroundColor: '#000000', textColor: '#ffffff' }, false, 360, 220) as any)._chrome);
    expect(wrong.background).not.toBe('#000000');
    expect(typeof chrome.background).toBe('string');
  });

  it('不是 G2 mark：返回 html 形状且不编造数据系列', () => {
    const spec: any = buildG2Spec('widget_decision', ['日期', '数量'],
      [{ 日期: '2026-11-12', 数量: 1 }], {}, false, 360, 220);
    expect(spec.type).toBe('html');
    expect(spec._widget).toBe('decision');
    expect(spec.children).toBeUndefined();
    expect(spec.data).toBeUndefined();
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

describe('图表入场动效（motion.chart）落进 spec 且不破坏默认零变化', () => {
  // ui-vis-library-style §2 同源防线：样式字段必须被真实消费；反过来，默认档必须
  // 一个键都不多写 —— 否则"我没配动效，图表怎么开始跳了"就是开箱变样。
  const animateOf = (appear: string) => g2Animate({ ...DEFAULT_MOTION.chart, appear } as any);

  it('appear=default（内置默认档）→ spec 不出现 animate 键，与改造前逐字节相同', () => {
    const base: any = buildG2Spec('bar', cols, rows, fadeOnly, false, 800, 400, asLight);
    const after: any = applyG2Animate(base, animateOf('default'));
    expect(animateOf('default')).toBeUndefined();
    expect(after).toBe(base);
    expect('animate' in after).toBe(false);
  });

  it('具名描画（pathIn）→ line 的 mark 级 animate.enter.type 被写入', () => {
    const spec: any = applyG2Animate(
      buildG2Spec('line', cols, rows, { xCol: '日期', yCol: '数量' }, false, 800, 400, tech),
      animateOf('pathIn'),
    );
    expect(spec.animate).toEqual({ enter: { type: 'pathIn', duration: 600, easing: 'cubic-bezier(0.22, 1, 0.36, 1)' } });
  });

  it('柱顶帽的 view 结构：主体与帽两个 mark 都要拿到 animate（否则帽先出现、柱后生长）', () => {
    const spec: any = applyG2Animate(buildG2Spec('bar', cols, rows, asPackConfig, false, 800, 400, asLight), animateOf('growInY'));
    expect(spec.type).toBe('view');
    expect(spec.children[0].animate.enter.type).toBe('growInY');
    expect(spec.children[1].animate.enter.type).toBe('growInY');
  });

  it('appear=none → animate:false（G2 maybeNonAnimate 据此关掉 enter/update/exit）', () => {
    const spec: any = applyG2Animate(buildG2Spec('pie', cols, rows, { xCol: '日期', yCol: '数量' }, false, 400, 300, tech), animateOf('none'));
    expect(spec.animate).toBe(false);
  });

  it('桑基图的 children 里组件节点（无 encode）不被误写 animate', () => {
    const sankeySpec: any = buildG2Spec('sankey', ['来源', '目标', '数量'],
      [{ 来源: 'A', 目标: 'B', 数量: 3 }, { 来源: 'A', 目标: 'C', 数量: 5 }],
      { sourceCol: '来源', targetCol: '目标', valueCol: '数量' }, false, 800, 400, tech);
    const out: any = applyG2Animate(sankeySpec, animateOf('fadeIn'));
    for (const child of out.children || []) {
      if (child.encode) expect(child.animate.enter.type).toBe('fadeIn');
      else expect('animate' in child).toBe(false);
    }
  });
});
