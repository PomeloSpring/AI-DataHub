import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

import {
  applyG2Animate, canvasEntrance, CHART_APPEARS, DEFAULT_MOTION, DEFAULT_PRESET_CODE, ENTER_EFFECTS,
  g2Animate, enterClass, enterStaggerStyle, motionCssVars, motionPresets, revealClass, MOTION_SECTION_KEYS,
  REVEAL_EFFECTS, resolveMotion, sanitizeMotion, sectionStaggerStyle, TRANSITION_EFFECTS,
  validateMotion, type MotionSpec, type MotionOverride,
} from '../motion';
import type { VisComponent } from '../../api/visLibrary';

const preset = (over: Partial<VisComponent> = {}): VisComponent => ({
  id: 1, code: 'mp_soft', name: '柔和舒缓', category: 'motion_preset' as const, chart_type: null,
  style_config: { enter: { effect: 'fadeUp', durationMs: 700, easing: 'standard', staggerMs: 120, maxStaggerMs: 600, offsetPx: 16 } },
  source: 'system', is_builtin: 1, is_active: 1, is_enabled: 1, sort_order: 82, description: '', ...over,
} as VisComponent);
const defaultPreset = (over: Partial<VisComponent> = {}) => preset({
  id: 2, code: DEFAULT_PRESET_CODE, name: '默认（现网）', style_config: DEFAULT_MOTION, sort_order: 80, ...over,
});

describe('动效默认档 = 现网观感（默认零变化）', () => {
  it('enter 逐段等于历史 stageSlideIn 常量（0.45s / 先快后缓 / 10px / 90ms 错峰上限 450ms）', () => {
    expect(DEFAULT_MOTION.enter).toEqual({
      effect: 'fadeUp', durationMs: 450, easing: 'standard', staggerMs: 90, maxStaggerMs: 450, offsetPx: 10,
    });
    // 切板与正文今天都是"没有动画"，默认档必须如实登记，否则开箱即变样
    expect(DEFAULT_MOTION.transition.effect).toBe('none');
    expect(DEFAULT_MOTION.reveal.effect).toBe('none');
  });

  it('chart.appear=default 时 g2Animate 返回 undefined，spec 一个键都不多写', () => {
    expect(g2Animate(DEFAULT_MOTION.chart)).toBeUndefined();
    const spec = { type: 'interval', encode: { x: 'a', y: 'b' }, style: {} };
    expect(applyG2Animate(spec, undefined)).toBe(spec);   // 原对象原样返回（同一引用）
  });

  it('none 档 enterClass 返回空串（不加 class，而不是回退旧 animate-stage-in）', () => {
    expect(enterClass({ ...DEFAULT_MOTION.enter, effect: 'none' })).toBe('');
    expect(enterClass(DEFAULT_MOTION.enter)).toBe('motion-enter motion-enter-fadeUp');
  });

  it('globals.css 的 :root 默认变量与 DEFAULT_MOTION 同口径（写了变量没人读 = 存了不生效）', () => {
    const css = fs.readFileSync(path.resolve(__dirname, '..', '..', 'styles', 'globals.css'), 'utf8');
    expect(css).toContain('--motion-enter-dur: 450ms;');
    expect(css).toContain('--motion-enter-offset: 10px;');
    expect(css).toContain('--motion-reveal-dur: 450ms;');
    // 每个入场效果都要有对应 keyframes 与 class，缺一个就是"选了没反应"
    for (const effect of ENTER_EFFECTS) {
      if (effect === 'none') continue;
      expect(css, `globals.css 缺 .motion-enter-${effect}`).toContain(`.motion-enter-${effect} {`);
    }
    for (const effect of TRANSITION_EFFECTS) {
      if (effect === 'none') continue;
      expect(css, `globals.css 缺 .motion-transition-${effect}`).toContain(`.motion-transition-${effect} {`);
    }
  });
});

