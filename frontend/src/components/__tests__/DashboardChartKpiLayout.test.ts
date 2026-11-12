/*
 * KPI 大数字（text_display / big_number_trend）版式随图表尺寸收敛的契约。
 *
 * 2026-11-12 反馈：卡片拖小后「7,391」下面的绿色涨幅只剩半截。根因不在字号而在坐标系：
 * G2 把 mark 层 translate(paddingLeft, paddingTop) 到绘图区原点（runtime/plot.js updateBBox），
 * 所以 style.y 是**绘图区**坐标；旧写法拿整图高算 `height / 2 ± 20` 又用固定偏移，
 * 涨幅行的画布 y = paddingTop + height/2 + 20，图高 ≤130 时越过画布下沿被裁掉半行。
 *
 * 因此这里守的是「两行都完整落在画布内 + 居中真的居中 + 尺寸变了字号跟着变」，
 * 不是某个具体像素值（那属于实现细节，改了不叫回退）。
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { buildG2Spec, estimateTextWidth, kpiPaddingY } from '../DashboardChart';

const cols = ['日期', '数量', '上月', '去年'];
const rows = [
  { 日期: '2026-10-12', 数量: 7100, 上月: 6900, 去年: 6200 },
  { 日期: '2026-10-13', 数量: 7391, 上月: 7050, 去年: 6500 },
];
const kpi = (type: string, config: Record<string, any>, w = 345, h = 120) =>
  buildG2Spec(type, cols, rows, { xCol: '日期', yCol: '数量', ...config }, false, w, h) as any;
const texts = (spec: any) => spec.children.filter((c: any) => c.type === 'text');
// Advance 文本默认 textBaseline='middle'，y 是行中心 → 上下沿各让出半个字号
const bottomInCanvas = (spec: any, style: any) => spec.padding[0] + style.y + style.fontSize / 2;
const topInCanvas = (spec: any, style: any) => spec.padding[0] + style.y - style.fontSize / 2;

describe('KPI 大数字版式随图表尺寸收敛（涨幅不得被裁）', () => {
  it('小卡片（345×120）：数值行与涨幅行都完整在画布内', () => {
    for (const type of ['big_number_trend', 'text_display']) {
      const spec = kpi(type, type === 'text_display' ? { momColumn: '上月' } : {});
      expect(spec.type).toBe('view');
      const [value, delta] = texts(spec);
      expect(delta).toBeDefined();
      for (const t of [value, delta]) {
        expect(topInCanvas(spec, t.style)).toBeGreaterThanOrEqual(0);
        expect(bottomInCanvas(spec, t.style)).toBeLessThanOrEqual(120);
      }
      expect(delta.style.y).toBeGreaterThan(value.style.y);   // 版式顺序不得颠倒
    }
  });

  it('水平居中：paddingLeft + cx 等于画布宽的一半（旧写法 x=width/2 整体右偏 24px）', () => {
    for (const type of ['big_number_trend', 'text_display']) {
      const spec = kpi(type, type === 'text_display' ? { momColumn: '上月' } : {}, 345, 120);
      for (const t of texts(spec)) expect(spec.padding[1] + t.style.x).toBeCloseTo(345 / 2, 6);
    }
  });

  it('上下留白按图高收敛：这两类图不渲染坐标轴，不再白占 40px 轴留白', () => {
    expect(kpiPaddingY(120)).toBe(12);
    expect(kpiPaddingY(400)).toBe(16);      // 上限：大图不必留太多
    expect(kpiPaddingY(30)).toBe(6);        // 下限：再小也得有呼吸位
    expect(kpi('big_number_trend', {}, 345, 120).padding).toEqual([12, 16, 12, 16]);
  });

  it('字号随可用高度收敛，并以基准/配置字号为上限（不是无条件放大）', () => {
    const sizeOf = (h: number) => texts(kpi('big_number_trend', {}, 345, h))[0].style.fontSize;
    expect(sizeOf(120)).toBe(36);                     // 装得下就按基准，不动版面
    expect(sizeOf(48)).toBeLessThan(sizeOf(120));     // 挤不下才缩
    // text_display 的「数值字号」滑杆（默认 48）仍是上限，但不得把涨幅顶出画布
    const cfg = { momColumn: '上月', valueFontSize: 96 };
    const spec = kpi('text_display', cfg, 345, 120);
    const [value, delta] = texts(spec);
    expect(value.style.fontSize).toBeLessThanOrEqual(96);
    expect(bottomInCanvas(spec, delta.style)).toBeLessThanOrEqual(120);
  });

  it('窄卡片按宽度回缩：长涨幅文案（同比 | 环比）不溢出绘图区，也不跌破可读下限', () => {
    const spec = kpi('text_display', { yoyColumn: '去年', momColumn: '上月' }, 200, 120);
    const [, delta] = texts(spec);
    expect(delta.style.fontSize).toBeLessThan(14);              // 确实按宽缩了
    expect(delta.style.fontSize).toBeGreaterThanOrEqual(9);     // 缩没 = 静默丢信息
    expect(estimateTextWidth(delta.style.text, delta.style.fontSize))
      .toBeLessThanOrEqual(200 - spec.padding[1] * 2);
  });

  it('showComparison=false 才不出涨幅行（显式关闭 ≠ 被裁掉）', () => {
    expect(texts(kpi('text_display', { momColumn: '上月', showComparison: false }))).toHaveLength(1);
    expect(texts(kpi('text_display', { momColumn: '上月' }))).toHaveLength(2);
  });

  it('趋势线画在文字之下：children[0] 是 line，数值/涨幅不被折线压住', () => {
    const spec = kpi('big_number_trend', {});
    expect(spec.children[0].type).toBe('line');
    expect(spec.children.slice(1).every((c: any) => c.type === 'text')).toBe(true);
  });

  it('KPI 分支不得再出现基于整图高的写死偏移（必须经 kpiNumberLayout）', () => {
    const src = fs.readFileSync(path.resolve(__dirname, '..', 'DashboardChart.tsx'), 'utf8');
    const seg = src.slice(src.indexOf("case 'text_display'"), src.indexOf("case 'timeseries_line'"));
    expect(seg).toContain('kpiNumberLayout');
    expect(seg).not.toMatch(/x: width \/ 2/);
    expect(seg).not.toMatch(/y: height \/ 2/);
  });
});
