import { useCallback, useEffect, useState } from 'react';
import { ArrowRight, Building2, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { contentApi } from '@/api/content';
import { ABOUT_CATEGORY } from './shared';

/**
 * 首页 Hero 右侧的「企业简介」卡。
 *
 * 卡里只放公司介绍（标题 + 摘要 + 点开看完整简介），**不放平台自己的计数**：
 * 上一版这里带过「站点 N · 数据源 N · 内容 N 篇」，那是"这是谁的什么系统"的口径，
 * 而这张卡回答的是"这是哪家公司"。两件事混在一张卡里，读者会以为站点数与数据源数
 * 是公司属性；而且可查范围本来就随角色变化，右栏与问数入口各有一份，这里再抄一遍
 * 只会多出第三个口径。要恢复平台计数请放到工作台区，不要放回简介卡。
 *
 * 简介**事实源在内容中心**（栏目 `about` 下最新一篇在线内容），不在前端硬编码文案：
 * 改介绍走创作→审核→发布，不需要发版；门户只消费发布结果（未发布的草稿不会出现在这里，
 * 服务端按 live 口径过滤，不是前端不显示）。
 */

type Phase = 'loading' | 'ready' | 'empty' | 'error';

interface Profile { slug: string; title: string; summary: string }

export default function CompanyProfile({
  canManage = false, onOpen, onManage,
}: {
  canManage?: boolean;
  onOpen: (slug: string) => void;
  onManage?: () => void;
}) {
  const [phase, setPhase] = useState<Phase>('loading');
  const [profile, setProfile] = useState<Profile | null>(null);
  const [nonce, setNonce] = useState(0);

  const load = useCallback(async () => {
    setPhase('loading');
    try {
      // 只取在线内容：不传 status，服务端门户口径就只回 live 且未下架的条目
      const { data } = await contentApi.list({ category: ABOUT_CATEGORY, page_size: 1 });
      const it = (data.items || [])[0];
      if (it?.slug) {
        setProfile({ slug: it.slug, title: it.title, summary: it.summary || '' });
        setPhase('ready');
      } else {
        setProfile(null);
        setPhase('empty');
      }
    } catch {
      // 读取失败不画成"暂无简介"：两者要用户做的事完全不同（重试 vs 去创建）
      setPhase('error');
    }
  }, []);

  useEffect(() => { void load(); }, [load, nonce]);

  const retry = () => setNonce(n => n + 1);

  if (phase === 'loading') {
    return (
      <div className="flex h-full min-h-[150px] flex-col justify-center gap-2 rounded-xl border bg-background p-3.5">
        <Skeleton className="h-4 w-2/3" />
        <Skeleton className="h-3 w-full" />
        <Skeleton className="h-3 w-4/5" />
      </div>
    );
  }

  if (phase === 'error') {
    return (
      <div className="flex h-full min-h-[150px] flex-col justify-center gap-2 rounded-xl border bg-background p-3.5">
        <p className="text-[13px] font-medium">企业简介读取失败</p>
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          暂时拿不到已发布的企业简介。不是没有内容 —— 可能是接口或服务未就绪。
        </p>
        <Button size="sm" variant="outline" className="mt-1 h-7 w-fit text-[11px]" onClick={retry}>
          <RefreshCw className="mr-1.5 h-3 w-3" />重试
        </Button>
      </div>
    );
  }

  if (phase === 'empty') {
    return (
      <div className="flex h-full min-h-[150px] flex-col justify-center gap-2 rounded-xl border bg-background p-3.5">
        <p className="flex items-center gap-1.5 text-[13px] font-medium">
          <Building2 className="h-3.5 w-3.5 text-primary" />企业简介尚未发布
        </p>
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          「关于本公司」栏目下还没有已发布内容。发布后这里会显示公司介绍。
        </p>
        {canManage && onManage && (
          <Button size="sm" variant="outline" className="mt-1 h-7 w-fit text-[11px]" onClick={onManage}>
            去内容中心创建
          </Button>
        )}
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={() => onOpen(profile!.slug)}
      className="group flex h-full min-h-[150px] w-full flex-col rounded-xl border bg-background p-3.5 text-left transition-colors hover:border-primary/50 hover:bg-muted/50"
    >
      {/* 卡片是 <button>，内部只能用 span 组内容（与 NewsFeed 的可点卡同一写法） */}
      <span className="flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
        <Building2 className="h-3.5 w-3.5 text-primary" />关于本公司
      </span>
      <span className="mt-1.5 line-clamp-2 text-[13.5px] font-semibold leading-snug">{profile!.title}</span>
      <span className="mt-1.5 line-clamp-4 flex-1 text-[11.5px] leading-relaxed text-muted-foreground">
        {profile!.summary || '（本篇未填摘要，点开看正文）'}
      </span>
      <span className="mt-2.5 flex items-center gap-1 border-t pt-2 text-[11px] font-medium text-primary">
        查看完整简介
        <ArrowRight className="h-3 w-3 transition-transform group-hover:translate-x-0.5" />
      </span>
    </button>
  );
}
