import { useState, useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { toast } from '@/lib/toast';
import { User, Mail, Phone, Shield, Save, Lock, ArrowLeft, Brain, Cloud } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import { Spinner } from '@/components/ui/spinner';
import { Switch } from '@/components/ui/switch';
import client from '../api/client';
import { useAuthStore } from '../stores/authStore';
import MotionPreferenceCard from '@/components/MotionPreferenceCard';

/** 角色可读名：以服务端按真值源（adh_user_roles ⋈ adh_roles.display_name）下发的 role_name 为准。
 *  历史缺陷：本页读的是 profile.role，而 /users/me 的账号列叫 user_role → 永远取不到值，
 *  角色一格渲染成空白徽标；前端硬编码的三档文案也接不住自定义角色（如「运营管理」）。
 *  无生效绑定就如实说「未绑定角色」（空绑定 = 零权限码），不回退内部标识充数。 */
const roleText = (profile: any): string => String(profile?.role_name || '').trim() || '未绑定角色';

export default function Profile() {
  const [profile, setProfile] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [changingPwd, setChangingPwd] = useState(false);

  // Profile form
  const [formValues, setFormValues] = useState<any>({});

  // Password form
  const [pwdForm, setPwdForm] = useState<any>({});

  // 云空间偏好：会话产物自动上云（默认开启）
  const [cloudSync, setCloudSync] = useState<boolean>(true);
  const [cloudSyncLoaded, setCloudSyncLoaded] = useState(false);
  const [cloudSyncSaving, setCloudSyncSaving] = useState(false);

  const updateUser = useAuthStore((s) => s.updateUser);

  const navigate = useNavigate();
  const location = useLocation();
  // 当前 Profile 内嵌于哪个布局框架, 返回按钮回到对应分区主页面
  const homePath = (() => {
    const p = location.pathname;
    if (p.startsWith('/system')) return '/system';
    const m = p.match(/^\/(ws|ask)\/([^/]+)/);
    if (m) return m[1] === 'ws' ? `/${m[1]}/${m[2]}/chat` : `/${m[1]}/${m[2]}`;
    return '/data';
  })();
  // 记忆页与本页同框架挂载(/ws/{id}、/ask/{id}、/data、/system 均有 /memory 子路由),
  // 从当前路径直接换尾段, 保证“个人设置 → 我的记忆”不跳框架。
  const memoryPath = location.pathname.replace(/\/profile\/?$/, '/memory');

  useEffect(() => {
    loadProfile();
    loadPreferences();
  }, []);

  const loadPreferences = async () => {
    try {
      const { data } = await client.get('/users/me/preferences');
      setCloudSync(Boolean(data.cloud_sync_enabled));
    } catch {
      // 偏好读不到时不静默当成“已关闭”，也不假装“已开启”：保持未知(未加载)并在界面上说明
      setCloudSyncLoaded(false);
      toast.error('云空间偏好加载失败，请刷新页面重试');
      return;
    }
    setCloudSyncLoaded(true);
  };

  const handleToggleCloudSync = async (next: boolean) => {
    const prev = cloudSync;
    setCloudSync(next);
    setCloudSyncSaving(true);
    try {
      await client.put('/users/me/preferences', { cloud_sync_enabled: next });
      toast.success(next ? '已开启会话产物自动上云' : '已关闭会话产物自动上云');
    } catch (e: any) {
      setCloudSync(prev);   // 保存失败回滚开关，不让界面显示与库里不一致的状态
      toast.error(e?.response?.data?.detail || '保存失败，请稍后重试');
    } finally {
      setCloudSyncSaving(false);
    }
  };

  const loadProfile = async () => {
    setLoading(true);
    try {
      const { data } = await client.get('/users/me');
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
      const { data } = await client.put('/users/me', formValues);
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
      await client.put('/users/me/password', {
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
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">个人设置</h1>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => navigate(memoryPath)}>
            <Brain className="h-4 w-4 mr-1" />
            我的记忆
          </Button>
          <Button variant="outline" size="sm" onClick={() => navigate(homePath)}>
            <ArrowLeft className="h-4 w-4 mr-1" />
            返回主页
          </Button>
        </div>
      </div>

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
                  {roleText(profile)}
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

        {/* 云空间偏好 card */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Cloud className="h-5 w-5" />
              云空间（对话产物备份）
            </CardTitle>
            <CardDescription>对话中产生的文件（上传附件与助手生成的报告/图表）自动同步云空间，换节点或本地磁盘被清理时可自动取回。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-center justify-between gap-4">
              <div className="min-w-0">
                <Label htmlFor="cloud-sync">会话产物自动上云</Label>
                <p className="text-xs text-muted-foreground mt-1">
                  {!cloudSyncLoaded
                    ? '偏好未加载成功，当前状态未知——请刷新后重试'
                    : cloudSync
                      ? '开启（默认）：每轮对话结束后在后台同步到对象存储'
                      : '已关闭：产物只留在本地，实例迁移或清理磁盘会丢失（已有云端副本仍可取回）'}
                </p>
              </div>
              <Switch
                id="cloud-sync"
                checked={cloudSync}
                disabled={cloudSyncSaving || !cloudSyncLoaded}
                onCheckedChange={v => void handleToggleCloudSync(v)}
              />
            </div>
          </CardContent>
        </Card>

        {/* 界面动效（出现效果）：预设来自 UI 字模库，差量落个人偏好，与主题配色相互独立 */}
        <MotionPreferenceCard />

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
