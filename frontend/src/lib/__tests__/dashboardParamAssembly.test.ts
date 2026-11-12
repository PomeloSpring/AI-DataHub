/**
 * 引入数据集时的参数装配计划（纯函数）。
 *
 * 这里锁的是"自动装配"的行为边界：形状来自服务端摘要（前端不重推）、
 * 幂等（可反复引入）、判不出的不猜控件、已有参数不重复新建。
 */
import { describe, expect, it } from 'vitest';
import {
  boundParamKeys, buildAssemblyPlan, CONTROL_BY_PARAM_SHAPE, CONTROL_LABELS, EMPTY_ASSEMBLY_PLAN,
  pageParamTypeOf, PARAM_SHAPES, PARAM_SHAPE_LABELS, paramBusinessName, paramDiagnosticHint, shapeOfParam,
  suggestControlPositions, uncontrolledParams,
  type DatasetParamSummary, type DatasetWithParams,
} from '../dashboardParamAssembly';
import { naturalSizeOf, minReadableWidth, WIDGET_DEFAULT_WIDTH, WIDGET_DEFAULT_HEIGHT,
  WIDGET_DEFAULT_HEIGHT_BY_TYPE, CONTENT_WIDGET_TYPES } from '@/lib/dashboardDesign';
import { CHART_TYPES } from '@/components/DashboardChart';

const tw = (over: Partial<DatasetParamSummary> = {}): DatasetParamSummary => ({
  key: 'time_window', label: 'time_window', kind: 'time_window', value_type: 'date_range',
  control: 'widget_daterange', options: [], required: false, default: null, referenced: true,
  conflicts: [], pending: false, suggested_label: '统计时间窗口', fallback_days: 30,
  vars: [{ var: 'start_time', role: 'range_start', type: 'string' }], ...over,
});

const site = (over: Partial<DatasetParamSummary> = {}): DatasetParamSummary => ({
  key: 'site', label: 'site', kind: 'scalar', value_type: 'string', control: 'widget_text',
  options: [], required: false, default: '', referenced: true, conflicts: [], pending: false,
  suggested_label: '', vars: [{ var: 'site', role: 'optional_eq', type: 'string' }], ...over,
});

const ds = (id: number, name: string, params: DatasetParamSummary[]): DatasetWithParams =>
  ({ id, name, param_summary: params });

