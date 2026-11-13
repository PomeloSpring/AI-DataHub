/**
 * 浏览器标签（标题 + 图标）与页内品牌标同源 —— 回归锁定
 *
 * 历史缺陷（同一件品牌事实在四处各写一份）：
 *  - index.html 标题「智能数据分析平台」，运行时 brandStore 改「数据分析平台」→ 首屏与加载后不一致；
 *  - favicon 兜底 /vite.svg 写死 #3B82F6，页内方块用主题主色 bg-primary → 标签与侧栏不同色、换主题标签不变；
 *  - 品牌方块「AD」在 5 个布局 + 首页/文章页各写一遍，品牌设置页预览还写着「CB」。
 * 本文件把口径钉死：文案与几何只在 lib/brand.ts 一处，标签图标按当前主题主色实时生成。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import {
  APP_TITLE_SUFFIX, BRAND_STORAGE_KEY, BRAND_TITLE_STORAGE_KEY, DEFAULT_APP_NAME, DEFAULT_TAGLINE, applyDocumentBrand, brandAbbrev,
  brandMarkDataUrl, brandMarkSvg, brandNameOf, buildAppTitle, markFontSize, markRadius,
  restoreBrandSnapshot, setBrandSnapshot, taglineOf,
} from '../brand';

const ROOT = path.resolve(__dirname, '..', '..', '..');
const REPO = path.resolve(ROOT, '..');
const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');

describe('标签标题口径唯一', () => {
  it('index.html 静态标题与运行时同文案（防首屏与加载后两个标题）', () => {
    expect(html).toContain(`<title>${buildAppTitle()}</title>`);
  });
  it('index.html 内联消闪脚本与 lib/brand.ts 同一缓存键（键不一致=改了不生效）', () => {
    expect(html).toContain(`localStorage.getItem('${BRAND_TITLE_STORAGE_KEY}')`);
  });
  it('标题格式固定为「{应用名} - 数据分析平台」', () => {
    expect(buildAppTitle('某平台')).toBe(`某平台 - ${APP_TITLE_SUFFIX}`);
  });
  it('应用名为空/全空格回落默认名，不留空标题', () => {
    expect(buildAppTitle('')).toBe(`${DEFAULT_APP_NAME} - ${APP_TITLE_SUFFIX}`);
    expect(buildAppTitle('   ')).toBe(`${DEFAULT_APP_NAME} - ${APP_TITLE_SUFFIX}`);
  });
});

describe('品牌字标由应用名派生（改名后页内标与标签图标一起跟着变）', () => {
  it.each([
    ['AI-DataHub', 'AD'],
    ['ai datahub', 'AD'],
    ['ChatBI 分析平台', 'CB'],
    ['DataHub', 'DH'],
    ['数智平台', '数'],
    ['', 'AD'],
  ])('%s → %s', (name, expected) => expect(brandAbbrev(name)).toBe(expected));
});

describe('方块几何：页内标与 favicon 同一组数字', () => {
  it('32 画布还原为 favicon 基准（rx 8 / 字号 14）', () => {
    expect(markRadius(32)).toBe(8);
    expect(markFontSize(32)).toBe(14);
  });
  it('页内尺寸按同比例收敛（24px → 圆角 6 / 字号 10.5）', () => {
    expect(markRadius(24)).toBe(6);
    expect(markFontSize(24)).toBe(10.5);
  });
  it('SVG 方块消费传入的配色与字标', () => {
    const svg = brandMarkSvg('AD', { bg: 'hsl(224, 100%, 40%)', fg: '#ffffff' });
    expect(svg).toContain('rx="8"');
    expect(svg).toContain('font-size="14"');
    expect(svg).toContain('fill="hsl(224, 100%, 40%)"');
    expect(svg).toContain('>AD</text>');
  });
  it('字标里的 XML 特殊字符被转义（应用名可含 & 等）', () => {
    expect(brandMarkSvg('A&B', { bg: '#000', fg: '#fff' })).toContain('>A&amp;B</text>');
  });
});

describe('标签图标跟随主题主色（与页内 bg-primary 同源）', () => {
  const stubTokens = (primary: string, foreground: string) =>
    vi.spyOn(window, 'getComputedStyle').mockReturnValue({
      getPropertyValue: (k: string) => (k === '--primary' ? primary : k === '--primary-foreground' ? foreground : ''),
    } as unknown as CSSStyleDeclaration);

  afterEach(() => vi.restoreAllMocks());

  it('读当前主题的 --primary / --primary-foreground 生成 SVG', () => {
    stubTokens(' 190 100% 50% ', '220 30% 5%');
    const svg = decodeURIComponent(brandMarkDataUrl('AD').replace('data:image/svg+xml,', ''));
    expect(svg).toContain('fill="hsl(190, 100%, 50%)"');
    expect(svg).toContain('fill="hsl(220, 30%, 5%)"');
  });
  it('令牌读不到时用 light 主题主色兜底（与 globals.css .light --primary 同值）', () => {
    stubTokens('', '');
    const svg = decodeURIComponent(brandMarkDataUrl('AD').replace('data:image/svg+xml,', ''));
    expect(svg).toContain('fill="hsl(224, 100%, 40%)"');
  });
  it('已是函数式/十六进制颜色不再二次包装', () => {
    stubTokens('#0037CB', 'rgb(255,255,255)');
    const svg = decodeURIComponent(brandMarkDataUrl('AD').replace('data:image/svg+xml,', ''));
    expect(svg).toContain('fill="#0037CB"');
    expect(svg).toContain('fill="rgb(255,255,255)"');
  });
});

describe('页内名称/标语取值口径（登录页与侧栏共用）', () => {
  it('应用名留空回落默认名，与标签标题同一判空口径', () => {
    expect(brandNameOf('')).toBe(DEFAULT_APP_NAME);
    expect(brandNameOf('   ')).toBe(DEFAULT_APP_NAME);
    expect(brandNameOf('数智中枢')).toBe('数智中枢');
  });
  it('标语：undefined（还没拉到品牌）给默认文案，空串（显式清空）就是不显示', () => {
    expect(taglineOf(undefined)).toBe(DEFAULT_TAGLINE);
    expect(taglineOf('')).toBe('');
    expect(taglineOf('   ')).toBe('');
    expect(taglineOf('让提问有可信答案')).toBe('让提问有可信答案');
  });
  it('默认标语与后端列默认值同值（两边不一致就会出现“改了没生效”）', () => {
    const sql = fs.readFileSync(path.join(REPO, 'docker', 'mysql', 'brand_tagline_migration.sql'), 'utf8');
    expect(sql).toContain(`DEFAULT ''${DEFAULT_TAGLINE}''`);
  });
});

describe('applyDocumentBrand：标题与图标的唯一落地路径', () => {
  const link = () => document.querySelector("link[rel~='icon']") as HTMLLinkElement;

  beforeEach(() => {
    document.head.innerHTML = '<link rel="icon" type="image/svg+xml" href="/favicon.svg" />';
    document.title = '';
    // 品牌快照是模块级状态（主题切换时要复用），逐例重置，避免上一例的 logo 泄到下一例
    setBrandSnapshot({ app_name: DEFAULT_APP_NAME, logo_url: '' });
    vi.spyOn(window, 'getComputedStyle').mockReturnValue({
      getPropertyValue: () => '',
    } as unknown as CSSStyleDeclaration);
  });
  afterEach(() => vi.restoreAllMocks());

  it('未上传 logo：标题按品牌口径，图标为按主题生成的 SVG data URL', () => {
    setBrandSnapshot({ app_name: DEFAULT_APP_NAME, logo_url: '' });
    expect(document.title).toBe(buildAppTitle());
    expect(link().getAttribute('href')).toMatch(/^data:image\/svg\+xml,/);
    expect(link().type).toBe('image/svg+xml');
  });
  it('改名后标题与图标字标一起变（不留旧字标）', () => {
    setBrandSnapshot({ app_name: '数智平台', logo_url: '' });
    expect(document.title).toBe('数智平台 - 数据分析平台');
    expect(decodeURIComponent(link().getAttribute('href')!)).toContain('>数</text>');
  });
  it('品牌设置上传的 logo 优先，且非 SVG 资源不挂 svg type', () => {
    setBrandSnapshot({ app_name: DEFAULT_APP_NAME, logo_url: 'data:image/png;base64,AAAA' });
    expect(link().getAttribute('href')).toBe('data:image/png;base64,AAAA');
    expect(link().hasAttribute('type')).toBe(false);
  });
  it('文档里没有 icon link 时自动补一个', () => {
    document.head.innerHTML = '';
    applyDocumentBrand();
    expect(link()).toBeTruthy();
    expect(link().getAttribute('href')).toMatch(/^data:image\/svg\+xml,/);
  });
});

describe('品牌快照本地缓存：刷新时标题不再先闪默认名', () => {
  const link = () => document.querySelector("link[rel~='icon']") as HTMLLinkElement;

  beforeEach(() => {
    document.head.innerHTML = '<link rel="icon" type="image/svg+xml" href="/favicon.svg" />';
    document.title = '';
    localStorage.clear();
    vi.spyOn(window, 'getComputedStyle').mockReturnValue({
      getPropertyValue: () => '',
    } as unknown as CSSStyleDeclaration);
  });
  afterEach(() => vi.restoreAllMocks());

  it('setBrandSnapshot 把品牌写入 localStorage（回访首屏可同步恢复）', () => {
    setBrandSnapshot({ app_name: '数智平台', logo_url: '' });
    expect(JSON.parse(localStorage.getItem(BRAND_STORAGE_KEY) || '{}').app_name).toBe('数智平台');
  });
  it('applyDocumentBrand 把完整标题同步落缓存（供 index.html 内联脚本模块加载前顶回）', () => {
    setBrandSnapshot({ app_name: '数智平台', logo_url: '' });
    expect(localStorage.getItem(BRAND_TITLE_STORAGE_KEY)).toBe('数智平台 - 数据分析平台');
  });
  it('restoreBrandSnapshot 在异步拉取前就把缓存名顶到标题（消除跳字）', () => {
    localStorage.setItem(BRAND_STORAGE_KEY, JSON.stringify({ app_name: '数智平台', logo_url: '' }));
    document.title = buildAppTitle(); // 模拟 index.html 默认标题先渲染
    restoreBrandSnapshot();
    expect(document.title).toBe('数智平台 - 数据分析平台');
    expect(decodeURIComponent(link().getAttribute('href')!)).toContain('>数</text>');
  });
  it('无缓存时 restore 不动默认快照（下次 fetchBrand 仍会校正）', () => {
    setBrandSnapshot({ app_name: DEFAULT_APP_NAME, logo_url: '' });
    localStorage.clear();
    document.title = buildAppTitle(); // 模拟 index.html 默认标题先渲染
    restoreBrandSnapshot();
    expect(document.title).toBe(buildAppTitle());
  });
  it('缓存损坏时 restore 不抛错、保留默认标题', () => {
    localStorage.setItem(BRAND_STORAGE_KEY, '{坏掉的 JSON');
    document.title = buildAppTitle();
    expect(() => restoreBrandSnapshot()).not.toThrow();
    expect(document.title).toBe(buildAppTitle());
  });
});

describe('静态护栏：品牌口径不许再写第二份', () => {
  const SRC = path.resolve(__dirname, '..', '..');

  function walk(dir: string, out: string[] = []): string[] {
    for (const name of fs.readdirSync(dir)) {
      const p = path.join(dir, name);
      if (fs.statSync(p).isDirectory()) {
        if (name === '__tests__' || name === 'node_modules') continue;
        walk(p, out);
      } else if (/\.tsx?$/.test(name)) {
        out.push(p);
      }
    }
    return out;
  }

  it('标题后缀只在 lib/brand.ts 定义一处（页面文案不得再拼一份「 - 数据分析平台」）', () => {
    expect(APP_TITLE_SUFFIX).toBe('数据分析平台');
    const TITLE_RE = /[-—]\s*数据分析平台/;
    const offenders = walk(SRC).filter((f) => !f.endsWith('lib/brand.ts') && TITLE_RE.test(fs.readFileSync(f, 'utf8')));
    expect(offenders.map((f) => path.relative(SRC, f))).toEqual([]);
  });
  it('品牌方块不许再内联硬编码字标（统一走 components/BrandLogo）', () => {
    const offenders = walk(SRC).filter((f) => {
      if (f.endsWith('lib/brand.ts') || f.endsWith('components/BrandLogo.tsx')) return false;
      return />(AD|CB)</.test(fs.readFileSync(f, 'utf8'));
    });
    expect(offenders.map((f) => path.relative(SRC, f))).toEqual([]);
  });
  it('品牌标语字面量只在 lib/brand.ts 一处（登录页不得自带第二套文案）', () => {
    const offenders = walk(SRC).filter((f) => !f.endsWith('lib/brand.ts') && fs.readFileSync(f, 'utf8').includes(DEFAULT_TAGLINE));
    expect(offenders.map((f) => path.relative(SRC, f))).toEqual([]);
  });
  it('默认应用名不许被当成写死文案渲染（只能取品牌快照或 DEFAULT_APP_NAME）', () => {
    const offenders = walk(SRC).filter((f) => {
      if (f.endsWith('lib/brand.ts')) return false;
      return new RegExp(`>${DEFAULT_APP_NAME}<`).test(fs.readFileSync(f, 'utf8'));
    });
    expect(offenders.map((f) => path.relative(SRC, f))).toEqual([]);
  });
  it('脚手架遗留的 /vite.svg 不得再被引用（图标已换成品牌 favicon + 主题动态生成）', () => {
    expect(html).not.toContain('vite.svg');
    expect(fs.existsSync(path.join(ROOT, 'public', 'vite.svg'))).toBe(false);
    // lib/brand.ts 仅在历史缺陷说明里提到旧文件名，不算引用
    const refs = walk(SRC).filter((f) => !f.endsWith('lib/brand.ts') && fs.readFileSync(f, 'utf8').includes('vite.svg'));
    expect(refs.map((f) => path.relative(SRC, f))).toEqual([]);
  });
  it('静态兜底图标与运行时生成的几何同源（32 画布 / rx 8 / 字号 14）', () => {
    const svg = fs.readFileSync(path.join(ROOT, 'public', 'favicon.svg'), 'utf8');
    expect(svg).toContain('viewBox="0 0 32 32"');
    expect(svg).toContain('rx="8"');
    expect(svg).toContain('font-size="14"');
    expect(svg).toContain(`>${brandAbbrev(DEFAULT_APP_NAME)}</text>`);
  });
});
