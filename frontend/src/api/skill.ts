import client from './client';

export interface Skill {
  id?: number;
  name: string;
  display_name: string;
  description: string;
  category: string;
  system_prompt?: string;
  skill_config?: string | object;
  source_type: 'system' | 'user';
  source_skill?: string;
  is_active: number;
  workspace_id?: number;
  created_at?: string;
  updated_at?: string;
}

export interface SkillCreate {
  name: string;
  display_name?: string;
  description?: string;
  category?: string;
  system_prompt?: string;
  skill_config?: string | object;
  source_type?: string;
  source_skill?: string;
  is_active?: number;
  workspace_id?: number;
}

export const skillApi = {
  /** List all skills (file system + DB merged) */
  list: (category?: string) =>
    client.get<Skill[]>('/admin/skills', { params: category ? { category } : {} }),

  /** Get a single skill by name (includes system_prompt) */
  get: (name: string) =>
    client.get<Skill>(`/admin/skills/${name}`),

  /** Create a user-defined skill */
  create: (data: SkillCreate) =>
    client.post<{ id: number; success: boolean }>('/admin/skills', data),

  /** Update a skill */
  update: (id: number, data: Partial<SkillCreate>) =>
    client.put<{ success: boolean }>(`/admin/skills/${id}`, data),

  /** Delete a skill (only user-created) */
  delete: (id: number) =>
    client.delete<{ success: boolean }>(`/admin/skills/${id}`),

  /** Copy a system skill to create a user-editable copy */
  copy: (name: string, workspaceId?: number) =>
    client.post<{ id: number; name: string; success: boolean }>(
      `/admin/skills/${name}/copy`,
      workspaceId ? { workspace_id: workspaceId } : {},
    ),
};