describe('buildAssemblyPlan', () => {
  it('时间窗口 → 新建 date_range 参数 + 放一个日期范围控件', () => {
    const plan = buildAssemblyPlan([ds(41, '版本统计·App 版本分布', [tw()])]);
    expect(plan.addParams).toEqual([
      { name: 'time_window', label: '统计时间窗口', type: 'date_range', default: '' },
    ]);
    expect(plan.addControls).toHaveLength(1);
    expect(plan.addControls[0]).toMatchObject({
      key: 'time_window', control: 'widget_daterange', label: '统计时间窗口', fallback_days: 30,
    });
  });

  it('显示名优先用数据集声明的 label，其次按形状的建议名，最后才是键名', () => {
    expect(buildAssemblyPlan([ds(1, 'A', [tw({ label: '统计区间' })])]).addParams[0].label).toBe('统计区间');
    expect(buildAssemblyPlan([ds(1, 'A', [tw({ suggested_label: '' })])]).addParams[0].label).toBe('time_window');
  });

  it('多个数据集共用同一逻辑参数时只补一份（异名同形状在后端已归一）', () => {
    const plan = buildAssemblyPlan([
      ds(41, 'App 版本分布', [tw()]),
      ds(55, '设备数据流', [tw({ vars: [{ var: 'start_date', role: 'range_start', type: 'date' }] })]),
    ]);
    expect(plan.addParams).toHaveLength(1);
    expect(plan.addControls).toHaveLength(1);
    expect(plan.addControls[0].datasetNames).toEqual(['App 版本分布', '设备数据流']);
  });

  it('单值参数落文本控件，带候选值的落下拉并把候选带过去', () => {
    const plan = buildAssemblyPlan([ds(32, '异常上报', [
      site(), tw({ key: 'level', label: 'level', kind: 'scalar', value_type: 'enum',
                   control: 'widget_select', options: ['致命', '严重', '提示'], suggested_label: '' }),
    ])]);
    const level = plan.addControls.find(c => c.key === 'level');
    expect(level).toMatchObject({ control: 'widget_select', options: ['致命', '严重', '提示'] });
    // 候选值参数登成选择框（登成文本框会丢下拉，用户得手打枚举值）
    expect(plan.addParams.find(p => p.name === 'level')?.type).toBe('select');
  });

  it('已有同名参数就复用，不重复新建', () => {
    const plan = buildAssemblyPlan([ds(41, 'A', [tw()])],
      [{ name: 'time_window', label: '统计时间', type: 'date_range', default: '' }]);
    expect(plan.addParams).toEqual([]);
    expect(plan.reuseParams).toEqual([{ key: 'time_window', label: '统计时间', type: 'date_range' }]);
  });

  it('已有同名但类型不符 → 列为待改判，不擅自改看板参数', () => {
    const plan = buildAssemblyPlan([ds(41, 'A', [tw()])],
      [{ name: 'time_window', label: '统计时间', type: 'string', default: '' }]);
    expect(plan.mismatchParams).toEqual([{
      // 显示名沿看板上已有的叫法：装配不该顺手改用户熟悉的词
      key: 'time_window', label: '统计时间', current: 'string', want: 'date_range',
      datasetNames: ['A'],
    }]);
  });

  it('画布上已有控件在驱动该键时不再重复放控件（幂等：连点两次引入只一排控件）', () => {
    const first = buildAssemblyPlan([ds(41, 'A', [tw()])]);
    const again = buildAssemblyPlan([ds(41, 'A', [tw()])], first.addParams, ['time_window']);
    expect(again.addControls).toEqual([]);
    expect(again.keptControls).toEqual(['time_window']);
    expect(again.addParams).toEqual([]);
  });

  it('判不出形状与口径常数都不自动建控件，只在计划里说明', () => {
    const plan = buildAssemblyPlan([ds(51, 'A', [
      tw({ key: 'range_days', label: 'range_days', kind: 'range', value_type: '', control: '',
           pending: true, suggested_label: '', vars: [{ var: 'days', role: 'range_start', type: 'int' }] }),
      tw({ key: 'window_sec', label: 'window_sec', kind: 'constant', value_type: 'number', control: '',
           pending: false, suggested_label: '口径常数', vars: [{ var: 'window_sec', role: 'func_arg', type: 'int' }] }),
    ])]);
    expect(plan.addControls).toEqual([]);
    expect(plan.pending).toHaveLength(1);
    expect(plan.pending[0].reason).toContain('区间参数');
    expect(plan.constants.map(c => c.key)).toEqual(['window_sec']);
    // 待确认/常数行显示数据集侧的变量名本身：换成形状建议名会一排「口径常数」认不出是谁
    expect(plan.constants[0].label).toBe('window_sec');
    expect(plan.pending[0].label).toBe('range_days');
  });

  it('跨数据集值类型不一致时收集成分歧，不静默挑一个', () => {
    const plan = buildAssemblyPlan([
      ds(1, 'A', [site()]),
      ds(2, 'B', [site({ value_type: 'number', control: 'widget_number' })]),
    ]);
    expect(plan.conflicts.join(' ')).toContain('值类型');
  });

  it('没有任何入参的数据集给出空计划（不弹一个空对话框打扰人）', () => {
    expect(buildAssemblyPlan([ds(9, '无入参', [])])).toEqual(EMPTY_ASSEMBLY_PLAN);
    expect(buildAssemblyPlan([])).toEqual(EMPTY_ASSEMBLY_PLAN);
  });

  it('时间窗口装配后要说明"按各数据集形状落地"，让人知道口径一致', () => {
    const plan = buildAssemblyPlan([ds(41, 'A', [tw(), site()])]);
    expect(plan.notes.join(' ')).toContain('相对天数');
  });
});

