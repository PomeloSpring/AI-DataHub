import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { toast } from '@/lib/toast';
import {
  ArrowRight, BarChart3, Eye, EyeOff,
  Lock, MessageSquare, Shield, Sparkles, User, Zap,
} from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Spinner } from '@/components/ui/spinner';
import BrandLockup from '@/components/BrandLockup';
import { workspaceLandingPath } from '@/components/WorkspaceRoute';
import { useBrandStore } from '../stores/brandStore';
import { brandNameOf } from '@/lib/brand';
import { useAuthStore } from '../stores/authStore';
import { useWorkspaceStore } from '../stores/workspaceStore';

const features = [
  { icon: MessageSquare, title: '自然语言查询', desc: '用中文提问，AI 自动生成 SQL' },
  { icon: BarChart3, title: '智能可视化', desc: '自动推荐最佳图表类型' },
  { icon: Zap, title: '实时分析', desc: '秒级返回查询结果' },
  { icon: Shield, title: '安全可控', desc: 'SQL 校验与权限管理' },
];

/** 左侧品牌区的迷你图表剪影（纯装饰，随主色变化） */
function ChartSilhouette() {
  const bars = [34, 52, 42, 66, 48, 78, 60, 90];
  return (
    <div className="absolute inset-x-0 bottom-0 h-40 pointer-events-none select-none" aria-hidden>
      {/* 柱状剪影 */}
      <div className="absolute bottom-0 left-8 right-8 flex items-end gap-2.5 h-28">
        {bars.map((h, i) => (
          <div
            key={i}
            className="flex-1 rounded-t-sm bg-primary-foreground/10"
            style={{ height: `${h}%` }}
          />
        ))}
      </div>
      {/* 折线剪影 */}
      <svg
        viewBox="0 0 320 100"
        preserveAspectRatio="none"
        className="absolute bottom-4 left-8 right-8 h-28 w-[calc(100%-4rem)] text-primary-foreground/35"
      >
        <path
          d="M0 78 L46 58 L92 66 L138 38 L184 48 L230 20 L276 30 L320 8"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <path
          d="M0 78 L46 58 L92 66 L138 38 L184 48 L230 20 L276 30 L320 8 L320 100 L0 100 Z"
          fill="currentColor"
          opacity="0.08"
        />
      </svg>
    </div>
  );
}

