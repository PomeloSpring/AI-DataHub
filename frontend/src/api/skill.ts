import client from './client';

export interface Skill {
  name: string;
  display_name: string;
  description: string;
  category: string;
  source_type: 'system' | 'custom';
  is_builtin: boolean;
  /** 'skill_md' = Qoder 文件夹规范;  '' = 旧格式 */
  format?: string;
  // 仅详情接口返回
  system_prompt?: string;
  markdown?: string;
}

export interface SkillUpsert {
  name: string;
  display_name?: string;
  description?: string;
  category?: string;
  system_prompt?: string;
}

export const skillApi = {
  /** List all skills (系统内置 + 自定义, 均来自本地文件夹) */
  list: (category?: string) =>
    client.get<Skill[]>('/admin/skills', { params: category ? { category } : {} }),

  /** Get a single skill by name (includes system_prompt + SKILL.md markdown) */
  get: (name: string) =>
    client.get<Skill>(`/admin/skills/${name}`),

  /** Create a custom skill (落盘为 config/skills/{name}/SKILL.md) */
  create: (data: SkillUpsert) =>
    client.post<{ name: string; success: boolean }>('/admin/skills', data),

  /** Update a custom skill (内置技能拒绝) */
  update: (name: string, data: SkillUpsert) =>
    client.put<{ name: string; success: boolean }>(`/admin/skills/${name}`, data),

  /** Delete a custom skill (内置技能拒绝) */
  delete: (name: string) =>
    client.delete<{ name: string; success: boolean }>(`/admin/skills/${name}`),
};