describe('boundParamKeys / 摆放 / 类型映射', () => {
  it('只从控件行收集键，bind_param 的逗号多键都算已驱动', () => {
    const charts = [
      { chart_type: 'widget_daterange', config: { paramKey: 'time_window' } },
      { chart_type: 'widget_select', config: { bind_param: 'site, region' } },
      { chart_type: 'bar', config: { paramKey: 'ignored' } },
    ];
    expect(boundParamKeys(charts)).toEqual(['time_window', 'site', 'region']);
  });

  it('摆放按每个控件自己的自然尺寸排，超出画布宽度换行而不叠在同一格', () => {
    // 统一尺寸（旧行为）会把 440 宽的区间控件与 180 宽的文本框当一个宽排，一排里区间控件会被挤到只显一半
    const sizes = [{ w: 180, h: 28 }, { w: 440, h: 32 }];
    // 192 + 440 = 632 ≤ 700 → 同一行排开
    expect(suggestControlPositions(2, { x: 0, y: 400 }, sizes, 700))
      .toEqual([{ x: 0, y: 400 }, { x: 192, y: 400 }]);
    // 第二个（440）超出 600 宽画布 → 换行，行高按它自己的高算
    expect(suggestControlPositions(2, { x: 0, y: 400 }, sizes, 600))
      .toEqual([{ x: 0, y: 400 }, { x: 0, y: 444 }]);
    // 单一尺寸仍可用（向后兼容）
    expect(suggestControlPositions(2, { x: 0, y: 0 }, { w: 100, h: 32 })).toEqual([{ x: 0, y: 0 }, { x: 112, y: 0 }]);
  });

  it('值类型映射到看板参数类型（五种形状，候选值走选择框）', () => {
    expect(pageParamTypeOf('date_range')).toBe('date_range');
    expect(pageParamTypeOf('date')).toBe('date');
    expect(pageParamTypeOf('number')).toBe('number');
    // enum 不再降级成文本：以前这样写，下拉字段的参数被登成文本框
    expect(pageParamTypeOf('enum')).toBe('select');
    expect(pageParamTypeOf('')).toBe('string');
  });

  it('形状词汇只有一套：五种形状都有参数类型与控件，且控件名都在 CHART_TYPES 里', () => {
    expect(Object.keys(PARAM_SHAPE_LABELS)).toEqual(['string', 'number', 'date', 'date_range', 'select']);
    // 形状→控件 与 控件业务名字典必须一一对应（否则面板上叫一个名、装配弹窗叫另一个名）
    expect(Object.keys(CONTROL_BY_PARAM_SHAPE).sort()).toEqual(PARAM_SHAPES.slice().sort());
    Object.values(CONTROL_BY_PARAM_SHAPE).forEach(c => expect(CONTROL_LABELS[c]).toBeTruthy());
    expect(shapeOfParam({ type: 'text' })).toBe('string');
    expect(shapeOfParam({ type: 'select' })).toBe('select');
    expect(shapeOfParam({ type: '没见过的类型' })).toBe('string');
  });

  it('控件默认尺寸只有一套口径：按类型自然宽 + 统一高度（基准=设备数据流）', () => {
    // 原生日期框有内在最小宽，预设 + 起止两端一行要 18 列（530px）；给小了就是“只有开始日期”
    expect(naturalSizeOf('widget_daterange')).toEqual({ w: 530, h: 40 });
    expect(naturalSizeOf('widget_date')).toEqual({ w: 257, h: 40 });
    expect(naturalSizeOf('widget_search')).toEqual({ w: 75, h: 40 });
    expect(naturalSizeOf('widget_text')).toEqual({ w: 135, h: 40 });
    // 可读下限：低于它就显式报“只展示一半”，不静默裁切
    expect(minReadableWidth('widget_daterange')).toBe(420);
    expect(minReadableWidth('widget_text')).toBe(0);
  });

  it('每个控件类型都配了默认宽度，且表单控件高度全一致（新增控件漏配就报错）', () => {
    const widgets = CHART_TYPES.filter(c => c.value.startsWith('widget_')).map(c => c.value);
    expect(widgets.length).toBeGreaterThanOrEqual(10);   // 没解析到清单则本用例没有意义
    for (const type of widgets) {
      expect(WIDGET_DEFAULT_WIDTH, `${type} 没配默认宽度（会落到兼容默认，观感与基准不一致）`)
        .toHaveProperty(type);
      if (CONTENT_WIDGET_TYPES.includes(type)) {
        /* 内容型控件（如决策建议卡：命中路径 + 建议正文 + 依据表）不是一行表单控件，
           40px 会把它压成“只看到一行字”；豁免必须显式登记在 WIDGET_DEFAULT_HEIGHT_BY_TYPE，
           并且真的高于基线（登记了个 40 = 假装豁免）。 */
        expect(WIDGET_DEFAULT_HEIGHT_BY_TYPE[type], `${type} 在豁免名单里却没配高度`)
          .toBeGreaterThan(WIDGET_DEFAULT_HEIGHT);
        continue;
      }
      expect(naturalSizeOf(type).h).toBe(WIDGET_DEFAULT_HEIGHT);
    }
    // 表单控件高度只有一个值：同一排控件上下对齐（内容型控件不参与）
    const formWidgets = widgets.filter(t => !CONTENT_WIDGET_TYPES.includes(t));
    expect(new Set(formWidgets.map(t => naturalSizeOf(t).h)).size).toBe(1);
  });
});

