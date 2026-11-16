import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import PaginationBar from '../PaginationBar';

afterEach(() => cleanup());

describe('PaginationBar 契约', () => {
  it('渲染「第 X / Y 页 · 共 N 条」并反映总页数', () => {
    render(<PaginationBar page={2} pageSize={20} total={100} onChange={() => {}} />);
    // 100 / 20 = 5 页
    expect(screen.getByText(/第\s*2\s*\/\s*5\s*页/)).toBeTruthy();
    expect(screen.getByText(/共\s*100\s*条/)).toBeTruthy();
  });

  it('静态底栏：不悬浮（禁 sticky/bottom-0 把底栏钉在视口压住表格行）+ 不透明背景', () => {
    const { container } = render(
      <PaginationBar page={1} pageSize={20} total={100} onChange={() => {}} />,
    );
    const bar = container.querySelector('[data-slot="pagination-bar"]') as HTMLElement;
    expect(bar).toBeTruthy();
    const cls = bar.className;
    // 不再悬浮：不得出现 sticky / bottom-0（历史缺陷：整页滚动时底栏浮在行上盖住数据）
    expect(cls).not.toContain('sticky');
    expect(cls).not.toContain('bottom-0');
    // 不透明：必须是 bg-background，不得是 bg-background/95 之类半透明
    expect(cls).toContain('bg-background');
    expect(cls).not.toContain('bg-background/');
    // 上边框
    expect(cls).toContain('border-t');
  });

  it('边界禁用：第 1 页禁「上一页」，末页禁「下一页」', () => {
    const { rerender } = render(<PaginationBar page={1} pageSize={20} total={100} onChange={() => {}} />);
    expect((screen.getByText('上一页').closest('button') as HTMLButtonElement).disabled).toBe(true);
    rerender(<PaginationBar page={5} pageSize={20} total={100} onChange={() => {}} />);
    expect((screen.getByText('下一页').closest('button') as HTMLButtonElement).disabled).toBe(true);
  });

  it('点击上一页/下一页回调正确页码', () => {
    const onChange = vi.fn();
    render(<PaginationBar page={3} pageSize={20} total={100} onChange={onChange} />);
    fireEvent.click(screen.getByText('下一页').closest('button') as HTMLButtonElement);
    expect(onChange).toHaveBeenCalledWith(4);
    fireEvent.click(screen.getByText('上一页').closest('button') as HTMLButtonElement);
    expect(onChange).toHaveBeenCalledWith(2);
  });

  it('未提供 onPageSizeChange 时不显示每页条数选择器（禁止死控件）', () => {
    const { container } = render(<PaginationBar page={1} pageSize={20} total={100} onChange={() => {}} />);
    expect(container.querySelector('[aria-label="每页条数"]')).toBeNull();
  });

  it('提供 onPageSizeChange 时显示选择器，候选含 20/50/100', async () => {
    const onPageSizeChange = vi.fn();
    render(
      <PaginationBar page={1} pageSize={20} total={100} onChange={() => {}} onPageSizeChange={onPageSizeChange} />,
    );
    const trigger = document.querySelector('[aria-label="每页条数"]') as HTMLElement;
    expect(trigger).toBeTruthy();
    fireEvent.click(trigger);
    // radix Select 选项渲染在 portal：按唯一文案查找（50/100 只作为选项出现）
    expect(await screen.findByText('50 条')).toBeTruthy();
    expect(screen.getByText('100 条')).toBeTruthy();
    // 20 会同时出现在触发器回显与选项中
    expect(screen.getAllByText('20 条').length).toBeGreaterThanOrEqual(1);
  });

  it('hideWhenSingle：仅 1 页时不渲染', () => {
    const { container } = render(
      <PaginationBar page={1} pageSize={20} total={5} onChange={() => {}} hideWhenSingle />,
    );
    expect(container.querySelector('[data-slot="pagination-bar"]')).toBeNull();
  });
});

