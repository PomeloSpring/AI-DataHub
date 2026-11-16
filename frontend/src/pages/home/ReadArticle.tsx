import { useEffect } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useBrandStore } from '@/stores/brandStore';
import { DEFAULT_APP_NAME } from '@/lib/brand';
import BrandLogo from '@/components/BrandLogo';
import SectionSwitcher from '@/components/SectionSwitcher';
import LanguageMenu from '@/components/LanguageMenu';
import ReleaseNotesButton from '@/components/ReleaseNotesButton';
import ArticleReader from './ArticleReader';

/**
 * 文章阅读页 `/read/:slug` —— 内容中心已发布内容的独立阅读页（"相当于单独的网页"）。
 *
 * 与报告中心内嵌查看共用 `ArticleReader`（同一份阅读正文/期次/附件/引用口径），本页只是
 * 给它套一层可独立访问的页面外壳（品牌头 + 模块切换 + 期次走 URL）。分工是刻意的，
 * 不与编排预览 `/content/preview/:slug` 合并：本页只读 live（未发布 404，服务端裁决）且计阅读数。
 *
 * 2026-11-16 用户拍板：左上角「返回首页」去掉 —— 这一页是独立网页，不给"退回首页"的导航，
 * 期次/文章跳转都留在本页（?release=N 与 /read/:slug 均可分享）。
 */
export default function ReadArticle() {
  const { slug = '' } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  // 期次在 URL 里（分享链接能直接指到旧那一期）：非法值归 0 = 看当前那一期
  const release = Number.isFinite(Number(searchParams.get('release')))
    ? Math.max(0, Math.trunc(Number(searchParams.get('release') || 0))) : 0;
  const { brand, fetchBrand } = useBrandStore();

  useEffect(() => { void fetchBrand(); }, [fetchBrand]);

  const pickRelease = (value: string) => {
    const next = new URLSearchParams(searchParams);
    // 0 = 当前那一期，不往 URL 里留个无意义的参数（保持旧链接形状不变）
    if (value === '0') next.delete('release');
    else next.set('release', value);
    setSearchParams(next, { replace: true });
  };

  return (
    <div className="flex h-full min-h-0 flex-col bg-background">
      <header className="z-10 border-b bg-background/95 backdrop-blur">
        <div className="mx-auto flex w-full max-w-4xl items-center justify-between px-6 py-2.5">
          <span className="flex items-center gap-2">
            <BrandLogo brand={brand} size={24} />
            {brand.show_text && <span className="text-sm font-semibold tracking-tight">{brand.app_name || DEFAULT_APP_NAME}</span>}
          </span>
          <div className="flex items-center gap-1.5">
            <ReleaseNotesButton />
            <SectionSwitcher current="home" />
            {/* 语言切换：顶栏常驻，不随模块切换显隐（与四套布局同口径） */}
            <LanguageMenu />
          </div>
        </div>
      </header>

      <ArticleReader
        slug={slug}
        release={release}
        onPickRelease={pickRelease}
        onOpenArticle={(s) => navigate(`/read/${encodeURIComponent(s)}`)}
        asMain
      />
    </div>
  );
}