describe('paramDiagnosticHint', () => {
  it('无诊断返回 null（不给正常图挂角标）', () => {
    expect(paramDiagnosticHint(undefined)).toBeNull();
    expect(paramDiagnosticHint({ unbound: [], notes: [] })).toBeNull();
  });

  it('首屏什么都没填 → 不挂角标（出全量是预期态，不是用户的错）', () => {
    const diag = { unbound: [{ key: 'site', label: 'site' }, { key: 'tid', label: 'tid' }] };
    // filledKeys=[] = 确实没提交任何条件
    expect(paramDiagnosticHint(diag, { filledKeys: [] })).toBeNull();
    // 给了别的条件（日期）但这张图不吃那几个 → 也不该告警
    expect(paramDiagnosticHint(diag, { filledKeys: ['start_time', 'end_time'] })).toBeNull();
  });

  it('已填条件却没被这张图接住 → 告警并指名参数', () => {
    const diag = { unbound: [{ key: 'site', label: 'site' }, { key: 'tid', label: 'tid' }] };
    const hint = paramDiagnosticHint(diag, { filledKeys: ['site'] });
    expect(hint?.text).toBe('已填条件未生效');
    expect(hint?.title).toContain('「site」已经填了值');
    expect(hint?.title).toContain('按数据集自身口径');
    // 没填的 tid 不得混进文案（否则用户以为自己要再去填 tid）
    expect(hint?.title).not.toContain('tid');
  });

  it('逻辑键靠 vars 匹配控件（time_window ← bind_param start_time,end_time）', () => {
    const diag = { unbound: [{ key: 'time_window', label: 'time_window', vars: ['start_time', 'end_time'] }] };
    const hint = paramDiagnosticHint(diag, { filledKeys: ['start_time', 'end_time'] });
    expect(hint?.text).toBe('已填条件未生效');
    // 无 ctx 时保守提示（编辑器试运行拿不到提交上下文）
    expect(paramDiagnosticHint(diag)?.text).toBe('已填条件未生效');
  });

  it('区间缺一端单独成文案，不能说成“没筛”', () => {
    const hint = paramDiagnosticHint(
      { unbound: [{ key: 'time_window', label: '统计时间窗口', partial: true }] },
      { filledKeys: ['time_window'] });
    expect(hint?.text).toBe('筛选区间不完整');
    expect(hint?.title).toContain('口径不完整');
  });
});

describe('paramBusinessName（角标文案用看板上的业务名，不露变量名）', () => {
  const charts = [
    { chart_type: 'widget_select', config: { paramKey: 'site', bind_param: 'site', label: '区域' } },
    { chart_type: 'widget_text', config: { paramKey: 'serial_number', label: '', placeholder: '设备序列号' } },
    { chart_type: 'widget_daterange', config: { paramKey: 'date_range', bind_param: 'start_time,end_time', label: '日期区间' } },
    { chart_type: 'bar', config: { label: '不是控件' } },
  ];

  it('控件 label 优先，label 空时用 placeholder（设备序列号就是这种写法）', () => {
    expect(paramBusinessName({ key: 'site', label: 'site' }, { charts })).toBe('区域');
    expect(paramBusinessName({ key: 'serial_number', label: 'serial_number' }, { charts })).toBe('设备序列号');
  });

  it('逻辑键用 vars 匹配 bind_param（time_window → 日期区间）', () => {
    expect(paramBusinessName({ key: 'time_window', label: 'time_window', vars: ['start_time', 'end_time'] },
      { charts })).toBe('日期区间');
  });

  it('没有输入口时回到看板参数 label，再退后端 label/键名（不编名字）', () => {
    expect(paramBusinessName({ key: 'level', label: 'level' },
      { charts, params: [{ name: 'level', label: '异常级别' }] })).toBe('异常级别');
    expect(paramBusinessName({ key: 'tid', label: 'tid' }, { charts })).toBe('tid');
    expect(paramBusinessName({ key: 'tid' }, { charts })).toBe('tid');
  });

  it('角标整句只讲业务名（回归护栏：不得把 site/serial_number 直接给用户）', () => {
    const diag = { unbound: [
      { key: 'site', label: 'site', vars: ['site'] },
      { key: 'serial_number', label: 'serial_number', vars: ['serial_number'] },
    ] };
    const hint = paramDiagnosticHint(diag, {
      filledKeys: ['site', 'serial_number'],
      nameOf: u => paramBusinessName(u, { charts }),
    });
    expect(hint?.title).toContain('「区域」');
    expect(hint?.title).toContain('「设备序列号」');
    expect(hint?.title).not.toMatch(/\bsite\b|serial_number/);
  });
});

