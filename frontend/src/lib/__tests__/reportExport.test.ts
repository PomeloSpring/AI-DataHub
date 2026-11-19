// 报告导出的纯函数契约：格式取舍、文件名、所见即所得取正文、打印能力缺失时的显式失败。
//
// 三条容易悄悄做错的地方各自有用例：
// * HTML 报告给 .md 入口 = 把 HTML 塞进 markdown 文件，语义错；
// * canvas/地图的像素不在 innerHTML 里 —— 直接序列化会得到空白图或一屏散落瓦片，
//   所以导出前要先拍成位图；拍不出来必须留可见说明，不得静默发空白；
// * 没有 window.print 时必须拒绝并说清出路，静默无反应看起来像按钮坏了。
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import {
  availableExportFormats, collectAppCss, isStandaloneHtmlDocument, printReport, renderedHtml, reportFileName,
  standaloneHtml, downloadTextFile, PRINT_CLASS, EXPORT_ALLOWED_TAGS, EXPORT_ALLOWED_ATTR,
} from '../reportExport'
import { ARTICLE_ALLOWED_ATTR, ARTICLE_ALLOWED_TAGS } from '@/components/HtmlArticle'

// jsdom 拍不出像素：html2canvas 用桩返回一张"有内容"的画布；
// 空白判定另有全同色画布的用例。
const h2c = vi.hoisted(() => vi.fn());
vi.mock('html2canvas', () => ({ default: h2c }));

/**
 * 造一张"快照画布"。
 *
 * 不用真 canvas：jsdom 没有 canvas 后端（getContext 直接 not implemented），
 * 而这里要验的是 reportExport 的像素采样逻辑，喂它一个可控的假画布反而更准。
 * blank=true 时所有采样点同色 —— 等价于瓦片没被 html2canvas 画上去。
 */
function makeCanvas(width = 20, height = 12, blank = false) {
  const data = (x: number, y: number) => new Uint8ClampedArray(blank ? [245, 244, 240, 255] : [x * 3 + y, 120, 200, 255]);
  const drawImage = vi.fn();
  const canvas = {
    width, height,
    getContext: () => ({ getImageData: (x: number, y: number) => ({ data: data(x, y) }), drawImage }),
    toDataURL: () => 'data:image/png:shot',
  };
  (canvas as unknown as { drawImage: typeof drawImage }).drawImage = drawImage;
  return canvas as unknown as HTMLCanvasElement & { drawImage: typeof drawImage };
}

/** jsdom 里 getBoundingClientRect 恒为 0，合成图层需要真实几何 → 定点桩 */
function stubRect(el: Element, left: number, top: number, width: number, height: number) {
  (el as HTMLElement).getBoundingClientRect = () =>
    ({ left, top, width, height, right: left + width, bottom: top + height, x: left, y: top, toJSON: () => ({}) }) as DOMRect;
}

const mount = (html: string) => {
  const host = document.createElement('div')
  host.innerHTML = html
  document.body.appendChild(host)
  return host
}

afterEach(() => { document.body.innerHTML = ''; document.documentElement.className = '' })

describe('导出格式取舍', () => {
  it('HTML 正文不出 Markdown 文件', async () => {
    expect(availableExportFormats('html')).toEqual(['html', 'pdf'])
    expect(availableExportFormats('markdown')).toEqual(['md', 'html', 'pdf'])
    expect(availableExportFormats(undefined)).toContain('md')
  })

  it('整份 HTML 文档只给 HTML 导出（iframe 隔离渲染拿不到全文分页，不给会截页的 PDF）', () => {
    expect(availableExportFormats('html', true)).toEqual(['html'])
    expect(availableExportFormats('html', false)).toEqual(['html', 'pdf'])
  })
})

describe('整份自包含 HTML 文档判定', () => {
  it('doctype 或 <html> 开头才算整份文档', () => {
    expect(isStandaloneHtmlDocument('<!DOCTYPE html><html><body>x</body></html>')).toBe(true)
    expect(isStandaloneHtmlDocument('<html lang="zh-CN"><style>a{}</style></html>')).toBe(true)
    // 正文里提到 <html> 不算（只看文档头部）
    expect(isStandaloneHtmlDocument('<p>参考文档里的 &lt;html&gt; 标签</p>')).toBe(false)
    expect(isStandaloneHtmlDocument('<h1>片段</h1><table></table>')).toBe(false)
    expect(isStandaloneHtmlDocument('')).toBe(false)
    expect(isStandaloneHtmlDocument(undefined)).toBe(false)
  })
})

