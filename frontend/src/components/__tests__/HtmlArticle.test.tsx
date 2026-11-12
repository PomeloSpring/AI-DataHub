import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import HtmlArticle, { sanitizeArticleHtml } from '../HtmlArticle';

/**
 * HTML 版式渲染口的契约（内容中心 kind='html' 的唯一消费路径）：
 * 1. **排版能力留下**：时间轴、卡片、表格这类结构，以及 `style` 里的主题变量
 *    （暗/亮两套主题全靠 `hsl(var(--x))`，写死色值才是错的）；
 * 2. **可执行的东西进不了 DOM**：脚本、事件属性、javascript: 链接、外部框架；
 * 3. **站外链接自动补 target/rel**，站内相对路径保持同页导航（不能把 /read/xxx 也开新窗口）。
 *
 * 服务端门禁（content_service.HTML_UNSAFE_RE）在入库时就拒同一批标记，这里是渲染侧的第二道，
 * 两道口径一致才不会"存得进去、渲染出来缺块东西"。
 */

afterEach(() => {
  document.body.innerHTML = '';
});

describe('HtmlArticle — HTML 版式正文的清洗渲染', () => {
  it('保留排版结构与主题变量内联样式', () => {
    const html = '<section style="display:grid;gap:30px">'
      + '<h3>企业发展时间线</h3>'
      + '<div style="position:relative;padding-left:28px">'
      + '<span style="background:hsl(var(--primary));box-shadow:0 0 0 4px hsl(var(--primary) / .18)"></span>'
      + '<p style="color:hsl(var(--muted-foreground))">2019 年 6 月，公司成立。</p></div>'
      + '<table><thead><tr><th>主体</th></tr></thead><tbody><tr><td>资阳联耀医疗器械有限责任公司</td></tr></tbody></table>'
      + '</section>';
    render(<HtmlArticle html={html} />);
    expect(screen.getByText('企业发展时间线').tagName).toBe('H3');
    expect(screen.getByText('2019 年 6 月，公司成立。').getAttribute('style')).toContain('hsl(var(--muted-foreground))');
    const dot = document.querySelector('span[style*="box-shadow"]');
    expect(dot).not.toBeNull();
    expect(dot?.getAttribute('style')).toContain('hsl(var(--primary) / .18)');
    expect(document.querySelector('table caption') === null).toBe(true);
    expect(screen.getByText('资阳联耀医疗器械有限责任公司').tagName).toBe('TD');
  });

  it('可执行标记一律进不了 DOM（脚本 / 事件属性 / javascript: / 外部框架 / style 块）', () => {
    const hostile = '<div><p onclick="window.hacked=1">正文</p>'
      + '<script>window.hacked=1</script>'
      + '<style>body{display:none}</style>'
      + '<iframe src="https://evil.example"></iframe>'
      + '<a href="javascript:window.hacked=1">点我</a>'
      + '<a href="https://ok.example">正常链接</a></div>';
    const clean = sanitizeArticleHtml(hostile);
    render(<HtmlArticle html={hostile} />);
    expect(screen.getByText('正文')).toBeInTheDocument();          // 文字留着，只是不再有处理器
    expect(document.querySelector('script')).toBeNull();
    expect(document.querySelector('style')).toBeNull();
    expect(document.querySelector('iframe')).toBeNull();
    expect(document.querySelector('[onclick]')).toBeNull();
    expect(clean).not.toMatch(/javascript:/i);
    expect((window as { hacked?: number }).hacked).toBeUndefined();
    // 整页版式不能被库内容打乱：body{display:none} 这类无作用域规则必须被剥掉
    expect(document.body.style.display).not.toBe('none');
  });

  it('站外链接补 target/rel，站内相对链接保持同页导航', () => {
    const html = '<div><a href="https://www.allied-star.com">官方网站</a>'
      + '<a href="/read/scan-accuracy-metric">站内文章</a></div>';
    render(<HtmlArticle html={html} />);
    const out = screen.getByRole('link', { name: '官方网站' });
    expect(out).toHaveAttribute('target', '_blank');
    expect(out).toHaveAttribute('rel', 'noopener noreferrer');
    const inner = screen.getByRole('link', { name: '站内文章' });
    expect(inner).not.toHaveAttribute('target');
  });

  it('空正文渲染成空容器，不抛错也不留占位文案', () => {
    const { container } = render(<HtmlArticle html="" />);
    expect(container.querySelector('div')?.innerHTML).toBe('');
  });
});
