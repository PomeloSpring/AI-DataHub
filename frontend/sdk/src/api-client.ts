export class ChatBIClient {
  private host: string;
  private apiKey: string;
  private userId: string;
  private userName: string;
  /** 宿主页面给的语言（`locale` 属性）：随每个请求以 Accept-Language 下发，
   *  后端据此定 LLM 回复语言（口径：提问语言优先 → 界面语言兜底，见 backend/common/i18n）。
   *  合法清单与后端 SUPPORTED_LOCALES / frontend/src/i18n 的 LOCALES 同源；新语言三处一起加。 */
  private locale: string;
  private embedToken: string | null = null;
  private tokenExpiresAt: number = 0;
  private refreshTimer: ReturnType<typeof setTimeout> | null = null;

  private static readonly SUPPORTED_LOCALES = ['zh-CN', 'en-US'];

  constructor(host: string, apiKey: string, userId: string, userName: string = '', locale: string = '') {
    this.host = host.replace(/\/$/, '');
    this.apiKey = apiKey;
    this.userId = userId;
    this.userName = userName;
    this.locale = this.normalizeLocale(locale);
  }

  /** 非法 locale 不静默采用：回默认值 + console 提醒（配置面写了却不生效比报错更难查）。 */
  private normalizeLocale(value: string): string {
    const raw = (value || '').trim().replace(/_/g, '-');
    if (!raw) return ChatBIClient.SUPPORTED_LOCALES[0];
    const hit = ChatBIClient.SUPPORTED_LOCALES.find(l => l.toLowerCase() === raw.toLowerCase());
    if (hit) return hit;
    console.warn(`[chatbi-sdk] 不支持的 locale "${value}"（可选: ${ChatBIClient.SUPPORTED_LOCALES.join(', ')}），已回默认语言`);
    return ChatBIClient.SUPPORTED_LOCALES[0];
  }

  /** 所有出站请求统一带界面语言（后端用它定 LLM 回复语言与文档 lang）。 */
  private localeHeader(): Record<string, string> {
    return { 'Accept-Language': this.locale };
  }

  async init(): Promise<void> {
    await this.verify();
    this.scheduleRefresh();
  }

  dispose(): void {
    if (this.refreshTimer) {
      clearTimeout(this.refreshTimer);
      this.refreshTimer = null;
    }
  }

  private async verify(): Promise<void> {
    const res = await fetch(`${this.host}/api/embed/auth/verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...this.localeHeader() },
      body: JSON.stringify({
        api_key: this.apiKey,
        user_id: this.userId,
        user_name: this.userName,
      }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: '认证失败' }));
      throw new Error(err.detail || `认证失败 (${res.status})`);
    }

    const data = await res.json();
    this.embedToken = data.embed_token;
    this.tokenExpiresAt = new Date(data.expires_at).getTime();
  }

  private async refresh(): Promise<void> {
    if (!this.embedToken) return;

    try {
      const res = await fetch(`${this.host}/api/embed/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...this.localeHeader() },
        body: JSON.stringify({ embed_token: this.embedToken }),
      });

      if (res.ok) {
        const data = await res.json();
        this.embedToken = data.embed_token;
        this.tokenExpiresAt = new Date(data.expires_at).getTime();
      }
    } catch {
      // Refresh failed, will re-verify on next request
    }

    this.scheduleRefresh();
  }

  private scheduleRefresh(): void {
    if (this.refreshTimer) clearTimeout(this.refreshTimer);
    // Refresh 5 minutes before expiry
    const refreshIn = Math.max(this.tokenExpiresAt - Date.now() - 5 * 60 * 1000, 60 * 1000);
    this.refreshTimer = setTimeout(() => this.refresh(), refreshIn);
  }

  private async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    if (!this.embedToken || Date.now() >= this.tokenExpiresAt) {
      await this.verify();
    }

    const url = `${this.host}/api/embed${path}`;
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...this.localeHeader(),
      'Authorization': `Bearer ${this.embedToken}`,
      ...(options.headers as Record<string, string> || {}),
    };

    const res = await fetch(url, { ...options, headers });

    if (res.status === 401) {
      // Token expired, re-verify and retry once
      await this.verify();
      headers['Authorization'] = `Bearer ${this.embedToken}`;
      const retryRes = await fetch(url, { ...options, headers });
      if (!retryRes.ok) {
        const err = await retryRes.json().catch(() => ({ detail: '请求失败' }));
        throw new Error(err.detail || `请求失败 (${retryRes.status})`);
      }
      return retryRes.json();
    }

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: '请求失败' }));
      throw new Error(err.detail || `请求失败 (${res.status})`);
    }

    return res.json();
  }

  // ── Public API ──────────────────────────────────────────────────

  async getConversations(): Promise<any> {
    return this.request('/chat/conversations');
  }

  async createConversation(datasourceId: number = 0): Promise<any> {
    return this.request('/chat/conversations', {
      method: 'POST',
      body: JSON.stringify({ datasource_id: datasourceId }),
    });
  }

  async getConversation(id: number): Promise<any> {
    return this.request(`/chat/conversations/${id}`);
  }

  async deleteConversation(id: number): Promise<any> {
    return this.request(`/chat/conversations/${id}`, { method: 'DELETE' });
  }

  async sendMessage(
    question: string,
    conversationId?: number,
    datasourceId: number = 0,
    history: any[] = [],
  ): Promise<Response> {
    // Returns raw Response for SSE streaming
    if (!this.embedToken || Date.now() >= this.tokenExpiresAt) {
      await this.verify();
    }

    return fetch(`${this.host}/api/embed/chat/send`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...this.localeHeader(),
        'Authorization': `Bearer ${this.embedToken}`,
      },
      body: JSON.stringify({
        question,
        conversation_id: conversationId,
        datasource_id: datasourceId,
        history,
      }),
    });
  }

  async getDashboards(): Promise<any> {
    return this.request('/dashboards');
  }

  async getDashboard(id: number): Promise<any> {
    return this.request(`/dashboards/${id}`);
  }
}
