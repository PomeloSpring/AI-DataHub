import { useState, useEffect, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { toast } from 'sonner';
import {
  Plus,
  Edit,
  Trash2,
  RefreshCw,
  Send,
  CheckCircle,
  XCircle,
} from 'lucide-react';
import {
  listNotificationChannels,
  createNotificationChannel,
  updateNotificationChannel,
  deleteNotificationChannel,
  testNotificationChannel,
  type NotificationChannel,
  type NotificationChannelCreateRequest,
} from '@/api/scheduledTask';

const CHANNEL_TYPE_MAP: Record<string, { label: string; icon: string }> = {
  dingtalk: { label: '钉钉', icon: '🤖' },
  feishu: { label: '飞书', icon: '🐦' },
  wecom: { label: '企业微信', icon: '💬' },
  email: { label: '邮件', icon: '📧' },
  webhook: { label: 'Webhook', icon: '🔗' },
};

interface FormData {
  name: string;
  channel_type: string;
  config: Record<string, any>;
  is_active: boolean;
}

const DEFAULT_FORM: FormData = {
  name: '',
  channel_type: 'webhook',
  config: {},
  is_active: true,
};

export default function NotificationChannels() {
  const [channels, setChannels] = useState<NotificationChannel[]>([]);
  const [loading, setLoading] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editChannel, setEditChannel] = useState<NotificationChannel | null>(null);
  const [form, setForm] = useState<FormData>(DEFAULT_FORM);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState<number | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<NotificationChannel | null>(null);

  const loadChannels = useCallback(async () => {
    setLoading(true);
    try {
      // 系统管理页面：不按 workspace 过滤
      const data = await listNotificationChannels();
      setChannels(data);
    } catch {
      toast.error('加载通知渠道失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadChannels(); }, [loadChannels]);

  const openCreate = () => {
    setEditChannel(null);
    setForm(DEFAULT_FORM);
    setFormOpen(true);
  };

  const openEdit = (ch: NotificationChannel) => {
    setEditChannel(ch);
    setForm({
      name: ch.name,
      channel_type: ch.channel_type,
      config: ch.config || {},
      is_active: ch.is_active,
    });
    setFormOpen(true);
  };

  const handleSave = async () => {
    if (!form.name.trim()) {
      toast.error('请输入渠道名称');
      return;
    }
    setSaving(true);
    try {
      const req: NotificationChannelCreateRequest = {
        name: form.name.trim(),
        channel_type: form.channel_type,
        config: form.config,
        is_active: form.is_active,
        workspace_id: 0,
      };
      if (editChannel) {
        await updateNotificationChannel(editChannel.id, req);
        toast.success('更新成功');
      } else {
        await createNotificationChannel(req);
        toast.success('创建成功');
      }
      setFormOpen(false);
      loadChannels();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async (ch: NotificationChannel) => {
    setTesting(ch.id);
    try {
      await testNotificationChannel(ch.id);
      toast.success('测试消息发送成功');
      loadChannels();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '测试失败');
    } finally {
      setTesting(null);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await deleteNotificationChannel(deleteTarget.id);
      toast.success('已删除');
      setDeleteTarget(null);
      loadChannels();
    } catch {
      toast.error('删除失败');
    }
  };

  const updateConfig = (key: string, value: any) => {
    setForm(prev => ({ ...prev, config: { ...prev.config, [key]: value } }));
  };

  const renderConfigFields = () => {
    const ct = form.channel_type;
    if (ct === 'dingtalk') {
      return (
        <>
          <div className="space-y-1.5">
            <Label>Webhook URL</Label>
            <Input
              value={form.config.webhook_url || ''}
              onChange={e => updateConfig('webhook_url', e.target.value)}
              placeholder="https://oapi.dingtalk.com/robot/send?access_token=xxx"
            />
          </div>
          <div className="space-y-1.5">
            <Label>签名密钥（可选）</Label>
            <Input
              value={form.config.secret || ''}
              onChange={e => updateConfig('secret', e.target.value)}
              placeholder="SEC..."
            />
          </div>
        </>
      );
    }
    if (ct === 'feishu') {
      return (
        <div className="space-y-1.5">
          <Label>Webhook URL</Label>
          <Input
            value={form.config.webhook_url || ''}
            onChange={e => updateConfig('webhook_url', e.target.value)}
            placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
          />
        </div>
      );
    }
    if (ct === 'wecom') {
      return (
        <div className="space-y-1.5">
          <Label>Webhook URL</Label>
          <Input
            value={form.config.webhook_url || ''}
            onChange={e => updateConfig('webhook_url', e.target.value)}
            placeholder="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"
          />
        </div>
      );
    }
    if (ct === 'email') {
      return (
        <>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>SMTP 服务器</Label>
              <Input
                value={form.config.smtp_host || ''}
                onChange={e => updateConfig('smtp_host', e.target.value)}
                placeholder="smtp.example.com"
              />
            </div>
            <div className="space-y-1.5">
              <Label>端口</Label>
              <Input
                type="number"
                value={form.config.smtp_port || 465}
                onChange={e => updateConfig('smtp_port', Number(e.target.value))}
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>用户名</Label>
              <Input
                value={form.config.smtp_user || ''}
                onChange={e => updateConfig('smtp_user', e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label>密码</Label>
              <Input
                type="password"
                value={form.config.smtp_password || ''}
                onChange={e => updateConfig('smtp_password', e.target.value)}
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label>收件人（多个用逗号分隔）</Label>
            <Input
              value={Array.isArray(form.config.to_addrs) ? form.config.to_addrs.join(',') : (form.config.to_addrs || '')}
              onChange={e => updateConfig('to_addrs', e.target.value.split(',').map((s: string) => s.trim()))}
              placeholder="admin@example.com, boss@example.com"
            />
          </div>
        </>
      );
    }
    // webhook
    return (
      <>
        <div className="space-y-1.5">
          <Label>URL</Label>
          <Input
            value={form.config.url || ''}
            onChange={e => updateConfig('url', e.target.value)}
            placeholder="https://example.com/webhook"
          />
        </div>
        <div className="space-y-1.5">
          <Label>请求头（JSON，可选）</Label>
          <Textarea
            value={form.config.headers ? JSON.stringify(form.config.headers) : ''}
            onChange={e => {
              try {
                updateConfig('headers', JSON.parse(e.target.value));
              } catch {}
            }}
            placeholder='{"Authorization": "Bearer xxx"}'
            rows={2}
          />
        </div>
      </>
    );
  };

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">通知渠道</h1>
          <p className="text-muted-foreground text-sm mt-1">配置消息推送渠道</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={loadChannels}>
            <RefreshCw className="w-4 h-4 mr-1" />
            刷新
          </Button>
          <Button size="sm" onClick={openCreate}>
            <Plus className="w-4 h-4 mr-1" />
            新建渠道
          </Button>
        </div>
      </div>

      {/* Channel List */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {loading ? (
          <div className="col-span-full text-center p-8 text-muted-foreground">加载中...</div>
        ) : channels.length === 0 ? (
          <div className="col-span-full text-center p-8 text-muted-foreground">暂无通知渠道</div>
        ) : (
          channels.map(ch => (
            <div key={ch.id} className="border rounded-lg p-4 space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-lg">{CHANNEL_TYPE_MAP[ch.channel_type]?.icon || '📢'}</span>
                  <div>
                    <div className="font-medium">{ch.name}</div>
                    <div className="text-xs text-muted-foreground">
                      {CHANNEL_TYPE_MAP[ch.channel_type]?.label || ch.channel_type}
                    </div>
                  </div>
                </div>
                <Badge variant="outline" className={ch.is_active ? 'text-green-500' : 'text-gray-500'}>
                  {ch.is_active ? '启用' : '禁用'}
                </Badge>
              </div>
              {ch.last_test_at && (
                <div className="flex items-center gap-1 text-xs text-muted-foreground">
                  {ch.last_test_status === 'success' ? (
                    <CheckCircle className="w-3 h-3 text-green-500" />
                  ) : (
                    <XCircle className="w-3 h-3 text-red-500" />
                  )}
                  测试于 {new Date(ch.last_test_at).toLocaleString()}
                </div>
              )}
              <div className="flex justify-end gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => handleTest(ch)}
                  disabled={testing === ch.id}
                >
                  <Send className="w-4 h-4 mr-1" />
                  {testing === ch.id ? '发送中...' : '测试'}
                </Button>
                <Button variant="ghost" size="sm" onClick={() => openEdit(ch)}>
                  <Edit className="w-4 h-4" />
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setDeleteTarget(ch)}>
                  <Trash2 className="w-4 h-4 text-destructive" />
                </Button>
              </div>
            </div>
          ))
        )}
      </div>

      {/* Create/Edit Dialog */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{editChannel ? '编辑渠道' : '新建渠道'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label>渠道名称</Label>
              <Input
                value={form.name}
                onChange={e => setForm(prev => ({ ...prev, name: e.target.value }))}
                placeholder="如：运维告警群"
              />
            </div>
            <div className="space-y-1.5">
              <Label>渠道类型</Label>
              <Select
                value={form.channel_type}
                onValueChange={v => setForm(prev => ({ ...prev, channel_type: v, config: {} }))}
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {Object.entries(CHANNEL_TYPE_MAP).map(([key, val]) => (
                    <SelectItem key={key} value={key}>{val.icon} {val.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {renderConfigFields()}

            {/* Message Template */}
            <div className="space-y-1.5">
              <Label>消息模板</Label>
              <p className="text-xs text-muted-foreground">
                自定义通知消息格式。留空使用默认格式。可用变量：
              </p>
              <div className="flex flex-wrap gap-1 text-xs">
                {['task_name', 'date', 'time', 'total', 'succeeded', 'failed', 'status', 'result_summary', 'report_link'].map(v => (
                  <code key={v} className="px-1 py-0.5 bg-muted rounded cursor-pointer hover:bg-muted-foreground/20"
                    onClick={() => {
                      const ta = document.getElementById('msg-template') as HTMLTextAreaElement;
                      if (ta) {
                        const start = ta.selectionStart;
                        const end = ta.selectionEnd;
                        const val = form.config.message_template || '';
                        const newVal = val.slice(0, start) + `{{${v}}}` + val.slice(end);
                        updateConfig('message_template', newVal);
                      }
                    }}
                  >{'{{' + v + '}}'}</code>
                ))}
              </div>
              <Textarea
                id="msg-template"
                value={form.config.message_template || ''}
                onChange={e => updateConfig('message_template', e.target.value)}
                placeholder={`📊 {{task_name}}\n📅 {{date}} {{time}}\n状态: {{status}}\n\n{{result_summary}}\n\n🔗 查看完整报告: {{report_link}`}
                rows={5}
                className="font-mono text-sm"
              />
            </div>

            <div className="flex items-center gap-2">
              <Switch
                checked={form.is_active}
                onCheckedChange={v => setForm(prev => ({ ...prev, is_active: v }))}
              />
              <Label>启用</Label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>取消</Button>
            <Button onClick={handleSave} disabled={saving}>{saving ? '保存中...' : '保存'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation */}
      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认删除</DialogTitle>
            <DialogDescription>
              确定要删除通知渠道「{deleteTarget?.name}」吗？
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>取消</Button>
            <Button variant="destructive" onClick={handleDelete}>删除</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