describe('导出文件名', () => {
  it('用业务标题 + 报告日期，文件系统非法字符换下划线', async () => {
    expect(reportFileName('新加坡/近30天: 新增案例?', '2026-11-02T09:00:00', 'md'))
      .toBe('新加坡_近30天_ 新增案例_-2026-11-02.md')
  })

  it('标题为空也不产出裸日期名（回落"报告"）', async () => {
    expect(reportFileName('   ', '2026-11-02T09:00:00', 'html')).toBe('报告-2026-11-02.html')
  })

  it('时间戳不可解析时只留标题，不写出 Invalid Date', async () => {
    const name = reportFileName('案例分布', 'not-a-date', 'md')
    expect(name).toBe('案例分布.md')
    expect(name).not.toContain('Invalid')
  })
})

describe('自包含 HTML 文档', () => {
  it('标题做 HTML 转义，正文原样嵌入', async () => {
    const doc = standaloneHtml({ title: '<script>x</script>', innerHtml: '<table><tr><td>1</td></tr></table>' })
    expect(doc).toContain('&lt;script&gt;x&lt;/script&gt;')
    expect(doc).toContain('<table><tr><td>1</td></tr></table>')
    expect(doc.startsWith('<!DOCTYPE html>')).toBe(true)
  })

  it('不带 css 时只有基础排版（老调用形状仍可用）', async () => {
    const doc = standaloneHtml({ title: 't', innerHtml: '<p>a</p>' })
    expect(doc).toContain('max-width: 900px')
    expect(doc.match(/<style>/g) || []).toHaveLength(1)
    expect(doc).not.toContain('<div class="">')
  })

  it('样式保真三件套：内联本站 CSS + 镜像根主题类 + 正文容器类包一层', async () => {
    const doc = standaloneHtml({
      title: '案例分布', innerHtml: '<p>正文</p>',
      css: '.tech{--primary:200 80% 50%}.prose p{margin:1em}',
      rootClass: 'tech dark', contentClass: 'mt-5 rounded-xl border bg-card p-5',
    })
    // 根类决定 CSS 变量取值（缺它 → 深色主题导出成浅色）
    expect(doc).toContain('<html lang="zh-CN" class="tech dark">')
    // 本站 CSS 必须在内置基础样式**之后**，否则主题规则被兜底档盖掉
    const styles = doc.split('<style>')
    expect(styles).toHaveLength(3)
    expect(styles[2]).toContain('--primary:200 80% 50%')
    // innerHtml 不含正文容器自身的类，靠这层包裹把卡片/prose 样式带进文件
    expect(doc).toContain('<div class="report-body mt-5 rounded-xl border bg-card p-5">')
    expect(doc).toContain('<p>正文</p>')
  })

  it('文件自己的标题挂类名而不是裸 h1：preflight 会把 h1~h6 字号字重置成 inherit', async () => {
    // 这个标题是导出侧造的（屏幕上不存在），而内联进来的本站 CSS 排在基础档之后，
    // preflight 的裸标签规则会盖掉裸标签写的规则 → 症状就是"导出文件里标题和正文一样大"。
    // 类选择器权重压过元素选择器，与到达顺序无关，这才站得住。
    const doc = standaloneHtml({ title: '入口点击分析', innerHtml: '<p>正文</p>', css: 'h1{font-size:inherit}' })
    expect(doc).toContain('<h1 class="report-title">入口点击分析</h1>')
    expect(doc).toMatch(/\.report-title\s*\{[^}]*font-size:/)
    expect(doc).toMatch(/\.report-title\s*\{[^}]*font-weight:/)
  })

  it('内容级兜底档只在内联 CSS 缺失时出现：与本站 CSS 同场会盖掉 prose 的表格样式', async () => {
    // prose 的规则几乎都写成 `.prose :where(td)…`（:where 权重 0）。兜底档写成 `.report-body td`
    // 权重更高，一旦同时在场就会把屏幕上那套表格样式改写掉 → 又是两个口径。
    const withCss = standaloneHtml({ title: 't', innerHtml: '<table><tr><td>1</td></tr></table>', css: '.prose td{padding:.4em}' })
    expect(withCss).not.toContain('.report-body table')
    expect(withCss).not.toContain('.report-body th')
    // 拿不到本站 CSS 时才需要它兜底：宁排版朴素，也不要裸结构贴边
    const degraded = standaloneHtml({ title: 't', innerHtml: '<table><tr><td>1</td></tr></table>' })
    expect(degraded).toContain('.report-body table')
    expect(degraded).toContain('.report-body th')
    expect(degraded).toContain('max-width: 900px')
  })

  it('版心由调用方给屏幕同款口径，导出侧不再自带 900px 硬上限当唯一宽度', async () => {
    // 富版式（kind='html'）屏幕走宽版填满；导出写死 900px 居中就是把卡片栅格压回中间一列
    //（lib/pageWidth.ts 头注释点名的事故）。shellClass 上到版心后，同权重的工具类排在后面照样胜出。
    const doc = standaloneHtml({ title: 't', innerHtml: '<p>a</p>', shellClass: 'mx-auto w-[94%] min-w-0 max-w-[2200px]' })
    expect(doc).toContain('<div class="report-shell mx-auto w-[94%] min-w-0 max-w-[2200px]">')
  })

  it('内联 CSS 里的 </style 被转义：raw text 里它是唯一能把后文当 HTML 解析的逃逸口', async () => {    const doc = standaloneHtml({ title: 't', innerHtml: '<p>a</p>', css: 'a{content:"</style><img src=x onerror=1>"}' })
    expect(doc).not.toContain('</style><img')
    expect(doc).toContain('<\\/style')
  })

  it('根类/容器类走转义，属性拼接不开洞', async () => {
    const doc = standaloneHtml({ title: 't', innerHtml: '<p>a</p>', rootClass: '" onload="evil()', contentClass: '"' })
    expect(doc).not.toContain('onload="evil')
    expect(doc).not.toContain('class=""')
  })
})