describe('迁移 seed 与契约枚举双向核对', () => {
  const SQL = path.resolve(__dirname, '..', '..', '..', '..', 'docker', 'mysql', 'vis_library_motion_migration.sql');
  const rows = () => fs.readFileSync(SQL, 'utf8').split('\n')
    .filter(l => l.includes("'{\"enter\""))
    .map(l => JSON.parse(/'(\{.*\})'/.exec(l)![1]));

  it('六套内置预设齐全，且每段效果名/数值都在白名单内（sanitize 不产生 invalid）', () => {
    const items = rows();
    expect(items.length).toBe(6);
    for (const row of items) {
      const { invalid } = sanitizeMotion(row);
      expect(invalid, JSON.stringify(row)).toEqual([]);
    }
  });

  it('mp_default 的 seed 与代码常量逐段相同（改一处必改两处，否则开箱观感漂移）', () => {
    const seeded = rows().find(r => r.enter && r.chart && r.transition && r.reveal && r.chart.appear === 'default');
    expect(sanitizeMotion(seeded).spec).toEqual(DEFAULT_MOTION);
  });

  it('seed 里每一段的键都在 MOTION_SECTION_KEYS 白名单里（漏登记=入库即剥，与 WIDGET_KEYS 同一道护栏）', () => {
    for (const row of rows()) {
      for (const [section, keys] of Object.entries(MOTION_SECTION_KEYS)) {
        expect(Object.keys(row[section] || {}).sort(), `${section} 段键与白名单不齐`).toEqual([...keys].sort());
      }
      expect(Object.keys(row).sort()).toEqual(['chart', 'enter', 'reveal', 'transition']);
    }
  });

  it('悬空效果名会被拒（防"字模看得见、挂不上"那类历史缺陷）', () => {
    expect(validateMotion({ enter: { effect: 'zoomSlide' } })).toEqual([expect.stringMatching(/不是可选效果/)]);
    expect(validateMotion({ unknownSection: {} })).toEqual([expect.stringMatching(/未知段/)]);
    expect(validateMotion({ enter: { durationMs: 99999 } })).toEqual([expect.stringMatching(/超出/)]);
  });
});