describe('形状与口径细节（值类型/候选并集/站点/只读常量/区间缺一端）', () => {
  const region = (over: Partial<DatasetParamSummary> = {}): DatasetParamSummary => ({
    key: 'region', label: 'region', kind: 'scalar', value_type: 'enum', control: 'widget_select',
    options: ['华东'], required: false, default: '', referenced: true, conflicts: [], pending: false,
    suggested_label: '区域', vars: [{ var: 'region', role: 'eq', type: 'string' }], ...over,
  });

  it('值类型决定控件：枚举→下拉、数字→数字框、日期→日期选择器', () => {
    const plan = buildAssemblyPlan([ds(1, 'A', [
      region(), site({ value_type: 'number', control: 'widget_number' }),
      tw({ key: 'one_day', kind: 'scalar', value_type: 'date', control: 'widget_date' })])], [], []);
    const byKey = Object.fromEntries(plan.addControls.map(c => [c.key, c.control]));
    expect(byKey).toEqual({ region: 'widget_select', site: 'widget_number', one_day: 'widget_date' });
  });

  it('同一逻辑参数的候选值取并集，不互相覆盖', () => {
    const plan = buildAssemblyPlan([
      ds(1, 'A', [region({ options: ['华东', '华南'] })]),
      ds(2, 'B', [region({ options: ['华北', '华东'] })])], [], []);
    expect(plan.addControls.find(c => c.key === 'region')?.options).toEqual(['华东', '华南', '华北']);
  });

  it('站点是业务值通道：给下拉给候选，值原样下发、不换成内部数据源 id', () => {
    const plan = buildAssemblyPlan([ds(1, 'A', [site({ value_type: 'enum', control: 'widget_select',
      options: ['cn-east', 'cn-south'], suggested_label: '站点',
      vars: [{ var: 'site', role: 'optional_eq', type: 'string' }] })])], [], []);
    expect(plan.addControls[0].control).toBe('widget_select');
    expect(plan.addControls[0].options).toEqual(['cn-east', 'cn-south']);
    expect(plan.addParams[0]).toMatchObject({ name: 'site', label: '站点', type: 'select' });
    expect(plan.notes.some(n => n.includes('业务值'))).toBe(true);
  });

  it('正文没引用的只读常量：登记参数但不放控件，理由要说清', () => {
    const plan = buildAssemblyPlan([ds(1, 'A', [site({ referenced: false, suggested_label: '口径版本' })])], [], []);
    expect(plan.addControls).toHaveLength(0);
    expect(plan.addParams[0]).toMatchObject({ name: 'site', label: '口径版本' });
    expect(plan.notes.some(n => n.includes('正文并未引用'))).toBe(true);
  });

  it('区间只给到一端不假装凑窗口，落进待确认并带数据集名', () => {
    const plan = buildAssemblyPlan([ds(1, 'A', [tw({ control: '', pending: true })])], [], []);
    expect(plan.pending[0]).toMatchObject({ key: 'time_window', label: '统计时间窗口' });
    expect(plan.pending[0].datasetNames).toEqual(['A']);
  });

  it('同一个键只放一个控件（后端按物理名展开出多行也不重复放）', () => {
    const plan = buildAssemblyPlan([ds(1, 'A', [
      tw({ vars: [{ var: 'start_time', role: 'range_start', type: 'string' }] }),
      tw({ vars: [{ var: 'end_time', role: 'range_end', type: 'string' }] })])], [], []);
    expect(plan.addControls).toHaveLength(1);
    expect(plan.addParams).toHaveLength(1);
  });
});

