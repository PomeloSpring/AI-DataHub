import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMenuAccess } from '@/hooks/useMenuAccess';
import { EmptyState, LoadingRow } from './shared';

/**
 * 内容中心落地页：跳到当前角色**第一个能进**的页面。
 *
 * 不写死 `/content` → 我的草稿，是因为审核人可能只有 article:review：
 * 固定跳转会把他带进一个 403 空壳页（"点进去只剩空布局"）。这里按菜单登记键
 * 反查，与侧栏用的是同一份权限清单，不另造第二套判断。
 */
const ORDER = [
  { menu: 'content:drafts', path: '/content/drafts' },
  { menu: 'content:manage', path: '/content/manage' },
  { menu: 'content:review', path: '/content/review' },
  { menu: 'content:taxonomy', path: '/content/taxonomy' },
  { menu: 'content:kb-docs', path: '/content/kb-docs' },
];

export default function ContentHome() {
  const navigate = useNavigate();
  const menuAccess = useMenuAccess();

  useEffect(() => {
    if (!menuAccess.ready) return;
    const first = ORDER.find(t => menuAccess.allowed(t.menu));
    if (first) navigate(first.path, { replace: true });
  }, [menuAccess, navigate]);

  if (!menuAccess.ready) return <LoadingRow text="正在确认你的内容权限" />;

  const hasAny = ORDER.some(t => menuAccess.allowed(t.menu));
  if (hasAny) return null;   // 跳转中，不闪空态

  return (
    <EmptyState
      title="内容中心还没有你的可进页面"
      hint="内容创作需要「编辑内容条目」权限，审核与发布各有独立权限。请联系管理员在「角色与菜单」中配置。"
    />
  );
}
