import { useEffect, useRef } from 'react';
import {
  Brain, Gem, GlassWater, Grid3x3, Heart, Moon, Sun, TrendingUp, Zap, type LucideIcon,
} from 'lucide-react';
import { DropdownMenuItem } from '@/components/ui/dropdown-menu';
import { useVisLibrary } from '@/hooks/useVisLibrary';
import { useThemeStore, type ThemeId } from '@/stores/themeStore';
import { themePackAvailability } from '@/lib/dashboardDesign';
import { toast } from '@/lib/toast';

/**
 * 系统主题菜单（顶栏「主题风格」）的唯一实现处。
 *
 * 为什么要收口成一个组件：主题清单此前在 5 个布局/首页各抄了一份，改一处漏四处；
 * 更要命的是"主题能不能选"过去没有任何裁决点 —— UI 字模库把主题包禁用后，
 * 主题菜单照样能选，选完图表拿不到那套包（只能回退默认/别套包），出现
 * "外壳是新主题、图表是旧配色"的半截状态（.qoder/rules/ui-vis-library-style.md §1/§3）。
 *
 * 口径：主题 id ↔ 主题包 `tp_<theme>`（与 lib/dashboardDesign 的 themePackFor 同一约定）。
 * 主题包被禁用/下架 → 该主题**从菜单里摘掉**（不给一个点不动的灰项：那只会让人以为
 * 还能救，且把管理员的停用决定当成界面噪声）；库里没有这个包（只存在于 CSS 的主题）→ 照常可选。
 */
export const THEMES: { id: ThemeId; label: string; icon: LucideIcon; desc: string }[] = [
  { id: 'dark', label: '暗色', icon: Moon, desc: '深色背景，适合长时间使用' },
  { id: 'light', label: '亮色', icon: Sun, desc: '浅色背景，清晰明亮' },
  { id: 'datafoundry', label: 'DataFoundry', icon: Gem, desc: '克制优雅，近黑主色 + 宝石色调' },
  { id: 'tech', label: '科技风', icon: Zap, desc: '深蓝底色，霓虹高亮' },
  { id: 'finance', label: '金融风', icon: TrendingUp, desc: '深色海军蓝，金色主调' },
  { id: 'bento', label: 'Bento Grid', icon: Grid3x3, desc: '柔和圆角卡片，彩色区块布局' },
  { id: 'glass', label: '玻璃拟态', icon: GlassWater, desc: '深色半透明毛玻璃质感' },
  { id: 'ainative', label: 'AI-Native', icon: Brain, desc: '深空神经网络，动态光效边框' },
  { id: 'medical', label: '医疗平台', icon: Heart, desc: '清爽蓝绿，专业可信' },
];

/** 主题包编码 → 系统主题（反查）：管理页禁用主题包时要把“系统主题里对应主题也会不可选”说清楚 */
export const themeOfPackCode = (code: string) => THEMES.find(t => `tp_${t.id}` === code);

export default function ThemeMenuItems() {
  // 判定要全量清单（含下架/禁用），否则区分不出"被禁用"与"库里没有"
  const { items, loaded } = useVisLibrary(true, true);
  const theme = useThemeStore(s => s.theme);
  const setTheme = useThemeStore(s => s.setTheme);
  // 清单未加载时按现状全给（拿不到依据就不改变可见项），加载完再按字模库收口
  const shown = loaded
    ? THEMES.filter(t => themePackAvailability(t.id, items).state !== 'blocked')
    : THEMES;

  return (
    <>
      {shown.map(t => {
        const Icon = t.icon;
        return (
          <DropdownMenuItem key={t.id} onClick={() => setTheme(t.id)}
            className={`flex items-center gap-3 ${theme === t.id ? 'bg-accent' : ''}`}>
            <Icon className="h-4 w-4" />
            <div className="flex-1 min-w-0">
              <div className={`text-sm ${theme === t.id ? 'font-medium' : ''}`}>{t.label}</div>
              <div className="text-xs text-muted-foreground truncate">{t.desc}</div>
            </div>
            {theme === t.id && <span className="text-xs text-primary">✓</span>}
          </DropdownMenuItem>
        );
      })}
    </>
  );
}

/**
 * 当前主题的主题包被禁用/下架后的显式收口（不静默换皮）。
 *
 * 挂在应用根：主题包停用后该主题已从菜单摘掉，正在用它的人不能再靠"点进去看看"发现这件事；
 * 管理员停用主题包时，正在用该主题的人必须知道"你的主题已被停用"，
 * 而不是继续停留在"外壳还是这个主题、图表已回退默认样式"的半截状态。
 * 同一条原因只提醒一次（清单刷新/重渲染不重复弹）。
 */
export function useThemePackGuard() {
  const { items, loaded, error } = useVisLibrary(true, true);
  const theme = useThemeStore(s => s.theme);
  const setTheme = useThemeStore(s => s.setTheme);
  const notified = useRef('');

  useEffect(() => {
    if (!loaded || error) return;
    const current = themePackAvailability(theme, items);
    if (current.state !== 'blocked') return;
    const next = THEMES.find(t => t.id !== theme && themePackAvailability(t.id, items).state !== 'blocked');
    const key = `${current.pack?.code || theme}|${next?.id || ''}`;
    if (notified.current === key) return;
    notified.current = key;
    if (next) {
      setTheme(next.id);
      toast.warning(`${current.reason}，系统主题已切换为「${next.label}」`, {
        description: '如需继续使用该主题，请在「系统配置 → UI 字模库」重新启用对应主题包。',
        duration: 6000,
      });
    } else {
      toast.error(`${current.reason}，且没有其它可用主题包，图表将使用默认样式`, {
        description: '请在「系统配置 → UI 字模库」启用至少一套主题包。',
        duration: 6000,
      });
    }
  }, [loaded, error, items, theme, setTheme]);
}
