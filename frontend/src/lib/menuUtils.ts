/**
 * 菜单渲染工具: 按权限过滤后, 一级分组(section 标题)下没有任何可见菜单项时
 * 不渲染该标题("没有菜单权限就不要有一级菜单")。
 */

interface MenuItemLike {
  section?: string;
  key?: string;
}

export function pruneEmptySections<T extends MenuItemLike>(items: T[]): T[] {
  const out: T[] = [];
  let pendingSection: T | null = null;
  let buffer: T[] = [];
  const flush = () => {
    if (buffer.length > 0) {
      if (pendingSection) out.push(pendingSection);
      out.push(...buffer);
    }
    pendingSection = null;
    buffer = [];
  };
  for (const it of items) {
    if ('section' in it && it.section !== undefined) {
      flush();
      pendingSection = it;
    } else {
      buffer.push(it);
    }
  }
  flush();
  return out;
}