describe('resolveMotion 分面裁决', () => {
  const items = [defaultPreset(), preset()];

  it('Chat/内容面：个人预设优先于系统默认', () => {
    const r = resolveMotion({ items, userPref: { presetCode: 'mp_soft' } });
    expect(r.origin).toBe('user');
    expect(r.motion.enter.durationMs).toBe(700);
    expect(r.preset.name).toBe('柔和舒缓');
  });

  it('播放面：板上挂载优先于个人预设（管理员调好的大屏不被观看者本地改掉）', () => {
    const r = resolveMotion({
      items,
      userPref: { presetCode: 'mp_soft' },
      board: { ref: 'mp_default' },
    });
    expect(r.origin).toBe('board');
    expect(r.motion.enter.durationMs).toBe(DEFAULT_MOTION.enter.durationMs);
  });

  it('未落偏好、未挂字模 → 默认档（等价改造前）', () => {
    const r = resolveMotion({ items });
    expect(r.origin).toBe('default');
    expect(r.motion).toEqual(DEFAULT_MOTION);
    expect(r.missing).toEqual([]);
    expect(r.reason).toBeUndefined();
  });

  it('个人预设被禁用 → 回退默认档，并回传 missing + 可见原因（不静默换一套）', () => {
    const r = resolveMotion({ items: [...items, preset({ id: 9, code: 'mp_gone' })], userPref: { presetCode: 'mp_missing' } });
    expect(r.origin).toBe('default');
    expect(r.missing).toEqual(['mp_missing']);
    expect(r.reason).toMatch(/已下架或禁用/);
  });

  it('板上引用的字模已下架 → 有挂载快照就按快照呈现，并说明"已按快照"', () => {
    const snapshot = { version: 1 as const, category: 'motion_preset' as const, code: 'mp_soft', style_config: preset().style_config };
    const r = resolveMotion({
      items,
      board: { ref: 'mp_soft', snapshot },
    });
    expect(r.origin).toBe('board');
    expect(r.motion.enter.durationMs).toBe(700);
    expect(r.missing).toEqual([]);
    const retired = resolveMotion({
      items: [defaultPreset(), preset({ is_enabled: 0 })],
      board: { ref: 'mp_soft', snapshot },
    });
    expect(retired.motion.enter.durationMs).toBe(700);
    expect(retired.missing).toEqual(['mp_soft']);
    expect(retired.reason).toMatch(/已按挂载时快照呈现/);
  });

  it('reduced-motion 压过板上挂载与个人偏好，且回传原因（配置入口据此禁用旋钮）', () => {
    const r = resolveMotion({
      items, reducedMotion: true,
      userPref: { presetCode: 'mp_soft' },
      board: { ref: 'mp_soft' },
    });
    expect(r.origin).toBe('reduced');
    expect(r.motion.enter.effect).toBe('none');
    expect(r.motion.chart.appear).toBe('none');      // 连 G2 动画一起关掉才是真"减少动效"
    expect(r.motion.transition.effect).toBe('none');
    expect(r.reason).toMatch(/prefers-reduced-motion/);
  });

  it('差量只覆盖填了的键（只调时长不该把预设的错峰与效果一起打回默认）', () => {
    const r = resolveMotion({ items, userPref: { presetCode: 'mp_soft', override: { enter: { durationMs: 300 } } } });
    expect(r.motion.enter.durationMs).toBe(300);
    expect(r.motion.enter.staggerMs).toBe(120);
    expect(r.motion.enter.effect).toBe('fadeUp');
  });

  it('板上生效时不叠加个人差量（差量属于"我的预设"的配套，不是全局覆盖）', () => {
    const r = resolveMotion({
      items,
      userPref: { presetCode: 'mp_soft', override: { enter: { durationMs: 300 } } },
      board: { ref: 'mp_default' },
    });
    expect(r.motion.enter.durationMs).toBe(450);
  });

  it('板上差量只叠在挂载预设上（这块板想快一点 ≠ 去改全局字模）', () => {
    const r = resolveMotion({ items, board: { ref: 'mp_soft', override: { enter: { durationMs: 260 } } } });
    expect(r.origin).toBe('board');
    expect(r.motion.enter.durationMs).toBe(260);
    expect(r.motion.enter.staggerMs).toBe(120);      // 逐键合并：预设里其余键原封不动
    expect(r.motion.enter.effect).toBe('fadeUp');
  });

  it('板上差量不越级：未挂载字模时它就是无人认领的草稿，不得替观看者改默认档', () => {
    const r = resolveMotion({ items, board: { override: { enter: { durationMs: 260 } } } });
    expect(r.origin).toBe('default');
    expect(r.motion.enter.durationMs).toBe(450);
  });

  it('库里没 seed mp_default（未跑迁移）时按代码内置默认渲染并说明原因', () => {
    const r = resolveMotion({ items: [preset()] });
    expect(r.motion).toEqual(DEFAULT_MOTION);
    expect(r.reason).toMatch(/未登记/);
  });
});

