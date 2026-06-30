import { useState, useEffect } from 'react';
import { toast } from 'sonner';
import { User, Mail, Phone, Shield, Clock, Save, Lock } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import { Spinner } from '@/components/ui/spinner';
import client from '../api/client';
import { useAuthStore } from '../stores/authStore';

const ROLE_LABELS: Record<string, string> = {
  admin: '管理员',
  analyst: '分析师',
  viewer: '查看者',
};

export default function Profile() {
  const [profile, setProfile] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [changingPwd, setChangingPwd] = useState(false);

  // Profile form
  const [formValues, setFormValues] = useState<any>({});

  // Password form
  const [pwdForm, setPwdForm] = useState<any>({});

  const updateUser = useAuthStore((s) => s.updateUser);

  useEffect(() => {
    loadProfile();
  }, []);

  const loadProfile = async () => {
    setLoading(true);
    try {
      const { data } = await client.get('/auth/me');
      setProfile(data);
      setFormValues({
        username: data.username || '',
        email: data.email || '',
        phone: data.phone || '',
      });
    } catch {
      toast.error('加载个人信息失败');
    } finally {
      setLoading(false);
    }
  };

  const handleSaveProfile = async () => {
    setSaving(true);
    try {
      const { data } = await client.put('/auth/me', formValues);
      setProfile(data);
      // Update auth store if username changed
      if (data.username !== profile?.username) {
        updateUser({ username: data.username });
      }
      toast.success('个人信息已更新');
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '更新失败');
    } finally {
      setSaving(false);
    }
  };

  const handleChangePassword = async () => {
    if (!pwdForm.old_password) {
      toast.error('请输入当前密码');
      return;
    }
    if (!pwdForm.new_password) {
      toast.error('请输入新密码');
      return;
    }
    if (pwdForm.new_password !== pwdForm.confirm_password) {
      toast.error('两次输入的密码不一致');
      return;
    }

    setChangingPwd(true);
    try {
      await client.put('/auth/me/password', {
        old_password: pwdForm.old_password,
        new_password: pwdForm.new_password,
      });
      toast.success('密码修改成功');
      setPwdForm({});
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '密码修改失败');
    } finally {
      setChangingPwd(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Spinner size={32} />
      </div>
    );
  }

  return (
    <div className="h-full overflow-auto p-6">
      <h1 className="text-2xl font-bold mb-6">个人设置</h1>

      <div className="max-w-[640px] space-y-6">
        {/* Account info card */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <User className="h-5 w-5" />
              账号信息
            </CardTitle>
            <CardDescription>您的账号基本信息</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <div className="text-sm text-muted-foreground mb-1">角色</div>
                <Badge variant={profile?.role === 'admin' ? 'destructive' : 'secondary'}>
                  {ROLE_LABELS[profile?.role] || profile?.role}
                </Badge>
              </div>
              <div>
                <div className="text-sm text-muted-foreground mb-1">状态</div>
                <Badge variant={profile?.status === 'active' ? 'default' : 'destructive'}>
                  {profile?.status === 'active' ? '正常' : profile?.status}
                </Badge>
              </div>
              <div>
                <div className="text-sm text-muted-foreground mb-1">创建时间</div>
                <div className="text-sm">
                  {profile?.created_at ? new Date(profile.created_at).toLocaleString('zh-CN') : '-'}
                </div>
              </div>
              <div>
                <div className="text-sm text-muted-foreground mb-1">最后登录</div>
                <div className="text-sm">
                  {profile?.last_login ? new Date(profile.last_login).toLocaleString('zh-CN') : '从未登录'}
                </div>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Edit profile card */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Mail className="h-5 w-5" />
              编辑资料
            </CardTitle>
            <CardDescription>更新您的个人信息</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="username">用户名</Label>
              <Input
                id="username"
                value={formValues.username || ''}
                onChange={(e) => setFormValues({ ...formValues, username: e.target.value })}
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="email">邮箱</Label>
                <div className="relative">
                  <Mail className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                  <Input
                    id="email"
                    type="email"
                    className="pl-9"
                    placeholder="user@example.com"
                    value={formValues.email || ''}
                    onChange={(e) => setFormValues({ ...formValues, email: e.target.value })}
                  />
                </div>
              </div>
              <div className="space-y-2">
                <Label htmlFor="phone">手机</Label>
                <div className="relative">
                  <Phone className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                  <Input
                    id="phone"
                    className="pl-9"
                    placeholder="手机号码"
                    value={formValues.phone || ''}
                    onChange={(e) => setFormValues({ ...formValues, phone: e.target.value })}
                  />
                </div>
              </div>
            </div>
            <Button onClick={handleSaveProfile} disabled={saving}>
              {saving ? <Spinner className="h-4 w-4 mr-2" /> : <Save className="h-4 w-4 mr-2" />}
              保存修改
            </Button>
          </CardContent>
        </Card>

        {/* Change password card */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Lock className="h-5 w-5" />
              修改密码
            </CardTitle>
            <CardDescription>定期更换密码可以提高账号安全性</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="old_password">当前密码</Label>
              <Input
                id="old_password"
                type="password"
                placeholder="请输入当前密码"
                value={pwdForm.old_password || ''}
                onChange={(e) => setPwdForm({ ...pwdForm, old_password: e.target.value })}
              />
            </div>
            <Separator />
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="new_password">新密码</Label>
                <Input
                  id="new_password"
                  type="password"
                  placeholder="至少8位，包含字母和数字"
                  value={pwdForm.new_password || ''}
                  onChange={(e) => setPwdForm({ ...pwdForm, new_password: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="confirm_password">确认新密码</Label>
                <Input
                  id="confirm_password"
                  type="password"
                  placeholder="再次输入新密码"
                  value={pwdForm.confirm_password || ''}
                  onChange={(e) => setPwdForm({ ...pwdForm, confirm_password: e.target.value })}
                />
              </div>
            </div>
            <Button onClick={handleChangePassword} disabled={changingPwd}>
              {changingPwd ? <Spinner className="h-4 w-4 mr-2" /> : <Shield className="h-4 w-4 mr-2" />}
              修改密码
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
