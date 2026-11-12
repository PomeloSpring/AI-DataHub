// 模板中心 · 消息模板（IM / 邮件 / 短信）。
//
// 与报告模板的分工：报告模板是**版式契约**（章节 + 图表规格 + 主题，决定报告长什么样），
// 消息模板是**带变量的文案**（发送时展开成通知正文）。两者都存在各自的表里，共用「模板中心」页面。
//
// 可用变量必须从 `getMessageTemplateMeta()` 取：服务端是唯一口径，而且业务内容（任务名、
// 结果摘要、报告正文）按安全护栏不允许进通知 —— 旧 UI 自己硬写过 9 个变量，其中 2 个根本发不出去。
import client from './client';

export interface MessageTemplateVariable {
  name: string;
  label: string;
  sample: string;
}

export interface MessageTemplateMeta {
  variables: MessageTemplateVariable[];
  kinds: { value: string; label: string }[];
  formats: Record<string, string[]>;
  sms_segment_chars: number;
  excluded_note: string;
}

export interface MessageTemplate {
  id: number;
  code: string;
  name: string;
  template_kind: string;
  format: string;
  subject: string;
  content: string;
  description: string;
  is_active: number;
  is_builtin: boolean;
  /** 正文里用到的、已登记的变量名 */
  variables: string[];
  /** 正文里出现但发不出去的变量（发送时留字面量），必须在列表里看得见 */
  unregistered_variables: string[];
  used_by_channels: { id: number; name: string }[];
  used_count: number;
  updated_at: string;
}

export interface MessageTemplatePayload {
  name: string;
  template_kind: string;
  format: string;
  subject?: string;
  content: string;
  description?: string;
  is_builtin?: boolean;
}

export interface MessageTemplatePreview {
  subject: string;
  content: string;
  unknown_variables: string[];
  variables: string[];
  char_count: number;
  note: string;
}

export async function getMessageTemplateMeta(): Promise<MessageTemplateMeta> {
  const { data } = await client.get('/message-templates/variables');
  return data;
}

export async function listMessageTemplates(kind?: string, includeInactive = false): Promise<MessageTemplate[]> {
  const { data } = await client.get('/message-templates', {
    params: { kind: kind || '', include_inactive: includeInactive },
  });
  return Array.isArray(data) ? data : [];
}

export async function previewMessageTemplate(payload: {
  template_kind: string; format: string; subject?: string; content: string;
}): Promise<MessageTemplatePreview> {
  const { data } = await client.post('/message-templates/preview', payload);
  return data;
}

export async function createMessageTemplate(payload: MessageTemplatePayload): Promise<{ id: number }> {
  const { data } = await client.post('/message-templates', payload);
  return data;
}

export async function updateMessageTemplate(
  id: number, payload: Partial<MessageTemplatePayload> & { is_active?: number },
): Promise<{ success: boolean; warnings?: string[] }> {
  const { data } = await client.put(`/message-templates/${id}`, payload);
  return data;
}

/** 被渠道引用的模板服务端会拒删并回原因（引用它的渠道清单），调用方必须把 detail 端给用户 */
export async function deleteMessageTemplate(id: number): Promise<{ success: boolean }> {
  const { data } = await client.delete(`/message-templates/${id}`);
  return data;
}

/** 渠道类型 → 模板分类（与后端 CHANNEL_KIND 同一映射，前端不自己 if） */
export const CHANNEL_KIND: Record<string, string> = {
  dingtalk: 'im', feishu: 'im', wecom: 'im', webhook: 'im',
  email: 'email', sms: 'sms',
};

export const KIND_LABELS: Record<string, string> = {
  im: '群机器人 / Webhook', email: '邮件', sms: '短信',
};

export const FORMAT_LABELS: Record<string, string> = {
  text: '纯文本', markdown: 'Markdown', html: 'HTML',
};
