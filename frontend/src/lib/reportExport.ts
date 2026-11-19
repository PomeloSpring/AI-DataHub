// 报告导出：把屏幕上看到的那份正文落成文件，或送进浏览器打印（另存为 PDF）。
//
// 为什么是纯前端：正文本来就已经被取进浏览器（详情接口回签名直链，见 api/report.ts
// 的 fetchReportContent），"导出我刚看过的内容"不产生任何新的数据权限判断，
// 再绕回服务端只会多出第二条正文寻址口径（与"两种存储后端只有一个 content_url"
// 的收敛方向相反）。
//
// 四条口径：
// 1. **导出格式必须与正文格式相称** —— HTML 报告不给 .md 入口（把 HTML 塞进 .md
//    是语义错），Markdown 报告两种都能出。
// 2. **HTML/PDF 导出所见即所得** —— 从渲染后的容器取，图表（canvas）转成图片快照，
//    否则导出的文件里图表是一片空白（canvas 的像素不在 innerHTML 里）。
// 3. **样式必须跟着文件走** —— 单靠净化时剥掉 class/style、再套一份最小样式表，
//    导出文件就只剩语义结构：屏幕上主题包的配色/卡片/间距全没了。本站主题是根元素上的
//    类 + globals.css 里的 CSS 变量，所以导出时把**同源编译后的 CSS 原样内联**、
//    保留 class/style、并把当前根类镜像到导出文档的 `<html>` 上；
//    这样 `dark:` 变体与 prose 的伪元素也能复现（逐元素抄 computed style 做不到这两点）；
//    版心宽度走 `lib/pageWidth.ts` 的同一份口径（html 版式填满、markdown 长文窄栏），
//    导出侧不自带第二个 max-width —— 富版式被压回中间一列正是那份模块注释点名的事故。
// 4. **降级事实报告的免责标注要跟着文件走** —— 正文里本来就写着（render_fact_report
//    把它写进正文首行），所以原样导出即带上，不需要在这里再拼一遍。
import DOMPurify from 'dompurify';
import html2canvas from 'html2canvas';
import i18n from '@/i18n';
import { ARTICLE_ALLOWED_ATTR, ARTICLE_ALLOWED_TAGS } from '@/components/HtmlArticle';

/**
 * 导出面的净化白名单 = **渲染面白名单的超集** + 图表快照所需的 img/尺寸属性。
 *
 * "超集"是硬要求：导出取的是渲染后的 DOM，渲染面允许的结构标签若在导出侧被剥，
 * DOMPurify 会删标签留子节点 —— 文字还在、结构塌了，看起来"导出了，但不是那一回事"。
 * 历史缺陷：清单里没有 `section`/`header`，HTML 版式正文的章节头
 * （`<header style="display:flex…">` 底栏 + `<section style="border…">` 卡片）整块散架，
 * 序号徽章与标题被拆成两段。所以清单从渲染口（HtmlArticle）派生，不在这里抄一份 —— 抄两份必然漂移。
 *
 * 反过来也不能直接用 `sanitizeArticleHtml` 洗导出结果：它禁 `src`（正文不该外链图片）且不含 `img`，
 * 而 canvas/地图拍出来的快照就是 `<img src="data:image/png…">`，复用会把图表整张洗掉。
 * `del` 同理：markdown `~~划掉~~` 出 `<del>`，站内渲染认它，导出侧不能少。
 * 拦的仍是**可执行的东西**：脚本、事件属性（onclick/onerror）、javascript: 伪协议。
 */
export const EXPORT_ALLOWED_TAGS = [...ARTICLE_ALLOWED_TAGS, 'img', 'del'];
// class/style 由渲染面白名单带进来：这份正文的版式写在内联 style 上，剥掉就只剩裸结构。
// 快照图只定宽、高交给 CSS 的 height:auto 按位图真实比例算：
// 同时写死 width+height 会在"布局高度 ≠ 位图高度"时把图拉变形。
export const EXPORT_ALLOWED_ATTR = [...ARTICLE_ALLOWED_ATTR, 'src', 'alt', 'width', 'height'];