describe('collectAppCss：同源样式表序列化', () => {
  it('把本站 <style> 的规则文本拼出来（导出文件靠它复现配色/间距）', () => {
    const tag = document.createElement('style')
    tag.textContent = '.export-probe-rule{color: rgb(1, 2, 3);}'
    document.head.appendChild(tag)
    try {
      expect(collectAppCss()).toContain('.export-probe-rule')
    } finally {
      tag.remove()
    }
  })

  it('跨域样式表读 cssRules 抛错时跳过该表，不整份导出失败', () => {
    const own = Object.getOwnPropertyDescriptor(document, 'styleSheets')
    Object.defineProperty(document, 'styleSheets', {
      configurable: true,
      value: [
        { get cssRules() { throw new DOMException('The operation is insecure.', 'SecurityError'); } },
        { cssRules: [{ cssText: '.after-cross-origin{color:red}' }] },
      ],
    })
    try {
      const css = collectAppCss()
      expect(css).toContain('.after-cross-origin')   // 坏表之后的表照收
      expect(css).not.toContain('insecure')
    } finally {
      if (own) Object.defineProperty(document, 'styleSheets', own)
      else delete (document as unknown as { styleSheets?: unknown }).styleSheets
    }
  })
})

describe('renderedHtml：交互控件剪掉、地图拍成一张图', () => {
  beforeEach(() => { h2c.mockReset(); h2c.mockResolvedValue(makeCanvas()); });

  function mountMap(host: HTMLElement) {
    host.innerHTML = ''
      + '<div class="card-title">全国企业分布地图</div>'
      + '<div data-export-strip><button>导出</button><span>明细</span>'
      + '<select><option>高德地图标准（默认）</option></select></div>'
      + '<div data-export-snapshot="地图"><div class="leaflet-pane">'
      + '<img src="https://tile.example/1/2/3.png" alt="">'
      + '<img src="https://tile.example/1/2/4.png" alt="">'
      + '</div></div>';
  }

  it('控件条整棵剪掉：不留下「导出/明细/瓦片名」这类没有上下文的残句', async () => {
    const host = document.createElement('div');
    mountMap(host);
    h2c.mockResolvedValue(makeCanvas());
    const html = await renderedHtml(host);
    // 只剥 <button> 标签不够：DOMPurify 保留按钮里的文字，必须连子树一起剪
    expect(html).not.toContain('高德地图标准');
    expect(html).not.toContain('明细');
    expect(html).toContain('全国企业分布地图');   // 业务标题要留
  });

  it('地图快照成一张位图，瓦片不再逐个落进文件', async () => {
    const host = document.createElement('div');
    mountMap(host);
    h2c.mockResolvedValue(makeCanvas());
    const html = await renderedHtml(host);
    expect(html).toContain('data:image/png:shot');
    expect(html).not.toContain('tile.example');           // 瓦片地址不得出现在导出里
    expect((html.match(/<img/g) || []).length).toBe(1);   // 两张瓦片 → 一张快照
    expect(html).toContain('alt="地图"');
  });

  it('取景参数只给 scale/useCORS，不覆写 width/windowWidth（覆写会让克隆文档重排导致裁错块）', async () => {
    const host = document.createElement('div');
    mountMap(host);
    h2c.mockResolvedValue(makeCanvas());
    await renderedHtml(host);
    const opts = h2c.mock.calls[0][1] as Record<string, unknown>;
    expect(opts.useCORS).toBe(true);
    // 这四个会把克隆页面的视口改成元素尺寸，整份文档重排后裁剪坐标就错位，
    // 表现为"导出的中国地图里是中东和印度"（热力点被裁在画面外）
    for (const bad of ['width', 'height', 'windowWidth', 'windowHeight']) {
      expect(opts).not.toHaveProperty(bad);
    }
  });

  it('缩放钮一类的交互控件不拍进静态图', async () => {
    const host = document.createElement('div');
    mountMap(host);
    h2c.mockResolvedValue(makeCanvas());
    await renderedHtml(host);
    const ignore = (h2c.mock.calls[0][1] as { ignoreElements: (n: Element) => boolean }).ignoreElements;
    const control = document.createElement('div');
    control.className = 'leaflet-control-container';
    const tile = document.createElement('img');
    tile.className = 'leaflet-tile';
    expect(ignore(control)).toBe(true);
    expect(ignore(tile)).toBe(false);   // 瓦片必须留着，否则拍出来就是空白
  });

  it('地图上的 canvas 图层由我们自己合成，不交给 html2canvas 复制', async () => {
    const host = document.createElement('div');
    host.innerHTML = '<div data-export-snapshot="地图"><canvas class="heat"></canvas></div>';
    const region = host.querySelector('[data-export-snapshot]') as HTMLElement;
    const heat = host.querySelector('canvas') as HTMLCanvasElement;
    stubRect(host, 0, 0, 800, 400);
    stubRect(region, 0, 0, 800, 400);
    stubRect(heat, 0, 0, 800, 400);
    const base = makeCanvas();
    h2c.mockResolvedValue(base);
    await renderedHtml(host);
    // 交给 html2canvas 时必须把 canvas 排除，否则它那份 + 我们这份会画两遍
    const ignore = (h2c.mock.calls[0][1] as { ignoreElements: (n: Element) => boolean }).ignoreElements;
    expect(ignore(heat)).toBe(true);
    expect(base.drawImage).toHaveBeenCalledTimes(1);
  });

  it('底图没拍到时不合成图层，宁给占位说明也不发"没有地图的彩色斑点"', async () => {
    const host = document.createElement('div');
    host.innerHTML = '<div data-export-snapshot="地图"><canvas class="heat"></canvas></div>';
    const region = host.querySelector('[data-export-snapshot]') as HTMLElement;
    const heat = host.querySelector('canvas') as HTMLCanvasElement;
    stubRect(host, 0, 0, 800, 400);
    stubRect(region, 0, 0, 800, 400);
    stubRect(heat, 0, 0, 800, 400);
    const base = makeCanvas(20, 12, true);
    h2c.mockResolvedValue(base);
    const html = await renderedHtml(host);
    expect(base.drawImage).not.toHaveBeenCalled();
    expect(html).toContain('无法导出为静态图片');
  });

  it('缩放钮在拍摄瞬间隐藏、拍完立刻还原（页面上还要能缩放）', async () => {
    const host = document.createElement('div');
    host.innerHTML = '<div data-export-snapshot="地图"><div class="leaflet-control-container"></div></div>';
    const control = host.querySelector('.leaflet-control-container') as HTMLElement;
    stubRect(host, 0, 0, 800, 400);
    stubRect(host.querySelector('[data-export-snapshot]') as Element, 0, 0, 800, 400);
    h2c.mockImplementation(async () => {
      expect(control.style.visibility).toBe('hidden');   // 拍摄那一刻必须是隐藏的
      return makeCanvas();
    });
    await renderedHtml(host);
    expect(control.style.visibility).toBe('');           // 拍完还原
  });

  it('快照拍出来是一片空白时给可见说明，不发一张空图当地图', async () => {
    const host = document.createElement('div');
    mountMap(host);
    h2c.mockResolvedValue(makeCanvas(20, 12, true));   // 瓦片未允许跨域 → html2canvas 静默不画
    const html = await renderedHtml(host);
    expect(html).not.toContain('data:image/png:shot');
    expect(html).toContain('无法导出为静态图片');
    expect(html).toContain('PDF（浏览器打印）');
  });

  it('快照抛错（画布被跨域瓦片污染）同样走可见说明，不整份导出失败', async () => {
    const host = document.createElement('div');
    mountMap(host);
    h2c.mockRejectedValue(new Error('tainted'));
    const html = await renderedHtml(host);
    expect(html).toContain('无法导出为静态图片');
    expect(html).toContain('全国企业分布地图');   // 其余内容照常导出
  });

  it('快照区之外的 canvas 仍走像素直取（不依赖 html2canvas）', async () => {
    const host = document.createElement('div');
    const canvas = document.createElement('canvas');
    canvas.toDataURL = () => 'data:image/png:echarts';
    host.appendChild(canvas);
    const html = await renderedHtml(host);
    expect(html).toContain('data:image/png:echarts');
    expect(h2c).not.toHaveBeenCalled();
  });
});