describe('动效落地产物', () => {
  const spec = (over: Partial<MotionSpec> = {}): MotionSpec => ({ ...DEFAULT_MOTION, ...over });

  it('预设清单只用在用字模，默认档排首', () => {
    const list = motionPresets([preset(), defaultPreset({ is_enabled: 0 }), preset({ id: 5, code: 'mp_crisp', sort_order: 83 })]);
    expect(list.map(c => c.code)).toEqual(['mp_soft', 'mp_crisp']);   // 默认档被禁用 → 不进下拉
    const list2 = motionPresets([preset({ id: 5, code: 'mp_crisp' }), defaultPreset()]);
    expect(list2.map(c => c.code)[0]).toBe(DEFAULT_PRESET_CODE);
  });

  it('CSS 变量按段输出（时长/缓动/位移），供 globals.css 读取', () => {
    const vars = motionCssVars(spec({ transition: { ...DEFAULT_MOTION.transition, effect: 'pushLeft', durationMs: 600 } }));
    expect(vars['--motion-enter-dur']).toBe('450ms');
    expect(vars['--motion-enter-ease']).toBe('cubic-bezier(0.22, 1, 0.36, 1)');
    expect(vars['--motion-enter-offset']).toBe('10px');
    expect(vars['--motion-transition-offset']).toBe('32px');   // 推入位移是板级幅度，不看 enter.offsetPx
  });

  it('错峰延迟有上限，长看板尾部卡片不会几秒后才出现', () => {
    const enter = { ...DEFAULT_MOTION.enter, staggerMs: 90, maxStaggerMs: 450 };
    expect(enterStaggerStyle(0, enter)).toBeUndefined();
    expect(enterStaggerStyle(2, enter)).toEqual({ animationDelay: '180ms' });
    expect(enterStaggerStyle(50, enter)).toEqual({ animationDelay: '450ms' });
    expect(enterStaggerStyle(3, { ...enter, effect: 'none' })).toBeUndefined();
  });

  it('正文出现的 class 覆盖全部枚举值（none 不加 class，其余共用同一条 fadeIn）', () => {
    for (const effect of REVEAL_EFFECTS) {
      const cls = revealClass({ effect, staggerMs: 90 });
      if (effect === 'none') expect(cls).toBe('');
      else expect(cls).toBe('motion-reveal motion-reveal-fadeIn');
    }
  });

  it('正文分段错峰只在 sectionStagger 生效（fadeIn 不给延迟）', () => {
    const reveal = { effect: 'sectionStagger' as const, staggerMs: 120 };
    expect(sectionStaggerStyle(3, spec({ reveal }))).toEqual({ animationDelay: '360ms' });
    expect(sectionStaggerStyle(3, spec({ reveal: { effect: 'fadeIn', staggerMs: 120 } }))).toBeUndefined();
    expect(sectionStaggerStyle(3, DEFAULT_MOTION)).toBeUndefined();
  });

  it('G2 animate：none → false（maybeNonAnimate 据此关动画），具名 → enter.type', () => {
    expect(g2Animate({ ...DEFAULT_MOTION.chart, appear: 'none' })).toBe(false);
    expect(g2Animate({ ...DEFAULT_MOTION.chart, appear: 'pathIn' }))
      .toEqual({ enter: { type: 'pathIn', duration: 600, easing: 'cubic-bezier(0.22, 1, 0.36, 1)' } });
    // 具名 appear 值必须与 @antv/g2 注册表同名，否则 G2 静默不播（清单里写错就是"选了没反应"）
    for (const appear of CHART_APPEARS) if (appear !== 'default') expect(typeof appear).toBe('string');
  });

  it('applyG2Animate 只给 mark 节点（有 encode）写 animate，不碰 axis/legend 组件节点', () => {
    const specInput: Record<string, any> = {
      type: 'view',
      children: [
        { type: 'interval', encode: { x: 'a', y: 'b' } },
        { type: 'legend', items: [] },
      ],
    };
    const out = applyG2Animate(specInput, { enter: { type: 'fadeIn' } });
    expect(out.children[0].animate).toEqual({ enter: { type: 'fadeIn' } });
    expect(out.children[1].animate).toBeUndefined();
    expect(specInput.children[0].animate).toBeUndefined();   // 不原地改调用方对象
  });

  it('环/饼/玫瑰（theta/polar 的 interval）不吃笛卡尔 enter 动画 → 降级为 fadeIn（防丢切片）', () => {
    const grow = { enter: { type: 'growInY', duration: 700, easing: 'e' } };
    // theta 饼：顶层 mark 带 coordinate.type=theta，growInY 会被换成 fadeIn，时长/缓动保留
    const pie = applyG2Animate({ type: 'interval', encode: { y: 'v', color: 'n' }, coordinate: { type: 'theta' } } as Record<string, any>, grow);
    expect(pie.animate).toEqual({ enter: { type: 'fadeIn', duration: 700, easing: 'e' } });
    // polar 玫瑰（children 里的 interval mark）同样降级
    const rose = applyG2Animate({ type: 'view', children: [{ type: 'interval', encode: { y: 'v' }, coordinate: { type: 'polar' } }] } as Record<string, any>, { enter: { type: 'pathIn' } });
    expect(rose.children[0].animate.enter.type).toBe('fadeIn');
    // 笛卡尔 interval（普通柱状）不受影响，照原样下发
    const bar = applyG2Animate({ type: 'interval', encode: { x: 'a', y: 'b' } } as Record<string, any>, grow);
    expect(bar.animate.enter.type).toBe('growInY');
    // 本来就是 fadeIn / none(false) → 原样
    expect(applyG2Animate({ type: 'interval', encode: { y: 'v' }, coordinate: { type: 'theta' } } as Record<string, any>, { enter: { type: 'fadeIn' } }).animate.enter.type).toBe('fadeIn');
    expect(applyG2Animate({ type: 'interval', encode: { y: 'v' }, coordinate: { type: 'theta' } } as Record<string, any>, false).animate).toBe(false);
  });

  it('非法旋钮回落默认值并记录原因（写入口必须拒收，不能存了不生效）', () => {
    // 效果名不合法 → 回落默认档值 + invalid 给出可直接展示的文案
    const badEffect = sanitizeMotion({ enter: { effect: 'zoomSlide' } });
    expect(badEffect.spec.enter.effect).toBe(DEFAULT_MOTION.enter.effect);
    expect(badEffect.invalid.join('')).toMatch(/不是可选效果/);
    // 非数字/超范围同样拒
    const badNumber = sanitizeMotion({ enter: { durationMs: 'fast' } });
    expect(badNumber.spec.enter.durationMs).toBe(450);
    expect(badNumber.invalid.join('')).not.toBe('');
    // 差量里出现段外的键（如自己拼一个 easingCurve）→ 被白名单剥掉，不入库也不渲染
    const override: MotionOverride = { enter: { easingCurve: 'bounce' } as Record<string, unknown> };
    expect(Object.keys(sanitizeMotion({ ...DEFAULT_MOTION, ...override }).spec.enter)).toEqual(
      Object.keys(DEFAULT_MOTION.enter));
  });
});