/**
 * style 值里的可执行写法。DOMPurify 放行 style 属性，但**不拦** `url(javascript:…)`
 * （实测 3.4.15 原样保留 —— 它赌现代浏览器不执行 CSS 里的 javascript:），
 * 而导出文件会被别人用浏览器直接打开，现行口径是"文件里不得出现 javascript:"。
 * 命中就整条丢掉该元素的 style：宁可少一点排版，也不猜哪一句声明是安全的。
 */
const UNSAFE_STYLE = /javascript:|vbscript:|expression\s*\(|@import|behavior\s*:/i;

// 独立实例：钩子只作用于导出净化，不改动站内其它 DOMPurify 调用方（如 ReportContent）的口径
const exportPurifier = DOMPurify();
exportPurifier.addHook('afterSanitizeAttributes', (node) => {
  const el = node as Element;
  if (typeof el.getAttribute !== 'function' || typeof el.removeAttribute !== 'function') return;
  const style = el.getAttribute('style');
  if (style && UNSAFE_STYLE.test(style)) el.removeAttribute('style');
});

function sanitizeForExport(html: string): string {
  return exportPurifier.sanitize(html, {
    ALLOWED_TAGS: EXPORT_ALLOWED_TAGS,
    ALLOWED_ATTR: EXPORT_ALLOWED_ATTR,
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
  });
}

export type ReportExportFormat = 'md' | 'html' | 'pdf';

const EXT: Record<ReportExportFormat, string> = { md: 'md', html: 'html', pdf: 'pdf' };

/** 打印隔离用的类名：globals.css 的 @media print 规则靠它只露出报告正文 */
export const PRINT_CLASS = 'printing-report';

/** 文件系统不允许的字符换成下划线；标题是展示用 name，不拼裸 id */
function safeName(text: string): string {
  return String(text || '')
    .replace(/[\\/:*?"<>|\u0000-\u001f]/g, '_')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 80);
}

function dateTag(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toISOString().slice(0, 10);
}

export function reportFileName(title: string, createdAt: string, format: ReportExportFormat): string {
  const base = safeName(title) || i18n.t('report:export.fileFallback');
  const day = dateTag(createdAt);
  return `${base}${day ? `-${day}` : ''}.${EXT[format]}`;
}

export function exportLabel(format: ReportExportFormat): string {
  return i18n.t(`report:export.format.${format}` as 'report:export.format.md');
}

/**
 * 是不是整份自包含 HTML 文档（AI 生成的报告产物就长这样）。
 *
 * 报告渲染面对它必须走隔离 iframe（样式与内联图表要保住，也不能把 <style> 泄到主
 * 文档），而导出/打印得跟着换策略：DOM 快照取不到 iframe 内容，只能直接给原始文档。
 */
export function isStandaloneHtmlDocument(content?: string): boolean {
  const head = String(content || '').slice(0, 2000).toLowerCase();
  return head.includes('<!doctype html') || /<html[\s>]/.test(head);
}

/** 这份报告能导出成什么：HTML 正文不出 .md（格式不相称） */
export function availableExportFormats(bodyFormat?: string, standaloneHtmlDoc = false): ReportExportFormat[] {
  if (bodyFormat !== 'html') return ['md', 'html', 'pdf'];
  // 整份 HTML 文档在隔离 iframe 里渲染：浏览器打印只能拿到可视窗口（长报告会截页），
  // 所以只给「导出 HTML 文件」（就是原始自包含文档，最保真）并显式不给 PDF
  return standaloneHtmlDoc ? ['html'] : ['html', 'pdf'];
}

/** 视觉区域快照失败时的可见说明：不能留白，也不能把空白图当地图发出去。 */
function snapshotPlaceholder(label: string, why: string): HTMLElement {
  const note = document.createElement('p');
  note.textContent = i18n.t('report:export.snapshotNote', { label, why });
  return note;
}

/**
 * 判断一张快照是不是"其实是空的"。
 *
 * html2canvas 在 useCORS 模式下遇到不允许跨域的瓦片不会抛错 —— 它直接不画，
 * 于是产出一张米色底图，看上去像导出了、其实地图没了。那种结果比报错更坏，
 * 所以按网格采样像素，全同色就判失败。
 */
function isBlankCanvas(canvas: HTMLCanvasElement): boolean {
  let ctx: CanvasRenderingContext2D | null = null;
  try {
    ctx = canvas.getContext('2d');
    if (!ctx) return false;
    const { width, height } = canvas;
    if (!width || !height) return true;
    const first = ctx.getImageData(0, 0, 1, 1).data.join(',');
    for (const fx of [0.1, 0.3, 0.5, 0.7, 0.9]) {
      for (const fy of [0.1, 0.3, 0.5, 0.7, 0.9]) {
        const px = ctx.getImageData(Math.floor((width - 1) * fx), Math.floor((height - 1) * fy), 1, 1).data;
        if (px.join(',') !== first) return false;
      }
    }
    return true;
  } catch {
    return true;   // 画布被污染读不出像素 = 无法证明它有内容，按失败处理
  }
}

/**
 * 把元素拍成一张位图；失败回 null，由调用方换成可见占位说明。
 *
 * 取景参数只给 backgroundColor/scale/useCORS，**不要传 width/height/windowWidth/windowHeight**。
 * 依据 html2canvas 1.4.1 的取帧口径（dist/html2canvas.js 主流程）：
 *   - windowWidth/windowHeight 决定克隆 iframe 的视口，元素在**克隆里**重新测量出
 *     left/top/width/height，裁剪框 = (x+left, y+top, opts.width ?? width, opts.height ?? height)；
 *   - 把视口压成元素尺寸会让整份文档重排（克隆里的地图 left/top 变了），
 *     而我传的 width/height 又来自真实页面的 rect —— 两套坐标系拼在一起，
 *     裁剪原点就落到地图的别处（历史缺陷：导出的"中国地图"里是中东和印度，
 *     热力点整个在画面外）。
 * 全交给默认值，量测与裁剪才同处一个坐标系。
 */
async function rasterize(el: HTMLElement): Promise<string | null> {
  // 图层归属：html2canvas 只负责底图，元素内的 <canvas>（Leaflet 上的热力层、
  // 图表库画布）由我们自己合成到结果上。
  // 依据：html2canvas 的 createCanvasClone 走 putImageData 复制像素，实测这条路径
  // 在本页的热力层上没有生效（导出的地图里点全没了）。依赖它不如自己 drawImage ——
  // 位置/尺寸/时机都在同一份代码里，出问题能直接推出来。
  const overlays = Array.from(el.querySelectorAll('canvas'));
  const controls = Array.from(el.querySelectorAll('.leaflet-control-container')) as HTMLElement[];
  controls.forEach(n => { n.style.visibility = 'hidden'; });
  let base: HTMLCanvasElement;
  try {
    base = await html2canvas(el, {
      backgroundColor: '#ffffff', logging: false, useCORS: true, scale: 2,
      ignoreElements: (node: Element) =>
        node.tagName === 'CANVAS'
        || (node.classList?.contains('leaflet-control-container') ?? false),
    });
  } catch {
    return null;   // 跨域瓦片把画布弄脏后 toDataURL 会抛 SecurityError
  } finally {
    // 控件只是"拍摄瞬间"不可见，拍完立刻还原（页面上还要能缩放）
    controls.forEach(n => { n.style.visibility = ''; });
  }

  // 先判底图是否为空，再叠自己的图层：反过来会把"底图没拍到 + 只剩热力点"
  // 判成有效，产出一坨没有地图的彩色斑点（第一轮导出里就有这个症状）
  if (isBlankCanvas(base)) return null;

  const ctx = base.getContext('2d');
  if (ctx && overlays.length) {
    const box = el.getBoundingClientRect();
    if (box.width > 0) {
      const factor = base.width / box.width;   // 实际放大倍率（scale 与 dpr 的合成结果）
      overlays.forEach((c) => {
        const r = (c as HTMLCanvasElement).getBoundingClientRect();
        if (!r.width || !r.height) return;
        // source-over：屏幕上热力层就是按普通 alpha 叠在瓦片之上的，
        // 'lighter' 只发生在它自己内部多次叠加时，跨图层不能再加一次
        ctx.drawImage(c as HTMLCanvasElement,
          (r.left - box.left) * factor, (r.top - box.top) * factor,
          r.width * factor, r.height * factor);
      });
    }
  }

  try {
    return base.toDataURL('image/png');
  } catch {
    return null;
  }
}

/**
 * 取渲染后容器的 HTML —— **所见即所得，但只保留"看得见的信息"**。
 *
 * 三类节点各有处理，缺一个就会出现"导出的文件和屏幕上不是一回事"：
 * 1. `[data-export-strip]`（图表卡的页签条/导出按钮/瓦片选择器）整棵剪掉。
 *    只靠标签白名单不够：DOMPurify 剥掉 <button> 但**保留里面的文字**，
 *    于是导出文件里会留下一行行没有上下文的「图表明细 导出 瓦片 中国地图」。
 * 2. `[data-export-snapshot]`（目前是地图）拍成**一张**位图。
 *    地图不能按 DOM 序列化：它靠 `.leaflet-pane` 里的绝对定位把几十张瓦片叠起来，
 *    序列化出来就是一屏散落的瓦片（历史缺陷：导出文件里是一屏方块 + 一个 ± 缩放钮）。
 * 3. 残余 <canvas>（图表库直接画的）用 toDataURL 取像素。
 * 2/3 都按 data-export-idx / -cidx 找回原节点，不靠"克隆里第 i 个"配对。
 */
export async function renderedHtml(container: HTMLElement): Promise<string> {
  const snapshotSel = '[data-export-snapshot]';
  const stripSel = '[data-export-strip]';
  const sources = Array.from(container.querySelectorAll<HTMLElement>(snapshotSel));
  const canvases = Array.from(container.querySelectorAll<HTMLCanvasElement>('canvas'));
  // 身份跟着节点走，不靠"克隆里第 i 个"配对：剪掉控件条会让快照区/canvas 的序号
  // 整体前移，按序号配对在结构变化后会静默张冠李戴（拍错图比不拍更糟）。
  sources.forEach((el, i) => el.setAttribute('data-export-idx', String(i)));
  canvases.forEach((el, i) => el.setAttribute('data-export-cidx', String(i)));
  const clone = container.cloneNode(true) as HTMLElement;
  clone.querySelectorAll(stripSel).forEach(n => n.remove());

  try {
    for (const ghost of Array.from(clone.querySelectorAll<HTMLElement>(snapshotSel))) {
      const source = sources[Number(ghost.getAttribute('data-export-idx'))];
      // 属性值就是这块区域的业务名（如「地图」），空则回落"图表"
      const label = (ghost.getAttribute('data-export-snapshot') || '').trim() || i18n.t('report:export.chartFallback');
      const dataUrl = source ? await rasterize(source) : null;
      if (!dataUrl) {
        ghost.replaceWith(snapshotPlaceholder(label, source ? i18n.t('report:export.whyCrossOrigin') : i18n.t('report:export.whyNotRendered')));
        continue;
      }
      const rect = source.getBoundingClientRect();
      const img = document.createElement('img');
      img.src = dataUrl;
      img.alt = label;
      // 只定宽、高交给 CSS 的 height:auto 按位图真实比例算：
      // 同时写死 width+height 会在"布局高度 ≠ 位图高度"时把图拉变形。
      // 尺寸仍用属性而不是内联 style：位图比例交给下面 CSS 的 height:auto 算，
      // 写死内联 height 会盖掉那条规则，在"布局高度 ≠ 位图高度"时把图拉变形。
      if (rect.width) img.setAttribute('width', String(Math.round(rect.width)));
      ghost.replaceWith(img);
    }

    for (const ghost of Array.from(clone.querySelectorAll<HTMLElement>('[data-export-cidx]'))) {
      const original = canvases[Number(ghost.getAttribute('data-export-cidx'))];
      let dataUrl = '';
      try {
        dataUrl = original.toDataURL('image/png');
      } catch {
        dataUrl = '';   // 画布被跨域内容污染时读不出像素：留可见说明，不整份导出失败
      }
      ghost.replaceWith(dataUrl
        ? Object.assign(document.createElement('img'), { src: dataUrl, alt: i18n.t('report:export.chartFallback') })
        : snapshotPlaceholder(i18n.t('report:export.chartFallback'), i18n.t('report:export.whyCanvasUnreadable')));
    }

    // 渲染面已经过站内净化，落文件前**再洗一次**：导出的文件会被别人用浏览器直接打开
    return sanitizeForExport(clone.innerHTML);
  } finally {
    // 临时标记不得留在页面上：否则第二次导出会带着上一次的 idx 属性
    sources.forEach(el => el.removeAttribute('data-export-idx'));
    canvases.forEach(el => el.removeAttribute('data-export-cidx'));
  }
}

/**
 * 本站编译后的 CSS 正文（逐张同源样式表拼接规则文本），供导出文件内联，实现样式保真。
 *
 * 跨域样式表（CDN 字体等）读 `cssRules` 会抛 SecurityError，跳过即可 —— 本站样式经
 * vite 打包是**同源**的，主题变量与工具类都在这几张表里，不会漏掉影响观感的规则。
 * 拿不到（无 document / 一张表都读不了）回空串：`standaloneHtml` 只剩自带的基础排版，
 * 正文内容照常导出 —— 这是排版退化，不是失败，所以不抛错。
 */
export function collectAppCss(): string {
  if (typeof document === 'undefined' || !document.styleSheets) return '';
  const rules: string[] = [];
  for (const sheet of Array.from(document.styleSheets)) {
    try {
      const list = sheet.cssRules;
      if (list) for (const rule of Array.from(list)) rules.push(rule.cssText);
    } catch {
      // SecurityError（跨域表）：这张跳过，其余照常
    }
  }
  return rules.join('\n');
}

/**
 * 自包含 HTML 文档。
 *
 * css / rootClass / contentClass / shellClass 四个入参是"所见即所得"的落点，缺任意一个都会退化成裸结构：
 * - `css`：本站编译 CSS（`collectAppCss`）。放在内置基础样式**之后**，让主题规则覆盖兜底档；
 *   只把 `</style` 转义掉 —— 它是 raw text 里唯一能提前结束标签、把后文当 HTML 解析的逃逸口。
 * - `rootClass`：当前 `documentElement.className`（`dark` 或主题包名），CSS 变量按它取值；
 * - `contentClass`：正文容器自身的类（如 `prose`、卡片内边距），`innerHTML` 不含它，需显式包一层；
 * - `shellClass`：版心，取 `lib/pageWidth.ts` 的 `containerForKind(kind)` —— 与屏幕同一份口径
 *   （html 版式=宽版填满、markdown 长文=窄栏）。**不得**在这里另写一个 max-width：
 *   把富版式栅格压回中间一列正是那份模块注释点名的事故。
 *
 * 屏幕与文件两个口径的分工要写死在这里，否则会互相盖掉：
 * - **本站 CSS 在场时**（正常路径）：正文的标题字号/表格/代码全由主题与 prose 决定，
 *   导出侧一份内容级样式都不加，否则 `.report-body td` 这类权重压过 prose 的规则（prose 的
 *   `:where()` 权重为 0）会把屏幕上的表格样式改写掉 —— 又是"文件与屏幕不一致"。
 * - **本站 CSS 缺失时**（拿不到样式表的退化路径）：只剩一份兜底档，宁可排版朴素也不要裸结构。
 * - 文件自己的标题与副标题是**我们造的**元素（屏幕上不存在），preflight 会把裸 `h1` 的
 *   字号字重重置成 inherit，所以它们必须挂在自己的类上（类选择器权重压过元素选择器，与顺序无关）。
 */
export function standaloneHtml(opts: {
  title: string;
  innerHtml: string;
  subtitle?: string;
  css?: string;
  rootClass?: string;
  contentClass?: string;
  shellClass?: string;
}): string {
  const esc = (s: string) => String(s || '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const appCss = String(opts.css || '').replace(/<\/style/gi, '<\\/style');
  const fallback = appCss ? '' : `
  .report-body h2, .report-body h3, .report-body h4 { line-height: 1.35; margin: 1.4em 0 .6em; font-weight: 600; }
  .report-body h2 { font-size: 22px; }
  .report-body h3 { font-size: 18px; }
  .report-body h4 { font-size: 16px; }
  .report-body table { border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 14px; }
  .report-body th, .report-body td { border: 1px solid #cbd5e1; padding: 6px 10px; text-align: left; }
  .report-body th { background: #f1f5f9; }
  .report-body img { max-width: 100%; height: auto; }
  .report-body code, .report-body pre { background: #f1f5f9; border-radius: 4px; }
  .report-body pre { padding: 12px; overflow: auto; }
  .report-body blockquote { margin: 1em 0; padding: 0 1em; color: #475569; border-left: 3px solid #cbd5e1; }`;
  return `<!DOCTYPE html>
<html lang="zh-CN"${opts.rootClass ? ` class="${esc(opts.rootClass)}"` : ''}>
<head>
<meta charset="utf-8">
<title>${esc(opts.title)}</title>
<style>
  /* 版心放 div 而不是 body：内联进来的 Tailwind preflight 带 body{margin:0}，
     写在 body 上的居中会被它盖掉，导出文件变成通栏贴边 */
  body { font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
         color: #0f172a; line-height: 1.65; }
  /* 兜底档（900px 居中）只在内联 CSS 缺失时说了算：shellClass 的 w-[94%]/max-w-[2200px]
     与它同权重且排在后面（本站 CSS 在后），屏幕同款宽版口径照常胜出 */
  .report-shell { max-width: 900px; margin: 32px auto; padding: 0 20px; }
  .report-title { font-size: 30px; font-weight: 600; line-height: 1.3; margin: 0 0 8px; }
  .report-sub { color: #64748b; font-size: 13px; margin: 0 0 24px; }${fallback}
</style>
${appCss ? `<style>${appCss}</style>\n` : ''}</head>
<body>
<div class="report-shell${opts.shellClass ? ` ${esc(opts.shellClass)}` : ''}">
<h1 class="report-title">${esc(opts.title)}</h1>
${opts.subtitle ? `<p class="report-sub">${esc(opts.subtitle)}</p>` : ''}
${opts.contentClass ? `<div class="report-body ${esc(opts.contentClass)}">` : '<div class="report-body">'}
${opts.innerHtml}
</div>
</div>
</body>
</html>`;
}

export function downloadTextFile(filename: string, text: string, mime: string): void {
  const blob = new Blob([text], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/**
 * 送进浏览器打印（用户在对话框里选「另存为 PDF」）。
 *
 * 用打印而不是 html2canvas+jspdf 拼图：打印保留实时 DOM（图表、表格选中态都在），
 * 且中文不分页切字；代价是依赖浏览器驱动 —— 没有 print 能力时必须显式说，
 * 不能静默什么都不发生（那看起来像按钮坏了）。
 */
export function printReport(): Promise<void> {
  if (typeof window.print !== 'function') {
    return Promise.reject(new Error(i18n.t('report:export.printUnsupported')));
  }
  return new Promise((resolve) => {
    const root = document.documentElement;
    const done = () => {
      root.classList.remove(PRINT_CLASS);
      window.removeEventListener('afterprint', done);
      resolve();
    };
    window.addEventListener('afterprint', done);
    root.classList.add(PRINT_CLASS);
    // 等一帧让打印样式生效，否则首屏可能按屏幕样式分页
    requestAnimationFrame(() => {
      window.print();
      // 某些浏览器/取消打印时不回 afterprint：兜底收起类名，避免页面卡在打印态
      window.setTimeout(() => {
        if (root.classList.contains(PRINT_CLASS)) done();
      }, 60_000);
    });
  });
}
