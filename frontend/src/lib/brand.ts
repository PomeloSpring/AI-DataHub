/**
 * 品牌对外口径的唯一来源：浏览器标签标题、品牌字标（AD）、浏览器标签图标（favicon）、
 * 以及名称/标语的取值口径（登录页与侧栏都按这两个函数取，不再各写一份文案）。
 *
 * 历史缺陷（三套口径并存，浏览器标签与页内品牌标"不是一套画风"）：
 *  1. index.html 写「AI-DataHub - 智能数据分析平台」，运行时 brandStore 又改成「… - 数据分析平台」，
 *     首屏与加载后标题不一致（品牌接口失败时永远停在旧文案）；
 *  2. favicon 兜底用脚手架遗留的 /vite.svg，颜色写死 #3B82F6，而页内品牌方块用主题主色 bg-primary
 *     （light 主题 ≈ #0036CC），两者肉眼不同色；切换主题时页内标跟着变、标签图标不变；
 *  3. 品牌方块「AD」在 5 个布局 + 首页/文章页各写一遍，品牌设置页预览还写着「CB」；
 *  4. 登录页把应用名与标语「企业级智能数据分析平台」写死在页面里，品牌设置改名/换图标/改标语
 *     登录页都不跟着变（同一件品牌事实的第二套真值源）。
 *
 * 现在：文案、字标派生、方块几何只在本文件定义一处；页内渲染走 components/BrandLogo.tsx 与
 * components/BrandLockup.tsx（同一组几何常量），标签图标由当前主题的 --primary / --primary-foreground
 * 实时生成 SVG，与页内方块同源同色同形。
 */

export const DEFAULT_APP_NAME = 'AI-DataHub';

/** 标签标题后缀：index.html 与运行时共用这一处，禁止再写第二份文案 */
export const APP_TITLE_SUFFIX = '数据分析平台';

/**
 * 品牌标语兜底文案（登录页名称下方那行）：与后端 adh_brand_settings.tagline 的列默认值同值。
 * 真实取值以品牌设置为准；这里只是品牌接口尚未返回时的首屏兜底。
 * 注意与 app_name 的差别：标语被显式清空（''）是合法配置 = 不显示，不像名称那样回落默认名。
 */
export const DEFAULT_TAGLINE = '企业级智能数据分析平台';

/** 方块几何（与 public/favicon.svg 同源）：32 画布、圆角 8、字号 14 */
export const MARK_VIEWBOX = 32;
const MARK_RADIUS_RATIO = 8 / MARK_VIEWBOX;
const MARK_FONT_RATIO = 14 / MARK_VIEWBOX;

/** 静态兜底（JS 未跑起来 / 主题令牌读不到时）：light 主题主色，与 globals.css `.light --primary` 同值 */
const FALLBACK_MARK_BG = 'hsl(224, 100%, 40%)';
const FALLBACK_MARK_FG = '#ffffff';

export interface BrandLike {
  app_name?: string;
  logo_url?: string;
}

export const DEFAULT_BRAND: Required<BrandLike> & {
  show_icon: boolean;
  show_text: boolean;
  tagline: string;
} = {
  app_name: DEFAULT_APP_NAME,
  logo_url: '',
  show_icon: true,
  show_text: true,
  tagline: DEFAULT_TAGLINE,
};

/** 页内显示用的应用名：留空回落默认名，与标签标题同一口径（只这一处判空） */
export function brandNameOf(appName?: string): string {
  return (appName || '').trim() || DEFAULT_APP_NAME;
}

/**
 * 页内显示用的品牌标语：undefined（品牌设置还没拉到）给默认文案，''（管理员显式清空）就是不显示。
 * 二者必须区分——把清空当未加载会把管理员删掉的标语又顶回登录页。
 */
export function taglineOf(tagline?: string): string {
  return tagline === undefined ? DEFAULT_TAGLINE : tagline.trim();
}

/** 标签标题：{应用名} - 数据分析平台（应用名为空回落默认名，不留空标题） */
export function buildAppTitle(appName?: string): string {
  return `${brandNameOf(appName)} - ${APP_TITLE_SUFFIX}`;
}

