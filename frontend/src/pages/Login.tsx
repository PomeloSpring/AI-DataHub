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
    <div className="min-h-screen flex items-center justify-center bg-secondary p-4">
      <div className="w-full max-w-[400px]">
        {/* Brand Mark */}
        <div className="flex items-center justify-center gap-2.5 mb-8">
          <div className="w-10 h-10 rounded-lg bg-primary flex items-center justify-center">
            <BarChart3 className="h-5 w-5 text-primary-foreground" />
          </div>
          <span className="text-xl font-bold text-foreground tracking-tight">AI-DataHub</span>
        </div>

        {/* Login Card */}
        <Card className="w-full">
          <CardHeader className="text-center pb-2">
            <CardTitle className="text-lg font-semibold">登录</CardTitle>
            <p className="text-xs text-muted-foreground mt-1">输入您的账号信息</p>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="username" className="text-xs">用户名</Label>
                <Input
                  id="username"
                  placeholder="请输入用户名"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  disabled={loading}
                  autoComplete="username"
                  className="h-10"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="password" className="text-xs">密码</Label>
                <Input
                  id="password"
                  type="password"
                  placeholder="请输入密码"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  disabled={loading}
                  autoComplete="current-password"
                  className="h-10"
                />
              </div>
              <Button type="submit" className="w-full h-10" disabled={loading}>
                {loading ? <Spinner className="mr-2" size={14} /> : null}
                {loading ? '登录中...' : '登录'}
              </Button>
            </form>
          </CardContent>
        </Card>

        {/* Features */}
        <div className="grid grid-cols-2 gap-3 mt-8">
          {features.map((f) => {
            const Icon = f.icon;
            return (
              <div
                key={f.title}
                className="p-3 rounded-xl border border-border bg-card"
              >
                <Icon className="h-4 w-4 text-muted-foreground mb-1.5" />
                <div className="text-xs font-medium text-foreground">{f.title}</div>
                <div className="text-[11px] text-muted-foreground mt-0.5">{f.desc}</div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