describe('所见即所得取正文（文字与图片）', () => {
  it('canvas 读不出像素时留可见占位说明，而不是产出一张空白图', async () => {
    // jsdom 无 canvas 后端：toDataURL 抛错，正好命中"画布不可读"分支
    const host = mount('<p>结论</p><canvas width="10" height="10"></canvas>')
    const html = await renderedHtml(host)
    expect(html).toContain('结论')
    expect(html).not.toContain('<canvas')
    expect(html).toContain('图表无法导出')
  })

  it('canvas 可读时换成 img 快照（导出文件里图表有像素）', async () => {
    const host = mount('<canvas width="10" height="10"></canvas>')
    const canvas = host.querySelector('canvas') as HTMLCanvasElement
    canvas.toDataURL = () => 'data:image/png;base64,AAAA'
    const html = await renderedHtml(host)
    // 导出面的白名单必须放行 img：站内渲染口径不含 img，直接复用会把图表整张洗掉
    expect(html).toContain('<img')
    expect(html).toContain('data:image/png;base64,AAAA')
    expect(html).not.toContain('<canvas')
  })

  it('类名与内联排版样式保留：内联进来的本站 CSS 全靠类名命中，剥类=导出裸结构', async () => {
    const host = mount('<h2 class="text-lg font-semibold">结论</h2>'
      + '<table class="w-full"><tr><td style="text-align:right">1</td></tr></table>')
    const html = await renderedHtml(host)
    expect(html).toContain('class="text-lg font-semibold"')
    expect(html).toContain('class="w-full"')
    expect(html).toContain('text-align:right')
  })

  it('版式结构标签不塌：section/header 连同内联 style 必须整块留下', async () => {
    // 资讯正文（kind='html'）的卡片/章节头就是写在 <section>/<header> 的内联 style 上的。
    // DOMPurify 对不在白名单的标签是"删标签留子节点"：文字照常、结构静默塌掉，
    // 导出的文件看起来成功、其实和屏幕上不是同一份东西。
    const host = mount('<section style="border:1px solid hsl(var(--border));border-radius:16px">'
      + '<header style="display:flex;align-items:center;gap:10px"><span>目</span><h3>站点分布</h3></header>'
      + '<div style="padding:18px">正文</div></section>')
    const html = await renderedHtml(host)
    expect(html).toContain('<section')
    expect(html).toContain('<header')
    expect(html).toContain('border-radius:16px')
    expect(html).toContain('display:flex')
    expect(html).toContain('站点分布')
  })

  it('导出白名单必须是渲染白名单的超集（两份清单手抄必然漂移）', () => {
    ARTICLE_ALLOWED_TAGS.forEach(tag => {
      expect(EXPORT_ALLOWED_TAGS, `渲染面有 <${tag}>、导出面没有 → 屏幕上有的结构导出塌掉`)
        .toContain(tag)
    })
    ARTICLE_ALLOWED_ATTR.forEach(attr => {
      expect(EXPORT_ALLOWED_ATTR, `渲染面留 ${attr}、导出面剥掉 → 版式跟着掉`).toContain(attr)
    })
  })

  it('导出净化拦可执行内容：脚本、事件属性、javascript: 伪协议', async () => {
    // 导出文件会被别人用浏览器直接打开，这里漏一个就是存储型 XSS 换了个载体
    // （放开 class/style 之后重新确认一遍：拦的是可执行的东西，不是排版属性）
    const host = mount('<p class="lead" style="color:red">ok</p><script>alert(1)</script>'
      + '<a href="javascript:steal()" onclick="evil()">链接</a>'
      + '<img src="x.png" onerror="boom()" style="background:url(javascript:steal())">')
    const html = await renderedHtml(host)
    expect(html).toContain('ok')
    expect(html).toContain('class="lead"')
    expect(html).not.toContain('<script')
    expect(html).not.toContain('alert(1)')
    expect(html).not.toContain('onclick')
    expect(html).not.toContain('onerror')
    expect(html).not.toContain('javascript:')
  })

  it('远程图片地址保留：站内 markdown 渲染本来就出图，导出侧再剥就是两个口径', async () => {
    const host = mount('<p>a</p><img src="https://cdn.example/chart.png" alt="图">')
    const html = await renderedHtml(host)
    expect(html).toContain('https://cdn.example/chart.png')
    expect(html).toContain('<img')
  })
})