/**
 * 品牌字标：从应用名派生，最多两个字母（AI-DataHub → AD），改名后页内标与标签图标一起跟着变。
 * 多个英文词取前两词首字母；单驼峰词取内部大写字母（DataHub → DH、ChatBI → CB）；
 * 无英文字母的纯中文名取首字（数智平台 → 数）。
 */
export function brandAbbrev(appName?: string): string {
  const raw = (appName || DEFAULT_APP_NAME).trim();
  const words = raw.split(/[^A-Za-z0-9]+/).filter(Boolean);
  if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
  if (words.length === 1) {
    const word = words[0];
    const caps = word.replace(/[^A-Z]/g, '');
    return (caps.length >= 2 ? caps.slice(0, 2) : word.slice(0, 2)).toUpperCase();
  }
  const chars = Array.from(raw.replace(/\s+/g, ''));
  return chars.length ? chars[0] : 'AD';
}

/** 方块圆角（px）：与 favicon 同一比例 */
export function markRadius(size: number): number {
  return round2(size * MARK_RADIUS_RATIO);
}

/** 方块字号（px）：与 favicon 同一比例 */
export function markFontSize(size: number): number {
  return round2(size * MARK_FONT_RATIO);
}

function round2(v: number): number {
  return Math.round(v * 100) / 100;
}

/** Tailwind 主题令牌是 HSL 三元组（"224 100% 40%"），SVG 里要用可解析的函数式写法 */
function tokenToCssColor(raw: string, fallback: string): string {
  const v = raw.trim();
  if (!v) return fallback;
  if (/[#,(]/.test(v)) return v;
  const parts = v.split(/\s+/);
  if (parts.length < 3) return fallback;
  const [h, s, l] = parts;
  return `hsl(${h}, ${s.endsWith('%') ? s : `${s}%`}, ${l.endsWith('%') ? l : `${l}%`})`;
}

/** 读当前主题的品牌色对（页内方块用的就是这两个令牌，标签图标必须同源） */
export function readMarkColors(): { bg: string; fg: string } {
  if (typeof document === 'undefined') return { bg: FALLBACK_MARK_BG, fg: FALLBACK_MARK_FG };
  const cs = getComputedStyle(document.documentElement);
  return {
    bg: tokenToCssColor(cs.getPropertyValue('--primary'), FALLBACK_MARK_BG),
    fg: tokenToCssColor(cs.getPropertyValue('--primary-foreground'), FALLBACK_MARK_FG),
  };
}

function escapeXml(s: string): string {
  return s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] as string));
}

/** 生成与页内品牌方块同形的 SVG 图标（bg/fg 缺省时按当前主题读取） */
export function brandMarkSvg(text: string, colors?: { bg: string; fg: string }): string {
  const { bg, fg } = colors ?? readMarkColors();
  const size = MARK_VIEWBOX;
  return [
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" fill="none">`,
    `<rect width="${size}" height="${size}" rx="${round2(size * MARK_RADIUS_RATIO)}" fill="${bg}"/>`,
    `<text x="${size / 2}" y="${round2(size * 0.6875)}" text-anchor="middle"`,
    ` font-family="system-ui, -apple-system, sans-serif" font-size="${round2(size * MARK_FONT_RATIO)}"`,
    ` font-weight="700" fill="${fg}">${escapeXml(text)}</text>`,
    '</svg>',
  ].join('');
}

export function brandMarkDataUrl(text: string, colors?: { bg: string; fg: string }): string {
  return `data:image/svg+xml,${encodeURIComponent(brandMarkSvg(text, colors))}`;
}

function ensureFaviconLink(): HTMLLinkElement {
  let link = document.querySelector("link[rel~='icon']") as HTMLLinkElement | null;
  if (!link) {
    link = document.createElement('link');
    link.rel = 'icon';
    document.head.appendChild(link);
  }
  return link;
}

