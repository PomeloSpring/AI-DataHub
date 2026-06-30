import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { BarChart3, MessageSquare, Zap, Shield } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Spinner } from '@/components/ui/spinner';
import { useAuthStore } from '../stores/authStore';

const features = [
  { icon: MessageSquare, title: '自然语言查询', desc: '用中文提问，AI 自动生成 SQL' },
  { icon: BarChart3, title: '智能可视化', desc: '自动推荐最佳图表类型' },
  { icon: Zap, title: '实时分析', desc: '秒级返回查询结果' },
  { icon: Shield, title: '安全可控', desc: 'SQL 校验与权限管理' },
];

export default function Login() {
  const [loading, setLoading] = useState(false);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const navigate = useNavigate();
  const login = useAuthStore((s) => s.login);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username || !password) {
      toast.error('请输入用户名和密码');
      return;
    }

    setLoading(true);
    try {
      await login(username, password);
      navigate('/');
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '登录失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-slate-900 via-blue-950 to-indigo-950 p-4">
      {/* Background decoration */}
      <div className="absolute inset-0 overflow-hidden">
        <div className="absolute top-1/4 -left-1/4 w-96 h-96 bg-blue-500/10 rounded-full blur-3xl" />
        <div className="absolute bottom-1/4 -right-1/4 w-96 h-96 bg-indigo-500/10 rounded-full blur-3xl" />
      </div>

      <div className="relative z-10 w-full max-w-[960px] flex flex-col lg:flex-row items-center gap-8 lg:gap-16">
        {/* Left: Branding & Features */}
        <div className="flex-1 text-center lg:text-left">
          <div className="flex items-center justify-center lg:justify-start gap-3 mb-6">
            <div className="w-12 h-12 rounded-xl bg-primary flex items-center justify-center">
              <BarChart3 className="h-7 w-7 text-primary-foreground" />
            </div>
            <h1 className="text-3xl font-bold text-white tracking-tight">AI-DataHub</h1>
          </div>
          <p className="text-lg text-blue-100/80 mb-8 max-w-md mx-auto lg:mx-0">
            智能数据分析平台 — 用自然语言与数据对话
          </p>
          <div className="grid grid-cols-2 gap-4 max-w-md mx-auto lg:mx-0">
            {features.map((f) => {
              const Icon = f.icon;
              return (
                <div
                  key={f.title}
                  className="p-3 rounded-lg bg-white/5 border border-white/10 backdrop-blur-sm"
                >
                  <Icon className="h-5 w-5 text-blue-300 mb-2" />
                  <div className="text-sm font-medium text-white">{f.title}</div>
                  <div className="text-xs text-blue-200/60 mt-0.5">{f.desc}</div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Right: Login Card */}
        <Card className="w-full max-w-[400px] shadow-2xl border-0 !bg-white backdrop-blur-sm">
          <CardHeader className="text-center pb-2">
            <CardTitle className="text-2xl font-bold !text-slate-900">登录</CardTitle>
            <p className="text-sm !text-slate-500 mt-1">输入您的账号信息</p>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="username" className="!text-slate-700">用户名</Label>
                <Input
                  id="username"
                  placeholder="请输入用户名"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  disabled={loading}
                  autoComplete="username"
                  className="h-11 !bg-white/90 !text-slate-900 !border-slate-300 placeholder:!text-slate-400"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="password" className="!text-slate-700">密码</Label>
                <Input
                  id="password"
                  type="password"
                  placeholder="请输入密码"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  disabled={loading}
                  autoComplete="current-password"
                  className="h-11 !bg-white/90 !text-slate-900 !border-slate-300 placeholder:!text-slate-400"
                />
              </div>
              <Button type="submit" className="w-full h-11" disabled={loading}>
                {loading ? <Spinner className="mr-2" size={16} /> : null}
                {loading ? '登录中...' : '登录'}
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