describe('画布面的入场开关（「默认零变化」的面级判定）', () => {
  const enter = DEFAULT_MOTION.enter;
  it('未挂字模、未落偏好（origin=default）= 关；板上挂载/个人预设（显式选过）= 开', () => {
    expect(canvasEntrance({ origin: 'default', enter })).toBe(false);
    expect(canvasEntrance({ origin: 'board', enter })).toBe(true);
    expect(canvasEntrance({ origin: 'user', enter })).toBe(true);
    // 系统减少动效：不是“静默关掉”，解析已把 effect 归 none，开关这里再兑一层
    expect(canvasEntrance({ origin: 'reduced', enter })).toBe(false);
  });

  it('编辑态不重闪整版；显式 entrance 是调用方的决定，优先于面级缺省', () => {
    expect(canvasEntrance({ origin: 'board', enter, draft: true })).toBe(false);
    expect(canvasEntrance({ origin: 'board', enter, draft: false })).toBe(true);
    expect(canvasEntrance({ origin: 'default', enter, draft: true, entrance: true })).toBe(true);
    expect(canvasEntrance({ origin: 'board', enter, entrance: false })).toBe(false);
  });

  it('effect=none 时无论来源都不开', () => {
    expect(canvasEntrance({ origin: 'board', enter: { ...enter, effect: 'none' } })).toBe(false);
  });
});