function isSvgIcon(href: string): boolean {
  return href.startsWith('data:image/svg+xml') || /\.svg(\?|#|$)/i.test(href);
}

// 当前品牌快照：主题切换时要按新主题色重算标签图标，而 themeStore 不该反向依赖 brandStore
let currentBrand: BrandLike = { ...DEFAULT_BRAND };

/**
 * 品牌快照的本地缓存键：刷新时浏览器标签标题的"闪一下"根因是——index.html 的静态标题先渲染，
 * 配置名要等 /admin/brand 异步返回后才 applyDocumentBrand 覆盖。把上次拉到的品牌同步存一份，
 * 供启动时恢复（favicon 靠它按主题重算）。
 */
export const BRAND_STORAGE_KEY = 'chatbi-brand';

/**
 * 标题的本地缓存键（存"已算好的完整标题字符串"，不是 app_name）：
 * 光在 main.tsx 顶层 restoreBrandSnapshot 仍会闪——那段代码要等整个 bundle 的模块图
 * 加载解析完才执行（dev 下数百 ms～秒级），期间浏览器一直显示 index.html 写死的默认标题。
 * 唯一能早于模块加载落地标题的地方是 index.html 的内联脚本（HTML 一解析就同步跑），
 * 它直接读这个键把 document.title 顶成上次配置的值，无需在 index.html 里重复拼后缀文案。
 */
export const BRAND_TITLE_STORAGE_KEY = 'chatbi-brand-title';

function persistBrandSnapshot(): void {
  if (typeof localStorage === 'undefined') return;
  try {
    localStorage.setItem(BRAND_STORAGE_KEY, JSON.stringify(currentBrand));
  } catch {
    // 隐私模式/配额写不进不影响功能：下次刷新退回异步校正即可
  }
}

/**
 * 应用启动时（渲染前）按本地缓存同步恢复品牌：让浏览器标签标题与图标在首屏就是上次配置的值，
 * 消除"先闪默认名再切配置名"。缓存缺失/损坏时保持默认快照不动（后续 fetchBrand 仍会校正）。
 */
export function restoreBrandSnapshot(): void {
  if (typeof localStorage === 'undefined') return;
  const raw = localStorage.getItem(BRAND_STORAGE_KEY);
  if (!raw) return;
  try {
    const parsed = JSON.parse(raw) as BrandLike;
    if (parsed && typeof parsed === 'object') {
      currentBrand = { ...currentBrand, ...parsed };
      applyDocumentBrand();
    }
  } catch {
    // 缓存损坏按无缓存处理，回落默认品牌
  }
}

/** brandStore 拉到/保存品牌设置后调用：写入快照并刷新标签标题与图标 */
export function setBrandSnapshot(brand: BrandLike): void {
  currentBrand = { ...currentBrand, ...brand };
  persistBrandSnapshot();
  applyDocumentBrand();
}

/**
 * 把品牌口径写进文档：标题 + 标签图标。
 * 图标优先级：品牌设置上传的 logo > 按当前主题主色生成的方块（与页内标同源）。
 * show_icon=false 只关掉页内方块，标签图标照常显示——浏览器标签没有图标会让用户在多标签里丢失定位。
 */
export function applyDocumentBrand(): void {
  if (typeof document === 'undefined') return;
  document.title = buildAppTitle(currentBrand.app_name);
  // 标题字符串同步落缓存，供 index.html 内联脚本在模块加载前顶回配置名（消除刷新闪默认名）
  if (typeof localStorage !== 'undefined') {
    try {
      localStorage.setItem(BRAND_TITLE_STORAGE_KEY, document.title);
    } catch {
      // 写不进不影响功能，下次刷新退回异步校正
    }
  }
  const link = ensureFaviconLink();
  const logo = (currentBrand.logo_url || '').trim();
  if (logo) {
    link.href = logo;
    if (isSvgIcon(logo)) link.type = 'image/svg+xml';
    else link.removeAttribute('type');
    return;
  }
  link.href = brandMarkDataUrl(brandAbbrev(currentBrand.app_name));
  link.type = 'image/svg+xml';
}