describe('引入带参数的图表时补齐「搜索/重置」入口', () => {
  it('画布没有按钮 → 两个都列进计划（控件只改值，取数靠按钮）', () => {
    const plan = buildAssemblyPlan([ds(1, 'A', [tw()])], [], []);
    expect(plan.addButtons.map(b => b.control)).toEqual(['widget_search', 'widget_reset']);
    expect(plan.addButtons.map(b => b.label)).toEqual(['搜索', '重置']);
    expect(plan.keptButtons).toEqual([]);
  });

  it('画布已有搜索 → 只补缺失的重置，已有点单独说明', () => {
    const plan = buildAssemblyPlan([ds(1, 'A', [tw()])], [], [], ['widget_search']);
    expect(plan.addButtons.map(b => b.control)).toEqual(['widget_reset']);
    expect(plan.keptButtons).toEqual(['搜索']);
  });

  it('本次没有要补的参数与控件时不塞按钮（不给已有看板凭空加东西）', () => {
    const plan = buildAssemblyPlan([ds(1, 'A', [tw()])],
      [{ name: 'time_window', label: '统计时间窗口', type: 'date_range', default: '' }],
      ['time_window'], ['widget_search', 'widget_reset']);
    expect(plan.addButtons).toEqual([]);
    expect(plan.keptButtons).toEqual(['搜索', '重置']);
  });

  it('参数已由现有控件驱动、但画布没按钮 → 只补按钮（否则填了不会生效）', () => {
    const plan = buildAssemblyPlan([ds(1, 'A', [tw()])],
      [{ name: 'time_window', label: '统计时间窗口', type: 'date_range', default: '' }],
      ['time_window'], ['widget_daterange']);
    expect(plan.addControls).toEqual([]);
    expect(plan.keptControls).toEqual(['time_window']);
    expect(plan.addButtons.map(b => b.control)).toEqual(['widget_search', 'widget_reset']);
  });
});

describe('参数定义去重（uncontrolledParams）', () => {
  const driven = (k: string) => ({ id: 1, name: 'c', chart_type: 'widget_select', config: { paramKey: k } }) as never;
  const row = (name: string, over: Record<string, unknown> = {}) => ({ name, label: name, type: 'string', default: '', ...over });

  it('遗留 params 与 pageParams 同名时只留一份（pageParams 口径优先）', () => {
    const rows = uncontrolledParams({
      params: [row('start_time', { label: '开始日期' })],
      page_params: [row('start_time', { label: '开始时间' })],
    } as never, []);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toEqual({ param: { name: 'start_time', label: '开始时间', type: 'string', default: '' }, via: 'page' });
  });

  it('两套都保留、各自不同名时都渲染（都真的在下发）', () => {
    const rows = uncontrolledParams({ params: [row('a')], page_params: [row('b')] } as never, []);
    expect(rows.map(r => r.param.name)).toEqual(['a', 'b']);
    expect(rows.map(r => r.via)).toEqual(['param', 'page']);
  });

  it('已被画布控件驱动的键不再重复渲染（两套都算）', () => {
    const rows = uncontrolledParams({ params: [row('a')], page_params: [row('b')] } as never,
      [driven('a'), { id: 2, name: 'c2', chart_type: 'widget_select', config: { bind_param: 'b' } } as never]);
    expect(rows).toEqual([]);
  });

  it('bind_param 逗号多端点逐个算，驱动了的不再出输入口', () => {
    const rows = uncontrolledParams({ params: [row('date_start'), row('date_end')] } as never,
      [{ id: 1, name: 'c', chart_type: 'widget_daterange', config: { bind_param: 'date_start,date_end' } } as never]);
    expect(rows).toEqual([]);
  });

  it('空看板返回空数组（渲染侧据此整行不出现）', () => {
    expect(uncontrolledParams({} as never, [])).toEqual([]);
  });

  it('计划/参数行缺字段时不崩（脏数据按最小说明处理）', () => {
    expect(() => buildAssemblyPlan([ds(1, 'A', [{ key: 'x' } as never])], [], [])).not.toThrow();
    expect(uncontrolledParams({ params: [{}] } as never, [])).toHaveLength(1);
  });
});