describe('静态护栏：列表页分页一律走 PaginationBar，禁止裸写内联分页', () => {
  const PAGES_DIR = path.resolve(__dirname, '..', '..', 'pages');

  function walk(dir: string, out: string[] = []): string[] {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full, out);
      else if (/\.tsx$/.test(entry.name) && !/\.test\.tsx$/.test(entry.name)) out.push(full);
    }
    return out;
  }

  it('出现「上一页」文案的页面必须 import PaginationBar（不得另写一套分页条）', () => {
    const offenders: string[] = [];
    for (const file of walk(PAGES_DIR)) {
      const code = fs.readFileSync(file, 'utf8');
      // 只拦“自己写了上一页按钮”：命中文案但未引入统一组件即视为回归
      if (code.includes('上一页') && !code.includes('PaginationBar')) {
        offenders.push(path.relative(PAGES_DIR, file));
      }
    }
    expect(offenders).toEqual([]);
  });

  it('不得再写半透明底栏（bg-background/NN 会让表格行从分页条透出）', () => {
    const offenders: string[] = [];
    for (const file of walk(PAGES_DIR)) {
      const code = fs.readFileSync(file, 'utf8');
      if (/上一页[\s\S]{0,200}bg-background\/\d/.test(code)) {
        offenders.push(path.relative(PAGES_DIR, file));
      }
    }
    expect(offenders).toEqual([]);
  });

  /**
   * 分页档位只能用规范集 {20,50,100} 的子集（用户反馈「怎么还能有 12 条」）。
   *
   * 为什么不再一律禁掉显式 `pageSizeOptions`：`list-pagination-server-side` §3 明文要求
   * 「档位候选必须 ≤ 服务端 page_size 上限」—— 接口 `Query(le=50)` 时给 100 只会换来 422（死选项），
   * 典型就是内容中心文章清单（`/content/articles` le=50 → 档位 [20, 50]）。旧护栏把这种
   * “按服务端能力收窄”也一并拒掉，与同族规则自相矛盾（2026-11-16 全量 vitest 跑出）。
   * 收紧为两条：① 出现的档位必须都 ∈ 规范集（12/24 这类自定义档位仍拦）；
   * ② 窄于规范集时必须就地写明服务端上限依据（注释含 `le=` 或「上限」），
   *   防止“顺手写个收窄档位”被当成合理配置。默认仍是 [20,50,100]，不传就不进本判定。
   */
  it('分页档位只能用规范集 20/50/100 的子集，收窄必须写明服务端上限依据', () => {
    const canonical = [20, 50, 100];
    const offenders: string[] = [];
    for (const file of walk(PAGES_DIR)) {
      const code = fs.readFileSync(file, 'utf8');
      const re = /pageSizeOptions=\{([A-Za-z0-9_]+|\[[^\]]*\])\}/g;
      let m: RegExpExecArray | null;
      while ((m = re.exec(code))) {
        const ref = m[1];
        const rel = path.relative(PAGES_DIR, file);
        // 解析档位清单：内联字面量直接取，标识符则回查同文件的 const 声明
        const src = ref.startsWith('[')
          ? ref
          : (new RegExp(`(?:const|let)\\s+${ref}\\s*(?::\\s*number\\[\\s*\\])?\\s*=\\s*(\\[[^\\]]*\\])`).exec(code)?.[1] ?? '');
        const nums = (src.match(/\d+/g) ?? []).map(Number);
        if (!nums.length) { offenders.push(`${rel} → 档位清单解不到数字（${ref}）`); continue; }
        const bad = nums.filter(n => !canonical.includes(n));
        if (bad.length) { offenders.push(`${rel} → ${bad.join('/')}`); continue; }
        if (nums.length < canonical.length && !/le=|page_size[^\n]{0,30}上限|上限[^\n]{0,30}page_size/.test(code)) {
          offenders.push(`${rel} → 收窄档位未就地写明服务端上限依据`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it('客户端分页初始档位只能是 20/50/100（useClientPagination 第二参不得写 12/24 等）', () => {
    const offenders: string[] = [];
    for (const file of walk(PAGES_DIR)) {
      const code = fs.readFileSync(file, 'utf8');
      const re = /useClientPagination\s*\([^)]*,\s*(\d+)\s*\)/g;
      let m: RegExpExecArray | null;
      while ((m = re.exec(code))) {
        const n = Number(m[1]);
        if (![20, 50, 100].includes(n)) offenders.push(`${path.relative(PAGES_DIR, file)} → ${n}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