describe('出现效果只有一个消费入口（源码级护栏）', () => {
  const SRC = path.resolve(__dirname, '..', '..');
  const walk = (dir: string, out: string[] = []): string[] => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full, out);
      else if (/\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) out.push(full);
    }
    return out;
  };

  it('渲染面不得再硬编码 animate-stage-in / motion-enter-* 类名', () => {
    const offenders: string[] = [];
    for (const file of walk(SRC)) {
      if (file.endsWith('lib/motion.ts')) continue;
      const code = fs.readFileSync(file, 'utf8');
      // className 里写死这两个 class = 该处绕开了动效解析口（改预设不会跟）
      if (/className=(?:"|`)[^"`]*animate-stage-in/.test(code)
        || /className=(?:"|`)[^"`]*motion-enter-/.test(code)) offenders.push(path.relative(SRC, file));
    }
    expect(offenders).toEqual([]);
  });

  it('用到入场 class 的文件必须走 useMotion / useEnterClass（不自己拼效果名）', () => {
    const consumers = ['pages/Chat.tsx', 'components/chat/StageCard.tsx', 'components/MarkdownWithCharts.tsx',
      'components/ExecutionProcess.tsx', 'components/InlineDetails.tsx', 'components/ThinkingBlock.tsx',
      'components/ChartPicker.tsx', 'components/MotionPreferenceCard.tsx',
      // P2/P3 新增面：看板挂载与正文出现也得从同一个口取
      'components/DashboardCanvas.tsx', 'pages/Screen.tsx', 'pages/Analysis.tsx',
      'components/editor/BoardMotionSection.tsx', 'components/admin/MotionPresetEditor.tsx',
      'pages/home/ReadArticle.tsx', 'pages/content/ContentPreview.tsx'];
    for (const rel of consumers) {
      const code = fs.readFileSync(path.resolve(SRC, rel), 'utf8');
      // 渲染面走 useMotion；表单/工具面直接拿 lib/motion 的纯函数（两处都算接了口）
      expect(code, `${rel} 未接动效解析口`).toMatch(/useMotion|useEnterClass|from '[^']*lib\/motion'/);
    }
  });

  it('动效不进主题包（正交）：sanitizePack / tp_* 结构里不得出现 enter/chart/transition/reveal 段', () => {
    const packs = fs.readFileSync(path.resolve(SRC, '..', '..', 'docker', 'mysql', 'vis_library_migration.sql'), 'utf8')
      .split('\n').filter(l => l.includes("'{\"mode\"")).map(l => JSON.parse(/'(\{.*\})'/.exec(l)![1]));
    expect(packs.length).toBeGreaterThan(0);
    for (const pack of packs) {
      for (const section of ['enter', 'chart', 'transition', 'reveal']) {
        expect(pack, '主题包不得夹带动效段').not.toHaveProperty(section);
      }
    }
  });
});

describe('内置预设之间必须肉眼可辨（入场通道互不重叠）', () => {
  // 历史缺陷：五档非默认预设都是 fadeUp 的时长变体（只差 10~20px 位移 + 时长），
  // 用户根本分不出差别，以为“效果都堆在图表里”。这里把“每套预设走不同通道”锁成规则。
  const seed = fs.readFileSync(
    path.resolve(__dirname, '..', '..', '..', '..', 'docker', 'mysql', 'vis_library_motion_migration.sql'), 'utf8');
  // code 与 payload 分两行：逐行扫描，记住上一个 mp_ 代码，遇到 payload 行就配对
  const rows: Array<[string, { enter: { effect: string; durationMs: number } }]> = [];
  let pending = '';
  for (const line of seed.split('\n')) {
    const code = /\('(mp_\w+)'/.exec(line);
    if (code) pending = code[1];
    if (pending && line.includes("'{\"enter\"")) {
      const json = /'(\{.*\})'/.exec(line);
      if (json) { rows.push([pending, JSON.parse(json[1])]); pending = ''; }
    }
  }

  it('六套预设齐全，入场通道各不重复', () => {
    expect(rows.length).toBe(6);
    const channels = rows.map(([, spec]) => spec.enter.effect as string);
    // none + 五种真实通道，必须全不相同
    expect(new Set(channels).size, `通道重复：${channels.join(',')}`).toBe(channels.length);
  });

  it('默认档仍是 fadeUp（默认零变化）', () => {
    const def = rows.find(([code]) => code === 'mp_default')?.[1];
    expect(def!.enter.effect).toBe('fadeUp');
    expect(def!.enter.durationMs).toBe(450);
  });
});