describe('落盘与打印', () => {
  it('downloadTextFile 走 blob + a[download]，用完即回收 objectURL', async () => {
    const clicked: string[] = []
    // 原实现必须在装 spy **之前**取走：之后再 bind 到的是 mock 自己，会无限递归
    const original = document.createElement.bind(document)
    const create = vi.spyOn(document, 'createElement')
    create.mockImplementation((tag: string) => {
      const el = original(tag) as HTMLElement
      if (tag === 'a') el.click = () => { clicked.push(String((el as HTMLAnchorElement).download)) }
      return el
    })
    const revoke = vi.fn()
    Object.defineProperty(window, 'URL', {
      configurable: true, value: { createObjectURL: vi.fn(() => 'blob:1'), revokeObjectURL: revoke },
    })
    downloadTextFile('报告.md', '# 正文', 'text/markdown')
    expect(clicked).toEqual(['报告.md'])
    expect(revoke).toHaveBeenCalledWith('blob:1')
    create.mockRestore()
  })

  it('浏览器不支持打印时显式拒绝并给出路（不静默无反应）', async () => {
    // jsdom 的 window.print 是个"未实现"占位函数：不删掉就永远走不到守卫分支，
    // 而那条分支才是"按钮按了没反应"和"给出路"的分界线
    const saved = window.print
    delete (window as unknown as { print?: unknown }).print
    try {
      await expect(printReport()).rejects.toThrow(/打印/)
      expect(document.documentElement.classList.contains(PRINT_CLASS)).toBe(false)
    } finally {
      window.print = saved
    }
  })

  it('打印时挂上隔离类名，afterprint 后收起', async () => {
    window.print = vi.fn(() => { window.dispatchEvent(new Event('afterprint')) })
    await printReport()
    expect(window.print).toHaveBeenCalled()
    // 类名在请求帧里挂上、afterprint 后移除；这里断言最终态干净
    expect(document.documentElement.classList.contains(PRINT_CLASS)).toBe(false)
  })
})