export default function Login() {
  const [loading, setLoading] = useState(false);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const navigate = useNavigate();
  const login = useAuthStore((s) => s.login);
  // 品牌设置（名称/图标/标语/显示开关）：登录页也是品牌门面，必须与
  // 「系统配置 → 品牌设置」同源同变（/admin/brand 读已登记为公开路由，无需身份）。
  const brand = useBrandStore((s) => s.brand);
  const fetchBrand = useBrandStore((s) => s.fetchBrand);

  useEffect(() => { void fetchBrand(); }, [fetchBrand]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username || !password) {
      toast.error('请输入用户名/邮箱和密码');
      return;
    }

    setLoading(true);
    try {
      await login(username, password);
      // 登录落地默认进工作空间（与顶栏模块切换器、首页「工作空间」卡同一落点口径，默认页 = 智能问数）。
      // 刚登录时 workspaceStore 已被 reset，拿不到默认空间 id 就走 /workspace 入口解析，
      // 由它拉清单后跳到该用户默认空间的智能问数；无可访问空间时显式告知原因，不静默转首页。
      navigate(workspaceLandingPath(useWorkspaceStore.getState().getDefaultWorkspaceId()));
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '登录失败');
    } finally {
      setLoading(false);
    }
  };

  const [showPwd, setShowPwd] = useState(false);

  return (
    <div className="relative min-h-screen flex items-center justify-center overflow-hidden bg-secondary p-4">
      {/* 背景装饰层：细网格 + 极光光斑（全部走主题变量，切主题不破相） */}
      <div
        aria-hidden
        className="absolute inset-0 pointer-events-none"
        style={{
          backgroundImage:
            'linear-gradient(hsl(var(--border) / 0.35) 1px, transparent 1px),' +
            'linear-gradient(90deg, hsl(var(--border) / 0.35) 1px, transparent 1px)',
          backgroundSize: '44px 44px',
          maskImage: 'radial-gradient(ellipse 70% 60% at 50% 40%, black 30%, transparent 75%)',
          WebkitMaskImage: 'radial-gradient(ellipse 70% 60% at 50% 40%, black 30%, transparent 75%)',
        }}
      />
      <div aria-hidden className="absolute inset-0 pointer-events-none overflow-hidden">
        <div className="login-orb absolute -top-24 -left-24 h-96 w-96 rounded-full bg-primary/25 blur-3xl" />
        <div className="login-orb login-orb-delay absolute -bottom-32 -right-16 h-[26rem] w-[26rem] rounded-full bg-chart-2/20 blur-3xl" />
        <div className="login-orb login-orb-delay2 absolute top-1/3 right-1/4 h-64 w-64 rounded-full bg-chart-4/15 blur-3xl" />
      </div>

      {/* 主体：双栏大卡片（窄屏只留表单栏） */}
      <div className="relative z-10 w-full max-w-5xl login-rise">
        <div className="grid md:grid-cols-[1.1fr_1fr] rounded-2xl border border-border/60 bg-card shadow-2xl shadow-black/20 overflow-hidden">
          {/* 左：品牌叙事区 */}
          <div className="relative hidden md:flex flex-col justify-between overflow-hidden bg-gradient-to-br from-primary via-primary to-primary/85 text-primary-foreground p-10">
            <div className="relative z-10">
              <BrandLockup
                brand={brand}
                size={44}
                tone="onPrimary"
                nameClassName="text-lg leading-tight"
                taglineClassName="text-[11px] leading-tight"
              />

              <h1 className="mt-10 text-[26px] font-bold leading-snug tracking-tight">
                让数据问答
                <br />
                像对话一样自然
              </h1>
              <p className="mt-3 text-sm opacity-75 leading-relaxed max-w-[300px]">
                连接你的数据源，用一句中文获得可信的查询结果、图表与洞察。
              </p>

              <ul className="mt-8 space-y-3 max-w-[320px]">
                {features.map((f) => {
                  const Icon = f.icon;
                  return (
                    <li key={f.title} className="flex items-center gap-3">
                      <span className="shrink-0 w-8 h-8 rounded-lg bg-primary-foreground/10 flex items-center justify-center">
                        <Icon className="h-4 w-4" />
                      </span>
                      <span className="min-w-0">
                        <span className="block text-[13px] font-medium leading-tight">{f.title}</span>
                        <span className="block text-[11px] opacity-65 leading-tight mt-0.5 truncate">{f.desc}</span>
                      </span>
                    </li>
                  );
                })}
              </ul>
            </div>

            <div className="relative z-10 mt-6 flex items-center gap-1.5 text-[11px] opacity-70">
              <Sparkles className="h-3.5 w-3.5" />
              AI大模型 * 语义层 * 全链路权限治理
            </div>
            <ChartSilhouette />
          </div>

          {/* 右：登录表单区 */}
          <Card className="border-0 bg-card shadow-none rounded-none">
            <CardContent className="p-8 sm:p-10 flex flex-col justify-center min-h-[520px]">
              {/* 移动端品牌头（左栏隐藏时补位：窄屏空紧，只显图标+名称不显标语） */}
              <div className="flex md:hidden items-center justify-center mb-8">
                <BrandLockup brand={brand} size={40} showTagline={false} nameClassName="text-xl leading-tight text-foreground" />
              </div>

              <div className="mb-8">
                <h2 className="text-2xl font-bold tracking-tight text-foreground">欢迎回来</h2>
                <p className="text-sm text-muted-foreground mt-1.5">登录以继续你的数据分析工作</p>
              </div>

              <form onSubmit={handleSubmit} className="space-y-5">
                <div className="space-y-1.5">
                  <Label htmlFor="username" className="text-xs font-medium text-muted-foreground">用户名 / 邮箱</Label>
                  <div className="relative">
                    <User className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input
                      id="username"
                      placeholder="请输入用户名或邮箱"
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                      disabled={loading}
                      autoComplete="username"
                      className="h-11 pl-9 rounded-lg"
                    />
                  </div>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="password" className="text-xs font-medium text-muted-foreground">密码</Label>
                  <div className="relative">
                    <Lock className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input
                      id="password"
                      type={showPwd ? 'text' : 'password'}
                      placeholder="请输入密码"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      disabled={loading}
                      autoComplete="current-password"
                      className="h-11 pl-9 pr-10 rounded-lg"
                    />
                    <button
                      type="button"
                      tabIndex={-1}
                      aria-label={showPwd ? '隐藏密码' : '显示密码'}
                      onClick={() => setShowPwd((v) => !v)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                    >
                      {showPwd ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                    </button>
                  </div>
                </div>
                <Button
                  type="submit"
                  className="w-full h-11 rounded-lg text-sm font-medium mt-1 group"
                  disabled={loading}
                >
                  {loading ? <Spinner className="mr-2" size={14} /> : null}
                  {loading ? '登录中...' : '登录'}
                  {!loading && <ArrowRight className="ml-1.5 h-4 w-4 transition-transform group-hover:translate-x-0.5" />}
                </Button>
              </form>

              <div className="mt-8 pt-6 border-t border-border/60 text-center text-[11px] text-muted-foreground">
                受权限治理的安全数据环境 · 所有查询均留审计痕迹
              </div>
            </CardContent>
          </Card>
        </div>

        <p className="mt-6 text-center text-[11px] text-muted-foreground/70">
          © {new Date().getFullYear()} {brandNameOf(brand.app_name)}
        </p>
      </div>
    </div>
  );
}
