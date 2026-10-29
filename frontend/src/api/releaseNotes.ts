import client from './client';

/** 更新项分组: feature=新增 / improvement=优化 / fix=修复 / other=未知类型(原样展示, 不静默丢) */
export type SectionKind = 'feature' | 'improvement' | 'fix' | 'other';

export interface ReleaseNoteSection {
  kind: SectionKind;
  /** 后端回传的原始 kind(未知类型时用于兜底展示) */
  raw_kind?: string;
  items: string[];
}

export interface ReleaseNote {
  id: number;
  version: string;
  title: string;
  summary: string;
  sections: ReleaseNoteSection[];
  /** 关联引导 id(前端 GUIDES 的键), 空 = 无引导 */
  guide_key: string;
  is_major: number;
  published_at?: string | null;
  is_published?: number;
  updated_by?: string;
  updated_at?: string | null;
}

export type GuideStatus = 'completed' | 'skipped' | 'read';

export interface GuideStateItem {
  status: GuideStatus;
  updated_at?: string | null;
}

export const releaseNotesApi = {
  /** 已发布公告(新→旧) */
  list: () => client.get<{ items: ReleaseNote[]; latest_version: string }>('/release-notes'),
  /** 当前用户的引导/已读状态 */
  getState: () => client.get<{ states: Record<string, GuideStateItem> }>('/release-notes/state'),
  /** 写引导状态(单条或批量; user_id 由服务端身份注入, 请求体不带身份字段) */
  putState: (keys: string[], status: GuideStatus) =>
    client.put('/release-notes/state', { keys, status }),

  // ── 管理域(仅 admin) ──────────────────────────────────────────────
  adminList: () => client.get<{ items: ReleaseNote[] }>('/admin/release-notes'),
  adminCreate: (data: Omit<ReleaseNote, 'id' | 'published_at'>) =>
    client.post<{ success: boolean; id: number }>('/admin/release-notes', data),
  adminUpdate: (id: number, data: Omit<ReleaseNote, 'id' | 'published_at'>) =>
    client.put<{ success: boolean; id: number }>(`/admin/release-notes/${id}`, data),
  adminRemove: (id: number) => client.delete<{ success: boolean }>(`/admin/release-notes/${id}`),
};

/** 状态 key 口径(与后端 _STATE_KEY_RE 一致, 改一侧必须同步另一侧) */
export const guideKey = (guideId: string) => `guide:${guideId}`;
export const noteKey = (version: string) => `note:${version}`;

/** 全局静音: 命中后所有引导都不再自动弹出(手动入口仍可用) */
export const GUIDE_ALL_KEY = guideKey('all');
/** 欢迎总览 */
export const GUIDE_WELCOME_KEY = guideKey('welcome');
